# message_bus/resilience.py

import threading
import time
import logging



logger = logging.getLogger(__name__)


class RetryPolicy:
    def __init__(self, max_retries: int = 3, backoff_factor: float = 0.01):
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative.")
        if backoff_factor <= 0:
            raise ValueError("backoff_factor must be positive.")
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive.")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be positive.")
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time = None
        self.state = "closed"
        self._lock = threading.Lock()

    def record_failure(self):
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold and self.state == "closed":
                self.state = "open"
                logger.warning(
                    "Circuit breaker opened due to repeated failures",
                    threshold=self.failure_threshold,
                )

    def record_success(self):
        with self._lock:
            if self.state != "closed":
                logger.info("Circuit breaker reset to closed state after success.")
            self.failure_count = 0
            self.state = "closed"

    def can_attempt(self) -> bool:
        with self._lock:
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
