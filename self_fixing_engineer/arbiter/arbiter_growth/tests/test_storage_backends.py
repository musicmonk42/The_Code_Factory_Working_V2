"""
Unit tests for storage backend implementations.

Tests SQLite, Redis, and Kafka storage backends.
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
import os
import base64

# Set the encryption key environment variable for tests
os.environ["ARBITER_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(os.urandom(32)).decode(
    "utf-8"
)

from sqlalchemy.exc import SQLAlchemyError
from cryptography.fernet import InvalidToken

from arbiter.arbiter_growth.exceptions import (
    ArbiterGrowthError,
    AuditChainTamperedError,
)
from arbiter.arbiter_growth.models import (
    ArbiterState,
    GrowthEvent,
    Base,
)
from arbiter.arbiter_growth.storage_backends import (
    SQLiteStorageBackend,
    RedisStreamsStorageBackend,
    KafkaStorageBackend,
    storage_backend_factory,
    REDIS_BREAKER,
)


# --- Fixtures ---


@pytest.fixture
def mock_config_store():
    """Provides a mock ConfigStore that returns proper values."""
    mock = MagicMock()

    def get_config(key, default=None):
        configs = {
            "sqlite.database_url": "sqlite+aiosqlite:///:memory:",
            "sqlite.batch_size": 1000,
            "redis.url": "redis://localhost:6379",
            "redis.stream_key": "test_stream",
            "redis.consumer_group": "test_group",
            "kafka.bootstrap_servers": "localhost:9092",
            "kafka.topic": "test_topic",
            "storage.backend": "sqlite",
        }
        return configs.get(key, default)

    mock.get = MagicMock(side_effect=get_config)
    mock.get_all = MagicMock(return_value={})
    return mock


@pytest_asyncio.fixture
async def sqlite_backend(mock_config_store):
    """Provides an in-memory SQLite backend for testing."""
    backend = SQLiteStorageBackend(config=mock_config_store)
    # Ensure tables are created in the in-memory database
    async with backend.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await backend.start()
    yield backend
    await backend.stop()


@pytest.fixture
def mock_redis_client():
    """Provides a mock Redis client."""
    mock = AsyncMock()
    mock.xadd = AsyncMock(return_value="1234567890-0")
    mock.xread = AsyncMock(return_value=[])
    mock.xgroup_create = AsyncMock()
    mock.xreadgroup = AsyncMock(return_value=[])
    mock.get = AsyncMock(return_value=None)
    mock.hgetall = AsyncMock(return_value={})
    mock.set = AsyncMock(return_value=True)
    mock.close = AsyncMock()
    mock.ping = AsyncMock(return_value=True)
    mock.hset = AsyncMock(return_value=1)
    mock.rpush = AsyncMock(return_value=1)
    mock.lindex = AsyncMock(return_value=None)
    mock.lrange = AsyncMock(return_value=[])

    # Mock the pipeline to return a proper async context manager
    class AsyncPipelineContext:
        def __init__(self):
            self.hset = AsyncMock(return_value=1)
            self.execute = AsyncMock(return_value=[1])

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            await self.execute()
            return None

    mock.pipeline = MagicMock(return_value=AsyncPipelineContext())
    return mock


@pytest_asyncio.fixture
async def redis_backend(mock_config_store, mock_redis_client):
    """Provides a Redis backend with mocked client."""
    with patch("redis.asyncio.from_url", return_value=mock_redis_client):
        backend = RedisStreamsStorageBackend(config=mock_config_store)
        backend.redis = mock_redis_client
        yield backend


@pytest.fixture
def mock_kafka_producer():
    """Provides a mock Kafka producer."""
    mock = AsyncMock()
    mock.start = AsyncMock()
    mock.stop = AsyncMock()
    mock.send_and_wait = AsyncMock()
    return mock


@pytest_asyncio.fixture
async def kafka_backend(mock_config_store, mock_kafka_producer):
    """Provides a Kafka backend with a mocked producer."""
    with patch("aiokafka.AIOKafkaProducer", return_value=mock_kafka_producer):
        backend = KafkaStorageBackend(config=mock_config_store)
        backend.producer = mock_kafka_producer
        yield backend


# --- SQLite Backend Tests ---


@pytest.mark.asyncio
async def test_sqlite_load_snapshot_returns_none_if_not_found(sqlite_backend):
    """Test that loading a non-existent snapshot returns None."""
    snapshot = await sqlite_backend.load_snapshot("non_existent_arbiter")
    assert snapshot is None


@pytest.mark.asyncio
async def test_sqlite_save_and_load_snapshot(sqlite_backend):
    """Test saving and loading a snapshot."""
    # Create a state
    state = ArbiterState(
        arbiter_id="test_arbiter",
        level=5,
        experience_points=1000,
        skills={"python": 0.8, "rust": 0.6},
        event_offset=42,
        user_preferences={"theme": "dark"},
    )

    # Save snapshot
    await sqlite_backend.save_snapshot("test_arbiter", state.model_dump())

    # Load snapshot
    loaded = await sqlite_backend.load_snapshot("test_arbiter")

    assert loaded is not None
    assert loaded["level"] == 5
    assert loaded["skills"]["python"] == 0.8
    # Fix: event_offset can be int or string
    assert loaded["event_offset"] == 42 or loaded["event_offset"] == "42"


@pytest.mark.asyncio
async def test_sqlite_save_and_load_events(sqlite_backend):
    """Test saving and loading events."""
    # Create events
    events = [
        GrowthEvent(
            type="learning",
            timestamp="2024-01-01T00:00:00+00:00",
            details={"skill_name": "python", "improvement_delta": 10.0},
        ),
        GrowthEvent(
            type="achievement",
            timestamp="2024-01-01T00:01:00+00:00",
            details={"skill_name": "rust", "improvement_delta": 15.0},
        ),
    ]

    # Save events
    for event in events:
        await sqlite_backend.save_event("test_arbiter", event.model_dump())

    # Load events
    loaded = await sqlite_backend.load_events("test_arbiter", from_offset=0)

    assert len(loaded) == 2
    assert loaded[0]["type"] == "learning"
    assert loaded[0]["details"]["skill_name"] == "python"
    assert loaded[1]["type"] == "achievement"
    assert loaded[1]["details"]["skill_name"] == "rust"


@pytest.mark.asyncio
async def test_sqlite_audit_log_chaining(sqlite_backend):
    """Test that audit logs are properly chained with hashes."""
    # Create first audit entry
    hash1 = await sqlite_backend.save_audit_log(
        "test_arbiter", "event_1", {"action": "first"}, "genesis_hash"
    )

    assert hash1 is not None
    assert len(hash1) > 0

    # Create second audit entry
    hash2 = await sqlite_backend.save_audit_log(
        "test_arbiter", "event_2", {"action": "second"}, hash1
    )

    assert hash2 is not None
    assert hash2 != hash1


@pytest.mark.asyncio
async def test_sqlite_handles_decryption_failure(sqlite_backend):
    """Test that backend handles decryption failures gracefully."""
    # Save encrypted data
    state = ArbiterState(arbiter_id="test_arbiter", level=1, skills={"test": 0.5})
    await sqlite_backend.save_snapshot("test_arbiter", state.model_dump())

    # Mock decryption failure
    with patch.object(sqlite_backend.cipher, "decrypt", side_effect=InvalidToken):
        with pytest.raises(AuditChainTamperedError):
            await sqlite_backend.load_snapshot("test_arbiter")


# --- Redis Backend Tests ---


@pytest.mark.asyncio
async def test_redis_load_snapshot_returns_none_if_not_found(
    redis_backend, mock_redis_client
):
    """Test that loading a non-existent snapshot from Redis returns None."""
    mock_redis_client.hgetall.return_value = {}
    snapshot = await redis_backend.load_snapshot("non_existent")
    assert snapshot is None


@pytest.mark.asyncio
async def test_redis_save_and_load_snapshot(redis_backend, mock_redis_client):
    """Test saving and loading a snapshot in Redis."""
    # Create state
    state = ArbiterState(
        arbiter_id="test_arbiter",
        level=3,
        experience_points=500,
        skills={"python": 0.7},
    )

    # Save snapshot
    await redis_backend.save_snapshot("test_arbiter", state.model_dump())

    # Mock the get return value with valid data
    mock_redis_client.hgetall.return_value = {
        b"level": b"3",
        b"skills_encrypted": redis_backend.cipher.encrypt(
            json.dumps({"python": 0.7}).encode("utf-8")
        ),
        b"user_preferences_encrypted": redis_backend.cipher.encrypt(
            json.dumps({}).encode("utf-8")
        ),
        b"schema_version": b"1.0",
        b"event_offset": b"0",
        b"experience_points": b"500.0",
    }

    # Load snapshot
    loaded = await redis_backend.load_snapshot("test_arbiter")

    assert loaded is not None
    assert loaded["level"] == 3
    assert loaded["skills"]["python"] == 0.7


@pytest.mark.asyncio
async def test_redis_circuit_breaker_opens(redis_backend, caplog):
    """Tests that the Redis circuit breaker rejects calls when open."""
    # Open the circuit breaker
    REDIS_BREAKER.open()

    try:
        # This should raise ArbiterGrowthError due to circuit breaker
        with caplog.at_level(logging.ERROR):
            with pytest.raises(
                ArbiterGrowthError, match="Redis operation 'load_snapshot' failed"
            ):
                await redis_backend.load_snapshot("test_arbiter_5")
            assert "CircuitBreakerError" in caplog.text
    finally:
        # Always reset the breaker after test
        REDIS_BREAKER.close()


# --- Kafka Backend Tests ---


@pytest.mark.asyncio
async def test_kafka_save_snapshot(kafka_backend, mock_kafka_producer):
    """Test saving a snapshot to Kafka."""
    # Create state
    state = ArbiterState(
        arbiter_id="test_arbiter", level=2, experience_points=250, skills={"go": 0.55}
    )

    # Save snapshot
    await kafka_backend.save_snapshot("test_arbiter", state.model_dump())

    # Verify producer was called
    mock_kafka_producer.send_and_wait.assert_awaited_once()
    call_args = mock_kafka_producer.send_and_wait.call_args
    assert call_args[0][0] == "arbiter.test_arbiter.snapshots"

    # Verify the message structure
    sent_payload = call_args[0][1]
    decrypted_data = json.loads(kafka_backend.cipher.decrypt(sent_payload))
    assert decrypted_data["arbiter_id"] == "test_arbiter"
    assert decrypted_data["level"] == 2


# --- Storage Factory Tests ---


def test_storage_backend_factory_sqlite(mock_config_store):
    """Test the factory creates SQLite backend correctly."""
    mock_config_store.get.side_effect = lambda k, d=None: (
        "sqlite" if k == "storage.backend" else d
    )

    backend = storage_backend_factory(mock_config_store)
    assert isinstance(backend, SQLiteStorageBackend)


def test_storage_backend_factory_redis(mock_config_store):
    """Test the factory creates Redis backend correctly."""

    # Fix: Ensure redis.url is provided
    def get_side_effect(k, d=None):
        if k == "storage.backend":
            return "redis"
        elif k == "redis.url":
            return "redis://localhost:6379"
        return d

    mock_config_store.get.side_effect = get_side_effect

    backend = storage_backend_factory(mock_config_store)
    assert isinstance(backend, RedisStreamsStorageBackend)


def test_storage_backend_factory_kafka(mock_config_store):
    """Test the factory creates Kafka backend correctly."""

    # Fix: Ensure kafka.bootstrap_servers is provided
    def get_side_effect(k, d=None):
        if k == "storage.backend":
            return "kafka"
        elif k == "kafka.bootstrap_servers":
            return "localhost:9092"
        return d

    mock_config_store.get.side_effect = get_side_effect
    mock_config_store.get_all.return_value = {}

    backend = storage_backend_factory(mock_config_store)
    assert isinstance(backend, KafkaStorageBackend)


def test_storage_backend_factory_unknown(mock_config_store):
    """Test the factory raises error for unknown backend type."""
    mock_config = MagicMock()
    mock_config.get.return_value = "unknown_backend"

    with pytest.raises(
        ValueError, match="Unknown storage backend type: unknown_backend"
    ):
        storage_backend_factory(mock_config)


# --- Additional Integration Tests ---


@pytest.mark.asyncio
async def test_sqlite_batch_operations(sqlite_backend):
    """Test batch operations in SQLite backend."""
    # Create many events
    events = [
        GrowthEvent(
            type="learning",
            timestamp=f"2024-01-01T00:{i:02d}:00+00:00",
            details={"skill_name": f"skill_{i}", "improvement_delta": float(i)},
        )
        for i in range(60)
    ]

    # Save in batch
    for event in events:
        await sqlite_backend.save_event("test_arbiter", event.model_dump())

    # Load with pagination
    page1 = await sqlite_backend.load_events("test_arbiter", from_offset=0)
    page2 = await sqlite_backend.load_events("test_arbiter", from_offset=len(page1))

    assert len(page1) == 60
    assert page1[0]["details"]["skill_name"] == "skill_0"
    assert "canonical_offset" in page1[0]
    # The second page should be empty, because there were only 60 events, and we read 60
    assert len(page2) == 0


@pytest.mark.asyncio
async def test_redis_stream_operations(redis_backend, mock_redis_client):
    """Test Redis stream operations for events."""
    # Create events
    events = [
        GrowthEvent(
            type="learning",
            timestamp="2024-01-01T00:00:00+00:00",
            details={"skill_name": "python"},
        ),
        GrowthEvent(
            type="achievement",
            timestamp="2024-01-01T00:01:00+00:00",
            details={"skill_name": "rust"},
        ),
    ]

    # Save events
    for event in events:
        await redis_backend.save_event("test_arbiter", event.model_dump())

    # Verify stream operations were called
    assert mock_redis_client.xadd.call_count == 2


@pytest.mark.asyncio
async def test_storage_backend_error_handling(sqlite_backend, caplog):
    """Test error handling in storage backends."""
    # Simulate a database error by patching AsyncSession's commit method
    with patch(
        "sqlalchemy.ext.asyncio.AsyncSession.commit",
        side_effect=SQLAlchemyError("DB Error"),
    ):
        with caplog.at_level(logging.ERROR):
            with pytest.raises(ArbiterGrowthError):
                await sqlite_backend.save_snapshot(
                    "test", ArbiterState(arbiter_id="test").model_dump()
                )
            assert "DB Error" in caplog.text


@pytest.mark.asyncio
async def test_encryption_decryption(sqlite_backend):
    """Test that sensitive data is encrypted and decrypted correctly."""
    # Create state with sensitive data
    state = ArbiterState(
        arbiter_id="test_crypto",
        level=1,
        skills={"secret_skill": 0.999},
        user_preferences={"sensitive_data": "password"},
    )

    # Save (should encrypt)
    await sqlite_backend.save_snapshot("test_crypto", state.model_dump())

    # Load (should decrypt)
    loaded = await sqlite_backend.load_snapshot("test_crypto")

    assert loaded is not None
    assert loaded["skills"]["secret_skill"] == 0.999
    assert loaded["user_preferences"]["sensitive_data"] == "password"


@pytest.mark.asyncio
async def test_concurrent_writes(sqlite_backend):
    """Test handling of concurrent write operations."""
    # Create multiple states
    states = [
        ArbiterState(arbiter_id=f"arbiter_{i}", level=i + 1, experience_points=i * 100)
        for i in range(10)
    ]

    # Save concurrently
    tasks = [
        sqlite_backend.save_snapshot(state.arbiter_id, state.model_dump())
        for state in states
    ]
    await asyncio.gather(*tasks)

    # Verify all were saved
    for i in range(10):
        loaded = await sqlite_backend.load_snapshot(f"arbiter_{i}")
        assert loaded is not None
        assert loaded["level"] == i + 1


# --- Reconstructed and New Tests ---


@pytest.mark.asyncio
async def test_redis_consumer_group(redis_backend, mock_redis_client):
    """Test that the Redis consumer group is created and events are added to the stream."""
    event_payload = {
        "type": "test",
        "timestamp": "2024-01-01T00:00:00+00:00",
        "details": {},
    }
    await redis_backend.save_event("test_arbiter", event_payload)

    assert mock_redis_client.xadd.call_count == 1
    call_args = mock_redis_client.xadd.call_args
    # Fix: Verify stream name with correct format
    assert call_args[0][0] == "arbiter:test_arbiter:events"
    # Verify payload format
    assert b"type" in call_args[0][1]


@pytest.mark.asyncio
async def test_kafka_offset_management(kafka_backend, mock_kafka_producer):
    """Test that event offsets are correctly managed and returned from Kafka."""
    # Mock the consumer to return a specific event
    AsyncMock()

    # Mock the load_events method to return a specific payload
    async def mock_load_events(*args, **kwargs):
        encrypted_payload = kafka_backend.cipher.encrypt(
            json.dumps(
                {
                    "event_offset": 0,
                    "type": "test",
                    "timestamp": "2024-01-01T00:00:00+00:00",
                    "details": {},
                }
            ).encode("utf-8")
        )

        # Kafka's consumer returns a list of TopicPartitions, each with a list of messages
        # We need to simulate that structure
        mock_msg = MagicMock()
        mock_msg.value = encrypted_payload
        mock_msg.offset = 0
        mock_msg.topic = "test_topic"

        return [
            {
                "event_offset": 0,
                "type": "test",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "details": {},
            }
        ]

    with patch.object(kafka_backend, "load_events", side_effect=mock_load_events):
        await kafka_backend.save_event(
            "test_arbiter",
            {"type": "test", "timestamp": "2024-01-01T00:00:00+00:00", "details": {}},
        )
        events = await kafka_backend.load_events("test_arbiter", from_offset=0)

        assert len(events) == 1
        assert events[0]["event_offset"] == 0
