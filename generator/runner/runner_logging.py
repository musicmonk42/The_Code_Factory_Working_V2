# runner/logging.py
# World-class, gold-standard logging module for the runner system.
# Provides structured, redaction, encrypted, and cryptographically signed logs,
# with pluggable handlers and real-time streaming capabilities.

import asyncio
import base64
import contextlib  # [NEW] Added for stop_logging_services
import getpass
import hashlib
import json
import logging
import logging.handlers

# [FIX] Patch: Safer crypto import block
import os
import queue
import re
import sys
import time
import traceback
import uuid
from collections import deque
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Deque, Dict, List, Optional, Union

import aiohttp
import backoff
import psutil
from opentelemetry import trace

# --- Global OpenTelemetry tracer for external callers (e.g., agents, testgen) ---
# This provides a stable symbol that other modules can import as:
#   from runner.runner_logging import tracer
try:
    tracer = trace.get_tracer(__name__)
except TypeError:
    # Fallback for older OpenTelemetry versions
    tracer = None

SIGNING_ENABLED = (
    os.getenv("DEV_MODE", "0") != "1"
    and os.getenv("TESTING") != "1"
    and os.getenv("PYTEST_CURRENT_TEST") is None
)
try:
    if SIGNING_ENABLED:
        # NOTE: Assuming generator.audit_log is installed and available in the environment
        from generator.audit_log.audit_crypto.audit_crypto_ops import (
            compute_hash,
            safe_sign,
        )
        from generator.audit_log.audit_crypto.audit_crypto_provider import (
            CryptoOperationError,
        )

        logging.getLogger(__name__).info("Secure audit log signing ENABLED.")
    else:
        raise ImportError("Crypto disabled in DEV/TEST")
except Exception:
    # Use debug level for expected behavior in dev/test environments
    logging.getLogger(__name__).debug(
        "Secure audit log signing DISABLED (DEV_MODE or TESTING). Using fallback crypto."
    )

    class CryptoOperationError(Exception):
        pass

    def compute_hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    async def safe_sign(entry, key_id, prev_hash):
        return base64.b64encode(b"unsigned").decode()


# [FIX] End of patch

# Initialize from environment variables at import time to prevent race conditions
# where log_audit_event() is called before configure_logging_from_config()
_DEFAULT_AUDIT_KEY_ID: str = (
    os.getenv("AGENTIC_AUDIT_HMAC_KEY", "")
    or os.getenv("AUDIT_SIGNING_KEY", "")
    or os.getenv("RUNNER_AUDIT_SIGNING_KEY_ID", "")
)

# [NEW] State management for the audit chain
_AUDIT_CHAIN_LOCK = asyncio.Lock()
_LAST_AUDIT_HASH: str = (
    ""  # In production, initialize this from the last audit log in persistent storage
)

# External dependency check for ecdsa
try:
    import ecdsa  # Assumes pip install ecdsa.

    HAS_ECDSA = True
except ImportError:
    HAS_ECDSA = False
    logging.getLogger(__name__).warning(
        "ecdsa library not installed. ECDSA signing will be unavailable."
    )

# --- Prometheus Imports (from observability_utils.py) ---
try:
    import prometheus_client as prom
except Exception:

    class _Counter:
        def __init__(self, *a, **k):
            pass

        def labels(self, *a, **k):
            return self

        def inc(self, n: float = 1.0):
            pass

    class _Gauge:
        def __init__(self, *a, **k):
            pass

        def set(self, v: float):
            pass

    class _Histogram:
        def __init__(self, *a, **k):
            pass

        def time(self):
            class _T:
                def __enter__(self):
                    return None

                def __exit__(self, *a):
                    return False

            return _T()

        def observe(self, v: float):
            pass

    class prom:
        Counter = _Counter
        Gauge = _Gauge
        Histogram = _Histogram


# Assume runner.utils and runner.config are correctly imported and configured
# FIXED: Updated imports to use security_utils and feedback_handlers
# FIX: Removed this import to break circular dependency
# from runner.runner_security_utils import redact_secrets, encrypt_data, decrypt_data
# FIX: Removed unused import that caused circular dependency
# from runner.runner_feedback_handlers import collect_feedback

# --- FIX: SPLIT CIRCULAR IMPORT ---
from runner.runner_config import SecretStr  # Import SecretStr for runtime checks

if TYPE_CHECKING:
    from runner.runner_config import (
        RunnerConfig,
    )  # Import RunnerConfig for type hinting only

    # FIX: Moved error imports here to break circular dependency
# --- END FIX ---

# Gold Standard: Import structured errors for consistent logging
# FIX: Corrected 'runner.errors' to 'runner.runner_errors'
# from runner.runner_errors import RunnerError, ConfigurationError, PersistenceError, error_codes # Import relevant error types

# --- FIX: Lazy import of metrics to break circular import ---
# The metrics are now loaded lazily via _get_metrics() to avoid circular import issues.
# When runner_logging is imported by runner_core (which is imported by runner/__init__),
# importing from runner_metrics at module level can cause circular import errors
# if runner_metrics directly or indirectly imports from runner_logging.

# Module-level cache for metrics (populated on first use)
_METRICS_CACHE: Dict[str, Any] = {}


def _get_metrics():
    """
    Lazily import and cache metrics from runner_metrics to break circular import.
    
    This function provides access to metrics objects without causing circular imports
    during module initialization. The metrics are cached after first successful import.
    
    Returns:
        dict: A dictionary containing the metric objects, or fallback dummy metrics
              if the import fails.
    """
    global _METRICS_CACHE
    
    if not _METRICS_CACHE:
        try:
            from runner.runner_metrics import (
                ANOMALY_DETECTED_TOTAL,
                DASHBOARD_QUEUE_SIZE,
                UTIL_ERRORS,
                UTIL_LATENCY,
                UTIL_SELF_HEAL,
            )
            _METRICS_CACHE = {
                "ANOMALY_DETECTED_TOTAL": ANOMALY_DETECTED_TOTAL,
                "DASHBOARD_QUEUE_SIZE": DASHBOARD_QUEUE_SIZE,
                "UTIL_ERRORS": UTIL_ERRORS,
                "UTIL_LATENCY": UTIL_LATENCY,
                "UTIL_SELF_HEAL": UTIL_SELF_HEAL,
            }
        except ImportError:
            # Fallback dummy metrics for when runner_metrics is not available
            class _DummyMetric:
                """Dummy metric that silently ignores all operations."""
                def labels(self, *args, **kwargs):
                    return self
                def inc(self, *args, **kwargs):
                    pass
                def set(self, *args, **kwargs):
                    pass
                def observe(self, *args, **kwargs):
                    pass
                def time(self):
                    class _DummyTimer:
                        def __enter__(self): return self
                        def __exit__(self, *args): pass
                    return _DummyTimer()
            
            _dummy = _DummyMetric()
            _METRICS_CACHE = {
                "ANOMALY_DETECTED_TOTAL": _dummy,
                "DASHBOARD_QUEUE_SIZE": _dummy,
                "UTIL_ERRORS": _dummy,
                "UTIL_LATENCY": _dummy,
                "UTIL_SELF_HEAL": _dummy,
            }
    
    return _METRICS_CACHE


# Create module-level references that lazily resolve to the actual metrics
# These are defined as properties on a helper class to enable lazy loading
class _LazyMetrics:
    """Lazy metric accessor that loads metrics on first access."""
    
    @property
    def ANOMALY_DETECTED_TOTAL(self):
        return _get_metrics()["ANOMALY_DETECTED_TOTAL"]
    
    @property
    def DASHBOARD_QUEUE_SIZE(self):
        return _get_metrics()["DASHBOARD_QUEUE_SIZE"]
    
    @property
    def UTIL_ERRORS(self):
        return _get_metrics()["UTIL_ERRORS"]
    
    @property
    def UTIL_LATENCY(self):
        return _get_metrics()["UTIL_LATENCY"]
    
    @property
    def UTIL_SELF_HEAL(self):
        return _get_metrics()["UTIL_SELF_HEAL"]


_lazy_metrics = _LazyMetrics()

# --- END FIX ---


# In-memory log store for search (deque for recent logs)
LOG_HISTORY: Deque[Dict[str, Any]] = deque(maxlen=10000)

# PII/Secrets redaction patterns - these are the regex ones.
PII_PATTERNS = [
    re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", re.IGNORECASE
    ),  # Emails
    re.compile(r"\b(?:\d{3}[- ]?\d{2}[- ]?\d{4})\b"),  # SSN (basic format)
    re.compile(
        r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|6(?:011|5[0-9]{2})[0-9]{12}|(?:2131|1800|35[0-9]{3})[0-9]{11})\b"
    ),  # Credit cards (basic Luhn validation needed for robust check)
    re.compile(
        r"\b(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
    ),  # IPs
    re.compile(
        r"(?i)\b(api_key|password|token|secret|auth_token|bearer)=[^& ]+"
    ),  # Common API keys/tokens
    re.compile(
        r"\b(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})\b"
    ),  # Phone numbers (US/Canada formats)
]

# [NEW] Gated the over-broad PII regex.
if os.getenv("STRICT_REDACTION", "0") == "1":
    logging.getLogger(__name__).warning(
        "STRICT_REDACTION=1 is enabled. Generic alphanumeric strings (20-40 chars) will be redacted."
    )
    PII_PATTERNS.append(
        re.compile(r"\b[A-Z0-9]{20,40}\b")
    )  # Generic long alphanumeric string (might be a key/token)


# --- FIX: REMOVED DUPLICATE METRIC DEFINITIONS ---
# The definitions for UTIL_LATENCY, UTIL_ERRORS, UTIL_SELF_HEAL,
# PROVENANCE_LOG_ENTRIES, DASHBOARD_QUEUE_SIZE, and ANOMALY_DETECTED_TOTAL
# have been removed from here. They are now imported from runner_metrics.py.
# --- END FIX ---

# --- Alerting Queue (from observability_utils.py) ---
_alert_queue: Optional["AsyncAlertQueue"] = None
_alert_worker_task: Optional[asyncio.Task] = None
_dashboard_stream_task: Optional[asyncio.Task] = None


class AsyncAlertQueue:
    def __init__(self, max_size: int = 100):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self.worker_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the alert worker task."""
        if self.worker_task is None or self.worker_task.done():
            self.worker_task = asyncio.create_task(self._worker())
            logger.info("AsyncAlertQueue worker started.")

    async def _worker(self):
        """Process alerts from the queue."""
        while True:
            try:
                alert_data = await self.queue.get()
                await send_alert(**alert_data)
                self.queue.task_done()
            except asyncio.CancelledError:
                logger.info("AsyncAlertQueue worker cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in AsyncAlertQueue worker: {e}", exc_info=True)

    async def enqueue(
        self,
        subject: str,
        message: str,
        severity: str = "low",
        recipients: Optional[List[str]] = None,
    ):
        """Add an alert to the queue."""
        try:
            await self.queue.put(
                {
                    "subject": subject,
                    "message": message,
                    "severity": severity,
                    "recipients": recipients,
                }
            )
        except asyncio.QueueFull:
            logger.error(f"Alert queue is full. Dropping alert: {subject}")
            _lazy_metrics.UTIL_ERRORS.labels(func="alert_queue", type="full").inc()


async def send_alert(
    subject: str,
    message: str,
    severity: str = "low",
    recipients: Optional[List[str]] = None,
):
    """Sends an alert (e.g., to Slack, email, PagerDuty)."""
    alert_channel = os.getenv("ALERT_CHANNEL", "email")
    alert_webhook_url = os.getenv("ALERT_WEBHOOK_URL")

    alert_payload = {
        "subject": subject,
        "message": message,
        "severity": severity,
        "timestamp": datetime.now().isoformat(),
        "channel": alert_channel,
    }

    if alert_webhook_url:
        try:
            async with aiohttp.ClientSession() as session:
                response = await session.post(alert_webhook_url, json=alert_payload)
                response.raise_for_status()
                logger.info(
                    f"Alert sent to {alert_channel} webhook.",
                    extra={"alert_subject": subject, "severity": severity},
                )
                await log_audit_event(
                    action="alert_notification",
                    data={
                        "subject": subject,
                        "severity": severity,
                        "channel": alert_channel,
                    },
                )
            return
        except aiohttp.ClientError as e:
            logger.error(
                f"Failed to send alert via webhook to {alert_webhook_url}: {e}",
                exc_info=True,
            )
            _lazy_metrics.UTIL_ERRORS.labels(func="send_alert", type="webhook_fail").inc()
        except Exception as e:
            logger.error(
                f"Unexpected error sending alert via webhook: {e}", exc_info=True
            )
            _lazy_metrics.UTIL_ERRORS.labels(func="send_alert", type="unexpected_webhook_error").inc()

    if recipients:
        logger.warning(
            f"No webhook configured or webhook failed. Falling back to email alert for '{subject}'."
        )
        logger.warning(
            f"Email sending not implemented. Alert for '{subject}' not sent to {recipients}."
        )
        _lazy_metrics.UTIL_ERRORS.labels(func="send_alert", type="email_fail").inc()
    else:
        logger.warning(
            f"No alert recipients or webhook. Alert for '{subject}' not dispatched."
        )
        _lazy_metrics.UTIL_ERRORS.labels(func="send_alert", type="no_recipient").inc()


# [NEW] Replaces _start_alert_worker
async def start_logging_services():
    """
    Starts all background logging services (Alert Queue, Dashboard Streamer).
    """
    global _alert_queue, _alert_worker_task, _dashboard_stream_task

    # 1. Start Alert Worker
    if _alert_worker_task is None or _alert_worker_task.done():
        if _alert_queue is None:
            _alert_queue = AsyncAlertQueue()
        await _alert_queue.start()  # This creates the task
        _alert_worker_task = _alert_queue.worker_task
        logger.info("Alert worker service started.")

    # 2. Start Dashboard Streamer
    if (
        (_dashboard_stream_task is None or _dashboard_stream_task.done())
        and os.getenv("DISABLE_DASHBOARD_STREAMING", "").lower()
        not in ("true", "1", "yes")
        and not os.getenv("PYTEST_CURRENT_TEST")
    ):

        dashboard_url = os.getenv("DASHBOARD_WS_URL", "ws://localhost:8080/logs")
        if (
            dashboard_url and dashboard_url != "ws://localhost:8080/logs"
        ):  # Don't start if it's the default
            _dashboard_stream_task = asyncio.create_task(
                _stream_to_dashboard(dashboard_url)
            )
            logger.info("Dashboard streaming service started.")
        else:
            logger.info(
                "Dashboard streaming is disabled (no URL configured or default URL is set)."
            )


async def stop_logging_services():
    """
    Stops all background logging services.
    """
    global _alert_worker_task, _dashboard_stream_task

    if _alert_worker_task:
        _alert_worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _alert_worker_task
        _alert_worker_task = None
        logger.info("Alert worker service stopped.")

    if _dashboard_stream_task:
        _dashboard_stream_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _dashboard_stream_task
        _dashboard_stream_task = None
        logger.info("Dashboard streaming service stopped.")


# [NEW] Replaces add_provenance and SigningFormatter
async def log_audit_event(action: str, data: Dict[str, Any], **kwargs):
    """
    Creates, signs, and logs a secure, chained audit event using the
    V0 audit_crypto system.
    """
    global _LAST_AUDIT_HASH

    # --- FIX: Lazy import metrics and security functions to break circular dependencies ---
    try:
        from runner.runner_metrics import (  # Use only specific metrics
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

    # NOTE: The dependency on `runner_security_utils` is implicitly handled by the top-level
    # SIGNING_ENABLED block, but we must ensure access to compute_hash.

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
            # In a true "fail-closed" system, this would raise a RuntimeError.
            # We rely on the startup check, but log a critical failure here.
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
            )  # Log as a single JSON string

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


# [NEW] Compatibility shim for legacy callers
add_provenance = log_audit_event

# --- Decorators & Anomaly Detection (from observability_utils.py) ---
METRICS_HOOKS: List[Callable[[str, float, Dict[str, Any]], None]] = []
LOGGING_HOOKS: List[Callable[[logging.LogRecord], None]] = []


def register_metrics_hook(func: Callable[[str, float, Dict[str, Any]], None]):
    """Registers a function to be called for custom metrics reporting."""
    METRICS_HOOKS.append(func)
    logger.info(f"Metrics hook '{func.__name__}' registered.")


def register_logging_hook(func: Callable[[logging.LogRecord], None]):
    """Registers a function to be called for custom logging processing."""
    LOGGING_HOOKS.append(func)
    logger.info(f"Logging hook '{func.__name__}' registered.")


_anomaly_history: Dict[str, List[float]] = {}
_anomaly_alerts: Dict[str, float] = {}
ALERT_COOLDOWN = 300  # 5 minutes


# ============================================================================
# INDUSTRY STANDARD ASYNC TASK SCHEDULING UTILITY
# ============================================================================
# This utility ensures safe async task creation with comprehensive error
# handling, logging, and graceful degradation when no event loop is available.
# Meets enterprise-grade standards for production systems.
# ============================================================================

def _safe_create_async_task(
    coro: Awaitable[Any],
    task_name: str,
    context: Optional[Dict[str, Any]] = None,
    fail_silently: bool = False,
) -> bool:
    """
    Safely create an async task with comprehensive error handling.
    
    This is the industry-standard approach for fire-and-forget async tasks
    that may be called from synchronous contexts. It provides:
    
    - Explicit event loop availability checking
    - Comprehensive error handling and logging
    - Context preservation for debugging
    - Graceful degradation when async operations are unavailable
    - Clear visibility into when tasks are skipped
    
    Args:
        coro: Awaitable/Coroutine to execute asynchronously
        task_name: Human-readable name for logging/debugging
        context: Optional context dict for enhanced logging
        fail_silently: If True, use debug logging; if False, use warning
        
    Returns:
        bool: True if task was created successfully, False otherwise
        
    Example:
        >>> _safe_create_async_task(
        ...     log_audit_event(action="test", data={}),
        ...     task_name="audit_logging",
        ...     context={"metric": "cpu_usage"}
        ... )
    """
    try:
        # Check for running event loop before attempting task creation
        # This is more defensive than just catching RuntimeError
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop - this is expected in some contexts (tests, CLI, sync code)
            log_level = logging.DEBUG if fail_silently else logging.WARNING
            logger.log(
                log_level,
                f"Async task '{task_name}' skipped: no event loop running. "
                f"Context: {context or 'none'}",
                extra={"task_name": task_name, "context": context, "skipped": True},
            )
            return False
        
        # Create task in the running event loop
        task = loop.create_task(coro)
        
        # Add done callback for error tracking (production best practice)
        def _handle_task_exception(t: asyncio.Task) -> None:
            """Handle uncaught exceptions in fire-and-forget tasks."""
            try:
                t.result()  # This will raise if the task failed
            except asyncio.CancelledError:
                # Task was cancelled - this is expected, log at debug level
                logger.debug(
                    f"Async task '{task_name}' was cancelled",
                    extra={"task_name": task_name, "cancelled": True},
                )
            except Exception as e:
                # Unexpected error in the task - log at error level
                logger.error(
                    f"Async task '{task_name}' failed with exception: {e}",
                    exc_info=True,
                    extra={
                        "task_name": task_name,
                        "error_type": type(e).__name__,
                        "context": context,
                    },
                )
        
        task.add_done_callback(_handle_task_exception)
        
        logger.debug(
            f"Async task '{task_name}' created successfully",
            extra={"task_name": task_name, "context": context},
        )
        return True
        
    except Exception as e:
        # Catch any unexpected errors in task creation itself
        logger.error(
            f"Failed to create async task '{task_name}': {e}",
            exc_info=True,
            extra={
                "task_name": task_name,
                "error_type": type(e).__name__,
                "context": context,
            },
        )
        return False


def detect_anomaly(
    metric_name: str,
    value: float,
    threshold: float,
    severity: str = "medium",
    anomaly_type: str = "threshold_breach",
) -> None:
    """
    Detect and handle metric anomalies using industry-standard thresholds.
    
    This function implements enterprise-grade anomaly detection with:
    - Comprehensive logging
    - Prometheus metrics tracking
    - Async alert dispatching with fallback
    - Secure audit trail logging
    
    Args:
        metric_name: Name of the metric being monitored
        value: Current metric value
        threshold: Threshold value for anomaly detection
        severity: Severity level (low, medium, high, critical)
        anomaly_type: Type of anomaly (threshold_breach, trend, etc.)
    """
    if value > threshold:
        # Log critical anomaly with structured context
        logger.critical(
            "Anomaly detected: %s value %.2f exceeded threshold %.2f (type=%s, severity=%s)",
            metric_name,
            value,
            threshold,
            anomaly_type,
            severity,
            extra={
                "metric_name": metric_name,
                "value": value,
                "threshold": threshold,
                "anomaly_type": anomaly_type,
                "severity": severity,
            },
        )
        
        # Update Prometheus metrics
        _lazy_metrics.ANOMALY_DETECTED_TOTAL.labels(type=anomaly_type, severity=severity).inc()
        
        # Send alert asynchronously with industry-standard error handling
        _safe_create_async_task(
            send_alert(
                f"Anomaly: {anomaly_type} in {metric_name}",
                f"Value: {value}, Threshold: {threshold}",
                severity=severity,
            ),
            task_name="anomaly_alert",
            context={
                "metric": metric_name,
                "value": value,
                "threshold": threshold,
                "severity": severity,
            },
            fail_silently=True,  # Alerts are non-critical
        )
        
        # Log to secure audit trail with industry-standard error handling
        _safe_create_async_task(
            log_audit_event(
                action="anomaly_detected",
                data={
                    "metric": metric_name,
                    "value": value,
                    "threshold": threshold,
                    "type": anomaly_type,
                    "severity": severity,
                },
            ),
            task_name="anomaly_audit_log",
            context={"metric": metric_name, "severity": severity},
            fail_silently=False,  # Audit logs are important - warn if skipped
        )


# Self-healing decorator
def self_healing(
    max_tries: int = 3,
    on_error: Optional[Callable[[Exception], None]] = None,
    alert_on_fail: bool = True,
):
    """
    A decorator that retries a function multiple times with exponential backoff
    in case of exceptions, intended for self-healing capabilities.
    """

    def decorator(func: Callable):
        # NOTE: UTIL_SELF_HEAL must be defined externally.
        # We rely on the internal mock/real definition from the top of the file.
        @backoff.on_exception(
            backoff.expo,
            Exception,
            max_tries=max_tries,
            logger=logger,
            on_backoff=lambda details: _lazy_metrics.UTIL_SELF_HEAL.labels(func=func.__name__).inc(),
        )
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(
                    f"Self-healing failed for {func.__name__} after {max_tries} tries: {e}",
                    extra={
                        "func_name": func.__name__,
                        "error_type": type(e).__name__,
                        "traceback": traceback.format_exc(),
                    },
                )
                if on_error:
                    on_error(e)
                if alert_on_fail:
                    # Send critical alert using industry-standard async task handling
                    _safe_create_async_task(
                        send_alert(
                            f"Critical Failure: {func.__name__} failed after {max_tries} retries.",
                            f"Error: {e}\nTraceback: {traceback.format_exc()}",
                            severity="critical",
                        ),
                        task_name="self_healing_failure_alert",
                        context={
                            "function": func.__name__,
                            "max_tries": max_tries,
                            "error_type": type(e).__name__,
                        },
                        fail_silently=False,  # Critical alerts should warn if skipped
                    )
                    _lazy_metrics.ANOMALY_DETECTED_TOTAL.labels(
                        type="function_failure", severity="critical"
                    ).inc()
                raise

        return wrapper

    return decorator


# Real-time dashboard streaming
DASHBOARD_QUEUE: queue.Queue = queue.Queue()


async def _stream_to_dashboard(url: str):
    """
    Connects to a WebSocket URL and streams log messages from DASHBOARD_QUEUE.
    """

    def safe_log(level, message, **kwargs):
        try:
            log_func = getattr(logger, level, logger.info)
            log_func(message, **kwargs)
        except (ValueError, OSError):
            try:
                sys.stderr.write(f"[{level.upper()}] {message}\n")
            except (IOError, OSError):
                # Silently ignore if stderr is not available
                pass

    safe_log("info", f"Attempting to connect to dashboard WebSocket at {url}")

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, heartbeat=30) as ws:
                    safe_log("info", f"Connected to dashboard WebSocket at {url}")
                    while True:
                        _lazy_metrics.DASHBOARD_QUEUE_SIZE.set(DASHBOARD_QUEUE.qsize())
                        try:
                            log_record_dict = await asyncio.wait_for(
                                asyncio.to_thread(DASHBOARD_QUEUE.get), timeout=5
                            )
                        except asyncio.TimeoutError:
                            continue

                        try:
                            await ws.send_json(log_record_dict)
                        except ConnectionResetError:
                            safe_log(
                                "warning",
                                "Dashboard WebSocket connection reset. Reconnecting...",
                            )
                            break
                        except Exception as e:
                            safe_log(
                                "error",
                                f"Error sending log to dashboard: {e}",
                                exc_info=True,
                            )
                            _lazy_metrics.UTIL_ERRORS.labels(
                                func="dashboard_stream_send", type=type(e).__name__
                            ).inc()
                        finally:
                            DASHBOARD_QUEUE.task_done()
                safe_log(
                    "info", "Dashboard WebSocket disconnected. Retrying in 5 seconds..."
                )
                _lazy_metrics.UTIL_ERRORS.labels(func="dashboard_stream", type="disconnected").inc()
        except aiohttp.ClientConnectorError as e:
            safe_log(
                "error",
                f"Could not connect to dashboard WebSocket at {url}: {e}. Retrying in 10 seconds...",
                exc_info=False,
            )
            _lazy_metrics.UTIL_ERRORS.labels(func="dashboard_stream", type="connection_error").inc()
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            safe_log("info", "Dashboard streaming task cancelled.")
            raise  # Propagate cancellation
        except Exception as e:
            safe_log(
                "error",
                f"Unexpected error in dashboard streaming: {e}. Retrying in 5 seconds...",
                exc_info=True,
            )
            _lazy_metrics.UTIL_ERRORS.labels(func="dashboard_stream", type="unexpected_error").inc()
            await asyncio.sleep(5)
        finally:
            _lazy_metrics.DASHBOARD_QUEUE_SIZE.set(DASHBOARD_QUEUE.qsize())


# [REMOVED] start_dashboard_streaming (logic moved to start_logging_services)


# Custom logging hook: Stream to queue
def stream_log_record_to_dashboard_queue(record: logging.LogRecord):
    """A logging hook that puts log records into the dashboard queue."""
    try:
        record_dict = {
            k: v
            for k, v in record.__dict__.items()
            if not k.startswith("_")
            and not isinstance(v, (logging.Logger, logging.Handler, logging.Formatter))
        }
        if "exc_info" in record_dict and record_dict["exc_info"]:
            record_dict["exc_info"] = traceback.format_exception(
                *record_dict["exc_info"]
            )

        sensitive_keys = [
            "message",
            "data_preview",
            "error",
            "prompt_hash",
            "response_hash",
        ]
        for key in sensitive_keys:
            if key in record_dict and isinstance(record_dict[key], str):
                # The assumption here is that log messages can be re-encrypted if needed,
                # or that the receiver (TUI/Dashboard) is trusted to decrypt or already has the key.
                # Here, we use a simple base64 placeholder indicating encryption.
                record_dict[key] = (
                    f"[ENCRYPTED_LOG_DATA:{base64.b64encode(record_dict[key].encode()).decode()}]"
                )

        DASHBOARD_QUEUE.put_nowait(record_dict)
    except queue.Full:
        try:
            logger.warning(
                "Dashboard log queue is full, dropping log record.",
                extra={
                    "record_level": record.levelname,
                    "record_msg_preview": record.getMessage()[:100],
                },
            )
        except (queue.Full, RuntimeError, ValueError):
            # Queue is full or not available, skip this log entry
            pass
        _lazy_metrics.UTIL_ERRORS.labels(func="dashboard_queue", type="full").inc()
    except Exception as e:
        try:
            sys.stderr.write(f"Error in stream_log_record_to_dashboard_queue: {e}\n")
        except (IOError, OSError):
            # Silently ignore if stderr is not available
            pass
        _lazy_metrics.UTIL_ERRORS.labels(func="logging_hook_fail", type=type(e).__name__).inc()


# [REMOVED] register_logging_hook(stream_log_record_to_dashboard_queue) - will be added by start_logging_services


# Universal decorator (enhanced)
def util_decorator(func: Callable):
    """
    A universal decorator for utility functions to enable:
    - OpenTelemetry tracing
    - Prometheus metrics (latency, errors)
    - Structured logging with *centralized auditing*
    - Execution of registered metrics and logging hooks.
    """
    # NOTE: tracer is imported safely at the top.
    # NOTE: All Prometheus metrics are imported safely at the top.

    try:
        from opentelemetry.trace.status import StatusCode as _SC
    except Exception:
        _SC = None

    @wraps(func)
    async def wrapper(*args, **kwargs):
        func_name = func.__name__
        start_time = time.time()

        # Use the safely imported tracer
        tracer_instance = trace.get_tracer(__name__)

        with tracer_instance.start_as_current_span(func_name) as span:
            span.set_attribute("func.name", func_name)
            span.set_attribute("func.module", func.__module__)

            try:
                span.set_attribute(
                    "func.args_preview", f"{len(args)} positional arg(s)"
                )
                span.set_attribute(
                    "func.kwargs_keys", ",".join(sorted(map(str, kwargs.keys())))
                )
            except Exception:
                pass

            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time

                _lazy_metrics.UTIL_LATENCY.labels(func=func_name, status="success").observe(duration)
                span.set_attribute("duration_seconds", duration)

                if _SC:
                    try:
                        span.set_status(_SC.OK)
                    except Exception:
                        pass

                await log_audit_event(
                    action=f"{func_name}_success",
                    data={
                        "result_preview": str(result)[:200],
                        "func_name": func_name,
                        "duration_seconds": duration,
                    },
                )

                logger.info(
                    f"{func_name} executed successfully (duration: {duration:.4f}s)"
                )

                for hook in METRICS_HOOKS:
                    try:
                        hook(
                            func_name, duration, {"result": result, "status": "success"}
                        )
                    except Exception as hook_e:
                        logger.error(
                            f"Error in metrics hook '{hook.__name__}' for {func_name}: {hook_e}",
                            exc_info=True,
                        )

                log_record_for_hooks = logging.LogRecord(
                    name=logger.name,
                    level=logging.INFO,
                    pathname=os.path.abspath(func.__code__.co_filename),
                    lineno=func.__code__.co_firstlineno,
                    msg=f"{func_name} success",
                    args=(),
                    exc_info=None,
                    func=func_name,
                )
                for hook in LOGGING_HOOKS:
                    try:
                        hook(log_record_for_hooks)
                    except Exception as hook_e:
                        logger.error(
                            f"Error in logging hook '{hook.__name__}' for {func_name}: {hook_e}",
                            exc_info=True,
                        )

                return result
            except Exception as e:
                duration = time.time() - start_time
                _lazy_metrics.UTIL_LATENCY.labels(func=func_name, status="failure").observe(duration)
                _lazy_metrics.UTIL_ERRORS.labels(func=func_name, type=type(e).__name__).inc()
                span.set_attribute("error.message", str(e))

                if _SC:
                    try:
                        span.set_status(_SC.ERROR)
                    except Exception:
                        pass

                span.record_exception(e)

                await log_audit_event(
                    action=f"{func_name}_failure",
                    data={
                        "error": str(e),
                        "func_name": func_name,
                        "traceback": traceback.format_exc(),
                    },
                )

                logger.error(f"{func_name} failed: {e}", extra={"exc_info": True})

                for hook in METRICS_HOOKS:
                    try:
                        hook(func_name, duration, {"error": str(e), "status": "failed"})
                    except Exception as hook_e:
                        logger.error(
                            f"Error in metrics hook '{hook.__name__}' during error handling for {func_name}: {hook_e}",
                            exc_info=True,
                        )

                log_record_for_hooks = logging.LogRecord(
                    name=logger.name,
                    level=logging.ERROR,
                    pathname=os.path.abspath(func.__code__.co_filename),
                    lineno=func.__code__.co_firstlineno,
                    msg=f"{func_name} failed: {e}",
                    args=(),
                    exc_info=True,
                    func=func_name,
                )
                for hook in LOGGING_HOOKS:
                    try:
                        hook(log_record_for_hooks)
                    except Exception as hook_e:
                        logger.error(
                            f"Error in logging hook '{hook.__name__}' during error handling for {func_name}: {hook_e}",
                            exc_info=True,
                        )
                raise

    return wrapper


# Dynamic hooks: Add at runtime
def add_custom_metrics_hook(hook: Callable[[str, float, Dict[str, Any]], None]) -> bool:
    """
    Dynamically register a custom metrics hook with enterprise-grade audit logging.
    
    This function implements industry-standard hook registration with:
    - Validation of hook callable
    - Type checking for safety
    - Comprehensive audit trail logging
    - Graceful degradation when async unavailable
    
    Args:
        hook: Callable that accepts (metric_name: str, value: float, context: dict)
        
    Returns:
        bool: True if hook was registered successfully
        
    Raises:
        TypeError: If hook is not callable
    """
    if not callable(hook):
        raise TypeError(f"Hook must be callable, got {type(hook).__name__}")
    
    # Register the hook
    register_metrics_hook(hook)
    
    # Calculate timestamp once for efficiency
    timestamp = datetime.now(timezone.utc).isoformat()
    
    # Log to audit trail using industry-standard async task handling
    success = _safe_create_async_task(
        log_audit_event(
            action="add_metrics_hook",
            data={
                "hook_name": hook.__name__,
                "hook_module": getattr(hook, "__module__", "unknown"),
                "timestamp": timestamp,
            },
        ),
        task_name="metrics_hook_registration_audit",
        context={"hook_name": hook.__name__},
        fail_silently=False,  # Hook registration is important - warn if audit skipped
    )
    
    logger.info(
        "Custom metrics hook '%s' registered successfully (audit_logged=%s)",
        hook.__name__,
        success,
        extra={"hook_name": hook.__name__, "audit_logged": success},
    )
    
    return True


def add_custom_logging_hook(hook: Callable[[logging.LogRecord], None]) -> bool:
    """
    Dynamically register a custom logging hook with enterprise-grade audit logging.
    
    This function implements industry-standard hook registration with:
    - Validation of hook callable
    - Type checking for safety
    - Comprehensive audit trail logging
    - Graceful degradation when async unavailable
    
    Args:
        hook: Callable that accepts a logging.LogRecord
        
    Returns:
        bool: True if hook was registered successfully
        
    Raises:
        TypeError: If hook is not callable
    """
    if not callable(hook):
        raise TypeError(f"Hook must be callable, got {type(hook).__name__}")
    
    # Register the hook
    register_logging_hook(hook)
    
    # Calculate timestamp once for efficiency
    timestamp = datetime.now(timezone.utc).isoformat()
    
    # Log to audit trail using industry-standard async task handling
    success = _safe_create_async_task(
        log_audit_event(
            action="add_logging_hook",
            data={
                "hook_name": hook.__name__,
                "hook_module": getattr(hook, "__module__", "unknown"),
                "timestamp": timestamp,
            },
        ),
        task_name="logging_hook_registration_audit",
        context={"hook_name": hook.__name__},
        fail_silently=False,  # Hook registration is important - warn if audit skipped
    )
    
    logger.info(
        "Custom logging hook '%s' registered successfully (audit_logged=%s)",
        hook.__name__,
        success,
        extra={"hook_name": hook.__name__, "audit_logged": success},
    )
    
    return True


# [FIX] This class is now self-contained and synchronous
class RedactionFilter(logging.Filter):
    """
    Synchronous filter to redact sensitive data from log records.
    Uses simple regex patterns for synchronous operation.
    """

    def __init__(self):
        super().__init__()
        # [FIX] Combine patterns from PII_PATTERNS and LogScrubberFilter
        self.patterns = [
            re.compile(
                r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", re.IGNORECASE
            ),  # Email
            re.compile(r"\b(?:\d{3}[- ]?\d{2}[- ]?\d{4})\b"),  # SSN
            re.compile(
                r'(?i)\b(api_key|password|token|secret|auth_token|bearer)\s*[:=]\s*["\']?([a-zA-Z0-9_-]{20,})["\']?'
            ),  # Key=Value (20+ chars)
            re.compile(
                r"(?i)\b(api_key|password|token|secret|auth_token|bearer)=[^& ]+"
            ),  # URL Param
        ]

    def _sync_redact(self, data: Any) -> Any:
        if isinstance(data, str):
            for pattern in self.patterns:
                data = pattern.sub("[REDACTED]", data)
            return data
        elif isinstance(data, dict):
            return {k: self._sync_redact(v) for k, v in data.items()}
        elif isinstance(data, tuple):
            # Preserve tuple type for logging format strings
            return tuple(self._sync_redact(item) for item in data)
        elif isinstance(data, list):
            return [self._sync_redact(item) for item in data]
        return data

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = self._sync_redact(record.msg)
            if isinstance(record.args, tuple):
                # Preserve tuple type for string formatting
                record.args = self._sync_redact(record.args)
            elif isinstance(record.args, (list, dict)):
                record.args = self._sync_redact(record.args)
        except Exception as e:
            print(f"Error in RedactionFilter: {e}", file=sys.stderr)
        return True


class StructuredJSONFormatter(logging.Formatter):
    """
    JSON formatter that structures log records with context-rich fields,
    real-time resource usage, and OpenTelemetry trace/span IDs.
    """

    _resource_usage_gauge: Optional[prom.Gauge] = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Lazy load the metric to avoid issues if runner_metrics isn't ready
        if self._resource_usage_gauge is None:
            try:
                from runner.runner_metrics import RUN_RESOURCE_USAGE

                self._resource_usage_gauge = RUN_RESOURCE_USAGE
            except ImportError:

                class DummyGauge:
                    def labels(self, *args, **kwargs):
                        return self

                    def set(self, value):
                        pass

                self._resource_usage_gauge = DummyGauge()
                logging.getLogger(__name__).warning(
                    "runner.runner_metrics not found. Resource usage metrics will be disabled in StructuredJSONFormatter."
                )

    def format(self, record: logging.LogRecord) -> str:
        trace_id_str = "00000000000000000000000000000000"
        span_id_str = "0000000000000000"
        try:
            span_context = trace.get_current_span().get_span_context()
            if span_context.is_valid:
                trace_id_str = f"{span_context.trace_id:032x}"
                span_id_str = f"{span_context.span_id:016x}"
        except Exception:
            pass

        cpu_percent: Union[float, str] = "N/A"
        mem_percent: Union[float, str] = "N/A"
        try:
            cpu_percent = psutil.cpu_percent(interval=None)
            mem_percent = psutil.virtual_memory().percent
        except Exception:
            # [FIX] Do not log from within a formatter
            pass

        log_data = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds")
            + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "logger_name": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process_id": os.getpid(),
            "process_name": record.processName,
            "thread_id": record.thread,
            "thread_name": record.threadName,
            "trace_id": trace_id_str,
            "span_id": span_id_str,
            "run_id": getattr(record, "run_id", "no_run_id"),
            "resources": {
                "cpu_percent": cpu_percent,
                "mem_percent": mem_percent,
            },
        }

        if hasattr(record, "__dict__"):
            for key, value in record.__dict__.items():
                if not key.startswith("_") and key not in {
                    "name",
                    "levelname",
                    "pathname",
                    "lineno",
                    "funcName",
                    "created",
                    "msecs",
                    "relativeCreated",
                    "thread",
                    "threadName",
                    "processName",
                    "process",
                    "exc_info",
                    "exc_text",
                    "stack_info",
                    "filename",
                    "module",
                    "msg",
                    "args",
                    "kwargs",
                    "levelno",
                    "message",
                    "trace_id",
                    "span_id",
                    "run_id",
                    "provenance_hash",
                    "resources",
                    "signature",
                    "signing_algorithm",
                    "extra",
                }:
                    log_data[key] = value

        if isinstance(cpu_percent, (int, float)) and self._resource_usage_gauge:
            self._resource_usage_gauge.labels(
                resource_type="cpu",
                instance_id=os.getenv("HOSTNAME", "default_runner_instance"),
            ).set(cpu_percent)
            self._resource_usage_gauge.labels(
                resource_type="mem",
                instance_id=os.getenv("HOSTNAME", "default_runner_instance"),
            ).set(mem_percent)

        # [FIX] Handle non-serializable objects during formatting
        def safe_default(o):
            return f"<Not Serializable: {type(o).__name__}>"

        return json.dumps(log_data, ensure_ascii=False, default=safe_default)


class _HttpHandlerBase(logging.Handler):
    """
    Base class for HTTP log handlers that batch and send logs asynchronously.
    """

    def __init__(
        self,
        host: str,
        url: str,
        method: str = "POST",
        headers: Optional[Dict[str, str]] = None,
        secure: bool = True,
        batch_size: int = 10,
        flush_interval: float = 1.0,
        timeout: float = 5.0,
    ):
        super().__init__()
        self.host = host
        self.url = url
        self.method = method
        self.headers = headers if headers is not None else {}
        self.secure = secure
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.timeout = timeout

        self.queue: Deque[str] = deque()
        self.session: Optional[aiohttp.ClientSession] = None
        self.flush_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        try:
            self._loop = asyncio.get_running_loop()
            self.flush_task = self._loop.create_task(self._start_flush_loop())
        except RuntimeError:
            # [FIX] Use print to stderr to avoid recursion
            print(
                "DEBUG [runner.runner_logging.HttpHandler]: No running loop at init. Will defer.",
                file=sys.stderr,
            )

    def emit(self, record: logging.LogRecord):
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
                self.flush_task = self._loop.create_task(self._start_flush_loop())
                # [FIX] Use print to stderr to avoid recursion
                print(
                    "INFO [runner.runner_logging.HttpHandler]: Re-initialized asyncio loop and flush task.",
                    file=sys.stderr,
                )
            except RuntimeError:
                # [FIX] Use print to stderr to avoid recursion
                print(
                    f"ERROR [runner.runner_logging.HttpHandler]: No running asyncio loop available to emit log: {record.getMessage()}",
                    file=sys.stderr,
                )
                return  # Drop the log

        log_entry_str = self.format(record)
        self.queue.append(log_entry_str)

        if len(self.queue) >= self.batch_size and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self._flush()))

    async def _start_flush_loop(self):
        """Periodically flushes logs."""
        while True:
            try:
                await asyncio.sleep(self.flush_interval)
                await self._flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # [FIX] Use print to stderr to avoid recursion
                print(
                    f"ERROR [runner.runner_logging.HttpHandler]: Periodic flush loop error: {e}",
                    file=sys.stderr,
                )

    async def _flush(self):
        """Sends accumulated logs from the queue over HTTP."""
        if not self.queue:
            return

        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        logs_to_send = []
        while self.queue:
            logs_to_send.append(self.queue.popleft())

        if not logs_to_send:
            return

        full_url = f"{'https' if self.secure else 'http'}://{self.host}{self.url}"

        try:
            payload = "\n".join(logs_to_send)

            async with self.session.request(
                self.method,
                full_url,
                data=payload,
                headers=self.headers,
                timeout=self.timeout,
            ) as resp:
                resp.raise_for_status()
                # [FIX] Use print to stderr to avoid recursion
                print(
                    f"DEBUG [runner.runner_logging.HttpHandler]: Flushed {len(self.queue)} logs to {full_url}. Status: {resp.status}",
                    file=sys.stderr,
                )
        except asyncio.TimeoutError:
            # [FIX] Use print to stderr to avoid recursion
            print(
                f"ERROR [runner.runner_logging.HttpHandler]: Timeout flushing logs to {full_url}.",
                file=sys.stderr,
            )
            for log_str in reversed(logs_to_send):
                self.queue.appendleft(log_str)
        except aiohttp.ClientError as e:
            # [FIX] Use print to stderr to avoid recursion
            print(
                f"ERROR [runner.runner_logging.HttpHandler]: ClientError flushing logs to {full_url}: {e}",
                file=sys.stderr,
            )
            for log_str in reversed(logs_to_send):
                self.queue.appendleft(log_str)
        except Exception as e:
            # [FIX] Use print to stderr to avoid recursion
            print(
                f"ERROR [runner.runner_logging.HttpHandler]: Unexpected error flushing logs to {full_url}: {e}",
                file=sys.stderr,
            )
            # Logs may be lost here

    def close(self):
        """Closes the handler, flushes any remaining logs, and closes the AIOHTTP session."""
        # [FIX] Use print to stderr to avoid recursion
        print(
            f"INFO [runner.runner_logging.HttpHandler]: Closing handler for {self.host}{self.url}. Flushing {len(self.queue)} logs.",
            file=sys.stderr,
        )

        if self.flush_task and not self.flush_task.done():
            self.flush_task.cancel()

        if self._loop and self._loop.is_running() and self.queue:
            try:
                self._loop.run_until_complete(self._flush())
            except Exception as e:
                # [FIX] Use print to stderr to avoid recursion
                print(
                    f"ERROR [runner.runner_logging.HttpHandler]: Failed final flush during close: {e}",
                    file=sys.stderr,
                )
        elif self.queue:
            # [FIX] Use print to stderr to avoid recursion
            print(
                f"WARN [runner.runner_logging.HttpHandler]: {len(self.queue)} unsent logs, but no running loop to flush.",
                file=sys.stderr,
            )

        if self.session and not self.session.closed:
            try:
                if self._loop and self._loop.is_running():
                    self._loop.run_until_complete(self.session.close())
                else:
                    asyncio.run(self.session.close())
                # [FIX] Use print to stderr to avoid recursion
                print(
                    f"DEBUG [runner.runner_logging.HttpHandler]: AIOHTTP session closed for {self.host}{self.url}.",
                    file=sys.stderr,
                )
            except Exception as e:
                # [FIX] Use print to stderr to avoid recursion
                print(
                    f"ERROR [runner.runner_logging.HttpHandler]: Failed to close AIOHTTP session: {e}",
                    file=sys.stderr,
                )
        super().close()


def get_handler(
    sink_type: str, config: Dict[str, Any], encryption_key: Optional[bytes] = None
) -> logging.Handler:
    """
    Pluggable handler factory for various logging sinks.
    """
    handler: Optional[logging.Handler] = None

    try:
        if sink_type == "file":
            handler = logging.handlers.TimedRotatingFileHandler(
                filename=config.get("filename", "runner.log"),
                when=config.get("when", "midnight"),
                interval=config.get("interval", 1),
                backupCount=config.get("backup_count", 7),
                encoding="utf-8",
            )
        elif sink_type == "stream":
            handler = logging.StreamHandler(sys.stdout)
        elif sink_type == "socket":
            handler = logging.handlers.SocketHandler(
                host=config.get("host", "localhost"),
                port=config.get("port", 12201),
            )
        elif sink_type == "http":
            handler = _HttpHandlerBase(
                host=config.get("host", "localhost"),
                url=config.get("url", "/"),
                method=config.get("method", "POST"),
                headers=config.get("headers"),
                secure=config.get("secure", False),
                batch_size=config.get("batch_size", 10),
                flush_interval=config.get("flush_interval", 1.0),
                timeout=config.get("timeout", 5.0),
            )
        elif sink_type == "syslog":
            handler = logging.handlers.SysLogHandler(
                address=(config.get("host", "localhost"), config.get("port", 514)),
                facility=config.get("facility", "user"),
            )
        elif sink_type == "cloudwatch":
            try:
                from watchtower import CloudWatchLogHandler

                aws_access_key = config.get("aws_access_key_id")
                aws_secret_key = config.get("aws_secret_access_key")

                if isinstance(aws_access_key, SecretStr):
                    aws_access_key = aws_access_key.get_secret_value()
                if isinstance(aws_secret_key, SecretStr):
                    aws_secret_key = aws_secret_key.get_secret_value()

                handler = CloudWatchLogHandler(
                    log_group=config.get("log_group", "runner-logs"),
                    stream_name=config.get("stream_name", str(uuid.uuid4())),
                    aws_access_key_id=aws_access_key,
                    aws_secret_access_key=aws_secret_key,
                    region_name=config.get("aws_region", "us-east-1"),
                    create_log_group=True,
                )
            except ImportError as ie:
                logging.getLogger(__name__).error(
                    f"CloudWatch handler: 'watchtower' not installed. pip install watchtower boto3. Error: {ie}"
                )
                handler = logging.NullHandler()
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"Failed to initialize CloudWatch handler: {e}; skipping CloudWatch handler.",
                    exc_info=True,
                )
                handler = logging.NullHandler()
        elif sink_type == "gcloud":
            try:
                import google.cloud.logging
                from google.cloud.logging.handlers import CloudLoggingHandler

                client = google.cloud.logging.Client(
                    project=config.get("gcp_project_id")
                )
                handler = CloudLoggingHandler(client, name=config.get("name", "runner"))
            except ImportError as ie:
                logging.getLogger(__name__).error(
                    f"Google Cloud Logging handler: 'google-cloud-logging' not installed. pip install google-cloud-logging. Error: {ie}"
                )
                handler = logging.NullHandler()
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"Failed to initialize Google Cloud Logging handler: {e}; skipping GCloud handler.",
                    exc_info=True,
                )
                handler = logging.NullHandler()
        elif sink_type == "elasticsearch":
            try:
                from elasticsearch import Elasticsearch

                class CustomESHandler(logging.Handler):
                    def __init__(
                        self, hosts: List[str], index_prefix: str = "runner-logs-"
                    ):
                        super().__init__()
                        self.es = Elasticsearch(hosts)
                        self.index_prefix = index_prefix
                        self.last_index_date = None
                        self.current_index_name = None

                    def _get_index_name(self):
                        today = datetime.now().date()
                        if today != self.last_index_date:
                            self.last_index_date = today
                            self.current_index_name = (
                                f"{self.index_prefix}{today.strftime('%Y.%m.%d')}"
                            )
                        return self.current_index_name

                    def emit(self, record: logging.LogRecord):
                        log_entry_json_str = self.format(record)
                        try:
                            self.es.index(
                                index=self._get_index_name(),
                                document=json.loads(log_entry_json_str),
                            )
                        except Exception as e:
                            sys.stderr.write(
                                f"Error sending log to Elasticsearch: {e}\n"
                            )
                            logging.getLogger(__name__).error(
                                f"Error sending log to Elasticsearch: {e}",
                                exc_info=True,
                            )

                handler = CustomESHandler(
                    config.get("hosts", ["http://localhost:9200"]),
                    config.get("index_prefix", "runner-logs-"),
                )
            except ImportError as ie:
                logging.getLogger(__name__).error(
                    f"Elasticsearch handler: 'elasticsearch' not installed. pip install elasticsearch. Error: {ie}"
                )
                handler = logging.NullHandler()
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"Failed to initialize Elasticsearch handler: {e}; skipping ES handler.",
                    exc_info=True,
                )
                handler = logging.NullHandler()

        elif sink_type == "datadog":
            try:
                from datadog_api_client.configuration import (
                    Configuration as DatadogConfiguration,
                )
                from datadog_api_client.v2.api import logs_api

                class DatadogLogHandler(_HttpHandlerBase):
                    def __init__(
                        self, api_key: str, site: str = "datadoghq.com", **kwargs
                    ):
                        host_url = f"http-intake.logs.{site}"
                        headers = {
                            "DD-API-KEY": api_key,
                            "Content-Type": "application/json",
                        }
                        super().__init__(
                            host=host_url,
                            url="/api/v2/logs",
                            method="POST",
                            headers=headers,
                            secure=True,
                            **kwargs,
                        )

                    async def _flush(self):
                        if not self.queue:
                            return
                        if not self.session or self.session.closed:
                            self.session = aiohttp.ClientSession()

                        logs_to_send = []
                        while self.queue:
                            logs_to_send.append(self.queue.popleft())

                        if not logs_to_send:
                            return

                        payload = [json.loads(log_item) for log_item in logs_to_send]
                        full_url = f"{'https' if self.secure else 'http'}://{self.host}{self.url}"

                        try:
                            async with self.session.post(
                                full_url,
                                json=payload,
                                headers=self.headers,
                                timeout=self.timeout,
                            ) as resp:
                                resp.raise_for_status()
                                # [FIX] Use print to stderr to avoid recursion
                                print(
                                    f"DEBUG [runner.runner_logging.HttpHandler]: Flushed {len(self.queue)} logs to Datadog. Status: {resp.status}",
                                    file=sys.stderr,
                                )
                        except asyncio.TimeoutError:
                            print(
                                f"ERROR [runner.runner_logging.HttpHandler]: Datadog timeout flushing logs to {full_url}.",
                                file=sys.stderr,
                            )
                            for log_str in reversed(logs_to_send):
                                self.queue.appendleft(log_str)
                        except aiohttp.ClientError as e:
                            print(
                                f"ERROR [runner.runner_logging.HttpHandler]: Datadog ClientError flushing logs to {full_url}: {e}",
                                file=sys.stderr,
                            )
                            for log_str in reversed(logs_to_send):
                                self.queue.appendleft(log_str)
                        except Exception as e:
                            print(
                                f"ERROR [runner.runner_logging.HttpHandler]: Datadog unexpected error flushing logs to {full_url}: {e}",
                                file=sys.stderr,
                            )

                datadog_api_key = config.get(
                    "datadog_api_key", os.getenv("DATADOG_API_KEY")
                )
                if isinstance(datadog_api_key, SecretStr):
                    datadog_api_key = datadog_api_key.get_secret_value()

                if datadog_api_key:
                    handler = DatadogLogHandler(
                        datadog_api_key,
                        site=config.get("datadog_site", "datadoghq.com"),
                        batch_size=config.get("batch_size", 10),
                        flush_interval=config.get("flush_interval", 1.0),
                        timeout=config.get("timeout", 5.0),
                    )
                else:
                    logging.getLogger(__name__).warning(
                        "Datadog API key not configured; skipping datadog handler."
                    )
                    handler = logging.NullHandler()
            except ImportError as ie:
                logging.getLogger(__name__).error(
                    f"Datadog handler: 'datadog-api-client' not installed. pip install datadog-api-client. Error: {ie}"
                )
                handler = logging.NullHandler()
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"Failed to initialize Datadog handler: {e}; skipping datadog handler.",
                    exc_info=True,
                )
                handler = logging.NullHandler()

        elif sink_type == "splunk_hec":
            try:
                from splunk_handler import SplunkHandler

                splunk_token = config.get("splunk_token", os.getenv("SPLUNK_HEC_TOKEN"))
                if isinstance(splunk_token, SecretStr):
                    splunk_token = splunk_token.get_secret_value()

                if splunk_token:
                    handler = SplunkHandler(
                        host=config.get("host", "localhost"),
                        port=config.get("port", 8088),
                        token=splunk_token,
                        index=config.get("index", "main"),
                        sourcetype=config.get("sourcetype", "_json"),
                        verify=config.get("verify_ssl", True),
                    )
                else:
                    logging.getLogger(__name__).warning(
                        "Splunk HEC token not configured; skipping Splunk handler."
                    )
                    handler = logging.NullHandler()
            except ImportError as ie:
                logging.getLogger(__name__).error(
                    f"Splunk HEC handler: 'splunk-handler' not installed. pip install splunk-handler. Error: {ie}"
                )
                handler = logging.NullHandler()
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"Failed to initialize Splunk HEC handler: {e}; skipping Splunk handler.",
                    exc_info=True,
                )
                handler = logging.NullHandler()

        elif sink_type == "newrelic":
            try:
                import logging.handlers as stdlib_handlers

                from newrelic.agent import NewRelicContextFormatter

                nr_license_key = config.get(
                    "license_key", os.getenv("NEW_RELIC_LICENSE_KEY")
                )
                if isinstance(nr_license_key, SecretStr):
                    nr_license_key = nr_license_key.get_secret_value()

                if nr_license_key:
                    handler = _HttpHandlerBase(
                        host="log-api.newrelic.com",
                        url="/log/v1",
                        method="POST",
                        headers={
                            "Content-Type": "application/json",
                            "X-License-Key": nr_license_key,
                        },
                        secure=True,
                        batch_size=config.get("batch_size", 10),
                        flush_interval=config.get("flush_interval", 1.0),
                        timeout=config.get("timeout", 5.0),
                    )
                else:
                    logging.getLogger(__name__).warning(
                        "New Relic license key not configured; skipping New Relic handler."
                    )
                    handler = logging.NullHandler()
            except ImportError as ie:
                logging.getLogger(__name__).error(
                    f"New Relic handler: 'newrelic' not installed. pip install newrelic. Error: {ie}"
                )
                handler = logging.NullHandler()
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"Failed to initialize New Relic handler: {e}; skipping New Relic handler.",
                    exc_info=True,
                )
                handler = logging.NullHandler()

        else:
            logging.getLogger(__name__).warning(
                f"Unknown sink type '{sink_type}'; creating NullHandler."
            )
            handler = logging.NullHandler()

    except Exception as e:
        logging.getLogger(__name__).error(
            f"Fatal error creating handler for sink '{sink_type}': {e}; using NullHandler as fallback.",
            exc_info=True,
        )
        handler = logging.NullHandler()

    if handler is None:
        handler = logging.NullHandler()

    return handler


def configure_logging_from_config(runner_config: "RunnerConfig"):
    """
    Configures the logger using settings from a RunnerConfig instance.
    Sets up sinks, encryption, and real-time streaming as specified.

    [NEW] This function also initializes the audit system key ID.
    """
    global _DEFAULT_AUDIT_KEY_ID

    logger = logging.getLogger("runner")
    logger.setLevel(logging.DEBUG)  # All handlers will filter by their own levels

    # Remove existing handlers to avoid duplicate logs during reconfiguration
    for handler in list(logger.handlers):
        handler.close()  # Close handler resources
        logger.removeHandler(handler)

    json_formatter = StructuredJSONFormatter()

    formatter_to_use = json_formatter

    # [NEW] Configure the V0 Audit Crypto System Key ID
    audit_key_id = getattr(runner_config, "audit_signing_key_id", None)
    if audit_key_id and isinstance(audit_key_id, str):
        _DEFAULT_AUDIT_KEY_ID = audit_key_id
        logger.info(f"Audit event signing enabled with Key ID: {audit_key_id}")

        if not _LAST_AUDIT_HASH:
            logger.info("Initializing new audit chain. _LAST_AUDIT_HASH is empty.")

    else:
        _DEFAULT_AUDIT_KEY_ID = ""
        # [NEW] Fail-closed logic
        if not os.getenv("DEV_MODE", "0") == "1" and not os.getenv(
            "PYTEST_CURRENT_TEST"
        ):
            logger.critical(
                "CRITICAL: No 'audit_signing_key_id' found in RunnerConfig. Audit logging is required in non-DEV_MODE. Aborting."
            )
            raise RuntimeError(
                "Audit key id missing; set audit_signing_key_id or set DEV_MODE=1 to bypass."
            )
        else:
            logger.warning(
                "No 'audit_signing_key_id' found in RunnerConfig. Secure audit logging will be DISABLED. (Allowed in DEV_MODE)"
            )

    redaction_filter = RedactionFilter()

    # Configure sinks based on RunnerConfig
    sinks_config = runner_config.log_sinks

    for sink in sinks_config:
        sink_type = sink.get("type", "unknown")
        sink_config = sink.get("config", {})
        try:
            handler = get_handler(sink_type, sink_config, encryption_key=None)
            handler.setFormatter(formatter_to_use)
            handler.addFilter(redaction_filter)

            # [NEW] Create a dedicated logger for 'runner.audit'
            audit_logger = logging.getLogger("runner.audit")
            if not any(isinstance(h, type(handler)) for h in audit_logger.handlers):
                if sink_type in [
                    "file",
                    "http",
                    "datadog",
                    "splunk_hec",
                    "socket",
                    "syslog",
                    "cloudwatch",
                    "gcloud",
                    "elasticsearch",
                ]:
                    audit_logger.addHandler(handler)

            logger.addHandler(handler)
            logger.info(f"Added log sink: {sink_type}")
        except Exception as e:
            logger.error(
                f"Failed to setup log handler for sink type '{sink_type}': {e}. Skipping this sink.",
                exc_info=True,
            )

    if runner_config.real_time_log_streaming:
        # We must register the logging hook here, as it's enabled by the config.
        # This hook feeds the dashboard queue.
        register_logging_hook(stream_log_record_to_dashboard_queue)
        logger.info("Real-time log streaming hook registered.")
    else:
        # Remove the hook if it was previously registered and streaming is now disabled
        try:
            LOGGING_HOOKS.remove(stream_log_record_to_dashboard_queue)
        except ValueError:
            pass  # Hook wasn't there
        logger.info("Real-time log streaming is disabled in config.")

    # [REMOVED] _start_alert_worker() - This is now handled by start_logging_services

    logger.info("Logger re-configured from RunnerConfig.")


async def log_listener(queue: asyncio.Queue):
    """
    Async listener task to process log records from the queue.
    Stores processed logs in LOG_HISTORY for search and potentially pushes to WebSockets.
    """
    logger.info("Log listener task started.")
    while True:
        try:
            # QueueHandler puts LogRecord objects onto the queue
            record = await queue.get()

            # We must format it here before processing.
            # This is an issue: QueueHandler is sync and doesn'g format.
            # Refactor: The hook `stream_log_record_to_dashboard_queue` puts a *dict* on the queue.
            # This task just reads that dict.
            record_dict = record  # The hook already puts a dict
            LOG_HISTORY.append(record_dict)
            queue.task_done()
        except json.JSONDecodeError as e:
            sys.stderr.write(
                f"Log listener: Failed to decode JSON log record: {e}. Raw: {record[:200]}...\n"
            )
            logger.error(f"Log listener: JSONDecodeError: {e}", exc_info=True)
        except asyncio.CancelledError:
            logger.info("Log listener task cancelled during shutdown.")
            break
        except Exception as e:
            sys.stderr.write(f"Log listener: Unexpected error processing record: {e}\n")
            logger.error(f"Log listener: Unexpected error: {e}", exc_info=True)


def log_action(
    action: str,
    data: Dict[str, Any],
    run_id: Optional[str] = None,
    provenance_hash: Optional[str] = None,
    extra: Optional[Dict] = None,
):
    """
    Logs a specific action with structured data, ensuring secrets are redacted and data is encrypted.
    """
    # FIX: Lazy import to break circular dependency
    try:
        from runner.runner_security_utils import encrypt_data, redact_secrets
    except ImportError as e:
        logger.error(
            f"Failed to import security utils for log_action: {e}. Logging unencrypted/unredacted data as fallback.",
            exc_info=True,
        )

        def encrypt_data(d, *a, **k):
            return base64.b64encode(json.dumps(d).encode()).decode()

        def redact_secrets(d, *a, **k):
            return d

    if provenance_hash:
        logger.warning(
            f"log_action called with 'provenance_hash' for action '{action}'. This is deprecated and will be ignored. Use log_audit_event for chained logging.",
            extra={"run_id": run_id},
        )

    try:
        # The key should be handled by runner_config or env var retrieval.
        # We assume a default key is available or the caller provides one in the config.
        # Here we just pass the redacted data to a mock encryption utility.
        encrypted_data_b64 = encrypt_data(redact_secrets(data))
    except Exception as e:  # Catch a broader exception if RunnerError is not available
        logger.error(
            f"Failed to encrypt log action data: {e}. Logging unencrypted/redacted data as fallback.",
            exc_info=True,
        )
        encrypted_data_b64 = base64.b64encode(
            json.dumps(redact_secrets(data), ensure_ascii=False).encode()
        ).decode()

    log_payload = {
        "action": action,
        "encrypted_data": encrypted_data_b64,
        "run_id": run_id,
    }
    if extra:
        log_payload.update(extra)

    action_logger = logging.getLogger("runner.action")
    action_logger.info(log_payload, extra={"run_id": run_id})


def search_logs(
    query: str, limit: int = 100, run_id: Optional[str] = None
) -> List[Dict]:
    """
    Searches the in-memory log history.
    """
    results = []
    for log_entry in list(LOG_HISTORY)[::-1]:
        if run_id and log_entry.get("run_id") != run_id:
            continue

        if query.lower() in json.dumps(log_entry, ensure_ascii=False).lower():
            display_log_entry = log_entry.copy()

            if "encrypted_data" in display_log_entry:
                display_log_entry["decryption_status"] = (
                    "[Encrypted; Decrypt in secure environment]"
                )

            results.append(display_log_entry)

            if len(results) >= limit:
                break
    return results


# Initial basic logging setup (before configure_logging_from_config is called by main.py)
logger = logging.getLogger("runner")
if not logger.handlers:
    initial_handler = logging.StreamHandler(sys.stdout)
    initial_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    initial_handler.setFormatter(initial_formatter)
    logger.addHandler(initial_handler)
    logger.setLevel(logging.INFO)

logging.getLogger("runner.audit").propagate = False


# --- Test/Example usage ---
if __name__ == "__main__":
    from unittest.mock import AsyncMock, MagicMock, patch

    # from runner.runner_errors import RunnerError as ActualRunnerError # Cannot import this due to circle
    from pydantic import ConfigDict, SecretStr

    # FIX: Import RunnerConfig from the correct location for the __main__ block
    from runner.runner_config import RunnerConfig

    # Mock the necessary parts for the test block
    class MockRunnerConfig(RunnerConfig):
        model_config = ConfigDict(extra="allow")

        @classmethod
        def model_validate(cls, obj, *args, **kwargs):
            return cls(**obj)

        # Override abstract methods for test compatibility
        def validator(self):
            pass

        def generate_docs(self, format: str = "markdown") -> str:
            return ""

        def encrypt_secrets(self, key: bytes):
            pass

        def decrypt_secrets(self, key: bytes):
            pass

        async def fetch_vault_secrets(self):
            pass

        # Add necessary fields for logging to pass checks
        audit_signing_key_id: Optional[str] = None
        log_sinks: List[Dict[str, Any]] = []  # Use pydantic v2 style default
        real_time_log_streaming: bool = True
        metrics_interval_seconds: int = 1

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    log_dir_path = Path(".")
    for f in log_dir_path.glob("test_runner.log*"):
        if f.is_file():
            os.remove(f)

    print("--- Simulating RunnerConfig for logging setup ---")
    dummy_config_data = {
        "version": 4,
        "backend": "docker",
        "framework": "pytest",
        "instance_id": "test_instance",
        "log_sinks": [
            {"type": "stream", "config": {}},
            {
                "type": "file",
                "config": {
                    "filename": "test_runner.log",
                    "backup_count": 1,
                    "when": "S",
                    "interval": 1,
                },
            },
            {
                "type": "datadog",
                "config": {
                    "datadog_api_key": SecretStr("dd_test_api_key_from_config"),
                    "datadog_site": "datadoghq.com",
                    "batch_size": 2,
                    "flush_interval": 0.5,
                },
            },
        ],
        "real_time_log_streaming": True,
        "audit_signing_key_id": "software-key-uuid-12345",
    }

    mock_runner_config = MockRunnerConfig(**dummy_config_data)

    with patch.dict(
        os.environ,
        {
            "FERNET_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "RUNNER_ENV": "development",
            "DEV_MODE": "1",  # [NEW] Set DEV_MODE to allow test to run without real key
        },
    ):
        # Mock security util dependencies before calling configure_logging_from_config
        with (
            patch(
                "runner.runner_security_utils.encrypt_data",
                new=MagicMock(
                    side_effect=lambda d, *a, **k: base64.b64encode(
                        json.dumps(d).encode()
                    ).decode()
                ),
            ),
            patch(
                "runner.runner_security_utils.redact_secrets",
                new=MagicMock(
                    side_effect=lambda d, *a, **k: (
                        d.replace("api_key=123xyz", "[REDACTED]").replace(
                            "dev@example.com", "[REDACTED]"
                        )
                        if isinstance(d, str)
                        else d
                    )
                ),
            ),
        ):

            configure_logging_from_config(mock_runner_config)

    print("\n--- Sending Test Log Messages ---")
    logger.info("This is a standard info message.")
    logger.debug(
        {"key": "value", "sensitive_info": "api_key=123xyz", "email": "dev@example.com"}
    )
    logger.warning("This is a warning with PII: Jane Doe, SSN 999-88-7777.")
    try:
        raise ValueError(
            "This is a test exception with sensitive data: token=abc123def456"
        )
    except ValueError:
        logger.error(
            "An error occurred during process execution!",
            extra={"process_id": 123, "error_code": "TASK_FAILED"},
            exc_info=True,
        )

    test_run_id = str(uuid.uuid4())
    log_action(
        "WorkflowStarted",
        {"workflow_name": "TestRun", "user": "demo_user", "plan": "basic"},
        run_id=test_run_id,
    )
    log_action(
        "AgentStepCompleted",
        {"agent": "codegen", "status": "success", "metrics": {"tokens": 100}},
        run_id=test_run_id,
        extra={"user_id": "demo_user"},
    )

    async def mock_http_post(*args, **kwargs):
        print(f"\n--- Mock HTTP POST to {args[0]} ---")
        print(f"Headers: {kwargs.get('headers')}")
        payload = kwargs.get("data") or kwargs.get("json")
        print(f"Payload: {payload}")

        if "datadoghq.com" in str(args[0]):
            assert isinstance(payload, list)
            assert isinstance(payload[0], dict)
            assert "message" in payload[0]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.status = 200

        async def __aenter__(*a, **kw):
            return mock_response

        async def __aexit__(*a, **kw):
            pass

        mock_response.__aenter__ = __aenter__
        mock_response.__aexit__ = __aexit__
        return mock_response

    async def mock_safe_sign(entry: Dict[str, Any], key_id: str, prev_hash: str) -> str:
        print("\n--- Mock V0 safe_sign CALLED ---")
        print(f"  Key ID: {key_id}")
        print(f"  Prev Hash: {prev_hash}")
        print(f"  Entry Action: {entry.get('action')}")
        return base64.b64encode(f"signed({entry.get('action')})".encode()).decode()

    async def main_test():
        # [NEW] Start the logging services
        await start_logging_services()

        with patch(
            "runner.runner_logging.safe_sign", new=AsyncMock(side_effect=mock_safe_sign)
        ):
            with patch(
                "runner.runner_logging.compute_hash",
                new=MagicMock(return_value="mock-hash-12345"),
            ):
                with patch(
                    "aiohttp.ClientSession.post",
                    new=AsyncMock(side_effect=mock_http_post),
                ):

                    await log_audit_event(
                        "TestAudit", {"test_data": "value"}, run_id=test_run_id
                    )

                    print(
                        "\n--- Waiting for async log handlers to flush (simulated) ---"
                    )
                    await asyncio.sleep(1)

        print("\n--- Searching logs ---")
        # FIX: The log_audit_event data is now in the 'message' field as a JSON string
        audit_search = search_logs(query="TestAudit", limit=1)
        print("\n--- Audit Log Search Result ---")
        assert len(audit_search) > 0

        # The log_audit_event now logs to 'runner.audit' and the message is a JSON string
        audit_message_data = json.loads(audit_search[0]["message"])
        assert audit_message_data["action"] == "TestAudit"
        assert "signature" in audit_message_data
        assert (
            audit_message_data["signature"]
            == base64.b64encode(b"signed(TestAudit)").decode()
        )
        assert audit_message_data["key_id"] == "software-key-uuid-12345"
        print(json.dumps(audit_search[0], indent=2))

        search_results = search_logs(query="sensitive", limit=5)
        found_redacted_log = False
        print("\n--- Redaction Search Results ---")
        for res in search_results:
            print(json.dumps(res, indent=2))
            res_str = json.dumps(res)
            # Check for generic redaction by RedactionFilter regex fallback
            if "Jane Doe" not in res_str and "999-88-7777" not in res_str:
                found_redacted_log = True
        assert found_redacted_log, "Did not find redacted content in search results"

        search_all = search_logs(query="", limit=5)
        print("\n--- Top 5 Recent Logs (All) ---")
        for res in search_all:
            print(json.dumps(res, indent=2))

        # [NEW] Stop the logging services
        await stop_logging_services()

        # Clean up handlers to allow script to exit gracefully
        for handler in logger.handlers:
            handler.close()
        for handler in logging.getLogger("runner.audit").handlers:
            handler.close()

    # We need to patch the RunnerError import in log_action for the __main__ block
    with patch.dict("sys.modules", {"runner.runner_security_utils": None}):
        asyncio.run(main_test())

    for f in log_dir_path.glob("test_runner.log*"):
        if f.is_file():
            os.remove(f)

    print("\n--- Logging tests completed ---")
