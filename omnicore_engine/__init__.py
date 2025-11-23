# app/omnicore_engine/__init__.py
"""
OmniCore Engine: Robust modular system for Legal Tender.
Manages dynamic plugin registration, loading, and invocation for all analytical engines.

Note: This package uses lazy imports to minimize import-time overhead and prevent
heavy initialization during pytest collection or package import.
"""

import logging
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

# Avoid importing submodules at package import time to prevent heavy initialization.
# Provide accessor functions that import lazily.


def get_plugin_registry():
    """Return the PLUGIN_REGISTRY singleton (lazy import).
    
    This function lazily imports the plugin registry to avoid heavy
    initialization during package import or pytest collection.
    
    Returns:
        The global plugin registry singleton instance.
    """
    from .plugin_registry import PLUGIN_REGISTRY as _PLUGIN_REGISTRY

    return _PLUGIN_REGISTRY


def get_plugin_event_handler_class():
    """Return the PluginEventHandler class (lazy import).
    
    This function lazily imports the plugin event handler class to avoid heavy
    initialization during package import or pytest collection.
    
    Returns:
        The PluginEventHandler class (not an instance).
    """
    from .plugin_event_handler import PluginEventHandler as _PluginEventHandler

    return _PluginEventHandler


# Export the accessor functions
__all__ = ["get_plugin_registry", "get_plugin_event_handler_class"]
