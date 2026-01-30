# generator/clarifier/__init__.py
"""
Clarifier module for ambiguity detection and resolution.

This module uses lazy imports to avoid circular dependencies.
Components are imported on-demand rather than at module load time.
"""

# Import core components from clarifier.py
# These are safe as they don't have circular dependencies
from .clarifier import Clarifier, get_config, get_fernet, get_logger

# Lazy import for get_channel to avoid circular dependency
# clarifier_user_prompt imports from clarifier, so we can't import it at module level
def get_channel(*args, **kwargs):
    """
    Get a user prompt channel with lazy import.
    
    This function delays the import of clarifier_user_prompt until it's actually called,
    avoiding circular import issues during module initialization.
    """
    from .clarifier_user_prompt import get_channel as _get_channel
    return _get_channel(*args, **kwargs)

# Lazy import for clarifier_prompt to ensure it's accessible
def _get_clarifier_prompt_module():
    """Lazy import of clarifier_prompt module."""
    from . import clarifier_prompt
    return clarifier_prompt

# Create a lazy property for clarifier_prompt
class _ClarifierPromptProxy:
    """Proxy for lazy loading clarifier_prompt module."""
    def __getattr__(self, name):
        return getattr(_get_clarifier_prompt_module(), name)

clarifier_prompt = _ClarifierPromptProxy()

# Also import PromptClarifier if needed
try:
    from .clarifier_prompt import PromptClarifier
    _prompt_clarifier_available = True
except ImportError:
    PromptClarifier = None
    _prompt_clarifier_available = False

__all__ = [
    'Clarifier',
    'get_config',
    'get_fernet',
    'get_logger',
    'get_channel',
    'clarifier_prompt',
]

if _prompt_clarifier_available:
    __all__.append('PromptClarifier')

