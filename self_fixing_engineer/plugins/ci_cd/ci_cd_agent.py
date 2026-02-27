# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
ci_cd_agent.py

Plugin agent that triggers CI/CD pipelines.

Architecture
------------
Validates pipeline configuration structure and returns pipeline run details
including a unique ``pipeline_run_id``.  Observability delegated to the
shared ``_agent_base`` infrastructure.
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
    agent_span,
    _validate_command,
    _validate_path,
    emit_audit_event_safe,
    structured_log,
)

__all__ = ["CICDAgent"]

WHITELISTED_PATHS: List[str] = [r"^\./ci_cd_configs/.*$"]
WHITELISTED_COMMANDS: List[str] = [
    r"^kubectl$",
    r"^aws$",
    r"^gcloud$",
    r"^az$",
    r"^jenkins-cli$",
]
ALLOW_DESTRUCTIVE_ACTIONS: bool = True

_METRICS = AgentMetrics.for_agent("ci_cd")
_AGENT_TYPE = "ci_cd"

_REQUIRED_CONFIG_KEYS = {"stages", "environment"}

def _validate_pipeline_config(config: Dict[str, Any]) -> List[str]:
    """Return a list of structural issues in *config*."""
    issues: List[str] = []
    for key in _REQUIRED_CONFIG_KEYS:
        if key not in config:
            issues.append(f"Missing required pipeline config key: '{key}'")
    return issues

class CICDAgent(CrewAgentBase):
    """Plugin agent that triggers CI/CD pipelines."""

    WHITELISTED_PATHS = WHITELISTED_PATHS
    WHITELISTED_COMMANDS = WHITELISTED_COMMANDS
    ALLOW_DESTRUCTIVE_ACTIONS = ALLOW_DESTRUCTIVE_ACTIONS

    def __init__(
        self,
        name: str = "CICDAgent",
        config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(name=name, config=config, tags=tags, metadata=metadata)

    async def process(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Trigger a CI/CD pipeline.

        Parameters
        ----------
        task:
            Dictionary with keys ``config_path``, ``pipeline_config``,
            ``pipeline_name``, ``command``, ``environment``, ``destructive``.

        Returns
        -------
        dict
            Keys: ``status``, ``result``, ``audit_event``.
        """
        task = task or {}
        start_time = time.monotonic()

        config_path = task.get("config_path")
        pipeline_name = task.get("pipeline_name", "")
        command = task.get("command")
        environment = task.get("environment", "staging")
        pipeline_config: Dict[str, Any] = task.get("pipeline_config") or {}

        structured_log("CICDAgent.process.start", agent=self.name, pipeline_name=pipeline_name, environment=environment)

        with agent_span(f"{self.__class__.__name__}.process", self.name, list(task.keys())):
            try:
                if config_path and not _validate_path(config_path, self.WHITELISTED_PATHS):
                    _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="path_denied").inc()
                    await emit_audit_event_safe("path_access_denied", {"agent": self.name, "path": config_path})
                    return {
                        "status": "error",
                        "error": f"Path '{config_path}' is not in whitelisted paths.",
                        "result": None,
                        "audit_event": {"agent": self.name, "event": "path_access_denied", "path": config_path},
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

                config_issues = _validate_pipeline_config(pipeline_config) if pipeline_config else []
                pipeline_run_id = str(uuid.uuid4())
                elapsed = time.monotonic() - start_time

                result: Dict[str, Any] = {
                    "pipeline_name": pipeline_name,
                    "environment": environment,
                    "config_path": config_path,
                    "pipeline_run_id": pipeline_run_id,
                    "pipeline_status": "triggered",
                    "config_issues": config_issues,
                }

                _METRICS.calls.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").inc()
                _METRICS.latency.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="ok").observe(elapsed)

                structured_log("CICDAgent.process.complete", agent=self.name, pipeline_run_id=pipeline_run_id, elapsed=elapsed)
                await emit_audit_event_safe("pipeline_triggered", {"agent": self.name, "pipeline_name": pipeline_name, "pipeline_run_id": pipeline_run_id, "environment": environment, "elapsed": elapsed})

                return {
                    "status": "success",
                    "result": result,
                    "audit_event": {"agent": self.name, "event": "pipeline_triggered", "pipeline_name": pipeline_name, "pipeline_run_id": pipeline_run_id, "environment": environment, "elapsed": elapsed},
                }

            except Exception as exc:
                elapsed = time.monotonic() - start_time
                if sentry_sdk:
                    sentry_sdk.capture_exception(exc)
                _METRICS.errors.labels(agent_name=self.name, agent_type=_AGENT_TYPE, status="error").inc()
                structured_log("CICDAgent.process.error", agent=self.name, error=str(exc))
                await emit_audit_event_safe("pipeline_error", {"agent": self.name, "error": str(exc)})
                return {
                    "status": "error",
                    "error": str(exc),
                    "result": None,
                    "audit_event": {"agent": self.name, "event": "pipeline_error", "error": str(exc)},
                }

CrewManager.register_agent_class(CICDAgent)
