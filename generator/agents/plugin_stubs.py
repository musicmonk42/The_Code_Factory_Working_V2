# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Shared Plugin Fallback Stubs for Generator Agents

Provides fallback implementations of the omnicore_engine plugin decorator and
PlugInKind class for use when omnicore_engine is not available (e.g., in test
environments or standalone deployments).

This eliminates duplicated fallback stubs across multiple agent modules.
"""

import logging

logger = logging.getLogger(__name__)


def _fallback_plugin(**kwargs):
    """Fallback no-op decorator when omnicore_engine.plugin_registry is unavailable."""

    def decorator(func):
        return func

    return decorator


class _FallbackPlugInKind:
    """Fallback PlugInKind class when omnicore_engine.plugin_registry is unavailable."""

    CHECK = "CHECK"
    FIX = "FIX"
    TRANSFORM = "TRANSFORM"
    ENRICH = "ENRICH"
    VALIDATE = "VALIDATE"


try:
    from omnicore_engine.plugin_registry import PlugInKind, plugin
except ImportError:
    plugin = _fallback_plugin
    PlugInKind = _FallbackPlugInKind
