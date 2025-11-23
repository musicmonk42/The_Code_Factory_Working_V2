# omnicore_engine/message_bus/kafka_sink_adapter.py
"""
Kafka sink adapter for the OmniCore message-bus.

This is a production adapter that wraps SFE's KafkaAuditPlugin so the bus
can treat Kafka as a normal runtime sink:

    async with KafkaBusSink.from_env() as sink:
        await sink.emit("user.login", {"user": "alice"}, subject="alice")

Design goals:
- Zero test stubs. Uses the real SFE plugin.
- Plays nicely with OmniCore resilience/metrics/context if available.
- No hard dependency on optional extras (prometheus, guardian, etc.).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Iterable, Optional, Tuple

# --------------------------------------------------------------------------- #
#  Import the *real* SFE Kafka plugin – fail clearly if missing
# --------------------------------------------------------------------------- #
try:
    from self_fixing_engineer.plugins.kafka.kafka_plugin import (
        KafkaAuditPlugin,
        KafkaConfig,
        MisconfigurationError,
        PermanentSendError,
        QueueDrainTimeout,
        StartupDependencyMissing,
        build_plugin_from_env,
    )
except Exception as exc:  # pragma: no cover
    raise ImportError(
        "KafkaBusSink could not import the SFE Kafka plugin. "
        "Install/enable `self_fixing_engineer` and ensure "
        "`self_fixing_engineer.plugins.kafka.kafka_plugin` is importable."
    ) from exc

# --------------------------------------------------------------------------- #
#  Optional OmniCore integrations (degrade gracefully if absent)
# --------------------------------------------------------------------------- #
# Metrics (Prometheus) – optional
try:  # pragma: no cover - optional runtime path
    from prometheus_client import Counter  # type: ignore
except Exception:  # pragma: no cover
    Counter = None  # type: ignore

# Resilience (CircuitBreaker/Guardian) – optional
try:  # pragma: no cover - optional runtime path
    from .resilience import CircuitBreaker, MessageBusGuardian
except Exception:  # pragma: no cover
    CircuitBreaker = None  # type: ignore
    MessageBusGuardian = None  # type: ignore

# Context propagation – optional
try:  # pragma: no cover - optional runtime path
    from .context import ExecutionContext
except Exception:  # pragma: no cover
    ExecutionContext = None  # type: ignore

# --------------------------------------------------------------------------- #
#  Logger
# --------------------------------------------------------------------------- #
logger = logging.getLogger(__name__)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)

# --------------------------------------------------------------------------- #
#  Prometheus counters (created only if library available)
# --------------------------------------------------------------------------- #
if Counter is not None:  # pragma: no cover
    METRIC_EVENTS_TOTAL = Counter(
        "omnicore_kafka_events_total",
        "Total events attempted to emit to Kafka",
        ["result", "event_type", "severity"],
    )
else:  # pragma: no cover
    METRIC_EVENTS_TOTAL = None  # type: ignore


def _metrics_inc(result: str, event_type: str, severity: str) -> None:
    if METRIC_EVENTS_TOTAL is not None:  # pragma: no cover
        try:
            METRIC_EVENTS_TOTAL.labels(
                result=result, event_type=event_type, severity=severity
            ).inc()
        except Exception:
            # Never let metrics break the sink path.
            pass


# --------------------------------------------------------------------------- #
#  Utility: best-effort JSON safety (avoid surprising serialization errors)
# --------------------------------------------------------------------------- #
def _ensure_jsonable(obj: Any) -> Any:
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        # Fallback: shallow convert non-serializables to repr
        if isinstance(obj, dict):
            return {k: _ensure_jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [_ensure_jsonable(v) for v in obj]
        return repr(obj)


# --------------------------------------------------------------------------- #
#  KafkaBusSink
# --------------------------------------------------------------------------- #
class KafkaBusSink:
    """
    Thin, bus-friendly wrapper around `KafkaAuditPlugin`.

    Lifecycle:
        await start()  -> initialize + start producer
        await emit()   -> enqueue audit event (batched/retried by plugin)
        await stop()   -> graceful shutdown (drains queue)
        await health() -> plugin health payload

    Factories:
        KafkaBusSink.from_env()
        KafkaBusSink.from_config(cfg)

    Extras:
        - emit_safe(): returns bool rather than raising
        - emit_many(): bounded-concurrency batch
        - ready(): fast readiness probe
        - flush(): ensure in-flight queue is flushed
        - async context manager support
    """

    def __init__(
        self,
        plugin: Optional[KafkaAuditPlugin] = None,
        *,
        use_env: bool = True,
        breaker_fail_threshold: int = 10,
        breaker_reset_timeout_s: float = 30.0,
        max_concurrency: int = 64,
    ) -> None:
        if plugin is None and use_env:
            plugin = build_plugin_from_env()
        if plugin is None:
            raise MisconfigurationError(
                "KafkaBusSink requires either a KafkaAuditPlugin or use_env=True."
            )

        self._plugin: KafkaAuditPlugin = plugin
        self._started: bool = False
        self._lock = asyncio.Lock()
        self._max_concurrency = max_concurrency

        # Optional circuit breaker around the enqueue path.
        self._breaker = None
        if CircuitBreaker is not None:  # pragma: no cover
            self._breaker = CircuitBreaker(
                fail_threshold=breaker_fail_threshold,
                reset_timeout_s=breaker_reset_timeout_s,
                name="kafka_sink_emit",
            )

    # ----- class factories ------------------------------------------------- #
    @classmethod
    def from_env(cls) -> "KafkaBusSink":
        return cls(use_env=True)

    @classmethod
    def from_config(cls, cfg: KafkaConfig) -> "KafkaBusSink":
        return cls(KafkaAuditPlugin(cfg), use_env=False)

    # ----- context manager ------------------------------------------------- #
    async def __aenter__(self) -> "KafkaBusSink":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    # ----- lifecycle ------------------------------------------------------- #
    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            await self._plugin.initialize()
            await self._plugin.start()
            self._started = True
            logger.info(
                "KafkaBusSink started – topic=%s, bootstrap=%s",
                getattr(self._plugin.config, "topic", "<unknown>"),
                getattr(self._plugin.config, "bootstrap_servers", "<unknown>"),
            )

    async def stop(self, drain_timeout: float = 10.0) -> None:
        async with self._lock:
            if not self._started:
                return
            await self._plugin.stop(drain_timeout=drain_timeout)
            self._started = False
            logger.info("KafkaBusSink stopped.")

    async def health(self) -> Dict[str, Any]:
        return await self._plugin.health()

    async def ready(self) -> bool:
        """
        Lightweight readiness check — true when started and plugin reports healthy.
        Never raises; logs and returns False on exception.
        """
        if not self._started:
            return False
        try:
            status = await self._plugin.health()
            return bool(status.get("ok", True))
        except Exception as e:
            logger.warning("KafkaBusSink.ready() health check failed: %s", e)
            return False

    async def flush(self, drain_timeout: float = 10.0) -> None:
        """
        Best-effort flush by delegating to plugin.stop with restart, without
        permanently stopping the sink. If the plugin cannot drain in time,
        it will raise QueueDrainTimeout (surfaced here).
        """
        if not self._started:
            return
        await self._plugin.flush(drain_timeout=drain_timeout)

    # ----- emit API -------------------------------------------------------- #
    async def emit(
        self,
        event_type: str,
        details: Dict[str, Any],
        *,
        subject: Optional[str] = None,
        severity: str = "INFO",
        correlation_id: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None,
        topic: Optional[str] = None,
        partition_key: Optional[str] = None,
        context: Optional["ExecutionContext"] = None,  # type: ignore[name-defined]
    ) -> None:
        """
        Emit a single audit event into Kafka.

        Raises on:
            - RuntimeError if not started
            - StartupDependencyMissing if Kafka infra missing
            - PermanentSendError on non-retryable broker errors
            - QueueDrainTimeout if internal queue cannot drain on shutdown/flush
        """
        if not self._started:
            raise RuntimeError("KafkaBusSink must be started before calling emit()")

        # Context propagation (optional)
        if context is not None:
            correlation_id = correlation_id or getattr(context, "correlation_id", None)
            subject = subject or getattr(context, "subject", None)

        # Defensive JSON safety: never explode the hot path on bad payloads.
        safe_details = _ensure_jsonable(details)

        async def _do_enqueue() -> None:
            await self._plugin.enqueue_event(
                event_type=event_type,
                details=safe_details,
                subject=subject,
                severity=severity,
                correlation_id=correlation_id,
                extra_headers=headers,
                topic=topic,
                partition_key=partition_key,
            )

        try:
            if self._breaker is not None:  # pragma: no cover
                await self._breaker.call(_do_enqueue)
            else:
                await _do_enqueue()
            _metrics_inc("success", event_type, severity)
        except (PermanentSendError, StartupDependencyMissing) as e:
            _metrics_inc("perm_fail", event_type, severity)
            logger.error(
                "Kafka emit permanent failure: event=%s severity=%s err=%s",
                event_type,
                severity,
                e,
            )
            raise
        except QueueDrainTimeout as e:
            _metrics_inc("drain_timeout", event_type, severity)
            logger.error(
                "Kafka emit drain-timeout: event=%s severity=%s err=%s",
                event_type,
                severity,
                e,
            )
            raise
        except Exception as e:
            _metrics_inc("transient_fail", event_type, severity)
            logger.warning(
                "Kafka emit transient failure (will rely on plugin retries): "
                "event=%s severity=%s err=%s",
                event_type,
                severity,
                e,
            )
            # Let the caller decide if a transient failure is acceptable.
            # Re-raise so upstream retry policies (if any) can kick in.
            raise

    async def emit_safe(
        self,
        event_type: str,
        details: Dict[str, Any],
        **kwargs: Any,
    ) -> bool:
        """
        Safe variant of emit(): never raises; returns True/False.
        Useful in fire-and-forget audit paths.
        """
        try:
            await self.emit(event_type, details, **kwargs)
            return True
        except Exception as e:
            logger.debug("emit_safe() suppressed error for event %s: %s", event_type, e)
            return False

    async def emit_many(
        self,
        events: Iterable[Tuple[str, Dict[str, Any]]],
        *,
        common_kwargs: Optional[Dict[str, Any]] = None,
        concurrency: Optional[int] = None,
    ) -> None:
        """
        Emit a batch of (event_type, details) with bounded concurrency.

        Raises on the first error (fail-fast) to match emit() semantics.
        """
        if not self._started:
            raise RuntimeError("KafkaBusSink must be started before calling emit_many()")
        sem = asyncio.Semaphore(concurrency or self._max_concurrency)
        common_kwargs = common_kwargs or {}

        async def _one(ev_type: str, payload: Dict[str, Any]) -> None:
            async with sem:
                await self.emit(ev_type, payload, **common_kwargs)

        tasks = [asyncio.create_task(_one(et, d)) for et, d in events]
        # Fail fast, bubble the first exception if any
        for t in asyncio.as_completed(tasks):
            await t  # exception propagates


__all__ = ["KafkaBusSink"]
