# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# __init__.py
# Import components gracefully to handle missing dependencies
# This prevents DLL initialization errors on Windows during test collection

# Initialize module-level exports as None
DeployAgent = None
DeployPromptAgent = None
HandlerRegistry = None
monitor_and_scan_code = None
parse_llm_response = None
ValidatorRegistry = None
DockerValidator = None
HelmValidator = None

# Availability flags for checking which components loaded successfully
_DEPLOY_AGENT_AVAILABLE = False
_DEPLOY_PROMPT_AVAILABLE = False
_DEPLOY_RESPONSE_HANDLER_AVAILABLE = False
_DEPLOY_VALIDATOR_AVAILABLE = False

# Try to import deploy_validator (minimal dependencies)
try:
    from .deploy_validator import DockerValidator, HelmValidator, ValidatorRegistry

    _DEPLOY_VALIDATOR_AVAILABLE = True
except (ImportError, OSError):
    # OSError catches DLL initialization failures on Windows
    pass

# Try to import deploy_response_handler
try:
    from .deploy_response_handler import (
        HandlerRegistry,
        monitor_and_scan_code,
        parse_llm_response,
    )

    _DEPLOY_RESPONSE_HANDLER_AVAILABLE = True
except (ImportError, OSError):
    # OSError catches DLL initialization failures on Windows
    pass

# Try to import deploy_prompt (has heavy dependencies: torch, transformers)
try:
    from .deploy_prompt import DeployPromptAgent

    _DEPLOY_PROMPT_AVAILABLE = True
except (ImportError, OSError):
    # OSError catches DLL initialization failures on Windows
    # DeployPromptAgent stays None; _DEPLOY_PROMPT_AVAILABLE remains False.
    # Callers must check _DEPLOY_PROMPT_AVAILABLE (or `DeployPromptAgent is not None`)
    # before instantiating.  A stub class is intentionally NOT substituted here
    # because `is not None` guards throughout the codebase would be silently defeated.
    pass

# Try to import deploy_agent
try:
    from .deploy_agent import DeployAgent

    _DEPLOY_AGENT_AVAILABLE = True
except (ImportError, OSError):
    # OSError catches DLL initialization failures on Windows
    pass

__all__ = [
    # Core agents — may be None if their dependencies are not installed.
    # Always check the corresponding _*_AVAILABLE flag before instantiating.
    "DeployAgent",
    "DeployPromptAgent",
    "parse_llm_response",
    "monitor_and_scan_code",
    "HandlerRegistry",
    "ValidatorRegistry",
    "DockerValidator",
    "HelmValidator",
    # Availability sentinels — use these for `is not None` / feature-flag checks.
    "_DEPLOY_AGENT_AVAILABLE",
    "_DEPLOY_PROMPT_AVAILABLE",
    "_DEPLOY_RESPONSE_HANDLER_AVAILABLE",
    "_DEPLOY_VALIDATOR_AVAILABLE",
]
