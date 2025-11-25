# runner/metrics.py
# module for metrics collection, export, and anomaly-based alerting.
# Integrates with Prometheus, external exporters (Datadog, CloudWatch), and provides
# a robust, configurable, and observable metrics pipeline with advanced resilience features.
#
# RESILIENT QUEUING (Merged from audit_metrics.py):
# - Exponential backoff retry mechanism for failed metric exports
# - Queue management with timestamp-based retry scheduling
# - Configurable retry intervals and max retry attempts
# - Graceful degradation with failover file support
# - Enhanced error tracking and observability

import asyncio
import contextlib
import json
import logging
import os

# FIX: Added 'Awaitable' and 'contextlib'
import typing  # FIX: Added for TYPE_CHECKING
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Tuple

import aiofiles
import prometheus_client as prom

# OpenTelemetry is used for tracing in other modules, but good to acknowledge
try:
    import opentelemetry.trace as trace

    _tracer = trace.get_tracer(__name__)
except ImportError:
    _tracer = None
    logging.getLogger(__name__).warning(
        "OpenTelemetry not installed. Tracing for metrics will be disabled."
    )


# External SDKs (with graceful degradation)
try:
    import datadog

    logging.getLogger(__name__).debug("Datadog SDK (v1) found.")
except ImportError:
    datadog = None
    logging.getLogger(__name__).debug("Datadog SDK (v1) not found.")

try:
    import boto3
    from botocore.exceptions import ClientError as BotoClientError
except ImportError:
    boto3 = None
    logging.getLogger(__name__).debug("Boto3 (AWS SDK) not found.")

# Assume runner.config and runner.logging are correctly imported and configured
# FIX: Move RunnerConfig import into TYPE_CHECKING block
if typing.TYPE_CHECKING:
    # --- FIX: Use relative imports ---
    from .runner_config import RunnerConfig, SecretStr

    # FIX: Move error imports here to break circular dependency
    from .runner_errors import (
        ERROR_CODE_REGISTRY as _error_codes,
    )  # Import the error code registry
else:
    # Break circular import at runtime
    _error_codes = {}
# --- FIX: Use relative import ---
from .runner_config import SecretStr  # Keep this one for runtime checks

# --- FIX: Removed log_action from top-level import to break circular dependency ---
# Gold Standard: Import structured errors and error codes
# from runner.runner_errors import RunnerError, ExporterError, PersistenceError # <-- MOVED
# from runner.runner_errors import ERROR_CODE_REGISTRY as error_codes # <-- MOVED

logger = logging.getLogger(__name__)


def get_error_codes() -> Dict[str, str]:
    """Lazy-load the registry to break circular import."""
    global _error_codes  # We need to write to the global cache
    if not _error_codes:
        try:
            # --- FIX: Use relative import ---
            from .runner_errors import ERROR_CODE_REGISTRY

            _error_codes = ERROR_CODE_REGISTRY  # Cache it
            return _error_codes
        except ImportError:
            # This should not happen at runtime, but as a fallback:
            return {}
    return _error_codes


# --- Prometheus Server Bootstrap ---
_prom_started = False
METRICS_PORT = int(os.getenv("METRICS_PORT", 8006))


def start_prometheus_server_once(port: int = METRICS_PORT):
    """Starts the Prometheus HTTP server on the given port, but only once."""
    global _prom_started
    if _prom_started:
        return
    try:
        addr = (
            "0.0.0.0"
            if os.getenv("PROMETHEUS_BIND_ALL", "false").lower() == "true"
            else "127.0.0.1"
        )
        prom.start_http_server(port, addr=addr)
        logger.info(
            f"Prometheus metrics HTTP server started on port {port}. Access at http://localhost:{port}/metrics"
        )
        _prom_started = True
        # SECURITY NOTE: If exposed externally, ensure proper network security (firewall, mTLS, IP allowlisting, or service mesh protection).
    except OSError as e:
        logger.warning(
            f"Failed to start Prometheus HTTP server on port {port}: {e}. It might already be running or port is in use."
        )
    except Exception as e:
        logger.error(
            f"Unexpected error starting Prometheus HTTP server: {e}", exc_info=True
        )


# --- Core Prometheus Metrics Initialization (FIXED ORDER) ---
# FIX: Changed LLM_CALLS_TOTAL to LLM_REQUESTS_TOTAL as per requirement
LLM_REQUESTS_TOTAL = prom.Counter(
    "llm_requests_total", "Total number of LLM API calls", ["provider", "model"]
)
LLM_ERRORS_TOTAL = prom.Counter(
    "llm_errors_total", "Total number of LLM API errors", ["provider", "model"]
)
LLM_LATENCY_SECONDS = prom.Histogram(
    "llm_latency_seconds", "LLM API response latency", ["provider", "model"]
)
LLM_TOKENS_INPUT = prom.Counter(
    "llm_tokens_input_total",
    "Total input tokens processed by LLM",
    ["provider", "model"],
)
LLM_TOKENS_OUTPUT = prom.Counter(
    "llm_tokens_output_total",
    "Total output tokens processed by LLM",
    ["provider", "model"],
)
LLM_COST_TOTAL = prom.Counter(
    "llm_cost_total", "Total estimated LLM cost", ["provider", "model"]
)
LLM_PROVIDER_HEALTH = prom.Gauge(
    "llm_provider_health_status",
    "Health status of LLM provider (1=healthy, 0=unhealthy)",
    ["provider"],
)
LLM_RATE_LIMIT_EXCEEDED = prom.Counter(
    "llm_rate_limit_exceeded_total", "Total times rate limit was exceeded", ["provider"]
)
LLM_CIRCUIT_STATE = prom.Gauge(
    "llm_circuit_breaker_state",
    "State of the LLM circuit breaker (1=open, 0.5=half, 0=closed)",
    ["provider"],
)

# Other Runner Metrics
RUN_LATENCY = prom.Histogram(
    "runner_latency_seconds",
    "Run latency of test executions",
    ["backend", "framework", "instance_id"],
)
RUN_ERRORS = prom.Counter(
    "runner_errors_total",
    "Total count of internal runner errors",
    ["error_type", "backend", "instance_id"],
)
RUN_SUCCESS = prom.Counter(
    "runner_successful_runs_total",
    "Total count of successful test runs",
    ["backend", "framework", "instance_id"],
)
RUN_FAILURE = prom.Counter(
    "runner_failed_runs_total",
    "Total count of failed test runs",
    ["backend", "framework", "instance_id"],
)

# --- FIX: ADD LABELS TO GAUGES AS EXPECTED BY test_runner_app.py ---
RUN_PASS_RATE = prom.Gauge(
    "runner_overall_test_pass_rate",
    "Overall Test pass rate (0-1) across all runs",
    # No labels for this one, as per test
)
RUN_RESOURCE_USAGE = prom.Gauge(
    "runner_resource_usage_percent",
    "Percentage of resource usage",
    ["resource_type", "instance_id"],
)
RUN_QUEUE = prom.Gauge(
    "runner_queue_length",
    "Current length of pending tasks in the queue",
    ["framework", "instance_id"],  # Added labels
)
HEALTH_STATUS = prom.Gauge(
    "runner_component_health_status",
    "Health status of a component (1=healthy, 0=unhealthy)",
    ["component_name", "instance_id"],
)
# --- END FIX ---

# --- App-level Metrics (used by main.py) ---
APP_RUNNING_STATUS = prom.Gauge(
    "app_running_status",
    "Application running status (1=running, 0=stopped)",
    ["app_name", "instance_id"],
)
APP_STARTUP_DURATION = prom.Histogram(
    "app_startup_duration_seconds",
    "Application startup duration in seconds",
    ["app_name", "instance_id"],
)
# --- END App-level Metrics ---

DISTRIBUTED_NODES_ACTIVE = prom.Gauge(
    "runner_distributed_nodes_active",
    "Number of active distributed nodes in the cluster",
)
DISTRIBUTED_LATENCY = prom.Histogram(
    "runner_distributed_latency_seconds",
    "Latency of distributed task submissions",
    ["instance_id"],
)

# --- FIX: ADDED MISSING UTILITY METRIC DEFINITIONS ---
# These metrics were being redefined in runner_logging.py, causing the error.
# They are now defined here as the single source of truth.
UTIL_LATENCY = prom.Histogram(
    "util_latency_seconds", "Util function latency", ["func", "status"]
)
UTIL_ERRORS = prom.Counter("util_errors", "Util errors", ["func", "type"])
UTIL_SELF_HEAL = prom.Counter("util_self_heal", "Self-healed operations", ["func"])
PROVENANCE_LOG_ENTRIES = prom.Counter(
    "provenance_log_entries_total", "Total provenance log entries", ["action"]
)
DASHBOARD_QUEUE_SIZE = prom.Gauge(
    "dashboard_log_queue_size", "Current size of the dashboard log queue"
)
ANOMALY_DETECTED_TOTAL = prom.Counter(
    "anomaly_detected_total", "Total anomalies detected", ["type", "severity"]
)
# --- END FIX ---

# --- Dependability & Quality Metrics ---
RUN_COVERAGE_PERCENT = prom.Gauge(
    "runner_overall_coverage_percent", "Overall code coverage percentage (0-1)"
)
RUN_VULNERABILITY_SCORE = prom.Gauge(
    "runner_overall_vulnerability_score",
    "Overall vulnerability score (lower is better, e.g., CVSS base score)",
)
RUN_AVG_TEST_LATENCY_HIST = prom.Histogram(
    "runner_individual_test_latency_seconds",
    "Distribution of individual test case latencies",
    ["framework", "instance_id"],
)
DOC_VALIDATION_STATUS = prom.Gauge(
    "runner_doc_validation_status",
    "Documentation validation status (1=pass, 0=fail)",
    ["doc_framework_name", "instance_id"],
)
DOC_GENERATION_ERRORS = prom.Counter(
    "runner_doc_generation_errors_total",
    "Total count of documentation generation errors",
    ["error_type", "doc_framework_name", "instance_id"],
)

# --- Mutation & Fuzzing Metrics ---
MUTATION_TOTAL = prom.Counter(
    "runner_mutation_total",
    "Total mutations generated",
    ["language", "strategy", "tool_name", "instance_id"],
)
MUTATION_KILLED = prom.Counter(
    "runner_mutation_killed_total",
    "Total mutations killed by tests",
    ["language", "strategy", "tool_name", "instance_id"],
)
MUTATION_SURVIVED = prom.Counter(
    "runner_mutation_survived_total",
    "Total mutations that survived tests",
    ["language", "strategy", "tool_name", "instance_id"],
)
MUTATION_TIMEOUT = prom.Counter(
    "runner_mutation_timeout_total",
    "Total mutations that timed out",
    ["language", "strategy", "tool_name", "instance_id"],
)
MUTATION_ERROR = prom.Counter(
    "runner_mutation_error_total",
    "Total mutations that caused an error",
    ["language", "strategy", "tool_name", "instance_id"],
)
# This is the one runner_mutation.py wants to import as MUTATION_SURVIVAL_RATE
RUN_MUTATION_SURVIVAL = prom.Gauge(
    "runner_mutation_survival_rate",
    "Latest mutation survival rate (0-1)",
    ["language", "strategy", "tool_name", "instance_id"],
)
# This is the one runner_mutation.py wants to import as FUZZ_DISCOVERIES
RUN_FUZZ_DISCOVERIES = prom.Counter(
    "runner_fuzz_discoveries_total",
    "Total count of issues discovered by fuzzing",
    ["language", "strategy", "instance_id"],
)
COVERAGE_GAPS = prom.Counter(
    "runner_mutation_coverage_gaps_total",
    "Total coverage gaps found during mutation",
    ["language", "instance_id"],
)

# --- Resilient Queuing Metrics ---
FAILED_EXPORT_QUEUE_SIZE = prom.Gauge(
    "runner_failed_export_queue_size", "Current size of failed export retry queue"
)
EXPORT_RETRY_ATTEMPTS = prom.Counter(
    "runner_export_retry_attempts_total",
    "Total number of export retry attempts",
    ["exporter", "success"],
)

# --- New Observability Metrics for Configuration and Task Lifecycle ---
RUNNER_CONFIG_RELOADS = prom.Counter(
    "runner_config_reloads_total",
    "Total count of times configuration has been reloaded",
)
RUNNER_CONFIG_VERSION = prom.Gauge(
    "runner_active_config_version",
    "Current active configuration schema version",
    ["version"],
)
RUNNER_TASK_STATUS = prom.Gauge(
    "runner_task_status_count", "Count of tasks by their current status", ["status"]
)

# In-memory history for anomaly detection. Stores (value, timestamp) for last X intervals/data points.
_METRIC_HISTORY: Dict[str, Deque[Tuple[float, datetime]]] = defaultdict(
    lambda: deque(maxlen=60)
)


# Alert thresholds (configurable via RunnerConfig)
ALERT_THRESHOLDS: Dict[str, Any] = {
    "error_rate": 0.1,
    "health_min": 1.0,
    "queue_max": 50,
    "resource_max": 90.0,
    "vulnerability_score_max": 7.0,
    "performance_latency_max": 5.0,
    "coverage_min": 0.7,
    "mutation_survival_max": 0.3,
    "doc_validation_fail": 0.5,
    "anomaly_detection_window": 5,
    "anomaly_detection_std_dev_multiplier": 2.0,
}

# --- Exporter Registry ---
_EXPORTER_REGISTRY: Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]] = (
    {}
)  # Exporter funcs are async


def register_exporter(
    name: str, export_func: Callable[[Dict[str, Any]], Awaitable[None]]
):
    """Registers a new metrics exporter function.
    Args:
        name (str): Unique name for the exporter (e.g., 'datadog', 'cloudwatch').
        export_func (Callable): An async function that takes a Dict[str, Any]
                                (the metrics snapshot) and exports it.
    """
    if name in _EXPORTER_REGISTRY:
        logger.warning(f"Metrics exporter '{name}' already registered. Overwriting.")
    _EXPORTER_REGISTRY[name] = export_func
    logger.info(f"Metrics exporter '{name}' registered.")


class MetricsExporter:
    """
    Handles collecting Prometheus metrics and exporting them to multiple external systems.
    Designed for resilience and configurability, including a retry mechanism and failover file.
    """

    def __init__(self, config: "RunnerConfig"):
        self.config = config
        self.instance_id: str = getattr(self.config, "instance_id", "unknown_instance")

        self._initialize_datadog_exporter()
        self._initialize_cloudwatch_exporter()
        self._initialize_custom_json_file_exporter()

        # Queue for failed exports that need retries with exponential backoff
        # Format: (metrics, exporter_name, retry_count, first_failure_timestamp, next_retry_time)
        self._failed_exports_queue: Deque[
            Tuple[Dict[str, Any], str, int, datetime, datetime]
        ] = deque()

        # FIX: Use getattr for config access
        self._max_export_retries = getattr(self.config, "max_metrics_export_retries", 3)
        self._export_retry_base_interval = getattr(
            self.config, "metrics_export_retry_interval_seconds", 5
        )
        self._export_retry_max_interval = getattr(
            self.config, "metrics_export_retry_max_interval_seconds", 60
        )
        self._export_retry_exponential_base = getattr(
            self.config, "metrics_export_retry_exponential_base", 2.0
        )

        # Failover file path (Path object)
        failover_file = getattr(self.config, "metrics_failover_file", None)
        self._failover_file_path: Optional[Path] = (
            Path(failover_file) if failover_file else None
        )

        # FIX: Start background task explicitly via start()
        self._retry_task: Optional[asyncio.Task] = None
        self._stop_evt = asyncio.Event()

    async def start(self):
        """Starts the background task for retrying failed metric exports."""
        if not self._retry_task or self._retry_task.done():
            self._stop_evt.clear()
            self._retry_task = asyncio.create_task(self._retry_failed_exports_loop())
            logger.info("Metrics exporter retry loop started.")

    def _calculate_retry_delay(self, retry_count: int) -> float:
        """
        Calculates exponential backoff delay for retry attempts.
        """
        delay = self._export_retry_base_interval * (
            self._export_retry_exponential_base**retry_count
        )
        return min(delay, self._export_retry_max_interval)

    async def _retry_failed_exports_loop(self):
        """
        Continuously retries failed metric export batches with exponential backoff.
        """
        while not self._stop_evt.is_set():
            # Check for stop event before sleeping
            if self._stop_evt.is_set():
                break

            await asyncio.sleep(1)  # Check queue frequently

            # Update queue size metric
            FAILED_EXPORT_QUEUE_SIZE.set(len(self._failed_exports_queue))

            if not self._failed_exports_queue:
                continue

            current_time = datetime.now(timezone.utc)
            logs_to_process = len(self._failed_exports_queue)
            logger.debug(
                f"Processing {logs_to_process} failed metric exports in retry queue."
            )

            # Process only items whose retry time has arrived
            for _ in range(logs_to_process):
                if (
                    self._stop_evt.is_set()
                ):  # Check for shutdown signal during processing
                    break
                if not self._failed_exports_queue:
                    break

                (
                    metrics_snapshot,
                    exporter_name,
                    retry_count,
                    first_failure_ts,
                    next_retry_time,
                ) = self._failed_exports_queue[0]

                if current_time < next_retry_time:
                    self._failed_exports_queue.rotate(-1)
                    continue

                self._failed_exports_queue.popleft()

                if retry_count >= self._max_export_retries:
                    logger.error(
                        f"Permanently dropping metrics batch for exporter '{exporter_name}' after {self._max_export_retries} retries. Writing to failover file if configured. Metrics: {metrics_snapshot.keys()}"
                    )

                    # --- FIX: Lazy import log_action ---
                    # --- FIX: Use relative import ---
                    from .runner_logging import log_action

                    log_action(
                        "MetricsExportDropped",
                        {
                            "exporter": exporter_name,
                            "reason": "max_retries_exceeded",
                            "metric_keys": list(metrics_snapshot.keys()),
                            "first_failure_timestamp": first_failure_ts.isoformat(),
                            "total_retries": retry_count,
                        },
                        extra={"instance_id": self.instance_id},
                    )
                    RUN_ERRORS.labels(
                        error_type="exporter_dropped_metrics",
                        backend=exporter_name,
                        instance_id=self.instance_id,
                    ).inc()

                    if self._failover_file_path:
                        await self._write_to_failover_file(
                            metrics_snapshot, exporter_name
                        )
                    continue

                delay = self._calculate_retry_delay(retry_count)

                logger.info(
                    f"Retrying export for '{exporter_name}' (attempt {retry_count + 1}/{self._max_export_retries}, next delay: {delay:.1f}s)..."
                )
                export_func = _EXPORTER_REGISTRY.get(exporter_name)

                if not export_func:
                    logger.error(
                        f"Exporter '{exporter_name}' not found in registry for retry. Dropping metrics."
                    )
                    RUN_ERRORS.labels(
                        error_type="exporter_missing_for_retry",
                        backend=exporter_name,
                        instance_id=self.instance_id,
                    ).inc()
                    if self._failover_file_path:
                        await self._write_to_failover_file(
                            metrics_snapshot, exporter_name, reason="exporter_missing"
                        )
                    continue

                retry_successful = False
                try:
                    if asyncio.iscoroutinefunction(export_func):
                        # Use a timeout based on the base interval, not the full max backoff
                        await asyncio.wait_for(
                            export_func(metrics_snapshot),
                            timeout=self._export_retry_base_interval * 2,
                        )
                    else:
                        await asyncio.wait_for(
                            asyncio.to_thread(export_func, metrics_snapshot),
                            timeout=self._export_retry_base_interval * 2,
                        )
                    logger.info(
                        f"Successfully retried export to '{exporter_name}' after {retry_count + 1} attempt(s)."
                    )
                    retry_successful = True
                    EXPORT_RETRY_ATTEMPTS.labels(
                        exporter=exporter_name, success="true"
                    ).inc()

                except asyncio.TimeoutError:
                    logger.warning(
                        f"Retry export to '{exporter_name}' timed out. Re-queueing with backoff."
                    )
                    RUN_ERRORS.labels(
                        error_type="exporter_retry_timeout",
                        backend=exporter_name,
                        instance_id=self.instance_id,
                    ).inc()
                    EXPORT_RETRY_ATTEMPTS.labels(
                        exporter=exporter_name, success="false"
                    ).inc()
                except Exception as e:
                    logger.warning(
                        f"Retry export to '{exporter_name}' failed: {e}. Re-queueing with backoff.",
                        exc_info=True,
                    )
                    RUN_ERRORS.labels(
                        error_type="exporter_retry_failure",
                        backend=exporter_name,
                        instance_id=self.instance_id,
                    ).inc()
                    EXPORT_RETRY_ATTEMPTS.labels(
                        exporter=exporter_name, success="false"
                    ).inc()

                if not retry_successful:
                    next_retry_at = current_time + timedelta(seconds=delay)
                    self._failed_exports_queue.append(
                        (
                            metrics_snapshot,
                            exporter_name,
                            retry_count + 1,
                            first_failure_ts,
                            next_retry_at,
                        )
                    )
                    logger.debug(
                        f"Re-queued failed export for '{exporter_name}'. Next retry at {next_retry_at.isoformat()}"
                    )

    async def _write_to_failover_file(
        self,
        metrics: Dict[str, Any],
        exporter_name: str,
        reason: str = "max_retries_exceeded",
    ):
        """Writes a failed metrics batch to a local file for later replay."""
        if not self._failover_file_path:
            logger.debug(
                f"Failover file not configured. Skipping write for {exporter_name}."
            )
            return

        try:
            self._failover_file_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(
                f"Failed to create directory for failover file {self._failover_file_path.parent}: {e}",
                exc_info=True,
            )
            RUN_ERRORS.labels(
                error_type="exporter_failover_dir_fail",
                backend=exporter_name,
                instance_id=self.instance_id,
            ).inc()
            return

        batch_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        failover_entry = {
            "timestamp": timestamp,
            "exporter": exporter_name,
            "reason": reason,
            "batch_id": batch_id,
            "metrics": metrics,
        }

        try:
            async with aiofiles.open(
                self._failover_file_path, mode="a", encoding="utf-8"
            ) as f:
                await f.write(json.dumps(failover_entry, ensure_ascii=False) + "\n")
            logger.critical(
                f"Wrote failed metrics batch ({batch_id}) for '{exporter_name}' to failover file: {self._failover_file_path}"
            )

            # --- FIX: Lazy import log_action ---
            # --- FIX: Use relative import ---
            from .runner_logging import log_action

            log_action(
                "MetricsFailoverWrite",
                {
                    "file_path": str(self._failover_file_path),
                    "exporter": exporter_name,
                    "batch_id": batch_id,
                    "reason": reason,
                },
                extra={"instance_id": self.instance_id},
            )
        except Exception as e:
            logger.error(
                f"Failed to write metrics batch to failover file {self._failover_file_path}: {e}",
                exc_info=True,
            )
            RUN_ERRORS.labels(
                error_type="exporter_failover_write_fail",
                backend=exporter_name,
                instance_id=self.instance_id,
            ).inc()

    def _initialize_datadog_exporter(self):
        """Initializes the Datadog exporter if configured."""
        # FIX: Use getattr
        datadog_api_key = getattr(self.config, "datadog_api_key", None)
        if isinstance(datadog_api_key, SecretStr):
            datadog_api_key = datadog_api_key.get_secret_value()

        if not datadog_api_key:
            logger.debug(
                "Datadog API key not configured. Datadog exporter will not be initialized."
            )
            return

        try:
            if datadog:
                if not datadog.api.initialized:
                    datadog_options = {
                        "api_key": datadog_api_key,
                        "app_key": getattr(
                            self.config, "datadog_app_key", ""
                        ),  # FIX: Use getattr
                        "host_name": self.instance_id,
                    }
                    datadog.initialize(**datadog_options)
                    logger.info("Datadog SDK (v1) initialized successfully.")

                async def datadog_export_wrapper(metrics_snapshot: Dict[str, Any]):
                    await asyncio.to_thread(
                        self._export_to_datadog_sync, metrics_snapshot
                    )

                register_exporter("datadog", datadog_export_wrapper)
                logger.info("Datadog exporter (legacy client) registered.")
            else:
                logger.warning(
                    "Datadog API key configured but SDK not available for direct export. Install 'datadog'."
                )
                RUN_ERRORS.labels(
                    error_type="exporter_init_fail",
                    backend="datadog",
                    instance_id=self.instance_id,
                ).inc()
        except Exception as e:
            logger.error(f"Failed to initialize Datadog exporter: {e}", exc_info=True)
            RUN_ERRORS.labels(
                error_type="exporter_init_fail",
                backend="datadog",
                instance_id=self.instance_id,
            ).inc()

    def _export_to_datadog_sync(self, metrics: Dict[str, Any]):
        """Synchronous part of Datadog export, meant to be run in a thread."""
        if datadog and datadog.api:
            try:
                for name, value in metrics.items():
                    # Datadog metric naming convention adjustments
                    dd_name = (
                        name.replace("runner_", "app.runner.")
                        .replace("_total", ".count")
                        .replace("_percent", ".percent")
                    )

                    if isinstance(value, (int, float)) and not (
                        value != value
                        or value == float("inf")
                        or value == float("-inf")
                    ):
                        dd_type = "count" if name.endswith("_total") else "gauge"
                        datadog.api.Metric.send(
                            metric=dd_name,
                            points=value,
                            type=dd_type,
                            host=self.instance_id,
                        )
                logger.debug(f"Exported {len(metrics)} metrics to Datadog.")
            except Exception as e:
                # Lazy import ExporterError
                if typing.TYPE_CHECKING:
                    # --- FIX: Use relative import ---
                    from .runner_errors import ExporterError
                else:
                    # If not type checking, we must import it dynamically
                    try:
                        # --- FIX: Use relative import ---
                        from .runner_errors import ExporterError
                    except ImportError:
                        ExporterError = Exception  # Fallback
                # Raise ExporterError for the retry logic to catch
                raise ExporterError(
                    get_error_codes()["EXPORTER_FAILURE"],
                    detail=f"Datadog export failed: {e}",
                    exporter_name="datadog",
                    cause=e,
                )

    def _initialize_cloudwatch_exporter(self):
        """Initializes the AWS CloudWatch exporter if configured."""
        # FIX: Use getattr
        if not getattr(self.config, "aws_region", None):
            logger.debug(
                "AWS region not configured. CloudWatch exporter will not be initialized."
            )
            return

        try:
            if not boto3:
                raise ImportError("boto3 package not installed for CloudWatch export.")

            # FIX: Use getattr
            aws_access_key = getattr(self.config, "aws_access_key_id", None)
            aws_secret_key = getattr(self.config, "aws_secret_access_key", None)

            if isinstance(aws_access_key, SecretStr):
                aws_access_key = aws_access_key.get_secret_value()
            if isinstance(aws_secret_key, SecretStr):
                aws_secret_key = aws_secret_key.get_secret_value()

            # FIX: Ensure region is retrieved correctly
            cw_region = getattr(self.config, "aws_region", None)
            if not cw_region:
                logger.warning(
                    "AWS region configured but is None/empty. CloudWatch exporter aborted."
                )
                return

            self.cw_client = boto3.client(
                "cloudwatch",
                region_name=cw_region,
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key,
            )
            # Perform a quick test call to verify credentials/connectivity
            try:
                self.cw_client.get_metric_statistics(
                    Namespace="AWS/EC2",
                    MetricName="CPUUtilization",
                    StartTime=datetime.now(timezone.utc) - timedelta(minutes=5),
                    EndTime=datetime.now(timezone.utc),
                    Period=300,
                    Statistics=["Average"],
                    Dimensions=[
                        {"Name": "InstanceId", "Value": "i-xxxxxxxxxxxxxxxxx"}
                    ],  # Dummy ID for test
                )
            except BotoClientError as e:
                logger.error(
                    f"CloudWatch test call failed during init: {e}. Check AWS credentials/permissions.",
                    exc_info=True,
                )
                RUN_ERRORS.labels(
                    error_type="exporter_init_fail",
                    backend="cloudwatch_test_call",
                    instance_id=self.instance_id,
                ).inc()
                self.cw_client = None
                return

            async def cloudwatch_export_wrapper(metrics_snapshot: Dict[str, Any]):
                await asyncio.to_thread(
                    self._export_to_cloudwatch_sync, metrics_snapshot
                )

            register_exporter("cloudwatch", cloudwatch_export_wrapper)
            logger.info(f"AWS CloudWatch exporter initialized for region: {cw_region}.")
        except ImportError as ie:
            logger.warning(f"CloudWatch exporter: {ie}. Install 'boto3'.")
            RUN_ERRORS.labels(
                error_type="exporter_init_fail",
                backend="cloudwatch",
                instance_id=self.instance_id,
            ).inc()
        except Exception as e:
            logger.error(
                f"Unexpected error initializing AWS CloudWatch exporter: {e}",
                exc_info=True,
            )
            RUN_ERRORS.labels(
                error_type="exporter_init_fail",
                backend="cloudwatch",
                instance_id=self.instance_id,
            ).inc()

    def _export_to_cloudwatch_sync(self, metrics: Dict[str, Any]):
        """Synchronous part of CloudWatch export, meant to be run in a thread."""
        if self.cw_client:
            metric_data: List[Dict[str, Any]] = []
            for name, value in metrics.items():
                if isinstance(value, (int, float)) and not (
                    value != value or value == float("inf") or value == float("-inf")
                ):
                    metric_data.append(
                        {
                            "MetricName": name,
                            "Value": value,
                            "Unit": "None",  # Unit logic is complex, defaulting to None for now
                            "Dimensions": [
                                {"Name": "InstanceId", "Value": self.instance_id}
                            ],
                        }
                    )

            if metric_data:
                batch_size = (
                    20  # CloudWatch API limit is 20 metrics per PutMetricData call
                )
                for i in range(0, len(metric_data), batch_size):
                    batch = metric_data[i : i + batch_size]
                    try:
                        self.cw_client.put_metric_data(
                            Namespace="RunnerMetrics", MetricData=batch
                        )
                        logger.debug(
                            f"Exported {len(batch)} metrics batch to CloudWatch."
                        )
                    except BotoClientError as e:
                        # Lazy import ExporterError
                        if typing.TYPE_CHECKING:
                            # --- FIX: Use relative import ---
                            from .runner_errors import ExporterError
                        else:
                            try:
                                # --- FIX: Use relative import ---
                                from .runner_errors import ExporterError
                            except ImportError:
                                ExporterError = Exception
                        # Raise ExporterError for the retry logic to catch
                        raise ExporterError(
                            get_error_codes()["EXPORTER_FAILURE"],
                            detail=f"CloudWatch export failed: {e}",
                            exporter_name="cloudwatch",
                            cause=e,
                        )
                    except Exception as e:
                        if typing.TYPE_CHECKING:
                            # --- FIX: Use relative import ---
                            from .runner_errors import ExporterError
                        else:
                            try:
                                # --- FIX: Use relative import ---
                                from .runner_errors import ExporterError
                            except ImportError:
                                ExporterError = Exception
                        raise ExporterError(
                            get_error_codes()["EXPORTER_FAILURE"],
                            detail=f"CloudWatch export failed: {e}",
                            exporter_name="cloudwatch",
                            cause=e,
                        )
        else:
            logger.debug("CloudWatch exporter not active for direct send.")

    def _initialize_custom_json_file_exporter(self):
        """Initializes the custom JSON file exporter (always available)."""

        async def json_file_export_wrapper(metrics_snapshot: Dict[str, Any]):
            await asyncio.to_thread(
                self._export_to_custom_json_file_sync, metrics_snapshot
            )

        register_exporter("custom_json_file", json_file_export_wrapper)
        logger.info("Custom JSON file exporter registered.")

    def _export_to_custom_json_file_sync(self, metrics: Dict[str, Any]):
        """Synchronous part of JSON file export, meant to be run in a thread."""
        # FIX: Use getattr
        metrics_file_path = getattr(
            self.config, "custom_metrics_file", "metrics_snapshot.json"
        )
        try:
            with open(metrics_file_path, "w", encoding="utf-8") as f:
                json.dump(metrics, f, indent=2)
            logger.debug(f"Exported metrics to custom file: {metrics_file_path}.")

            # --- FIX: Lazy import log_action ---
            # --- FIX: Use relative import ---
            from .runner_logging import log_action

            log_action(
                "MetricsExportedToFile",
                {"file_path": metrics_file_path, "metric_count": len(metrics)},
                extra={"instance_id": self.instance_id},
            )
        except Exception as e:
            if typing.TYPE_CHECKING:
                # --- FIX: Use relative import ---
                from .runner_errors import ExporterError
            else:
                try:
                    # --- FIX: Use relative import ---
                    from .runner_errors import ExporterError
                except ImportError:
                    ExporterError = Exception
            raise ExporterError(
                get_error_codes()["EXPORTER_FAILURE"],
                detail=f"Custom JSON file export failed: {e}",
                exporter_name="custom_json_file",
                cause=e,
            )

    def export_prometheus(self):
        """Prometheus metrics are automatically exposed via the HTTP server started globally."""
        pass

    def _sanitize_metrics(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitizes metrics before export: ensures numeric values, safe labels,
        and redacts any potentially sensitive string values.
        """
        # FIX: Lazy import redact_secrets here to break the cycle (runner_logging -> runner_security_utils -> runner_metrics -> runner_logging)
        try:
            # --- FIX: Use relative import ---
            from .runner_security_utils import redact_secrets
        except ImportError:
            # Fallback if security utils is not available
            def redact_secrets(text, patterns=None):  # Adjusted fallback signature
                return text  # No redaction

            logger.warning(
                "runner.runner_security_utils.redact_secrets could not be imported. Security redaction disabled."
            )

        sanitized = {}
        for key, value in metrics.items():
            if isinstance(value, str):
                # FIX: Use getattr
                sanitized[key] = redact_secrets(
                    value,
                    patterns=getattr(self.config, "custom_redaction_patterns", []),
                )
            elif isinstance(value, (int, float)):
                if value != value or value == float("inf") or value == float("-inf"):
                    logger.warning(
                        f"Sanitizing non-finite metric value for {key}: {value}. Setting to 0.0."
                    )
                    sanitized[key] = 0.0
                else:
                    sanitized[key] = value
            else:
                # --- [FIX for alert_monitor TypeError] ---
                # If the value is a dict (from get_metrics_dict),
                # pass it through. The sanitizer shouldn't be
                # responsible for flattening it.
                if isinstance(value, dict):
                    sanitized[key] = value
                else:
                    logger.debug(
                        f"Skipping non-numeric/non-string metric {key} with type {type(value)}"
                    )
                # --- [END FIX] ---
        return sanitized

    async def export_all(self):
        """Collects all current Prometheus metrics and exports them to all configured systems."""
        metrics_snapshot = get_metrics_dict()
        sanitized_metrics_snapshot = self._sanitize_metrics(metrics_snapshot)

        export_tasks: List[Awaitable[Any]] = []
        for exporter_name, export_func in _EXPORTER_REGISTRY.items():

            async def _run_export_safely(
                exp_name: str,
                exp_func: Callable[[Dict[str, Any]], Awaitable[None]],
                metrics_snap: Dict[str, Any],
            ):
                # Lazy import log_action inside the async function body is safer for the lifecycle
                try:
                    # --- FIX: Use relative import ---
                    from .runner_logging import log_action
                except ImportError:

                    def log_action(*a, **k):
                        pass  # Dummy log_action

                # Lazy import error types
                if typing.TYPE_CHECKING:
                    # --- FIX: Use relative import ---
                    from .runner_errors import ExporterError, RunnerError
                else:
                    try:
                        # --- FIX: Use relative import ---
                        from .runner_errors import ExporterError, RunnerError
                    except ImportError:
                        RunnerError = Exception
                        ExporterError = Exception

                try:

                    log_action(
                        "MetricsExportAttempt",
                        {"exporter": exp_name, "metric_count": len(metrics_snap)},
                        extra={"instance_id": self.instance_id},
                    )

                    await exp_func(metrics_snap)
                    log_action(
                        "MetricsExportSuccess",
                        {"exporter": exp_name},
                        extra={"instance_id": self.instance_id},
                    )

                except ExporterError as e:
                    logger.error(
                        f"Structured error exporting metrics to '{exp_name}': {e.as_dict()}",
                        exc_info=True,
                    )

                    # --- FIX: Lazy import log_action ---
                    log_action(
                        "MetricsExportFailure",
                        e.as_dict(),
                        extra={"instance_id": self.instance_id},
                    )
                    current_time = datetime.now(timezone.utc)
                    next_retry = current_time + timedelta(
                        seconds=self._export_retry_base_interval
                    )
                    self._failed_exports_queue.append(
                        (metrics_snap, exp_name, 0, current_time, next_retry)
                    )
                    FAILED_EXPORT_QUEUE_SIZE.set(len(self._failed_exports_queue))
                    RUN_ERRORS.labels(
                        error_type="exporter_send_failure",
                        backend=exp_name,
                        instance_id=self.instance_id,
                    ).inc()
                except Exception as e:
                    logger.error(
                        f"Unexpected error exporting metrics to '{exp_name}': {e}",
                        exc_info=True,
                    )

                    # Lazy import RunnerError
                    if typing.TYPE_CHECKING:
                        # --- FIX: Use relative import ---
                        from .runner_errors import RunnerError
                    else:
                        try:
                            # --- FIX: Use relative import ---
                            from .runner_errors import RunnerError
                        except ImportError:
                            RunnerError = Exception

                    error_dict = RunnerError(
                        "EXPORTER_UNEXPECTED_ERROR",
                        f"Unexpected error in exporter {exp_name}: {e}",
                        exporter_name=exp_name,
                        cause=e,
                    ).as_dict()

                    # --- FIX: Lazy import log_action ---
                    log_action(
                        "MetricsExportFailure",
                        error_dict,
                        extra={"instance_id": self.instance_id},
                    )
                    current_time = datetime.now(timezone.utc)
                    next_retry = current_time + timedelta(
                        seconds=self._export_retry_base_interval
                    )
                    self._failed_exports_queue.append(
                        (metrics_snap, exp_name, 0, current_time, next_retry)
                    )
                    FAILED_EXPORT_QUEUE_SIZE.set(len(self._failed_exports_queue))
                    RUN_ERRORS.labels(
                        error_type="exporter_send_failure",
                        backend=exp_name,
                        instance_id=self.instance_id,
                    ).inc()

            export_tasks.append(
                _run_export_safely(
                    exporter_name, export_func, sanitized_metrics_snapshot
                )
            )

        if export_tasks:
            await asyncio.gather(*export_tasks, return_exceptions=True)
            logger.debug(
                f"All configured metrics exports ({len(export_tasks)} total) initiated."
            )
        else:
            logger.debug("No metrics exporters are registered.")

    async def shutdown(self):
        """Gracefully shuts down the MetricsExporter and its background tasks."""
        logger.info(
            "MetricsExporter shutdown initiated. Flushing any remaining failed exports."
        )
        self._stop_evt.set()

        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._retry_task
            self._retry_task = None
            logger.debug("Metrics exporter retry task cancelled successfully.")

        if self._failed_exports_queue:
            logger.info(
                f"Attempting final flush of {len(self._failed_exports_queue)} failed metric exports before shutdown."
            )

            current_failed_exports = list(self._failed_exports_queue)
            self._failed_exports_queue.clear()

            # Lazy import log_action here for the flush loop
            try:
                # --- FIX: Use relative import ---
                from .runner_logging import log_action
            except ImportError:

                def log_action(*a, **k):
                    pass  # Dummy log_action

            for (
                metrics_snapshot,
                exporter_name,
                retry_count,
                first_failure_ts,
                next_retry_time,
            ) in current_failed_exports:
                export_func = _EXPORTER_REGISTRY.get(exporter_name)
                if export_func:
                    try:
                        await asyncio.wait_for(
                            export_func(metrics_snapshot),
                            timeout=self._export_retry_base_interval,
                        )
                        logger.info(
                            f"Successfully flushed remaining metrics to '{exporter_name}' on shutdown."
                        )
                        log_action(
                            "MetricsFlushSuccess",
                            {"exporter": exporter_name},
                            extra={"instance_id": self.instance_id},
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed final flush to '{exporter_name}' on shutdown: {e}."
                        )
                        log_action(
                            "MetricsFlushFailure",
                            {"exporter": exporter_name, "reason": str(e)},
                            extra={"instance_id": self.instance_id},
                        )
                        if self._failover_file_path:
                            await self._write_to_failover_file(
                                metrics_snapshot,
                                exporter_name,
                                reason="final_flush_failed_on_shutdown",
                            )
                else:
                    logger.error(
                        f"Exporter '{exporter_name}' not found for final shutdown flush. Metrics lost."
                    )
                    log_action(
                        "MetricsFlushFailure",
                        {"exporter": exporter_name, "reason": "exporter_missing"},
                        extra={"instance_id": self.instance_id},
                    )
                    if self._failover_file_path:
                        await self._write_to_failover_file(
                            metrics_snapshot,
                            exporter_name,
                            reason="exporter_missing_on_shutdown",
                        )

        logger.info("MetricsExporter shutdown complete.")


# Assuming DOC_FRAMEWORKS_FOR_ALERTS is managed centrally.
try:
    # FIX: Attempt to import from runner.runner_core first, then runner.core
    try:
        # --- FIX: Use relative import ---
        from .runner_core import DOC_FRAMEWORKS as CORE_DOC_FRAMEWORKS
    except ImportError:
        # --- FIX: Use relative import ---
        from .core import DOC_FRAMEWORKS as CORE_DOC_FRAMEWORKS
    DOC_FRAMEWORKS_FOR_ALERTS: List[str] = list(CORE_DOC_FRAMEWORKS.keys())
except ImportError:
    DOC_FRAMEWORKS_FOR_ALERTS: List[str] = [
        "sphinx",
        "mkdocs",
        "javadoc",
        "jsdoc",
        "go_doc",
    ]


async def alert_monitor(config: "RunnerConfig"):
    """
    Built-in alerting system: Periodically checks metrics against defined thresholds and logs critical alerts.
    """
    logger.info("Alert monitor started.")
    # FIX: Use getattr
    alert_interval_seconds = getattr(config, "alert_monitor_interval_seconds", 60)

    while True:
        metrics = get_metrics_dict()
        alerts: List[str] = []

        instance_id: str = config.instance_id

        current_thresholds = ALERT_THRESHOLDS.copy()
        for key in current_thresholds.keys():
            config_key_name = f"alert_threshold_{key}"
            # FIX: Use getattr
            config_val = getattr(config, config_key_name, None)
            if config_val is not None:
                current_thresholds[key] = config_val

        current_timestamp = datetime.now(timezone.utc)
        for metric_name, value in metrics.items():
            if isinstance(value, (int, float)):
                _METRIC_HISTORY[metric_name].append((float(value), current_timestamp))

        # NOTE: Calculating total runs from specific labelled metrics is an approximation.
        # It should rely on the raw Counter object for a true total, but Prometheus
        # export only provides the current value/sum/count for histograms.
        # For simplicity, we use the exported dict value here.

        # --- [FIX for TypeError] ---
        # Helper to sum values from a metric dict or return the float
        def sum_metric_values(metric_value: Any) -> float:
            if isinstance(metric_value, dict):
                return sum(metric_value.values())
            elif isinstance(metric_value, (int, float)):
                return float(metric_value)
            return 0.0

        # Calculate overall error rate based on the sum of all RUN_SUCCESS/RUN_FAILURE/RUN_ERRORS (approx)
        total_runs = sum_metric_values(
            metrics.get("runner_successful_runs_total", 0.0)
        ) + sum_metric_values(metrics.get("runner_failed_runs_total", 0.0))

        total_errors_from_runs_dict = sum_metric_values(
            metrics.get("runner_errors_total", 0.0)
        )
        # --- [END FIX] ---

        # Error rate calculation needs refinement in a real system but this uses the available dict info
        error_rate_total_base = total_runs + total_errors_from_runs_dict
        error_rate = (
            total_errors_from_runs_dict / error_rate_total_base
            if error_rate_total_base > 0
            else 0.0
        )

        if error_rate > current_thresholds["error_rate"]:
            alerts.append(
                f"High error rate: {error_rate:.2f} (>{current_thresholds['error_rate']})."
            )

        # Health checks
        for component_name in [
            "overall",
            "backend",
            "queue",
            "exporter",
            "config_watcher",
        ]:
            # The get_metrics_dict flattens simple labelled metrics, so we check the dictionary structure
            health_statuses = metrics.get("runner_component_health_status", {})
            # Look for the specific label combo for health status
            current_health = health_statuses.get(
                f"component_name_{component_name}_instance_id_{instance_id}", 1.0
            )
            if current_health < current_thresholds["health_min"]:
                alerts.append(
                    f"Component '{component_name}' health degraded: {current_health} (<{current_thresholds['health_min']})."
                )

        queue_length_metrics = metrics.get("runner_queue_length", {})
        queue_length = sum(
            queue_length_metrics.values()
        )  # Sum all queue lengths regardless of labels
        if queue_length > current_thresholds["queue_max"]:
            alerts.append(
                f"Queue overload: {queue_length} (>{current_thresholds['queue_max']})."
            )

        for res_type in ["cpu", "mem"]:
            # The get_metrics_dict logic handles this specific metric by flattening it to resource_cpu_percent etc.
            usage = metrics.get(f"resource_{res_type}_percent", 0.0)
            if usage > current_thresholds["resource_max"]:
                alerts.append(
                    f"High {res_type.upper()} usage: {usage:.2f}% (>{current_thresholds['resource_max']}%)."
                )

        # --- Dependability Alerts ---
        vuln_score = metrics.get("runner_overall_vulnerability_score", 0.0)
        if vuln_score > current_thresholds["vulnerability_score_max"]:
            alerts.append(
                f"High vulnerability score: {vuln_score:.1f} (>{current_thresholds['vulnerability_score_max']:.1f}). Security risk detected."
            )

        # FIX: Use getattr
        framework_for_latency = getattr(config, "framework", "unknown")

        # Histogram metrics appear as separate entries in get_metrics_dict
        latency_hist_sums = metrics.get(
            "runner_individual_test_latency_seconds_sum", {}
        )
        latency_hist_counts = metrics.get(
            "runner_individual_test_latency_seconds_count", {}
        )

        label_key = f"framework_{framework_for_latency}_instance_id_{instance_id}"

        avg_test_latency_sum = latency_hist_sums.get(label_key, 0.0)
        avg_test_latency_count = latency_hist_counts.get(label_key, 0.0)

        avg_test_latency = avg_test_latency_sum / max(1.0, avg_test_latency_count)
        if avg_test_latency > current_thresholds["performance_latency_max"]:
            alerts.append(
                f"High average test latency: {avg_test_latency:.2f}s (>{current_thresholds['performance_latency_max']}s). Performance degradation."
            )

        coverage_percent = metrics.get("runner_overall_coverage_percent", 0.0)
        if coverage_percent < current_thresholds["coverage_min"]:
            alerts.append(
                f"Low code coverage: {coverage_percent:.2f} (<{current_thresholds['coverage_min']}). Potential quality issue."
            )

        mutation_survival_rate = metrics.get("runner_mutation_survival_rate", 0.0)
        if mutation_survival_rate > current_thresholds["mutation_survival_max"]:
            alerts.append(
                f"High mutation survival rate: {mutation_survival_rate:.2f} (>{current_thresholds['mutation_survival_max']}). Tests might be weak."
            )

        for doc_fw in DOC_FRAMEWORKS_FOR_ALERTS:
            doc_validation_statuses = metrics.get("runner_doc_validation_status", {})
            doc_status = doc_validation_statuses.get(
                f"doc_framework_name_{doc_fw}_instance_id_{instance_id}", 1.0
            )
            if doc_status < current_thresholds["doc_validation_fail"]:
                alerts.append(
                    f"Documentation validation failed for '{doc_fw}'. Critical doc integrity issue."
                )

        # --- Anomaly Detection ---
        # The key in _METRIC_HISTORY needs to be a simple string, using the internal helper for consistency
        cpu_metric_key = _get_canonical_metric_key(
            "runner_resource_usage_percent",
            {"resource_type": "cpu", "instance_id": instance_id},
        )
        cpu_history = _METRIC_HISTORY[cpu_metric_key]

        anomaly_window = int(current_thresholds["anomaly_detection_window"])
        std_dev_multiplier = float(
            current_thresholds["anomaly_detection_std_dev_multiplier"]
        )

        # Check for the raw value as flattened in get_metrics_dict
        current_cpu_value = metrics.get("resource_cpu_percent", 0.0)

        if len(cpu_history) >= anomaly_window:
            # We take the *window size* values from the end of the deque
            [val for val, _ in list(cpu_history)[-anomaly_window:]]

            # Recalculate mean/std_dev from the window *excluding* the very latest point if it was just added
            # The current_cpu_value is already the *latest* value. The history contains up to `anomaly_window`
            # points. For a real-time check, we should check the current value against the stats of the history.
            # Using all points in the deque (up to maxlen) to calculate the stats is a simpler approximation.

            all_history_values = [val for val, _ in cpu_history]
            if len(all_history_values) > 1:
                mean_cpu = sum(all_history_values) / len(all_history_values)
                # Use numpy or scipy for real STD DEV, but implement basic for no external deps
                variance = sum((x - mean_cpu) ** 2 for x in all_history_values) / (
                    len(all_history_values)
                )  # Population variance
                std_dev_cpu = variance**0.5

                if (
                    std_dev_cpu > 0.001
                    and abs(current_cpu_value - mean_cpu)
                    > std_dev_multiplier * std_dev_cpu
                ):
                    alerts.append(
                        f"CPU usage anomaly detected: {current_cpu_value:.2f}% (Mean: {mean_cpu:.2f}%, StdDev: {std_dev_cpu:.2f})."
                    )

                    # --- FIX: Lazy import log_action ---
                    try:
                        # --- FIX: Use relative import ---
                        from .runner_logging import log_action
                    except ImportError:

                        def log_action(*a, **k):
                            pass  # Dummy log_action

                    log_action(
                        "Anomaly_Detected",
                        {
                            "metric": "CPU_Usage",
                            "value": current_cpu_value,
                            "mean": mean_cpu,
                            "std_dev": std_dev_cpu,
                            "threshold_multiplier": std_dev_multiplier,
                            "instance_id": instance_id,
                        },
                        extra={
                            "alert_type": "anomaly",
                            "metric_name": "cpu_usage",
                            "instance_id": instance_id,
                        },
                    )

        if alerts:
            timestamp = datetime.now(timezone.utc).isoformat() + "Z"
            for alert_msg in alerts:
                # Use logger.critical which is expected to be picked up by external systems
                logger.critical(
                    json.dumps(
                        {
                            "event": "ALERT_TRIGGERED",
                            "timestamp": timestamp,
                            "instance_id": instance_id,
                            "message": alert_msg,
                            "alert_type": "system_dependability",
                            "triggered_metrics": {},  # Removed metrics dump to avoid huge log lines
                        }
                    )
                )
                # Placeholder for external notification
                # asyncio.create_task(send_external_alert_notification(...))

        try:
            await asyncio.sleep(alert_interval_seconds)
        except asyncio.CancelledError:
            logger.info("Alert monitor received shutdown signal.")
            break


# Assuming DOC_FRAMEWORKS_FOR_ALERTS is managed centrally
try:
    # FIX: Prioritize runner.runner_core
    try:
        # --- FIX: Use relative import ---
        from .runner_core import DOC_FRAMEWORKS as CORE_DOC_FRAMEWORKS
    except ImportError:
        # --- FIX: Use relative import ---
        from .core import DOC_FRAMEWORKS as CORE_DOC_FRAMEWORKS
    DOC_FRAMEWORKS_FOR_ALERTS: List[str] = list(CORE_DOC_FRAMEWORKS.keys())
except ImportError:
    DOC_FRAMEWORKS_FOR_ALERTS: List[str] = [
        "sphinx",
        "mkdocs",
        "javadoc",
        "jsdoc",
        "go_doc",
    ]


def get_metrics_dict() -> Dict[str, Any]:
    """
    Retrieves all current Prometheus metrics from the default registry
    and formats them into a simple, nested dictionary for easy API/internal consumption.
    """
    metrics_data = {}
    for collector in prom.REGISTRY.collect():
        for metric in collector.samples:
            name = metric.name
            labels = metric.labels
            value = metric.value  # Get the value

            # Skip non-finite values which cause issues with JSON
            if not (
                isinstance(value, (int, float))
                and (
                    value == value and value != float("inf") and value != float("-inf")
                )
            ):
                continue

            # Skip internal Prometheus metrics that aren't useful for business logic
            if name.startswith("process_") or name.startswith("python_"):
                continue

            # Helper for label-less metrics
            if not labels:
                metrics_data[name] = value
                # --- [FIX] ---
                # REMOVED: continue
                # We must allow logic to proceed to handle cases where
                # a metric has BOTH label-less and labeled values.
                # --- [END FIX] ---

            # Special case for resource usage to flatten it to a single-level key
            if name == "runner_resource_usage_percent":
                resource_type = labels.get("resource_type")
                if resource_type:
                    metrics_data[f"resource_{resource_type}_percent"] = value
                    continue

            # Default handling for labelled metrics: nest under the metric name, with a label-key
            if name not in metrics_data:
                metrics_data[name] = {}

            # Create a simple, non-nested string key for the labels for easier lookup
            # Sort the labels for a canonical key
            label_key = "_".join(f"{k}_{v}" for k, v in sorted(labels.items()))

            # Only add if labels exist, otherwise it was handled by the label-less block
            if label_key:
                metrics_data[name][label_key] = value

    return metrics_data


def _get_canonical_metric_key(metric_name: str, labels: Dict[str, str]) -> str:
    """Helper for internal metric key generation (used by history/anomaly)."""
    # This format is chosen to be unique for the history dict key
    label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{metric_name}{{{label_str}}}"


# --- Metrics for Provider-Specific Streaming ---
# These are used by local_provider, grok_provider, gemini_provider
stream_chunks_total = prom.Counter(
    "llm_stream_chunks_total", "Total number of stream chunks", ["model"]
)
stream_chunk_latency = prom.Histogram(
    "llm_stream_chunk_latency_seconds", "Latency per stream chunk in seconds", ["model"]
)

# --- Backward Compatibility Aliases ---
# These aliases maintain compatibility with older code that uses the old metric names.
# The metrics were renamed for consistency, but we provide aliases to prevent breaking changes.
LLM_CALLS_TOTAL = LLM_REQUESTS_TOTAL  # Renamed to LLM_REQUESTS_TOTAL
LLM_TOKEN_INPUT_TOTAL = LLM_TOKENS_INPUT  # Renamed to LLM_TOKENS_INPUT
LLM_TOKEN_OUTPUT_TOTAL = LLM_TOKENS_OUTPUT  # Renamed to LLM_TOKENS_OUTPUT


# --- Bootstrap Function (used by main.py) ---
def bootstrap_metrics() -> None:
    """
    Initialize all metrics with default values.
    This ensures metrics are registered with the Prometheus registry
    before they are accessed by get_metrics_dict().
    """
    # Initialize app-level metrics with default values
    # Using a default instance_id to ensure they appear in the registry
    default_instance = os.getenv("INSTANCE_ID", "default")
    
    # Initialize APP_RUNNING_STATUS
    APP_RUNNING_STATUS.labels(app_name="main", instance_id=default_instance).set(0)
    
    # Initialize APP_STARTUP_DURATION (observe a zero value to register it)
    # Histograms don't need explicit initialization, but we can observe a placeholder
    
    # Initialize other commonly used metrics with default values
    HEALTH_STATUS.labels(component_name="main", instance_id=default_instance).set(1)
    
    logger.debug("Metrics bootstrapped with default values.")
