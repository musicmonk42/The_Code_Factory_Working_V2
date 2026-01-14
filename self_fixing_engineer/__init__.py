"""
Self-Fixing Engineer (SFE) package entry point.

This module sets up module aliases for backwards compatibility and internal imports.
The aliasing mechanism allows legacy code to import modules using short names
(e.g., 'from simulation import ...') while maintaining the proper package structure.

Module Structure:
    - simulation: Agent simulation and testing framework
    - arbiter: Core arbitration and decision-making engine
    - guardrails: Safety and validation mechanisms
    - test_generation: Automated test generation
    - intent_capture: User intent parsing and understanding

Version: 1.0.0
"""

import logging
import sys
from typing import Any, Optional

# Configure module logger with NullHandler to follow Python logging best practices
# Applications using this package should configure their own handlers
_init_logger = logging.getLogger(__name__)
_init_logger.addHandler(logging.NullHandler())

# Module metadata
__version__ = "1.0.0"
__all__ = ["__version__"]

# --- Module Aliasing for Backwards Compatibility ---
# This must be done BEFORE any submodule imports to prevent duplicate module loading.
# Many internal modules use relative imports like 'from simulation.xyz import ...'
# or 'from arbiter.xyz import ...', which need to resolve to the full package path.

# Define modules to alias for backwards compatibility
_MODULE_ALIASES = [
    "simulation",
    "arbiter",
    "guardrails",
    "test_generation",
    "intent_capture",
]


def _setup_module_alias(module_name: str) -> None:
    """
    Set up a module alias in sys.modules for backwards compatibility.
    
    Args:
        module_name: The name of the submodule to alias (e.g., 'simulation')
        
    Note:
        This function silently handles ImportError to allow partial package installations.
        Missing modules are logged at DEBUG level for troubleshooting.
    """
    try:
        # Import the module dynamically
        submodule = __import__(
            f"{__name__}.{module_name}",
            fromlist=[module_name]
        )
        
        # Only create alias if it doesn't already exist
        if module_name not in sys.modules:
            sys.modules[module_name] = submodule
            _init_logger.debug(
                "Module alias created: '%s' -> '%s.%s'",
                module_name,
                __name__,
                module_name
            )
    except ImportError as e:
        _init_logger.debug(
            "Module '%s' not available for aliasing: %s",
            module_name,
            e,
            exc_info=False  # Set to True for debugging if needed
        )
    except Exception as e:
        # Catch any unexpected errors during module setup
        _init_logger.warning(
            "Unexpected error setting up alias for module '%s': %s",
            module_name,
            e,
            exc_info=True
        )


# Set up all module aliases
for _module in _MODULE_ALIASES:
    _setup_module_alias(_module)
