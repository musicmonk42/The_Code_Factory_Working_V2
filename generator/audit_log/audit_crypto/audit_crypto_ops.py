# audit_crypto_ops.py
# Purpose: High-level cryptographic business operations.
# Contains all chain logic and calls into the configured crypto provider.

"""
High-Level Cryptographic Operations for the Audit Log

This module provides the core business logic for signing, verifying, and chaining audit log
entries. It acts as a robust, resilient layer between the high-level application
logic and the underlying cryptographic provider (e.g., software-based or HSM-backed).

Key Features:
- **Cryptographic Chaining**: Ensures the immutability and order of log entries by
  including a hash of the previous entry in the signature.
- **Resilient Fallback**: Implements an HMAC-based fallback mechanism for signing in
  case the primary cryptographic provider (e.g., an HSM) becomes unavailable.
- **Robust Observability**: Emits detailed Prometheus metrics, structured logs, and
  external alerts for all critical operations and failures.
- **Comprehensive Error Handling**: Catches and logs a wide range of cryptographic,
  network, and configuration errors, failing gracefully where possible and raising
  specific exceptions for callers to handle.
- **Sensitive Data Redaction**: Automatically redacts sensitive information from all
  logs to prevent data leaks.
- **Streaming Support**: Allows for signing and verifying of large data blobs that
  cannot be loaded into memory all at once.

Configuration (from `audit_crypto_factory.settings`):
- `AUDIT_CRYPTO_FALLBACK_ALERT_INTERVAL_SECONDS`: Time in seconds between fallback alerts.
- `AUDIT_CRYPTO_MAX_FALLBACK_ATTEMPTS_BEFORE_ALERT`: Number of consecutive fallback attempts
  before an alert is sent.
- `AUDIT_CRYPTO_MAX_FALLBACK_ATTEMPTS_BEFORE_DISABLE`: The threshold at which the
  HMAC fallback is automatically disabled.

Global State:
- `_FALLBACK_ATTEMPT_COUNT`: A dictionary to track consecutive fallback attempts.
- `_LAST_FALLBACK_ALERT_TIME`: The timestamp of the last fallback alert sent.

Security Considerations:
- The HMAC fallback secret is a critical security asset. Its compromise would allow an
  attacker to forge log entries during a primary provider outage. It is essential
  that this secret is managed securely via a dedicated secrets manager.

"""

import asyncio
import base64
import json
import logging
import time
import hashlib
import hmac

from typing import Any, Dict, List, Optional, AsyncIterable

# Import the crypto provider and related utilities from the factory
from .audit_crypto_factory import (
    crypto_provider_factory,
    settings,
    log_action,
    send_alert,
    CRYPTO_ERRORS,
    _FALLBACK_HMAC_SECRET,
    SensitiveDataFilter,
)
from cryptography.exceptions import InvalidSignature
from .audit_crypto_provider import (
    CryptoOperationError,
    KeyNotFoundError,
    InvalidKeyStatusError,
    UnsupportedAlgorithmError,
    HSMError,
)


logger = logging.getLogger(__name__)
logger.addFilter(SensitiveDataFilter())

# --- Production TODOs: ---
# [X] Harden HMAC fallback (rate limit, log, alert, auto-disable).
# [X] Provide strong return type guarantees (avoid returning None unless documented).
# [X] Document all edge cases (missing key, corrupt entry, chain breakage).
# [X] Instrument: every error path must emit Prometheus/log/audit records.

# --- Global Constants (configurable via settings) ---
# --- FIX: Remove global constants that read from settings at import time ---
# FALLBACK_ALERT_INTERVAL_SECONDS = settings.get("FALLBACK_ALERT_INTERVAL_SECONDS", 300)
# MAX_FALLBACK_ATTEMPTS_BEFORE_ALERT = settings.get("MAX_FALLBACK_ATTEMPTS_BEFORE_ALERT", 5)
# MAX_FALLBACK_ATTEMPTS_BEFORE_DISABLE = settings.get("MAX_FALLBACK_ATTEMPTS_BEFORE_DISABLE", 20)

# Simple rate limiting for fallback attempts
_FALLBACK_ATTEMPT_COUNT: Dict[str, int] = {}
_LAST_FALLBACK_ALERT_TIME: float = 0


# --- Utility Functions ---
def compute_hash(data: bytes) -> str:
    """
    Computes the SHA256 hash of the given data.
    Args:
        data (bytes): The input data to hash.
    Returns:
        str: The SHA256 hash as a hexadecimal string.
    Raises:
        TypeError: If data is not bytes.
    """
    if not isinstance(data, bytes):
        logger.error(
            "TypeError: Data for hashing must be bytes.",
            extra={"operation": "compute_hash_fail", "reason": "invalid_input_type"},
        )
        raise TypeError("Data for hashing must be bytes.")
    return hashlib.sha256(data).hexdigest()


async def stream_compute_hash(data_chunks: AsyncIterable[bytes]) -> str:
    """
    Computes the SHA256 hash of a stream of data chunks.

    Args:
        data_chunks (AsyncIterable[bytes]): An async iterable yielding data chunks.

    Returns:
        str: The SHA256 hash as a hexadecimal string.

    Raises:
        TypeError: If data_chunks is not an async iterable.
    """
    if not hasattr(data_chunks, "__aiter__"):
        raise TypeError("data_chunks must be an async iterable.")

    hasher = hashlib.sha256()
    try:
        async for chunk in data_chunks:
            if not isinstance(chunk, bytes):
                raise TypeError("All chunks yielded by data_chunks must be bytes.")
            hasher.update(chunk)
    except Exception as e:
        logger.error(
            f"Error while consuming data stream for hashing: {e}", exc_info=True
        )
        raise

    return hasher.hexdigest()


# --- Core Cryptographic Operations (using the global provider) ---


async def sign_entry(entry: Dict[str, Any], key_id: str, prev_hash: str = "") -> str:
    """
    Signs a log entry with cryptographic chaining.

    The `prev_hash` is included in the data signed to establish a cryptographic chain of custody.

    Args:
        entry (Dict[str, Any]): The log entry data to sign. This dictionary must
                                contain 'action', 'timestamp', and 'entry_id'.
        key_id (str): The ID of the key to use for signing.
        prev_hash (str): The hash of the previous log entry in the chain.
                         Defaults to an empty string for the first entry.

    Returns:
        str: The base64-encoded cryptographic signature.

    Raises:
        TypeError: If inputs are not of the correct type.
        ValueError: If `entry` is missing required fields for signing.
        CryptoOperationError: If the underlying crypto provider fails.

    Side Effects:
        - Emits a structured log message for the operation's success or failure.
        - Increments Prometheus metrics for sign operations and errors.
    """
    if not isinstance(entry, dict):
        logger.error(
            "TypeError: Entry must be a dictionary.",
            extra={"operation": "sign_entry_fail", "reason": "invalid_entry_type"},
        )
        raise TypeError("Entry must be a dictionary.")
    if not isinstance(key_id, str) or not key_id:
        logger.error(
            "TypeError: Key ID must be a non-empty string.",
            extra={"operation": "sign_entry_fail", "reason": "invalid_key_id_type"},
        )
        raise TypeError("Key ID must be a non-empty string.")
    if not isinstance(prev_hash, str):
        logger.error(
            "TypeError: Previous hash must be a string.",
            extra={"operation": "sign_entry_fail", "reason": "invalid_prev_hash_type"},
        )
        raise TypeError("Previous hash must be a string.")

    # Validate essential fields in entry for consistent signing
    required_fields = ["action", "timestamp", "entry_id"]
    if not all(k in entry for k in required_fields):
        missing_fields = [f for f in required_fields if f not in entry]
        error_msg = f"Entry dictionary must contain all required fields: {', '.join(required_fields)}. Missing: {', '.join(missing_fields)}."
        logger.error(
            error_msg,
            extra={
                "operation": "sign_entry_fail",
                "reason": "missing_required_fields",
                "missing_fields": missing_fields,
            },
        )
        raise ValueError(error_msg)

    entry_for_signing = entry.copy()
    entry_for_signing["prev_hash"] = prev_hash

    entry_for_signing.pop("signature", None)
    entry_for_signing.pop("key_id", None)

    # Ensure consistent serialization for hashing and signing.
    # `sort_keys=True` is crucial for reproducible hashing across different runs/systems.
    data_to_sign = json.dumps(entry_for_signing, sort_keys=True).encode("utf-8")

    try:
        current_crypto_provider = crypto_provider_factory.get_provider(
            settings.PROVIDER_TYPE
        )
        sig_bytes = await current_crypto_provider.sign(data_to_sign, key_id)

        await log_action(
            "crypto_key_operation",
            {
                "operation": "sign",
                "key_id": key_id,
                "entry_hash_signed_content": compute_hash(data_to_sign),
                "prev_hash_used": prev_hash,
                "success": True,
            },
        )
        return base64.b64encode(sig_bytes).decode("utf-8")
    except (
        KeyNotFoundError,
        InvalidKeyStatusError,
        UnsupportedAlgorithmError,
        HSMError,
        CryptoOperationError,
    ) as e:
        logger.error(
            f"Signing failed for entry (ID: {entry.get('entry_id', 'N/A')}, Key: {key_id}): {e}",
            exc_info=True,
            extra={
                "operation": "sign_entry_fail",
                "entry_id": entry.get("entry_id"),
                "key_id": key_id,
                "error_type": type(e).__name__,
            },
        )
        CRYPTO_ERRORS.labels(
            type=type(e).__name__,
            provider_type=settings.PROVIDER_TYPE,
            operation="sign_entry",
        ).inc()
        await log_action(
            "crypto_key_operation",
            {
                "operation": "sign",
                "key_id": key_id,
                "entry_hash_signed_content": compute_hash(data_to_sign),
                "prev_hash_used": prev_hash,
                "success": False,
                "error": str(e),
            },
        )
        raise
    except Exception as e:
        logger.critical(
            f"Unexpected error during signing for entry (ID: {entry.get('entry_id', 'N/A')}, Key: {key_id}): {e}",
            exc_info=True,
            extra={
                "operation": "sign_entry_fail_unexpected",
                "entry_id": entry.get("entry_id"),
                "key_id": key_id,
            },
        )
        CRYPTO_ERRORS.labels(
            type="UnexpectedError",
            provider_type=settings.PROVIDER_TYPE,
            operation="sign_entry",
        ).inc()
        await log_action(
            "crypto_key_operation",
            {
                "operation": "sign",
                "key_id": key_id,
                "entry_hash_signed_content": compute_hash(data_to_sign),
                "prev_hash_used": prev_hash,
                "success": False,
                "error": str(e),
            },
        )
        raise CryptoOperationError(f"Unexpected error during signing: {e}") from e


async def stream_sign_entry(
    data_chunks: AsyncIterable[bytes],
    metadata: Dict[str, Any],
    key_id: str,
    prev_hash: str = "",
) -> str:
    """
    Signs a log entry from a stream of data chunks with cryptographic chaining.

    This function is suitable for large data blobs (e.g., files) that should not be
    loaded into memory. It signs a canonical representation of the metadata and the
    SHA-256 hash of the streamed data.

    Args:
        data_chunks (AsyncIterable[bytes]): An async iterable yielding chunks of the data blob.
        metadata (Dict[str, Any]): The log entry metadata to sign. This dictionary must
                                   contain 'action', 'timestamp', and 'entry_id'.
        key_id (str): The ID of the key to use for signing.
        prev_hash (str): The hash of the previous log entry in the chain.

    Returns:
        str: The base64-encoded cryptographic signature.

    Raises:
        TypeError: If inputs are not of the correct type.
        ValueError: If `metadata` is missing required fields.
        CryptoOperationError: If the underlying crypto provider fails.

    Side Effects:
        - Emits structured log messages and increments Prometheus metrics.
    """
    if not hasattr(data_chunks, "__aiter__"):
        raise TypeError("data_chunks must be an async iterable.")
    if not isinstance(metadata, dict):
        raise TypeError("Metadata must be a dictionary.")
    if not isinstance(key_id, str) or not key_id:
        raise TypeError("Key ID must be a non-empty string.")
    if not isinstance(prev_hash, str):
        raise TypeError("Previous hash must be a string.")

    # Validate essential fields in metadata
    required_fields = ["action", "timestamp", "entry_id"]
    if not all(k in metadata for k in required_fields):
        missing_fields = [f for f in required_fields if f not in metadata]
        raise ValueError(
            f"Metadata must contain all required fields: {', '.join(required_fields)}. Missing: {', '.join(missing_fields)}."
        )

    # 1. Stream the data and compute its hash
    try:
        data_hash = await stream_compute_hash(data_chunks)
    except Exception as e:
        logger.error(f"Failed to compute hash from data stream: {e}", exc_info=True)
        CRYPTO_ERRORS.labels(
            type="StreamingHashFail", provider_type="utility", operation="stream_sign"
        ).inc()
        raise CryptoOperationError("Failed to hash data stream.") from e

    # 2. Create a canonical representation of metadata and data hash to sign
    data_to_sign_entry = metadata.copy()
    data_to_sign_entry["data_hash"] = data_hash
    data_to_sign_entry["prev_hash"] = prev_hash

    # Ensure consistent serialization for reproducible signatures
    data_to_sign = json.dumps(data_to_sign_entry, sort_keys=True).encode("utf-8")

    # 3. Sign the canonical representation
    try:
        current_crypto_provider = crypto_provider_factory.get_provider(
            settings.PROVIDER_TYPE
        )
        sig_bytes = await current_crypto_provider.sign(data_to_sign, key_id)

        await log_action(
            "crypto_key_operation",
            {
                "operation": "stream_sign",
                "key_id": key_id,
                "data_hash": data_hash,
                "prev_hash_used": prev_hash,
                "success": True,
            },
        )
        return base64.b64encode(sig_bytes).decode("utf-8")
    except (
        KeyNotFoundError,
        InvalidKeyStatusError,
        UnsupportedAlgorithmError,
        HSMError,
        CryptoOperationError,
    ) as e:
        logger.error(
            f"Streaming signing failed: {e}",
            exc_info=True,
            extra={
                "operation": "stream_sign_fail",
                "key_id": key_id,
                "error_type": type(e).__name__,
            },
        )
        CRYPTO_ERRORS.labels(
            type=type(e).__name__,
            provider_type=settings.PROVIDER_TYPE,
            operation="stream_sign",
        ).inc()
        await log_action(
            "crypto_key_operation",
            {
                "operation": "stream_sign",
                "key_id": key_id,
                "data_hash": data_hash,
                "prev_hash_used": prev_hash,
                "success": False,
                "error": str(e),
            },
        )
        raise
    except Exception as e:
        logger.critical(
            f"Unexpected error during streaming signing: {e}", exc_info=True
        )
        CRYPTO_ERRORS.labels(
            type="UnexpectedError",
            provider_type=settings.PROVIDER_TYPE,
            operation="stream_sign",
        ).inc()
        await log_action(
            "crypto_key_operation",
            {
                "operation": "stream_sign",
                "key_id": key_id,
                "data_hash": data_hash,
                "prev_hash_used": prev_hash,
                "success": False,
                "error": str(e),
            },
        )
        raise CryptoOperationError(
            f"Unexpected error during streaming signing: {e}"
        ) from e


async def verify_entry(entry: Dict[str, Any], signature_b64: str, key_id: str) -> bool:
    """
    Verifies a single entry's signature.

    Args:
        entry (Dict[str, Any]): The log entry data to verify. This should be the
                                full entry as retrieved, including 'prev_hash' if present.
        signature_b64 (str): The base64-encoded cryptographic signature.
        key_id (str): The ID of the key (public key) used for signing.

    Returns:
        bool: True if the signature is valid, False otherwise.

    Raises:
        TypeError: If inputs are not of the correct type.
        ValueError: If `entry` is missing required fields or signature is invalid base64.
        CryptoOperationError: Propagates errors from the underlying crypto provider (other than InvalidSignature).

    Side Effects:
        - Emits a structured log message for the operation's success or failure.
        - Increments Prometheus metrics for verify operations and errors.
    """
    if not isinstance(entry, dict):
        logger.error(
            "TypeError: Entry must be a dictionary.",
            extra={"operation": "verify_entry_fail", "reason": "invalid_entry_type"},
        )
        raise TypeError("Entry must be a dictionary.")
    if not isinstance(signature_b64, str) or not signature_b64:
        logger.error(
            "TypeError: Signature (base64) must be a non-empty string.",
            extra={
                "operation": "verify_entry_fail",
                "reason": "invalid_signature_type",
            },
        )
        raise TypeError("Signature (base64) must be a non-empty string.")
    if not isinstance(key_id, str) or not key_id:
        logger.error(
            "TypeError: Key ID must be a non-empty string.",
            extra={"operation": "verify_entry_fail", "reason": "invalid_key_id_type"},
        )
        raise TypeError("Key ID must be a non-empty string.")

    entry_for_verification = entry.copy()

    entry_for_verification.pop("signature", None)
    entry_for_verification.pop("key_id", None)

    data_to_verify = json.dumps(entry_for_verification, sort_keys=True).encode("utf-8")
    try:
        sig_bytes = base64.b64decode(signature_b64)
    except Exception as e:
        logger.error(
            f"Failed to base64 decode signature for key '{key_id}' (Entry ID: {entry.get('entry_id', 'N/A')}): {e}",
            exc_info=True,
            extra={
                "operation": "verify_entry_fail",
                "reason": "base64_decode_fail",
                "key_id": key_id,
                "entry_id": entry.get("entry_id"),
            },
        )
        CRYPTO_ERRORS.labels(
            type="Base64DecodeError", provider_type="unknown", operation="verify_entry"
        ).inc()
        await log_action(
            "crypto_key_operation",
            {
                "operation": "verify",
                "key_id": key_id,
                "success": False,
                "entry_hash_verified_content": compute_hash(data_to_verify),
                "error": str(e),
            },
        )
        return False

    try:
        current_crypto_provider = crypto_provider_factory.get_provider(
            settings.PROVIDER_TYPE
        )
        verified = await current_crypto_provider.verify(
            sig_bytes, data_to_verify, key_id
        )

        await log_action(
            "crypto_key_operation",
            {
                "operation": "verify",
                "key_id": key_id,
                "success": verified,
                "entry_hash_verified_content": compute_hash(data_to_verify),
            },
        )
        return verified
    except InvalidSignature:
        logger.info(
            f"Invalid signature detected for key '{key_id}' (Entry ID: {entry.get('entry_id', 'N/A')}).",
            extra={
                "operation": "verify_entry_invalid_sig",
                "key_id": key_id,
                "entry_id": entry.get("entry_id"),
            },
        )
        CRYPTO_ERRORS.labels(
            type="InvalidSignature",
            provider_type=settings.PROVIDER_TYPE,
            operation="verify_entry",
        ).inc()
        await log_action(
            "crypto_key_operation",
            {
                "operation": "verify",
                "key_id": key_id,
                "success": False,
                "entry_hash_verified_content": compute_hash(data_to_verify),
                "error": "InvalidSignature",
            },
        )
        return False
    except (
        KeyNotFoundError,
        UnsupportedAlgorithmError,
        HSMError,
        CryptoOperationError,
    ) as e:
        logger.error(
            f"Verification failed for entry (ID: {entry.get('entry_id', 'N/A')}, Key: {key_id}): {e}",
            exc_info=True,
            extra={
                "operation": "verify_entry_fail",
                "entry_id": entry.get("entry_id"),
                "key_id": key_id,
                "error_type": type(e).__name__,
            },
        )
        CRYPTO_ERRORS.labels(
            type=type(e).__name__,
            provider_type=settings.PROVIDER_TYPE,
            operation="verify_entry",
        ).inc()
        await log_action(
            "crypto_key_operation",
            {
                "operation": "verify",
                "key_id": key_id,
                "success": False,
                "entry_hash_verified_content": compute_hash(data_to_verify),
                "error": str(e),
            },
        )
        raise
    except Exception as e:
        logger.critical(
            f"Unexpected error during verification for entry (ID: {entry.get('entry_id', 'N/A')}, Key: {key_id}): {e}",
            exc_info=True,
            extra={
                "operation": "verify_entry_fail_unexpected",
                "entry_id": entry.get("entry_id"),
                "key_id": key_id,
            },
        )
        CRYPTO_ERRORS.labels(
            type="UnexpectedError",
            provider_type=settings.PROVIDER_TYPE,
            operation="verify_entry",
        ).inc()
        await log_action(
            "crypto_key_operation",
            {
                "operation": "verify",
                "key_id": key_id,
                "success": False,
                "entry_hash_verified_content": compute_hash(data_to_verify),
                "error": str(e),
            },
        )
        raise CryptoOperationError(f"Unexpected error during verification: {e}") from e


async def stream_verify_entry(
    data_chunks: AsyncIterable[bytes],
    metadata: Dict[str, Any],
    signature_b64: str,
    key_id: str,
) -> bool:
    """
    Verifies a log entry from a stream of data chunks.

    This function verifies the signature against a canonical representation of the
    metadata and the SHA-256 hash of the streamed data.

    Args:
        data_chunks (AsyncIterable[bytes]): An async iterable yielding chunks of the data blob.
        metadata (Dict[str, Any]): The log entry metadata.
        signature_b64 (str): The base64-encoded cryptographic signature.
        key_id (str): The ID of the key used for signing.

    Returns:
        bool: True if the signature is valid, False otherwise.

    Raises:
        TypeError: If inputs are not of the correct type.
        ValueError: If `metadata` is missing required fields or signature is invalid base64.
        CryptoOperationError: Propagates errors from the underlying crypto provider.

    Side Effects:
        - Emits structured log messages and increments Prometheus metrics.
    """
    if not hasattr(data_chunks, "__aiter__"):
        raise TypeError("data_chunks must be an async iterable.")
    if not isinstance(metadata, dict):
        raise TypeError("Metadata must be a dictionary.")
    if not isinstance(signature_b64, str) or not signature_b64:
        raise TypeError("Signature (base64) must be a non-empty string.")
    if not isinstance(key_id, str) or not key_id:
        raise TypeError("Key ID must be a non-empty string.")

    # 1. Stream the data and compute its hash
    try:
        data_hash = await stream_compute_hash(data_chunks)
    except Exception as e:
        logger.error(
            f"Failed to compute hash from data stream for verification: {e}",
            exc_info=True,
        )
        CRYPTO_ERRORS.labels(
            type="StreamingHashFail", provider_type="utility", operation="stream_verify"
        ).inc()
        return False

    # 2. Re-create the canonical representation of metadata and data hash
    data_to_verify_entry = metadata.copy()
    data_to_verify_entry["data_hash"] = data_hash
    data_to_verify = json.dumps(data_to_verify_entry, sort_keys=True).encode("utf-8")

    # 3. Verify the signature against the canonical representation
    try:
        sig_bytes = base64.b64decode(signature_b64)
    except Exception as e:
        logger.error(
            f"Failed to base64 decode signature for streaming verification: {e}",
            exc_info=True,
        )
        CRYPTO_ERRORS.labels(
            type="Base64DecodeError", provider_type="unknown", operation="stream_verify"
        ).inc()
        await log_action(
            "crypto_key_operation",
            {
                "operation": "stream_verify",
                "key_id": key_id,
                "success": False,
                "data_hash_verified_content": data_hash,
                "error": str(e),
            },
        )
        return False

    try:
        current_crypto_provider = crypto_provider_factory.get_provider(
            settings.PROVIDER_TYPE
        )
        verified = await current_crypto_provider.verify(
            sig_bytes, data_to_verify, key_id
        )

        await log_action(
            "crypto_key_operation",
            {
                "operation": "stream_verify",
                "key_id": key_id,
                "success": verified,
                "data_hash_verified_content": data_hash,
            },
        )
        return verified
    except InvalidSignature:
        logger.info(
            f"Invalid signature detected during streaming verification for key '{key_id}'."
        )
        CRYPTO_ERRORS.labels(
            type="InvalidSignature",
            provider_type=settings.PROVIDER_TYPE,
            operation="stream_verify",
        ).inc()
        await log_action(
            "crypto_key_operation",
            {
                "operation": "stream_verify",
                "key_id": key_id,
                "success": False,
                "data_hash_verified_content": data_hash,
                "error": "InvalidSignature",
            },
        )
        return False
    except (
        KeyNotFoundError,
        UnsupportedAlgorithmError,
        HSMError,
        CryptoOperationError,
    ) as e:
        logger.error(
            f"Streaming verification failed for key '{key_id}': {e}",
            exc_info=True,
            extra={
                "operation": "stream_verify_fail",
                "key_id": key_id,
                "error_type": type(e).__name__,
            },
        )
        CRYPTO_ERRORS.labels(
            type=type(e).__name__,
            provider_type=settings.PROVIDER_TYPE,
            operation="stream_verify",
        ).inc()
        await log_action(
            "crypto_key_operation",
            {
                "operation": "stream_verify",
                "key_id": key_id,
                "success": False,
                "data_hash_verified_content": data_hash,
                "error": str(e),
            },
        )
        raise
    except Exception as e:
        logger.critical(
            f"Unexpected error during streaming verification: {e}", exc_info=True
        )
        CRYPTO_ERRORS.labels(
            type="UnexpectedError",
            provider_type=settings.PROVIDER_TYPE,
            operation="stream_verify",
        ).inc()
        await log_action(
            "crypto_key_operation",
            {
                "operation": "stream_verify",
                "key_id": key_id,
                "success": False,
                "data_hash_verified_content": data_hash,
                "error": str(e),
            },
        )
        raise CryptoOperationError(
            f"Unexpected error during streaming verification: {e}"
        ) from e


async def verify_chain(entries: List[Dict[str, Any]]) -> bool:
    """
    Verifies an entire chain of entries.

    This function ensures both cryptographic signature validity and correct hash chaining.
    It prevents tampering with individual entries or their order.

    Args:
        entries (List[Dict[str, Any]]): A list of log entry dictionaries in chronological order.
                                        Each entry is expected to contain 'signature', 'key_id',
                                        and 'prev_hash' (or omit 'prev_hash' for the first entry).

    Returns:
        bool: True if the entire chain is valid, False otherwise.

    Raises:
        TypeError: If `entries` is not a list of dictionaries.
        CryptoOperationError: If a critical error occurs during verification that
                              prevents a complete chain integrity check.

    Side Effects:
        - Emits a structured log messages for success or failure.
        - Increments Prometheus metrics for errors and integrity failures.
    """
    if not isinstance(entries, list):
        logger.error(
            "TypeError: Entries must be a list.",
            extra={"operation": "verify_chain_fail", "reason": "invalid_entries_type"},
        )
        raise TypeError("Entries must be a list.")
    if not all(isinstance(e, dict) for e in entries):
        logger.error(
            "TypeError: All entries in the list must be dictionaries.",
            extra={
                "operation": "verify_chain_fail",
                "reason": "invalid_entry_type_in_list",
            },
        )
        raise TypeError("All entries in the list must be dictionaries.")

    if not entries:
        logger.warning(
            "verify_chain called with empty entries list. Returning True.",
            extra={"operation": "verify_chain_empty"},
        )
        await log_action(
            "verify_chain", status="success", message="Empty chain, considered valid."
        )
        return True

    prev_hash = ""
    verification_tasks = []

    for i, entry_original in enumerate(entries):
        entry_copy = entry_original.copy()

        entry_current_prev_hash = entry_copy.get("prev_hash", "")
        if entry_current_prev_hash != prev_hash:
            logger.error(
                f"Chain integrity check failed at entry {i} (ID: {entry_copy.get('entry_id', 'N/A')}): expected prev_hash '{prev_hash}', got '{entry_current_prev_hash}'.",
                extra={
                    "operation": "verify_chain_fail",
                    "entry_index": i,
                    "expected_hash": prev_hash,
                    "actual_hash": entry_current_prev_hash,
                    "entry_id": entry_copy.get("entry_id"),
                },
            )
            CRYPTO_ERRORS.labels(
                type="ChainIntegrityFail",
                provider_type="unknown",
                operation="verify_chain",
            ).inc()
            await log_action(
                "verify_chain",
                status="fail",
                reason="hash_mismatch",
                entry_index=i,
                entry_id=entry_copy.get("entry_id"),
            )
            return False

        signature_b64 = entry_copy.pop("signature", None)
        key_id = entry_copy.pop("key_id", None)

        if signature_b64 is None or key_id is None:
            logger.error(
                f"Entry {i} (ID: {entry_copy.get('entry_id', 'N/A')}) missing signature or key_id for chain verification. Chain broken.",
                extra={
                    "operation": "verify_chain_missing_sig_key",
                    "entry_index": i,
                    "entry_id": entry_copy.get("entry_id"),
                },
            )
            CRYPTO_ERRORS.labels(
                type="MissingSigOrKey",
                provider_type="unknown",
                operation="verify_chain",
            ).inc()
            await log_action(
                "verify_chain",
                status="fail",
                reason="missing_sig_or_key",
                entry_index=i,
                entry_id=entry_copy.get("entry_id"),
            )
            return False

        # NOTE: Pass entry_copy here, which is missing signature/key_id,
        # as verify_entry expects the signed content (without sig/key_id fields).
        verification_tasks.append(verify_entry(entry_copy, signature_b64, key_id))

        # Calculate hash for the *next* iteration's prev_hash
        data_for_current_hash_calc = json.dumps(entry_copy, sort_keys=True).encode(
            "utf-8"
        )
        prev_hash = compute_hash(data_for_current_hash_calc)

    try:
        results = await asyncio.gather(*verification_tasks)

        if not all(results):
            logger.error(
                "One or more entries failed cryptographic signature verification within the chain.",
                extra={"operation": "verify_chain_cryptographic_fail"},
            )
            await log_action(
                "verify_chain", status="fail", reason="cryptographic_mismatch"
            )
            return False

        logger.info(
            "Audit log chain verification successful.",
            extra={"operation": "verify_chain_success"},
        )
        await log_action("verify_chain", status="success")
        return True
    except CryptoOperationError as e:
        logger.error(
            f"Critical crypto error during chain verification: {e}",
            exc_info=True,
            extra={"operation": "verify_chain_critical_error", "error": str(e)},
        )
        CRYPTO_ERRORS.labels(
            type="CriticalCryptoError",
            provider_type="unknown",
            operation="verify_chain",
        ).inc()
        await log_action(
            "verify_chain", status="fail", reason="critical_crypto_error", error=str(e)
        )
        raise


async def rotate_key(algo: str, old_key_id: Optional[str] = None) -> str:
    """
    Initiates key rotation, generating a new key and (if HSM) destroying the old one,
    or (if software) marking it retired.

    This operation ensures forward secrecy by replacing old keys while maintaining
    backward compatibility for verification of past entries.

    Args:
        algo (str): The cryptographic algorithm for the new key.
        old_key_id (Optional[str]): The ID of the key to be rotated.

    Returns:
        str: The unique identifier of the newly generated key.

    Raises:
        TypeError: If inputs are not of the correct type.
        UnsupportedAlgorithmError: If the algorithm is unsupported.
        CryptoOperationError: Propagates errors from the underlying crypto provider.

    Side Effects:
        - Emits a structured log message for the operation's success or failure.
        - Increments Prometheus metrics for key rotations and errors.
    """
    if not isinstance(algo, str) or not algo:
        raise TypeError("Algorithm must be a non-empty string.")
    if old_key_id is not None and not isinstance(old_key_id, str):
        raise TypeError("Old key ID must be a string or None.")
    if algo not in settings.SUPPORTED_ALGOS:
        raise UnsupportedAlgorithmError(
            f"Unsupported algorithm for key rotation: {algo}"
        )

    try:
        current_crypto_provider = crypto_provider_factory.get_provider(
            settings.PROVIDER_TYPE
        )
        new_id = await current_crypto_provider.rotate_key(old_key_id, algo)
        logger.info(
            f"Key rotation initiated: New key '{new_id}' generated for algo '{algo}'.",
            extra={
                "operation": "rotate_key_initiated",
                "old_key_id": old_key_id,
                "new_key_id": new_id,
                "algo": algo,
            },
        )
        await log_action(
            "crypto_key_operation",
            {
                "operation": "rotate_key_overall",
                "old_key_id": old_key_id,
                "new_key_id": new_id,
                "algo": algo,
                "provider": settings.PROVIDER_TYPE,
                "success": True,
            },
        )
        return new_id
    except (
        KeyNotFoundError,
        UnsupportedAlgorithmError,
        HSMError,
        CryptoOperationError,
    ) as e:
        logger.error(
            f"Key rotation failed for algo '{algo}' (old key: {old_key_id}): {e}",
            exc_info=True,
            extra={
                "operation": "rotate_key_fail",
                "old_key_id": old_key_id,
                "algo": algo,
                "error_type": type(e).__name__,
            },
        )
        CRYPTO_ERRORS.labels(
            type=type(e).__name__,
            provider_type=settings.PROVIDER_TYPE,
            operation="rotate_key",
        ).inc()
        await log_action(
            "crypto_key_operation",
            {
                "operation": "rotate_key_overall",
                "old_key_id": old_key_id,
                "new_key_id": "N/A",
                "algo": algo,
                "provider": settings.PROVIDER_TYPE,
                "success": False,
                "error": str(e),
            },
        )
        raise
    except Exception as e:
        logger.critical(
            f"Unexpected error during key rotation for algo '{algo}' (old key: {old_key_id}): {e}",
            exc_info=True,
            extra={
                "operation": "rotate_key_fail_unexpected",
                "old_key_id": old_key_id,
                "algo": algo,
            },
        )
        CRYPTO_ERRORS.labels(
            type="UnexpectedError",
            provider_type=settings.PROVIDER_TYPE,
            operation="rotate_key",
        ).inc()
        await log_action(
            "crypto_key_operation",
            {
                "operation": "rotate_key_overall",
                "old_key_id": old_key_id,
                "new_key_id": "N/A",
                "algo": algo,
                "provider": settings.PROVIDER_TYPE,
                "success": False,
                "error": str(e),
            },
        )
        raise CryptoOperationError(f"Unexpected error during key rotation: {e}") from e


# --- Fallback Crypto Operations ---


async def safe_sign(entry: Dict[str, Any], key_id: str, prev_hash: str = "") -> str:
    """
    Attempts to sign an entry using the primary crypto provider; falls back to HMAC
    if the primary provider fails. This provides resilience against primary key system failures.

    Args:
        entry (Dict[str, Any]): The log entry data to sign.
        key_id (str): The ID of the key to use for primary signing.
        prev_hash (str): The hash of the previous log entry in the chain.

    Returns:
        str: The base64-encoded signature from the primary provider or a hex digest
             from the HMAC fallback.

    Raises:
        TypeError: If inputs are not of the correct type.
        RuntimeError: If HMAC fallback secret is not configured and primary signing fails.
        CryptoOperationError: If both primary and fallback signing fail.

    Side Effects:
        - Logs all attempts and failures, including a distinct log for fallback success.
        - Increments primary and fallback-specific metrics.
        - Sends a critical alert if fallback is used repeatedly or fails entirely.
    """
    if not isinstance(entry, dict):
        raise TypeError("Entry must be a dictionary.")
    if not isinstance(key_id, str) or not key_id:
        raise TypeError("Key ID must be a non-empty string.")
    if not isinstance(prev_hash, str):
        raise TypeError("Previous hash must be a string.")

    # --- FIX: Read settings inside the function with proper None handling ---
    max_fallback_disable = settings.get("MAX_FALLBACK_ATTEMPTS_BEFORE_DISABLE") or 20
    max_fallback_alert = settings.get("MAX_FALLBACK_ATTEMPTS_BEFORE_ALERT") or 5
    fallback_alert_interval = settings.get("FALLBACK_ALERT_INTERVAL_SECONDS") or 300
    # --- END OF FIX ---

    # Get the current primary crypto provider instance from the factory
    primary_crypto_provider = crypto_provider_factory.get_provider(
        settings.PROVIDER_TYPE
    )
    sign_function = primary_crypto_provider.sign

    entry_for_signing = entry.copy()
    entry_for_signing["prev_hash"] = prev_hash
    entry_for_signing.pop("signature", None)
    entry_for_signing.pop("key_id", None)
    data_to_sign_primary = json.dumps(entry_for_signing, sort_keys=True).encode("utf-8")

    # Check if fallback is auto-disabled
    if _FALLBACK_ATTEMPT_COUNT.get("total", 0) >= max_fallback_disable:
        logger.critical(
            "HMAC fallback has been auto-disabled due to excessive failures. Primary crypto system is likely critical.",
            extra={"operation": "fallback_auto_disabled"},
        )

        # We need to run this async call in a safe way.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                send_alert(
                    "CRITICAL: HMAC fallback auto-disabled. Audit log cannot be signed!",
                    severity="emergency",
                )
            )
            loop.create_task(
                log_action(
                    "crypto_fallback_disabled", {"reason": "max_attempts_reached"}
                )
            )
        except RuntimeError:
            # If no event loop is running, we must run the async calls synchronously
            try:
                asyncio.run(
                    send_alert(
                        "CRITICAL: HMAC fallback auto-disabled. Audit log cannot be signed!",
                        severity="emergency",
                    )
                )
                asyncio.run(
                    log_action(
                        "crypto_fallback_disabled", {"reason": "max_attempts_reached"}
                    )
                )
            except Exception:
                logging.critical(
                    "No running event loop to send critical alert or log crypto fallback disable."
                )

        raise CryptoOperationError("HMAC fallback auto-disabled. Cannot sign entry.")

    try:
        signature = await sign_function(data=data_to_sign_primary, key_id=key_id)
        # Reset fallback counters on primary success
        _FALLBACK_ATTEMPT_COUNT["total"] = 0
        _FALLBACK_ATTEMPT_COUNT["since_alert"] = 0
        return signature
    except Exception as e:
        logger.error(
            f"Primary signing failed for entry (ID: {entry.get('entry_id', 'N/A')}, Action: {entry.get('action', 'N/A')}): {e}. Falling back to HMAC.",
            exc_info=True,
            extra={
                "operation": "primary_sign_fail",
                "entry_id": entry.get("entry_id"),
                "action": entry.get("action"),
            },
        )
        CRYPTO_ERRORS.labels(
            type="PrimarySignFail",
            provider_type=settings.PROVIDER_TYPE,
            operation="safe_sign",
        ).inc()
        await log_action(
            "crypto_primary_sign_fail",
            {
                "operation": "sign",
                "key_id": key_id,
                "entry_id": entry.get("entry_id"),
                "reason": str(e),
            },
        )

        # Increment fallback attempt counters
        _FALLBACK_ATTEMPT_COUNT["total"] = _FALLBACK_ATTEMPT_COUNT.get("total", 0) + 1
        _FALLBACK_ATTEMPT_COUNT["since_alert"] = (
            _FALLBACK_ATTEMPT_COUNT.get("since_alert", 0) + 1
        )

        # Rate limit alerts for fallback
        global _LAST_FALLBACK_ALERT_TIME
        current_time = time.time()
        if (
            _FALLBACK_ATTEMPT_COUNT["since_alert"] >= max_fallback_alert
            and (current_time - _LAST_FALLBACK_ALERT_TIME) > fallback_alert_interval
        ):

            # Run async alert and log in a safe manner
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    send_alert(
                        f"WARNING: Primary crypto system is experiencing issues. {_FALLBACK_ATTEMPT_COUNT['since_alert']} fallback attempts in a row. HMAC fallback is active.",
                        severity="high",
                    )
                )
            except RuntimeError:
                # If no event loop is running, we must run the async calls synchronously
                try:
                    asyncio.run(
                        send_alert(
                            f"WARNING: Primary crypto system is experiencing issues. {_FALLBACK_ATTEMPT_COUNT['since_alert']} fallback attempts in a row. HMAC fallback is active.",
                            severity="high",
                        )
                    )
                except Exception:
                    logging.critical("No running event loop to send fallback alert.")

            _LAST_FALLBACK_ALERT_TIME = current_time
            _FALLBACK_ATTEMPT_COUNT["since_alert"] = 0

        try:
            fallback_signature = hmac_sign_fallback(entry, prev_hash)
            logger.warning(
                f"Successfully signed using HMAC fallback for entry (ID: {entry.get('entry_id', 'N/A')}). ALERT: Primary crypto system is down!",
                extra={
                    "operation": "hmac_fallback_success",
                    "entry_id": entry.get("entry_id"),
                },
            )
            await log_action(
                "crypto_fallback_sign",
                {
                    "operation": "sign",
                    "key_id": "HMAC_FALLBACK",
                    "entry_id": entry.get("entry_id"),
                    "reason": str(e),
                    "success": True,
                },
            )
            return fallback_signature
        except Exception as fallback_e:
            logger.critical(
                f"HMAC fallback signing also failed for entry (ID: {entry.get('entry_id', 'N/A')}): {fallback_e}. Data will NOT be signed.",
                exc_info=True,
                extra={
                    "operation": "hmac_fallback_fail",
                    "entry_id": entry.get("entry_id"),
                },
            )
            CRYPTO_ERRORS.labels(
                type="FallbackSignFail", provider_type="fallback", operation="safe_sign"
            ).inc()

            # Explicitly escalate the failure
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    send_alert(
                        f"CRITICAL: HMAC fallback signing failed for {entry.get('entry_id')}. Audit log cannot be signed!",
                        severity="emergency",
                    )
                )
                loop.create_task(
                    log_action(
                        "crypto_fallback_sign",
                        {
                            "operation": "sign",
                            "key_id": "HMAC_FALLBACK",
                            "entry_id": entry.get("entry_id"),
                            "reason": str(fallback_e),
                            "success": False,
                        },
                    )
                )
            except RuntimeError:
                # If no event loop is running, we must run the async calls synchronously
                try:
                    asyncio.run(
                        send_alert(
                            f"CRITICAL: HMAC fallback signing failed for {entry.get('entry_id')}. Audit log cannot be signed!",
                            severity="emergency",
                        )
                    )
                    asyncio.run(
                        log_action(
                            "crypto_fallback_sign",
                            {
                                "operation": "sign",
                                "key_id": "HMAC_FALLBACK",
                                "entry_id": entry.get("entry_id"),
                                "reason": str(fallback_e),
                                "success": False,
                            },
                        )
                    )
                except Exception:
                    logging.critical(
                        "No running event loop to send critical alert or log fallback failure."
                    )

            raise CryptoOperationError(
                f"Both primary and fallback signing failed: {fallback_e}"
            )


def hmac_sign_fallback(entry: Dict[str, Any], prev_hash: str) -> str:
    """
    Synchronous HMAC fallback signing.

    This function uses a pre-configured HMAC secret.
    WARNING: The fallback secret MUST be managed securely (e.g., dedicated secret manager).

    Args:
        entry (Dict[str, Any]): The log entry data to sign.
        prev_hash (str): The hash of the previous log entry in the chain.

    Returns:
        str: The HMAC digest as a hexadecimal string.

    Raises:
        TypeError: If inputs are not of the correct type.
        RuntimeError: If the HMAC fallback secret is not configured.

    Side Effects:
        - Logs the operation's success or failure.
        - Increments Prometheus metrics for errors.
    """
    if not isinstance(entry, dict):
        logger.error(
            "TypeError: Entry must be a dictionary for HMAC fallback.",
            extra={
                "operation": "hmac_sign_fallback_fail",
                "reason": "invalid_entry_type",
            },
        )
        raise TypeError("Entry must be a dictionary.")
    if not isinstance(prev_hash, str):
        logger.error(
            "TypeError: Previous hash must be a string for HMAC fallback.",
            extra={
                "operation": "hmac_sign_fallback_fail",
                "reason": "invalid_prev_hash_type",
            },
        )
        raise TypeError("Previous hash must be a string.")

    if _FALLBACK_HMAC_SECRET is None:
        logger.critical(
            "HMAC fallback secret not securely configured. Cannot perform fallback signing.",
            extra={"operation": "hmac_fallback_secret_missing"},
        )
        CRYPTO_ERRORS.labels(
            type="FallbackSecretMissing",
            provider_type="fallback",
            operation="hmac_sign",
        ).inc()
        raise RuntimeError(
            "HMAC fallback secret not securely configured. Cannot perform fallback signing."
        )

    entry_for_signing = entry.copy()
    entry_for_signing["prev_hash"] = prev_hash

    entry_for_signing.pop("signature", None)
    entry_for_signing.pop("key_id", None)

    data = json.dumps(entry_for_signing, sort_keys=True).encode("utf-8")

    return hmac.new(_FALLBACK_HMAC_SECRET, data, hashlib.sha256).hexdigest()
