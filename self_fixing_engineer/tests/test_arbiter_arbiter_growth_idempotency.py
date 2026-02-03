import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

# Assuming all modules are in a discoverable path
from self_fixing_engineer.arbiter.arbiter_growth.idempotency import (
    IDEMPOTENCY_HITS_TOTAL,
    IdempotencyStore,
    IdempotencyStoreError,
)
from opentelemetry import trace  # Added for the tracer fixture

# FIX: Added imports to set up a real OpenTelemetry context for tests
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from redis.asyncio import Redis
from redis.exceptions import RedisError

# --- Fixtures ---


# This is the original, correct fixture.
# It will work once conftest.py is fixed.
@pytest.fixture(scope="session")
def tracer():
    """
    Provides a REAL OpenTelemetry tracer for tests.
    This sets up a minimal provider to ensure trace.get_current_span() works.
    """
    # Set up a simple provider and processor to make tracing work
    provider = TracerProvider()
    processor = SimpleSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)

    # Set this as the global provider for the test session
    trace.set_tracer_provider(provider)

    return trace.get_tracer("test.idempotency")


@pytest_asyncio.fixture
async def mock_redis():
    """A fixture that provides a direct mock of the Redis client instance."""
    mock = AsyncMock(spec=Redis)
    mock.ping = AsyncMock(return_value=True)
    mock.set = AsyncMock(return_value=True)  # Default for a cache miss
    mock.close = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def set_env_redis_url(monkeypatch):
    """A fixture to set the REDIS_URL environment variable for tests."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    yield
    monkeypatch.delenv("REDIS_URL", raising=False)


@pytest_asyncio.fixture
async def idempotency_store(mock_redis, set_env_redis_url):
    """Provides a configured IdempotencyStore instance with an injected mock redis client."""
    # Reset metrics before each test run to ensure isolation
    if hasattr(IDEMPOTENCY_HITS_TOTAL, "_metrics"):
        IDEMPOTENCY_HITS_TOTAL._metrics.clear()

    store = IdempotencyStore(arbiter_name="default")
    # Directly inject the mock redis client, bypassing the real connection logic in start()
    # This ensures self.redis is not None, fixing teardown and attribute errors.
    store.redis = mock_redis

    yield store

    # The stop() method will now work because store.redis is not None.
    await store.stop()


# --- Unit Tests ---


def test_init_no_redis_url(monkeypatch):
    """Tests that initialization fails if no Redis URL is provided."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(IdempotencyStoreError, match="Redis URL must be provided"):
        IdempotencyStore()


def test_init_with_custom_params(set_env_redis_url):
    """Tests that the store can be initialized with custom parameters."""
    store = IdempotencyStore(
        redis_url="rediss://secure:6379",
        namespace="custom:ns",
        default_ttl=7200,
        arbiter_name="custom_arbiter",
    )
    assert store.namespace == "custom:ns"
    assert store.default_ttl == 7200
    assert store.arbiter_name == "custom_arbiter"


@pytest.mark.asyncio
async def test_check_and_set_miss(idempotency_store, mock_redis, tracer):
    """Tests the behavior of check_and_set on a cache miss (new key)."""
    mock_redis.set.return_value = True

    with tracer.start_as_current_span("test-span"):
        result = await idempotency_store.check_and_set("new_key")

    assert result is True
    mock_redis.set.assert_awaited_with(
        "app:idempotency:new_key", "processed", nx=True, ex=3600
    )

    assert (
        IDEMPOTENCY_HITS_TOTAL.labels(arbiter="default", hit="false")._value.get() == 1
    )


@pytest.mark.asyncio
async def test_check_and_set_hit(idempotency_store, mock_redis, tracer):
    """Tests the behavior of check_and_set on a cache hit (existing key)."""
    mock_redis.set.return_value = False

    with tracer.start_as_current_span("test-span"):
        result = await idempotency_store.check_and_set("existing_key")

    assert result is False
    assert (
        IDEMPOTENCY_HITS_TOTAL.labels(arbiter="default", hit="true")._value.get() == 1
    )


@pytest.mark.asyncio
async def test_check_and_set_redis_error(idempotency_store, mock_redis, tracer):
    """Tests that a specific error is raised when Redis fails."""
    mock_redis.set.side_effect = RedisError("Connection failed")

    with tracer.start_as_current_span("test-span"):
        with pytest.raises(
            IdempotencyStoreError, match="Failed to check/set idempotency key"
        ):
            await idempotency_store.check_and_set("error_key")


@pytest.mark.asyncio
async def test_check_and_set_empty_key(idempotency_store):
    """Tests that empty keys are rejected."""
    with pytest.raises(ValueError, match="Idempotency key cannot be empty"):
        await idempotency_store.check_and_set("")


@pytest.mark.asyncio
async def test_check_and_set_no_redis(set_env_redis_url):
    """Tests that check_and_set fails if redis client is not initialized."""
    store = IdempotencyStore(arbiter_name="test")
    # Don't set store.redis, simulating uninitialized state

    with pytest.raises(IdempotencyStoreError, match="IdempotencyStore is not started"):
        await store.check_and_set("test_key")


@pytest.mark.asyncio
async def test_start_success(set_env_redis_url, caplog):
    """Tests a successful connection start."""
    store = IdempotencyStore(arbiter_name="test")
    mock_redis_client = AsyncMock(spec=Redis)
    mock_redis_client.ping = AsyncMock(return_value=True)

    with patch(
        "self_fixing_engineer.arbiter.arbiter_growth.idempotency.redis.from_url",
        return_value=mock_redis_client,
    ):
        with patch("redis.asyncio.from_url", return_value=mock_redis_client):
            with caplog.at_level(logging.INFO):
                await store.start()
                mock_redis_client.ping.assert_awaited_once()
                assert "Successfully connected to IdempotencyStore Redis" in caplog.text
                assert store.redis is not None


@pytest.mark.asyncio
async def test_start_idempotent(set_env_redis_url):
    """Tests that start() is idempotent."""
    store = IdempotencyStore(arbiter_name="test")
    mock_redis_client = AsyncMock(spec=Redis)
    mock_redis_client.ping = AsyncMock(return_value=True)

    with patch(
        "self_fixing_engineer.arbiter.arbiter_growth.idempotency.redis.from_url",
        return_value=mock_redis_client,
    ) as mock_from_url:
        await store.start()
        await store.start()  # Second call should do nothing

        # from_url should only be called once
        mock_from_url.assert_called_once()


@pytest.mark.asyncio
async def test_start_retry_logic(set_env_redis_url):
    """Tests that start() retries on transient failures and eventually succeeds."""
    store = IdempotencyStore(arbiter_name="test")
    mock_redis_client = AsyncMock(spec=Redis)

    # Create a counter to track calls
    call_count = 0

    async def ping_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise RedisError(f"Fail {call_count}")
        return True  # Success on third attempt

    mock_redis_client.ping = AsyncMock(side_effect=ping_side_effect)

    with patch(
        "self_fixing_engineer.arbiter.arbiter_growth.idempotency.redis.from_url",
        return_value=mock_redis_client,
    ):
        await store.start()
        assert mock_redis_client.ping.await_count == 3
        assert store.redis is not None


@pytest.mark.asyncio
async def test_start_fails_after_max_retries(set_env_redis_url, caplog):
    """Tests that start() fails after all retry attempts are exhausted."""
    store = IdempotencyStore(arbiter_name="test")
    mock_redis_client = AsyncMock(spec=Redis)

    # Always fail
    mock_redis_client.ping = AsyncMock(side_effect=RedisError("Persistent failure"))

    with patch(
        "self_fixing_engineer.arbiter.arbiter_growth.idempotency.redis.from_url",
        return_value=mock_redis_client,
    ):
        with pytest.raises(IdempotencyStoreError, match="Failed to connect to Redis"):
            await store.start()

        # Should have attempted 5 times (based on retry configuration)
        assert mock_redis_client.ping.await_count == 5
        assert "Failed to connect to IdempotencyStore Redis" in caplog.text
        assert store.redis is None  # Should be reset on failure


@pytest.mark.asyncio
async def test_stop_success(idempotency_store, mock_redis):
    """Tests successful connection close."""
    await idempotency_store.stop()
    mock_redis.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_handles_error_gracefully(idempotency_store, mock_redis, caplog):
    """Tests that stop() logs an error but does not raise an exception on failure."""
    mock_redis.close.side_effect = RedisError("Shutdown failed")
    with caplog.at_level(logging.WARNING):
        # FIX: Changed 'store.stop()' back to 'idempotency_store.stop()'
        await idempotency_store.stop()
        assert "An error occurred while closing the Redis connection" in caplog.text


@pytest.mark.asyncio
async def test_stop_when_not_started(set_env_redis_url):
    """Tests that stop() handles being called when redis is not initialized."""
    store = IdempotencyStore(arbiter_name="test")
    await store.stop()


@pytest.mark.asyncio
async def test_concurrent_check_and_set(idempotency_store, mock_redis, tracer):
    """Tests that concurrent operations are handled correctly."""
    # Simulate the first call succeeding and subsequent calls failing
    mock_redis.set.side_effect = [True] + [False] * 49

    async def check_key():
        with tracer.start_as_current_span("test-span-concurrent"):
            return await idempotency_store.check_and_set("concurrent_key")

    tasks = [check_key() for _ in range(50)]
    results = await asyncio.gather(*tasks)

    # Only one call should succeed (return True)
    assert results.count(True) == 1
    assert results.count(False) == 49

    # Verify metrics reflect the outcome
    assert (
        IDEMPOTENCY_HITS_TOTAL.labels(arbiter="default", hit="false")._value.get() == 1
    )
    assert (
        IDEMPOTENCY_HITS_TOTAL.labels(arbiter="default", hit="true")._value.get() == 49
    )
    assert mock_redis.set.call_count == 50


@pytest.mark.asyncio
async def test_check_and_set_with_custom_ttl(idempotency_store, mock_redis, tracer):
    """Tests that custom TTL is used when provided."""
    mock_redis.set.return_value = True
    custom_ttl = 7200

    with tracer.start_as_current_span("test-span"):
        await idempotency_store.check_and_set("custom_ttl_key", ttl=custom_ttl)
    mock_redis.set.assert_awaited_with(
        "app:idempotency:custom_ttl_key", "processed", nx=True, ex=custom_ttl
    )


@pytest.mark.asyncio
async def test_cluster_mode_initialization(set_env_redis_url):
    """Tests that cluster mode uses RedisCluster."""
    store = IdempotencyStore(arbiter_name="test", cluster_mode=True)
    mock_cluster = AsyncMock()
    mock_cluster.ping = AsyncMock(return_value=True)

    with patch(
        "self_fixing_engineer.arbiter.arbiter_growth.idempotency.RedisCluster.from_url",
        return_value=mock_cluster,
    ) as mock_from_url:
        await store.start()
        mock_from_url.assert_called_once()
        assert store.redis is mock_cluster
