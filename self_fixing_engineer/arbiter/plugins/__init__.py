# D:\SFE\self_fixing_engineer\arbiter\plugins\__init__.py
from .llm_client import LLMClient, LoadBalancedLLMClient, LLMClientError
from .openai_adapter import (
    OpenAIAdapter,
    AuthError as OpenAIAuthError,
    APIError as OpenAIAPIError,
)
from .anthropic_adapter import (
    AnthropicAdapter,
    AuthError as AnthropicAuthError,
    APIError as AnthropicAPIError,
)
from .gemini_adapter import (
    GeminiAdapter,
    AuthError as GeminiAuthError,
    APIError as GeminiAPIError,
)
from .ollama_adapter import OllamaAdapter, APIError as OllamaAPIError
from .multi_modal_plugin import MultiModalPlugin
from .multi_modal_config import MultiModalConfig

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
