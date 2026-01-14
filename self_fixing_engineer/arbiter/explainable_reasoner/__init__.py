"""
This module serves as the public API for the explainable reasoning package.
It exposes key components and classes for easy access and integration.
"""

from .adapters import (
    AnthropicAdapter,
    GeminiAPIAdapter,
    LLMAdapter,
    LLMAdapterFactory,
    OpenAIGPTAdapter,
)
from .audit_ledger import AuditLedgerClient
from .explainable_reasoner import ExplainableReasoner, ExplainableReasonerPlugin
from .metrics import METRICS, get_metrics_content
from .prompt_strategies import (
    ConcisePromptStrategy,
    DefaultPromptStrategy,
    PromptStrategy,
    PromptStrategyFactory,
)
from .reasoner_config import ReasonerConfig, SensitiveValue
from .reasoner_errors import ReasonerError, ReasonerErrorCode
from .utils import (
    AudioAnalysisResult,
    ImageAnalysisResult,
    MultiModalAnalysisResult,
    MultiModalData,
    VideoAnalysisResult,
    _rule_based_fallback,
    _sanitize_context,
    _simple_text_sanitize,
    rate_limited,
)

# Define the package version
__version__ = "1.0.0"

__all__ = [
    "ExplainableReasoner",
    "ExplainableReasonerPlugin",
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
    "GeminiAPIAdapter",
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
