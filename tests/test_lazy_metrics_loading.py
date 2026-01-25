"""
Test suite for lazy metrics loading in omnicore_engine.message_bus.metrics.

Verifies that:
- Module import completes quickly (no expensive initialization at import time)
- Metrics are created only when accessed
- Backward compatibility is maintained
- All metric names are accessible
"""

import sys
import time
import pytest


def test_fast_module_import():
    """Test that importing the metrics module is fast (< 1 second)."""
    # Remove module from cache if already loaded
    if "omnicore_engine.message_bus.metrics" in sys.modules:
        del sys.modules["omnicore_engine.message_bus.metrics"]
    
    start_time = time.time()
    import omnicore_engine.message_bus.metrics
    import_duration = time.time() - start_time
    
    # Import should complete in under 1 second (previously took 18+ seconds)
    assert import_duration < 1.0, f"Module import took {import_duration:.2f}s (expected < 1s)"
    print(f"✓ Module import completed in {import_duration:.3f}s")


def test_metrics_created_on_first_access():
    """Test that metrics are created lazily on first access."""
    from omnicore_engine.message_bus import metrics
    
    # Check that lazy metrics storage exists but is initially empty
    assert hasattr(metrics, "_lazy_metrics"), "Module should have _lazy_metrics storage"
    
    # Access a metric for the first time
    metric_name = "MESSAGE_BUS_QUEUE_SIZE"
    initial_count = len(metrics._lazy_metrics)
    
    queue_size_metric = getattr(metrics, metric_name)
    
    # Metric should now be in lazy metrics cache
    assert metric_name in metrics._lazy_metrics, f"{metric_name} should be in lazy cache after access"
    assert len(metrics._lazy_metrics) == initial_count + 1, "Lazy metrics cache should grow by 1"
    
    # Second access should return the same cached instance
    queue_size_metric_2 = getattr(metrics, metric_name)
    assert queue_size_metric is queue_size_metric_2, "Should return cached instance on second access"
    assert len(metrics._lazy_metrics) == initial_count + 1, "Cache size should not change on re-access"


def test_all_metrics_accessible():
    """Test that all expected metrics can be accessed."""
    from omnicore_engine.message_bus.metrics import (
        MESSAGE_BUS_QUEUE_SIZE,
        MESSAGE_BUS_DISPATCH_DURATION,
        MESSAGE_BUS_TOPIC_THROUGHPUT,
        MESSAGE_BUS_CALLBACK_ERRORS,
        MESSAGE_BUS_PUBLISH_RETRIES,
        MESSAGE_BUS_MESSAGE_AGE,
        MESSAGE_BUS_CALLBACK_LATENCY,
        MESSAGE_BUS_HEALTH_STATUS,
        MESSAGE_BUS_CRITICAL_FAILURES_TOTAL,
        MESSAGE_BUS_REDIS_PUBLISH_TOTAL,
        MESSAGE_BUS_REDIS_CONSUME_TOTAL,
        MESSAGE_BUS_KAFKA_PRODUCE_TOTAL,
        MESSAGE_BUS_KAFKA_CONSUME_TOTAL,
        MESSAGE_BUS_KAFKA_LAG,
        MESSAGE_BUS_CIRCUIT_STATE,
        MESSAGE_BUS_DLQ_TOTAL,
    )
    
    # All metrics should be importable
    assert MESSAGE_BUS_QUEUE_SIZE is not None
    assert MESSAGE_BUS_DISPATCH_DURATION is not None
    assert MESSAGE_BUS_TOPIC_THROUGHPUT is not None
    assert MESSAGE_BUS_CALLBACK_ERRORS is not None
    assert MESSAGE_BUS_PUBLISH_RETRIES is not None
    assert MESSAGE_BUS_MESSAGE_AGE is not None
    assert MESSAGE_BUS_CALLBACK_LATENCY is not None
    assert MESSAGE_BUS_HEALTH_STATUS is not None
    assert MESSAGE_BUS_CRITICAL_FAILURES_TOTAL is not None
    assert MESSAGE_BUS_REDIS_PUBLISH_TOTAL is not None
    assert MESSAGE_BUS_REDIS_CONSUME_TOTAL is not None
    assert MESSAGE_BUS_KAFKA_PRODUCE_TOTAL is not None
    assert MESSAGE_BUS_KAFKA_CONSUME_TOTAL is not None
    assert MESSAGE_BUS_KAFKA_LAG is not None
    assert MESSAGE_BUS_CIRCUIT_STATE is not None
    assert MESSAGE_BUS_DLQ_TOTAL is not None
    
    print("✓ All 16 metrics are accessible")


def test_metric_functionality_preserved():
    """Test that metrics still work correctly after lazy loading."""
    from omnicore_engine.message_bus.metrics import MESSAGE_BUS_QUEUE_SIZE
    
    # Test that we can use the metric (even if it's a mock)
    # The metric should support labels and operations
    try:
        labeled_metric = MESSAGE_BUS_QUEUE_SIZE.labels(shard_id="test_shard", queue_type="normal")
        labeled_metric.set(42)
        print("✓ Metric operations work correctly")
    except Exception as e:
        pytest.fail(f"Metric operations failed: {e}")


def test_getattr_raises_on_unknown_attribute():
    """Test that accessing unknown attributes raises AttributeError."""
    from omnicore_engine.message_bus import metrics
    
    with pytest.raises(AttributeError, match="has no attribute 'UNKNOWN_METRIC'"):
        _ = metrics.UNKNOWN_METRIC


def test_helper_functions_work():
    """Test that helper functions like dispatch_timer still work."""
    from omnicore_engine.message_bus.metrics import dispatch_timer
    
    # Test that dispatch_timer can be used
    try:
        with dispatch_timer(shard_id=0):
            time.sleep(0.01)
        print("✓ dispatch_timer context manager works")
    except Exception as e:
        pytest.fail(f"dispatch_timer failed: {e}")


def test_metric_definition_count():
    """Test that all expected metrics are defined."""
    from omnicore_engine.message_bus.metrics import _METRIC_DEFINITIONS
    
    # Should have all 16 metrics defined
    assert len(_METRIC_DEFINITIONS) == 16, f"Expected 16 metrics, found {len(_METRIC_DEFINITIONS)}"
    
    expected_metrics = {
        "MESSAGE_BUS_QUEUE_SIZE",
        "MESSAGE_BUS_DISPATCH_DURATION",
        "MESSAGE_BUS_TOPIC_THROUGHPUT",
        "MESSAGE_BUS_CALLBACK_ERRORS",
        "MESSAGE_BUS_PUBLISH_RETRIES",
        "MESSAGE_BUS_MESSAGE_AGE",
        "MESSAGE_BUS_CALLBACK_LATENCY",
        "MESSAGE_BUS_HEALTH_STATUS",
        "MESSAGE_BUS_CRITICAL_FAILURES_TOTAL",
        "MESSAGE_BUS_REDIS_PUBLISH_TOTAL",
        "MESSAGE_BUS_REDIS_CONSUME_TOTAL",
        "MESSAGE_BUS_KAFKA_PRODUCE_TOTAL",
        "MESSAGE_BUS_KAFKA_CONSUME_TOTAL",
        "MESSAGE_BUS_KAFKA_LAG",
        "MESSAGE_BUS_CIRCUIT_STATE",
        "MESSAGE_BUS_DLQ_TOTAL",
    }
    
    assert set(_METRIC_DEFINITIONS.keys()) == expected_metrics, "Metric definitions should match expected set"
    print("✓ All 16 expected metrics are defined")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
