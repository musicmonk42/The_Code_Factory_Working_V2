# tests/test_compliance_mapper.py
import argparse
import logging
import os
import shutil
import sys
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Fix import path for compliance_mapper module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from compliance_mapper import (
    PROMETHEUS_AVAILABLE,
    ComplianceEnforcementError,
    _audit_log_gap,
    check_coverage,
    generate_report,
    health_check,
    load_compliance_map,
    main_cli,
    sanitize_log,
    write_dummy_config,
)


@pytest.fixture
def temp_config(tmp_path):
    """Fixture to create a temporary YAML config file."""
    config_path = tmp_path / "crew_config.yaml"
    config = {
        "compliance_controls": {
            "AC-1": {
                "name": "Access Control Policy",
                "description": "Test",
                "status": "enforced",
                "required": True,
            },
            "AC-2": {
                "name": "Account Management",
                "description": "Test",
                "status": "not_implemented",
                "required": True,
            },
            "AC-3": {
                "name": "Access Enforcement",
                "description": "Test",
                "status": "partially_enforced",
                "required": True,
            },
            "AC-4": {
                "name": "Least Privilege",
                "description": "Test",
                "status": "logged",
                "required": False,
            },
            "AC-5": {
                "name": "Unmapped Control",
                "description": "Test",
                "status": "not_specified",
                "required": True,
            },
        }
    }
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f)
    return str(config_path)


@pytest.fixture
def invalid_config(tmp_path):
    """Fixture to create an invalid YAML config file."""
    config_path = tmp_path / "invalid_crew_config.yaml"
    config = {
        "compliance_controls": {
            "AC-1": {"status": "invalid_status", "required": True},  # Invalid status
        }
    }
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f)
    return str(config_path)


@pytest.fixture
def malformed_yaml(tmp_path):
    """Fixture to create a malformed YAML file."""
    config_path = tmp_path / "malformed.yaml"
    with open(config_path, "w") as f:
        f.write("compliance_controls: [invalid: yaml")
    return str(config_path)


@pytest.fixture
def mock_env(monkeypatch):
    """Fixture to mock environment variables."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CREW_CONFIG_PATH", "dummy_path")


@pytest.fixture
def caplog(caplog):
    """Fixture to capture logs."""
    caplog.set_level(logging.INFO)
    return caplog


@pytest.mark.asyncio
async def test_load_compliance_map(temp_config, caplog):
    """Test loading a valid compliance map."""
    compliance_map = load_compliance_map(temp_config)
    assert len(compliance_map) == 5
    assert compliance_map["AC-1"]["status"] == "enforced"
    assert "Loaded 5 compliance controls" in caplog.text


@pytest.mark.asyncio
async def test_load_compliance_map_missing_file(mock_env, caplog):
    """Test loading when config file is missing."""
    compliance_map = load_compliance_map("non_existent_path")
    assert compliance_map == {}
    assert "Error: crew_config.yaml not found" in caplog.text


@pytest.mark.asyncio
async def test_load_compliance_map_permission_error(mock_env, temp_config, monkeypatch):
    """Test permission denied error."""

    def mock_open(*args, **kwargs):
        raise PermissionError("Permission denied")

    with monkeypatch.context() as m:
        m.setattr("builtins.open", mock_open)
        with pytest.raises(PermissionError):
            load_compliance_map(temp_config)


@pytest.mark.asyncio
async def test_load_compliance_map_malformed_yaml(malformed_yaml, caplog):
    """Test loading malformed YAML."""
    compliance_map = load_compliance_map(malformed_yaml)
    assert compliance_map == {}
    assert "Error parsing crew_config.yaml" in caplog.text


@pytest.mark.asyncio
async def test_load_compliance_map_invalid_schema(invalid_config, caplog):
    """Test invalid YAML schema."""
    compliance_map = load_compliance_map(invalid_config)
    assert compliance_map == {}
    assert "Invalid YAML structure" in caplog.text


@pytest.mark.asyncio
async def test_load_compliance_map_no_controls(temp_config, monkeypatch, caplog):
    """Test loading config with no compliance_controls."""

    def mock_safe_load(*args):
        return {}

    with monkeypatch.context() as m:
        m.setattr(yaml, "safe_load", mock_safe_load)
        compliance_map = load_compliance_map(temp_config)
        assert compliance_map == {}
        assert "No 'compliance_controls' found" in caplog.text


@pytest.mark.asyncio
async def test_load_compliance_map_prometheus_inc(temp_config, monkeypatch):
    """Test Prometheus metric increment on load failure."""
    if not PROMETHEUS_AVAILABLE:
        pytest.skip("Prometheus not available")
    with patch("compliance_mapper.self_healing_config_load_failures") as mock_counter:

        def mock_open(*args, **kwargs):
            raise Exception("Test error")

        with monkeypatch.context() as m:
            m.setattr("builtins.open", mock_open)
            load_compliance_map(temp_config)
            mock_counter.inc.assert_called_once()


@pytest.mark.asyncio
async def test_check_coverage():
    """Test check_coverage with various statuses."""
    compliance_map = {
        "AC-1": {"status": "enforced", "required": True},
        "AC-2": {"status": "not_implemented", "required": True},
        "AC-3": {"status": "partially_enforced", "required": True},
        "AC-4": {"status": "logged", "required": False},
        "AC-5": {"status": "not_specified", "required": True},
    }
    gaps = check_coverage(compliance_map)
    assert "AC-2" in gaps["required_but_not_enforced"]
    assert "AC-3" in gaps["required_but_not_enforced"]
    assert "AC-5" in gaps["required_but_not_enforced"]
    assert "AC-4" not in gaps["required_but_not_enforced"]


@pytest.mark.asyncio
async def test_check_coverage_prometheus_set(monkeypatch):
    """Test Prometheus gauge set in check_coverage."""
    if not PROMETHEUS_AVAILABLE:
        pytest.skip("Prometheus not available")
    with patch(
        "compliance_mapper.self_healing_compliance_required_controls_not_enforced"
    ) as mock_gauge:
        mock_gauge.labels.return_value.set = MagicMock()
        compliance_map = {"AC-2": {"status": "not_implemented", "required": True}}
        check_coverage(compliance_map)
        mock_gauge.labels.return_value.set.assert_called_with(1)


@pytest.mark.asyncio
async def test_generate_report(temp_config, capsys, caplog):
    """Test generate_report with valid config."""
    gaps, all_enforced = generate_report(temp_config)
    captured = capsys.readouterr()
    assert "Generating Compliance Coverage Report" in captured.out
    assert not all_enforced
    assert "Compliance Report: Gaps detected" in caplog.text


@pytest.mark.asyncio
async def test_generate_report_all_enforced(temp_config, monkeypatch):
    """Test generate_report when all enforced."""

    def mock_load(*args):
        return {"AC-1": {"status": "enforced", "required": True}}

    with patch("compliance_mapper.load_compliance_map", mock_load):
        gaps, all_enforced = generate_report(temp_config)
        assert all_enforced


@pytest.mark.asyncio
async def test_health_check():
    """Test health_check function."""
    health = health_check()
    assert "prometheus_available" in health
    assert "config_path_exists" in health


@pytest.mark.asyncio
async def test_compliance_enforcement_error(caplog):
    """Test ComplianceEnforcementError raising and logging."""
    with pytest.raises(ComplianceEnforcementError):
        raise ComplianceEnforcementError("test_action", "AC-1", "test_message")
    assert "ACTION_BLOCKED_BY_COMPLIANCE" in caplog.text


@pytest.mark.asyncio
async def test_audit_log_gap(caplog):
    """Test _audit_log_gap logging and metrics."""
    _audit_log_gap("test_gap", {"detail": "test"})
    assert "AUDIT_LOG_COMPLIANCE_GAP" in caplog.text


@pytest.mark.asyncio
async def test_write_dummy_config(tmp_path):
    """Test write_dummy_config with retries."""
    config_path = tmp_path / "dummy.yaml"
    write_dummy_config(str(config_path), "test_content")
    with open(config_path, "r") as f:
        assert f.read() == "test_content"


@pytest.mark.asyncio
async def test_write_dummy_config_low_disk_space(tmp_path, monkeypatch):
    """Test low disk space check in write_dummy_config."""

    def mock_disk_usage(*args):
        return (100, 0, 50 * 1024 * 1024)  # Less than 100MB free

    with monkeypatch.context() as m:
        m.setattr(shutil, "disk_usage", mock_disk_usage)
        with pytest.raises(SystemExit):
            write_dummy_config(str(tmp_path / "dummy.yaml"), "test_content")


@pytest.mark.asyncio
async def test_main_cli(mock_env, temp_config, monkeypatch, capsys):
    """Test main_cli with valid config."""
    monkeypatch.setenv("CREW_CONFIG_PATH", temp_config)
    with pytest.raises(SystemExit) as exc:
        main_cli()
    assert exc.value.code == 1  # Gaps exist
    captured = capsys.readouterr()
    assert "WARNING: Compliance enforcement gaps detected" in captured.out


@pytest.mark.asyncio
async def test_main_cli_health_check(mock_env, monkeypatch, capsys):
    """Test main_cli --health-check option."""
    with patch("argparse.ArgumentParser.parse_args") as mock_parse:
        mock_parse.return_value = argparse.Namespace(health_check=True)
        with pytest.raises(SystemExit):
            main_cli()
        captured = capsys.readouterr()
        assert "prometheus_available" in captured.out


@pytest.mark.asyncio
async def test_main_cli_prometheus_required(mock_env, monkeypatch):
    """Test Prometheus enforcement in production."""
    monkeypatch.setenv("APP_ENV", "production")
    with patch("compliance_mapper.PROMETHEUS_AVAILABLE", False):
        with pytest.raises(SystemExit) as exc:
            main_cli()
        assert exc.value.code == 1


@pytest.mark.asyncio
async def test_main_cli_permission_error(mock_env, monkeypatch):
    """Test permission error in main_cli."""

    def mock_generate(*args):
        raise PermissionError("Test permission error")

    with patch("compliance_mapper.generate_report", mock_generate):
        with pytest.raises(SystemExit) as exc:
            main_cli()
        assert exc.value.code == 2


@pytest.mark.asyncio
async def test_main_cli_compliance_error(mock_env, monkeypatch):
    """Test ComplianceEnforcementError in main_cli."""

    def mock_generate(*args):
        raise ComplianceEnforcementError("startup", "CONFIG", "Test")

    with patch("compliance_mapper.generate_report", mock_generate):
        with pytest.raises(SystemExit) as exc:
            main_cli()
        assert exc.value.code == 2


@pytest.mark.asyncio
async def test_main_cli_unexpected_error(mock_env, monkeypatch):
    """Test unexpected error in main_cli."""

    def mock_generate(*args):
        raise Exception("Test unexpected error")

    with patch("compliance_mapper.generate_report", mock_generate):
        with pytest.raises(SystemExit) as exc:
            main_cli()
        assert exc.value.code == 3


def test_sanitize_log():
    """Test sanitize_log function."""
    msg = "api_key=secret123 password=pass123 user@example.com"
    sanitized = sanitize_log(msg)
    assert "REDACTED" in sanitized
    assert "example.com" not in sanitized  # Email should be redacted
