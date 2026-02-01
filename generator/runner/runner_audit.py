# runner/runner_audit.py
"""
Audit Event Logging - Extracted from runner_logging.py to break circular dependencies.

This module provides secure, chained audit event logging with cryptographic signatures.
It is separated from runner_logging.py to avoid circular import issues.

Exports:
    - log_audit_event: Async function for logging audit events
    - log_audit_event_sync: Synchronous wrapper using asyncio.create_task
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

try:
    if SIGNING_ENABLED:
        from generator.audit_log.audit_crypto.audit_crypto_ops import (
            compute_hash,
            safe_sign,
        )
        from generator.audit_log.audit_crypto.audit_crypto_provider import (
            CryptoOperationError,
        )
        logger.info("Secure audit log signing ENABLED.")
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
    Creates, signs, and logs a secure, chained audit event using the
    V0 audit_crypto system.
    
    Args:
        action: The action being logged (e.g., "llm_call", "security_redact")
        data: Dictionary of data associated with the action
        **kwargs: Additional context (e.g., run_id)
        
    Note:
        This is an async function. For synchronous contexts, use log_audit_event_sync.
    """
    global _LAST_AUDIT_HASH

    # --- Lazy import metrics to avoid circular dependencies ---
    try:
        from .runner_metrics import (
            ANOMALY_DETECTED_TOTAL,
            PROVENANCE_LOG_ENTRIES,
        )
    except ImportError:

        class DummyMetric:
            def labels(self, *a, **k):
                return self

            def inc(self, *a, **k):
                pass

            def set(self, *a, **k):
                pass

        PROVENANCE_LOG_ENTRIES = DummyMetric()
        ANOMALY_DETECTED_TOTAL = DummyMetric()

    if not _DEFAULT_AUDIT_KEY_ID:
        # This check is now critical and should have been caught at startup,
        # but we double-check to prevent unsigned logs.
        if not os.getenv("DEV_MODE", "0") == "1" and not os.getenv(
            "PYTEST_CURRENT_TEST"
        ):
            logger.critical(
                f"FATAL: log_audit_event called for '{action}' but no signing key is configured and not in DEV_MODE. This should have been caught at startup.",
                extra={"action": action, "reason": "key_id_missing_in_prod"},
            )
            return
        else:
            logger.error(
                f"log_audit_event: No audit signing key ID is configured. Audit event '{action}' will not be signed (DEV_MODE).",
                extra={"action": action, "reason": "key_id_missing"},
            )
            return

    logger.debug(f"Attempting to log audit event: {action}", extra={"action": action})

    # Helper function to handle non-serializable objects (particularly bytes)
    def safe_json_default(o):
        """Convert non-serializable objects to JSON-safe formats."""
        if isinstance(o, bytes):
            return base64.b64encode(o).decode('utf-8')
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, (set, frozenset)):
            return list(o)
        if isinstance(o, uuid.UUID):
            return str(o)
        return f"<Not Serializable: {type(o).__name__}>"

    async with _AUDIT_CHAIN_LOCK:
        try:
            current_prev_hash = _LAST_AUDIT_HASH

            # 1. Construct the entry to be signed
            entry_to_sign = {
                "action": action,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user": getpass.getuser() or "unknown",
                "run_id": kwargs.get("run_id"),
                "data": data,
                "extra_context": {k: v for k, v in kwargs.items() if k != "run_id"},
            }

            # 2. Call the superior V0 safe_sign function
            signature_b64 = await safe_sign(
                entry=entry_to_sign,
                key_id=_DEFAULT_AUDIT_KEY_ID,
                prev_hash=current_prev_hash,
            )

            # 3. Create the final, complete log entry
            final_audit_log = {
                **entry_to_sign,
                "prev_hash": current_prev_hash,
                "signature": signature_b64,
                "key_id": _DEFAULT_AUDIT_KEY_ID,
            }

            # 4. Log the complete, signed event to the 'runner.audit' logger
            audit_logger = logging.getLogger("runner.audit")
            audit_logger.info(
                json.dumps(final_audit_log, default=safe_json_default)
            )

            # 5. Update the chain's state with the hash of the *signed content*
            entry_for_hash_calc = entry_to_sign.copy()
            entry_for_hash_calc["prev_hash"] = current_prev_hash
            entry_for_hash_calc.pop("signature", None)
            entry_for_hash_calc.pop("key_id", None)

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
            PROVENANCE_LOG_ENTRIES.labels(action=action).inc()

        except CryptoOperationError as e:
            logger.critical(
                f"CRITICAL: Failed to sign audit event '{action}'. The audit chain may be broken. Error: {e}",
                exc_info=True,
                extra={"action": action, "error_type": "CryptoOperationError"},
            )
            ANOMALY_DETECTED_TOTAL.labels(
                type="audit_signing_failure", severity="critical"
            ).inc()
        except Exception as e:
            logger.critical(
                f"CRITICAL: Unexpected error during audit event logging for '{action}'. Error: {e}",
                exc_info=True,
                extra={"action": action, "error_type": "UnexpectedError"},
            )


def log_audit_event_sync(action: str, data: Dict[str, Any], **kwargs) -> None:
    """
    Synchronous wrapper for log_audit_event that uses asyncio.create_task.
    
    This function can be called from synchronous contexts. It will:
    - Create an async task if an event loop is running
    - Silently skip logging if no event loop is available
    - Never raise exceptions that could disrupt the main flow
    
    Args:
        action: The action being logged (e.g., "llm_call", "security_redact")
        data: Dictionary of data associated with the action
        **kwargs: Additional context (e.g., run_id)
        
    Note:
        This is fire-and-forget. Errors in audit logging won't crash your app.
    """
    try:
        # Try to create a task in the current event loop
        asyncio.create_task(log_audit_event(action, data, **kwargs))
    except RuntimeError:
        # No event loop running - this happens in pure sync contexts
        logger.debug(
            f"Cannot log audit event '{action}': No running event loop. "
            "This is expected in synchronous-only contexts."
        )
    except Exception as e:
        # Never crash due to logging failures
        logger.debug(f"Failed to create audit logging task for '{action}': {e}")


# Provide access to global state for configuration
def get_audit_state():
    """Get current audit chain state (for configuration/debugging)."""
    return {
        "key_id": _DEFAULT_AUDIT_KEY_ID,
        "last_hash": _LAST_AUDIT_HASH,
        "signing_enabled": SIGNING_ENABLED,
    }


def set_audit_key_id(key_id: str):
    """Set the audit signing key ID (for configuration)."""
    global _DEFAULT_AUDIT_KEY_ID
    _DEFAULT_AUDIT_KEY_ID = key_id


def get_last_audit_hash():
    """Get the last audit hash (for testing/verification)."""
    return _LAST_AUDIT_HASH
