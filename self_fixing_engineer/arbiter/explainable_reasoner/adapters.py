import logging
import asyncio
import time
import base64
import mimetypes
import json
import os
import random
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Union, Type, Callable, Tuple, AsyncGenerator
from functools import lru_cache, wraps

import httpx
from pydantic import BaseModel, Field, HttpUrl, ValidationError

# Real internal imports (enforce)
from arbiter.explainable_reasoner.reasoner_errors import ReasonerError, ReasonerErrorCode
from arbiter.explainable_reasoner.reasoner_config import SensitiveValue
from arbiter.explainable_reasoner.metrics import METRICS
from prometheus_client import Counter, Histogram, REGISTRY
from prometheus_client import Histogram
from arbiter.explainable_reasoner.utils import redact_pii, rate_limited

# Structured logging
import structlog
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(indent=2),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
)
_logger = structlog.get_logger(__name__)

# Optional breakers
try:
    import pybreaker
    BREAKER_AVAILABLE = True
except ImportError:
    BREAKER_AVAILABLE = False
    pybreaker = None
    _logger.warning("pybreaker missing; circuit breakers disabled")

# Moved import here to resolve circular dependency
from arbiter.explainable_reasoner.metrics import get_or_create_metric

# Define metrics dynamically
# Create metrics with duplicate check
try:
    INFERENCE_LATENCY = Histogram('model_inference_latency_seconds', 'Inference latency', labelnames=['adapter', 'endpoint'])
except ValueError:
    # Metric already exists, find it in registry
    for collector in REGISTRY._collector_to_names:
        if 'model_inference_latency_seconds' in REGISTRY._collector_to_names[collector]:
            INFERENCE_LATENCY = collector
            break
try:
    INFERENCE_ERRORS = Counter('model_inference_errors_total', 'Inference errors', labelnames=['adapter', 'endpoint', 'code'])
except ValueError:
    # Metric already exists, find it in registry
    for collector in REGISTRY._collector_to_names:
        if 'model_inference_errors_total' in REGISTRY._collector_to_names[collector]:
            INFERENCE_ERRORS = collector
            break
STREAM_CHUNKS = get_or_create_metric(Counter, 'model_stream_chunks_total', 'Streamed chunks', ('adapter', 'endpoint'))
HEALTH_CHECK_ERRORS = get_or_create_metric(Counter, 'adapter_health_check_errors_total', 'Health check failures', ('adapter'))


# Export for test mocking
__all__ = [
    'LLMAdapter', 'OpenAIGPTAdapter', 'GeminiAPIAdapter', 
    'AnthropicAdapter', 'LLMAdapterFactory', 'retry',
    'ReasonerError', 'ReasonerErrorCode', 'SensitiveValue',
    'METRICS',
]


# --- LLM Adapter Interfaces ---
class LLMAdapter(ABC):
    """
    Abstract base for LLM adapters. Handles text/multimodal inference/streaming with resilience/security.
    """
    def __init__(self, model_name: str, api_key: SensitiveValue, base_url: str,
                 timeout: float = float(os.getenv('ADAPTER_TIMEOUT', '60.0')),
                 max_retries: int = int(os.getenv('ADAPTER_MAX_RETRIES', '5')),
                 backoff_factor: float = float(os.getenv('ADAPTER_BACKOFF_FACTOR', '1.0'))):
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self._client = None
        if BREAKER_AVAILABLE:
            self._breaker = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=60)
        else:
            self._breaker = None
            _logger.warning("No breakers; failure cascading possible")
        _logger.info("adapter_init", adapter=type(self).__name__, model=model_name, base_url=base_url)
    
    async def _get_client(self) -> httpx.AsyncClient:
        """
        Lazily initializes and returns the httpx client.

        Returns:
            An httpx.AsyncClient instance.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
                verify=True  # Security: Enforce SSL
            )
        return self._client
    
    async def rotate_key(self, new_key: str):
        """
        Rotates the API key securely.
        
        Args:
            new_key: The new API key string.
        """
        self.api_key = SensitiveValue(new_key)
        if self._client:
            await self._client.aclose()
            self._client = None
        _logger.info("key_rotated", adapter=type(self).__name__)

    @abstractmethod
    async def generate(self, prompt: str, multi_modal_data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> str:
        """
        Generates a text response based on a prompt and optional multi-modal data.

        Args:
            prompt: The primary text prompt.
            multi_modal_data: A dictionary containing multi-modal inputs, such as
                              images, where keys are identifiers and values are
                              Pydantic models containing `data` (bytes) and `data_type`.
            **kwargs: Additional generation parameters (e.g., `max_tokens`, `temperature`).

        Returns:
            The generated text string.
        
        Raises:
            ReasonerError: On API failure, timeout, or invalid input.
        """
        raise NotImplementedError

    @abstractmethod
    async def stream_generate(self, prompt: str, multi_modal_data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> AsyncGenerator[str, None]:
        """
        Streams a text response based on a prompt and optional multi-modal data.

        Args:
            prompt: The primary text prompt.
            multi_modal_data: A dictionary containing multi-modal inputs.
            **kwargs: Additional generation parameters.

        Yields:
            Asynchronous chunks of the generated text string.
        
        Raises:
            ReasonerError: On API failure, timeout, or invalid input.
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Checks API connectivity.

        Returns:
            True if API is reachable, False otherwise.
        Raises:
            ReasonerError: On unexpected errors.
        """
        pass

    async def aclose(self):
        """
        Closes the underlying HTTP client session if it exists.
        """
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            _logger.info("client_closed", adapter=type(self).__name__)


# --- Retry Decorator with Metrics ---
def retry(max_retries: int = 5, initial_backoff_delay: float = 1.0,
          exceptions_to_catch: Tuple[Type[Exception], ...] = (
              httpx.RequestError, httpx.TimeoutException, httpx.ConnectError)):
    """
    Decorator for async functions with retry logic, exponential backoff, and jitter.

    Args:
        max_retries: Maximum retry attempts.
        initial_backoff_delay: Base delay for backoff (seconds).
        exceptions_to_catch: Exceptions to retry on.
    Returns:
        Decorated function with retry logic.
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            async def operation_with_retries():
                endpoint = kwargs.get('endpoint', 'unknown')
                for attempt in range(max_retries):
                    try:
                        return await func(self, *args, **kwargs)
                    except httpx.HTTPStatusError as e:
                        status_code = e.response.status_code
                        if status_code == 429:
                            retry_after = float(e.response.headers.get('Retry-After', initial_backoff_delay * (2 ** attempt)))
                            _logger.warning("rate_limit_hit", adapter=type(self).__name__, endpoint=endpoint, retry_after=retry_after)
                            await asyncio.sleep(retry_after)
                        else:
                            _logger.warning("http_status_error", adapter=type(self).__name__, endpoint=endpoint, status_code=status_code, error=str(e))
                            INFERENCE_ERRORS.labels(adapter=type(self).__name__, endpoint=endpoint, code=str(status_code)).inc()
                            if attempt == max_retries - 1:
                                raise ReasonerError(f"API error after {max_retries} retries: {str(e)}", ReasonerErrorCode.MODEL_INFERENCE_FAILED, e)
                    except exceptions_to_catch as e:
                        _logger.warning("retry_attempt", attempt=attempt+1, max=max_retries, error=str(e), adapter=type(self).__name__, func=func.__name__)
                        INFERENCE_ERRORS.labels(adapter=type(self).__name__, endpoint=endpoint, code=type(e).__name__).inc()
                        if attempt == max_retries - 1:
                            raise ReasonerError(f"Failed after {max_retries} retries: {str(e)}", ReasonerErrorCode.SERVICE_UNAVAILABLE, e)
                    delay = initial_backoff_delay * (2 ** attempt) + random.random() * 0.5
                    await asyncio.sleep(delay)

            if self._breaker:
                try:
                    return await self._breaker.call_async(operation_with_retries)
                except pybreaker.CircuitBreakerError as e:
                    _logger.error("circuit_breaker_open", adapter=type(self).__name__, func=func.__name__)
                    raise ReasonerError("Circuit breaker open", ReasonerErrorCode.SERVICE_UNAVAILABLE, e)
            else:
                return await operation_with_retries()
        return wrapper
    return decorator


# --- Concrete LLM Adapter Implementations ---
class OpenAIGPTAdapter(LLMAdapter):
    """
    Adapter for OpenAI API compatible models (e.g., GPT-4, GPT-3.5-Turbo).
    This adapter supports multi-modal inputs (text and images) for vision-capable models.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client_headers = {
            "Authorization": f"Bearer {self.api_key.get_actual_value()}",
            "Content-Type": "application/json"
        }

    def _build_openai_messages(self, prompt: str, multi_modal_data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Constructs the 'messages' payload for the OpenAI API, with PII redaction and validation.

        Args:
            prompt: The primary text prompt.
            multi_modal_data: A dictionary of multi-modal data.

        Returns:
            A list of message dictionaries formatted for the OpenAI API.

        Raises:
            ReasonerError: If an image is too large.
        """
        if not isinstance(prompt, str):
            raise ReasonerError("Prompt must be a string", ReasonerErrorCode.INVALID_INPUT)
        prompt = redact_pii(prompt)
        # Assuming 128k context for GPT-4-turbo
        if len(prompt) > 128000:
            raise ReasonerError("Prompt too long", ReasonerErrorCode.INVALID_INPUT)
        content = [{"type": "text", "text": prompt}]
        if multi_modal_data:
            if not isinstance(multi_modal_data, dict):
                raise ReasonerError("Multimodal data must be a dictionary", ReasonerErrorCode.INVALID_INPUT)
            _logger.info("multimodal_encode", count=len(multi_modal_data))
            for key, data in multi_modal_data.items():
                if not isinstance(data, dict) or 'data_type' not in data or 'data' not in data:
                    raise ReasonerError(f"Invalid multimodal data for key {key}", ReasonerErrorCode.INVALID_INPUT)
                if data.get('data_type') == 'image':
                    if len(data.get('data', b'')) > 10 * 1024 * 1024:
                        raise ReasonerError(f"Image too large for key {key}", ReasonerErrorCode.INVALID_INPUT)
                    mime = mimetypes.guess_type(data.get('filename', ''))[0] or 'image/jpeg'
                    if mime not in ['image/jpeg', 'image/png', 'image/gif', 'image/webp']:
                        raise ReasonerError(f"Unsupported image type {mime} for key {key}", ReasonerErrorCode.INVALID_INPUT)
                    b64 = base64.b64encode(data['data']).decode('utf-8')
                    content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{b64}"
                        }
                    })
        return [{"role": "user", "content": content}]

    @retry()
    async def generate(self, prompt: str, multi_modal_data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> str:
        """
        Generates text using the OpenAI Chat Completions API, with multi-modal support.
        
        Args:
            prompt: The primary text prompt.
            multi_modal_data: A dictionary of multi-modal inputs.
            **kwargs: Additional generation parameters.

        Returns:
            The generated text string.

        Raises:
            ReasonerError: On API failure, timeout, or unexpected errors.
        """
        endpoint = "chat/completions"
        _logger.debug("openai_request_start", model=self.model_name, endpoint=endpoint)
        
        max_tokens = kwargs.get("max_tokens", 2048)
        temperature = kwargs.get("temperature", 0.7)
        if not (0 <= temperature <= 1):
            raise ReasonerError("Temperature must be between 0 and 1", ReasonerErrorCode.INVALID_INPUT)
        # GPT-4 has a max_tokens limit of 4096, but newer models are higher. Sticking to a safe lower bound.
        if max_tokens > 4096 and "gpt-4" in self.model_name.lower(): 
            raise ReasonerError("max_tokens exceeds limit (4096)", ReasonerErrorCode.INVALID_INPUT)

        start_time = time.monotonic()
        messages = self._build_openai_messages(prompt, multi_modal_data)
        payload = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        
        try:
            client = await self._get_client()
            response = await client.post(f"{self.base_url}/{endpoint}", json=payload, headers=self._client_headers)
            response.raise_for_status()
            result = response.json()
            generation = result['choices'][0]['message']['content']
            
            latency = time.monotonic() - start_time
            INFERENCE_LATENCY.labels(adapter=self.__class__.__name__, endpoint=endpoint).observe(latency)
            return generation
        except httpx.HTTPStatusError as e:
            _logger.error("openai_api_error", status_code=e.response.status_code, text=e.response.text, endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code=str(e.response.status_code)).inc()
            raise ReasonerError(f"OpenAI API error: {e.response.text}", code=ReasonerErrorCode.MODEL_INFERENCE_FAILED, original_exception=e) from e
        except Exception as e:
            _logger.critical("openai_unexpected_error", exc_info=True, error=str(e), endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code='unexpected').inc()
            raise ReasonerError(f"An unexpected error occurred: {e}", code=ReasonerErrorCode.UNEXPECTED_ERROR, original_exception=e) from e

    @retry()
    async def stream_generate(self, prompt: str, multi_modal_data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> AsyncGenerator[str, None]:
        """
        Streams a response from the OpenAI Chat Completions API, with multi-modal support.

        Args:
            prompt: The primary text prompt.
            multi_modal_data: A dictionary of multi-modal inputs.
            **kwargs: Additional generation parameters.

        Yields:
            Asynchronous chunks of the generated text string.

        Raises:
            ReasonerError: On API failure, timeout, or unexpected errors.
        """
        endpoint = "chat/completions"
        _logger.debug("openai_stream_start", model=self.model_name, endpoint=endpoint)

        max_tokens = kwargs.get("max_tokens", 2048)
        temperature = kwargs.get("temperature", 0.7)
        if not (0 <= temperature <= 1):
            raise ReasonerError("Temperature must be between 0 and 1", ReasonerErrorCode.INVALID_INPUT)
        if max_tokens > 4096 and "gpt-4" in self.model_name.lower():
            raise ReasonerError("max_tokens exceeds limit (4096)", ReasonerErrorCode.INVALID_INPUT)

        start_time = time.monotonic()
        messages = self._build_openai_messages(prompt, multi_modal_data)
        payload = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True
        }
        headers = self._client_headers

        try:
            client = await self._get_client()
            async with client.stream("POST", f"{self.base_url}/{endpoint}", json=payload, headers=headers) as response:
                response.raise_for_status()
                async for chunk_bytes in response.aiter_bytes():
                    chunk_str = chunk_bytes.decode('utf-8')
                    if chunk_str.strip():
                        for line in chunk_str.splitlines():
                            line = line.strip()
                            if not line or line == "data: [DONE]":
                                continue
                            try:
                                data = json.loads(line.replace("data: ", "", 1))
                                if data.get('choices', [{}])[0].get('delta', {}).get('content'):
                                    text_chunk = data['choices'][0]['delta']['content']
                                    STREAM_CHUNKS.labels(adapter=self.__class__.__name__, endpoint=endpoint).inc()
                                    yield text_chunk
                            except json.JSONDecodeError as e:
                                _logger.error("json_decode_error", line=line, error=str(e), endpoint=endpoint)
            
            latency = time.monotonic() - start_time
            INFERENCE_LATENCY.labels(adapter=self.__class__.__name__, endpoint=endpoint).observe(latency)
        except httpx.TimeoutException as e:
            _logger.error("openai_stream_timeout", timeout=self.timeout, endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code='timeout').inc()
            raise ReasonerError(f"Stream timed out after {self.timeout}s", ReasonerErrorCode.TIMEOUT, e)
        except httpx.HTTPStatusError as e:
            _logger.error("openai_stream_error", status_code=e.response.status_code, text=e.response.text, endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code=str(e.response.status_code)).inc()
            raise ReasonerError(f"OpenAI API stream error: {e.response.text}", ReasonerErrorCode.MODEL_INFERENCE_FAILED, e)
        except Exception as e:
            _logger.critical("openai_stream_unexpected_error", exc_info=True, error=str(e), endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code='unexpected').inc()
            raise ReasonerError(f"Unexpected error during streaming: {str(e)}", ReasonerErrorCode.UNEXPECTED_ERROR, e)

    async def health_check(self) -> bool:
        """
        Checks OpenAI API connectivity by querying /v1/models.
        
        Returns:
            True if API is reachable, False otherwise.
        Raises:
            ReasonerError: On unexpected errors during the check.
        """
        endpoint = "models"
        _logger.debug("openai_health_check_start", model=self.model_name, endpoint=endpoint)
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/{endpoint}", headers=self._client_headers)
            response.raise_for_status()
            _logger.info("openai_health_check_success")
            return True
        except httpx.HTTPError as e:
            _logger.error("openai_health_check_failed", error=str(e))
            HEALTH_CHECK_ERRORS.labels(adapter=self.__class__.__name__).inc()
            return False
        except Exception as e:
            _logger.critical("openai_health_check_unexpected", exc_info=True, error=str(e))
            raise ReasonerError(f"Health check failed: {str(e)}", ReasonerErrorCode.SERVICE_UNAVAILABLE, e)


class GeminiAPIAdapter(LLMAdapter):
    """
    Adapter for the Google Gemini API (e.g., gemini-1.5-pro-latest).
    This adapter supports multi-modal inputs by encoding them into the request payload.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._api_key_param = self.api_key.get_actual_value()

    def _build_gemini_parts(self, prompt: str, multi_modal_data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Constructs the 'parts' payload for the Gemini API, with redaction and validation.

        Args:
            prompt: The primary text prompt.
            multi_modal_data: A dictionary of multi-modal data.

        Returns:
            A list of parts dictionaries formatted for the Gemini API.

        Raises:
            ReasonerError: If an image is too large.
        """
        if not isinstance(prompt, str):
            raise ReasonerError("Prompt must be a string", ReasonerErrorCode.INVALID_INPUT)
        prompt = redact_pii(prompt)
        parts = [{"text": prompt}]
        if multi_modal_data:
            if not isinstance(multi_modal_data, dict):
                raise ReasonerError("Multimodal data must be a dictionary", ReasonerErrorCode.INVALID_INPUT)
            _logger.info("multimodal_encode", count=len(multi_modal_data))
            for key, data in multi_modal_data.items():
                if not isinstance(data, dict) or 'data_type' not in data or 'data' not in data:
                    raise ReasonerError(f"Invalid multimodal data for key {key}", ReasonerErrorCode.INVALID_INPUT)
                if data.get('data_type') == 'image':
                    if len(data.get('data', b'')) > 10 * 1024 * 1024:
                        raise ReasonerError(f"Image too large for key {key}", ReasonerErrorCode.INVALID_INPUT)
                    mime_type = mimetypes.guess_type(data.get('filename', 'image.jpg'))[0] or "image/jpeg"
                    if mime_type not in ['image/jpeg', 'image/png', 'image/gif', 'image/webp']:
                        raise ReasonerError(f"Unsupported image type {mime_type} for key {key}", ReasonerErrorCode.INVALID_INPUT)
                    b64_image = base64.b64encode(data['data']).decode('utf-8')
                    parts.append({"inline_data": {"mime_type": mime_type, "data": b64_image}})
        return parts

    @retry()
    async def generate(self, prompt: str, multi_modal_data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> str:
        """
        Generates text using the Gemini API, with support for multi-modal data.
        
        Args:
            prompt: The primary text prompt.
            multi_modal_data: A dictionary of multi-modal inputs.
            **kwargs: Additional generation parameters.

        Returns:
            The generated text string.

        Raises:
            ReasonerError: On API failure, timeout, or unexpected errors.
        """
        endpoint = "generateContent"
        _logger.debug("gemini_request_start", model=self.model_name, endpoint=endpoint)
        
        max_tokens = kwargs.get("max_tokens", 2048)
        temperature = kwargs.get("temperature", 0.7)
        if not (0 <= temperature <= 1):
            raise ReasonerError("Temperature must be between 0 and 1", ReasonerErrorCode.INVALID_INPUT)
        if max_tokens > 8192:
            raise ReasonerError("maxOutputTokens exceeds limit (8192)", ReasonerErrorCode.INVALID_INPUT)

        start_time = time.monotonic()
        url_path = f"/{self.model_name}:{endpoint}"
        content_parts = self._build_gemini_parts(prompt, multi_modal_data)
        
        payload = {
            "contents": [{"parts": content_parts}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }

        try:
            client = await self._get_client()
            response = await client.post(f"{self.base_url}{url_path}", params={"key": self._api_key_param}, json=payload)
            response.raise_for_status()
            result = response.json()
            if not result.get('candidates') or not result['candidates'][0].get('content'):
                finish_reason = result['candidates'][0].get('finishReason', 'UNKNOWN')
                _logger.warning("gemini_empty_response", finish_reason=finish_reason)
                return f"[MODEL_RESPONSE_BLOCKED: {finish_reason}]"

            generation = result['candidates'][0]['content']['parts'][0]['text']
            latency = time.monotonic() - start_time
            INFERENCE_LATENCY.labels(adapter=self.__class__.__name__, endpoint=endpoint).observe(latency)
            return generation
        except httpx.HTTPStatusError as e:
            _logger.error("gemini_api_error", status_code=e.response.status_code, text=e.response.text, endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code=str(e.response.status_code)).inc()
            raise ReasonerError(f"Gemini API returned an error: {e.response.text}", code=ReasonerErrorCode.MODEL_INFERENCE_FAILED, original_exception=e) from e
        except Exception as e:
            _logger.critical("gemini_unexpected_error", exc_info=True, error=str(e), endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code='unexpected').inc()
            raise ReasonerError(f"An unexpected error occurred: {e}", code=ReasonerErrorCode.UNEXPECTED_ERROR, original_exception=e) from e
            
    @retry()
    async def stream_generate(self, prompt: str, multi_modal_data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> AsyncGenerator[str, None]:
        """
        Streams a response from the Gemini API, with multi-modal support.

        Args:
            prompt: The primary text prompt.
            multi_modal_data: A dictionary of multi-modal inputs.
            **kwargs: Additional generation parameters.

        Yields:
            Asynchronous chunks of the generated text string.

        Raises:
            ReasonerError: On API failure, timeout, or unexpected errors.
        """
        endpoint = "streamGenerateContent"
        _logger.debug("gemini_stream_start", model=self.model_name, endpoint=endpoint)

        max_tokens = kwargs.get("max_tokens", 2048)
        temperature = kwargs.get("temperature", 0.7)
        if not (0 <= temperature <= 1):
            raise ReasonerError("Temperature must be between 0 and 1", ReasonerErrorCode.INVALID_INPUT)
        if max_tokens > 8192:
            raise ReasonerError("maxOutputTokens exceeds limit (8192)", ReasonerErrorCode.INVALID_INPUT)

        start_time = time.monotonic()
        url_path = f"/{self.model_name}:{endpoint}"
        content_parts = self._build_gemini_parts(prompt, multi_modal_data)
        
        payload = {
            "contents": [{"parts": content_parts}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }

        try:
            client = await self._get_client()
            async with client.stream("POST", f"{self.base_url}{url_path}", params={"key": self._api_key_param}, json=payload) as response:
                response.raise_for_status()
                async for chunk_bytes in response.aiter_bytes():
                    chunk_str = chunk_bytes.decode('utf-8')
                    if chunk_str.strip():
                        for line in chunk_str.splitlines():
                            line = line.strip(', \n')
                            if not line:
                                continue
                            try:
                                data = json.loads(line)
                                if data.get('candidates') and data['candidates'][0].get('content'):
                                    text_chunk = data['candidates'][0]['content']['parts'][0]['text']
                                    STREAM_CHUNKS.labels(adapter=self.__class__.__name__, endpoint=endpoint).inc()
                                    yield text_chunk
                            except json.JSONDecodeError as e:
                                _logger.error("json_decode_error", line=line, error=str(e), endpoint=endpoint)
            
            latency = time.monotonic() - start_time
            INFERENCE_LATENCY.labels(adapter=self.__class__.__name__, endpoint=endpoint).observe(latency)
        except httpx.TimeoutException as e:
            _logger.error("gemini_stream_timeout", timeout=self.timeout, endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code='timeout').inc()
            raise ReasonerError(f"Stream timed out after {self.timeout}s", ReasonerErrorCode.TIMEOUT, e)
        except httpx.HTTPStatusError as e:
            _logger.error("gemini_stream_error", status_code=e.response.status_code, text=e.response.text, endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code=str(e.response.status_code)).inc()
            raise ReasonerError(f"Gemini API stream error: {e.response.text}", ReasonerErrorCode.MODEL_INFERENCE_FAILED, e)
        except Exception as e:
            _logger.critical("gemini_stream_unexpected_error", exc_info=True, error=str(e), endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code='unexpected').inc()
            raise ReasonerError(f"An unexpected error occurred during streaming: {e}", code=ReasonerErrorCode.UNEXPECTED_ERROR, original_exception=e)

    async def health_check(self) -> bool:
        """
        Checks Gemini API connectivity by querying /v1beta/models.

        Returns:
            True if API is reachable, False otherwise.
        Raises:
            ReasonerError: On unexpected errors during the check.
        """
        endpoint = "models"
        _logger.debug("gemini_health_check_start", model=self.model_name, endpoint=endpoint)
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}", params={"key": self._api_key_param})
            response.raise_for_status()
            _logger.info("gemini_health_check_success")
            return True
        except httpx.HTTPError as e:
            _logger.error("gemini_health_check_failed", error=str(e))
            HEALTH_CHECK_ERRORS.labels(adapter=self.__class__.__name__).inc()
            return False
        except Exception as e:
            _logger.critical("gemini_health_check_unexpected", exc_info=True, error=str(e))
            raise ReasonerError(f"Health check failed: {str(e)}", ReasonerErrorCode.SERVICE_UNAVAILABLE, e)


class AnthropicAdapter(LLMAdapter):
    """
    Adapter for the Anthropic Messages API (e.g., Claude 3 family).
    This adapter supports multi-modal inputs (text and images) for Claude 3 models.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client_headers = {
            "x-api-key": self.api_key.get_actual_value(),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }

    def _build_anthropic_messages(self, prompt: str, multi_modal_data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Constructs the 'messages' payload for the Anthropic Messages API, with redaction and validation.

        Args:
            prompt: The primary text prompt.
            multi_modal_data: A dictionary of multi-modal data.

        Returns:
            A list of message dictionaries formatted for the Anthropic API.

        Raises:
            ReasonerError: If an image is too large.
        """
        if not isinstance(prompt, str):
            raise ReasonerError("Prompt must be a string", ReasonerErrorCode.INVALID_INPUT)
        prompt = redact_pii(prompt)
        content = [{"type": "text", "text": prompt}]
        if multi_modal_data:
            if not isinstance(multi_modal_data, dict):
                raise ReasonerError("Multimodal data must be a dictionary", ReasonerErrorCode.INVALID_INPUT)
            _logger.info("multimodal_encode", count=len(multi_modal_data))
            for key, data in multi_modal_data.items():
                if not isinstance(data, dict) or 'data_type' not in data or 'data' not in data:
                    raise ReasonerError(f"Invalid multimodal data for key {key}", ReasonerErrorCode.INVALID_INPUT)
                if data.get('data_type') == 'image':
                    if len(data.get('data', b'')) > 10 * 1024 * 1024:
                        raise ReasonerError(f"Image too large for key {key}", ReasonerErrorCode.INVALID_INPUT)
                    media_type = mimetypes.guess_type(data.get('filename', 'image.jpg'))[0] or "image/jpeg"
                    if media_type not in ['image/jpeg', 'image/png', 'image/gif', 'image/webp']:
                        raise ReasonerError(f"Unsupported image type {media_type} for key {key}", ReasonerErrorCode.INVALID_INPUT)
                    b64_image = base64.b64encode(data['data']).decode('utf-8')
                    content.insert(0, {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_image,
                        }
                    })
        return [{"role": "user", "content": content}]

    @retry()
    async def generate(self, prompt: str, multi_modal_data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> str:
        """
        Generates text using the Anthropic API, with multi-modal support.
        
        Args:
            prompt: The primary text prompt.
            multi_modal_data: A dictionary of multi-modal inputs.
            **kwargs: Additional generation parameters.

        Returns:
            The generated text string.

        Raises:
            ReasonerError: On API failure, timeout, or unexpected errors.
        """
        endpoint = "messages"
        _logger.debug("anthropic_request_start", model=self.model_name, endpoint=endpoint)

        max_tokens = kwargs.get("max_tokens", 2048)
        temperature = kwargs.get("temperature", 0.7)
        if not (0 <= temperature <= 1):
            raise ReasonerError("Temperature must be between 0 and 1", ReasonerErrorCode.INVALID_INPUT)
        if max_tokens > 4096:
            raise ReasonerError("max_tokens exceeds limit (4096)", ReasonerErrorCode.INVALID_INPUT)

        start_time = time.monotonic()
        messages = self._build_anthropic_messages(prompt, multi_modal_data)
        payload = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        
        try:
            client = await self._get_client()
            response = await client.post(f"{self.base_url}/{endpoint}", json=payload, headers=self._client_headers)
            response.raise_for_status()
            result = response.json()
            generation = result['content'][0]['text']
            
            latency = time.monotonic() - start_time
            INFERENCE_LATENCY.labels(adapter=self.__class__.__name__, endpoint=endpoint).observe(latency)
            return generation
        except httpx.HTTPStatusError as e:
            _logger.error("anthropic_api_error", status_code=e.response.status_code, text=e.response.text, endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code=str(e.response.status_code)).inc()
            raise ReasonerError(f"Anthropic API returned an error: {e.response.text}", code=ReasonerErrorCode.MODEL_INFERENCE_FAILED, original_exception=e) from e
        except Exception as e:
            _logger.critical("anthropic_unexpected_error", exc_info=True, error=str(e), endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code='unexpected').inc()
            raise ReasonerError(f"An unexpected error occurred: {e}", code=ReasonerErrorCode.UNEXPECTED_ERROR, original_exception=e) from e

    @retry()
    async def stream_generate(self, prompt: str, multi_modal_data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> AsyncGenerator[str, None]:
        """
        Streams a response from the Anthropic API, with multi-modal support.
        
        Args:
            prompt: The primary text prompt.
            multi_modal_data: A dictionary of multi-modal inputs.
            **kwargs: Additional generation parameters.

        Yields:
            Asynchronous chunks of the generated text string.

        Raises:
            ReasonerError: On API failure, timeout, or unexpected errors.
        """
        endpoint = "messages"
        _logger.debug("anthropic_stream_start", model=self.model_name, endpoint=endpoint)
        
        max_tokens = kwargs.get("max_tokens", 2048)
        temperature = kwargs.get("temperature", 0.7)
        if not (0 <= temperature <= 1):
            raise ReasonerError("Temperature must be between 0 and 1", ReasonerErrorCode.INVALID_INPUT)
        if max_tokens > 4096:
            raise ReasonerError("max_tokens exceeds limit (4096)", ReasonerErrorCode.INVALID_INPUT)
        
        start_time = time.monotonic()
        messages = self._build_anthropic_messages(prompt, multi_modal_data)
        payload = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True
        }
        
        try:
            client = await self._get_client()
            async with client.stream("POST", f"{self.base_url}/{endpoint}", json=payload, headers=self._client_headers) as response:
                response.raise_for_status()
                async for chunk_bytes in response.aiter_bytes():
                    chunk_str = chunk_bytes.decode('utf-8')
                    if chunk_str.strip():
                        for line in chunk_str.splitlines():
                            line = line.strip()
                            if not line or line.startswith("event: ping"):
                                continue
                            try:
                                data = json.loads(line.replace("data: ", "", 1))
                                if data.get('type') == 'content_block_delta' and data['delta'].get('type') == 'text_delta':
                                    text_chunk = data['delta']['text']
                                    STREAM_CHUNKS.labels(adapter=self.__class__.__name__, endpoint=endpoint).inc()
                                    yield text_chunk
                            except json.JSONDecodeError as e:
                                _logger.error("json_decode_error", line=line, error=str(e), endpoint=endpoint)
            
            latency = time.monotonic() - start_time
            INFERENCE_LATENCY.labels(adapter=self.__class__.__name__, endpoint=endpoint).observe(latency)
        except httpx.TimeoutException as e:
            _logger.error("anthropic_stream_timeout", timeout=self.timeout, endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code='timeout').inc()
            raise ReasonerError(f"Stream timed out after {self.timeout}s", ReasonerErrorCode.TIMEOUT, e)
        except httpx.HTTPStatusError as e:
            _logger.error("anthropic_stream_error", status_code=e.response.status_code, text=e.response.text, endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code=str(e.response.status_code)).inc()
            raise ReasonerError(f"Anthropic API stream error: {e.response.text}", ReasonerErrorCode.MODEL_INFERENCE_FAILED, e)
        except Exception as e:
            _logger.critical("anthropic_stream_unexpected_error", exc_info=True, error=str(e), endpoint=endpoint)
            INFERENCE_ERRORS.labels(adapter=self.__class__.__name__, endpoint=endpoint, code='unexpected').inc()
            raise ReasonerError(f"An unexpected error occurred during streaming: {e}", code=ReasonerErrorCode.UNEXPECTED_ERROR, original_exception=e)

    async def health_check(self) -> bool:
        """
        Checks Anthropic API connectivity with a simple messages request.

        Returns:
            True if API is reachable, False otherwise.
        Raises:
            ReasonerError: On unexpected errors during the check.
        """
        endpoint = "health" # Hypothetical endpoint, as Anthropic doesn't have a public health check. A lightweight request could be used instead.
        _logger.debug("anthropic_health_check_start", model=self.model_name, endpoint=endpoint)
        try:
            client = await self._get_client()
            # As a substitute for a true health endpoint, we can make a lightweight request that checks for a valid response
            response = await client.get(f"{self.base_url}/messages", headers=self._client_headers)
            # A 4xx or 5xx response here would indicate an issue.
            response.raise_for_status()
            _logger.info("anthropic_health_check_success")
            return True
        except httpx.HTTPError as e:
            _logger.error("anthropic_health_check_failed", error=str(e))
            HEALTH_CHECK_ERRORS.labels(adapter=self.__class__.__name__).inc()
            return False
        except Exception as e:
            _logger.critical("anthropic_health_check_unexpected", exc_info=True, error=str(e))
            raise ReasonerError(f"Health check failed: {str(e)}", ReasonerErrorCode.SERVICE_UNAVAILABLE, e)


class LLMAdapterFactory:
    """A factory for creating LLMAdapter instances based on configuration, with caching."""
    _adapters: Dict[str, Type[LLMAdapter]] = {}
    _default_base_urls = {
        'openai': 'https://api.openai.com/v1',
        'gemini': 'https://generativelanguage.googleapis.com/v1beta/models',
        'anthropic': 'https://api.anthropic.com/v1'
    }

    class AdapterConfig(BaseModel):
        model_name: str = Field(..., description="Model name (e.g., 'gpt-4').")
        api_key: Optional[str] = Field(None, description="API key.")
        base_url: Optional[HttpUrl] = Field(None, description="Base URL.")
        adapter_type: str = Field(..., description="Adapter type (openai/gemini/anthropic).")

    @classmethod
    def register_adapter(cls, name: str, adapter_class: Type[LLMAdapter]):
        """
        Registers a new adapter class with the factory.

        Args:
            name: Internal name (e.g., 'openai').
            adapter_class: Concrete LLMAdapter subclass.
        Raises:
            TypeError: If adapter_class is not a subclass of LLMAdapter.
        """
        if not issubclass(adapter_class, LLMAdapter):
            raise TypeError(f"{adapter_class.__name__} must inherit from LLMAdapter")
        cls._adapters[name.lower()] = adapter_class
        _logger.info("adapter_registered", name=name, class_name=adapter_class.__name__)

    @classmethod
    @lru_cache(maxsize=32)
    def get_adapter(cls, model_config_json: str) -> LLMAdapter:
        """
        Retrieves an initialized adapter instance for a given model configuration.

        Args:
            model_config_json: JSON string with model configuration.
        Returns:
            Initialized LLMAdapter instance.
        Raises:
            ReasonerError: If configuration is invalid or API key is missing.
            ValueError: If adapter type is unknown.
        """
        # Parse the JSON string back to dictionary
        model_config = json.loads(model_config_json)
        
        try:
            config = cls.AdapterConfig.model_validate(model_config)
        except ValidationError as e:
            _logger.error("config_validation_failed", errors=str(e), config=model_config)
            raise ReasonerError(f"Invalid model configuration: {str(e)}", ReasonerErrorCode.CONFIGURATION_ERROR, e)

        adapter_name = config.adapter_type.lower()
        if adapter_name not in cls._adapters:
            _logger.error("unknown_adapter_type", adapter_type=adapter_name, available=list(cls._adapters.keys()))
            raise ValueError(f"Unknown adapter type: {adapter_name}. Available: {list(cls._adapters.keys())}")

        key_env_var = f'REASONER_{adapter_name.upper()}_KEY'
        api_key_value = config.api_key or os.getenv(key_env_var)
        if not api_key_value:
            _logger.error("api_key_missing", adapter=adapter_name, env_var=key_env_var)
            raise ReasonerError(f"API key missing for {adapter_name} in config or env var {key_env_var}", ReasonerErrorCode.CONFIGURATION_ERROR)

        base_url = str(config.base_url or cls._default_base_urls[adapter_name])
        _logger.info("adapter_created", adapter=adapter_name, model=config.model_name, base_url=base_url)

        return cls._adapters[adapter_name](
            model_name=config.model_name,
            api_key=SensitiveValue(api_key_value),
            base_url=base_url
        )


# --- Register Adapters on Module Load ---
LLMAdapterFactory.register_adapter("openai", OpenAIGPTAdapter)
LLMAdapterFactory.register_adapter("gemini", GeminiAPIAdapter)
LLMAdapterFactory.register_adapter("anthropic", AnthropicAdapter)