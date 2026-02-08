# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio

# Other imports
import hashlib
import json
import logging
import os
import time
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Type, Union

import redis.asyncio as aioredis

# Import centralized OpenTelemetry configuration
from self_fixing_engineer.arbiter.otel_config import get_tracer
from opentelemetry import trace

# Prometheus Metrics
from prometheus_client import REGISTRY, Counter, Gauge, Histogram, start_http_server
from redis.asyncio import Redis

# Defensive import for redis Lock class
# In redis-py 5.x, Lock is located in redis.asyncio.client module
try:
    from redis.asyncio.client import Lock as RedisLock
except ImportError as e:
    # Provide a clear error message if redis version is incompatible
    import warnings
    warnings.warn(
        f"Could not import redis.asyncio.client.Lock: {e}. "
        f"This typically indicates an incompatible redis version. "
        f"Please ensure redis>=5.0.0 is installed. "
        f"Redis locking features will be disabled.",
        ImportWarning,
        stacklevel=2
    )
    # Create a type-annotated placeholder to prevent NameError
    RedisLock: Optional[type] = None

from redis.exceptions import (
    ConnectionError,
    DataError,
    LockError,
    RedisError,
    TimeoutError,
)

# Import tenacity for retries with exponential backoff
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Logger initialization
logger = logging.getLogger(__name__)
logger.setLevel(
    os.getenv("LOG_LEVEL", "INFO").upper()
)  # Allow log level to be configured via env var

# Get tracer using centralized configuration
tracer = get_tracer(__name__)


# Ensure metrics are registered only once
def _get_or_create_metric(
    metric_class: Union[Type[Counter], Type[Gauge], Type[Histogram]],
    name: str,
    documentation: str,
    labelnames: Tuple[str, ...] = (),
    buckets: Optional[Tuple[float, ...]] = None,
):
    """
    Idempotently get or create a Prometheus metric.
    If the metric already exists in the registry, it returns the existing one.
    Otherwise, it creates a new metric of the specified class.
    """
    try:
        existing_metric = REGISTRY._names_to_collectors.get(name)
        if existing_metric and isinstance(existing_metric, metric_class):
            return existing_metric
        if existing_metric:  # Unregister if type mismatch
            REGISTRY.unregister(existing_metric)
            logger.warning(
                f"Unregistered existing metric '{name}' due to type mismatch or re-creation attempt."
            )
    except KeyError:
        pass  # Metric does not exist, proceed to create
    except Exception as e:
        logging.error(f"Error checking/unregistering metric {name}: {e}")

    if buckets:
        return metric_class(name, documentation, labelnames=labelnames, buckets=buckets)
    return metric_class(name, documentation, labelnames=labelnames)


# Metrics for RedisClient Operations
REDIS_CALLS_TOTAL = _get_or_create_metric(
    Counter, "redis_calls_total", "Total Redis calls", ["operation", "status"]
)
REDIS_CALLS_ERRORS = _get_or_create_metric(
    Counter, "redis_calls_errors", "Redis call errors", ["operation", "error_type"]
)
REDIS_CALL_LATENCY_SECONDS = _get_or_create_metric(
    Histogram,
    "redis_call_latency_seconds",
    "Redis call latency in seconds",
    ["operation"],
)
REDIS_CONNECTIONS_CURRENT = _get_or_create_metric(
    Gauge, "redis_connections_current", "Current number of active Redis connections"
)
REDIS_LOCK_ACQUIRED_TOTAL = _get_or_create_metric(
    Counter, "redis_lock_acquired_total", "Total Redis locks acquired"
)
REDIS_LOCK_RELEASED_TOTAL = _get_or_create_metric(
    Counter, "redis_lock_released_total", "Total Redis locks released"
)
REDIS_LOCK_FAILED_TOTAL = _get_or_create_metric(
    Counter, "redis_lock_failed_total", "Total Redis lock acquisition failures"
)
REDIS_MEMORY_USAGE = _get_or_create_metric(
    Gauge, "redis_memory_usage_mb", "Redis memory usage in megabytes", ["instance"]
)
REDIS_KEYSPACE_SIZE = _get_or_create_metric(
    Gauge, "redis_keyspace_size", "Number of keys in Redis database", ["instance"]
)


def _redact_key(key: str) -> str:
    """Fully anonymizes a key for logging."""
    if not key:
        return "<empty>"
    return f"hash:{hashlib.sha256(key.encode()).hexdigest()[:8]}"


class RedisClient:
    """
    An asynchronous Redis client with connection management, CRUD operations,
    and integrated observability (Prometheus metrics and OpenTelemetry tracing).

    Supports basic key-value operations and distributed locking.
    """

    def __init__(self, redis_url: Optional[str] = None):
        """
        Initializes the RedisClient.
        Args:
            redis_url (Optional[str]): The URL for the Redis server (e.g., "redis://localhost:6379/0").
                                       Defaults to REDIS_URL environment variable.
        """
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        try:
            parsed = urllib.parse.urlparse(self.redis_url)
            if parsed.scheme not in ("redis", "rediss") or not parsed.hostname:
                raise ValueError(f"Invalid REDIS_URL: {self.redis_url}")
        except ValueError as e:
            logger.error(f"Invalid Redis URL: {e}")
            raise ValueError(f"Invalid Redis URL: {e}") from e
        self.client: Optional[Redis] = None
        # Determine if SSL should be used based on URL scheme or environment variable
        self.use_ssl = (
            self.redis_url.startswith("rediss://")
            or os.getenv("REDIS_USE_SSL", "false").lower() == "true"
        )

        env = os.getenv("ENV", "dev").lower()
        if env == "prod" and not self.use_ssl:
            logger.error(
                "SSL is required in production (ENV=prod). Use rediss:// or set REDIS_USE_SSL=true."
            )
            raise ValueError("SSL is required in production.")

        self._health_check_task: Optional[asyncio.Task] = None
        metrics_port = int(os.getenv("METRICS_PORT", "0"))
        if metrics_port > 0:
            logger.info(f"Starting Prometheus metrics server on port {metrics_port}.")
            try:
                start_http_server(metrics_port)
            except Exception as e:
                logger.error(
                    f"Failed to start Prometheus metrics server on port {metrics_port}: {e}"
                )
        logger.info(
            f"RedisClient initialized for URL: {_redact_key(self.redis_url)}, SSL: {self.use_ssl}"
        )

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, RedisError)),
        reraise=True,
    )
    async def connect(self) -> None:
        """
        Establishes a connection to the Redis server with retries and health checks.
        """
        if self.client is not None:
            logger.info("Redis client already connected.")
            return

        with tracer.start_as_current_span("redis_connect") as span:
            start_time = time.monotonic()
            REDIS_CALLS_TOTAL.labels(operation="connect", status="attempt").inc()
            try:
                # aioredis.from_url handles connection pooling automatically
                max_connections = int(os.getenv("REDIS_MAX_CONNECTIONS", "50"))
                self.client = aioredis.from_url(
                    self.redis_url,
                    decode_responses=True,
                    ssl=self.use_ssl,
                    max_connections=max_connections,
                )
                await self.client.ping()
                REDIS_CONNECTIONS_CURRENT.inc()  # Increment gauge on successful connection
                self._health_check_task = asyncio.create_task(
                    self._start_health_check()
                )
                REDIS_CALLS_TOTAL.labels(operation="connect", status="success").inc()
                span.set_status(trace.Status(trace.StatusCode.OK))
                logger.info(
                    f"Successfully connected to Redis at {_redact_key(self.redis_url)}"
                )
            except Exception as e:
                REDIS_CALLS_TOTAL.labels(operation="connect", status="failure").inc()
                REDIS_CALLS_ERRORS.labels(
                    operation="connect", error_type=type(e).__name__
                ).inc()
                span.record_exception(e)
                span.set_status(
                    trace.Status(trace.StatusCode.ERROR, f"Failed to connect: {e}")
                )
                logger.error(
                    f"Failed to connect to Redis at {_redact_key(self.redis_url)}: {e}",
                    exc_info=True,
                )
                raise ConnectionError(f"Failed to connect to Redis: {e}") from e
            finally:
                REDIS_CALL_LATENCY_SECONDS.labels(operation="connect").observe(
                    time.monotonic() - start_time
                )

    async def reconnect(self) -> None:
        """Attempts to reconnect to Redis if the connection is unhealthy."""
        if await self.ping():
            logger.debug("Redis connection is healthy, no reconnect needed.")
            return
        logger.warning("Redis connection unhealthy, attempting reconnect.")
        await self.disconnect()
        await self.connect()

    async def ping(self) -> bool:
        """Pings the Redis server to check connection health."""
        if not self.client:
            return False
        try:
            return await self.client.ping()
        except Exception as e:
            logger.debug(f"Ping failed: {e}")
            return False

    async def _start_health_check(
        self, interval: float = float(os.getenv("REDIS_HEALTH_CHECK_INTERVAL", "60.0"))
    ) -> None:
        """Runs a background task to periodically check connection health."""
        while self.client is not None:
            try:
                if not await self.ping():
                    await self.reconnect()
                await self.update_redis_stats()
            except asyncio.CancelledError:
                logger.info("Health check task cancelled.")
                break
            except Exception as e:
                logger.error(f"Health check failed: {e}", exc_info=True)
            await asyncio.sleep(interval)

    async def update_redis_stats(self) -> None:
        """Updates Redis metrics like keyspace size and memory usage."""
        if not self.client:
            return
        try:
            # Use the existing _execute_operation for consistency and retries
            info = await self._execute_operation(
                "info", "server", self.client.info, "memory"
            )
            used_memory = info.get("used_memory", 0) / 1024 / 1024  # MB
            REDIS_MEMORY_USAGE.labels(instance=self.redis_url).set(used_memory)

            key_count = await self._execute_operation(
                "dbsize", "server", self.client.dbsize
            )
            REDIS_KEYSPACE_SIZE.labels(instance=self.redis_url).set(key_count)

        except Exception as e:
            logger.error(f"Failed to update Redis stats: {e}", exc_info=True)

    async def disconnect(self) -> None:
        """
        Closes the Redis client connection and cancels health checks.
        """
        if self.client is None:
            logger.info("Redis client already disconnected.")
            return
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            self._health_check_task = None
        with tracer.start_as_current_span("redis_disconnect") as span:
            start_time = time.monotonic()
            REDIS_CALLS_TOTAL.labels(operation="disconnect", status="attempt").inc()
            try:
                await self.client.close()
                self.client = None
                REDIS_CONNECTIONS_CURRENT.dec()
                REDIS_CALLS_TOTAL.labels(operation="disconnect", status="success").inc()
                span.set_status(trace.Status(trace.StatusCode.OK))
                logger.info("Redis client connection closed.")
            except Exception as e:
                REDIS_CALLS_TOTAL.labels(operation="disconnect", status="failure").inc()
                REDIS_CALLS_ERRORS.labels(
                    operation="disconnect", error_type=type(e).__name__
                ).inc()
                span.record_exception(e)
                span.set_status(
                    trace.Status(trace.StatusCode.ERROR, f"Failed to disconnect: {e}")
                )
                logger.error(f"Failed to close Redis connection: {e}", exc_info=True)
                raise ConnectionError(f"Failed to disconnect from Redis: {e}") from e
            finally:
                REDIS_CALL_LATENCY_SECONDS.labels(operation="disconnect").observe(
                    time.monotonic() - start_time
                )

    async def _execute_operation(
        self, operation: str, key: str, func: callable, *args, **kwargs
    ) -> Any:
        """
        Executes a Redis operation with retries and observability.
        Args:
            operation (str): The operation name (e.g., 'set', 'get').
            key (str): The Redis key (for tracing).
            func (callable): The Redis client method to execute.
            *args, **kwargs: Arguments for the Redis method.
        Returns:
            Any: The result of the Redis operation.
        """
        if not self.client:
            raise RuntimeError("Redis client not connected. Call connect() first.")
        start_time = time.monotonic()
        span_name = f"redis_{operation}"

        with tracer.start_as_current_span(span_name) as span:
            span.set_attribute("redis.key", _redact_key(key))
            span.set_attribute("redis.operation", operation)
            REDIS_CALLS_TOTAL.labels(operation=operation, status="attempt").inc()
            for attempt in range(2):
                try:
                    result = await func(*args, **kwargs)
                    REDIS_CALLS_TOTAL.labels(
                        operation=operation, status="success"
                    ).inc()
                    span.set_status(trace.Status(trace.StatusCode.OK))
                    return result
                except (ConnectionError, TimeoutError, RedisError) as e:
                    if attempt == 0:
                        logger.warning(
                            f"Transient error during {operation} on key '{_redact_key(key)}': {e}. Attempting reconnect."
                        )
                        await self.reconnect()
                        continue
                    REDIS_CALLS_TOTAL.labels(
                        operation=operation, status="failure"
                    ).inc()
                    REDIS_CALLS_ERRORS.labels(
                        operation=operation, error_type=type(e).__name__
                    ).inc()
                    span.record_exception(e)
                    span.set_status(
                        trace.Status(
                            trace.StatusCode.ERROR,
                            f"Redis {operation.upper()} error: {e}",
                        )
                    )
                    logger.error(
                        f"Redis {operation.upper()} operation failed for key '{_redact_key(key)}': {e}",
                        exc_info=True,
                    )
                    raise
                finally:
                    REDIS_CALL_LATENCY_SECONDS.labels(operation=operation).observe(
                        time.monotonic() - start_time
                    )

    async def set(
        self, key: str, value: Any, ex: Optional[int] = None, px: Optional[int] = None
    ) -> bool:
        """
        Sets the string value of a key.

        Args:
            key (str): The key to set.
            value (Any): The value to store. Will be JSON-serialized if not string/bytes/int/float.
            ex (Optional[int]): Expire time in seconds.
            px (Optional[int]): Expire time in milliseconds.

        Returns:
            bool: True if set successfully, False otherwise.

        Raises:
            ValueError: If key or value is invalid.
            DataError: If value is not serializable.
            RuntimeError: If Redis client is not connected.

        Example:
            ```python
            client = RedisClient("redis://localhost:6379/0")
            await client.connect()
            success = await client.set("my_key", {"data": "test"})
            print(f"Set successful: {success}")
            ```
        """
        if not key or len(key) > 1024:
            raise ValueError("Key must be non-empty and <= 1024 characters.")
        if ex is not None and ex <= 0:
            raise ValueError("Expiration time (ex) must be positive.")
        if px is not None and px <= 0:
            raise ValueError("Expiration time (px) must be positive.")

        # Ensure value is serializable
        if not isinstance(value, (str, bytes, int, float)):
            try:
                value = json.dumps(value, default=str)
            except TypeError as e:
                logger.error(
                    f"Value for key '{_redact_key(key)}' is not JSON serializable: {e}",
                    exc_info=True,
                )
                raise DataError(f"Value not serializable: {e}") from e
        if isinstance(value, (str, bytes)) and len(value) > 1024 * 1024:  # 1MB limit
            raise ValueError("Value size exceeds 1MB limit.")

        return await self._execute_operation(
            "set", key, self.client.set, key, value, ex=ex, px=px
        )

    async def mset(self, mapping: Dict[str, Any]) -> bool:
        """
        Sets multiple key-value pairs in a single operation.

        Args:
            mapping (Dict[str, Any]): Dictionary of keys to values.

        Returns:
            bool: True if set successfully.

        Raises:
            ValueError: If any key is invalid or value is not serializable.
            RuntimeError: If Redis client is not connected.

        Example:
            ```python
            client = RedisClient("redis://localhost:6379/0")
            await client.connect()
            mapping = {"key1": {"data": 1}, "key2": "value2"}
            success = await client.mset(mapping)
            print(f"Batch set successful: {success}")
            ```
        """
        if not mapping:
            return True
        sanitized_mapping = {}
        for key, value in mapping.items():
            if not key or len(key) > 1024:
                raise ValueError(
                    f"Key '{key}' must be non-empty and <= 1024 characters."
                )
            if not isinstance(value, (str, bytes, int, float)):
                try:
                    sanitized_mapping[key] = json.dumps(value, default=str)
                except TypeError as e:
                    logger.error(
                        f"Value for key '{_redact_key(key)}' is not JSON serializable: {e}",
                        exc_info=True,
                    )
                    raise DataError(f"Value not serializable: {e}") from e
            else:
                sanitized_mapping[key] = value
            if isinstance(value, (str, bytes)) and len(value) > 1024 * 1024:
                raise ValueError(f"Value for key '{key}' exceeds 1MB limit.")

        return await self._execute_operation(
            "mset", "multiple", self.client.mset, sanitized_mapping
        )

    async def get(self, key: str) -> Optional[Union[str, Dict]]:
        """
        Gets the value of a key, optionally deserializing JSON.

        Args:
            key (str): The key to retrieve.

        Returns:
            Optional[Union[str, Dict]]: The value, or None if the key does not exist.

        Raises:
            ValueError: If key is invalid.
            RuntimeError: If Redis client is not connected.

        Example:
            ```python
            client = RedisClient("redis://localhost:6379/0")
            await client.connect()
            value = await client.get("my_key")
            print(f"Retrieved value: {value}")
            ```
        """
        if not key or len(key) > 1024:
            raise ValueError("Key must be non-empty and <= 1024 characters.")

        value = await self._execute_operation("get", key, self.client.get, key)
        if value and isinstance(value, str):
            try:
                # Attempt to deserialize JSON, but return raw string if it fails
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    async def mget(self, keys: List[str]) -> List[Optional[Union[str, Dict]]]:
        """
        Gets multiple keys in a single operation.

        Args:
            keys (List[str]): List of keys to retrieve.

        Returns:
            List[Optional[Union[str, Dict]]]: List of values, with JSON deserialization attempted.

        Raises:
            ValueError: If any key is invalid.
            RuntimeError: If Redis client is not connected.

        Example:
            ```python
            client = RedisClient("redis://localhost:6379/0")
            await client.connect()
            values = await client.mget(["key1", "key2"])
            print(f"Retrieved values: {values}")
            ```
        """
        if not keys:
            return []
        for key in keys:
            if not key or len(key) > 1024:
                raise ValueError(
                    f"Key '{key}' must be non-empty and <= 1024 characters."
                )
        values = await self._execute_operation(
            "mget", "multiple", self.client.mget, keys
        )

        def safe_parse(v):
            if isinstance(v, str) and v.startswith(("{", "[")):
                try:
                    return json.loads(v)
                except json.JSONDecodeError:
                    return v
            return v

        return [safe_parse(v) for v in values]

    async def delete(self, *keys: str) -> int:
        """
        Deletes one or more keys.

        Args:
            *keys (str): Keys to delete.

        Returns:
            int: The number of keys that were removed.

        Raises:
            ValueError: If any key is invalid.
            RuntimeError: If Redis client is not connected.

        Example:
            ```python
            client = RedisClient("redis://localhost:6379/0")
            await client.connect()
            deleted_count = await client.delete("key1", "key2")
            print(f"Deleted {deleted_count} keys.")
            ```
        """
        if not keys:
            return 0
        for key in keys:
            if not key or len(key) > 1024:
                raise ValueError(
                    f"Key '{key}' must be non-empty and <= 1024 characters."
                )
        return await self._execute_operation(
            "delete", "multiple", self.client.delete, *keys
        )

    async def setex(self, key: str, time: int, value: Any) -> bool:
        """
        Set the value and expiration of a key.
        This method uses the 'set' command internally with 'ex' argument.

        Args:
            key (str): The key to set.
            time (int): Expire time in seconds.
            value (Any): The value to store.

        Returns:
            bool: True if set successfully, False otherwise.

        Raises:
            ValueError: If key, time, or value is invalid.
            DataError: If value is not serializable.
            RuntimeError: If Redis client is not connected.

        Example:
            ```python
            client = RedisClient("redis://localhost:6379/0")
            await client.connect()
            success = await client.setex("temp_key", 60, "ephemeral value")
            print(f"Setex successful: {success}")
            ```
        """
        return await self.set(key, value, ex=time)

    def lock(
        self, name: str, timeout: int = 10, blocking_timeout: int = 5
    ) -> RedisLock:
        """
        Creates a new Lock instance for distributed locking with metrics.

        Args:
            name (str): The name of the lock.
            timeout (int): The maximum time in seconds the lock is held.
            blocking_timeout (int): The maximum time in seconds to wait for the lock to be acquired.

        Returns:
            RedisLock: A Redis Lock instance with enhanced acquire/release methods for metrics.

        Raises:
            ValueError: If name or timeouts are invalid.
            RuntimeError: If Redis client is not connected or RedisLock is not available.

        Example:
            ```python
            client = RedisClient("redis://localhost:6379/0")
            await client.connect()
            async with client.lock("my_lock"):
                print("Lock acquired, doing work...")
                await asyncio.sleep(1)
            print("Lock released.")
            ```
        """
        if RedisLock is None:
            raise RuntimeError(
                "Redis locking features are not available. "
                "Please ensure redis>=5.0.0 is installed and redis.asyncio.client.Lock can be imported."
            )
        if not name or len(name) > 1024:
            raise ValueError("Lock name must be non-empty and <= 1024 characters.")
        if timeout <= 0 or blocking_timeout < 0:
            raise ValueError("Timeouts must be non-negative; timeout must be positive.")
        if not self.client:
            raise RuntimeError("Redis client not connected. Call connect() first.")

        logger.debug(
            f"Creating RedisLock for '{_redact_key(name)}' with timeout={timeout}, blocking_timeout={blocking_timeout}"
        )

        lock = RedisLock(
            self.client, name, timeout=timeout, blocking_timeout=blocking_timeout
        )

        # Enhance the acquire and release methods to include metrics
        original_acquire = lock.acquire
        original_release = lock.release

        async def _acquire():
            try:
                acquired = await original_acquire()
                if acquired:
                    REDIS_LOCK_ACQUIRED_TOTAL.inc()
                else:
                    REDIS_LOCK_FAILED_TOTAL.inc()
                return acquired
            except Exception:
                REDIS_LOCK_FAILED_TOTAL.inc()
                raise

        async def _release():
            try:
                await original_release()
                REDIS_LOCK_RELEASED_TOTAL.inc()
            except Exception as e:
                logger.error(
                    f"Failed to release lock '{_redact_key(name)}': {e}", exc_info=True
                )
                raise

        lock.acquire = _acquire
        lock.release = _release

        return lock


# Example Usage (for testing purposes)
async def main():
    # To run this, ensure dependencies are installed:
    # pip install -r requirements.txt
    # Start a Redis server (e.g., docker run -p 6379:6379 redis:latest)
    # Set environment variables:
    # export REDIS_URL="redis://localhost:6379/0"
    # export REDIS_USE_SSL="false"
    # export LOG_LEVEL="DEBUG"
    # export METRICS_PORT="8000"
    # export SFE_OTEL_EXPORTER_TYPE="otlp"
    # export REDIS_HEALTH_CHECK_INTERVAL="10"

    # Configure logging for main
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger.setLevel(logging.DEBUG)  # Set to DEBUG for detailed output in example

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    client = RedisClient(redis_url)

    try:
        await client.connect()
        logger.info("\n--- RedisClient Example Usage ---")

        test_key = "my_test_key"
        test_value = "hello_redis_world"
        test_json_value = {
            "data": "complex_object",
            "timestamp": datetime.now().isoformat(),
        }
        test_expiring_key = "expiring_key"
        test_lock_name = "my_distributed_lock"

        # Test SET operation
        logger.info(f"Setting key '{_redact_key(test_key)}' to '{test_value}'...")
        success = await client.set(test_key, test_value)
        logger.info(f"SET operation successful: {success}")
        assert success

        # Test GET operation
        logger.info(f"Getting value for key '{_redact_key(test_key)}'...")
        retrieved_value = await client.get(test_key)
        logger.info(f"Retrieved value: '{retrieved_value}'")
        assert retrieved_value == test_value

        # Test SET with JSON value
        json_key = f"{test_key}_json"
        logger.info(
            f"Setting key '{_redact_key(json_key)}' to '{test_json_value}' (JSON)..."
        )
        success_json = await client.set(json_key, test_json_value)
        logger.info(f"SET JSON operation successful: {success_json}")
        assert success_json
        retrieved_json_value = await client.get(json_key)
        logger.info(f"Retrieved JSON value: '{retrieved_json_value}'")
        assert retrieved_json_value == test_json_value

        # Test SETEX operation
        logger.info(
            f"Setting expiring key '{_redact_key(test_expiring_key)}' for 2 seconds..."
        )
        success_expiring = await client.setex(test_expiring_key, 2, "will_expire")
        logger.info(f"SETEX operation successful: {success_expiring}")
        assert success_expiring

        await asyncio.sleep(2.5)  # Wait for key to expire
        expired_value = await client.get(test_expiring_key)
        logger.info(
            f"Value for '{_redact_key(test_expiring_key)}' after 2.5s: '{expired_value}'"
        )
        assert expired_value is None

        # Test DELETE operation
        logger.info(f"Deleting key '{_redact_key(test_key)}'...")
        deleted_count = await client.delete(test_key, json_key)
        logger.info(f"DELETE operation removed {deleted_count} key(s).")
        assert deleted_count == 2

        retrieved_after_delete = await client.get(test_key)
        assert retrieved_after_delete is None
        logger.info("Key deletion verified.")

        # Test batch operations
        logger.info("Testing batch operations...")
        batch_data = {f"batch_key_{i}": {"num": i} for i in range(3)}
        success = await client.mset(batch_data)
        logger.info(f"Batch SET successful: {success}")
        assert success
        values = await client.mget(list(batch_data.keys()))
        logger.info(f"Batch GET values: {values}")
        assert len(values) == 3
        deleted = await client.delete(*batch_data.keys())
        logger.info(f"Batch DELETE removed {deleted} keys")
        assert deleted == 3

        # Test Distributed Lock
        logger.info(
            f"Attempting to acquire distributed lock '{_redact_key(test_lock_name)}'..."
        )
        lock = client.lock(
            test_lock_name, timeout=5, blocking_timeout=2
        )  # Lock for max 5s, wait max 2s

        async with lock:
            logger.info(
                f"Successfully acquired lock '{_redact_key(test_lock_name)}'. Holding for 1 second..."
            )
            # Simulate work while holding the lock
            await asyncio.sleep(1)
            logger.info(f"Releasing lock '{_redact_key(test_lock_name)}'.")

        logger.info("Lock released.")

        # Test lock acquisition failure (conceptual, requires concurrent setup)
        # To truly test this, you'd need another client instance trying to acquire the same lock.
        logger.info(
            f"Attempting to acquire lock '{_redact_key(test_lock_name)}' concurrently (should fail)..."
        )
        concurrent_lock = client.lock(test_lock_name, timeout=1, blocking_timeout=0.5)
        try:
            async with concurrent_lock:
                logger.warning(
                    "Concurrent lock acquired (this should ideally not happen in a real concurrent test)."
                )
        except LockError:
            logger.info("Concurrent lock acquisition failed as expected.")
        except Exception as e:
            logger.error(
                f"Unexpected error during concurrent lock attempt: {e}", exc_info=True
            )

        # Test security validations
        logger.info("Testing security validations...")
        try:
            await client.set("toolong" * 300, "value")
        except ValueError as e:
            logger.info(f"Key validation caught: {e}")
            assert "Key must be non-empty and <= 1024 characters." in str(e)
        try:
            await client.set("valid_key", "x" * 2_000_000)
        except ValueError as e:
            logger.info(f"Value size validation caught: {e}")
            assert "Value size exceeds 1MB limit." in str(e)

    except Exception as e:
        logger.error(
            f"An error occurred during RedisClient testing: {e}", exc_info=True
        )
    finally:
        await client.disconnect()
        logger.info("RedisClient disconnected.")


if __name__ == "__main__":
    asyncio.run(main())
