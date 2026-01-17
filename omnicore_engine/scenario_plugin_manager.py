"""
DEPRECATED: This module is deprecated and will be removed in a future version.

The functionality in this file duplicates code from core.py and is no longer needed.
Scenario plugins should be registered through the main PluginRegistry in plugin_registry.py
using the PlugInKind.SCENARIO type.

Migration Guide:
- Instead of using scenario_plugin_manager, import and use plugin_registry.PLUGIN_REGISTRY
- Register scenario plugins with kind=PlugInKind.SCENARIO
- Use scenario_constants.py for scenario-specific data structures and metrics

For backward compatibility, this module re-exports the relevant components from core.py
but all new code should import directly from omnicore_engine.core or omnicore_engine.plugin_registry.
"""

import warnings

# Show deprecation warning only once per session to avoid log noise
warnings.simplefilter("once", DeprecationWarning)
warnings.warn(
    "scenario_plugin_manager is deprecated. Use omnicore_engine.plugin_registry for plugin management "
    "and omnicore_engine.core for engine functionality. "
    "This module will be removed in a future version.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from core for backward compatibility
from omnicore_engine.core import (
    Base,
    ExplainableAI,
    OmniCoreEngine,
    get_plugin_metrics,
    get_test_metrics,
    logger,
    omnicore_engine,
    safe_serialize,
    settings,
)

__all__ = [
    "Base",
    "omnicore_engine",
    "safe_serialize",
    "logger",
    "settings",
    "get_plugin_metrics",
    "get_test_metrics",
    "ExplainableAI",
    "OmniCoreEngine",
]
