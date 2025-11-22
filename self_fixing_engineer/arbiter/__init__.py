"""
Arbiter package - Core components for the Self-Fixing Engineer platform.
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
    from .human_loop import HumanInLoop
except ImportError:
    HumanInLoop = None

try:
    from .config import ArbiterConfig
except ImportError:
    ArbiterConfig = None

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
    "ArbiterConfig",
]
