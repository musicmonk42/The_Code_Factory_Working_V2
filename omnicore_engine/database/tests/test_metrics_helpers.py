"""
Comprehensive test suite for omnicore_engine/database/metrics_helpers.py
"""

import os
import sys
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metrics_helpers import (
    get_or_create_counter_local,
    get_or_create_gauge_local,
    get_or_create_histogram_local,
)
from prometheus_client import REGISTRY, Counter, Gauge, Histogram


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear the Prometheus registry before and after each test."""
    # Clear all collectors from registry
    collectors_to_remove = list(REGISTRY._collector_to_names.keys())
    for collector in collectors_to_remove:
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass
    yield
    # Clear again after test
    collectors_to_remove = list(REGISTRY._collector_to_names.keys())
    for collector in collectors_to_remove:
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass


class TestGetOrCreateCounter:
    """Test get_or_create_counter_local function."""

    def test_create_new_counter(self):
        """Test creating a new counter."""
        name = "test_counter"
        documentation = "Test counter documentation"
        labelnames = ("label1", "label2")

        counter = get_or_create_counter_local(name, documentation, labelnames)

        assert isinstance(counter, Counter)
        assert counter._name == name
        assert counter._documentation == documentation
        assert set(counter._labelnames) == set(labelnames)

    def test_get_existing_counter(self):
        """Test retrieving an existing counter."""
        name = "test_counter"
        documentation = "Test counter documentation"

        # Create counter first
        counter1 = get_or_create_counter_local(name, documentation)

        # Try to get it again
        counter2 = get_or_create_counter_local(name, documentation)

        assert counter1 is counter2

    def test_counter_with_different_type_exists(self):
        """Test when a metric with same name but different type exists."""
        name = "test_metric"

        # Create a gauge first
        Gauge(name, "Test gauge")

        # Try to create a counter with same name
        with patch("metrics_helpers.logger") as mock_logger:
            counter = get_or_create_counter_local(name, "Test counter")
            # Should return the existing metric (even though it's wrong type)
            assert counter is not None

    def test_counter_creation_error(self):
        """Test error handling during counter creation."""
        name = "test_counter"
        documentation = "Test documentation"

        with patch("metrics_helpers.Counter", side_effect=Exception("Creation failed")):
            with patch("metrics_helpers.logger") as mock_logger:
                with pytest.raises(Exception):
                    get_or_create_counter_local(name, documentation)
                mock_logger.error.assert_called()

    def test_counter_operations(self):
        """Test that created counter can perform operations."""
        counter = get_or_create_counter_local("operation_counter", "Test operations", ("op_type",))

        # Test increment
        counter.labels(op_type="read").inc()
        counter.labels(op_type="write").inc(2)

        # Verify the counter is working (we can't easily check values in tests)
        assert counter._name == "operation_counter"


class TestGetOrCreateGauge:
    """Test get_or_create_gauge_local function."""

    def test_create_new_gauge(self):
        """Test creating a new gauge."""
        name = "test_gauge"
        documentation = "Test gauge documentation"
        labelnames = ("label1",)

        gauge = get_or_create_gauge_local(name, documentation, labelnames)

        assert isinstance(gauge, Gauge)
        assert gauge._name == name
        assert gauge._documentation == documentation
        assert gauge._labelnames == labelnames

    def test_get_existing_gauge(self):
        """Test retrieving an existing gauge."""
        name = "test_gauge"
        documentation = "Test gauge documentation"

        # Create gauge first
        gauge1 = get_or_create_gauge_local(name, documentation)

        # Try to get it again
        gauge2 = get_or_create_gauge_local(name, documentation)

        assert gauge1 is gauge2

    def test_gauge_with_different_type_exists(self):
        """Test when a metric with same name but different type exists."""
        name = "test_metric"

        # Create a counter first
        Counter(name, "Test counter")

        # Try to create a gauge with same name
        with patch("metrics_helpers.logger") as mock_logger:
            gauge = get_or_create_gauge_local(name, "Test gauge")
            # Should log warning and return existing metric
            mock_logger.warning.assert_called()
            assert gauge is not None

    def test_gauge_creation_error(self):
        """Test error handling during gauge creation."""
        name = "test_gauge"
        documentation = "Test documentation"

        with patch("metrics_helpers.Gauge", side_effect=Exception("Creation failed")):
            with patch("metrics_helpers.logger") as mock_logger:
                with pytest.raises(Exception):
                    get_or_create_gauge_local(name, documentation)
                mock_logger.error.assert_called()

    def test_gauge_operations(self):
        """Test that created gauge can perform operations."""
        gauge = get_or_create_gauge_local("memory_gauge", "Memory usage", ("process",))

        # Test gauge operations
        gauge.labels(process="main").set(100)
        gauge.labels(process="worker").inc()
        gauge.labels(process="worker").dec(5)

        assert gauge._name == "memory_gauge"


class TestGetOrCreateHistogram:
    """Test get_or_create_histogram_local function."""

    def test_create_new_histogram(self):
        """Test creating a new histogram."""
        name = "test_histogram"
        documentation = "Test histogram documentation"
        labelnames = ("endpoint",)
        buckets = (0.1, 0.5, 1.0, 5.0, 10.0)

        histogram = get_or_create_histogram_local(name, documentation, labelnames, buckets)

        assert isinstance(histogram, Histogram)
        assert histogram._name == name
        assert histogram._documentation == documentation
        assert histogram._labelnames == labelnames

    def test_create_histogram_with_default_buckets(self):
        """Test creating histogram with default buckets."""
        name = "test_histogram"
        documentation = "Test histogram"

        histogram = get_or_create_histogram_local(name, documentation)

        assert isinstance(histogram, Histogram)
        # Should use Histogram.DEFAULT_BUCKETS

    def test_get_existing_histogram(self):
        """Test retrieving an existing histogram."""
        name = "test_histogram"
        documentation = "Test histogram documentation"

        # Create histogram first
        histogram1 = get_or_create_histogram_local(name, documentation)

        # Try to get it again
        histogram2 = get_or_create_histogram_local(name, documentation)

        assert histogram1 is histogram2

    def test_histogram_with_different_type_exists(self):
        """Test when a metric with same name but different type exists."""
        name = "test_metric"

        # Create a gauge first
        Gauge(name, "Test gauge")

        # Try to create a histogram with same name
        with patch("metrics_helpers.logger") as mock_logger:
            histogram = get_or_create_histogram_local(name, "Test histogram")
            # Should log warning and return existing metric
            mock_logger.warning.assert_called()
            assert histogram is not None

    def test_histogram_creation_error(self):
        """Test error handling during histogram creation."""
        name = "test_histogram"
        documentation = "Test documentation"

        with patch("metrics_helpers.Histogram", side_effect=Exception("Creation failed")):
            with patch("metrics_helpers.logger") as mock_logger:
                with pytest.raises(Exception):
                    get_or_create_histogram_local(name, documentation)
                mock_logger.error.assert_called()

    def test_histogram_operations(self):
        """Test that created histogram can perform operations."""
        histogram = get_or_create_histogram_local(
            "request_duration", "Request duration in seconds", ("method", "endpoint")
        )

        # Test histogram operations
        histogram.labels(method="GET", endpoint="/api/users").observe(0.5)
        histogram.labels(method="POST", endpoint="/api/users").observe(1.2)

        # Test time context manager
        with histogram.labels(method="GET", endpoint="/api/items").time():
            pass  # Simulates timing a block

        assert histogram._name == "request_duration"


class TestRegistryIntegration:
    """Test integration with Prometheus REGISTRY."""

    def test_metrics_registered_in_registry(self):
        """Test that created metrics are registered in REGISTRY."""
        counter = get_or_create_counter_local("test_counter", "Test")
        gauge = get_or_create_gauge_local("test_gauge", "Test")
        histogram = get_or_create_histogram_local("test_histogram", "Test")

        # Check they're in the registry
        assert "test_counter" in REGISTRY._names_to_collectors
        assert "test_gauge" in REGISTRY._names_to_collectors
        assert "test_histogram" in REGISTRY._names_to_collectors

    def test_duplicate_metric_names_handled(self):
        """Test that duplicate metric names are handled correctly."""
        # Create a counter
        counter1 = get_or_create_counter_local("duplicate_metric", "First metric")

        # Try to create another counter with same name - should return same instance
        counter2 = get_or_create_counter_local("duplicate_metric", "Second metric")

        assert counter1 is counter2

    @patch("metrics_helpers.REGISTRY._names_to_collectors", new_callable=dict)
    def test_registry_lookup(self, mock_names):
        """Test the registry lookup mechanism."""
        # Set up mock registry
        mock_counter = Mock(spec=Counter)
        mock_names["existing_counter"] = mock_counter

        with patch("metrics_helpers.REGISTRY._names_to_collectors", mock_names):
            result = get_or_create_counter_local("existing_counter", "Test")
            assert result is mock_counter


class TestErrorScenarios:
    """Test various error scenarios."""

    def test_invalid_labelnames_type(self):
        """Test handling of invalid labelnames type."""
        # Prometheus should handle this, but test our wrapper
        with pytest.raises(Exception):
            get_or_create_counter_local("test", "Test", "not_a_tuple")

    def test_empty_name(self):
        """Test handling of empty metric name."""
        with pytest.raises(Exception):
            get_or_create_counter_local("", "Test documentation")

    def test_none_documentation(self):
        """Test handling of None documentation."""
        # This should work - Prometheus allows None/empty documentation
        counter = get_or_create_counter_local("test_counter", None)
        assert isinstance(counter, Counter)

    @patch("metrics_helpers.logger")
    def test_logging_on_reuse(self, mock_logger):
        """Test that appropriate logging occurs when reusing metrics."""
        # Create a counter
        Counter("reused_metric", "Original")

        # Try to create gauge with same name
        get_or_create_gauge_local("reused_metric", "New")

        # Should log warning about reusing existing metric
        mock_logger.warning.assert_called_with(
            "Metric 'reused_metric' already registered with a different type or incompatible labels. Reusing existing."
        )


class TestConcurrency:
    """Test concurrent metric creation."""

    def test_concurrent_creation(self):
        """Test that concurrent calls to create same metric work correctly."""
        import threading

        results = []

        def create_metric():
            metric = get_or_create_counter_local("concurrent_test", "Test")
            results.append(metric)

        threads = [threading.Thread(target=create_metric) for _ in range(5)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # All threads should get the same metric instance
        assert len(results) == 5
        assert all(r is results[0] for r in results)


class TestMetricLabels:
    """Test metric label handling."""

    def test_counter_with_multiple_labels(self):
        """Test counter with multiple labels."""
        counter = get_or_create_counter_local(
            "multi_label_counter",
            "Counter with multiple labels",
            ("service", "method", "status"),
        )

        # Use the counter with labels
        counter.labels(service="api", method="GET", status="200").inc()
        counter.labels(service="api", method="POST", status="201").inc(2)

        assert counter._labelnames == ("service", "method", "status")

    def test_gauge_without_labels(self):
        """Test gauge without any labels."""
        gauge = get_or_create_gauge_local("no_label_gauge", "Gauge without labels")

        # Use the gauge without labels
        gauge.set(42)
        gauge.inc()
        gauge.dec(5)

        assert gauge._labelnames == ()

    def test_histogram_with_custom_buckets(self):
        """Test histogram with custom bucket configuration."""
        custom_buckets = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5)

        histogram = get_or_create_histogram_local(
            "custom_bucket_histogram",
            "Histogram with custom buckets",
            ("operation",),
            buckets=custom_buckets,
        )

        # Use the histogram
        histogram.labels(operation="fast").observe(0.002)
        histogram.labels(operation="slow").observe(2.5)

        assert histogram._name == "custom_bucket_histogram"
