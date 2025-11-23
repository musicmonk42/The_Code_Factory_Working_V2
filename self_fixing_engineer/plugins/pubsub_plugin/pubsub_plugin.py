import os
import asyncio
import time
import json
import logging
import sys
import datetime
import redis.asyncio as redis

from typing import Dict, Any, Optional, List

# --- OpenTelemetry Tracing ---
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    _tracer_provider = TracerProvider()
    _span_processor = BatchSpanProcessor(
        ConsoleSpanExporter()
    )  # For dev/local. In prod, use a real exporter (e.g., OTLP)
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
logger = logging.getLogger("pubsub_audit_plugin")
if not logger.handlers:
    if PRODUCTION_MODE:
        LOG_FILE_PATH = os.getenv("PUBSUB_PLUGIN_LOG_FILE", "/var/log/pubsub_plugin.log")
        try:
            os.makedirs(os.path.dirname(LOG_FILE_PATH) or ".", exist_ok=True)
            handler = logging.FileHandler(LOG_FILE_PATH)
            os.chmod(LOG_FILE_PATH, 0o600)
        except Exception as e:
            logger.error(
                f"CRITICAL: Failed to configure file logging to {LOG_FILE_PATH}: {e}. Falling back to stdout.",
                exc_info=True,
            )
            # Do not use alert_operator here, as it may not be ready. Fall back to stdout and alert on that channel.
            handler = logging.StreamHandler(sys.stdout)
            sys.stderr.write("CRITICAL: Pub/Sub plugin file logging failed. Aborting startup.\n")
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
    from plugins.core_utils import alert_operator, scrub_secrets as scrub_sensitive_data
    from plugins.core_audit import audit_logger
    from plugins.core_secrets import SECRETS_MANAGER
except ImportError as e:
    logger.critical(f"CRITICAL: Missing core dependency for Pub/Sub plugin: {e}. Aborting startup.")
    sys.exit(1)


# --- Dependency Gating ---
try:
    from google.cloud import pubsub_v1
    from google.api_core import exceptions as google_exceptions
    from google.api_core import retry as api_retry
    from google.oauth2 import service_account
except ImportError as e:
    logger.critical(
        f"CRITICAL: google-cloud-pubsub not found. Pub/Sub plugin functionality is critical. Aborting startup: {e}."
    )
    alert_operator(
        "CRITICAL: google-cloud-pubsub missing. Pub/Sub plugin aborted.",
        level="CRITICAL",
    )
    sys.exit(1)

try:
    from pydantic import BaseModel, ValidationError, Field, validator
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError as e:
    logger.critical(
        f"CRITICAL: pydantic or pydantic-settings not found. Schema validation is critical. Aborting startup: {e}."
    )
    alert_operator("CRITICAL: pydantic missing. Pub/Sub plugin aborted.", level="CRITICAL")
    sys.exit(1)

try:
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        generate_latest,
        CollectorRegistry,
    )
except ImportError as e:
    logger.critical(
        f"CRITICAL: prometheus_client not found. Metrics are mandatory. Aborting startup: {e}."
    )
    alert_operator(
        "CRITICAL: prometheus_client missing. Pub/Sub plugin aborted.",
        level="CRITICAL",
    )
    sys.exit(1)

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
class PubSubSettings(BaseSettings):
    """Manages all Pub/Sub configuration using Pydantic."""

    model_config = SettingsConfigDict(env_prefix="PUBSUB_")

    project_id: str = Field(..., min_length=1, description="GCP Project ID.")
    topic_id: str = Field(..., min_length=1, description="Pub/Sub Topic ID.")

    # Publisher client batching settings
    batch_max_messages: int = Field(1000, ge=1, description="Maximum messages per batch.")
    batch_max_bytes: int = Field(
        1024 * 1024, ge=1, description="Maximum bytes per batch (1MB default)."
    )
    batch_max_latency_sec: float = Field(
        0.5, ge=0.01, description="Maximum time to wait for a batch to fill."
    )

    # Gateway queue and worker settings
    max_queue_size: int = Field(
        10000,
        ge=0,
        description="Maximum number of events to buffer in the internal queue.",
    )
    worker_batch_size: int = Field(
        500, ge=1, description="How many messages the worker pulls from queue at once."
    )
    worker_linger_sec: float = Field(
        0.2, ge=0.01, description="How long the worker waits to build a batch."
    )
    num_workers: int = Field(
        4, ge=1, description="Number of concurrent workers to process the queue."
    )

    # Resilience patterns
    circuit_breaker_threshold: int = Field(
        5,
        ge=1,
        description="Number of consecutive failures to trip the circuit breaker.",
    )
    circuit_breaker_reset_sec: int = Field(
        30,
        ge=1,
        description="Time in seconds before a tripped circuit breaker attempts to reset.",
    )

    # Dry Run and Main/Example: The dry_run setting must not be present or executable in production builds.
    dry_run: bool = Field(False, description="If true, events are logged but not sent to Pub/Sub.")

    audit_schema_version: int = Field(1, ge=1, description="Version of the audit event schema.")

    # Topic and Project ID Enforcement: MANDATORY: project_id and topic_id must be on an operator-managed allowlist.
    allowed_project_ids: Optional[List[str]] = Field(
        None, description="List of allowed GCP Project IDs in production."
    )
    allowed_topic_ids: Optional[List[str]] = Field(
        None, description="List of allowed Pub/Sub Topic IDs in production."
    )

    # Credentials & Secret Management: NEVER source GCP credentials from ENV or plaintext files in production.
    # This config field is a placeholder for a secret ID if using a vault.
    gcp_credentials_secret_id: Optional[str] = Field(
        None,
        description="Secret ID for GCP credentials (e.g., service account key JSON) in a secure vault.",
    )

    @validator("project_id")
    def validate_project_id_in_prod(cls, v, values):
        # Cache allowed project IDs
        async def _get_allowed_project_ids():
            if REDIS_CLIENT:
                cache_key = "pubsub_allowed_project_ids"
                cached = await REDIS_CLIENT.get(cache_key)
                if cached:
                    return json.loads(cached)

            allowed_ids_str = SECRETS_MANAGER.get_secret(
                "PUBSUB_ALLOWED_PROJECT_IDS", required=PRODUCTION_MODE
            )
            if not allowed_ids_str:
                return []

            allowed_ids = [i.strip() for i in allowed_ids_str.split(",")]
            if REDIS_CLIENT:
                await REDIS_CLIENT.setex(cache_key, 3600, json.dumps(allowed_ids))
            return allowed_ids

        allowed_project_ids = asyncio.run(_get_allowed_project_ids())
        values["allowed_project_ids"] = allowed_project_ids

        # Topic and Project ID Enforcement: Block/abort if project_id is not explicitly authorized.
        if PRODUCTION_MODE:
            if allowed_project_ids:
                if v not in allowed_project_ids:
                    raise ValueError(
                        f"Project ID '{v}' is not in the 'allowed_project_ids' list: {allowed_project_ids}."
                    )
            else:
                raise ValueError(
                    "In PRODUCTION_MODE, 'allowed_project_ids' list must be configured and non-empty."
                )

            if "dummy" in v.lower() or "test" in v.lower() or "mock" in v.lower():
                raise ValueError(
                    f"Dummy/test Project ID '{v}' detected in PRODUCTION_MODE. Not allowed."
                )
        return v

    @validator("topic_id")
    def validate_topic_id_in_prod(cls, v, values):
        # Cache allowed topic IDs
        async def _get_allowed_topic_ids():
            if REDIS_CLIENT:
                cache_key = "pubsub_allowed_topic_ids"
                cached = await REDIS_CLIENT.get(cache_key)
                if cached:
                    return json.loads(cached)

            allowed_ids_str = SECRETS_MANAGER.get_secret(
                "PUBSUB_ALLOWED_TOPIC_IDS", required=PRODUCTION_MODE
            )
            if not allowed_ids_str:
                return []

            allowed_ids = [i.strip() for i in allowed_ids_str.split(",")]
            if REDIS_CLIENT:
                await REDIS_CLIENT.setex(cache_key, 3600, json.dumps(allowed_ids))
            return allowed_ids

        allowed_topic_ids = asyncio.run(_get_allowed_topic_ids())
        values["allowed_topic_ids"] = allowed_topic_ids

        # Topic and Project ID Enforcement: Block/abort if topic_id is not explicitly authorized.
        if PRODUCTION_MODE:
            if allowed_topic_ids:
                if v not in allowed_topic_ids:
                    raise ValueError(
                        f"Topic ID '{v}' is not in the 'allowed_topic_ids' list: {allowed_topic_ids}."
                    )
            else:
                raise ValueError(
                    "In PRODUCTION_MODE, 'allowed_topic_ids' list must be configured and non-empty."
                )

            if "dummy" in v.lower() or "test" in v.lower() or "mock" in v.lower():
                raise ValueError(
                    f"Dummy/test Topic ID '{v}' detected in PRODUCTION_MODE. Not allowed."
                )
        return v

    @validator("gcp_credentials_secret_id")
    def validate_gcp_credentials_source(cls, v):
        # Credentials & Secret Management: Fail startup if credentials are missing or not loaded securely.
        if PRODUCTION_MODE and not v:
            raise ValueError(
                "In PRODUCTION_MODE, 'gcp_credentials_secret_id' must be provided for GCP credentials. Default ADC/ENV is forbidden."
            )
        return v

    @validator("dry_run")
    def validate_dry_run_in_prod(cls, v):
        # Dry Run and Main/Example: The dry_run setting must not be present or executable in production builds.
        if PRODUCTION_MODE and v:
            raise ValueError("In PRODUCTION_MODE, 'dry_run' must be False.")
        return v


# Instantiate settings globally (this will trigger validation at startup)
try:
    settings = PubSubSettings()
except ValidationError as e:
    logger.critical(
        f"CRITICAL: PubSubSettings validation failed: {e}. Aborting startup.",
        exc_info=True,
    )
    alert_operator(f"CRITICAL: PubSubSettings validation failed: {e}. Aborting.", level="CRITICAL")
    sys.exit(1)
except Exception as e:
    logger.critical(
        f"CRITICAL: Unexpected error loading PubSubSettings: {e}. Aborting startup.",
        exc_info=True,
    )
    alert_operator(
        f"CRITICAL: Unexpected error loading PubSubSettings: {e}. Aborting.",
        level="CRITICAL",
    )
    sys.exit(1)


# ---- 2. Granular, Labeled Metrics (REQUIRED) ----
class PubSubMetrics:
    """Holds all Prometheus metrics for the Pub/Sub gateway."""

    def __init__(self, registry: CollectorRegistry):
        self.EVENTS_QUEUED = Counter(
            "pubsub_audit_events_queued_total",
            "Events placed into the send queue.",
            ["event_name"],
            registry=registry,
        )
        self.EVENTS_DROPPED = Counter(
            "pubsub_audit_events_dropped_total",
            "Events dropped due to a full queue.",
            ["event_name"],
            registry=registry,
        )
        self.EVENTS_PUBLISHED_SUCCESS = Counter(
            "pubsub_audit_events_published_success_total",
            "Events successfully published to Pub/Sub.",
            ["topic"],
            registry=registry,
        )
        self.EVENTS_FAILED_PERMANENTLY = Counter(
            "pubsub_audit_events_failed_permanently_total",
            "Events that failed to publish after all retries.",
            ["topic", "reason"],
            registry=registry,
        )
        self.PUBLISH_LATENCY = Histogram(
            "pubsub_audit_publish_latency_seconds",
            "Latency of a successful batch publish operation.",
            ["topic"],
            registry=registry,
        )
        self.CIRCUIT_BREAKER_STATUS = Gauge(
            "pubsub_audit_circuit_breaker_status",
            "The status of the circuit breaker (1 for open, 0 for closed).",
            registry=registry,
        )
        self.QUEUE_SIZE = Gauge(
            "pubsub_audit_queue_current_size",
            "Current number of events in the Pub/Sub send queue.",
            registry=registry,
        )


# Instantiate metrics globally
_metrics_registry_instance = CollectorRegistry()
try:
    metrics = PubSubMetrics(_metrics_registry_instance)
    logger.info("Prometheus metrics initialized.")
except Exception as e:
    logger.critical(
        f"CRITICAL: Failed to initialize Prometheus metrics: {e}. Aborting startup.",
        exc_info=True,
    )
    alert_operator(
        "CRITICAL: Prometheus metrics initialization failed. Pub/Sub plugin aborted.",
        level="CRITICAL",
    )
    sys.exit(1)


# ---- 3. Validated Event Schema (REQUIRED) ----
class AuditEvent(BaseModel):
    """Defines the structure for a consistent, validated audit event."""

    event_name: str = Field(..., min_length=1)
    service_name: str = Field(..., min_length=1)
    timestamp: float = Field(default_factory=time.time, ge=0)
    details: Dict[str, Any] = Field(default_factory=dict)
    schema_version: int = Field(settings.audit_schema_version, const=True)

    @validator("details")
    def validate_details_for_pii(cls, v):
        scrubbed_data = scrub_sensitive_data(v)
        if scrubbed_data != v:
            raise AnalyzerCriticalError(
                f"Sensitive data detected in audit event details during schema validation. Original: {scrub_sensitive_data(str(v)[:100])}"
            )
        return v


# ---- 4. Standalone Circuit Breaker (REQUIRED) ----
class CircuitBreaker:
    """A standalone circuit breaker to prevent hammering a failing service."""

    def __init__(self, threshold: int, reset_seconds: int, metrics: PubSubMetrics):
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
                audit_logger.log_event("pubsub_circuit_breaker_reset", status="closed")
                alert_operator(
                    "INFO: Pub/Sub Circuit Breaker RESET. Resuming publishes.",
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
                    "pubsub_circuit_breaker_tripped",
                    status="open",
                    failure_count=self._failure_count,
                )
                alert_operator(
                    f"CRITICAL: Pub/Sub Circuit Breaker TRIPPED. Publishes suspended. Failures: {self._failure_count}",
                    level="CRITICAL",
                )

    def record_success(self):
        self._failure_count = 0


# ---- 5. The Divine Pub/Sub Gateway (with Production Hardening) ----
class PubSubGateway:
    """A highly resilient, performant, and observable Google Cloud Pub/Sub publisher."""

    def __init__(self, settings: PubSubSettings, metrics: PubSubMetrics):
        self.settings = settings
        self.metrics = metrics
        self.circuit_breaker = CircuitBreaker(
            settings.circuit_breaker_threshold,
            settings.circuit_breaker_reset_sec,
            metrics,
        )
        self._publisher_client: Optional[pubsub_v1.PublisherClient] = None

        self._event_queue = asyncio.Queue(maxsize=settings.max_queue_size)
        self._worker_tasks: List[asyncio.Task] = []
        self._topic_path = ""
        self._gcp_credentials = None
        self._sem = asyncio.Semaphore(settings.num_workers * 2)  # Client-side rate limiting

        # Define the custom retry policy based on Google's best practices
        self.custom_retry = api_retry.Retry(
            initial=0.1,  # seconds
            maximum=60.0,
            multiplier=1.3,
            deadline=300.0,  # Total retry timeout
            predicate=api_retry.if_exception_type(
                google_exceptions.Aborted,
                google_exceptions.DeadlineExceeded,
                google_exceptions.InternalServerError,
                google_exceptions.ResourceExhausted,
                google_exceptions.ServiceUnavailable,
                google_exceptions.Unknown,
                google_exceptions.Cancelled,
            ),
        )

        logger.info(
            "Pub/Sub Gateway initialized.",
            extra={
                "context": {
                    "project_id": self.settings.project_id,
                    "topic_id": self.settings.topic_id,
                }
            },
        )
        audit_logger.log_event(
            "pubsub_gateway_init",
            project_id=self.settings.project_id,
            topic_id=self.settings.topic_id,
            max_queue_size=self.settings.max_queue_size,
        )

    async def _load_gcp_credentials(self):
        """Loads GCP credentials securely from a secret manager."""
        if PRODUCTION_MODE:
            gcp_credentials_secret_id = self.settings.gcp_credentials_secret_id
            if not gcp_credentials_secret_id:
                raise AnalyzerCriticalError(
                    "In PRODUCTION_MODE, 'gcp_credentials_secret_id' must be provided for GCP credentials. Aborting startup."
                )
            try:
                credentials_json = SECRETS_MANAGER.get_secret(
                    gcp_credentials_secret_id, required=True
                )
                self._gcp_credentials = service_account.Credentials.from_service_account_info(
                    json.loads(credentials_json)
                )
                logger.info("GCP credentials loaded securely from secrets manager.")
                audit_logger.log_event("gcp_credentials_loaded", source="secrets_manager")
            except Exception as e:
                raise AnalyzerCriticalError(
                    f"Failed to load GCP credentials from secret manager '{gcp_credentials_secret_id}': {e}"
                )
        else:
            logger.warning(
                "Using Google Application Default Credentials (ADC). Not recommended for production without explicit control."
            )
            audit_logger.log_event("gcp_credentials_loaded", source="adc_fallback")
            self._gcp_credentials = None

    async def startup(self):
        """Initializes the PublisherClient and starts the background worker."""
        if not self._worker_tasks:
            await self._load_gcp_credentials()

            self._publisher_client = pubsub_v1.PublisherClient(
                batch_settings=pubsub_v1.types.BatchSettings(
                    max_messages=self.settings.batch_max_messages,
                    max_bytes=self.settings.batch_max_bytes,
                    max_latency=self.settings.batch_max_latency_sec,
                ),
                credentials=self._gcp_credentials,
            )
            self._topic_path = self._publisher_client.topic_path(
                self.settings.project_id, self.settings.topic_id
            )

            try:
                await asyncio.to_thread(
                    self._publisher_client.get_topic,
                    request={"topic": self._topic_path},
                )
                logger.info(f"Pub/Sub Topic '{self.settings.topic_id}' validated successfully.")
            except google_exceptions.NotFound:
                raise AnalyzerCriticalError(
                    f"Pub/Sub Topic '{self.settings.topic_id}' not found. Aborting startup."
                )
            except Exception as e:
                raise AnalyzerCriticalError(
                    f"Failed to validate Pub/Sub Topic '{self.settings.topic_id}': {e}. Aborting startup."
                )

            self._worker_tasks = [
                asyncio.create_task(self._worker()) for _ in range(self.settings.num_workers)
            ]
            logger.info(
                f"Pub/Sub Gateway started with {self.settings.num_workers} workers.",
                extra={"context": {"topic": self._topic_path}},
            )
            audit_logger.log_event(
                "pubsub_gateway_startup",
                status="success",
                num_workers=self.settings.num_workers,
            )

    async def shutdown(self):
        """
        Graceful Shutdown: REQUIRED: On shutdown, ensure all queued events are flushed and published.
        """
        logger.info("Pub/Sub Gateway shutting down. Draining queue...")
        audit_logger.log_event(
            "pubsub_gateway_shutdown_start", queue_size=self._event_queue.qsize()
        )

        # Send sentinel value for each worker to exit
        for _ in self._worker_tasks:
            await self._event_queue.put(None)

        try:
            await asyncio.wait_for(
                asyncio.gather(*self._worker_tasks),
                timeout=self.settings.batch_max_latency_sec * self.settings.num_workers * 2 + 10,
            )
            logger.info("Pub/Sub Gateway worker tasks finished.")
            audit_logger.log_event("pubsub_gateway_worker_finished")
        except asyncio.TimeoutError:
            raise AnalyzerCriticalError(
                "Pub/Sub Gateway worker tasks timed out during shutdown. Some messages may be lost."
            )
        except Exception as e:
            raise AnalyzerCriticalError(f"Unexpected error during worker task shutdown: {e}.")

        if self._publisher_client:
            try:
                await asyncio.to_thread(self._publisher_client.stop)
                logger.info("Pub/Sub PublisherClient stopped.")
                audit_logger.log_event("pubsub_publisher_client_stopped", status="success")
            except Exception as e:
                raise AnalyzerCriticalError(f"Failed to stop Pub/Sub PublisherClient cleanly: {e}.")

        logger.info("Pub/Sub Gateway shutdown complete.")
        audit_logger.log_event("pubsub_gateway_shutdown_complete", status="success")

    def publish(self, event_name: str, service_name: str, details: Dict[str, Any]):
        """Validates and enqueues an event for publishing. Returns instantly."""
        try:
            event = AuditEvent(event_name=event_name, service_name=service_name, details=details)

            self._event_queue.put_nowait(event)
            self.metrics.EVENTS_QUEUED.labels(event_name=event_name).inc()
            self.metrics.QUEUE_SIZE.set(self._event_queue.qsize())
            audit_logger.log_event(
                "pubsub_event_queued",
                event_name=event_name,
                queue_size=self._event_queue.qsize(),
            )
        except asyncio.QueueFull:
            self.metrics.EVENTS_DROPPED.labels(event_name=event_name).inc()
            logger.critical(
                "CRITICAL: Pub/Sub event queue is full. Dropping event.",
                extra={
                    "context": {
                        "event_name": event_name,
                        "queue_size": self.settings.max_queue_size,
                    }
                },
            )
            audit_logger.log_event(
                "pubsub_event_dropped",
                event_name=event_name,
                reason="queue_full",
                queue_size=self.settings.max_queue_size,
            )
            alert_operator(
                f"CRITICAL: Pub/Sub event queue is FULL ({self.settings.max_queue_size} events). Events are being dropped. IMMEDIATE ACTION REQUIRED!",
                level="CRITICAL",
            )
        except ValidationError as e:
            logger.error(
                f"Invalid event schema for event '{event_name}': {e}",
                extra={"context": {"error": str(e)}},
            )
            audit_logger.log_event(
                "pubsub_event_validation_failed", event_name=event_name, error=str(e)
            )
            alert_operator(
                f"CRITICAL: Pub/Sub event validation failed for '{event_name}': {e}. Aborting.",
                level="CRITICAL",
            )
        except AnalyzerCriticalError as e:
            # This is raised by the PII validator; log and alert
            logger.critical(f"CRITICAL: {e}")
            audit_logger.log_event("pubsub_pii_validation_failed", event_name=event_name)
            alert_operator(
                f"CRITICAL: Pub/Sub event PII validation failed for '{event_name}': {e}.",
                level="CRITICAL",
            )
        except Exception as e:
            raise AnalyzerCriticalError(f"Unexpected error enqueueing event '{event_name}': {e}")

    async def _publish_batch(self, batch: List[AuditEvent]):
        with _tracer.start_as_current_span(
            "pubsub_publish_batch",
            attributes={"batch.size": len(batch), "topic": self.settings.topic_id},
        ) as span:
            try:
                self.circuit_breaker.check()
            except ConnectionAbortedError as e:
                logger.warning(
                    "Publish skipped due to open circuit breaker.",
                    extra={"context": {"error": str(e)}},
                )
                self.metrics.EVENTS_FAILED_PERMANENTLY.labels(
                    topic=self.settings.topic_id, reason="circuit_breaker"
                ).inc(len(batch))
                audit_logger.log_event(
                    "pubsub_publish_skipped",
                    reason="circuit_breaker_open",
                    batch_size=len(batch),
                )
                return

            futures = []
            async with self._sem:
                for event in batch:
                    data = event.model_dump_json().encode("utf-8")
                    future = self._publisher_client.publish(
                        self._topic_path, data, retry=self.custom_retry
                    )
                    futures.append(future)

            start_time = time.monotonic()
            try:
                await asyncio.gather(*futures)

                duration = time.monotonic() - start_time
                self.metrics.PUBLISH_LATENCY.labels(topic=self.settings.topic_id).observe(duration)
                self.metrics.EVENTS_PUBLISHED_SUCCESS.labels(topic=self.settings.topic_id).inc(
                    len(batch)
                )
                self.circuit_breaker.record_success()
                if span:
                    span.set_status(trace.Status(trace.StatusCode.OK))
                logger.info(
                    f"Successfully published batch of {len(batch)} events to Pub/Sub.",
                    extra={"topic": self.settings.topic_path},
                )
                audit_logger.log_event(
                    "pubsub_publish_success",
                    batch_size=len(batch),
                    topic=self.settings.topic_id,
                    duration=duration,
                )
            except (
                google_exceptions.RetryError,
                google_exceptions.ServiceUnavailable,
                google_exceptions.DeadlineExceeded,
            ) as e:
                self.circuit_breaker.record_failure()
                self.metrics.EVENTS_FAILED_PERMANENTLY.labels(
                    topic=self.settings.topic_id, reason="service_unavailable"
                ).inc(len(batch))
                logger.critical(
                    f"CRITICAL: Failed to publish batch after retries (service unavailable/timeout): {e}.",
                    exc_info=True,
                )
                if span:
                    span.record_exception(e)
                if span:
                    span.set_status(trace.Status(trace.StatusCode.ERROR, "Publish failed"))
                audit_logger.log_event(
                    "pubsub_publish_failure",
                    batch_size=len(batch),
                    topic=self.settings.topic_id,
                    error=str(e),
                    reason="service_unavailable",
                )
                alert_operator(
                    f"CRITICAL: Pub/Sub publish failed after retries for topic {self.settings.topic_id}: {e}.",
                    level="CRITICAL",
                )
                raise AnalyzerCriticalError("Pub/Sub publish failed after retries.")
            except Exception as e:
                self.circuit_breaker.record_failure()
                logger.critical(
                    f"CRITICAL: Unhandled exception during Pub/Sub batch publish: {e}.",
                    exc_info=True,
                )
                if span:
                    span.record_exception(e)
                if span:
                    span.set_status(trace.Status(trace.StatusCode.ERROR, "Unhandled publish error"))
                audit_logger.log_event(
                    "pubsub_publish_failure",
                    batch_size=len(batch),
                    topic=self.settings.topic_id,
                    error=str(e),
                    reason="unhandled_exception",
                )
                alert_operator(
                    f"CRITICAL: Unhandled exception during Pub/Sub publish: {e}.",
                    level="CRITICAL",
                )
                raise AnalyzerCriticalError("Unhandled exception during Pub/Sub publish.")

    async def _worker(self):
        while True:
            try:
                batch = []
                first_event = await self._event_queue.get()
                if first_event is None:
                    self._event_queue.task_done()
                    logger.info("Received shutdown sentinel. Exiting event processor.")
                    break
                batch.append(first_event)

                while len(batch) < self.settings.worker_batch_size:
                    try:
                        event = await asyncio.wait_for(
                            self._event_queue.get(), self.settings.worker_linger_sec
                        )
                        if event is None:
                            self._event_queue.task_done()
                            self._event_queue.put_nowait(None)  # Re-add sentinel for other workers
                            break
                        batch.append(event)
                    except asyncio.TimeoutError:
                        break

                if self.settings.dry_run:
                    logger.info(
                        "[DRY RUN] Would publish batch.",
                        extra={"context": {"batch_size": len(batch)}},
                    )
                    audit_logger.log_event("pubsub_dry_run_publish", batch_size=len(batch))
                    for event in batch:
                        self._event_queue.task_done()
                    continue

                await self._publish_batch(batch)
                for event in batch:
                    self._event_queue.task_done()

            except asyncio.CancelledError:
                logger.info("Pub/Sub worker task cancelled.")
                break
            except Exception as e:
                raise AnalyzerCriticalError(f"Unhandled exception in Pub/Sub worker: {e}.")

        logger.info("Event processor task finished.")

    async def __aenter__(self):
        await self.startup()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.shutdown()


pubsub_gateway = PubSubGateway(settings, metrics)

# --- Health Check Endpoint for Kubernetes ---
try:
    from aiohttp import web

    async def health(_):
        return web.json_response(
            {
                "status": "healthy",
                "queue_size": pubsub_gateway._event_queue.qsize(),
                "circuit_breaker_status": (
                    "closed" if not pubsub_gateway.circuit_breaker._is_open else "open"
                ),
            }
        )

    async def run_health_check_server():
        app = web.Application()
        app.add_routes([web.get("/health", health)])
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 8080)
        await site.start()
        logger.info("Health check server started on :8080/health.")

except ImportError:
    web = None
    logger.warning("aiohttp not found. Health check server will not be available.")

if not PRODUCTION_MODE:

    async def app_lifecycle():
        health_server_task = None
        if web:
            health_server_task = asyncio.create_task(run_health_check_server())

        await pubsub_gateway.startup()
        try:
            yield
        finally:
            await pubsub_gateway.shutdown()
            if health_server_task:
                health_server_task.cancel()
                try:
                    await health_server_task
                except asyncio.CancelledError:
                    pass

    async def main():
        async with app_lifecycle():
            logger.info("Divine Pub/Sub Gateway example started.")

            pubsub_gateway.publish(
                "user_logged_in",
                "auth-service",
                {"user_id": "user-123", "ip_address": "192.168.1.1"},
            )
            pubsub_gateway.publish(
                "payment_processed",
                "billing-service",
                {"amount": 99.99, "currency": "USD"},
            )

            logger.info("Main application logic continues immediately after publishing.")

            # Allow time for workers to process and publish events
            await asyncio.sleep(5)

    if __name__ == "__main__":
        try:
            asyncio.run(main())
        except SystemExit:
            pass
        except Exception as e:
            logger.critical(f"Unhandled exception during example run: {e}", exc_info=True)
            alert_operator(
                f"CRITICAL: Unhandled exception during Pub/Sub example run: {e}.",
                level="CRITICAL",
            )

else:
    if __name__ == "__main__":
        logger.critical(
            "CRITICAL: Attempted to run example/test code in PRODUCTION_MODE. This file should not be executed directly in production."
        )
        alert_operator(
            "CRITICAL: Pub/Sub plugin example code executed in PRODUCTION_MODE. Aborting.",
            level="CRITICAL",
        )
        sys.exit(1)
