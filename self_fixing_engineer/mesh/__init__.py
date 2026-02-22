# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Mesh - Enterprise Event-Driven Architecture Framework
"""

__version__ = "1.0.0"

# Import core modules
from . import event_bus, mesh_adapter, mesh_policy

# Import checkpoint components
from .checkpoint import CheckpointManager, checkpoint_manager

# Import GraphRAG policy reasoning engine
try:
    from .graph_rag_policy import GraphRAGPolicyReasoner, PolicyDecision, PolicyNode

    GRAPH_RAG_AVAILABLE = True
except ImportError:
    GraphRAGPolicyReasoner = None  # type: ignore[assignment,misc]
    PolicyDecision = None  # type: ignore[assignment,misc]
    PolicyNode = None  # type: ignore[assignment,misc]
    GRAPH_RAG_AVAILABLE = False

# Export for convenience
__all__ = [
    "event_bus",
    "mesh_adapter",
    "mesh_policy",
    "checkpoint_manager",
    "CheckpointManager",
    "GraphRAGPolicyReasoner",
    "PolicyDecision",
    "PolicyNode",
    "GRAPH_RAG_AVAILABLE",
]
