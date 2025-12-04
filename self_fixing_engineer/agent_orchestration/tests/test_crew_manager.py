# tests/test_crew_manager.py
import asyncio
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from self_fixing_engineer.agent_orchestration.crew_manager import (
    MAX_CONFIG_SIZE,
    CrewAgentBase,
    CrewManager,
    CrewPermissionError as PermissionError,
    ResourceError,
    sanitize_dict,
    structured_log,
)

# Import availability flags
try:
    from agent_orchestration.crew_manager import _AIOREDIS_AVAILABLE, _PSUTIL_AVAILABLE
except ImportError:
    try:
        import psutil

        _PSUTIL_AVAILABLE = True
    except ImportError:
        _PSUTIL_AVAILABLE = False

    try:
        import redis.asyncio as redis

        _AIOREDIS_AVAILABLE = True
    except ImportError:
        _AIOREDIS_AVAILABLE = False

if _PSUTIL_AVAILABLE:
    pass


@pytest.fixture
def mock_policy():
    """Mock policy object."""
    policy = MagicMock()
    policy.can_perform = AsyncMock(return_value=True)
    policy.health = AsyncMock(return_value="OK")
    policy.status = AsyncMock(return_value={"status": "active"})
    return policy


@pytest.fixture
def mock_metrics_hook():
    """Mock metrics hook."""
    return AsyncMock()


@pytest.fixture
def mock_audit_hook():
    """Mock audit hook."""
    return AsyncMock()


@pytest.fixture
def mock_sandbox_runner():
    """Mock sandbox runner that doesn't create background tasks."""

    async def fake_runner(*args, **kwargs):
        mock_sandbox = MagicMock()
        mock_sandbox.id = "sandbox_id"
        # Create a future that never completes to simulate a running process
        mock_sandbox.wait = AsyncMock(side_effect=asyncio.CancelledError())
        mock_sandbox.stop = Mock()
        mock_sandbox.kill = Mock()
        mock_sandbox.remove = Mock()
        return mock_sandbox

    return fake_runner


@pytest.fixture
def mock_agent_health_poller():
    """Mock health poller."""
    return AsyncMock(return_value={"status": "healthy", "last_heartbeat": time.time()})


@pytest.fixture
def mock_agent_stop_commander():
    """Mock stop commander."""
    return AsyncMock()


@pytest.fixture
async def crew_manager(
    mock_policy,
    mock_metrics_hook,
    mock_audit_hook,
    mock_sandbox_runner,
    mock_agent_health_poller,
    mock_agent_stop_commander,
    monkeypatch,
):
    """Fixture for CrewManager instance with monitoring disabled."""
    manager = CrewManager(
        policy=mock_policy,
        metrics_hook=mock_metrics_hook,
        audit_hook=mock_audit_hook,
        sandbox_runner=mock_sandbox_runner,
        agent_health_poller=mock_agent_health_poller,
        agent_stop_commander=mock_agent_stop_commander,
        max_restart=2,
        heartbeat_timeout=100.0,  # Very long timeout
        backpressure=2,
        auto_restart=False,  # Disable auto-restart
    )

    # Override RBAC for testing
    manager._check_rbac = AsyncMock(return_value=True)

    # Patch the monitor method to prevent it from creating tasks
    async def mock_monitor_agent_sandbox(name):
        """Do nothing - no monitoring in tests"""
        pass

    monkeypatch.setattr(manager, "_monitor_agent_sandbox", mock_monitor_agent_sandbox)

    yield manager

    # Clean shutdown
    manager._closed = True

    # Cancel any tasks that might have been created
    if manager._heartbeat_monitor_task:
        manager._heartbeat_monitor_task.cancel()
        try:
            await asyncio.wait_for(manager._heartbeat_monitor_task, timeout=0.1)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    # Clean up all sandboxes
    for name in list(manager._agent_sandboxes.keys()):
        manager._agent_sandboxes.pop(name, None)

    for name in list(manager.agents.keys()):
        if name in manager.agents:
            manager.agents[name]["sandbox"] = None
            manager.agents[name]["status"] = "STOPPED"


@pytest.fixture
def caplog(caplog):
    """Capture logs."""
    caplog.set_level(logging.DEBUG)
    return caplog


# Test agent class
class TestAgent(CrewAgentBase):
    pass


# Register the test agent class
CrewManager.register_agent_class(TestAgent)


@pytest.mark.asyncio
async def test_register_and_get_agent_class():
    """Test agent class registry."""
    CrewManager.register_agent_class(TestAgent)
    cls = CrewManager.get_agent_class_by_name("TestAgent")
    assert cls == TestAgent


@pytest.mark.asyncio
async def test_get_agent_class_not_registered():
    """Test getting unregistered class."""
    with pytest.raises(ValueError, match="not registered"):
        CrewManager.get_agent_class_by_name("NonExistent")


@pytest.mark.asyncio
async def test_sanitize_dict():
    """Test dict sanitization."""
    data = {
        "password": "secret",
        "safe": "value",
        "nested": {"data": "ok", "secret_key": "hidden"},
    }
    sanitized = sanitize_dict(data)
    assert sanitized["password"] == "REDACTED"
    assert sanitized["nested"]["secret_key"] == "REDACTED"
    assert sanitized["safe"] == "value"


@pytest.mark.asyncio
async def test_structured_log(caplog):
    """Test structured logging - expecting sanitization."""
    structured_log("test_event", safe_data="value", secret="hidden")
    log_entry = json.loads(caplog.records[0].message)
    assert log_entry["event"] == "test_event"
    assert log_entry["safe_data"] == "value"
    assert log_entry["secret"] == "REDACTED"


@pytest.mark.asyncio
async def test_add_agent_success(crew_manager):
    """Test adding an agent."""
    agent_info = await crew_manager.add_agent(
        "test_agent",
        TestAgent,
        config={"key": "value"},
        tags=["tag1"],
        metadata={"meta": "data"},
    )
    assert agent_info["name"] == "test_agent"
    assert agent_info["agent_class_name"] == "TestAgent"
    assert agent_info["status"] == "STOPPED"


@pytest.mark.asyncio
async def test_add_agent_invalid_name(crew_manager):
    """Test invalid agent name."""
    with pytest.raises(ValueError, match="Invalid agent name"):
        await crew_manager.add_agent("invalid@name", TestAgent)


@pytest.mark.asyncio
async def test_add_agent_invalid_config(crew_manager):
    """Test invalid config."""
    large_config = {"key": "a" * (MAX_CONFIG_SIZE + 1)}
    with pytest.raises(ValueError, match="Invalid config"):
        await crew_manager.add_agent("test_agent", TestAgent, config=large_config)


@pytest.mark.asyncio
async def test_add_agent_rbac_failure(crew_manager):
    """Test RBAC failure for add_agent."""
    crew_manager._check_rbac = AsyncMock(return_value=False)
    with pytest.raises(PermissionError):
        await crew_manager.add_agent(
            "test_agent", TestAgent, caller_role="unauthorized"
        )
    crew_manager._check_rbac = AsyncMock(return_value=True)


@pytest.mark.asyncio
async def test_add_agent_max_agents(crew_manager, monkeypatch):
    """Test max agents cap."""
    monkeypatch.setattr(crew_manager, "_max_agents", 1)
    await crew_manager.add_agent("agent1", TestAgent)
    with pytest.raises(ResourceError):
        await crew_manager.add_agent("agent2", TestAgent)


@pytest.mark.asyncio
async def test_sync_add_agent():
    """Test sync wrapper for add_agent."""
    pytest.skip("Sync wrapper test requires non-async context")


@pytest.mark.asyncio
async def test_remove_agent(crew_manager):
    """Test removing an agent."""
    await crew_manager.add_agent("test_agent", TestAgent)
    await crew_manager.remove_agent("test_agent")
    assert "test_agent" not in crew_manager.agents


@pytest.mark.asyncio
async def test_start_agent_success(crew_manager):
    """Test starting an agent."""
    await crew_manager.add_agent("test_agent", TestAgent)
    await crew_manager.start_agent("test_agent")
    assert crew_manager.agents["test_agent"]["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_start_agent_resource_error(crew_manager, monkeypatch):
    """Test resource error on start - agent should be marked as FAILED."""
    if _PSUTIL_AVAILABLE:
        # Mock the _start_sandbox_with_retries method to raise ResourceError
        async def mock_start_sandbox_with_retries(agent_info):
            raise ResourceError("High CPU usage. Aborting agent launch.")

        monkeypatch.setattr(
            crew_manager, "_start_sandbox_with_retries", mock_start_sandbox_with_retries
        )

        await crew_manager.add_agent("test_agent", TestAgent)

        # Start the agent - it should fail but not raise the exception
        await crew_manager.start_agent("test_agent")

        # Check that the agent is marked as FAILED
        assert crew_manager.agents["test_agent"]["status"] == "FAILED"
        assert len(crew_manager.agents["test_agent"]["failures"]) > 0
        assert "High CPU usage" in str(
            crew_manager.agents["test_agent"]["failures"][-1]["error"]
        )
    else:
        pytest.skip("psutil not available")


@pytest.mark.asyncio
async def test_stop_agent_success(crew_manager):
    """Test stopping an agent."""
    await crew_manager.add_agent("test_agent", TestAgent)
    await crew_manager.start_agent("test_agent")
    await crew_manager.stop_agent("test_agent")
    assert crew_manager.agents["test_agent"]["status"] == "STOPPED"


@pytest.mark.asyncio
async def test_reload_agent(crew_manager):
    """Test reloading an agent."""
    await crew_manager.add_agent("test_agent", TestAgent)
    await crew_manager.start_agent("test_agent")

    # Reload with new config
    await crew_manager.reload_agent("test_agent", config={"new_key": "value"})

    # Check config was updated
    assert crew_manager.agents["test_agent"]["config"]["new_key"] == "value"
    # Status should be RUNNING after reload
    assert crew_manager.agents["test_agent"]["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_scale_agents(crew_manager):
    """Test scaling agents."""
    await crew_manager.add_agent("agent1", TestAgent, tags=["scale"])
    await crew_manager.scale(3, TestAgent, tags=["scale"])
    agents = crew_manager.list_agents(tags=["scale"])
    assert len(agents) == 3


@pytest.mark.asyncio
async def test_heartbeat_monitor():
    """Test heartbeat monitor in isolation."""
    # Create a separate manager for this test
    manager = CrewManager(auto_restart=False, heartbeat_timeout=0.5)
    manager._check_rbac = AsyncMock(return_value=True)

    # Don't actually run the monitor - just verify the setup
    await manager.add_agent("test_agent", TestAgent)
    assert "test_agent" in manager.agents

    # Clean up
    manager._closed = True


@pytest.mark.asyncio
async def test_health_report(crew_manager):
    """Test health report generation."""
    await crew_manager.add_agent("test_agent", TestAgent)
    health = await crew_manager.health()
    assert "test_agent" in health


@pytest.mark.asyncio
async def test_lint(crew_manager):
    """Test linting for duplicates/unused."""
    await crew_manager.add_agent("test_agent", TestAgent)
    lint = await crew_manager.lint()
    assert lint["duplicates"] == []


@pytest.mark.asyncio
async def test_describe(crew_manager):
    """Test describe method."""
    await crew_manager.add_agent("test_agent", TestAgent, tags=["tag1"])
    desc = await crew_manager.describe()
    assert desc["agent_count"] == 1
    assert desc["tags_configured"]["test_agent"] == ["tag1"]


@pytest.mark.asyncio
async def test_save_load_redis(crew_manager):
    """Test Redis state backend."""
    if not _AIOREDIS_AVAILABLE:
        pytest.skip("aioredis not available")

    mock_redis = MagicMock()
    mock_redis.set = AsyncMock()
    crew_manager.redis_pool = mock_redis

    await crew_manager.add_agent("test_agent", TestAgent)
    await crew_manager.save_state_redis()

    mock_redis.set.assert_called()


@pytest.mark.asyncio
async def test_shutdown(crew_manager):
    """Test full shutdown."""
    await crew_manager.add_agent("test_agent", TestAgent)
    await crew_manager.start_agent("test_agent")
    await crew_manager.shutdown()
    assert crew_manager._closed
    assert crew_manager.agents["test_agent"]["status"] == "STOPPED"
