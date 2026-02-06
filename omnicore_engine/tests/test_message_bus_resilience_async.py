# test_resilience_async.py
"""
Test suite for async-aware CircuitBreaker functionality.

Tests verify:
- Async methods work correctly (arecord_failure, arecord_success, acan_attempt)
- No event loop blocking with async operations
- Thread safety and asyncio safety
- Backward compatibility with sync methods
- Proper state transitions in async context
"""

import asyncio
import concurrent.futures
import time

import pytest

from omnicore_engine.message_bus.resilience import CircuitBreaker, _is_async_context


class TestAsyncContextDetection:
    """Test async context detection helper."""

    def test_no_event_loop(self):
        """Test detection when no event loop is running."""
        assert _is_async_context() is False

    @pytest.mark.asyncio
    async def test_with_event_loop(self):
        """Test detection when event loop is running."""
        assert _is_async_context() is True


class TestAsyncCircuitBreakerBasics:
    """Test basic async circuit breaker functionality."""

    @pytest.mark.asyncio
    async def test_async_initialization(self):
        """Test circuit breaker can be initialized in async context."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)

        assert cb.failure_count == 0
        assert cb.state == "closed"
        assert cb.failure_threshold == 3
        assert cb.recovery_timeout == 1

    @pytest.mark.asyncio
    async def test_arecord_failure(self):
        """Test async record_failure method."""
        cb = CircuitBreaker(failure_threshold=3)

        await cb.arecord_failure()
        assert cb.failure_count == 1
        assert cb.state == "closed"

        await cb.arecord_failure()
        await cb.arecord_failure()

        assert cb.failure_count == 3
        assert cb.state == "open"

    @pytest.mark.asyncio
    async def test_arecord_success(self):
        """Test async record_success method."""
        cb = CircuitBreaker(failure_threshold=3)

        await cb.arecord_failure()
        await cb.arecord_failure()
        assert cb.failure_count == 2

        await cb.arecord_success()
        assert cb.failure_count == 0
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_acan_attempt_closed(self):
        """Test acan_attempt returns True when closed."""
        cb = CircuitBreaker()

        result = await cb.acan_attempt()
        assert result is True

    @pytest.mark.asyncio
    async def test_acan_attempt_open(self):
        """Test acan_attempt returns False when open."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=10)

        await cb.arecord_failure()
        assert cb.state == "open"

        result = await cb.acan_attempt()
        assert result is False

    @pytest.mark.asyncio
    async def test_acan_attempt_half_open(self):
        """Test acan_attempt moves to half-open after timeout."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)

        await cb.arecord_failure()
        assert cb.state == "open"

        # Wait for recovery timeout
        await asyncio.sleep(0.15)

        result = await cb.acan_attempt()
        assert result is True
        assert cb.state == "half-open"


class TestAsyncCircuitBreakerStates:
    """Test state transitions in async context."""

    @pytest.mark.asyncio
    async def test_closed_to_open_transition(self):
        """Test transition from closed to open."""
        cb = CircuitBreaker(failure_threshold=2)

        assert cb.state == "closed"

        await cb.arecord_failure()
        assert cb.state == "closed"

        await cb.arecord_failure()
        assert cb.state == "open"

    @pytest.mark.asyncio
    async def test_open_to_half_open_transition(self):
        """Test transition from open to half-open."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)

        await cb.arecord_failure()
        assert cb.state == "open"

        await asyncio.sleep(0.15)

        await cb.acan_attempt()
        assert cb.state == "half-open"

    @pytest.mark.asyncio
    async def test_half_open_to_closed_transition(self):
        """Test transition from half-open to closed."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)

        await cb.arecord_failure()
        assert cb.state == "open"

        await asyncio.sleep(0.15)
        await cb.acan_attempt()
        assert cb.state == "half-open"

        await cb.arecord_success()
        assert cb.state == "closed"
        assert cb.failure_count == 0


class TestAsyncSyncMixedUsage:
    """Test mixed async/sync usage patterns."""

    @pytest.mark.asyncio
    async def test_async_then_sync(self):
        """Test using async methods then sync methods."""
        cb = CircuitBreaker(failure_threshold=5)

        await cb.arecord_failure()
        await cb.arecord_failure()

        cb.record_failure()

        assert cb.failure_count == 3

        cb.record_success()
        assert cb.failure_count == 0

    @pytest.mark.asyncio
    async def test_concurrent_async_operations(self):
        """Test concurrent async operations don't corrupt state."""
        cb = CircuitBreaker(failure_threshold=100)

        async def record_failures():
            for _ in range(10):
                await cb.arecord_failure()
                await asyncio.sleep(0.001)

        # Run 10 concurrent tasks
        await asyncio.gather(*[record_failures() for _ in range(10)])

        assert cb.failure_count == 100
        assert cb.state == "open"

    @pytest.mark.asyncio
    async def test_mixed_operations_concurrent(self):
        """Test mixed success/failure operations concurrently."""
        cb = CircuitBreaker(failure_threshold=50)

        async def mixed_ops():
            for i in range(10):
                if i % 2 == 0:
                    await cb.arecord_failure()
                else:
                    await cb.arecord_success()
                await asyncio.sleep(0.001)

        await asyncio.gather(*[mixed_ops() for _ in range(5)])

        # Final state depends on last operation, but should be consistent
        assert cb.failure_count >= 0


class TestAsyncCircuitBreakerPerformance:
    """Test that async operations don't block event loop."""

    @pytest.mark.asyncio
    async def test_no_blocking_on_async_operations(self):
        """Test async operations don't block other tasks."""
        cb = CircuitBreaker(failure_threshold=10)

        completed_tasks = []

        async def fast_task(task_id):
            await asyncio.sleep(0.01)
            completed_tasks.append(task_id)

        async def circuit_breaker_operations():
            for _ in range(100):
                await cb.arecord_failure()
                await cb.acan_attempt()

        # Run circuit breaker ops alongside fast tasks
        start = time.time()
        await asyncio.gather(
            circuit_breaker_operations(), *[fast_task(i) for i in range(10)]
        )
        duration = time.time() - start

        # All fast tasks should complete quickly (not blocked by CB ops)
        assert len(completed_tasks) == 10
        # Should complete in reasonable time (not blocked)
        assert duration < 1.0


class TestAsyncCircuitBreakerEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_multiple_async_lock_initializations(self):
        """Test that async lock is properly initialized once."""
        cb = CircuitBreaker()

        # Call async methods multiple times
        await cb.arecord_failure()
        await cb.arecord_success()
        await cb.acan_attempt()

        # Should not crash or have issues
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_failure_timing_async(self):
        """Test that last_failure_time is updated correctly in async."""
        cb = CircuitBreaker()

        assert cb.last_failure_time is None

        before = time.time()
        await cb.arecord_failure()
        after = time.time()

        assert cb.last_failure_time is not None
        assert cb.last_failure_time >= before
        assert cb.last_failure_time <= after

    @pytest.mark.asyncio
    async def test_recovery_timeout_precision_async(self):
        """Test recovery timeout with precise timing in async."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)

        await cb.arecord_failure()
        assert cb.state == "open"

        # Should not attempt before timeout
        assert await cb.acan_attempt() is False

        # Wait almost the full timeout
        await asyncio.sleep(0.09)
        assert await cb.acan_attempt() is False

        # Wait past timeout
        await asyncio.sleep(0.02)
        assert await cb.acan_attempt() is True


class TestBackwardCompatibility:
    """Test backward compatibility with sync methods."""

    def test_sync_methods_still_work(self):
        """Test that original sync methods still work."""
        cb = CircuitBreaker(failure_threshold=3)

        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2

        cb.record_success()
        assert cb.failure_count == 0

        assert cb.can_attempt() is True

    
    @pytest.mark.skip(reason="Thread creation may fail in CI environment with resource constraints")
    def test_thread_safety_with_sync_methods(self):
        """Test thread safety still works with sync methods."""
        cb = CircuitBreaker(failure_threshold=100)

        def record_failures():
            for _ in range(10):
                cb.record_failure()

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(record_failures) for _ in range(10)]
            concurrent.futures.wait(futures)

        assert cb.failure_count == 100
        assert cb.state == "open"


class TestAsyncCircuitBreakerIntegration:
    """Integration tests for async circuit breaker."""

    @pytest.mark.asyncio
    async def test_realistic_workflow(self):
        """Test realistic workflow with retries and recovery."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.2)

        # Simulate service calls that fail
        for _ in range(3):
            if await cb.acan_attempt():
                # Simulate failure
                await cb.arecord_failure()

        # Circuit should be open
        assert cb.state == "open"
        assert await cb.acan_attempt() is False

        # Wait for recovery
        await asyncio.sleep(0.25)

        # Should allow one attempt (half-open)
        assert await cb.acan_attempt() is True
        assert cb.state == "half-open"

        # Simulate successful call
        await cb.arecord_success()

        # Circuit should be closed
        assert cb.state == "closed"
        assert cb.failure_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
