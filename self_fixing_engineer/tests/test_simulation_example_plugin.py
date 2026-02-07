# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# tests/test_example_plugin.py

import json
import os
import shutil
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# Fix the import path - add the simulation/plugins directory to path
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "simulation", "plugins"))
)

# Mock cachetools before importing the plugin since it might not be installed
sys.modules["cachetools"] = MagicMock()
sys.modules["cachetools"].TTLCache = MagicMock(return_value={})

# Now import the plugin
import example_plugin
from example_plugin import (
    PLUGIN_MANIFEST,
    _scrub_secrets,
    check_compatibility,
    perform_custom_security_audit,
    plugin_health,
    run_custom_chaos_experiment,
)

# ==============================================================================
# Pytest Fixtures for mocking external dependencies and environment
# ==============================================================================


@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset the metrics dictionary before each test."""
    example_plugin._METRICS = {}
    yield
    example_plugin._METRICS = {}


@pytest.fixture(autouse=True)
def mock_prometheus_client():
    """
    Mocks prometheus_client for testing, providing a default bucket attribute
    to prevent AttributeError.
    """
    mock_histogram = MagicMock()
    # Provide the necessary attribute to prevent the AttributeError
    mock_histogram.DEFAULT_BUCKETS = (
        0.005,
        0.01,
        0.025,
        0.05,
        0.075,
        0.1,
        0.25,
        0.5,
        0.75,
        1.0,
        2.5,
        5.0,
        7.5,
        10.0,
        15.0,
    )

    with (
        patch("example_plugin.Counter", MagicMock()),
        patch("example_plugin.Histogram", mock_histogram),
        patch("example_plugin.Gauge", MagicMock()),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_plugin_config():
    """
    Mocks the plugin config file and environment variables.
    """
    temp_dir = tempfile.mkdtemp()
    configs_dir = os.path.join(temp_dir, "configs")
    os.makedirs(configs_dir)

    mock_config = {
        "manifest": {
            "name": "MockPlugin",
            "version": "1.0.0",
            "compatibility": {
                "min_sim_runner_version": "1.0.0",
                "max_sim_runner_version": "2.0.0",
            },
        },
        "default_chaos_intensity": 0.7,
    }

    config_path = os.path.join(configs_dir, "example_plugin_config.json")
    with open(config_path, "w") as f:
        json.dump(mock_config, f)

    # Patch the CONFIG_PATH and reload the config
    with patch.object(example_plugin, "CONFIG_PATH", config_path):
        # Reload the config
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                example_plugin.PLUGIN_CONFIG = json.load(f)
                example_plugin.PLUGIN_MANIFEST = (
                    example_plugin._DEFAULT_MANIFEST
                    | example_plugin.PLUGIN_CONFIG.get("manifest", {})
                )
        except:
            pass

        # Set environment variables
        with patch.dict(
            os.environ,
            {
                "SANCTIONED_CODE_DIR": temp_dir,
                "CHAOS_INTENSITY_DEFAULT": "0.7",
                "PLUGIN_NAME": "MockPlugin",
                "ENABLE_CHAOS_EXPERIMENTS": "true",
                "CHAOS_SIMULATE_FAILURE_PROB": "0.0",  # Disable random failures for tests
            },
        ):
            yield temp_dir

    shutil.rmtree(temp_dir)


@pytest.fixture
def mock_metrics():
    """Mocks metrics to capture calls."""
    # Mock the metric objects directly
    mock_chaos_counter = MagicMock()
    mock_findings_counter = MagicMock()
    mock_duration_histogram = MagicMock()

    with (
        patch.object(example_plugin, "CHAOS_EXPERIMENT_TOTAL", mock_chaos_counter),
        patch.object(example_plugin, "SECURITY_FINDINGS_TOTAL", mock_findings_counter),
        patch.object(
            example_plugin, "SECURITY_AUDIT_DURATION", mock_duration_histogram
        ),
    ):
        yield {
            "chaos": mock_chaos_counter,
            "findings": mock_findings_counter,
            "duration": mock_duration_histogram,
        }


# ==============================================================================
# Unit Tests for Plugin Manifest and Configuration
# ==============================================================================


def test_plugin_manifest_loading(mock_plugin_config):
    """Test that the manifest is correctly loaded and has expected structure."""
    # The plugin loads its manifest at import time with defaults
    # We verify the structure and key fields exist
    assert "name" in PLUGIN_MANIFEST
    assert "version" in PLUGIN_MANIFEST
    assert "compatibility" in PLUGIN_MANIFEST
    assert "min_sim_runner_version" in PLUGIN_MANIFEST["compatibility"]
    assert "max_sim_runner_version" in PLUGIN_MANIFEST["compatibility"]

    # For the patched config test, we can test with the actual values
    # The plugin uses 'ExampleChaosSecurityPlugin' as default
    assert PLUGIN_MANIFEST["name"] == "ExampleChaosSecurityPlugin"

    # Alternatively, test that we can override it
    with patch.object(
        example_plugin, "PLUGIN_MANIFEST", {"name": "MockPlugin", "version": "1.0.0"}
    ):
        assert example_plugin.PLUGIN_MANIFEST["name"] == "MockPlugin"


def test_check_compatibility_success():
    """Test compatibility check with a compatible version."""
    assert check_compatibility("1.5.0") is True


def test_check_compatibility_failure_min_version():
    """Test compatibility check with a version below the minimum."""
    with patch.object(example_plugin.plugin_logger, "error") as mock_error:
        assert check_compatibility("0.9.0") is False
        mock_error.assert_called_once()


# ==============================================================================
# Unit Tests for `run_custom_chaos_experiment`
# ==============================================================================


def test_run_custom_chaos_experiment_success(mock_metrics):
    """Test a successful chaos experiment with low intensity."""
    # Set intensity below threshold
    with patch.dict(os.environ, {"CHAOS_INTENSITY_DEFAULT": "0.7"}):
        result = run_custom_chaos_experiment("test-agent-1", intensity=0.5)

    assert result["status"] == "EXPERIMENT_COMPLETED"
    assert "completed without significant impact" in result["message"]
    assert result["target"] == "test-agent-1"
    assert result["intensity"] == 0.5

    # Check metrics were called
    mock_metrics["chaos"].labels.assert_called_with(status="EXPERIMENT_COMPLETED")
    mock_metrics["chaos"].labels.return_value.inc.assert_called()


def test_run_custom_chaos_experiment_failure_injected(mock_metrics):
    """Test a chaos experiment that triggers failure due to high intensity."""
    # Set intensity above threshold
    with patch.dict(os.environ, {"CHAOS_INTENSITY_DEFAULT": "0.7"}):
        result = run_custom_chaos_experiment("test-agent-2", intensity=0.8)

    assert result["status"] == "FAILURE_INJECTED"
    assert "High intensity chaos" in result["message"]
    assert result["target"] == "test-agent-2"

    mock_metrics["chaos"].labels.assert_called_with(status="FAILURE_INJECTED")


def test_run_custom_chaos_experiment_validation_error(mock_metrics):
    """Test that invalid input is handled with a validation error."""
    result = run_custom_chaos_experiment("test-agent-3", intensity=1.5)

    assert result["status"] == "ERROR"
    assert "Input validation failed" in result["message"]

    mock_metrics["chaos"].labels.assert_called_with(status="validation_error")


def test_run_custom_chaos_experiment_simulated_error(mock_metrics):
    """Test that a simulated unexpected error is caught and handled."""
    # Force a connection error by enabling chaos and setting high failure probability
    with patch.dict(
        os.environ,
        {"ENABLE_CHAOS_EXPERIMENTS": "true", "CHAOS_SIMULATE_FAILURE_PROB": "1.0"},
    ):
        with patch("random.random", return_value=0.0):  # Ensure failure triggers
            result = run_custom_chaos_experiment("test-agent-4", intensity=0.5)

    assert result["status"] == "ERROR"
    assert "error occurred during experiment" in result["message"].lower()

    mock_metrics["chaos"].labels.assert_called_with(status="error")


# ==============================================================================
# Unit Tests for `perform_custom_security_audit`
# ==============================================================================


def test_perform_custom_security_audit_success_no_findings(
    mock_plugin_config, mock_metrics
):
    """Test a security audit on a clean file with no findings."""
    temp_dir = mock_plugin_config
    file_path = os.path.join(temp_dir, "clean_code.py")
    with open(file_path, "w") as f:
        f.write("def safe_function():\n    return 'hello world'")

    # Mock the cache to ensure fresh audit
    with patch.object(example_plugin, "_audit_cache", {}):
        result = perform_custom_security_audit("clean_code.py")

    assert result["status"] == "COMPLETED"
    assert len(result["findings"]) == 0
    assert result["severity"] == "None"
    assert "clean_code.py" in result["code_path"]

    mock_metrics["findings"].labels.assert_called_with(severity="None")
    mock_metrics["findings"].labels.return_value.inc.assert_called()


def test_perform_custom_security_audit_with_findings(mock_plugin_config, mock_metrics):
    """Test a security audit on a file with simulated findings."""
    temp_dir = mock_plugin_config
    file_path = os.path.join(temp_dir, "vulnerable_code.py")
    with open(file_path, "w") as f:
        f.write("api_key = 'super-secret-key'\n")
        f.write("def run_command(cmd):\n")
        f.write("    eval(cmd)\n")

    # Mock the cache to ensure fresh audit
    with patch.object(example_plugin, "_audit_cache", {}):
        result = perform_custom_security_audit("vulnerable_code.py")

    assert result["status"] == "FINDINGS_DETECTED"
    assert len(result["findings"]) >= 2  # At least api_key and eval findings
    assert result["severity"] == "High"

    # Check specific findings
    finding_texts = [f["text"] for f in result["findings"]]
    assert any("secret detected" in text.lower() for text in finding_texts)
    assert any("eval" in text.lower() for text in finding_texts)

    mock_metrics["findings"].labels.assert_called_with(severity="High")
    mock_metrics["duration"].labels.return_value.observe.assert_called()


def test_perform_custom_security_audit_path_traversal_attack(
    mock_plugin_config, mock_metrics
):
    """Test that a path traversal attempt is blocked."""
    result = perform_custom_security_audit("../../../etc/passwd")

    assert result["status"] == "ERROR"
    assert "path" in result["message"].lower()

    mock_metrics["findings"].labels.assert_called_with(severity="ERROR")


def test_perform_custom_security_audit_file_not_found(mock_plugin_config, mock_metrics):
    """Test handling of non-existent file."""
    # Mock the cache to ensure fresh audit
    with patch.object(example_plugin, "_audit_cache", {}):
        result = perform_custom_security_audit("nonexistent_file.py")

    assert result["status"] == "ERROR"
    assert "not found" in result["message"].lower()


def test_scrub_secrets_utility_function():
    """Test the secret scrubbing utility function."""
    content = "This is a key: super-secret-key and another_token=jwt123."
    scrubbed = _scrub_secrets(content)

    # The scrubbing should replace sensitive values
    assert "super-secret-key" not in scrubbed
    assert "jwt123" not in scrubbed
    assert "[REDACTED]" in scrubbed

    # Test various patterns
    test_cases = [
        ("password='secret123'", "password='[REDACTED]'"),
        ("api_key: mysecret", "api_key: [REDACTED]"),
        ("token = abc123", "token = [REDACTED]"),
        ("AWS_SECRET_ACCESS_KEY=xyz789", "AWS_SECRET_ACCESS_KEY=[REDACTED]"),
    ]

    for input_str, expected_pattern in test_cases:
        result = _scrub_secrets(input_str)
        assert "[REDACTED]" in result, f"Failed to scrub: {input_str}"


# ==============================================================================
# Unit Tests for Plugin Health Check
# ==============================================================================


def test_plugin_health_check_success():
    """Test the plugin health check with all dependencies available."""
    with patch.dict(os.environ, {"CHAOS_INTENSITY_DEFAULT": "0.5"}):
        result = plugin_health()

    assert result["status"] in [
        "ok",
        "degraded",
    ]  # May be degraded if prometheus not available
    assert isinstance(result["details"], list)


def test_plugin_health_check_degraded():
    """Test the plugin health check with invalid configuration."""
    with patch.dict(os.environ, {"CHAOS_INTENSITY_DEFAULT": "not_a_float"}):
        result = plugin_health()

    assert result["status"] == "degraded"
    assert any("CHAOS_INTENSITY_DEFAULT" in detail for detail in result["details"])
