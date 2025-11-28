"""
meta_learning_data_store.py

A production-ready, extensible data store for tracking meta-learning experiments and metadata.

Features:
- Pydantic schemas for meta-learning records.
- Async CRUD interface.
- Extensible backend (in-memory, Redis).
- Prometheus metrics and OpenTelemetry tracing.
- Full logging and robust error handling.
- Concurrency control for in-memory backend.
- Encryption for sensitive fields.
- Retries for database operations.
- Configurable data limits.

Author: SFE Platform Team
"""

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Type, Union

# Import centralized OpenTelemetry configuration
from arbiter.otel_config import get_tracer
from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    field_serializer,
)

# Import tenacity for retries with exponential backoff
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Import cryptography for encryption
try:
    from cryptography.fernet import Fernet, InvalidToken

    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).warning(
        "cryptography library not found. Sensitive fields will not be encrypted."
    )
    CRYPTOGRAPHY_AVAILABLE = False

    class Fernet:
        def __init__(self, key: bytes):
            pass

        def encrypt(self, data: bytes) -> bytes:
            return data

        def decrypt(self, data: bytes) -> bytes:
            return data

    class InvalidToken(Exception):
        pass


# Import redis for Redis backend
try:
    import redis.asyncio as redis

    REDIS_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).warning(
        "redis.asyncio library not found. Redis backend will not be available."
    )
    REDIS_AVAILABLE = False

    class redis:
        class Redis:
            def __init__(self, *args, **kwargs):
                pass

            async def ping(self):
                raise ConnectionError("Redis not available")

            async def hset(self, *args, **kwargs):
                raise ConnectionError("Redis not available")

            async def hget(self, *args, **kwargs):
                raise ConnectionError("Redis not available")

            async def hgetall(self, *args, **kwargs):
                raise ConnectionError("Redis not available")

            async def close(self):
                pass

        class ConnectionError(Exception):
            pass

        class exceptions:
            ConnectionError = ConnectionError


# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(
    os.getenv("LOG_LEVEL", "INFO").upper()
)  # Allow log level to be configured via env var
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    )
    logger.addHandler(handler)

# Get tracer using centralized configuration
tracer = get_tracer(__name__)


# Prometheus metrics
def _get_or_create_metric(
    metric_class: Union[Type[Counter], Type[Gauge], Type[Histogram]],
    name: str,
    documentation: str,
    labelnames: Tuple[str, ...] = (),
    buckets: Optional[Tuple[float, ...]] = None,
) -> Union[Counter, Gauge, Histogram]:
    """
    Idempotently get or create a Prometheus metric.
    If the metric already exists in the registry, it returns the existing one.
    Otherwise, it creates a new metric of the specified class.
    This version is safer with the default global registry.
    """
    try:
        if buckets:
            return metric_class(
                name, documentation, labelnames=labelnames, buckets=buckets
            )
        return metric_class(name, documentation, labelnames=labelnames)
    except ValueError as e:
        if "Duplicated timeseries" in str(e):
            existing_metric = REGISTRY._names_to_collectors.get(name)
            if existing_metric and isinstance(existing_metric, metric_class):
                return existing_metric
            # If a metric with this name exists but is of a different type, or doesn't exist, re-raise.
            raise
        raise


MLDS_OPS_TOTAL = _get_or_create_metric(
    Counter,
    "meta_learning_ops_total",
    "Total MetaLearningDataStore operations",
    ["operation", "status"],
)
MLDS_OPS_LATENCY = _get_or_create_metric(
    Histogram,
    "meta_learning_ops_latency_seconds",
    "Latency of MetaLearningDataStore ops",
    ["operation"],
)
MLDS_DATA_SIZE = _get_or_create_metric(
    Gauge,
    "meta_learning_data_size",
    "Current number of records in MetaLearningDataStore",
    ["backend"],
)


# --- Pydantic Schema for Meta-Learning Records ---
class MetaLearningDataStoreConfig(BaseModel):
    """Configuration for meta learning data store."""

    db_url: Optional[str] = None


class MetaLearningRecord(BaseModel):
    experiment_id: str = Field(
        ..., description="Unique identifier for the meta-learning experiment."
    )
    task_type: str = Field(
        ..., description="Type of ML task (classification, regression, etc.)"
    )
    dataset_name: str = Field(..., description="Name of the dataset used.")
    meta_features: Dict[str, Any] = Field(
        ..., description="Meta-features extracted from the dataset."
    )
    hyperparameters: Dict[str, Any] = Field(
        ..., description="Hyperparameters/settings used."
    )
    metrics: Dict[str, float] = Field(..., description="Evaluation metrics/results.")
    model_artifact_uri: Optional[str] = Field(
        None, description="URI/path to the trained model artifact (can be encrypted)."
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="UTC timestamp of record creation."
    )
    tags: Optional[List[str]] = Field(
        default_factory=list, description="User-defined tags/annotations."
    )

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    @field_validator("tags", mode="before")
    @classmethod
    def validate_tags(cls, v):
        if v is None:
            return []
        validated_tags = []
        for tag in v:
            if not isinstance(tag, str):
                raise ValueError("Tag must be a string.")
            if len(tag) > 50:
                raise ValueError("Tag length cannot exceed 50 characters.")
            if (
                not tag.replace("-", "").replace("_", "").isalnum()
            ):  # Allow alphanumeric, hyphens, underscores
                raise ValueError(
                    "Tag can only contain alphanumeric characters, hyphens, and underscores."
                )
            validated_tags.append(tag)
        return validated_tags

    @field_serializer("timestamp")
    def serialize_timestamp(self, v: datetime) -> str:
        return v.isoformat()


# --- Exceptions ---
class MetaLearningDataStoreError(Exception):
    """Base exception for MetaLearningDataStore."""

    pass


class MetaLearningRecordNotFound(MetaLearningDataStoreError):
    pass


class MetaLearningRecordValidationError(MetaLearningDataStoreError):
    pass


class MetaLearningBackendError(MetaLearningDataStoreError):
    """Exception for issues with the backend storage."""

    pass


class MetaLearningEncryptionError(MetaLearningDataStoreError):
    """Exception for encryption/decryption failures."""

    pass


# Define transient errors for retry logic
TRANSIENT_ERRORS = (asyncio.TimeoutError, MetaLearningBackendError, ConnectionError)


# --- Base Data Store Interface ---
class BaseMetaLearningDataStore:
    def __init__(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def add_record(
        self, record: Union[MetaLearningRecord, Dict[str, Any]]
    ) -> str:
        raise NotImplementedError

    async def get_record(self, experiment_id: str) -> MetaLearningRecord:
        raise NotImplementedError

    async def list_records(
        self, filter_by: Optional[Dict[str, Any]] = None
    ) -> List[MetaLearningRecord]:
        raise NotImplementedError

    async def update_record(
        self, experiment_id: str, updates: Dict[str, Any]
    ) -> MetaLearningRecord:
        raise NotImplementedError

    async def delete_record(self, experiment_id: str) -> None:
        raise NotImplementedError

    async def _encrypt_field(self, data: Optional[str]) -> Optional[str]:
        if not data or not CRYPTOGRAPHY_AVAILABLE:
            return data

        REQUIRE_ENCRYPTION = (
            os.getenv("MLDS_ENCRYPTION_REQUIRED", "false").lower() == "true"
        )
        key = os.getenv("MLDS_ENCRYPTION_KEY")
        if not key:
            if REQUIRE_ENCRYPTION:
                raise MetaLearningEncryptionError(
                    "Encryption key not set and encryption is required."
                )
            logger.warning(
                "MLDS_ENCRYPTION_KEY not set. Sensitive fields will not be encrypted."
            )
            return data

        try:
            f = Fernet(key.encode("utf-8"))
            return f.encrypt(data.encode("utf-8")).decode("utf-8")
        except Exception as e:
            logger.error(f"Encryption failed: {e}", exc_info=True)
            raise MetaLearningEncryptionError(f"Failed to encrypt data: {e}")

    async def _decrypt_field(self, data: Optional[str]) -> Optional[str]:
        if not data or not CRYPTOGRAPHY_AVAILABLE:
            return data
        try:
            key = os.getenv("MLDS_ENCRYPTION_KEY")
            if not key:
                logger.warning(
                    "MLDS_ENCRYPTION_KEY not set. Cannot decrypt sensitive fields."
                )
                return data
            f = Fernet(key.encode("utf-8"))
            return f.decrypt(data.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            logger.error("Decryption failed: Invalid token.", exc_info=True)
            raise MetaLearningEncryptionError("Failed to decrypt data: Invalid token.")
        except Exception as e:
            logger.error(f"Decryption failed: {e}", exc_info=True)
            raise MetaLearningEncryptionError(f"Failed to decrypt data: {e}")


# --- In-Memory Backend ---
class InMemoryMetaLearningDataStore(BaseMetaLearningDataStore):
    """
    In-memory implementation of the MetaLearningDataStore for dev/test/small scale.
    Uses asyncio.Lock for concurrency control.
    """

    def __init__(self):
        super().__init__()
        self._store: Dict[str, MetaLearningRecord] = {}
        self._lock = asyncio.Lock()
        self.max_records = int(os.getenv("MLDS_MAX_RECORDS", "1000"))
        self._backend_label = "inmemory"
        MLDS_DATA_SIZE.labels(backend=self._backend_label).set(len(self._store))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        reraise=True,
    )
    async def add_record(
        self, record: Union[MetaLearningRecord, Dict[str, Any]]
    ) -> str:
        op = "add_record"
        start = time.monotonic()
        with tracer.start_as_current_span(f"meta_learning_{op}") as span:
            MLDS_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            try:
                if isinstance(record, dict):
                    record = MetaLearningRecord(**record)

                # Encrypt sensitive fields
                if record.model_artifact_uri:
                    record.model_artifact_uri = await self._encrypt_field(
                        record.model_artifact_uri
                    )

                async with self._lock:
                    if record.experiment_id in self._store:
                        raise MetaLearningDataStoreError(
                            f"Experiment ID {record.experiment_id} already exists."
                        )

                    if len(self._store) >= self.max_records:
                        raise MetaLearningDataStoreError(
                            f"Max records limit ({self.max_records}) reached for in-memory store."
                        )

                    self._store[record.experiment_id] = record
                    MLDS_DATA_SIZE.labels(backend=self._backend_label).inc()
                    logger.info(f"Added meta-learning record: {record.experiment_id}")
                    span.set_attribute("mlds.experiment_id", record.experiment_id)
                    MLDS_OPS_TOTAL.labels(operation=op, status="success").inc()
                    return record.experiment_id
            except (
                ValidationError,
                MetaLearningDataStoreError,
                MetaLearningEncryptionError,
            ) as e:
                logger.error(f"Error adding record: {e}", exc_info=True)
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise e
            except Exception as e:
                logger.error(f"Unexpected error adding record: {e}", exc_info=True)
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningDataStoreError(f"Failed to add record: {e}") from e
            finally:
                MLDS_OPS_LATENCY.labels(operation=op).observe(time.monotonic() - start)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        reraise=True,
    )
    async def get_record(self, experiment_id: str) -> MetaLearningRecord:
        op = "get_record"
        start = time.monotonic()
        with tracer.start_as_current_span(f"meta_learning_{op}") as span:
            MLDS_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            span.set_attribute("mlds.experiment_id", experiment_id)
            try:
                async with self._lock:
                    if experiment_id not in self._store:
                        raise MetaLearningRecordNotFound(
                            f"Experiment ID {experiment_id} not found."
                        )
                    record = self._store[experiment_id].copy(
                        deep=True
                    )  # Return a copy to prevent external modification

                # Decrypt sensitive fields
                if record.model_artifact_uri:
                    record.model_artifact_uri = await self._decrypt_field(
                        record.model_artifact_uri
                    )

                MLDS_OPS_TOTAL.labels(operation=op, status="success").inc()
                return record
            except (MetaLearningRecordNotFound, MetaLearningEncryptionError) as e:
                logger.error(
                    f"Error getting record {experiment_id}: {e}", exc_info=True
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise e
            except Exception as e:
                logger.error(
                    f"Unexpected error getting record {experiment_id}: {e}",
                    exc_info=True,
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningDataStoreError(f"Failed to get record: {e}") from e
            finally:
                MLDS_OPS_LATENCY.labels(operation=op).observe(time.monotonic() - start)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        reraise=True,
    )
    async def list_records(
        self, filter_by: Optional[Dict[str, Any]] = None
    ) -> List[MetaLearningRecord]:
        op = "list_records"
        start = time.monotonic()
        with tracer.start_as_current_span(f"meta_learning_{op}"):
            MLDS_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            try:
                async with self._lock:
                    records = list(self._store.values())

                filtered_records = []
                for r in records:
                    record_copy = r.copy(deep=True)  # Work on a copy
                    # Decrypt sensitive fields for filtering/return
                    if record_copy.model_artifact_uri:
                        record_copy.model_artifact_uri = await self._decrypt_field(
                            record_copy.model_artifact_uri
                        )

                    if filter_by:
                        matches = True
                        for k, v in filter_by.items():
                            if not hasattr(record_copy, k):
                                matches = False
                                break

                            attr = getattr(record_copy, k)
                            if k == "tags":
                                want = v if isinstance(v, (list, tuple, set)) else [v]
                                if not all(tag in (attr or []) for tag in want):
                                    matches = False
                                    break
                            elif isinstance(attr, dict):
                                if not all(item in attr.items() for item in v.items()):
                                    matches = False
                                    break
                            else:
                                if attr != v:
                                    matches = False
                                    break
                        if matches:
                            filtered_records.append(record_copy)
                    else:
                        filtered_records.append(record_copy)

                MLDS_OPS_TOTAL.labels(operation=op, status="success").inc()
                return filtered_records
            except Exception as e:
                logger.error(f"Error listing records: {e}", exc_info=True)
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningDataStoreError(f"Failed to list records: {e}") from e
            finally:
                MLDS_OPS_LATENCY.labels(operation=op).observe(time.monotonic() - start)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        reraise=True,
    )
    async def update_record(
        self, experiment_id: str, updates: Dict[str, Any]
    ) -> MetaLearningRecord:
        op = "update_record"
        start = time.monotonic()
        with tracer.start_as_current_span(f"meta_learning_{op}") as span:
            MLDS_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            span.set_attribute("mlds.experiment_id", experiment_id)
            try:
                async with self._lock:
                    if experiment_id not in self._store:
                        raise MetaLearningRecordNotFound(
                            f"Experiment ID {experiment_id} not found."
                        )

                    base = self._store[experiment_id]
                    # Only encrypt the new URI if it's provided
                    if (
                        "model_artifact_uri" in updates
                        and updates["model_artifact_uri"] is not None
                    ):
                        updates["model_artifact_uri"] = await self._encrypt_field(
                            updates["model_artifact_uri"]
                        )

                    updated = base.copy(update=updates, deep=True)
                    # Validate after update
                    updated = MetaLearningRecord(**updated.model_dump())
                    self._store[experiment_id] = updated
                    logger.info(f"Updated meta-learning record: {experiment_id}")

                    # Decrypt for return value
                    if updated.model_artifact_uri:
                        updated.model_artifact_uri = await self._decrypt_field(
                            updated.model_artifact_uri
                        )

                    MLDS_OPS_TOTAL.labels(operation=op, status="success").inc()
                    return updated
            except (
                ValidationError,
                MetaLearningRecordNotFound,
                MetaLearningEncryptionError,
            ) as e:
                logger.error(
                    f"Error updating record {experiment_id}: {e}", exc_info=True
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise e
            except Exception as e:
                logger.error(
                    f"Unexpected error updating record {experiment_id}: {e}",
                    exc_info=True,
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningDataStoreError(f"Failed to update record: {e}") from e
            finally:
                MLDS_OPS_LATENCY.labels(operation=op).observe(time.monotonic() - start)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        reraise=True,
    )
    async def delete_record(self, experiment_id: str) -> None:
        op = "delete_record"
        start = time.monotonic()
        with tracer.start_as_current_span(f"meta_learning_{op}") as span:
            MLDS_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            span.set_attribute("mlds.experiment_id", experiment_id)
            try:
                async with self._lock:
                    if experiment_id not in self._store:
                        raise MetaLearningRecordNotFound(
                            f"Experiment ID {experiment_id} not found."
                        )
                    del self._store[experiment_id]
                    MLDS_DATA_SIZE.labels(backend=self._backend_label).dec()
                    logger.info(f"Deleted meta-learning record: {experiment_id}")
                    MLDS_OPS_TOTAL.labels(operation=op, status="success").inc()
            except MetaLearningRecordNotFound as e:
                logger.error(
                    f"Error deleting record {experiment_id}: {e}", exc_info=True
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise e
            except Exception as e:
                logger.error(
                    f"Unexpected error deleting record {experiment_id}: {e}",
                    exc_info=True,
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningDataStoreError(f"Failed to delete record: {e}") from e
            finally:
                MLDS_OPS_LATENCY.labels(operation=op).observe(time.monotonic() - start)


# --- Redis Backend ---
class RedisMetaLearningDataStore(BaseMetaLearningDataStore):
    """
    Redis implementation of the MetaLearningDataStore.
    Uses Redis hashes to store records.
    """

    def __init__(self):
        super().__init__()
        if not REDIS_AVAILABLE:
            raise ImportError(
                "redis.asyncio is not installed. Cannot use Redis backend."
            )

        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._redis = redis.from_url(self.redis_url, decode_responses=True)
        self.redis_hash_key = os.getenv("REDIS_HASH_KEY", "meta_learning_records")
        self.max_records = int(
            os.getenv("MLDS_MAX_RECORDS", "100000")
        )  # Higher limit for Redis
        self._backend_label = "redis"
        logger.info(
            f"RedisMetaLearningDataStore initialized with URL: {self.redis_url}, Hash Key: {self.redis_hash_key}"
        )

    async def __aenter__(self):
        await self._check_connection()
        current_size = await self._redis.hlen(self.redis_hash_key)
        MLDS_DATA_SIZE.labels(backend=self._backend_label).set(current_size)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._redis.close()
        logger.info("Redis connection closed.")

    async def _check_connection(self):
        try:
            await self._redis.ping()
            logger.debug("Redis connection successful.")
        except ConnectionError as e:
            logger.error(f"Redis connection error: {e}", exc_info=True)
            raise MetaLearningBackendError(f"Redis connection failed: {e}") from e

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        reraise=True,
    )
    async def add_record(
        self, record: Union[MetaLearningRecord, Dict[str, Any]]
    ) -> str:
        op = "add_record"
        start = time.monotonic()
        with tracer.start_as_current_span(f"meta_learning_{op}") as span:
            MLDS_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            try:
                if isinstance(record, dict):
                    record = MetaLearningRecord(**record)

                # Encrypt sensitive fields
                if record.model_artifact_uri:
                    record.model_artifact_uri = await self._encrypt_field(
                        record.model_artifact_uri
                    )

                record_json = record.json()

                # Check record limit
                current_size = await self._redis.hlen(self.redis_hash_key)
                if current_size >= self.max_records:
                    raise MetaLearningDataStoreError(
                        f"Max records limit ({self.max_records}) reached for Redis store."
                    )

                result = await self._redis.hset(
                    self.redis_hash_key, record.experiment_id, record_json
                )
                if result == 0:  # Key already existed and was updated
                    raise MetaLearningDataStoreError(
                        f"Experiment ID {record.experiment_id} already exists (was updated instead of added)."
                    )

                MLDS_DATA_SIZE.labels(backend=self._backend_label).inc()
                logger.info(
                    f"Added meta-learning record to Redis: {record.experiment_id}"
                )
                span.set_attribute("mlds.experiment_id", record.experiment_id)
                MLDS_OPS_TOTAL.labels(operation=op, status="success").inc()
                return record.experiment_id
            except (
                ValidationError,
                MetaLearningDataStoreError,
                MetaLearningEncryptionError,
            ) as e:
                logger.error(f"Error adding record to Redis: {e}", exc_info=True)
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise e
            except ConnectionError as e:
                logger.error(
                    f"Redis connection error during add_record: {e}", exc_info=True
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningBackendError(f"Redis operation failed: {e}") from e
            except Exception as e:
                logger.error(
                    f"Unexpected error adding record to Redis: {e}", exc_info=True
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningDataStoreError(f"Failed to add record: {e}") from e
            finally:
                MLDS_OPS_LATENCY.labels(operation=op).observe(time.monotonic() - start)

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        reraise=True,
    )
    async def get_record(self, experiment_id: str) -> MetaLearningRecord:
        op = "get_record"
        start = time.monotonic()
        with tracer.start_as_current_span(f"meta_learning_{op}") as span:
            MLDS_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            span.set_attribute("mlds.experiment_id", experiment_id)
            try:
                record_json = await self._redis.hget(self.redis_hash_key, experiment_id)
                if not record_json:
                    raise MetaLearningRecordNotFound(
                        f"Experiment ID {experiment_id} not found in Redis."
                    )

                record = MetaLearningRecord.parse_raw(record_json)

                # Decrypt sensitive fields
                if record.model_artifact_uri:
                    record.model_artifact_uri = await self._decrypt_field(
                        record.model_artifact_uri
                    )

                MLDS_OPS_TOTAL.labels(operation=op, status="success").inc()
                return record
            except (MetaLearningRecordNotFound, MetaLearningEncryptionError) as e:
                logger.error(
                    f"Error getting record {experiment_id} from Redis: {e}",
                    exc_info=True,
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise e
            except ConnectionError as e:
                logger.error(
                    f"Redis connection error during get_record: {e}", exc_info=True
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningBackendError(f"Redis operation failed: {e}") from e
            except Exception as e:
                logger.error(
                    f"Unexpected error getting record {experiment_id} from Redis: {e}",
                    exc_info=True,
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningDataStoreError(f"Failed to get record: {e}") from e
            finally:
                MLDS_OPS_LATENCY.labels(operation=op).observe(time.monotonic() - start)

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        reraise=True,
    )
    async def list_records(
        self, filter_by: Optional[Dict[str, Any]] = None
    ) -> List[MetaLearningRecord]:
        op = "list_records"
        start = time.monotonic()
        with tracer.start_as_current_span(f"meta_learning_{op}"):
            MLDS_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            try:
                all_records_data = await self._redis.hgetall(self.redis_hash_key)
                records = []
                for _, record_json in all_records_data.items():
                    try:
                        record = MetaLearningRecord.parse_raw(record_json)
                        # Decrypt sensitive fields
                        if record.model_artifact_uri:
                            record.model_artifact_uri = await self._decrypt_field(
                                record.model_artifact_uri
                            )
                        records.append(record)
                    except ValidationError as e:
                        logger.warning(
                            f"Skipping invalid record from Redis: {e}. Data: {record_json[:100]}...",
                            exc_info=True,
                        )
                    except MetaLearningEncryptionError as e:
                        logger.warning(
                            f"Skipping record due to decryption error: {e}. Data: {record_json[:100]}...",
                            exc_info=True,
                        )

                filtered_records = []
                if filter_by:
                    for r in records:
                        matches = True
                        for k, v in filter_by.items():
                            if not hasattr(r, k):
                                matches = False
                                break

                            attr = getattr(r, k)
                            if k == "tags":
                                want = v if isinstance(v, (list, tuple, set)) else [v]
                                if not all(tag in (attr or []) for tag in want):
                                    matches = False
                                    break
                            elif isinstance(attr, dict):
                                if not all(item in attr.items() for item in v.items()):
                                    matches = False
                                    break
                            else:
                                if attr != v:
                                    matches = False
                                    break
                        if matches:
                            filtered_records.append(r)
                else:
                    filtered_records = records

                MLDS_OPS_TOTAL.labels(operation=op, status="success").inc()
                return filtered_records
            except ConnectionError as e:
                logger.error(
                    f"Redis connection error during list_records: {e}", exc_info=True
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningBackendError(f"Redis operation failed: {e}") from e
            except Exception as e:
                logger.error(
                    f"Unexpected error listing records from Redis: {e}", exc_info=True
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningDataStoreError(f"Failed to list records: {e}") from e
            finally:
                MLDS_OPS_LATENCY.labels(operation=op).observe(time.monotonic() - start)

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        reraise=True,
    )
    async def update_record(
        self, experiment_id: str, updates: Dict[str, Any]
    ) -> MetaLearningRecord:
        op = "update_record"
        start = time.monotonic()
        with tracer.start_as_current_span(f"meta_learning_{op}") as span:
            MLDS_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            span.set_attribute("mlds.experiment_id", experiment_id)
            try:
                existing_record_json = await self._redis.hget(
                    self.redis_hash_key, experiment_id
                )
                if not existing_record_json:
                    raise MetaLearningRecordNotFound(
                        f"Experiment ID {experiment_id} not found in Redis for update."
                    )

                base_record = MetaLearningRecord.parse_raw(existing_record_json)

                # Only decrypt the URI if it's not being updated, to avoid unnecessary work
                if (
                    "model_artifact_uri" not in updates
                    and base_record.model_artifact_uri
                ):
                    base_record.model_artifact_uri = await self._decrypt_field(
                        base_record.model_artifact_uri
                    )

                # Encrypt new model_artifact_uri if provided in updates
                if (
                    "model_artifact_uri" in updates
                    and updates["model_artifact_uri"] is not None
                ):
                    updates["model_artifact_uri"] = await self._encrypt_field(
                        updates["model_artifact_uri"]
                    )

                updated_record = base_record.copy(update=updates, deep=True)
                # Validate after update
                updated_record = MetaLearningRecord(**updated_record.model_dump())

                # Re-encrypt for storage if it was decrypted for update logic
                if (
                    updated_record.model_artifact_uri
                    and "model_artifact_uri" not in updates
                ):
                    updated_record.model_artifact_uri = await self._encrypt_field(
                        updated_record.model_artifact_uri
                    )

                await self._redis.hset(
                    self.redis_hash_key, experiment_id, updated_record.json()
                )
                logger.info(f"Updated meta-learning record in Redis: {experiment_id}")

                # Decrypt for return value
                if updated_record.model_artifact_uri:
                    updated_record.model_artifact_uri = await self._decrypt_field(
                        updated_record.model_artifact_uri
                    )

                MLDS_OPS_TOTAL.labels(operation=op, status="success").inc()
                return updated_record
            except (
                ValidationError,
                MetaLearningRecordNotFound,
                MetaLearningEncryptionError,
            ) as e:
                logger.error(
                    f"Error updating record {experiment_id} in Redis: {e}",
                    exc_info=True,
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise e
            except ConnectionError as e:
                logger.error(
                    f"Redis connection error during update_record: {e}", exc_info=True
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningBackendError(f"Redis operation failed: {e}") from e
            except Exception as e:
                logger.error(
                    f"Unexpected error updating record {experiment_id} in Redis: {e}",
                    exc_info=True,
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningDataStoreError(f"Failed to update record: {e}") from e
            finally:
                MLDS_OPS_LATENCY.labels(operation=op).observe(time.monotonic() - start)

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        reraise=True,
    )
    async def delete_record(self, experiment_id: str) -> None:
        op = "delete_record"
        start = time.monotonic()
        with tracer.start_as_current_span(f"meta_learning_{op}") as span:
            MLDS_OPS_TOTAL.labels(operation=op, status="attempt").inc()
            span.set_attribute("mlds.experiment_id", experiment_id)
            try:
                result = await self._redis.hdel(self.redis_hash_key, experiment_id)
                if result == 0:
                    raise MetaLearningRecordNotFound(
                        f"Experiment ID {experiment_id} not found in Redis for deletion."
                    )

                MLDS_DATA_SIZE.labels(backend=self._backend_label).dec()
                logger.info(f"Deleted meta-learning record from Redis: {experiment_id}")
                MLDS_OPS_TOTAL.labels(operation=op, status="success").inc()
            except MetaLearningRecordNotFound as e:
                logger.error(
                    f"Error deleting record {experiment_id} from Redis: {e}",
                    exc_info=True,
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise e
            except ConnectionError as e:
                logger.error(
                    f"Redis connection error during delete_record: {e}", exc_info=True
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningBackendError(f"Redis operation failed: {e}") from e
            except Exception as e:
                logger.error(
                    f"Unexpected error deleting record {experiment_id} from Redis: {e}",
                    exc_info=True,
                )
                MLDS_OPS_TOTAL.labels(operation=op, status="failure").inc()
                raise MetaLearningDataStoreError(f"Failed to delete record: {e}") from e
            finally:
                MLDS_OPS_LATENCY.labels(operation=op).observe(time.monotonic() - start)


# --- Factory Method for Backend Selection ---
def get_meta_learning_data_store(
    backend: Optional[str] = None, **kwargs
) -> BaseMetaLearningDataStore:
    """
    Factory method to get a MetaLearningDataStore instance based on the backend.
    Backend is determined by MLDS_BACKEND environment variable, defaulting to 'inmemory'.
    """
    if backend is None:
        backend = os.getenv("MLDS_BACKEND", "inmemory").lower()

    if backend == "inmemory":
        logger.info("Initializing InMemoryMetaLearningDataStore.")
        return InMemoryMetaLearningDataStore()
    elif backend == "redis":
        if not REDIS_AVAILABLE:
            raise ImportError(
                "Redis backend requested but redis.asyncio is not installed."
            )
        logger.info("Initializing RedisMetaLearningDataStore.")
        return RedisMetaLearningDataStore()
    else:
        raise NotImplementedError(f"Backend '{backend}' not implemented or supported.")


# --- Example Usage (for testing/demo) ---
async def main():
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Generate a Fernet key for encryption if not already set
    if not os.getenv("MLDS_ENCRYPTION_KEY") and CRYPTOGRAPHY_AVAILABLE:
        key = Fernet.generate_key().decode("utf-8")
        os.environ["MLDS_ENCRYPTION_KEY"] = key
    if CRYPTOGRAPHY_AVAILABLE:
        logger.info("MLDS_ENCRYPTION_KEY is set. Encryption is enabled.")

    # --- Test In-Memory Backend ---
    logger.info("\n--- Testing In-Memory Backend ---")
    os.environ["MLDS_BACKEND"] = "inmemory"
    os.environ["MLDS_MAX_RECORDS"] = "5"  # Set a small limit for testing
    in_memory_store = get_meta_learning_data_store()

    record1 = {
        "experiment_id": "mem_exp001",
        "task_type": "classification",
        "dataset_name": "iris",
        "meta_features": {"n_samples": 150, "n_features": 4},
        "hyperparameters": {"max_depth": 3, "min_samples_split": 2},
        "metrics": {"accuracy": 0.95},
        "model_artifact_uri": "s3://models/iris/v1",
        "tags": ["test", "baseline"],
    }

    try:
        exp_id1 = await in_memory_store.add_record(record1)
        rec1 = await in_memory_store.get_record(exp_id1)
        assert rec1.experiment_id == "mem_exp001"
        assert rec1.model_artifact_uri == "s3://models/iris/v1"  # Should be decrypted
        logger.info(f"In-memory record added and retrieved: {rec1.experiment_id}")

        await in_memory_store.update_record(exp_id1, {"metrics": {"accuracy": 0.96}})
        updated_rec1 = await in_memory_store.get_record(exp_id1)
        assert updated_rec1.metrics["accuracy"] == 0.96
        logger.info(f"In-memory record updated: {updated_rec1.experiment_id}")

        all_recs_mem = await in_memory_store.list_records()
        assert len(all_recs_mem) == 1
        logger.info(f"In-memory list records count: {len(all_recs_mem)}")

        await in_memory_store.delete_record(exp_id1)
        assert len(await in_memory_store.list_records()) == 0
        logger.info("In-memory record deleted successfully.")

        # Test limit
        for i in range(in_memory_store.max_records):
            await in_memory_store.add_record(
                MetaLearningRecord(
                    experiment_id=f"limit_exp_{i}",
                    task_type="test",
                    dataset_name="test",
                    meta_features={},
                    hyperparameters={},
                    metrics={},
                )
            )

        try:
            await in_memory_store.add_record(
                MetaLearningRecord(
                    experiment_id="over_limit",
                    task_type="test",
                    dataset_name="test",
                    meta_features={},
                    hyperparameters={},
                    metrics={},
                )
            )
            assert False, "Should have raised MetaLearningDataStoreError for limit"
        except MetaLearningDataStoreError as e:
            logger.info(f"Successfully caught expected limit error: {e}")

        # Clean up limit test records
        for i in range(in_memory_store.max_records):
            await in_memory_store.delete_record(f"limit_exp_{i}")

    except Exception as e:
        logger.error(f"In-memory backend test failed: {e}", exc_info=True)

    # --- Test Redis Backend (if available) ---
    if REDIS_AVAILABLE:
        logger.info("\n--- Testing Redis Backend ---")
        os.environ["MLDS_BACKEND"] = "redis"
        os.environ["REDIS_URL"] = (
            "redis://localhost:6379/1"  # Use a different DB for testing
        )
        os.environ["MLDS_MAX_RECORDS"] = "10"  # Set a small limit for testing Redis

        redis_store: Optional[RedisMetaLearningDataStore] = None
        try:
            redis_store = get_meta_learning_data_store()
            async with redis_store:
                # Clear existing data in test DB
                await redis_store._redis.flushdb()
                MLDS_DATA_SIZE.labels(backend=redis_store._backend_label).set(
                    0
                )  # Reset metric after flush

                record_redis = {
                    "experiment_id": "redis_exp001",
                    "task_type": "regression",
                    "dataset_name": "boston",
                    "meta_features": {"n_samples": 506, "n_features": 13},
                    "hyperparameters": {"n_estimators": 100},
                    "metrics": {"rmse": 4.5},
                    "model_artifact_uri": "s3://models/boston/v2",
                    "tags": ["prod", "new_model"],
                }

                exp_id_redis = await redis_store.add_record(record_redis)
                rec_redis = await redis_store.get_record(exp_id_redis)
                assert rec_redis.experiment_id == "redis_exp001"
                assert (
                    rec_redis.model_artifact_uri == "s3://models/boston/v2"
                )  # Should be decrypted
                logger.info(
                    f"Redis record added and retrieved: {rec_redis.experiment_id}"
                )

                await redis_store.update_record(
                    exp_id_redis, {"metrics": {"rmse": 4.2}}
                )
                updated_rec_redis = await redis_store.get_record(exp_id_redis)
                assert updated_rec_redis.metrics["rmse"] == 4.2
                logger.info(f"Redis record updated: {updated_rec_redis.experiment_id}")

                all_recs_redis = await redis_store.list_records()
                assert len(all_recs_redis) == 1
                logger.info(f"Redis list records count: {len(all_recs_redis)}")

                await redis_store.delete_record(exp_id_redis)
                assert len(await redis_store.list_records()) == 0
                logger.info("Redis record deleted successfully.")

                # Test limit for Redis
                for i in range(redis_store.max_records):
                    await redis_store.add_record(
                        MetaLearningRecord(
                            experiment_id=f"redis_limit_exp_{i}",
                            task_type="test",
                            dataset_name="test",
                            meta_features={},
                            hyperparameters={},
                            metrics={},
                        )
                    )

                try:
                    await redis_store.add_record(
                        MetaLearningRecord(
                            experiment_id="redis_over_limit",
                            task_type="test",
                            dataset_name="test",
                            meta_features={},
                            hyperparameters={},
                            metrics={},
                        )
                    )
                    assert (
                        False
                    ), "Should have raised MetaLearningDataStoreError for Redis limit"
                except MetaLearningDataStoreError as e:
                    logger.info(f"Successfully caught expected Redis limit error: {e}")

                # Clean up limit test records
                for i in range(redis_store.max_records):
                    await redis_store.delete_record(f"redis_limit_exp_{i}")

        except Exception as e:
            logger.error(f"Redis backend test failed: {e}", exc_info=True)
    else:
        logger.warning(
            "\n--- Skipping Redis Backend Tests (redis.asyncio not installed) ---"
        )

    print("\nMetaLearningDataStore E2E tests finished.")


if __name__ == "__main__":
    # To run this, ensure you have the necessary libraries installed:
    # pip install pydantic prometheus_client opentelemetry-sdk opentelemetry-exporter-otlp tenacity cryptography redis[async]

    # For Redis testing, ensure a Redis server is running, and set environment variables:
    # export MLDS_BACKEND="redis"
    # export REDIS_URL="redis://localhost:6379/1" # Use a dedicated DB for testing
    # export MLDS_ENCRYPTION_KEY="<your_fernet_key_here>" # Generate one using Fernet.generate_key().decode()
    # export MLDS_MAX_RECORDS="100000" # Set your desired limit
    # export LOG_LEVEL="DEBUG" # Optional: Set to DEBUG for more verbose logging
    # export MLDS_ENABLE_OTEL="true" # Optional: Set to "true" to enable OpenTelemetry
    # export SFE_OTEL_EXPORTER_TYPE="otlp" # Optional: Set to "otlp" to export traces via OTLP

    asyncio.run(main())
