# runner/llm_client_providers/local_provider.py
"""
local_provider.py
Local LLM provider plugin (e.g., Ollama) with GOAT-level upgrades for observability, reliability, security, extensibility, and more.
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
from runner.runner_config import RunnerConfig
from runner.runner_config import load_config # [FIX] Import load_config

# [FIX] Corrected import path for base class
try:
    from runner.llm_client.llm_provider_base import LLMProvider
except ImportError:
    # Fallback for environments where it's in a different structure
    try:
        from ..docgen_llm_call import LLMProvider
    except ImportError:
        logger.critical("Failed to import LLMProvider base class. Shutting down.")
        raise

try:
    from runner import tracer   # central OTEL tracer
    if tracer is None:
        raise ImportError("Tracer is None")
except ImportError:
    logger.warning("Tracer not found, using OTel default.")
    from opentelemetry import trace
    tracer = trace.get_tracer(__name__)
# -------------------------------------------------------------------------------

# Configuration and API Key loading
config = load_config() # picks up env / config file
# API_KEY is technically not needed for a local Ollama endpoint, but we retain
# the structure in case the local provider requires a local bearer token/key.
API_KEY = config.llm_provider_api_key or os.getenv("LOCAL_API_KEY")

# Metrics initialization (Prometheus) - Retain local stream metrics
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
        self.failures = 0
        if self.is_open:
             logger.info("Circuit breaker closed after success.")
        self.is_open = False

    def reset(self):
        self.is_open = False
        self.failures = 0
        logger.info("Circuit breaker manually reset.")

# Security: Regex patterns for scrubbing secrets/PII
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
    """
    for pattern in SECRET_PATTERNS:
        text = re.sub(pattern, '[REDACTED]', text)
    return text

# Cost awareness: For local models, cost is typically 0, but allow custom pricing
PRICING: Dict[str, Dict[str, float]] = {}  # User can populate: model: {'input': cost_per_token, 'output': cost_per_token}

def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Calculate cost based on token counts and model pricing (default 0 for local).
    """
    if model in PRICING:
        return PRICING[model]['input'] * input_tokens + PRICING[model]['output'] * output_tokens
    return 0.0

class LocalProvider(LLMProvider):
    """
    Enhanced LLMProvider for local LLMs (e.g., Ollama) with observability, reliability, security, and extensibility features.
    """
    name = "local"
    # Local models do not require a self.api_key check, but we include it for structure

    def __init__(self):
        """
        Initialize the Local provider with initial setup.
        """
        super().__init__()
        # self.api_key = API_KEY or os.getenv("LOCAL_API_KEY") # Retained for template structure, but unused by default.
        self.circuit_breaker = CircuitBreaker()
        self.custom_models: Dict[str, Dict[str, Any]] = {
            'llama2': {'endpoint': 'http://localhost:11434/api/generate', 'headers': {}},
            'mistral': {'endpoint': 'http://localhost:11434/api/generate', 'headers': {}}
        }  # Default supported models
        self.pre_hooks: List[Callable[[str], str]] = []
        self.post_hooks: List[Callable[[Any], Any]] = []
        LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1)
        self.load_plugins()  # Initial load

    def load_config(self, file_path: str):
        """
        Load external configuration for model aliases and endpoints from YAML or JSON file.
        """
        if file_path.endswith('.yaml') or file_path.endswith('.yml'):
            with open(file_path, 'r') as f:
                config = yaml.safe_load(f)
        elif file_path.endswith('.json'):
            with open(file_path, 'r') as f:
                config = json.load(f)
        else:
            raise ValueError("Unsupported config format. Use YAML or JSON.")
        for model_name, details in config.get('models', {}).items():
            self.register_custom_model(model_name, details['endpoint'], details.get('headers', {}))

    def register_custom_model(self, model_name: str, endpoint: str, headers: Dict[str, str] = None):
        """
        Register a custom model with alternative endpoint and headers for extensibility.
        """
        self.custom_models[model_name] = {'endpoint': endpoint, 'headers': headers or {}}
        logger.info(f"Registered custom model: {model_name}")

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

    async def _scrub_prompt(self, prompt: str) -> str:
        """
        Scrub prompt using the central security utility.
        """
        # Use the central redactor (adds PII, credit-cards, etc.)
        return await redact_secrets(prompt)

    @retry(retry=retry_if_exception_type(aiohttp.ClientError), stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def _api_call(self, endpoint: str, headers: Dict[str, str], data: Dict[str, Any], stream: bool, run_id: str):
        """
        Internal API call with retry and tracing.
        """
        with tracer.start_as_current_span("local_api_call") as span:
            span.set_attribute("endpoint", endpoint)
            span.set_attribute("run_id", run_id)
            span.set_attribute("stream", stream)
            async with aiohttp.ClientSession(headers=headers) as session: # Pass headers to session
                try:
                    async with session.post(endpoint, json=data) as resp:
                        if resp.status != 200:
                            error_msg = f"API error: {resp.status} - {await resp.text()}"
                            logger.error(error_msg, extra={'run_id': run_id})
                            span.set_attribute("error", error_msg)
                            raise LLMError(detail=error_msg, provider=self.name)
                        # We must return the response object itself to be awaited in the stream
                        return resp
                except aiohttp.ClientError as e:
                    error_msg = f"Client error: {str(e)}. Check server status or endpoint configuration."
                    logger.error(error_msg, extra={'run_id': run_id})
                    span.set_attribute("error", error_msg)
                    raise LLMError(detail=error_msg, provider=self.name)


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
        
        # Runner foundation logging and provenance
        logger.info(f"[{run_id}] Calling {self.name} model={model}", extra={"run_id": run_id})
        
        # [FIX] Replace add_provenance with log_audit_event
        await log_audit_event(
            action="llm_provider_call",
            data={"provider": self.name, "model": model, "run_id": run_id}
        )

        start_time = time.time()
        
        scrubbed_prompt = await self._scrub_prompt(prompt) # Use central scrubbing
        for hook in self.pre_hooks:
            scrubbed_prompt = hook(scrubbed_prompt)

        data = {"model": model, "prompt": scrubbed_prompt, "stream": stream}
        data.update(kwargs)  # Support additional Ollama options
        
        input_tokens = await self.count_tokens(scrubbed_prompt, model)
        LLM_TOKENS_INPUT.labels(provider=self.name, model=model).inc(input_tokens)
        LLM_CALLS_TOTAL.labels(provider=self.name, model=model).inc()

        endpoint = self.custom_models.get(model, {'endpoint': 'http://localhost:11434/api/generate', 'headers': {}})['endpoint']
        headers = self.custom_models.get(model, {'headers': {}})['headers']
        headers.update({"Content-Type": "application/json"})
        
        # Add API Key if it exists in config/env (for local servers with auth)
        if API_KEY:
            headers['Authorization'] = f'Bearer {API_KEY}'

        try:
            response = await self._api_call(endpoint, headers, data, stream, run_id)

            self.circuit_breaker.record_success()
            LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1)

            if stream:
                async def gen():
                    partial_response = ""
                    chunk_start = time.time()
                    output_tokens = 0
                    with tracer.start_as_current_span("local_stream") as span:
                        span.set_attribute("model", model)
                        span.set_attribute("run_id", run_id)
                        try:
                            async for line in response.content:
                                if line.strip():
                                    try:
                                        chunk = json.loads(line)
                                    except json.JSONDecodeError:
                                        logger.warning(f"Failed to decode stream chunk: {line.decode()}", extra=log_extra)
                                        continue

                                    chunk_text = chunk.get('response', '')
                                    if chunk_text:
                                        yield chunk_text
                                        partial_response += chunk_text
                                        # [FIX] Use local variable for output tokens
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
                            if isinstance(e, LLMError):
                                raise
                            else:
                                raise LLMError(detail=str(e), provider=self.name) from e
                        finally:
                            # Finalize Metrics
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

                return gen()
            else:
                with tracer.start_as_current_span("local_call") as span:
                    span.set_attribute("model", model)
                    span.set_attribute("run_id", run_id)
                    lines = await response.text()
                    # Non-streaming response is usually one big JSON object in the stream format from Ollama
                    full_json = "".join([line for line in lines.splitlines() if line.strip()])
                    
                    # Parse the full response to extract content
                    try:
                        final_response_obj = json.loads(full_json)
                        content = final_response_obj.get('response', '')
                    except json.JSONDecodeError:
                        # Fallback for unexpected formats
                        logger.warning("Failed to parse non-streaming response as single JSON object.", extra=log_extra)
                        # Try to parse line by line
                        try:
                            content = "".join([json.loads(line)['response'] for line in lines.splitlines() if line.strip()])
                        except Exception:
                            content = "" # Give up if parsing fails
                    
                    output_tokens = await self.count_tokens(content, model)
                    
                    # Finalize Metrics
                    LLM_TOKENS_OUTPUT.labels(provider=self.name, model=model).inc(output_tokens)
                    cost = calculate_cost(model, input_tokens, output_tokens)
                    LLM_COST_TOTAL.labels(provider=self.name, model=model).inc(cost)
                    total_latency = time.time() - start_time
                    LLM_LATENCY_SECONDS.labels(provider=self.name, model=model).observe(total_latency)
                    
                    logger.info("Call completed", extra={**log_extra, 'output_tokens': output_tokens, 'cost': cost, 'latency': total_latency, 'response_preview': self._redact_log(content)})
                    stamped_response = {"content": content, "model": model, 'version': '1.0', 'run_id': run_id, 'timestamp': timestamp}
                    for hook in self.post_hooks:
                        stamped_response = hook(stamped_response)
                    span.set_attribute("output_tokens", output_tokens)
                    span.set_attribute("cost", cost)
                    return stamped_response
        except Exception as e:
            self.circuit_breaker.record_failure()
            LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
            logger.error("Call error", extra={**log_extra, 'error': str(e)})
            
            # Re-raise as LLMError if it's not already one
            if isinstance(e, LLMError):
                raise
            else:
                raise LLMError(detail=str(e), provider=self.name) from e
        finally:
            # Update health gauge based on final circuit breaker state
            LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1 if self.circuit_breaker.is_closed() else 0)

    def _redact_log(self, content: str) -> str:
        """
        Redact sensitive content for logging.
        """
        return scrub_text(content)[:100] + '...' if len(content) > 100 else scrub_text(content)

    async def count_tokens(self, text: str, model: str) -> int:
        """
        Approximate token count (no native support in Ollama).
        """
        # Kept async signature for consistency.
        return len(text) // 4  # Rough estimate

    async def health_check(self) -> bool:
        """
        Perform health check and update metrics/logs.
        """
        run_id = str(uuid.uuid4())
        log_extra = {'run_id': run_id, 'provenance': 'health_check'}
        logger.info("Health check started", extra=log_extra)
        
        # Reset health gauge before check
        LLM_PROVIDER_HEALTH.labels(provider=self.name).set(0)
        
        with tracer.start_as_current_span("local_health_check") as span:
            is_healthy = False
            try:
                async with aiohttp.ClientSession() as session:
                    # Default Ollama root endpoint check
                    async with session.get("http://localhost:11434") as resp:
                        is_healthy = resp.status == 200
                        LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1 if is_healthy else 0)
                        logger.info("Health check result", extra={**log_extra, 'healthy': is_healthy})
                        span.set_attribute("status", is_healthy)
                        return is_healthy
            except Exception as e:
                error_msg = f"Health check error: {str(e)}. Check if local server (e.g., Ollama) is running."
                logger.error(error_msg, extra=log_extra)
                LLM_PROVIDER_HEALTH.labels(provider=self.name).set(0)
                span.set_attribute("error", error_msg)
                return False

    def get_circuit_status(self) -> Dict[str, Any]:
        """
        Get current circuit breaker status for monitoring.
        """
        return {'is_open': self.circuit_breaker.is_open, 'failures': self.circuit_breaker.failures, 'last_failure_time': self.circuit_breaker.last_failure_time}

    def reset_circuit(self):
        """
        Manually reset the circuit breaker.
        """
        self.circuit_breaker.reset()
        LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1)

# This function is required by the plugin manager
def get_provider():
    return LocalProvider()

# Testability: Unit/Integration tests
import unittest
from unittest.mock import patch, AsyncMock

class TestLocalProvider(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = LocalProvider()

    def test_scrub_text(self):
        input_text = "My api_key = sk-abc123 and email is test@example.com"
        scrubbed = scrub_text(input_text)
        self.assertIn('[REDACTED]', scrubbed)
        self.assertNotIn('sk-abc123', scrubbed)
        self.assertNotIn('test@example.com', scrubbed)

    def test_calculate_cost(self):
        cost = calculate_cost('llama2', 1000, 500)
        self.assertEqual(cost, 0.0)

    def test_circuit_breaker(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        self.assertTrue(cb.can_proceed())
        cb.record_failure()
        self.assertFalse(cb.can_proceed())
        cb.record_success()
        self.assertTrue(cb.can_proceed())

    async def test_count_tokens(self):
        tokens = await self.provider.count_tokens("test text", "llama2")
        self.assertGreater(tokens, 0)

    @patch('aiohttp.ClientSession.post', new_callable=AsyncMock)
    async def test_call_non_stream(self, mock_post):
        mock_resp = AsyncMock()
        # Mocking the typical full response from Ollama non-stream endpoint (which is often a collection of streaming responses joined)
        mock_resp.text = AsyncMock(return_value='{"response": "Hello", "done": true}') 
        mock_resp.status = 200
        mock_post.return_value.__aenter__.return_value = mock_resp
        
        # [FIX] Patch log_audit_event
        with patch('runner.llm_client_providers.local_provider.log_audit_event', new_callable=AsyncMock):
            response = await self.provider.call("test prompt", "llama2")
            self.assertIn('content', response)
            self.assertIn('run_id', response)
            self.assertEqual(response['content'], 'Hello')

    @patch('aiohttp.ClientSession.post', new_callable=AsyncMock)
    async def test_call_stream(self, mock_post):
        mock_resp = AsyncMock()
        async def mock_iter():
            yield b'{"response": "chunk1"}\n'
            yield b'{"response": "chunk2"}\n'

        mock_resp.content.__aiter__ = mock_iter
        mock_resp.status = 200
        mock_post.return_value.__aenter__.return_value = mock_resp
        
        # [FIX] Patch log_audit_event
        with patch('runner.llm_client_providers.local_provider.log_audit_event', new_callable=AsyncMock):
            gen = await self.provider.call("test", "llama2", stream=True)
            chunks = []
            async for chunk in gen:
                chunks.append(chunk)
            self.assertEqual(chunks, ['chunk1', 'chunk2'])

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
        provider = LocalProvider()
        return {"healthy": await provider.health_check(), "circuit": provider.get_circuit_status()}

    @app.get("/metrics")
    def api_metrics():
        from prometheus_client import generate_latest
        return generate_latest()

    @app.post("/call")
    async def api_call(prompt: str = Query(...), model: str = Query("llama2"), stream: bool = Query(False)):
        provider = LocalProvider()
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
    parser = argparse.ArgumentParser(description="Local Provider CLI")
    parser.add_argument("--prompt", required=False, help="Input prompt") # Made optional for --test/--server
    parser.add_argument("--model", default="llama2", help="Model name")
    parser.add_argument("--stream", action="store_true", help="Stream response")
    parser.add_argument("--test", action="store_true", help="Run tests")
    parser.add_argument("--server", action="store_true", help="Start FastAPI server")
    args = parser.parse_args()

    if args.test:
        unittest.main(argv=['first-arg-is-ignored'])
    elif args.server:
        if HAS_API_LIBS:
            uvicorn.run(app, host="0.0.0.0", port=8000)
        else:
            print("Cannot start server: FastAPI/Uvicorn not installed.")
    elif args.prompt:
        import asyncio
        provider = LocalProvider()
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