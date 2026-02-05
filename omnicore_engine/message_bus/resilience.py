# message_bus/resilience.py
"""
Resilience components for the OmniCore Message Bus.

Provides retry policies and circuit breakers with both sync and async support.
Follows industry-standard patterns from:
- Michael Nygard's "Release It!" (Circuit Breaker pattern)
- Netflix's Hystrix design principles
- AWS Well-Architected Framework resilience best practices

Major improvements:
- Async-aware circuit breaker to prevent event loop blocking
- Hybrid locking strategy for both sync and async contexts
- Thread-safe and asyncio-safe implementations
"""

import asyncio
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class RetryPolicy:
    def __init__(self, max_retries: int = 3, backoff_factor: float = 0.01):
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative.")
        if backoff_factor <= 0:
            raise ValueError("backoff_factor must be positive.")
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor


def _is_async_context() -> bool:
    """
    Detect if we're running in an asyncio event loop context.

    Returns:
        True if an event loop is running in the current thread, False otherwise.
    """
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


class CircuitBreaker:
    """
    Hybrid sync/async circuit breaker implementation with multiprocessing support.

    This circuit breaker automatically detects the execution context (sync vs async)
    and uses the appropriate locking mechanism to prevent event loop blocking.

    States:
        - closed: Normal operation, requests allowed
        - open: Failure threshold exceeded, requests blocked
        - half-open: Testing if service has recovered

    Thread-safe and asyncio-safe through context-aware locking.
    Pickle-safe for multiprocessing and distributed testing scenarios.

    Industry Standards:
        - Circuit Breaker pattern: "Release It!" by Michael Nygard
        - Resilience patterns: Netflix's Hystrix implementation
        - Pickle protocol: PEP 307 for distributed computing compatibility
        - Used with pytest-xdist, Celery, Dask, and other distributed frameworks
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        """
        Initialize the circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery (half-open)

        Raises:
            ValueError: If parameters are invalid
        """
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive.")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be positive.")

        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time: Optional[float] = None
        self.state = "closed"

        # Hybrid locking: Both thread lock and async lock for context-aware safety
        self._thread_lock = threading.Lock()
        self._async_lock: Optional[asyncio.Lock] = None
        self._lock_init_lock = threading.Lock()

    def __getstate__(self):
        """
        Prepare CircuitBreaker for serialization (pickle protocol).

        This method is essential for multiprocessing and distributed testing scenarios,
        particularly with pytest-xdist's --forked mode. Synchronization primitives
        (threading.Lock, asyncio.Lock) cannot be pickled as they are process-specific
        and bound to the parent process's memory space.

        Industry Standard: Python's pickle protocol (PEP 307) is used by distributed
        computing frameworks (Celery, Dask, Ray) and multiprocessing libraries to
        enable safe object transfer across process boundaries.

        Returns:
            dict: Object state without unpicklable synchronization primitives

        Note:
            This follows the same pattern used in production-grade libraries like
            concurrent.futures.ProcessPoolExecutor and multiprocessing.Queue.
        """
        state = self.__dict__.copy()
        # Remove synchronization primitives which cannot be pickled
        # These will be reconstructed in the target process via __setstate__
        state['_async_lock'] = None
        state['_thread_lock'] = None
        state['_lock_init_lock'] = None
        return state

    def __setstate__(self, state):
        """
        Restore CircuitBreaker after deserialization in forked/spawned process.

        Reconstructs synchronization primitives that were excluded during pickling.
        This ensures the CircuitBreaker is fully functional in the new process with
        fresh locks bound to the new process's memory space.

        Industry Standard: Following the pickle protocol pattern used in concurrent
        and distributed computing frameworks where objects must be safely
        reconstructed across process boundaries.

        Args:
            state: Pickled object state dictionary

        Note:
            - Thread locks are recreated immediately for thread-safety
            - Async lock remains None and will be lazily initialized when needed
              via _get_async_lock() to ensure it's bound to the correct event loop
            - All circuit breaker state (failure counts, thresholds) is preserved
        """
        self.__dict__.update(state)
        # Reconstruct synchronization primitives in the new process
        self._thread_lock = threading.Lock()
        self._async_lock = None  # Lazy initialization via _get_async_lock()
        self._lock_init_lock = threading.Lock()

    def _get_async_lock(self) -> Optional[asyncio.Lock]:
        """
        Lazy initialization of asyncio.Lock.

        We can't create asyncio.Lock in __init__ because we might not have
        an event loop at initialization time. This method creates it on first use.

        Returns:
            asyncio.Lock instance bound to the current event loop, or None if
            no event loop is available
        """
        if self._async_lock is None:
            with self._lock_init_lock:
                # Double-check locking pattern for thread safety
                if self._async_lock is None:
                    try:
                        self._async_lock = asyncio.Lock()
                    except RuntimeError:
                        # No event loop, will fall back to thread lock
                        pass
        return self._async_lock

    def record_failure(self):
        """
        Record a failure. Opens circuit if threshold is reached.

        Thread-safe. Can be called from both sync and async contexts,
        but uses blocking lock (safe for sync code calling from async).
        """
        with self._thread_lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold and self.state == "closed":
                self.state = "open"
                logger.warning(
                    f"Circuit breaker opened due to repeated failures "
                    f"(threshold={self.failure_threshold})"
                )

    async def arecord_failure(self):
        """
        Async version of record_failure().

        Use this method when calling from async code to avoid blocking
        the event loop.
        """
        async_lock = self._get_async_lock()
        if async_lock is not None:
            async with async_lock:
                self.failure_count += 1
                self.last_failure_time = time.time()
                if (
                    self.failure_count >= self.failure_threshold
                    and self.state == "closed"
                ):
                    self.state = "open"
                    logger.warning(
                        f"Circuit breaker opened due to repeated failures "
                        f"(threshold={self.failure_threshold})"
                    )
        else:
            # Fallback to sync version if no event loop
            self.record_failure()

    def record_success(self):
        """
        Record a success. Closes circuit and resets failure count.

        Thread-safe. Can be called from both sync and async contexts.
        """
        with self._thread_lock:
            if self.state != "closed":
                logger.info("Circuit breaker reset to closed state after success.")
            self.failure_count = 0
            self.state = "closed"

    async def arecord_success(self):
        """
        Async version of record_success().

        Use this method when calling from async code to avoid blocking
        the event loop.
        """
        async_lock = self._get_async_lock()
        if async_lock is not None:
            async with async_lock:
                if self.state != "closed":
                    logger.info("Circuit breaker reset to closed state after success.")
                self.failure_count = 0
                self.state = "closed"
        else:
            # Fallback to sync version if no event loop
            self.record_success()

    def can_attempt(self) -> bool:
        """
        Check if an attempt can be made.

        Returns:
            True if circuit is closed or moved to half-open, False if open

        Thread-safe. Can be called from both sync and async contexts.
        """
        with self._thread_lock:
            if self.state == "closed":
                return True

            if (
                self.state == "open"
                and self.last_failure_time
                and (time.time() - self.last_failure_time) > self.recovery_timeout
            ):
                self.state = "half-open"
                logger.info(
                    "Circuit breaker moved to half-open state for trial attempt."
                )
                return True

            return False

    async def acan_attempt(self) -> bool:
        """
        Async version of can_attempt().

        Use this method when calling from async code to avoid blocking
        the event loop.

        Returns:
            True if circuit is closed or moved to half-open, False if open
        """
        async_lock = self._get_async_lock()
        if async_lock is not None:
            async with async_lock:
                if self.state == "closed":
                    return True

                if (
                    self.state == "open"
                    and self.last_failure_time
                    and (time.time() - self.last_failure_time) > self.recovery_timeout
                ):
                    self.state = "half-open"
                    logger.info(
                        "Circuit breaker moved to half-open state for trial attempt."
                    )
                    return True

                return False
        else:
            # Fallback to sync version if no event loop
            return self.can_attempt()
