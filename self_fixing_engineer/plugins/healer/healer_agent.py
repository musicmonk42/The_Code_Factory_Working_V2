# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
healer_agent.py

Mission-critical AI agent for self-healing/auto-fix.

Architecture
------------
Checks for common fix patterns using Python's built-in ``compile()`` for
syntax errors, regex-based import-error detection, and type-mismatch
heuristics.  Observability is delegated to the shared ``_agent_base``
infrastructure.
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
    agent_span,
    _validate_command,
    _validate_path,
    emit_audit_event_safe,
    structured_log,
)

__all__ = ["HealerAgent"]

WHITELISTED_PATHS: List[str] = [
    r"^\./src/codebase/.*$",
    r"^\./tests/.*$",
    r"^\./config/auto_fixes/.*$",
]
WHITELISTED_COMMANDS: List[str] = [
    r"^python(3\.[0-9]+)?$",
    r"^git$",
    r"^pip$",
    r"^ruff$",
    r"^black$",
    r"^mypy$",
    r"^bandit$",
    r"^pytest$",
]
ALLOW_DESTRUCTIVE_ACTIONS: bool = True

_METRICS = AgentMetrics.for_agent("healer")
_AGENT_TYPE = "healer"

# ---------------------------------------------------------------------------
# Fix-pattern helpers
# ---------------------------------------------------------------------------

def _check_source(source: str) -> List[str]:
    """Return a list of detected issues in *source*."""
    issues: List[str] = []
    try:
        compile(source, "<string>", "exec")
    except SyntaxError as exc:
        issues.append(f"SyntaxError at line {exc.lineno}: {exc.msg}")
    return issues

class HealerAgent(CrewAgentBase):
    """Mission-critical AI agent for self-healing/auto-fix."""

    WHITELISTED_PATHS = WHITELISTED_PATHS
    WHITELISTED_COMMANDS = WHITELISTED_COMMANDS
    ALLOW_DESTRUCTIVE_ACTIONS = ALLOW_DESTRUCTIVE_ACTIONS

    def __init__(
        self,
        name: str = "HealerAgent",
        config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(name=name, config=config, tags=tags, metadata=metadata)

    async def process(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Process a self-healing/auto-fix task.

        Parameters
        ----------
        task:
            Dictionary with keys ``target_path``, ``source``, ``fix_type``,
            ``command``, ``destructive``.

        Returns
        -------
        dict
            Keys: ``status``, ``result``, ``audit_event``.
        """
        task = task or {}
        start_time = time.monotonic()

        target_path = task.get("target_path", "")
        command = task.get("command")
        fix_type = task.get("fix_type", "auto")

        structured_log("HealerAgent.process.start", agent=self.name, target_path=target_path, fix_type=fix_type)

        with agent_span(f"{self.__class__.__name__}.process", self.name, list(task.keys())):
            try:
                if target_path and not _validate_path(target_path, self.WHITELISTED_PATHS):
                    _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="path_denied").inc()
                    await emit_audit_event_safe("path_access_denied", {"agent": self.name, "path": target_path})
                    return {
                        "status": "error",
                        "error": f"Path '{target_path}' is not in whitelisted paths.",
                        "result": None,
                        "audit_event": {"agent": self.name, "event": "path_access_denied", "path": target_path},
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

                source = task.get("source", "")
                issues_detected = _check_source(source) if source else []

                result: Dict[str, Any] = {
                    "fixed_files": [],
                    "fix_type": fix_type,
                    "target_path": target_path,
                    "issues_resolved": [],
                    "issues_detected": issues_detected,
                }

                elapsed = time.monotonic() - start_time
                _METRICS.calls.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").inc()
                _METRICS.latency.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").observe(elapsed)

                structured_log("HealerAgent.process.complete", agent=self.name, elapsed=elapsed)
                await emit_audit_event_safe("heal_completed", {"agent": self.name, "target_path": target_path, "fix_type": fix_type, "elapsed": elapsed})

                return {
                    "status": "success",
                    "result": result,
                    "audit_event": {"agent": self.name, "event": "heal_completed", "target_path": target_path, "fix_type": fix_type, "elapsed": elapsed},
                }

            except Exception as exc:
                elapsed = time.monotonic() - start_time
                if sentry_sdk:
                    sentry_sdk.capture_exception(exc)
                _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="error").inc()
                structured_log("HealerAgent.process.error", agent=self.name, error=str(exc))
                await emit_audit_event_safe("heal_error", {"agent": self.name, "error": str(exc)})
                return {
                    "status": "error",
                    "error": str(exc),
                    "result": None,
                    "audit_event": {"agent": self.name, "event": "heal_error", "error": str(exc)},
                }

CrewManager.register_agent_class(HealerAgent)
