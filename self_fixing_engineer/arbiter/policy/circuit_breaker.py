# D:\SFE\self_fixing_engineer\arbiter\policy\circuit_breaker.py

"""
Circuit breaker for LLM policy API calls, with per-provider state management and optional Redis persistence.

Required ArbiterConfig attributes (to be defined in config.py):
- LLM_API_FAILURE_THRESHOLD: int, positive, default 3
- LLM_API_BACKOFF_MAX_SECONDS: float, positive, default 60.0
- REDIS_URL: str, optional, Redis connection URL
- CIRCUIT_BREAKER_STATE_TTL_SECONDS: int, optional, Redis key TTL in seconds, default 86400
- CIRCUIT_BREAKER_CLEANUP_INTERVAL_SECONDS: int, optional, interval for cleanup task in seconds, default 3600

Environment variables:
- OTLP_ENDPOINT: str, optional, OpenTelemetry OTLP endpoint, default 'http://localhost:4317'
- CIRCUIT_BREAKER_STATE_TTL_SECONDS: int, optional, overrides config attribute, minimum 3600
- CIRCUIT_BREAKER_CLEANUP_INTERVAL_SECONDS: int, optional, overrides config attribute, minimum 60
- REDIS_MAX_CONNECTIONS: int, optional, Redis connection pool size, default 100
- REDIS_SOCKET_TIMEOUT: float, optional, Redis socket timeout in seconds, default 5.0
- REDIS_SOCKET_CONNECT_TIMEOUT: float, optional, Redis socket connect timeout in seconds, default 5.0
- CONFIG_REFRESH_INTERVAL_SECONDS: int, optional, config refresh interval in seconds, default 300
- PAUSE_CIRCUIT_BREAKER_TASKS: str, optional, set to 'true' to pause cleanup and refresh tasks, default 'false'
- CIRCUIT_BREAKER_MAX_PROVIDERS: int, optional, maximum number of providers, default 1000
- CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL: float, optional, minimum interval between Redis operations in seconds, default 0.1
- CIRCUIT_BREAKER_CRITICAL_PROVIDERS: str, optional, comma-separated list of providers exempt from cleanup, default ''
- ENVIRONMENT: str, optional, set to 'test' to use InMemorySpanExporter
- PYTEST_CURRENT_TEST: str, optional, set by pytest during test runs

Initialization:
- Call `start_cleanup_task()`, `start_config_refresh_task()`, and `register_shutdown_handler()` during application startup (e.g., in core.py or main app).
- Verify task initialization by checking logs for 'Started circuit breaker cleanup task' and 'Started circuit breaker config refresh task'.
- Ensure tracer is initialized (automatically handled at module import via centralized config).
"""

import asyncio
import atexit
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Union

import redis.asyncio as redis

# Import the centralized tracer configuration
from arbiter.otel_config import get_tracer

# Import all prometheus_client types at once at the top
from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Assuming ArbiterConfig is correctly imported or mocked globally
from .config import get_config

# Use the factory function to get the config instance
ArbiterConfig = get_config

logger = logging.getLogger(__name__)

# --- Thread-safe metric creation helper (MUST BE BEFORE ANY METRIC USE) ---
_metrics_lock = threading.Lock()


def get_or_create_metric(
    metric_class: type,
    name: str,
    documentation: str,
    labelnames: Union[tuple, list] = (),
    initial_value: float = 0.0,
    buckets=None,
):
    """
    Thread-safe helper to create a Prometheus metric or retrieve an existing one.
    Args:
        metric_class: The Prometheus metric class (e.g., Counter, Gauge).
        name: The metric name.
        documentation: A description of the metric.
        labelnames: A tuple or list of label names.
        initial_value: The initial value for the metric (only for Gauge).
        buckets: The buckets for Histogram metrics.
    Returns:
        The created or retrieved Prometheus metric object.
    """
    with _metrics_lock:
        if name in REGISTRY._names_to_collectors:
            return REGISTRY._names_to_collectors[name]

        # Create metric with appropriate arguments
        if metric_class == Histogram and buckets is not None:
            metric = metric_class(
                name, documentation, labelnames=labelnames, buckets=buckets
            )
        else:
            metric = metric_class(name, documentation, labelnames=labelnames)

        # Check if the metric is a Gauge type and set initial value if provided
        if metric_class == Gauge and initial_value is not None and not labelnames:
            metric.set(initial_value)

        return metric


# Create all metrics BEFORE they're used anywhere
LLM_API_FAILURE_COUNT = get_or_create_metric(
    Counter,
    "llm_api_failure_count",
    "Total failures for LLM policy API calls.",
    labelnames=("provider",),
)
LLM_CIRCUIT_BREAKER_STATE = get_or_create_metric(
    Gauge,
    "llm_circuit_breaker_state",
    "Current state of the LLM policy API circuit breaker (0=closed, 1=open).",
    labelnames=("provider",),
)
LLM_CIRCUIT_BREAKER_TRIPS = get_or_create_metric(
    Counter,
    "llm_circuit_breaker_trips_total",
    "Total number of times the circuit breaker has tripped.",
    labelnames=("provider",),
)
LLM_CIRCUIT_BREAKER_ERRORS = get_or_create_metric(
    Counter,
    "llm_circuit_breaker_errors_total",
    "Total errors in circuit breaker operations",
    labelnames=("error_type",),
)
LLM_CIRCUIT_BREAKER_TRANSITIONS = get_or_create_metric(
    Counter,
    "llm_circuit_breaker_transitions_total",
    "Total circuit breaker state transitions",
    labelnames=("provider", "from_state", "to_state"),
)
CIRCUIT_BREAKER_CLEANUP_OPERATIONS = get_or_create_metric(
    Counter,
    "circuit_breaker_cleanup_operations_total",
    "Total circuit breaker state cleanup operations",
    labelnames=("provider", "result"),
)
REDIS_OPERATION_LATENCY = get_or_create_metric(
    Histogram,
    "circuit_breaker_redis_operation_latency_seconds",
    "Latency of Redis operations for circuit breaker",
    labelnames=("provider", "operation"),
    buckets=(0.001, 0.01, 0.1, 0.5, 1, 2, 5),
)
CONFIG_REFRESH_OPERATIONS = get_or_create_metric(
    Counter,
    "circuit_breaker_config_refresh_operations_total",
    "Total configuration refresh operations",
    labelnames=("result",),
)
TASK_STATE_TRANSITIONS = get_or_create_metric(
    Counter,
    "circuit_breaker_task_state_transitions_total",
    "Total task state transitions (pause/resume)",
    labelnames=("task", "state"),
)

# Get tracer using centralized configuration
tracer = get_tracer("circuit_breaker")


def sanitize_log_message(message: Optional[str]) -> str:
    """
    Sanitizes log messages to prevent injection or excessive length.

    Args:
        message: The log message to sanitize.

    Returns:
        A sanitized string with control characters removed and truncated.
    """
    if not message:
        return ""
    # Remove control characters and truncate
    sanitized = re.sub(r"[\n\r\t\b\f]", "", message)
    return sanitized[:200]


def _sanitize_provider(provider: str) -> str:
    """Sanitizes provider names for logging."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", provider)[:50]


_last_validation_error_time: float = 0.0
_validation_error_interval: float = 1.0  # 1 error log per second


def _log_validation_error(message: str, error_type: str) -> None:
    """Logs validation errors with rate-limiting."""
    global _last_validation_error_time
    current_time = time.monotonic()
    if current_time - _last_validation_error_time >= _validation_error_interval:
        logger.error(message)
        LLM_CIRCUIT_BREAKER_ERRORS.labels(error_type=error_type).inc()
        _last_validation_error_time = current_time


# --- Redis Connection Pool Management ---
_global_connection_pool: Optional[redis.ConnectionPool] = None
_connection_pool_lock = threading.Lock()


def get_global_connection_pool(
    config: "ArbiterConfig",
) -> Optional[redis.ConnectionPool]:
    """
    Returns a shared Redis connection pool with validated settings.
    Handles reinitialization if the pool becomes invalid.
    """
    global _global_connection_pool
    with _connection_pool_lock:
        if _global_connection_pool is None and getattr(config, "REDIS_URL", None):
            try:
                # Use environment variables as fallbacks for Redis settings
                max_connections_str = os.getenv(
                    "REDIS_MAX_CONNECTIONS",
                    str(getattr(config, "REDIS_MAX_CONNECTIONS", 100)),
                )
                socket_timeout_str = os.getenv(
                    "REDIS_SOCKET_TIMEOUT",
                    str(getattr(config, "REDIS_SOCKET_TIMEOUT", 5.0)),
                )
                socket_connect_timeout_str = os.getenv(
                    "REDIS_SOCKET_CONNECT_TIMEOUT",
                    str(getattr(config, "REDIS_SOCKET_CONNECT_TIMEOUT", 5.0)),
                )

                try:
                    max_connections = int(max_connections_str)
                    if max_connections <= 0:
                        raise ValueError("REDIS_MAX_CONNECTIONS must be positive")
                except ValueError:
                    _log_validation_error(
                        f"Invalid REDIS_MAX_CONNECTIONS: {max_connections_str}. Using default: 100",
                        "invalid_config",
                    )
                    max_connections = 100
                try:
                    socket_timeout = float(socket_timeout_str)
                    if socket_timeout <= 0:
                        raise ValueError("REDIS_SOCKET_TIMEOUT must be positive")
                except ValueError:
                    _log_validation_error(
                        f"Invalid REDIS_SOCKET_TIMEOUT: {socket_timeout_str}. Using default: 5.0",
                        "invalid_config",
                    )
                    socket_timeout = 5.0
                try:
                    socket_connect_timeout = float(socket_connect_timeout_str)
                    if socket_connect_timeout <= 0:
                        raise ValueError(
                            "REDIS_SOCKET_CONNECT_TIMEOUT must be positive"
                        )
                except ValueError:
                    _log_validation_error(
                        f"Invalid REDIS_SOCKET_CONNECT_TIMEOUT: {socket_connect_timeout_str}. Using default: 5.0",
                        "invalid_config",
                    )
                    socket_connect_timeout = 5.0
                try:
                    _global_connection_pool = redis.ConnectionPool.from_url(
                        config.REDIS_URL,
                        max_connections=max_connections,
                        decode_responses=True,
                        socket_timeout=socket_timeout,
                        socket_connect_timeout=socket_connect_timeout,
                    )
                except ValueError as e:
                    _log_validation_error(
                        f"Invalid REDIS_URL: {e}", "invalid_redis_url"
                    )
                    _global_connection_pool = None
            except Exception as e:
                _log_validation_error(
                    f"Failed to create global Redis connection pool: {e}",
                    "redis_pool_creation_failed",
                )
                _global_connection_pool = None
        elif _global_connection_pool:
            # Check if pool is still valid, and if not, reinitialize
            try:
                test_client = redis.Redis(connection_pool=_global_connection_pool)
                # Check if we're in an async context
                try:
                    asyncio.get_running_loop()
                    # We're already in an async context, can't use asyncio.run
                    logger.debug("Skipping Redis ping check - already in async context")
                except RuntimeError:
                    # No running loop, safe to use asyncio.run
                    try:
                        asyncio.run(test_client.ping())
                    except Exception:
                        # If ping fails, invalidate the pool
                        logger.warning("Redis connection pool invalid. Reinitializing.")
                        _global_connection_pool = None
                        # Recursive call to attempt reinitialization
                        return get_global_connection_pool(config)
            except redis.RedisError:
                logger.warning("Redis connection pool invalid. Reinitializing.")
                _global_connection_pool = None
                # Recursive call to attempt reinitialization
                return get_global_connection_pool(config)
        return _global_connection_pool


class InMemoryBreakerStateManager:
    """
    Manages the state of a single circuit breaker in memory.
    """

    def __init__(self, provider: str):
        self.provider = provider
        self._state = {
            "failures": 0,
            "last_failure_time": datetime.min.replace(tzinfo=timezone.utc),
            "next_try_after": datetime.min.replace(tzinfo=timezone.utc),
            "circuit_state": "closed",  # explicit state: "closed", "half-open", "open"
        }
        self._lock = asyncio.Lock()

    async def get_state(self) -> dict:
        """Fetches the current circuit breaker state from memory.
        
        Note: This method does NOT acquire the internal lock. Callers should
        use state_lock() if they need atomic read-modify-write operations.
        """
        return self._state.copy()

    async def set_state(self, state: dict) -> None:
        """Saves the current circuit breaker state to memory.
        
        Note: This method does NOT acquire the internal lock. Callers should
        use state_lock() if they need atomic read-modify-write operations.
        """
        # Validate and clamp failures to [0, 1000]
        if "failures" in state:
            state["failures"] = min(max(int(state["failures"]), 0), 1000)
        self._state.update(state)

    def state_lock(self):
        """Context manager for async-safe access to the state."""
        return self._lock

    async def close(self) -> None:
        """No-op close method for compatibility."""
        pass


class CircuitBreakerState:
    """
    Manages the state of a single circuit breaker, with optional Redis persistence.
    """

    def __init__(self, provider: str, config: "ArbiterConfig"):
        self.provider = provider
        self.config = config
        self.redis_client: Optional[redis.Redis] = None
        self._state_lock = asyncio.Lock()
        self._in_memory_state: Dict[str, Any] = {
            "failures": 0,
            "last_failure_time": datetime.min.replace(tzinfo=timezone.utc),
            "next_try_after": datetime.min.replace(tzinfo=timezone.utc),
        }
        self._last_operation_time: float = 0.0
        min_interval_str = os.getenv("CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL", "0.1")
        try:
            self._min_operation_interval = float(min_interval_str)
            if self._min_operation_interval <= 0:
                raise ValueError(
                    "CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL must be positive"
                )
        except ValueError:
            _log_validation_error(
                f"Invalid CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL: {min_interval_str}. Using default: 0.1",
                "invalid_config",
            )
            self._min_operation_interval = 0.1

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(redis.RedisError),
        before_sleep=lambda retry_state: logger.debug(
            f"Retrying Redis connection for {retry_state.args[0].provider if retry_state.args else 'unknown'}: attempt {retry_state.attempt_number}"
        ),
    )
    async def initialize(self) -> None:
        """Initializes the Redis connection using the global connection pool."""
        redis_url = getattr(self.config, "REDIS_URL", None)
        # Validate REDIS_URL format to prevent connection errors
        if redis_url and not re.match(r"^redis://[\w.-]+(:\d+)?(/.*)?$", redis_url):
            _log_validation_error(
                f"Invalid REDIS_URL format: {redis_url}", "invalid_redis_url"
            )
            self.redis_client = None
            return
        if redis_url:
            try:
                pool = get_global_connection_pool(self.config)
                if pool:
                    self.redis_client = redis.Redis(connection_pool=pool)
                    await self.redis_client.ping()
                    logger.debug(
                        f"Connected to Redis for circuit breaker: {_sanitize_provider(self.provider)}"
                    )
                else:
                    self.redis_client = None
                    logger.warning(
                        f"No Redis connection pool available for {_sanitize_provider(self.provider)}. Using in-memory state."
                    )
            except redis.ConnectionError as e:
                _log_validation_error(
                    f"Redis connection pool exhausted or failed for {_sanitize_provider(self.provider)}: {e}",
                    "redis_connection_pool_exhausted",
                )
                self.redis_client = None
                raise redis.RedisError(
                    f"Failed to connect to Redis for {_sanitize_provider(self.provider)}: {e}"
                )
            except Exception as e:
                _log_validation_error(
                    f"Failed to connect to Redis for {_sanitize_provider(self.provider)}: {e}",
                    "redis_connection_failed",
                )
                self.redis_client = None
                raise redis.RedisError(
                    f"Failed to connect to Redis for {_sanitize_provider(self.provider)}: {e}"
                )

    async def close(self) -> None:
        """
        Closes the Redis connection for this state manager.
        Note: Do not close the connection pool itself, as it's shared.
        """
        if self.redis_client:
            await self.redis_client.close()
            logger.debug(
                f"Closed Redis connection for circuit breaker: {_sanitize_provider(self.provider)}"
            )
            self.redis_client = None

    def state_lock(self):
        """Context manager for async-safe access to the state."""
        return self._state_lock

    async def _rate_limit(self) -> None:
        """Enforces a simple rate-limiting mechanism for Redis operations to prevent abuse."""
        with tracer.start_as_current_span(
            "rate_limit", attributes={"provider": self.provider}
        ) as span:
            current_time = time.monotonic()
            elapsed = current_time - self._last_operation_time
            if elapsed < self._min_operation_interval:
                delay = self._min_operation_interval - elapsed
                span.set_attribute("delay_seconds", delay)
                await asyncio.sleep(delay)
            self._last_operation_time = time.monotonic()

    async def _check_redis_health(self) -> bool:
        """Checks if Redis connection is healthy."""
        if not self.redis_client:
            return False
        try:
            await self.redis_client.ping()
            return True
        except redis.RedisError as e:
            logger.error(
                f"Redis health check failed for {_sanitize_provider(self.provider)}: {e}"
            )
            LLM_CIRCUIT_BREAKER_ERRORS.labels(
                error_type="redis_health_check_failed"
            ).inc()
            return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(redis.RedisError),
        before_sleep=lambda retry_state: logger.debug(
            f"Retrying Redis get operation for provider: attempt {retry_state.attempt_number}"
        ),
    )
    async def get_state(self) -> Dict[str, Any]:
        """Fetches the current circuit breaker state from Redis or in-memory, with validation."""
        start_time = time.monotonic()
        await self._rate_limit()
        if not await self._check_redis_health():
            REDIS_OPERATION_LATENCY.labels(
                provider=self.provider, operation="get_state"
            ).observe(time.monotonic() - start_time)
            return self._in_memory_state.copy()
        try:
            state_data = await self.redis_client.hgetall(
                f"circuit_breaker:{self.provider}"
            )
            # If key doesn't exist, return in-memory initial state
            if not state_data:
                REDIS_OPERATION_LATENCY.labels(
                    provider=self.provider, operation="get_state"
                ).observe(time.monotonic() - start_time)
                return self._in_memory_state.copy()

            state = {
                "failures": int(state_data.get("failures", 0)),
                "last_failure_time": datetime.fromisoformat(
                    state_data.get("last_failure_time", datetime.min.isoformat())
                ).replace(tzinfo=timezone.utc),
                "next_try_after": datetime.fromisoformat(
                    state_data.get("next_try_after", datetime.min.isoformat())
                ).replace(tzinfo=timezone.utc),
            }

            # State validation
            if not isinstance(state["failures"], int) or state["failures"] < 0:
                _log_validation_error(
                    f"Invalid failures value for {_sanitize_provider(self.provider)}: {state['failures']}. Resetting to 0.",
                    "invalid_state",
                )
                state["failures"] = 0
            for key in ("last_failure_time", "next_try_after"):
                if (
                    not isinstance(state[key], datetime)
                    or state[key].tzinfo != timezone.utc
                ):
                    _log_validation_error(
                        f"Invalid {key} for {_sanitize_provider(self.provider)}: {state[key]}. Resetting to min value.",
                        "invalid_state",
                    )
                    state[key] = datetime.min.replace(tzinfo=timezone.utc)

            REDIS_OPERATION_LATENCY.labels(
                provider=self.provider, operation="get_state"
            ).observe(time.monotonic() - start_time)
            return state

        except ValueError as e:
            REDIS_OPERATION_LATENCY.labels(
                provider=self.provider, operation="get_state"
            ).observe(time.monotonic() - start_time)
            _log_validation_error(
                f"Invalid datetime format in Redis state for {_sanitize_provider(self.provider)}: {e}",
                "invalid_datetime_format",
            )
            return self._in_memory_state.copy()
        except Exception as e:
            REDIS_OPERATION_LATENCY.labels(
                provider=self.provider, operation="get_state"
            ).observe(time.monotonic() - start_time)
            _log_validation_error(
                f"Error fetching state from Redis for {_sanitize_provider(self.provider)}: {e}",
                "redis_get_failed",
            )
            return self._in_memory_state.copy()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(redis.RedisError),
        before_sleep=lambda retry_state: logger.debug(
            f"Retrying Redis set operation for provider: attempt {retry_state.attempt_number}"
        ),
    )
    async def set_state(self, state: Dict[str, Any]) -> None:
        """
        Saves the current circuit breaker state to Redis or in-memory, with validation.
        Uses a Redis pipeline to batch HSET and EXPIRE operations for efficiency.
        """
        start_time = time.monotonic()
        await self._rate_limit()
        # Validate state before setting
        if (
            not isinstance(state["failures"], int)
            or state["failures"] < 0
            or state["failures"] > 1000
        ):
            _log_validation_error(
                f"Invalid failures value for {_sanitize_provider(self.provider)}: {state['failures']}. Clamping to [0, 1000].",
                "invalid_state",
            )
            state["failures"] = min(max(state["failures"], 0), 1000)
        for key in ("last_failure_time", "next_try_after"):
            if (
                not isinstance(state[key], datetime)
                or state[key].tzinfo != timezone.utc
            ):
                _log_validation_error(
                    f"Invalid {key} for {_sanitize_provider(self.provider)}: {state[key]}. Resetting to min value.",
                    "invalid_state",
                )
                state[key] = datetime.min.replace(tzinfo=timezone.utc)

        if not await self._check_redis_health():
            self._in_memory_state.update(state)
            REDIS_OPERATION_LATENCY.labels(
                provider=self.provider, operation="set_state"
            ).observe(time.monotonic() - start_time)
            return

        try:
            # Use pipeline to batch Redis operations for efficiency
            async with self.redis_client.pipeline() as pipe:
                await pipe.hset(
                    f"circuit_breaker:{self.provider}",
                    mapping={
                        "failures": str(state["failures"]),
                        "last_failure_time": state["last_failure_time"].isoformat(),
                        "next_try_after": state["next_try_after"].isoformat(),
                    },
                )
                # Use environment variable as fallback
                expiry_seconds_str = os.getenv(
                    "CIRCUIT_BREAKER_STATE_TTL_SECONDS",
                    str(
                        getattr(self.config, "CIRCUIT_BREAKER_STATE_TTL_SECONDS", 86400)
                    ),
                )
                try:
                    expiry_seconds = int(expiry_seconds_str)
                except ValueError:
                    _log_validation_error(
                        f"Invalid CIRCUIT_BREAKER_STATE_TTL_SECONDS: {expiry_seconds_str}. Using default: 86400",
                        "invalid_config",
                    )
                    expiry_seconds = 86400
                await pipe.expire(f"circuit_breaker:{self.provider}", expiry_seconds)
                await pipe.execute()
            REDIS_OPERATION_LATENCY.labels(
                provider=self.provider, operation="set_state"
            ).observe(time.monotonic() - start_time)
        except Exception as e:
            REDIS_OPERATION_LATENCY.labels(
                provider=self.provider, operation="set_state"
            ).observe(time.monotonic() - start_time)
            _log_validation_error(
                f"Error setting state in Redis for {_sanitize_provider(self.provider)}: {e}",
                "redis_set_failed",
            )
            self._in_memory_state.update(state)
            raise redis.RedisError("Failed to set state in Redis after retries.")


# State for the circuit breaker, protected by a lock
BreakerStateManager = Union[CircuitBreakerState, InMemoryBreakerStateManager]
_breaker_states: Dict[str, BreakerStateManager] = {}
_breaker_states_lock = threading.Lock()
_cleanup_task: Optional[asyncio.Task] = None
_cleanup_task_lock = threading.Lock()
_config_refresh_task: Optional[asyncio.Task] = None
_config_refresh_lock = threading.Lock()
_MAX_PROVIDERS = int(os.getenv("CIRCUIT_BREAKER_MAX_PROVIDERS", 1000))
_pause_tasks = False
_last_pause_state: bool = False


def validate_config(config: "ArbiterConfig") -> None:
    """Validates the configuration object."""
    required_fields = [
        ("LLM_API_FAILURE_THRESHOLD", int, 3),
        ("LLM_API_BACKOFF_MAX_SECONDS", float, 60.0),
        ("CIRCUIT_BREAKER_STATE_TTL_SECONDS", int, 86400),
        ("CIRCUIT_BREAKER_CLEANUP_INTERVAL_SECONDS", int, 3600),
        ("REDIS_MAX_CONNECTIONS", int, 100),
        ("REDIS_SOCKET_TIMEOUT", float, 5.0),
        ("REDIS_SOCKET_CONNECT_TIMEOUT", float, 5.0),
        ("CONFIG_REFRESH_INTERVAL_SECONDS", int, 300),
        ("CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL", float, 0.1),
        ("CIRCUIT_BREAKER_VALIDATION_ERROR_INTERVAL", float, 300.0),
        ("CIRCUIT_BREAKER_MAX_PROVIDERS", int, 1000),
        ("PAUSE_CIRCUIT_BREAKER_TASKS", str, "false"),
        ("CIRCUIT_BREAKER_CRITICAL_PROVIDERS", str, ""),
    ]
    for field, type_, default in required_fields:
        value = getattr(config, field, default)
        if not isinstance(value, type_):
            logger.warning(
                f"Invalid {field}: expected {type_.__name__}, got {type(value).__name__}. Using default: {default}"
            )
            setattr(config, field, default)


async def get_breaker_state(
    provider: str, config: "ArbiterConfig"
) -> BreakerStateManager:
    """
    Retrieves or creates a CircuitBreakerState instance for a given provider, with provider validation.

    Args:
        provider: The unique name of the LLM provider.
        config: The ArbiterConfig instance.

    Returns:
        The CircuitBreakerState instance.

    Raises:
        ValueError: If the provider name is invalid.
        RuntimeError: If the maximum number of providers is reached.
    """
    with tracer.start_as_current_span(
        "get_breaker_state", attributes={"provider": provider}
    ):
        # Double-checked locking pattern to avoid holding the lock during async operations
        with _breaker_states_lock:
            if provider in _breaker_states:
                return _breaker_states[provider]

        # --- Lock Released ---

        if not re.match(r"^[a-zA-Z0-9_-]+$", provider) or len(provider) > 50:
            _log_validation_error(
                f"Invalid provider name: '{_sanitize_provider(provider)}'",
                "invalid_provider_name",
            )
            raise ValueError(
                "Provider name must be alphanumeric with underscores or hyphens and <= 50 characters"
            )

        redis_url = getattr(config, "REDIS_URL", None)
        if redis_url:
            new_state_manager = CircuitBreakerState(provider, config)
            try:
                await new_state_manager.initialize()
            except redis.RedisError:
                logger.warning(
                    "Falling back to in-memory behavior for %s due to Redis connection failure.",
                    _sanitize_provider(provider),
                )
        else:
            logger.debug(
                f"No REDIS_URL set for provider {provider}. Using in-memory state."
            )
            new_state_manager = InMemoryBreakerStateManager(provider)

        # Re-acquire lock to safely update the shared dictionary
        with _breaker_states_lock:
            if (
                len(_breaker_states) >= _MAX_PROVIDERS
                and provider not in _breaker_states
            ):
                _log_validation_error(
                    f"Maximum provider limit ({_MAX_PROVIDERS}) reached. Cannot create state for {_sanitize_provider(provider)}.",
                    "max_providers_exceeded",
                )
                raise RuntimeError(f"Maximum provider limit ({_MAX_PROVIDERS}) reached")

            # Another coroutine might have created the state while we were unlocked
            if provider in _breaker_states:
                return _breaker_states[provider]

            _breaker_states[provider] = new_state_manager
            return new_state_manager


async def close_all_breaker_states() -> None:
    """
    Closes all active Redis connections and the shared connection pool.
    """
    global _global_connection_pool
    with tracer.start_as_current_span("close_all_breaker_states"):
        with _breaker_states_lock:
            for state in _breaker_states.values():
                if hasattr(state, "close"):
                    await state.close()
            if _global_connection_pool:
                await _global_connection_pool.disconnect()
                _global_connection_pool = None
            _breaker_states.clear()
            logger.info("Closed all circuit breaker states and Redis connection pool.")


def register_shutdown_handler() -> None:
    """Registers a shutdown handler to close all breaker states."""
    # Don't register during tests
    if os.getenv("PYTEST_CURRENT_TEST"):
        return

    def shutdown():
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_closed():
                loop.run_until_complete(close_all_breaker_states())
        except RuntimeError:
            # If no loop exists, create one
            try:
                asyncio.run(close_all_breaker_states())
            except Exception as e:
                logger.error(f"Error during shutdown: {e}")

    atexit.register(shutdown)


async def cleanup_breaker_states() -> None:
    """
    Periodically cleans up unused circuit breaker states to prevent memory leaks.
    This task handles states that are no longer being used and haven't expired.
    """
    global _last_pause_state

    # Exit immediately if in test environment
    if os.getenv("PYTEST_CURRENT_TEST"):
        logger.debug("Skipping cleanup_breaker_states in test environment")
        return

    while True:
        with tracer.start_as_current_span("cleanup_breaker_states") as span:
            pause_value = os.getenv("PAUSE_CIRCUIT_BREAKER_TASKS", "false").lower()
            if pause_value not in ("true", "false"):
                _log_validation_error(
                    f"Invalid PAUSE_CIRCUIT_BREAKER_TASKS: {pause_value}. Using default: false",
                    "invalid_config",
                )
                pause_value = "false"
            is_paused = _pause_tasks or pause_value == "true"
            if is_paused != _last_pause_state:
                logger.info(
                    f"Circuit breaker cleanup task {'paused' if is_paused else 'resumed'}"
                )
                TASK_STATE_TRANSITIONS.labels(
                    task="cleanup", state="paused" if is_paused else "resumed"
                ).inc()
                _last_pause_state = is_paused
                span.set_attribute("task_state", "paused" if is_paused else "resumed")
            if is_paused:
                logger.debug("Circuit breaker cleanup task paused.")
                await asyncio.sleep(60)
                continue
            try:
                critical_providers = os.getenv(
                    "CIRCUIT_BREAKER_CRITICAL_PROVIDERS", ""
                ).split(",")
                with _breaker_states_lock:
                    expired = []
                    # Iterate over a list to avoid runtime errors from dictionary changes
                    for provider, state in list(_breaker_states.items()):
                        # Check if provider is critical and skip cleanup
                        if provider in critical_providers:
                            logger.debug(
                                f"Skipping cleanup for critical provider: {_sanitize_provider(provider)}"
                            )
                            continue
                        async with state.state_lock():
                            current_state = await state.get_state()
                        # Add provider-specific tracing attributes
                        span.set_attribute(
                            f"provider.{provider}.failures",
                            current_state.get("failures", 0),
                        )
                        span.set_attribute(
                            f"provider.{provider}.next_try_after",
                            current_state["next_try_after"].isoformat(),
                        )

                        # Validate state to ensure data integrity
                        if (
                            not isinstance(current_state.get("failures"), int)
                            or current_state.get("failures", -1) < 0
                        ):
                            _log_validation_error(
                                f"Invalid state for {_sanitize_provider(provider)} during cleanup: {current_state}",
                                "invalid_state",
                            )
                            CIRCUIT_BREAKER_CLEANUP_OPERATIONS.labels(
                                provider=provider, result="invalid_state"
                            ).inc()
                            expired.append(provider)
                            continue

                        # Check if state is stale (no activity for 1 day)
                        if current_state["next_try_after"] < datetime.now(
                            timezone.utc
                        ) - timedelta(days=1):
                            expired.append(provider)
                            CIRCUIT_BREAKER_CLEANUP_OPERATIONS.labels(
                                provider=provider, result="expired"
                            ).inc()

                    for provider in expired:
                        state_to_close = _breaker_states[provider]
                        if hasattr(state_to_close, "close"):
                            await state_to_close.close()
                        del _breaker_states[provider]
                        logger.debug(
                            f"Cleaned up expired circuit breaker state for {_sanitize_provider(provider)}"
                        )

                # Use environment variable as fallback for cleanup interval
                try:
                    config = get_config()
                    cleanup_interval_str = os.getenv(
                        "CIRCUIT_BREAKER_CLEANUP_INTERVAL_SECONDS",
                        str(
                            getattr(
                                config, "CIRCUIT_BREAKER_CLEANUP_INTERVAL_SECONDS", 3600
                            )
                        ),
                    )
                except Exception:
                    cleanup_interval_str = os.getenv(
                        "CIRCUIT_BREAKER_CLEANUP_INTERVAL_SECONDS", "3600"
                    )
                try:
                    cleanup_interval = int(cleanup_interval_str)
                    if cleanup_interval < 60:
                        raise ValueError(
                            "CIRCUIT_BREAKER_CLEANUP_INTERVAL_SECONDS must be at least 60"
                        )
                except ValueError:
                    _log_validation_error(
                        f"Invalid CIRCUIT_BREAKER_CLEANUP_INTERVAL_SECONDS: {cleanup_interval_str}. Using default: 3600",
                        "invalid_config",
                    )
                    cleanup_interval = 3600
                await asyncio.sleep(cleanup_interval)
            except Exception as e:
                logger.error(f"Error in cleanup_breaker_states: {e}")
                LLM_CIRCUIT_BREAKER_ERRORS.labels(error_type="cleanup_failed").inc()
                CIRCUIT_BREAKER_CLEANUP_OPERATIONS.labels(
                    provider="none", result="error"
                ).inc()
                span.record_exception(e)
                await asyncio.sleep(60)


def start_cleanup_task() -> None:
    """Starts the cleanup task in the background if not already running."""
    global _cleanup_task
    with _cleanup_task_lock:
        if _cleanup_task is None or _cleanup_task.done():
            try:
                _cleanup_task = asyncio.create_task(cleanup_breaker_states())
                logger.info("Started circuit breaker cleanup task.")
            except RuntimeError:
                logger.error("Cannot start cleanup task: no running event loop")
        else:
            logger.debug("Circuit breaker cleanup task already running.")


async def periodic_config_refresh() -> None:
    """Periodically refreshes circuit breaker configuration."""
    global _last_pause_state

    # Exit immediately if in test environment
    if os.getenv("PYTEST_CURRENT_TEST"):
        logger.debug("Skipping periodic_config_refresh in test environment")
        return

    while True:
        with tracer.start_as_current_span("periodic_config_refresh") as span:
            pause_value = os.getenv("PAUSE_CIRCUIT_BREAKER_TASKS", "false").lower()
            if pause_value not in ("true", "false"):
                _log_validation_error(
                    f"Invalid PAUSE_CIRCUIT_BREAKER_TASKS: {pause_value}. Using default: false",
                    "invalid_config",
                )
                pause_value = "false"
            is_paused = _pause_tasks or pause_value == "true"
            if is_paused != _last_pause_state:
                logger.info(
                    f"Circuit breaker config refresh task {'paused' if is_paused else 'resumed'}"
                )
                TASK_STATE_TRANSITIONS.labels(
                    task="config_refresh", state="paused" if is_paused else "resumed"
                ).inc()
                _last_pause_state = is_paused
                span.set_attribute("task_state", "paused" if is_paused else "resumed")
            if is_paused:
                logger.debug("Circuit breaker config refresh task paused.")
                await asyncio.sleep(60)
                continue
            try:
                await refresh_breaker_states()
                refresh_interval_str = os.getenv(
                    "CONFIG_REFRESH_INTERVAL_SECONDS", "300"
                )
                try:
                    refresh_interval = int(refresh_interval_str)
                    if refresh_interval < 60:
                        raise ValueError(
                            "CONFIG_REFRESH_INTERVAL_SECONDS must be at least 60"
                        )
                except ValueError:
                    _log_validation_error(
                        f"Invalid CONFIG_REFRESH_INTERVAL_SECONDS: {refresh_interval_str}. Using default: 300",
                        "invalid_config",
                    )
                    refresh_interval = 300
                CONFIG_REFRESH_OPERATIONS.labels(result="success").inc()
                await asyncio.sleep(refresh_interval)
            except Exception as e:
                logger.error(f"Error in periodic_config_refresh: {e}")
                LLM_CIRCUIT_BREAKER_ERRORS.labels(
                    error_type="config_refresh_failed"
                ).inc()
                CONFIG_REFRESH_OPERATIONS.labels(result="error").inc()
                span.record_exception(e)
                await asyncio.sleep(60)


def start_config_refresh_task() -> None:
    """Starts the config refresh task in the background if not already running."""
    global _config_refresh_task
    with _config_refresh_lock:
        if _config_refresh_task is None or _config_refresh_task.done():
            _config_refresh_task = asyncio.create_task(periodic_config_refresh())
            logger.info("Started circuit breaker config refresh task.")
        else:
            logger.debug("Circuit breaker config refresh task already running.")


async def refresh_breaker_states() -> None:
    """Refreshes configuration for all circuit breaker states."""
    with tracer.start_as_current_span("refresh_breaker_states") as span:
        config = get_config()
        try:
            # Fallback for ArbiterConfig if reload_config isn't present
            if hasattr(config, "reload_config"):
                await config.reload_config()
                span.set_attribute("reload_config", "success")
            else:
                logger.debug(
                    "ArbiterConfig.reload_config not available. Using existing config."
                )
                span.set_attribute("reload_config", "skipped")
        except AttributeError:
            logger.debug(
                "ArbiterConfig.reload_config not available. Using existing config."
            )
            span.set_attribute("reload_config", "skipped")
        except Exception as e:
            _log_validation_error(
                f"Error reloading config: {e}", "config_reload_failed"
            )
            span.record_exception(e)
            span.set_attribute("reload_config", "failed")
        with _breaker_states_lock:
            for state in _breaker_states.values():
                if hasattr(state, "config"):
                    state.config = config
                    validate_config(config)
                    logger.debug(
                        f"Refreshed configuration for circuit breaker: {_sanitize_provider(state.provider)}"
                    )
                    span.set_attribute(f"provider.{state.provider}.refreshed", True)


async def is_llm_policy_circuit_breaker_open(
    provider: str = "default", config: Optional["ArbiterConfig"] = None
) -> bool:
    """
    Checks if the LLM policy API circuit breaker is open for the specified provider.

    Args:
        provider: The LLM provider (e.g., 'openai', 'anthropic'). Defaults to 'default'.
        config: An optional ArbiterConfig object.

    Returns:
        bool: True if the breaker is open, blocking requests; False otherwise.
    """
    with tracer.start_as_current_span(
        "is_llm_policy_circuit_breaker_open", attributes={"provider": provider}
    ) as span:
        config = config or get_config()
        validate_config(config)
        breaker_state_manager = await get_breaker_state(provider, config)

        async with breaker_state_manager.state_lock():
            state = await breaker_state_manager.get_state()
            failure_threshold = getattr(config, "LLM_API_FAILURE_THRESHOLD", 3)
            current_state_str = (
                "closed" if state["failures"] < failure_threshold else "open"
            )

            # Add tracing attributes for debugging
            span.set_attribute("failure_count", state["failures"])
            span.set_attribute("failure_threshold", failure_threshold)
            span.set_attribute("next_try_after", state["next_try_after"].isoformat())

            # Check if breaker is open (failures >= threshold and backoff period active)
            if state["failures"] >= failure_threshold:
                if datetime.now(timezone.utc) < state["next_try_after"]:
                    LLM_CIRCUIT_BREAKER_STATE.labels(provider=provider).set(
                        1
                    )  # Reflects open state
                    LLM_CIRCUIT_BREAKER_TRANSITIONS.labels(
                        provider=provider, from_state=current_state_str, to_state="open"
                    ).inc()
                    span.set_attribute("breaker_state", "open")
                    return True
                else:
                    # Half-open state: allow one test request to check recovery
                    logger.debug(
                        "LLM policy API circuit breaker for '%s' is in half-open state. Allowing one request to check for recovery.",
                        _sanitize_provider(provider),
                    )
                    state["circuit_state"] = "half-open"  # explicit state tracking
                    await breaker_state_manager.set_state(state)
                    LLM_CIRCUIT_BREAKER_TRANSITIONS.labels(
                        provider=provider,
                        from_state=current_state_str,
                        to_state="half-open",
                    ).inc()
                    span.set_attribute("breaker_state", "half-open")
                    return False

            # Closed state: allow requests
            LLM_CIRCUIT_BREAKER_STATE.labels(provider=provider).set(
                0
            )  # Reflects closed state
            LLM_CIRCUIT_BREAKER_TRANSITIONS.labels(
                provider=provider, from_state=current_state_str, to_state="closed"
            ).inc()
            span.set_attribute("breaker_state", "closed")
            return False


async def record_llm_policy_api_success(
    provider: str = "default", config: Optional["ArbiterConfig"] = None
) -> None:
    """
    Resets the LLM policy API circuit breaker on success.

    Args:
        provider: The LLM provider. Defaults to 'default'.
        config: An optional ArbiterConfig object.
    """
    with tracer.start_as_current_span(
        "record_llm_policy_api_success", attributes={"provider": provider}
    ) as span:
        config = config or get_config()
        validate_config(config)
        breaker_state_manager = await get_breaker_state(provider, config)

        async with breaker_state_manager.state_lock():
            state = await breaker_state_manager.get_state()
            if state["failures"] > 0:
                logger.debug(
                    "LLM policy API call successful for '%s'. Resetting circuit breaker.",
                    _sanitize_provider(provider),
                )
                span.set_attribute("previous_failures", state["failures"])
                state["failures"] = 0
                state["last_failure_time"] = datetime.min.replace(tzinfo=timezone.utc)
                state["next_try_after"] = datetime.min.replace(tzinfo=timezone.utc)
                await breaker_state_manager.set_state(state)
                LLM_CIRCUIT_BREAKER_STATE.labels(provider=provider).set(0)
                span.set_attribute("breaker_state", "closed")


async def record_llm_policy_api_failure(
    provider: str = "default",
    error_message: Optional[str] = None,
    config: Optional["ArbiterConfig"] = None,
) -> None:
    """
    Records a failure and updates the LLM circuit breaker with exponential backoff.

    Args:
        provider: The LLM provider. Defaults to 'default'.
        error_message: The error message associated with the failure.
        config: An optional ArbiterConfig object.
    """
    with tracer.start_as_current_span(
        "record_llm_policy_api_failure", attributes={"provider": provider}
    ) as span:
        config = config or get_config()
        validate_config(config)
        breaker_state_manager = await get_breaker_state(provider, config)

        async with breaker_state_manager.state_lock():
            state = await breaker_state_manager.get_state()

            span.set_attribute("failure_count", state["failures"] + 1)
            failure_threshold = getattr(config, "LLM_API_FAILURE_THRESHOLD", 3)
            span.set_attribute("failure_threshold", failure_threshold)
            if error_message:
                sanitized_message = sanitize_log_message(error_message)
                span.set_attribute("error_message", sanitized_message)

            state["failures"] = min(state["failures"] + 1, 1000)  # Cap at 1000
            state["last_failure_time"] = datetime.now(timezone.utc)

            backoff_max_seconds = getattr(config, "LLM_API_BACKOFF_MAX_SECONDS", 60.0)

            # Exponential backoff calculation
            delay = min(backoff_max_seconds, 2 ** state["failures"])
            state["next_try_after"] = state["last_failure_time"] + timedelta(
                seconds=delay
            )

            log_message = f"LLM policy API failure for '{_sanitize_provider(provider)}'. Failures: {state['failures']}/{failure_threshold}. "
            if error_message:
                log_message += f"Error: {sanitize_log_message(error_message)}"

            if state["failures"] >= failure_threshold:
                logger.warning(log_message + f" Opening circuit for {delay} seconds.")
                LLM_CIRCUIT_BREAKER_TRIPS.labels(provider=provider).inc()
                LLM_CIRCUIT_BREAKER_STATE.labels(provider=provider).set(1)
            else:
                logger.warning(log_message)

            LLM_API_FAILURE_COUNT.labels(provider=provider).inc()
            await breaker_state_manager.set_state(state)
