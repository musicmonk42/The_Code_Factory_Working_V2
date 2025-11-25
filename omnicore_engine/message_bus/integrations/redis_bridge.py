# omnicore_engine/message_bus/integrations/redis_bridge.py

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from copy import copy
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

# Attempt to import redis, which is assumed to be installed in a production environment
try:
    import redis.asyncio as redis
    from redis.asyncio import PubSub, Redis
    from redis.exceptions import ConnectionError, TimeoutError
except ImportError:
    # Use standard library logging for initial import failure
    logging.warning("Redis dependencies not found. RedisBridge will be unavailable.")
    redis = None
    Redis = None
    PubSub = None
    ConnectionError = type("MockConnectionError", (Exception,), {})
    TimeoutError = type("MockTimeoutError", (Exception,), {})


from pydantic import BaseModel, Field

from omnicore_engine.core import safe_serialize

from ..message_types import Message
from ..resilience import CircuitBreaker  # Assumes resilience.py is available

# Optional Prometheus metrics
try:
    from prometheus_client import Counter, Gauge, Histogram  # type: ignore

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROMETHEUS_AVAILABLE = False
    Counter = None
    Gauge = None
    Histogram = None

logger = logging.getLogger(__name__)


# --- Configuration ---
class RedisBridgeConfig(BaseModel):
    """Configuration model for the Redis Bridge."""

    REDIS_URL: str = Field(
        default="redis://localhost:6379/0", description="The connection URL for Redis."
    )
    POOL_SIZE: int = Field(
        default=10, description="Maximum number of connections in the pool."
    )
    TIMEOUT_SECONDS: float = Field(
        default=5.0, description="Connection and operation timeout in seconds."
    )
    USE_STREAM_PUB_SUB: bool = Field(
        default=False,
        description="Use Redis Streams instead of traditional Pub/Sub for complex consumption.",
    )
    DEDUP_KEY_TTL_SECONDS: int = Field(
        default=3600, description="TTL for message deduplication keys."
    )
    HANDLER_MAX_RETRIES: int = Field(
        default=3, description="Maximum retries for message handlers."
    )
    HANDLER_RETRY_BASE_DELAY: float = Field(
        default=0.1, description="Base delay for handler retries (seconds)."
    )
    HANDLER_RETRY_MAX_DELAY: float = Field(
        default=10.0, description="Maximum delay for handler retries (seconds)."
    )
    HANDLER_RETRY_JITTER: float = Field(
        default=0.5, description="Jitter factor for retry delays (0-1)."
    )
    DLQ_CHANNEL_SUFFIX: str = Field(
        default="_dlq", description="Suffix for dead-letter channels."
    )
    ENABLE_METRICS: bool = Field(
        default=True, description="Enable Prometheus metrics if available."
    )

    class Config:
        """Pydantic configuration settings."""

        extra = "ignore"  # Allow extra fields from core ArbiterConfig


# --- Metrics (Prometheus if available) ---
if _PROMETHEUS_AVAILABLE and Counter is not None:  # pragma: no cover
    METRIC_REDIS_PUBLISH_TOTAL = Counter(
        "omnicore_redis_publish_total",
        "Total messages published to Redis",
        ["result", "topic"],
    )
    METRIC_REDIS_CONSUME_TOTAL = Counter(
        "omnicore_redis_consume_total",
        "Total messages consumed from Redis",
        ["result", "topic"],
    )
    METRIC_REDIS_HANDLER_ERRORS = Counter(
        "omnicore_redis_handler_errors_total",
        "Total handler errors for Redis messages",
        ["topic"],
    )
    METRIC_REDIS_DEDUP_HITS = Counter(
        "omnicore_redis_dedup_hits_total",
        "Total dedup cache hits in Redis",
    )
else:  # pragma: no cover
    METRIC_REDIS_PUBLISH_TOTAL = None
    METRIC_REDIS_CONSUME_TOTAL = None
    METRIC_REDIS_HANDLER_ERRORS = None
    METRIC_REDIS_DEDUP_HITS = None


def _metrics_inc_publish(result: str, topic: str) -> None:
    if METRIC_REDIS_PUBLISH_TOTAL is not None:  # pragma: no cover
        try:
            METRIC_REDIS_PUBLISH_TOTAL.labels(result=result, topic=topic).inc()
        except Exception:
            pass


def _metrics_inc_consume(result: str, topic: str) -> None:
    if METRIC_REDIS_CONSUME_TOTAL is not None:  # pragma: no cover
        try:
            METRIC_REDIS_CONSUME_TOTAL.labels(result=result, topic=topic).inc()
        except Exception:
            pass


def _metrics_inc_handler_error(topic: str) -> None:
    if METRIC_REDIS_HANDLER_ERRORS is not None:  # pragma: no cover
        try:
            METRIC_REDIS_HANDLER_ERRORS.labels(topic=topic).inc()
        except Exception:
            pass


def _metrics_inc_dedup_hit() -> None:
    if METRIC_REDIS_DEDUP_HITS is not None:  # pragma: no cover
        try:
            METRIC_REDIS_DEDUP_HITS.inc()
        except Exception:
            pass


# --- Handler Type ---
MessageHandler = Callable[[str, Message], Awaitable[None]]  # topic, message


# --- Bridge Implementation ---


class RedisBridge:
    """
    Asynchronous Redis bridge for the Sharded Message Bus.

    Handles Pub/Sub for external services and acts as a shared backend for
    message deduplication and cross-bus coordination.

    Upgrades from base:
    - Prometheus metrics (optional).
    - Handler retries with exponential backoff + jitter.
    - Health check with connection status and subscriber count.
    - Graceful shutdown with listener cancellation.
    - DLQ simulation via suffixed channels.
    - Typed handlers and full error surfacing.
    """

    def __init__(
        self,
        message_bus: Any,  # Use Any to avoid circular dependency hell
        config: RedisBridgeConfig,
        circuit_breaker: CircuitBreaker,
    ):
        if redis is None:
            raise RuntimeError(
                "RedisBridge requires 'redis-py' with asyncio support. Please install it."
            )

        self.message_bus = message_bus
        self.cfg = config
        self.circuit = circuit_breaker
        self.redis_client: Optional[Redis] = None
        self.pubsub_client: Optional[PubSub] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._listener_lock = asyncio.Lock()  # Issue #23 fix: Lock for listener creation
        self._running = False
        self._stop_event = asyncio.Event()
        self._subscribers: Dict[str, List[MessageHandler]] = {}
        self._subscribed_topics: Set[str] = set()
        self._dedup_namespace = "omnicore:dedup:"
        logger.info("RedisBridge initialized with config.", url=config.REDIS_URL)

    # --- Lifecycle ---

    def _should_start_listener(self) -> bool:
        """Check if listener task needs to be started."""
        if not self._subscribed_topics:
            return False
        if self._listener_task is None:
            return True
        return self._listener_task.done()

    async def _ensure_listener_running(self) -> None:
        """Issue #23 fix: Idempotently starts the listener task."""
        async with self._listener_lock:
            if self._should_start_listener():
                if self._listener_task and self._listener_task.done():
                    self._listener_task = None
                if self._listener_task is None:
                    self._listener_task = asyncio.create_task(self._listener_loop())

    async def start(self):
        """Initializes the Redis connection pool and starts listener if subscribed."""
        if self._running:
            return

        try:
            self.redis_client = redis.from_url(
                self.cfg.REDIS_URL,
                decode_responses=True,
                max_connections=self.cfg.POOL_SIZE,
                socket_timeout=self.cfg.TIMEOUT_SECONDS,
                socket_connect_timeout=self.cfg.TIMEOUT_SECONDS,
            )
            # Test connection
            await self.redis_client.ping()
            self.pubsub_client = self.redis_client.pubsub()
            self._running = True

            # Subscribe to topics if any
            for topic in self._subscribed_topics:
                await self.pubsub_client.subscribe(topic)

            # Start the listener loop in the background if we have subscriptions
            await self._ensure_listener_running()

            self.circuit.record_success()
            logger.info(
                "RedisBridge started successfully.",
                topics=list(self._subscribed_topics),
            )
        except (ConnectionError, TimeoutError) as e:
            self.circuit.record_failure()
            logger.error(f"Failed to start RedisBridge due to connection error: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error starting RedisBridge: {e}", exc_info=True)
            raise

    async def stop(self, drain_timeout: float = 5.0):
        """Gracefully stops the bridge, cancelling tasks and closing connections."""
        if not self._running:
            return

        logger.info("Stopping RedisBridge...")
        self._running = False
        self._stop_event.set()

        # Cancel listener task
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await asyncio.wait_for(self._listener_task, timeout=drain_timeout)
            except asyncio.TimeoutError:
                logger.warning("RedisBridge listener drain timed out.")

        # Unsubscribe if subscribed
        if self.pubsub_client:
            for topic in self._subscribed_topics:
                await self.pubsub_client.unsubscribe(topic)

        # Close clients
        if self.pubsub_client:
            await self.pubsub_client.close()
        if self.redis_client:
            await self.redis_client.close()

        logger.info("RedisBridge stopped.")

    # --- Publishing ---

    async def publish(self, message: Message) -> bool:
        """Publishes a message to the external Redis Pub/Sub channel."""
        if not self._running or not self.redis_client or not self.circuit.can_attempt():
            _metrics_inc_publish("skipped", message.topic)
            return False

        try:
            # Issue #22 fix: Handle encrypted payloads correctly
            if message.encrypted:
                # Payload is already encrypted string/bytes, don't re-serialize
                if isinstance(message.payload, bytes):
                    payload_str = message.payload.decode('utf-8')
                else:
                    payload_str = str(message.payload)
            else:
                payload_str = json.dumps(message.payload, default=safe_serialize)
            
            await self.redis_client.publish(message.topic, payload_str)
            self.circuit.record_success()
            _metrics_inc_publish("success", message.topic)
            logger.debug(
                "Published message to Redis.",
                topic=message.topic,
                trace_id=message.trace_id,
            )
            return True
        except (ConnectionError, TimeoutError) as e:
            self.circuit.record_failure()
            _metrics_inc_publish("conn_fail", message.topic)
            logger.error(
                f"Redis publish failed due to connection/timeout error: {e}",
                topic=message.topic,
            )
            return False
        except Exception as e:
            self.circuit.record_failure()
            _metrics_inc_publish("error", message.topic)
            logger.error(
                f"Unexpected error during Redis publish: {e}",
                topic=message.topic,
                exc_info=True,
            )
            return False

    async def publish_dlq(self, message: Message, original_error: str) -> bool:
        """Simulates DLQ by publishing to a suffixed channel."""
        dlq_channel = f"{message.topic}{self.cfg.DLQ_CHANNEL_SUFFIX}"

        # Create a copy to avoid mutating original
        dlq_message = copy(message)
        dlq_message.topic = dlq_channel

        # Safely add error to payload
        if isinstance(dlq_message.payload, dict):
            dlq_message.payload["dlq_original_error"] = original_error
        else:
            dlq_message.payload = {
                "original_payload": dlq_message.payload,
                "dlq_original_error": original_error,
            }

        return await self.publish(dlq_message)

    # --- Subscription ---

    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        """
        Registers a handler for messages published from the Redis channel.
        
        Issue #21, #27 fix: Make method async and subscribe to Redis immediately if running.
        """
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        self._subscribers[topic].append(handler)

        # Issue #21 fix: If already running, subscribe immediately
        if self._running and self.pubsub_client:
            if topic not in self._subscribed_topics:
                await self.pubsub_client.subscribe(topic)
                self._subscribed_topics.add(topic)
                
                # Start listener if needed
                await self._ensure_listener_running()
        else:
            # Not running yet, just add to set for later
            self._subscribed_topics.add(topic)

        logger.info("Subscribed to Redis topic.", topic=topic)

    async def _listener_loop(self) -> None:
        """Background loop to listen for Pub/Sub messages."""
        while self._running and not self._stop_event.is_set():
            try:
                # Issue #25 fix: Check circuit breaker before attempting operations
                if not self.circuit.can_attempt():
                    logger.warning("Circuit is open, pausing Redis listener")
                    await asyncio.sleep(self.circuit.recovery_timeout)
                    continue

                if not self.pubsub_client:
                    logger.warning("PubSub client not available, waiting...")
                    await asyncio.sleep(1)
                    continue

                message = await self.pubsub_client.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message is None:
                    continue

                topic = message["channel"]
                payload_str = message["data"]

                if payload_str == "ping":  # Ignore pings if any
                    continue

                # Deserialize and create Message
                try:
                    payload = json.loads(payload_str)
                    internal_message = Message(
                        topic=topic,
                        payload=payload,
                        priority=(
                            payload.get("priority", 0)
                            if isinstance(payload, dict)
                            else 0
                        ),
                        timestamp=time.time(),
                        trace_id=(
                            payload.get("trace_id", str(uuid.uuid4()))
                            if isinstance(payload, dict)
                            else str(uuid.uuid4())
                        ),
                        encrypted=False,  # Redis messages come as plain JSON
                        idempotency_key=(
                            payload.get("idempotency_key")
                            if isinstance(payload, dict)
                            else None
                        ),
                        context=(
                            payload.get("context", {})
                            if isinstance(payload, dict)
                            else {}
                        ),
                    )
                except json.JSONDecodeError as e:
                    logger.error(
                        f"Failed to decode JSON from Redis topic {topic}: {e}. Payload: {payload_str[:100]}..."
                    )
                    # Issue #26 fix: Record failure to circuit breaker on decode errors
                    self.circuit.record_failure()
                    _metrics_inc_consume("decode_fail", topic)
                    continue

                # Dispatch to handlers with retries
                await self._dispatch_with_retries(topic, internal_message)

                _metrics_inc_consume("success", topic)

            except asyncio.TimeoutError:
                continue  # Expected on timeout
            except (ConnectionError, TimeoutError) as e:
                self.circuit.record_failure()
                logger.warning(f"Redis listener connection issue: {e}. Retrying...")
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(
                    f"Unexpected error in Redis listener loop: {e}", exc_info=True
                )
                await asyncio.sleep(1)

        logger.info("Redis listener loop exited.")

    async def _dispatch_with_retries(self, topic: str, message: Message) -> None:
        """Dispatches message to handlers with configurable retries."""
        handlers = self._subscribers.get(topic, [])
        if not handlers:
            # Issue #24 fix: Don't fallback to message bus to avoid infinite loop
            # Just log and optionally publish to DLQ
            logger.warning(
                f"No handlers registered for Redis topic {topic}. Message dropped.",
                extra={"trace_id": message.trace_id}
            )
            # Optionally publish to DLQ instead of dropping
            try:
                await self.publish_dlq(message, "No handlers registered")
            except Exception as dlq_err:
                logger.error(f"Failed to publish to DLQ: {dlq_err}")
            return

        for handler in handlers:
            attempt = 0
            delay = self.cfg.HANDLER_RETRY_BASE_DELAY
            while attempt < self.cfg.HANDLER_MAX_RETRIES:
                try:
                    await handler(topic, message)
                    break  # Success
                except Exception as e:
                    attempt += 1
                    _metrics_inc_handler_error(topic)
                    if attempt >= self.cfg.HANDLER_MAX_RETRIES:
                        logger.error(
                            f"Handler failed after {attempt} retries for topic {topic}: {e}"
                        )
                        # Route to DLQ
                        await self.publish_dlq(message, str(e))
                        break

                    # Exponential backoff with jitter
                    jitter = random.uniform(
                        -self.cfg.HANDLER_RETRY_JITTER * delay,
                        self.cfg.HANDLER_RETRY_JITTER * delay,
                    )
                    await asyncio.sleep(max(delay + jitter, 0))
                    delay = min(delay * 2, self.cfg.HANDLER_RETRY_MAX_DELAY)

    # --- Deduplication Cache Backend ---

    async def check_dedup_cache(self, key: str) -> bool:
        """Checks if an idempotency key exists in the shared Redis cache."""
        if not self._running or not self.redis_client or not self.circuit.can_attempt():
            return False

        try:
            exists = await self.redis_client.exists(self._dedup_key(key))
            if exists > 0:
                _metrics_inc_dedup_hit()
            self.circuit.record_success()
            return exists > 0
        except (ConnectionError, TimeoutError) as e:
            self.circuit.record_failure()
            logger.error(f"Redis dedup check failed: {e}")
            return False  # Conservative fail: assume not seen if Redis is down
        except Exception as e:
            self.circuit.record_failure()
            logger.error(
                f"Unexpected error during Redis dedup check: {e}", exc_info=True
            )
            return False

    async def set_dedup_cache(self, key: str, value: str) -> None:
        """Sets an idempotency key in the shared Redis cache with a TTL."""
        if not self._running or not self.redis_client or not self.circuit.can_attempt():
            return

        try:
            await self.redis_client.set(
                self._dedup_key(key), value, ex=self.cfg.DEDUP_KEY_TTL_SECONDS
            )
            self.circuit.record_success()
        except (ConnectionError, TimeoutError) as e:
            self.circuit.record_failure()
            logger.error(f"Redis dedup set failed: {e}")
        except Exception as e:
            self.circuit.record_failure()
            logger.error(f"Unexpected error during Redis dedup set: {e}", exc_info=True)

    def _dedup_key(self, key: str) -> str:
        """Generates a namespaced key for deduplication."""
        return f"{self._dedup_namespace}{key}"

    # --- Health Check ---

    async def health(self) -> Dict[str, Any]:
        """Returns health status of the bridge."""
        status = {
            "running": self._running,
            "redis_connected": self.redis_client is not None and self._running,
            "circuit_state": self.circuit.state,
            "subscriber_count": len(self._subscribers),
            "subscribed_topics": list(self._subscribed_topics),
        }
        try:
            if self.redis_client:
                info = await self.redis_client.info()
                status["redis_uptime"] = info.get("uptime_in_seconds", 0)
                status["redis_memory_used"] = info.get("used_memory", 0)
        except Exception as e:
            status["redis_info_error"] = str(e)
        return status
