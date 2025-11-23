# app/omnicore_engine/metrics.py
"""
Prometheus metrics for OmniCore Omega Engine.
Import from this file in all modules that need to increment metrics.
This file also provides compatibility and legacy helpers for older code.
"""

import logging
import os
import json
from typing import Optional, Dict, Any, Union
from prometheus_client import Counter, Gauge, Histogram, REGISTRY, start_http_server
from prometheus_client.metrics import MetricWrapperBase
from datetime import datetime

# Setup logger for metrics module
logger = logging.getLogger(__name__)
# Set a default logging handler for the metrics file
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# --- Helper Functions ---


def _get_or_create_metric(
    collector_class: type[MetricWrapperBase],
    name: str,
    documentation: str,
    labelnames: tuple = (),
    buckets: Optional[tuple] = None,
) -> MetricWrapperBase:
    """Idempotently create or retrieve a Prometheus metric."""
    if name in REGISTRY._names_to_collectors:
        existing_metric = REGISTRY._names_to_collectors[name]
        if not isinstance(existing_metric, collector_class):
            logger.warning(
                f"Metric '{name}' already registered with type {type(existing_metric).__name__}, "
                f"but requested as {collector_class.__name__}. Reusing existing."
            )
        return existing_metric
    if buckets is not None and collector_class == Histogram:
        metric = collector_class(name, documentation, labelnames=labelnames, buckets=buckets)
    else:
        metric = collector_class(name, documentation, labelnames=labelnames)
    return metric


# --- FIX: Define Fallback Classes at the Top-Level Scope ---
# This resolves the NameError and makes the code more robust.


class MockInfluxWriteApi:
    """A mock InfluxDB write API that logs metrics to a file."""

    def __init__(self):
        self.log_file_path = os.getenv("INFLUXDB_FALLBACK_LOG", "influxdb_fallback.log")

    def write(self, bucket: str, org: str, point: Any):
        """Simulates writing a point by logging it to a file."""
        with open(self.log_file_path, "a") as f:
            log_entry = {
                "bucket": bucket,
                "org": org,
                "measurement": point._name,
                "tags": point._tags,
                "fields": point._fields,
                "time": point._time,
            }
            f.write(json.dumps(log_entry) + "\n")
        logger.info(
            f"InfluxDB not available; metric '{point._name}' logged to file: {self.log_file_path}"
        )


class MockInfluxDBClient:
    """A mock InfluxDB client that logs to a file as a fallback."""

    def __init__(self, *args, **kwargs):
        logger.warning(
            "InfluxDB client not available. Metrics will be logged to a file as a fallback."
        )
        self._write_api = MockInfluxWriteApi()

    def write_api(self, *args, **kwargs):
        return self._write_api

    def close(self):
        pass


class MockPoint:
    """A mock Point class to mimic the InfluxDB client's Point."""

    def __init__(self, measurement: str):
        self._name = measurement
        self._tags = {}
        self._fields = {}
        self._time = None

    def tag(self, key: str, value: str):
        self._tags[key] = value
        return self

    def field(self, key: str, value: Union[str, float, int, bool]):
        self._fields[key] = value
        return self

    def time(self, t: datetime, *args, **kwargs):
        self._time = t.isoformat()
        return self


class MockWritePrecision:
    """A mock class for the WritePrecision enum."""

    NS = "ns"
    US = "us"
    MS = "ms"
    S = "s"


# --- InfluxDB Proxy & Availability Flag with Fallback ---
try:
    from influxdb_client import InfluxDBClient, Point
    from influxdb_client.client.write_api import WritePrecision

    INFLUXDB_AVAILABLE = True
except ImportError:
    INFLUXDB_AVAILABLE = False
    InfluxDBClient = MockInfluxDBClient
    Point = MockPoint
    WritePrecision = MockWritePrecision
    logger.warning("Could not import influxdb_client. Using mock fallback classes.")


# --- Prometheus HTTP Server Startup (optional) ---
try:
    # Use a port from environment variables or settings, if available
    port = int(os.getenv("PROMETHEUS_PORT", 8000))
    start_http_server(port)
    logger.info(f"Prometheus metrics server started on port {port}")
except OSError:
    logger.warning("Prometheus metrics server already started or port is in use.")


# --- Unified Metric Definitions ---

# Plugin Metrics
PLUGIN_EXECUTIONS_TOTAL = _get_or_create_metric(
    Counter,
    "omnicore_plugin_executions_total",
    "Total plugin executions",
    ["kind", "name"],
)
PLUGIN_EXECUTION_DURATION_SECONDS = _get_or_create_metric(
    Histogram,
    "omnicore_plugin_execution_duration_seconds",
    "Duration of plugin executions in seconds",
    ["kind", "name"],
    buckets=(
        0.001,
        0.002,
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
        float("inf"),
    ),
)
PLUGIN_ERRORS_TOTAL = _get_or_create_metric(
    Counter,
    "omnicore_plugin_errors_total",
    "Total errors encountered during plugin execution",
    ["kind", "name", "error_type"],
)
PLUGIN_ACTIVE_COUNT = _get_or_create_metric(
    Gauge, "omnicore_plugin_active_count", "Current number of active plugins"
)
PLUGIN_LOAD_ERRORS_TOTAL = _get_or_create_metric(
    Counter, "omnicore_plugin_load_errors_total", "Total plugin load errors"
)

# Simulation Metrics
SIMULATIONS_TOTAL = _get_or_create_metric(
    Counter, "omnicore_simulations_total", "Total simulations created"
)
ACTIVE_SIMULATIONS = _get_or_create_metric(
    Gauge, "omnicore_active_simulations", "Currently active simulations"
)
SIMULATION_DURATION_SECONDS = _get_or_create_metric(
    Gauge,
    "omnicore_simulation_duration_seconds",
    "Duration of simulations in seconds",
    ["sim_id", "status"],
)
SIMULATION_ERRORS_TOTAL = _get_or_create_metric(
    Counter, "omnicore_simulation_errors_total", "Total simulation errors", ["sim_id"]
)

# Database Metrics
DB_OPERATIONS_TOTAL = _get_or_create_metric(
    Counter, "omnicore_db_operations_total", "Total database operations", ["operation"]
)
DB_ERRORS_TOTAL = _get_or_create_metric(
    Counter, "omnicore_db_errors_total", "Total database errors", ["operation"]
)
DB_OPERATION_DURATION_SECONDS = _get_or_create_metric(
    Histogram,
    "omnicore_db_operation_duration_seconds",
    "Latency of database operations in seconds",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, float("inf")),
)

# Audit Metrics
AUDIT_RECORDS = _get_or_create_metric(
    Counter,
    "omnicore_audit_records_total",
    "Total audit records written",
    ["operation"],
)
AUDIT_ERRORS = _get_or_create_metric(
    Counter, "omnicore_audit_errors_total", "Total audit errors", ["operation"]
)
AUDIT_RECORDS_PROCESSED_TOTAL = _get_or_create_metric(
    Counter,
    "omnicore_audit_records_processed_total",
    "Total audit records processed",
    ["status"],
)
AUDIT_BUFFER_SIZE_CURRENT = _get_or_create_metric(
    Gauge, "omnicore_audit_buffer_size_current", "Current size of the audit buffer"
)
AUDIT_DB_OPERATIONS = _get_or_create_metric(
    Counter,
    "omnicore_audit_db_operations_total",
    "Total audit database operations",
    ["operation"],
)
AUDIT_DB_ERRORS = _get_or_create_metric(
    Counter,
    "omnicore_audit_db_errors_total",
    "Total audit database errors",
    ["operation"],
)

# API Metrics
API_REQUESTS_TOTAL = _get_or_create_metric(
    Counter, "omnicore_api_requests_total", "Total API requests", ["endpoint", "method"]
)
API_REQUEST_DURATION_SECONDS = _get_or_create_metric(
    Histogram,
    "omnicore_api_request_duration_seconds",
    "Duration of API requests in seconds",
    ["endpoint", "method"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, float("inf")),
)
API_ERRORS_TOTAL = _get_or_create_metric(
    Counter,
    "omnicore_api_errors_total",
    "Total API errors",
    ["endpoint", "method", "error_type"],
)

# Legacy aliases for backward compatibility
API_REQUESTS = API_REQUESTS_TOTAL
API_ERRORS = API_ERRORS_TOTAL

# CLI Metrics
CLI_COMMANDS_TOTAL = _get_or_create_metric(
    Counter, "omnicore_cli_commands_total", "Total CLI commands executed", ["command"]
)
CLI_COMMAND_DURATION_SECONDS = _get_or_create_metric(
    Histogram,
    "omnicore_cli_command_duration_seconds",
    "Duration of CLI commands in seconds",
    ["command"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, float("inf")),
)
CLI_ERRORS_TOTAL = _get_or_create_metric(
    Counter,
    "omnicore_cli_errors_total",
    "Total CLI command errors",
    ["command", "error_type"],
)

# Feedback Metrics
FEEDBACK_RECORDED = _get_or_create_metric(
    Counter,
    "omnicore_feedback_recorded_total",
    "Total feedback recorded",
    ["feedback_type"],
)
FEEDBACK_LATENCY = _get_or_create_metric(
    Histogram,
    "omnicore_feedback_processing_latency_seconds",
    "Feedback processing latency",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, float("inf")),
)

# Policy Engine Metrics
POLICY_EVALUATIONS_TOTAL = _get_or_create_metric(
    Counter,
    "omnicore_policy_evaluations_total",
    "Total policy evaluations performed",
    ["policy_name", "result"],
)
POLICY_DENIALS_TOTAL = _get_or_create_metric(
    Counter,
    "omnicore_policy_denials_total",
    "Total policy evaluations resulting in denial",
    ["policy_name", "reason"],
)
POLICY_EVALUATION_DURATION_SECONDS = _get_or_create_metric(
    Histogram,
    "omnicore_policy_evaluation_duration_seconds",
    "Duration of policy evaluations in seconds",
    ["policy_name"],
    buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, float("inf")),
)

# Feature Flag Metrics
FEATURE_FLAG_TOGGLES_TOTAL = _get_or_create_metric(
    Counter,
    "omnicore_feature_flag_toggles_total",
    "Total times a feature flag has been toggled",
    ["flag_name", "new_state"],
)
FEATURE_FLAG_READS_TOTAL = _get_or_create_metric(
    Counter,
    "omnicore_feature_flag_reads_total",
    "Total times a feature flag has been read",
    ["flag_name", "state"],
)

# System Snapshot Metrics
SYSTEM_SNAPSHOTS_TOTAL = _get_or_create_metric(
    Counter,
    "omnicore_system_snapshots_total",
    "Total system state snapshots created",
    ["status"],
)
SNAPSHOT_DURATION_SECONDS = _get_or_create_metric(
    Histogram,
    "omnicore_snapshot_duration_seconds",
    "Duration of system state snapshot operations in seconds",
    buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, float("inf")),
)

# Regression Guard Metrics
REGRESSION_DETECTIONS_TOTAL = _get_or_create_metric(
    Counter,
    "omnicore_regression_detections_total",
    "Total regressions detected by the guard",
    ["operation_name"],
)
REVERSIONS_TOTAL = _get_or_create_metric(
    Counter,
    "omnicore_reversions_total",
    "Total automatic system reversions initiated due to regression",
    ["reversion_type"],
)

# Message Bus Specific Metrics
MESSAGE_BUS_QUEUE_SIZE = _get_or_create_metric(
    Gauge,
    "omnicore_message_bus_queue_size",
    "Current size of message bus queues",
    ["shard_id"],
)
MESSAGE_BUS_DISPATCH_DURATION = _get_or_create_metric(
    Histogram,
    "omnicore_message_bus_dispatch_duration_seconds",
    "Time to dispatch messages",
    ["shard_id"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, float("inf")),
)
MESSAGE_BUS_TOPIC_THROUGHPUT = _get_or_create_metric(
    Counter,
    "omnicore_message_bus_topic_throughput_total",
    "Total messages processed per topic",
    ["topic"],
)
MESSAGE_BUS_CALLBACK_ERRORS = _get_or_create_metric(
    Counter,
    "omnicore_message_bus_callback_errors_total",
    "Total callback errors in message bus",
    ["shard_id", "topic"],
)
MESSAGE_BUS_PUBLISH_RETRIES = _get_or_create_metric(
    Counter,
    "omnicore_message_bus_publish_retries_total",
    "Total retries for publishing messages",
    ["shard_id"],
)
MESSAGE_BUS_CONSUMER_LAG = _get_or_create_metric(
    Gauge, "omnicore_message_bus_consumer_lag", "Kafka consumer lag", ["topic"]
)
MESSAGE_BUS_CALLBACK_LATENCY = _get_or_create_metric(
    Histogram,
    "omnicore_message_bus_callback_latency_seconds",
    "Time taken by subscriber callbacks",
    ["topic", "callback"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, float("inf")),
)
MESSAGE_BUS_MESSAGE_AGE = _get_or_create_metric(
    Histogram,
    "omnicore_message_bus_message_age_seconds",
    "Time messages spend in queue before dispatch",
    ["shard_id"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, float("inf")),
)

# Arbiter Metrics
ARBITER_OPERATIONS = _get_or_create_metric(
    Counter,
    "omnicore_arbiter_operations_total",
    "Total Arbiter operations",
    ["operation", "source"],
)

# --- ALIASES AND EXPORTS ---
# Define aliases for consistency
API_ERRORS = API_ERRORS_TOTAL
DB_OPERATIONS = DB_OPERATIONS_TOTAL
DB_ERRORS = DB_ERRORS_TOTAL
CLI_COMMANDS = CLI_COMMANDS_TOTAL
CLI_ERRORS = CLI_ERRORS_TOTAL

# The `plugin_executions` metric is a legacy alias, so we'll point it to the modern metric
plugin_executions = PLUGIN_EXECUTIONS_TOTAL

# --- Utility Functions ---


def get_all_metrics_data() -> Dict:
    """
    Returns a dictionary of all registered metrics and their current values.
    This is for introspection, not for Prometheus scraping.
    """
    data = {}
    for collector in list(REGISTRY.collect()):
        for metric in collector.samples:
            name = metric.name
            labels = metric.labels
            value = metric.value

            if labels:
                label_str = ",".join(f"{k}={v}" for k, v in labels.items())
                data[f"{name}{{{label_str}}}"] = value
            else:
                data[name] = value
    return data


def get_plugin_metrics() -> Dict:
    """Returns a dictionary of plugin-related metrics."""
    return {
        "plugin_executions_total": PLUGIN_EXECUTIONS_TOTAL.collect(),
        "plugin_active_count": PLUGIN_ACTIVE_COUNT.collect(),
        "plugin_load_errors_total": PLUGIN_LOAD_ERRORS_TOTAL.collect(),
    }


def get_test_metrics() -> Dict:
    """Returns a simplified dictionary of test-related metrics. (Placeholder)"""
    return {
        "test_suite_runs_total": 0,
        "test_failures_total": 0,
    }


# --- END FILE ---
