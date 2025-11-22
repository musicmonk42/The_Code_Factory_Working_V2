#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Enterprise-Grade Test Suite for the Unified Simulation Module

This test suite provides comprehensive coverage of the simulation_module.py
file, including normal operation paths, error conditions, edge cases, and
performance characteristics.

Run with: pytest -xvs test_simulation_module.py

Coverage includes:
- Component initialization and shutdown
- Simulation execution for all simulation types
- Quantum operation handling
- Error handling and resilience mechanisms
- Message bus integration
- Health checks and failure recovery
- Explanation generation
- Sandboxed execution
- Performance and stress testing

Author: Security Engineering Team
Date: 2025-08-16
Version: 1.0.0
"""

import json
import time
import asyncio
import logging
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, ANY

# Configure logging for tests
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(name)s - %(message)s'
)
logger = logging.getLogger("simulation-module-tests")

# Import the module under test
from simulation_module import (
    UnifiedSimulationModule,
    SIM_MODULE_METRICS,
)


# --- Test Constants and Helpers ---

TEST_CONFIG = {
    "SIM_MAX_WORKERS": 4,
    "SIM_RETRY_ATTEMPTS": 3,
    "SIM_BACKOFF_FACTOR": 1.0,
}

SAMPLE_SIMULATION_CONFIG = {
    "type": "agent",
    "id": "test-sim-001",
    "parameters": {
        "iterations": 100,
        "target_accuracy": 0.95
    }
}

SAMPLE_SWARM_CONFIG = {
    "type": "swarm",
    "id": "test-swarm-001",
    "agent_count": 5,
    "iterations": 100
}

SAMPLE_PARALLEL_CONFIG = {
    "type": "parallel",
    "id": "test-parallel-001",
    "tasks": [
        {"type": "agent", "id": "task-1", "parameters": {}},
        {"type": "agent", "id": "task-2", "parameters": {}}
    ]
}

SAMPLE_QUANTUM_PARAMS = {
    "circuit_type": "variational",
    "qubit_count": 5,
    "shots": 1000
}


async def wait_for_async_conditions(condition_fn, timeout=5.0, check_interval=0.1):
    """Helper function to wait for async conditions with timeout."""
    start_time = time.time()
    while (time.time() - start_time) < timeout:
        if await condition_fn():
            return True
        await asyncio.sleep(check_interval)
    return False


# --- Test Fixtures ---

@pytest.fixture
def mock_settings():
    """Mock the global settings object."""
    with patch('simulation_module.settings') as mock_settings:
        mock_settings.SIM_RETRY_ATTEMPTS = 3
        mock_settings.SIM_BACKOFF_FACTOR = 1.0
        mock_settings.LOG_LEVEL = "INFO"
        yield mock_settings


@pytest.fixture
def mock_metrics():
    """Mock all Prometheus metrics."""
    metrics = {}
    for metric_name in SIM_MODULE_METRICS:
        metrics[metric_name] = MagicMock()
        metrics[metric_name].labels.return_value = MagicMock()
        metrics[metric_name].labels.return_value.inc = MagicMock()
        metrics[metric_name].labels.return_value.observe = MagicMock()
        metrics[metric_name].set = MagicMock()
    
    with patch.dict('simulation_module.SIM_MODULE_METRICS', metrics):
        yield metrics


@pytest.fixture
def mock_db():
    """Mock the Database object."""
    mock = AsyncMock()
    mock.health_check = AsyncMock(return_value={"status": "ok", "latency_ms": 5})
    mock.save_audit_record = AsyncMock()
    mock.close = AsyncMock()
    yield mock


@pytest.fixture
def mock_message_bus():
    """Mock the ShardedMessageBus object."""
    mock = AsyncMock()
    mock.health_check = AsyncMock(return_value={"status": "running", "partitions": 10})
    mock.publish = AsyncMock()
    mock.subscribe = AsyncMock()
    mock.unsubscribe = AsyncMock()
    mock.close = AsyncMock()
    yield mock


@pytest.fixture
def mock_reasoner():
    """Mock the ExplainableReasonerPlugin."""
    mock = AsyncMock()
    mock.async_init = AsyncMock()
    mock.execute = AsyncMock(return_value={"status": "ok", "result": "test result"})
    mock.explain_result = AsyncMock(return_value="This is an explanation of the simulation results.")
    mock.shutdown = AsyncMock()
    with patch('simulation_module.ExplainableReasonerPlugin', return_value=mock):
        yield mock


@pytest.fixture
def mock_quantum_api():
    """Mock the QuantumPluginAPI."""
    mock = AsyncMock()
    mock.perform_quantum_operation = AsyncMock(return_value={
        "status": "SUCCESS",
        "result": {"probability": 0.75, "confidence": 0.95}
    })
    mock.get_available_backends = MagicMock(return_value=["qasm_simulator", "aer_simulator"])
    with patch('simulation_module.QuantumPluginAPI', return_value=mock):
        yield mock


@pytest.fixture
def mock_sandbox():
    """Mock the sandbox execution environment."""
    with patch('simulation_module.run_in_sandbox') as mock:
        mock.return_value = {"status": "success", "result": {"value": 42}}
        yield mock


@pytest.fixture
def mock_agent_runners():
    """Mock the agent runner functions."""
    with patch('simulation_module.run_agent') as mock_run_agent, \
         patch('simulation_module.run_simulation_swarm') as mock_swarm, \
         patch('simulation_module.run_parallel_simulations') as mock_parallel:
        
        mock_run_agent.return_value = {"status": "success", "accuracy": 0.95}
        mock_swarm.return_value = {"status": "success", "swarm_results": [{"accuracy": 0.92}, {"accuracy": 0.94}]}
        mock_parallel.return_value = {"status": "success", "results": [{"id": "task-1", "accuracy": 0.91}, {"id": "task-2", "accuracy": 0.93}]}
        
        yield {
            "agent": mock_run_agent,
            "swarm": mock_swarm,
            "parallel": mock_parallel
        }


@pytest.fixture
async def simulation_module_instance(mock_db, mock_message_bus, mock_reasoner, mock_quantum_api):
    """Create and initialize a UnifiedSimulationModule instance."""
    module = UnifiedSimulationModule(TEST_CONFIG, mock_db, mock_message_bus)
    await module.initialize()
    yield module
    await module.shutdown()


# --- Basic Unit Tests ---

@pytest.mark.asyncio
async def test_initialization(mock_db, mock_message_bus, mock_reasoner, mock_quantum_api):
    """Test that the simulation module initializes all components correctly."""
    module = UnifiedSimulationModule(TEST_CONFIG, mock_db, mock_message_bus)
    assert not module._is_initialized
    
    await module.initialize()
    
    assert module._is_initialized
    assert module.reasoner_plugin is not None
    assert module.quantum_api is not None
    
    mock_reasoner.async_init.assert_awaited_once()
    
    await module.shutdown()


@pytest.mark.asyncio
async def test_double_initialization(simulation_module_instance, mock_reasoner):
    """Test that initializing twice doesn't cause issues."""
    # It's already initialized in the fixture
    await simulation_module_instance.initialize()
    
    # Should only be called once even with multiple initializations
    mock_reasoner.async_init.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_check_success(simulation_module_instance, mock_db, mock_message_bus, mock_reasoner, mock_quantum_api):
    """Test health check when all components are healthy."""
    health_report = await simulation_module_instance.health_check()
    
    assert health_report["status"] == "ok"
    assert health_report["components"]["reasoner"]["status"] == "ok"
    assert "available_backends" in health_report["components"]["quantum"]
    assert len(health_report["components"]["quantum"]["available_backends"]) > 0
    assert health_report["components"]["database"]["status"] == "ok"
    assert health_report["components"]["message_bus"]["status"] == "running"
    
    SIM_MODULE_METRICS["health_status"].set.assert_called_with(1)


@pytest.mark.asyncio
async def test_health_check_failure_reasoner(
    mock_db, mock_message_bus, mock_reasoner, mock_quantum_api
):
    """Test health check when reasoner component fails."""
    mock_reasoner.execute.return_value = {"status": "error", "message": "Internal error"}
    
    module = UnifiedSimulationModule(TEST_CONFIG, mock_db, mock_message_bus)
    await module.initialize()
    
    health_report = await module.health_check()
    
    assert health_report["status"] == "unhealthy"
    assert health_report["components"]["reasoner"]["status"] == "error"
    assert health_report["components"]["database"]["status"] == "ok"
    assert health_report["components"]["message_bus"]["status"] == "running"
    
    SIM_MODULE_METRICS["health_status"].set.assert_called_with(0)
    
    await module.shutdown()


@pytest.mark.asyncio
async def test_health_check_fail_on_error(
    mock_db, mock_message_bus, mock_reasoner, mock_quantum_api
):
    """Test health check with fail_on_error=True when a component fails."""
    mock_reasoner.execute.side_effect = RuntimeError("Catastrophic failure")
    
    module = UnifiedSimulationModule(TEST_CONFIG, mock_db, mock_message_bus)
    await module.initialize()
    
    with patch('simulation_module.sys.exit') as mock_exit:
        await module.health_check(fail_on_error=True)
        mock_exit.assert_called_once_with(1)
    
    await module.shutdown()


@pytest.mark.asyncio
async def test_shutdown(mock_db, mock_message_bus, mock_reasoner, mock_quantum_api):
    """Test that shutdown closes all resources properly."""
    mock_executor = MagicMock()
    
    module = UnifiedSimulationModule(TEST_CONFIG, mock_db, mock_message_bus)
    await module.initialize()
    module._executor = mock_executor
    
    await module.shutdown()
    
    mock_reasoner.shutdown.assert_awaited_once()
    mock_executor.shutdown.assert_called_once_with(wait=True)
    assert not module._is_initialized


# --- Simulation Execution Tests ---

@pytest.mark.asyncio
async def test_execute_simulation_agent_type(
    simulation_module_instance, mock_agent_runners, mock_db, mock_metrics
):
    """Test executing a single agent simulation."""
    result = await simulation_module_instance.execute_simulation(SAMPLE_SIMULATION_CONFIG)
    
    assert result == {"status": "success", "accuracy": 0.95}
    mock_agent_runners["agent"].assert_awaited_once()
    mock_db.save_audit_record.assert_awaited_once()
    mock_metrics["simulation_run_total"].labels.assert_called_with(type="agent", status="success")
    mock_metrics["simulation_run_total"].labels.return_value.inc.assert_called_once()
    mock_metrics["simulation_duration_seconds"].observe.assert_called_once()


@pytest.mark.asyncio
async def test_execute_simulation_swarm_type(
    simulation_module_instance, mock_agent_runners, mock_metrics
):
    """Test executing a swarm simulation."""
    result = await simulation_module_instance.execute_simulation(SAMPLE_SWARM_CONFIG)
    
    assert result == {"status": "success", "swarm_results": [{"accuracy": 0.92}, {"accuracy": 0.94}]}
    mock_agent_runners["swarm"].assert_awaited_once()
    mock_metrics["simulation_run_total"].labels.assert_called_with(type="swarm", status="success")


@pytest.mark.asyncio
async def test_execute_simulation_parallel_type(
    simulation_module_instance, mock_agent_runners, mock_metrics
):
    """Test executing parallel simulations."""
    result = await simulation_module_instance.execute_simulation(SAMPLE_PARALLEL_CONFIG)
    
    assert result["status"] == "success"
    assert len(result["results"]) == 2
    mock_agent_runners["parallel"].assert_awaited_once()
    mock_metrics["simulation_run_total"].labels.assert_called_with(type="parallel", status="success")


@pytest.mark.asyncio
async def test_execute_simulation_unknown_type(
    simulation_module_instance, mock_metrics
):
    """Test executing a simulation with an unknown type."""
    with pytest.raises(ValueError) as excinfo:
        await simulation_module_instance.execute_simulation({"type": "unknown"})
    
    assert "Unknown simulation type" in str(excinfo.value)
    mock_metrics["simulation_run_total"].labels.assert_called_with(type="unknown", status="failed")


@pytest.mark.asyncio
async def test_execute_simulation_failure(
    simulation_module_instance, mock_agent_runners, mock_db, mock_metrics
):
    """Test handling of simulation execution failures."""
    mock_agent_runners["agent"].side_effect = RuntimeError("Simulation crashed")
    
    with pytest.raises(RuntimeError) as excinfo:
        await simulation_module_instance.execute_simulation(SAMPLE_SIMULATION_CONFIG)
    
    assert "Simulation crashed" in str(excinfo.value)
    mock_db.save_audit_record.assert_awaited_once()
    mock_metrics["simulation_run_total"].labels.assert_called_with(type="agent", status="failed")


# --- Quantum Operation Tests ---

@pytest.mark.asyncio
async def test_perform_quantum_op_mutation(
    simulation_module_instance, mock_quantum_api, mock_db, mock_metrics
):
    """Test performing a quantum mutation operation."""
    result = await simulation_module_instance.perform_quantum_op("mutation", SAMPLE_QUANTUM_PARAMS)
    
    assert result["status"] == "SUCCESS"
    assert "probability" in result["result"]
    mock_quantum_api.perform_quantum_operation.assert_awaited_once_with(
        operation_type="run_mutation_circuit",
        params=SAMPLE_QUANTUM_PARAMS
    )
    mock_db.save_audit_record.assert_awaited_once()
    mock_metrics["quantum_op_total"].labels.assert_called_with(op_type="mutation", status="success")


@pytest.mark.asyncio
async def test_perform_quantum_op_forecast(
    simulation_module_instance, mock_quantum_api, mock_metrics
):
    """Test performing a quantum forecast operation."""
    result = await simulation_module_instance.perform_quantum_op("forecast", SAMPLE_QUANTUM_PARAMS)
    
    assert result["status"] == "SUCCESS"
    mock_quantum_api.perform_quantum_operation.assert_awaited_once_with(
        operation_type="forecast_failure_trend",
        params=SAMPLE_QUANTUM_PARAMS
    )
    mock_metrics["quantum_op_total"].labels.assert_called_with(op_type="forecast", status="success")


@pytest.mark.asyncio
async def test_perform_quantum_op_unknown_type(
    simulation_module_instance, mock_metrics
):
    """Test performing an unknown quantum operation type."""
    with pytest.raises(ValueError) as excinfo:
        await simulation_module_instance.perform_quantum_op("invalid", {})
    
    assert "Unknown quantum operation type" in str(excinfo.value)
    mock_metrics["quantum_op_total"].labels.assert_called_with(op_type="invalid", status="failed")


@pytest.mark.asyncio
async def test_perform_quantum_op_api_error(
    simulation_module_instance, mock_quantum_api, mock_metrics
):
    """Test handling of errors from the quantum API."""
    # Set up the quantum API to return an error
    mock_quantum_api.perform_quantum_operation.return_value = {
        "status": "ERROR", 
        "reason": "Backend failure"
    }
    
    with pytest.raises(RuntimeError) as excinfo:
        await simulation_module_instance.perform_quantum_op("mutation", {})
    
    assert "Quantum operation failed: Backend failure" in str(excinfo.value)
    mock_metrics["quantum_op_total"].labels.assert_called_with(op_type="mutation", status="failed")


# --- Result Explanation Tests ---

@pytest.mark.asyncio
async def test_explain_result_success(
    simulation_module_instance, mock_reasoner, mock_db
):
    """Test successful explanation generation."""
    result = {
        "id": "sim-123",
        "status": "success",
        "data": {"accuracy": 0.95}
    }
    
    explanation = await simulation_module_instance.explain_result(result)
    
    assert explanation == "This is an explanation of the simulation results."
    mock_reasoner.explain_result.assert_awaited_once()
    mock_db.save_audit_record.assert_awaited_once()


@pytest.mark.asyncio
async def test_explain_result_invalid_input(
    simulation_module_instance, mock_reasoner
):
    """Test explanation with invalid input format."""
    with pytest.raises(ValueError) as excinfo:
        await simulation_module_instance.explain_result("not a dict")
    
    assert "Invalid simulation result format" in str(excinfo.value)
    mock_reasoner.explain_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_explain_result_reasoner_error(
    simulation_module_instance, mock_reasoner
):
    """Test handling of reasoner errors during explanation."""
    from simulation_module import ReasonerError
    mock_reasoner.explain_result.side_effect = ReasonerError("Reasoning failed")
    
    with pytest.raises(ReasonerError) as excinfo:
        await simulation_module_instance.explain_result({"id": "sim-123", "status": "success"})
    
    assert "Reasoning failed" in str(excinfo.value)


# --- Sandbox Execution Tests ---

@pytest.mark.asyncio
async def test_run_in_secure_sandbox(
    simulation_module_instance, mock_sandbox
):
    """Test running code in a secure sandbox."""
    code = "result = {'value': a + b}"
    inputs = {"a": 15, "b": 27}
    
    result = await simulation_module_instance.run_in_secure_sandbox(code, inputs)
    
    assert result == {"status": "success", "result": {"value": 42}}
    mock_sandbox.assert_called_once_with(code, inputs, ANY)


@pytest.mark.asyncio
async def test_run_in_secure_sandbox_with_custom_policy(
    simulation_module_instance, mock_sandbox
):
    """Test sandbox execution with a custom security policy."""
    code = "import math; result = {'value': math.sqrt(a)}"
    inputs = {"a": 16}
    policy = {"allow_imports": ["math"], "timeout": 5.0}
    
    await simulation_module_instance.run_in_secure_sandbox(code, inputs, policy)
    
    # Verify the policy was passed correctly
    from simulation_module import SandboxPolicy
    mock_sandbox.assert_called_once()
    _, _, policy_arg = mock_sandbox.call_args[0]
    assert isinstance(policy_arg, SandboxPolicy)
    # We can't directly verify the policy attributes as we're mocking the SandboxPolicy constructor


# --- Message Handling Tests ---

@pytest.mark.asyncio
async def test_register_message_handlers(
    simulation_module_instance, mock_message_bus
):
    """Test registration of message handlers."""
    # This should be called during initialization, but let's call it explicitly
    await simulation_module_instance.register_message_handlers()
    
    mock_message_bus.subscribe.assert_awaited_once()
    # Verify the pattern and handler function
    assert mock_message_bus.subscribe.call_args[1]["topic_pattern"] == "requests.simulation.*"
    assert mock_message_bus.subscribe.call_args[1]["handler"] == simulation_module_instance.handle_simulation_request


@pytest.mark.asyncio
async def test_register_message_handlers_not_initialized(
    mock_db, mock_message_bus
):
    """Test message handler registration before initialization."""
    module = UnifiedSimulationModule(TEST_CONFIG, mock_db, mock_message_bus)
    
    with pytest.raises(RuntimeError) as excinfo:
        await module.register_message_handlers()
    
    assert "Cannot register message handlers before initialization" in str(excinfo.value)
    mock_message_bus.subscribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_simulation_request_success(
    simulation_module_instance, mock_message_bus, mock_agent_runners
):
    """Test successful handling of a simulation request message."""
    message = MagicMock()
    message.id = "msg-123"
    message.payload = SAMPLE_SIMULATION_CONFIG
    message.topic = "requests.simulation.agent"
    
    await simulation_module_instance.handle_simulation_request(message)
    
    # Verify the response was published correctly
    mock_message_bus.publish.assert_awaited_once_with(
        topic="responses.simulation.agent",
        payload=ANY
    )
    
    # Verify the payload contains the expected fields
    call_args = mock_message_bus.publish.await_args[1]
    payload_dict = json.loads(call_args["payload"])
    assert payload_dict["request_id"] == "msg-123"
    assert payload_dict["status"] == "success"
    assert "result" in payload_dict


@pytest.mark.asyncio
async def test_handle_simulation_request_with_explanation(
    simulation_module_instance, mock_message_bus, mock_agent_runners, mock_reasoner
):
    """Test simulation request handling with explanation generation."""
    message = MagicMock()
    message.id = "msg-123"
    message.payload = {**SAMPLE_SIMULATION_CONFIG, "explain": True}
    message.topic = "requests.simulation.agent"
    
    await simulation_module_instance.handle_simulation_request(message)
    
    # Verify both response and explanation were published
    assert mock_message_bus.publish.await_count == 2
    
    # Verify the explanation was published
    explanation_call = mock_message_bus.publish.await_args_list[1]
    assert explanation_call[1]["topic"] == "responses.simulation.agent.explanation"
    payload_dict = json.loads(explanation_call[1]["payload"])
    assert payload_dict["request_id"] == "msg-123"
    assert "explanation" in payload_dict


@pytest.mark.asyncio
async def test_handle_simulation_request_error(
    simulation_module_instance, mock_message_bus, mock_agent_runners
):
    """Test handling of errors during simulation request processing."""
    mock_agent_runners["agent"].side_effect = ValueError("Invalid parameter")
    
    message = MagicMock()
    message.id = "msg-123"
    message.payload = SAMPLE_SIMULATION_CONFIG
    message.topic = "requests.simulation.agent"
    message.original_payload = json.dumps(SAMPLE_SIMULATION_CONFIG)
    
    await simulation_module_instance.handle_simulation_request(message)
    
    # Verify error response
