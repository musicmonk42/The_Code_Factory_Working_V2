# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
ci_cd_agent.py

Plugin agent that triggers CI/CD pipelines.
"""

import logging
import re
import time
from typing import Any, Dict, List

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


WHITELISTED_PATHS: List[str] = [r"^\./ci_cd_configs/.*$"]
WHITELISTED_COMMANDS: List[str] = [
    r"^kubectl$",
    r"^aws$",
    r"^gcloud$",
    r"^az$",
    r"^jenkins-cli$",
]
ALLOW_DESTRUCTIVE_ACTIONS: bool = True


def _validate_path(path: str, patterns: List[str]) -> bool:
    """Returns True if path matches any of the whitelist patterns."""
    return any(re.match(p, path) for p in patterns)


def _validate_command(command: str, patterns: List[str]) -> bool:
    """Returns True if command matches any of the whitelist patterns."""
    if not patterns:
        return False
    return any(re.match(p, command) for p in patterns)


class CICDAgent(CrewAgentBase):
    """Plugin agent that triggers CI/CD pipelines."""

    WHITELISTED_PATHS = WHITELISTED_PATHS
    WHITELISTED_COMMANDS = WHITELISTED_COMMANDS
    ALLOW_DESTRUCTIVE_ACTIONS = ALLOW_DESTRUCTIVE_ACTIONS

    def __init__(self, name: str = "CICDAgent", config=None, tags=None, metadata=None):
        super().__init__(name=name, config=config, tags=tags, metadata=metadata)

    async def process(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Trigger a CI/CD pipeline.

        Args:
            task: Dictionary with keys like config_path, pipeline_name, command, environment.

        Returns:
            Dictionary with status, result, and audit_event.
        """
        start_time = time.time()
        config_path = task.get("config_path")
        pipeline_name = task.get("pipeline_name", "")
        command = task.get("command")
        environment = task.get("environment", "staging")

        logger.info(
            "CICDAgent.process called",
            extra={"agent": self.name, "pipeline_name": pipeline_name, "environment": environment},
        )

        # Validate config path access
        if config_path:
            if not _validate_path(config_path, self.WHITELISTED_PATHS):
                logger.warning(
                    "CICDAgent: path access denied",
                    extra={"path": config_path},
                )
                return {
                    "status": "error",
                    "error": f"Path '{config_path}' is not in whitelisted paths.",
                    "audit_event": {
                        "agent": self.name,
                        "event": "path_access_denied",
                        "path": config_path,
                        "timestamp": time.time(),
                    },
                }

        # Validate command
        if command:
            if not _validate_command(command, self.WHITELISTED_COMMANDS):
                logger.warning(
                    "CICDAgent: command denied",
                    extra={"command": command},
                )
                return {
                    "status": "error",
                    "error": f"Command '{command}' is not in whitelisted commands.",
                    "audit_event": {
                        "agent": self.name,
                        "event": "command_denied",
                        "command": command,
                        "timestamp": time.time(),
                    },
                }

        # Destructive action check
        is_destructive = task.get("destructive", False)
        if is_destructive and not self.ALLOW_DESTRUCTIVE_ACTIONS:
            return {
                "status": "error",
                "error": "Destructive actions are not allowed for this agent.",
                "audit_event": {
                    "agent": self.name,
                    "event": "destructive_action_blocked",
                    "timestamp": time.time(),
                },
            }

        # Trigger CI/CD pipeline (placeholder implementation)
        result = {
            "pipeline_name": pipeline_name,
            "environment": environment,
            "config_path": config_path,
            "pipeline_run_id": None,
            "pipeline_status": "triggered",
        }

        elapsed = time.time() - start_time
        logger.info(
            "CICDAgent.process completed",
            extra={"agent": self.name, "elapsed": elapsed},
        )

        return {
            "status": "success",
            "result": result,
            "audit_event": {
                "agent": self.name,
                "event": "pipeline_triggered",
                "pipeline_name": pipeline_name,
                "environment": environment,
                "elapsed": elapsed,
                "timestamp": time.time(),
            },
        }


CrewManager.register_agent_class(CICDAgent)
