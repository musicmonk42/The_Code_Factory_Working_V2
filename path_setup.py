# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Centralized Path Configuration Module

This module provides centralized path management for The Code Factory platform.
It ensures all components (generator, self_fixing_engineer, omnicore_engine,
shared) are discoverable by adding them to sys.path in a consistent manner.

Features:
- Defines PROJECT_ROOT as the repository root
- Lists all component paths
- Ensures PROJECT_ROOT itself is in sys.path for top-level packages (``shared``)
- Provides setup_paths() function for explicit setup
- Auto-executes on import for convenience
- Idempotent: safe to call multiple times

Usage:
    # Automatic setup on import
    import path_setup

    # Or explicit setup
    from path_setup import setup_paths, PROJECT_ROOT
    setup_paths()
"""

import sys
from pathlib import Path
from typing import List

# Define project root (repository root directory)
PROJECT_ROOT = Path(__file__).parent.absolute()

# Define all component paths relative to project root
COMPONENT_PATHS = {
    "generator": PROJECT_ROOT / "generator",
    "self_fixing_engineer": PROJECT_ROOT / "self_fixing_engineer",
    "omnicore_engine": PROJECT_ROOT / "omnicore_engine",
    "monitoring": PROJECT_ROOT / "monitoring",
}

# Initialize logger module-level for efficiency
import logging

_logger = logging.getLogger(__name__)


def setup_paths(verbose: bool = False) -> List[str]:
    """
    Add all component paths to sys.path for discoverability.

    This function is idempotent - it's safe to call multiple times.
    Paths are only added if they exist and aren't already in sys.path.

    Args:
        verbose: If True, print status messages about path additions

    Returns:
        List of paths that were added to sys.path
    """
    added_paths = []

    # ------------------------------------------------------------------
    # Step 1: Ensure PROJECT_ROOT is in sys.path.
    #
    # Top-level packages such as ``shared`` live directly under the
    # repository root (e.g. ``shared/noop_metrics.py``).  Python can only
    # resolve ``from shared.noop_metrics import X`` when PROJECT_ROOT
    # itself — not just its sub-directories — is present on sys.path.
    #
    # ``server/run.py`` and ``conftest.py`` both add the root explicitly,
    # but standalone scripts that import ``path_setup`` directly would
    # otherwise miss it.
    # ------------------------------------------------------------------
    root_str = str(PROJECT_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
        added_paths.append(root_str)
        if verbose:
            print(f"[path_setup] Added project root to sys.path: {root_str}")

    # ------------------------------------------------------------------
    # Step 2: Add per-component sub-directories for legacy-style imports.
    #
    # These are appended (not prepended) so that the project root added in
    # Step 1 is always searched first.  This prevents component-local
    # sub-packages (e.g. ``self_fixing_engineer/shared/``) from shadowing
    # top-level packages such as ``shared/`` that live under PROJECT_ROOT.
    # ------------------------------------------------------------------
    for component_name, component_path in COMPONENT_PATHS.items():
        if component_path.exists():
            path_str = str(component_path)
            if path_str not in sys.path:
                sys.path.append(path_str)
                added_paths.append(path_str)
                if verbose:
                    print(
                        f"[path_setup] Appended {component_name} to sys.path: {path_str}"
                    )
            else:
                if verbose:
                    print(
                        f"[path_setup] {component_name} already in sys.path: {path_str}"
                    )
        else:
            if verbose:
                print(
                    f"[path_setup] Component not found: {component_name} at {component_path}"
                )

    return added_paths


def get_component_path(component_name: str) -> Path:
    """
    Get the path for a specific component.

    Args:
        component_name: Name of the component (e.g., "generator", "self_fixing_engineer")

    Returns:
        Path object for the component

    Raises:
        KeyError: If component_name is not recognized
    """
    if component_name not in COMPONENT_PATHS:
        raise KeyError(
            f"Unknown component '{component_name}'. "
            f"Available components: {list(COMPONENT_PATHS.keys())}"
        )
    return COMPONENT_PATHS[component_name]


def validate_paths() -> dict:
    """
    Validate that all component paths exist.

    Returns:
        Dictionary mapping component names to existence status (bool)
    """
    return {
        component_name: component_path.exists()
        for component_name, component_path in COMPONENT_PATHS.items()
    }


# Auto-execute path setup on module import
# This ensures paths are configured as soon as this module is imported
_added_on_import = setup_paths(verbose=False)

if _added_on_import:
    # Only log if we actually added paths (avoid spam on re-imports)
    _logger.debug(
        "path_setup: Added %d component paths to sys.path", len(_added_on_import)
    )


__all__ = [
    "PROJECT_ROOT",
    "COMPONENT_PATHS",
    "setup_paths",
    "get_component_path",
    "validate_paths",
]
