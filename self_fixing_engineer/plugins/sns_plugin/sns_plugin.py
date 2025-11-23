import os
import sys
import asyncio
import time
import json
import logging
import socket
import random
import hmac
import hashlib
import importlib.metadata
import re
import ssl
import uuid
from typing import (
    Dict,
    Any,
    Optional,
    List,
    Callable,
    Awaitable,
    Protocol,
    Literal,
)
from contextlib import asynccontextmanager
from collections import deque
from logging.handlers import RotatingFileHandler

try:
    import fcntl

    def file_lock(fd):
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)

    def file_unlock(fd):
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)

except ImportError:
    try:
        import msvcrt

        def file_lock(fd):
            msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)

        def file_unlock(fd):
            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)

    except ImportError:
        fcntl = None

        def file_lock(fd):
            pass

        def file_unlock(fd):
            pass


import aiohttp
import aiofiles
from pydantic import BaseModel, ValidationError, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pythonjsonlogger import jsonlogger
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from cryptography.fernet import Fernet
import psutil

# Import centralized exceptions
from self_fixing_engineer.exceptions import AnalyzerCriticalError

# ---- PROD MODE ENFORCEMENT ----
PROD_MODE = os.environ.get("PROD_MODE", "false").lower() == "true"

# --- Core integrations for multi-plugin production readiness ---
try:
    from core_utils import alert_operator
    from core_audit import audit_logger
    from core_secrets import SECRETS_MANAGER
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
            "SNS_AUDIT_LOG_HMAC_KEY", required=PROD_MODE
        ).encode()

    def add_fields(self, log_record, message_dict):
        super().add_fields(log_record, message_dict)
        log_record["timestamp"] = time.time()
        log_record["hostname"] = socket.gethostname()
        log_record["service_name"] = "sns_gateway"

        payload = {k: v for k, v in log_record.items() if k not in ["signature"]}
        payload_str = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        signature = hmac.new(
            self._hmac_key, payload_str.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        log_record["signature"] = signature


AUDIT_LOG_PATH = os.environ.get("SNS_AUDIT_LOG_FILE", "/var/log/sns_audit.log")
try:
    os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
    audit_log_handler = RotatingFileHandler(
        AUDIT_LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    os.chmod(AUDIT_LOG_PATH, 0o600)
except Exception as e:
    main_logger = logging.getLogger("sns_plugin")
    main_logger.warning(
        f"Could not set up audit log file at {AUDIT_LOG_PATH}: {e}. Using a dummy handler."
    )

    class DummyHandler(logging.Handler):
        def emit(self, record):
            pass

    audit_log_handler = DummyHandler()

audit_log_handler.setFormatter(
    AuditJsonFormatter("%(timestamp)s %(hostname)s %(service_name)s %(levelname)s %(message)s")
)
audit_logger = logging.getLogger("sns_audit")
audit_logger.setLevel(logging.INFO)
if not audit_logger.handlers:
    audit_logger.addHandler(audit_log_handler)

main_logger = logging.getLogger("sns_plugin")
main_logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
log_handler = logging.StreamHandler()
log_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log_handler.setFormatter(log_formatter)
if not main_logger.handlers:
    main_logger.addHandler(log_handler)

# ---- OpenTelemetry for Distributed Tracing ----
OPENTELEMETRY_AVAILABLE = False
tracer = None
TraceContextTextMapPropagator = None

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )
    from opentelemetry.context import get_current
    from opentelemetry.sdk.trace.sampling import ProbabilitySampler

    resource = Resource(
        attributes={SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", "sns-gateway-service")}
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
    main_logger.warning("OpenTelemetry SDK not found. Distributed tracing will be disabled.")
except Exception as e:
    main_logger.critical(f"Failed to initialize OpenTelemetry: {e}. Exiting.")
    alert_operator(f"Failed to initialize OpenTelemetry: {e}", "CRITICAL")
    sys.exit(1)


# ---- 1. Multi-Tenant & Dynamic Configuration ----
class SNSTarget(BaseSettings):
    """Configuration for a single SNS topic target."""

    name: str
    topic_arn: str
    region: str
    access_key_id: str = Field(repr=False)
    secret_access_key: str = Field(repr=False)
    serializer: str = "json_serializer"
    url_endpoint: Optional[str] = None

    @field_validator("topic_arn")
    @classmethod
    def validate_topic_arn(cls, v: str) -> str:
        if not v.startswith("arn:aws:sns:"):
            raise ValueError("Invalid SNS Topic ARN format.")
        return v


class SNSGatewaySettings(BaseSettings):
    """Manages all SNS Gateway configuration."""

    model_config = SettingsConfigDict(env_prefix="SNS_GATEWAY_")

    signing_secret: str = Field(..., repr=False)
    admin_api_key: str = Field(..., repr=False)
    encryption_key: Optional[str] = Field(None, repr=False)
    cert_path: Optional[str] = Field(None)
    key_path: Optional[str] = Field(None)

    targets: List[SNSTarget] = []
    persistence_dir: str = "/var/lib/sns_gateway"

    min_workers: int = 1
    max_workers: int = 4
    queue_size_per_worker: int = 250
    worker_scaling_interval: int = 5

    max_queue_size: int = 10000
    worker_batch_size: int = 50
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
    admin_api_ip_allowlist: List[str] = ["127.0.0.1", "localhost"]

    verify_ssl: bool = True
    admin_api_enabled: bool = True
    admin_api_port: int = 9878
    admin_api_host: str = "127.0.0.1"

    strict_plugins: bool = True
    heartbeat_interval: int = Field(30)
    compaction_interval: int = Field(3600)
    max_concurrent_per_region: int = Field(5, ge=1)

    def __setattr__(self, name, value):
        if PROD_MODE and hasattr(self, name):
            raise AttributeError("Configuration is immutable in production mode")
        super().__setattr__(name, value)

    @field_validator("signing_secret", "admin_api_key")
    @classmethod
    def validate_secrets(cls, v: str, info) -> str:
        if v in ("default-sns-secret-key-change-me", ""):
            raise ValueError(
                f"CRITICAL: The {info.field_name} must not be the default value or empty. Use a secure vault."
            )
        return v

    @field_validator("admin_api_host")
    @classmethod
    def validate_admin_api_host(cls, v: str) -> str:
        if PROD_MODE and v not in ["127.0.0.1", "localhost"]:
            raise ValueError("In production, the admin API must only be exposed on localhost.")
        return v

    @classmethod
    def load_from_secure_vault(cls) -> "SNSGatewaySettings":
        main_logger.info("Loading secrets and configuration from secure vault...")
        try:
            settings_dict = {
                "signing_secret": SECRETS_MANAGER.get_secret("SNS_GATEWAY_SIGNING_SECRET"),
                "admin_api_key": SECRETS_MANAGER.get_secret("SNS_GATEWAY_ADMIN_API_KEY"),
                "encryption_key": SECRETS_MANAGER.get_secret(
                    "SNS_GATEWAY_ENCRYPTION_KEY", required=False
                ),
                "cert_path": SECRETS_MANAGER.get_secret("SNS_GATEWAY_API_CERT", required=False),
                "key_path": SECRETS_MANAGER.get_secret("SNS_GATEWAY_API_KEY", required=False),
            }

            # Check for key expiry
            key_expiry = SECRETS_MANAGER.get_secret(
                "SNS_GATEWAY_ENCRYPTION_KEY_EXPIRY", required=False
            )
            if key_expiry and time.time() > float(key_expiry):
                raise ValueError("Encryption key expired. Aborting startup.")

            for key in [
                "persistence_dir",
                "min_workers",
                "max_workers",
                "max_queue_size",
                "worker_batch_size",
                "worker_linger_sec",
                "max_concurrent_requests",
                "requests_per_second_limit",
                "max_retries",
                "retry_backoff_factor",
                "circuit_breaker_threshold",
                "circuit_breaker_reset_sec",
                "admin_api_port",
                "admin_api_host",
                "dry_run",
                "dry_run_failure_rate",
                "verify_ssl",
                "admin_api_enabled",
                "strict_plugins",
                "url_allowlist",
                "admin_api_ip_allowlist",
                "heartbeat_interval",
                "compaction_interval",
                "max_concurrent_per_region",
            ]:
                env_key = f"SNS_GATEWAY_{key.upper()}"
                if env_key in os.environ:
                    if PROD_MODE:
                        main_logger.warning(
                            f"Env override for {key} in production. Use a secure vault for full compliance."
                        )
                    settings_dict[key] = os.environ[env_key]

            targets_json = SECRETS_MANAGER.get_secret("SNS_GATEWAY_TARGETS", required=False)
            if targets_json:
                settings_dict["targets"] = [
                    SNSTarget.model_validate(t) for t in json.loads(targets_json)
                ]

            settings = cls.model_validate(settings_dict)

            if PROD_MODE:
                if not settings.encryption_key:
                    raise ValueError("Encryption must be enabled in production for compliance.")

                for target in settings.targets:
                    if not target.access_key_id.startswith("AKIA"):
                        raise ValueError(
                            f"Invalid AWS access key format for '{target.name}'. Must start with 'AKIA'."
                        )

                    endpoint = target.url_endpoint or f"https://sns.{target.region}.amazonaws.com"
                    if not settings.url_allowlist and not re.match(
                        r"^https://sns\..*\.amazonaws\.com", endpoint
                    ):
                        raise ValueError(
                            f"URL for SNS target '{target.name}' is not a valid AWS SNS endpoint and no allowlist is provided."
                        )
                    if settings.url_allowlist and not any(
                        re.match(pattern, endpoint) for pattern in settings.url_allowlist
                    ):
                        raise ValueError(
                            f"URL for SNS target '{target.name}' not in allowed_urls list."
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
class SNSMetrics:
    NOTIFICATIONS_QUEUED = Counter(
        "sns_notifications_queued_total",
        "Notifications placed into the send queue.",
        ["target_name", "event_name", "severity"],
    )
    NOTIFICATIONS_DROPPED = Counter(
        "sns_notifications_dropped_total",
        "Notifications dropped due to a full queue.",
        ["target_name", "event_name"],
    )
    NOTIFICATIONS_SENT_SUCCESS = Counter(
        "sns_notifications_sent_success_total",
        "Notifications successfully sent to SNS.",
        ["target_name"],
    )
    NOTIFICATIONS_FAILED_PERMANENTLY = Counter(
        "sns_notifications_failed_permanently_total",
        "Notifications that failed to send after all retries.",
        ["target_name", "reason"],
    )
    DEAD_LETTER_NOTIFICATIONS = Counter(
        "sns_dead_letter_notifications_total",
        "Notifications sent to the dead-letter handler.",
        ["target_name", "reason"],
    )
    SEND_LATENCY = Histogram(
        "sns_send_latency_seconds",
        "Latency of a successful batch send operation.",
        ["target_name"],
    )
    CIRCUIT_BREAKER_STATUS = Gauge(
        "sns_circuit_breaker_status",
        "The status of the circuit breaker (1 for open, 0 for closed).",
        ["target_name"],
    )
    RATE_LIMIT_THROTTLED_SECONDS = Counter(
        "sns_rate_limit_throttled_seconds_total",
        "Total time in seconds spent waiting for the rate limiter.",
        ["target_name"],
    )
    ACTIVE_WORKERS = Gauge(
        "sns_active_workers",
        "Number of active worker tasks for a target.",
        ["target_name"],
    )
    NON_TRACED_NOTIFICATIONS = Counter(
        "sns_non_traced_notifications_total",
        "Events sent without OpenTelemetry tracing.",
        ["target_name"],
    )
    QUEUE_SIZE = Gauge("sns_queue_size", "Current size of the event queue.", ["target_name"])
    WAL_COMPACTIONS = Counter(
        "sns_wal_compactions_total",
        "Number of WAL compactions performed.",
        ["target_name"],
    )
    QUEUE_LATENCY = Histogram(
        "sns_queue_latency_seconds", "Time from enqueue to processing.", ["target_name"]
    )
    RETRY_ATTEMPTS = Histogram(
        "sns_retry_attempts", "Number of retry attempts per batch.", ["target_name"]
    )

    SYSTEM_CPU_USAGE = Gauge("sns_system_cpu_usage_percent", "CPU usage percentage.")
    SYSTEM_MEMORY_USAGE = Gauge("sns_system_memory_usage_bytes", "Memory usage in bytes.")

    def update_system_metrics(self):
        self.SYSTEM_CPU_USAGE.set(psutil.cpu_percent())
        self.SYSTEM_MEMORY_USAGE.set(psutil.virtual_memory().used)


# ---- 3. End-to-End Integrity Event Schema ----
class SNSEvent(BaseModel):
    event_name: str
    timestamp: float = Field(default_factory=time.time)
    details: Dict[str, Any] = Field(default_factory=dict)
    severity: Literal["info", "warning", "error", "critical"] = "info"
    trace_context: Optional[Dict[str, str]] = None
    sequence_id: int = 0
    signature: str = ""
    enqueue_time: float = 0.0
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    SENSITIVE_KEYS = re.compile(
        r".*(password|secret|key|token|pii|ssn|credit_card|credentials).*",
        re.IGNORECASE,
    )
    SENSITIVE_PATTERNS = [
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
        re.compile(r"\b\d{3}[-.\s]??\d{3}[-.\s]??\d{4}\b"),
        re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"),
    ]

    @field_validator("details")
    @classmethod
    def scrub_sensitive_details(cls, v: Dict[str, Any]) -> Dict[str, Any]:
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


# ---- 4. Pluggable Serialization & Persistence ----
class Serializer(Protocol):
    def encode_payload(self, event: SNSEvent) -> str: ...


class JsonSerializer:
    def encode_payload(self, event: SNSEvent) -> str:
        return event.model_dump_json(exclude_none=True)


class EventQueue(Protocol):
    async def startup(self): ...
    async def shutdown(self): ...
    async def put(self, item: SNSEvent): ...
    async def get(self) -> SNSEvent: ...
    def qsize(self) -> int: ...
    async def task_done(self): ...
    async def flush(self, timeout: int = 60): ...


class PersistentWALQueue(EventQueue):
    def __init__(
        self,
        target_name: str,
        persistence_dir: str,
        max_in_memory_size: int,
        metrics: SNSMetrics,
        dead_letter_hook: Callable,
        encryption_key: Optional[str] = None,
    ):
        self._dir = os.path.join(
            persistence_dir,
            f"{target_name}_{hashlib.sha256(target_name.encode()).hexdigest()}",
        )
        self._target_name = target_name
        self._metrics = metrics
        self._dead_letter_hook = dead_letter_hook
        self._cipher: Optional[Fernet] = None
        if encryption_key:
            self._cipher = Fernet(encryption_key.encode())

        self._max_log_size = 10 * 1024 * 1024
        self._log_rotation_interval = 86400
        self._last_rotation_time = time.time()
        self._hmac_key = SECRETS_MANAGER.get_secret("SNS_WAL_HMAC_KEY", required=PROD_MODE).encode()

        self._mem_queue = asyncio.Queue(maxsize=max_in_memory_size)
        self._write_lock = asyncio.Lock()
        self._current_write_log: Optional[aiofiles.threadpool.binary.AsyncBufferedIOBase] = None
        self._current_log_path: Optional[str] = None
        self._compactor_task: Optional[asyncio.Task] = None

        os.makedirs(self._dir, exist_ok=True)
        os.chmod(self._dir, 0o700)

    async def startup(self):
        if PROD_MODE and fcntl is None:
            raise AnalyzerCriticalError("File locking unavailable in prod—required for WAL safety.")
        try:
            log_files = sorted(
                [f for f in os.listdir(self._dir) if f.startswith("events.") and f.endswith(".log")]
            )
            for log_file in log_files:
                path = os.path.join(self._dir, log_file)
                async with aiofiles.open(path, "rb" if self._cipher else "r") as f:
                    file_lock(f)
                    async for line in f:
                        if line.strip():
                            try:
                                # Data can be bytes (encrypted) or string (unencrypted)
                                sig, data = (
                                    line.strip().split(b":", 1)
                                    if self._cipher
                                    else line.strip().split(":", 1)
                                )

                                data_to_check = (
                                    data.encode("utf-8") if isinstance(data, str) else data
                                )

                                if not hmac.compare_digest(
                                    sig.decode("utf-8"),
                                    hmac.new(
                                        self._hmac_key, data_to_check, hashlib.sha256
                                    ).hexdigest(),
                                ):
                                    raise ValueError("WAL integrity check failed.")

                                decrypted_line = (
                                    self._cipher.decrypt(data)
                                    if self._cipher
                                    else data.encode("utf-8")
                                )
                                event = SNSEvent.model_validate_json(decrypted_line)
                                await self._mem_queue.put(event)
                            except Exception as e:
                                main_logger.warning(
                                    f"Skipping corrupt WAL line: {e}",
                                    extra={"context": {"line_snippet": line[:50]}},
                                )
                                await self._dead_letter_hook(
                                    SNSEvent(
                                        event_name="corrupt_wal",
                                        details={"line": str(line[:50])},
                                    ),
                                    "wal_corrupt",
                                )
                    file_unlock(f)
            main_logger.info(
                f"Loaded {self._mem_queue.qsize()} events from disk for target {self._target_name}."
            )
            audit_logger.info(
                "WAL loaded from disk.",
                extra={
                    "context": {
                        "target": self._target_name,
                        "event_count": self._mem_queue.qsize(),
                    }
                },
            )
        except OSError as e:
            main_logger.critical(
                f"Failed to load WAL from disk: {e}. Exiting.",
                extra={"context": {"target": self._target_name}},
            )
            raise RuntimeError("Critical startup failure: WAL could not be loaded.") from e
        await self._open_next_log_segment()
        self._compactor_task = asyncio.create_task(self._wal_compactor())

    def _create_signature(self, event: SNSEvent) -> str:
        canonical_event = (
            f"{event.sequence_id}|{event.event_name}|{json.dumps(event.details, sort_keys=True)}"
        )
        return hmac.new(
            SECRETS_MANAGER.get_secret("SNS_GATEWAY_SIGNING_SECRET").encode(),
            canonical_event.encode(),
            hashlib.sha256,
        ).hexdigest()

    async def _open_next_log_segment(self):
        async with self._write_lock:
            if self._current_write_log:
                await self._current_write_log.flush()
                await self._current_write_log.close()
            temp_path = os.path.join(self._dir, f"events.temp.{time.strftime('%Y%m%d_%H%M%S')}.log")
            self._current_write_log = await aiofiles.open(temp_path, "ab")
            os.chmod(temp_path, 0o600)
            self._current_log_path = os.path.join(
                self._dir, f"events.{time.strftime('%Y%m%d_%H%M%S')}.log"
            )
            os.rename(temp_path, self._current_log_path)
            self._last_rotation_time = time.time()

    async def put(self, item: SNSEvent):
        async with self._write_lock:
            if (
                not self._current_write_log
                or await aiofiles.os.stat(self._current_log_path).st_size > self._max_log_size
                or time.time() - self._last_rotation_time > self._log_rotation_interval
            ):
                await self._open_next_log_segment()

            line = item.model_dump_json().encode("utf-8")
            if self._cipher:
                line = self._cipher.encrypt(line)

            signature = hmac.new(self._hmac_key, line, hashlib.sha256).hexdigest()
            await self._current_write_log.write(f"{signature}:{line.decode()}\n".encode())
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
            alert_operator("CRITICAL: Shutdown timeout exceeded. Events may be lost.", "CRITICAL")

    async def _wal_compactor(self):
        while not self._compactor_task.done():
            await asyncio.sleep(self.global_settings.compaction_interval)
            log_files = sorted(
                [f for f in os.listdir(self._dir) if f.startswith("events.") and f.endswith(".log")]
            )
            if len(log_files) > 2:
                for old_file in log_files[:-2]:
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
            extra={"context": {"target": self._target_name}},
        )


# ---- 5. Advanced Resilience Patterns ----
class CircuitBreaker:
    def __init__(self, threshold: int, reset_seconds: int, metrics: SNSMetrics, target_name: str):
        self._threshold, self._reset_seconds = threshold, reset_seconds
        self._metrics, self._target_name = metrics, target_name
        self._failure_count, self._is_open, self._last_failure_time = 0, False, 0.0
        self._metrics.CIRCUIT_BREAKER_STATUS.labels(target_name=self._target_name).set(0)

    def check(self):
        if self._is_open:
            jitter = random.uniform(0, self._reset_seconds * 0.1)
            if time.monotonic() - self._last_failure_time > (self._reset_seconds + jitter):
                self._is_open, self._failure_count = False, 0
                self._metrics.CIRCUIT_BREAKER_STATUS.labels(target_name=self._target_name).set(0)
                main_logger.warning(
                    "Circuit breaker has been reset.",
                    extra={"context": {"target": self._target_name}},
                )
                audit_logger.info(
                    "circuit_breaker_reset",
                    extra={"context": {"target": self._target_name}},
                )
            else:
                raise ConnectionAbortedError(f"Circuit breaker for {self._target_name} is open.")

    def record_failure(self):
        self._failure_count += 1
        if self._failure_count >= self._threshold and not self._is_open:
            self._is_open, self._last_failure_time = True, time.monotonic()
            self._metrics.CIRCUIT_BREAKER_STATUS.labels(target_name=self._target_name).set(1)
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
                f"CRITICAL: SNS circuit breaker tripped for {self._target_name}.",
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
    def __init__(self, rate: float, capacity: float, metrics: SNSMetrics, target_name: str):
        self._rate, self._capacity = rate, max(rate * 10, capacity)
        self._metrics, self._target_name = metrics, target_name
        self._tokens, self._last_refill = self._capacity, time.monotonic()
        self._last_response_status = 200

    async def acquire(self):
        if self._last_response_status == 429:
            self._rate = max(self._rate * 0.5, 0.1)

        while self._tokens < 1:
            self._refill()
            throttled_time = max(0, (1 - self._tokens) / self._rate)
            if throttled_time > 0:
                self._metrics.RATE_LIMIT_THROTTLED_SECONDS.labels(
                    target_name=self._target_name
                ).inc(throttled_time)
                await asyncio.sleep(throttled_time)
        self._tokens -= 1

    def _refill(self):
        now = time.monotonic()
        elapsed = max(0, now - self._last_refill)
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now

    def record_status(self, status: int):
        self._last_response_status = status


# ---- 6. The Unrivaled SNS Gateway Manager ----
DeadLetterHook = Callable[[SNSEvent, str], Awaitable[None]]


class SNSGateway:
    def __init__(
        self,
        target_config: SNSTarget,
        global_settings: SNSGatewaySettings,
        metrics: SNSMetrics,
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
            metrics,
            dead_letter_hook,
            encryption_key=global_settings.encryption_key,
        )
        self._fallback_queue: EventQueue = asyncio.Queue(maxsize=global_settings.max_queue_size)

        self._workers: List[asyncio.Task] = []
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        self._concurrency_limiter = asyncio.Semaphore(global_settings.max_concurrent_per_region)
        self._hostname = socket.gethostname()
        self._health_stats = {"processed_count": 0, "last_processed_time": 0.0}
        self._is_paused = False
        self._shutdown_event = asyncio.Event()
        self._sequence_id = 0
        self._sequence_lock = asyncio.Lock()
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._queue_history = deque(maxlen=3)
        self._scale_down_timer = 0

    async def startup(self):
        await self._event_queue.startup()

        # Load the per-target sequence counter
        await self._load_sequence_counter()

        self._shutdown_event.clear()
        self._workers.append(asyncio.create_task(self._worker_manager()))
        self._heartbeat_task = asyncio.create_task(self._heartbeat())
        main_logger.info(f"SNS Gateway started for target '{self.target_config.name}'.")

    async def shutdown(self):
        main_logger.info(f"Initiating graceful shutdown for target '{self.target_config.name}'.")
        self._shutdown_event.set()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        for _ in self._workers:
            await self._event_queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)

        await self._event_queue.flush(
            timeout=self.global_settings.max_queue_size / self.global_settings.max_workers + 10
        )
        await self._event_queue.shutdown()
        async with self._session_lock:
            if self._session and not self._session.closed:
                await self._session.close()

        await self._save_sequence_counter(self._sequence_id)
        main_logger.info(f"Graceful shutdown complete for target '{self.target_config.name}'.")

    def pause(self):
        self._is_paused = True
        main_logger.warning(f"Target '{self.target_config.name}' is paused.")

    def resume(self):
        self._is_paused = False
        main_logger.info(f"Target '{self.target_config.name}' is resumed.")

    async def _load_sequence_counter(self):
        path = os.path.join(
            self.global_settings.persistence_dir, f"{self.target_config.name}_seq.txt"
        )
        try:
            async with aiofiles.open(path, "r") as f:
                file_lock(f)
                self._sequence_id = int(await f.read())
                file_unlock(f)
        except (OSError, ValueError):
            self._sequence_id = 0
            main_logger.warning(
                "Failed to load sequence counter, starting from 0.",
                extra={"context": {"target": self.target_config.name}},
            )

    async def _save_sequence_counter(self, seq_id: int):
        path = os.path.join(
            self.global_settings.persistence_dir, f"{self.target_config.name}_seq.txt"
        )
        try:
            async with aiofiles.open(path, "w") as f:
                file_lock(f)
                await f.write(str(seq_id))
                file_unlock(f)
            os.chmod(path, 0o600)
        except OSError as e:
            main_logger.critical(
                "Failed to persist sequence counter.",
                extra={"context": {"target": self.target_config.name, "error": str(e)}},
            )
            audit_logger.critical(
                "sequence_counter_persistence_failure",
                extra={"context": {"target": self.target_config.name, "error": str(e)}},
            )
            raise

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._session_lock:
            if self._session is None or self._session.closed:
                ssl_context = ssl.create_default_context()
                ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3
                if PROD_MODE and self.global_settings.cert_path and self.global_settings.key_path:
                    ssl_context.load_cert_chain(
                        self.global_settings.cert_path, self.global_settings.key_path
                    )

                timeout = aiohttp.ClientTimeout(total=self.global_settings.retry_backoff_factor * 5)
                self._session = aiohttp.ClientSession(timeout=timeout, ssl=ssl_context)
        return self._session

    async def _handle_dead_letter(self, event: SNSEvent, reason: str):
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
                    extra={"context": {"error": str(e), "target": self.target_config.name}},
                )

    async def publish(self, event: SNSEvent):
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

    async def _send_batch(self, batch: List[SNSEvent]):
        deduped_batch = []
        seen_ids = set()
        for event in batch:
            if event.sequence_id not in seen_ids:
                deduped_batch.append(event)
                seen_ids.add(event.sequence_id)

        if not deduped_batch:
            return True

        with tracer.start_as_current_span(
            "sns_send_batch",
            attributes={
                "sns.target": self.target_config.name,
                "batch.size": len(deduped_batch),
                "event.names": [event.event_name for event in deduped_batch],
            },
        ):
            try:
                self.circuit_breaker.check()
            except ConnectionAbortedError:
                main_logger.warning(
                    "Batch dropped due to circuit breaker being open.",
                    extra={"context": {"target": self.target_config.name}},
                )
                for event in deduped_batch:
                    await self._handle_dead_letter(event, "circuit_breaker_open")
                return False

            payload_data = [self.serializer.encode_payload(event) for event in deduped_batch]
            headers = {"Content-Type": "application/json"}
            attempt = 0
            while attempt < self.global_settings.max_retries:
                self.metrics.RETRY_ATTEMPTS.labels(target_name=self.target_config.name).observe(
                    attempt
                )
                await self.rate_limiter.acquire()
                async with self._concurrency_limiter:
                    start_time = time.monotonic()
                    try:
                        session = await self._get_session()
                        timeout = aiohttp.ClientTimeout(
                            total=self.global_settings.retry_backoff_factor**attempt * 5
                        )

                        endpoint = (
                            self.target_config.url_endpoint
                            or f"https://sns.{self.target_config.region}.amazonaws.com"
                        )

                        sns_payload = {
                            "Action": "PublishBatch",
                            "TopicArn": self.target_config.topic_arn,
                            "PublishBatchRequestEntries": [
                                {"Id": event.correlation_id, "Message": data}
                                for event, data in zip(deduped_batch, payload_data)
                            ],
                        }

                        async with session.post(
                            endpoint,
                            json=sns_payload,
                            headers=headers,
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
                                ).inc(len(deduped_batch))
                                self.circuit_breaker.record_success()
                                audit_logger.info(
                                    "notification_sent_success",
                                    extra={
                                        "context": {
                                            "target": self.target_config.name,
                                            "batch_size": len(deduped_batch),
                                        }
                                    },
                                )
                                return True
                            elif resp.status == 429:
                                retry_after = int(resp.headers.get("Retry-After", "5"))
                                main_logger.warning(
                                    f"Rate limited by SNS API. Backing off for {retry_after} seconds.",
                                    extra={"context": {"target": self.target_config.name}},
                                )
                                await asyncio.sleep(retry_after)
                                continue
                            elif 400 <= resp.status < 500:
                                error_text = await resp.text()
                                main_logger.error(
                                    "Permanent failure sending to SNS (client error).",
                                    extra={
                                        "context": {
                                            "target": self.target_config.name,
                                            "error": error_text,
                                            "status_code": resp.status,
                                        }
                                    },
                                )
                                for event in deduped_batch:
                                    await self._handle_dead_letter(event, "client_error")
                                self.metrics.NOTIFICATIONS_FAILED_PERMANENTLY.labels(
                                    target_name=self.target_config.name,
                                    reason=f"client_error_{resp.status}",
                                ).inc(len(deduped_batch))
                                return False
                            else:
                                resp.raise_for_status()
                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        main_logger.warning(
                            "Temporary failure sending to SNS.",
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
                            for event in deduped_batch:
                                await self._handle_dead_letter(event, "service_unavailable")
                            self.metrics.NOTIFICATIONS_FAILED_PERMANENTLY.labels(
                                target_name=self.target_config.name,
                                reason="service_unavailable",
                            ).inc(len(deduped_batch))
                            return False
                attempt += 1
                if attempt < self.global_settings.max_retries:
                    await asyncio.sleep(self.global_settings.retry_backoff_factor**attempt)
            return False

    async def _worker(self, worker_id: int):
        main_logger.info(f"Starting worker {worker_id} for target {self.target_config.name}")
        audit_logger.info(
            "worker_started",
            extra={"context": {"target": self.target_config.name, "worker_id": worker_id}},
        )
        while not self._shutdown_event.is_set():
            try:
                self.metrics.QUEUE_SIZE.labels(target_name=self.target_config.name).set(
                    self._event_queue.qsize()
                )
                if self._is_paused:
                    await asyncio.sleep(1)
                    continue
                batch = []
                first_event = await self._event_queue.get()
                if first_event is None:
                    self._event_queue.task_done()
                    await self._event_queue.put(None)
                    break
                self.metrics.QUEUE_LATENCY.labels(target_name=self.target_config.name).observe(
                    time.time() - first_event.enqueue_time
                )
                batch.append(first_event)
                while len(batch) < self.global_settings.worker_batch_size:
                    try:
                        event = await asyncio.wait_for(
                            self._event_queue.get(),
                            self.global_settings.worker_linger_sec,
                        )
                        if event is None:
                            self._event_queue.put_nowait(None)
                            break
                        self.metrics.QUEUE_LATENCY.labels(
                            target_name=self.target_config.name
                        ).observe(time.time() - event.enqueue_time)
                        batch.append(event)
                    except asyncio.TimeoutError:
                        break

                success = False
                if self.global_settings.dry_run:
                    main_logger.info(
                        "[DRY RUN] Would send SNS notification.",
                        extra={
                            "context": {
                                "target": self.target_config.name,
                                "batch_size": len(batch),
                            }
                        },
                    )
                    if (
                        self.global_settings.dry_run_failure_rate > 0
                        and random.random() < self.global_settings.dry_run_failure_rate
                    ):
                        for event in batch:
                            await self._handle_dead_letter(event, "dry_run_simulated_failure")
                        success = False
                    else:
                        success = True
                else:
                    success = await self._send_batch(batch)

                if success:
                    self._health_stats["processed_count"] += len(batch)
                    self._health_stats["last_processed_time"] = time.time()
                for _ in batch:
                    await self._event_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                raise AnalyzerCriticalError(f"Unhandled exception in SNS worker: {e}.")
        main_logger.info(f"Stopping worker {worker_id} for target {self.target_config.name}")
        audit_logger.info(
            "worker_stopped",
            extra={"context": {"target": self.target_config.name, "worker_id": worker_id}},
        )

    async def _worker_manager(self):
        active_workers = [
            asyncio.create_task(self._worker(i)) for i in range(self.global_settings.min_workers)
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
                avg_queue > self.global_settings.queue_size_per_worker * len(active_workers)
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
            elif avg_queue == 0 and len(active_workers) > self.global_settings.min_workers:
                self._scale_down_timer += self.global_settings.worker_scaling_interval
                if self._scale_down_timer >= self.global_settings.worker_scaling_interval * 3:
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
                heartbeat_event = SNSEvent(
                    event_name="heartbeat",
                    details={"ping": "pong"},
                    severity="info",
                    sequence_id=0,
                    signature="",
                    correlation_id=str(uuid.uuid4()),
                )
                success = await self._send_batch([heartbeat_event])
                if success:
                    self.circuit_breaker.record_success()
                else:
                    self.circuit_breaker.record_failure()
            except Exception as e:
                self.circuit_breaker.record_failure()
                main_logger.warning(f"Heartbeat failed for {self.target_config.name}: {e}")
            await asyncio.sleep(self.global_settings.heartbeat_interval)


class SNSGatewayManager:
    def __init__(
        self,
        settings: SNSGatewaySettings,
        metrics: SNSMetrics,
        dead_letter_hook: Optional[DeadLetterHook] = None,
    ):
        self.settings = settings
        self.metrics = metrics
        self.dead_letter_hook = dead_letter_hook
        self._gateways: Dict[str, SNSGateway] = {}
        self._serializers: Dict[str, Serializer] = {"json_serializer": JsonSerializer()}
        self._rate_limiters: Dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()
        self._http_server_task: Optional[asyncio.Task] = None
        self._sequence_counters: Dict[str, int] = {}
        self._sequence_locks: Dict[str, asyncio.Lock] = {}
        self._admin_audit_log: Optional[aiofiles.threadpool.binary.AsyncBufferedIOBase] = None
        self._config_version: int = 0
        self._system_metrics_task: Optional[asyncio.Task] = None
        self._api_sem = asyncio.Semaphore(self.settings.max_concurrent_requests)

    async def startup(self):
        if PROD_MODE:
            if not fcntl:
                main_logger.critical(
                    "`fcntl` module not available. File locking is required for persistence in production mode. Exiting."
                )
                sys.exit(1)
            if self.settings.dry_run or self.settings.dry_run_failure_rate > 0:
                main_logger.critical(
                    "DRY_RUN or DRY_RUN_FAILURE_RATE is enabled in production mode. This is a critical error. Exiting."
                )
                sys.exit(1)
            if not OPENTELEMETRY_AVAILABLE:
                main_logger.critical("OpenTelemetry is mandatory in production. Exiting.")
                sys.exit(1)

        self._admin_audit_log = await aiofiles.open("admin_audit.log", "a")
        os.chmod("admin_audit.log", 0o600)
        await self._log_admin_action("startup", {"status": "starting"})

        main_logger.info("SNS Gateway Manager starting up.")
        self.load_serializers_from_plugins()
        await self.reload_config(self.settings)
        if self.settings.admin_api_enabled and self._http_server_task is None:
            self._http_server_task = asyncio.create_task(self._run_admin_api_server())

        self._system_metrics_task = asyncio.create_task(self._run_system_metrics_collector())

    async def _run_system_metrics_collector(self):
        while self._http_server_task is None or not self._http_server_task.done():
            self.metrics.update_system_metrics()
            await asyncio.sleep(5)

    async def shutdown(self):
        main_logger.info("Initiating SNS Gateway Manager shutdown.")
        await self._log_admin_action("shutdown", {"status": "stopping"})
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
        if self._admin_audit_log:
            await self._admin_audit_log.close()
        main_logger.info("SNS Gateway Manager shut down.")

    async def _log_admin_action(self, action: str, details: Dict[str, Any]):
        if self._admin_audit_log:
            log_entry = json.dumps({"timestamp": time.time(), "action": action, **details})
            await self._admin_audit_log.write(log_entry + "\n")
            await self._admin_audit_log.flush()

    def load_serializers_from_plugins(self, group="sns_gateway.serializers"):
        try:
            for entry_point in importlib.metadata.entry_points(group=group):
                try:
                    serializer_class = entry_point.load()
                    self.register_serializer(entry_point.name, serializer_class())
                except Exception as e:
                    main_logger.error(
                        f"Failed to load serializer plugin '{entry_point.name}'.",
                        extra={"context": {"error": str(e)}},
                    )
                    if self.settings.strict_plugins:
                        raise RuntimeError(
                            f"Critical serializer plugin '{entry_point.name}' failed to load."
                        )
        except TypeError:
            main_logger.warning(
                f"Falling back to legacy entry_points due to Python version {sys.version}"
            )
            for entry_point in importlib.metadata.entry_points().get(group, []):
                try:
                    serializer_class = entry_point.load()
                    self.register_serializer(entry_point.name, serializer_class())
                except Exception as e:
                    main_logger.error(
                        f"Failed to load serializer plugin '{entry_point.name}'.",
                        extra={"context": {"error": str(e)}},
                    )
                    if self.settings.strict_plugins:
                        raise RuntimeError(
                            f"Critical serializer plugin '{entry_point.name}' failed to load."
                        )

    def register_serializer(self, name: str, serializer: Serializer):
        self._serializers[name] = serializer
        main_logger.info(f"Registered new serializer: {name}")

    async def reload_config(self, new_settings: SNSGatewaySettings):
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
                limiter_key = f"{target.region}_{new_settings.requests_per_second_limit}"
                if limiter_key not in self._rate_limiters:
                    self._rate_limiters[limiter_key] = TokenBucket(
                        new_settings.requests_per_second_limit,
                        new_settings.requests_per_second_limit,
                        self.metrics,
                        limiter_key,
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

                limiter_key = f"{target_config.region}_{new_settings.requests_per_second_limit}"
                rate_limiter = self._rate_limiters[limiter_key]
                new_gateways[name] = SNSGateway(
                    target_config,
                    new_settings,
                    self.metrics,
                    serializer,
                    rate_limiter,
                    self.dead_letter_hook,
                )

            # The sequence counters are now managed by each gateway individually
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

    async def publish(self, target_name: str, event_name: str, details: Dict[str, Any], **kwargs):
        gateway = self._gateways.get(target_name)
        if not gateway:
            main_logger.warning(f"Publish to unknown target '{target_name}'. Event dropped.")
            audit_logger.warning(
                "publish_to_unknown_target",
                extra={"context": {"target": target_name, "event_name": event_name}},
            )
            return

        scrubbed_details = SNSEvent.scrub_sensitive_details(details)
        if scrubbed_details != details:
            main_logger.error("Sensitive data detected in event payload. Event dropped.")
            return

        async with gateway._sequence_lock:
            gateway._sequence_id += 1
            seq_id = gateway._sequence_id
            try:
                await gateway._save_sequence_counter(seq_id)
            except OSError:
                main_logger.critical(
                    "Failed to save sequence counter. This may lead to duplicate events on restart."
                )

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
            event = SNSEvent(
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
                "Invalid SNS event schema.",
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
                "rate_limiter_tokens": gw.rate_limiter._tokens,
                "wal_files": len(
                    [f for f in os.listdir(gw._event_queue._dir) if f.endswith(".log")]
                ),
                "status": (
                    "paused"
                    if gw._is_paused
                    else (
                        "healthy" if not gw.circuit_breaker._is_open else "unhealthy_circuit_open"
                    )
                ),
                **gw._health_stats,
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
            if request.remote not in self.settings.admin_api_ip_allowlist:
                return web.Response(status=403, text="Forbidden IP")
            if request.path.startswith("/admin"):
                auth_header = request.headers.get("Authorization")
                if not auth_header or auth_header != f"Bearer {self.settings.admin_api_key}":
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
                    return web.Response(status=400, text="Request body required for reload.")
                data = await request.json()
                new_settings = SNSGatewaySettings(**data)
                if new_settings.model_dump_json() == self.settings.model_dump_json():
                    return web.Response(status=200, text="No configuration changes detected.")
                await self._log_admin_action("api_reload", {"source_ip": request.remote})
                await self.reload_config(new_settings)
                return web.Response(text="Configuration reload initiated.")
            except json.JSONDecodeError:
                return web.Response(status=400, text="Invalid JSON body.")
            except ValidationError as e:
                return web.Response(status=400, text=f"Invalid configuration: {e}")
            except Exception as e:
                main_logger.critical(f"Admin API reload failed due to an unexpected error: {e}")
                audit_logger.critical(
                    "admin_api_reload_critical_failure",
                    extra={"context": {"error": str(e), "source_ip": request.remote}},
                )
                return web.Response(status=500, text=f"Internal Server Error: {e}")

        async def handle_pause(request):
            target_name = request.match_info["name"]
            if gw := self._gateways.get(target_name):
                gw.pause()
                await self._log_admin_action(
                    "api_pause", {"target": target_name, "source_ip": request.remote}
                )
                return web.Response(text=f"Target '{target_name}' paused.")
            return web.Response(status=404, text="Target not found.")

        async def handle_resume(request):
            target_name = request.match_info["name"]
            if gw := self._gateways.get(target_name):
                gw.resume()
                await self._log_admin_action(
                    "api_resume", {"target": target_name, "source_ip": request.remote}
                )
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
                ssl_context.load_cert_chain(self.settings.cert_path, self.settings.key_path)
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
DEAD_LETTER_DIR = os.environ.get("SNS_GATEWAY_DEAD_LETTER_DIR", "/var/lib/sns_gateway_dead_letters")
if not os.path.exists(DEAD_LETTER_DIR):
    os.makedirs(DEAD_LETTER_DIR, exist_ok=True)
    os.chmod(DEAD_LETTER_DIR, 0o700)


async def dead_letter_to_file(event: SNSEvent, reason: str):
    log_line = json.dumps(
        {
            "event": event.model_dump(),
            "failure_reason": reason,
            "timestamp": time.time(),
        }
    )
    filepath = os.path.join(DEAD_LETTER_DIR, f"sns_dead_letters.{time.strftime('%Y%m%d')}.log")

    encryption_key = SECRETS_MANAGER.get_secret(
        "SNS_GATEWAY_DEAD_LETTER_ENCRYPTION_KEY", required=False
    )

    if encryption_key:
        cipher = Fernet(encryption_key.encode())
        log_line = cipher.encrypt(log_line.encode("utf-8")).decode("utf-8")

    async with aiofiles.open(filepath, "a") as f:
        await f.write(log_line + "\n")
        os.chmod(filepath, 0o600)


sns_gateway_manager: Optional[SNSGatewayManager] = None


@asynccontextmanager
async def app_lifecycle(main_func: Callable):
    global sns_gateway_manager
    try:
        if PROD_MODE:
            sns_settings = SNSGatewaySettings.load_from_secure_vault()
        else:
            main_logger.warning("Running in non-production mode.")
            sns_settings = SNSGatewaySettings(
                signing_secret=os.environ.get(
                    "SNS_GATEWAY_SIGNING_SECRET", "non-prod-signing-secret"
                ),
                admin_api_key=os.environ.get("SNS_GATEWAY_ADMIN_API_KEY", "non-prod-admin-key"),
                targets=[
                    SNSTarget(
                        name="alerts",
                        topic_arn="arn:aws:sns:us-east-1:123456789012:alerts-topic",
                        region="us-east-1",
                        access_key_id="dummy-key-alerts",
                        secret_access_key="dummy-secret-alerts",
                    ),
                    SNSTarget(
                        name="audit",
                        topic_arn="arn:aws:sns:us-east-2:123456789012:audit-topic",
                        region="us-east-2",
                        access_key_id="dummy-key-audit",
                        secret_access_key="dummy-secret-audit",
                    ),
                ],
                url_allowlist=["^https://sns\\..*\\.amazonaws\\.com"],
            )

        sns_metrics = SNSMetrics()
        sns_gateway_manager = SNSGatewayManager(
            sns_settings, sns_metrics, dead_letter_hook=dead_letter_to_file
        )

        await sns_gateway_manager.startup()
        await main_func()
    except (ValidationError, RuntimeError, KeyError, AnalyzerCriticalError) as e:
        main_logger.critical(f"Critical initialization failure. Exiting. Error: {e}")
        alert_operator(f"Critical initialization failure. Exiting. Error: {e}", "CRITICAL")
        sys.exit(1)
    finally:
        if sns_gateway_manager:
            await sns_gateway_manager.shutdown()


if __name__ == "__main__":
    if PROD_MODE:
        main_logger.critical("Refusing to run __main__ block in production mode.")
        sys.exit(1)

    async def main_example():
        main_logger.info("Unrivaled SNS Gateway example started.")
        await sns_gateway_manager.publish(
            "alerts",
            "database_connection_failed",
            {"db_host": "prod-db-1"},
            severity="critical",
        )
        await sns_gateway_manager.publish(
            "audit", "user_logged_in", {"user_id": "user-123"}, severity="info"
        )
        await sns_gateway_manager.publish(
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
        health_status = await sns_gateway_manager.health_check()
        main_logger.info(f"Health Status: {json.dumps(health_status, indent=2)}")

        main_logger.info("Example run finished.")

    asyncio.run(app_lifecycle(main_example))
