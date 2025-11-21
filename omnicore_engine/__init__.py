# app/omnicore_engine/__init__.py
"""
OmniCore Engine: Robust modular system for Legal Tender.
Manages dynamic plugin registration, loading, and invocation for all analytical engines.
"""

import logging
import sys
from typing import Any, Callable, Optional, Type, Dict, List

# Import PLUGIN_REGISTRY and plugin_event_handler as they are global singletons
from .plugin_registry import PLUGIN_REGISTRY
from .plugin_event_handler import PluginEventHandler as plugin_event_handler

# Note: Other modules (audit, core, cli, engines, etc.) should be imported
# directly when needed, not at package level, to avoid circular imports.
# Example: from omnicore_engine import audit
#          or: from omnicore_engine.audit import ExplainAudit


# ---- Logging Configuration (simple for __init__.py) ----
logger = logging.getLogger("omnicore_engine_init")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    logger.addHandler(handler)

# FIXED: Removed misleading entries from __all__.
# The __all__ list now only contains names actually imported or defined in this file,
# making the package's public API explicit and avoiding import errors.
__all__ = [
    "PLUGIN_REGISTRY",
    "plugin_event_handler"
]