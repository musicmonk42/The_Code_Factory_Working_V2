# arbiter/queue_consumer_worker.py

import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import time
import uuid
from functools import partial
from typing import Any, Callable, Dict, Optional

from aiohttp import web
from prometheus_client import REGISTRY, Counter, Histogram, start_http_server
from tenacity import retry, stop_after_attempt, wait_exponential

# --- Initialize logger early before any usage ---
logger = logging.getLogger("queue_consumer_worker")

# --- SFE Core Imports and Mock Fallback ---
SFE_CORE_AVAILABLE = False
try:
    from arbiter.bug_manager import AuditLogManager
    from arbiter.config import ArbiterConfig as Settings
    from arbiter.logging_utils import PIIRedactorFilter
    from arbiter.message_queue_service import (
        DecryptionError,
        MessageQueueService,
        MessageQueueServiceError,
        SerializationError,
    )
    from arbiter_plugin_registry import PlugInKind, registry
    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.propagate import get_global_textmap
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    SFE_CORE_AVAILABLE = True
except ImportError as e:
    logger.warning(f"SFE core components unavailable. Running in mock mode. Error: {e}")

# --- Mock Settings and Classes for Degraded Mode ---
if not SFE_CORE_AVAILABLE:
    # Mock PIIRedactorFilter for degraded mode
    class PIIRedactorFilter(logging.Filter):
        def filter(self, record):
            return True

    class MockSettings:
        """Mock settings class that can be instantiated."""

        LOG_LEVEL = "INFO"
        MQ_BACKEND_TYPE = "mock"
        REDIS_URL = "redis://localhost:6379"
        KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
        ENCRYPTION_KEY_BYTES = b""
        MQ_TOPIC_PREFIX = "mock_events"
        MQ_DLQ_TOPIC_SUFFIX = "dlq"
        MQ_MAX_RETRIES = 3
        MQ_RETRY_DELAY_BASE = 0.5
        MQ_CONSUMER_GROUP_ID = "mock_consumer_group"
        MQ_KAFKA_PRODUCER_ACKS = "all"
        MQ_KAFKA_PRODUCER_RETRIES = 3
        MQ_KAFKA_CONSUMER_AUTO_OFFSET_RESET = "earliest"
        MQ_KAFKA_CONSUMER_ENABLE_AUTO_COMMIT = True
        MQ_KAFKA_CONSUMER_AUTO_COMMIT_INTERVAL_MS = 1000
        MQ_REDIS_STREAM_MAXLEN = 1000
        MQ_REDIS_STREAM_TRIM_STRATEGY = "~"
        MQ_POISON_MESSAGE_THRESHOLD = 5
        MQ_CONSUMER_CONCURRENCY = 10
        PROMETHEUS_PORT = 9090
        HEALTH_PORT = 8080
        CRITICAL_EVENTS_FOR_MQ = ["mock_event"]
        SLACK_WEBHOOK_URL = None

    # Settings should be a class, not an instance
    Settings = MockSettings

    class DummySpan:
        def __enter__(self):
            pass

        def __exit__(self, *a):
            pass

        def set_attribute(self, *a, **k):
            pass

        def record_exception(self, *a, **k):
            pass

        def set_status(self, *a, **k):
            pass

    class DummyTracer:
        def start_as_current_span(self, *a, **k):
            return DummySpan()

    tracer = DummyTracer()

    def meter():
        return None

    def propagator():
        return None

    class MockCallTracker:
        """Helper class to track method calls for testing."""

        def __init__(self, name, return_value=None):
            self.name = name
            self.call_count = 0
            self.call_args = []
            self.return_value = return_value

        async def __call__(self, *args, **kwargs):
            self.call_count += 1
            self.call_args.append((args, kwargs))
            logger.info(f"Mock {self.name} called")
            return self.return_value

        def assert_called_once(self):
            if self.call_count != 1:
                raise AssertionError(
                    f"{self.name} was called {self.call_count} times, expected 1"
                )

        def assert_called(self):
            if self.call_count == 0:
                raise AssertionError(f"{self.name} was not called")

    class MockService:
        """A mock message queue service for testing or degraded mode."""

        def __init__(self, *args, **kwargs):
            self.redis_client = None
            # Create trackable mock methods
            self.connect = MockCallTracker("MQ connect")
            self.disconnect = MockCallTracker("MQ disconnect")
            self.subscribe = MockCallTracker("MQ subscribe")
            self._send_to_dlq = MockCallTracker("MQ send_to_dlq")
            self.healthcheck = MockCallTracker(
                "MQ healthcheck", return_value={"status": "healthy"}
            )

    MessageQueueService = MockService

    class MockAudit:
        """A mock audit log manager."""

        def __init__(self):
            # Create trackable mock methods
            self.audit = MockCallTracker("Audit")
            self.initialize = MockCallTracker("AuditLogger initialize")
            self.shutdown = MockCallTracker("AuditLogger shutdown")

    AuditLogManager = MockAudit

    # Mock registry and PlugInKind for degraded mode
    class MockRegistry:
        def register(self, **kwargs):
            def decorator(cls):
                return cls

            return decorator

    class PlugInKind:
        CORE_SERVICE = "core_service"

    registry = MockRegistry()

# --- Logging Setup ---
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    )
    handler.addFilter(PIIRedactorFilter())
    logger.addHandler(handler)

# Create an instance to access attributes, but keep Settings as a class
_settings_instance = Settings() if not SFE_CORE_AVAILABLE else Settings
logger.setLevel(getattr(_settings_instance, "LOG_LEVEL", "INFO").upper())

# --- Tracing ---
if SFE_CORE_AVAILABLE:
    try:
        resource = Resource.create({"service.name": "sfe-queue-consumer-worker"})
        trace_provider = TracerProvider(resource=resource)

        # Only add span processor if not in test mode
        if "pytest" not in sys.modules and "unittest" not in sys.modules:
            trace_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

        trace.set_tracer_provider(trace_provider)
        tracer = trace.get_tracer(__name__)
        meter = metrics.get_meter(__name__)
        propagator = get_global_textmap()
    except Exception as e:
        logger.warning(f"Failed to initialize OpenTelemetry: {e}. Tracing disabled.")

        # Fallback to dummy implementations
        class DummySpan:
            def __enter__(self):
                pass

            def __exit__(self, *a):
                pass

            def set_attribute(self, *a, **k):
                pass

            def record_exception(self, *a, **k):
                pass

            def set_status(self, *a, **k):
                pass

        class DummyTracer:
            def start_as_current_span(self, *a, **k):
                return DummySpan()

        tracer = DummyTracer()

        def meter():
            return None

        def propagator():
            return None


# --- Prometheus Metrics ---
def _get_metric(mt, name, doc, labels=(), buckets=None):
    """Safely create or retrieve a Prometheus metric."""
    if (
        hasattr(REGISTRY, "_names_to_collectors")
        and name in REGISTRY._names_to_collectors
    ):
        return REGISTRY._names_to_collectors[name]
    if mt == Histogram and buckets:
        return mt(name, doc, labelnames=labels, buckets=buckets)
    return mt(name, doc, labelnames=labels)


CONSUMER_MESSAGES_PROCESSED_TOTAL = _get_metric(
    Counter,
    "consumer_messages_processed_total",
    "Total messages processed by consumer",
    ["event_type", "status"],
)
CONSUMER_DELIVERY_ATTEMPTS_TOTAL = _get_metric(
    Counter,
    "consumer_delivery_attempts_total",
    "Delivery attempts to external webhooks",
    ["event_type", "webhook_url_hash"],
)
CONSUMER_DELIVERY_SUCCESS_TOTAL = _get_metric(
    Counter,
    "consumer_delivery_success_total",
    "Successful messages delivered externally",
    ["event_type", "webhook_url_hash"],
)
CONSUMER_DELIVERY_FAILURE_TOTAL = _get_metric(
    Counter,
    "consumer_delivery_failure_total",
    "Failed external deliveries",
    ["event_type", "webhook_url_hash", "error_code"],
)
CONSUMER_DELIVERY_LATENCY_SECONDS = _get_metric(
    Histogram,
    "consumer_delivery_latency_seconds",
    "Latency for external delivery",
    ["event_type", "webhook_url_hash"],
)
CONSUMER_POISON_MESSAGES_TOTAL = _get_metric(
    Counter,
    "consumer_poison_messages_total",
    "Poison messages quarantined",
    ["event_type"],
)
CONSUMER_OPS_TOTAL = _get_metric(
    Counter, "consumer_ops_total", "Total consumer operations", ["event_type"]
)
CONSUMER_ERRORS_TOTAL = _get_metric(
    Counter,
    "consumer_errors_total",
    "Total consumer errors",
    ["event_type", "error_type"],
)

shutdown_event = asyncio.Event()


# --- Security: Redact Sensitive Data ---
def redact_sensitive(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Redacts sensitive keys in a dictionary for logging and auditing.
    This function is a recursive, non-destructive check.

    Args:
        data (Dict[str, Any]): The input dictionary.

    Returns:
        Dict[str, Any]: A new dictionary with sensitive values redacted.
    """
    if not isinstance(data, dict):
        return data
    sensitive_keys = {
        "token",
        "key",
        "password",
        "secret",
        "api_key",
        "webhook_url",
        "routing_key",
    }
    out = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[k] = redact_sensitive(v)
        elif isinstance(k, str) and any(s in k.lower() for s in sensitive_keys):
            out[k] = "[REDACTED]"
        else:
            out[k] = v
    return out


# --- External Notifier Handlers ---
_EXTERNAL_NOTIFIER_HANDLERS: Dict[str, Any] = {}


async def initialize_handlers():
    """Initializes external notification handlers based on settings."""
    if not SFE_CORE_AVAILABLE:
        logger.info("Skipping external handler initialization in mock mode.")
        return

    settings_inst = Settings() if callable(Settings) else Settings
    if getattr(settings_inst, "SLACK_WEBHOOK_URL", None):
        try:
            from plugins.slack_plugin import SlackAuditHook

            _EXTERNAL_NOTIFIER_HANDLERS["slack"] = SlackAuditHook(
                webhook_url=str(settings_inst.SLACK_WEBHOOK_URL)
            )
            logger.info("Successfully initialized Slack audit hook.")
        except ImportError:
            logger.warning("Slack plugin not found. Slack notifications disabled.")
        except Exception as e:
            logger.warning(f"SlackAuditHook initialization failed: {e}")


async def send_to_external_notifier(event_type: str, data: Dict[str, Any]) -> bool:
    """
    Sends an event to all initialized external notification handlers.

    Args:
        event_type (str): The type of event.
        data (Dict[str, Any]): The event data.

    Returns:
        bool: True if at least one handler successfully delivered the message, False otherwise.
    """
    url_hash = hashlib.sha256(
        str(data.get("webhook_url", "generic")).encode()
    ).hexdigest()[:8]
    correlation_id = data.get("correlation_id", str(uuid.uuid4()))
    outgoing_data = data.copy()
    outgoing_data["correlation_id"] = correlation_id

    delivered_count = 0

    with tracer.start_as_current_span(
        f"send_notification_{event_type}",
        attributes={"event.type": event_type, "correlation.id": correlation_id},
    ):
        for handler_name, handler_instance in _EXTERNAL_NOTIFIER_HANDLERS.items():
            CONSUMER_DELIVERY_ATTEMPTS_TOTAL.labels(event_type, url_hash).inc()
            try:
                # Check for a specific 'audit_hook' async method
                if hasattr(
                    handler_instance, "audit_hook"
                ) and asyncio.iscoroutinefunction(handler_instance.audit_hook):
                    await handler_instance.audit_hook(event_type, outgoing_data)
                # Check for a partial function wrapped with a handler
                elif isinstance(
                    handler_instance, partial
                ) and asyncio.iscoroutinefunction(handler_instance.func):
                    await handler_instance(event_type, outgoing_data)
                else:
                    logger.warning(
                        f"Handler '{handler_name}' has an unsupported interface."
                    )
                    continue

                delivered_count += 1
                CONSUMER_DELIVERY_SUCCESS_TOTAL.labels(event_type, url_hash).inc()
                logger.info(
                    f"Delivered {event_type} via {handler_name} [correlation_id={correlation_id}]"
                )
            except Exception as e:
                CONSUMER_DELIVERY_FAILURE_TOTAL.labels(
                    event_type, url_hash, type(e).__name__
                ).inc()
                CONSUMER_ERRORS_TOTAL.labels(
                    event_type=event_type, error_type="external_delivery"
                ).inc()
                logger.error(
                    f"Delivery to '{handler_name}' failed: {e} [correlation_id={correlation_id}]"
                )

    return delivered_count > 0


# --- Poison Detection ---
# Use settings instance for attribute access
_settings_for_attrs = Settings() if not SFE_CORE_AVAILABLE else Settings
POISON_MESSAGE_THRESHOLD = getattr(
    _settings_for_attrs, "MQ_POISON_MESSAGE_THRESHOLD", 5
)
POISON_MESSAGE_KEY_PREFIX = "poison_msg:"


async def process_event(
    event_type: str,
    data: Dict[str, Any],
    mq_service: MessageQueueService,
    audit_logger: AuditLogManager,
):
    """
    Processes a single message, handles delivery, and manages poison messages.

    Args:
        event_type (str): The type of event.
        data (Dict[str, Any]): The event payload.
        mq_service (MessageQueueService): The message queue service instance.
        audit_logger (AuditLogManager): The audit logger instance.
    """
    CONSUMER_OPS_TOTAL.labels(event_type=event_type).inc()
    correlation_id = data.get("correlation_id", str(uuid.uuid4()))
    message_id = (
        data.get("event_id")
        or data.get("id")
        or hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    )
    poison_key = f"{POISON_MESSAGE_KEY_PREFIX}{event_type}:{message_id}"

    current_retries = 0
    if SFE_CORE_AVAILABLE and getattr(mq_service, "redis_client", None):
        try:
            val = await mq_service.redis_client.get(poison_key)
            if val:
                current_retries = int(val)
        except Exception as e:
            logger.error(f"Failed to check poison retry count for '{poison_key}': {e}")
            CONSUMER_ERRORS_TOTAL.labels(
                event_type=event_type, error_type="redis_check"
            ).inc()

    if current_retries >= POISON_MESSAGE_THRESHOLD:
        logger.critical(
            f"Poison message for {event_type} [{correlation_id}] exceeded retries; moving to DLQ."
        )
        CONSUMER_POISON_MESSAGES_TOTAL.labels(event_type).inc()
        CONSUMER_ERRORS_TOTAL.labels(
            event_type=event_type, error_type="poison_message"
        ).inc()
        await audit_logger.audit(
            "poison_message_quarantined",
            {
                "event_type": event_type,
                "message_id": message_id,
                "correlation_id": correlation_id,
                "data_summary": str(redact_sensitive(data))[:200],
                "reason": "exceeded_poison_threshold",
            },
        )
        await mq_service._send_to_dlq(event_type, data, "Poison: retries exceeded")
        return

    start_time = time.monotonic()

    try:
        delivered = await send_to_external_notifier(event_type, redact_sensitive(data))
        if delivered:
            if SFE_CORE_AVAILABLE and getattr(mq_service, "redis_client", None):
                await mq_service.redis_client.delete(poison_key)
            CONSUMER_MESSAGES_PROCESSED_TOTAL.labels(event_type, "success").inc()
        else:
            if SFE_CORE_AVAILABLE and getattr(mq_service, "redis_client", None):
                await mq_service.redis_client.incr(poison_key)
                # Set a TTL for the poison key
                await mq_service.redis_client.expire(poison_key, 86400)  # 24 hours
            await mq_service._send_to_dlq(event_type, data, "External delivery failed")
            CONSUMER_MESSAGES_PROCESSED_TOTAL.labels(
                event_type, "failed_delivery"
            ).inc()
            CONSUMER_ERRORS_TOTAL.labels(
                event_type=event_type, error_type="delivery_failed"
            ).inc()
            logger.critical(
                f"{event_type} [{correlation_id}] to DLQ after failed delivery."
            )
            await audit_logger.audit(
                "message_delivery_failed",
                {
                    "event_type": event_type,
                    "message_id": message_id,
                    "correlation_id": correlation_id,
                    "reason": "external_delivery_failed",
                },
            )
    except Exception as e:
        # Catch-all for unexpected errors during processing, marking as poison.
        CONSUMER_POISON_MESSAGES_TOTAL.labels(event_type).inc()
        CONSUMER_ERRORS_TOTAL.labels(
            event_type=event_type, error_type="processing_exception"
        ).inc()
        await audit_logger.audit(
            "poison_message_unhandled_error",
            {
                "event_type": event_type,
                "message_id": message_id,
                "error": str(e),
                "correlation_id": correlation_id,
                "reason": "unhandled_processing_exception",
            },
        )
        await mq_service._send_to_dlq(event_type, data, f"Poison message: {e}")
    finally:
        url_hash = hashlib.sha256(
            str(data.get("webhook_url", "generic")).encode()
        ).hexdigest()[:8]
        CONSUMER_DELIVERY_LATENCY_SECONDS.labels(event_type, url_hash).observe(
            time.monotonic() - start_time
        )


CONCURRENT_LIMIT = getattr(_settings_for_attrs, "MQ_CONSUMER_CONCURRENCY", 10)
delivery_semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)


async def handle_message(
    event_type: str,
    data: Dict[str, Any],
    mq_service: MessageQueueService,
    audit_logger: AuditLogManager,
):
    """
    Processes a single message, handles delivery, and manages poison messages.

    Args:
        event_type (str): The type of the event.
        data (Dict[str, Any]): The event payload.
        mq_service (MessageQueueService): The message queue service instance.
        audit_logger (AuditLogManager): The audit logger instance.

    Raises:
        ValueError: If message processing fails or poison threshold is exceeded.
    """
    async with delivery_semaphore:
        await process_event(event_type, data, mq_service, audit_logger)


# --- Health Endpoint ---
mq_service_instance: Optional[MessageQueueService] = None
start_time = time.time()


async def health_check_handler(request: web.Request) -> web.Response:
    """Provides a detailed health status of the worker and its dependencies."""
    status = {"status": "healthy", "uptime_seconds": round(time.time() - start_time, 2)}
    try:
        if SFE_CORE_AVAILABLE and mq_service_instance:
            mq_status = await mq_service_instance.healthcheck()
            status["mq_service_status"] = mq_status.get("status")
            if mq_status.get("status") != "healthy":
                status["status"] = "degraded"

        handler_healths = {}
        for name, handler in _EXTERNAL_NOTIFIER_HANDLERS.items():
            if hasattr(handler, "health") and asyncio.iscoroutinefunction(
                handler.health
            ):
                try:
                    handler_healths[name] = await handler.health()
                except Exception as e:
                    handler_healths[name] = {"status": "error", "message": str(e)}
                    status["status"] = "degraded"
        status["external_notifiers_health"] = handler_healths
    except Exception as e:
        status["status"] = "unhealthy"
        status["error"] = str(e)

    response_status = 200 if status["status"] == "healthy" else 503
    return web.json_response(status, status=response_status)


# --- Main Application Class ---
audit_logger_instance: Optional[AuditLogManager] = None


class QueueConsumerWorker:
    def __init__(self, settings=None):
        self.settings = settings or Settings()
        self.mq_service = None
        self.audit_logger = None
        self.health_app = None
        self.runner = None

    async def __aenter__(self):
        global mq_service_instance, audit_logger_instance, start_time
        start_time = time.time()

        if SFE_CORE_AVAILABLE:
            self.mq_service = MessageQueueService(
                backend_type=self.settings.MQ_BACKEND_TYPE,
                redis_url=str(self.settings.REDIS_URL),
                kafka_bootstrap_servers=self.settings.KAFKA_BOOTSTRAP_SERVERS,
                encryption_key=self.settings.ENCRYPTION_KEY_BYTES,
                topic_prefix=self.settings.MQ_TOPIC_PREFIX,
                dlq_topic_suffix=self.settings.MQ_DLQ_TOPIC_SUFFIX,
                max_retries=self.settings.MQ_MAX_RETRIES,
                retry_delay_base=self.settings.MQ_RETRY_DELAY_BASE,
                consumer_group_id=self.settings.MQ_CONSUMER_GROUP_ID,
                kafka_producer_acks=self.settings.MQ_KAFKA_PRODUCER_ACKS,
                kafka_producer_retries=self.settings.MQ_KAFKA_PRODUCER_RETRIES,
                kafka_consumer_auto_offset_reset=self.settings.MQ_KAFKA_CONSUMER_AUTO_OFFSET_RESET,
                kafka_consumer_enable_auto_commit=self.settings.MQ_KAFKA_CONSUMER_ENABLE_AUTO_COMMIT,
                kafka_consumer_auto_commit_interval_ms=self.settings.MQ_KAFKA_CONSUMER_AUTO_COMMIT_INTERVAL_MS,
                redis_stream_maxlen=self.settings.MQ_REDIS_STREAM_MAXLEN,
                redis_stream_trim_strategy=self.settings.MQ_REDIS_STREAM_TRIM_STRATEGY,
            )
            self.audit_logger = AuditLogManager()
            await self.audit_logger.initialize()
        else:
            self.mq_service = MessageQueueService()
            self.audit_logger = AuditLogManager()
            # Initialize mock audit logger if it has an initialize method
            if hasattr(self.audit_logger, "initialize"):
                await self.audit_logger.initialize()

        mq_service_instance = self.mq_service
        audit_logger_instance = self.audit_logger

        await self.mq_service.connect()
        await initialize_handlers()

        metrics_port = getattr(self.settings, "PROMETHEUS_PORT", 9090)
        start_http_server(metrics_port)
        logger.info(f"Prometheus metrics at :{metrics_port}")

        health_port = getattr(self.settings, "HEALTH_PORT", 8080)
        # Security: Use environment variable for host binding (default to localhost)
        health_host = os.getenv("HEALTH_HOST", "127.0.0.1")
        self.health_app = web.Application()
        self.health_app.router.add_get("/health", health_check_handler)
        self.runner = web.AppRunner(self.health_app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, health_host, health_port)
        await site.start()
        logger.info(f"Health at http://{health_host}:{health_port}/health")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        logger.info("Consumer worker shutting down...")
        if self.mq_service:
            await self.mq_service.disconnect()
        if self.audit_logger and hasattr(self.audit_logger, "shutdown"):
            await self.audit_logger.shutdown()
        if self.runner:
            await self.runner.cleanup()
        logger.info("Consumer worker shutdown complete.")

    async def run(self):
        logger.info("Queue consumer worker started.")
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            # Signal handlers are not supported on Windows
            if sys.platform != "win32":
                loop.add_signal_handler(sig, shutdown_event.set)

        event_types = getattr(self.settings, "CRITICAL_EVENTS_FOR_MQ", [])
        if not event_types:
            logger.warning("No critical events configured. Consumer will idle.")

        subs = []
        for event_type in event_types:
            handler_with_ctx = partial(
                handle_message,
                event_type=event_type,
                mq_service=self.mq_service,
                audit_logger=self.audit_logger,
            )

            @retry(
                stop=stop_after_attempt(5),
                wait=wait_exponential(multiplier=1, min=5, max=60),
                reraise=True,
            )
            async def subscribe_robustly(et: str, h: Callable):
                logger.info(f"Attempting to subscribe to '{et}'...")
                await self.mq_service.subscribe(et, h)
                logger.info(f"Successfully subscribed to '{et}'.")

            subs.append(subscribe_robustly(event_type, handler_with_ctx))

        if subs:
            await asyncio.gather(*subs)

        await shutdown_event.wait()


async def consumer_main_loop():
    """
    Main asynchronous loop for the message queue consumer worker.
    Initializes services, starts health and metrics servers, and subscribes to events.
    """
    async with QueueConsumerWorker() as worker:
        await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(consumer_main_loop())
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Main worker loop cancelled by signal.")
    except Exception as e:
        logger.critical(f"Fatal error in worker: {e}", exc_info=True)
        sys.exit(1)

# Register as a plugin if core components are available
if SFE_CORE_AVAILABLE:
    registry.register(
        kind=PlugInKind.CORE_SERVICE,
        name="QueueConsumerWorker",
        version="1.0.0",
        author="Arbiter Team",
    )(QueueConsumerWorker)
