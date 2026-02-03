"""
Neo4j Knowledge Graph implementation for managing graph-based knowledge storage.
"""

import asyncio
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Type, Union

# OpenTelemetry tracing
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.trace import Status, StatusCode

    HAS_OPENTELEMETRY = True
except (ImportError, ModuleNotFoundError):
    # Fallback when OpenTelemetry is not fully installed
    HAS_OPENTELEMETRY = False
    trace = None  # type: ignore
    Resource = None  # type: ignore
    TracerProvider = None  # type: ignore
    BatchSpanProcessor = None  # type: ignore
    ConsoleSpanExporter = None  # type: ignore
    OTLPSpanExporter = None  # type: ignore
    Status = None  # type: ignore
    StatusCode = None  # type: ignore

# Import the no-op tracer for fallback
try:
    from self_fixing_engineer.arbiter.otel_config import NoOpTracer
except ImportError:
    # Define locally if otel_config is not available
    from contextlib import nullcontext

    class NoOpTracer:
        def start_as_current_span(self, name, **kwargs):
            return nullcontext()


# Prometheus metrics
from prometheus_client import (
    PLATFORM_COLLECTOR,
    PROCESS_COLLECTOR,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Import tenacity for retries with exponential backoff
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
    wait_random_exponential,
)

# Boto3 for AWS Secrets Manager
try:
    import boto3
    from botocore.exceptions import ClientError as BotoClientError

    BOTO3_AVAILABLE = True
except ImportError:
    boto3 = None
    BotoClientError = Exception
    BOTO3_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "Boto3 not found. Secrets Manager integration unavailable."
    )

# Neo4j imports with proper fallback
try:
    from neo4j import AsyncGraphDatabase as RealAsyncGraphDatabase
    from neo4j import AsyncManagedTransaction, AsyncSession, ManagedTransaction
    from neo4j.exceptions import (
        ClientError,
        DatabaseError,
        Neo4jError,
        ServiceUnavailable,
        SessionExpired,
    )

    NEO4J_AVAILABLE = True
    AsyncGraphDatabase = RealAsyncGraphDatabase
except ImportError:
    logging.getLogger(__name__).critical(
        "neo4j library not found. Neo4j Knowledge Graph functionality will be conceptual."
    )
    NEO4J_AVAILABLE = False

    # Mock Neo4j classes for graceful degradation
    class MockResult:
        def __init__(self, query, params):
            self._query = query
            self._params = params
            normalized_query = re.sub(r"\s+", " ", query.lower())
            if re.search(
                r"create\s+\(n:?`?.*?`?\)\s+set\s+n\s+=\s+\$", normalized_query
            ):
                self._data = [{"nodeId": "mock_node_id"}]
            elif re.search(
                r"create\s+\(a\)-\[r:?`?.*?`?\]->\(b\)\s+set\s+r\s+=\s+\$",
                normalized_query,
            ):
                self._data = [{"relId": "mock_rel_id"}]
            elif re.search(r"return\s+count\(n\)\s+as\s+node_count", normalized_query):
                self._data = [{"node_count": 1}]
            elif re.search(r"return\s+count\(r\)\s+as\s+count", normalized_query):
                self._data = [{"count": 1}]
            elif re.search(
                r"match\s+\(n\)\s+return\s+elementid\(n\)", normalized_query
            ):
                self._data = [
                    {
                        "eid": "nid-1",
                        "labels": ["Agent"],
                        "props": {"name": "SFE_Agent_001"},
                    }
                ]
            elif re.search(r"match\s+\(a\)-\[r\]->\(b\)\s+return", normalized_query):
                self._data = [
                    {
                        "rid": "rid-1",
                        "type": "DETECTED",
                        "start_eid": "nid-1",
                        "end_eid": "nid-2",
                        "props": {"timestamp": "2024-01-01T00:00:00Z"},
                    }
                ]
            else:
                self._data = []

        async def single(self):
            return self._data[0] if self._data else None

        async def data(self):
            return self._data

        async def consume(self):
            return None

    class MockTx:
        async def run(self, query, params=None, **kwargs):
            logging.getLogger(__name__).warning("Mock transaction run called.")
            return MockResult(query, params)

    # Mock AsyncManagedTransaction that matches the interface
    AsyncManagedTransaction = MockTx
    ManagedTransaction = MockTx

    class MockSession:
        def __init__(self, driver):
            self._driver = driver

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def execute_write(self, query_function, *args, **kwargs):
            logging.getLogger(__name__).warning(
                "Mock AsyncSession: execute_write called."
            )
            await asyncio.sleep(0.01)
            return await query_function(MockTx(), *args, **kwargs)

        async def execute_read(self, query_function, *args, **kwargs):
            logging.getLogger(__name__).warning(
                "Mock AsyncSession: execute_read called."
            )
            await asyncio.sleep(0.01)
            return await query_function(MockTx(), *args, **kwargs)

        async def close(self):
            logging.getLogger(__name__).debug("Mock AsyncSession closed.")

    AsyncSession = MockSession

    class MockDriver:
        def __init__(self, url, **kwargs):
            self._url = url

        async def verify_connectivity(self):
            logging.getLogger(__name__).debug(
                "Mock AsyncGraphDatabase connectivity verified."
            )

        def session(self, *args, **kwargs):
            return MockSession(self)

        async def close(self):
            logging.getLogger(__name__).debug("Mock AsyncGraphDatabase closed.")

        @staticmethod
        def driver(*args, **kwargs):
            return MockDriver(args[0])

    AsyncGraphDatabase = MockDriver

    class neo4j_exceptions:
        class ServiceUnavailable(Exception):
            pass

        class ClientError(Exception):
            pass

        class DatabaseError(Exception):
            pass

        class SessionExpired(Exception):
            pass

        class Neo4jError(Exception):
            pass

    ServiceUnavailable = neo4j_exceptions.ServiceUnavailable
    SessionExpired = neo4j_exceptions.SessionExpired
    Neo4jError = neo4j_exceptions.Neo4jError
    ClientError = neo4j_exceptions.ClientError
    DatabaseError = neo4j_exceptions.DatabaseError

# ruff: noqa: E501, F841, F401, E701
# black: off
# mypy: ignore-errors


# --- Custom Exception Classes for Structured Error Handling ---
class KnowledgeGraphError(Exception):
    """Base exception for all Knowledge Graph errors."""

    pass


class ConnectionError(KnowledgeGraphError):
    """Raised when there is a failure to connect to the database."""

    pass


class QueryError(KnowledgeGraphError):
    """Raised for Cypher query execution failures."""

    def __init__(self, message: str, original_error: Exception = None):
        super().__init__(message)
        self.original_error = original_error


class SchemaValidationError(KnowledgeGraphError):
    """Raised when input data fails Pydantic schema validation."""

    pass


class NodeNotFoundError(QueryError):
    """Raised when a specific node cannot be found."""

    pass


# --- Observability: Metrics, Tracing, and Logging ---
logger = logging.getLogger("neo4j_kg")
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

# Detect test/CI environment to skip thread-spawning OpenTelemetry initialization.
# Resource.create() spawns threads for resource detection, which can cause
# "can't start new thread" errors in resource-constrained CI environments.
# Note: We use local detection here rather than Environment.current() from otel_config
# to avoid potential circular imports and to include CI-specific checks (PYTEST_COLLECTING, CI)
# that are critical for the test collection phase.
_is_test_environment = any(
    [
        os.getenv("TESTING"),
        os.getenv("PYTEST_CURRENT_TEST"),
        os.getenv("PYTEST_COLLECTING"),
        os.getenv("CI"),
        "pytest" in sys.modules,
        "unittest" in sys.modules,
    ]
)

# OpenTelemetry setup - only set tracer provider if not already configured
# Skip in test environments to avoid thread exhaustion issues
if HAS_OPENTELEMETRY and not _is_test_environment:
    _existing_provider = trace.get_tracer_provider()
    _provider_needs_setup = (
        _existing_provider is None
        or not hasattr(_existing_provider, "add_span_processor")
        or type(_existing_provider).__name__ == "ProxyTracerProvider"
    )

    if _provider_needs_setup:
        trace.set_tracer_provider(
            TracerProvider(
                resource=Resource.create({"service.name": "sfe-knowledge-graph-db"})
            )
        )
        # Configurable OpenTelemetry Exporter via environment variable
        exporter_type = os.getenv("SFE_OTEL_EXPORTER_TYPE", "console").lower()
        if exporter_type == "otlp":
            exporter = OTLPSpanExporter()
            logger.info("Using OTLPSpanExporter for OpenTelemetry traces.")
        else:
            exporter = ConsoleSpanExporter()
            logger.info(
                "Using ConsoleSpanExporter for OpenTelemetry traces (default). Set SFE_OTEL_EXPORTER_TYPE=otlp for OTLP."
            )
        trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(exporter))

    # Get tracer with version compatibility handling
    try:
        tracer = trace.get_tracer(__name__)
    except TypeError:
        # Fallback for older OpenTelemetry versions that don't support all parameters
        # This can happen if opentelemetry-sdk version is older than opentelemetry-api
        try:
            tracer = trace.get_tracer(__name__, None)
        except TypeError:
            # If still failing, use no-op tracer
            tracer = NoOpTracer()
            logger.warning(
                "Failed to initialize OpenTelemetry tracer due to version compatibility issues. "
                "Using no-op tracer. Please ensure opentelemetry-api and opentelemetry-sdk versions match."
            )
elif _is_test_environment:
    # Test/CI environment detected - use no-op tracer to avoid thread exhaustion
    tracer = NoOpTracer()
    logger.info("Test environment detected - using no-op tracer for knowledge_graph_db")
else:
    # OpenTelemetry not available, use no-op tracer
    tracer = NoOpTracer()

# Unregister default collectors for clean testing environment
try:
    REGISTRY.unregister(PROCESS_COLLECTOR)
    REGISTRY.unregister(PLATFORM_COLLECTOR)
except KeyError:
    pass

# Metric registry for a cleaner, testable setup
KG_REGISTRY = CollectorRegistry(auto_describe=True)

# Cache for idempotency
_METRIC_CACHE: Dict[str, Union[Counter, Gauge, Histogram]] = {}


def _get_or_create_metric(
    metric_class: Union[Type[Counter], Type[Gauge], Type[Histogram]],
    name: str,
    documentation: str,
    labelnames: Tuple[str, ...] = (),
    buckets: Optional[Tuple[float, ...]] = None,
) -> Union[Counter, Gauge, Histogram]:
    """
    Idempotently get or create a Prometheus metric.
    """
    if name in _METRIC_CACHE:
        return _METRIC_CACHE[name]

    if buckets and metric_class is Histogram:
        metric = metric_class(
            name,
            documentation,
            labelnames=labelnames,
            buckets=buckets,
            registry=KG_REGISTRY,
        )
    else:
        metric = metric_class(
            name, documentation, labelnames=labelnames, registry=KG_REGISTRY
        )
    _METRIC_CACHE[name] = metric
    return metric


KG_OPS_TOTAL = _get_or_create_metric(
    Counter, "neo4j_kg_ops_total", "Total Neo4j KG operations", ["operation", "status"]
)
KG_OPS_LATENCY = _get_or_create_metric(
    Histogram,
    "neo4j_kg_latency_seconds",
    "Latency of Neo4j KG operations",
    ["operation"],
)
KG_CONNECTIONS = _get_or_create_metric(
    Gauge, "neo4j_kg_active_connections", "Active Neo4j connections"
)
KG_ERRORS = _get_or_create_metric(
    Counter,
    "neo4j_kg_errors_total",
    "Total errors in Neo4j KG",
    ["operation", "error_type"],
)

# Whitelist for safe Cypher identifiers
_NAME_RX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_name(name: str) -> str:
    if not _NAME_RX.match(name):
        raise SchemaValidationError(f"Illegal identifier: {name}")
    return f"`{name}`"


# --- Compliance: Auditable, Immutable Logging ---
class ImmutableAuditLogger:
    """
    A conceptual client for an immutable, tamper-evident audit log.
    In a real system, this would write to a WORM store, hash-chain, or similar.
    """

    def __init__(
        self,
        file_path: str = "audit_log.jsonl",
        max_bytes: int = 100 * 1024 * 1024,
        backup_count: int = 5,
    ):
        self.file_path = file_path
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.queue = asyncio.Queue()
        self._worker_task = None
        self._is_closing = False
        self._lock = asyncio.Lock()

    async def _worker(self):
        while True:
            event_data = await self.queue.get()
            if event_data is None:
                self.queue.task_done()
                break
            try:
                await self._rotate_log()
                with open(self.file_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event_data) + "\n")
            except Exception as e:
                logger.error(
                    f"Failed to write to immutable audit log: {e}", exc_info=True
                )
            self.queue.task_done()

    async def _rotate_log(self):
        async with self._lock:
            if (
                os.path.exists(self.file_path)
                and os.path.getsize(self.file_path) >= self.max_bytes
            ):
                logger.info("Rotating audit log file...")
                if self.backup_count > 0:
                    for i in range(self.backup_count - 1, 0, -1):
                        s = f"{self.file_path}.{i}"
                        d = f"{self.file_path}.{i + 1}"
                        if os.path.exists(s):
                            shutil.move(s, d)
                    shutil.move(self.file_path, f"{self.file_path}.1")
                logger.info("Audit log rotation complete.")

    async def log_event(self, event: str, details: Dict[str, Any]):
        """Logs a structured audit event to an append-only file."""
        if self._is_closing:
            logger.warning(
                "Attempted to log event while audit logger is closing. Event dropped."
            )
            return

        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker())
        log_message = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": details,
        }
        await self.queue.put(log_message)

    async def close(self):
        """
        Shuts down the worker and ensures all queued messages are written.

        This method is a no-op if log_event() was never called and the worker
        task was never created. This behavior is intentional.
        """
        if self._is_closing:
            return
        self._is_closing = True

        if self._worker_task is None:
            return

        logger.info("Initiating audit logger shutdown...")
        await self.queue.put(None)
        await self._worker_task
        logger.info("Audit logger closed successfully.")


# --- Correctness: Pydantic for Validation and Type Safety ---
class KGNode(BaseModel):
    label: str = Field(..., description="The primary label of the node.")
    properties: Dict[str, Any] = Field(
        ..., description="A dictionary of properties for the node."
    )

    model_config = ConfigDict(extra="forbid")


class KGRelationship(BaseModel):
    from_node_id: str = Field(..., description="The ID of the start node.")
    to_node_id: str = Field(..., description="The ID of the end node.")
    rel_type: str = Field(..., description="The type of the relationship.")
    properties: Dict[str, Any] = Field(
        default_factory=dict,
        description="A dictionary of properties for the relationship.",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("properties", mode="before")
    def validate_properties(cls, v):
        if not isinstance(v, dict):
            raise ValueError("Properties must be a dictionary.")
        for key, value in v.items():
            if key == "timestamp" and isinstance(value, str):
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if dt.tzinfo != timezone.utc:
                    v[key] = dt.astimezone(timezone.utc).isoformat()
        return v


# --- Main Neo4j Client Class ---
class Neo4jKnowledgeGraph:
    """
    Gold Standard Async Neo4j Knowledge Graph Client

    - Fully async, observable, auditable, and security-conscious.
    - Complete type safety and pydantic validation.
    - All actions traced, logged, metered, and (optionally) audited.
    - Pluggable for real Neo4j, memory, or test backends.
    - Robust error handling with retries and graceful degradation.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        audit_logger: Optional[ImmutableAuditLogger] = None,
        strict_ssl: bool = True,
        connect_timeout: int = 10,
        max_retries: int = 5,
        retry_delay_sec: float = 1.0,
        connection_pool_size: int = 50,
        statement_timeout: int = 60,
        connection_lifetime: int = 3600,
    ):
        """
        Initializes the Neo4j client.
        """
        self.url = url or os.getenv("NEO4J_URL", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = self._get_password()
        self.audit_logger = audit_logger or ImmutableAuditLogger()
        self.strict_ssl = strict_ssl
        self.connect_timeout = connect_timeout
        self.max_retries = max_retries
        self.retry_delay_sec = retry_delay_sec
        self.connection_pool_size = connection_pool_size
        self.statement_timeout = statement_timeout
        self.connection_lifetime = connection_lifetime
        self._driver: Optional[AsyncGraphDatabase] = None
        self._connected = False
        self._conn_gauge_inc = False
        self._temp_import_id_key = "__kg_import_tmp_eid"

        if (
            os.getenv("ENV", "dev") != "dev"
            and not os.getenv("USE_SECRETS_MANAGER", "false").lower() == "true"
        ):
            logger.warning(
                "Using NEO4J_PASSWORD env var in production; prefer Secrets Manager for security."
            )

        is_dev_mode = os.getenv("ENV", "dev") == "dev"
        if not self.password or self.password == "password":
            if is_dev_mode:
                logger.warning(
                    "Neo4jKnowledgeGraph: Running in development mode without a secure password. "
                    "Set NEO4J_PASSWORD environment variable for production use."
                )
            else:
                raise ConnectionError(
                    "A secure password must be provided via environment variable (NEO4J_PASSWORD) or a secrets manager."
                )

        logger.info(f"Neo4jKnowledgeGraph initialized for URL: {self.url}.")

    def _get_password(self) -> Optional[str]:
        """Fetch Neo4j password from AWS Secrets Manager or env vars."""
        if (
            os.getenv("USE_SECRETS_MANAGER", "false").lower() == "true"
            and os.getenv("ENV", "dev") != "dev"
            and BOTO3_AVAILABLE
        ):
            try:
                client = boto3.client(
                    "secretsmanager", region_name=os.getenv("AWS_REGION", "us-east-1")
                )
                secret_id = os.getenv("NEO4J_SECRET_ID", "neo4j/password")
                secret_value = client.get_secret_value(SecretId=secret_id)[
                    "SecretString"
                ]

                secret_json_key = os.getenv("NEO4J_SECRET_JSON_KEY")
                if secret_json_key:
                    secret_dict = json.loads(secret_value)
                    return secret_dict.get(secret_json_key)

                return secret_value
            except BotoClientError as e:
                logger.error(
                    f"Failed to fetch NEO4J_PASSWORD from Secrets Manager: {e}",
                    exc_info=True,
                )
                raise ConnectionError(f"Failed to fetch NEO4J_PASSWORD: {e}") from e
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(
                    f"Failed to parse Secrets Manager JSON payload or find key: {e}",
                    exc_info=True,
                )
                raise ConnectionError(
                    f"Invalid Secrets Manager JSON format or key missing: {e}"
                ) from e
        return os.getenv("NEO4J_PASSWORD")

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_random_exponential(multiplier=1, max=10),
        retry=retry_if_exception_type(
            (ServiceUnavailable, SessionExpired, asyncio.TimeoutError, ConnectionError)
        ),
        reraise=True,
    )
    async def _with_retry(self, func, *args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Neo4jError as e:
            logger.error(f"Neo4j query error: {e}", exc_info=True)
            raise QueryError(f"Neo4j query failed: {e}", original_error=e) from e
        except Exception as e:
            logger.error(
                f"An unexpected error occurred during database operation: {e}",
                exc_info=True,
            )
            raise

    async def _do_connect(self):
        if self._driver:
            await self._driver.close()
            self._driver = None

        if NEO4J_AVAILABLE:
            self._driver = AsyncGraphDatabase.driver(
                self.url,
                auth=(self.user, self.password),
                encrypted=self.strict_ssl,
                connection_acquisition_timeout=self.connect_timeout,
                max_connection_lifetime=self.connection_lifetime,
                max_connection_pool_size=self.connection_pool_size,
            )
            await self._driver.verify_connectivity()
            self._connected = True
            if not self._conn_gauge_inc:
                KG_CONNECTIONS.inc()
                self._conn_gauge_inc = True
            logger.info(f"Neo4jKnowledgeGraph: Connected to {self.url}.")
        else:
            self._driver = AsyncGraphDatabase.driver(self.url)
            self._connected = True
            logger.warning("Neo4j driver not available. Using mock driver.")
            if not self._conn_gauge_inc:
                KG_CONNECTIONS.inc()
                self._conn_gauge_inc = True

    async def health_check(self) -> bool:
        op = "health_check"
        with tracer.start_as_current_span(f"neo4j_{op}") as span:
            KG_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            start_time = time.monotonic()
            try:
                if not self._connected or not self._driver:
                    return False

                await self._with_retry(self._driver.verify_connectivity)

                KG_OPS_TOTAL.labels(operation=op, status="success").inc()
                span.set_status(Status(StatusCode.OK))
                return True
            except Exception as e:
                KG_OPS_TOTAL.labels(operation=op, status="failure").inc()
                KG_ERRORS.labels(operation=op, error_type=type(e).__name__).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Health check failed: {e}"))
                logger.error(f"Health check failed: {e}", exc_info=True)
                return False
            finally:
                KG_OPS_LATENCY.labels(operation=op).observe(
                    time.monotonic() - start_time
                )

    async def connect(self):
        op = "connect"
        with tracer.start_as_current_span(f"neo4j_{op}") as span:
            KG_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            start_time = time.monotonic()
            try:
                await self._with_retry(self._do_connect)
                await self.audit_logger.log_event(
                    op, {"url": self.url, "user": self.user, "status": "success"}
                )
                KG_OPS_TOTAL.labels(operation=op, status="success").inc()
                span.set_status(Status(StatusCode.OK))
            except Exception as e:
                KG_OPS_TOTAL.labels(operation=op, status="failure").inc()
                KG_ERRORS.labels(operation=op, error_type=type(e).__name__).inc()
                logger.error(f"Failed to connect: {e}", exc_info=True)
                await self.audit_logger.log_event(
                    "connect_failure",
                    {"url": self.url, "user": self.user, "error": str(e)},
                )
                span.record_exception(e)
                span.set_status(
                    Status(StatusCode.ERROR, f"Failed to establish connection: {e}")
                )
                raise ConnectionError(
                    f"Failed to establish connection to Neo4j at {self.url}"
                ) from e
            finally:
                KG_OPS_LATENCY.labels(operation=op).observe(
                    time.monotonic() - start_time
                )

    async def disconnect(self):
        op = "disconnect"
        with tracer.start_as_current_span(f"neo4j_{op}") as span:
            KG_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            start_time = time.monotonic()
            if not self._connected:
                logger.warning("Attempted to disconnect a non-connected client.")
                KG_OPS_TOTAL.labels(operation=op, status="skipped").inc()
                if self.audit_logger:
                    await self.audit_logger.close()
                return
            try:
                if self._driver:
                    await self._driver.close()
                    self._driver = None

                self._connected = False
                if self._conn_gauge_inc:
                    KG_CONNECTIONS.dec()
                    self._conn_gauge_inc = False
                await self.audit_logger.log_event(
                    op, {"url": self.url, "user": self.user, "status": "success"}
                )
                KG_OPS_TOTAL.labels(operation=op, status="success").inc()
                span.set_status(Status(StatusCode.OK))
                logger.info("Neo4jKnowledgeGraph: Disconnected.")
            except Exception as e:
                KG_OPS_TOTAL.labels(operation=op, status="failure").inc()
                KG_ERRORS.labels(operation=op, error_type=type(e).__name__).inc()
                logger.error(f"Failed to disconnect: {e}", exc_info=True)
                await self.audit_logger.log_event(
                    "disconnect_failure",
                    {"url": self.url, "user": self.user, "error": str(e)},
                )
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Failed to disconnect: {e}"))
                raise ConnectionError(f"Failed to disconnect from Neo4j: {e}") from e
            finally:
                if self.audit_logger:
                    await self.audit_logger.close()
                KG_OPS_LATENCY.labels(operation=op).observe(
                    time.monotonic() - start_time
                )

    async def _execute_tx(
        self,
        tx: AsyncManagedTransaction,
        query: str,
        params: Dict[str, Any],
        write: bool = False,
    ) -> Any:
        """Internal helper to execute a transaction with tracing and sanitization."""
        op_type = "write" if write else "read"
        sanitized_params = {
            k: (
                "<REDACTED>"
                if "password" in k.lower()
                or "secret" in k.lower()
                or "token" in k.lower()
                else v
            )
            for k, v in params.items()
        }
        logger.debug(
            f"Executing {op_type} query: {query} with params: {sanitized_params}"
        )
        with tracer.start_as_current_span(f"neo4j_execute_{op_type}_tx") as span:
            span.set_attribute("db.statement", query)
            span.set_attribute(
                "db.statement.parameters", json.dumps(sanitized_params, default=str)
            )
            span.set_attribute("db.operation.type", op_type)
            result = await tx.run(query, params, timeout=float(self.statement_timeout))
            if write:
                return await result.single()
            else:
                return [record for record in await result.data()]

    async def add_node(self, label: str, properties: Dict[str, Any]) -> str:
        op = "add_node"
        with tracer.start_as_current_span(f"neo4j_{op}") as span:
            KG_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            start_time = time.monotonic()
            try:
                safe_label = _safe_name(label)

                hashed_properties = {}
                for key, value in properties.items():
                    if "id" in key.lower() or "email" in key.lower():
                        hashed_properties[key] = hashlib.sha256(
                            str(value).encode()
                        ).hexdigest()
                    else:
                        hashed_properties[key] = value

                node = KGNode(label=label, properties=hashed_properties)

                query = (
                    f"CREATE (n:{safe_label}) "
                    "SET n = $properties "
                    "RETURN elementId(n) AS nodeId"
                )
                params = {"properties": node.properties}

                if not self._driver:
                    raise ConnectionError("Neo4j driver not connected.")

                async with self._driver.session() as session:
                    result = await self._with_retry(
                        session.execute_write,
                        self._execute_tx,
                        query,
                        params,
                        write=True,
                    )

                node_id = str(result["nodeId"]) if result else "mock_node_id"
                span.set_attribute("db.node_id", node_id)

                logger.info(f"Node created: {label} (id={node_id})")
                await self.audit_logger.log_event(
                    op,
                    {
                        "label": label,
                        "properties": hashed_properties,
                        "node_id": node_id,
                    },
                )
                KG_OPS_TOTAL.labels(operation=op, status="success").inc()
                span.set_status(Status(StatusCode.OK))
                return node_id
            except ValidationError as e:
                KG_OPS_TOTAL.labels(operation=op, status="failure").inc()
                KG_ERRORS.labels(operation=op, error_type="ValidationError").inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Validation failed: {e}"))
                logger.error(
                    f"Pydantic validation failed for add_node: {e}", exc_info=True
                )
                raise SchemaValidationError(
                    f"Invalid input for node creation: {e}"
                ) from e
            except Exception as e:
                KG_OPS_TOTAL.labels(operation=op, status="failure").inc()
                KG_ERRORS.labels(operation=op, error_type=type(e).__name__).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Failed to add node: {e}"))
                logger.error(f"Failed to add_node: {e}", exc_info=True)
                raise QueryError("Failed to create node.", original_error=e) from e
            finally:
                KG_OPS_LATENCY.labels(operation=op).observe(
                    time.monotonic() - start_time
                )

    async def add_relationship(
        self,
        from_node_id: str,
        to_node_id: str,
        rel_type: str,
        properties: Dict[str, Any],
    ) -> str:
        op = "add_relationship"
        with tracer.start_as_current_span(f"neo4j_{op}") as span:
            KG_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            start_time = time.monotonic()
            try:
                rel = KGRelationship(
                    from_node_id=from_node_id,
                    to_node_id=to_node_id,
                    rel_type=rel_type,
                    properties=properties,
                )

                safe_rel_type = _safe_name(rel.rel_type)

                query = (
                    "MATCH (a), (b) "
                    "WHERE elementId(a) = $from_node_id AND elementId(b) = $to_node_id "
                    f"CREATE (a)-[r:{safe_rel_type}]->(b) "
                    "SET r = $properties "
                    "RETURN elementId(r) AS relId"
                )
                params = {
                    "from_node_id": rel.from_node_id,
                    "to_node_id": rel.to_node_id,
                    "properties": rel.properties,
                }

                sanitized_rel_props = {}
                for k, v in rel.properties.items():
                    if "id" in k.lower() or "email" in k.lower():
                        sanitized_rel_props[k] = hashlib.sha256(
                            str(v).encode()
                        ).hexdigest()
                    else:
                        sanitized_rel_props[k] = v
                sanitized_rel_data = rel.model_dump()
                sanitized_rel_data["properties"] = sanitized_rel_props

                if not self._driver:
                    raise ConnectionError("Neo4j driver not connected.")

                async with self._driver.session() as session:
                    result = await self._with_retry(
                        session.execute_write,
                        self._execute_tx,
                        query,
                        params,
                        write=True,
                    )

                rel_id = str(result["relId"]) if result else "mock_rel_id"
                span.set_attribute("db.relationship_id", rel_id)

                logger.info(
                    f"Relationship created: {from_node_id} -[{rel_type}]-> {to_node_id} (id={rel_id})"
                )
                await self.audit_logger.log_event(op, sanitized_rel_data)
                KG_OPS_TOTAL.labels(operation=op, status="success").inc()
                span.set_status(Status(StatusCode.OK))
                return rel_id
            except ValidationError as e:
                KG_OPS_TOTAL.labels(operation=op, status="failure").inc()
                KG_ERRORS.labels(operation=op, error_type="ValidationError").inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Validation failed: {e}"))
                logger.error(
                    f"Pydantic validation failed for add_relationship: {e}",
                    exc_info=True,
                )
                raise SchemaValidationError(
                    f"Invalid input for relationship creation: {e}"
                ) from e
            except Exception as e:
                KG_OPS_TOTAL.labels(operation=op, status="failure").inc()
                KG_ERRORS.labels(operation=op, error_type=type(e).__name__).inc()
                span.record_exception(e)
                span.set_status(
                    Status(StatusCode.ERROR, f"Failed to add relationship: {e}")
                )
                logger.error(f"Failed to add_relationship: {e}", exc_info=True)
                raise QueryError(
                    "Failed to create relationship.", original_error=e
                ) from e
            finally:
                KG_OPS_LATENCY.labels(operation=op).observe(
                    time.monotonic() - start_time
                )

    async def find_related_facts(
        self, domain: str, key: str, value: Any
    ) -> List[Dict[str, Any]]:
        op = "find_related_facts"
        with tracer.start_as_current_span(f"neo4j_{op}") as span:
            KG_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            start_time = time.monotonic()
            try:
                safe_domain = _safe_name(domain)
                safe_key = _safe_name(key)

                query = (
                    f"MATCH (n:{safe_domain}) WHERE n.{safe_key} = $value "
                    "OPTIONAL MATCH (n)-[r]-(m) "
                    "RETURN n, r, m"
                )
                params = {"value": value}

                if not self._driver:
                    raise ConnectionError("Neo4j driver not connected.")

                async with self._driver.session() as session:
                    results = await self._with_retry(
                        session.execute_read, self._execute_tx, query, params
                    )

                logger.info(
                    f"Query for related facts: {domain}.{key}={value}. Found {len(results)} results."
                )
                await self.audit_logger.log_event(
                    op,
                    {
                        "domain": domain,
                        "key": key,
                        "value": value,
                        "results_count": len(results),
                    },
                )
                KG_OPS_TOTAL.labels(operation=op, status="success").inc()
                span.set_status(Status(StatusCode.OK))
                return results
            except Exception as e:
                KG_OPS_TOTAL.labels(operation=op, status="failure").inc()
                KG_ERRORS.labels(operation=op, error_type=type(e).__name__).inc()
                span.record_exception(e)
                span.set_status(
                    Status(StatusCode.ERROR, f"Failed to find related facts: {e}")
                )
                logger.error(f"Failed to find_related_facts: {e}", exc_info=True)
                raise QueryError(
                    "Failed to find related facts.", original_error=e
                ) from e
            finally:
                KG_OPS_LATENCY.labels(operation=op).observe(
                    time.monotonic() - start_time
                )

    async def check_consistency(
        self, domain: str, key: str, value: Any
    ) -> Optional[str]:
        op = "check_consistency"
        with tracer.start_as_current_span(f"neo4j_{op}") as span:
            KG_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            start_time = time.monotonic()
            try:
                safe_domain = _safe_name(domain)
                safe_key = _safe_name(key)

                query = (
                    f"MATCH (n:{safe_domain}) WHERE n.{safe_key} = $value "
                    "RETURN count(n) AS node_count"
                )
                params = {"value": value}

                if not self._driver:
                    raise ConnectionError("Neo4j driver not connected.")

                async with self._driver.session() as session:
                    result = await self._with_retry(
                        session.execute_read, self._execute_tx, query, params
                    )

                node_count = result[0]["node_count"] if result and result[0] else 0

                if node_count == 0:
                    consistency_message = f"No nodes found for {domain}.{key}={value}. Possible inconsistency."
                    logger.warning(consistency_message)
                    return consistency_message

                logger.info(
                    f"Consistency check: {domain}.{key}={value}. Found {node_count} nodes."
                )
                await self.audit_logger.log_event(
                    op,
                    {
                        "domain": domain,
                        "key": key,
                        "value": value,
                        "node_count": node_count,
                    },
                )
                KG_OPS_TOTAL.labels(operation=op, status="success").inc()
                span.set_status(Status(StatusCode.OK))
                return None
            except Exception as e:
                KG_OPS_TOTAL.labels(operation=op, status="failure").inc()
                KG_ERRORS.labels(operation=op, error_type=type(e).__name__).inc()
                span.record_exception(e)
                span.set_status(
                    Status(StatusCode.ERROR, f"Consistency check failed: {e}")
                )
                logger.error(f"Failed to check_consistency: {e}", exc_info=True)
                return f"Inconsistency check failed due to a system error: {e}"
            finally:
                KG_OPS_LATENCY.labels(operation=op).observe(
                    time.monotonic() - start_time
                )

    async def _export_nodes(
        self, session: AsyncSession, filename: str, chunk_size: int, node_total: int
    ):
        nodes_query_template = "MATCH (n) RETURN elementId(n) AS eid, labels(n) AS labels, properties(n) AS props ORDER BY eid SKIP $skip LIMIT $limit"
        logger.info(f"Exporting {node_total} nodes to {filename}.nodes.jsonl.gz...")
        with gzip.open(f"{filename}.nodes.jsonl.gz", "wt", encoding="utf-8") as f:
            skip = 0
            while skip < node_total:
                nodes_results = await self._with_retry(
                    session.execute_read,
                    self._execute_tx,
                    nodes_query_template,
                    {"skip": skip, "limit": chunk_size},
                )
                if not nodes_results:
                    break
                for record in nodes_results:
                    f.write(json.dumps(record) + "\n")
                skip += chunk_size
                logger.debug(f"Exported {skip} / {node_total} nodes.")
                await asyncio.sleep(0)

    async def _export_relationships(
        self, session: AsyncSession, filename: str, chunk_size: int, rel_total: int
    ):
        rels_query_template = "MATCH (a)-[r]->(b) RETURN elementId(r) AS rid, type(r) AS type, elementId(startNode(r)) AS start_eid, elementId(endNode(r)) AS end_eid, properties(r) AS props ORDER BY rid SKIP $skip LIMIT $limit"
        logger.info(
            f"Exporting {rel_total} relationships to {filename}.rels.jsonl.gz..."
        )
        with gzip.open(f"{filename}.rels.jsonl.gz", "wt", encoding="utf-8") as f:
            skip = 0
            while skip < rel_total:
                rels_results = await self._with_retry(
                    session.execute_read,
                    self._execute_tx,
                    rels_query_template,
                    {"skip": skip, "limit": chunk_size},
                )
                if not rels_results:
                    break
                for record in rels_results:
                    f.write(json.dumps(record) + "\n")
                skip += chunk_size
                logger.debug(f"Exported {skip} / {rel_total} relationships.")
                await asyncio.sleep(0)

    async def export_graph(self, filename: str, chunk_size: int = 1000):
        op = "export_graph"
        with tracer.start_as_current_span(f"neo4j_{op}") as span:
            KG_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            start_time = time.monotonic()
            try:
                if not self._driver:
                    raise ConnectionError("Neo4j driver not connected.")
                async with self._driver.session() as session:
                    nodes_count_query = "MATCH (n) RETURN count(n) AS count"
                    node_total_result = await self._with_retry(
                        session.execute_read, self._execute_tx, nodes_count_query, {}
                    )
                    node_total = (
                        node_total_result[0]["count"]
                        if node_total_result and node_total_result[0]
                        else 0
                    )

                    rels_count_query = "MATCH ()-[r]->() RETURN count(r) AS count"
                    rel_total_result = await self._with_retry(
                        session.execute_read, self._execute_tx, rels_count_query, {}
                    )
                    rel_total = (
                        rel_total_result[0]["count"]
                        if rel_total_result and rel_total_result[0]
                        else 0
                    )

                    await self._export_nodes(session, filename, chunk_size, node_total)
                    await self._export_relationships(
                        session, filename, chunk_size, rel_total
                    )

                logger.info(
                    f"Graph export to {filename}.nodes.jsonl.gz and {filename}.rels.jsonl.gz complete."
                )
                await self.audit_logger.log_event(
                    op,
                    {
                        "filename": filename,
                        "status": "success",
                        "nodes_exported": node_total,
                        "rels_exported": rel_total,
                    },
                )
                KG_OPS_TOTAL.labels(operation=op, status="success").inc()
                span.set_status(Status(StatusCode.OK))
            except Exception as e:
                KG_OPS_TOTAL.labels(operation=op, status="failure").inc()
                KG_ERRORS.labels(operation=op, error_type=type(e).__name__).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Graph export failed: {e}"))
                logger.error(
                    f"Failed to export graph to {filename}: {e}", exc_info=True
                )
                raise QueryError("Failed to export graph.", original_error=e) from e
            finally:
                KG_OPS_LATENCY.labels(operation=op).observe(
                    time.monotonic() - start_time
                )

    async def _import_nodes(
        self, session: AsyncSession, filename: str, chunk_size: int, validate: bool
    ):
        nodes_imported = 0
        nodes_file = f"{filename}.nodes.jsonl.gz"
        if not os.path.exists(nodes_file):
            logger.warning(f"Nodes file not found: {nodes_file}. Skipping node import.")
            return nodes_imported
        logger.info(f"Importing nodes from {nodes_file}...")
        batch = []
        with gzip.open(nodes_file, "rt", encoding="utf-8") as f:
            for line in f:
                node_data = json.loads(line)
                if validate:
                    node = KGNode(
                        label=(
                            node_data["labels"][0] if node_data["labels"] else "Generic"
                        ),
                        properties=node_data["props"],
                    )
                    node.properties[self._temp_import_id_key] = node_data["eid"]
                else:
                    node = type(
                        "Node",
                        (object,),
                        {
                            "label": (
                                node_data["labels"][0]
                                if node_data["labels"]
                                else "Generic"
                            ),
                            "properties": node_data["props"],
                        },
                    )()
                    node.properties[self._temp_import_id_key] = node_data["eid"]
                batch.append(node)
                if len(batch) >= chunk_size:
                    await self._import_nodes_batch(session, batch)
                    nodes_imported += len(batch)
                    batch = []
                    logger.debug(f"Imported {nodes_imported} nodes.")
                    await asyncio.sleep(0)
            if batch:
                await self._import_nodes_batch(session, batch)
                nodes_imported += len(batch)
        logger.info(f"Finished importing {nodes_imported} nodes.")
        return nodes_imported

    async def _import_relationships(
        self, session: AsyncSession, filename: str, chunk_size: int, validate: bool
    ):
        rels_imported = 0
        rels_file = f"{filename}.rels.jsonl.gz"
        if not os.path.exists(rels_file):
            logger.warning(
                f"Relationships file not found: {rels_file}. Skipping relationship import."
            )
            return rels_imported
        logger.info(f"Importing relationships from {rels_file}...")
        batch = []
        with gzip.open(rels_file, "rt", encoding="utf-8") as f:
            for line in f:
                rel_data = json.loads(line)
                if validate:
                    rel = KGRelationship(
                        from_node_id=rel_data["start_eid"],
                        to_node_id=rel_data["end_eid"],
                        rel_type=rel_data["type"],
                        properties=rel_data["props"],
                    )
                else:
                    rel = type(
                        "Relationship",
                        (object,),
                        {
                            "from_node_id": rel_data["start_eid"],
                            "to_node_id": rel_data["end_eid"],
                            "rel_type": rel_data["type"],
                            "properties": rel_data["props"],
                        },
                    )()
                batch.append(rel)
                if len(batch) >= chunk_size:
                    await self._import_relationships_batch(session, batch)
                    rels_imported += len(batch)
                    batch = []
                    logger.debug(f"Imported {rels_imported} relationships.")
                    await asyncio.sleep(0)
            if batch:
                await self._import_relationships_batch(session, batch)
                rels_imported += len(batch)
        logger.info(f"Finished importing {rels_imported} relationships.")
        return rels_imported

    async def import_graph(
        self, filename: str, chunk_size: int = 1000, validate: bool = True
    ):
        op = "import_graph"
        with tracer.start_as_current_span(f"neo4j_{op}") as span:
            KG_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            start_time = time.monotonic()
            nodes_imported = 0
            rels_imported = 0
            try:
                if not self._driver:
                    raise ConnectionError("Neo4j driver not connected.")
                async with self._driver.session() as session:
                    nodes_imported = await self._import_nodes(
                        session, filename, chunk_size, validate
                    )
                    rels_imported = await self._import_relationships(
                        session, filename, chunk_size, validate
                    )
                    cleanup_query = f"MATCH (n) WHERE n.`{self._temp_import_id_key}` IS NOT NULL REMOVE n.`{self._temp_import_id_key}`"
                    await self._with_retry(
                        session.execute_write,
                        self._execute_tx,
                        cleanup_query,
                        {},
                        write=True,
                    )

                logger.info(
                    f"Graph import from {filename} complete. Nodes: {nodes_imported}, Relationships: {rels_imported}."
                )
                await self.audit_logger.log_event(
                    op,
                    {
                        "filename": filename,
                        "status": "success",
                        "nodes_imported": nodes_imported,
                        "rels_imported": rels_imported,
                    },
                )
                KG_OPS_TOTAL.labels(operation=op, status="success").inc()
                span.set_status(Status(StatusCode.OK))
            except Exception as e:
                KG_OPS_TOTAL.labels(operation=op, status="failure").inc()
                KG_ERRORS.labels(operation=op, error_type=type(e).__name__).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Graph import failed: {e}"))
                logger.error(
                    f"Failed to import graph from {filename}: {e}", exc_info=True
                )
                raise QueryError("Failed to import graph.", original_error=e) from e
            finally:
                KG_OPS_LATENCY.labels(operation=op).observe(
                    time.monotonic() - start_time
                )

    async def _import_nodes_batch(self, session: AsyncSession, nodes: List[KGNode]):
        query_parts = []
        params = {}
        for i, node in enumerate(nodes):
            safe_label = _safe_name(node.label)
            query_parts.append(f"CREATE (n{i}:{safe_label}) SET n{i} = $props{i}")
            params[f"props{i}"] = node.properties

        query = " ".join(query_parts)
        await self._with_retry(
            session.execute_write, self._execute_tx, query, params, write=True
        )
        logger.debug(f"Batch imported {len(nodes)} nodes.")

    async def _import_relationships_batch(
        self, session: AsyncSession, relationships: List[KGRelationship]
    ):
        query_parts = []
        params = {}
        for i, rel in enumerate(relationships):
            safe_rel_type = _safe_name(rel.rel_type)
            query_parts.append(
                f"MATCH (a{i} {{`{self._temp_import_id_key}`: $from_node_id{i}}}), (b{i} {{`{self._temp_import_id_key}`: $to_node_id{i}}}) "
                f"CREATE (a{i})-[r{i}:{safe_rel_type}]->(b{i}) "
                f"SET r{i} = $props{i}"
            )
            params[f"from_node_id{i}"] = rel.from_node_id
            params[f"to_node_id{i}"] = rel.to_node_id
            params[f"props{i}"] = rel.properties

        query = " ".join(query_parts)
        await self._with_retry(
            session.execute_write, self._execute_tx, query, params, write=True
        )
        logger.debug(f"Batch imported {len(relationships)} relationships.")
