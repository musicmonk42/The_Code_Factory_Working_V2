import asyncio
import json
import logging
import os
import pytest
import pytest_asyncio
from pytest_mock import MockerFixture
from datetime import datetime, timezone
from typing import Dict, List
from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# Import the centralized tracer configuration
from arbiter.otel_config import get_tracer

# Import from the correct module
from meta_learning_data_store import (
    get_meta_learning_data_store, MetaLearningRecord, MetaLearningDataStoreError,
    MetaLearningRecordNotFound, MetaLearningRecordValidationError, MetaLearningBackendError,
    MetaLearningEncryptionError, MLDS_OPS_TOTAL, MLDS_OPS_LATENCY, MLDS_DATA_SIZE
)

# Configure logging for tests
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Get tracer using centralized configuration
tracer = get_tracer("test-meta-learning-data-store")

# Setup in-memory exporter for testing
in_memory_exporter = InMemorySpanExporter()

# Sample environment variables for tests
SAMPLE_ENV = {
    "MLDS_BACKEND": "inmemory",
    "REDIS_URL": "redis://localhost:6379/1",
    "MLDS_ENCRYPTION_KEY": "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleQ==",  # Valid base64 Fernet key
    "MLDS_MAX_RECORDS": "5",
    "LOG_LEVEL": "DEBUG",
    "SFE_OTEL_EXPORTER_TYPE": "console"
}

# Sample record for testing
SAMPLE_RECORD = {
    "experiment_id": "test_exp_001",
    "task_type": "classification",
    "dataset_name": "iris",
    "meta_features": {"n_samples": 150, "n_features": 4},
    "hyperparameters": {"max_depth": 3},
    "metrics": {"accuracy": 0.95},
    "model_artifact_uri": "s3://models/iris/v1",
    "tags": ["test", "baseline"]
}

def get_metric_value(metric, **labels):
    """Helper to get metric value with labels."""
    try:
        if labels:
            return metric.labels(**labels)._value.get()
        else:
            return metric._value.get()
    except:
        return 0

@pytest_asyncio.fixture(autouse=True)
async def setup_env(mocker: MockerFixture):
    """Set up environment variables and clean up after tests."""
    for key, value in SAMPLE_ENV.items():
        mocker.patch.dict(os.environ, {key: value})
    yield
    for key in SAMPLE_ENV:
        os.environ.pop(key, None)

@pytest_asyncio.fixture
async def inmemory_store():
    """Fixture for InMemoryMetaLearningDataStore."""
    store = get_meta_learning_data_store(backend="inmemory")
    yield store
    # Clear store after tests
    async with store._lock:
        store._store.clear()
    MLDS_DATA_SIZE.labels(backend="inmemory").set(0)

@pytest_asyncio.fixture
async def redis_store(mocker: MockerFixture):
    """Fixture for RedisMetaLearningDataStore with mocked Redis."""
    try:
        import redis.asyncio as redis
    except ImportError:
        pytest.skip("Redis library not installed; skipping RedisMetaLearningDataStore tests.")
    
    # Mock Redis client
    mock_redis = mocker.AsyncMock()
    mock_redis.ping = mocker.AsyncMock(return_value=True)
    mock_redis.hset = mocker.AsyncMock(return_value=1)
    mock_redis.hget = mocker.AsyncMock(return_value=None)
    mock_redis.hgetall = mocker.AsyncMock(return_value={})
    mock_redis.hdel = mocker.AsyncMock(return_value=1)
    mock_redis.hlen = mocker.AsyncMock(return_value=0)
    mock_redis.flushdb = mocker.AsyncMock()
    mock_redis.close = mocker.AsyncMock()
    
    # Mock redis.from_url to return our mock
    mocker.patch("meta_learning_data_store.redis.from_url", return_value=mock_redis)
    
    store = get_meta_learning_data_store(backend="redis")
    store._redis = mock_redis
    store._connected = True
    
    yield store
    
    # Reset mock state
    mock_redis.reset_mock()

@pytest_asyncio.fixture(autouse=True)
async def clear_metrics_and_traces():
    """Clear Prometheus metrics and OpenTelemetry traces before each test."""
    in_memory_exporter.clear()
    # Reset gauge metrics
    try:
        MLDS_DATA_SIZE.labels(backend="inmemory").set(0)
        MLDS_DATA_SIZE.labels(backend="redis").set(0)
    except:
        pass
    yield

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_initialization_success(store_type, inmemory_store, redis_store):
    """Test successful initialization of data store."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    assert store.max_records == 5
    if store_type == "redis":
        assert store.redis_url == SAMPLE_ENV["REDIS_URL"]
        assert store.redis_hash_key == "meta_learning_records"

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_add_record_success(store_type, inmemory_store, redis_store):
    """Test successful addition of a record."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    
    if store_type == "redis":
        # Setup mock for Redis to simulate record doesn't exist yet
        store._redis.hlen.return_value = 0
        store._redis.hset.return_value = 1  # New record added
    
    exp_id = await store.add_record(SAMPLE_RECORD)
    assert exp_id == "test_exp_001"
    assert get_metric_value(MLDS_OPS_TOTAL, operation="add_record", status="success") == 1
    assert get_metric_value(MLDS_DATA_SIZE, backend=store._backend_label) == 1
    
    spans = in_memory_exporter.get_finished_spans()
    add_span = next((span for span in spans if span.name == "meta_learning_add_record"), None)
    assert add_span is not None
    assert add_span.attributes["mlds.experiment_id"] == "test_exp_001"

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_add_record_duplicate(store_type, inmemory_store, redis_store):
    """Test adding a duplicate record raises error."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    
    if store_type == "redis":
        store._redis.hlen.return_value = 0
        store._redis.hset.return_value = 1
    
    await store.add_record(SAMPLE_RECORD)
    
    if store_type == "redis":
        # Simulate record already exists
        store._redis.hset.return_value = 0
    
    with pytest.raises(MetaLearningDataStoreError, match="already exists"):
        await store.add_record(SAMPLE_RECORD)
    
    assert get_metric_value(MLDS_OPS_TOTAL, operation="add_record", status="failure") >= 1

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_add_record_validation_failure(store_type, inmemory_store, redis_store):
    """Test adding a record with invalid data."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    invalid_record = SAMPLE_RECORD.copy()
    invalid_record["task_type"] = 123  # Invalid type
    
    with pytest.raises((MetaLearningRecordValidationError, MetaLearningDataStoreError)):
        await store.add_record(invalid_record)
    
    assert get_metric_value(MLDS_OPS_TOTAL, operation="add_record", status="failure") >= 1

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_get_record_success(store_type, inmemory_store, redis_store):
    """Test successful retrieval of a record."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    
    if store_type == "redis":
        store._redis.hlen.return_value = 0
        store._redis.hset.return_value = 1
        # Mock the get to return the record
        record_obj = MetaLearningRecord(**SAMPLE_RECORD)
        store._redis.hget.return_value = record_obj.json()
    
    await store.add_record(SAMPLE_RECORD)
    record = await store.get_record("test_exp_001")
    
    assert record.experiment_id == "test_exp_001"
    assert record.task_type == "classification"
    assert record.model_artifact_uri == "s3://models/iris/v1"  # Should be decrypted
    assert get_metric_value(MLDS_OPS_TOTAL, operation="get_record", status="success") >= 1

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_get_record_not_found(store_type, inmemory_store, redis_store):
    """Test retrieving a non-existent record."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    
    if store_type == "redis":
        store._redis.hget.return_value = None
    
    with pytest.raises(MetaLearningRecordNotFound, match="not found"):
        await store.get_record("non_existent")
    
    assert get_metric_value(MLDS_OPS_TOTAL, operation="get_record", status="failure") >= 1

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_list_records_success(store_type, inmemory_store, redis_store):
    """Test listing records with and without filters."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    
    if store_type == "redis":
        store._redis.hlen.return_value = 0
        store._redis.hset.return_value = 1
        # Mock hgetall to return our record
        record_obj = MetaLearningRecord(**SAMPLE_RECORD)
        store._redis.hgetall.return_value = {"test_exp_001": record_obj.json()}
    
    await store.add_record(SAMPLE_RECORD)
    
    records = await store.list_records()
    assert len(records) == 1
    assert records[0].experiment_id == "test_exp_001"
    
    # Test with filter
    records_filtered = await store.list_records(filter_by={"task_type": "classification"})
    assert len(records_filtered) == 1
    
    records_filtered = await store.list_records(filter_by={"task_type": "regression"})
    assert len(records_filtered) == 0
    
    assert get_metric_value(MLDS_OPS_TOTAL, operation="list_records", status="success") >= 2

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_update_record_success(store_type, inmemory_store, redis_store):
    """Test successful update of a record."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    
    if store_type == "redis":
        store._redis.hlen.return_value = 0
        store._redis.hset.return_value = 1
        record_obj = MetaLearningRecord(**SAMPLE_RECORD)
        store._redis.hget.return_value = record_obj.json()
    
    await store.add_record(SAMPLE_RECORD)
    
    updates = {"metrics": {"accuracy": 0.96}, "model_artifact_uri": "s3://models/iris/v2"}
    updated = await store.update_record("test_exp_001", updates)
    
    assert updated.metrics["accuracy"] == 0.96
    assert updated.model_artifact_uri == "s3://models/iris/v2"  # Should be decrypted
    assert get_metric_value(MLDS_OPS_TOTAL, operation="update_record", status="success") >= 1

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_update_record_not_found(store_type, inmemory_store, redis_store):
    """Test updating a non-existent record."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    
    if store_type == "redis":
        store._redis.hget.return_value = None
    
    with pytest.raises(MetaLearningRecordNotFound, match="not found"):
        await store.update_record("non_existent", {"metrics": {"accuracy": 0.96}})
    
    assert get_metric_value(MLDS_OPS_TOTAL, operation="update_record", status="failure") >= 1

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_delete_record_success(store_type, inmemory_store, redis_store):
    """Test successful deletion of a record."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    
    if store_type == "redis":
        store._redis.hlen.return_value = 0
        store._redis.hset.return_value = 1
        store._redis.hdel.return_value = 1  # Successfully deleted
    
    await store.add_record(SAMPLE_RECORD)
    await store.delete_record("test_exp_001")
    
    assert get_metric_value(MLDS_DATA_SIZE, backend=store._backend_label) == 0
    assert get_metric_value(MLDS_OPS_TOTAL, operation="delete_record", status="success") >= 1

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_delete_record_not_found(store_type, inmemory_store, redis_store):
    """Test deleting a non-existent record."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    
    if store_type == "redis":
        store._redis.hdel.return_value = 0  # Nothing deleted
    
    with pytest.raises(MetaLearningRecordNotFound, match="not found"):
        await store.delete_record("non_existent")
    
    assert get_metric_value(MLDS_OPS_TOTAL, operation="delete_record", status="failure") >= 1

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_max_records_limit(store_type, inmemory_store, redis_store):
    """Test max records limit enforcement."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    
    if store_type == "redis":
        # Mock hlen to simulate increasing record count
        store._redis.hlen.side_effect = list(range(6))  # 0, 1, 2, 3, 4, 5
        store._redis.hset.return_value = 1
    
    for i in range(5):  # Max limit set to 5 in SAMPLE_ENV
        await store.add_record({
            "experiment_id": f"limit_exp_{i}",
            "task_type": "test",
            "dataset_name": "test",
            "meta_features": {},
            "hyperparameters": {},
            "metrics": {},
            "tags": ["limit"]
        })
    
    with pytest.raises(MetaLearningDataStoreError, match="Max records limit"):
        await store.add_record({
            "experiment_id": "over_limit",
            "task_type": "test",
            "dataset_name": "test",
            "meta_features": {},
            "hyperparameters": {},
            "metrics": {},
            "tags": ["limit"]
        })

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_encryption_decryption(store_type, inmemory_store, redis_store):
    """Test encryption and decryption of model_artifact_uri."""
    try:
        from cryptography.fernet import Fernet
        CRYPTOGRAPHY_AVAILABLE = True
    except ImportError:
        CRYPTOGRAPHY_AVAILABLE = False
    
    store = inmemory_store if store_type == "inmemory" else redis_store
    
    if store_type == "redis":
        store._redis.hlen.return_value = 0
        store._redis.hset.return_value = 1
    
    record = SAMPLE_RECORD.copy()
    await store.add_record(record)
    
    if CRYPTOGRAPHY_AVAILABLE:
        # Verify that the stored value is encrypted (not the original)
        if store_type == "inmemory":
            stored_record = store._store.get("test_exp_001")
            if stored_record and stored_record.model_artifact_uri:
                # The stored URI should be encrypted (different from original)
                assert stored_record.model_artifact_uri != record["model_artifact_uri"]
    
    # When retrieving, it should be decrypted
    if store_type == "redis":
        # Mock the retrieval
        encrypted_record = MetaLearningRecord(**record)
        if CRYPTOGRAPHY_AVAILABLE:
            # Simulate encrypted storage
            encrypted_record.model_artifact_uri = "encrypted_value"
        store._redis.hget.return_value = encrypted_record.json()
    
    retrieved = await store.get_record("test_exp_001")
    assert retrieved.model_artifact_uri == "s3://models/iris/v1"  # Should be decrypted

@pytest.mark.asyncio
async def test_redis_connection_failure(redis_store, mocker: MockerFixture):
    """Test Redis backend connection failure."""
    try:
        import redis.asyncio as redis
    except ImportError:
        pytest.skip("Redis library not installed.")
    
    # Mock ping to fail
    redis_store._redis.ping.side_effect = redis.ConnectionError("Connection failed")
    
    # Since _check_connection is called internally, we need to trigger it through a public method
    # The add_record method doesn't directly call _check_connection in the implementation,
    # but we can test connection errors during operations
    redis_store._redis.hlen.side_effect = redis.ConnectionError("Connection failed")
    
    with pytest.raises(MetaLearningBackendError, match="Redis operation failed"):
        await redis_store.add_record(SAMPLE_RECORD)

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_concurrent_add_records(store_type, inmemory_store, redis_store):
    """Test concurrent addition of records."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    
    if store_type == "redis":
        store._redis.hlen.return_value = 0
        store._redis.hset.return_value = 1
    
    async def add_task(i: int):
        return await store.add_record({
            "experiment_id": f"conc_exp_{i}",
            "task_type": "test",
            "dataset_name": "test",
            "meta_features": {},
            "hyperparameters": {},
            "metrics": {},
            "tags": ["concurrent"]
        })

    tasks = [add_task(i) for i in range(4)]
    exp_ids = await asyncio.gather(*tasks)
    
    assert len(exp_ids) == 4
    assert get_metric_value(MLDS_DATA_SIZE, backend=store._backend_label) == 4
    assert get_metric_value(MLDS_OPS_TOTAL, operation="add_record", status="success") == 4

@pytest.mark.asyncio
async def test_redis_retry_on_connection_error(redis_store, mocker: MockerFixture):
    """Test retry mechanism on Redis connection error."""
    try:
        import redis.asyncio as redis
    except ImportError:
        pytest.skip("Redis library not installed.")
    
    # Mock hset to fail twice then succeed
    call_count = [0]
    
    async def mock_hset(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] <= 2:
            raise redis.ConnectionError("Failed")
        return 1  # Success on third attempt
    
    redis_store._redis.hset = mock_hset
    redis_store._redis.hlen.return_value = 0
    
    # The retry mechanism should handle the failures
    exp_id = await redis_store.add_record(SAMPLE_RECORD)
    assert exp_id == "test_exp_001"
    assert call_count[0] == 3  # Called 3 times (2 failures + 1 success)

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_context_manager(store_type, inmemory_store, redis_store):
    """Test async context manager."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    
    if store_type == "redis":
        store._redis.hlen.return_value = 0
        store._redis.hset.return_value = 1
        record_obj = MetaLearningRecord(**SAMPLE_RECORD)
        store._redis.hget.return_value = record_obj.json()
    
    async with store:
        await store.add_record(SAMPLE_RECORD)
        record = await store.get_record("test_exp_001")
        assert record.experiment_id == "test_exp_001"
    
    if store_type == "redis":
        # Check that close was called
        assert store._redis.close.called

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_invalid_tag_validation(store_type, inmemory_store, redis_store):
    """Test validation of tags in MetaLearningRecord."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    invalid_record = SAMPLE_RECORD.copy()
    invalid_record["tags"] = ["valid", "invalid@tag"]  # Invalid character
    
    with pytest.raises((MetaLearningRecordValidationError, MetaLearningDataStoreError)):
        await store.add_record(invalid_record)
    
    assert get_metric_value(MLDS_OPS_TOTAL, operation="add_record", status="failure") >= 1

@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", ["inmemory", "redis"])
async def test_filter_by_tags(store_type, inmemory_store, redis_store):
    """Test filtering records by tags."""
    store = inmemory_store if store_type == "inmemory" else redis_store
    
    if store_type == "redis":
        store._redis.hlen.side_effect = [0, 1]  # For two add operations
        store._redis.hset.return_value = 1
    
    # Add records with different tags
    record1 = SAMPLE_RECORD.copy()
    record1["experiment_id"] = "exp_with_tags_1"
    record1["tags"] = ["test", "baseline"]
    
    record2 = SAMPLE_RECORD.copy()
    record2["experiment_id"] = "exp_with_tags_2"
    record2["tags"] = ["test", "experimental"]
    
    await store.add_record(record1)
    await store.add_record(record2)
    
    if store_type == "redis":
        # Mock hgetall to return both records
        r1 = MetaLearningRecord(**record1)
        r2 = MetaLearningRecord(**record2)
        store._redis.hgetall.return_value = {
            "exp_with_tags_1": r1.json(),
            "exp_with_tags_2": r2.json()
        }
    
    # Filter by single tag
    records = await store.list_records(filter_by={"tags": ["baseline"]})
    assert len(records) == 1
    assert records[0].experiment_id == "exp_with_tags_1"
    
    # Filter by multiple tags
    records = await store.list_records(filter_by={"tags": ["test"]})
    assert len(records) == 2  # Both have "test" tag