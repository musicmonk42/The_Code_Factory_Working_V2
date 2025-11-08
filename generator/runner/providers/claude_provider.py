"""
claude_provider.py
Anthropic Claude LLM provider plugin with  for observability, reliability, security, extensibility, and more.

Features:
- Observability: Prometheus metrics, structured logging with run IDs, and OpenTelemetry tracing.
- Reliability: Circuit breaker, retries with exponential backoff, health checks.
- Security: Prompt scrubbing for PII/secrets, redacted logging.
- Extensibility: Custom model registration, pre/post hooks, plugin support, externalizable config via YAML/JSON.
- Streaming: Advanced chunk logging and partial aggregation.
- Provenance: Outputs stamped with metadata.
- Cost Awareness: Token-based cost tracking.
- Testability: Built-in unit and integration tests.
- Documentation: Comprehensive docstrings and examples.
- API/CLI: Ready for FastAPI integration and CLI usage.

Dependencies:
- anthropic
- aiohttp
- prometheus_client
- tenacity (for retries)
- logging
- uuid
- re
- json
- datetime
- typing
- opentelemetry-api
- opentelemetry-sdk
- opentelemetry-exporter-otlp-proto-grpc
- pyyaml
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
    from anthropic import AsyncAnthropic, AnthropicError, AuthenticationError, RateLimitError, APIConnectionError
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False
    # Define dummy types for the class to load without crashing
    class AnthropicError(Exception): pass
    class AuthenticationError(AnthropicError): pass
    class RateLimitError(AnthropicError): pass
    class APIConnectionError(AnthropicError): pass

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
        from ..docgen_llm_call import LLMProvider # Original relative import retained
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
API_KEY = config.llm_provider_api_key or os.getenv("CLAUDE_API_KEY")

# Prometheus Metrics (retained local metrics for streaming chunks, others use shared)
from prometheus_client import Counter, Histogram

stream_chunks_total = Counter('claude_stream_chunks_total', 'Total Claude stream chunks', ['model'])
stream_chunk_latency = Histogram('claude_stream_chunk_latency_seconds', 'Claude stream chunk latency in seconds', ['model'])

PRICING = {
    'claude-3-opus-20240229': {'input': 0.000015, 'output': 0.000075},
    'claude-3-sonnet-20240229': {'input': 0.000003, 'output': 0.000015},
    'claude-3-haiku-20240307': {'input': 0.00000025, 'output': 0.00000125},
    'claude-3.5-sonnet-20240620': {'input': 0.000003, 'output': 0.000015},
    'claude-3.5-haiku-20241022': {'input': 0.00000025, 'output': 0.00000125},
}

# NOTE: SECRET_PATTERNS and scrub_text are removed/redundant as we use redact_secrets

def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    if model in PRICING:
        return PRICING[model]['input'] * input_tokens + PRICING[model]['output'] * output_tokens
    logger.warning(json.dumps({"event": "cost_warning", "message": f"No pricing info for model {model}. Cost set to 0."}))
    return 0.0

class CircuitBreaker:
    """Simple circuit breaker to prevent calls during repeated failures."""
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
                logger.info(json.dumps({"event": "circuit_breaker_recovery", "status": "closed"}))
                return True
            logger.warning(json.dumps({"event": "circuit_breaker_open"}))
            return False
        return True
    
    def is_closed(self) -> bool:
        return not self.is_open

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.failure_threshold:
            self.is_open = True
            logger.error(json.dumps({"event": "circuit_breaker_opened", "failures": self.failures}))

    def record_success(self):
        if self.is_open:
            logger.info(json.dumps({"event": "circuit_breaker_reset", "status": "closed"}))
        self.failures = 0
        self.is_open = False

class ClaudeProvider(LLMProvider):
    """
    ClaudeProvider: GOAT-level Anthropic Claude LLM provider plugin.
    """
    
    name = "claude"

    def __init__(self):
        super().__init__()
        # Use centrally loaded key, falling back to os.getenv
        self.api_key = API_KEY or os.getenv('CLAUDE_API_KEY')
        
        # [FIX] SDK/key guards
        if not HAS_ANTHROPIC or not self.api_key:
            LLM_ERRORS_TOTAL.labels(provider=self.name, model="claude-config").inc()
            raise ConfigurationError("Claude provider configured but SDK (anthropic) or CLAUDE_API_KEY is missing.")

        self.client = AsyncAnthropic(api_key=self.api_key)
        self.circuit_breaker = CircuitBreaker()
        self.custom_models: Dict[str, Dict[str, Any]] = {}
        self.pre_hooks: List[Callable[[str], str]] = []
        self.post_hooks: List[Callable[[Dict[str, Any]], Dict[str, Any]]] = []
        self.plugins: List[Callable] = []
        LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1)

    def load_config(self, file_path: str):
        """
        Load configuration from YAML or JSON file for models and endpoints.
        """
        if file_path.endswith('.yaml') or file_path.endswith('.yml'):
            with open(file_path, 'r') as f:
                config = yaml.safe_load(f)
        elif file_path.endswith('.json'):
            with open(file_path, 'r') as f:
                config = json.load(f)
        else:
            raise ValueError("Unsupported config format. Use YAML or JSON.")
        for model, details in config.get('models', {}).items():
            self.register_custom_model(model, details['endpoint'], details.get('headers', {}))

    def register_custom_model(self, model_name: str, endpoint: str, headers: Optional[Dict[str, str]] = None):
        self.custom_models[model_name] = {'endpoint': endpoint, 'headers': headers or {}}

    def add_pre_hook(self, hook: Callable[[str], str]):
        self.pre_hooks.append(hook)

    def add_post_hook(self, hook: Callable[[Dict[str, Any]], Dict[str, Any]]):
        self.post_hooks.append(hook)

    async def _scrub_prompt(self, prompt: str) -> str:
        """
        Scrub prompt using the central security utility.
        """
        # [FIX] Use the central redactor (await)
        return await redact_secrets(prompt)

    def _redact_log(self, content: str) -> str:
        # Simple local redaction for log previews
        return content[:50] + '...' if len(content) > 50 else content

    def _apply_pre_hooks(self, prompt: str) -> str:
        for hook in self.pre_hooks:
            prompt = hook(prompt)
        return prompt

    def _apply_post_hooks(self, response: Dict[str, Any]) -> Dict[str, Any]:
        for hook in self.post_hooks:
            response = hook(response)
        return response

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10), retry=retry_if_exception_type(Exception))
    async def _api_call(self, model: str, processed_prompt: str, stream: bool, run_id: str):
        with tracer.start_as_current_span("claude_api_call") as span:
            span.set_attribute("model", model)
            span.set_attribute("run_id", run_id)
            span.set_attribute("stream", stream)
            try:
                if model in self.custom_models:
                    custom = self.custom_models[model]
                    async with aiohttp.ClientSession() as session:
                        headers = {"x-api-key": self.api_key, **custom['headers']}
                        payload = {"model": model, "max_tokens": 4096, "messages": [{"role": "user", "content": processed_prompt}]}
                        async with session.post(custom['endpoint'], json=payload, headers=headers) as resp:
                            if resp.status != 200:
                                raise Exception(f"Custom endpoint error: {resp.status}")
                            response = await resp.json()
                            return response, False
                else:
                    if stream:
                        return await self.client.messages.create(model=model, max_tokens=4096, messages=[{"role": "user", "content": processed_prompt}], stream=True), True
                    else:
                        return await self.client.messages.create(model=model, max_tokens=4096, messages=[{"role": "user", "content": processed_prompt}]), False
            except AuthenticationError as e:
                error_msg = "Authentication failed: Invalid or missing API key. Please verify CLAUDE_API_KEY environment variable."
                LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
                logger.error(json.dumps({"event": "call_error", "run_id": run_id, "error": error_msg}))
                span.set_attribute("error", error_msg)
                raise LLMError(detail=error_msg, provider=self.name) from e
            except RateLimitError as e:
                error_msg = "Rate limit exceeded: Reduce request frequency or check Anthropic dashboard for limits."
                LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
                logger.error(json.dumps({"event": "call_error", "run_id": run_id, "error": error_msg}))
                span.set_attribute("error", error_msg)
                raise LLMError(detail=error_msg, provider=self.name) from e
            except APIConnectionError as e:
                error_msg = "Connection error: Check network connectivity or API endpoint status."
                LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
                logger.error(json.dumps({"event": "call_error", "run_id": run_id, "error": error_msg}))
                span.set_attribute("error", error_msg)
                raise LLMError(detail=error_msg, provider=self.name) from e
            except AnthropicError as e:
                error_type = type(e).__name__
                error_msg = f"Anthropic API error ({error_type}): {str(e)}. Check logs for details and Anthropic documentation."
                LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
                logger.error(json.dumps({"event": "call_error", "run_id": run_id, "error": error_msg}))
                span.set_attribute("error", error_msg)
                raise LLMError(detail=error_msg, provider=self.name) from e
            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}. Please check the implementation or contact support."
                LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
                logger.error(json.dumps({"event": "call_error", "run_id": run_id, "error": error_msg}))
                span.set_attribute("error", error_msg)
                raise LLMError(detail=error_msg, provider=self.name) from e

    async def call(self, prompt: str, model: str, stream: bool = False, **kwargs) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
        """
        Call the Claude API with the given prompt and model.
        """
        if not self.circuit_breaker.can_proceed():
            LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
            raise RuntimeError("Circuit breaker open: Provider temporarily disabled due to failures. Reset with reset_circuit().")

        run_id = str(uuid.uuid4())
        start_time = time.time()
        timestamp = time.time() # For stamped response
        
        log_extra = {'run_id': run_id, 'model': model, 'stream': stream, 'provenance': self.name}
        
        logger.info(f"[{run_id}] Calling {self.name} model={model}", extra={"run_id": run_id})
        LLM_CALLS_TOTAL.labels(provider=self.name, model=model).inc()
        
        # [FIX] Redact before call
        scrubbed_prompt = await self._scrub_prompt(prompt)
        processed_prompt = self._apply_pre_hooks(scrubbed_prompt)

        logger.info(json.dumps({
            "event": "call_start", "run_id": run_id, "model": model,
            "prompt_preview": self._redact_log(processed_prompt), "stream": stream,
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(start_time))
        }))

        try:
            input_tokens = await self.count_tokens(processed_prompt, model)
            LLM_TOKENS_INPUT.labels(provider=self.name, model=model).inc(input_tokens)
            
            api_response, is_stream = await self._api_call(model, processed_prompt, stream, run_id)
            
            if stream and is_stream:
                async def gen():
                    partial_response = ""
                    chunk_start = time.time()
                    chunk_count = 0
                    with tracer.start_as_current_span("claude_stream") as span:
                        span.set_attribute("model", model)
                        span.set_attribute("run_id", run_id)
                        
                        output_tokens = 0
                        try:
                            async for chunk in api_response:
                                if chunk.type == 'content_block_delta':
                                    chunk_text = chunk.delta.text
                                    yield chunk_text
                                    partial_response += chunk_text
                                    
                                    # [FIX] Await token count
                                    chunk_output_tokens = await self.count_tokens(chunk_text, model)
                                    output_tokens += chunk_output_tokens
                                    chunk_latency = time.time() - chunk_start
                                    stream_chunks_total.labels(model=model).inc()
                                    stream_chunk_latency.labels(model=model).observe(chunk_latency)
                                    logger.debug(json.dumps({
                                        "event": "stream_chunk",
                                        "run_id": run_id,
                                        "chunk_size": len(chunk_text),
                                        "chunk_preview": self._redact_log(chunk_text),
                                        "chunk_latency": chunk_latency
                                    }))
                                    span.add_event("chunk_received", {"chunk_number": chunk_count, "chunk_latency": chunk_latency})
                                    chunk_start = time.time()
                                    chunk_count += 1
                            
                            output_tokens_final = await self.count_tokens(partial_response, model)
                            LLM_TOKENS_OUTPUT.labels(provider=self.name, model=model).inc(output_tokens_final)
                            cost = calculate_cost(model, input_tokens, output_tokens_final)
                            LLM_COST_TOTAL.labels(provider=self.name, model=model).inc(cost)
                            total_latency = time.time() - start_time
                            LLM_LATENCY_SECONDS.labels(provider=self.name, model=model).observe(total_latency)
                            LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1)

                            logger.info(json.dumps({
                                "event": "call_complete_stream",
                                "run_id": run_id,
                                "output_tokens": output_tokens_final,
                                "cost": cost,
                                "latency": total_latency,
                            }))
                            self.circuit_breaker.record_success()
                            span.set_attribute("output_tokens", output_tokens_final)
                            span.set_attribute("cost", cost)
                            
                            # [FIX] Audit after call (stream)
                            await log_audit_event(
                                action="llm_provider_call",
                                data={"provider": self.name, "model": model, "run_id": run_id, "stream": True, "input_tokens": input_tokens, "output_tokens": output_tokens_final}
                            )

                        except Exception as e:
                            self.circuit_breaker.record_failure()
                            LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
                            logger.error(f"Stream error: {e}", extra={**log_extra, 'error': str(e)})
                            # Re-raise as LLMError if it's not already one
                            if isinstance(e, LLMError):
                                raise
                            else:
                                raise LLMError(detail=str(e), provider=self.name) from e
                return gen()
            else:
                with tracer.start_as_current_span("claude_call") as span:
                    span.set_attribute("model", model)
                    span.set_attribute("run_id", run_id)
                    if is_stream:
                        # This should not happen based on _api_call return, but safety first
                        content = api_response.get('content', [{}])[0].get('text', '')
                    else:
                        content = api_response.content[0].text
                    
                    output_tokens = await self.count_tokens(content, model)
                    LLM_TOKENS_OUTPUT.labels(provider=self.name, model=model).inc(output_tokens)
                    cost = calculate_cost(model, input_tokens, output_tokens)
                    LLM_COST_TOTAL.labels(provider=self.name, model=model).inc(cost)
                    latency = time.time() - start_time
                    LLM_LATENCY_SECONDS.labels(provider=self.name, model=model).observe(latency)
                    LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1)

                    logger.info(json.dumps({
                        "event": "call_complete",
                        "run_id": run_id,
                        "output_tokens": output_tokens,
                        "cost": cost,
                        "latency": latency,
                    }))
                    self.circuit_breaker.record_success()
                    result = {
                        "content": content,
                        "model": model,
                        "version": "1.0",
                        "run_id": run_id,
                        "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    }
                    result = self._apply_post_hooks(result)
                    span.set_attribute("output_tokens", output_tokens)
                    span.set_attribute("cost", cost)
                    
                    # [FIX] Audit after call (non-stream)
                    await log_audit_event(
                        action="llm_provider_call",
                        data={"provider": self.name, "model": model, "run_id": run_id, "stream": False, "input_tokens": input_tokens, "output_tokens": output_tokens}
                    )
                    return result
        except Exception as e:
            self.circuit_breaker.record_failure()
            LLM_ERRORS_TOTAL.labels(provider=self.name, model=model).inc()
            
            latency = time.time() - start_time
            logger.error(json.dumps({
                "event": "call_error",
                "run_id": run_id,
                "error": str(e),
                "latency": latency,
            }))
            
            # Re-raise as LLMError if it's not already one
            if isinstance(e, LLMError):
                raise
            else:
                raise LLMError(detail=str(e), provider=self.name) from e
        finally:
            # Update health gauge based on final circuit breaker state
            LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1 if self.circuit_breaker.is_closed() else 0)

    async def count_tokens(self, text: str, model: str) -> int:
        try:
            # Note: Anthropic's count_tokens is sync, wrapping in async to avoid blocking
            return await asyncio.to_thread(self.client.count_tokens, text)
        except Exception as e:
            logger.warning(json.dumps({"event": "token_count_warning", "message": f"Token counting failed: {str(e)}. Approximating tokens."}))
            return len(text.split())

    async def health_check(self) -> bool:
        run_id = str(uuid.uuid4())
        log_extra = {'run_id': run_id, 'provenance': 'health_check'}
        logger.info("Health check started", extra=log_extra)
        
        # [FIX] Add guard for API key and SDK
        if not self.api_key or not HAS_ANTHROPIC:
             logger.error("Claude health check failed: API key or SDK is missing.", extra=log_extra)
             LLM_PROVIDER_HEALTH.labels(provider=self.name).set(0)
             return False

        # Reset health gauge before check
        LLM_PROVIDER_HEALTH.labels(provider=self.name).set(0)
        
        with tracer.start_as_current_span("claude_health_check") as span:
            try:
                # Use aiohttp for raw endpoint check, relying on client config for official API url
                async with aiohttp.ClientSession() as session:
                    # Note: Using the official API endpoint for a general check, if Anthropic has one
                    url = "https://api.anthropic.com/v1/models" 
                    headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
                    
                    # We only check models endpoint, which is less heavy than a completion call
                    async with session.get(url, headers=headers) as resp:
                        status = resp.status == 200
                        LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1 if status else 0)
                        logger.info(json.dumps({
                            "event": "health_check",
                            "run_id": run_id,
                            "status": status
                        }))
                        span.set_attribute("status", status)
                        return status
            except Exception as e:
                LLM_PROVIDER_HEALTH.labels(provider=self.name).set(0)
                error_msg = f"Health check failed: {str(e)}. Check API key, network, or service status."
                logger.error(json.dumps({
                    "event": "health_check_error",
                    "run_id": run_id,
                    "error": error_msg
                }))
                span.set_attribute("error", error_msg)
                return False

    def reset_circuit(self):
        self.circuit_breaker.failures = 0
        self.circuit_breaker.is_open = False
        LLM_PROVIDER_HEALTH.labels(provider=self.name).set(1)

# This function is required by the plugin manager
def get_provider():
    return ClaudeProvider()

# Example CLI usage
if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="ClaudeProvider CLI")
    parser.add_argument('--prompt', type=str, required=False, help='Prompt text')
    parser.add_argument('--model', type=str, default='claude-3-opus-20240229', help='Claude model name')
    parser.add_argument('--stream', action='store_true', help='Stream response')
    parser.add_argument('--test', action='store_true', help="Run tests")
    args = parser.parse_args()

    async def main():
        # NOTE: This init will fail if CLAUDE_API_KEY is not set/config not loaded
        if not (API_KEY or os.getenv('CLAUDE_API_KEY')):
             print("ERROR: CLAUDE_API_KEY environment variable must be set to run the CLI.")
             return
        
        provider = ClaudeProvider()
        if args.stream:
            gen = await provider.call(args.prompt, args.model, stream=True)
            async for chunk in gen:
                print(chunk, end='', flush=True)
        else:
            result = await provider.call(args.prompt, args.model)
            print(result["content"])

    if args.test:
        unittest.main(argv=['first-arg-is-ignored'])
    elif args.prompt:
        asyncio.run(main())
    else:
        parser.print_help()


# Example FastAPI integration
def example_fastapi():
    from fastapi import FastAPI, Query
    import asyncio
    app = FastAPI()
    # NOTE: This init will fail if CLAUDE_API_KEY is not set/config not loaded
    provider = ClaudeProvider()

    @app.get("/health")
    async def health():
        return {"healthy": await provider.health_check()}

    @app.post("/generate")
    async def generate(prompt: str = Query(...), model: str = Query('claude-3-opus-20240229')):
        return await provider.call(prompt, model)

    return app

# Unit tests
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

class TestClaudeProvider(unittest.IsolatedAsyncioTestCase):
    # Patch environment for init to pass
    @patch.dict(os.environ, {'CLAUDE_API_KEY': 'test-key'})
    @patch('runner.runner_config.load_config', return_value=MagicMock(llm_provider_api_key=''))
    async def test_scrub_prompt(self, mock_config_load):
        if not HAS_ANTHROPIC: self.skipTest("Anthropic SDK not installed")
        provider = ClaudeProvider()
        # Test now relies on central redact_secrets being called
        with patch('runner.runner_security_utils.redact_secrets', new_callable=AsyncMock) as mock_redact:
            mock_redact.return_value = "[REDACTED]"
            scrubbed = await provider._scrub_prompt("API_KEY=sk-12345678901234567890, email: test@example.com")
            self.assertEqual(scrubbed, "[REDACTED]")
            mock_redact.assert_called_once()

    @patch.dict(os.environ, {'CLAUDE_API_KEY': 'test-key'})
    @patch('runner.runner_config.load_config', return_value=MagicMock(llm_provider_api_key=''))
    @patch('runner.llm_client_providers.claude_provider.log_audit_event', new_callable=AsyncMock)
    async def test_call_non_stream(self, mock_audit, mock_config_load):
        if not HAS_ANTHROPIC: self.skipTest("Anthropic SDK not installed")
        provider = ClaudeProvider()
        with patch('anthropic.AsyncAnthropic.messages.create', new_callable=AsyncMock) as mock_create:
            # Mock the Anthropic message object structure
            mock_response_obj = MagicMock()
            mock_response_obj.content = [MagicMock(text="Claude response")]
            mock_create.return_value = mock_response_obj
            
            with patch.object(provider.client, 'count_tokens', return_value=3): # Mock sync token counting
                result = await provider.call("Hello", "claude-3-haiku-20240307")
                self.assertIn("content", result)
                self.assertEqual(result["content"], "Claude response")
                self.assertIn("model", result)
                mock_audit.assert_called_once() # Verify audit log was called

    @patch.dict(os.environ, {'CLAUDE_API_KEY': 'test-key'})
    @patch('runner.runner_config.load_config', return_value=MagicMock(llm_provider_api_key=''))
    async def test_count_tokens(self, mock_config_load):
        if not HAS_ANTHROPIC: self.skipTest("Anthropic SDK not installed")
        provider = ClaudeProvider()
        with patch('anthropic.AsyncAnthropic.count_tokens', return_value=3) as mock_count:
            tokens = await provider.count_tokens("Hello world", "claude-3-haiku-20240307")
            self.assertEqual(tokens, 3)
            # asyncio.to_thread is used, so the mock_count (the original sync func) is called
            mock_count.assert_called_once()
            
    @patch.dict(os.environ, {'CLAUDE_API_KEY': 'test-key'})
    @patch('runner.runner_config.load_config', return_value=MagicMock(llm_provider_api_key=''))
    async def test_health_check(self, mock_config_load):
        if not HAS_ANTHROPIC: self.skipTest("Anthropic SDK not installed")
        provider = ClaudeProvider()
        with patch('aiohttp.ClientSession.get', new_callable=AsyncMock) as mock_get:
            # Mock the aiohttp response structure
            mock_get.return_value.__aenter__.return_value.status = 200
            self.assertTrue(await provider.health_check())

if __name__ == '__main__':
    # NOTE: The original file included unittest.main() so it remains for completeness.
    unittest.main()