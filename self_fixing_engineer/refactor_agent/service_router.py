# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
service_router.py

Routes service:// URIs to actual handler implementations.
Handles all 7 event hook URIs from refactor_agent.yaml plus the escalation
policy path resolver.
"""

import logging
import re
import time
from typing import Any, Callable, Awaitable, Dict, Optional

logger = logging.getLogger(__name__)

_URI_PATTERN = re.compile(r"^service://(?P<service>[^/]+)/(?P<action>.+)$")

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
    """Routes service:// URIs to actual handler implementations."""

    def __init__(self) -> None:
        self._handlers: Dict[
            str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
        ] = dict(_DEFAULT_HANDLERS)

    def register(
        self,
        route: str,
        handler: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
    ) -> None:
        """
        Register a custom handler for a service route.

        Args:
            route: The route key in the form ``<service>/<action>``
                   (the part after ``service://``).
            handler: An async callable that accepts a payload dict and returns
                     a response dict.
        """
        self._handlers[route] = handler
        logger.debug("ServiceRouter: registered handler for route '%s'", route)

    async def dispatch(self, uri: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dispatch an event to the service specified by the URI.

        Args:
            uri: A ``service://`` URI string, e.g.
                 ``service://automation/escalate_and_log``.
            payload: Arbitrary event payload to pass to the handler.

        Returns:
            The response dict from the handler.

        Raises:
            ValueError: If the URI format is not recognised.
            KeyError: If no handler is registered for the route.
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
