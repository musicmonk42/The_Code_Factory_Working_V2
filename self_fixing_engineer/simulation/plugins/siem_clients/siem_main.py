import os
import asyncio
import json
import logging
import datetime
import uuid
import sys  # For sys.exit to fail fast
import socket  # For hostname in test_log_entry
import base64  # For base64.b64encode used in dummy shared_key
import hashlib
import hmac
import re
from typing import Dict, Any

# Import the factory and base logger from the new structure
from .siem_factory import (
    get_siem_client,
    list_available_siem_clients,
    SIEM_CLIENT_REGISTRY,
)
from .siem_base import (
    SIEMClientConfigurationError,
    SIEMClientError,
    _base_logger,
    PRODUCTION_MODE,
    alert_operator,
    SECRETS_MANAGER,
)

# --- Async Click Integration ---
try:
    import asyncclick as click

    CLICK_AVAILABLE = True
except ImportError:
    _base_logger.warning(
        "asyncclick library not found. Production CLI commands will not be available."
    )
    CLICK_AVAILABLE = False


# --- Helpers: robust secrets retrieval and output scrubbing ---


async def _maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value


async def _get_secret(key: str, default: Any = None, *, required: bool = False) -> Any:
    """
    Supports both sync and async SECRETS_MANAGER.get_secret implementations.
    """
    val = (
        SECRETS_MANAGER.get_secret(key, default, required=required)
        if default is not None
        else SECRETS_MANAGER.get_secret(key, required=required)
    )
    return await _maybe_await(val)


_SCRUB_KEY_REGEX = re.compile(
    r"(password|passwd|secret|api[_-]?key|token|connection[_-]?string|shared[_-]?key|private[_-]?key|client[_-]?secret)",
    re.IGNORECASE,
)


def _scrub_obj(obj: Any) -> Any:
    """
    Best-effort scrubbing for console/CLI output. Redacts values for keys that look sensitive.
    Does not mutate the original object.
    """
    try:
        if isinstance(obj, dict):
            redacted = {}
            for k, v in obj.items():
                if isinstance(k, str) and _SCRUB_KEY_REGEX.search(k):
                    redacted[k] = "***REDACTED***"
                else:
                    redacted[k] = _scrub_obj(v)
            return redacted
        if isinstance(obj, list):
            return [_scrub_obj(i) for i in obj]
        if isinstance(obj, str):
            # Redact common inline key=value patterns
            s = obj
            s = re.sub(
                r'(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*["\']?([A-Za-z0-9_\-\.=/+]{6,})',
                r"\1=***REDACTED***",
                s,
            )
            return s
        return obj
    except Exception:
        return obj


def _scrub_and_dump(obj: Any) -> str:
    try:
        return json.dumps(_scrub_obj(obj), indent=2, default=str)
    except Exception:
        # Fallback for non-serializable objects
        try:
            return str(_scrub_obj(obj))
        except Exception:
            return "<unprintable>"


# --- Test Runner Functionality ---
async def run_tests():
    """Main function to run all SIEM client tests."""

    # Check for an explicit test flag if not in production mode.
    if os.getenv("RUN_SIEM_TESTS", "false").lower() != "true":
        _base_logger.critical(
            "CRITICAL: siem_main.py (test runner) is attempting to execute without 'RUN_SIEM_TESTS=true' environment flag. Aborting."
        )
        alert_operator(
            "CRITICAL: siem_main.py (test runner) is attempting to execute without explicit test flag. Aborting.",
            level="CRITICAL",
        )
        sys.exit(1)

    logging.basicConfig(level=logging.DEBUG)
    _base_logger.setLevel(logging.DEBUG)

    print("--- Running SIEM Clients Module Test ---")
    print("\n--- Available SIEM Clients and their Dependencies ---")
    for client_info in list_available_siem_clients():
        print(
            f"  Type: {client_info['type']}, Available: {client_info['is_available']}, Requires: {', '.join(client_info['required_dependencies_status'])}"
        )
        print(f"    Description: {client_info['description']}")

    # Dummy metrics hook for testing
    def test_metrics_hook(event_name: str, status: str, data: Dict[str, Any]):
        print(f"[METRICS_HOOK] {event_name}.{status}: {_scrub_and_dump(data)}")

    # --- Configure Environment Variables for Testing ---
    # Set these environment variables for the respective clients to work.
    # For production, these should come from a secure secrets manager.

    # General config for all clients
    test_config = {
        "default_timeout_seconds": 15,
        "retry_attempts": 3,
        "retry_backoff_factor": 2.0,
        "rate_limit_tps": 5,  # Example rate limit
        "rate_limit_burst": 2,
        "secret_scrub_patterns": [
            r"password",
            r"api_key",
            r"secret_info",
            r"connection_string",
        ],  # Example scrubbing
        "paranoid_mode": True,  # Ensure paranoid mode is ON for tests
        "splunk": {
            "url": os.getenv(
                "SIEM_SPLUNK_HEC_URL",
                "http://dummy-splunk:8088/services/collector/event",
            ),
            "token": os.getenv("SIEM_SPLUNK_HEC_TOKEN", "dummy_token_splunk"),
        },
        "elastic": {
            "url": os.getenv("SIEM_ELASTIC_URL", "http://dummy-elastic:9200"),
            "username": os.getenv("SIEM_ELASTIC_USERNAME", "dummy_user"),
            "password": os.getenv("SIEM_ELASTIC_PASSWORD", "dummy_password"),
        },
        "datadog": {
            "url": os.getenv(
                "SIEM_DATADOG_API_URL",
                "https://dummy-intake.logs.datadoghq.com/api/v2/logs",
            ),
            "query_url": os.getenv(
                "SIEM_DATADOG_QUERY_URL",
                "https://dummy-api.datadoghq.com/api/v1/logs-queries",
            ),
            "api_key": os.getenv("SIEM_DATADOG_API_KEY", "dummy_dd_api_key"),
            "application_key": os.getenv(
                "SIEM_DATADOG_APPLICATION_KEY", "dummy_dd_app_key"
            ),
        },
        "azure_sentinel": {
            "workspace_id": os.getenv("SIEM_AZURE_WORKSPACE_ID", "dummy_workspace_id"),
            "shared_key": os.getenv(
                "SIEM_AZURE_SHARED_KEY", base64.b64encode(b"dummy_shared_key").decode()
            ),
            "client_id": os.getenv("AZURE_CLIENT_ID", "dummy_client_id"),
            "tenant_id": os.getenv("AZURE_TENANT_ID", "dummy_tenant_id"),
            "client_secret": os.getenv("AZURE_CLIENT_SECRET", "dummy_client_secret"),
        },
        "aws_cloudwatch": {
            "region_name": os.getenv("AWS_REGION", "us-east-1"),
            "log_group_name": os.getenv(
                "AWS_CLOUDWATCH_LOG_GROUP_NAME", "sfe-test-logs"
            ),
        },
        "gcp_logging": {
            "project_id": os.getenv("GCP_PROJECT_ID", "dummy-gcp-project"),
            "log_name": os.getenv("GCP_LOG_NAME", "sfe-gcp-test-log"),
        },
        "azure_event_grid": {
            "endpoint": os.getenv(
                "SIEM_AZURE_EVENTGRID_ENDPOINT",
                "https://dummy-topic.eastus-1.eventgrid.azure.net/api/events",
            ),
            "key": os.getenv("SIEM_AZURE_EVENTGRID_KEY", "dummy_eventgrid_key"),
        },
        "azure_service_bus": {
            "connection_string": os.getenv(
                "SIEM_AZURE_SERVICEBUS_CONNECTION_STRING",
                "Endpoint=sb://dummy.servicebus.windows.net/;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=dummy_key",
            ),
            "queue_name": os.getenv("SIEM_AZURE_SERVICEBUS_QUEUE_NAME", "dummy-queue"),
        },
    }

    # Validate test_config for dummy/mock values
    for client_type, client_config in test_config.items():
        if isinstance(client_config, dict):
            for key, value in client_config.items():
                if isinstance(value, str) and (
                    "dummy" in value.lower()
                    or "mock" in value.lower()
                    or "test" in value.lower()
                    or "example.com" in value.lower()
                ):
                    _base_logger.critical(
                        f"CRITICAL: Dummy/test config value detected for {client_type}.{key}: {value}. Aborting test run."
                    )
                    alert_operator(
                        f"CRITICAL: Dummy/test config value detected in SIEM test config for {client_type}.{key}. Aborting.",
                        level="CRITICAL",
                    )
                    sys.exit(1)

    test_log_entry = {
        "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
        "event_type": "test_event",
        "message": "This is a test log message from the SIEM client.",
        "severity": "INFO",
        "hostname": socket.gethostname(),
        "source_ip": "192.168.1.1",
        "user_id": "testuser",
        "details": {
            "test_field": "test_value",
            "sensitive_info": "my_secret_password_123",
            "api_key_data": "ak-12345-xyz",
        },
    }

    # Test each client
    for siem_type in SIEM_CLIENT_REGISTRY.keys():  # Iterate through registered types
        print(f"\n--- Testing Client: {siem_type} ---")

        # Check availability status from list_available_siem_clients for more accurate dependency check
        client_info = next(
            (
                info
                for info in list_available_siem_clients()
                if info["type"] == siem_type
            ),
            None,
        )
        if client_info and not client_info["is_available"]:
            print(
                f"Skipping {siem_type} client test: Dependencies not met. Required: {', '.join(client_info['required_dependencies_status'])}"
            )
            continue

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

                # Test send_log (single)
                send_success, send_msg = await client.send_log(
                    test_log_entry, correlation_id=correlation_id
                )
                print(f"Single Log Send: {send_success} - {send_msg}")

                # Test send_logs (batch)
                batch_log_entries = [
                    {
                        **test_log_entry,
                        "message": "Batch log 1",
                        "event_id": str(uuid.uuid4()),
                    },
                    {
                        **test_log_entry,
                        "message": "Batch log 2",
                        "severity": "WARNING",
                        "event_id": str(uuid.uuid4()),
                    },
                    {
                        **test_log_entry,
                        "message": "Batch log 3 (sensitive)",
                        "details": {
                            "payment_info": "1111-2222-3333-4444",
                            "private_key": "abc-xyz-123",
                        },
                        "event_id": str(uuid.uuid4()),
                    },
                ]
                batch_success, batch_msg, failed_logs = await client.send_logs(
                    batch_log_entries, correlation_id=correlation_id
                )
                print(f"Batch Log Send: {batch_success} - {batch_msg}")
                if failed_logs:
                    print(f"  Failed logs in batch: {_scrub_and_dump(failed_logs)}")

                # Simplified query support detection: just try and catch NotImplementedError
                try:
                    print("Attempting to query logs...")
                    query_string_test = 'message:"test log"'
                    if siem_type == "azure_sentinel":
                        query_string_test = 'search "test log"'
                    if siem_type == "aws_cloudwatch":
                        query_string_test = "fields @timestamp, @message | filter @message like /test log/"
                    if siem_type == "gcp_logging":
                        query_string_test = 'jsonPayload.message:"test log"'

                    query_results = await client.query_logs(
                        query_string_test, "1h", 2, correlation_id=correlation_id
                    )
                    print(
                        f"Query Results (first 2): {_scrub_and_dump(query_results[:2])}"
                    )
                    print(f"Total query results: {len(query_results)}")
                except NotImplementedError as nie:
                    print(
                        f"Querying not supported or implemented for {siem_type}: {nie}."
                    )
                except SIEMClientQueryError as qe:
                    print(
                        f"Query Error for {siem_type}: {_scrub_and_dump({'error': qe.args[0], 'correlation_id': getattr(qe, 'correlation_id', None)})}"
                    )
                except Exception as exc:
                    print(
                        f"Unexpected error during query for {siem_type}: {_scrub_and_dump(str(exc))}"
                    )

        except (SIEMClientConfigurationError, ImportError) as e:
            print(
                f"Configuration/Import Error for {siem_type}: {_scrub_and_dump(str(e))}"
            )
        except SIEMClientError as e:
            print(
                f"SIEM Client Error for {siem_type}: {e.client_type} - {_scrub_and_dump(e.args[0])} (Correlation ID: {e.correlation_id})"
            )
            if getattr(e, "details", None):
                print(f"  Details: {_scrub_and_dump(e.details)}")
        except Exception as e:
            print(
                f"An unexpected error occurred for {siem_type}: {_scrub_and_dump(str(e))}"
            )
            import traceback

            traceback.print_exc()

    print("\n--- All SIEM Clients Module Tests Complete ---")


# --- Production CLI Functionality ---
if CLICK_AVAILABLE:

    @click.group()
    async def cli():
        """
        Secure Production CLI for SIEM client operations.
        """
        # Enforce production mode check at the top level for all CLI commands.
        if not PRODUCTION_MODE:
            _base_logger.critical(
                "CRITICAL: Production CLI commands are restricted to PRODUCTION_MODE. Aborting."
            )
            alert_operator(
                "CRITICAL: Production CLI commands are restricted to PRODUCTION_MODE. Aborting.",
                level="CRITICAL",
            )
            # Using click's fail-fast
            raise click.ClickException(
                "Production CLI commands are restricted to PRODUCTION_MODE."
            )

    @cli.command("health-check")
    @click.option(
        "--siem-type",
        required=True,
        type=click.Choice(list(SIEM_CLIENT_REGISTRY.keys())),
        help="The type of SIEM client to check (e.g., 'splunk', 'aws_cloudwatch').",
    )
    @click.option(
        "--config-file",
        required=True,
        type=click.Path(exists=True, dir_okay=False, readable=True),
        help="Path to a JSON configuration file.",
    )
    async def health_check_command(siem_type, config_file):
        """Perform a health check on a specified SIEM client."""
        # Config file integrity check: Validate config file integrity with HMAC
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config_data = f.read()

            hmac_file = config_file + ".hmac"
            if not os.path.exists(hmac_file):
                raise SIEMClientConfigurationError(
                    f"HMAC signature file not found for config: {hmac_file}", "Main"
                )

            with open(hmac_file, "r", encoding="utf-8") as f:
                expected_hmac = f.read().strip()

            # Use a shared secret to generate the HMAC for validation
            hmac_secret_val = SECRETS_MANAGER.get_secret(
                "SIEM_CONFIG_HMAC_SECRET", required=True
            )
            hmac_secret = await _maybe_await(hmac_secret_val)
            if not isinstance(hmac_secret, (str, bytes)):
                raise SIEMClientConfigurationError(
                    "HMAC secret is not a string/bytes value.", "Main"
                )
            if isinstance(hmac_secret, str):
                hmac_secret = hmac_secret.encode("utf-8")

            generated_hmac = hmac.new(
                hmac_secret, config_data.encode("utf-8"), hashlib.sha256
            ).hexdigest()

            if not hmac.compare_digest(generated_hmac, expected_hmac):
                raise SIEMClientConfigurationError(
                    "Config file integrity check failed: HMAC mismatch.", "Main"
                )

            config = json.loads(config_data)

        except Exception as e:
            _base_logger.critical(
                f"CRITICAL: Failed to load or validate config file '{config_file}': {e}. Aborting."
            )
            alert_operator(
                f"CRITICAL: Failed to load or validate config file '{config_file}': {e}. Aborting.",
                level="CRITICAL",
            )
            raise click.ClickException("Failed to load or validate config file.")

        try:
            async with get_siem_client(
                siem_type, config, metrics_hook=lambda *args, **kwargs: None
            ) as client:
                is_healthy, msg = await client.health_check()
                click.echo(
                    f"Health Check for '{siem_type}': {is_healthy} - {_scrub_and_dump(msg)}"
                )
                # Return proper exit code through exception to asyncclick runner if needed
                if not is_healthy:
                    raise click.ClickException("Health check failed.")
        except click.ClickException:
            raise
        except Exception as e:
            click.echo(
                f"Health check failed with error: {_scrub_and_dump(str(e))}", err=True
            )
            raise click.ClickException("Health check failed.")

    @cli.command("send-log")
    @click.option(
        "--siem-type",
        required=True,
        type=click.Choice(list(SIEM_CLIENT_REGISTRY.keys())),
        help="The type of SIEM client to use.",
    )
    @click.option(
        "--config-file",
        required=True,
        type=click.Path(exists=True, dir_okay=False, readable=True),
        help="Path to a JSON configuration file.",
    )
    @click.option(
        "--log-file",
        required=True,
        type=click.Path(exists=True, dir_okay=False, readable=True),
        help="Path to a single JSON log entry file.",
    )
    async def send_log_command(siem_type, config_file, log_file):
        """Send a single log entry to a specified SIEM client."""
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
            with open(log_file, "r", encoding="utf-8") as f:
                log_entry = json.load(f)
        except Exception as e:
            click.echo(f"Error loading files: {_scrub_and_dump(str(e))}", err=True)
            raise click.ClickException("Failed to load files.")

        try:
            async with get_siem_client(
                siem_type, config, metrics_hook=lambda *args, **kwargs: None
            ) as client:
                success, msg = await client.send_log(log_entry)
                click.echo(f"Log Send: {success} - {_scrub_and_dump(msg)}")
                if not success:
                    raise click.ClickException("Log send failed.")
        except click.ClickException:
            raise
        except Exception as e:
            click.echo(
                f"Log send failed with error: {_scrub_and_dump(str(e))}", err=True
            )
            raise click.ClickException("Log send failed.")

    @cli.command("send-batch")
    @click.option(
        "--siem-type",
        required=True,
        type=click.Choice(list(SIEM_CLIENT_REGISTRY.keys())),
        help="The type of SIEM client to use.",
    )
    @click.option(
        "--config-file",
        required=True,
        type=click.Path(exists=True, dir_okay=False, readable=True),
        help="Path to a JSON configuration file.",
    )
    @click.option(
        "--log-file",
        required=True,
        type=click.Path(exists=True, dir_okay=False, readable=True),
        help="Path to a JSON file containing a list of log entries.",
    )
    async def send_batch_command(siem_type, config_file, log_file):
        """Send a batch of log entries to a specified SIEM client."""
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
            with open(log_file, "r", encoding="utf-8") as f:
                log_entries = json.load(f)
            if not isinstance(log_entries, list):
                raise ValueError("Log file must contain a JSON array of log entries.")
        except Exception as e:
            click.echo(f"Error loading files: {_scrub_and_dump(str(e))}", err=True)
            raise click.ClickException("Failed to load files.")

        try:
            async with get_siem_client(
                siem_type, config, metrics_hook=lambda *args, **kwargs: None
            ) as client:
                success, msg, failed = await client.send_logs(log_entries)
                click.echo(f"Batch Send: {success} - {_scrub_and_dump(msg)}")
                if failed:
                    click.echo(
                        f"Failed logs in batch: {_scrub_and_dump(failed)}", err=True
                    )
                if not success:
                    raise click.ClickException("Batch send failed.")
        except click.ClickException:
            raise
        except Exception as e:
            click.echo(
                f"Batch send failed with error: {_scrub_and_dump(str(e))}", err=True
            )
            raise click.ClickException("Batch send failed.")

    @cli.command("query-logs")
    @click.option(
        "--siem-type",
        required=True,
        type=click.Choice(list(SIEM_CLIENT_REGISTRY.keys())),
        help="The type of SIEM client to use.",
    )
    @click.option(
        "--config-file",
        required=True,
        type=click.Path(exists=True, dir_okay=False, readable=True),
        help="Path to a JSON configuration file.",
    )
    @click.option("--query-string", required=True, help="The query string to execute.")
    @click.option(
        "--time-range", default="24h", help="Relative time range (e.g., '24h', '7d')."
    )
    @click.option(
        "--limit", type=int, default=100, help="Maximum number of results to return."
    )
    async def query_logs_command(
        siem_type, config_file, query_string, time_range, limit
    ):
        """Query a specified SIEM client for logs."""
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            click.echo(
                f"Error loading config file: {_scrub_and_dump(str(e))}", err=True
            )
            raise click.ClickException("Failed to load config file.")

        try:
            async with get_siem_client(
                siem_type, config, metrics_hook=lambda *args, **kwargs: None
            ) as client:
                results = await client.query_logs(query_string, time_range, limit)
                click.echo(f"Query Results ({len(results)} total):")
                click.echo(_scrub_and_dump(results))
        except NotImplementedError as e:
            click.echo(
                f"Error: {siem_type} client does not support querying. {_scrub_and_dump(str(e))}",
                err=True,
            )
            raise click.ClickException("Client does not support querying.")
        except Exception as e:
            click.echo(f"Query failed with error: {_scrub_and_dump(str(e))}", err=True)
            raise click.ClickException("Query failed.")


async def main():
    # BLOCKER: Never allow this file (or any test runner/entry point) to execute in production.
    if PRODUCTION_MODE:
        _base_logger.critical(
            "CRITICAL: siem_main.py (test runner) is attempting to execute in PRODUCTION_MODE. Aborting for security."
        )
        alert_operator(
            "CRITICAL: siem_main.py (test runner) is attempting to execute in PRODUCTION_MODE. Aborting.",
            level="CRITICAL",
        )
        sys.exit(1)

    await run_tests()


if __name__ == "__main__":
    if PRODUCTION_MODE and CLICK_AVAILABLE:
        # For production, run the CLI (asyncclick handles the event loop)
        cli()
    else:
        # For testing/development, run the test runner
        asyncio.run(main())
