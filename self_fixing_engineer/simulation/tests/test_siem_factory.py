# test_siem_factory.py
"""
Test suite for SIEM factory module.
Tests client instantiation, registry management, and availability checking.
"""

import asyncio
import sys
from typing import Any, Dict, Type
from unittest.mock import MagicMock, patch

import pytest

# Mock modules before importing
sys.modules["simulation.plugins.siem_base"] = MagicMock()
sys.modules["simulation.plugins.siem_factory"] = MagicMock()
sys.modules["simulation.plugins.siem_generic_clients"] = MagicMock()
sys.modules["simulation.plugins.siem_aws_clients"] = MagicMock()
sys.modules["simulation.plugins.siem_azure_clients"] = MagicMock()
sys.modules["simulation.plugins.siem_gcp_clients"] = MagicMock()


# Mock exception classes
class SIEMClientError(Exception):
    def __init__(self, message, client_type, original_exception=None):
        self.message = message
        self.client_type = client_type
        self.original_exception = original_exception
        super().__init__(message)


class SIEMClientConfigurationError(SIEMClientError):
    pass


# Mock base client
class BaseSIEMClient:
    def __init__(self, config, metrics_hook=None, paranoid_mode=False):
        self.config = config
        self.metrics_hook = metrics_hook
        self.paranoid_mode = paranoid_mode
        # Don't override client_type if it's already set by subclass
        if not hasattr(self, "client_type"):
            self.client_type = self.__class__.__name__

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


# Mock client implementations
class SplunkClient(BaseSIEMClient):
    """Splunk SIEM client for log ingestion and querying."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_type = "splunk"


class ElasticClient(BaseSIEMClient):
    """Elasticsearch client for log analytics."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_type = "elastic"


class DatadogClient(BaseSIEMClient):
    """Datadog client for monitoring and logging."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_type = "datadog"


class AwsCloudWatchClient(BaseSIEMClient):
    """AWS CloudWatch client for cloud logging."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_type = "aws_cloudwatch"


class GcpLoggingClient(BaseSIEMClient):
    """GCP Cloud Logging client."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_type = "gcp_logging"


class AzureSentinelClient(BaseSIEMClient):
    """Azure Sentinel client for security analytics."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_type = "azure_sentinel"


class AzureEventGridClient(BaseSIEMClient):
    """Azure Event Grid client for event routing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_type = "azure_event_grid"


class AzureServiceBusClient(BaseSIEMClient):
    """Azure Service Bus client for messaging."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_type = "azure_service_bus"


# Mock global variables
PRODUCTION_MODE = False
_base_logger = MagicMock()


def alert_operator(message: str, level: str = "CRITICAL"):
    """Mock alert operator."""
    pass


class SecretsManager:
    async def get_secret(self, key, default=None):
        return "dummy_secret"


SECRETS_MANAGER = SecretsManager()

# Client registry
SIEM_CLIENT_REGISTRY: Dict[str, Type[BaseSIEMClient]] = {
    "splunk": SplunkClient,
    "elastic": ElasticClient,
    "datadog": DatadogClient,
    "aws_cloudwatch": AwsCloudWatchClient,
    "gcp_logging": GcpLoggingClient,
    "azure_sentinel": AzureSentinelClient,
    "azure_event_grid": AzureEventGridClient,
    "azure_service_bus": AzureServiceBusClient,
}


# Factory functions
def get_siem_client(siem_type: str, config: Dict[str, Any], metrics_hook=None) -> BaseSIEMClient:
    """
    Factory function to get an initialized SIEM client.
    """
    global PRODUCTION_MODE

    # Enforce paranoid mode in production
    paranoid_mode = config.get("paranoid_mode", False)
    if PRODUCTION_MODE and not paranoid_mode:
        msg = "'paranoid_mode' must be enabled in PRODUCTION_MODE for SIEM clients."
        alert_operator(f"CRITICAL: {msg}", level="CRITICAL")
        raise SIEMClientConfigurationError(msg, "SIEM_Factory")

    # Metrics hook is required
    if metrics_hook is None:
        msg = "A metrics_hook must be provided to the SIEM client factory."
        alert_operator(f"CRITICAL: {msg}", level="CRITICAL")
        raise SIEMClientConfigurationError(msg, "SIEM_Factory")

    client_class = SIEM_CLIENT_REGISTRY.get(siem_type)
    if not client_class:
        msg = f"Unknown or unavailable SIEM client type: {siem_type}. Available: {list(SIEM_CLIENT_REGISTRY.keys())}"
        alert_operator(f"CRITICAL: {msg}", level="CRITICAL")
        raise SIEMClientConfigurationError(msg, "SIEM_Factory")

    try:
        client_instance = client_class(
            config, metrics_hook=metrics_hook, paranoid_mode=paranoid_mode
        )
        return client_instance
    except SIEMClientConfigurationError:
        raise
    except Exception as e:
        msg = f"Failed to initialize '{siem_type}' client due to unexpected error: {e}"
        alert_operator(f"CRITICAL: {msg}", level="CRITICAL")
        raise SIEMClientError(msg, "SIEM_Factory", original_exception=e)


def list_available_siem_clients():
    """
    Lists available SIEM client types and their dependency status.
    """
    available_clients_info = []

    for siem_type, client_class in SIEM_CLIENT_REGISTRY.items():
        try:
            description = (client_class.__doc__ or "No description.").strip().split("\n")[0]
        except Exception:
            description = "No description."

        available_clients_info.append(
            {
                "type": siem_type,
                "class_name": client_class.__name__,
                "is_available": True,  # All mocked clients are available
                "required_dependencies_status": [],
                "description": description,
            }
        )

    return available_clients_info


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global state before each test."""
    global PRODUCTION_MODE
    PRODUCTION_MODE = False
    yield
    PRODUCTION_MODE = False


@pytest.fixture
def mock_alert_operator():
    """Mock the alert_operator function."""
    mock = MagicMock()
    # Use globals to replace the function in the current module
    global alert_operator
    original = alert_operator
    alert_operator = mock
    yield mock
    alert_operator = original


@pytest.fixture
def mock_metrics_hook():
    """Create a mock metrics hook."""
    return MagicMock()


@pytest.fixture
def valid_config():
    """Provide a valid configuration for testing."""
    return {
        "splunk": {"url": "https://splunk.example.com", "token": "test-token"},
        "paranoid_mode": False,
    }


# ============================================================================
# Test Cases
# ============================================================================


class TestGetSiemClient:
    """Tests for get_siem_client function."""

    def test_successful_instantiation(self, valid_config, mock_metrics_hook):
        """Test successful client instantiation with valid configuration."""
        siem_type = "splunk"

        client = get_siem_client(siem_type, valid_config, mock_metrics_hook)

        assert isinstance(client, SplunkClient)
        assert client.config == valid_config
        assert client.metrics_hook == mock_metrics_hook
        assert client.paranoid_mode is False

    def test_unknown_client_type(self, valid_config, mock_metrics_hook, mock_alert_operator):
        """Test that unknown SIEM client type raises error."""
        siem_type = "unknown_client"

        with pytest.raises(SIEMClientConfigurationError) as exc_info:
            get_siem_client(siem_type, valid_config, mock_metrics_hook)

        assert "Unknown or unavailable SIEM client type" in str(exc_info.value)
        mock_alert_operator.assert_called_once()

    def test_missing_metrics_hook(self, valid_config, mock_alert_operator):
        """Test that missing metrics hook raises error."""
        siem_type = "splunk"

        with pytest.raises(SIEMClientConfigurationError) as exc_info:
            get_siem_client(siem_type, valid_config, None)

        assert "metrics_hook must be provided" in str(exc_info.value)
        mock_alert_operator.assert_called_once()

    def test_production_mode_requires_paranoid(
        self, valid_config, mock_metrics_hook, mock_alert_operator
    ):
        """Test that production mode requires paranoid_mode."""
        global PRODUCTION_MODE
        PRODUCTION_MODE = True

        siem_type = "splunk"
        config = {**valid_config, "paranoid_mode": False}

        with pytest.raises(SIEMClientConfigurationError) as exc_info:
            get_siem_client(siem_type, config, mock_metrics_hook)

        assert "paranoid_mode' must be enabled in PRODUCTION_MODE" in str(exc_info.value)
        mock_alert_operator.assert_called_once()

    def test_production_mode_with_paranoid(self, valid_config, mock_metrics_hook):
        """Test successful instantiation in production mode with paranoid_mode."""
        global PRODUCTION_MODE
        PRODUCTION_MODE = True

        siem_type = "splunk"
        config = {**valid_config, "paranoid_mode": True}

        client = get_siem_client(siem_type, config, mock_metrics_hook)

        assert isinstance(client, SplunkClient)
        assert client.paranoid_mode is True

    def test_client_init_error_handling(self, valid_config, mock_metrics_hook, mock_alert_operator):
        """Test handling of client initialization errors."""
        siem_type = "splunk"

        # Mock SplunkClient to raise an error
        with patch.object(SplunkClient, "__init__", side_effect=Exception("Init failed")):
            with pytest.raises(SIEMClientError) as exc_info:
                get_siem_client(siem_type, valid_config, mock_metrics_hook)

            assert "Failed to initialize" in str(exc_info.value)
            assert "Init failed" in str(exc_info.value)
            mock_alert_operator.assert_called()

    def test_all_client_types(self, valid_config, mock_metrics_hook):
        """Test instantiation of all registered client types."""
        for siem_type in SIEM_CLIENT_REGISTRY.keys():
            # Create appropriate config for each type
            config = {siem_type: {"test": "config"}, "paranoid_mode": False}

            client = get_siem_client(siem_type, config, mock_metrics_hook)

            assert client is not None
            assert client.config == config


class TestListAvailableClients:
    """Tests for list_available_siem_clients function."""

    def test_lists_all_clients(self):
        """Test that all registered clients are listed."""
        clients_info = list_available_siem_clients()

        assert len(clients_info) == len(SIEM_CLIENT_REGISTRY)

        # Check all client types are present
        listed_types = {info["type"] for info in clients_info}
        expected_types = set(SIEM_CLIENT_REGISTRY.keys())
        assert listed_types == expected_types

    def test_client_info_structure(self):
        """Test the structure of returned client information."""
        clients_info = list_available_siem_clients()

        for info in clients_info:
            assert "type" in info
            assert "class_name" in info
            assert "is_available" in info
            assert "required_dependencies_status" in info
            assert "description" in info

            # All mocked clients should be available
            assert info["is_available"] is True
            assert info["required_dependencies_status"] == []

    def test_client_descriptions(self):
        """Test that client descriptions are properly extracted."""
        clients_info = list_available_siem_clients()

        # Check a specific client's description
        splunk_info = next(info for info in clients_info if info["type"] == "splunk")
        assert "Splunk SIEM client" in splunk_info["description"]

        aws_info = next(info for info in clients_info if info["type"] == "aws_cloudwatch")
        assert "AWS CloudWatch" in aws_info["description"]


class TestConcurrentOperations:
    """Tests for concurrent client operations."""

    @pytest.mark.asyncio
    async def test_concurrent_same_type(self, valid_config, mock_metrics_hook):
        """Test concurrent instantiation of same client type."""
        siem_type = "aws_cloudwatch"
        config = {
            "aws_cloudwatch": {
                "region_name": "us-east-1",
                "log_group_name": "test-group",
                "log_stream_name": "test-stream",
            },
            "paranoid_mode": False,
        }

        # Create multiple clients concurrently
        tasks = [
            asyncio.to_thread(get_siem_client, siem_type, config, mock_metrics_hook)
            for _ in range(100)
        ]
        clients = await asyncio.gather(*tasks)

        assert len(clients) == 100
        assert all(isinstance(c, AwsCloudWatchClient) for c in clients)
        assert all(c.config == config for c in clients)

    @pytest.mark.asyncio
    async def test_concurrent_mixed_types(self, mock_metrics_hook):
        """Test concurrent instantiation of different client types."""
        configs = {
            "aws_cloudwatch": {
                "aws_cloudwatch": {"region_name": "us-east-1"},
                "paranoid_mode": False,
            },
            "azure_sentinel": {
                "azure_sentinel": {"workspace_id": "test-id"},
                "paranoid_mode": False,
            },
            "splunk": {
                "splunk": {"url": "https://splunk.example.com"},
                "paranoid_mode": False,
            },
        }

        tasks = []
        for client_type, config in configs.items():
            tasks.extend(
                [
                    asyncio.to_thread(get_siem_client, client_type, config, mock_metrics_hook)
                    for _ in range(30)
                ]
            )

        clients = await asyncio.gather(*tasks)

        assert len(clients) == 90

        # Count clients by type
        aws_count = sum(1 for c in clients if isinstance(c, AwsCloudWatchClient))
        azure_count = sum(1 for c in clients if isinstance(c, AzureSentinelClient))
        splunk_count = sum(1 for c in clients if isinstance(c, SplunkClient))

        assert aws_count == 30
        assert azure_count == 30
        assert splunk_count == 30


class TestErrorScenarios:
    """Tests for error handling scenarios."""

    def test_registry_manipulation(self, valid_config, mock_metrics_hook):
        """Test behavior when registry is manipulated."""
        # Save original registry
        original_registry = SIEM_CLIENT_REGISTRY.copy()

        try:
            # Remove a client from registry
            del SIEM_CLIENT_REGISTRY["splunk"]

            with pytest.raises(SIEMClientConfigurationError) as exc_info:
                get_siem_client("splunk", valid_config, mock_metrics_hook)

            assert "Unknown or unavailable" in str(exc_info.value)

        finally:
            # Restore registry
            SIEM_CLIENT_REGISTRY.update(original_registry)

    def test_client_with_config_validation_error(self, mock_metrics_hook):
        """Test client with configuration validation error."""

        # Mock a client that validates config strictly
        class StrictClient(BaseSIEMClient):
            def __init__(self, config, **kwargs):
                if "required_field" not in config:
                    raise SIEMClientConfigurationError("Missing required_field", "StrictClient")
                super().__init__(config, **kwargs)

        SIEM_CLIENT_REGISTRY["strict"] = StrictClient

        try:
            with pytest.raises(SIEMClientConfigurationError) as exc_info:
                get_siem_client("strict", {}, mock_metrics_hook)

            assert "Missing required_field" in str(exc_info.value)

        finally:
            del SIEM_CLIENT_REGISTRY["strict"]


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests for the factory module."""

    def test_full_lifecycle(self, mock_metrics_hook):
        """Test full lifecycle: list, create, use clients."""
        # List available clients
        available = list_available_siem_clients()
        assert len(available) > 0

        # Create a client for each available type
        clients = []
        for client_info in available:
            if client_info["is_available"]:
                config = {
                    client_info["type"]: {"test": "config"},
                    "paranoid_mode": False,
                }
                client = get_siem_client(client_info["type"], config, mock_metrics_hook)
                clients.append(client)

        assert len(clients) == len(available)

        # Verify all clients have correct type
        for client, info in zip(clients, available):
            assert client.client_type == info["type"]

    @pytest.mark.asyncio
    async def test_async_context_manager(self, valid_config, mock_metrics_hook):
        """Test using clients as async context managers."""
        client = get_siem_client("splunk", valid_config, mock_metrics_hook)

        async with client as c:
            assert c is client
            assert c.config == valid_config


# ============================================================================
# Test Runner
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-x", "--asyncio-mode=auto"])
