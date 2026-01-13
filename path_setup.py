"""
Centralized Path Configuration Module

This module provides centralized path management for The Code Factory platform.
It ensures all components (generator, self_fixing_engineer, omnicore_engine) are
discoverable by adding them to sys.path in a consistent manner.

Features:
- Defines PROJECT_ROOT as the repository root
- Lists all component paths
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
    
    for component_name, component_path in COMPONENT_PATHS.items():
        if component_path.exists():
            path_str = str(component_path)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)
                added_paths.append(path_str)
                if verbose:
                    print(f"[path_setup] Added {component_name} to sys.path: {path_str}")
            else:
                if verbose:
                    print(f"[path_setup] {component_name} already in sys.path: {path_str}")
        else:
            if verbose:
                print(f"[path_setup] Component not found: {component_name} at {component_path}")
    
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
    import logging
    logger = logging.getLogger(__name__)
    logger.debug(f"path_setup: Added {len(_added_on_import)} component paths to sys.path")


__all__ = [
    "PROJECT_ROOT",
    "COMPONENT_PATHS",
    "setup_paths",
    "get_component_path",
    "validate_paths",
]
