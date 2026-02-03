import logging
from unittest.mock import MagicMock

import pytest
import yaml
from self_fixing_engineer.simulation.core import (
    KUBERNETES_AVAILABLE,
    CircuitBreaker,
    NotificationManager,
    check_permission,
    correlated,
    execute_remotely,
    generate_correlation_id,
    get_role_permissions,
    get_user_roles,
    load_config,
    load_rbac_policy,
    main,
    run_job,
    validate_file,
    watch_mode,
)

# Mark all tests as unit tests for selective running
pytestmark = pytest.mark.unit


@pytest.fixture
def temp_dir(tmp_path):
    """Fixture for a temporary directory."""
    return tmp_path


@pytest.fixture
def mock_config_yaml(temp_dir):
    """Fixture for a mock config YAML file."""
    config_path = temp_dir / "config.yaml"
    config_data = {
        "jobs": [{"name": "test_job", "enabled": True}],
        "notifications": {"slack_webhook_url": "http://slack.com"},
    }
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)
    return str(config_path)


@pytest.fixture
def mock_rbac_yaml(temp_dir):
    """Fixture for a mock RBAC YAML file."""
    rbac_path = temp_dir / "rbac_policy.yaml"
    rbac_data = {
        "roles": [
            {"name": "admin", "permissions": [{"action": "run:*", "resource": "*"}]}
        ],
        "user_roles": {"test_user": ["admin"]},
    }
    with open(rbac_path, "w") as f:
        yaml.dump(rbac_data, f)
    return str(rbac_path)


@pytest.fixture
def mock_args():
    """Fixture for mock argparse arguments."""

    class Args:
        watch = False
        job = None
        summary = False
        remote_backend = None
        agentic = False

    return Args()


# --- Tests for load_config ---


def test_load_config_success(mock_config_yaml, monkeypatch):
    """Test successful loading and validation of config."""
    monkeypatch.setattr("self_fixing_engineer.simulation.core.PYDANTIC_AVAILABLE", True)
    config = load_config(mock_config_yaml)
    assert "jobs" in config
    assert config["jobs"][0]["name"] == "test_job"


def test_load_config_file_not_found(monkeypatch):
    """Test config loading when file not found."""
    with pytest.raises(SystemExit):
        load_config("nonexistent.yaml")


def test_load_config_no_pydantic(mock_config_yaml, monkeypatch):
    """Test config loading without Pydantic."""
    monkeypatch.setattr("self_fixing_engineer.simulation.core.PYDANTIC_AVAILABLE", False)
    config = load_config(mock_config_yaml)
    assert "jobs" in config


# --- Tests for load_rbac_policy ---


def test_load_rbac_policy_success(mock_rbac_yaml, monkeypatch):
    """Test successful loading and validation of RBAC policy."""
    monkeypatch.setattr("self_fixing_engineer.simulation.core.PYDANTIC_AVAILABLE", True)
    rbac = load_rbac_policy(mock_rbac_yaml)
    assert "roles" in rbac
    assert rbac["roles"][0]["name"] == "admin"


def test_load_rbac_policy_file_not_found(monkeypatch):
    """Test RBAC policy loading when file not found."""
    with pytest.raises(SystemExit):
        load_rbac_policy("nonexistent.yaml")


# --- Tests for get_user_roles ---


def test_get_user_roles(monkeypatch):
    """Test getting user roles from RBAC policy."""
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.core.RBAC_POLICY", {"user_roles": {"test_user": ["admin"]}}
    )
    roles = get_user_roles("test_user")
    assert roles == ["admin"]


# --- Tests for get_role_permissions ---


def test_get_role_permissions(monkeypatch):
    """Test getting role permissions from RBAC policy."""
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.core.RBAC_POLICY",
        {"roles": [{"name": "admin", "permissions": [{"action": "run:*"}]}]},
    )
    permissions = get_role_permissions("admin")
    assert permissions[0]["action"] == "run:*"


# --- Tests for check_permission ---


def test_check_permission_granted(monkeypatch):
    """Test permission granted for user role."""
    monkeypatch.setattr("self_fixing_engineer.simulation.core.CURRENT_USER", "test_user")
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.core.RBAC_POLICY",
        {
            "user_roles": {"test_user": ["admin"]},
            "roles": [
                {"name": "admin", "permissions": [{"action": "run:*", "resource": "*"}]}
            ],
        },
    )
    assert check_permission("run:agent", "*")


def test_check_permission_denied(monkeypatch):
    """Test permission denied for user."""
    monkeypatch.setattr("self_fixing_engineer.simulation.core.CURRENT_USER", "test_user")
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.core.RBAC_POLICY", {"user_roles": {"test_user": []}}
    )
    assert not check_permission("run:agent", "*")


# --- Tests for CircuitBreaker ---


def test_circuit_breaker_attempt_success():
    """Test successful operation through circuit breaker."""
    cb = CircuitBreaker(3, 10, "test", lambda msg: None)

    def func():
        return "success"

    result = cb.attempt_operation(func)
    assert result == "success"


def test_circuit_breaker_permanent_failure(monkeypatch, caplog):
    """Test permanent failure state in circuit breaker."""
    cb = CircuitBreaker(3, 10, "test", lambda msg: None)

    def failing_func():
        raise Exception("fail")

    for _ in range(7):  # Exceed double threshold
        with pytest.raises(Exception):
            cb.attempt_operation(failing_func)
    assert cb.permanent_failure
    assert "PERMANENT FAILURE for test" in caplog.text


# --- Tests for NotificationManager ---


def test_notification_manager_send_slack(monkeypatch):
    """Test sending Slack notification."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://slack.com")
    config = {"notifications": {"slack_webhook_url": "http://slack.com"}}
    manager = NotificationManager(config)
    manager.notify("slack", "test message")


# --- Tests for generate_correlation_id ---


def test_generate_correlation_id():
    """Test generation of correlation ID."""
    cid = generate_correlation_id()
    assert cid.startswith("sim-")
    assert len(cid) > 10


# --- Tests for correlated decorator ---


@pytest.mark.asyncio
async def test_correlated_decorator(caplog):
    """Test correlated decorator sets and clears correlation ID."""
    # Configure caplog to capture from the simulation.core logger
    with caplog.at_level(logging.INFO, logger="simulation.core"):

        @correlated
        def test_fn():
            return "success"

        result = test_fn()
        assert result == "success"

        # Check that correlation ID messages were logged
        assert "Starting test_fn with Correlation ID" in caplog.text
        assert "Finished test_fn with Correlation ID" in caplog.text


# --- Tests for execute_remotely ---


def test_execute_remotely_success():
    """Test successful remote execution."""
    result = execute_remotely({"name": "test"}, "kubernetes")
    # If Kubernetes is not available in the test environment, skip the test.
    if not KUBERNETES_AVAILABLE and result["status"] == "ERROR":
        pytest.skip(
            "Kubernetes not available in this environment, skipping remote execution test."
        )
    assert result["status"] == "SUBMITTED"


def test_execute_remotely_failure(monkeypatch):
    """Test remote execution failure."""
    monkeypatch.setattr("self_fixing_engineer.simulation.core.KUBERNETES_AVAILABLE", False)
    result = execute_remotely({"name": "test"}, "kubernetes")
    assert result["status"] == "ERROR"


# --- Tests for run_job ---


def test_run_job_success(monkeypatch):
    """Test successful job run."""
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.core.check_permission", MagicMock(return_value=True)
    )
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.core.run_agent", MagicMock(return_value={"status": "success"})
    )
    result = run_job({"name": "test", "enabled": True, "agentic": False})
    assert result["status"] == "success"


def test_run_job_disabled():
    """Test disabled job."""
    result = run_job({"name": "test", "enabled": False})
    assert result["status"] == "SKIPPED"


# --- Tests for watch_mode ---


def test_watch_mode_success(monkeypatch):
    """Test watch mode with watchdog."""
    monkeypatch.setattr("self_fixing_engineer.simulation.core.WATCHDOG_AVAILABLE", True)
    monkeypatch.setattr("watchdog.observers.Observer", MagicMock())
    monkeypatch.setattr("watchdog.events.FileSystemEventHandler", MagicMock())
    with pytest.raises(KeyboardInterrupt):
        watch_mode(["file.txt"], lambda: None)


def test_watch_mode_no_watchdog(monkeypatch):
    """Test watch mode without watchdog."""
    monkeypatch.setattr("self_fixing_engineer.simulation.core.WATCHDOG_AVAILABLE", False)
    with pytest.raises(SystemExit):
        watch_mode(["file.txt"], lambda: None)


# --- Tests for main ---


@pytest.mark.asyncio
async def test_main_success(mock_args, monkeypatch):
    """Test main function with successful execution."""
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.core.APP_CONFIG", {"jobs": [{"name": "test", "enabled": True}]}
    )
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.core.run_job", MagicMock(return_value={"status": "success"})
    )
    monkeypatch.setattr(
        "self_fixing_engineer.simulation.core.check_permission", MagicMock(return_value=True)
    )
    await main(mock_args)


def test_validate_file_success(temp_dir):
    """Test successful file validation."""
    file_path = temp_dir / "valid.yaml"
    with open(file_path, "w") as f:
        yaml.dump({"key": "value"}, f)
    assert validate_file(str(file_path))


def test_validate_file_not_found(temp_dir):
    """Test file validation when not found."""
    assert not validate_file("nonexistent.yaml")
