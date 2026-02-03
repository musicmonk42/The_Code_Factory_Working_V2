"""
Runner package entry point.
Centralises registries, OTEL tracer, and re-exports public symbols.
"""

# generator/runner/__init__.py
import os
import sys

# --- Module Aliasing for Backwards Compatibility ---
# This must be done BEFORE any submodule imports to prevent duplicate module loading.
# When this package is imported as 'generator.runner', we need to ensure that
# any internal imports like 'from runner.runner_config import ...' resolve to
# the same module objects.

# Determine if we're being imported as 'generator.runner' or just 'runner'
_is_generator_import = __name__ == "generator.runner"

# Set up 'runner' as an alias to this module
# Skip aliasing if the target is a Mock (happens during tests)
if "runner" not in sys.modules:
    sys.modules["runner"] = sys.modules[__name__]
elif _is_generator_import and sys.modules.get("runner") is not sys.modules[__name__]:
    # Check if 'runner' is a Mock before overriding
    runner_module = sys.modules.get("runner")
    if not (
        hasattr(runner_module, "_mock_name")
        or str(type(runner_module).__name__) == "MagicMock"
    ):
        # Make runner point to generator.runner only if it's not a mock
        sys.modules["runner"] = sys.modules[__name__]


def _ensure_submodule_alias(submodule_name: str):
    """
    Ensure that runner.{submodule} and generator.runner.{submodule}
    point to the same module object.
    """
    gen_key = f"generator.runner.{submodule_name}"
    run_key = f"runner.{submodule_name}"

    # Skip aliasing if either module is a Mock (happens during tests)
    gen_module = sys.modules.get(gen_key)
    run_module = sys.modules.get(run_key)

    # Check if either is a Mock object
    if gen_module is not None and hasattr(gen_module, "_mock_name"):
        return  # Skip aliasing for mocked modules
    if run_module is not None and hasattr(run_module, "_mock_name"):
        return  # Skip aliasing for mocked modules

    if gen_key in sys.modules and run_key not in sys.modules:
        sys.modules[run_key] = sys.modules[gen_key]
    elif run_key in sys.modules and gen_key not in sys.modules:
        sys.modules[gen_key] = sys.modules[run_key]


# FIX: Added missing typing imports
from typing import Any, Callable, Dict, List, Optional, Union

# Detect pytest / testing early & reliably
TESTING = (
    os.getenv("TESTING") == "1"
    or "pytest" in sys.modules
    or os.getenv("PYTEST_CURRENT_TEST") is not None
    or os.getenv("PYTEST_ADDOPTS") is not None
)


# --- FIX: Create a custom registry class that allows attribute setting ---
class FileHandlerRegistry(Dict[str, Callable[..., Any]]):
    """A dictionary subclass used to store file handlers, allowing extensions to be stored as attributes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._extensions: Dict[str, List[str]] = {}  # Separate attribute for extensions

    def get_extensions(self) -> Dict[str, List[str]]:
        """Utility method required by runner_file_utils.py"""
        return self._extensions


# FIX: Initialize FILE_HANDLERS with the custom class
FILE_HANDLERS: FileHandlerRegistry = FileHandlerRegistry()


def register_file_handler(mime_type: str, extensions: List[str]):
    """Registers a function to handle loading a specific mime type."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        FILE_HANDLERS[mime_type] = func
        # FIX: Directly use the internal _extensions attribute on the class instance
        FILE_HANDLERS._extensions[mime_type] = extensions
        return func

    return decorator


# --- FIX: ADDED MISSING SECURITY REGISTRIES ---
REDACTORS: Dict[str, Callable[..., Any]] = {}
ENCRYPTORS: Dict[str, Callable[..., Any]] = {}
DECRYPTORS: Dict[str, Callable[..., Any]] = {}


def register_redactor(name: str, func: Callable[..., Any]):
    """Registers a redaction function."""
    REDACTORS[name] = func
    return func


def register_encryptor(name: str, func: Callable[..., Any]):
    """Registers an encryption function."""
    ENCRYPTORS[name] = func
    return func


def register_decryptor(name: str, func: Callable[..., Any]):
    """Registers a decryption function."""
    DECRYPTORS[name] = func
    return func


# --- END FIX ---


# --- FIX: ADDED MISSING SUMMARIZER REGISTRY ---
# This is needed by summarize_utils.py
class Registry:
    """Generic registry class for features like summarizers."""

    def __init__(self):
        self._items: Dict[str, Callable[..., Any]] = {}

    def register(self, name: str, item: Callable[..., Any]):
        self._items[name] = item

    def get(self, name: str) -> Optional[Callable[..., Any]]:
        return self._items.get(name)

    def clear(self):
        self._items.clear()

    def get_all(self) -> List[str]:
        return list(self._items.keys())

    # FIX: Add attribute-style access for convenience
    def __getitem__(self, key: str) -> Callable[..., Any]:
        return self._items[key]

    def __setitem__(self, key: str, value: Callable[..., Any]):
        self._items[key] = value


SUMMARIZERS = Registry()


# FIX: Add registration function for Summarizers as required
def register_summarizer(name: str):
    """Registers a summarization function."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        SUMMARIZERS.register(name, func)
        return func

    return decorator


# --- END FIX ---

# --- CIRCULAR IMPORT FIX: Import runner_logging BEFORE runner_core ---
# runner_core imports from runner_parsers, which imports from runner_logging.
# We must ensure runner_logging is initialized first to break the circular import.
# The import is for side-effect (module initialization) only - no variable is used.
try:
    from . import runner_logging  # noqa: F401 - imported for side effects
    _ensure_submodule_alias("runner_logging")
except ImportError as _logging_init_err:
    # Log the error but continue - fallback logging will be handled by runner_parsers
    import logging as _init_logging
    _init_logging.getLogger(__name__).debug(
        f"Early runner_logging import failed (fallback logging available): {_logging_init_err}"
    )

# --- CRITICAL FIX: ALWAYS IMPORT SANDBOX FUNCTIONS ---
# REMOVED THE "if not TESTING:" CONDITION THAT WAS BREAKING IMPORTS
try:
    from .runner_core import run_stress_tests, run_tests_in_sandbox, run_tests
    from .runner_mutation import mutation_test, property_based_test
except ImportError as e:
    import logging

    _logger = logging.getLogger(__name__)
    _logger.error(f"Failed to import sandbox functions from runner_core: {e}")

    # Define fallback stub functions that raise meaningful exceptions
    async def run_tests_in_sandbox(*args, **kwargs):
        _logger.warning(
            "run_tests_in_sandbox called but runner_core is not available - raising NotImplementedError"
        )
        raise NotImplementedError(
            "run_tests_in_sandbox is not available. "
            "The runner_core module failed to import. "
            "This functionality requires proper installation of the runner module."
        )

    async def run_stress_tests(*args, **kwargs):
        _logger.warning(
            "run_stress_tests called but runner_core is not available - raising NotImplementedError"
        )
        raise NotImplementedError(
            "run_stress_tests is not available. "
            "The runner_core module failed to import. "
            "This functionality requires proper installation of the runner module."
        )

    async def run_tests(*args, **kwargs):
        _logger.warning(
            "run_tests called but runner_core is not available - raising NotImplementedError"
        )
        raise NotImplementedError(
            "run_tests is not available. "
            "The runner_core module failed to import. "
            "This functionality requires proper installation of the runner module."
        )

    async def mutation_test(*args, **kwargs):
        _logger.warning("mutation_test called but runner_mutation is not available")
        raise NotImplementedError("mutation_test is not available.")

    async def property_based_test(*args, **kwargs):
        _logger.warning("property_based_test called but runner_mutation is not available")
        raise NotImplementedError("property_based_test is not available.")


__all__ = [
    "TESTING",
    "FILE_HANDLERS",
    "register_file_handler",
    "REDACTORS",
    "register_redactor",
    "ENCRYPTORS",
    "register_encryptor",
    "DECRYPTORS",
    "register_decryptor",
    "SUMMARIZERS",
    "register_summarizer",  # Added summarizer registration function
    "run_tests_in_sandbox",  # NEW: Export for testgen_validator
    "run_stress_tests",  # NEW: Export for testgen_validator
    "run_tests",  # NEW: Export for critique_agent
    "mutation_test",  # NEW: Export for mutation testing
    "property_based_test",  # NEW: Export for property-based testing
    "runner_metrics",  # NEW: Export for metrics module access
]

# Import tracer for OpenTelemetry support
try:
    from .runner_logging import tracer

    __all__.append("tracer")
except ImportError:
    # Create a no-op tracer for testing environments
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer(__name__)
    except ImportError:
        # Fallback: create a minimal no-op tracer stub
        class _NoOpSpan:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def set_attribute(self, *args, **kwargs):
                pass

            def add_event(self, *args, **kwargs):
                pass

        class _NoOpTracer:
            def start_as_current_span(self, name, **kwargs):
                return _NoOpSpan()

            def start_span(self, name, **kwargs):
                return _NoOpSpan()

        tracer = _NoOpTracer()
    __all__.append("tracer")

# --- Backwards compatibility aliases ---
import sys as _sys

# NEW: Import the feedback_handlers module for aliasing
# NEW: Import the logging module for aliasing
# NEW: Import the errors module for aliasing
# NEW: Import the contracts module for aliasing
# FIX: Wrap imports in try-except to handle circular import during initial module load
_runner_alerting = None
_runner_feedback_handlers = None
_runner_config = None
_runner_contracts = None
_runner_core = None
_runner_errors = None
_runner_logging = None
_runner_metrics = None
_runner_providers = None

try:
    # FIX: Import order is critical to avoid circular imports
    # runner_logging MUST be imported before runner_core because runner_core
    # imports from runner_logging and needs it to be fully initialized.
    # Similarly, modules are ordered by their dependencies.

    # Level 1: No internal runner dependencies (safe to import first)
    from . import alerting as _runner_alerting
    _ensure_submodule_alias("alerting")

    from . import providers as _runner_providers
    _ensure_submodule_alias("providers")

    from . import runner_contracts as _runner_contracts
    _ensure_submodule_alias("runner_contracts")

    # Level 2: runner_errors depends on runner_security_utils
    # but runner_security_utils has fallbacks for circular imports
    from . import runner_errors as _runner_errors
    _ensure_submodule_alias("runner_errors")

    # Level 3: runner_config depends on runner_errors
    from . import runner_config as _runner_config
    _ensure_submodule_alias("runner_config")

    # Level 4: runner_logging depends on pydantic (external) only now
    from . import runner_logging as _runner_logging
    _ensure_submodule_alias("runner_logging")

    # Level 5: runner_metrics can be imported after runner_logging
    from . import runner_metrics as _runner_metrics
    _ensure_submodule_alias("runner_metrics")

    # Level 6: feedback_handlers may depend on runner_logging
    from . import feedback_handlers as _runner_feedback_handlers
    _ensure_submodule_alias("feedback_handlers")

    # Level 7: runner_security_utils has function-level imports from runner_logging
    from . import runner_security_utils as _runner_security_utils
    _ensure_submodule_alias("runner_security_utils")

    # Level 8: runner_core depends on most other modules
    # Import it last to ensure all dependencies are available
    from . import runner_core as _runner_core
    _ensure_submodule_alias("runner_core")

except ImportError:
    # Circular import during initial load - modules will be available later
    # when accessed directly (e.g., from runner.alerting import send_alert)
    pass

# Backwards compatibility aliases so older imports used by tests/clients still work.
# Allows `from runner.config import ...` to resolve to `runner.runner_config`
if _runner_config is not None and "runner.config" not in _sys.modules:
    _sys.modules["runner.config"] = _runner_config

# Allows `from runner.core import ...` to resolve to `runner.runner_core`
if _runner_core is not None and "runner.core" not in _sys.modules:
    _sys.modules["runner.core"] = _runner_core

# NEW: Allows `from runner.contracts import ...` to resolve to `runner.runner_contracts`
if _runner_contracts is not None and "runner.contracts" not in _sys.modules:
    _sys.modules["runner.contracts"] = _runner_contracts

# NEW: Allows `from runner.errors import ...` to resolve to `runner.runner_errors`
if _runner_errors is not None and "runner.errors" not in _sys.modules:
    _sys.modules["runner.errors"] = _runner_errors

# NEW: Allows `from runner.logging import ...` to resolve to `runner.runner_logging`
if _runner_logging is not None and "runner.logging" not in _sys.modules:
    _sys.modules["runner.logging"] = _runner_logging

# NEW: Allows `from runner.feedback_handlers import ...` to resolve to `runner.runner_feedback_handlers`
if (
    _runner_feedback_handlers is not None
    and "runner.feedback_handlers" not in _sys.modules
):
    _sys.modules["runner.feedback_handlers"] = _runner_feedback_handlers

# NEW: Allows `from runner.alerting import ...` to resolve to the alerting module
if _runner_alerting is not None and "runner.alerting" not in _sys.modules:
    _sys.modules["runner.alerting"] = _runner_alerting

# NEW: Allows `from runner.metrics import ...` to resolve to `runner.runner_metrics`
if _runner_metrics is not None and "runner.metrics" not in _sys.modules:
    _sys.modules["runner.metrics"] = _runner_metrics

# NEW: Allows `from runner.providers import ...` to resolve to the providers subpackage
if _runner_providers is not None and "runner.providers" not in _sys.modules:
    _sys.modules["runner.providers"] = _runner_providers

# Public aliases for direct module imports (e.g., from generator.runner import runner_metrics)
runner_metrics = _runner_metrics


# --- Import hook to support runner.providers imports ---
# When tests do `from runner.providers import ...`, Python needs to find the providers module.
# Since runner is an alias to generator.runner, we need to redirect these imports.
import importlib.abc
import importlib.machinery
import importlib.util


class RunnerSubmoduleLoader(importlib.abc.Loader):
    """Loader that creates an alias after loading the actual module."""

    def __init__(self, origin_name):
        self.origin_name = origin_name

    def exec_module(self, module):
        # Module is already loaded, just ensure the alias exists
        pass

    def create_module(self, spec):
        # Return the already-loaded generator.runner.* module
        return _sys.modules.get(self.origin_name)


class RunnerSubmoduleFinder(importlib.abc.MetaPathFinder):
    """
    Finds runner.providers (and similar) submodules by redirecting to generator.runner.providers.
    """

    def find_spec(self, fullname, path, target=None):
        # Only handle runner.providers and runner.providers.*
        if fullname == "runner.providers" or fullname.startswith("runner.providers."):
            # Convert to generator.runner.providers
            origin_name = fullname.replace("runner.", "generator.runner.", 1)

            # Check if the origin module exists or can be found
            if origin_name in _sys.modules:
                # Module already loaded, just create alias
                _sys.modules[fullname] = _sys.modules[origin_name]
                return None  # Signal that we handled it

            # Try to find the origin spec
            try:
                origin_spec = importlib.util.find_spec(origin_name)
                if origin_spec is not None:
                    # Import the origin module first
                    origin_module = importlib.util.module_from_spec(origin_spec)
                    _sys.modules[origin_name] = origin_module
                    origin_spec.loader.exec_module(origin_module)

                    # Now create the alias
                    _sys.modules[fullname] = origin_module

                    # Ensure submodules are also imported if this is a package
                    if origin_spec.submodule_search_locations:
                        # Handle submodule aliasing for packages
                        _ensure_submodule_alias(fullname.replace("runner.", ""))

                    # Return None to indicate the module is now in sys.modules
                    return None
            except (ImportError, ValueError, AttributeError, ModuleNotFoundError):
                pass

        # Not our concern
        return None


# Install the finder
if not any(isinstance(f, RunnerSubmoduleFinder) for f in _sys.meta_path):
    _sys.meta_path.insert(0, RunnerSubmoduleFinder())
