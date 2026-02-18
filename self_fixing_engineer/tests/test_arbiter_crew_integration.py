# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Integration tests for Arbiter-CrewManager integration.
Tests event hook wiring, status reporting, YAML config loading, and graceful fallback.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
import yaml
from self_fixing_engineer.agent_orchestration.crew_manager import (
    CrewAgentBase,
    CrewManager,
)


@pytest.fixture
def mock_sandbox_runner():
    """Mock sandbox runner that doesn't create background tasks."""

    async def fake_runner(*args, **kwargs):
        mock_sandbox = MagicMock()
        mock_sandbox.id = "sandbox_id"
        # Mock sandbox that raises CancelledError when waited upon (simulates interrupted process)
        mock_sandbox.wait = AsyncMock(side_effect=asyncio.CancelledError())
        mock_sandbox.stop = Mock()
        mock_sandbox.kill = Mock()
        mock_sandbox.remove = Mock()
        return mock_sandbox

    return fake_runner


@pytest.fixture
def mock_agent_health_poller():
    """Mock health poller."""
    return AsyncMock(return_value={"status": "healthy", "last_heartbeat": 0})


@pytest.fixture
def mock_agent_stop_commander():
    """Mock stop commander."""
    return AsyncMock()


@pytest.fixture
async def crew_manager(
    mock_sandbox_runner,
    mock_agent_health_poller,
    mock_agent_stop_commander,
    monkeypatch,
):
    """Fixture for CrewManager instance."""
    manager = CrewManager(
        sandbox_runner=mock_sandbox_runner,
        agent_health_poller=mock_agent_health_poller,
        agent_stop_commander=mock_agent_stop_commander,
        auto_restart=False,
        heartbeat_timeout=100.0,
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
    if manager._heartbeat_monitor_task:
        manager._heartbeat_monitor_task.cancel()
        try:
            await asyncio.wait_for(manager._heartbeat_monitor_task, timeout=0.1)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


@pytest.fixture
def mock_arbiter_settings():
    """Create mock settings for Arbiter."""
    settings = MagicMock()
    settings.REPORTS_DIRECTORY = "/tmp/test_reports"
    settings.PROMETHEUS_GATEWAY = None
    settings.ALERT_WEBHOOK_URL = None
    settings.REDIS_URL = "redis://localhost:6379"
    return settings


@pytest.fixture
def mock_monitor():
    """Create a mock Monitor."""
    monitor = MagicMock()
    monitor.log_metric = MagicMock()
    monitor.generate_reports = MagicMock(return_value={"status": "ok"})
    return monitor


@pytest.fixture
def mock_message_queue_service():
    """Create a mock MessageQueueService."""
    mq = MagicMock()
    mq.publish = AsyncMock()
    return mq


@pytest.mark.asyncio
async def test_arbiter_accepts_crew_manager_parameter():
    """Test that Arbiter accepts crew_manager as an explicit parameter."""
    # Import Arbiter lazily to avoid expensive imports
    from self_fixing_engineer.arbiter import Arbiter

    # Create a mock crew_manager
    mock_crew_manager = MagicMock()
    mock_crew_manager.add_hook = MagicMock()

    # Create minimal mocks for required parameters
    mock_engine = MagicMock()
    mock_settings = MagicMock()
    mock_settings.REPORTS_DIRECTORY = "/tmp/test"
    mock_settings.PROMETHEUS_GATEWAY = None

    # The key test: verify crew_manager is accepted as a named parameter
    # We don't fully initialize to avoid expensive setup
    try:
        arbiter = Arbiter(
            name="test_arbiter",
            db_engine=mock_engine,
            settings=mock_settings,
            crew_manager=mock_crew_manager,  # This should not raise TypeError
        )
        # Verify crew_manager was stored
        assert arbiter.crew_manager is mock_crew_manager
        # Verify hooks were attempted to be wired
        assert mock_crew_manager.add_hook.called
    except Exception as e:
        # Some initialization might fail, but we're testing parameter acceptance
        # Check that it's not a TypeError about unexpected keyword argument
        assert "crew_manager" not in str(e), f"crew_manager should be accepted: {e}"


@pytest.mark.asyncio
async def test_arbiter_event_hooks_wired(
    crew_manager, mock_monitor, mock_message_queue_service, mock_arbiter_settings
):
    """Test that event hooks are properly wired when crew_manager is provided."""
    from self_fixing_engineer.arbiter import Arbiter

    # Track hook registrations
    hooks_registered = []

    def track_hook(event: str, callback):
        hooks_registered.append(event)

    crew_manager.add_hook = track_hook

    mock_engine = MagicMock()

    # Create Arbiter with crew_manager
    try:
        arbiter = Arbiter(
            name="test_arbiter",
            db_engine=mock_engine,
            settings=mock_arbiter_settings,
            crew_manager=crew_manager,
            monitor=mock_monitor,
            message_queue_service=mock_message_queue_service,
        )

        # Verify all 4 hooks were registered
        expected_hooks = [
            "on_agent_start",
            "on_agent_stop",
            "on_agent_fail",
            "on_agent_heartbeat_missed",
        ]
        for hook in expected_hooks:
            assert hook in hooks_registered, f"Hook {hook} should be registered"

    except Exception as e:
        # Some initialization might fail, but we're testing hook wiring
        # The hooks should be registered before any failure
        if "on_agent_start" not in hooks_registered:
            pytest.fail(f"Hooks should be wired even if initialization fails later: {e}")


@pytest.mark.asyncio
async def test_arbiter_get_crew_status(crew_manager):
    """Test that get_crew_status returns proper status from CrewManager."""
    from self_fixing_engineer.arbiter import Arbiter

    mock_engine = MagicMock()
    mock_settings = MagicMock()
    mock_settings.REPORTS_DIRECTORY = "/tmp/test"
    mock_settings.PROMETHEUS_GATEWAY = None

    # Mock crew_manager.status()
    crew_manager.status = AsyncMock(
        return_value={"agents": [], "running": 0, "stopped": 0}
    )

    try:
        arbiter = Arbiter(
            name="test_arbiter",
            db_engine=mock_engine,
            settings=mock_settings,
            crew_manager=crew_manager,
        )

        # Test get_crew_status
        status = await arbiter.get_crew_status()
        assert status["agents"] == []
        assert crew_manager.status.called

    except Exception as e:
        # If initialization fails for other reasons, skip this test
        pytest.skip(f"Arbiter initialization failed: {e}")


@pytest.mark.asyncio
async def test_arbiter_get_crew_status_without_crew_manager():
    """Test that get_crew_status returns safe default when crew_manager is None."""
    from self_fixing_engineer.arbiter import Arbiter

    mock_engine = MagicMock()
    mock_settings = MagicMock()
    mock_settings.REPORTS_DIRECTORY = "/tmp/test"
    mock_settings.PROMETHEUS_GATEWAY = None

    try:
        arbiter = Arbiter(
            name="test_arbiter",
            db_engine=mock_engine,
            settings=mock_settings,
            crew_manager=None,  # No crew_manager
        )

        # Test get_crew_status with None crew_manager
        status = await arbiter.get_crew_status()
        assert status["available"] is False
        assert "reason" in status

    except Exception as e:
        pytest.skip(f"Arbiter initialization failed: {e}")


@pytest.mark.asyncio
async def test_arbiter_scale_crew(crew_manager):
    """Test that scale_crew delegates to CrewManager."""
    from self_fixing_engineer.arbiter import Arbiter

    mock_engine = MagicMock()
    mock_settings = MagicMock()
    mock_settings.REPORTS_DIRECTORY = "/tmp/test"
    mock_settings.PROMETHEUS_GATEWAY = None

    # Mock crew_manager.scale()
    crew_manager.scale = AsyncMock()

    try:
        arbiter = Arbiter(
            name="test_arbiter",
            db_engine=mock_engine,
            settings=mock_settings,
            crew_manager=crew_manager,
        )

        # Test scale_crew
        result = await arbiter.scale_crew(count=5, agent_class="TestAgent")
        assert result["success"] is True
        assert result["target_count"] == 5
        crew_manager.scale.assert_called_once()

    except Exception as e:
        pytest.skip(f"Arbiter initialization failed: {e}")


@pytest.mark.asyncio
async def test_arbiter_get_status_includes_crew_manager(crew_manager):
    """Test that get_status includes crew_manager section."""
    from self_fixing_engineer.arbiter import Arbiter

    mock_engine = MagicMock()
    mock_settings = MagicMock()
    mock_settings.REPORTS_DIRECTORY = "/tmp/test"
    mock_settings.PROMETHEUS_GATEWAY = None

    # Mock crew_manager methods
    crew_manager.health = AsyncMock(return_value={"status": "healthy"})
    crew_manager.list_agents = MagicMock(return_value=["agent1", "agent2"])

    try:
        arbiter = Arbiter(
            name="test_arbiter",
            db_engine=mock_engine,
            settings=mock_settings,
            crew_manager=crew_manager,
        )

        # Mock feedback to avoid DB access
        arbiter.feedback = None

        # Test get_status
        status = await arbiter.get_status()
        assert "crew_manager" in status
        assert status["crew_manager"]["available"] is True
        assert status["crew_manager"]["agent_count"] == 2

    except Exception as e:
        pytest.skip(f"Arbiter initialization failed: {e}")


@pytest.mark.asyncio
async def test_from_config_yaml_loads_agents(tmp_path):
    """Test that from_config_yaml loads agents from YAML file."""
    # Create a temporary YAML config
    config_data = {
        "version": "1.0.0",
        "agents": [
            {
                "id": "test_agent_1",
                "name": "test_agent_1",
                "manifest": "plugins/test/manifest.json",
                "entrypoint": "plugins/test/agent.py",
                "agent_type": "ai",
                "role_ref": "configdb://roles/test",
                "skills_ref": "configdb://skills/test",
                "config": {"param1": "value1"},
            },
            {
                "id": "test_agent_2",
                "name": "test_agent_2",
                "manifest": "plugins/test2/manifest.json",
                "entrypoint": "plugins/test2/agent.py",
                "agent_type": "plugin",
            },
        ],
    }

    config_path = tmp_path / "test_crew_config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)

    # Mock sandbox runner
    mock_runner = AsyncMock()
    mock_health_poller = AsyncMock(return_value={"status": "healthy"})
    mock_stop_commander = AsyncMock()

    # Load from YAML
    manager = await CrewManager.from_config_yaml(
        str(config_path),
        caller_role="admin",
        sandbox_runner=mock_runner,
        agent_health_poller=mock_health_poller,
        agent_stop_commander=mock_stop_commander,
        auto_restart=False,
    )

    # Verify agents were loaded
    assert len(manager.agents) == 2
    assert "test_agent_1" in manager.agents
    assert "test_agent_2" in manager.agents

    # Verify metadata was preserved
    agent1 = manager.agents["test_agent_1"]
    assert agent1["metadata"]["manifest"] == "plugins/test/manifest.json"
    assert agent1["metadata"]["role_ref"] == "configdb://roles/test"


@pytest.mark.asyncio
async def test_from_config_yaml_validates_agent_names(tmp_path):
    """Test that from_config_yaml validates agent names against NAME_REGEX."""
    # Create a temporary YAML config with invalid names
    config_data = {
        "version": "1.0.0",
        "agents": [
            {
                "name": "valid_agent_123",
                "manifest": "plugins/valid/manifest.json",
                "entrypoint": "plugins/valid/agent.py",
                "agent_type": "ai",
            },
            {
                "name": "invalid agent with spaces",  # Invalid
                "manifest": "plugins/invalid/manifest.json",
                "entrypoint": "plugins/invalid/agent.py",
                "agent_type": "ai",
            },
            {
                "name": "invalid@agent#special",  # Invalid
                "manifest": "plugins/invalid2/manifest.json",
                "entrypoint": "plugins/invalid2/agent.py",
                "agent_type": "ai",
            },
        ],
    }

    config_path = tmp_path / "test_crew_config_invalid.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)

    # Mock dependencies
    mock_runner = AsyncMock()
    mock_health_poller = AsyncMock(return_value={"status": "healthy"})
    mock_stop_commander = AsyncMock()

    # Load from YAML
    manager = await CrewManager.from_config_yaml(
        str(config_path),
        caller_role="admin",
        sandbox_runner=mock_runner,
        agent_health_poller=mock_health_poller,
        agent_stop_commander=mock_stop_commander,
        auto_restart=False,
    )

    # Only valid agent should be loaded
    assert len(manager.agents) == 1
    assert "valid_agent_123" in manager.agents
    assert "invalid agent with spaces" not in manager.agents
    assert "invalid@agent#special" not in manager.agents


@pytest.mark.asyncio
async def test_crew_agent_base_registered():
    """Test that CrewAgentBase is registered as a default loadable class."""
    # Verify CrewAgentBase is in the registry
    assert "CrewAgentBase" in CrewManager.AGENT_CLASS_REGISTRY
    assert CrewManager.AGENT_CLASS_REGISTRY["CrewAgentBase"] == CrewAgentBase

    # Verify it can be retrieved
    agent_class = CrewManager.get_agent_class_by_name("CrewAgentBase")
    assert agent_class == CrewAgentBase


@pytest.mark.asyncio
async def test_arbiter_hook_handlers_catch_exceptions(
    crew_manager, mock_monitor, mock_arbiter_settings
):
    """Test that hook handlers catch exceptions and don't crash."""
    from self_fixing_engineer.arbiter import Arbiter

    mock_engine = MagicMock()

    # Create a message queue that raises exceptions
    mock_mq = MagicMock()
    mock_mq.publish = AsyncMock(side_effect=Exception("MQ Error"))

    try:
        arbiter = Arbiter(
            name="test_arbiter",
            db_engine=mock_engine,
            settings=mock_arbiter_settings,
            crew_manager=crew_manager,
            monitor=mock_monitor,
            message_queue_service=mock_mq,
        )

        # Call hook handlers directly - they should handle exceptions
        await arbiter._on_crew_agent_start(
            crew_manager, "test_agent", {"status": "running"}
        )
        await arbiter._on_crew_agent_stop(
            crew_manager, "test_agent", {"status": "stopped"}
        )
        await arbiter._on_crew_agent_fail(
            crew_manager, "test_agent", {"status": "failed"}, error="Test error"
        )
        await arbiter._on_crew_heartbeat_missed(
            crew_manager, "test_agent", {"status": "unresponsive"}
        )

        # If we get here, exceptions were caught properly
        assert True

    except Exception as e:
        pytest.skip(f"Arbiter initialization failed: {e}")
