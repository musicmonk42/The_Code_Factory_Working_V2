"""
Unit tests for the ArbiterGrowthManager class.

Tests the core growth tracking and management functionality.
"""

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
import pytest_asyncio

from arbiter.arbiter_growth.exceptions import (
    OperationQueueFullError,
    RateLimitError,
    CircuitBreakerOpenError,
    AuditChainTamperedError,
)
from arbiter.arbiter_growth.arbiter_growth_manager import ArbiterGrowthManager
from arbiter.arbiter_growth.models import GrowthEvent
from arbiter.arbiter_growth.metrics import (
    GROWTH_SAVE_ERRORS,
    GROWTH_ANOMALY_SCORE,
)
from pybreaker import CircuitBreakerListener

# Add HealthStatus enum if not in manager
from enum import Enum


class HealthStatus(Enum):
    """Health status states."""

    INITIALIZING = "initializing"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STOPPED = "stopped"


# --- Fixtures ---


class BreakerListener(CircuitBreakerListener):
    """A proper listener for pybreaker to test state changes."""

    def before_call(self, cb, func, *args, **kwargs):
        """Called before a circuit breaker protected call."""
        pass

    def success(self, cb):
        """Called when a circuit breaker protected call succeeds."""
        pass

    def failure(self, cb, exc):
        """Called when a circuit breaker protected call fails."""
        pass

    def state_change(self, cb, old_state, new_state):
        """Called when the circuit breaker changes state."""
        pass


@pytest.fixture
def mock_config_store():
    """Provides a mock ConfigStore."""
    mock = MagicMock()

    async def get_config_side_effect(key, default=None):
        value = {
            "rate_limit_tokens": 10,
            "rate_limit_refill_rate": 10,
            "rate_limit_timeout": 30.0,
            "global.schema_version": 1.0,
            "arbiter.max_pending_operations": 1000,
            "security.idempotency_salt": "test_salt",
            "arbiter.flush_interval_min": 5,
            "arbiter.flush_interval_max": 60,
            "arbiter.evolution_cycle_interval_seconds": 3600,
            "arbiter.snapshot_interval_events": 100,
            "anomaly_detection.max_skill_improvement": 0.5,
            "anomaly_threshold": 0.95,
        }.get(key, default)
        return value

    mock.get_config = AsyncMock(side_effect=get_config_side_effect)
    mock.get = MagicMock(
        side_effect=lambda key, default=None: {
            "storage.backend": "sqlite",
            "anomaly_threshold": 0.95,
            "rate_limit_timeout": 30.0,
        }.get(key, default)
    )

    return mock


@pytest.fixture
def mock_storage_backend():
    """Provides a mock StorageBackend."""
    mock = AsyncMock()
    mock.start = AsyncMock()
    mock.stop = AsyncMock()
    mock.load_snapshot = AsyncMock(return_value=None)
    mock.save_snapshot = AsyncMock()
    mock.load_events = AsyncMock(return_value=[])
    mock.save_event = AsyncMock()
    mock.load_all_audit_logs = AsyncMock(return_value=[])
    mock.get_last_audit_hash = AsyncMock(return_value="genesis_hash")
    mock.save_audit_log = AsyncMock(return_value="new_hash")
    return mock


@pytest.fixture
def mock_knowledge_graph(mock_config_store):
    """Provides a mock Neo4jKnowledgeGraph."""
    from arbiter.arbiter_growth.arbiter_growth_manager import Neo4jKnowledgeGraph

    mock = AsyncMock(spec=Neo4jKnowledgeGraph)
    mock.add_fact = AsyncMock()
    return mock


@pytest.fixture
def mock_feedback_manager(mock_config_store):
    """Provides a mock FeedbackManager."""
    from arbiter.arbiter_growth.arbiter_growth_manager import LoggingFeedbackManager

    mock = AsyncMock(spec=LoggingFeedbackManager)
    mock.record_feedback = AsyncMock()
    return mock


@pytest.fixture
def mock_idempotency_store():
    """Provides a mock IdempotencyStore."""
    mock = AsyncMock()
    mock.check_and_set = AsyncMock(return_value=True)
    mock.start = AsyncMock()
    mock.stop = AsyncMock()
    return mock


@pytest.fixture
def mock_clock():
    """Provides a mock clock function."""
    return lambda: datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def manager_factory(
    mock_config_store,
    mock_storage_backend,
    mock_knowledge_graph,
    mock_feedback_manager,
    mock_idempotency_store,
    mock_clock,
):
    """Provides a factory for a manager instance."""

    def _factory(name="test_arbiter"):
        return ArbiterGrowthManager(
            arbiter_name=name,
            storage_backend=mock_storage_backend,
            knowledge_graph=mock_knowledge_graph,
            feedback_manager=mock_feedback_manager,
            clock=mock_clock,
            config_store=mock_config_store,
            idempotency_store=mock_idempotency_store,
        )

    return _factory


# --- Test Cases ---


@pytest.mark.asyncio
async def test_init(manager_factory, mock_storage_backend, mock_idempotency_store):
    """Test that the manager initializes with correct defaults."""
    manager = manager_factory()
    await manager.start()

    assert manager.arbiter == "test_arbiter"
    assert manager._state.arbiter_id == "test_arbiter"
    assert manager._state.level == 1
    # Fix: event_offset can be either int or string
    assert manager._state.event_offset in [0, "0"]
    assert len(manager._state.skills) == 0
    assert manager._running is True

    await manager.stop()


@pytest.mark.asyncio
async def test_start_and_stop(manager_factory, mock_storage_backend, caplog):
    """Test starting and stopping the manager."""
    manager = manager_factory()

    with caplog.at_level(logging.INFO):
        # Start the manager
        await manager.start()
        assert manager._running is True
        mock_storage_backend.start.assert_awaited_once()
        mock_storage_backend.load_snapshot.assert_awaited_once_with("test_arbiter")
        # Fix: Update log message assertion
        assert "started successfully" in caplog.text

        # Stop the manager
        await manager.stop()
        assert manager._running is False
        mock_storage_backend.stop.assert_awaited_once()
        # Fix: Update log message assertion
        assert "shut down completely" in caplog.text


@pytest.mark.asyncio
async def test_record_growth_event_happy_path(
    manager_factory, mock_storage_backend, mock_knowledge_graph
):
    """Test recording a growth event successfully."""
    manager = manager_factory()
    await manager.start()

    # Record a skill improvement
    await manager.improve_skill("python", 0.1)

    # Allow async processing
    while manager._pending_operations.qsize() > 0:
        await asyncio.sleep(0.1)

    # Check state was updated
    assert "python" in manager._state.skills
    assert manager._state.skills["python"] == 0.1

    # Verify external systems were called
    mock_knowledge_graph.add_fact.assert_awaited()

    await manager.stop()


@pytest.mark.asyncio
async def test_queue_full_error(manager_factory):
    """Test that the queue full error is raised when the queue is at capacity."""
    manager = manager_factory()
    await manager.start()

    # Fill the queue
    manager._pending_operations = asyncio.Queue(maxsize=2)
    manager.MAX_PENDING_OPERATIONS = 2

    # Fill with dummy operations
    await manager._queue_operation(lambda: None)
    await manager._queue_operation(lambda: None)

    # Next should fail with queue full
    with pytest.raises(OperationQueueFullError):
        await manager._queue_operation(lambda: None)

    await manager.stop()


@pytest.mark.asyncio
async def test_circuit_breaker_opens_and_rejects(manager_factory):
    """Test that the circuit breaker opens after failures and rejects requests."""
    manager = manager_factory()
    await manager.start()

    # Manually set circuit breaker state with proper listener
    listener = BreakerListener()
    manager._push_event_breaker.add_listener(listener)
    manager._push_event_breaker.open()

    # Next request should be rejected
    with pytest.raises(CircuitBreakerOpenError):
        await manager._push_events([])

    await manager.stop()


@pytest.mark.asyncio
async def test_audit_chain_tampered_on_hash_mismatch(
    manager_factory, mock_storage_backend
):
    """Test that audit chain tampering is detected."""
    manager = manager_factory()
    await manager.start()

    # Set up tampered audit logs
    mock_storage_backend.load_all_audit_logs.return_value = [
        {
            "log_hash": "hash1",
            "previous_log_hash": "wrong_hash",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "arbiter_id": "test_arbiter",
            "operation": "test_op",
            "details": {},
        }
    ]

    # Should detect tampering
    with pytest.raises(AuditChainTamperedError):
        await manager._validate_audit_chain()

    await manager.stop()


@pytest.mark.asyncio
async def test_health_status_reports_correctly(manager_factory):
    """Test that health status is reported correctly."""
    manager = manager_factory()
    await manager.start()

    status = await manager.get_health_status()
    assert status["status"] == "healthy"
    assert status["arbiter_id"] == "test_arbiter"
    assert "queue" in status
    assert "circuit_breakers" in status

    await manager.stop()


@pytest.mark.asyncio
async def test_readiness_probe_succeeds(manager_factory):
    """Test that readiness probe works."""
    manager = manager_factory()
    await manager.start()

    is_ready = await manager.readiness_probe()
    assert is_ready is True

    await manager.stop()


@pytest.mark.asyncio
async def test_concurrent_operations_are_processed(manager_factory):
    """Test that concurrent growth events are processed correctly."""
    manager = manager_factory()
    await manager.start()

    # Record multiple events concurrently
    tasks = [manager.improve_skill(f"skill_{i}", 0.01) for i in range(5)]

    await asyncio.gather(*tasks)

    # Allow async processing
    while manager._pending_operations.qsize() > 0:
        await asyncio.sleep(0.1)

    # Check state reflects events
    assert len(manager._state.skills) >= 5

    await manager.stop()


@pytest.mark.asyncio
async def test_save_errors_increment_metric(manager_factory, mock_storage_backend):
    """Test that save errors increment the appropriate metric."""
    manager = manager_factory()
    await manager.start()

    # Clear any existing metric value
    GROWTH_SAVE_ERRORS.labels(arbiter="test_arbiter")._value.set(0)

    # Simulate storage failure
    mock_storage_backend.save_snapshot.side_effect = Exception("Storage error")

    # Force a save
    manager._dirty = True
    with pytest.raises(Exception):
        await manager._save_snapshot_to_db()

    # Check that the error metric was incremented
    assert GROWTH_SAVE_ERRORS.labels(arbiter="test_arbiter")._value.get() >= 1

    # Reset the mock to prevent errors during stop
    mock_storage_backend.save_snapshot.side_effect = None
    mock_storage_backend.save_snapshot.return_value = None

    await manager.stop()


@pytest.mark.asyncio
async def test_anomaly_detection_sets_metric(manager_factory):
    """Test that anomaly detection sets the appropriate metric."""
    manager = manager_factory()
    await manager.start()

    # Clear any existing metric value
    GROWTH_ANOMALY_SCORE._metrics.clear()

    # Create an anomalous event (huge improvement)
    await manager.record_growth_event(
        "skill_improved", {"skill_name": "test", "improvement_amount": 10.0}
    )

    # Allow async processing
    while manager._pending_operations.qsize() > 0:
        await asyncio.sleep(0.1)

    # Check that anomaly was detected
    assert (
        GROWTH_ANOMALY_SCORE.labels(
            arbiter="test_arbiter", event_type="skill_improved"
        )._value.get()
        >= 10.0
    )

    await manager.stop()


@pytest.mark.asyncio
async def test_snapshot_persistence(manager_factory, mock_storage_backend):
    """Test that snapshots are saved and loaded correctly."""
    # Create initial state
    initial_state_dict = {
        "arbiter_id": "test_arbiter",
        "level": 5,
        "skills": {"python": 0.8},
        "event_offset": 10,
    }

    # Mock loading this snapshot
    mock_storage_backend.load_snapshot.return_value = initial_state_dict

    manager = manager_factory()
    await manager.start()

    # State should be loaded from snapshot
    assert manager._state.level == 5
    assert manager._state.skills["python"] == 0.8
    # Fix: event_offset type comparison
    assert str(manager._state.event_offset) == "10" or manager._state.event_offset == 10

    # Modify state
    await manager.improve_skill("rust", 0.2)

    # Allow async processing
    while manager._pending_operations.qsize() > 0:
        await asyncio.sleep(0.1)

    # Force save
    manager._dirty = True
    await manager._save_snapshot_to_db()

    # Verify snapshot was saved
    mock_storage_backend.save_snapshot.assert_awaited()

    await manager.stop()


@pytest.mark.asyncio
async def test_rate_limiting(manager_factory):
    """Test that rate limiting prevents excessive requests."""
    manager = manager_factory()
    await manager.start()

    # Mock rate limiter to reject
    manager._rate_limiter.acquire = AsyncMock(return_value=False)

    # Should raise rate limit error
    with pytest.raises(RateLimitError):
        await manager._queue_operation(lambda: None)

    await manager.stop()


@pytest.mark.asyncio
async def test_graceful_shutdown_saves_pending(manager_factory, mock_storage_backend):
    """Test that graceful shutdown saves pending operations."""
    manager = manager_factory()
    await manager.start()

    # Add events
    await manager.improve_skill("skill1", 0.1)
    await manager.improve_skill("skill2", 0.2)

    # Stop immediately
    await manager.stop()

    # Should have saved
    mock_storage_backend.save_snapshot.assert_awaited()


@pytest.mark.asyncio
async def test_plugin_hooks(manager_factory):
    """Test that plugin hooks work."""

    class TestHook:
        def __init__(self):
            self.events = []

        async def on_growth_event(self, event, state):
            self.events.append(event)

    manager = manager_factory()
    await manager.start()

    hook = TestHook()
    manager.register_hook(hook, stage="after")

    await manager.improve_skill("test", 0.05)

    # Allow async processing
    while manager._pending_operations.qsize() > 0:
        await asyncio.sleep(0.1)

    assert len(hook.events) > 0

    await manager.stop()


@pytest.mark.asyncio
async def test_level_up(manager_factory):
    """Test level up functionality."""
    manager = manager_factory()
    await manager.start()

    initial_level = manager._state.level
    await manager.level_up()

    # Allow async processing
    while manager._pending_operations.qsize() > 0:
        await asyncio.sleep(0.1)

    assert manager._state.level == initial_level + 1

    await manager.stop()


@pytest.mark.asyncio
async def test_idempotency_key_generation(manager_factory):
    """Test idempotency key generation."""
    manager = manager_factory()
    await manager.start()

    event = GrowthEvent(
        type="test_event",
        timestamp="2024-01-01T00:00:00+00:00",
        details={"key": "value"},
    )

    key1 = manager._generate_idempotency_key(event, "service1")
    key2 = manager._generate_idempotency_key(event, "service1")
    key3 = manager._generate_idempotency_key(event, "service2")

    # Same event and service should generate same key
    assert key1 == key2
    # Different service should generate different key
    assert key1 != key3

    await manager.stop()


# --- Reconstructed and New Tests ---


@pytest.mark.asyncio
async def test_snapshot_interval(
    manager_factory, mock_storage_backend, mock_clock, mock_config_store
):
    """Test that a snapshot is saved after the configured number of events."""
    manager = manager_factory()

    # Mock config to ensure proper rate limiting values
    mock_config_store.get_config = AsyncMock(
        side_effect=lambda key, default=None: {
            "rate_limit_tokens": 200,  # Increase to allow many operations
            "rate_limit_refill_rate": 100,
            "rate_limit_timeout": 30.0,
            "arbiter.snapshot_interval_events": 100,
        }.get(key, default)
    )

    await manager.start()

    # Clear any previous calls
    mock_storage_backend.save_snapshot.reset_mock()

    # Record events with unique skills to avoid idempotency issues
    for i in range(100):
        await manager.improve_skill(f"skill_{i}", 0.01)

    # Allow async processing
    while manager._pending_operations.qsize() > 0:
        await asyncio.sleep(0.1)

    # Should have triggered at least one snapshot
    assert mock_storage_backend.save_snapshot.call_count >= 1

    await manager.stop()


@pytest.mark.asyncio
async def test_audit_chaining(manager_factory, mock_storage_backend, caplog):
    """Test that audit logs are created and correctly chained."""
    manager = manager_factory()
    await manager.start()

    mock_storage_backend.load_all_audit_logs.return_value = []

    # Record a few events
    await manager.improve_skill("skill1", 0.1)
    await manager.improve_skill("skill2", 0.2)

    # Allow async processing
    while manager._pending_operations.qsize() > 0:
        await asyncio.sleep(0.1)

    # Verify audit logs were created
    assert mock_storage_backend.save_audit_log.await_count >= 2

    # Get the calls to save_audit_log
    calls = mock_storage_backend.save_audit_log.await_args_list

    # Verify they were called with proper arguments
    assert len(calls) >= 2
    for call_args in calls:
        assert len(call_args[0]) >= 4  # arbiter_id, operation, details, previous_hash
        assert call_args[0][0] == "test_arbiter"  # arbiter_id

    await manager.stop()
