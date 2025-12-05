# D:\SFE\self_fixing_engineer\arbiter\plugins\llm_client.py
import asyncio
import atexit  # For cleanup of shared resources
import hashlib  # For prompt hashing
import itertools  # For round-robin iteration
import json
import logging
import os
import re  # For PII masking
import threading  # For thread-safe metric creation
import time
from typing import (
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    Union,
)

import aiohttp
import anthropic
import google.api_core.exceptions as google_exceptions
import google.generativeai as genai
import openai
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from opentelemetry import trace
from arbiter.otel_config import get_tracer_safe
from prometheus_client import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    Summary,
    start_http_server,
)

# Tenacity for retries
from tenacity import (
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    )
    logger.addHandler(handler)

# OpenTelemetry tracer
tracer = get_tracer_safe(__name__)


# Custom exceptions for the LLM client to provide a consistent error type.
class LLMClientError(Exception):
    """Base custom exception for LLM Client errors."""

    pass


class AuthError(LLMClientError):
    """Raised for authentication failures (e.g., invalid API key)."""

    pass


class RateLimitError(LLMClientError):
    """Raised when API rate limits are exceeded."""

    pass


class TimeoutError(LLMClientError):
    """Raised when an LLM API call times out."""

    pass


class APIError(LLMClientError):
    """Raised for general LLM API errors (e.g., bad request, server errors)."""

    pass


class InputValidationError(LLMClientError):
    """Raised for invalid or out-of-range input parameters."""

    pass


class CircuitBreakerOpenError(LLMClientError):
    """Raised when the circuit breaker is open, preventing a call."""

    pass


# Prometheus metrics utility function
_metrics_lock = threading.Lock()  # Lock for thread-safe metric creation


def get_or_create_metric(
    metric_class: Union[Type[Counter], Type[Gauge], Type[Histogram], Type[Summary]],
    name: str,
    documentation: str,
    labelnames: Tuple[str, ...] = (),
    buckets: Optional[Tuple[float, ...]] = None,
):
    """
    Creates or returns an existing Prometheus metric in a thread-safe manner,
    handling Histogram and Summary checks properly.
    """
    from prometheus_client import Histogram, Summary

    labelnames = labelnames or ()
    # For Histogram and Summary, check '_sum' sub-metric to detect existence
    if metric_class in (Histogram, Summary):
        name + "_sum"
    else:
        pass

    with _metrics_lock:
        try:
            # Try to get existing collector - avoid private attributes
            # Just attempt to create; Prometheus will handle duplicates gracefully
            pass
        except Exception as e:
            logger.error(f"Error checking/unregistering metric {name}: {e}")

        # Create the new metric. Pass buckets only if provided and applicable.
        # Prometheus client handles duplicate registrations internally
        try:
            if buckets and metric_class in (Histogram, Summary):
                return metric_class(
                    name, documentation, labelnames=labelnames, buckets=buckets
                )
            return metric_class(name, documentation, labelnames=labelnames)
        except ValueError as e:
            # Metric already exists - try to retrieve it
            if "Duplicated timeseries" in str(e) or "already registered" in str(e):
                # Return None and let caller handle, or try to get from registry
                logger.debug(f"Metric {name} already registered, reusing existing")
                # Attempt to get from collector registry (no private access)
                for collector in list(REGISTRY._collector_to_names.keys()):
                    try:
                        if hasattr(collector, "_name") and collector._name == name:
                            return collector
                    except AttributeError:
                        continue
            raise


# Prometheus metrics
LLM_CALL_LATENCY = get_or_create_metric(
    Histogram,
    "llm_call_latency_seconds",
    "Latency of LLM API calls",
    ["provider", "model", "correlation_id"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60),  # Define buckets for the Histogram
)
LLM_CALL_ERRORS = get_or_create_metric(
    Counter,
    "llm_call_errors_total",
    "Total errors in LLM API calls",
    ["provider", "model", "error_type", "correlation_id"],
)
LLM_CALL_SUCCESS = get_or_create_metric(
    Counter,
    "llm_call_success_total",
    "Total successful LLM API calls",
    ["provider", "model", "correlation_id"],
)
LLM_PROVIDER_FAILOVERS_TOTAL = get_or_create_metric(
    Counter,
    "llm_provider_failovers_total",
    "Total times LLM provider fallback occurred",
    ["failed_provider", "fallback_provider"],
)


class LLMClient:
    """Unified async client for LLM providers (OpenAI, Anthropic, Gemini, Ollama)."""

    _client_sessions: Dict[str, aiohttp.ClientSession] = {}
    _session_lock = (
        threading.Lock()
    )  # Protects _client_sessions for atexit registration

    def __init__(
        self,
        provider: str,
        api_key: Optional[str],
        model: str,
        base_url: Optional[str] = None,
        timeout: int = 60,
        retry_attempts: int = 3,
        retry_backoff_factor: float = 2.0,
    ):
        """
        Initializes the LLMClient for a specific provider.

        Args:
            provider (str): The name of the LLM provider (e.g., 'openai', 'anthropic', 'gemini', 'ollama').
            api_key (Optional[str]): The API key for the provider. Required for most commercial providers.
            model (str): The specific model to use (e.g., 'gpt-4o-mini').
            base_url (Optional[str]): The base URL for the API endpoint.
            timeout (int): The timeout for API calls in seconds.
            retry_attempts (int): The number of times to retry a failed API call.
            retry_backoff_factor (float): The backoff factor for exponential retries.
        """
        # Validate initialization parameters
        if not provider or not isinstance(provider, str):
            raise InputValidationError("Provider name must be a non-empty string.")
        if not model or not isinstance(model, str):
            raise InputValidationError("Model name must be a non-empty string.")
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise InputValidationError("Timeout must be a positive number.")
        if not isinstance(retry_attempts, int) or retry_attempts < 0:
            raise InputValidationError("Retry attempts must be a non-negative integer.")
        if (
            not isinstance(retry_backoff_factor, (int, float))
            or retry_backoff_factor < 1.0
        ):
            raise InputValidationError("Retry backoff factor must be 1.0 or greater.")

        self.provider = provider
        self.model = model
        self.client = None
        self.base_url = base_url
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_backoff_factor = retry_backoff_factor

        # Circuit Breaker attributes
        self.circuit_breaker_state = "closed"
        self.circuit_breaker_failures = 0
        self.circuit_breaker_threshold = 5
        self.circuit_breaker_timeout = 300
        self.circuit_breaker_last_failure_time = None
        self._circuit_breaker_lock = threading.Lock()

        # Set default model if not provided
        if not self.model:
            if self.provider == "openai":
                self.model = "gpt-4o-mini"
            elif self.provider == "anthropic":
                self.model = "claude-3-sonnet-20240229"
            elif self.provider == "gemini":
                self.model = "gemini-1.5-flash-latest"
            elif self.provider == "ollama":
                self.model = "llama3"
            else:
                logger.warning(
                    f"Model not specified for provider {provider} and no default is known. This may lead to errors."
                )

        # Validate API key for providers that require it
        if provider in ["openai", "anthropic", "gemini"] and not api_key:
            raise ValueError(f"Missing API key for {provider} provider.")

        # Initialize provider-specific clients
        if provider == "openai":
            self.client = AsyncOpenAI(
                api_key=api_key, base_url=base_url, timeout=self.timeout
            )
        elif provider == "anthropic":
            self.client = AsyncAnthropic(
                api_key=api_key, base_url=base_url, timeout=self.timeout
            )
        elif provider == "gemini":
            genai.configure(api_key=api_key)
            try:
                self.client = genai.GenerativeModel(self.model)
            except Exception as e:
                logger.error(
                    f"Failed to initialize Gemini GenerativeModel({self.model}): {e}. Check API key and model availability."
                )
                self.client = None
        elif provider == "ollama":
            self.base_url = base_url if base_url else "http://localhost:11434"
            # Register atexit cleanup for Ollama's aiohttp session only once
            with LLMClient._session_lock:
                if "ollama" not in LLMClient._client_sessions:
                    atexit.register(
                        lambda: asyncio.run(self._close_ollama_session_atexit())
                    )
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")

    def _update_circuit_breaker(self, success: bool):
        """Manages the state of the circuit breaker based on call success/failure."""
        with self._circuit_breaker_lock:
            if success:
                self.circuit_breaker_failures = 0
                self.circuit_breaker_state = "closed"
            else:
                self.circuit_breaker_failures += 1
                self.circuit_breaker_last_failure_time = time.monotonic()
                if self.circuit_breaker_failures >= self.circuit_breaker_threshold:
                    self.circuit_breaker_state = "open"
                    logger.warning(
                        f"Circuit breaker for {self.provider}/{self.model} is now OPEN after {self.circuit_breaker_failures} consecutive failures."
                    )

    def _check_circuit_breaker(self):
        """Checks the circuit breaker state before allowing a call."""
        with self._circuit_breaker_lock:
            if self.circuit_breaker_state == "open":
                elapsed_time = time.monotonic() - self.circuit_breaker_last_failure_time
                if elapsed_time > self.circuit_breaker_timeout:
                    self.circuit_breaker_state = "half-open"
                    logger.info(
                        f"Circuit breaker for {self.provider}/{self.model} is now HALF-OPEN. Next request will be a probe."
                    )
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker for {self.provider}/{self.model} is OPEN. Not attempting call."
                    )
            elif self.circuit_breaker_state == "half-open":
                # In half-open state, only allow one request to pass.
                # If it succeeds, close the breaker. If it fails, open it again.
                # This is handled by the `_update_circuit_breaker` call after the attempt.
                pass

    @classmethod
    async def _get_ollama_session(cls) -> aiohttp.ClientSession:
        """Manages a single aiohttp.ClientSession for Ollama connection pooling."""
        with cls._session_lock:
            if (
                "ollama" not in cls._client_sessions
                or cls._client_sessions["ollama"].closed
            ):
                timeout_obj = aiohttp.ClientTimeout(total=300)
                cls._client_sessions["ollama"] = aiohttp.ClientSession(
                    timeout=timeout_obj
                )
            return cls._client_sessions["ollama"]

    @classmethod
    async def _close_ollama_session_atexit(cls):
        """Closes the shared Ollama aiohttp session when the program exits."""
        with cls._session_lock:
            if (
                "ollama" in cls._client_sessions
                and not cls._client_sessions["ollama"].closed
            ):
                await cls._client_sessions["ollama"].close()
                logger.info("Ollama aiohttp client session closed via atexit.")
                del cls._client_sessions["ollama"]

    async def aclose_session(self):
        """
        Closes provider-specific client sessions if applicable.

        This method is designed to be called explicitly to clean up resources,
        especially for providers that maintain persistent connections.
        """
        if self.provider == "openai" and self.client:
            await self.client.close()
            logger.info(f"OpenAI client session for model {self.model} closed.")
        elif self.provider == "anthropic" and self.client:
            await self.client.aclose()
            logger.info(f"Anthropic client session for model {self.model} closed.")
        elif self.provider == "ollama":
            # Ollama session is class-level and managed by atexit, so no action needed here for instance
            pass
        elif self.provider == "gemini":
            # Gemini client (genai.GenerativeModel) does not have a close method
            pass

    async def _handle_llm_call(
        self,
        coro_producer: Callable[[], Awaitable],
        prompt: str,
        is_streaming: bool,
        correlation_id: Optional[str] = None,
    ) -> Union[str, AsyncGenerator[str, None]]:
        """
        Internal handler with retry logic for individual LLM API calls.
        Receives a callable (coroutine producer) that generates a fresh coroutine for each attempt.
        """

        # Check circuit breaker before attempting the call
        self._check_circuit_breaker()

        # Define transient errors that are typically retryable
        transient_errors = (
            openai.APIConnectionError,
            anthropic.APITimeoutError,
            asyncio.TimeoutError,
            aiohttp.ClientConnectionError,
            aiohttp.ServerTimeoutError,
        )

        # Define predicates for specific SDK transient errors
        def is_anthropic_transient(e):
            return isinstance(e, anthropic.APIStatusError) and (
                e.status_code == 429 or e.status_code >= 500
            )

        def is_google_transient(e):
            return isinstance(e, google_exceptions.GoogleAPICallError) and (
                e.code == 429 or e.code >= 500
            )

        def is_aiohttp_transient(e):
            return isinstance(e, aiohttp.ClientResponseError) and (
                e.status == 429 or e.status >= 500
            )

        @retry(
            stop=stop_after_attempt(self.retry_attempts),
            wait=wait_exponential(multiplier=self.retry_backoff_factor, min=1, max=10),
            retry=retry_if_exception_type(transient_errors)
            | retry_if_exception_type(
                openai.RateLimitError
            )  # OpenAI specific rate limit error
            | retry_if_exception(
                is_anthropic_transient
            )  # Anthropic specific transient errors
            | retry_if_exception(
                is_google_transient
            )  # Google specific transient errors
            | retry_if_exception(
                is_aiohttp_transient
            ),  # aiohttp specific transient errors
        )
        async def _call_with_retry():
            with tracer.start_as_current_span(f"llm_api_call_{self.provider}") as span:
                span.set_attribute("provider", self.provider)
                span.set_attribute("model", self.model)
                span.set_attribute(
                    "correlation_id", correlation_id if correlation_id else "none"
                )
                span.set_attribute(
                    "llm.call_type", "streaming" if is_streaming else "non_streaming"
                )

                start_time = time.monotonic()
                try:
                    result = await coro_producer()

                    self._update_circuit_breaker(success=True)
                    LLM_CALL_SUCCESS.labels(
                        provider=self.provider,
                        model=self.model,
                        correlation_id=correlation_id,
                    ).inc()
                    LLM_CALL_LATENCY.labels(
                        provider=self.provider,
                        model=self.model,
                        correlation_id=correlation_id,
                    ).observe(time.monotonic() - start_time)

                    logger.info(
                        f"LLM call {self.provider}/{self.model} successful for prompt hash: {hashlib.sha256(prompt.encode()).hexdigest()[:8]}... [{correlation_id}]"
                    )
                    return result
                except Exception as e:
                    error_type = type(e).__name__
                    status_code = getattr(
                        e, "status_code", getattr(e, "code", getattr(e, "status", None))
                    )
                    log_message = (
                        f"LLM call failed for {self.provider}/{self.model}: {e}"
                    )
                    if status_code:
                        log_message += f" (Status: {status_code})"

                    # Determine if the error is non-retryable (e.g., client-side errors, invalid arguments)
                    is_non_retryable = False
                    if (
                        isinstance(e, openai.BadRequestError)
                        or (
                            isinstance(e, anthropic.APIStatusError)
                            and status_code
                            and 400 <= status_code < 500
                            and status_code != 429
                        )
                        or (isinstance(e, google_exceptions.InvalidArgument))
                        or (
                            isinstance(e, aiohttp.ClientResponseError)
                            and status_code
                            and 400 <= status_code < 500
                            and status_code != 429
                        )
                    ):
                        is_non_retryable = True

                    if is_non_retryable:
                        self._update_circuit_breaker(success=False)
                        LLM_CALL_ERRORS.labels(
                            provider=self.provider,
                            model=self.model,
                            error_type=f"ClientError_{status_code if status_code else 'UNKNOWN'}",
                            correlation_id=correlation_id,
                        ).inc()
                        span.record_exception(e)
                        span.set_status(
                            trace.Status(
                                trace.StatusCode.ERROR,
                                f"Non-Retryable API Error: {str(e)}",
                            )
                        )
                        logger.error(
                            f"{log_message} [Non-Retryable] [{correlation_id}]",
                            exc_info=True,
                        )
                        raise  # Re-raise immediately, tenacity won't retry this
                    else:
                        LLM_CALL_ERRORS.labels(
                            provider=self.provider,
                            model=self.model,
                            error_type=error_type,
                            correlation_id=correlation_id,
                        ).inc()
                        span.record_exception(e)
                        span.set_status(
                            trace.Status(
                                trace.StatusCode.ERROR, f"Transient Error: {str(e)}"
                            )
                        )
                        logger.warning(
                            f"{log_message} [Transient, Retrying] [{correlation_id}]",
                            exc_info=True,
                        )
                        raise  # Re-raise for tenacity to handle retries

        # Attempt the call with retries
        try:
            return await _call_with_retry()
        except Exception as final_e:
            type(final_e).__name__
            status_code = getattr(
                final_e,
                "status_code",
                getattr(final_e, "code", getattr(final_e, "status", None)),
            )
            final_log_message = f"LLM call {self.provider}/{self.model} ultimately failed after retries: {final_e}"
            if status_code:
                final_log_message += f" (Status: {status_code})"

            logger.error(f"{final_log_message} [{correlation_id}]", exc_info=True)
            # Re-raise as custom LLMClientError for consistent handling by calling code
            if isinstance(final_e, openai.AuthenticationError) or (
                isinstance(final_e, anthropic.APIStatusError)
                and getattr(final_e, "status_code", None) in [401, 403]
            ):
                raise AuthError(final_log_message) from final_e
            elif isinstance(final_e, openai.RateLimitError) or (
                isinstance(final_e, anthropic.APIStatusError)
                and getattr(final_e, "status_code", None) == 429
            ):
                raise RateLimitError(final_log_message) from final_e
            elif isinstance(final_e, (openai.APITimeoutError, asyncio.TimeoutError)):
                raise TimeoutError(final_log_message) from final_e
            elif isinstance(
                final_e,
                (
                    openai.APIStatusError,
                    google_exceptions.GoogleAPICallError,
                    aiohttp.ClientResponseError,
                ),
            ):
                raise APIError(final_log_message) from final_e
            else:
                raise LLMClientError(final_log_message) from final_e

    @property
    def _llm_type(self) -> str:
        """Returns the type of LLM model being used (e.g., "CustomLLMChatModel")."""
        return "CustomLLMChatModel"  # Placeholder, implement actual logic if needed

    def _sanitize_prompt(self, prompt: str) -> str:
        """
        Removes control characters and masks PII from a prompt string.
        """
        # Remove control characters
        sanitized_prompt = re.sub(r"[\x00-\x1F\x7F-\x9F]", "", prompt)

        # PII masking patterns
        pii_patterns = {
            "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            "phone": r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
            "ssn": r"\d{3}-\d{2}-\d{4}",
            "credit_card": r"\d{4}[-.\s]?\d{4}[-.\s]?\d{4}[-.\s]?\d{4}",
        }

        for pii_type, pattern in pii_patterns.items():
            sanitized_prompt = re.sub(
                pattern, f"[{pii_type.upper()}_MASKED]", sanitized_prompt
            )

        return sanitized_prompt

    def _generate_prompt(
        self, messages: List[Union[Dict[str, str], Any]]
    ) -> str:  # Use Any for BaseMessage types
        """
        Converts a list of messages (dicts or BaseMessage objects) into a single string prompt
        suitable for the custom LLM API (e.g., Ollama).
        """
        formatted_prompt = ""
        for message in messages:
            role = ""
            content = ""
            if isinstance(message, dict):
                role = message.get("role", "").capitalize()
                content = message.get("content", "")
            else:  # Assume it's a BaseMessage object (e.g., HumanMessage, SystemMessage)
                role = getattr(message, "type", "").capitalize()
                content = getattr(message, "content", "")

            if role:
                formatted_prompt += f"{role}: {content}\n"
            else:
                formatted_prompt += f"{content}\n"
        formatted_prompt += "AI:"
        return formatted_prompt.strip()

    async def generate_text(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        correlation_id: Optional[str] = None,
    ) -> str:
        """
        Generates text using the configured LLM provider (non-streaming).

        Args:
            prompt (str): The text prompt for the LLM.
            max_tokens (int): The maximum number of tokens to generate.
            temperature (float): The sampling temperature.
            correlation_id (Optional[str]): A unique ID for tracing the call.

        Returns:
            str: The generated text response.

        Raises:
            InputValidationError: If input parameters are invalid.
            LLMClientError: For API-specific or general client errors.
            CircuitBreakerOpenError: If the circuit breaker is open.
        """
        # Input Validation
        if not prompt or not isinstance(prompt, str) or len(prompt) > 100000:
            raise InputValidationError(
                "Prompt must be a non-empty string and under 100,000 characters."
            )
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            raise InputValidationError("max_tokens must be a positive integer.")
        if not isinstance(temperature, (int, float)) or not (0.0 <= temperature <= 2.0):
            raise InputValidationError(
                "Temperature must be between 0.0 and 2.0 (inclusive)."
            )

        # PII Masking
        sanitized_prompt = self._sanitize_prompt(prompt)
        messages_for_llm = [{"role": "user", "content": sanitized_prompt}]

        def coro_producer():
            return self._generate_core(messages_for_llm, max_tokens, temperature)

        return await self._handle_llm_call(
            coro_producer, prompt, is_streaming=False, correlation_id=correlation_id
        )

    async def _generate_core(
        self, messages: List[Dict[str, str]], max_tokens: int, temperature: float
    ) -> str:
        """Core logic for non-streaming generation, separated for _handle_llm_call."""
        result = ""
        if self.provider == "openai":
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=self.timeout,
            )
            result = response.choices[0].message.content
        elif self.provider == "anthropic":
            response = await self.client.messages.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=self.timeout,
            )
            result = response.content[0].text
        elif self.provider == "gemini":
            if not self.client:
                raise RuntimeError("Gemini client not initialized.")
            generation_config = {"temperature": temperature}
            if max_tokens:
                generation_config["max_output_tokens"] = max_tokens
            response = await self.client.generate_content(
                messages,
                generation_config=generation_config,
                request_options={"timeout": self.timeout},
            )
            result = response.text
        elif self.provider == "ollama":
            session = await self._get_ollama_session()
            ollama_payload = {
                "model": self.model,
                "prompt": self._generate_prompt(messages),
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            async with session.post(
                f"{self.base_url}/api/generate",
                json=ollama_payload,
                timeout=self.timeout,
            ) as resp:
                resp.raise_for_status()
                full_response_text = ""
                async for line in resp.content:
                    try:
                        full_response_text += json.loads(line.decode("utf-8")).get(
                            "response", ""
                        )
                    except json.JSONDecodeError as e:
                        logger.debug(f"Skipping non-JSON line in Ollama response: {e}")
                        pass
                result = full_response_text.strip()
        else:
            raise ValueError(f"Invalid provider: {self.provider}")
        return result

    async def async_stream_text(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        correlation_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Streams text using the configured LLM provider.

        Args:
            prompt (str): The text prompt for the LLM.
            max_tokens (int): The maximum number of tokens to generate.
            temperature (float): The sampling temperature.
            correlation_id (Optional[str]): A unique ID for tracing the call.

        Yields:
            str: Chunks of the generated text.

        Raises:
            InputValidationError: If input parameters are invalid.
            LLMClientError: For API-specific or general client errors.
            CircuitBreakerOpenError: If the circuit breaker is open.
        """
        # Input Validation
        if not prompt or not isinstance(prompt, str) or len(prompt) > 100000:
            raise InputValidationError(
                "Prompt must be a non-empty string and under 100,000 characters."
            )
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            raise InputValidationError("max_tokens must be a positive integer.")
        if not isinstance(temperature, (int, float)) or not (0.0 <= temperature <= 2.0):
            raise InputValidationError(
                "Temperature must be between 0.0 and 2.0 (inclusive)."
            )

        # PII Masking
        sanitized_prompt = self._sanitize_prompt(prompt)
        messages_for_llm = [{"role": "user", "content": sanitized_prompt}]

        async def _stream_coro_producer():
            # This inner async generator is what _handle_llm_call will retry
            async for chunk in self._stream_core(
                messages_for_llm, max_tokens, temperature
            ):
                yield chunk

        # _handle_llm_call expects a callable that returns an awaitable (coroutine)
        # For streaming, the awaitable is an async generator.
        return await self._handle_llm_call(
            _stream_coro_producer,
            prompt,
            is_streaming=True,
            correlation_id=correlation_id,
        )

    async def _stream_core(
        self, messages: List[Dict[str, str]], max_tokens: int, temperature: float
    ) -> AsyncGenerator[str, None]:
        """Core logic for streaming generation, separated for _handle_llm_call."""
        if self.provider == "openai":
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                timeout=self.timeout,
            )
            async for chunk in stream:
                if chunk.choices[0].delta.content is not None:
                    yield chunk.choices[0].delta.content

        elif self.provider == "anthropic":
            with self.client.messages.stream(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=self.timeout,
            ) as stream:
                async for text_chunk in stream.text_stream:
                    yield text_chunk

        elif self.provider == "gemini":
            if not self.client:
                raise RuntimeError("Gemini client not initialized.")
            generation_config = {"temperature": temperature}
            if max_tokens:
                generation_config["max_output_tokens"] = max_tokens
            try:
                stream = await self.client.generate_content(
                    messages,
                    generation_config=generation_config,
                    stream=True,
                    request_options={"timeout": self.timeout},
                )
                async for chunk in stream:
                    yield chunk.text
            except (
                google_exceptions.FailedPrecondition,
                google_exceptions.InvalidArgument,
            ) as e:
                logger.error(
                    f"Gemini streaming not supported for model {self.model} or invalid request: {e}",
                    exc_info=True,
                )
                raise RuntimeError(
                    f"Gemini streaming not supported or failed for {self.model}: {e}"
                )

        elif self.provider == "ollama":
            session = await self._get_ollama_session()
            ollama_payload = {
                "model": self.model,
                "prompt": self._generate_prompt(messages),
                "options": {"temperature": temperature, "num_predict": max_tokens},
                "stream": True,
            }
            async with session.post(
                f"{self.base_url}/api/generate",
                json=ollama_payload,
                timeout=self.timeout,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.content:
                    try:
                        json_line = json.loads(line.decode("utf-8"))
                        if "response" in json_line:
                            yield json_line["response"]
                    except json.JSONDecodeError as e:
                        logger.debug(f"Skipping non-JSON line in Ollama response: {e}")
                        pass
        else:
            raise ValueError(f"Invalid provider: {self.provider}")


class LoadBalancedLLMClient:
    """
    Intelligent LLM client that can load balance and failover between multiple providers
    based on performance, cost, and availability.
    """

    # Thresholds for quarantining a provider
    FAILURE_QUARANTINE_THRESHOLD = 5
    QUARANTINE_DURATION_SECONDS = 300
    RETRYABLE_FAILURE_PENALTY_TIME = 30

    def __init__(self, providers_config: List[Dict[str, Any]]):
        """
        Initializes the load balancer with a list of provider configurations.

        Args:
            providers_config (List[Dict[str, Any]]): A list of dictionaries, where each
                                                      dictionary contains the configuration
                                                      for a single LLM provider.
        """
        self.providers: List[LLMClient] = []
        self.active_providers: List[LLMClient] = []
        self.provider_status: Dict[str, Dict[str, Union[str, float, int]]] = {}

        for i, config in enumerate(providers_config):
            provider_name = config.get("provider")
            model = config.get("model")

            timeout = config.get("timeout", 60)
            retry_attempts = config.get("retry_attempts", 3)
            retry_backoff_factor = config.get("retry_backoff_factor", 2.0)
            weight = config.get("weight", 1.0)

            if not provider_name or not model:
                logger.error(
                    f"Invalid provider config at index {i}: 'provider' and 'model' are required. Skipping."
                )
                continue

            try:
                llm_client = LLMClient(
                    provider=provider_name,
                    api_key=config.get("api_key"),
                    model=model,
                    base_url=config.get("base_url"),
                    timeout=timeout,
                    retry_attempts=retry_attempts,
                    retry_backoff_factor=retry_backoff_factor,
                )
                self.providers.append(llm_client)

                self.provider_status[provider_name] = {
                    "status": "ok",
                    "last_error_time": None,
                    "error_count": 0,
                    "consecutive_failures": 0,
                    "last_selection_penalty_time": None,
                    "weight": weight,  # Store weight in status for dynamic re-evaluation if needed
                }
                self.active_providers.append(llm_client)
            except Exception as e:
                logger.error(
                    f"Failed to initialize LLMClient for {provider_name}/{model}: {e}. Marking as unavailable.",
                    exc_info=True,
                )
                self.provider_status[provider_name] = {
                    "status": "unavailable",
                    "last_error_time": time.monotonic(),
                    "error_count": 1,
                    "consecutive_failures": 1,
                    "initialization_error": str(e),
                    "last_selection_penalty_time": time.monotonic(),
                    "weight": weight,
                }

        if not self.providers:
            raise ValueError(
                "No LLM providers successfully initialized. Cannot create LoadBalancedLLMClient."
            )

        # Populate weighted providers list for round-robin
        self._weighted_providers_list = []
        for p_client in self.active_providers:
            weight = self.provider_status[p_client.provider].get("weight", 1.0)
            self._weighted_providers_list.extend(
                [p_client] * int(weight * 10)
            )  # Scale weights to integers for repetition

        if not self._weighted_providers_list:
            # Fallback to all initialized providers if weighted list is empty (e.g., all weights were 0)
            self._weighted_providers_list = list(self.active_providers)

        self._provider_cycle = itertools.cycle(self._weighted_providers_list)

    def _select_provider(self) -> LLMClient:
        """
        Selects an LLM provider based on weighted round-robin, considering health and backoff.

        Returns:
            LLMClient: The selected provider client.

        Raises:
            LLMClientError: If no healthy provider can be found after multiple attempts.
        """
        attempt_count = 0
        max_selection_attempts = (
            len(self.providers) * 2
        )  # Limit attempts to avoid infinite loops

        while attempt_count < max_selection_attempts:
            selected_provider = next(self._provider_cycle)
            provider_name = selected_provider.provider
            status_info = self.provider_status[provider_name]

            # Check if provider is healthy
            if status_info["status"] == "ok":
                return selected_provider

            # Check if provider is quarantined and cooldown period has passed (half-open state)
            if status_info["status"] == "unavailable":
                if status_info["last_error_time"] and (
                    time.monotonic() - status_info["last_error_time"]
                    > self.QUARANTINE_DURATION_SECONDS
                ):
                    logger.info(
                        f"Attempting to re-enable quarantined provider {provider_name}."
                    )
                    status_info["status"] = (
                        "ok"  # Transition to half-open implicitly by marking ok
                    )
                    status_info["consecutive_failures"] = 0
                    status_info["last_selection_penalty_time"] = None
                    return selected_provider
                else:
                    attempt_count += 1
                    continue  # Skip this provider, still in quarantine

            # Check if provider is degraded and penalty time has passed
            if status_info["status"] == "degraded":
                if status_info["last_selection_penalty_time"] and (
                    time.monotonic() - status_info["last_selection_penalty_time"]
                    < self.RETRYABLE_FAILURE_PENALTY_TIME
                ):
                    attempt_count += 1
                    continue  # Skip this provider, still in penalty
                else:
                    # Allow a degraded provider to be selected if its penalty time is over
                    return selected_provider

            attempt_count += 1

        logger.warning(
            "All LLM providers are currently unhealthy or in backoff. Choosing next available provider (may fail)."
        )
        # Fallback to cycling through all providers if selection logic fails to find a healthy one
        return next(itertools.cycle(self.providers))

    def _update_provider_status(
        self, provider_name: str, success: bool, is_retryable_error: bool = False
    ):
        """
        Updates the internal status of a provider based on call outcome.

        Args:
            provider_name (str): The name of the provider.
            success (bool): True if the call was successful, False otherwise.
            is_retryable_error (bool): True if the failure was a transient, retryable error.
        """
        status_info = self.provider_status.get(provider_name)
        if not status_info:
            return

        if success:
            status_info["status"] = "ok"
            status_info["error_count"] = 0
            status_info["consecutive_failures"] = 0
            status_info["last_error_time"] = None
            status_info["last_selection_penalty_time"] = None
        else:
            status_info["last_error_time"] = time.monotonic()
            status_info["error_count"] += 1
            if is_retryable_error:
                status_info["status"] = "degraded"
                status_info["last_selection_penalty_time"] = time.monotonic()
                status_info["consecutive_failures"] = (
                    0  # Reset consecutive failures for retryable errors
                )
            else:
                status_info["consecutive_failures"] += 1
                if (
                    status_info["consecutive_failures"]
                    >= self.FAILURE_QUARANTINE_THRESHOLD
                ):
                    status_info["status"] = "unavailable"
                    logger.error(
                        f"Provider {provider_name} quarantined after {status_info['consecutive_failures']} consecutive non-retryable failures."
                    )
                else:
                    status_info["status"] = "degraded"
                status_info["last_selection_penalty_time"] = time.monotonic()

    async def generate_text(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        correlation_id: Optional[str] = None,
    ) -> str:
        """
        Generates text using the load-balanced LLM providers with fallback.

        Args:
            prompt (str): The text prompt.
            max_tokens (int): The max number of tokens to generate.
            temperature (float): The sampling temperature.
            correlation_id (Optional[str]): A unique ID for tracing the call.

        Returns:
            str: The generated text response.

        Raises:
            LLMClientError: If all providers fail to generate a response.
        """
        selected_provider: Optional[LLMClient] = None
        current_attempt = 0

        # Calculate max attempts based on total retry attempts across all providers
        max_attempts = (
            sum(
                p.retry_attempts + 1
                for p in self.providers
                if self.provider_status[p.provider]["status"] != "unavailable"
            )
            or 1
        )
        if (
            max_attempts == 0 and self.providers
        ):  # If all are unavailable, still try once per provider
            max_attempts = len(self.providers)

        while current_attempt < max_attempts:
            selected_provider = self._select_provider()
            current_attempt += 1
            try:
                # Coroutine producer for non-streaming generation
                def coro_producer():
                    return selected_provider._generate_core(
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )

                response = await selected_provider._handle_llm_call(
                    coro_producer,
                    prompt,
                    is_streaming=False,
                    correlation_id=correlation_id,
                )
                self._update_provider_status(selected_provider.provider, success=True)
                return response
            except LLMClientError as e:
                # Catch custom LLMClientError types to update provider status correctly
                is_retryable_error = isinstance(
                    e, (RateLimitError, TimeoutError)
                )  # These are retryable by load balancer
                self._update_provider_status(
                    selected_provider.provider,
                    success=False,
                    is_retryable_error=is_retryable_error,
                )

                logger.warning(
                    f"Load Balancer: Provider {selected_provider.provider} failed after its internal retries (Error: {type(e).__name__}). Attempting fallback. [{correlation_id}]"
                )

                # If this was the last attempt, or no other active providers to try, re-raise
                if current_attempt >= max_attempts or not self.active_providers:
                    LLM_PROVIDER_FAILOVERS_TOTAL.labels(
                        failed_provider=selected_provider.provider,
                        fallback_provider="none",
                    ).inc()
                    raise LLMClientError(
                        f"All configured LLM providers failed to generate text after all retries and fallbacks. Last error: {e} [{correlation_id}]"
                    ) from e
                else:
                    # Log failover and continue to next iteration to try another provider
                    next_provider_candidate = (
                        self._select_provider()
                    )  # Get the next provider to log for failover metric
                    LLM_PROVIDER_FAILOVERS_TOTAL.labels(
                        failed_provider=selected_provider.provider,
                        fallback_provider=next_provider_candidate.provider,
                    ).inc()

        # This part should ideally not be reached if max_attempts logic is sound, but as a safeguard
        raise LLMClientError(
            "Load balancing logic exhausted all active providers without success (unexpected path)."
        )

    async def async_stream_text(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        correlation_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Streams text using the load-balanced LLM providers with fallback.

        Args:
            prompt (str): The text prompt.
            max_tokens (int): The max number of tokens to generate.
            temperature (float): The sampling temperature.
            correlation_id (Optional[str]): A unique ID for tracing the call.

        Yields:
            str: Chunks of the generated text.

        Raises:
            LLMClientError: If all providers fail to stream a response.
        """
        selected_provider: Optional[LLMClient] = None
        current_attempt = 0
        max_attempts = (
            sum(
                p.retry_attempts + 1
                for p in self.providers
                if self.provider_status[p.provider]["status"] != "unavailable"
            )
            or 1
        )
        if max_attempts == 0 and self.providers:
            max_attempts = len(self.providers)

        while current_attempt < max_attempts:
            selected_provider = self._select_provider()
            current_attempt += 1
            try:
                # Coroutine producer for streaming generation
                async def _stream_coro_producer():
                    async for chunk in selected_provider._stream_core(
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                        temperature=temperature,
                    ):
                        yield chunk

                # _handle_llm_call will wrap the async generator and handle retries
                stream_gen = await selected_provider._handle_llm_call(
                    _stream_coro_producer,
                    prompt,
                    is_streaming=True,
                    correlation_id=correlation_id,
                )

                # Yield chunks from the stream
                async for chunk in stream_gen:
                    yield chunk

                # If stream completes successfully, update provider status
                self._update_provider_status(selected_provider.provider, success=True)
                return  # Exit after successful streaming

            except LLMClientError as e:
                is_retryable_error = isinstance(e, (RateLimitError, TimeoutError))
                self._update_provider_status(
                    selected_provider.provider,
                    success=False,
                    is_retryable_error=is_retryable_error,
                )

                logger.warning(
                    f"Load Balancer: Streaming provider {selected_provider.provider} failed after internal retries (Error: {type(e).__name__}). Attempting fallback. [{correlation_id}]"
                )

                if current_attempt >= max_attempts or not self.active_providers:
                    LLM_PROVIDER_FAILOVERS_TOTAL.labels(
                        failed_provider=selected_provider.provider,
                        fallback_provider="none",
                    ).inc()
                    raise LLMClientError(
                        f"All configured LLM providers failed to stream text after all retries and fallbacks. Last error: {e} [{correlation_id}]"
                    ) from e
                else:
                    next_provider_candidate = self._select_provider()
                    LLM_PROVIDER_FAILOVERS_TOTAL.labels(
                        failed_provider=selected_provider.provider,
                        fallback_provider=next_provider_candidate.provider,
                    ).inc()

        raise LLMClientError(
            "Load balancing logic exhausted all active providers for streaming without success (unexpected path)."
        )

    async def close_all_sessions(self):
        """Ensures all underlying LLM client SDK sessions are properly closed."""
        for provider_client in self.providers:
            await provider_client.aclose_session()
        logger.info("All LLM client sessions closed.")


# Example Usage (for testing purposes)
async def main():
    """
    Main function to test the functionality of LLMClient and LoadBalancedLLMClient.
    This includes non-streaming, streaming, and simulated failure/fallback tests.
    """
    # Start Prometheus metrics server
    try:
        start_http_server(9090)
        logger.info("Prometheus metrics server started on port 9090.")
    except Exception as e:
        logger.error(f"Failed to start Prometheus server: {e}")

    # Example Provider Configuration
    # You MUST set environment variables like OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY
    # For Ollama, ensure it's running locally on port 11434 with 'llama3' model available.
    providers_config = [
        {
            "provider": "openai",
            "api_key": os.getenv("OPENAI_API_KEY"),
            "model": "gpt-4o-mini",
            "weight": 2.0,
            "timeout": 90,
            "retry_attempts": 1,
        },
        {
            "provider": "anthropic",
            "api_key": os.getenv("ANTHROPIC_API_KEY"),
            "model": "claude-3-haiku-20240307",
            "weight": 1.0,
            "timeout": 90,
            "retry_attempts": 1,
        },
        {
            "provider": "gemini",
            "api_key": os.getenv("GEMINI_API_KEY"),
            "model": "gemini-1.5-flash-latest",
            "weight": 1.0,
            "timeout": 120,
            "retry_attempts": 1,
        },
        {
            "provider": "ollama",
            "api_key": "ollama_key_dummy",
            "model": "llama3",
            "base_url": "http://localhost:11434",
            "weight": 0.5,
            "timeout": 180,
            "retry_attempts": 1,
        },
    ]

    # Initialize LoadBalancedLLMClient
    try:
        lb_client = LoadBalancedLLMClient(providers_config)
    except ValueError as e:
        logger.error(f"Failed to initialize LoadBalancedLLMClient: {e}. Exiting.")
        return

    print("\n--- Initial Provider Status ---")
    for provider, status in lb_client.provider_status.items():
        print(
            f"  {provider}: {status['status']} (Errors: {status['error_count']}, Consecutive: {status['consecutive_failures']})"
        )

    print("\n--- Testing Non-Streaming Generate Text ---")
    prompt_ns = "What is the capital of Canada?"
    corr_id_ns = "test_ns_1"
    try:
        response_ns = await lb_client.generate_text(
            prompt_ns, max_tokens=50, correlation_id=corr_id_ns
        )
        print(f"Non-Streaming Response ({corr_id_ns}): {response_ns}")
    except Exception as e:
        print(f"Non-Streaming failed ({corr_id_ns}): {e}")
        logger.error(f"Non-streaming test failed ({corr_id_ns}): {e}", exc_info=True)

    print("\n--- Testing Streaming Generate Text ---")
    prompt_s = "Explain quantum entanglement in simple terms."
    corr_id_s = "test_s_1"
    try:
        print(f"Streaming Response ({corr_id_s}):")
        full_stream_response_s = ""
        async for chunk in lb_client.async_stream_text(
            prompt_s, max_tokens=100, correlation_id=corr_id_s
        ):
            full_stream_response_s += chunk
            print(chunk, end="", flush=True)
        print(
            f"\nFull Streamed Response Length ({corr_id_s}): {len(full_stream_response_s)}"
        )
    except Exception as e:
        print(f"Streaming failed ({corr_id_s}): {e}")
        logger.error(f"Streaming test failed ({corr_id_s}): {e}", exc_info=True)

    print("\n--- Provider Status After Initial Calls ---")
    for provider, status in lb_client.provider_status.items():
        print(
            f"  {provider}: {status['status']} (Errors: {status['error_count']}, Consecutive: {status['consecutive_failures']})"
        )

    print("\n--- Testing Fallback Mechanism (simulated failure) ---")

    if lb_client.active_providers:
        failing_provider_client = lb_client.active_providers[0]
        original_generate_text = failing_provider_client.generate_text
        original_stream_text = failing_provider_client.async_stream_text

        async def mock_fail_generate_non_retryable(*args, **kwargs):
            raise openai.BadRequestError(
                "Simulated bad request (non-retryable)",
                response=None,
                body=None,
                status_code=400,
            )

        async def mock_fail_stream_non_retryable(*args, **kwargs):
            raise anthropic.APIStatusError(
                "Simulated streaming client error (non-retryable)",
                response=None,
                body=None,
                status_code=400,
            )

        failing_provider_client.generate_text = mock_fail_generate_non_retryable
        failing_provider_client.async_stream_text = mock_fail_stream_non_retryable

        print(
            f"\n--- First active provider ({failing_provider_client.provider}) will now simulate non-retryable failure ---"
        )
        prompt_fail_ns = "What is the largest ocean on Earth?"
        try:
            response_fail_ns = await lb_client.generate_text(
                prompt_fail_ns, max_tokens=20, correlation_id=corr_id_ns
            )
            print(f"Fallback Non-Streaming Response ({corr_id_ns}): {response_fail_ns}")
        except Exception as e:
            print(
                f"Fallback Non-Streaming ultimately failed as expected after quarantining primary ({corr_id_ns}): {e}"
            )

        print("\n--- Provider Status After Simulated Failure ---")
        for provider, status in lb_client.provider_status.items():
            print(
                f"  {provider}: {status['status']} (Errors: {status['error_count']}, Consecutive: {status['consecutive_failures']})"
            )

        print(
            f"\n--- First active provider ({failing_provider_client.provider}) will simulate non-retryable streaming failure ---"
        )
        prompt_stream_fail_s = "Describe the solar system in brief."
        try:
            print(f"Fallback Streaming Response ({corr_id_s}):")
            full_stream_response_fail_s = ""
            async for chunk in lb_client.async_stream_text(
                prompt_stream_fail_s, max_tokens=50, correlation_id=corr_id_s
            ):
                full_stream_response_fail_s += chunk
                print(chunk, end="", flush=True)
            print(
                f"\nFull Fallback Streamed Response Length ({corr_id_s}): {len(full_stream_response_fail_s)}"
            )
        except Exception as e:
            print(f"Streaming failed ({corr_id_s}): {e}")
            logger.error(f"Streaming test failed ({corr_id_s}): {e}", exc_info=True)

        failing_provider_client.generate_text = original_generate_text
        failing_provider_client.async_stream_text = original_stream_text

    print("\n--- Final Provider Status ---")
    for provider, status in lb_client.provider_status.items():
        print(
            f"  {provider}: {status['status']} (Errors: {status['error_count']}, Consecutive: {status['consecutive_failures']})"
        )

    print("\n--- Closing all client sessions ---")
    await lb_client.close_all_sessions()
    print("--- Test Run Complete ---")


if __name__ == "__main__":
    # Set logging level to DEBUG to see detailed log messages
    logger.setLevel(logging.DEBUG)
    # Ensure aiohttp client sessions are closed gracefully at application exit
    try:
        asyncio.run(main())
    except ValueError as e:
        print(f"Initial setup failed: {e}")
