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
import runner.runner_metrics as metrics
from dotenv import load_dotenv
from runner.llm_plugin_manager import LLMPluginManager

# Runner Foundation
from runner.runner_config import RunnerConfig
from runner.runner_errors import ConfigurationError, LLMError

# [FIX] Import log_audit_event instead of add_provenance
from runner.runner_logging import log_audit_event, logger

# FIX: Import only redact_secrets
from runner.runner_security_utils import redact_secrets

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
        secret = os.environ.get(secret_name)
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
class CacheManager:
    def __init__(self, redis_url: Optional[str] = None):
        self.redis = None
        if redis_url:
            try:
                self.redis = aioredis.from_url(redis_url)
                logger.info(f"CacheManager: Connected to Redis at {redis_url}")
            except Exception as e:
                logger.error(
                    f"CacheManager: Failed to connect to Redis at {redis_url}. Falling back to in-memory. Error: {e}"
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
            await self.redis.close()


# --- Rate Limiter ---
class DistributedRateLimiter:
    def __init__(
        self, redis_url: Optional[str] = None, limit: int = 100, window: int = 60
    ):
        self.redis = aioredis.from_url(redis_url) if redis_url else None
        self.limit = limit
        self.window = window

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
            await self.redis.close()


# --- Circuit Breaker ---
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count: Dict[str, int] = {}
        self.last_failure: Dict[str, float] = {}
        self.state: Dict[str, str] = {}

    def record_failure(self, provider: str):
        self.failure_count[provider] = self.failure_count.get(provider, 0) + 1
        self.last_failure[provider] = time.time()
        if self.failure_count[provider] >= self.failure_threshold:
            if self.state.get(provider, "CLOSED") != "OPEN":
                logger.warning(
                    f"CircuitBreaker: Tripped to OPEN for provider {provider}"
                )
            self.state[provider] = "OPEN"
            metrics.LLM_CIRCUIT_STATE.labels(provider=provider).set(1)

    def record_success(self, provider: str):
        if self.state.get(provider) == "HALF-OPEN":
            logger.info(f"CircuitBreaker: Reset to CLOSED for provider {provider}")
        self.failure_count[provider] = 0
        self.state[provider] = "CLOSED"
        # FIX: Corrected indentation for metric update
        metrics.LLM_CIRCUIT_STATE.labels(provider=provider).set(0)

    async def allow_request(self, provider: str) -> bool:
        if self.state.get(provider, "CLOSED") == "CLOSED":
            return True
        if time.time() - self.last_failure.get(provider, 0) > self.timeout:
            self.state[provider] = "HALF-OPEN"
            metrics.LLM_CIRCUIT_STATE.labels(provider=provider).set(0.5)
            logger.info(
                f"CircuitBreaker: State for {provider} is now HALF-OPEN. Allowing trial request."
            )
            return True
        return False


# --- Unified LLM Client ---
class LLMClient:
    def __init__(self, config: RunnerConfig):
        self.config = config
        self.manager = LLMPluginManager(plugin_dir=Path(__file__).parent)
        self.secrets = SecretsManager()
        self.cache = CacheManager(config.redis_url)
        self.rate_limiter = DistributedRateLimiter(config.redis_url)
        self.circuit_breaker = CircuitBreaker()
        self._is_initialized = asyncio.Event()
        # This starts the initialization process immediately in the background
        asyncio.create_task(self._initialize())

    async def _initialize(self):
        # NOTE: self.manager._load_task is awaited here.
        # If the test mocks self.manager, it must ensure _load_task is awaitable (e.g., an AsyncMock).
        if hasattr(self.manager, "_load_task"):
            await self.manager._load_task
        else:
            logger.warning(
                "LLMPluginManager does not have _load_task attribute. Skipping initialization wait."
            )

        for name in self.manager.list_providers():
            metrics.LLM_PROVIDER_HEALTH.labels(provider=name).set(1)
        self._is_initialized.set()

    async def count_tokens(self, text: str, model: str) -> int:
        if not HAS_TIKTOKEN:
            return int(len(text.split()) * 1.3)
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except:
            return int(len(text.split()) * 1.3)

    async def call_llm_api(
        self,
        prompt: str,
        model: Optional[str] = None,
        stream: bool = False,
        provider: Optional[
            Literal["openai", "claude", "grok", "gemini", "local"]
        ] = None,
        **kwargs,
    ) -> Dict[str, Any] | AsyncGenerator[str, None]:
        await self._is_initialized.wait()
        provider = provider or self.config.llm_provider or "openai"
        # FIX: Ensure default_llm_model exists on the mock object (or provide a safe fallback)
        model = model or getattr(self.config, "default_llm_model", "gpt-4")

        # [FIX] Redact secrets from the prompt *before* it's used in cache keys or logs
        # [FIX] redact_secrets is now synchronous, remove await
        prompt = redact_secrets(prompt)
        start_time = time.time()

        if not await self.rate_limiter.acquire(provider):
            metrics.LLM_ERRORS_TOTAL.labels(provider=provider, model=model).inc()
            raise LLMError("Rate limit exceeded")

        if not await self.circuit_breaker.allow_request(provider):
            metrics.LLM_ERRORS_TOTAL.labels(provider=provider, model=model).inc()
            raise LLMError("Circuit breaker open")

        cache_key = hashlib.sha256(f"{prompt}:{model}:{provider}".encode()).hexdigest()
        cached = await self.cache.get(cache_key)
        if cached and not stream:
            metrics.LLM_CALLS_TOTAL.labels(provider=provider, model=model).inc()
            return cached

        try:
            plugin = self.manager.get_provider(provider)
            # [FIX] Graceful degradation if provider plugin failed to load (e.g., missing SDK/Key)
            if not plugin:
                metrics.LLM_ERRORS_TOTAL.labels(provider=provider, model=model).inc()
                self.circuit_breaker.record_failure(provider)
                raise ConfigurationError(
                    f"LLM provider '{provider}' not loaded",
                    detail="SDK or API key may be missing",
                )

            response = await plugin.call(
                prompt=prompt, model=model, stream=stream, **kwargs
            )
            latency = time.time() - start_time
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

        except Exception as e:
            metrics.LLM_ERRORS_TOTAL.labels(provider=provider, model=model).inc()
            self.circuit_breaker.record_failure(provider)
            # Don't re-raise as LLMError if it's already a ConfigurationError
            if isinstance(e, (LLMError, ConfigurationError)):
                raise
            raise LLMError(f"LLM call failed: {e}") from e
        # [FIX] Removed 'finally' block that incorrectly recorded success

    async def call_ensemble_api(
        self,
        prompt: str,
        models: List[Dict[str, str]],  # List of {provider, model}
        voting_strategy: str = "majority",
        **kwargs,
    ) -> Dict[str, Any]:
        await self._is_initialized.wait()
        results = []
        tasks = []

        # Create tasks for all models
        for m in models:
            tasks.append(
                self.call_llm_api(
                    prompt, model=m["model"], provider=m["provider"], **kwargs
                )
            )

        # Run all calls in parallel
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in task_results:
            if isinstance(result, Dict):
                results.append(result)
            elif isinstance(result, Exception):
                logger.warning(
                    f"Ensemble call failed for one provider: {result}", exc_info=result
                )

        if not results:
            raise LLMError("All ensemble calls failed")

        if voting_strategy == "majority":
            contents = [r["content"] for r in results if "content" in r]
            if not contents:
                raise LLMError("No content returned from successful ensemble calls")
            most_common = Counter(contents).most_common(1)
            return {"content": most_common[0][0], "ensemble_results": results}

        return results[0]  # First valid

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
        except:
            metrics.LLM_PROVIDER_HEALTH.labels(provider=provider or "unknown").set(0)
            return False

    async def close(self):
        await self.cache.close()
        await self.rate_limiter.close()
        for name, provider in self.manager.registry.items():
            if hasattr(provider, "close"):
                try:
                    await provider.close()
                except:
                    logger.error(f"Error closing {name}")


# --- Global API ---
_async_client: Optional[LLMClient] = None
_client_lock = asyncio.Lock()


async def call_llm_api(
    prompt: str,
    model: Optional[str] = None,
    stream: bool = False,
    provider: Optional[Literal["openai", "claude", "grok", "gemini", "local"]] = None,
    config: Optional[RunnerConfig] = None,
) -> Dict[str, Any] | AsyncGenerator[str, None]:
    global _async_client
    async with _client_lock:
        if _async_client is None:
            config = config or RunnerConfig.load()
            _async_client = LLMClient(config)
    return await _async_client.call_llm_api(prompt, model, stream, provider)


async def call_ensemble_api(
    prompt: str,
    models: List[Dict[str, str]],
    voting_strategy: str = "majority",
    config: Optional[RunnerConfig] = None,
) -> Dict[str, Any]:
    global _async_client
    async with _client_lock:
        if _async_client is None:
            config = config or RunnerConfig.load()
            _async_client = LLMClient(config)
    return await _async_client.call_ensemble_api(prompt, models, voting_strategy)


async def shutdown_llm_client():
    global _async_client
    if _async_client:
        await _async_client.close()
        _async_client = None


# import atexit
# atexit.register(lambda: asyncio.run(shutdown_llm_client()))

__all__ = ["call_llm_api", "call_ensemble_api", "shutdown_llm_client"]
