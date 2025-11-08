"""
Runner package entry point.
Centralises registries, OTEL tracer, and re-exports public symbols.
"""
# generator/runner/__init__.py
import os, sys
# Detect pytest / testing early & reliably
TESTING = (
    os.getenv("TESTING") == "1"
    or "pytest" in sys.modules
    or os.getenv("PYTEST_CURRENT_TEST") is not None
    or os.getenv("PYTEST_ADDOPTS") is not None
)

__all__ = ["TESTING"]  # keep this light; no heavy imports here

# Import heavy/validating modules only in real runtime, never during tests
if not TESTING:
    # from .llm_plugin_manager import LLMPluginManager  # ← leave commented out
    pass