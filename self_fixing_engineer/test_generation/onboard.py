"""
Onboarding module for test_generation.

This module re-exports the onboarding functionality from the simulation plugins module.
"""

import logging

logger = logging.getLogger(__name__)

# Re-export onboarding components from simulation.plugins.onboard
try:
    from simulation.plugins.onboard import (
        CORE_VERSION,
        ONBOARD_DEFAULTS,
        OnboardConfig,
        onboard,
    )
except ImportError as e:
    logger.warning(
        f"Warning: Could not import from simulation.plugins.onboard: {e}. "
        "Using stub implementations."
    )
    # Provide stub implementations for graceful degradation
    CORE_VERSION = "1.0.0-stub"
    ONBOARD_DEFAULTS = {}

    class OnboardConfig:
        """Stub OnboardConfig class for when the real module is not available."""

        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    def onboard(*args, **kwargs):
        """Stub onboard function for when the real module is not available."""
        logger.warning("Onboard function called but module is not available")
        return None


__all__ = ["onboard", "OnboardConfig", "ONBOARD_DEFAULTS", "CORE_VERSION"]
