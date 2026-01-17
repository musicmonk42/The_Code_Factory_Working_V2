# generator/agents/codegen_agent/__init__.py
"""
Codegen Agent - Code Generation Agent for the Code Factory Platform.
Handles automated code generation from natural language requirements.

IMPORTANT: Mock/Stub Behavior
-----------------------------
This module uses mock implementations for LLM calls and other runner dependencies
ONLY when running in explicit testing mode. In production:
- Set CODEGEN_STRICT_MODE=1 to fail fast if runner dependencies are unavailable
- Mock implementations will log ERROR level warnings, not silently proceed
- call_llm_api mock will raise RuntimeError in non-testing mode

Environment Variables:
- TESTING=1: Enable testing mode with mock implementations
- CODEGEN_STRICT_MODE=1: Raise errors if runner dependencies are unavailable
- PYTEST_CURRENT_TEST: Auto-detected by pytest
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Get logger first so we can log during import
_module_logger = logging.getLogger(__name__)

# Environment detection
TESTING = (
    os.getenv("TESTING") == "1"
    or "pytest" in sys.modules
    or os.getenv("PYTEST_CURRENT_TEST") is not None
)

# Strict mode: fail if runner dependencies are unavailable
STRICT_MODE = os.getenv("CODEGEN_STRICT_MODE", "0") == "1"

# Add project root to path if needed
project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


class RunnerDependencyUnavailableError(Exception):
    """Raised when a critical runner dependency is unavailable in strict mode.

    This error indicates that the code generation agent cannot function properly
    without the runner foundation. In production, this should halt startup.
    """

    pass


class MockLLMUsageError(RuntimeError):
    """Raised when mock LLM is used in non-testing mode.

    This error prevents silent failures where code generation appears to work
    but actually produces useless mock output.
    """

    pass


# Track what we're using
_USING_MOCK_LLM = False
_USING_MOCK_CONFIG = False
_RUNNER_IMPORT_ERROR: Optional[str] = None

try:
    # Try importing from the runner foundation
    from runner.llm_client import LLMError, call_llm_api
    from runner.runner_config import ConfigurationError, load_config
    from runner.runner_errors import RunnerError, ValidationError
    from runner.runner_logging import log_audit_event, logger
    from runner.runner_security_utils import redact_secrets

    _module_logger.info("Runner foundation imports successful")

except ImportError as e:
    _RUNNER_IMPORT_ERROR = str(e)

    # In strict mode, fail immediately
    if STRICT_MODE:
        _module_logger.critical(
            f"STRICT MODE: Runner imports failed and CODEGEN_STRICT_MODE=1. "
            f"Cannot proceed without runner foundation. Error: {e}"
        )
        raise RunnerDependencyUnavailableError(
            f"Critical runner dependencies unavailable: {e}. "
            f"The codegen agent requires the runner foundation for LLM calls, "
            f"logging, and security utilities. Please install the runner package "
            f"or disable strict mode for development/testing."
        ) from e

    # Not in strict mode - use fallbacks with appropriate warnings
    logger = _module_logger

    if TESTING:
        logger.warning(
            f"TESTING MODE: Runner imports not available, using mock implementations. "
            f"Error: {e}"
        )
    else:
        # Not testing AND not strict mode - log at ERROR level
        logger.error(
            f"PRODUCTION WARNING: Runner imports not available ({e}). "
            f"Using mock implementations which will NOT generate real code. "
            f"Set CODEGEN_STRICT_MODE=1 to fail fast in production."
        )

    _USING_MOCK_LLM = True
    _USING_MOCK_CONFIG = True

    # Define mock implementations with proper guardrails
    async def call_llm_api(*args, **kwargs) -> Dict[str, Any]:
        """Mock LLM API that raises an error in non-testing mode.

        In testing mode, returns mock data.
        In non-testing mode, raises MockLLMUsageError to prevent silent failures.
        """
        if not TESTING:
            raise MockLLMUsageError(
                "Mock LLM API called in non-testing mode. This would produce "
                "useless 'Mock generated code' output instead of real code generation. "
                "Either install the runner foundation or set TESTING=1 for development. "
                f"Original import error: {_RUNNER_IMPORT_ERROR}"
            )

        logger.warning("MOCK: call_llm_api returning mock response (TESTING mode)")
        return {
            "content": "# Mock generated code - TESTING MODE ONLY\n"
            "# This is not real generated code\n"
            "def mock_function():\n"
            "    return 'mock'",
            "model": "mock-test-model",
            "_mock": True,
            "_warning": "This is mock output from testing mode",
        }

    def load_config():
        """Mock config loader for testing."""
        if not TESTING:
            logger.error(
                "Mock load_config called in non-testing mode. "
                "Configuration may not be properly loaded."
            )

        class MockConfig:
            llm_provider = "mock"
            max_tokens = 4000
            temperature = 0.1
            _mock = True

        return MockConfig()

    def log_audit_event(*args, **kwargs):
        """Mock audit event logger - logs warning that auditing is disabled."""
        if not TESTING:
            logger.error(
                "AUDIT DISABLED: log_audit_event mock called in non-testing mode. "
                "Audit events are NOT being recorded."
            )

    def redact_secrets(text: str) -> str:
        """Mock secret redaction - returns text unchanged with warning."""
        if not TESTING:
            logger.error(
                "SECURITY WARNING: redact_secrets mock called in non-testing mode. "
                "Secrets may NOT be properly redacted."
            )
        return text

    class LLMError(Exception):
        """Mock LLM error class."""

        pass

    class ConfigurationError(Exception):
        """Mock configuration error class."""

        pass

    class ValidationError(Exception):
        """Mock validation error class."""

        pass

    class RunnerError(Exception):
        """Mock runner error class."""

        pass


# Import the available classes from the codegen_agent module
from .codegen_agent import (
    CodeGenConfig,
    EnsembleGenerationError,
    SecurityUtils,
)


def is_using_mock_llm() -> bool:
    """Check if the module is using mock LLM implementations.

    Returns:
        True if mock implementations are in use, False if real runner is available.
    """
    return _USING_MOCK_LLM


def get_runner_status() -> Dict[str, Any]:
    """Get the status of runner dependencies.

    Returns a dict with:
        - available: bool - True if real runner is available
        - using_mock_llm: bool
        - using_mock_config: bool
        - testing_mode: bool
        - strict_mode: bool
        - import_error: Optional[str] - Error message if import failed
    """
    return {
        "available": not _USING_MOCK_LLM,
        "using_mock_llm": _USING_MOCK_LLM,
        "using_mock_config": _USING_MOCK_CONFIG,
        "testing_mode": TESTING,
        "strict_mode": STRICT_MODE,
        "import_error": _RUNNER_IMPORT_ERROR,
    }


# Export main symbols
__all__ = [
    "CodeGenConfig",
    "EnsembleGenerationError",
    "SecurityUtils",
    "call_llm_api",
    "LLMError",
    "ConfigurationError",
    "ValidationError",
    "RunnerError",
    "RunnerDependencyUnavailableError",
    "MockLLMUsageError",
    "is_using_mock_llm",
    "get_runner_status",
    "TESTING",
    "STRICT_MODE",
]

__version__ = "1.0.0"
