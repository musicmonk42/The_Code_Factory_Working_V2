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

import prometheus_client as prom
import asyncio
import logging
import json
import os
import re
import time
from pathlib import Path
# FIX: Added 'Awaitable' and 'contextlib'
from typing import Dict, Any, Optional, List, Callable, Tuple, Deque, Awaitable
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import uuid
import aiofiles
import contextlib

# OpenTelemetry is used for tracing in other modules, but good to acknowledge
try:
    import opentelemetry.trace as trace
    _tracer = trace.get_tracer(__name__)
except ImportError:
    _tracer = None
    logging.getLogger(__name__).warning("OpenTelemetry not installed. Tracing for metrics will be disabled.")


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
from runner.runner_config import RunnerConfig, SecretStr
from runner.runner_logging import logger, log_action
# Gold Standard: Import structured errors and error codes
from runner.runner_errors import RunnerError, ExporterError, PersistenceError
from runner.runner_errors import ERROR_CODE_REGISTRY as error_codes # Import the error code registry

logger = logging.getLogger(__name__)

# --- Prometheus Server Bootstrap ---
_prom_started = False
METRICS_PORT = int(os.getenv('METRICS_PORT', 8006))

def start_prometheus_server_once(port: int = METRICS_PORT):
    """Starts the Prometheus HTTP server on the given port, but only once."""
    global _prom_started
    if _prom_started:
        return
    try:
        addr = '0.0.0.0' if os.getenv('PROMETHEUS_BIND_ALL', 'false').lower() == 'true' else '127.0.0.1'
        prom.start_http_server(port, addr=addr)
        logger.info(f"Prometheus metrics HTTP server started on port {port}. Access at http://localhost:{port}/metrics")
        _prom_started = True
        # SECURITY NOTE: If exposed externally, ensure proper network security (firewall, mTLS, IP allowlisting, or service mesh protection).
    except OSError as e:
        logger.warning(f"Failed to start Prometheus HTTP server on port {port}: {e}. It might already be running or port is in use.")
    except Exception as e:
        logger.error(f"Unexpected error starting Prometheus HTTP server: {e}", exc_info=True)


# --- Core Prometheus Metrics Initialization (FIXED ORDER) ---
# FIX: Move LLM-related metrics to the top to ensure they exist on partial import
LLM_CALLS_TOTAL = prom.Counter('llm_calls_total', 'Total number of LLM API calls', ['provider', 'model'])
LLM_ERRORS_TOTAL = prom.Counter('llm_errors_total', 'Total number of LLM API errors', ['provider', 'model'])
LLM_LATENCY_SECONDS = prom.Histogram('llm_latency_seconds', 'LLM API response latency', ['provider', 'model'])
LLM_TOKENS_INPUT = prom.Counter('llm_tokens_input_total', 'Total input tokens processed by LLM', ['provider', 'model'])
LLM_TOKENS_OUTPUT = prom.Counter('llm_tokens_output_total', 'Total output tokens processed by LLM', ['provider', 'model'])
LLM_COST_TOTAL = prom.Counter('llm_cost_total', 'Total estimated LLM cost', ['provider', 'model'])
LLM_PROVIDER_HEALTH = prom.Gauge('llm_provider_health_status', 'Health status of LLM provider (1=healthy, 0=unhealthy)', ['provider'])
LLM_RATE_LIMIT_EXCEEDED = prom.Counter('llm_rate_limit_exceeded_total', 'Total times rate limit was exceeded', ['provider'])
LLM_CIRCUIT_STATE = prom.Gauge('llm_circuit_breaker_state', 'State of the LLM circuit breaker (1=open, 0.5=half, 0=closed)', ['provider'])

# Other Runner Metrics
RUN_LATENCY = prom.Histogram('runner_latency_seconds', 'Run latency of test executions', ['backend', 'framework', 'instance_id'])
RUN_ERRORS = prom.Counter('runner_errors_total', 'Total count of internal runner errors', ['error_type', 'backend', 'instance_id'])
RUN_SUCCESS = prom.Counter('runner_successful_runs_total', 'Total count of successful test runs', ['backend', 'framework', 'instance_id'])
RUN_FAILURE = prom.Counter('runner_failed_runs_total', 'Total count of failed test runs', ['backend', 'framework', 'instance_id'])
RUN_PASS_RATE = prom.Gauge('runner_overall_test_pass_rate', 'Overall Test pass rate (0-1) across all runs')
RUN_RESOURCE_USAGE = prom.Gauge('runner_resource_usage_percent', 'Percentage of resource usage', ['resource_type', 'instance_id'])
RUN_QUEUE = prom.Gauge('runner_queue_length', 'Current length of pending tasks in the queue')
RUN_MUTATION_SURVIVAL = prom.Gauge('runner_mutation_survival_rate', 'Latest mutation survival rate (0-1)')
RUN_FUZZ_DISCOVERIES = prom.Counter('runner_fuzz_discoveries_total', 'Total count of issues discovered by fuzzing')
HEALTH_STATUS = prom.Gauge('runner_component_health_status', 'Health status of a component (1=healthy, 0=unhealthy)', ['component_name', 'instance_id'])
DISTRIBUTED_NODES_ACTIVE = prom.Gauge('runner_distributed_nodes_active', 'Number of active distributed nodes in the cluster')
DISTRIBUTED_LATENCY = prom.Histogram('runner_distributed_latency_seconds', 'Latency of distributed task submissions', ['instance_id'])

# --- Dependability & Quality Metrics ---
RUN_COVERAGE_PERCENT = prom.Gauge('runner_overall_coverage_percent', 'Overall code coverage percentage (0-1)')
RUN_VULNERABILITY_SCORE = prom.Gauge('runner_overall_vulnerability_score', 'Overall vulnerability score (lower is better, e.g., CVSS base score)')
RUN_AVG_TEST_LATENCY_HIST = prom.Histogram('runner_individual_test_latency_seconds', 'Distribution of individual test case latencies', ['framework', 'instance_id'])
DOC_VALIDATION_STATUS = prom.Gauge('runner_doc_validation_status', 'Documentation validation status (1=pass, 0=fail)', ['doc_framework_name', 'instance_id'])
DOC_GENERATION_ERRORS = prom.Counter('runner_doc_generation_errors_total', 'Total count of documentation generation errors', ['error_type', 'doc_framework_name', 'instance_id'])

# --- Resilient Queuing Metrics ---
FAILED_EXPORT_QUEUE_SIZE = prom.Gauge('runner_failed_export_queue_size', 'Current size of failed export retry queue')
EXPORT_RETRY_ATTEMPTS = prom.Counter('runner_export_retry_attempts_total', 'Total number of export retry attempts', ['exporter', 'success'])

# --- New Observability Metrics for Configuration and Task Lifecycle ---
RUNNER_CONFIG_RELOADS = prom.Counter('runner_config_reloads_total', 'Total count of times configuration has been reloaded')
RUNNER_CONFIG_VERSION = prom.Gauge('runner_active_config_version', 'Current active configuration schema version', ['version'])
RUNNER_TASK_STATUS = prom.Gauge('runner_task_status_count', 'Count of tasks by their current status', ['status'])

# In-memory history for anomaly detection. Stores (value, timestamp) for last X intervals/data points.
_METRIC_HISTORY: Dict[str, Deque[Tuple[float, datetime]]] = defaultdict(lambda: deque(maxlen=60))


# Alert thresholds (configurable via RunnerConfig)
ALERT_THRESHOLDS: Dict[str, Any] = {
    'error_rate': 0.1,
    'health_min': 1.0,
    'queue_max': 50,
    'resource_max': 90.0,
    'vulnerability_score_max': 7.0,
    'performance_latency_max': 5.0,
    'coverage_min': 0.7,
    'mutation_survival_max': 0.3,
    'doc_validation_fail': 0.5,
    'anomaly_detection_window': 5,
    'anomaly_detection_std_dev_multiplier': 2.0
}

# --- Exporter Registry ---
_EXPORTER_REGISTRY: Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]] = {} # Exporter funcs are async

def register_exporter(name: str, export_func: Callable[[Dict[str, Any]], Awaitable[None]]):
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
    def __init__(self, config: RunnerConfig):
        self.config = config
        self.instance_id: str = getattr(self.config, 'instance_id', 'unknown_instance')
        
        self._initialize_datadog_exporter()
        self._initialize_cloudwatch_exporter()
        self._initialize_custom_json_file_exporter()
        
        # Queue for failed exports that need retries with exponential backoff
        # Format: (metrics, exporter_name, retry_count, first_failure_timestamp, next_retry_time)
        self._failed_exports_queue: Deque[Tuple[Dict[str, Any], str, int, datetime, datetime]] = deque()
        
        # FIX: Use getattr for config access
        self._max_export_retries = getattr(self.config, 'max_metrics_export_retries', 3)
        self._export_retry_base_interval = getattr(self.config, 'metrics_export_retry_interval_seconds', 5)
        self._export_retry_max_interval = getattr(self.config, 'metrics_export_retry_max_interval_seconds', 60)
        self._export_retry_exponential_base = getattr(self.config, 'metrics_export_retry_exponential_base', 2.0)
        
        # Failover file path (Path object)
        failover_file = getattr(self.config, 'metrics_failover_file', None)
        self._failover_file_path: Optional[Path] = Path(failover_file) if failover_file else None
        
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
        delay = self._export_retry_base_interval * (self._export_retry_exponential_base ** retry_count)
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
            logger.debug(f"Processing {logs_to_process} failed metric exports in retry queue.")

            # Process only items whose retry time has arrived
            for _ in range(logs_to_process):
                if self._stop_evt.is_set(): # Check for shutdown signal during processing
                    break
                if not self._failed_exports_queue:
                    break
                    
                metrics_snapshot, exporter_name, retry_count, first_failure_ts, next_retry_time = self._failed_exports_queue[0]
                
                if current_time < next_retry_time:
                    self._failed_exports_queue.rotate(-1)
                    continue
                
                self._failed_exports_queue.popleft()
                
                if retry_count >= self._max_export_retries:
                    logger.error(f"Permanently dropping metrics batch for exporter '{exporter_name}' after {self._max_export_retries} retries. Writing to failover file if configured. Metrics: {metrics_snapshot.keys()}")
                    log_action("MetricsExportDropped", {
                        "exporter": exporter_name,
                        "reason": "max_retries_exceeded",
                        "metric_keys": list(metrics_snapshot.keys()),
                        "first_failure_timestamp": first_failure_ts.isoformat(),
                        "total_retries": retry_count
                    }, extra={'instance_id': self.instance_id})
                    RUN_ERRORS.labels(error_type='exporter_dropped_metrics', backend=exporter_name, instance_id=self.instance_id).inc()
                    
                    if self._failover_file_path:
                        await self._write_to_failover_file(metrics_snapshot, exporter_name)
                    continue

                delay = self._calculate_retry_delay(retry_count)
                
                logger.info(f"Retrying export for '{exporter_name}' (attempt {retry_count + 1}/{self._max_export_retries}, next delay: {delay:.1f}s)...")
                export_func = _EXPORTER_REGISTRY.get(exporter_name)
                
                if not export_func:
                    logger.error(f"Exporter '{exporter_name}' not found in registry for retry. Dropping metrics.")
                    RUN_ERRORS.labels(error_type='exporter_missing_for_retry', backend=exporter_name, instance_id=self.instance_id).inc()
                    if self._failover_file_path:
                        await self._write_to_failover_file(metrics_snapshot, exporter_name, reason="exporter_missing")
                    continue
                
                retry_successful = False
                try:
                    if asyncio.iscoroutinefunction(export_func):
                        await asyncio.wait_for(export_func(metrics_snapshot), timeout=self._export_retry_base_interval * 2)
                    else:
                        await asyncio.wait_for(
                            asyncio.to_thread(export_func, metrics_snapshot),
                            timeout=self._export_retry_base_interval * 2
                        )
                    logger.info(f"Successfully retried export to '{exporter_name}' after {retry_count + 1} attempt(s).")
                    retry_successful = True
                    EXPORT_RETRY_ATTEMPTS.labels(exporter=exporter_name, success='true').inc()
                    
                except asyncio.TimeoutError:
                    logger.warning(f"Retry export to '{exporter_name}' timed out. Re-queueing with backoff.")
                    RUN_ERRORS.labels(error_type='exporter_retry_timeout', backend=exporter_name, instance_id=self.instance_id).inc()
                    EXPORT_RETRY_ATTEMPTS.labels(exporter=exporter_name, success='false').inc()
                except Exception as e:
                    logger.warning(f"Retry export to '{exporter_name}' failed: {e}. Re-queueing with backoff.", exc_info=True)
                    RUN_ERRORS.labels(error_type='exporter_retry_failure', backend=exporter_name, instance_id=self.instance_id).inc()
                    EXPORT_RETRY_ATTEMPTS.labels(exporter=exporter_name, success='false').inc()
                
                if not retry_successful:
                    next_retry_at = current_time + timedelta(seconds=delay)
                    self._failed_exports_queue.append((
                        metrics_snapshot,
                        exporter_name,
                        retry_count + 1,
                        first_failure_ts,
                        next_retry_at
                    ))
                    logger.debug(f"Re-queued failed export for '{exporter_name}'. Next retry at {next_retry_at.isoformat()}")


    async def _write_to_failover_file(self, metrics: Dict[str, Any], exporter_name: str, reason: str = "max_retries_exceeded"):
        """Writes a failed metrics batch to a local file for later replay."""
        if not self._failover_file_path:
            logger.debug(f"Failover file not configured. Skipping write for {exporter_name}.")
            return
        
        try:
            self._failover_file_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create directory for failover file {self._failover_file_path.parent}: {e}", exc_info=True)
            RUN_ERRORS.labels(error_type='exporter_failover_dir_fail', backend=exporter_name, instance_id=self.instance_id).inc()
            return
            
        batch_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        
        failover_entry = {
            "timestamp": timestamp,
            "exporter": exporter_name,
            "reason": reason,
            "batch_id": batch_id,
            "metrics": metrics
        }
        
        try:
            async with aiofiles.open(self._failover_file_path, mode='a', encoding='utf-8') as f:
                await f.write(json.dumps(failover_entry, ensure_ascii=False) + '\n')
            logger.critical(f"Wrote failed metrics batch ({batch_id}) for '{exporter_name}' to failover file: {self._failover_file_path}")
            log_action("MetricsFailoverWrite", {"file_path": str(self._failover_file_path), "exporter": exporter_name, "batch_id": batch_id, "reason": reason}, extra={'instance_id': self.instance_id})
        except Exception as e:
            logger.error(f"Failed to write metrics batch to failover file {self._failover_file_path}: {e}", exc_info=True)
            RUN_ERRORS.labels(error_type='exporter_failover_write_fail', backend=exporter_name, instance_id=self.instance_id).inc()


    def _initialize_datadog_exporter(self):
        """Initializes the Datadog exporter if configured."""
        # FIX: Use getattr
        datadog_api_key = getattr(self.config, 'datadog_api_key', None)
        if isinstance(datadog_api_key, SecretStr):
            datadog_api_key = datadog_api_key.get_secret_value()

        if not datadog_api_key:
            logger.debug("Datadog API key not configured. Datadog exporter will not be initialized.")
            return

        try:
            if datadog:
                if not datadog.api.initialized:
                    datadog_options = {
                        'api_key': datadog_api_key,
                        'app_key': getattr(self.config, 'datadog_app_key', ''), # FIX: Use getattr
                        'host_name': self.instance_id
                    }
                    datadog.initialize(**datadog_options)
                    logger.info("Datadog SDK (v1) initialized successfully.")
                
                async def datadog_export_wrapper(metrics_snapshot: Dict[str, Any]):
                    await asyncio.to_thread(self._export_to_datadog_sync, metrics_snapshot)
                register_exporter('datadog', datadog_export_wrapper)
                logger.info("Datadog exporter (legacy client) registered.")
            else:
                logger.warning("Datadog API key configured but SDK not available for direct export. Install 'datadog'.")
                RUN_ERRORS.labels(error_type='exporter_init_fail', backend='datadog', instance_id=self.instance_id).inc()
        except Exception as e:
            logger.error(f"Failed to initialize Datadog exporter: {e}", exc_info=True)
            RUN_ERRORS.labels(error_type='exporter_init_fail', backend='datadog', instance_id=self.instance_id).inc()

    def _export_to_datadog_sync(self, metrics: Dict[str, Any]):
        """Synchronous part of Datadog export, meant to be run in a thread."""
        if datadog and datadog.api:
            try:
                for name, value in metrics.items():
                    dd_name = name.replace('runner_', 'app.runner.').replace('_total', '.count').replace('_percent', '.percent')
                    
                    if isinstance(value, (int, float)) and not (value != value or value == float('inf') or value == float('-inf')):
                        dd_type = 'count' if '_total' in name or name.endswith('_total') else 'gauge'
                        datadog.api.Metric.send(metric=dd_name, points=value, type=dd_type, host=self.instance_id)
                logger.debug(f"Exported {len(metrics)} metrics to Datadog.")
            except Exception as e:
                raise ExporterError(error_codes['EXPORTER_FAILURE'], detail=f"Datadog export failed: {e}", exporter_name='datadog', cause=e)

    def _initialize_cloudwatch_exporter(self):
        """Initializes the AWS CloudWatch exporter if configured."""
        # FIX: Use getattr
        if not getattr(self.config, 'aws_region', None):
            logger.debug("AWS region not configured. CloudWatch exporter will not be initialized.")
            return

        try:
            if not boto3:
                raise ImportError("boto3 package not installed for CloudWatch export.")
            
            # FIX: Use getattr
            aws_access_key = getattr(self.config, 'aws_access_key_id', None)
            aws_secret_key = getattr(self.config, 'aws_secret_access_key', None)
            
            if isinstance(aws_access_key, SecretStr): aws_access_key = aws_access_key.get_secret_value()
            if isinstance(aws_secret_key, SecretStr): aws_secret_key = aws_secret_key.get_secret_value()

            self.cw_client = boto3.client(
                'cloudwatch',
                region_name=self.config.aws_region,
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key
            )
            # Perform a quick test call to verify credentials/connectivity
            try:
                self.cw_client.get_metric_statistics(
                    Namespace='AWS/EC2',
                    MetricName='CPUUtilization',
                    StartTime=datetime.now(timezone.utc) - timedelta(minutes=5),
                    EndTime=datetime.now(timezone.utc),
                    Period=300,
                    Statistics=['Average'],
                    Dimensions=[{'Name': 'InstanceId', 'Value': 'i-xxxxxxxxxxxxxxxxx'}] # Dummy ID for test
                )
            except BotoClientError as e:
                logger.error(f"CloudWatch test call failed during init: {e}. Check AWS credentials/permissions.", exc_info=True)
                RUN_ERRORS.labels(error_type='exporter_init_fail', backend='cloudwatch_test_call', instance_id=self.instance_id).inc()
                self.cw_client = None
                return
            
            async def cloudwatch_export_wrapper(metrics_snapshot: Dict[str, Any]):
                await asyncio.to_thread(self._export_to_cloudwatch_sync, metrics_snapshot)
            register_exporter('cloudwatch', cloudwatch_export_wrapper)
            logger.info(f"AWS CloudWatch exporter initialized for region: {self.config.aws_region}.")
        except ImportError as ie:
            logger.warning(f"CloudWatch exporter: {ie}. Install 'boto3'.")
            RUN_ERRORS.labels(error_type='exporter_init_fail', backend='cloudwatch', instance_id=self.instance_id).inc()
        except Exception as e:
            logger.error(f"Unexpected error initializing AWS CloudWatch exporter: {e}", exc_info=True)
            RUN_ERRORS.labels(error_type='exporter_init_fail', backend='cloudwatch', instance_id=self.instance_id).inc()

    def _export_to_cloudwatch_sync(self, metrics: Dict[str, Any]):
        """Synchronous part of CloudWatch export, meant to be run in a thread."""
        if self.cw_client:
            metric_data: List[Dict[str, Any]] = []
            for name, value in metrics.items():
                if isinstance(value, (int, float)) and not (value != value or value == float('inf') or value == float('-inf')):
                    metric_data.append({
                        'MetricName': name,
                        'Value': value,
                        'Unit': 'None',
                        'Dimensions': [{'Name': 'InstanceId', 'Value': self.instance_id}]
                    })
            
            if metric_data:
                batch_size = 20
                for i in range(0, len(metric_data), batch_size):
                    batch = metric_data[i:i + batch_size]
                    try:
                        self.cw_client.put_metric_data(
                            Namespace='RunnerMetrics',
                            MetricData=batch
                        )
                        logger.debug(f"Exported {len(batch)} metrics batch to CloudWatch.")
                    except BotoClientError as e:
                        raise ExporterError(error_codes['EXPORTER_FAILURE'], detail=f"CloudWatch export failed: {e}", exporter_name='cloudwatch', cause=e)
                    except Exception as e:
                        raise ExporterError(error_codes['EXPORTER_FAILURE'], detail=f"CloudWatch export failed: {e}", exporter_name='cloudwatch', cause=e)
        else:
            logger.debug("CloudWatch exporter not active for direct send.")

    def _initialize_custom_json_file_exporter(self):
        """Initializes the custom JSON file exporter (always available)."""
        async def json_file_export_wrapper(metrics_snapshot: Dict[str, Any]):
            await asyncio.to_thread(self._export_to_custom_json_file_sync, metrics_snapshot)
        register_exporter('custom_json_file', json_file_export_wrapper)
        logger.info("Custom JSON file exporter registered.")

    def _export_to_custom_json_file_sync(self, metrics: Dict[str, Any]):
        """Synchronous part of JSON file export, meant to be run in a thread."""
        # FIX: Use getattr
        metrics_file_path = getattr(self.config, 'custom_metrics_file', 'metrics_snapshot.json')
        try:
            with open(metrics_file_path, 'w', encoding='utf-8') as f:
                json.dump(metrics, f, indent=2)
            logger.debug(f"Exported metrics to custom file: {metrics_file_path}.")
            log_action("MetricsExportedToFile", {"file_path": metrics_file_path, "metric_count": len(metrics)}, extra={'instance_id': self.instance_id})
        except Exception as e:
            raise ExporterError(error_codes['EXPORTER_FAILURE'], detail=f"Custom JSON file export failed: {e}", exporter_name='custom_json_file', cause=e)

    def export_prometheus(self):
        """Prometheus metrics are automatically exposed via the HTTP server started globally."""
        pass

    def _sanitize_metrics(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitizes metrics before export: ensures numeric values, safe labels,
        and redacts any potentially sensitive string values.
        """
        # FIX: Lazy import redact_secrets here to break the cycle (runner_logging -> runner_security_utils -> runner_metrics -> runner_logging)
        from runner.runner_security_utils import redact_secrets 

        sanitized = {}
        for key, value in metrics.items():
            if isinstance(value, str):
                # FIX: Use getattr
                sanitized[key] = redact_secrets(value, patterns=getattr(self.config, 'custom_redaction_patterns', []))
            elif isinstance(value, (int, float)):
                if value != value or value == float('inf') or value == float('-inf'):
                    logger.warning(f"Sanitizing non-finite metric value for {key}: {value}. Setting to 0.0.")
                    sanitized[key] = 0.0
                else:
                    sanitized[key] = value
            else:
                logger.debug(f"Skipping non-numeric/non-string metric {key} with type {type(value)}")
        return sanitized

    async def export_all(self):
        """Collects all current Prometheus metrics and exports them to all configured systems."""
        metrics_snapshot = get_metrics_dict()
        sanitized_metrics_snapshot = self._sanitize_metrics(metrics_snapshot)

        export_tasks: List[Awaitable[Any]] = []
        for exporter_name, export_func in _EXPORTER_REGISTRY.items():
            async def _run_export_safely(exp_name: str, exp_func: Callable[[Dict[str, Any]], Awaitable[None]], metrics_snap: Dict[str, Any]):
                try:
                    log_action("MetricsExportAttempt", {"exporter": exp_name, "metric_count": len(metrics_snap)}, extra={'instance_id': self.instance_id})
                    
                    await exp_func(metrics_snap)
                    log_action("MetricsExportSuccess", {"exporter": exp_name}, extra={'instance_id': self.instance_id})

                except ExporterError as e:
                    logger.error(f"Structured error exporting metrics to '{exp_name}': {e.as_dict()}", exc_info=True)
                    log_action("MetricsExportFailure", e.as_dict(), extra={'instance_id': self.instance_id})
                    current_time = datetime.now(timezone.utc)
                    next_retry = current_time + timedelta(seconds=self._export_retry_base_interval)
                    self._failed_exports_queue.append((metrics_snap, exp_name, 0, current_time, next_retry))
                    FAILED_EXPORT_QUEUE_SIZE.set(len(self._failed_exports_queue))
                    RUN_ERRORS.labels(error_type='exporter_send_failure', backend=exp_name, instance_id=self.instance_id).inc()
                except Exception as e:
                    logger.error(f"Unexpected error exporting metrics to '{exp_name}': {e}", exc_info=True)
                    error_dict = RunnerError("EXPORTER_UNEXPECTED_ERROR", f"Unexpected error in exporter {exp_name}: {e}", exporter_name=exp_name, cause=e).as_dict()
                    log_action("MetricsExportFailure", error_dict, extra={'instance_id': self.instance_id})
                    current_time = datetime.now(timezone.utc)
                    next_retry = current_time + timedelta(seconds=self._export_retry_base_interval)
                    self._failed_exports_queue.append((metrics_snap, exp_name, 0, current_time, next_retry))
                    FAILED_EXPORT_QUEUE_SIZE.set(len(self._failed_exports_queue))
                    RUN_ERRORS.labels(error_type='exporter_send_failure', backend=exp_name, instance_id=self.instance_id).inc()

            export_tasks.append(_run_export_safely(exporter_name, export_func, sanitized_metrics_snapshot))
        
        if export_tasks:
            await asyncio.gather(*export_tasks, return_exceptions=True)
            logger.debug(f"All configured metrics exports ({len(export_tasks)} total) initiated.")
        else:
            logger.debug("No metrics exporters are registered.")

    async def shutdown(self):
        """Gracefully shuts down the MetricsExporter and its background tasks."""
        logger.info("MetricsExporter shutdown initiated. Flushing any remaining failed exports.")
        self._stop_evt.set()
        
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._retry_task
            self._retry_task = None
            logger.debug("Metrics exporter retry task cancelled successfully.")

        if self._failed_exports_queue:
            logger.info(f"Attempting final flush of {len(self._failed_exports_queue)} failed metric exports before shutdown.")
            
            current_failed_exports = list(self._failed_exports_queue)
            self._failed_exports_queue.clear()

            for metrics_snapshot, exporter_name, retry_count, first_failure_ts, next_retry_time in current_failed_exports:
                export_func = _EXPORTER_REGISTRY.get(exporter_name)
                if export_func:
                    try:
                        await asyncio.wait_for(export_func(metrics_snapshot), timeout=self._export_retry_base_interval)
                        logger.info(f"Successfully flushed remaining metrics to '{exporter_name}' on shutdown.")
                    except Exception as e:
                        logger.error(f"Failed final flush to '{exporter_name}' on shutdown: {e}.")
                        if self._failover_file_path:
                            await self._write_to_failover_file(metrics_snapshot, exporter_name, reason="final_flush_failed_on_shutdown")
                else:
                    logger.error(f"Exporter '{exporter_name}' not found for final shutdown flush. Metrics lost.")
                    if self._failover_file_path:
                        await self._write_to_failover_file(metrics_snapshot, exporter_name, reason="exporter_missing_on_shutdown")
        
        logger.info("MetricsExporter shutdown complete.")


# Assuming DOC_FRAMEWORKS_FOR_ALERTS is managed centrally.
try:
    from runner.core import DOC_FRAMEWORKS as CORE_DOC_FRAMEWORKS
    DOC_FRAMEWORKS_FOR_ALERTS: List[str] = list(CORE_DOC_FRAMEWORKS.keys())
except ImportError:
    DOC_FRAMEWORKS_FOR_ALERTS: List[str] = ['sphinx', 'mkdocs', 'javadoc', 'jsdoc', 'go_doc']


async def alert_monitor(config: RunnerConfig):
    """
    Built-in alerting system: Periodically checks metrics against defined thresholds and logs critical alerts.
    """
    logger.info("Alert monitor started.")
    # FIX: Use getattr
    alert_interval_seconds = getattr(config, 'alert_monitor_interval_seconds', 60)
    
    while True:
        metrics = get_metrics_dict()
        alerts: List[str] = []
        
        instance_id: str = config.instance_id
        
        current_thresholds = ALERT_THRESHOLDS.copy()
        for key in current_thresholds.keys():
            config_key_name = f'alert_threshold_{key}'
            # FIX: Use getattr
            config_val = getattr(config, config_key_name, None)
            if config_val is not None:
                current_thresholds[key] = config_val
        
        current_timestamp = datetime.now(timezone.utc)
        for metric_name, value in metrics.items():
            if isinstance(value, (int, float)):
                _METRIC_HISTORY[metric_name].append((float(value), current_timestamp))
        
        total_runs_metric_key = _get_canonical_metric_key('runner_successful_runs_total', {'backend': 'all', 'framework': 'all', 'instance_id': instance_id})
        total_runs_failed_metric_key = _get_canonical_metric_key('runner_failed_runs_total', {'backend': 'all', 'framework': 'all', 'instance_id': instance_id})
        
        total_runs = metrics.get(total_runs_metric_key, 0.0) + metrics.get(total_runs_failed_metric_key, 0.0)
        total_errors = metrics.get(_get_canonical_metric_key('runner_errors_total', {'error_type': 'all', 'backend': 'all', 'instance_id': instance_id}), 0.0)
        error_rate = total_errors / total_runs if total_runs > 0 else 0.0
        if error_rate > current_thresholds['error_rate']:
            alerts.append(f"High error rate: {error_rate:.2f} (>{current_thresholds['error_rate']}).")
        
        for component_name in ['overall', 'backend', 'queue', 'exporter', 'config_watcher']:
            health_metric_key = _get_canonical_metric_key('runner_component_health_status', {'component_name': component_name, 'instance_id': instance_id})
            current_health = metrics.get(health_metric_key, 1.0)
            if current_health < current_thresholds['health_min']:
                alerts.append(f"Component '{component_name}' health degraded: {current_health} (<{current_thresholds['health_min']}).")

        queue_length = metrics.get('runner_queue_length', 0.0)
        if queue_length > current_thresholds['queue_max']:
            alerts.append(f"Queue overload: {queue_length} (>{current_thresholds['queue_max']}).")
        
        for res_type in ['cpu', 'mem']:
            resource_metric_key = _get_canonical_metric_key('runner_resource_usage_percent', {'resource_type': res_type, 'instance_id': instance_id})
            usage = metrics.get(resource_metric_key, 0.0)
            if usage > current_thresholds['resource_max']:
                alerts.append(f"High {res_type.upper()} usage: {usage:.2f}% (>{current_thresholds['resource_max']}%).")

        # --- Dependability Alerts ---
        vuln_score = metrics.get('runner_overall_vulnerability_score', 0.0)
        if vuln_score > current_thresholds['vulnerability_score_max']:
            alerts.append(f"High vulnerability score: {vuln_score:.1f} (>{current_thresholds['vulnerability_score_max']:.1f}). Security risk detected.")

        # FIX: Use getattr
        framework_for_latency = getattr(config, 'framework', 'unknown')
        avg_test_latency_sum_key = _get_canonical_metric_key('runner_individual_test_latency_seconds_sum', {'framework': framework_for_latency, 'instance_id': instance_id})
        avg_test_latency_count_key = _get_canonical_metric_key('runner_individual_test_latency_seconds_count', {'framework': framework_for_latency, 'instance_id': instance_id})

        avg_test_latency_sum = metrics.get(avg_test_latency_sum_key, 0.0)
        avg_test_latency_count = metrics.get(avg_test_latency_count_key, 0.0)
        
        avg_test_latency = avg_test_latency_sum / max(1.0, avg_test_latency_count)
        if avg_test_latency > current_thresholds['performance_latency_max']:
            alerts.append(f"High average test latency: {avg_test_latency:.2f}s (>{current_thresholds['performance_latency_max']}s). Performance degradation.")

        coverage_percent = metrics.get('runner_overall_coverage_percent', 0.0)
        if coverage_percent < current_thresholds['coverage_min']:
            alerts.append(f"Low code coverage: {coverage_percent:.2f} (<{current_thresholds['coverage_min']}). Potential quality issue.")

        mutation_survival_rate = metrics.get('runner_mutation_survival_rate', 0.0)
        if mutation_survival_rate > current_thresholds['mutation_survival_max']:
            alerts.append(f"High mutation survival rate: {mutation_survival_rate:.2f} (>{current_thresholds['mutation_survival_max']}). Tests might be weak.")

        for doc_fw in DOC_FRAMEWORKS_FOR_ALERTS:
            doc_validation_metric_key = _get_canonical_metric_key('runner_doc_validation_status', {'doc_framework_name': doc_fw, 'instance_id': instance_id})
            doc_status = metrics.get(doc_validation_metric_key, 1.0)
            if doc_status < current_thresholds['doc_validation_fail']:
                alerts.append(f"Documentation validation failed for '{doc_fw}'. Critical doc integrity issue.")
        
        # --- Anomaly Detection ---
        cpu_metric_key = _get_canonical_metric_key('runner_resource_usage_percent', {'resource_type': 'cpu', 'instance_id': instance_id})
        cpu_history = _METRIC_HISTORY[cpu_metric_key]
        
        anomaly_window = current_thresholds['anomaly_detection_window']
        std_dev_multiplier = current_thresholds['anomaly_detection_std_dev_multiplier']

        if len(cpu_history) >= anomaly_window:
            recent_cpu_values = [val for val, _ in list(cpu_history)[-anomaly_window:]]
            if len(recent_cpu_values) > 1:
                mean_cpu = sum(recent_cpu_values) / len(recent_cpu_values)
                variance = sum((x - mean_cpu) ** 2 for x in recent_cpu_values) / (len(recent_cpu_values) - 1)
                std_dev_cpu = variance**0.5

                current_cpu_value = metrics.get(cpu_metric_key, 0.0)
                
                if std_dev_cpu > 0.001 and abs(current_cpu_value - mean_cpu) > std_dev_multiplier * std_dev_cpu:
                    alerts.append(f"CPU usage anomaly detected: {current_cpu_value:.2f}% (Mean: {mean_cpu:.2f}%, StdDev: {std_dev_cpu:.2f}).")
                    log_action("Anomaly_Detected", {
                        "metric": "CPU_Usage",
                        "value": current_cpu_value,
                        "mean": mean_cpu,
                        "std_dev": std_dev_cpu,
                        "threshold_multiplier": std_dev_multiplier,
                        "instance_id": instance_id
                    }, extra={'alert_type': 'anomaly', 'metric_name': 'cpu_usage', 'instance_id': instance_id})
        
        if alerts:
            timestamp = datetime.now(timezone.utc).isoformat() + 'Z'
            for alert_msg in alerts:
                logger.critical(json.dumps({
                    "event": "ALERT_TRIGGERED",
                    "timestamp": timestamp,
                    "instance_id": instance_id,
                    "message": alert_msg,
                    "alert_type": 'system_dependability',
                    "triggered_metrics": {} # Removed metrics dump to avoid huge log lines
                }))
                # Placeholder for external notification
                # asyncio.create_task(send_external_alert_notification(...))
        
        await asyncio.sleep(alert_interval_seconds)

# Assuming DOC_FRAMEWORKS_FOR_ALERTS is managed centrally
try:
    from runner.runner_core import DOC_FRAMEWORKS as CORE_DOC_FRAMEWORKS
    DOC_FRAMEWORKS_FOR_ALERTS: List[str] = list(CORE_DOC_FRAMEWORKS.keys())
except ImportError:
    DOC_FRAMEWORKS_FOR_ALERTS: List[str] = ['sphinx', 'mkdocs', 'javadoc', 'jsdoc', 'go_doc']


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
            value = metric.value # Get the value

            # Skip non-finite values which cause issues with JSON
            if not (isinstance(value, (int, float)) and (value == value and value != float('inf') and value != float('-inf'))):
                continue

            if labels:
                label_key = "_".join(f"{k}_{v}" for k, v in sorted(labels.items()))
                
                if name == 'runner_resource_usage_percent':
                    resource_type = labels.get('resource_type')
                    if resource_type:
                        metrics_data[f'resource_{resource_type}_percent'] = value
                        continue
                
                if name not in metrics_data:
                    metrics_data[name] = {}
                metrics_data[name][label_key] = value
            else:
                metrics_data[name] = value
                
    return metrics_data


def _get_canonical_metric_key(metric_name: str, labels: Dict[str, str]) -> str:
    """Helper for internal metric key generation."""
    label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f'{metric_name}{{{label_str}}}'


# --- Test/Example usage ---
if __name__ == "__main__":
    # Setup basic logging for standalone execution
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    from unittest.mock import patch, MagicMock, AsyncMock
    
    # Mock RunnerConfig and SecretStr for this isolated test block
    class MockSecretStr(str):
        def get_secret_value(self):
            return self

    class MockRunnerConfig:
        def __init__(self, data: Dict):
            self._data = data
            self.instance_id = data.get('instance_id', 'default_runner_instance')
            self.aws_region = data.get('aws_region')
            self.datadog_api_key = data.get('datadog_api_key')
            self.datadog_app_key = data.get('datadog_app_key')
            self.custom_metrics_file = data.get('custom_metrics_file')
            self.metrics_interval_seconds = data.get('metrics_interval_seconds', 1)
            self.alert_monitor_interval_seconds = data.get('alert_monitor_interval_seconds', 5)
            self.max_metrics_export_retries = data.get('max_metrics_export_retries', 3)
            self.metrics_export_retry_base_interval = data.get('metrics_export_retry_interval_seconds', 2)
            self.metrics_export_retry_max_interval_seconds = 60 # Default max interval
            self.metrics_export_retry_exponential_base = 2.0 # Default exponential base
            self.framework = data.get('framework', 'pytest')
            self.custom_redaction_patterns = data.get('custom_redaction_patterns', [])
            self.metrics_failover_file = data.get('metrics_failover_file')

            # Dynamic alert thresholds
            for key in ALERT_THRESHOLDS.keys():
                setattr(self, f'alert_threshold_{key}', data.get(f'alert_threshold_{key}', ALERT_THRESHOLDS[key]))

        def get(self, key, default=None):
            return getattr(self, key, default)

        def __getattr__(self, name: str) -> Any:
            if name in self._data: return self._data[name]
            # Allow access to attributes set in __init__
            if name in self.__dict__: return self.__dict__[name]
            # FIX: Ensure it returns None if not found, like a config object should
            if name.startswith('alert_threshold_'):
                return ALERT_THRESHOLDS.get(name.replace('alert_threshold_', ''))
            return None


    # Ensure aiofiles is installed for failover file testing
    try:
        import aiofiles
    except ImportError:
        logger.error("aiofiles not installed. Failover file testing will be limited.")
        aiofiles = None

    test_config = MockRunnerConfig(data={
        'instance_id': 'test_instance',
        'aws_region': 'us-east-1',
        'datadog_api_key': MockSecretStr('mock_dd_api_key'),
        'datadog_app_key': MockSecretStr('mock_dd_app_key'),
        'metrics_failover_file': 'metrics_failover_test.log',
        'alert_monitor_interval_seconds': 5,
        'metrics_interval_seconds': 1,
        'framework': 'pytest',
        'max_metrics_export_retries': 2,
        'metrics_export_retry_interval_seconds': 1,
        'alert_threshold_error_rate': 0.05, 'alert_threshold_resource_max': 80.0, 'alert_threshold_vulnerability_score_max': 5.0,
        'alert_threshold_performance_latency_max': 1.0, 'alert_threshold_coverage_min': 0.9, 'alert_threshold_mutation_survival_max': 0.1,
        'alert_threshold_doc_validation_fail': 0.5, 'alert_threshold_anomaly_detection_window': 3, 'alert_threshold_anomaly_detection_std_dev_multiplier': 1.5,
    })
    
    async def mock_datadog_metric_send(*args, **kwargs):
        if "fail_datadog" in kwargs.get('metric', ''):
            logger.info("Simulating Datadog failure...")
            raise Exception("Simulated Datadog failure")
        logger.info(f"Mock Datadog Send: {kwargs.get('metric')}, {kwargs.get('points')}, {kwargs.get('type')}, {kwargs.get('host')}")
    
    async def mock_put_cloudwatch_metric_data(*args, **kwargs):
        from botocore.exceptions import ClientError as BotoClientError # Lazy import needed here
        if "fail_cloudwatch" in kwargs.get('Namespace', ''):
            logger.info("Simulating CloudWatch failure...")
            raise BotoClientError({"Error": {"Code": "Throttling", "Message": "Simulated CloudWatch failure"}}, "PutMetricData")
        logger.info(f"Mock CloudWatch Send: {kwargs.get('Namespace')}, {kwargs.get('MetricData')}")
    
    async def mock_cw_get_metric_statistics(*args, **kwargs):
        return {'Datapoints': []}

    
    # Initialize metrics with mock values
    HEALTH_STATUS.labels(component_name='overall', instance_id='test_instance').set(1.0)
    HEALTH_STATUS.labels(component_name='backend', instance_id='test_instance').set(1.0)
    RUN_QUEUE.set(5.0)
    RUN_RESOURCE_USAGE.labels(resource_type='cpu', instance_id='test_instance').set(30.0)
    RUN_RESOURCE_USAGE.labels(resource_type='mem', instance_id='test_instance').set(40.0)
    RUN_ERRORS.labels(error_type='any', backend='all', instance_id='test_instance').inc(0)
    RUN_SUCCESS.labels(backend='backend_x', framework='pytest', instance_id='test_instance').inc(100)
    RUN_VULNERABILITY_SCORE.set(3.0)
    RUN_AVG_TEST_LATENCY_HIST.labels(framework='pytest', instance_id='test_instance').observe(0.5)
    RUN_AVG_TEST_LATENCY_HIST.labels(framework='pytest', instance_id='test_instance').observe(0.6)
    RUN_COVERAGE_PERCENT.set(0.95)
    RUN_MUTATION_SURVIVAL.set(0.05)
    DOC_VALIDATION_STATUS.labels(doc_framework_name='sphinx', instance_id='test_instance').set(1.0)
    DOC_GENERATION_ERRORS.labels(error_type='runtime_error', doc_framework_name='sphinx', instance_id='test_instance').inc(0)
    
    _METRIC_HISTORY.clear()
    
    print("--- Initial Metrics State (Healthy) ---")
    print(json.dumps(get_metrics_dict(), indent=2))
    
    async def main():
        # [NEW] Start Prometheus server
        start_prometheus_server_once()

        if test_config.metrics_failover_file and os.path.exists(test_config.metrics_failover_file):
            os.remove(test_config.metrics_failover_file)
            print(f"Cleaned up {test_config.metrics_failover_file}")

        with patch('datadog.api.Metric.send', new=AsyncMock(side_effect=mock_datadog_metric_send)), \
             patch('boto3.client', return_value=MagicMock(put_metric_data=AsyncMock(side_effect=mock_put_cloudwatch_metric_data), get_metric_statistics=AsyncMock(side_effect=mock_cw_get_metric_statistics))):
            
            exporter = MetricsExporter(test_config)
            await exporter.start() # [NEW] Explicitly start the exporter's background tasks
            alert_task = asyncio.create_task(alert_monitor(test_config))
            
            print("\n--- Initial export to confirm exporters are active ---")
            await exporter.export_all()
            
            print("\n--- Simulating unhealthy metrics to trigger alerts ---")
            RUN_ERRORS.labels(error_type='type_b', backend='backend_y', instance_id='test_instance').inc(20)
            RUN_SUCCESS.labels(backend='backend_y', framework='pytest', instance_id='test_instance').inc(10)
            
            RUN_RESOURCE_USAGE.labels(resource_type='cpu', instance_id='test_instance').set(95.0)
            RUN_RESOURCE_USAGE.labels(resource_type='mem', instance_id='test_instance').set(92.0)
            RUN_VULNERABILITY_SCORE.set(8.5)
            RUN_AVG_TEST_LATENCY_HIST.labels(framework='pytest', instance_id='test_instance').observe(6.0)
            RUN_COVERAGE_PERCENT.set(0.60)
            RUN_MUTATION_SURVIVAL.set(0.40)
            DOC_VALIDATION_STATUS.labels(doc_framework_name='sphinx', instance_id='test_instance').set(0.0)
            DOC_GENERATION_ERRORS.labels(error_type='runtime_error', doc_framework_name='sphinx', instance_id='test_instance').inc(1)
            
            await asyncio.sleep(test_config.alert_monitor_interval_seconds + 1)
            
            print("\n--- Triggering CPU anomaly (with pre-filled history) ---")
            cpu_metric_key = _get_canonical_metric_key('runner_resource_usage_percent', {'resource_type': 'cpu', 'instance_id': test_config.instance_id})
            _METRIC_HISTORY[cpu_metric_key] = deque([
                (50.0, datetime.now(timezone.utc) - timedelta(seconds=5*test_config.alert_monitor_interval_seconds)),
                (55.0, datetime.now(timezone.utc) - timedelta(seconds=4*test_config.alert_monitor_interval_seconds)),
                (60.0, datetime.now(timezone.utc) - timedelta(seconds=3*test_config.alert_monitor_interval_seconds)),
                (52.0, datetime.now(timezone.utc) - timedelta(seconds=2*test_config.alert_monitor_interval_seconds)),
                (58.0, datetime.now(timezone.utc) - timedelta(seconds=1*test_config.alert_monitor_interval_seconds)),
            ], maxlen=60)
            RUN_RESOURCE_USAGE.labels(resource_type='cpu', instance_id='test_instance').set(99.0)
            
            await asyncio.sleep(test_config.alert_monitor_interval_seconds + 1)

            print("\n--- Simulating exporter failures and retries ---")
            prom.Gauge('runner_fail_datadog_metric', 'A metric to test Datadog failure', ['instance_id']).labels('test_instance').set(1.0)
            prom.Gauge('runner_fail_cloudwatch_metric', 'A metric to test CloudWatch failure', ['instance_id']).labels('test_instance').set(1.0)
            
            await exporter.export_all()

            print("\n--- Waiting for exporter retries to complete ---")
            await asyncio.sleep(test_config.max_metrics_export_retries * test_config.metrics_export_retry_base_interval * 2 + 2)

            if test_config.metrics_failover_file and aiofiles and os.path.exists(test_config.metrics_failover_file):
                print(f"\n--- Checking failover file: {test_config.metrics_failover_file} ---")
                async with aiofiles.open(test_config.metrics_failover_file, mode='r', encoding='utf-8') as f:
                    content = await f.read()
                    print(content)
                    assert 'fail_datadog' in content
                    assert 'fail_cloudwatch' in content
                    assert 'batch_id' in content
            
            print("\n--- Shutting down exporter and alert monitor ---")
            await exporter.shutdown() # [NEW] Explicitly shut down exporter
            alert_task.cancel()
            try:
                await alert_task
            except asyncio.CancelledError:
                print("Alert monitor task cancelled.")
            except Exception as e:
                print(f"Error during alert monitor task cancellation: {e}")

    asyncio.run(main())