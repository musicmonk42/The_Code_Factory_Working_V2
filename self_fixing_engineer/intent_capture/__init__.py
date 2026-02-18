# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# intent_capture/__init__.py
"""
Intent Capture Agent Package

This package provides:
- CollaborativeAgent: Main agent for intent capture and requirements generation
- get_or_create_agent: Factory function for agent instances
- AgentResponse: Response model for agent predictions
- create_app: FastAPI application factory
- Session management: save_session, load_session, list_sessions, delete_session
- GlobalConfigManager: Configuration management

Performance Considerations:
    - Defers all heavy imports until actually needed
    - Uses lazy loading pattern similar to arbiter package
"""

import os
import sys

# Detect pytest collection mode to avoid expensive initialization
PYTEST_COLLECTING = bool(os.getenv("PYTEST_COLLECTING"))

# Track if we've already loaded components
_components_loaded = False
_components_loading = False

# Components that support lazy loading via __getattr__
_LAZY_COMPONENT_NAMES = {
    "CollaborativeAgent",
    "get_or_create_agent",
    "AgentResponse",
    "create_app",
    "save_session",
    "load_session",
    "list_sessions",
    "delete_session",
    "GlobalConfigManager",
}

__version__ = "1.0.0"


def _load_components():
    """Load all components lazily. Called on first access."""
    global CollaborativeAgent
    global get_or_create_agent
    global AgentResponse
    global create_app
    global save_session
    global load_session
    global list_sessions
    global delete_session
    global GlobalConfigManager
    global _components_loaded
    global _components_loading
    
    if _components_loaded or _components_loading:
        return
    
    _components_loading = True
    
    try:
        # Import agent_core components
        try:
            from .agent_core import (
                AgentResponse as _AgentResponse,
                CollaborativeAgent as _CollaborativeAgent,
                get_or_create_agent as _get_or_create_agent,
            )
            CollaborativeAgent = _CollaborativeAgent
            get_or_create_agent = _get_or_create_agent
            AgentResponse = _AgentResponse
        except ImportError as e:
            import logging
            logging.getLogger(__name__).debug(f"Failed to import agent_core components: {e}")
        
        # Import API components
        try:
            from .api import create_app as _create_app
            create_app = _create_app
        except ImportError as e:
            import logging
            logging.getLogger(__name__).debug(f"Failed to import api components: {e}")
        
        # Import session management components
        try:
            from .session import (
                delete_session as _delete_session,
                list_sessions as _list_sessions,
                load_session as _load_session,
                save_session as _save_session,
            )
            save_session = _save_session
            load_session = _load_session
            list_sessions = _list_sessions
            delete_session = _delete_session
        except ImportError as e:
            import logging
            logging.getLogger(__name__).debug(f"Failed to import session components: {e}")
        
        # Import config components
        try:
            from .config import GlobalConfigManager as _GlobalConfigManager
            GlobalConfigManager = _GlobalConfigManager
        except ImportError as e:
            import logging
            logging.getLogger(__name__).debug(f"Failed to import config components: {e}")
        
        _components_loaded = True
    finally:
        _components_loading = False


# Only load components if not in pytest collection mode
if not PYTEST_COLLECTING:
    _load_components()


# Export all main components
__all__ = [
    "CollaborativeAgent",
    "get_or_create_agent",
    "AgentResponse",
    "create_app",
    "save_session",
    "load_session",
    "list_sessions",
    "delete_session",
    "GlobalConfigManager",
]


def __getattr__(name):
    """
    Lazy loading of components to avoid expensive imports during test collection.
    This allows 'from intent_capture import CollaborativeAgent' to work while deferring
    actual import until runtime.
    """
    if name in _LAZY_COMPONENT_NAMES:
        # Load components on first access
        _load_components()
        # Return the now-loaded component
        result = globals().get(name)
        if result is not None:
            return result
        raise ImportError(f"Cannot import name '{name}' from 'intent_capture'")
    
    raise AttributeError(f"module 'intent_capture' has no attribute '{name}'")
