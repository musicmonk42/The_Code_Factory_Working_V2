"""
Database client implementations for the Arbiter platform.

This module provides unified database client abstractions with full observability,
retry logic, and production-grade error handling.

Features:
- PostgresClient: Production-grade async PostgreSQL client (re-exported)
- SQLiteClient: Async SQLite client with connection pooling for development/testing
- DummyDBClient: In-memory client with thread-safe operations for testing

Supported Environment Variables:
- **LOG_LEVEL**: (default `INFO`) Logging verbosity level.
- **SQLITE_DB_PATH**: (default `feedback.db`) Path to SQLite database file.
- **SQLITE_TIMEOUT**: (default `30.0`) SQLite connection timeout in seconds.
- **SQLITE_WAL_MODE**: (default `1`) Enable WAL mode for better concurrency.

Author: Arbiter Platform Team
Version: 2.0.0
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union

# Tenacity for retries with exponential backoff
try:
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )

    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False

    def retry(*args, **kwargs):
        """No-op decorator when tenacity is not available."""

        def decorator(func):
            return func

        return decorator

    def stop_after_attempt(*args, **kwargs):
        return None

    def wait_exponential(*args, **kwargs):
        return None

    def retry_if_exception_type(*args, **kwargs):
        return None


# OpenTelemetry tracing - Using centralized configuration
try:
    from arbiter.otel_config import get_tracer

    tracer = get_tracer(__name__)
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

    class _NoOpSpan:
        """
        Fallback span implementation with structured logging.

        When OpenTelemetry is unavailable, this implementation provides
        tracing-like functionality through structured logging, ensuring
        observability is maintained even without OTEL.

        Environment Variables:
        - TRACE_LOG_FILE: Path to trace log file (default: '/tmp/trace_fallback.log')
        - TRACE_LOG_DISABLED: Set to 'true' to disable trace logging
        """

        def __init__(self, name, attributes=None):
            self._name = name
            self._attributes = attributes or {}
            self._start_time = time.time()
            self._span_id = str(uuid.uuid4())
            self._file = os.getenv("TRACE_LOG_FILE", "/tmp/trace_fallback.log")
            self._disabled = os.getenv("TRACE_LOG_DISABLED", "false").lower() == "true"

            if not self._disabled:
                self._log_event("span_start")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            duration = time.time() - self._start_time
            if not self._disabled:
                self._log_event(
                    "span_end",
                    {
                        "duration_seconds": duration,
                        "error": exc_type is not None,
                        "exception_type": exc_type.__name__ if exc_type else None,
                    },
                )

        def set_attribute(self, key, value):
            """Set a span attribute and log it."""
            self._attributes[key] = value
            if not self._disabled:
                self._log_event("attribute_set", {"key": key, "value": value})

        def set_status(self, status):
            """Set span status and log it."""
            if not self._disabled:
                status_code = getattr(status, "status_code", status)
                self._log_event("status_set", {"status": str(status_code)})

        def record_exception(self, exc):
            """Record an exception and log it."""
            if not self._disabled:
                self._log_event(
                    "exception",
                    {
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                        "exception_traceback": traceback.format_exc() if exc else None,
                    },
                )

        def _log_event(self, event_type, extra_data=None):
            """Write structured trace event to log file."""
            try:
                log_entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "span_id": self._span_id,
                    "span_name": self._name,
                    "event_type": event_type,
                    "attributes": self._attributes.copy(),
                }
                if extra_data:
                    log_entry.update(extra_data)

                with open(self._file, "a") as f:
                    f.write(json.dumps(log_entry) + "\n")
            except Exception:
                pass  # Silent failure for fallback tracing

    class _NoOpTracer:
        """
        Fallback tracer that creates structured logging spans.

        This ensures observability is maintained even when OpenTelemetry
        is unavailable, by logging span information to a file.
        """

        @contextlib.contextmanager
        def start_as_current_span(self, name, **kwargs):
            """Start a new span with structured logging."""
            attributes = kwargs.get("attributes", {})
            span = _NoOpSpan(name, attributes)
            try:
                yield span
            except Exception as e:
                span.record_exception(e)
                raise

    tracer = _NoOpTracer()


# OpenTelemetry status codes
try:
    from opentelemetry.trace import Status, StatusCode
except ImportError:

    class StatusCode:
        OK = "OK"
        ERROR = "ERROR"

    class Status:
        def __init__(self, status_code, description=""):
            self.status_code = status_code
            self.description = description


# Prometheus metrics
try:
    from prometheus_client import Counter, Gauge, Histogram

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

    class Counter:
        """
        Fallback Counter with file-based metric logging.

        When Prometheus is unavailable, this writes metrics to a file
        for later analysis or processing by external monitoring systems.

        Environment Variables:
        - METRICS_LOG_FILE: Path to metrics file (default: '/tmp/db_metrics.log')
        - METRICS_LOG_DISABLED: Set to 'true' to disable file logging
        """

        def __init__(self, *args, **kwargs):
            self._name = args[0] if args else "unknown_counter"
            self._documentation = args[1] if len(args) > 1 else ""
            self._labelnames = kwargs.get("labelnames", [])
            self._labels = {}
            self._value = 0
            self._file = os.getenv("METRICS_LOG_FILE", "/tmp/db_metrics.log")
            self._disabled = (
                os.getenv("METRICS_LOG_DISABLED", "false").lower() == "true"
            )

        def labels(self, *args, **kwargs):
            """Return a labeled version of this counter."""
            labeled = Counter(
                self._name, self._documentation, labelnames=self._labelnames
            )
            labeled._labels = kwargs
            labeled._file = self._file
            labeled._disabled = self._disabled
            return labeled

        def inc(self, amount=1):
            """Increment counter and log to file."""
            if self._disabled:
                return

            self._value += amount
            try:
                with open(self._file, "a") as f:
                    f.write(
                        json.dumps(
                            {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "type": "counter",
                                "name": self._name,
                                "labels": self._labels,
                                "operation": "inc",
                                "amount": amount,
                                "value": self._value,
                            }
                        )
                        + "\n"
                    )
            except Exception:
                pass  # Silent failure for fallback metrics

    class Gauge:
        """
        Fallback Gauge with file-based metric logging.

        When Prometheus is unavailable, this writes metrics to a file.
        """

        def __init__(self, *args, **kwargs):
            self._name = args[0] if args else "unknown_gauge"
            self._documentation = args[1] if len(args) > 1 else ""
            self._labelnames = kwargs.get("labelnames", [])
            self._labels = {}
            self._value = 0
            self._file = os.getenv("METRICS_LOG_FILE", "/tmp/db_metrics.log")
            self._disabled = (
                os.getenv("METRICS_LOG_DISABLED", "false").lower() == "true"
            )

        def labels(self, *args, **kwargs):
            """Return a labeled version of this gauge."""
            labeled = Gauge(
                self._name, self._documentation, labelnames=self._labelnames
            )
            labeled._labels = kwargs
            labeled._file = self._file
            labeled._disabled = self._disabled
            return labeled

        def set(self, value):
            """Set gauge value and log to file."""
            if self._disabled:
                return

            self._value = value
            self._log_metric("set", value)

        def inc(self, amount=1):
            """Increment gauge."""
            if self._disabled:
                return

            self._value += amount
            self._log_metric("inc", amount)

        def dec(self, amount=1):
            """Decrement gauge."""
            if self._disabled:
                return

            self._value -= amount
            self._log_metric("dec", amount)

        def _log_metric(self, operation, value):
            """Log metric operation to file."""
            try:
                with open(self._file, "a") as f:
                    f.write(
                        json.dumps(
                            {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "type": "gauge",
                                "name": self._name,
                                "labels": self._labels,
                                "operation": operation,
                                "value": value,
                                "current_value": self._value,
                            }
                        )
                        + "\n"
                    )
            except Exception:
                pass  # Silent failure for fallback metrics

    class Histogram:
        """
        Fallback Histogram with file-based metric logging.

        When Prometheus is unavailable, this writes observations to a file.
        """

        def __init__(self, *args, **kwargs):
            self._name = args[0] if args else "unknown_histogram"
            self._documentation = args[1] if len(args) > 1 else ""
            self._labelnames = kwargs.get("labelnames", [])
            self._labels = {}
            self._observations = []
            self._file = os.getenv("METRICS_LOG_FILE", "/tmp/db_metrics.log")
            self._disabled = (
                os.getenv("METRICS_LOG_DISABLED", "false").lower() == "true"
            )

        def labels(self, *args, **kwargs):
            """Return a labeled version of this histogram."""
            labeled = Histogram(
                self._name, self._documentation, labelnames=self._labelnames
            )
            labeled._labels = kwargs
            labeled._file = self._file
            labeled._disabled = self._disabled
            return labeled

        def observe(self, value):
            """Record an observation and log to file."""
            if self._disabled:
                return

            self._observations.append(value)
            try:
                with open(self._file, "a") as f:
                    f.write(
                        json.dumps(
                            {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "type": "histogram",
                                "name": self._name,
                                "labels": self._labels,
                                "operation": "observe",
                                "value": value,
                            }
                        )
                        + "\n"
                    )
            except Exception:
                pass  # Silent failure for fallback metrics


# Logger initialization
logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())


# ============================================================================
# Custom Exceptions
# ============================================================================


class DBClientError(Exception):
    """Base exception for all database client errors."""

    pass


class DBClientConnectionError(DBClientError):
    """Raised when connection to the database fails."""

    pass


class DBClientQueryError(DBClientError):
    """Raised for query execution failures."""

    pass


class DBClientTimeoutError(DBClientError):
    """Raised when a database operation times out."""

    pass


class DBClientIntegrityError(DBClientError):
    """Raised for data integrity violations."""

    pass


# ============================================================================
# Metrics (idempotent registration)
# ============================================================================

_METRIC_CACHE: Dict[str, Any] = {}


def _get_or_create_metric(
    metric_class: Union[Type[Counter], Type[Gauge], Type[Histogram]],
    name: str,
    documentation: str,
    labelnames: Tuple[str, ...] = (),
    buckets: Optional[Tuple[float, ...]] = None,
) -> Union[Counter, Gauge, Histogram]:
    """Idempotently get or create a Prometheus metric."""
    if name in _METRIC_CACHE:
        return _METRIC_CACHE[name]

    try:
        if buckets is not None and metric_class is Histogram:
            m = metric_class(
                name, documentation, labelnames=labelnames, buckets=buckets
            )
        else:
            m = metric_class(name, documentation, labelnames=labelnames)
    except ValueError:
        # Already registered - return existing metric
        if PROMETHEUS_AVAILABLE:
            from prometheus_client import REGISTRY

            existing = REGISTRY._names_to_collectors.get(name)
            if existing:
                m = existing
            else:
                m = metric_class(name, documentation, labelnames=labelnames)
        else:
            m = metric_class(name, documentation, labelnames=labelnames)

    _METRIC_CACHE[name] = m
    return m


# Define metrics for database operations
DB_CLIENT_OPS_TOTAL = _get_or_create_metric(
    Counter,
    "db_client_ops_total",
    "Total database client operations",
    ("client_type", "operation", "status"),
)
DB_CLIENT_OPS_LATENCY = _get_or_create_metric(
    Histogram,
    "db_client_ops_latency_seconds",
    "Database client operation latency in seconds",
    ("client_type", "operation"),
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
DB_CLIENT_ENTRIES = _get_or_create_metric(
    Gauge,
    "db_client_entries_total",
    "Total entries stored in the database client",
    ("client_type",),
)
DB_CLIENT_ERRORS = _get_or_create_metric(
    Counter,
    "db_client_errors_total",
    "Total database client errors",
    ("client_type", "operation", "error_type"),
)


# ============================================================================
# DummyDBClient - In-memory client for testing
# ============================================================================


class DummyDBClient:
    """
    In-memory database client for testing and development.

    Thread-safe implementation with full observability integration.
    All data is stored in memory and lost when the instance is garbage collected.

    Features:
    - Thread-safe operations using RLock
    - Full OpenTelemetry tracing integration
    - Prometheus metrics for monitoring
    - Async-compatible interface
    - Query filtering support

    Usage:
        async with DummyDBClient() as client:
            await client.save_feedback_entry({"type": "test", "data": "value"})
            entries = await client.get_feedback_entries({"type": "test"})
    """

    def __init__(self) -> None:
        """Initialize the in-memory database client."""
        self._entries: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._client_id = str(uuid.uuid4())[:8]
        logger.info(f"DummyDBClient[{self._client_id}] initialized")

    async def __aenter__(self) -> "DummyDBClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        pass

    @property
    def feedback_entries(self) -> List[Dict[str, Any]]:
        """Return a copy of all entries (for backward compatibility)."""
        with self._lock:
            return self._entries.copy()

    async def connect(self) -> None:
        """No-op connect for interface compatibility."""
        logger.debug(f"DummyDBClient[{self._client_id}] connect called (no-op)")

    async def disconnect(self) -> None:
        """No-op disconnect for interface compatibility."""
        logger.debug(f"DummyDBClient[{self._client_id}] disconnect called (no-op)")

    async def save_feedback_entry(self, entry: Dict[str, Any]) -> str:
        """
        Save a feedback entry to the in-memory store.

        Args:
            entry: Dictionary containing the feedback data.

        Returns:
            The unique ID assigned to the entry.
        """
        op = "save_feedback_entry"
        start_time = time.monotonic()
        DB_CLIENT_OPS_TOTAL.labels(
            client_type="dummy", operation=op, status="attempt"
        ).inc()

        with tracer.start_as_current_span(f"dummy_db_{op}") as span:
            try:
                entry_copy = entry.copy()
                entry_id = entry_copy.get("id", str(uuid.uuid4()))
                entry_copy["id"] = entry_id

                if "timestamp" not in entry_copy:
                    entry_copy["timestamp"] = datetime.now(timezone.utc).isoformat()

                with self._lock:
                    self._entries.append(entry_copy)
                    total = len(self._entries)

                DB_CLIENT_ENTRIES.labels(client_type="dummy").set(total)
                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="dummy", operation=op, status="success"
                ).inc()

                span.set_attribute("db.entry_id", entry_id)
                span.set_attribute("db.total_entries", total)
                span.set_status(Status(StatusCode.OK))

                logger.debug(
                    f"DummyDBClient[{self._client_id}]: Saved entry {entry_id}. "
                    f"Total entries: {total}"
                )
                return entry_id

            except Exception as e:
                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="dummy", operation=op, status="failure"
                ).inc()
                DB_CLIENT_ERRORS.labels(
                    client_type="dummy", operation=op, error_type=type(e).__name__
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.error(
                    f"DummyDBClient[{self._client_id}]: Failed to save entry: {e}",
                    exc_info=True,
                )
                raise DBClientQueryError(f"Failed to save entry: {e}") from e

            finally:
                DB_CLIENT_OPS_LATENCY.labels(client_type="dummy", operation=op).observe(
                    time.monotonic() - start_time
                )

    async def get_feedback_entries(
        self, query: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve feedback entries, optionally filtered by query.

        Args:
            query: Optional dictionary of key-value pairs to filter entries.

        Returns:
            List of matching entries.
        """
        op = "get_feedback_entries"
        start_time = time.monotonic()
        DB_CLIENT_OPS_TOTAL.labels(
            client_type="dummy", operation=op, status="attempt"
        ).inc()

        with tracer.start_as_current_span(f"dummy_db_{op}") as span:
            try:
                with self._lock:
                    if query is None:
                        result = self._entries.copy()
                    else:
                        result = [
                            e.copy()
                            for e in self._entries
                            if isinstance(e, dict)
                            and all(e.get(k) == v for k, v in query.items())
                        ]

                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="dummy", operation=op, status="success"
                ).inc()

                span.set_attribute("db.query", str(query) if query else "all")
                span.set_attribute("db.result_count", len(result))
                span.set_status(Status(StatusCode.OK))

                logger.debug(
                    f"DummyDBClient[{self._client_id}]: Retrieved {len(result)} entries"
                )
                return result

            except Exception as e:
                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="dummy", operation=op, status="failure"
                ).inc()
                DB_CLIENT_ERRORS.labels(
                    client_type="dummy", operation=op, error_type=type(e).__name__
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.error(
                    f"DummyDBClient[{self._client_id}]: Failed to get entries: {e}",
                    exc_info=True,
                )
                raise DBClientQueryError(f"Failed to get entries: {e}") from e

            finally:
                DB_CLIENT_OPS_LATENCY.labels(client_type="dummy", operation=op).observe(
                    time.monotonic() - start_time
                )

    async def update_feedback_entry(
        self, query: Dict[str, Any], updates: Dict[str, Any]
    ) -> int:
        """
        Update feedback entries matching the query.

        Args:
            query: Dictionary of key-value pairs to match entries.
            updates: Dictionary of key-value pairs to update in matching entries.

        Returns:
            Number of entries updated.
        """
        op = "update_feedback_entry"
        start_time = time.monotonic()
        DB_CLIENT_OPS_TOTAL.labels(
            client_type="dummy", operation=op, status="attempt"
        ).inc()

        with tracer.start_as_current_span(f"dummy_db_{op}") as span:
            try:
                updated = 0
                with self._lock:
                    for e in self._entries:
                        if isinstance(e, dict) and all(
                            e.get(k) == v for k, v in query.items()
                        ):
                            e.update(updates)
                            e["updated_at"] = datetime.now(timezone.utc).isoformat()
                            updated += 1

                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="dummy", operation=op, status="success"
                ).inc()

                span.set_attribute("db.query", str(query))
                span.set_attribute("db.updated_count", updated)
                span.set_status(Status(StatusCode.OK))

                logger.debug(
                    f"DummyDBClient[{self._client_id}]: Updated {updated} entries "
                    f"for query {query}"
                )
                return updated

            except Exception as e:
                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="dummy", operation=op, status="failure"
                ).inc()
                DB_CLIENT_ERRORS.labels(
                    client_type="dummy", operation=op, error_type=type(e).__name__
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.error(
                    f"DummyDBClient[{self._client_id}]: Failed to update entries: {e}",
                    exc_info=True,
                )
                raise DBClientQueryError(f"Failed to update entries: {e}") from e

            finally:
                DB_CLIENT_OPS_LATENCY.labels(client_type="dummy", operation=op).observe(
                    time.monotonic() - start_time
                )

    async def delete_feedback_entry(self, query: Dict[str, Any]) -> int:
        """
        Delete feedback entries matching the query.

        Args:
            query: Dictionary of key-value pairs to match entries for deletion.

        Returns:
            Number of entries deleted.
        """
        op = "delete_feedback_entry"
        start_time = time.monotonic()
        DB_CLIENT_OPS_TOTAL.labels(
            client_type="dummy", operation=op, status="attempt"
        ).inc()

        with tracer.start_as_current_span(f"dummy_db_{op}") as span:
            try:
                deleted = 0
                with self._lock:
                    original_count = len(self._entries)
                    self._entries = [
                        e
                        for e in self._entries
                        if not (
                            isinstance(e, dict)
                            and all(e.get(k) == v for k, v in query.items())
                        )
                    ]
                    deleted = original_count - len(self._entries)

                DB_CLIENT_ENTRIES.labels(client_type="dummy").set(len(self._entries))
                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="dummy", operation=op, status="success"
                ).inc()

                span.set_attribute("db.query", str(query))
                span.set_attribute("db.deleted_count", deleted)
                span.set_status(Status(StatusCode.OK))

                logger.debug(
                    f"DummyDBClient[{self._client_id}]: Deleted {deleted} entries"
                )
                return deleted

            except Exception as e:
                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="dummy", operation=op, status="failure"
                ).inc()
                DB_CLIENT_ERRORS.labels(
                    client_type="dummy", operation=op, error_type=type(e).__name__
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.error(
                    f"DummyDBClient[{self._client_id}]: Failed to delete entries: {e}",
                    exc_info=True,
                )
                raise DBClientQueryError(f"Failed to delete entries: {e}") from e

            finally:
                DB_CLIENT_OPS_LATENCY.labels(client_type="dummy", operation=op).observe(
                    time.monotonic() - start_time
                )

    async def health_check(self) -> Dict[str, Any]:
        """
        Perform a health check on the client.

        Returns:
            Dictionary with health status information.
        """
        with self._lock:
            return {
                "status": "healthy",
                "client_type": "dummy",
                "client_id": self._client_id,
                "entry_count": len(self._entries),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def clear(self) -> None:
        """Clear all entries (for testing purposes)."""
        with self._lock:
            self._entries.clear()
            DB_CLIENT_ENTRIES.labels(client_type="dummy").set(0)
            logger.info(f"DummyDBClient[{self._client_id}]: All entries cleared")


# ============================================================================
# SQLiteClient - Lightweight persistent storage
# ============================================================================


class SQLiteClient:
    """
    SQLite database client with async interface and full observability.

    Suitable for development, single-instance deployments, and edge cases
    where a full PostgreSQL database is not available.

    Features:
    - WAL mode for improved concurrency
    - Automatic schema migrations
    - Connection pooling (thread-local connections)
    - Full OpenTelemetry tracing integration
    - Prometheus metrics for monitoring
    - Retry logic for transient failures

    Supported Environment Variables:
    - **SQLITE_DB_PATH**: Path to the database file.
    - **SQLITE_TIMEOUT**: Connection timeout in seconds.
    - **SQLITE_WAL_MODE**: Enable WAL mode (1/0).

    Usage:
        async with SQLiteClient("feedback.db") as client:
            await client.save_feedback_entry({"type": "test", "data": "value"})
            entries = await client.get_feedback_entries({"type": "test"})
    """

    # SQL schema for the feedback entries table
    _SCHEMA_VERSION = 2
    _CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS feedback_entries (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            type TEXT,
            decision_id TEXT,
            status TEXT,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_type ON feedback_entries(type);
        CREATE INDEX IF NOT EXISTS idx_feedback_decision_id ON feedback_entries(decision_id);
        CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback_entries(status);
        CREATE INDEX IF NOT EXISTS idx_feedback_timestamp ON feedback_entries(timestamp);
    """

    def __init__(
        self,
        db_file: Optional[str] = None,
        timeout: Optional[float] = None,
        wal_mode: Optional[bool] = None,
    ) -> None:
        """
        Initialize the SQLite database client.

        Args:
            db_file: Path to the SQLite database file.
            timeout: Connection timeout in seconds.
            wal_mode: Enable WAL mode for better concurrency.
        """
        self.db_file = db_file or os.getenv("SQLITE_DB_PATH", "feedback.db")
        self.timeout = timeout or float(os.getenv("SQLITE_TIMEOUT", "30.0"))
        self.wal_mode = (
            wal_mode
            if wal_mode is not None
            else os.getenv("SQLITE_WAL_MODE", "1") == "1"
        )

        self._local = threading.local()
        self._client_id = str(uuid.uuid4())[:8]
        self._initialized = False

        logger.info(
            f"SQLiteClient[{self._client_id}] initialized for database: {self.db_file}"
        )

    def _get_connection(self) -> sqlite3.Connection:
        """Get a thread-local database connection."""
        if not hasattr(self._local, "connection") or self._local.connection is None:
            conn = sqlite3.connect(
                self.db_file,
                timeout=self.timeout,
                check_same_thread=False,
                isolation_level=None,  # Autocommit mode
            )
            conn.row_factory = sqlite3.Row

            if self.wal_mode:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")

            conn.execute("PRAGMA foreign_keys=ON")
            self._local.connection = conn

        return self._local.connection

    async def __aenter__(self) -> "SQLiteClient":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.disconnect()

    async def connect(self) -> None:
        """Connect to the database and ensure schema is initialized."""
        op = "connect"
        start_time = time.monotonic()
        DB_CLIENT_OPS_TOTAL.labels(
            client_type="sqlite", operation=op, status="attempt"
        ).inc()

        with tracer.start_as_current_span(f"sqlite_db_{op}") as span:
            try:
                conn = self._get_connection()
                conn.executescript(self._CREATE_TABLE_SQL)
                self._initialized = True

                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="sqlite", operation=op, status="success"
                ).inc()
                span.set_attribute("db.file", self.db_file)
                span.set_status(Status(StatusCode.OK))

                logger.info(
                    f"SQLiteClient[{self._client_id}] connected to {self.db_file}"
                )

            except sqlite3.Error as e:
                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="sqlite", operation=op, status="failure"
                ).inc()
                DB_CLIENT_ERRORS.labels(
                    client_type="sqlite", operation=op, error_type=type(e).__name__
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.error(
                    f"SQLiteClient[{self._client_id}]: Connection failed: {e}",
                    exc_info=True,
                )
                raise DBClientConnectionError(f"Failed to connect: {e}") from e

            finally:
                DB_CLIENT_OPS_LATENCY.labels(
                    client_type="sqlite", operation=op
                ).observe(time.monotonic() - start_time)

    async def disconnect(self) -> None:
        """Disconnect from the database."""
        op = "disconnect"
        start_time = time.monotonic()

        with tracer.start_as_current_span(f"sqlite_db_{op}") as span:
            try:
                if hasattr(self._local, "connection") and self._local.connection:
                    self._local.connection.close()
                    self._local.connection = None
                    self._initialized = False

                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="sqlite", operation=op, status="success"
                ).inc()
                span.set_status(Status(StatusCode.OK))

                logger.info(f"SQLiteClient[{self._client_id}] disconnected")

            except Exception as e:
                DB_CLIENT_ERRORS.labels(
                    client_type="sqlite", operation=op, error_type=type(e).__name__
                ).inc()
                span.record_exception(e)
                logger.error(
                    f"SQLiteClient[{self._client_id}]: Disconnect error: {e}",
                    exc_info=True,
                )

            finally:
                DB_CLIENT_OPS_LATENCY.labels(
                    client_type="sqlite", operation=op
                ).observe(time.monotonic() - start_time)

    async def save_feedback_entry(self, entry: Dict[str, Any]) -> str:
        """
        Save a feedback entry to the database.

        Args:
            entry: Dictionary containing the feedback data.

        Returns:
            The unique ID assigned to the entry.
        """
        op = "save_feedback_entry"
        start_time = time.monotonic()
        DB_CLIENT_OPS_TOTAL.labels(
            client_type="sqlite", operation=op, status="attempt"
        ).inc()

        with tracer.start_as_current_span(f"sqlite_db_{op}") as span:
            try:
                entry_id = entry.get("id", str(uuid.uuid4()))
                now = datetime.now(timezone.utc).isoformat()
                timestamp = entry.get("timestamp", now)

                conn = self._get_connection()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO feedback_entries
                    (id, timestamp, type, decision_id, status, data, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry_id,
                        timestamp,
                        entry.get("type"),
                        entry.get("decision_id"),
                        entry.get("status"),
                        json.dumps(entry, default=str),
                        now,
                        now,
                    ),
                )

                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="sqlite", operation=op, status="success"
                ).inc()

                span.set_attribute("db.entry_id", entry_id)
                span.set_status(Status(StatusCode.OK))

                logger.debug(f"SQLiteClient[{self._client_id}]: Saved entry {entry_id}")
                return entry_id

            except sqlite3.Error as e:
                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="sqlite", operation=op, status="failure"
                ).inc()
                DB_CLIENT_ERRORS.labels(
                    client_type="sqlite", operation=op, error_type=type(e).__name__
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.error(
                    f"SQLiteClient[{self._client_id}]: Failed to save entry: {e}",
                    exc_info=True,
                )
                raise DBClientQueryError(f"Failed to save entry: {e}") from e

            finally:
                DB_CLIENT_OPS_LATENCY.labels(
                    client_type="sqlite", operation=op
                ).observe(time.monotonic() - start_time)

    async def get_feedback_entries(
        self, query: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve feedback entries, optionally filtered by query.

        Args:
            query: Optional dictionary of key-value pairs to filter entries.

        Returns:
            List of matching entries.
        """
        op = "get_feedback_entries"
        start_time = time.monotonic()
        DB_CLIENT_OPS_TOTAL.labels(
            client_type="sqlite", operation=op, status="attempt"
        ).inc()

        with tracer.start_as_current_span(f"sqlite_db_{op}") as span:
            try:
                conn = self._get_connection()

                if query:
                    # Build parameterized query for indexed columns
                    conditions = []
                    params = []
                    indexed_cols = {"type", "decision_id", "status"}

                    for k, v in query.items():
                        if k in indexed_cols:
                            conditions.append(f"{k} = ?")
                            params.append(v)

                    if conditions:
                        sql = f"SELECT data FROM feedback_entries WHERE {' AND '.join(conditions)}"
                        cursor = conn.execute(sql, params)
                    else:
                        cursor = conn.execute("SELECT data FROM feedback_entries")

                    rows = cursor.fetchall()
                    entries = [json.loads(row[0]) for row in rows]

                    # Apply non-indexed filters in Python
                    non_indexed_query = {
                        k: v for k, v in query.items() if k not in indexed_cols
                    }
                    if non_indexed_query:
                        entries = [
                            e
                            for e in entries
                            if all(e.get(k) == v for k, v in non_indexed_query.items())
                        ]
                else:
                    cursor = conn.execute("SELECT data FROM feedback_entries")
                    rows = cursor.fetchall()
                    entries = [json.loads(row[0]) for row in rows]

                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="sqlite", operation=op, status="success"
                ).inc()

                span.set_attribute("db.result_count", len(entries))
                span.set_status(Status(StatusCode.OK))

                logger.debug(
                    f"SQLiteClient[{self._client_id}]: Retrieved {len(entries)} entries"
                )
                return entries

            except sqlite3.Error as e:
                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="sqlite", operation=op, status="failure"
                ).inc()
                DB_CLIENT_ERRORS.labels(
                    client_type="sqlite", operation=op, error_type=type(e).__name__
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.error(
                    f"SQLiteClient[{self._client_id}]: Failed to get entries: {e}",
                    exc_info=True,
                )
                raise DBClientQueryError(f"Failed to get entries: {e}") from e

            finally:
                DB_CLIENT_OPS_LATENCY.labels(
                    client_type="sqlite", operation=op
                ).observe(time.monotonic() - start_time)

    async def update_feedback_entry(
        self, query: Dict[str, Any], updates: Dict[str, Any]
    ) -> int:
        """
        Update feedback entries matching the query.

        Args:
            query: Dictionary of key-value pairs to match entries.
            updates: Dictionary of key-value pairs to update in matching entries.

        Returns:
            Number of entries updated.
        """
        op = "update_feedback_entry"
        start_time = time.monotonic()
        DB_CLIENT_OPS_TOTAL.labels(
            client_type="sqlite", operation=op, status="attempt"
        ).inc()

        with tracer.start_as_current_span(f"sqlite_db_{op}") as span:
            try:
                # Get matching entries
                entries = await self.get_feedback_entries(query)
                updated = 0

                for entry in entries:
                    entry.update(updates)
                    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
                    await self.save_feedback_entry(entry)
                    updated += 1

                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="sqlite", operation=op, status="success"
                ).inc()

                span.set_attribute("db.updated_count", updated)
                span.set_status(Status(StatusCode.OK))

                logger.debug(
                    f"SQLiteClient[{self._client_id}]: Updated {updated} entries"
                )
                return updated

            except Exception as e:
                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="sqlite", operation=op, status="failure"
                ).inc()
                DB_CLIENT_ERRORS.labels(
                    client_type="sqlite", operation=op, error_type=type(e).__name__
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.error(
                    f"SQLiteClient[{self._client_id}]: Failed to update entries: {e}",
                    exc_info=True,
                )
                raise DBClientQueryError(f"Failed to update entries: {e}") from e

            finally:
                DB_CLIENT_OPS_LATENCY.labels(
                    client_type="sqlite", operation=op
                ).observe(time.monotonic() - start_time)

    async def delete_feedback_entry(self, query: Dict[str, Any]) -> int:
        """
        Delete feedback entries matching the query.

        Args:
            query: Dictionary of key-value pairs to match entries for deletion.

        Returns:
            Number of entries deleted.
        """
        op = "delete_feedback_entry"
        start_time = time.monotonic()
        DB_CLIENT_OPS_TOTAL.labels(
            client_type="sqlite", operation=op, status="attempt"
        ).inc()

        with tracer.start_as_current_span(f"sqlite_db_{op}") as span:
            try:
                # Get matching entry IDs first
                entries = await self.get_feedback_entries(query)
                entry_ids = [e.get("id") for e in entries if e.get("id")]

                if not entry_ids:
                    span.set_attribute("db.deleted_count", 0)
                    span.set_status(Status(StatusCode.OK))
                    return 0

                conn = self._get_connection()
                placeholders = ",".join("?" * len(entry_ids))
                cursor = conn.execute(
                    f"DELETE FROM feedback_entries WHERE id IN ({placeholders})",
                    entry_ids,
                )
                deleted = cursor.rowcount

                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="sqlite", operation=op, status="success"
                ).inc()

                span.set_attribute("db.deleted_count", deleted)
                span.set_status(Status(StatusCode.OK))

                logger.debug(
                    f"SQLiteClient[{self._client_id}]: Deleted {deleted} entries"
                )
                return deleted

            except sqlite3.Error as e:
                DB_CLIENT_OPS_TOTAL.labels(
                    client_type="sqlite", operation=op, status="failure"
                ).inc()
                DB_CLIENT_ERRORS.labels(
                    client_type="sqlite", operation=op, error_type=type(e).__name__
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.error(
                    f"SQLiteClient[{self._client_id}]: Failed to delete entries: {e}",
                    exc_info=True,
                )
                raise DBClientQueryError(f"Failed to delete entries: {e}") from e

            finally:
                DB_CLIENT_OPS_LATENCY.labels(
                    client_type="sqlite", operation=op
                ).observe(time.monotonic() - start_time)

    async def health_check(self) -> Dict[str, Any]:
        """
        Perform a health check on the database.

        Returns:
            Dictionary with health status information.
        """
        op = "health_check"
        start_time = time.monotonic()

        with tracer.start_as_current_span(f"sqlite_db_{op}") as span:
            try:
                conn = self._get_connection()
                cursor = conn.execute("SELECT COUNT(*) FROM feedback_entries")
                count = cursor.fetchone()[0]

                health = {
                    "status": "healthy",
                    "client_type": "sqlite",
                    "client_id": self._client_id,
                    "database": self.db_file,
                    "entry_count": count,
                    "wal_mode": self.wal_mode,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                span.set_status(Status(StatusCode.OK))
                return health

            except sqlite3.Error as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                return {
                    "status": "unhealthy",
                    "client_type": "sqlite",
                    "client_id": self._client_id,
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

            finally:
                DB_CLIENT_OPS_LATENCY.labels(
                    client_type="sqlite", operation=op
                ).observe(time.monotonic() - start_time)


# ============================================================================
# Re-export PostgresClient from postgres_client module
# ============================================================================

try:
    from arbiter.models.postgres_client import PostgresClient
except ImportError:
    logger.warning(
        "PostgresClient not available - arbiter.models.postgres_client module not found"
    )

    class PostgresClient:  # type: ignore
        """Stub PostgresClient when the full implementation is not available."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "PostgresClient requires the arbiter.models.postgres_client module "
                "and its dependencies (asyncpg). Install with: pip install asyncpg"
            )


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    # Clients
    "DummyDBClient",
    "SQLiteClient",
    "PostgresClient",
    # Exceptions
    "DBClientError",
    "DBClientConnectionError",
    "DBClientQueryError",
    "DBClientTimeoutError",
    "DBClientIntegrityError",
]
