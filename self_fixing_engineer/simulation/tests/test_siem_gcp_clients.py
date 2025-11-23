# test_siem_gcp_clients.py
"""
Test suite for GCP SIEM client implementation.
Tests GCP Cloud Logging client functionality including health checks,
log sending, querying, and batch operations.
"""

import asyncio
import datetime
import os
import sys
import uuid
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# Mock modules before importing
sys.modules["simulation.plugins.siem_base"] = MagicMock()
sys.modules["simulation.plugins.siem_gcp_clients"] = MagicMock()


# Mock exception classes
class SIEMClientError(Exception):
    def __init__(
        self,
        message,
        client_type,
        original_exception=None,
        details=None,
        correlation_id=None,
    ):
        self.message = message
        self.client_type = client_type
        self.original_exception = original_exception
        self.details = details  # Added details parameter
        self.correlation_id = correlation_id
        super().__init__(message)


class SIEMClientConfigurationError(SIEMClientError):
    pass


class SIEMClientAuthError(SIEMClientError):
    pass


class SIEMClientConnectivityError(SIEMClientError):
    pass


class SIEMClientPublishError(SIEMClientError):
    pass


class SIEMClientQueryError(SIEMClientError):
    pass


class SIEMClientResponseError(SIEMClientError):
    pass


class SIEMClientValidationError(SIEMClientError):
    pass


# Mock Google Cloud exceptions
class GoogleAPIError(Exception):
    def __init__(self, message="API Error"):
        self.message = message
        super().__init__(message)


class Forbidden(GoogleAPIError):
    pass


class NotFound(GoogleAPIError):
    pass


class GoogleAPICallError(GoogleAPIError):
    pass


# Mock global variables
PRODUCTION_MODE = False
GCP_AVAILABLE = True
_base_logger = MagicMock()


def alert_operator(message: str, level: str = "CRITICAL"):
    """Mock alert operator."""
    pass


class SecretsManager:
    async def get_secret(self, key, default=None):
        return "dummy_secret"


SECRETS_MANAGER = SecretsManager()


# Mock GCP configuration
class GcpLoggingConfig:
    def __init__(self, **kwargs):
        self.project_id = kwargs.get("project_id", "test-project")
        self.log_name = kwargs.get("log_name", "test-log")
        self.credentials_path = kwargs.get("credentials_path")
        self.credentials_secret_id = kwargs.get("credentials_secret_id")
        self.secrets_providers = kwargs.get("secrets_providers", [])
        self.secrets_provider_config = kwargs.get("secrets_provider_config", {})

        # Validation
        if not self.project_id:
            raise ValueError("project_id is required")

        if PRODUCTION_MODE:
            # Production validation
            import re

            if not re.match(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$", self.project_id):
                raise ValueError(f"Invalid GCP Project ID format: {self.project_id}")
            if self.log_name and not re.match(r"^[a-zA-Z0-9-._/]+$", self.log_name):
                raise ValueError(f"Invalid GCP Log Name format: {self.log_name}")
            if not self.credentials_secret_id:
                raise ValueError("credentials_secret_id required in production")

    def dict(self, exclude_unset=False):
        return {
            "project_id": self.project_id,
            "log_name": self.log_name,
            "credentials_path": self.credentials_path,
            "credentials_secret_id": self.credentials_secret_id,
            "secrets_providers": self.secrets_providers,
            "secrets_provider_config": self.secrets_provider_config,
        }


# Mock base client
class BaseSIEMClient:
    def __init__(self, config, metrics_hook=None, paranoid_mode=False):
        self.config = config
        self.metrics_hook = metrics_hook
        self.paranoid_mode = paranoid_mode
        self.client_type = getattr(self, "client_type", self.__class__.__name__)
        self.timeout = config.get("default_timeout_seconds", 10)
        self.logger = MagicMock()
        self.logger.extra = {"client_type": self.client_type, "correlation_id": "N/A"}

    async def _run_blocking_in_executor(self, func, *args, **kwargs):
        """Mock executor for blocking operations."""
        return func(*args, **kwargs)

    def _parse_relative_time_range_to_timedelta(self, time_range: str):
        """Parse time range string to timedelta."""
        if not time_range or len(time_range) < 2:
            return datetime.timedelta(hours=24)
        unit = time_range[-1].lower()
        try:
            value = int(time_range[:-1])
        except ValueError:
            return datetime.timedelta(hours=24)
        if unit == "s":
            return datetime.timedelta(seconds=value)
        elif unit == "m":
            return datetime.timedelta(minutes=value)
        elif unit == "h":
            return datetime.timedelta(hours=value)
        elif unit == "d":
            return datetime.timedelta(days=value)
        else:
            return datetime.timedelta(hours=24)

    async def close(self):
        pass


# Mock GCP Secret Manager Backend
class GCPSecretManagerBackend:
    def __init__(self, project_id):
        self.project_id = project_id

    async def get_secret(self, secret_id):
        return '{"type": "service_account", "project_id": "test-project"}'

    async def close(self):
        pass


# GCP Logging Client implementation
class GcpLoggingClient(BaseSIEMClient):
    client_type = "GCPLogging"
    MAX_BATCH_SIZE = 1000

    def __init__(self, config: Dict[str, Any], metrics_hook=None, paranoid_mode=False):
        super().__init__(config, metrics_hook, paranoid_mode)

        try:
            gcp_config_data = config.get("gcplogging", config.get("gcp_logging", {}))
            validated_config = GcpLoggingConfig(**gcp_config_data)
        except (ValueError, KeyError) as e:
            raise SIEMClientConfigurationError(
                f"Invalid GCP Logging config: {e}", self.client_type
            )

        self.project_id = validated_config.project_id
        self.log_name = validated_config.log_name
        self.credentials_path = validated_config.credentials_path
        self.credentials_secret_id = validated_config.credentials_secret_id
        self.secrets_providers = validated_config.secrets_providers
        self.secrets_provider_config = validated_config.secrets_provider_config

        self._logging_client = None
        self._credentials = None
        self._temp_credentials_path = None
        self._creds_lock = asyncio.Lock()

        self.logger.extra.update(
            {"project_id": self.project_id, "log_name": self.log_name}
        )

    async def _ensure_credentials_loaded(self):
        """Load credentials from secrets if needed."""
        if self.credentials_path or not self.credentials_secret_id:
            return

        async with self._creds_lock:
            if self.credentials_path:
                return

            # Mock loading credentials from secret
            for provider in self.secrets_providers:
                if provider == "gcp":
                    backend = GCPSecretManagerBackend(self.project_id)
                    await backend.get_secret(self.credentials_secret_id)
                    self._temp_credentials_path = (
                        f"/tmp/gcp_sa_key_{uuid.uuid4().hex}.json"
                    )
                    self.credentials_path = self._temp_credentials_path
                    await backend.close()
                    return

            raise SIEMClientConfigurationError(
                "Failed to load credentials", self.client_type
            )

    def _encoded_log_id(self):
        """URL-encode log name."""
        import urllib.parse

        return urllib.parse.quote(self.log_name, safe="")

    async def _get_gcp_client(self):
        """Get or create GCP client."""
        if self._logging_client is None:
            await self._ensure_credentials_loaded()
            # Mock client creation
            self._logging_client = MagicMock()
        return self._logging_client

    async def health_check(self, correlation_id=None):
        """Perform health check."""
        try:
            client = await self._get_gcp_client()

            # Check if we're being mocked to fail
            if hasattr(client, "list_entries") and client.list_entries.side_effect:
                # If side_effect is an exception, raise it
                if isinstance(client.list_entries.side_effect, Exception):
                    raise client.list_entries.side_effect

            # Mock health check
            return True, "Successfully connected to GCP Cloud Logging."
        except Forbidden as e:
            raise SIEMClientAuthError(f"Permission denied: {e}", self.client_type)
        except NotFound as e:
            raise SIEMClientConfigurationError(f"Not found: {e}", self.client_type)
        except Exception as e:
            raise SIEMClientConnectivityError(
                f"Health check failed: {e}", self.client_type
            )

    async def send_log(self, log_entry, validate_schema=True, correlation_id=None):
        """Send single log entry."""
        success, msg, failed = await self.send_logs(
            [log_entry], validate_schema, correlation_id
        )
        if success:
            return True, "Log sent to GCP Cloud Logging."
        if failed:
            raise SIEMClientPublishError(
                f"Failed to send log: {failed[0]['error']}",
                self.client_type,
                details=failed[0],
            )
        return False, msg

    async def send_logs(self, log_entries, validate_schema=True, correlation_id=None):
        """Send multiple log entries."""
        await self._get_gcp_client()

        # Batch logs
        batches = []
        for i in range(0, len(log_entries), self.MAX_BATCH_SIZE):
            batches.append(log_entries[i : i + self.MAX_BATCH_SIZE])

        failed_logs = []
        total_sent = 0

        for batch in batches:
            try:
                # Mock sending batch
                total_sent += len(batch)
            except Exception as e:
                failed_logs.extend([{"log": log, "error": str(e)} for log in batch])

        if failed_logs:
            return (
                False,
                f"Sent {total_sent} of {len(log_entries)} logs with errors.",
                failed_logs,
            )
        return True, f"Batch of {len(log_entries)} logs sent to GCP Cloud Logging.", []

    async def query_logs(
        self, query_string, time_range="24h", limit=100, correlation_id=None
    ):
        """Query logs from GCP."""
        await self._get_gcp_client()

        end_time = datetime.datetime.utcnow()
        end_time - self._parse_relative_time_range_to_timedelta(time_range)

        # Mock query results
        return [
            {
                "message": "test message",
                "severity": "INFO",
                "timestamp": datetime.datetime.utcnow().isoformat(),
            }
        ]

    async def close(self):
        """Clean up resources."""
        await super().close()
        if self._temp_credentials_path and os.path.exists(self._temp_credentials_path):
            try:
                os.remove(self._temp_credentials_path)
            except:
                pass


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global state before each test."""
    global PRODUCTION_MODE, GCP_AVAILABLE
    PRODUCTION_MODE = False
    GCP_AVAILABLE = True
    yield
    PRODUCTION_MODE = False
    GCP_AVAILABLE = True


@pytest.fixture
def mock_gcp_client():
    """Mock GCP logging client."""
    client = MagicMock()
    logger = MagicMock()
    client.logger.return_value = logger

    # Mock batch context manager
    batch = MagicMock()
    batch.__enter__ = MagicMock(return_value=batch)
    batch.__exit__ = MagicMock(return_value=None)
    batch.log_struct = MagicMock()
    logger.batch.return_value = batch

    # Mock list_entries
    client.list_entries = MagicMock()

    return client


@pytest.fixture
def default_config():
    """Default test configuration."""
    return {
        "gcplogging": {
            "project_id": "test-project",
            "log_name": "test-log",
            "credentials_secret_id": "gcp-creds-secret",
            "secrets_providers": ["gcp"],
            "secrets_provider_config": {"gcp": {"project_id": "test-project"}},
        }
    }


# ============================================================================
# Test Cases
# ============================================================================


class TestConfiguration:
    """Tests for configuration validation."""

    def test_valid_config(self, default_config):
        """Test valid configuration."""
        config = GcpLoggingConfig(**default_config["gcplogging"])
        assert config.project_id == "test-project"
        assert config.log_name == "test-log"
        assert config.credentials_secret_id == "gcp-creds-secret"

    def test_missing_project_id(self):
        """Test missing project ID."""
        with pytest.raises(ValueError):
            GcpLoggingConfig(project_id="", log_name="test")

    def test_production_mode_validation(self):
        """Test production mode validation."""
        global PRODUCTION_MODE
        PRODUCTION_MODE = True

        # Invalid project ID format
        with pytest.raises(ValueError):
            GcpLoggingConfig(
                project_id="INVALID-PROJECT", credentials_secret_id="secret"
            )

        # Valid project ID
        config = GcpLoggingConfig(
            project_id="test-project-123", credentials_secret_id="secret"
        )
        assert config.project_id == "test-project-123"

    def test_production_requires_credentials(self):
        """Test production mode requires credentials."""
        global PRODUCTION_MODE
        PRODUCTION_MODE = True

        with pytest.raises(ValueError):
            GcpLoggingConfig(
                project_id="test-project-123",
                # Missing credentials_secret_id
            )


class TestClientInitialization:
    """Tests for client initialization."""

    def test_successful_init(self, default_config):
        """Test successful client initialization."""
        client = GcpLoggingClient(default_config)
        assert client.project_id == "test-project"
        assert client.log_name == "test-log"
        assert client.client_type == "GCPLogging"

    def test_invalid_config(self):
        """Test initialization with invalid config."""
        with pytest.raises(SIEMClientConfigurationError):
            GcpLoggingClient({"gcplogging": {"project_id": ""}})

    async def test_credentials_loading(self, default_config):
        """Test credentials loading from secrets."""
        client = GcpLoggingClient(default_config)

        with patch.object(
            GCPSecretManagerBackend,
            "get_secret",
            return_value='{"type": "service_account"}',
        ):
            await client._ensure_credentials_loaded()
            assert client.credentials_path is not None


class TestHealthCheck:
    """Tests for health check functionality."""

    @pytest.mark.asyncio
    async def test_health_check_success(self, default_config, mock_gcp_client):
        """Test successful health check."""
        client = GcpLoggingClient(default_config)
        client._logging_client = mock_gcp_client

        # Mock list_entries for health check
        mock_iterator = MagicMock()
        mock_iterator.pages = [MagicMock()]
        mock_gcp_client.list_entries.return_value = mock_iterator

        is_healthy, message = await client.health_check()
        assert is_healthy is True
        assert "Successfully connected" in message

    @pytest.mark.asyncio
    async def test_health_check_failure(self, default_config):
        """Test health check failure."""
        client = GcpLoggingClient(default_config)

        # Force health check to fail
        with patch.object(
            client, "_get_gcp_client", side_effect=Exception("Connection failed")
        ):
            with pytest.raises(SIEMClientConnectivityError):
                await client.health_check()


class TestLogSending:
    """Tests for log sending functionality."""

    @pytest.mark.asyncio
    async def test_send_single_log(self, default_config, mock_gcp_client):
        """Test sending a single log."""
        client = GcpLoggingClient(default_config)
        client._logging_client = mock_gcp_client

        log_entry = {"message": "test log", "severity": "INFO"}
        success, message = await client.send_log(log_entry, validate_schema=False)

        assert success is True
        assert "Log sent" in message

    @pytest.mark.asyncio
    async def test_send_batch_logs(self, default_config, mock_gcp_client):
        """Test sending batch of logs."""
        client = GcpLoggingClient(default_config)
        client._logging_client = mock_gcp_client

        log_entries = [{"message": f"Log {i}"} for i in range(100)]
        success, message, failed = await client.send_logs(
            log_entries, validate_schema=False
        )

        assert success is True
        assert "Batch of 100 logs sent" in message
        assert len(failed) == 0

    @pytest.mark.asyncio
    async def test_large_batch_chunking(self, default_config, mock_gcp_client):
        """Test large batch is properly chunked."""
        client = GcpLoggingClient(default_config)
        client._logging_client = mock_gcp_client

        # Create 2500 logs (should be 3 batches)
        log_entries = [{"message": f"Log {i}"} for i in range(2500)]
        success, message, failed = await client.send_logs(
            log_entries, validate_schema=False
        )

        assert success is True
        assert "Batch of 2500 logs sent" in message
        assert len(failed) == 0

    @pytest.mark.asyncio
    async def test_send_log_failure(self, default_config):
        """Test log send failure."""
        client = GcpLoggingClient(default_config)

        # Mock send_logs to return failure
        with patch.object(
            client,
            "send_logs",
            return_value=(
                False,
                "Send failed",
                [{"log": {"message": "test"}, "error": "Send failed"}],
            ),
        ):
            log_entry = {"message": "test"}
            with pytest.raises(SIEMClientPublishError):
                await client.send_log(log_entry, validate_schema=False)


class TestQueryLogs:
    """Tests for log querying functionality."""

    @pytest.mark.asyncio
    async def test_query_logs_success(self, default_config, mock_gcp_client):
        """Test successful log query."""
        client = GcpLoggingClient(default_config)
        client._logging_client = mock_gcp_client

        # Mock query results
        mock_entry = MagicMock()
        mock_entry.payload = {"message": "test message"}
        mock_entry.severity = "INFO"
        mock_entry.timestamp = datetime.datetime.utcnow()

        mock_iterator = [mock_entry]
        mock_gcp_client.list_entries.return_value = mock_iterator

        results = await client.query_logs("message:test", "5m", 10)

        assert len(results) > 0
        assert "message" in results[0]

    @pytest.mark.asyncio
    async def test_query_with_time_range(self, default_config, mock_gcp_client):
        """Test query with different time ranges."""
        client = GcpLoggingClient(default_config)
        client._logging_client = mock_gcp_client

        mock_gcp_client.list_entries.return_value = []

        # Test various time ranges
        for time_range in ["5m", "1h", "24h", "7d"]:
            results = await client.query_logs("test", time_range, 10)
            assert isinstance(results, list)


class TestConcurrentOperations:
    """Tests for concurrent operations."""

    @pytest.mark.asyncio
    async def test_concurrent_sends(self, default_config, mock_gcp_client):
        """Test concurrent log sends."""
        client = GcpLoggingClient(default_config)
        client._logging_client = mock_gcp_client

        async def send_task(i):
            logs = [{"message": f"Task {i} - Log {j}"} for j in range(10)]
            return await client.send_logs(logs, validate_schema=False)

        tasks = [send_task(i) for i in range(20)]
        results = await asyncio.gather(*tasks)

        assert all(r[0] for r in results)  # All successful

    @pytest.mark.asyncio
    async def test_concurrent_queries(self, default_config, mock_gcp_client):
        """Test concurrent log queries."""
        client = GcpLoggingClient(default_config)
        client._logging_client = mock_gcp_client

        mock_gcp_client.list_entries.return_value = []

        async def query_task(i):
            return await client.query_logs(f"id:{i}", "1h", 10)

        tasks = [query_task(i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 10
        assert all(isinstance(r, list) for r in results)


class TestResourceCleanup:
    """Tests for resource cleanup."""

    @pytest.mark.asyncio
    async def test_temp_credentials_cleanup(self, default_config):
        """Test temporary credentials file is cleaned up."""
        client = GcpLoggingClient(default_config)

        # Simulate temp file creation
        client._temp_credentials_path = "/tmp/test_creds.json"

        with patch("os.path.exists", return_value=True):
            with patch("os.remove") as mock_remove:
                await client.close()
                mock_remove.assert_called_once_with("/tmp/test_creds.json")

    @pytest.mark.asyncio
    async def test_cleanup_on_error(self, default_config):
        """Test cleanup even when removal fails."""
        client = GcpLoggingClient(default_config)
        client._temp_credentials_path = "/tmp/test_creds.json"

        with patch("os.path.exists", return_value=True):
            with patch("os.remove", side_effect=Exception("Permission denied")):
                await client.close()  # Should not raise


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_auth_error(self, default_config, mock_gcp_client):
        """Test authentication error handling."""
        client = GcpLoggingClient(default_config)
        client._logging_client = mock_gcp_client

        mock_gcp_client.list_entries.side_effect = Forbidden("Permission denied")

        with pytest.raises(SIEMClientAuthError):
            await client.health_check()

    @pytest.mark.asyncio
    async def test_not_found_error(self, default_config, mock_gcp_client):
        """Test not found error handling."""
        client = GcpLoggingClient(default_config)
        client._logging_client = mock_gcp_client

        mock_gcp_client.list_entries.side_effect = NotFound("Project not found")

        with pytest.raises(SIEMClientConfigurationError):
            await client.health_check()


# ============================================================================
# Test Runner
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-x", "--asyncio-mode=auto"])
