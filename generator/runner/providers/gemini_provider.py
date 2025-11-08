# runner/llm_client_providers/gemini_provider.py
"""
gemini_provider.py
Google Gemini LLM provider plugin with GOAT-level upgrades for observability, reliability, security, extensibility, and more.
"""

import os
import logging
import uuid
import time
import re
import json
import yaml
from typing import Union, Dict, Any, AsyncGenerator, Callable, List, Optional
import aiohttp

# --- Conditional SDK Import ---
try:
    from google.generativeai import GenerativeModel, configure
    from google.generativeai.types import GenerateContentResponse
    import google.generativeai.types.generation_types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False
    # Define dummy types for the class to load without crashing
    class GenerateContentResponse: pass

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ---- Runner foundation imports ------------------------------------------------
# [FIX] Corrected imports
from runner.runner_logging import logger, log_audit_event
from runner.runner_metrics import (
    LLM_CALLS_TOTAL, LLM_ERRORS_TOTAL, LLM_LATENCY_SECONDS,
    LLM_TOKENS_INPUT, LLM_TOKENS_OUTPUT, LLM_COST_TOTAL,
    LLM_PROVIDER_HEALTH,
)
from runner.runner_security_utils import redact_secrets
from runner.runner_errors import LLMError, ConfigurationError
from runner.runner_config import RunnerConfig, load_config # [FIX] Import load_config

# [FIX] Guarded tracer import
try:
    from runner import tracer   # central OTEL tracer
    if tracer is None: raise ImportError("Tracer is None")
except ImportError:
    logger.warning("Tracer not found, using OTel default.")
    from opentelemetry import trace
    tracer = trace.get_tracer(__name__)

# [FIX] Corrected import path for base class
try:
    from runner.llm_client.llm_provider_base import LLMProvider
except ImportError:
    try:
        from ..docgen_llm_call import LLMProvider
    except ImportError:
        logger.critical("Failed to import LLMProvider base class. Shutting down.")
        # Define a dummy base class to allow the file to be parsed
        class LLMProvider:
            name: str = "dummy"
            def __init__(self): pass
            async def call(self, *args, **kwargs): raise NotImplementedError()
            async def health_check(self, *args, **kwargs): return False
# -------------------------------------------------------------------------------

# Configuration and API Key loading
config = load_config() # [FIX] Use load_config()
API_KEY = config.llm_provider_api_key or os.getenv("GEMINI_API_KEY")

# [REMOVED] Global configure(api_key=API_KEY) - Moved to __init__

# Metrics initialization (Prometheus) - Retain local metrics for stream chunks
from prometheus_client import Counter, Histogram

stream_chunks_total = Counter('llm_stream_chunks_total', 'Total number of stream chunks', ['model'])
stream_chunk_latency = Histogram('llm_stream_chunk_latency_seconds', 'Latency per stream chunk in seconds', ['model'])

# Simple Circuit Breaker implementation
class CircuitBreaker:
    """
    A simple circuit breaker to prevent calls during repeated failures.
    """
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time: float = 0.0
        self.is_open = False

    def can_proceed(self) -> bool:
        if self.is_open:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.is_open = False
                self.failures = 0
                logger.info("Circuit breaker recovered and closed.")
                return True
            logger.warning("Circuit breaker is open.")
            return False
        return True

    def is_closed(self) -> bool:
        return not self.is_open
    
    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.failure_threshold:
            self.is_open = True
            logger.error(f"Circuit breaker opened after {self.failures} failures.")

    def record_success(self):
        if self.is_open:
            logger.info("Circuit breaker closed after success.")
        self.failures = 0
        self.is_open = False

# Security: Regex patterns for scrubbing secrets/PII (Used only for local log redaction now)
SECRET_PATTERNS = [
    r'(?i)(api[-_]?key|secret|token)\s*[:=]\s*["\']?[a-zA-Z0-9_\-]{20,}["\']?',  # API keys, secrets, tokens
    r'(?i)password\s*[:=]\s*["\']?.+?["\']?',  # Passwords
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # Emails
    r'\b(?:\d{3}-?\d{2}-?\d{4})\b',  # SSN-like
    r'\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|6(?:011|5[0-9]{2})[0-9]{12}|(?:2131|1800|35[0-9]{3})[0-9]{11})\b',  # Credit cards
]

def scrub_text(text: str) -> str:
    """
    Scrub sensitive information from text using regex patterns.
    (Retained for the local log redaction helper)
    """
    for pattern in SECRET_PATTERNS:
        text = re.sub(pattern, '[REDACTED]', text)
    return text

# Cost awareness: Pricing per model (USD per token). Updated as of July 2025.
PRICING = {
    'gemini-2.5-pro': {'input': 1.25e-6, 'output': 10e-6},  # Base rates; actual may vary by prompt size
    'gemini-2.5-flash': {'input': 0.3e-6, 'output': 2.5e-6},
    'gemini-2.5-flash-lite': {'input': 0.1e-6, 'output': 0.4e-6},
    # Add more models as needed; for thresholds, use base for estimation
}

def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Calculate cost based on token counts and model pricing.
    Note: This is an estimation; actual costs may vary based on prompt size thresholds.
    """
    if model in PRICING:
        return PRICING[model]['input'] * input_tokens + PRICING[model]['output'] * output_tokens
    logger.warning(f"No pricing info for model {model}. Cost set to 0.")
    return 0.0

class GeminiProvider(LLMProvider):
    """
    Enhanced LLMProvider for Google Gemini with observability, reliability, security, and extensibility features.
    """
    name = "gemini"

    def __init__(self):
        """
        Initialize the Gemini provider with API key validation and initial setup.
        """
        super().__init__()
        # Use centrally loaded key, falling back to os.getenv
        self.api_key = API_KEY or os.getenv('GEMINI_API_KEY')
        
        # [FIX] SDK/key guards
        if not HAS_GEMINI or not self.api_key:
            LLM_ERRORS_TOTAL.labels(provider=self.name, model="gemini-config").inc()
            raise ConfigurationError("Gemini provider configured but SDK (google-generativeai) or GEMINI_API_KEY is missing.")

        try:
            # [FIX] Configure the SDK *after* checking the key
            configure(api_key=self.api_key) # Use the centrally loaded/preferred key
        except Exception as e:
            LLM_ERRORS_TOTAL.labels(provider=self.name, model="gemini-config").inc()
            raise ConfigurationError(f"Failed to configure Gemini SDK: {e}")

        self.circuit_breaker = CircuitBreaker()
        self.custom_models: Dict[str, str] = {}  # model_name: gemini_model_name (for custom aliases)
        self.pre_hooks: List[Callable[[str], str]] = []
        self.post_hooks: List[Callable[[Any], Any]] = []
        LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1)
        self.load_plugins()  # Initial load

    def load_config(self, file_path: str):
        """
        Load external configuration for model aliases from YAML or JSON file.
        """
        if file_path.endswith('.yaml') or file_path.endswith('.yml'):
            with open(file_path, 'r') as f:
                config = yaml.safe_load(f)
        elif file_path.endswith('.json'):
            with open(file_path, 'r') as f:
                config = json.load(f)
        else:
            raise ValueError("Unsupported config format. Use YAML or JSON.")
        for alias, gemini_model in config.get('models', {}).items():
            self.register_custom_model(alias, gemini_model)

    def register_custom_model(self, alias: str, gemini_model: str):
        """
        Register a custom model alias mapping to a Gemini model name.
        """
        self.custom_models[alias] = gemini_model
        logger.info(f"Registered custom model alias: {alias} -> {gemini_model}")

    def add_pre_hook(self, hook: Callable[[str], str]):
        """
        Add a pre-processing hook for prompts (e.g., for additional transformations).
        """
        self.pre_hooks.append(hook)

    def add_post_hook(self, hook: Callable[[Any], Any]):
        """
        Add a post-processing hook for responses (e.g., for formatting or filtering).
        """
        self.post_hooks.append(hook)

    def load_plugins(self):
        """
        Auto-discover and hot-reload plugins/extensions. (Placeholder: Implement directory scan for .py files.)
        """
        # Example: Scan 'plugins/' directory and load modules dynamically using importlib.
        # For hot-reload, use watchdogs or periodic checks in a separate thread.
        logger.info("Plugins loaded (placeholder implementation).")

    @retry(retry=retry_if_exception_type(Exception), stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def _api_call(self, client: GenerativeModel, scrubbed_prompt: str, stream: bool, run_id: str):
        """
        Internal API call with retry and tracing.
        """
        with tracer.start_as_current_span("gemini_api_call") as span:
            span.set_attribute("model", client.model_name)
            span.set_attribute("run_id", run_id)
            span.set_attribute("stream", stream)
            try:
                if stream:
                    return await client.generate_content_async(scrubbed_prompt, stream=True)
                else:
                    return await client.generate_content_async(scrubbed_prompt)
            except ValueError as e:
                error_msg = f"Invalid request: {str(e)}. Check prompt format or model capabilities."
                logger.error(error_msg, extra={'run_id': run_id})
                span.set_attribute("error", error_msg)
                raise LLMError(detail=error_msg, provider=self.name) from e
            except Exception as e:  # Catch broader for retry
                error_type = type(e).__name__
                # Check for specific Gemini/Google API errors if available
                if "API_KEY" in str(e):
                    error_msg = "API error: Invalid API Key."
                else:
                    error_msg = f"API error ({error_type}): {str(e)}. Check API key, network, or service status."
                logger.error(error_msg, extra={'run_id': run_id})
                span.set_attribute("error", error_msg)
                raise LLMError(detail=error_msg, provider=self.name) from e
    
    async def _scrub_prompt(self, prompt: str) -> str:
        """
        Scrub prompt using the central security utility.
        """
        # [FIX] Use central redactor
        return await redact_secrets(prompt)

    async def call(self, prompt: str, model: str, stream: bool = False, **kwargs) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
        """
        Call the LLM with reliability, security, and observability features.
        """
        if not self.circuit_breaker.can_proceed():
            LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
            raise RuntimeError("CircuitOpenError: Provider disabled due to failures. Try later or reset circuit.")

        run_id = str(uuid.uuid4())
        timestamp = time.time()
        log_extra = {'run_id': run_id, 'model': model, 'stream': stream, 'provenance': self.name}
        
        logger.info(f"[{run_id}] Calling {self.name} model={model}", extra={"run_id": run_id})
        # [REMOVED] add_provenance call - moved to after the call

        start_time = time.time()
        
        # [FIX] Redact before call
        scrubbed_prompt = await self._scrub_prompt(prompt)
        
        for hook in self.pre_hooks:
            scrubbed_prompt = hook(scrubbed_prompt)

        gemini_model = self.custom_models.get(model, model)
        client = GenerativeModel(gemini_model)
        
        input_tokens = await self.count_tokens(scrubbed_prompt, gemini_model)
        LLM_TOKENS_INPUT.labels(provider=self.name, model=model).inc(input_tokens)
        LLM_CALLS_TOTAL.labels(provider=self.name, model=model).inc() # [FIX] Use .inc()

        try:
            response = await self._api_call(client, scrubbed_prompt, stream, run_id)

            self.circuit_breaker.record_success()
            LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1)

            if stream:
                async def gen():
                    partial_response = ""
                    chunk_start = time.time()
                    output_tokens = 0
                    with tracer.start_as_current_span("gemini_stream") as span:
                        span.set_attribute("model", model)
                        span.set_attribute("run_id", run_id)
                        try:
                            async for chunk in response:
                                chunk_text = chunk.text
                                yield chunk_text
                                partial_response += chunk_text
                                # [FIX] Await token count
                                chunk_output_tokens = await self.count_tokens(chunk_text, model)
                                output_tokens += chunk_output_tokens
                                chunk_latency = time.time() - chunk_start
                                stream_chunk_latency.labels(model=model).observe(chunk_latency)
                                stream_chunks_total.labels(model=model).inc()
                                logger.debug("Stream chunk", extra={**log_extra, 'chunk_size': len(chunk_text), 'latency': chunk_latency, 'preview': self._redact_log(chunk_text)})
                                span.add_event("chunk_received", {"latency": chunk_latency})
                                chunk_start = time.time()
                        except Exception as e:
                            logger.error("Stream error", extra={**log_extra, 'error': str(e)})
                            LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
                            self.circuit_breaker.record_failure()
                            raise LLMError(detail=str(e), provider=self.name) from e
                        finally:
                            # Finalize
                            LLM_TOKENS_OUTPUT.labels(provider=self.name, model=model).inc(output_tokens)
                            cost = calculate_cost(model, input_tokens, output_tokens)
                            LLM_COST_TOTAL.labels(provider=self.name, model=model).inc(cost)
                            total_latency = time.time() - start_time
                            LLM_LATENCY_SECONDS.labels(provider=self.name, model=model).observe(total_latency)
                            logger.info("Stream completed", extra={**log_extra, 'output_tokens': output_tokens, 'cost': cost, 'latency': total_latency})
                            stamped_partial = {'partial_content': partial_response, 'model': model, 'version': '1.0', 'run_id': run_id, 'timestamp': timestamp}
                            for hook in self.post_hooks:
                                stamped_partial = hook(stamped_partial)
                            span.set_attribute("output_tokens", output_tokens)
                            span.set_attribute("cost", cost)
                            # [FIX] Audit after call (stream)
                            await log_audit_event(
                                action="llm_provider_call",
                                data={"provider": self.name, "model": model, "run_id": run_id, "stream": True, "input_tokens": input_tokens, "output_tokens": output_tokens}
                            )

                return gen()
            else:
                with tracer.start_as_current_span("gemini_call") as span:
                    span.set_attribute("model", model)
                    span.set_attribute("run_id", run_id)
                    content = response.text
                    output_tokens = await self.count_tokens(content, model) # [FIX] Await token count
                    
                    LLM_TOKENS_OUTPUT.labels(provider=self.name, model=model).inc(output_tokens)
                    cost = calculate_cost(model, input_tokens, output_tokens)
                    LLM_COST_TOTAL.labels(provider=self.name, model=model).inc(cost)
                    total_latency = time.time() - start_time
                    LLM_LATENCY_SECONDS.labels(provider=self.name, model=model).observe(total_latency)
                    
                    # [FIX] Audit after call (non-stream)
                    await log_audit_event(
                        action="llm_provider_call",
                        data={"provider": self.name, "model": model, "run_id": run_id, "stream": False, "input_tokens": input_tokens, "output_tokens": output_tokens}
                    )
                    
                    logger.info("Call completed", extra={**log_extra, 'output_tokens': output_tokens, 'cost': cost, 'latency': total_latency, 'response_preview': self._redact_log(content)})
                    stamped_response = {"content": content, "model": model, "version": '1.0', "run_id": run_id, "timestamp": timestamp}
                    for hook in self.post_hooks:
                        stamped_response = hook(stamped_response)
                    span.set_attribute("output_tokens", output_tokens)
                    span.set_attribute("cost", cost)
                    return stamped_response
        except Exception as e:
            self.circuit_breaker.record_failure()
            LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
            logger.error("Call error", extra={**log_extra, 'error': str(e)})
            
            # Re-raise as LLMError if it's not already one from _api_call
            if isinstance(e, LLMError):
                raise
            else:
                raise LLMError(detail=str(e), provider=self.name) from e
        finally:
            # Update health gauge based on final circuit breaker state
            LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1 if self.circuit_breaker.is_closed() else 0)


    def _redact_log(self, content: str) -> str:
        """
        Redact sensitive content for logging. (Uses local scrub_text utility)
        """
        return scrub_text(content)[:100] + '...' if len(content) > 100 else scrub_text(content)

    async def count_tokens(self, text: str, model: str) -> int:
        """
        Count tokens using Gemini's API asynchronously.
        """
        if not HAS_GEMINI:
             logger.warning("Gemini SDK not found. Using approximation for token count.")
             return len(text) // 4 + 1
        try:
            client = GenerativeModel(model)
            response = await client.count_tokens_async(text)
            return response.total_tokens
        except Exception as e:
            # Fallback approximation if API fails
            logger.warning(f"Token count API failed: {str(e)}. Using approximation.")
            return len(text) // 4 + 1  # Rough estimate for English text

    async def health_check(self) -> bool:
        """
        Perform health check and update metrics/logs.
        """
        run_id = str(uuid.uuid4())
        log_extra = {'run_id': run_id, 'provenance': 'health_check'}
        logger.info("Health check started", extra=log_extra)
        
        # [FIX] Add guard for API key and SDK
        if not self.api_key or not HAS_GEMINI:
             logger.error("Gemini health check failed: API key or SDK is missing.", extra=log_extra)
             LLM_PROVIDER_HEALTH.labels(provider=self.name).set(0)
             return False
        
        # Reset health gauge before check
        LLM_PROVIDER_HEALTH.labels(provider=self.name).set(0)
        
        with tracer.start_as_current_span("gemini_health_check") as span:
            is_healthy = False
            try:
                async with aiohttp.ClientSession() as session:
                    # Using the standard models endpoint
                    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self.api_key}"
                    async with session.get(url) as resp:
                        is_healthy = resp.status == 200
                        LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1 if is_healthy else 0)
                        logger.info("Health check result", extra={**log_extra, 'healthy': is_healthy})
                        span.set_attribute("status", is_healthy)
                        return is_healthy
            except Exception as e:
                LLM_PROVIDER_HEALTH.labels(provider=self.name).set(0)
                error_msg = f"Health check error: {str(e)}. Check API key or network."
                logger.error(error_msg, extra=log_extra)
                span.set_attribute("error", error_msg)
                return False

    def get_circuit_status(self) -> Dict[str, Any]:
        """
        Get current circuit breaker status for monitoring.
        """
        return {'is_open': self.circuit_breaker.is_open, 'failures': self.circuit_breaker.failures, 'last_failure_time': self.circuit_breaker.last_failure_time}

# Testability: Unit/Integration tests
import unittest
from unittest.mock import patch, AsyncMock

class TestGeminiProvider(unittest.IsolatedAsyncioTestCase):
    
    @patch.dict(os.environ, {'GEMINI_API_KEY': 'test-key'})
    @patch('runner.runner_config.load_config', return_value=MagicMock(llm_provider_api_key=''))
    @patch('google.generativeai.configure')
    def setUp(self, mock_configure, mock_config_load):
        # This setup will fail if HAS_GEMINI is False, but tests should be skipped anyway
        if HAS_GEMINI:
            self.provider = GeminiProvider()
        else:
            self.skipTest("google-generativeai SDK not installed")

    def test_scrub_text(self):
        input_text = "My api_key = sk-abc123 and email is test@example.com"
        # Note: Testing the original local scrub_text used by _redact_log
        scrubbed = scrub_text(input_text)
        self.assertIn('[REDACTED]', scrubbed)
        self.assertNotIn('sk-abc123', scrubbed)
        self.assertNotIn('test@example.com', scrubbed)

    def test_calculate_cost(self):
        cost = calculate_cost('gemini-2.5-flash', 1000, 500)
        self.assertEqual(cost, (0.3e-6 * 1000) + (2.5e-6 * 500))

    def test_circuit_breaker(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        self.assertTrue(cb.can_proceed())
        cb.record_failure()
        self.assertFalse(cb.can_proceed())
        cb.record_success()
        self.assertTrue(cb.can_proceed())

    @patch('google.generativeai.GenerativeModel.count_tokens_async', new_callable=AsyncMock)
    async def test_count_tokens(self, mock_count):
        mock_count.return_value = type('Resp', (), {'total_tokens': 10})
        tokens = await self.provider.count_tokens("test", "model")
        self.assertEqual(tokens, 10)

    @patch('google.generativeai.GenerativeModel.count_tokens_async', new_callable=AsyncMock)
    @patch('google.generativeai.GenerativeModel.generate_content_async', new_callable=AsyncMock)
    @patch('runner.llm_client_providers.gemini_provider.log_audit_event', new_callable=AsyncMock)
    async def test_call_non_stream(self, mock_audit, mock_generate, mock_count):
        mock_generate.return_value = type('Resp', (), {'text': 'Hello'})
        mock_count.return_value = type('Resp', (), {'total_tokens': 1})
        response = await self.provider.call("test prompt", "gemini-2.5-pro")
        self.assertIn('content', response)
        self.assertIn('run_id', response)
        self.assertEqual(response['content'], 'Hello')
        mock_audit.assert_called_once() # Check that audit was called

    @patch('google.generativeai.GenerativeModel.count_tokens_async', new_callable=AsyncMock)
    @patch('google.generativeai.GenerativeModel.generate_content_async', new_callable=AsyncMock)
    @patch('runner.llm_client_providers.gemini_provider.log_audit_event', new_callable=AsyncMock)
    async def test_call_stream(self, mock_audit, mock_generate, mock_count):
        async def mock_stream():
            yield type('Chunk', (), {'text': 'chunk1'})
            yield type('Chunk', (), {'text': 'chunk2'})

        mock_generate.return_value = mock_stream()
        mock_count.return_value = type('Resp', (), {'total_tokens': 1})
        
        gen = await self.provider.call("test", "model", stream=True)
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
        self.assertEqual(chunks, ['chunk1', 'chunk2'])
        mock_audit.assert_called_once() # Check that audit was called

# API/CLI Integration
try:
    from fastapi import FastAPI, Query
    from starlette.responses import StreamingResponse
    import uvicorn
    HAS_API_LIBS = True
except ImportError:
    HAS_API_LIBS = False
    logger.warning("FastAPI/Uvicorn not installed. API/CLI server functionality will be disabled.")


if HAS_API_LIBS:
    app = FastAPI()

    @app.get("/health")
    async def api_health():
        # Note: Requires API_KEY environment variable to be set for initialization
        if not HAS_GEMINI or not API_KEY:
            return {"healthy": False, "circuit": "SDK or API_KEY_MISSING"}
        provider = GeminiProvider() 
        return {"healthy": await provider.health_check(), "circuit": provider.get_circuit_status()}

    @app.get("/metrics")
    def api_metrics():
        from prometheus_client import generate_latest
        return generate_latest()

    @app.post("/call")
    async def api_call(prompt: str = Query(...), model: str = Query("gemini-2.5-pro"), stream: bool = Query(False)):
        # Note: Requires API_KEY environment variable to be set for initialization
        if not HAS_GEMINI or not API_KEY:
            return {"error": "Gemini SDK or API_KEY not configured on server."}
        provider = GeminiProvider() 
        if stream:
            gen = await provider.call(prompt, model, stream=True)
            async def stream_gen():
                async for chunk in gen:
                    yield chunk
            return StreamingResponse(stream_gen(), media_type="text/event-stream")
        else:
            return await provider.call(prompt, model)

# CLI integration
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gemini Provider CLI")
    parser.add_argument("--prompt", required=False, help="Input prompt") # Make optional for --test/--server
    parser.add_argument("--model", default="gemini-2.5-pro", help="Model name")
    parser.add_argument("--stream", action="store_true", help="Stream response")
    parser.add_argument("--test", action="store_true", help="Run tests")
    parser.add_argument("--server", action="store_true", help="Start FastAPI server")
    args = parser.parse_args()

    if args.test:
        unittest.main(argv=['first-arg-is-ignored']) # Pass dummy arg to unittest.main
    elif args.server:
        if HAS_API_LIBS and HAS_GEMINI:
            if not API_KEY:
                print("ERROR: GEMINI_API_KEY environment variable must be set to run the server.")
            else:
                uvicorn.run(app, host="0.0.0.0", port=8000)
        else:
            print("Cannot start server: FastAPI/Uvicorn or google-generativeai not installed.")
    elif args.prompt:
        if not HAS_GEMINI or not API_KEY:
             print("ERROR: GEMINI_API_KEY environment variable must be set, and google-generativeai must be installed.")
        else:
            import asyncio
            provider = GeminiProvider() 
            if args.stream:
                async def stream_main():
                    gen = await provider.call(args.prompt, args.model, stream=True)
                    async for chunk in gen:
                        print(chunk, end='', flush=True)
                asyncio.run(stream_main())
            else:
                response = asyncio.run(provider.call(args.prompt, args.model))
                print(response["content"])
    else:
        parser.print_help()