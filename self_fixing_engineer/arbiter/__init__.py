"""
Arbiter package - Core components for the Self-Fixing Engineer platform.

This package provides:
- Arbiter: Main orchestrator for self-fixing engineering workflows
- ArbiterArena: Multi-agent collaboration environment
- FeedbackManager: Feedback collection and metrics management
- HumanInLoop: Human-in-the-loop approval workflows
- HumanInLoopConfig: Configuration for human-in-the-loop features
- ArbiterConfig: Core configuration management

Performance Considerations:
    - Defers all heavy imports until actually needed
    - Skips initialization entirely during pytest collection phase
    - Uses environment variables PYTEST_CURRENT_TEST and PYTEST_COLLECTING
      to detect test collection mode
"""

import os
import sys

# Detect pytest collection mode to avoid expensive initialization
# This prevents CPU timeouts during pytest --collect-only
PYTEST_COLLECTING = bool(os.getenv("PYTEST_COLLECTING"))

# Module-level variables for lazy loading
arbiter = None
Arbiter = None
ArbiterArena = None
FeedbackManager = None
ArbiterConfig = None

# Track if we've already loaded components
_components_loaded = False

# Components that support lazy loading via __getattr__
_LAZY_COMPONENT_NAMES = {"arbiter", "Arbiter", "ArbiterArena", "FeedbackManager", "ArbiterConfig"}


def _load_components():
    """Load all components lazily. Called on first access."""
    global arbiter
    global Arbiter
    global ArbiterArena
    global FeedbackManager
    global ArbiterConfig
    global _components_loaded
    
    if _components_loaded:
        return
    
    _components_loaded = True
    
    # Import and expose main components that tests and other modules expect
    try:
        from . import arbiter as _arbiter
        from .arbiter import Arbiter as _Arbiter
        arbiter = _arbiter
        Arbiter = _Arbiter
    except ImportError as e:
        print(f"WARNING: Failed to import arbiter.arbiter module: {e}", file=sys.stderr)

    try:
        from .arena import ArbiterArena as _ArbiterArena
        ArbiterArena = _ArbiterArena
    except ImportError as e:
        print(f"WARNING: Failed to import arbiter.arena module: {e}", file=sys.stderr)

    try:
        from .feedback import FeedbackManager as _FeedbackManager
        FeedbackManager = _FeedbackManager
    except ImportError as e:
        print(f"WARNING: Failed to import arbiter.feedback module: {e}", file=sys.stderr)

    try:
        from .config import ArbiterConfig as _ArbiterConfig
        ArbiterConfig = _ArbiterConfig
    except ImportError as e:
        print(f"WARNING: Failed to import arbiter.config module: {e}", file=sys.stderr)


def _get_human_loop():
    """Lazy import HumanInLoop to avoid circular imports."""
    try:
        from .human_loop import HumanInLoop

        return HumanInLoop
    except ImportError:
        return None


def _get_human_loop_config():
    """Lazy import HumanInLoopConfig to avoid circular imports."""
    try:
        from .human_loop import HumanInLoopConfig

        return HumanInLoopConfig
    except ImportError:
        return None


# Only load components if not in pytest collection mode
if not PYTEST_COLLECTING:
    _load_components()


def get_component_status():
    """
    Returns the availability status of key arbiter components.
    Useful for debugging import issues and checking component availability.

    Returns:
        dict: Component names mapped to their availability status (bool).
              True indicates the component was successfully imported and is available.
              False indicates the component import failed or is not available.
    """
    # Ensure components are loaded before checking status
    _load_components()
    return {
        "Arbiter": Arbiter is not None,
        "ArbiterArena": ArbiterArena is not None,
        "FeedbackManager": FeedbackManager is not None,
        "HumanInLoop": _get_human_loop() is not None,
        "HumanInLoopConfig": _get_human_loop_config() is not None,
        "ArbiterConfig": ArbiterConfig is not None,
    }


# Version info
__version__ = "1.0.0"

# Export all main components
__all__ = [
    "arbiter",
    "Arbiter",
    "ArbiterArena",
    "FeedbackManager",
    "HumanInLoop",
    "HumanInLoopConfig",
    "ArbiterConfig",
    "get_component_status",
]


def __getattr__(name):
    """
    Lazy loading of components to avoid expensive imports during test collection.
    This allows 'from arbiter import Arbiter' to work while deferring
    actual import until runtime.
    """
    # Special lazy imports for circular dependency handling
    lazy_imports = {
        "HumanInLoop": _get_human_loop,
        "HumanInLoopConfig": _get_human_loop_config,
    }

    if name in lazy_imports:
        result = lazy_imports[name]()
        if result is None:
            raise ImportError(f"Cannot import name '{name}' from 'arbiter'")
        return result
    
    if name in _LAZY_COMPONENT_NAMES:
        # Load components on first access
        _load_components()
        # Return the now-loaded component
        result = globals().get(name)
        if result is not None:
            return result
        raise ImportError(f"Cannot import name '{name}' from 'arbiter'")

    raise AttributeError(f"module 'arbiter' has no attribute '{name}'")
