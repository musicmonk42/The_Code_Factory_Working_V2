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
        CrewManager,
        CrewAgentBase,
        ResourceError,
        PermissionError,
        AgentError,
        structured_log,
        sanitize_dict,
        NAME_REGEX,
        MAX_CONFIG_SIZE,
    )
    
    __all__ = [
        'CrewManager',
        'CrewAgentBase',
        'ResourceError',
        'PermissionError',
        'AgentError',
        'structured_log',
        'sanitize_dict',
        'NAME_REGEX',
        'MAX_CONFIG_SIZE',
    ]
except ImportError as e:
    # Graceful fallback if dependencies are missing
    import warnings
    warnings.warn(f"Agent Orchestration module not fully available: {e}")
    
    CrewManager = None
    CrewAgentBase = None
    ResourceError = Exception
    PermissionError = Exception
    AgentError = Exception
    
    __all__ = []

__version__ = "1.0.0"
