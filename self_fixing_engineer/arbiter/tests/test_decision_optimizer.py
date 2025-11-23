# test_decision_optimizer.py

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from cryptography.fernet import Fernet

# Mock modules before imports
sys.modules["envs"] = MagicMock()
sys.modules["envs.evolution"] = MagicMock()

# Mock ArrayBackend
mock_array_backend = MagicMock()
mock_array_backend.array = lambda x: np.array(x)
mock_array_backend.asnumpy = lambda x: (
    np.array(x) if not isinstance(x, np.ndarray) else x
)
sys.modules["arbiter.arbiter_array_backend"] = MagicMock()
sys.modules["arbiter.arbiter_array_backend"].ConcreteArrayBackend = MagicMock(
    return_value=mock_array_backend
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from arbiter.arbiter_plugin_registry import PLUGIN_REGISTRY

# Mock all dependencies
from arbiter.config import ArbiterConfig
from arbiter.decision_optimizer import Agent, DecisionOptimizer, Task, safe_serialize


@pytest.fixture
def mock_dependencies():
    """Create all required mocks."""
    settings = MagicMock(spec=ArbiterConfig)
    settings.REDIS_URL = "redis://localhost:6379"
    settings.ENCRYPTION_KEY = MagicMock()
    settings.ENCRYPTION_KEY.get_secret_value.return_value = Fernet.generate_key()

    plugin_registry = MagicMock(spec=PLUGIN_REGISTRY)
    plugin_registry.get.return_value = None

    logger = MagicMock()

    return settings, plugin_registry, logger


@pytest.fixture
def optimizer(mock_dependencies):
    """Create optimizer without background tasks."""
    settings, plugin_registry, logger = mock_dependencies

    opt = DecisionOptimizer(
        plugin_registry=plugin_registry, settings=settings, logger=logger
    )

    # Disable background tasks
    opt._refresh_task = None
    opt.refresh_strategies = AsyncMock()

    yield opt

    # Cleanup
    if hasattr(opt, "shutdown"):
        opt.shutdown()


# Basic Tests


def test_initialization(optimizer):
    """Test basic initialization."""
    assert optimizer is not None
    assert isinstance(optimizer.event_log, list)
    assert optimizer.config["max_tasks_per_agent"] == 10


def test_task_creation():
    """Test Task dataclass."""
    task = Task(id="t1", priority=0.5)
    assert task.id == "t1"
    assert task.priority == 0.5


def test_agent_creation():
    """Test Agent dataclass."""
    agent = Agent(id="a1", skills={"python"}, max_compute=10.0)
    assert agent.id == "a1"
    assert "python" in agent.skills


def test_safe_serialize():
    """Test serialization utility."""
    data = {"set": {1, 2}, "array": np.array([3, 4])}
    result = safe_serialize(data)
    assert sorted(result["set"]) == [1, 2]
    assert result["array"] == [3, 4]


@pytest.mark.asyncio
async def test_prioritize_and_allocate(optimizer):
    """Test basic prioritize and allocate."""
    agents = [
        Agent(id="a1", skills={"python"}, max_compute=10.0),
        Agent(id="a2", skills={"js"}, max_compute=5.0),
    ]
    tasks = [
        Task(id="t1", priority=1.0, required_skills={"python"}, estimated_compute=2.0),
        Task(id="t2", priority=0.5, required_skills={"js"}, estimated_compute=3.0),
        Task(id="t3", priority=0.8, required_skills={"rust"}, estimated_compute=1.0),
    ]

    assignments, unassigned = await optimizer.prioritize_and_allocate(agents, tasks)

    assert "a1" in assignments
    assert "t1" in assignments["a1"]
    assert "t3" in unassigned  # No agent has rust skill


@pytest.mark.asyncio
async def test_prioritize_tasks_simple(optimizer):
    """Test task prioritization without dependencies."""
    optimizer.policy_engine = None  # Disable policy checks

    agents = [Agent(id="a1", skills=set(), max_compute=10.0)]
    tasks = [
        Task(id="t1", priority=0.3),
        Task(id="t2", priority=0.9),
        Task(id="t3", priority=0.5),
    ]

    prioritized = await optimizer.prioritize_tasks(agents, tasks)

    assert len(prioritized) == 3
    assert prioritized[0].priority == 0.9
    assert prioritized[1].priority == 0.5
    assert prioritized[2].priority == 0.3


@pytest.mark.asyncio
async def test_allocate_resources_simple(optimizer):
    """Test resource allocation."""
    optimizer.policy_engine = None
    optimizer.human_in_loop = None

    agents = [Agent(id="a1", skills={"python"}, max_compute=10.0, current_load=0.0)]
    tasks = [
        Task(id="t1", priority=1.0, required_skills={"python"}, estimated_compute=2.0)
    ]

    with patch(
        "arbiter.decision_optimizer.get_system_metrics_async",
        AsyncMock(return_value={"cpu_percent": 50}),
    ):
        assignments = await optimizer.allocate_resources(agents, tasks)

    assert "a1" in assignments
    assert len(assignments["a1"]) == 1


@pytest.mark.asyncio
async def test_compute_trust_score(optimizer):
    """Test trust score computation."""
    optimizer.policy_engine = None

    context = {"mfa_enabled": True, "device_registered": True, "risk_level": "low"}

    score = await optimizer.compute_trust_score(context, "user1")

    assert 0.0 <= score <= 1.0
    assert score > 0.5  # Should be high with positive factors


@pytest.mark.asyncio
async def test_process_remediation_low_risk(optimizer):
    """Test low-risk remediation processing."""
    optimizer.policy_engine = None

    proposal = {
        "type": "import_fix",
        "risk_level": "low",
        "suggested_fixer": "self_healing_import_fixer",
    }

    await optimizer.process_remediation_proposal(proposal)

    # Should complete without errors
    assert len(optimizer.event_log) > 0


@pytest.mark.asyncio
async def test_get_metrics(optimizer):
    """Test metrics retrieval."""
    metrics = await optimizer.get_metrics()

    assert "event_log_size" in metrics
    assert metrics["event_log_size"] == 0


@pytest.mark.asyncio
async def test_coordinate_arbiters_basic(optimizer):
    """Test basic arbiter coordination."""
    agent = Agent(id="a1", skills=set(), max_compute=10.0)
    agent.arbiter_instance = MagicMock()
    agent.arbiter_instance.propose_action = AsyncMock(return_value={"action": "test"})
    agent.arbiter_instance.receive_context = AsyncMock()

    result = await optimizer.coordinate_arbiters([agent], {"test": "context"})

    assert "a1" in result
    agent.arbiter_instance.propose_action.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
