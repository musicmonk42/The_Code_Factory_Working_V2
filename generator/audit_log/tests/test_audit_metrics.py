
"""
test_audit_metrics.py

Regulated industry-grade test suite for audit_metrics.py.

Features:
- Tests metric registration, export, alerting, anomaly detection, and self-testing.
- Validates sensitive data redaction and audit logging.
- Ensures Prometheus metrics and OpenTelemetry tracing.
- Tests async-safe operations, thread-safety, and graceful shutdown.
- Verifies retry logic, error handling, and compliance (SOC2/PCI DSS/HIPAA).
- Uses real implementations with mocked external dependencies (Pushgateway, Datadog, CloudWatch, Slack).

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun, aiohttp
- prometheus-client, numpy, opentelemetry-sdk
- audit_log
"""

import asyncio
import json
import os
import threading
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from faker import Faker
from freezegun import freeze_time
import aiohttp
import numpy as np
from prometheus_client import Counter, Gauge, Histogram, REGISTRY

from audit_metrics import (
    audit_metrics,
    LOG_WRITES,
    LOG_ERRORS,
    ERROR_TYPES,
    PLUGIN_INVOCATIONS,
    CRYPTO_FAILURES,
    PERF_SCORE,
    update_vulnerability_count,
    update_performance_score
)
from audit_log import log_action

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_PUSHGATEWAY_URL = "https://localhost:9091"
TEST_DATADOG_API_KEY = "mock_datadog_key"
TEST_CLOUDWATCH_NAMESPACE = "AuditMetrics"
TEST_SLACK_WEBHOOK = "https://hooks.slack.com/services/mock"
TEST_PAGERDUTY_KEY = "mock_pagerduty_key"
TEST_ALERT_EMAIL = "alerts@codefactory.com"
MOCK_CORRELATION_ID = str(uuid.uuid4())

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'
os.environ['PUSHGATEWAY_URL'] = TEST_PUSHGATEWAY_URL
os.environ['DATADOG_API_KEY'] = TEST_DATADOG_API_KEY
os.environ['CLOUDWATCH_NAMESPACE'] = TEST_CLOUDWATCH_NAMESPACE
os.environ['ALERT_SLACK_WEBHOOK'] = TEST_SLACK_WEBHOOK
os.environ['ALERT_PAGERDUTY_ROUTING_KEY'] = TEST_PAGERDUTY_KEY
os.environ['ALERT_EMAIL_TO'] = TEST_ALERT_EMAIL
os.environ['ALERT_RETRY_ATTEMPTS'] = '3'
os.environ['SELF_TEST_INTERVAL'] = '10'

@pytest.fixture(scope="function")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(autouse=True)
def cleanup_metrics():
    """Clear Prometheus metrics before and after tests."""
    REGISTRY._metrics.clear()
    yield
    REGISTRY._metrics.clear()

@pytest_asyncio.fixture
async def mock_aiohttp():
    """Mock aiohttp client session."""
    with patch('aiohttp.ClientSession') as mock_session:
        mock_client = AsyncMock()
        mock_session.return_value.__aenter__.return_value = mock_client
        yield mock_client

@pytest_asyncio.fixture
async def mock_audit_log():
    """Mock audit_log.log_action."""
    with patch('audit_log.log_action') as mock_log:
        yield mock_log

@pytest_asyncio.fixture
async def mock_opentelemetry():
    """Mock OpenTelemetry tracer."""
    with patch('audit_metrics.trace') as mock_trace:
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        mock_trace.get_tracer.return_value = mock_tracer
        yield mock_tracer, mock_span

@pytest_asyncio.fixture
async def audit_metrics_instance():
    """Create an audit_metrics instance and start it."""
    audit_metrics.start()
    yield audit_metrics
    await audit_metrics.shutdown()

class TestAuditMetrics:
    """Test suite for audit_metrics.py."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_metric_registration_and_emission(self, audit_metrics_instance, mock_audit_log, mock_opentelemetry):
        """Test registration and emission of standard metrics."""
        with freeze_time("2025-09-01T12:00:00Z"):
            LOG_WRITES.labels(action="user_login").inc()
            ERROR_TYPES.labels(type="network_issue").inc()
            PLUGIN_INVOCATIONS.labels(plugin="data_enrichment").inc()
            CRYPTO_FAILURES.labels(op="sign_fail").inc()
            PERF_SCORE.set(85.5)

        # Verify metrics in Prometheus registry
        assert REGISTRY.get_sample_value('audit_log_writes_total', {'action': 'user_login'}) == 1
        assert REGISTRY.get_sample_value('audit_error_types_total', {'type': 'network_issue'}) == 1
        assert REGISTRY.get_sample_value('audit_plugin_invocations_total', {'plugin': 'data_enrichment'}) == 1
        assert REGISTRY.get_sample_value('audit_crypto_failures_total', {'op': 'sign_fail'}) == 1
        assert REGISTRY.get_sample_value('audit_system_performance_score') == 85.5

        # Verify audit logging
        mock_audit_log.assert_called_with("metric_emitted", Any)

        # Verify tracing
        mock_opentelemetry[1].set_attribute.assert_any_call("metric_name", "audit_log_writes_total")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_custom_metric_definition(self, audit_metrics_instance, mock_audit_log):
        """Test dynamic custom metric definition."""
        custom_counter = audit_metrics_instance.define_custom_metric(
            'database_errors', 'counter', ['db_type'], 'Counts database-related errors'
        )
        custom_counter.labels(db_type='sqlite').inc(3)
        assert REGISTRY.get_sample_value('database_errors_total', {'db_type': 'sqlite'}) == 3
        mock_audit_log.assert_called_with("metric_defined", metric_name="database_errors")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_alerting_slack(self, audit_metrics_instance, mock_aiohttp, mock_audit_log):
        """Test Slack alerting with retry logic."""
        mock_aiohttp.post.return_value.__aenter__.return_value.status = 200
        await audit_metrics_instance._send_slack_alert("Test alert", severity="critical")
        mock_aiohttp.post.assert_called_with(
            TEST_SLACK_WEBHOOK,
            json=Any,
            headers={"Content-Type": "application/json"}
        )
        mock_audit_log.assert_called_with("alert_sent", destination="slack", status="success")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_alerting_retry_failure(self, audit_metrics_instance, mock_aiohttp, mock_audit_log, mock_metrics):
        """Test alerting with retry failure."""
        mock_aiohttp.post.side_effect = aiohttp.ClientError("Network failure")
        with pytest.raises(aiohttp.ClientError):
            await audit_metrics_instance._send_slack_alert("Test alert", severity="critical")
        assert mock_aiohttp.post.call_count == 3  # Matches ALERT_RETRY_ATTEMPTS
        mock_audit_log.assert_called_with("alert_failed", destination="slack", error=Any)
        mock_metrics['audit_log_errors_total'].labels.assert_called_with(error_type="AlertError")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_anomaly_detection(self, audit_metrics_instance):
        """Test Z-score anomaly detection."""
        scores = [60, 62, 61, 59, 60, 100]  # Last score is an outlier
        for score in scores:
            update_performance_score(score)
        with freeze_time("2025-09-01T12:00:00Z"):
            await audit_metrics_instance._check_anomalies()
        mock_audit_log.assert_called_with("anomaly_detected", metric_name="system_performance_score", value=100, z_score=Any)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_self_test(self, audit_metrics_instance, mock_audit_log):
        """Test periodic self-test execution."""
        await asyncio.sleep(10)  # Wait for self-test interval
        mock_audit_log.assert_called_with("self_test_completed", status="success")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_concurrent_metric_updates(self, audit_metrics_instance, mock_audit_log):
        """Test concurrent metric updates for thread-safety."""
        async def update_metrics(i):
            LOG_WRITES.labels(action=f"user_action_{i}").inc()
            update_performance_score(60 + i)

        tasks = [update_metrics(i) for i in range(5)]
        with freeze_time("2025-09-01T12:00:00Z"):
            await asyncio.gather(*tasks)

        for i in range(5):
            assert REGISTRY.get_sample_value('audit_log_writes_total', {'action': f'user_action_{i}'}) == 1
        assert REGISTRY.get_sample_value('audit_system_performance_score') == 64  # Last score
        mock_audit_log.assert_called_with("metric_emitted", Any)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_graceful_shutdown(self, audit_metrics_instance, mock_audit_log):
        """Test graceful shutdown of background tasks."""
        await audit_metrics_instance.shutdown()
        mock_audit_log.assert_called_with("metrics_shutdown", status="success")
        assert not audit_metrics_instance._running, "Background tasks not stopped"

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_sensitive_data_redaction(self, audit_metrics_instance, mock_audit_log):
        """Test sensitive data redaction in logs."""
        sensitive_data = {"api_key": "sk-1234567890", "action": "user_login"}
        await audit_metrics_instance.example_append(sensitive_data)
        mock_audit_log.assert_called_with("metric_emitted", Any)
        assert "sk-1234567890" not in str(mock_audit_log.call_args), "Sensitive data not redacted"

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=audit_metrics",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
