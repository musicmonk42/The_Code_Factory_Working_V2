import asyncio
import json
import logging
import os
import time
import pytest
import pytest_asyncio
from pytest_mock import MockerFixture
from datetime import datetime
from typing import Dict, Any, Optional
from unittest.mock import MagicMock, AsyncMock, patch
from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# Import the centralized tracer configuration
from arbiter.otel_config import get_tracer

# Import the client and its exceptions
from arbiter.models.redis_client import (
    RedisClient, ConnectionError, TimeoutError, DataError, RedisError, LockError
)

# Import metrics from redis_client
from arbiter.models.redis_client import (
    REDIS_CALLS_TOTAL, REDIS_CALLS_ERRORS, REDIS_CALL_LATENCY_SECONDS,
    REDIS_CONNECTIONS_CURRENT, REDIS_LOCK_ACQUIRED_TOTAL,
    REDIS_LOCK_RELEASED_TOTAL, REDIS_LOCK_FAILED_TOTAL,
    REDIS_MEMORY_USAGE, REDIS_KEYSPACE_SIZE
)

# Configure logging for tests
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Get tracer using centralized configuration
tracer = get_tracer("test-redis-client")

# Setup in-memory exporter for testing
in_memory_exporter = InMemorySpanExporter()

# Sample environment variables for tests
SAMPLE_ENV = {
    "REDIS_URL": "redis://localhost:6379/0",
    "REDIS_USE_SSL": "false",
    "LOG_LEVEL": "DEBUG",
    "SFE_OTEL_EXPORTER_TYPE": "console",
    "REDIS_HEALTH_CHECK_INTERVAL": "60",
    "REDIS_MAX_CONNECTIONS": "50",
    "METRICS_PORT": "0",
    "ENV": "dev"
}

SAMPLE_KEY = "test_key"
SAMPLE_VALUE = "test_value"
SAMPLE_JSON_VALUE = {"data": "test", "nested": {"value": 123}}
SAMPLE_LOCK_NAME = "test_lock"

@pytest_asyncio.fixture(autouse=True)
async def setup_env(mocker: MockerFixture):
    """Set up environment variables and clean up after tests."""
    for key, value in SAMPLE_ENV.items():
        mocker.patch.dict(os.environ, {key: value})
    yield
    for key in SAMPLE_ENV:
        os.environ.pop(key, None)

@pytest_asyncio.fixture
async def redis_client(mocker: MockerFixture):
    """Fixture for RedisClient with mocked aioredis dependencies."""
    import redis.asyncio as aioredis
    
    # Mock aioredis client
    mock_client = mocker.MagicMock(spec=aioredis.Redis)
    mock_client.ping = mocker.AsyncMock(return_value=True)
    mock_client.set = mocker.AsyncMock(return_value=True)
    mock_client.get = mocker.AsyncMock(return_value=json.dumps(SAMPLE_VALUE))
    mock_client.mset = mocker.AsyncMock(return_value=True)
    mock_client.mget = mocker.AsyncMock(return_value=[json.dumps({"num": i}) for i in range(3)])
    mock_client.delete = mocker.AsyncMock(return_value=1)
    mock_client.close = mocker.AsyncMock()
    mock_client.info = mocker.AsyncMock(return_value={"used_memory": 1048576})
    mock_client.dbsize = mocker.AsyncMock(return_value=100)
    
    # Mock lock
    mock_lock = mocker.MagicMock()
    mock_lock.acquire = mocker.AsyncMock(return_value=True)
    mock_lock.release = mocker.AsyncMock()
    mock_lock.__aenter__ = mocker.AsyncMock(return_value=mock_lock)
    mock_lock.__aexit__ = mocker.AsyncMock()
    
    mocker.patch("redis.asyncio.Lock", return_value=mock_lock)
    mocker.patch("redis.asyncio.from_url", return_value=mock_client)
    
    client = RedisClient()
    client._mock_client = mock_client  # Store for test access
    client._mock_lock = mock_lock
    yield client
    
    # Cleanup
    if client.client:
        await client.disconnect()

@pytest_asyncio.fixture(autouse=True)
async def clear_metrics_and_traces():
    """Clear Prometheus metrics and OpenTelemetry traces before each test."""
    in_memory_exporter.clear()
    yield

# Initialization Tests
class TestInitialization:
    """Test RedisClient initialization."""
    
    def test_initialization_with_defaults(self, mocker: MockerFixture):
        """Test initialization with default values from environment."""
        mocker.patch.dict(os.environ, SAMPLE_ENV)
        client = RedisClient()
        assert client.redis_url == SAMPLE_ENV["REDIS_URL"]
        assert client.use_ssl == False
        assert client.client is None
    
    def test_initialization_with_custom_url(self, mocker: MockerFixture):
        """Test initialization with custom Redis URL."""
        mocker.patch.dict(os.environ, SAMPLE_ENV)
        custom_url = "redis://custom:6380/1"
        client = RedisClient(redis_url=custom_url)
        assert client.redis_url == custom_url
    
    def test_initialization_with_ssl_url(self, mocker: MockerFixture):
        """Test initialization with SSL URL."""
        mocker.patch.dict(os.environ, SAMPLE_ENV)
        ssl_url = "rediss://secure:6380/0"
        client = RedisClient(redis_url=ssl_url)
        assert client.use_ssl == True
    
    def test_initialization_with_ssl_env(self, mocker: MockerFixture):
        """Test SSL detection from environment variable."""
        env = SAMPLE_ENV.copy()
        env["REDIS_USE_SSL"] = "true"
        mocker.patch.dict(os.environ, env)
        client = RedisClient()
        assert client.use_ssl == True
    
    def test_initialization_invalid_url(self, mocker: MockerFixture):
        """Test initialization with invalid URL."""
        mocker.patch.dict(os.environ, SAMPLE_ENV)
        with pytest.raises(ValueError, match="Invalid Redis URL"):
            RedisClient(redis_url="invalid://url")
    
    def test_initialization_prod_requires_ssl(self, mocker: MockerFixture):
        """Test that production environment requires SSL."""
        env = SAMPLE_ENV.copy()
        env["ENV"] = "prod"
        env["REDIS_USE_SSL"] = "false"
        mocker.patch.dict(os.environ, env)
        with pytest.raises(ValueError, match="SSL is required in production"):
            RedisClient()

# Connection Tests
class TestConnection:
    """Test connection management."""
    
    @pytest.mark.asyncio
    async def test_connect_success(self, redis_client):
        """Test successful connection to Redis."""
        await redis_client.connect()
        assert redis_client.client is not None
        redis_client._mock_client.ping.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_connect_idempotent(self, redis_client, caplog):
        """Test that connect is idempotent."""
        caplog.set_level(logging.INFO)
        await redis_client.connect()
        await redis_client.connect()
        assert "Redis client already connected" in caplog.text
        assert redis_client._mock_client.ping.call_count == 1
    
    @pytest.mark.asyncio
    async def test_connect_failure(self, redis_client, mocker: MockerFixture):
        """Test connection failure handling."""
        redis_client._mock_client.ping.side_effect = ConnectionError("Connection failed")
        with pytest.raises(ConnectionError, match="Failed to connect to Redis"):
            await redis_client.connect()
    
    @pytest.mark.asyncio
    async def test_disconnect_success(self, redis_client):
        """Test successful disconnection."""
        await redis_client.connect()
        await redis_client.disconnect()
        assert redis_client.client is None
        redis_client._mock_client.close.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_disconnect_idempotent(self, redis_client, caplog):
        """Test that disconnect is idempotent."""
        caplog.set_level(logging.INFO)
        await redis_client.disconnect()
        assert "Redis client already disconnected" in caplog.text
    
    @pytest.mark.asyncio
    async def test_context_manager(self, redis_client):
        """Test async context manager for automatic connect/disconnect."""
        async with redis_client:
            assert redis_client.client is not None
            redis_client._mock_client.ping.assert_called_once()
        assert redis_client.client is None
        redis_client._mock_client.close.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_ping_success(self, redis_client):
        """Test successful ping operation."""
        await redis_client.connect()
        result = await redis_client.ping()
        assert result == True
    
    @pytest.mark.asyncio
    async def test_ping_when_disconnected(self, redis_client):
        """Test ping returns False when disconnected."""
        result = await redis_client.ping()
        assert result == False
    
    @pytest.mark.asyncio
    async def test_reconnect(self, redis_client):
        """Test reconnection functionality."""
        await redis_client.connect()
        redis_client._mock_client.ping.return_value = False  # Simulate unhealthy connection
        await redis_client.reconnect()
        assert redis_client._mock_client.close.call_count == 1
        assert redis_client._mock_client.ping.call_count >= 2

# CRUD Operations Tests
class TestCRUDOperations:
    """Test CRUD operations."""
    
    @pytest.mark.asyncio
    async def test_set_success(self, redis_client):
        """Test successful SET operation."""
        await redis_client.connect()
        success = await redis_client.set(SAMPLE_KEY, SAMPLE_VALUE)
        assert success == True
        redis_client._mock_client.set.assert_called_once_with(
            SAMPLE_KEY, SAMPLE_VALUE, ex=None, px=None
        )
    
    @pytest.mark.asyncio
    async def test_set_with_json_value(self, redis_client):
        """Test SET with JSON serialization."""
        await redis_client.connect()
        success = await redis_client.set("json_key", SAMPLE_JSON_VALUE)
        assert success == True
        # Verify JSON serialization
        call_args = redis_client._mock_client.set.call_args
        assert json.loads(call_args[0][1]) == SAMPLE_JSON_VALUE
    
    @pytest.mark.asyncio
    async def test_set_with_expiration(self, redis_client):
        """Test SET with expiration time."""
        await redis_client.connect()
        success = await redis_client.set(SAMPLE_KEY, SAMPLE_VALUE, ex=60)
        assert success == True
        redis_client._mock_client.set.assert_called_with(
            SAMPLE_KEY, SAMPLE_VALUE, ex=60, px=None
        )
    
    @pytest.mark.asyncio
    async def test_set_invalid_key(self, redis_client):
        """Test SET with invalid key."""
        await redis_client.connect()
        with pytest.raises(ValueError, match="Key must be non-empty"):
            await redis_client.set("", SAMPLE_VALUE)
        with pytest.raises(ValueError, match="Key must be non-empty"):
            await redis_client.set("x" * 1025, SAMPLE_VALUE)
    
    @pytest.mark.asyncio
    async def test_set_invalid_expiration(self, redis_client):
        """Test SET with invalid expiration times."""
        await redis_client.connect()
        with pytest.raises(ValueError, match="Expiration time .* must be positive"):
            await redis_client.set(SAMPLE_KEY, SAMPLE_VALUE, ex=0)
        with pytest.raises(ValueError, match="Expiration time .* must be positive"):
            await redis_client.set(SAMPLE_KEY, SAMPLE_VALUE, px=-1)
    
    @pytest.mark.asyncio
    async def test_set_oversized_value(self, redis_client):
        """Test SET with value exceeding size limit."""
        await redis_client.connect()
        large_value = "x" * (1024 * 1024 + 1)
        with pytest.raises(ValueError, match="Value size exceeds 1MB limit"):
            await redis_client.set(SAMPLE_KEY, large_value)
    
    @pytest.mark.asyncio
    async def test_set_non_serializable_value(self, redis_client):
        """Test SET with non-serializable value."""
        await redis_client.connect()
        
        class NonSerializable:
            def __init__(self):
                self.circular_ref = self
        
        with pytest.raises(DataError, match="Value not serializable"):
            await redis_client.set(SAMPLE_KEY, NonSerializable())
    
    @pytest.mark.asyncio
    async def test_get_success(self, redis_client):
        """Test successful GET operation."""
        await redis_client.connect()
        redis_client._mock_client.get.return_value = json.dumps(SAMPLE_VALUE)
        value = await redis_client.get(SAMPLE_KEY)
        assert value == SAMPLE_VALUE
        redis_client._mock_client.get.assert_called_once_with(SAMPLE_KEY)
    
    @pytest.mark.asyncio
    async def test_get_json_value(self, redis_client):
        """Test GET with JSON deserialization."""
        await redis_client.connect()
        redis_client._mock_client.get.return_value = json.dumps(SAMPLE_JSON_VALUE)
        value = await redis_client.get("json_key")
        assert value == SAMPLE_JSON_VALUE
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_key(self, redis_client):
        """Test GET for non-existent key."""
        await redis_client.connect()
        redis_client._mock_client.get.return_value = None
        value = await redis_client.get("nonexistent")
        assert value is None
    
    @pytest.mark.asyncio
    async def test_delete_success(self, redis_client):
        """Test successful DELETE operation."""
        await redis_client.connect()
        redis_client._mock_client.delete.return_value = 2
        deleted = await redis_client.delete(SAMPLE_KEY, "another_key")
        assert deleted == 2
        redis_client._mock_client.delete.assert_called_once_with(SAMPLE_KEY, "another_key")
    
    @pytest.mark.asyncio
    async def test_delete_no_keys(self, redis_client):
        """Test DELETE with no keys."""
        await redis_client.connect()
        deleted = await redis_client.delete()
        assert deleted == 0
        redis_client._mock_client.delete.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_setex_success(self, redis_client):
        """Test successful SETEX operation."""
        await redis_client.connect()
        success = await redis_client.setex("exp_key", 60, "exp_value")
        assert success == True
        redis_client._mock_client.set.assert_called_with(
            "exp_key", "exp_value", ex=60, px=None
        )

# Batch Operations Tests
class TestBatchOperations:
    """Test batch operations."""
    
    @pytest.mark.asyncio
    async def test_mset_success(self, redis_client):
        """Test successful MSET operation."""
        await redis_client.connect()
        mapping = {"key1": "value1", "key2": {"data": 2}}
        success = await redis_client.mset(mapping)
        assert success == True
        # Verify JSON serialization for dict value
        call_args = redis_client._mock_client.mset.call_args[0][0]
        assert "key1" in call_args
        assert json.loads(call_args["key2"]) == {"data": 2}
    
    @pytest.mark.asyncio
    async def test_mset_empty_mapping(self, redis_client):
        """Test MSET with empty mapping."""
        await redis_client.connect()
        success = await redis_client.mset({})
        assert success == True
        redis_client._mock_client.mset.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_mset_invalid_key(self, redis_client):
        """Test MSET with invalid key."""
        await redis_client.connect()
        with pytest.raises(ValueError, match="Key .* must be non-empty"):
            await redis_client.mset({"": "value"})
    
    @pytest.mark.asyncio
    async def test_mget_success(self, redis_client):
        """Test successful MGET operation."""
        await redis_client.connect()
        keys = ["key1", "key2", "key3"]
        values = await redis_client.mget(keys)
        assert len(values) == 3
        redis_client._mock_client.mget.assert_called_once_with(keys)
    
    @pytest.mark.asyncio
    async def test_mget_empty_keys(self, redis_client):
        """Test MGET with empty keys list."""
        await redis_client.connect()
        values = await redis_client.mget([])
        assert values == []
        redis_client._mock_client.mget.assert_not_called()

# Lock Tests
class TestDistributedLocking:
    """Test distributed locking functionality."""
    
    @pytest.mark.asyncio
    async def test_lock_creation(self, redis_client):
        """Test lock creation."""
        await redis_client.connect()
        lock = redis_client.lock(SAMPLE_LOCK_NAME, timeout=10, blocking_timeout=5)
        assert lock is not None
    
    @pytest.mark.asyncio
    async def test_lock_invalid_name(self, redis_client):
        """Test lock with invalid name."""
        await redis_client.connect()
        with pytest.raises(ValueError, match="Lock name must be non-empty"):
            redis_client.lock("")
    
    @pytest.mark.asyncio
    async def test_lock_invalid_timeout(self, redis_client):
        """Test lock with invalid timeout."""
        await redis_client.connect()
        with pytest.raises(ValueError, match="Timeouts must be non-negative"):
            redis_client.lock(SAMPLE_LOCK_NAME, timeout=0)
    
    @pytest.mark.asyncio
    async def test_lock_acquisition_success(self, redis_client):
        """Test successful lock acquisition."""
        await redis_client.connect()
        lock = redis_client.lock(SAMPLE_LOCK_NAME)
        acquired = await lock.acquire()
        assert acquired == True
        await lock.release()
    
    @pytest.mark.asyncio
    async def test_lock_context_manager(self, redis_client):
        """Test lock as async context manager."""
        await redis_client.connect()
        lock = redis_client.lock(SAMPLE_LOCK_NAME)
        async with lock:
            # Lock should be acquired here
            pass
        # Lock should be released here
    
    @pytest.mark.asyncio
    async def test_lock_not_connected(self):
        """Test lock creation when not connected."""
        client = RedisClient()
        with pytest.raises(RuntimeError, match="Redis client not connected"):
            client.lock(SAMPLE_LOCK_NAME)

# Retry and Error Handling Tests
class TestRetryAndErrorHandling:
    """Test retry mechanism and error handling."""
    
    @pytest.mark.asyncio
    async def test_retry_on_connection_error(self, redis_client, mocker: MockerFixture):
        """Test retry on connection error."""
        await redis_client.connect()
        # First call fails, second succeeds after reconnect
        redis_client._mock_client.set.side_effect = [
            ConnectionError("Connection lost"),
            True
        ]
        redis_client._mock_client.ping.return_value = False  # Force reconnect
        
        success = await redis_client.set(SAMPLE_KEY, SAMPLE_VALUE)
        assert success == True
        assert redis_client._mock_client.set.call_count == 2
    
    @pytest.mark.asyncio
    async def test_retry_exhaustion(self, redis_client, mocker: MockerFixture):
        """Test error when retries are exhausted."""
        await redis_client.connect()
        redis_client._mock_client.set.side_effect = ConnectionError("Persistent error")
        redis_client._mock_client.ping.return_value = False
        
        with pytest.raises(ConnectionError):
            await redis_client.set(SAMPLE_KEY, SAMPLE_VALUE)
    
    @pytest.mark.asyncio
    async def test_operation_without_connection(self, redis_client):
        """Test operations fail when not connected."""
        with pytest.raises(RuntimeError, match="Redis client not connected"):
            await redis_client.set(SAMPLE_KEY, SAMPLE_VALUE)

# Health Check and Stats Tests
class TestHealthCheckAndStats:
    """Test health check and statistics functionality."""
    
    @pytest.mark.asyncio
    async def test_health_check_task_creation(self, redis_client):
        """Test health check task is created on connect."""
        await redis_client.connect()
        assert redis_client._health_check_task is not None
        assert not redis_client._health_check_task.done()
    
    @pytest.mark.asyncio
    async def test_health_check_task_cancellation(self, redis_client):
        """Test health check task is cancelled on disconnect."""
        await redis_client.connect()
        task = redis_client._health_check_task
        await redis_client.disconnect()
        assert task.cancelled() or task.done()
    
    @pytest.mark.asyncio
    async def test_update_redis_stats(self, redis_client):
        """Test Redis stats update."""
        await redis_client.connect()
        await redis_client.update_redis_stats()
        redis_client._mock_client.info.assert_called_once_with("memory")
        redis_client._mock_client.dbsize.assert_called_once()

# Security and Validation Tests
class TestSecurityAndValidation:
    """Test security features and input validation."""
    
    @pytest.mark.asyncio
    async def test_key_length_validation(self, redis_client):
        """Test key length validation."""
        await redis_client.connect()
        # Test maximum key length
        long_key = "x" * 1024
        success = await redis_client.set(long_key, "value")
        assert success == True
        
        # Test exceeding maximum
        too_long_key = "x" * 1025
        with pytest.raises(ValueError, match="Key must be non-empty and <= 1024"):
            await redis_client.set(too_long_key, "value")
    
    @pytest.mark.asyncio
    async def test_value_size_validation(self, redis_client):
        """Test value size validation."""
        await redis_client.connect()
        # Test maximum value size (1MB)
        large_value = "x" * (1024 * 1024)
        success = await redis_client.set("key", large_value)
        assert success == True
        
        # Test exceeding maximum
        too_large_value = "x" * (1024 * 1024 + 1)
        with pytest.raises(ValueError, match="Value size exceeds 1MB"):
            await redis_client.set("key", too_large_value)
    
    def test_key_redaction_in_logs(self, caplog):
        """Test that keys are redacted in logs."""
        caplog.set_level(logging.DEBUG)
        from arbiter.models.redis_client import _redact_key
        
        key = "sensitive_key_12345"
        redacted = _redact_key(key)
        assert "sensitive_key_12345" not in redacted
        assert redacted.startswith("hash:")
        assert len(redacted) == 13  # "hash:" + 8 chars

# Integration Tests
class TestIntegration:
    """Integration tests for complete workflows."""
    
    @pytest.mark.asyncio
    async def test_complete_crud_workflow(self, redis_client):
        """Test complete CRUD workflow."""
        async with redis_client:
            # Create
            await redis_client.set("item1", {"name": "Test Item"})
            
            # Read
            item = await redis_client.get("item1")
            assert item == {"name": "Test Item"}
            
            # Update
            await redis_client.set("item1", {"name": "Updated Item"})
            updated = await redis_client.get("item1")
            assert updated == {"name": "Updated Item"}
            
            # Delete
            deleted = await redis_client.delete("item1")
            assert deleted == 1
            
            # Verify deletion
            redis_client._mock_client.get.return_value = None
            result = await redis_client.get("item1")
            assert result is None
    
    @pytest.mark.asyncio
    async def test_batch_operations_workflow(self, redis_client):
        """Test batch operations workflow."""
        async with redis_client:
            # Batch set
            data = {f"batch_{i}": {"value": i} for i in range(5)}
            await redis_client.mset(data)
            
            # Batch get
            keys = list(data.keys())
            values = await redis_client.mget(keys)
            assert len(values) == 5
            
            # Batch delete
            deleted = await redis_client.delete(*keys)
            assert deleted >= 1
    
    @pytest.mark.asyncio
    async def test_expiration_workflow(self, redis_client):
        """Test key expiration workflow."""
        async with redis_client:
            # Set with expiration
            await redis_client.setex("temp_key", 1, "temporary")
            
            # Verify key exists
            redis_client._mock_client.get.return_value = json.dumps("temporary")
            value = await redis_client.get("temp_key")
            assert value == "temporary"
            
            # Simulate expiration
            redis_client._mock_client.get.return_value = None
            value = await redis_client.get("temp_key")
            assert value is None

# Performance Tests
class TestPerformance:
    """Test performance-related features."""
    
    @pytest.mark.asyncio
    async def test_connection_pooling(self, redis_client, mocker: MockerFixture):
        """Test that connection pooling is configured."""
        import redis.asyncio as aioredis
        mock_from_url = mocker.patch("redis.asyncio.from_url")
        
        client = RedisClient()
        await client.connect()
        
        mock_from_url.assert_called_once()
        call_kwargs = mock_from_url.call_args[1]
        assert "max_connections" in call_kwargs
        assert call_kwargs["max_connections"] == 50
    
    @pytest.mark.asyncio
    async def test_concurrent_operations(self, redis_client):
        """Test concurrent operations."""
        await redis_client.connect()
        
        async def set_operation(key: str, value: str):
            return await redis_client.set(key, value)
        
        # Run multiple operations concurrently
        tasks = [
            set_operation(f"concurrent_{i}", f"value_{i}")
            for i in range(10)
        ]
        results = await asyncio.gather(*tasks)
        assert all(results)
        assert redis_client._mock_client.set.call_count == 10