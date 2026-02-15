# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Canonical plugin base classes and enums for the OmniCore Engine plugin system.

This module is the single source of truth for:
- ``PluginBase``: Abstract base class that all class-based plugins must inherit from.
- ``PlugInKind``: Unified enum covering plugin kinds for OmniCore, Arbiter/SFE, and Generator.

Other modules (arbiter_plugin_registry, generator plugin stubs, etc.) should import
from here rather than defining their own copies.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, List

logger = logging.getLogger(__name__)


class PlugInKind(str, Enum):
    """Unified plugin kind enum for the entire platform.

    Combines kinds from omnicore_engine, arbiter/SFE, and generator so that
    every module shares a single definition.
    """

    # --- OmniCore core kinds ---
    FIX = "fix"
    CHECK = "check"
    VALIDATION = "validation"
    EXECUTION = "execution"
    CORE_SERVICE = "core_service"
    SCENARIO = "scenario"
    CUSTOM = "custom"
    AGGREGATOR = "aggregator"
    AI_ASSISTANT = "ai_assistant"
    OPTIMIZATION = "optimization"
    MONITORING = "monitoring"
    GROWTH_MANAGER = "growth_manager"
    SIMULATION_RUNNER = "simulation_runner"
    EVOLUTION = "evolution"
    RL_ENVIRONMENT = "rl_environment"

    # --- Arbiter/SFE kinds ---
    WORKFLOW = "workflow"
    VALIDATOR = "validator"
    REPORTER = "reporter"
    ANALYTICS = "analytics"
    STRATEGY = "strategy"
    TRANSFORMER = "transformer"


class PluginBase(ABC):
    """Abstract base class for all class-based plugins.

    Plugins that register with the Arbiter :class:`PluginRegistry` **must**
    inherit from this class and implement every abstract method.

    Lifecycle
    ---------
    ``initialize`` → ``start`` → (running) → ``stop``

    Health & Discovery
    ------------------
    ``health_check`` is called periodically by monitoring subsystems.
    ``get_capabilities`` is used by UIs / orchestration layers.
    """

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the plugin (e.g., setup resources)."""

    @abstractmethod
    async def start(self) -> None:
        """Start the plugin (e.g., begin processing)."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the plugin and clean up resources."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the plugin is healthy."""
        return True

    @abstractmethod
    async def get_capabilities(self) -> List[str]:
        """Return a list of the plugin's capabilities or exposed APIs.

        This enables UIs or orchestration layers to dynamically discover
        the services offered by a plugin.
        """
        return []

    def on_reload(self) -> None:
        """Handle plugin reload event (optional override)."""
        pass
