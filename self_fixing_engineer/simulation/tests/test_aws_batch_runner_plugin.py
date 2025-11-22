# tests/test_aws_batch_runner_plugin.py

import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, mock_open
from botocore.exceptions import ClientError
from pydantic import ValidationError

# Import the plugin from the correct location
import sys

# Add the parent directory to the Python path to find the plugin
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, parent_dir)

# Now import from the correct module path
# If aws_batch_runner_plugin.py is in simulation/plugins/
try:
    from simulation.plugins.aws_batch_runner_plugin import (
        plugin_health,
        run_batch_job,
        JobConfig,
        AWS_AVAILABLE,
    )
except ImportError:
    # If it's in a different location, try alternative import
    try:
        from aws_batch_runner_plugin import (
            plugin_health,
            run_batch_job,
            JobConfig,
            AWS_AVAILABLE,
        )
    except ImportError:
        # If the plugin is in the parent directory
        sys.path.insert(
            0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        )
        from aws_batch_runner_plugin import (
            plugin_health,
            run_batch_job,
            JobConfig,
            AWS_AVAILABLE,
        )

# ==============================================================================
# Pytest Fixtures for mocking external dependencies and environment
# ==============================================================================


@pytest.fixture
def mock_aws_clients():
    """
    A focused fixture that only mocks boto3 and its clients.
    File system mocks are handled by individual tests.
    """
    if not AWS_AVAILABLE:
        pytest.skip("Boto3 not available, skipping AWS-dependent tests")

    # Update the patch paths to match the actual module location
    with patch("boto3.Session") as mock_session, patch(
        "simulation.plugins.aws_batch_runner_plugin._load_credentials_from_vault",
        new_callable=AsyncMock,
    ) as mock_load_vault:

        mock_session_instance = MagicMock()
        mock_session.return_value = mock_session_instance

        mock_s3_client = MagicMock()
        mock_batch_client = MagicMock()
        mock_logs_client = MagicMock()
        mock_sts_client = MagicMock()

        mock_session_instance.client.side_effect = lambda service_name, **kwargs: {
            "s3": mock_s3_client,
            "batch": mock_batch_client,
            "logs": mock_logs_client,
            "sts": mock_sts_client,
        }[service_name]

        # Configure minimal default behaviors
        mock_sts_client.get_caller_identity.return_value = {"Account": "123456789012"}
        mock_batch_client.submit_job.return_value = {"jobId": "mock_job_id"}
        mock_load_vault.return_value = {
            "aws_access_key_id": "vault_key",
            "aws_secret_access_key": "vault_secret",
        }

        with patch(
            "simulation.plugins.aws_batch_runner_plugin._session_has_real_creds",
            return_value=True,
        ):
            yield {
                "mock_session": mock_session,
                "mock_load_vault": mock_load_vault,
                "mock_s3_client": mock_s3_client,
                "mock_batch_client": mock_batch_client,
                "mock_logs_client": mock_logs_client,
                "mock_sts_client": mock_sts_client,
            }


@pytest.fixture(autouse=True)
def mock_env_vars():
    """Mocks environment variables for all tests."""
    with patch.dict(
        os.environ,
        {
            "AWS_ACCESS_KEY_ID": "mock_key_id",
            "AWS_SECRET_ACCESS_KEY": "mock_secret_key",
            "VAULT_URL": "https://mock-vault:8200",
            "VAULT_TOKEN": "mock_token",
            "PYTEST_CURRENT_TEST": "1",
        },
    ):
        yield


@pytest.fixture
def mock_job_config_valid():
    """Returns a valid mock job configuration."""
    return {
        "jobDefinition": "arn:aws:batch:us-east-1:123456789012:job-definition/mock_job_def:1",
        "jobQueue": "mock-job-queue",
        "input_s3_bucket": "mock-input-bucket",
        "output_s3_bucket": "mock-output-bucket",
    }


# ==============================================================================
# Unit Tests for Pydantic Model Validation
# ==============================================================================


def test_job_config_validates_successfully(mock_job_config_valid):
    config = JobConfig(**mock_job_config_valid)
    assert config.jobDefinition == mock_job_config_valid["jobDefinition"]


def test_job_config_invalid_arn_format():
    with pytest.raises(ValidationError, match="Invalid Batch ARN or name format"):
        JobConfig(
            jobDefinition="invalid:arn!!",
            jobQueue="invalid-queue",
            input_s3_bucket="test",
        )


def test_job_config_path_traversal_prevention():
    with pytest.raises(ValidationError, match="invalid characters"):
        JobConfig(
            jobDefinition="mock-def",
            jobQueue="mock-queue",
            input_s3_bucket="test/../bucket",
        )


def test_job_config_missing_required_fields():
    with pytest.raises(ValidationError):
        JobConfig(jobQueue="mock-queue", input_s3_bucket="test")


# ==============================================================================
# Unit Tests for Plugin Health Check
# ==============================================================================


@pytest.mark.asyncio
async def test_plugin_health_success(mock_aws_clients):
    result = await plugin_health()
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_plugin_health_no_credentials_error(mock_aws_clients):
    mock_aws_clients["mock_load_vault"].return_value = None
    with patch(
        "simulation.plugins.aws_batch_runner_plugin._session_has_real_creds",
        return_value=False,
    ):
        result = await plugin_health()
        assert result["status"] == "error"


# ==============================================================================
# Integration Tests for run_batch_job workflow
# ==============================================================================


@pytest.mark.asyncio
async def test_run_batch_job_full_workflow_success(
    mock_aws_clients, mock_job_config_valid
):
    mock_batch_client = mock_aws_clients["mock_batch_client"]
    mock_s3_client = mock_aws_clients["mock_s3_client"]

    fast_poll_config = mock_job_config_valid.copy()
    fast_poll_config["poll_interval_seconds"] = 1

    mock_batch_client.describe_jobs.side_effect = [
        {"jobs": [{"status": "RUNNING"}]},
        {"jobs": [{"status": "SUCCEEDED", "container": {}}]},
    ]

    with patch("shutil.make_archive") as mock_make_archive, patch(
        "os.path.getsize", return_value=1024
    ), patch("os.path.exists", return_value=True), patch(
        "builtins.open", mock_open(read_data='{"result": "success"}')
    ):
        mock_make_archive.return_value = "/mock/archive.tar.gz"
        result = await run_batch_job(
            fast_poll_config, "/mock/project_root", "/mock/output_dir"
        )

    assert result["success"] is True
    assert result["finalStatus"] == "SUCCEEDED"
    mock_s3_client.upload_fileobj.assert_called_once()
    mock_s3_client.download_fileobj.assert_called_once()


@pytest.mark.asyncio
async def test_run_batch_job_failure_workflow(mock_aws_clients, mock_job_config_valid):
    mock_batch_client = mock_aws_clients["mock_batch_client"]
    mock_logs_client = mock_aws_clients["mock_logs_client"]

    fast_poll_config = mock_job_config_valid.copy()
    fast_poll_config["poll_interval_seconds"] = 1

    mock_batch_client.describe_jobs.side_effect = [
        {"jobs": [{"status": "RUNNING"}]},
        {
            "jobs": [
                {
                    "status": "FAILED",
                    "statusReason": "User Error",
                    "container": {"logStreamName": "mock_stream"},
                }
            ]
        },
    ]
    mock_logs_client.get_log_events.return_value = {
        "events": [{"message": "Error log"}]
    }

    with patch("shutil.make_archive") as mock_make_archive, patch(
        "os.path.getsize", return_value=1024
    ), patch("builtins.open", mock_open()):
        mock_make_archive.return_value = "/mock/archive.tar.gz"
        result = await run_batch_job(
            fast_poll_config, "/mock/project_root", "/mock/output_dir"
        )

    assert result["success"] is False
    assert result["finalStatus"] == "FAILED"
    assert result["statusReason"] == "User Error"
    mock_logs_client.get_log_events.assert_called_once()


@pytest.mark.asyncio
async def test_run_batch_job_s3_download_failure(
    mock_aws_clients, mock_job_config_valid
):
    mock_batch_client = mock_aws_clients["mock_batch_client"]
    mock_s3_client = mock_aws_clients["mock_s3_client"]

    fast_poll_config = mock_job_config_valid.copy()
    fast_poll_config["poll_interval_seconds"] = 1

    mock_batch_client.describe_jobs.return_value = {
        "jobs": [{"status": "SUCCEEDED", "container": {}}]
    }
    mock_s3_client.download_fileobj.side_effect = ClientError({}, "op")

    with patch("shutil.make_archive") as mock_make_archive, patch(
        "os.path.getsize", return_value=1024
    ), patch("os.makedirs"), patch("builtins.open", mock_open()):
        mock_make_archive.return_value = "/mock/archive.tar.gz"
        result = await run_batch_job(
            fast_poll_config, "/mock/project_root", "/mock/output_dir"
        )

    assert result["success"] is False
    assert "Output file not found" in result["reason"]


# ==============================================================================
# Security and Resilience Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_run_batch_job_invalid_path_traversal(
    mock_aws_clients, mock_job_config_valid
):
    result = await run_batch_job(
        mock_job_config_valid, "../../project_root", "/tmp/output"
    )
    assert result["success"] is False
    assert "Path traversal" in result["reason"]


@pytest.mark.asyncio
async def test_run_batch_job_with_vault_credentials_failure(
    mock_aws_clients, mock_job_config_valid
):
    mock_session = mock_aws_clients["mock_session"]
    mock_aws_clients["mock_load_vault"].return_value = None

    await plugin_health()

    mock_session.assert_called_with(
        aws_access_key_id="mock_key_id", aws_secret_access_key="mock_secret_key"
    )
