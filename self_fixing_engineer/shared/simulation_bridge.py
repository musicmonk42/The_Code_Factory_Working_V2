# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
SimulationBridge — Runtime wiring of Arbiter ↔ Simulation event flows.

Problem
-------
At application startup the simulation module's
:class:`~self_fixing_engineer.simulation.simulation_module.ShardedMessageBus`
and the live :class:`~self_fixing_engineer.arbiter.arbiter.Arbiter` instance
are both running but unconnected at the event level.  Simulation requests
published on ``requests.simulation.*`` topics never reach the Arbiter
governance layer, and Arbiter ``task_complete`` events never reach the
simulation bus — preventing feedback loops and audit-trail completeness.

Solution
--------
:func:`setup_simulation_bridge` performs two wiring operations at startup:

1. **Simulation → Arbiter**: Subscribes to ``requests.simulation.*`` on the
   simulation :class:`ShardedMessageBus`.  Each received payload is
   forwarded to ``arbiter_instance.handle_simulation_request()`` when that
   method exists, falling back to a structured log entry.

2. **Arbiter → Simulation**: Registers a ``task_complete`` handler on the
   Arbiter via ``arbiter_instance.on_event()`` (when supported).  Each
   event is published asynchronously to the simulation bus under the topic
   ``events.arbiter.task_complete``.

Both wiring paths are independently optional — if the Arbiter or simulation
bus lacks the expected interface, the bridge logs a warning rather than
raising, allowing the application to continue in a partially-connected state.

Observability
-------------
* :data:`_BRIDGE_OPS` Prometheus counter — labels ``operation``,
  ``status``; tracks subscription setups and event publications.
* All log entries include ``correlation_id`` when available in the payload.

Usage
-----
::

    # In application lifespan (after both arbiter and simulation_module
    # are fully initialised):
    from self_fixing_engineer.shared.simulation_bridge import setup_simulation_bridge

    await setup_simulation_bridge(
        arbiter_instance=chatbot_arbiter,
        simulation_bus=simulation_module.message_bus,
    )

See Also
--------
:mod:`self_fixing_engineer.arbiter.event_bus_bridge` — tripartite bridge
also wiring the simulation bus, managed as an application-level singleton.
:mod:`self_fixing_engineer.shared.registry_bridge` — plugin-registry bridge.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Prometheus metrics — lazy, noop-safe
# ---------------------------------------------------------------------------
try:
    from prometheus_client import Counter as _PCounter
except ImportError:  # pragma: no cover
    _PCounter = None  # type: ignore[assignment]

try:
    from shared.noop_metrics import safe_metric as _safe_metric  # type: ignore[import]
except ImportError:  # pragma: no cover
    def _safe_metric(factory: Any, name: str, doc: str, **kw: Any) -> Any:  # type: ignore[misc]
        class _Noop:
            def labels(self, *_: Any, **__: Any) -> "_Noop":
                return self
            def inc(self, *_: Any, **__: Any) -> None:
                pass
        return _Noop()

logger = logging.getLogger(__name__)

_BRIDGE_OPS = _safe_metric(
    _PCounter,
    "sfe_simulation_bridge_ops_total",
    "Total operations performed by SimulationBridge",
    labelnames=["operation", "status"],
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def setup_simulation_bridge(
    arbiter_instance: Any,
    simulation_bus: Any,
) -> None:
    """Wire a live Arbiter instance to the simulation :class:`ShardedMessageBus`.

    Performs two independent wiring operations and logs a warning (never
    raises) when either endpoint is missing or incompatible.

    Parameters
    ----------
    arbiter_instance:
        A running :class:`~self_fixing_engineer.arbiter.arbiter.Arbiter`
        instance, or any object exposing ``handle_simulation_request()``
        and/or ``on_event()``.  ``None`` is accepted and causes a warning.
    simulation_bus:
        A
        :class:`~self_fixing_engineer.simulation.simulation_module.ShardedMessageBus`
        instance exposing ``subscribe(topic_pattern, handler)`` and
        ``publish(topic, payload)``.  ``None`` is accepted and causes a
        warning.
    """
    if simulation_bus is None:
        logger.warning(
            "SimulationBridge: simulation_bus is None — bridge not started"
        )
        return

    if arbiter_instance is None:
        logger.warning(
            "SimulationBridge: arbiter_instance is None — bridge not started"
        )
        return

    await _wire_simulation_to_arbiter(arbiter_instance, simulation_bus)
    _wire_arbiter_to_simulation(arbiter_instance, simulation_bus)


async def _wire_simulation_to_arbiter(
    arbiter_instance: Any,
    simulation_bus: Any,
) -> None:
    """Subscribe to ``requests.simulation.*`` and forward to Arbiter.

    Parameters
    ----------
    arbiter_instance:
        Target Arbiter instance.
    simulation_bus:
        Source :class:`ShardedMessageBus`.
    """
    try:
        async def _handle(payload: Dict[str, Any]) -> None:
            cid: str = str(payload.get("correlation_id") or uuid.uuid4().hex[:12])
            try:
                if hasattr(arbiter_instance, "handle_simulation_request"):
                    await arbiter_instance.handle_simulation_request(payload)
                    _BRIDGE_OPS.labels(
                        operation="sim_to_arbiter", status="success"
                    ).inc()
                else:
                    logger.debug(
                        "SimulationBridge: arbiter has no handle_simulation_request — "
                        "event dropped [cid=%s]",
                        cid,
                    )
                    _BRIDGE_OPS.labels(
                        operation="sim_to_arbiter", status="no_handler"
                    ).inc()
            except Exception:
                _BRIDGE_OPS.labels(operation="sim_to_arbiter", status="error").inc()
                logger.error(
                    "SimulationBridge: error forwarding simulation request [cid=%s]",
                    cid,
                    exc_info=True,
                    extra={"correlation_id": cid},
                )

        await simulation_bus.subscribe(
            topic_pattern="requests.simulation.*",
            handler=_handle,
        )
        _BRIDGE_OPS.labels(operation="subscribe_sim_topics", status="success").inc()
        logger.info(
            "SimulationBridge: Subscribed to requests.simulation.* on simulation bus"
        )
    except Exception:
        _BRIDGE_OPS.labels(operation="subscribe_sim_topics", status="error").inc()
        logger.error(
            "SimulationBridge: Failed to subscribe to simulation topics", exc_info=True
        )


def _wire_arbiter_to_simulation(
    arbiter_instance: Any,
    simulation_bus: Any,
) -> None:
    """Register an Arbiter ``task_complete`` handler that publishes to the simulation bus.

    Uses ``arbiter_instance.on_event()`` when available.  The callback
    schedules an async publish via :func:`asyncio.ensure_future` so it is
    safe to call from synchronous Arbiter event dispatch.

    Parameters
    ----------
    arbiter_instance:
        Source Arbiter instance.
    simulation_bus:
        Target :class:`ShardedMessageBus`.
    """
    if not hasattr(arbiter_instance, "on_event"):
        logger.warning(
            "SimulationBridge: arbiter_instance has no on_event() — "
            "Arbiter → Simulation leg not wired"
        )
        _BRIDGE_OPS.labels(operation="register_arbiter_handler", status="no_method").inc()
        return

    try:
        def _on_task_complete(event: Dict[str, Any]) -> None:
            asyncio.ensure_future(
                _publish_to_simulation(
                    simulation_bus,
                    "events.arbiter.task_complete",
                    event,
                )
            )

        arbiter_instance.on_event("task_complete", _on_task_complete)
        _BRIDGE_OPS.labels(
            operation="register_arbiter_handler", status="success"
        ).inc()
        logger.info(
            "SimulationBridge: Registered task_complete handler on arbiter"
        )
    except Exception:
        _BRIDGE_OPS.labels(operation="register_arbiter_handler", status="error").inc()
        logger.warning(
            "SimulationBridge: Could not register arbiter event handler",
            exc_info=True,
        )


async def _publish_to_simulation(
    simulation_bus: Any,
    topic: str,
    event: Dict[str, Any],
) -> None:
    """Publish *event* to *topic* on *simulation_bus*.

    Parameters
    ----------
    simulation_bus:
        :class:`ShardedMessageBus` instance.
    topic:
        Destination topic string.
    event:
        Payload dict to publish.
    """
    cid: str = str(event.get("correlation_id") or uuid.uuid4().hex[:12])
    t0: float = time.monotonic()
    try:
        await simulation_bus.publish(topic, event)
        _BRIDGE_OPS.labels(operation="publish_to_sim", status="success").inc()
        logger.debug(
            "SimulationBridge: Published %s to simulation bus in %.3fs [cid=%s]",
            topic,
            time.monotonic() - t0,
            cid,
        )
    except Exception:
        _BRIDGE_OPS.labels(operation="publish_to_sim", status="error").inc()
        logger.error(
            "SimulationBridge: Failed to publish %s [cid=%s]",
            topic,
            cid,
            exc_info=True,
            extra={"topic": topic, "correlation_id": cid},
        )
