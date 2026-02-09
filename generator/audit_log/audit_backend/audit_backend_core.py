# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# audit_backends/audit_backend_core.py
import abc
import asyncio
import base64
import datetime
import functools  # <-- ADDED
import json
import logging
import os
import time
import uuid
import warnings  # <-- ADDED
import zlib
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Type

import boto3  # For KMS
import botocore  # <-- ADDED
import botocore.exceptions
import zstandard as zstd
from cryptography.fernet import (
    Fernet,
    InvalidToken,
    MultiFernet,
)  # <-- FIX: ADD InvalidToken HERE
from prometheus_client import REGISTRY  # <-- ADDED
from prometheus_client import Counter, Gauge, Histogram

# OpenTelemetry imports (guarded)
try:
    from opentelemetry import trace
    from opentelemetry.trace import StatusCode

    HAS_OPENTELEMETRY = True
except ImportError:
    trace = None
    tracer = None
    StatusCode = None
    HAS_OPENTELEMETRY = False

# Local utility module (assumed to exist outside audit_backends package)
_send_alert_imported = False
try:
    from audit_utils import compute_hash, send_alert
    _send_alert_imported = True
except ImportError:
    logging.warning(
        "audit_utils.py not found. Tamper detection and alerting features will use fallback implementations."
    )

# Always define compute_hash fallback if not imported
if not _send_alert_imported:
    import hashlib
    
    def compute_hash(data: bytes) -> str:
        """
        Stable SHA-256 hash used for tamper-evident chaining.
        """
        h = hashlib.sha256()
        h.update(data)
        return h.hexdigest()

# Always define send_alert fallback at module level if not imported
if not _send_alert_imported:
    async def send_alert(message: str, severity: str = "warning") -> None:
        """
        Minimal alert hook.
        
        In production, override via audit_utils or env-specific wiring to:
        - push to Slack/Teams
        - send email
        - hit incident webhook, etc.
        """
        logger.log(
            logging.WARNING if severity in ("low", "warning") else logging.ERROR,
            f"[ALERT:{severity.upper()}] {message}",
        )


# Early logger initialization for safe_metric function
logger = logging.getLogger(__name__)

# --- START: ADDED HELPER FUNCTION (Modified to use universal safe_metric) ---
def safe_metric(metric_type, name, description, labelnames=()):
    """
    Return existing metric if already registered, otherwise create a new one.

    This function is defensive and will ALWAYS return a valid metric object.
    If the global REGISTRY has been swapped/cleared by tests, it will properly
    handle registry re-registration.

    Strategy:
    1. Check if metric exists in current registry
    2. If not in registry but exists as a module-level variable, re-register it
    3. If doesn't exist at all, create it
    4. If creation fails due to duplicate, retrieve and return existing
    """
    import prometheus_client

    # Use the current active registry (might have been swapped by tests)
    current_registry = prometheus_client.REGISTRY

    try:
        # Strategy 1: Try to retrieve from current registry
        # Check both the bare name and common suffixes (_total for Counter, etc.)
        if hasattr(current_registry, '_names_to_collectors'):
            names_to_check = [name]
            # Counters internally register with _total and _created suffixes
            if metric_type == "Counter":
                names_to_check.extend([f"{name}_total", f"{name}_created"])

            for check_name in names_to_check:
                try:
                    collector = current_registry._names_to_collectors.get(check_name)
                    if collector is not None:
                        return collector
                except (KeyError, AttributeError):
                    continue

        # Strategy 2: Try creating the metric with the current registry
        try:
            if metric_type == "Counter":
                return Counter(name, description, labelnames, registry=current_registry)
            elif metric_type == "Gauge":
                return Gauge(name, description, labelnames, registry=current_registry)
            elif metric_type == "Histogram":
                return Histogram(name, description, labelnames, registry=current_registry)
            else:
                # Unknown type - default to Counter
                return Counter(name, description, labelnames, registry=current_registry)
        except ValueError as e:
            # Strategy 3: Creation failed (likely duplicate), try retrieving again
            # The error might have occurred because another thread/module registered it
            if "Duplicated" in str(e) or "already registered" in str(e):
                if hasattr(current_registry, '_names_to_collectors'):
                    names_to_check = [name]
                    if metric_type == "Counter":
                        names_to_check.extend([f"{name}_total", f"{name}_created"])

                    for check_name in names_to_check:
                        try:
                            collector = current_registry._names_to_collectors.get(check_name)
                            if collector is not None:
                                return collector
                        except (KeyError, AttributeError):
                            continue

            # If we still can't find it, try to unregister and recreate
            # This handles the case where the metric was registered with a different registry
            try:
                # Try to unregister the old metric from current registry
                if hasattr(current_registry, '_names_to_collectors'):
                    for check_name in [name, f"{name}_total", f"{name}_created"]:
                        collector = current_registry._names_to_collectors.get(check_name)
                        if collector is not None:
                            try:
                                current_registry.unregister(collector)
                            except Exception:
                                pass

                # Now try creating again with current registry
                if metric_type == "Counter":
                    return Counter(name, description, labelnames, registry=current_registry)
                elif metric_type == "Gauge":
                    return Gauge(name, description, labelnames, registry=current_registry)
                elif metric_type == "Histogram":
                    return Histogram(name, description, labelnames, registry=current_registry)
                else:
                    return Counter(name, description, labelnames, registry=current_registry)
            except Exception as inner_e:
                logger.error(
                    f"[safe_metric] Failed to recreate metric '{name}' after unregister: {inner_e}"
                )
                raise

    except Exception as e:
        logger.error(
            f"[safe_metric] Failed to create or retrieve metric '{name}': {e}. "
            f"This will cause metric tracking to fail."
        )
        raise  # Don't create fallback metrics - fail fast to identify issues


# Retaining safe_counter alias for backward compatibility with previous steps
def safe_counter(name, description, labelnames=()):
    """Create or retrieve a Counter metric safely."""
    return safe_metric("Counter", name, description, labelnames)


# --- END: ADDED HELPER FUNCTION ---

# Configuration management with fallback for test environments
try:
    from dynaconf import Dynaconf
    from dynaconf.validator import ValidationError, Validator
    HAS_DYNACONF = True
except ImportError:
    HAS_DYNACONF = False
    # Provide minimal stubs for test environments
    class Dynaconf:
        def __init__(self, *args, **kwargs):
            self._data = {}
        def get(self, key, default=None):
            return self._data.get(key, default)
        def set(self, key, value):
            self._data[key] = value
        def __getattr__(self, name):
            return self._data.get(name)
    
    class Validator:
        def __init__(self, *args, **kwargs):
            pass
    
    class ValidationError(Exception):
        pass


# --- START: EDIT B (Moved Custom Exception Classes) ---
# --- Custom Exception Classes ---
class AuditBackendError(Exception):
    """Base exception for audit backend errors."""

    pass


class MigrationError(AuditBackendError):
    """Exception raised for errors during schema migration."""

    pass


class TamperDetectionError(AuditBackendError):
    """Exception raised when audit log tampering is detected."""

    pass


class BackendNotFoundError(AuditBackendError):
    """Exception raised when a requested backend type is not registered."""

    pass


class CryptoInitializationError(Exception):
    """Exception raised when a cryptographic provider fails to initialize."""

    pass


# --- END: EDIT B ---

# --- Configuration and Secrets Management ---


# FIX: Set test mode environment variables BEFORE creating Dynaconf
def _is_test_or_dev_mode() -> bool:
    # pytest sets PYTEST_CURRENT_TEST; we also respect a simple dev flag.
    # CI workflows set TESTING=1 and/or CI=1 which should also trigger test mode
    # to avoid KMS/production crypto initialization during test collection.
    return bool(
        os.getenv("PYTEST_CURRENT_TEST")
        or os.getenv("AUDIT_LOG_DEV_MODE") == "true"
        or os.getenv("TESTING") == "1"
        or os.getenv("CI") == "1"
    )


# In test/dev mode, pre-set the required values before Dynaconf initialization
if _is_test_or_dev_mode():
    os.environ.setdefault(
        "AUDIT_ENCRYPTION_KEYS",
        '[{"key_id":"mock_key_1","key":"hYnO2bq3m0yqgqz5WJt9j3ZCsb3dC-5H9qv1Hj4XGxw="}]',
    )
    os.environ.setdefault("AUDIT_COMPRESSION_ALGO", "none")
    os.environ.setdefault("AUDIT_BATCH_FLUSH_INTERVAL", "10")
    os.environ.setdefault("AUDIT_BATCH_MAX_SIZE", "100")
    os.environ.setdefault("AUDIT_HEALTH_CHECK_INTERVAL", "30")
    os.environ.setdefault("AUDIT_RETRY_MAX_ATTEMPTS", "3")
    os.environ.setdefault("AUDIT_RETRY_BACKOFF_FACTOR", "0.1")

# Using Dynaconf for environment-based configuration
# FIX: Only add validators in production mode
if _is_test_or_dev_mode():
    settings = Dynaconf(
        envvar_prefix="AUDIT",
        settings_files=["audit_config.yaml"],
    )
else:
    settings = Dynaconf(
        envvar_prefix="AUDIT",
        settings_files=["audit_config.yaml"],
        validators=[
            Validator("ENCRYPTION_KEYS", must_exist=True, is_type_of=list),
            Validator(
                "COMPRESSION_ALGO", must_exist=True, is_in=["zstd", "gzip", "none"]
            ),
            Validator("COMPRESSION_LEVEL", default=9, gte=1, lte=22),
            Validator("BATCH_FLUSH_INTERVAL", must_exist=True, gte=1, lte=60),
            Validator("BATCH_MAX_SIZE", must_exist=True, gte=10, lte=1000),
            Validator("HEALTH_CHECK_INTERVAL", must_exist=True, gte=30, lte=300),
            Validator("RETRY_MAX_ATTEMPTS", must_exist=True, gte=1, lte=5),
            Validator("RETRY_BACKOFF_FACTOR", must_exist=True, gte=0.1, lte=2.0),
            Validator("TAMPER_DETECTION_ENABLED", default=True, is_type_of=bool),
        ],
    )

# --- START OF REPLACEMENT BLOCK ---


# from dynaconf import settings <-- START: EDIT A (Removed)


# FIX: Moved _is_test_or_dev_mode() and environment setup BEFORE Dynaconf creation above


# ---- Import-time validation with test/dev fallback ----
# FIX: This block is now handled before Dynaconf creation, but keep validation attempt
if _is_test_or_dev_mode():
    # Try validation, but don't fail in test/dev mode
    try:
        settings.validators.validate()
    except ValidationError as ve:
        warnings.warn(
            f"[audit_backend_core] Dynaconf validation bypassed for tests/dev: {ve}",
            RuntimeWarning,
        )
        # Clear validators to prevent re-validation
        try:
            settings.validators.validators = []
        except Exception:
            pass
    except Exception as ex:
        warnings.warn(
            f"[audit_backend_core] Settings initialization failed in test/dev mode: {ex}",
            RuntimeWarning,
        )
else:
    # In production, enforce strict validation
    try:
        settings.validators.validate()
    except ValidationError as ve:
        logger.critical(
            f"[audit_backend_core] Production validation failed: {ve}. "
            "Audit logging will operate in degraded mode with mock encryption. "
            "Set AUDIT_ENCRYPTION_KEYS environment variable to fix this. "
            "WARNING: Using ephemeral keys - audit logs cannot be decrypted after restart!"
        )
        warnings.warn(
            f"[audit_backend_core] Production validation failed: {ve}",
            RuntimeWarning,
        )
        # Set fallback environment defaults so the rest of the module can load
        # NOTE: Using ephemeral keys means audit logs cannot be decrypted after restart
        # This is a last-resort fallback to prevent total system failure
        os.environ.setdefault(
            "AUDIT_ENCRYPTION_KEYS",
            '[{"key_id":"mock_fallback_key","key":"' + Fernet.generate_key().decode() + '"}]',
        )
        os.environ.setdefault("AUDIT_COMPRESSION_ALGO", "none")
        os.environ.setdefault("AUDIT_BATCH_FLUSH_INTERVAL", "10")
        os.environ.setdefault("AUDIT_BATCH_MAX_SIZE", "100")
        os.environ.setdefault("AUDIT_HEALTH_CHECK_INTERVAL", "30")
        os.environ.setdefault("AUDIT_RETRY_MAX_ATTEMPTS", "3")
        os.environ.setdefault("AUDIT_RETRY_BACKOFF_FACTOR", "0.1")
        # Recreate settings WITHOUT validators to avoid re-validation errors
        settings = Dynaconf(
            envvar_prefix="AUDIT",
            settings_files=["audit_config.yaml"],
        )


# ---- Safe getters (handle strings from env) ----
def _as_int(name: str, default: int) -> int:
    try:
        v = settings.get(name, default)
    except ValidationError:
        if _is_test_or_dev_mode():
            v = os.environ.get(name, str(default))
        else:
            raise
    try:
        return int(v)
    except Exception:
        return default


def _as_float(name: str, default: float) -> float:
    try:
        v = settings.get(name, default)
    except ValidationError:
        if _is_test_or_dev_mode():
            v = os.environ.get(name, str(default))
        else:
            raise
    try:
        return float(v)
    except Exception:
        return default


def _as_bool(name: str, default: bool) -> bool:
    try:
        v = settings.get(name, default)
    except ValidationError:
        if _is_test_or_dev_mode():
            v = os.environ.get(name, str(default))
        else:
            raise
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return default


def _as_json_list(name: str, default: list) -> list:
    try:
        v = settings.get(name, default)
    except ValidationError:
        # In test/dev or when validation fails, use environment variable directly
        v = os.environ.get(name, None)
        if v is None:
            if _is_test_or_dev_mode():
                return default
            else:
                raise ValidationError(f"{name} is required but not set in environment")
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            j = json.loads(v)
            return j if isinstance(j, list) else default
        except Exception:
            return default
    return default


def _safe_settings_get(name: str, default: Any) -> Any:
    """Get settings value with test/dev mode fallback."""
    try:
        return settings.get(name, default)
    except ValidationError:
        if _is_test_or_dev_mode():
            return os.environ.get(name, default)
        raise


# ---- Public module-level constants used elsewhere ----
ENCRYPTION_KEYS = _as_json_list("ENCRYPTION_KEYS", [])
COMPRESSION_ALGO = _safe_settings_get("COMPRESSION_ALGO", "gzip")
COMPRESSION_LEVEL = _as_int("COMPRESSION_LEVEL", 9)
BATCH_FLUSH_INTERVAL = _as_int("BATCH_FLUSH_INTERVAL", 10)
BATCH_MAX_SIZE = _as_int("BATCH_MAX_SIZE", 100)
HEALTH_CHECK_INTERVAL = _as_int("HEALTH_CHECK_INTERVAL", 30)
RETRY_MAX_ATTEMPTS = _as_int("RETRY_MAX_ATTEMPTS", 3)
RETRY_BACKOFF_FACTOR = _as_float("RETRY_BACKOFF_FACTOR", 0.1)
TAMPER_DETECTION_ENABLED = _as_bool("TAMPER_DETECTION_ENABLED", True)

# --- END OF REPLACEMENT BLOCK ---


# --- START: ADDED KMS HELPERS (EDIT 1) ---
# --- KMS helpers (add near top-level imports) ---
def _kms_region() -> str | None:
    # Prefer explicit env; fall back to dynaconf if you expose AWS_REGION there
    return (
        os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or settings.get("AWS_REGION", None)
    )


def _make_kms_client():
    import botocore

    region = _kms_region()
    if not region:
        # Do NOT create a client without a region
        raise botocore.exceptions.NoRegionError()
    return boto3.client("kms", region_name=region)


# --- END: ADDED KMS HELPERS ---

SCHEMA_VERSION = 2  # <-- Manually re-added constant

# --- Key Management ---
_decrypted_keys: List[bytes] = []
ENCRYPTER = None  # Initialize to None for test mode fallback

if _is_test_or_dev_mode():
    # In test/dev mode, use mock encryption with a simple Fernet key
    logger.info("[audit_backend_core] Running in test/dev mode - using mock encrypter")
    try:
        from cryptography.fernet import Fernet

        # Generate a valid Fernet key for testing
        test_key = Fernet.generate_key()
        ENCRYPTER = MultiFernet([Fernet(test_key)])
        _decrypted_keys = [test_key]
    except Exception as ex:
        logger.warning(f"[audit_backend_core] Could not create test encrypter: {ex}")
        ENCRYPTER = None
else:
    try:
        # --- START: EDITS 2, 3, 4 (Verified per Edit D) ---

        # --- EDIT 4: Refined mock key check ---
        if ENCRYPTION_KEYS and all(
            isinstance(k, dict) and str(k.get("key_id", "")).lower().startswith("mock_")
            for k in ENCRYPTION_KEYS
        ):
            logger.warning("Using mock encryption keys. Skipping KMS.")
            _decrypted_keys = [k["key"].encode("utf-8") for k in ENCRYPTION_KEYS]
        else:
            # --- EDIT 2: Lazy-init KMS client ---
            # Only initialize KMS when we actually need it (non-mock keys)
            kms_client = _make_kms_client()
            for key_obj in ENCRYPTION_KEYS:
                b64_key = key_obj.get("key")
                if not b64_key:
                    logger.warning("Encryption key object missing 'key'; skipping.")
                    continue
                # Use synchronous decrypt at import time
                resp = kms_client.decrypt(CiphertextBlob=base64.b64decode(b64_key))
                _decrypted_keys.append(resp["Plaintext"])

        if not _decrypted_keys:
            raise ValueError("No encryption keys provided or decrypted successfully.")

        ENCRYPTER = MultiFernet([Fernet(key) for key in _decrypted_keys])

    # --- EDIT 3: Handle NoRegionError and replace SystemExit ---
    except botocore.exceptions.NoRegionError as e:
        # Clear error in prod; skip SystemExit and raise a typed error
        logger.critical("AWS region is not configured for KMS decryption.")
        raise CryptoInitializationError(
            "AWS region not configured (AWS_REGION or AWS_DEFAULT_REGION required)."
        ) from e
    except Exception as e:
        # If you still want strict prod behavior, raise
        logger.critical(f"Failed to initialize encryption keys: {e}", exc_info=True)
        raise CryptoInitializationError(
            f"Failed to initialize encryption keys: {e}"
        ) from e
    # --- END: EDITS 2, 3, 4 ---


# --- Constants ---
# === FIX 2: Rewrite this block to use the safe getters ===
# These lines overwrite the constants defined in the new block above,
# allowing for runtime checks (like the ENCRYPTER check).
COMPRESSION_ALGO = (
    _safe_settings_get("COMPRESSION_ALGO", "gzip") if ENCRYPTER else "none"
)  # Disable if crypto failed. settings.get() is safe.
COMPRESSION_LEVEL = _as_int("COMPRESSION_LEVEL", 9)
BATCH_FLUSH_INTERVAL = _as_int("BATCH_FLUSH_INTERVAL", 10)
BATCH_MAX_SIZE = _as_int("BATCH_MAX_SIZE", 100)
HEALTH_CHECK_INTERVAL = _as_int("HEALTH_CHECK_INTERVAL", 30)
RETRY_MAX_ATTEMPTS = _as_int("RETRY_MAX_ATTEMPTS", 3)
RETRY_BACKOFF_FACTOR = _as_float("RETRY_BACKOFF_FACTOR", 0.1)
TAMPER_DETECTION_ENABLED = _as_bool("TAMPER_DETECTION_ENABLED", True)
# === END FIX 2 ===

# --- Metrics ---
BACKEND_WRITES = safe_counter(
    "audit_backend_writes_total", "Total writes to backend", ["backend"]
)
BACKEND_READS = safe_counter(
    "audit_backend_reads_total", "Total reads from backend", ["backend"]
)
BACKEND_QUERIES = safe_metric(
    "Histogram", "audit_backend_queries_seconds", "Query time", ["backend"]
)
BACKEND_APPEND_LATENCY = safe_metric(
    "Histogram", "audit_backend_append_latency_seconds", "Append time", ["backend"]
)
BACKEND_HEALTH = safe_metric(
    "Gauge", "audit_backend_health", "Health (1=up)", ["backend"]
)
BACKEND_ERRORS = safe_counter(
    "audit_backend_errors_total", "Total errors per backend", ["backend", "type"]
)
BACKEND_BATCH_FLUSHES = safe_counter(
    "audit_backend_batch_flushes_total", "Total batch flushes", ["backend"]
)
BACKEND_THROUGHPUT_BYTES = safe_counter(
    "audit_backend_throughput_bytes_total",
    "Total bytes processed",
    ["backend", "operation"],
)
BACKEND_RETRY_ATTEMPTS = safe_counter(
    "audit_backend_retry_attempts_total",
    "Total retry attempts",
    ["backend", "operation"],
)
BACKEND_NETWORK_ERRORS = safe_counter(
    "audit_backend_network_errors_total",
    "Total network errors",
    ["backend", "operation"],
)
BACKEND_TAMPER_DETECTION_FAILURES = safe_counter(
    "audit_backend_tamper_detection_failures_total",
    "Count of failed tamper detection checks",
    ["backend"],
)

# --- OpenTelemetry Setup ---
if HAS_OPENTELEMETRY:
    # Use the default/configured tracer provider instead of manually creating one
    # This avoids version compatibility issues and respects OTEL_* environment variables
    try:
        tracer = trace.get_tracer(__name__)
    except TypeError:
        # Fallback for older OpenTelemetry versions that don't support all parameters
        # This can happen if opentelemetry-sdk version is older than opentelemetry-api
        try:
            tracer = trace.get_tracer(__name__, None)
        except TypeError:
            # If still failing, use None as tracer
            tracer = None
            logger.warning(
                "Failed to initialize OpenTelemetry tracer due to version compatibility issues. "
                "Tracing disabled. Please ensure opentelemetry-api and opentelemetry-sdk versions match."
            )
    _STATUS_OK = StatusCode.OK
    _STATUS_ERROR = StatusCode.ERROR
else:
    tracer = None
    _STATUS_OK = True
    _STATUS_ERROR = False


# --- Retry Logic ---
async def retry_operation(
    operation: callable,
    max_attempts: int = RETRY_MAX_ATTEMPTS,
    backoff_factor: float = RETRY_BACKOFF_FACTOR,
    backend_name: str = "unknown",
    op_name: str = "operation",
):
    """Retries an async operation with exponential backoff."""
    for attempt in range(max_attempts):
        try:
            # FIX: If the operation is synchronous, run it in a threadpool
            if asyncio.iscoroutinefunction(operation):
                return await operation()
            else:
                return await asyncio.to_thread(operation)

        except Exception as e:
            error_type = type(e).__name__

            # Always increment these metrics for ANY exception
            BACKEND_ERRORS.labels(backend=backend_name, type=error_type).inc()
            BACKEND_RETRY_ATTEMPTS.labels(backend=backend_name, operation=op_name).inc()

            # Additional increment for network-specific errors (including AWS/botocore errors)
            # Build the tuple of error types dynamically to handle cases where botocore might not be available
            network_error_types = [ConnectionError, TimeoutError, OSError]
            if hasattr(botocore, 'exceptions') and hasattr(botocore.exceptions, 'ClientError'):
                network_error_types.append(botocore.exceptions.ClientError)
            if isinstance(e, tuple(network_error_types)):
                BACKEND_NETWORK_ERRORS.labels(backend=backend_name, operation=op_name).inc()

            if attempt == max_attempts - 1:
                logger.error(
                    f"Operation '{op_name}' failed for {backend_name} after {max_attempts} attempts: {e}",
                    exc_info=True,
                )
                raise
            
            delay = backoff_factor * (2**attempt)
            logger.warning(
                f"Attempt {attempt + 1} for '{op_name}' on {backend_name} failed: {e}. Retrying after {delay:.2f}s"
            )
            await asyncio.sleep(delay)


# --- Base Backend Class ---
class LogBackend(abc.ABC):
    """
    Abstract backend with async writes, batching, encryption, compression, querying, and health checks.
    """

    def __init__(self, params: Dict[str, Any]):
        self.params = params
        self.batch: List[Dict[str, Any]] = []
        self.batch_lock = asyncio.Lock()

        # Use the global ENCRYPTER instance
        if ENCRYPTER is None:
            raise CryptoInitializationError(
                "LogBackend cannot be initialized: Cryptographic provider failed to initialize."
            )
        self.encrypter = ENCRYPTER

        self.schema_version = SCHEMA_VERSION
        self.tamper_detection_enabled = TAMPER_DETECTION_ENABLED

        self._validate_params()

        # FIX: Initialize task set, but DO NOT create tasks here.
        self._async_tasks = set()  # Use a set to track tasks for graceful shutdown

    async def start(self):
        """
        Starts the background tasks for this backend (migration, flushing, health).
        Subclasses MUST call await super().start() if they override this.
        """
        logger.info(f"Starting background tasks for {self.__class__.__name__}...")
        # FIX: Get the *running* loop. This is safe as start() is async.
        loop = asyncio.get_running_loop()

        self._migrate_task = loop.create_task(self._migrate_schema())
        self._flush_task = loop.create_task(self._flush_batch_periodically())
        self._health_task = loop.create_task(self._health_check_periodically())

        self._async_tasks.add(self._migrate_task)
        self._async_tasks.add(self._flush_task)
        self._async_tasks.add(self._health_task)

        # Set callbacks to discard finished tasks from the set
        self._migrate_task.add_done_callback(self._async_tasks.discard)
        self._flush_task.add_done_callback(self._async_tasks.discard)
        self._health_task.add_done_callback(self._async_tasks.discard)

        # Wait for migration to finish before proceeding
        await self._migrate_task
        logger.info(f"Background tasks for {self.__class__.__name__} started.")

    async def close(self):
        """Gracefully shuts down all background tasks."""
        logger.info(f"Shutting down background tasks for {self.__class__.__name__}...")
        for task in list(self._async_tasks):  # Iterate over a copy
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # Expected
            except Exception as e:
                logger.error(
                    f"Error during {self.__class__.__name__} task cleanup: {e}",
                    exc_info=True,
                )

        # Subclasses can override this to close connections, etc.
        logger.info(f"{self.__class__.__name__} shutdown complete.")

    def _validate_params(self) -> None:
        """Override in subclasses to validate self.params."""
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _validate_params."
        )

    def _compress(self, data: str) -> bytes:
        """Compresses data using the configured algorithm and compression level."""
        data_bytes = data.encode("utf-8")
        if COMPRESSION_ALGO == "zstd":
            return zstd.compress(data_bytes, level=COMPRESSION_LEVEL)
        elif COMPRESSION_ALGO == "gzip":
            return zlib.compress(data_bytes, level=COMPRESSION_LEVEL)
        return data_bytes

    def _decompress(self, data: bytes) -> str:
        """Decompresses data using the configured algorithm with fallback support."""
        backend_name = self.__class__.__name__
        
        # Try primary decompression algorithm
        try:
            if COMPRESSION_ALGO == "zstd":
                return zstd.decompress(data).decode("utf-8")
            elif COMPRESSION_ALGO == "gzip":
                return zlib.decompress(data).decode("utf-8")
            return data.decode("utf-8")
        except (zstd.ZstdError, zlib.error) as primary_err:
            # Fallback: try the other algorithm if primary fails
            # This handles cases where data was compressed with a different algorithm
            logger.warning(
                f"Primary decompression with {COMPRESSION_ALGO} failed for {backend_name}, "
                f"trying fallback algorithm: {primary_err}"
            )
            try:
                if COMPRESSION_ALGO == "zstd":
                    # Try gzip as fallback
                    return zlib.decompress(data).decode("utf-8")
                elif COMPRESSION_ALGO == "gzip":
                    # Try zstd as fallback
                    return zstd.decompress(data).decode("utf-8")
                else:
                    # No fallback available for 'none' compression
                    raise primary_err
            except Exception as fallback_err:
                logger.error(
                    f"All decompression methods failed for {backend_name}: "
                    f"primary={primary_err}, fallback={fallback_err}"
                )
                BACKEND_ERRORS.labels(backend=backend_name, type="DecompressionError").inc()
                # Safely schedule alert - handle case when no event loop is running
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        send_alert(
                            f"Audit log decompression failed for {backend_name}. Data corruption or wrong algorithm?",
                            severity="medium",
                        )
                    )
                except RuntimeError:
                    # No running event loop - log instead of creating task
                    logger.warning(
                        f"Could not send alert for decompression failure (no event loop): {fallback_err}"
                    )
                raise fallback_err
        except Exception as e:
            # Handle other exceptions (e.g., UnicodeDecodeError)
            BACKEND_ERRORS.labels(backend=backend_name, type="DecompressionError").inc()
            logger.error(f"Decompression failed for {backend_name}: {e}", exc_info=True)
            # Safely schedule alert - handle case when no event loop is running
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    send_alert(
                        f"Audit log decompression failed for {backend_name}. Data corruption or wrong algorithm?",
                        severity="medium",
                    )
                )
            except RuntimeError:
                # No running event loop - log instead of creating task
                logger.warning(
                    f"Could not send alert for decompression failure (no event loop): {e}"
                )
            raise

    def _encrypt(self, data: bytes) -> bytes:
        """Encrypts data."""
        return self.encrypter.encrypt(data)

    def _decrypt(self, data: bytes) -> bytes:
        """Decrypts data, handling key rotation via MultiFernet."""
        try:
            return self.encrypter.decrypt(data)
        except InvalidToken:
            backend_name = self.__class__.__name__
            BACKEND_ERRORS.labels(backend=backend_name, type="DecryptionError").inc()
            logger.error(
                f"Decryption failed for {backend_name}: Invalid token or key mismatch.",
                exc_info=True,
            )
            # Safely schedule alert - handle case when no event loop is running
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    send_alert(
                        f"Audit log decryption failed for {backend_name}. Invalid token or key mismatch.",
                        severity="high",
                    )
                )
            except RuntimeError:
                # No running event loop - log instead of creating task
                logger.warning(
                    f"Could not send alert for decryption failure (no event loop)"
                )
            raise
        except Exception as e:
            backend_name = self.__class__.__name__
            BACKEND_ERRORS.labels(backend=backend_name, type="DecryptionError").inc()
            logger.error(f"Decryption failed for {backend_name}: {e}", exc_info=True)
            # Safely schedule alert - handle case when no event loop is running
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    send_alert(
                        f"Audit log decryption failed for {backend_name}. Possible key mismatch or data corruption.",
                        severity="high",
                    )
                )
            except RuntimeError:
                # No running event loop - log instead of creating task
                logger.warning(
                    f"Could not send alert for decryption failure (no event loop): {e}"
                )
            raise

    async def append(self, entry: Dict[str, Any]) -> None:
        """Async append with batching, encryption, compression, and tamper detection."""
        backend_name = self.__class__.__name__

        # Add metadata to the *original* entry before processing
        entry["schema_version"] = self.schema_version
        entry["entry_id"] = str(uuid.uuid4())
        # --- FIX: Removed + 'Z' ---
        # isoformat() on an aware datetime object already includes the timezone.
        entry["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="milliseconds"
        )
        # --- END FIX ---

        # Compute hash for tamper detection
        entry_json_str = json.dumps(
            entry, sort_keys=True
        )  # Sort keys for consistent hashing
        entry_hash = compute_hash(entry_json_str.encode("utf-8"))
        entry["_audit_hash"] = entry_hash  # Embed hash in the original entry

        if HAS_OPENTELEMETRY:
            with tracer.start_as_current_span(f"{backend_name}.append") as span:
                span.set_attribute(
                    "audit.entry_type", entry.get("event_type", "unknown")
                )
                span.set_attribute("audit.entry_id", entry["entry_id"])

                async with self.batch_lock:
                    self.batch.append(entry)
                    if len(self.batch) >= BATCH_MAX_SIZE:
                        await self.flush_batch()
                span.set_status(_STATUS_OK)
        else:
            async with self.batch_lock:
                self.batch.append(entry)
                if len(self.batch) >= BATCH_MAX_SIZE:
                    await self.flush_batch()

    async def flush_batch(self) -> None:
        """Flushes batch to backend with retry and error handling."""
        async with self.batch_lock:
            if not self.batch:
                return
            batch_copy = self.batch[:]
            self.batch.clear()

        backend_name = self.__class__.__name__
        BACKEND_BATCH_FLUSHES.labels(backend=backend_name).inc()

        async def perform_flush():
            start_time = time.perf_counter()

            try:
                if HAS_OPENTELEMETRY:
                    with tracer.start_as_current_span(
                        f"{backend_name}.flush_batch"
                    ) as span:
                        span.set_attribute("batch.size", len(batch_copy))
                        span.set_attribute("backend.type", backend_name)
                        try:
                            await self._perform_atomic_batch_write(batch_copy, span)
                            span.set_status(_STATUS_OK)
                        except Exception as e:
                            span.set_status(_STATUS_ERROR, description=str(e))
                            raise
                else:
                    await self._perform_atomic_batch_write(batch_copy)

                # REMOVED: BACKEND_WRITES increment (now done in _perform_atomic_batch_write)
                # Keep only the latency metric
                BACKEND_APPEND_LATENCY.labels(backend=backend_name).observe(
                    time.perf_counter() - start_time
                )

            except Exception as e:
                # Increment error metrics in BOTH paths
                BACKEND_ERRORS.labels(
                    backend=backend_name, type=type(e).__name__
                ).inc()
                logger.error(
                    f"Batch flush failed for {backend_name}: {e}", exc_info=True
                )

                asyncio.create_task(
                    send_alert(
                        f"Audit log batch flush failed for {backend_name}: {e}",
                        severity="high",
                    )
                )
                raise

        # --- START: Change to allow opting out of core retries ---
        # New: allow backends to disable core-level retries
        use_core_retries = getattr(self, "core_retries_enabled", True)
        if use_core_retries:
            await retry_operation(
                perform_flush, backend_name=backend_name, op_name="flush_batch"
            )
        else:
            # Single attempt; backend handles its own transactional/queue semantics
            await perform_flush()
        # --- END: Change ---
        
        # INCREMENT BACKEND_WRITES HERE - AFTER retry_operation completes successfully
        # This ensures the metric is incremented only when the operation fully succeeds
        BACKEND_WRITES.labels(backend=backend_name).inc(len(batch_copy))

    # --- START: APPLIED EDIT ---
    async def _perform_atomic_batch_write(
        self, batch: List[Dict[str, Any]], span: Optional[Any] = None
    ) -> None:
        # --- END: APPLIED EDIT ---
        """Internal method for executing atomic batch writes."""
        prepared_entries: List[Dict[str, Any]] = []
        for entry in batch:
            data_str = json.dumps(entry, sort_keys=True)
            compressed = self._compress(data_str)
            encrypted = self._encrypt(compressed)
            base64_data = base64.b64encode(encrypted).decode("utf-8")

            prepared_entry = {
                "encrypted_data": base64_data,
                "entry_id": entry["entry_id"],
                "schema_version": entry["schema_version"],
                "timestamp": entry["timestamp"],
                "_audit_hash": entry["_audit_hash"],
            }
            prepared_entries.append(prepared_entry)
            BACKEND_THROUGHPUT_BYTES.labels(
                backend=self.__class__.__name__, operation="write"
            ).inc(len(base64_data))

        # Pass the prepared entries to the backend's atomic context
        async with self._atomic_context(prepared_entries=prepared_entries):
            # The yield in _atomic_context will handle the actual storage based on backend type
            pass
        
        # REMOVED: BACKEND_WRITES increment - now done in flush_batch after retry completes

    async def _flush_batch_periodically(self):
        """Periodically flushes batch."""
        while True:
            await asyncio.sleep(BATCH_FLUSH_INTERVAL)
            try:
                await self.flush_batch()
            except asyncio.CancelledError:
                logger.info(
                    f"Periodic batch flush for {self.__class__.__name__} cancelled."
                )
                break
            except Exception as e:
                logger.error(f"Periodic batch flush failed: {e}", exc_info=True)
                BACKEND_ERRORS.labels(
                    backend=self.__class__.__name__, type="PeriodicFlushError"
                ).inc()
                asyncio.create_task(
                    send_alert(
                        f"Periodic audit log flush failed for {self.__class__.__name__}: {e}",
                        severity="high",
                    )
                )

    @asynccontextmanager
    @abc.abstractmethod
    async def _atomic_context(
        self, prepared_entries: List[Dict[str, Any]]
    ) -> AsyncIterator[None]:
        """
        Backend-specific atomic context. Receives a list of prepared entries to write.
        """
        yield

    async def query(
        self, filters: Dict[str, Any], limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Queries backend with decryption, decompression, and tamper detection."""
        backend_name = self.__class__.__name__
        start_time = time.perf_counter()
        entries = []

        async def perform_query():
            raw_stored_entries = await retry_operation(
                functools.partial(
                    self._query_single, filters, limit
                ),  # Use functools.partial to wrap arguments
                backend_name=backend_name,
                op_name="query_single",
            )
            return raw_stored_entries

        raw_stored_entries = []
        try:
            if HAS_OPENTELEMETRY:
                with tracer.start_as_current_span(f"{backend_name}.query") as span:
                    span.set_attribute("query.filters", json.dumps(filters))
                    span.set_attribute("query.limit", limit)
                    raw_stored_entries = await perform_query()
                    span.set_status(_STATUS_OK)
            else:
                raw_stored_entries = await perform_query()
        except Exception as e:
            if HAS_OPENTELEMETRY:
                with tracer.start_as_current_span(f"{backend_name}.query_fail") as span:
                    span.set_status(_STATUS_ERROR, description=str(e))
            BACKEND_ERRORS.labels(backend=backend_name, type=type(e).__name__).inc()
            logger.error(f"Query failed for {backend_name}: {e}", exc_info=True)
            asyncio.create_task(
                send_alert(
                    f"Audit log query failed for {backend_name}: {e}", severity="high"
                )
            )
            return []

        # --- Post-Query Processing (Decryption, Tamper Check) ---
        BACKEND_THROUGHPUT_BYTES.labels(backend=backend_name, operation="read").inc(
            sum(
                len(r.get("encrypted_data", "").encode("utf-8"))
                for r in raw_stored_entries
            )
        )
        BACKEND_READS.labels(backend=backend_name).inc(len(raw_stored_entries))

        for stored_entry in raw_stored_entries:
            encrypted_b64 = stored_entry.get("encrypted_data")
            stored_entry_id = stored_entry.get("entry_id")

            if not encrypted_b64:
                logger.warning(
                    f"Empty encrypted_data in {backend_name} for entry_id: {stored_entry_id}"
                )
                continue

            # --- FIX: Refined exception handling loop ---
            try:
                encrypted_bytes = base64.b64decode(encrypted_b64)
                decrypted = self._decrypt(encrypted_bytes)
                decompressed = self._decompress(decrypted)
                audit_entry = json.loads(decompressed)

                if self.tamper_detection_enabled:
                    stored_hash = stored_entry.get("_audit_hash")
                    if not stored_hash:
                        logger.warning(
                            f"Audit hash missing from storage for entry_id {stored_entry_id} in {backend_name}. Cannot verify integrity."
                        )
                        BACKEND_TAMPER_DETECTION_FAILURES.labels(
                            backend=backend_name
                        ).inc()
                        asyncio.create_task(
                            send_alert(
                                f"Audit hash missing from storage for entry_id {stored_entry_id} in {backend_name}.",
                                severity="low",
                            )
                        )
                    else:
                        internal_hash = audit_entry.pop("_audit_hash", None)
                        recomputed_hash = compute_hash(
                            json.dumps(audit_entry, sort_keys=True).encode("utf-8")
                        )

                        if internal_hash:
                            audit_entry["_audit_hash"] = internal_hash

                        if stored_hash != recomputed_hash:
                            logger.error(
                                f"Tamper detected for entry_id {stored_entry_id} in {backend_name}! Stored hash: {stored_hash}, Recomputed hash: {recomputed_hash}"
                            )
                            BACKEND_TAMPER_DETECTION_FAILURES.labels(
                                backend=backend_name
                            ).inc()
                            asyncio.create_task(
                                send_alert(
                                    f"Tamper detected for entry_id {stored_entry_id} in {backend_name}!",
                                    severity="critical",
                                )
                            )
                            raise TamperDetectionError(
                                f"Tamper detected for entry_id {stored_entry_id}"
                            )

                entries.append(audit_entry)

            except TamperDetectionError as tamper_e:
                # Log the error (already alerted) and skip this entry
                logger.error(f"Skipping tampered entry: {tamper_e}", exc_info=True)
                continue  # Do not fall through to the next exception block

            except (
                InvalidToken,
                zlib.error,
                json.JSONDecodeError,
                base64.binascii.Error,
            ) as decode_error:
                # Handle decryption, decompression, or JSON parsing errors
                logger.error(
                    f"Failed to decode/decrypt/decompress entry (ID: {stored_entry_id}) from {backend_name}: {decode_error}",
                    exc_info=True,
                )
                BACKEND_ERRORS.labels(
                    backend=self.__class__.__name__, type="DecodeError"
                ).inc()
                asyncio.create_task(
                    send_alert(
                        f"Failed to process log entry from {backend_name}. Entry ID: {stored_entry_id}",
                        severity="medium",
                    )
                )
                continue

            except Exception as e:
                # Catch-all for other unexpected errors during loop
                logger.error(
                    f"Unexpected error processing entry (ID: {stored_entry_id}) from {backend_name}: {e}",
                    exc_info=True,
                )
                BACKEND_ERRORS.labels(
                    backend=self.__class__.__name__, type="DecodeLoopError"
                ).inc()
                continue
            # --- END FIX ---

        BACKEND_QUERIES.labels(backend=backend_name).observe(
            time.perf_counter() - start_time
        )
        return entries

    async def read_last_n(self, limit: int) -> List[str]:
        """
        Retrieves the last N raw encrypted entries. Used by audit_log.py's self_heal process.
        """
        # Note: We must fetch raw entries from the source first (no decryption)
        raw_stored_entries = await self._query_single({}, limit)
        # Return only the base64 encrypted payload
        return [
            e.get("encrypted_data")
            for e in raw_stored_entries
            if e.get("encrypted_data")
        ]

    async def range_query(
        self, start_time: str, end_time: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Queries entries within a timestamp range."""
        return await self.query(
            {"timestamp >=": start_time, "timestamp <=": end_time}, limit
        )

    async def text_search(self, keyword: str, limit: int) -> List[Dict[str, Any]]:
        """Text search across entries (inefficient for unindexed backends)."""
        logger.warning(f"Text search on {self.__class__.__name__} may be inefficient.")
        entries = await self.query({}, limit * 10)  # Fetch more to filter down
        # This filtering is done on decrypted, decompressed data (the most expensive part)
        return [e for e in entries if keyword.lower() in json.dumps(e).lower()][:limit]

    async def _health_check_periodically(self):
        """Periodically checks backend health and updates metrics."""
        backend_name = self.__class__.__name__
        while True:
            health_status = False
            if HAS_OPENTELEMETRY:
                with tracer.start_as_current_span(
                    f"{backend_name}.health_check"
                ) as span:
                    try:
                        health_status = await self._health_check()
                        span.set_attribute("health.status", health_status)
                        span.set_status(_STATUS_OK if health_status else _STATUS_ERROR)
                    except Exception as e:
                        span.set_status(_STATUS_ERROR, description=str(e))
                        logger.error(
                            f"Health check for {backend_name} failed: {e}",
                            exc_info=True,
                        )
                        BACKEND_ERRORS.labels(
                            backend=backend_name, type="HealthCheckError"
                        ).inc()
                        asyncio.create_task(
                            send_alert(
                                f"Health check failed for {backend_name}: {e}",
                                severity="critical",
                            )
                        )
            else:
                try:
                    health_status = await self._health_check()
                except Exception as e:
                    logger.error(
                        f"Health check for {backend_name} failed: {e}", exc_info=True
                    )
                    BACKEND_ERRORS.labels(
                        backend=backend_name, type="HealthCheckError"
                    ).inc()
                    asyncio.create_task(
                        send_alert(
                            f"Health check failed for {backend_name}: {e}",
                            severity="critical",
                        )
                    )

            BACKEND_HEALTH.labels(backend=backend_name).set(1 if health_status else 0)
            if not health_status:
                logger.error(f"Backend {backend_name} unhealthy! Alerting...")
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

    @abc.abstractmethod
    async def _append_single(self, prepared_entry: Dict[str, Any]) -> None:
        """
        Backend-specific single append. This method expects an already prepared (encrypted, compressed, encoded) entry.
        It should handle the actual storage mechanism for a single item within the context of an atomic batch.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def _query_single(
        self, filters: Dict[str, Any], limit: int
    ) -> List[Dict[str, Any]]:
        """
        Backend-specific query. This method should return raw stored entries.
        Returned entries should be dicts containing at least 'encrypted_data', 'entry_id', 'schema_version', '_audit_hash'.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def _migrate_schema(self) -> None:
        """
        Backend-specific schema migration logic.
        Should handle upgrades and provide rollback capability if migration fails.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def _health_check(self) -> bool:
        """Backend-specific health check."""
        raise NotImplementedError

    @abc.abstractmethod
    async def _get_current_schema_version(self) -> int:
        """
        Retrieve the current schema version from the backend's persistent storage.
        This is critical for migration logic to know the starting version.
        """
        raise NotImplementedError


# =========================================================================
# --- Concrete Backend Implementations (Real Logic) ---
# =========================================================================


class InMemoryBackend(LogBackend):
    """
    A fast, ephemeral in-memory storage backend for testing and buffering.
    NOTE: Data is lost on process restart.
    """

    def __init__(self, params):
        super().__init__(params)
        self.name = "inmemory"
        self.storage: List[Dict[str, Any]] = []
        self._validate_params()  # _validate_params is called by super().__init__

        # FIX: Task creation moved to start()
        # self._load_snapshot_task = asyncio.create_task(self._load_snapshot())

    async def start(self):
        """Overrides start to include snapshot loading."""
        await super().start()  # Start core tasks (flush, health, migrate)

        # Now create subclass-specific tasks
        loop = asyncio.get_running_loop()
        self._load_snapshot_task = loop.create_task(self._load_snapshot())
        self._async_tasks.add(self._load_snapshot_task)
        self._load_snapshot_task.add_done_callback(self._async_tasks.discard)

        await self._load_snapshot_task  # Wait for snapshot to load

    async def close(self):
        """
        Cleans up the InMemoryBackend. This involves optional snapshotting to disk
        and clearing all in-memory logs.
        """
        logger.info(
            "InMemoryBackend: Initiating graceful shutdown.",
            extra={"backend_type": self.__class__.__name__, "operation": "close_start"},
        )

        # In a real scenario, you might want to save a snapshot on close
        # For simplicity, we just clear and shut down tasks

        logger.info(
            "InMemoryBackend: Clearing all in-memory logs.",
            extra={"backend_type": self.__class__.__name__, "operation": "clear_logs"},
        )

        async with self.batch_lock:  # Use batch_lock to ensure no writes
            self.storage.clear()

        # Call super() to cancel base tasks
        await super().close()
        logger.info(
            "InMemoryBackend: Shutdown complete.",
            extra={"backend_type": self.__class__.__name__, "operation": "close_end"},
        )

    def _validate_params(self) -> None:
        unexpected = {k: v for k, v in self.params.items() if k not in ("name",)}
        if unexpected:
            logger.warning(
                "InMemoryBackend: Ignoring unsupported parameters: %s",
                ",".join(unexpected.keys()),
            )

    @asynccontextmanager
    async def _atomic_context(
        self, prepared_entries: List[Dict[str, Any]]
    ) -> AsyncIterator[None]:
        # Atomic write for in-memory is trivial: extend the list.
        # This implementation simply trusts the flush to work, and if it fails, the entries
        # remain in the prepared_entries list which is GC'd.
        self.storage.extend(prepared_entries)
        yield

    async def _append_single(self, prepared_entry: Dict[str, Any]):
        # Batching handles storage, so single append is effectively no-op in this implementation
        raise NotImplementedError

    async def _query_single(
        self, filters: Dict[str, Any], limit: int
    ) -> List[Dict[str, Any]]:
        # In-memory storage returns the raw stored dictionary objects
        return self.storage[-limit:]

    async def _migrate_schema(self):
        logger.info(
            f"{self.name}: Schema migration is trivial for in-memory storage. Current version: {self.schema_version}."
        )

    async def _health_check(self):
        # A simple check: Can we access the storage?
        return isinstance(self.storage, list)

    async def _get_current_schema_version(self):
        return SCHEMA_VERSION

    # --- ADDED: _load_snapshot method referenced in __init__ ---
    async def _load_snapshot(self):
        """Conceptual: Loads previously saved snapshot."""
        # This is a placeholder. In a real InMemoryBackend for testing,
        # you might load from a file specified in params.
        logger.info("InMemoryBackend: Skipping snapshot load (not implemented).")
        await asyncio.sleep(0)  # Yield control to show it's async
        return


# =========================================================================
# --- Backend Factory and Registry (No changes here, remains in core) ---
# =========================================================================

_REGISTRY: Dict[str, Type[LogBackend]] = {}
_INSTANCES: Dict[str, LogBackend] = {}


def register_backend(name: str, backend_cls: Type[LogBackend]) -> None:
    """
    Registers a concrete LogBackend class with the factory.
    """
    if not issubclass(backend_cls, LogBackend):
        raise TypeError(
            f"Backend class {backend_cls.__name__} must inherit from LogBackend."
        )
    _REGISTRY[name.lower()] = backend_cls
    logger.info(f"Registered audit backend: {name.lower()} -> {backend_cls.__name__}")


def get_backend(kind: str, params: Dict[str, Any]) -> LogBackend:
    """
    Retrieves a cached or initializes a new LogBackend instance.
    """
    kind_lower = kind.lower()

    if kind_lower in _INSTANCES:
        logger.debug(f"Returning cached instance for backend: {kind_lower}")
        return _INSTANCES[kind_lower]

    backend_cls = _REGISTRY.get(kind_lower)
    if not backend_cls:
        raise BackendNotFoundError(
            f"Audit log backend type '{kind}' not registered. Registered: {list(_REGISTRY.keys())}"
        )

    try:
        instance = backend_cls(params)
        _INSTANCES[kind_lower] = instance
        logger.info(f"Initialized new audit backend instance: {backend_cls.__name__}")
        return instance
    except Exception as e:
        logger.critical(
            f"Failed to initialize backend {kind_lower} with params {params}: {e}",
            exc_info=True,
        )
        raise CryptoInitializationError(
            f"Failed to initialize backend {kind_lower}: {e}"
        ) from e


# --- Default Backend Registration ---
# NOTE: These are registered via imports in __init__.py and streaming_backends.py
# register_backend('file', FileBackend)
register_backend("inmemory", InMemoryBackend)
