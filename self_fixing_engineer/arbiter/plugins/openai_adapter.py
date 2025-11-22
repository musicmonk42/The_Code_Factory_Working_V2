import logging
import asyncio
import re
import time
from typing import Any, Dict, Optional

# Import custom exceptions and LLMClient from the shared client module
from .llm_client import LLMClient, LLMClientError, AuthError, RateLimitError, TimeoutError, APIError
import openai  # Import the underlying SDK for specific exception types

# Import Prometheus metrics
from prometheus_client import Counter, Histogram, Gauge

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
    logger.addHandler(handler)


# Custom exceptions for adapter's public interface
class AuthError(Exception):
    """Custom exception for authentication errors specific to OpenAIAdapter."""
    pass


class TimeoutError(Exception):
    """Custom exception for timeout errors specific to OpenAIAdapter."""
    pass


class RateLimitError(Exception):
    """Custom exception for rate limit errors specific to OpenAIAdapter."""
    pass


class APIError(Exception):
    """Custom exception for general API errors specific to OpenAIAdapter."""
    pass


class OpenAIAdapter:
    """
    Adapter for OpenAI LLM integration.
    This class provides a robust and observable interface for interacting with OpenAI's API,
    handling various error conditions and leveraging the shared LLMClient's retry mechanisms.
    """

    def __init__(self, settings: Dict[str, Any]):
        """
        Initializes the OpenAIAdapter.

        Args:
            settings (Dict[str, Any]): A dictionary containing configuration for the OpenAI client.
                                       Expected keys: "OPENAI_API_KEY", "LLM_MODEL".

        Raises:
            ValueError: If the "OPENAI_API_KEY" is missing from settings.
        """
        self.logger = logger
        api_key = settings.get("OPENAI_API_KEY")
        if not api_key:
            self.logger.critical("OPENAI_API_KEY is missing from settings. Cannot initialize OpenAIAdapter.")
            raise ValueError("Missing API key for OpenAI provider.")

        self.client = LLMClient(
            provider="openai",
            api_key=api_key,
            model=settings.get("LLM_MODEL", "gpt-4o-mini"),
            timeout=settings.get("LLM_API_TIMEOUT_SECONDS", 60),
            retry_attempts=settings.get("LLM_API_RETRY_ATTEMPTS", 3),
            retry_backoff_factor=settings.get("LLM_API_RETRY_BACKOFF_FACTOR", 2.0)
        )
        self.logger.info("OpenAIAdapter initialized.")

        # Circuit Breaker attributes
        self._circuit_breaker_state = "closed"
        self._circuit_breaker_failures = 0
        self._circuit_breaker_last_failure_time = 0.0
        self._circuit_breaker_threshold = settings.get("CIRCUIT_BREAKER_THRESHOLD", 3)
        self._circuit_breaker_timeout = settings.get("CIRCUIT_BREAKER_TIMEOUT_SECONDS", 30)

        # Security configuration for PII masking
        self.security_config = settings.get("security_config", {})

        # Prometheus Metrics
        self.requests_total = Counter(
            'openai_requests_total',
            'Total OpenAI requests',
            ['status', 'correlation_id']
        )
        self.processing_latency_seconds = Histogram(
            'openai_processing_latency_seconds',
            'OpenAI processing latency in seconds',
            ['correlation_id'],
            buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, float('inf'))
        )
        self.circuit_breaker_state_gauge = Gauge('openai_circuit_breaker_state', 'Circuit breaker state (0=closed, 1=half-open, 2=open)')
        self.circuit_breaker_state_gauge.set(0)

    def _check_circuit_breaker(self):
        """
        Checks the current state of the circuit breaker.
        Raises an APIError if the circuit is open.
        """
        if self._circuit_breaker_state == "open":
            if time.monotonic() - self._circuit_breaker_last_failure_time > self._circuit_breaker_timeout:
                self._circuit_breaker_state = "half-open"
                self.circuit_breaker_state_gauge.set(1)
                self.logger.warning("Circuit breaker is now 'half-open'.")
            else:
                raise APIError("Circuit breaker is open. Not attempting OpenAI API call.")

    def _update_circuit_breaker(self, success: bool):
        """
        Updates the circuit breaker state based on the outcome of the API call.
        """
        if success:
            if self._circuit_breaker_state in ["open", "half-open"]:
                self.logger.info("Circuit breaker is now 'closed' after a successful request.")
            self._circuit_breaker_failures = 0
            self._circuit_breaker_state = "closed"
            self.circuit_breaker_state_gauge.set(0)
        else:
            self._circuit_breaker_failures += 1
            if self._circuit_breaker_state == "half-open":
                self._circuit_breaker_state = "open"
                self.circuit_breaker_state_gauge.set(2)
                self._circuit_breaker_last_failure_time = time.monotonic()
                self.logger.error("Circuit breaker failed in 'half-open' state and is now 'open'.")
            elif self._circuit_breaker_failures >= self._circuit_breaker_threshold:
                self._circuit_breaker_state = "open"
                self.circuit_breaker_state_gauge.set(2)
                self._circuit_breaker_last_failure_time = time.monotonic()
                self.logger.error(f"Circuit breaker is now 'open' after {self._circuit_breaker_failures} failures.")

    async def __aenter__(self):
        """
        Initializes resources for the async context.
        """
        self.logger.info("Entering OpenAIAdapter async context.")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Performs resource cleanup when exiting the async context.
        """
        self.logger.info("Exiting OpenAIAdapter async context.")
        await self.client.aclose_session()
        if exc_val:
            self.logger.error(f"OpenAIAdapter exited with an exception: {exc_val}", exc_info=True)
        self.logger.info("OpenAIAdapter cleanup complete.")

    async def health_check(self) -> bool:
        """
        Performs a health check of the OpenAI API.
        Returns:
            bool: True if the API is available, False otherwise.
        """
        try:
            await self.client.ping()
            self.logger.info("OpenAI API health check passed.")
            return True
        except Exception as e:
            self.logger.error(f"OpenAI API health check failed: {e}", exc_info=True)
            return False

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        correlation_id: Optional[str] = None
    ) -> str:
        """
        Generates text using the OpenAI LLM.

        Args:
            prompt (str): The input prompt for text generation.
            max_tokens (int): The maximum number of tokens to generate.
            temperature (float): The sampling temperature to use.
            correlation_id (Optional[str]): An optional ID for tracing/logging purposes.

        Returns:
            str: The generated text.

        Raises:
            AuthError: If there's an authentication issue with the OpenAI API.
            RateLimitError: If the OpenAI API rate limit is exceeded.
            TimeoutError: If the API call times out.
            APIError: For other general OpenAI API errors or unexpected exceptions.
        """
        # Check circuit breaker to prevent cascading failures
        self._check_circuit_breaker()
        start_time = time.monotonic()

        # Mask PII in prompt before processing
        masked_prompt = prompt
        if self.security_config.get("mask_pii_in_logs", False):
            for pattern in self.security_config.get("pii_patterns", {}).values():
                masked_prompt = re.sub(pattern, '[PII_MASKED]', masked_prompt)
            self.logger.debug("PII masking applied to prompt.")

        try:
            # Call LLMClient to generate text with retries
            response_text = await self.client.generate_text(
                masked_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                correlation_id=correlation_id
            )
            self._update_circuit_breaker(success=True)
            self.requests_total.labels(status='success', correlation_id=correlation_id or 'none').inc()
            self.processing_latency_seconds.labels(correlation_id=correlation_id or 'none').observe(time.monotonic() - start_time)
            self.logger.info(f"OpenAI generation successful for correlation_id: {correlation_id}")
            return response_text

        except LLMClientError as e:
            self._update_circuit_breaker(success=False)
            self.requests_total.labels(status='failure', correlation_id=correlation_id or 'none').inc()
            self.processing_latency_seconds.labels(correlation_id=correlation_id or 'none').observe(time.monotonic() - start_time)

            original_exception = e.__cause__ if e.__cause__ else e
            # Handle timeout, authentication, and API errors
            if isinstance(original_exception, (openai.APITimeoutError, asyncio.TimeoutError)):
                self.logger.error(f"OpenAI generation timed out: {original_exception} [Correlation ID: {correlation_id}]")
                raise TimeoutError(f"OpenAI API call timed out: {original_exception}") from original_exception
            elif isinstance(original_exception, openai.APIStatusError):
                self.logger.error(f"OpenAI API status error: {original_exception.status_code} - {original_exception.message} [Correlation ID: {correlation_id}]")
                if original_exception.status_code in [401, 403]:
                    raise AuthError(f"OpenAI authentication error: {original_exception.status_code} - {original_exception.message}") from original_exception
                elif original_exception.status_code == 429:
                    raise RateLimitError(f"OpenAI rate limit exceeded: {original_exception.message}") from original_exception
                else:
                    raise APIError(f"OpenAI API error (status {original_exception.status_code}): {original_exception.message}") from original_exception
            else:
                self.logger.error(f"Unexpected error during OpenAI generation: {original_exception} [Correlation ID: {correlation_id}]", exc_info=True)
                raise APIError(f"Unexpected OpenAI API error: {original_exception}") from original_exception
        except Exception as e:
            self._update_circuit_breaker(success=False)
            self.requests_total.labels(status='failure', correlation_id=correlation_id or 'none').inc()
            self.processing_latency_seconds.labels(correlation_id=correlation_id or 'none').observe(time.monotonic() - start_time)
            self.logger.critical(f"A critical, unhandled error occurred in OpenAIAdapter: {e} [Correlation ID: {correlation_id}]", exc_info=True)
            raise APIError(f"Critical unhandled error in OpenAIAdapter: {e}") from e