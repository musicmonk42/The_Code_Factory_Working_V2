"""
API Key Management endpoints.

Centralized API key configuration for all modules (Generator, SFE, OmniCore).
"""

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from server.schemas import LLMConfigRequest, LLMProvider, SuccessResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api-keys", tags=["API Keys"])

# In-memory storage for API keys (in production, use encrypted storage)
_api_keys_storage: Dict[str, Dict[str, any]] = {}


@router.get("/", response_model=Dict)
@router.get("", response_model=Dict)
async def get_api_keys_status():
    """
    Get status of all configured API keys.
    
    Returns information about which providers are configured and which is active.
    API keys are never returned for security.
    
    **Returns:**
    - active_provider: Currently active LLM provider
    - providers: Status of each provider
    - total_configured: Number of configured providers
    """
    active = _api_keys_storage.get("active_provider")
    
    providers_status = {}
    for provider in ["openai", "anthropic", "google", "xai", "ollama"]:
        if provider in _api_keys_storage:
            config = _api_keys_storage[provider]
            providers_status[provider] = {
                "configured": True,
                "has_api_key": bool(config.get("api_key")),
                "model": config.get("model"),
                "is_active": provider == active,
            }
        else:
            providers_status[provider] = {
                "configured": False,
                "has_api_key": False,
                "model": None,
                "is_active": False,
            }
    
    return {
        "active_provider": active,
        "providers": providers_status,
        "total_configured": sum(1 for p in providers_status.values() if p["configured"]),
    }


@router.post("/llm/configure", response_model=SuccessResponse)
async def configure_llm_api_key(request: LLMConfigRequest) -> SuccessResponse:
    """
    Configure LLM API key for all modules.
    
    This sets the API key globally for use by Generator, SFE, and OmniCore modules.
    The key is stored securely and made available to all LLM operations.
    
    **Request Body:**
    - provider: LLM provider (openai, anthropic, google, xai, ollama)
    - api_key: API key for the provider
    - model: Optional specific model to use
    - config: Additional configuration
    
    **Returns:**
    - Success confirmation
    
    **Security Note:**
    - API keys are stored in memory (use encrypted storage in production)
    - Keys are never returned in API responses
    """
    provider_key = request.provider.value
    
    # Store API key configuration
    _api_keys_storage[provider_key] = {
        "provider": provider_key,
        "api_key": request.api_key,  # In production: encrypt this
        "model": request.model,
        "config": request.config or {},
        "configured_at": "now",
    }
    
    # Mark as active provider if it's the first one
    if "active_provider" not in _api_keys_storage:
        _api_keys_storage["active_provider"] = provider_key
    
    logger.info(f"Configured LLM API key for {provider_key}")
    
    # Propagate to all modules
    await _propagate_api_key_to_modules(provider_key, request.api_key, request.model)
    
    return SuccessResponse(
        success=True,
        message=f"API key configured for {provider_key} and propagated to all modules",
        data={
            "provider": provider_key,
            "model": request.model or "default",
            "modules_updated": ["generator", "sfe", "omnicore"],
        },
    )


@router.post("/llm/{provider}/activate", response_model=SuccessResponse)
async def activate_llm_provider(provider: str) -> SuccessResponse:
    """
    Set the active LLM provider.
    
    Switches the active provider for all modules.
    
    **Path Parameters:**
    - provider: Provider to activate (openai, anthropic, google, xai, ollama)
    
    **Returns:**
    - Success confirmation
    
    **Errors:**
    - 404: Provider not configured
    """
    if provider not in _api_keys_storage:
        raise HTTPException(
            status_code=404,
            detail=f"Provider {provider} not configured. Configure it first.",
        )
    
    _api_keys_storage["active_provider"] = provider
    
    logger.info(f"Activated LLM provider: {provider}")
    
    return SuccessResponse(
        success=True,
        message=f"Activated {provider} as the active LLM provider",
        data={"active_provider": provider},
    )


@router.get("/llm/status")
async def get_llm_api_key_status():
    """
    Get status of all configured LLM API keys.
    
    Returns information about which providers are configured and which is active.
    API keys are never returned for security.
    
    **Returns:**
    - Provider status information
    """
    active = _api_keys_storage.get("active_provider")
    
    providers_status = {}
    for provider in ["openai", "anthropic", "google", "xai", "ollama"]:
        if provider in _api_keys_storage:
            config = _api_keys_storage[provider]
            providers_status[provider] = {
                "configured": True,
                "has_api_key": bool(config.get("api_key")),
                "model": config.get("model"),
                "is_active": provider == active,
            }
        else:
            providers_status[provider] = {
                "configured": False,
                "has_api_key": False,
                "model": None,
                "is_active": False,
            }
    
    return {
        "active_provider": active,
        "providers": providers_status,
        "total_configured": sum(1 for p in providers_status.values() if p["configured"]),
    }


@router.delete("/llm/{provider}", response_model=SuccessResponse)
async def remove_llm_api_key(provider: str) -> SuccessResponse:
    """
    Remove API key for a provider.
    
    **Path Parameters:**
    - provider: Provider to remove
    
    **Returns:**
    - Success confirmation
    
    **Errors:**
    - 404: Provider not configured
    """
    if provider not in _api_keys_storage:
        raise HTTPException(
            status_code=404,
            detail=f"Provider {provider} not configured",
        )
    
    del _api_keys_storage[provider]
    
    # If this was the active provider, clear it
    if _api_keys_storage.get("active_provider") == provider:
        _api_keys_storage["active_provider"] = None
    
    logger.info(f"Removed API key for {provider}")
    
    return SuccessResponse(
        success=True,
        message=f"API key removed for {provider}",
    )


async def _propagate_api_key_to_modules(
    provider: str, api_key: str, model: Optional[str]
) -> None:
    """
    Propagate API key to all modules that use LLMs.
    
    Internal function to ensure Generator and SFE have access to the API key.
    """
    # In a real implementation, this would:
    # 1. Update environment variables or config files
    # 2. Notify running services to reload configuration
    # 3. Store in secure key vault (AWS Secrets Manager, Azure Key Vault, etc.)
    
    # For now, we store in a way that services can access
    import os
    
    # Set environment variable for the modules to pick up
    os.environ[f"LLM_{provider.upper()}_API_KEY"] = api_key
    if model:
        os.environ[f"LLM_{provider.upper()}_MODEL"] = model
    
    logger.info(f"Propagated {provider} API key to environment for all modules")


def get_active_llm_config() -> Optional[Dict[str, any]]:
    """
    Get the active LLM configuration for use by modules.
    
    Returns:
        Dictionary with provider, api_key, model, and config
    """
    active_provider = _api_keys_storage.get("active_provider")
    if not active_provider or active_provider not in _api_keys_storage:
        return None
    
    return _api_keys_storage[active_provider]
