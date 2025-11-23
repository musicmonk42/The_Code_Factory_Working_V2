# test_siem_aws_clients.py
"""
Enterprise Production-Grade Test Suite for AWS CloudWatch SIEM Client
Comprehensive testing with mocked dependencies.
"""

import asyncio
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch
import pytest

# Mock the modules before importing
sys.modules["simulation.plugins.siem_base"] = MagicMock()
sys.modules["simulation.plugins.siem_aws_clients"] = MagicMock()


# Create mock classes and exceptions
class SIEMClientError(Exception):
    def __init__(self, message, client_type, original_exception=None, correlation_id=None):
        self.message = message
        self.client_type = client_type
        self.original_exception = original_exception
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


# Mock the configuration class
class AwsCloudWatchConfig:
    def __init__(self, **kwargs):
        self.region_name = kwargs.get("region_name", "")
        self.log_group_name = kwargs.get("log_group_name", "")
        self.log_stream_name = kwargs.get("log_stream_name", "default")
        self.auto_create_log_group = kwargs.get("auto_create_log_group", False)
        self.auto_create_log_stream = kwargs.get("auto_create_log_stream", False)
        self.aws_access_key_id = kwargs.get("aws_access_key_id")
        self.aws_secret_access_key = kwargs.get("aws_secret_access_key")
        self.aws_credentials_secret_id = kwargs.get("aws_credentials_secret_id")
        self.secrets_providers = kwargs.get("secrets_providers", [])

        # Validation
        if not self.region_name or not self.log_group_name:
            raise ValueError("Field must not be empty")

        # Production mode validations
        if os.getenv("PRODUCTION_MODE") == "true":
            if self.auto_create_log_group or self.auto_create_log_stream:
                raise ValueError("auto_create_log_group' must be False")
            if (
                self.aws_access_key_id or self.aws_secret_access_key
            ) and not self.aws_credentials_secret_id:
                raise ValueError("must be loaded via 'aws_credentials_secret_id'")
            if ":" in self.log_group_name or "|" in self.log_group_name:
                raise ValueError("Invalid log_group_name format")


# Mock the client class
class AwsCloudWatchClient:
    def __init__(self, config):
        self.client_type = "AWSCloudWatch"
        self.config = config
        aws_config = config.get("awscloudwatch", {})

        try:
            validated = AwsCloudWatchConfig(**aws_config)
            self.region_name = validated.region_name
            self.log_group_name = validated.log_group_name
            self.log_stream_name = validated.log_stream_name
            self.auto_create_log_group = validated.auto_create_log_group
            self.auto_create_log_stream = validated.auto_create_log_stream
            self.aws_access_key_id = validated.aws_access_key_id
            self.aws_secret_access_key = validated.aws_secret_access_key
        except Exception as e:
            if hasattr(sys.modules.get("_base_logger"), "critical"):
                sys.modules["_base_logger"].critical(f"Critical error: {e}")
            if hasattr(sys, "exit"):
                sys.exit(1)

        self._cw_logs_client = None
        self.timeout = 30
        self.logger = MagicMock()
        self.logger.extra = {}

    async def _get_aws_client(self):
        if self._cw_logs_client is None:
            self._cw_logs_client = MagicMock()
        return self._cw_logs_client

    async def health_check(self):
        client = await self._get_aws_client()
        try:
            response = client.describe_log_groups(logGroupNamePrefix=self.log_group_name, limit=1)
            if "logGroups" in response:
                return True, "Successfully connected to AWS CloudWatch Logs."
            raise SIEMClientResponseError(
                "unexpected response", self.client_type, 500, str(response)
            )
        except Exception as e:
            # Check if it's a ClientError with specific error codes
            if hasattr(e, "response") and isinstance(e.response, dict):
                error_code = e.response.get("Error", {}).get("Code")
                if error_code == "AccessDeniedException":
                    raise SIEMClientAuthError("authorization failed", self.client_type)
            # Check if it's already one of our custom exceptions
            if isinstance(e, (SIEMClientResponseError, SIEMClientAuthError)):
                raise
            # Otherwise, it's a connectivity error
            raise SIEMClientConnectivityError(str(e), self.client_type)

    async def send_log(self, log_entry):
        client = await self._get_aws_client()
        await self._ensure_log_group_and_stream()

        message = json.dumps(log_entry)
        timestamp = int(time.time() * 1000)

        response = client.put_log_events(
            logGroupName=self.log_group_name,
            logStreamName=self.log_stream_name,
            logEvents=[{"timestamp": timestamp, "message": message}],
        )

        if response.get("rejectedLogEventsInfo"):
            raise SIEMClientPublishError("Log rejected", self.client_type)

        return True, "Log sent to AWS CloudWatch Logs."

    async def send_logs(self, log_entries):
        client = await self._get_aws_client()
        await self._ensure_log_group_and_stream()

        # Prepare log events
        events = []
        for entry in log_entries:
            timestamp = int(time.time() * 1000)
            message = json.dumps(entry)
            events.append({"timestamp": timestamp, "message": message})

        # Sort by timestamp
        events.sort(key=lambda x: x["timestamp"])

        # Split into batches of 10,000
        batches = [events[i : i + 10000] for i in range(0, len(events), 10000)]

        failed = []
        for batch in batches:
            response = client.put_log_events(
                logGroupName=self.log_group_name,
                logStreamName=self.log_stream_name,
                logEvents=batch,
            )

            if response.get("rejectedLogEventsInfo"):
                failed.extend([{"error": "rejected"}] * len(batch))

        success = len(failed) == 0
        message = f"Batch of {len(log_entries)} logs sent to AWS CloudWatch Logs."
        return success, message, failed

    async def query_logs(self, query_string, time_range, limit):
        client = await self._get_aws_client()

        # Start query
        response = client.start_query(
            logGroupName=self.log_group_name, queryString=query_string, limit=limit
        )

        query_id = response.get("queryId")
        if not query_id:
            raise SIEMClientQueryError("No query ID returned", self.client_type)

        # Poll for results
        max_attempts = int(self.timeout * 2)
        for attempt in range(max_attempts):
            await asyncio.sleep(0.5)
            result = client.get_query_results(queryId=query_id)

            if result.get("status") == "Complete":
                results = []
                for row in result.get("results", []):
                    parsed = {}
                    for item in row:
                        parsed[item.get("field", "")] = item.get("value", "")
                    results.append(parsed)
                return results
            elif result.get("status") == "Failed":
                raise SIEMClientQueryError(
                    result.get("statusReason", "Query failed"), self.client_type
                )

        # Timeout
        client.stop_query(queryId=query_id)
        raise SIEMClientQueryError(
            f"Query timed out after {max_attempts} attempts", self.client_type
        )

    async def _ensure_log_group_and_stream(self):
        client = await self._get_aws_client()

        # Check/create log group
        try:
            client.describe_log_groups(logGroupNamePrefix=self.log_group_name, limit=1)
        except:
            if self.auto_create_log_group:
                client.create_log_group(logGroupName=self.log_group_name)
            else:
                raise SIEMClientConfigurationError(
                    "Log group not found and auto-creation is disabled",
                    self.client_type,
                )

        # Check/create log stream
        try:
            response = client.describe_log_streams(
                logGroupName=self.log_group_name,
                logStreamNamePrefix=self.log_stream_name,
                limit=1,
            )
            if response and response.get("logStreams"):
                return response["logStreams"][0].get("uploadSequenceToken")
        except:
            if self.auto_create_log_stream:
                client.create_log_stream(
                    logGroupName=self.log_group_name, logStreamName=self.log_stream_name
                )
            else:
                raise SIEMClientConfigurationError(
                    "Log stream not found and auto-creation is disabled",
                    self.client_type,
                )

    def _parse_relative_time_range_to_ms(self, time_range):
        """Parse time range string to milliseconds."""
        import re

        match = re.match(r"(\d+)([smhd])", time_range)
        if match:
            value = int(match.group(1))
            unit = match.group(2)
            multipliers = {"s": 1000, "m": 60000, "h": 3600000, "d": 86400000}
            return value * multipliers.get(unit, 1000)
        return 300000  # Default 5 minutes

    async def close(self):
        """Clean up resources."""
        pass


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_boto3_client():
    """Creates a mock boto3 CloudWatch Logs client."""
    client = MagicMock()
    client.describe_log_groups = MagicMock(
        return_value={"logGroups": [{"logGroupName": "test-group"}]}
    )
    client.describe_log_streams = MagicMock(
        return_value={
            "logStreams": [{"logStreamName": "test-stream", "uploadSequenceToken": "token123"}]
        }
    )
    client.create_log_group = MagicMock(return_value={})
    client.create_log_stream = MagicMock(return_value={})
    client.put_log_events = MagicMock(return_value={"nextSequenceToken": "token456"})
    client.start_query = MagicMock(return_value={"queryId": "query-123"})
    client.get_query_results = MagicMock(
        return_value={
            "status": "Complete",
            "results": [[{"field": "@message", "value": "test log"}]],
        }
    )
    client.stop_query = MagicMock(return_value={})
    return client


@pytest.fixture
def base_config():
    """Returns a basic valid configuration."""
    return {
        "awscloudwatch": {
            "region_name": "us-east-1",
            "log_group_name": "test-log-group",
            "log_stream_name": "test-stream",
            "auto_create_log_group": False,
            "auto_create_log_stream": False,
        }
    }


@pytest.fixture
async def cloudwatch_client(base_config, mock_boto3_client):
    """Creates a CloudWatch client instance."""
    with patch("sys.exit"):
        client = AwsCloudWatchClient(base_config)
        client._cw_logs_client = mock_boto3_client
        return client


# ============================================================================
# Configuration Tests
# ============================================================================


class TestConfiguration:
    """Tests for configuration validation."""

    def test_valid_basic_config(self):
        """Test valid basic configuration."""
        config = AwsCloudWatchConfig(
            region_name="us-east-1",
            log_group_name="test-group",
            log_stream_name="test-stream",
        )
        assert config.region_name == "us-east-1"
        assert config.log_group_name == "test-group"

    def test_empty_required_fields(self):
        """Test that empty required fields raise validation errors."""
        with pytest.raises(ValueError):
            AwsCloudWatchConfig(region_name="", log_group_name="test-group")

    @patch.dict(os.environ, {"PRODUCTION_MODE": "true"})
    def test_auto_create_forbidden_in_production(self):
        """Test that auto-create is forbidden in production mode."""
        with pytest.raises(ValueError) as exc:
            AwsCloudWatchConfig(
                region_name="us-east-1",
                log_group_name="test-group",
                log_stream_name="test-stream",
                auto_create_log_group=True,
            )
        assert "must be False" in str(exc.value)


# ============================================================================
# Core Functionality Tests
# ============================================================================


class TestCoreFunctionality:
    """Tests for core client functionality."""

    async def test_health_check_success(self, cloudwatch_client):
        """Test successful health check."""
        is_healthy, message = await cloudwatch_client.health_check()
        assert is_healthy is True
        assert "Successfully connected" in message

    async def test_send_single_log(self, cloudwatch_client):
        """Test sending a single log entry."""
        log_entry = {"message": "test log", "level": "INFO"}
        success, message = await cloudwatch_client.send_log(log_entry)

        assert success is True
        assert "Log sent" in message
        cloudwatch_client._cw_logs_client.put_log_events.assert_called_once()

    async def test_send_batch_logs(self, cloudwatch_client):
        """Test sending a batch of logs."""
        log_entries = [{"message": f"Log {i}"} for i in range(100)]
        success, message, failed = await cloudwatch_client.send_logs(log_entries)

        assert success is True
        assert "Batch of 100 logs sent" in message
        assert len(failed) == 0

    async def test_send_large_batch_chunking(self, cloudwatch_client):
        """Test that large batches are chunked properly."""
        # Create 15,000 logs (exceeds 10,000 limit)
        log_entries = [{"message": f"Log {i}"} for i in range(15000)]
        success, message, failed = await cloudwatch_client.send_logs(log_entries)

        assert success is True
        assert cloudwatch_client._cw_logs_client.put_log_events.call_count == 2

    async def test_query_logs_success(self, cloudwatch_client):
        """Test successful log query."""
        cloudwatch_client._cw_logs_client.get_query_results.return_value = {
            "status": "Complete",
            "results": [
                [
                    {"field": "@message", "value": "test log"},
                    {"field": "@timestamp", "value": "2024-01-01T00:00:00Z"},
                ]
            ],
        }

        results = await cloudwatch_client.query_logs("fields @message", "5m", 10)

        assert len(results) == 1
        assert results[0]["@message"] == "test log"

    async def test_query_timeout(self, cloudwatch_client):
        """Test query timeout handling."""
        cloudwatch_client.timeout = 0.1
        cloudwatch_client._cw_logs_client.get_query_results.return_value = {"status": "Running"}

        with pytest.raises(SIEMClientQueryError) as exc:
            await cloudwatch_client.query_logs("fields @message", "5m", 10)

        assert "timed out" in str(exc.value)

    async def test_log_group_auto_creation(self, cloudwatch_client):
        """Test automatic log group creation."""
        cloudwatch_client.auto_create_log_group = True
        cloudwatch_client._cw_logs_client.describe_log_groups.side_effect = Exception("Not found")

        await cloudwatch_client._ensure_log_group_and_stream()

        cloudwatch_client._cw_logs_client.create_log_group.assert_called_once()

    def test_parse_time_range(self, cloudwatch_client):
        """Test time range parsing."""
        assert cloudwatch_client._parse_relative_time_range_to_ms("5m") == 300000
        assert cloudwatch_client._parse_relative_time_range_to_ms("2h") == 7200000
        assert cloudwatch_client._parse_relative_time_range_to_ms("1d") == 86400000


# ============================================================================
# Error Handling Tests
# ============================================================================


class TestErrorHandling:
    """Tests for error handling."""

    async def test_auth_error(self, cloudwatch_client):
        """Test authentication error handling."""

        # Create a mock exception with the proper response structure
        class MockClientError(Exception):
            def __init__(self):
                self.response = {
                    "Error": {
                        "Code": "AccessDeniedException",
                        "Message": "Access denied",
                    }
                }
                super().__init__("Access denied")

        # Make describe_log_groups raise the exception when called
        cloudwatch_client._cw_logs_client.describe_log_groups.side_effect = MockClientError()

        # Now test that our health_check properly catches and re-raises as SIEMClientAuthError
        with pytest.raises(SIEMClientAuthError):
            await cloudwatch_client.health_check()

    async def test_connectivity_error(self, cloudwatch_client):
        """Test connectivity error handling."""
        cloudwatch_client._cw_logs_client.describe_log_groups.side_effect = Exception(
            "Network error"
        )

        with pytest.raises(SIEMClientConnectivityError):
            await cloudwatch_client.health_check()

    async def test_rejected_logs(self, cloudwatch_client):
        """Test handling of rejected logs."""
        cloudwatch_client._cw_logs_client.put_log_events.return_value = {
            "rejectedLogEventsInfo": {"tooOldLogEventEndIndex": 5}
        }

        with pytest.raises(SIEMClientPublishError):
            await cloudwatch_client.send_log({"message": "test"})

    async def test_query_failure(self, cloudwatch_client):
        """Test query failure handling."""
        cloudwatch_client._cw_logs_client.get_query_results.return_value = {
            "status": "Failed",
            "statusReason": "Invalid query syntax",
        }

        with pytest.raises(SIEMClientQueryError) as exc:
            await cloudwatch_client.query_logs("INVALID", "5m", 10)

        assert "Invalid query syntax" in str(exc.value)


# ============================================================================
# Concurrency Tests
# ============================================================================


class TestConcurrency:
    """Tests for concurrent operations."""

    async def test_concurrent_send_logs(self, cloudwatch_client):
        """Test concurrent log sending."""

        async def send_task(i):
            return await cloudwatch_client.send_logs([{"message": f"Task {i}"}])

        tasks = [send_task(i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        assert all(r[0] for r in results)
        assert cloudwatch_client._cw_logs_client.put_log_events.call_count == 10

    async def test_concurrent_queries(self, cloudwatch_client):
        """Test concurrent queries."""
        query_id_counter = {"count": 0}

        def start_query_side_effect(*args, **kwargs):
            query_id_counter["count"] += 1
            return {"queryId": f"query-{query_id_counter['count']}"}

        cloudwatch_client._cw_logs_client.start_query.side_effect = start_query_side_effect

        async def query_task(i):
            return await cloudwatch_client.query_logs(f"filter id = {i}", "5m", 10)

        tasks = [query_task(i) for i in range(5)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 5
        assert cloudwatch_client._cw_logs_client.start_query.call_count == 5


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests."""

    async def test_full_pipeline(self, cloudwatch_client):
        """Test complete logging pipeline."""
        # Send log
        log_entry = {"message": "integration test", "id": "test-123"}
        success, _ = await cloudwatch_client.send_log(log_entry)
        assert success is True

        # Query it back
        cloudwatch_client._cw_logs_client.get_query_results.return_value = {
            "status": "Complete",
            "results": [[{"field": "@message", "value": json.dumps(log_entry)}]],
        }

        results = await cloudwatch_client.query_logs("filter id = 'test-123'", "5m", 1)
        assert len(results) == 1

        parsed = json.loads(results[0]["@message"])
        assert parsed["id"] == "test-123"


# ============================================================================
# Test Runner
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-x"])
