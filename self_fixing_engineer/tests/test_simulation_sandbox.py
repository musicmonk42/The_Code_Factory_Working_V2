import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from self_fixing_engineer.simulation.sandbox import (
    AUDIT_LOG_FILE,
    ContainerValidationConfig,
    SandboxPolicy,
    _active_sandboxes,
    _get_audit_hmac_key,
    _periodic_external_service_check,
    _start_background_tasks,
    burst_to_cloud,
    check_external_services_async,
    cleanup_sandbox,
    deploy_to_kubernetes,
    log_audit,
    run_chaos_experiment,
    run_in_docker_sandbox,
    run_in_local_process_sandbox,
    run_in_podman_sandbox,
    run_in_sandbox,
    verify_audit_log_integrity,
)

pytestmark = pytest.mark.unit

# To ensure the global key is reset between tests
_audit_hmac_key = None


@pytest.fixture
def temp_dir(tmp_path):
    """Fixture for a temporary directory."""
    return tmp_path


@pytest.fixture
def mock_policy():
    """Fixture for a mock SandboxPolicy."""
    return SandboxPolicy()


@pytest.fixture
def mock_audit_log(temp_dir):
    """
    Fixture for mock audit log files.
    Clears the audit log file but not the integrity file.
    """
    audit_log = temp_dir / "sandbox_audit.log"
    integrity_file = temp_dir / "sandbox_audit_integrity.json"
    audit_log.write_text("", encoding="utf-8")  # Clear the log file only

    # Clear global audit log to prevent stale entries
    global_audit_log = Path(AUDIT_LOG_FILE)
    if global_audit_log.exists():
        global_audit_log.write_text("", encoding="utf-8")

    return str(audit_log), str(integrity_file)


@pytest.fixture(autouse=True)
def reset_audit_hmac_key():
    """Fixture to reset the global HMAC key before each test."""
    global _audit_hmac_key
    _audit_hmac_key = None
    yield


# --- Tests for SandboxPolicy ---


def test_sandbox_policy_validation_success():
    """Test successful validation of SandboxPolicy."""
    policy = {
        "seccomp_profile": "test.json",
        "apparmor_profile": "test",
        "privileged": False,
        "network_disabled": True,
        "run_as_user": "1000:1000",
        "allow_write": False,
        "allow_privileged_containers": False,
    }
    SandboxPolicy(**policy)


def test_sandbox_policy_run_as_user_not_root():
    """Test validation failure for root user in SandboxPolicy."""
    policy = {"run_as_user": "0:0"}
    with pytest.raises(ValueError):
        SandboxPolicy(**policy)


# --- Tests for ContainerValidationConfig ---


def test_container_validation_config_success():
    """Test successful validation of ContainerValidationConfig."""
    config = {
        "image": "python:3.9-slim",
        "command": ["python"],
        "kubernetes_pod_manifest": {
            "spec": {"containers": [{"securityContext": {"privileged": False}}]}
        },
    }
    ContainerValidationConfig(**config)


def test_container_validation_config_image_not_whitelist():
    """Test validation failure for untrusted image."""
    config = {"image": "unknown_image", "command": ["python"]}
    with pytest.raises(ValueError):
        ContainerValidationConfig(**config)


# --- Tests for log_audit ---


def test_log_audit(mock_audit_log, monkeypatch):
    """Test logging an audit event."""
    audit_log, integrity_file = mock_audit_log
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.AUDIT_LOG_FILE", audit_log)
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.AUDIT_LOG_INTEGRITY_FILE", integrity_file)
    log_audit({"event": "test"})
    with open(audit_log, "r") as f:
        logged = json.loads(f.read())
    assert "event" in logged["event"]
    assert "signature" in logged


# --- Tests for verify_audit_log_integrity ---


@patch("self_fixing_engineer.simulation.sandbox.glob.glob", return_value=[])
def test_verify_audit_log_integrity_recent(mock_glob, mock_audit_log, monkeypatch):
    """Test audit log integrity verification with a recent integrity file."""
    audit_log, integrity_file = mock_audit_log
    monkeypatch.setenv("SANDBOX_AUDIT_HMAC_KEY", "test_key")
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.AUDIT_LOG_FILE", audit_log)
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.AUDIT_LOG_INTEGRITY_FILE", integrity_file)

    with open(integrity_file, "w", encoding="utf-8") as f:
        json.dump({"last_verification_time": datetime.utcnow().isoformat()}, f)

    log_audit({"event": "test"})

    assert verify_audit_log_integrity()


@patch("self_fixing_engineer.simulation.sandbox.glob.glob", return_value=[])
def test_verify_audit_log_integrity_mismatch(mock_glob, mock_audit_log, monkeypatch):
    """Test audit log integrity verification with signature mismatch."""
    audit_log, integrity_file = mock_audit_log
    monkeypatch.setenv("SANDBOX_AUDIT_HMAC_KEY", "test_key")
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.AUDIT_LOG_FILE", audit_log)
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.AUDIT_LOG_INTEGRITY_FILE", integrity_file)
    with open(audit_log, "w", encoding="utf-8") as f:
        f.write(json.dumps({"event": {"event": "test"}, "signature": "invalid"}) + "\n")
    assert not verify_audit_log_integrity()


# --- Tests for cleanup_sandbox ---


@pytest.mark.asyncio
async def test_cleanup_sandbox_docker(monkeypatch):
    """Test cleanup of Docker sandbox."""
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.DOCKER_AVAILABLE", True)
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.sandbox.docker.from_env",
        MagicMock(
            return_value=MagicMock(
                containers=MagicMock(
                    get=MagicMock(
                        return_value=MagicMock(stop=MagicMock(), remove=MagicMock())
                    )
                )
            )
        ),
    )
    _active_sandboxes["test_id"] = {"type": "docker", "container_id": "test_container"}
    await cleanup_sandbox("test_id")
    assert "test_id" not in _active_sandboxes


# --- Tests for run_in_docker_sandbox ---


@pytest.mark.asyncio
async def test_run_in_docker_sandbox_success(monkeypatch):
    """Test successful Docker sandbox execution."""
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.DOCKER_AVAILABLE", True)
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.sandbox.docker.from_env",
        MagicMock(
            return_value=MagicMock(
                containers=MagicMock(
                    run=MagicMock(
                        return_value=MagicMock(
                            wait=MagicMock(return_value={"StatusCode": 0}),
                            logs=MagicMock(return_value=b"output"),
                        )
                    )
                )
            )
        ),
    )
    result = await run_in_docker_sandbox(["echo", "test"], "/tmp")
    assert result["status"] == "COMPLETED"
    assert "output" in result["stdout"]


# --- Tests for run_in_podman_sandbox ---


@pytest.mark.asyncio
async def test_run_in_podman_sandbox_success(monkeypatch):
    """Test successful Podman sandbox execution."""
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.PODMAN_AVAILABLE", True)
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.sandbox.podman.Client",
        MagicMock(
            return_value=MagicMock(
                containers=MagicMock(
                    run=MagicMock(
                        return_value=MagicMock(
                            wait=MagicMock(return_value={"StatusCode": 0}),
                            logs=MagicMock(return_value=b"output"),
                        )
                    )
                )
            )
        ),
    )
    result = await run_in_podman_sandbox(["echo", "test"], "/tmp")
    assert result["status"] == "COMPLETED"
    assert "output" in result["stdout"]


# --- Tests for deploy_to_kubernetes ---


@pytest.mark.asyncio
async def test_deploy_to_kubernetes_success(monkeypatch):
    """Test successful Kubernetes deployment."""
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.KUBERNETES_AVAILABLE", True)
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.kube_config.load_kube_config", MagicMock())
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.sandbox.client.CoreV1Api",
        MagicMock(
            return_value=MagicMock(
                read_namespaced_pod_status=MagicMock(
                    return_value=MagicMock(status=MagicMock(phase="Succeeded"))
                ),
                read_namespaced_pod_log=MagicMock(return_value="output"),
                list_namespaced_pod=MagicMock(
                    return_value=MagicMock(
                        items=[MagicMock(metadata=MagicMock(name="mock-pod"))]
                    )
                ),
            )
        ),
    )
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.sandbox.client.BatchV1Api",
        MagicMock(
            return_value=MagicMock(
                create_namespaced_job=MagicMock(),
                read_namespaced_job_status=MagicMock(
                    return_value=MagicMock(status=MagicMock(succeeded=1))
                ),
                delete_namespaced_job=MagicMock(),
            )
        ),
    )
    result = await deploy_to_kubernetes(["echo", "test"], "/tmp")
    assert result["status"] == "COMPLETED"
    assert "output" in result["stdout"]


# --- Tests for run_in_local_process_sandbox ---


@pytest.mark.asyncio
async def test_run_in_local_process_sandbox_success(monkeypatch):
    """Test successful local process sandbox execution."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(
            return_value=MagicMock(
                returncode=0, communicate=AsyncMock(return_value=(b"output", b""))
            )
        ),
    )
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.sandbox.client.BatchV1Api",
        MagicMock(
            return_value=MagicMock(
                create_namespaced_job=MagicMock(),
                read_namespaced_job_status=MagicMock(
                    return_value=MagicMock(status=MagicMock(succeeded=1))
                ),
            )
        ),
    )
    result = await run_in_local_process_sandbox(["echo", "test"], "/tmp")
    assert result["status"] == "COMPLETED"
    assert "output" in result["stdout"]


# --- Tests for burst_to_cloud ---


@pytest.mark.asyncio
async def test_burst_to_cloud_aws_success(monkeypatch):
    """Test successful cloud burst to AWS."""
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.AWS_AVAILABLE", True)
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.sandbox.boto3.client",
        MagicMock(
            return_value=MagicMock(
                submit_job=MagicMock(return_value={"jobId": "test_id"})
            )
        ),
    )
    result = await burst_to_cloud({"job_name": "test"}, "aws")
    assert result["status"] == "CLOUD_BURST_INITIATED"
    assert result["provider"] == "aws"


# --- Tests for run_chaos_experiment ---


@pytest.mark.asyncio
async def test_run_chaos_experiment_success(monkeypatch):
    """Test successful chaos experiment run."""
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.GREMLIN_AVAILABLE", True)
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.sandbox.gremlin.GremlinClient", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setenv("GREMLIN_TEAM_ID", "test")
    monkeypatch.setenv("GREMLIN_API_KEY", "test")
    result = await run_chaos_experiment("test_app", "cpu_hog")
    assert result["status"] == "STARTED"


# --- Tests for run_in_sandbox ---


@pytest.mark.asyncio
async def test_run_in_sandbox_success(monkeypatch):
    """Test successful sandbox execution."""
    mock_backend = AsyncMock(return_value={"status": "COMPLETED"})
    with patch.dict("self_fixing_engineer.simulation.sandbox._sandbox_backends", {"docker": mock_backend}):
        result = await run_in_sandbox("docker", ["echo", "test"], "/tmp")
        assert result["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_run_in_sandbox_no_backends(monkeypatch):
    """Test sandbox execution with no backends available."""
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.DOCKER_AVAILABLE", False)
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.PODMAN_AVAILABLE", False)
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.KUBERNETES_AVAILABLE", False)
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.SECCOMP_AVAILABLE", False)
    result = await run_in_sandbox("docker", ["echo", "test"], "/tmp")
    assert result["status"] == "ERROR"


# --- Tests for _get_audit_hmac_key ---


def test_get_audit_hmac_key_env(monkeypatch):
    """Test getting audit HMAC key from environment."""
    monkeypatch.setenv("SANDBOX_AUDIT_HMAC_KEY", "test_key")
    key = _get_audit_hmac_key()
    assert key == b"test_key"


# --- Tests for check_external_services_async ---


@pytest.mark.asyncio
async def test_check_external_services_async_success(monkeypatch):
    """Test successful external services check."""
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.DOCKER_AVAILABLE", True)
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.sandbox.docker.from_env",
        MagicMock(return_value=MagicMock(ping=MagicMock())),
    )
    await check_external_services_async()  # No exception raised


# --- Tests for _periodic_external_service_check ---


@pytest.mark.asyncio
async def test_periodic_external_service_check(monkeypatch):
    """Test periodic external service check."""
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.check_external_services_async", AsyncMock())
    task = asyncio.create_task(_periodic_external_service_check(interval_seconds=1))
    await asyncio.sleep(2)
    task.cancel()


# --- Tests for _start_background_tasks ---


@pytest.mark.asyncio
async def test_start_background_tasks(monkeypatch):
    """Test starting background tasks."""
    monkeypatch.setattr("self_fixing_engineer.simulation.sandbox.check_external_services_async", AsyncMock())
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.sandbox._periodic_external_service_check", AsyncMock()
    )
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.sandbox._periodic_audit_log_verification", AsyncMock()
    )
    await _start_background_tasks()
