# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Import Fixer Package - Lazy Loading Architecture

This package provides lazy imports to avoid loading heavy submodules during
test collection, preventing OOM errors and excessive initialization overhead.

Submodules are only imported when actually accessed, reducing memory footprint
and startup time in test/CI environments.
"""

import sys as _sys
import importlib as _importlib


# Static mapping of submodule names to full module paths
_submodules = {
    'fixer_ast': 'self_fixing_engineer.self_healing_import_fixer.import_fixer.fixer_ast',
    'fixer_dep': 'self_fixing_engineer.self_healing_import_fixer.import_fixer.fixer_dep',
    'fixer_plugins': 'self_fixing_engineer.self_healing_import_fixer.import_fixer.fixer_plugins',
    'fixer_validate': 'self_fixing_engineer.self_healing_import_fixer.import_fixer.fixer_validate',
}


def __getattr__(name):
    """Lazy import submodules only when accessed.
    
    This prevents eager loading of fixer_ast, fixer_dep, fixer_plugins, and
    fixer_validate until they're actually used, avoiding heavy initialization
    of core_audit, core_secrets, Redis clients, etc. during test collection.
    
    Args:
        name: Module attribute name (e.g., 'fixer_ast')
        
    Returns:
        The requested submodule
        
    Raises:
        AttributeError: If the requested attribute doesn't exist
    """
    
    if name in _submodules:
        mod = _importlib.import_module(_submodules[name])
        # Also register bare-name alias for legacy/bare imports
        _sys.modules.setdefault(name, mod)
        # Cache in this module's __dict__ for faster subsequent access
        globals()[name] = mod
        return mod
    
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Provide explicit __all__ for better IDE support and import clarity
__all__ = ['fixer_ast', 'fixer_dep', 'fixer_plugins', 'fixer_validate']
