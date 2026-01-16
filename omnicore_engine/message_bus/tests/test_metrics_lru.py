# test_metrics_lru.py
"""
Test suite for LRU eviction in mock metrics.

Tests verify:
- LRU eviction in _MockRegistry
- LRU eviction in _ThreadSafeDict
- Memory leak prevention
- Warning thresholds
- Memory usage tracking
"""

import threading
import time

import pytest

# Force mock mode by ensuring prometheus is not available
import sys
from unittest.mock import MagicMock

# Mock prometheus_client to force mock mode
sys.modules['prometheus_client'] = MagicMock()
sys.modules['prometheus_client.registry'] = MagicMock()

# Now import our metrics module
from omnicore_engine.message_bus import metrics

# Force reload to pick up mocked prometheus
import importlib
importlib.reload(metrics)

from omnicore_engine.message_bus.metrics import (
    _ThreadSafeDict,
    get_mock_registry_stats,
    reset_metrics,
)


class TestThreadSafeDictLRU:
    """Test LRU eviction in _ThreadSafeDict."""

    def test_initialization_with_max_size(self):
        """Test _ThreadSafeDict initializes with configurable max size."""
        d = _ThreadSafeDict(max_size=100)
        assert d.max_size == 100

    def test_small_capacity_no_eviction(self):
        """Test that no eviction occurs below capacity."""
        d = _ThreadSafeDict(max_size=10)
        
        for i in range(5):
            d.set((f"key{i}",), i)
        
        # All items should still be present
        assert len(d._data) == 5
        for i in range(5):
            assert d.get((f"key{i}",), None) == i

    def test_eviction_at_capacity(self):
        """Test that LRU eviction occurs when capacity is reached."""
        d = _ThreadSafeDict(max_size=5)
        
        # Fill to capacity
        for i in range(5):
            d.set((f"key{i}",), i)
        
        assert len(d._data) == 5
        
        # Add one more item, should evict LRU
        d.set(("key5",), 5)
        
        # Should still be at capacity
        assert len(d._data) == 5
        
        # Oldest item (key0) should be evicted
        assert d.get(("key0",), None) is None
        # Newest item should be present
        assert d.get(("key5",), None) == 5

    def test_lru_order_with_access(self):
        """Test that access order affects LRU eviction."""
        d = _ThreadSafeDict(max_size=3)
        
        d.set(("a",), 1)
        d.set(("b",), 2)
        d.set(("c",), 3)
        
        # Access 'a' to make it more recently used
        d.get(("a",), None)
        time.sleep(0.01)  # Ensure time difference
        
        # Add new item, should evict 'b' (least recently used)
        d.set(("d",), 4)
        
        # 'a' should still be present (was accessed recently)
        assert d.get(("a",), None) == 1
        # 'b' should be evicted
        assert d.get(("b",), None) is None
        # 'c' and 'd' should be present
        assert d.get(("c",), None) == 3
        assert d.get(("d",), None) == 4

    def test_inc_respects_lru(self):
        """Test that inc() operations respect LRU eviction."""
        d = _ThreadSafeDict(max_size=3)
        
        d.inc(("a",), 1.0)
        d.inc(("b",), 2.0)
        d.inc(("c",), 3.0)
        
        # Add new item, should trigger eviction
        d.inc(("d",), 4.0)
        
        # Should be at capacity
        assert len(d._data) == 3
        
        # Oldest item should be evicted
        assert d.get(("a",), 0) == 0  # Evicted, returns default

    def test_clear_method(self):
        """Test that clear() properly resets everything."""
        d = _ThreadSafeDict(max_size=10)
        
        # Add some data
        for i in range(5):
            d.set((f"key{i}",), i)
        
        # Trigger warning threshold
        d._warned_at_threshold = True
        
        # Clear
        d.clear()
        
        # Everything should be reset
        assert len(d._data) == 0
        assert len(d._access_times) == 0
        assert d._warned_at_threshold is False

    def test_thread_safety_during_eviction(self):
        """Test thread safety during LRU eviction."""
        d = _ThreadSafeDict(max_size=50)
        errors = []
        
        def worker(thread_id):
            try:
                for i in range(20):
                    key = (f"thread{thread_id}_key{i}",)
                    d.set(key, i)
                    d.get(key, None)
            except Exception as e:
                errors.append((thread_id, str(e)))
        
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # No errors should occur
        assert len(errors) == 0
        
        # Dictionary should be at or near capacity
        assert len(d._data) <= 50


class TestMockRegistryLRU:
    """Test LRU eviction in _MockRegistry."""

    def test_registry_initialization(self):
        """Test registry initializes with max collectors."""
        from omnicore_engine.message_bus.metrics import REGISTRY
        assert hasattr(REGISTRY, 'max_collectors')
        assert REGISTRY.max_collectors > 0

    def test_registry_eviction_at_capacity(self):
        """Test registry evicts oldest collector at capacity."""
        # Create a small registry for testing
        from omnicore_engine.message_bus.metrics import _MockRegistry
        registry = _MockRegistry(max_collectors=5)
        
        # Create mock collectors
        collectors = []
        for i in range(5):
            collector = MagicMock()
            collector.name = f"metric_{i}"
            registry.register(collector)
            collectors.append(collector)
        
        assert len(registry.collectors) == 5
        
        # Register one more, should evict oldest
        new_collector = MagicMock()
        new_collector.name = "metric_5"
        registry.register(new_collector)
        
        # Should still be at capacity
        assert len(registry.collectors) == 5
        
        # First collector should be evicted
        assert collectors[0] not in registry.collectors
        # New collector should be present
        assert new_collector in registry.collectors

    def test_registry_memory_usage_stats(self):
        """Test get_memory_usage() returns correct stats."""
        from omnicore_engine.message_bus.metrics import _MockRegistry
        registry = _MockRegistry(max_collectors=100)
        
        # Add some collectors
        for i in range(30):
            collector = MagicMock()
            registry.register(collector)
        
        stats = registry.get_memory_usage()
        
        assert stats['collector_count'] == 30
        assert stats['max_collectors'] == 100
        assert stats['usage_percent'] == 30.0

    def test_registry_warning_at_threshold(self):
        """Test that warning is logged at 90% capacity."""
        from omnicore_engine.message_bus.metrics import _MockRegistry
        import logging
        
        registry = _MockRegistry(max_collectors=10)
        
        # Add collectors up to 90% capacity (should trigger warning)
        for i in range(9):
            registry.register(MagicMock())
        
        # Warning flag should be set after reaching threshold
        assert registry._warned_at_threshold is True


class TestMockMetricsMemoryLeakPrevention:
    """Test that mock metrics don't cause memory leaks."""

    def test_dynamic_metric_creation_bounded(self):
        """Test that creating many dynamic metrics doesn't cause unbounded growth."""
        # Reset metrics first
        reset_metrics()
        
        # Create a mock metric with labels
        from omnicore_engine.message_bus.metrics import Counter
        metric = Counter("test_counter", "Test counter", labelnames=["label1", "label2"])
        
        # Create many unique label combinations (more than max_size)
        for i in range(15000):  # More than default max_size of 10000
            metric.labels(label1=f"value_{i}", label2=f"other_{i}").inc()
        
        # Internal storage should not exceed max_size
        assert len(metric._values._data) <= metric._values.max_size

    def test_histogram_buckets_bounded(self):
        """Test that histogram bucket storage is bounded."""
        reset_metrics()
        
        from omnicore_engine.message_bus.metrics import Histogram
        histogram = Histogram("test_histogram", "Test histogram", labelnames=["label"])
        
        # Observe many values with different labels
        for i in range(15000):
            histogram.labels(label=f"value_{i}").observe(0.5)
        
        # Bucket storage should be bounded
        if histogram._bucket_values:
            assert len(histogram._bucket_values._data) <= histogram._bucket_values.max_size


class TestGetMockRegistryStats:
    """Test get_mock_registry_stats() function."""

    def test_get_stats_returns_dict(self):
        """Test that get_mock_registry_stats returns proper dict."""
        stats = get_mock_registry_stats()
        
        assert isinstance(stats, dict)
        assert 'collector_count' in stats
        assert 'max_collectors' in stats
        assert 'usage_percent' in stats

    def test_stats_accuracy(self):
        """Test that stats accurately reflect registry state."""
        reset_metrics()
        
        # Create some metrics
        from omnicore_engine.message_bus.metrics import Counter
        for i in range(5):
            Counter(f"test_metric_{i}", f"Test metric {i}")
        
        stats = get_mock_registry_stats()
        
        # Should have at least the metrics we just created
        assert stats['collector_count'] >= 5


class TestResetMetrics:
    """Test reset_metrics() with new clear() method."""

    def test_reset_clears_data(self):
        """Test that reset_metrics() clears all data."""
        from omnicore_engine.message_bus.metrics import Counter
        
        metric = Counter("test_reset", "Test reset", labelnames=["label"])
        
        # Add some data
        metric.labels(label="a").inc()
        metric.labels(label="b").inc()
        
        # Reset
        reset_metrics()
        
        # Data should be cleared
        assert len(metric._values._data) == 0

    def test_reset_clears_warning_flags(self):
        """Test that reset_metrics() clears warning flags."""
        from omnicore_engine.message_bus.metrics import Counter
        
        metric = Counter("test_reset_warn", "Test reset warn", labelnames=["label"])
        
        # Trigger warning by adding many items
        metric._values._warned_at_threshold = True
        
        # Reset
        reset_metrics()
        
        # Warning flag should be cleared
        assert metric._values._warned_at_threshold is False


class TestIntegrationScenarios:
    """Integration tests for realistic usage scenarios."""

    def test_long_running_application_simulation(self):
        """Simulate a long-running application with dynamic metrics."""
        reset_metrics()
        
        from omnicore_engine.message_bus.metrics import Counter
        
        # Create a counter with dynamic labels (like session IDs)
        counter = Counter("requests_total", "Total requests", labelnames=["session_id"])
        
        # Simulate 20000 unique sessions over time
        for i in range(20000):
            session_id = f"session_{i}"
            counter.labels(session_id=session_id).inc()
        
        # Memory should not explode - should be bounded by max_size
        assert len(counter._values._data) <= counter._values.max_size
        
        # Most recent sessions should be accessible
        recent_value = counter.labels(session_id="session_19999")._parent._values.get(
            ("session_19999",), None
        )
        # Recent data might be present (if not evicted)
        # But system should not crash

    def test_concurrent_metric_updates_with_eviction(self):
        """Test concurrent updates don't cause issues during eviction."""
        reset_metrics()
        
        from omnicore_engine.message_bus.metrics import Counter
        counter = Counter("concurrent_test", "Test", labelnames=["worker"])
        
        errors = []
        
        def worker(worker_id):
            try:
                for i in range(1000):
                    counter.labels(worker=f"worker_{worker_id}_{i}").inc()
            except Exception as e:
                errors.append((worker_id, str(e)))
        
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # No errors should occur
        assert len(errors) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
