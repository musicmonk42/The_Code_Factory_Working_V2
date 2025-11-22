from typing import Dict, Any, List, Callable, Type, Optional

# Import base/tooling
from .siem_base import (
    BaseSIEMClient,
    SIEMClientConfigurationError,
    SIEMClientError,
    alert_operator,
    PRODUCTION_MODE,
    _base_logger,
)

# Attempt to import individual client classes; do not abort on failure.
# Only successfully imported clients will be added to the registry.
SplunkClient = ElasticClient = DatadogClient = None
AwsCloudWatchClient = None
GcpLoggingClient = None
AzureSentinelClient = AzureEventGridClient = AzureServiceBusClient = None

try:
    from .siem_generic_clients import SplunkClient, ElasticClient, DatadogClient
except ImportError as e:
    _base_logger.error(
        f"Generic SIEM clients unavailable: {e}",
        extra={"client_type": "SIEM_Factory", "correlation_id": "N/A"},
    )
    alert_operator(f"Generic SIEM clients unavailable: {e}", level="ERROR")

try:
    from .siem_aws_clients import AwsCloudWatchClient
except ImportError as e:
    _base_logger.error(
        f"AWS SIEM clients unavailable: {e}",
        extra={"client_type": "SIEM_Factory", "correlation_id": "N/A"},
    )
    alert_operator(f"AWS SIEM clients unavailable: {e}", level="ERROR")

try:
    from .siem_gcp_clients import GcpLoggingClient
except ImportError as e:
    _base_logger.error(
        f"GCP SIEM clients unavailable: {e}",
        extra={"client_type": "SIEM_Factory", "correlation_id": "N/A"},
    )
    alert_operator(f"GCP SIEM clients unavailable: {e}", level="ERROR")

try:
    from .siem_azure_clients import (
        AzureSentinelClient,
        AzureEventGridClient,
        AzureServiceBusClient,
    )
except ImportError as e:
    _base_logger.error(
        f"Azure SIEM clients unavailable: {e}",
        extra={"client_type": "SIEM_Factory", "correlation_id": "N/A"},
    )
    alert_operator(f"Azure SIEM clients unavailable: {e}", level="ERROR")

# --- Client Registry (conditional on import success) ---
SIEM_CLIENT_REGISTRY: Dict[str, Type[BaseSIEMClient]] = {}
if SplunkClient:
    SIEM_CLIENT_REGISTRY["splunk"] = SplunkClient
if ElasticClient:
    SIEM_CLIENT_REGISTRY["elastic"] = ElasticClient
if DatadogClient:
    SIEM_CLIENT_REGISTRY["datadog"] = DatadogClient
if AzureSentinelClient:
    SIEM_CLIENT_REGISTRY["azure_sentinel"] = AzureSentinelClient
if AwsCloudWatchClient:
    SIEM_CLIENT_REGISTRY["aws_cloudwatch"] = AwsCloudWatchClient
if GcpLoggingClient:
    SIEM_CLIENT_REGISTRY["gcp_logging"] = GcpLoggingClient
if AzureEventGridClient:
    SIEM_CLIENT_REGISTRY["azure_event_grid"] = AzureEventGridClient
if AzureServiceBusClient:
    SIEM_CLIENT_REGISTRY["azure_service_bus"] = AzureServiceBusClient


def get_siem_client(
    siem_type: str,
    config: Dict[str, Any],
    metrics_hook: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
) -> BaseSIEMClient:
    """
    Factory function to get an initialized SIEM client.

    Args:
        siem_type: The type of SIEM client to retrieve (e.g., "splunk", "elastic").
        config: The configuration dictionary for the client, including default_timeout_seconds,
                retry_attempts, retry_backoff_factor, rate_limit_tps, rate_limit_burst, and
                secret_scrub_patterns along with SIEM-specific settings.
        metrics_hook: A callable to emit metrics. It should accept
                      (event_name: str, status: str, data: Dict[str, Any]).

    Returns:
        BaseSIEMClient: An instance of the requested SIEM client.

    Raises:
        SIEMClientConfigurationError: If the SIEM client type is unknown or misconfigured.
        SIEMClientError: For any other unexpected initialization errors.
    """
    # Enforce paranoid mode in production
    paranoid_mode = config.get("paranoid_mode", False)
    if PRODUCTION_MODE and not paranoid_mode:
        msg = "'paranoid_mode' must be enabled in PRODUCTION_MODE for SIEM clients."
        _base_logger.critical(
            f"CRITICAL: {msg}",
            extra={"client_type": "SIEM_Factory", "correlation_id": "N/A"},
        )
        alert_operator(f"CRITICAL: {msg}", level="CRITICAL")
        raise SIEMClientConfigurationError(msg, "SIEM_Factory")

    # Metrics hook is required
    if metrics_hook is None:
        msg = "A metrics_hook must be provided to the SIEM client factory."
        _base_logger.critical(
            f"CRITICAL: {msg}",
            extra={"client_type": "SIEM_Factory", "correlation_id": "N/A"},
        )
        alert_operator(f"CRITICAL: {msg}", level="CRITICAL")
        raise SIEMClientConfigurationError(msg, "SIEM_Factory")

    client_class = SIEM_CLIENT_REGISTRY.get(siem_type)
    if not client_class:
        msg = f"Unknown or unavailable SIEM client type: {siem_type}. Available: {list(SIEM_CLIENT_REGISTRY.keys())}"
        _base_logger.critical(
            f"CRITICAL: {msg}",
            extra={"client_type": "SIEM_Factory", "correlation_id": "N/A"},
        )
        alert_operator(f"CRITICAL: {msg}", level="CRITICAL")
        raise SIEMClientConfigurationError(msg, "SIEM_Factory")

    try:
        client_instance = client_class(
            config, metrics_hook=metrics_hook, paranoid_mode=paranoid_mode
        )
        _base_logger.info(
            f"Successfully initialized SIEM client: {siem_type}",
            extra={"client_type": siem_type, "correlation_id": "N/A"},
        )
        return client_instance
    except SIEMClientConfigurationError:
        # Already logged/alerted at origin
        _base_logger.critical(
            f"CRITICAL: SIEM client '{siem_type}' failed configuration.",
            exc_info=True,
            extra={"client_type": "SIEM_Factory", "correlation_id": "N/A"},
        )
        raise
    except Exception as e:
        msg = f"Failed to initialize '{siem_type}' client due to unexpected error: {e}"
        _base_logger.critical(
            f"CRITICAL: {msg}",
            exc_info=True,
            extra={"client_type": "SIEM_Factory", "correlation_id": "N/A"},
        )
        alert_operator(f"CRITICAL: {msg}", level="CRITICAL")
        raise SIEMClientError(msg, "SIEM_Factory", original_exception=e)


def list_available_siem_clients() -> List[Dict[str, Any]]:
    """
    Lists available SIEM client types and their dependency status.
    In production mode, this lists clients whose critical dependencies are met at import time.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries, each describing an available SIEM client.
    """
    _base_logger.info(
        "Listing available SIEM clients and their dependency status.",
        extra={"client_type": "SIEM_Factory", "correlation_id": "N/A"},
    )
    available_clients_info: List[Dict[str, Any]] = []

    for siem_type, client_class in SIEM_CLIENT_REGISTRY.items():
        is_client_available = True
        missing_or_errors: List[str] = []

        # We consider a client "available" if its module imported successfully (i.e., it's in the registry).
        # Deeper runtime configuration (e.g., credentials, endpoints) is validated during get_siem_client().
        # We intentionally do not instantiate clients here to avoid side effects and unsafe global toggles.
        try:
            description = (
                (client_class.__doc__ or "No description.").strip().split("\n")[0]
            )
        except Exception:
            description = "No description."

        available_clients_info.append(
            {
                "type": siem_type,
                "class_name": client_class.__name__,
                "is_available": is_client_available,
                "required_dependencies_status": missing_or_errors,  # empty if available
                "description": description,
            }
        )

    # Log the availability report
    _base_logger.info(
        "SIEM Client Availability Report:",
        extra={"client_type": "SIEM_Factory", "correlation_id": "N/A"},
    )
    for client_info in available_clients_info:
        status_msg = "Available" if client_info["is_available"] else "Unavailable"
        deps_msg = (
            f" (Missing/Error: {', '.join(client_info['required_dependencies_status'])})"
            if not client_info["is_available"]
            else ""
        )
        _base_logger.info(
            f"  - {client_info['type']}: {status_msg}{deps_msg}",
            extra={"client_type": "SIEM_Factory", "correlation_id": "N/A"},
        )

    return available_clients_info


# Perform initial availability check on module load (logs status; no side effects)
list_available_siem_clients()
