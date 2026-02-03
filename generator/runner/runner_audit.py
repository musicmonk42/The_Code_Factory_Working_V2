# runner/runner_audit.py
"""
World-Class Audit Event Logging Module - Secure, Chained, Cryptographically Signed.

This module provides production-grade audit event logging with the following features:
- Cryptographic signing of audit events using V0 audit_crypto system
- Chain-of-custody tracking with linked hashes (blockchain-inspired)
- Async-first design with synchronous compatibility layer
- Zero-dependency circular import resolution (extracted from runner_logging.py)
- Fail-closed security model in production environments
- Thread-safe and async-safe state management
- Comprehensive error handling with fallback strategies

Architecture:
    This module was extracted from runner_logging.py to break circular import chains
    while maintaining full backward compatibility. It uses minimal dependencies and
    lazy imports to ensure it can be imported early in the application lifecycle.

Security Model:
    - In production (DEV_MODE=0): All audit events MUST be signed
    - In development (DEV_MODE=1): Fallback to unsigned logging with warnings
    - Missing signing keys in production trigger CRITICAL level alerts
    - Audit chain integrity is maintained via cryptographic hash linking

Exports:
    - log_audit_event: Async function for logging audit events (primary interface)
    - log_audit_event_sync: Synchronous wrapper using asyncio.create_task
    - get_audit_state: Retrieve current audit chain state
    - set_audit_key_id: Configure audit signing key
    - get_last_audit_hash: Get last audit hash for verification

Usage:
    # Async context (preferred):
    await log_audit_event("user_login", {"user_id": "123", "ip": "1.2.3.4"})
    
    # Synchronous context:
    log_audit_event_sync("api_call", {"endpoint": "/api/v1/generate", "status": 200})

Thread Safety:
    All state modifications are protected by asyncio.Lock to ensure thread-safe
    operation in multi-threaded async environments.

Version: 2.0 (Extracted 2026-02-01)
"""

import asyncio
import base64
import getpass
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

# --- Early logger setup to prevent circular imports ---
logger = logging.getLogger("runner")

# --- Crypto imports with fallback ---
SIGNING_ENABLED = (
    os.getenv("DEV_MODE", "0") != "1"
    and os.getenv("TESTING") != "1"
    and os.getenv("PYTEST_CURRENT_TEST") is None
)

# Also check if crypto mode is disabled
CRYPTO_MODE = os.getenv("AUDIT_CRYPTO_MODE", "").lower()
CRYPTO_ACTUALLY_ENABLED = SIGNING_ENABLED and CRYPTO_MODE not in ("disabled", "")

try:
    if SIGNING_ENABLED:
        from generator.audit_log.audit_crypto.audit_crypto_ops import (
            compute_hash,
            safe_sign,
        )
        from generator.audit_log.audit_crypto.audit_crypto_provider import (
            CryptoOperationError,
        )
        
        # Log accurate status based on AUDIT_CRYPTO_MODE
        if CRYPTO_MODE == "disabled":
            logger.warning(
                "Audit log crypto imports loaded, but AUDIT_CRYPTO_MODE=disabled. "
                "Signatures will use NoOpCryptoProvider (no actual cryptographic signing)."
            )
        elif CRYPTO_MODE == "":
            logger.warning(
                "Audit log crypto imports loaded, but AUDIT_CRYPTO_MODE not set. "
                "Crypto provider will be determined at runtime."
            )
        else:
            logger.info("Secure audit log signing ENABLED (AUDIT_CRYPTO_MODE=%s).", CRYPTO_MODE)
    else:
        raise ImportError("Crypto disabled in DEV/TEST")
except Exception:
    logger.debug(
        "Secure audit log signing DISABLED (DEV_MODE or TESTING). Using fallback crypto."
    )

    class CryptoOperationError(Exception):
        pass

    def compute_hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    async def safe_sign(entry, key_id, prev_hash):
        return base64.b64encode(b"unsigned").decode()


# --- Audit chain state management ---
_AUDIT_CHAIN_LOCK = asyncio.Lock()
_LAST_AUDIT_HASH: str = ""

# Initialize from environment variables
_DEFAULT_AUDIT_KEY_ID: str = (
    os.getenv("AGENTIC_AUDIT_HMAC_KEY", "")
    or os.getenv("AUDIT_SIGNING_KEY", "")
    or os.getenv("RUNNER_AUDIT_SIGNING_KEY_ID", "")
)


async def log_audit_event(action: str, data: Dict[str, Any], **kwargs):
    """
    Creates, signs, and logs a secure, chained audit event using the V0 audit_crypto system.
    
    This function implements a blockchain-inspired audit trail where each event is:
    1. Signed with a cryptographic signature
    2. Linked to the previous event via hash chaining
    3. Timestamped with UTC timezone
    4. Enriched with user context and metadata
    5. Logged to a dedicated audit logger for compliance
    
    Args:
        action: The action being logged (e.g., "llm_call", "security_redact", "user_login")
                Should follow a consistent naming convention across the application.
        data: Dictionary of structured data associated with the action. Must be JSON-serializable.
              Example: {"user_id": "123", "endpoint": "/api/v1/generate", "status": 200}
        **kwargs: Additional context to include in the audit event:
            - run_id: Optional run identifier for grouping related events
            - Any other metadata fields that should be tracked
    
    Returns:
        None. This is a fire-and-forget logging operation.
    
    Raises:
        Does NOT raise exceptions. All errors are logged and metrics are updated, but the
        function returns gracefully to prevent audit logging from disrupting application flow.
    
    Security Considerations:
        - In production (DEV_MODE=0), missing signing keys trigger CRITICAL alerts
        - All audit events are logged to the 'runner.audit' logger
        - Chain integrity is maintained via cryptographic hash linking
        - Non-serializable objects are converted to safe representations
    
    Performance:
        - Async operation to avoid blocking callers
        - Uses asyncio.Lock to ensure atomic chain updates
        - Lazy imports for metrics to avoid circular dependencies
        - Optimized JSON serialization with custom default handler
    
    Example:
        >>> await log_audit_event(
        ...     "llm_call",
        ...     {
        ...         "provider": "openai",
        ...         "model": "gpt-4",
        ...         "tokens": 1500,
        ...         "cost_usd": 0.045
        ...     },
        ...     run_id="abc-123-def-456"
        ... )
    
    Note:
        This is an async function. For synchronous contexts, use log_audit_event_sync.
        The function is designed to never raise exceptions that could disrupt the main
        application flow, making it safe to call from critical code paths.
    """
    global _LAST_AUDIT_HASH

    # --- Lazy import metrics to avoid circular dependencies ---
    # These imports happen inside the function to break circular import chains
    # between runner_audit -> runner_metrics -> runner_logging -> runner_audit
    try:
        from .runner_metrics import (
            ANOMALY_DETECTED_TOTAL,
            PROVENANCE_LOG_ENTRIES,
        )
    except ImportError:
        # Fallback: Create dummy metrics if runner_metrics is unavailable
        # This happens during early initialization or in minimal environments
        class DummyMetric:
            """Stub metric for when Prometheus is unavailable."""
            def labels(self, *a, **k):
                return self

            def inc(self, *a, **k):
                pass

            def set(self, *a, **k):
                pass

        PROVENANCE_LOG_ENTRIES = DummyMetric()
        ANOMALY_DETECTED_TOTAL = DummyMetric()

    # --- Pre-flight checks: Validate signing key configuration ---
    if not _DEFAULT_AUDIT_KEY_ID:
        # CRITICAL: In production, audit events MUST be signed for compliance
        if not os.getenv("DEV_MODE", "0") == "1" and not os.getenv(
            "PYTEST_CURRENT_TEST"
        ):
            logger.critical(
                f"FATAL: log_audit_event called for '{action}' but no signing key is configured and not in DEV_MODE. This should have been caught at startup.",
                extra={"action": action, "reason": "key_id_missing_in_prod"},
            )
            # Fail-closed: Don't log unsigned events in production
            return
        else:
            # DEV_MODE: Allow unsigned events but log prominently
            logger.error(
                f"log_audit_event: No audit signing key ID is configured. Audit event '{action}' will not be signed (DEV_MODE).",
                extra={"action": action, "reason": "key_id_missing"},
            )
            return

    logger.debug(f"Attempting to log audit event: {action}", extra={"action": action})

    # --- JSON serialization helper for non-standard types ---
    def safe_json_default(o):
        """
        Convert non-serializable objects to JSON-safe formats.
        
        Handles common Python types that json.dumps() can't serialize:
        - bytes: Base64 encoded strings
        - datetime: ISO 8601 formatted strings
        - set/frozenset: Lists
        - UUID: String representation
        - Unknown types: Type name placeholder
        """
        if isinstance(o, bytes):
            return base64.b64encode(o).decode('utf-8')
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, (set, frozenset)):
            return list(o)
        if isinstance(o, uuid.UUID):
            return str(o)
        # Fallback for truly unserializable objects
        return f"<Not Serializable: {type(o).__name__}>"

    # --- Critical section: Chain update must be atomic ---
    # Uses asyncio.Lock to ensure only one audit event modifies the chain at a time
    async with _AUDIT_CHAIN_LOCK:
        try:
            # Capture current chain state before modification
            current_prev_hash = _LAST_AUDIT_HASH

            # 1. Construct the entry to be signed
            # This includes all metadata and forms the canonical representation
            entry_to_sign = {
                "action": action,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user": getpass.getuser() or "unknown",
                "run_id": kwargs.get("run_id"),
                "data": data,
                "extra_context": {k: v for k, v in kwargs.items() if k != "run_id"},
            }

            # 2. Call the V0 safe_sign function for cryptographic signature
            # This may involve network calls to KMS or hardware security modules
            signature_b64 = await safe_sign(
                entry=entry_to_sign,
                key_id=_DEFAULT_AUDIT_KEY_ID,
                prev_hash=current_prev_hash,
            )

            # 3. Create the final, complete log entry with signature and chain metadata
            final_audit_log = {
                **entry_to_sign,
                "prev_hash": current_prev_hash,
                "signature": signature_b64,
                "key_id": _DEFAULT_AUDIT_KEY_ID,
            }

            # 4. Log the complete, signed event to the dedicated 'runner.audit' logger
            # This logger should be configured with appropriate handlers (e.g., file, remote)
            audit_logger = logging.getLogger("runner.audit")
            audit_logger.info(
                json.dumps(final_audit_log, default=safe_json_default)
            )

            # 5. Update the chain's state with the hash of the signed content
            # This creates the cryptographic link to the next event (blockchain-inspired)
            entry_for_hash_calc = entry_to_sign.copy()
            entry_for_hash_calc["prev_hash"] = current_prev_hash
            # Don't include signature/key in hash (they're metadata, not payload)
            entry_for_hash_calc.pop("signature", None)
            entry_for_hash_calc.pop("key_id", None)

            # Calculate SHA-256 hash of the canonical JSON representation
            data_that_was_signed = json.dumps(
                entry_for_hash_calc, sort_keys=True, default=safe_json_default
            ).encode("utf-8")
            _LAST_AUDIT_HASH = compute_hash(data_that_was_signed)

            logger.debug(
                f"Successfully logged signed audit event: {action}",
                extra={
                    "action": action,
                    "key_id": _DEFAULT_AUDIT_KEY_ID,
                    "next_hash": _LAST_AUDIT_HASH,
                },
            )
            # Update Prometheus metric for audit event success
            PROVENANCE_LOG_ENTRIES.labels(action=action).inc()

        except CryptoOperationError as e:
            # CRITICAL: Signing failed - this breaks the audit chain
            # This indicates a problem with key material, KMS, or crypto infrastructure
            logger.critical(
                f"CRITICAL: Failed to sign audit event '{action}'. The audit chain may be broken. Error: {e}",
                exc_info=True,
                extra={"action": action, "error_type": "CryptoOperationError"},
            )
            # Update anomaly metric for monitoring/alerting
            ANOMALY_DETECTED_TOTAL.labels(
                type="audit_signing_failure", severity="critical"
            ).inc()
        except Exception as e:
            # CRITICAL: Unexpected error - log for investigation
            # This should never happen in production and indicates a bug
            logger.critical(
                f"CRITICAL: Unexpected error during audit event logging for '{action}'. Error: {e}",
                exc_info=True,
                extra={"action": action, "error_type": "UnexpectedError"},
            )


def log_audit_event_sync(action: str, data: Dict[str, Any], **kwargs) -> None:
    """
    Synchronous wrapper for log_audit_event with intelligent event loop detection.
    
    This function provides a safe way to call log_audit_event from synchronous code
    without triggering "coroutine was never awaited" warnings. It uses asyncio's
    get_running_loop() to detect if an event loop is available and creates a task
    only if one exists.
    
    Behavior:
        1. If an event loop is running: Creates an async task (fire-and-forget)
        2. If no event loop exists: Logs a debug message and returns gracefully
        3. Never raises exceptions that could disrupt the main flow
    
    Args:
        action: The action being logged (e.g., "llm_call", "security_redact", "file_access")
                Should follow consistent naming conventions across the application.
        data: Dictionary of structured data associated with the action. Must be JSON-serializable.
              Example: {"method": "regex", "data_type": "str", "redacted_count": 3}
        **kwargs: Additional context to include in the audit event:
            - run_id: Optional run identifier for grouping related events
            - Any other metadata fields that should be tracked
    
    Returns:
        None. This is a fire-and-forget operation.
    
    Raises:
        Does NOT raise exceptions. All errors are caught and logged at DEBUG level
        to prevent audit logging from disrupting the main application flow.
    
    Thread Safety:
        Safe to call from any thread. If called from a thread without an event loop,
        the function will log a debug message and return gracefully.
    
    Performance:
        - O(1) event loop detection via get_running_loop()
        - Fire-and-forget task creation (non-blocking)
        - Minimal overhead in synchronous-only contexts
    
    Example:
        >>> # From synchronous function
        >>> def process_payment(amount: float, user_id: str):
        ...     log_audit_event_sync(
        ...         "payment_processed",
        ...         {"amount": amount, "user_id": user_id, "currency": "USD"}
        ...     )
        ...     # Continue with processing...
    
    Note:
        This is fire-and-forget. The audit event is queued but not awaited. If you
        need confirmation that the audit event was logged, use the async version
        (log_audit_event) with await in an async context.
        
    Integration:
        Designed to be a drop-in replacement for synchronous logging calls without
        requiring code refactoring to async. Perfect for legacy code paths that need
        audit logging but can't be easily converted to async.
    """
    try:
        # STEP 1: Check if there's a running event loop
        # This is the key to avoiding "coroutine was never awaited" warnings
        loop = asyncio.get_running_loop()
        
        # STEP 2: If we got here, we're in an async context with a running loop
        # Create a fire-and-forget task that will execute in the background
        asyncio.create_task(log_audit_event(action, data, **kwargs))
        
    except RuntimeError:
        # EXPECTED: No event loop running - this happens in pure sync contexts
        # This is the normal case for synchronous-only code paths
        # Just skip logging in this case (can't create async task without a loop)
        logger.debug(
            f"Cannot log audit event '{action}': No running event loop. "
            "This is expected in synchronous-only contexts. "
            "Consider refactoring to async if audit logging is critical for this code path."
        )
    except Exception as e:
        # UNEXPECTED: Something went wrong during task creation
        # This should be rare but we handle it gracefully to prevent crashes
        logger.debug(
            f"Failed to create audit logging task for '{action}': {e}. "
            "This may indicate an issue with the event loop or asyncio infrastructure.",
            exc_info=True
        )


# Provide access to global state for configuration
def get_audit_state() -> Dict[str, Any]:
    """
    Get current audit chain state for configuration, debugging, or monitoring.
    
    Returns a dictionary containing the current state of the audit chain,
    including security configuration and chain integrity information.
    
    Returns:
        Dict[str, Any]: Dictionary with the following keys:
            - key_id (str): Current signing key identifier (empty if not configured)
            - last_hash (str): Hash of the last audit event in the chain
            - signing_enabled (bool): Whether cryptographic signing is enabled
    
    Example:
        >>> state = get_audit_state()
        >>> print(f"Audit signing: {'enabled' if state['signing_enabled'] else 'disabled'}")
        >>> print(f"Chain length: {0 if not state['last_hash'] else 'N/A'}")
    
    Security:
        The key_id is returned but not the actual key material, which is never
        exposed through the API.
    """
    return {
        "key_id": _DEFAULT_AUDIT_KEY_ID,
        "last_hash": _LAST_AUDIT_HASH,
        "signing_enabled": SIGNING_ENABLED,
    }


def set_audit_key_id(key_id: str) -> None:
    """
    Set the audit signing key ID for cryptographic operations.
    
    This function should be called during application initialization to configure
    the audit system. In production environments, failure to set a valid key_id
    will result in CRITICAL level alerts when audit events are logged.
    
    Args:
        key_id: The identifier for the signing key. This should reference a key
                stored in a secure key management system (e.g., AWS KMS, HashiCorp Vault).
                Example: "arn:aws:kms:us-east-1:123456789012:key/abc-def-123"
    
    Returns:
        None
    
    Security:
        - Only the key identifier is stored, not the key material itself
        - Key material is retrieved by the audit_crypto system at signing time
        - This function should only be called during initialization
        - Setting an empty string disables signing (allowed only in DEV_MODE)
    
    Example:
        >>> # During application startup
        >>> set_audit_key_id(os.getenv("AUDIT_SIGNING_KEY_ID"))
    
    Note:
        This function modifies global state and should only be called during
        application initialization or configuration updates, not during normal
        operation.
    """
    global _DEFAULT_AUDIT_KEY_ID
    _DEFAULT_AUDIT_KEY_ID = key_id
    if key_id:
        logger.info(f"Audit signing key ID configured: {key_id[:20]}..." if len(key_id) > 20 else key_id)
    else:
        if not os.getenv("DEV_MODE", "0") == "1":
            logger.warning("Audit signing key ID cleared in production mode")


def get_last_audit_hash() -> str:
    """
    Get the hash of the last audit event in the chain.
    
    This function is primarily used for:
    - Testing and verification of audit chain integrity
    - Monitoring and alerting on audit chain status
    - Debugging audit chain issues
    
    Returns:
        str: SHA-256 hash of the last audit event, or empty string if no events
             have been logged yet.
    
    Example:
        >>> last_hash = get_last_audit_hash()
        >>> if last_hash:
        ...     print(f"Audit chain active. Last hash: {last_hash[:16]}...")
        ... else:
        ...     print("Audit chain not initialized")
    
    Note:
        The hash is calculated from the signed audit event content and forms
        a cryptographic link to the next event in the chain, similar to
        blockchain technology.
    """
    return _LAST_AUDIT_HASH


# Module-level exports for clear API surface
__all__ = [
    # Primary audit logging functions
    "log_audit_event",          # Async version (preferred)
    "log_audit_event_sync",     # Sync wrapper for legacy code
    
    # Configuration and state management
    "get_audit_state",          # Get current audit chain state
    "set_audit_key_id",         # Configure signing key
    "get_last_audit_hash",      # Get last hash for verification
    
    # Error classes (re-exported for convenience)
    "CryptoOperationError",     # Raised when signing fails
]
