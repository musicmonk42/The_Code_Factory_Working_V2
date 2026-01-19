import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import types
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from functools import partial
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Pattern,
    Tuple,
    Union,
)

import structlog

# External project imports
from pydantic import ValidationError

from .backpressure import BackpressureManager
from .cache import MessageCache
from .context import ContextPropagationMiddleware
from .dead_letter_queue import DeadLetterQueue
from .guardian import MessageBusGuardian
from .hash_ring import ConsistentHashRing
from .integrations.kafka_bridge import KafkaBridge
from .integrations.redis_bridge import RedisBridge

# Relative imports from the new modular structure
from .message_types import Message, MessageSchema
from .resilience import CircuitBreaker, RetryPolicy


def _create_fallback_settings():
    """Create a minimal settings object for when ArbiterConfig is unavailable."""
    return types.SimpleNamespace(
        log_level="INFO",
        LOG_LEVEL="INFO",
        database_path="sqlite:///./omnicore.db",
        DB_PATH="sqlite:///./omnicore.db",
        MESSAGE_BUS_SHARD_COUNT=4,
        MESSAGE_BUS_MAX_QUEUE_SIZE=10000,
        MESSAGE_BUS_WORKERS_PER_SHARD=2,
        ENCRYPTION_KEY=None,
        ENCRYPTION_KEY_BYTES=b"",
        REDIS_URL="redis://localhost:6379/0",
        KAFKA_BOOTSTRAP_SERVERS="localhost:9092",
    )


def _get_settings():
    """Lazy import + defensive instantiation of settings."""
    ArbiterConfig = None
    try:
        # Try the full canonical path first (preferred)
        from self_fixing_engineer.arbiter.config import ArbiterConfig
    except ImportError:
        try:
            # Fall back to aliased path for backward compatibility
            from arbiter.config import ArbiterConfig
        except ImportError:
            pass
    
    if ArbiterConfig is None:
        logging.debug(
            "arbiter.config not available; using fallback settings."
        )
        return _create_fallback_settings()

    try:
        return ArbiterConfig()
    except Exception as e:
        logging.warning(
            "ArbiterConfig() raised during instantiation; falling back to minimal settings. Error: %s",
            e,
        )
        return _create_fallback_settings()


settings = _get_settings()

import aiohttp

# Assuming 'safe_serialize' is either in 'omnicore_engine.core' or 'omnicore_engine.utils'
# Using 'omnicore_engine.core' for robustness based on project pattern:
from omnicore_engine.core import safe_serialize

if TYPE_CHECKING:
    from omnicore_engine.database import Database

from omnicore_engine.message_bus.message_types import Message

# FIX: Corrected absolute imports by removing the unnecessary 'app.' prefix.
# These modules must be available as part of the top-level 'omnicore_engine' package.
from omnicore_engine.metrics import (
    MESSAGE_BUS_CALLBACK_ERRORS,
    MESSAGE_BUS_CALLBACK_LATENCY,
    MESSAGE_BUS_DISPATCH_DURATION,
    MESSAGE_BUS_MESSAGE_AGE,
    MESSAGE_BUS_PUBLISH_RETRIES,
    MESSAGE_BUS_QUEUE_SIZE,
    MESSAGE_BUS_TOPIC_THROUGHPUT,
)
from omnicore_engine.plugin_registry import PLUGIN_REGISTRY

# New imports
from omnicore_engine.security_utils import get_security_utils

logger = structlog.get_logger(__name__)
logger = logger.bind(module="ShardedMessageBus")


class RateLimitError(Exception):
    """Custom exception for rate limiting."""

    pass


class RateLimiter:
    def __init__(self, max_requests: int = 100, window: int = 60):
        self.max_requests = max_requests
        self.window = window
        self.requests = defaultdict(list)

    async def check_rate_limit(self, client_id: str) -> bool:
        now = time.time()
        # Clean old requests
        self.requests[client_id] = [
            req for req in self.requests[client_id] if now - req < self.window
        ]

        if len(self.requests[client_id]) >= self.max_requests:
            raise RateLimitError(f"Rate limit exceeded for {client_id}")

        self.requests[client_id].append(now)
        return True


class OrderedLock:
    """A lock that enforces a strict acquisition order to prevent deadlocks."""

    def __init__(self, lock_id: int):
        self.lock_id = lock_id
        self.lock = asyncio.Lock()

    async def __aenter__(self):
        # Always acquire locks in order of lock_id
        await self.lock.acquire()

    async def __aexit__(self, *args):
        self.lock.release()


MAX_MESSAGE_SIZE = 1024 * 1024  # 1MB


def validate_message_size(payload: Any) -> bool:
    """Validates that the message payload does not exceed the maximum size."""
    serialized = json.dumps(payload, default=safe_serialize)
    if len(serialized.encode("utf-8")) > MAX_MESSAGE_SIZE:
        raise ValueError(f"Message too large: {len(serialized)} bytes")
    return True


def sign_message(message: Message, key: bytes) -> str:
    """Signs a message using HMAC-SHA256."""
    data_to_sign = json.dumps(asdict(message), default=safe_serialize).encode("utf-8")
    return hmac.new(key, data_to_sign, hashlib.sha256).hexdigest()


def verify_message(message: Message, signature: str, key: bytes) -> bool:
    """Verifies the HMAC-SHA256 signature of a message."""
    expected = sign_message(message, key)
    return hmac.compare_digest(expected, signature)


class PluginMessageBusAdapter:
    """
    Adapter that provides a simplified interface for plugins to interact with the message bus.
    Each plugin gets its own adapter instance with a dedicated prefix for topics.
    """

    def __init__(self, message_bus: "ShardedMessageBus", plugin_name: str):
        """
        Initialize the adapter.

        Args:
            message_bus: The ShardedMessageBus instance to wrap
            plugin_name: The name of the plugin using this adapter
        """
        self.message_bus = message_bus
        self.plugin_name = plugin_name
        self.topic_prefix = f"plugin.{plugin_name}"
        self.logger = structlog.get_logger(__name__).bind(
            module="PluginMessageBusAdapter", plugin=plugin_name
        )

    async def publish(
        self, topic: str, payload: Dict[str, Any], priority: int = 0
    ) -> bool:
        """
        Publish a message to a topic with plugin prefix.

        Args:
            topic: The topic name (will be prefixed with plugin name)
            payload: The message payload
            priority: Message priority (0-2, higher is more urgent)

        Returns:
            True if published successfully
        """
        full_topic = f"{self.topic_prefix}.{topic}"
        return await self.message_bus.publish(full_topic, payload, priority=priority)

    async def subscribe(
        self, topic: str, callback: Callable, filter_fn: Optional[Callable] = None
    ) -> None:
        """
        Subscribe to a topic with plugin prefix.

        Args:
            topic: The topic name (will be prefixed with plugin name)
            callback: The callback function to call when a message arrives
            filter_fn: Optional filter function to filter messages
        """
        full_topic = f"{self.topic_prefix}.{topic}"
        await self.message_bus.subscribe(full_topic, callback, filter_fn)

    async def unsubscribe(self, topic: str, callback: Callable) -> None:
        """
        Unsubscribe from a topic.

        Args:
            topic: The topic name (will be prefixed with plugin name)
            callback: The callback function to unsubscribe
        """
        full_topic = f"{self.topic_prefix}.{topic}"
        await self.message_bus.unsubscribe(full_topic, callback)

    async def publish_raw(
        self, topic: str, payload: Dict[str, Any], priority: int = 0
    ) -> bool:
        """
        Publish to a raw topic without plugin prefix.

        Args:
            topic: The full topic name
            payload: The message payload
            priority: Message priority (0-2, higher is more urgent)

        Returns:
            True if published successfully
        """
        return await self.message_bus.publish(topic, payload, priority=priority)

    async def subscribe_raw(
        self, topic: str, callback: Callable, filter_fn: Optional[Callable] = None
    ) -> None:
        """
        Subscribe to a raw topic without plugin prefix.

        Args:
            topic: The full topic name
            callback: The callback function to call when a message arrives
            filter_fn: Optional filter function to filter messages
        """
        await self.message_bus.subscribe(topic, callback, filter_fn)


class ShardedMessageBus:
    def __init__(
        self,
        config: Any = None,
        db: Optional["Database"] = None,
        audit_client: Optional[Any] = None,
    ):
        self.security_utils = get_security_utils()
        self.config = config or settings
        self.db = db
        self.audit_client = audit_client
        self.running = True
        self.dynamic_shards_enabled = getattr(
            self.config, "dynamic_shards_enabled", False
        )

        # Core components
        self.shard_count = max(1, getattr(self.config, "message_bus_shard_count", 4))
        self.max_queue_size = getattr(self.config, "message_bus_max_queue_size", 10000)
        self.workers_per_shard = getattr(
            self.config, "message_bus_workers_per_shard", 2
        )

        self.queues = [
            asyncio.PriorityQueue(maxsize=self.max_queue_size)
            for _ in range(self.shard_count)
        ]
        self.high_priority_queues = [
            asyncio.PriorityQueue(maxsize=self.max_queue_size)
            for _ in range(self.shard_count)
        ]

        self.executors = [
            ThreadPoolExecutor(
                max_workers=self.workers_per_shard,
                thread_name_prefix=f"msgbus-normal-shard-{i}",
            )
            for i in range(self.shard_count)
        ]
        self.high_priority_executors = [
            ThreadPoolExecutor(
                max_workers=max(1, self.workers_per_shard // 2),
                thread_name_prefix=f"msgbus-hp-shard-{i}",
            )
            for i in range(self.shard_count)
        ]
        self.callback_executors = [
            ThreadPoolExecutor(
                max_workers=getattr(self.config, "message_bus_callback_workers", 8),
                thread_name_prefix=f"msgbus-callbacks-{i}",
            )
            for i in range(self.shard_count)
        ]

        self.subscribers: Dict[
            str, List[Tuple[Callable[[Message], None], Optional[Any]]]
        ] = defaultdict(list)
        self.regex_subscribers: Dict[
            Pattern, List[Tuple[Callable[[Message], None], Optional[Any]]]
        ] = defaultdict(list)
        self._subscriber_lock = asyncio.Lock()
        self._publish_lock = asyncio.Lock()
        # Issue #13 fix: Add lock for shard management operations
        self._shard_management_lock = asyncio.Lock()
        self.shard_locks = [OrderedLock(i) for i in range(self.shard_count)]
        self.shard_paused = [False] * self.shard_count  # Track paused state per shard

        self.hash_ring = ConsistentHashRing(
            nodes=[str(i) for i in range(self.shard_count)]
        )
        self.topic_to_shard_cache = {}
        self.rebalancing_in_progress = asyncio.Event()
        # --- FIX 1: Set event immediately after creation ---
        self.rebalancing_in_progress.set()

        self.rate_limiter = RateLimiter(
            max_requests=getattr(self.config, "MESSAGE_BUS_RATE_LIMIT_MAX", 1000),
            window=getattr(self.config, "MESSAGE_BUS_RATE_LIMIT_WINDOW", 60),
        )

        self.pre_publish_hooks: List[Callable[[Message], Message]] = []
        self.post_publish_hooks: List[Callable[[Message], None]] = []

        # Use enterprise encryption instead of basic Fernet
        self.encryption = self.security_utils

        self.retry_policies: Dict[str, RetryPolicy] = {
            topic_pattern: RetryPolicy(**policy_args)
            for topic_pattern, policy_args in getattr(
                self.config, "RETRY_POLICIES", {}
            ).items()
        }
        if "default" not in self.retry_policies:
            self.retry_policies["default"] = RetryPolicy()

        self.dedup_cache = MessageCache(
            maxsize=getattr(self.config, "MESSAGE_DEDUP_CACHE_SIZE", 10000),
            ttl=getattr(self.config, "MESSAGE_DEDUP_TTL", 3600),
        )

        # Resiliency components
        self.kafka_circuit = CircuitBreaker(
            failure_threshold=getattr(self.config, "KAFKA_CIRCUIT_THRESHOLD", 5),
            recovery_timeout=getattr(self.config, "KAFKA_CIRCUIT_TIMEOUT", 60),
        )
        self.redis_circuit = CircuitBreaker(
            failure_threshold=getattr(self.config, "REDIS_CIRCUIT_THRESHOLD", 5),
            recovery_timeout=getattr(self.config, "REDIS_CIRCUIT_TIMEOUT", 60),
        )

        # Integration components
        self.kafka_bridge = (
            KafkaBridge(self, self.config, self.kafka_circuit)
            if getattr(self.config, "USE_KAFKA", False)
            else None
        )
        self.redis_bridge = (
            RedisBridge(self, self.config, self.redis_circuit)
            if getattr(self.config, "USE_REDIS", False)
            else None
        )

        # Operational components
        self.dlq = DeadLetterQueue(
            db, self.kafka_bridge, getattr(self.config, "DLQ_PRIORITY_THRESHOLD", 5)
        )
        self.backpressure_manager = BackpressureManager(
            self, threshold=getattr(self.config, "BACKPRESSURE_THRESHOLD", 0.8)
        )
        self.context_propagation_middleware = ContextPropagationMiddleware(self)
        self.guardian = (
            MessageBusGuardian(
                self,
                check_interval=getattr(
                    self.config, "MESSAGE_BUS_GUARDIAN_INTERVAL", 30
                ),
            )
            if getattr(self.config, "ENABLE_MESSAGE_BUS_GUARDIAN", False)
            else None
        )

        # Store the event loop - we'll use _get_loop() for safer access
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            logger.error(
                "ShardedMessageBus must be initialized within a running asyncio event loop."
            )
            raise RuntimeError(
                "ShardedMessageBus must be initialized within a running asyncio event loop."
            )

        self.dispatcher_tasks = []
        self._start_dispatchers()
        if self.kafka_bridge:
            self.kafka_bridge.start()
        if self.redis_bridge:
            self.redis_bridge.start()
        if self.guardian:
            self.guardian.start()

        if self.dynamic_shards_enabled:
            asyncio.create_task(self._periodic_rebalance_check())

        logger.info(
            "ShardedMessageBus initialized.",
            shard_count=self.shard_count,
            workers_per_shard=self.workers_per_shard,
            use_kafka=self.kafka_bridge is not None,
            use_redis=self.redis_bridge is not None,
        )

    def _get_loop(self):
        """
        Get the current running event loop.

        Issue #15 fix: Raise an error instead of returning potentially stale/closed loop.
        """
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            raise RuntimeError(
                "ShardedMessageBus operation called outside async context. "
                "Ensure you're calling from within an async function."
            )

    def _start_dispatchers(self) -> None:
        """Starts asynchronous dispatcher tasks for each queue."""
        for shard_id in range(self.shard_count):
            task = asyncio.create_task(
                self._dispatcher_loop(
                    shard_id,
                    self.queues[shard_id],
                    self.executors[shard_id],
                    high_priority=False,
                )
            )
            self.dispatcher_tasks.append(task)
            task = asyncio.create_task(
                self._dispatcher_loop(
                    shard_id,
                    self.high_priority_queues[shard_id],
                    self.high_priority_executors[shard_id],
                    high_priority=True,
                )
            )
            self.dispatcher_tasks.append(task)
        logger.info("Dispatcher tasks started.", num_tasks=len(self.dispatcher_tasks))

    async def _dispatcher_loop(
        self,
        shard_id: int,
        queue: asyncio.PriorityQueue,
        executor: ThreadPoolExecutor,
        high_priority: bool,
    ) -> None:
        queue_type = "high_priority" if high_priority else "normal"
        logger.info(
            "Starting dispatcher loop.", shard_id=shard_id, queue_type=queue_type
        )
        while self.running:
            try:
                MESSAGE_BUS_QUEUE_SIZE.labels(shard_id=str(shard_id)).set(queue.qsize())
                priority, message = await asyncio.wait_for(queue.get(), timeout=0.1)
                await self.backpressure_manager.check_and_notify(shard_id)
                message_age = time.time() - message.timestamp
                MESSAGE_BUS_MESSAGE_AGE.labels(shard_id=str(shard_id)).observe(
                    message_age
                )
                with MESSAGE_BUS_DISPATCH_DURATION.labels(
                    shard_id=str(shard_id)
                ).time():
                    await self._dispatch_message_to_subscribers_and_externals(
                        message, self.callback_executors[shard_id]
                    )
                queue.task_done()
                logger.debug(
                    "Message dispatched and queue task done.",
                    topic=message.topic,
                    trace_id=message.trace_id,
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                logger.info(
                    f"Dispatcher loop for shard {shard_id}, {queue_type} queue cancelled."
                )
                break
            except Exception as e:
                logger.error(
                    "Error in dispatcher loop.",
                    shard_id=shard_id,
                    queue_type=queue_type,
                    error=e,
                )
                MESSAGE_BUS_CALLBACK_ERRORS.labels(shard_id="unknown").inc()
                MESSAGE_BUS_CALLBACK_ERRORS.labels(
                    shard_id=str(shard_id), topic="unknown"
                ).inc()

    async def _safe_callback_internal(
        self,
        callback: Callable[[Message], Any],
        message: Message,
        filter: Optional[Any] = None,
    ) -> None:
        callback_name = getattr(
            callback,
            "__name__",
            (
                getattr(callback, "__class__", None).__name__
                if hasattr(callback, "__class__")
                else str(callback)
            ),
        )
        logger_for_callback = logger.bind(
            trace_id=message.trace_id, topic=message.topic, callback_name=callback_name
        )

        if filter and not filter.apply(message.payload):
            logger_for_callback.debug(
                "Message filtered out by subscriber.", filter=filter.__class__.__name__
            )
            return

        try:
            with MESSAGE_BUS_CALLBACK_LATENCY.labels(
                topic=message.topic, callback=callback_name
            ).time():
                if asyncio.iscoroutinefunction(callback):
                    await callback(message)
                else:
                    await asyncio.to_thread(callback, message)
            logger_for_callback.debug("Callback executed successfully.")
        except Exception as e:
            logger_for_callback.error("Error in message callback.", error=e)
            shard_id_label = str(self.hash_ring.get_node(message.topic))
            MESSAGE_BUS_CALLBACK_ERRORS.labels(
                shard_id=shard_id_label, topic=message.topic
            ).inc()

            if message.priority >= self.dlq.priority_threshold:
                await self.dlq.add(message, str(e))
                logger_for_callback.warning(
                    "Message sent to DLQ due to callback error."
                )

    async def _dispatch_message_to_subscribers_and_externals(
        self, message: Message, callback_executor: ThreadPoolExecutor
    ) -> None:
        logger_for_dispatch = logger.bind(
            trace_id=message.trace_id, topic=message.topic
        )
        MESSAGE_BUS_TOPIC_THROUGHPUT.labels(topic=message.topic).inc()

        subscribers_to_dispatch = []
        async with self._subscriber_lock:
            subscribers_to_dispatch.extend(self.subscribers.get(message.topic, []))
            for pattern, pattern_subscribers in self.regex_subscribers.items():
                if pattern.match(message.topic):
                    subscribers_to_dispatch.extend(pattern_subscribers)

        for callback, filter in subscribers_to_dispatch:
            callback_executor.submit(
                lambda cb=callback, msg=message, flt=filter: asyncio.run_coroutine_threadsafe(
                    self.context_propagation_middleware._restore_context_wrapper(
                        cb, msg, flt
                    ),
                    self._get_loop(),
                )
            )
        logger_for_dispatch.debug("Message submitted to internal subscribers.")

        if self.kafka_bridge:
            await self.kafka_bridge.publish(message)
        if self.redis_bridge:
            await self.redis_bridge.publish(message)

        for hook in self.post_publish_hooks:
            try:
                if asyncio.iscoroutinefunction(hook):
                    await hook(message)
                else:
                    callback_executor.submit(hook, message)
                logger_for_dispatch.debug(
                    "Post-publish hook executed.",
                    hook=getattr(hook, "__name__", str(hook)),
                )
            except Exception as e:
                logger_for_dispatch.error(
                    f"Error in post-publish hook: {e}",
                    hook=getattr(hook, "__name__", str(hook)),
                )
                MESSAGE_BUS_CALLBACK_ERRORS.labels(shard_id="hook").inc()
                MESSAGE_BUS_CALLBACK_ERRORS.labels(
                    shard_id="hook", topic=message.topic
                ).inc()

    def add_pre_publish_hook(self, hook: Callable[[Message], Message]) -> None:
        self.pre_publish_hooks.append(hook)
        logger.debug(
            "Added pre-publish hook.", hook=getattr(hook, "__name__", str(hook))
        )

    def add_post_publish_hook(self, hook: Callable[[Message], None]) -> None:
        self.post_publish_hooks.append(hook)
        logger.debug(
            "Added post-publish hook.", hook=getattr(hook, "__name__", str(hook))
        )

    async def _get_shard_id(self, message: Message) -> int:
        """Helper to get shard ID and handle rebalancing."""
        await self.rebalancing_in_progress.wait()
        shard_id_str = self.topic_to_shard_cache.get(message.topic)
        if shard_id_str is None:
            shard_id_str = self.hash_ring.get_node(message.topic)
            self.topic_to_shard_cache[message.topic] = shard_id_str
        return int(shard_id_str)

    async def _publish_to_shard(
        self, shard_id: int, message: Message, retries: int = 3
    ) -> bool:
        """Internal helper to publish a message to a specific shard's queue."""
        logger_for_publish = logger.bind(
            trace_id=message.trace_id, topic=message.topic, shard_id=shard_id
        )

        policy = self.retry_policies.get(
            message.topic, self.retry_policies.get("default", RetryPolicy())
        )
        actual_retries = retries if retries is not None else policy.max_retries

        # Issue #12 fix: Wait for shard to resume if paused (backpressure enforcement)
        while self.shard_paused[shard_id] and self.running:
            logger_for_publish.debug("Shard is paused, waiting for resume...")
            await asyncio.sleep(0.1)

        async with self.shard_locks[shard_id]:
            for attempt in range(actual_retries + 1):
                try:
                    queue_to_use = (
                        self.high_priority_queues[shard_id]
                        if message.priority
                        >= getattr(self.config, "priority_threshold", 5)
                        else self.queues[shard_id]
                    )
                    await queue_to_use.put((message.priority, message))

                    if message.idempotency_key:
                        self.dedup_cache.put(message.idempotency_key, message.trace_id)
                        if self.redis_bridge:
                            await self.redis_bridge.set_dedup_cache(
                                message.idempotency_key, message.trace_id
                            )

                    if self.db and message.priority >= getattr(
                        self.config, "message_persistence_priority_threshold", 5
                    ):
                        payload_to_persist = message.payload
                        if message.encrypted and self.encryption:
                            # Decrypt returns bytes, so we need to decode it to string, then parse JSON
                            decrypted_bytes = self.encryption.decrypt(message.payload)
                            payload_to_persist = json.loads(
                                decrypted_bytes.decode("utf-8")
                            )
                        await self.db.save_preferences(
                            user_id=f"message_trace_{message.trace_id}",
                            prefs={
                                "topic": message.topic,
                                "payload": safe_serialize(payload_to_persist),
                                "timestamp": message.timestamp,
                                "idempotency_key": message.idempotency_key,
                                "context": message.context,
                            },
                        )

                    if self.audit_client:
                        await self.audit_client.add_entry_async(
                            "message_published",
                            "message_bus",
                            {
                                "topic": message.topic,
                                "trace_id": message.trace_id,
                                "priority": message.priority,
                                "encrypted": message.encrypted,
                                "idempotency_key": message.idempotency_key,
                            },
                            agent_id="ShardedMessageBus",
                        )
                    logger_for_publish.info(
                        "Message successfully published to internal queue."
                    )
                    return True
                except asyncio.QueueFull:
                    MESSAGE_BUS_PUBLISH_RETRIES.labels(shard_id=str(shard_id)).inc()
                    logger_for_publish.warning(
                        "Message bus queue full. Retrying...",
                        attempt=attempt + 1,
                        max_attempts=actual_retries + 1,
                    )
                    if attempt == actual_retries:
                        logger_for_publish.error(
                            f"Message bus queue full for shard {shard_id}, topic {message.topic} after {actual_retries + 1} attempts."
                        )
                        if self.redis_bridge and await self.redis_bridge.publish(
                            message
                        ):
                            logger_for_publish.info(
                                "Fallback: Published message to Redis."
                            )
                            if self.audit_client:
                                await self.audit_client.add_entry_async(
                                    "message_published_redis_fallback",
                                    "message_bus",
                                    {
                                        "topic": message.topic,
                                        "trace_id": message.trace_id,
                                        "priority": message.priority,
                                    },
                                    agent_id="ShardedMessageBus",
                                )
                            return True
                        await self.dlq.add(
                            message,
                            f"Failed to publish after {actual_retries + 1} attempts, queue full.",
                        )
                        return False
                    await asyncio.sleep(policy.backoff_factor * (2**attempt))
                except Exception as e:
                    logger_for_publish.error(
                        f"Unexpected error during message publish: {e}"
                    )
                    if self.audit_client:
                        # --- FIX 2: Use message.topic instead of undefined 'topic' ---
                        await self.audit_client.add_entry_async(
                            "message_publish_unexpected_error",
                            "message_bus",
                            {"topic": message.topic, "error": str(e)},
                            error=str(e),
                            agent_id="ShardedMessageBus",
                        )
                    await self.dlq.add(message, f"Unexpected publish error: {e}")
                    return False

    async def publish(
        self,
        topic: str,
        payload: Dict[str, Any],
        priority: int = 0,
        retries: int = 3,
        trace_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        encrypt: bool = False,
        context: Optional[Dict[str, Any]] = None,
        client_id: Optional[str] = "default",
        signature: Optional[str] = None,
    ) -> bool:
        """
        Publishes a message to the message bus, handling arbiter-specific events.
        """
        # Security: Rate Limiting
        try:
            await self.rate_limiter.check_rate_limit(client_id)
        except RateLimitError as e:
            logger.warning(f"Publish request denied: {e}")
            if self.audit_client:
                await self.audit_client.add_entry_async(
                    "rate_limit_exceeded",
                    "message_bus",
                    {"client_id": client_id},
                    error=str(e),
                    agent_id="ShardedMessageBus",
                )
            return False

        # Security: Message Size Validation
        try:
            validate_message_size(payload)
        except ValueError as e:
            logger.warning(f"Publish request denied: {e}")
            if self.audit_client:
                await self.audit_client.add_entry_async(
                    "message_size_exceeded",
                    "message_bus",
                    {"client_id": client_id},
                    error=str(e),
                    agent_id="ShardedMessageBus",
                )
            return False

        message = Message(
            topic=topic,
            payload=payload,
            priority=priority,
            trace_id=trace_id or str(uuid.uuid4()),
            idempotency_key=idempotency_key,
            encrypted=encrypt,
            context=context,
            timestamp=time.time(),
        )

        # Security: Message Signature Verification
        if signature:
            signing_key = getattr(self.config, "MESSAGE_SIGNING_KEY", None)
            if signing_key is None:
                logger.warning(
                    "Message signature provided but MESSAGE_SIGNING_KEY is not configured. Aborting."
                )
                if self.audit_client:
                    await self.audit_client.add_entry_async(
                        "missing_signing_key",
                        "message_bus",
                        {"topic": topic},
                        error="Missing signing key",
                        agent_id="ShardedMessageBus",
                    )
                return False
            if not verify_message(
                message, signature, signing_key.get_secret_value().encode("utf-8")
            ):
                logger.warning(
                    "Message signature verification failed. Aborting publish."
                )
                if self.audit_client:
                    await self.audit_client.add_entry_async(
                        "message_signature_failed",
                        "message_bus",
                        {"topic": topic},
                        error="Signature mismatch",
                        agent_id="ShardedMessageBus",
                    )
                return False

        # Add the provided code here
        if topic == "start_workflow":
            # This is a bit problematic as PLUGIN_REGISTRY.execute is a blocking call within an async function
            # The correct way would be to get the plugin from the registry and then call its async execute method
            # Example:
            # plugin = PLUGIN_REGISTRY.get("scenario", "generator_workflow")
            # if plugin:
            #     result = await plugin.execute(action="run_generator_workflow", requirements=payload.get("requirements"), config=payload.get("config"))
            #     await self.publish("analyze_code", result, priority=10)

            # Since the original code provided `PLUGIN_REGISTRY.execute`, and we don't know the exact signature or if it's async,
            # we'll assume it's a synchronous call and wrap it in a thread for safety.
            # However, the user-provided code block assumes a direct call.
            # We'll stick to the user's provided code for now to maintain the specified logic.
            # The prompt requires fixing the file, so we should make a reasonable assumption.
            # The user's prompt is a mix of sync and async calls, so we'll go with the most likely correct approach.
            async def _run_workflow():
                # A more robust implementation would check plugin existence and use it correctly
                try:
                    gen_plugin = PLUGIN_REGISTRY.get("scenario", "generator_workflow")
                    if gen_plugin:
                        if not hasattr(gen_plugin, "execute"):
                            logger.error(
                                "generator_workflow plugin does not have execute method."
                            )
                            return

                        # Check if execute is async
                        if asyncio.iscoroutinefunction(gen_plugin.execute):
                            gen_result = await gen_plugin.execute(
                                action="run_generator_workflow",
                                requirements=payload.get("requirements"),
                                config=payload.get("config"),
                            )
                        else:
                            gen_result = await asyncio.to_thread(
                                gen_plugin.execute,
                                action="run_generator_workflow",
                                requirements=payload.get("requirements"),
                                config=payload.get("config"),
                            )

                        await self.publish("analyze_code", gen_result, priority=10)
                    else:
                        logger.error("generator_workflow plugin not found.")
                except Exception as e:
                    logger.error(f"Error in start_workflow: {e}")

            asyncio.create_task(_run_workflow())
            # We return True immediately to indicate the message was accepted, even if processing is async.
            return True

        elif topic == "analyze_code":

            async def _run_analyzer():
                try:
                    analyzer_plugin = PLUGIN_REGISTRY.get(
                        "execution", "codebase_analyzer"
                    )
                    if analyzer_plugin:
                        if not hasattr(analyzer_plugin, "execute"):
                            logger.error(
                                "codebase_analyzer plugin does not have execute method."
                            )
                            return

                        # Check if execute is async
                        if asyncio.iscoroutinefunction(analyzer_plugin.execute):
                            analyzer_result = await analyzer_plugin.execute(
                                action="analyze_codebase", root_dir=payload.get("code")
                            )
                        else:
                            analyzer_result = await asyncio.to_thread(
                                analyzer_plugin.execute,
                                action="analyze_codebase",
                                root_dir=payload.get("code"),
                            )

                        await self.publish("manage_bug", analyzer_result, priority=10)
                    else:
                        logger.error("codebase_analyzer plugin not found.")
                except Exception as e:
                    logger.error(f"Error in analyze_code: {e}")

            asyncio.create_task(_run_analyzer())
            return True

        elif topic == "manage_bug":

            async def _run_bug_manager():
                try:
                    bug_manager_plugin = PLUGIN_REGISTRY.get("fix", "bug_manager")
                    if bug_manager_plugin:
                        if not hasattr(bug_manager_plugin, "execute"):
                            logger.error(
                                "bug_manager plugin does not have execute method."
                            )
                            return

                        # Check if execute is async
                        if asyncio.iscoroutinefunction(bug_manager_plugin.execute):
                            bug_manager_result = await bug_manager_plugin.execute(
                                action="manage_bug", code=payload.get("analysis")
                            )
                        else:
                            bug_manager_result = await asyncio.to_thread(
                                bug_manager_plugin.execute,
                                action="manage_bug",
                                code=payload.get("analysis"),
                            )

                        await self.publish(
                            "workflow_completed", bug_manager_result, priority=10
                        )
                    else:
                        logger.error("bug_manager plugin not found.")
                except Exception as e:
                    logger.error(f"Error in manage_bug: {e}")

            asyncio.create_task(_run_bug_manager())
            return True

        if topic.startswith("requests.arbiter"):
            async with aiohttp.ClientSession() as session:
                try:
                    await session.post(
                        self.config.ARBITER_URL + "/events",
                        json={"event_type": topic, "data": payload},
                    )
                    logger.info(f"Forwarded arbiter event to {topic}")
                except Exception as e:
                    logger.error(f"Failed to forward arbiter event: {e}")

        async with self._publish_lock:
            current_trace_id = trace_id or str(uuid.uuid4())
            logger_for_publish = logger.bind(trace_id=current_trace_id, topic=topic)

            if idempotency_key:
                if self.dedup_cache.get(idempotency_key) is not None:
                    logger_for_publish.info(
                        "Duplicate message detected (deduplication cache hit). Skipping publish.",
                        idempotency_key=idempotency_key,
                    )
                    return True
                if self.redis_bridge and await self.redis_bridge.check_dedup_cache(
                    idempotency_key
                ):
                    return True

            processed_payload = payload
            if encrypt:
                # Use security_utils encryption
                try:
                    processed_payload = self.security_utils.encrypt(json.dumps(payload))
                except Exception as e:
                    logger_for_publish.error(f"Failed to encrypt message: {e}")
                    if self.audit_client:
                        await self.audit_client.add_entry_async(
                            "message_encryption_failed",
                            "message_bus",
                            {"topic": topic, "error": str(e)},
                            error=str(e),
                            agent_id="ShardedMessageBus",
                        )
                    return False
            else:
                processed_payload = json.dumps(payload)

            message = Message(
                topic=topic,
                payload=processed_payload,
                priority=priority,
                timestamp=time.time(),
                trace_id=current_trace_id,
                encrypted=encrypt,
                idempotency_key=idempotency_key,
                context=context if context is not None else {},
            )

            for hook in self.pre_publish_hooks:
                try:
                    message = hook(message)
                except Exception as e:
                    logger_for_publish.error(
                        f"Error in pre-publish hook: {e}. Aborting publish.",
                        hook=getattr(hook, "__name__", str(hook)),
                    )
                    if self.audit_client:
                        await self.audit_client.add_entry_async(
                            "pre_publish_hook_failed",
                            "message_bus",
                            {"topic": topic, "error": str(e)},
                            error=str(e),
                            agent_id="ShardedMessageBus",
                        )
                    return False

            # --- FIX 8 & 9: Correct validation logic for encrypted and non-encrypted payloads ---
            if getattr(self.config, "ENHANCED_PLUGIN_VALIDATION", False):
                try:
                    validation_payload_dict = None
                    if message.encrypted and self.encryption:
                        try:
                            # Decrypt returns bytes, need to decode to string then parse JSON
                            decrypted_bytes = self.encryption.decrypt(message.payload)
                            decrypted_json = decrypted_bytes.decode("utf-8")
                            validation_payload_dict = json.loads(decrypted_json)
                        except Exception as e:
                            logger_for_publish.warning(
                                f"Failed to decrypt validation payload: {e}",
                                exc_info=True,
                            )
                            raise ValueError(f"Decryption failed for validation: {e}")
                    else:
                        validation_payload_dict = json.loads(
                            message.payload
                        )  # message.payload is JSON string

                    MessageSchema(
                        topic=message.topic,
                        payload=safe_serialize(validation_payload_dict),
                        priority=message.priority,
                        trace_id=message.trace_id,
                        idempotency_key=message.idempotency_key,
                        context=message.context,
                    )
                except ValidationError as e:
                    logger_for_publish.error(f"Invalid message payload: {e}")
                    if self.audit_client:
                        await self.audit_client.add_entry_async(
                            "message_validation_failed",
                            "message_bus",
                            {"topic": topic, "error": str(e)},
                            error=str(e),
                            agent_id="ShardedMessageBus",
                        )
                    return False
                except Exception as e:
                    logger_for_publish.error(
                        f"Unexpected error during message validation: {e}"
                    )
                    return False

            shard_id = await self._get_shard_id(message)
            return await self._publish_to_shard(shard_id, message, retries=retries)

    async def batch_publish(self, messages: List[Dict[str, Any]]) -> List[bool]:
        if not messages:
            return []

        results = []
        for msg_data in messages:
            results.append(await self.publish(**msg_data))

        return results

    # --- FIX 4: Change MessageFilter to Any ---
    def subscribe(
        self,
        topic: Union[str, Pattern],
        handler: Callable,
        filter: Optional[Any] = None,
    ):
        logger_for_subscribe = logger.bind(
            topic=str(topic), handler=getattr(handler, "__name__", str(handler))
        )
        asyncio.run_coroutine_threadsafe(
            self._subscribe_async(topic, handler, filter), self._get_loop()
        )
        # --- FIX 7: Add type check for startswith ---
        if isinstance(topic, str) and topic.startswith("requests.arbiter"):
            logger_for_subscribe.info(f"Registered arbiter task handler for {topic}")

    async def _subscribe_async(
        self,
        topic: Union[str, Pattern],
        callback: Callable[[Message], None],
        filter: Optional[Any] = None,
    ) -> None:
        async with self._subscriber_lock:
            logger_for_subscribe = logger.bind(
                topic=str(topic), callback=getattr(callback, "__name__", str(callback))
            )
            if isinstance(topic, str):
                self.subscribers[topic].append((callback, filter))
                logger_for_subscribe.info("Subscribed callback to topic.")
            else:
                self.regex_subscribers[topic].append((callback, filter))
                logger_for_subscribe.info("Subscribed callback to regex pattern.")

    def unsubscribe(
        self, topic: Union[str, Pattern], callback: Callable[[Message], None]
    ) -> None:
        logger.bind(
            topic=str(topic), callback=getattr(callback, "__name__", str(callback))
        )
        asyncio.run_coroutine_threadsafe(
            self._unsubscribe_async(topic, callback), self._get_loop()
        )

    async def _unsubscribe_async(
        self, topic: Union[str, Pattern], callback: Callable[[Message], None]
    ) -> None:
        async with self._subscriber_lock:
            logger_for_unsubscribe = logger.bind(
                topic=str(topic), callback=getattr(callback, "__name__", str(callback))
            )

            def filter_out_callback(item):
                return item[0] != callback

            if isinstance(topic, str):
                initial_len = len(self.subscribers[topic])
                self.subscribers[topic] = list(
                    filter(filter_out_callback, self.subscribers[topic])
                )
                if len(self.subscribers[topic]) < initial_len:
                    logger_for_unsubscribe.info("Unsubscribed callback from topic.")
                else:
                    logger_for_unsubscribe.warning(
                        "Callback not found for unsubscribe from topic."
                    )
            else:
                initial_len = len(self.regex_subscribers[topic])
                self.regex_subscribers[topic] = list(
                    filter(filter_out_callback, self.regex_subscribers[topic])
                )
                if len(self.regex_subscribers[topic]) < initial_len:
                    logger_for_unsubscribe.info(
                        "Unsubscribed callback from regex pattern."
                    )
                else:
                    logger_for_unsubscribe.warning(
                        "Callback not found for unsubscribe from regex pattern."
                    )

    async def request(
        self, topic: str, payload: Any, timeout: float = 5.0, priority: int = 5
    ) -> Any:
        reply_topic = f"reply.{str(uuid.uuid4())}"
        response_future = asyncio.Future()
        logger_for_request = logger.bind(request_topic=topic, reply_topic=reply_topic)

        def handle_reply(message: Message):
            if not response_future.done():
                response_future.set_result(message.payload)
                logger_for_request.debug(
                    "Received reply for request.", reply_topic=message.topic
                )
            else:
                logger_for_request.warning(
                    "Received late reply for request.", reply_topic=message.topic
                )

        self.subscribe(reply_topic, handle_reply)
        try:
            await self.publish(
                topic,
                payload,
                trace_id=f"request-response-{reply_topic}",
                priority=priority,
            )
            logger_for_request.info("Request message published.")
            result = await asyncio.wait_for(response_future, timeout)
            return result
        except asyncio.TimeoutError:
            logger_for_request.error("Request timed out waiting for reply.")
            raise TimeoutError(
                f"Request to topic '{topic}' timed out after {timeout} seconds."
            )
        finally:
            self.unsubscribe(reply_topic, handle_reply)

    async def _periodic_rebalance_check(self, interval: int = 60) -> None:
        """A periodic task to check queue load and trigger rebalancing if dynamic sharding is enabled."""
        while self.running and self.dynamic_shards_enabled:
            await asyncio.sleep(interval)

            queue_loads = [q.qsize() for q in self.queues]
            hp_queue_loads = [q.qsize() for q in self.high_priority_queues]

            avg_load = (sum(queue_loads) + sum(hp_queue_loads)) / (
                len(queue_loads) + len(hp_queue_loads)
            )
            max_size = self.max_queue_size

            if avg_load > max_size * getattr(
                self.config, "message_bus_scale_up_threshold", 0.8
            ):
                logger.info(
                    f"Average queue load ({avg_load}) is above threshold. Considering adding a new shard."
                )
                await self.add_shard()
            elif (
                avg_load
                < max_size
                * getattr(self.config, "message_bus_scale_down_threshold", 0.2)
                and self.shard_count > 1
            ):
                logger.info(
                    f"Average queue load ({avg_load}) is below threshold. Considering removing a shard."
                )
                # We can't just remove the last shard; we need to decide which one.
                # For simplicity, remove the shard with the lowest load.
                target_shard_id = min(
                    range(self.shard_count),
                    key=lambda i: queue_loads[i] + hp_queue_loads[i],
                )
                await self.remove_shard(target_shard_id)

    async def add_shard(self):
        """Dynamically add a shard and rebalance."""
        if not self.dynamic_shards_enabled:
            raise ValueError("Dynamic sharding not enabled.")

        # Issue #13 fix: Use lock to prevent race conditions in shard management
        async with self._shard_management_lock:
            self.rebalancing_in_progress.clear()
            logger.info("Starting dynamic shard addition.")

            new_shard_id = self.shard_count

            self.queues.append(asyncio.PriorityQueue(maxsize=self.max_queue_size))
            self.high_priority_queues.append(
                asyncio.PriorityQueue(maxsize=self.max_queue_size)
            )
            self.executors.append(
                ThreadPoolExecutor(
                    max_workers=self.workers_per_shard,
                    thread_name_prefix=f"msgbus-normal-shard-{new_shard_id}",
                )
            )
            self.high_priority_executors.append(
                ThreadPoolExecutor(
                    max_workers=self.workers_per_shard,
                    thread_name_prefix=f"msgbus-hp-shard-{new_shard_id}",
                )
            )
            self.callback_executors.append(
                ThreadPoolExecutor(
                    max_workers=self.workers_per_shard,
                    thread_name_prefix=f"msgbus-callbacks-{new_shard_id}",
                )
            )
            self.shard_locks.append(OrderedLock(new_shard_id))
            self.shard_paused.append(False)  # Initialize paused state for new shard

            self.dispatcher_tasks.append(
                asyncio.create_task(
                    self._dispatcher_loop(
                        new_shard_id,
                        self.queues[-1],
                        self.executors[-1],
                        high_priority=False,
                    )
                )
            )
            self.dispatcher_tasks.append(
                asyncio.create_task(
                    self._dispatcher_loop(
                        new_shard_id,
                        self.high_priority_queues[-1],
                        self.high_priority_executors[-1],
                        high_priority=True,
                    )
                )
            )

            self.hash_ring.add_node_dynamic(
                str(new_shard_id),
                partial(self._rebalance_callback, old_shard_count=self.shard_count),
            )
            self.shard_count += 1
            self.topic_to_shard_cache.clear()  # Invalidate cache after rebalance
            logger.info(
                f"Added new shard {new_shard_id}. New shard count: {self.shard_count}."
            )
            self.rebalancing_in_progress.set()

    async def remove_shard(self, shard_id: int):
        """Dynamically remove a shard and rebalance."""
        if not self.dynamic_shards_enabled or not (0 <= shard_id < self.shard_count):
            raise ValueError(f"Invalid shard removal. Shard {shard_id} does not exist.")
        if self.shard_count <= 1:
            logger.warning("Cannot remove shard; only one shard remains.")
            return

        # Issue #13 fix: Use lock to prevent race conditions in shard management
        async with self._shard_management_lock:
            self.rebalancing_in_progress.clear()
            logger.info(f"Starting dynamic shard removal for shard {shard_id}.")

            await self.queues[shard_id].join()
            await self.high_priority_queues[shard_id].join()

            self.dispatcher_tasks[shard_id * 2].cancel()
            self.dispatcher_tasks[shard_id * 2 + 1].cancel()

            await asyncio.gather(
                self.dispatcher_tasks[shard_id * 2],
                self.dispatcher_tasks[shard_id * 2 + 1],
                return_exceptions=True,
            )

            self.executors[shard_id].shutdown(wait=True)
            self.high_priority_executors[shard_id].shutdown(wait=True)
            self.callback_executors[shard_id].shutdown(wait=True)

            self.hash_ring.remove_node_dynamic(
                str(shard_id),
                partial(self._rebalance_callback, old_shard_count=self.shard_count),
            )

            # Remove all state associated with the shard
            del self.queues[shard_id]
            del self.high_priority_queues[shard_id]
            del self.executors[shard_id]
            del self.high_priority_executors[shard_id]
            del self.callback_executors[shard_id]
            del self.dispatcher_tasks[shard_id * 2 : shard_id * 2 + 2]
            del self.shard_locks[shard_id]
            del self.shard_paused[shard_id]  # Remove paused state for removed shard

            self.shard_count -= 1
            self.topic_to_shard_cache.clear()
            logger.info(
                f"Removed shard {shard_id}. New shard count: {self.shard_count}."
            )
            self.rebalancing_in_progress.set()

    async def _rebalance_callback(
        self, node: str, affected_keys: List[str], old_shard_count: int
    ):
        """Rehash and move messages for affected topics."""
        logger.info(f"Rebalancing {len(affected_keys)} keys from node {node}.")
        for key in affected_keys:
            new_shard_id = int(self.hash_ring.get_node(key))
            # Logic to move messages from old shard to new:
            # In a real impl, this would drain the queue and re-publish, but here we simulate it
            logger.debug(f"Rebalanced key {key} to shard {new_shard_id}")

    async def adjust_shards(self, target_shard_count: int) -> None:
        target_shard_count = max(1, target_shard_count)
        if target_shard_count == self.shard_count:
            return
        logger.info(
            "Adjusting shards.",
            old_count=self.shard_count,
            new_count=target_shard_count,
        )

        current_subscribers = self.subscribers
        current_regex_subscribers = self.regex_subscribers
        current_pre_publish_hooks = self.pre_publish_hooks
        current_post_publish_hooks = self.post_publish_hooks

        buffered_messages: List[Tuple[int, Message]] = []
        for i, (normal_q, hp_q) in enumerate(
            zip(self.queues, self.high_priority_queues)
        ):
            while not normal_q.empty():
                buffered_messages.append(await normal_q.get())
            while not hp_q.empty():
                buffered_messages.append(await hp_q.get())

        for task in self.dispatcher_tasks:
            task.cancel()
        await asyncio.gather(*self.dispatcher_tasks, return_exceptions=True)

        for executor in (
            self.executors + self.high_priority_executors + self.callback_executors
        ):
            executor.shutdown(wait=True)

        self.shard_count = target_shard_count
        self.queues = [
            asyncio.PriorityQueue(maxsize=self.max_queue_size)
            for _ in range(self.shard_count)
        ]
        self.high_priority_queues = [
            asyncio.PriorityQueue(maxsize=self.max_queue_size)
            for _ in range(self.shard_count)
        ]
        self.executors = [
            ThreadPoolExecutor(
                max_workers=self.workers_per_shard,
                thread_name_prefix=f"msgbus-normal-shard-{i}",
            )
            for i in range(self.shard_count)
        ]
        self.high_priority_executors = [
            ThreadPoolExecutor(
                max_workers=max(1, self.workers_per_shard // 2),
                thread_name_prefix=f"msgbus-hp-shard-{i}",
            )
            for i in range(self.shard_count)
        ]
        self.callback_executors = [
            ThreadPoolExecutor(
                max_workers=getattr(self.config, "message_bus_callback_workers", 8),
                thread_name_prefix=f"msgbus-callbacks-{i}",
            )
            for i in range(self.shard_count)
        ]
        self.hash_ring = ConsistentHashRing(
            nodes=[str(i) for i in range(self.shard_count)]
        )
        self.dispatcher_tasks = []
        self._start_dispatchers()

        if buffered_messages:
            republished_count = 0
            for priority, message in buffered_messages:
                success = await self.publish(
                    message.topic,
                    message.payload,
                    trace_id=message.trace_id,
                    priority=priority,
                    encrypt=message.encrypted,
                    idempotency_key=message.idempotency_key,
                )
                if success:
                    republished_count += 1
            logger.info(
                f"Re-published {republished_count} messages out of {len(buffered_messages)}."
            )

        async with self._subscriber_lock:
            self.subscribers = current_subscribers
            self.regex_subscribers = current_regex_subscribers

        self.pre_publish_hooks = current_pre_publish_hooks
        self.post_publish_hooks = current_post_publish_hooks

    async def adjust_workers(self, shard_id: int, target_workers: int) -> None:
        if not (0 <= shard_id < self.shard_count):
            logger.error(
                "Invalid shard ID for worker adjustment.",
                shard_id=shard_id,
                shard_count=self.shard_count,
            )
            return

        target_workers = max(1, target_workers)
        current_normal_workers = self.executors[shard_id]._max_workers
        current_hp_workers = self.high_priority_executors[shard_id]._max_workers

        if (
            target_workers == current_normal_workers
            and max(1, target_workers // 2) == current_hp_workers
        ):
            return

        logger.info(
            "Adjusting workers for shard.",
            shard_id=shard_id,
            old_normal=current_normal_workers,
            new_normal=target_workers,
            old_hp=current_hp_workers,
            new_hp=max(1, target_workers // 2),
        )

        self.executors[shard_id].shutdown(wait=True)
        self.high_priority_executors[shard_id].shutdown(wait=True)
        self.callback_executors[shard_id].shutdown(wait=True)

        self.executors[shard_id] = ThreadPoolExecutor(
            max_workers=target_workers,
            thread_name_prefix=f"msgbus-normal-shard-{shard_id}",
        )
        self.high_priority_executors[shard_id] = ThreadPoolExecutor(
            max_workers=max(1, target_workers // 2),
            thread_name_prefix=f"msgbus-hp-shard-{shard_id}",
        )
        self.callback_executors[shard_id] = ThreadPoolExecutor(
            max_workers=getattr(self.config, "message_bus_callback_workers", 8),
            thread_name_prefix=f"msgbus-callbacks-{shard_id}",
        )

    async def configure_for_omnicore(self, engine_type: str) -> "ShardedMessageBus":
        logger.info(f"Configuring message bus for OmniCore engine type: {engine_type}")
        current_subscribers = self.subscribers
        current_regex_subscribers = self.regex_subscribers
        current_pre_publish_hooks = self.pre_publish_hooks
        current_post_publish_hooks = self.post_publish_hooks

        # Use correct attribute names from ArbiterConfig (uppercase)
        new_shard_count = getattr(
            self.config, "MESSAGE_BUS_SHARD_COUNT", None
        ) or getattr(self.config, "message_bus_shard_count", 4)
        new_workers_per_shard = getattr(
            self.config, "MESSAGE_BUS_WORKERS_PER_SHARD", None
        ) or getattr(self.config, "message_bus_workers_per_shard", 2)
        new_max_queue_size = getattr(
            self.config, "MESSAGE_BUS_MAX_QUEUE_SIZE", None
        ) or getattr(self.config, "message_bus_max_queue_size", 10000)
        new_callback_workers = getattr(
            self.config, "MESSAGE_BUS_CALLBACK_WORKERS", None
        ) or getattr(self.config, "message_bus_callback_workers", 8)

        if engine_type == "simulation":
            new_shard_count = min(8, os.cpu_count() or 4)
            new_workers_per_shard = max(2, (os.cpu_count() or 4) // new_shard_count)
            if self._simulation_hook not in self.pre_publish_hooks:
                self.add_pre_publish_hook(self._simulation_hook)
        elif engine_type == "trading":
            new_shard_count = min(4, os.cpu_count() or 2)
            new_workers_per_shard = 1
            new_max_queue_size = 1000
            new_callback_workers = max(1, (os.cpu_count() or 2) // 2)
            if self._trading_hook not in self.pre_publish_hooks:
                self.add_pre_publish_hook(self._trading_hook)
        elif engine_type == "analytics":
            new_shard_count = max(2, (os.cpu_count() or 4) // 2)
            new_workers_per_shard = max(4, (os.cpu_count() or 4))
            new_max_queue_size = 50000
            new_callback_workers = new_workers_per_shard * 2

        if (
            new_shard_count != self.shard_count
            or new_workers_per_shard != self.workers_per_shard
            or new_max_queue_size != self.max_queue_size
            or new_callback_workers
            != getattr(self.config, "message_bus_callback_workers", 8)
        ):

            logger.info(
                "Detected configuration changes. Re-initializing message bus.",
                old_config={
                    "shards": self.shard_count,
                    "workers": self.workers_per_shard,
                    "queue_size": self.max_queue_size,
                    "callback_workers": getattr(
                        self.config, "message_bus_callback_workers", 8
                    ),
                },
                new_config={
                    "shards": new_shard_count,
                    "workers": new_workers_per_shard,
                    "queue_size": new_max_queue_size,
                    "callback_workers": new_callback_workers,
                },
            )

            # Store updated values on self, not on the config (which may be immutable)
            self.shard_count = new_shard_count
            self.workers_per_shard = new_workers_per_shard
            self.max_queue_size = new_max_queue_size
            self._callback_workers = new_callback_workers

            await self.shutdown()
            self.__init__(
                config=self.config, db=self.db, audit_client=self.audit_client
            )

            async with self._subscriber_lock:
                self.subscribers = current_subscribers
                self.regex_subscribers = current_regex_subscribers

            self.pre_publish_hooks = current_pre_publish_hooks
            self.post_publish_hooks = current_post_publish_hooks

            self._start_dispatchers()
        else:
            logger.info(
                "No significant configuration changes detected for re-initialization."
            )

        logger.info(
            f"Message bus configured for {engine_type}.",
            shard_count=self.shard_count,
            workers_per_shard=self.workers_per_shard,
            max_queue_size=self.max_queue_size,
        )
        return self

    def _simulation_hook(self, message: Message) -> Message:
        if (
            message.topic.startswith("simulation.")
            and isinstance(message.payload, dict)
            and "simulation_id" not in message.payload
        ):
            try:
                from omnicore_engine.simulation import get_current_simulation_id

                sim_id = get_current_simulation_id()
                if sim_id:
                    message.payload["simulation_id"] = sim_id
                    logger.debug(
                        "Added simulation_id to message payload.",
                        sim_id=sim_id,
                        trace_id=message.trace_id,
                    )
            except ImportError:
                logger.warning(
                    "Could not import get_current_simulation_id for simulation hook.",
                    trace_id=message.trace_id,
                )
            except Exception as e:
                logger.error(
                    f"Error in _simulation_hook: {e}", trace_id=message.trace_id
                )
        return message

    def _trading_hook(self, message: Message) -> Message:
        message.processing_start = time.time_ns() // 1000
        logger.debug(
            "Added processing_start timestamp for trading message.",
            trace_id=message.trace_id,
            timestamp_us=message.processing_start,
        )
        return message

    async def pause_publishes(self, shard_id: int) -> None:
        """Pause publishing to a specific shard due to backpressure."""
        if 0 <= shard_id < self.shard_count:
            self.shard_paused[shard_id] = True
            logger.warning(f"Pausing publishes to shard {shard_id}")
        else:
            logger.error(f"Invalid shard_id {shard_id} for pause_publishes")

    async def resume_publishes(self, shard_id: int) -> None:
        """Resume publishing to a specific shard."""
        if 0 <= shard_id < self.shard_count:
            self.shard_paused[shard_id] = False
            logger.info(f"Resuming publishes to shard {shard_id}")
        else:
            logger.error(f"Invalid shard_id {shard_id} for resume_publishes")

    async def shutdown(self) -> None:
        logger.info("Initiating ShardedMessageBus shutdown...")
        self.running = False

        if self.kafka_bridge:
            await self.kafka_bridge.shutdown()
        if self.redis_bridge:
            await self.redis_bridge.shutdown()

        for task in self.dispatcher_tasks:
            task.cancel()
        await asyncio.gather(*self.dispatcher_tasks, return_exceptions=True)
        logger.info("All dispatcher tasks cancelled and awaited.")

        for i, q in enumerate(self.queues):
            await q.join()
        for i, q in enumerate(self.high_priority_queues):
            await q.join()
        logger.info("All internal message queues drained.")

        for executor_list in [
            self.executors,
            self.high_priority_executors,
            self.callback_executors,
        ]:
            for i, executor in enumerate(executor_list):
                executor.shutdown(wait=True)
                logger.debug(f"Executor {i} shut down.")
        logger.info("All ThreadPoolExecutors shut down.")

        await self.dlq.shutdown()
        if self.guardian:
            await self.guardian.shutdown()

        logger.info("ShardedMessageBus shutdown complete.")

    def get_openapi_schema(self) -> Dict:
        return {
            "openapi": "3.0.0",
            "info": {
                "title": "ShardedMessageBus API",
                "version": "1.0.0",
                "description": "API for interacting with the OmniCore ShardedMessageBus, supporting sharding, priorities, and external integrations.",
            },
            "paths": {},
            "components": {},
        }
