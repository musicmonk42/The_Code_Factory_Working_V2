import asyncio
import datetime
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Literal, Optional, Tuple

import redis.asyncio as redis

# --- OpenTelemetry Tracing ---
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    _tracer_provider = TracerProvider()
    _span_processor = BatchSpanProcessor(
        ConsoleSpanExporter()
    )  # For dev; use a real exporter in prod
    _tracer_provider.add_span_processor(_span_processor)
    trace.set_tracer_provider(_tracer_provider)
    _tracer = trace.get_tracer(__name__)
except ImportError:
    # Fallback for systems without OpenTelemetry
    _tracer = None

    class NoOpTracer:
        def start_as_current_span(self, *args, **kwargs):
            return self

        def __enter__(self):
            pass

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def set_status(self, *args):
            pass

        def record_exception(self, *args):
            pass

    _tracer = NoOpTracer()

# --- Global Production Mode Flag (from main orchestrator) ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

# --- Logging Setup ---
logger = logging.getLogger("rabbitmq_audit_plugin")
if not logger.handlers:
    if PRODUCTION_MODE:
        LOG_FILE_PATH = os.getenv("RABBITMQ_PLUGIN_LOG_FILE", "/var/log/rabbitmq_plugin.log")
        try:
            os.makedirs(os.path.dirname(LOG_FILE_PATH) or ".", exist_ok=True)
            handler = logging.FileHandler(LOG_FILE_PATH)
            os.chmod(LOG_FILE_PATH, 0o600)
        except Exception as e:
            logger.error(
                f"CRITICAL: Failed to configure file logging to {LOG_FILE_PATH}: {e}. Falling back to stdout.",
                exc_info=True,
            )
            handler = logging.StreamHandler(sys.stdout)
            sys.stderr.write("CRITICAL: RabbitMQ plugin file logging failed. Aborting startup.\n")
            sys.exit(1)

        class JsonFormatter(logging.Formatter):
            def format(self, record):
                log_entry = {
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                    "level": record.levelname,
                    "name": record.name,
                    "message": record.getMessage(),
                }
                if hasattr(record, "extra") and record.extra:
                    log_entry.update(record.extra)
                return json.dumps(log_entry, ensure_ascii=False)

        handler.setFormatter(JsonFormatter())
    else:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")
        handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


# --- Custom Exceptions ---
class AnalyzerCriticalError(Exception):
    """
    Custom exception for critical errors that should halt execution and alert ops.
    """

    def __init__(self, message: str, alert_level: str = "CRITICAL"):
        super().__init__(message)
        alert_operator(message, alert_level)


class NonCriticalError(Exception):
    """
    Custom exception for recoverable issues that should be logged but not halt execution.
    """

    pass


# --- Centralized Utilities (replacing placeholders) ---
try:
    from plugins.core_audit import audit_logger
    from plugins.core_secrets import SECRETS_MANAGER
    from plugins.core_utils import alert_operator
    from plugins.core_utils import scrub_secrets as scrub_sensitive_data
except ImportError as e:
    logger.critical(
        f"CRITICAL: Missing core dependency for RabbitMQ plugin: {e}. Aborting startup."
    )
    sys.exit(1)

# --- Dependency Gating ---
try:
    import aiormq
    from aiormq.exceptions import AMQPConnectionError, ChannelInvalidStateError
except ImportError as e:
    logger.critical(
        f"CRITICAL: aiormq not found. RabbitMQ plugin functionality is critical. Aborting startup: {e}."
    )
    alert_operator("CRITICAL: aiormq missing. RabbitMQ plugin aborted.", level="CRITICAL")
    sys.exit(1)

try:
    from pydantic import BaseModel, Field, ValidationError, validator
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError as e:
    logger.critical(
        f"CRITICAL: pydantic or pydantic-settings not found. Schema validation is critical. Aborting startup: {e}."
    )
    alert_operator("CRITICAL: pydantic missing. RabbitMQ plugin aborted.", level="CRITICAL")
    sys.exit(1)

try:
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
except ImportError as e:
    logger.critical(
        f"CRITICAL: prometheus_client not found. Metrics are mandatory. Aborting startup: {e}."
    )
    alert_operator(
        "CRITICAL: prometheus_client missing. RabbitMQ plugin aborted.",
        level="CRITICAL",
    )
    sys.exit(1)

try:
    from aiohttp import web
except ImportError:
    web = None
    logger.warning("aiohttp not found. Health check server will not be available.")

# --- Caching: Redis Client Initialization ---
try:
    REDIS_CLIENT = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        db=0,
        decode_responses=True,
    )
except Exception as e:
    logger.warning(f"Failed to connect to Redis for caching: {e}. Caching will be disabled.")
    REDIS_CLIENT = None


# ---- 1. Centralized & Validated Settings (with Production Hardening) ----
class RabbitMQSettings(BaseSettings):
    """Manages all RabbitMQ configuration using Pydantic."""

    model_config = SettingsConfigDict(env_prefix="RABBITMQ_")

    url: Optional[str] = Field(
        None,
        description="RabbitMQ connection URL (e.g., amqps://user:pass@host:port/vhost).",
    )
    url_secret_id: Optional[str] = Field(
        None, description="Secret ID for RabbitMQ URL in a secure vault."
    )
    exchange_name: str = Field(..., min_length=1, description="RabbitMQ exchange name.")
    exchange_type: Literal["direct", "topic", "fanout", "headers"] = Field(
        "topic", description="RabbitMQ exchange type."
    )

    # Connection pool and channel settings
    connection_pool_size: int = Field(
        5, ge=1, description="Number of concurrent connections to maintain."
    )
    channel_pool_size_per_connection: int = Field(
        20, ge=1, description="Number of channels per connection."
    )

    # Gateway queue and worker settings
    max_queue_size: int = Field(
        10000,
        ge=0,
        description="Maximum number of events to buffer in the internal queue.",
    )
    worker_batch_size: int = Field(
        100, ge=1, description="How many messages the worker pulls from queue at once."
    )
    worker_linger_sec: float = Field(
        0.1, ge=0.01, description="How long the worker waits to build a batch."
    )
    num_workers: int = Field(
        4, ge=1, description="Number of concurrent workers to process the queue."
    )

    # Resilience patterns
    circuit_breaker_threshold: int = Field(
        10,
        ge=1,
        description="Number of consecutive failures to trip the circuit breaker.",
    )
    circuit_breaker_reset_sec: int = Field(
        30,
        ge=1,
        description="Time in seconds before a tripped circuit breaker attempts to reset.",
    )
    max_retries: int = Field(
        5, ge=1, description="Maximum retries for connection and publish operations."
    )
    retry_backoff_factor: float = Field(
        2.0, ge=1.0, description="Exponential backoff factor for retries."
    )

    # Exchange & Routing: allowlist for exchange_name and routing_key
    allowed_exchange_names: Optional[List[str]] = Field(
        None, description="List of allowed RabbitMQ exchange names in production."
    )
    allowed_routing_keys: Optional[List[str]] = Field(
        None,
        description="List of allowed RabbitMQ routing keys (regex allowed) in production.",
    )

    dry_run: bool = Field(False, description="If true, events are logged but not sent to RabbitMQ.")

    @validator("url")
    def validate_url_in_prod(cls, v, values):
        if values.get("url_secret_id"):
            url_from_secret = SECRETS_MANAGER.get_secret(
                values["url_secret_id"], required=True if PRODUCTION_MODE else False
            )
            v = url_from_secret

        if PRODUCTION_MODE:
            if not v:
                raise ValueError("In PRODUCTION_MODE, 'url' must be provided via 'url_secret_id'.")

            if "guest:guest" in v.lower():
                raise ValueError(
                    "Default 'guest:guest' credentials detected in RabbitMQ URL. Not allowed in production."
                )

            if not v.lower().startswith("amqps://"):
                raise ValueError(
                    f"Non-TLS URL '{v}' detected in PRODUCTION_MODE. 'amqps://' is mandatory."
                )

            allowed_exchanges = asyncio.run(cls._get_allowed_exchange_names())
            if allowed_exchanges and values.get("exchange_name") not in allowed_exchanges:
                raise ValueError(
                    f"Exchange name '{values.get('exchange_name')}' is not in the 'allowed_exchange_names' list."
                )

            if (
                "localhost" in v.lower()
                or "127.0.0.1" in v
                or "test" in v.lower()
                or "mock" in v.lower()
                or "example.com" in v.lower()
            ):
                raise ValueError(
                    f"Dummy/test RabbitMQ URL '{v}' detected in PRODUCTION_MODE. Not allowed."
                )
        return v

    @classmethod
    async def _get_allowed_exchange_names(cls):
        if REDIS_CLIENT:
            cache_key = "rabbitmq_allowed_exchanges"
            cached = await REDIS_CLIENT.get(cache_key)
            if cached:
                return json.loads(cached)

        allowed_names_str = SECRETS_MANAGER.get_secret(
            "RABBITMQ_ALLOWED_EXCHANGES", required=PRODUCTION_MODE
        )
        if not allowed_names_str:
            return []

        allowed_names = [n.strip() for n in allowed_names_str.split(",")]
        if REDIS_CLIENT:
            await REDIS_CLIENT.setex(cache_key, 3600, json.dumps(allowed_names))
        return allowed_names

    @validator("exchange_name")
    def validate_exchange_name_in_prod(cls, v, values):
        if PRODUCTION_MODE:
            if "*" in v or "?" in v:
                raise ValueError(
                    "Wildcard characters are not allowed in 'exchange_name' in PRODUCTION_MODE."
                )

            allowed_names = asyncio.run(cls._get_allowed_exchange_names())
            if allowed_names and v not in allowed_names:
                raise ValueError(
                    f"Exchange name '{v}' is not in the 'allowed_exchange_names' list: {allowed_names}."
                )

            if "dummy" in v.lower() or "test" in v.lower() or "mock" in v.lower():
                raise ValueError(
                    f"Dummy/test Exchange name '{v}' detected in PRODUCTION_MODE. Not allowed."
                )
        return v

    @validator("allowed_routing_keys")
    def validate_allowed_routing_keys_regex(cls, v):
        if v:
            for pattern_str in v:
                try:
                    re.compile(pattern_str)
                except re.error as e:
                    raise ValueError(
                        f"Invalid regex pattern in allowed_routing_keys: '{pattern_str}': {e}"
                    )
        return v

    @validator("dry_run")
    def validate_dry_run_in_prod(cls, v):
        if PRODUCTION_MODE and v:
            raise ValueError("In PRODUCTION_MODE, 'dry_run' must be False.")
        return v


# Instantiate settings globally (this will trigger validation at startup)
try:
    settings = RabbitMQSettings()
except ValidationError as e:
    logger.critical(
        f"CRITICAL: RabbitMQSettings validation failed: {e}. Aborting startup.",
        exc_info=True,
    )
    alert_operator(
        f"CRITICAL: RabbitMQSettings validation failed: {e}. Aborting.",
        level="CRITICAL",
    )
    sys.exit(1)
except Exception as e:
    logger.critical(
        f"CRITICAL: Unexpected error loading RabbitMQSettings: {e}. Aborting startup.",
        exc_info=True,
    )
    alert_operator(
        f"CRITICAL: Unexpected error loading RabbitMQSettings: {e}. Aborting.",
        level="CRITICAL",
    )
    sys.exit(1)


# ---- 2. Granular, Labeled Metrics (REQUIRED) ----
class RabbitMQMetrics:
    """Holds all Prometheus metrics for the RabbitMQ gateway."""

    def __init__(self, registry: CollectorRegistry):
        self.EVENTS_QUEUED = Counter(
            "rabbitmq_audit_events_queued_total",
            "Events placed into the send queue.",
            ["event_name"],
            registry=registry,
        )
        self.EVENTS_DROPPED = Counter(
            "rabbitmq_audit_events_dropped_total",
            "Events dropped due to a full queue.",
            ["event_name"],
            registry=registry,
        )
        self.EVENTS_PUBLISHED_SUCCESS = Counter(
            "rabbitmq_audit_events_published_success_total",
            "Events successfully published to RabbitMQ.",
            ["exchange"],
            registry=registry,
        )
        self.EVENTS_FAILED_PERMANENTLY = Counter(
            "rabbitmq_audit_events_failed_permanently_total",
            "Events that failed to publish after all retries.",
            ["exchange", "reason"],
            registry=registry,
        )
        self.PUBLISH_LATENCY = Histogram(
            "rabbitmq_audit_publish_latency_seconds",
            "Latency of a successful batch publish operation.",
            ["exchange"],
            registry=registry,
        )
        self.CIRCUIT_BREAKER_STATUS = Gauge(
            "rabbitmq_audit_circuit_breaker_status",
            "The status of the circuit breaker (1 for open, 0 for closed).",
            registry=registry,
        )
        self.QUEUE_SIZE = Gauge(
            "rabbitmq_audit_queue_current_size",
            "Current number of events in the RabbitMQ send queue.",
            registry=registry,
        )


_metrics_registry_instance = CollectorRegistry()
try:
    metrics = RabbitMQMetrics(_metrics_registry_instance)
    logger.info("Prometheus metrics initialized.")
except Exception as e:
    logger.critical(
        f"CRITICAL: Failed to initialize Prometheus metrics: {e}. Aborting startup.",
        exc_info=True,
    )
    alert_operator(
        "CRITICAL: Prometheus metrics initialization failed. RabbitMQ plugin aborted.",
        level="CRITICAL",
    )
    sys.exit(1)


# ---- 3. Validated Event Schema (REQUIRED) ----
class AuditEvent(BaseModel):
    event_name: str = Field(..., min_length=1)
    service_name: str = Field(..., min_length=1)
    timestamp: float = Field(default_factory=time.time, ge=0)
    details: Dict[str, Any] = Field(default_factory=dict)
    signature: Optional[str] = Field(None, description="HMAC signature of the event payload.")

    @validator("details")
    def validate_details_for_pii(cls, v):
        scrubbed_data = scrub_sensitive_data(v)
        if scrubbed_data != v:
            raise AnalyzerCriticalError(
                f"Sensitive data detected in audit event details during schema validation. Original: {scrub_sensitive_data(str(v)[:100])}"
            )
        return v

    def _sign_event(self) -> str:
        event_payload = self.model_dump(exclude={"signature"})
        event_json_str = json.dumps(event_payload, sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )
        rabbitmq_hmac_key = SECRETS_MANAGER.get_secret(
            "RABBITMQ_HMAC_KEY", required=True if PRODUCTION_MODE else False
        )

        if PRODUCTION_MODE and not rabbitmq_hmac_key:
            raise AnalyzerCriticalError(
                "Missing 'RABBITMQ_HMAC_KEY' in PRODUCTION_MODE for event signing. Aborting."
            )

        return hmac.new(
            rabbitmq_hmac_key.encode("utf-8"), event_json_str, hashlib.sha256
        ).hexdigest()


# ---- 4. Standalone Circuit Breaker (REQUIRED) ----
class CircuitBreaker:
    def __init__(self, threshold: int, reset_seconds: int, metrics: RabbitMQMetrics):
        self._threshold = threshold
        self._reset_seconds = reset_seconds
        self._metrics = metrics
        self._failure_count = 0
        self._is_open = False
        self._last_failure_time = 0.0
        self._metrics.CIRCUIT_BREAKER_STATUS.set(0)

    def check(self):
        if self._is_open:
            if time.monotonic() - self._last_failure_time > self._reset_seconds:
                self._is_open = False
                self._failure_count = 0
                self._metrics.CIRCUIT_BREAKER_STATUS.set(0)
                logger.warning("Circuit breaker has been reset. Resuming publish attempts.")
                audit_logger.log_event("rabbitmq_circuit_breaker_reset", status="closed")
                alert_operator(
                    "INFO: RabbitMQ Circuit Breaker RESET. Resuming publishes.",
                    level="INFO",
                )
            else:
                raise ConnectionAbortedError(
                    "Circuit breaker is open. Publish attempts are suspended."
                )

    def record_failure(self):
        self._failure_count += 1
        if self._failure_count >= self._threshold:
            if not self._is_open:
                self._is_open = True
                self._last_failure_time = time.monotonic()
                self._metrics.CIRCUIT_BREAKER_STATUS.set(1)
                logger.error("Circuit breaker tripped due to excessive publish failures.")
                audit_logger.log_event(
                    "rabbitmq_circuit_breaker_tripped",
                    status="open",
                    failure_count=self._failure_count,
                )
                alert_operator(
                    f"CRITICAL: RabbitMQ Circuit Breaker TRIPPED. Publishes suspended. Failures: {self._failure_count}",
                    level="CRITICAL",
                )

    def record_success(self):
        self._failure_count = 0


# ---- 5. The Divine RabbitMQ Gateway (with Production Hardening) ----
class RabbitMQGateway:
    """A highly resilient, performant, and observable RabbitMQ publisher."""

    def __init__(self, settings: RabbitMQSettings, metrics: RabbitMQMetrics):
        self.settings = settings
        self.metrics = metrics
        self.circuit_breaker = CircuitBreaker(
            settings.circuit_breaker_threshold,
            settings.circuit_breaker_reset_sec,
            metrics,
        )

        self._event_queue = asyncio.Queue(maxsize=settings.max_queue_size)
        self._worker_tasks: List[asyncio.Task] = []
        self._connection: Optional[aiormq.abc.AbstractConnection] = None
        self._connection_pool: Optional[asyncio.Queue] = None
        self._health_task: Optional[asyncio.Task] = None
        self._sem = asyncio.Semaphore(settings.num_workers * 2)

        if self.settings.url_secret_id:
            try:
                self.settings.url = SECRETS_MANAGER.get_secret(
                    self.settings.url_secret_id,
                    required=True if PRODUCTION_MODE else False,
                )
                logger.info("RabbitMQ URL loaded securely from secrets manager.")
            except Exception as e:
                raise AnalyzerCriticalError(
                    f"Failed to load RabbitMQ URL from secret manager: {e}."
                )
        elif PRODUCTION_MODE and (
            "guest:guest" in self.settings.url.lower()
            or not self.settings.url.lower().startswith("amqps://")
        ):
            audit_logger.log_event("rabbitmq_url_insecure_abort", url=self.settings.url)
            raise AnalyzerCriticalError(
                "RabbitMQ URL not securely loaded or uses insecure/default credentials/protocol in PRODUCTION_MODE."
            )

        logger.info(
            "RabbitMQ Gateway initialized.",
            extra={
                "context": {
                    "url_host": (
                        self.settings.url.split("@")[-1]
                        if "@" in self.settings.url
                        else self.settings.url
                    ),
                    "exchange": self.settings.exchange_name,
                }
            },
        )
        audit_logger.log_event(
            "rabbitmq_gateway_init",
            url_host=(
                self.settings.url.split("@")[-1] if "@" in self.settings.url else self.settings.url
            ),
            exchange=self.settings.exchange_name,
            max_queue_size=self.settings.max_queue_size,
        )

    async def _connect_with_retry(self):
        for attempt in range(self.settings.max_retries):
            try:
                conn = await aiormq.connect(self.settings.url)
                logger.info(
                    "RabbitMQ connection established.",
                    extra={"context": {"url": self.settings.url}},
                )
                audit_logger.log_event("rabbitmq_connect_success", url=self.settings.url)
                return conn
            except AMQPConnectionError as e:
                logger.warning(
                    f"Failed to connect to RabbitMQ (attempt {attempt + 1}/{self.settings.max_retries}): {e}. Retrying."
                )
                audit_logger.log_event(
                    "rabbitmq_connect_retry",
                    attempt=attempt + 1,
                    url=self.settings.url,
                    error=str(e),
                )
                await asyncio.sleep(self.settings.retry_backoff_factor**attempt)

        logger.critical("Failed to connect to RabbitMQ after all retries. Aborting.")
        audit_logger.log_event("rabbitmq_connect_failed_final", url=self.settings.url)
        raise AnalyzerCriticalError("Failed to connect to RabbitMQ after all retries.")

    async def startup(self):
        if not self._worker_tasks:
            self._connection_pool = asyncio.Queue(
                maxsize=self.settings.connection_pool_size
                * self.settings.channel_pool_size_per_connection
            )

            for _ in range(self.settings.connection_pool_size):
                conn = await self._connect_with_retry()
                for _ in range(self.settings.channel_pool_size_per_connection):
                    channel = await conn.channel()
                    await self._connection_pool.put(channel)

            async with self._connection_pool.get() as channel:
                await channel.exchange_declare(
                    exchange=self.settings.exchange_name,
                    exchange_type=self.settings.exchange_type,
                    durable=True,
                )

            logger.info(
                f"RabbitMQ exchange '{self.settings.exchange_name}' declared.",
                extra={"context": {"exchange_type": self.settings.exchange_type}},
            )
            audit_logger.log_event(
                "rabbitmq_exchange_declared",
                exchange=self.settings.exchange_name,
                exchange_type=self.settings.exchange_type,
            )

            self._worker_tasks = [
                asyncio.create_task(self._worker()) for _ in range(self.settings.num_workers)
            ]
            logger.info(
                f"RabbitMQ Gateway started with {self.settings.num_workers} workers.",
                extra={"context": {"exchange": self.settings.exchange_name}},
            )
            audit_logger.log_event(
                "rabbitmq_gateway_startup",
                status="success",
                num_workers=self.settings.num_workers,
            )

    async def shutdown(self):
        logger.info("RabbitMQ Gateway shutting down. Draining queue...")
        audit_logger.log_event(
            "rabbitmq_gateway_shutdown_start", queue_size=self._event_queue.qsize()
        )

        for _ in self._worker_tasks:
            await self._event_queue.put(None)

        try:
            await asyncio.wait_for(
                asyncio.gather(*self._worker_tasks),
                timeout=self.settings.worker_linger_sec
                * self.settings.max_queue_size
                / self.settings.worker_batch_size
                * 2
                + 10,
            )
            logger.info("RabbitMQ Gateway worker tasks finished.")
            audit_logger.log_event("rabbitmq_gateway_worker_finished")
        except asyncio.TimeoutError:
            remaining_events = self._event_queue.qsize()
            raise AnalyzerCriticalError(
                f"RabbitMQ Gateway worker tasks timed out during shutdown. {remaining_events} events remain unsent."
            )
        except Exception as e:
            raise AnalyzerCriticalError(f"Unexpected error during worker task shutdown: {e}.")

        if self._connection_pool:
            while not self._connection_pool.empty():
                try:
                    channel = self._connection_pool.get_nowait()
                    if not channel.is_closed:
                        await channel.close()
                except asyncio.QueueEmpty:
                    break
                except Exception as e:
                    logger.warning(f"Error closing a channel during shutdown: {e}")

            # The connection object is not directly managed in this pool, but the channels are.
            # A more robust system would also pool and manage connections to close them cleanly.
            logger.info("RabbitMQ channel pool drained and channels closed.")
            audit_logger.log_event("rabbitmq_channels_closed", status="success")

        logger.info("RabbitMQ Gateway shutdown complete.")
        audit_logger.log_event("rabbitmq_gateway_shutdown_complete", status="success")

    def publish(
        self,
        event_name: str,
        service_name: str,
        details: Dict[str, Any],
        routing_key: str = "",
    ):
        if PRODUCTION_MODE and self.settings.allowed_routing_keys:
            if not any(
                re.fullmatch(pattern, routing_key) for pattern in self.settings.allowed_routing_keys
            ):
                audit_logger.log_event(
                    "rabbitmq_routing_key_forbidden",
                    routing_key=routing_key,
                    allowed_keys=self.settings.allowed_routing_keys,
                )
                raise AnalyzerCriticalError(
                    f"Routing key '{routing_key}' not in allowed_routing_keys."
                )

        try:
            event = AuditEvent(event_name=event_name, service_name=service_name, details=details)
            event.signature = event._sign_event()

            self._event_queue.put_nowait((event, routing_key))
            self.metrics.EVENTS_QUEUED.labels(event_name=event_name).inc()
            self.metrics.QUEUE_SIZE.set(self._event_queue.qsize())
            audit_logger.log_event(
                "rabbitmq_event_queued",
                event_name=event_name,
                queue_size=self._event_queue.qsize(),
            )
        except asyncio.QueueFull:
            self.metrics.EVENTS_DROPPED.labels(event_name=event_name).inc()
            logger.critical(
                "CRITICAL: RabbitMQ event queue is full. Dropping event.",
                extra={
                    "context": {
                        "event_name": event_name,
                        "queue_size": self.settings.max_queue_size,
                    }
                },
            )
            audit_logger.log_event(
                "rabbitmq_event_dropped",
                event_name=event_name,
                reason="queue_full",
                queue_size=self.settings.max_queue_size,
            )
            alert_operator(
                f"CRITICAL: RabbitMQ event queue is FULL ({self.settings.max_queue_size} events). Events are being dropped. IMMEDIATE ACTION REQUIRED!",
                level="CRITICAL",
            )
        except ValidationError as e:
            logger.error(
                f"Invalid event schema for event '{event_name}': {e}",
                extra={"context": {"error": str(e)}},
            )
            audit_logger.log_event(
                "rabbitmq_event_validation_failed", event_name=event_name, error=str(e)
            )
            alert_operator(
                f"CRITICAL: RabbitMQ event validation failed for '{event_name}': {e}. Aborting.",
                level="CRITICAL",
            )
        except AnalyzerCriticalError as e:
            logger.critical(f"CRITICAL: {e}")
            audit_logger.log_event("rabbitmq_event_enqueue_error", error=str(e))
            alert_operator(f"CRITICAL: {e}", level="CRITICAL")
        except Exception as e:
            raise AnalyzerCriticalError(f"Unexpected error enqueueing event '{event_name}': {e}")

    async def _publish_batch(self, batch: List[Tuple[AuditEvent, str]]):
        with _tracer.start_as_current_span(
            "rabbitmq_publish_batch",
            attributes={
                "batch.size": len(batch),
                "exchange": self.settings.exchange_name,
            },
        ) as span:
            try:
                self.circuit_breaker.check()
            except ConnectionAbortedError as e:
                logger.warning(
                    "Publish skipped due to open circuit breaker.",
                    extra={"context": {"error": str(e)}},
                )
                self.metrics.EVENTS_FAILED_PERMANENTLY.labels(
                    exchange=self.settings.exchange_name, reason="circuit_breaker"
                ).inc(len(batch))
                audit_logger.log_event(
                    "rabbitmq_publish_skipped",
                    reason="circuit_breaker_open",
                    batch_size=len(batch),
                )
                return

            start_time = time.monotonic()

            try:
                channel = await self._connection_pool.get()
                await channel.confirm_select()

                async with self._sem:
                    for event, routing_key in batch:
                        body = event.model_dump_json().encode("utf-8")
                        await channel.basic_publish(
                            body,
                            exchange=self.settings.exchange_name,
                            routing_key=routing_key,
                            properties=aiormq.spec.Basic.Properties(delivery_mode=2),
                        )

                await channel.channel_close()
                self._connection_pool.put_nowait(channel)

                duration = time.monotonic() - start_time
                self.metrics.PUBLISH_LATENCY.labels(exchange=self.settings.exchange_name).observe(
                    duration
                )
                self.metrics.EVENTS_PUBLISHED_SUCCESS.labels(
                    exchange=self.settings.exchange_name
                ).inc(len(batch))
                self.circuit_breaker.record_success()
                if span:
                    span.set_status(trace.Status(trace.StatusCode.OK))
                logger.info(
                    f"Successfully published batch of {len(batch)} events to RabbitMQ.",
                    extra={"exchange": self.settings.exchange_name},
                )
                audit_logger.log_event(
                    "rabbitmq_publish_success",
                    batch_size=len(batch),
                    exchange=self.settings.exchange_name,
                    duration=duration,
                )
            except (
                AMQPConnectionError,
                ChannelInvalidStateError,
                asyncio.TimeoutError,
            ) as e:
                self.circuit_breaker.record_failure()
                self.metrics.EVENTS_FAILED_PERMANENTLY.labels(
                    exchange=self.settings.exchange_name, reason="service_unavailable"
                ).inc(len(batch))
                logger.critical(
                    f"CRITICAL: Failed to publish batch to RabbitMQ (connection/channel error/timeout): {e}.",
                    exc_info=True,
                )
                if span:
                    span.record_exception(e)
                if span:
                    span.set_status(trace.Status(trace.StatusCode.ERROR, "Publish failed"))
                audit_logger.log_event(
                    "rabbitmq_publish_failure",
                    batch_size=len(batch),
                    exchange=self.settings.exchange_name,
                    error=str(e),
                    reason="connection_error",
                )
                alert_operator(
                    f"CRITICAL: RabbitMQ publish failed: {e}. Aborting.",
                    level="CRITICAL",
                )
                raise AnalyzerCriticalError("RabbitMQ publish failed after retries.")
            except Exception as e:
                self.circuit_breaker.record_failure()
                logger.critical(
                    f"CRITICAL: Unhandled exception during RabbitMQ batch publish: {e}.",
                    exc_info=True,
                )
                if span:
                    span.record_exception(e)
                if span:
                    span.set_status(trace.Status(trace.StatusCode.ERROR, "Unhandled publish error"))
                audit_logger.log_event(
                    "rabbitmq_publish_failure",
                    batch_size=len(batch),
                    exchange=self.settings.exchange_name,
                    error=str(e),
                    reason="unhandled_exception",
                )
                alert_operator(
                    f"CRITICAL: Unhandled exception during RabbitMQ publish: {e}. Aborting.",
                    level="CRITICAL",
                )
                raise AnalyzerCriticalError("Unhandled exception during RabbitMQ publish.")

    async def _worker(self):
        while True:
            try:
                batch = []
                first_item = await self._event_queue.get()
                if first_item is None:
                    self._event_queue.task_done()
                    logger.info("Received shutdown sentinel. Exiting event processor.")
                    break
                batch.append(first_item)

                while len(batch) < self.settings.worker_batch_size:
                    try:
                        item = await asyncio.wait_for(
                            self._event_queue.get(), self.settings.worker_linger_sec
                        )
                        if item is None:
                            self._event_queue.task_done()
                            self._event_queue.put_nowait(None)
                            break
                        batch.append(item)
                    except asyncio.TimeoutError:
                        break

                if self.settings.dry_run:
                    logger.info(
                        "[DRY RUN] Would publish batch.",
                        extra={"context": {"batch_size": len(batch)}},
                    )
                    audit_logger.log_event("rabbitmq_dry_run_publish", batch_size=len(batch))
                    for event in batch:
                        self._event_queue.task_done()
                    continue

                await self._publish_batch(batch)
                for event in batch:
                    self._event_queue.task_done()

            except asyncio.CancelledError:
                logger.info("RabbitMQ worker task cancelled.")
                break
            except Exception as e:
                raise AnalyzerCriticalError(f"Unhandled exception in RabbitMQ worker: {e}.")

        logger.info("Event processor task finished.")

    async def __aenter__(self):
        await self.startup()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.shutdown()


rabbitmq_gateway = RabbitMQGateway(settings, metrics)


async def run_health_check_server():
    if not web:
        return

    async def health(_):
        return web.json_response(
            {
                "status": "healthy",
                "queue_size": rabbitmq_gateway._event_queue.qsize(),
                "circuit_breaker_status": (
                    "closed" if not rabbitmq_gateway.circuit_breaker._is_open else "open"
                ),
            }
        )

    app = web.Application()
    app.add_routes([web.get("/health", health)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("Health check server started on :8080/health.")


if not PRODUCTION_MODE:

    async def app_lifecycle():
        health_server_task = None
        if web:
            health_server_task = asyncio.create_task(run_health_check_server())

        await rabbitmq_gateway.startup()
        try:
            yield
        finally:
            await rabbitmq_gateway.shutdown()
            if health_server_task:
                health_server_task.cancel()
                try:
                    await health_server_task
                except asyncio.CancelledError:
                    pass

    async def main():
        async with app_lifecycle():
            logger.info("Divine RabbitMQ Gateway example started.")

            rabbitmq_gateway.publish(
                "user.logged_in",
                "auth-service",
                {"user_id": "user-456", "ip_address": "192.168.1.1"},
                routing_key="auth.login",
            )
            rabbitmq_gateway.publish(
                "payment.processed",
                "billing-service",
                {"amount": 129.50, "currency": "EUR"},
                routing_key="billing.payment.success",
            )

            logger.info("Main application logic continues immediately after publishing.")

            await asyncio.sleep(5)

    if __name__ == "__main__":
        try:
            asyncio.run(main())
        except SystemExit:
            pass
        except Exception as e:
            logger.critical(f"Unhandled exception during example run: {e}", exc_info=True)
            alert_operator(
                f"CRITICAL: Unhandled exception during RabbitMQ example run: {e}.",
                level="CRITICAL",
            )

else:
    if __name__ == "__main__":
        logger.critical(
            "CRITICAL: Attempted to run example/test code in PRODUCTION_MODE. This file should not be executed directly in production."
        )
        alert_operator(
            "CRITICAL: RabbitMQ plugin example code executed in PRODUCTION_MODE. Aborting.",
            level="CRITICAL",
        )
        sys.exit(1)
