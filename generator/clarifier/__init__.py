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
# These are safe as they don't have circular dependencies
try:
    from . import clarifier as clarifier  # Import the submodule itself
    from .clarifier import Clarifier, get_config, get_fernet, get_logger
    _CLARIFIER_AVAILABLE = True
except ImportError as e:
    logger.warning(
        "Failed to import core clarifier components: %s. "
        "Clarifier functionality may be limited.",
        str(e),
        extra={"error": str(e), "error_type": type(e).__name__}
    )
    _CLARIFIER_AVAILABLE = False
    
    # Provide stub implementations
    import types
    import sys
    
    # Create a stub module that supports attribute access for mocking
    clarifier_stub = types.ModuleType('generator.clarifier.clarifier')
    clarifier_stub.__file__ = '<stub>'
    
    # Add __getattr__ to support dynamic attribute access (needed for test mocking)
    def _stub_getattr(name):
        """Return a placeholder for any attribute to support test mocking."""
        return None
    
    clarifier_stub.__getattr__ = _stub_getattr
    clarifier = clarifier_stub
    
    # Register in sys.modules so patches can find it
    sys.modules['generator.clarifier.clarifier'] = clarifier_stub
    
    Clarifier = None  # type: ignore
    get_config = None  # type: ignore
    get_fernet = None  # type: ignore
    get_logger = None  # type: ignore

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
        
        # Load module on first access
        if not hasattr(self, '_module'):
            # Use object.__setattr__ to bypass __setattr__ if defined
            object.__setattr__(self, '_module', None)
            object.__setattr__(self, '_import_attempted', False)
        
        if self._module is None and not self._import_attempted:
            self._import_attempted = True
            try:
                self._module = _get_clarifier_prompt_module()
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
        
        if self._module is None:
            raise ImportError(
                f"clarifier_prompt module is not available. "
                f"Cannot access attribute: {name}"
            )
        
        # Check if module is actually a proxy (circular import), raise ImportError
        if isinstance(self._module, _ClarifierPromptProxy):
            raise ImportError(
                f"clarifier_prompt module failed to load properly "
                "(circular import or stub). Cannot access attribute: {name}"
            )
        
        # Safely get the attribute from the real module
        try:
            return getattr(self._module, name)
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

