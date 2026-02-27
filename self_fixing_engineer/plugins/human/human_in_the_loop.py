# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
human_in_the_loop.py

Human escalation node - mission critical.
"""

import logging
import re
import time
import uuid
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


WHITELISTED_PATHS: List[str] = [r"^\./escalations/.*$"]
WHITELISTED_COMMANDS: List[str] = []
ALLOW_DESTRUCTIVE_ACTIONS: bool = False


def _validate_path(path: str, patterns: List[str]) -> bool:
    """Returns True if path matches any of the whitelist patterns."""
    return any(re.match(p, path) for p in patterns)


def _validate_command(command: str, patterns: List[str]) -> bool:
    """Returns True if command matches any of the whitelist patterns."""
    if not patterns:
        return False
    return any(re.match(p, command) for p in patterns)


class HumanInTheLoop(CrewAgentBase):
    """Human escalation node - mission critical."""

    WHITELISTED_PATHS = WHITELISTED_PATHS
    WHITELISTED_COMMANDS = WHITELISTED_COMMANDS
    ALLOW_DESTRUCTIVE_ACTIONS = ALLOW_DESTRUCTIVE_ACTIONS

    def __init__(self, name: str = "HumanInTheLoop", config=None, tags=None, metadata=None):
        super().__init__(name=name, config=config, tags=tags, metadata=metadata)

    async def process(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create an escalation record and wait for human response.

        Args:
            task: Dictionary with keys like escalation_path, reason, context, priority.

        Returns:
            Dictionary with status, result, and audit_event.
        """
        start_time = time.time()
        escalation_path = task.get("escalation_path")
        reason = task.get("reason", "unspecified")
        context = task.get("context", {})
        priority = task.get("priority", "normal")

        logger.info(
            "HumanInTheLoop.process called",
            extra={"agent": self.name, "reason": reason, "priority": priority},
        )

        # Validate escalation path access
        if escalation_path:
            if not _validate_path(escalation_path, self.WHITELISTED_PATHS):
                logger.warning(
                    "HumanInTheLoop: path access denied",
                    extra={"path": escalation_path},
                )
                return {
                    "status": "error",
                    "error": f"Path '{escalation_path}' is not in whitelisted paths.",
                    "audit_event": {
                        "agent": self.name,
                        "event": "path_access_denied",
                        "path": escalation_path,
                        "timestamp": time.time(),
                    },
                }

        # No commands allowed — deny any command unconditionally
        command = task.get("command")
        if command:
            logger.warning(
                "HumanInTheLoop: command denied (no commands permitted)",
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

        # Destructive action check (read-only agent)
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

        # Create escalation record and simulate waiting for human response
        escalation_id = str(uuid.uuid4())
        logger.info(
            "HumanInTheLoop: escalation record created, awaiting human response",
            extra={"agent": self.name, "escalation_id": escalation_id, "priority": priority},
        )

        result = {
            "escalation_id": escalation_id,
            "reason": reason,
            "priority": priority,
            "context": context,
            "escalation_path": escalation_path,
            # Placeholder: in production this would block until a human responds
            "human_response": None,
            "awaiting_response": True,
        }

        elapsed = time.time() - start_time
        logger.info(
            "HumanInTheLoop.process completed",
            extra={"agent": self.name, "escalation_id": escalation_id, "elapsed": elapsed},
        )

        return {
            "status": "pending_human",
            "result": result,
            "audit_event": {
                "agent": self.name,
                "event": "human_escalation_created",
                "escalation_id": escalation_id,
                "reason": reason,
                "priority": priority,
                "elapsed": elapsed,
                "timestamp": time.time(),
            },
        }


CrewManager.register_agent_class(HumanInTheLoop)
