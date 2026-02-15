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
    """No-op decorator used when omnicore_engine.plugin_registry is unavailable.

    Note: Exported as ``plugin`` at the bottom of this module so that consumer
    code can write ``from generator.agents.plugin_stubs import plugin``.
    """

    def decorator(func):
        return func

    return decorator


class _FallbackPlugInKind:
    """Stub for PlugInKind used when omnicore_engine.plugin_registry is unavailable.

    Note: Exported as ``PlugInKind`` at the bottom of this module.
    """

    CHECK = "CHECK"
    FIX = "FIX"
    TRANSFORM = "TRANSFORM"
    ENRICH = "ENRICH"
    VALIDATE = "VALIDATE"


try:
    from omnicore_engine.plugin_base import PlugInKind
    from omnicore_engine.plugin_registry import plugin
except ImportError:
    try:
        from omnicore_engine.plugin_registry import PlugInKind, plugin
    except ImportError:
        plugin = _fallback_plugin
        PlugInKind = _FallbackPlugInKind
