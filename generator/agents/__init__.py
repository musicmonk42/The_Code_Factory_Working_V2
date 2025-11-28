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

logger = logging.getLogger(__name__)

# Track which agents are available
_AVAILABLE_AGENTS = {}

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

# Try to import each agent with proper error handling
# codegen_agent
try:
    _codegen_module = __import__('generator.agents.codegen_agent', fromlist=['CodeGenConfig', 'SecurityUtils'])
    if hasattr(_codegen_module, 'CodeGenConfig'):
        CodeGenConfig = _codegen_module.CodeGenConfig
    if hasattr(_codegen_module, 'SecurityUtils'):
        SecurityUtils = _codegen_module.SecurityUtils
    _AVAILABLE_AGENTS['codegen'] = True
except (ImportError, AttributeError) as e:
    logger.debug(f"codegen_agent not available: {e}")
    _AVAILABLE_AGENTS['codegen'] = False

# critique_agent
try:
    _critique_module = __import__('generator.agents.critique_agent', fromlist=['CritiqueConfig', 'orchestrate_critique_pipeline'])
    if hasattr(_critique_module, 'CritiqueConfig'):
        CritiqueConfig = _critique_module.CritiqueConfig
    if hasattr(_critique_module, 'orchestrate_critique_pipeline'):
        orchestrate_critique_pipeline = _critique_module.orchestrate_critique_pipeline
    _AVAILABLE_AGENTS['critique'] = True
except (ImportError, AttributeError) as e:
    logger.debug(f"critique_agent not available: {e}")
    _AVAILABLE_AGENTS['critique'] = False

# testgen_agent - has heavy dependencies (presidio, spacy, torch)
try:
    _testgen_module = __import__('generator.agents.testgen_agent', fromlist=['TestGenAgent', 'Policy'])
    if hasattr(_testgen_module, 'TestGenAgent'):
        TestGenAgent = _testgen_module.TestGenAgent
    if hasattr(_testgen_module, 'Policy'):
        Policy = _testgen_module.Policy
    _AVAILABLE_AGENTS['testgen'] = True
except (ImportError, AttributeError) as e:
    logger.debug(f"testgen_agent not available: {e}")
    _AVAILABLE_AGENTS['testgen'] = False

# deploy_agent
try:
    _deploy_module = __import__('generator.agents.deploy_agent', fromlist=['DeployAgent', 'DeployConfig'])
    if hasattr(_deploy_module, 'DeployAgent'):
        DeployAgent = _deploy_module.DeployAgent
    if hasattr(_deploy_module, 'DeployConfig'):
        DeployConfig = _deploy_module.DeployConfig
    _AVAILABLE_AGENTS['deploy'] = True
except (ImportError, AttributeError) as e:
    logger.debug(f"deploy_agent not available: {e}")
    _AVAILABLE_AGENTS['deploy'] = False

# docgen_agent
try:
    _docgen_module = __import__('generator.agents.docgen_agent', fromlist=['DocgenAgent', 'DocgenConfig'])
    if hasattr(_docgen_module, 'DocgenAgent'):
        DocgenAgent = _docgen_module.DocgenAgent
    if hasattr(_docgen_module, 'DocgenConfig'):
        DocgenConfig = _docgen_module.DocgenConfig
    _AVAILABLE_AGENTS['docgen'] = True
except (ImportError, AttributeError) as e:
    logger.debug(f"docgen_agent not available: {e}")
    _AVAILABLE_AGENTS['docgen'] = False


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
