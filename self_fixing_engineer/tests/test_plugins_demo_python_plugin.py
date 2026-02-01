import importlib
import importlib.util
import logging
import os
import re
import sys
from typing import Dict
from unittest.mock import MagicMock, patch

import pytest

# Dynamically import the plugin to be tested
try:
    from plugins import demo_python_plugin
except ImportError:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    from plugins import demo_python_plugin

# Import components from the plugin
from plugins.demo_python_plugin import (
    PLUGIN_API,
    PLUGIN_MANIFEST,
    logger,
    plugin_health,
)


# --- Test Setup ---
@pytest.fixture(autouse=True)
def setup_logging():
    """Set up logging to capture output for tests."""
    logger.handlers = []
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield
    logger.handlers = []


@pytest.fixture
def mock_audit_logger():
    """Mock the audit logger to capture log events."""
    mock = MagicMock()
    with patch("demo_python_plugin.audit_logger", mock):
        yield mock


@pytest.fixture
def mock_alert_operator():
    """Mock the alert_operator function."""
    with patch("demo_python_plugin.alert_operator") as mock:
        yield mock


@pytest.fixture
def mock_scrub_secrets():
    """Mock the scrub_secrets function."""
    with patch("demo_python_plugin.scrub_secrets") as mock:
        mock.side_effect = (
            lambda x: x
        )  # Default behavior is to return the input un-scrubbed
        yield mock


@pytest.fixture
def mock_importlib():
    """Mock importlib.util.find_spec for dependency checks."""
    with patch("importlib.util.find_spec") as mock:
        yield mock


@pytest.fixture
def set_env(monkeypatch):
    """Fixture to set environment variables for tests."""

    def _set_env(vars: Dict[str, str]):
        for key, value in vars.items():
            monkeypatch.setenv(key, value)

    return _set_env


# --- Manifest Tests ---
def test_plugin_manifest_structure():
    """Test that the plugin manifest has all required fields."""
    required_fields = [
        "name",
        "version",
        "description",
        "entrypoint",
        "type",
        "author",
        "capabilities",
        "permissions",
        "dependencies",
        "min_core_version",
        "max_core_version",
        "health_check",
        "api_version",
        "license",
        "homepage",
        "tags",
        "generated_with",
        "is_demo_plugin",
        "signature",
    ]
    for field in required_fields:
        assert field in PLUGIN_MANIFEST, f"Manifest missing required field: {field}"
    assert PLUGIN_MANIFEST["name"] == "demo_python_plugin"
    assert PLUGIN_MANIFEST["is_demo_plugin"] is True
    assert "do_not_deploy_to_prod" in PLUGIN_MANIFEST["tags"]
    assert PLUGIN_MANIFEST["type"] == "python"
    assert isinstance(PLUGIN_MANIFEST["generated_with"], dict)
    assert "wizard_version" in PLUGIN_MANIFEST["generated_with"]


def test_manifest_version_format():
    """Test that version fields follow semantic versioning."""
    version_pattern = r"^\d+\.\d+\.\d+$"
    assert re.match(
        version_pattern, PLUGIN_MANIFEST["version"]
    ), "Invalid version format"
    assert re.match(
        version_pattern, PLUGIN_MANIFEST["min_core_version"]
    ), "Invalid min_core_version format"
    assert re.match(
        version_pattern, PLUGIN_MANIFEST["max_core_version"]
    ), "Invalid max_core_version format"


# --- Production Mode Tests ---
def test_production_mode_block(monkeypatch, mock_audit_logger, mock_alert_operator):
    """Test that the plugin aborts in PRODUCTION_MODE."""
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    with pytest.raises(SystemExit) as exc:
        importlib.reload(demo_python_plugin)
    assert exc.value.code == 1
    mock_alert_operator.assert_called_with(
        "CRITICAL: Demo plugin 'demo_python_plugin.py' detected in PRODUCTION_MODE. Aborting.",
        level="CRITICAL",
    )


# --- Health Check Tests ---
def test_plugin_health_healthy(mock_importlib, mock_audit_logger, mock_scrub_secrets):
    """Test plugin_health when all dependencies are present."""
    mock_importlib.return_value = MagicMock()
    health_status = plugin_health()
    assert health_status["status"] == "ok"
    assert health_status["message"] == "Demo Python plugin is healthy!"
    assert health_status["details"]["runtime_check"] == "passed"
    mock_scrub_secrets.assert_called_with(health_status)
    mock_audit_logger.log_event.assert_called_with(
        "plugin_health_check",
        plugin="demo_python_plugin",
        status="ok",
        details=mock_scrub_secrets.return_value,
    )


def test_plugin_health_degraded(mock_importlib, mock_audit_logger, mock_scrub_secrets):
    """Test plugin_health when dependencies are missing."""
    mock_importlib.side_effect = lambda x: (
        None if x in ["requests", "numpy"] else MagicMock()
    )
    health_status = plugin_health()
    assert health_status["status"] == "degraded"
    assert "Missing optional dependencies: requests, numpy" in health_status["message"]
    assert health_status["details"]["missing_dependencies"] == ["requests", "numpy"]
    mock_scrub_secrets.assert_called_with(health_status)
    mock_audit_logger.log_event.assert_called_with(
        "plugin_health_degraded",
        plugin="demo_python_plugin",
        reason="missing_dependencies",
        details=mock_scrub_secrets.return_value,
    )


def test_plugin_health_unhealthy_runtime(
    mock_importlib, mock_audit_logger, mock_alert_operator, mock_scrub_secrets, set_env
):
    """Test plugin_health with a simulated runtime failure."""
    set_env({"DEMO_PLUGIN_HEALTH_FAIL": "true"})
    health_status = plugin_health()
    assert health_status["status"] == "unhealthy"
    assert (
        "Runtime check failed: Simulated runtime failure for demo plugin."
        in health_status["message"]
    )
    assert health_status["details"]["runtime_check"] == "failed"
    mock_alert_operator.assert_called_with(
        "WARNING: Demo plugin 'demo_python_plugin' health check failed: Simulated runtime failure for demo plugin.",
        level="WARNING",
    )
    mock_scrub_secrets.assert_called_with("Simulated runtime failure for demo plugin.")
    mock_audit_logger.log_event.assert_called_with(
        "plugin_health_unhealthy",
        plugin="demo_python_plugin",
        reason="runtime_failure",
        error=mock_scrub_secrets.return_value,
    )


def test_plugin_health_unhandled_exception(
    mock_importlib, mock_audit_logger, mock_alert_operator, mock_scrub_secrets
):
    """Test plugin_health with an unhandled exception."""
    mock_importlib.side_effect = Exception("Unexpected error")
    health_status = plugin_health()
    assert health_status["status"] == "unhealthy"
    assert "Runtime check failed: Unexpected error" in health_status["message"]
    assert health_status["details"]["runtime_check"] == "failed"
    mock_alert_operator.assert_called_with(
        "WARNING: Demo plugin 'demo_python_plugin' health check failed: Unexpected error",
        level="WARNING",
    )
    mock_scrub_secrets.assert_called_with("Unexpected error")
    mock_audit_logger.log_event.assert_called_with(
        "plugin_health_unhealthy",
        plugin="demo_python_plugin",
        reason="runtime_failure",
        error=mock_scrub_secrets.return_value,
    )


# --- API Tests ---
def test_plugin_api_hello(mock_audit_logger):
    """Test the PLUGIN_API.hello method."""
    api = PLUGIN_API()
    result = api.hello()
    assert result == "Hello from the safe mode demo Python plugin!"
    mock_audit_logger.log_event.assert_not_called()


def test_plugin_api_hello_qa_mode(mock_audit_logger, set_env):
    """Test the PLUGIN_API.hello method in QA mode."""
    set_env({"RUN_QA_TESTS": "true"})
    api = PLUGIN_API()
    result = api.hello()
    assert result == "Hello from the safe mode demo Python plugin!"
    mock_audit_logger.log_event.assert_called_with(
        "demo_plugin_hello_executed_in_qa", plugin="demo_python_plugin"
    )


# --- Security Tests ---
def test_no_hardcoded_secrets():
    """Test that the plugin does not contain hardcoded secrets."""
    with open("demo_python_plugin.py", "r") as f:
        code = f.read()
    forbidden_patterns = [
        r"api_key\s*=\s*['\"][^'\"]+['\"]",
        r"password\s*=\s*['\"][^'\"]+['\"]",
        r"secret\s*=\s*['\"][^'\"]+['\"]",
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, code), f"Hardcoded secret detected: {pattern}"


# --- Integration Tests (Verifying fallback functionality) ---
def test_plugin_load_with_missing_core_utils(monkeypatch):
    """Test plugin behavior when core_utils is missing."""
    # This test simulates the case where the initial `try...except` block fails.
    # We need to reload the module after patching sys.modules
    # Use a dummy importable module to replace core_utils
    monkeypatch.setitem(sys.modules, "core_utils", None)
    importlib.reload(demo_python_plugin)

    # Now verify that the fallback functions are in place
    demo_python_plugin.alert_operator("Test message", level="INFO")
    # The fallback alert_operator calls logger.critical
    # Check that logger.critical was called.
    with patch.object(demo_python_plugin.logger, "critical") as mock_critical:
        demo_python_plugin.alert_operator("Test message", level="INFO")
        mock_critical.assert_called_with("[OPS ALERT - INFO] Test message")

    # Check that the fallback audit logger works
    mock_audit_logger_instance = demo_python_plugin.audit_logger
    assert isinstance(mock_audit_logger_instance, demo_python_plugin.DummyAuditLogger)

    with patch.object(demo_python_plugin.logger, "info") as mock_info:
        mock_audit_logger_instance.log_event("test_event", detail="test")
        mock_info.assert_called_with(
            "[AUDIT_LOG_DISABLED] test_event: {'detail': 'test'}"
        )


# --- Cleanup Fixture ---
@pytest.fixture(autouse=True)
def cleanup_env(monkeypatch):
    """Clean up environment variables and module state after each test."""
    yield
    for key in ["PRODUCTION_MODE", "DEMO_PLUGIN_HEALTH_FAIL", "RUN_QA_TESTS"]:
        if key in os.environ:
            monkeypatch.delenv(key, raising=False)
    # Reload the module to its original state to avoid side effects between tests
    importlib.reload(demo_python_plugin)


# --- Main block for running tests ---
if __name__ == "__main__":
    pytest.main(["-v", os.path.basename(__file__)])
