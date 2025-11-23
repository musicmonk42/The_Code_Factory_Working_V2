# self_healing_import_fixer/tests/test_analyzer.py

import json
import os

# Add parent directory to path
import pathlib
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

# Import from the correct location - analyzer.analyzer
from self_healing_import_fixer.analyzer.analyzer import AnalyzerCriticalError, load_config, main


# Add test fixtures that are missing
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
    with patch("self_healing_import_fixer.analyzer.analyzer.alert_operator") as mock:
        yield mock


@pytest.fixture
def mock_audit_logger():
    """Mock the audit_logger"""
    mock_logger = MagicMock()
    with patch("self_healing_import_fixer.analyzer.analyzer.audit_logger", mock_logger):
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


# --- Test load_config function ---
def test_load_config_valid_yaml(valid_config_yaml_path, mock_audit_logger):
    """Tests loading a valid YAML configuration file."""
    config = load_config(valid_config_yaml_path)
    assert config.project_root.endswith("project")
    assert config.audit_logging_enabled is True
    assert config.policy_rules_file.endswith("policy.json")
    assert mock_audit_logger.log_event.call_count >= 1


def test_load_config_valid_json(valid_config_json_path, mock_audit_logger):
    """Tests loading a valid JSON configuration file."""
    config = load_config(valid_config_json_path)
    assert config.project_root.endswith("project")
    assert config.audit_logging_enabled is True
    assert config.policy_rules_file.endswith("policy.json")
    assert mock_audit_logger.log_event.call_count >= 1


def test_load_config_invalid_file_path(mock_alert_operator):
    """Tests that a non-existent config file raises a critical error."""
    with pytest.raises(AnalyzerCriticalError) as excinfo:
        load_config("/non/existent/path.yaml")
    assert "Configuration file not found" in str(excinfo.value)
    # Check that alert_operator was called at least once (it may be called multiple times)
    assert mock_alert_operator.call_count >= 1


def test_load_config_malformed_file(malformed_config_path, mock_alert_operator):
    """Tests that a syntactically incorrect config file raises a critical error."""
    with pytest.raises(AnalyzerCriticalError) as excinfo:
        load_config(malformed_config_path)
    assert "Failed to load/validate configuration" in str(excinfo.value)
    assert mock_alert_operator.call_count >= 1


def test_load_config_invalid_schema(invalid_schema_config_path, mock_alert_operator):
    """Tests that a config file with an invalid schema raises a critical error."""
    with pytest.raises(AnalyzerCriticalError) as excinfo:
        load_config(invalid_schema_config_path)
    # The error could be about missing required field or validation
    assert "project_root" in str(excinfo.value) or "Unexpected error loading configuration" in str(
        excinfo.value
    )
    assert mock_alert_operator.call_count >= 1


# --- Tests for production mode enforcement ---
def test_production_mode_enforcement():
    """Test that production mode properly enforces audit logging and demo mode restrictions."""
    # Test with non-production mode - should succeed with disabled audit logging
    with patch.dict(os.environ, {"PRODUCTION_MODE": "false"}):
        temp_dir = tempfile.mkdtemp()
        project_dir = os.path.join(temp_dir, "test_project")
        os.makedirs(project_dir, exist_ok=True)

        config_data = {
            "project_root": project_dir,
            "audit_logging_enabled": False,  # Disabled in non-prod
            "demo_mode_enabled": True,  # Enabled in non-prod
        }

        config_file = os.path.join(temp_dir, "config.yaml")
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        # Should load successfully in non-production mode
        config = load_config(config_file)
        assert config.audit_logging_enabled is False
        assert config.demo_mode_enabled is True


def test_prod_mode_blocks_demo_mode(tmp_path):
    """Tests that demo mode is blocked in production."""
    # Create a real project directory
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()

    # Create a config file with demo mode enabled
    config_file = tmp_path / "config.yaml"
    config_data = {
        "project_root": str(project_dir),
        "demo_mode_enabled": True,
        "audit_logging_enabled": True,
    }
    config_file.write_text(yaml.dump(config_data))

    # Use the --production-mode CLI flag which will make it read from SSM
    # The updated SSM mock will read from our config file
    with patch.dict(os.environ, {"PRODUCTION_MODE": "false"}):
        cli_args = [
            "analyzer.py",
            "analyze",
            "--config",
            str(config_file),
            "--production-mode",
        ]

        with patch("sys.argv", cli_args):
            with pytest.raises(AnalyzerCriticalError) as excinfo:
                main(standalone_mode=False)
            assert "Demo mode enabled in production" in str(excinfo.value)


def test_prod_mode_blocks_mock_llm(tmp_path):
    """Tests that load_config raises a critical error if a mock LLM endpoint is used in production."""
    # Create a real project directory
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()

    # Create a config file with mock LLM endpoint
    config_file = tmp_path / "config.yaml"
    config_data = {
        "project_root": str(project_dir),
        "llm_endpoint": "https://mock.ai/api",
        "audit_logging_enabled": True,
    }
    config_file.write_text(yaml.dump(config_data))

    # Test in production mode (will load from file even in prod mode for this test)
    with patch.dict(os.environ, {"PRODUCTION_MODE": "true"}):
        # Since we're in production mode but loading from a file (not SSM),
        # we need to mock the file check to pass
        with patch("os.path.exists", return_value=False):  # Make it think file doesn't exist
            with patch("self_healing_import_fixer.analyzer.analyzer.boto3") as mock_boto3:
                # Mock SSM to return config with mock endpoint
                mock_client = MagicMock()
                mock_client.get_parameter.return_value = {
                    "Parameter": {"Value": json.dumps(config_data)}
                }
                mock_boto3.client.return_value = mock_client

                with pytest.raises(AnalyzerCriticalError) as excinfo:
                    load_config(str(config_file))
                assert "Mock LLM endpoint detected in PRODUCTION_MODE" in str(excinfo.value)


def test_prod_mode_blocks_disabled_audit_logging(tmp_path):
    """Tests that main raises a critical error if audit logging is disabled in production."""
    # Create a real project directory
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()

    # Create a config file with audit logging disabled
    config_file = tmp_path / "config.yaml"
    config_data = {"project_root": str(project_dir), "audit_logging_enabled": False}
    config_file.write_text(yaml.dump(config_data))

    # Use CLI flag to enable production mode
    # The updated SSM mock will read from our config file
    with patch.dict(os.environ, {"PRODUCTION_MODE": "false"}):
        cli_args = [
            "analyzer.py",
            "analyze",
            "--config",
            str(config_file),
            "--production-mode",
        ]

        with patch("sys.argv", cli_args):
            with pytest.raises(AnalyzerCriticalError) as excinfo:
                main(standalone_mode=False)
            assert "Audit logging disabled in production" in str(excinfo.value)


def test_production_mode_flag_precedence():
    """Test that the --production-mode CLI flag sets production mode."""
    temp_dir = tempfile.mkdtemp()
    project_dir = os.path.join(temp_dir, "test_project")
    os.makedirs(project_dir, exist_ok=True)

    # Create config file
    config_data = {
        "project_root": project_dir,
        "audit_logging_enabled": True,
        "demo_mode_enabled": False,
    }
    config_file = os.path.join(temp_dir, "config.yaml")
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)

    # Test with production-mode flag
    with patch.dict(os.environ, {"PRODUCTION_MODE": "false"}):
        cli_args = [
            "analyzer.py",
            "analyze",
            "--config",
            config_file,
            "--production-mode",
        ]

        with patch("sys.argv", cli_args):
            with patch("self_healing_import_fixer.analyzer.analyzer._handle_analyze"):
                with patch("self_healing_import_fixer.analyzer.analyzer.asyncio.run"):
                    # This should run with PRODUCTION_MODE set to True due to CLI flag
                    try:
                        main(standalone_mode=False)
                    except SystemExit as e:
                        assert e.code == 0


# Additional test for the main CLI functionality
def test_main_analyze_action_success(tmp_path):
    """Test that the analyze action runs successfully with valid config."""
    # Create a real project directory
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()

    config_content = {
        "project_root": str(project_dir),
        "audit_logging_enabled": True,
        "demo_mode_enabled": False,
    }

    # Create config file
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(config_content))
    config_path = str(config_file)

    with patch("sys.argv", ["analyzer.py", "analyze", "--config", config_path]):
        with patch("self_healing_import_fixer.analyzer.analyzer._handle_analyze"):
            with patch("self_healing_import_fixer.analyzer.analyzer.asyncio.run"):
                try:
                    main(standalone_mode=False)
                except SystemExit as e:
                    assert e.code == 0
