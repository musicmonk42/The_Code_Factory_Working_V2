# test_resilience.py

import concurrent.futures
import time
import unittest
from unittest.mock import patch

from omnicore_engine.message_bus.resilience import CircuitBreaker, RetryPolicy


class TestRetryPolicy(unittest.TestCase):
    """Test suite for RetryPolicy class."""

    def test_initialization_valid(self):
        """Test RetryPolicy initialization with valid parameters."""
        policy = RetryPolicy(max_retries=5, backoff_factor=2.0)

        self.assertEqual(policy.max_retries, 5)
        self.assertEqual(policy.backoff_factor, 2.0)

    def test_initialization_defaults(self):
        """Test RetryPolicy initialization with default values."""
        policy = RetryPolicy()

        self.assertEqual(policy.max_retries, 3)
        self.assertEqual(policy.backoff_factor, 0.01)

    def test_initialization_zero_retries(self):
        """Test RetryPolicy with zero max_retries (no retries)."""
        policy = RetryPolicy(max_retries=0)
        self.assertEqual(policy.max_retries, 0)

    def test_initialization_negative_retries(self):
        """Test RetryPolicy initialization with negative max_retries."""
        with self.assertRaises(ValueError) as context:
            RetryPolicy(max_retries=-1)

        self.assertIn("max_retries must be non-negative", str(context.exception))

    def test_initialization_zero_backoff(self):
        """Test RetryPolicy initialization with zero backoff_factor."""
        with self.assertRaises(ValueError) as context:
            RetryPolicy(backoff_factor=0)

        self.assertIn("backoff_factor must be positive", str(context.exception))

    def test_initialization_negative_backoff(self):
        """Test RetryPolicy initialization with negative backoff_factor."""
        with self.assertRaises(ValueError) as context:
            RetryPolicy(backoff_factor=-0.5)

        self.assertIn("backoff_factor must be positive", str(context.exception))

    def test_backoff_calculation(self):
        """Test exponential backoff calculation."""
        policy = RetryPolicy(max_retries=5, backoff_factor=2.0)

        # Calculate expected delays for each retry
        expected_delays = [
            2.0 * (2**0),  # 2.0
            2.0 * (2**1),  # 4.0
            2.0 * (2**2),  # 8.0
            2.0 * (2**3),  # 16.0
            2.0 * (2**4),  # 32.0
        ]

        for attempt, expected_delay in enumerate(expected_delays):
            actual_delay = policy.backoff_factor * (2**attempt)
            self.assertEqual(actual_delay, expected_delay)

    def test_small_backoff_factor(self):
        """Test with very small backoff factor."""
        policy = RetryPolicy(max_retries=3, backoff_factor=0.001)

        self.assertEqual(policy.backoff_factor, 0.001)

        # First retry delay should be 0.001 seconds
        first_delay = policy.backoff_factor * (2**0)
        self.assertEqual(first_delay, 0.001)

    def test_large_values(self):
        """Test with large retry and backoff values."""
        policy = RetryPolicy(max_retries=100, backoff_factor=10.0)

        self.assertEqual(policy.max_retries, 100)
        self.assertEqual(policy.backoff_factor, 10.0)


class TestCircuitBreaker(unittest.TestCase):
    """Test suite for CircuitBreaker class."""

    def test_initialization_valid(self):
        """Test CircuitBreaker initialization with valid parameters."""
        cb = CircuitBreaker(failure_threshold=10, recovery_timeout=30)

        self.assertEqual(cb.failure_threshold, 10)
        self.assertEqual(cb.recovery_timeout, 30)
        self.assertEqual(cb.failure_count, 0)
        self.assertEqual(cb.state, "closed")
        self.assertIsNone(cb.last_failure_time)

    def test_initialization_defaults(self):
        """Test CircuitBreaker initialization with default values."""
        cb = CircuitBreaker()

        self.assertEqual(cb.failure_threshold, 5)
        self.assertEqual(cb.recovery_timeout, 60)
        self.assertEqual(cb.state, "closed")

    def test_initialization_zero_threshold(self):
        """Test CircuitBreaker with zero failure_threshold."""
        with self.assertRaises(ValueError) as context:
            CircuitBreaker(failure_threshold=0)

        self.assertIn("failure_threshold must be positive", str(context.exception))

    def test_initialization_negative_threshold(self):
        """Test CircuitBreaker with negative failure_threshold."""
        with self.assertRaises(ValueError) as context:
            CircuitBreaker(failure_threshold=-5)

        self.assertIn("failure_threshold must be positive", str(context.exception))

    def test_initialization_zero_timeout(self):
        """Test CircuitBreaker with zero recovery_timeout."""
        with self.assertRaises(ValueError) as context:
            CircuitBreaker(recovery_timeout=0)

        self.assertIn("recovery_timeout must be positive", str(context.exception))

    def test_initialization_negative_timeout(self):
        """Test CircuitBreaker with negative recovery_timeout."""
        with self.assertRaises(ValueError) as context:
            CircuitBreaker(recovery_timeout=-10)

        self.assertIn("recovery_timeout must be positive", str(context.exception))

    @patch("omnicore_engine.message_bus.resilience.logger")
    def test_record_failure_below_threshold(self, mock_logger):
        """Test recording failures below threshold."""
        cb = CircuitBreaker(failure_threshold=3)

        # Record first failure
        cb.record_failure()
        self.assertEqual(cb.failure_count, 1)
        self.assertIsNotNone(cb.last_failure_time)
        self.assertEqual(cb.state, "closed")

        # Record second failure
        cb.record_failure()
        self.assertEqual(cb.failure_count, 2)
        self.assertEqual(cb.state, "closed")

        # Still closed, no logging yet
        mock_logger.warning.assert_not_called()

    @patch("omnicore_engine.message_bus.resilience.logger")
    def test_record_failure_reaches_threshold(self, mock_logger):
        """Test circuit opens when failure threshold is reached."""
        cb = CircuitBreaker(failure_threshold=3)

        # Record failures up to threshold
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()

        self.assertEqual(cb.failure_count, 3)
        self.assertEqual(cb.state, "open")
        self.assertIsNotNone(cb.last_failure_time)

        # Should log warning
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args[0][0]
        self.assertIn("Circuit breaker opened", call_args)

    @patch("omnicore_engine.message_bus.resilience.logger")
    def test_record_failure_when_already_open(self, mock_logger):
        """Test recording failure when circuit is already open."""
        cb = CircuitBreaker(failure_threshold=2)

        # Open the circuit
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, "open")
        mock_logger.warning.assert_called_once()

        # Record another failure
        cb.record_failure()
        self.assertEqual(cb.failure_count, 3)
        self.assertEqual(cb.state, "open")

        # Should not log again
        mock_logger.warning.assert_called_once()

    @patch("omnicore_engine.message_bus.resilience.logger")
    def test_record_success_resets_state(self, mock_logger):
        """Test recording success resets the circuit."""
        cb = CircuitBreaker(failure_threshold=3)

        # Build up some failures
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.failure_count, 2)

        # Record success
        cb.record_success()

        self.assertEqual(cb.failure_count, 0)
        self.assertEqual(cb.state, "closed")

    @patch("omnicore_engine.message_bus.resilience.logger")
    def test_record_success_from_open_state(self, mock_logger):
        """Test recording success from open state."""
        cb = CircuitBreaker(failure_threshold=2)

        # Open the circuit
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, "open")

        # Record success
        cb.record_success()

        self.assertEqual(cb.failure_count, 0)
        self.assertEqual(cb.state, "closed")

        # Should log the reset
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args[0][0]
        self.assertIn("Circuit breaker reset to closed", call_args)

    def test_can_attempt_when_closed(self):
        """Test can_attempt returns True when circuit is closed."""
        cb = CircuitBreaker()

        self.assertTrue(cb.can_attempt())

    def test_can_attempt_when_open_before_timeout(self):
        """Test can_attempt returns False when circuit is open and timeout not reached."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=10)

        cb.record_failure()
        self.assertEqual(cb.state, "open")

        # Immediately after opening
        self.assertFalse(cb.can_attempt())

    @patch("omnicore_engine.message_bus.resilience.time.time")
    @patch("omnicore_engine.message_bus.resilience.logger")
    def test_can_attempt_when_open_after_timeout(self, mock_logger, mock_time):
        """Test can_attempt returns True after recovery timeout (half-open)."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=10)

        # Set initial time
        mock_time.return_value = 1000.0

        cb.record_failure()
        self.assertEqual(cb.state, "open")

        # Move time forward past recovery timeout
        mock_time.return_value = 1011.0  # 11 seconds later

        self.assertTrue(cb.can_attempt())
        self.assertEqual(cb.state, "half-open")

        # Should log state change
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args[0][0]
        self.assertIn("half-open", call_args)

    def test_half_open_state_behavior(self):
        """Test half-open state behavior."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)

        # Open the circuit
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, "open")

        # Wait for recovery timeout
        time.sleep(0.02)

        # Should move to half-open
        self.assertTrue(cb.can_attempt())
        self.assertEqual(cb.state, "half-open")

        # Success should close the circuit
        cb.record_success()
        self.assertEqual(cb.state, "closed")

    def test_thread_safety_concurrent_failures(self):
        """Test thread safety with concurrent failure recording."""
        cb = CircuitBreaker(failure_threshold=100)

        def record_failures():
            for _ in range(10):
                cb.record_failure()

        # Run concurrent failure recording
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(record_failures) for _ in range(10)]
            concurrent.futures.wait(futures)

        # Should have recorded all failures
        self.assertEqual(cb.failure_count, 100)
        self.assertEqual(cb.state, "open")

    def test_thread_safety_mixed_operations(self):
        """Test thread safety with mixed operations."""
        cb = CircuitBreaker(failure_threshold=50)
        errors = []

        def mixed_operations(thread_id):
            try:
                for i in range(20):
                    if i % 3 == 0:
                        cb.record_failure()
                    elif i % 3 == 1:
                        cb.record_success()
                    else:
                        cb.can_attempt()
            except Exception as e:
                errors.append((thread_id, str(e)))

        # Run mixed operations concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(mixed_operations, i) for i in range(5)]
            concurrent.futures.wait(futures)

        # No errors should occur
        self.assertEqual(len(errors), 0)

    def test_state_transitions(self):
        """Test all state transitions."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)

        # Start in closed state
        self.assertEqual(cb.state, "closed")

        # Closed -> Open (via failures)
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, "open")

        # Open -> Half-Open (via timeout)
        time.sleep(0.02)
        self.assertTrue(cb.can_attempt())
        self.assertEqual(cb.state, "half-open")

        # Half-Open -> Closed (via success)
        cb.record_success()
        self.assertEqual(cb.state, "closed")

        # Closed -> Open again
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, "open")

        # Open -> Closed directly (via success)
        cb.record_success()
        self.assertEqual(cb.state, "closed")

    def test_failure_timing(self):
        """Test that last_failure_time is updated correctly."""
        cb = CircuitBreaker()

        self.assertIsNone(cb.last_failure_time)

        before = time.time()
        cb.record_failure()
        after = time.time()

        self.assertIsNotNone(cb.last_failure_time)
        self.assertGreaterEqual(cb.last_failure_time, before)
        self.assertLessEqual(cb.last_failure_time, after)

    def test_recovery_timeout_precision(self):
        """Test recovery timeout with precise timing."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)

        # Open the circuit
        cb.record_failure()

        # Should not attempt before timeout
        self.assertFalse(cb.can_attempt())

        # Wait almost the full timeout
        time.sleep(0.09)
        self.assertFalse(cb.can_attempt())

        # Wait past timeout
        time.sleep(0.02)
        self.assertTrue(cb.can_attempt())


class TestIntegration(unittest.TestCase):
    """Integration tests for resilience components."""

    def test_retry_with_circuit_breaker(self):
        """Test using RetryPolicy with CircuitBreaker."""
        policy = RetryPolicy(max_retries=3, backoff_factor=0.01)
        cb = CircuitBreaker(failure_threshold=5)

        attempts = 0

        for retry in range(policy.max_retries + 1):
            if cb.can_attempt():
                attempts += 1
                # Simulate failure
                cb.record_failure()

                if retry < policy.max_retries:
                    time.sleep(policy.backoff_factor * (2**retry))

        self.assertEqual(attempts, 4)  # Initial + 3 retries
        self.assertEqual(cb.failure_count, 4)
        self.assertEqual(cb.state, "closed")  # Still under threshold


if __name__ == "__main__":
    unittest.main(verbosity=2)
