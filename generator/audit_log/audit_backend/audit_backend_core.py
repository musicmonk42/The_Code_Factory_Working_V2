# audit_backends/audit_backend_core.py
import abc
import asyncio
import base64
import datetime
import json
import logging
import os
import time
import uuid
import zlib
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Type, Callable, AsyncIterator

import boto3  # For KMS
import botocore.exceptions
import zstandard as zstd
from cryptography.fernet import Fernet, MultiFernet
from cryptography.exceptions import InvalidToken
from prometheus_client import Counter, Gauge, Histogram
import aiofiles
import tempfile
import stat

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
    logging.warning("audit_utils.py not found. Tamper detection and alerting features will be unavailable.")

    def compute_hash(data: bytes) -> str:
        """Placeholder for a hash computation function."""
        import hashlib
        return hashlib.sha256(data).hexdigest()

    async def send_alert(message: str, severity: str = "critical"):
        """Placeholder for sending alerts."""
        logging.error(f"ALERT [{severity.upper()}]: {message}")


# Configuration management
from dynaconf import Dynaconf
from dynaconf.validator import Validator

logger = logging.getLogger(__name__)

# --- Configuration and Secrets Management ---
# Using Dynaconf for environment-based configuration
settings = Dynaconf(
    envvar_prefix="AUDIT",
    settings_files=["audit_config.yaml"],
    validators=[
        Validator("ENCRYPTION_KEYS", must_exist=True, is_type_of=list, of_type=str),
        Validator("COMPRESSION_ALGO", must_exist=True, is_in=["zstd", "gzip", "none"]),
        Validator("COMPRESSION_LEVEL", default=9, gte=1, lte=22),
        Validator("BATCH_FLUSH_INTERVAL", must_exist=True, gte=1, lte=60),
        Validator("BATCH_MAX_SIZE", must_exist=True, gte=10, lte=1000),
        Validator("HEALTH_CHECK_INTERVAL", must_exist=True, gte=30, lte=300),
        Validator("RETRY_MAX_ATTEMPTS", must_exist=True, gte=1, lte=5),
        Validator("RETRY_BACKOFF_FACTOR", must_exist=True, gte=0.1, lte=2.0),
        Validator("TAMPER_DETECTION_ENABLED", default=True, is_type_of=bool),
    ]
)

# Validate configuration at startup
try:
    settings.validators.validate()
except Exception as e:
    logger.critical(f"Configuration validation failed: {e}")
    raise SystemExit(1)

# --- Key Management ---
_decrypted_keys: List[bytes] = []
try:
    kms_client = boto3.client("kms")
    for b64_key in settings.ENCRYPTION_KEYS:
        plaintext_key = kms_client.decrypt(
            CiphertextBlob=base64.b64decode(b64_key)
        )["Plaintext"]
        _decrypted_keys.append(plaintext_key)

    if not _decrypted_keys:
        raise ValueError("No encryption keys provided or decrypted successfully.")

    ENCRYPTER = MultiFernet([Fernet(key) for key in _decrypted_keys])

except Exception as e:
    logger.critical(f"Failed to fetch and initialize encryption keys from KMS: {e}")
    raise SystemExit(1)

# --- Constants ---
SCHEMA_VERSION = 2
COMPRESSION_ALGO = settings.COMPRESSION_ALGO
COMPRESSION_LEVEL = settings.COMPRESSION_LEVEL
BATCH_FLUSH_INTERVAL = settings.BATCH_FLUSH_INTERVAL
BATCH_MAX_SIZE = settings.BATCH_MAX_SIZE
HEALTH_CHECK_INTERVAL = settings.HEALTH_CHECK_INTERVAL
RETRY_MAX_ATTEMPTS = settings.RETRY_MAX_ATTEMPTS
RETRY_BACKOFF_FACTOR = settings.RETRY_BACKOFF_FACTOR
TAMPER_DETECTION_ENABLED = settings.TAMPER_DETECTION_ENABLED

# --- Metrics ---
BACKEND_WRITES = Counter("audit_backend_writes_total", "Total writes to backend", ["backend"])
BACKEND_READS = Counter("audit_backend_reads_total", "Total reads from backend", ["backend"])
BACKEND_QUERIES = Histogram("audit_backend_queries_seconds", "Query time", ["backend"])
BACKEND_APPEND_LATENCY = Histogram("audit_backend_append_latency_seconds", "Append time", ["backend"])
BACKEND_HEALTH = Gauge("audit_backend_health", "Health (1=up)", ["backend"])
BACKEND_ERRORS = Counter("audit_backend_errors_total", "Total errors per backend", ["backend", "type"])
BACKEND_BATCH_FLUSHES = Counter("audit_backend_batch_flushes_total", "Total batch flushes", ["backend"])
BACKEND_THROUGHPUT_BYTES = Counter("audit_backend_throughput_bytes_total", "Total bytes processed", ["backend", "operation"])
BACKEND_RETRY_ATTEMPTS = Counter("audit_backend_retry_attempts_total", "Total retry attempts", ["backend", "operation"])
BACKEND_NETWORK_ERRORS = Counter("audit_backend_network_errors_total", "Total network errors", ["backend", "operation"])
BACKEND_TAMPER_DETECTION_FAILURES = Counter("audit_backend_tamper_detection_failures_total", "Count of failed tamper detection checks", ["backend"])

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

# --- Retry Logic ---
async def retry_operation(operation: callable, max_attempts: int = RETRY_MAX_ATTEMPTS, backoff_factor: float = RETRY_BACKOFF_FACTOR, backend_name: str = "unknown", op_name: str = "operation"):
    """Retries an async operation with exponential backoff."""
    for attempt in range(max_attempts):
        try:
            return await operation()
        except (botocore.exceptions.ClientError,  # AWS related errors
                ConnectionError, TimeoutError,     # Generic network/timeout errors
                OSError,                           # OS-level I/O errors
                # Add specific exceptions from client libraries as needed, e.g.,
                # aiohttp.ClientError, asyncpg.exceptions.PostgresError,
                # aiokafka.errors.KafkaError, sqlite3.Error
                ) as e:
            BACKEND_NETWORK_ERRORS.labels(backend=backend_name, operation=op_name).inc()
            error_type = "network_error"
        except Exception as e:
            error_type = type(e).__name__

        BACKEND_ERRORS.labels(backend=backend_name, type=error_type).inc()
        BACKEND_RETRY_ATTEMPTS.labels(backend=backend_name, operation=op_name).inc()

        if attempt == max_attempts - 1:
            logger.error(f"Operation '{op_name}' failed for {backend_name} after {max_attempts} attempts: {e}", exc_info=True)
            raise
        delay = backoff_factor * (2 ** attempt)
        logger.warning(f"Attempt {attempt + 1} for '{op_name}' on {backend_name} failed: {e}. Retrying after {delay:.2f}s")
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
        self.encrypter = ENCRYPTER
        self.schema_version = SCHEMA_VERSION
        self.tamper_detection_enabled = TAMPER_DETECTION_ENABLED

        self._validate_params()
        # Schedule these as tasks immediately upon initialization
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop() 

        loop.create_task(self._migrate_schema())
        loop.create_task(self._flush_batch_periodically())
        loop.create_task(self._health_check_periodically())

    def _validate_params(self):
        """Validates backend-specific parameters. Must be implemented by subclasses."""
        pass

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
            asyncio.create_task(send_alert(f"Audit log decompression failed for {backend_name}. Data corruption or wrong algorithm?", severity="medium"))
            raise

    def _encrypt(self, data: bytes) -> bytes:
        """Encrypts data."""
        return self.encrypter.encrypt(data)

    def _decrypt(self, data: bytes) -> bytes:
        """Decrypts data, handling key rotation via MultiFernet."""
        try:
            return self.encrypter.decrypt(data)
        except InvalidToken as e:
            backend_name = self.__class__.__name__
            BACKEND_ERRORS.labels(backend=backend_name, type="DecryptionError").inc()
            logger.error(f"Decryption failed for {backend_name}: Invalid token or key mismatch.", exc_info=True)
            asyncio.create_task(send_alert(f"Audit log decryption failed for {backend_name}. Invalid token or key mismatch.", severity="high"))
            raise
        except Exception as e:
            backend_name = self.__class__.__name__
            BACKEND_ERRORS.labels(backend=backend_name, type="DecryptionError").inc()
            logger.error(f"Decryption failed for {backend_name}: {e}", exc_info=True)
            asyncio.create_task(send_alert(f"Audit log decryption failed for {backend_name}. Possible key mismatch or data corruption.", severity="high"))
            raise

    async def append(self, entry: Dict[str, Any]) -> None:
        """Async append with batching, encryption, compression, and tamper detection."""
        backend_name = self.__class__.__name__
        
        # Add metadata to the *original* entry before processing
        entry["schema_version"] = self.schema_version
        entry["entry_id"] = str(uuid.uuid4())
        entry["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds') + 'Z'

        # Compute hash for tamper detection
        entry_json_str = json.dumps(entry, sort_keys=True) # Sort keys for consistent hashing
        entry_hash = compute_hash(entry_json_str.encode("utf-8"))
        entry["_audit_hash"] = entry_hash # Embed hash in the original entry

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
                        BACKEND_APPEND_LATENCY.labels(backend=backend_name).observe(time.perf_counter() - start_time)
                        span.set_status(_STATUS_OK)
                    except Exception as e:
                        BACKEND_ERRORS.labels(backend=backend_name, type=type(e).__name__).inc()
                        logger.error(f"Batch flush failed for {backend_name}: {e}", exc_info=True)
                        span.set_status(_STATUS_ERROR, description=str(e))
                        asyncio.create_task(send_alert(f"Audit log batch flush failed for {backend_name}: {e}", severity="high"))
                        raise
            else:
                await self._perform_atomic_batch_write(batch_copy)
                BACKEND_WRITES.labels(backend=backend_name).inc(len(batch_copy))
                BACKEND_APPEND_LATENCY.labels(backend=backend_name).observe(time.perf_counter() - start_time)

        await retry_operation(perform_flush, backend_name=backend_name, op_name="flush_batch")

    async def _perform_atomic_batch_write(self, batch: List[Dict[str, Any]], span: Optional[trace.Span] = None) -> None:
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
                "_audit_hash": entry["_audit_hash"]
            }
            prepared_entries.append(prepared_entry)
            BACKEND_THROUGHPUT_BYTES.labels(backend=self.__class__.__name__, operation="write").inc(len(base64_data))

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
            except Exception as e:
                logger.error(f"Periodic batch flush failed: {e}", exc_info=True)
                BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="PeriodicFlushError").inc()
                asyncio.create_task(send_alert(f"Periodic audit log flush failed for {self.__class__.__name__}: {e}", severity="high"))


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
                lambda: self._query_single(filters, limit),
                backend_name=backend_name, op_name="query_single"
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
            asyncio.create_task(send_alert(f"Audit log query failed for {backend_name}: {e}", severity="high"))
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
                logger.warning(f"Empty encrypted_data in {backend_name} for entry_id: {stored_entry_id}")
                continue

            try:
                encrypted_bytes = base64.b64decode(encrypted_b64)
                decrypted = self._decrypt(encrypted_bytes)
                decompressed = self._decompress(decrypted)
                audit_entry = json.loads(decompressed)

                # Tamper detection
                if self.tamper_detection_enabled:
                    if "_audit_hash" not in audit_entry:
                        logger.warning(f"Audit hash missing for entry_id {stored_entry_id} in {backend_name}. Cannot verify integrity.")
                        BACKEND_TAMPER_DETECTION_FAILURES.labels(backend=backend_name).inc()
                        asyncio.create_task(send_alert(f"Audit hash missing for entry_id {stored_entry_id} in {backend_name}.", severity="low"))
                    else:
                        # CRITICAL: pop _audit_hash before hashing for verification!
                        original_hash = audit_entry.pop("_audit_hash")
                        recomputed_hash = compute_hash(json.dumps(audit_entry, sort_keys=True).encode("utf-8"))
                        
                        # Add audit_hash back for display/further processing
                        audit_entry["_audit_hash"] = original_hash 

                        if original_hash != recomputed_hash:
                            logger.error(f"Tamper detected for entry_id {stored_entry_id} in {backend_name}! Original hash: {original_hash}, Recomputed hash: {recomputed_hash}")
                            BACKEND_TAMPER_DETECTION_FAILURES.labels(backend=backend_name).inc()
                            asyncio.create_task(send_alert(f"Tamper detected for entry_id {stored_entry_id} in {backend_name}!", severity="critical"))
                            raise TamperDetectionError(f"Tamper detected for entry_id {stored_entry_id}")
                
                entries.append(audit_entry)

            except (TamperDetectionError, Exception) as decode_error:
                logger.error(f"Failed to decode/decrypt/decompress entry (ID: {stored_entry_id}) from {backend_name}: {decode_error}", exc_info=True)
                BACKEND_ERRORS.labels(backend=backend_name, type="DecodeError").inc()
                asyncio.create_task(send_alert(f"Failed to process log entry from {backend_name}. Entry ID: {stored_entry_id}", severity="medium"))
                continue

        BACKEND_QUERIES.labels(backend=backend_name).observe(time.perf_counter() - start_time)
        return entries

    async def read_last_n(self, limit: int) -> List[str]:
        """
        Retrieves the last N raw encrypted entries. Used by audit_log.py's self_heal process.
        """
        raw_stored_entries = await self._query_single({}, limit)
        # Return only the base64 encrypted payload
        return [e.get("encrypted_data") for e in raw_stored_entries if e.get("encrypted_data")]


    async def range_query(self, start_time: str, end_time: str, limit: int) -> List[Dict[str, Any]]:
        """Queries entries within a timestamp range."""
        return await self.query({"timestamp >=": start_time, "timestamp <=": end_time}, limit)

    async def text_search(self, keyword: str, limit: int) -> List[Dict[str, Any]]:
        """Text search across entries (inefficient for unindexed backends)."""
        logger.warning(f"Text search on {self.__class__.__name__} may be inefficient.")
        entries = await self.query({}, limit * 10) # Fetch more to filter down
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
                        logger.error(f"Health check for {backend_name} failed: {e}", exc_info=True)
                        BACKEND_ERRORS.labels(backend=backend_name, type="HealthCheckError").inc()
                        asyncio.create_task(send_alert(f"Health check failed for {backend_name}: {e}", severity="critical"))
            else:
                try:
                    health_status = await self._health_check()
                except Exception as e:
                    logger.error(f"Health check for {backend_name} failed: {e}", exc_info=True)
                    BACKEND_ERRORS.labels(backend=backend_name, type="HealthCheckError").inc()
                    asyncio.create_task(send_alert(f"Health check failed for {backend_name}: {e}", severity="critical"))

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
        pass

    @abc.abstractmethod
    async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        """
        Backend-specific query. This method should return raw stored entries.
        Returned entries should be dicts containing at least 'encrypted_data', 'entry_id', 'schema_version', '_audit_hash'.
        """
        pass

    @abc.abstractmethod
    async def _migrate_schema(self) -> None:
        """
        Backend-specific schema migration logic.
        Should handle upgrades and provide rollback capability if migration fails.
        """
        pass

    @abc.abstractmethod
    async def _health_check(self) -> bool:
        """Backend-specific health check."""
        pass

    @abc.abstractmethod
    async def _get_current_schema_version(self) -> int:
        """
        Retrieve the current schema version from the backend's persistent storage.
        This is critical for migration logic to know the starting version.
        """
        pass

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
        self._validate_params()

    def _validate_params(self):
        # No specific parameters needed for in-memory, but ensure no conflicting ones are passed
        pass

    @asynccontextmanager
    async def _atomic_context(self, prepared_entries: List[Dict[str, Any]]) -> AsyncIterator[None]:
        # Atomic write for in-memory is trivial: extend the list.
        # This implementation simply trusts the flush to work, and if it fails, the entries
        # remain in the prepared_entries list which is GC'd.
        self.storage.extend(prepared_entries)
        yield

    async def _append_single(self, prepared_entry: Dict[str, Any]):
        # Batching handles storage, so single append is effectively no-op in this implementation
        pass

    async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        # In-memory storage returns the raw stored dictionary objects
        return self.storage[-limit:] 

    async def _migrate_schema(self):
        logger.info(f"{self.name}: Schema migration is trivial for in-memory storage. Current version: {self.schema_version}.")

    async def _health_check(self):
        # A simple check: Can we access the storage?
        return isinstance(self.storage, list)

    async def _get_current_schema_version(self):
        return SCHEMA_VERSION

class FileBackend(LogBackend):
    """
    A secure, file-based backend using atomic writes for resilience.
    Stores data as encrypted, compressed JSON Lines (.jsonl).
    NOTE: Uses tempfile/os.replace for atomic operations.
    """
    def __init__(self, params):
        super().__init__(params)
        self.name = "file"
        self.filepath = params.get("path", "audit_log.jsonl")
        self.dirpath = os.path.dirname(self.filepath) or '.'
        self.temp_suffix = ".tmp"
        os.makedirs(self.dirpath, exist_ok=True)
        self.lock = asyncio.Lock()
        self._validate_params()

    def _validate_params(self):
        if not self.filepath.endswith(".jsonl"):
            self.filepath += ".jsonl"
            logger.warning(f"FileBackend path forced to .jsonl suffix: {self.filepath}")

    @asynccontextmanager
    async def _atomic_context(self, prepared_entries: List[Dict[str, Any]]) -> AsyncIterator[None]:
        """
        Ensures atomicity by writing to a temporary file and renaming it only upon success.
        However, for an APPEND-ONLY log, we append atomically line by line to a temp file,
        then rename/append to the main file.
        Since we flush a *batch* atomically, we treat the batch as one unit.
        """
        temp_file_name = None
        temp_file_fd = None

        # 1. Acquire global file lock before touching the main file.
        async with self.lock:
            try:
                # 2. Create a unique temporary file and set permissions (0o600)
                temp_file_fd, temp_file_name = await asyncio.to_thread(
                    tempfile.mkstemp, dir=self.dirpath, suffix=self.temp_suffix
                )
                await asyncio.to_thread(os.fchmod, temp_file_fd, 0o600)

                # 3. Write all entries to the temp file
                with os.fdopen(temp_file_fd, 'w', encoding='utf-8') as tmp_file:
                    for entry in prepared_entries:
                        tmp_file.write(json.dumps(entry) + '\n')
                    tmp_file.flush()
                    await asyncio.to_thread(os.fsync, tmp_file.fileno())
                
                # Close the handle (file is now on disk)
                temp_file_fd = None 

                # 4. Yield control for external logic/span completion (empty in this case)
                yield

                # 5. ATOMIC APPEND: Use aiofiles.open with mode 'a' and a shared lock 
                # (handled by flush_batch's retry loop via its own lock)
                async with aiofiles.open(self.filepath, mode='a', encoding='utf-8') as f:
                    # Read content from temp file
                    async with aiofiles.open(temp_file_name, mode='r', encoding='utf-8') as tmp_f_read:
                        content_to_append = await tmp_f_read.read()
                    
                    # Append content
                    await f.write(content_to_append)
                    await f.flush()
                    await asyncio.to_thread(os.fsync, f.fileno())

            except Exception as e:
                logger.error(f"FileBackend atomic flush failed: {e}", exc_info=True)
                raise
            finally:
                # 6. Cleanup: Delete the temporary file
                if temp_file_name and os.path.exists(temp_file_name):
                    await asyncio.to_thread(os.remove, temp_file_name)
                # If file descriptor is still open (error before os.fdopen closed it)
                if temp_file_fd is not None:
                    try:
                        await asyncio.to_thread(os.close, temp_file_fd)
                    except OSError:
                        pass # Already closed

    async def _append_single(self, prepared_entry: Dict[str, Any]):
        # Batching handles storage
        pass

    async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        """Reads entries from the file, applying basic filters if possible."""
        results: List[Dict[str, Any]] = []

        if not os.path.exists(self.filepath):
            return results

        # Simple reverse-read approach to get the last 'limit' entries.
        # For small files, this is okay; for large files, it's slow (a known limitation of simple file backends).
        async with aiofiles.open(self.filepath, mode='r', encoding='utf-8') as f:
            lines = await f.readlines()

        # Reverse the list and process up to 'limit' matching entries
        for line in reversed(lines):
            try:
                entry = json.loads(line)
                
                # Simple filter application (only supports timestamp comparison, which is slow here anyway)
                is_match = True
                if 'timestamp >=' in filters and entry.get('timestamp', '') < filters['timestamp >=']:
                    is_match = False
                if 'timestamp <=' in filters and entry.get('timestamp', '') > filters['timestamp <=']:
                    is_match = False
                
                if is_match:
                    results.append(entry)
                
                if len(results) >= limit:
                    break
            except json.JSONDecodeError:
                logger.warning(f"Skipping malformed line in {self.filepath}.")
                continue
            
        return results

    async def _migrate_schema(self):
        # For a simple file backend, migration involves reading the file and rewriting it line by line.
        # This is a potentially long-running operation, thus it's async.
        logger.info(f"{self.name}: Schema migration is skipped for FileBackend placeholder.")

    async def _health_check(self):
        # Health check: Can we read and write to the directory?
        return os.path.exists(self.dirpath) and os.access(self.dirpath, os.R_OK | os.W_OK)

    async def _get_current_schema_version(self):
        # The file backend schema version is implicitly the latest, as we write the version with each entry.
        return SCHEMA_VERSION

# =========================================================================
# --- Backend Factory and Registry ---
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
        raise BackendNotFoundError(f"Audit log backend type '{kind}' not registered. Registered: {list(_REGISTRY.keys())}")

    try:
        instance = backend_cls(params)
        _INSTANCES[kind_lower] = instance
        logger.info(f"Initialized new audit backend instance: {backend_cls.__name__}")
        return instance
    except Exception as e:
        logger.critical(f"Failed to initialize backend {kind_lower} with params {params}: {e}", exc_info=True)
        raise CryptoInitializationError(f"Failed to initialize backend {kind_lower}: {e}") from e

# --- Default Backend Registration ---
register_backend('file', FileBackend)
register_backend('inmemory', InMemoryBackend)