import pytest
import asyncio
from unittest.mock import MagicMock, mock_open
from self_fixing_engineer.simulation.agentic import (
    check_and_import,
    AuditLogger,
    ObjectStorageClient,
    MeshNotifier,
    EventBus,
    PolicyManager,
    rbac_enforce,
    SwarmConfig,
    GAOptimizer,
    run_simulation_swarm,
    main_async,
)
from self_fixing_engineer.simulation.plugins.dlt_clients.dlt_base import SecretsManager

pytestmark = pytest.mark.unit


@pytest.fixture
def temp_file(tmp_path):
    return tmp_path / "temp_file.txt"


@pytest.fixture
def mock_config():
    return {
        "swarm_id": "test_swarm",
        "agents": [{"id": "agent1"}, {"id": "agent2"}],
        "max_concurrency": 2,
    }


@pytest.fixture
def mock_audit_log_path(tmp_path):
    return {
        "dlq_path": tmp_path / "dlq.jsonl",
        "audit_log_path": tmp_path / "audit.jsonl",
        "integrity_file": tmp_path / "integrity.json",
    }


@pytest.fixture(autouse=True)
async def cleanup_tasks():
    yield
    tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    await asyncio.sleep(0)


@pytest.fixture
def mock_httpx(monkeypatch):
    mock_response = MagicMock()
    mock_response.json.return_value = {"result": {"allow": True}}
    monkeypatch.setattr("httpx.post", MagicMock(return_value=mock_response))


def test_check_and_import_success(monkeypatch):
    monkeypatch.setattr("importlib.import_module", MagicMock(return_value=MagicMock()))
    module = check_and_import("os")
    assert module is not None


def test_check_and_import_critical_failure(monkeypatch):
    monkeypatch.setattr("importlib.import_module", MagicMock(side_effect=ImportError))
    with pytest.raises(SystemExit):
        check_and_import("nonexistent", critical=True)


def test_secrets_manager_get_secret_from_env(monkeypatch):
    monkeypatch.setenv("TEST_SECRET", "value")
    manager = SecretsManager()
    value = manager.get_secret("TEST_SECRET")
    assert value == "value"
    assert "TEST_SECRET" in manager.cache


def test_secrets_manager_get_required_secret_missing(monkeypatch):
    with pytest.raises(Exception):
        manager = SecretsManager()
        manager.get_secret("MISSING_SECRET", required=True)


@pytest.mark.asyncio
async def test_audit_logger_init_file_backend(mock_audit_log_path, monkeypatch):
    monkeypatch.setenv("AUDIT_BACKEND", "file")
    logger = AuditLogger()
    assert logger.backend == "file"


@pytest.mark.asyncio
async def test_audit_logger_log_event_file(mock_audit_log_path, monkeypatch):
    monkeypatch.setenv("AUDIT_BACKEND", "file")
    monkeypatch.setenv("AUDIT_LOG_PATH", str(mock_audit_log_path["audit_log_path"]))
    monkeypatch.setenv("AUDIT_DLQ_PATH", str(mock_audit_log_path["dlq_path"]))
    monkeypatch.setenv(
        "AUDIT_INTEGRITY_FILE", str(mock_audit_log_path["integrity_file"])
    )

    mock_file = mock_open()
    monkeypatch.setattr("builtins.open", mock_file)

    logger = AuditLogger()

    # Override async background call with direct await for test control
    async def direct_send(event):
        await logger._send_to_backend(event)

    monkeypatch.setattr(logger, "_send_with_retries", direct_send)

    await logger.log_event("test_event", payload={"key": "value"})

    mock_file().write.assert_called_once()


@pytest.mark.asyncio
async def test_object_storage_save_load_success(monkeypatch):
    secrets = {
        "OBJ_STORE_BACKEND": "minio",
        "MINIO_ENDPOINT": "http://localhost:9000",
        "MINIO_ACCESS_KEY": "dummy-access-key",
        "MINIO_SECRET_KEY": "dummy-secret-key",
        "MINIO_SECURE": "false",
        "OBJ_BUCKET": "agentic",
    }

    monkeypatch.setattr(
        "self_fixing_engineer.simulation.agentic.SECRETS_MANAGER.get_secret",
        lambda key, default=None, required=True: secrets[key],
    )

    mock_minio = MagicMock()
    mock_minio.bucket_exists.return_value = True
    mock_minio.put_object.return_value = None
    mock_minio.get_object.return_value.read.return_value = b"test_data"

    monkeypatch.setattr("minio.Minio", MagicMock(return_value=mock_minio))

    client = ObjectStorageClient()
    data = b"test_data"
    key = "test_key"
    await client.save_object(key, data)
    loaded = await client.load_object(key)
    assert loaded == data


@pytest.mark.asyncio
async def test_mesh_notifier_notify_slack(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN", "fake_token")
    mock_httpx = MagicMock()
    mock_httpx.post.return_value = MagicMock(status_code=200)
    monkeypatch.setattr("httpx.Client", MagicMock(return_value=mock_httpx))
    notifier = MeshNotifier()
    await notifier.notify("test_msg")


@pytest.mark.asyncio
async def test_event_bus_publish_memory():
    bus = EventBus()
    await bus.connect()
    await bus.publish("test_topic", {"key": "value"})


def test_policy_manager_has_permission_no_opa(mock_httpx):
    manager = PolicyManager()
    assert manager.has_permission("agent", "action", "resource")


@pytest.mark.asyncio
async def test_rbac_enforce_allowed(mock_httpx):
    async def test_fn():
        return "success"

    decorated = rbac_enforce("agent", "action", "resource")(test_fn)
    result = await decorated()
    assert result == "success"


def test_swarm_config_validation():
    config = {
        "swarm_id": "valid_id",
        "agents": [{"id": "agent1"}],
        "max_concurrency": 2,
    }
    SwarmConfig(**config)


@pytest.mark.asyncio
async def test_ga_optimizer_evolve(monkeypatch):
    class MockAdapter:
        def evaluate(self, individual):
            return (sum(individual),)

    adapter = MockAdapter()
    optimizer = GAOptimizer()
    monkeypatch.setattr(
        "deap.algorithms.eaSimple", MagicMock(return_value=([1, 2, 3], {}))
    )
    mock_hof = MagicMock()
    mock_hof.__getitem__.return_value = [1, 2, 3]
    monkeypatch.setattr(optimizer.tools, "HallOfFame", MagicMock(return_value=mock_hof))
    best = await optimizer.evolve(adapter)
    assert len(best) == 3


@pytest.mark.asyncio
async def test_run_simulation_swarm_success(mock_config):
    results = await run_simulation_swarm(mock_config)
    assert results["status"] == "completed"
    assert len(results["results"]) == 2


@pytest.mark.skip(
    reason="OperatorAPI not available in self_fixing_engineer.simulation.agentic"
)
async def test_operator_api_health_status(monkeypatch):
    pass


@pytest.mark.asyncio
async def test_main_async_health(monkeypatch, caplog):
    monkeypatch.setattr("sys.argv", ["agentic.py", "--mode", "health"])
    caplog.set_level("INFO")
    await main_async()
    assert "Agentic core is running" in caplog.text
