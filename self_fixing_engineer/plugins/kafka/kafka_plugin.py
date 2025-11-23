"""
Kafka Plugin (production-ready)
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import math
import os
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ---- Optional core utilities (graceful fallbacks for dev/tests)
try:
    from core_audit import audit_logger
    from core_secrets import SECRETS_MANAGER
    from core_utils import alert_operator, scrub_secrets
except ImportError:

    def alert_operator(msg: str, level: str = "WARNING") -> None:
        """Dummy alert function."""
        print(f"[OPS ALERT - {level}] {msg}", file=sys.stderr)

    def scrub_secrets(obj: Any) -> Any:
        """Dummy secret scrubber."""
        return obj

    class _DummyAudit:
        """Dummy audit logger."""

        def log_event(self, *a, **k):
            print(f"[AUDIT_LOG] args={a} kwargs={k}")

    audit_logger = _DummyAudit()
    SECRETS_MANAGER = None  # type: ignore

# ---- Optional plugin registry (no-op if unavailable)
try:
    from omnicore_engine.plugin_registry import PlugInKind, plugin
except ImportError:

    def plugin(**_kwargs):
        def _decorator(cls):
            return cls

        return _decorator

    class PlugInKind:
        SINK = "sink"
        INTEGRATION = "integration"


# ---- Optional OpenTelemetry
try:
    from opentelemetry import trace as _otel_trace

    _tracer = _otel_trace.get_tracer(__name__)
    _OTEL_AVAILABLE = True
except ImportError:
    _tracer = None
    _OTEL_AVAILABLE = False

# ---- Optional Prometheus metrics
try:
    from prometheus_client import Counter, Gauge, Histogram

    _METRICS = True
except ImportError:
    _METRICS = False

    class Counter:
        def __init__(self, *a, **k):
            pass

        def labels(self, *a, **k):
            return self

        def inc(self, *a, **k):
            pass

    class Gauge:
        def __init__(self, *a, **k):
            pass

        def set(self, *a, **k):
            pass

    class Histogram:
        def __init__(self, *a, **k):
            pass

        def labels(self, *a, **k):
            return self

        def observe(self, *a, **k):
            pass


logger = logging.getLogger(__name__)
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _f = logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")
    _h.setFormatter(_f)
    logger.addHandler(_h)
logger.setLevel(logging.INFO)

PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"


# ---- Exceptions (library-safe)
class StartupDependencyMissing(RuntimeError): ...


class QueueDrainTimeout(RuntimeError): ...


class PermanentSendError(RuntimeError): ...


class MisconfigurationError(RuntimeError): ...


# ---- aiokafka (async) or dry-run
_AIOKAFKA_AVAILABLE = False
_kafka_errors = None
try:
    # Bug fix 1: Import all aiokafka-related classes in one block
    from aiokafka import AIOKafkaProducer
    from aiokafka import errors as _kafka_errors

    _AIOKAFKA_AVAILABLE = True
except ImportError:
    pass

# ---- Metrics (labeled by topic)
_kafka_sent = Counter(
    "kafka_plugin_messages_sent_total", "Messages successfully sent to Kafka", ["topic"]
)
_kafka_retried = Counter(
    "kafka_plugin_messages_retried_total",
    "Messages retried after transient failure",
    ["topic"],
)
_kafka_dropped = Counter(
    "kafka_plugin_messages_dropped_total",
    "Messages permanently dropped",
    ["topic", "reason"],
)
_kafka_dlq = Counter(
    "kafka_plugin_dlq_total", "Messages sent to dead-letter queue", ["topic", "reason"]
)
_kafka_queue_depth = Gauge("kafka_plugin_queue_depth", "Current in-memory queue depth")
_kafka_latency_seconds = Histogram(
    "kafka_plugin_latency_seconds",
    "End-to-end event latency",
    ["topic"],
    buckets=[0.01, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0],
)


# ---- Config dataclass
@dataclass
class KafkaConfig:
    bootstrap_servers: str
    topic: str
    dlq_topic: Optional[str] = os.getenv("KAFKA_DLQ_TOPIC")
    client_id: str = os.getenv("KAFKA_CLIENT_ID", "audit-plugin")
    security_protocol: str = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
    sasl_mechanism: Optional[str] = os.getenv("KAFKA_SASL_MECHANISM") or None
    sasl_username: Optional[str] = os.getenv("KAFKA_SASL_USERNAME") or None
    sasl_password: Optional[str] = os.getenv("KAFKA_SASL_PASSWORD") or None
    ssl_cafile: Optional[str] = os.getenv("KAFKA_SSL_CAFILE") or None
    ssl_certfile: Optional[str] = os.getenv("KAFKA_SSL_CERTFILE") or None
    ssl_keyfile: Optional[str] = os.getenv("KAFKA_SSL_KEYFILE") or None
    allow_plaintext: bool = os.getenv("KAFKA_ALLOW_PLAINTEXT", "false").lower() == "true"

    acks: str = os.getenv("KAFKA_ACKS", "all")
    enable_idempotence: bool = os.getenv("KAFKA_ENABLE_IDEMPOTENCE", "true").lower() == "true"
    linger_ms: int = int(os.getenv("KAFKA_LINGER_MS", "25"))
    batch_size: int = int(os.getenv("KAFKA_BATCH_BYTES", "16384"))
    max_in_flight: int = int(os.getenv("KAFKA_MAX_IN_FLIGHT", "5"))
    compression_type: Optional[str] = os.getenv("KAFKA_COMPRESSION_TYPE") or None
    request_timeout_ms: int = int(os.getenv("KAFKA_REQUEST_TIMEOUT_MS", "30000"))

    # Plugin behavior
    queue_maxsize: int = int(os.getenv("KAFKA_QUEUE_MAXSIZE", "5000"))
    queue_drop_policy: str = os.getenv("KAFKA_QUEUE_DROP_POLICY", "block")
    flush_interval_ms: int = int(os.getenv("KAFKA_FLUSH_INTERVAL_MS", "200"))
    batch_max: int = int(os.getenv("KAFKA_BATCH_MAX", "100"))
    send_concurrency: int = int(os.getenv("KAFKA_SEND_CONCURRENCY", "8"))
    dev_dry_run: bool = os.getenv("KAFKA_DEV_DRY_RUN", "false").lower() == "true"
    max_record_bytes: int = int(os.getenv("KAFKA_MAX_RECORD_BYTES", "900000"))
    key_field: Optional[str] = os.getenv("KAFKA_KEY_FIELD") or None
    allowed_topics: Optional[List[str]] = (
        [t.strip() for t in os.getenv("KAFKA_ALLOWED_TOPICS", "").split(",") if t.strip()]
        if os.getenv("KAFKA_ALLOWED_TOPICS")
        else None
    )

    # Retry policy
    max_retries: int = int(os.getenv("KAFKA_MAX_RETRIES", "6"))
    base_backoff_ms: int = int(os.getenv("KAFKA_BASE_BACKOFF_MS", "100"))
    max_backoff_ms: int = int(os.getenv("KAFKA_MAX_BACKOFF_MS", "30000"))
    max_retry_total_ms: int = int(os.getenv("KAFKA_MAX_RETRY_TOTAL_MS", "120000"))

    # HMAC key (from secrets manager first, then env)
    hmac_key: Optional[bytes] = None

    @staticmethod
    def from_env_and_secrets() -> "KafkaConfig":
        """Loads configuration from environment variables and secrets manager."""
        bs = os.getenv("KAFKA_BOOTSTRAP_SERVERS") or ""
        topic = os.getenv("KAFKA_TOPIC") or ""
        if SECRETS_MANAGER:
            bs = SECRETS_MANAGER.get("KAFKA_BOOTSTRAP_SERVERS", default=bs) or bs
            topic = SECRETS_MANAGER.get("KAFKA_TOPIC", default=topic) or topic
        if not bs or not topic:
            raise MisconfigurationError("KAFKA_BOOTSTRAP_SERVERS and KAFKA_TOPIC are required")

        cfg = KafkaConfig(bootstrap_servers=bs, topic=topic)

        # Bug fix 1: use explicit mapping instead of k.lower() for setattr
        SECRET_TO_FIELD = {
            "KAFKA_SASL_USERNAME": "sasl_username",
            "KAFKA_SASL_PASSWORD": "sasl_password",
            "KAFKA_SASL_MECHANISM": "sasl_mechanism",
            "KAFKA_SECURITY_PROTOCOL": "security_protocol",
            "KAFKA_SSL_CAFILE": "ssl_cafile",
            "KAFKA_SSL_CERTFILE": "ssl_certfile",
            "KAFKA_SSL_KEYFILE": "ssl_keyfile",
            "KAFKA_ALLOW_PLAINTEXT": "allow_plaintext",
            "KAFKA_COMPRESSION_TYPE": "compression_type",
            "KAFKA_DLQ_TOPIC": "dlq_topic",
            "KAFKA_QUEUE_DROP_POLICY": "queue_drop_policy",
        }
        if SECRETS_MANAGER:
            for env_key, field in SECRET_TO_FIELD.items():
                v = SECRETS_MANAGER.get(env_key, default=os.getenv(env_key))
                if v is not None:
                    if field == "allow_plaintext":
                        v = str(v).lower() == "true"
                    setattr(cfg, field, v)

            event_key = SECRETS_MANAGER.get("EVENT_HMAC_KEY", default=os.getenv("EVENT_HMAC_KEY"))
        else:
            event_key = os.getenv("EVENT_HMAC_KEY")

        if event_key:
            try:
                import base64

                cfg.hmac_key = (
                    base64.b64decode(event_key)
                    if any(c in event_key for c in "/+=")
                    else event_key.encode("utf-8")
                )
            except Exception:
                cfg.hmac_key = event_key.encode("utf-8")
        else:
            cfg.hmac_key = None

        cfg._validate()
        return cfg

    def _validate(self) -> None:
        """Performs strict validation of configuration."""
        servers = [s.strip() for s in self.bootstrap_servers.split(",") if s.strip()]
        if not servers:
            raise MisconfigurationError("No bootstrap servers configured")
        for s in servers:
            if "://" in s or ":" not in s:
                raise MisconfigurationError(f"Bootstrap server must be host:port: {s}")

        sec = (self.security_protocol or "PLAINTEXT").upper()
        if sec not in {"PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"}:
            raise MisconfigurationError(f"Invalid KAFKA_SECURITY_PROTOCOL: {sec}")
        if PRODUCTION_MODE and sec in {"PLAINTEXT", "SASL_PLAINTEXT"} and not self.allow_plaintext:
            raise MisconfigurationError(
                "PLAINTEXT disallowed in PRODUCTION_MODE (set KAFKA_ALLOW_PLAINTEXT=true to override)"
            )
        if self.compression_type and self.compression_type not in {
            "gzip",
            "snappy",
            "lz4",
            "zstd",
        }:
            raise MisconfigurationError(
                "KAFKA_COMPRESSION_TYPE must be one of: gzip, snappy, lz4, zstd"
            )
        if PRODUCTION_MODE and not _AIOKAFKA_AVAILABLE and not self.dev_dry_run:
            raise StartupDependencyMissing("aiokafka not installed in PRODUCTION_MODE")

        # Bug fix 3: Validate queue drop policy
        self.queue_drop_policy = (self.queue_drop_policy or "block").lower()
        if self.queue_drop_policy not in {"block", "drop_newest", "drop_oldest"}:
            raise MisconfigurationError(
                "KAFKA_QUEUE_DROP_POLICY must be one of: block, drop_newest, drop_oldest"
            )


# ---- Helpers
def _utc_now_iso() -> str:
    """Returns current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sign_event(hmac_key: Optional[bytes], payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Generates an HMAC signature for the entire payload."""
    if not hmac_key:
        return None

    # Bug fix: sign everything except the signature fields themselves
    canonical = {k: v for k, v in payload.items() if k not in ("signature", "sig_alg", "sig_scope")}
    body = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    digest = hmac.new(hmac_key, body, hashlib.sha256).hexdigest()
    return {"value": digest, "alg": "HMAC-SHA256", "scope": "*"}


def _jittered_backoff_ms(base_ms: int, attempt: int, cap_ms: int) -> int:
    """Calculates exponential backoff with jitter."""
    exp = min(cap_ms, int(base_ms * (2**attempt)))
    lo = max(base_ms, int(exp * 0.5))
    return random.randint(lo, exp)


def _serialize_event(event: Dict[str, Any]) -> bytes:
    """Serializes a dictionary to a JSON bytes object."""
    return json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---- Plugin Implementation
@plugin(name="kafka_audit_plugin", version="1.2.0", kind=PlugInKind.SINK)
class KafkaAuditPlugin:
    """
    Async Kafka audit sink with batching, retries, and DLQ support.

    The plugin enqueues events from the application and sends them in batches to Kafka
    using an asynchronous producer. It handles transient failures with retries and
    graceful shutdown.
    """

    def __init__(self, config: Optional[KafkaConfig] = None) -> None:
        self.config = config or KafkaConfig.from_env_and_secrets()
        # queue item: (topic, payload, headers, enq_ts)
        # Bug fix 2: Update queue type hint to match encoded headers
        self._queue: asyncio.Queue[
            Tuple[str, Dict[str, Any], Optional[List[Tuple[str, bytes]]], float]
        ] = asyncio.Queue(maxsize=self.config.queue_maxsize)
        self._producer: Optional["AIOKafkaProducer"] = None
        self._sender_task: Optional[asyncio.Task] = None
        self._stop_evt = asyncio.Event()
        self._started = False
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.get_event_loop()

        _kafka_queue_depth.set(0)

    async def initialize(self) -> None:
        """
        Initializes the Kafka producer. Must be awaited before start().
        """
        if self.config.dev_dry_run:
            self._producer = None
            logger.info("KafkaAuditPlugin initialized (DRY-RUN, topic=%s)", self.config.topic)
            return

        if not _AIOKAFKA_AVAILABLE:
            if PRODUCTION_MODE:
                raise StartupDependencyMissing("aiokafka not available and dev dry-run disabled")
            else:
                logger.warning("aiokafka not installed; running in dry-run mode.")
                self.config.dev_dry_run = True
                return

        kwargs: Dict[str, Any] = dict(
            bootstrap_servers=self.config.bootstrap_servers,
            client_id=self.config.client_id,
            linger_ms=self.config.linger_ms,
            acks=self.config.acks,
            enable_idempotence=self.config.enable_idempotence,
            max_in_flight_requests_per_connection=self.config.max_in_flight,
            batch_size=self.config.batch_size,
            request_timeout_ms=self.config.request_timeout_ms,
        )

        if self.config.compression_type:
            kwargs["compression_type"] = self.config.compression_type

        sec = (
            self.config.security_protocol.upper() if self.config.security_protocol else "PLAINTEXT"
        )
        kwargs["security_protocol"] = sec
        if sec in ("SASL_PLAINTEXT", "SASL_SSL"):
            if (
                self.config.sasl_mechanism
                and self.config.sasl_username
                and self.config.sasl_password
            ):
                kwargs.update(
                    sasl_mechanism=self.config.sasl_mechanism,
                    sasl_plain_username=self.config.sasl_username,
                    sasl_plain_password=self.config.sasl_password,
                )
            elif PRODUCTION_MODE:
                raise MisconfigurationError(
                    "SASL_* required for SASL_* protocol in PRODUCTION_MODE"
                )

        if sec in ("SSL", "SASL_SSL"):
            if self.config.ssl_cafile:
                kwargs["ssl_cafile"] = self.config.ssl_cafile
            if self.config.ssl_certfile:
                kwargs["ssl_certfile"] = self.config.ssl_certfile
            if self.config.ssl_keyfile:
                kwargs["ssl_keyfile"] = self.config.ssl_keyfile

        self._producer = AIOKafkaProducer(**kwargs)
        logger.info(
            "KafkaAuditPlugin initialized (topic=%s, compression=%s)",
            self.config.topic,
            self.config.compression_type,
        )

    async def start(self) -> None:
        """Starts the producer and the sender loop."""
        if self._started:
            logger.info("KafkaAuditPlugin already started; ignoring duplicate start()")
            return
        if self._producer is not None:
            await self._producer.start()
        self._stop_evt.clear()
        self._sender_task = asyncio.create_task(self._sender_loop(), name="kafka_sender_loop")
        self._started = True
        logger.info("KafkaAuditPlugin started.")

    async def stop(self, drain_timeout: float = 10.0) -> None:
        """
        Graceful shutdown: flushes the queue and closes the producer.
        """
        if not self._started:
            logger.info("KafkaAuditPlugin not started; ignoring stop()")
            return

        logger.info("Initiating graceful shutdown...")
        self._stop_evt.set()

        try:
            await asyncio.wait_for(self._sender_task, timeout=drain_timeout)
        except asyncio.TimeoutError as e:
            remaining = self._queue.qsize()
            logger.error("Timed out draining Kafka queue (remaining=%s)", remaining)
            alert_operator(
                f"Timed out draining Kafka queue on shutdown (remaining={remaining})",
                level="CRITICAL",
            )
            raise QueueDrainTimeout(
                f"Timed out draining Kafka queue (remaining={remaining})"
            ) from e
        finally:
            self._sender_task = None

        if self._producer is not None:
            try:
                await self._producer.stop()
            finally:
                self._producer = None

        self._started = False
        logger.info("KafkaAuditPlugin stopped.")

    async def enqueue_event(
        self,
        event_type: str,
        details: Dict[str, Any],
        *,
        subject: Optional[str] = None,
        severity: str = "INFO",
        correlation_id: Optional[str] = None,
        extra_headers: Optional[Dict[str, Any]] = None,
        topic: Optional[str] = None,
    ) -> None:
        """
        Enqueues an event for asynchronous sending.
        """
        if not self._started:
            raise RuntimeError("KafkaAuditPlugin is not started; cannot enqueue.")

        dest_topic = (topic or self.config.topic).strip()
        if self.config.allowed_topics is not None and dest_topic not in self.config.allowed_topics:
            raise MisconfigurationError(f"Topic '{dest_topic}' not in KAFKA_ALLOWED_TOPICS")

        safe_details = scrub_secrets(details) if details is not None else {}
        uid = uuid.uuid4().hex

        payload: Dict[str, Any] = {
            "id": uid,
            "type": event_type,
            "subject": subject,
            "severity": severity,
            "time": _utc_now_iso(),
            "details": safe_details,
            "correlation_id": correlation_id,
        }
        sig = _sign_event(self.config.hmac_key, payload)
        if sig:
            payload["signature"] = sig["value"]
            payload["sig_alg"] = sig["alg"]
            payload["sig_scope"] = sig["scope"]

        # Bug fix: Sanitize headers too
        headers_in = extra_headers or {}
        try:
            safe_headers = scrub_secrets(headers_in)
        except Exception:
            safe_headers = headers_in
        headers = (
            [(str(k), str(v).encode("utf-8")) for k, v in safe_headers.items()]
            if safe_headers
            else None
        )

        enq_ts = self._loop.time()

        # Bug fix 4: Implement queue drop policy
        try:
            if self.config.queue_drop_policy == "block":
                await self._queue.put((dest_topic, payload, headers, enq_ts))
            elif self.config.queue_drop_policy == "drop_newest":
                if self._queue.full():
                    _kafka_dropped.labels(dest_topic, "queue_full_newest").inc()
                    return
                self._queue.put_nowait((dest_topic, payload, headers, enq_ts))
            elif self.config.queue_drop_policy == "drop_oldest":
                if self._queue.full():
                    try:
                        _ = self._queue.get_nowait()
                        self._queue.task_done()
                        _kafka_dropped.labels(dest_topic, "queue_full_oldest").inc()
                    except asyncio.QueueEmpty:
                        pass
                self._queue.put_nowait((dest_topic, payload, headers, enq_ts))
            else:
                await self._queue.put((dest_topic, payload, headers, enq_ts))
        finally:
            _kafka_queue_depth.set(self._queue.qsize())

        audit_logger.log_event(
            "kafka_enqueue",
            event_type=event_type,
            severity=severity,
            correlation_id=correlation_id,
            topic=dest_topic,
        )

    async def _sender_loop(self) -> None:
        """
        Main loop for batching and sending messages.
        """
        batch: List[Tuple[str, Dict[str, Any], Optional[List[Tuple[str, bytes]]], float]] = []
        next_flush = self._loop.time() + (self.config.flush_interval_ms / 1000.0)

        while True:
            timeout = max(0.0, next_flush - self._loop.time())
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                batch.append(item)
                _kafka_queue_depth.set(self._queue.qsize())
            except asyncio.TimeoutError:
                pass

            if self._stop_evt.is_set() and self._queue.empty():
                if batch:
                    await self._flush_batch(batch)
                break

            if len(batch) >= self.config.batch_max or self._loop.time() >= next_flush:
                if batch:
                    await self._flush_batch(batch)
                batch = []
                next_flush = self._loop.time() + (self.config.flush_interval_ms / 1000.0)

    async def _flush_batch(
        self,
        batch: List[Tuple[str, Dict[str, Any], Optional[List[Tuple[str, bytes]]], float]],
    ) -> None:
        """Sends a batch of messages concurrently."""
        if not batch:
            return

        chunk_size = max(1, math.ceil(len(batch) / max(1, self.config.send_concurrency)))
        chunks = [batch[i : i + chunk_size] for i in range(0, len(batch), chunk_size)]

        cm = (
            _tracer.start_as_current_span("kafka_flush_batch")
            if _OTEL_AVAILABLE and _tracer
            else contextlib.nullcontext()
        )

        with cm as span:
            if span:
                span.set_attribute("batch.size", len(batch))
                span.set_attribute("chunk.count", len(chunks))

            send_tasks = [self._send_chunk(ch, span) for ch in chunks]
            results = await asyncio.gather(*send_tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    logger.error("Chunk send error (continuing): %s", res)

    async def _send_chunk(
        self,
        chunk: List[Tuple[str, Dict[str, Any], Optional[List[Tuple[str, bytes]]], float]],
        span=None,
    ) -> None:
        """Sends a chunk of messages with individual retry logic."""
        if not chunk:
            return

        if (not _AIOKAFKA_AVAILABLE or self._producer is None) and self.config.dev_dry_run:
            per_topic_count: Dict[str, int] = {}
            for topic, payload, _, enq_ts in chunk:
                _kafka_latency_seconds.labels(topic).observe(self._loop.time() - enq_ts)
                per_topic_count[topic] = per_topic_count.get(topic, 0) + 1
                logger.debug("[DRY-RUN] Kafka send: %s", payload.get("type"))
            for t, n in per_topic_count.items():
                _kafka_sent.labels(t).inc(n)
            return

        # Bug fix 3: Use explicit check instead of assert
        if self._producer is None:
            raise StartupDependencyMissing("Producer not started")

        for topic, payload, headers, enq_ts in chunk:
            body = _serialize_event(payload)
            if len(body) > self.config.max_record_bytes:
                _kafka_dropped.labels(topic, "payload_too_large").inc()
                audit_logger.log_event(
                    "kafka_send_drop",
                    error="record_too_large",
                    size=len(body),
                    topic=topic,
                )
                alert_operator(
                    f"Kafka record too large; dropped (topic={topic}, size={len(body)})",
                    level="ERROR",
                )
                continue

            # Bug fix 2: Headers are already prepared in enqueue_event
            hdrs = headers

            key_bytes: Optional[bytes] = None
            if self.config.key_field:
                k = payload.get(self.config.key_field)
                if k is None and isinstance(payload.get("details"), dict):
                    k = payload["details"].get(self.config.key_field)
                key_bytes = (str(k) if k is not None else payload.get("id", "")).encode("utf-8")
            else:
                key_bytes = payload.get("id", "").encode("utf-8")

            try:
                await self._send_with_retry(topic, key_bytes, body, hdrs, enq_ts, span)
                _kafka_sent.labels(topic).inc()
            except PermanentSendError as e:
                _kafka_dropped.labels(topic, "permanent_error").inc()
                if self.config.dlq_topic:
                    await self._send_to_dlq(self.config.dlq_topic, payload, str(e), headers)
                else:
                    logger.error("Permanent send error; dropped message (topic=%s): %s", topic, e)

    async def _send_with_retry(
        self,
        topic: str,
        key: Optional[bytes],
        value: bytes,
        headers: Optional[List[Tuple[str, bytes]]],
        enq_ts: float,
        span=None,
    ) -> None:
        """Sends a single message with exponential backoff and jitter."""
        attempt = 0
        start_ms = int(self._loop.time() * 1000)
        while True:
            try:
                if self._producer is None:
                    raise StartupDependencyMissing("Producer not started")

                md = await self._producer.send_and_wait(
                    topic, value=value, key=key, headers=headers
                )
                _kafka_latency_seconds.labels(topic).observe(self._loop.time() - enq_ts)
                audit_logger.log_event(
                    "kafka_send_ok",
                    topic=topic,
                    partition=getattr(md, "partition", None),
                    offset=getattr(md, "offset", None),
                )
                if span:
                    span.set_attribute("kafka.topic", topic)
                    span.set_attribute("kafka.partition", getattr(md, "partition", -1))
                    span.set_attribute("kafka.offset", getattr(md, "offset", -1))
                return
            except Exception as e:
                retryable = self._is_retryable(e)
                now_ms = int(self._loop.time() * 1000)
                total_elapsed = now_ms - start_ms

                if span:
                    span.set_attribute("kafka.retry.attempt", attempt)
                    span.set_attribute("kafka.retryable", retryable)

                if (
                    (not retryable)
                    or (attempt >= self.config.max_retries)
                    or (total_elapsed >= self.config.max_retry_total_ms)
                ):
                    audit_logger.log_event(
                        "kafka_send_drop",
                        error=str(e),
                        topic=topic,
                        attempt=attempt,
                        retryable=retryable,
                    )
                    if PRODUCTION_MODE:
                        alert_operator(
                            f"Kafka send dropped (topic={topic}, attempt={attempt}, retryable={retryable}): {e}",
                            level="CRITICAL",
                        )
                    raise PermanentSendError(str(e)) from e

                attempt += 1
                _kafka_retried.labels(topic).inc()
                backoff = (
                    _jittered_backoff_ms(
                        self.config.base_backoff_ms, attempt, self.config.max_backoff_ms
                    )
                    / 1000.0
                )
                audit_logger.log_event(
                    "kafka_send_retry", attempt=attempt, backoff=backoff, topic=topic
                )
                await asyncio.sleep(backoff)

    async def _send_to_dlq(
        self,
        dlq_topic: str,
        payload: Dict[str, Any],
        reason: str,
        headers: Optional[List[Tuple[str, bytes]]],
    ) -> None:
        """Sends a failed message to the Dead-Letter Queue."""
        if self.config.dev_dry_run:
            _kafka_dlq.labels(dlq_topic, "permanent_error").inc()
            logger.warning("[DRY-RUN] Would send to DLQ: %s", reason)
            return

        if self._producer is None:
            raise StartupDependencyMissing("Producer not started; cannot send to DLQ")

        try:
            dlq_payload = {
                "original_event": payload,
                "dlq_reason": reason,
                "dlq_time": _utc_now_iso(),
            }
            body = _serialize_event(dlq_payload)
            key_bytes = payload.get("id", "").encode("utf-8")

            await self._producer.send_and_wait(
                dlq_topic, value=body, key=key_bytes, headers=headers
            )
            _kafka_dlq.labels(dlq_topic, "permanent_error").inc()
            logger.warning(
                "Message failed to send and was moved to DLQ (topic=%s, reason=%s)",
                dlq_topic,
                reason,
            )
        except Exception as e:
            logger.critical(
                "Failed to send message to DLQ. Message is permanently lost. Error: %s",
                e,
            )
            alert_operator(
                f"Failed to send to DLQ. Event lost! Reason: {reason}. Error: {e}",
                level="CRITICAL",
            )
            _kafka_dropped.labels(dlq_topic, "dlq_failure").inc()

    async def health(self) -> Dict[str, Any]:
        """
        Health check endpoint for readiness probes.
        """
        if self.config.dev_dry_run:
            return {"ok": True, "mode": "dry-run"}
        if self._producer is None:
            return {"ok": False, "error": "producer_not_started"}

        try:
            # A safer metadata fetch
            if hasattr(self._producer, "client") and hasattr(
                self._producer.client, "fetch_all_metadata"
            ):
                await self._producer.client.fetch_all_metadata()
            else:
                # Fallback for older aiokafka versions
                await self._producer.partitions_for_topic(self.config.topic)
            return {"ok": True, "mode": "live"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Determines if a Kafka exception is transient and should be retried."""
        if _kafka_errors:
            RetriableError = getattr(_kafka_errors, "RetriableError", None)
            if RetriableError and isinstance(exc, RetriableError):
                return True
        text = repr(exc).lower()
        transient_signals = (
            "connection",
            "timeout",
            "temporarily",
            "retriable",
            "not leader",
            "leader epoch",
            "transport",
            "disconnected",
        )
        return any(s in text for s in transient_signals)


# ---- Convenience factory used by some loaders
def build_plugin_from_env() -> KafkaAuditPlugin:
    """Factory function to build plugin from environment variables."""
    return KafkaAuditPlugin(KafkaConfig.from_env_and_secrets())


# ---- If this module is run directly, do a quick dry-run smoke
if __name__ == "__main__":

    async def _smoke():
        print("Starting Kafka Audit Plugin smoke test...")
        hook = KafkaAuditPlugin(KafkaConfig.from_env_and_secrets())
        await hook.initialize()
        await hook.start()
        try:
            await hook.enqueue_event(
                "diagnostic.smoke",
                {"hello": "world"},
                subject="self-test",
                severity="INFO",
            )
            await asyncio.sleep(0.5)
        finally:
            await hook.stop()
        print("Smoke test finished.")

    try:
        asyncio.run(_smoke())
    except Exception as e:
        print(f"Smoke test failed: {e}")
        sys.exit(1)
