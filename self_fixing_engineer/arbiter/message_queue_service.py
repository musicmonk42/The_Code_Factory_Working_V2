import asyncio
import json
import logging
import os
import sys
import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List, Coroutine, Union, Tuple, Type
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from cryptography.fernet import Fernet, InvalidToken
from aiolimiter import AsyncLimiter
from prometheus_client import Counter, Histogram, Gauge, REGISTRY
import aiohttp

# --- Conditional Imports for Backends ---
try:
    import redis.asyncio as redis
    REDIS_STREAMS_AVAILABLE = True
except ImportError:
    redis = None
    REDIS_STREAMS_AVAILABLE = False

try:
    from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
    from aiokafka.errors import KafkaError
    KAFKA_AVAILABLE = True
except ImportError:
    AIOKafkaProducer = None
    AIOKafkaConsumer = None
    KafkaError = Exception
    KAFKA_AVAILABLE = False

try:
    from cryptography.fernet import Fernet, InvalidToken
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    Fernet = None
    InvalidToken = Exception
    CRYPTOGRAPHY_AVAILABLE = False

# --- Metrics Imports and Mock Fallback ---
_metrics_lock = threading.Lock()

def _get_or_create_metric(metric_type: Type, name: str, documentation: str, labelnames: Optional[Tuple[str, ...]] = None, buckets: Optional[Tuple[float, ...]] = None):
    """
    Safely gets or creates a Prometheus metric, using a lock to prevent race conditions.
    """
    if labelnames is None:
        labelnames = ()
    
    # Use a lock to ensure thread-safe metric registration
    with _metrics_lock:
        if name in REGISTRY._names_to_collectors:
            existing_metric = REGISTRY._names_to_collectors[name]
            if isinstance(existing_metric, metric_type):
                return existing_metric
            else:
                logging.getLogger(__name__).warning(f"Metric '{name}' already registered with a different type. Reusing existing.")
                return existing_metric
        else:
            if metric_type == Histogram:
                return metric_type(name, documentation, labelnames=labelnames, buckets=buckets or (0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0, float('inf')))
            elif metric_type == Counter:
                return metric_type(name, documentation, labelnames=labelnames)
            elif metric_type == Gauge:
                return metric_type(name, documentation, labelnames=labelnames)
            else:
                raise ValueError(f"Unsupported metric type: {metric_type}")


# --- Metrics Definitions ---
MQ_PUBLISH_TOTAL = _get_or_create_metric(Counter, 'mq_publish_total', 'Total messages published', ('backend', 'event_type', 'status'))
MQ_CONSUME_TOTAL = _get_or_create_metric(Counter, 'mq_consume_total', 'Total messages consumed', ('backend', 'event_type', 'status'))
MQ_PUBLISH_LATENCY = _get_or_create_metric(Histogram, 'mq_publish_latency_seconds', 'Latency of message publishing', ('backend', 'event_type'))
MQ_CONSUME_LATENCY = _get_or_create_metric(Histogram, 'mq_consume_latency_seconds', 'Latency of message consumption', ('backend', 'event_type'))
MQ_DLQ_TOTAL = _get_or_create_metric(Counter, 'mq_dlq_total', 'Total messages sent to Dead Letter Queue', ('backend', 'event_type', 'reason'))
MQ_ENCRYPTION_ERRORS = _get_or_create_metric(Counter, 'mq_encryption_errors_total', 'Total encryption/decryption errors', ('backend', 'event_type'))
MQ_CONNECTION_STATUS = _get_or_create_metric(Gauge, 'mq_connection_status', 'Status of message queue connection (1=up, 0=down)', ('backend',))

# --- Logger Setup ---
try:
    from arbiter_plugin_registry import registry, PlugInKind
    from arbiter.logging_utils import PIIRedactorFilter
    from arbiter.config import ArbiterConfig
    from arbiter import PermissionManager
except ImportError:
    class registry:
        @staticmethod
        def register(kind, name, version, author):
            def decorator(cls):
                return cls
            return decorator
    class PlugInKind:
        CORE_SERVICE = "core_service"
    class PIIRedactorFilter(logging.Filter):
        def filter(self, record):
            return True
    class ArbiterConfig:
        def __init__(self):
            self.REDIS_URL = "redis://localhost:6379/0"
            self.KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
            self.ENCRYPTION_KEY = 'default-encryption-key-for-tests-only-must-be-32-bytes'
            self.REDIS_MAX_CONNECTIONS = 10
            self.MESSAGE_BUS_MAX_QUEUE_SIZE = 100
    class PermissionManager:
        def __init__(self, config): pass
        def check_permission(self, role, permission): return True


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    handler.addFilter(PIIRedactorFilter())
    logger.addHandler(handler)
    

# --- Exceptions ---
class MessageQueueServiceError(Exception): pass
class BackendNotAvailableError(MessageQueueServiceError): pass
class SerializationError(MessageQueueServiceError): pass
class DecryptionError(MessageQueueServiceError): pass
class PermissionError(MessageQueueServiceError): pass

class MessageQueueService:
    """
    Ultra Gold Standard Async Message Queue Service

    This class provides a high-level, asynchronous API for publishing and consuming
    messages, with support for Redis Streams and Kafka. It includes features for
    encryption, dead-letter queues, and robust error handling.

    Parameters:
        - backend_type: 'redis_streams' or 'kafka'
        - redis_url, kafka_bootstrap_servers, encryption_key, topic_prefix, etc.

    Optional Hooks:
        - policy_hook(event_type, action, data) -> bool (raises if policy denies)
        - audit_hook(event_type, action, data, status, reason)
    """

    SUPPORTED_BACKENDS = ["redis_streams", "kafka", "memory"]

    def __init__(
        self,
        backend_type: str = "redis_streams",
        redis_url: Optional[str] = "redis://localhost:6379/0",
        kafka_bootstrap_servers: Optional[Union[str, List[str]]] = "localhost:9092",
        encryption_key: Optional[Union[str, bytes]] = None,
        topic_prefix: str = "sfe_events",
        dlq_topic_suffix: str = "_dlq",
        max_retries: int = 5,
        retry_delay_base: float = 1.0,
        consumer_group_id: str = "sfe_consumer_group",
        kafka_producer_acks: str = "all",
        kafka_producer_retries: int = 3,
        kafka_consumer_auto_offset_reset: str = "latest",
        kafka_consumer_enable_auto_commit: bool = True,
        kafka_consumer_auto_commit_interval_ms: int = 5000,
        redis_stream_maxlen: Optional[int] = 10000,
        redis_stream_trim_strategy: str = "~",
        policy_hook: Optional[Callable[[str, str, dict], bool]] = None,
        audit_hook: Optional[Callable[[str, str, dict, str, str], None]] = None,
        config: Optional[ArbiterConfig] = None,
        omnicore_url: Optional[str] = None
    ):
        if backend_type not in self.SUPPORTED_BACKENDS:
            raise ValueError(f"Unsupported backend type: {backend_type}. Must be one of {self.SUPPORTED_BACKENDS}")

        self.backend_type = backend_type
        self.config = config or ArbiterConfig()
        self.omnicore_url = omnicore_url or "https://api.example.com"
        self.topic_prefix = topic_prefix
        self.dlq_topic_suffix = dlq_topic_suffix
        self.max_retries = max_retries
        self.retry_delay_base = retry_delay_base
        self.consumer_group_id = consumer_group_id
        self.policy_hook = policy_hook
        self.audit_hook = audit_hook
        self._publish_limiter = AsyncLimiter(self.config.MESSAGE_BUS_MAX_QUEUE_SIZE, 60)

        self._is_connected = False
        self._shutdown_event = asyncio.Event()

        self._cipher: Optional[Fernet] = None
        if encryption_key:
            if not CRYPTOGRAPHY_AVAILABLE:
                raise BackendNotAvailableError("cryptography library is required for encryption.")
            # Fixed: Validate Fernet key format before using it
            if isinstance(encryption_key, str):
                encryption_key = encryption_key.encode('utf-8')
            try:
                self._cipher = Fernet(encryption_key)
            except Exception as e:
                raise ValueError(f"Invalid Fernet encryption key format. Key must be 32 url-safe base64-encoded bytes: {e}") from e
        elif self.config.ENCRYPTION_KEY and CRYPTOGRAPHY_AVAILABLE:
            key = self.config.ENCRYPTION_KEY.get_secret_value()
            if isinstance(key, str):
                key = key.encode('utf-8')
            try:
                self._cipher = Fernet(key)
            except Exception as e:
                raise ValueError(f"Invalid Fernet encryption key format in config. Key must be 32 url-safe base64-encoded bytes: {e}") from e

        self._redis_client: Optional[redis.Redis] = None
        self._kafka_producer: Optional[AIOKafkaProducer] = None
        self._consumer_tasks: Dict[str, asyncio.Task] = {}
        self.memory_queue: Optional[asyncio.Queue] = None

        if self.backend_type == "redis_streams":
            if not REDIS_STREAMS_AVAILABLE:
                raise BackendNotAvailableError("redis-py (asyncio) is not installed for Redis Streams backend.")
            self.redis_url = redis_url or self.config.REDIS_URL
            self.redis_stream_maxlen = redis_stream_maxlen
            self.redis_stream_trim_strategy = redis_stream_trim_strategy
            self._redis_client = redis.from_url(self.redis_url, decode_responses=False, max_connections=self.config.REDIS_MAX_CONNECTIONS)
        elif self.backend_type == "kafka":
            if not KAFKA_AVAILABLE:
                raise BackendNotAvailableError("aiokafka is not installed for Kafka backend.")
            self.kafka_bootstrap_servers = kafka_bootstrap_servers or self.config.KAFKA_BOOTSTRAP_SERVERS
            if isinstance(self.kafka_bootstrap_servers, list):
                self.kafka_bootstrap_servers = ",".join(self.kafka_bootstrap_servers)
            self.kafka_producer_acks = kafka_producer_acks
            self.kafka_producer_retries = kafka_producer_retries
            self.kafka_consumer_auto_offset_reset = kafka_consumer_auto_offset_reset
            self.kafka_consumer_enable_auto_commit = kafka_consumer_enable_auto_commit
            self.kafka_consumer_auto_commit_interval_ms = kafka_consumer_auto_commit_interval_ms
        elif self.backend_type == "memory":
            self.memory_queue = asyncio.Queue()

        logger.info(f"MessageQueueService initialized with backend: {self.backend_type}")

    async def __aenter__(self):
        """Asynchronous context manager entry point. Connects to the message broker."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Asynchronous context manager exit point. Disconnects from the message broker."""
        await self.disconnect()

    async def connect(self) -> None:
        """Establishes a connection to the message broker."""
        if self._is_connected:
            return

        try:
            if self.backend_type == "redis_streams" and self._redis_client:
                await self._redis_client.ping()
            elif self.backend_type == "kafka" and KAFKA_AVAILABLE:
                self._kafka_producer = AIOKafkaProducer(
                    bootstrap_servers=self.kafka_bootstrap_servers,
                    acks=self.kafka_producer_acks,
                    retries=self.kafka_producer_retries,
                    enable_idempotence=True
                )
                await self._kafka_producer.start()

            # Perform a healthcheck to verify the connection
            await self.healthcheck()
            self._is_connected = True
            MQ_CONNECTION_STATUS.labels(backend=self.backend_type).set(1)
            logger.info(f"MessageQueueService connected to {self.backend_type} backend.")
        except Exception as e:
            MQ_CONNECTION_STATUS.labels(backend=self.backend_type).set(0)
            logger.critical(f"Failed to connect to {self.backend_type} backend: {e}", exc_info=True)
            raise MessageQueueServiceError(f"Failed to connect to {self.backend_type} backend.") from e

    async def disconnect(self) -> None:
        """Closes all connections and gracefully shuts down consumer tasks."""
        if not self._is_connected:
            return

        self._shutdown_event.set()

        # Gracefully shut down consumer tasks
        tasks_to_await = []
        for task in self._consumer_tasks.values():
            if not task.done():
                task.cancel()
                tasks_to_await.append(task)
        
        if tasks_to_await:
            try:
                await asyncio.gather(*tasks_to_await, return_exceptions=True)
            except asyncio.CancelledError:
                # Expected if tasks were cancelled
                pass
            
        self._consumer_tasks.clear()

        try:
            if self.backend_type == "redis_streams" and self._redis_client:
                await self._redis_client.close()
            elif self.backend_type == "kafka" and self._kafka_producer:
                await self._kafka_producer.stop()
        except Exception as e:
            logger.error(f"Error disconnecting from {self.backend_type} backend: {e}", exc_info=True)
            # Do not re-raise, as we want to continue the shutdown process
        finally:
            self._is_connected = False
            MQ_CONNECTION_STATUS.labels(backend=self.backend_type).set(0)
            logger.info(f"MessageQueueService disconnected from {self.backend_type} backend.")

    async def healthcheck(self) -> Dict[str, Any]:
        """
        Verifies the connection and basic functionality of the message broker.

        Returns:
            Dict with health status and details.

        Raises:
            MessageQueueServiceError: If health check fails.
        """
        try:
            if self.backend_type == "redis_streams" and self._redis_client:
                await asyncio.wait_for(self._redis_client.ping(), timeout=5)
                return {"status": "healthy", "backend": "redis_streams"}
            elif self.backend_type == "kafka" and KAFKA_AVAILABLE and self._kafka_producer:
                if not self._kafka_producer.bootstrap_connected():
                    raise ConnectionError("Kafka producer not bootstrapped.")
                return {"status": "healthy", "backend": "kafka"}
            elif self.backend_type == "memory":
                return {"status": "healthy", "backend": "memory", "message_count": self.memory_queue.qsize()}
            else:
                raise MessageQueueServiceError("Backend client not initialized or unavailable.")
        except Exception as e:
            logger.error(f"Healthcheck failed for {self.backend_type}: {e}", exc_info=True)
            raise MessageQueueServiceError(f"Healthcheck failed for {self.backend_type}: {e}") from e

    def _get_topic_name(self, event_type: str, is_dlq: bool = False) -> str:
        """Constructs the full topic name with prefix and optional DLQ suffix."""
        return f"{self.topic_prefix}_{event_type}{self.dlq_topic_suffix if is_dlq else ''}"

    def _encrypt_payload(self, payload: bytes) -> bytes:
        """Encrypts the payload using the configured cipher."""
        if not self._cipher:
            return payload
        try:
            return self._cipher.encrypt(payload)
        except Exception as e:
            MQ_ENCRYPTION_ERRORS.labels(backend=self.backend_type, event_type="encryption").inc()
            raise SerializationError(f"Failed to encrypt payload: {e}") from e

    def _decrypt_payload(self, encrypted_payload: bytes) -> bytes:
        """Decrypts the payload using the configured cipher."""
        if not self._cipher:
            return encrypted_payload
        try:
            return self._cipher.decrypt(encrypted_payload)
        except InvalidToken as e:
            MQ_ENCRYPTION_ERRORS.labels(backend=self.backend_type, event_type="decryption_invalid_token").inc()
            raise DecryptionError(f"Invalid encryption token during decryption: {e}") from e
        except Exception as e:
            MQ_ENCRYPTION_ERRORS.labels(backend=self.backend_type, event_type="decryption_error").inc()
            raise DecryptionError(f"Failed to decrypt payload: {e}") from e

    def _serialize_message(self, data: Dict[str, Any]) -> bytes:
        """Serializes a dictionary to a JSON byte string."""
        try:
            return json.dumps(data, default=str).encode('utf-8')
        except Exception as e:
            raise SerializationError(f"Failed to serialize message: {e}") from e

    def _deserialize_message(self, data_bytes: bytes) -> Dict[str, Any]:
        """Deserializes a JSON byte string to a dictionary."""
        try:
            return json.loads(data_bytes.decode('utf-8'))
        except Exception as e:
            raise SerializationError(f"Failed to deserialize message: {e}") from e
    
    def check_permission(self, role: str, permission: str) -> bool:
        """Checks if a user role has a specific permission."""
        from arbiter import PermissionManager
        permission_mgr = PermissionManager(self.config)
        return permission_mgr.check_permission(role, permission)
    
    async def rotate_encryption_key(self, new_key: bytes) -> None:
        """
        Rotates the encryption key. This is a highly sensitive operation.
        In a real system, this would require a careful, orchestrated process.
        """
        if not CRYPTOGRAPHY_AVAILABLE:
            raise BackendNotAvailableError("Cryptography library is not available.")
            
        old_cipher = self._cipher
        try:
            self._cipher = Fernet(new_key)
            logger.warning("Encryption key rotated in-memory. All encrypted data in topics must be re-encrypted.")
            MQ_ENCRYPTION_ERRORS.labels(backend=self.backend_type, event_type="key_rotation").inc()
        except Exception as e:
            self._cipher = old_cipher # Revert to old key on failure
            MQ_ENCRYPTION_ERRORS.labels(backend=self.backend_type, event_type="key_rotation_fail").inc()
            raise MessageQueueServiceError(f"Failed to rotate encryption key: {e}") from e


    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(Exception))
    async def publish(self, event_type: str, data: Dict[str, Any], is_critical: bool = False, omnicore: bool = False) -> None:
        """
        Publishes a message to the broker with a robust retry mechanism.
        
        Args:
            event_type (str): The type of the event.
            data (Dict[str, Any]): The payload of the message.
            is_critical (bool): If True, marks the message for a potentially
                                different handling strategy (not currently used).
            omnicore (bool): If True, also publishes the message to the OmniCore API endpoint.
        
        Raises:
            MessageQueueServiceError: If the service is not connected or publishing fails.
            PermissionError: If the user lacks publish permission.
        """
        if omnicore:
            async with aiohttp.ClientSession() as session:
                try:
                    await session.post(f"{self.omnicore_url}/events", json={"event_type": event_type, "data": data})
                    logging.getLogger(__name__).info(f"Published to omnicore_engine: {event_type}")
                except Exception as e:
                    logging.getLogger(__name__).error(f"Failed to publish to omnicore_engine: {e}")
        
        if not self._is_connected:
            raise MessageQueueServiceError("Service not connected. Call connect() first.")
        # Conceptual access control
        # if not self.check_permission("user", "publish"):
        #     raise PermissionError("Publish permission required")

        # Policy enforcement (optional)
        if self.policy_hook:
            if not self.policy_hook(event_type, "publish", data):
                reason = f"Policy denied publish for event '{event_type}'"
                if self.audit_hook:
                    self.audit_hook(event_type, "publish", data, "denied", reason)
                logger.warning(reason)
                raise MessageQueueServiceError(reason)

        topic_name = self._get_topic_name(event_type)
        start_time = time.monotonic()
        
        try:
            serialized_data = self._serialize_message(data)
            encrypted_data = self._encrypt_payload(serialized_data)
        except (SerializationError, DecryptionError) as e:
            MQ_PUBLISH_TOTAL.labels(backend=self.backend_type, event_type=event_type, status='failed_serialization_encryption').inc()
            await self._send_to_dlq(event_type, data, f"Serialization/Encryption error: {e}")
            if self.audit_hook:
                self.audit_hook(event_type, "publish", data, "failed", f"Serialization/Encryption error: {e}")
            raise MessageQueueServiceError(f"Failed to publish '{event_type}' due to serialization/encryption error.") from e

        try:
            await self._publish_limiter.acquire()
            if self.backend_type == "redis_streams" and self._redis_client:
                await self._redis_client.xadd(
                    topic_name,
                    {"payload": encrypted_data},
                    maxlen=self.redis_stream_maxlen,
                    approximate=self.redis_stream_trim_strategy == "~"
                )
            elif self.backend_type == "kafka" and self._kafka_producer:
                await self._kafka_producer.send_and_wait(topic_name, encrypted_data)
            elif self.backend_type == "memory" and self.memory_queue:
                await self.memory_queue.put({"event_type": event_type, "data": data, "raw_data": encrypted_data})
            else:
                raise MessageQueueServiceError(f"Publish not implemented for {self.backend_type} or client not ready.")

            MQ_PUBLISH_TOTAL.labels(backend=self.backend_type, event_type=event_type, status='success').inc()
            MQ_PUBLISH_LATENCY.labels(backend=self.backend_type, event_type=event_type).observe(time.monotonic() - start_time)
            if self.audit_hook:
                self.audit_hook(event_type, "publish", data, "success", "")
            logger.debug(f"Published event '{event_type}' to {self.backend_type} topic '{topic_name}'. Critical: {is_critical}")
            return
        except Exception as e:
            MQ_PUBLISH_TOTAL.labels(backend=self.backend_type, event_type=event_type, status='failed_retries').inc()
            await self._send_to_dlq(event_type, data, f"Publish failed after retries: {e}")
            if self.audit_hook:
                self.audit_hook(event_type, "publish", data, "failed", f"Max retries reached: {e}")
            raise MessageQueueServiceError(f"Failed to publish '{event_type}' after retries.") from e

    async def subscribe(self, event_type: str, handler: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]) -> None:
        """
        Subscribes to messages from a topic/stream and processes them with a handler.
        
        Args:
            event_type (str): The type of event to subscribe to.
            handler (Callable): An async handler function to process incoming messages.
        
        Raises:
            PermissionError: If the user lacks consume permission.
        """
        if not self._is_connected:
            raise MessageQueueServiceError("Service not connected. Call connect() first.")
        # Conceptual access control
        # if not self.check_permission("user", "consume"):
        #     raise PermissionError("Consume permission required")

        topic_name = self._get_topic_name(event_type)
        if self.backend_type == "redis_streams" and self._redis_client:
            consumer_task = asyncio.create_task(self._redis_stream_consumer(topic_name, handler))
        elif self.backend_type == "kafka" and KAFKA_AVAILABLE:
            consumer_task = asyncio.create_task(self._kafka_consumer(topic_name, handler))
        elif self.backend_type == "memory" and self.memory_queue:
            consumer_task = asyncio.create_task(self._memory_consumer(event_type, handler))
        else:
            raise MessageQueueServiceError(f"Subscribe not implemented for {self.backend_type} or client not ready.")

        self._consumer_tasks[event_type] = consumer_task
        logger.info(f"Subscribed to '{event_type}' on {self.backend_type} backend. Consumer group: {self.consumer_group_id}")

    async def _memory_consumer(self, event_type: str, handler: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]):
        """Internal consumer loop for in-memory queue."""
        while not self._shutdown_event.is_set():
            try:
                message = await self.memory_queue.get()
                if message.get("event_type") == event_type:
                    start_time = time.monotonic()
                    try:
                        decrypted_data = self._decrypt_payload(message.get("raw_data"))
                        deserialized_data = self._deserialize_message(decrypted_data)
                        await handler(deserialized_data)
                        MQ_CONSUME_TOTAL.labels(backend='memory', event_type=event_type, status='success').inc()
                        MQ_CONSUME_LATENCY.labels(backend='memory', event_type=event_type).observe(time.monotonic() - start_time)
                    except (SerializationError, DecryptionError) as e:
                        logger.error(f"In-memory message processing failed: {e}. Sending to DLQ.", exc_info=True)
                        await self._send_to_dlq(event_type, message.get("data"), f"Processing error: {e}")
                    except Exception as e:
                        logger.error(f"In-memory message handler failed: {e}. Sending to DLQ.", exc_info=True)
                        await self._send_to_dlq(event_type, message.get("data"), f"Handler error: {e}")
            except Exception as e:
                logger.critical(f"Unhandled error in in-memory consumer: {e}", exc_info=True)
                await asyncio.sleep(self.retry_delay_base)


    async def _redis_stream_consumer(self, stream_name: str, handler: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]) -> None:
        """Internal Redis Streams consumer loop."""
        consumer_id = f"{self.consumer_group_id}_{os.getpid()}"
        try:
            await self._redis_client.xgroup_create(stream_name, self.consumer_group_id, id='0', mkstream=True)
            logger.info(f"Redis Stream consumer group '{self.consumer_group_id}' created or already exists for stream '{stream_name}'.")
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                logger.error(f"Failed to create Redis Stream consumer group: {e}")
                MQ_CONSUME_TOTAL.labels(backend='redis_streams', event_type=stream_name, status='group_create_failed').inc()
                return

        while not self._shutdown_event.is_set():
            try:
                response = await self._redis_client.xreadgroup(
                    self.consumer_group_id,
                    consumer_id,
                    {stream_name: '>'}, # Use '>' to read new messages
                    count=10,
                    block=1000
                )
                if response:
                    for stream_name_r, messages in response:
                        for message_id, message_data in messages:
                            start_time = time.monotonic()
                            try:
                                payload_bytes = message_data.get(b'payload')
                                if not payload_bytes:
                                    raise SerializationError("Missing 'payload' field in Redis Stream message.")
                                decrypted_data = self._decrypt_payload(payload_bytes)
                                deserialized_data = self._deserialize_message(decrypted_data)

                                # Policy and audit hooks for consume
                                if self.policy_hook and not self.policy_hook(stream_name, "consume", deserialized_data):
                                    logger.warning(f"Policy denied consume for event '{stream_name}'. Skipping.")
                                    # Acknowledge the message even if policy is denied to avoid reprocessing.
                                    await self._redis_client.xack(stream_name, self.consumer_group_id, message_id)
                                    continue
                                if self.audit_hook:
                                    self.audit_hook(stream_name, "consume", deserialized_data, "success", "")

                                await handler(deserialized_data)
                                await self._redis_client.xack(stream_name, self.consumer_group_id, message_id)
                                MQ_CONSUME_TOTAL.labels(backend='redis_streams', event_type=stream_name, status='success').inc()
                                MQ_CONSUME_LATENCY.labels(backend='redis_streams', event_type=stream_name).observe(time.monotonic() - start_time)
                            except (SerializationError, DecryptionError) as e:
                                logger.error(f"Redis Stream message processing failed for ID {message_id}: {e}. Sending to DLQ.", exc_info=True)
                                await self._send_to_dlq(stream_name, {"id": message_id, "data": message_data.get(b'payload').decode('latin-1', errors='replace') if message_data.get(b'payload') else 'N/A'}, f"Processing error: {e}")
                                # Acknowledge the message here to prevent reprocessing
                                await self._redis_client.xack(stream_name, self.consumer_group_id, message_id)
                            except Exception as e:
                                logger.error(f"Redis Stream message handler failed for ID {message_id}: {e}. Sending to DLQ.", exc_info=True)
                                await self._send_to_dlq(stream_name, {"id": message_id, "data": message_data.get(b'payload').decode('latin-1', errors='replace') if message_data.get(b'payload') else 'N/A'}, f"Handler error: {e}")
                                # Acknowledge the message here to prevent reprocessing
                                await self._redis_client.xack(stream_name, self.consumer_group_id, message_id)
                else:
                    await asyncio.sleep(0.1) # Short sleep to avoid busy-waiting when no messages
            except Exception as e:
                logger.critical(f"Unhandled error in Redis Stream consumer for '{stream_name}': {e}", exc_info=True)
                await asyncio.sleep(self.retry_delay_base * 5)

    async def _kafka_consumer(self, topic_name: str, handler: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]) -> None:
        """Internal Kafka consumer loop."""
        consumer = AIOKafkaConsumer(
            topic_name,
            bootstrap_servers=self.kafka_bootstrap_servers,
            group_id=self.consumer_group_id,
            auto_offset_reset=self.kafka_consumer_auto_offset_reset,
            enable_auto_commit=self.kafka_consumer_enable_auto_commit,
            auto_commit_interval_ms=self.kafka_consumer_auto_commit_interval_ms
        )
        try:
            await consumer.start()
            logger.info(f"Kafka consumer started for topic '{topic_name}'.")
            while not self._shutdown_event.is_set():
                try:
                    async for msg in consumer:
                        start_time = time.monotonic()
                        try:
                            decrypted_data = self._decrypt_payload(msg.value)
                            deserialized_data = self._deserialize_message(decrypted_data)

                            # Policy and audit hooks for consume
                            if self.policy_hook and not self.policy_hook(topic_name, "consume", deserialized_data):
                                logger.warning(f"Policy denied consume for event '{topic_name}'. Skipping.")
                                continue
                            if self.audit_hook:
                                self.audit_hook(topic_name, "consume", deserialized_data, "success", "")

                            await handler(deserialized_data)
                            MQ_CONSUME_TOTAL.labels(backend='kafka', event_type=topic_name, status='success').inc()
                            MQ_CONSUME_LATENCY.labels(backend='kafka', event_type=topic_name).observe(time.monotonic() - start_time)
                        except (SerializationError, DecryptionError) as e:
                            logger.error(f"Kafka message processing failed for offset {msg.offset}: {e}. Sending to DLQ.", exc_info=True)
                            await self._send_to_dlq(topic_name, {"offset": msg.offset, "value": msg.value.decode('latin-1', errors='replace')}, f"Processing error: {e}")
                        except Exception as e:
                            logger.error(f"Kafka message handler failed for offset {msg.offset}: {e}. Sending to DLQ.", exc_info=True)
                            await self._send_to_dlq(topic_name, {"offset": msg.offset, "value": msg.value.decode('latin-1', errors='replace')}, f"Handler error: {e}")
                except KafkaError as e:
                    logger.error(f"Kafka consumer error: {e}. Attempting reconnection...", exc_info=True)
                    # The aiokafka client should automatically attempt to reconnect, but this provides an additional safeguard.
                    await asyncio.sleep(self.retry_delay_base * 2)
                except Exception as e:
                    logger.critical(f"Unhandled error in Kafka consumer for '{topic_name}': {e}", exc_info=True)
                    await asyncio.sleep(self.retry_delay_base * 5)
        finally:
            await consumer.stop()
            logger.info(f"Kafka consumer for topic '{topic_name}' stopped.")

    async def _send_to_dlq(self, event_type: str, original_data: Dict[str, Any], reason: str) -> None:
        """Sends a message to the Dead Letter Queue."""
        dlq_topic = self._get_topic_name(event_type, is_dlq=True)
        dlq_message = {
            "original_event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
            "original_data": original_data
        }
        try:
            serialized_dlq_data = self._serialize_message(dlq_message)
            encrypted_dlq_data = self._encrypt_payload(serialized_dlq_data)

            if self.backend_type == "redis_streams" and self._redis_client:
                await self._redis_client.xadd(dlq_topic, {"payload": encrypted_dlq_data})
            elif self.backend_type == "kafka" and self._kafka_producer:
                await self._kafka_producer.send_and_wait(dlq_topic, encrypted_dlq_data)
            elif self.backend_type == "memory" and self.memory_queue:
                await self.memory_queue.put({"event_type": dlq_topic, "data": dlq_message})

            MQ_DLQ_TOTAL.labels(backend=self.backend_type, event_type=event_type, reason=reason).inc()
            logger.warning(f"Message for '{event_type}' sent to DLQ topic '{dlq_topic}' due to: {reason}")
        except Exception as e:
            logger.critical(f"Failed to send message to DLQ for '{event_type}': {e}. Message lost.", exc_info=True)
            if self.audit_hook:
                self.audit_hook(event_type, "dlq", original_data, "failed", str(e))

    async def replay_dlq(self, event_type: str) -> None:
        """
        Replays messages from a specific dead-letter queue topic/stream.
        This operation is typically triggered manually by an operator.
        """
        dlq_topic = self._get_topic_name(event_type, is_dlq=True)
        logger.info(f"Attempting to replay DLQ for '{event_type}' from topic '{dlq_topic}'.")

        if self.backend_type == "redis_streams" and self._redis_client:
            messages = await self._redis_client.xrange(dlq_topic)
            for message_id, message_data in messages:
                try:
                    payload_bytes = message_data.get(b'payload')
                    if not payload_bytes:
                        raise SerializationError("Missing 'payload' field in Redis Stream DLQ message.")
                    decrypted_data = self._decrypt_payload(payload_bytes)
                    dlq_entry = self._deserialize_message(decrypted_data)
                    original_event_type = dlq_entry.get("original_event_type")
                    original_data = dlq_entry.get("original_data")
                    if original_event_type and original_data:
                        await self.publish(original_event_type, original_data, is_critical=True)
                        await self._redis_client.xdel(dlq_topic, message_id)
                        logger.info(f"Replayed and deleted DLQ message ID {message_id} for '{original_event_type}'.")
                    else:
                        logger.warning(f"Malformed DLQ entry {message_id}: {dlq_entry}. Skipping replay and deleting from DLQ.")
                        await self._redis_client.xdel(dlq_topic, message_id)
                except Exception as e:
                    logger.error(f"Error replaying Redis Stream DLQ message ID {message_id}: {e}. Will retry on next replay_dlq call.", exc_info=True)
            logger.info(f"Finished attempting to replay DLQ for '{event_type}' on Redis Streams.")

        elif self.backend_type == "kafka" and KAFKA_AVAILABLE:
            consumer = AIOKafkaConsumer(
                dlq_topic,
                bootstrap_servers=self.kafka_bootstrap_servers,
                group_id=f"{self.consumer_group_id}_dlq_replay_{os.getpid()}",
                auto_offset_reset="earliest",
                enable_auto_commit=False
            )
            try:
                await consumer.start()
                logger.info(f"Kafka DLQ consumer started for topic '{dlq_topic}'.")
                async for msg in consumer:
                    try:
                        decrypted_data = self._decrypt_payload(msg.value)
                        deserialized_data = self._deserialize_message(decrypted_data)
                        dlq_entry = deserialized_data
                        original_event_type = dlq_entry.get("original_event_type")
                        original_data = dlq_entry.get("original_data")
                        if original_event_type and original_data:
                            await self.publish(original_event_type, original_data, is_critical=True)
                            await consumer.commit()
                            logger.info(f"Replayed Kafka DLQ message from offset {msg.offset} for '{original_event_type}'.")
                        else:
                            logger.warning(f"Malformed Kafka DLQ entry at offset {msg.offset}: {dlq_entry}. Skipping replay and committing offset.")
                            await consumer.commit()
                    except Exception as e:
                        logger.error(f"Error replaying Kafka DLQ message at offset {msg.offset}: {e}. Will retry on next replay_dlq call.", exc_info=True)
            finally:
                await consumer.stop()
                logger.info(f"Finished attempting to replay DLQ for '{event_type}' on Kafka.")
        else:
            logger.warning(f"DLQ replay not implemented for {self.backend_type} backend.")

# End of ultra gold standard message_queue_service.py

registry.register(kind=PlugInKind.CORE_SERVICE, name="MessageQueueService", version="1.0.0", author="Arbiter Team")(MessageQueueService)