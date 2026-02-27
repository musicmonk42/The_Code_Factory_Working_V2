# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
simulation_agent.py

AI agent for simulation orchestration.
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


WHITELISTED_PATHS: List[str] = [r"^\./simulations/.*$", r"^\./data/.*$"]
WHITELISTED_COMMANDS: List[str] = [r"^python(3\.[0-9]+)?$"]
ALLOW_DESTRUCTIVE_ACTIONS: bool = False


def _validate_path(path: str, patterns: List[str]) -> bool:
    """Returns True if path matches any of the whitelist patterns."""
    return any(re.match(p, path) for p in patterns)


def _validate_command(command: str, patterns: List[str]) -> bool:
    """Returns True if command matches any of the whitelist patterns."""
    if not patterns:
        return False
    return any(re.match(p, command) for p in patterns)


class SimulationAgent(CrewAgentBase):
    """AI agent for simulation orchestration."""

    WHITELISTED_PATHS = WHITELISTED_PATHS
    WHITELISTED_COMMANDS = WHITELISTED_COMMANDS
    ALLOW_DESTRUCTIVE_ACTIONS = ALLOW_DESTRUCTIVE_ACTIONS

    def __init__(self, name: str = "SimulationAgent", config=None, tags=None, metadata=None):
        super().__init__(name=name, config=config, tags=tags, metadata=metadata)

    async def process(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Orchestrate a simulation run.

        Args:
            task: Dictionary with keys like simulation_path, data_path, parameters, command.

        Returns:
            Dictionary with status, result, and audit_event.
        """
        start_time = time.time()
        simulation_path = task.get("simulation_path")
        data_path = task.get("data_path")
        command = task.get("command")
        parameters = task.get("parameters", {})

        logger.info(
            "SimulationAgent.process called",
            extra={"agent": self.name, "simulation_path": simulation_path},
        )

        # Validate simulation path access
        if simulation_path:
            if not _validate_path(simulation_path, self.WHITELISTED_PATHS):
                logger.warning(
                    "SimulationAgent: path access denied",
                    extra={"path": simulation_path},
                )
                return {
                    "status": "error",
                    "error": f"Path '{simulation_path}' is not in whitelisted paths.",
                    "audit_event": {
                        "agent": self.name,
                        "event": "path_access_denied",
                        "path": simulation_path,
                        "timestamp": time.time(),
                    },
                }

        # Validate data path access
        if data_path:
            if not _validate_path(data_path, self.WHITELISTED_PATHS):
                logger.warning(
                    "SimulationAgent: path access denied",
                    extra={"path": data_path},
                )
                return {
                    "status": "error",
                    "error": f"Path '{data_path}' is not in whitelisted paths.",
                    "audit_event": {
                        "agent": self.name,
                        "event": "path_access_denied",
                        "path": data_path,
                        "timestamp": time.time(),
                    },
                }

        # Validate command
        if command:
            if not _validate_command(command, self.WHITELISTED_COMMANDS):
                logger.warning(
                    "SimulationAgent: command denied",
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

        # Run simulation (placeholder implementation)
        result = {
            "simulation_path": simulation_path,
            "data_path": data_path,
            "parameters": parameters,
            "simulation_output": {},
            "metrics": {},
        }

        elapsed = time.time() - start_time
        logger.info(
            "SimulationAgent.process completed",
            extra={"agent": self.name, "elapsed": elapsed},
        )

        return {
            "status": "success",
            "result": result,
            "audit_event": {
                "agent": self.name,
                "event": "simulation_completed",
                "simulation_path": simulation_path,
                "elapsed": elapsed,
                "timestamp": time.time(),
            },
        }


CrewManager.register_agent_class(SimulationAgent)
