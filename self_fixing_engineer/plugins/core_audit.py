# -*- coding: utf-8 -*-
"""
A thread-safe, singleton AuditLogger for production environments.

This module provides a robust AuditLogger that writes structured, pure JSON events
asynchronously to a rotating file and optionally to the console. It is designed
for high-throughput, resilient, secure, and ops-friendly logging.

**NOTE**: The file-based handlers (RotatingFileHandler, WatchedFileHandler) are
intended for single-process applications. For multi-process or multi-worker
setups (e.g., Gunicorn, Celery), do not log to a shared file directly. Instead,
forward logs to a syslog daemon, a local TCP/UDP collector, or another centralized
logging service to avoid interleaved or corrupted log files.

Features:
- Asynchronous logging via a bounded QueueListener to prevent blocking and memory exhaustion.
- Thread-safe singleton pattern ensures a single logging instance per process.
- Pure JSON output, one event per line, with a schema version for migrations.
- Durable, unique `event_id` and a process-lifetime `app_instance_id`.
- Rate limiting (storm control) with a cap on unique event types to protect memory.
- Event size capping to prevent disk DoS from oversized log payloads.
- Log file rotation with configurable size and backup count.
- Configuration driven by environment variables via a `SecretsManager`.
- Automatic enrichment of log events with context (app, env, host, pid, extra static context).
- Runtime reloading of configuration without dropping logs (including SIGHUP on POSIX).
- Optional HMAC signatures with key rotation (kid) support for tamper-detection.
- Safe serialization that coerces unknown types to strings, preventing crashes.
- Hardened file permissions on POSIX systems (best-effort) with warnings on failure.
- Hardened against I/O errors (e.g., disk full) with fallback to stderr and optional strict mode.
- Graceful shutdown and reload with proper resource (file descriptor) management.

Configuration via Environment Variables (through SecretsManager):
- AUDIT_LOG_FILE: Path to the log file. (Default: 'audit.log')
- AUDIT_LOG_LEVEL: Logging level (e.g., 'INFO', 'DEBUG'). (Default: 'INFO')
- AUDIT_LOG_TO_CONSOLE: Log to stdout as well. (Default: False)
- AUDIT_QUEUE_MAXSIZE: Max items in the async logging queue before dropping. (Default: 10000)
- AUDIT_LOG_MAX_BYTES: Max size in bytes before rotation. (Default: 10MB)
- AUDIT_LOG_BACKUP_COUNT: Number of backup files to keep. (Default: 5)
- AUDIT_USE_WATCHED_FILE: On POSIX, use WatchedFileHandler for external rotation. (Default: False)
- AUDIT_EVENT_MAX_BYTES: Max size for a single log event before truncation. (Default: 256KB)
- AUDIT_RL_WINDOW_SEC: Time window in seconds for rate limiting. (Default: 10)
- AUDIT_RL_MAX_EVENTS: Max events per event_type per window before dropping. (Default: 100)
- AUDIT_RL_MAX_KEYS: Max distinct (event_type, level) buckets to track. (Default: 1000)
- AUDIT_HMAC_KEY: A secret key for single-key HMAC-SHA256 signatures. (Optional)
- AUDIT_HMAC_KEYS_JSON: JSON map of key IDs to keys for multi-key signing. (e.g., '{"kid1":"key1"}')
- AUDIT_HMAC_ACTIVE_KID: The key ID from the JSON map to use for signing.
- AUDIT_EXTRA_CONTEXT_JSON: A JSON string of static key-value pairs to add to all logs.
- APP_NAME: Name of the application. (Default: 'unknown_app')
- ENVIRONMENT: Deployment environment (e.g., 'prod', 'dev'). (Default: 'unknown')
- AUDIT_APP_VERSION: Application version. (Optional)
- AUDIT_BUILD_ID: Application build identifier. (Optional)
- AUDIT_INCLUDE_TRACES: Set to 'true' to include full stack traces in exception logs. (Default: False)
- AUDIT_STRICT_WRITES: If 'true', I/O errors will be raised after being logged to stderr. (Default: False)
"""
import atexit
import hashlib
import hmac
import json
import logging
import os
import queue
import socket
import stat
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from datetime import datetime
from logging.handlers import (
    QueueHandler,
    QueueListener,
    RotatingFileHandler,
    WatchedFileHandler,
)
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from .core_secrets import (
    SecretsManager,
)  # Assumes core_secrets.py is in the same directory

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
SCHEMA_VERSION = 1
_INIT_ONCE_LOCK = threading.Lock()


def _json_fallback(o: Any) -> str:
    """Coerce non-serializable types to their string representation."""
    return str(o)


def _prod() -> bool:
    """Check if the environment is configured as production."""
    return os.getenv("ENVIRONMENT", "").lower().startswith("prod")


class _DropOnFullQueueHandler(QueueHandler):
    """A QueueHandler that drops messages and warns to stderr if the queue is full."""

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            try:
                ts = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
                err_msg = {
                    "schema_version": SCHEMA_VERSION,
                    "event_type": "audit_queue_full",
                    "timestamp": ts,
                }
                sys.stderr.write(
                    json.dumps(err_msg, separators=(",", ":"), ensure_ascii=False)
                    + "\n"
                )
            except Exception:
                pass


class _SafeHandler(logging.Handler):
    """Wraps a real handler to catch emit/flush/close errors and mirror JSON to stderr."""

    def __init__(self, inner: logging.Handler, strict: bool, name: str):
        super().__init__(level=inner.level)
        self.inner = inner
        self.strict = strict
        self.name = name

    def setFormatter(self, fmt: logging.Formatter) -> None:
        self.inner.setFormatter(fmt)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.inner.emit(record)
        except Exception as e:
            self._mirror("audit_sink_write_failed", str(e))

    def flush(self) -> None:
        try:
            self.inner.flush()
        except Exception as e:
            self._mirror("audit_sink_flush_failed", str(e))

    def close(self) -> None:
        try:
            self.inner.close()
        except Exception as e:
            self._mirror("audit_sink_close_failed", str(e))
        finally:
            super().close()

    def _mirror(self, event_type: str, err: str) -> None:
        try:
            ts = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
            sys.stderr.write(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "event_type": event_type,
                        "timestamp": ts,
                        "handler": self.name,
                        "error": err,
                    },
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                + "\n"
            )
        except Exception:
            pass
        if self.strict:
            # Raise an exception for graceful shutdown instead of immediate process termination
            raise IOError(f"Audit handler {self.name} failed: {err}")


class AuditLogger:
    """A thread-safe, production-ready audit logger that writes structured JSON events."""

    _instance: Optional["AuditLogger"] = None
    _singleton_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Implements the thread-safe singleton pattern for the class."""
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = super(AuditLogger, cls).__new__(cls)
        return cls._instance

    def __init__(self, secrets_manager: Optional[SecretsManager] = None):
        """
        Initialize the AuditLogger with retry logic.

        IMPROVED: This now tracks initialization success/failure and allows
        retries if initial initialization fails. Previous implementation
        would skip all subsequent initialization attempts even if the first
        one failed.

        Environment Variables:
        - AUDIT_INIT_RETRY_COUNT: Number of retry attempts (default: 3)
        - AUDIT_INIT_RETRY_DELAY: Delay between retries in seconds (default: 1)
        """
        # Check if already successfully initialized
        if getattr(self, "_initialized", False) and getattr(
            self, "_init_successful", False
        ):
            return

        with _INIT_ONCE_LOCK:
            # Double-check after acquiring lock
            if getattr(self, "_initialized", False) and getattr(
                self, "_init_successful", False
            ):
                return

            # Allow retry if previous initialization failed
            if getattr(self, "_initialized", False) and not getattr(
                self, "_init_successful", False
            ):
                retry_count = int(os.getenv("AUDIT_INIT_RETRY_COUNT", "3"))
                current_retry = getattr(self, "_init_retry_count", 0)

                if current_retry >= retry_count:
                    raise RuntimeError(
                        f"AuditLogger initialization failed after {retry_count} attempts. "
                        "Check logs for details."
                    )

                self._init_retry_count = current_retry + 1
                retry_delay = float(os.getenv("AUDIT_INIT_RETRY_DELAY", "1"))
                time.sleep(retry_delay)

            # Initialize retry tracking
            if not hasattr(self, "_init_retry_count"):
                self._init_retry_count = 0

            try:
                self.secrets_manager = secrets_manager or SecretsManager()
                self._init_lock = threading.Lock()
                self._write_lock = threading.Lock()  # Guards stderr fallbacks
                self._rl_lock = threading.Lock()
                self._context: Dict[str, Any] = {}
                self._rl: Dict[tuple, deque] = {}
                self._closed = False
                self.logger = logging.getLogger("audit_logger")
                self.logger.propagate = False
                self._app_instance_id = uuid.uuid4().hex

                maxsize = self._get_config_value(
                    "AUDIT_QUEUE_MAXSIZE", 10000, int, (1000, 1_000_000)
                )
                self._log_queue: queue.Queue = queue.Queue(maxsize=maxsize)
                self._queue_handler = _DropOnFullQueueHandler(self._log_queue)
                self.logger.addHandler(self._queue_handler)
                self._listener: Optional[QueueListener] = None
                self._attached_handlers: List[logging.Handler] = []

                self._configure_logger_locked()
                atexit.register(self.close)

                # Mark as successfully initialized
                self._init_successful = True
                self._initialized = True

            except Exception as e:
                # Mark as initialized but failed
                self._initialized = True
                self._init_successful = False

                # Log error to stderr
                error_msg = {
                    "event_type": "audit_logger_init_failed",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "error": str(e),
                    "retry_count": self._init_retry_count,
                }
                try:
                    sys.stderr.write(json.dumps(error_msg) + "\n")
                except Exception:
                    pass

                raise RuntimeError(f"AuditLogger initialization failed: {e}") from e

    def health_check(self) -> Dict[str, Any]:
        """
        Perform a health check on the AuditLogger.

        Returns:
            dict: Health status including initialization state, queue status, etc.
        """
        return {
            "initialized": getattr(self, "_initialized", False),
            "init_successful": getattr(self, "_init_successful", False),
            "init_retry_count": getattr(self, "_init_retry_count", 0),
            "closed": getattr(self, "_closed", True),
            "queue_size": self._log_queue.qsize() if hasattr(self, "_log_queue") else 0,
            "app_instance_id": getattr(self, "_app_instance_id", None),
        }

    def _get_config_value(
        self, key: str, default: Any, type_cast: type, bounds: Optional[tuple] = None
    ) -> Any:
        """Helper to fetch, cast, and bound configuration values."""
        val = self.secrets_manager.get_secret(key, default=default, type_cast=type_cast)
        if bounds and isinstance(val, (int, float)):
            return min(max(val, bounds[0]), bounds[1])
        return val

    def _load_context(self) -> None:
        """Load and cache global context metadata from the environment."""
        self._context = {
            "app_name": self._get_config_value("APP_NAME", "unknown_app", str),
            "environment": self._get_config_value("ENVIRONMENT", "unknown", str),
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "app_instance_id": self._app_instance_id,
        }
        try:
            extra_context_str = self._get_config_value(
                "AUDIT_EXTRA_CONTEXT_JSON", None, str
            )
            if extra_context_str:
                extra_context = json.loads(extra_context_str)
                if isinstance(extra_context, dict):
                    for k, v in extra_context.items():
                        if k not in self._context and isinstance(
                            v, (str, int, float, bool, type(None))
                        ):
                            self._context[k] = v
        except (json.JSONDecodeError, TypeError):
            pass

    def _configure_handlers(self) -> None:
        """Builds concrete handlers, wraps them for safety, and manages the QueueListener lifecycle."""
        log_file_path = Path(self._get_config_value("AUDIT_LOG_FILE", "audit.log", str))
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path_str = str(log_file_path)

        try:
            if not log_file_path.exists():
                log_file_path.touch(mode=0o600, exist_ok=True)
        except Exception:
            pass

        formatter = logging.Formatter("%(message)s")
        new_handlers: List[logging.Handler] = []

        use_watched = self._get_config_value("AUDIT_USE_WATCHED_FILE", False, bool)
        if use_watched and os.name == "posix":
            fh = WatchedFileHandler(filename=file_path_str, encoding="utf-8")
        else:
            max_bytes = self._get_config_value(
                "AUDIT_LOG_MAX_BYTES", 10 * 1024 * 1024, int, (1_000_000, 1_000_000_000)
            )
            backup_count = self._get_config_value(
                "AUDIT_LOG_BACKUP_COUNT", 5, int, (1, 50)
            )
            fh = RotatingFileHandler(
                file_path_str,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
                delay=True,
            )
        fh.setFormatter(formatter)
        new_handlers.append(fh)

        try:
            os.chmod(file_path_str, stat.S_IRUSR | stat.S_IWUSR)
        except Exception as perm_err:
            if _prod():
                try:
                    ts = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
                    err_msg = {
                        "schema_version": SCHEMA_VERSION,
                        "event_type": "audit_perm_warning",
                        "timestamp": ts,
                        "path": file_path_str,
                        "error": str(perm_err),
                    }
                    sys.stderr.write(
                        json.dumps(err_msg, separators=(",", ":"), ensure_ascii=False)
                        + "\n"
                    )
                except Exception:
                    pass

        if self._get_config_value("AUDIT_LOG_TO_CONSOLE", False, bool):
            ch = logging.StreamHandler()
            ch.setFormatter(formatter)
            ch.setLevel(self.logger.level)
            new_handlers.append(ch)

        # Wrap all new handlers in the _SafeHandler to catch I/O errors
        strict = self._strict_writes
        wrapped_handlers: List[logging.Handler] = []
        for h in new_handlers:
            wrapped = _SafeHandler(h, strict=strict, name=type(h).__name__)
            wrapped.setFormatter(formatter)
            wrapped_handlers.append(wrapped)

        # Atomically swap listeners
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
        for h in self._attached_handlers:
            try:
                h.flush()
                h.close()
            except Exception:
                pass
        self._attached_handlers.clear()

        self._listener = QueueListener(
            self._log_queue, *wrapped_handlers, respect_handler_level=True
        )
        self._attached_handlers = wrapped_handlers
        self._listener.start()

    def _configure_logger_locked(self) -> None:
        """(Re)configures the logger and caches hot-path settings. Assumes the caller holds _init_lock."""
        level_str = self._get_config_value("AUDIT_LOG_LEVEL", "INFO", str).upper()
        self.logger.setLevel(getattr(logging, level_str, logging.INFO))
        self._load_context()

        self._rl_window = self._get_config_value(
            "AUDIT_RL_WINDOW_SEC", 10, int, (1, 300)
        )
        self._rl_limit = self._get_config_value(
            "AUDIT_RL_MAX_EVENTS", 100, int, (10, 10000)
        )
        self._rl_max_keys = self._get_config_value(
            "AUDIT_RL_MAX_KEYS", 1000, int, (100, 10000)
        )
        self._max_event_bytes = self._get_config_value(
            "AUDIT_EVENT_MAX_BYTES", 256 * 1024, int, (16 * 1024, 2 * 1024 * 1024)
        )
        self._strict_writes = self._get_config_value("AUDIT_STRICT_WRITES", False, bool)

        self._active_hmac_key, self._active_hmac_kid = None, None
        hmac_keys_str = self._get_config_value("AUDIT_HMAC_KEYS_JSON", None, str)
        if hmac_keys_str:
            try:
                keys = json.loads(hmac_keys_str) or {}
                active_kid = self._get_config_value("AUDIT_HMAC_ACTIVE_KID", None, str)
                if not active_kid and keys:
                    active_kid = sorted(keys.keys())[0]
                if active_kid in keys:
                    self._active_hmac_key = keys[active_kid]
                    self._active_hmac_kid = active_kid
            except (json.JSONDecodeError, TypeError):
                pass
        else:
            self._active_hmac_key = self._get_config_value("AUDIT_HMAC_KEY", None, str)

        self._configure_handlers()

    def log_event(
        self, event_type: str, level: LogLevel = "INFO", **kwargs: Any
    ) -> None:
        """Logs a structured JSON event with contextual metadata."""
        if self._closed:
            try:
                ts = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
                err_msg = {
                    "schema_version": SCHEMA_VERSION,
                    "event_type": "audit_after_close",
                    "timestamp": ts,
                }
                sys.stderr.write(
                    json.dumps(err_msg, separators=(",", ":"), ensure_ascii=False)
                    + "\n"
                )
            except Exception:
                pass
            return

        if not event_type or not isinstance(event_type, str):
            raise ValueError("event_type must be a non-empty string")

        lvl = (level or "INFO").upper()
        if lvl not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            lvl = "INFO"
        log_level = getattr(logging, lvl)

        key = (event_type, lvl)
        with self._rl_lock:
            if len(self._rl) >= self._rl_max_keys and key not in self._rl:
                return
            dq = self._rl.setdefault(key, deque())
            now = time.time()
            while dq and now - dq[0] > self._rl_window:
                dq.popleft()
            if len(dq) >= self._rl_limit:
                return
            dq.append(now)

        timestamp = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        corr = kwargs.get("corr_id") or kwargs.get("correlation_id")
        reserved = {
            "schema_version",
            "event_id",
            "event_type",
            "timestamp",
            "signature",
            "kid",
            "thread",
            "correlation_id",
            "corr_id",
            "level",
        } | self._context.keys()
        user_data = {k: v for k, v in kwargs.items() if k not in reserved}

        log_entry = {
            "schema_version": SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "timestamp": timestamp,
            "level": lvl,
            "thread": threading.current_thread().name,
            **self._context,
            **user_data,
        }
        if corr is not None:
            log_entry["correlation_id"] = corr

        try:
            log_json = json.dumps(
                log_entry,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=_json_fallback,
            )

            if len(log_json.encode("utf-8")) > self._max_event_bytes:
                log_entry["truncated"] = True
                oversized = [
                    k
                    for k, v in user_data.items()
                    if isinstance(v, str) and len(v) > 1024
                ]
                for k in oversized:
                    log_entry[k] = log_entry[k][:1024] + "...(truncated)"
                log_json = json.dumps(
                    log_entry,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    default=_json_fallback,
                )

                if len(log_json.encode("utf-8")) > self._max_event_bytes:
                    core_keys = {
                        "schema_version",
                        "event_id",
                        "event_type",
                        "timestamp",
                        "thread",
                        "app_name",
                        "environment",
                        "pid",
                    }
                    if "correlation_id" in log_entry:
                        core_keys.add("correlation_id")  # preserve trace linkage
                    log_entry = {k: log_entry[k] for k in core_keys if k in log_entry}
                    log_entry["truncated"] = True
                    log_entry["truncation_notice"] = (
                        f"payload exceeded {self._max_event_bytes} bytes"
                    )
                    log_json = json.dumps(
                        log_entry,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        default=_json_fallback,
                    )

            if self._active_hmac_key:
                if self._active_hmac_kid:
                    log_entry["kid"] = self._active_hmac_kid
                try:
                    body = json.dumps(
                        log_entry,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        default=_json_fallback,
                    ).encode("utf-8")
                    log_entry["signature"] = hmac.new(
                        self._active_hmac_key.encode("utf-8"), body, hashlib.sha256
                    ).hexdigest()
                except Exception as e:
                    log_entry["signature_error"] = str(e)

            log_json = json.dumps(
                log_entry,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=_json_fallback,
            )

            self.logger.log(log_level, log_json)

        except Exception as e:
            fallback_entry = {
                "schema_version": SCHEMA_VERSION,
                "event_type": "audit_serialization_error",
                "timestamp": timestamp,
                "error": str(e),
            }
            fallback_json = json.dumps(
                fallback_entry, separators=(",", ":"), ensure_ascii=False
            )
            with self._write_lock:
                try:
                    self.logger.error(fallback_json)
                except Exception:
                    try:
                        sys.stderr.write(fallback_json + "\n")
                    except Exception:
                        pass

    def log_exception(self, event_type: str, exc: Exception, **kwargs: Any) -> None:
        try:
            tb_lines = traceback.TracebackException.from_exception(exc).format()
            exc_info = {
                "exc_type": type(exc).__name__,
                "exc_message": str(exc),
                "trace_hash": hashlib.sha256(
                    "".join(tb_lines).encode("utf-8")
                ).hexdigest(),
            }
            if self._get_config_value("AUDIT_INCLUDE_TRACES", False, bool):
                exc_info["traceback"] = "".join(tb_lines)
        except Exception as format_exc:
            exc_info = {
                "exc_type": type(exc).__name__,
                "exc_message": str(exc),
                "trace_format_error": str(format_exc),
            }
        self.log_event(event_type, level="ERROR", **exc_info, **kwargs)

    def update_context(self, **kwargs: Any) -> None:
        with self._init_lock:
            self._context.update(kwargs)
            self.log_event(
                "audit_context_updated", level="DEBUG", updated_keys=list(kwargs.keys())
            )

    def reload(self) -> None:
        with self._init_lock:
            try:
                self.secrets_manager.reload()
                self._configure_logger_locked()
                self.log_event("audit_logger_reloaded", reason="secrets_reload")
            except Exception as e:
                self.log_exception("audit_logger_reload_failed", e)

    def close(self) -> None:
        with self._init_lock:
            if self._closed:
                return
            if self._listener:
                try:
                    self._listener.stop()
                except Exception:
                    pass
                self._listener = None
            for h in self._attached_handlers:
                try:
                    h.flush()
                    h.close()
                except Exception:
                    pass
            self._attached_handlers.clear()
            self.logger.removeHandler(self._queue_handler)
            self._closed = True


audit_logger = AuditLogger()

if os.name == "posix":
    import signal

    def _hup_handler(_signo, _frame):
        try:
            audit_logger.reload()
        except Exception:
            pass

    try:
        signal.signal(signal.SIGHUP, _hup_handler)
    except (ValueError, OSError):
        pass
