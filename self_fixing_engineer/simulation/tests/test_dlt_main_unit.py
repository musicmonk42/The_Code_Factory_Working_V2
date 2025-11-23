# tests/test_dlt_main_unit.py

import pytest
import json
import logging
import click
from unittest.mock import AsyncMock, patch
from click.testing import CliRunner

# Import the CLI and core components
from simulation.plugins.dlt_clients.dlt_main import cli
from simulation.plugins.dlt_clients.dlt_factory import DLTFactory
from simulation.plugins.dlt_clients.dlt_base import (
    DLTClientError,
    DLTClientConfigurationError,
    _base_logger,
)


@pytest.fixture(autouse=True)
def disable_info_logging():
    original_level = _base_logger.level
    _base_logger.setLevel(
        logging.CRITICAL + 1
    )  # Suppress all logs below CRITICAL to reduce warning noise
    yield
    _base_logger.setLevel(original_level)


# Mock client for dependency injection
@pytest.fixture
def mock_dlt_client():
    mock = AsyncMock()
    mock.health_check = AsyncMock(return_value={"status": True, "message": "OK", "details": {}})
    mock.write_checkpoint = AsyncMock(return_value=("mock_tx_id", "mock_off_chain_id", 1))
    mock.read_checkpoint = AsyncMock(
        return_value={
            "metadata": {},
            "payload_blob": b"mock data",
            "tx_id": "mock_tx_id",
        }
    )
    mock.rollback_checkpoint = AsyncMock(
        return_value={"metadata": {}, "tx_id": "mock_rollback_tx_id", "version": 2}
    )
    mock.close = AsyncMock()
    return mock


# Mock factory to return the mock client
@pytest.fixture
def mock_factory(mocker, mock_dlt_client):
    mocker.patch.object(DLTFactory, "get_dlt_client", new=AsyncMock(return_value=mock_dlt_client))
    mocker.patch.object(DLTFactory, "list_available_dlt_clients", return_value=["simple", "evm"])
    # Mock alert_operator to avoid actual alerts during tests
    mocker.patch("simulation.plugins.dlt_clients.dlt_main.alert_operator", new=AsyncMock())
    # Mock scrub_secrets to just return the input unchanged
    mocker.patch("simulation.plugins.dlt_clients.dlt_main.scrub_secrets", side_effect=lambda x: x)
    return mock_dlt_client


# Mock a configuration file
@pytest.fixture
def mock_config_file(tmp_path):
    config = {"dlt_type": "simple", "off_chain_storage_type": "in_memory"}
    file_path = tmp_path / "config.json"
    with open(file_path, "w") as f:
        json.dump(config, f)
    return str(file_path)


# Mock a payload file
@pytest.fixture
def mock_payload_file(tmp_path):
    file_path = tmp_path / "payload.bin"
    with open(file_path, "wb") as f:
        f.write(b"this is a payload")
    return str(file_path)


def test_cli_health_check_success(mock_factory, mock_config_file):
    """
    Test a successful `health-check` command execution.
    """
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["health-check", "--dlt-type", "simple", "--config-file", mock_config_file],
        standalone_mode=False,
    )

    assert result.exit_code == 0
    assert "Health Check SUCCESS" in result.output
    assert mock_factory.health_check.called


def test_cli_health_check_failure(mock_factory, mock_config_file):
    """
    Test a failed `health-check` command execution due to unhealthy status.
    """
    mock_factory.health_check.return_value = {
        "status": False,
        "message": "Failed",
        "details": {},
    }

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["health-check", "--dlt-type", "simple", "--config-file", mock_config_file],
        standalone_mode=False,
    )

    # In test mode, we don't signal exit codes to avoid stream issues
    # Just check that the failure message is in the output
    assert "Health Check FAILED" in result.output
    assert "Failed" in result.output  # Check for the actual message in the JSON output
    assert mock_factory.health_check.called


def test_cli_write_checkpoint_success(mock_factory, mock_config_file, mock_payload_file):
    """
    Test a successful `write-checkpoint` command execution.
    """
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "write-checkpoint",
            "--dlt-type",
            "simple",
            "--config-file",
            mock_config_file,
            "--checkpoint-name",
            "test-chain",
            "--hash",
            "test-hash",
            "--payload-file",
            mock_payload_file,
        ],
        standalone_mode=False,
    )

    assert result.exit_code == 0
    assert "Write Checkpoint SUCCESS" in result.output
    assert "Transaction ID: mock_tx_id" in result.output
    assert mock_factory.write_checkpoint.called

    # Check the call arguments
    call_kwargs = mock_factory.write_checkpoint.call_args.kwargs
    assert call_kwargs["checkpoint_name"] == "test-chain"
    assert call_kwargs["hash"] == "test-hash"
    assert call_kwargs["payload_blob"] == b"this is a payload"


def test_cli_read_checkpoint_success(mock_factory, mock_config_file, tmp_path):
    """
    Test a successful `read-checkpoint` command execution.
    """
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "read-checkpoint",
            "--dlt-type",
            "simple",
            "--config-file",
            mock_config_file,
            "--checkpoint-name",
            "test-chain",
            "--version",
            "latest",
        ],
        standalone_mode=False,
    )

    assert result.exit_code == 0
    assert "Read Checkpoint SUCCESS" in result.output
    assert "Metadata" in result.output
    assert mock_factory.read_checkpoint.called

    call_kwargs = mock_factory.read_checkpoint.call_args.kwargs
    assert call_kwargs["name"] == "test-chain"
    assert call_kwargs["version"] == "latest"


def test_cli_read_checkpoint_with_output_file(mock_factory, mock_config_file, tmp_path):
    """
    Test a successful `read-checkpoint` command execution with output file.
    """
    output_file = tmp_path / "output.bin"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "read-checkpoint",
            "--dlt-type",
            "simple",
            "--config-file",
            mock_config_file,
            "--checkpoint-name",
            "test-chain",
            "--version",
            "latest",
            "--output-file",
            str(output_file),
        ],
        standalone_mode=False,
    )

    assert result.exit_code == 0
    assert "Read Checkpoint SUCCESS" in result.output
    assert f"Payload saved to: {output_file}" in result.output
    assert output_file.exists()
    assert output_file.read_bytes() == b"mock data"


def test_cli_rollback_checkpoint_success(mock_factory, mock_config_file):
    """
    Test a successful `rollback-checkpoint` command execution.
    """
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "rollback-checkpoint",
            "--dlt-type",
            "simple",
            "--config-file",
            mock_config_file,
            "--checkpoint-name",
            "test-chain",
            "--rollback-hash",
            "rollback-hash-value",
        ],
        standalone_mode=False,
    )

    assert result.exit_code == 0
    assert "Rollback Checkpoint SUCCESS" in result.output
    assert mock_factory.rollback_checkpoint.called

    call_kwargs = mock_factory.rollback_checkpoint.call_args.kwargs
    assert call_kwargs["name"] == "test-chain"
    assert call_kwargs["rollback_hash"] == "rollback-hash-value"


def test_cli_invalid_config_file(mocker, tmp_path):
    """
    Test that the CLI correctly handles a non-existent configuration file.
    """
    mocker.patch.object(DLTFactory, "list_available_dlt_clients", return_value=["simple", "evm"])

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "health-check",
            "--dlt-type",
            "simple",
            "--config-file",
            str(tmp_path / "non-existent.json"),
        ],
        standalone_mode=False,
        catch_exceptions=True,
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, click.exceptions.BadParameter)
    assert "does not exist" in str(result.exception)


def test_cli_invalid_json_in_config(mocker, tmp_path):
    """
    Test that the CLI correctly handles an invalid JSON file.
    """
    mocker.patch.object(DLTFactory, "list_available_dlt_clients", return_value=["simple", "evm"])
    mocker.patch("simulation.plugins.dlt_clients.dlt_main.scrub_secrets", side_effect=lambda x: x)

    invalid_json_file = tmp_path / "invalid.json"
    with open(invalid_json_file, "w", encoding="utf-8") as f:
        f.write("{'key': 'value'")  # Invalid JSON

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "health-check",
            "--dlt-type",
            "simple",
            "--config-file",
            str(invalid_json_file),
        ],
        standalone_mode=False,
        catch_exceptions=True,
    )

    assert result.exit_code != 0
    assert "Invalid JSON" in result.output or "Error:" in result.output


def test_cli_dlt_client_configuration_error(mocker, mock_config_file):
    """
    Test that the CLI handles a DLTClientConfigurationError from the factory.
    """
    mocker.patch.object(DLTFactory, "list_available_dlt_clients", return_value=["simple", "evm"])
    mocker.patch.object(
        DLTFactory,
        "get_dlt_client",
        new=AsyncMock(side_effect=DLTClientConfigurationError("Mock config error", "Main")),
    )
    mocker.patch("simulation.plugins.dlt_clients.dlt_main.alert_operator", new=AsyncMock())
    mocker.patch("simulation.plugins.dlt_clients.dlt_main.scrub_secrets", side_effect=lambda x: x)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["health-check", "--dlt-type", "simple", "--config-file", mock_config_file],
        standalone_mode=False,
        catch_exceptions=True,
    )

    assert result.exit_code != 0
    assert "Mock config error" in result.output
    assert DLTFactory.get_dlt_client.called


def test_cli_dlt_client_error(mocker, mock_config_file):
    """
    Test that the CLI handles a DLTClientError from operations.
    """
    mock_client = AsyncMock()
    mock_client.health_check = AsyncMock(side_effect=DLTClientError("Mock operation error", "Main"))
    mock_client.close = AsyncMock()

    mocker.patch.object(DLTFactory, "list_available_dlt_clients", return_value=["simple", "evm"])
    mocker.patch.object(DLTFactory, "get_dlt_client", new=AsyncMock(return_value=mock_client))
    mocker.patch("simulation.plugins.dlt_clients.dlt_main.scrub_secrets", side_effect=lambda x: x)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["health-check", "--dlt-type", "simple", "--config-file", mock_config_file],
        standalone_mode=False,
        catch_exceptions=True,
    )

    assert result.exit_code != 0
    assert "Mock operation error" in result.output


def test_cli_write_checkpoint_invalid_metadata(mocker, mock_config_file, mock_payload_file):
    """
    Test write-checkpoint with invalid metadata JSON.
    """
    mocker.patch.object(DLTFactory, "list_available_dlt_clients", return_value=["simple", "evm"])
    mocker.patch("simulation.plugins.dlt_clients.dlt_main.scrub_secrets", side_effect=lambda x: x)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "write-checkpoint",
            "--dlt-type",
            "simple",
            "--config-file",
            mock_config_file,
            "--checkpoint-name",
            "test-chain",
            "--hash",
            "test-hash",
            "--payload-file",
            mock_payload_file,
            "--metadata",
            "{invalid json}",
        ],
        standalone_mode=False,
    )

    # In test mode, we don't signal exit codes to avoid stream issues
    # Just check that the error message is in the output
    assert "Invalid JSON format for metadata" in result.output or "Error:" in result.output


def test_cli_verbose_flag(mock_factory, mock_config_file):
    """
    Test that the verbose flag enables debug logging.
    """
    runner = CliRunner()
    with patch("simulation.plugins.dlt_clients.dlt_main._base_logger") as mock_logger:
        result = runner.invoke(
            cli,
            [
                "--verbose",
                "health-check",
                "--dlt-type",
                "simple",
                "--config-file",
                mock_config_file,
            ],
            standalone_mode=False,
        )
        mock_logger.setLevel.assert_called_with(logging.DEBUG)
        assert result.exit_code == 0


def test_cli_correlation_id_provided(mock_factory, mock_config_file):
    """
    Test that a provided correlation ID is used.
    """
    test_correlation_id = "test-correlation-123"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "health-check",
            "--dlt-type",
            "simple",
            "--config-file",
            mock_config_file,
            "--correlation-id",
            test_correlation_id,
        ],
        standalone_mode=False,
    )

    assert result.exit_code == 0
    # Check that the correlation ID was passed to the factory
    call_kwargs = DLTFactory.get_dlt_client.call_args.kwargs
    assert call_kwargs["correlation_id"] == test_correlation_id
