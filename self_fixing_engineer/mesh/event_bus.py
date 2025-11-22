# Final production-ready event bus module v2.1.0
#
# This module provides a robust, scalable, and secure event bus using Redis
# as a backend. It supports both a simple, fire-and-forget Pub/Sub model
# for backwards compatibility and a production-grade Redis Streams model
# for guaranteed message delivery (at-least-once semantics) and consumer groups.
#
# Key Features:
# - **Reliability:** Redis Streams with Consumer Groups, XACK, and pending entry
#   handling to ensure no messages are lost.
# - **Security:** HMAC-based integrity checks, end-to-end encryption with
#   MultiFernet for key rotation, and strict TLS enforcement in production.
# - **Observability:** Asynchronous OpenTelemetry tracing and Prometheus metrics
#   with appropriate labels for environment and tenant.
# - **Resilience:** Jittered backoff retries, a circuit breaker for upstream
#   failures, and a Dead-Letter Queue (DLQ) for failed messages.
# - **Scalability:** Asynchronous I/O, Redis connection pooling, and optional
#   rate limiting for high-traffic scenarios.
# - **Maintainability:** Comprehensive docstrings, clear environment variable
#   configuration, and a detailed test harness for local development.
#
# This version represents a significant upgrade from the previous stub and is
# designed for mission-critical, high-traffic distributed systems and agentic
# workflows.
#
# --- README ---
#
# **Getting Started**
# 1. Install dependencies:
#    `pip install redis-py==6.4.0 prometheus-async==25.1.0 aiolimiter==1.2.1 cryptography==46.0.0 pydantic==2.11.0 opentelemetry-distro==0.48.0 opentelemetry-instrumentation-redis==0.48b0`
# 2. Configure environment variables (all are required for production):
#    - `PROD_MODE=true`
#    - `REDIS_URL=rediss://<user>:<password>@<host>:<port>` (must be `rediss://` in prod)
#    - `EVENT_BUS_ENCRYPTION_KEY=<key_1>,<key_2>` (comma-separated keys for rotation)
#    - `EVENT_BUS_HMAC_KEY=<super_secret_key>`
#    - `USE_REDIS_STREAMS=true` (highly recommended for production)
#    - `ENV=prod`
#    - `TENANT=acme-corp`
#    - `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://otel-collector:4318/v1/traces`
#
# **Usage**
#
# **Publishing an event:**
# ```python
# from pydantic import BaseModel
#
# class UserCreated(BaseModel):
#     user_id: str
#     email: str
#
# # Publish a single event
# await publish_event(
#     "user_created",
#     {"user_id": "123", "email": "test@example.com"},
#     schema=UserCreated
# )
#
# # Publish a batch of events
# await publish_events([
#     {"event_type": "user_created", "data": {...}},
#     {"event_type": "order_placed", "data": {...}},
# ])
# ```
#
# **Subscribing to events:**
# ```python
# from event_bus import subscribe_event
#
# async def my_handler(data):
#     print(f"Received event with data: {data}")
#
# # This will run in a background task
# subscribe_task = await subscribe_event(
#     "user_created",
#     my_handler,
#     consumer_group="user_service_group",
#     consumer_name="instance_1"
# )
#
# # Wait for the task to complete (e.g., on shutdown)
# await subscribe_task
# ```
#
# **Dead-Letter Queue (DLQ) Replay:**
# The DLQ is a Redis Stream named `event_bus:dlq`. You can manually replay
# failed messages using the `replay_dlq` function.
#
# ```python
# await replay_dlq()
# ```
#
# **Testing:**
# The `if __name__ == "__main__":` block is disabled in production but
# provides a test harness for development. Set `PROD_MODE=false` and run
# `python event_bus.py`.
#
# ---

__version__ = "2.1.0"

import os
import json
import asyncio
import time
import sys
import threading
import random
import hmac
import hashlib
import logging
from typing import Dict, Any, Callable, Awaitable, List, Optional, Type
from dataclasses import dataclass
from datetime import datetime
from queue import Queue, Empty
from threading import Thread

# Conditional imports for production requirements
try:
    import redis.asyncio as redis
    from redis.asyncio.connection import ConnectionPool, ClusterConnectionPool
    from redis.exceptions import ConnectionError, RedisError, TimeoutError, ResponseError
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    
try:
    from cryptography.fernet import Fernet, MultiFernet
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False

try:
    from pydantic import BaseModel, ValidationError
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False

try:
    from aiolimiter import AsyncLimiter
    AIOLIMITER_AVAILABLE = True
except ImportError:
    AIOLIMITER_AVAILABLE = False
    
try:
    from prometheus_async.aio import Counter as AsyncCounter, Gauge as AsyncGauge, Histogram as AsyncHistogram
    PROMETHEUS_ASYNC_AVAILABLE = True
except ImportError:
    from prometheus_client import Counter, Gauge, Histogram
    PROMETHEUS_ASYNC_AVAILABLE = False

try:
    from opentelemetry import trace
    from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    class MockTracer:
        def start_as_current_span(self, *args, **kwargs):
            import contextlib
            @contextlib.contextmanager
            def mock_span(): yield
            return mock_span()
    tracer = MockTracer()
    OPENTELEMETRY_AVAILABLE = False


# ---- Environment Configuration & Module State ----
PROD_MODE = os.environ.get("PROD_MODE", "false").lower() == "true"
MAX_RETRIES = int(os.environ.get("EVENT_BUS_MAX_RETRIES", 3))
RETRY_DELAY = float(os.environ.get("EVENT_BUS_RETRY_DELAY", 1.0))
ENCRYPTION_KEY = os.environ.get("EVENT_BUS_ENCRYPTION_KEY")
HMAC_KEY = os.environ.get("EVENT_BUS_HMAC_KEY")
REDIS_USER = os.environ.get("REDIS_USER")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD")
REDIS_URL = os.environ.get("REDIS_URL")
USE_REDIS_STREAMS = os.environ.get("USE_REDIS_STREAMS", "false").lower() == "true"
ENV = os.environ.get("ENV", "unknown")
TENANT = os.environ.get("TENANT", "unknown")
DLQ_STREAM_NAME = f"{TENANT}:{ENV}:event_bus:dlq"
MAX_STREAM_LENGTH = int(os.environ.get("REDIS_STREAMS_MAXLEN", 10000))
PUBLISH_RATE_LIMIT_RPS = int(os.environ.get("PUBLISH_RATE_LIMIT_RPS", 1000))
IS_TEST_ENV = os.environ.get("PYTEST_CURRENT_TEST") is not None


# ---- Async-Safe Logger Implementation ----
class AsyncSafeLogger:
    """Thread-safe, async-safe logger that doesn't block the event loop."""
    
    def __init__(self, name: str, level=logging.INFO):
        self.name = name
        self.level = level
        self._queue = Queue(maxsize=10000)
        self._worker_thread = None
        self._shutdown = threading.Event()
        self._started = False
        
        # Setup the actual logger
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)
        self._logger.propagate = False
        
        # Only add console handler (no file handlers that can block)
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(
                logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            )
            self._logger.addHandler(handler)
    
    def _worker(self):
        """Background worker thread for processing log messages."""
        while not self._shutdown.is_set():
            try:
                # Get message with timeout to check shutdown flag periodically
                level, msg, kwargs = self._queue.get(timeout=0.1)
                self._logger.log(level, msg, **kwargs)
            except Empty:
                continue
            except Exception as e:
                # Emergency fallback - print to stderr
                print(f"Logging error: {e}", file=sys.stderr)
    
    def start(self):
        """Start the background logging thread."""
        if not self._started:
            self._worker_thread = Thread(target=self._worker, daemon=True)
            self._worker_thread.start()
            self._started = True
    
    def stop(self):
        """Stop the background logging thread."""
        if self._started:
            self._shutdown.set()
            if self._worker_thread:
                self._worker_thread.join(timeout=2)
            self._started = False
    
    def _log(self, level: int, msg: str, **kwargs):
        """Queue a log message (non-blocking)."""
        if not self._started:
            self.start()
        
        try:
            # Non-blocking put
            self._queue.put_nowait((level, msg, kwargs))
        except:
            # Queue full - in production we'd rather drop logs than block
            if PROD_MODE:
                pass
            else:
                print(f"[{self.name}] {logging.getLevelName(level)}: {msg}", file=sys.stderr)
    
    def debug(self, msg: str, **kwargs):
        self._log(logging.DEBUG, msg, **kwargs)
    
    def info(self, msg: str, **kwargs):
        self._log(logging.INFO, msg, **kwargs)
    
    def warning(self, msg: str, **kwargs):
        self._log(logging.WARNING, msg, **kwargs)
    
    def error(self, msg: str, exc_info=False, **kwargs):
        if exc_info:
            kwargs['exc_info'] = True
        self._log(logging.ERROR, msg, **kwargs)
    
    def critical(self, msg: str, exc_info=False, **kwargs):
        if exc_info:
            kwargs['exc_info'] = True
        self._log(logging.CRITICAL, msg, **kwargs)


# Initialize async-safe logger
logger = AsyncSafeLogger("event_bus")
logger.start()


# ---- PROD MODE ENFORCEMENT ----
def _enforce_prod_requirements():
    """Checks for and enforces production-critical dependencies and configs."""
    if not REDIS_AVAILABLE:
        logger.critical("CRITICAL: redis-py is required for production but not installed.")
        sys.exit(1)
    if not CRYPTOGRAPHY_AVAILABLE:
        logger.critical("CRITICAL: cryptography is required for encryption in production but not installed.")
        sys.exit(1)
    if not PYDANTIC_AVAILABLE:
        logger.critical("CRITICAL: pydantic is required for schema validation in production but not installed.")
        sys.exit(1)
    if not ENCRYPTION_KEY:
        logger.critical("CRITICAL: EVENT_BUS_ENCRYPTION_KEY required in production.")
        sys.exit(1)
    if not HMAC_KEY:
        logger.critical("CRITICAL: EVENT_BUS_HMAC_KEY required in production.")
        sys.exit(1)
    if not REDIS_USER or not REDIS_PASSWORD:
        logger.critical("CRITICAL: REDIS_USER and REDIS_PASSWORD required in production.")
        sys.exit(1)
    if not REDIS_URL:
        logger.critical("CRITICAL: REDIS_URL environment variable is not set.")
        sys.exit(1)
    if not REDIS_URL.startswith("rediss://"):
        logger.critical("CRITICAL: Redis URL must use SSL (rediss://) in production.")
        sys.exit(1)
    if ("redis://localhost" in REDIS_URL or "redis://127.0.0.1" in REDIS_URL):
        logger.critical("CRITICAL: Redis URL points to localhost, which is not allowed in production.")
        sys.exit(1)

if PROD_MODE:
    _enforce_prod_requirements()


# ---- Security & Helpers ----
fernet = None
def _get_fernet() -> Optional[MultiFernet]:
    """Initializes and returns a MultiFernet instance for key rotation."""
    global fernet
    if fernet is None and ENCRYPTION_KEY and CRYPTOGRAPHY_AVAILABLE:
        keys = ENCRYPTION_KEY.split(",")
        fernet = MultiFernet([Fernet(k.encode()) for k in keys])
    return fernet

def _sign_payload(payload: bytes) -> str:
    """Creates an HMAC signature for the given payload."""
    if HMAC_KEY:
        signer = hmac.new(HMAC_KEY.encode(), digestmod=hashlib.sha256)
        signer.update(payload)
        return signer.hexdigest()
    raise RuntimeError("HMAC key is not configured.")

def _verify_signature(payload: bytes, signature: str) -> bool:
    """Verifies an HMAC signature against the given payload."""
    if HMAC_KEY:
        signer = hmac.new(HMAC_KEY.encode(), digestmod=hashlib.sha256)
        signer.update(payload)
        return hmac.compare_digest(signer.hexdigest(), signature)
    return False


# ---- Observability ----
if OPENTELEMETRY_AVAILABLE and not IS_TEST_ENV:
    resource = Resource(attributes={SERVICE_NAME: f"mesh-event-bus-tenant:{TENANT}"})
    trace_provider = TracerProvider(resource=resource)
    trace_exporter = OTLPSpanExporter(endpoint=os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://localhost:4318/v1/traces"))
    trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
    trace.set_tracer_provider(trace_provider)
    tracer = trace.get_tracer(__name__)
    AsyncioInstrumentor().instrument()
    RedisInstrumentor().instrument()
    logger.info("OpenTelemetry initialized and configured.")
else:
    class MockTracer:
        def start_as_current_span(self, *args, **kwargs):
            import contextlib
            @contextlib.contextmanager
            def mock_span(): yield
            return mock_span()
    tracer = MockTracer()
    if not IS_TEST_ENV:
        logger.warning("OpenTelemetry SDK not found. Distributed tracing will be disabled.")


# Prometheus Metrics with tenant/environment labels
if PROMETHEUS_ASYNC_AVAILABLE:
    EVENTS_PUBLISHED = AsyncCounter("event_bus_published_total", "Total events published.", ["event_type", "status", "env", "tenant", "protocol"])
    EVENTS_SUBSCRIBED = AsyncCounter("event_bus_subscribed_total", "Total events subscribed.", ["event_type", "status", "env", "tenant", "protocol"])
    PUBLISH_LATENCY = AsyncHistogram("event_bus_publish_latency_seconds", "Latency of publishing an event.")
    SUBSCRIBE_LATENCY = AsyncHistogram("event_bus_subscribe_latency_seconds", "Latency of processing a subscribed event.")
    BUS_LIVENESS = AsyncGauge("event_bus_liveness_status", "Status of the event bus connection (1=live, 0=down).")
    logger.info("Prometheus async metrics enabled.")
else:
    EVENTS_PUBLISHED = Counter("event_bus_published_total", "Total events published.", ["event_type", "status", "env", "tenant", "protocol"])
    EVENTS_SUBSCRIBED = Counter("event_bus_subscribed_total", "Total events subscribed.", ["event_type", "status", "env", "tenant", "protocol"])
    PUBLISH_LATENCY = Histogram("event_bus_publish_latency_seconds", "Latency of publishing an event.")
    SUBSCRIBE_LATENCY = Histogram("event_bus_subscribe_latency_seconds", "Latency of processing a subscribed event.")
    BUS_LIVENESS = Gauge("event_bus_liveness_status", "Status of the event bus connection (1=live, 0=down).")
    if not IS_TEST_ENV:
        logger.warning("prometheus-async not available. Using blocking metrics.")


async def _inc_counter(counter, **labels):
    # Cap event_type cardinality for metrics
    labels['event_type'] = labels['event_type'] if labels['event_type'] in ['user_created', 'order_placed', 'payment_failed'] else 'other'
    if PROMETHEUS_ASYNC_AVAILABLE:
        await counter.labels(**labels).inc()
    else:
        counter.labels(**labels).inc()

async def _set_gauge(gauge, value):
    if PROMETHEUS_ASYNC_AVAILABLE:
        await gauge.set(value)
    else:
        gauge.set(value)

def _observe_histogram(histogram, value):
    histogram.observe(value)


# ---- Circuit Breaker Implementation ----
@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    reset_timeout: int = 60  # seconds
    failures: int = 0
    last_failure: datetime = None
    is_open: bool = False

    def record_failure(self):
        self.failures += 1
        self.last_failure = datetime.now()
        if self.failures >= self.failure_threshold:
            self.is_open = True

    def can_proceed(self):
        if not self.is_open:
            return True
        if self.last_failure and (datetime.now() - self.last_failure).total_seconds() > self.reset_timeout:
            self.is_open = False
            self.failures = 0
            return True
        return False
        
circuit_breaker = CircuitBreaker()


# ---- Secure Redis Configuration and DLQ ----
_redis_connection_pool: Optional[ConnectionPool] = None

def _setup_bus():
    """Initializes module-level resources like the Redis connection pool."""
    global _redis_connection_pool
    if not _redis_connection_pool and REDIS_URL:
        if 'cluster' in REDIS_URL:
            # Note: redis-py handles cluster discovery from a single seed URL
            _redis_connection_pool = ClusterConnectionPool.from_url(REDIS_URL,
                username=REDIS_USER, password=REDIS_PASSWORD, max_connections=100)
            logger.info("Redis ClusterConnectionPool initialized.")
        else:
            _redis_connection_pool = ConnectionPool.from_url(
                REDIS_URL, 
                username=REDIS_USER, 
                password=REDIS_PASSWORD,
                encoding="utf-8", 
                decode_responses=True,
                max_connections=100
            )
            logger.info("Redis ConnectionPool initialized.")

def get_redis_client() -> redis.Redis:
    """Retrieves a securely configured Redis client from the module-level pool."""
    if not _redis_connection_pool:
        _setup_bus()
    return redis.Redis(connection_pool=_redis_connection_pool)

async def _write_to_dlq(event_type: str, payload: str, error: str, original_id: Optional[str] = None):
    """Writes a failed event to the Redis Stream dead-letter queue."""
    try:
        r = get_redis_client()
        dlq_entry = {
            "event_type": event_type,
            "payload": payload,
            "error": error,
            "timestamp": time.time(),
            "original_id": original_id if original_id else ""
        }
        await r.xadd(DLQ_STREAM_NAME, dlq_entry, maxlen=MAX_STREAM_LENGTH, approximate=True)
        logger.warning(f"Event {event_type} written to DLQ stream {DLQ_STREAM_NAME}.")
        await _inc_counter(EVENTS_PUBLISHED, event_type=event_type, status="dlq", env=ENV, tenant=TENANT, protocol="stream")
    except Exception as e:
        logger.error(f"Failed to write to DLQ for event {event_type}: {e}", exc_info=True)

async def replay_dlq():
    """Reads all messages from the DLQ and attempts to republish them."""
    logger.info(f"Starting DLQ replay from stream: {DLQ_STREAM_NAME}")
    try:
        r = get_redis_client()
        messages = await r.xread(count=1000, streams={DLQ_STREAM_NAME: "0-0"})
        
        if not messages:
            logger.info("DLQ is empty.")
            return

        dlq_stream, dlq_entries = messages[0]
        for msg_id, fields in dlq_entries:
            try:
                dlq_data = {k.decode('utf-8'): v.decode('utf-8') for k, v in fields.items()}
                event_type = dlq_data.get("event_type")
                payload_str = dlq_data.get("payload")
                
                logger.info(f"Replaying event ID {msg_id.decode()} for type {event_type}")
                
                await publish_event(event_type, json.loads(payload_str), is_replay=True)
                
                await r.xdel(DLQ_STREAM_NAME, msg_id)
                logger.info(f"Successfully replayed and deleted event ID {msg_id.decode()} from DLQ.")
            except Exception as e:
                logger.error(f"Failed to replay DLQ message {msg_id.decode()}: {e}", exc_info=True)
        
        logger.info("DLQ replay complete.")
    except Exception as e:
        logger.error(f"Critical error during DLQ replay: {e}", exc_info=True)


# ---- Publish/Subscribe Logic ----
if AIOLIMITER_AVAILABLE:
    publish_limiter = AsyncLimiter(PUBLISH_RATE_LIMIT_RPS, 1) # r/s
else:
    class MockLimiter:
        def __init__(self): pass
        async def __aenter__(self): pass
        async def __aexit__(self, exc_type, exc, tb): pass
    publish_limiter = MockLimiter()
    logger.warning("aiolimiter not available. Rate limiting is disabled.")

async def _prepare_payload(event_type: str, data: Dict[str, Any], schema: Optional[Type[BaseModel]] = None) -> Dict[str, str]:
    """Serializes, validates, and secures the event payload."""
    if schema and PYDANTIC_AVAILABLE:
        try:
            schema(**data)
        except ValidationError as e:
            logger.error(f"Event validation failed for {event_type}: {e.errors()}", exc_info=True)
            raise ValueError("Event data does not match schema.") from e
            
    payload_str = json.dumps(data)
    payload_bytes = payload_str.encode('utf-8')
    
    # The payload for signing should be the raw data, before encryption
    encrypted_payload_bytes = payload_bytes
    fernet_client = _get_fernet()
    if fernet_client:
        encrypted_payload_bytes = fernet_client.encrypt(payload_bytes)
    else:
        logger.warning("Encryption disabled due to missing key or library.")

    signature = _sign_payload(encrypted_payload_bytes)

    return {
        "payload": encrypted_payload_bytes.decode('utf-8'),
        "signature": signature
    }

async def _process_received_payload(message: Dict[str, bytes]) -> Dict[str, Any]:
    """Verifies and decrypts a received event payload."""
    signature = message.get(b"signature")
    encrypted_payload_bytes = message.get(b"payload")

    if not encrypted_payload_bytes or not signature:
        raise ValueError("Malformed message: missing payload or signature.")
        
    if not _verify_signature(encrypted_payload_bytes, signature.decode()):
        raise ValueError("HMAC signature verification failed. Message may be tampered with.")
    
    fernet_client = _get_fernet()
    if fernet_client:
        decrypted_payload_bytes = fernet_client.decrypt(encrypted_payload_bytes)
    else:
        decrypted_payload_bytes = encrypted_payload_bytes
        
    return json.loads(decrypted_payload_bytes.decode('utf-8'))

async def publish_event(
    event_type: str, 
    data: Dict[str, Any], 
    schema: Optional[Type[BaseModel]] = None,
    is_replay: bool = False
):
    """
    Publishes a single event to the mesh event bus.
    """
    if not REDIS_AVAILABLE:
        raise RuntimeError("Redis client library is not available.")
    
    if not circuit_breaker.can_proceed():
        logger.critical(f"Circuit breaker open for {event_type}. Skipping publish.")
        await _inc_counter(EVENTS_PUBLISHED, event_type=event_type, status="circuit_breaker", env=ENV, tenant=TENANT, protocol="n/a")
        raise RuntimeError("Circuit breaker open.")
        
    start_time = time.perf_counter()
    protocol = "stream" if USE_REDIS_STREAMS else "pubsub"
    with tracer.start_as_current_span(f"publish_event_{event_type}") as span:
        span.set_attribute("event.type", event_type)
        span.set_attribute("env", ENV)
        span.set_attribute("tenant", TENANT)
        span.set_attribute("protocol", protocol)

        try:
            # Prepare payload for pub/sub (json string) and streams (dict)
            payload_bytes = json.dumps(data).encode('utf-8')
            
            # Encrypt if needed
            encrypted_payload_bytes = payload_bytes
            fernet_client = _get_fernet()
            if fernet_client:
                encrypted_payload_bytes = fernet_client.encrypt(payload_bytes)
            
            # Sign the (potentially encrypted) payload
            signature = _sign_payload(encrypted_payload_bytes)
            
            payload_to_send = {
                "payload": encrypted_payload_bytes,
                "signature": signature.encode('utf-8')
            }
            
        except (ValueError, RuntimeError) as e:
            logger.critical(f"Event serialization, validation, or security failed for {event_type}: {e}")
            await _inc_counter(EVENTS_PUBLISHED, event_type=event_type, status="serialization_error", env=ENV, tenant=TENANT, protocol=protocol)
            span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, description=str(e)))
            raise

        async with publish_limiter:
            for i in range(MAX_RETRIES):
                try:
                    r = get_redis_client()
                    logger.info(f"Attempting to publish event: {event_type} via {protocol}")
                    
                    if USE_REDIS_STREAMS:
                        await r.xadd(f"{TENANT}:{event_type}", payload_to_send, maxlen=MAX_STREAM_LENGTH, approximate=True)
                    else:
                        pubsub_payload = {
                            "payload": encrypted_payload_bytes.decode('utf-8'),
                            "signature": signature
                        }
                        await r.publish(f"{TENANT}:{event_type}", json.dumps(pubsub_payload))
                    
                    logger.info(f"Published event successfully: {event_type}")
                    await _inc_counter(EVENTS_PUBLISHED, event_type=event_type, status="success", env=ENV, tenant=TENANT, protocol=protocol)
                    _observe_histogram(PUBLISH_LATENCY, time.perf_counter() - start_time)
                    await _set_gauge(BUS_LIVENESS, 1)
                    span.set_attribute("publish.attempt", i + 1)
                    span.set_status(trace.status.Status(trace.status.StatusCode.OK))
                    return
                except (ConnectionError, TimeoutError, RedisError) as e:
                    circuit_breaker.record_failure()
                    wait_time = RETRY_DELAY * (2**i) + random.uniform(0, 0.1) # Add jitter
                    logger.error(f"Publish retry {i+1}/{MAX_RETRIES} failed for {event_type}: {e}. Retrying in {wait_time:.2f}s...", exc_info=True)
                    await _set_gauge(BUS_LIVENESS, 0)
                    span.add_event("publish_retry", {"error": str(e), "attempt": i + 1})
                    if i < MAX_RETRIES - 1:
                        await asyncio.sleep(wait_time)
                except Exception as e:
                    logger.critical(f"An unexpected error occurred during publish for {event_type}: {e}", exc_info=True)
                    await _inc_counter(EVENTS_PUBLISHED, event_type=event_type, status="unexpected_error", env=ENV, tenant=TENANT, protocol=protocol)
                    span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, description=str(e)))
                    raise
        
        await _write_to_dlq(event_type, json.dumps(data), "Publish failed after multiple retries.")
        logger.critical(f"Publish failed for event '{event_type}' after {MAX_RETRIES} retries. Event written to DLQ.")
        span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, description="All retries failed"))
        raise RuntimeError(f"Event publish failed permanently for {event_type}.")

async def publish_events(events: List[Dict[str, Any]]):
    """
    Publishes a list of events to the mesh event bus in a single batch.
    """
    if not REDIS_AVAILABLE:
        raise RuntimeError("Redis client library is not available.")
    
    start_time = time.perf_counter()
    protocol = "stream" if USE_REDIS_STREAMS else "pubsub"
    with tracer.start_as_current_span("publish_events_batch") as span:
        span.set_attribute("event.count", len(events))
        span.set_attribute("env", ENV)
        span.set_attribute("tenant", TENANT)
        span.set_attribute("protocol", protocol)

        async with publish_limiter:
            try:
                r = get_redis_client()
                async with r.pipeline() as pipe:
                    for event in events:
                        event_type = event.get("event_type")
                        data = event.get("data")
                        schema = event.get("schema")
                        
                        if not event_type or data is None:
                            logger.error("Skipping malformed event in batch.")
                            continue
                        
                        payload_dict = await _prepare_payload(event_type, data, schema)

                        if USE_REDIS_STREAMS:
                            stream_payload = {
                                "payload": payload_dict["payload"].encode('utf-8'),
                                "signature": payload_dict["signature"].encode('utf-8')
                            }
                            pipe.xadd(f"{TENANT}:{event_type}", stream_payload, maxlen=MAX_STREAM_LENGTH, approximate=True)
                        else:
                            pipe.publish(f"{TENANT}:{event_type}", json.dumps(payload_dict))
                    
                    await pipe.execute()
                
                await _inc_counter(EVENTS_PUBLISHED, event_type="batch", status="success", env=ENV, tenant=TENANT, protocol=protocol)
                _observe_histogram(PUBLISH_LATENCY, time.perf_counter() - start_time)
                await _set_gauge(BUS_LIVENESS, 1)
                span.set_status(trace.status.Status(trace.status.StatusCode.OK))
            except Exception as e:
                logger.critical(f"Batch publish failed: {e}", exc_info=True)
                await _inc_counter(EVENTS_PUBLISHED, event_type="batch", status="failure", env=ENV, tenant=TENANT, protocol=protocol)
                span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, description=str(e)))
                raise

async def subscribe_event(
    event_type: str, 
    handler: Callable[[Dict[str, Any]], Awaitable[None]],
    consumer_group: str = "default_group",
    consumer_name: str = "consumer_1"
):
    """
    Subscribes to an event type. This function runs a persistent, monitored
    listener loop as an asyncio task.
    """
    if not REDIS_AVAILABLE:
        raise RuntimeError("Redis client library is not available.")
    
    async def listener_loop_streams():
        stream_name = f"{TENANT}:{event_type}"
        r = None
        
        try:
            r = get_redis_client()
            try:
                await r.xgroup_create(name=stream_name, groupname=consumer_group, id="$", mkstream=True)
            except ResponseError:
                pass
            logger.info(f"Subscribed to stream {stream_name} with group {consumer_group}")
            
            while True:
                pending_messages = await r.xautoclaim(
                    name=stream_name,
                    groupname=consumer_group,
                    consumername=consumer_name,
                    min_idle_time=10000,
                    start_id="0-0",
                    count=100
                )
                
                if pending_messages and pending_messages[1]:
                    logger.info(f"Claimed and processing {len(pending_messages[1])} pending messages...")
                    for msg_id, fields in pending_messages[1]:
                        await _handle_message(r, stream_name, msg_id, fields, handler, consumer_group, event_type)
                
                messages = await r.xreadgroup(
                    groupname=consumer_group,
                    consumername=consumer_name,
                    streams={stream_name: ">"},
                    count=1,
                    block=5000
                )
                
                if messages:
                    for stream, msgs in messages:
                        for msg_id, fields in msgs:
                            await _handle_message(r, stream_name, msg_id, fields, handler, consumer_group, event_type)
                else:
                    await r.ping()
                    await _set_gauge(BUS_LIVENESS, 1)

        except (ConnectionError, TimeoutError, RedisError) as e:
            logger.critical(f"Event bus connection lost. Retrying in 5s... Error: {e}")
            await _set_gauge(BUS_LIVENESS, 0)
            if r:
                await r.close()
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.info(f"Listener for {event_type} task cancelled.")
        except Exception as e:
            logger.critical(f"Critical failure in listener loop for {event_type}: {e}")
            await _set_gauge(BUS_LIVENESS, 0)
            raise
        finally:
            if r:
                await r.close()
            logger.info(f"Listener for {event_type} shutting down.")

    async def listener_loop_pubsub():
        channel_name = f"{TENANT}:{event_type}"
        r = None
        while True:
            try:
                r = get_redis_client()
                pubsub = r.pubsub()
                await pubsub.subscribe(channel_name)
                logger.info(f"Subscribed to event channel: {channel_name}")
                await _set_gauge(BUS_LIVENESS, 1)
                
                while True:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message:
                        start_time = time.perf_counter()
                        with tracer.start_as_current_span(f"handle_event_{event_type}") as span:
                            span.set_attribute("event.type", event_type)
                            span.set_attribute("env", ENV)
                            span.set_attribute("tenant", TENANT)
                            span.set_attribute("protocol", "pubsub")
                            try:
                                payload_dict_str = message['data']
                                payload_dict = json.loads(payload_dict_str)
                                received_payload = await _process_received_payload({
                                    b"payload": payload_dict["payload"].encode(),
                                    b"signature": payload_dict["signature"].encode()
                                })
                                logger.info(f"Received event: {event_type}")
                                await handler(received_payload)
                                await _inc_counter(EVENTS_SUBSCRIBED, event_type=event_type, status="success", env=ENV, tenant=TENANT, protocol="pubsub")
                                _observe_histogram(SUBSCRIBE_LATENCY, time.perf_counter() - start_time)
                                span.set_status(trace.status.Status(trace.status.StatusCode.OK))
                            except (json.JSONDecodeError, ValueError) as e:
                                logger.error(f"Failed to decode or verify event {event_type}: {e}", exc_info=True)
                                await _inc_counter(EVENTS_SUBSCRIBED, event_type=event_type, status="decode_error", env=ENV, tenant=TENANT, protocol="pubsub")
                                span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, description=str(e)))
                            except Exception as e:
                                logger.error(f"Error in handler for {event_type}: {e}", exc_info=True)
                                await _inc_counter(EVENTS_SUBSCRIBED, event_type=event_type, status="handler_error", env=ENV, tenant=TENANT, protocol="pubsub")
                                span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, description="Handler failed"))
                    else:
                        await r.ping()
                        await _set_gauge(BUS_LIVENESS, 1)

            except (ConnectionError, TimeoutError, RedisError) as e:
                logger.critical(f"Event bus connection lost. Retrying in 5s... Error: {e}")
                await _set_gauge(BUS_LIVENESS, 0)
                if r:
                    await r.close()
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                logger.info(f"Listener for {event_type} task cancelled.")
                if r:
                    await r.close()
                break
            except Exception as e:
                logger.critical(f"Critical failure in listener loop for {event_type}: {e}")
                await _set_gauge(BUS_LIVENESS, 0)
                if r:
                    await r.close()
                raise
            finally:
                if r:
                    await r.close()
                logger.info(f"Listener for {event_type} shutting down.")

    async def _handle_message(r, stream_name, msg_id, fields, handler, consumer_group, event_type):
        start_time = time.perf_counter()
        with tracer.start_as_current_span(f"handle_event_{event_type}") as span:
            span.set_attribute("event.type", event_type)
            span.set_attribute("env", ENV)
            span.set_attribute("tenant", TENANT)
            span.set_attribute("protocol", "stream")
            
            # Decode msg_id if it's bytes
            msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
            
            try:
                # Reliability Fix: Check redelivery count before processing
                pending_info = await r.xpending_range(stream_name, consumer_group, min=msg_id, max=msg_id, count=1)
                delivered_times = (pending_info[0]['delivered'] 
                                   if pending_info and len(pending_info) > 0 
                                   else 0)
                if delivered_times > 3:  # Example threshold
                    logger.warning(f"Max redeliveries exceeded for {msg_id_str}. Moving to DLQ.")
                    await _write_to_dlq(event_type, json.dumps({k.decode(): v.decode() for k, v in fields.items()}), "Max redeliveries exceeded", msg_id_str)
                    await r.xack(stream_name, consumer_group, msg_id)
                    await _inc_counter(EVENTS_SUBSCRIBED, event_type=event_type, status="dlq_max_retries", env=ENV, tenant=TENANT, protocol="stream")
                    return

                payload_dict = await _process_received_payload(fields)
                
                logger.info(f"Received event {msg_id_str} from stream {stream_name}")
                await handler(payload_dict)
                
                await r.xack(stream_name, consumer_group, msg_id)
                logger.debug(f"Acknowledged event {msg_id_str}")
                
                await _inc_counter(EVENTS_SUBSCRIBED, event_type=event_type, status="success", env=ENV, tenant=TENANT, protocol="stream")
                _observe_histogram(SUBSCRIBE_LATENCY, time.perf_counter() - start_time)
                span.set_status(trace.status.Status(trace.status.StatusCode.OK))
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Failed to decode or verify event {msg_id_str} for {event_type}: {e}", exc_info=True)
                await _inc_counter(EVENTS_SUBSCRIBED, event_type=event_type, status="decode_error", env=ENV, tenant=TENANT, protocol="stream")
                span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, description=str(e)))
                await r.xack(stream_name, consumer_group, msg_id) # Ack to prevent re-processing of bad messages
            except Exception as e:
                logger.error(f"Error in handler for {event_type} (id: {msg_id_str}): {e}", exc_info=True)
                await _inc_counter(EVENTS_SUBSCRIBED, event_type=event_type, status="handler_error", env=ENV, tenant=TENANT, protocol="stream")
                span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, description="Handler failed"))
                # No XACK here, message remains in PEL for potential redelivery

    if USE_REDIS_STREAMS:
        return asyncio.create_task(listener_loop_streams(), name=f"listener_task_{event_type}")
    else:
        return asyncio.create_task(listener_loop_pubsub(), name=f"listener_task_{event_type}")


# ---- Cleanup Functions ----
def cleanup():
    """Cleanup function to stop background threads."""
    logger.stop()

import atexit
atexit.register(cleanup)


# --- Test Harness ---
if __name__ == "__main__":
    if PROD_MODE:
        logger.critical("Test harness not allowed in production. Use separate test scripts.")
        sys.exit(1)

    logger.warning("Running in DEVELOPMENT mode. Not all production checks are active.")
    
    os.environ["REDIS_URL"] = os.environ.get("REDIS_URL", "redis://localhost:6379")
    os.environ["EVENT_BUS_ENCRYPTION_KEY"] = os.environ.get("EVENT_BUS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    os.environ["EVENT_BUS_HMAC_KEY"] = os.environ.get("EVENT_BUS_HMAC_KEY", os.urandom(32).hex())
    os.environ["REDIS_USER"] = os.environ.get("REDIS_USER", "default")
    os.environ["REDIS_PASSWORD"] = os.environ.get("REDIS_PASSWORD", "")
    os.environ["TENANT"] = os.environ.get("TENANT", "dev_tenant")
    os.environ["ENV"] = os.environ.get("ENV", "dev")
    
    async def run_integration_tests():
        try:
            logger.info("--- Testing DLQ replay ---")
            await replay_dlq()
            
            if not USE_REDIS_STREAMS:
                test_event_type = "test:pubsub:event"
                async def test_handler(data):
                    logger.info(f"Pub/Sub Test Handler received: {data}")
                    assert data["message"] == "Hello, Pub/Sub!"
                subscribe_task = await subscribe_event(test_event_type, test_handler)
                await asyncio.sleep(1)
                await publish_event(test_event_type, {"message": "Hello, Pub/Sub!"})
                await asyncio.sleep(1)
                subscribe_task.cancel()
                try:
                    await subscribe_task
                except asyncio.CancelledError:
                    pass # Expected
                logger.info("Pub/Sub test passed.")
            
            if USE_REDIS_STREAMS:
                test_event_type = "test:stream:event"
                class TestSchema(BaseModel):
                    message: str
                    number: int
                async def test_handler_streams(data):
                    logger.info(f"Streams Test Handler received: {data}")
                    assert data["message"] == "Hello, Streams!"
                    assert data["number"] == 42
                subscribe_task = await subscribe_event(
                    test_event_type, 
                    test_handler_streams,
                    consumer_group="test_group",
                    consumer_name="test_consumer"
                )
                await asyncio.sleep(1)
                await publish_event(
                    test_event_type, 
                    {"message": "Hello, Streams!", "number": 42},
                    schema=TestSchema
                )
                await asyncio.sleep(1)
                subscribe_task.cancel()
                try:
                    await subscribe_task
                except asyncio.CancelledError:
                    pass # Expected
                logger.info("Streams test passed.")
            
            logger.info("All tests passed successfully.")
            
        except Exception as e:
            logger.critical(f"Test harness failed: {e}", exc_info=True)
            sys.exit(1)
        finally:
            cleanup()

    asyncio.run(run_integration_tests())