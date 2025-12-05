import logging
import os
from typing import Optional, Union

import redis.asyncio as redis
from opentelemetry import trace
from arbiter.otel_config import get_tracer_safe
from prometheus_client import REGISTRY, Counter
from redis.asyncio.cluster import RedisCluster
from redis.exceptions import RedisError
from tenacity import retry, stop_after_attempt, wait_exponential

# Configure logging
logger = logging.getLogger(__name__)
tracer = get_tracer_safe(__name__)


# Helper function for idempotent metric creation
def _get_or_create_metric(metric_class: type, name: str, doc: str, labelnames: list):
    """Idempotently create or retrieve a Prometheus metric."""
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    return metric_class(name, doc, labelnames)


# Prometheus metric to track idempotency cache hits and misses.
# 'arbiter' label can be used to distinguish between different services using the store.
IDEMPOTENCY_HITS_TOTAL = _get_or_create_metric(
    Counter,
    "idempotency_hits_total",
    "Total number of idempotency check hits and misses.",
    ["arbiter", "hit"],
)


class IdempotencyStoreError(Exception):
    """Custom exception for IdempotencyStore errors."""

    pass


class IdempotencyStore:
    """
    Manages idempotency keys for exactly-once processing using Redis.

    This store provides a mechanism to check for and set idempotency keys to prevent
    duplicate processing of requests or messages. It is designed to be resilient,
    configurable, and observable.

    It supports standard Redis connections, SSL/TLS (rediss://), and Redis Cluster.
    The Redis client is initialized lazily upon calling the start() method.
    """

    def __init__(
        self,
        *,
        redis_url: Optional[str] = None,
        namespace: str = "app:idempotency",
        default_ttl: int = 3600,
        arbiter_name: str = "default",
        cluster_mode: bool = False,
    ):
        """
        Initializes the IdempotencyStore.

        Args:
            redis_url (Optional[str]): The Redis connection URL. If not provided, it will be
                sourced from the 'REDIS_URL' environment variable.
                Example: 'rediss://user:password@host:port'.
            namespace (str): A namespace to prefix all Redis keys, preventing collisions.
                Defaults to "app:idempotency".
            default_ttl (int): The default time-to-live for idempotency keys in seconds.
                Defaults to 3600 (1 hour).
            arbiter_name (str): The name of the service using this store, for observability labels.
                Defaults to "default".
            cluster_mode (bool): Set to True if connecting to a Redis Cluster.
                Defaults to False.
        """
        self.namespace = namespace
        self.default_ttl = default_ttl
        self.arbiter_name = arbiter_name
        self.cluster_mode = cluster_mode
        self.redis: Optional[Union[redis.Redis, RedisCluster]] = None

        # Source redis_url from parameter or environment variable
        _redis_url = redis_url or os.environ.get("REDIS_URL")
        if not _redis_url:
            err_msg = "Redis URL must be provided via 'redis_url' parameter or 'REDIS_URL' environment variable."
            logger.error(err_msg)
            raise IdempotencyStoreError(err_msg)
        self._redis_url = _redis_url

    async def check_and_set(self, key: str, ttl: Optional[int] = None) -> bool:
        """
        Atomically checks if a key exists and sets it if it does not.

        This method uses Redis's 'SET key value NX EX seconds' command to ensure atomicity.

        Args:
            key (str): The unique idempotency key for the operation.
            ttl (Optional[int]): The time-to-live for the key in seconds. If not provided,
                the instance's default_ttl will be used.

        Returns:
            bool: True if the key was successfully set (i.e., it was a new key).
                  False if the key already existed (i.e., it was a duplicate).

        Raises:
            IdempotencyStoreError: If there is an error communicating with Redis or if the store is not started.
        """
        if self.redis is None:
            raise IdempotencyStoreError(
                "IdempotencyStore is not started. Please call start() before use."
            )

        if not key:
            raise ValueError("Idempotency key cannot be empty.")

        effective_ttl = ttl if ttl is not None else self.default_ttl
        namespaced_key = f"{self.namespace}:{key}"

        with tracer.start_as_current_span(
            "idempotency_check", attributes={"idempotency.key": namespaced_key}
        ):
            span = trace.get_current_span()
            try:
                # Atomically set the key if it does not exist (nx=True)
                # with the specified expiration (ex=effective_ttl).
                result = await self.redis.set(
                    namespaced_key, "processed", nx=True, ex=effective_ttl
                )

                if result:
                    # Cache miss - key was set successfully
                    span.set_attribute("idempotency.hit", False)
                    IDEMPOTENCY_HITS_TOTAL.labels(
                        arbiter=self.arbiter_name, hit="false"
                    ).inc()
                    logger.debug("Idempotency key set successfully: %s", namespaced_key)
                    return True
                else:
                    # Cache hit - key already existed
                    span.set_attribute("idempotency.hit", True)
                    IDEMPOTENCY_HITS_TOTAL.labels(
                        arbiter=self.arbiter_name, hit="true"
                    ).inc()
                    logger.debug("Idempotency key hit (duplicate): %s", namespaced_key)
                    return False

            except RedisError as e:
                logger.error(
                    "Redis error during check_and_set for key '%s': %s",
                    namespaced_key,
                    e,
                )
                span.record_exception(e)
                raise IdempotencyStoreError(
                    f"Failed to check/set idempotency key '{namespaced_key}'"
                ) from e

    @retry(
        stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=10), reraise=True
    )
    async def start(self):
        """
        Initializes the Redis client, connects to Redis, and verifies the connection.

        This method is idempotent. If the client is already connected, it will do nothing.
        Implements exponential backoff retry logic to handle transient connection issues.

        Raises:
            IdempotencyStoreError: If the connection fails after all retry attempts.
        """
        if self.redis:
            return

        try:
            logger.info("Connecting to IdempotencyStore Redis...")
            # The `from_url` method transparently handles connection pooling.
            # It also supports SSL/TLS via 'rediss://' protocol and authentication.
            if self.cluster_mode:
                self.redis = RedisCluster.from_url(
                    self._redis_url, decode_responses=True
                )
            else:
                self.redis = redis.from_url(self._redis_url, decode_responses=True)

            await self.redis.ping()
            logger.debug("Ping successful.")
            logger.info("Successfully connected to IdempotencyStore Redis.")
        except (RedisError, Exception) as e:
            logger.error(
                "Failed to connect to IdempotencyStore Redis on attempt: %s", e
            )
            # Ensure redis client is reset to None on failure to allow retry logic to work correctly
            self.redis = None
            raise IdempotencyStoreError("Failed to connect to Redis") from e

    async def stop(self):
        """
        Closes the connection to Redis gracefully.

        Logs a warning if an error occurs during closing but does not raise an exception.
        This method is safe to call even if the store was not started.
        """
        if not self.redis:
            return

        logger.info("Closing connection to IdempotencyStore Redis.")
        try:
            await self.redis.close()
            self.redis = None
        except RedisError as e:
            logger.warning(
                "An error occurred while closing the Redis connection: %s",
                e,
                exc_info=True,
            )
