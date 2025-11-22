# simulation/__init__.py
"""
Simulation module for Self-Fixing Engineer platform.
Provides entry points for OmniCore integration.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# --- Entry points for OmniCore ---
def simulation_run_entrypoint(*args, **kwargs):
    """
    Main entrypoint for running simulation orchestrator.
    Calls the core.main() function with async support.
    """
    from .core import main as simulation_main
    return asyncio.run(simulation_main(*args, **kwargs))

def simulation_health_check():
    """
    Health check for simulation module.
    Returns a simple health dict or raises an exception.
    """
    try:
        from .registry import get_registry
        registry = get_registry()
        # Handle case where registry might be None or not a dict
        plugin_count = 0
        if registry:
            if isinstance(registry, dict):
                plugin_count = len(registry)
            else:
                logger.warning(f"Registry returned unexpected type: {type(registry)}")
        
        return {
            "status": "healthy",
            "module": "simulation",
            "plugins_loaded": plugin_count
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "module": "simulation",
            "error": str(e)
        }

def simulation_get_registry():
    """Return the SIM_REGISTRY or other registry dict."""
    from .registry import get_registry
    return get_registry()

# --- Register with OmniCore if running inside it ---
def _register_with_omnicore():
    """
    Register simulation engine with OmniCore.
    Gracefully handles case where OmniCore is not available.
    """
    try:
        from omnicore_engine.engines import register_engine
        register_engine(
            "simulation",
            entrypoints={
                "run": simulation_run_entrypoint,
                "health_check": simulation_health_check,
                "get_registry": simulation_get_registry,
            }
        )
        logger.info("Simulation engine registered with OmniCore successfully")
    except ImportError:
        # Not running under OmniCore, skip registration
        logger.debug("OmniCore not available, skipping simulation engine registration")
    except Exception as e:
        logger.warning(f"Failed to register simulation engine with OmniCore: {e}")

_register_with_omnicore()
