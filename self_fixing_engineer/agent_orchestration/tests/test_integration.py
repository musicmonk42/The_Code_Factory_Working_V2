# tests/test_integration.py
import pytest
import yaml
import asyncio
from agent_orchestration.crew_manager import CrewManager, CrewAgentBase
from unittest.mock import AsyncMock, MagicMock, Mock

# Register the base class for testing
CrewManager.register_agent_class(CrewAgentBase)


@pytest.fixture
def temp_config(tmp_path):
    """Fixture for temp crew_config.yaml."""
    path = tmp_path / "crew_config.yaml"
    config = {
        "agents": [
            {
                "id": "agent1",
                "name": "agent_one",
                "manifest": "test",
                "entrypoint": "run",
                "agent_type": "ai",
            }  # Changed to "agent_one"
        ],
        "compliance_controls": {"AC-1": {"status": "enforced", "required": True}},
    }
    with open(path, "w") as f:
        yaml.safe_dump(config, f)
    return str(path)


@pytest.fixture
def mock_sandbox_runner():
    """Mock sandbox runner for integration tests."""

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
    """Mock health poller for integration tests."""
    # Don't include 'status' in the return value to avoid overriding the agent's status
    # Only return health-related fields
    return AsyncMock(return_value={"last_heartbeat": 1234567890, "running": True})


@pytest.mark.asyncio
async def test_integration_load_config_and_manage_agents(
    temp_config, mock_sandbox_runner, mock_agent_health_poller
):
    """Integration test: Load config, add/start/stop agent."""

    def load_crew_config(path):
        with open(path) as f:
            return yaml.safe_load(f)

    # Create manager with mock sandbox runner
    manager = CrewManager(
        sandbox_runner=mock_sandbox_runner,
        agent_health_poller=mock_agent_health_poller,
        auto_restart=False,  # Disable auto-restart for testing
    )
    # Override RBAC for testing
    manager._check_rbac = AsyncMock(return_value=True)

    config_data = load_crew_config(temp_config)
    for agent in config_data["agents"]:
        await manager.add_agent(agent["name"], "CrewAgentBase")  # Use registered class name

    # Start the agent
    await manager.start_agent(config_data["agents"][0]["name"])

    # Get health report
    health = await manager.health()

    # Verify the agent exists in health report
    assert config_data["agents"][0]["name"] in health

    # The agent should be running (the mock health poller returns running=True)
    assert health[config_data["agents"][0]["name"]]["running"]

    # The agent's status should be "RUNNING" as set by start_agent
    assert manager.agents[config_data["agents"][0]["name"]]["status"] == "RUNNING"

    # Stop the agent
    await manager.stop_agent(config_data["agents"][0]["name"])

    # Get updated health report
    health = await manager.health()

    # The agent should no longer be running
    assert not health[config_data["agents"][0]["name"]]["running"]
    assert manager.agents[config_data["agents"][0]["name"]]["status"] == "STOPPED"

    # Test linting
    lint = await manager.lint()
    assert "duplicates" in lint

    # Clean up
    await manager.close()


@pytest.mark.asyncio
async def test_integration_scale_with_config(
    temp_config, mock_sandbox_runner, mock_agent_health_poller
):
    """Integration test: Scale based on config tags."""
    # Create manager with mock sandbox runner
    manager = CrewManager(
        sandbox_runner=mock_sandbox_runner,
        agent_health_poller=mock_agent_health_poller,
        auto_restart=False,  # Disable auto-restart for testing
    )
    # Override RBAC for testing
    manager._check_rbac = AsyncMock(return_value=True)

    # Scale agents with specific tags
    await manager.scale(2, "CrewAgentBase", tags=["test_tag"])

    # Check the scaled agents
    agents = manager.list_agents(tags=["test_tag"])
    assert len(agents) == 2

    # Verify all agents were created with correct tags
    for agent_name in agents:
        assert "test_tag" in manager.agents[agent_name]["tags"]

    # Clean up
    await manager.close()
