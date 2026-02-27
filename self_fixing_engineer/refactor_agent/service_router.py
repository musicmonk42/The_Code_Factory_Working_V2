# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
service_router.py

Routes service:// URIs to actual handler implementations.
Handles all 7 event hook URIs from refactor_agent.yaml plus the escalation
policy path resolver.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_URI_PATTERN = re.compile(r"^service://(?P<service>[^/]+)/(?P<action>.+)$")


# ---------------------------------------------------------------------------
# Arbiter protocol — a structural type that describes the subset of the
# Arbiter interface consumed by ServiceRouter.  Using a Protocol avoids a
# hard circular import while still enabling static type checking.
# ---------------------------------------------------------------------------

@runtime_checkable
class _ArbiterBridge(Protocol):
    """Structural protocol describing the Arbiter interface used by ServiceRouter."""

    name: str

    def log_event(self, description: str, event_type: str = "general") -> None: ...

    @property
    def human_in_loop(self) -> Any: ...  # HumanInLoop | None

    @property
    def message_queue_service(self) -> Any: ...  # MessageQueueService | None

# ---------------------------------------------------------------------------
# Built-in handler implementations
# ---------------------------------------------------------------------------


async def _handle_escalate_and_log(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Log agent failure and trigger an escalation."""
    agent = payload.get("agent", "unknown")
    error = payload.get("error", "")
    logger.error(
        "service://automation/escalate_and_log: agent=%s error=%s", agent, error
    )
    return {
        "status": "escalated",
        "action": "escalate_and_log",
        "agent": agent,
        "timestamp": time.time(),
    }


async def _handle_provenance_update(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Update the provenance log when an artifact is created."""
    artifact = payload.get("artifact")
    logger.info("service://provenance/update: artifact=%s", artifact)
    return {
        "status": "logged",
        "action": "provenance_update",
        "artifact": artifact,
        "timestamp": time.time(),
    }


async def _handle_trigger_human_review(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Escalate to human agent when a score is below threshold."""
    score = payload.get("score")
    threshold = payload.get("threshold")
    logger.warning(
        "service://workflow/trigger_human_review: score=%s threshold=%s",
        score,
        threshold,
    )
    return {
        "status": "human_review_triggered",
        "action": "trigger_human_review",
        "score": score,
        "threshold": threshold,
        "timestamp": time.time(),
    }


async def _handle_escalate_to_human(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Escalate a blocked pipeline to human intervention."""
    pipeline = payload.get("pipeline", "unknown")
    reason = payload.get("reason", "")
    logger.warning(
        "service://automation/escalate_to_human: pipeline=%s reason=%s",
        pipeline,
        reason,
    )
    return {
        "status": "escalated_to_human",
        "action": "escalate_to_human",
        "pipeline": pipeline,
        "timestamp": time.time(),
    }


async def _handle_trigger_consensus(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Log a swarm disagreement event and trigger consensus resolution."""
    agents = payload.get("agents", [])
    topic = payload.get("topic", "")
    logger.info(
        "service://swarm/trigger_consensus: agents=%s topic=%s", agents, topic
    )
    return {
        "status": "consensus_triggered",
        "action": "trigger_consensus",
        "topic": topic,
        "timestamp": time.time(),
    }


async def _handle_update_knowledge(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Update the swarm knowledge base with a new learning opportunity."""
    learning = payload.get("learning", {})
    agent = payload.get("agent", "unknown")
    logger.info(
        "service://swarm/update_knowledge: agent=%s learning=%s", agent, learning
    )
    return {
        "status": "knowledge_updated",
        "action": "update_knowledge",
        "agent": agent,
        "timestamp": time.time(),
    }


async def _handle_oracle_notify(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Notify the oracle agent of a world event."""
    event_type = payload.get("event_type", "unknown")
    data = payload.get("data", {})
    logger.info(
        "service://oracle/notify: event_type=%s", event_type
    )
    return {
        "status": "oracle_notified",
        "action": "oracle_notify",
        "event_type": event_type,
        "timestamp": time.time(),
    }


async def _handle_escalation_policy_paths(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve dynamic escalation paths for the given context."""
    context = payload.get("context", {})
    logger.debug("service://escalation-policy/v1/paths: context=%s", context)
    # Default escalation chain: agent → human → admin
    return {
        "status": "resolved",
        "paths": ["human_escalation_node", "admin"],
        "context": context,
        "timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_DEFAULT_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
    "automation/escalate_and_log": _handle_escalate_and_log,
    "provenance/update": _handle_provenance_update,
    "workflow/trigger_human_review": _handle_trigger_human_review,
    "automation/escalate_to_human": _handle_escalate_to_human,
    "swarm/trigger_consensus": _handle_trigger_consensus,
    "swarm/update_knowledge": _handle_update_knowledge,
    "oracle/notify": _handle_oracle_notify,
    "escalation-policy/v1/paths": _handle_escalation_policy_paths,
}


class ServiceRouter:
    """Routes service:// URIs to actual handler implementations.

    When an ``arbiter`` instance is provided the escalation handlers call into
    the Arbiter's ``human_in_loop.request_approval()`` and ``log_event()``
    directly, giving full end-to-end routing.  When no arbiter is available the
    router falls back to standalone logging so it can still be used in tests and
    CLI contexts without a live Arbiter.

    Args:
        arbiter: Optional Arbiter instance to integrate with for escalations,
                 audit logging, human-in-the-loop, and message queue publishing.
    """

    def __init__(self, arbiter: Optional[_ArbiterBridge] = None) -> None:
        self._arbiter = arbiter
        self._handlers: Dict[
            str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
        ] = dict(_DEFAULT_HANDLERS)
        # Override escalation handlers when an Arbiter is available so they use
        # the real human_in_loop and audit subsystems.
        if arbiter is not None:
            self._handlers["automation/escalate_and_log"] = self._arbiter_escalate_and_log
            self._handlers["workflow/trigger_human_review"] = self._arbiter_trigger_human_review
            self._handlers["automation/escalate_to_human"] = self._arbiter_escalate_to_human
            self._handlers["swarm/update_knowledge"] = self._arbiter_update_knowledge
            self._handlers["oracle/notify"] = self._arbiter_oracle_notify

    # ------------------------------------------------------------------
    # Arbiter-backed handlers (override defaults when arbiter is present)
    # ------------------------------------------------------------------

    async def _arbiter_escalate_and_log(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Escalate and log via Arbiter's audit trail and alert mechanism."""
        agent = payload.get("agent", "unknown")
        error = payload.get("error", "")
        arbiter = self._arbiter
        logger.error(
            "service://automation/escalate_and_log via Arbiter: agent=%s error=%s", agent, error
        )
        if arbiter is not None:
            arbiter.log_event(f"Crew agent '{agent}' failure escalated: {error}", "crew_escalation")
            if arbiter.human_in_loop:
                try:
                    await arbiter.human_in_loop.request_approval({
                        "issue": f"Agent '{agent}' failure requires attention: {error}",
                        "agent": agent,
                        "error": error,
                        "arbiter": arbiter.name,
                    })
                except Exception as exc:
                    logger.error("escalate_and_log: human_in_loop.request_approval failed: %s", exc)
            if arbiter.message_queue_service:
                try:
                    await arbiter.message_queue_service.publish(
                        "crew_escalation", {"event": "escalate_and_log", "agent": agent, "error": error}
                    )
                except Exception as exc:
                    logger.error("escalate_and_log: message_queue_service.publish failed: %s", exc)
        return {
            "status": "escalated",
            "action": "escalate_and_log",
            "agent": agent,
            "arbiter_integrated": arbiter is not None,
            "timestamp": time.time(),
        }

    async def _arbiter_trigger_human_review(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Trigger human review via Arbiter's HumanInLoop subsystem."""
        score = payload.get("score")
        threshold = payload.get("threshold")
        agent = payload.get("agent", "unknown")
        arbiter = self._arbiter
        logger.warning(
            "service://workflow/trigger_human_review via Arbiter: agent=%s score=%s threshold=%s",
            agent, score, threshold,
        )
        if arbiter is not None and arbiter.human_in_loop:
            try:
                await arbiter.human_in_loop.request_approval({
                    "issue": f"Agent '{agent}' quality score {score} is below threshold {threshold}",
                    "agent": agent,
                    "score": score,
                    "threshold": threshold,
                    "arbiter": arbiter.name,
                })
            except Exception as exc:
                logger.error("trigger_human_review: human_in_loop.request_approval failed: %s", exc)
        return {
            "status": "human_review_triggered",
            "action": "trigger_human_review",
            "agent": agent,
            "score": score,
            "threshold": threshold,
            "arbiter_integrated": arbiter is not None,
            "timestamp": time.time(),
        }

    async def _arbiter_escalate_to_human(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Escalate a blocked pipeline to the Arbiter's HumanInLoop subsystem."""
        pipeline = payload.get("pipeline", "unknown")
        reason = payload.get("reason", "")
        arbiter = self._arbiter
        logger.warning(
            "service://automation/escalate_to_human via Arbiter: pipeline=%s reason=%s",
            pipeline, reason,
        )
        if arbiter is not None:
            arbiter.log_event(f"Pipeline '{pipeline}' blocked — escalating: {reason}", "pipeline_escalation")
            if arbiter.human_in_loop:
                try:
                    await arbiter.human_in_loop.request_approval({
                        "issue": f"Pipeline '{pipeline}' is blocked: {reason}",
                        "pipeline": pipeline,
                        "reason": reason,
                        "arbiter": arbiter.name,
                    })
                except Exception as exc:
                    logger.error("escalate_to_human: human_in_loop.request_approval failed: %s", exc)
        return {
            "status": "escalated_to_human",
            "action": "escalate_to_human",
            "pipeline": pipeline,
            "arbiter_integrated": arbiter is not None,
            "timestamp": time.time(),
        }

    async def _arbiter_update_knowledge(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Update swarm knowledge via the message queue."""
        learning = payload.get("learning", {})
        agent = payload.get("agent", "unknown")
        arbiter = self._arbiter
        logger.info(
            "service://swarm/update_knowledge via Arbiter: agent=%s", agent
        )
        if arbiter is not None and arbiter.message_queue_service:
            try:
                await arbiter.message_queue_service.publish(
                    "crew_knowledge_lifecycle",
                    {"event": "learning_opportunity", "agent": agent, "learning": learning, "arbiter": arbiter.name},
                )
            except Exception as exc:
                logger.error("update_knowledge: message_queue_service.publish failed: %s", exc)
        return {
            "status": "knowledge_updated",
            "action": "update_knowledge",
            "agent": agent,
            "arbiter_integrated": arbiter is not None,
            "timestamp": time.time(),
        }

    async def _arbiter_oracle_notify(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Notify oracle of world event via the message queue."""
        event_type = payload.get("event_type", "unknown")
        data = payload.get("data", {})
        arbiter = self._arbiter
        logger.info("service://oracle/notify via Arbiter: event_type=%s", event_type)
        if arbiter is not None and arbiter.message_queue_service:
            try:
                await arbiter.message_queue_service.publish(
                    "crew_oracle_lifecycle",
                    {"event": "world_event", "event_type": event_type, "data": data, "arbiter": arbiter.name},
                )
            except Exception as exc:
                logger.error("oracle_notify: message_queue_service.publish failed: %s", exc)
        return {
            "status": "oracle_notified",
            "action": "oracle_notify",
            "event_type": event_type,
            "arbiter_integrated": arbiter is not None,
            "timestamp": time.time(),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def bind_arbiter(self, arbiter: _ArbiterBridge) -> None:
        """Attach (or re-attach) an Arbiter instance after construction.

        This allows the ServiceRouter to be created before the Arbiter is
        instantiated (e.g., when loaded from YAML config) and then connected
        later once the Arbiter is ready.

        Args:
            arbiter: The live Arbiter instance to integrate with.
        """
        self._arbiter = arbiter
        self._handlers["automation/escalate_and_log"] = self._arbiter_escalate_and_log
        self._handlers["workflow/trigger_human_review"] = self._arbiter_trigger_human_review
        self._handlers["automation/escalate_to_human"] = self._arbiter_escalate_to_human
        self._handlers["swarm/update_knowledge"] = self._arbiter_update_knowledge
        self._handlers["oracle/notify"] = self._arbiter_oracle_notify
        logger.info("ServiceRouter: bound to Arbiter '%s'", getattr(arbiter, "name", arbiter))

    def register(
        self,
        route: str,
        handler: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
    ) -> None:
        """Register a custom handler for a service route.

        Args:
            route: The route key in the form ``<service>/<action>``
                   (the part after ``service://``).
            handler: An async callable that accepts a payload dict and returns
                     a response dict.
        """
        self._handlers[route] = handler
        logger.debug("ServiceRouter: registered handler for route '%s'", route)

    async def dispatch(self, uri: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch an event to the service specified by the URI.

        Args:
            uri: A ``service://`` URI string, e.g.
                 ``service://automation/escalate_and_log``.
            payload: Arbitrary event payload to pass to the handler.

        Returns:
            The response dict from the handler.

        Raises:
            ValueError: If the URI format is not recognised.
        """
        match = _URI_PATTERN.match(uri)
        if not match:
            raise ValueError(f"ServiceRouter: unrecognised URI format: {uri!r}")

        route = f"{match.group('service')}/{match.group('action')}"
        handler = self._handlers.get(route)
        if handler is None:
            logger.warning("ServiceRouter: no handler registered for route '%s'", route)
            return {
                "status": "unhandled",
                "route": route,
                "timestamp": time.time(),
            }

        logger.debug("ServiceRouter: dispatching to '%s'", route)
        try:
            result = await handler(payload)
            return result
        except Exception as exc:
            logger.error(
                "ServiceRouter: handler '%s' raised %s: %s", route, type(exc).__name__, exc
            )
            return {
                "status": "error",
                "route": route,
                "error": str(exc),
                "timestamp": time.time(),
            }
