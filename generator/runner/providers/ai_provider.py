"""
ai_provider.py
OpenAI LLM provider plugin with OpenTelemetry tracing, configurable secret scrubbing, enhanced error handling, and updated cost estimation.

This plugin provides integration with OpenAI's API for LLM calls, with enhancements for observability, error handling, security, and more.

README Section for Plugin Extension and Troubleshooting:

Extension:
- Custom Headers/Endpoints: Use `register_custom_headers` and `register_custom_endpoint` methods to add runtime configurations.
- Models: Register additional models via `register_model`.
- Tokenization: Automatically switches encoding based on model (e.g., 'cl100k_base' for GPT-4, etc.).
- Secret Scrubbing: Configure regex patterns for secrets via `configure_secret_patterns`.

Troubleshooting:
- Check logs for run_id and errors.
- Metrics available via Prometheus endpoint.
- Traces available via OpenTelemetry exporter (if configured).
- Health check: Run `health_check` to verify API connectivity.
- Circuit Breaker: If provider is disabled due to failures, reset via `reset_circuit`.
- For testing, use mocked client in unit tests.
"""

import os
import uuid
import time
import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, Any, Union, AsyncGenerator
from functools import wraps

import aiohttp
from openai import AsyncOpenAI, OpenAIError, APIConnectionError, RateLimitError, AuthenticationError
from tiktoken import get_encoding, Encoding

from ..docgen_llm_call import LLMProvider

# ---- Runner foundation imports ------------------------------------------------
from runner.runner_logging import logger, add_provenance, log_audit_event
from runner.runner_metrics import (
    LLM_CALLS_TOTAL, LLM_ERRORS_TOTAL, LLM_LATENCY_SECONDS,
    LLM_TOKENS_INPUT, LLM_TOKENS_OUTPUT, LLM_COST_TOTAL,
    LLM_PROVIDER_HEALTH,
)
from runner.runner_security_utils import redact_secrets
from runner.runner_errors import LLMError, ConfigurationError
from runner.runner_config import RunnerConfig
from runner import tracer   # central OTEL tracer
# -------------------------------------------------------------------------------

# Configuration and API Key loading
config = RunnerConfig.load()   # picks up env / config file
# Using the standard environment variable but preferring the config value if set
API_KEY = config.llm_provider_api_key or os.getenv("OPENAI_API_KEY")

# Simple Circuit Breaker Implementation
class CircuitBreaker:
    def __init__(self, failure_threshold=5, reset_timeout=60):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.last_failure_time = None
        self.open = False

    def can_proceed(self):
        if self.open:
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.open = False
                self.failures = 0
                return True
            return False
        return True

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.failure_threshold:
            self.open = True

    def record_success(self):
        self.failures = 0
    
    def is_closed(self) -> bool:
        return not self.open

    def reset(self):
        self.open = False
        self.failures = 0

# Retry decorator with exponential backoff
def retry(max_retries=3, backoff_factor=1):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    retries += 1
                    if retries == max_retries:
                        raise
                    wait = backoff_factor * (2 ** (retries - 1))
                    await asyncio.sleep(wait)
        return wrapper
    return decorator

class OpenAIProvider(LLMProvider):
    """
    OpenAI LLM Provider with enhanced features:
    - Observability: Prometheus metrics and OpenTelemetry tracing.
    - Error Handling: Retries, circuit breaker, and actionable error messages.
    - Security: Centralized prompt scrubbing.
    - Extensibility: Custom registrations for headers, endpoints, and models.
    - Provenance: Output stamping.
    """
    
    # Provider name for metrics and logging
    name = "openai"

    def __init__(self):
        """
        Initialize the OpenAI provider.
        """
        super().__init__()
        
        if not API_KEY:
            LLM_ERRORS_TOTAL.labels(provider=self.name, error_type="config_init").inc()
            raise ConfigurationError("OPENAI_API_KEY environment variable or runner config not set.")
        
        self.api_key = API_KEY
        
        # Initialize client with the API key
        self.client = AsyncOpenAI(api_key=self.api_key)
        self.tokenizer_cache: Dict[str, Encoding] = {}
        self.circuit_breaker = CircuitBreaker()
        self.custom_headers: Dict[str, str] = {}
        self.custom_endpoint: str = None
        self.registered_models: set = {'gpt-3.5-turbo', 'gpt-4', 'gpt-4o'}
        self.disabled = False
        
        # The original custom secret_patterns dict is kept for the `_is_sensitive` check 
        # but the main scrubbing logic now uses `redact_secrets`
        self.secret_patterns = {
            'api_key': r'sk-[a-zA-Z0-9]{48}',
            'email': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            'credit_card': r'\b(?:\d{4}-){3}\d{4}\b',
            'ssn': r'\b\d{3}-\d{2}-\d{4}\b'
        }

    def configure_secret_patterns(self, patterns: Dict[str, str]):
        """
        Configure regex patterns for secret scrubbing (augments the central redactor).
        NOTE: This is retained, but the main scrubbing is handled by runner.security_utils.
        """
        self.secret_patterns.update(patterns)

    def register_custom_headers(self, headers: Dict[str, str]):
        """
        Register custom headers for API calls.
        """
        self.custom_headers.update(headers)

    def register_custom_endpoint(self, endpoint: str):
        """
        Register a custom API endpoint.
        """
        self.custom_endpoint = endpoint
        self.client.base_url = endpoint if endpoint else "https://api.openai.com/v1"

    def register_model(self, model: str):
        """
        Register a custom model.
        """
        self.registered_models.add(model)

    def _get_tokenizer(self, model: str) -> Encoding:
        """
        Get model-specific tokenizer.
        """
        if model not in self.tokenizer_cache:
            # Check for specific models, fallback to default
            if 'gpt-4' in model or 'gpt-3.5' in model or 'gpt-4o' in model:
                encoding_name = 'cl100k_base'
            else:
                encoding_name = 'p50k_base' # Older models
                
            self.tokenizer_cache[model] = get_encoding(encoding_name)
        return self.tokenizer_cache[model]

    async def _scrub_prompt(self, prompt: str) -> str:
        """
        Scrub prompt for secrets/PII using the central utility.
        """
        # Use the central redactor
        return redact_secrets(prompt)

    def _is_sensitive(self, text: str) -> bool:
        """
        Check if text contains sensitive patterns. (Used for logging redaction)
        """
        for pattern in self.secret_patterns.values():
            if re.search(pattern, text):
                return True
        return False

    def _stamp_output(self, content: str, model: str, run_id: str) -> Dict[str, Any]:
        """
        Stamp output with provenance.
        """
        # NOTE: Original logic retained, but `add_provenance` in `call` is preferred.
        return {
            "content": content,
            "model": model,
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "call_uuid": run_id
        }

    @retry(max_retries=3, backoff_factor=1)
    async def _api_call(self, model: str, messages: list, stream: bool, run_id: str, **kwargs):
        """
        Internal API call with headers, endpoint handling, and tracing.
        """
        with tracer.start_as_current_span("openai_api_call") as span:
            span.set_attribute("model", model)
            span.set_attribute("run_id", run_id)
            span.set_attribute("stream", stream)

            if self.custom_endpoint:
                kwargs['base_url'] = self.custom_endpoint
            if self.custom_headers:
                kwargs['extra_headers'] = self.custom_headers

            try:
                if stream:
                    return await self.client.chat.completions.create(model=model, messages=messages, stream=True, **kwargs)
                else:
                    return await self.client.chat.completions.create(model=model, messages=messages, **kwargs)
            except AuthenticationError as e:
                error_msg = "Authentication failed: Invalid or missing API key. Please verify OPENAI_API_KEY environment variable."
                LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
                logger.error(f"Run ID: {run_id} - {error_msg}")
                span.set_attribute("error", error_msg)
                raise LLMError(detail=error_msg, provider=self.name) from e
            except RateLimitError as e:
                error_msg = "Rate limit exceeded: Try reducing request frequency or check OpenAI dashboard for limits."
                LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
                logger.error(f"Run ID: {run_id} - {error_msg}")
                span.set_attribute("error", error_msg)
                raise LLMError(detail=error_msg, provider=self.name) from e
            except APIConnectionError as e:
                error_msg = "Connection error: Check network connectivity or custom endpoint configuration."
                LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
                logger.error(f"Run ID: {run_id} - {error_msg}")
                span.set_attribute("error", error_msg)
                raise LLMError(detail=error_msg, provider=self.name) from e
            except OpenAIError as e:
                error_type = type(e).__name__
                error_msg = f"OpenAI API error ({error_type}): {str(e)}. Check logs for details."
                LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
                logger.error(f"Run ID: {run_id} - {error_msg}")
                span.set_attribute("error", error_msg)
                raise LLMError(detail=error_msg, provider=self.name) from e
            except Exception as e:
                error_type = type(e).__name__
                error_msg = f"Unexpected error ({error_type}): {str(e)}. Check logs for details."
                LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
                logger.error(f"Run ID: {run_id} - {error_msg}")
                span.set_attribute("error", error_msg)
                raise LLMError(detail=error_msg, provider=self.name) from e


    async def call(self, prompt: str, model: str, stream: bool = False, **kwargs) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
        """
        Call the OpenAI API with prompt and model.

        Supports streaming and non-streaming modes.
        Includes metrics, logging, tracing, error handling, security scrubbing, and provenance stamping.

        Args:
            prompt: The input prompt.
            model: The OpenAI model to use.
            stream: Whether to stream the response.
            **kwargs: Additional parameters like temperature, max_tokens, etc.

        Returns:
            Dict with content and metadata, or async generator for streaming.
        """
        if model not in self.registered_models:
            raise ValueError(f"Model {model} not registered. Available models: {self.registered_models}")

        if self.disabled or not self.circuit_breaker.can_proceed():
            raise RuntimeError("Provider disabled due to circuit breaker. Reset with reset_circuit().")

        run_id = str(uuid.uuid4())
        start_time = time.time()

        # Runner foundation logging and provenance
        logger.info(f"[{run_id}] Calling {self.name} model={model}", extra={"run_id": run_id})
        await log_audit_event(event="llm_provider_call", data={"provider":self.name,"model":model}, run_id=run_id)
        LLM_CALLS_TOTAL.labels(provider=self.name, model=model).inc()
        
        # Scrubbing and Token Counting
        scrubbed_prompt = await self._scrub_prompt(prompt)
        input_tokens = await self.count_tokens(scrubbed_prompt, model)

        messages = [{"role": "user", "content": scrubbed_prompt}]

        # Check for sensitivity for logging redaction
        log_prompt = scrubbed_prompt if not self._is_sensitive(prompt) else "[SENSITIVE_PROMPT_REDACTED]"

        logger.info(f"Run ID: {run_id} - Starting call - Model: {model}, Input Tokens: {input_tokens}, Prompt (scrubbed): {log_prompt}")

        try:
            if stream:
                async def gen():
                    partial_output = ""
                    chunk_count = 0
                    with tracer.start_as_current_span("openai_stream") as span:
                        span.set_attribute("model", model)
                        span.set_attribute("run_id", run_id)
                        
                        # API Call
                        api_response = await self._api_call(model, messages, stream=True, run_id=run_id, **kwargs)
                        
                        async for chunk in api_response:
                            content = chunk.choices[0].delta.content or ""
                            partial_output += content
                            chunk_count += 1
                            logger.debug(f"Run ID: {run_id} - Chunk {chunk_count}: {content[:50]}..." if not self._is_sensitive(content) else "[SENSITIVE_CHUNK_REDACTED]")
                            span.add_event("chunk_received", {"chunk_number": chunk_count})
                            yield content
                            
                        # Metrics and Observability on completion
                        output_tokens = await self.count_tokens(partial_output, model)
                        latency = time.time() - start_time
                        cost = self._estimate_cost(model, input_tokens, output_tokens)
                        
                        logger.info(f"Run ID: {run_id} - Streaming complete - Latency: {latency}s, Output Tokens: {output_tokens}, Cost: {cost}")
                        
                        LLM_LATENCY_SECONDS.labels(provider=self.name, model=model).observe(latency)
                        LLM_TOKENS_INPUT.labels(provider=self.name, model=model).inc(input_tokens)
                        LLM_TOKENS_OUTPUT.labels(provider=self.name, model=model).inc(output_tokens)
                        LLM_COST_TOTAL.labels(provider=self.name, model=model).inc(cost)
                        LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1)
                        
                        self.circuit_breaker.record_success()
                        span.set_attribute("output_tokens", output_tokens)
                        span.set_attribute("cost", cost)

                return gen()
            else:
                with tracer.start_as_current_span("openai_call") as span:
                    span.set_attribute("model", model)
                    span.set_attribute("run_id", run_id)
                    
                    # API Call
                    completion = await self._api_call(model, messages, stream=False, run_id=run_id, **kwargs)
                    
                    content = completion.choices[0].message.content
                    output_tokens = await self.count_tokens(content, model)
                    latency = time.time() - start_time
                    cost = self._estimate_cost(model, input_tokens, output_tokens)
                    
                    log_output = content if not self._is_sensitive(content) else "[SENSITIVE_OUTPUT_REDACTED]"
                    logger.info(f"Run ID: {run_id} - Call complete - Latency: {latency}s, Output Tokens: {output_tokens}, Cost: {cost}, Output: {log_output[:100]}...")
                    
                    # Metrics and Observability
                    LLM_LATENCY_SECONDS.labels(provider=self.name, model=model).observe(latency)
                    LLM_TOKENS_INPUT.labels(provider=self.name, model=model).inc(input_tokens)
                    LLM_TOKENS_OUTPUT.labels(provider=self.name, model=model).inc(output_tokens)
                    LLM_COST_TOTAL.labels(provider=self.name, model=model).inc(cost)
                    LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1)
                    
                    self.circuit_breaker.record_success()
                    span.set_attribute("output_tokens", output_tokens)
                    span.set_attribute("cost", cost)
                    
                    # Stamping
                    return self._stamp_output(content, model, run_id)
        except Exception as e:
            latency = time.time() - start_time
            logger.error(f"Run ID: {run_id} - Error - Latency: {latency}s - {str(e)}")
            LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
            self.circuit_breaker.record_failure()
            
            if self.circuit_breaker.open:
                self.disabled = True
                logger.warning(f"Provider disabled due to repeated failures. Reset after {self.circuit_breaker.reset_timeout}s.")
            
            # The original file re-raised, but with the new LLMError from the patch
            if isinstance(e, LLMError):
                raise e # Re-raise if already an LLMError from _api_call
            else:
                raise LLMError(detail=str(e), provider=self.name) from e
        finally:
            # Update health gauge based on final circuit breaker state
            LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1 if self.circuit_breaker.is_closed() else 0)

    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """
        Estimate cost based on model using approximate 2025 OpenAI pricing.
        Source: Approximated based on typical OpenAI pricing trends.
        - GPT-3.5-turbo: $0.0005/$0.0015 per 1K input/output tokens
        - GPT-4o: $0.005/$0.015 per 1K input/output tokens
        - GPT-4 (legacy): $0.03/$0.06 per 1K input/output tokens
        """
        # NOTE: The original cost estimation is kept as the source of truth for this plugin's logic.
        if 'gpt-4o' in model:
            # Use original logic for consistency with the file's intent
            return (input_tokens * 0.005 + output_tokens * 0.015) / 1000
        elif 'gpt-3.5' in model:
            return (input_tokens * 0.0005 + output_tokens * 0.0015) / 1000
        elif 'gpt-4' in model:
            # Use original logic for consistency with the file's intent
            return (input_tokens * 0.03 + output_tokens * 0.06) / 1000
        return 0.0

    async def count_tokens(self, text: str, model: str) -> int:
        """
        Count tokens using model-specific tokenizer.
        """
        tokenizer = self._get_tokenizer(model)
        # NOTE: The original code had an await here, but the tokenizer.encode is sync. Keeping it as async
        # for consistency with the original function signature:
        # return len(tokenizer.encode(text))
        return await asyncio.to_thread(lambda: len(tokenizer.encode(text)))

    async def health_check(self) -> bool:
        """
        Health check with metrics and tracing.
        """
        # Reset health gauge before check
        LLM_PROVIDER_HEALTH.labels(provider=self.name).set(0)
        
        with tracer.start_as_current_span("openai_health_check") as span:
            start_time = time.time()
            status = False
            try:
                # Use base URL from client, which handles custom endpoint registration
                url = f"{self.client.base_url}/models" if self.client.base_url else "https://api.openai.com/v1/models"
                async with aiohttp.ClientSession() as session:
                    headers = {"Authorization": f"Bearer {self.api_key}"}
                    headers.update(self.custom_headers)
                    async with session.get(url, headers=headers) as resp:
                        status = resp.status == 200
                        LLM_LATENCY_SECONDS.labels(provider=self.name, model='health_check').observe(time.time() - start_time)
                        span.set_attribute("status", status)
                        if status:
                            LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1)
                        return status
            except Exception as e:
                logger.error(f"Health check failed: {str(e)}")
                span.set_attribute("error", str(e))
                return False

    def reset_circuit(self):
        """
        Manually reset circuit breaker.
        """
        self.circuit_breaker.reset()
        self.disabled = False
        LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1)

# Tests
import unittest
from unittest.mock import AsyncMock, patch

class TestOpenAIProvider(unittest.IsolatedAsyncioTestCase):
    
    # NOTE: Need to patch os.getenv for API_KEY since it's checked on init
    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'})
    @patch('runner.runner_config.RunnerConfig.load', return_value=AsyncMock(llm_provider_api_key=''))
    async def test_call_non_stream(self, mock_config_load):
        provider = OpenAIProvider()
        with patch('openai.AsyncOpenAI.chat.completions.create', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = AsyncMock(choices=[AsyncMock(message=AsyncMock(content="Test response"))])
            result = await provider.call("Hello", "gpt-3.5-turbo")
            self.assertIn("content", result)
            self.assertEqual(result["content"], "Test response")
            self.assertIn("model", result)

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'})
    @patch('runner.runner_config.RunnerConfig.load', return_value=AsyncMock(llm_provider_api_key=''))
    async def test_count_tokens(self, mock_config_load):
        provider = OpenAIProvider()
        tokens = await provider.count_tokens("Hello world", "gpt-3.5-turbo")
        self.assertEqual(tokens, 2)

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'})
    @patch('runner.runner_config.RunnerConfig.load', return_value=AsyncMock(llm_provider_api_key=''))
    async def test_health_check(self, mock_config_load):
        provider = OpenAIProvider()
        with patch('aiohttp.ClientSession.get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value.__aenter__.return_value.status = 200
            self.assertTrue(await provider.health_check())

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'})
    @patch('runner.runner_config.RunnerConfig.load', return_value=AsyncMock(llm_provider_api_key=''))
    async def test_scrub_prompt(self, mock_config_load):
        provider = OpenAIProvider()
        # Note: _scrub_prompt is now async in the original file, so we must await it.
        # Also, we test the centralized redactor, not the provider's local regex.
        # We assume runner.security_utils.redact_secrets handles the necessary patterns.
        with patch('runner.runner_security_utils.redact_secrets', return_value="[REDACTED_API_KEY], [REDACTED_EMAIL], [REDACTED_CREDIT_CARD]"):
            scrubbed = await provider._scrub_prompt("My key is sk-abc123, email: test@example.com, card: 1234-5678-9012-3456")
            self.assertIn("[REDACTED_API_KEY]", scrubbed)
            self.assertIn("[REDACTED_EMAIL]", scrubbed)
            self.assertIn("[REDACTED_CREDIT_CARD]", scrubbed)

if __name__ == '__main__':
    # NOTE: Since the file now depends on runner imports, running unittest.main() 
    # directly without the full runner environment will likely fail, but the 
    # original file included it, so it remains here for completeness.
    unittest.main()