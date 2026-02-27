# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
EventBusBridge — Tripartite event bridge for Mesh, Arbiter, and Simulation.

Problem
-------
The simulation module's :class:`ShardedMessageBus` had no integration with
the arbiter's event system.  Events published on ``requests.simulation.*``
topics were invisible to the Arbiter governance layer, and Arbiter decisions
never reached the simulation bus.  Additionally, the existing Mesh ↔ Arbiter
bridge had no awareness of the simulation subsystem, creating three isolated
event namespaces that could not exchange state.

Solution
--------
This module extends the bidirectional Mesh ↔ Arbiter bridge with a third
leg — Simulation :class:`ShardedMessageBus` → Arbiter — giving the platform
a unified event topology:

* **Mesh → Arbiter** (existing): mesh lifecycle events forwarded to Arbiter MQS.
* **Arbiter → Mesh** (existing): governance decisions forwarded to the mesh.
* **Simulation → Arbiter** (new): simulation requests on
  ``requests.simulation.*`` topics forwarded to Arbiter with full metadata.

Architecture
------------
::

    ┌──────────────────────────────────────────────────────────────┐
    │                      EventBusBridge                          │
    │                                                              │
    │  Mesh EventBus ──────mesh_to_arbiter──────► Arbiter MQS     │
    │  Arbiter MQS   ─────arbiter_to_mesh────────► Mesh EventBus  │
    │  Simulation Bus ──simulation_to_arbiter────► Arbiter MQS    │
    │                                                              │
    │  Each leg: correlation ID · Prometheus metrics · logging     │
    └──────────────────────────────────────────────────────────────┘

Each leg is independently optional — missing subsystems are logged and
skipped rather than raising, preventing cascading failures in lightweight
deployments.

Observability
-------------
* :data:`BRIDGE_EVENTS_TOTAL` counter — labels ``direction``, ``event_type``,
  ``status``.
* :data:`BRIDGE_LATENCY` histogram — label ``direction``; buckets tuned for
  in-process IPC latency (1 ms – 5 s).
* All metrics created via :func:`shared.noop_metrics.safe_metric`; degrade
  silently when ``prometheus_client`` is absent.
* Structured log fields carry ``direction``, ``event_type``, and
  ``correlation_id`` for end-to-end tracing.

Thread / Async Safety
---------------------
* :data:`_bridge_lock` serialises :func:`get_bridge` with double-checked
  locking to prevent duplicate singleton creation under concurrent startup.
* Background :class:`asyncio.Task` objects carry ``name=`` annotations for
  debuggability and are cancelled and awaited cleanly on :meth:`stop`.

Usage
-----
::

    # Application startup
    bridge = await get_bridge()
    print(bridge.get_stats())

    # Application teardown
    await stop_bridge()

See Also
--------
:mod:`self_fixing_engineer.shared.simulation_bridge` — wires a live Arbiter
instance directly to the simulation bus subscription API.
:mod:`self_fixing_engineer.shared.registry_bridge` — unified plugin-registry
bridge between simulation and arbiter subsystems.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Prometheus metrics — lazy, thread-safe, noop-safe via shared.noop_metrics
# ---------------------------------------------------------------------------
try:
    from prometheus_client import Counter as _PCounter, Histogram as _PHistogram
except ImportError:  # pragma: no cover
    _PCounter = None  # type: ignore[assignment]
    _PHistogram = None  # type: ignore[assignment]

try:
    from shared.noop_metrics import safe_metric as _safe_metric  # type: ignore[import]
except ImportError:  # pragma: no cover
    # Minimal inline fallback so this module works without the shared/ package.
    def _safe_metric(factory: Any, name: str, doc: str, **kw: Any) -> Any:  # type: ignore[misc]
        class _Noop:
            def labels(self, *_: Any, **__: Any) -> "_Noop":
                return self
            def inc(self, *_: Any, **__: Any) -> None:
                pass
            def observe(self, *_: Any, **__: Any) -> None:
                pass
        return _Noop()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level Prometheus metrics (created once; reused across all instances)
# ---------------------------------------------------------------------------
BRIDGE_EVENTS_TOTAL = _safe_metric(
    _PCounter,
    "event_bus_bridge_events_total",
    "Total events bridged between systems",
    labelnames=["direction", "event_type", "status"],
)
BRIDGE_LATENCY = _safe_metric(
    _PHistogram,
    "event_bus_bridge_latency_seconds",
    "Latency of event bridging in seconds",
    labelnames=["direction"],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

# ---------------------------------------------------------------------------
# Direction constants (avoid magic strings throughout)
# ---------------------------------------------------------------------------
_DIR_MESH_TO_ARBITER: str = "mesh_to_arbiter"
_DIR_ARBITER_TO_MESH: str = "arbiter_to_mesh"
_DIR_SIMULATION_TO_ARBITER: str = "simulation_to_arbiter"



class EventBusBridge:
    """Tripartite event bridge connecting Mesh, Arbiter, and Simulation buses.

    Each of the three bridge legs is independently optional: the bridge
    starts whichever legs have both endpoints available and logs a warning
    for any that are missing, rather than raising an exception.

    Attributes
    ----------
    mesh_to_arbiter_events : set[str]
        Event types forwarded from Mesh → Arbiter.
    arbiter_to_mesh_events : set[str]
        Event types forwarded from Arbiter → Mesh.
    running : bool
        ``True`` while the bridge has active forwarding tasks.
    mesh_bus : callable | None
        ``publish_event`` from ``self_fixing_engineer.mesh.event_bus``, or
        ``None`` when the mesh subsystem is unavailable.
    arbiter_mqs : MessageQueueService | None
        Arbiter message-queue service, or ``None`` when unavailable.
    simulation_bus : ShardedMessageBus | None
        Simulation message bus, or ``None`` when unavailable.

    Parameters
    ----------
    mesh_to_arbiter_events : set[str] | None
        Override the default Mesh event types forwarded to Arbiter.
        Pass an empty set to disable this leg entirely.
    arbiter_to_mesh_events : set[str] | None
        Override the default Arbiter event types forwarded to Mesh.
        Pass an empty set to disable this leg entirely.
    """

    _DEFAULT_MESH_TO_ARBITER: frozenset = frozenset({
        "mesh_event",
        "agent_update",
        "policy_violation",
        "system_alert",
    })
    _DEFAULT_ARBITER_TO_MESH: frozenset = frozenset({
        "arbiter_decision",
        "policy_update",
        "governance_alert",
        "task_assigned",
    })

    def __init__(
        self,
        mesh_to_arbiter_events: Optional[Set[str]] = None,
        arbiter_to_mesh_events: Optional[Set[str]] = None,
    ) -> None:
        self.mesh_to_arbiter_events: Set[str] = (
            set(mesh_to_arbiter_events)
            if mesh_to_arbiter_events is not None
            else set(self._DEFAULT_MESH_TO_ARBITER)
        )
        self.arbiter_to_mesh_events: Set[str] = (
            set(arbiter_to_mesh_events)
            if arbiter_to_mesh_events is not None
            else set(self._DEFAULT_ARBITER_TO_MESH)
        )

        self.running: bool = False
        self._tasks: List[asyncio.Task] = []  # type: ignore[type-arg]

        self.mesh_bus: Any = None
        self.arbiter_mqs: Any = None
        self.simulation_bus: Any = None

        self._init_mesh_bus()
        self._init_arbiter_mqs()
        self._init_simulation_bus()

    # ------------------------------------------------------------------
    # Subsystem initialisation
    # ------------------------------------------------------------------

    def _init_mesh_bus(self) -> None:
        """Resolve the Mesh EventBus ``publish_event`` callable."""
        try:
            from self_fixing_engineer.mesh.event_bus import publish_event  # type: ignore[import]

            self.mesh_bus = publish_event
            logger.info("EventBusBridge: Mesh EventBus available")
        except ImportError as exc:
            logger.warning("EventBusBridge: Mesh EventBus not available — %s", exc)
        except Exception:
            logger.error(
                "EventBusBridge: Failed to initialise Mesh EventBus", exc_info=True
            )

    def _init_arbiter_mqs(self) -> None:
        """Instantiate the Arbiter MessageQueueService."""
        try:
            from self_fixing_engineer.arbiter.message_queue_service import (  # type: ignore[import]
                MessageQueueService,
            )

            self.arbiter_mqs = MessageQueueService()
            logger.info("EventBusBridge: Arbiter MessageQueueService available")
        except ImportError as exc:
            logger.warning("EventBusBridge: Arbiter MQS not available — %s", exc)
        except Exception:
            logger.error(
                "EventBusBridge: Failed to initialise Arbiter MQS", exc_info=True
            )

    def _init_simulation_bus(self) -> None:
        """Instantiate the Simulation ShardedMessageBus.

        The bus is created without ``enable_dlq=True``; DLQ semantics belong
        to the producing simulation components.
        """
        try:
            from self_fixing_engineer.simulation.simulation_module import (  # type: ignore[import]
                ShardedMessageBus,
            )

            self.simulation_bus = ShardedMessageBus()
            logger.info("EventBusBridge: Simulation ShardedMessageBus available")
        except ImportError as exc:
            logger.warning("EventBusBridge: Simulation bus not available — %s", exc)
        except Exception:
            logger.error(
                "EventBusBridge: Failed to initialise simulation bus", exc_info=True
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start all available bridge legs as background :class:`asyncio.Task` objects.

        Returns immediately without error when no subsystems are available.
        Already-running bridges are not restarted.
        """
        if self.running:
            logger.debug("EventBusBridge: Already running — ignoring start()")
            return

        if not any([self.mesh_bus, self.arbiter_mqs, self.simulation_bus]):
            logger.warning(
                "EventBusBridge: No subsystems available — bridge will not start."
            )
            return

        self.running = True
        logger.info("EventBusBridge: Starting event bridge")

        if self.mesh_bus and self.arbiter_mqs:
            self._tasks.append(
                asyncio.create_task(
                    self._bridge_mesh_to_arbiter(),
                    name="event_bridge:mesh_to_arbiter",
                )
            )
            logger.info("EventBusBridge: Mesh → Arbiter bridge started")

        if self.arbiter_mqs and self.mesh_bus:
            self._tasks.append(
                asyncio.create_task(
                    self._bridge_arbiter_to_mesh(),
                    name="event_bridge:arbiter_to_mesh",
                )
            )
            logger.info("EventBusBridge: Arbiter → Mesh bridge started")

        if self.simulation_bus and self.arbiter_mqs:
            self._tasks.append(
                asyncio.create_task(
                    self._bridge_simulation_to_arbiter(),
                    name="event_bridge:simulation_to_arbiter",
                )
            )
            logger.info("EventBusBridge: Simulation → Arbiter bridge started")

        active = sum(1 for t in self._tasks if not t.done())
        logger.info(
            "EventBusBridge: Active with %d leg(s). "
            "Mesh→Arbiter types=%d, Arbiter→Mesh types=%d",
            active,
            len(self.mesh_to_arbiter_events),
            len(self.arbiter_to_mesh_events),
        )

    async def stop(self) -> None:
        """Cancel all background bridge tasks and await their completion."""
        self.running = False
        logger.info("EventBusBridge: Stopping...")

        for task in self._tasks:
            if not task.done():
                task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        self._tasks.clear()
        logger.info("EventBusBridge: Stopped")

    # ------------------------------------------------------------------
    # Bridge leg workers
    # ------------------------------------------------------------------

    async def _bridge_mesh_to_arbiter(self) -> None:
        """Subscribe to configured Mesh event types and forward to Arbiter."""
        logger.info("EventBusBridge: Mesh → Arbiter worker started")
        try:
            for event_type in self.mesh_to_arbiter_events:
                await self._subscribe_mesh_event(event_type)
        except Exception:
            logger.error(
                "EventBusBridge: Mesh → Arbiter worker error", exc_info=True
            )

    async def _bridge_arbiter_to_mesh(self) -> None:
        """Subscribe to configured Arbiter event types and forward to Mesh."""
        logger.info("EventBusBridge: Arbiter → Mesh worker started")
        try:
            for event_type in self.arbiter_to_mesh_events:
                # Capture loop variable by value via default argument.
                async def _handler(
                    data: Dict[str, Any], _et: str = event_type
                ) -> None:
                    await self._forward_arbiter_to_mesh(_et, data)

                await self.arbiter_mqs.subscribe(event_type, _handler)
        except Exception:
            logger.error(
                "EventBusBridge: Arbiter → Mesh worker error", exc_info=True
            )

    async def _bridge_simulation_to_arbiter(self) -> None:
        """Subscribe to ``requests.simulation.*`` on the simulation bus and
        forward matching events to the Arbiter MessageQueueService.

        Uses the ``topic_pattern`` wildcard API of
        :class:`~self_fixing_engineer.simulation.simulation_module.ShardedMessageBus`.
        All forwarded events arrive at the canonical Arbiter topic
        ``"events.simulation.request"``.
        """
        logger.info("EventBusBridge: Simulation → Arbiter worker started")
        try:
            async def _handle_simulation_request(data: Dict[str, Any]) -> None:
                await self._forward_simulation_to_arbiter(data)

            await self.simulation_bus.subscribe(
                topic_pattern="requests.simulation.*",
                handler=_handle_simulation_request,
            )
            logger.info(
                "EventBusBridge: Subscribed to requests.simulation.* on simulation bus"
            )
        except Exception:
            logger.error(
                "EventBusBridge: Simulation → Arbiter worker error", exc_info=True
            )

    # ------------------------------------------------------------------
    # Mesh subscription placeholder
    # ------------------------------------------------------------------

    async def _subscribe_mesh_event(self, event_type: str) -> None:
        """Register a Mesh → Arbiter forwarding subscription for *event_type*.

        .. note::
            The current Mesh EventBus exposes only a ``publish_event``
            callable and does not yet provide a ``subscribe()`` API.  This
            method is a no-op placeholder; it will be wired up when Mesh
            gains subscription support.
        """
        logger.debug(
            "EventBusBridge: Subscription for %r (mesh_to_arbiter) "
            "pending Mesh EventBus subscription API",
            event_type,
        )

    # ------------------------------------------------------------------
    # Envelope factory
    # ------------------------------------------------------------------

    @staticmethod
    def _make_bridge_envelope(
        data: Dict[str, Any],
        *,
        source: str,
        destination: str,
        original_event_type: str,
        correlation_id: str,
    ) -> Dict[str, Any]:
        """Return *data* merged with standard bridge metadata.

        The ``_bridge`` key is always overwritten to ensure fresh metadata.

        Parameters
        ----------
        data:
            Original event payload (not mutated).
        source:
            Originating subsystem (``"mesh"``, ``"arbiter"``, ``"simulation"``).
        destination:
            Target subsystem name.
        original_event_type:
            The event type as it appears on the source bus.
        correlation_id:
            Unique identifier for end-to-end tracing.
        """
        return {
            **data,
            "_bridge": {
                "source": source,
                "destination": destination,
                "bridged_at": time.time(),
                "original_event_type": original_event_type,
                "correlation_id": correlation_id,
            },
        }

    # ------------------------------------------------------------------
    # Forwarding helpers
    # ------------------------------------------------------------------

    async def _forward_mesh_to_arbiter(
        self, event_type: str, data: Dict[str, Any]
    ) -> None:
        """Forward a single Mesh event to the Arbiter MessageQueueService."""
        cid: str = str(data.get("correlation_id") or uuid.uuid4().hex[:12])
        t0: float = time.monotonic()
        try:
            await self.arbiter_mqs.publish(
                event_type,
                self._make_bridge_envelope(
                    data,
                    source="mesh",
                    destination="arbiter",
                    original_event_type=event_type,
                    correlation_id=cid,
                ),
            )
            BRIDGE_EVENTS_TOTAL.labels(
                direction=_DIR_MESH_TO_ARBITER, event_type=event_type, status="success"
            ).inc()
            BRIDGE_LATENCY.labels(direction=_DIR_MESH_TO_ARBITER).observe(
                time.monotonic() - t0
            )
            logger.debug(
                "EventBusBridge: %s Mesh → Arbiter [cid=%s]", event_type, cid
            )
        except Exception:
            BRIDGE_EVENTS_TOTAL.labels(
                direction=_DIR_MESH_TO_ARBITER, event_type=event_type, status="error"
            ).inc()
            logger.error(
                "EventBusBridge: Failed to forward %r Mesh → Arbiter [cid=%s]",
                event_type, cid, exc_info=True,
                extra={"direction": _DIR_MESH_TO_ARBITER, "correlation_id": cid},
            )

    async def _forward_arbiter_to_mesh(
        self, event_type: str, data: Dict[str, Any]
    ) -> None:
        """Forward a single Arbiter event to the Mesh EventBus callable."""
        cid: str = str(data.get("correlation_id") or uuid.uuid4().hex[:12])
        t0: float = time.monotonic()
        try:
            await self.mesh_bus(
                event_type,
                self._make_bridge_envelope(
                    data,
                    source="arbiter",
                    destination="mesh",
                    original_event_type=event_type,
                    correlation_id=cid,
                ),
            )
            BRIDGE_EVENTS_TOTAL.labels(
                direction=_DIR_ARBITER_TO_MESH, event_type=event_type, status="success"
            ).inc()
            BRIDGE_LATENCY.labels(direction=_DIR_ARBITER_TO_MESH).observe(
                time.monotonic() - t0
            )
            logger.debug(
                "EventBusBridge: %s Arbiter → Mesh [cid=%s]", event_type, cid
            )
        except Exception:
            BRIDGE_EVENTS_TOTAL.labels(
                direction=_DIR_ARBITER_TO_MESH, event_type=event_type, status="error"
            ).inc()
            logger.error(
                "EventBusBridge: Failed to forward %r Arbiter → Mesh [cid=%s]",
                event_type, cid, exc_info=True,
                extra={"direction": _DIR_ARBITER_TO_MESH, "correlation_id": cid},
            )

    async def _forward_simulation_to_arbiter(self, data: Dict[str, Any]) -> None:
        """Forward a simulation bus event to the Arbiter MessageQueueService.

        All simulation request events are published to the canonical Arbiter
        topic ``"events.simulation.request"`` so that governance logic has a
        single, stable subscription point regardless of the original topic.
        """
        cid: str = str(data.get("correlation_id") or uuid.uuid4().hex[:12])
        original: str = str(data.get("topic", "simulation.request"))
        t0: float = time.monotonic()
        try:
            await self.arbiter_mqs.publish(
                "events.simulation.request",
                self._make_bridge_envelope(
                    data,
                    source="simulation",
                    destination="arbiter",
                    original_event_type=original,
                    correlation_id=cid,
                ),
            )
            BRIDGE_EVENTS_TOTAL.labels(
                direction=_DIR_SIMULATION_TO_ARBITER,
                event_type=original,
                status="success",
            ).inc()
            BRIDGE_LATENCY.labels(direction=_DIR_SIMULATION_TO_ARBITER).observe(
                time.monotonic() - t0
            )
            logger.debug(
                "EventBusBridge: simulation:%s → Arbiter [cid=%s]", original, cid
            )
        except Exception:
            BRIDGE_EVENTS_TOTAL.labels(
                direction=_DIR_SIMULATION_TO_ARBITER,
                event_type=original,
                status="error",
            ).inc()
            logger.error(
                "EventBusBridge: Failed to forward simulation event %r → Arbiter [cid=%s]",
                original, cid, exc_info=True,
                extra={"direction": _DIR_SIMULATION_TO_ARBITER, "correlation_id": cid},
            )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Return a serialisable snapshot of bridge state.

        Suitable for health-check endpoints and operator dashboards.

        Returns
        -------
        dict
            Keys: ``running``, ``mesh_available``, ``arbiter_available``,
            ``simulation_available``, ``mesh_to_arbiter_events``,
            ``arbiter_to_mesh_events``, ``active_tasks``.
        """
        return {
            "running": self.running,
            "mesh_available": self.mesh_bus is not None,
            "arbiter_available": self.arbiter_mqs is not None,
            "simulation_available": self.simulation_bus is not None,
            "mesh_to_arbiter_events": sorted(self.mesh_to_arbiter_events),
            "arbiter_to_mesh_events": sorted(self.arbiter_to_mesh_events),
            "active_tasks": sum(1 for t in self._tasks if not t.done()),
        }


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------

_bridge_instance: Optional[EventBusBridge] = None
_bridge_lock: threading.Lock = threading.Lock()


async def get_bridge(
    mesh_to_arbiter_events: Optional[Set[str]] = None,
    arbiter_to_mesh_events: Optional[Set[str]] = None,
) -> EventBusBridge:
    """Return (or create and start) the global :class:`EventBusBridge` singleton.

    Uses double-checked locking to prevent duplicate instantiation under
    concurrent startup.

    Parameters
    ----------
    mesh_to_arbiter_events:
        Forwarded to :class:`EventBusBridge` on first creation only.
    arbiter_to_mesh_events:
        Forwarded to :class:`EventBusBridge` on first creation only.

    Returns
    -------
    EventBusBridge
        The running singleton instance.
    """
    global _bridge_instance

    # Fast path — no lock needed once initialised
    if _bridge_instance is not None:
        return _bridge_instance

    with _bridge_lock:
        if _bridge_instance is None:  # Double-checked locking
            _bridge_instance = EventBusBridge(
                mesh_to_arbiter_events=mesh_to_arbiter_events,
                arbiter_to_mesh_events=arbiter_to_mesh_events,
            )
            await _bridge_instance.start()

    return _bridge_instance


async def stop_bridge() -> None:
    """Stop and discard the global :class:`EventBusBridge` singleton."""
    global _bridge_instance
    with _bridge_lock:
        if _bridge_instance is not None:
            await _bridge_instance.stop()
            _bridge_instance = None
