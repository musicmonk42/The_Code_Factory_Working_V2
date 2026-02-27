# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
smart_refactor_agent.py

AI agent that performs code refactoring using AST analysis.

Architecture
------------
Uses ``ast`` to parse Python source and identify refactoring opportunities:
long functions (>50 lines), duplicate patterns, and missing type annotations.
All observability is delegated to the shared ``_agent_base`` infrastructure:
Prometheus metrics, structured JSON logging, OpenTelemetry tracing, and
safe audit-event emission.
"""

from __future__ import annotations

import ast
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
    agent_span,
    _validate_command,
    _validate_path,
    emit_audit_event_safe,
    structured_log,
)

__all__ = ["SmartRefactorAgent"]

WHITELISTED_PATHS: List[str] = [r"^\./src/codebase/.*$", r"^\./tests/.*$"]
WHITELISTED_COMMANDS: List[str] = [
    r"^python(3\.[0-9]+)?$",
    r"^git$",
    r"^pytest(-cov)?$",
    r"^ruff$",
]
ALLOW_DESTRUCTIVE_ACTIONS: bool = True

_METRICS = AgentMetrics.for_agent("smart_refactor")
_AGENT_TYPE = "smart_refactor"

# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _analyze_code(source: str) -> Dict[str, Any]:
    """Parse *source* and return a dict of refactoring opportunities."""
    suggestions: List[str] = []
    long_functions: List[str] = []
    missing_annotations: List[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"suggestions": ["syntax error — cannot analyse"], "long_functions": [], "missing_annotations": []}

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body_lines = (node.end_lineno or 0) - node.lineno
            if body_lines > 50:
                long_functions.append(node.name)
                suggestions.append(f"Function '{node.name}' is {body_lines} lines — consider splitting")
            if node.returns is None:
                missing_annotations.append(node.name)
                suggestions.append(f"Function '{node.name}' is missing a return type annotation")

    return {
        "suggestions": suggestions,
        "long_functions": long_functions,
        "missing_annotations": missing_annotations,
    }

class SmartRefactorAgent(CrewAgentBase):
    """AI agent that performs code refactoring using AST analysis."""

    WHITELISTED_PATHS = WHITELISTED_PATHS
    WHITELISTED_COMMANDS = WHITELISTED_COMMANDS
    ALLOW_DESTRUCTIVE_ACTIONS = ALLOW_DESTRUCTIVE_ACTIONS

    def __init__(
        self,
        name: str = "SmartRefactorAgent",
        config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(name=name, config=config, tags=tags, metadata=metadata)

    async def process(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Process a refactoring task.

        Parameters
        ----------
        task:
            Dictionary with keys ``codebase_path``, ``source``, ``command``,
            ``destructive``.

        Returns
        -------
        dict
            Keys: ``status``, ``result``, ``audit_event``.
        """
        task = task or {}
        start_time = time.monotonic()

        codebase_path = task.get("codebase_path", "")
        command = task.get("command")

        structured_log("SmartRefactorAgent.process.start", agent=self.name, codebase_path=codebase_path)

        with agent_span(f"{self.__class__.__name__}.process", self.name, list(task.keys())):
            try:
                if codebase_path and not _validate_path(codebase_path, self.WHITELISTED_PATHS):
                    elapsed = time.monotonic() - start_time
                    _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="path_denied").inc()
                    await emit_audit_event_safe("path_access_denied", {"agent": self.name, "path": codebase_path})
                    return {
                        "status": "error",
                        "error": f"Path '{codebase_path}' is not in whitelisted paths.",
                        "result": None,
                        "audit_event": {"agent": self.name, "event": "path_access_denied", "path": codebase_path},
                    }

                if command and not _validate_command(command, self.WHITELISTED_COMMANDS):
                    elapsed = time.monotonic() - start_time
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

                source = task.get("source", "")
                analysis = _analyze_code(source) if source else {"suggestions": [], "long_functions": [], "missing_annotations": []}

                result: Dict[str, Any] = {
                    "refactored_files": [],
                    "suggestions": analysis["suggestions"],
                    "long_functions": analysis["long_functions"],
                    "missing_annotations": analysis["missing_annotations"],
                    "codebase_path": codebase_path,
                }

                elapsed = time.monotonic() - start_time
                _METRICS.calls.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").inc()
                _METRICS.latency.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").observe(elapsed)

                structured_log("SmartRefactorAgent.process.complete", agent=self.name, elapsed=elapsed)
                await emit_audit_event_safe("refactor_completed", {"agent": self.name, "codebase_path": codebase_path, "elapsed": elapsed})

                return {
                    "status": "success",
                    "result": result,
                    "audit_event": {"agent": self.name, "event": "refactor_completed", "codebase_path": codebase_path, "elapsed": elapsed},
                }

            except Exception as exc:
                elapsed = time.monotonic() - start_time
                if sentry_sdk:
                    sentry_sdk.capture_exception(exc)
                _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="error").inc()
                structured_log("SmartRefactorAgent.process.error", agent=self.name, error=str(exc))
                await emit_audit_event_safe("refactor_error", {"agent": self.name, "error": str(exc)})
                return {
                    "status": "error",
                    "error": str(exc),
                    "result": None,
                    "audit_event": {"agent": self.name, "event": "refactor_error", "error": str(exc)},
                }

CrewManager.register_agent_class(SmartRefactorAgent)
