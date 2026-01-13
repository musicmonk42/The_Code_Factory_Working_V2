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

# Import and expose main components that tests and other modules expect

# Database component
try:
    from .database import Database
except ImportError:
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
except ImportError:
    arbiter = None
    Arbiter = None

try:
    from .arena import ArbiterArena
except ImportError:
    ArbiterArena = None

try:
    from .feedback import FeedbackManager
except ImportError:
    FeedbackManager = None

try:
    from .human_loop import HumanInLoop, HumanInLoopConfig
except ImportError:
    HumanInLoop = None
    HumanInLoopConfig = None

try:
    from .config import ArbiterConfig
except ImportError:
    ArbiterConfig = None


def get_component_status():
    """
    Returns the availability status of key arbiter components.
    Useful for debugging import issues and checking component availability.
    
    Returns:
        dict: Component names mapped to their availability status (bool or class reference)
    """
    return {
        "Arbiter": Arbiter is not None,
        "ArbiterArena": ArbiterArena is not None,
        "FeedbackManager": FeedbackManager is not None,
        "HumanInLoop": HumanInLoop is not None,
        "HumanInLoopConfig": HumanInLoopConfig is not None,
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
