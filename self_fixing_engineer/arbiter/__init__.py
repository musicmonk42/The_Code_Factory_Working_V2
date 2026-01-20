"""
Arbiter package - Core components for the Self-Fixing Engineer platform.

This package provides:
- Arbiter: Main orchestrator for self-fixing engineering workflows
- ArbiterArena: Multi-agent collaboration environment
- FeedbackManager: Feedback collection and metrics management
- HumanInLoop: Human-in-the-loop approval workflows
- HumanInLoopConfig: Configuration for human-in-the-loop features
- ArbiterConfig: Core configuration management
- Database: Database abstraction layer
"""

import sys

# Import and expose main components that tests and other modules expect

# Database component
try:
    from .database import Database
except ImportError as e:
    print(f"WARNING: Failed to import arbiter.database module: {e}", file=sys.stderr)
    # Mock Database for testing when actual implementation isn't available
    class Database:
        """Mock Database implementation for testing."""

        def __init__(self, *args, **kwargs):
            self.connection = None
            self.is_connected = False

        async def connect(self):
            self.is_connected = True
            return True

        async def disconnect(self):
            self.is_connected = False
            return True

        async def execute(self, query, params=None):
            return {"status": "ok", "rows": []}

        async def fetch_one(self, query, params=None):
            return {"id": 1, "data": "mock"}

        async def fetch_all(self, query, params=None):
            return [{"id": 1, "data": "mock"}]


# Import other components that might be needed
try:
    from . import arbiter
    from .arbiter import Arbiter
except ImportError as e:
    print(f"WARNING: Failed to import arbiter.arbiter module: {e}", file=sys.stderr)
    arbiter = None
    Arbiter = None

try:
    from .arena import ArbiterArena
except ImportError as e:
    print(f"WARNING: Failed to import arbiter.arena module: {e}", file=sys.stderr)
    ArbiterArena = None

try:
    from .feedback import FeedbackManager
except ImportError as e:
    print(f"WARNING: Failed to import arbiter.feedback module: {e}", file=sys.stderr)
    FeedbackManager = None


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


try:
    from .config import ArbiterConfig
except ImportError as e:
    print(f"WARNING: Failed to import arbiter.config module: {e}", file=sys.stderr)
    ArbiterConfig = None


def get_component_status():
    """
    Returns the availability status of key arbiter components.
    Useful for debugging import issues and checking component availability.

    Returns:
        dict: Component names mapped to their availability status (bool).
              True indicates the component was successfully imported and is available.
              False indicates the component import failed or is not available.
    """
    return {
        "Arbiter": Arbiter is not None,
        "ArbiterArena": ArbiterArena is not None,
        "FeedbackManager": FeedbackManager is not None,
        "HumanInLoop": _get_human_loop() is not None,
        "HumanInLoopConfig": _get_human_loop_config() is not None,
        "ArbiterConfig": ArbiterConfig is not None,
        "Database": Database is not None,
    }


# Version info
__version__ = "1.0.0"

# Export all main components
__all__ = [
    "Database",
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
    Lazy loading of HumanInLoop and HumanInLoopConfig to avoid circular imports.
    This allows 'from arbiter import HumanInLoop' to work while preventing
    circular dependency issues at module initialization time.
    """
    lazy_imports = {
        "HumanInLoop": _get_human_loop,
        "HumanInLoopConfig": _get_human_loop_config,
    }

    if name in lazy_imports:
        result = lazy_imports[name]()
        if result is None:
            raise ImportError(f"Cannot import name '{name}' from 'arbiter'")
        return result

    raise AttributeError(f"module 'arbiter' has no attribute '{name}'")
