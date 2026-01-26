# omnicore_engine/message_bus/guardian.py
"""
The MessageBusGuardian is a background service responsible for monitoring the
health and operational integrity of the ShardedMessageBus and its external
dependencies (RedisBridge, KafkaBridge, Executors).

It uses a failure counter and a configurable threshold to determine if a
critical failure has occurred, triggering self-healing actions if necessary.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

# --------------------------------------------------------------------------- #
#  Logging – use structlog (the same logger the bus uses) and add .bind()
# --------------------------------------------------------------------------- #
import structlog

logger = structlog.get_logger(__name__)
# The original code did:
#   logger = logger.bind(module="MessageBusGuardian")
# structlog already gives us a bound logger, so we just bind once here:
logger = logger.bind(module="MessageBusGuardian")

# --------------------------------------------------------------------------- #
#  Optional Prometheus metrics (same pattern as metrics.py)
# --------------------------------------------------------------------------- #
try:
    from prometheus_client import Counter, Gauge, Histogram  # type: ignore

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROMETHEUS_AVAILABLE = False
    Counter = Gauge = Histogram = None

# --------------------------------------------------------------------------- #
#  Local / Relative imports
# --------------------------------------------------------------------------- #
from .metrics import MESSAGE_BUS_CRITICAL_FAILURES_TOTAL, MESSAGE_BUS_HEALTH_STATUS

if TYPE_CHECKING:
    from .sharded_message_bus import ShardedMessageBus

# --------------------------------------------------------------------------- #
#  External config (ArbiterConfig) – mock fallback if missing
# --------------------------------------------------------------------------- #
try:
    from self_fixing_engineer.arbiter.config import ArbiterConfig

    settings = ArbiterConfig()
except ImportError:
    try:
        # Fall back to aliased path for backward compatibility
        from arbiter.config import ArbiterConfig

        settings = ArbiterConfig()
    except ImportError:  # pragma: no cover

        class MockConfig:
            def __getattr__(self, name):
                defaults = {
                    "MESSAGE_BUS_GUARDIAN_CHECK_INTERVAL": 30,
                    "MESSAGE_BUS_GUARDIAN_FAILURE_THRESHOLD": 5,
                    "MESSAGE_BUS_GUARDIAN_HEALING_TIMEOUT": 300,
                    "MESSAGE_BUS_GUARDIAN_ALERT_RETRIES": 3,
                    "MESSAGE_BUS_GUARDIAN_ALERT_BASE_DELAY": 0.5,
                    "MESSAGE_BUS_GUARDIAN_ALERT_MAX_DELAY": 10.0,
                    "MESSAGE_BUS_GUARDIAN_ALERT_JITTER": 0.3,
                    "ENABLE_CRITICAL_FAILURES": True,
                    "ALERT_WEBHOOK_URL": None,
                    "ENABLE_METRICS": True,
                }
                return defaults.get(name, None)

    settings = MockConfig()


# --------------------------------------------------------------------------- #
#  Prometheus metrics (optional but always defined)
# --------------------------------------------------------------------------- #
if _PROMETHEUS_AVAILABLE and Counter is not None:  # pragma: no cover
    try:
        METRIC_GUARDIAN_CHECKS_TOTAL = Counter(
            "omnicore_guardian_checks_total",
            "Total health checks performed by the guardian",
            ["result"],
        )
    except (ValueError, Exception):
        # Metric already registered, retrieve it
        from prometheus_client import REGISTRY
        METRIC_GUARDIAN_CHECKS_TOTAL = REGISTRY._names_to_collectors.get("omnicore_guardian_checks_total")
    
    try:
        METRIC_GUARDIAN_ALERTS_TOTAL = Counter(
            "omnicore_guardian_alerts_total",
            "Total alerts sent by the guardian",
            ["result"],
        )
    except (ValueError, Exception):
        from prometheus_client import REGISTRY
        METRIC_GUARDIAN_ALERTS_TOTAL = REGISTRY._names_to_collectors.get("omnicore_guardian_alerts_total")
    
    try:
        METRIC_GUARDIAN_HEALING_ATTEMPTS = Counter(
            "omnicore_guardian_healing_attempts_total",
            "Total self-healing attempts",
            ["result"],
        )
    except (ValueError, Exception):
        from prometheus_client import REGISTRY
        METRIC_GUARDIAN_HEALING_ATTEMPTS = REGISTRY._names_to_collectors.get("omnicore_guardian_healing_attempts_total")
    
    try:
        METRIC_GUARDIAN_CHECK_DURATION = Histogram(
            "omnicore_guardian_check_duration_seconds",
            "Duration of health checks",
        )
    except (ValueError, Exception):
        from prometheus_client import REGISTRY
        METRIC_GUARDIAN_CHECK_DURATION = REGISTRY._names_to_collectors.get("omnicore_guardian_check_duration_seconds")
    
    try:
        METRIC_GUARDIAN_COMPONENT_STATUS = Gauge(
            "omnicore_guardian_component_status",
            "Status of registered components (1=healthy, 0=unhealthy)",
            ["component"],
        )
    except (ValueError, Exception):
        from prometheus_client import REGISTRY
        METRIC_GUARDIAN_COMPONENT_STATUS = REGISTRY._names_to_collectors.get("omnicore_guardian_component_status")
else:  # pragma: no cover
    METRIC_GUARDIAN_CHECKS_TOTAL = None
    METRIC_GUARDIAN_ALERTS_TOTAL = None
    METRIC_GUARDIAN_HEALING_ATTEMPTS = None
    METRIC_GUARDIAN_CHECK_DURATION = None
    METRIC_GUARDIAN_COMPONENT_STATUS = None


def _inc_check(result: str) -> None:
    if METRIC_GUARDIAN_CHECKS_TOTAL:
        try:
            METRIC_GUARDIAN_CHECKS_TOTAL.labels(result=result).inc()
        except Exception:
            pass


def _inc_alert(result: str) -> None:
    if METRIC_GUARDIAN_ALERTS_TOTAL:
        try:
            METRIC_GUARDIAN_ALERTS_TOTAL.labels(result=result).inc()
        except Exception:
            pass


def _inc_healing(result: str) -> None:
    if METRIC_GUARDIAN_HEALING_ATTEMPTS:
        try:
            METRIC_GUARDIAN_HEALING_ATTEMPTS.labels(result=result).inc()
        except Exception:
            pass


def _observe_check(duration: float) -> None:
    if METRIC_GUARDIAN_CHECK_DURATION:
        try:
            METRIC_GUARDIAN_CHECK_DURATION.observe(duration)
        except Exception:
            pass


def _set_component(component: str, healthy: bool) -> None:
    if METRIC_GUARDIAN_COMPONENT_STATUS:
        try:
            METRIC_GUARDIAN_COMPONENT_STATUS.labels(component=component).set(
                1 if healthy else 0
            )
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Core Guardian implementation
# --------------------------------------------------------------------------- #
class CriticalFailure(Exception):
    """Raised when the Guardian detects a non-recoverable systemic failure."""

    pass


class MessageBusGuardian:
    """
    Monitors the ShardedMessageBus health and handles critical failures.

    Upgrades from the original file:
    * Uses **structlog** (`.bind()` works, no AttributeError).
    * Optional **Prometheus** metrics for checks, alerts, healing.
    * **Retry-aware** webhook alerts (exponential back-off + jitter).
    * **Health endpoint** (`await guardian.health()`).
    * **DLQ publishing** for critical-failure reports (if bus has DLQ).
    * **Graceful shutdown** with configurable drain timeout.
    * More configuration knobs (alert retries, jitter, etc.).
    """

    def __init__(self, message_bus: "ShardedMessageBus", check_interval: int = 30):
        self.message_bus = message_bus
        self.check_interval = check_interval
        self.running = False
        self._guardian_task: Optional[asyncio.Task] = None
        self._component_statuses: Dict[str, bool] = {}
        self._critical_failure_counter: int = 0
        self._stop_event = asyncio.Event()

        # ------------------------------------------------------------------- #
        #  Configurable thresholds / behaviour
        # ------------------------------------------------------------------- #
        self.failure_threshold = getattr(
            settings, "MESSAGE_BUS_GUARDIAN_FAILURE_THRESHOLD", 5
        )
        self.healing_timeout = getattr(
            settings, "MESSAGE_BUS_GUARDIAN_HEALING_TIMEOUT", 300
        )
        self.alert_retries = getattr(settings, "MESSAGE_BUS_GUARDIAN_ALERT_RETRIES", 3)
        self.alert_base_delay = getattr(
            settings, "MESSAGE_BUS_GUARDIAN_ALERT_BASE_DELAY", 0.5
        )
        self.alert_max_delay = getattr(
            settings, "MESSAGE_BUS_GUARDIAN_ALERT_MAX_DELAY", 10.0
        )
        self.alert_jitter = getattr(settings, "MESSAGE_BUS_GUARDIAN_ALERT_JITTER", 0.3)
        self.enable_critical_failures = getattr(
            settings, "ENABLE_CRITICAL_FAILURES", True
        )
        self.enable_metrics = getattr(settings, "ENABLE_METRICS", True)

        logger.info(
            "MessageBusGuardian initialized.",
            interval=self.check_interval,
            threshold=self.failure_threshold,
            alert_retries=self.alert_retries,
        )

    # ------------------------------------------------------------------- #
    #  Component registration
    # ------------------------------------------------------------------- #
    def register_component(self, name: str) -> None:
        """Allows external components to register for health tracking."""
        self._component_statuses[name] = True
        if self.enable_metrics:
            _set_component(name, True)
        logger.info("Registered health component.", component=name)

    def signal_component_status(self, name: str, is_healthy: bool) -> None:
        """Updates the health status of a registered component."""
        self._component_statuses[name] = is_healthy
        if self.enable_metrics:
            _set_component(name, is_healthy)
        logger.debug("Updated component status.", component=name, healthy=is_healthy)

    # ------------------------------------------------------------------- #
    #  Lifecycle
    # ------------------------------------------------------------------- #
    def start(self) -> None:
        """Starts the periodic health-check loop."""
        if self.running:
            return
        self.running = True
        self._guardian_task = asyncio.create_task(self._guardian_loop())
        logger.info("MessageBusGuardian started.")

    async def shutdown(self, drain_timeout: float = 5.0) -> None:
        """Gracefully stops the guardian."""
        if not self.running:
            return
        self.running = False
        self._stop_event.set()
        if self._guardian_task and not self._guardian_task.done():
            self._guardian_task.cancel()
            try:
                await asyncio.wait_for(self._guardian_task, timeout=drain_timeout)
            except asyncio.TimeoutError:
                logger.warning("Guardian task did not finish within drain timeout.")
            except asyncio.CancelledError:
                pass
        logger.info("MessageBusGuardian shutdown complete.")

    # ------------------------------------------------------------------- #
    #  Main loop
    # ------------------------------------------------------------------- #
    async def _guardian_loop(self) -> None:
        """Periodic health-check loop."""
        while self.running and not self._stop_event.is_set():
            try:
                start = time.time()
                report = await self._perform_health_check()
                duration = time.time() - start

                if self.enable_metrics:
                    _observe_check(duration)

                if report["overall_healthy"]:
                    self._critical_failure_counter = 0
                    MESSAGE_BUS_HEALTH_STATUS.set(1)
                    if self.enable_metrics:
                        _inc_check("healthy")
                    logger.info("Health check passed.", report=report)
                else:
                    self._critical_failure_counter += 1
                    MESSAGE_BUS_HEALTH_STATUS.set(0)
                    if self.enable_metrics:
                        _inc_check("unhealthy")
                    logger.warning(
                        "Health check failed.",
                        failure_count=self._critical_failure_counter,
                        report=report,
                    )

                    if (
                        self._critical_failure_counter >= self.failure_threshold
                        and self.enable_critical_failures
                    ):
                        await self._handle_critical_failure(report)

            except Exception as exc:  # pragma: no cover
                logger.error("Unexpected error in guardian loop.", exc_info=exc)
                if self.enable_metrics:
                    _inc_check("error")
            finally:
                await asyncio.sleep(self.check_interval)

        logger.info("Guardian loop terminated.")

    # ------------------------------------------------------------------- #
    #  Health-check implementation
    # ------------------------------------------------------------------- #
    async def _perform_health_check(self) -> Dict[str, Any]:
        """Runs a full health check and returns a structured report."""
        report: Dict[str, Any] = {
            "overall_healthy": True,
            "timestamp": time.time(),
            "shard_count": self.message_bus.shard_count,
            "unhealthy_shards": 0,
            "unhealthy_queues": 0,
            "unhealthy_bridges": 0,
            "unhealthy_components": 0,
            "critical_errors": [],
        }

        # ---- internal queues -------------------------------------------------
        for i in range(self.message_bus.shard_count):
            q = self.message_bus.queues[i]
            hp = self.message_bus.high_priority_queues[i]
            max_q = self.message_bus.max_queue_size

            if q.qsize() > max_q * 0.9:
                report["overall_healthy"] = False
                report["unhealthy_queues"] += 1
                report["critical_errors"].append(
                    f"Shard {i} normal queue >90% ({q.qsize()}/{max_q})"
                )
            if hp.qsize() > max_q * 0.9:
                report["overall_healthy"] = False
                report["unhealthy_queues"] += 1
                report["critical_errors"].append(
                    f"Shard {i} high-priority queue >90% ({hp.qsize()}/{max_q})"
                )

        # ---- external bridges ------------------------------------------------
        if self.message_bus.kafka_bridge:
            health = await self.message_bus.kafka_bridge.health()
            if not health.get("healthy", False):
                report["overall_healthy"] = False
                report["unhealthy_bridges"] += 1
                report["critical_errors"].append("Kafka bridge unhealthy")
            elif self.message_bus.kafka_bridge.circuit.state != "closed":
                report["overall_healthy"] = False
                report["unhealthy_bridges"] += 1
                report["critical_errors"].append(
                    f"Kafka circuit {self.message_bus.kafka_bridge.circuit.state}"
                )

        if self.message_bus.redis_bridge:
            health = await self.message_bus.redis_bridge.health()
            if not health.get("running", False) or not health.get(
                "redis_connected", False
            ):
                report["overall_healthy"] = False
                report["unhealthy_bridges"] += 1
                report["critical_errors"].append("Redis bridge unhealthy")
            elif self.message_bus.redis_bridge.circuit.state != "closed":
                report["overall_healthy"] = False
                report["unhealthy_bridges"] += 1
                report["critical_errors"].append(
                    f"Redis circuit {self.message_bus.redis_bridge.circuit.state}"
                )

        # ---- registered components -------------------------------------------
        for name, ok in self._component_statuses.items():
            if not ok:
                report["overall_healthy"] = False
                report["unhealthy_components"] += 1
                report["critical_errors"].append(f"Component '{name}' unhealthy")

        return report

    # ------------------------------------------------------------------- #
    #  Critical-failure handling
    # ------------------------------------------------------------------- #
    async def _handle_critical_failure(self, report: Dict[str, Any]) -> None:
        """Full critical-failure protocol."""
        logger.critical(
            "CRITICAL FAILURE THRESHOLD REACHED.",
            failure_count=self._critical_failure_counter,
            report=report,
        )
        MESSAGE_BUS_CRITICAL_FAILURES_TOTAL.inc()

        # 1. Alert (with retries)
        await self._send_alert_with_retries(report)

        # 2. DLQ (if the bus has one)
        if getattr(self.message_bus, "dlq", None):
            try:
                from .message_types import Message

                dlq_msg = Message(
                    topic="guardian.critical_failure",
                    payload=report,
                    priority=10,
                )
                await self.message_bus.dlq.add(dlq_msg, "Guardian critical failure")
                logger.info("Critical-failure report sent to DLQ.")
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to publish to DLQ.", exc_info=exc)

        # 3. Self-healing (restart bus)
        try:
            logger.warning("Initiating self-healing – shutting down bus.")
            await self.message_bus.shutdown()

            type(self.message_bus)(
                config=self.message_bus.config,
                db=self.message_bus.db,
                audit_client=self.message_bus.audit_client,
            )
            logger.info("New ShardedMessageBus instantiated – awaiting stabilisation.")
            await asyncio.sleep(5)  # simple wait; replace with readiness probe in prod

            self._critical_failure_counter = 0
            if self.enable_metrics:
                _inc_healing("success")
        except Exception as exc:  # pragma: no cover
            logger.critical("Self-healing failed.", exc_info=exc)
            if self.enable_metrics:
                _inc_healing("failure")
            raise CriticalFailure("Self-healing failed.") from exc

    # ------------------------------------------------------------------- #
    #  Alert webhook (retry-aware)
    # ------------------------------------------------------------------- #
    async def _send_alert_with_retries(self, report: Dict[str, Any]) -> None:
        webhook_url = getattr(settings, "ALERT_WEBHOOK_URL", None)
        if not webhook_url:
            logger.warning("ALERT_WEBHOOK_URL not configured – skipping alert.")
            return

        payload = {
            "level": "CRITICAL",
            "message": "OmniCore Message Bus Critical Failure Detected.",
            "details": f"Threshold reached ({self.failure_threshold} failures).",
            "report": report,
        }

        attempt = 0
        delay = self.alert_base_delay
        while attempt < self.alert_retries:
            try:
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        webhook_url, json=payload, timeout=5
                    ) as resp:
                        if resp.status == 200:
                            logger.info("Critical alert sent successfully.")
                            if self.enable_metrics:
                                _inc_alert("success")
                            return
                        raise ValueError(f"Webhook responded {resp.status}")
            except ImportError:
                logger.error("aiohttp missing – cannot send webhook.")
                if self.enable_metrics:
                    _inc_alert("import_error")
                return
            except Exception as exc:
                attempt += 1
                if self.enable_metrics:
                    _inc_alert("failure")
                logger.error(
                    f"Alert attempt {attempt}/{self.alert_retries} failed.",
                    exc_info=exc,
                )
                if attempt >= self.alert_retries:
                    return
                jitter = random.uniform(
                    -self.alert_jitter * delay, self.alert_jitter * delay
                )
                await asyncio.sleep(max(delay + jitter, 0))
                delay = min(delay * 2, self.alert_max_delay)

    # ------------------------------------------------------------------- #
    #  Public health endpoint (useful for monitoring)
    # ------------------------------------------------------------------- #
    async def health(self) -> Dict[str, Any]:
        """Return a snapshot of the guardian’s own health."""
        return {
            "running": self.running,
            "failure_counter": self._critical_failure_counter,
            "registered_components": list(self._component_statuses.keys()),
            "unhealthy_components": [
                n for n, ok in self._component_statuses.items() if not ok
            ],
            "last_check_timestamp": getattr(self, "_last_check_time", None),
        }
