# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Agent Orchestration Module

Provides dynamic crew/agent orchestration for the Self-Fixing Engineer platform.

Key Components:
- CrewManager: Main orchestrator for agent lifecycle management
- CrewAgentBase: Base class for all agents
- Resource management and health monitoring
"""

try:
    from .crew_manager import (
        MAX_CONFIG_SIZE,
        NAME_REGEX,
        AgentError,
        CrewAgentBase,
        CrewManager,
        CrewPermissionError,
        ResourceError,
        sanitize_dict,
        structured_log,
    )

    __all__ = [
        "CrewManager",
        "CrewAgentBase",
        "ResourceError",
        "CrewPermissionError",
        "AgentError",
        "structured_log",
        "sanitize_dict",
        "NAME_REGEX",
        "MAX_CONFIG_SIZE",
    ]
except ImportError as e:
    # Graceful fallback if dependencies are missing
    import warnings

    warnings.warn(f"Agent Orchestration module not fully available: {e}")

    CrewManager = None
    CrewAgentBase = None
    ResourceError = Exception
    CrewPermissionError = Exception
    AgentError = Exception

    __all__ = []

__version__ = "1.0.0"
