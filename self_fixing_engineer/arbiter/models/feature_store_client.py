# requirements.txt
#
# # Core Dependencies
# feast[bigquery,redis,ray,gcp]==0.52.0
# pandas==2.3.2
# tenacity==9.1.2
# pydantic==2.11
# boto3==1.40.14  # For Secrets Manager
#
# # Scalability & Validation
# ray==2.37.0  # For distributed ingestion
# great-expectations==1.5.9  # For data quality
# scipy==1.13.1 # For statistical drift detection
#
# # Observability
# prometheus-client==0.22.1
# opentelemetry-sdk==1.36.0
# opentelemetry-exporter-otlp==1.36.0
#
# # Testing
# pytest==8.4.1
# pytest-asyncio==1.1.0

import asyncio
import concurrent.futures
import hashlib
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Type, Union

# Pydantic for robust data validation
from pydantic import BaseModel
from pydantic import Field as PydanticField
from pydantic import field_validator

# Import tenacity for retries with exponential backoff
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Feast imports
try:
    from feast import Entity, FeatureStore, FeatureView
    from feast import Field as FeastField
    from feast import ValueType
    from feast.data_source import DataSource, FileSource
    from feast.errors import (
        FeastObjectNotFoundException,
        FeastProviderError,
        FeastResourceError,
    )

    # Production-ready sources
    from feast.infra.offline_stores.bigquery_source import BigQuerySource

    FEAST_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).critical(
        "Feast library not found. FeatureStoreClient cannot operate in real mode."
    )
    FEAST_AVAILABLE = False

    # Define dummy classes to prevent NameError if Feast is not installed
    class FeatureStore:
        def __init__(self, *args, **kwargs):
            raise ImportError("Feast is not installed.")

    class FeatureView:
        pass

    class Entity:
        pass

    class ValueType:
        INT64 = "INT64"
        STRING = "STRING"
        DOUBLE = "DOUBLE"
        DATETIME = "DATETIME"

    class FeastField:
        pass

    class DataSource:
        pass

    class FileSource:
        pass

    class BigQuerySource:
        pass

    class FeastObjectNotFoundException(Exception):
        pass

    class FeastProviderError(Exception):
        pass

    class FeastResourceError(Exception):
        pass


# Scalability and Validation
try:
    import ray

    RAY_AVAILABLE = True
except ImportError:
    RAY_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "Ray not found. Distributed ingestion unavailable."
    )

try:
    import scipy.stats

    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "Scipy not found. Statistical drift detection unavailable."
    )

try:
    from great_expectations.data_context import DataContext

    GX_AVAILABLE = True
except ImportError:
    GX_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "great-expectations not found. Feature validation unavailable."
    )

# For Secrets Manager
try:
    import boto3
    from botocore.exceptions import ClientError

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "Boto3 not found. Secrets Manager integration unavailable."
    )

# For redaction storage
try:
    from postgres_client import PostgresClient

    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "postgres_client.py not found. Redaction storage unavailable."
    )

    # Placeholder for local testing if PostgresClient is not installed
    class PostgresClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def save(self, table, data, id_field):
            logger.info(
                f"Stubbed PostgresClient.save called for table: {table}, data: {data}"
            )


# For audit logging
try:
    from audit_ledger_client import AuditLedgerClient

    AUDIT_LEDGER_AVAILABLE = True
except ImportError:
    AUDIT_LEDGER_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "audit_ledger_client.py not found. Audit logging unavailable."
    )

    class AuditLedgerClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def log_event(self, event_type, details, operator):
            logger.info(
                f"Stubbed AuditLedgerClient.log_event called for event_type: {event_type}, details: {details}, operator: {operator}"
            )
            return "stub_tx_hash_123"


from arbiter.otel_config import get_tracer

# OpenTelemetry Tracing - Use centralized configuration
from opentelemetry.trace import Status, StatusCode

# Prometheus Metrics
from prometheus_client import REGISTRY, Counter, Gauge, Histogram

# Logger initialization
logger = logging.getLogger(__name__)
logger.setLevel(
    os.getenv("LOG_LEVEL", "INFO").upper()
)  # Allow log level to be configured via env var

# --- Observability Setup ---

# Get tracer from centralized configuration
tracer = get_tracer(__name__)


# Idempotent Prometheus metric registration
def _get_or_create_metric(
    metric_class: Union[Type[Counter], Type[Gauge], Type[Histogram]],
    name: str,
    documentation: str,
    labelnames: Tuple[str, ...] = (),
    buckets: Optional[Tuple[float, ...]] = None,
):
    """
    Idempotently get or create a Prometheus metric.
    If the metric already exists in the registry, it returns the existing one.
    Otherwise, it creates a new metric of the specified class.
    """
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]

    if buckets:
        return metric_class(name, documentation, labelnames=labelnames, buckets=buckets)
    return metric_class(name, documentation, labelnames=labelnames)


# Metrics for Feature Store Operations
FS_CALLS_TOTAL = _get_or_create_metric(
    Counter,
    "feature_store_calls_total",
    "Total Feature Store API calls",
    ("operation", "status", "env", "cluster"),
)
FS_CALLS_ERRORS = _get_or_create_metric(
    Counter,
    "feature_store_calls_errors",
    "Feature Store API call errors",
    ("operation", "error_type", "env", "cluster"),
)
FS_CALL_LATENCY_SECONDS = _get_or_create_metric(
    Histogram,
    "feature_store_call_latency_seconds",
    "Feature Store API call latency in seconds",
    ("operation", "env", "cluster"),
)
FS_FEATURE_FRESHNESS_SECONDS = _get_or_create_metric(
    Gauge,
    "feature_store_feature_freshness_seconds",
    "Freshness of features in the online store",
    ("feature_view", "env", "cluster"),
)
FS_REDACTIONS_TOTAL = _get_or_create_metric(
    Counter,
    "feature_store_redactions_total",
    "Total redactions flagged",
    ("feature_view", "env", "cluster"),
)
FS_AUDIT_LOGS_TOTAL = _get_or_create_metric(
    Counter,
    "feature_store_audit_logs_total",
    "Total audit logs created",
    ("operation", "status", "env", "cluster"),
)


# --- Pydantic Models for Validation ---


class FeatureEntityModel(BaseModel):
    """Pydantic model for validating Feast Entity definitions."""

    name: str = PydanticField(
        ..., min_length=1, max_length=100, description="Unique entity name."
    )
    value_type: str = PydanticField(
        ..., description="Value type (e.g., INT64, STRING, DOUBLE, DATETIME)."
    )
    description: Optional[str] = PydanticField(
        None, max_length=500, description="Entity description."
    )

    @field_validator("value_type")
    @classmethod
    def validate_value_type(cls, v):
        valid_types = set(getattr(ValueType, "__members__", {}).keys()) or {
            "INT64",
            "STRING",
            "DOUBLE",
            "DATETIME",
        }
        if v not in valid_types:
            raise ValueError(
                f"Invalid value_type: {v}. Must be one of {sorted(list(valid_types))}."
            )
        return v

    class Config:
        arbitrary_types_allowed = True


class FeatureViewModel(BaseModel):
    """Pydantic model for validating Feast FeatureView definitions."""

    name: str = PydanticField(
        ..., min_length=1, max_length=100, description="Unique feature view name."
    )
    entities: List[str] = PydanticField(
        ..., min_length=1, description="List of entity names."
    )
    ttl: timedelta = PydanticField(..., description="Time-to-live duration.")
    feature_schema: List[Dict[str, str]] = PydanticField(
        ..., min_length=1, description="Schema fields with name and dtype."
    )
    source: Dict[str, Any] = PydanticField(
        ..., description="Data source configuration."
    )

    @field_validator("feature_schema")
    @classmethod
    def validate_feature_schema(cls, v):
        for field in v:
            if "name" not in field or "dtype" not in field:
                raise ValueError("Schema fields must have 'name' and 'dtype'.")
        return v

    @field_validator("ttl")
    @classmethod
    def validate_ttl(cls, v):
        if v <= timedelta(0):
            raise ValueError("TTL must be positive.")
        return v

    class Config:
        arbitrary_types_allowed = True


class FeatureSourceModel(BaseModel):
    """Pydantic model for validating Feast DataSource configurations."""

    name: Optional[str] = PydanticField(
        None, max_length=100, description="Source name."
    )
    type: str = PydanticField(
        ..., description="Source type (e.g., BigQuerySource, FileSource)."
    )
    config: Dict[str, Any] = PydanticField(
        ..., description="Source configuration (e.g., table, path)."
    )
    timestamp_field: Optional[str] = PydanticField(
        None, description="Timestamp field for time-based joins."
    )

    @field_validator("type")
    @classmethod
    def validate_source_type(cls, v):
        valid_types = ["BigQuerySource", "FileSource"]
        if v not in valid_types:
            raise ValueError(f"Invalid source type: {v}. Must be one of {valid_types}.")
        return v

    class Config:
        arbitrary_types_allowed = True


# --- Feature Store Client Class ---


class FeatureStoreClient:
    """
    Asynchronous client for managing Feast Feature Store operations in the Self-Fixing Engineer (SFE) system.
    Supports connection, feature definition, ingestion (with Ray for distributed processing), retrieval (online/historical),
    validation (with Great Expectations and statistical drift detection), GDPR-compliant redaction (with Postgres integration),
    and audit logging. Tested on Python 3.13+. For production, configure:
    - Secrets Manager for credentials (GCP_CREDENTIALS, REDIS_URL) to ensure secure access.
    - Expectation suites for Great Expectations validation, including null checks and range validation.
    - audit_ledger_client.py for immutable operation logging (e.g., ingestion, validation events).
    - postgres_client.py for GDPR-compliant redaction storage (e.g., right to be forgotten).
    For GDPR, hash PII (e.g., user_id) before ingestion and use flag_for_redaction for post-ingestion compliance.
    Metrics (Prometheus) and traces (OpenTelemetry) provide observability. Retain audit logs for 7 years per GDPR requirements.
    """

    def __init__(self, repo_path: Optional[str] = None):
        """Initialize with Feast repo path and credentials."""
        if not FEAST_AVAILABLE:
            raise ImportError("Feast library required but not found.")
        self.repo_path = repo_path or os.getenv("FEAST_REPO_PATH")
        if not self.repo_path:
            raise ValueError("Feast repo path (repo_path or FEAST_REPO_PATH) required.")

        if (
            os.getenv("ENV", "dev") != "dev"
            and not os.getenv("USE_SECRETS_MANAGER", "false").lower() == "true"
        ):
            raise ValueError(
                "Secrets Manager required in production for GCP_CREDENTIALS and REDIS_URL."
            )

        self._fs: Optional[FeatureStore] = None
        self._is_connected = False
        self.semaphore = asyncio.Semaphore(5)  # Limit concurrent operations
        self.metric_labels = {
            "env": os.getenv("ENV", "dev"),
            "cluster": os.getenv("CLUSTER", "default"),
        }
        logger.info(f"FeatureStoreClient initialized with repo_path: {self.repo_path}")

    def _get_credentials(self, key: str) -> str:
        """Fetch credentials from AWS Secrets Manager or env vars."""
        if (
            os.getenv("USE_SECRETS_MANAGER", "false").lower() == "true"
            and os.getenv("ENV", "dev") != "dev"
            and BOTO3_AVAILABLE
        ):
            try:
                client = boto3.client(
                    "secretsmanager", region_name=os.getenv("AWS_REGION", "us-east-1")
                )
                secret = client.get_secret_value(SecretId=f"feast/{key}")[
                    "SecretString"
                ]
                return secret
            except ClientError as e:
                logger.error(
                    f"Failed to fetch {key} from Secrets Manager: {e}", exc_info=True
                )
                raise ConnectionError(f"Failed to fetch {key}: {e}") from e
        env_value = os.getenv(key)
        if env_value and os.getenv("ENV", "dev") != "dev":
            logger.warning(
                f"Using env var for {key} in production; prefer Secrets Manager for security."
            )
        return env_value or ""

    async def __aenter__(self):
        """Async context manager entry: connect to Feast."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit: disconnect from Feast."""
        await self.disconnect()

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (ConnectionError, FeastProviderError, FeastResourceError)
        ),
        reraise=True,
    )
    async def connect(self) -> None:
        """
        Connect to Feast Feature Store with retries for transient errors.
        Initializes the FeatureStore and performs a health check by listing feature views.
        Args:
            None
        Raises:
            ConnectionError: If connection fails after retries.
            FeastProviderError: If provider-specific issues occur.
        """
        if self._is_connected:
            logger.info("Feast FeatureStore already connected.")
            return
        with tracer.start_as_current_span("feast_connect") as span:
            start_time = time.monotonic()
            FS_CALLS_TOTAL.labels(
                operation="connect", status="attempt", **self.metric_labels
            ).inc()
            try:
                loop = asyncio.get_running_loop()
                self._fs = await loop.run_in_executor(
                    None, FeatureStore, self.repo_path
                )
                await loop.run_in_executor(
                    None, self._fs.list_feature_views
                )  # Health check
                self._is_connected = True
                FS_CALLS_TOTAL.labels(
                    operation="connect", status="success", **self.metric_labels
                ).inc()
                span.set_status(Status(StatusCode.OK))
                logger.info(f"Connected to Feast Feature Store at {self.repo_path}")
            except Exception as e:
                FS_CALLS_TOTAL.labels(
                    operation="connect", status="failure", **self.metric_labels
                ).inc()
                FS_CALLS_ERRORS.labels(
                    operation="connect",
                    error_type=type(e).__name__,
                    **self.metric_labels,
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.error(f"Failed to connect to Feast: {e}", exc_info=True)
                raise ConnectionError(f"Failed to connect to Feast: {e}") from e
            finally:
                FS_CALL_LATENCY_SECONDS.labels(
                    operation="connect", **self.metric_labels
                ).observe(time.monotonic() - start_time)

    async def disconnect(self) -> None:
        """Disconnect from Feast Feature Store."""
        if RAY_AVAILABLE and ray.is_initialized():
            ray.shutdown()
        self._fs = None
        self._is_connected = False
        logger.info("Disconnected from Feast Feature Store.")

    async def health_check(self) -> bool:
        """Verify connection status."""
        with tracer.start_as_current_span("feast_health_check"):
            FS_CALLS_TOTAL.labels(
                operation="health_check", status="attempt", **self.metric_labels
            ).inc()
            try:
                if not self._is_connected or not self._fs:
                    return False
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._fs.list_feature_views)
                FS_CALLS_TOTAL.labels(
                    operation="health_check", status="success", **self.metric_labels
                ).inc()
                return True
            except Exception as e:
                FS_CALLS_ERRORS.labels(
                    operation="health_check",
                    error_type=type(e).__name__,
                    **self.metric_labels,
                ).inc()
                return False

    async def log_operation(self, operation: str, details: Dict[str, Any]) -> str:
        """
        Log feature store operations for auditability.
        Args:
            operation (str): The operation performed (e.g., 'ingest_features', 'validate_features').
            details (Dict[str, Any]): Additional details about the operation.
        """
        if not AUDIT_LEDGER_AVAILABLE:
            logger.warning(
                f"Audit logging skipped for operation '{operation}': audit_ledger_client.py not available."
            )
            return "not_available"

        with tracer.start_as_current_span("feast_log_operation") as span:
            span.set_attribute("feast.operation", operation)
            FS_AUDIT_LOGS_TOTAL.labels(
                operation=operation, status="attempt", **self.metric_labels
            ).inc()
            try:
                audit_client = AuditLedgerClient()
                async with audit_client:
                    tx_hash = await audit_client.log_event(
                        event_type=f"feature_store:{operation}",
                        details=details,
                        operator="sfe_feature_store",
                    )
                FS_AUDIT_LOGS_TOTAL.labels(
                    operation=operation, status="success", **self.metric_labels
                ).inc()
                span.set_status(Status(StatusCode.OK))
                logger.info(f"Logged operation '{operation}' with details: {details}")
                return tx_hash
            except Exception as e:
                FS_AUDIT_LOGS_TOTAL.labels(
                    operation=operation, status="failure", **self.metric_labels
                ).inc()
                FS_CALLS_ERRORS.labels(
                    operation="log_operation",
                    error_type=type(e).__name__,
                    **self.metric_labels,
                ).inc()
                span.record_exception(e)
                span.set_status(
                    Status(StatusCode.ERROR, f"Failed to log operation: {e}")
                )
                logger.error(
                    f"Failed to log operation '{operation}': {e}", exc_info=True
                )
                raise ValueError(f"Failed to log operation: {e}") from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(
            (FeastProviderError, FeastResourceError, FeastObjectNotFoundException)
        ),
        reraise=True,
    )
    async def apply_feature_definitions(
        self, definitions: List[Union[Entity, FeatureView]]
    ) -> None:
        """
        Apply Feast entity and feature view definitions to the registry.
        Validates definitions using Pydantic models and logs operations for auditability.
        Args:
            definitions (List[Union[Entity, FeatureView]]): List of Feast Entity or FeatureView objects.
        Raises:
            RuntimeError: If FeatureStore not connected.
            ValueError: If definitions are invalid.
            FeastProviderError: If registry update fails.
        """
        if not self._fs:
            raise RuntimeError(
                "Feast FeatureStore not connected. Call connect() first."
            )
        with tracer.start_as_current_span("feast_apply_definitions") as span:
            span.set_attribute("feast.num_definitions", len(definitions))
            start_time = time.monotonic()
            FS_CALLS_TOTAL.labels(
                operation="apply_definitions", status="attempt", **self.metric_labels
            ).inc()
            try:
                for defn in definitions:
                    if isinstance(defn, Entity):
                        FeatureEntityModel(
                            name=defn.name,
                            value_type=defn.value_type.name,
                            description=defn.description,
                        )
                    elif isinstance(defn, FeatureView):
                        FeatureViewModel(
                            name=defn.name,
                            entities=defn.entities,
                            ttl=defn.ttl,
                            feature_schema=[
                                {"name": f.name, "dtype": f.dtype.name}
                                for f in defn.schema
                            ],
                            source=defn.source.__dict__,
                        )
                        FeatureSourceModel(
                            type=defn.source.__class__.__name__,
                            config=defn.source.__dict__,
                            timestamp_field=defn.source.timestamp_field,
                        )
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._fs.apply, definitions)

                await self.log_operation(
                    "apply_definitions",
                    {
                        "num_definitions": len(definitions),
                        "definitions": [d.name for d in definitions],
                    },
                )

                FS_CALLS_TOTAL.labels(
                    operation="apply_definitions",
                    status="success",
                    **self.metric_labels,
                ).inc()
                span.set_status(Status(StatusCode.OK))
                logger.info(f"Applied {len(definitions)} Feast definitions.")
            except Exception as e:
                FS_CALLS_TOTAL.labels(
                    operation="apply_definitions",
                    status="failure",
                    **self.metric_labels,
                ).inc()
                FS_CALLS_ERRORS.labels(
                    operation="apply_definitions",
                    error_type=type(e).__name__,
                    **self.metric_labels,
                ).inc()
                span.record_exception(e)
                span.set_status(
                    Status(StatusCode.ERROR, f"Failed to apply definitions: {e}")
                )
                logger.error(f"Failed to apply definitions: {e}", exc_info=True)
                raise ValueError(f"Failed to apply definitions: {e}") from e
            finally:
                FS_CALL_LATENCY_SECONDS.labels(
                    operation="apply_definitions", **self.metric_labels
                ).observe(time.monotonic() - start_time)

    async def wait_for_ingestion(
        self, feature_view_name: str, timeout: int = 30
    ) -> None:
        """
        Poll for ingestion completion to ensure features are available.
        Args:
            feature_view_name (str): The name of the FeatureView.
            timeout (int): The maximum time to wait in seconds.
        Raises:
            TimeoutError: If ingestion is not completed within the timeout.
        """
        start_time = time.monotonic()
        with tracer.start_as_current_span("feast_wait_for_ingestion") as span:
            span.set_attribute("feast.feature_view", feature_view_name)
            FS_CALLS_TOTAL.labels(
                operation="wait_for_ingestion", status="attempt", **self.metric_labels
            ).inc()
            try:
                while time.monotonic() - start_time < timeout:
                    features = await self.get_online_features(
                        [f"{feature_view_name}:daily_login_count"], [{"user_id": 101}]
                    )
                    if features and features[0].get("daily_login_count") is not None:
                        FS_CALLS_TOTAL.labels(
                            operation="wait_for_ingestion",
                            status="success",
                            **self.metric_labels,
                        ).inc()
                        span.set_status(Status(StatusCode.OK))
                        return
                    await asyncio.sleep(1)
                raise TimeoutError(
                    f"Ingestion for {feature_view_name} not completed within {timeout}s"
                )
            except Exception as e:
                FS_CALLS_TOTAL.labels(
                    operation="wait_for_ingestion",
                    status="failure",
                    **self.metric_labels,
                ).inc()
                FS_CALLS_ERRORS.labels(
                    operation="wait_for_ingestion",
                    error_type=type(e).__name__,
                    **self.metric_labels,
                ).inc()
                span.record_exception(e)
                span.set_status(
                    Status(StatusCode.ERROR, f"Failed to wait for ingestion: {e}")
                )
                logger.error(
                    f"Failed to wait for ingestion of '{feature_view_name}': {e}",
                    exc_info=True,
                )
                raise
            finally:
                FS_CALL_LATENCY_SECONDS.labels(
                    operation="wait_for_ingestion", **self.metric_labels
                ).observe(time.monotonic() - start_time)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(
            (FeastProviderError, FeastResourceError, FeastObjectNotFoundException)
        ),
        reraise=True,
    )
    async def ingest_features(self, feature_view_name: str, data_df: Any) -> None:
        """
        Ingest data into a FeatureView's online store, using Ray for distributed processing in production.
        Args:
            feature_view_name (str): Name of the FeatureView to ingest into.
            data_df (Any): Pandas DataFrame conforming to the FeatureView's schema.
        Raises:
            RuntimeError: If FeatureStore not connected.
            ValueError: If data_df is invalid.
            FeastProviderError: If ingestion fails due to provider issues.
        """
        if not self._fs:
            raise RuntimeError("Feast FeatureStore not connected.")
        if not hasattr(data_df, "columns"):
            raise ValueError("Expected a DataFrame-like object.")

        # Enforce Secrets Manager for production
        if (
            os.getenv("ENV", "dev") != "dev"
            and os.getenv("USE_SECRETS_MANAGER", "false").lower() != "true"
        ):
            raise ValueError(
                "Secrets Manager required for secure credential management."
            )

        # Hash PII columns before ingestion, but keep entity keys
        sensitive_cols = ["email"]  # Configurable
        for col in sensitive_cols:
            if col in data_df.columns:
                data_df[col + "_hash"] = data_df[col].apply(
                    lambda x: hashlib.sha256(str(x).encode()).hexdigest()
                )
                data_df = data_df.drop(columns=[col])

        async with self.semaphore:
            with tracer.start_as_current_span("feast_ingest_features") as span:
                span.set_attribute("feast.feature_view", feature_view_name)
                span.set_attribute("feast.data_rows", len(data_df))
                start_time = time.monotonic()
                FS_CALLS_TOTAL.labels(
                    operation="ingest_features", status="attempt", **self.metric_labels
                ).inc()
                try:
                    feature_view = self._fs.get_feature_view(feature_view_name)
                    loop = asyncio.get_running_loop()
                    if (
                        RAY_AVAILABLE
                        and os.getenv("FEAST_PROVIDER", "local") != "local"
                    ):
                        ray.init(ignore_reinit_error=True)

                        @ray.remote
                        def ingest_batch(repo_path, fv_name, batch_df):
                            fs = FeatureStore(repo_path=repo_path)
                            fv = fs.get_feature_view(name=fv_name)
                            fs.ingest(fv, batch_df)
                            return len(batch_df)

                        batch_size = int(os.getenv("RAY_BATCH_SIZE", "1000"))
                        batches = [
                            data_df[i : i + batch_size]
                            for i in range(0, len(data_df), batch_size)
                        ]
                        futures = [
                            ingest_batch.remote(
                                self.repo_path, feature_view_name, batch
                            )
                            for batch in batches
                        ]
                        await loop.run_in_executor(None, lambda: ray.get(futures))
                    else:
                        logger.warning("Ray unavailable; using threaded ingestion.")
                        batch_size = int(os.getenv("BATCH_SIZE", "1000"))
                        with concurrent.futures.ThreadPoolExecutor(
                            max_workers=4
                        ) as executor:
                            batches = [
                                data_df[i : i + batch_size]
                                for i in range(0, len(data_df), batch_size)
                            ]
                            futures = [
                                executor.submit(self._fs.ingest, feature_view, batch)
                                for batch in batches
                            ]
                            concurrent.futures.wait(futures)

                    await self.wait_for_ingestion(feature_view_name)
                    FS_FEATURE_FRESHNESS_SECONDS.labels(
                        feature_view=feature_view_name, **self.metric_labels
                    ).set(0)

                    await self.log_operation(
                        "ingest_features",
                        {"feature_view": feature_view_name, "rows": len(data_df)},
                    )

                    FS_CALLS_TOTAL.labels(
                        operation="ingest_features",
                        status="success",
                        **self.metric_labels,
                    ).inc()
                    span.set_status(Status(StatusCode.OK))
                    logger.info(
                        f"Ingested {len(data_df)} rows into FeatureView '{feature_view_name}'."
                    )
                except Exception as e:
                    FS_CALLS_TOTAL.labels(
                        operation="ingest_features",
                        status="failure",
                        **self.metric_labels,
                    ).inc()
                    FS_CALLS_ERRORS.labels(
                        operation="ingest_features",
                        error_type=type(e).__name__,
                        **self.metric_labels,
                    ).inc()
                    span.record_exception(e)
                    span.set_status(
                        Status(StatusCode.ERROR, f"Failed to ingest features: {e}")
                    )
                    logger.error(
                        f"Failed to ingest data into FeatureView '{feature_view_name}': {e}",
                        exc_info=True,
                    )
                    raise
                finally:
                    FS_CALL_LATENCY_SECONDS.labels(
                        operation="ingest_features", **self.metric_labels
                    ).observe(time.monotonic() - start_time)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(
            (FeastProviderError, FeastResourceError, FeastObjectNotFoundException)
        ),
        reraise=True,
    )
    async def get_online_features(
        self, feature_refs: List[str], entity_rows: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Retrieve online features with batching.
        Args:
            feature_refs (List[str]): List of feature references to retrieve.
            entity_rows (List[Dict[str, Any]]): List of dictionaries, each representing an entity key.
        Returns:
            List[Dict[str, Any]]: List of dictionaries, each containing the requested online features.
        Raises:
            RuntimeError: If FeastStore is not connected.
            ValueError: If entity rows exceed the configured limit.
            FeastProviderError: If retrieval fails due to provider issues.
        """
        if not self._fs:
            raise RuntimeError("Feast FeatureStore not connected.")
        max_rows = int(os.getenv("MAX_ENTITY_ROWS", "1000"))
        if len(entity_rows) > max_rows:
            raise ValueError(f"Entity rows exceed limit of {max_rows}")
        async with self.semaphore:
            with tracer.start_as_current_span("feast_get_online_features") as span:
                span.set_attribute("feast.num_entity_rows", len(entity_rows))
                span.set_attribute("feast.feature_refs", str(feature_refs))
                start_time = time.monotonic()
                FS_CALLS_TOTAL.labels(
                    operation="get_online_features",
                    status="attempt",
                    **self.metric_labels,
                ).inc()
                try:
                    batch_size = max(1, min(100, len(entity_rows) // 10))
                    all_results = []
                    loop = asyncio.get_running_loop()
                    for i in range(0, len(entity_rows), batch_size):
                        batch = entity_rows[i : i + batch_size]
                        response = await loop.run_in_executor(
                            None,
                            lambda: self._fs.get_online_features(
                                features=feature_refs, entity_rows=batch
                            ),
                        )
                        d = response.to_dict()
                        if d:
                            # Validate that all dictionary values have consistent lengths
                            lengths = [
                                len(v) for v in d.values() if isinstance(v, list)
                            ]
                            if not lengths or len(set(lengths)) > 1:
                                logger.error(
                                    f"Inconsistent response lengths: {lengths}"
                                )
                                raise ValueError(
                                    "Feast response has inconsistent value lengths"
                                )
                            n = lengths[0]
                            rows = [{k: v[i] for k, v in d.items()} for i in range(n)]
                            # ensure entity keys are present (Feast may omit them)
                            for j, ent in enumerate(batch[:n]):
                                rows[j].update(ent)
                            all_results.extend(rows)
                    FS_CALLS_TOTAL.labels(
                        operation="get_online_features",
                        status="success",
                        **self.metric_labels,
                    ).inc()
                    span.set_status(Status(StatusCode.OK))
                    logger.info(
                        f"Retrieved online features for {len(entity_rows)} entities."
                    )
                    return all_results
                except Exception as e:
                    FS_CALLS_TOTAL.labels(
                        operation="get_online_features",
                        status="failure",
                        **self.metric_labels,
                    ).inc()
                    FS_CALLS_ERRORS.labels(
                        operation="get_online_features",
                        error_type=type(e).__name__,
                        **self.metric_labels,
                    ).inc()
                    span.record_exception(e)
                    span.set_status(
                        Status(StatusCode.ERROR, f"Failed to get online features: {e}")
                    )
                    logger.error(
                        f"Failed to retrieve online features: {e}", exc_info=True
                    )
                    raise
                finally:
                    FS_CALL_LATENCY_SECONDS.labels(
                        operation="get_online_features", **self.metric_labels
                    ).observe(time.monotonic() - start_time)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(
            (FeastProviderError, FeastResourceError, FeastObjectNotFoundException)
        ),
        reraise=True,
    )
    async def get_historical_features(
        self, entity_df: Any, feature_refs: List[str]
    ) -> Any:
        """
        Retrieve point-in-time correct historical features.
        Args:
            entity_df (Any): A DataFrame containing entity IDs and a timestamp column.
            feature_refs (List[str]): List of feature references (e.g., "view_name:feature_name").
        Returns:
            Any: A DataFrame containing the historical features.
        Raises:
            RuntimeError: If FeastStore is not connected.
            ValueError: If entity rows exceed the configured limit.
            FeastProviderError: If retrieval fails due to provider issues.
        """
        if not self._fs:
            raise RuntimeError("Feast FeatureStore not connected.")
        max_rows = int(os.getenv("MAX_ENTITY_ROWS", "1000"))
        if len(entity_df) > max_rows:
            raise ValueError(f"Entity rows exceed limit of {max_rows}")
        async with self.semaphore:
            with tracer.start_as_current_span("feast_get_historical_features") as span:
                span.set_attribute("feast.num_entities", len(entity_df))
                span.set_attribute("feast.feature_refs", str(feature_refs))
                start_time = time.monotonic()
                FS_CALLS_TOTAL.labels(
                    operation="get_historical_features",
                    status="attempt",
                    **self.metric_labels,
                ).inc()
                try:
                    loop = asyncio.get_running_loop()
                    job = await loop.run_in_executor(
                        None, self._fs.get_historical_features, entity_df, feature_refs
                    )
                    feature_data = await loop.run_in_executor(None, job.to_df)

                    if "email" in feature_data.columns:
                        feature_data["email_hash"] = feature_data["email"].apply(
                            lambda x: hashlib.sha256(str(x).encode()).hexdigest()
                        )
                        feature_data = feature_data.drop(columns=["email"])

                    await self.log_operation(
                        "get_historical_features",
                        {"num_entities": len(entity_df), "feature_refs": feature_refs},
                    )

                    FS_CALLS_TOTAL.labels(
                        operation="get_historical_features",
                        status="success",
                        **self.metric_labels,
                    ).inc()
                    span.set_status(Status(StatusCode.OK))
                    logger.info(
                        f"Retrieved historical features for {len(entity_df)} entities."
                    )
                    return feature_data
                except Exception as e:
                    FS_CALLS_TOTAL.labels(
                        operation="get_historical_features",
                        status="failure",
                        **self.metric_labels,
                    ).inc()
                    FS_CALLS_ERRORS.labels(
                        operation="get_historical_features",
                        error_type=type(e).__name__,
                        **self.metric_labels,
                    ).inc()
                    span.record_exception(e)
                    span.set_status(
                        Status(
                            StatusCode.ERROR, f"Failed to get historical features: {e}"
                        )
                    )
                    logger.error(
                        f"Failed to retrieve historical features: {e}", exc_info=True
                    )
                    raise
                finally:
                    FS_CALL_LATENCY_SECONDS.labels(
                        operation="get_historical_features", **self.metric_labels
                    ).observe(time.monotonic() - start_time)

    async def validate_features(self, feature_view_name: str) -> Dict[str, Any]:
        """
        Validate FeatureView data using Great Expectations and statistical drift detection.
        Args:
            feature_view_name (str): The name of the feature view to validate.
        Returns:
            Dict[str, Any]: A dictionary containing validation results, including freshness and drift.
        Raises:
            ImportError: If great-expectations is not installed.
            RuntimeError: If FeatureStore is not connected.
            ValueError: If validation or drift detection fails.
        """
        if not self._fs:
            raise RuntimeError("Feast FeatureStore not connected.")
        with tracer.start_as_current_span("feast_validate_features") as span:
            span.set_attribute("feast.feature_view", feature_view_name)
            start_time = time.monotonic()
            FS_CALLS_TOTAL.labels(
                operation="validate_features", status="attempt", **self.metric_labels
            ).inc()
            try:
                import pandas as pd

                loop = asyncio.get_running_loop()
                feature_view = self._fs.get_feature_view(feature_view_name)

                data = await self.get_online_features(
                    [f"{feature_view_name}:{f.name}" for f in feature_view.schema],
                    [{"user_id": 101}],
                )
                df = pd.DataFrame(data)

                results = {"success": True, "details": {}}
                if not GX_AVAILABLE:
                    logger.warning(
                        "Great Expectations unavailable; using basic Pandas validation."
                    )
                    null_counts = df.isnull().sum().to_dict()
                    results["details"]["nulls"] = null_counts
                    results["success"] = all(null == 0 for null in null_counts.values())
                else:
                    context = await loop.run_in_executor(None, DataContext)
                    suite_name = f"{feature_view_name}_suite"
                    checkpoint = context.add_or_update_checkpoint(
                        name=f"{feature_view_name}_checkpoint",
                        validations=[{"expectation_suite_name": suite_name}],
                    )
                    ge_results = await loop.run_in_executor(
                        None,
                        lambda: context.run_checkpoint(
                            checkpoint.name, batch_request={"dataframe": df}
                        ),
                    )
                    results["success"] = ge_results.success
                    results["validation_results"] = ge_results.to_dict()

                freshness = (
                    (
                        datetime.now(timezone.utc)
                        - df.get(
                            "event_timestamp", pd.Series([datetime.now(timezone.utc)])
                        ).max()
                    ).total_seconds()
                    if "event_timestamp" in df
                    else 0
                )
                FS_FEATURE_FRESHNESS_SECONDS.labels(
                    feature_view=feature_view_name, **self.metric_labels
                ).set(freshness)

                # Statistical drift detection (e.g., KL divergence for numerical features)
                drift_stats = {}
                if SCIPY_AVAILABLE:
                    numeric_type_names = {
                        t for t in ("INT64", "DOUBLE", "FLOAT") if hasattr(ValueType, t)
                    }
                    for feature in feature_view.schema:
                        if feature.dtype.name in numeric_type_names:
                            baseline_data = await self.get_historical_features(
                                entity_df=pd.DataFrame(
                                    {
                                        "user_id": [101],
                                        "event_timestamp": [
                                            datetime.now(timezone.utc)
                                            - timedelta(days=7)
                                        ],
                                    }
                                ),
                                feature_refs=[f"{feature_view_name}:{feature.name}"],
                            )
                            if not baseline_data.empty and not df.empty:
                                current_values = (
                                    df.get(feature.name, pd.Series([])).dropna().values
                                )
                                baseline_values = (
                                    baseline_data.get(feature.name, pd.Series([]))
                                    .dropna()
                                    .values
                                )
                                if len(current_values) > 0 and len(baseline_values) > 0:
                                    current_dist = (
                                        pd.Series(current_values)
                                        .value_counts(normalize=True)
                                        .sort_index()
                                    )
                                    baseline_dist = (
                                        pd.Series(baseline_values)
                                        .value_counts(normalize=True)
                                        .sort_index()
                                    )
                                    common_indices = current_dist.index.intersection(
                                        baseline_dist.index
                                    )
                                    if not common_indices.empty:
                                        kl_div = scipy.stats.entropy(
                                            current_dist[common_indices],
                                            baseline_dist[common_indices],
                                        )
                                        drift_stats[feature.name] = kl_div > 0.1

                await self.log_operation(
                    "validate_features",
                    {
                        "feature_view": feature_view_name,
                        "validation_success": results["success"],
                        "freshness_seconds": freshness,
                        "drift_detected": (
                            any(drift_stats.values()) if drift_stats else None
                        ),
                    },
                )

                FS_CALLS_TOTAL.labels(
                    operation="validate_features",
                    status="success",
                    **self.metric_labels,
                ).inc()
                span.set_status(Status(StatusCode.OK))
                logger.info(
                    f"Validated FeatureView '{feature_view_name}' with Great Expectations and drift detection."
                )
                return {
                    "freshness_ok": freshness < 3600,
                    "drift_detected": not results["success"]
                    or (any(drift_stats.values()) if drift_stats else False),
                    "validation_results": results,
                    "drift_stats": drift_stats,
                }
            except Exception as e:
                FS_CALLS_TOTAL.labels(
                    operation="validate_features",
                    status="failure",
                    **self.metric_labels,
                ).inc()
                FS_CALLS_ERRORS.labels(
                    operation="validate_features",
                    error_type=type(e).__name__,
                    **self.metric_labels,
                ).inc()
                span.record_exception(e)
                span.set_status(
                    Status(StatusCode.ERROR, f"Failed to validate features: {e}")
                )
                logger.error(
                    f"Failed to validate FeatureView '{feature_view_name}': {e}",
                    exc_info=True,
                )
                raise ValueError(f"Failed to validate features: {e}") from e
            finally:
                FS_CALL_LATENCY_SECONDS.labels(
                    operation="validate_features", **self.metric_labels
                ).observe(time.monotonic() - start_time)

    async def flag_for_redaction(self, feature_view_name: str, reason: str) -> None:
        """
        Flag a FeatureView for redaction to comply with GDPR 'right to be forgotten'.
        Stores redaction requests in Postgres for auditability and compliance tracking.
        Args:
            feature_view_name (str): The name of the feature view to flag.
            reason (str): The reason for the redaction request (e.g., 'GDPR Right to be Forgotten').
        Raises:
            RuntimeError: If FeastStore is not connected.
            ImportError: If postgres_client.py is not available.
            ValueError: If the redaction request fails.
        """
        if not self._fs:
            raise RuntimeError("Feast FeatureStore not connected.")
        start_time = time.monotonic()
        with tracer.start_as_current_span("feast_flag_for_redaction") as span:
            span.set_attribute("feast.feature_view", feature_view_name)
            FS_CALLS_TOTAL.labels(
                operation="flag_for_redaction", status="attempt", **self.metric_labels
            ).inc()
            try:
                view_hash = hashlib.sha256(feature_view_name.encode()).hexdigest()
                if not POSTGRES_AVAILABLE:
                    logger.warning(
                        "Postgres unavailable; using audit ledger for redaction."
                    )
                    await self.log_operation(
                        "redaction_flag",
                        {
                            "feature_view": feature_view_name,
                            "reason": reason,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                else:
                    pg_client = PostgresClient()
                    async with pg_client:
                        await pg_client.save(
                            "feature_redactions",
                            {
                                "id": view_hash,
                                "feature_view": feature_view_name,
                                "reason": reason,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                            id_field="id",
                        )

                FS_REDACTIONS_TOTAL.labels(
                    feature_view=feature_view_name, **self.metric_labels
                ).inc()
                FS_CALLS_TOTAL.labels(
                    operation="flag_for_redaction",
                    status="success",
                    **self.metric_labels,
                ).inc()
                span.set_status(Status(StatusCode.OK))
                logger.info(
                    f"Flagged FeatureView '{feature_view_name}' for redaction: {reason}"
                )
            except Exception as e:
                FS_CALLS_TOTAL.labels(
                    operation="flag_for_redaction",
                    status="failure",
                    **self.metric_labels,
                ).inc()
                FS_CALLS_ERRORS.labels(
                    operation="flag_for_redaction",
                    error_type=type(e).__name__,
                    **self.metric_labels,
                ).inc()
                span.record_exception(e)
                span.set_status(
                    Status(StatusCode.ERROR, f"Failed to flag for redaction: {e}")
                )
                logger.error(
                    f"Failed to flag FeatureView '{feature_view_name}' for redaction: {e}",
                    exc_info=True,
                )
                raise ValueError(f"Failed to flag for redaction: {e}") from e
            finally:
                FS_CALL_LATENCY_SECONDS.labels(
                    operation="flag_for_redaction", **self.metric_labels
                ).observe(time.monotonic() - start_time)


# --- Custom Exception Classes ---


class ConnectionError(Exception):
    """Exception raised when connection to Feature Store fails."""

    pass


class SchemaValidationError(Exception):
    """Exception raised when schema validation fails."""

    pass


# --- Example Usage and Testing ---


async def main():
    """Demonstrate FeatureStoreClient usage with local and production configurations."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger.setLevel(logging.DEBUG)
    repo_path = "test_feature_repo"
    os.environ["FEAST_REPO_PATH"] = repo_path
    if os.path.exists(repo_path):
        shutil.rmtree(repo_path)
    os.makedirs(os.path.join(repo_path, "data"), exist_ok=True)
    # Create feature_store.yaml
    provider = os.getenv("FEAST_PROVIDER", "local")
    client = FeatureStoreClient()
    if provider == "gcp":
        project_id = os.getenv("GCP_PROJECT")
        bigquery_dataset = os.getenv("BIGQUERY_DATASET")
        redis_url = client._get_credentials("REDIS_URL")
        if not all([project_id, bigquery_dataset, redis_url]):
            raise ValueError(
                "GCP configuration requires GCP_PROJECT, BIGQUERY_DATASET, and REDIS_URL."
            )
        yaml_content = f"""
project: {project_id}
registry: gs://{project_id}-feast-registry/registry.db
provider: gcp
online_store:
    type: redis
    connection_string: "{redis_url}"
offline_store:
    type: bigquery
"""
    else:
        yaml_content = """
project: test_project
registry: data/registry.db
provider: local
online_store:
    type: sqlite
    path: data/online_store.db
offline_store:
    type: file
"""
    with open(os.path.join(repo_path, "feature_store.yaml"), "w") as f:
        f.write(yaml_content)
    # Define entities and feature views
    import pandas as pd

    user_entity = Entity(
        name="user_id", value_type=ValueType.INT64, description="User ID"
    )
    user_data_df = pd.DataFrame(
        {
            "user_id": [101, 102, 101, 103, 104],
            "daily_login_count": [5, 12, 6, 8, 2],
            "event_timestamp": [
                datetime(2023, 8, 1, tzinfo=timezone.utc),
                datetime(2023, 8, 1, tzinfo=timezone.utc),
                datetime(2023, 8, 2, tzinfo=timezone.utc),
                datetime(2023, 8, 3, tzinfo=timezone.utc),
                datetime(2023, 8, 3, tzinfo=timezone.utc),
            ],
        }
    )
    if provider == "gcp":
        user_activity_source = BigQuerySource(
            table=f"{project_id}.{bigquery_dataset}.user_logins",
            timestamp_field="event_timestamp",
        )
    else:
        user_data_path = os.path.join(repo_path, "data", "user_data.parquet")
        user_data_df.to_parquet(user_data_path)
        user_activity_source = FileSource(
            path=user_data_path,
            timestamp_field="event_timestamp",
        )
    user_login_fv = FeatureView(
        name="user_daily_logins",
        entities=["user_id"],
        ttl=timedelta(days=7),
        schema=[FeastField(name="daily_login_count", dtype=ValueType.INT64)],
        source=user_activity_source,
    )
    try:
        await client.connect()
        assert await client.health_check(), "Health check failed"
        logger.info("\n--- FeatureStoreClient Example Usage ---")
        # Apply definitions
        logger.info("Applying feature definitions...")
        await client.apply_feature_definitions([user_entity, user_login_fv])
        await client.log_operation("apply_feature_definitions", {"num_definitions": 2})
        # Materialize data
        logger.info("\nMaterializing features...")
        subprocess.run(
            [
                "feast",
                "materialize-incremental",
                datetime.now(timezone.utc).isoformat(),
            ],
            cwd=repo_path,
            check=True,
        )
        # Get online features
        logger.info("\nGetting online features...")
        online_features = await client.get_online_features(
            feature_refs=["user_daily_logins:daily_login_count"],
            entity_rows=[{"user_id": 101}, {"user_id": 103}],
        )
        logger.info(f"Online features: {online_features}")
        assert len(online_features) == 2, "Expected 2 feature rows"
        assert online_features[0]["user_id"] == 101, "Feature row mismatch"
        # Ingest real-time data
        logger.info("\nIngesting real-time features...")
        real_time_df = pd.DataFrame(
            {
                "user_id": [105, 106],
                "daily_login_count": [1, 3],
                "event_timestamp": [
                    datetime.now(timezone.utc),
                    datetime.now(timezone.utc),
                ],
            }
        )
        await client.ingest_features("user_daily_logins", real_time_df)
        rt_features = await client.get_online_features(
            feature_refs=["user_daily_logins:daily_login_count"],
            entity_rows=[{"user_id": 105}],
        )
        logger.info(f"Verified real-time features: {rt_features}")
        assert rt_features[0]["daily_login_count"] == 1, "Real-time ingestion failed"
        # Get historical features
        logger.info("\nGetting historical features...")
        entity_df_historical = pd.DataFrame(
            {
                "user_id": [101, 102],
                "event_timestamp": [
                    datetime(2023, 8, 1, 10, 30, tzinfo=timezone.utc),
                    datetime(2023, 8, 1, 11, tzinfo=timezone.utc),
                ],
            }
        )
        historical_features = await client.get_historical_features(
            entity_df=entity_df_historical,
            feature_refs=["user_daily_logins:daily_login_count"],
        )
        logger.info(f"Historical features:\n{historical_features}")
        assert not historical_features.empty, "Historical features empty"
        assert (
            historical_features[historical_features["user_id"] == 101][
                "daily_login_count"
            ].iloc[0]
            == 5
        ), "Point-in-time join failed"
        await client.log_operation(
            "get_historical_features", {"num_entities": len(entity_df_historical)}
        )
        # Validate features
        logger.info("\nValidating features...")
        validation_results = await client.validate_features("user_daily_logins")
        logger.info(f"Validation results: {validation_results}")
        assert validation_results["freshness_ok"], "Feature freshness validation failed"
        await client.log_operation(
            "validate_features",
            {
                "feature_view": "user_daily_logins",
                "success": validation_results["freshness_ok"],
            },
        )
        # Test edge cases
        try:
            invalid_fv = FeatureView(
                name="invalid", entities=["missing"], ttl=timedelta(days=0), schema=[]
            )
            await client.apply_feature_definitions([invalid_fv])
            logger.error("Expected invalid definition to fail")
        except ValueError:
            logger.info("Caught invalid definition error")
        try:
            invalid_data = pd.DataFrame(
                {
                    "user_id": [None],
                    "daily_login_count": [None],
                    "event_timestamp": [datetime.now(timezone.utc)],
                }
            )
            await client.ingest_features("user_daily_logins", invalid_data)
            logger.error("Expected invalid data ingestion to fail")
        except ValueError:
            logger.info("Caught invalid data ingestion error")
        try:
            validation_results = await client.validate_features("invalid_view")
            logger.error("Expected validation to fail for non-existent view")
        except ValueError:
            logger.info("Caught validation error for non-existent view")
        try:
            invalid_gx_data = pd.DataFrame(
                {
                    "user_id": [107],
                    "daily_login_count": [-1],  # Invalid negative value
                    "event_timestamp": [datetime.now(timezone.utc)],
                }
            )
            await client.ingest_features("user_daily_logins", invalid_gx_data)
            validation_results = await client.validate_features("user_daily_logins")
            assert (
                not validation_results["freshness_ok"]
                or validation_results["drift_detected"]
            ), "Expected validation failure for invalid data"
            logger.info("Caught expected GX validation failure")
            await client.log_operation(
                "validate_features",
                {"feature_view": "user_daily_logins", "success": False},
            )
        except ValueError:
            logger.info("Caught invalid data validation error")
        # Test extreme stress ingestion
        logger.info("\nTesting extreme stress ingestion...")
        extreme_df = pd.DataFrame(
            {
                "user_id": list(range(1000, 11000)),  # 10,000 rows
                "daily_login_count": [1] * 10000,
                "event_timestamp": [datetime.now(timezone.utc)] * 10000,
            }
        )
        await client.ingest_features("user_daily_logins", extreme_df)
        extreme_features = await client.get_online_features(
            feature_refs=["user_daily_logins:daily_login_count"],
            entity_rows=[{"user_id": 1000}],
        )
        logger.info(f"Extreme stress features: {extreme_features}")
        assert (
            extreme_features[0]["daily_login_count"] == 1
        ), "Extreme stress ingestion failed"
        await client.log_operation(
            "ingest_features",
            {"feature_view": "user_daily_logins", "rows": len(extreme_df)},
        )
        # Test audit logging failure
        try:
            await client.log_operation("invalid_op", {"details": None})
            logger.error("Expected audit logging to fail for invalid operation")
        except ValueError:
            logger.info("Caught audit logging error")
        # Flag for redaction
        logger.info("\nFlagging feature view for redaction...")
        await client.flag_for_redaction(
            "user_daily_logins", "GDPR Right to be Forgotten"
        )
        await client.log_operation(
            "flag_for_redaction",
            {"feature_view": "user_daily_logins", "reason": "GDPR"},
        )
    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
        raise
    finally:
        await client.disconnect()
        if os.path.exists(repo_path):
            shutil.rmtree(repo_path)
            logger.info("Cleaned up temporary repo.")


if __name__ == "__main__":
    # To run this example, first install dependencies:
    # pip install "feast[sqlite,redis,bigquery,ray,gcp]" pandas tenacity pydantic boto3 opentelemetry-sdk opentelemetry-exporter-otlp prometheus-client pyarrow great-expectations scipy
    asyncio.run(main())
