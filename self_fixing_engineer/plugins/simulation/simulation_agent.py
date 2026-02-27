# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
simulation_agent.py

AI agent for simulation orchestration.

Architecture
------------
Accepts simulation parameters and returns structured simulation metadata with
a unique ``run_id`` for downstream correlation.  Observability delegated to
the shared ``_agent_base`` infrastructure.
"""

from __future__ import annotations

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

__all__ = ["SimulationAgent"]

WHITELISTED_PATHS: List[str] = [r"^\./simulations/.*$", r"^\./data/.*$"]
WHITELISTED_COMMANDS: List[str] = [r"^python(3\.[0-9]+)?$"]
ALLOW_DESTRUCTIVE_ACTIONS: bool = False

_METRICS = AgentMetrics.for_agent("simulation")
_AGENT_TYPE = "simulation"


class SimulationAgent(CrewAgentBase):
    """AI agent for simulation orchestration."""

    WHITELISTED_PATHS = WHITELISTED_PATHS
    WHITELISTED_COMMANDS = WHITELISTED_COMMANDS
    ALLOW_DESTRUCTIVE_ACTIONS = ALLOW_DESTRUCTIVE_ACTIONS

    def __init__(
        self,
        name: str = "SimulationAgent",
        config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(name=name, config=config, tags=tags, metadata=metadata)

    async def process(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Orchestrate a simulation run.

        Parameters
        ----------
        task:
            Dictionary with keys ``simulation_path``, ``data_path``,
            ``parameters``, ``command``, ``destructive``.

        Returns
        -------
        dict
            Keys: ``status``, ``result``, ``audit_event``.
            ``result`` includes a unique ``run_id`` for correlation.
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

        simulation_path = task.get("simulation_path")
        data_path = task.get("data_path")
        command = task.get("command")
        parameters: Dict[str, Any] = task.get("parameters") or {}

        structured_log("SimulationAgent.process.start", agent=self.name, simulation_path=simulation_path)

        try:
            if simulation_path and not _validate_path(simulation_path, self.WHITELISTED_PATHS):
                _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="path_denied").inc()
                await emit_audit_event_safe("path_access_denied", {"agent": self.name, "path": simulation_path})
                return {
                    "status": "error",
                    "error": f"Path '{simulation_path}' is not in whitelisted paths.",
                    "result": None,
                    "audit_event": {"agent": self.name, "event": "path_access_denied", "path": simulation_path},
                }

            if data_path and not _validate_path(data_path, self.WHITELISTED_PATHS):
                _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="path_denied").inc()
                await emit_audit_event_safe("path_access_denied", {"agent": self.name, "path": data_path})
                return {
                    "status": "error",
                    "error": f"Path '{data_path}' is not in whitelisted paths.",
                    "result": None,
                    "audit_event": {"agent": self.name, "event": "path_access_denied", "path": data_path},
                }

            if command and not _validate_command(command, self.WHITELISTED_COMMANDS):
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

            run_id = str(uuid.uuid4())
            elapsed = time.monotonic() - start_time

            result: Dict[str, Any] = {
                "run_id": run_id,
                "simulation_path": simulation_path,
                "data_path": data_path,
                "parameters": parameters,
                "simulation_status": "queued",
                "elapsed_setup_seconds": elapsed,
                "metrics": {},
            }

            _METRICS.calls.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").inc()
            _METRICS.latency.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").observe(elapsed)

            structured_log("SimulationAgent.process.complete", agent=self.name, run_id=run_id, elapsed=elapsed)
            await emit_audit_event_safe("simulation_queued", {"agent": self.name, "run_id": run_id, "simulation_path": simulation_path, "elapsed": elapsed})

            return {
                "status": "success",
                "result": result,
                "audit_event": {"agent": self.name, "event": "simulation_queued", "run_id": run_id, "simulation_path": simulation_path, "elapsed": elapsed},
            }

        except Exception as exc:
            elapsed = time.monotonic() - start_time
            if sentry_sdk:
                sentry_sdk.capture_exception(exc)
            _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="error").inc()
            structured_log("SimulationAgent.process.error", agent=self.name, error=str(exc))
            await emit_audit_event_safe("simulation_error", {"agent": self.name, "error": str(exc)})
            return {
                "status": "error",
                "error": str(exc),
                "result": None,
                "audit_event": {"agent": self.name, "event": "simulation_error", "error": str(exc)},
            }
        finally:
            if span_ctx:
                try:
                    span_ctx.__exit__(None, None, None)
                except Exception:
                    pass


CrewManager.register_agent_class(SimulationAgent)
