# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
smart_refactor_agent.py

AI agent that performs code refactoring using AST analysis and LLM suggestions.
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


WHITELISTED_PATHS: List[str] = [r"^\./src/codebase/.*$", r"^\./tests/.*$"]
WHITELISTED_COMMANDS: List[str] = [
    r"^python(3\.[0-9]+)?$",
    r"^git$",
    r"^pytest(-cov)?$",
    r"^ruff$",
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


class SmartRefactorAgent(CrewAgentBase):
    """AI agent that performs code refactoring using AST analysis and LLM suggestions."""

    WHITELISTED_PATHS = WHITELISTED_PATHS
    WHITELISTED_COMMANDS = WHITELISTED_COMMANDS
    ALLOW_DESTRUCTIVE_ACTIONS = ALLOW_DESTRUCTIVE_ACTIONS

    def __init__(self, name: str = "SmartRefactorAgent", config=None, tags=None, metadata=None):
        super().__init__(name=name, config=config, tags=tags, metadata=metadata)

    async def process(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a refactoring task.

        Args:
            task: Dictionary with keys like codebase_path, mode, options, command.

        Returns:
            Dictionary with status, result, and audit_event.
        """
        start_time = time.time()
        codebase_path = task.get("codebase_path", ".")
        command = task.get("command")

        logger.info(
            "SmartRefactorAgent.process called",
            extra={"agent": self.name, "codebase_path": codebase_path},
        )

        # Validate path access
        if codebase_path and codebase_path != ".":
            if not _validate_path(codebase_path, self.WHITELISTED_PATHS):
                logger.warning(
                    "SmartRefactorAgent: path access denied",
                    extra={"path": codebase_path},
                )
                return {
                    "status": "error",
                    "error": f"Path '{codebase_path}' is not in whitelisted paths.",
                    "audit_event": {
                        "agent": self.name,
                        "event": "path_access_denied",
                        "path": codebase_path,
                        "timestamp": time.time(),
                    },
                }

        # Validate command
        if command:
            if not _validate_command(command, self.WHITELISTED_COMMANDS):
                logger.warning(
                    "SmartRefactorAgent: command denied",
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

        # Perform refactoring (placeholder implementation)
        result = {
            "refactored_files": [],
            "suggestions": [],
            "codebase_path": codebase_path,
        }

        elapsed = time.time() - start_time
        logger.info(
            "SmartRefactorAgent.process completed",
            extra={"agent": self.name, "elapsed": elapsed},
        )

        return {
            "status": "success",
            "result": result,
            "audit_event": {
                "agent": self.name,
                "event": "refactor_completed",
                "codebase_path": codebase_path,
                "elapsed": elapsed,
                "timestamp": time.time(),
            },
        }


CrewManager.register_agent_class(SmartRefactorAgent)
