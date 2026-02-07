# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# D:\SFE\self_fixing_engineer\arbiter\plugins\anthropic_adapter.py
import asyncio
import hashlib
import logging
import re
import time
from typing import Any, Dict, Optional

import anthropic  # Import the underlying SDK for specific exception types
from prometheus_client import Counter, Histogram
from tenacity import RetryError  # Import RetryError to catch it specifically

# Import custom exceptions and LLMClient from the shared client module
from .llm_client import (
    APIError,
    AuthError,
    CircuitBreakerOpenError,
    LLMClient,
    LLMClientError,
    RateLimitError,
    TimeoutError,
    get_or_create_metric,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    )
    logger.addHandler(handler)

# 1. Add Prometheus Metrics
# Define metrics to be consistent with llm_client.py and multi_modal_plugin.py
LLM_PROVIDER_NAME = "anthropic"
anthropic_call_latency_seconds = get_or_create_metric(
    Histogram,
    "anthropic_call_latency_seconds",
    "Latency of Anthropic API calls in seconds.",
    ["provider", "model", "correlation_id"],
)
anthropic_call_success_total = get_or_create_metric(
    Counter,
    "anthropic_call_success_total",
    "Total number of successful Anthropic API calls.",
    ["provider", "model", "correlation_id"],
)
anthropic_call_errors_total = get_or_create_metric(
    Counter,
    "anthropic_call_errors_total",
    "Total number of failed Anthropic API calls.",
    ["provider", "model", "correlation_id", "error_type"],
)


class AnthropicAdapter:
    """
    Adapter for Anthropic LLM integration.

    This class provides a robust and observable interface for interacting with Anthropic's API,
    handling various error conditions and leveraging the shared LLMClient's retry mechanisms.
    It includes features for production readiness such as metrics, circuit breaking, input validation,
    and PII masking.
    """

    def __init__(self, settings: Dict[str, Any]):
        """
        Initializes the AnthropicAdapter.

        Args:
            settings (Dict[str, Any]): A dictionary containing configuration for the Anthropic client.
                                       Expected keys: "ANTHROPIC_API_KEY", "LLM_MODEL",
                                       "LLM_API_TIMEOUT_SECONDS", "LLM_API_RETRY_ATTEMPTS",
                                       "LLM_API_RETRY_BACKOFF_FACTOR", "CIRCUIT_BREAKER_THRESHOLD",
                                       "CIRCUIT_BREAKER_TIMEOUT_SECONDS".

        Raises:
            ValueError: If the "ANTHROPIC_API_KEY" is missing or if LLMClient initialization fails.
        """
        self.logger = logger
        api_key = settings.get("ANTHROPIC_API_KEY")
        if not api_key:
            self.logger.critical(
                "ANTHROPIC_API_KEY is missing from settings. Cannot initialize AnthropicAdapter."
            )
            raise ValueError("Missing API key for Anthropic provider.")

        # 9. Remove Hardcoded Defaults
        # Make all defaults configurable via the settings dictionary.
        self.client = LLMClient(
            provider=LLM_PROVIDER_NAME,
            api_key=api_key,
            model=settings.get("LLM_MODEL", "claude-3-sonnet-20240229"),
            timeout=settings.get("LLM_API_TIMEOUT_SECONDS", 60),
            retry_attempts=settings.get("LLM_API_RETRY_ATTEMPTS", 3),
            retry_backoff_factor=settings.get("LLM_API_RETRY_BACKOFF_FACTOR", 2.0),
        )

        # 3. Validate LLMClient Dependency
        # Ensure the client was properly initialized.
        if not self.client:
            raise ValueError("Failed to initialize LLMClient.")

        # 8. Add Circuit Breaker
        self.circuit_breaker_state = "closed"
        self.circuit_breaker_failures = 0
        self.circuit_breaker_last_failure_time = 0.0
        self.circuit_breaker_threshold = settings.get("CIRCUIT_BREAKER_THRESHOLD", 5)
        self.circuit_breaker_timeout = settings.get(
            "CIRCUIT_BREAKER_TIMEOUT_SECONDS", 300
        )

        self.logger.info("AnthropicAdapter initialized.")

    # 5. Add Async Context Management
    async def __aenter__(self):
        """
        Enters the async context.
        """
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """
        Exits the async context, ensuring the client session is closed.
        """
        if hasattr(self.client, "aclose_session"):
            try:
                await self.client.aclose_session()
            except Exception as e:
                self.logger.error(
                    f"Failed to close Anthropic client session: {e}", exc_info=True
                )

    def _update_circuit_breaker(self, success: bool):
        """
        Updates the circuit breaker state based on call success or failure.
        """
        if success:
            self.circuit_breaker_failures = 0
            if self.circuit_breaker_state != "closed":
                self.logger.info(
                    "Circuit breaker reset to 'closed' after successful call."
                )
                self.circuit_breaker_state = "closed"
        else:
            self.circuit_breaker_failures += 1
            self.circuit_breaker_last_failure_time = time.time()
            if self.circuit_breaker_failures >= self.circuit_breaker_threshold:
                self.circuit_breaker_state = "open"
                self.logger.warning(
                    f"Circuit breaker opened due to {self.circuit_breaker_failures} failures."
                )

    # 7. Add Security Risks
    def _sanitize_prompt(self, prompt: str) -> str:
        """
        Removes control characters and masks PII from the prompt for logging.

        This method ensures sensitive information is not logged and that control characters
        don't disrupt log parsing.
        """
        # Remove control characters
        sanitized_prompt = re.sub(r"[\x00-\x1F\x7F-\x9F]", "", prompt)

        # Mask common PII patterns
        # Email addresses
        sanitized_prompt = re.sub(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
            "[EMAIL]",
            sanitized_prompt,
        )
        # Phone numbers (common formats)
        sanitized_prompt = re.sub(
            r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", "[PHONE]", sanitized_prompt
        )
        # SSNs (simplified)
        sanitized_prompt = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[SSN]", sanitized_prompt)

        return sanitized_prompt

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        correlation_id: Optional[str] = None,
    ) -> str:
        """
        Generates text using the Anthropic LLM.

        This method handles API calls, error handling, retry exhaustion, and logs metrics.
        It also implements a circuit breaker to prevent cascading failures.

        Args:
            prompt (str): The input prompt for text generation. Max 100,000 characters.
            max_tokens (int): The maximum number of tokens to generate. Must be between 1 and 4096.
            temperature (float): The sampling temperature to use. Must be between 0.0 and 1.0.
            correlation_id (Optional[str]): An optional ID for tracing/logging purposes.

        Returns:
            str: The generated text.

        Raises:
            ValueError: For invalid input parameters.
            CircuitBreakerOpenError: If the circuit breaker is open.
            AuthError: If there's an authentication issue with the Anthropic API.
            RateLimitError: If the Anthropic API rate limit is exceeded.
            TimeoutError: If the API call times out.
            APIError: For other general Anthropic API errors or unexpected exceptions.
        """
        # 4. Add Input Validation
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("Prompt must be a non-empty string.")
        if len(prompt) > 100000:
            raise ValueError("Prompt exceeds the maximum length of 100,000 characters.")
        if not (1 <= max_tokens <= 4096):
            raise ValueError(
                f"max_tokens must be between 1 and 4096, but was {max_tokens}."
            )
        if not (0.0 <= temperature <= 1.0):
            raise ValueError(
                f"temperature must be between 0.0 and 1.0, but was {temperature}."
            )

        # 8. Add Circuit Breaker
        # Check circuit breaker state before making the call
        if self.circuit_breaker_state == "open":
            if (
                time.time() - self.circuit_breaker_last_failure_time
                > self.circuit_breaker_timeout
            ):
                self.logger.info(
                    "Circuit breaker entering 'half-open' state to test for recovery."
                )
                self.circuit_breaker_state = "half-open"
            else:
                self.logger.warning(
                    f"Circuit breaker is open. Not attempting Anthropic API call. [Correlation ID: {correlation_id}]"
                )
                raise CircuitBreakerOpenError("Anthropic API circuit breaker is open.")

        # Sanitize and hash prompt for secure logging
        self._sanitize_prompt(prompt)
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        self.logger.info(
            f"Attempting Anthropic generation for prompt hash: {prompt_hash[:10]}... [Correlation ID: {correlation_id}]"
        )

        start_time = time.time()
        success = False
        error_type = "unknown"

        try:
            response_text = await self.client.generate_text(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                correlation_id=correlation_id,
            )
            success = True
            self.logger.info(
                f"Anthropic generation successful for correlation_id: {correlation_id}"
            )
            self._update_circuit_breaker(success=True)
            return response_text

        # 2. Handle RetryError Explicitly
        except RetryError as e:
            self.logger.error(
                f"Exhausted retries for Anthropic generation: {e} [Correlation ID: {correlation_id}]"
            )
            error_type = "retry_exhausted"
            self._update_circuit_breaker(success=False)
            raise APIError(
                f"Anthropic API call failed after multiple retries: {e}"
            ) from e

        except LLMClientError as e:
            # LLMClientError is a wrapper for underlying exceptions.
            # We need to unwrap it to re-raise as specific adapter exceptions.
            original_exception = e.__cause__ if e.__cause__ else e

            if isinstance(
                original_exception, (anthropic.APITimeoutError, asyncio.TimeoutError)
            ):
                self.logger.error(
                    f"Anthropic generation timed out: {original_exception} [Correlation ID: {correlation_id}]"
                )
                error_type = "timeout"
                self._update_circuit_breaker(success=False)
                raise TimeoutError(
                    f"Anthropic API call timed out: {original_exception}"
                ) from original_exception
            elif isinstance(original_exception, anthropic.APIStatusError) or (
                hasattr(original_exception, "status_code")
            ):
                self.logger.error(
                    f"Anthropic API status error: {original_exception.status_code} - {original_exception.message} [Correlation ID: {correlation_id}]"
                )
                if original_exception.status_code in [401, 403]:
                    error_type = "authentication"
                    self._update_circuit_breaker(success=False)
                    raise AuthError(
                        f"Anthropic authentication error: {original_exception.status_code} - {original_exception.message}"
                    ) from original_exception
                elif original_exception.status_code == 429:
                    error_type = "rate_limit"
                    self._update_circuit_breaker(success=False)
                    raise RateLimitError(
                        f"Anthropic rate limit exceeded: {original_exception.message}"
                    ) from original_exception
                else:
                    error_type = "api_status_error"
                    self._update_circuit_breaker(success=False)
                    raise APIError(
                        f"Anthropic API error (status {original_exception.status_code}): {original_exception.message}"
                    ) from original_exception
            else:
                self.logger.error(
                    f"Unexpected error during Anthropic generation: {original_exception} [Correlation ID: {correlation_id}]",
                    exc_info=True,
                )
                error_type = "unexpected_llm_client_error"
                self._update_circuit_breaker(success=False)
                raise APIError(
                    f"Unexpected Anthropic API error: {original_exception}"
                ) from original_exception

        # 3. Validate LLMClient Dependency
        # Generic catch-all for any other unhandled exceptions
        except Exception as e:
            self.logger.critical(
                f"A critical, unhandled error occurred in AnthropicAdapter: {e} [Correlation ID: {correlation_id}]",
                exc_info=True,
            )
            error_type = "critical_unhandled"
            self._update_circuit_breaker(success=False)
            raise APIError(f"Critical unhandled error in AnthropicAdapter: {e}") from e

        finally:
            # 1. Add Prometheus Metrics
            # Record metrics regardless of success or failure
            latency = time.time() - start_time
            model_name = self.client.model

            anthropic_call_latency_seconds.labels(
                provider=LLM_PROVIDER_NAME,
                model=model_name,
                correlation_id=correlation_id,
            ).observe(latency)

            if success:
                anthropic_call_success_total.labels(
                    provider=LLM_PROVIDER_NAME,
                    model=model_name,
                    correlation_id=correlation_id,
                ).inc()
            else:
                anthropic_call_errors_total.labels(
                    provider=LLM_PROVIDER_NAME,
                    model=model_name,
                    correlation_id=correlation_id,
                    error_type=error_type,
                ).inc()

            # Circuit breaker is updated in exception handlers, not here
            # to avoid double-counting failures
