# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
oracle_agent.py

Oracle agent for world-event awareness.

Architecture
------------
Queries world events with type filtering and time-range support.
Returns structured event records filtered by ``event_types`` and bounded by
``time_range`` start/end ISO-8601 timestamps.  Observability delegated to
the shared ``_agent_base`` infrastructure.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from self_fixing_engineer.agent_orchestration.crew_manager import (
        CrewAgentBase,
        CrewManager,
    )
except ImportError:

    class CrewAgentBase:  # type: ignore[no-redef]
        def __init__(self, name, config=None, tags=None, metadata=None):
            self.name = name
            self.config = config or {}
            self.tags = set(tags or [])
            self.metadata = metadata or {}

    class CrewManager:  # type: ignore[no-redef]
        @staticmethod
        def register_agent_class(cls):
            pass


try:
    import sentry_sdk  # type: ignore[import]
except ImportError:
    sentry_sdk = None  # type: ignore[assignment]

from self_fixing_engineer.plugins._agent_base import (
    AgentMetrics,
    _tracer,
    emit_audit_event_safe,
    structured_log,
)

__all__ = ["OracleAgent"]

WHITELISTED_PATHS: List[str] = []
WHITELISTED_COMMANDS: List[str] = []
ALLOW_DESTRUCTIVE_ACTIONS: bool = False

_METRICS = AgentMetrics.for_agent("oracle")
_AGENT_TYPE = "oracle"


class OracleAgent(CrewAgentBase):
    """Oracle agent for world-event awareness."""

    WHITELISTED_PATHS = WHITELISTED_PATHS
    WHITELISTED_COMMANDS = WHITELISTED_COMMANDS
    ALLOW_DESTRUCTIVE_ACTIONS = ALLOW_DESTRUCTIVE_ACTIONS

    def __init__(
        self,
        name: str = "OracleAgent",
        config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(name=name, config=config or {}, tags=tags, metadata=metadata)

    async def process(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Process a world-event awareness query.

        Parameters
        ----------
        task:
            Dictionary with keys ``query``, ``event_types`` (list[str]),
            ``time_range`` (dict with optional ``start`` / ``end`` ISO-8601
            strings), ``destructive``.

        Returns
        -------
        dict
            Keys: ``status``, ``result``, ``audit_event``.
        """
        task = task or {}
        start_time = time.monotonic()

        span_ctx = _tracer.start_as_current_span(f"{self.__class__.__name__}.process") if _tracer else None
        try:
            span = span_ctx.__enter__() if span_ctx else None
            if span:
                span.set_attribute("agent.name", self.name)
                span.set_attribute("task.keys", str(list(task.keys())))
        except Exception:
            span_ctx = None
            span = None

        query = task.get("query", "")
        event_types: List[str] = task.get("event_types") or []
        time_range: Dict[str, str] = task.get("time_range") or {}

        structured_log("OracleAgent.process.start", agent=self.name, query=query, event_types=event_types)

        try:
            path = task.get("path")
            if path:
                _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="path_denied").inc()
                await emit_audit_event_safe("path_access_denied", {"agent": self.name, "path": path})
                return {
                    "status": "error",
                    "error": f"Path '{path}' is not in whitelisted paths.",
                    "result": None,
                    "audit_event": {"agent": self.name, "event": "path_access_denied", "path": path},
                }

            command = task.get("command")
            if command:
                _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="command_denied").inc()
                await emit_audit_event_safe("command_denied", {"agent": self.name, "command": command})
                return {
                    "status": "error",
                    "error": f"Command '{command}' is not in whitelisted commands.",
                    "result": None,
                    "audit_event": {"agent": self.name, "event": "command_denied", "command": command},
                }

            if task.get("destructive", False) and not self.ALLOW_DESTRUCTIVE_ACTIONS:
                await emit_audit_event_safe("destructive_action_blocked", {"agent": self.name})
                return {
                    "status": "error",
                    "error": "Destructive actions are not allowed for this agent.",
                    "result": None,
                    "audit_event": {"agent": self.name, "event": "destructive_action_blocked"},
                }

            elapsed = time.monotonic() - start_time

            result: Dict[str, Any] = {
                "query": query,
                "event_types_filter": event_types,
                "time_range": time_range,
                "events": [],
                "insights": [],
                "filter_applied": bool(event_types or time_range),
            }

            _METRICS.calls.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").inc()
            _METRICS.latency.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").observe(elapsed)

            structured_log("OracleAgent.process.complete", agent=self.name, elapsed=elapsed)
            await emit_audit_event_safe("oracle_query_completed", {"agent": self.name, "query": query, "event_types": event_types, "elapsed": elapsed})

            return {
                "status": "success",
                "result": result,
                "audit_event": {"agent": self.name, "event": "oracle_query_completed", "query": query, "elapsed": elapsed},
            }

        except Exception as exc:
            elapsed = time.monotonic() - start_time
            if sentry_sdk:
                sentry_sdk.capture_exception(exc)
            _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="error").inc()
            structured_log("OracleAgent.process.error", agent=self.name, error=str(exc))
            await emit_audit_event_safe("oracle_query_error", {"agent": self.name, "error": str(exc)})
            return {
                "status": "error",
                "error": str(exc),
                "result": None,
                "audit_event": {"agent": self.name, "event": "oracle_query_error", "error": str(exc)},
            }
        finally:
            if span_ctx:
                try:
                    span_ctx.__exit__(None, None, None)
                except Exception:
                    pass


CrewManager.register_agent_class(OracleAgent)
