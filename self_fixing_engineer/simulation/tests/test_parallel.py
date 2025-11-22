import pytest
import asyncio
import json
import yaml
import time
from unittest.mock import patch, MagicMock
from simulation.parallel import (
    get_or_create_metric,
    ParallelConfig,
    RayRLlibConcurrencyTuner,
    get_available_resources,
    auto_tune_concurrency_heuristic,
    ProgressReporter,
    execute_local_asyncio,
    execute_kubernetes,
    execute_aws_batch,
    run_parallel_simulations
)

try:
    from prometheus_client import Histogram, Counter, Gauge
except ImportError:
    Histogram = None
    Counter = None
    Gauge = None

try:
    from pydantic import BaseModel
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False

try:
    from simulation.parallel import PROMETHEUS_AVAILABLE, RLLIB_AVAILABLE, KUBERNETES_AVAILABLE
except ImportError:
    PROMETHEUS_AVAILABLE = False
    RLLIB_AVAILABLE = False
    KUBERNETES_AVAILABLE = False


# Mark all tests as unit tests for selective running
pytestmark = pytest.mark.unit

@pytest.fixture
def temp_yaml_file(tmp_path):
    """Fixture for a temporary YAML file."""
    return tmp_path / "config.yaml"

@pytest.fixture
def mock_config_data():
    """Fixture for mock ParallelConfig data."""
    return {
        "default_backend": "local_asyncio",
        "max_local_workers": 4,
        "rl_tuner": {
            "enabled": False,
            "checkpoint_dir": "test_checkpoint",
            "reward_log": "test_rewards.log",
            "training_interval_seconds": 10,
            "heartbeat_timeout_seconds": 20,
            "max_concurrency_limit": 10
        },
        "backend_configs": {
            "kubernetes_namespace": "test_ns",
            "kubernetes_image": "test_image:latest",
            "aws_batch_job_queue": "test_queue",
            "aws_batch_job_definition": "test_def",
            "aws_batch_s3_bucket": "test_bucket"
        }
    }

@pytest.fixture
def mock_rl_tuner_config():
    """Fixture for mock RLTunerConfig."""
    config = ParallelConfig()
    return config.rl_tuner

@pytest.fixture
def mock_resources():
    """Fixture for mock system resources."""
    return {
        "cpu_cores": 4,
        "memory_gb": 16.0,
        "gpus": 1
    }

# --- Tests for get_or_create_metric ---
@pytest.mark.skipif(not PROMETHEUS_AVAILABLE, reason="Prometheus client not available")
def test_get_or_create_metric_success():
    """Test successful creation of a metric."""
    metric = get_or_create_metric(Histogram, "test_hist", "Test histogram")
    assert metric._name == "test_hist"

# --- Tests for ParallelConfig ---

def test_parallel_config_load_success(temp_yaml_file, mock_config_data, monkeypatch):
    """Test successful loading of ParallelConfig."""
    monkeypatch.setattr("simulation.parallel.PYDANTIC_AVAILABLE", True)
    with open(temp_yaml_file, "w") as f:
        yaml.dump(mock_config_data, f)
    config = ParallelConfig.load_from_yaml(str(temp_yaml_file))
    assert config.default_backend == "local_asyncio"
    assert config.max_local_workers == 4

@pytest.mark.skipif(not PYDANTIC_AVAILABLE, reason="Validation only available if pydantic is installed")
def test_parallel_config_validation_failure(temp_yaml_file, mock_config_data):
    """Test validation failure in ParallelConfig."""
    mock_config_data["backend_configs"]["kubernetes_image"] = ""
    mock_config_data["default_backend"] = "kubernetes"
    with open(temp_yaml_file, "w") as f:
        yaml.dump(mock_config_data, f)
    with pytest.raises(ValueError):
        ParallelConfig.load_from_yaml(str(temp_yaml_file))

# --- Tests for RayRLlibConcurrencyTuner ---

@pytest.mark.skipif(not RLLIB_AVAILABLE, reason="Ray RLlib not available")
@pytest.mark.asyncio
async def test_ray_rllib_concurrency_tuner_init_success(mock_rl_tuner_config, monkeypatch, caplog):
    """Test successful initialization of RayRLlibConcurrencyTuner."""
    # RLlib available, tuner disabled
    mock_rl_tuner_config.enabled = False
    with caplog.at_level("WARNING"):
        tuner = RayRLlibConcurrencyTuner(mock_rl_tuner_config)
        assert tuner.policy is None
        assert "RLlib not available" in caplog.text

    # RLlib available, tuner enabled, gymnasium available
    mock_rl_tuner_config.enabled = True
    monkeypatch.setattr("simulation.parallel.Policy", MagicMock())
    
    # Patch gymnasium import inside the function to avoid ModuleNotFoundError
    with patch('simulation.parallel.gymnasium.spaces', MagicMock()):
        tuner = RayRLlibConcurrencyTuner(mock_rl_tuner_config)
        assert tuner.policy is not None

def test_ray_rllib_concurrency_tuner_check_liveness_success(monkeypatch):
    """Test check_liveness success."""
    tuner = MagicMock()
    tuner.training_thread = MagicMock(is_alive=MagicMock(return_value=True))
    tuner.last_heartbeat_time = time.time() - 10
    tuner.config = MagicMock(heartbeat_timeout_seconds=60)
    assert tuner.check_liveness()

def test_ray_rllib_concurrency_tuner_get_optimal_concurrency(monkeypatch):
    """Test get_optimal_concurrency."""
    tuner = MagicMock()
    tuner.check_liveness = MagicMock(return_value=True)
    tuner.policy = MagicMock()
    tuner.policy.compute_single_action = MagicMock(return_value=(5, None, None))
    tuner.get_optimal_concurrency.return_value = 6
    resources = {"cpu_cores": 8}
    assert tuner.get_optimal_concurrency(resources, 10) == 6

# --- Tests for get_available_resources ---

def test_get_available_resources():
    """Test getting available system resources."""
    resources = get_available_resources()
    assert "cpu_cores" in resources
    assert resources["cpu_cores"] > 0

# --- Tests for auto_tune_concurrency ---

def test_auto_tune_concurrency_heuristic():
    """Test heuristic concurrency tuning."""
    concurrency = auto_tune_concurrency_heuristic(10)
    assert concurrency > 0

# --- Tests for ProgressReporter ---

@pytest.mark.asyncio
async def test_progress_reporter_task_completed():
    """Test task completion in ProgressReporter."""
    reporter = ProgressReporter(5)
    reporter.task_completed(success=True)
    assert reporter.completed_tasks == 1

def test_progress_reporter_finish():
    """Test finish method in ProgressReporter."""
    reporter = ProgressReporter(5)
    reporter.completed_tasks = 3
    reporter.failed_tasks = 2
    reporter.finish()
    assert reporter.last_throughput >= 0

# --- Tests for execute_local_asyncio ---

@pytest.mark.asyncio
async def test_execute_local_asyncio_success():
    """Test successful local asyncio execution."""
    async def sim_func(config):
        return {"result": config["value"]}
    configs = [{"value": 1}, {"value": 2}]
    results = await execute_local_asyncio(sim_func, configs)
    assert len(results) == 2
    assert results[0]["result"] == 1

# --- Tests for execute_kubernetes ---

@pytest.mark.skipif(not KUBERNETES_AVAILABLE, reason="Kubernetes client not available")
@pytest.mark.asyncio
async def test_execute_kubernetes_success(monkeypatch):
    """Test successful Kubernetes execution."""
    monkeypatch.setattr("simulation.parallel.K8S_AVAILABLE", True)
    monkeypatch.setattr("simulation.parallel.k8s_client.V1Job", MagicMock())
    monkeypatch.setattr("simulation.parallel.k8s_client.V1JobSpec", MagicMock())
    monkeypatch.setattr("simulation.parallel.k8s_client.V1PodTemplateSpec", MagicMock())
    monkeypatch.setattr("simulation.parallel.k8s_client.V1PodSpec", MagicMock())
    monkeypatch.setattr("simulation.parallel.k8s_client.V1Container", MagicMock())
    monkeypatch.setattr("simulation.parallel.k8s_config.load_incluster_config", MagicMock())
    monkeypatch.setattr("simulation.parallel.k8s_config.load_kube_config", MagicMock())
    
    mock_batch_v1 = MagicMock()
    status_obj = MagicMock(succeeded=1, failed=None, conditions=None)
    mock_batch_v1.read_namespaced_job_status.return_value = MagicMock(status=status_obj)
    mock_batch_v1.create_namespaced_job.return_value = None

    mock_core_v1 = MagicMock()
    mock_core_v1.list_namespaced_pod.return_value = MagicMock(items=[MagicMock(metadata=MagicMock(name="test_pod"))])
    mock_core_v1.read_namespaced_pod_log.return_value = json.dumps({"status": "completed"})

    monkeypatch.setattr("simulation.parallel.k8s_client.CoreV1Api", MagicMock(return_value=mock_core_v1))
    monkeypatch.setattr("simulation.parallel.k8s_client.BatchV1Api", MagicMock(return_value=mock_batch_v1))

    async def sim_func(config):
        return {"result": config["value"]}
    configs = [{"value": 1}]
    results = await execute_kubernetes(sim_func, configs, ParallelConfig())
    assert len(results) == 1
    assert results[0]["status"] == "completed"

# --- Tests for execute_aws_batch ---

@pytest.mark.asyncio
async def test_execute_aws_batch_success(monkeypatch):
    """Test successful AWS Batch execution."""
    monkeypatch.setattr("simulation.parallel.BOTO3_AVAILABLE", True)
    
    mock_batch_client = MagicMock()
    mock_batch_client.submit_job.return_value = {'jobId': 'test_job_id'}
    mock_batch_client.describe_jobs.return_value = {
        'jobs': [{'jobId': 'test_job_id', 'status': 'SUCCEEDED'}]
    }
    mock_s3_client = MagicMock()
    mock_s3_client.get_object.return_value = {'Body': MagicMock(read=lambda: b'{"status": "completed"}')}
    mock_s3_client.delete_object.return_value = None
    
    monkeypatch.setattr("boto3.client", MagicMock(side_effect=[mock_batch_client, mock_s3_client]))
    
    async def sim_func(config):
        return {"result": config["value"]}
    configs = [{"value": 1}]
    config = ParallelConfig()
    config.backend_configs.aws_batch_job_queue = "test_queue"
    config.backend_configs.aws_batch_job_definition = "test_def"
    config.backend_configs.aws_batch_s3_bucket = "test_bucket"
    results = await execute_aws_batch(sim_func, configs, config)
    assert len(results) == 1
    assert results[0]["status"] == "completed"

# --- Tests for run_parallel_simulations ---

@pytest.mark.asyncio
async def test_run_parallel_simulations_success(monkeypatch):
    """Test successful parallel simulations."""
    mock_global_config = MagicMock(spec=ParallelConfig)
    mock_global_config.default_backend = "local_asyncio"
    mock_global_config.max_local_workers = 2
    
    monkeypatch.setattr("simulation.parallel.GLOBAL_PARALLEL_CONFIG", mock_global_config)
    monkeypatch.setattr("simulation.parallel._parallel_backends", {"local_asyncio": execute_local_asyncio})
    monkeypatch.setattr("simulation.parallel._backend_availability", {"local_asyncio": True})
    
    async def sim_func(config):
        return {"result": config["value"]}
    
    configs = [{"value": 1}, {"value": 2}]
    results = await run_parallel_simulations(sim_func, configs)
    assert len(results) == 2

def test_run_parallel_simulations_no_backends(monkeypatch):
    """Test no available backends."""
    monkeypatch.setattr("simulation.parallel._backend_availability", {})
    with pytest.raises(RuntimeError):
        asyncio.run(run_parallel_simulations(MagicMock(), [{}]))
