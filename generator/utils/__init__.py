# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Generator Utilities — Reusable helpers for the code generation pipeline.

Exports:
    ASTEndpointExtractor: AST-based FastAPI endpoint discovery engine.
"""

try:
    from .ast_endpoint_extractor import ASTEndpointExtractor

    AST_EXTRACTOR_AVAILABLE = True
except ImportError:
    ASTEndpointExtractor = None  # type: ignore[assignment,misc]
    AST_EXTRACTOR_AVAILABLE = False

__all__ = [
    "ASTEndpointExtractor",
    "AST_EXTRACTOR_AVAILABLE",
]
