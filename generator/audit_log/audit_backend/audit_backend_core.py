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
from cryptography.fernet import Fernet, InvalidToken, MultiFernet  # <-- FIX: ADD InvalidToken HERE
from prometheus_client import REGISTRY  # <-- ADDED
from prometheus_client import Counter, Gauge, Histogram

# OpenTelemetry imports (guarded)
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
    from opentelemetry.trace import StatusCode

    HAS_OPENTELEMETRY = True
except ImportError:
    trace = None
    tracer = None
    StatusCode = None
    HAS_OPENTELEMETRY = False

# Local utility module (assumed to exist outside audit_backends package)
try:
    from audit_utils import compute_hash, send_alert
except ImportError:
    logging.warning(
        "audit_utils.py not found. Tamper detection and alerting features will be unavailable."
    )

    if "compute_hash" not in globals():
        import hashlib

        def compute_hash(data: bytes) -> str:
            """
            Stable SHA-256 hash used for tamper-evident chaining.
            """
            h = hashlib.sha256()
            h.update(data)
            return h.hexdigest()

    if "send_alert" not in globals():

        async def send_alert(
            message: str, severity: str = "warning"
        ) -> None:  # <-- Kept async to match usage
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


# --- START: ADDED HELPER FUNCTION (Modified to use universal safe_metric) ---
def safe_metric(metric_type, name, description, labelnames=()):
    """Return existing metric if already registered, otherwise create a new one."""
    try:
        # Prometheus stores metrics by name (and its sub-components)
        return REGISTRY._names_to_collectors[name]
    except KeyError:
        # Create metric based on type
        if metric_type == "Counter":
            return Counter(name, description, labelnames)
        elif metric_type == "Gauge":
            return Gauge(name, description, labelnames)
        elif metric_type == "Histogram":
            return Histogram(name, description, labelnames)
        else:
            raise ValueError(f"Unknown metric type: {metric_type}")


# Retaining safe_counter alias for backward compatibility with previous steps
def safe_counter(name, description, labelnames=()):
    return safe_metric("Counter", name, description, labelnames)


# --- END: ADDED HELPER FUNCTION ---

# Configuration management
from dynaconf import Dynaconf
from dynaconf.validator import ValidationError, Validator  # <-- ADDED ValidationError

logger = logging.getLogger(__name__)


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
# Using Dynaconf for environment-based configuration
settings = Dynaconf(
    envvar_prefix="AUDIT",
    settings_files=["audit_config.yaml"],
    validators=[
        Validator("ENCRYPTION_KEYS", must_exist=True, is_type_of=list),
        Validator("COMPRESSION_ALGO", must_exist=True, is_in=["zstd", "gzip", "none"]),
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


def _is_test_or_dev_mode() -> bool:
    # pytest sets PYTEST_CURRENT_TEST; we also respect a simple dev flag
    return bool(os.getenv("PYTEST_CURRENT_TEST") or os.getenv("AUDIT_LOG_DEV_MODE") == "true")


# ---- Import-time validation with test/dev fallback ----
try:
    # Your existing validators are attached via dynaconf settings
    settings.validators.validate()
except ValidationError as e:
    if _is_test_or_dev_mode():
        warnings.warn(
            f"[audit_backend_core] Dynaconf validation bypassed for tests/dev: {e}",
            RuntimeWarning,
        )
        # Provide safe defaults so subsequent attribute access doesn't re-trigger validation
        settings.setdefault(
            "ENCRYPTION_KEYS",
            [
                {
                    "key_id": "mock_key_1",
                    "key": "hYnO2bq3m0yqgqz5WJt9j3ZCsb3dC-5H9qv1Hj4XGxw=",
                }
            ],
        )
        settings.setdefault("COMPRESSION_ALGO", "gzip")
        settings.setdefault("COMPRESSION_LEVEL", 9)
        settings.setdefault("BATCH_FLUSH_INTERVAL", 10)
        settings.setdefault("BATCH_MAX_SIZE", 100)
        settings.setdefault("HEALTH_CHECK_INTERVAL", 30)
        settings.setdefault("RETRY_MAX_ATTEMPTS", 3)
        settings.setdefault("RETRY_BACKOFF_FACTOR", 0.1)
        settings.setdefault("TAMPER_DETECTION_ENABLED", True)

        # Prevent further strict re-validations during this process
        try:
            settings.validators.validators = []
        except Exception:
            pass
    else:
        # In real runtime (not tests/dev), honor strict validation
        raise


# ---- Safe getters (handle strings from env) ----
def _as_int(name: str, default: int) -> int:
    v = settings.get(name, default)
    try:
        return int(v)
    except Exception:
        return default


def _as_float(name: str, default: float) -> float:
    v = settings.get(name, default)
    try:
        return float(v)
    except Exception:
        return default


def _as_bool(name: str, default: bool) -> bool:
    v = settings.get(name, default)
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return default


def _as_json_list(name: str, default: list) -> list:
    v = settings.get(name, default)
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            j = json.loads(v)
            return j if isinstance(j, list) else default
        except Exception:
            return default
    return default


# ---- Public module-level constants used elsewhere ----
ENCRYPTION_KEYS = _as_json_list("ENCRYPTION_KEYS", [])
COMPRESSION_ALGO = settings.get("COMPRESSION_ALGO", "gzip")
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
    raise CryptoInitializationError(f"Failed to initialize encryption keys: {e}") from e
# --- END: EDITS 2, 3, 4 ---


# --- Constants ---
# === FIX 2: Rewrite this block to use the safe getters ===
# These lines overwrite the constants defined in the new block above,
# allowing for runtime checks (like the ENCRYPTER check).
COMPRESSION_ALGO = (
    settings.get("COMPRESSION_ALGO", "gzip") if ENCRYPTER else "none"
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
BACKEND_WRITES = safe_counter("audit_backend_writes_total", "Total writes to backend", ["backend"])
BACKEND_READS = safe_counter("audit_backend_reads_total", "Total reads from backend", ["backend"])
BACKEND_QUERIES = safe_metric(
    "Histogram", "audit_backend_queries_seconds", "Query time", ["backend"]
)
BACKEND_APPEND_LATENCY = safe_metric(
    "Histogram", "audit_backend_append_latency_seconds", "Append time", ["backend"]
)
BACKEND_HEALTH = safe_metric("Gauge", "audit_backend_health", "Health (1=up)", ["backend"])
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
    provider = TracerProvider()
    processor = SimpleSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer(__name__)
    _STATUS_OK = StatusCode.OK
    _STATUS_ERROR = StatusCode.ERROR
else:
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

        except (
            botocore.exceptions.ClientError,  # AWS related errors
            ConnectionError,
            TimeoutError,  # Generic network/timeout errors
            OSError,  # OS-level I/O errors
            # Add specific exceptions from client libraries as needed, e.g.,
            # aiohttp.ClientError, asyncpg.exceptions.PostgresError,
            # aiokafka.errors.KafkaError, sqlite3.Error
        ) as e:
            BACKEND_NETWORK_ERRORS.labels(backend=backend_name, operation=op_name).inc()
            error_type = "network_error"

            BACKEND_ERRORS.labels(backend=backend_name, type=error_type).inc()
            BACKEND_RETRY_ATTEMPTS.labels(backend=backend_name, operation=op_name).inc()

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

        except Exception as e:
            error_type = type(e).__name__

            BACKEND_ERRORS.labels(backend=backend_name, type=error_type).inc()
            BACKEND_RETRY_ATTEMPTS.labels(backend=backend_name, operation=op_name).inc()

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
        raise NotImplementedError(f"{self.__class__.__name__} must implement _validate_params.")

    def _compress(self, data: str) -> bytes:
        """Compresses data using the configured algorithm and compression level."""
        data_bytes = data.encode("utf-8")
        if COMPRESSION_ALGO == "zstd":
            return zstd.compress(data_bytes, level=COMPRESSION_LEVEL)
        elif COMPRESSION_ALGO == "gzip":
            return zlib.compress(data_bytes, level=COMPRESSION_LEVEL)
        return data_bytes

    def _decompress(self, data: bytes) -> str:
        """Decompresses data using the configured algorithm."""
        try:
            if COMPRESSION_ALGO == "zstd":
                return zstd.decompress(data).decode("utf-8")
            elif COMPRESSION_ALGO == "gzip":
                return zlib.decompress(data).decode("utf-8")
            return data.decode("utf-8")
        except Exception as e:
            backend_name = self.__class__.__name__
            BACKEND_ERRORS.labels(backend=backend_name, type="DecompressionError").inc()
            logger.error(f"Decompression failed for {backend_name}: {e}", exc_info=True)
            asyncio.create_task(
                send_alert(
                    f"Audit log decompression failed for {backend_name}. Data corruption or wrong algorithm?",
                    severity="medium",
                )
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
            asyncio.create_task(
                send_alert(
                    f"Audit log decryption failed for {backend_name}. Invalid token or key mismatch.",
                    severity="high",
                )
            )
            raise
        except Exception as e:
            backend_name = self.__class__.__name__
            BACKEND_ERRORS.labels(backend=backend_name, type="DecryptionError").inc()
            logger.error(f"Decryption failed for {backend_name}: {e}", exc_info=True)
            asyncio.create_task(
                send_alert(
                    f"Audit log decryption failed for {backend_name}. Possible key mismatch or data corruption.",
                    severity="high",
                )
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
        entry_json_str = json.dumps(entry, sort_keys=True)  # Sort keys for consistent hashing
        entry_hash = compute_hash(entry_json_str.encode("utf-8"))
        entry["_audit_hash"] = entry_hash  # Embed hash in the original entry

        if HAS_OPENTELEMETRY:
            with tracer.start_as_current_span(f"{backend_name}.append") as span:
                span.set_attribute("audit.entry_type", entry.get("event_type", "unknown"))
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
            if HAS_OPENTELEMETRY:
                with tracer.start_as_current_span(f"{backend_name}.flush_batch") as span:
                    span.set_attribute("batch.size", len(batch_copy))
                    span.set_attribute("backend.type", backend_name)
                    try:
                        await self._perform_atomic_batch_write(batch_copy, span)

                        BACKEND_WRITES.labels(backend=backend_name).inc(len(batch_copy))
                        BACKEND_APPEND_LATENCY.labels(backend=backend_name).observe(
                            time.perf_counter() - start_time
                        )
                        span.set_status(_STATUS_OK)
                    except Exception as e:
                        BACKEND_ERRORS.labels(backend=backend_name, type=type(e).__name__).inc()
                        logger.error(f"Batch flush failed for {backend_name}: {e}", exc_info=True)
                        span.set_status(_STATUS_ERROR, description=str(e))
                        asyncio.create_task(
                            send_alert(
                                f"Audit log batch flush failed for {backend_name}: {e}",
                                severity="high",
                            )
                        )
                        raise
            else:
                await self._perform_atomic_batch_write(batch_copy)
                BACKEND_WRITES.labels(backend=backend_name).inc(len(batch_copy))
                BACKEND_APPEND_LATENCY.labels(backend=backend_name).observe(
                    time.perf_counter() - start_time
                )

        # --- START: Change to allow opting out of core retries ---
        # New: allow backends to disable core-level retries
        use_core_retries = getattr(self, "core_retries_enabled", True)
        if use_core_retries:
            await retry_operation(perform_flush, backend_name=backend_name, op_name="flush_batch")
        else:
            # Single attempt; backend handles its own transactional/queue semantics
            await perform_flush()
        # --- END: Change ---

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
            BACKEND_THROUGHPUT_BYTES.labels(backend=self.__class__.__name__, operation="write").inc(
                len(base64_data)
            )

        # Pass the prepared entries to the backend's atomic context
        async with self._atomic_context(prepared_entries=prepared_entries):
            # The yield in _atomic_context will handle the actual storage based on backend type
            pass

    async def _flush_batch_periodically(self):
        """Periodically flushes batch."""
        while True:
            await asyncio.sleep(BATCH_FLUSH_INTERVAL)
            try:
                await self.flush_batch()
            except asyncio.CancelledError:
                logger.info(f"Periodic batch flush for {self.__class__.__name__} cancelled.")
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
    async def _atomic_context(self, prepared_entries: List[Dict[str, Any]]) -> AsyncIterator[None]:
        """
        Backend-specific atomic context. Receives a list of prepared entries to write.
        """
        yield

    async def query(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
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
                send_alert(f"Audit log query failed for {backend_name}: {e}", severity="high")
            )
            return []

        # --- Post-Query Processing (Decryption, Tamper Check) ---
        BACKEND_THROUGHPUT_BYTES.labels(backend=backend_name, operation="read").inc(
            sum(len(r.get("encrypted_data", "").encode("utf-8")) for r in raw_stored_entries)
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
                        BACKEND_TAMPER_DETECTION_FAILURES.labels(backend=backend_name).inc()
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
                            BACKEND_TAMPER_DETECTION_FAILURES.labels(backend=backend_name).inc()
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
                BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="DecodeError").inc()
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
                BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="DecodeLoopError").inc()
                continue
            # --- END FIX ---

        BACKEND_QUERIES.labels(backend=backend_name).observe(time.perf_counter() - start_time)
        return entries

    async def read_last_n(self, limit: int) -> List[str]:
        """
        Retrieves the last N raw encrypted entries. Used by audit_log.py's self_heal process.
        """
        # Note: We must fetch raw entries from the source first (no decryption)
        raw_stored_entries = await self._query_single({}, limit)
        # Return only the base64 encrypted payload
        return [e.get("encrypted_data") for e in raw_stored_entries if e.get("encrypted_data")]

    async def range_query(self, start_time: str, end_time: str, limit: int) -> List[Dict[str, Any]]:
        """Queries entries within a timestamp range."""
        return await self.query({"timestamp >=": start_time, "timestamp <=": end_time}, limit)

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
                with tracer.start_as_current_span(f"{backend_name}.health_check") as span:
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
                        BACKEND_ERRORS.labels(backend=backend_name, type="HealthCheckError").inc()
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
                    logger.error(f"Health check for {backend_name} failed: {e}", exc_info=True)
                    BACKEND_ERRORS.labels(backend=backend_name, type="HealthCheckError").inc()
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
    async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
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
    async def _atomic_context(self, prepared_entries: List[Dict[str, Any]]) -> AsyncIterator[None]:
        # Atomic write for in-memory is trivial: extend the list.
        # This implementation simply trusts the flush to work, and if it fails, the entries
        # remain in the prepared_entries list which is GC'd.
        self.storage.extend(prepared_entries)
        yield

    async def _append_single(self, prepared_entry: Dict[str, Any]):
        # Batching handles storage, so single append is effectively no-op in this implementation
        raise NotImplementedError

    async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
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
        raise TypeError(f"Backend class {backend_cls.__name__} must inherit from LogBackend.")
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
        raise CryptoInitializationError(f"Failed to initialize backend {kind_lower}: {e}") from e


# --- Default Backend Registration ---
# NOTE: These are registered via imports in __init__.py and streaming_backends.py
# register_backend('file', FileBackend)
register_backend("inmemory", InMemoryBackend)
