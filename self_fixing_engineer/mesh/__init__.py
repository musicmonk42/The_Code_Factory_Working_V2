# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Mesh - Enterprise Event-Driven Architecture Framework
"""

__version__ = "1.0.0"

# Import core modules
from . import event_bus, mesh_adapter, mesh_policy

# Import checkpoint components
from .checkpoint import CheckpointManager, checkpoint_manager

# Export for convenience
__all__ = [
    "event_bus",
    "mesh_adapter",
    "mesh_policy",
    "checkpoint_manager",
    "CheckpointManager",
]
