# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Generator Utilities — Reusable helpers for the code generation pipeline.

Exports:
    ASTEndpointExtractor: AST-based FastAPI endpoint discovery engine.
    ProjectEndpointAnalyzer: Cross-file FastAPI router prefix resolution engine.
"""

try:
    from .ast_endpoint_extractor import ASTEndpointExtractor

    AST_EXTRACTOR_AVAILABLE = True
except ImportError:
    ASTEndpointExtractor = None  # type: ignore[assignment,misc]
    AST_EXTRACTOR_AVAILABLE = False

try:
    from .project_endpoint_analyzer import ProjectEndpointAnalyzer

    PROJECT_ENDPOINT_ANALYZER_AVAILABLE = True
except ImportError:
    ProjectEndpointAnalyzer = None  # type: ignore[assignment,misc]
    PROJECT_ENDPOINT_ANALYZER_AVAILABLE = False

__all__ = [
    "ASTEndpointExtractor",
    "AST_EXTRACTOR_AVAILABLE",
    "ProjectEndpointAnalyzer",
    "PROJECT_ENDPOINT_ANALYZER_AVAILABLE",
]
