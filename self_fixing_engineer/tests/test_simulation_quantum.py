# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import all necessary components for testing
from self_fixing_engineer.simulation.quantum import (
    ForecastFailureTrendParams,
    QuantumPluginAPI,
    QuantumRLAgent,
    RunMutationCircuitParams,
    alert_operator,
    backend_client_pool,
    check_any_backend_available,
    check_backend_health,
    load_quantum_credentials,
    quantum_forecast_failure,
    run_quantum_mutation,
)

# Assuming prometheus_client is installed for tests, or you can mock it.
# To handle optional dependencies in tests, you can set the module-level
# variables before each test.


# Mark all tests as unit tests for selective running
pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def cleanup_backend_pool():
    """Fixture to ensure the backend client pool is cleaned up after each test."""
    yield
    asyncio.run(backend_client_pool.close())


# --- Tests for get_or_create_metric ---
@pytest.mark.asyncio
async def test_get_or_create_metric_success(monkeypatch):
    """Test successful creation of a metric."""
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.PROMETHEUS_AVAILABLE", True)

    # Reload the module to apply the monkeypatch for global variables
    import importlib

    import self_fixing_engineer.simulation.quantum as simulation_quantum

    importlib.reload(simulation_quantum)

    metric = simulation_quantum.get_or_create_metric(
        simulation_quantum.Histogram, "test_hist", "Test histogram"
    )
    assert metric._name == "test_hist"


# --- Tests for check_any_backend_available ---
def test_check_any_backend_available_success(monkeypatch):
    """Test successful backend availability check."""
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.QISKIT_AVAILABLE", True)
    check_any_backend_available()  # No exception raised


def test_check_any_backend_available_failure(monkeypatch):
    """Test failure when no backends available."""
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.QISKIT_AVAILABLE", False)
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.DWAVE_AVAILABLE", False)
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.SCIPY_AVAILABLE", False)
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.DEAP_AVAILABLE", False)
    with pytest.raises(RuntimeError):
        check_any_backend_available()


# --- Tests for alert_operator ---
@pytest.mark.asyncio
async def test_alert_operator(caplog):
    """Test alert_operator logs critical message."""
    await alert_operator("Test alert", "WARNING")
    assert "[OPS ALERT] Test alert" in caplog.text


# --- Tests for load_quantum_credentials ---
@pytest.mark.asyncio
async def test_load_quantum_credentials_success(monkeypatch):
    """Test successful loading of quantum credentials."""
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.BOTO3_AVAILABLE", True)
    mock_boto_client = MagicMock()
    mock_boto_client.get_secret_value = MagicMock(
        return_value={"SecretString": '{"token": "fake_token"}'}
    )
    monkeypatch.setattr("boto3.client", MagicMock(return_value=mock_boto_client))

    credentials = await load_quantum_credentials("dwave")
    assert credentials["token"] == "fake_token"


@pytest.mark.asyncio
async def test_load_quantum_credentials_failure(monkeypatch):
    """Test failure to load quantum credentials."""
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.BOTO3_AVAILABLE", False)

    # Re-instantiate credential_manager to apply the monkeypatch
    from self_fixing_engineer.simulation.quantum import CredentialManager

    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.credential_manager", CredentialManager())

    with pytest.raises(RuntimeError):
        await load_quantum_credentials("dwave")


# --- Tests for check_backend_health ---
@pytest.mark.asyncio
async def test_check_backend_health_qiskit_success(monkeypatch):
    """Test successful health check for Qiskit backend."""
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.QISKIT_AVAILABLE", True)
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.quantum.backend_client_pool.get_client",
        AsyncMock(return_value=MagicMock(status=MagicMock(return_value=True))),
    )
    assert await check_backend_health("qiskit")


@pytest.mark.asyncio
async def test_check_backend_health_dwave_success(monkeypatch):
    """Test successful health check for D-Wave backend."""
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.DWAVE_AVAILABLE", True)
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.quantum.backend_client_pool.get_client",
        AsyncMock(
            return_value=MagicMock(
                sampler=MagicMock(
                    client=MagicMock(is_solvent=MagicMock(return_value=True))
                )
            )
        ),
    )
    assert await check_backend_health("dwave")


# --- Tests for RunMutationCircuitParams ---
@pytest.mark.asyncio
async def test_run_mutation_circuit_params_validation_success():
    """Test successful validation of RunMutationCircuitParams."""
    params = {
        "code_file": "./examples/file.py",
        "backend": "auto",
        "n_qubits": 5,
        "n_vars": 5,
        "backend_config": {},
    }
    with patch("os.path.isfile", return_value=True):
        RunMutationCircuitParams(**params)


@pytest.mark.asyncio
async def test_run_mutation_circuit_params_validation_failure():
    """Test validation failure in RunMutationCircuitParams."""
    params = {"code_file": "../invalid"}
    with pytest.raises(ValueError):
        RunMutationCircuitParams(**params)


# --- Tests for ForecastFailureTrendParams ---
@pytest.mark.asyncio
async def test_forecast_failure_trend_params_validation_success():
    """Test successful validation of ForecastFailureTrendParams."""
    params = {"trend_data": [1.0, 2.0, 3.0]}
    ForecastFailureTrendParams(**params)


@pytest.mark.asyncio
async def test_forecast_failure_trend_params_validation_failure():
    """Test validation failure in ForecastFailureTrendParams."""
    params = {"trend_data": [1, "invalid"]}
    with pytest.raises(ValueError):
        ForecastFailureTrendParams(**params)


# --- Tests for run_quantum_mutation ---
@pytest.mark.asyncio
async def test_run_quantum_mutation_success(monkeypatch):
    """Test successful quantum mutation with Qiskit."""
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.QISKIT_AVAILABLE", True)
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.QuantumCircuit", MagicMock())
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.quantum.backend_client_pool.get_client",
        AsyncMock(
            return_value=MagicMock(
                run=MagicMock(
                    return_value=MagicMock(
                        result=MagicMock(
                            return_value=MagicMock(
                                get_counts=MagicMock(
                                    return_value={"000": 512, "111": 512}
                                )
                            )
                        )
                    )
                )
            )
        ),
    )
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.quantum.transpile", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.audit_logger", AsyncMock())

    with patch("os.path.isfile", return_value=True):
        result = await run_quantum_mutation("./examples/test.py", "qiskit")

    assert result["status"] == "COMPLETED"
    assert "backend" in result


@pytest.mark.asyncio
async def test_run_quantum_mutation_no_backend(monkeypatch):
    """Test quantum mutation with no backend available."""
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.QISKIT_AVAILABLE", False)
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.DWAVE_AVAILABLE", False)
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.SCIPY_AVAILABLE", False)
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.DEAP_AVAILABLE", False)
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.audit_logger", AsyncMock())

    with patch("os.path.isfile", return_value=True):
        result = await run_quantum_mutation("./examples/test.py")

    assert result["status"] == "ERROR"


# --- Tests for quantum_forecast_failure ---
@pytest.mark.asyncio
async def test_quantum_forecast_failure_success(monkeypatch):
    """Test successful quantum forecast with Qiskit."""
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.QISKIT_AVAILABLE", True)
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.QuantumCircuit", MagicMock())
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.quantum.backend_client_pool.get_client",
        AsyncMock(
            return_value=MagicMock(
                run=MagicMock(
                    return_value=MagicMock(
                        result=MagicMock(
                            return_value=MagicMock(
                                get_counts=MagicMock(
                                    return_value={"000": 128, "111": 128}
                                )
                            )
                        )
                    )
                )
            )
        ),
    )
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.quantum.transpile", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.audit_logger", AsyncMock())

    result = await quantum_forecast_failure([1.0, 2.0, 3.0])

    assert result["status"] == "COMPLETED"
    assert "forecast" in result


# --- Tests for QuantumRLAgent ---
# Check if torch is available for conditional test execution
try:
    import torch
    TORCH_AVAILABLE_FOR_TESTS = True
except ImportError:
    TORCH_AVAILABLE_FOR_TESTS = False


@pytest.mark.skipif(not TORCH_AVAILABLE_FOR_TESTS, reason="PyTorch not available")
def test_quantum_rl_agent_init_success():
    """Test successful initialization of QuantumRLAgent."""
    agent = QuantumRLAgent(10, 5)
    assert agent.actor is not None
    assert agent.critic is not None


def test_quantum_rl_agent_init_failure(monkeypatch):
    """Test failure to initialize QuantumRLAgent without Torch."""
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.TORCH_RL_AVAILABLE", False)
    with pytest.raises(RuntimeError):
        QuantumRLAgent(10, 5)


# --- Tests for QuantumPluginAPI ---
def test_quantum_plugin_api_get_available_backends(monkeypatch):
    """Test getting available backends from QuantumPluginAPI."""
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.QISKIT_AVAILABLE", True)
    monkeypatch.setattr("self_fixing_engineer.simulation.quantum.DWAVE_AVAILABLE", True)
    api = QuantumPluginAPI()
    backends = api.get_available_backends()
    assert "qiskit" in backends
    assert "dwave" in backends


@pytest.mark.asyncio
async def test_quantum_plugin_api_perform_quantum_operation(monkeypatch):
    """Test performing quantum operation via QuantumPluginAPI."""
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.quantum.run_quantum_mutation",
        AsyncMock(return_value={"status": "COMPLETED"}),
    )
    api = QuantumPluginAPI()
    api._initialized = True  # Manually initialize for this test
    result = await api.perform_quantum_operation(
        "run_mutation_circuit", {"code_file": "test.py"}
    )
    assert result["status"] == "COMPLETED"
