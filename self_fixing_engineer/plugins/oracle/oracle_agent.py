# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
oracle_agent.py

Oracle agent for world-event awareness.
"""

import logging
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


WHITELISTED_PATHS: List[str] = []
WHITELISTED_COMMANDS: List[str] = []
ALLOW_DESTRUCTIVE_ACTIONS: bool = False


class OracleAgent(CrewAgentBase):
    """Oracle agent for world-event awareness."""

    WHITELISTED_PATHS = WHITELISTED_PATHS
    WHITELISTED_COMMANDS = WHITELISTED_COMMANDS
    ALLOW_DESTRUCTIVE_ACTIONS = ALLOW_DESTRUCTIVE_ACTIONS

    def __init__(self, name: str = "OracleAgent", config=None, tags=None, metadata=None):
        super().__init__(name=name, config=config, tags=tags, metadata=metadata)

    async def process(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a world-event awareness query.

        Args:
            task: Dictionary with keys like query, event_types, time_range.

        Returns:
            Dictionary with status, result, and audit_event.
        """
        start_time = time.time()
        query = task.get("query", "")
        event_types = task.get("event_types", [])
        time_range = task.get("time_range", {})

        logger.info(
            "OracleAgent.process called",
            extra={"agent": self.name, "query": query},
        )

        # No filesystem access — deny any path unconditionally
        path = task.get("path")
        if path:
            logger.warning(
                "OracleAgent: path access denied (no filesystem access permitted)",
                extra={"path": path},
            )
            return {
                "status": "error",
                "error": f"Path '{path}' is not in whitelisted paths.",
                "audit_event": {
                    "agent": self.name,
                    "event": "path_access_denied",
                    "path": path,
                    "timestamp": time.time(),
                },
            }

        # No commands allowed — deny any command unconditionally
        command = task.get("command")
        if command:
            logger.warning(
                "OracleAgent: command denied (no commands permitted)",
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

        # World-event awareness query (placeholder implementation)
        result = {
            "query": query,
            "event_types": event_types,
            "time_range": time_range,
            "events": [],
            "insights": [],
        }

        elapsed = time.time() - start_time
        logger.info(
            "OracleAgent.process completed",
            extra={"agent": self.name, "elapsed": elapsed},
        )

        return {
            "status": "success",
            "result": result,
            "audit_event": {
                "agent": self.name,
                "event": "oracle_query_completed",
                "query": query,
                "elapsed": elapsed,
                "timestamp": time.time(),
            },
        }


CrewManager.register_agent_class(OracleAgent)
