# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
ethics_agent.py

AI agent for ethical/compliance review.

Architecture
------------
Scans source for compliance violations: hardcoded secrets (regex-based),
unsafe ``eval()`` calls, and missing error-handling patterns.
Observability delegated to the shared ``_agent_base`` infrastructure.
"""

from __future__ import annotations

import logging
import re
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
    _validate_command,
    _validate_path,
    emit_audit_event_safe,
    structured_log,
)

__all__ = ["EthicsAgent"]

WHITELISTED_PATHS: List[str] = [r"^\./policies/.*$", r"^\./audit_logs/.*$"]
WHITELISTED_COMMANDS: List[str] = [r"^python(3\.[0-9]+)?$"]
ALLOW_DESTRUCTIVE_ACTIONS: bool = False

_METRICS = AgentMetrics.for_agent("ethics")
_AGENT_TYPE = "ethics"

_SECRET_PATTERN = re.compile(
    r'(?:password|secret|api_key|apikey|token)\s*=\s*["\'][^"\']{4,}["\']',
    re.IGNORECASE,
)
_EVAL_PATTERN = re.compile(r'\beval\s*\(')


def _scan_compliance(source: str) -> List[str]:
    """Return a list of compliance violations found in *source*."""
    violations: List[str] = []
    for match in _SECRET_PATTERN.finditer(source):
        violations.append(f"Potential hardcoded secret near: {match.group()[:40]!r}")
    for match in _EVAL_PATTERN.finditer(source):
        violations.append("Unsafe eval() call detected")
    if "try" not in source and "except" not in source and len(source) > 100:
        violations.append("No error handling (try/except) found in non-trivial source")
    return violations


class EthicsAgent(CrewAgentBase):
    """AI agent for ethical/compliance review."""

    WHITELISTED_PATHS = WHITELISTED_PATHS
    WHITELISTED_COMMANDS = WHITELISTED_COMMANDS
    ALLOW_DESTRUCTIVE_ACTIONS = ALLOW_DESTRUCTIVE_ACTIONS

    def __init__(
        self,
        name: str = "EthicsAgent",
        config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(name=name, config=config, tags=tags, metadata=metadata)

    async def process(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Perform an ethical/compliance review.

        Parameters
        ----------
        task:
            Dictionary with keys ``review_target``, ``source``, ``policy_path``,
            ``command``, ``destructive``.

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

        review_target = task.get("review_target", "")
        policy_path = task.get("policy_path")
        command = task.get("command")

        structured_log("EthicsAgent.process.start", agent=self.name, review_target=review_target)

        try:
            if policy_path and not _validate_path(policy_path, self.WHITELISTED_PATHS):
                _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="path_denied").inc()
                await emit_audit_event_safe("path_access_denied", {"agent": self.name, "path": policy_path})
                return {
                    "status": "error",
                    "error": f"Path '{policy_path}' is not in whitelisted paths.",
                    "result": None,
                    "audit_event": {"agent": self.name, "event": "path_access_denied", "path": policy_path},
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
            violations = _scan_compliance(source) if source else []
            compliance_status = "compliant" if not violations else "violations_found"

            result: Dict[str, Any] = {
                "review_target": review_target,
                "policy_path": policy_path,
                "compliance_status": compliance_status,
                "violations": violations,
                "recommendations": [f"Fix: {v}" for v in violations],
            }

            elapsed = time.monotonic() - start_time
            _METRICS.calls.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").inc()
            _METRICS.latency.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").observe(elapsed)

            structured_log("EthicsAgent.process.complete", agent=self.name, compliance_status=compliance_status, elapsed=elapsed)
            await emit_audit_event_safe("ethics_review_completed", {"agent": self.name, "review_target": review_target, "compliance_status": compliance_status, "elapsed": elapsed})

            return {
                "status": "success",
                "result": result,
                "audit_event": {"agent": self.name, "event": "ethics_review_completed", "review_target": review_target, "elapsed": elapsed},
            }

        except Exception as exc:
            elapsed = time.monotonic() - start_time
            if sentry_sdk:
                sentry_sdk.capture_exception(exc)
            _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="error").inc()
            structured_log("EthicsAgent.process.error", agent=self.name, error=str(exc))
            await emit_audit_event_safe("ethics_review_error", {"agent": self.name, "error": str(exc)})
            return {
                "status": "error",
                "error": str(exc),
                "result": None,
                "audit_event": {"agent": self.name, "event": "ethics_review_error", "error": str(exc)},
            }
        finally:
            if span_ctx:
                try:
                    span_ctx.__exit__(None, None, None)
                except Exception:
                    pass


CrewManager.register_agent_class(EthicsAgent)
