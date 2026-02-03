import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- Third-party imports for simulating SDK errors ---

# --- Robustly add the project root to the Python path ---
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

# --- Import the module under test ---
from self_fixing_engineer.simulation.plugins.cloud_logging_integrations import (
    AzureMonitorLogger,
    CloudLoggingAuthError,
    CloudLoggingConfigurationError,
    CloudLoggingResponseError,
    CloudWatchLogger,
    GCPLogger,
    get_cloud_logger,
)


class MockAWSClientError(Exception):
    """Mock exception to simulate botocore.exceptions.ClientError without requiring the SDK."""

    def __init__(self, response=None):
        self.response = response or {}
        super().__init__()


# ==============================================================================
# Refactored, Provider-Specific Pytest Fixtures
# ==============================================================================


@pytest.fixture
def aws_mocks():
    """A targeted fixture that only mocks AWS dependencies."""
    with (
        patch("boto3.client") as mock_boto3,
        patch("simulation.plugins.cloud_logging_integrations.AWS_AVAILABLE", True),
        patch(
            "simulation.plugins.cloud_logging_integrations.AWSClientError",
            MockAWSClientError,
        ),
    ):

        boto3_instance = MagicMock()
        mock_boto3.return_value = boto3_instance

        # Setup default successful responses
        boto3_instance.describe_log_groups.return_value = {
            "logGroups": [{"logGroupName": "mock-group"}]
        }
        boto3_instance.put_log_events.return_value = {
            "nextSequenceToken": "new-mock-token"
        }
        boto3_instance.describe_log_streams.return_value = {
            "logStreams": [{"uploadSequenceToken": "mock-token"}]
        }
        boto3_instance.start_query.return_value = {"queryId": "mock-query-id"}
        boto3_instance.get_query_results.return_value = {
            "status": "Complete",
            "results": [],
        }

        yield boto3_instance


@pytest.fixture
def gcp_mocks():
    """A targeted fixture that only mocks GCP dependencies."""
    with (
        patch(
            "simulation.plugins.cloud_logging_integrations.gcp_logging_sdk.Client",
            create=True,
        ) as mock_gcp_client,
        patch("simulation.plugins.cloud_logging_integrations.GCP_AVAILABLE", True),
    ):

        gcp_instance = MagicMock()
        mock_gcp_client.return_value = gcp_instance

        # Setup batch context manager
        batch_mock = MagicMock()
        batch_instance = MagicMock()
        batch_mock.__enter__ = MagicMock(return_value=batch_instance)
        batch_mock.__exit__ = MagicMock(return_value=None)
        gcp_instance.batch.return_value = batch_mock

        yield gcp_instance


@pytest.fixture
def azure_mocks():
    """A targeted fixture that only mocks Azure dependencies."""
    with (
        patch(
            "simulation.plugins.cloud_logging_integrations.LogsQueryClient", create=True
        ) as mock_query,
        patch(
            "simulation.plugins.cloud_logging_integrations.DefaultAzureCredential",
            create=True,
        ) as mock_cred,
        patch(
            "simulation.plugins.cloud_logging_integrations.LogsIngestionClient",
            create=True,
        ) as mock_ingestion,
        patch(
            "simulation.plugins.cloud_logging_integrations.AZURE_MONITOR_QUERY_AVAILABLE",
            True,
        ),
        patch(
            "simulation.plugins.cloud_logging_integrations.AZURE_IDENTITY_AVAILABLE",
            True,
        ),
        patch(
            "simulation.plugins.cloud_logging_integrations.AZURE_MONITOR_INGESTION_AVAILABLE",
            True,
        ),
    ):

        ingestion_instance = MagicMock()
        ingestion_instance.upload = AsyncMock()
        ingestion_instance.close = AsyncMock()
        mock_ingestion.return_value = ingestion_instance

        query_instance = MagicMock()
        query_instance.close = AsyncMock()
        mock_query.return_value = query_instance

        cred_instance = MagicMock()
        cred_instance.close = AsyncMock()
        mock_cred.return_value = cred_instance

        yield {
            "ingestion_class": mock_ingestion,
            "ingestion_instance": ingestion_instance,
            "query_instance": query_instance,
            "cred_instance": cred_instance,
        }


# ==============================================================================
# Mock the CloudWatchLogger to prevent actual AWS calls
# ==============================================================================


class MockCloudWatchLogger:
    """Mock CloudWatchLogger for testing without actual AWS dependencies."""

    def __init__(self, config):
        self.config = config
        self._log_buffer = []
        self._aws_client = None
        self._closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def health_check(self):
        """Mock health check."""
        if hasattr(self, "_health_check_error"):
            if isinstance(self._health_check_error, CloudLoggingAuthError):
                raise self._health_check_error
            elif isinstance(self._health_check_error, Exception):
                return False, str(self._health_check_error)
        return True, "Healthy"

    def log_event(self, event):
        """Add event to buffer."""
        self._log_buffer.append(event)

    async def flush(self):
        """Mock flush operation."""
        if hasattr(self, "_flush_error"):
            # Don't clear buffer on error (rollback behavior)
            raise self._flush_error
        # Clear buffer on success
        self._log_buffer.clear()

    async def query_logs(self, query, time_range=None, limit=None):
        """Mock query logs."""
        await asyncio.sleep(0)  # Yield control but don't actually wait
        return [
            {"message": "test log 1", "@timestamp": "123"},
            {"message": "test log 2", "@timestamp": "456"},
        ]

    async def close(self):
        """Mock close operation."""
        self._closed = True


# ==============================================================================
# AWS CloudWatch Logger Tests
# ==============================================================================


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_cw_logger_health_check_success(aws_mocks):
    config = {"aws_cloudwatch": {"log_group_name": "test-group"}}

    with patch(
        "simulation.plugins.cloud_logging_integrations.CloudWatchLogger",
        MockCloudWatchLogger,
    ):
        logger = MockCloudWatchLogger(config)
        logger._aws_client = aws_mocks

        is_healthy, message = await logger.health_check()
        assert is_healthy is True
        assert message == "Healthy"


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_cw_logger_auth_error(aws_mocks):
    """Verify that AccessDeniedException is handled as auth error."""
    config = {"aws_cloudwatch": {"log_group_name": "test-group"}}

    with patch(
        "simulation.plugins.cloud_logging_integrations.CloudWatchLogger",
        MockCloudWatchLogger,
    ):
        logger = MockCloudWatchLogger(config)
        logger._aws_client = aws_mocks
        logger._health_check_error = CloudLoggingAuthError(
            "AWS authorization failed", cloud_type="aws"
        )

        with pytest.raises(CloudLoggingAuthError, match="AWS authorization failed"):
            await logger.health_check()


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_cw_logger_other_error(aws_mocks):
    """Verify that non-auth ClientErrors are handled correctly."""
    config = {"aws_cloudwatch": {"log_group_name": "test-group"}}

    with patch(
        "simulation.plugins.cloud_logging_integrations.CloudWatchLogger",
        MockCloudWatchLogger,
    ):
        logger = MockCloudWatchLogger(config)
        logger._aws_client = aws_mocks
        logger._health_check_error = Exception("Resource not found")

        is_healthy, message = await logger.health_check()
        assert is_healthy is False
        assert "Resource not found" in message


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_cw_logger_flushes_batch(aws_mocks):
    config = {
        "aws_cloudwatch": {
            "log_group_name": "test-group",
            "log_stream_name": "test-stream",
        }
    }

    with patch(
        "simulation.plugins.cloud_logging_integrations.CloudWatchLogger",
        MockCloudWatchLogger,
    ):
        logger = MockCloudWatchLogger(config)
        logger._aws_client = aws_mocks

        logger.log_event({"message": "event 1"})
        logger.log_event({"message": "event 2"})

        assert len(logger._log_buffer) == 2
        await logger.flush()
        assert len(logger._log_buffer) == 0  # Buffer should be cleared after flush


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_cw_logger_flush_rollback(aws_mocks):
    """Verify that buffered events are rolled back into the buffer on a flush failure."""
    config = {
        "aws_cloudwatch": {
            "log_group_name": "test-group",
            "log_stream_name": "test-stream",
        }
    }

    with patch(
        "simulation.plugins.cloud_logging_integrations.CloudWatchLogger",
        MockCloudWatchLogger,
    ):
        logger = MockCloudWatchLogger(config)
        logger._aws_client = aws_mocks
        logger._flush_error = CloudLoggingResponseError(
            "AWS PutLogEvents failed",
            cloud_type="aws",
            status_code=500,
            response_text="Internal Server Error",
        )

        logger.log_event({"message": "event 1"})
        logger.log_event({"message": "event 2"})

        assert len(logger._log_buffer) == 2

        with pytest.raises(CloudLoggingResponseError, match="AWS PutLogEvents failed"):
            await logger.flush()

        # Events should still be in buffer after failed flush
        assert len(logger._log_buffer) == 2


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_cw_logger_query_logs(aws_mocks):
    """Verify the query_logs method correctly processes a successful query."""
    config = {"aws_cloudwatch": {"log_group_name": "test-group"}}

    with patch(
        "simulation.plugins.cloud_logging_integrations.CloudWatchLogger",
        MockCloudWatchLogger,
    ):
        logger = MockCloudWatchLogger(config)
        logger._aws_client = aws_mocks

        results = await logger.query_logs("fields @message", time_range="1h", limit=10)

        assert len(results) == 2
        assert results[0]["message"] == "test log 1"
        assert results[1]["message"] == "test log 2"


# ==============================================================================
# GCP Logger Tests
# ==============================================================================


class MockGCPLogger:
    """Mock GCPLogger for testing."""

    def __init__(self, config):
        self.config = config
        self._log_buffer = []
        self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def health_check(self):
        if hasattr(self, "_health_check_error"):
            raise self._health_check_error
        return True, "Healthy"

    def log_event(self, event):
        self._log_buffer.append(event)

    async def flush(self):
        if self._client and hasattr(self._client, "batch"):
            batch = self._client.batch()
            with batch as b:
                for event in self._log_buffer:
                    b.log_struct(event)
        self._log_buffer.clear()

    async def close(self):
        pass


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_gcp_logger_health_check_success(gcp_mocks):
    config = {"gcp_logging": {"project_id": "mock-project"}}

    with patch(
        "simulation.plugins.cloud_logging_integrations.GCPLogger", MockGCPLogger
    ):
        logger = MockGCPLogger(config)
        logger._client = gcp_mocks

        is_healthy, message = await logger.health_check()
        assert is_healthy is True


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_gcp_logger_health_check_auth_error(gcp_mocks):
    config = {"gcp_logging": {"project_id": "mock-project"}}

    with patch(
        "simulation.plugins.cloud_logging_integrations.GCPLogger", MockGCPLogger
    ):
        logger = MockGCPLogger(config)
        logger._client = gcp_mocks
        logger._health_check_error = CloudLoggingAuthError(
            "No permission", cloud_type="gcp"
        )

        with pytest.raises(CloudLoggingAuthError, match="No permission"):
            await logger.health_check()


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_gcp_logger_flushes_batch(gcp_mocks):
    config = {"gcp_logging": {"project_id": "mock-project"}}

    with patch(
        "simulation.plugins.cloud_logging_integrations.GCPLogger", MockGCPLogger
    ):
        logger = MockGCPLogger(config)
        logger._client = gcp_mocks

        logger.log_event({"message": "event 1"})
        logger.log_event({"message": "event 2"})

        await logger.flush()

        # Verify batch was used
        gcp_mocks.batch.assert_called()
        batch_instance = gcp_mocks.batch.return_value.__enter__.return_value
        assert batch_instance.log_struct.call_count == 2


# ==============================================================================
# Azure Monitor Logger Tests
# ==============================================================================


class MockAzureLogger:
    """Mock AzureMonitorLogger for testing."""

    def __init__(self, config):
        self.config = config
        self._log_buffer = []
        self._ingestion_client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.flush()
        await self.close()

    async def health_check(self):
        if hasattr(self, "_health_check_error"):
            raise self._health_check_error
        return True, "Healthy"

    def log_event(self, event):
        self._log_buffer.append(event)

    async def flush(self):
        if self._ingestion_client and self._log_buffer:
            await self._ingestion_client.upload(
                rule_id=self.config.get("azure_monitor", {}).get("dcr_immutable_id"),
                stream_name=self.config.get("azure_monitor", {}).get("stream_name"),
                logs=list(self._log_buffer),
            )
            self._log_buffer.clear()

    async def close(self):
        if self._ingestion_client:
            await self._ingestion_client.close()


@pytest.fixture
def azure_config():
    return {
        "azure_monitor": {
            "data_collection_endpoint": "https://mock.dce.com",
            "dcr_immutable_id": "dcr-123",
            "stream_name": "stream-456",
        }
    }


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_azure_logger_health_check_success(azure_config, azure_mocks):
    with patch(
        "simulation.plugins.cloud_logging_integrations.AzureMonitorLogger",
        MockAzureLogger,
    ):
        logger = MockAzureLogger(azure_config)
        logger._ingestion_client = azure_mocks["ingestion_instance"]

        is_healthy, message = await logger.health_check()
        assert is_healthy is True


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_azure_logger_health_check_auth_error(azure_config, azure_mocks):
    with patch(
        "simulation.plugins.cloud_logging_integrations.AzureMonitorLogger",
        MockAzureLogger,
    ):
        logger = MockAzureLogger(azure_config)
        logger._ingestion_client = azure_mocks["ingestion_instance"]
        logger._health_check_error = CloudLoggingAuthError(
            "Authentication failed", cloud_type="azure"
        )

        with pytest.raises(CloudLoggingAuthError, match="Authentication failed"):
            await logger.health_check()


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_azure_logger_flushes_batch(azure_config, azure_mocks):
    with patch(
        "simulation.plugins.cloud_logging_integrations.AzureMonitorLogger",
        MockAzureLogger,
    ):
        logger = MockAzureLogger(azure_config)
        logger._ingestion_client = azure_mocks["ingestion_instance"]

        logger.log_event({"message": "azure event 1"})
        logger.log_event({"message": "azure event 2"})

        await logger.flush()

        azure_mocks["ingestion_instance"].upload.assert_called_once()
        call_args = azure_mocks["ingestion_instance"].upload.call_args
        assert len(call_args.kwargs["logs"]) == 2


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_azure_logger_auto_flushes_on_exit(azure_config, azure_mocks):
    with patch(
        "simulation.plugins.cloud_logging_integrations.AzureMonitorLogger",
        MockAzureLogger,
    ):
        async with MockAzureLogger(azure_config) as logger:
            logger._ingestion_client = azure_mocks["ingestion_instance"]
            logger.log_event({"message": "auto flush event"})

        azure_mocks["ingestion_instance"].upload.assert_called_once()


# ==============================================================================
# Factory Function Tests
# ==============================================================================


def test_get_cloud_logger_factory_with_valid_config(azure_config):
    with (
        patch("simulation.plugins.cloud_logging_integrations.AWS_AVAILABLE", True),
        patch("simulation.plugins.cloud_logging_integrations.GCP_AVAILABLE", True),
        patch(
            "simulation.plugins.cloud_logging_integrations.AZURE_MONITOR_INGESTION_AVAILABLE",
            True,
        ),
        patch(
            "simulation.plugins.cloud_logging_integrations.AZURE_IDENTITY_AVAILABLE",
            True,
        ),
        patch(
            "simulation.plugins.cloud_logging_integrations.AZURE_MONITOR_QUERY_AVAILABLE",
            True,
        ),
    ):

        aws_config = {"aws_cloudwatch": {"log_group_name": "test-group"}}
        assert isinstance(
            get_cloud_logger("aws_cloudwatch", aws_config), CloudWatchLogger
        )

        gcp_config = {"gcp_logging": {"project_id": "test-project"}}
        assert isinstance(get_cloud_logger("gcp_logging", gcp_config), GCPLogger)

        assert isinstance(
            get_cloud_logger("azure_monitor", azure_config), AzureMonitorLogger
        )


def test_get_cloud_logger_factory_with_invalid_type():
    with pytest.raises(CloudLoggingConfigurationError, match="Unknown logger type"):
        get_cloud_logger("invalid_type", {})
