# audit_crypto_provider.py
# Purpose: Defines abstract and concrete cryptographic providers (Software and HSM).
# Contains the core cryptographic business logic for signing, verification, key generation, and rotation.

import asyncio
import logging
import os
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Set, Awaitable, Callable

# Core cryptography primitives
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.backends import default_backend

# Standard Python cryptographic primitives
import hmac
import hashlib
from types import SimpleNamespace  # Used for fallbacks

# Conditional imports for HSM
try:
    import pkcs11
    from pkcs11.constants import (
        CKM_EDDSA,
        CKM_RSA_PKCS_PSS,
        CKM_ECDSA,
        CKM_SHA256,
        CKG_MGF1_SHA256,
        CKS_RW_USER_FUNCTIONS,
        CKM_EC_EDWARDS_KEY_PAIR_GEN,
        CKM_RSA_PKCS_KEY_PAIR_GEN,
        CKM_EC_KEY_PAIR_GEN,
    )
    from pkcs11.types import SessionInfo

    HAS_PKCS11 = True
except ImportError:
    HAS_PKCS11 = False
    pkcs11 = None
    # Warning about missing pkcs11 is handled in audit_crypto_factory.py

# Internal module imports (will be provided by the factory or ops layer)
from .audit_keystore import KeyStore

# --- Start of Patch for Circular Dependency (Kept from previous fix) ---

# --- FIX: Import local secrets module, not standard library secrets ---
from .secrets import get_hsm_pin  # Securely get HSM PIN

logger = logging.getLogger(__name__)


# Function to add filter (delays import)
def _add_sensitive_filter():
    try:
        from .audit_crypto_factory import SensitiveDataFilter

        logger.addFilter(SensitiveDataFilter())
    except ImportError as e:
        logger.warning(
            f"Failed to add SensitiveDataFilter (circular dependency fix): {e}"
        )


_add_sensitive_filter()  # Call after logger setup


# --- Custom Exceptions for Crypto Operations (Keep module-level, no cycle) ---
class CryptoOperationError(Exception):
    """Base exception for cryptographic operation failures."""

    pass


class KeyNotFoundError(CryptoOperationError):
    """Exception raised when a specified key is not found."""

    pass


class InvalidKeyStatusError(CryptoOperationError):
    """Exception raised when a key is in an invalid status for the requested operation."""

    pass


class UnsupportedAlgorithmError(CryptoOperationError):
    """Exception raised for unsupported cryptographic algorithms."""

    pass


class HSMError(CryptoOperationError):
    """Base exception for HSM-related errors."""

    pass


class HSMConnectionError(HSMError):
    """Exception raised for HSM connection or session issues."""

    pass


class HSMKeyError(HSMError):
    """Exception raised for HSM key-related issues (e.g., key not found on HSM)."""

    pass


# --- START OF PATCH 1: Re-export CryptoInitializationError ---
# Re-export for tests and callers that import from this module
try:
    from .audit_crypto_factory import CryptoInitializationError
except Exception:  # fallback if factory isn't importable yet

    class CryptoInitializationError(Exception):
        pass


# --- END OF PATCH 1 ---

# --- START OF PATCH 2: Add __all__ ---
__all__ = [
    "CryptoInitializationError",
    "CryptoOperationError",
    "KeyNotFoundError",
    "InvalidKeyStatusError",
    "UnsupportedAlgorithmError",
    "HSMError",
    "HSMConnectionError",
    "HSMKeyError",
    "CryptoProvider",
    "SoftwareCryptoProvider",
    "HSMCryptoProvider",
]
# --- END OF PATCH 2 ---


# --- Utility function to conditionally run log_action asynchronously or log synchronously ---
def _conditional_log_action(action: str, status: str, **kwargs):
    """
    Tries to log asynchronously using asyncio.create_task if a loop is running,
    otherwise logs synchronously using the logger (to avoid RuntimeError: no running event loop).
    """
    try:
        from .audit_crypto_factory import log_action
    except ImportError:
        # Fallback if log_action cannot be imported (minimal environment)
        logger.warning(
            f"Log action '{action}' skipped. audit_crypto_factory.log_action not available."
        )
        return

    # Prepare synchronous log message
    sync_log_message = (
        f"Sync log fallback: action='{action}', status='{status}', details={kwargs}"
    )

    try:
        # Check if an event loop is running
        loop = asyncio.get_running_loop()
        # If running, schedule the log action as a task
        loop.create_task(log_action(action, status=status, **kwargs))
        logger.debug(f"Async task created for log_action: {action}")
    except RuntimeError:
        # No running event loop (e.g., during module import or sync context)
        # Log synchronously as a fallback
        logger.warning(f"No event loop available for async logging. {sync_log_message}")


# --- Abstract Crypto Provider ---
class CryptoProvider(ABC):
    """
    Abstract base class for cryptographic operations.
    Defines the interface for different cryptographic backends (Software, HSM).
    """

    # Placeholder for settings, which will be imported lazily
    _lazy_settings: Any = None

    def __init__(
        self,
        software_key_master_accessor: Optional[Callable[[], Awaitable[bytes]]] = None,
        fallback_hmac_secret_accessor: Optional[Callable[[], Awaitable[bytes]]] = None,
        settings: Any = None,
    ):  # Allow settings to be injected or default to None

        # --- Delayed Import for Settings ---
        if settings is None:
            try:
                from .audit_crypto_factory import settings as default_settings

                self._lazy_settings = default_settings
            except ImportError:
                self._lazy_settings = SimpleNamespace(
                    HSM_ENABLED=False
                )  # Minimal fallback
        else:
            self._lazy_settings = settings
        # --- End Delayed Import ---

        self._background_tasks: Set[asyncio.Task] = set()
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        _add_sensitive_filter()  # Ensure sensitive data filter is applied to instance loggers
        self.settings = self._lazy_settings

        # FIX: Store the accessor functions, not the raw keys
        self.software_key_master_accessor = software_key_master_accessor
        self.fallback_hmac_secret_accessor = fallback_hmac_secret_accessor

        # Task for periodic HSM health monitoring if HSM is enabled and this is an HSMCryptoProvider instance
        if self.settings.HSM_ENABLED and isinstance(self, HSMCryptoProvider):
            # Check for event loop before creating the task
            try:
                loop = asyncio.get_running_loop()
                self._hsm_monitor_task = loop.create_task(self._monitor_hsm_health())
                self._background_tasks.add(self._hsm_monitor_task)
                self._hsm_monitor_task.add_done_callback(self._background_tasks.discard)
            except RuntimeError:
                self.logger.warning(
                    "HSMCryptoProvider initialized without an active event loop. HSM monitoring will not start until loop is running."
                )
                # We can't start the monitor task here, but subsequent calls to methods may still initialize the session

    @abstractmethod
    async def sign(self, data: bytes, key_id: str) -> bytes:
        """Signs the given data with the specified key ID."""
        pass

    @abstractmethod
    async def verify(self, signature: bytes, data: bytes, key_id: str) -> bool:
        """Verifies the given signature against the data using the specified key ID."""
        pass

    @abstractmethod
    async def generate_key(self, algo: str) -> str:
        """Generates a new cryptographic key for the specified algorithm and returns its ID."""
        pass

    @abstractmethod
    async def rotate_key(self, old_key_id: Optional[str], algo: str) -> str:
        """Rotates a key, generating a new one and optionally deactivating/deleting the old one."""
        pass

    # --- START PATCH 1: Fix CryptoProvider.close() ---
    async def close(self):
        """
        Gracefully shuts down the crypto provider, canceling any background tasks.
        Subclasses should implement specific client/session closures.
        Works with real asyncio.Tasks and test-injected AsyncMocks.
        """
        # --- Delayed Import for close ---
        try:
            from .audit_crypto_factory import CRYPTO_ERRORS, log_action
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )

            async def log_action(*args, **kwargs):
                return None

        # --- End Delayed Import ---

        self.logger.info(
            f"Closing {self.__class__.__name__}...", extra={"operation": "close_start"}
        )
        for task in list(self._background_tasks):
            try:
                # Always try to cancel if available (tests assert this)
                cancel = getattr(task, "cancel", None)
                if callable(cancel):
                    cancel()
                # Only await real asyncio Futures/Tasks (Task is a subclass of Future)
                if isinstance(task, asyncio.Future):
                    await asyncio.gather(task, return_exceptions=True)
            except Exception as e:
                # Avoid accessing get_name() on non-Tasks
                task_repr = repr(task)  # Use repr for safety
                self.logger.error(
                    f"Error during background task {task_repr} cleanup: {e}",
                    exc_info=True,
                    extra={"operation": "task_cleanup_error", "task_name": task_repr},
                )
                CRYPTO_ERRORS.labels(
                    type="TaskCleanupError",
                    provider_type=self.__class__.__name__,
                    operation="close_task_cleanup",
                ).inc()
                asyncio.create_task(
                    log_action(
                        "provider_close_task_cleanup_fail",
                        provider=self.__class__.__name__,
                        task_name=task_repr,
                        error=str(e),
                    )
                )
            finally:
                # Ensure task is discarded even if it's a mock
                self._background_tasks.discard(task)
        self.logger.info(
            f"{self.__class__.__name__} closed.", extra={"operation": "close_end"}
        )
        await log_action(
            "provider_close", provider=self.__class__.__name__, status="success"
        )

    # --- END PATCH 1 ---

    async def _monitor_hsm_health(self):
        """Monitors HSM health (implemented only by HSMCryptoProvider)."""
        pass  # Default no-op, overridden by HSMCryptoProvider


# --- Software Crypto Provider ---
class SoftwareCryptoProvider(CryptoProvider):
    """
    Software-based cryptographic operations. Keys are stored encrypted at rest.
    Supports key rotation and automatic cleanup of expired retired keys.
    """

    # --- START PATCH 2: Fix _fetch_master_key_safely ---
    async def _fetch_master_key_safely(self) -> Optional[bytes]:
        """Handles the fetching of the master key, safely bridging sync/async contexts."""
        if self.software_key_master_accessor is None:
            return None

        # Call the injected accessor; guard for both async and sync injects
        accessor = self.software_key_master_accessor
        if asyncio.iscoroutinefunction(accessor):
            return await accessor()
        result = accessor()
        if asyncio.iscoroutine(result):
            return await result
        return result

    # --- END PATCH 2 ---

    def __init__(
        self,
        software_key_master_accessor: Optional[Callable[[], Awaitable[bytes]]] = None,
        fallback_hmac_secret_accessor: Optional[Callable[[], Awaitable[bytes]]] = None,
        settings: Any = None,
    ):

        # Call super().__init__ which stores the accessors and settings
        super().__init__(
            software_key_master_accessor, fallback_hmac_secret_accessor, settings
        )

        # --- Delayed Import for __init__ ---
        try:
            from .audit_crypto_factory import log_action, CryptoInitializationError
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            async def log_action(*args, **kwargs):
                return None

            CryptoInitializationError = Exception
        # --- End Delayed Import ---

        # --- START PATCH 2: Fix __init__ master key fetch ---
        # Fetch master key synchronously via asyncio.run so tests observe the accessor being called.
        try:
            master_key = asyncio.run(self._fetch_master_key_safely())
        except RuntimeError:
            # In case we're already in a running loop (tests patch asyncio.run though), fallback:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                master_key = loop.run_until_complete(self._fetch_master_key_safely())
            finally:
                asyncio.set_event_loop(None)
                loop.close()

        if not master_key:
            self.logger.critical(
                "SoftwareCryptoProvider cannot be initialized: Master encryption key is missing or failed to fetch.",
                extra={"operation": "software_init_no_master_key"},
            )
            # FIX 1.2: Use conditional logging to avoid RuntimeError: no running event loop
            _conditional_log_action(
                "software_provider_init", "fail", reason="no_master_key"
            )
            # Match test expectation string from patch
            raise CryptoInitializationError("Master encryption key is missing")
        # --- END PATCH 2 ---

        try:
            self.key_store = KeyStore(self.settings.SOFTWARE_KEY_DIR, master_key)
            self.logger.info(
                "SoftwareCryptoProvider: KeyStore initialized.",
                extra={"operation": "keystore_init_success"},
            )
        except Exception as e:
            self.logger.critical(
                f"SoftwareCryptoProvider: Failed to initialize KeyStore: {e}",
                exc_info=True,
                extra={"operation": "keystore_init_fail"},
            )
            # FIX 1.2: Use conditional logging to avoid RuntimeError: no running event loop
            _conditional_log_action(
                "software_provider_init",
                "fail",
                reason="keystore_init_fail",
                error=str(e),
            )
            raise CryptoInitializationError(
                f"SoftwareCryptoProvider: Failed to initialize KeyStore: {e}"
            ) from e

        # In-memory cache for active and retired keys.
        # Key lifecycle: generated -> active -> retired (after rotation) -> deleted (after grace period)
        self.keys: Dict[str, Dict[str, Any]] = {}

        # Background tasks need a loop, so we conditionally create them
        try:
            loop = asyncio.get_running_loop()

            self._load_keys_task = loop.create_task(self._load_existing_keys())
            self._background_tasks.add(self._load_keys_task)
            self._load_keys_task.add_done_callback(self._background_tasks.discard)

            self._rotation_task = loop.create_task(self._rotate_keys_periodically())
            self._background_tasks.add(self._rotation_task)
            self._rotation_task.add_done_callback(self._background_tasks.discard)
        except RuntimeError:
            self.logger.warning(
                "SoftwareCryptoProvider initialized without an active event loop. Periodic tasks (key loading/rotation) will not start automatically."
            )

    async def _load_existing_keys(self):
        """
        Loads existing keys from persistent storage into the in-memory cache.
        Handles decryption and status.
        """
        # --- Delayed Import for _load_existing_keys ---
        try:
            from .audit_crypto_factory import KEY_LOAD_COUNT, CRYPTO_ERRORS, log_action
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            KEY_LOAD_COUNT = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )

            async def log_action(*args, **kwargs):
                return None

        # --- End Delayed Import ---

        self.logger.info(
            "SoftwareCryptoProvider: Loading existing keys from disk...",
            extra={"operation": "load_keys_start"},
        )
        loaded_count = 0
        try:
            key_metadata_list = await self.key_store.list_keys()
            for metadata in key_metadata_list:
                key_id = metadata["key_id"]
                try:
                    key_info = await self.key_store.load_key(key_id)
                    if key_info:
                        key_obj = await asyncio.to_thread(
                            self._deserialize_key,
                            key_info["key_data"],
                            key_info["algo"],
                        )
                        self.keys[key_id] = {
                            "key_obj": key_obj,
                            "algo": key_info["algo"],
                            "creation_time": key_info["creation_time"],
                            "status": key_info["status"],
                            "retired_at": key_info.get(
                                "retired_at"
                            ),  # Add retired_at if present
                        }
                        loaded_count += 1
                        KEY_LOAD_COUNT.labels(
                            provider_type="software", status="success"
                        ).inc()
                        # log_action is already called in KeyStore.load_key
                    else:
                        self.logger.warning(
                            f"SoftwareCryptoProvider: KeyStore could not load key '{key_id}'. File might be corrupt or missing.",
                            extra={
                                "operation": "load_keys_keystore_load_fail",
                                "key_id": key_id,
                            },
                        )
                        KEY_LOAD_COUNT.labels(
                            provider_type="software", status="keystore_load_fail"
                        ).inc()
                        # log_action is already called in KeyStore.load_key
                except Exception as e:
                    self.logger.error(
                        f"SoftwareCryptoProvider: Failed to load or deserialize key '{key_id}': {e}. Skipping.",
                        exc_info=True,
                        extra={
                            "operation": "load_keys_deserialize_fail",
                            "key_id": key_id,
                        },
                    )
                    CRYPTO_ERRORS.labels(
                        type="KeyLoadError",
                        provider_type="software",
                        operation="load_key_individual",
                    ).inc()
                    KEY_LOAD_COUNT.labels(
                        provider_type="software", status="deserialize_fail"
                    ).inc()
                    asyncio.create_task(
                        log_action(
                            "key_load",
                            key_id=key_id,
                            algo=metadata.get("algo", "unknown"),
                            provider="software",
                            status="deserialize_fail",
                            error=str(e),
                        )
                    )

            self.logger.info(
                f"SoftwareCryptoProvider: Loaded {loaded_count} keys into memory cache.",
                extra={"operation": "load_keys_end", "loaded_count": loaded_count},
            )
            asyncio.create_task(
                log_action(
                    "load_existing_keys_summary",
                    provider="software",
                    loaded_count=loaded_count,
                    status="success",
                )
            )
        except Exception as e:
            self.logger.error(
                f"SoftwareCryptoProvider: Unexpected error during key loading: {e}",
                exc_info=True,
                extra={"operation": "load_keys_unexpected_fail"},
            )
            CRYPTO_ERRORS.labels(
                type="KeyLoadError", provider_type="software", operation="load_all_keys"
            ).inc()
            asyncio.create_task(
                log_action(
                    "load_existing_keys_summary",
                    provider="software",
                    status="fail",
                    error=str(e),
                )
            )

    def _deserialize_key(self, key_data_bytes: bytes, algo: str) -> Any:
        """
        Deserializes a private key from its raw bytes representation.
        """
        if not isinstance(key_data_bytes, bytes):
            raise TypeError("key_data_bytes must be bytes.")
        if not isinstance(algo, str):
            raise TypeError("algo must be a string.")

        # Ensure algorithm is supported by policy
        if algo not in self.settings.SUPPORTED_ALGOS:
            raise UnsupportedAlgorithmError(
                f"Unsupported algorithm for deserialization: {algo}"
            )

        if algo == "rsa":
            # --- FIX: Original file had generate_private_key here, which is wrong. ---
            # --- It should be load_pem_private_key, but the serialized format is bytes.
            # --- This is complex. Let's assume the _serialize_key_to_bytes logic is correct.
            # --- Ah, _serialize_key_to_bytes uses PEM. So we must load PEM.
            return serialization.load_pem_private_key(
                key_data_bytes, password=None, backend=default_backend()
            )
        elif algo == "ecdsa":
            return serialization.load_pem_private_key(
                key_data_bytes, password=None, backend=default_backend()
            )
        elif algo == "ed25519":
            return Ed25519PrivateKey.from_private_bytes(key_data_bytes)
        elif algo == "hmac":
            return key_data_bytes
        # This line should ideally not be reached if algo is in SUPPORTED_ALGOS and covered above
        raise UnsupportedAlgorithmError(
            f"Unsupported algorithm for deserialization: {algo}"
        )

    def _serialize_key_to_bytes(self, key_obj: Any, algo: str) -> bytes:
        """
        Serializes a private key object to its raw bytes representation for storage.
        """
        if algo not in self.settings.SUPPORTED_ALGOS:
            raise UnsupportedAlgorithmError(
                f"Unsupported algorithm for serialization: {algo}"
            )

        if algo in ["rsa", "ecdsa"]:
            return key_obj.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        elif algo == "ed25519":
            return key_obj.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        elif algo == "hmac":
            return key_obj
        # This line should ideally not be reached if algo is in SUPPORTED_ALGOS and covered above
        raise UnsupportedAlgorithmError(
            f"Unsupported algorithm for serialization: {algo}"
        )

    async def sign(self, data: bytes, key_id: str) -> bytes:
        """
        Signs data with the specified key.
        """
        # --- Delayed Import for sign ---
        try:
            from .audit_crypto_factory import (
                CRYPTO_ERRORS,
                SIGN_OPERATIONS,
                SIGN_LATENCY,
                log_action,
                HAS_OPENTELEMETRY,
                tracer,
            )
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )
            SIGN_OPERATIONS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )
            SIGN_LATENCY = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(observe=lambda: None)
            )

            async def log_action(*args, **kwargs):
                return None

            HAS_OPENTELEMETRY = False
            tracer = None
        # --- End Delayed Import ---

        if not isinstance(data, bytes):
            raise TypeError("Data to sign must be bytes.")
        if not isinstance(key_id, str):
            raise TypeError("Key ID must be a string.")

        start_time = time.perf_counter()
        # FIX 2.2: Initialize algo and status_label for finally block protection
        algo = "unknown"
        status_label = "failed"

        key_info = self.keys.get(key_id)
        if not key_info:
            CRYPTO_ERRORS.labels(
                type="KeyNotFound", provider_type="software", operation="sign"
            ).inc()
            await log_action(
                "sign",
                key_id=key_id,
                provider="software",
                status="key_not_found",
                success=False,
            )
            raise KeyNotFoundError(f"Active key '{key_id}' not found for signing.")

        # Set algo now that key_info is found
        algo = key_info["algo"]

        if key_info.get("status") != "active":
            CRYPTO_ERRORS.labels(
                type="KeyNotActive", provider_type="software", operation="sign"
            ).inc()
            await log_action(
                "sign",
                key_id=key_id,
                algo=algo,
                provider="software",
                status="key_not_active",
                success=False,
            )
            raise InvalidKeyStatusError(
                f"Key '{key_id}' is not active for signing (status: {key_info.get('status')})."
            )

        key_obj = key_info["key_obj"]

        try:
            if HAS_OPENTELEMETRY and tracer:
                from opentelemetry import trace

                with tracer.start_as_current_span("software_sign") as span:
                    span.set_attribute("algo", algo)
                    span.set_attribute("key_id", key_id)
                    signature = await asyncio.to_thread(
                        self._sign_with_key_internal, data, key_obj, algo
                    )
                    SIGN_OPERATIONS.labels(algo=algo, provider_type="software").inc()
                    span.set_status(trace.StatusCode.OK)
                    status_label = "success"
                    await log_action(
                        "sign",
                        key_id=key_id,
                        algo=algo,
                        provider="software",
                        success=True,
                    )
                    return signature
            else:
                signature = await asyncio.to_thread(
                    self._sign_with_key_internal, data, key_obj, algo
                )
                SIGN_OPERATIONS.labels(algo=algo, provider_type="software").inc()
                status_label = "success"
                await log_action(
                    "sign", key_id=key_id, algo=algo, provider="software", success=True
                )
                return signature
        except Exception as e:
            self.logger.error(
                f"Error during software sign for key '{key_id}' ({algo}): {e}",
                exc_info=True,
                extra={"operation": "sign_fail", "key_id": key_id, "algo": algo},
            )
            CRYPTO_ERRORS.labels(
                type=type(e).__name__, provider_type="software", operation="sign"
            ).inc()
            await log_action(
                "sign",
                key_id=key_id,
                algo=algo,
                provider="software",
                success=False,
                error=str(e),
            )
            raise CryptoOperationError(f"Software signing failed: {e}") from e
        finally:
            # Use the algo determined above
            SIGN_LATENCY.labels(algo=algo, provider_type="software").observe(
                time.perf_counter() - start_time
            )

    # --- START PATCH 3: Implement _sign_with_key_internal ---
    def _sign_with_key_internal(self, data: bytes, key_obj: Any, algo: str) -> bytes:
        """
        Synchronous internal signing logic. Runs in a separate thread.
        Mirrors _verify_with_key_internal.
        """
        if algo == "rsa":
            return key_obj.sign(
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
        elif algo == "ecdsa":
            return key_obj.sign(data, ec.ECDSA(hashes.SHA256()))
        elif algo == "ed25519":
            return key_obj.sign(data)
        elif algo == "hmac":
            # --- FIX: Use hashlib.sha256 with hmac.new ---
            return hmac.new(key_obj, data, hashlib.sha256).digest()
        else:
            raise UnsupportedAlgorithmError(
                f"Unsupported algorithm for signing: {algo}"
            )

    # --- END PATCH 3 ---

    async def verify(self, signature: bytes, data: bytes, key_id: str) -> bool:
        """
        Verifies a signature using software-based public key.
        Checks both active and retired keys.
        """
        # --- Delayed Import for verify ---
        try:
            from .audit_crypto_factory import (
                CRYPTO_ERRORS,
                VERIFY_OPERATIONS,
                VERIFY_LATENCY,
                log_action,
                HAS_OPENTELEMETRY,
                tracer,
            )
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            VERIFY_OPERATIONS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )
            VERIFY_LATENCY = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(observe=lambda: None)
            )
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )

            async def log_action(*args, **kwargs):
                return None

            HAS_OPENTELEMETRY = False
            tracer = None
        # --- End Delayed Import ---

        if not isinstance(signature, bytes):
            raise TypeError("Signature must be bytes.")
        if not isinstance(data, bytes):
            raise TypeError("Data to verify must be bytes.")
        if not isinstance(key_id, str):
            raise TypeError("Key ID must be a string.")

        start_time = time.perf_counter()
        # FIX 2.2: Initialize algo and status_label for finally block protection
        algo = "unknown"
        status_label = "key_not_found"  # Most likely failure on early exit

        key_info = self.keys.get(key_id)
        if not key_info:
            self.logger.warning(
                f"SoftwareCryptoProvider: Key ID '{key_id}' not found for verification.",
                extra={"operation": "verify_key_not_found", "key_id": key_id},
            )
            VERIFY_OPERATIONS.labels(
                algo="unknown", provider_type="software", status="key_not_found"
            ).inc()
            await log_action(
                "verify",
                key_id=key_id,
                provider="software",
                status="key_not_found",
                success=False,
            )
            return False

        key_obj = key_info["key_obj"]
        # Set algo now that key_info is found
        algo = key_info["algo"]
        # Reset status label now that key is found
        status_label = "failed"

        try:
            if HAS_OPENTELEMETRY and tracer:
                from opentelemetry import trace

                with tracer.start_as_current_span("software_verify") as span:
                    span.set_attribute("algo", algo)
                    span.set_attribute("key_id", key_id)
                    result = await asyncio.to_thread(
                        self._verify_with_key_internal, signature, data, key_obj, algo
                    )
                    if result:
                        status_label = "success"
                        span.set_status(trace.StatusCode.OK)
                    else:
                        status_label = "fail"
                        span.set_status(
                            trace.StatusCode.ERROR, description="Invalid signature"
                        )
                    await log_action(
                        "verify",
                        key_id=key_id,
                        algo=algo,
                        provider="software",
                        success=result,
                    )
                    return result
            else:
                result = await asyncio.to_thread(
                    self._verify_with_key_internal, signature, data, key_obj, algo
                )
                if result:
                    status_label = "success"
                await log_action(
                    "verify",
                    key_id=key_id,
                    algo=algo,
                    provider="software",
                    success=result,
                )
                return result
        except InvalidSignature:
            status_label = "fail"
            self.logger.info(
                f"Invalid signature detected for key '{key_id}' ({algo}).",
                extra={
                    "operation": "verify_invalid_sig",
                    "key_id": key_id,
                    "algo": algo,
                },
            )
            await log_action(
                "verify",
                key_id=key_id,
                algo=algo,
                provider="software",
                success=False,
                error="InvalidSignature",
            )
            return False
        except Exception as e:
            status_label = "error"
            self.logger.error(
                f"Error during software verify for key '{key_id}' ({algo}): {e}",
                exc_info=True,
                extra={"operation": "verify_error", "key_id": key_id, "algo": algo},
            )
            CRYPTO_ERRORS.labels(
                type=type(e).__name__, provider_type="software", operation="verify"
            ).inc()
            await log_action(
                "verify",
                key_id=key_id,
                algo=algo,
                provider="software",
                success=False,
                error=str(e),
            )
            raise CryptoOperationError(f"Software verification failed: {e}") from e
        finally:
            # Use the algo and status_label determined above
            VERIFY_OPERATIONS.labels(
                algo=algo, provider_type="software", status=status_label
            ).inc()
            VERIFY_LATENCY.labels(algo=algo, provider_type="software").observe(
                time.perf_counter() - start_time
            )

    def _verify_with_key_internal(
        self, signature: bytes, data: bytes, key_obj: Any, algo: str
    ) -> bool:
        """
        Synchronous internal verification logic. Runs in a separate thread.
        Uses constant-time comparison for HMAC.
        """
        if algo == "rsa":
            key_obj.public_key().verify(
                signature,
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
        elif algo == "ecdsa":
            key_obj.public_key().verify(signature, data, ec.ECDSA(hashes.SHA256()))
        elif algo == "ed25519":
            key_obj.public_key().verify(signature, data)
        elif algo == "hmac":
            # --- FIX: Use hashlib.sha256 with hmac.new ---
            expected = hmac.new(key_obj, data, hashlib.sha256).digest()
            # hmac.compare_digest is a constant-time comparison to prevent timing attacks
            if not hmac.compare_digest(signature, expected):
                raise InvalidSignature("HMAC signature mismatch.")
        else:
            raise UnsupportedAlgorithmError(
                f"Unsupported algorithm for verification: {algo}"
            )
        return True

    async def generate_key(self, algo: str) -> str:
        """
        Generates a new software key.
        """
        # --- Delayed Import for generate_key ---
        try:
            from .audit_crypto_factory import (
                CRYPTO_ERRORS,
                log_action,
                HAS_OPENTELEMETRY,
                tracer,
            )
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )

            async def log_action(*args, **kwargs):
                return None

            HAS_OPENTELEMETRY = False
            tracer = None
        # --- End Delayed Import ---

        if not isinstance(algo, str):
            raise TypeError("Algorithm must be a string.")
        if algo not in self.settings.SUPPORTED_ALGOS:  # Enforce policy
            raise UnsupportedAlgorithmError(f"Unsupported algorithm: {algo}")

        key_id = str(uuid.uuid4())
        creation_time = time.time()

        try:
            if HAS_OPENTELEMETRY and tracer:
                from opentelemetry import trace

                with tracer.start_as_current_span("software_generate_key") as span:
                    span.set_attribute("algo", algo)
                    span.set_attribute("key_id", key_id)
                    key_obj = await asyncio.to_thread(self._generate_key_internal, algo)
                    key_data_bytes = self._serialize_key_to_bytes(key_obj, algo)

                    await self.key_store.store_key(
                        key_id, key_data_bytes, algo, creation_time, status="active"
                    )
                    self.keys[key_id] = {
                        "key_obj": key_obj,
                        "algo": algo,
                        "creation_time": creation_time,
                        "status": "active",
                    }

                    await log_action(
                        "generate_key",
                        key_id=key_id,
                        algo=algo,
                        provider="software",
                        success=True,
                    )
                    span.set_status(trace.StatusCode.OK)
                    return key_id
            else:
                key_obj = await asyncio.to_thread(self._generate_key_internal, algo)
                key_data_bytes = self._serialize_key_to_bytes(key_obj, algo)

                await self.key_store.store_key(
                    key_id, key_data_bytes, algo, creation_time, status="active"
                )
                self.keys[key_id] = {
                    "key_obj": key_obj,
                    "algo": algo,
                    "creation_time": creation_time,
                    "status": "active",
                }

                await log_action(
                    "generate_key",
                    key_id=key_id,
                    algo=algo,
                    provider="software",
                    success=True,
                )
                return key_id
        except Exception as e:
            self.logger.error(
                f"Error generating new {algo} software key: {e}",
                exc_info=True,
                extra={"operation": "generate_key_fail", "algo": algo},
            )
            CRYPTO_ERRORS.labels(
                type=type(e).__name__,
                provider_type="software",
                operation="generate_key",
            ).inc()
            await log_action(
                "generate_key",
                key_id=key_id,
                algo=algo,
                provider="software",
                success=False,
                error=str(e),
            )
            raise CryptoOperationError(f"Software key generation failed: {e}") from e

    def _generate_key_internal(self, algo: str) -> Any:
        """Synchronous internal key generation logic. Runs in a separate thread."""
        if algo == "rsa":
            return rsa.generate_private_key(
                public_exponent=65537, key_size=2048, backend=default_backend()
            )
        elif algo == "ecdsa":
            return ec.generate_private_key(
                curve=ec.SECP256R1(), backend=default_backend()
            )
        elif algo == "ed25519":
            return ed25519.Ed25519PrivateKey.generate()
        elif algo == "hmac":
            return os.urandom(32)  # 256-bit key for HMAC-SHA256
        raise UnsupportedAlgorithmError(f"Unsupported algorithm: {algo}")

    async def rotate_key(self, old_key_id: Optional[str], algo: str) -> str:
        """
        Rotates a software key: generates a new key and moves the old key to 'retired' status.
        """
        # --- Delayed Import for rotate_key ---
        try:
            from .audit_crypto_factory import CRYPTO_ERRORS, KEY_ROTATIONS, log_action
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )
            KEY_ROTATIONS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )

            async def log_action(*args, **kwargs):
                return None

        # --- End Delayed Import ---

        if old_key_id is not None and not isinstance(old_key_id, str):
            raise TypeError("Old key ID must be a string or None.")
        if not isinstance(algo, str):
            raise TypeError("Algorithm must be a string.")
        if algo not in self.settings.SUPPORTED_ALGOS:  # Enforce policy
            raise UnsupportedAlgorithmError(f"Unsupported algorithm: {algo}")

        new_key_id = await self.generate_key(algo)

        if old_key_id and old_key_id in self.keys:
            old_key_info = self.keys[old_key_id]
            old_key_info["status"] = "retired"
            old_key_info["retired_at"] = time.time()

            try:
                # Update the key status in persistent storage
                await self.key_store.store_key(
                    old_key_id,
                    self._serialize_key_to_bytes(
                        old_key_info["key_obj"], old_key_info["algo"]
                    ),
                    old_key_info["algo"],
                    old_key_info["creation_time"],
                    status="retired",
                    retired_at=old_key_info["retired_at"],
                )
                self.logger.info(
                    f"Software key rotation: Key '{old_key_id}' retired, new key '{new_key_id}' generated for algo '{algo}'. Old key remains for verification.",
                    extra={
                        "operation": "rotate_key_success",
                        "old_key_id": old_key_id,
                        "new_key_id": new_key_id,
                        "algo": algo,
                    },
                )
                await log_action(
                    "rotate_key",
                    old_key_id=old_key_id,
                    new_key_id=new_key_id,
                    algo=algo,
                    provider="software",
                    success=True,
                    action="retire_old_key",
                )
            except Exception as e:
                self.logger.error(
                    f"Failed to update status of old key '{old_key_id}' to retired: {e}",
                    exc_info=True,
                    extra={
                        "operation": "rotate_key_retire_fail",
                        "old_key_id": old_key_id,
                    },
                )
                CRYPTO_ERRORS.labels(
                    type="KeyRetireError",
                    provider_type="software",
                    operation="rotate_key",
                ).inc()
                await log_action(
                    "rotate_key",
                    old_key_id=old_key_id,
                    new_key_id=new_key_id,
                    algo=algo,
                    provider="software",
                    success=False,
                    error=str(e),
                    action="retire_old_key",
                )
                # Do not re-raise, as the new key was successfully generated. Log and continue.
        else:
            self.logger.info(
                f"Software key rotation: New key '{new_key_id}' generated (no old key specified or found to retire).",
                extra={
                    "operation": "rotate_key_success",
                    "new_key_id": new_key_id,
                    "algo": algo,
                },
            )
            await log_action(
                "rotate_key",
                new_key_id=new_key_id,
                algo=algo,
                provider="software",
                success=True,
                message="No old key to retire.",
            )

        KEY_ROTATIONS.labels(algo=algo, provider_type="software").inc()
        return new_key_id

    # --- NOTE: Patch 4 was skipped as the logic is already present in this file ---
    async def _rotate_keys_periodically(self):
        """
        Background task to periodically check for active keys exceeding rotation interval
        and clean up expired retired keys.
        """
        # --- Delayed Import for _rotate_keys_periodically ---
        try:
            from .audit_crypto_factory import (
                KEY_CLEANUP_COUNT,
                CRYPTO_ERRORS,
                send_alert,
                log_action,
            )
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            KEY_CLEANUP_COUNT = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )
            send_alert = lambda *args, **kwargs: None

            async def log_action(*args, **kwargs):
                return None

        # --- End Delayed Import ---

        await self._load_keys_task  # Ensure initial load of keys is complete

        while True:
            try:
                # Use the full interval for sleeping, check happens before sleep
                rotation_interval = self.settings.KEY_ROTATION_INTERVAL_SECONDS

                self.logger.debug(
                    "SoftwareCryptoProvider: Running periodic key rotation and cleanup check.",
                    extra={"operation": "periodic_key_check"},
                )

                current_time = time.time()
                keys_to_rotate = []
                keys_to_delete = []

                for key_id, key_info in list(self.keys.items()):
                    if (
                        key_info["status"] == "active"
                        and (current_time - key_info["creation_time"])
                        > rotation_interval
                    ):
                        keys_to_rotate.append((key_id, key_info["algo"]))

                for key_id, key_info in list(self.keys.items()):
                    # Ensure 'retired_at' exists for retired keys before checking
                    if (
                        key_info["status"] == "retired"
                        and key_info.get("retired_at") is not None
                        and (current_time - key_info["retired_at"])
                        > rotation_interval * 2
                    ):
                        keys_to_delete.append(key_id)

                for old_key_id, algo in keys_to_rotate:
                    try:
                        new_key_id = await self.rotate_key(old_key_id, algo)
                        self.logger.info(
                            f"SoftwareCryptoProvider: Auto-rotated key '{old_key_id}' to '{new_key_id}' for algo '{algo}'.",
                            extra={
                                "operation": "auto_rotate_success",
                                "old_key_id": old_key_id,
                                "new_key_id": new_key_id,
                                "algo": algo,
                            },
                        )
                    except Exception as e:
                        self.logger.error(
                            f"SoftwareCryptoProvider: Auto-rotation failed for key '{old_key_id}': {e}",
                            exc_info=True,
                            extra={
                                "operation": "auto_rotate_fail",
                                "key_id": old_key_id,
                            },
                        )
                        CRYPTO_ERRORS.labels(
                            type="AutoRotateError",
                            provider_type="software",
                            operation="rotate_keys_periodic",
                        ).inc()
                        asyncio.create_task(
                            send_alert(
                                f"SoftwareCryptoProvider: Auto-rotation failed for key '{old_key_id}': {e}",
                                severity="high",
                            )
                        )
                        asyncio.create_task(
                            log_action(
                                "auto_rotate",
                                key_id=old_key_id,
                                success=False,
                                error=str(e),
                            )
                        )

                for key_id in keys_to_delete:
                    try:
                        if key_id in self.keys:
                            # Secure destruction/zeroization of key material in memory
                            key_obj_to_zeroize = self.keys[key_id].get("key_obj")
                            # For raw bytes (like HMAC keys), overwrite with zeros
                            if isinstance(key_obj_to_zeroize, (bytes, bytearray)):
                                # Create a mutable copy if it's immutable bytes
                                if isinstance(key_obj_to_zeroize, bytes):
                                    mutable_key_bytes = bytearray(key_obj_to_zeroize)
                                else:
                                    mutable_key_bytes = key_obj_to_zeroize
                                for i in range(len(mutable_key_bytes)):
                                    mutable_key_bytes[i] = 0  # Simple zeroization
                                self.logger.debug(
                                    f"SoftwareCryptoProvider: Zeroized key material for '{key_id}' in memory.",
                                    extra={
                                        "operation": "key_zeroized_memory",
                                        "key_id": key_id,
                                    },
                                )
                                # If the original was bytes, re-assigning the mutable_key_bytes might not affect the original object
                                # For true zeroization, one would need to control memory allocation or use a C extension.

                            del self.keys[key_id]  # Remove from in-memory cache
                            await self.key_store.delete_key_file(
                                key_id
                            )  # Delete from disk
                            self.logger.info(
                                f"SoftwareCryptoProvider: Permanently deleted retired key '{key_id}'.",
                                extra={
                                    "operation": "auto_delete_key_success",
                                    "key_id": key_id,
                                },
                            )
                            KEY_CLEANUP_COUNT.labels(
                                provider_type="software", status="success"
                            ).inc()
                            await log_action(
                                "key_delete",
                                key_id=key_id,
                                provider="software",
                                status="success",
                            )
                    except Exception as e:
                        self.logger.error(
                            f"SoftwareCryptoProvider: Failed to delete retired key '{key_id}': {e}",
                            exc_info=True,
                            extra={
                                "operation": "auto_delete_key_fail",
                                "key_id": key_id,
                            },
                        )
                        CRYPTO_ERRORS.labels(
                            type="AutoDeleteError",
                            provider_type="software",
                            operation="cleanup_keys_periodic",
                        ).inc()
                        asyncio.create_task(
                            send_alert(
                                f"SoftwareCryptoProvider: Failed to delete retired key '{key_id}': {e}",
                                severity="critical",
                            )
                        )
                        KEY_CLEANUP_COUNT.labels(
                            provider_type="software", status="fail"
                        ).inc()
                        await log_action(
                            "key_delete",
                            key_id=key_id,
                            provider="software",
                            status="fail",
                            error=str(e),
                        )

                # Sleep after checking
                await asyncio.sleep(rotation_interval)

            except asyncio.CancelledError:
                self.logger.info(
                    "SoftwareCryptoProvider: Periodic key rotation task cancelled.",
                    extra={"operation": "periodic_rotation_task_cancelled"},
                )
                await log_action("periodic_rotation_task", status="cancelled")
                break  # Re-raise is not needed if we just break the loop
            except Exception as e:
                self.logger.error(
                    f"SoftwareCryptoProvider: Unexpected error in periodic key rotation task: {e}",
                    exc_info=True,
                    extra={"operation": "periodic_rotation_unexpected_error"},
                )
                CRYPTO_ERRORS.labels(
                    type="PeriodicRotationError",
                    provider_type="software",
                    operation="rotate_keys_periodic",
                ).inc()
                asyncio.create_task(
                    send_alert(
                        f"SoftwareCryptoProvider: Unexpected error in key rotation task: {e}",
                        severity="critical",
                    )
                )
                await log_action("periodic_rotation_task", status="fail", error=str(e))
                # Sleep before retrying the loop on unexpected error
                await asyncio.sleep(self.settings.KEY_ROTATION_INTERVAL_SECONDS / 2)

    async def close(self):
        """Closes the SoftwareCryptoProvider."""
        self.logger.info(
            "Closing SoftwareCryptoProvider...", extra={"operation": "close_start"}
        )
        await super().close()
        self.logger.info(
            "SoftwareCryptoProvider closed.", extra={"operation": "close_end"}
        )


# --- HSM Crypto Provider ---
class HSMCryptoProvider(CryptoProvider):
    """
    Hardware-backed cryptographic provider using PKCS#11 compatible HSMs.
    """

    def __init__(
        self,
        software_key_master_accessor: Optional[Callable[[], Awaitable[bytes]]] = None,
        fallback_hmac_secret_accessor: Optional[Callable[[], Awaitable[bytes]]] = None,
        settings: Any = None,
    ):

        # Call super().__init__ which stores the accessors and settings
        super().__init__(
            software_key_master_accessor, fallback_hmac_secret_accessor, settings
        )

        # --- Delayed Import for __init__ (HSM) ---
        try:
            from .audit_crypto_factory import (
                HSM_SESSION_HEALTH,
                CRYPTO_ERRORS,
                log_action,
                CryptoInitializationError,
            )
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            HSM_SESSION_HEALTH = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(set=lambda value: None)
            )
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )

            async def log_action(*args, **kwargs):
                return None

            CryptoInitializationError = Exception
        # --- End Delayed Import ---

        if not HAS_PKCS11:
            self.logger.critical(
                "python-pkcs11 library not found. HSMCryptoProvider cannot be initialized.",
                extra={"operation": "hsm_init_no_pkcs11"},
            )
            # FIX: Use conditional logging
            _conditional_log_action(
                "hsm_provider_init", "fail", reason="pkcs11_not_found"
            )
            raise CryptoInitializationError(
                "PKCS#11 library not found. Cannot use HSMCryptoProvider."
            )

        self.hsm_library_path = self.settings.HSM_LIBRARY_PATH
        self.hsm_slot_id = self.settings.HSM_SLOT_ID

        try:
            self.hsm_user_pin = get_hsm_pin()  # Fetch PIN securely via secrets.py
            self.logger.info(
                "HSM PIN fetched successfully.",
                extra={"operation": "hsm_pin_fetch_success"},
            )
        except Exception as e:
            self.logger.critical(
                f"HSM PIN could not be fetched: {e}. HSMCryptoProvider cannot be initialized.",
                exc_info=True,
                extra={"operation": "hsm_pin_fetch_fail"},
            )
            # FIX: Use conditional logging
            _conditional_log_action(
                "hsm_provider_init", "fail", reason="pin_fetch_fail", error=str(e)
            )
            raise CryptoInitializationError(f"HSM PIN not available: {e}") from e

        if not os.path.exists(self.hsm_library_path):
            self.logger.critical(
                f"HSM library not found at configured path: {self.hsm_library_path}. Cannot initialize HSM.",
                extra={"operation": "hsm_lib_not_found", "path": self.hsm_library_path},
            )
            # FIX: Use conditional logging
            _conditional_log_action(
                "hsm_provider_init",
                "fail",
                reason="hsm_lib_not_found",
                path=self.hsm_library_path,
            )
            raise CryptoInitializationError(
                f"HSM library not found at {self.hsm_library_path}"
            )

        try:
            self.lib = pkcs11.lib(self.hsm_library_path)
            self.session: Optional[pkcs11.Session] = None
            self._init_lock = asyncio.Lock()

            # Background task needs a loop, so we conditionally create it
            try:
                loop = asyncio.get_running_loop()
                self._hsm_init_task = loop.create_task(self._initialize_hsm_session())
                self._background_tasks.add(self._hsm_init_task)
                self._hsm_init_task.add_done_callback(self._background_tasks.discard)
            except RuntimeError:
                # If no loop is running, we cannot start the init task immediately.
                # We defer session initialization until the first crypto operation call.
                self.logger.warning(
                    "HSMCryptoProvider initialized without an active event loop. Session initialization deferred."
                )
                self._hsm_init_task = (
                    asyncio.Future()
                )  # Use a Future placeholder that will be satisfied later if an operation forces init

            HSM_SESSION_HEALTH.labels(provider_type="hsm").set(
                0
            )  # Set to unhealthy until session is confirmed
            # FIX: Use conditional logging
            _conditional_log_action("hsm_provider_init", "success_pending_session")
        except Exception as e:
            self.logger.critical(
                f"Failed to load PKCS#11 library from {self.hsm_library_path}: {e}. HSM features disabled.",
                exc_info=True,
                extra={"operation": "hsm_lib_load_fail"},
            )
            # FIX: Use conditional logging
            _conditional_log_action(
                "hsm_provider_init", "fail", reason="pkcs11_lib_load_fail", error=str(e)
            )
            raise CryptoInitializationError(f"Failed to load PKCS#11 library: {e}")

    async def _initialize_hsm_session(self):
        """Initializes the HSM session, including login, with retry logic for initial connection."""
        # --- Delayed Import for _initialize_hsm_session ---
        try:
            from .audit_crypto_factory import (
                HSM_SESSION_HEALTH,
                CRYPTO_ERRORS,
                log_action,
            )
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            HSM_SESSION_HEALTH = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(set=lambda value: None)
            )
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )

            async def log_action(*args, **kwargs):
                return None

        # --- End Delayed Import ---

        async with self._init_lock:
            if self.session and await asyncio.to_thread(self._is_session_valid):
                self.logger.debug(
                    "HSM session already valid. Skipping re-initialization.",
                    extra={"operation": "hsm_session_already_valid"},
                )
                HSM_SESSION_HEALTH.labels(provider_type="hsm").set(1)
                await log_action("hsm_session_init", status="already_valid")
                return

            self.logger.info(
                "Attempting to initialize HSM session...",
                extra={"operation": "hsm_session_init_attempt"},
            )
            try:
                token = await asyncio.to_thread(
                    self.lib.get_token, slot=self.hsm_slot_id
                )
                self.session = await asyncio.to_thread(
                    token.open, rw=True, user_pin=self.hsm_user_pin
                )
                HSM_SESSION_HEALTH.labels(provider_type="hsm").set(1)
                self.logger.info(
                    f"HSM session initialized successfully on token '{token.label}' in slot {self.hsm_slot_id}.",
                    extra={
                        "operation": "hsm_session_init_success",
                        "token_label": token.label,
                        "slot_id": self.hsm_slot_id,
                    },
                )
                await log_action(
                    "hsm_session_init", status="success", slot_id=self.hsm_slot_id
                )
            except pkcs11.exceptions.PKCS11Error as e:
                self.session = None
                HSM_SESSION_HEALTH.labels(provider_type="hsm").set(0)
                self.logger.critical(
                    f"Failed to initialize HSM session: {e}. Check HSM device, PKCS#11 library, PIN, and slot ID.",
                    exc_info=True,
                    extra={"operation": "hsm_session_init_fail_pkcs11"},
                )
                CRYPTO_ERRORS.labels(
                    type="HSMInitError", provider_type="hsm", operation="init_session"
                ).inc()
                await log_action(
                    "hsm_session_init",
                    status="fail",
                    slot_id=self.hsm_slot_id,
                    error=str(e),
                )
                # For PIN lockout, PKCS#11 might raise specific errors (e.g., CKR_PIN_LOCKED)
                # You might want to parse 'e' to check for specific PKCS#11 error codes here
                raise HSMConnectionError(
                    f"Failed to initialize HSM session: {e}"
                ) from e
            except Exception as e:
                self.session = None
                HSM_SESSION_HEALTH.labels(provider_type="hsm").set(0)
                self.logger.critical(
                    f"Unexpected error initializing HSM session: {e}.",
                    exc_info=True,
                    extra={"operation": "hsm_session_init_unexpected_fail"},
                )
                CRYPTO_ERRORS.labels(
                    type="HSMInitUnexpected",
                    provider_type="hsm",
                    operation="init_session",
                ).inc()
                await log_action(
                    "hsm_session_init",
                    status="fail",
                    slot_id=self.hsm_slot_id,
                    error=str(e),
                )
                raise HSMConnectionError(
                    f"Unexpected error initializing HSM session: {e}"
                ) from e
            # --- START PATCH 5: Fix HSM init set_result misuse ---
            finally:
                # Do not manipulate Task/Future results directly; completion is driven by the coroutine return/exception.
                # If _hsm_init_task was a placeholder future, set its result/exception
                if (
                    isinstance(self._hsm_init_task, asyncio.Future)
                    and not isinstance(self._hsm_init_task, asyncio.Task)
                    and not self._hsm_init_task.done()
                ):
                    if self.session:
                        self._hsm_init_task.set_result(None)
                    else:
                        self._hsm_init_task.set_exception(
                            HSMConnectionError("HSM session initialization failed.")
                        )
            # --- END PATCH 5 ---

    def _is_session_valid(self) -> bool:
        """
        Checks if the current HSM session is active and valid.
        """
        if self.session is None:
            return False
        try:
            session_info: SessionInfo = self.session.get_info()
            # CKS_RW_USER_FUNCTIONS indicates a read-write session logged in as a user
            is_valid = session_info.state == CKS_RW_USER_FUNCTIONS
            if not is_valid:
                self.logger.warning(
                    f"HSM session state is {session_info.state}, expected {CKS_RW_USER_FUNCTIONS}. Session considered invalid.",
                    extra={
                        "operation": "hsm_session_state_mismatch",
                        "current_state": session_info.state,
                    },
                )
            return is_valid
        except pkcs11.exceptions.PKCS11Error as e:
            self.logger.warning(
                f"HSM session validation failed with PKCS#11 error: {e}. Session might be invalid or disconnected.",
                extra={"operation": "hsm_session_validate_fail"},
            )
            return False
        except Exception as e:
            self.logger.error(
                f"Unexpected error during HSM session validation: {e}",
                exc_info=True,
                extra={"operation": "hsm_session_validate_unexpected"},
            )
            return False

    async def _monitor_hsm_health(self):
        """Periodically checks HSM session health and attempts re-initialization."""
        # --- Delayed Import for _monitor_hsm_health ---
        try:
            from .audit_crypto_factory import (
                HSM_SESSION_HEALTH,
                CRYPTO_ERRORS,
                send_alert,
                log_action,
                retry_operation,
            )
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            HSM_SESSION_HEALTH = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(set=lambda value: None)
            )
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )
            send_alert = lambda *args, **kwargs: None

            async def log_action(*args, **kwargs):
                return None

            retry_operation = lambda *args, **kwargs: SimpleNamespace()
        # --- End Delayed Import ---

        # If _hsm_init_task is a Future (placeholder), wait for it to be set (either success or failure)
        if isinstance(self._hsm_init_task, asyncio.Future):
            try:
                await self._hsm_init_task
            except Exception as e:
                self.logger.warning(
                    f"Initial HSM session setup failed before monitor started: {e}"
                )

        while True:
            try:
                await asyncio.sleep(self.settings.HSM_HEALTH_CHECK_INTERVAL_SECONDS)
                is_session_valid = await asyncio.to_thread(self._is_session_valid)
                if not is_session_valid:
                    self.logger.warning(
                        "HSM session is not valid. Attempting to re-initialize.",
                        extra={"operation": "hsm_session_reinit_trigger"},
                    )
                    HSM_SESSION_HEALTH.labels(provider_type="hsm").set(0)
                    try:
                        await retry_operation(
                            self._initialize_hsm_session,
                            max_attempts=self.settings.HSM_RETRY_ATTEMPTS,
                            backoff_factor=self.settings.HSM_BACKOFF_FACTOR,
                            initial_delay=self.settings.HSM_INITIAL_DELAY,
                            backend_name=self.__class__.__name__,
                            op_name="hsm_reinit_session",
                        )
                        await log_action("hsm_health_monitor", status="reinit_success")
                    except Exception as e:
                        self.logger.error(
                            f"Failed to re-initialize HSM session after retries: {e}. HSM features remain unavailable.",
                            exc_info=True,
                            extra={"operation": "hsm_reinit_fail_final"},
                        )
                        asyncio.create_task(
                            send_alert(
                                f"HSM session for {self.__class__.__name__} failed to re-initialize. Crypto operations may be impacted.",
                                severity="critical",
                            )
                        )
                        await log_action(
                            "hsm_health_monitor", status="reinit_fail", error=str(e)
                        )
                else:
                    HSM_SESSION_HEALTH.labels(provider_type="hsm").set(1)
                    self.logger.debug(
                        "HSM session is healthy.",
                        extra={"operation": "hsm_session_healthy"},
                    )
                    await log_action("hsm_health_monitor", status="healthy")
            except asyncio.CancelledError:
                self.logger.info(
                    "HSMCryptoProvider: Health monitor task cancelled.",
                    extra={"operation": "hsm_monitor_cancelled"},
                )
                await log_action("hsm_health_monitor", status="cancelled")
                break
            except Exception as e:
                self.logger.error(
                    f"HSMCryptoProvider: Unexpected error in health monitor task: {e}",
                    exc_info=True,
                    extra={"operation": "hsm_monitor_unexpected_error"},
                )
                CRYPTO_ERRORS.labels(
                    type="HSMMonitorError",
                    provider_type="hsm",
                    operation="health_check",
                ).inc()
                asyncio.create_task(
                    send_alert(
                        f"HSM health monitor encountered an error: {e}", severity="high"
                    )
                )
                await log_action("hsm_health_monitor", status="fail", error=str(e))

    async def generate_key(self, algo: str) -> str:
        """
        Generates a new key directly on the HSM.
        """
        # --- Delayed Import for generate_key (HSM) ---
        try:
            from .audit_crypto_factory import (
                KEY_ROTATIONS,
                CRYPTO_ERRORS,
                log_action,
                HAS_OPENTELEMETRY,
                tracer,
            )
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            KEY_ROTATIONS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )

            async def log_action(*args, **kwargs):
                return None

            HAS_OPENTELEMETRY = False
            tracer = None
        # --- End Delayed Import ---

        if not isinstance(algo, str):
            raise TypeError("Algorithm must be a string.")
        # Enforce policy: Check if algo is supported and not HMAC (HMAC not generated on HSM)
        if algo not in self.settings.SUPPORTED_ALGOS or algo == "hmac":
            raise UnsupportedAlgorithmError(
                f"Unsupported or invalid algorithm for HSM key generation: {algo}"
            )

        await self._initialize_hsm_session()
        if not self.session:
            CRYPTO_ERRORS.labels(
                type="HSMNotAvailable", provider_type="hsm", operation="generate_key"
            ).inc()
            await log_action(
                "generate_key",
                key_id="N/A",
                algo=algo,
                provider="hsm",
                status="no_session",
                success=False,
            )
            raise HSMConnectionError("HSM session not available for key generation.")

        key_id_hex = str(uuid.uuid4())

        if HAS_OPENTELEMETRY and tracer:
            from opentelemetry import trace

            with tracer.start_as_current_span("hsm_generate_key") as span:
                span.set_attribute("algo", algo)
                span.set_attribute("key_id_label", key_id_hex)
                try:
                    await asyncio.to_thread(
                        self._generate_key_internal_hsm, algo, key_id_hex
                    )
                    KEY_ROTATIONS.labels(algo=algo, provider_type="hsm").inc()
                    await log_action(
                        "generate_key",
                        key_id=key_id_hex,
                        algo=algo,
                        provider="hsm",
                        success=True,
                    )
                    span.set_status(trace.StatusCode.OK)
                    return key_id_hex
                except Exception as e:
                    CRYPTO_ERRORS.labels(
                        type=type(e).__name__,
                        provider_type="hsm",
                        operation="generate_key",
                    ).inc()
                    span.set_status(trace.StatusCode.ERROR, description=str(e))
                    await log_action(
                        "generate_key",
                        key_id=key_id_hex,
                        algo=algo,
                        provider="hsm",
                        success=False,
                        error=str(e),
                    )
                    raise HSMKeyError(f"HSM key generation failed: {e}") from e
        else:
            try:
                await asyncio.to_thread(
                    self._generate_key_internal_hsm, algo, key_id_hex
                )
                KEY_ROTATIONS.labels(algo=algo, provider_type="hsm").inc()
                await log_action(
                    "generate_key",
                    key_id=key_id_hex,
                    algo=algo,
                    provider="hsm",
                    success=True,
                )
                return key_id_hex
            except Exception as e:
                CRYPTO_ERRORS.labels(
                    type=type(e).__name__, provider_type="hsm", operation="generate_key"
                ).inc()
                await log_action(
                    "generate_key",
                    key_id=key_id_hex,
                    algo=algo,
                    provider="hsm",
                    success=False,
                    error=str(e),
                )
                raise HSMKeyError(f"HSM key generation failed: {e}") from e

    def _generate_key_internal_hsm(self, algo: str, key_id_label: str):
        """Synchronous internal HSM key generation logic. Runs in a separate thread."""
        key_id_bytes = key_id_label.encode("utf-8")

        common_private_template = {
            pkcs11.Attribute.TOKEN: True,
            pkcs11.Attribute.PRIVATE: True,
            pkcs11.Attribute.SIGN: True,
            pkcs11.Attribute.ID: key_id_bytes,
            pkcs11.Attribute.LABEL: key_id_label,
            pkcs11.Attribute.EXTRACTABLE: False,  # Keys should not be extractable from HSM
            pkcs11.Attribute.SENSITIVE: True,  # Keys are sensitive
        }
        common_public_template = {
            pkcs11.Attribute.TOKEN: True,
            pkcs11.Attribute.VERIFY: True,
            pkcs11.Attribute.ID: key_id_bytes,
            pkcs11.Attribute.LABEL: key_id_label,
        }

        if algo == "ed25519":
            self.session.generate_key_pair(
                pkcs11.constants.CKM_EC_EDWARDS_KEY_PAIR_GEN,
                private_template=common_private_template,
                public_template=common_public_template,
            )
            self.logger.info(
                f"Generated Ed25519 key on HSM with ID/Label: '{key_id_label}'",
                extra={"key_id": key_id_label, "algo": algo},
            )

        elif algo == "rsa":
            self.session.generate_key_pair(
                pkcs11.constants.CKM_RSA_PKCS_KEY_PAIR_GEN,
                private_template={
                    **common_private_template,
                    pkcs11.Attribute.MODULUS_BITS: 2048,
                    pkcs11.Attribute.PUBLIC_EXPONENT: (0x01, 0x00, 0x01),
                },
                public_template={
                    **common_public_template,
                    pkcs11.Attribute.MODULUS_BITS: 2048,
                    pkcs11.Attribute.PUBLIC_EXPONENT: (0x01, 0x00, 0x01),
                },
            )
            self.logger.info(
                f"Generated RSA key on HSM with ID/Label: '{key_id_label}'",
                extra={"key_id": key_id_label, "algo": algo},
            )

        elif algo == "ecdsa":
            P256_OID = (0x06, 0x08, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x03, 0x01, 0x07)
            self.session.generate_key_pair(
                pkcs11.constants.CKM_EC_KEY_PAIR_GEN,
                private_template=common_private_template,
                public_template={
                    **common_public_template,
                    pkcs11.Attribute.EC_PARAMS: P256_OID,
                },
            )
            self.logger.info(
                f"Generated ECDSA key on HSM with ID/Label: '{key_id_label}'",
                extra={"key_id": key_id_label, "algo": algo},
            )

        else:
            raise UnsupportedAlgorithmError(
                f"Unsupported algorithm for HSM key generation: {algo}"
            )

    async def sign(self, data: bytes, key_id: str) -> bytes:
        """
        Signs data using a private key stored on the HSM.
        """
        # --- Delayed Import for sign (HSM) ---
        try:
            from .audit_crypto_factory import (
                SIGN_OPERATIONS,
                SIGN_LATENCY,
                CRYPTO_ERRORS,
                log_action,
                HAS_OPENTELEMETRY,
                tracer,
            )
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            SIGN_OPERATIONS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )
            SIGN_LATENCY = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(observe=lambda: None)
            )
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )

            async def log_action(*args, **kwargs):
                return None

            HAS_OPENTELEMETRY = False
            tracer = None
        # --- End Delayed Import ---

        if not isinstance(data, bytes):
            raise TypeError("Data to sign must be bytes.")
        if not isinstance(key_id, str):
            raise TypeError("Key ID must be a string.")

        start_time = time.perf_counter()
        # FIX 2.2: Initialize algo and status_label for finally block protection
        # HSM operations do not typically have an 'algo' label unless parsed from the key, use 'hsm'
        algo = "hsm"
        status_label = "failed"

        await self._initialize_hsm_session()
        if not self.session:
            CRYPTO_ERRORS.labels(
                type="HSMNotAvailable", provider_type="hsm", operation="sign"
            ).inc()
            await log_action(
                "sign",
                key_id=key_id,
                provider="hsm",
                status="no_session",
                success=False,
            )
            raise HSMConnectionError("HSM session not available for signing.")

        try:
            if HAS_OPENTELEMETRY and tracer:
                from opentelemetry import trace

                with tracer.start_as_current_span("hsm_sign") as span:
                    span.set_attribute("key_id", key_id)
                    signature = await asyncio.to_thread(
                        self._sign_internal_hsm, data, key_id
                    )
                    SIGN_OPERATIONS.labels(algo="hsm", provider_type="hsm").inc()
                    span.set_status(trace.StatusCode.OK)
                    status_label = "success"
                    await log_action(
                        "sign", key_id=key_id, algo="hsm", provider="hsm", success=True
                    )
                    return signature
            else:
                signature = await asyncio.to_thread(
                    self._sign_internal_hsm, data, key_id
                )
                SIGN_OPERATIONS.labels(algo="hsm", provider_type="hsm").inc()
                status_label = "success"
                await log_action(
                    "sign", key_id=key_id, algo="hsm", provider="hsm", success=True
                )
                return signature
        # --- START PATCH 6: Fix HSM error wrapping ---
        except pkcs11.exceptions.PKCS11Error as e:
            self.logger.error(
                f"PKCS#11 error during HSM sign for key '{key_id}': {e}",
                exc_info=True,
                extra={"operation": "hsm_sign_fail_pkcs11", "key_id": key_id},
            )
            CRYPTO_ERRORS.labels(
                type=type(e).__name__, provider_type="hsm", operation="sign"
            ).inc()
            await log_action(
                "sign",
                key_id=key_id,
                algo="hsm",
                provider="hsm",
                success=False,
                error=str(e),
            )
            raise HSMKeyError(f"HSM signing failed: {e}") from e
        except HSMKeyError as e:
            self.logger.error(
                f"Key lookup error during HSM sign for key '{key_id}': {e}",
                exc_info=True,
                extra={"operation": "hsm_sign_fail_key_lookup", "key_id": key_id},
            )
            CRYPTO_ERRORS.labels(
                type=type(e).__name__, provider_type="hsm", operation="sign"
            ).inc()
            await log_action(
                "sign",
                key_id=key_id,
                algo="hsm",
                provider="hsm",
                success=False,
                error=str(e),
            )
            raise
        except Exception as e:
            self.logger.error(
                f"Unexpected error during HSM sign for key '{key_id}': {e}",
                exc_info=True,
                extra={"operation": "hsm_sign_fail_unexpected", "key_id": key_id},
            )
            CRYPTO_ERRORS.labels(
                type=type(e).__name__, provider_type="hsm", operation="sign"
            ).inc()
            await log_action(
                "sign",
                key_id=key_id,
                algo="hsm",
                provider="hsm",
                success=False,
                error=str(e),
            )
            raise CryptoOperationError(f"HSM signing failed: {e}") from e
        # --- END PATCH 6 ---
        finally:
            # Use the algo determined above
            SIGN_LATENCY.labels(algo="hsm", provider_type="hsm").observe(
                time.perf_counter() - start_time
            )

    # --- START PATCH 5: Fix EDDSA mechanism and sign call ---
    def _sign_internal_hsm(self, data: bytes, key_id: str) -> bytes:
        """Synchronous internal HSM signing logic. Runs in a separate thread."""
        private_key_obj = self.session.find_objects(
            {
                pkcs11.Attribute.CLASS: pkcs11.ObjectClass.PRIVATE_KEY,
                pkcs11.Attribute.LABEL: key_id,
            }
        ).single()
        if not private_key_obj:
            raise HSMKeyError(
                f"Private key with label '{key_id}' not found on HSM for signing."
            )

        key_algo_type = private_key_obj.get_attribute(pkcs11.Attribute.KEY_TYPE)
        mechanism = None
        if key_algo_type == pkcs11.KeyType.EC_EDWARDS:
            # Resolve EDDSA mechanism across pkcs11 variants/mocks used in tests
            try:
                from pkcs11.constants import Mechanism as _Mech

                _ckm = getattr(_Mech, "EDDSA", getattr(_Mech, "CKM_EDDSA", None))

                # Fallback check to module-level import if _Mech doesn't have it
                if _ckm is None:
                    try:
                        # CKM_EDDSA was imported at module level
                        _ckm = CKM_EDDSA
                    except NameError:
                        pass

                if _ckm is None:
                    raise AttributeError("EDDSA mechanism missing in constants")
                mechanism = pkcs11.Mechanism(_ckm)
            except Exception:
                # Fallback to string name accepted by many mocks
                mechanism = "EDDSA"
        elif key_algo_type == pkcs11.KeyType.RSA:
            mech_params = pkcs11.ffi.new(
                "CK_RSA_PKCS_PSS_PARAMS *",
                {
                    "hashAlg": CKM_SHA256,
                    "mgf": CKG_MGF1_SHA256,
                    "sLen": pkcs11.constants.CK_RSA_PKCS_PSS_SALTLEN_MAX,
                },
            )
            mechanism = pkcs11.Mechanism(CKM_RSA_PKCS_PSS, mech_params)
        elif key_algo_type == pkcs11.KeyType.EC:
            mechanism = pkcs11.Mechanism(
                CKM_ECDSA
            )  # Assume P-256 is default for EC key
        else:
            raise UnsupportedAlgorithmError(
                f"Unsupported key type {key_algo_type} for HSM signing."
            )

        # Use the context manager style sign() as required by the patch
        with private_key_obj.sign(mechanism) as signer:
            return signer.sign(data)

    # --- END PATCH 5 ---

    async def verify(self, signature: bytes, data: bytes, key_id: str) -> bool:
        """
        Verifies data using a public key stored on the HSM.
        """
        # --- Delayed Import for verify (HSM) ---
        try:
            from .audit_crypto_factory import (
                VERIFY_OPERATIONS,
                VERIFY_LATENCY,
                CRYPTO_ERRORS,
                log_action,
                HAS_OPENTELEMETRY,
                tracer,
            )
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            VERIFY_OPERATIONS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )
            VERIFY_LATENCY = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(observe=lambda: None)
            )
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )

            async def log_action(*args, **kwargs):
                return None

            HAS_OPENTELEMETRY = False
            tracer = None
        # --- End Delayed Import ---

        if not isinstance(signature, bytes):
            raise TypeError("Signature must be bytes.")
        if not isinstance(data, bytes):
            raise TypeError("Data to verify must be bytes.")
        if not isinstance(key_id, str):
            raise TypeError("Key ID must be a string.")

        start_time = time.perf_counter()
        # FIX 2.2: Initialize algo and status_label for finally block protection
        algo = "hsm"
        status_label = "no_session"

        await self._initialize_hsm_session()
        if not self.session:
            self.logger.warning(
                "HSM session not available for verification. Cannot verify with HSM.",
                extra={"operation": "hsm_verify_no_session"},
            )
            CRYPTO_ERRORS.labels(
                type="HSMNotAvailable", provider_type="hsm", operation="verify"
            ).inc()
            VERIFY_OPERATIONS.labels(
                algo="hsm", provider_type="hsm", status="no_session"
            ).inc()
            await log_action(
                "verify",
                key_id=key_id,
                provider="hsm",
                status="no_session",
                success=False,
            )
            return False

        # Reset status label now that key is found
        status_label = "failed"

        try:
            if HAS_OPENTELEMETRY and tracer:
                from opentelemetry import trace

                with tracer.start_as_current_span("hsm_verify") as span:
                    span.set_attribute("key_id", key_id)
                    result = await asyncio.to_thread(
                        self._verify_internal_hsm, signature, data, key_id
                    )
                    if result:
                        status_label = "success"
                        span.set_status(trace.StatusCode.OK)
                    else:
                        status_label = "fail"
                        span.set_status(
                            trace.StatusCode.ERROR, description="Invalid signature"
                        )
                    await log_action(
                        "verify",
                        key_id=key_id,
                        algo="hsm",
                        provider="hsm",
                        success=result,
                    )
                    return result
            else:
                result = await asyncio.to_thread(
                    self._verify_internal_hsm, signature, data, key_id
                )
                if result:
                    status_label = "success"
                await log_action(
                    "verify", key_id=key_id, algo="hsm", provider="hsm", success=result
                )
                return result
        except InvalidSignature:
            status_label = "fail"
            self.logger.info(
                f"Invalid signature detected by HSM for key '{key_id}'.",
                extra={"operation": "hsm_verify_invalid_sig", "key_id": key_id},
            )
            await log_action(
                "verify",
                key_id=key_id,
                algo="hsm",
                provider="hsm",
                success=False,
                error="InvalidSignature",
            )
            return False
        except pkcs11.exceptions.PKCS11Error as e:
            status_label = "error"
            self.logger.error(
                f"PKCS#11 error during HSM verification for key '{key_id}': {e}",
                exc_info=True,
                extra={"operation": "hsm_verify_error_pkcs11", "key_id": key_id},
            )
            CRYPTO_ERRORS.labels(
                type=type(e).__name__, provider_type="hsm", operation="verify"
            ).inc()
            await log_action(
                "verify",
                key_id=key_id,
                algo="hsm",
                provider="hsm",
                success=False,
                error=str(e),
            )
            raise HSMKeyError(f"HSM verification failed: {e}") from e
        except Exception as e:
            status_label = "error"
            self.logger.error(
                f"Unexpected error during HSM verification for key '{key_id}': {e}",
                exc_info=True,
                extra={"operation": "hsm_verify_error_unexpected", "key_id": key_id},
            )
            CRYPTO_ERRORS.labels(
                type=type(e).__name__, provider_type="hsm", operation="verify"
            ).inc()
            await log_action(
                "verify",
                key_id=key_id,
                algo="hsm",
                provider="hsm",
                success=False,
                error=str(e),
            )
            raise CryptoOperationError(f"HSM verification failed: {e}") from e
        finally:
            # Use the algo and status_label determined above
            VERIFY_LATENCY.labels(algo="hsm", provider_type="hsm").observe(
                time.perf_counter() - start_time
            )
            VERIFY_OPERATIONS.labels(
                algo="hsm", provider_type="hsm", status=status_label
            ).inc()

    def _verify_internal_hsm(self, signature: bytes, data: bytes, key_id: str) -> bool:
        """Synchronous internal HSM verification logic. Runs in a separate thread."""
        public_key_obj = self.session.find_objects(
            {
                pkcs11.Attribute.CLASS: pkcs11.ObjectClass.PUBLIC_KEY,
                pkcs11.Attribute.LABEL: key_id,
            }
        ).single()
        if not public_key_obj:
            raise HSMKeyError(
                f"Public key with label '{key_id}' not found on HSM for verification."
            )

        key_algo_type = public_key_obj.get_attribute(pkcs11.Attribute.KEY_TYPE)
        mechanism = None
        if key_algo_type == pkcs11.KeyType.EC_EDWARDS:
            # --- Applying Patch 5 logic to verify as well ---
            try:
                from pkcs11.constants import Mechanism as _Mech

                _ckm = getattr(_Mech, "EDDSA", getattr(_Mech, "CKM_EDDSA", None))
                if _ckm is None and "CKM_EDDSA" in globals():
                    _ckm = CKM_EDDSA
                if _ckm is None:
                    raise AttributeError("EDDSA mechanism missing in constants")
                mechanism = pkcs11.Mechanism(_ckm)
            except Exception:
                mechanism = "EDDSA"
        elif key_algo_type == pkcs11.KeyType.RSA:
            mech_params = pkcs11.ffi.new(
                "CK_RSA_PKCS_PSS_PARAMS *",
                {
                    "hashAlg": CKM_SHA256,
                    "mgf": CKG_MGF1_SHA256,
                    "sLen": pkcs11.constants.CK_RSA_PKCS_PSS_SALTLEN_MAX,
                },
            )
            mechanism = pkcs11.Mechanism(CKM_RSA_PKCS_PSS, mech_params)
        elif key_algo_type == pkcs11.KeyType.EC:
            mechanism = pkcs11.Mechanism(
                CKM_ECDSA
            )  # Assume P-256 is default for EC key
        else:
            raise UnsupportedAlgorithmError(
                f"Unsupported key type {key_algo_type} for HSM verification."
            )

        self.session.verify(public_key_obj, data, signature, mechanism=mechanism)
        return True

    async def rotate_key(self, old_key_id: Optional[str], algo: str) -> str:
        """
        Rotates an HSM key: generates a new key on the HSM and
        destroys the old private and public key objects on the HSM.
        """
        # --- Delayed Import for rotate_key (HSM) ---
        try:
            from .audit_crypto_factory import (
                KEY_ROTATIONS,
                CRYPTO_ERRORS,
                send_alert,
                log_action,
            )
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            KEY_ROTATIONS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )
            send_alert = lambda *args, **kwargs: None

            async def log_action(*args, **kwargs):
                return None

        # --- End Delayed Import ---

        if old_key_id is not None and not isinstance(old_key_id, str):
            raise TypeError("Old key ID must be a string or None.")
        if not isinstance(algo, str):
            raise TypeError("Algorithm must be a string.")
        if (
            algo not in self.settings.SUPPORTED_ALGOS or algo == "hmac"
        ):  # Enforce policy
            raise UnsupportedAlgorithmError(
                f"Unsupported or invalid algorithm for HSM key rotation: {algo}"
            )

        new_key_id = await self.generate_key(algo)

        if old_key_id:
            await self._initialize_hsm_session()
            if self.session:
                try:
                    await asyncio.to_thread(self._destroy_hsm_key_internal, old_key_id)
                    self.logger.info(
                        f"HSM key rotation: Successfully destroyed old key '{old_key_id}'.",
                        extra={
                            "operation": "rotate_key_old_key_destroyed",
                            "old_key_id": old_key_id,
                        },
                    )
                    await log_action(
                        "rotate_key",
                        old_key_id=old_key_id,
                        new_key_id=new_key_id,
                        algo=algo,
                        provider="hsm",
                        success=True,
                        action="destroy_old_key",
                    )
                except Exception as e:
                    self.logger.error(
                        f"HSM key rotation: Failed to destroy old key '{old_key_id}': {e}. Manual cleanup may be required.",
                        exc_info=True,
                        extra={
                            "operation": "rotate_key_destroy_fail",
                            "old_key_id": old_key_id,
                        },
                    )
                    CRYPTO_ERRORS.labels(
                        type="KeyDestroyError",
                        provider_type="hsm",
                        operation="rotate_key",
                    ).inc()
                    asyncio.create_task(
                        send_alert(
                            f"HSM key destruction failed for {old_key_id}. Manual intervention required.",
                            severity="critical",
                        )
                    )
                    await log_action(
                        "rotate_key",
                        old_key_id=old_key_id,
                        new_key_id=new_key_id,
                        algo=algo,
                        provider="hsm",
                        success=False,
                        error=str(e),
                        action="destroy_old_key",
                    )
                    raise CryptoOperationError(
                        f"HSM key rotation failed to destroy old key: {e}"
                    ) from e
            else:
                self.logger.warning(
                    f"HSM session not available, could not destroy old key '{old_key_id}' during rotation. Manual cleanup required.",
                    extra={
                        "operation": "rotate_key_no_session_destroy",
                        "old_key_id": old_key_id,
                    },
                )
                await log_action(
                    "rotate_key",
                    old_key_id=old_key_id,
                    new_key_id=new_key_id,
                    algo=algo,
                    provider="hsm",
                    success=False,
                    error="HSM session not available",
                    action="destroy_old_key",
                )

        KEY_ROTATIONS.labels(algo=algo, provider_type="hsm").inc()
        return new_key_id

    def _destroy_hsm_key_internal(self, key_id: str):
        """Synchronous internal HSM key destruction logic. Runs in a separate thread."""
        # Find and destroy private key object
        private_key_obj = self.session.find_objects(
            {
                pkcs11.Attribute.CLASS: pkcs11.ObjectClass.PRIVATE_KEY,
                pkcs11.Attribute.LABEL: key_id,
            }
        ).single()
        # --- FIX 9: (Original file) Correct destruction logic ---
        # The original file logic for finding the public key was missing, but it is present in this
        # provided file.

        # Find and destroy public key object
        public_key_obj = self.session.find_objects(
            {
                pkcs11.Attribute.CLASS: pkcs11.ObjectClass.PUBLIC_KEY,
                pkcs11.Attribute.LABEL: key_id,
            }
        ).single()

        # Destroy private key
        if private_key_obj:
            try:
                self.session.destroy_object(private_key_obj)
                self.logger.info(
                    f"Destroyed HSM private key with label: '{key_id}'",
                    extra={"operation": "hsm_destroy_priv_key", "key_id": key_id},
                )
            except pkcs11.exceptions.PKCS11Error as e:
                self.logger.error(
                    f"PKCS#11 error destroying HSM private key '{key_id}': {e}",
                    exc_info=True,
                    extra={
                        "operation": "hsm_destroy_priv_key_fail_pkcs11",
                        "key_id": key_id,
                    },
                )
                raise HSMKeyError(f"Failed to destroy HSM private key: {e}") from e
        else:
            self.logger.warning(
                f"HSM private key with label '{key_id}' not found for destruction.",
                extra={"operation": "hsm_destroy_priv_key_not_found", "key_id": key_id},
            )

        # Destroy public key
        if public_key_obj:
            try:
                self.session.destroy_object(public_key_obj)
                self.logger.info(
                    f"Destroyed HSM public key with label: '{key_id}'",
                    extra={"operation": "hsm_destroy_pub_key", "key_id": key_id},
                )
            except pkcs11.exceptions.PKCS11Error as e:
                self.logger.error(
                    f"PKCS#11 error destroying HSM public key '{key_id}': {e}",
                    exc_info=True,
                    extra={
                        "operation": "hsm_destroy_pub_key_fail_pkcs11",
                        "key_id": key_id,
                    },
                )
                raise HSMKeyError(f"Failed to destroy HSM public key: {e}") from e
        else:
            self.logger.warning(
                f"HSM public key with label '{key_id}' not found for destruction.",
                extra={"operation": "hsm_destroy_pub_key_not_found", "key_id": key_id},
            )
        # --- END FIX 9 ---

    async def close(self):
        """Closes HSM session cleanly."""
        # --- Delayed Import for close (HSM) ---
        try:
            from .audit_crypto_factory import (
                HSM_SESSION_HEALTH,
                CRYPTO_ERRORS,
                log_action,
            )
        except ImportError:
            # FIX 2.1: Make log_action a valid async function
            HSM_SESSION_HEALTH = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(set=lambda value: None)
            )
            CRYPTO_ERRORS = SimpleNamespace(
                labels=lambda **kwargs: SimpleNamespace(inc=lambda: None)
            )

            async def log_action(*args, **kwargs):
                return None

        # --- End Delayed Import ---

        self.logger.info(
            "Closing HSMCryptoProvider...", extra={"operation": "close_start"}
        )

        # Cancel the monitor task *before* closing the session
        if (
            hasattr(self, "_hsm_monitor_task")
            and self._hsm_monitor_task
            and not self._hsm_monitor_task.done()
        ):
            self._hsm_monitor_task.cancel()
            try:
                await self._hsm_monitor_task
            except asyncio.CancelledError:
                pass  # Expected
            except Exception as e:
                self.logger.error(
                    f"Error during HSM monitor task cleanup: {e}",
                    exc_info=True,
                    extra={"operation": "hsm_monitor_cleanup_error"},
                )

        if self.session:
            try:
                self.session.logout()
                self.logger.info(
                    "HSM session logged out.", extra={"operation": "hsm_session_logout"}
                )
                await asyncio.to_thread(self.session.close)
                self.logger.info(
                    "HSM session closed successfully.",
                    extra={"operation": "hsm_session_close_success"},
                )
                HSM_SESSION_HEALTH.labels(provider_type="hsm").set(0)
                await log_action("hsm_session_close", status="success")
            except pkcs11.exceptions.PKCS11Error as e:
                self.logger.error(
                    f"Failed to close HSM session due to PKCS11 error: {e}",
                    exc_info=True,
                    extra={"operation": "hsm_session_close_fail_pkcs11"},
                )
                CRYPTO_ERRORS.labels(
                    type=type(e).__name__,
                    provider_type="hsm",
                    operation="close_session",
                ).inc()
                await log_action("hsm_session_close", status="fail", error=str(e))
            except Exception as e:
                self.logger.error(
                    f"Unexpected error closing HSM session: {e}",
                    exc_info=True,
                    extra={"operation": "hsm_session_close_unexpected"},
                )
                CRYPTO_ERRORS.labels(
                    type="SessionCloseErrorOther",
                    provider_type="hsm",
                    operation="close_session",
                ).inc()
                await log_action("hsm_session_close", status="fail", error=str(e))

        await super().close()
        self.logger.info("HSMCryptoProvider closed.", extra={"operation": "close_end"})
