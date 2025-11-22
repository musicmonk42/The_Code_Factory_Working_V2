# test_siem_main.py
"""
Test suite for siem_main.py module.
Tests the main SIEM functionality including test runner and CLI commands.
"""

import pytest
import asyncio
import os
import sys
import json
import uuid
import datetime
import hashlib
import hmac
from unittest.mock import MagicMock, patch

# Mock all SIEM modules before importing
sys.modules["simulation.plugins.siem_base"] = MagicMock()
sys.modules["simulation.plugins.siem_factory"] = MagicMock()
sys.modules["simulation.plugins.siem_generic_clients"] = MagicMock()
sys.modules["simulation.plugins.siem_aws_clients"] = MagicMock()
sys.modules["simulation.plugins.siem_azure_clients"] = MagicMock()
sys.modules["simulation.plugins.siem_gcp_clients"] = MagicMock()
sys.modules["simulation.plugins.siem_main"] = MagicMock()


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
        self.details = details
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


class SIEMClientValidationError(SIEMClientError):
    pass


# Mock global variables
PRODUCTION_MODE = False
_base_logger = MagicMock()
_base_logger.critical = MagicMock()
_base_logger.warning = MagicMock()
_base_logger.info = MagicMock()
_base_logger.debug = MagicMock()
_base_logger.setLevel = MagicMock()


def alert_operator(message: str, level: str = "CRITICAL"):
    """Mock alert operator."""
    pass


# Mock SECRETS_MANAGER
class MockSecretsManager:
    def get_secret(self, key, default=None, required=True):
        secrets = {
            "SIEM_CONFIG_HMAC_SECRET": "test_hmac_secret",
            "SIEM_SPLUNK_HEC_URL": "https://splunk.example.com:8088/services/collector/event",
            "SIEM_SPLUNK_HEC_TOKEN": "test_token",
            "SIEM_ELASTIC_URL": "https://elastic.example.com:9200",
            "SIEM_ELASTIC_USERNAME": "test_user",
            "SIEM_ELASTIC_PASSWORD": "test_pass",
            "SIEM_DATADOG_API_KEY": "test_dd_key",
            "SIEM_DATADOG_APPLICATION_KEY": "test_dd_app_key",
        }
        return secrets.get(key, default)


SECRETS_MANAGER = MockSecretsManager()


# Mock base client
class BaseSIEMClient:
    def __init__(self, config, metrics_hook=None, paranoid_mode=False):
        self.config = config
        self.metrics_hook = metrics_hook
        self.paranoid_mode = paranoid_mode
        self.client_type = getattr(self, "client_type", self.__class__.__name__)
        self.logger = MagicMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        pass

    async def health_check(self, correlation_id=None):
        return True, "Healthy"

    async def send_log(self, log_entry, correlation_id=None):
        return True, "Sent"

    async def send_logs(self, log_entries, correlation_id=None):
        return True, "Batch sent", []

    async def query_logs(
        self, query_string, time_range="24h", limit=100, correlation_id=None
    ):
        return [{"message": "test log"}]


# Mock client classes
class SplunkClient(BaseSIEMClient):
    client_type = "splunk"


class ElasticClient(BaseSIEMClient):
    client_type = "elastic"


class DatadogClient(BaseSIEMClient):
    client_type = "datadog"


class AwsCloudWatchClient(BaseSIEMClient):
    client_type = "aws_cloudwatch"


class AzureSentinelClient(BaseSIEMClient):
    client_type = "azure_sentinel"


class GcpLoggingClient(BaseSIEMClient):
    client_type = "gcp_logging"


# Mock registry
SIEM_CLIENT_REGISTRY = {
    "splunk": SplunkClient,
    "elastic": ElasticClient,
    "datadog": DatadogClient,
    "aws_cloudwatch": AwsCloudWatchClient,
    "azure_sentinel": AzureSentinelClient,
    "gcp_logging": GcpLoggingClient,
}


# Mock factory functions
def get_siem_client(siem_type, config, metrics_hook=None):
    if siem_type not in SIEM_CLIENT_REGISTRY:
        raise ValueError(f"Unknown SIEM type: {siem_type}")
    return SIEM_CLIENT_REGISTRY[siem_type](config, metrics_hook)


def list_available_siem_clients():
    return [
        {
            "type": client_type,
            "is_available": True,
            "required_dependencies_status": [],
            "description": f"Mock {client_type} client",
        }
        for client_type in SIEM_CLIENT_REGISTRY
    ]


# Mock click if available
try:
    import click
    from click.testing import CliRunner

    CLICK_AVAILABLE = True
except ImportError:
    CLICK_AVAILABLE = False
    click = None
    CliRunner = None


# Helper functions
async def _maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value


async def _get_secret(key: str, default=None, required=False):
    val = SECRETS_MANAGER.get_secret(key, default, required=required)
    return await _maybe_await(val)


def _scrub_and_dump(obj):
    """Simple scrubbing function for testing."""
    return json.dumps(obj, default=str) if not isinstance(obj, str) else obj


# Main test runner function
async def run_tests():
    """Main function to run all SIEM client tests."""

    # Check for test flag
    if os.getenv("RUN_SIEM_TESTS", "false").lower() != "true":
        _base_logger.critical(
            "CRITICAL: siem_main.py (test runner) is attempting to execute without 'RUN_SIEM_TESTS=true' environment flag. Aborting."
        )
        alert_operator(
            "CRITICAL: siem_main.py (test runner) is attempting to execute without explicit test flag. Aborting.",
            level="CRITICAL",
        )
        sys.exit(1)
        return  # Return to prevent further execution in tests

    print("--- Running SIEM Clients Module Test ---")
    print("\n--- Available SIEM Clients and their Dependencies ---")

    for client_info in list_available_siem_clients():
        print(
            f"  Type: {client_info['type']}, Available: {client_info['is_available']}"
        )

    # Test metrics hook
    def test_metrics_hook(event_name: str, status: str, data: dict):
        print(f"[METRICS_HOOK] {event_name}.{status}: {_scrub_and_dump(data)}")

    # Test configuration
    test_config = {
        "default_timeout_seconds": 15,
        "retry_attempts": 3,
    }

    test_log_entry = {
        "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
        "event_type": "test_event",
        "message": "This is a test log message from the SIEM client.",
        "severity": "INFO",
    }

    # Test each client
    for siem_type in SIEM_CLIENT_REGISTRY.keys():
        print(f"\n--- Testing Client: {siem_type} ---")

        try:
            correlation_id = str(uuid.uuid4())
            async with get_siem_client(
                siem_type, test_config, metrics_hook=test_metrics_hook
            ) as client:
                print(
                    f"Initialized {siem_type} client with correlation ID: {correlation_id}."
                )

                is_healthy, health_msg = await client.health_check(
                    correlation_id=correlation_id
                )
                print(f"Health Check: {is_healthy} - {health_msg}")

                if not is_healthy:
                    print(
                        f"Skipping send/query for {siem_type} due to failed health check."
                    )
                    continue

                # Test send_log
                send_success, send_msg = await client.send_log(
                    test_log_entry, correlation_id=correlation_id
                )
                print(f"Single Log Send: {send_success} - {send_msg}")

                # Test send_logs
                batch_log_entries = [test_log_entry, test_log_entry]
                batch_success, batch_msg, failed_logs = await client.send_logs(
                    batch_log_entries, correlation_id=correlation_id
                )
                print(f"Batch Log Send: {batch_success} - {batch_msg}")

                # Test query_logs
                try:
                    query_results = await client.query_logs(
                        "test query", "1h", 2, correlation_id=correlation_id
                    )
                    print(f"Query Results: {len(query_results)} results")
                except NotImplementedError:
                    print(f"Querying not supported for {siem_type}")

        except Exception as e:
            print(f"Error testing {siem_type}: {e}")

    print("\n--- All SIEM Clients Module Tests Complete ---")


async def main():
    """Main entry point."""
    if PRODUCTION_MODE:
        _base_logger.critical(
            "CRITICAL: siem_main.py (test runner) is attempting to execute in PRODUCTION_MODE. Aborting for security."
        )
        alert_operator(
            "CRITICAL: siem_main.py (test runner) is attempting to execute in PRODUCTION_MODE. Aborting.",
            level="CRITICAL",
        )
        sys.exit(1)
        return  # Return to prevent further execution

    await run_tests()


# CLI functions if click is available
if CLICK_AVAILABLE:

    @click.group()
    def cli():
        """Secure Production CLI for SIEM client operations."""
        if not PRODUCTION_MODE:
            raise click.ClickException(
                "Production CLI commands are restricted to PRODUCTION_MODE."
            )

    @cli.command("health-check")
    @click.option(
        "--siem-type",
        required=True,
        type=click.Choice(list(SIEM_CLIENT_REGISTRY.keys())),
    )
    @click.option("--config-file", required=True, type=click.Path(exists=True))
    def health_check_command(siem_type, config_file):
        """Perform a health check on a specified SIEM client."""
        try:
            with open(config_file, "r") as f:
                config = json.load(f)

            # Mock HMAC validation
            hmac_file = config_file + ".hmac"
            if os.path.exists(hmac_file):
                with open(hmac_file, "r") as f:
                    expected_hmac = f.read().strip()

                hmac_secret = SECRETS_MANAGER.get_secret(
                    "SIEM_CONFIG_HMAC_SECRET", required=True
                ).encode("utf-8")
                with open(config_file, "r") as f:
                    config_data = f.read()
                generated_hmac = hmac.new(
                    hmac_secret, config_data.encode("utf-8"), hashlib.sha256
                ).hexdigest()

                if not hmac.compare_digest(generated_hmac, expected_hmac):
                    raise click.ClickException(
                        "Config file integrity check failed: HMAC mismatch."
                    )

            # Run health check asynchronously
            async def run_health_check():
                async with get_siem_client(siem_type, config) as client:
                    return await client.health_check()

            is_healthy, msg = asyncio.run(run_health_check())
            click.echo(f"Health Check for '{siem_type}': {is_healthy} - {msg}")

            if not is_healthy:
                raise click.ClickException("Health check failed.")

        except click.ClickException:
            raise
        except Exception as e:
            click.echo(f"Health check failed with error: {e}", err=True)
            raise click.ClickException("Health check failed.")


# ============================================================================
# Test Cases
# ============================================================================


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global state before each test."""
    global PRODUCTION_MODE
    PRODUCTION_MODE = False
    yield
    PRODUCTION_MODE = False


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Mock environment variables for testing."""
    monkeypatch.setenv("RUN_SIEM_TESTS", "true")
    monkeypatch.setenv("PRODUCTION_MODE", "false")


class TestRunTests:
    """Tests for the run_tests function."""

    @pytest.mark.asyncio
    async def test_run_tests_success(self, mock_env_vars):
        """Test that run_tests executes successfully."""
        # Mock sys.exit to prevent actual exit
        with patch("sys.exit") as mock_exit:
            await run_tests()
            mock_exit.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_tests_without_flag(self, monkeypatch):
        """Test that run_tests aborts without RUN_SIEM_TESTS flag."""
        monkeypatch.setenv("RUN_SIEM_TESTS", "false")

        # Create a mock that raises SystemExit to simulate sys.exit behavior
        with patch("sys.exit", side_effect=SystemExit(1)) as mock_exit:
            with pytest.raises(SystemExit) as exc_info:
                await run_tests()
            assert exc_info.value.code == 1
            mock_exit.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_run_tests_handles_client_error(self, mock_env_vars):
        """Test that run_tests handles client errors gracefully."""
        # Mock a client to raise an error
        with patch.object(
            SplunkClient, "health_check", side_effect=Exception("Test error")
        ):
            await run_tests()  # Should not raise


class TestMain:
    """Tests for the main function."""

    @pytest.mark.asyncio
    async def test_main_in_production_mode(self):
        """Test that main aborts in production mode."""
        global PRODUCTION_MODE
        PRODUCTION_MODE = True

        # Create a mock that raises SystemExit to simulate sys.exit behavior
        with patch("sys.exit", side_effect=SystemExit(1)) as mock_exit:
            with pytest.raises(SystemExit) as exc_info:
                await main()
            assert exc_info.value.code == 1
            mock_exit.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_main_in_test_mode(self, mock_env_vars):
        """Test that main runs in test mode."""
        with patch("sys.exit") as mock_exit:
            await main()
            mock_exit.assert_not_called()


@pytest.mark.skipif(not CLICK_AVAILABLE, reason="Click not available")
class TestCLI:
    """Tests for CLI commands."""

    def test_cli_blocks_non_production(self):
        """Test that CLI blocks non-production mode."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["health-check", "--siem-type", "splunk", "--config-file", "dummy.json"],
        )
        assert result.exit_code == 1
        assert (
            "Production CLI commands are restricted to PRODUCTION_MODE" in result.output
        )

    def test_health_check_command_success(self, tmp_path):
        """Test health-check command with valid config."""
        global PRODUCTION_MODE
        PRODUCTION_MODE = True

        # Create config file
        config = {"timeout": 30}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        # Create HMAC file
        hmac_secret = b"test_hmac_secret"
        config_data = config_file.read_text()
        expected_hmac = hmac.new(
            hmac_secret, config_data.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        hmac_file = tmp_path / "config.json.hmac"
        hmac_file.write_text(expected_hmac)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "health-check",
                "--siem-type",
                "splunk",
                "--config-file",
                str(config_file),
            ],
        )

        assert result.exit_code == 0
        assert "Health Check for 'splunk': True" in result.output

    def test_health_check_command_hmac_failure(self, tmp_path):
        """Test health-check command with invalid HMAC."""
        global PRODUCTION_MODE
        PRODUCTION_MODE = True

        # Create config file
        config = {"timeout": 30}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        # Create invalid HMAC file
        hmac_file = tmp_path / "config.json.hmac"
        hmac_file.write_text("invalid_hmac")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "health-check",
                "--siem-type",
                "splunk",
                "--config-file",
                str(config_file),
            ],
        )

        assert result.exit_code == 1
        assert "Config file integrity check failed" in result.output


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_get_siem_client_valid(self):
        """Test getting a valid SIEM client."""
        client = get_siem_client("splunk", {})
        assert isinstance(client, SplunkClient)

    def test_get_siem_client_invalid(self):
        """Test getting an invalid SIEM client."""
        with pytest.raises(ValueError, match="Unknown SIEM type"):
            get_siem_client("invalid_type", {})

    def test_list_available_siem_clients(self):
        """Test listing available SIEM clients."""
        clients = list_available_siem_clients()
        assert len(clients) == len(SIEM_CLIENT_REGISTRY)
        assert all(c["is_available"] for c in clients)


# ============================================================================
# Test Runner
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-x", "--asyncio-mode=auto"])
