# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Clarifier Module for Ambiguity Detection and Resolution.

This module provides requirements clarification functionality using
LLM-based analysis and interactive user feedback to resolve ambiguities
in software requirements.

Architecture:
    This module uses lazy imports to avoid circular dependencies and
    improve startup performance. Components are imported on-demand
    rather than at module load time.

Key Components:
    - Clarifier: Main clarification orchestrator
    - PromptClarifier: LLM-based prompt generation
    - clarifier_prompt: Prompt templates and utilities

Usage:
    from generator.clarifier import Clarifier
    
    # Create clarifier instance
    clarifier = await Clarifier.create()
    
    # Detect ambiguities
    ambiguities = await clarifier.detect_ambiguities(requirements)
    
    # Generate clarification questions
    questions = await clarifier.generate_questions(ambiguities)

Standards Compliance:
    - PEP 8 style guidelines
    - Lazy loading for performance
    - Circular dependency prevention
    - Comprehensive error handling
    - Type hints for public APIs

Author: Code Factory Generator
Version: 1.0.0
"""

from typing import Any, Callable, Optional
import logging

# Configure module logger
logger = logging.getLogger(__name__)

# Import core components from the clarifier module
# Only import Clarifier class to avoid circular dependencies
# Utility functions will be wrapped to avoid circular imports
try:
    from . import clarifier as clarifier  # Import the submodule itself
    from .clarifier import Clarifier
    _CLARIFIER_AVAILABLE = True
except ImportError as e:
    logger.warning(
        "Failed to import core clarifier components: %s. "
        "Clarifier functionality may be limited.",
        str(e),
        extra={"error": str(e), "error_type": type(e).__name__}
    )
    _CLARIFIER_AVAILABLE = False
    
    # Use simple None stubs instead of creating magic modules
    # This prevents circular dependency issues during test collection
    clarifier = None  # type: ignore
    Clarifier = None  # type: ignore

# Wrapper functions for utility functions from clarifier.py
# These avoid circular imports by lazily importing from the clarifier module
# Cache the imported functions to avoid repeated imports
_cached_get_logger = None
_cached_get_config = None
_cached_get_fernet = None


def get_logger(*args: Any, **kwargs: Any) -> Any:
    """
    Get logger instance with lazy import.
    
    This function delays the import of get_logger from clarifier until it's
    actually called, avoiding circular import issues during module initialization.
    
    Returns:
        Logger instance
    """
    global _cached_get_logger
    
    if _CLARIFIER_AVAILABLE and clarifier is not None:
        if _cached_get_logger is None:
            from .clarifier import get_logger as _get_logger
            _cached_get_logger = _get_logger
        return _cached_get_logger(*args, **kwargs)
    else:
        # Fallback to module logger if clarifier not available
        return logger


def get_config(*args: Any, **kwargs: Any) -> Any:
    """
    Get config instance with lazy import.
    
    This function delays the import of get_config from clarifier until it's
    actually called, avoiding circular import issues during module initialization.
    
    Returns:
        Config instance (Dynaconf)
    """
    global _cached_get_config
    
    if _CLARIFIER_AVAILABLE and clarifier is not None:
        if _cached_get_config is None:
            from .clarifier import get_config as _get_config
            _cached_get_config = _get_config
        return _cached_get_config(*args, **kwargs)
    else:
        raise ImportError("Clarifier module not available, cannot get config")


def get_fernet(*args: Any, **kwargs: Any) -> Any:
    """
    Get Fernet instance with lazy import.
    
    This function delays the import of get_fernet from clarifier until it's
    actually called, avoiding circular import issues during module initialization.
    
    Returns:
        Fernet instance for encryption
    """
    global _cached_get_fernet
    
    if _CLARIFIER_AVAILABLE and clarifier is not None:
        if _cached_get_fernet is None:
            from .clarifier import get_fernet as _get_fernet
            _cached_get_fernet = _get_fernet
        return _cached_get_fernet(*args, **kwargs)
    else:
        raise ImportError("Clarifier module not available, cannot get fernet")

# Import clarifier_prompt if available
try:
    from . import clarifier_prompt
except ImportError:
    clarifier_prompt = None  # type: ignore

# Lazy import for get_channel to avoid circular dependency
# clarifier_user_prompt imports from clarifier, so we can't import it at module level
def get_channel(*args: Any, **kwargs: Any) -> Any:
    """
    Get a user prompt channel with lazy import.
    
    This function delays the import of clarifier_user_prompt until it's
    actually called, avoiding circular import issues during module
    initialization.
    
    Args:
        *args: Positional arguments to pass to the underlying get_channel
        **kwargs: Keyword arguments to pass to the underlying get_channel
        
    Returns:
        Channel instance for user interaction
        
    Raises:
        ImportError: If clarifier_user_prompt cannot be imported
        
    Example:
        >>> channel = get_channel(user_id="user123")
        >>> await channel.send_message("What is your requirement?")
    """
    try:
        from .clarifier_user_prompt import get_channel as _get_channel
        return _get_channel(*args, **kwargs)
    except ImportError as e:
        logger.error(
            "Failed to import get_channel: %s",
            str(e),
            exc_info=True,
            extra={"error": str(e)}
        )
        raise


# Lazy import for clarifier_prompt module
def _get_clarifier_prompt_module() -> Any:
    """
    Lazy import of clarifier_prompt module.
    
    Returns:
        clarifier_prompt module
        
    Raises:
        ImportError: If clarifier_prompt cannot be imported
    """
    try:
        from . import clarifier_prompt
        return clarifier_prompt
    except ImportError as e:
        logger.error(
            "Failed to import clarifier_prompt module: %s",
            str(e),
            exc_info=True,
            extra={"error": str(e)}
        )
        raise


class _ClarifierPromptProxy:
    """
    Proxy for lazy loading clarifier_prompt module.
    
    This proxy delays the actual import of clarifier_prompt until
    an attribute is accessed, preventing circular import issues
    and improving startup time.
    
    Example:
        >>> from generator.clarifier import clarifier_prompt
        >>> # Import happens here when attribute is accessed
        >>> template = clarifier_prompt.get_template("ambiguity")
    """
    
    def __init__(self):
        """Initialize the proxy."""
        self._module: Optional[Any] = None
        self._import_attempted = False
    
    def __getattr__(self, name: str) -> Any:
        """
        Lazy load the module and return the requested attribute.
        
        Args:
            name: Name of the attribute to access
            
        Returns:
            The requested attribute from clarifier_prompt module
            
        Raises:
            AttributeError: If attribute doesn't exist
            ImportError: If module cannot be imported
        """
        # Prevent infinite recursion on private attributes
        if name.startswith('_'):
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )
        
        # Access _module and _import_attempted safely without triggering __getattr__
        # Use __dict__ to avoid recursion since accessing self._module would call __getattr__
        module = self.__dict__.get('_module', None)
        import_attempted = self.__dict__.get('_import_attempted', False)
        
        # Initialize if not yet set (should only happen if __init__ wasn't called)
        if module is None and '_module' not in self.__dict__:
            object.__setattr__(self, '_module', None)
            object.__setattr__(self, '_import_attempted', False)
            module = None
            import_attempted = False
        
        if module is None and not import_attempted:
            object.__setattr__(self, '_import_attempted', True)
            try:
                module = _get_clarifier_prompt_module()
                object.__setattr__(self, '_module', module)
                logger.debug(
                    "Lazy loaded clarifier_prompt module",
                    extra={"accessed_attribute": name}
                )
            except ImportError as e:
                # Re-raise with context
                raise ImportError(
                    f"Cannot access clarifier_prompt.{name}: "
                    "clarifier_prompt module failed to import"
                ) from e
        
        if module is None:
            raise ImportError(
                f"clarifier_prompt module is not available. "
                f"Cannot access attribute: {name}"
            )
        
        # Check if module is actually a proxy (circular import), raise ImportError
        if isinstance(module, _ClarifierPromptProxy):
            raise ImportError(
                f"clarifier_prompt module failed to load properly "
                "(circular import or stub). Cannot access attribute: {name}"
            )
        
        # Safely get the attribute from the real module
        try:
            return getattr(module, name)
        except AttributeError:
            raise AttributeError(
                f"module 'clarifier_prompt' has no attribute '{name}'"
            )


# Create lazy proxy for clarifier_prompt
clarifier_prompt = _ClarifierPromptProxy()

# Attempt to import PromptClarifier (optional)
try:
    from .clarifier_prompt import PromptClarifier
    _PROMPT_CLARIFIER_AVAILABLE = True
except ImportError as e:
    logger.debug(
        "PromptClarifier not available: %s",
        str(e),
        extra={"error": str(e)}
    )
    PromptClarifier = None  # type: ignore
    _PROMPT_CLARIFIER_AVAILABLE = False


# Define public API
__all__ = [
    'Clarifier',
    'get_config',
    'get_fernet',
    'get_logger',
    'get_channel',
    'clarifier_prompt',
]

# Add PromptClarifier to exports if available
if _PROMPT_CLARIFIER_AVAILABLE:
    __all__.append('PromptClarifier')


# Module initialization logging
logger.debug(
    "Clarifier module initialized - clarifier_available=%s, prompt_clarifier_available=%s",
    _CLARIFIER_AVAILABLE,
    _PROMPT_CLARIFIER_AVAILABLE,
    extra={
        "clarifier_available": _CLARIFIER_AVAILABLE,
        "prompt_clarifier_available": _PROMPT_CLARIFIER_AVAILABLE,
    }
)

