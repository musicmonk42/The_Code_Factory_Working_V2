# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# D:\SFE\self_fixing_engineer\arbiter\plugins\__init__.py
from .anthropic_adapter import AnthropicAdapter
from .anthropic_adapter import APIError as AnthropicAPIError
from .anthropic_adapter import AuthError as AnthropicAuthError
from .gemini_adapter import APIError as GeminiAPIError
from .gemini_adapter import AuthError as GeminiAuthError
from .gemini_adapter import GeminiAdapter
from .llm_client import LLMClient, LLMClientError, LoadBalancedLLMClient
from .multi_modal_config import MultiModalConfig
from .multi_modal_plugin import MultiModalPlugin
from .ollama_adapter import APIError as OllamaAPIError
from .ollama_adapter import OllamaAdapter
from .openai_adapter import APIError as OpenAIAPIError
from .openai_adapter import AuthError as OpenAIAuthError
from .openai_adapter import OpenAIAdapter

# Alias for backward compatibility if needed
GeminiAPIAdapter = GeminiAdapter

__all__ = [
    "LLMClient",
    "LoadBalancedLLMClient",
    "LLMClientError",
    "OpenAIAdapter",
    "OpenAIAuthError",
    "OpenAIAPIError",
    "AnthropicAdapter",
    "AnthropicAuthError",
    "AnthropicAPIError",
    "GeminiAdapter",
    "GeminiAPIAdapter",  # Keeping both names for compatibility
    "GeminiAuthError",
    "GeminiAPIError",
    "OllamaAdapter",
    "OllamaAPIError",
    "MultiModalPlugin",
    "MultiModalConfig",
]
