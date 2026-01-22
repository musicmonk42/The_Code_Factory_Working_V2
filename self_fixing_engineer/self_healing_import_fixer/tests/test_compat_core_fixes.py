"""
test_compat_core_fixes.py

Comprehensive tests for the fixes to duplicated Prometheus metrics and Redis connection handling.
Tests validate thread-safe metric creation, idempotent metric registration, and robust Redis fallback.
"""

import os
import threading
from unittest.mock import MagicMock, patch

import pytest

# Import the module to test
from self_healing_import_fixer.import_fixer import compat_core

# Module path constant for patching
PKG_PATH = "self_healing_import_fixer.import_fixer.compat_core"


class TestPrometheusMetricsDeduplication:
    """Test suite for Issue 1: Duplicated Prometheus Timeseries fix."""

    @pytest.fixture(autouse=True)
    def reset_metrics_registry(self):
        """Reset the metrics registry before each test."""
        compat_core._metrics_registry = {}
        yield
        compat_core._metrics_registry = {}

    @patch.dict(os.environ, {"METRICS_ENABLED": "true"})
    def test_metrics_created_once(self):
        """Test that metrics are created only once even when called multiple times."""
        if not compat_core._HAS_PROMETHEUS:
            pytest.skip("Prometheus not available")

        # First call should create metrics
        metrics1 = compat_core._get_metrics()
        assert metrics1 is not None
        assert len(metrics1) == 7
        assert "init_duration" in metrics1
        assert "import_failures" in metrics1

        # Second call should return the same registry
        metrics2 = compat_core._get_metrics()
        assert metrics2 is metrics1
        assert id(metrics1) == id(metrics2)

    @patch.dict(os.environ, {"METRICS_ENABLED": "true"})
    def test_metrics_reuse_existing_from_registry(self):
        """Test that existing metrics in REGISTRY are reused."""
        if not compat_core._HAS_PROMETHEUS:
            pytest.skip("Prometheus not available")

        from prometheus_client import Counter, Histogram

        # Pre-register a metric
        existing_counter = Counter(
            "compat_core_import_failures_total",
            "Core module import failures",
            ["module"],
        )

        # Get metrics should reuse the existing counter
        metrics = compat_core._get_metrics()
        assert metrics["import_failures"] is existing_counter

    @patch.dict(os.environ, {"METRICS_ENABLED": "true"})
    def test_concurrent_metric_creation_thread_safe(self):
        """Test that concurrent metric creation is thread-safe."""
        if not compat_core._HAS_PROMETHEUS:
            pytest.skip("Prometheus not available")

        results = []
        errors = []

        def create_metrics():
            try:
                metrics = compat_core._get_metrics()
                results.append(metrics)
            except Exception as e:
                errors.append(e)

        # Create 10 threads trying to create metrics simultaneously
        threads = [threading.Thread(target=create_metrics) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No errors should occur
        assert len(errors) == 0, f"Errors occurred: {errors}"

        # All threads should get the same metrics instance
        assert len(results) == 10
        first_metrics = results[0]
        for metrics in results[1:]:
            assert metrics is first_metrics

    @patch.dict(os.environ, {"METRICS_ENABLED": "false"})
    def test_metrics_disabled_returns_noop(self):
        """Test that when metrics are disabled, NoOp metrics are returned."""
        metrics = compat_core._get_metrics()
        assert metrics is not None
        assert len(metrics) == 7

        # All metrics should be NoOp instances
        for metric in metrics.values():
            assert isinstance(metric, compat_core._NoopMetric)

    @patch.dict(os.environ, {"METRICS_ENABLED": "true"})
    def test_all_seven_metrics_created(self):
        """Test that all 7 expected metrics are created."""
        if not compat_core._HAS_PROMETHEUS:
            pytest.skip("Prometheus not available")

        metrics = compat_core._get_metrics()
        expected_metrics = [
            "init_duration",
            "import_failures",
            "fallback_usage",
            "load_status",
            "fallback_latency",
            "s3_offload_failures",
            "suppressed_warnings",
        ]

        for metric_name in expected_metrics:
            assert metric_name in metrics, f"Metric {metric_name} not found"


class TestRedisConnectionFallback:
    """Test suite for Issue 2: Redis Connection Failure handling."""

    @pytest.fixture(autouse=True)
    def reset_redis_client(self):
        """Reset the Redis client before each test."""
        compat_core._redis_client = None
        yield
        compat_core._redis_client = None

    @patch.dict(os.environ, {"REDIS_URL": "redis://test-host:6380/0"})
    def test_redis_url_preferred(self):
        """Test that REDIS_URL is used when available."""
        if not compat_core._HAS_REDIS:
            pytest.skip("Redis not available")

        with patch(f"{PKG_PATH}.redis") as mock_redis:
            mock_client = MagicMock()
            mock_client.ping.return_value = True
            mock_redis.from_url.return_value = mock_client

            client = compat_core._get_redis_client()

            # Verify from_url was called with REDIS_URL
            mock_redis.from_url.assert_called_once_with(
                "redis://test-host:6380/0", decode_responses=True
            )
            assert client is mock_client

    @patch.dict(
        os.environ,
        {
            "REDIS_HOST": "custom-host",
            "REDIS_PORT": "6380",
        },
        clear=True,
    )
    def test_redis_host_port_fallback(self):
        """Test fallback to REDIS_HOST and REDIS_PORT."""
        if not compat_core._HAS_REDIS:
            pytest.skip("Redis not available")

        with patch(f"{PKG_PATH}.redis") as mock_redis:
            mock_client = MagicMock()
            mock_client.ping.return_value = True
            mock_redis.Redis.return_value = mock_client

            client = compat_core._get_redis_client()

            # Verify Redis was called with host and port
            mock_redis.Redis.assert_called_once_with(
                host="custom-host",
                port=6380,
                decode_responses=True,
            )
            assert client is mock_client

    @patch.dict(
        os.environ,
        {
            "REDISHOST": "railway-host",
            "REDISPORT": "6381",
        },
        clear=True,
    )
    def test_railway_variables_fallback(self):
        """Test fallback to Railway's REDISHOST and REDISPORT."""
        if not compat_core._HAS_REDIS:
            pytest.skip("Redis not available")

        with patch(f"{PKG_PATH}.redis") as mock_redis:
            mock_client = MagicMock()
            mock_client.ping.return_value = True
            mock_redis.Redis.return_value = mock_client

            client = compat_core._get_redis_client()

            # Verify Redis was called with Railway variables
            mock_redis.Redis.assert_called_once_with(
                host="railway-host",
                port=6381,
                decode_responses=True,
            )
            assert client is mock_client

    @patch.dict(os.environ, {}, clear=True)
    def test_default_localhost_fallback(self):
        """Test fallback to localhost:6379 when no env vars are set."""
        if not compat_core._HAS_REDIS:
            pytest.skip("Redis not available")

        with patch(f"{PKG_PATH}.redis") as mock_redis:
            mock_client = MagicMock()
            mock_client.ping.return_value = True
            mock_redis.Redis.return_value = mock_client

            client = compat_core._get_redis_client()

            # Verify Redis was called with defaults
            mock_redis.Redis.assert_called_once_with(
                host="localhost",
                port=6379,
                decode_responses=True,
            )
            assert client is mock_client

    @patch.dict(os.environ, {"REDIS_URL": "redis://unavailable:6379"})
    def test_connection_failure_returns_none(self):
        """Test that connection failure returns None gracefully."""
        if not compat_core._HAS_REDIS:
            pytest.skip("Redis not available")

        with patch(f"{PKG_PATH}.redis") as mock_redis:
            mock_client = MagicMock()
            # Simulate connection failure
            mock_client.ping.side_effect = ConnectionError("Connection refused")
            mock_redis.from_url.return_value = mock_client

            # Should not raise, should return None
            client = compat_core._get_redis_client()
            assert client is None

    @patch.dict(os.environ, {"REDIS_HOST": "failing-host"})
    def test_ping_failure_logs_warning(self):
        """Test that ping failure logs a warning message."""
        if not compat_core._HAS_REDIS:
            pytest.skip("Redis not available")

        with patch(f"{PKG_PATH}.redis") as mock_redis:
            with patch(f"{PKG_PATH}.logger") as mock_logger:
                mock_client = MagicMock()
                mock_client.ping.side_effect = ConnectionError("Connection refused")
                mock_redis.Redis.return_value = mock_client

                client = compat_core._get_redis_client()

                # Verify warning was logged
                assert mock_logger.warning.called
                call_args = mock_logger.warning.call_args[0][0]
                assert "Redis unavailable" in call_args
                assert "distributed caching disabled" in call_args

    def test_redis_client_cached_after_success(self):
        """Test that successful client is cached and reused."""
        if not compat_core._HAS_REDIS:
            pytest.skip("Redis not available")

        with patch(f"{PKG_PATH}.redis") as mock_redis:
            mock_client = MagicMock()
            mock_client.ping.return_value = True
            mock_redis.from_url.return_value = mock_client

            with patch.dict(os.environ, {"REDIS_URL": "redis://test:6379"}):
                # First call
                client1 = compat_core._get_redis_client()
                # Second call
                client2 = compat_core._get_redis_client()

                # Should be the same instance
                assert client1 is client2
                # from_url should only be called once
                assert mock_redis.from_url.call_count == 1


class TestIntegration:
    """Integration tests to ensure both fixes work together."""

    @pytest.fixture(autouse=True)
    def reset_state(self):
        """Reset state before each test."""
        compat_core._metrics_registry = {}
        compat_core._redis_client = None
        yield
        compat_core._metrics_registry = {}
        compat_core._redis_client = None

    @patch.dict(
        os.environ,
        {
            "METRICS_ENABLED": "true",
            "REDIS_URL": "redis://localhost:6379",
        },
    )
    def test_metrics_and_redis_both_work(self):
        """Test that metrics and Redis initialization don't interfere with each other."""
        # This would be the scenario when both are enabled
        # Metrics should work regardless of Redis state
        metrics = compat_core._get_metrics()
        assert metrics is not None

        # Redis client creation should work independently
        # (or fail gracefully without affecting metrics)
        if compat_core._HAS_REDIS:
            with patch(f"{PKG_PATH}.redis") as mock_redis:
                mock_client = MagicMock()
                mock_client.ping.return_value = True
                mock_redis.from_url.return_value = mock_client

                client = compat_core._get_redis_client()
                assert client is not None


if __name__ == "__main__":
    pytest.main(["-v", __file__])
