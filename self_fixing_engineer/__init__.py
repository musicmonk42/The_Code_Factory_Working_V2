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
    # Skip if alias already exists (don't overwrite existing modules)
    if module_name in sys.modules:
        existing = sys.modules[module_name]
        _init_logger.debug(
            "Module alias '%s' already exists (existing module: %s), skipping",
            module_name,
            getattr(existing, "__name__", "unknown"),
        )
        return

    try:
        # Import the module dynamically
        full_module_name = f"{__name__}.{module_name}"
        submodule = __import__(full_module_name, fromlist=[module_name])

        # Create alias
        sys.modules[module_name] = submodule
        _init_logger.debug(
            "Module alias created: '%s' -> '%s'",
            module_name,
            full_module_name,
        )
    except ImportError as e:
        _init_logger.debug(
            "Module '%s' not available for aliasing: %s",
            module_name,
            e,
            exc_info=False,
        )
    except RuntimeError as e:
        # Handle thread creation errors in CI environments
        if "can't start new thread" in str(e):
            _init_logger.warning(
                "Thread limit reached while setting up alias for '%s': %s. This is expected in CI environments.",
                module_name,
                e,
            )
            # Try to still set up the alias if the module was partially imported
            full_module_name = f"{__name__}.{module_name}"
            if full_module_name in sys.modules:
                partial_module = sys.modules[full_module_name]
                # Only create alias if the module appears to be properly initialized
                # (has __file__ attribute which all properly loaded modules have)
                if hasattr(partial_module, "__file__") or hasattr(
                    partial_module, "__path__"
                ):
                    sys.modules[module_name] = partial_module
                    _init_logger.debug(
                        "Module alias created despite thread error: '%s' -> '%s'",
                        module_name,
                        full_module_name,
                    )
                else:
                    _init_logger.debug(
                        "Skipping alias for '%s' - module appears incomplete",
                        module_name,
                    )
            return
        else:
            raise
    except Exception as e:
        # Catch any unexpected errors during module setup
        _init_logger.warning(
            "Unexpected error setting up alias for module '%s': %s",
            module_name,
            e,
            exc_info=True,  # Keep full traceback for unexpected errors
        )


# Use lazy import hooks instead of eager loading
# Module aliases will be created on-demand when first accessed
class _LazyModuleLoader:
    """Lazy loader for module aliases to avoid import-time overhead."""

    def __init__(self, module_aliases):
        self._aliases = module_aliases
        self._loaded = set()

    def __call__(self, name):
        if name in self._aliases and name not in self._loaded:
            _setup_module_alias(name)
            self._loaded.add(name)


_lazy_loader = _LazyModuleLoader(_MODULE_ALIASES)


# Override module __getattr__ for lazy loading
def __getattr__(name: str) -> Any:
    if name in _MODULE_ALIASES:
        _lazy_loader(name)
        # First try to get the alias
        result = sys.modules.get(name)
        if result is None:
            # Fallback to the full module path if alias wasn't set
            full_module_name = f"{__name__}.{name}"
            result = sys.modules.get(full_module_name)
        if result is None:
            raise AttributeError(
                f"module '{__name__}' has no attribute '{name}' "
                f"(module import may have failed)"
            )
        return result
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
