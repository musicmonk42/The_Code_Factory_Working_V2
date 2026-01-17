"""
Generator Agents Package
=========================

This package contains specialized AI agents for different aspects of code generation:
- codegen_agent: Code generation from natural language
- critique_agent: Code review and critique
- testgen_agent: Test generation
- deploy_agent: Deployment automation
- docgen_agent: Documentation generation

Each agent integrates with the runner foundation for LLM calls, metrics, and logging.

IMPORTANT: Agent Loading Behavior
---------------------------------
This module implements a **fail-visible** loading pattern:
- All import failures are logged at WARNING level (visible in production logs)
- Import errors are captured with full details for debugging
- The `validate_agents_for_production()` function can be called to enforce
  strict mode where missing critical agents raise an error at startup

In production environments, operators should call `validate_agents_for_production()`
during application startup to ensure all required agents are available before
accepting workflow requests.
"""

import logging
import os
import sys
import traceback
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Environment variable to control strict mode
# When GENERATOR_STRICT_MODE=1, missing agents will raise errors at import time
STRICT_MODE = os.getenv("GENERATOR_STRICT_MODE", "0") == "1"

# Track which agents are available and their import errors
_AVAILABLE_AGENTS: Dict[str, bool] = {}
_AGENT_IMPORT_ERRORS: Dict[str, str] = {}

# Initialize placeholders for exports
CodeGenConfig = None
SecurityUtils = None
CritiqueConfig = None
orchestrate_critique_pipeline = None
TestGenAgent = None
Policy = None
DeployAgent = None
DeployConfig = None
DocgenAgent = None
DocgenConfig = None


def _log_agent_import_failure(agent_name: str, error: Exception) -> None:
    """Log agent import failure with appropriate severity and full context.

    This function ensures import failures are visible in production logs
    rather than being silently swallowed at DEBUG level.
    """
    # Get full traceback for debugging
    tb_str = traceback.format_exc()
    error_msg = f"{type(error).__name__}: {error}"

    # Store the error for later retrieval
    _AGENT_IMPORT_ERRORS[agent_name] = f"{error_msg}\n{tb_str}"

    # Log at WARNING level so it's visible in production
    logger.warning(
        f"Agent '{agent_name}' failed to load and will be unavailable. "
        f"Error: {error_msg}. "
        f"This may cause workflow failures if this agent is required. "
        f"Set GENERATOR_STRICT_MODE=1 to enforce agent availability at startup."
    )

    # In strict mode, also log the full traceback at ERROR level
    if STRICT_MODE or os.getenv("DEBUG", "0") == "1":
        logger.error(f"Full traceback for '{agent_name}' import failure:\n{tb_str}")


def _safe_import_agent(
    module_path: str, agent_name: str, attributes: List[str]
) -> Tuple[bool, Optional[object]]:
    """Safely import an agent module with comprehensive error handling.

    Args:
        module_path: The full module path to import.
        agent_name: The human-readable name of the agent for logging.
        attributes: List of attribute names to extract from the module.

    Returns:
        A tuple of (success: bool, module: Optional[object]).
    """
    try:
        module = __import__(module_path, fromlist=attributes)
        return True, module
    except ImportError as e:
        _log_agent_import_failure(agent_name, e)
        return False, None
    except AttributeError as e:
        _log_agent_import_failure(agent_name, e)
        return False, None
    except OSError as e:
        # OSError can occur with native extension loading failures
        _log_agent_import_failure(agent_name, e)
        return False, None
    except SyntaxError as e:
        # SyntaxError should NOT be silently ignored - it's a code bug
        _log_agent_import_failure(agent_name, e)
        logger.error(
            f"SYNTAX ERROR in agent '{agent_name}': {e}. "
            f"This is a code bug that must be fixed."
        )
        return False, None
    except Exception as e:
        # Catch-all for any other errors, but still log them prominently
        _log_agent_import_failure(agent_name, e)
        logger.error(
            f"Unexpected error loading agent '{agent_name}': {type(e).__name__}: {e}"
        )
        return False, None


# --- Agent Imports with Fail-Visible Pattern ---

# codegen_agent
_success, _codegen_module = _safe_import_agent(
    "generator.agents.codegen_agent", "codegen", ["CodeGenConfig", "SecurityUtils"]
)
if _success and _codegen_module:
    if hasattr(_codegen_module, "CodeGenConfig"):
        CodeGenConfig = _codegen_module.CodeGenConfig
    if hasattr(_codegen_module, "SecurityUtils"):
        SecurityUtils = _codegen_module.SecurityUtils
    _AVAILABLE_AGENTS["codegen"] = True
else:
    _AVAILABLE_AGENTS["codegen"] = False

# critique_agent
_success, _critique_module = _safe_import_agent(
    "generator.agents.critique_agent",
    "critique",
    ["CritiqueConfig", "orchestrate_critique_pipeline"],
)
if _success and _critique_module:
    if hasattr(_critique_module, "CritiqueConfig"):
        CritiqueConfig = _critique_module.CritiqueConfig
    if hasattr(_critique_module, "orchestrate_critique_pipeline"):
        orchestrate_critique_pipeline = _critique_module.orchestrate_critique_pipeline
    _AVAILABLE_AGENTS["critique"] = True
else:
    _AVAILABLE_AGENTS["critique"] = False

# testgen_agent - has heavy dependencies (presidio, spacy, torch)
_success, _testgen_module = _safe_import_agent(
    "generator.agents.testgen_agent", "testgen", ["TestGenAgent", "Policy"]
)
if _success and _testgen_module:
    if hasattr(_testgen_module, "TestGenAgent"):
        TestGenAgent = _testgen_module.TestGenAgent
    if hasattr(_testgen_module, "Policy"):
        Policy = _testgen_module.Policy
    _AVAILABLE_AGENTS["testgen"] = True
else:
    _AVAILABLE_AGENTS["testgen"] = False

# deploy_agent
_success, _deploy_module = _safe_import_agent(
    "generator.agents.deploy_agent", "deploy", ["DeployAgent", "DeployConfig"]
)
if _success and _deploy_module:
    if hasattr(_deploy_module, "DeployAgent"):
        DeployAgent = _deploy_module.DeployAgent
    if hasattr(_deploy_module, "DeployConfig"):
        DeployConfig = _deploy_module.DeployConfig
    _AVAILABLE_AGENTS["deploy"] = True
else:
    _AVAILABLE_AGENTS["deploy"] = False

# docgen_agent
_success, _docgen_module = _safe_import_agent(
    "generator.agents.docgen_agent", "docgen", ["DocgenAgent", "DocgenConfig"]
)
if _success and _docgen_module:
    if hasattr(_docgen_module, "DocgenAgent"):
        DocgenAgent = _docgen_module.DocgenAgent
    if hasattr(_docgen_module, "DocgenConfig"):
        DocgenConfig = _docgen_module.DocgenConfig
    _AVAILABLE_AGENTS["docgen"] = True
else:
    _AVAILABLE_AGENTS["docgen"] = False


# --- Public API Functions ---


def get_available_agents() -> Dict[str, bool]:
    """Returns a dict of agent names and their availability status."""
    return _AVAILABLE_AGENTS.copy()


def is_agent_available(agent_name: str) -> bool:
    """Check if a specific agent is available."""
    return _AVAILABLE_AGENTS.get(agent_name, False)


def get_agent_import_errors() -> Dict[str, str]:
    """Returns a dict of agent names and their import error messages.

    This is useful for debugging and for operators to understand why
    certain agents are not available.
    """
    return _AGENT_IMPORT_ERRORS.copy()


def get_unavailable_agents() -> List[str]:
    """Returns a list of agent names that failed to load."""
    return [name for name, available in _AVAILABLE_AGENTS.items() if not available]


class AgentLoadError(Exception):
    """Raised when required agents fail to load in strict/production mode."""

    pass


def validate_agents_for_production(required_agents: Optional[List[str]] = None) -> None:
    """Validate that all required agents are available.

    This function should be called during application startup in production
    environments to ensure fail-fast behavior rather than discovering
    missing agents during workflow execution.

    Args:
        required_agents: List of agent names that must be available.
                        Defaults to ['codegen', 'critique', 'testgen', 'deploy', 'docgen'].

    Raises:
        AgentLoadError: If any required agent is not available.
    """
    if required_agents is None:
        required_agents = ["codegen", "critique", "testgen", "deploy", "docgen"]

    missing = []
    for agent_name in required_agents:
        if not _AVAILABLE_AGENTS.get(agent_name, False):
            missing.append(agent_name)

    if missing:
        error_details = []
        for agent_name in missing:
            error_msg = _AGENT_IMPORT_ERRORS.get(agent_name, "Unknown error")
            # Get just the first line of the error for the summary
            first_line = error_msg.split("\n")[0]
            error_details.append(f"  - {agent_name}: {first_line}")

        raise AgentLoadError(
            f"Critical agents failed to load: {', '.join(missing)}. "
            f"The generator workflow cannot execute without these agents.\n"
            f"Details:\n" + "\n".join(error_details) + "\n"
            f"Please check dependencies and configuration. "
            f"Full error details available via get_agent_import_errors()."
        )

    logger.info(
        f"All required agents validated successfully: {', '.join(required_agents)}"
    )


# --- Strict Mode Enforcement ---
# If GENERATOR_STRICT_MODE=1, validate agents at import time
if STRICT_MODE:
    try:
        validate_agents_for_production()
    except AgentLoadError as e:
        logger.critical(f"STRICT MODE: {e}")
        # In strict mode, we raise the error to prevent the application from starting
        raise


__all__ = [
    # Availability helpers
    "get_available_agents",
    "is_agent_available",
    "get_agent_import_errors",
    "get_unavailable_agents",
    "validate_agents_for_production",
    "AgentLoadError",
    # codegen_agent exports
    "CodeGenConfig",
    "SecurityUtils",
    # critique_agent exports
    "CritiqueConfig",
    "orchestrate_critique_pipeline",
    # testgen_agent exports
    "TestGenAgent",
    "Policy",
    # deploy_agent exports
    "DeployAgent",
    "DeployConfig",
    # docgen_agent exports
    "DocgenAgent",
    "DocgenConfig",
]

__version__ = "1.0.0"
