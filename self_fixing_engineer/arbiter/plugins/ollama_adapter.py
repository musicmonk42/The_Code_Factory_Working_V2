import logging
import asyncio
import time
import re
from typing import Any, Dict, Optional

# Import custom exceptions and LLMClient from the shared client module
from .llm_client import LLMClient, LLMClientError, TimeoutError, APIError
import aiohttp # Import aiohttp for specific exception handling
from prometheus_client import Counter, Histogram, Gauge

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
    logger.addHandler(handler)

# Custom exceptions for adapter's public interface
# AuthError and RateLimitError are less common for a local Ollama server,
# but are included for API consistency with other adapters.
class AuthError(Exception):
    """Custom exception for authentication errors specific to OllamaAdapter."""
    pass

class RateLimitError(Exception):
    """Custom exception for rate limit errors specific to OllamaAdapter."""
    pass

class OllamaAdapter:
    """
    Adapter for Ollama LLM integration (local server).
    This class provides a robust and observable interface for interacting with a local
    Ollama instance, handling various error conditions and leveraging the shared
    LLMClient's retry mechanisms.
    """
    def __init__(self, settings: Dict[str, Any]):
        """
        Initializes the OllamaAdapter.

        Args:
            settings (Dict[str, Any]): A dictionary containing configuration for the Ollama client.
                                       Expected key: "LLM_MODEL".

        Raises:
            ValueError: If the "LLM_MODEL" is missing from settings.
        """
        self.logger = logger
        model_name = settings.get("LLM_MODEL", "llama3")
        if not model_name:
            self.logger.critical("LLM_MODEL is missing from settings. Cannot initialize OllamaAdapter.")
            raise ValueError("Missing model name for Ollama provider.")
            
        self.client = LLMClient(
            provider="ollama",
            api_key=None, # No API key needed for local Ollama
            model=model_name,
            base_url=settings.get("OLLAMA_API_URL"), # Get base URL from settings
            timeout=settings.get("LLM_API_TIMEOUT_SECONDS", 60),
            retry_attempts=settings.get("LLM_API_RETRY_ATTEMPTS", 3),
            retry_backoff_factor=settings.get("LLM_API_RETRY_BACKOFF_FACTOR", 2.0)
        )
        self.logger.info("OllamaAdapter initialized.")

        # Circuit breaker attributes
        self._circuit_breaker_state = "closed"
        self._circuit_breaker_failures = 0
        self._circuit_breaker_last_failure_time = 0.0
        self._circuit_breaker_threshold = settings.get("CIRCUIT_BREAKER_THRESHOLD", 3)
        self._circuit_breaker_timeout = settings.get("CIRCUIT_BREAKER_TIMEOUT_SECONDS", 30)
        
        # Security configuration for PII masking
        self.security_config = settings.get("security_config", {})

        # Prometheus metrics
        self.requests_total = Counter(
            'ollama_requests_total',
            'Total Ollama requests',
            ['status', 'correlation_id']
        )
        self.processing_latency_seconds = Histogram(
            'ollama_processing_latency_seconds',
            'Ollama processing latency in seconds',
            ['correlation_id'],
            buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, float('inf'))
        )
        self.circuit_breaker_state_gauge = Gauge(
            'ollama_circuit_breaker_state', 
            'Circuit breaker state (0=closed, 1=half-open, 2=open)'
        )
        self.circuit_breaker_state_gauge.set(0) # 0 for "closed" initially

    async def __aenter__(self):
        """
        Asynchronous context manager entry point.
        """
        self.logger.info("Entering OllamaAdapter async context.")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Asynchronous context manager exit point.
        Closes the underlying client session.
        """
        self.logger.info("Exiting OllamaAdapter async context.")
        await self.client.aclose_session()
        if exc_val:
            self.logger.error(f"OllamaAdapter exited with an exception: {exc_val}", exc_info=True)
        self.logger.info("OllamaAdapter cleanup complete.")

    def _check_circuit_breaker(self):
        """
        Checks the state of the circuit breaker before a request.
        Raises LLMClientError if the circuit is open and the timeout has not been reached.
        """
        if self._circuit_breaker_state == "open":
            if time.monotonic() - self._circuit_breaker_last_failure_time > self._circuit_breaker_timeout:
                self._circuit_breaker_state = "half-open"
                self.circuit_breaker_state_gauge.set(1) # 1 for "half-open"
                self.logger.warning("Circuit breaker is now 'half-open'.")
            else:
                raise LLMClientError("Circuit breaker is open. Not attempting Ollama API call.")

    def _update_circuit_breaker(self, success: bool):
        """
        Updates the circuit breaker state based on the success or failure of a request.
        """
        if success:
            if self._circuit_breaker_state in ["open", "half-open"]:
                self.logger.info("Circuit breaker is now 'closed' after a successful request.")
                self.circuit_breaker_state_gauge.set(0) # 0 for "closed"
            self._circuit_breaker_failures = 0
            self._circuit_breaker_state = "closed"
        else:
            self._circuit_breaker_failures += 1
            if self._circuit_breaker_state == "half-open":
                self._circuit_breaker_state = "open"
                self._circuit_breaker_last_failure_time = time.monotonic()
                self.circuit_breaker_state_gauge.set(2) # 2 for "open"
                self.logger.error(f"Circuit breaker failed in 'half-open' state and is now 'open'.")
            elif self._circuit_breaker_failures >= self._circuit_breaker_threshold:
                self._circuit_breaker_state = "open"
                self._circuit_breaker_last_failure_time = time.monotonic()
                self.circuit_breaker_state_gauge.set(2) # 2 for "open"
                self.logger.error(f"Circuit breaker is now 'open' after {self._circuit_breaker_failures} failures.")

    async def health_check(self) -> bool:
        """
        Performs a health check of the Ollama server.
        Returns:
            bool: True if the server is available, False otherwise.
        """
        try:
            # Assuming LLMClient has a health check or ping method
            await self.client.ping()
            self.logger.info("Ollama server health check passed.")
            return True
        except Exception as e:
            self.logger.error(f"Ollama server health check failed: {e}", exc_info=True)
            return False

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        correlation_id: Optional[str] = None
    ) -> str:
        """
        Generates text using the Ollama LLM.

        Args:
            prompt (str): The input prompt for text generation.
            max_tokens (int): The maximum number of tokens to generate.
            temperature (float): The sampling temperature to use.
            correlation_id (Optional[str]): An optional ID for tracing/logging purposes.

        Returns:
            str: The generated text.

        Raises:
            TimeoutError: If the API call times out.
            APIError: For other general Ollama API errors or unexpected exceptions.
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
            
            # Record success metrics
            self.requests_total.labels(status='success', correlation_id=correlation_id or 'none').inc()
            self.processing_latency_seconds.labels(correlation_id=correlation_id or 'none').observe((time.monotonic() - start_time))
            self._update_circuit_breaker(success=True)
            self.logger.info(f"Ollama generation successful for correlation_id: {correlation_id}")
            return response_text

        except LLMClientError as e:
            # Record failure metrics
            self.requests_total.labels(status='failure', correlation_id=correlation_id or 'none').inc()
            self.processing_latency_seconds.labels(correlation_id=correlation_id or 'none').observe((time.monotonic() - start_time))
            self._update_circuit_breaker(success=False)
            
            # Handle connection, timeout, and API errors
            original_exception = e.__cause__ if e.__cause__ else e
            
            if isinstance(original_exception, (aiohttp.ClientConnectionError, asyncio.TimeoutError)):
                self.logger.error(f"Ollama generation failed due to connection error or timeout: {original_exception} [Correlation ID: {correlation_id}]")
                raise TimeoutError(f"Ollama API call timed out or failed to connect: {original_exception}") from original_exception
            elif isinstance(original_exception, aiohttp.ClientResponseError):
                self.logger.error(f"Ollama API status error: {original_exception.status} - {original_exception.message} [Correlation ID: {correlation_id}]")
                if original_exception.status in [401, 403]:
                    # This is unlikely for a local Ollama but included for consistency
                    raise AuthError(f"Ollama authentication error: {original_exception.status} - {original_exception.message}") from original_exception
                elif original_exception.status == 429:
                    # This is unlikely for a local Ollama but included for consistency
                    raise RateLimitError(f"Ollama rate limit exceeded: {original_exception.message}") from original_exception
                else:
                    raise APIError(f"Ollama API error (status {original_exception.status}): {original_exception.message}") from original_exception
            else:
                self.logger.error(f"Unexpected error during Ollama generation: {original_exception} [Correlation ID: {correlation_id}]", exc_info=True)
                raise APIError(f"Unexpected Ollama API error: {original_exception}") from original_exception
        except Exception as e:
            # Record failure metrics for unexpected errors
            self.requests_total.labels(status='failure', correlation_id=correlation_id or 'none').inc()
            self.processing_latency_seconds.labels(correlation_id=correlation_id or 'none').observe((time.monotonic() - start_time))
            self._update_circuit_breaker(success=False)
            self.logger.critical(f"A critical, unhandled error occurred in OllamaAdapter: {e} [Correlation ID: {correlation_id}]", exc_info=True)
            raise APIError(f"Critical unhandled error in OllamaAdapter: {e}") from e