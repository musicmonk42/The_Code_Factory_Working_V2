# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import base64
import gzip
import hashlib
import json
import logging
import os
import secrets
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import aiohttp

# Third-party module imports
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError:
    Fernet = None

try:
    from opentelemetry import metrics, trace
except ImportError:
    trace = None
    metrics = None

try:
    from plugins.dlt_backend import AuditLedgerClient
except ImportError:
    AuditLedgerClient = None

try:
    import syslog
except ImportError:
    syslog = None

try:
    import prometheus_client
except ImportError:
    prometheus_client = None


# --- Enums for Validation ---
class RotationType(str, Enum):
    """Valid rotation types for TimedRotatingFileHandler."""

    SIZE = "size"
    SECOND = "s"
    MINUTE = "m"
    HOUR = "h"
    DAY = "d"
    MIDNIGHT = "midnight"
    WEEKDAY = "w0-w6"


class CompressionType(str, Enum):
    """Supported compression types for rotated files."""

    NONE = "none"
    GZIP = "gzip"


# --- Configuration Dataclass ---
@dataclass
class AuditLoggerConfig:
    """
    Configuration for the Tamper-Evident Audit Logger.
    Supports advanced options for encryption, batching, and custom validation.
    """

    log_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("AUDIT_LOG_PATH", "./logs/audit_log.jsonl")
        )
    )
    rotation_type: str = os.environ.get("AUDIT_LOG_ROTATION", RotationType.MIDNIGHT)
    rotation_interval: int = int(os.environ.get("AUDIT_LOG_INTERVAL", 1))
    max_file_size: int = int(os.environ.get("AUDIT_LOG_MAX_SIZE", 10 * 1024 * 1024))
    retention_count: int = int(os.environ.get("AUDIT_LOG_RETENTION", 30))
    compression_type: str = os.environ.get(
        "AUDIT_LOG_COMPRESSION", CompressionType.GZIP
    )
    encrypt_logs: bool = os.environ.get("AUDIT_LOG_ENCRYPT", "false").lower() == "true"
    encryption_key: Optional[str] = os.environ.get("AUDIT_LOG_ENCRYPTION_KEY", None)
    batch_size: int = int(os.environ.get("AUDIT_LOG_BATCH_SIZE", 100))
    batch_timeout: float = float(os.environ.get("AUDIT_LOG_BATCH_TIMEOUT", 1.0))
    dlt_enabled: bool = AuditLedgerClient is not None
    dlt_anchor_critical: bool = (
        os.environ.get("DLT_ANCHOR_CRITICAL", "true").lower() == "true"
    )
    dlt_retry_count: int = int(os.environ.get("DLT_RETRY_COUNT", 3))
    dlt_batch_size: int = int(os.environ.get("DLT_BATCH_SIZE", 10))
    syslog_enabled: bool = syslog is not None
    syslog_facility: int = int(
        os.environ.get("SYSLOG_FACILITY", getattr(syslog, "LOG_LOCAL0", 0))
    )  # Fixed: safe attribute access
    async_logging: bool = os.environ.get("ASYNC_LOGGING", "true").lower() == "true"
    metrics_enabled: bool = prometheus_client is not None
    valid_event_types: List[str] = field(
        default_factory=lambda: [
            e.strip()
            for e in os.environ.get("VALID_EVENT_TYPES", "").split(",")
            if e.strip()
        ]
    )
    max_details_size: int = int(os.environ.get("MAX_DETAILS_SIZE", 1024 * 1024))
    alert_callback: Optional[Callable[[str], None]] = None

    def __post_init__(self):
        """Validate configuration parameters."""
        if self.rotation_type not in [e.value for e in RotationType]:
            raise ValueError(
                f"Invalid rotation_type: {self.rotation_type}. Must be one of {[e.value for e in RotationType]}"
            )
        if self.compression_type not in [e.value for e in CompressionType]:
            raise ValueError(
                f"Invalid compression_type: {self.compression_type}. Must be one of {[e.value for e in CompressionType]}"
            )
        if self.retention_count < 0:
            raise ValueError("retention_count must be non-negative")
        if self.max_file_size <= 0 and self.rotation_type == RotationType.SIZE:
            raise ValueError("max_file_size must be positive for size-based rotation")
        if self.dlt_retry_count < 0:
            raise ValueError("dlt_retry_count must be non-negative")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if self.batch_timeout <= 0:
            raise ValueError("batch_timeout must be positive")
        if self.encrypt_logs and not Fernet:
            raise ValueError("cryptography module required for log encryption")
        if self.encrypt_logs and not self.encryption_key:
            # Generate a key if encryption is enabled but no key is provided
            self.encryption_key = base64.urlsafe_b64encode(
                secrets.token_bytes(32)
            ).decode("utf-8")
        self.log_path = Path(self.log_path)


# --- Custom Handler for Size-Based Rotation ---
class SizedTimedRotatingFileHandler(TimedRotatingFileHandler):
    """Custom handler supporting size-based rotation and compression."""

    def __init__(
        self,
        filename: str,
        when: str,
        interval: int,
        backupCount: int,
        maxBytes: int,
        compression_type: str,
    ):
        super().__init__(
            filename,
            when=when,
            interval=interval,
            backupCount=backupCount,
            encoding="utf-8",
        )
        self.maxBytes = maxBytes
        self.compression_type = compression_type

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        """Check if the log file should rotate based on time or size."""
        if super().shouldRollover(record):
            return True
        if self.maxBytes > 0:
            if self.stream is not None:
                try:
                    if os.fstat(self.stream.fileno()).st_size >= self.maxBytes:
                        return True
                except (OSError, AttributeError):
                    return False
        return False

    def doRollover(self):
        """Perform log rotation and compress if configured."""
        super().doRollover()
        if self.compression_type == CompressionType.GZIP:
            self._compress_rotated_file()

    def _compress_rotated_file(self):
        """Compress the most recently rotated file using gzip."""
        rotated_file_path = Path(f"{self.baseFilename}.1")
        if rotated_file_path.exists():
            try:
                with rotated_file_path.open("rb") as f_in:
                    with gzip.open(f"{rotated_file_path}.gz", "wb") as f_out:
                        f_out.writelines(f_in)
                os.remove(rotated_file_path)
            except OSError as e:
                logging.error(
                    f"Failed to compress rotated file {rotated_file_path}: {e}"
                )


# --- Main AuditLogger Class ---
class TamperEvidentLogger:
    """
    A tamper-evident audit logger with hash chaining, async support, encryption, batching, and integrations.
    Uses a singleton pattern for consistent state across the application.
    """

    _instance: Optional["TamperEvidentLogger"] = None

    def __new__(cls, config: Optional[AuditLoggerConfig] = None):
        if cls._instance is None:
            cls._instance = super(TamperEvidentLogger, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config: Optional[AuditLoggerConfig] = None):
        if self._initialized:
            return

        self.config = config or AuditLoggerConfig()
        self._file_logging_available = True

        # Try to create log directory, gracefully handle permission errors
        try:
            self.config.log_path.parent.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            logging.warning(
                f"Cannot create audit log directory {self.config.log_path.parent}: {e}. "
                "File-based audit logging will be disabled."
            )
            self._file_logging_available = False
        except OSError as e:
            logging.warning(
                f"Cannot create audit log directory {self.config.log_path.parent}: {e}. "
                "File-based audit logging will be disabled."
            )
            self._file_logging_available = False

        self._last_hash: Optional[str] = None
        self._logger = self._setup_file_logger()
        self._dlt_client = self._setup_dlt_client()
        self._agent_info = self._get_agent_info()
        self._metrics = self._setup_metrics()
        self._lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._batch_queue: List[Dict[str, Any]] = []
        self._batch_task: Optional[asyncio.Task] = None
        self._fernet = self._setup_encryption()
        self._initialized = True
        self._log_queue = asyncio.Queue()
        self.app_instance_id = secrets.token_hex(16)
        self._hmac_key = None  # For future HMAC support

    @classmethod
    def get_instance(cls, config: Optional[AuditLoggerConfig] = None) -> "TamperEvidentLogger":
        """
        Get the singleton instance of TamperEvidentLogger.
        
        Args:
            config: Optional configuration. If provided and instance doesn't exist,
                   creates instance with this config. If instance exists, config is ignored.
                   
        Returns:
            TamperEvidentLogger: The singleton instance
        """
        if cls._instance is None:
            cls._instance = cls(config)
        return cls._instance

    def shutdown(self):
        """Shutdown the logger and cleanup background tasks.
        
        This method should be called during test cleanup or application shutdown
        to ensure all background tasks are properly terminated.
        """
        if self._batch_task and not self._batch_task.done():
            self._batch_task.cancel()
            # Give the task a chance to finish cancellation
            try:
                # If we're in an async context, we can await directly
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # We can't await in a running loop, so just cancel
                    pass
                else:
                    # We can run until the task is cancelled
                    loop.run_until_complete(
                        asyncio.wait_for(self._batch_task, timeout=1.0)
                    )
            except (asyncio.CancelledError, asyncio.TimeoutError, RuntimeError):
                # Task was cancelled or timeout, which is expected
                pass
            except Exception as e:
                self._logger.error(f"Error during shutdown: {e}")
        
        # Shutdown the executor
        if hasattr(self, '_executor') and self._executor:
            self._executor.shutdown(wait=False)

    def _setup_file_logger(self) -> logging.Logger:
        """Configure and return the file-based logger with rotation.

        If file logging is not available (e.g., due to permission errors),
        returns a logger with a NullHandler that effectively discards messages.
        """
        logger = logging.getLogger("AuditLogger")
        logger.setLevel(logging.INFO)

        # If file logging is not available, use a NullHandler
        if not self._file_logging_available:
            if not logger.handlers:
                logger.addHandler(logging.NullHandler())
            return logger

        if not logger.handlers:
            try:
                handler = SizedTimedRotatingFileHandler(
                    filename=str(self.config.log_path),
                    when=(
                        "midnight"
                        if self.config.rotation_type == RotationType.SIZE
                        else self.config.rotation_type
                    ),
                    interval=self.config.rotation_interval,
                    backupCount=self.config.retention_count,
                    maxBytes=self.config.max_file_size,
                    compression_type=self.config.compression_type,
                )
                formatter = logging.Formatter("%(message)s")
                handler.setFormatter(formatter)
                logger.addHandler(handler)
            except PermissionError as e:
                logging.warning(
                    f"Cannot create audit log file {self.config.log_path}: {e}. "
                    "File-based audit logging will be disabled."
                )
                self._file_logging_available = False
                logger.addHandler(logging.NullHandler())
            except OSError as e:
                logging.warning(
                    f"Cannot create audit log file {self.config.log_path}: {e}. "
                    "File-based audit logging will be disabled."
                )
                self._file_logging_available = False
                logger.addHandler(logging.NullHandler())
        return logger

    def _setup_dlt_client(self) -> Optional[Any]:
        """Initialize the DLT client if enabled."""
        if not self.config.dlt_enabled:
            return None
        try:
            return AuditLedgerClient()
        except Exception as e:
            self._logger.error(f"Failed to initialize DLT client: {e}")
            return None

    def _setup_metrics(self) -> Dict[str, Any]:
        """Initialize Prometheus metrics if enabled.
        
        Uses idempotent metric creation to avoid 'Duplicated timeseries' errors
        when the module is imported multiple times (e.g., during test collection).
        """
        if not self.config.metrics_enabled or not prometheus_client:
            return {}
        
        def _get_or_create_metric(metric_class, name: str, description: str, labelnames=None, buckets=None):
            """Idempotently create or retrieve a Prometheus metric.
            
            Note: Uses _names_to_collectors which is a private attribute of the registry.
            This is a common pattern in prometheus_client usage as there's no public API
            for checking if a metric exists. A try-except wrapper is used for safety.
            """
            registry = prometheus_client.REGISTRY
            # Check if metric already exists in registry (with defensive error handling)
            try:
                if hasattr(registry, '_names_to_collectors') and name in registry._names_to_collectors:
                    return registry._names_to_collectors[name]
            except (AttributeError, KeyError):
                pass  # Fall through to create the metric
            
            # Create new metric with error handling for duplicate registration
            try:
                if metric_class == prometheus_client.Histogram and buckets is not None:
                    return metric_class(name, description, labelnames=labelnames or [], buckets=buckets)
                elif labelnames:
                    return metric_class(name, description, labelnames=labelnames)
                else:
                    return metric_class(name, description)
            except ValueError as e:
                # Handle duplicate registration error gracefully
                if "Duplicated timeseries" in str(e) or "already registered" in str(e):
                    try:
                        return registry._names_to_collectors.get(name)
                    except (AttributeError, KeyError):
                        pass
                raise
        
        return {
            "log_events_total": _get_or_create_metric(
                prometheus_client.Counter,
                "audit_log_events_total",
                "Total number of audit log events",
                labelnames=["event_type"],
            ),
            "dlt_failures_total": _get_or_create_metric(
                prometheus_client.Counter,
                "audit_dlt_failures_total",
                "Total number of DLT anchoring failures",
            ),
            "integrity_checks_failed": _get_or_create_metric(
                prometheus_client.Counter,
                "audit_integrity_checks_failed_total",
                "Total number of failed integrity checks",
            ),
            "log_latency_seconds": _get_or_create_metric(
                prometheus_client.Histogram,
                "audit_log_latency_seconds",
                "Latency of log operations",
                buckets=[0.001, 0.01, 0.1, 0.5, 1, 5],
            ),
            "batch_size": _get_or_create_metric(
                prometheus_client.Gauge,
                "audit_log_batch_size",
                "Current size of the batch queue",
            ),
        }

    def _setup_encryption(self) -> Optional[Fernet]:
        """Initialize encryption if enabled."""
        if not self.config.encrypt_logs or not Fernet:
            return None
        try:
            # Use a KDF to derive a strong key from the provided (or generated) key string
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=b"audit_logger_salt",  # A fixed salt is acceptable here as the key is unique
                iterations=100000,
            )
            key = base64.urlsafe_b64encode(
                kdf.derive(self.config.encryption_key.encode("utf-8"))
            )
            return Fernet(key)
        except Exception as e:
            self._logger.error(f"Failed to initialize encryption: {e}")
            return None

    @staticmethod
    def _get_trace_ids() -> Tuple[Optional[str], Optional[str]]:
        """Return OpenTelemetry trace and span IDs if available."""
        if not trace:
            return None, None
        try:
            span = trace.get_current_span()
            context = span.get_span_context()
            if context.is_valid:
                return f"{context.trace_id:x}", f"{context.span_id:x}"
            return None, None
        except Exception:
            return None, None

    @staticmethod
    def _get_agent_info() -> Dict[str, Any]:
        """Return information about the current agent/process."""
        try:
            hostname = socket.gethostname()
        except socket.error:
            hostname = "localhost"
        return {
            "agent_id": os.environ.get("AGENT_ID", "unknown_agent"),
            "hostname": hostname,
            "pid": os.getpid(),
            "version": os.environ.get("APP_VERSION", "unknown"),
        }

    @staticmethod
    def _hash_entry(prev_hash: Optional[str], entry_dict: Dict[str, Any]) -> str:
        """Calculate a SHA256 hash of a log entry, chained with the previous hash."""
        h = hashlib.sha256()
        h.update((prev_hash or "").encode("utf-8"))
        h.update(
            json.dumps(entry_dict, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        return h.hexdigest()

    @staticmethod
    def _sanitize_dict(d: Dict[str, Any], max_size: int) -> Dict[str, Any]:
        """Sanitize dictionary and enforce size limits."""

        def sanitize_value(v: Any) -> Any:
            if isinstance(v, dict):
                return TamperEvidentLogger._sanitize_dict(v, max_size)
            if isinstance(v, list):
                return [sanitize_value(item) for item in v]
            if isinstance(v, str):
                v = v.encode("utf-8", errors="replace").decode("utf-8")
                if len(v.encode("utf-8")) > max_size:
                    return v[: max_size // 4] + "...[truncated]"
            return v

        result = {k: sanitize_value(v) for k, v in d.items()}
        serialized = json.dumps(result, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > max_size:
            raise ValueError(f"Dictionary size exceeds {max_size} bytes")
        return result

    def _encrypt_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Encrypt sensitive fields in the log entry."""
        if not self._fernet:
            return entry
        # A list of fields to be encrypted
        sensitive_fields = ["details", "extra"]
        encrypted_entry = entry.copy()
        for field_name in sensitive_fields:
            if field_name in encrypted_entry and isinstance(
                encrypted_entry[field_name], dict
            ):
                serialized = json.dumps(encrypted_entry[field_name], ensure_ascii=False)
                encrypted_entry[field_name] = self._fernet.encrypt(
                    serialized.encode("utf-8")
                ).decode("utf-8")
        return encrypted_entry

    def _decrypt_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Decrypt sensitive fields in the log entry."""
        if not self._fernet:
            return entry
        sensitive_fields = ["details", "extra"]
        decrypted_entry = entry.copy()
        for field_name in sensitive_fields:
            if field_name in decrypted_entry and isinstance(
                decrypted_entry[field_name], str
            ):
                try:
                    decrypted = self._fernet.decrypt(
                        decrypted_entry[field_name].encode("utf-8")
                    ).decode("utf-8")
                    decrypted_entry[field_name] = json.loads(decrypted)
                except Exception as e:
                    decrypted_entry[field_name] = {"error": f"Decryption failed: {e}"}
        return decrypted_entry

    async def _log_to_file_async(self, entries: List[Dict[str, Any]]):
        """Write a batch of log entries asynchronously."""
        loop = asyncio.get_event_loop()

        def write_batch():
            for entry in entries:
                serialized = json.dumps(self._encrypt_entry(entry), ensure_ascii=False)
                self._logger.info(serialized)

        await loop.run_in_executor(self._executor, write_batch)

    def _log_to_file_sync(self, entries: List[Dict[str, Any]]):
        """Write a batch of log entries synchronously."""
        for entry in entries:
            serialized = json.dumps(self._encrypt_entry(entry), ensure_ascii=False)
            self._logger.info(serialized)

    async def _process_batch_loop(self):
        """Periodically check and process the batch queue."""
        while True:
            try:
                # Check if we're in test mode and should exit early
                if os.getenv("PYTEST_CURRENT_TEST"):
                    await asyncio.sleep(0.1)  # Short sleep in test mode
                else:
                    # Flush the queue every timeout
                    await asyncio.sleep(self.config.batch_timeout)
                
                async with self._lock:
                    # In test mode, exit if queue is empty
                    if os.getenv("PYTEST_CURRENT_TEST") and not self._batch_queue:
                        break
                    
                    if self._batch_queue:
                        batch = self._batch_queue
                        self._batch_queue = []
                        if self._metrics:
                            self._metrics["batch_size"].set(0)

                        # Process file writing and DLT anchoring for the batch
                        try:
                            if self.config.async_logging:
                                await self._log_to_file_async(batch)
                            else:
                                self._log_to_file_sync(batch)
                        except Exception as e:
                            self._logger.error(f"Failed to write batch to file: {e}")
                            if self.config.alert_callback:
                                self.config.alert_callback(f"Batch write failed: {e}")

                        # Anchor to DLT if critical logs are in the batch
                        critical_entries = [e for e in batch if e.get("critical")]
                        if critical_entries and self.config.dlt_enabled:
                            dlt_results = await self._anchor_to_dlt(critical_entries)
                            for entry, dlt_result in zip(critical_entries, dlt_results):
                                if (
                                    isinstance(dlt_result, str)
                                    and "Failed" in dlt_result
                                ):
                                    entry["dlt_error"] = dlt_result
                                    if self.config.dlt_anchor_critical:
                                        self._logger.critical(dlt_result)
                                        if self.config.alert_callback:
                                            self.config.alert_callback(dlt_result)
                                else:
                                    entry["dlt_tx_id"] = dlt_result

                        # Forward to syslog
                        if self.config.syslog_enabled:
                            for syslog_entry in batch:
                                try:
                                    syslog_data = {
                                        k: v
                                        for k, v in syslog_entry.items()
                                        if k not in ["dlt_error"]
                                    }
                                    syslog.syslog(
                                        self.config.syslog_facility | syslog.LOG_INFO,
                                        json.dumps(syslog_data, ensure_ascii=False),
                                    )
                                except Exception:
                                    pass
            except asyncio.CancelledError:
                # Handle task cancellation gracefully during shutdown
                break
            except Exception as e:
                self._logger.error(f"Error in batch processing loop: {e}")

    async def _anchor_to_dlt(
        self, entries: List[Dict[str, Any]]
    ) -> List[Optional[str]]:
        """Attempt to anchor a batch of events to the DLT with retries."""
        if not self._dlt_client:
            return [None] * len(entries)

        results = []
        for i in range(0, len(entries), self.config.dlt_batch_size):
            sub_batch = entries[i : i + self.config.dlt_batch_size]
            dlt_batch_payload = [
                {
                    "event_type": entry["event_type"],
                    "details": entry["details"],
                    "hash": entry["current_hash"],
                    "timestamp": entry["timestamp"],
                    "agent": self._agent_info,
                    "user_id": entry.get("user_id"),
                    "extra": entry.get("extra"),
                }
                for entry in sub_batch
            ]

            for attempt in range(self.config.dlt_retry_count + 1):
                try:
                    loop = asyncio.get_event_loop()
                    dlt_results = await loop.run_in_executor(
                        None, self._dlt_client.log_event_batch, dlt_batch_payload
                    )
                    results.extend(dlt_results)
                    break
                except Exception as e:
                    if attempt == self.config.dlt_retry_count:
                        if self._metrics:
                            self._metrics["dlt_failures_total"].inc(len(sub_batch))
                        error_msg = f"Failed to anchor DLT batch after {self.config.dlt_retry_count} retries: {e}"
                        results.extend([error_msg] * len(sub_batch))
                        if self.config.alert_callback:
                            self.config.alert_callback(error_msg)
                    await asyncio.sleep(2**attempt)
        return results

    async def log_event(
        self,
        event_type: str,
        details: Dict[str, Any],
        user_id: Optional[str] = None,
        critical: bool = False,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Public-facing method to log an audit event, now with OmniCore integration.
        """
        # Note: extra parameter is accepted but not used in emit_audit_event
        return await self.emit_audit_event(
            event_type,
            details,
            user_id,
            critical,
            omnicore_url="https://api.example.com",
        )

    def _sanitize_details(self, details: Dict[str, Any]) -> Dict[str, Any]:
        """A helper to sanitize details before logging."""
        return TamperEvidentLogger._sanitize_dict(
            details or {}, self.config.max_details_size
        )

    def _compute_hmac(self, event_id, event_type, details, user_id):
        """Placeholder for HMAC computation."""
        return "mock_hmac"

    async def emit_audit_event(
        self,
        event_type: str,
        details: Dict[str, Any],
        user_id: Optional[str] = None,
        critical: bool = False,
        omnicore_url: Optional[str] = None,
    ):
        start_time = time.time()

        if (
            self.config.valid_event_types
            and event_type not in self.config.valid_event_types
        ):
            raise ValueError(
                f"Invalid event_type: {event_type}. Must be one of {self.config.valid_event_types}"
            )

        datetime.utcnow().isoformat() + "Z"
        trace_id, span_id = self._get_trace_ids()

        try:
            sanitized_details = self._sanitize_dict(
                details or {}, self.config.max_details_size
            )
        except ValueError as e:
            if self.config.alert_callback:
                self.config.alert_callback(f"Log event dropped due to size limit: {e}")
            raise

        event_id = secrets.token_hex(16)
        log_entry = {
            "event_id": event_id,
            "event_type": event_type,
            "details": sanitized_details,
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id or "anonymous",
            "app_instance_id": self.app_instance_id,
            "critical": critical,
        }
        if self._hmac_key:
            log_entry["hmac"] = self._compute_hmac(
                event_id, event_type, details, user_id
            )

        self._log_queue.put_nowait(log_entry)

        async with self._lock:
            log_entry["previous_hash"] = self._last_hash
            log_entry["current_hash"] = self._hash_entry(self._last_hash, log_entry)
            self._last_hash = log_entry["current_hash"]

            if self.config.async_logging:
                self._batch_queue.append(log_entry)
                if self._metrics:
                    self._metrics["batch_size"].set(len(self._batch_queue))
                    self._metrics["log_events_total"].labels(
                        event_type=event_type
                    ).inc()
                if not self._batch_task or self._batch_task.done():
                    self._batch_task = asyncio.create_task(self._process_batch_loop())

                if len(self._batch_queue) >= self.config.batch_size or critical:
                    batch_to_flush = self._batch_queue
                    self._batch_queue = []

                    try:
                        await self._log_to_file_async(batch_to_flush)
                    except Exception as e:
                        self._logger.error(f"Failed to write batch to file: {e}")
                        if self.config.alert_callback:
                            self.config.alert_callback(f"Batch write failed: {e}")

                    if critical and self.config.dlt_enabled:
                        dlt_results = await self._anchor_to_dlt(batch_to_flush)
                        for entry, dlt_result in zip(batch_to_flush, dlt_results):
                            if isinstance(dlt_result, str) and "Failed" in dlt_result:
                                entry["dlt_error"] = dlt_result
                                if self.config.dlt_anchor_critical:
                                    self._logger.critical(dlt_result)
                                    if self.config.alert_callback:
                                        self.config.alert_callback(dlt_result)
                            else:
                                entry["dlt_tx_id"] = dlt_result

                    if self.config.syslog_enabled:
                        for syslog_entry in batch_to_flush:
                            try:
                                syslog_data = {
                                    k: v
                                    for k, v in syslog_entry.items()
                                    if k not in ["dlt_error"]
                                }
                                syslog.syslog(
                                    self.config.syslog_facility | syslog.LOG_INFO,
                                    json.dumps(syslog_data, ensure_ascii=False),
                                )
                            except Exception:
                                pass
            else:  # Synchronous logging
                batch_to_flush = [log_entry]
                try:
                    self._log_to_file_sync(batch_to_flush)
                except Exception as e:
                    self._logger.error(f"Failed to write entry to file: {e}")
                    if self.config.alert_callback:
                        self.config.alert_callback(f"Entry write failed: {e}")

                if self.config.dlt_enabled and critical:
                    dlt_results = await self._anchor_to_dlt(batch_to_flush)
                    for entry, dlt_result in zip(batch_to_flush, dlt_results):
                        if isinstance(dlt_result, str) and "Failed" in dlt_result:
                            entry["dlt_error"] = dlt_result
                            if self.config.dlt_anchor_critical:
                                self._logger.critical(dlt_result)
                                if self.config.alert_callback:
                                    self.config.alert_callback(dlt_result)
                        else:
                            entry["dlt_tx_id"] = dlt_result

                if self.config.syslog_enabled:
                    for syslog_entry in batch_to_flush:
                        try:
                            syslog_data = {
                                k: v
                                for k, v in syslog_entry.items()
                                if k not in ["dlt_error"]
                            }
                            syslog.syslog(
                                self.config.syslog_facility | syslog.LOG_INFO,
                                json.dumps(syslog_data, ensure_ascii=False),
                            )
                        except Exception:
                            pass

                if self._metrics:
                    self._metrics["log_events_total"].labels(
                        event_type=event_type
                    ).inc()

            if self._metrics:
                self._metrics["log_latency_seconds"].observe(time.time() - start_time)

        # Add omnicore_engine publishing
        if omnicore_url:
            async with aiohttp.ClientSession() as session:
                try:
                    await session.post(f"{omnicore_url}/audit", json=log_entry)
                    logging.getLogger(__name__).info(
                        f"Audit event sent to omnicore_engine: {event_type}"
                    )
                except Exception as e:
                    logging.getLogger(__name__).error(
                        f"Failed to send audit event to omnicore_engine: {e}"
                    )

        return log_entry["current_hash"]

    def _get_log_files(self, log_path: Path) -> List[Path]:
        """Return a sorted list of log files, including compressed rotated files."""
        files = []
        if log_path.exists():
            files.append(log_path)
        for f in log_path.parent.glob(f"{log_path.name}.*"):
            if f.is_file() and not f.name.endswith(".gz.gz"):
                files.append(f)
        return sorted(
            files,
            key=lambda x: (
                x.name == log_path.name,
                os.path.getmtime(x) if x.exists() else 0,
            ),
            reverse=True,
        )

    async def verify_log_integrity(
        self, log_path: Optional[Path] = None
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        Verify the hash chain integrity of the audit log, including rotated files.
        """
        start_time = time.time()
        target_path = log_path or self.config.log_path
        log_files = self._get_log_files(target_path)
        prev_hash: Optional[str] = None

        for file_path in log_files:
            idx = 0
            try:
                # Determine how to open the file based on its extension
                if file_path.suffix == ".gz":
                    open_func = gzip.open
                    mode = "rt"
                else:
                    open_func = Path.open
                    mode = "r"

                with open_func(file_path, mode, encoding="utf-8") as f:
                    for idx, line in enumerate(f, 1):
                        if not line.strip():
                            continue

                        try:
                            entry = json.loads(line)
                            current_hash = entry.get("current_hash")
                            decrypted_entry = self._decrypt_entry(entry)
                            temp_entry = {
                                k: v
                                for k, v in decrypted_entry.items()
                                if k != "current_hash"
                            }
                            expected_hash = self._hash_entry(prev_hash, temp_entry)
                        except (json.JSONDecodeError, ValueError) as e:
                            if self._metrics:
                                self._metrics["integrity_checks_failed"].inc()
                            if self.config.alert_callback:
                                self.config.alert_callback(
                                    f"Integrity check failed (JSON/decryption error) at {file_path}:{idx}: {e}"
                                )
                            return False, idx, str(file_path)

                        if current_hash != expected_hash:
                            if self._metrics:
                                self._metrics["integrity_checks_failed"].inc()
                            if self.config.alert_callback:
                                self.config.alert_callback(
                                    f"Integrity check failed at {file_path}:{idx}"
                                )
                            return False, idx, str(file_path)

                        prev_hash = current_hash
            except (IOError, gzip.BadGzipFile) as e:
                if self._metrics:
                    self._metrics["integrity_checks_failed"].inc()
                if self.config.alert_callback:
                    self.config.alert_callback(
                        f"Integrity check error at {file_path}: {e}"
                    )
                return False, idx, str(file_path)

        if self._metrics:
            self._metrics["log_latency_seconds"].observe(time.time() - start_time)
        return True, None, None

    def load_audit_trail(
        self,
        log_path: Optional[Path] = None,
        event_type: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        user_id: Optional[str] = None,
    ) -> Iterator[Dict[str, Any]]:
        """
        A generator that yields each valid log entry from the audit log file, with optional filtering.
        """
        start_time_read = time.time()
        target_path = log_path or self.config.log_path
        log_files = self._get_log_files(target_path)

        for file_path in log_files:
            try:
                if file_path.suffix == ".gz":
                    open_func = gzip.open
                    mode = "rt"
                else:
                    open_func = Path.open
                    mode = "r"

                with open_func(file_path, mode, encoding="utf-8") as f:
                    yield from self._filter_log_entries(
                        f, event_type, start_time, end_time, user_id
                    )
            except (IOError, gzip.BadGzipFile) as e:
                self._logger.error(f"Error reading audit log file {file_path}: {e}")
                if self.config.alert_callback:
                    self.config.alert_callback(
                        f"Audit trail read error at {file_path}: {e}"
                    )
                continue

        if self._metrics:
            self._metrics["log_latency_seconds"].observe(time.time() - start_time_read)

    def _filter_log_entries(
        self,
        file_handle,
        event_type: Optional[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        user_id: Optional[str],
    ) -> Iterator[Dict[str, Any]]:
        """Yields log entries that match the given filters."""
        for line in file_handle:
            line = line.strip()
            if not line:
                continue
            try:
                encrypted_entry = json.loads(line)
                entry = self._decrypt_entry(encrypted_entry)
                if self._filter_entry_logic(
                    entry, event_type, start_time, end_time, user_id
                ):
                    yield entry
            except (json.JSONDecodeError, ValueError) as e:
                self._logger.warning(
                    f"Skipping malformed or undecryptable log entry: {e}"
                )
                continue

    @staticmethod
    def _filter_entry_logic(
        entry: Dict[str, Any],
        event_type: Optional[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        user_id: Optional[str],
    ) -> bool:
        """Apply filtering conditions to a log entry."""
        # Change this line to check both 'event' and 'event_type' for compatibility
        if (
            event_type
            and entry.get("event_type") != event_type
            and entry.get("event") != event_type
        ):
            return False
        if user_id and entry.get("user_id") != user_id:
            return False
        timestamp_str = entry.get("timestamp")
        if timestamp_str and (start_time or end_time):
            try:
                entry_time = datetime.fromisoformat(timestamp_str.rstrip("Z"))
                if start_time and entry_time < start_time:
                    return False
                if end_time and entry_time > end_time:
                    return False
            except ValueError:
                return False
        return True


# --- Global Singleton Instance and Public API ---
# The singleton pattern ensures only one instance manages the state (hash, queue, etc.).
audit_logger = TamperEvidentLogger()


# The public API functions are wrappers around the singleton's methods.
async def log_event(
    event_type: str,
    details: Dict[str, Any],
    critical: bool = False,
    user_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    return await audit_logger.log_event(event_type, details, user_id, critical, extra)


async def verify_log_integrity(
    log_path: Optional[Path] = None,
) -> Tuple[bool, Optional[int], Optional[str]]:
    return await audit_logger.verify_log_integrity(log_path)


async def load_audit_trail(
    log_path: Optional[Path] = None,
    event_type: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    user_id: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    for entry in audit_logger.load_audit_trail(
        log_path, event_type, start_time, end_time, user_id
    ):
        yield entry


async def emit_audit_event(
    event_type: str,
    details: Dict[str, Any],
    user_id: Optional[str] = None,
    critical: bool = False,
    omnicore_url: Optional[str] = None,
):
    """Emit an audit event to the tamper-evident log."""
    return await audit_logger.emit_audit_event(
        event_type, details, user_id, critical, omnicore_url
    )


# --- Example Usage ---
if __name__ == "__main__":

    async def main():
        def alert_callback(message: str):
            """A simple callback function for alerts."""
            print(f"ALERT: {message}")

        # Set up a new logger instance with custom configuration for demonstration
        config = AuditLoggerConfig(
            log_path=Path("./audit_logs/test_log.jsonl"),
            rotation_type=RotationType.SIZE,
            max_file_size=1024 * 1024,
            compression_type=CompressionType.GZIP,
            encrypt_logs=True,
            encryption_key="my-super-secret-key-1234567890",
            valid_event_types=["user_action", "policy_violation"],
            alert_callback=alert_callback,
            batch_size=2,
            batch_timeout=5.0,
        )
        # Re-initialize the global singleton with the new config for this example
        TamperEvidentLogger._instance = None
        logger_instance = TamperEvidentLogger(config)

        # Log events
        details = {"action": "user_login", "ip": "192.168.1.1"}
        hash_val = await logger_instance.log_event(
            "user_action", details, user_id="user123"
        )
        print(f"Logged event with hash: {hash_val}")

        details_critical = {
            "file_op": "deletion",
            "filename": "sensitive_data.txt",
            "data": "This is sensitive info." * 100,
        }
        hash_val_critical = await logger_instance.log_event(
            "policy_violation", details_critical, user_id="user456", critical=True
        )
        print(f"Logged critical event with hash: {hash_val_critical}")

        # Ensure all batches are processed before verification
        await asyncio.sleep(config.batch_timeout * 2)

        # Verify integrity
        is_valid, line, file = await logger_instance.verify_log_integrity()
        print(
            f"Log integrity: {'Valid' if is_valid else f'Invalid at line {line} in {file}'}"
        )

        # Load and filter audit trail
        print("\nLoading filtered audit trail:")
        async for entry in logger_instance.load_audit_trail(
            event_type="policy_violation", user_id="user456"
        ):
            print(json.dumps(entry, indent=2))

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program interrupted by user.")
    except Exception as e:
        print(f"An error occurred: {e}")
