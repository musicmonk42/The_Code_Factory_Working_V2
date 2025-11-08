
# test_runner_metrics.py
# Industry-grade test suite for runner_metrics.py, ensuring compliance with regulated standards.
# Covers unit and integration tests for metrics collection, export, and alerting, with traceability and security.

import pytest
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timedelta, timezone
import logging
import uuid
from collections import deque

# Import required classes and functions from runner_metrics
from runner.metrics import (
    MetricsExporter, alert_monitor, _get_canonical_metric_key,
    RUN_LATENCY, RUN_ERRORS, RUN_SUCCESS, RUN_FAILURE, RUN_PASS_RATE,
    RUN_RESOURCE_USAGE, RUN_QUEUE, RUN_MUTATION_SURVIVAL, RUN_FUZZ_DISCOVERIES,
    HEALTH_STATUS, DISTRIBUTED_NODES_ACTIVE, DISTRIBUTED_LATENCY,
    _METRIC_HISTORY
)
from runner.config import RunnerConfig, SecretStr
from runner.logging import logger, log_action
from runner.errors import RunnerError, ExporterError, PersistenceError
from runner.errors import ERROR_CODE_REGISTRY as error_codes
from runner.metrics import prom

# Configure logging for traceability and auditability
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s [trace_id=%(trace_id)s]',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Mock OpenTelemetry tracer for testing without external dependencies
class MockSpan:
    def set_attribute(self, key, value): pass
    def set_status(self, status): pass
    def record_exception(self, exception): pass
    def end(self): pass
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): pass

class MockTracer:
    def start_as_current_span(self, name, *args, **kwargs): return MockSpan()

mock_tracer = MockTracer()

# Fixture for temporary directory
@pytest.fixture
def tmp_path(tmp_path_factory):
    """Create a temporary directory for test files."""
    return tmp_path_factory.mktemp("metrics_test")

# Fixture for mock OpenTelemetry tracer
@pytest.fixture(autouse=True)
def mock_opentelemetry():
    """Mock OpenTelemetry tracer for all tests."""
    with patch('runner.metrics.trace', mock_tracer):
        yield

# Fixture for audit log
@pytest.fixture
def audit_log(tmp_path):
    """Set up an audit log file for traceability."""
    log_file = tmp_path / "audit.log"
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s [trace_id=%(trace_id)s]'
    ))
    logger.addHandler(handler)
    yield log_file
    logger.removeHandler(handler)

# Fixture for mock RunnerConfig
@pytest.fixture
def mock_config(tmp_path):
    """Create a mock RunnerConfig for testing."""
    return RunnerConfig(
        version=4,
        backend='docker',
        framework='pytest',
        instance_id='test_instance',
        metrics_failover_file=str(tmp_path / "metrics_failover.json"),
        metrics_export_retry_interval_seconds=1,
        max_metrics_export_retries=3,
        alert_monitor_interval_seconds=1,
        alert_thresholds={
            'runner_resource_usage_percent': {'cpu': 90.0, 'mem': 90.0},
            'runner_overall_test_pass_rate': 0.7,
            'runner_mutation_survival_rate': 0.2
        }
    )

# Helper function to log test execution for auditability
def log_test_execution(test_name, result, trace_id):
    """Log test execution details for audit trail."""
    logger.debug(
        f"Test {test_name}: {result}",
        extra={'trace_id': trace_id}
    )

# Test class for metrics collection
class TestMetricsCollection:
    """Tests for Prometheus metrics collection in runner_metrics.py."""

    @pytest.mark.asyncio
    async def test_run_latency_metric(self, audit_log):
        """Test RUN_LATENCY metric collection."""
        trace_id = str(uuid.uuid4())
        RUN_LATENCY.labels(backend='docker', framework='pytest', instance_id='test_instance').observe(1.5)
        assert RUN_LATENCY._metrics[('docker', 'pytest', 'test_instance')]._sum.get() == 1.5
        log_test_execution("test_run_latency_metric", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_run_errors_metric(self, audit_log):
        """Test RUN_ERRORS metric collection."""
        trace_id = str(uuid.uuid4())
        RUN_ERRORS.labels(error_type='test_error', backend='docker', instance_id='test_instance').inc(5)
        assert RUN_ERRORS._metrics[('test_error', 'docker', 'test_instance')].get() == 5
        log_test_execution("test_run_errors_metric", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_run_success_failure_metrics(self, audit_log):
        """Test RUN_SUCCESS and RUN_FAILURE metrics."""
        trace_id = str(uuid.uuid4())
        RUN_SUCCESS.labels(backend='docker', framework='pytest', instance_id='test_instance').inc(10)
        RUN_FAILURE.labels(backend='docker', framework='pytest', instance_id='test_instance').inc(3)
        assert RUN_SUCCESS._metrics[('docker', 'pytest', 'test_instance')].get() == 10
        assert RUN_FAILURE._metrics[('docker', 'pytest', 'test_instance')].get() == 3
        log_test_execution("test_run_success_failure_metrics", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_run_pass_rate_metric(self, audit_log):
        """Test RUN_PASS_RATE metric."""
        trace_id = str(uuid.uuid4())
        RUN_PASS_RATE.set(0.85)
        assert RUN_PASS_RATE._metrics[()].get() == 0.85
        log_test_execution("test_run_pass_rate_metric", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_run_resource_usage_metric(self, audit_log):
        """Test RUN_RESOURCE_USAGE metric."""
        trace_id = str(uuid.uuid4())
        RUN_RESOURCE_USAGE.labels(resource_type='cpu', instance_id='test_instance').set(75.0)
        assert RUN_RESOURCE_USAGE._metrics[('cpu', 'test_instance')].get() == 75.0
        log_test_execution("test_run_resource_usage_metric", "Passed", trace_id)

# Test class for MetricsExporter
class TestMetricsExporter:
    """Tests for MetricsExporter class in runner_metrics.py."""

    @pytest.mark.asyncio
    @patch('runner.metrics.datadog', None)
    @patch('runner.metrics.boto3', None)
    async def test_exporter_initialization_no_external_sdks(self, mock_config, audit_log):
        """Test MetricsExporter initialization without Datadog or boto3."""
        trace_id = str(uuid.uuid4())
        exporter = MetricsExporter(mock_config)
        assert exporter.datadog_enabled is False
        assert exporter.cloudwatch_enabled is False
        log_test_execution("test_exporter_initialization_no_external_sdks", "Passed", trace_id)

    @pytest.mark.asyncio
    @patch('runner.metrics.datadog')
    async def test_datadog_export(self, mock_datadog, mock_config, audit_log):
        """Test Datadog metrics export."""
        trace_id = str(uuid.uuid4())
        mock_datadog.initialize = MagicMock()
        mock_datadog.api.Metric.send = AsyncMock()
        exporter = MetricsExporter(mock_config)
        RUN_RESOURCE_USAGE.labels(resource_type='cpu', instance_id='test_instance').set(80.0)
        await exporter._export_datadog()
        mock_datadog.api.Metric.send.assert_called()
        log_test_execution("test_datadog_export", "Passed", trace_id)

    @pytest.mark.asyncio
    @patch('runner.metrics.boto3')
    async def test_cloudwatch_export(self, mock_boto3, mock_config, audit_log):
        """Test CloudWatch metrics export."""
        trace_id = str(uuid.uuid4())
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.put_metric_data = AsyncMock()
        exporter = MetricsExporter(mock_config)
        RUN_RESOURCE_USAGE.labels(resource_type='cpu', instance_id='test_instance').set(85.0)
        await exporter._export_cloudwatch()
        mock_client.put_metric_data.assert_called()
        log_test_execution("test_cloudwatch_export", "Passed", trace_id)

    @pytest.mark.asyncio
    @patch('runner.metrics.datadog')
    async def test_datadog_export_failure(self, mock_datadog, mock_config, tmp_path, audit_log):
        """Test Datadog export failure with failover to file."""
        trace_id = str(uuid.uuid4())
        mock_datadog.initialize = MagicMock()
        mock_datadog.api.Metric.send = AsyncMock(side_effect=Exception("Datadog failure"))
        exporter = MetricsExporter(mock_config)
        RUN_RESOURCE_USAGE.labels(resource_type='cpu', instance_id='test_instance').set(90.0)
        with pytest.raises(ExporterError) as exc_info:
            await exporter._export_datadog()
        assert exc_info.value.error_code == error_codes["EXPORTER_ERROR"]
        async with aiofiles.open(mock_config.metrics_failover_file, mode='r', encoding='utf-8') as f:
            content = await f.read()
            assert 'runner_resource_usage_percent' in content
        log_test_execution("test_datadog_export_failure", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_export_all(self, mock_config, audit_log):
        """Test export_all method with no external exporters."""
        trace_id = str(uuid.uuid4())
        exporter = MetricsExporter(mock_config)
        await exporter.export_all()
        log_test_execution("test_export_all", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_exporter_shutdown(self, mock_config, audit_log):
        """Test MetricsExporter shutdown."""
        trace_id = str(uuid.uuid4())
        exporter = MetricsExporter(mock_config)
        await exporter.shutdown()
        assert exporter.running is False
        log_test_execution("test_exporter_shutdown", "Passed", trace_id)

# Test class for alert monitoring
class TestAlertMonitor:
    """Tests for alert_monitor function in runner_metrics.py."""

    @pytest.mark.asyncio
    async def test_alert_monitor_threshold_exceeded(self, mock_config, audit_log):
        """Test alert_monitor for threshold exceeded."""
        trace_id = str(uuid.uuid4())
        RUN_RESOURCE_USAGE.labels(resource_type='cpu', instance_id='test_instance').set(95.0)
        with patch('runner.metrics.log_action') as mock_log_action:
            await alert_monitor(mock_config)
            mock_log_action.assert_called_with(
                'AlertTriggered',
                {
                    'metric': 'runner_resource_usage_percent',
                    'labels': {'resource_type': 'cpu', 'instance_id': 'test_instance'},
                    'value': 95.0,
                    'threshold': 90.0,
                    'alert_type': 'threshold_exceeded'
                },
                run_id=None,
                provenance_hash=None
            )
        log_test_execution("test_alert_monitor_threshold_exceeded", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_alert_monitor_anomaly_detection(self, mock_config, tmp_path, audit_log):
        """Test alert_monitor for anomaly detection."""
        trace_id = str(uuid.uuid4())
        cpu_metric_key = _get_canonical_metric_key('runner_resource_usage_percent', {'resource_type': 'cpu', 'instance_id': 'test_instance'})
        _METRIC_HISTORY[cpu_metric_key] = deque([
            (50.0, datetime.now(timezone.utc) - timedelta(seconds=5)),
            (55.0, datetime.now(timezone.utc) - timedelta(seconds=4)),
            (60.0, datetime.now(timezone.utc) - timedelta(seconds=3)),
            (52.0, datetime.now(timezone.utc) - timedelta(seconds=2)),
            (58.0, datetime.now(timezone.utc) - timedelta(seconds=1))
        ], maxlen=60)
        RUN_RESOURCE_USAGE.labels(resource_type='cpu', instance_id='test_instance').set(99.0)
        with patch('runner.metrics.log_action') as mock_log_action:
            await alert_monitor(mock_config)
            mock_log_action.assert_called_with(
                'AlertTriggered',
                {
                    'metric': 'runner_resource_usage_percent',
                    'labels': {'resource_type': 'cpu', 'instance_id': 'test_instance'},
                    'value': 99.0,
                    'mean': pytest.approx(55.0, rel=1e-2),
                    'stddev': pytest.approx(3.67, rel=1e-2),
                    'alert_type': 'anomaly_detected'
                },
                run_id=None,
                provenance_hash=None
            )
        log_test_execution("test_alert_monitor_anomaly_detection", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_alert_monitor_no_alerts(self, mock_config, audit_log):
        """Test alert_monitor when no thresholds are exceeded."""
        trace_id = str(uuid.uuid4())
        RUN_RESOURCE_USAGE.labels(resource_type='cpu', instance_id='test_instance').set(50.0)
        with patch('runner.metrics.log_action') as mock_log_action:
            await alert_monitor(mock_config)
            mock_log_action.assert_not_called()
        log_test_execution("test_alert_monitor_no_alerts", "Passed", trace_id)

# Integration test class
class TestMetricsIntegration:
    """Integration tests for metrics collection and export workflows."""

    @pytest.mark.asyncio
    async def test_metrics_export_and_alert_integration(self, mock_config, tmp_path, audit_log):
        """Test integrated metrics export and alerting."""
        trace_id = str(uuid.uuid4())
        exporter = MetricsExporter(mock_config)
        RUN_RESOURCE_USAGE.labels(resource_type='cpu', instance_id='test_instance').set(95.0)
        with patch('runner.metrics.log_action') as mock_log_action:
            await exporter.export_all()
            await alert_monitor(mock_config)
            mock_log_action.assert_called()
        log_test_execution("test_metrics_export_and_alert_integration", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_persistence_error_handling(self, mock_config, tmp_path, audit_log):
        """Test handling of persistence errors during failover."""
        trace_id = str(uuid.uuid4())
        exporter = MetricsExporter(mock_config)
        with patch('aiofiles.open', AsyncMock(side_effect=IOError("Disk full"))):
            with pytest.raises(PersistenceError) as exc_info:
                await exporter._export_to_file([{'metric': 'test', 'value': 1.0}])
            assert exc_info.value.error_code == error_codes["PERSISTENCE_FAILURE"]
        log_test_execution("test_persistence_error_handling", "Passed", trace_id)

# Run tests with audit logging
if __name__ == "__main__":
    pytest.main(["-v", "--log-level=DEBUG"])
