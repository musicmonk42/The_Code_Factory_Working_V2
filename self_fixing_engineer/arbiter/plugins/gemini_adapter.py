# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import hashlib
import logging
import re
import time
from typing import Any, Dict, Optional

import google.api_core.exceptions as google_exceptions
from prometheus_client import Counter, Histogram
from tenacity import RetryError

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

# --- Prometheus Metrics ---
# Define metrics for API call latency, successes, and errors.
# The labels allow for filtering by provider, model, and other relevant dimensions.
# Note: get_or_create_metric ensures these are thread-safe singletons.
gemini_call_latency_seconds = get_or_create_metric(
    Histogram,
    "gemini_call_latency_seconds",
    "Latency of Gemini API calls in seconds.",
    labelnames=["provider", "model", "correlation_id"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60],
)
gemini_call_success_total = get_or_create_metric(
    Counter,
    "gemini_call_success_total",
    "Total number of successful Gemini API calls.",
    labelnames=["provider", "model", "correlation_id"],
)
gemini_call_errors_total = get_or_create_metric(
    Counter,
    "gemini_call_errors_total",
    "Total number of failed Gemini API calls, labeled by error type.",
    labelnames=["provider", "model", "correlation_id", "error_type"],
)


class GeminiAdapter:
    """
    Adapter for Google Gemini LLM integration.
    This class provides a robust and observable interface for interacting with Gemini's API,
    handling various error conditions and leveraging the shared LLMClient's retry mechanisms.
    It includes:
    - Prometheus metrics for observability.
    - Explicit handling of RetryError for transparent failure reporting.
    - Input validation to prevent API errors.
    - Asynchronous context management for resource cleanup.
    - Security features like PII masking and prompt sanitization.
    - A circuit breaker to prevent cascading failures.
    - Comprehensive error handling for various Google API exceptions.
    """

    def __init__(self, settings: Dict[str, Any]):
        """
        Initializes the GeminiAdapter.

        Args:
            settings (Dict[str, Any]): A dictionary containing configuration for the Gemini client.
                                       Expected keys:
                                       - "GEMINI_API_KEY" (str): The API key for Gemini.
                                       - "LLM_MODEL" (str, optional): The LLM model to use (default: "gemini-1.5-flash").
                                       - "LLM_API_TIMEOUT_SECONDS" (int, optional): API call timeout in seconds (default: 60).
                                       - "LLM_API_RETRY_ATTEMPTS" (int, optional): Number of retry attempts (default: 3).
                                       - "LLM_API_RETRY_BACKOFF_FACTOR" (float, optional): Backoff factor for retries (default: 2.0).
                                       - "CIRCUIT_BREAKER_THRESHOLD" (int, optional): Number of consecutive failures to open the circuit (default: 5).
                                       - "CIRCUIT_BREAKER_TIMEOUT_SECONDS" (int, optional): Time in seconds to stay open before half-open state (default: 300).
                                       - "SECURITY_CONFIG" (Dict, optional): Configuration for security, including PII rules and compliance frameworks.

        Raises:
            ValueError: If the "GEMINI_API_KEY" or "GOOGLE_API_KEY" is missing from settings or if LLMClient fails to initialize.
        """
        self.logger = logger
        api_key = settings.get("GEMINI_API_KEY") or settings.get("GOOGLE_API_KEY")
        if not api_key:
            self.logger.critical(
                "GEMINI_API_KEY or GOOGLE_API_KEY is missing from settings. Cannot initialize GeminiAdapter."
            )
            raise ValueError("Missing API key for Gemini provider.")

        try:
            self.client = LLMClient(
                provider="gemini",
                api_key=api_key,
                model=settings.get("LLM_MODEL", "gemini-1.5-flash"),
                timeout=settings.get("LLM_API_TIMEOUT_SECONDS", 60),
                retry_attempts=settings.get("LLM_API_RETRY_ATTEMPTS", 3),
                retry_backoff_factor=settings.get("LLM_API_RETRY_BACKOFF_FACTOR", 2.0),
            )
        except Exception as e:
            self.logger.critical(f"Failed to initialize LLMClient: {e}", exc_info=True)
            raise ValueError(f"Failed to initialize LLMClient: {e}") from e

        if self.client is None:
            raise ValueError(
                "LLMClient initialization failed, resulting in a None client."
            )

        self.provider = "gemini"
        self.model = self.client.model
        self.security_config = settings.get("SECURITY_CONFIG", {})

        # --- Circuit Breaker State ---
        self.circuit_breaker_state = "closed"
        self.circuit_breaker_failures = 0
        self.circuit_breaker_threshold = settings.get("CIRCUIT_BREAKER_THRESHOLD", 5)
        self.circuit_breaker_timeout = settings.get(
            "CIRCUIT_BREAKER_TIMEOUT_SECONDS", 300
        )
        self.circuit_breaker_last_failure_time = 0.0

        self.logger.info("GeminiAdapter initialized.")

    async def __aenter__(self):
        """
        Enters the async context manager.
        """
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Exits the async context manager and performs cleanup.
        """
        try:
            if hasattr(self.client, "aclose_session"):
                await self.client.aclose_session()
                self.logger.info("Gemini API session closed successfully.")
        except Exception as e:
            self.logger.error(
                f"Error during Gemini API session cleanup: {e}", exc_info=True
            )

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        correlation_id: Optional[str] = None,
    ) -> str:
        """
        Generates text using the Gemini LLM.

        Args:
            prompt (str): The input prompt for text generation. Must be a non-empty string.
            max_tokens (int): The maximum number of tokens to generate.
                              Must be between 1 and 8192 for gemini-1.5-flash.
            temperature (float): The sampling temperature to use.
                                 Must be between 0.0 and 2.0.
            correlation_id (Optional[str]): An optional ID for tracing/logging purposes.

        Returns:
            str: The generated text.

        Raises:
            ValueError: If input validation fails (e.g., empty prompt, out-of-range parameters).
            AuthError: If there's an authentication issue with the Gemini API.
            RateLimitError: If the Gemini API rate limit is exceeded.
            TimeoutError: If the API call times out.
            CircuitBreakerOpenError: If the circuit breaker is open, preventing the call.
            APIError: For other general Gemini API errors, unexpected exceptions, or retry exhaustion.
        """
        # --- Input Validation Checks ---
        if not prompt or not isinstance(prompt, str) or len(prompt) == 0:
            raise ValueError("Prompt must be a non-empty string.")
        if len(prompt) > 100000:  # Gemini's typical context limit
            raise ValueError(
                f"Prompt is too long. Max length is 100,000 characters, but got {len(prompt)}."
            )
        if not (1 <= max_tokens <= 8192):
            raise ValueError(
                f"max_tokens must be between 1 and 8192, but got {max_tokens}."
            )
        if not (0.0 <= temperature <= 2.0):
            raise ValueError(
                f"temperature must be between 0.0 and 2.0, but was {temperature}."
            )

        # --- Circuit Breaker State Check ---
        # Before making the API call, check the circuit breaker state.
        if self.circuit_breaker_state == "open":
            if (
                time.monotonic() - self.circuit_breaker_last_failure_time
                > self.circuit_breaker_timeout
            ):
                self.logger.warning(
                    f"Circuit breaker timeout reached. Transitioning to 'half-open' state. [Correlation ID: {correlation_id}]"
                )
                self.circuit_breaker_state = "half-open"
            else:
                self.logger.error(
                    f"Circuit breaker is 'open'. Blocking API call to prevent cascading failures. [Correlation ID: {correlation_id}]"
                )
                gemini_call_errors_total.labels(
                    provider=self.provider,
                    model=self.model,
                    correlation_id=correlation_id,
                    error_type="circuit_breaker",
                ).inc()
                raise CircuitBreakerOpenError(
                    "Circuit breaker is open due to repeated failures."
                )

        start_time = time.monotonic()
        error_type = "unknown"
        self._sanitize_prompt(prompt)
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

        compliance_frameworks = self.security_config.get("compliance_frameworks", [])

        try:
            # The core API call, wrapped by the LLMClient's retry logic.
            response_text = await self.client.generate_text(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                correlation_id=correlation_id,
            )

            # If the call succeeds, reset the circuit breaker and record success.
            self._update_circuit_breaker(success=True)
            gemini_call_success_total.labels(
                provider=self.provider, model=self.model, correlation_id=correlation_id
            ).inc()
            self.logger.info(
                f"Gemini generation successful. Prompt hash: {prompt_hash} [Correlation ID: {correlation_id}] "
                f"Compliance: {compliance_frameworks}"
            )
            return response_text

        except RetryError as e:
            # This handles the case where all retry attempts are exhausted.
            error_type = "retry_exhausted"
            self.logger.error(
                f"Gemini generation failed after multiple retries. Last exception: {e.__cause__} [Correlation ID: {correlation_id}]"
            )
            self._update_circuit_breaker(success=False)
            raise APIError(
                f"Gemini API call failed after multiple retries: {e.__cause__}"
            ) from e

        except LLMClientError as e:
            # This block handles exceptions from the LLMClient's internal workings.
            original_exception = e.__cause__ if e.__cause__ else e

            # Exception mapping for Google API errors.
            if isinstance(original_exception, asyncio.TimeoutError):
                error_type = "timeout"
                self.logger.error(
                    f"Gemini generation timed out: {original_exception} [Correlation ID: {correlation_id}]"
                )
                self._update_circuit_breaker(success=False)
                raise TimeoutError(
                    f"Gemini API call timed out: {original_exception}"
                ) from original_exception
            elif isinstance(original_exception, google_exceptions.GoogleAPICallError):
                # Attempt to get a status code from various places in the Google API exception.
                status_code = getattr(original_exception, "code", None)
                if status_code is None and hasattr(original_exception, "response"):
                    status_code = getattr(
                        original_exception.response, "status_code", None
                    )

                if status_code is not None:
                    if status_code in [401, 403]:
                        error_type = "auth_error"
                        self._update_circuit_breaker(success=False)
                        raise AuthError(
                            f"Gemini authentication error: {status_code} - {str(original_exception)}"
                        ) from original_exception
                    elif status_code == 429:
                        error_type = "rate_limit_exceeded"
                        self._update_circuit_breaker(success=False)
                        raise RateLimitError(
                            f"Gemini rate limit exceeded: {str(original_exception)}"
                        ) from original_exception
                    else:
                        error_type = f"api_error_{status_code}"
                        self._update_circuit_breaker(success=False)
                        raise APIError(
                            f"Gemini API error (status {status_code}): {str(original_exception)}"
                        ) from original_exception
                else:
                    # If no status code can be found, log the full error message.
                    error_type = "api_error_no_code"
                    self.logger.error(
                        f"GoogleAPICallError without a status code. Message: {original_exception} [Correlation ID: {correlation_id}]",
                        exc_info=True,
                    )
                    self._update_circuit_breaker(success=False)
                    raise APIError(
                        f"Gemini API error: {str(original_exception)}"
                    ) from original_exception
            else:
                # Catch any other unexpected LLMClient errors.
                error_type = "unexpected_llm_client_error"
                self.logger.error(
                    f"Unexpected error during Gemini generation: {original_exception} [Correlation ID: {correlation_id}]",
                    exc_info=True,
                )
                self._update_circuit_breaker(success=False)
                raise APIError(
                    f"Unexpected Gemini API error: {original_exception}"
                ) from original_exception

        except Exception as e:
            # This is a critical, top-level catch for any unhandled exceptions.
            error_type = "critical_unhandled_error"
            self.logger.critical(
                f"A critical, unhandled error occurred in GeminiAdapter: {e} [Correlation ID: {correlation_id}]",
                exc_info=True,
            )
            self._update_circuit_breaker(success=False)
            raise APIError(f"Critical unhandled error in GeminiAdapter: {e}") from e

        finally:
            # Record latency and error metrics regardless of call outcome.
            end_time = time.monotonic()
            latency = end_time - start_time
            gemini_call_latency_seconds.labels(
                provider=self.provider, model=self.model, correlation_id=correlation_id
            ).observe(latency)

            # If the call failed and an error_type was set, record the error metric.
            if error_type != "unknown":
                gemini_call_errors_total.labels(
                    provider=self.provider,
                    model=self.model,
                    correlation_id=correlation_id,
                    error_type=error_type,
                ).inc()

    def _sanitize_prompt(self, prompt: str) -> str:
        """
        Sanitizes the prompt by removing control characters and masking PII.

        This method helps prevent log injection and protects sensitive data.
        It removes non-printable ASCII characters and masks common PII patterns.
        """
        # Regex to remove control characters
        sanitized = re.sub(r"[\x00-\x1F\x7F-\x9F]", "", prompt)

        # Define PII patterns based on security configuration
        pii_patterns = {
            "EMAIL": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            "PHONE": r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
            "SSN": r"\d{3}-\d{2}-\d{4}",
            "CREDIT_CARD": r"\b(?:\d[ -]*?){13,16}\b",  # Generic credit card pattern
            "ADDRESS": r"\d{1,5}\s\w+\s(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Place|Pl|Court|Ct|Circle|Cir)\b",
        }

        # Merge with custom rules from security config
        custom_pii_rules = self.security_config.get("pii_patterns", {})
        pii_patterns.update(custom_pii_rules)

        # Apply each PII masking pattern
        for pii_type, pattern in pii_patterns.items():
            sanitized = re.sub(pattern, f"[{pii_type}]", sanitized, flags=re.IGNORECASE)

        return sanitized

    def _update_circuit_breaker(self, success: bool):
        """
        Updates the state of the circuit breaker based on the success or failure of an API call.
        """
        if success:
            if self.circuit_breaker_state == "half-open":
                # If a call succeeds in half-open state, close the circuit.
                self.logger.info(
                    "Circuit breaker is now 'closed' after a successful call in 'half-open' state."
                )
                self.circuit_breaker_state = "closed"
                self.circuit_breaker_failures = 0
            elif self.circuit_breaker_state == "closed":
                # Reset failure count on success in closed state.
                self.circuit_breaker_failures = 0
        else:  # On failure
            self.circuit_breaker_failures += 1
            self.circuit_breaker_last_failure_time = time.monotonic()
            self.logger.warning(
                f"Gemini API call failed. Consecutive failures: {self.circuit_breaker_failures}/{self.circuit_breaker_threshold}."
            )
            if self.circuit_breaker_failures >= self.circuit_breaker_threshold:
                self.circuit_breaker_state = "open"
                self.logger.error(
                    "Circuit breaker is now 'open' due to too many consecutive failures."
                )
