# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

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
# NOTE: These are intentionally NOT pre-defined here so that __getattr__
# can handle lazy loading. They are set by _load_components() when called.
# Pre-defining them as None would prevent __getattr__ from being triggered
# since Python only calls __getattr__ for attributes that don't exist.

# Track if we've already loaded components
_components_loaded = False
_components_loading = False  # Prevents recursive _load_components() calls

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
    global _components_loading
    
    if _components_loaded or _components_loading:
        return
    
    _components_loading = True
    
    try:
        # Import and expose main components that tests and other modules expect
        try:
            # Import arbiter.py module explicitly to avoid circular reference
            # First trigger the import to populate sys.modules
            import self_fixing_engineer.arbiter.arbiter
            # Then get the actual module from sys.modules
            _arbiter = sys.modules.get('self_fixing_engineer.arbiter.arbiter')
            from .arbiter import Arbiter as _Arbiter
            arbiter = _arbiter
            if isinstance(_Arbiter, type):
                Arbiter = _Arbiter
            else:
                globals().pop("Arbiter", None)
        except Exception as e:
            print(f"WARNING: Failed to import arbiter.arbiter module: {e}", file=sys.stderr)

        try:
            from .arena import ArbiterArena as _ArbiterArena
            ArbiterArena = _ArbiterArena
        except Exception as e:
            print(f"WARNING: Failed to import arbiter.arena module: {e}", file=sys.stderr)

        try:
            from .feedback import FeedbackManager as _FeedbackManager
            FeedbackManager = _FeedbackManager
        except Exception as e:
            print(f"WARNING: Failed to import arbiter.feedback module: {e}", file=sys.stderr)

        try:
            from .config import ArbiterConfig as _ArbiterConfig
            ArbiterConfig = _ArbiterConfig
        except Exception as e:
            print(f"WARNING: Failed to import arbiter.config module: {e}", file=sys.stderr)
        
        _components_loaded = True
    finally:
        _components_loading = False


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
        "Arbiter": globals().get("Arbiter") is not None,
        "ArbiterArena": globals().get("ArbiterArena") is not None,
        "FeedbackManager": globals().get("FeedbackManager") is not None,
        "HumanInLoop": _get_human_loop() is not None,
        "HumanInLoopConfig": _get_human_loop_config() is not None,
        "ArbiterConfig": globals().get("ArbiterConfig") is not None,
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
    # Persistent array store (canonical replacement for arbiter_array_backend)
    "persistent_array_store",
    "PersistentArrayStore",
    "ConcretePersistentArrayStore",
    # Stub implementations for graceful degradation
    "stubs",
    "ArbiterStub",
    "PolicyEngineStub",
    "BugManagerStub",
    "KnowledgeGraphStub",
    "HumanInLoopStub",
    "MessageQueueServiceStub",
    "FeedbackManagerStub",
    "ArbiterArenaStub",
    "KnowledgeLoaderStub",
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
    
    # Lazy load stub components
    if name in ["stubs", "ArbiterStub", "PolicyEngineStub", "BugManagerStub", 
                "KnowledgeGraphStub", "HumanInLoopStub", "MessageQueueServiceStub",
                "FeedbackManagerStub", "ArbiterArenaStub", "KnowledgeLoaderStub"]:
        from . import stubs as _stubs_module
        if name == "stubs":
            return _stubs_module
        return getattr(_stubs_module, name)

    # Lazy-load persistent_array_store and its public classes.
    # This avoids the heavy import (NumPy, cryptography, Redis, SQLite, etc.)
    # until the caller actually needs the persistent array store.
    if name in ("persistent_array_store", "PersistentArrayStore", "ConcretePersistentArrayStore"):
        try:
            from . import persistent_array_store as _pas_module
            if name == "persistent_array_store":
                return _pas_module
            return getattr(_pas_module, name)
        except ImportError as exc:
            raise ImportError(
                f"Cannot import {name!r} from 'arbiter.persistent_array_store': {exc}"
            ) from exc

    if name in _LAZY_COMPONENT_NAMES:
        # Load components on first access
        _load_components()
        # Return the now-loaded component
        result = globals().get(name)
        if result is not None:
            return result
        raise ImportError(f"Cannot import name '{name}' from 'arbiter'")

    # Check if this is a submodule that was already imported by the import system
    # (e.g. 'policy', 'models', 'plugins') — Python sets subpackages as attributes
    # on their parent package, but a custom __getattr__ is only called when the
    # attribute is NOT found in the module's __dict__.  When pytest's monkeypatch
    # or unittest.mock.patch traverses dotted import paths like
    # "self_fixing_engineer.arbiter.policy.core.audit_log", they use getattr() on
    # each intermediate module.  Without this fallback, the traversal would fail
    # even for subpackages that have already been imported.
    full_name = f"self_fixing_engineer.arbiter.{name}"
    submodule = sys.modules.get(full_name)
    if submodule is not None:
        return submodule

    raise AttributeError(f"module 'arbiter' has no attribute '{name}'")
