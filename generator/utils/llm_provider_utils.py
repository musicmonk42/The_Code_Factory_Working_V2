"""
LLM Provider Utilities

This module provides utilities for determining LLM providers from model names
and creating properly formatted model configurations for ensemble API calls.

Industry Standards Applied:
- Single Responsibility Principle: Each function has one clear purpose
- DRY (Don't Repeat Yourself): Centralized provider mapping logic
- Defensive Programming: Input validation and fallback handling
- Type Safety: Full type hints for better IDE support and runtime checking
"""

from typing import Dict, List, Literal, Optional
import logging

logger = logging.getLogger(__name__)

# Type alias for supported providers
LLMProvider = Literal["openai", "claude", "gemini", "grok", "local"]

# Industry Standard: Define provider mappings as configuration rather than code
PROVIDER_MODEL_PREFIXES: Dict[str, List[str]] = {
    "openai": ["gpt", "o1", "text-davinci", "text-curie"],
    "claude": ["claude"],
    "gemini": ["gemini"],
    "grok": ["grok"],
    "local": ["local"],
}

# Default provider when model prefix is not recognized
DEFAULT_PROVIDER: LLMProvider = "openai"


def infer_provider_from_model(model_name: str) -> LLMProvider:
    """
    Infer the LLM provider from a model name using industry-standard prefix matching.
    
    This function implements a robust provider detection algorithm that:
    1. Validates input parameters
    2. Uses case-insensitive prefix matching
    3. Logs warnings for unknown models
    4. Returns a safe default for unrecognized models
    
    Args:
        model_name: The name of the LLM model (e.g., "gpt-4o", "claude-3-opus")
        
    Returns:
        The inferred provider name
        
    Raises:
        ValueError: If model_name is empty or None
        
    Examples:
        >>> infer_provider_from_model("gpt-4o")
        'openai'
        >>> infer_provider_from_model("claude-3-opus")
        'claude'
        >>> infer_provider_from_model("gemini-pro")
        'gemini'
        >>> infer_provider_from_model("unknown-model-123")
        'openai'  # Returns default with warning logged
    """
    # Input validation - Industry Standard: Fail fast with clear errors
    if not model_name:
        raise ValueError("model_name cannot be empty or None")
    
    if not isinstance(model_name, str):
        raise TypeError(f"model_name must be a string, got {type(model_name)}")
    
    # Normalize for case-insensitive matching - Industry Standard: Be liberal in what you accept
    model_lower = model_name.lower().strip()
    
    # Check each provider's prefixes
    for provider, prefixes in PROVIDER_MODEL_PREFIXES.items():
        for prefix in prefixes:
            if model_lower.startswith(prefix.lower()):
                logger.debug(
                    f"Inferred provider '{provider}' from model '{model_name}' using prefix '{prefix}'"
                )
                return provider  # type: ignore
    
    # Industry Standard: Log warnings for unexpected inputs but don't fail
    logger.warning(
        f"Could not infer provider for model '{model_name}'. "
        f"Using default provider '{DEFAULT_PROVIDER}'. "
        f"Known prefixes: {PROVIDER_MODEL_PREFIXES}"
    )
    
    return DEFAULT_PROVIDER


def create_model_config(
    model_name: str,
    provider: Optional[LLMProvider] = None
) -> Dict[str, str]:
    """
    Create a properly formatted model configuration for ensemble API calls.
    
    This function ensures all model configurations have both 'provider' and 'model' keys,
    as required by the ensemble API validation logic. If provider is not specified,
    it will be inferred from the model name.
    
    Industry Standards Applied:
    - Defensive programming: Validates inputs and provides clear error messages
    - Type safety: Returns a typed dictionary
    - Documentation: Clear docstring with examples
    
    Args:
        model_name: The name of the LLM model
        provider: Optional provider override. If None, will be inferred from model_name
        
    Returns:
        Dictionary with 'provider' and 'model' keys suitable for ensemble API
        
    Raises:
        ValueError: If model_name is empty or None
        
    Examples:
        >>> create_model_config("gpt-4o")
        {'provider': 'openai', 'model': 'gpt-4o'}
        >>> create_model_config("claude-3-opus", provider="claude")
        {'provider': 'claude', 'model': 'claude-3-opus'}
    """
    # Input validation
    if not model_name:
        raise ValueError("model_name cannot be empty or None")
    
    # Infer provider if not explicitly provided
    if provider is None:
        provider = infer_provider_from_model(model_name)
    
    # Create configuration - Industry Standard: Explicit is better than implicit
    config = {
        "provider": provider,
        "model": model_name
    }
    
    logger.debug(f"Created model config: {config}")
    
    return config


def create_model_configs(
    model_names: List[str],
    provider_overrides: Optional[Dict[str, LLMProvider]] = None
) -> List[Dict[str, str]]:
    """
    Create multiple model configurations for ensemble API calls.
    
    This is a convenience function for creating configurations for multiple models
    at once, with optional provider overrides for specific models.
    
    Args:
        model_names: List of model names
        provider_overrides: Optional dict mapping model names to specific providers
        
    Returns:
        List of model configuration dictionaries
        
    Raises:
        ValueError: If model_names is empty or contains invalid entries
        
    Examples:
        >>> create_model_configs(["gpt-4o", "claude-3-opus"])
        [{'provider': 'openai', 'model': 'gpt-4o'}, 
         {'provider': 'claude', 'model': 'claude-3-opus'}]
    """
    if not model_names:
        raise ValueError("model_names list cannot be empty")
    
    provider_overrides = provider_overrides or {}
    
    configs = []
    for model_name in model_names:
        provider = provider_overrides.get(model_name)
        config = create_model_config(model_name, provider)
        configs.append(config)
    
    return configs


# Industry Standard: Provide a simple validation function
def validate_model_config(config: Dict[str, str]) -> bool:
    """
    Validate that a model configuration has all required fields.
    
    Args:
        config: Model configuration dictionary to validate
        
    Returns:
        True if valid, False otherwise
        
    Examples:
        >>> validate_model_config({"provider": "openai", "model": "gpt-4o"})
        True
        >>> validate_model_config({"model": "gpt-4o"})
        False
    """
    if not isinstance(config, dict):
        return False
    
    required_keys = {"provider", "model"}
    return required_keys.issubset(config.keys()) and all(
        isinstance(config[k], str) and config[k].strip()
        for k in required_keys
    )
