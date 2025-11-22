import os
import asyncio
import time
import logging
import sys
import re
import hmac
import hashlib
import json
import datetime
import contextlib
import random

from typing import Dict, Any, Optional, Literal, List


# --- Custom Exceptions (defined early for immediate use) ---
class StartupCriticalError(Exception):
    """
    Exception for critical errors during startup that should halt execution.
    """

    pass


class PagerDutyEventError(Exception):
    """
    Exception for errors related to PagerDuty events.
    """

    pass


# --- Centralized Utilities (replacing placeholders) ---
try:
    from plugins.core_utils import alert_operator, scrub_secrets as scrub_sensitive_data
    from plugins.core_audit import audit_logger
    from plugins.core_secrets import SECRETS_MANAGER
except ImportError as e:
    # A cleaner alternative to sys.exit at the top level, let's keep it here for now
    # as it's a critical dependency.
    raise StartupCriticalError(
        f"CRITICAL: Missing core dependency for PagerDuty plugin: {e}. Aborting startup."
    )

# --- Global Production Mode Flag (from main orchestrator) ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"


# --- Logging Setup ---
class PagerDutyJsonFormatter(logging.Formatter):
    """
    A JSON formatter that includes standard log record fields and merges a specific
    'extra' dictionary directly into the JSON object.
    """

    def format(self, record):
        log_entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        # Merge the user-provided 'context' dictionary if it exists
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            log_entry["context"] = context

        if record.exc_info:
            log_entry["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False, default=str)


logger = logging.getLogger("pagerduty_audit_plugin")
if not logger.handlers:
    LOG_FILE_PATH = os.getenv(
        "PAGERDUTY_PLUGIN_LOG_FILE", "/var/log/pagerduty_plugin.log"
    )
    handler: logging.Handler
    if PRODUCTION_MODE:
        try:
            os.makedirs(os.path.dirname(LOG_FILE_PATH) or ".", exist_ok=True)
            handler = logging.FileHandler(LOG_FILE_PATH)
            os.chmod(LOG_FILE_PATH, 0o600)
            handler.setFormatter(PagerDutyJsonFormatter())
        except Exception as e:
            alert_operator(
                f"CRITICAL: PagerDuty plugin failed to configure file logging to {LOG_FILE_PATH}: {e}. Aborting.",
                level="CRITICAL",
            )
            raise StartupCriticalError(
                f"CRITICAL: PagerDuty plugin failed to configure file logging: {e}."
            )
    else:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")
        handler.setFormatter(formatter)

    logger.addHandler(handler)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

# --- Caching: Redis Client Initialization ---
REDIS_CLIENT: Optional["redis.Redis"] = None
try:
    import redis.asyncio as redis
except ImportError:
    logger.info("Redis not installed; caching will be disabled.")
    redis = None


async def get_redis_client() -> Optional["redis.Redis"]:
    global REDIS_CLIENT
    if REDIS_CLIENT:
        return REDIS_CLIENT

    if not redis:
        return None

    try:
        REDIS_CLIENT = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=0,
            decode_responses=True,
        )
        await REDIS_CLIENT.ping()
        logger.info("Successfully connected to Redis for caching.")
        return REDIS_CLIENT
    except Exception as e:
        logger.warning(
            f"Failed to connect to Redis for caching: {e}. Caching will be disabled."
        )
        REDIS_CLIENT = None
        return None


# --- Dependency Gating ---
try:
    import aiohttp
    from pydantic import (
        BaseModel,
        Field,
        ValidationError,
        field_validator,
        model_validator,
    )
    from pydantic_settings import BaseSettings, SettingsConfigDict
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        CollectorRegistry,
        REGISTRY,
        Info,
    )
except ImportError as e:
    logger.critical(
        f"CRITICAL: Missing core dependency for PagerDuty plugin: {e}. Aborting startup."
    )
    alert_operator(
        f"CRITICAL: PagerDuty plugin missing core dependency: {e}. Aborting.",
        level="CRITICAL",
    )
    raise StartupCriticalError(f"CRITICAL: Missing core dependency: {e}.")


# ---- 1. Gold Standard: Centralized & Validated Settings ----
class PagerDutySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAGERDUTY_")

    routing_key_secret_id: str = Field(
        ..., description="Secret ID for routing_key in a secure vault."
    )
    timeout_seconds: float = Field(
        10.0, ge=1.0, description="Timeout for HTTP requests to PagerDuty API."
    )
    max_retries: int = Field(
        3, ge=0, description="Maximum number of retries for sending an event."
    )
    retry_backoff_factor: float = Field(
        2.0, ge=1.0, description="Exponential backoff factor for retries."
    )
    dry_run: bool = Field(
        False, description="If true, events are logged but not sent to PagerDuty."
    )
    max_concurrent_requests: int = Field(
        10, ge=1, description="Maximum concurrent HTTP requests to PagerDuty API."
    )
    max_queue_size: int = Field(
        1000,
        ge=0,
        description="Maximum number of events to buffer in the internal queue.",
    )
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
    pagerduty_api_url: str = Field(
        "https://events.pagerduty.com/v2/enqueue",
        description="PagerDuty Events API endpoint.",
    )

    @model_validator(mode="after")
    def validate_production_mode_settings(self) -> "PagerDutySettings":
        if PRODUCTION_MODE:
            if self.dry_run:
                raise ValueError("In PRODUCTION_MODE, 'dry_run' must be False.")
            if not self.pagerduty_api_url.lower().startswith("https://"):
                raise ValueError(
                    f"Non-HTTPS endpoint '{self.pagerduty_api_url}' detected in PRODUCTION_MODE. HTTPS is mandatory."
                )

            allowed_urls_str = SECRETS_MANAGER.get_secret(
                "PAGERDUTY_ALLOWED_URLS", required=True
            )
            allowed_endpoints = {u.strip() for u in allowed_urls_str.split(",")}
            if self.pagerduty_api_url not in allowed_endpoints:
                raise ValueError(
                    f"PagerDuty API URL '{self.pagerduty_api_url}' is not in the 'allowed_pagerduty_api_urls' list."
                )

            if any(
                term in self.pagerduty_api_url.lower()
                for term in ["dummy", "test", "mock", "example.com"]
            ):
                raise ValueError(
                    f"Dummy/test PagerDuty API URL '{self.pagerduty_api_url}' detected in PRODUCTION_MODE."
                )

        return self


# Instantiate settings globally (this will trigger validation at startup)
try:
    settings = PagerDutySettings()
except ValidationError as e:
    logger.critical(
        f"CRITICAL: PagerDutySettings validation failed: {e}. Aborting startup.",
        extra={"validation_errors": e.errors()},
    )
    alert_operator(
        f"CRITICAL: PagerDutySettings validation failed: {e}. Aborting.",
        level="CRITICAL",
    )
    raise StartupCriticalError("PagerDutySettings validation failed.")
except Exception as e:
    logger.critical(
        f"CRITICAL: Unexpected error loading PagerDutySettings: {e}. Aborting startup.",
        exc_info=True,
    )
    alert_operator(
        f"CRITICAL: Unexpected error loading PagerDutySettings: {e}. Aborting.",
        level="CRITICAL",
    )
    raise StartupCriticalError("Unexpected error loading PagerDutySettings.")


# ---- 2. Gold Standard: Structured, Machine-Readable Logging ----
# Logger setup is already done at the top of the file.


# ---- 3. Gold Standard: Granular, Labeled Metrics (MANDATORY) ----
class PagerDutyMetrics:
    def __init__(self, registry: CollectorRegistry):
        self.INFO = Info(
            "pagerduty_audit_plugin_info",
            "A static info metric with plugin metadata.",
            registry=registry,
        )
        self.INFO.info(
            {"version": "1.0.0", "env": "prod" if PRODUCTION_MODE else "dev"}
        )

        self.EVENTS_QUEUED = Counter(
            "pagerduty_audit_events_queued_total",
            "Total number of audit events placed into the send queue.",
            ["severity"],
            registry=registry,
        )
        self.EVENTS_DROPPED = Counter(
            "pagerduty_audit_events_dropped_total",
            "Events dropped because the send queue was full or circuit breaker was open.",
            registry=registry,
        )
        self.EVENTS_SENT_SUCCESS = Counter(
            "pagerduty_audit_events_sent_success_total",
            "Events successfully sent to PagerDuty after retries.",
            ["action"],
            registry=registry,
        )
        self.EVENTS_FAILED_PERMANENTLY = Counter(
            "pagerduty_audit_events_failed_permanently_total",
            "Events that failed permanently after all retries.",
            ["reason"],
            registry=registry,
        )
        self.SEND_LATENCY = Histogram(
            "pagerduty_audit_send_latency_seconds",
            "Latency of sending an event to PagerDuty.",
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
            registry=registry,
        )
        self.CIRCUIT_BREAKER_STATUS = Gauge(
            "pagerduty_audit_circuit_breaker_status",
            "The status of the circuit breaker (1 for open, 0 for closed, 0.5 for half-open).",
            registry=registry,
        )
        self.QUEUE_SIZE = Gauge(
            "pagerduty_audit_queue_current_size",
            "Current number of events in the PagerDuty send queue.",
            registry=registry,
        )


# Use the default Prometheus registry
try:
    metrics = PagerDutyMetrics(REGISTRY)
    logger.info("Prometheus metrics initialized on the default registry.")
except Exception as e:
    logger.critical(
        f"CRITICAL: Failed to initialize Prometheus metrics: {e}. Aborting startup.",
        exc_info=True,
    )
    alert_operator(
        "CRITICAL: Prometheus metrics initialization failed. PagerDuty plugin aborted.",
        level="CRITICAL",
    )
    raise StartupCriticalError("Failed to initialize Prometheus metrics.")


# ---- 4. Gold Standard: Rich & Validated API Schemas (REQUIRED) ----
_ISO8601_Z_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class PagerDutyEventPayload(BaseModel):
    summary: str = Field(..., min_length=1, max_length=1024)
    source: str = Field(..., min_length=1, max_length=255)
    severity: Literal["critical", "error", "warning", "info"]
    timestamp: str
    component: Optional[str] = Field(None, max_length=255)
    group: Optional[str] = Field(None, max_length=255)
    custom_details: Optional[Dict[str, Any]] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        if not _ISO8601_Z_REGEX.match(v):
            raise ValueError(
                "Timestamp must be in ISO 8601 UTC format (YYYY-MM-DDTHH:MM:SSZ)."
            )
        return v

    @model_validator(mode="before")
    @classmethod
    def validate_and_scrub_pii(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            return data

        custom_details = payload.get("custom_details", {})
        if not isinstance(custom_details, dict):
            return data

        scrubbed_details = scrub_sensitive_data(custom_details)
        if scrubbed_details != custom_details:
            logger.warning(
                "Sensitive fields scrubbed from custom_details. PagerDuty event will proceed."
            )
            audit_logger.log_event(
                "pagerduty_custom_details_scrubbed",
                details_snippet=scrub_sensitive_data(str(custom_details)[:100]),
            )
            payload["custom_details"] = scrubbed_details
            data["payload"] = payload

        return data


class PagerDutyAPIRequest(BaseModel):
    routing_key: str = Field(..., min_length=1, max_length=32)
    event_action: Literal["trigger", "acknowledge", "resolve"]
    dedup_key: str = Field(..., min_length=1, max_length=255)
    payload: Optional[PagerDutyEventPayload] = None

    @field_validator("payload")
    @classmethod
    def payload_required_for_trigger(cls, v, info):
        if info.data.get("event_action") == "trigger" and v is None:
            raise ValueError("Payload is required for 'trigger' event_action.")
        return v

    def _sign_request(self) -> str:
        """Generates an HMAC signature for the request payload."""
        request_payload = self.model_dump(exclude={"signature"})
        request_json_str = json.dumps(
            request_payload, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        pagerduty_hmac_key = SECRETS_MANAGER.get_secret(
            "PAGERDUTY_HMAC_KEY", required=PRODUCTION_MODE
        )

        if PRODUCTION_MODE and not pagerduty_hmac_key:
            raise PagerDutyEventError(
                "Missing 'PAGERDUTY_HMAC_KEY' for event signing. Aborting send."
            )

        return hmac.new(
            pagerduty_hmac_key.encode("utf-8"), request_json_str, hashlib.sha256
        ).hexdigest()


# ---- 5. Final Boss: The Resilient, Performant, Decoupled Gateway ----
class PagerDutyGateway:
    """
    Manages the entire lifecycle of PagerDuty communication, including a
    background sending queue, concurrency limiting, and a circuit breaker.
    """

    def __init__(self, settings: PagerDutySettings, metrics: PagerDutyMetrics):
        self.settings = settings
        self.metrics = metrics
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()

        self._event_queue: asyncio.Queue = asyncio.Queue(
            maxsize=self.settings.max_queue_size
        )
        self._workers: List[asyncio.Task] = []

        self._routing_key: Optional[str] = SECRETS_MANAGER.get_secret(
            self.settings.routing_key_secret_id, required=True
        )
        if not self._routing_key:
            raise StartupCriticalError(
                "PagerDuty routing key not found in secrets manager."
            )

        self._circuit_state: Literal["closed", "open", "half_open"] = "closed"
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._half_open_probe_lock = asyncio.Lock()
        self.metrics.CIRCUIT_BREAKER_STATUS.set(0)

        logger.info(
            "PagerDuty Gateway initialized.",
            extra={
                "api_url": self.settings.pagerduty_api_url,
                "max_queue_size": self.settings.max_queue_size,
            },
        )
        audit_logger.log_event(
            "pagerduty_gateway_init",
            api_url=self.settings.pagerduty_api_url,
            max_queue_size=self.settings.max_queue_size,
        )

    async def startup(self):
        """Starts the background worker tasks that process the event queue."""
        if not self._workers:
            for i in range(self.settings.max_concurrent_requests):
                self._workers.append(asyncio.create_task(self._event_processor_task(i)))
            logger.info(
                f"PagerDuty Gateway started with {len(self._workers)} background event processors."
            )
            audit_logger.log_event(
                "pagerduty_gateway_startup",
                status="success",
                worker_count=len(self._workers),
            )

    async def shutdown(self):
        """
        Async Lifecycle Management: Gracefully shuts down the gateway by draining the queue.
        """
        logger.info("PagerDuty Gateway shutting down. Draining queue...")
        audit_logger.log_event(
            "pagerduty_gateway_shutdown_start", queue_size=self._event_queue.qsize()
        )

        for _ in self._workers:
            await self._event_queue.put(None)

        try:
            await asyncio.wait_for(
                self._event_queue.join(),
                timeout=self.settings.timeout_seconds * self.settings.max_retries * 2
                + 10,
            )
            logger.info("PagerDuty Gateway queue drained successfully.")
            audit_logger.log_event(
                "pagerduty_gateway_queue_drained",
                remaining_events=self._event_queue.qsize(),
            )
        except asyncio.TimeoutError:
            remaining_events = self._event_queue.qsize()
            logger.critical(
                f"CRITICAL: PagerDuty Gateway failed to drain queue within timeout. {remaining_events} events remain unsent."
            )
            audit_logger.log_event(
                "pagerduty_gateway_shutdown_failure",
                reason="queue_drain_timeout",
                remaining_events=remaining_events,
            )
            alert_operator(
                f"CRITICAL: PagerDuty Gateway queue NOT drained. {remaining_events} events unsent. Aborting.",
                level="CRITICAL",
            )
            raise StartupCriticalError(
                f"Queue drain timed out with {remaining_events} events remaining."
            )
        except Exception as e:
            logger.critical(
                f"CRITICAL: Unexpected error during queue drain: {e}.", exc_info=True
            )
            audit_logger.log_event(
                "pagerduty_gateway_shutdown_failure",
                reason="queue_drain_error",
                error=str(e),
            )
            alert_operator(
                f"CRITICAL: PagerDuty Gateway queue drain failed unexpectedly: {e}.",
                level="CRITICAL",
            )
            raise StartupCriticalError(f"Unexpected error during queue drain: {e}.")

        for worker in self._workers:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker

        async with self._session_lock:
            if self._session and not self._session.closed:
                await self._session.close()
                self._session = None
                logger.info("Gracefully shut down PagerDuty aiohttp session.")

        logger.info("PagerDuty Gateway shutdown complete.")
        audit_logger.log_event("pagerduty_gateway_shutdown_complete", status="success")

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._session_lock:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(
                    total=self.settings.timeout_seconds, connect=5
                )
                self._session = aiohttp.ClientSession(timeout=timeout, trust_env=True)
        return self._session

    async def _check_circuit_breaker(self):
        """
        Checks the circuit breaker state and handles state transitions.
        """
        if self._circuit_state == "open":
            if (
                time.monotonic() - self._last_failure_time
                > self.settings.circuit_breaker_reset_sec
            ):
                self._circuit_state = "half_open"
                self.metrics.CIRCUIT_BREAKER_STATUS.set(0.5)
                logger.warning(
                    "Circuit breaker in half-open state. Will attempt a single request."
                )
                audit_logger.log_event(
                    "pagerduty_circuit_breaker_half_open", status="half_open"
                )
            else:
                raise PagerDutyEventError(
                    "Circuit breaker is open. PagerDuty requests are temporarily suspended."
                )
        elif self._circuit_state == "half_open":
            if self._half_open_probe_lock.locked():
                raise PagerDutyEventError("Half-open probe already in flight.")
            await self._half_open_probe_lock.acquire()

    def _handle_success(self):
        """Resets failure count on success."""
        if self._circuit_state == "half_open":
            self._circuit_state = "closed"
            self._failure_count = 0
            self.metrics.CIRCUIT_BREAKER_STATUS.set(0)
            if self._half_open_probe_lock.locked():
                self._half_open_probe_lock.release()
            logger.info("Half-open probe succeeded; circuit is now closed.")
            audit_logger.log_event(
                "pagerduty_circuit_breaker_success_reset", status="closed"
            )
            return

        if self._failure_count > 0:
            self._failure_count = 0
            self.metrics.CIRCUIT_BREAKER_STATUS.set(0)
            logger.info("Circuit breaker state reset on success.")

    def _handle_failure(self, event_action: str, dedup_key: str):
        """Increments failure count and trips the circuit if threshold is reached."""
        if self._circuit_state == "half_open":
            self._circuit_state = "open"
            self._last_failure_time = time.monotonic()
            self.metrics.CIRCUIT_BREAKER_STATUS.set(1)
            if self._half_open_probe_lock.locked():
                self._half_open_probe_lock.release()
            logger.error("Half-open probe failed; circuit is now open.")
            alert_operator(
                "CRITICAL: PagerDuty CB probe failed. TRIPPED again.", level="CRITICAL"
            )
            return

        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if (
            self._failure_count >= self.settings.circuit_breaker_threshold
            and self._circuit_state == "closed"
        ):
            self._circuit_state = "open"
            self.metrics.CIRCUIT_BREAKER_STATUS.set(1)
            logger.error("Circuit breaker tripped due to excessive failures.")
            audit_logger.log_event(
                "pagerduty_circuit_breaker_tripped",
                failure_count=self._failure_count,
                event_action=event_action,
                dedup_key=dedup_key,
            )
            alert_operator(
                f"CRITICAL: PagerDuty Circuit Breaker TRIPPED. Requests suspended. Failures: {self._failure_count}",
                level="CRITICAL",
            )

    async def _send_request(self, request: PagerDutyAPIRequest):
        """The core logic for sending a single, prepared request with retries."""
        await self._check_circuit_breaker()

        request_body = request.model_dump(exclude_none=True, exclude={"signature"})

        attempt = 0
        while attempt <= self.settings.max_retries:
            start_time = time.monotonic()
            log_context = {
                "dedup_key": request.dedup_key,
                "action": request.event_action,
                "attempt": attempt + 1,
            }
            audit_logger.log_event(
                "pagerduty_send_attempt",
                **log_context,
                api_url=self.settings.pagerduty_api_url,
            )

            try:
                session = await self._get_session()
                async with session.post(
                    self.settings.pagerduty_api_url, json=request_body
                ) as resp:
                    duration = time.monotonic() - start_time
                    self.metrics.SEND_LATENCY.observe(duration)

                    if resp.status in [200, 202]:
                        self._handle_success()
                        self.metrics.EVENTS_SENT_SUCCESS.labels(
                            action=request.event_action
                        ).inc()
                        logger.info(
                            "Successfully sent PagerDuty event.",
                            extra={"context": log_context, "status": resp.status},
                        )
                        audit_logger.log_event(
                            "pagerduty_send_success", **log_context, status=resp.status
                        )
                        return

                    text = await resp.text()
                    if resp.status == 429:
                        jitter = random.uniform(0.0, 2.0)
                        try:
                            delay = float(resp.headers.get("Retry-After", "1")) + jitter
                        except (ValueError, TypeError):
                            delay = 1.0 + jitter
                        logger.warning(
                            f"Rate limited by PagerDuty. Retrying in {delay:.2f} seconds.",
                            extra={"context": log_context, "status": resp.status},
                        )
                        await asyncio.sleep(delay)
                        attempt += 1
                        continue

                    if 400 <= resp.status < 500:
                        self._handle_failure(request.event_action, request.dedup_key)
                        self.metrics.EVENTS_FAILED_PERMANENTLY.labels(
                            reason="client_error"
                        ).inc()
                        logger.error(
                            "Permanent PagerDuty client error. Dropping event.",
                            extra={
                                "context": log_context,
                                "status": resp.status,
                                "response": scrub_sensitive_data(text),
                            },
                        )
                        audit_logger.log_event(
                            "pagerduty_send_permanent_failure",
                            **log_context,
                            status=resp.status,
                            response=scrub_sensitive_data(text),
                        )
                        return

                    resp.raise_for_status()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning(
                    "Temporary failure sending to PagerDuty. Retrying...",
                    extra={"context": log_context, "error": str(exc)},
                )
                audit_logger.log_event(
                    "pagerduty_send_retriable_failure", **log_context, error=str(exc)
                )
                if attempt == self.settings.max_retries:
                    self._handle_failure(request.event_action, request.dedup_key)
                    self.metrics.EVENTS_FAILED_PERMANENTLY.labels(
                        reason="server_error"
                    ).inc()
                    logger.error(
                        "Failed to send PagerDuty event after all retries.",
                        extra={"context": log_context},
                    )
                    audit_logger.log_event(
                        "pagerduty_send_final_failure", **log_context, error=str(exc)
                    )
                else:
                    await asyncio.sleep(self.settings.retry_backoff_factor**attempt)
            except Exception as e:
                logger.error(
                    "Unhandled exception in _send_request.",
                    extra={"context": log_context, "error": str(e)},
                    exc_info=True,
                )
                self._handle_failure(request.event_action, request.dedup_key)
                audit_logger.log_event(
                    "pagerduty_send_unhandled_exception", **log_context, error=str(e)
                )
                alert_operator(
                    f"CRITICAL: Unhandled exception sending PagerDuty event: {e}.",
                    level="CRITICAL",
                )
                return
            attempt += 1

    async def _event_processor_task(self, worker_id: int):
        """The background worker that consumes from the queue and sends events."""
        self.metrics.QUEUE_SIZE.set(self._event_queue.qsize())
        logger.info(f"Worker {worker_id} started.")
        while True:
            request = await self._event_queue.get()
            self.metrics.QUEUE_SIZE.set(self._event_queue.qsize())

            if request is None:
                logger.info(f"Worker {worker_id} received shutdown sentinel. Exiting.")
                self._event_queue.task_done()
                break

            if self.settings.dry_run:
                safe_body = request.model_dump(
                    exclude_none=True, exclude={"routing_key"}
                )
                safe_body["payload"]["custom_details"] = scrub_sensitive_data(
                    safe_body["payload"]["custom_details"]
                )
                logger.info(
                    "[DRY RUN] Would send PagerDuty event.",
                    extra={"context": {"event_body": safe_body}},
                )
                audit_logger.log_event(
                    "pagerduty_dry_run_event",
                    dedup_key=request.dedup_key,
                    action=request.event_action,
                )
                self._event_queue.task_done()
                continue

            try:
                await self._send_request(request)
            except PagerDutyEventError as e:
                self.metrics.EVENTS_DROPPED.inc()
                logger.warning(
                    "Event dropped due to error.",
                    extra={
                        "context": {"dedup_key": request.dedup_key, "error": str(e)}
                    },
                )
                audit_logger.log_event(
                    "pagerduty_event_dropped",
                    dedup_key=request.dedup_key,
                    reason="payload_error",
                    error=str(e),
                )
            except Exception as e:
                logger.error(
                    "Unhandled exception in event processor.",
                    extra={"context": {"error": str(e)}},
                    exc_info=True,
                )
                audit_logger.log_event(
                    "pagerduty_event_processor_unhandled_exception",
                    dedup_key=request.dedup_key,
                    error=str(e),
                )
                alert_operator(
                    f"CRITICAL: Unhandled exception in PagerDuty event processor: {e}.",
                    level="CRITICAL",
                )
            finally:
                self._event_queue.task_done()

        logger.info(f"Worker {worker_id} finished.")

    async def _enqueue_request(self, request: PagerDutyAPIRequest):
        """Puts a request onto the queue, logging a failure if full."""
        try:
            if request.payload:
                self.metrics.EVENTS_QUEUED.labels(
                    severity=request.payload.severity
                ).inc()
            await self._event_queue.put(request)
            self.metrics.QUEUE_SIZE.set(self._event_queue.qsize())
        except asyncio.QueueFull:
            self.metrics.EVENTS_DROPPED.inc()
            logger.critical(
                "PagerDuty event queue is full. Dropping event.",
                extra={
                    "dedup_key": request.dedup_key,
                    "queue_size": self.settings.max_queue_size,
                },
            )
            audit_logger.log_event(
                "pagerduty_event_dropped",
                dedup_key=request.dedup_key,
                reason="queue_full",
                queue_size=self.settings.max_queue_size,
            )
            alert_operator(
                f"CRITICAL: PagerDuty event queue is FULL ({self.settings.max_queue_size} events). Events are being dropped. IMMEDIATE ACTION REQUIRED!",
                level="CRITICAL",
            )

    async def trigger(
        self,
        event_name: str,
        details: Dict[str, Any],
        severity: Literal["critical", "error", "warning", "info"],
        source: str,
        dedup_key: str,
    ):
        try:
            timestamp_utc = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            payload = PagerDutyEventPayload(
                summary=f"[{severity.upper()}] {event_name}",
                source=source,
                severity=severity,
                custom_details=details,
                timestamp=timestamp_utc,
            )
            request = PagerDutyAPIRequest(
                routing_key=self._routing_key,
                event_action="trigger",
                dedup_key=dedup_key,
                payload=payload,
            )
        except (ValidationError, PagerDutyEventError) as e:
            logger.error(
                f"PagerDuty event payload validation failed for trigger: {e}",
                extra={
                    "event_name": event_name,
                    "details": scrub_sensitive_data(details),
                },
            )
            audit_logger.log_event(
                "pagerduty_payload_validation_failed",
                event_name=event_name,
                action="trigger",
                error=str(e),
                details_snippet=scrub_sensitive_data(str(details)[:100]),
            )
            alert_operator(
                f"CRITICAL: PagerDuty event payload validation failed for trigger '{event_name}': {e}.",
                level="CRITICAL",
            )
            return

        await self._enqueue_request(request)

    async def acknowledge(self, dedup_key: str):
        try:
            request = PagerDutyAPIRequest(
                routing_key=self._routing_key,
                event_action="acknowledge",
                dedup_key=dedup_key,
            )
        except (ValidationError, PagerDutyEventError) as e:
            logger.error(
                f"PagerDuty event payload validation failed for acknowledge: {e}",
                extra={"dedup_key": dedup_key},
            )
            audit_logger.log_event(
                "pagerduty_payload_validation_failed",
                dedup_key=dedup_key,
                action="acknowledge",
                error=str(e),
            )
            alert_operator(
                f"CRITICAL: PagerDuty event payload validation failed for acknowledge '{dedup_key}': {e}.",
                level="CRITICAL",
            )
            return
        await self._enqueue_request(request)

    async def resolve(self, dedup_key: str):
        try:
            request = PagerDutyAPIRequest(
                routing_key=self._routing_key,
                event_action="resolve",
                dedup_key=dedup_key,
            )
        except (ValidationError, PagerDutyEventError) as e:
            logger.error(
                f"PagerDuty event payload validation failed for resolve: {e}",
                extra={"dedup_key": dedup_key},
            )
            audit_logger.log_event(
                "pagerduty_payload_validation_failed",
                dedup_key=dedup_key,
                action="resolve",
                error=str(e),
            )
            alert_operator(
                f"CRITICAL: PagerDuty event payload validation failed for resolve '{dedup_key}': {e}.",
                level="CRITICAL",
            )
            return
        await self._enqueue_request(request)


pd_settings = settings
pd_metrics = metrics
pagerduty_gateway = PagerDutyGateway(pd_settings, pd_metrics)

if not PRODUCTION_MODE:

    async def app_lifecycle():
        await get_redis_client()
        await pagerduty_gateway.startup()
        try:
            yield
        finally:
            await pagerduty_gateway.shutdown()
            if REDIS_CLIENT:
                await REDIS_CLIENT.close()

    async def main():
        async with app_lifecycle():
            logger.info("Starting PagerDuty gateway example application.")

            incident_key = f"db-conn-fail-{int(time.time())}"
            await pagerduty_gateway.trigger(
                event_name="database_connection_failed",
                details={"db_host": "prod-db-1", "db_pass": "secret_db_pass"},
                severity="critical",
                source="db-monitor",
                dedup_key=incident_key,
            )
            await pagerduty_gateway.trigger(
                event_name="user_login_brute_force",
                details={"ip": "1.2.3.4"},
                severity="warning",
                source="auth-service",
                dedup_key="brute-force-1.2.3.4",
            )

            logger.info("Main application logic continues immediately...")
            await asyncio.sleep(2)

            await pagerduty_gateway.acknowledge(dedup_key=incident_key)
            logger.info(f"Acknowledged incident: {incident_key}")

            await asyncio.sleep(2)

            await pagerduty_gateway.resolve(dedup_key=incident_key)
            logger.info(f"Resolved incident: {incident_key}")

            await asyncio.sleep(5)

    if __name__ == "__main__":
        try:
            asyncio.run(main())
        except (ImportError, RuntimeError) as e:
            print(
                f"Failed to run example: {e}. Please install aiohttp (`pip install aiohttp`) to run the usage example."
            )
        except StartupCriticalError as e:
            print(
                f"Example execution aborted due to critical startup error: {e}",
                file=sys.stderr,
            )
            sys.exit(1)
else:
    if __name__ == "__main__":
        logger.critical(
            "CRITICAL: Attempted to run example/test code in PRODUCTION_MODE. This file should not be executed directly in production."
        )
        alert_operator(
            "CRITICAL: PagerDuty plugin example code executed in PRODUCTION_MODE. Aborting.",
            level="CRITICAL",
        )
        raise StartupCriticalError("Refusing to run example in PRODUCTION_MODE")
