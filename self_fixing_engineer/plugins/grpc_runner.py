"""
gRPC runner: production-ready async utilities for secure gRPC connectivity,
plugin health checks, manifest validation, and Prometheus metrics.

Environment variables (subset):
  PRODUCTION_MODE (bool) [false]

TLS/mTLS secrets (required in PRODUCTION_MODE):
  GRPC_TLS_CERT_PATH
  GRPC_TLS_KEY_PATH
  GRPC_TLS_CA_PATH
  GRPC_ENDPOINT_ALLOWLIST  (comma-separated host:port values)

Manifest integrity (prod):
  MANIFEST_HMAC_KEY
"""

import asyncio
import logging
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

import hmac
import hashlib

from omnicore_engine.plugin_registry import plugin, PlugInKind

# --- Global Production Mode Flag ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

# --- Logging Setup ---
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# --- Centralized Utilities (Import-Safe) ---
MISSING_DEPS = False
try:
    # Align with alerting core: use send_alert and scrub
    from core_utils import send_alert as alert_operator, scrub as scrub_sensitive_data
    from core_audit import audit_logger
    from core_secrets import SECRETS_MANAGER
except ImportError as e:
    logger.critical(
        "CRITICAL: Missing core dependency for gRPC runner: %s. Aborting startup.", e
    )
    # Provide fallbacks so importing this module doesn't kill pytest, but fail when actually used
    MISSING_DEPS = True

    def alert_operator(*args, **kwargs):
        if PRODUCTION_MODE:
            raise ImportError("core_utils.send_alert is required but missing")
        logger.warning("alert_operator called but core_utils.send_alert is missing.")

    def scrub_sensitive_data(value):
        if PRODUCTION_MODE:
            raise ImportError("core_utils.scrub is required but missing")
        return value

    class _MockLogger:
        def log_event(self, *args, **kwargs):
            if PRODUCTION_MODE:
                raise ImportError("core_audit.audit_logger is required but missing")
            logger.warning("audit_logger.log_event called but core_audit is missing.")
    audit_logger = _MockLogger()

    class _MockSecrets:
        def get_secret(self, key, required=False):
            if PRODUCTION_MODE or required:
                raise ImportError("core_secrets.SECRETS_MANAGER is required but missing")
            logger.warning("SECRETS_MANAGER.get_secret called but core_secrets is missing.")
            return None
    SECRETS_MANAGER = _MockSecrets()

    # Only exit in production mode; allow import for testing
    if PRODUCTION_MODE:
        logger.critical("Cannot run gRPC runner in production without core dependencies")
        sys.exit(1)


# --- Custom Exceptions ---
class AnalyzerCriticalError(Exception):
    """
    Critical errors should halt execution and alert ops.
    """
    def __init__(self, message: str, alert_level: str = "CRITICAL"):
        super().__init__(message)
        try:
            alert_operator(message, level=alert_level)
        except Exception:
            # Avoid masking the original error if alerting fails
            pass

class NonCriticalError(Exception):
    """Recoverable issues that should be logged but not halt execution."""
    pass

# --- Dependencies: hard-fail if missing (Import-Safe) ---
try:
    import grpc
    import prometheus_client
    from pydantic import BaseModel, Extra, ValidationError, Field, validator
    from grpc_health.v1 import health_pb2, health_pb2_grpc
except ImportError as e:
    logger.critical(f"CRITICAL: Missing core dependency for gRPC runner: {e}. Aborting startup.")
    MISSING_DEPS = True
    grpc = None
    prometheus_client = None
    # Pydantic fallbacks
    class BaseModel: pass
    class Extra: pass
    class ValidationError(Exception): pass
    def Field(*args, **kwargs): return None
    def validator(*args, **kwargs): return lambda f: f
    # gRPC health fallbacks
    health_pb2 = None
    health_pb2_grpc = None


# --- Prometheus Metrics (own registry to avoid global collisions) ---
_metrics_registry = prometheus_client.CollectorRegistry(auto_describe=True) if prometheus_client else None

PLUGIN_HEALTH_GAUGE = prometheus_client.Gauge(
    'plugin_health_status',
    'Health status of the plugin (1 for SERVING, 0 otherwise)',
    ['plugin_name'],
    registry=_metrics_registry
) if prometheus_client else None

PLUGIN_OPERATION_COUNTER = prometheus_client.Counter(
    'plugin_operations_total',
    'Total number of plugin operations',
    ['plugin_name', 'operation_type'],
    registry=_metrics_registry
) if prometheus_client else None


# --- Manifest Model ---
class PluginManifest(BaseModel, extra=Extra.forbid):
    """Strict validation of plugin manifest."""
    name: str = Field(..., min_length=1)
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    entrypoint: str = Field(..., min_length=1)
    description: str = "No description provided."
    author: str = "Unknown"
    capabilities: List[str] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    min_core_version: str = Field("1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    max_core_version: str = Field("999.0.0", pattern=r"^\d+\.\d+\.\d+$")
    health_check: str = Field("plugin_health", min_length=1)
    api_version: str = Field("v1", min_length=1)
    license: str = "Proprietary"
    homepage: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    signature: Optional[str] = None
    whitelisted_paths: List[str] = Field(default_factory=list)
    whitelisted_commands: List[str] = Field(default_factory=list)
    is_demo_plugin: bool = Field(False)

    @validator('name', 'entrypoint', 'health_check', 'api_version')
    def no_dummy_in_prod(cls, v):
        if PRODUCTION_MODE and any(k in v.lower() for k in ("dummy", "test", "mock")):
            raise ValueError(f"Dummy/test field '{v}' not allowed in production.")
        return v

# --- TLS/Endpoint Security ---
GRPC_TLS_CERT_PATH_SECRET = "GRPC_TLS_CERT_PATH"  # client cert
GRPC_TLS_KEY_PATH_SECRET  = "GRPC_TLS_KEY_PATH"   # client key
GRPC_TLS_CA_PATH_SECRET   = "GRPC_TLS_CA_PATH"    # CA cert for server verify
GRPC_ENDPOINT_ALLOWLIST_SECRET = "GRPC_ENDPOINT_ALLOWLIST"  # comma-separated host:port

def _get_tls_credentials() -> Optional[grpc.ChannelCredentials]:
    """Load TLS credentials from secrets manager."""
    try:
        cert_path = SECRETS_MANAGER.get_secret(GRPC_TLS_CERT_PATH_SECRET, required=PRODUCTION_MODE)
        key_path  = SECRETS_MANAGER.get_secret(GRPC_TLS_KEY_PATH_SECRET,  required=PRODUCTION_MODE)
        ca_path   = SECRETS_MANAGER.get_secret(GRPC_TLS_CA_PATH_SECRET,   required=PRODUCTION_MODE)
    except AnalyzerCriticalError:
        sys.exit(1)

    if not all([cert_path, key_path, ca_path]):
        if PRODUCTION_MODE:
            logger.critical("CRITICAL: Missing TLS credentials for gRPC. Aborting startup.")
            alert_operator("CRITICAL: Missing gRPC TLS credentials in PRODUCTION_MODE. Aborting.", level="CRITICAL")
            sys.exit(1)
        logger.warning("Missing TLS credential paths. Insecure gRPC allowed in non-prod only.")
        return None

    try:
        with open(cert_path, 'rb') as f:
            client_cert = f.read()
        with open(key_path, 'rb') as f:
            client_key = f.read()
        with open(ca_path, 'rb') as f:
            ca_cert = f.read()
        return grpc.ssl_channel_credentials(root_certificates=ca_cert,
                                            private_key=client_key,
                                            certificate_chain=client_cert)
    except Exception as e:
        logger.critical(f"CRITICAL: Failed to load gRPC TLS credentials: {e}. Aborting startup.", exc_info=True)
        alert_operator(f"CRITICAL: Failed to load gRPC TLS credentials: {e}. Aborting.", level="CRITICAL")
        sys.exit(1)

def _is_endpoint_allowed(address: str) -> bool:
    """Check if a gRPC endpoint is in the allowlist."""
    allowlist_str = SECRETS_MANAGER.get_secret(GRPC_ENDPOINT_ALLOWLIST_SECRET, required=PRODUCTION_MODE)
    if not allowlist_str:
        if PRODUCTION_MODE:
            logger.critical("CRITICAL: gRPC endpoint allowlist not set in PRODUCTION_MODE. Aborting startup.")
            alert_operator("CRITICAL: gRPC endpoint allowlist not configured in PRODUCTION_MODE. Aborting.", level="CRITICAL")
            sys.exit(1)
        logger.warning("gRPC endpoint allowlist not configured. All endpoints allowed in non-prod.")
        return True

    allowlist = [ep.strip() for ep in allowlist_str.split(',') if ep.strip()]
    if address in allowlist:
        return True

    logger.critical(f"CRITICAL: gRPC endpoint '{address}' is not in the allowlist. Aborting connection.")
    audit_logger.log_event("grpc_connection_forbidden", endpoint=address, reason="not_in_allowlist")
    alert_operator(f"CRITICAL: gRPC endpoint '{address}' not in allowlist. Aborting.", level="CRITICAL")
    return False

# --- Health ---
async def plugin_health(channel: grpc.aio.Channel, plugin_name: str, health_check_method_name: str = "Check") -> str:
    """
    Check the health of a gRPC service via standard Health service.
    """
    stub = health_pb2_grpc.HealthStub(channel)
    status_str = "UNKNOWN"
    try:
        request = health_pb2.HealthCheckRequest(service="")
        response = await asyncio.wait_for(getattr(stub, health_check_method_name)(request), timeout=5.0)
        status_str = health_pb2.HealthCheckResponse.ServingStatus.Name(response.status)
        logger.info(f"Health check PASSED for plugin '{plugin_name}': {status_str}")
        PLUGIN_HEALTH_GAUGE.labels(plugin_name=plugin_name).set(1 if status_str == "SERVING" else 0)
        PLUGIN_OPERATION_COUNTER.labels(plugin_name=plugin_name, operation_type='health_check').inc()
        audit_logger.log_event("plugin_health_check", plugin=plugin_name, status=status_str,
                               details={"method": health_check_method_name})
        return status_str
    except asyncio.TimeoutError:
        status_str = "TIMEOUT"
        logger.warning(f"Health check TIMEOUT for plugin '{plugin_name}'")
        PLUGIN_HEALTH_GAUGE.labels(plugin_name=plugin_name).set(0)
        PLUGIN_OPERATION_COUNTER.labels(plugin_name=plugin_name, operation_type='health_check_timeout').inc()
        audit_logger.log_event("plugin_health_check_failed", plugin=plugin_name, status=status_str, reason="timeout")
        alert_operator(f"CRITICAL: Plugin '{plugin_name}' health check TIMEOUT.", level="CRITICAL")
        return status_str
    except grpc.aio.AioRpcError as e:
        status_str = "NOT_SERVING" if e.code() == grpc.StatusCode.UNAVAILABLE else "SERVICE_UNKNOWN"
        logger.error(f"Health check FAILED for '{plugin_name}': {e.details()} (Code: {e.code()})")
        PLUGIN_HEALTH_GAUGE.labels(plugin_name=plugin_name).set(0)
        PLUGIN_OPERATION_COUNTER.labels(plugin_name=plugin_name, operation_type='health_check_failed').inc()
        audit_logger.log_event("plugin_health_check_failed", plugin=plugin_name, status=status_str,
                               reason="grpc_error", code=e.code().name, details=e.details())
        alert_operator(f"CRITICAL: Plugin '{plugin_name}' health check FAILED: {e.details()} (Code: {e.code().name}).",
                       level="CRITICAL")
        return status_str
    except Exception as e:
        status_str = "ERROR"
        logger.error(f"Health check ERROR for '{plugin_name}': {e}", exc_info=True)
        PLUGIN_HEALTH_GAUGE.labels(plugin_name=plugin_name).set(0)
        PLUGIN_OPERATION_COUNTER.labels(plugin_name=plugin_name, operation_type='health_check_error').inc()
        audit_logger.log_event("plugin_health_check_failed", plugin=plugin_name, status=status_str,
                               reason="unexpected_error", error=str(e))
        alert_operator(f"CRITICAL: Plugin '{plugin_name}' health check unexpected error: {e}.", level="CRITICAL")
        return status_str

# --- Connect ---
async def connect(address: str, retries: int = 3, backoff_sec: float = 1.0, max_backoff_sec: float = 30.0) -> grpc.aio.Channel:
    """
    Connect to a gRPC server with retries and exponential backoff.
    In PRODUCTION_MODE, TLS is required.
    """
    if not _is_endpoint_allowed(address):
        raise ConnectionRefusedError(f"Connection to {address} forbidden by allowlist.")

    tls_credentials = _get_tls_credentials()
    if PRODUCTION_MODE and not tls_credentials:
        logger.critical("CRITICAL: TLS credentials required in PRODUCTION_MODE but not loaded.")
        alert_operator("CRITICAL: TLS credentials missing for gRPC in PRODUCTION_MODE.", level="CRITICAL")
        sys.exit(1)

    last_error: Optional[str] = None
    delay = max(0.1, float(backoff_sec))
    for attempt in range(1, max(1, int(retries)) + 1):
        try:
            if tls_credentials:
                channel = grpc.aio.secure_channel(address, tls_credentials)
                logger.debug(f"Attempting secure gRPC connection to {address}...")
            else:
                channel = grpc.aio.insecure_channel(address)
                logger.warning(f"Attempting INSECURE gRPC connection to {address} (non-prod only).")

            await asyncio.wait_for(channel.channel_ready(), timeout=5.0)
            logger.info(f"Connected to gRPC server at {address} (attempt {attempt})")
            audit_logger.log_event("grpc_connect_success", endpoint=address, attempt=attempt)
            return channel
        except asyncio.TimeoutError:
            last_error = "Timeout"
            logger.warning(f"Connection timeout to {address} (attempt {attempt}/{retries}). Retrying in {delay:.1f}s...")
            audit_logger.log_event("grpc_connect_timeout", endpoint=address, attempt=attempt)
        except grpc.aio.AioRpcError as e:
            last_error = e.details() if hasattr(e, 'details') else str(e)
            logger.warning(f"Connection failed to {address} (attempt {attempt}/{retries}): {last_error} "
                           f"(Code: {e.code()}). Retrying in {delay:.1f}s...")
            audit_logger.log_event("grpc_connect_failed", endpoint=address, attempt=attempt,
                                   error=str(e), code=e.code().name)
        except ConnectionRefusedError:
            raise
        except Exception as e:
            last_error = str(e)
            logger.error(f"Unexpected error connecting to {address} (attempt {attempt}/{retries}): {e}. "
                         f"Retrying in {delay:.1f}s...", exc_info=True)
            audit_logger.log_event("grpc_connect_error", endpoint=address, attempt=attempt, error=str(e))

        await asyncio.sleep(delay)
        delay = min(max_backoff_sec, delay * 2.0)

    logger.critical(f"CRITICAL: Failed to connect to {address} after {retries} attempts: {last_error}")
    audit_logger.log_event("grpc_connect_critical_failure", endpoint=address, retries=retries, last_error=last_error)
    alert_operator(f"CRITICAL: Failed to connect to gRPC endpoint {address} after {retries} attempts.", level="CRITICAL")
    raise ConnectionError(f"Failed to connect to {address} after {retries} attempts: {last_error}")

# --- Plugin method runner ---
@plugin(kind=PlugInKind.EXECUTION, name="grpc_runner_method",
        description="Run a method on a gRPC stub with a timeout.")
async def run_method(stub: Any, method_name: str, request: Any, timeout: float) -> Any:
    """
    Invoke a method on a gRPC stub with a timeout; audit all outcomes.
    """
    method_to_call = getattr(stub, method_name)
    audit_logger.log_event("grpc_call_start",
                           method=method_name,
                           request_type=type(request).__name__,
                           timeout=timeout,
                           request_summary=scrub_sensitive_data(str(request)[:200]))
    try:
        response = await asyncio.wait_for(method_to_call(request), timeout=timeout)
        audit_logger.log_event("grpc_call_success",
                               method=method_name,
                               response_type=type(response).__name__,
                               response_summary=scrub_sensitive_data(str(response)[:200]))
        return response
    except grpc.aio.AioRpcError as e:
        logger.error(f"gRPC call to {method_name} failed: {e.details()} (Code: {e.code()})")
        audit_logger.log_event("grpc_call_failed",
                               method=method_name,
                               error_code=e.code().name,
                               error_details=e.details(),
                               request_summary=scrub_sensitive_data(str(request)[:200]))
        alert_operator(f"CRITICAL: gRPC call to {method_name} failed: {e.details()} (Code: {e.code().name}).",
                       level="CRITICAL")
        raise
    except asyncio.TimeoutError:
        logger.error(f"gRPC call to {method_name} timed out after {timeout}s.")
        audit_logger.log_event("grpc_call_timeout",
                               method=method_name,
                               timeout=timeout,
                               request_summary=scrub_sensitive_data(str(request)[:200]))
        alert_operator(f"CRITICAL: gRPC call to {method_name} timed out.", level="CRITICAL")
        raise NonCriticalError("gRPC call timeout")
    except Exception as e:
        logger.error(f"Unexpected error during gRPC call to {method_name}: {e}", exc_info=True)
        audit_logger.log_event("grpc_call_error",
                               method=method_name,
                               error=str(e),
                               request_summary=scrub_sensitive_data(str(request)[:200]))
        alert_operator(f"CRITICAL: Unexpected error during gRPC call to {method_name}: {e}.", level="CRITICAL")
        raise

# --- Metrics helper ---
def emit_metric(name: str, value: float, labels: Dict[str, str], metric_type: str = "counter"):
    """
    Emit a metric to the local registry (scrape/push is external to this file).
    """
    try:
        if name == 'plugin_health_status' and metric_type == 'gauge' and PLUGIN_HEALTH_GAUGE:
            PLUGIN_HEALTH_GAUGE.labels(**labels).set(value)
        elif name == 'plugin_operations_total' and metric_type == 'counter' and PLUGIN_OPERATION_COUNTER:
            PLUGIN_OPERATION_COUNTER.labels(**labels).inc(value)
        else:
            logger.warning(f"Unknown metric '{name}' type '{metric_type}'; skipping.")
        audit_logger.log_event("metric_emitted", metric_name=name, value=value, labels=labels, metric_type=metric_type)
    except Exception as e:
        logger.error(f"Failed to emit metric {name}: {e}", exc_info=True)
        alert_operator(f"ERROR: Failed to emit Prometheus metric {name}: {e}.", level="ERROR")

# --- Manifest validation ---
def validate_manifest(manifest_data: Dict[str, Any]) -> None:
    """
    Validate plugin manifest strictly; verify HMAC signature in prod.
    """
    try:
        data_for_hmac = dict(manifest_data)  # shallow copy
        signature = data_for_hmac.pop("signature", None)
        manifest_content_str = json.dumps(data_for_hmac, sort_keys=True, ensure_ascii=False).encode('utf-8')

        manifest_hmac_key = SECRETS_MANAGER.get_secret("MANIFEST_HMAC_KEY", required=PRODUCTION_MODE)
        if not manifest_hmac_key:
            if PRODUCTION_MODE:
                raise AnalyzerCriticalError("Manifest HMAC key is required in PRODUCTION_MODE but not found.")
            else:
                logger.warning("Manifest HMAC key missing. Skipping signature validation in non-prod.")
                PluginManifest(**manifest_data)
                return

        calculated_signature = hmac.new(
            manifest_hmac_key.encode('utf-8'),
            manifest_content_str,
            hashlib.sha256
        ).hexdigest()

        if not signature or not hmac.compare_digest(calculated_signature, signature):
            logger.critical("CRITICAL: Manifest signature mismatch.")
            audit_logger.log_event("manifest_signature_mismatch",
                                   status="failure",
                                   manifest_name=manifest_data.get('name'),
                                   calculated_signature=calculated_signature,
                                   provided_signature=signature)
            raise AnalyzerCriticalError("Manifest signature mismatch")

        PluginManifest(**manifest_data)
        logger.info("Plugin manifest validated successfully.")
        audit_logger.log_event("manifest_validation", status="success", manifest_name=manifest_data.get('name'))
    except ValidationError as e:
        logger.critical(f"CRITICAL: Plugin manifest validation failed: {e}.", exc_info=True)
        audit_logger.log_event("manifest_validation",
                               status="failure",
                               manifest_name=manifest_data.get('name'),
                               error=str(e))
        alert_operator(f"CRITICAL: Plugin manifest validation failed: {e}.", level="CRITICAL")
        raise
    except Exception as e:
        logger.critical(f"CRITICAL: Unexpected error during manifest validation: {e}.", exc_info=True)
        audit_logger.log_event("manifest_validation",
                               status="failure",
                               manifest_name=manifest_data.get('name'),
                               error=str(e))
        alert_operator(f"CRITICAL: Unexpected error during manifest validation: {e}.", level="CRITICAL")
        raise

def list_plugins(directory_path: str) -> List[Dict[str, Any]]:
    """
    List and validate all plugin manifest JSON files in a directory.
    """
    abs_directory_path = os.path.abspath(directory_path)
    if not os.path.isdir(abs_directory_path):
        logger.error(f"Plugin directory does not exist: {abs_directory_path}")
        return []

    valid_plugins: List[Dict[str, Any]] = []
    audit_logger.log_event("list_plugins_start", directory=abs_directory_path)

    for filename in os.listdir(abs_directory_path):
        if not filename.endswith(".json"):
            logger.debug(f"Skipping non-JSON file: {filename}")
            continue

        file_path = os.path.join(abs_directory_path, filename)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)

            validate_manifest(manifest)  # raises if invalid

            if PRODUCTION_MODE and manifest.get("is_demo_plugin", False):
                logger.critical(f"CRITICAL: Demo plugin '{manifest.get('name')}' detected in PRODUCTION_MODE: {file_path}.")
                audit_logger.log_event("demo_plugin_detected_in_prod", file=file_path, plugin_name=manifest.get('name'))
                alert_operator(f"CRITICAL: Demo plugin '{manifest.get('name')}' detected in PRODUCTION_MODE.", level="CRITICAL")
                sys.exit(1)

            valid_plugins.append(manifest)
            logger.info(f"Validated plugin manifest: {filename}")
            audit_logger.log_event("plugin_manifest_validated", file=filename, plugin_name=manifest.get('name'))
        except json.JSONDecodeError:
            logger.warning(f"Failed to decode JSON from manifest: {filename}")
            audit_logger.log_event("plugin_manifest_invalid_json", file=filename)
        except ValidationError as e:
            logger.warning(f"Invalid manifest format in {filename}: {e}")
            audit_logger.log_event("plugin_manifest_validation_failed", file=filename, error=str(e))
        except Exception as e:
            logger.error(f"Error processing manifest {filename}: {e}", exc_info=True)
            audit_logger.log_event("plugin_manifest_processing_error", file=filename, error=str(e))

    audit_logger.log_event("list_plugins_complete", directory=abs_directory_path, valid_plugins_count=len(valid_plugins))
    return valid_plugins

def generate_plugin_docs(manifest: Dict[str, Any], output_path: str) -> None:
    """
    Generate a Markdown documentation file from a plugin manifest.
    """
    name = manifest.get('name', 'Unnamed Plugin')
    content = [
        f"# {name}",
        f"**Version**: `{manifest.get('version', 'N/A')}`",
        f"**Author**: `{manifest.get('author', 'N/A')}`",
        f"**Entrypoint**: `{manifest.get('entrypoint', 'N/A')}`",
        "",
        f"_{manifest.get('description', '')}_"
    ]
    try:
        Path(output_path).write_text("\n".join(content), encoding='utf-8')
        logger.info(f"Generated documentation for {name} at {output_path}")
        audit_logger.log_event("plugin_doc_generated", plugin_name=name, path=output_path)
    except OSError as e:
        logger.error(f"Failed to write documentation to {output_path}: {e}", exc_info=True)
        audit_logger.log_event("plugin_doc_generation_failed", plugin_name=name, path=output_path, error=str(e))
        alert_operator(f"ERROR: Failed to write plugin documentation to {output_path}: {e}.", level="ERROR")
        raise IOError(f"Failed to write documentation to {output_path}: {e}") from e

async def start_prometheus_exporter(address: str, port: int):
    """Start the Prometheus metrics exporter (plaintext HTTP)."""
    try:
        if prometheus_client:
            prometheus_client.start_http_server(port, address, registry=_metrics_registry)
            logger.info(f"Prometheus metrics exporter started on {address}:{port}")
            audit_logger.log_event("prometheus_exporter_started", address=address, port=port)
    except Exception as e:
        logger.critical(f"CRITICAL: Failed to start Prometheus exporter on {address}:{port}: {e}", exc_info=True)
        audit_logger.log_event("prometheus_exporter_failed", address=address, port=port, error=str(e))
        alert_operator(f"CRITICAL: Failed to start Prometheus exporter: {e}.", level="CRITICAL")
        sys.exit(1)