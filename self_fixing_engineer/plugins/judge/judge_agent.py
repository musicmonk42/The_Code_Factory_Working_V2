# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
judge_agent.py

AI agent that evaluates code quality, produces scores and feedback.

Architecture
------------
Uses ``ast.parse`` to count complexity proxy metrics (branch count as a
cyclomatic complexity approximation) and returns a quality score in [0.0, 1.0].
Observability delegated to the shared ``_agent_base`` infrastructure.
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

__all__ = ["JudgeAgent"]

WHITELISTED_PATHS: List[str] = [r"^\./reports/.*$"]
WHITELISTED_COMMANDS: List[str] = [r"^python(3\.[0-9]+)?$"]
ALLOW_DESTRUCTIVE_ACTIONS: bool = False

_METRICS = AgentMetrics.for_agent("judge")
_AGENT_TYPE = "judge"

# Branch node types used for cyclomatic complexity proxy
_BRANCH_NODES = (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.With, ast.Assert)

def _compute_complexity_score(source: str) -> float:
    """Return a quality score 0.0–1.0 based on cyclomatic complexity proxy.

    Lower branch count → higher score.  A complexity of 0 returns 1.0; each
    branch reduces the score by 0.02 down to a floor of 0.0.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0.0
    branch_count = sum(1 for node in ast.walk(tree) if isinstance(node, _BRANCH_NODES))
    return max(0.0, 1.0 - branch_count * 0.02)

class JudgeAgent(CrewAgentBase):
    """AI agent that evaluates code quality, produces scores and feedback."""

    WHITELISTED_PATHS = WHITELISTED_PATHS
    WHITELISTED_COMMANDS = WHITELISTED_COMMANDS
    ALLOW_DESTRUCTIVE_ACTIONS = ALLOW_DESTRUCTIVE_ACTIONS

    def __init__(
        self,
        name: str = "JudgeAgent",
        config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(name=name, config=config, tags=tags, metadata=metadata)

    async def process(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Evaluate code quality and produce a score and feedback.

        Parameters
        ----------
        task:
            Dictionary with keys ``code_path``, ``source``, ``report_path``,
            ``command``, ``destructive``.

        Returns
        -------
        dict
            Keys: ``status``, ``result``, ``audit_event``.
        """
        task = task or {}
        start_time = time.monotonic()

        code_path = task.get("code_path", ".")
        report_path = task.get("report_path")
        command = task.get("command")

        structured_log("JudgeAgent.process.start", agent=self.name, code_path=code_path)

        with agent_span(f"{self.__class__.__name__}.process", self.name, list(task.keys())):
            try:
                if report_path and not _validate_path(report_path, self.WHITELISTED_PATHS):
                    _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="path_denied").inc()
                    await emit_audit_event_safe("path_access_denied", {"agent": self.name, "path": report_path})
                    return {
                        "status": "error",
                        "error": f"Path '{report_path}' is not in whitelisted paths.",
                        "result": None,
                        "audit_event": {"agent": self.name, "event": "path_access_denied", "path": report_path},
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
                score = _compute_complexity_score(source) if source else 0.0

                result: Dict[str, Any] = {
                    "score": score,
                    "feedback": [f"Complexity score: {score:.2f}"],
                    "code_path": code_path,
                    "report_path": report_path,
                }

                elapsed = time.monotonic() - start_time
                _METRICS.calls.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").inc()
                _METRICS.latency.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").observe(elapsed)

                structured_log("JudgeAgent.process.complete", agent=self.name, score=score, elapsed=elapsed)
                await emit_audit_event_safe("evaluation_completed", {"agent": self.name, "code_path": code_path, "score": score, "elapsed": elapsed})

                return {
                    "status": "success",
                    "result": result,
                    "audit_event": {"agent": self.name, "event": "evaluation_completed", "code_path": code_path, "score": score, "elapsed": elapsed},
                }

            except Exception as exc:
                elapsed = time.monotonic() - start_time
                if sentry_sdk:
                    sentry_sdk.capture_exception(exc)
                _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="error").inc()
                structured_log("JudgeAgent.process.error", agent=self.name, error=str(exc))
                await emit_audit_event_safe("evaluation_error", {"agent": self.name, "error": str(exc)})
                return {
                    "status": "error",
                    "error": str(exc),
                    "result": None,
                    "audit_event": {"agent": self.name, "event": "evaluation_error", "error": str(exc)},
                }

CrewManager.register_agent_class(JudgeAgent)
