# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# clarifier_llm.py
"""
LLM Provider implementations for the clarifier system.

This module provides production-grade LLM provider implementations for the clarifier
to generate clarifying questions and process requirements.

Providers:
- LLMProvider: Abstract base class defining the LLM interface
- GrokLLM: xAI Grok API integration with retry logic and fallback

Security:
- API keys are never logged
- All sensitive data is redacted before logging
- Proper error handling prevents information leakage

Reliability:
- Exponential backoff retry for transient failures
- Graceful fallback when API is unavailable
- Comprehensive error categorization
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Final, List, Optional, TypedDict

import aiohttp
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Exception Hierarchy
# ============================================================================


class LLMProviderError(Exception):
    """Base exception for all LLM provider errors."""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "unknown",
        error_code: str = "LLM_ERROR",
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.error_code = error_code
        self.retryable = retryable
        self.details = details or {}


class LLMAuthenticationError(LLMProviderError):
    """Raised when API authentication fails."""

    def __init__(self, message: str, provider: str = "unknown"):
        super().__init__(
            message, provider=provider, error_code="LLM_AUTH_ERROR", retryable=False
        )


class LLMRateLimitError(LLMProviderError):
    """Raised when API rate limit is exceeded."""

    def __init__(
        self, message: str, provider: str = "unknown", retry_after: Optional[int] = None
    ):
        super().__init__(
            message,
            provider=provider,
            error_code="LLM_RATE_LIMIT",
            retryable=True,
            details={"retry_after": retry_after},
        )
        self.retry_after = retry_after


class LLMNetworkError(LLMProviderError):
    """Raised when network communication fails."""

    def __init__(self, message: str, provider: str = "unknown"):
        super().__init__(
            message, provider=provider, error_code="LLM_NETWORK_ERROR", retryable=True
        )


class LLMTimeoutError(LLMProviderError):
    """Raised when API request times out."""

    def __init__(self, message: str, provider: str = "unknown"):
        super().__init__(
            message, provider=provider, error_code="LLM_TIMEOUT", retryable=True
        )


class LLMResponseError(LLMProviderError):
    """Raised when API returns an invalid response."""

    def __init__(
        self, message: str, provider: str = "unknown", status_code: Optional[int] = None
    ):
        super().__init__(
            message,
            provider=provider,
            error_code="LLM_RESPONSE_ERROR",
            retryable=status_code in (500, 502, 503, 504) if status_code else False,
            details={"status_code": status_code},
        )
        self.status_code = status_code


# ============================================================================
# Type Definitions
# ============================================================================


class GenerationParams(TypedDict, total=False):
    """Parameters for text generation."""

    temperature: float
    max_tokens: int
    top_p: float
    frequency_penalty: float
    presence_penalty: float


@dataclass
class GenerationResult:
    """Result from text generation."""

    content: str
    model: str
    tokens_used: int = 0
    latency_ms: float = 0.0
    from_fallback: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Configuration
# ============================================================================


@dataclass(frozen=True)
class GrokConfig:
    """Configuration for Grok LLM provider."""

    # API Configuration
    api_url: str = "https://api.x.ai/v1/chat/completions"
    default_model: str = "grok-1"

    # Timeouts (seconds)
    connect_timeout: float = 10.0
    read_timeout: float = 60.0
    total_timeout: float = 120.0

    # Retry Configuration
    max_retries: int = 3
    retry_min_wait: float = 1.0
    retry_max_wait: float = 30.0

    # Default Generation Parameters
    default_temperature: float = 0.7
    default_max_tokens: int = 1024


@dataclass(frozen=True)
class FallbackQuestion:
    """A single fallback clarification question."""

    question: str
    context: str
    priority: str = "medium"


@dataclass
class FallbackConfig:
    """
    Configuration for fallback response generation.

    Allows customization of clarification questions without code changes.
    """

    questions: List[FallbackQuestion] = field(
        default_factory=lambda: [
            FallbackQuestion(
                question="What is the expected scope and scale of this requirement?",
                context="Understanding scale helps determine architecture decisions",
                priority="high",
            ),
            FallbackQuestion(
                question="Are there specific constraints or limitations to consider?",
                context="Constraints affect implementation approach and technology choices",
                priority="high",
            ),
            FallbackQuestion(
                question="What are the acceptance criteria for this requirement?",
                context="Clear criteria ensure proper validation and testing",
                priority="medium",
            ),
            FallbackQuestion(
                question="Are there any dependencies on external systems or services?",
                context="Dependencies affect timeline and integration complexity",
                priority="medium",
            ),
        ]
    )

    generic_guidance: str = (
        "To provide a comprehensive response, please clarify the following:\n\n"
        "1. **Functionality**: What specific functionality is required?\n"
        "2. **Constraints**: Are there performance, security, or compliance constraints?\n"
        "3. **Integration**: What systems need to integrate with this component?\n"
        "4. **Success Criteria**: How will success be measured?\n\n"
        "_Note: This response was generated locally as the API is currently unavailable._"
    )


# Default configurations
DEFAULT_GROK_CONFIG: Final[GrokConfig] = GrokConfig()
DEFAULT_FALLBACK_CONFIG: Final[FallbackConfig] = FallbackConfig()

# ============================================================================
# Fallback Response Constants
# ============================================================================

# Keywords to detect code generation requests
CODE_GENERATION_KEYWORDS: Final[tuple] = (
    "generate", "create", "write", "implement", "code", 
    "function", "class", "method", "program", "script",
    "def ", "class ", "import ", "function(", "const ",
    "file:", "files:", "main.py", ".py", ".js", ".java"
)

# Keywords to detect clarification requests
CLARIFICATION_KEYWORDS: Final[tuple] = (
    "ambiguit", "clarif", "unclear", "requirement", "specify"
)

# Placeholder code template for when LLM API is unavailable
FALLBACK_PYTHON_CODE: Final[str] = (
    "# TODO: API unavailable - placeholder code\n"
    "# Please configure API access or try again later\n\n"
    "def main():\n"
    "    print('Hello World')\n"
    "    pass\n\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)

FALLBACK_README: Final[str] = (
    "# Placeholder\n\n"
    "This is placeholder code generated because the API was unavailable.\n\n"
    "Please configure proper API access to generate actual code.\n"
)


# ============================================================================
# Abstract Base Class
# ============================================================================


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers.

    All LLM providers must implement the generate() method. This class
    provides a consistent interface for text generation across different
    LLM services.

    Attributes:
        api_key: API key for the LLM service (kept private)
        model: Model identifier to use
        config: Additional provider-specific configuration
    """

    def __init__(
        self, api_key: Optional[str] = None, model: str = "default", **kwargs: Any
    ) -> None:
        """
        Initialize the LLM provider.

        Args:
            api_key: API key for the LLM service (if required)
            model: Model identifier to use
            **kwargs: Additional provider-specific parameters
        """
        self._api_key = api_key
        self.model = model
        self.config = kwargs

        # Log initialization without exposing API key
        logger.info(
            "Initialized LLM provider",
            extra={
                "provider": self.__class__.__name__,
                "model": model,
                "has_api_key": bool(api_key),
            },
        )

    @property
    def api_key(self) -> Optional[str]:
        """
        Get the API key.

        Note: This returns the raw API key for internal use only.
        Never log or expose this value. For external checks, use has_api_key.

        Security: The API key is stored in memory and should be
        managed through secure credential managers in production.
        """
        return self._api_key

    @property
    def has_api_key(self) -> bool:
        """Check if API key is configured without exposing the value."""
        return bool(self._api_key)

    def __repr__(self) -> str:
        """Safe string representation that doesn't expose API key."""
        return (
            f"{self.__class__.__name__}("
            f"model={self.model!r}, "
            f"has_api_key={self.has_api_key})"
        )

    @abstractmethod
    async def generate(self, prompt: str, **kwargs: Any) -> str:
        """
        Generate text from the LLM based on a prompt.

        Args:
            prompt: The input prompt for the LLM
            **kwargs: Additional generation parameters

        Returns:
            Generated text from the LLM

        Raises:
            ValueError: If the prompt is invalid
            LLMProviderError: If the LLM service fails
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.generate() must be implemented"
        )

    @abstractmethod
    async def generate_with_metadata(
        self, prompt: str, **kwargs: Any
    ) -> GenerationResult:
        """
        Generate text with detailed metadata.

        Args:
            prompt: The input prompt for the LLM
            **kwargs: Additional generation parameters

        Returns:
            GenerationResult with content and metadata

        Raises:
            ValueError: If the prompt is invalid
            LLMProviderError: If the LLM service fails
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.generate_with_metadata() must be implemented"
        )

    async def health_check(self) -> bool:
        """
        Check if the provider is healthy and ready.

        Returns:
            True if healthy, False otherwise
        """
        return self.has_api_key


# ============================================================================
# Grok LLM Provider Implementation
# ============================================================================


class GrokLLM(LLMProvider):
    """
    Production-grade xAI Grok API integration for requirements clarification.

    Features:
    - Automatic retry with exponential backoff for transient failures
    - Graceful fallback when API is unavailable
    - Comprehensive error handling and categorization
    - Structured logging without sensitive data exposure
    - Configurable timeouts and retry behavior

    Configuration:
        - api_key: Grok API key (from environment or constructor)
        - model: Model name (default: "grok-1")
        - target_language: Language for responses (default: "en")
        - config: GrokConfig instance for advanced settings

    Example:
        >>> llm = GrokLLM(api_key="your-key", model="grok-1")
        >>> response = await llm.generate("Clarify this requirement...")
    """

    PROVIDER_NAME: Final[str] = "grok"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "grok-1",
        target_language: str = "en",
        config: Optional[GrokConfig] = None,
        fallback_config: Optional[FallbackConfig] = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize Grok LLM provider.

        Args:
            api_key: Grok API key (defaults to GROK_API_KEY env var)
            model: Grok model identifier
            target_language: Target language for responses (ISO code)
            config: GrokConfig instance for advanced settings
            fallback_config: FallbackConfig for customizing fallback responses
            **kwargs: Additional configuration passed to base class
        """
        # Resolve API key from environment if not provided
        resolved_api_key = api_key or os.getenv("GROK_API_KEY", "")

        super().__init__(api_key=resolved_api_key, model=model, **kwargs)

        self.target_language = target_language
        self._config = config or DEFAULT_GROK_CONFIG
        self._fallback_config = fallback_config or DEFAULT_FALLBACK_CONFIG

        # Session management for connection pooling
        self._session: Optional[aiohttp.ClientSession] = None

        if not self.has_api_key:
            logger.warning(
                "GrokLLM initialized without API key",
                extra={
                    "provider": self.PROVIDER_NAME,
                    "model": model,
                    "action_required": "Set GROK_API_KEY environment variable or pass api_key parameter",
                },
            )

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session with connection pooling."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                connect=self._config.connect_timeout,
                sock_read=self._config.read_timeout,
                total=self._config.total_timeout,
            )
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        """Close the HTTP session and release resources."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> "GrokLLM":
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        """
        Generate clarifying questions or responses using Grok API.

        This method makes actual API calls to the xAI Grok service with
        automatic retry for transient failures. Falls back to intelligent
        local generation if the API is unavailable.

        Args:
            prompt: Input prompt for clarification
            **kwargs: Generation parameters (temperature, max_tokens, etc.)

        Returns:
            Generated clarification text

        Raises:
            ValueError: If prompt is empty
            LLMProviderError: If API call fails and cannot be recovered
        """
        result = await self.generate_with_metadata(prompt, **kwargs)
        return result.content

    async def generate_with_metadata(
        self, prompt: str, **kwargs: Any
    ) -> GenerationResult:
        """
        Generate text with detailed metadata including latency and token usage.

        Args:
            prompt: Input prompt for clarification
            **kwargs: Generation parameters

        Returns:
            GenerationResult with content, metadata, and timing information

        Raises:
            ValueError: If prompt is empty
            LLMProviderError: If API call fails after all retries
        """
        # Validate input
        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty")

        start_time = time.monotonic()

        # If no API key, use fallback response generation
        if not self.has_api_key:
            logger.info(
                "Using fallback generation (no API key)",
                extra={
                    "provider": self.PROVIDER_NAME,
                    "prompt_length": len(prompt),
                },
            )
            content = self._generate_fallback_response(prompt)
            latency_ms = (time.monotonic() - start_time) * 1000

            return GenerationResult(
                content=content,
                model=self.model,
                tokens_used=0,
                latency_ms=latency_ms,
                from_fallback=True,
                metadata={"reason": "no_api_key"},
            )

        # Prepare request parameters
        temperature = kwargs.get("temperature", self._config.default_temperature)
        max_tokens = kwargs.get("max_tokens", self._config.default_max_tokens)

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        logger.info(
            "Initiating Grok API call",
            extra={
                "provider": self.PROVIDER_NAME,
                "model": self.model,
                "prompt_length": len(prompt),
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )

        try:
            # Attempt API call with retries
            result = await self._call_api_with_retry(payload)
            latency_ms = (time.monotonic() - start_time) * 1000

            content = result["choices"][0]["message"]["content"]
            tokens_used = result.get("usage", {}).get("total_tokens", 0)

            logger.info(
                "Grok API call successful",
                extra={
                    "provider": self.PROVIDER_NAME,
                    "model": self.model,
                    "response_length": len(content),
                    "tokens_used": tokens_used,
                    "latency_ms": round(latency_ms, 2),
                },
            )

            return GenerationResult(
                content=content,
                model=self.model,
                tokens_used=tokens_used,
                latency_ms=latency_ms,
                from_fallback=False,
                metadata={"finish_reason": result["choices"][0].get("finish_reason")},
            )

        except LLMAuthenticationError:
            # Don't retry auth errors, use fallback
            logger.warning(
                "Using fallback due to authentication failure",
                extra={"provider": self.PROVIDER_NAME},
            )
            content = self._generate_fallback_response(prompt)
            latency_ms = (time.monotonic() - start_time) * 1000

            return GenerationResult(
                content=content,
                model=self.model,
                latency_ms=latency_ms,
                from_fallback=True,
                metadata={"reason": "auth_failure"},
            )

        except LLMRateLimitError as e:
            # Use fallback on rate limit
            logger.warning(
                "Using fallback due to rate limit",
                extra={
                    "provider": self.PROVIDER_NAME,
                    "retry_after": e.retry_after,
                },
            )
            content = self._generate_fallback_response(prompt)
            latency_ms = (time.monotonic() - start_time) * 1000

            return GenerationResult(
                content=content,
                model=self.model,
                latency_ms=latency_ms,
                from_fallback=True,
                metadata={"reason": "rate_limit", "retry_after": e.retry_after},
            )

        except (LLMNetworkError, LLMTimeoutError) as e:
            # Network issues - use fallback
            logger.warning(
                "Using fallback due to network/timeout error",
                extra={
                    "provider": self.PROVIDER_NAME,
                    "error_type": type(e).__name__,
                },
            )
            content = self._generate_fallback_response(prompt)
            latency_ms = (time.monotonic() - start_time) * 1000

            return GenerationResult(
                content=content,
                model=self.model,
                latency_ms=latency_ms,
                from_fallback=True,
                metadata={"reason": "network_error"},
            )

        except LLMProviderError:
            # Re-raise other provider errors
            raise

        except Exception as e:
            # Wrap unexpected errors
            logger.error(
                "Unexpected error during generation",
                extra={
                    "provider": self.PROVIDER_NAME,
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise LLMProviderError(
                f"Unexpected error: {e}",
                provider=self.PROVIDER_NAME,
                error_code="LLM_UNEXPECTED_ERROR",
            ) from e

    async def _call_api_with_retry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call the Grok API with automatic retry for transient failures.

        Args:
            payload: Request payload to send

        Returns:
            Parsed JSON response from API

        Raises:
            LLMAuthenticationError: If authentication fails
            LLMRateLimitError: If rate limit exceeded after retries
            LLMNetworkError: If network error persists after retries
            LLMTimeoutError: If request times out after retries
            LLMResponseError: If API returns error response
        """
        session = await self._get_session()

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        last_exception: Optional[Exception] = None

        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type((LLMNetworkError, LLMTimeoutError)),
                stop=stop_after_attempt(self._config.max_retries),
                wait=wait_exponential(
                    multiplier=1,
                    min=self._config.retry_min_wait,
                    max=self._config.retry_max_wait,
                ),
                reraise=True,
            ):
                with attempt:
                    try:
                        async with session.post(
                            self._config.api_url,
                            headers=headers,
                            json=payload,
                        ) as response:
                            return await self._handle_response(response)

                    except aiohttp.ClientError as e:
                        raise LLMNetworkError(
                            f"Network error: {e}", provider=self.PROVIDER_NAME
                        ) from e

                    except asyncio.TimeoutError as e:
                        raise LLMTimeoutError(
                            "Request timed out", provider=self.PROVIDER_NAME
                        ) from e

        except RetryError as e:
            # All retries exhausted
            last_exception = e.last_attempt.exception()
            if last_exception:
                raise last_exception
            raise LLMProviderError(
                "All retry attempts exhausted",
                provider=self.PROVIDER_NAME,
                error_code="LLM_RETRY_EXHAUSTED",
            )

    async def _handle_response(
        self, response: aiohttp.ClientResponse
    ) -> Dict[str, Any]:
        """
        Handle API response and raise appropriate exceptions for errors.

        Args:
            response: aiohttp response object

        Returns:
            Parsed JSON response

        Raises:
            LLMAuthenticationError: If status is 401
            LLMRateLimitError: If status is 429
            LLMResponseError: If status indicates other error
        """
        status = response.status

        if status == 200:
            return await response.json()

        # Read error body (don't log it as it might contain sensitive info)
        try:
            error_body = await response.text()
        except Exception:
            error_body = "Unable to read error response"

        if status == 401:
            raise LLMAuthenticationError(
                "API authentication failed", provider=self.PROVIDER_NAME
            )

        if status == 429:
            # Try to extract retry-after header
            retry_after = None
            if "Retry-After" in response.headers:
                try:
                    retry_after = int(response.headers["Retry-After"])
                except ValueError:
                    pass

            raise LLMRateLimitError(
                "Rate limit exceeded",
                provider=self.PROVIDER_NAME,
                retry_after=retry_after,
            )

        # Log error details at debug level only (avoid exposing in prod logs)
        logger.debug(
            "API error response",
            extra={
                "provider": self.PROVIDER_NAME,
                "status": status,
                "error_preview": error_body[:100] if error_body else "empty",
            },
        )

        raise LLMResponseError(
            f"API returned status {status}",
            provider=self.PROVIDER_NAME,
            status_code=status,
        )

    def _generate_fallback_response(self, prompt: str) -> str:
        """
        Generate an intelligent fallback response when API is unavailable.

        This method uses the FallbackConfig to generate contextually
        appropriate clarifying questions or code templates based on the prompt type.

        Args:
            prompt: The original prompt

        Returns:
            Generated fallback response with clarifying questions or code template
        """
        prompt_lower = prompt.lower()
        
        # Detect if this is a code generation request
        is_code_request = any(
            keyword in prompt_lower for keyword in CODE_GENERATION_KEYWORDS
        )
        
        # Detect if this is a clarification request
        is_clarification_request = any(
            keyword in prompt_lower for keyword in CLARIFICATION_KEYWORDS
        )

        if is_code_request:
            # Return a minimal valid code structure for code generation requests
            logger.info(
                "Fallback detected code generation request, returning placeholder code",
                extra={"provider": self.PROVIDER_NAME}
            )
            # Return multi-file JSON format with placeholder
            return json.dumps(
                {
                    "files": {
                        "main.py": FALLBACK_PYTHON_CODE,
                        "README.md": FALLBACK_README
                    },
                    "metadata": {
                        "generated_by": "fallback",
                        "note": "API unavailable - placeholder code returned"
                    }
                },
                indent=2
            )
        
        if is_clarification_request:
            # Generate structured clarification response using configured questions
            clarifications = [
                {"question": q.question, "context": q.context, "priority": q.priority}
                for q in self._fallback_config.questions
            ]

            return json.dumps(
                {
                    "clarifications": clarifications,
                    "metadata": {
                        "generated_by": "fallback",
                        "target_language": self.target_language,
                        "note": "API unavailable - using configured fallback questions",
                    },
                },
                indent=2,
            )

        # Generic response using configured guidance
        return self._fallback_config.generic_guidance

    def set_target_language(self, language: str) -> None:
        """
        Update the target language for responses.

        Args:
            language: ISO 639-1 language code (e.g., 'en', 'es', 'fr')
        """
        self.target_language = language
        logger.info(
            "Updated target language",
            extra={
                "provider": self.PROVIDER_NAME,
                "language": language,
            },
        )

    async def health_check(self) -> bool:
        """
        Perform health check against the Grok API.

        Returns:
            True if API is reachable and authenticated, False otherwise
        """
        if not self.has_api_key:
            return False

        try:
            session = await self._get_session()
            headers = {"Authorization": f"Bearer {self._api_key}"}

            async with session.get(
                "https://api.x.ai/v1/models",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                is_healthy = response.status == 200

                logger.debug(
                    "Health check completed",
                    extra={
                        "provider": self.PROVIDER_NAME,
                        "healthy": is_healthy,
                        "status": response.status,
                    },
                )

                return is_healthy

        except Exception as e:
            logger.warning(
                "Health check failed",
                extra={
                    "provider": self.PROVIDER_NAME,
                    "error_type": type(e).__name__,
                },
            )
            return False


# ============================================================================
# Unified LLM Provider (Uses Central runner/llm_client.py)
# ============================================================================


class UnifiedLLMProvider(LLMProvider):
    """
    LLM Provider that uses the central runner/llm_client.py for unified LLM access.
    
    This provider wraps the central LLM client to provide consistent access to
    multiple LLM providers (OpenAI, Anthropic, xAI, Google, Ollama) with unified
    retry logic, rate limiting, circuit breaker, and metrics.
    
    Features:
    - Auto-detected LLM provider (OpenAI, Anthropic, xAI, Google, Ollama)
    - Unified retry logic and rate limiting
    - Circuit breaker for resilience
    - Consistent metrics and logging
    - Graceful fallback on errors
    
    Example:
        >>> llm = UnifiedLLMProvider(provider="openai", model="gpt-4")
        >>> response = await llm.generate("Clarify this requirement...")
    """
    
    PROVIDER_NAME: Final[str] = "unified"
    
    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4",
        fallback_config: Optional[FallbackConfig] = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize Unified LLM provider.
        
        Args:
            provider: LLM provider name (openai, anthropic, grok, google, ollama)
            model: Model identifier for the provider
            fallback_config: FallbackConfig for customizing fallback responses
            **kwargs: Additional configuration passed to base class
        """
        super().__init__(api_key=None, model=model, **kwargs)
        
        self.provider = provider
        self._fallback_config = fallback_config or DEFAULT_FALLBACK_CONFIG
        
        logger.info(
            "Initialized UnifiedLLMProvider",
            extra={
                "provider": provider,
                "model": model,
            },
        )
    
    async def generate(self, prompt: str, **kwargs: Any) -> str:
        """
        Generate clarifying questions or responses using central LLM client.
        
        This method uses the runner/llm_client.py for actual API calls with
        automatic retry for transient failures. Falls back to intelligent
        local generation if the API is unavailable.
        
        Args:
            prompt: Input prompt for clarification
            **kwargs: Generation parameters (temperature, max_tokens, etc.)
        
        Returns:
            Generated clarification text
        
        Raises:
            ValueError: If prompt is empty
            LLMProviderError: If API call fails and cannot be recovered
        """
        result = await self.generate_with_metadata(prompt, **kwargs)
        return result.content
    
    async def generate_with_metadata(
        self, prompt: str, **kwargs: Any
    ) -> GenerationResult:
        """
        Generate text with detailed metadata using central LLM client.
        
        Args:
            prompt: Input prompt for clarification
            **kwargs: Generation parameters
        
        Returns:
            GenerationResult with content, metadata, and timing information
        
        Raises:
            ValueError: If prompt is empty
            LLMProviderError: If API call fails after all retries
        """
        # Validate input
        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty")
        
        start_time = time.monotonic()
        
        try:
            # Import the central LLM client
            from runner.llm_client import call_llm_api
            
            logger.info(
                "Calling central LLM client",
                extra={
                    "provider": self.provider,
                    "model": self.model,
                    "prompt_length": len(prompt),
                },
            )
            
            # Call the central LLM client
            response = await call_llm_api(
                prompt=prompt,
                model=self.model,
                provider=self.provider,
                **kwargs,
            )
            
            latency_ms = (time.monotonic() - start_time) * 1000
            
            # Extract content from response
            content = response.get("content", "")
            tokens_used = response.get("tokens_used", 0)
            
            logger.info(
                "Central LLM client call successful",
                extra={
                    "provider": self.provider,
                    "model": self.model,
                    "response_length": len(content),
                    "tokens_used": tokens_used,
                    "latency_ms": round(latency_ms, 2),
                },
            )
            
            return GenerationResult(
                content=content,
                model=self.model,
                tokens_used=tokens_used,
                latency_ms=latency_ms,
                from_fallback=False,
                metadata={"provider": self.provider},
            )
        
        except Exception as e:
            # Use fallback on any error
            logger.warning(
                "Using fallback due to central LLM client error",
                extra={
                    "provider": self.provider,
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
            )
            
            content = self._generate_fallback_response(prompt)
            latency_ms = (time.monotonic() - start_time) * 1000
            
            return GenerationResult(
                content=content,
                model=self.model,
                latency_ms=latency_ms,
                from_fallback=True,
                metadata={"reason": "llm_client_error", "error": str(e)},
            )
    
    def _generate_fallback_response(self, prompt: str) -> str:
        """
        Generate an intelligent fallback response when API is unavailable.
        
        This method uses the FallbackConfig to generate contextually
        appropriate clarifying questions or code templates based on the prompt type.
        
        Args:
            prompt: The original prompt
        
        Returns:
            Generated fallback response with clarifying questions or code template
        """
        prompt_lower = prompt.lower()
        
        # Detect if this is a code generation request
        is_code_request = any(
            keyword in prompt_lower for keyword in CODE_GENERATION_KEYWORDS
        )
        
        # Detect if this is a clarification request
        is_clarification_request = any(
            keyword in prompt_lower for keyword in CLARIFICATION_KEYWORDS
        )
        
        if is_code_request:
            # Return a minimal valid code structure for code generation requests
            logger.info(
                "Fallback detected code generation request, returning placeholder code",
                extra={"provider": self.provider}
            )
            # Return multi-file JSON format with placeholder
            return json.dumps(
                {
                    "files": {
                        "main.py": FALLBACK_PYTHON_CODE,
                        "README.md": FALLBACK_README
                    },
                    "metadata": {
                        "generated_by": "fallback",
                        "note": "Central LLM client unavailable - placeholder code returned"
                    }
                },
                indent=2
            )
        
        if is_clarification_request:
            # Generate structured clarification response using configured questions
            clarifications = [
                {"question": q.question, "context": q.context, "priority": q.priority}
                for q in self._fallback_config.questions
            ]
            
            return json.dumps(
                {
                    "clarifications": clarifications,
                    "metadata": {
                        "generated_by": "fallback",
                        "note": "Central LLM client unavailable - using fallback questions",
                    },
                },
                indent=2,
            )
        
        # Generic response using configured guidance
        return self._fallback_config.generic_guidance
    
    async def health_check(self) -> bool:
        """
        Perform health check via central LLM client.
        
        Returns:
            True if LLM client is healthy, False otherwise
        """
        try:
            from runner.llm_client import call_llm_api
            
            # Try a minimal prompt to check health
            await call_llm_api(
                prompt="test",
                model=self.model,
                provider=self.provider,
            )
            return True
        except Exception as e:
            logger.warning(
                "Health check failed",
                extra={
                    "provider": self.provider,
                    "error_type": type(e).__name__,
                },
            )
            return False


# ============================================================================
# Factory Function
# ============================================================================


def create_llm_provider(provider_type: str = "grok", **kwargs: Any) -> LLMProvider:
    """
    Factory function to create LLM provider instances.

    Args:
        provider_type: Type of provider to create ('grok', 'unified', 'openai', 'anthropic', etc.)
        **kwargs: Provider-specific configuration

    Returns:
        Configured LLM provider instance

    Raises:
        ValueError: If provider_type is not supported

    Example:
        >>> provider = create_llm_provider("grok", api_key="your-key")
        >>> response = await provider.generate("Clarify this requirement")
        
        >>> # Or use unified provider for OpenAI
        >>> provider = create_llm_provider("unified", provider="openai", model="gpt-4")
        >>> response = await provider.generate("Clarify this requirement")
    """
    # Map provider types to classes
    providers: Dict[str, type] = {
        "grok": GrokLLM,
        "unified": UnifiedLLMProvider,
        # Aliases for convenience
        "openai": lambda **kw: UnifiedLLMProvider(provider="openai", **kw),
        "anthropic": lambda **kw: UnifiedLLMProvider(provider="anthropic", **kw),
        "google": lambda **kw: UnifiedLLMProvider(provider="google", **kw),
        "ollama": lambda **kw: UnifiedLLMProvider(provider="ollama", **kw),
    }

    provider_class = providers.get(provider_type.lower())
    if not provider_class:
        supported = ", ".join(sorted(providers.keys()))
        raise ValueError(
            f"Unknown LLM provider: '{provider_type}'. "
            f"Supported providers: {supported}"
        )

    # Handle both class and lambda constructors
    if callable(provider_class) and not isinstance(provider_class, type):
        return provider_class(**kwargs)
    return provider_class(**kwargs)


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    # Base classes
    "LLMProvider",
    "GrokLLM",
    "UnifiedLLMProvider",
    # Factory
    "create_llm_provider",
    # Configuration
    "GrokConfig",
    "DEFAULT_GROK_CONFIG",
    "FallbackConfig",
    "FallbackQuestion",
    "DEFAULT_FALLBACK_CONFIG",
    # Types
    "GenerationParams",
    "GenerationResult",
    # Exceptions
    "LLMProviderError",
    "LLMAuthenticationError",
    "LLMRateLimitError",
    "LLMNetworkError",
    "LLMTimeoutError",
    "LLMResponseError",
]
