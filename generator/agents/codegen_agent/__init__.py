# generator/agents/codegen_agent/__init__.py
"""
Codegen Agent - Code Generation Agent for the Code Factory Platform.
Handles automated code generation from natural language requirements.
"""

import os
import sys
from pathlib import Path

# Testing environment detection
TESTING = (
    os.getenv("TESTING") == "1" 
    or "pytest" in sys.modules 
    or os.getenv("PYTEST_CURRENT_TEST") is not None
)

# Add project root to path if needed
if TESTING or True:  # Always ensure proper imports
    project_root = Path(__file__).parent.parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

try:
    # Try importing from the runner foundation
    from runner.llm_client import (
        call_llm_api, 
        LLMError
    )
    from runner.runner_config import load_config, ConfigurationError
    from runner.runner_logging import logger, log_audit_event
    from runner.runner_security_utils import redact_secrets
    from runner.runner_errors import ValidationError, RunnerError
    
except ImportError as e:
    # Fallback for testing or when runner not fully available
    import logging
    logger = logging.getLogger(__name__)
    logger.warning(f"Runner imports not available: {e}")
    
    # Define mock implementations for testing
    async def call_llm_api(*args, **kwargs):
        return {"content": "Mock generated code", "model": "test"}
    
    def load_config():
        class MockConfig:
            llm_provider = "openai"
            max_tokens = 4000
            temperature = 0.1
        return MockConfig()
    
    def log_audit_event(*args, **kwargs):
        pass
    
    def redact_secrets(text):
        return text
    
    class LLMError(Exception):
        pass
    
    class ConfigurationError(Exception):
        pass
    
    class ValidationError(Exception):
        pass
    
    class RunnerError(Exception):
        pass

# Import the available classes from the codegen_agent module
from .codegen_agent import (
    CodeGenConfig,
    EnsembleGenerationError,
    SecurityUtils,
    # Add other classes as needed
)

# Export main symbols (removed CodegenAgent since it doesn't exist)
__all__ = [
    "CodeGenConfig",
    "EnsembleGenerationError", 
    "SecurityUtils",
    "call_llm_api",
    "LLMError",
    "ConfigurationError", 
    "ValidationError",
    "RunnerError"
]

__version__ = "1.0.0"