# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import collections
import datetime
import gzip
import hashlib
import json
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import aiohttp
except ImportError:
    aiohttp = None

try:
    import portalocker
except ImportError:
    portalocker = None

try:
    import tenacity
except ImportError:
    # Provide a minimal tenacity shim for when it's not available
    class _MockTenacity:
        @staticmethod
        def retry(*args, **kwargs):
            def decorator(func):
                return func
            return decorator
        
        @staticmethod
        def stop_after_attempt(*args, **kwargs):
            pass
        
        @staticmethod
        def wait_exponential(*args, **kwargs):
            pass
        
        @staticmethod
        def wait_random(*args, **kwargs):
            pass
        
        @staticmethod
        def retry_if_exception_type(*args, **kwargs):
            class _P:
                def __or__(self, other):
                    return _P()
            return _P()
        
        @staticmethod
        def before_sleep_log(*args, **kwargs):
            pass
        
        @staticmethod
        def after_log(*args, **kwargs):
            pass
    
    tenacity = _MockTenacity()

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

try:
    from prometheus_client import REGISTRY, Counter, Gauge, Histogram
except ImportError:
    REGISTRY = None
    Counter = Gauge = Histogram = None

# Assuming a local utils module with these components
from .utils import AuditLogError, validate_input_details

logger = logging.getLogger(__name__)


# --- Idempotent Metric Registration ---
def get_or_create_metric(metric_class, name, documentation, labelnames=None):
    for collector in list(REGISTRY._collector_to_names.keys()):
        if hasattr(collector, "_name") and collector._name == name:
            return collector
    try:
        if labelnames:
            return metric_class(name, documentation, labelnames)
        else:
            return metric_class(name, documentation)
    except ValueError:
        for collector in list(REGISTRY._collector_to_names.keys()):
            if hasattr(collector, "_name") and collector._name == name:
                return collector
        raise


# Prometheus Metrics
AUDIT_LOG_FLUSH = get_or_create_metric(
    Counter, "audit_log_flush", "Total number of times the audit log buffer was flushed"
)
AUDIT_LOG_WRITE_SUCCESS = get_or_create_metric(
    Counter,
    "audit_log_write_success",
    "Total number of successful audit log writes to local file",
)
AUDIT_LOG_WRITE_FAILED = get_or_create_metric(
    Counter,
    "audit_log_write_failed",
    "Total number of failed audit log writes to local file",
)
AUDIT_LOG_DEAD_LETTER = get_or_create_metric(
    Counter,
    "audit_log_dead_letter",
    "Total number of audit entries sent to dead-letter queue",
    ["reason"],
)
AUDIT_LOG_ROTATION = get_or_create_metric(
    Counter, "audit_log_rotation", "Total number of audit log file rotations"
)
AUDIT_LOG_BUFFER_SIZE_GAUGE = get_or_create_metric(
    Gauge, "audit_log_buffer_size", "Current size of the audit log buffer"
)
AUDIT_LOG_REMOTE_SEND_SUCCESS = get_or_create_metric(
    Counter, "audit_log_remote_send_success", "Total successful remote audit log sends"
)
AUDIT_LOG_REMOTE_SEND_FAILED = get_or_create_metric(
    Counter, "audit_log_remote_send_failed", "Total failed remote audit log sends"
)
AUDIT_LOG_DISK_CHECK_FAILED = get_or_create_metric(
    Counter, "audit_log_disk_check_failed", "Total number of disk space check failures"
)
AUDIT_LOG_FLUSH_DURATION_SECONDS = get_or_create_metric(
    Histogram,
    "audit_log_flush_duration_seconds",
    "Duration of audit log buffer flush operations.",
)
AUDIT_LOG_DROPPED = get_or_create_metric(
    Counter, "audit_log_dropped", "Total dropped audit entries due to buffer overflow"
)


class AuditLogManager:
    """
    A robust, asynchronous audit log manager for high-throughput applications.

    This manager buffers log entries in-memory, flushes them periodically and
    on a full buffer, and writes them to a local file. It supports log rotation,
    dead-letter queuing for failed writes, and optional remote forwarding.
    """

    def __init__(
        self,
        log_path: Optional[str] = None,
        dead_letter_log_path: Optional[str] = None,
        enabled: Optional[bool] = None,
        settings: Optional[Any] = None,
        max_io_workers: int = 2,
    ):
        """
        Initializes the AuditLogManager with configurable settings.

        Args:
            log_path: The file path for the main audit log.
            dead_letter_log_path: The file path for failed audit entries.
            enabled: A flag to enable or disable the manager.
            settings: A settings object to source configurations from.
            max_io_workers: The number of threads to use for I/O operations.
        """
        self.settings = settings

        # 1.1 Migrate hardcoded constants to settings
        self.audit_schema_version = getattr(self.settings, "AUDIT_SCHEMA_VERSION", 1)
        self.retry_attempts = getattr(self.settings, "AUDIT_LOG_RETRY_ATTEMPTS", 3)
        self.retry_delay_seconds = getattr(
            self.settings, "AUDIT_LOG_RETRY_DELAY_SECONDS", 1
        )
        self.min_disk_space_mb = getattr(
            self.settings, "AUDIT_LOG_MIN_DISK_SPACE_MB", 100
        )
        self.enable_compression = getattr(
            self.settings, "AUDIT_LOG_ENABLE_COMPRESSION", False
        )
        self.temp_cleanup_timeout = getattr(
            self.settings, "AUDIT_LOG_TEMP_CLEANUP_TIMEOUT_SECONDS", 10
        )

        buffer_size = getattr(settings, "AUDIT_LOG_BUFFER_SIZE", 100)
        # Set deque maxlen slightly larger than flush threshold to prevent dropping logs under high load
        deque_maxlen = buffer_size * 2 if buffer_size > 0 else 1000
        self._log_buffer: collections.deque = collections.deque(maxlen=deque_maxlen)

        self._flush_task: Optional[asyncio.Task] = None
        self._buffer_lock = asyncio.Lock()
        self._last_audit_entry_hash: Optional[str] = None

        self.log_path = (
            log_path
            if log_path is not None
            else getattr(settings, "AUDIT_LOG_FILE_PATH", "sfe_bug_manager_audit.log")
        )
        self.dead_letter_log_path = (
            dead_letter_log_path
            if dead_letter_log_path is not None
            else getattr(
                settings,
                "AUDIT_DEAD_LETTER_FILE_PATH",
                "sfe_bug_manager_dead_letter.log",
            )
        )
        self.enabled = (
            enabled
            if enabled is not None
            else getattr(settings, "AUDIT_LOG_ENABLED", True)
        )
        self.encryption_key = getattr(self.settings, "AUDIT_LOG_ENCRYPTION_KEY", None)

        # 5.2 Validate paths/settings on init
        log_dir = Path(self.log_path).parent
        dead_letter_dir = Path(self.dead_letter_log_path).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        dead_letter_dir.mkdir(parents=True, exist_ok=True)

        if not os.access(log_dir, os.W_OK):
            raise ValueError(f"Log path parent directory '{log_dir}' is not writable.")
        if not os.access(dead_letter_dir, os.W_OK):
            raise ValueError(
                f"Dead letter log path parent directory '{dead_letter_dir}' is not writable."
            )

        # 2.1 Set initial file permissions
        if os.path.exists(self.log_path):
            os.chmod(self.log_path, 0o600)
        if os.path.exists(self.dead_letter_log_path):
            os.chmod(self.dead_letter_log_path, 0o600)

        self._io_executor = ThreadPoolExecutor(max_workers=max_io_workers)
        self._session: Optional[aiohttp.ClientSession] = None

        if self.encryption_key and not isinstance(self.encryption_key, (bytes, str)):
            logger.warning(
                "AUDIT_LOG_ENCRYPTION_KEY is not a valid type (bytes/str). Encryption will be disabled."
            )
            self.encryption_key = None

    async def initialize(self) -> None:
        """Initializes the manager and its background tasks."""
        if self.enabled and self._flush_task is None:
            self._flush_task = asyncio.create_task(self._periodic_flush())
            logger.info("AuditLogManager initialized and periodic flush task started.")
        if getattr(self.settings, "REMOTE_AUDIT_SERVICE_ENABLED", False):
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
                logger.info(
                    "aiohttp ClientSession initialized for remote audit service."
                )

    async def shutdown(self) -> None:
        """Gracefully shuts down the manager, ensuring all logs are flushed."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task  # Ensure task completion
            except asyncio.CancelledError:
                logger.info("Audit log periodic flush task cancelled.")
            except Exception as e:
                logger.error(
                    f"Error awaiting flush task cancellation: {e}", exc_info=True
                )
            finally:
                self._flush_task = None

        await self._flush_buffer(final_flush=True)

        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("aiohttp ClientSession closed.")

        if self._io_executor:
            self._io_executor.shutdown(wait=True)

        logger.info("AuditLogManager shut down.")

    async def _write_to_dead_letter_queue(
        self, entry: Dict[str, Any], reason: str
    ) -> None:
        if not getattr(self.settings, "REMOTE_AUDIT_DEAD_LETTER_ENABLED", True):
            return
        try:
            timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
            dead_letter_entry = {
                "timestamp": timestamp,
                "reason": reason,
                "original_entry": entry,
            }
            await asyncio.get_running_loop().run_in_executor(
                self._io_executor,
                self._sync_write_to_dead_letter,
                json.dumps(dead_letter_entry) + "\n",
            )
            logger.warning(f"Audit entry written to dead-letter queue: {reason}")
            AUDIT_LOG_DEAD_LETTER.labels(reason=reason).inc()
        except Exception as e:
            logger.error(f"Failed to write to dead-letter queue: {e}", exc_info=True)

    def _sync_write_to_dead_letter(self, data: str) -> None:
        try:
            with open(self.dead_letter_log_path, "a", encoding="utf-8") as f:
                portalocker.lock(f, portalocker.LOCK_EX)
                f.write(data)
                portalocker.unlock(f)
            os.chmod(self.dead_letter_log_path, 0o600)
        except (IOError, portalocker.LockException) as e:
            logger.critical(
                f"Could not write to dead-letter file '{self.dead_letter_log_path}': {e}",
                exc_info=True,
            )

    async def _periodic_flush(self) -> None:
        while True:
            try:
                await asyncio.sleep(
                    getattr(self.settings, "AUDIT_LOG_FLUSH_INTERVAL_SECONDS", 5)
                )
                await self._flush_buffer()
            except asyncio.CancelledError:
                logger.info("Audit log periodic flush task cancelled.")
                break
            except Exception as e:
                logger.critical(
                    f"Unhandled exception in periodic audit log flush: {e}",
                    exc_info=True,
                )
                await asyncio.sleep(self.retry_delay_seconds)

    async def _flush_buffer(self, final_flush: bool = False) -> None:
        """
        Flushes the in-memory log buffer to the local and remote destinations.

        Args:
            final_flush: True if this is a final flush during shutdown.
        """
        if not self.enabled and not final_flush:
            self._log_buffer.clear()
            AUDIT_LOG_BUFFER_SIZE_GAUGE.set(0)
            return

        with AUDIT_LOG_FLUSH_DURATION_SECONDS.time():
            async with self._buffer_lock:
                if not self._log_buffer:
                    return

                logs_to_flush = list(self._log_buffer)
                self._log_buffer.clear()
                AUDIT_LOG_BUFFER_SIZE_GAUGE.set(0)

            AUDIT_LOG_FLUSH.inc()
            processed_logs = []
            for entry in logs_to_flush:
                entry["schema_version"] = self.audit_schema_version
                entry["prev_hash"] = self._last_audit_entry_hash
                entry_json = json.dumps(entry, sort_keys=True, default=str)
                current_hash = hashlib.sha256(entry_json.encode("utf-8")).hexdigest()
                entry["current_hash"] = current_hash
                self._last_audit_entry_hash = current_hash
                processed_logs.append(entry)

            local_write_successful = False
            try:
                # Ensure log file directory exists
                os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
                await self._rotate_logs()
                await asyncio.get_running_loop().run_in_executor(
                    self._io_executor,
                    self._sync_atomic_write_with_retry,
                    processed_logs,
                )
                logger.debug(
                    f"Flushed {len(processed_logs)} audit entries to {self.log_path}"
                )
                AUDIT_LOG_WRITE_SUCCESS.inc()
                local_write_successful = True
            except portalocker.LockException:
                logger.warning(
                    "Could not acquire file lock for audit log. Re-buffering entries for next flush."
                )
                async with self._buffer_lock:
                    self._log_buffer.extendleft(reversed(processed_logs))
                AUDIT_LOG_BUFFER_SIZE_GAUGE.set(len(self._log_buffer))
                AUDIT_LOG_WRITE_FAILED.inc()
            except Exception as e:
                logger.error(f"Failed to flush audit logs: {e}", exc_info=True)
                for entry in processed_logs:
                    await self._write_to_dead_letter_queue(
                        entry, f"Local audit file write failed: {e}"
                    )
                AUDIT_LOG_WRITE_FAILED.inc()
                raise AuditLogError(f"Local audit log write failed: {e}")

            if (
                getattr(self.settings, "REMOTE_AUDIT_SERVICE_ENABLED", False)
                and local_write_successful
            ):
                await self._send_to_remote_audit_service(processed_logs)

    async def _rotate_logs(self) -> None:
        await asyncio.get_running_loop().run_in_executor(
            self._io_executor, self._sync_rotate_logs
        )

    # 1.2 Add compression for rotated logs
    def _sync_rotate_logs(self) -> None:
        if not os.path.exists(self.log_path):
            return

        log_dir = Path(self.log_path).parent
        try:
            # Use cross-platform shutil.disk_usage instead of os.statvfs
            usage = shutil.disk_usage(log_dir)
            free_space_mb = usage.free / (1024 * 1024)
            if free_space_mb < self.min_disk_space_mb:
                logger.critical(
                    f"Low disk space ({free_space_mb:.2f}MB). Skipping log rotation."
                )
                AUDIT_LOG_DISK_CHECK_FAILED.inc()
                return
        except OSError as e:
            logger.error(f"Disk space check failed for {log_dir}: {e}", exc_info=True)
            AUDIT_LOG_DISK_CHECK_FAILED.inc()
            return

        file_size_mb = os.path.getsize(self.log_path) / (1024 * 1024)
        max_size = getattr(self.settings, "AUDIT_LOG_MAX_FILE_SIZE_MB", 100)
        if file_size_mb < max_size:
            return

        logger.info(
            f"Audit log size ({file_size_mb:.2f}MB) exceeds max ({max_size}MB). Rotating."
        )
        AUDIT_LOG_ROTATION.inc()
        backup_count = getattr(self.settings, "AUDIT_LOG_BACKUP_COUNT", 5)
        for i in range(backup_count - 1, -1, -1):
            src = self.log_path if i == 0 else f"{self.log_path}.{i}"
            dst = f"{self.log_path}.{i+1}"
            if os.path.exists(src):
                try:
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.rename(src, dst)
                    logger.debug(f"Rotated '{src}' to '{dst}'")
                    if self.enable_compression and i > 0:
                        compressed_dst = f"{dst}.gz"
                        with (
                            open(dst, "rb") as f_in,
                            gzip.open(compressed_dst, "wb") as f_out,
                        ):
                            shutil.copyfileobj(f_in, f_out)
                        os.remove(dst)
                        logger.debug(f"Compressed '{dst}' to '{compressed_dst}'")
                except OSError as e:
                    logger.error(
                        f"Failed to rotate/compress '{src}' to '{dst}': {e}",
                        exc_info=True,
                    )

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(3),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _sync_atomic_write_with_retry(self, entries: List[Dict[str, Any]]) -> None:
        temp_filepath = (
            Path(self.log_path).parent
            / f"{Path(self.log_path).name}.tmp_{os.getpid()}_{time.monotonic()}"
        )

        # 2.2 Add optional encryption
        if self.encryption_key:
            try:
                fernet = Fernet(self.encryption_key)
                encrypted_lines = [
                    fernet.encrypt(
                        json.dumps(entry, default=str).encode("utf-8")
                    ).decode("utf-8")
                    for entry in entries
                ]
                data_to_write = "\n".join(encrypted_lines) + "\n"
                logger.info("Writing encrypted audit log entries.")
            except Exception as e:
                logger.error(f"Failed to encrypt audit log entries: {e}", exc_info=True)
                raise AuditLogError(f"Encryption failed: {e}")
        else:
            data_to_write = "".join(
                [json.dumps(entry, default=str) + "\n" for entry in entries]
            )

        try:
            with open(temp_filepath, "w", encoding="utf-8") as temp_f:
                portalocker.lock(temp_f, portalocker.LOCK_EX)
                temp_f.write(data_to_write)
                portalocker.unlock(temp_f)
            os.replace(temp_filepath, self.log_path)
            os.chmod(self.log_path, 0o600)
        except Exception as e:
            logger.error(f"Atomic write to audit log failed: {e}", exc_info=True)
            raise
        finally:
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                except OSError as e_remove:
                    logger.error(
                        f"Failed to remove temporary audit file {temp_filepath}: {e_remove}"
                    )

    async def _send_to_remote_audit_service(
        self, entries: List[Dict[str, Any]]
    ) -> None:
        remote_url = getattr(self.settings, "REMOTE_AUDIT_SERVICE_URL", None)
        if not remote_url:
            logger.warning("Remote audit service URL not configured.")
            return

        if (
            not hasattr(self, "_session")
            or self._session is None
            or self._session.closed
        ):
            logger.error(
                "Cannot send to remote audit service: aiohttp ClientSession is not available."
            )
            await self._handle_remote_send_failure(entries, "Session not ready")
            return

        timeout = getattr(self.settings, "REMOTE_AUDIT_SERVICE_TIMEOUT", 3.0)
        try:
            async with self._session.post(
                remote_url, json=entries, timeout=timeout
            ) as response:
                if 200 <= response.status < 300:
                    logger.info(
                        f"Sent {len(entries)} audit entries to remote service successfully."
                    )
                    AUDIT_LOG_REMOTE_SEND_SUCCESS.inc()
                else:
                    response_text = await response.text()
                    logger.error(
                        f"Failed to send audit entries. Status: {response.status}, Response: {response_text}"
                    )
                    await self._handle_remote_send_failure(
                        entries, f"Remote send failed: HTTP {response.status}"
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            error_reason = (
                "Timeout"
                if isinstance(e, asyncio.TimeoutError)
                else f"ClientError - {str(e)}"
            )
            logger.error(
                f"Network error sending audit entries: {error_reason}", exc_info=True
            )
            await self._handle_remote_send_failure(
                entries, f"Remote send failed: {error_reason}"
            )
        except Exception as e:
            logger.error(
                f"Unexpected error during remote audit send: {e}", exc_info=True
            )
            await self._handle_remote_send_failure(
                entries, f"Remote send failed: Unexpected error - {e}"
            )

    async def _handle_remote_send_failure(
        self, entries: List[Dict[str, Any]], reason: str
    ):
        AUDIT_LOG_REMOTE_SEND_FAILED.inc()
        if getattr(self.settings, "REMOTE_AUDIT_DEAD_LETTER_ENABLED", True):
            for entry in entries:
                await self._write_to_dead_letter_queue(entry, reason)

    async def audit(self, event_type: str, details: Dict[str, Any]) -> None:
        """
        Logs an audit event with validation and buffering.

        Args:
            event_type: The type of audit event (e.g., "login_success").
            details: A dictionary with event-specific details.

        Raises:
            ValueError: If the event_type is invalid.
        """
        if not self.enabled:
            return
        if not isinstance(event_type, str) or not event_type:
            logger.error(
                f"Invalid event_type: must be a non-empty string, got {type(event_type).__name__}"
            )
            raise ValueError("event_type must be a non-empty string.")

        entry = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event_type": event_type,
            "details": validate_input_details(details),
        }
        async with self._buffer_lock:
            prev_size = len(self._log_buffer)
            self._log_buffer.append(entry)
            current_size = len(self._log_buffer)
            if current_size == prev_size:
                AUDIT_LOG_DROPPED.inc()
                logger.warning("Audit entry dropped due to buffer overflow.")

            AUDIT_LOG_BUFFER_SIZE_GAUGE.set(current_size)
            if current_size >= getattr(self.settings, "AUDIT_LOG_BUFFER_SIZE", 100):
                # Schedule a flush but don't wait for it, to avoid blocking the caller
                asyncio.create_task(self._flush_buffer())
