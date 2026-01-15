# audit_keystore.py
# Purpose: Handles secure storage, retrieval, and deletion of software keys.
# All disk crypto/serialization is performed here.

import asyncio
import base64
import json
import logging
import os
import stat  # For file permissions

# import fcntl # For POSIX advisory file locking - REMOVED: Replaced with portalocker
import tempfile  # For atomic writes

# --- FIX: Missing import time ---
import time
from typing import Any, Dict, List, Optional, Protocol

import aiofiles  # For async file operations
import portalocker  # For cross-platform file locking
from cryptography.exceptions import InvalidTag  # For AES GCM decryption validation
from cryptography.hazmat.backends import default_backend

# Core cryptography primitives
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# --- FIX: Import from audit_common.py to break circular dependency ---
# audit_common is a "leaf node" module with no dependencies on other audit_crypto modules
from .audit_common import (
    CryptoOperationError,
    SensitiveDataFilter,
    add_sensitive_filter_to_logger,
)

logger = logging.getLogger(__name__)

# Apply sensitive data filter from audit_common
add_sensitive_filter_to_logger(logger)


# --- Production TODOs: ---
# [X] Replace file-based storage with a production-grade key vault (HSM, KMS, or at least encrypted storage).
#     (Implemented a Protocol for pluggable backends, default is file-based but now extensible)
# [ ] Support versioned keys, revocation, expiry policies. (Partially addressed with status, but full versioning needs more)
# [ ] Enforce key usage limits and periodic rotation. (Handled in CryptoProvider, not KeyStore)
# [ ] Add key export/import policies (disable by default).
# [X] Full audit log for every key event (create, retire, destroy).
# [ ] Optionally support key wrapping/unwrapping (hardware-backed if possible).
# [X] Write testable interfaces for mock/fake stores in CI.
# [X] Harden all I/O: atomic writes, no race conditions. (Implemented advisory locking for file backend)
# [X] All errors must be audit logged.
# [X] Metadata: Every key must have full creation, retirement, status, and last-used info.
# [X] Strong Typing: Protocols and backends must be exhaustively type checked.
# [ ] Comprehensive Testing: All backends must have mock and integration tests (not just filesystem). (This is a testing task, not a code change here)


class KeyStorageBackend(Protocol):
    """
    Protocol defining the interface for a key storage backend.
    This allows for plug-and-play storage solutions (e.g., file system, database, cloud storage).
    """

    async def store_key_data(
        self, key_id: str, encrypted_payload_b64: str, metadata: Dict[str, Any]
    ) -> None:
        """
        Stores the encrypted key payload and its metadata.
        Raises CryptoOperationError on failure.
        """
        ...

    async def load_key_data(self, key_id: str) -> Optional[Dict[str, Any]]:
        """
        Loads the encrypted key payload and its metadata.
        Returns None if key not found. Raises CryptoOperationError on other failures.
        """
        ...

    async def list_key_metadata(self) -> List[Dict[str, Any]]:
        """
        Lists metadata for all stored keys (excluding sensitive key data).
        Raises CryptoOperationError on failure.
        """
        ...

    async def delete_key_data(self, key_id: str) -> bool:
        """
        Deletes the key data from storage.
        Returns True if deleted, False if not found. Raises CryptoOperationError on other failures.
        """
        ...


class FileSystemKeyStorageBackend:
    """
    A file system-based implementation of the KeyStorageBackend.
    Keys are stored as encrypted JSON files.
    Uses cross-platform advisory file locking (via portalocker) for basic cross-process synchronization.
    WARNING: This backend is suitable for development/testing but NOT for production
    due to limitations of file-based storage for sensitive data (e.g., secure erase on SSDs/NVMe,
    robustness in distributed environments, performance, and true atomic operations across networks).
    """

    def __init__(self, key_dir: str):
        self.key_dir = key_dir
        os.makedirs(key_dir, exist_ok=True)
        self.logger = logging.getLogger(f"{__name__}.FileSystemKeyStorageBackend")

        # We need CryptoOperationError for the file system backend to raise exceptions
        try:
            from .audit_crypto_provider import (
                CryptoOperationError as RealCryptoOperationError,
            )

            self._CryptoOperationError = RealCryptoOperationError
        except ImportError:
            self._CryptoOperationError = Exception  # Fallback if provider fails to load

    # Dictionary to hold file descriptors for locks, to ensure they are kept open
    # for the duration of the lock. This is a process-local cache.
    _lock_files: Dict[str, Any] = {}

    async def _acquire_lock(self, filepath: str, shared: bool = False):
        """
        Acquires an advisory file lock for the given filepath using portalocker.
        """
        lock_file_path = f"{filepath}.lock"
        # Ensure the lock file directory exists
        os.makedirs(os.path.dirname(lock_file_path), exist_ok=True)

        lock_mode = "r" if shared else "a+"
        lock_type = (
            portalocker.LockFlags.SHARED if shared else portalocker.LockFlags.EXCLUSIVE
        )

        self.logger.debug(
            f"Attempting to acquire {'shared' if shared else 'exclusive'} lock for {filepath}"
        )

        try:
            # 1. Open the lock file using aiofiles for async context management
            # We must use 'a+' so that the file is not truncated when opening for exclusive lock.
            f = await aiofiles.open(lock_file_path, "a+")

            # 2. Acquire the lock (blocking call, run in executor)
            # Use non-blocking + retry loop for better control, but portalocker.lock is cleaner
            await asyncio.to_thread(portalocker.lock, f, lock_type)

            self._lock_files[filepath] = f
            self.logger.debug(
                f"Acquired {'shared' if shared else 'exclusive'} lock for {filepath}."
            )
        except Exception as e:
            self.logger.error(
                f"Failed to acquire lock for {filepath}: {e}", exc_info=True
            )
            # Ensure the file object is closed if it was opened but locking failed
            if filepath in self._lock_files:
                await self._lock_files[filepath].close()
                del self._lock_files[filepath]
            elif "f" in locals():
                await f.close()  # Close locally opened file object
            # Use the local or fallback CryptoOperationError
            raise self._CryptoOperationError(
                f"Failed to acquire file lock for {filepath}: {e}"
            ) from e

    async def _release_lock(self, filepath: str):
        """Releases an advisory file lock using portalocker."""
        if filepath in self._lock_files:
            f = self._lock_files[filepath]
            try:
                # 1. Release the lock (blocking call, run in executor)
                await asyncio.to_thread(portalocker.unlock, f)
                self.logger.debug(f"Released lock for {filepath}")
            except Exception as e:
                self.logger.error(
                    f"Failed to release lock for {filepath}: {e}", exc_info=True
                )
            finally:
                # 2. Close the file object
                await f.close()
                del self._lock_files[filepath]

    async def _atomic_write_and_set_permissions(self, filepath: str, data_bytes: bytes):
        """
        Performs an atomic write to a file and sets strict permissions (0o600).
        Uses a temporary file and os.replace() for atomicity.
        """
        dirpath = os.path.dirname(filepath)
        temp_file_name = None
        temp_file_fd = None
        try:
            # 1. Create a temp file in the same directory
            # Use tempfile.NamedTemporaryFile for atomic file creation and unique naming
            # delete=False to keep it after closing for os.replace
            # Use os.open to manually manage file descriptor and ensure correct permissions are set immediately.
            temp_file_fd, temp_file_name = await asyncio.to_thread(
                tempfile.mkstemp, dir=dirpath, suffix=".tmp", text=False
            )

            # 2. Set restrictive permissions before writing any sensitive data
            # --- FIX 1: Use os.chmod(path) for Windows compatibility ---
            # --- FIX 2: Skip on Windows (nt) as it doesn't support 0o600 ---
            if os.name != "nt":
                await asyncio.to_thread(os.chmod, temp_file_name, 0o600)

            # 3. Write data to the temporary file
            await asyncio.to_thread(os.write, temp_file_fd, data_bytes)

            # 4. Flush and synchronize to disk (blocking call, run in executor)
            await asyncio.to_thread(os.fsync, temp_file_fd)
            await asyncio.to_thread(os.close, temp_file_fd)  # Close the file descriptor
            temp_file_fd = None  # Mark as closed

            # 5. Atomically replace the target file with the temporary file
            await asyncio.to_thread(os.replace, temp_file_name, filepath)

            # 6. Set restrictive file permissions (read/write for owner only) on the final file
            # This is done again as os.replace might inherit permissions from the destination if it exists
            # --- FIX 2: Skip on Windows (nt) ---
            if os.name != "nt":
                await asyncio.to_thread(os.chmod, filepath, 0o600)
            self.logger.debug(f"Atomic write and permissions set for {filepath}.")
        except Exception as e:
            self.logger.error(
                f"Failed atomic write or permission set for {filepath}: {e}",
                exc_info=True,
            )
            if temp_file_fd is not None:
                # Clean up the file descriptor if it's still open
                await asyncio.to_thread(os.close, temp_file_fd)
            if temp_file_name and os.path.exists(temp_file_name):
                # Clean up the temporary file if the operation fails
                await asyncio.to_thread(os.remove, temp_file_name)
            raise self._CryptoOperationError(
                f"Atomic file operation failed for {filepath}: {e}"
            ) from e

    async def _verify_permissions(self, filepath: str):
        """
        Verifies that file permissions are set to 0o600. If not, attempts to correct them.
        Raises CryptoOperationError if permissions cannot be verified/corrected.
        """
        # --- FIX 2: Skip this entire check on Windows ---
        if os.name == "nt":
            self.logger.debug(
                f"Skipping permission check for '{filepath}' on Windows (nt)."
            )
            return
        # --- END FIX ---

        try:
            mode = await asyncio.to_thread(lambda: os.stat(filepath).st_mode)
            current_permissions = stat.S_IMODE(mode)  # Get only the permission bits
            expected_permissions = 0o600

            if current_permissions != expected_permissions:
                self.logger.warning(
                    f"File '{filepath}' has incorrect permissions ({oct(current_permissions)}), expected {oct(expected_permissions)}. Attempting to correct.",
                    extra={
                        "operation": "permission_mismatch",
                        "filepath": filepath,
                        "current_perm": oct(current_permissions),
                    },
                )
                await asyncio.to_thread(os.chmod, filepath, expected_permissions)
                self.logger.info(
                    f"Permissions for '{filepath}' corrected to {oct(expected_permissions)}.",
                    extra={"operation": "permission_corrected", "filepath": filepath},
                )
        except Exception as e:
            self.logger.error(
                f"Failed to verify or correct permissions for '{filepath}': {e}",
                exc_info=True,
            )
            raise self._CryptoOperationError(
                f"Failed to verify/correct file permissions for {filepath}: {e}"
            ) from e

    async def store_key_data(
        self, key_id: str, encrypted_payload_b64: str, metadata: Dict[str, Any]
    ) -> None:
        filepath = os.path.join(self.key_dir, f"{key_id}.json")

        full_metadata = metadata.copy()
        full_metadata["encrypted_payload_b64"] = encrypted_payload_b64
        full_metadata["key_id"] = key_id  # Ensure key_id is explicitly in metadata
        full_metadata["last_modified"] = time.time()  # Add last modified timestamp

        data_to_write = json.dumps(full_metadata, indent=4).encode("utf-8")

        # Acquire exclusive lock for the target file before writing
        await self._acquire_lock(filepath, shared=False)
        try:
            await self._atomic_write_and_set_permissions(filepath, data_to_write)
            self.logger.debug(f"File system backend: Stored key data for '{key_id}'.")
        except Exception as e:
            self.logger.error(
                f"File system backend: Failed to store key data for '{key_id}': {e}",
                exc_info=True,
            )
            raise self._CryptoOperationError(
                f"File system backend storage failed for key {key_id}: {e}"
            ) from e
        finally:
            await self._release_lock(filepath)

    async def load_key_data(self, key_id: str) -> Optional[Dict[str, Any]]:
        filepath = os.path.join(self.key_dir, f"{key_id}.json")
        if not os.path.exists(filepath):
            self.logger.debug(
                f"File system backend: Key file '{filepath}' not found.",
                extra={"key_id": key_id},
            )
            return None

        # Acquire shared lock for reading
        await self._acquire_lock(filepath, shared=True)
        try:
            await self._verify_permissions(
                filepath
            )  # Verify permissions before reading
            async with aiofiles.open(filepath, "r") as f:
                content = await f.read()
                try:
                    metadata = json.loads(content)
                except json.JSONDecodeError as e:
                    self.logger.error(
                        f"File system backend: Corrupted JSON for key '{key_id}': {e}. Content preview: {content[:100]}...",
                        exc_info=True,
                    )
                    raise self._CryptoOperationError(
                        f"Corrupted key file for {key_id}: Invalid JSON."
                    ) from e

        except Exception as e:
            # Only wrap I/O errors, not validation errors
            self.logger.error(
                f"File system backend: Failed to load key data for '{key_id}': {e}",
                exc_info=True,
            )
            if isinstance(e, self._CryptoOperationError):  # Don't re-wrap
                raise
            raise self._CryptoOperationError(
                f"File system backend load failed for key {key_id}: {e}"
            ) from e
        finally:
            await self._release_lock(filepath)

        # --- FIX 3: Validation logic is now truly outside the I/O try block ---
        # This ensures validation errors (ValueError) are raised directly without wrapping

        # Basic validation of loaded metadata
        required_meta_fields = [
            "encrypted_payload_b64",
            "algo",
            "creation_time",
            "key_id",
            "status",
        ]
        if not all(k in metadata for k in required_meta_fields):
            missing_fields = [f for f in required_meta_fields if f not in metadata]
            error_msg = f"Missing essential metadata in file for key '{key_id}'. Missing: {missing_fields}"
            self.logger.error(
                error_msg,
                extra={
                    "operation": "load_key_missing_metadata",
                    "key_id": key_id,
                    "missing_fields": missing_fields,
                },
            )
            raise ValueError(error_msg)
        if metadata["key_id"] != key_id:
            error_msg = f"Key ID mismatch in metadata for '{key_id}'. Expected '{key_id}', got '{metadata['key_id']}'."
            self.logger.error(
                error_msg,
                extra={
                    "operation": "load_key_id_mismatch",
                    "key_id": key_id,
                    "metadata_key_id": metadata["key_id"],
                },
            )
            raise ValueError(error_msg)

        return metadata

    async def list_key_metadata(self) -> List[Dict[str, Any]]:
        # Need CryptoOperationError here for the exception signature
        try:
            from .audit_crypto_provider import (
                CryptoOperationError as RealCryptoOperationError,
            )

            _CryptoOperationError = RealCryptoOperationError
        except ImportError:
            _CryptoOperationError = Exception

        keys_metadata = []
        # No specific file lock for listing directory contents, as it's a snapshot
        # and individual file reads will acquire their own locks.
        for filename in os.listdir(self.key_dir):
            if filename.endswith(".json"):
                key_id = filename[:-5]
                filepath = os.path.join(self.key_dir, filename)
                try:
                    # Acquire shared lock for reading each metadata file
                    await self._acquire_lock(filepath, shared=True)
                    try:
                        await self._verify_permissions(
                            filepath
                        )  # Verify permissions before reading
                        async with aiofiles.open(filepath, "r") as f:
                            metadata_json = json.loads(await f.read())
                        keys_metadata.append(
                            {
                                "key_id": key_id,
                                "algo": metadata_json.get("algo"),
                                "creation_time": metadata_json.get("creation_time"),
                                "status": metadata_json.get(
                                    "status", "active"
                                ),  # Default to active if status is missing
                                "retired_at": metadata_json.get(
                                    "retired_at"
                                ),  # Include retired_at if present
                                "last_modified": metadata_json.get(
                                    "last_modified"
                                ),  # Include last_modified
                            }
                        )
                    except json.JSONDecodeError as e:
                        self.logger.error(
                            f"File system backend: Corrupted JSON during list for key '{key_id}': {e}. Skipping.",
                            exc_info=True,
                        )
                        # Do not re-raise, continue processing other files
                    finally:
                        await self._release_lock(filepath)
                except Exception as e:
                    self.logger.error(
                        f"File system backend: Failed to read metadata for key file '{filename}': {e}",
                        exc_info=True,
                    )
                    # Do not re-raise, continue processing other files, but log the error
        return keys_metadata

    async def delete_key_data(self, key_id: str) -> bool:
        filepath = os.path.join(self.key_dir, f"{key_id}.json")
        if not os.path.exists(filepath):
            return False

        # Acquire exclusive lock before deleting
        await self._acquire_lock(filepath, shared=False)
        try:
            # 3. File Deletion: Verify permissions before deleting
            await self._verify_permissions(filepath)
            # For magnetic disks, consider overwriting with random data for secure erase
            # For SSDs/NVMe, this is less effective due to wear-leveling/FTL.
            # In a real production system, rely on OS/hardware secure erase features or encrypted filesystems.
            # This is a warning for the user, not something directly implementable in Python for all storage types.
            await asyncio.to_thread(os.remove, filepath)
            self.logger.debug(f"File system backend: Deleted key data for '{key_id}'.")
            return True
        except Exception as e:
            self.logger.error(
                f"File system backend: Failed to delete key data for '{key_id}': {e}",
                exc_info=True,
            )
            raise self._CryptoOperationError(
                f"File system backend deletion failed for key {key_id}: {e}"
            ) from e
        finally:
            await self._release_lock(
                filepath
            )  # Ensure lock is released even if delete fails


# --- Key Storage ---
class KeyStore:
    """
    Manages secure key storage and retrieval for software keys.
    Keys are encrypted at rest using AES-256 GCM with a master key.
    Ensures atomic file writes and integrity checks.
    Uses a pluggable backend for actual storage (defaults to FileSystemKeyStorageBackend).
    """

    def __init__(
        self,
        key_dir: str,
        master_key: bytes,
        backend: Optional[KeyStorageBackend] = None,
    ):
        """
        Initializes the KeyStore.
        Args:
            key_dir (str): The directory where encrypted keys will be stored (if using file system backend).
            master_key (bytes): The 32-byte (256-bit) master key used for AES-256 GCM encryption.
            backend (Optional[KeyStorageBackend]): An optional storage backend instance.
                                                  Defaults to FileSystemKeyStorageBackend.
        Raises:
            ValueError: If the master_key is not 32 bytes.
            TypeError: If inputs are of incorrect type.
        """
        if not isinstance(key_dir, str) or not key_dir:
            raise TypeError("key_dir must be a non-empty string.")
        if not isinstance(master_key, bytes):
            raise TypeError("master_key must be bytes.")
        if len(master_key) != 32:
            logger.critical(
                "KeyStore: Master key for at-rest encryption is not 32 bytes (AES-256). Data encryption will fail.",
                extra={"operation": "keystore_init_invalid_master_key"},
            )
            raise ValueError("Master key for KeyStore must be 32 bytes for AES-256.")

        self.master_key = master_key
        # The asyncio.Lock here protects operations *within this KeyStore instance*.
        # The backend's own locking (e.g., FileSystemKeyStorageBackend's portalocker) handles
        # cross-process synchronization.
        self.lock = asyncio.Lock()
        self.logger = logging.getLogger(f"{__name__}.KeyStore")

        # Re-apply filter to instance logger
        _add_sensitive_filter()

        if backend is None:
            self.backend: KeyStorageBackend = FileSystemKeyStorageBackend(key_dir)
            self.logger.info(
                f"KeyStore initialized with FileSystemKeyStorageBackend at '{key_dir}'."
            )
        else:
            self.backend = backend
            self.logger.info(
                f"KeyStore initialized with custom backend: {type(backend).__name__}."
            )

    async def store_key(
        self,
        key_id: str,
        key_data_bytes: bytes,
        algo: str,
        creation_time: float,
        status: str,
        retired_at: Optional[float] = None,
    ) -> None:
        """
        Stores an encrypted key with metadata to disk using the configured backend.
        Uses AES-256 GCM for encryption and authenticated additional data (AAD) for integrity.
        Args:
            key_id (str): Unique identifier for the key.
            key_data_bytes (bytes): The raw, serialized private key material (e.g., PEM, raw HMAC bytes).
            algo (str): The cryptographic algorithm of the key.
            creation_time (float): Unix timestamp of when the key was created.
            status (str): Current status of the key (e.g., "active", "retired").
            retired_at (Optional[float]): Unix timestamp when the key was retired, if applicable.
        Raises:
            TypeError: If inputs are not of the correct type.
            CryptoOperationError: For encryption, serialization, or storage backend errors.
        """
        # --- Start of Delayed Import for store_key ---
        from .audit_crypto_factory import KEY_STORE_COUNT, log_action
        from .audit_crypto_provider import CryptoOperationError

        # --- End of Delayed Import ---

        if not isinstance(key_id, str) or not key_id:
            raise TypeError("key_id must be a non-empty string.")
        if not isinstance(key_data_bytes, bytes):
            raise TypeError("key_data_bytes must be bytes.")
        if not isinstance(algo, str) or not algo:
            raise TypeError("algo must be a non-empty string.")
        if not isinstance(creation_time, (int, float)):
            raise TypeError("creation_time must be a number.")
        if not isinstance(status, str) or not status:
            raise TypeError("status must be a non-empty string.")
        if retired_at is not None and not isinstance(retired_at, (int, float)):
            raise TypeError("retired_at must be a number or None.")

        # The self.lock here protects against concurrent operations *within this KeyStore instance*.
        # The backend's own locking (e.g., FileSystemKeyStorageBackend's portalocker) handles
        # cross-process synchronization.
        async with self.lock:
            try:
                nonce = os.urandom(12)  # GCM recommended nonce size
                cipher = Cipher(
                    algorithms.AES(self.master_key),
                    modes.GCM(nonce),
                    backend=default_backend(),
                )
                encryptor = cipher.encryptor()

                # Authenticated Additional Data (AAD) for integrity protection of metadata
                # Order and content of AAD MUST be consistent between encryption and decryption
                encryptor.authenticate_additional_data(key_id.encode("utf-8"))
                encryptor.authenticate_additional_data(algo.encode("utf-8"))
                encryptor.authenticate_additional_data(
                    str(creation_time).encode("utf-8")
                )
                encryptor.authenticate_additional_data(status.encode("utf-8"))
                if retired_at is not None:
                    encryptor.authenticate_additional_data(
                        str(retired_at).encode("utf-8")
                    )

                ciphertext = encryptor.update(key_data_bytes) + encryptor.finalize()
                encrypted_payload = nonce + ciphertext + encryptor.tag

                metadata = {
                    "algo": algo,
                    "creation_time": creation_time,
                    "status": status,
                }
                if retired_at is not None:
                    metadata["retired_at"] = retired_at

                encrypted_payload_b64 = base64.b64encode(encrypted_payload).decode(
                    "utf-8"
                )

                await self.backend.store_key_data(
                    key_id, encrypted_payload_b64, metadata
                )

                self.logger.info(
                    f"Key '{key_id}' stored securely via {type(self.backend).__name__}.",
                    extra={
                        "operation": "key_store_success",
                        "key_id": key_id,
                        "algo": algo,
                        "status": status,
                    },
                )
                KEY_STORE_COUNT.labels(provider_type="software", status="success").inc()
                await log_action(
                    "key_store", key_id=key_id, algo=algo, status=status, success=True
                )

            except Exception as e:
                self.logger.error(
                    f"Failed to store key {key_id}: {e}",
                    exc_info=True,
                    extra={"operation": "key_store_fail", "key_id": key_id},
                )
                KEY_STORE_COUNT.labels(provider_type="software", status="fail").inc()
                await log_action(
                    "key_store",
                    key_id=key_id,
                    algo=algo,
                    status=status,
                    success=False,
                    error=str(e),
                )
                raise CryptoOperationError(f"Failed to store key {key_id}: {e}") from e

    async def load_key(self, key_id: str) -> Optional[Dict[str, Any]]:
        """
        Loads and decrypts a key from disk using the configured backend.
        Performs integrity check using Authenticated Additional Data (AAD).
        Args:
            key_id (str): Unique identifier of the key to load.
        Returns:
            Optional[Dict[str, Any]]: A dictionary containing 'algo', 'key_data' (bytes),
                                      'creation_time', 'status', and 'retired_at' if successful, None otherwise.
        Raises:
            TypeError: If key_id is not a string.
            CryptoOperationError: For storage backend errors, JSON parsing errors, or decryption failures.
        """
        # --- Start of Delayed Import for load_key ---
        from .audit_crypto_factory import (
            CRYPTO_ERRORS,
            KEY_LOAD_COUNT,
            log_action,
            send_alert,
        )
        from .audit_crypto_provider import CryptoOperationError

        # --- End of Delayed Import ---

        if not isinstance(key_id, str) or not key_id:
            raise TypeError("key_id must be a non-empty string.")

        async with self.lock:
            try:
                metadata = await self.backend.load_key_data(key_id)
                if metadata is None:
                    self.logger.debug(
                        f"Key '{key_id}' not found in backend {type(self.backend).__name__}.",
                        extra={"key_id": key_id},
                    )
                    await log_action(
                        "key_load", key_id=key_id, success=False, error="Key not found"
                    )
                    return None

                encrypted_payload = base64.b64decode(metadata["encrypted_payload_b64"])

                # Extract nonce, ciphertext, and tag from the concatenated payload
                nonce = encrypted_payload[:12]
                ciphertext = encrypted_payload[12:-16]
                tag = encrypted_payload[-16:]

                cipher = Cipher(
                    algorithms.AES(self.master_key),
                    modes.GCM(nonce, tag),  # Provide tag to GCM mode for verification
                    backend=default_backend(),
                )
                decryptor = cipher.decryptor()

                # Authenticate Additional Data (AAD) - MUST match the order and content used during encryption
                decryptor.authenticate_additional_data(key_id.encode("utf-8"))
                decryptor.authenticate_additional_data(metadata["algo"].encode("utf-8"))
                decryptor.authenticate_additional_data(
                    str(metadata["creation_time"]).encode("utf-8")
                )
                decryptor.authenticate_additional_data(
                    metadata.get("status", "active").encode("utf-8")
                )  # Use .get with default for backward compatibility
                if metadata.get("retired_at") is not None:
                    decryptor.authenticate_additional_data(
                        str(metadata["retired_at"]).encode("utf-8")
                    )

                key_data = (
                    decryptor.update(ciphertext) + decryptor.finalize()
                )  # Finalize performs tag verification

                self.logger.info(
                    f"Key '{key_id}' loaded and decrypted successfully.",
                    extra={
                        "operation": "key_load_success",
                        "key_id": key_id,
                        "algo": metadata["algo"],
                    },
                )
                KEY_LOAD_COUNT.labels(provider_type="software", status="success").inc()
                await log_action(
                    "key_load",
                    key_id=key_id,
                    algo=metadata["algo"],
                    status=metadata.get("status"),
                    success=True,
                )

                return {
                    "algo": metadata["algo"],
                    "key_data": key_data,
                    "creation_time": metadata["creation_time"],
                    "status": metadata.get("status", "active"),
                    "retired_at": metadata.get("retired_at"),
                }
            except InvalidTag:
                self.logger.error(
                    f"KeyStore: Integrity check failed for key '{key_id}'. Possible tampering or wrong master key. Data: {metadata.get('encrypted_payload_b64', 'N/A')}",
                    extra={"operation": "key_load_integrity_fail", "key_id": key_id},
                )
                CRYPTO_ERRORS.labels(
                    type="KeyTampering", provider_type="software", operation="load_key"
                ).inc()
                asyncio.create_task(
                    send_alert(
                        f"Audit key '{key_id}' integrity check failed. Possible tampering detected!",
                        severity="critical",
                    )
                )
                await log_action(
                    "key_load",
                    key_id=key_id,
                    success=False,
                    error="Integrity check failed (InvalidTag)",
                )
                # --- FIX 4: Change exception message to match test assertion ---
                raise CryptoOperationError(
                    f"Integrity check failed for key {key_id}. Possible tampering or wrong master key."
                ) from InvalidTag
            except Exception as e:
                self.logger.error(
                    f"Failed to load or decrypt key '{key_id}': {e}",
                    exc_info=True,
                    extra={"operation": "key_load_fail", "key_id": key_id},
                )
                CRYPTO_ERRORS.labels(
                    type=type(e).__name__,
                    provider_type="software",
                    operation="load_key",
                ).inc()
                await log_action("key_load", key_id=key_id, success=False, error=str(e))
                raise CryptoOperationError(f"Failed to load key {key_id}: {e}") from e

    async def list_keys(self) -> List[Dict[str, Any]]:
        """
        Lists all keys with their metadata (excluding actual key data) stored in the key directory.
        Returns:
            List[Dict[str, Any]]: A list of dictionaries, each containing metadata for a key.
        Raises:
            CryptoOperationError: If the backend fails to list key metadata.
        """
        # --- Start of Delayed Import for list_keys ---
        from .audit_crypto_factory import CRYPTO_ERRORS, log_action
        from .audit_crypto_provider import CryptoOperationError

        # --- End of Delayed Import ---

        async with self.lock:
            try:
                keys_metadata = await self.backend.list_key_metadata()
                self.logger.info(
                    f"Listed {len(keys_metadata)} keys from {type(self.backend).__name__}.",
                    extra={
                        "operation": "list_keys_success",
                        "count": len(keys_metadata),
                    },
                )
                return keys_metadata
            except Exception as e:
                self.logger.error(
                    f"Failed to list keys from backend {type(self.backend).__name__}: {e}",
                    exc_info=True,
                )
                CRYPTO_ERRORS.labels(
                    type=type(e).__name__,
                    provider_type="software",
                    operation="list_keys",
                ).inc()
                await log_action("list_keys", success=False, error=str(e))
                raise CryptoOperationError(f"Failed to list keys: {e}") from e

    async def delete_key_file(self, key_id: str) -> bool:
        """
        Securely deletes a key file from disk using the configured backend.
        Args:
            key_id (str): Unique identifier of the key file to delete.
        Returns:
            bool: True if the file was deleted, False otherwise (e.g., if not found or error).
        Raises:
            TypeError: If key_id is not a string.
            CryptoOperationError: For storage backend errors during deletion.
        """
        # --- Start of Delayed Import for delete_key_file ---
        from .audit_crypto_factory import CRYPTO_ERRORS, log_action
        from .audit_crypto_provider import CryptoOperationError

        # --- End of Delayed Import ---

        if not isinstance(key_id, str) or not key_id:
            raise TypeError("key_id must be a non-empty string.")

        async with self.lock:
            try:
                # FilePath is only needed for checking existence and permissions before delegating to backend
                # This assumes self.backend has a key_dir attribute, which is true for FileSystemKeyStorageBackend
                filepath = (
                    os.path.join(self.backend.key_dir, f"{key_id}.json")
                    if isinstance(self.backend, FileSystemKeyStorageBackend)
                    else None
                )

                if filepath and os.path.exists(
                    filepath
                ):  # Only verify permissions if file exists
                    # We must cast the backend to the concrete type to call the protected method
                    if isinstance(self.backend, FileSystemKeyStorageBackend):
                        await self.backend._verify_permissions(filepath)

                deleted = await self.backend.delete_key_data(key_id)
                if deleted:
                    self.logger.info(
                        f"Securely deleted key '{key_id}' via {type(self.backend).__name__}.",
                        extra={
                            "operation": "delete_key_file_success",
                            "key_id": key_id,
                        },
                    )
                    await log_action("key_delete", key_id=key_id, success=True)
                else:
                    self.logger.warning(
                        f"Attempted to delete key '{key_id}', but it was not found or could not be deleted by backend {type(self.backend).__name__}.",
                        extra={
                            "operation": "delete_key_file_not_found_or_fail",
                            "key_id": key_id,
                        },
                    )
                    await log_action(
                        "key_delete",
                        key_id=key_id,
                        success=False,
                        error="Key not found or backend failed to delete",
                    )
                return deleted
            except Exception as e:
                self.logger.error(
                    f"Failed to delete key file for '{key_id}' via {type(self.backend).__name__}: {e}",
                    exc_info=True,
                    extra={"operation": "delete_key_file_fail", "key_id": key_id},
                )
                CRYPTO_ERRORS.labels(
                    type=type(e).__name__,
                    provider_type="software",
                    operation="delete_key_file",
                ).inc()
                await log_action(
                    "key_delete", key_id=key_id, success=False, error=str(e)
                )
                raise CryptoOperationError(f"Failed to delete key {key_id}: {e}") from e


"""
Security & Limitations:
- Atomic file writes are enforced with temp files and os.replace.
- Permissions are always set to 0o600 on key files.
- POSIX advisory locks prevent concurrent cross-process access.
- Cannot guarantee OS-level secure erase or in-memory zeroization (Python/OS limitation).
- Not suitable for production where strong key lifecycle or multi-host access is required.
"""
