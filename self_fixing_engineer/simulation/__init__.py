# simulation/__init__.py
"""
Simulation module for Self-Fixing Engineer platform.

This module provides entry points for OmniCore integration with optimized
lazy loading to avoid expensive initialization during pytest collection.

Performance Considerations:
    - Defers all heavy imports until actually needed
    - Skips initialization entirely during pytest collection phase
    - Uses environment variables PYTEST_CURRENT_TEST and PYTEST_COLLECTING
      to detect test collection mode

Module Behavior:
    - In test collection mode: Provides stub functions that raise errors if called
    - In runtime mode: Full initialization with database, message bus, and event loops

Environment Variables:
    PYTEST_CURRENT_TEST: Set by pytest during test execution
    PYTEST_COLLECTING: Set during pytest collection phase
"""

from __future__ import annotations

import os
import logging
from typing import Any, Dict

__all__ = [
    "simulation_run_entrypoint",
    "simulation_health_check", 
    "simulation_get_registry",
]

logger = logging.getLogger(__name__)

# Detect pytest collection mode to avoid expensive initialization
# Using PYTEST_COLLECTING for consistency with simulation_module.py
PYTEST_COLLECTING = bool(
    os.getenv("PYTEST_CURRENT_TEST") or os.getenv("PYTEST_COLLECTING")
)

if PYTEST_COLLECTING:
    # Test collection mode: Provide lightweight stubs
    logger.debug("Skipping simulation module initialization during pytest collection")
    
    def simulation_run_entrypoint(*args: Any, **kwargs: Any) -> Any:
        """
        Stub entrypoint for test collection mode.
        
        Raises:
            RuntimeError: Always raises to prevent accidental use during collection.
        """
        raise RuntimeError(
            "Simulation not initialized - test collection mode. "
            "This function should not be called during pytest collection."
        )
    
    def simulation_health_check() -> Dict[str, str]:
        """
        Stub health check for test collection mode.
        
        Returns:
            Dict indicating test mode status.
        """
        return {"status": "test_mode", "module": "simulation"}
    
    def simulation_get_registry() -> Dict[str, Any]:
        """
        Stub registry getter for test collection mode.
        
        Returns:
            Empty dictionary.
        """
        return {}
    
    def _register_with_omnicore() -> None:
        """No-op registration stub for test collection mode."""
        pass
    
else:
    # Runtime mode: Full initialization with lazy imports
    import asyncio
    
    def simulation_run_entrypoint(*args: Any, **kwargs: Any) -> Any:
        """
        Main entrypoint for running simulation orchestrator.
        
        Lazily imports and runs the core simulation main function.
        Uses asyncio.run() to handle async execution.
        
        Args:
            *args: Positional arguments passed to simulation main.
            **kwargs: Keyword arguments passed to simulation main.
            
        Returns:
            Result from simulation execution.
            
        Raises:
            ImportError: If core simulation module is not available.
            
        Example:
            >>> from self_fixing_engineer.simulation import simulation_run_entrypoint
            >>> result = simulation_run_entrypoint(config={'type': 'test'})
        """
        from .core import main as simulation_main
        return asyncio.run(simulation_main(*args, **kwargs))
    
    def simulation_health_check() -> Dict[str, Any]:
        """
        Health check for simulation module.
        
        Checks registry availability and plugin count.
        
        Returns:
            Dict with health status, containing:
                - status: "healthy" or "unhealthy"
                - module: Module name ("simulation")
                - plugins_loaded: Number of registered plugins (on success)
                - error: Error message (on failure)
        """
        try:
            from .registry import get_registry
            
            registry = get_registry()
            plugin_count = 0
            
            if registry:
                if isinstance(registry, dict):
                    plugin_count = len(registry)
                else:
                    logger.warning(
                        f"Registry returned unexpected type: {type(registry).__name__}"
                    )
            
            return {
                "status": "healthy",
                "module": "simulation",
                "plugins_loaded": plugin_count,
            }
        except Exception as e:
            logger.error(f"Health check failed: {e}", exc_info=True)
            return {
                "status": "unhealthy",
                "module": "simulation",
                "error": str(e),
            }
    
    def simulation_get_registry() -> Dict[str, Any]:
        """
        Return the simulation plugin registry.
        
        Lazily imports the registry module to avoid expensive initialization.
        
        Returns:
            Dict containing registered simulation plugins.
            
        Raises:
            ImportError: If registry module is not available.
        """
        from .registry import get_registry
        return get_registry()
    
    def _register_with_omnicore() -> None:
        """
        Register simulation engine with OmniCore.
        
        Attempts to register simulation entrypoints with the OmniCore engine
        registry. Gracefully handles cases where OmniCore is not available.
        
        This function is called automatically at module import time (not during
        test collection).
        
        Logs:
            - INFO: On successful registration
            - DEBUG: If OmniCore is not available
            - WARNING: On registration failure
        """
        try:
            from omnicore_engine.engines import register_engine
            
            register_engine(
                "simulation",
                entrypoints={
                    "run": simulation_run_entrypoint,
                    "health_check": simulation_health_check,
                    "get_registry": simulation_get_registry,
                },
            )
            logger.info("Simulation engine registered with OmniCore successfully")
        except ImportError:
            logger.debug("OmniCore not available, skipping simulation engine registration")
        except Exception as e:
            logger.warning(
                f"Failed to register simulation engine with OmniCore: {e}",
                exc_info=True,
            )

# Perform registration only if not in test collection mode
_register_with_omnicore()
