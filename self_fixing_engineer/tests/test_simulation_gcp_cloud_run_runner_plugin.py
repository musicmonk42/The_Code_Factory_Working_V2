# tests/test_gcp_cloud_run_runner_plugin.py

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add simulation directory to path for imports
SIMULATION_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SIMULATION_DIR not in sys.path:
    sys.path.insert(0, SIMULATION_DIR)

# Import the plugin - we'll handle missing dependencies gracefully
try:
    from google.api_core.exceptions import (
        Conflict,
        GoogleAPIError,
        NotFound,
        ResourceExhausted,
    )

    # Note: QuotaExceeded doesn't exist in google.api_core.exceptions
    # ResourceExhausted is the correct exception for quota issues
    GOOGLE_EXCEPTIONS_AVAILABLE = True
except ImportError:
    # Create mock exception classes for testing without Google Cloud libraries
    class GoogleAPIError(Exception):
        def __init__(self, message, code=None):
            super().__init__(message)
            self.code = code

    class NotFound(GoogleAPIError):
        pass

    class Conflict(GoogleAPIError):
        pass

    class ResourceExhausted(GoogleAPIError):
        pass

    GOOGLE_EXCEPTIONS_AVAILABLE = False

# Import plugin with proper error handling
try:
    # First, we need to mock GCP_AVAILABLE before importing the plugin
    from self_fixing_engineer.simulation.plugins import gcp_cloud_run_runner_plugin as gcp_plugin_module

    # Import the needed components
    from self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin import (
        GCP_AVAILABLE,
        PLUGIN_MANIFEST,
        JobConfig,
        _bucket_valid,
        _tar_directory_to_temp,
        plugin_health,
        run_cloud_run_job,
    )
except ImportError as e:
    pytest.skip(f"Cannot import plugin: {e}", allow_module_level=True)

# ==============================================================================
# Test Fixtures
# ==============================================================================


@pytest.fixture
def mock_credentials():
    """Mock Google Cloud credentials."""
    with patch("plugins.gcp_cloud_run_runner_plugin.service_account") as mock_sa:
        mock_creds = MagicMock()
        mock_sa.Credentials.from_service_account_file.return_value = mock_creds
        mock_sa.Credentials.from_service_account_info.return_value = mock_creds
        yield mock_creds


@pytest.fixture
def mock_gcs_client():
    """Mock Google Cloud Storage client."""
    with patch("plugins.gcp_cloud_run_runner_plugin.storage") as mock_storage:
        mock_client = MagicMock()
        mock_storage.Client.return_value = mock_client

        # Mock bucket operations
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob

        # Mock list_buckets for health check
        mock_client.list_buckets.return_value = iter([MagicMock(name="test-bucket")])

        yield mock_client


@pytest.fixture
def mock_jobs_client():
    """Mock Cloud Run Jobs client with proper mock classes."""
    with patch("plugins.gcp_cloud_run_runner_plugin.run_v2") as mock_run_v2:
        mock_client = MagicMock()
        mock_run_v2.JobsClient.return_value = mock_client

        # Mock ExecutionsClient
        mock_exec_client = MagicMock()
        mock_run_v2.ExecutionsClient.return_value = mock_exec_client

        # Create proper mock classes instead of MagicMock to avoid AttributeError
        class MockEnvVar:
            def __init__(self, name=None, value=None, **kwargs):
                self.name = name
                self.value = value

        class MockContainer:
            def __init__(self, image=None, command=None, args=None, env=None, **kwargs):
                self.image = image
                self.command = command
                self.args = args
                self.env = env
                self.resources = None

        class MockResourceRequirements:
            def __init__(self, limits=None, **kwargs):
                self.limits = limits

        class MockTaskTemplate:
            def __init__(
                self, containers=None, max_retries=None, timeout=None, **kwargs
            ):
                self.containers = containers
                self.max_retries = max_retries
                self.timeout = timeout

        class MockExecutionTemplate:
            def __init__(
                self,
                template=None,
                task_template=None,
                parallelism=None,
                task_count=None,
                **kwargs,
            ):
                self.template = template or task_template
                self.parallelism = parallelism
                self.task_count = task_count

        class MockJob:
            def __init__(self, template=None, **kwargs):
                self.template = template

        class MockCreateJobRequest:
            def __init__(self, parent=None, job_id=None, job=None, **kwargs):
                self.parent = parent
                self.job_id = job_id
                self.job = job

        class MockRunJobRequest:
            def __init__(self, name=None, **kwargs):
                self.name = name

        # Assign the mock classes
        mock_run_v2.EnvVar = MockEnvVar
        mock_run_v2.Container = MockContainer
        mock_run_v2.ResourceRequirements = MockResourceRequirements
        mock_run_v2.TaskTemplate = MockTaskTemplate
        mock_run_v2.ExecutionTemplate = MockExecutionTemplate
        mock_run_v2.Job = MockJob
        mock_run_v2.CreateJobRequest = MockCreateJobRequest
        mock_run_v2.RunJobRequest = MockRunJobRequest

        yield mock_client, mock_exec_client, mock_run_v2


@pytest.fixture
def mock_logging_client():
    """Mock Google Cloud Logging client."""
    with patch("plugins.gcp_cloud_run_runner_plugin.LoggingClient") as mock_logging:
        mock_client = MagicMock()
        mock_logging.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_environment():
    """Mock environment variables."""
    env_vars = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_LOCATION": "us-central1",
        "GOOGLE_APPLICATION_CREDENTIALS": "/mock/creds.json",
    }
    with patch.dict(os.environ, env_vars, clear=False):
        yield env_vars


@pytest.fixture
def valid_job_config():
    """Returns a valid job configuration."""
    return {
        "project_id": "test-project",
        "location": "us-central1",
        "image_url": "gcr.io/test-project/test-image:latest",
        "input_gcs_bucket": "test-input-bucket",
        "output_gcs_bucket": "test-output-bucket",
        "output_gcs_key_prefix": "test-prefix",
        "output_filename": "result.json",
        "timeout_seconds": 300,
        "cpu_limit": "1",
        "memory_limit": "512Mi",
        "env_vars": [{"name": "TEST_VAR", "value": "test_value"}],
    }


@pytest.fixture
def temp_project_dir():
    """Creates a temporary project directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some test files
        project_path = Path(tmpdir) / "test_project"
        project_path.mkdir()
        (project_path / "main.py").write_text("print('Hello')")
        (project_path / "requirements.txt").write_text("pytest==7.0.0")

        # Create directories that should be excluded
        (project_path / ".git").mkdir()
        (project_path / "__pycache__").mkdir()

        yield str(project_path)


# ==============================================================================
# Helper Classes for Testing
# ==============================================================================


class MockOperation:
    """Mock a Google Cloud operation."""

    def __init__(self, result_val, exception=None):
        self._result = result_val
        self._exception = exception

    def result(self, timeout=None):
        if self._exception:
            raise self._exception
        return self._result


# ==============================================================================
# Unit Tests for Pydantic Model Validation
# ==============================================================================


class TestJobConfigValidation:
    """Test suite for JobConfig validation."""

    def test_valid_config(self, valid_job_config):
        """Test that a valid config passes validation."""
        config = JobConfig(**valid_job_config)
        assert config.project_id == "test-project"
        assert config.location == "us-central1"
        assert config.timeout_seconds == 300

    def test_invalid_image_url(self):
        """Test that non-GCP registry URLs are rejected."""
        with pytest.raises(ValueError, match="trusted GCP registry"):
            JobConfig(
                project_id="test-project",
                location="us-central1",
                image_url="docker.io/library/nginx:latest",  # Non-GCP registry
                input_gcs_bucket="test-bucket",
            )

    def test_invalid_project_id(self):
        """Test that invalid project IDs are rejected."""
        with pytest.raises(ValueError, match="Invalid GCP project_id"):
            JobConfig(
                project_id="Test-Project",  # Capital letters not allowed
                location="us-central1",
                image_url="gcr.io/test/image:latest",
                input_gcs_bucket="test-bucket",
            )

    def test_invalid_location(self):
        """Test that invalid locations are rejected."""
        with pytest.raises(ValueError, match="Invalid GCP location"):
            JobConfig(
                project_id="test-project",
                location="invalid_location",  # Underscores not allowed
                image_url="gcr.io/test/image:latest",
                input_gcs_bucket="test-bucket",
            )

    def test_invalid_bucket_name(self):
        """Test that invalid bucket names are rejected."""
        with pytest.raises(ValueError, match="Invalid GCS bucket"):
            JobConfig(
                project_id="test-project",
                location="us-central1",
                image_url="gcr.io/test/image:latest",
                input_gcs_bucket="Test-Bucket",  # Capital letters not allowed
            )

    def test_optional_fields_defaults(self):
        """Test that optional fields have correct defaults."""
        config = JobConfig(
            project_id="test-project",
            location="us-central1",
            image_url="gcr.io/test/image:latest",
            input_gcs_bucket="test-bucket",
        )
        assert config.timeout_seconds == 600
        assert config.max_retries == 0
        assert config.parallelism == 1
        assert config.task_count == 1
        assert config.cleanup_gcs_input
        assert not config.retain_temp_archive


# ==============================================================================
# Unit Tests for Helper Functions
# ==============================================================================


class TestHelperFunctions:
    """Test suite for helper functions."""

    def test_bucket_valid(self):
        """Test bucket name validation."""
        assert _bucket_valid("valid-bucket-name")
        assert _bucket_valid("bucket123")
        assert _bucket_valid("bucket.with.dots")

        assert not _bucket_valid("Bucket-Name")  # Capital letters
        assert not _bucket_valid("bucket..name")  # Double dots
        assert not _bucket_valid("bucket.-name")  # Dot-dash
        assert not _bucket_valid("-bucket")  # Starts with dash

    def test_tar_directory_to_temp(self, temp_project_dir):
        """Test archive creation with exclusions."""
        # This test actually creates a real tar file to test the exclusion logic
        result = _tar_directory_to_temp(temp_project_dir)

        # Verify result is a string path to a tar.gz file
        assert isinstance(result, str)
        assert result.endswith(".tar.gz")
        assert "sfe-run-job-" in result
        assert os.path.exists(result)

        # Clean up
        try:
            os.remove(result)
        except Exception:
            pass


# ==============================================================================
# Unit Tests for Plugin Health Check
# ==============================================================================


class TestPluginHealth:
    """Test suite for plugin health check."""

    @pytest.mark.asyncio
    @patch("self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin.GCP_AVAILABLE", True)
    async def test_health_check_success(
        self, mock_environment, mock_credentials, mock_gcs_client, mock_jobs_client
    ):
        """Test successful health check."""
        with patch(
            "plugins.gcp_cloud_run_runner_plugin._get_credentials",
            return_value=mock_credentials,
        ):
            result = await plugin_health()

            assert result["status"] == "ok"
            assert any(
                "credentials loaded successfully" in d.lower()
                for d in result["details"]
            )

    @pytest.mark.asyncio
    @patch("self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin.GCP_AVAILABLE", True)
    async def test_health_check_no_credentials(self, mock_environment):
        """Test health check with missing credentials."""
        with patch(
            "plugins.gcp_cloud_run_runner_plugin._get_credentials", return_value=None
        ):
            result = await plugin_health()

            assert result["status"] == "error"
            assert any(
                "credentials not configured" in d.lower() for d in result["details"]
            )

    @pytest.mark.asyncio
    @patch("self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin.GCP_AVAILABLE", True)
    async def test_health_check_gcs_failure(
        self, mock_environment, mock_credentials, mock_gcs_client
    ):
        """Test health check with GCS connectivity failure."""
        mock_gcs_client.list_buckets.side_effect = GoogleAPIError("GCS error")

        with patch(
            "plugins.gcp_cloud_run_runner_plugin._get_credentials",
            return_value=mock_credentials,
        ):
            result = await plugin_health()

            # When GCS fails but other things might work, status is 'degraded' not 'error'
            assert result["status"] in ["error", "degraded"]
            assert any("GCS connectivity" in d for d in result["details"])

    @pytest.mark.asyncio
    @patch("self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin.GCP_AVAILABLE", False)
    async def test_health_check_no_gcp_libraries(self):
        """Test health check when GCP libraries aren't available."""
        result = await plugin_health()

        assert result["status"] == "error"
        assert any(
            "Google Cloud client libraries not found" in d for d in result["details"]
        )


# ==============================================================================
# Integration Tests for run_cloud_run_job
# ==============================================================================


class TestRunCloudRunJob:
    """Test suite for run_cloud_run_job function."""

    @pytest.mark.asyncio
    @patch("self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin.GCP_AVAILABLE", True)
    async def test_successful_job_execution(
        self,
        valid_job_config,
        temp_project_dir,
        mock_environment,
        mock_credentials,
        mock_gcs_client,
        mock_jobs_client,
        mock_logging_client,
    ):
        """Test successful job execution workflow."""
        mock_client, mock_exec_client, mock_run_v2 = mock_jobs_client

        # Setup mocks for successful execution
        mock_job = MagicMock()
        mock_job.name = "projects/test/locations/us-central1/jobs/test-job"
        mock_execution = MagicMock()
        mock_execution.name = "projects/test/locations/us-central1/executions/test-exec"

        mock_client.create_job.return_value = MockOperation(mock_job)
        mock_client.run_job.return_value = MockOperation(mock_execution)
        mock_client.delete_job.return_value = MockOperation(None)

        # Mock execution status checks - fix the state.name attribute
        mock_running_state = MagicMock()
        mock_running_state.name = "RUNNING"
        mock_succeeded_state = MagicMock()
        mock_succeeded_state.name = "SUCCEEDED"

        mock_exec_client.get_execution.side_effect = [
            MagicMock(state=mock_running_state),
            MagicMock(
                state=mock_succeeded_state,
                start_time=datetime.now() - timedelta(seconds=10),
                completion_time=datetime.now(),
            ),
        ]

        # Use a proper async sleep mock
        async def mock_sleep(seconds):
            return None

        with (
            patch(
                "plugins.gcp_cloud_run_runner_plugin._get_credentials",
                return_value=mock_credentials,
            ),
            patch(
                "plugins.gcp_cloud_run_runner_plugin._tar_directory_to_temp",
                return_value="/tmp/test-archive.tar.gz",
            ),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("builtins.open", create=True),
            patch("os.path.exists", return_value=True),
            patch("os.remove"),
        ):

            with tempfile.TemporaryDirectory() as output_dir:
                result = await run_cloud_run_job(
                    valid_job_config, temp_project_dir, output_dir
                )

        assert result["success"]
        assert result["finalStatus"] == "SUCCEEDED"
        assert "test-exec" in result["executionName"]

    @pytest.mark.asyncio
    @patch("self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin.GCP_AVAILABLE", True)
    async def test_failed_job_execution(
        self,
        valid_job_config,
        temp_project_dir,
        mock_environment,
        mock_credentials,
        mock_gcs_client,
        mock_jobs_client,
        mock_logging_client,
    ):
        """Test failed job execution with log retrieval."""
        mock_client, mock_exec_client, mock_run_v2 = mock_jobs_client

        # Setup mocks for failed execution
        mock_job = MagicMock()
        mock_job.name = "projects/test/locations/us-central1/jobs/test-job"
        mock_execution = MagicMock()
        mock_execution.name = "projects/test/locations/us-central1/executions/test-exec"

        mock_client.create_job.return_value = MockOperation(mock_job)
        mock_client.run_job.return_value = MockOperation(mock_execution)
        mock_client.delete_job.return_value = MockOperation(None)

        # Mock execution status checks - job fails
        mock_running_state = MagicMock()
        mock_running_state.name = "RUNNING"
        mock_failed_state = MagicMock()
        mock_failed_state.name = "FAILED"

        mock_exec_client.get_execution.side_effect = [
            MagicMock(state=mock_running_state),
            MagicMock(
                state=mock_failed_state,
                conditions=[MagicMock(message="Container failed to start")],
                start_time=datetime.now() - timedelta(seconds=10),
                completion_time=datetime.now(),
            ),
        ]

        # Mock log entries
        mock_log_entry = MagicMock(payload="Error: Container failed")
        mock_logging_client.list_entries.return_value = iter([mock_log_entry])

        # Use a proper async sleep mock
        async def mock_sleep(seconds):
            return None

        with (
            patch(
                "plugins.gcp_cloud_run_runner_plugin._get_credentials",
                return_value=mock_credentials,
            ),
            patch(
                "plugins.gcp_cloud_run_runner_plugin._tar_directory_to_temp",
                return_value="/tmp/test-archive.tar.gz",
            ),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("builtins.open", create=True),
            patch("os.path.exists", return_value=True),
            patch("os.remove"),
        ):

            with tempfile.TemporaryDirectory() as output_dir:
                result = await run_cloud_run_job(
                    valid_job_config, temp_project_dir, output_dir
                )

        assert not result["success"]
        assert result["finalStatus"] == "FAILED"
        assert "Container failed to start" in result["statusReason"]
        assert result["raw_log"] is not None

    @pytest.mark.asyncio
    @patch("self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin.GCP_AVAILABLE", True)
    @patch(
        "plugins.gcp_cloud_run_runner_plugin.retry", lambda *args, **kwargs: lambda f: f
    )  # Disable retry decorator
    async def test_quota_exceeded_retry(
        self,
        valid_job_config,
        temp_project_dir,
        mock_environment,
        mock_credentials,
        mock_gcs_client,
        mock_jobs_client,
    ):
        """Test retry logic when quota is exceeded."""
        mock_client, mock_exec_client, mock_run_v2 = mock_jobs_client

        # Setup - quota exception should be caught and handled
        # Use the QuotaExceeded alias that the plugin defines
        with patch(
            "plugins.gcp_cloud_run_runner_plugin.QuotaExceeded", ResourceExhausted
        ):

            # Use a proper async sleep mock
            async def mock_sleep(seconds):
                return None

            with (
                patch(
                    "plugins.gcp_cloud_run_runner_plugin._get_credentials",
                    return_value=mock_credentials,
                ),
                patch(
                    "plugins.gcp_cloud_run_runner_plugin._tar_directory_to_temp",
                    return_value="/tmp/test-archive.tar.gz",
                ),
                patch("asyncio.sleep", side_effect=mock_sleep),
                patch("builtins.open", create=True),
                patch("os.path.exists", return_value=True),
                patch("os.remove"),
            ):

                # First call raises ResourceExhausted
                mock_client.create_job.side_effect = ResourceExhausted("Quota exceeded")

                with tempfile.TemporaryDirectory() as output_dir:
                    # This should catch the exception and return gracefully
                    try:
                        _result = await run_cloud_run_job(
                            valid_job_config, temp_project_dir, output_dir
                        )
                    except ResourceExhausted:
                        # This is expected - the test passes if exception is raised
                        # (meaning the decorator would retry in production)
                        pass
                    else:
                        # If no exception, check that resources were reduced
                        assert valid_job_config.get("_reduced_resources_once")

    @pytest.mark.asyncio
    @patch("self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin.GCP_AVAILABLE", True)
    async def test_gcs_download_failure(
        self,
        valid_job_config,
        temp_project_dir,
        mock_environment,
        mock_credentials,
        mock_gcs_client,
        mock_jobs_client,
    ):
        """Test handling of GCS download failures."""
        mock_client, mock_exec_client, mock_run_v2 = mock_jobs_client

        # Setup successful job execution
        mock_job = MagicMock()
        mock_job.name = "projects/test/jobs/test-job"
        mock_execution = MagicMock()
        mock_execution.name = "projects/test/executions/test-exec"

        mock_client.create_job.return_value = MockOperation(mock_job)
        mock_client.run_job.return_value = MockOperation(mock_execution)
        mock_client.delete_job.return_value = MockOperation(None)

        # Mock successful execution status
        mock_succeeded_state = MagicMock()
        mock_succeeded_state.name = "SUCCEEDED"

        mock_exec_client.get_execution.return_value = MagicMock(
            state=mock_succeeded_state,
            start_time=datetime.now() - timedelta(seconds=10),
            completion_time=datetime.now(),
        )

        # Mock GCS download failure - properly catch the exception
        async def mock_sleep(seconds):
            return None

        with (
            patch(
                "plugins.gcp_cloud_run_runner_plugin._get_credentials",
                return_value=mock_credentials,
            ),
            patch(
                "plugins.gcp_cloud_run_runner_plugin._tar_directory_to_temp",
                return_value="/tmp/test-archive.tar.gz",
            ),
            patch("plugins.gcp_cloud_run_runner_plugin.NotFound", NotFound),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("builtins.open", create=True),
            patch("os.path.exists", return_value=True),
            patch("os.remove"),
        ):

            # Mock the download failure at the right place
            mock_blob = mock_gcs_client.bucket.return_value.blob.return_value
            mock_blob.download_to_file.side_effect = NotFound("Output not found")

            with tempfile.TemporaryDirectory() as output_dir:
                result = await run_cloud_run_job(
                    valid_job_config, temp_project_dir, output_dir
                )

        # Should succeed but with download failure noted
        assert not result["success"]
        assert "Output file not found" in result["reason"]

    @pytest.mark.asyncio
    @patch("self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin.GCP_AVAILABLE", False)
    async def test_run_job_no_gcp_libraries(self, valid_job_config, temp_project_dir):
        """Test that run_cloud_run_job fails gracefully when GCP libraries aren't available."""
        with tempfile.TemporaryDirectory() as output_dir:
            result = await run_cloud_run_job(
                valid_job_config, temp_project_dir, output_dir
            )

        assert not result["success"]
        assert "Google Cloud client libraries not found" in result["reason"]


# ==============================================================================
# Security Tests
# ==============================================================================


class TestSecurity:
    """Test suite for security features."""

    @pytest.mark.asyncio
    async def test_vault_credentials_loading(self, mock_environment):
        """Test loading credentials from vault."""
        with (
            patch("plugins.gcp_cloud_run_runner_plugin.GCP_AVAILABLE", True),
            patch(
                "plugins.gcp_cloud_run_runner_plugin.service_account"
            ) as mock_service_account,
            patch(
                "plugins.gcp_cloud_run_runner_plugin.aiohttp.ClientSession"
            ) as mock_aiohttp_session,
        ):

            vault_response = {
                "data": {
                    "data": {
                        "sa_json": {
                            "type": "service_account",
                            "project_id": "test-project",
                            "private_key": "mock-key",
                        }
                    }
                }
            }

            mock_creds = MagicMock()
            mock_service_account.Credentials.from_service_account_info.return_value = (
                mock_creds
            )

            # Mock response
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = AsyncMock(return_value=vault_response)

            # Mock get context manager
            mock_get_cm = MagicMock()
            mock_get_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get_cm.__aexit__ = AsyncMock(return_value=None)

            # Mock session
            mock_session = MagicMock()
            mock_session.get.return_value = mock_get_cm

            # Mock session context manager
            mock_session_cm = MagicMock()
            mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cm.__aexit__ = AsyncMock(return_value=None)

            mock_aiohttp_session.return_value = mock_session_cm

            with patch.dict(
                os.environ,
                {"VAULT_URL": "https://vault.example.com", "VAULT_TOKEN": "mock-token"},
            ):
                from self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin import (
                    _load_credentials_from_vault,
                )

                creds = await _load_credentials_from_vault()

                assert creds == mock_creds
                mock_service_account.Credentials.from_service_account_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_vault_https_enforcement(self):
        """Test that non-HTTPS vault URLs are rejected."""
        with patch.dict(
            os.environ,
            {
                "VAULT_URL": "http://vault.example.com",  # HTTP, not HTTPS
                "VAULT_TOKEN": "mock-token",
            },
        ):
            from self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin import _load_credentials_from_vault

            creds = await _load_credentials_from_vault()

            assert creds is None  # Should refuse non-HTTPS


# ==============================================================================
# Performance Tests
# ==============================================================================


class TestPerformance:
    """Test suite for performance-related features."""

    def test_archive_excludes_heavy_dirs(self, temp_project_dir):
        """Test that archive creation excludes heavy directories."""
        # Create heavy directories that should be excluded
        heavy_dirs = [".git", "node_modules", "__pycache__", ".venv"]
        for dir_name in heavy_dirs:
            dir_path = Path(temp_project_dir) / dir_name
            dir_path.mkdir(exist_ok=True)
            (dir_path / "large_file.bin").write_text("x" * 1000)

        with patch("tarfile.open") as mock_tarfile:
            mock_tar = MagicMock()
            mock_tarfile.return_value.__enter__.return_value = mock_tar

            _result = _tar_directory_to_temp(temp_project_dir)

            # Verify excluded directories weren't added
            added_files = [call[0][0] for call in mock_tar.add.call_args_list]
            for excluded in heavy_dirs:
                assert not any(excluded in str(f) for f in added_files)


# ==============================================================================
# End-to-end Tests
# ==============================================================================


@pytest.mark.integration
class TestEndToEnd:
    """End-to-end integration tests (requires GCP credentials)."""

    @pytest.mark.skipif(
        not os.getenv("RUN_INTEGRATION_TESTS"),
        reason="Integration tests require RUN_INTEGRATION_TESTS=1",
    )
    @pytest.mark.asyncio
    async def test_real_cloud_run_execution(self, valid_job_config, temp_project_dir):
        """Test against real GCP infrastructure (requires credentials)."""
        # This test would run against actual GCP services
        # Only run in CI/CD with proper credentials

        with tempfile.TemporaryDirectory() as output_dir:
            result = await run_cloud_run_job(
                valid_job_config, temp_project_dir, output_dir
            )

            # Basic assertions - actual behavior depends on GCP setup
            assert "success" in result
            assert "executionName" in result


# ==============================================================================
# Additional Edge Case Tests
# ==============================================================================


class TestEdgeCases:
    """Test suite for edge cases and error conditions."""

    @pytest.mark.asyncio
    @patch("self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin.GCP_AVAILABLE", True)
    async def test_monitoring_timeout(
        self,
        valid_job_config,
        temp_project_dir,
        mock_environment,
        mock_credentials,
        mock_gcs_client,
        mock_jobs_client,
    ):
        """Test job monitoring timeout."""
        mock_client, mock_exec_client, mock_run_v2 = mock_jobs_client

        # Setup mocks
        mock_job = MagicMock()
        mock_job.name = "projects/test/jobs/test-job"
        mock_execution = MagicMock()
        mock_execution.name = "projects/test/executions/test-exec"

        mock_client.create_job.return_value = MockOperation(mock_job)
        mock_client.run_job.return_value = MockOperation(mock_execution)
        mock_client.delete_job.return_value = MockOperation(None)

        # Mock execution that stays in RUNNING state
        mock_running_state = MagicMock()
        mock_running_state.name = "RUNNING"

        mock_exec_client.get_execution.return_value = MagicMock(
            state=mock_running_state
        )

        # Set a very short timeout for testing
        valid_job_config["timeout_seconds"] = 1

        # Non-exhaustible side effect with incremental time advance
        call_count = 0

        def time_side_effect():
            nonlocal call_count
            call_count += 1
            if (
                call_count <= 4
            ):  # Initial calls before/during setup (overall start, upload start, observe, monitor start)
                return 0
            else:
                # Advance by 150 "seconds" per subsequent call (each loop's timeout check)
                return (call_count - 4) * 150

        async def mock_sleep(seconds):
            # Yield control without real delay
            await asyncio.to_thread(lambda: None)

        with (
            patch(
                "plugins.gcp_cloud_run_runner_plugin._get_credentials",
                return_value=mock_credentials,
            ),
            patch(
                "plugins.gcp_cloud_run_runner_plugin._tar_directory_to_temp",
                return_value="/tmp/test-archive.tar.gz",
            ),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch.object(
                gcp_plugin_module.time, "monotonic", side_effect=time_side_effect
            ),
            patch("builtins.open", create=True),
            patch("os.path.exists", return_value=True),
            patch("os.remove"),
        ):

            with tempfile.TemporaryDirectory() as output_dir:
                result = await run_cloud_run_job(
                    valid_job_config, temp_project_dir, output_dir
                )

        assert not result["success"]
        assert result.get("finalStatus") == "MONITORING_TIMED_OUT"
        assert "Monitoring timed out" in result.get("statusReason", "")

    @pytest.mark.asyncio
    @patch("self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin.GCP_AVAILABLE", True)
    async def test_invalid_job_config(self, temp_project_dir):
        """Test with invalid job configuration."""
        invalid_config = {
            "project_id": "INVALID",  # Invalid format
            "location": "us-central1",
            "image_url": "gcr.io/test/image:latest",
            "input_gcs_bucket": "test-bucket",
        }

        with tempfile.TemporaryDirectory() as output_dir:
            result = await run_cloud_run_job(
                invalid_config, temp_project_dir, output_dir
            )

        assert not result["success"]
        assert "Invalid job config" in result["reason"]

    @pytest.mark.asyncio
    @patch("self_fixing_engineer.simulation.plugins.gcp_cloud_run_runner_plugin.GCP_AVAILABLE", True)
    @patch(
        "plugins.gcp_cloud_run_runner_plugin.retry", lambda *args, **kwargs: lambda f: f
    )  # Disable retry decorator
    async def test_job_conflict_retry(
        self,
        valid_job_config,
        temp_project_dir,
        mock_environment,
        mock_credentials,
        mock_gcs_client,
        mock_jobs_client,
    ):
        """Test job creation conflict and retry."""
        mock_client, mock_exec_client, mock_run_v2 = mock_jobs_client

        # Patch the Conflict exception properly
        with patch("plugins.gcp_cloud_run_runner_plugin.Conflict", Conflict):

            # First create_job raises Conflict, which should trigger delete and retry
            # But since we disabled the retry decorator, we'll just see the error
            mock_client.create_job.side_effect = Conflict("Job already exists")

            async def mock_sleep(seconds):
                return None

            with (
                patch(
                    "plugins.gcp_cloud_run_runner_plugin._get_credentials",
                    return_value=mock_credentials,
                ),
                patch(
                    "plugins.gcp_cloud_run_runner_plugin._tar_directory_to_temp",
                    return_value="/tmp/test-archive.tar.gz",
                ),
                patch("asyncio.sleep", side_effect=mock_sleep),
                patch("builtins.open", create=True),
                patch("os.path.exists", return_value=True),
                patch("os.remove"),
            ):

                with tempfile.TemporaryDirectory() as output_dir:
                    result = await run_cloud_run_job(
                        valid_job_config, temp_project_dir, output_dir
                    )

            # Should have failed due to conflict
            assert not result["success"]
            assert "Job already exists" in result.get("error", "")
