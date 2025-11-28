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
"""

import logging
import warnings

logger = logging.getLogger(__name__)

# Track which agents are available
_AVAILABLE_AGENTS = {}

# Try to import each agent with proper error handling
# codegen_agent
try:
    from .codegen_agent import CodeGenConfig, SecurityUtils
    _AVAILABLE_AGENTS['codegen'] = True
except ImportError as e:
    logger.debug(f"codegen_agent not available: {e}")
    _AVAILABLE_AGENTS['codegen'] = False
    CodeGenConfig = None
    SecurityUtils = None

# critique_agent
try:
    from .critique_agent import CritiqueConfig, orchestrate_critique_pipeline
    _AVAILABLE_AGENTS['critique'] = True
except ImportError as e:
    logger.debug(f"critique_agent not available: {e}")
    _AVAILABLE_AGENTS['critique'] = False
    CritiqueConfig = None
    orchestrate_critique_pipeline = None

# testgen_agent - has heavy dependencies (presidio, spacy, torch)
try:
    from .testgen_agent import TestGenAgent, Policy
    _AVAILABLE_AGENTS['testgen'] = True
except ImportError as e:
    logger.debug(f"testgen_agent not available: {e}")
    _AVAILABLE_AGENTS['testgen'] = False
    TestGenAgent = None
    Policy = None

# deploy_agent
try:
    from .deploy_agent import DeployAgent, DeployConfig
    _AVAILABLE_AGENTS['deploy'] = True
except ImportError as e:
    logger.debug(f"deploy_agent not available: {e}")
    _AVAILABLE_AGENTS['deploy'] = False
    DeployAgent = None
    DeployConfig = None

# docgen_agent
try:
    from .docgen_agent import DocgenAgent, DocgenConfig
    _AVAILABLE_AGENTS['docgen'] = True
except ImportError as e:
    logger.debug(f"docgen_agent not available: {e}")
    _AVAILABLE_AGENTS['docgen'] = False
    DocgenAgent = None
    DocgenConfig = None


def get_available_agents():
    """Returns a dict of agent names and their availability status."""
    return _AVAILABLE_AGENTS.copy()


def is_agent_available(agent_name: str) -> bool:
    """Check if a specific agent is available."""
    return _AVAILABLE_AGENTS.get(agent_name, False)


__all__ = [
    # Availability helpers
    'get_available_agents',
    'is_agent_available',
    # codegen_agent exports
    'CodeGenConfig',
    'SecurityUtils',
    # critique_agent exports
    'CritiqueConfig',
    'orchestrate_critique_pipeline',
    # testgen_agent exports
    'TestGenAgent',
    'Policy',
    # deploy_agent exports
    'DeployAgent',
    'DeployConfig',
    # docgen_agent exports
    'DocgenAgent',
    'DocgenConfig',
]

__version__ = "1.0.0"
