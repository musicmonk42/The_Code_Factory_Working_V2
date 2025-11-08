"""
This module serves as the public API for the explainable reasoning package.
It exposes key components and classes for easy access and integration.
"""

from .reasoner_config import ReasonerConfig, SensitiveValue
from .reasoner_errors import ReasonerError, ReasonerErrorCode
from .audit_ledger import AuditLedgerClient
from .prompt_strategies import (
    PromptStrategy,
    DefaultPromptStrategy,
    ConcisePromptStrategy,
    PromptStrategyFactory
)
from .utils import (
    _sanitize_context,
    _simple_text_sanitize,
    _rule_based_fallback,
    MultiModalData,
    MultiModalAnalysisResult,
    ImageAnalysisResult,
    AudioAnalysisResult,
    VideoAnalysisResult,
    rate_limited,
)
from .metrics import METRICS, get_metrics_content
from .adapters import (
    LLMAdapter,
    OpenAIGPTAdapter,
    GeminiAPIAdapter,
    AnthropicAdapter,
    LLMAdapterFactory
)

# Define the package version
__version__ = "1.0.0"

# List of public exports for star imports (if used)

# ExplainableReasoner class
class ExplainableReasoner:
    """Main reasoner class for explainable AI reasoning"""
    def __init__(self, config=None):
        self.config = config or {}
    
    async def reason(self, query, context=None):
        """Perform reasoning on a query"""
        return {"reasoning": "Not implemented", "query": query}

__all__ = [
    "ReasonerConfig",
    "SensitiveValue",
    "ReasonerError",
    "ReasonerErrorCode",
    "AuditLedgerClient",
    "PromptStrategy",
    "DefaultPromptStrategy",
    "ConcisePromptStrategy",
    "PromptStrategyFactory",
    "LLMAdapter",
    "OpenAIGPTAdapter",
    "GeminiAdapter",
    "AnthropicAdapter",
    "LLMAdapterFactory",
    "_sanitize_context",
    "_simple_text_sanitize",
    "_rule_based_fallback",
    "MultiModalData",
    "MultiModalAnalysisResult",
    "ImageAnalysisResult",
    "AudioAnalysisResult",
    "VideoAnalysisResult",
    "rate_limited",
    "METRICS",
    "get_metrics_content",
]