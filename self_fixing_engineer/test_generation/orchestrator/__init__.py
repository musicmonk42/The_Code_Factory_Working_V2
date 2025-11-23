# test_generation/orchestrator/__init__.py
from __future__ import annotations
import warnings
import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

try:
    # Correct imports from the appropriate modules
    from ..utils import (
        validate_and_resolve_path as validate_path,
        PathError,
    )
    from .venvs import sanitize_path
except ImportError as e:
    # FIX: Soften the error handling to a warning and proceed.
    # This allows tests and components to gracefully handle missing dependencies.
    warnings.warn(
        f"Import warning: Failed to import a core component: {e}. "
        "Some functionality may be disabled.",
        RuntimeWarning,
    )
    logger.warning(
        f"Import warning: Failed to import a core component: {e}. "
        "Some functionality may be disabled."
    )

    # Define dummy functions to prevent NameError
    def sanitize_path(*args, **kwargs):
        raise NotImplementedError("sanitize_path is not available due to import failure.")

    def validate_path(*args, **kwargs):
        raise NotImplementedError("validate_path is not available due to import failure.")

    class PathError(Exception):
        pass


# Define the public API for this package
__all__ = [
    "sanitize_path",
    "validate_path",
    "PathError",
]
