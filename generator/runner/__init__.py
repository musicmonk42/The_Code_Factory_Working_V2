# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Runner package entry point.
Centralises registries, OTEL tracer, and re-exports public symbols.

Module aliasing
---------------
When this package is installed under ``generator.runner`` it ensures that bare
``runner.*`` imports (used by legacy code and some third-party plugins) resolve
to the *same* module objects.  The aliasing is performed with a strict identity
check so that a pre-existing ``runner`` entry in ``sys.modules`` (e.g. an
unrelated package or a test mock) is **never silently overwritten**.
"""

# generator/runner/__init__.py
import logging
import os
import sys

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module aliasing — backward compatibility
# ---------------------------------------------------------------------------
# Only register this package as the bare ``runner`` name when:
#   1. ``runner`` is not yet registered, OR
#   2. the registered object is the same package under a different key
#      (both ``generator.runner`` and ``runner`` are already pointing here).
#
# We explicitly skip aliasing when ``runner`` is already occupied by a
# *different* object (another package, a mock, etc.) to avoid identity
# collisions that break isinstance() checks and duplicate metric registration.

_is_generator_import = __name__ == "generator.runner"
_existing_runner = sys.modules.get("runner")

if _existing_runner is None:
    sys.modules["runner"] = sys.modules[__name__]
elif _is_generator_import and _existing_runner is not sys.modules[__name__]:
    _is_mock = (
        hasattr(_existing_runner, "_mock_name")
        or type(_existing_runner).__name__ in ("MagicMock", "NonCallableMagicMock")
    )
    if _is_mock:
        # Leave test mocks untouched; they own the ``runner`` namespace during tests.
        _logger.debug(
            "runner/__init__: 'runner' in sys.modules is a mock — skipping alias."
        )
    elif getattr(_existing_runner, "__file__", None) == getattr(
        sys.modules[__name__], "__file__", object()
    ):
        # Same file on disk, different sys.modules key — harmless, unify them.
        sys.modules["runner"] = sys.modules[__name__]
    else:
        # Different package already registered as 'runner'; do NOT overwrite.
        _logger.warning(
            "runner/__init__: 'runner' is already registered in sys.modules as a "
            "different object (%r).  Module aliasing skipped to prevent identity "
            "collision.  If bare 'runner.*' imports fail, check your PYTHONPATH.",
            _existing_runner,
        )


def _ensure_submodule_alias(submodule_name: str) -> None:
    """Ensure ``runner.<submodule>`` and ``generator.runner.<submodule>`` are the
    same object in ``sys.modules``.

    Aliasing is skipped when either slot already holds a mock or when both
    slots are occupied by *different* non-mock objects (which would indicate a
    genuine conflict that silently overwriting would hide).
    """
    gen_key = f"generator.runner.{submodule_name}"
    run_key = f"runner.{submodule_name}"

    gen_module = sys.modules.get(gen_key)
    run_module = sys.modules.get(run_key)

    def _is_mock(m: object) -> bool:
        return m is not None and (
            hasattr(m, "_mock_name")
            or type(m).__name__ in ("MagicMock", "NonCallableMagicMock")
        )

    if _is_mock(gen_module) or _is_mock(run_module):
        return  # Leave test mocks untouched.

    if gen_module is not None and run_module is None:
        sys.modules[run_key] = gen_module
    elif run_module is not None and gen_module is None:
        sys.modules[gen_key] = run_module
    elif gen_module is not None and run_module is not None and gen_module is not run_module:
        # Both slots occupied by different real objects — do NOT silently unify.
        _logger.warning(
            "_ensure_submodule_alias: '%s' and '%s' point to different objects; "
            "skipping alias to prevent identity collision.",
            gen_key,
            run_key,
        )


from typing import Any, Callable, Dict, List, Optional, Union

# TESTING is authoritative in runner_base_types (no intra-package imports there).
# Re-export it here so that ``from runner import TESTING`` continues to work.
from .runner_base_types import TESTING  # noqa: F401


# --- Registry class ---
class FileHandlerRegistry(Dict[str, Callable[..., Any]]):
    """A dictionary subclass used to store file handlers, allowing extensions to be stored as attributes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._extensions: Dict[str, List[str]] = {}  # Separate attribute for extensions

    def get_extensions(self) -> Dict[str, List[str]]:
        """Utility method required by runner_file_utils.py"""
        return self._extensions


# Initialize FILE_HANDLERS with the custom class
FILE_HANDLERS: FileHandlerRegistry = FileHandlerRegistry()


def register_file_handler(mime_type: str, extensions: List[str]):
    """Registers a function to handle loading a specific mime type."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        FILE_HANDLERS[mime_type] = func
        # Directly use the internal _extensions attribute on the class instance
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
from shared.registry import Registry  # noqa: E402


SUMMARIZERS = Registry()


# Add registration function for Summarizers as required
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
    _logger.debug(
        "Early runner_logging import failed (fallback logging available): %s",
        _logging_init_err,
    )

# --- CRITICAL FIX: ALWAYS IMPORT SANDBOX FUNCTIONS ---
# REMOVED THE "if not TESTING:" CONDITION THAT WAS BREAKING IMPORTS
try:
    from .runner_core import run_stress_tests, run_tests_in_sandbox, run_tests
    from .runner_mutation import mutation_test, property_based_test
except ImportError as e:
    _logger.error("Failed to import sandbox functions from runner_core: %s", e)

    # Define fallback stub functions that raise meaningful exceptions
    async def run_tests_in_sandbox(*args, **kwargs):
        _logger.critical(
            "run_tests_in_sandbox fallback stub called — runner_core failed to import. "
            "Check import chain for errors."
        )
        raise NotImplementedError(
            "run_tests_in_sandbox is not available. "
            "The runner_core module failed to import. "
            "This functionality requires proper installation of the runner module."
        )

    async def run_stress_tests(*args, **kwargs):
        _logger.critical(
            "run_stress_tests fallback stub called — runner_core failed to import. "
            "Check import chain for errors."
        )
        raise NotImplementedError(
            "run_stress_tests is not available. "
            "The runner_core module failed to import. "
            "This functionality requires proper installation of the runner module."
        )

    async def run_tests(*args, **kwargs):
        _logger.critical(
            "run_tests fallback stub called — runner_core failed to import. "
            "Check import chain for errors."
        )
        raise NotImplementedError(
            "run_tests is not available. "
            "The runner_core module failed to import. "
            "This functionality requires proper installation of the runner module."
        )

    async def mutation_test(*args, **kwargs):
        _logger.critical("mutation_test fallback stub called — runner_mutation failed to import.")
        raise NotImplementedError("mutation_test is not available.")

    async def property_based_test(*args, **kwargs):
        _logger.critical("property_based_test fallback stub called — runner_mutation failed to import.")
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
    "call_llm_api",  # Re-export for backward-compatible `from generator.runner import call_llm_api`
]

# Re-export call_llm_api from llm_client so that legacy callers using
# `from generator.runner import call_llm_api` continue to work.
try:
    from .llm_client import call_llm_api  # noqa: F401
except ImportError as _llm_import_err:
    _logger.warning(
        "runner/__init__: call_llm_api not importable from llm_client (%s); "
        "LLM-based fix generation will be unavailable.",
        _llm_import_err,
    )

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

            def set_status(self, *args, **kwargs):
                pass

            def record_exception(self, *args, **kwargs):
                pass

            def end(self, *args, **kwargs):
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
# Wrap imports in try-except to handle circular import during initial module load
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
    # Import order is critical to avoid circular imports
    # runner_logging MUST be imported before runner_core because runner_core
    # imports from runner_logging and needs it to be fully initialized.
    # Similarly, modules are ordered by their dependencies.

    # Level 1: No internal runner dependencies (safe to import first)
    from . import alerting as _runner_alerting
    _ensure_submodule_alias("alerting")
except ImportError as _e:
    _logger.warning("Failed to import alerting: %s", _e)

try:
    from . import providers as _runner_providers
    _ensure_submodule_alias("providers")
except ImportError as _e:
    _logger.warning("Failed to import providers: %s", _e)

try:
    from . import runner_contracts as _runner_contracts
    _ensure_submodule_alias("runner_contracts")
except ImportError as _e:
    _logger.warning("Failed to import runner_contracts: %s", _e)

try:
    # Level 2: runner_errors depends on runner_security_utils
    # but runner_security_utils has fallbacks for circular imports
    from . import runner_errors as _runner_errors
    _ensure_submodule_alias("runner_errors")
except ImportError as _e:
    _logger.warning("Failed to import runner_errors: %s", _e)

try:
    # Level 3: runner_config depends on runner_errors
    from . import runner_config as _runner_config
    _ensure_submodule_alias("runner_config")
except ImportError as _e:
    _logger.warning("Failed to import runner_config: %s", _e)

try:
    # Level 4: runner_logging depends on pydantic (external) only now
    from . import runner_logging as _runner_logging
    _ensure_submodule_alias("runner_logging")
except ImportError as _e:
    _logger.warning("Failed to import runner_logging: %s", _e)

try:
    # Level 5: runner_metrics can be imported after runner_logging
    from . import runner_metrics as _runner_metrics
    _ensure_submodule_alias("runner_metrics")
except ImportError as _e:
    _logger.warning("Failed to import runner_metrics: %s", _e)

try:
    # Level 6: feedback_handlers may depend on runner_logging
    from . import feedback_handlers as _runner_feedback_handlers
    _ensure_submodule_alias("feedback_handlers")
except ImportError as _e:
    _logger.warning("Failed to import feedback_handlers: %s", _e)

try:
    # Level 7: runner_security_utils has function-level imports from runner_logging
    from . import runner_security_utils as _runner_security_utils
    _ensure_submodule_alias("runner_security_utils")
except ImportError as _e:
    _logger.warning("Failed to import runner_security_utils: %s", _e)

try:
    # Level 8: runner_core depends on most other modules
    # Import it last to ensure all dependencies are available
    from . import runner_core as _runner_core
    _ensure_submodule_alias("runner_core")
except ImportError as _e:
    _logger.warning("Failed to import runner_core: %s", _e)

try:
    # Level 9: language_utils depends on runner_parsers (which is imported by runner_core)
    # Import after runner_core to ensure runner_parsers is available
    from . import language_utils as _runner_language_utils
    _ensure_submodule_alias("language_utils")
except ImportError as _e:
    _logger.warning("Failed to import language_utils: %s", _e)

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
