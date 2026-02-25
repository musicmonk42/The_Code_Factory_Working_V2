# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# runner/llm_client.py
"""
UNIFIED LLM CLIENT (2025 Production Edition) — PLUGIN ORCHESTRATOR + ENHANCED FEATURES

Features:
- Single import: `from runner.llm_client import call_llm_api`
- Dynamic plugins (OpenAI/Claude/Grok/Gemini/Local)
- Ensemble calls with voting
- Redis-backed caching
- Distributed rate limiting
- Secrets management (env/.env)
- 2025 models (e.g., GPT-5, Claude 4.5, Gemini 3.0)
- Observability (OTEL/Prometheus)
- Resilience (circuit breaker/retries)
- Security (redaction)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections import Counter

# FIX: Import Path
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional

import redis.asyncio as aioredis

# FIX: Import metrics module, not individual components to avoid import cycle issues
from . import runner_metrics as metrics
from dotenv import load_dotenv
from .llm_plugin_manager import LLMPluginManager

# Runner Foundation
from .runner_config import RunnerConfig
from .runner_errors import ConfigurationError, LLMError

# [FIX] Import log_audit_event instead of add_provenance
from .runner_logging import log_audit_event, logger

# FIX: Import only redact_secrets
from .runner_security_utils import redact_secrets

# Conditional SDKs
try:
    from openai import APIError as OpenAIError
    from openai import AsyncOpenAI

    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
try:
    from anthropic import AnthropicError, AsyncAnthropic

    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False
try:
    import google.generativeai as genai

    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False
try:
    import tiktoken

    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False

# FIX: Set TOKENIZERS_PARALLELISM to avoid warning about fork safety
# This prevents the warning: "The current process just got forked, after parallelism has already been used"
if "TOKENIZERS_PARALLELISM" not in os.environ:
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    logger.debug("Set TOKENIZERS_PARALLELISM=false to prevent fork warnings")

# Constants for retry logic
# Reduced retries and increased backoff to prevent rate limit exhaustion
DEFAULT_MAX_RETRIES = 2  # Reduced from 3 to 2
BASE_BACKOFF_SECONDS = 2.0  # Increased from 1.0 to 2.0
# Multiplier applied to per-provider timeout to derive the hard cap on total
# ensemble wall-clock time.  Can be overridden via the
# ENSEMBLE_TOTAL_TIMEOUT_MULTIPLIER environment variable.
ENSEMBLE_TOTAL_TIMEOUT_MULTIPLIER: float = float(
    os.getenv("ENSEMBLE_TOTAL_TIMEOUT_MULTIPLIER", "1.5")
)

# Global LLM call budget per job to prevent exhaustion
# Can be overridden via JOB_LLM_BUDGET environment variable
DEFAULT_JOB_LLM_BUDGET = int(os.getenv("JOB_LLM_BUDGET", "50"))  # Maximum LLM calls per job
JOB_LLM_CALL_TRACKER: Dict[str, int] = {}  # Track calls per job_id

# Provider default models for fallback scenarios
_PROVIDER_DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "gemini": "gemini-pro",
    "local": "codellama",
    "grok": "grok-beta",
    "claude": "claude-3-sonnet-20240229",
}


# --- Secrets Management ---
class SecretsManager:
    def __init__(self):
        self._cache: Dict[str, Optional[str]] = {}
        self.app_env = os.getenv("APP_ENV", "development").lower()
        if self.app_env != "production":
            load_dotenv()
            logger.info("SecretsManager: Loaded .env for development")
        else:
            logger.info("SecretsManager: Prod mode, no .env")

    # FIX: Renamed 'get' to 'get_secret' to match test expectation
    def get_secret(self, secret_name: str) -> Optional[str]:
        if secret_name in self._cache:
            return self._cache[secret_name]

        # In a real app, this should prioritize the environment variable over anything loaded by dotenv
        raw_secret = os.environ.get(secret_name)
        
        # FIX: Sanitize environment variables from Railway/cloud providers that may include
        # wrapping quotes or whitespace that cause API key validation failures
        secret = None
        if raw_secret:
            sanitized = raw_secret.strip()
            # Remove wrapping quotes only (preserves quotes in the middle of values)
            if len(sanitized) >= 2:
                if (sanitized.startswith('"') and sanitized.endswith('"')) or \
                   (sanitized.startswith("'") and sanitized.endswith("'")):
                    sanitized = sanitized[1:-1]
            secret = sanitized if sanitized else None
            
            # Log if sanitization changed the value (indicates potential config issue)
            if raw_secret != secret:
                logger.debug(f"SecretsManager: Sanitized {secret_name} (removed wrapping quotes/whitespace)")
        
        self._cache[secret_name] = secret
        return secret

    def get_required(self, secret_name: str) -> str:
        secret = self.get_secret(secret_name)  # FIX: Use get_secret
        if not secret:
            raise ConfigurationError(
                f"Missing secret: {secret_name}",
                detail=f"Environment variable {secret_name} is not set",
            )
        return secret


# --- Cache Manager ---
def _redact_redis_url(url: str) -> str:
    """Redact password from Redis URL for safe logging."""
    import re
    # Pattern: redis://[username]:password@host:port
    # Replace password with [REDACTED]
    return re.sub(r'(redis://[^:]*:)[^@]+(@)', r'\1[REDACTED]\2', url)


class CacheManager:
    def __init__(self, redis_url: Optional[str] = None):
        self.redis = None
        if redis_url:
            try:
                self.redis = aioredis.from_url(redis_url)
                # Redact password before logging
                safe_url = _redact_redis_url(redis_url)
                logger.info(f"CacheManager: Connected to Redis at {safe_url}")
            except Exception as e:
                # Redact password before logging
                safe_url = _redact_redis_url(redis_url)
                logger.error(
                    f"CacheManager: Failed to connect to Redis at {safe_url}. Falling back to in-memory. Error: {e}"
                )
                self.redis = None
        self.in_memory: Dict[str, Any] = {}

    async def get(self, key: str) -> Optional[Any]:
        if self.redis:
            try:
                value = await self.redis.get(key)
                return json.loads(value) if value else None
            except Exception as e:
                logger.error(
                    f"CacheManager: Redis GET failed. Falling back to in-memory. Error: {e}"
                )
                return self.in_memory.get(key)  # Fallback to in-memory on error
        return self.in_memory.get(key)

    async def set(self, key: str, value: Any, ttl: int = 3600):
        try:
            value_json = json.dumps(value)
        except TypeError as e:
            logger.error(
                f"CacheManager: Failed to serialize value for cache key {key}. Error: {e}"
            )
            return

        if self.redis:
            try:
                await self.redis.set(key, value_json, ex=ttl)
            except Exception as e:
                logger.error(
                    f"CacheManager: Redis SET failed. Saving to in-memory. Error: {e}"
                )
                self.in_memory[key] = value  # Fallback to in-memory on error
        else:
            self.in_memory[key] = value

    async def close(self):
        if self.redis:
            await self.redis.aclose()


# --- Rate Limiter ---
class DistributedRateLimiter:
    def __init__(
        self, redis_url: Optional[str] = None, limit: int = 100, window: int = 60
    ):
        self.redis = None
        self.limit = limit
        self.window = window

        # Wrap Redis connection in try-except to handle connection failures gracefully
        if redis_url:
            try:
                self.redis = aioredis.from_url(redis_url)
                # Redact sensitive info from URL for logging
                safe_url = (
                    redis_url.split("@")[-1]
                    if "@" in redis_url
                    else redis_url.split("//")[1] if "//" in redis_url else "configured"
                )
                logger.info(
                    f"DistributedRateLimiter initialized with Redis at {safe_url}"
                )
            except Exception as e:
                logger.warning(
                    f"DistributedRateLimiter: Failed to connect to Redis: {e}. "
                    "Falling back to no rate limiting."
                )
                self.redis = None
        else:
            logger.info(
                "DistributedRateLimiter initialized without Redis (no rate limiting)"
            )

    async def acquire(self, key: str) -> bool:
        if not self.redis:
            return True  # No rate limiting if Redis isn't configured
        try:
            current = await self.redis.get(key)
            count = int(current or 0)
            if count >= self.limit:
                metrics.LLM_RATE_LIMIT_EXCEEDED.labels(provider="any").inc()
                return False

            # Use a pipeline for atomic incr/expire
            pipe = self.redis.pipeline()
            await pipe.incr(key)
            await pipe.expire(
                key, self.window, nx=True
            )  # NX = only set expire if it doesn't exist
            await pipe.execute()
            return True
        except Exception as e:
            logger.error(
                f"RateLimiter: Redis acquire failed. Allowing request. Error: {e}"
            )
            return True  # Fail open

    async def close(self):
        if self.redis:
            await self.redis.aclose()


# --- Circuit Breaker ---
from shared.circuit_breaker import CircuitBreaker  # noqa: E402


# --- Unified LLM Client ---
class LLMClient:
    """
    Enterprise-grade LLM client with comprehensive error handling and observability.
    
    This client implements industry-standard practices:
    - Defensive programming with path validation
    - Comprehensive error handling and logging
    - Circuit breaking and rate limiting
    - Redis-backed caching with graceful fallback
    - Plugin-based provider architecture
    - Full observability with metrics and tracing
    """
    
    def __init__(self, config: RunnerConfig):
        """
        Initialize LLM client with validated provider directory.
        
        Args:
            config: Runner configuration object
            
        Raises:
            ValueError: If provider directory doesn't exist
            ConfigurationError: If required dependencies are missing
        """
        self.config = config
        
        # INDUSTRY STANDARD: Defensive path validation before use
        # Explicitly construct and validate provider directory path
        provider_dir = Path(__file__).parent / "providers"
        
        if not provider_dir.exists():
            error_msg = (
                f"LLM provider directory not found: {provider_dir.absolute()}. "
                f"Expected provider files (*_provider.py) in this directory. "
                f"Cannot initialize LLM client without providers."
            )
            logger.error(error_msg, extra={"provider_dir": str(provider_dir)})
            raise ValueError(error_msg)
        
        if not provider_dir.is_dir():
            error_msg = (
                f"Provider path exists but is not a directory: {provider_dir.absolute()}"
            )
            logger.error(error_msg, extra={"provider_dir": str(provider_dir)})
            raise ValueError(error_msg)
        
        # Log provider directory for diagnostics
        logger.info(
            "Initializing LLM client with provider directory: %s",
            provider_dir.absolute(),
            extra={
                "provider_dir": str(provider_dir.absolute()),
                "provider_files": [
                    p.name for p in provider_dir.glob("*_provider.py")
                    if not p.name.startswith("_")
                ],
            },
        )
        
        # Initialize plugin manager with validated provider directory
        self.manager = LLMPluginManager(plugin_dir=provider_dir)
        
        # Initialize other components with proper error handling
        self.secrets = SecretsManager()
        self.cache = CacheManager(config.redis_url)
        self.rate_limiter = DistributedRateLimiter(config.redis_url)
        self.circuit_breaker = CircuitBreaker(state_metric_gauge=metrics.LLM_CIRCUIT_STATE)
        self._is_initialized = asyncio.Event()
        self._init_task = None
        
        # Job-level LLM call budget tracking
        self.job_llm_budget = getattr(config, 'job_llm_budget', DEFAULT_JOB_LLM_BUDGET)
        self.job_call_counts: Dict[str, int] = {}  # Track calls per job_id

    @classmethod
    async def create(cls, config: RunnerConfig) -> "LLMClient":
        """
        Factory method to create and initialize LLMClient.
        This is the recommended way to create an LLMClient instance.

        Usage:
            client = await LLMClient.create(config)
        """
        client = cls(config)
        await client._initialize()
        return client

    def _ensure_initialization(self):
        """
        Lazy initialization: Start initialization task if not already started.
        This allows backward compatibility with direct instantiation while avoiding
        asyncio.create_task() in __init__.
        """
        if self._init_task is None:
            try:
                loop = asyncio.get_running_loop()
                self._init_task = loop.create_task(self._initialize())
            except RuntimeError:
                # No event loop running - initialization will happen on first use
                logger.warning(
                    "LLMClient: No event loop running during initialization. "
                    "Initialization will be deferred until first use."
                )

    async def _initialize(self):
        # NOTE: self.manager._load_task is awaited here.
        # If the test mocks self.manager, it must ensure _load_task is awaitable (e.g., an AsyncMock).
        if hasattr(self.manager, "_load_task"):
            await self.manager._load_task
        else:
            logger.warning(
                "LLMPluginManager does not have _load_task attribute. Skipping initialization wait."
            )

        available_providers = self.manager.list_providers()
        for name in available_providers:
            metrics.LLM_PROVIDER_HEALTH.labels(provider=name).set(1)
        
        self._is_initialized.set()
        
        logger.info(
            "[LLM] Provider initialization complete. Available: %s, Unavailable: %s",
            available_providers or "NONE",
            [p for p in ["openai", "gemini", "grok", "claude", "local"] if p not in available_providers]
        )

    async def count_tokens(self, text: str, model: str) -> int:
        if not HAS_TIKTOKEN:
            return int(len(text.split()) * 1.3)
        try:
            try:
                encoding = tiktoken.encoding_for_model(model)
            except (KeyError, ValueError):
                encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except Exception as e:
            logger.warning(
                f"Failed to count tokens using tiktoken: {e}. Falling back to word-based estimation."
            )
            return int(len(text.split()) * 1.3)
    
    def _detect_model_provider(self, model: str) -> Optional[str]:
        """
        Detect which provider a model name belongs to based on naming patterns.
        
        Args:
            model: Model name (e.g., "gpt-4o", "gemini-pro", "claude-3-sonnet")
            
        Returns:
            Provider name or None if unable to detect
        """
        model_lower = model.lower()
        
        # Check for provider-specific prefixes/patterns
        if "gpt" in model_lower:
            return "openai"
        elif "gemini" in model_lower:
            return "gemini"
        elif "claude" in model_lower:
            return "claude"
        elif "grok" in model_lower:
            return "grok"
        elif any(x in model_lower for x in ["codellama", "llama", "mistral"]):
            return "local"
        
        return None
    
    def _remap_model_for_provider(self, model: str, target_provider: str) -> str:
        """
        Remap a model name to be compatible with the target provider.
        
        Args:
            model: Original model name
            target_provider: The provider to remap the model for
            
        Returns:
            Remapped model name suitable for the target provider
        """
        model_provider = self._detect_model_provider(model)
        
        # If model already belongs to target provider, no remapping needed
        if model_provider == target_provider:
            return model
        
        # If we detected the model belongs to a different provider, remap to default
        if model_provider and model_provider != target_provider:
            remapped = _PROVIDER_DEFAULT_MODELS.get(target_provider, model)
            logger.info(
                f"[LLM] Model remapped for fallback: {model} ({model_provider}) -> {remapped} ({target_provider})"
            )
            return remapped
        
        # If we couldn't detect provider, assume it needs remapping and use default
        return _PROVIDER_DEFAULT_MODELS.get(target_provider, model)
    
    def _get_fallback_providers(self, primary_provider: str) -> List[str]:
        """
        Get list of fallback providers to try when primary provider fails.
        
        Returns providers in order of preference, excluding the primary provider.
        
        Args:
            primary_provider: The provider that failed
            
        Returns:
            List of fallback provider names
        """
        # Define provider fallback hierarchy
        # Priority: openai > gemini > local
        all_providers = ["openai", "gemini", "local"]
        
        # Remove the primary provider from the list
        fallback_providers = [p for p in all_providers if p != primary_provider]
        
        # Filter to only providers that have plugins loaded
        available_fallbacks = [
            p for p in fallback_providers 
            if self.manager.get_provider(p) is not None
        ]
        
        return available_fallbacks

    async def call_llm_api(
        self,
        prompt: str,
        model: Optional[str] = None,
        stream: bool = False,
        provider: Optional[
            Literal["openai", "claude", "grok", "gemini", "local"]
        ] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        job_id: Optional[str] = None,  # Added for job-level budget tracking
        **kwargs,
    ) -> Dict[str, Any] | AsyncGenerator[str, None]:
        # Ensure initialization has started (lazy initialization for backward compatibility)
        self._ensure_initialization()
        await self._is_initialized.wait()
        provider = provider or getattr(self.config, 'llm_provider', 'openai') or "openai"
        # FIX #4: Changed default model from gpt-4 to gpt-4o to handle higher TPM limits
        # The critique semantic analysis was hitting 10,000 TPM limit with gpt-4 (20,587 tokens requested)
        # gpt-4o has much higher rate limits and is also cheaper and faster
        model = model or getattr(self.config, "default_llm_model", "gpt-4o")

        # [FIX] Redact secrets from the prompt *before* it's used in cache keys or logs
        # [FIX] redact_secrets is now synchronous, remove await
        prompt = redact_secrets(prompt)
        
        # Check job-level LLM call budget
        if job_id:
            current_count = self.job_call_counts.get(job_id, 0)
            if current_count >= self.job_llm_budget:
                error_msg = (
                    f"Job {job_id} has exhausted LLM call budget "
                    f"({current_count}/{self.job_llm_budget} calls). "
                    f"Aborting gracefully to prevent rate limit exhaustion."
                )
                logger.error(error_msg, extra={"job_id": job_id, "call_count": current_count})
                metrics.LLM_ERRORS_TOTAL.labels(provider=provider, model=model).inc()
                raise LLMError(error_msg)
            
            # Increment call counter for this job
            self.job_call_counts[job_id] = current_count + 1
            logger.info(
                f"Job {job_id} LLM call {self.job_call_counts[job_id]}/{self.job_llm_budget}",
                extra={"job_id": job_id, "call_count": self.job_call_counts[job_id], "budget": self.job_llm_budget}
            )
        
        # FIX: Add retry logic with exponential backoff
        for attempt in range(max_retries):
            try:
                start_time = time.time()

                if not await self.rate_limiter.acquire(provider):
                    metrics.LLM_ERRORS_TOTAL.labels(provider=provider, model=model).inc()
                    raise LLMError("Rate limit exceeded")

                # FIX: Log circuit breaker state before call
                circuit_state = self.circuit_breaker.get_state(provider)
                logger.debug(
                    "[LLM] Circuit breaker state",
                    extra={
                        "provider": provider,
                        "state": circuit_state,
                        "failure_count": self.circuit_breaker.get_failure_count(provider),
                    }
                )
                
                if not await self.circuit_breaker.allow_request(provider):
                    # FIX: Log when circuit is open and blocking call
                    logger.warning(
                        "[LLM] Circuit breaker OPEN - call blocked",
                        extra={
                            "provider": provider,
                            "state": self.circuit_breaker.get_state(provider),
                            "failure_count": self.circuit_breaker.get_failure_count(provider),
                        }
                    )
                    metrics.LLM_ERRORS_TOTAL.labels(provider=provider, model=model).inc()
                    raise LLMError("Circuit breaker open")

                skip_cache = kwargs.pop("skip_cache", False)
                cache_key = hashlib.sha256(f"{prompt}:{model}:{provider}".encode()).hexdigest()
                if not skip_cache:
                    cached = await self.cache.get(cache_key)
                    if cached and not stream:
                        metrics.LLM_CALLS_TOTAL.labels(provider=provider, model=model).inc()
                        logger.info(
                            f"[LLM] Cache HIT for {provider}/{model}",
                            extra={
                                "provider": provider,
                                "model": model,
                                "cache_key": cache_key[:16],
                                "job_id": job_id
                            }
                        )
                        return cached

                plugin = self.manager.get_provider(provider)
                # [FIX] Graceful degradation if provider plugin failed to load (e.g., missing SDK/Key)
                if not plugin:
                    metrics.LLM_ERRORS_TOTAL.labels(provider=provider, model=model).inc()
                    self.circuit_breaker.record_failure(provider)
                    raise ConfigurationError(
                        f"LLM provider '{provider}' not loaded",
                        detail="SDK or API key may be missing",
                    )

                logger.info(f"[LLM] Calling {provider}/{model} with prompt length: {len(prompt)}")
                response = await plugin.call(
                    prompt=prompt, model=model, stream=stream, **kwargs
                )
                latency = time.time() - start_time
                logger.info(f"[LLM] {provider}/{model} responded in {latency:.1f}s")
                metrics.LLM_LATENCY_SECONDS.labels(provider=provider, model=model).observe(
                    latency
                )
                metrics.LLM_CALLS_TOTAL.labels(provider=provider, model=model).inc()
                self.circuit_breaker.record_success(provider)  # Record success here

                if isinstance(response, dict):
                    input_tokens = await self.count_tokens(prompt, model)
                    output_tokens = await self.count_tokens(
                        response.get("content", ""), model
                    )
                    metrics.LLM_TOKENS_INPUT.labels(provider=provider, model=model).inc(
                        input_tokens
                    )
                    metrics.LLM_TOKENS_OUTPUT.labels(provider=provider, model=model).inc(
                        output_tokens
                    )
                    # [FIX] Replace add_provenance with log_audit_event
                    await log_audit_event(
                        action="llm_call",
                        data={
                            "provider": provider,
                            "model": model,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        },
                    )
                    await self.cache.set(cache_key, response)
                    return response
                else:
                    # Handle async generator for streaming
                    async def stream_generator():
                        total_output = ""
                        try:
                            async for chunk in response:
                                total_output += chunk
                                yield chunk
                        finally:
                            input_tokens = await self.count_tokens(prompt, model)
                            output_tokens = await self.count_tokens(total_output, model)
                            metrics.LLM_TOKENS_INPUT.labels(
                                provider=provider, model=model
                            ).inc(input_tokens)
                            metrics.LLM_TOKENS_OUTPUT.labels(
                                provider=provider, model=model
                            ).inc(output_tokens)
                            # [FIX] Log audit event for streaming call
                            await log_audit_event(
                                action="llm_stream_call",
                                data={
                                    "provider": provider,
                                    "model": model,
                                    "input_tokens": input_tokens,
                                    "output_tokens": output_tokens,
                                },
                            )

                    return stream_generator()

            except (LLMError, ConfigurationError) as e:
                # Try fallback providers on ANY error, not just circuit breaker
                fallback_providers = self._get_fallback_providers(provider)
                if fallback_providers and attempt == 0:  # Only try fallback on first attempt
                    logger.warning(
                        f"Provider {provider} failed with {type(e).__name__}: {e}. "
                        f"Attempting fallback providers: {fallback_providers}"
                    )
                    for fallback_provider in fallback_providers:
                        try:
                            # Check if fallback provider is available
                            if await self.circuit_breaker.allow_request(fallback_provider):
                                logger.info(f"Trying fallback provider: {fallback_provider}")
                                
                                # Remap model for fallback provider
                                fallback_model = self._remap_model_for_provider(model, fallback_provider)
                                
                                # Recursively call with fallback provider and remapped model
                                return await self.call_llm_api(
                                    prompt=prompt,
                                    model=fallback_model,
                                    stream=stream,
                                    provider=fallback_provider,
                                    max_retries=1,  # Limit retries for fallback
                                    job_id=job_id,  # Pass job_id for budget tracking
                                    **kwargs
                                )
                        except Exception as fallback_error:
                            logger.warning(
                                f"Fallback provider {fallback_provider} also failed: {fallback_error}"
                            )
                            continue
                
                # No fallback succeeded
                metrics.LLM_ERRORS_TOTAL.labels(provider=provider, model=model).inc()
                self.circuit_breaker.record_failure(provider)
                raise
            except Exception as e:
                metrics.LLM_ERRORS_TOTAL.labels(provider=provider, model=model).inc()
                self.circuit_breaker.record_failure(provider)

                # Retry with exponential backoff
                if attempt < max_retries - 1:
                    backoff_time = (2 ** attempt) * BASE_BACKOFF_SECONDS
                    logger.warning(
                        f"LLM call failed (attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {backoff_time}s: {e}"
                    )
                    await asyncio.sleep(backoff_time)
                else:
                    # Last attempt failed, raise error
                    raise LLMError(f"LLM call failed after {max_retries} retries: {e}") from e

    async def _call_llm_with_provider_timeout(
        self,
        prompt: str,
        provider: str,
        model: str,
        timeout: float,
        **kwargs,
    ) -> Dict[str, Any]:
        """Invoke ``call_llm_api`` for a single provider with a hard timeout.

        Designed to be called from ``call_ensemble_api`` so that the timeout
        logic lives in one place rather than as closures captured inside a loop.
        Logs a structured ``[ENSEMBLE]`` error before re-raising so that
        operators can immediately identify which provider is the bottleneck.

        Args:
            prompt: Prompt text forwarded to ``call_llm_api``.
            provider: LLM provider name (e.g. ``"openai"``).
            model: Model identifier (e.g. ``"gpt-4o"``).
            timeout: Maximum seconds to wait for this provider to respond.
            **kwargs: Additional arguments forwarded to ``call_llm_api``.

        Returns:
            The raw response dict from ``call_llm_api``.

        Raises:
            asyncio.TimeoutError: If the provider does not respond within *timeout*.
            LLMError: Propagated from ``call_llm_api`` on non-timeout failures.
        """
        try:
            return await asyncio.wait_for(
                self.call_llm_api(prompt, model=model, provider=provider, **kwargs),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.error(
                "[ENSEMBLE] Provider %s/%s timed out after %.0fs",
                provider, model, timeout,
            )
            raise LLMError(f"Provider {provider}/{model} timed out after {timeout:.0f}s")

    async def call_ensemble_api(
        self,
        prompt: str,
        models: List[Dict[str, str]],  # List of {provider, model}
        voting_strategy: str = "majority",
        timeout_per_provider: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Call multiple LLM providers in parallel and combine their responses.

        Provider availability is checked against the plugin registry before any
        network I/O is attempted; providers that are not loaded are skipped with a
        warning rather than being called (which would otherwise hang waiting for an
        initialization event that never fires).

        Two timeouts are enforced:

        * **Per-provider timeout** (``timeout_per_provider`` / ``ENSEMBLE_PROVIDER_TIMEOUT_SECONDS``
          env var, default 180 s): each individual provider call is wrapped in
          ``asyncio.wait_for`` inside ``_call_llm_with_provider_timeout``.
        * **Total ensemble timeout** (``per_provider × ENSEMBLE_TOTAL_TIMEOUT_MULTIPLIER``,
          configurable via the env var of the same name, default 1.5): hard wall-clock
          cap on the entire ``asyncio.gather`` to prevent indefinite hangs.

        On total timeout all outstanding :class:`asyncio.Task` objects are cancelled
        and awaited so that no dangling coroutines remain.

        Args:
            prompt: Prompt forwarded to every provider.
            models: List of ``{"provider": ..., "model": ...}`` dicts.
            voting_strategy: How to combine results. ``"majority"`` returns the
                most-common response; any other value returns the first success.
            timeout_per_provider: Per-provider deadline in seconds.  Defaults to
                ``ENSEMBLE_PROVIDER_TIMEOUT_SECONDS`` (default 300 s).
            **kwargs: Additional keyword arguments forwarded to ``call_llm_api``.

        Returns:
            Dict with at minimum ``"content"`` and ``"ensemble_results"`` keys.
            Also includes ``"skipped_providers"`` listing any providers that were
            omitted because they were not loaded at call time.

        Raises:
            LLMError: If no providers are available, if the total ensemble timeout
                is exceeded, or if every attempted provider fails.
        """
        await self._is_initialized.wait()
        results: List[Dict[str, Any]] = []

        # Validate models list before processing
        if not models:
            raise LLMError("Empty models list provided to ensemble API")

        # Resolve per-provider timeout: explicit parameter > env var > 180s default
        effective_timeout: float = (
            timeout_per_provider
            if timeout_per_provider is not None
            else float(os.environ.get("ENSEMBLE_PROVIDER_TIMEOUT_SECONDS", "300"))
        )

        # Snapshot which providers are currently loaded so we can skip ones that
        # were never initialized (avoids hanging on _is_initialized.wait() inside
        # call_llm_api for providers that failed to load).
        available_providers = self.manager.list_providers()
        logger.info(
            "[ENSEMBLE] Available providers: %s; requested: %s",
            available_providers,
            [m.get("provider", "<inferred>") for m in models],
        )

        # Pre-flight: fail fast if all providers have open circuit breakers.
        self._check_ensemble_readiness(models, available_providers=available_providers)

        # Build a validated list of (provider, model) pairs and their coroutines.
        # Using an explicit list keeps the error-reporting loop index-aligned with
        # task_results, and avoids re-reading the original `models` argument after
        # it may have contained entries with missing fields.
        valid_models: List[Dict[str, str]] = []
        skipped_providers: List[str] = []
        coroutines = []

        for m in models:
            provider = m.get("provider")
            model = m.get("model")

            # Infer default provider if not specified
            if not provider:
                provider = getattr(self.config, 'llm_provider', 'openai') or 'openai'
                logger.info(
                    "[ENSEMBLE] Model configuration missing 'provider', using default: %s",
                    provider,
                )

            # Skip only if model is missing (provider is now guaranteed)
            if not model:
                logger.warning(
                    "[ENSEMBLE] Skipping model configuration with missing 'model' key: %s", m
                )
                continue

            # Skip providers that are not loaded/initialized to avoid hangs
            if provider not in available_providers:
                skipped_providers.append(provider)
                logger.warning(
                    "[ENSEMBLE] Skipping provider '%s' (model=%s): not loaded. "
                    "Available providers: %s",
                    provider, model, available_providers,
                )
                continue

            valid_models.append({"provider": provider, "model": model})
            coroutines.append(
                self._call_llm_with_provider_timeout(
                    prompt=prompt,
                    provider=provider,
                    model=model,
                    timeout=effective_timeout,
                    **kwargs,
                )
            )

        # Ensure at least one valid provider is available before proceeding
        if not coroutines:
            if skipped_providers:
                raise LLMError(
                    "No available providers for ensemble call; "
                    "skipped (not loaded): %s. Available providers: %s"
                    % (skipped_providers, available_providers)
                )
            raise LLMError("No valid model configurations found in ensemble API call")

        # Wrap each coroutine in an explicit asyncio.Task so they can be
        # individually cancelled if the total-ensemble timeout fires.
        task_objects: List[asyncio.Task] = [
            asyncio.create_task(coro) for coro in coroutines
        ]

        # Total ensemble timeout: prevents indefinite hangs when a provider's
        # per-task asyncio.wait_for fails to trigger (e.g. blocked in __init__).
        total_timeout = effective_timeout * ENSEMBLE_TOTAL_TIMEOUT_MULTIPLIER
        try:
            task_results = await asyncio.wait_for(
                asyncio.gather(*task_objects, return_exceptions=True),
                timeout=total_timeout,
            )
        except asyncio.TimeoutError:
            # Cancel every outstanding task and drain them to suppress
            # "Task was destroyed but it is pending!" warnings.
            for t in task_objects:
                t.cancel()
            await asyncio.gather(*task_objects, return_exceptions=True)
            logger.error(
                "[ENSEMBLE] Total ensemble timeout exceeded after %.0fs; "
                "attempted providers: %s",
                total_timeout,
                [m["provider"] for m in valid_models],
            )
            raise LLMError(
                "Ensemble timed out after %.0fs (total timeout exceeded)" % total_timeout
            )

        # Track which providers failed and why
        failed_providers = []
        for idx, result in enumerate(task_results):
            if isinstance(result, Dict):
                results.append(result)
            elif isinstance(result, Exception):
                # Get the provider/model info for this failed task
                provider = valid_models[idx].get("provider", "unknown")
                model = valid_models[idx].get("model", "unknown")
                error_msg = str(result)
                failed_providers.append("%s/%s: %s" % (provider, model, error_msg))
                logger.warning(
                    "[ENSEMBLE] Provider %s/%s failed: %s",
                    provider, model, result,
                    exc_info=result,
                )

        if not results:
            # Provide detailed error message listing all failed providers
            failure_details = "; ".join(failed_providers)
            error_message = (
                "All ensemble calls failed. Attempted %d provider(s): %s"
                % (len(valid_models), failure_details)
            )
            # Attempt fallback to single configured provider before giving up
            _fallback_provider = getattr(self.config, 'llm_provider', 'openai') or 'openai'
            _fallback_model = _PROVIDER_DEFAULT_MODELS.get(_fallback_provider, 'gpt-4o')
            if _fallback_provider in available_providers:
                logger.warning(
                    "[ENSEMBLE] %s Attempting fallback to single provider: %s",
                    error_message, _fallback_provider,
                )
                try:
                    _fb_result = await self.call_llm_api(
                        prompt=prompt, provider=_fallback_provider, model=_fallback_model, **kwargs
                    )
                    if isinstance(_fb_result, dict):
                        _fb_result = dict(_fb_result)
                        _fb_result["fallback_used"] = True
                        _fb_result["ensemble_failed"] = True
                        _fb_result["failed_providers"] = failed_providers
                        _fb_result["skipped_providers"] = skipped_providers
                        return _fb_result
                except Exception as _fb_err:
                    logger.error(
                        "[ENSEMBLE] Fallback to single provider %s also failed: %s",
                        _fallback_provider, _fb_err,
                    )
            logger.error("[ENSEMBLE] %s", error_message)
            raise LLMError(error_message)

        if voting_strategy == "majority":
            contents = [r["content"] for r in results if "content" in r]
            if not contents:
                raise LLMError("No content returned from successful ensemble calls")
            most_common = Counter(contents).most_common(1)
            return {
                "content": most_common[0][0],
                "ensemble_results": results,
                "skipped_providers": skipped_providers,
            }

        # results only contains Dict entries (guarded by isinstance above), so
        # spreading results[0] is always safe; we copy to avoid mutating cached data.
        first_result: Dict[str, Any] = dict(results[0])
        first_result["skipped_providers"] = skipped_providers
        return first_result

    def _check_ensemble_readiness(
        self,
        models: List[Dict[str, str]],
        available_providers: Optional[List[str]] = None,
    ) -> None:
        """Pre-flight check: fail fast if all configured providers have open circuit breakers.

        Logs a structured readiness summary before each ensemble call.

        Args:
            models: List of ``{"provider": ..., "model": ...}`` dicts.
            available_providers: Optional snapshot of loaded providers (from
                ``manager.list_providers()``). When ``None``, the list is fetched
                internally — useful for standalone callers.

        Raises:
            LLMError: If all available providers have open circuit breakers.
        """
        if available_providers is None:
            available_providers = self.manager.list_providers()
        healthy: List[str] = []
        open_breakers: List[str] = []
        for m in models:
            provider = m.get("provider") or getattr(self.config, 'llm_provider', 'openai') or 'openai'
            if provider not in available_providers:
                continue
            state = self.circuit_breaker.get_state(provider)
            if state == "OPEN":
                open_breakers.append(provider)
            else:
                healthy.append(provider)
        logger.info(
            "[ENSEMBLE] Provider readiness: healthy=%s, circuit-open=%s",
            healthy, open_breakers,
        )
        if open_breakers and not healthy:
            raise LLMError(
                "All ensemble providers have open circuit breakers; failing fast. "
                "Providers: %s" % open_breakers
            )

    async def health_check(self, provider: Optional[str] = None) -> bool:
        await self._is_initialized.wait()
        try:
            provider_name = provider or self.config.llm_provider
            plugin = self.manager.get_provider(provider_name)
            if not plugin:
                return False
            is_healthy = await plugin.health_check()
            metrics.LLM_PROVIDER_HEALTH.labels(provider=provider_name).set(
                1 if is_healthy else 0
            )
            return is_healthy
        except Exception as e:
            logger.error(
                f"Health check failed for provider {provider or 'unknown'}: {e}"
            )
            metrics.LLM_PROVIDER_HEALTH.labels(provider=provider or "unknown").set(0)
            return False

    async def close(self):
        await self.cache.aclose()
        await self.rate_limiter.aclose()
        for name, provider in self.manager.registry.items():
            if hasattr(provider, "close"):
                try:
                    await provider.aclose()
                except Exception as e:
                    logger.error(f"Error closing provider {name}: {e}", exc_info=True)


# --- Global API ---
# Note: Global singleton pattern with module-level lock for backward compatibility.
# For new code, prefer using the factory method: client = await LLMClient.create(config)
# This provides better dependency injection and testing capabilities.
_async_client: Optional[LLMClient] = None
# Lock is lazily created in the event loop to avoid initialization issues
_client_lock: Optional[asyncio.Lock] = None
_lock_loop_id: Optional[int] = None  # Track which event loop owns the lock


def _get_or_create_lock() -> asyncio.Lock:
    """
    Get or create the client lock for the current event loop.
    Creates a new lock if called from a different event loop.
    """
    global _client_lock, _lock_loop_id

    try:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)

        # Create new lock if none exists or if we're in a different event loop
        if _client_lock is None or _lock_loop_id != loop_id:
            _client_lock = asyncio.Lock()
            _lock_loop_id = loop_id
            logger.debug(f"Created new client lock for event loop {loop_id}")

        return _client_lock
    except RuntimeError:
        # No event loop - this shouldn't happen in async context
        logger.error("No running event loop in async function - this is a bug")
        # Return a fresh lock as fallback
        return asyncio.Lock()


async def call_llm_api(
    prompt: str,
    model: Optional[str] = None,
    stream: bool = False,
    provider: Optional[Literal["openai", "claude", "grok", "gemini", "local"]] = None,
    config: Optional[RunnerConfig] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    job_id: Optional[str] = None,  # Added for job-level budget tracking
    **kwargs,
) -> Dict[str, Any] | AsyncGenerator[str, None]:
    """
    Call LLM API with automatic config loading and graceful fallback.
    
    Args:
        prompt: The prompt to send to the LLM
        model: Optional model name
        stream: Whether to stream the response
        provider: Optional provider name
        config: Optional RunnerConfig. If None, will attempt to load from file with fallback to defaults.
        max_retries: Maximum number of retry attempts
        job_id: Optional job ID for tracking LLM call budget per job
        **kwargs: Additional arguments passed to the provider
    
    Returns:
        LLM response dictionary or async generator for streaming
        
    Note:
        In production environments, ensure runner_config.yaml exists or pass explicit config.
        Fallback defaults are suitable for development/testing only.
    """
    global _async_client
    lock = _get_or_create_lock()
    async with lock:
        if _async_client is None:
            if config is None:
                # Try to load config from file, fallback to minimal defaults if file doesn't exist
                try:
                    config = RunnerConfig.load()
                    logger.info("✅ Configuration loaded successfully")
                except (ConfigurationError, FileNotFoundError) as e:
                    # Check if we're in production - if so, log more seriously
                    is_production = os.getenv("PYTHON_ENV", "").lower() == "production"
                    
                    if is_production:
                        logger.error(
                            f"PRODUCTION WARNING: {e}. "
                            f"Using minimal fallback configuration. This may cause degraded functionality. "
                            f"Please ensure runner_config.yaml exists in production deployments."
                        )
                    else:
                        logger.info(
                            "✅ Using minimal fallback configuration (backend=docker, framework=pytest). "
                            "This is acceptable for development/testing. "
                            "For production, set RUNNER_CONFIG_PATH or place config in a standard location."
                        )
                    
                    # Create minimal config with required fields suitable for development
                    config = RunnerConfig(
                        backend="docker",
                        framework="pytest",
                        instance_id=f"fallback-{os.getpid()}"
                    )
            # Use direct instantiation for backward compatibility (lazy init happens on first call)
            _async_client = LLMClient(config)
    return await _async_client.call_llm_api(prompt, model, stream, provider, max_retries, job_id=job_id, **kwargs)


async def call_ensemble_api(
    prompt: str,
    models: List[Dict[str, str]],
    voting_strategy: str = "majority",
    config: Optional[RunnerConfig] = None,
    stream: bool = False,
    timeout_per_provider: Optional[float] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Call ensemble LLM API with automatic config loading and graceful fallback.
    
    Args:
        prompt: The prompt to send to the LLMs
        models: List of model configurations
        voting_strategy: Strategy for combining results
        config: Optional RunnerConfig. If None, will attempt to load from file with fallback to defaults.
        stream: Whether to stream the response (default: False)
        timeout_per_provider: Per-provider timeout in seconds. Defaults to ENSEMBLE_PROVIDER_TIMEOUT_SECONDS
            env var (default 180s). Pass an explicit value to override.
        **kwargs: Additional parameters to forward to call_llm_api
    
    Returns:
        Ensemble LLM response dictionary
        
    Note:
        In production environments, ensure runner_config.yaml exists or pass explicit config.
        Fallback defaults are suitable for development/testing only.
    """
    global _async_client
    lock = _get_or_create_lock()
    async with lock:
        if _async_client is None:
            if config is None:
                # Try to load config from file, fallback to minimal defaults if file doesn't exist
                try:
                    config = RunnerConfig.load()
                    logger.info("✅ Configuration loaded successfully")
                except (ConfigurationError, FileNotFoundError) as e:
                    # Check if we're in production - if so, log more seriously
                    is_production = os.getenv("PYTHON_ENV", "").lower() == "production"
                    
                    if is_production:
                        logger.error(
                            f"PRODUCTION WARNING: {e}. "
                            f"Using minimal fallback configuration. This may cause degraded functionality. "
                            f"Please ensure runner_config.yaml exists in production deployments."
                        )
                    else:
                        logger.info(
                            "✅ Using minimal fallback configuration (backend=docker, framework=pytest). "
                            "This is acceptable for development/testing. "
                            "For production, set RUNNER_CONFIG_PATH or place config in a standard location."
                        )
                    
                    # Create minimal config with required fields suitable for development
                    config = RunnerConfig(
                        backend="docker",
                        framework="pytest",
                        instance_id=f"fallback-{os.getpid()}"
                    )
            _async_client = LLMClient(config)
    
    # Call the ensemble API and provide additional context on failure
    try:
        return await _async_client.call_ensemble_api(
            prompt, models, voting_strategy,
            timeout_per_provider=timeout_per_provider,
            stream=stream,
            **kwargs,
        )
    except LLMError as e:
        # Add additional logging for module-level context
        model_list = ", ".join([f"{m.get('provider', 'unknown')}/{m.get('model', 'unknown')}" for m in models])
        logger.error(
            f"Module-level ensemble API call failed. Attempted models: [{model_list}]. "
            f"Check API key configuration and provider availability. Error: {e}"
        )
        raise


async def shutdown_llm_client():
    global _async_client
    if _async_client:
        await _async_client.aclose()
        _async_client = None


async def count_tokens(text: str, model: str = "gpt-4") -> int:
    """
    Count tokens in text for the given model.

    Module-level function for backward compatibility.
    Uses tiktoken if available, otherwise estimates based on word count.

    Args:
        text: Text to count tokens for
        model: Model name (for encoding selection)

    Returns:
        Estimated token count
    """
    if not HAS_TIKTOKEN:
        return int(len(text.split()) * 1.3)
    try:
        try:
            encoding = tiktoken.encoding_for_model(model)
        except (KeyError, ValueError):
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception as e:
        logger.warning(
            f"Failed to count tokens using tiktoken: {e}. Using word-based estimation."
        )
        return int(len(text.split()) * 1.3)


def reset_job_llm_budget(job_id: str) -> None:
    """
    Reset the LLM call counter for a specific job.
    
    Should be called when a job completes or is cancelled to prevent
    memory leaks in long-running processes.
    
    Args:
        job_id: The job ID to reset
    """
    global _async_client
    if _async_client and job_id in _async_client.job_call_counts:
        count = _async_client.job_call_counts.pop(job_id)
        logger.info(f"Reset LLM budget for job {job_id} (had {count} calls)")


def get_job_llm_stats(job_id: str) -> Dict[str, int]:
    """
    Get LLM call statistics for a specific job.
    
    Args:
        job_id: The job ID to query
        
    Returns:
        Dict with 'calls_made' and 'budget_remaining'
    """
    global _async_client
    if not _async_client:
        return {"calls_made": 0, "budget_remaining": DEFAULT_JOB_LLM_BUDGET}
    
    calls_made = _async_client.job_call_counts.get(job_id, 0)
    budget_remaining = max(0, _async_client.job_llm_budget - calls_made)
    
    return {
        "calls_made": calls_made,
        "budget_remaining": budget_remaining,
        "budget_total": _async_client.job_llm_budget
    }


# import atexit
# atexit.register(lambda: asyncio.run(shutdown_llm_client()))

__all__ = [
    "call_llm_api",
    "call_ensemble_api",
    "shutdown_llm_client",
    "count_tokens",
    "reset_job_llm_budget",
    "get_job_llm_stats",
]
