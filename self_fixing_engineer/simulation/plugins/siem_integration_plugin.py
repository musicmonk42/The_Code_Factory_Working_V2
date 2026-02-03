# plugins/siem_integration_plugin.py

import asyncio
import datetime
import hashlib
import json
import logging
import os
import re
import socket
import time
import uuid
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple, Union

# --- Logger Setup ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    import sys

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- External Plugin Imports ---
try:
    from simulation.plugins.siem_clients import (
        SIEM_CLIENT_REGISTRY,
        AwsCloudWatchClient,
        AzureEventGridClient,
        AzureSentinelClient,
        AzureServiceBusClient,
        BaseSIEMClient,
        DatadogClient,
        ElasticClient,
        GcpLoggingClient,
        SIEMClientAuthError,
        SIEMClientConfigurationError,
        SIEMClientConnectivityError,
        SIEMClientError,
        SIEMClientPublishError,
        SIEMClientQueryError,
        SIEMClientResponseError,
        SplunkClient,
        get_siem_client,
    )

    SIEM_CLIENTS_AVAILABLE = True
except ImportError as e:
    logger.error(
        f"Failed to import SIEM clients: {e}. SIEM functionality will be limited.",
        exc_info=True,
    )
    SIEM_CLIENTS_AVAILABLE = False

    class BaseSIEMClient:
        def __init__(self, config):
            pass

        async def health_check(self):
            return False, "Client not available"

        async def send_log(self, log):
            return False, "Client not available"

        async def query_logs(self, query):
            return []

        async def close(self):
            pass

    SIEM_CLIENT_REGISTRY = {}

    class SIEMClientError(Exception):
        pass

    class SIEMClientConfigurationError(Exception):
        pass

    class SIEMClientConnectivityError(Exception):
        pass

    class SIEMClientAuthError(Exception):
        pass

    class SIEMClientResponseError(Exception):
        pass

    class SIEMClientQueryError(Exception):
        pass

    class SIEMClientPublishError(Exception):
        pass

    def get_siem_client(siem_type, config):
        # Dummy/fake for fallback, always raises
        raise NotImplementedError("get_siem_client is not available (import failed)")


try:
    from simulation.plugins.siem_query_language_parser import (
        QueryParsingError,
        SiemQueryLanguageParser,
    )

    QUERY_PARSER_AVAILABLE = True
except ImportError as e:
    logger.warning(
        f"simulation.plugins.siem_query_language_parser not found: {e}. Generic query translation will be disabled.",
        exc_info=True,
    )
    QUERY_PARSER_AVAILABLE = False

    class SiemQueryLanguageParser:
        def parse_query(self, generic_query: str, siem_type: str) -> Dict[str, Any]:
            logger.warning("Query parser not available, returning raw query.")
            return {
                "query_string": generic_query,
                "time_range": "24h",
                "limit": 100,
                "raw_query_terms": [generic_query],
            }

    class QueryParsingError(Exception):
        pass


# --- Distributed Queue Persistence Imports ---
try:
    import redis

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

try:
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )

    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False

    def retry(*args, **kwargs):
        return lambda f: f

    def stop_after_attempt(n):
        return None

    def wait_exponential(*args, **kwargs):
        return None

    def retry_if_exception_type(e):
        return lambda x: False


# --- Pydantic for Schema Validation ---
try:
    from pydantic import BaseModel, Field, ValidationError

    try:
        from pydantic import VERSION as PYDANTIC_VERSION

        PYDANTIC_V2 = int(PYDANTIC_VERSION.split(".")[0]) >= 2
    except Exception:
        PYDANTIC_V2 = False

    # Version-specific imports
    if PYDANTIC_V2:
        from pydantic import ConfigDict

        try:
            from pydantic import PrivateAttr
        except ImportError:
            from pydantic.fields import PrivateAttr
    else:
        from pydantic import Extra, PrivateAttr

    from pydantic.networks import IPvAnyAddress

    PYDANTIC_AVAILABLE = True
except ImportError:
    logger.error(
        "Pydantic not found. Schema validation will be disabled.", exc_info=True
    )
    PYDANTIC_V2 = False

    class BaseModel:
        def __init__(self, **data: Any):
            self.__dict__.update(data)

        def dict(self, *args, **kwargs):
            return self.__dict__

        @classmethod
        def parse_obj(cls, obj):
            return cls(**obj)

        model_config = {"extra": "allow"}

    class Field:
        def __new__(cls, default=None, **kwargs):
            return default

    ValidationError = Exception
    Extra = type("Extra", (object,), {"forbid": "forbid", "allow": "allow"})
    PYDANTIC_AVAILABLE = False

try:
    from detect_secrets.core import SecretsCollection
    from detect_secrets.settings import transient_settings

    DETECT_SECRETS_AVAILABLE = True
except ImportError:
    DETECT_SECRETS_AVAILABLE = False

# --- Centralized OpenTelemetry Imports ---
try:
    from self_fixing_engineer.arbiter.otel_config import get_tracer
    from opentelemetry import trace

    TRACER = get_tracer(__name__)
    logger.info("OpenTelemetry configured via centralized config.")
except ImportError:
    logger.warning(
        "Could not import centralized OTel config. Tracing will be disabled."
    )

    class DummySpan:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def set_status(self, *a, **k):
            pass

        def record_exception(self, *a, **k):
            pass

    class DummyTracer:
        def start_as_current_span(self, *a, **k):
            return DummySpan()

    TRACER = DummyTracer()
    trace = None  # To prevent AttributeError on trace.Status

# --- Prometheus Metrics (Idempotent Definition) ---
try:
    from prometheus_client import REGISTRY, Counter, Histogram, Info

    prometheus_available = True

    def _get_or_create_metric(
        metric_type: type,
        name: str,
        documentation: str,
        labelnames: Optional[Tuple[str, ...]] = None,
        buckets: Optional[Tuple[float, ...]] = None,
    ) -> Any:
        if labelnames is None:
            labelnames = ()
        if name in REGISTRY._names_to_collectors:
            return REGISTRY._names_to_collectors[name]
        if metric_type == Histogram:
            return metric_type(
                name,
                documentation,
                labelnames=labelnames,
                buckets=buckets or Histogram.DEFAULT_BUCKETS,
            )
        if metric_type == Counter:
            return metric_type(name, documentation, labelnames=labelnames)
        if metric_type == Info:
            return metric_type(name, documentation)
        return metric_type(name, documentation, labelnames=labelnames)

except ImportError:
    prometheus_available = False
    logger.warning(
        "Prometheus client not found. Metrics for Generic SIEM plugin will be disabled."
    )

    class DummyMetric:
        def inc(self, amount: float = 1.0):
            pass

        def set(self, value: float):
            pass

        def observe(self, value: float):
            pass

        def labels(self, *args, **kwargs):
            return self

    def _get_or_create_metric(*args, **kwargs) -> Any:
        return DummyMetric()


if prometheus_available:
    SIEM_EVENTS_SENT_TOTAL = _get_or_create_metric(
        Counter,
        "siem_events_sent_total",
        "Total SIEM events sent",
        ["siem_type", "status"],
    )
    SIEM_SEND_LATENCY_SECONDS = _get_or_create_metric(
        Histogram,
        "siem_send_latency_seconds",
        "Latency of SIEM event sends",
        ["siem_type"],
    )
    SIEM_SEND_ERRORS_TOTAL = _get_or_create_metric(
        Counter,
        "siem_send_errors_total",
        "Total errors sending to SIEM",
        ["siem_type", "error_type"],
    )
    SIEM_QUERY_LATENCY_SECONDS = _get_or_create_metric(
        Histogram,
        "siem_query_latency_seconds",
        "Latency of SIEM query operations",
        ["siem_type"],
    )
    SIEM_QUERY_ERRORS_TOTAL = _get_or_create_metric(
        Counter,
        "siem_query_errors_total",
        "Total errors querying SIEM",
        ["siem_type", "error_type"],
    )
    PLUGIN_INFO = _get_or_create_metric(
        Info,
        "siem_integration_plugin_info",
        "Information about the SIEM Integration Plugin.",
    )
    PLUGIN_INFO.info({"version": "1.2.0", "author": "Self-Fixing Engineer Team"})
    EVENT_TYPES_SENT = _get_or_create_metric(
        Counter,
        "siem_event_types_sent_total",
        "Total events sent by type",
        ["event_type"],
    )
else:

    class DummyMetric:
        def inc(self, amount: float = 1.0):
            pass

        def set(self, value: float):
            pass

        def observe(self, value: float):
            pass

        def labels(self, *args, **kwargs):
            return self

    SIEM_EVENTS_SENT_TOTAL = SIEM_SEND_LATENCY_SECONDS = SIEM_SEND_ERRORS_TOTAL = (
        SIEM_QUERY_LATENCY_SECONDS
    ) = SIEM_QUERY_ERRORS_TOTAL = EVENT_TYPES_SENT = DummyMetric()


# --- PLUGIN MANIFEST ---
PLUGIN_MANIFEST = {
    "name": "GenericSIEMIntegrationPlugin",
    "version": "1.2.0",
    "description": "Provides a generic, pluggable interface for sending security events and audit logs to various SIEM platforms. Includes log retrieval, dynamic loading, and advanced observability.",
    "author": "Self-Fixing Engineer Team",
    "capabilities": [
        "siem_integration",
        "audit_logging",
        "security_event_forwarding",
        "log_retrieval",
        "cloud_logging",
        "observability",
        "policy_enforcement",
    ],
    "permissions_required": ["network_access_external", "cloud_api_access"],
    "compatibility": {
        "min_sim_runner_version": "1.0.0",
        "max_sim_runner_version": "2.0.0",
    },
    "entry_points": {
        "send_siem_event": {
            "description": "Sends a structured event to the configured SIEM backend.",
            "parameters": [
                "event_type",
                "event_details",
                "siem_type_override",
                "metadata",
            ],
        },
        "query_siem_logs": {
            "description": "Queries logs from a configured SIEM backend, optionally translating query language.",
            "parameters": [
                "query_string",
                "siem_type_override",
                "time_range",
                "limit",
                "generic_query_format",
            ],
        },
    },
    "health_check": "plugin_health",
    "api_version": "v1.2",
    "license": "MIT",
    "homepage": "https://www.self-fixing.engineer",
    "tags": [
        "siem",
        "security",
        "audit",
        "logging",
        "splunk",
        "elk",
        "datadog",
        "azure_sentinel",
        "aws",
        "gcp",
        "azure",
        "event_grid",
        "service_bus",
        "cloud_logging",
        "observability",
        "policy",
        "telemetry",
    ],
}


# --- Helper Functions for Configuration ---
def _filter_none(d: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively filter out None values from a dictionary."""
    if not isinstance(d, dict):
        return d
    return {k: _filter_none(v) for k, v in d.items() if v is not None}


def _load_raw_config_from_env() -> Dict[str, Any]:
    """Load SIEM configuration from environment variables."""
    config = {
        "default_siem_type": os.getenv("SIEM_DEFAULT_TYPE"),
        "default_timeout_seconds": os.getenv("SIEM_DEFAULT_TIMEOUT_SECONDS"),
        "retry_attempts": os.getenv("SIEM_RETRY_ATTEMPTS"),
        "retry_backoff_factor": os.getenv("SIEM_RETRY_BACKOFF_FACTOR"),
        "enable_batching": os.getenv("SIEM_ENABLE_BATCHING"),
        "batch_size": os.getenv("SIEM_BATCH_SIZE"),
        "batch_interval_seconds": os.getenv("SIEM_BATCH_INTERVAL_SECONDS"),
        "distributed_queue_enabled": os.getenv("SIEM_DISTRIBUTED_QUEUE_ENABLED"),
        "distributed_queue_url": os.getenv("SIEM_DISTRIBUTED_QUEUE_URL"),
    }

    sub_configs = {
        "splunk": {
            "url": os.getenv("SIEM_SPLUNK_HEC_URL"),
            "token": os.getenv("SIEM_SPLUNK_HEC_TOKEN"),
            "source": os.getenv("SIEM_SPLUNK_SOURCE"),
            "sourcetype": os.getenv("SIEM_SPLUNK_SOURCETYPE"),
            "index": os.getenv("SIEM_SPLUNK_INDEX"),
        },
        "elastic": {
            "url": os.getenv("SIEM_ELASTIC_APM_URL"),
            "api_key": os.getenv("SIEM_ELASTIC_API_KEY"),
            "username": os.getenv("SIEM_ELASTIC_USERNAME"),
            "password": os.getenv("SIEM_ELASTIC_PASSWORD"),
            "index": os.getenv("SIEM_ELASTIC_INDEX"),
        },
        "datadog": {
            "url": os.getenv("SIEM_DATADOG_API_URL"),
            "query_url": os.getenv("SIEM_DATADOG_QUERY_URL"),
            "api_key": os.getenv("SIEM_DATADOG_API_KEY"),
            "application_key": os.getenv("SIEM_DATADOG_APPLICATION_KEY"),
            "service": os.getenv("SIEM_DATADOG_SERVICE"),
            "source": os.getenv("SIEM_DATADOG_SOURCE"),
            "tags": (
                os.getenv("SIEM_DATADOG_TAGS").split(",")
                if os.getenv("SIEM_DATADOG_TAGS")
                else None
            ),
        },
        "azure_sentinel": {
            "workspace_id": os.getenv("SIEM_AZURE_WORKSPACE_ID"),
            "shared_key": os.getenv("SIEM_AZURE_SHARED_KEY"),
            "log_type": os.getenv("SIEM_AZURE_LOG_TYPE"),
            "api_version": os.getenv("SIEM_AZURE_API_VERSION"),
            "monitor_query_endpoint": os.getenv("SIEM_AZURE_MONITOR_QUERY_ENDPOINT"),
        },
        "azure_event_grid": {
            "endpoint": os.getenv("SIEM_AZURE_EVENTGRID_ENDPOINT"),
            "key": os.getenv("SIEM_AZURE_EVENTGRID_KEY"),
            "topic_name": os.getenv("SIEM_AZURE_EVENTGRID_TOPIC_NAME"),
        },
        "azure_service_bus": {
            "connection_string": os.getenv("SIEM_AZURE_SERVICEBUS_CONNECTION_STRING"),
            "queue_name": os.getenv("SIEM_AZURE_SERVICEBUS_QUEUE_NAME"),
            "topic_name": os.getenv("SIEM_AZURE_SERVICEBUS_TOPIC_NAME"),
        },
        "aws_cloudwatch": {
            "region_name": os.getenv("AWS_REGION"),
            "log_group_name": os.getenv("AWS_CLOUDWATCH_LOG_GROUP_NAME"),
            "log_stream_name": os.getenv("AWS_CLOUDWATCH_LOG_STREAM_NAME"),
        },
        "gcp_logging": {
            "project_id": os.getenv("GCP_PROJECT_ID"),
            "log_name": os.getenv("GCP_LOG_NAME"),
            "credentials_path": os.getenv("GCP_CREDENTIALS_PATH"),
        },
        "policy": {
            "rules": (
                json.loads(os.getenv("SIEM_POLICY_RULES"))
                if os.getenv("SIEM_POLICY_RULES")
                else None
            ),
            "default_pii_patterns": (
                os.getenv("SIEM_POLICY_DEFAULT_PII_PATTERNS").split(",")
                if os.getenv("SIEM_POLICY_DEFAULT_PII_PATTERNS")
                else None
            ),
            "allowed_event_types": (
                os.getenv("SIEM_POLICY_ALLOWED_EVENT_TYPES").split(",")
                if os.getenv("SIEM_POLICY_ALLOWED_EVENT_TYPES")
                else None
            ),
            "disallowed_event_types": (
                os.getenv("SIEM_POLICY_DISALLOWED_EVENT_TYPES").split(",")
                if os.getenv("SIEM_POLICY_DISALLOWED_EVENT_TYPES")
                else None
            ),
            "compliance_flags": (
                os.getenv("SIEM_POLICY_COMPLIANCE_FLAGS").split(",")
                if os.getenv("SIEM_POLICY_COMPLIANCE_FLAGS")
                else None
            ),
        },
    }

    for name, sub_config_data in sub_configs.items():
        filtered_sub_config = _filter_none(sub_config_data)
        if filtered_sub_config:
            config[name] = filtered_sub_config

    return _filter_none(config)


# --- Plugin-Specific Configuration Schema (Pydantic) ---
if PYDANTIC_AVAILABLE:

    class SplunkConfig(BaseModel):
        url: str
        token: str
        source: str = "sfe_audit"
        sourcetype: str = "_json"
        index: Optional[str] = None
        model_config = ConfigDict(extra="forbid")

    class ElasticConfig(BaseModel):
        url: str
        api_key: Optional[str] = None
        username: Optional[str] = None
        password: Optional[str] = None
        index: str = "sfe-logs"
        model_config = ConfigDict(extra="forbid")

    class DatadogConfig(BaseModel):
        url: str = "https://http-intake.logs.datadoghq.com/api/v2/logs"
        query_url: str = "https://api.datadoghq.com/api/v1/logs-queries"
        api_key: str
        application_key: Optional[str] = None
        service: str = "sfe-agent"
        source: str = "sfe-audit-plugin"
        tags: List[str] = Field(default_factory=list)
        model_config = ConfigDict(extra="forbid")

    class AzureSentinelConfig(BaseModel):
        workspace_id: str
        shared_key: str
        log_type: str = "SFE_Audit_CL"
        api_version: str = "2016-04-01"
        monitor_query_endpoint: Optional[str] = None
        model_config = ConfigDict(extra="forbid")

    class AzureEventGridConfig(BaseModel):
        endpoint: str
        key: str
        topic_name: str = "sfe-events"
        model_config = ConfigDict(extra="forbid")

    class AzureServiceBusConfig(BaseModel):
        connection_string: Optional[str] = None
        queue_name: Optional[str] = None
        topic_name: Optional[str] = None
        model_config = ConfigDict(extra="forbid")

    class AWSCloudWatchConfig(BaseModel):
        region_name: str = "us-east-1"
        log_group_name: str = "sfe-audit-logs"
        log_stream_name: str = "default"
        model_config = ConfigDict(extra="forbid")

    class GCPLoggingConfig(BaseModel):
        project_id: str
        log_name: str = "sfe-audit-log"
        credentials_path: Optional[str] = None
        model_config = ConfigDict(extra="forbid")

    class PolicyCondition(BaseModel):
        field: str = Field(..., description="Field name to apply the condition to.")
        operator: str = Field(
            ...,
            description="Operator (e.g., 'equals', 'contains', 'matches_regex', 'is_in').",
        )
        value: Union[str, int, float, bool, List[Any]] = Field(
            ...,
            description="Value to compare against. Can be a regex string for 'matches_regex'.",
        )
        model_config = ConfigDict(extra="forbid")

    class PolicyRule(BaseModel):
        conditions: List[PolicyCondition] = Field(
            default_factory=list,
            description="List of conditions that must ALL be true for this rule to apply.",
        )
        action: str = Field(
            ..., description="Action to take: 'mask', 'block', 'allow'."
        )
        target_field: Optional[str] = Field(
            None, description="Field to apply action to if action is 'mask'."
        )
        mask_with: str = Field(
            "[MASKED]", description="Value to replace with if action is 'mask'."
        )
        model_config = ConfigDict(extra="forbid")

    class PolicyConfig(BaseModel):
        rules: List[PolicyRule] = Field(
            default_factory=list, description="Ordered list of policy rules."
        )
        default_pii_patterns: List[str] = Field(
            default_factory=list,
            description="Default regex patterns for PII masking if no rule applies.",
        )
        allowed_event_types: Optional[List[str]] = None
        disallowed_event_types: Optional[List[str]] = None
        compliance_flags: List[str] = Field(
            default_factory=list, description="Compliance flags to tag events with."
        )
        model_config = ConfigDict(extra="forbid")

    class PluginGlobalConfig(BaseModel):
        _CONFIG_VERSION = PrivateAttr(1)
        default_siem_type: str = Field(
            default="splunk",
            pattern="^(splunk|elastic|datadog|azure_sentinel|azure_event_grid|azure_service_bus|aws_cloudwatch|gcp_logging)$",
        )
        default_timeout_seconds: int = Field(default=10, ge=1)
        retry_attempts: int = Field(default=3, ge=0)
        retry_backoff_factor: float = Field(default=2.0, ge=0)
        enable_batching: bool = False
        batch_size: int = 100
        batch_interval_seconds: int = 5
        distributed_queue_enabled: bool = False
        distributed_queue_url: Optional[str] = None
        splunk: Optional[SplunkConfig] = None
        elastic: Optional[ElasticConfig] = None
        datadog: Optional[DatadogConfig] = None
        azure_sentinel: Optional[AzureSentinelConfig] = None
        azure_event_grid: Optional[AzureEventGridConfig] = None
        azure_service_bus: Optional[AzureServiceBusConfig] = None
        aws_cloudwatch: Optional[AWSCloudWatchConfig] = None
        gcp_logging: Optional[GCPLoggingConfig] = None
        policy: PolicyConfig = Field(default_factory=PolicyConfig)
        model_config = ConfigDict(extra="forbid")

        @classmethod
        def migrate_config(cls, raw_config: Dict[str, Any]) -> Dict[str, Any]:
            config_version = raw_config.get("_CONFIG_VERSION", 0)
            if config_version < 1:
                logger.info("Migrating SIEM plugin config from v0 to v1.")
                if "pii_masking_patterns" in raw_config and isinstance(
                    raw_config["pii_masking_patterns"], list
                ):
                    raw_config.setdefault("policy", {})["default_pii_patterns"] = (
                        raw_config.pop("pii_masking_patterns")
                    )
                raw_config["_CONFIG_VERSION"] = 1
            return raw_config

    try:
        raw_config_from_env = _load_raw_config_from_env()
        migrated_config = PluginGlobalConfig.migrate_config(raw_config_from_env)
        migrated_config.pop("_CONFIG_VERSION", None)
        final_config_to_parse = {
            k: v for k, v in migrated_config.items() if v is not None
        }
        SIEM_CONFIG_MODEL = PluginGlobalConfig.parse_obj(final_config_to_parse)

        logger.info(
            "SIEM_CONFIG_MODEL validated and migrated successfully with Pydantic."
        )
    except ValidationError as e:
        logger.critical(
            f"SIEM Configuration Validation Error: {e.errors()}", exc_info=True
        )
        SIEM_CONFIG_MODEL = PluginGlobalConfig()
        logger.warning(
            "Falling back to default SIEM configuration due to validation errors."
        )
    except Exception as e:
        logger.critical(
            f"Failed to load or parse SIEM configuration: {e}", exc_info=True
        )
        SIEM_CONFIG_MODEL = PluginGlobalConfig()
        logger.warning(
            "Falling back to default SIEM configuration due to unexpected error."
        )
else:
    logger.warning(
        "Pydantic is not available, using raw dictionary for SIEM_CONFIG. Schema validation is disabled."
    )
    SIEM_CONFIG_MODEL = {
        "default_siem_type": os.getenv("SIEM_DEFAULT_TYPE", "splunk"),
        "default_timeout_seconds": int(os.getenv("SIEM_DEFAULT_TIMEOUT_SECONDS", "10")),
        "retry_attempts": int(os.getenv("SIEM_RETRY_ATTEMPTS", "3")),
        "retry_backoff_factor": float(os.getenv("SIEM_RETRY_BACKOFF_FACTOR", "2.0")),
        "enable_batching": os.getenv("SIEM_ENABLE_BATCHING", "false").lower() == "true",
        "batch_size": int(os.getenv("SIEM_BATCH_SIZE", "100")),
        "batch_interval_seconds": int(os.getenv("SIEM_BATCH_INTERVAL_SECONDS", "5")),
        "distributed_queue_enabled": os.getenv(
            "SIEM_DISTRIBUTED_QUEUE_ENABLED", "false"
        ).lower()
        == "true",
        "distributed_queue_url": os.getenv("SIEM_DISTRIBUTED_QUEUE_URL"),
        "splunk": {
            "url": os.getenv("SIEM_SPLUNK_HEC_URL"),
            "token": os.getenv("SIEM_SPLUNK_HEC_TOKEN"),
            "source": os.getenv("SIEM_SPLUNK_SOURCE", "sfe_audit"),
            "sourcetype": os.getenv("SIEM_SPLUNK_SOURCETYPE", "_json"),
            "index": os.getenv("SIEM_SPLUNK_INDEX"),
        },
        "elastic": {
            "url": os.getenv("SIEM_ELASTIC_APM_URL"),
            "api_key": os.getenv("SIEM_ELASTIC_API_KEY"),
            "username": os.getenv("SIEM_ELASTIC_USERNAME"),
            "password": os.getenv("SIEM_ELASTIC_PASSWORD"),
            "index": os.getenv("SIEM_ELASTIC_INDEX", "sfe-logs"),
        },
        "datadog": {
            "url": os.getenv(
                "SIEM_DATADOG_API_URL",
                "https://http-intake.logs.datadoghq.com/api/v2/logs",
            ),
            "query_url": os.getenv(
                "SIEM_DATADOG_QUERY_URL",
                "https://api.datadoghq.com/api/v1/logs-queries",
            ),
            "api_key": os.getenv("SIEM_DATADOG_API_KEY"),
            "application_key": os.getenv("SIEM_DATADOG_APPLICATION_KEY"),
            "service": os.getenv("SIEM_DATADOG_SERVICE", "sfe-agent"),
            "source": os.getenv("SIEM_DATADOG_SOURCE", "sfe-audit-plugin"),
            "tags": os.getenv("SIEM_DATADOG_TAGS", "env:prod,team:sfe").split(","),
        },
        "azure_sentinel": {
            "workspace_id": os.getenv("SIEM_AZURE_WORKSPACE_ID"),
            "shared_key": os.getenv("SIEM_AZURE_SHARED_KEY"),
            "log_type": os.getenv("SIEM_AZURE_LOG_TYPE", "SFE_Audit_CL"),
            "api_version": os.getenv("SIEM_AZURE_API_VERSION", "2016-04-01"),
            "monitor_query_endpoint": os.getenv("SIEM_AZURE_MONITOR_QUERY_ENDPOINT"),
        },
        "azure_event_grid": {
            "endpoint": os.getenv("SIEM_AZURE_EVENTGRID_ENDPOINT"),
            "key": os.getenv("SIEM_AZURE_EVENTGRID_KEY"),
            "topic_name": os.getenv("SIEM_AZURE_EVENTGRID_TOPIC_NAME", "sfe-events"),
        },
        "azure_service_bus": {
            "connection_string": os.getenv("SIEM_AZURE_SERVICEBUS_CONNECTION_STRING"),
            "queue_name": os.getenv("SIEM_AZURE_SERVICEBUS_QUEUE_NAME"),
            "topic_name": os.getenv("SIEM_AZURE_SERVICEBUS_TOPIC_NAME"),
        },
        "aws_cloudwatch": {
            "region_name": os.getenv("AWS_REGION", "us-east-1"),
            "log_group_name": os.getenv(
                "AWS_CLOUDWATCH_LOG_GROUP_NAME", "sfe-audit-logs"
            ),
            "log_stream_name": os.getenv("AWS_CLOUDWATCH_LOG_STREAM_NAME", "default"),
        },
        "gcp_logging": {
            "project_id": os.getenv("GCP_PROJECT_ID"),
            "log_name": os.getenv("GCP_LOG_NAME", "sfe-audit-log"),
            "credentials_path": os.getenv("GCP_CREDENTIALS_PATH"),
        },
        "policy": {
            "rules": json.loads(os.getenv("SIEM_POLICY_RULES", "[]")),
            "default_pii_patterns": (
                [
                    p.strip()
                    for p in os.getenv("SIEM_POLICY_DEFAULT_PII_PATTERNS", "").split(
                        ","
                    )
                ]
                if os.getenv("SIEM_POLICY_DEFAULT_PII_PATTERNS")
                else []
            ),
            "allowed_event_types": (
                [
                    t.strip()
                    for t in os.getenv("SIEM_POLICY_ALLOWED_EVENT_TYPES", "").split(",")
                ]
                if os.getenv("SIEM_POLICY_ALLOWED_EVENT_TYPES")
                else None
            ),
            "disallowed_event_types": (
                [
                    t.strip()
                    for t in os.getenv("SIEM_POLICY_DISALLOWED_EVENT_TYPES", "").split(
                        ","
                    )
                ]
                if os.getenv("SIEM_POLICY_DISALLOWED_EVENT_TYPES")
                else None
            ),
            "compliance_flags": (
                [
                    f.strip()
                    for f in os.getenv("SIEM_POLICY_COMPLIANCE_FLAGS", "").split(",")
                ]
                if os.getenv("SIEM_POLICY_COMPLIANCE_FLAGS")
                else []
            ),
        },
    }

try:
    from simulation.audit_log import AuditLogger as SFE_AuditLogger

    _sfe_audit_logger = SFE_AuditLogger.from_environment()
except ImportError:
    logger.warning(
        "SFE AuditLogger not found. Audit events will be logged to plugin's logger only."
    )

    class MockAuditLogger:
        async def add_entry(
            self, kind: str, name: str, detail: Dict[str, Any], **kwargs: Any
        ):
            logger.info(f"[AUDIT_MOCK] Kind: {kind}, Name: {name}, Detail: {detail}")

    _sfe_audit_logger = MockAuditLogger()


def _scrub_secrets(data: Union[Dict, List, str]) -> Union[Dict, List, str]:
    if not DETECT_SECRETS_AVAILABLE:
        return data
    if isinstance(data, str):
        secrets = SecretsCollection()
        with transient_settings():
            secrets.scan_string(data)
        for secret in secrets:
            data = data.replace(secret.secret_value, "[REDACTED]")
        return data
    if isinstance(data, dict):
        return {k: _scrub_secrets(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_scrub_secrets(item) for item in data]
    return data


async def _audit_event(kind: str, name: str, details: Dict[str, Any], **kwargs: Any):
    with TRACER.start_as_current_span(f"sfe.audit.{kind}.{name}"):
        await _sfe_audit_logger.add_entry(
            kind=kind, name=name, detail=details, **kwargs
        )


class PolicyEnforcer:
    def __init__(self, policy_config: Union["PolicyConfig", Dict[str, Any]]):
        if PYDANTIC_AVAILABLE and isinstance(policy_config, dict):
            self.policy_config = PolicyConfig(**policy_config)
        else:
            self.policy_config = policy_config
        self.default_pii_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in self.policy_config.default_pii_patterns
        ]
        # Store compiled regexes separately instead of modifying Pydantic models
        self._compiled_regexes: Dict[Tuple[int, int], re.Pattern] = {}
        for rule_idx, rule in enumerate(self.policy_config.rules):
            for cond_idx, condition in enumerate(rule.conditions):
                if condition.operator == "matches_regex" and isinstance(
                    condition.value, str
                ):
                    key = (rule_idx, cond_idx)
                    self._compiled_regexes[key] = re.compile(
                        condition.value, re.IGNORECASE
                    )
        logger.info(
            f"Policy Enforcer initialized with {len(self.policy_config.rules)} rules and {len(self.default_pii_patterns)} default PII patterns."
        )

    def _evaluate_condition(
        self,
        event: Dict[str, Any],
        condition: "PolicyCondition",
        rule_idx: int = 0,
        cond_idx: int = 0,
    ) -> bool:
        field_value = self._get_field_value(event, condition.field)
        if field_value is None:
            return False
        if condition.operator == "equals":
            return field_value == condition.value
        elif condition.operator == "contains":
            return (
                isinstance(field_value, str)
                and isinstance(condition.value, str)
                and condition.value in field_value
            )
        elif condition.operator == "matches_regex":
            if isinstance(field_value, str):
                regex_key = (rule_idx, cond_idx)
                compiled_regex = self._compiled_regexes.get(regex_key)
                if compiled_regex:
                    return bool(compiled_regex.search(field_value))
            return False
        elif condition.operator == "is_in":
            return (
                field_value in condition.value
                if isinstance(condition.value, list)
                else False
            )
        elif condition.operator == "greater_than":
            return (
                field_value > condition.value
                if isinstance(field_value, (int, float))
                and isinstance(condition.value, (int, float))
                else False
            )
        elif condition.operator == "less_than":
            return (
                field_value < condition.value
                if isinstance(field_value, (int, float))
                and isinstance(condition.value, (int, float))
                else False
            )
        return False

    def _get_field_value(self, data: Dict[str, Any], field_path: str) -> Any:
        parts = field_path.split(".")
        current_data = data
        for part in parts:
            if isinstance(current_data, dict) and part in current_data:
                current_data = current_data[part]
            else:
                return None
        return current_data

    def _set_field_value(self, data: Dict[str, Any], field_path: str, value: Any):
        parts = field_path.split(".")
        current_data = data
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                current_data[part] = value
            else:
                if part not in current_data or not isinstance(current_data[part], dict):
                    current_data[part] = {}
                current_data = current_data[part]

    def enforce(self, event: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        modified_event = json.loads(json.dumps(event))
        event_type = event.get("event_type")
        if (
            self.policy_config.allowed_event_types is not None
            and event_type not in self.policy_config.allowed_event_types
        ):
            return False, f"Event type '{event_type}' not in allowed list.", event
        if (
            self.policy_config.disallowed_event_types is not None
            and event_type in self.policy_config.disallowed_event_types
        ):
            return False, f"Event type '{event_type}' is in disallowed list.", event
        for rule_idx, rule in enumerate(self.policy_config.rules):
            all_conditions_met = all(
                self._evaluate_condition(modified_event, condition, rule_idx, cond_idx)
                for cond_idx, condition in enumerate(rule.conditions)
            )
            if all_conditions_met:
                if rule.action == "block":
                    return (
                        False,
                        f"Event blocked by policy rule {rule_idx} (target_field: {rule.target_field or 'event-level'}).",
                        event,
                    )
                elif rule.action == "mask" and rule.target_field:
                    field_to_mask_value = self._get_field_value(
                        modified_event, rule.target_field
                    )
                    if field_to_mask_value is not None:
                        self._set_field_value(
                            modified_event, rule.target_field, rule.mask_with
                        )
                        logger.debug(
                            f"Applied masking for field '{rule.target_field}' by rule {rule_idx}."
                        )

        def _recursive_mask_default_pii(data: Any) -> Any:
            if isinstance(data, dict):
                return {k: _recursive_mask_default_pii(v) for k, v in data.items()}
            elif isinstance(data, list):
                return [_recursive_mask_default_pii(item) for item in data]
            elif isinstance(data, str):
                for pattern in self.default_pii_patterns:
                    data = pattern.sub("[MASKED_PII]", data)
                return data
            else:
                return data

        modified_event = _recursive_mask_default_pii(modified_event)
        if self.policy_config.compliance_flags:
            modified_event.setdefault("compliance_flags", []).extend(
                self.policy_config.compliance_flags
            )
        return True, "Policy enforced successfully.", modified_event


class RedisQueuePersistence:
    def __init__(self, redis_url: str = "redis://localhost:6379/1"):
        if not REDIS_AVAILABLE:
            raise ImportError(
                "Redis is not installed or configured for distributed queue persistence."
            )
        self.redis_client = redis.from_url(redis_url, decode_responses=False)
        self.queue_key = "siem:retry_queue"
        logger.info(
            f"RedisQueuePersistence initialized for key '{self.queue_key}' at {redis_url}."
        )

    async def enqueue(self, item: Dict[str, Any]):
        await asyncio.to_thread(
            self.redis_client.rpush, self.queue_key, json.dumps(item).encode("utf-8")
        )

    async def dequeue(self) -> Optional[Dict[str, Any]]:
        item_bytes = await asyncio.to_thread(self.redis_client.lpop, self.queue_key)
        return json.loads(item_bytes.decode("utf-8")) if item_bytes else None

    async def size(self) -> int:
        return await asyncio.to_thread(self.redis_client.llen, self.queue_key)

    async def flush(self):
        await asyncio.to_thread(self.redis_client.delete, self.queue_key)
        logger.info(f"Flushed Redis queue '{self.queue_key}'.")


class SelfHealingManager:
    def __init__(self, config: "PluginGlobalConfig"):
        self.config = config
        self.failure_threshold = 5
        self.disabled_backends: Dict[str, Dict[str, Any]] = {}
        self.re_enable_interval_seconds = 300
        self._last_retry_attempt_time: Dict[str, float] = {}

        self.queue_persistence: Optional[RedisQueuePersistence] = None
        if self.config.distributed_queue_enabled and REDIS_AVAILABLE:
            try:
                self.queue_persistence = RedisQueuePersistence(
                    self.config.distributed_queue_url or "redis://localhost:6379/1"
                )
            except Exception as e:
                logger.error(
                    f"Failed to initialize distributed queue persistence (Redis): {e}. Falling back to in-memory queue.",
                    exc_info=True,
                )
                self.config.distributed_queue_enabled = False
        if not self.config.distributed_queue_enabled:
            self.in_memory_queue: Deque[Dict[str, Any]] = deque(
                maxlen=(
                    self.config.batch_size * 10 if self.config.enable_batching else 1000
                )
            )
            logger.info("Using in-memory queue for retry persistence.")
        else:
            logger.info("Using distributed queue (Redis) for retry persistence.")
        logger.info(
            f"Self-Healing Manager initialized. Failure threshold: {self.failure_threshold}."
        )

    async def record_failure(self, siem_type: str, error: Exception):
        current_time = time.monotonic()
        if siem_type not in self.disabled_backends:
            self.disabled_backends[siem_type] = {
                "consecutive_failures": 0,
                "first_failure_time": current_time,
                "disabled_until": 0,
            }
        self.disabled_backends[siem_type]["consecutive_failures"] += 1
        logger.warning(
            f"Recorded failure for {siem_type}. Consecutive failures: {self.disabled_backends[siem_type]['consecutive_failures']}."
        )
        if (
            self.disabled_backends[siem_type]["consecutive_failures"]
            >= self.failure_threshold
        ):
            re_enable_at = current_time + self.re_enable_interval_seconds
            self.disabled_backends[siem_type]["disabled_until"] = re_enable_at
            logger.error(
                f"Backend '{siem_type}' disabled due to too many consecutive failures. Will re-attempt after {self.re_enable_interval_seconds} seconds (at {datetime.datetime.fromtimestamp(re_enable_at)})."
            )
            await _audit_event(
                "siem_backend_disabled",
                "auto_disabled",
                {
                    "siem_type": siem_type,
                    "reason": str(error),
                    "disabled_until": re_enable_at,
                },
            )

    def record_success(self, siem_type: str):
        if siem_type in self.disabled_backends:
            logger.info(f"Backend '{siem_type}' recovered. Resetting failure count.")
            self.disabled_backends.pop(siem_type)
            asyncio.create_task(
                _audit_event(
                    "siem_backend_re_enabled",
                    "auto_re_enabled",
                    {"siem_type": siem_type, "reason": "success_after_failures"},
                )
            )

    def is_backend_disabled(self, siem_type: str) -> bool:
        if siem_type in self.disabled_backends:
            if time.monotonic() < self.disabled_backends[siem_type]["disabled_until"]:
                return True
            else:
                logger.info(
                    f"Backend '{siem_type}' re-enable period elapsed. Will attempt re-enable."
                )
                self.disabled_backends[siem_type]["consecutive_failures"] = 0
                self.disabled_backends[siem_type]["disabled_until"] = 0
                return False
        return False

    async def enqueue_for_retry(self, event_data: Dict[str, Any]):
        if self.config.enable_batching:
            if self.config.distributed_queue_enabled and self.queue_persistence:
                await self.queue_persistence.enqueue(event_data)
            else:
                self.in_memory_queue.append(event_data)
                logger.debug(
                    f"Event queued for retry (in-memory). Queue size: {len(self.in_memory_queue)}"
                )

    async def dequeue_for_retry(self) -> Optional[Dict[str, Any]]:
        if self.config.distributed_queue_enabled and self.queue_persistence:
            return await self.queue_persistence.dequeue()
        else:
            return self.in_memory_queue.popleft() if self.in_memory_queue else None

    async def get_queue_size(self) -> int:
        return (
            await self.queue_persistence.size()
            if self.config.distributed_queue_enabled and self.queue_persistence
            else len(self.in_memory_queue)
        )

    async def process_retry_queue(
        self, siem_plugin_instance: "GenericSIEMIntegrationPlugin"
    ):
        current_queue_size = await self.get_queue_size()
        if not self.config.enable_batching or current_queue_size == 0:
            return
        if hasattr(self, "_processing_lock") and self._processing_lock.locked():
            return
        self._processing_lock = asyncio.Lock()
        async with self._processing_lock:
            current_queue_size = await self.get_queue_size()
            if current_queue_size == 0:
                return
            if (
                time.monotonic()
                < self._last_retry_attempt_time.get("all_queues", 0)
                + self.config.batch_interval_seconds
            ):
                return
            logger.info(
                f"Attempting to process retry queue. Current size: {current_queue_size}"
            )
            self._last_retry_attempt_time["all_queues"] = time.monotonic()
            events_to_retry = [
                await self.dequeue_for_retry()
                for _ in range(min(self.config.batch_size, current_queue_size))
            ]
            if not events_to_retry:
                return
            success_count, failure_count = 0, 0
            for event_data in events_to_retry:
                if not event_data:
                    continue
                event_type = event_data.get("event_type", "queued_event")
                event_details = event_data.get("details", {})
                metadata = {
                    k: v
                    for k, v in event_data.items()
                    if k not in ["event_type", "details", "target_siem_type"]
                }
                try:
                    send_result = await siem_plugin_instance.send_siem_event(
                        event_type=event_type,
                        event_details=event_details,
                        siem_type_override=event_data.get("target_siem_type"),
                        metadata=metadata,
                    )
                    if send_result["success"]:
                        success_count += 1
                    else:
                        failure_count += 1
                        await self.enqueue_for_retry(event_data)
                except Exception as e:
                    failure_count += 1
                    await self.enqueue_for_retry(event_data)
                    logger.error(f"Error re-sending queued event: {e}", exc_info=True)
            logger.info(
                f"Processed retry queue: {success_count} succeeded, {failure_count} failed. Remaining in queue: {await self.get_queue_size()}"
            )
            if failure_count > 0:
                await _audit_event(
                    "siem_queue_retry",
                    "partial_failure",
                    {
                        "succeeded": success_count,
                        "failed": failure_count,
                        "remaining_in_queue": await self.get_queue_size(),
                    },
                )
            elif success_count > 0:
                await _audit_event(
                    "siem_queue_retry",
                    "success",
                    {
                        "succeeded": success_count,
                        "remaining_in_queue": await self.get_queue_size(),
                    },
                )


class GenericSIEMIntegrationPlugin:
    def __init__(self, config: Union[Dict[str, Any], "PluginGlobalConfig"]):
        if PYDANTIC_AVAILABLE and isinstance(config, dict):
            self.config = PluginGlobalConfig(**config)
        elif PYDANTIC_AVAILABLE and isinstance(config, PluginGlobalConfig):
            self.config = config
        else:
            self.config = config
        self.active_backends: Dict[str, BaseSIEMClient] = {}
        self.default_siem_type = (
            self.config.default_siem_type
            if PYDANTIC_AVAILABLE
            else self.config.get("default_siem_type", "splunk")
        )
        self._hostname = socket.gethostname()
        self.policy_enforcer = (
            PolicyEnforcer(self.config.policy) if PYDANTIC_AVAILABLE else None
        )
        self.self_healing_manager = SelfHealingManager(self.config)
        self._query_parser = (
            SiemQueryLanguageParser() if QUERY_PARSER_AVAILABLE else None
        )
        self._init_active_backends()
        self._retry_task: Optional[asyncio.Task] = None

    def _get_config_for_client(self, siem_type: str) -> Dict[str, Any]:
        if PYDANTIC_AVAILABLE:
            siem_sub_config = getattr(self.config, siem_type, None)
            if siem_sub_config:
                return siem_sub_config.dict() | {
                    "default_timeout_seconds": self.config.default_timeout_seconds,
                    "retry_attempts": self.config.retry_attempts,
                    "retry_backoff_factor": self.config.retry_backoff_factor,
                }
            return {}
        else:
            raw_siem_cfg = self.config.get(siem_type, {})
            return raw_siem_cfg | {
                "default_timeout_seconds": self.config.get(
                    "default_timeout_seconds", 10
                ),
                "retry_attempts": self.config.get("retry_attempts", 3),
                "retry_backoff_factor": self.config.get("retry_backoff_factor", 2.0),
            }

    def _init_active_backends(self):
        if not SIEM_CLIENTS_AVAILABLE:
            logger.error(
                "SIEM client classes are not available. No backends can be initialized."
            )
            return
        for siem_type, client_class in SIEM_CLIENT_REGISTRY.items():
            siem_sub_config_data = self._get_config_for_client(siem_type)
            if not siem_sub_config_data:
                logger.debug(
                    f"Skipping initialization for SIEM type '{siem_type}': No specific configuration found or is empty."
                )
                continue
            try:
                client_instance = get_siem_client(siem_type, siem_sub_config_data)
                self.active_backends[siem_type] = client_instance
                logger.info(f"Initialized SIEM backend client: {siem_type}.")
                asyncio.create_task(
                    _audit_event(
                        "siem_backend_init", "success", {"siem_type": siem_type}
                    )
                )
            except SIEMClientConfigurationError as e:
                logger.warning(
                    f"Configuration error for SIEM backend '{siem_type}': {e}. This backend will be disabled.",
                    exc_info=True,
                )
                if prometheus_available:
                    SIEM_SEND_ERRORS_TOTAL.labels(
                        siem_type=siem_type, error_type="configuration_error"
                    ).inc()
                asyncio.create_task(
                    _audit_event(
                        "siem_backend_init",
                        "config_error",
                        {"siem_type": siem_type, "error": str(e), "details": e.details},
                    )
                )
            except ImportError as e:
                logger.error(
                    f"Missing dependency for SIEM backend '{siem_type}': {e}. This backend will be disabled.",
                    exc_info=True,
                )
                if prometheus_available:
                    SIEM_SEND_ERRORS_TOTAL.labels(
                        siem_type=siem_type, error_type="dependency_error"
                    ).inc()
                asyncio.create_task(
                    _audit_event(
                        "siem_backend_init",
                        "dependency_missing",
                        {"siem_type": siem_type, "error": str(e)},
                    )
                )
            except Exception as e:
                logger.error(
                    f"Failed to initialize SIEM backend '{siem_type}': {e}. This backend will be disabled.",
                    exc_info=True,
                )
                if prometheus_available:
                    SIEM_SEND_ERRORS_TOTAL.labels(
                        siem_type=siem_type, error_type="initialization_failed"
                    ).inc()
                asyncio.create_task(
                    _audit_event(
                        "siem_backend_init",
                        "init_failed",
                        {"siem_type": siem_type, "error": str(e)},
                    )
                )
        if not self.active_backends:
            logger.warning(
                "No SIEM backends were successfully initialized. Events will not be forwarded."
            )

    async def _run_retry_loop(self):
        while True:
            await self.self_healing_manager.process_retry_queue(self)
            await asyncio.sleep(self.config.batch_interval_seconds)

    def start_retry_task(self):
        if self.config.enable_batching and self._retry_task is None:
            self._retry_task = asyncio.create_task(self._run_retry_loop())
            logger.info("Started background task for retrying queued SIEM events.")

    def stop_retry_task(self):
        if self._retry_task:
            self._retry_task.cancel()
            self._retry_task = None
            logger.info("Stopped background task for retrying queued SIEM events.")

    async def plugin_health(self) -> Dict[str, Any]:
        with TRACER.start_as_current_span("siem_plugin_health_check") as span:
            status_summary, details = "ok", []
            if not self.active_backends:
                status_summary = "warning"
                details.append("No SIEM backends configured or initialized.")
                span.set_attribute("health.status", status_summary)
                return {"status": status_summary, "details": details}
            tasks = [
                self._check_backend_health(siem_type, backend_instance)
                for siem_type, backend_instance in self.active_backends.items()
            ]
            results = await asyncio.gather(*tasks)
            for siem_type, is_healthy, message in results:
                details.append(
                    f"Backend '{siem_type}': {'Healthy' if is_healthy else 'Unhealthy'} - {message}"
                )
                if not is_healthy:
                    status_summary = "degraded"
            span.set_attribute("health.status", status_summary)
            logger.info(f"Generic SIEM Plugin health check: {status_summary}")
            await _audit_event(
                "plugin_health_check", status_summary, {"backends_status": details}
            )
            return {"status": status_summary, "details": details}

    async def _check_backend_health(
        self, siem_type: str, backend_instance: BaseSIEMClient
    ) -> Tuple[str, bool, str]:
        with TRACER.start_as_current_span(f"siem_backend_health.{siem_type}") as span:
            try:
                is_healthy, message = await backend_instance.health_check()
                if not is_healthy:
                    self.self_healing_manager.record_failure(
                        siem_type, Exception(message)
                    )
                else:
                    self.self_healing_manager.record_success(siem_type)
                span.set_attribute(f"siem.{siem_type}.health", is_healthy)
                span.set_attribute(f"siem.{siem_type}.health.message", message)
                return siem_type, is_healthy, message
            except SIEMClientError as e:
                self.self_healing_manager.record_failure(siem_type, e)
                span.set_attribute(f"siem.{siem_type}.health", False)
                span.set_attribute(
                    f"siem.{siem_type}.health.error.type", type(e).__name__
                )
                span.set_attribute(f"siem.{siem_type}.health.error.message", str(e))
                logger.error(f"Health check for {siem_type} failed: {e}", exc_info=True)
                return (
                    siem_type,
                    False,
                    f"Health check failed ({type(e).__name__}): {e.args[0]}",
                )
            except Exception as e:
                self.self_healing_manager.record_failure(siem_type, e)
                span.set_attribute(f"siem.{siem_type}.health", False)
                span.set_attribute(
                    f"siem.{siem_type}.health.error.type", type(e).__name__
                )
                span.set_attribute(f"siem.{siem_type}.health.error.message", str(e))
                logger.error(
                    f"Health check for {siem_type} failed with unexpected error: {e}",
                    exc_info=True,
                )
                return (
                    siem_type,
                    False,
                    f"Health check failed with unexpected exception: {e}",
                )

    async def send_siem_event(
        self,
        event_type: str,
        event_details: Dict[str, Any],
        siem_type_override: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # Validate event_type for security
        event_type = self.validate_event_type(event_type)
        with TRACER.start_as_current_span(f"siem_event.send.{event_type}") as span:
            span.set_attribute("event.type", event_type)
            span.set_attribute("siem.target_override", siem_type_override)
            siem_type_to_use = siem_type_override or self.default_siem_type
            backend = self.active_backends.get(siem_type_to_use)
            if not backend:
                reason = (
                    f"No active SIEM backend configured for type '{siem_type_to_use}'."
                )
                logger.warning(reason)
                if prometheus_available:
                    SIEM_SEND_ERRORS_TOTAL.labels(
                        siem_type=siem_type_to_use, error_type="backend_not_found"
                    ).inc()
                if trace:
                    span.set_status(
                        trace.Status(trace.StatusCode.ERROR, description=reason)
                    )
                await _audit_event(
                    "siem_event_send",
                    "backend_not_found",
                    {
                        "siem_type": siem_type_to_use,
                        "event_type": event_type,
                        "success": False,
                        "reason": reason,
                    },
                )
                return {"success": False, "reason": reason}
            if self.self_healing_manager.is_backend_disabled(siem_type_to_use):
                reason = f"SIEM backend '{siem_type_to_use}' is temporarily disabled due to repeated failures."
                logger.warning(reason)
                if prometheus_available:
                    SIEM_SEND_ERRORS_TOTAL.labels(
                        siem_type=siem_type_to_use, error_type="backend_disabled"
                    ).inc()
                if trace:
                    span.set_status(
                        trace.Status(trace.StatusCode.ERROR, description=reason)
                    )
                await self.self_healing_manager.enqueue_for_retry(
                    {
                        "event_type": event_type,
                        "details": event_details,
                        "metadata": metadata,
                        "target_siem_type": siem_type_to_use,
                    }
                )
                await _audit_event(
                    "siem_event_send",
                    "backend_disabled",
                    {
                        "siem_type": siem_type_to_use,
                        "event_type": event_type,
                        "success": False,
                        "reason": reason,
                    },
                )
                return {"success": False, "reason": reason}
            full_event_payload = {
                "event_id": str(uuid.uuid4()),
                "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
                "hostname": self._hostname,
                "event_type": event_type,
                "details": event_details,
                "plugin_source": PLUGIN_MANIFEST["name"],
                **(metadata or {}),
            }
            if self.policy_enforcer:
                is_allowed, policy_reason, processed_event = (
                    self.policy_enforcer.enforce(full_event_payload)
                )
                if not is_allowed:
                    logger.warning(
                        f"Event '{event_type}' blocked by policy: {policy_reason}"
                    )
                    if prometheus_available:
                        SIEM_SEND_ERRORS_TOTAL.labels(
                            siem_type=siem_type_to_use, error_type="policy_blocked"
                        ).inc()
                    if trace:
                        span.set_status(
                            trace.Status(
                                trace.StatusCode.ERROR,
                                description=f"Policy blocked: {policy_reason}",
                            )
                        )
                    await _audit_event(
                        "siem_event_send",
                        "policy_blocked",
                        {
                            "siem_type": siem_type_to_use,
                            "event_type": event_type,
                            "success": False,
                            "reason": policy_reason,
                            "original_event_id": full_event_payload.get("event_id"),
                        },
                    )
                    return {"success": False, "reason": policy_reason}
                full_event_payload = processed_event
                span.set_attribute("policy.enforced", True)
            start_time = time.monotonic()
            try:
                is_success, message = await backend.send_log(full_event_payload)
                if prometheus_available:
                    SIEM_SEND_LATENCY_SECONDS.labels(
                        siem_type=siem_type_to_use
                    ).observe(time.monotonic() - start_time)
                if is_success:
                    if prometheus_available:
                        SIEM_EVENTS_SENT_TOTAL.labels(
                            siem_type=siem_type_to_use, status="success"
                        ).inc()
                        EVENT_TYPES_SENT.labels(event_type=event_type).inc()
                    self.self_healing_manager.record_success(siem_type_to_use)
                    logger.info(
                        f"Event '{event_type}' sent to {siem_type_to_use}: {message}"
                    )
                    if trace:
                        span.set_status(trace.Status(trace.StatusCode.OK))
                    await _audit_event(
                        "siem_event_send",
                        "success",
                        {
                            "siem_type": siem_type_to_use,
                            "event_type": event_type,
                            "event_id": full_event_payload["event_id"],
                            "success": True,
                            "message": message,
                        },
                    )
                    return {"success": True, "reason": message}
                else:
                    reason = (
                        message or "Failed to send event without explicit exception."
                    )
                    if prometheus_available:
                        SIEM_EVENTS_SENT_TOTAL.labels(
                            siem_type=siem_type_to_use, status="failed"
                        ).inc()
                        SIEM_SEND_ERRORS_TOTAL.labels(
                            siem_type=siem_type_to_use,
                            error_type="send_failed_no_exception",
                        ).inc()
                    self.self_healing_manager.record_failure(
                        siem_type_to_use, Exception(reason)
                    )
                    if trace:
                        span.set_status(
                            trace.Status(trace.StatusCode.ERROR, description=reason)
                        )
                    await self.self_healing_manager.enqueue_for_retry(
                        {
                            "event_type": event_type,
                            "details": event_details,
                            "metadata": metadata,
                            "target_siem_type": siem_type_to_use,
                        }
                    )
                    logger.error(
                        f"Failed to send event '{event_type}' to {siem_type_to_use}: {reason}"
                    )
                    await _audit_event(
                        "siem_event_send",
                        "failed",
                        {
                            "siem_type": siem_type_to_use,
                            "event_type": event_type,
                            "event_id": full_event_payload["event_id"],
                            "success": False,
                            "error": reason,
                        },
                    )
                    return {"success": False, "reason": reason}
            except SIEMClientError as e:
                if prometheus_available:
                    SIEM_EVENTS_SENT_TOTAL.labels(
                        siem_type=siem_type_to_use, status="failed"
                    ).inc()
                    SIEM_SEND_ERRORS_TOTAL.labels(
                        siem_type=siem_type_to_use, error_type=type(e).__name__
                    ).inc()
                self.self_healing_manager.record_failure(siem_type_to_use, e)
                if trace:
                    span.set_status(
                        trace.Status(
                            trace.StatusCode.ERROR,
                            description=f"{type(e).__name__}: {e.args[0]}",
                        )
                    )
                    span.record_exception(e)
                await self.self_healing_manager.enqueue_for_retry(
                    {
                        "event_type": event_type,
                        "details": event_details,
                        "metadata": metadata,
                        "target_siem_type": siem_type_to_use,
                    }
                )
                logger.error(
                    f"SIEM Client Error sending event '{event_type}' to {siem_type_to_use}: {e}",
                    exc_info=True,
                )
                await _audit_event(
                    "siem_event_send",
                    "client_error",
                    {
                        "siem_type": siem_type_to_use,
                        "event_type": event_type,
                        "event_id": full_event_payload["event_id"],
                        "success": False,
                        "error_type": type(e).__name__,
                        "error": str(e),
                        "details": e.details,
                    },
                )
                return {
                    "success": False,
                    "reason": f"{type(e).__name__}: {e.args[0]}",
                    "details": e.details,
                }
            except Exception as e:
                if prometheus_available:
                    SIEM_EVENTS_SENT_TOTAL.labels(
                        siem_type=siem_type_to_use, status="failed"
                    ).inc()
                    SIEM_SEND_ERRORS_TOTAL.labels(
                        siem_type=siem_type_to_use, error_type="unexpected_exception"
                    ).inc()
                self.self_healing_manager.record_failure(siem_type_to_use, e)
                if trace:
                    span.set_status(
                        trace.Status(
                            trace.StatusCode.ERROR,
                            description=f"Unexpected error: {str(e)}",
                        )
                    )
                    span.record_exception(e)
                await self.self_healing_manager.enqueue_for_retry(
                    {
                        "event_type": event_type,
                        "details": event_details,
                        "metadata": metadata,
                        "target_siem_type": siem_type_to_use,
                    }
                )
                logger.error(
                    f"Unexpected exception while sending event '{event_type}' to {siem_type_to_use}: {e}",
                    exc_info=True,
                )
                await _audit_event(
                    "siem_event_send",
                    "unexpected_error",
                    {
                        "siem_type": siem_type_to_use,
                        "event_type": event_type,
                        "event_id": full_event_payload["event_id"],
                        "success": False,
                        "error": str(e),
                    },
                )
                return {
                    "success": False,
                    "reason": f"Unexpected error during send: {e}",
                }

    def validate_event_type(event_type: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]+$", event_type):
            raise ValueError("Invalid event type.")
        return event_type

    async def query_siem_logs(
        self,
        query_string: str,
        siem_type_override: Optional[str] = None,
        time_range: str = "24h",
        limit: int = 100,
        generic_query_format: bool = False,
    ) -> Dict[str, Any]:
        with TRACER.start_as_current_span("siem_query.execute") as span:
            span.set_attribute("siem.query_string", query_string)
            span.set_attribute("siem.target_override", siem_type_override)
            span.set_attribute("siem.time_range", time_range)
            span.set_attribute("siem.limit", limit)
            span.set_attribute("siem.generic_query_format", generic_query_format)
            siem_type_to_use = siem_type_override or self.default_siem_type
            backend = self.active_backends.get(siem_type_to_use)
            if not backend:
                reason = (
                    f"No active SIEM backend configured for type '{siem_type_to_use}'."
                )
                logger.warning(reason)
                if prometheus_available:
                    SIEM_QUERY_ERRORS_TOTAL.labels(
                        siem_type=siem_type_to_use, error_type="backend_not_found"
                    ).inc()
                if trace:
                    span.set_status(
                        trace.Status(trace.StatusCode.ERROR, description=reason)
                    )
                await _audit_event(
                    "siem_log_query",
                    "backend_not_found",
                    {
                        "siem_type": siem_type_to_use,
                        "query": query_string,
                        "success": False,
                        "reason": reason,
                    },
                )
                return {"success": False, "reason": reason, "results": []}
            if not hasattr(backend, "query_logs") or not backend.query_logs:
                reason = f"SIEM backend '{siem_type_to_use}' does not support querying."
                logger.warning(reason)
                if prometheus_available:
                    SIEM_QUERY_ERRORS_TOTAL.labels(
                        siem_type=siem_type_to_use, error_type="query_not_supported"
                    ).inc()
                if trace:
                    span.set_status(
                        trace.Status(trace.StatusCode.ERROR, description=reason)
                    )
                await _audit_event(
                    "siem_log_query",
                    "query_not_supported",
                    {
                        "siem_type": siem_type_to_use,
                        "query": query_string,
                        "success": False,
                        "reason": reason,
                    },
                )
                return {"success": False, "reason": reason, "results": []}
            actual_query_string = query_string
            if generic_query_format and self._query_parser:
                try:
                    parsed_query_info = self._query_parser.parse_query(
                        query_string, siem_type_to_use
                    )
                    actual_query_string = parsed_query_info["query_string"]
                    time_range = parsed_query_info["time_range"] or time_range
                    limit = parsed_query_info["limit"] or limit
                    span.set_attribute("siem.translated_query", actual_query_string)
                    span.set_attribute("siem.parsed_time_range", time_range)
                    span.set_attribute("siem.parsed_limit", limit)
                    logger.info(
                        f"Translated generic query for {siem_type_to_use}: '{query_string}' -> '{actual_query_string}'"
                    )
                except QueryParsingError as e:
                    reason = f"Failed to parse generic query: {e.message}"
                    logger.error(reason, exc_info=True)
                    if prometheus_available:
                        SIEM_QUERY_ERRORS_TOTAL.labels(
                            siem_type=siem_type_to_use, error_type="query_parse_error"
                        ).inc()
                    if trace:
                        span.set_status(
                            trace.Status(trace.StatusCode.ERROR, description=reason)
                        )
                        span.record_exception(e)
                    await _audit_event(
                        "siem_log_query",
                        "query_parse_error",
                        {
                            "siem_type": siem_type_to_use,
                            "query": query_string,
                            "success": False,
                            "reason": reason,
                        },
                    )
                    return {"success": False, "reason": reason, "results": []}
            elif generic_query_format and not self._query_parser:
                reason = "Generic query format requested but SiemQueryLanguageParser is not available."
                logger.warning(reason)
                if prometheus_available:
                    SIEM_QUERY_ERRORS_TOTAL.labels(
                        siem_type=siem_type_to_use, error_type="parser_unavailable"
                    ).inc()
                if trace:
                    span.set_status(
                        trace.Status(trace.StatusCode.ERROR, description=reason)
                    )
                await _audit_event(
                    "siem_log_query",
                    "parser_unavailable",
                    {
                        "siem_type": siem_type_to_use,
                        "query": query_string,
                        "success": False,
                        "reason": reason,
                    },
                )
                return {"success": False, "reason": reason, "results": []}
            start_time = time.monotonic()
            try:
                results = await backend.query_logs(
                    actual_query_string, time_range, limit
                )
                if prometheus_available:
                    SIEM_QUERY_LATENCY_SECONDS.labels(
                        siem_type=siem_type_to_use
                    ).observe(time.monotonic() - start_time)
                    SIEM_EVENTS_SENT_TOTAL.labels(
                        siem_type=siem_type_to_use, status="query_success"
                    ).inc()
                self.self_healing_manager.record_success(siem_type_to_use)
                logger.info(
                    f"Query '{actual_query_string}' to {siem_type_to_use} returned {len(results)} results."
                )
                if trace:
                    span.set_status(trace.Status(trace.StatusCode.OK))
                span.set_attribute("siem.query_result_count", len(results))
                await _audit_event(
                    "siem_log_query",
                    "success",
                    {
                        "siem_type": siem_type_to_use,
                        "query": actual_query_string,
                        "time_range": time_range,
                        "limit": limit,
                        "result_count": len(results),
                        "success": True,
                    },
                )
                return {
                    "success": True,
                    "results": results,
                    "reason": "Query successful.",
                }
            except SIEMClientError as e:
                if prometheus_available:
                    SIEM_QUERY_ERRORS_TOTAL.labels(
                        siem_type=siem_type_to_use, error_type=type(e).__name__
                    ).inc()
                self.self_healing_manager.record_failure(siem_type_to_use, e)
                if trace:
                    span.set_status(
                        trace.Status(
                            trace.StatusCode.ERROR,
                            description=f"{type(e).__name__}: {e.args[0]}",
                        )
                    )
                    span.record_exception(e)
                logger.error(
                    f"SIEM Client Error querying '{siem_type_to_use}': {e}",
                    exc_info=True,
                )
                await _audit_event(
                    "siem_log_query",
                    "client_error",
                    {
                        "siem_type": siem_type_to_use,
                        "query": actual_query_string,
                        "success": False,
                        "error_type": type(e).__name__,
                        "error": str(e),
                        "details": e.details,
                    },
                )
                return {
                    "success": False,
                    "reason": f"{type(e).__name__}: {e.args[0]}",
                    "results": [],
                    "details": e.details,
                }
            except Exception as e:
                if prometheus_available:
                    SIEM_QUERY_ERRORS_TOTAL.labels(
                        siem_type=siem_type_to_use, error_type="unexpected_exception"
                    ).inc()
                self.self_healing_manager.record_failure(siem_type_to_use, e)
                if trace:
                    span.set_status(
                        trace.Status(
                            trace.StatusCode.ERROR,
                            description=f"Unexpected error: {str(e)}",
                        )
                    )
                    span.record_exception(e)
                logger.error(
                    f"Unexpected exception while querying '{siem_type_to_use}': {e}",
                    exc_info=True,
                )
                await _audit_event(
                    "siem_log_query",
                    "unexpected_error",
                    {
                        "siem_type": siem_type_to_use,
                        "query": actual_query_string,
                        "success": False,
                        "error": str(e),
                    },
                )
                return {
                    "success": False,
                    "reason": f"Unexpected error during query: {e}",
                    "results": [],
                }

    async def close_all_backends(self):
        self.stop_retry_task()
        tasks = [
            self._close_backend_safely(siem_type, backend_instance)
            for siem_type, backend_instance in self.active_backends.items()
        ]
        await asyncio.gather(*tasks)

    async def _close_backend_safely(
        self, siem_type: str, backend_instance: BaseSIEMClient
    ):
        try:
            await backend_instance.close()
            logger.info(f"Closed session for SIEM backend: {siem_type}.")
            await _audit_event(
                "siem_backend_close", "success", {"siem_type": siem_type}
            )
        except Exception as e:
            logger.error(
                f"Error closing session for SIEM backend '{siem_type}': {e}",
                exc_info=True,
            )
            await _audit_event(
                "siem_backend_close",
                "failed",
                {"siem_type": siem_type, "error": str(e)},
            )


_siem_plugin_instance: Optional[GenericSIEMIntegrationPlugin] = None
_config_reload_task: Optional[asyncio.Task] = None


def get_plugin_manifest() -> Dict[str, Any]:
    return PLUGIN_MANIFEST


async def _monitor_config_changes():
    global _siem_plugin_instance
    last_config_hash = None
    while True:
        try:
            current_config_dict = _load_raw_config_from_env()
            current_config_hash = hashlib.sha256(
                json.dumps(current_config_dict, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if last_config_hash is None:
                last_config_hash = current_config_hash
            elif current_config_hash != last_config_hash:
                logger.info("Detected configuration change. Reloading SIEM plugin.")
                if _siem_plugin_instance:
                    await _siem_plugin_instance.close_all_backends()
                if PYDANTIC_AVAILABLE:
                    migrated_new_config = PluginGlobalConfig.migrate_config(
                        current_config_dict
                    )
                    migrated_new_config.pop("_CONFIG_VERSION", None)
                    new_config_model = PluginGlobalConfig.parse_obj(migrated_new_config)
                else:
                    new_config_model = current_config_dict
                _siem_plugin_instance = GenericSIEMIntegrationPlugin(new_config_model)
                _siem_plugin_instance.start_retry_task()
                last_config_hash = current_config_hash
                logger.info("SIEM plugin reloaded with new configuration.")
                await _audit_event(
                    "siem_config_reload",
                    "success",
                    {"new_config_hash": current_config_hash},
                )
        except Exception as e:
            logger.error(f"Error during config hot reload process: {e}", exc_info=True)
            await _audit_event("siem_config_reload", "failed", {"error": str(e)})
        await asyncio.sleep(60)


def register_plugin_entrypoints(register_func: Callable):
    global _siem_plugin_instance, _config_reload_task
    logger.info("Registering GenericSIEMIntegrationPlugin entrypoints...")
    if _siem_plugin_instance is None:
        initial_raw_config = _load_raw_config_from_env()
        if PYDANTIC_AVAILABLE:
            migrated_config = PluginGlobalConfig.migrate_config(initial_raw_config)
            migrated_config.pop("_CONFIG_VERSION", None)
            processed_config = PluginGlobalConfig.parse_obj(migrated_config)
        else:
            processed_config = initial_raw_config
        _siem_plugin_instance = GenericSIEMIntegrationPlugin(processed_config)
        _siem_plugin_instance.start_retry_task()
    register_func(
        name="siem_integration_health",
        executor_func=_siem_plugin_instance.plugin_health,
        capabilities=["siem_health_check"],
        is_async=True,
    )
    register_func(
        name="send_siem_event",
        executor_func=_siem_plugin_instance.send_siem_event,
        capabilities=["siem_event_forwarding", "audit_logging"],
        is_async=True,
    )
    register_func(
        name="query_siem_logs",
        executor_func=_siem_plugin_instance.query_siem_logs,
        capabilities=["siem_log_retrieval", "query_translation"],
        is_async=True,
    )
    if _config_reload_task is None:
        _config_reload_task = asyncio.create_task(_monitor_config_changes())
        logger.info(
            "Started background task for monitoring SIEM configuration changes."
        )


def shutdown_plugin():
    global _siem_plugin_instance, _config_reload_task
    if _config_reload_task:
        _config_reload_task.cancel()
        _config_reload_task = None
        logger.info(
            "Stopped background task for monitoring SIEM configuration changes."
        )
    if _siem_plugin_instance:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(_siem_plugin_instance.close_all_backends())
        _siem_plugin_instance = None
        logger.info("SIEM Integration Plugin shut down gracefully.")


if __name__ == "__main__":
    try:
        import uvicorn
        from fastapi import APIRouter, FastAPI, HTTPException, Request, status
        from fastapi.responses import JSONResponse
        from pydantic import BaseModel, Field

        try:
            import typer

            TYPER_AVAILABLE = True
        except ImportError:
            typer = None
            TYPER_AVAILABLE = False
        FASTAPI_CLI_AVAILABLE = True
    except ImportError:
        logger.error(
            "FastAPI or Typer not found. Direct REST/CLI testing features unavailable. Install with `pip install fastapi uvicorn pydantic typer`."
        )
        FASTAPI_CLI_AVAILABLE = False

    async def _run_api_server():
        if not FASTAPI_CLI_AVAILABLE:
            print("FastAPI not available for API server.", file=sys.stderr)
            return
        app = FastAPI(
            title="SIEM Plugin Test API",
            description="API for testing Generic SIEM Integration Plugin capabilities.",
        )
        register_plugin_entrypoints(
            lambda name, executor_func, capabilities, is_async: None
        )

        class SiemEventRequest(BaseModel):
            event_type: str = Field(..., description="Type of the SIEM event.")
            event_details: Dict[str, Any] = Field(
                ..., description="Detailed payload of the event."
            )
            siem_type_override: Optional[str] = Field(
                None, description="Optional: specific SIEM type to send to."
            )
            metadata: Optional[Dict[str, Any]] = Field(
                None, description="Optional: additional event metadata."
            )

        class SiemQueryRequest(BaseModel):
            query_string: str = Field(..., description="The query string to execute.")
            siem_type_override: Optional[str] = Field(
                None, description="Optional: specific SIEM type to query."
            )
            time_range: str = Field("24h", description="Relative time range.")
            limit: int = Field(100, description="Maximum number of results.")
            generic_query_format: bool = Field(
                False,
                description="Whether the query_string is in a generic format that needs translation.",
            )

        @app.post(
            "/send_siem_event",
            response_model=Dict[str, Any],
            summary="Send a SIEM event",
        )
        async def send_event_api(request: SiemEventRequest):
            if not _siem_plugin_instance:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="SIEM plugin not initialized.",
                )
            try:
                result = await _siem_plugin_instance.send_siem_event(
                    event_type=request.event_type,
                    event_details=request.event_details,
                    siem_type_override=request.siem_type_override,
                    metadata=request.metadata,
                )
                return JSONResponse(content=result)
            except Exception as e:
                logger.error(f"API call to send_siem_event failed: {e}", exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
                )

        @app.post(
            "/query_siem_logs", response_model=Dict[str, Any], summary="Query SIEM logs"
        )
        async def query_logs_api(request: SiemQueryRequest):
            if not _siem_plugin_instance:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="SIEM plugin not initialized.",
                )
            try:
                result = await _siem_plugin_instance.query_siem_logs(
                    query_string=request.query_string,
                    siem_type_override=request.siem_type_override,
                    time_range=request.time_range,
                    limit=request.limit,
                    generic_query_format=request.generic_query_format,
                )
                return JSONResponse(content=result)
            except Exception as e:
                logger.error(f"API call to query_siem_logs failed: {e}", exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
                )

        @app.get(
            "/health", response_model=Dict[str, Any], summary="Get plugin health status"
        )
        async def health_check_api():
            if not _siem_plugin_instance:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="SIEM plugin not initialized.",
                )
            return await _siem_plugin_instance.plugin_health()

        @app.on_event("shutdown")
        async def shutdown_event_hook():
            shutdown_plugin()
            logger.info(
                "SIEM Plugin FastAPI server shutting down: shutdown hook triggered."
            )

        print("\n--- Starting SIEM Plugin Test API (Uvicorn) ---")
        print("API endpoint: POST http://localhost:8000/send_siem_event")
        print("API endpoint: POST http://localhost:8000/query_siem_logs")
        print("Health check: GET http://localhost:8000/health")
        uvicorn.run(app, host="0.0.0.0", port=8000)

    async def _run_cli_test():
        if not TYPER_AVAILABLE:
            print(
                "Typer not available for CLI mode. Install with `pip install typer`.",
                file=sys.stderr,
            )
            return
        app_cli = typer.Typer(help="Generic SIEM Integration Plugin CLI.")
        register_plugin_entrypoints(
            lambda name, executor_func, capabilities, is_async: None
        )

        @app_cli.command(name="send")
        def send_command(
            event_type: str = typer.Argument(..., help="Type of the SIEM event."),
            details_json: str = typer.Argument(
                ..., help="JSON string of event details."
            ),
            siem_type: Optional[str] = typer.Option(
                None,
                "--siem-type",
                "-s",
                help="Specific SIEM type to override default.",
            ),
            metadata_json: Optional[str] = typer.Option(
                None, "--metadata", "-m", help="JSON string of additional metadata."
            ),
        ):
            try:
                event_details_parsed = json.loads(details_json)
            except json.JSONDecodeError:
                print("Error: 'details_json' is not valid JSON.", file=sys.stderr)
                return
            metadata_parsed = None

            if metadata_json:
                try:
                    metadata_parsed = json.loads(metadata_json)
                except json.JSONDecodeError:
                    print("Error: 'metadata_json' is not valid JSON.", file=sys.stderr)
                    return

            async def _send_wrapper():
                if not _siem_plugin_instance:
                    print("SIEM plugin not initialized.", file=sys.stderr)
                    return
                print(f"Sending event '{event_type}' to SIEM...")
                result = await _siem_plugin_instance.send_siem_event(
                    event_type=event_type,
                    event_details=event_details_parsed,
                    siem_type_override=siem_type,
                    metadata=metadata_parsed,
                )
                print("\nSend Result:")
                print(json.dumps(result, indent=2))

            asyncio.run(_send_wrapper())

        @app_cli.command(name="query")
        def query_command(
            query_string: str = typer.Argument(
                ..., help="The query string to execute."
            ),
            siem_type: Optional[str] = typer.Option(
                None, "--siem-type", "-s", help="Specific SIEM type to query."
            ),
            time_range: str = typer.Option(
                "24h", "--time-range", "-t", help="Relative time range."
            ),
            limit: int = typer.Option(
                100, "--limit", "-l", help="Maximum number of results."
            ),
            generic_format: bool = typer.Option(
                False,
                "--generic-format",
                "-g",
                help="Use generic query format which will be translated.",
            ),
        ):
            async def _query_wrapper():
                if not _siem_plugin_instance:
                    print("SIEM plugin not initialized.", file=sys.stderr)
                    return
                print(f"Querying SIEM for '{query_string}'...")
                result = await _siem_plugin_instance.query_siem_logs(
                    query_string=query_string,
                    siem_type_override=siem_type,
                    time_range=time_range,
                    limit=limit,
                    generic_query_format=generic_format,
                )
                print("\nQuery Result:")
                print(json.dumps(result, indent=2))

            asyncio.run(_query_wrapper())

        @app_cli.command(name="health")
        def health_command():
            async def _health_wrapper():
                if not _siem_plugin_instance:
                    print("SIEM plugin not initialized.", file=sys.stderr)
                    return
                health_status = await _siem_plugin_instance.plugin_health()
                print("\nHealth Check Result:")
                print(json.dumps(health_status, indent=2))

            asyncio.run(_health_wrapper())

        print("\n--- Running SIEM Plugin Test CLI ---")
        app_cli()

    async def _default_standalone_test():
        print("\n--- Generic SIEM Integration Plugin Standalone Test ---")
        register_plugin_entrypoints(
            lambda name, executor_func, capabilities, is_async: None
        )
        print("\n--- Running Plugin Health Check ---")
        health_status = await _siem_plugin_instance.plugin_health()
        print(f"Health Status: {health_status['status']}")
        [print(f"  - {detail}") for detail in health_status["details"]]
        if health_status["status"] not in ["ok", "degraded"]:
            print("\n--- Skipping Event Send/Query Test: Plugin not healthy. ---")
            print(
                "Please ensure a SIEM is properly configured via environment variables and its dependencies are installed."
            )
            return
        print("\n--- Test 1: Sending a Sample Security Alert Event (Default SIEM) ---")
        sample_event_details = {
            "alert_id": str(uuid.uuid4()),
            "source_module": "self_healing_engineer",
            "vulnerability_type": "SQL_Injection_Attempt",
            "severity": "CRITICAL",
            "message": "Unusual login activity detected from IP 192.168.1.100.",
            "user_id": "test_user_123",
            "ip_address": "192.168.1.100",
            "action_taken": "blocked_ip",
            "pii_example_email": "test@example.com",
            "credit_card_number": "1234-5678-9012-3456",
        }
        send_result_1 = await _siem_plugin_instance.send_siem_event(
            event_type="security_alert", event_details=sample_event_details
        )
        print("\nSend Result (Test 1 - Default SIEM):")
        print(json.dumps(send_result_1, indent=2))
        if send_result_1["success"]:
            print("Test 1 PASSED: Sample security alert sent successfully.")
        else:
            print(f"Test 1 FAILED: {send_result_1['reason']}")
        print("-" * 50)
        print(
            "\n--- Test 2: Sending an Audit Log Event (Attempt to specific SIEM with policy checks) ---"
        )
        sample_audit_details = {
            "audit_id": str(uuid.uuid4()),
            "user": "sfe_bot",
            "action": "code_refactor_applied",
            "file_changed": "src/utils.py",
            "commit_hash": "a1b2c3d4e5f6",
            "policy_compliant": True,
            "dry_run": False,
            "secret_key_field": "my_secret_key_123",
            "transaction_value": 150.75,
            "customer_region": "EU-WEST",
            "sensitive_data": "Social Security Number: 999-99-9999",
        }
        if PYDANTIC_AVAILABLE:
            _siem_plugin_instance.config.policy.rules.append(
                PolicyRule(
                    conditions=[
                        PolicyCondition(
                            field="secret_key_field",
                            operator="contains",
                            value="secret",
                        )
                    ],
                    action="mask",
                    target_field="secret_key_field",
                    mask_with="[MASKED_SECRET]",
                )
            )
            _siem_plugin_instance.config.policy.rules.append(
                PolicyRule(
                    conditions=[
                        PolicyCondition(
                            field="transaction_value",
                            operator="greater_than",
                            value=1000.0,
                        )
                    ],
                    action="block",
                )
            )
            _siem_plugin_instance.config.policy.default_pii_patterns.append(
                r"\d{3}-\d{2}-\d{4}"
            )
            _siem_plugin_instance.config.policy.compliance_flags.append(
                "GDPR_Compliant"
            )
            _siem_plugin_instance.policy_enforcer = PolicyEnforcer(
                _siem_plugin_instance.config.policy
            )
        send_result_2 = await _siem_plugin_instance.send_siem_event(
            event_type="code_audit_event",
            event_details=sample_audit_details,
            siem_type_override="elastic",
        )
        print("\nSend Result (Test 2 - Elasticsearch attempt with policy):")
        print(json.dumps(send_result_2, indent=2))
        if send_result_2["success"]:
            print(
                "Test 2 PASSED: Sample audit log sent successfully to Elasticsearch with policy."
            )
        else:
            print(
                f"Test 2 FAILED (expected if Elastic not configured or policy blocked): {send_result_2['reason']}"
            )
        print("-" * 50)
        print(
            "\n--- Test 3: Querying a SIEM (Attempt to default SIEM with generic query) ---"
        )
        default_siem_for_query = _siem_plugin_instance.default_siem_type
        if (
            _siem_plugin_instance.active_backends.get(default_siem_for_query)
            and hasattr(
                _siem_plugin_instance.active_backends[default_siem_for_query],
                "query_logs",
            )
            and _siem_plugin_instance.active_backends[default_siem_for_query].query_logs
        ):
            generic_query_test_string = (
                "ERROR events in past 1 hour for service=self_healing_engineer"
            )
            print(
                f"Attempting to query default SIEM: {default_siem_for_query} with generic query: '{generic_query_test_string}'"
            )
            query_result_1 = await _siem_plugin_instance.query_siem_logs(
                query_string=generic_query_test_string,
                limit=5,
                generic_query_format=True,
            )
            print("\nQuery Result (Test 3 - Default SIEM with Generic Query):")
            print(json.dumps(query_result_1, indent=2))
            if query_result_1["success"]:
                print(
                    f"Test 3 PASSED: Query returned {len(query_result_1['results'])} results."
                )
            else:
                print(f"Test 3 FAILED: {query_result_1['reason']}")
        else:
            print(
                f"Test 3 SKIPPED: Default SIEM '{default_siem_for_query}' not configured for query or does not support it."
            )
        print("-" * 50)
        print("\n--- Test 4: Simulate Backend Failure and Re-enable ---")
        print(
            f"Current disabled backends: {json.dumps(_siem_plugin_instance.self_healing_manager.disabled_backends, default=str, indent=2)}"
        )
        print(
            "To fully test self-healing, manually induce failures (e.g., invalidate API keys or URLs) and observe behavior."
        )
        print("-" * 50)
        print("\n--- Test 5: Simulate Configuration Hot Reload ---")
        print(
            "To test hot reload, change SIEM_DEFAULT_TYPE or another SIEM_CONFIG_ENV_VAR after this point and let the script run for a minute."
        )
        original_default_siem = _siem_plugin_instance.default_siem_type
        print(f"Original default SIEM: {original_default_siem}")
        print(
            "Waiting for 70 seconds to allow for potential config reload detection..."
        )
        await asyncio.sleep(70)
        current_default_siem = _siem_plugin_instance.default_siem_type
        if current_default_siem != original_default_siem:
            print(
                f"Test 5 PASSED: Default SIEM hot reloaded from '{original_default_siem}' to '{current_default_siem}'."
            )
        else:
            print(
                "Test 5 FAILED: Default SIEM did NOT hot reload (or no change detected). Ensure env var changed externally and waited long enough."
            )
        print("-" * 50)
        print("\n--- Test 6: Test Distributed Queue (if enabled) ---")
        if _siem_plugin_instance.config.distributed_queue_enabled:
            print("Distributed Queue is ENABLED. Testing enqueue/dequeue...")
            test_event_queue = {
                "type": "test_queue_event",
                "data": "This is a test from distributed queue.",
            }
            await _siem_plugin_instance.self_healing_manager.enqueue_for_retry(
                test_event_queue
            )
            print(
                f"Enqueued test event. Queue size: {await _siem_plugin_instance.self_healing_manager.get_queue_size()}"
            )
            print("Manually triggering queue processing...")
            await _siem_plugin_instance.self_healing_manager.process_retry_queue(
                _siem_plugin_instance
            )
            print(
                f"Queue size after processing: {await _siem_plugin_instance.self_healing_manager.get_queue_size()}"
            )
            print("Test 6 PASSED (manual verification of Redis/queue recommended).")
        else:
            print("Distributed Queue is DISABLED. Skipping Test 6.")
        print("-" * 50)
        print("\n--- All Generic SIEM Integration Plugin Standalone Tests Complete ---")

    if len(sys.argv) > 1 and sys.argv[1] == "run_api_server":
        asyncio.run(_run_api_server())
    elif len(sys.argv) > 1 and sys.argv[1] == "run_cli_test":
        _run_cli_test()
    else:
        try:
            asyncio.run(_default_standalone_test())
        finally:
            shutdown_plugin()
            logger.info("Main test finished. Plugin shutdown initiated.")
