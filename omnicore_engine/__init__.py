# app/omnicore_engine/__init__.py
"""
OmniCore Engine: Robust modular system for Legal Tender.
Manages dynamic plugin registration, loading, and invocation for all analytical engines.

Note: This package uses lazy imports to minimize import-time overhead and prevent
heavy initialization during pytest collection or package import.
"""

import logging
import os
import sys
from typing import Any

# ---- Logging Configuration (simple for __init__.py) ----
logger = logging.getLogger("omnicore_engine_init")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(handler)

# Detect pytest collection phase to skip expensive imports
PYTEST_COLLECTING = (
    os.getenv('PYTEST_COLLECTING_ONLY') == '1' or
    os.getenv('PYTEST_CURRENT_TEST') is not None or
    any(arg in sys.argv for arg in ['--collect-only', '--co']) or
    any('--collect' in arg for arg in sys.argv)
)

if PYTEST_COLLECTING:
    logger.info("Pytest collection detected - skipping heavy initialization")

# Avoid importing submodules at package import time to prevent heavy initialization.
# Provide accessor functions that import lazily.

# Module cache for lazy-loaded submodules to prevent stub module creation
_module_cache = {}


def get_plugin_registry():
    """Return the PLUGIN_REGISTRY singleton (lazy import).

    This function lazily imports the plugin registry to avoid heavy
    initialization during package import or pytest collection.

    Returns:
        The global plugin registry singleton instance, or a stub during collection.
        
    Raises:
        ImportError: If plugin_registry module cannot be imported.
    """
    if PYTEST_COLLECTING:
        # Return a stub during collection to avoid heavy initialization
        return type('StubRegistry', (), {
            '__getattr__': lambda self, name: lambda *args, **kwargs: None
        })()
    
    try:
        from .plugin_registry import PLUGIN_REGISTRY as _PLUGIN_REGISTRY
        return _PLUGIN_REGISTRY
    except ImportError as e:
        logger.error(f"Failed to import plugin_registry: {e}", exc_info=True)
        raise ImportError(
            f"Could not import omnicore_engine.plugin_registry. "
            f"Ensure the package is properly installed. Error: {e}"
        ) from e


def get_plugin_event_handler_class():
    """Return the PluginEventHandler class (lazy import).

    This function lazily imports the plugin event handler class to avoid heavy
    initialization during package import or pytest collection.

    Returns:
        The PluginEventHandler class (not an instance), or a stub during collection.
        
    Raises:
        ImportError: If plugin_event_handler module cannot be imported.
    """
    if PYTEST_COLLECTING:
        # Return a stub class during collection to avoid heavy initialization
        return type('StubPluginEventHandler', (), {
            '__init__': lambda self, *args, **kwargs: None,
            '__getattr__': lambda self, name: lambda *args, **kwargs: None
        })
    
    try:
        from .plugin_event_handler import PluginEventHandler as _PluginEventHandler
        return _PluginEventHandler
    except ImportError as e:
        logger.error(f"Failed to import plugin_event_handler: {e}", exc_info=True)
        raise ImportError(
            f"Could not import omnicore_engine.plugin_event_handler. "
            f"Ensure the package is properly installed. Error: {e}"
        ) from e


# PEP 562: Module-level __getattr__ for lazy submodule imports
# This allows "from omnicore_engine import plugin_registry" to work
# while still deferring the actual import until accessed
def __getattr__(name: str) -> Any:
    """Lazy import submodules on attribute access.
    
    This implements PEP 562 to support both:
    1. Direct imports: from omnicore_engine import plugin_registry
    2. Lazy loading: only import when actually accessed
    
    The function ensures proper module caching to prevent stub module creation
    when using imports like "from omnicore_engine.database import Database".
    
    Args:
        name: The attribute/module name being accessed
        
    Returns:
        The requested module or attribute
        
    Raises:
        AttributeError: If the requested attribute doesn't exist
    """
    # Map of lazy-loadable modules
    _lazy_modules = {
        'plugin_registry': '.plugin_registry',
        'plugin_event_handler': '.plugin_event_handler',
        'core': '.core',
        'meta_supervisor': '.meta_supervisor',
        'database': '.database',
        'message_bus': '.message_bus',
    }
    
    if name in _lazy_modules:
        # Check cache first (only for valid lazy-loadable modules)
        if name in _module_cache:
            return _module_cache[name]
        
        import importlib
        import sys
        try:
            module = importlib.import_module(_lazy_modules[name], package=__package__)
            # Cache the imported module in multiple places to prevent stub modules:
            # 1. Package namespace (globals) - for "from omnicore_engine import database"
            globals()[name] = module
            # 2. Module cache dict - for explicit tracking and reuse
            _module_cache[name] = module
            # 3. sys.modules - ensure Python's import system can find it
            # This is crucial for "from omnicore_engine.database import X" to work
            full_module_name = f'{__package__}.{name}'
            if full_module_name not in sys.modules:
                sys.modules[full_module_name] = module
            
            logger.debug(f"Lazy-loaded module: omnicore_engine.{name}")
            return module
        except ImportError as e:
            logger.error(
                f"Failed to lazy-load omnicore_engine.{name}: {e}",
                exc_info=True
            )
            raise AttributeError(
                f"Module 'omnicore_engine' has no attribute '{name}'. "
                f"Import failed: {e}"
            ) from e
    
    raise AttributeError(f"Module 'omnicore_engine' has no attribute '{name}'")


# Export the accessor functions and mark modules as available for import
__all__ = [
    "get_plugin_registry",
    "get_plugin_event_handler_class",
    # Mark these as available for "from omnicore_engine import X"
    # They will be lazy-loaded via __getattr__ when accessed
    "plugin_registry",
    "plugin_event_handler",
    "core",
    "meta_supervisor",
    "database",
    "message_bus",
]
