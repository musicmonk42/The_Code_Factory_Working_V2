import os
import logging
import json
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Union, Type

from pydantic import BaseModel
from opentelemetry import trace

# Internal imports for metrics and utils
from arbiter.explainable_reasoner.metrics import METRICS
from arbiter.explainable_reasoner.utils import (
    _simple_text_sanitize,
    _format_multimodal_for_prompt,
)

# Conditional import for MultiModalData and schemas
try:
    from arbiter.models.multi_modal_schemas import (
        MultiModalData,
        ImageAnalysisResult,
        AudioAnalysisResult,
        VideoAnalysisResult,
        MultiModalAnalysisResult,
    )

    MULTI_MODAL_SCHEMAS_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).warning(
        "Warning: arbiter.models.multi_modal_schemas not found. Using dummy MultiModalData/Schemas for standalone mode."
    )

    # Dummy MultiModalData if schema not available
    class MultiModalData(BaseModel):
        data_type: str
        data: bytes
        metadata: Dict = {}

        def dict(self, exclude_unset=False) -> Dict[str, Any]:
            data_snippet = ""
            if self.data_type == "image" and self.data:
                import base64

                data_snippet = f"base64_preview:{base64.b64encode(self.data).decode()[:50]}..."
            elif self.data_type in ("audio", "video") and self.data:
                data_snippet = f"bytes_len:{len(self.data)}"

            return {
                "data_type": self.data_type,
                "data_preview": data_snippet,
                "metadata": self.metadata,
            }

    # Dummy schemas for type hinting purposes
    class MultiModalAnalysisResult(BaseModel):
        pass

    class ImageAnalysisResult(MultiModalAnalysisResult):
        image_id: str = "dummy_id"
        captioning_result: Optional[Any] = None
        ocr_result: Optional[Any] = None
        detected_objects: Optional[List[str]] = None

    class AudioAnalysisResult(MultiModalAnalysisResult):
        audio_id: str = "dummy_id"
        transcription: Optional[Any] = None
        sentiment: Optional[Any] = None
        keywords: Optional[List[str]] = None

    class VideoAnalysisResult(MultiModalAnalysisResult):
        video_id: str = "dummy_id"
        summary_result: Optional[Any] = None
        audio_transcription_result: Optional[Any] = None
        main_entities: Optional[List[str]] = None

    MULTI_MODAL_SCHEMAS_AVAILABLE = False

# Structured logging with structlog
import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(indent=2),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
_prompt_strategy_logger = structlog.get_logger(__name__)

# OpenTelemetry for tracing
try:
    tracer = trace.get_tracer(__name__)
except ImportError:

    class DummyTracer:
        def start_as_current_span(self, name: str, *args, **kwargs):
            class DummySpan:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc_val, exc_tb):
                    pass

            return DummySpan()

    tracer = DummyTracer()

# Production readiness check: if running in prod and required schemas are missing, fail fast.
if not MULTI_MODAL_SCHEMAS_AVAILABLE and os.getenv("ENV", "dev").lower() == "prod":
    _prompt_strategy_logger.error("multi_modal_schemas_missing_in_production")
    raise ImportError(
        "The 'arbiter.models.multi_modal_schemas' package is required for production environment."
    )


def _truncate_context(context: Dict[str, Any], max_len: int = 1000) -> str:
    """Helper to reliably truncate context dictionaries to a max string length."""
    # Handle empty context
    if not context:
        return "{}"

    parts = []
    current_len = 0
    for k, v in context.items():
        # Use the multimodal formatter to get a string representation
        val_str = str(_format_multimodal_for_prompt(v))
        part = f"{k}: {val_str}"
        if current_len + len(part) > max_len:
            remaining_len = max_len - current_len
            if remaining_len > 0:
                parts.append(part[:remaining_len] + "...")
            break
        parts.append(part)
        current_len += len(part)

    # Return formatted context or empty JSON object if no parts
    return "; ".join(parts) if parts else "{}"


class PromptStrategy(ABC):
    """
    Abstract base class for prompt generation strategies.

    Each strategy is responsible for formatting input data (context, goal, history)
    into a coherent prompt string for a language model. The methods are asynchronous
    to support potential future I/O operations (e.g., retrieving data from a remote
    cache or knowledge base).
    """

    def __init__(self, logger_instance: Union[logging.Logger, structlog.BoundLogger]):
        self.logger = logger_instance

    @abstractmethod
    async def create_explanation_prompt(
        self, context: Dict[str, Any], goal: str, history_str: str = ""
    ) -> str:
        """Creates an explanation prompt."""
        pass

    @abstractmethod
    async def create_reasoning_prompt(
        self, context: Dict[str, Any], goal: str, history_str: str = ""
    ) -> str:
        """Creates a reasoning prompt."""
        pass


class DefaultPromptStrategy(PromptStrategy):
    """
    Default prompt strategy with moderately detailed, balanced templates.
    This strategy is designed to work well with general-purpose language models.
    """

    async def create_explanation_prompt(
        self, context: Dict[str, Any], goal: str, history_str: str = ""
    ) -> str:
        """Creates a standard explanation prompt."""
        with tracer.start_as_current_span("create_explanation_prompt_default"):
            start_time = time.monotonic()
            # Security: Sanitize all user-provided string inputs to prevent prompt injection.
            safe_goal = _simple_text_sanitize(goal)
            safe_history = _simple_text_sanitize(history_str)
            # Reliability: Truncate context to avoid overly long prompts.
            context_str = _truncate_context(context, max_len=1500)

            # Template logic: A clear, direct instruction for general-purpose models.
            prompt = f"Explain the following goal in detail: {safe_goal}\nBased on this context: {context_str}\nPrevious interactions: {safe_history}\nExplanation:"

            self.logger.debug(
                "prompt_generated",
                type="explanation",
                strategy="default",
                length=len(prompt),
            )
            if "prompt_size_bytes" in METRICS:
                METRICS["prompt_size_bytes"].labels(type="explanation").observe(
                    len(prompt.encode("utf-8"))
                )
            if "inference_duration_seconds" in METRICS:
                METRICS["inference_duration_seconds"].labels(
                    type="prompt_generation", strategy="default"
                ).observe(time.monotonic() - start_time)
            return prompt

    async def create_reasoning_prompt(
        self, context: Dict[str, Any], goal: str, history_str: str = ""
    ) -> str:
        """Creates a standard reasoning prompt."""
        with tracer.start_as_current_span("create_reasoning_prompt_default"):
            start_time = time.monotonic()
            # Security: Sanitize all user-provided string inputs.
            safe_goal = _simple_text_sanitize(goal)
            safe_history = _simple_text_sanitize(history_str)
            # Reliability: Truncate context.
            context_str = _truncate_context(context, max_len=1500)

            # Template logic: Encourages a step-by-step process, ideal for chain-of-thought tasks.
            prompt = f"Reason step-by-step about: {safe_goal}\nUsing this context: {context_str}\nPrevious interactions: {safe_history}\nReasoning:"

            self.logger.debug(
                "prompt_generated",
                type="reasoning",
                strategy="default",
                length=len(prompt),
            )
            if "prompt_size_bytes" in METRICS:
                METRICS["prompt_size_bytes"].labels(type="reasoning").observe(
                    len(prompt.encode("utf-8"))
                )
            if "inference_duration_seconds" in METRICS:
                METRICS["inference_duration_seconds"].labels(
                    type="prompt_generation", strategy="default"
                ).observe(time.monotonic() - start_time)
            return prompt


class ConcisePromptStrategy(PromptStrategy):
    """
    Concise prompt strategy for shorter, more direct interactions.
    This is ideal for use cases where speed or token limits are critical.
    """

    async def create_explanation_prompt(
        self, context: Dict[str, Any], goal: str, history_str: str = ""
    ) -> str:
        """Creates a concise explanation prompt, ideal for speed and smaller models."""
        with tracer.start_as_current_span("create_explanation_prompt_concise"):
            start_time = time.monotonic()
            safe_goal = _simple_text_sanitize(goal)
            safe_history = _simple_text_sanitize(history_str)
            context_str = _truncate_context(context, max_len=500)

            # Template logic: Uses keywords like "briefly" and heavily truncates history to keep the prompt small.
            prompt = f"Explain {safe_goal} briefly. Context: {context_str}. History: {safe_history[:200]}. Explanation:"

            self.logger.debug(
                "prompt_generated",
                type="explanation",
                strategy="concise",
                length=len(prompt),
            )
            if "prompt_size_bytes" in METRICS:
                METRICS["prompt_size_bytes"].labels(type="explanation").observe(
                    len(prompt.encode("utf-8"))
                )
            if "inference_duration_seconds" in METRICS:
                METRICS["inference_duration_seconds"].labels(
                    type="prompt_generation", strategy="concise"
                ).observe(time.monotonic() - start_time)
            return prompt

    async def create_reasoning_prompt(
        self, context: Dict[str, Any], goal: str, history_str: str = ""
    ) -> str:
        """Creates a concise reasoning prompt."""
        with tracer.start_as_current_span("create_reasoning_prompt_concise"):
            start_time = time.monotonic()
            safe_goal = _simple_text_sanitize(goal)
            safe_history = _simple_text_sanitize(history_str)
            context_str = _truncate_context(context, max_len=500)

            # Template logic: Similar to the explanation, focuses on brevity.
            prompt = f"Reason about {safe_goal} briefly. Context: {context_str}. History: {safe_history[:200]}. Reasoning:"

            self.logger.debug(
                "prompt_generated",
                type="reasoning",
                strategy="concise",
                length=len(prompt),
            )
            if "prompt_size_bytes" in METRICS:
                METRICS["prompt_size_bytes"].labels(type="reasoning").observe(
                    len(prompt.encode("utf-8"))
                )
            if "inference_duration_seconds" in METRICS:
                METRICS["inference_duration_seconds"].labels(
                    type="prompt_generation", strategy="concise"
                ).observe(time.monotonic() - start_time)
            return prompt


class VerbosePromptStrategy(PromptStrategy):
    """
    Verbose prompt strategy designed to elicit detailed, comprehensive responses.
    This is best suited for models with large context windows and tasks
    requiring in-depth analysis.
    """

    async def create_explanation_prompt(
        self, context: Dict[str, Any], goal: str, history_str: str = ""
    ) -> str:
        """Creates a verbose explanation prompt, providing as much information as possible."""
        with tracer.start_as_current_span("create_explanation_prompt_verbose"):
            start_time = time.monotonic()
            safe_goal = _simple_text_sanitize(goal)
            safe_history = _simple_text_sanitize(history_str)
            # Reliability: Use a larger truncation limit for verbose contexts.
            context_str = _truncate_context(context, max_len=4000)

            # Template logic: Uses explicit headers ("Full context:", "Complete history:") and instructions ("Provide a detailed explanation")
            # to guide the model towards a thorough response.
            prompt = f"Provide a detailed explanation for the goal: '{safe_goal}'.\n\nFull context provided:\n---\n{context_str}\n---\n\nComplete conversation history:\n---\n{safe_history}\n---\n\nDetailed Explanation:"

            self.logger.debug(
                "prompt_generated",
                type="explanation",
                strategy="verbose",
                length=len(prompt),
            )
            if "prompt_size_bytes" in METRICS:
                METRICS["prompt_size_bytes"].labels(type="explanation").observe(
                    len(prompt.encode("utf-8"))
                )
            if "inference_duration_seconds" in METRICS:
                METRICS["inference_duration_seconds"].labels(
                    type="prompt_generation", strategy="verbose"
                ).observe(time.monotonic() - start_time)
            return prompt

    async def create_reasoning_prompt(
        self, context: Dict[str, Any], goal: str, history_str: str = ""
    ) -> str:
        """Creates a verbose reasoning prompt."""
        with tracer.start_as_current_span("create_reasoning_prompt_verbose"):
            start_time = time.monotonic()
            safe_goal = _simple_text_sanitize(goal)
            safe_history = _simple_text_sanitize(history_str)
            context_str = _truncate_context(context, max_len=4000)

            # Template logic: Explicitly asks for a "step-by-step" process to encourage structured reasoning.
            prompt = f"Provide detailed, step-by-step reasoning for the goal: '{safe_goal}'.\n\nFull context provided:\n---\n{context_str}\n---\n\nComplete conversation history:\n---\n{safe_history}\n---\n\nStep-by-Step Reasoning:"

            self.logger.debug(
                "prompt_generated",
                type="reasoning",
                strategy="verbose",
                length=len(prompt),
            )
            if "prompt_size_bytes" in METRICS:
                METRICS["prompt_size_bytes"].labels(type="reasoning").observe(
                    len(prompt.encode("utf-8"))
                )
            if "inference_duration_seconds" in METRICS:
                METRICS["inference_duration_seconds"].labels(
                    type="prompt_generation", strategy="verbose"
                ).observe(time.monotonic() - start_time)
            return prompt


class StructuredPromptStrategy(PromptStrategy):
    """
    Structured prompt strategy for models that can reliably generate JSON.
    This is crucial for downstream processing and automation.
    """

    async def create_explanation_prompt(
        self, context: Dict[str, Any], goal: str, history_str: str = ""
    ) -> str:
        """Creates a structured explanation prompt designed to force a JSON output."""
        with tracer.start_as_current_span("create_explanation_prompt_structured"):
            start_time = time.monotonic()
            safe_goal = _simple_text_sanitize(goal)
            safe_history = _simple_text_sanitize(history_str)

            # Create a Python dictionary and then dump it to a valid JSON string.
            prompt_data = {
                "task": "explanation",
                "goal": safe_goal,
                "context": context,
                "history": safe_history,
                "response_format": "json",
                "json_schema": {
                    "type": "object",
                    "properties": {"explanation": {"type": "string"}},
                },
            }

            prompt = json.dumps(prompt_data, indent=2, default=str)

            self.logger.debug(
                "prompt_generated",
                type="explanation",
                strategy="structured",
                length=len(prompt),
            )
            if "prompt_size_bytes" in METRICS:
                METRICS["prompt_size_bytes"].labels(type="explanation").observe(
                    len(prompt.encode("utf-8"))
                )
            if "inference_duration_seconds" in METRICS:
                METRICS["inference_duration_seconds"].labels(
                    type="prompt_generation", strategy="structured"
                ).observe(time.monotonic() - start_time)
            return prompt

    async def create_reasoning_prompt(
        self, context: Dict[str, Any], goal: str, history_str: str = ""
    ) -> str:
        """Creates a structured reasoning prompt for JSON list output."""
        with tracer.start_as_current_span("create_reasoning_prompt_structured"):
            start_time = time.monotonic()
            safe_goal = _simple_text_sanitize(goal)
            safe_history = _simple_text_sanitize(history_str)

            # Create a Python dictionary and then dump it to a valid JSON string.
            prompt_data = {
                "task": "reasoning",
                "goal": safe_goal,
                "context": context,
                "history": safe_history,
                "response_format": "json",
                "json_schema": {
                    "type": "object",
                    "properties": {"reasoning": {"type": "array", "items": {"type": "string"}}},
                },
            }

            prompt = json.dumps(prompt_data, indent=2, default=str)

            self.logger.debug(
                "prompt_generated",
                type="reasoning",
                strategy="structured",
                length=len(prompt),
            )
            if "prompt_size_bytes" in METRICS:
                METRICS["prompt_size_bytes"].labels(type="reasoning").observe(
                    len(prompt.encode("utf-8"))
                )
            if "inference_duration_seconds" in METRICS:
                METRICS["inference_duration_seconds"].labels(
                    type="prompt_generation", strategy="structured"
                ).observe(time.monotonic() - start_time)
            return prompt


class PromptStrategyFactory:
    """A factory class for creating and managing PromptStrategy instances."""

    _strategies: Dict[str, Type[PromptStrategy]] = {}

    @classmethod
    def register_strategy(cls, name: str, strategy_class: Type[PromptStrategy]):
        """Registers a new prompt strategy class with the factory."""
        if not issubclass(strategy_class, PromptStrategy):
            raise TypeError(f"Class {strategy_class.__name__} must inherit from PromptStrategy.")
        if name in cls._strategies:
            _prompt_strategy_logger.warning(
                "strategy_re-registration", name=name, new_class=strategy_class.__name__
            )
        cls._strategies[name] = strategy_class

    @classmethod
    def get_strategy(
        cls, name: str, logger_instance: Union[logging.Logger, structlog.BoundLogger]
    ) -> PromptStrategy:
        """
        Retrieves an instance of the specified prompt strategy.

        If 'default' is requested, it checks the REASONER_PROMPT_STRATEGY
        environment variable to allow for deploy-time configuration.
        """
        strategy_name = name
        # Deployability: Allow overriding the 'default' strategy via an environment variable.
        if name == "default":
            env_strategy = os.getenv("REASONER_PROMPT_STRATEGY")
            if env_strategy and env_strategy in cls._strategies:
                strategy_name = env_strategy
                _prompt_strategy_logger.debug("default_strategy_overridden", strategy=strategy_name)

        strategy_class = cls._strategies.get(strategy_name)
        if not strategy_class:
            _prompt_strategy_logger.error(
                "strategy_not_found",
                requested_name=name,
                available=list(cls._strategies.keys()),
            )
            raise ValueError(
                f"No prompt strategy registered with name: '{name}'. Available: {list(cls._strategies.keys())}"
            )

        return strategy_class(logger_instance)

    @classmethod
    def list_strategies(cls) -> List[str]:
        """Returns a list of all registered strategy names."""
        return list(cls._strategies.keys())


# Register default strategies upon module import
PromptStrategyFactory.register_strategy("default", DefaultPromptStrategy)
PromptStrategyFactory.register_strategy("concise", ConcisePromptStrategy)
PromptStrategyFactory.register_strategy("verbose", VerbosePromptStrategy)
PromptStrategyFactory.register_strategy("structured", StructuredPromptStrategy)
