import asyncio
import logging
import json
import uuid
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Union, Tuple, Type
import os
import ssl
import re
import hashlib
import subprocess
import contextlib

import asyncpg
from asyncpg.pool import Pool
from asyncpg import exceptions as asyncpg_exceptions

from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type, wait_exponential

from prometheus_client import Counter, Gauge, Histogram, REGISTRY, start_http_server

# Import centralized OpenTelemetry configuration
from arbiter.otel_config import get_tracer
from opentelemetry.trace import Status, StatusCode

# Logger initialization
logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

# Custom Exceptions


class ConnectionError(Exception):
    """Database connection error."""
    pass

class QueryError(Exception):
    """Database query error."""
    pass

class PostgresClientError(Exception):
    """Base exception for PostgresClient errors."""
    pass

class PostgresClientConnectionError(PostgresClientError):
    """Raised when connection to the database fails."""
    pass

class PostgresClientSchemaError(PostgresClientError):
    """Raised for schema-related issues."""
    pass

class PostgresClientQueryError(PostgresClientError):
    """Raised for query execution failures."""
    pass

class PostgresClientTimeoutError(PostgresClientError):
    """Raised when a query times out."""
    pass

# Get tracer using centralized configuration
tracer = get_tracer(__name__)

_METRIC_CACHE: Dict[str, Any] = {}

def _get_or_create_metric(metric_class: Union[Type[Counter], Type[Gauge], Type[Histogram]],
                          name: str, documentation: str, labelnames: Tuple[str, ...] = (), buckets: Optional[Tuple[float, ...]] = None):
    """
    Idempotently get or create a Prometheus metric.
    """
    if name in _METRIC_CACHE:
        return _METRIC_CACHE[name]
    try:
        if buckets is not None and metric_class is Histogram:
            m = metric_class(name, documentation, labelnames=labelnames, buckets=buckets)
        else:
            m = metric_class(name, documentation, labelnames=labelnames)
    except ValueError:
        # Already registered by someone else; get it from the registry if possible
        existing = REGISTRY._names_to_collectors.get(name)  # best available, yes it's private
        if not existing:
            raise RuntimeError(f"Metric registry missing {name}")
        m = existing
    _METRIC_CACHE[name] = m
    return m

# Metrics for PostgresClient Operations
DB_CALLS_TOTAL = _get_or_create_metric(Counter, "db_calls_total", "Total database calls", ["db_type", "operation", "table", "status"])
DB_CALLS_ERRORS = _get_or_create_metric(Counter, "db_calls_errors", "Database call errors", ["db_type", "operation", "table", "error_type"])
DB_CALL_LATENCY_SECONDS = _get_or_create_metric(
    Histogram,
    "db_call_latency_seconds",
    "Database call latency in seconds",
    ["db_type", "operation", "table", "status"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
)
DB_CONNECTIONS_CURRENT = _get_or_create_metric(Gauge, "db_connections_current", "Current number of active DB connections", ["db_type"])
DB_CONNECTIONS_IN_USE = _get_or_create_metric(Gauge, "db_connections_in_use", "Number of in-use DB connections from the pool", ["db_type"])
DB_TABLE_ROWS = _get_or_create_metric(Gauge, "db_table_rows_total", "Total rows in a table", ["db_type", "table"])


FATAL_EXC = (
    asyncpg_exceptions.InvalidPasswordError,
    asyncpg_exceptions.UndefinedTableError,
    asyncpg_exceptions.UndefinedColumnError,
    asyncpg_exceptions.DuplicateTableError,
    asyncpg_exceptions.DuplicateColumnError,
    asyncpg_exceptions.DataError,
    asyncpg_exceptions.NotNullViolationError,
    asyncpg_exceptions.ForeignKeyViolationError,
    asyncpg_exceptions.InvalidCatalogNameError,
    asyncpg_exceptions.InsufficientPrivilegeError,
)
TRANSIENT_EXC = (
    asyncpg_exceptions.CannotConnectNowError,
    asyncpg_exceptions.TooManyConnectionsError,
    asyncpg_exceptions.ConnectionDoesNotExistError,
    asyncpg_exceptions.PostgresConnectionError,
    asyncpg_exceptions.InterfaceError,
    asyncpg_exceptions.SerializationError,
    asyncpg_exceptions.DeadlockDetectedError,
    asyncio.TimeoutError,
    OSError,
    ConnectionError,
    ConnectionResetError,
)

def _sanitize_dsn(dsn: str) -> str:
    """Removes password from a DSN string for safe logging."""
    return re.sub(r"://[^:]+:[^@]+@", "://user:***@", dsn)

class PostgresClient:
    """
    An asynchronous PostgreSQL client with connection pooling, schema management,
    and integrated observability (Prometheus metrics and OpenTelemetry tracing).

    Supported Environment Variables:

    Database
    - **DATABASE_URL**: (required if no db_url passed) The PostgreSQL connection string.
    - **PG_POOL_MIN_SIZE**: (default `1`) Minimum number of connections in the pool.
    - **PG_POOL_MAX_SIZE**: (default `10`) Maximum number of connections in the pool.
    - **PG_POOL_TIMEOUT**: (default `30`) Timeout in seconds for acquiring a connection from the pool.
    - **PG_QUERY_TIMEOUT**: (default `0`) Timeout in seconds for individual queries. `0` disables it.
    - **PG_STATEMENT_TIMEOUT_MS**: (optional) Sets a server-side timeout for statements in milliseconds.
    - **PG_SSL_MODE**: (default `prefer`) SSL mode (`require`, `allow`, `prefer`, `disable`).
    - **AUTO_MIGRATE**: (default `0`) `1` to automatically create tables on connect.
    - **PG_MAX_PARAMS**: (default `65535`) Maximum query parameters for batch operations.
    - **PG_MAX_ROWS_PER_CHUNK**: (default `1000`) Max rows per chunk for batch saves.
    - **PG_MAX_LIMIT**: (default `10000`) Maximum allowed `LIMIT` for `load_all` queries.
    - **PG_DEFAULT_LIMIT**: (default `1000`) Default `LIMIT` for `load_all` if no `limit` is specified.
    - **PG_GLOBAL_MAX_ROWS**: (default `50000`) A global cap on the number of rows a `load_all` query can return.
    - **PG_COPY_BATCH_THRESHOLD**: (default `1000`) Number of records at which to switch from UPSERT to COPY for `save_many`.

    Observability
    - **SFE_OTEL_EXPORTER_TYPE**: (default `console`) `otlp` or `console` for OpenTelemetry.
    - **SFE_LOG_SQL**: (default `0`) `1` to enable logging of SQL statements.
    - **LOG_LEVEL**: (default `INFO`) Logging level.
    - **APP_NAME**: (default `sfe-postgres-client`) Sets the PostgreSQL application_name.
    - **METRICS_PORT**: (default `0`) Port to expose Prometheus metrics on. `0` disables.

    Environment
    - **ENV**: (default `dev`) `prod` affects strictness of `PG_SSL_MODE='allow'`.
    - **HEALTH_CHECK_INTERVAL**: (default `60.0`) Interval in seconds for the background health check.

    JSONB Update Contract for `.update()`:
    - **To merge/patch keys**: pass a dict (e.g., `{"key1": "value1"}`). This uses the `||` JSONB merge operator.
    - **To unset/delete keys**: pass a dict with a special `$unset` key (e.g., `{"$unset": ["key1", "key2"]}`).
    - **To replace the entire column**: pass a dict with a special `$replace` key (e.g., `{"$replace": {"new_key": "new_value"}}`).
    - **To set the column to SQL `NULL`**: pass the Python value `None`.
    """

    _TABLE_SCHEMAS = {
        "feedback": {
            "columns": ["id", "type", "data", "timestamp"],
            "jsonb_columns": ["data"],
            "pk": ["id"],
            "schema_sql": """
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    data JSONB NOT NULL,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_feedback_type ON feedback (type);
                CREATE INDEX IF NOT EXISTS idx_feedback_timestamp ON feedback (timestamp);
                CREATE INDEX IF NOT EXISTS idx_feedback_data ON feedback USING GIN (data);
            """
        },
        "agent_knowledge": {
            "columns": ["domain", "key", "value", "timestamp", "source", "user_id", "version", "diff", "merkle_leaf", "merkle_proof", "merkle_root"],
            "jsonb_columns": ["value", "diff", "merkle_proof"],
            "pk": ["domain", "key"],
            "schema_sql": """
                CREATE TABLE IF NOT EXISTS agent_knowledge (
                    domain TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value JSONB NOT NULL,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    source TEXT,
                    user_id TEXT,
                    version INTEGER,
                    diff JSONB,
                    merkle_leaf TEXT,
                    merkle_proof JSONB,
                    merkle_root TEXT,
                    PRIMARY KEY (domain, key)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_knowledge_timestamp ON agent_knowledge (timestamp);
                CREATE INDEX IF NOT EXISTS idx_agent_knowledge_domain ON agent_knowledge (domain);
                CREATE INDEX IF NOT EXISTS idx_agent_knowledge_value ON agent_knowledge USING GIN (value);
            """
        },
        "agent_states": {
            "columns": ["session_id", "state", "last_updated"],
            "jsonb_columns": ["state"],
            "pk": ["session_id"],
            "schema_sql": """
                CREATE TABLE IF NOT EXISTS agent_states (
                    session_id TEXT PRIMARY KEY,
                    state JSONB NOT NULL,
                    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_agent_states_last_updated ON agent_states (last_updated);
                CREATE INDEX IF NOT EXISTS idx_agent_states_state ON agent_states USING GIN (state);
            """
        },
        "audit_events": {
            "columns": ["id", "timestamp", "event_type", "details", "host", "previous_log_hash", "hash", "signatures", "correlation_id"],
            "jsonb_columns": ["details", "signatures"],
            "pk": ["id"],
            "schema_sql": """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    event_type TEXT NOT NULL,
                    details JSONB,
                    host TEXT,
                    previous_log_hash TEXT,
                    hash TEXT UNIQUE,
                    signatures JSONB,
                    correlation_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_audit_events_timestamp ON audit_events (timestamp);
                CREATE INDEX IF NOT EXISTS idx_audit_events_event_type ON audit_events (event_type);
                CREATE INDEX IF NOT EXISTS idx_audit_events_correlation_id ON audit_events (correlation_id);
                CREATE INDEX IF NOT EXISTS idx_audit_events_details ON audit_events USING GIN (details);
            """
        }
    }

    def __init__(self, db_url: Optional[str] = None):
        """Initializes the PostgresClient."""
        self.db_url = db_url or os.getenv("DATABASE_URL")
        if not self.db_url:
            raise ValueError("Database URL (db_url or DATABASE_URL env var) must be provided.")

        self._pool: Optional[Pool] = None
        self.db_type = "postgresql"
        self._connect_lock = asyncio.Lock()
        self._is_closed = True
        self._health_check_task: Optional[asyncio.Task] = None
        logger.info(f"PostgresClient initialized for URL: {_sanitize_dsn(self.db_url)}")

        metrics_port = int(os.getenv("METRICS_PORT", "0"))
        if metrics_port > 0:
            logger.info(f"Starting Prometheus metrics server on port {metrics_port}.")
            start_http_server(metrics_port)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()

    async def ping(self) -> bool:
        """Performs a simple query to check database connection health."""
        if self._pool is None or self._pool.is_closed():
            logger.debug("Ping failed: pool is not initialized or closed.")
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1;")
            return True
        except Exception as e:
            logger.debug(f"Ping failed with exception: {e}")
            return False

    async def reconnect(self) -> None:
        """Attempts to reconnect to the database if the pool is unhealthy."""
        async with self._connect_lock:
            if await self.ping():
                logger.debug("Connection pool is healthy, no reconnect needed.")
                return
            logger.warning("Connection pool unhealthy, attempting reconnect.")
            await self.disconnect()
            await self.connect()

    async def _start_health_check(self, interval: float = 60.0) -> None:
        """Runs a background task to periodically check pool health."""
        while not self._is_closed:
            try:
                if not await self.ping():
                    await self.reconnect()
                await self.update_table_row_counts()
            except Exception as e:
                logger.error(f"Health check failed: {e}", exc_info=True)
            await asyncio.sleep(interval)

    async def update_table_row_counts(self) -> None:
        """Updates row count metrics for all tables."""
        if self._pool is None or self._is_closed:
            return
        for table in self._TABLE_SCHEMAS.keys():
            try:
                query = f"SELECT COUNT(*) FROM {table};"
                async with self._pool.acquire() as conn:
                    count = await conn.fetchval(query)
                DB_TABLE_ROWS.labels(db_type=self.db_type, table=table).set(count)
            except Exception as e:
                logger.error(f"Failed to update row count for {table}: {e}", exc_info=True)

    async def _init_conn(self, conn):
        """Sets per-connection settings like timeout, timezone, and application name."""
        stmt_timeout_ms = os.getenv("PG_STATEMENT_TIMEOUT_MS")
        if stmt_timeout_ms:
            await conn.execute(f"SET statement_timeout = {int(stmt_timeout_ms)};")

        await conn.execute("SET TIME ZONE 'UTC';")
        app_name = os.getenv("APP_NAME", "sfe-postgres-client").replace("'", "''")
        await conn.execute(f"SET application_name = '{app_name}';")

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_random_exponential(multiplier=1, max=10),
        retry=retry_if_exception_type(TRANSIENT_EXC),
        reraise=True
    )
    async def connect(self) -> None:
        """
        Establishes a connection pool to the PostgreSQL database and
        ensures all necessary tables exist with the correct schema.
        Includes retries for transient connection issues.
        """
        async with self._connect_lock:
            if self._pool is not None and not self._pool.is_closed():
                logger.info("PostgreSQL client already connected.")
                return

            with tracer.start_as_current_span("db_connect") as span:
                span.set_attribute("db.url", _sanitize_dsn(self.db_url))
                start_time = time.monotonic()
                DB_CALLS_TOTAL.labels(db_type=self.db_type, operation="connect", table="n/a", status="attempt").inc()
                try:
                    min_size = int(os.getenv("PG_POOL_MIN_SIZE", "1"))
                    max_size = int(os.getenv("PG_POOL_MAX_SIZE", "10"))
                    max_connections = int(os.getenv("PG_MAX_CONNECTIONS", "100"))
                    if max_size > max_connections:
                        logger.warning(f"PG_POOL_MAX_SIZE ({max_size}) exceeds PG_MAX_CONNECTIONS ({max_connections}). Clamping.")
                        max_size = max_connections
                    if min_size > max_size:
                        logger.warning(f"PG_POOL_MIN_SIZE ({min_size}) is greater than PG_POOL_MAX_SIZE ({max_size}). Clamping min_size to max_size.")
                        min_size = max_size
                    timeout = float(os.getenv("PG_POOL_TIMEOUT", "30"))
                    ssl_mode = os.getenv("PG_SSL_MODE", "require" if os.getenv("ENV", "dev") == "prod" else "prefer").lower()
                    env = os.getenv("ENV", "dev").lower()

                    logger.info("Pool settings min=%s max=%s timeout=%s ssl_mode=%s env=%s",
                                 min_size, max_size, timeout, ssl_mode, env)

                    ssl_context = None
                    if ssl_mode == "require" or (ssl_mode == "allow" and env == "prod"):
                        ssl_context = ssl.create_default_context(purpose=ssl.SSLPurpose.SERVER_AUTH)
                        ssl_context.check_hostname = True
                        ssl_context.verify_mode = ssl.CERT_REQUIRED
                    elif ssl_mode == "allow":
                        logger.warning("PG_SSL_MODE='allow' is insecure and should only be used in development.")
                        ssl_context = ssl.create_default_context()
                        ssl_context.check_hostname = False
                        ssl_context.verify_mode = ssl.CERT_NONE
                    elif ssl_mode == "disable":
                        logger.warning("PG_SSL_MODE=disable: using plaintext connection (dev/local only).")
                        ssl_context = None  # plaintext
                    else:
                        logger.warning("PG_SSL_MODE=prefer: may fall back to plaintext, not recommended for production.")

                    self._pool = await asyncpg.create_pool(
                        self.db_url,
                        min_size=min_size,
                        max_size=max_size,
                        timeout=timeout,
                        ssl=ssl_context,
                        init=self._init_conn
                    )

                    async with self._pool.acquire() as conn:
                        await conn.fetchval("SELECT 1;")
                    logger.info("PostgreSQL connection pool warmed up.")

                    self._is_closed = False
                    DB_CONNECTIONS_CURRENT.labels(db_type=self.db_type).set(self._pool.get_size())
                    logger.info(f"PostgreSQL connection pool created for {_sanitize_dsn(self.db_url)}")

                    if os.getenv("AUTO_MIGRATE", "0") == "1":
                        logger.info("AUTO_MIGRATE is enabled. Running Alembic migrations.")
                        try:
                            subprocess.run(["alembic", "upgrade", "head"], check=True, capture_output=True, text=True)
                            logger.info("Alembic migrations applied successfully.")
                        except FileNotFoundError:
                            logger.error("Alembic command not found. Please ensure it is installed and in your PATH.")
                            raise
                        except subprocess.CalledProcessError as e:
                            logger.error(f"Alembic migration failed: {e.stderr}", exc_info=True)
                            raise RuntimeError(f"Failed to apply migrations: {e.stderr}") from e
                    else:
                        logger.info("AUTO_MIGRATE is disabled. Skipping table schema creation.")

                    self._health_check_task = asyncio.create_task(self._start_health_check(
                        interval=float(os.getenv("HEALTH_CHECK_INTERVAL", "60.0"))
                    ))

                    DB_CALLS_TOTAL.labels(db_type=self.db_type, operation="connect", table="n/a", status="success").inc()
                    DB_CALL_LATENCY_SECONDS.labels(db_type=self.db_type, operation="connect", table="n/a", status="success").observe(time.monotonic() - start_time)
                    span.set_status(Status(StatusCode.OK))
                except Exception as e:
                    if self._pool:
                        await self._pool.close()
                    self._pool = None
                    self._is_closed = True
                    DB_CALLS_TOTAL.labels(db_type=self.db_type, operation="connect", table="n/a", status="failure").inc()
                    DB_CALLS_ERRORS.labels(db_type=self.db_type, operation="connect", table="n/a", error_type=type(e).__name__).inc()
                    DB_CALL_LATENCY_SECONDS.labels(db_type=self.db_type, operation="connect", table="n/a", status="failure").observe(time.monotonic() - start_time)
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, f"Failed to connect: {e}"))
                    logger.error(f"Failed to connect to PostgreSQL at {_sanitize_dsn(self.db_url)}: {e}", exc_info=True)
                    raise PostgresClientConnectionError(f"Failed to connect to PostgreSQL: {e}") from e

    async def disconnect(self) -> None:
        """Closes the PostgreSQL connection pool."""
        if self._pool is None or self._is_closed:
            logger.info("PostgreSQL client already disconnected.")
            return

        if self._health_check_task:
            with contextlib.suppress(asyncio.CancelledError):
                self._health_check_task.cancel()
                await self._health_check_task
            self._health_check_task = None

        with tracer.start_as_current_span("db_disconnect") as span:
            start_time = time.monotonic()
            DB_CALLS_TOTAL.labels(db_type=self.db_type, operation="disconnect", table="n/a", status="attempt").inc()
            try:
                await self._pool.close()
                self._pool = None
                self._is_closed = True
                DB_CONNECTIONS_CURRENT.labels(db_type=self.db_type).set(0)
                DB_CONNECTIONS_IN_USE.labels(db_type=self.db_type).set(0)
                DB_CALLS_TOTAL.labels(db_type=self.db_type, operation="disconnect", table="n/a", status="success").inc()
                DB_CALL_LATENCY_SECONDS.labels(db_type=self.db_type, operation="disconnect", table="n/a", status="success").observe(time.monotonic() - start_time)
                span.set_status(Status(StatusCode.OK))
                logger.info("PostgreSQL connection pool closed.")
            except Exception as e:
                DB_CALLS_TOTAL.labels(db_type=self.db_type, operation="disconnect", table="n/a", status="failure").inc()
                DB_CALLS_ERRORS.labels(db_type=self.db_type, operation="disconnect", table="n/a", error_type=type(e).__name__).inc()
                DB_CALL_LATENCY_SECONDS.labels(db_type=self.db_type, operation="disconnect", table="n/a", status="failure").observe(time.monotonic() - start_time)
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Failed to disconnect: {e}"))
                logger.error(f"Failed to close PostgreSQL connection pool: {e}", exc_info=True)
                raise PostgresClientConnectionError(f"Failed to disconnect from PostgreSQL: {e}") from e

    def _validate_table_and_columns(self, table: str, cols: Optional[List[str]] = None) -> None:
        """
        Validates that a table and an optional list of columns exist in the predefined schema.
        Prevents SQL injection by checking against a hardcoded whitelist.
        """
        schema = self._TABLE_SCHEMAS.get(table)
        if not schema:
            raise ValueError(f"Unknown or unsupported table: {table}")
        if cols:
            allowed = {c.lower().strip() for c in schema["columns"]}
            invalid = {c.lower().strip() for c in cols} - allowed
            if invalid:
                raise ValueError(f"Invalid column(s) for {table}: {sorted(invalid)}")

    def _normalize_row(self, table: str, row: asyncpg.Record, normalize_datetimes: bool = True) -> Dict[str, Any]:
        """Converts an asyncpg.Record to a dictionary."""
        d = dict(row)
        if normalize_datetimes:
            for k, v in list(d.items()):
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
        return d

    async def _ensure_table_exists(self, table_name: str) -> None:
        """Ensures a specific table exists with its predefined schema using Alembic."""
        if self._pool is None or self._is_closed:
            raise PostgresClientConnectionError("Database pool not initialized. Call connect() first.")
        self._validate_table_and_columns(table_name)
        if os.getenv("AUTO_MIGRATE", "0") == "1":
            import subprocess
            logger.info(f"Running Alembic migrations for table '{table_name}'.")
            try:
                subprocess.run(["alembic", "upgrade", "head"], check=True, capture_output=True, text=True)
                logger.info(f"Alembic migrations applied successfully for '{table_name}'.")
            except FileNotFoundError:
                logger.error("Alembic command not found. Please ensure it is installed and in your PATH.")
                raise
            except subprocess.CalledProcessError as e:
                logger.error(f"Alembic migration failed: {e.stderr}", exc_info=True)
                raise RuntimeError(f"Failed to apply migrations: {e.stderr}") from e
        else:
            logger.info(f"AUTO_MIGRATE is disabled. Skipping migration for '{table_name}'.")

    async def _execute_query(self, operation: str, table: str, query: str, *args: Any) -> Any:
        """Executes a database query with metrics and tracing."""
        if self._pool is None or self._is_closed:
            raise PostgresClientConnectionError("Database pool not initialized. Call connect() first.")

        start_time = time.monotonic()
        span_name = f"db.{operation}.{table}"
        with tracer.start_as_current_span(span_name) as span:
            span.set_attribute("db.operation", operation)
            span.set_attribute("db.table", table)
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.name", os.getenv("PGDATABASE", ""))

            log_sql = os.getenv("SFE_LOG_SQL", "0") == "1"
            if log_sql:
                sanitized_query = query if len(query) < 200 else query[:197] + '...'
                span.set_attribute("db.statement", sanitized_query)

            sanitized_args = []
            for arg in args:
                if isinstance(arg, (dict, list)):
                    try:
                        json_str = json.dumps(arg, sort_keys=True)
                        sanitized_args.append(f"hash:{hashlib.sha256(json_str.encode()).hexdigest()[:8]}")
                    except (TypeError, ValueError):
                        sanitized_args.append(f"hash:{hashlib.sha256(str(arg).encode()).hexdigest()[:8]}")
                elif isinstance(arg, str) and len(arg) > 64:
                    sanitized_args.append(f"truncated:{arg[:61]}...")
                else:
                    sanitized_args.append(self._scrub_secrets(arg))
            span.set_attribute("db.statement.parameters", str(sanitized_args))

            status = "failure"
            result = None
            try:
                DB_CALLS_TOTAL.labels(db_type=self.db_type, operation=operation, table=table, status="attempt").inc()

                async with self._pool.acquire() as conn:
                    size = self._pool.get_size()
                    get_idle = getattr(self._pool, "get_idle_count", None)
                    idle = get_idle() if callable(get_idle) else None
                    if idle is not None:
                        DB_CONNECTIONS_IN_USE.labels(db_type=self.db_type).set(max(0, size - idle))
                    DB_CONNECTIONS_CURRENT.labels(db_type=self.db_type).set(size)

                    should_fetch = "RETURNING" in query.upper() or operation.startswith(("load", "get"))
                    query_timeout = float(os.getenv("PG_QUERY_TIMEOUT", "0"))

                    if should_fetch:
                        if query_timeout > 0:
                            result = await asyncio.wait_for(conn.fetch(query, *args), timeout=query_timeout)
                        else:
                            result = await conn.fetch(query, *args)
                        span.set_attribute("db.rows_returned", len(result))
                    else:
                        if query_timeout > 0:
                            result = await asyncio.wait_for(conn.execute(query, *args), timeout=query_timeout)
                        else:
                            result = await conn.execute(query, *args)
                        if isinstance(result, str) and (operation.startswith("insert") or operation.startswith("update") or operation.startswith("delete")):
                            try:
                                affected_rows = int(result.split()[-1])
                                span.set_attribute("db.affected_rows", affected_rows)
                            except (ValueError, IndexError):
                                pass
                status = "success"
                DB_CALLS_TOTAL.labels(db_type=self.db_type, operation=operation, table=table, status="success").inc()
                span.set_status(Status(StatusCode.OK))
                return result

            except asyncio.TimeoutError as e:
                status = "timeout"
                DB_CALLS_TOTAL.labels(db_type=self.db_type, operation=operation, table=table, status="failure").inc()
                DB_CALLS_ERRORS.labels(db_type=self.db_type, operation=operation, table=table, error_type="TimeoutError").inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Query timed out: {e}"))
                logger.error(f"Query timed out during DB operation '{operation}' on '{table}': {e}", exc_info=True)
                raise PostgresClientTimeoutError(f"Query timed out during {operation} on {table}: {e}") from e

            except TRANSIENT_EXC as e:
                status = "transient_failure"
                DB_CALLS_TOTAL.labels(db_type=self.db_type, operation=operation, table=table, status="failure").inc()
                DB_CALLS_ERRORS.labels(db_type=self.db_type, operation=operation, table=table, error_type=type(e).__name__).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Transient DB error: {e}"))
                logger.warning(f"Transient error during {operation} on {table}: {e}. Attempting reconnect.")
                await self.reconnect()
                # Re-raise the original exception, let the caller decide to retry
                raise PostgresClientConnectionError(f"Transient error during {operation} on {table}: {e}") from e

            except asyncio.CancelledError:
                status = "cancelled"
                raise

            except FATAL_EXC as e:
                status = "fatal"
                DB_CALLS_TOTAL.labels(db_type=self.db_type, operation=operation, table=table, status="failure").inc()
                DB_CALLS_ERRORS.labels(db_type=self.db_type, operation=operation, table=table, error_type=type(e).__name__).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"PostgreSQL fatal error: {e}"))
                logger.error(f"PostgreSQL fatal error during DB operation '{operation}' on '{table}': {e}", exc_info=True)
                raise PostgresClientSchemaError(f"Schema error during {operation} on {table}: {e}") from e

            except Exception as e:
                status = "failure"
                DB_CALLS_TOTAL.labels(db_type=self.db_type, operation=operation, table=table, status="failure").inc()
                DB_CALLS_ERRORS.labels(db_type=self.db_type, operation=operation, table=table, error_type=type(e).__name__).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"DB error: {e}"))
                logger.error(f"Error during DB operation '{operation}' on '{table}': {e}", exc_info=True)
                raise PostgresClientQueryError(f"Query error during {operation} on {table}: {e}") from e
            finally:
                if self._pool and not self._is_closed:
                    size = self._pool.get_size()
                    get_idle = getattr(self._pool, "get_idle_count", None)
                    idle = get_idle() if callable(get_idle) else None
                    if idle is not None:
                        DB_CONNECTIONS_IN_USE.labels(db_type=self.db_type).set(max(0, size - idle))
                    DB_CONNECTIONS_CURRENT.labels(db_type=self.db_type).set(size)
                DB_CALL_LATENCY_SECONDS.labels(db_type=self.db_type, operation=operation, table=table, status=status).observe(time.monotonic() - start_time)

    def _scrub_secrets(self, value: Any) -> Any:
        """Scrub potentially sensitive data from logging."""
        if isinstance(value, str):
            sensitive_patterns = [
                r'(password|token|secret|key|credential|auth)\b',
                r'[a-zA-Z0-9]{32,}',  # Long alphanumeric strings (e.g., API keys)
                r'[\w\.-]+@[\w\.-]+',  # Email addresses
                r'\b\d{4}-\d{4}-\d{4}-\d{4}\b'  # Credit card-like patterns
            ]
            for pattern in sensitive_patterns:
                if re.search(pattern, value, re.IGNORECASE):
                    return '***'
        return value

    async def _get_insert_update_sql_and_values(self, table: str, data: Dict[str, Any]) -> Tuple[str, List[Any]]:
        """Helper to construct INSERT/UPDATE SQL and prepare values for a single record."""
        self._validate_table_and_columns(table)
        table_schema = self._TABLE_SCHEMAS.get(table)
        columns = table_schema["columns"]
        pk_columns = table_schema["pk"]
        jsonb_columns = table_schema["jsonb_columns"]

        values: List[Any] = []
        placeholders: List[str] = []
        for i, col in enumerate(columns):
            val = data.get(col)
            values.append(val)
            if col in jsonb_columns:
                placeholders.append(f"${i+1}::jsonb")
            else:
                placeholders.append(f"${i+1}")

        insert_sql_part = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"

        on_conflict_pk_part = ", ".join(pk_columns)
        update_set_parts = []
        for col in columns:
            if col not in pk_columns:
                if col in jsonb_columns:
                    update_set_parts.append(f"{col} = COALESCE({table}.{col}, '{{}}'::jsonb) || COALESCE(EXCLUDED.{col}, '{{}}'::jsonb)")
                else:
                    update_set_parts.append(f"{col} = EXCLUDED.{col}")

        on_conflict_sql = f"ON CONFLICT ({on_conflict_pk_part}) DO UPDATE SET {', '.join(update_set_parts)}"
        full_sql = f"{insert_sql_part} {on_conflict_sql} RETURNING {', '.join(pk_columns)};"
        return full_sql, values

    async def _save_many_copy(self, table: str, data_list: List[Dict[str, Any]]) -> List[str]:
        """Uses PostgreSQL COPY for batch inserts, falling back to UPSERT for conflicts."""
        self._validate_table_and_columns(table)
        table_schema = self._TABLE_SCHEMAS.get(table)
        columns = table_schema["columns"]
        pk_columns = table_schema["pk"]
        jsonb_columns = table_schema["jsonb_columns"]
        saved_ids: List[str] = []
        
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Prepare records for COPY and update original data_list with generated IDs
                records = []
                for data_item in data_list:
                    # Generate IDs directly in the original data_item to ensure consistency
                    if pk_columns == ["id"] and "id" not in data_item:
                        data_item["id"] = str(uuid.uuid4())
                    elif pk_columns == ["session_id"] and "session_id" not in data_item:
                        data_item["session_id"] = str(uuid.uuid4())
                    elif table == "agent_knowledge":
                        if "domain" not in data_item or "key" not in data_item:
                            raise ValueError("Each item in agent_knowledge batch must have 'domain' and 'key'.")
                    record = [data_item.get(col) for col in columns]
                    for i, col in enumerate(columns):
                        if col in jsonb_columns and record[i] is not None:
                            record[i] = json.dumps(record[i])
                        elif isinstance(record[i], datetime):
                            record[i] = record[i].isoformat()
                    records.append(tuple(record))
                    if table == "agent_knowledge":
                        saved_ids.append(f"{data_item['domain']}:{data_item['key']}")
                    else:
                        saved_ids.append(str(data_item[pk_columns[0]]))
                
                # Use COPY for bulk insert
                await conn.copy_records_to_table(
                    table,
                    columns=columns,
                    records=records,
                    schema_name="public"
                )
                
                # Handle conflicts with UPSERT (now using data_item with generated IDs)
                for data_item in data_list:
                    query_sql, values = await self._get_insert_update_sql_and_values(table, data_item)
                    await conn.execute(query_sql, *values)
        
        return saved_ids

    async def save(self, table: str, data: Dict[str, Any]) -> str:
        """
        UPSERTs a single record into the specified table, returning the primary key(s) as a string.

        Args:
            table (str): The target table name (e.g., 'feedback', 'agent_knowledge').
            data (Dict[str, Any]): The data to insert or update, with keys matching table columns.

        Returns:
            str: The primary key of the saved record (e.g., 'id' or 'domain:key' for agent_knowledge).

        Raises:
            ValueError: If the table is invalid or required primary keys are missing.
            PostgresClientError: If the database operation fails.

        Example:
            ```python
            client = PostgresClient("postgresql://user:pass@localhost/db")
            await client.connect()
            data = {"id": "123", "type": "user_feedback", "data": {"comment": "Great!"}, "timestamp": "2025-08-21T12:00:00Z"}
            pk = await client.save("feedback", data)
            print(f"Saved with PK: {pk}")
            ```
        """
        self._validate_table_and_columns(table)
        pk_columns = self._TABLE_SCHEMAS.get(table, {}).get("pk")
        if not pk_columns:
            raise ValueError(f"Table '{table}' has no primary key defined.")

        if pk_columns == ["id"] and "id" not in data:
            data['id'] = str(uuid.uuid4())
        elif pk_columns == ["session_id"] and "session_id" not in data:
            data['session_id'] = str(uuid.uuid4())

        if table == "agent_knowledge":
            if "domain" not in data or "key" not in data:
                raise ValueError("For agent_knowledge table, 'domain' and 'key' are required in data for save operation.")
            pk_value = f"{data['domain']}:{data['key']}"
        elif len(pk_columns) == 1:
            pk_value = data.get(pk_columns[0])
            if pk_value is None:
                raise ValueError(f"Primary key column '{pk_columns[0]}' is missing from data for table '{table}'.")
        else:
            pk_value = "unknown_pk"

        query_sql, values = await self._get_insert_update_sql_and_values(table, data)
        rows = await self._execute_query("save", table, query_sql, *values)

        if not rows:
            return pk_value
        pk_record = rows[0]
        if table == "agent_knowledge":
            return f"{pk_record['domain']}:{pk_record['key']}"
        return str(pk_record[pk_columns[0]])

    async def save_many(self, table: str, data_list: List[Dict[str, Any]]) -> List[str]:
        """
        Batch UPSERTs multiple records, returning a list of primary key strings.

        Args:
            table (str): The table name.
            data_list (List[Dict[str, Any]]): A list of dictionaries, where each dictionary represents a record.

        Returns:
            List[str]: A list of primary keys for the saved records.

        Raises:
            ValueError: If the table is invalid or required primary keys are missing.
            PostgresClientError: If the database operation fails.

        Example:
            ```python
            client = PostgresClient("postgresql://user:pass@localhost/db")
            await client.connect()
            batch = [{"id": "1", "type": "A", "data": {}}, {"id": "2", "type": "B", "data": {}}]
            pks = await client.save_many("feedback", batch)
            print(f"Saved PKs: {pks}")
            ```
        """
        if not data_list:
            return []
        if self._pool is None or self._is_closed:
            raise PostgresClientConnectionError("Database pool not initialized. Call connect() first.")

        self._validate_table_and_columns(table)
        table_schema = self._TABLE_SCHEMAS.get(table)
        columns = table_schema["columns"]
        pk_columns = table_schema["pk"]
        jsonb_columns = table_schema["jsonb_columns"]
        
        batch_size_threshold = int(os.getenv("PG_COPY_BATCH_THRESHOLD", "1000"))
        if len(data_list) >= batch_size_threshold:
            logger.info(f"Using COPY for batch save of {len(data_list)} records to '{table}'.")
            return await self._save_many_copy(table, data_list)

        MAX_PARAMS = int(os.getenv("PG_MAX_PARAMS", "65535"))
        MAX_ROWS_PER_CHUNK = max(1, int(os.getenv("PG_MAX_ROWS_PER_CHUNK", "1000")))
        cols_per_row = len(columns)
        max_rows_per_chunk = min(MAX_ROWS_PER_CHUNK, max(1, MAX_PARAMS // cols_per_row))

        saved_ids: List[str] = []
        start_time = time.monotonic()
        op = "save_many"
        status = "failure"
        with tracer.start_as_current_span(f"db.{op}.{table}") as span:
            span.set_attribute("db.operation", op)
            span.set_attribute("db.table", table)
            try:
                DB_CALLS_TOTAL.labels(db_type=self.db_type, operation=op, table=table, status="attempt").inc()
                async with self._pool.acquire() as conn:
                    size = self._pool.get_size()
                    get_idle = getattr(self._pool, "get_idle_count", None)
                    idle = get_idle() if callable(get_idle) else None
                    if idle is not None:
                        DB_CONNECTIONS_IN_USE.labels(db_type=self.db_type).set(max(0, size - idle))
                    DB_CONNECTIONS_CURRENT.labels(db_type=self.db_type).set(size)

                    async with conn.transaction():
                        for start in range(0, len(data_list), max_rows_per_chunk):
                            chunk = data_list[start:start + max_rows_per_chunk]

                            all_values_flat: List[Any] = []
                            all_placeholders: List[str] = []

                            for row_idx, data_item in enumerate(chunk):
                                current_data = data_item.copy()
                                if pk_columns == ["id"] and "id" not in current_data:
                                    current_data["id"] = str(uuid.uuid4())
                                elif pk_columns == ["session_id"] and "session_id" not in current_data:
                                    current_data["session_id"] = str(uuid.uuid4())
                                elif table == "agent_knowledge":
                                    if "domain" not in current_data or "key" not in current_data:
                                        raise ValueError("Each item in agent_knowledge batch must have 'domain' and 'key'.")

                                row_placeholders: List[str] = []
                                for col_idx, col in enumerate(columns):
                                    param_idx = (row_idx * cols_per_row) + col_idx + 1
                                    val = current_data.get(col)
                                    all_values_flat.append(val)
                                    if col in jsonb_columns:
                                        row_placeholders.append(f"${param_idx}::jsonb")
                                    else:
                                        row_placeholders.append(f"${param_idx}")
                                all_placeholders.append(f"({', '.join(row_placeholders)})")

                            columns_str = ", ".join(columns)
                            placeholders_str = ", ".join(all_placeholders)
                            on_conflict_pk_part = ", ".join(pk_columns)
                            update_set_parts = []
                            for col in columns:
                                if col not in pk_columns:
                                    if col in jsonb_columns:
                                        update_set_parts.append(f"{col} = COALESCE({table}.{col}, '{{}}'::jsonb) || COALESCE(EXCLUDED.{col}, '{{}}'::jsonb)")
                                    else:
                                        update_set_parts.append(f"{col} = EXCLUDED.{col}")

                            on_conflict_sql = f"ON CONFLICT ({on_conflict_pk_part}) DO UPDATE SET {', '.join(update_set_parts)}"
                            query_sql = f"INSERT INTO {table} ({columns_str}) VALUES {placeholders_str} {on_conflict_sql} RETURNING {', '.join(pk_columns)};"

                            result = await conn.fetch(query_sql, *all_values_flat)
                            for record in result:
                                if table == "agent_knowledge":
                                    saved_ids.append(f"{record['domain']}:{record['key']}")
                                else:
                                    saved_ids.append(str(record[pk_columns[0]]))

                status = "success"
                DB_CALLS_TOTAL.labels(db_type=self.db_type, operation=op, table=table, status="success").inc()
                span.set_attribute("db.rows_affected", len(saved_ids))
                span.set_status(Status(StatusCode.OK))
                return saved_ids
            except asyncio.TimeoutError as e:
                status = "timeout"
                DB_CALLS_TOTAL.labels(db_type=self.db_type, operation=op, table=table, status="failure").inc()
                DB_CALLS_ERRORS.labels(db_type=self.db_type, operation=op, table=table, error_type="TimeoutError").inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"save_many timed out: {e}"))
                logger.error(f"save_many timed out on '{table}': {e}", exc_info=True)
                raise PostgresClientTimeoutError(f"save_many timed out on {table}: {e}") from e
            except FATAL_EXC as e:
                status = "fatal"
                DB_CALLS_TOTAL.labels(db_type=self.db_type, operation=op, table=table, status="failure").inc()
                DB_CALLS_ERRORS.labels(db_type=self.db_type, operation=op, table=table, error_type=type(e).__name__).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"PostgreSQL fatal error in save_many: {e}"))
                logger.error(f"PostgreSQL fatal error in save_many on '{table}': {e}", exc_info=True)
                raise PostgresClientSchemaError(f"Schema error in save_many on {table}: {e}") from e
            except Exception as e:
                status = "failure"
                DB_CALLS_TOTAL.labels(db_type=self.db_type, operation=op, table=table, status="failure").inc()
                DB_CALLS_ERRORS.labels(db_type=self.db_type, operation=op, table=table, error_type=type(e).__name__).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"save_many error: {e}"))
                logger.error(f"Error in save_many on '{table}': {e}", exc_info=True)
                raise PostgresClientQueryError(f"Query error in save_many on {table}: {e}") from e
            finally:
                if self._pool and not self._is_closed:
                    size = self._pool.get_size()
                    get_idle = getattr(self._pool, "get_idle_count", None)
                    idle = get_idle() if callable(get_idle) else None
                    if idle is not None:
                        DB_CONNECTIONS_IN_USE.labels(db_type=self.db_type).set(max(0, size - idle))
                    DB_CONNECTIONS_CURRENT.labels(db_type=self.db_type).set(size)
                DB_CALL_LATENCY_SECONDS.labels(db_type=self.db_type, operation=op, table=table, status=status).observe(time.monotonic() - start_time)


    async def load(self, table: str, query_value: Any, query_field: str = "id", normalize_datetimes: bool = True) -> Optional[Dict[str, Any]]:
        """
        Loads a single record from a table based on a query field.

        Args:
            table (str): The table name.
            query_value (Any): The value to search for.
            query_field (str): The column name to search within. Defaults to 'id'.
            normalize_datetimes (bool): Whether to convert datetime objects to ISO format strings.

        Returns:
            Optional[Dict[str, Any]]: The found record as a dictionary, or None if not found.

        Raises:
            ValueError: If the table or query field is invalid.
            PostgresClientError: If the database operation fails.
        """
        query_sql = ""
        params: List[Any] = []
        if table == "agent_knowledge" and query_field == "domain_key":
            if not isinstance(query_value, str) or ":" not in query_value:
                raise ValueError("For agent_knowledge, query_value for 'domain_key' must be 'domain:key' string.")
            domain, key = query_value.split(':', 1)
            query_sql = f"SELECT * FROM {table} WHERE domain = $1 AND key = $2;"
            params = [domain, key]
        else:
            self._validate_table_and_columns(table, [query_field])
            query_sql = f"SELECT * FROM {table} WHERE {query_field} = $1;"
            params = [query_value]

        res = await self._execute_query("load", table, query_sql, *params)
        if res:
            return self._normalize_row(table, res[0], normalize_datetimes)
        return None

    async def load_all(self, table: str, filters: Optional[Dict[str, Any]] = None,
                       order_by: Optional[str] = None, limit: Optional[int] = None, normalize_datetimes: bool = True) -> List[Dict[str, Any]]:
        """
        Loads multiple records from a table based on filters.

        Args:
            table (str): The table name.
            filters (Optional[Dict[str, Any]]): A dictionary of column filters (e.g., `{"type": "user_feedback"}`).
            order_by (Optional[str]): A column to order the results by (e.g., `"timestamp DESC"`).
            limit (Optional[int]): The maximum number of records to return.
            normalize_datetimes (bool): Whether to convert datetime objects to ISO format strings.

        Returns:
            List[Dict[str, Any]]: A list of found records.

        Raises:
            ValueError: If the table, filters, order_by, or limit are invalid.
            PostgresClientError: If the database operation fails.
        """
        filter_cols = list(filters.keys()) if filters else []
        self._validate_table_and_columns(table, filter_cols)

        where_clauses: List[str] = []
        params: List[Any] = []
        param_counter = 1

        if filters:
            jsonb_cols = set(self._TABLE_SCHEMAS.get(table, {}).get("jsonb_columns", []))
            for k, v in filters.items():
                if k in jsonb_cols:
                    where_clauses.append(f"{k} @> ${param_counter}::jsonb")
                    params.append(v)
                else:
                    where_clauses.append(f"{k} = ${param_counter}")
                    params.append(v)
                param_counter += 1

        query_sql = f"SELECT * FROM {table}"
        if where_clauses:
            query_sql += " WHERE " + " AND ".join(where_clauses)

        if order_by:
            col_map = {c.lower().strip(): c for c in self._TABLE_SCHEMAS[table]["columns"]}
            order_parts = order_by.strip().split()
            key = order_parts[0].lower()
            direction = order_parts[1].lower() if len(order_parts) > 1 else ""
            if key not in col_map or (direction and direction not in ("asc", "desc")):
                raise ValueError(f"Invalid order_by. Columns: {sorted(col_map.values())}; directions: asc, desc.")
            order_col = col_map[key]
            query_sql += f" ORDER BY {order_col}" + (f" {direction.upper()}" if direction else "")

        global_max_rows = int(os.getenv("PG_GLOBAL_MAX_ROWS", "50000"))
        if limit is not None:
            max_limit = min(int(os.getenv("PG_MAX_LIMIT", "10000")), global_max_rows)
            if not isinstance(limit, int) or limit <= 0 or limit > max_limit:
                raise ValueError(f"Invalid limit. Must be an integer between 1 and {max_limit}.")
        else:
            default_limit = min(int(os.getenv("PG_DEFAULT_LIMIT", "1000")), global_max_rows)
            logger.warning(f"load_all called without a filter or limit. Applying default limit of {default_limit} to prevent full table scan.")
            limit = default_limit
        
        query_sql += f" LIMIT {limit};"
        res = await self._execute_query("load_all", table, query_sql, *params)
        
        if len(res) >= limit:
            logger.warning(f"Query on {table} returned {len(res)} rows, which is the applied limit of {limit}. There may be more rows. Consider a more specific query or a higher limit.")
        
        return [self._normalize_row(table, r, normalize_datetimes) for r in res]

    async def update(self, table: str, query: Dict[str, Any], updates: Dict[str, Any]) -> bool:
        """
        Updates records in a table that match the query.

        Args:
            table (str): The table name.
            query (Dict[str, Any]): A dictionary of key-value pairs to match records.
            updates (Dict[str, Any]): A dictionary of column updates to apply.

        Returns:
            bool: True if at least one record was updated, False otherwise.

        Raises:
            ValueError: If the table, query, or updates are invalid.
            PostgresClientError: If the database operation fails.
        """
        update_cols = list(updates.keys()) + list(query.keys())
        self._validate_table_and_columns(table, update_cols)

        pk_cols = self._TABLE_SCHEMAS[table]["pk"]
        if any(c in pk_cols for c in updates):
            raise ValueError("Updating primary key columns is not allowed.")
        if not updates:
            return False
        if not query:
            raise ValueError("Refusing to UPDATE without a WHERE clause.")

        set_clauses: List[str] = []
        set_params: List[Any] = []
        param_counter = 1

        jsonb_columns = set(self._TABLE_SCHEMAS.get(table, {}).get("jsonb_columns", []))
        for k, v in updates.items():
            if k in jsonb_columns:
                if v is None:
                    set_clauses.append(f"{k} = NULL")
                elif isinstance(v, dict):
                    if "$replace" in v and "$unset" in v:
                        raise ValueError("Cannot use '$replace' and '$unset' in the same update for a JSONB column.")
                    if "$replace" in v:
                        set_clauses.append(f"{k} = ${param_counter}::jsonb")
                        set_params.append(v["$replace"])
                        param_counter += 1
                    elif "$unset" in v:
                        if not isinstance(v["$unset"], list) or not all(isinstance(i, str) for i in v["$unset"]):
                            raise ValueError("'$unset' value must be a list of strings.")
                        set_clauses.append(f"{k} = {k} #- ${param_counter}::text[]")
                        set_params.append(v["$unset"])
                        param_counter += 1
                    else:  # default JSONB merge
                        set_clauses.append(f"{k} = COALESCE({k}, '{{}}'::jsonb) || ${param_counter}::jsonb")
                        set_params.append(v)
                        param_counter += 1
                else:
                    set_clauses.append(f"{k} = ${param_counter}::jsonb")
                    set_params.append(v)
                    param_counter += 1
            else:
                set_clauses.append(f"{k} = ${param_counter}")
                set_params.append(v)
                param_counter += 1

        where_clauses: List[str] = []
        where_params: List[Any] = []
        for k, v in query.items():
            if k in jsonb_columns and isinstance(v, dict):
                where_clauses.append(f"{k} @> ${param_counter}::jsonb")
                where_params.append(v)
            else:
                where_clauses.append(f"{k} = ${param_counter}")
                where_params.append(v)
            param_counter += 1

        query_sql = f"UPDATE {table} SET {', '.join(set_clauses)}"
        if where_clauses:
            query_sql += " WHERE " + " AND ".join(where_clauses)
        query_sql += f" RETURNING {', '.join(pk_cols)};"

        all_params = set_params + where_params
        res = await self._execute_query("update", table, query_sql, *all_params)
        return bool(res)

    async def delete(self, table: str, query_value: Any, query_field: str = "id") -> bool:
        """
        Deletes a record from a table based on a query field.

        Args:
            table (str): The table name.
            query_value (Any): The value to search for.
            query_field (str): The column name to search within. Defaults to 'id'.

        Returns:
            bool: True if a record was deleted, False otherwise.

        Raises:
            ValueError: If the table or query field is invalid.
            PostgresClientError: If the database operation fails.
        """
        pk_cols = self._TABLE_SCHEMAS[table]["pk"]
        if table == "agent_knowledge" and query_field == "domain_key":
            if not isinstance(query_value, str) or ":" not in query_value:
                raise ValueError("For agent_knowledge, query_value for 'domain_key' must be 'domain:key' string.")
            domain, key = query_value.split(':', 1)
            query_sql = f"DELETE FROM {table} WHERE domain = $1 AND key = $2 RETURNING {', '.join(pk_cols)};"
            params = [domain, key]
        else:
            self._validate_table_and_columns(table, [query_field])
            query_sql = f"DELETE FROM {table} WHERE {query_field} = $1 RETURNING {', '.join(pk_cols)};"
            params = [query_value]

        res = await self._execute_query("delete", table, query_sql, *params)
        return bool(res)


# Example Usage (for testing purposes)
async def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger.setLevel(logging.DEBUG)

    db_url = os.getenv("DATABASE_URL", "postgresql://sfe_user:sfe_password@localhost:5432/sfe_test_db")
    client = PostgresClient(db_url)
    exit_code = 0

    try:
        if os.getenv("RUN_EXAMPLE", "0") == "1":
            await client.connect()
            logger.info("\n--- PostgresClient Example Usage ---")

            # --- Test Table: feedback ---
            logger.info("\n--- Testing 'feedback' table ---")
            feedback_data = {
                "id": str(uuid.uuid4()),
                "type": "user_feedback",
                "data": {"decision_id": "dec_123", "approved": True, "comment": "Great job!"},
                "timestamp": datetime.now(timezone.utc)
            }
            saved_id = await client.save("feedback", feedback_data)
            logger.info(f"Saved feedback with ID: {saved_id}")

            retrieved_feedback = await client.load("feedback", saved_id)
            logger.info(f"Retrieved feedback: {retrieved_feedback}")
            assert retrieved_feedback and retrieved_feedback['id'] == saved_id

            updated_feedback_data = {"data": {"comment": "Excellent work, approved."}}
            updated = await client.update("feedback", {"id": saved_id}, updated_feedback_data)
            logger.info(f"Updated feedback: {updated}")
            assert updated

            retrieved_updated_feedback = await client.load("feedback", saved_id)
            logger.info(f"Retrieved updated feedback: {retrieved_updated_feedback}")
            assert retrieved_updated_feedback['data']['comment'] == "Excellent work, approved."

            deleted = await client.delete("feedback", saved_id)
            logger.info(f"Deleted feedback: {deleted}")
            assert deleted

            retrieved_deleted_feedback = await client.load("feedback", saved_id)
            assert retrieved_deleted_feedback is None
            logger.info("Feedback deletion verified.")

            # --- Test Table: agent_knowledge ---
            logger.info("\n--- Testing 'agent_knowledge' table ---")
            knowledge_data = {
                "domain": "bug_fix",
                "key": "bug_id_456",
                "value": {"description": "Fix for login bug", "status": "resolved"},
                "timestamp": datetime.now(timezone.utc),
                "source": "dev_agent",
                "user_id": "user_abc",
                "version": 1,
                "diff": None,
                "merkle_leaf": "leaf_hash_1",
                "merkle_proof": [{"node": "node1"}],
                "merkle_root": "root_hash_1"
            }
            saved_knowledge_key = await client.save("agent_knowledge", knowledge_data)
            logger.info(f"Saved knowledge for domain:key {saved_knowledge_key}")

            retrieved_knowledge = await client.load("agent_knowledge", "bug_fix:bug_id_456", query_field="domain_key")
            logger.info(f"Retrieved knowledge: {retrieved_knowledge}")
            assert retrieved_knowledge and retrieved_knowledge['domain'] == "bug_fix"

            updated_knowledge_data = {"value": {"status": "verified", "priority": "high"}, "version": 2}
            updated_knowledge = await client.update("agent_knowledge", {"domain": "bug_fix", "key": "bug_id_456"}, updated_knowledge_data)
            logger.info(f"Updated knowledge: {updated_knowledge}")
            assert updated_knowledge

            retrieved_updated_knowledge = await client.load("agent_knowledge", "bug_fix:bug_id_456", query_field="domain_key")
            logger.info(f"Retrieved updated knowledge: {retrieved_updated_knowledge}")
            assert retrieved_updated_knowledge['value']['status'] == "verified"
            assert retrieved_updated_knowledge['version'] == 2

            deleted_knowledge = await client.delete("agent_knowledge", "bug_fix:bug_id_456", query_field="domain_key")
            logger.info(f"Deleted knowledge: {deleted_knowledge}")
            assert deleted_knowledge

            retrieved_deleted_knowledge = await client.load("agent_knowledge", "bug_fix:bug_id_456", query_field="domain_key")
            assert retrieved_deleted_knowledge is None
            logger.info("Knowledge deletion verified.")

            # --- Test Table: agent_states ---
            logger.info("\n--- Testing 'agent_states' table ---")
            state_data = {
                "session_id": "sess_agent_xyz",
                "state": {"current_step": "planning", "memory_buffer": ["hi", "hello"]},
                "last_updated": datetime.now(timezone.utc)
            }
            saved_session_id = await client.save("agent_states", state_data)
            logger.info(f"Saved agent state for session: {saved_session_id}")

            retrieved_state = await client.load("agent_states", saved_session_id, query_field="session_id")
            logger.info(f"Retrieved agent state: {retrieved_state}")
            assert retrieved_state and retrieved_state['session_id'] == saved_session_id

            updated_state_data = {"state": {"current_step": "executing", "memory_buffer": ["hi", "hello", "executing"]}, "last_updated": datetime.now(timezone.utc)}
            updated_state = await client.update("agent_states", {"session_id": saved_session_id}, updated_state_data)
            logger.info(f"Updated agent state: {updated_state}")
            assert updated_state

            retrieved_updated_state = await client.load("agent_states", saved_session_id, query_field="session_id")
            logger.info(f"Retrieved updated agent state: {retrieved_updated_state}")
            assert retrieved_updated_state['state']['current_step'] == "executing"

            deleted_state = await client.delete("agent_states", saved_session_id, query_field="session_id")
            logger.info(f"Deleted agent state: {deleted_state}")
            assert deleted_state

            retrieved_deleted_state = await client.load("agent_states", saved_session_id, query_field="session_id")
            assert retrieved_deleted_state is None
            logger.info("Agent state deletion verified.")

            # --- Test Table: audit_events ---
            logger.info("\n--- Testing 'audit_events' table ---")
            audit_event_data = {
                "id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc),
                "event_type": "user:login",
                "details": {"user_id": "test_user", "ip_address": "192.168.1.1"},
                "host": "localhost",
                "previous_log_hash": "prev_hash_abc",
                "hash": "current_hash_xyz",
                "signatures": [{"key_id": "key1", "signature": "sig1"}],
                "correlation_id": str(uuid.uuid4())
            }
            saved_audit_id = await client.save("audit_events", audit_event_data)
            logger.info(f"Saved audit event with ID: {saved_audit_id}")

            retrieved_audit = await client.load("audit_events", saved_audit_id)
            logger.info(f"Retrieved audit event: {retrieved_audit}")
            assert retrieved_audit and retrieved_audit['id'] == saved_audit_id

            # Test load_all
            all_audit_events = await client.load_all("audit_events", filters={"event_type": "user:login"})
            logger.info(f"Retrieved all 'user:login' audit events: {len(all_audit_events)}")
            assert len(all_audit_events) > 0

            deleted_audit = await client.delete("audit_events", saved_audit_id)
            logger.info(f"Deleted audit event: {deleted_audit}")
            assert deleted_audit

            retrieved_deleted_audit = await client.load("audit_events", saved_audit_id)
            assert retrieved_deleted_audit is None
            logger.info("Audit event deletion verified.")

            # --- Test save_many ---
            logger.info("\n--- Testing 'save_many' ---")
            batch_feedback_data = []
            for i in range(5):
                batch_feedback_data.append({
                    "id": str(uuid.uuid4()),
                    "type": "batch_feedback",
                    "data": {"item_idx": i, "status": "processed"},
                    "timestamp": datetime.now(timezone.utc)
                })

            batch_ids = await client.save_many("feedback", batch_feedback_data)
            logger.info(f"Saved {len(batch_ids)} items in batch: {batch_ids}")
            assert len(batch_ids) == 5

            # Verify batch items can be loaded
            loaded_batch_items = await client.load_all("feedback", filters={"type": "batch_feedback"})
            logger.info(f"Loaded {len(loaded_batch_items)} batch feedback items.")
            assert len(loaded_batch_items) >= 5

            # Clean up batch items
            for item_data in batch_feedback_data:
                await client.delete("feedback", item_data["id"])
            logger.info("Batch feedback items cleaned up.")

        elif os.getenv("RUN_TESTS", "0") == "1":
            logger.info("Running test suite.")
            import pytest
            exit_code = pytest.main(["tests/test_postgres_client.py", "-v"])
        else:
            logger.info("Skipping example usage. Set RUN_EXAMPLE=1 to run.")

    except Exception as e:
        logger.error(f"An error occurred during PostgresClient testing: {e}", exc_info=True)
        exit_code = 1
    finally:
        await client.disconnect()
        logger.info("PostgresClient disconnected.")
        if exit_code != 0:
            raise SystemExit(exit_code)

if __name__ == "__main__":
    asyncio.run(main())

class SchemaValidationError(Exception):
    pass