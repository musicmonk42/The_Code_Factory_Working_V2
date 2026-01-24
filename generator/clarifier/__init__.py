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

__all__ = [
    'Clarifier',
    'get_config',
    'get_fernet',
    'get_logger',
    'get_channel',
]
