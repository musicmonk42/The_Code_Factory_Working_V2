import asyncio
import hashlib
import hmac
import importlib.metadata
import json
import logging
import os
import random
import re
import socket
import ssl
import sys
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from queue import PriorityQueue
from typing import (
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    List,
    Literal,
    Optional,
    Protocol,
)

import aiofiles
import aiohttp
import psutil
from cryptography.fernet import Fernet
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pythonjsonlogger import jsonlogger


# ---- CUSTOM EXCEPTION CLASSES ----
class AnalyzerCriticalError(Exception):
    """Raised for unrecoverable failures in the analyzer/gateway logic."""

    pass


# ---- PROD MODE ENFORCEMENT ----
PROD_MODE = os.environ.get("PROD_MODE", "false").lower() == "true"

# --- Core Integrations (shared, namespaced for all plugins/gateways) ---
try:
    from core_audit import audit_logger
    from core_secrets import SECRETS_MANAGER
    from core_utils import alert_operator
except ImportError:
    import logging

    def alert_operator(message: str, level: str):
        logging.critical(f"[FALLBACK ALERT] {level} - {message}")

    class DummyAuditLogger:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

        def critical(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

    audit_logger = DummyAuditLogger()

    class DummySecretsManager:
        def get_secret(self, key, required=True):
            value = os.environ.get(key)
            if required and not value:
                alert_operator(
                    f"CRITICAL: Failed to fetch required secret '{key}'. Aborting.",
                    level="CRITICAL",
                )
                sys.exit(1)
            return value

    SECRETS_MANAGER = DummySecretsManager()


# ---- Logging Setup for Tamper-Evident Audit Logs ----
class AuditJsonFormatter(jsonlogger.JsonFormatter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hmac_key = SECRETS_MANAGER.get_secret(
            "SLACK_AUDIT_LOG_HMAC_KEY", required=PROD_MODE
        ).encode()

    def add_fields(self, log_record, message_dict):
        super().add_fields(log_record, message_dict)
        log_record["timestamp"] = time.time()
        log_record["hostname"] = socket.gethostname()
        log_record["service_name"] = "slack_gateway"

        payload = {k: v for k, v in log_record.items() if k not in ["signature"]}
        payload_str = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        signature = hmac.new(
            self._hmac_key, payload_str.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        log_record["signature"] = signature


AUDIT_LOG_PATH = os.environ.get("SLACK_AUDIT_LOG_FILE", "/var/log/slack_audit.log")
try:
    os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
    audit_log_handler = RotatingFileHandler(
        AUDIT_LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    os.chmod(AUDIT_LOG_PATH, 0o600)
except Exception as e:

    class DummyHandler(logging.Handler):
        def emit(self, record):
            pass

    audit_log_handler = DummyHandler()
    logging.getLogger("slack_plugin").warning(
        f"Could not set up audit log file at {AUDIT_LOG_PATH}: {e}. Using a dummy handler."
    )

audit_log_handler.setFormatter(
    AuditJsonFormatter(
        "%(timestamp)s %(hostname)s %(service_name)s %(levelname)s %(message)s"
    )
)
audit_logger = logging.getLogger("slack_audit")
audit_logger.setLevel(logging.INFO)
if not audit_logger.handlers:
    audit_logger.addHandler(audit_log_handler)

main_logger = logging.getLogger("slack_plugin")
main_logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
log_handler = logging.StreamHandler()
log_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
log_handler.setFormatter(log_formatter)
if not main_logger.handlers:
    main_logger.addHandler(log_handler)

# ---- OpenTelemetry for Distributed Tracing ----
OPENTELEMETRY_AVAILABLE = False
tracer = None
TraceContextTextMapPropagator = None

try:
    from opentelemetry import trace
    from opentelemetry.context import get_current
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.trace.sampling import ProbabilitySampler
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )

    resource = Resource(
        attributes={
            SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", "slack-gateway-service")
        }
    )
    trace_provider = TracerProvider(sampler=ProbabilitySampler(0.1), resource=resource)
    trace_exporter = OTLPSpanExporter(
        endpoint=os.environ.get(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://localhost:4318/v1/traces"
        )
    )
    trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
    trace.set_tracer_provider(trace_provider)
    set_global_textmap(TraceContextTextMapPropagator())
    tracer = trace.get_tracer(__name__)
    OPENTELEMETRY_AVAILABLE = True
    AsyncioInstrumentor().instrument()
    main_logger.info("OpenTelemetry initialized and configured.")
except ImportError:

    class MockTracer:
        def start_as_current_span(self, *args, **kwargs):
            @asynccontextmanager
            async def mock_span():
                yield

            return mock_span()

    tracer = MockTracer()
    TraceContextTextMapPropagator = None
    OPENTELEMETRY_AVAILABLE = False
    main_logger.warning(
        "OpenTelemetry SDK not found. Distributed tracing will be disabled."
    )
except Exception as e:
    main_logger.critical(f"Failed to initialize OpenTelemetry: {e}. Exiting.")
    alert_operator(f"Failed to initialize OpenTelemetry: {e}", "CRITICAL")
    sys.exit(1)


# ---- 1. Multi-Tenant & Dynamic Configuration ----
class SlackTarget(BaseSettings):
    """Configuration for a single Slack webhook target."""

    name: str
    webhook_url: str = Field(repr=False)
    channel: Optional[str] = None
    username: str = "Audit Gateway"
    icon_emoji: str = ":shield:"
    serializer: str = "block_kit_serializer"
    workspace_id: Optional[str] = None
    template_name: Optional[str] = None

    validate_url_protocol: ClassVar = None

    @field_validator("webhook_url", mode="after")
    @classmethod
    def validate_url_protocol(cls, v: str, info) -> str:
        if not v.startswith("https://"):
            if PROD_MODE:
                raise ValueError("All Slack webhook URLs must use HTTPS in production.")
            else:
                main_logger.warning(
                    "Using non-HTTPS URL. This is not secure for production."
                )
        return v


class SlackGatewaySettings(BaseSettings):
    """Manages all Slack Gateway configuration."""

    model_config = SettingsConfigDict(env_prefix="SLACK_GATEWAY_")

    signing_secret: str = Field(..., repr=False)
    admin_api_key: str = Field(..., repr=False)
    encryption_key: Optional[str] = Field(None, repr=False)
    cert_path: Optional[str] = Field(None)
    key_path: Optional[str] = Field(None)

    targets: List[SlackTarget] = []
    persistence_dir: str = "/var/lib/slack_gateway"

    min_workers: int = 1
    max_workers: int = 4
    queue_size_per_worker: int = 250
    worker_scaling_interval: int = 5

    max_queue_size: int = 10000
    worker_linger_sec: float = 1.0
    max_concurrent_requests: int = 5
    requests_per_second_limit: float = 1.0
    max_retries: int = 3
    retry_backoff_factor: float = 2.0
    circuit_breaker_threshold: int = 5
    circuit_breaker_reset_sec: int = 60

    dry_run: bool = False
    dry_run_failure_rate: float = 0.0
    url_allowlist: List[str] = []

    verify_ssl: bool = True
    admin_api_enabled: bool = True
    admin_api_port: int = 9877
    admin_api_host: str = "127.0.0.1"

    def __setattr__(self, name, value):
        if PROD_MODE and hasattr(self, name):
            raise AttributeError("Configuration is immutable in production mode")
        super().__setattr__(self, name, value)

    @field_validator("signing_secret", "admin_api_key", mode="after")
    @classmethod
    def validate_secrets(cls, v: str, info) -> str:
        if v in ("default-slack-secret-key-change-me", ""):
            raise ValueError(
                f"CRITICAL: The {info.field_name} must not be the default value or empty. Use a secure vault."
            )
        return v

    @field_validator("admin_api_host", mode="after")
    @classmethod
    def validate_admin_api_host(cls, v: str, info) -> str:
        if PROD_MODE and v not in ["127.0.0.1", "localhost"]:
            raise ValueError(
                "In production, the admin API must only be exposed on localhost."
            )
        return v

    @classmethod
    def load_from_secure_vault(cls) -> "SlackGatewaySettings":
        main_logger.info("Loading secrets and configuration from secure vault...")
        try:
            settings_dict = {
                "signing_secret": SECRETS_MANAGER.get_secret(
                    "SLACK_GATEWAY_SIGNING_SECRET"
                ),
                "admin_api_key": SECRETS_MANAGER.get_secret(
                    "SLACK_GATEWAY_ADMIN_API_KEY"
                ),
                "encryption_key": SECRETS_MANAGER.get_secret(
                    "SLACK_GATEWAY_ENCRYPTION_KEY", required=False
                ),
                "cert_path": SECRETS_MANAGER.get_secret(
                    "SLACK_GATEWAY_API_CERT", required=False
                ),
                "key_path": SECRETS_MANAGER.get_secret(
                    "SLACK_GATEWAY_API_KEY", required=False
                ),
            }

            for key in [
                "persistence_dir",
                "min_workers",
                "max_workers",
                "max_queue_size",
                "admin_api_port",
                "admin_api_host",
                "requests_per_second_limit",
                "circuit_breaker_threshold",
                "circuit_breaker_reset_sec",
                "dry_run",
                "dry_run_failure_rate",
                "url_allowlist",
            ]:
                env_key = f"SLACK_GATEWAY_{key.upper()}"
                if env_key in os.environ:
                    if PROD_MODE:
                        main_logger.warning(
                            f"Env override for {key} in production. Use a secure vault for full compliance."
                        )
                    settings_dict[key] = os.environ[env_key]

            targets_json = SECRETS_MANAGER.get_secret(
                "SLACK_GATEWAY_TARGETS", required=False
            )
            if targets_json:
                settings_dict["targets"] = [
                    SlackTarget.model_validate(t) for t in json.loads(targets_json)
                ]

            settings = cls.model_validate(settings_dict)

            if PROD_MODE:
                if not settings.encryption_key:
                    raise ValueError(
                        "Encryption must be enabled in production for compliance."
                    )

                for target in settings.targets:
                    if not any(
                        re.match(pattern, target.webhook_url)
                        for pattern in settings.url_allowlist
                    ):
                        raise ValueError(
                            f"URL '{target.webhook_url}' not in allowed_urls list."
                        )

            return settings
        except (KeyError, json.JSONDecodeError, ValidationError) as e:
            main_logger.critical(
                f"Failed to load production configuration from secure source. Error: {e}"
            )
            raise RuntimeError(
                "Critical startup failure: Secure configuration could not be loaded."
            ) from e


# ---- 2. Granular, Labeled Metrics ----
class SlackMetrics:
    NOTIFICATIONS_QUEUED = Counter(
        "slack_notifications_queued_total",
        "Notifications placed into the send queue.",
        ["target_name", "event_name", "severity"],
    )
    NOTIFICATIONS_DROPPED = Counter(
        "slack_notifications_dropped_total",
        "Notifications dropped due to a full queue.",
        ["target_name", "event_name"],
    )
    NOTIFICATIONS_SENT_SUCCESS = Counter(
        "slack_notifications_sent_success_total",
        "Notifications successfully sent to Slack.",
        ["target_name"],
    )
    NOTIFICATIONS_FAILED_PERMANENTLY = Counter(
        "slack_notifications_failed_permanently_total",
        "Notifications that failed to send after all retries.",
        ["target_name", "reason"],
    )
    DEAD_LETTER_NOTIFICATIONS = Counter(
        "slack_dead_letter_notifications_total",
        "Notifications sent to the dead-letter handler.",
        ["target_name", "reason"],
    )
    SEND_LATENCY = Histogram(
        "slack_send_latency_seconds",
        "Latency of a successful batch send operation.",
        ["target_name"],
    )
    CIRCUIT_BREAKER_STATUS = Gauge(
        "slack_circuit_breaker_status",
        "The status of the circuit breaker (1 for open, 0 for closed).",
        ["target_name"],
    )
    RATE_LIMIT_THROTTLED_SECONDS = Counter(
        "slack_rate_limit_throttled_seconds_total",
        "Total time in seconds spent waiting for the rate limiter.",
        ["target_name"],
    )
    ACTIVE_WORKERS = Gauge(
        "slack_active_workers",
        "Number of active worker tasks for a target.",
        ["target_name"],
    )
    NON_TRACED_NOTIFICATIONS = Counter(
        "slack_non_traced_notifications_total",
        "Events sent without OpenTelemetry tracing.",
        ["target_name"],
    )
    QUEUE_SIZE = Gauge(
        "slack_notifications_queue_size",
        "The current size of the in-memory event queue.",
        ["target_name"],
    )
    QUEUE_LATENCY = Histogram(
        "slack_queue_latency_seconds",
        "Time from enqueue to processing.",
        ["target_name"],
    )
    RETRY_ATTEMPTS = Histogram(
        "slack_retry_attempts", "Number of retry attempts per batch.", ["target_name"]
    )

    SYSTEM_CPU_USAGE = Gauge("slack_system_cpu_usage_percent", "CPU usage percentage.")
    SYSTEM_MEMORY_USAGE = Gauge(
        "slack_system_memory_usage_bytes", "Memory usage in bytes."
    )

    def update_system_metrics(self):
        self.SYSTEM_CPU_USAGE.set(psutil.cpu_percent())
        self.SYSTEM_MEMORY_USAGE.set(psutil.virtual_memory().used)


# ---- 3. End-to-End Integrity Event Schema ----
class SlackEvent(BaseModel):
    """Defines the structure for a Slack event with sequencing and signing."""

    event_name: str
    timestamp: float = Field(default_factory=time.time)
    details: Dict[str, Any] = Field(default_factory=dict)
    severity: Literal["info", "warning", "error", "critical"] = "info"
    trace_context: Optional[Dict[str, str]] = None
    sequence_id: int = 0
    signature: str = ""
    enqueue_time: float = 0.0
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    SENSITIVE_KEYS: ClassVar[re.Pattern] = re.compile(
        r".*(password|secret|key|token|pii|ssn|credit_card|credentials).*",
        re.IGNORECASE,
    )
    SENSITIVE_PATTERNS: ClassVar[List[re.Pattern]] = [
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
        re.compile(r"\b\d{3}[-.\s]??\d{3}[-.\s]??\d{4}\b"),
        re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"),
    ]

    @field_validator("details", mode="after")
    @classmethod
    def scrub_sensitive_details(cls, v: Dict[str, Any], info) -> Dict[str, Any]:
        def scrub(data):
            if isinstance(data, dict):
                scrubbed_data = {}
                for key, value in data.items():
                    if cls.SENSITIVE_KEYS.match(key):
                        audit_logger.critical(
                            f"Detected and scrubbed sensitive key '{key}' from event."
                        )
                        scrubbed_data[key] = "[REDACTED]"
                    else:
                        scrubbed_data[key] = scrub(value)
                return scrubbed_data
            elif isinstance(data, list):
                return [scrub(item) for item in data]
            elif isinstance(data, str):
                for pattern in cls.SENSITIVE_PATTERNS:
                    if pattern.search(data):
                        audit_logger.critical(
                            "Detected and scrubbed sensitive pattern from string."
                        )
                        return "[REDACTED]"
                return data
            else:
                return data

        return scrub(v)


def load_template(event_name: str) -> str:
    templates = {
        "database_connection_failed": "Database connection failed on host `{hostname}` with details: ```{event.details}```",
        "user_logged_in": "User `{event.details['user_id']}` logged in successfully.",
        "sensitive_info_exposed": "CRITICAL: Sensitive information exposed by user `{event.details['user_id']}`. Action required!",
    }
    return templates.get(
        event_name,
        "{event.event_name} alert from {hostname}. Details: ```{event.details}```",
    )


class Serializer(Protocol):
    def encode_payload(
        self, event: SlackEvent, target_config: SlackTarget, hostname: str
    ) -> Dict[str, Any]: ...


class SlackBlockKitSerializer:
    SEVERITY_COLORS = {
        "info": "#36a64f",
        "warning": "#ffa500",
        "error": "#d50000",
        "critical": "#b71c1c",
    }

    def encode_payload(
        self, event: SlackEvent, target_config: SlackTarget, hostname: str
    ) -> Dict[str, Any]:
        template_str = (
            load_template(event.event_name)
            if target_config.template_name is None
            else load_template(target_config.template_name)
        )
        message_text = template_str.format(
            event=event, hostname=hostname, target_config=target_config
        )

        payload = {
            "channel": target_config.channel,
            "username": target_config.username,
            "icon_emoji": target_config.icon_emoji,
            "attachments": [
                {
                    "color": self.SEVERITY_COLORS.get(event.severity, "#cccccc"),
                    "blocks": [
                        {
                            "type": "header",
                            "text": {
                                "type": "plain_text",
                                "text": f"{event.severity.upper()}: {event.event_name}",
                            },
                        },
                        {
                            "type": "section",
                            "fields": [
                                {"type": "mrkdwn", "text": f"*Host:*\n`{hostname}`"},
                                {
                                    "type": "mrkdwn",
                                    "text": f"*Timestamp:*\n{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(event.timestamp))}",
                                },
                            ],
                        },
                        {"type": "divider"},
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": message_text},
                        },
                        {
                            "type": "context",
                            "elements": [
                                {
                                    "type": "mrkdwn",
                                    "text": f"CorrID: `{event.correlation_id[:8]}` | Seq: {event.sequence_id} | Sig: `{event.signature[:8]}...`",
                                }
                            ],
                        },
                    ],
                }
            ],
        }
        return payload


class EventQueue(Protocol):
    async def startup(self): ...
    async def shutdown(self): ...
    async def put(self, item: SlackEvent): ...
    async def get(self) -> SlackEvent: ...
    def qsize(self) -> int: ...
    async def task_done(self): ...
    async def flush(self, timeout: int = 60): ...


class PriorityEventQueue(EventQueue):
    def __init__(self, maxsize: int):
        self._queue = PriorityQueue(maxsize=maxsize)

    async def startup(self):
        pass

    async def shutdown(self):
        pass

    async def put(self, item: SlackEvent):
        priority = 1 if item.severity == "critical" else 2
        await self._queue.put((priority, item))

    async def get(self) -> SlackEvent:
        priority, item = await self._queue.get()
        return item

    def qsize(self) -> int:
        return self._queue.qsize()

    async def task_done(self):
        self._queue.task_done()

    async def flush(self, timeout: int = 60):
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            main_logger.critical("Shutdown timeout exceeded for PriorityEventQueue.")


class PersistentWALQueue(EventQueue):
    def __init__(
        self,
        target_name: str,
        persistence_dir: str,
        max_in_memory_size: int,
        encryption_key: Optional[str] = None,
    ):
        self._dir = os.path.join(
            persistence_dir,
            f"{target_name}_{hashlib.sha256(target_name.encode()).hexdigest()}",
        )
        self._cipher: Optional[Fernet] = None
        if encryption_key:
            self._cipher = Fernet(encryption_key.encode())

        self._max_log_size = 10 * 1024 * 1024
        self._log_rotation_interval = 86400
        self._last_rotation_time = time.time()
        self._hmac_key = SECRETS_MANAGER.get_secret(
            "SLACK_WAL_HMAC_KEY", required=PROD_MODE
        ).encode()

        if not os.path.exists(self._dir):
            os.makedirs(self._dir, exist_ok=True)
            os.chmod(self._dir, 0o700)
        self._mem_queue = asyncio.Queue(maxsize=max_in_memory_size)
        self._write_lock = asyncio.Lock()
        self._current_write_log: Optional[
            aiofiles.threadpool.binary.AsyncBufferedIOBase
        ] = None
        self._sequence_number = 0
        self._current_log_path: Optional[str] = None
        self._compactor_task: Optional[asyncio.Task] = None

    async def startup(self):
        try:
            log_files = sorted(
                [
                    f
                    for f in os.listdir(self._dir)
                    if f.startswith("events.") and f.endswith(".log")
                ]
            )
            for log_file in log_files:
                path = os.path.join(self._dir, log_file)
                async with aiofiles.open(path, "r") as f:
                    async for line in f:
                        if line.strip():
                            try:
                                sig, data = line.strip().split(":", 1)
                                if not hmac.compare_digest(
                                    sig,
                                    hmac.new(
                                        self._hmac_key,
                                        data.encode("utf-8"),
                                        hashlib.sha256,
                                    ).hexdigest(),
                                ):
                                    raise AnalyzerCriticalError(
                                        "WAL integrity check failed on decrypt."
                                    )

                                decrypted_line = (
                                    self._cipher.decrypt(data.encode("utf-8"))
                                    if self._cipher
                                    else data.encode("utf-8")
                                )
                                event = SlackEvent.model_validate_json(decrypted_line)
                                await self._mem_queue.put(event)
                            except Exception as e:
                                main_logger.critical(
                                    f"Failed to process WAL entry: {e}. Aborting."
                                )
                                raise AnalyzerCriticalError(
                                    f"Failed to process WAL entry: {e}."
                                )
            main_logger.info(
                f"Loaded {self._mem_queue.qsize()} events from disk for target {os.path.basename(self._dir)}."
            )
            audit_logger.info(
                "WAL loaded from disk.",
                extra={
                    "context": {
                        "target": os.path.basename(self._dir),
                        "event_count": self._mem_queue.qsize(),
                    }
                },
            )
        except OSError as e:
            main_logger.critical(
                f"Failed to load WAL from disk: {e}. Exiting.",
                extra={"context": {"target": os.path.basename(self._dir)}},
            )
            raise RuntimeError(
                "Critical startup failure: WAL could not be loaded."
            ) from e

        await self._open_next_log_segment()
        self._compactor_task = asyncio.create_task(self._wal_compactor())

    def _create_signature(self, event: SlackEvent) -> str:
        canonical_event = f"{event.sequence_id}|{event.event_name}|{json.dumps(event.details, sort_keys=True)}"
        return hmac.new(
            SECRETS_MANAGER.get_secret("SLACK_GATEWAY_SIGNING_SECRET").encode(),
            canonical_event.encode(),
            hashlib.sha256,
        ).hexdigest()

    async def _open_next_log_segment(self):
        async with self._write_lock:
            if self._current_write_log:
                await self._current_write_log.flush()
                await self._current_write_log.close()
            temp_path = os.path.join(
                self._dir, f"events.temp.{time.strftime('%Y%m%d_%H%M%S')}.log"
            )
            self._current_write_log = await aiofiles.open(temp_path, "ab")
            os.chmod(temp_path, 0o600)
            self._current_log_path = os.path.join(
                self._dir, f"events.{time.strftime('%Y%m%d_%H%M%S')}.log"
            )
            os.rename(temp_path, self._current_log_path)
            self._last_rotation_time = time.time()

    async def put(self, item: SlackEvent):
        async with self._write_lock:
            if (
                not self._current_write_log
                or (await aiofiles.os.stat(self._current_log_path)).st_size
                > self._max_log_size
                or time.time() - self._last_rotation_time > self._log_rotation_interval
            ):
                await self._open_next_log_segment()

            line = item.model_dump_json().encode("utf-8")
            if self._cipher:
                line = self._cipher.encrypt(line)

            signature = hmac.new(self._hmac_key, line, hashlib.sha256).hexdigest()
            await self._current_write_log.write(
                f"{signature}:{line.decode()}\n".encode()
            )
            await self._current_write_log.flush()
        await self._mem_queue.put(item)

    async def get(self):
        return await self._mem_queue.get()

    def qsize(self):
        return self._mem_queue.qsize()

    async def task_done(self):
        self._mem_queue.task_done()

    async def flush(self, timeout: int = 60):
        try:
            await asyncio.wait_for(self._mem_queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            main_logger.critical("Shutdown timeout exceeded. Forcing shutdown.")
            audit_logger.critical(
                "shutdown_timeout",
                extra={"context": {"remaining_events": self._mem_queue.qsize()}},
            )
            alert_operator(
                "CRITICAL: Shutdown timeout exceeded. Events may be lost.", "CRITICAL"
            )

    async def _wal_compactor(self):
        while not self._mem_queue.empty() or not self._compactor_task.done():
            await asyncio.sleep(3600)
            log_files = sorted(
                [
                    f
                    for f in os.listdir(self._dir)
                    if f.startswith("events.") and f.endswith(".log")
                ]
            )
            if len(log_files) > 1:
                for old_file in log_files[:-1]:
                    try:
                        os.remove(os.path.join(self._dir, old_file))
                        audit_logger.info(
                            "WAL file compacted.", extra={"context": {"file": old_file}}
                        )
                    except OSError as e:
                        main_logger.error(f"Failed to compact WAL file {old_file}: {e}")
            if self._compactor_task.done():
                break

    async def shutdown(self):
        await self.flush()
        if self._compactor_task:
            self._compactor_task.cancel()
            try:
                await self._compactor_task
            except asyncio.CancelledError:
                pass
        if self._current_write_log:
            await self._current_write_log.close()
        audit_logger.info(
            "WAL compacted and queues flushed on shutdown.",
            extra={"context": {"target": os.path.basename(self._dir)}},
        )


# ---- 5. Advanced Resilience Patterns ----
class CircuitBreaker:
    def __init__(
        self,
        threshold: int,
        reset_seconds: int,
        metrics: SlackMetrics,
        target_name: str,
    ):
        self._threshold, self._reset_seconds = threshold, reset_seconds
        self._metrics, self._target_name = metrics, target_name
        self._failure_count, self._is_open, self._last_failure_time = 0, False, 0.0
        self._metrics.CIRCUIT_BREAKER_STATUS.labels(target_name=self._target_name).set(
            0
        )

    def check(self):
        if self._is_open:
            jitter = random.uniform(0, self._reset_seconds * 0.1)
            if time.monotonic() - self._last_failure_time > (
                self._reset_seconds + jitter
            ):
                self._is_open, self._failure_count = False, 0
                self._metrics.CIRCUIT_BREAKER_STATUS.labels(
                    target_name=self._target_name
                ).set(0)
                main_logger.warning(
                    "Circuit breaker has been reset.",
                    extra={"context": {"target": self._target_name}},
                )
                audit_logger.info(
                    "circuit_breaker_reset",
                    extra={"context": {"target": self._target_name}},
                )
            else:
                raise ConnectionAbortedError(
                    f"Circuit breaker for {self._target_name} is open."
                )

    def record_failure(self):
        self._failure_count += 1
        if self._failure_count >= self._threshold and not self._is_open:
            self._is_open, self._last_failure_time = True, time.monotonic()
            self._metrics.CIRCUIT_BREAKER_STATUS.labels(
                target_name=self._target_name
            ).set(1)
            main_logger.critical(
                "Circuit breaker tripped. Escalating.",
                extra={"context": {"target": self._target_name}},
            )
            audit_logger.critical(
                "circuit_breaker_tripped",
                extra={
                    "context": {
                        "target": self._target_name,
                        "threshold": self._threshold,
                    }
                },
            )
            alert_operator(
                f"CRITICAL: Slack circuit breaker tripped for {self._target_name}.",
                "CRITICAL",
            )

    def record_success(self):
        if self._failure_count > 0:
            self._failure_count = 0
            main_logger.info(
                "Circuit breaker failure count reset due to success.",
                extra={"context": {"target": self._target_name}},
            )


class TokenBucket:
    def __init__(
        self, rate: float, capacity: float, metrics: SlackMetrics, target_name: str
    ):
        self._rate, self._capacity = rate, max(rate * 10, capacity)
        self._metrics, self._target_name = metrics, target_name
        self._tokens, self._last_refill = self._capacity, time.monotonic()
        self._last_response_status = 200

    async def acquire(self):
        self._refill()
        while self._tokens < 1:
            throttled_time = max(0, (1 - self._tokens) / self._rate)
            if throttled_time > 0:
                self._metrics.RATE_LIMIT_THROTTLED_SECONDS.labels(
                    target_name=self._target_name
                ).inc(throttled_time)
                await asyncio.sleep(throttled_time)
            self._refill()
        self._tokens -= 1

    def _refill(self):
        now = time.monotonic()
        elapsed = max(0, now - self._last_refill)
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now

    def record_status(self, status: int):
        self._last_response_status = status
        if status == 429:
            self._rate = max(self._rate * 0.5, 0.1)


# ---- 6. The Unrivaled Slack Gateway Manager ----
DeadLetterHook = Callable[[SlackEvent, str], Awaitable[None]]


class SlackGateway:
    def __init__(
        self,
        target_config: SlackTarget,
        global_settings: SlackGatewaySettings,
        metrics: SlackMetrics,
        serializer: Serializer,
        rate_limiter: TokenBucket,
        dead_letter_hook: Optional[DeadLetterHook],
    ):
        self.target_config = target_config
        self.global_settings = global_settings
        self.metrics = metrics
        self.serializer = serializer
        self.rate_limiter = rate_limiter
        self.dead_letter_hook = dead_letter_hook
        self.circuit_breaker = CircuitBreaker(
            global_settings.circuit_breaker_threshold,
            global_settings.circuit_breaker_reset_sec,
            metrics,
            target_config.name,
        )

        self._event_queue: EventQueue = PersistentWALQueue(
            target_config.name,
            global_settings.persistence_dir,
            global_settings.max_queue_size,
            encryption_key=global_settings.encryption_key,
        )
        self._fallback_queue: EventQueue = asyncio.Queue(
            maxsize=global_settings.max_queue_size
        )

        self._workers: List[asyncio.Task] = []
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        self._concurrency_limiter = asyncio.Semaphore(
            global_settings.max_concurrent_requests
        )
        self._hostname = socket.gethostname()
        self._health_stats = {"processed_count": 0, "last_processed_time": 0.0}
        self._is_paused = False
        self._shutdown_event = asyncio.Event()
        self._sequence_id = 0
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._sequence_lock = asyncio.Lock()
        self._queue_history = deque(maxlen=3)
        self._scale_down_timer = 0

    async def startup(self):
        await self._event_queue.startup()
        self._shutdown_event.clear()
        self._workers.append(asyncio.create_task(self._worker_manager()))
        self._heartbeat_task = asyncio.create_task(self._heartbeat())
        main_logger.info(
            f"Slack Gateway started for target '{self.target_config.name}'."
        )

    async def shutdown(self):
        main_logger.info(
            f"Initiating graceful shutdown for target '{self.target_config.name}'."
        )
        self._shutdown_event.set()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Send sentinel to all workers and wait
        for _ in self._workers:
            await self._event_queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)

        await self._event_queue.flush(
            timeout=self.global_settings.max_queue_size
            / self.global_settings.max_workers
            + 10
        )
        await self._event_queue.shutdown()
        async with self._session_lock:
            if self._session and not self._session.closed:
                await self._session.close()
        main_logger.info(
            f"Graceful shutdown complete for target '{self.target_config.name}'."
        )

    def pause(self):
        self._is_paused = True
        main_logger.warning(f"Target '{self.target_config.name}' is paused.")

    def resume(self):
        self._is_paused = False
        main_logger.info(f"Target '{self.target_config.name}' is resumed.")

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._session_lock:
            if self._session is None or self._session.closed:
                ssl_context = ssl.create_default_context()
                ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3
                if (
                    PROD_MODE
                    and self.global_settings.cert_path
                    and self.global_settings.key_path
                ):
                    ssl_context.load_cert_chain(
                        self.global_settings.cert_path, self.global_settings.key_path
                    )

                timeout = aiohttp.ClientTimeout(
                    total=self.global_settings.retry_backoff_factor * 5
                )
                self._session = aiohttp.ClientSession(timeout=timeout, ssl=ssl_context)
        return self._session

    async def _handle_dead_letter(self, event: SlackEvent, reason: str):
        self.metrics.DEAD_LETTER_NOTIFICATIONS.labels(
            target_name=self.target_config.name, reason=reason
        ).inc()
        audit_logger.warning(
            "dead_letter_event",
            extra={
                "context": {
                    "target": self.target_config.name,
                    "reason": reason,
                    "event_name": event.event_name,
                }
            },
        )
        if self.dead_letter_hook:
            try:
                await self.dead_letter_hook(event, reason)
            except Exception as e:
                main_logger.error(
                    "Dead-letter hook failed.",
                    extra={
                        "context": {"error": str(e), "target": self.target_config.name}
                    },
                )

    async def publish(self, event: SlackEvent):
        try:
            event.enqueue_time = time.time()
            await self._event_queue.put(event)
            self.metrics.NOTIFICATIONS_QUEUED.labels(
                target_name=self.target_config.name,
                event_name=event.event_name,
                severity=event.severity,
            ).inc()
            self.metrics.QUEUE_SIZE.labels(target_name=self.target_config.name).set(
                self._event_queue.qsize()
            )
            audit_logger.debug(
                "event_queued",
                extra={
                    "context": {
                        "target": self.target_config.name,
                        "event_name": event.event_name,
                    }
                },
            )
        except asyncio.QueueFull:
            try:
                await self._fallback_queue.put(event)
                main_logger.warning(
                    "Primary queue full, redirected to fallback queue.",
                    extra={"context": {"target": self.target_config.name}},
                )
                audit_logger.warning(
                    "event_redirected_to_fallback",
                    extra={
                        "context": {
                            "target": self.target_config.name,
                            "event_name": event.event_name,
                        }
                    },
                )
            except asyncio.QueueFull:
                self.metrics.NOTIFICATIONS_DROPPED.labels(
                    target_name=self.target_config.name, event_name=event.event_name
                ).inc()
                main_logger.warning(
                    "Primary and fallback queues are full. Event dropped.",
                    extra={
                        "context": {
                            "target": self.target_config.name,
                            "event_name": event.event_name,
                        }
                    },
                )
                await self._handle_dead_letter(event, "queue_full")

    async def _send_event(self, event: SlackEvent):
        with tracer.start_as_current_span(
            "slack_send_event",
            attributes={
                "event.name": event.event_name,
                "slack.target": self.target_config.name,
                "correlation.id": event.correlation_id,
                "severity": event.severity,
            },
        ):
            try:
                self.circuit_breaker.check()
            except ConnectionAbortedError:
                main_logger.warning(
                    "Event dropped due to circuit breaker being open.",
                    extra={"context": {"target": self.target_config.name}},
                )
                await self._handle_dead_letter(event, "circuit_breaker_open")
                return False

            payload = self.serializer.encode_payload(
                event, self.target_config, self._hostname
            )
            attempt = 0
            while attempt < self.global_settings.max_retries:
                self.metrics.RETRY_ATTEMPTS.labels(
                    target_name=self.target_config.name
                ).observe(attempt)
                await self.rate_limiter.acquire()
                async with self._concurrency_limiter:
                    start_time = time.monotonic()
                    try:
                        session = await self._get_session()
                        timeout = aiohttp.ClientTimeout(
                            total=self.global_settings.retry_backoff_factor**attempt * 5
                        )
                        async with session.post(
                            self.target_config.webhook_url,
                            json=payload,
                            ssl=self.global_settings.verify_ssl,
                            timeout=timeout,
                        ) as resp:
                            self.rate_limiter.record_status(resp.status)
                            if resp.status == 200:
                                self.metrics.SEND_LATENCY.labels(
                                    target_name=self.target_config.name
                                ).observe(time.monotonic() - start_time)
                                self.metrics.NOTIFICATIONS_SENT_SUCCESS.labels(
                                    target_name=self.target_config.name
                                ).inc()
                                self.circuit_breaker.record_success()
                                audit_logger.info(
                                    "notification_sent_success",
                                    extra={
                                        "context": {
                                            "target": self.target_config.name,
                                            "event_name": event.event_name,
                                        }
                                    },
                                )
                                return True
                            elif resp.status == 429:
                                retry_after = int(resp.headers.get("Retry-After", "5"))
                                main_logger.warning(
                                    f"Rate limited by Slack API. Backing off for {retry_after} seconds.",
                                    extra={
                                        "context": {"target": self.target_config.name}
                                    },
                                )
                                await asyncio.sleep(retry_after)
                                continue
                            elif 400 <= resp.status < 500:
                                error_text = await resp.text()
                                main_logger.error(
                                    "Permanent failure sending to Slack (client error).",
                                    extra={
                                        "context": {
                                            "target": self.target_config.name,
                                            "error": error_text,
                                            "status_code": resp.status,
                                        }
                                    },
                                )
                                await self._handle_dead_letter(event, "client_error")
                                self.metrics.NOTIFICATIONS_FAILED_PERMANENTLY.labels(
                                    target_name=self.target_config.name,
                                    reason=f"client_error_{resp.status}",
                                ).inc()
                                return False
                            else:
                                resp.raise_for_status()
                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        main_logger.warning(
                            "Temporary failure sending to Slack.",
                            extra={
                                "context": {
                                    "target": self.target_config.name,
                                    "attempt": attempt + 1,
                                    "error": str(e),
                                }
                            },
                        )
                        if attempt + 1 >= self.global_settings.max_retries:
                            self.circuit_breaker.record_failure()
                            await self._handle_dead_letter(
                                event, f"service_unavailable: {type(e).__name__}"
                            )
                            self.metrics.NOTIFICATIONS_FAILED_PERMANENTLY.labels(
                                target_name=self.target_config.name,
                                reason="service_unavailable",
                            ).inc()
                            return False
                attempt += 1
                if attempt < self.global_settings.max_retries:
                    await asyncio.sleep(
                        self.global_settings.retry_backoff_factor**attempt
                    )
            return False

    async def _worker(self, worker_id: int):
        main_logger.info(
            f"Starting worker {worker_id} for target {self.target_config.name}"
        )
        audit_logger.info(
            "worker_started",
            extra={
                "context": {"target": self.target_config.name, "worker_id": worker_id}
            },
        )
        while not self._shutdown_event.is_set():
            try:
                self.metrics.QUEUE_SIZE.labels(target_name=self.target_config.name).set(
                    self._event_queue.qsize()
                )
                if self._is_paused:
                    await asyncio.sleep(1)
                    continue
                event = await self._event_queue.get()
                if event is None:
                    self._event_queue.task_done()
                    await self._event_queue.put(None)
                    break

                self.metrics.QUEUE_LATENCY.labels(
                    target_name=self.target_config.name
                ).observe(time.time() - event.enqueue_time)

                success = False
                if self.global_settings.dry_run:
                    main_logger.info(
                        "[DRY RUN] Would send Slack notification.",
                        extra={
                            "context": {
                                "target": self.target_config.name,
                                "event_name": event.event_name,
                                "simulated_failure": self.global_settings.dry_run_failure_rate
                                > 0
                                and random.random()
                                < self.global_settings.dry_run_failure_rate,
                            }
                        },
                    )
                    if (
                        self.global_settings.dry_run_failure_rate > 0
                        and random.random() < self.global_settings.dry_run_failure_rate
                    ):
                        await self._handle_dead_letter(
                            event, "dry_run_simulated_failure"
                        )
                        success = False
                    else:
                        success = True
                else:
                    success = await self._send_event(event)

                if success:
                    self._health_stats["processed_count"] += 1
                    self._health_stats["last_processed_time"] = time.time()
                await self._event_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                main_logger.critical(
                    "Unhandled exception in Slack worker.",
                    extra={
                        "context": {
                            "error": str(e),
                            "target": self.target_config.name,
                            "worker_id": worker_id,
                        }
                    },
                )
                audit_logger.critical(
                    "worker_unhandled_exception",
                    extra={
                        "context": {
                            "target": self.target_config.name,
                            "worker_id": worker_id,
                            "error": str(e),
                        }
                    },
                )
                await asyncio.sleep(1)
        main_logger.info(
            f"Stopping worker {worker_id} for target {self.target_config.name}"
        )
        audit_logger.info(
            "worker_stopped",
            extra={
                "context": {"target": self.target_config.name, "worker_id": worker_id}
            },
        )

    async def _worker_manager(self):
        active_workers = [
            asyncio.create_task(self._worker(i))
            for i in range(self.global_settings.min_workers)
        ]

        self._queue_history = deque(maxlen=3)
        self._scale_down_timer = 0

        while not self._shutdown_event.is_set():
            await asyncio.sleep(self.global_settings.worker_scaling_interval)

            queue_size = self._event_queue.qsize()
            self._queue_history.append(queue_size)
            avg_queue = sum(self._queue_history) / len(self._queue_history)
            cpu_usage = psutil.cpu_percent()
            mem_usage = psutil.virtual_memory().percent

            if (
                avg_queue
                > self.global_settings.queue_size_per_worker * len(active_workers)
                and len(active_workers) < self.global_settings.max_workers
                and cpu_usage < 80
                and mem_usage < 80
            ):
                worker_id = len(active_workers)
                main_logger.info(
                    f"Queue size high ({queue_size}), scaling up worker for {self.target_config.name} to {worker_id + 1}"
                )
                audit_logger.info(
                    "worker_scale_up",
                    extra={
                        "context": {
                            "target": self.target_config.name,
                            "new_worker_count": worker_id + 1,
                            "queue_size": queue_size,
                        }
                    },
                )
                active_workers.append(asyncio.create_task(self._worker(worker_id)))

            elif (
                avg_queue == 0
                and len(active_workers) > self.global_settings.min_workers
            ):
                self._scale_down_timer += self.global_settings.worker_scaling_interval
                if (
                    self._scale_down_timer
                    >= self.global_settings.worker_scaling_interval * 3
                ):
                    main_logger.info(
                        f"Queue empty, scaling down worker for {self.target_config.name} to {len(active_workers) - 1}"
                    )
                    audit_logger.info(
                        "worker_scale_down",
                        extra={
                            "context": {
                                "target": self.target_config.name,
                                "new_worker_count": len(active_workers) - 1,
                            }
                        },
                    )
                    worker_to_stop = active_workers.pop()
                    worker_to_stop.cancel()
                    self._scale_down_timer = 0
            else:
                self._scale_down_timer = 0

            self.metrics.ACTIVE_WORKERS.labels(target_name=self.target_config.name).set(
                len(active_workers)
            )

        for worker in active_workers:
            if not worker.done():
                worker.cancel()
        await asyncio.gather(*active_workers, return_exceptions=True)
        main_logger.info(f"Worker manager for {self.target_config.name} has shut down.")

    async def _heartbeat(self):
        while not self._shutdown_event.is_set():
            try:
                # Send a non-audit event heartbeat
                heartbeat_event = SlackEvent(
                    event_name="heartbeat",
                    details={"ping": "pong"},
                    severity="info",
                    sequence_id=0,
                    signature="",
                    correlation_id=str(uuid.uuid4()),
                )
                success = await self._send_event(heartbeat_event)
                if success:
                    self.circuit_breaker.record_success()
                else:
                    self.circuit_breaker.record_failure()
            except Exception as e:
                self.circuit_breaker.record_failure()
                main_logger.warning(
                    f"Heartbeat failed for {self.target_config.name}: {e}"
                )
            await asyncio.sleep(30)


class SlackGatewayManager:
    def __init__(
        self,
        settings: SlackGatewaySettings,
        metrics: SlackMetrics,
        dead_letter_hook: Optional[DeadLetterHook] = None,
    ):
        self.settings = settings
        self.metrics = metrics
        self.dead_letter_hook = dead_letter_hook
        self._gateways: Dict[str, SlackGateway] = {}
        self._serializers: Dict[str, Serializer] = {
            "block_kit_serializer": SlackBlockKitSerializer()
        }
        self._rate_limiters: Dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()
        self._http_server_task: Optional[asyncio.Task] = None
        self._sequence_counters: Dict[str, int] = {}
        self._sequence_locks: Dict[str, asyncio.Lock] = {}
        self._config_version: int = 0
        self._system_metrics_task: Optional[asyncio.Task] = None
        self._api_sem = asyncio.Semaphore(10)  # API rate limit

    async def startup(self):
        if PROD_MODE and (
            self.settings.dry_run or self.settings.dry_run_failure_rate > 0
        ):
            main_logger.critical(
                "DRY_RUN or DRY_RUN_FAILURE_RATE is enabled in production mode. This is a critical error. Exiting."
            )
            sys.exit(1)

        if PROD_MODE and not OPENTELEMETRY_AVAILABLE:
            main_logger.critical("OpenTelemetry is mandatory in production. Exiting.")
            sys.exit(1)

        main_logger.info("Slack Gateway Manager starting up.")
        self.load_serializers_from_plugins()
        await self.reload_config(self.settings)
        if self.settings.admin_api_enabled and self._http_server_task is None:
            self._http_server_task = asyncio.create_task(self._run_admin_api_server())

        self._system_metrics_task = asyncio.create_task(
            self._run_system_metrics_collector()
        )

    async def _run_system_metrics_collector(self):
        while self._http_server_task is None or not self._http_server_task.done():
            self.metrics.update_system_metrics()
            await asyncio.sleep(5)

    async def shutdown(self):
        main_logger.info("Initiating Slack Gateway Manager shutdown.")
        if self._system_metrics_task:
            self._system_metrics_task.cancel()
        if self._http_server_task:
            self._http_server_task.cancel()
            try:
                await self._http_server_task
            except asyncio.CancelledError:
                pass

        async with self._lock:
            await asyncio.gather(*(gw.shutdown() for gw in self._gateways.values()))

        main_logger.info("Slack Gateway Manager shut down.")

    async def _load_sequence_counters(self):
        for target in self.settings.targets:
            try:
                path = os.path.join(
                    self.settings.persistence_dir, f"{target.name}_seq.txt"
                )
                async with aiofiles.open(path, "r") as f:
                    self._sequence_counters[target.name] = int(await f.read())
            except (OSError, ValueError) as e:
                main_logger.warning(
                    "Failed to load sequence counter, starting from 0.",
                    extra={"context": {"target": target.name, "error": str(e)}},
                )
                self._sequence_counters[target.name] = 0

    async def _save_sequence_counter(self, target_name: str, seq_id: int):
        try:
            path = os.path.join(self.settings.persistence_dir, f"{target_name}_seq.txt")
            async with aiofiles.open(path, "w") as f:
                await f.write(str(seq_id))
            os.chmod(path, 0o600)
        except OSError as e:
            main_logger.error(
                "Failed to persist sequence counter.",
                extra={"context": {"target": target_name, "error": str(e)}},
            )

    def load_serializers_from_plugins(self, group="slack_gateway.serializers"):
        try:
            for entry_point in importlib.metadata.entry_points(group=group):
                try:
                    serializer_class = entry_point.load()
                    self.register_serializer(entry_point.name, serializer_class())
                except Exception:
                    raise AnalyzerCriticalError(
                        f"Failed to load serializer plugin '{entry_point.name}'."
                    )
        except TypeError:  # Pre Python 3.10
            for entry_point in importlib.metadata.entry_points().get(group, []):
                try:
                    serializer_class = entry_point.load()
                    self.register_serializer(entry_point.name, serializer_class())
                except Exception:
                    raise AnalyzerCriticalError(
                        f"Failed to load serializer plugin '{entry_point.name}'."
                    )

    def register_serializer(self, name: str, serializer: Serializer):
        self._serializers[name] = serializer
        main_logger.info(f"Registered new serializer: {name}")

    async def reload_config(self, new_settings: SlackGatewaySettings):
        async with self._lock:
            self._config_version += 1
            audit_logger.info(
                "reload_config_initiated",
                extra={
                    "context": {
                        "new_target_count": len(new_settings.targets),
                        "config_version": self._config_version,
                    }
                },
            )
            main_logger.info("Performing zero-downtime configuration reload...")
            new_targets_map = {t.name: t for t in new_settings.targets}
            old_gateways = self._gateways
            new_gateways = {}

            self._rate_limiters = {}
            for target in new_settings.targets:
                ws_id = target.workspace_id or target.name
                if ws_id not in self._rate_limiters:
                    self._rate_limiters[ws_id] = TokenBucket(
                        new_settings.requests_per_second_limit,
                        new_settings.requests_per_second_limit,
                        self.metrics,
                        ws_id,
                    )

            for name, target_config in new_targets_map.items():
                serializer = self._serializers.get(target_config.serializer)
                if not serializer:
                    main_logger.error(
                        f"Unknown serializer '{target_config.serializer}' for target '{name}'. Skipping."
                    )
                    audit_logger.error(
                        "reload_config_failed_unknown_serializer",
                        extra={
                            "context": {
                                "target": name,
                                "serializer": target_config.serializer,
                            }
                        },
                    )
                    continue

                ws_id = target_config.workspace_id or name
                rate_limiter = self._rate_limiters[ws_id]
                new_gateways[name] = SlackGateway(
                    target_config,
                    new_settings,
                    self.metrics,
                    serializer,
                    rate_limiter,
                    self.dead_letter_hook,
                )

            await self._load_sequence_counters()
            self._sequence_locks = {name: asyncio.Lock() for name in new_targets_map}

            await asyncio.gather(*(gw.startup() for gw in new_gateways.values()))

            self._gateways = new_gateways
            self.settings = new_settings
            main_logger.info(
                "Configuration reloaded. Draining old gateways.",
                extra={"context": {"active_targets": list(self._gateways.keys())}},
            )
            audit_logger.info(
                "reload_config_success",
                extra={
                    "context": {
                        "active_targets": list(self._gateways.keys()),
                        "config_version": self._config_version,
                    }
                },
            )

            await asyncio.gather(*(gw.shutdown() for gw in old_gateways.values()))
            main_logger.info("Old gateways drained and shut down.")

    async def publish(
        self, target_name: str, event_name: str, details: Dict[str, Any], **kwargs
    ):
        gateway = self._gateways.get(target_name)
        if not gateway:
            main_logger.warning(
                f"Publish to unknown target '{target_name}'. Event dropped."
            )
            audit_logger.warning(
                "publish_to_unknown_target",
                extra={"context": {"target": target_name, "event_name": event_name}},
            )
            return

        scrubbed_details = SlackEvent.scrub_sensitive_details(details)
        if scrubbed_details != details:
            main_logger.error(
                "Sensitive data detected in event payload. Event dropped."
            )
            return

        lock = self._sequence_locks.get(target_name)
        if not lock:
            main_logger.error(
                f"Sequence lock missing for target '{target_name}'. Event dropped."
            )
            return

        async with lock:
            seq_id = self._sequence_counters.get(target_name, 0) + 1
            self._sequence_counters[target_name] = seq_id
            await self._save_sequence_counter(target_name, seq_id)

        canonical_event = f"{seq_id}|{event_name}|{json.dumps(details, sort_keys=True)}"
        signature = hmac.new(
            self.settings.signing_secret.encode(),
            canonical_event.encode(),
            hashlib.sha256,
        ).hexdigest()

        trace_context = kwargs.get("trace_context", {})
        if TraceContextTextMapPropagator and OPENTELEMETRY_AVAILABLE:
            try:
                TraceContextTextMapPropagator().inject(trace_context, get_current())
            except Exception as e:
                main_logger.error(f"Failed to inject OpenTelemetry trace context: {e}")
        else:
            self.metrics.NON_TRACED_NOTIFICATIONS.labels(target_name=target_name).inc()

        try:
            event = SlackEvent(
                event_name=event_name,
                details=details,
                trace_context=trace_context,
                sequence_id=seq_id,
                signature=signature,
                **kwargs,
            )
            await gateway.publish(event)
        except ValidationError as e:
            main_logger.error(
                "Invalid Slack event schema.",
                extra={"context": {"error": str(e), "event_name": event_name}},
            )
            audit_logger.error(
                "invalid_event_schema",
                extra={"context": {"event_name": event_name, "error": str(e)}},
            )

    async def health_check(self) -> Dict[str, Any]:
        targets_health = {}
        for name, gw in self._gateways.items():
            targets_health[name] = {
                "queue_size": gw._event_queue.qsize(),
                "circuit_breaker_open": gw.circuit_breaker._is_open,
                "active_workers": len(gw._workers),
                "processed_count": gw._health_stats.get("processed_count", 0),
                "last_processed_time": gw._health_stats.get("last_processed_time", 0.0),
                "status": (
                    "paused"
                    if gw._is_paused
                    else (
                        "healthy"
                        if not gw.circuit_breaker._is_open
                        else "unhealthy_circuit_open"
                    )
                ),
            }
        return {
            "status": "ok",
            "targets": targets_health,
            "version": self._config_version,
        }

    async def _run_admin_api_server(self):
        from aiohttp import web

        @web.middleware
        async def auth_middleware(request, handler):
            async with self._api_sem:
                if request.path.startswith("/admin"):
                    auth_header = request.headers.get("Authorization")
                    if (
                        not auth_header
                        or auth_header != f"Bearer {self.settings.admin_api_key}"
                    ):
                        audit_logger.warning(
                            "unauthorized_admin_api_access",
                            extra={
                                "context": {
                                    "path": request.path,
                                    "source_ip": request.remote,
                                }
                            },
                        )
                        return web.Response(status=401, text="Unauthorized")
            return await handler(request)

        app = web.Application(middlewares=[auth_middleware])

        async def handle_health(request):
            return web.json_response(await self.health_check())

        async def handle_metrics(request):
            return web.Response(body=generate_latest(), content_type="text/plain")

        async def handle_reload(request):
            try:
                if not request.can_read_body:
                    return web.Response(
                        status=400, text="Request body required for reload."
                    )
                data = await request.json()
                new_settings = SlackGatewaySettings(**data)
                if new_settings == self.settings:
                    return web.Response(
                        status=200, text="No configuration changes detected."
                    )
                await self.reload_config(new_settings)
                return web.Response(text="Configuration reload initiated.")
            except (ValidationError, json.JSONDecodeError) as e:
                return web.Response(status=400, text=f"Invalid configuration: {e}")

        async def handle_pause(request):
            target_name = request.match_info["name"]
            if gw := self._gateways.get(target_name):
                gw.pause()
                return web.Response(text=f"Target '{target_name}' paused.")
            return web.Response(status=404, text="Target not found.")

        async def handle_resume(request):
            target_name = request.match_info["name"]
            if gw := self._gateways.get(target_name):
                gw.resume()
                return web.Response(text=f"Target '{target_name}' resumed.")
            return web.Response(status=404, text="Target not found.")

        app.router.add_get("/health", handle_health)
        app.router.add_get("/metrics", handle_metrics)
        app.router.add_post("/admin/reload", handle_reload)
        app.router.add_post("/admin/targets/{name}/pause", handle_pause)
        app.router.add_post("/admin/targets/{name}/resume", handle_resume)

        runner = web.AppRunner(app)
        await runner.setup()

        ssl_context = None
        if PROD_MODE and self.settings.cert_path and self.settings.key_path:
            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            try:
                ssl_context.load_cert_chain(
                    self.settings.cert_path, self.settings.key_path
                )
            except FileNotFoundError as e:
                raise AnalyzerCriticalError(f"SSL certificate/key not found: {e}")

        site = web.TCPSite(
            runner,
            self.settings.admin_api_host,
            self.settings.admin_api_port,
            ssl_context=ssl_context,
        )

        try:
            await site.start()
            main_logger.info(
                f"Admin API server started on {'https' if ssl_context else 'http'}://{self.settings.admin_api_host}:{self.settings.admin_api_port}"
            )
            audit_logger.info(
                "admin_api_started",
                extra={
                    "context": {
                        "host": self.settings.admin_api_host,
                        "port": self.settings.admin_api_port,
                        "protocol": "https" if ssl_context else "http",
                    }
                },
            )
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        except OSError as e:
            raise AnalyzerCriticalError(
                f"Failed to start admin API server on port {self.settings.admin_api_port}: {e}."
            )
        finally:
            await runner.cleanup()


# ---- Global Instances & Application Lifecycle ----
DEAD_LETTER_DIR = os.environ.get(
    "SLACK_GATEWAY_DEAD_LETTER_DIR", "/var/lib/slack_gateway_dead_letters"
)
try:
    if not os.path.exists(DEAD_LETTER_DIR):
        os.makedirs(DEAD_LETTER_DIR, exist_ok=True)
        os.chmod(DEAD_LETTER_DIR, 0o700)
except (PermissionError, OSError) as e:
    # Fall back to a temp directory if we can't create the default one
    import tempfile
    DEAD_LETTER_DIR = os.path.join(tempfile.gettempdir(), "slack_gateway_dead_letters")
    try:
        os.makedirs(DEAD_LETTER_DIR, exist_ok=True)
    except (PermissionError, OSError) as fallback_error:
        # Last resort - use current working directory
        DEAD_LETTER_DIR = os.path.join(os.getcwd(), ".slack_gateway_dead_letters")
        os.makedirs(DEAD_LETTER_DIR, exist_ok=True)
        main_logger.warning(f"Could not create temp dead letter directory, using {DEAD_LETTER_DIR}: {fallback_error}")
    else:
        main_logger.warning(f"Could not create dead letter directory at default location, using {DEAD_LETTER_DIR}: {e}")


async def dead_letter_to_file(event: SlackEvent, reason: str):
    log_line = json.dumps(
        {
            "event": event.model_dump(),
            "failure_reason": reason,
            "timestamp": time.time(),
        }
    )
    filepath = os.path.join(
        DEAD_LETTER_DIR, f"slack_dead_letters.{time.strftime('%Y%m%d')}.log"
    )

    encryption_key = SECRETS_MANAGER.get_secret(
        "SLACK_GATEWAY_DEAD_LETTER_ENCRYPTION_KEY", required=False
    )

    if encryption_key:
        cipher = Fernet(encryption_key.encode())
        log_line = cipher.encrypt(log_line.encode("utf-8")).decode("utf-8")

    async with aiofiles.open(filepath, "a") as f:
        await f.write(log_line + "\n")
        os.chmod(filepath, 0o600)


slack_gateway_manager: Optional[SlackGatewayManager] = None


@asynccontextmanager
async def app_lifecycle(main_func: Callable):
    global slack_gateway_manager
    try:
        if PROD_MODE:
            slack_settings = SlackGatewaySettings.load_from_secure_vault()
        else:
            main_logger.warning("Running in non-production mode.")
            slack_settings = SlackGatewaySettings(
                signing_secret=os.environ.get(
                    "SLACK_GATEWAY_SIGNING_SECRET", "non-prod-signing-secret"
                ),
                admin_api_key=os.environ.get(
                    "SLACK_GATEWAY_ADMIN_API_KEY", "non-prod-admin-key"
                ),
                targets=[
                    SlackTarget(
                        name="alerts",
                        webhook_url=os.environ.get(
                            "SLACK_ALERTS_URL", "https://localhost/alerts"
                        ),
                    ),
                    SlackTarget(
                        name="audit",
                        webhook_url=os.environ.get(
                            "SLACK_AUDIT_URL", "https://localhost/audit"
                        ),
                    ),
                ],
                url_allowlist=["^https://localhost", "^https://hooks.slack.com"],
            )

        slack_metrics = SlackMetrics()
        slack_gateway_manager = SlackGatewayManager(
            slack_settings, slack_metrics, dead_letter_hook=dead_letter_to_file
        )

        await slack_gateway_manager.startup()
        await main_func()
    except (ValidationError, RuntimeError, KeyError, AnalyzerCriticalError) as e:
        main_logger.critical(f"Critical initialization failure. Exiting. Error: {e}")
        alert_operator(
            f"Critical initialization failure. Exiting. Error: {e}", "CRITICAL"
        )
        sys.exit(1)
    finally:
        if slack_gateway_manager:
            await slack_gateway_manager.shutdown()


if __name__ == "__main__":
    if PROD_MODE:
        main_logger.critical("Refusing to run __main__ block in production mode.")
        sys.exit(1)

    async def main_example():
        main_logger.info("Unrivaled Slack Gateway example started.")
        await slack_gateway_manager.publish(
            "alerts",
            "database_connection_failed",
            {"db_host": "prod-db-1"},
            severity="critical",
        )
        await slack_gateway_manager.publish(
            "audit", "user_logged_in", {"user_id": "user-123"}, severity="info"
        )
        await slack_gateway_manager.publish(
            "alerts",
            "sensitive_info_exposed",
            {"user_id": "user-456", "token": "super-secret-token-123"},
            severity="critical",
        )

        main_logger.info(
            "Published example events. Waiting for a few seconds to let workers process."
        )
        await asyncio.sleep(10)

        main_logger.info("Simulating an admin API call to check health.")
        health_status = await slack_gateway_manager.health_check()
        main_logger.info(f"Health Status: {json.dumps(health_status, indent=2)}")

        main_logger.info("Example run finished.")

    asyncio.run(app_lifecycle(main_example))
