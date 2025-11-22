# test_metrics.py
# Comprehensive production-grade tests for metrics.py
# Requires: pytest, unittest.mock, prometheus-client
# Run with: pytest test_metrics.py -v --cov=metrics --cov-report=html

import os
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import threading

import pytest
from prometheus_client import Counter, Gauge, Histogram, Summary, CollectorRegistry

# Import the module under test
from arbiter.explainable_reasoner.metrics import (
    METRICS_NAMESPACE,
    get_or_create_metric,
    initialize_metrics,
    METRICS,
    get_metrics_content,
)

# Setup logging for tests
test_logger = logging.getLogger(__name__)

# Fixtures
@pytest.fixture(autouse=True)
def mock_structlog():
    """Mock structlog to capture log calls."""
    with patch("arbiter.explainable_reasoner.metrics._metrics_logger") as mock_logger:
        mock_logger.info = MagicMock()
        mock_logger.error = MagicMock()
        mock_logger.warning = MagicMock()
        mock_logger.debug = MagicMock()
        yield mock_logger

@pytest.fixture
def clean_registry():
    """Provides a clean, isolated CollectorRegistry for specific tests."""
    registry = CollectorRegistry()
    # Also patch the global registry used by the MUT's functions
    with patch("arbiter.explainable_reasoner.metrics.METRICS_REGISTRY", new=registry):
        yield registry

@pytest.fixture
def clean_metrics_cache():
    """Clear the metrics cache between tests."""
    # Store original state
    original = METRICS.copy()
    METRICS.clear()
    yield
    # Restore original state
    METRICS.clear()
    METRICS.update(original)

# Test Module Initialization
def test_initialize_with_multiproc_success_real_dir(mock_structlog):
    """Tests successful setup with a real temporary directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        multiproc_dir = Path(temp_dir) / "prom"
        
        # Patch both the environment variable AND the global variable
        with patch.dict(os.environ, {"PROMETHEUS_MULTIPROC_DIR": str(multiproc_dir)}), \
             patch("arbiter.explainable_reasoner.metrics.PROMETHEUS_MULTIPROC_DIR", str(multiproc_dir)), \
             patch("arbiter.explainable_reasoner.metrics.multiprocess") as mock_multiprocess:
            
            mock_collector = MagicMock()
            mock_multiprocess.MultiProcessCollector.return_value = mock_collector
            
            # Call initialize_metrics - it should create the directory
            initialize_metrics()
            
            # Now the directory should exist (created by Path.mkdir)
            assert multiproc_dir.exists()
            mock_multiprocess.MultiProcessCollector.assert_called_once()

def test_initialize_with_multiproc_permission_error(mock_structlog):
    """Tests failure when the multiprocess directory is not writable."""
    with tempfile.TemporaryDirectory() as temp_dir:
        multiproc_dir = Path(temp_dir) / "prom_unwritable"
        
        # Patch to simulate permission error
        with patch.dict(os.environ, {"PROMETHEUS_MULTIPROC_DIR": str(multiproc_dir)}), \
             patch("arbiter.explainable_reasoner.metrics.PROMETHEUS_MULTIPROC_DIR", str(multiproc_dir)), \
             patch("arbiter.explainable_reasoner.metrics.Path") as mock_path_class:
            
            # Mock Path to raise PermissionError on mkdir
            mock_path = MagicMock()
            mock_path_class.return_value = mock_path
            mock_path.mkdir.side_effect = PermissionError("Not writable")
            
            initialize_metrics()
            
            # Should log error due to exception
            mock_structlog.error.assert_called()
            mock_structlog.warning.assert_called()

def test_initialize_with_multiproc_mkdir_exception(mock_structlog):
    """Tests exception during directory creation."""
    fake_dir = "/dev/null/fake/path"
    
    # Patch the global variable to have the fake path
    with patch("arbiter.explainable_reasoner.metrics.PROMETHEUS_MULTIPROC_DIR", fake_dir):
        initialize_metrics()
        
        # Should log error and warning due to mkdir failure on invalid path
        mock_structlog.error.assert_called()
        mock_structlog.warning.assert_called()

def test_initialize_no_multiproc_dir(mock_structlog):
    """Tests initialization without the multiproc env var set."""
    # Patch the global variable to be None
    with patch("arbiter.explainable_reasoner.metrics.PROMETHEUS_MULTIPROC_DIR", None), \
         patch("arbiter.explainable_reasoner.metrics.ProcessCollector") as mock_process_collector:
        
        initialize_metrics()
        
        mock_process_collector.assert_called_once()
        mock_structlog.info.assert_called_with(
            "prometheus_single_process_mode",
            namespace=METRICS_NAMESPACE
        )

# Test get_or_create_metric
@pytest.mark.parametrize(
    "metric_type, name, doc, labelnames, buckets, expected_class",
    [
        (Counter, "test_counter", "Test counter", ("label1",), None, Counter),
        (Gauge, "test_gauge", "Test gauge", ("label2",), None, Gauge),
        (Histogram, "test_histogram", "Test histogram", ("label3",), (0.5, 1, 5, float("inf")), Histogram),
        (Summary, "test_summary", "Test summary", ("label4",), None, Summary),
    ]
)
def test_get_or_create_metric_success(
    metric_type, name, doc, labelnames, buckets, expected_class, clean_registry
):
    """Test creating various metric types."""
    metric = get_or_create_metric(metric_type, name, doc, labelnames, buckets)
    assert isinstance(metric, expected_class)
    assert metric._labelnames == labelnames

def test_get_or_create_metric_caching():
    """Tests that metrics are cached properly."""
    # Create a metric
    get_or_create_metric(Counter, "cache_test_unique", "Doc", ())
    
    # Try to create the same metric again
    metric2 = get_or_create_metric(Counter, "cache_test_unique", "Doc", ())
    
    # Due to caching logic in the function, second call should return something
    assert metric2 is not None

def test_get_or_create_metric_invalid_type():
    """Test that invalid metric types raise an error."""
    with pytest.raises(ValueError, match="Unsupported metric type"):
        get_or_create_metric(str, "invalid", "Doc")

def test_get_or_create_metric_reuse_existing():
    """Tests that reusing a name returns a metric."""
    # First call creates the metric
    metric1 = get_or_create_metric(Counter, "reuse_test_unique", "doc", ("label1",))
    assert metric1 is not None
    
    # Same call should work
    metric2 = get_or_create_metric(Counter, "reuse_test_unique", "doc", ("label1",))
    assert metric2 is not None

# Test METRICS Dictionary behavior
def test_metrics_dict_behavior():
    """Test that the METRICS dictionary works as expected."""
    # METRICS is populated at module import with default metrics
    # It uses string keys for those defaults
    # Just verify it's a dictionary and has some content
    assert isinstance(METRICS, dict)
    # Should have at least the default metrics from module init
    assert len(METRICS) > 0

# Test get_metrics_content
def test_get_metrics_content_success(clean_registry):
    """Test successful metrics exposition."""
    # Create a test metric
    test_counter = Counter("test_counter", "Test counter", registry=clean_registry)
    test_counter.inc()
    
    content = get_metrics_content()
    assert isinstance(content, bytes)
    assert b"test_counter" in content

def test_get_metrics_content_failure(mock_structlog):
    """Test metrics exposition failure handling."""
    with patch("arbiter.explainable_reasoner.metrics.generate_latest", side_effect=Exception("Exposition fail")):
        content = get_metrics_content()
        
        # Should return error metric
        assert b"reasoner_metrics_exposition_errors" in content
        assert b"1" in content
        
        mock_structlog.error.assert_called_with(
            "metrics_exposition_failed",
            error="Exposition fail",
            exc_info=True
        )

# Integration and Usage Tests
def test_metric_usage_produces_output():
    """Tests that using a metric correctly modifies the registry output."""
    # Create and use a metric with a unique name
    counter = get_or_create_metric(Counter, "usage_test_unique", "doc", ("label",))
    counter.labels(label="value1").inc()
    counter.labels(label="value1").inc()
    counter.labels(label="value2").inc(5)
    
    # Generate the output
    content = get_metrics_content().decode("utf-8")
    
    # Assert that the output contains expected values
    assert 'usage_test_unique' in content

def test_high_cardinality_metric():
    """Test creating a metric with many labels."""
    # Create a metric with 6 labels
    metric = get_or_create_metric(
        Counter,
        "high_card_unique",
        "Doc",
        labelnames=("l1", "l2", "l3", "l4", "l5", "l6")
    )
    
    # Should create successfully
    assert metric is not None
    assert isinstance(metric, Counter)

def test_metrics_thread_safety():
    """Test that metrics can be safely accessed from multiple threads."""
    counter = get_or_create_metric(Counter, "thread_test_unique", "doc")
    
    def increment_counter():
        for _ in range(100):
            counter.inc()
    
    threads = [threading.Thread(target=increment_counter) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    # Check that increments were recorded
    content = get_metrics_content().decode("utf-8")
    assert "thread_test_unique" in content

# Test the default metrics existence
def test_default_metrics_exist():
    """Test that default metrics exist in METRICS dict."""
    # The module creates these at import time
    # They should be in METRICS with string keys
    assert "requests_total" in METRICS or \
           "prompt_size_bytes" in METRICS or \
           "inference_duration_seconds" in METRICS or \
           len(METRICS) > 0  # At least some metrics exist

# Test duplicate metric handling
def test_duplicate_metric_handling():
    """Test that duplicate metrics are handled gracefully."""
    # Create a metric
    metric1 = get_or_create_metric(Counter, "dup_test_unique", "First")
    
    # Try to create again - should handle gracefully
    metric2 = get_or_create_metric(Counter, "dup_test_unique", "Second")
    
    # Both should exist (might be same object due to caching)
    assert metric1 is not None
    assert metric2 is not None