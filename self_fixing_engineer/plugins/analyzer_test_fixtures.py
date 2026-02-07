# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# Add these fixtures to conftest.py or at the top of test_analyzer.py

import json
import os
from unittest.mock import MagicMock, patch

import pytest
import yaml


@pytest.fixture
def valid_config_yaml_path(tmp_path):
    """Create a valid YAML configuration file for testing"""
    config_data = {
        "project_root": str(tmp_path / "project"),
        "audit_logging_enabled": True,
        "policy_rules_file": str(tmp_path / "policy.json"),
        "ai_config": {"model": "gpt-3.5-turbo", "temperature": 0.7},
        "demo_mode_enabled": False,
        "llm_endpoint": "https://api.openai.com",
    }

    # Create the project directory
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Create the policy file
    policy_file = tmp_path / "policy.json"
    policy_file.write_text("{}")

    # Create the config file
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(config_data))

    return str(config_file)


@pytest.fixture
def valid_config_json_path(tmp_path):
    """Create a valid JSON configuration file for testing"""
    config_data = {
        "project_root": str(tmp_path / "project"),
        "audit_logging_enabled": True,
        "policy_rules_file": str(tmp_path / "policy.json"),
        "ai_config": {"model": "gpt-3.5-turbo", "temperature": 0.7},
        "demo_mode_enabled": False,
        "llm_endpoint": "https://api.openai.com",
    }

    # Create the project directory
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Create the policy file
    policy_file = tmp_path / "policy.json"
    policy_file.write_text("{}")

    # Create the config file
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_data))

    return str(config_file)


@pytest.fixture
def malformed_config_path(tmp_path):
    """Create a malformed configuration file for testing"""
    config_file = tmp_path / "malformed.yaml"
    config_file.write_text("{ invalid yaml content: [}")
    return str(config_file)


@pytest.fixture
def invalid_schema_config_path(tmp_path):
    """Create a configuration file with invalid schema for testing"""
    config_data = {
        # Missing required 'project_root' field
        "audit_logging_enabled": "not_a_boolean",  # Wrong type
        "invalid_field": "should_not_exist",
    }

    config_file = tmp_path / "invalid_schema.yaml"
    config_file.write_text(yaml.dump(config_data))

    return str(config_file)


@pytest.fixture
def mock_alert_operator():
    """Mock the alert_operator function"""
    with patch("self_fixing_engineer.plugins.core_utils.alert_operator") as mock:
        yield mock


@pytest.fixture
def mock_audit_logger():
    """Mock the audit_logger"""
    mock_logger = MagicMock()
    with patch("self_fixing_engineer.plugins.core_audit.audit_logger", mock_logger):
        yield mock_logger


@pytest.fixture
def tmp_config_file(tmp_path):
    """Helper fixture to create temporary config files"""

    def _create_config(content, format="yaml"):
        if format == "yaml":
            config_file = tmp_path / "config.yaml"
            config_file.write_text(yaml.dump(content))
        else:
            config_file = tmp_path / "config.json"
            config_file.write_text(json.dumps(content))
        return str(config_file)

    return _create_config


@pytest.fixture
def mock_sys_exit():
    """Mock sys.exit to prevent test runner from exiting"""
    with patch("sys.exit") as mock:
        yield mock


@pytest.fixture
def mock_os_env():
    """Mock environment variables"""
    with patch.dict(os.environ, {}, clear=True):
        yield
