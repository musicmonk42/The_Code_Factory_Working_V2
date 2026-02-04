# test_cache.py

import concurrent.futures
import time
import unittest
from unittest.mock import patch

from omnicore_engine.message_bus.cache import MessageCache


class TestMessageCache(unittest.TestCase):
    """Test suite for MessageCache class."""

    def setUp(self):
        """Set up test fixtures before each test."""
        self.cache = MessageCache(maxsize=10, ttl=2)

    def test_initialization(self):
        """Test MessageCache initialization with valid parameters."""
        cache = MessageCache(maxsize=100, ttl=3600)
        self.assertEqual(cache.maxsize, 100)
        self.assertEqual(cache.ttl, 3600)
        self.assertEqual(len(cache.cache), 0)
        self.assertEqual(len(cache.access_times), 0)
        self.assertIsNotNone(cache._lock)

    def test_initialization_invalid_maxsize(self):
        """Test initialization with invalid maxsize values."""
        # Test zero maxsize
        with self.assertRaises(ValueError) as context:
            MessageCache(maxsize=0, ttl=100)
        self.assertIn("maxsize must be positive", str(context.exception))

        # Test negative maxsize
        with self.assertRaises(ValueError) as context:
            MessageCache(maxsize=-10, ttl=100)
        self.assertIn("maxsize must be positive", str(context.exception))

    def test_initialization_invalid_ttl(self):
        """Test initialization with invalid TTL values."""
        # Test zero TTL
        with self.assertRaises(ValueError) as context:
            MessageCache(maxsize=10, ttl=0)
        self.assertIn("ttl must be positive", str(context.exception))

        # Test negative TTL
        with self.assertRaises(ValueError) as context:
            MessageCache(maxsize=10, ttl=-100)
        self.assertIn("ttl must be positive", str(context.exception))

    def test_put_and_get_basic(self):
        """Test basic put and get operations."""
        # Put a value
        self.cache.put("key1", "value1")

        # Get the value
        result = self.cache.get("key1")
        self.assertEqual(result, "value1")

        # Verify cache state
        self.assertIn("key1", self.cache.cache)
        self.assertIn("key1", self.cache.access_times)

    def test_get_nonexistent_key(self):
        """Test getting a key that doesn't exist."""
        result = self.cache.get("nonexistent")
        self.assertIsNone(result)

    def test_put_update_existing_key(self):
        """Test updating an existing key."""
        # Put initial value
        self.cache.put("key1", "value1")
        initial_time = self.cache.access_times["key1"]

        # Wait a tiny bit to ensure time difference
        time.sleep(0.01)

        # Update the value
        self.cache.put("key1", "value2")
        updated_time = self.cache.access_times["key1"]

        # Verify update
        self.assertEqual(self.cache.get("key1"), "value2")
        self.assertGreater(updated_time, initial_time)
        # Cache size should remain 1
        self.assertEqual(len(self.cache.cache), 1)

    def test_ttl_expiration(self):
        """Test that items expire after TTL."""
        # Create cache with 1 second TTL
        cache = MessageCache(maxsize=10, ttl=1)

        # Put a value
        cache.put("key1", "value1")
        self.assertEqual(cache.get("key1"), "value1")

        # Wait for expiration
        time.sleep(1.1)

        # Should return None after expiration
        result = cache.get("key1")
        self.assertIsNone(result)

        # Key should be removed from cache
        self.assertNotIn("key1", cache.cache)
        self.assertNotIn("key1", cache.access_times)

    def test_lru_eviction(self):
        """Test LRU eviction when cache is full."""
        # Create small cache
        cache = MessageCache(maxsize=3, ttl=100)

        # Fill the cache
        cache.put("key1", "value1")
        time.sleep(0.01)
        cache.put("key2", "value2")
        time.sleep(0.01)
        cache.put("key3", "value3")

        # Access key1 and key3 to make key2 the LRU
        cache.get("key1")
        cache.get("key3")

        # Add new item, should evict key2 (LRU)
        cache.put("key4", "value4")

        # Verify eviction
        self.assertIsNone(cache.get("key2"))
        self.assertIsNotNone(cache.get("key1"))
        self.assertIsNotNone(cache.get("key3"))
        self.assertIsNotNone(cache.get("key4"))
        self.assertEqual(len(cache.cache), 3)

    @patch("omnicore_engine.message_bus.cache.logger")
    def test_evict_expired_items(self, mock_logger):
        """Test eviction of expired items when cache is full."""
        # Create cache with short TTL
        cache = MessageCache(maxsize=3, ttl=1)

        # Add items that will expire
        cache.put("expired1", "value1")
        cache.put("expired2", "value2")

        # Wait for expiration
        time.sleep(1.1)

        # Add fresh item
        cache.put("fresh", "fresh_value")

        # Fill cache to trigger eviction
        cache.put("key4", "value4")
        cache.put("key5", "value5")

        # Expired items should have been evicted
        self.assertIsNone(cache.get("expired1"))
        self.assertIsNone(cache.get("expired2"))
        self.assertIsNotNone(cache.get("fresh"))

        # Check that eviction was logged
        mock_logger.debug.assert_called()

    def test_access_time_update(self):
        """Test that access times are updated on get."""
        cache = MessageCache(maxsize=10, ttl=100)

        # Put a value
        cache.put("key1", "value1")
        initial_time = cache.access_times["key1"]

        # Wait and access
        time.sleep(0.01)
        cache.get("key1")
        updated_time = cache.access_times["key1"]

        # Access time should be updated
        self.assertGreater(updated_time, initial_time)

    def test_thread_safety_concurrent_puts(self):
        """Test thread safety with concurrent put operations."""
        cache = MessageCache(maxsize=100, ttl=10)
        num_threads = 3
        items_per_thread = 10

        def put_items(thread_id):
            for i in range(items_per_thread):
                key = f"thread_{thread_id}_item_{i}"
                value = f"value_{thread_id}_{i}"
                cache.put(key, value)

        # Run concurrent puts
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = [executor.submit(put_items, i) for i in range(num_threads)]
                concurrent.futures.wait(futures)
        except RuntimeError as e:
            if "can't start new thread" in str(e):
                self.skipTest("Thread limit reached in constrained environment")
            raise

        # Verify all items are in cache
        self.assertEqual(len(cache.cache), num_threads * items_per_thread)

        # Verify we can get all items
        for thread_id in range(num_threads):
            for item_id in range(items_per_thread):
                key = f"thread_{thread_id}_item_{item_id}"
                value = cache.get(key)
                self.assertEqual(value, f"value_{thread_id}_{item_id}")

    def test_thread_safety_concurrent_gets(self):
        """Test thread safety with concurrent get operations."""
        cache = MessageCache(maxsize=100, ttl=10)

        # Pre-populate cache
        for i in range(20):
            cache.put(f"key_{i}", f"value_{i}")

        results = {}

        def get_items(thread_id):
            thread_results = []
            for i in range(20):
                value = cache.get(f"key_{i}")
                thread_results.append((f"key_{i}", value))
            results[thread_id] = thread_results

        # Run concurrent gets
        num_threads = 3
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = [executor.submit(get_items, i) for i in range(num_threads)]
                concurrent.futures.wait(futures)
        except RuntimeError as e:
            if "can't start new thread" in str(e):
                self.skipTest("Thread limit reached in constrained environment")
            raise

        # Verify all threads got correct values
        for thread_id in range(num_threads):
            for key, value in results[thread_id]:
                expected = f"value_{int(key.split('_')[1])}"
                self.assertEqual(value, expected)

    def test_thread_safety_mixed_operations(self):
        """Test thread safety with mixed put/get operations."""
        cache = MessageCache(maxsize=50, ttl=10)
        errors = []

        def mixed_operations(thread_id):
            try:
                for i in range(20):
                    # Alternate between put and get
                    if i % 2 == 0:
                        cache.put(f"key_{thread_id}_{i}", f"value_{thread_id}_{i}")
                    else:
                        # Try to get recently added item
                        key = f"key_{thread_id}_{i-1}"
                        value = cache.get(key)
                        if value and value != f"value_{thread_id}_{i-1}":
                            errors.append(f"Wrong value for {key}: {value}")
            except Exception as e:
                errors.append(f"Thread {thread_id} error: {e}")

        # Run mixed operations concurrently
        num_threads = 2
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = [executor.submit(mixed_operations, i) for i in range(num_threads)]
                concurrent.futures.wait(futures)
        except RuntimeError as e:
            if "can't start new thread" in str(e):
                self.skipTest("Thread limit reached in constrained environment")
            raise

        # No errors should have occurred
        self.assertEqual(len(errors), 0, f"Errors occurred: {errors}")

    def test_eviction_with_full_cache(self):
        """Test eviction behavior when cache reaches maxsize."""
        cache = MessageCache(maxsize=3, ttl=100)

        # Fill cache beyond capacity
        for i in range(5):
            cache.put(f"key_{i}", f"value_{i}")
            time.sleep(0.01)  # Ensure different timestamps

        # Cache should only contain maxsize items
        self.assertEqual(len(cache.cache), 3)

        # Most recent items should be in cache
        self.assertIsNotNone(cache.get("key_2"))
        self.assertIsNotNone(cache.get("key_3"))
        self.assertIsNotNone(cache.get("key_4"))

        # Oldest items should have been evicted
        self.assertIsNone(cache.get("key_0"))
        self.assertIsNone(cache.get("key_1"))

    @patch("omnicore_engine.message_bus.cache.logger")
    def test_logging_initialization(self, mock_logger):
        """Test that initialization is logged."""
        cache = MessageCache(maxsize=50, ttl=3600)
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args
        self.assertIn("MessageCache initialized", call_args[0][0])

    @patch("omnicore_engine.message_bus.cache.logger")
    def test_logging_eviction(self, mock_logger):
        """Test that evictions are logged."""
        cache = MessageCache(maxsize=2, ttl=100)

        # Fill cache and trigger eviction
        cache.put("key1", "value1")
        cache.put("key2", "value2")
        cache.put("key3", "value3")  # Should trigger eviction

        # Check debug logging for eviction
        mock_logger.debug.assert_called()
        call_args = mock_logger.debug.call_args[0][0]
        self.assertIn("Evicted", call_args)

    def test_remove_method(self):
        """Test the internal _remove method."""
        cache = MessageCache(maxsize=10, ttl=100)

        # Add items
        cache.put("key1", "value1")
        cache.put("key2", "value2")

        # Manually remove an item
        cache._remove("key1")

        # Verify removal
        self.assertNotIn("key1", cache.cache)
        self.assertNotIn("key1", cache.access_times)
        self.assertIn("key2", cache.cache)

        # Removing non-existent key should not raise error
        cache._remove("nonexistent")  # Should not raise

    def test_cache_with_different_value_types(self):
        """Test cache with different value types."""
        cache = MessageCache(maxsize=10, ttl=100)

        # Test different types
        cache.put("string", "test_string")
        cache.put("int", 42)
        cache.put("float", 3.14)
        cache.put("list", [1, 2, 3])
        cache.put("dict", {"a": 1, "b": 2})
        cache.put("none", None)

        # Verify retrieval
        self.assertEqual(cache.get("string"), "test_string")
        self.assertEqual(cache.get("int"), 42)
        self.assertEqual(cache.get("float"), 3.14)
        self.assertEqual(cache.get("list"), [1, 2, 3])
        self.assertEqual(cache.get("dict"), {"a": 1, "b": 2})
        self.assertIsNone(cache.get("none"))

    def test_edge_case_immediate_expiration(self):
        """Test edge case where TTL is very small."""
        cache = MessageCache(maxsize=10, ttl=0.001)  # 1ms TTL

        cache.put("key1", "value1")
        time.sleep(0.002)  # Wait longer than TTL

        result = cache.get("key1")
        self.assertIsNone(result)

    def test_large_cache_performance(self):
        """Test performance with large cache."""
        cache = MessageCache(maxsize=10000, ttl=3600)

        # Add many items
        start_time = time.time()
        for i in range(10000):
            cache.put(f"key_{i}", f"value_{i}")
        put_time = time.time() - start_time

        # Performance assertion - should complete in reasonable time
        self.assertLess(put_time, 5.0, f"Put operations took {put_time}s")

        # Test retrieval performance
        start_time = time.time()
        for i in range(0, 10000, 100):  # Sample every 100th item
            cache.get(f"key_{i}")
        get_time = time.time() - start_time

        self.assertLess(get_time, 1.0, f"Get operations took {get_time}s")


if __name__ == "__main__":
    unittest.main(verbosity=2)
