"""
Mesh - Enterprise Event-Driven Architecture Framework
"""

__version__ = "1.0.0"

# Import core modules
from . import event_bus
from . import mesh_adapter
from . import mesh_policy

# Import checkpoint components
from .checkpoint import checkpoint_manager
from .checkpoint import CheckpointManager

# Export for convenience
__all__ = [
    'event_bus',
    'mesh_adapter', 
    'mesh_policy',
    'checkpoint_manager',
    'CheckpointManager',
]