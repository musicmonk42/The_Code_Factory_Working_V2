# -*- coding: utf-8 -*-
"""
test_runner_metrics.py
Industry-grade test suite for runner_metrics.py.

* Tests Prometheus metric state and isolation.
* Mocks external exporters (Datadog, Boto3/CloudWatch, aiofiles).
* Tests async MetricsExporter, including retry loop and shutdown.
* Tests lazy-loaded circular dependencies (logging, errors).
* Tests alert_monitor logic.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock, call

import pytest
import prometheus_client as prom

# Import the module to test
import runner.runner_metrics as m

# --- Fixtures for Mocking and Isolation ------------------------------------- #


@pytest.fixture(autouse=True)
def clean_prometheus_registry(monkeypatch):
    """
    Ensures that Prometheus metric state does not leak between tests.

    This fixture replaces the global `prom.REGISTRY` with a new,
    clean `CollectorRegistry` for each test. It also CLEARS
    the internal state of the module-level metric objects.
    """

    # Store original global registry
    original_registry = prom.REGISTRY

    # Create a new clean registry for this test
    new_registry = prom.CollectorRegistry(auto_describe=True)

    # Monkeypatch the global REGISTRY to be our new one
    monkeypatch.setattr(prom, "REGISTRY", new_registry)

    # Get all metric objects defined in the module
    sut_metrics = [
        v
        for v in vars(m).values()
        if isinstance(v, (prom.Counter, prom.Gauge, prom.Histogram))
    ]

    # --- START FIX ---
    # Clear the internal state of all metric objects before registering.
    # This prevents state (values, labels) from leaking between tests.
    for metric in sut_metrics:
        # Clear labeled metrics
        if hasattr(metric, "_metrics"):
            metric._metrics.clear()

        # Clear label-less Gauge
        if isinstance(metric, prom.Gauge) and not hasattr(metric, "_labelnames"):
            if hasattr(metric, "_value"):
                metric._value.set(0)

        # Clear label-less Counter
        if isinstance(metric, prom.Counter) and not hasattr(metric, "_labelnames"):
            if hasattr(metric, "_value"):
                metric._value.set(0)

        # Clear Histogram/Summary internals
        if hasattr(metric, "_sum"):
            if hasattr(metric._sum, "_metrics"):
                metric._sum._metrics.clear()
            if hasattr(metric._sum, "_value"):
                metric._sum._value.set(0)

        if hasattr(metric, "_count"):
            if hasattr(metric._count, "_metrics"):
                metric._count._metrics.clear()
            if hasattr(metric._count, "_value"):
                metric._count._value.set(0)

        # For Histograms, also clear buckets
        if hasattr(metric, "_buckets"):
            # The buckets are Counters, clear their internal state
            for bucket_counter in metric._buckets:
                if hasattr(bucket_counter, "_metrics"):
                    bucket_counter._metrics.clear()
                if hasattr(bucket_counter, "_value"):
                    bucket_counter._value.set(0)

    # Clear the anomaly history
    m._METRIC_HISTORY.clear()
    # --- END FIX ---

    # Register all metrics with the *new* registry
    # This will call _metric_init() on them and create the _lock
    for metric in sut_metrics:
        # We don't need to clear, it's a new registry.
        new_registry.register(metric)

    yield new_registry  # Run the test

    # Teardown:
    # Unregister from our new registry
    for metric in sut_metrics:
        try:
            new_registry.unregister(metric)
        except KeyError:
            pass

    # Also attempt to unregister from the *original* global registry
    # to prevent state from leaking *out* of this test module.
    for metric in sut_metrics:
        try:
            original_registry.unregister(metric)
        except (KeyError, ValueError):
            pass  # Ignore errors if it wasn't registered


@pytest.fixture
def mock_config():
    """Provides a comprehensive mock RunnerConfig."""
    cfg = MagicMock(spec=["RunnerConfig"])
    cfg.instance_id = "mock_instance_id"
    cfg.datadog_api_key = None
    cfg.datadog_app_key = None
    cfg.aws_region = None
    cfg.aws_access_key_id = None
    cfg.aws_secret_access_key = None
    cfg.metrics_failover_file = None
    cfg.max_metrics_export_retries = 2  # Keep low for tests
    cfg.metrics_export_retry_interval_seconds = 1
    cfg.metrics_export_retry_max_interval_seconds = 10
    cfg.metrics_export_retry_exponential_base = 2.0
    cfg.custom_metrics_file = "test_metrics_snapshot.json"
    cfg.custom_redaction_patterns = []
    cfg.alert_monitor_interval_seconds = 60

    # Mock alert thresholds
    cfg.alert_threshold_error_rate = 0.1
    cfg.alert_threshold_health_min = 1.0
    cfg.alert_threshold_queue_max = 50
    cfg.alert_threshold_resource_max = 90.0
    cfg.alert_threshold_vulnerability_score_max = 7.0
    cfg.alert_threshold_performance_latency_max = 5.0
    cfg.alert_threshold_coverage_min = 0.7
    cfg.alert_threshold_mutation_survival_max = 0.3
    cfg.alert_threshold_doc_validation_fail = 0.5
    cfg.alert_threshold_anomaly_detection_window = 5
    cfg.alert_threshold_anomaly_detection_std_dev_multiplier = 2.0

    # Mock framework for latency tests
    cfg.framework = "pytest"

    yield cfg


@pytest.fixture
def mock_external_sdks():
    """Mocks Datadog, Boto3, and aiofiles."""

    # Update patch targets from 'runner.metrics' to 'runner.runner_metrics'
    with patch("runner.runner_metrics.datadog", MagicMock()) as mock_dd, patch(
        "runner.runner_metrics.boto3", MagicMock()
    ) as mock_boto, patch("runner.runner_metrics.aiofiles", MagicMock()) as mock_aio:

        # Configure Boto3 mock
        mock_cw_client = MagicMock()
        mock_boto.client.return_value = mock_cw_client

        # Configure Datadog mock
        mock_dd.api.initialized = False

        # Configure aiofiles mock
        mock_aio_file = AsyncMock()
        mock_aio.open.return_value.__aenter__.return_value = mock_aio_file

        yield {
            "datadog": mock_dd,
            "boto3": mock_boto,
            "aiofiles": mock_aio,
            "cw_client": mock_cw_client,
            "aio_file": mock_aio_file,
        }


@pytest.fixture
def mock_lazy_imports():
    """
    Mocks modules that are lazy-loaded to break circular dependencies.
    This is critical for runner_logging, runner_errors, etc.
    """

    # 1. Create mock exception classes that are actual exceptions
    class MockExporterError(Exception):
        def __init__(self, code, detail, **kwargs):
            super().__init__(detail)
            self.code = code
            self.detail = detail
            self.kwargs = kwargs

        def as_dict(self):
            return {"error_code": self.code, "detail": self.detail, **self.kwargs}

    class MockRunnerError(Exception):
        def __init__(self, code, detail, **kwargs):
            super().__init__(detail)
            self.code = code
            self.detail = detail
            self.kwargs = kwargs

        def as_dict(self):
            return {"error_code": self.code, "detail": self.detail, **self.kwargs}

    # 2. Create mock modules
    mock_logging_mod = MagicMock(log_action=MagicMock())
    mock_security_mod = MagicMock(
        redact_secrets=MagicMock(side_effect=lambda x, **kwargs: x)
    )  # Pass-through
    mock_errors_mod = MagicMock(
        ExporterError=MockExporterError,
        RunnerError=MockRunnerError,
        get_error_codes=lambda: {
            "EXPORTER_FAILURE": "E500",
            "EXPORTER_UNEXPECTED_ERROR": "E501",
        },
    )

    # 3. Use patch.dict to inject them into sys.modules
    with patch.dict(
        "sys.modules",
        {
            "runner.runner_logging": mock_logging_mod,
            "runner.runner_security_utils": mock_security_mod,
            "runner.runner_errors": mock_errors_mod,
        },
    ):
        yield {
            "log_action": mock_logging_mod.log_action,
            "redact_secrets": mock_security_mod.redact_secrets,
            "errors": mock_errors_mod,
        }


@pytest.fixture
@pytest.mark.asyncio
async def started_metrics_exporter(
    mock_config, mock_external_sdks, mock_lazy_imports, clean_prometheus_registry
):
    """
    Provides a fully initialized and started MetricsExporter.
    Handles startup and shutdown.

    Note: Depends on `clean_prometheus_registry` to ensure it uses the
    monkeypatched registry.
    """
    exporter = m.MetricsExporter(mock_config)
    await exporter.start()

    yield exporter, mock_lazy_imports, mock_external_sdks

    await exporter.shutdown()


# --- Global Function Tests -------------------------------------------------- #


# Update patch target from 'runner.metrics' to 'runner.runner_metrics'
@patch("runner.runner_metrics.prom.start_http_server")
def test_start_prometheus_server_once(mock_start_http):
    m._prom_started = False  # Reset global flag

    m.start_prometheus_server_once(8001)
    mock_start_http.assert_called_once_with(8001, addr="127.0.0.1")
    assert m._prom_started == True

    # Call again
    m.start_prometheus_server_once(8001)
    mock_start_http.assert_called_once()  # Still only called once

    m._prom_started = False  # Cleanup


def test_register_exporter():
    # Clear the registry (it will be the test-specific one)
    m._EXPORTER_REGISTRY.clear()

    async def my_exporter(metrics):
        pass

    m.register_exporter("my_test_exporter", my_exporter)

    assert "my_test_exporter" in m._EXPORTER_REGISTRY
    assert m._EXPORTER_REGISTRY["my_test_exporter"] == my_exporter
    m._EXPORTER_REGISTRY.clear()


def test_get_metrics_dict():
    # Set values for various metric types
    m.RUN_QUEUE.labels(framework="pytest", instance_id="123").set(10)
    m.RUN_RESOURCE_USAGE.labels(resource_type="cpu", instance_id="123").set(50.5)
    m.RUN_RESOURCE_USAGE.labels(resource_type="mem", instance_id="123").set(25.0)
    m.LLM_REQUESTS_TOTAL.labels(provider="test", model="a").inc(2)
    m.RUN_LATENCY.labels(backend="local", framework="test", instance_id="123").observe(
        0.5
    )

    # get_metrics_dict() will use the monkeypatched prom.REGISTRY
    data = m.get_metrics_dict()

    # Test labelled Gauge
    assert data["runner_queue_length"]["framework_pytest_instance_id_123"] == 10

    # Test special-cased flattened Gauge
    assert data["resource_cpu_percent"] == 50.5
    assert data["resource_mem_percent"] == 25.0
    assert "runner_resource_usage_percent" not in data  # It's flattened

    # Test labelled Counter
    assert data["llm_requests_total"]["model_a_provider_test"] == 2

    # Test labelled Histogram (shows up as sum and count)
    assert (
        data["runner_latency_seconds_sum"][
            "backend_local_framework_test_instance_id_123"
        ]
        == 0.5
    )
    assert (
        data["runner_latency_seconds_count"][
            "backend_local_framework_test_instance_id_123"
        ]
        == 1
    )


# --- MetricsExporter Initialization Tests --------------------------------- #


def test_exporter_init_no_exporters(mock_config, mock_external_sdks, mock_lazy_imports):
    m._EXPORTER_REGISTRY.clear()
    exporter = m.MetricsExporter(mock_config)

    # 'custom_json_file' is always registered
    assert "custom_json_file" in m._EXPORTER_REGISTRY
    assert "datadog" not in m._EXPORTER_REGISTRY
    assert "cloudwatch" not in m._EXPORTER_REGISTRY


def test_exporter_init_datadog(mock_config, mock_external_sdks, mock_lazy_imports):
    m._EXPORTER_REGISTRY.clear()
    mock_config.datadog_api_key = "fake_key"

    exporter = m.MetricsExporter(mock_config)

    mock_external_sdks["datadog"].initialize.assert_called_once()
    assert "datadog" in m._EXPORTER_REGISTRY


def test_exporter_init_cloudwatch(mock_config, mock_external_sdks, mock_lazy_imports):
    m._EXPORTER_REGISTRY.clear()
    mock_config.aws_region = "us-east-1"

    exporter = m.MetricsExporter(mock_config)

    mock_external_sdks["boto3"].client.assert_called_with(
        "cloudwatch",
        region_name="us-east-1",
        aws_access_key_id=None,
        aws_secret_access_key=None,
    )
    # Test call was made
    mock_external_sdks["cw_client"].get_metric_statistics.assert_called_once()
    assert "cloudwatch" in m._EXPORTER_REGISTRY


def test_exporter_init_cloudwatch_fail_test_call(
    mock_config, mock_external_sdks, mock_lazy_imports
):
    m._EXPORTER_REGISTRY.clear()
    mock_config.aws_region = "us-east-1"

    # Mock Boto3 ClientError (must be imported from botocore)
    try:
        from botocore.exceptions import ClientError as BotoClientError
    except ImportError:
        BotoClientError = type("BotoClientError", (Exception,), {})

    mock_external_sdks["cw_client"].get_metric_statistics.side_effect = BotoClientError(
        {"Error": {}}, "op"
    )

    exporter = m.MetricsExporter(mock_config)

    assert "cloudwatch" not in m._EXPORTER_REGISTRY


# --- MetricsExporter Core Logic Tests ------------------------------------- #


@pytest.mark.asyncio
async def test_export_all_success(started_metrics_exporter):
    exporter, mocks, _ = started_metrics_exporter
    log_action = mocks["log_action"]

    # Register a mock async exporter
    mock_exporter_func = AsyncMock()
    m.register_exporter("test_exporter", mock_exporter_func)

    m.RUN_QUEUE.labels(framework="test", instance_id="mock_instance_id").set(5)

    await exporter.export_all()

    # Check that metrics were collected and sanitized
    mock_exporter_func.assert_called_once()
    metrics_arg = mock_exporter_func.call_args[0][0]
    assert "runner_queue_length" in metrics_arg
    assert (
        metrics_arg["runner_queue_length"][
            "framework_test_instance_id_mock_instance_id"
        ]
        == 5
    )

    # Check logging
    log_action.assert_has_calls(
        [
            call(
                "MetricsExportAttempt",
                {"exporter": "custom_json_file", "metric_count": len(metrics_arg)},
                extra={"instance_id": "mock_instance_id"},
            ),
            call(
                "MetricsExportSuccess",
                {"exporter": "custom_json_file"},
                extra={"instance_id": "mock_instance_id"},
            ),
            call(
                "MetricsExportAttempt",
                {"exporter": "test_exporter", "metric_count": len(metrics_arg)},
                extra={"instance_id": "mock_instance_id"},
            ),
            call(
                "MetricsExportSuccess",
                {"exporter": "test_exporter"},
                extra={"instance_id": "mock_instance_id"},
            ),
        ],
        any_order=True,
    )


@pytest.mark.asyncio
async def test_export_all_failure_queues_for_retry(started_metrics_exporter):
    exporter, mocks, _ = started_metrics_exporter
    log_action = mocks["log_action"]
    Errors = mocks["errors"]

    # Register a mock exporter that raises ExporterError
    mock_exporter_func = AsyncMock(
        side_effect=Errors.ExporterError("E500", "Test export fail")
    )
    m.register_exporter("failing_exporter", mock_exporter_func)

    assert len(exporter._failed_exports_queue) == 0

    await exporter.export_all()

    # Assert it was tried
    mock_exporter_func.assert_called_once()

    # Assert it was logged
    log_action.assert_any_call(
        "MetricsExportFailure",
        {"error_code": "E500", "detail": "Test export fail"},
        extra={"instance_id": "mock_instance_id"},
    )

    # Assert it was queued for retry
    assert len(exporter._failed_exports_queue) == 1
    queued_item = exporter._failed_exports_queue[0]
    assert queued_item[1] == "failing_exporter"  # exporter_name
    assert queued_item[2] == 0  # retry_count


@pytest.mark.asyncio
async def test_retry_loop_success(started_metrics_exporter):
    exporter, mocks, _ = started_metrics_exporter

    mock_exporter_func = AsyncMock()
    m.register_exporter("retry_exporter_success", mock_exporter_func)

    # Manually add a failed job
    test_metrics = {"metric": "value"}
    past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    exporter._failed_exports_queue.append(
        (test_metrics, "retry_exporter_success", 0, past_time, past_time)
    )

    # Let the loop run
    await asyncio.sleep(1.1)

    mock_exporter_func.assert_called_once_with(test_metrics)
    assert len(exporter._failed_exports_queue) == 0

    # Check metric
    metric_data = m.EXPORT_RETRY_ATTEMPTS.collect()[0].samples
    assert metric_data[0].labels == {
        "exporter": "retry_exporter_success",
        "success": "true",
    }
    assert metric_data[0].value == 1


@pytest.mark.asyncio
async def test_retry_loop_fail_and_requeue(started_metrics_exporter):
    exporter, mocks, _ = started_metrics_exporter
    Errors = mocks["errors"]

    mock_exporter_func = AsyncMock(
        side_effect=Errors.ExporterError("E500", "Retry fail")
    )
    m.register_exporter("retry_exporter_fail", mock_exporter_func)

    test_metrics = {"metric": "value"}
    past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    exporter._failed_exports_queue.append(
        (test_metrics, "retry_exporter_fail", 0, past_time, past_time)
    )

    await asyncio.sleep(1.1)

    mock_exporter_func.assert_called_once_with(test_metrics)
    assert len(exporter._failed_exports_queue) == 1

    # Check that retry count was incremented
    assert exporter._failed_exports_queue[0][2] == 1

    # Check metric
    metric_data = m.EXPORT_RETRY_ATTEMPTS.collect()[0].samples
    found = False
    for sample in metric_data:
        if sample.labels == {"exporter": "retry_exporter_fail", "success": "false"}:
            assert sample.value == 1
            found = True
            break
    assert found, "Metric sample for retry_exporter_fail (success=false) not found"


@pytest.mark.asyncio
async def test_retry_loop_max_retries_and_drop(started_metrics_exporter, tmp_path):
    exporter, mocks, sdks = started_metrics_exporter
    log_action = mocks["log_action"]
    Errors = mocks["errors"]

    # Configure failover file
    failover_file = tmp_path / "failover.log"
    exporter._failover_file_path = failover_file

    mock_exporter_func = AsyncMock(
        side_effect=Errors.ExporterError("E500", "Final fail")
    )
    m.register_exporter("retry_exporter_drop", mock_exporter_func)

    test_metrics = {"metric_to_drop": "value"}
    past_time = datetime.now(timezone.utc) - timedelta(seconds=10)

    # Add a job that is already at max retries (which is 2)
    exporter._failed_exports_queue.append(
        (
            test_metrics,
            "retry_exporter_drop",
            exporter._max_export_retries,
            past_time,
            past_time,
        )
    )

    await asyncio.sleep(1.1)

    # Exporter should NOT be called, it should be dropped immediately
    mock_exporter_func.assert_not_called()
    assert len(exporter._failed_exports_queue) == 0

    # Check for log
    log_action.assert_any_call(
        "MetricsExportDropped",
        {
            "exporter": "retry_exporter_drop",
            "reason": "max_retries_exceeded",
            "metric_keys": ["metric_to_drop"],
            "first_failure_timestamp": past_time.isoformat(),
            "total_retries": exporter._max_export_retries,
        },
        extra={"instance_id": "mock_instance_id"},
    )

    # Check that failover file was written to
    sdks["aio_file"].write.assert_called_once()
    written_data = json.loads(sdks["aio_file"].write.call_args[0][0])
    assert written_data["reason"] == "max_retries_exceeded"
    assert written_data["exporter"] == "retry_exporter_drop"
    assert written_data["metrics"] == test_metrics


@pytest.mark.asyncio
async def test_shutdown_flushes_queue(started_metrics_exporter):
    exporter, mocks, _ = started_metrics_exporter

    # Stop the retry loop immediately
    exporter._stop_evt.set()

    mock_exporter_func = AsyncMock()
    m.register_exporter("shutdown_flush_exporter", mock_exporter_func)

    test_metrics = {"metric_to_flush": "value"}
    exporter._failed_exports_queue.append(
        (
            test_metrics,
            "shutdown_flush_exporter",
            0,
            datetime.now(timezone.utc),
            datetime.now(timezone.utc),
        )
    )

    assert len(exporter._failed_exports_queue) == 1

    # Manually call shutdown (fixture shutdown will be a no-op)
    await exporter.shutdown()

    # Assert flush was attempted
    mock_exporter_func.assert_called_once_with(test_metrics)
    assert len(exporter._failed_exports_queue) == 0


# --- Alert Monitor Tests -------------------------------------------------- #


@pytest.mark.asyncio
async def test_alert_monitor_no_alerts(mock_config, caplog):
    # Set all metrics to good values
    m.RUN_ERRORS.labels(
        error_type="test", backend="test", instance_id="mock_instance_id"
    ).inc(0)
    m.RUN_SUCCESS.labels(
        backend="test", framework="test", instance_id="mock_instance_id"
    ).inc(10)
    m.HEALTH_STATUS.labels(
        component_name="backend", instance_id="mock_instance_id"
    ).set(1)
    m.RUN_QUEUE.labels(framework="pytest", instance_id="mock_instance_id").set(1)
    # --- FIX: Set default alert-triggering metrics to good values ---
    m.RUN_COVERAGE_PERCENT.set(1.0)  # > 0.7

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = asyncio.CancelledError  # Stop loop after one run

        with caplog.at_level(logging.CRITICAL):
            await m.alert_monitor(mock_config)

    assert "ALERT_TRIGGERED" not in caplog.text


@pytest.mark.asyncio
async def test_alert_monitor_triggers_health_alert(mock_config, caplog):
    m.HEALTH_STATUS.labels(
        component_name="backend", instance_id="mock_instance_id"
    ).set(0)
    # --- FIX: Set default alert-triggering metrics to good values ---
    m.RUN_COVERAGE_PERCENT.set(1.0)  # > 0.7

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = asyncio.CancelledError

        with caplog.at_level(logging.CRITICAL):
            await m.alert_monitor(mock_config)

    assert "ALERT_TRIGGERED" in caplog.text
    assert "Component 'backend' health degraded: 0.0" in caplog.text


@pytest.mark.asyncio
async def test_alert_monitor_triggers_queue_alert(mock_config, caplog):
    mock_config.alert_threshold_queue_max = 10
    m.RUN_QUEUE.labels(framework="pytest", instance_id="mock_instance_id").set(20)
    # --- FIX: Set default alert-triggering metrics to good values ---
    m.RUN_COVERAGE_PERCENT.set(1.0)  # > 0.7

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = asyncio.CancelledError

        with caplog.at_level(logging.CRITICAL):
            await m.alert_monitor(mock_config)

    assert "ALERT_TRIGGERED" in caplog.text
    assert "Queue overload: 20" in caplog.text


@pytest.mark.asyncio
async def test_alert_monitor_triggers_resource_alert(mock_config, caplog):
    mock_config.alert_threshold_resource_max = 50.0
    m.RUN_RESOURCE_USAGE.labels(
        resource_type="cpu", instance_id="mock_instance_id"
    ).set(75.5)
    # --- FIX: Set default alert-triggering metrics to good values ---
    m.RUN_COVERAGE_PERCENT.set(1.0)  # > 0.7

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = asyncio.CancelledError

        with caplog.at_level(logging.CRITICAL):
            await m.alert_monitor(mock_config)

    assert "ALERT_TRIGGERED" in caplog.text
    assert "High CPU usage: 75.50%" in caplog.text


@pytest.mark.asyncio
async def test_alert_monitor_triggers_anomaly_alert(
    mock_config, caplog, mock_lazy_imports
):
    mock_config.alert_threshold_anomaly_detection_window = 3
    mock_config.alert_threshold_anomaly_detection_std_dev_multiplier = 2.0

    # Get the key logic from the SUT
    cpu_metric_key = m._get_canonical_metric_key(
        "runner_resource_usage_percent",
        {"resource_type": "cpu", "instance_id": "mock_instance_id"},
    )

    # Populate history with stable values
    now = datetime.now(timezone.utc)
    m._METRIC_HISTORY[cpu_metric_key].append((10.0, now - timedelta(seconds=30)))
    m._METRIC_HISTORY[cpu_metric_key].append((10.1, now - timedelta(seconds=20)))
    m._METRIC_HISTORY[cpu_metric_key].append((9.9, now - timedelta(seconds=10)))

    # Set a new, anomalous value
    m.RUN_RESOURCE_USAGE.labels(
        resource_type="cpu", instance_id="mock_instance_id"
    ).set(50.0)
    # --- FIX: Set default alert-triggering metrics to good values ---
    m.RUN_COVERAGE_PERCENT.set(1.0)  # > 0.7

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = asyncio.CancelledError

        with caplog.at_level(logging.CRITICAL):
            await m.alert_monitor(mock_config)

    assert "ALERT_TRIGGERED" in caplog.text
    assert "CPU usage anomaly detected" in caplog.text

    # --- START FIX ---
    # Check that the anomaly was also logged via log_action
    # Can't use pytest.approx inside assert_called_with. Must check args manually.

    # Find the specific call to 'Anomaly_Detected'
    anomaly_call = None
    for call_obj in mock_lazy_imports["log_action"].call_args_list:
        if call_obj.args[0] == "Anomaly_Detected":
            anomaly_call = call_obj
            break

    assert (
        anomaly_call is not None
    ), "log_action('Anomaly_Detected', ...) was not called."

    # Now assert on the args of that specific call
    args, kwargs = anomaly_call
    log_payload = args[1]

    assert log_payload["metric"] == "CPU_Usage"
    assert log_payload["value"] == 50.0
    assert log_payload["mean"] == pytest.approx(10.0)
    assert log_payload["std_dev"] == pytest.approx(
        0.08164965809277232
    )  # stddev of [10.0, 10.1, 9.9]
    assert log_payload["threshold_multiplier"] == 2.0
    assert log_payload["instance_id"] == "mock_instance_id"
    assert kwargs["extra"] == {
        "alert_type": "anomaly",
        "metric_name": "cpu_usage",
        "instance_id": "mock_instance_id",
    }
    # --- END FIX ---
