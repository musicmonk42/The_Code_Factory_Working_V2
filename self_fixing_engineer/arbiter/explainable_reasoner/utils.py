import asyncio
import collections
import json
import logging
import re
import time

# --- Start of Placeholder for METRICS ---
# In a real application, METRICS would be imported. For this standalone file,
# we define a dummy structure to make the code executable and demonstrate changes.
from collections import defaultdict
from datetime import date, datetime
from datetime import time as dt_time
from functools import wraps
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, ParamSpec, Union

from arbiter.explainable_reasoner.reasoner_config import ReasonerConfig, SensitiveValue

# Import ReasonerError and ReasonerErrorCode for consistent error handling
from arbiter.explainable_reasoner.reasoner_errors import ReasonerError, ReasonerErrorCode
from pydantic import BaseModel


class DummyCounter:
    def labels(self, *args, **kwargs):
        return self

    def inc(self, value=1):
        pass


class DummyHistogram:
    def labels(self, *args, **kwargs):
        return self

    def observe(self, value):
        pass


METRICS = defaultdict(DummyCounter)
METRICS["context_validation_errors"] = DummyCounter()
METRICS["sensitive_data_redaction_total"] = DummyCounter()
METRICS["reasoner_sanitization_latency_seconds"] = DummyHistogram()
# --- End of Placeholder for METRICS ---

_utils_logger = logging.getLogger("ReasonerUtils")
_utils_logger.setLevel(logging.INFO)

# --- Availability Checks for Optional Dependencies ---
try:
    import redis.asyncio as aioredis

    REDIS_AVAILABLE = True
except ImportError:
    aioredis = None
    REDIS_AVAILABLE = False

# Conditional import for MultiModalData and schemas
try:
    from arbiter.models.multi_modal_schemas import (
        AudioAnalysisResult,
        ImageAnalysisResult,
        MultiModalAnalysisResult,
        MultiModalData,
        VideoAnalysisResult,
    )

    MULTI_MODAL_SCHEMAS_AVAILABLE = True
except ImportError:
    _utils_logger.warning(
        "Warning: arbiter.models.multi_modal_schemas not found. Using dummy MultiModalData/Schemas for standalone mode in utils."
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
            else:
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
        captioning_result: Optional[Any] = None
        ocr_result: Optional[Any] = None
        image_id: str = "dummy_id"
        detected_objects: Optional[List[str]] = None

    class AudioAnalysisResult(MultiModalAnalysisResult):
        transcription: Optional[Any] = None
        audio_id: str = "dummy_id"
        sentiment: Optional[Any] = None
        keywords: Optional[List[str]] = None

    class VideoAnalysisResult(MultiModalAnalysisResult):
        summary_result: Optional[Any] = None
        audio_transcription_result: Optional[Any] = None
        video_id: str = "dummy_id"
        main_entities: Optional[List[str]] = None

    MULTI_MODAL_SCHEMAS_AVAILABLE = False


def _format_multimodal_for_prompt(
    data: Union[ImageAnalysisResult, AudioAnalysisResult, VideoAnalysisResult],
) -> str:
    """
    Formats structured multi-modal analysis results into a string for LLM prompts.
    This function is robust and safe to use with dummy schemas as it checks for
    attribute existence before access.
    """
    parts = []
    if isinstance(data, ImageAnalysisResult):
        if hasattr(data, "image_id"):
            parts.append(f"--- Image Analysis (ID: {data.image_id}) ---")
        if (
            data.captioning_result
            and hasattr(data.captioning_result, "caption")
            and data.captioning_result.caption
        ):
            parts.append(f"[Image Caption]: {data.captioning_result.caption}")
        if data.ocr_result and hasattr(data.ocr_result, "text") and data.ocr_result.text:
            truncated_ocr = (
                data.ocr_result.text[:500] + "..."
                if len(data.ocr_result.text) > 500
                else data.ocr_result.text
            )
            parts.append(f"[OCR Text]: {truncated_ocr}")
        if hasattr(data, "detected_objects") and data.detected_objects:
            parts.append(f"[Detected Objects]: {', '.join(data.detected_objects)}")
    elif isinstance(data, AudioAnalysisResult):
        if hasattr(data, "audio_id"):
            parts.append(f"--- Audio Analysis (ID: {data.audio_id}) ---")
        if data.transcription and hasattr(data.transcription, "text") and data.transcription.text:
            truncated_transcript = (
                data.transcription.text[:1000] + "..."
                if len(data.transcription.text) > 1000
                else data.transcription.text
            )
            parts.append(f"[Audio Transcript]: {truncated_transcript}")
        if hasattr(data, "sentiment") and data.sentiment:
            parts.append(f"[Audio Sentiment]: {data.sentiment}")
        if hasattr(data, "keywords") and data.keywords:
            parts.append(f"[Audio Keywords]: {', '.join(data.keywords)}")
    elif isinstance(data, VideoAnalysisResult):
        if hasattr(data, "video_id"):
            parts.append(f"--- Video Analysis (ID: {data.video_id}) ---")
        if hasattr(data, "summary_result") and data.summary_result:
            parts.append(f"[Video Summary]: {data.summary_result}")
        if hasattr(data, "audio_transcription_result") and data.audio_transcription_result:
            parts.append(f"[Audio Transcription]: {data.audio_transcription_result}")
        if hasattr(data, "main_entities") and data.main_entities:
            parts.append(f"[Main Entities]: {', '.join(data.main_entities)}")
    return "\n".join(parts) + "\n" if parts else ""


async def _sanitize_context(context: Dict[str, Any], config: ReasonerConfig) -> Dict[str, Any]:
    """
    Sanitizes context, handling circular references, multi-modal data, JSON serialization,
    and sensitive information redaction.
    """
    start_time = time.monotonic()

    # Expanded patterns with comments for maintainability
    default_redact_patterns = [
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # Email addresses
        r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b",  # Credit card numbers (basic)
        r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b",  # Social Security Numbers (basic)
        r"[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}",  # Common JWT format
    ]

    redact_keys_lower = [k.lower() for k in config.sanitization_options.get("redact_keys", [])]
    redact_patterns = config.sanitization_options.get("redact_patterns", [])
    compiled_patterns = [
        re.compile(p, re.IGNORECASE) for p in redact_patterns + default_redact_patterns
    ]

    def _redact_value(key: str, value: Any) -> Any:
        """Helper to check for and redact sensitive values based on keys and regex patterns."""
        if key.lower() in redact_keys_lower:
            METRICS["sensitive_data_redaction_total"].labels(redaction_type="key").inc()
            return "[REDACTED]"

        if isinstance(value, str):
            for pattern in compiled_patterns:
                if pattern.search(value):
                    METRICS["sensitive_data_redaction_total"].labels(redaction_type="pattern").inc()
                    return "[REDACTED]"

        return value

    def _json_serializable_converter(obj: Any, current_depth: int = 0, visited: set = None) -> Any:
        """
        Recursively converts objects to JSON-serializable types, handling nesting
        depth and circular references.
        """
        if visited is None:
            visited = set()

        # Handle circular references
        if id(obj) in visited:
            return "[CIRCULAR_REFERENCE]"

        # Handle SensitiveValue specifically before other checks
        if isinstance(obj, SensitiveValue):
            return "[REDACTED]"

        # Check depth before processing nested structures
        if current_depth >= config.sanitization_options.get("max_nesting_depth", 10):
            _utils_logger.warning(
                f"Max context nesting depth ({config.sanitization_options.get('max_nesting_depth', 10)}) exceeded."
            )
            METRICS["context_validation_errors"].labels(
                error_code=ReasonerErrorCode.CONTEXT_MAX_DEPTH_EXCEEDED
            ).inc()
            return "[MAX_DEPTH_EXCEEDED]"

        new_visited = visited | {id(obj)}
        allowed_types = config.sanitization_options.get(
            "allowed_primitive_types", (str, int, float, bool, type(None))
        )

        if isinstance(obj, allowed_types):
            return obj
        if isinstance(obj, (datetime, date, dt_time)):
            return obj.isoformat()
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, dict):
            # Apply redaction here
            sanitized_dict = {}
            for k, v in obj.items():
                v = _redact_value(k, v)
                sanitized_dict[k] = _json_serializable_converter(v, current_depth + 1, new_visited)
            return sanitized_dict
        if isinstance(obj, list):
            return [
                _json_serializable_converter(elem, current_depth + 1, new_visited) for elem in obj
            ]

        if MULTI_MODAL_SCHEMAS_AVAILABLE and isinstance(
            obj, (ImageAnalysisResult, AudioAnalysisResult, VideoAnalysisResult)
        ):
            return _format_multimodal_for_prompt(obj)

        if isinstance(obj, BaseModel):
            return _json_serializable_converter(obj.model_dump(), current_depth + 1, new_visited)

        _utils_logger.warning(f"Unsupported type {type(obj)} found during sanitization.")
        METRICS["context_validation_errors"].labels(
            error_code=ReasonerErrorCode.CONTEXT_UNSUPPORTED_TYPE
        ).inc()
        return str(obj)

    try:
        # Pydantic schema validation happens before sanitization for more robust handling
        if config.sanitization_options.get("context_schema_model"):
            try:
                validated_model = config.sanitization_options[
                    "context_schema_model"
                ].model_validate(context)
                context = validated_model.model_dump()
            except Exception as e:
                raise ReasonerError(
                    f"Context schema validation failed: {e}",
                    code=ReasonerErrorCode.CONTEXT_SCHEMA_VIOLATION,
                    original_exception=e,
                )

        sanitized_context = _json_serializable_converter(context)
        context_json = json.dumps(sanitized_context, sort_keys=True, ensure_ascii=False)

        max_size_bytes = config.sanitization_options.get("max_size_bytes", 4096)
        if len(context_json.encode("utf-8")) > max_size_bytes:
            _utils_logger.warning(f"Context size exceeds max_size ({max_size_bytes} bytes).")
            return {"_truncated_context_error": "Original context too large."}

        METRICS["reasoner_sanitization_latency_seconds"].observe(time.monotonic() - start_time)
        return json.loads(context_json)

    except ReasonerError:
        raise
    except Exception as e:
        _utils_logger.error(f"Unexpected error during context sanitization: {e}", exc_info=True)
        raise ReasonerError(
            f"Failed to sanitize context: {e}",
            code=ReasonerErrorCode.CONTEXT_SANITIZATION_FAILED,
            original_exception=e,
        )


def _simple_text_sanitize(text: str, max_length: int = 1024) -> str:
    """Sanitizes a simple text string, removing control characters and limiting length."""
    if not isinstance(text, str):
        raise TypeError("Input must be a string")
    # Remove control characters including zero-width spaces
    text = re.sub(r"[\x00-\x1F\x7F\u200b]+", "", text.strip())
    text = re.sub(r"\s+", " ", text).strip()  # Normalize whitespace
    text = re.sub(r"<[^>]*>", "", text)  # Basic HTML tag stripping
    return text[:max_length]


def _rule_based_fallback(query: str, context: Dict[str, Any], mode: str) -> str:
    """Provides a rule-based fallback response if the primary model fails."""
    summary_parts = []
    for k, v in context.items():
        if isinstance(v, (str, int, float, bool)):
            summary_parts.append(f"{k}: {v}")
    summary = ", ".join(summary_parts) if summary_parts else "no specific context"

    response_phrases = {
        "explain": f"[Fallback] Based on available information ({summary}), the requested explanation for '{query}' could not be generated by the primary model.",
        "reason": f"[Fallback] Based on available information ({summary}), a detailed reasoning for '{query}' could not be generated by the primary model.",
    }
    return response_phrases.get(mode, f"[Fallback] Could not process '{query}'.")


P = ParamSpec("P")


def rate_limited(
    calls_per_second: float, key_extractor: Optional[Callable[[Any, Any], str]] = None
):
    """
    A decorator for async functions to apply rate limiting, with support for
    distributed limiting via Redis if available.
    """
    _local_last_call_time: Dict[str, float] = collections.defaultdict(float)
    _local_locks: Dict[str, asyncio.Lock] = collections.defaultdict(asyncio.Lock)
    _interval = 1.0 / calls_per_second

    def decorator(func: Callable[P, Awaitable[Any]]):
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            redis_client = None
            if REDIS_AVAILABLE and args and hasattr(args[0], "_redis_client"):
                redis_client = getattr(args[0], "_redis_client", None)

            key = "global"
            if key_extractor:
                try:
                    key = key_extractor(args[0], kwargs)
                except Exception as e:
                    _utils_logger.error(
                        f"Error in rate_limited key_extractor: {e}. Using 'global' key."
                    )

            rate_limit_key = f"rate_limit:{func.__name__}:{key}"

            if redis_client:
                try:
                    # Use a Lua script for atomic rate limiting to prevent race conditions
                    # without an explicit lock.
                    lua_script = """
                    local key = KEYS[1]
                    local now = tonumber(ARGV[1])
                    local interval = tonumber(ARGV[2])

                    local last_call_time = tonumber(redis.call('get', key) or 0)
                    local time_since_last_call = now - last_call_time

                    if time_since_last_call < interval then
                        return interval - time_since_last_call
                    else
                        redis.call('set', key, now)
                        return 0
                    end
                    """
                    wait_time = await redis_client.eval(
                        lua_script, 1, rate_limit_key, time.monotonic(), _interval
                    )
                    if wait_time > 0:
                        _utils_logger.warning(
                            f"Distributed rate limit hit for key '{key}'. Waiting {wait_time:.2f}s."
                        )
                        await asyncio.sleep(wait_time)
                except Exception as e:
                    # Fall back to local rate limiting if Redis fails
                    _utils_logger.warning(
                        f"Redis rate limiting failed: {e}. Falling back to local rate limiting."
                    )
                    async with _local_locks[key]:
                        current_time = time.monotonic()
                        time_since_last_call = current_time - _local_last_call_time[key]
                        if time_since_last_call < _interval:
                            wait_time = _interval - time_since_last_call
                            _utils_logger.warning(
                                f"Local rate limit hit for key '{key}'. Waiting {wait_time:.2f}s."
                            )
                            await asyncio.sleep(wait_time)
                        _local_last_call_time[key] = time.monotonic()
            else:
                async with _local_locks[key]:
                    current_time = time.monotonic()
                    time_since_last_call = current_time - _local_last_call_time[key]
                    if time_since_last_call < _interval:
                        wait_time = _interval - time_since_last_call
                        _utils_logger.warning(
                            f"Local rate limit hit for key '{key}'. Waiting {wait_time:.2f}s."
                        )
                        await asyncio.sleep(wait_time)
                    _local_last_call_time[key] = time.monotonic()

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def redact_pii(data):
    """Redact PII from data"""
    import re

    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if isinstance(v, str):
                # Redact email
                v = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL]", v)
                # Redact phone
                v = re.sub(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", "[PHONE]", v)
            result[k] = v
        return result
    elif isinstance(data, str):
        data = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL]", data)
        data = re.sub(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", "[PHONE]", data)
        return data
    return data
