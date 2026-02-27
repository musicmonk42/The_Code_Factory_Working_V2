# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
healer_agent.py

Mission-critical AI agent for self-healing/auto-fix.
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


def _validate_path(path: str, patterns: List[str]) -> bool:
    """Returns True if path matches any of the whitelist patterns."""
    return any(re.match(p, path) for p in patterns)


def _validate_command(command: str, patterns: List[str]) -> bool:
    """Returns True if command matches any of the whitelist patterns."""
    if not patterns:
        return False
    return any(re.match(p, command) for p in patterns)


class HealerAgent(CrewAgentBase):
    """Mission-critical AI agent for self-healing/auto-fix."""

    WHITELISTED_PATHS = WHITELISTED_PATHS
    WHITELISTED_COMMANDS = WHITELISTED_COMMANDS
    ALLOW_DESTRUCTIVE_ACTIONS = ALLOW_DESTRUCTIVE_ACTIONS

    def __init__(self, name: str = "HealerAgent", config=None, tags=None, metadata=None):
        super().__init__(name=name, config=config, tags=tags, metadata=metadata)

    async def process(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a self-healing/auto-fix task.

        Args:
            task: Dictionary with keys like target_path, fix_type, command, options.

        Returns:
            Dictionary with status, result, and audit_event.
        """
        start_time = time.time()
        target_path = task.get("target_path", "")
        command = task.get("command")
        fix_type = task.get("fix_type", "auto")

        logger.info(
            "HealerAgent.process called",
            extra={"agent": self.name, "target_path": target_path, "fix_type": fix_type},
        )

        # Validate path access
        if target_path:
            if not _validate_path(target_path, self.WHITELISTED_PATHS):
                logger.warning(
                    "HealerAgent: path access denied",
                    extra={"path": target_path},
                )
                return {
                    "status": "error",
                    "error": f"Path '{target_path}' is not in whitelisted paths.",
                    "audit_event": {
                        "agent": self.name,
                        "event": "path_access_denied",
                        "path": target_path,
                        "timestamp": time.time(),
                    },
                }

        # Validate command
        if command:
            if not _validate_command(command, self.WHITELISTED_COMMANDS):
                logger.warning(
                    "HealerAgent: command denied",
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

        # Perform self-healing (placeholder implementation)
        result = {
            "fixed_files": [],
            "fix_type": fix_type,
            "target_path": target_path,
            "issues_resolved": [],
        }

        elapsed = time.time() - start_time
        logger.info(
            "HealerAgent.process completed",
            extra={"agent": self.name, "elapsed": elapsed},
        )

        return {
            "status": "success",
            "result": result,
            "audit_event": {
                "agent": self.name,
                "event": "heal_completed",
                "target_path": target_path,
                "fix_type": fix_type,
                "elapsed": elapsed,
                "timestamp": time.time(),
            },
        }


CrewManager.register_agent_class(HealerAgent)
