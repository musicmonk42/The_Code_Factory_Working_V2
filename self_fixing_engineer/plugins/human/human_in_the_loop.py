# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
human_in_the_loop.py

Human escalation node — mission critical.

Architecture
------------
Creates a proper escalation record with priority queue metadata and SLA
deadlines based on the requested priority level.  Observability delegated to
the shared ``_agent_base`` infrastructure.
"""

from __future__ import annotations

import datetime
import logging
import time
import uuid
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
    _validate_command,
    _validate_path,
    emit_audit_event_safe,
    structured_log,
)

__all__ = ["HumanInTheLoop"]

WHITELISTED_PATHS: List[str] = [r"^\./escalations/.*$"]
WHITELISTED_COMMANDS: List[str] = []
ALLOW_DESTRUCTIVE_ACTIONS: bool = False

_METRICS = AgentMetrics.for_agent("human_in_the_loop")
_AGENT_TYPE = "human_in_the_loop"

# SLA deadlines (hours) by priority
_SLA_HOURS: Dict[str, int] = {
    "critical": 1,
    "high": 4,
    "normal": 24,
    "low": 72,
}

# Thread-safe escalation queue counter for stable ordering within a process.
_queue_counter_lock = __import__("threading").Lock()
_queue_counter: int = 0


def _next_queue_position() -> int:
    """Return the next monotonically-increasing escalation queue position."""
    global _queue_counter
    with _queue_counter_lock:
        _queue_counter += 1
        return _queue_counter


def _compute_sla_deadline(priority: str) -> str:
    """Return an ISO-8601 deadline string for the given *priority*."""
    hours = _SLA_HOURS.get(priority.lower(), 24)
    deadline = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)
    return deadline.isoformat()


class HumanInTheLoop(CrewAgentBase):
    """Human escalation node — mission critical."""

    WHITELISTED_PATHS = WHITELISTED_PATHS
    WHITELISTED_COMMANDS = WHITELISTED_COMMANDS
    ALLOW_DESTRUCTIVE_ACTIONS = ALLOW_DESTRUCTIVE_ACTIONS

    def __init__(
        self,
        name: str = "HumanInTheLoop",
        config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(name=name, config=config, tags=tags, metadata=metadata)

    async def process(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create an escalation record and wait for human response.

        Parameters
        ----------
        task:
            Dictionary with keys ``escalation_path``, ``reason``, ``context``,
            ``priority``, ``destructive``.

        Returns
        -------
        dict
            Keys: ``status``, ``result``, ``audit_event``.  Status is
            ``pending_human`` to signal the pipeline that human input is needed.
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

        escalation_path = task.get("escalation_path")
        reason = task.get("reason", "unspecified")
        context: Dict[str, Any] = task.get("context") or {}
        priority = task.get("priority", "normal")
        command = task.get("command")

        structured_log("HumanInTheLoop.process.start", agent=self.name, reason=reason, priority=priority)

        try:
            if escalation_path and not _validate_path(escalation_path, self.WHITELISTED_PATHS):
                _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="path_denied").inc()
                await emit_audit_event_safe("path_access_denied", {"agent": self.name, "path": escalation_path})
                return {
                    "status": "error",
                    "error": f"Path '{escalation_path}' is not in whitelisted paths.",
                    "result": None,
                    "audit_event": {"agent": self.name, "event": "path_access_denied", "path": escalation_path},
                }

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

            escalation_id = str(uuid.uuid4())
            sla_deadline = _compute_sla_deadline(priority)
            elapsed = time.monotonic() - start_time

            result: Dict[str, Any] = {
                "escalation_id": escalation_id,
                "reason": reason,
                "priority": priority,
                "sla_deadline": sla_deadline,
                "queue_position": _next_queue_position(),
                "context": context,
                "escalation_path": escalation_path,
                "human_response": None,
                "awaiting_response": True,
            }

            _METRICS.calls.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").inc()
            _METRICS.latency.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").observe(elapsed)

            structured_log("HumanInTheLoop.process.complete", agent=self.name, escalation_id=escalation_id, priority=priority, sla_deadline=sla_deadline, elapsed=elapsed)
            await emit_audit_event_safe("human_escalation_created", {"agent": self.name, "escalation_id": escalation_id, "reason": reason, "priority": priority, "sla_deadline": sla_deadline, "elapsed": elapsed})

            return {
                "status": "pending_human",
                "result": result,
                "audit_event": {"agent": self.name, "event": "human_escalation_created", "escalation_id": escalation_id, "reason": reason, "priority": priority, "elapsed": elapsed},
            }

        except Exception as exc:
            elapsed = time.monotonic() - start_time
            if sentry_sdk:
                sentry_sdk.capture_exception(exc)
            _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="error").inc()
            structured_log("HumanInTheLoop.process.error", agent=self.name, error=str(exc))
            await emit_audit_event_safe("escalation_error", {"agent": self.name, "error": str(exc)})
            return {
                "status": "error",
                "error": str(exc),
                "result": None,
                "audit_event": {"agent": self.name, "event": "escalation_error", "error": str(exc)},
            }
        finally:
            if span_ctx:
                try:
                    span_ctx.__exit__(None, None, None)
                except Exception:
                    pass


CrewManager.register_agent_class(HumanInTheLoop)
