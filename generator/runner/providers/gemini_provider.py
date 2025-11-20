# runner/llm_client_providers/gemini_provider.py
"""
gemini_provider.py
Google Gemini LLM provider plugin.

This is a "dumb" plugin. All reliability (circuit breaking),
observability (metrics, logging, tracing), and security (redaction)
are handled by the llm_client.py manager that calls this plugin.
"""

import os
import logging
import uuid
import time
import re
import json
import yaml
import asyncio
from typing import Union, Dict, Any, AsyncGenerator, Callable, List, Optional
import aiohttp

# --- Conditional SDK Import ---
try:
    from google.generativeai import GenerativeModel, configure
    from google.generativeai.types import GenerateContentResponse
    # Import specific error types for translation
    from google.api_core.exceptions import InvalidArgument, PermissionDenied, NotFound, InternalServerError, ServiceUnavailable, DeadlineExceeded, ResourceExhausted
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False
    # Define dummy types for the class to load without crashing
    class GenerateContentResponse: pass
    class InvalidArgument(Exception): pass
    class PermissionDenied(Exception): pass
    class NotFound(Exception): pass
    class InternalServerError(Exception): pass
    class ServiceUnavailable(Exception): pass
    class DeadlineExceeded(Exception): pass
    class ResourceExhausted(Exception): pass


from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ---- Runner foundation imports ------------------------------------------------
from runner.llm_provider_base import LLMProvider
from runner.runner_errors import LLMError, ConfigurationError
from runner.runner_config import load_config # For loading API key in get_provider
# -------------------------------------------------------------------------------

# Get the logger for this module
logger = logging.getLogger(__name__)

# Metrics initialization (Prometheus) - Retain local metrics for stream chunks
# These are plugin-specific and not managed by the central llm_client
from prometheus_client import Counter, Histogram
# --- FIX: Import shared metrics from runner_metrics ---
from runner.runner_metrics import stream_chunks_total, stream_chunk_latency
# --- END FIX ---

# --- FIX: REMOVE LOCAL DEFINITIONS ---
# stream_chunks_total = Counter('llm_stream_chunks_total', 'Total number of stream chunks', ['model'])
# stream_chunk_latency = Histogram('llm_stream_chunk_latency_seconds', 'Latency per stream chunk in seconds', ['model'])
# --- END FIX ---


class GeminiProvider(LLMProvider):
    """
    Dumb plugin for Google Gemini.
    
    Handles the direct API interaction with the Gemini SDK.
    Assumes it is managed by a higher-level client that provides
    logging, metrics, tracing, circuit breaking, and scrubbing.
    """
    name = "gemini"

    def __init__(self, api_key: str):
        """
        Initialize the Gemini provider with API key validation and initial setup.
        """
        super().__init__()
        
        if not HAS_GEMINI:
             # --- FIX: Pass 'error_code' and 'detail' keywords ---
             raise ConfigurationError(
                 detail="Gemini provider configured but SDK (google-generativeai) is missing.",
                 error_code="CONFIG_SDK_MISSING"
             )
        
        if not api_key:
            # --- FIX: Pass 'error_code' and 'detail' keywords ---
            raise ConfigurationError(
                detail="GeminiProvider initialized without an API key.",
                error_code="CONFIG_INIT_KEY_MISSING"
            )

        self.api_key = api_key
        try:
            # Configure the SDK *once* with the key
            configure(api_key=self.api_key)
        except Exception as e:
            # --- FIX: Pass 'error_code' and 'detail' keywords ---
            raise ConfigurationError(
                detail=f"Failed to configure Gemini SDK: {e}",
                error_code="CONFIG_SDK_FAILURE"
            )

        self.custom_models: Dict[str, str] = {}  # model_name: gemini_model_name (for custom aliases)
        self.pre_hooks: List[Callable[[str], str]] = []
        self.post_hooks: List[Callable[[Any], Any]] = []
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
        logger.info("Plugins loaded (placeholder implementation).")

    @retry(
        retry=retry_if_exception_type((ServiceUnavailable, InternalServerError, DeadlineExceeded, ResourceExhausted)), 
        stop=stop_after_attempt(5), 
        wait=wait_exponential(multiplier=1, min=2, max=30)
    )
    async def _api_call(self, client: GenerativeModel, scrubbed_prompt: str, stream: bool, run_id: str):
        """
        Internal API call with retry and error translation.
        """
        try:
            if stream:
                return await client.generate_content_async(scrubbed_prompt, stream=True)
            else:
                return await client.generate_content_async(scrubbed_prompt)
        except (InvalidArgument, ValueError) as e:
            error_msg = f"Invalid request: {str(e)}. Check prompt format or model capabilities."
            raise LLMError(detail=error_msg, provider=self.name, error_code="LLM_PROVIDER_ERROR") from e
        except PermissionDenied as e:
            error_msg = f"API error: Invalid API Key or insufficient permissions. {str(e)}"
            raise LLMError(detail=error_msg, provider=self.name, error_code="LLM_PROVIDER_ERROR") from e
        except NotFound as e:
            error_msg = f"API error: Model not found or endpoint incorrect. {str(e)}"
            raise LLMError(detail=error_msg, provider=self.name, error_code="LLM_PROVIDER_ERROR") from e
        except (ServiceUnavailable, InternalServerError, DeadlineExceeded, ResourceExhausted) as e:
            # These are retriable errors
            error_type = type(e).__name__
            error_msg = f"Retriable API error ({error_type}): {str(e)}. Retrying..."
            logger.warning(error_msg, extra={'run_id': run_id})
            raise # Re-raise to trigger tenacity retry
        except Exception as e:  # Catch broader exceptions
            error_type = type(e).__name__
            error_msg = f"API error ({error_type}): {str(e)}."
            raise LLMError(detail=error_msg, provider=self.name, error_code="LLM_PROVIDER_ERROR") from e

    async def call(self, prompt: str, model: str, stream: bool = False, **kwargs) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
        """
        Call the LLM with reliability, security, and observability features.
        The prompt is assumed to be pre-scrubbed by the llm_client.
        """
        
        # Apply any registered pre-processing hooks
        processed_prompt = prompt
        for hook in self.pre_hooks:
            processed_prompt = hook(processed_prompt)

        # Get the actual Gemini model name from alias, or use the provided name
        gemini_model = self.custom_models.get(model, model)
        client = GenerativeModel(gemini_model)
        
        # A run_id just for this call, in case _api_call needs it for logging retries
        run_id = str(uuid.uuid4())[:8]

        try:
            response = await self._api_call(client, processed_prompt, stream, run_id)

            if stream:
                async def gen():
                    partial_response = ""
                    chunk_start = time.time()
                    try:
                        async for chunk in response:
                            chunk_text = chunk.text
                            yield chunk_text
                            partial_response += chunk_text
                            
                            # Keep local, plugin-specific stream metrics
                            chunk_latency = time.time() - chunk_start
                            stream_chunk_latency.labels(model=model).observe(chunk_latency)
                            stream_chunks_total.labels(model=model).inc()
                            chunk_start = time.time()
                    except Exception as e:
                        # Let the llm_client handle logging this error
                        raise LLMError(detail=f"Error during streaming: {e}", provider=self.name) from e
                return gen()
            else:
                content = response.text
                
                # Apply post-processing hooks
                stamped_response = {"content": content, "model": model}
                for hook in self.post_hooks:
                    stamped_response = hook(stamped_response)
                
                # Return the simple, hook-modified dictionary
                return {"content": stamped_response["content"], "model": stamped_response["model"]}
        
        except Exception as e:
            # Let the llm_client handle logging, metrics, and circuit breaking
            if isinstance(e, LLMError):
                raise  # Re-raise errors we've already translated
            else:
                # Wrap unexpected errors
                raise LLMError(detail=f"Unexpected error in call: {e}", provider=self.name) from e

    async def count_tokens(self, text: str, model: str) -> int:
        """
        Count tokens using Gemini's API asynchronously.
        """
        if not HAS_GEMINI:
             logger.warning("Gemini SDK not found. Using approximation for token count.")
             return len(text) // 4 + 1
        try:
            client = GenerativeModel(self.custom_models.get(model, model))
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
        if not self.api_key or not HAS_GEMINI:
             return False
        
        try:
            async with aiohttp.ClientSession() as session:
                # Using the standard models endpoint
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self.api_key}"
                async with session.get(url, timeout=5) as resp:
                    return resp.status == 200
        except Exception:
            return False

# --- Plugin Manager Entry Point ---
def get_provider():
    """
    Plugin manager entry point.
    Loads the API key from config/env and instantiates the provider.
    """
    config = load_config()
    API_KEY = config.llm_provider_api_key or os.getenv("GEMINI_API_KEY")

    if not HAS_GEMINI:
        logger.error("Google GenerativeAI SDK not found. Skipping GeminiProvider.")
        # --- FIX: Pass 'error_code' and 'detail' keywords ---
        raise ConfigurationError(
            detail="Google GenerativeAI SDK not found. Please run 'pip install google-generativeai'.",
            error_code="CONFIG_SDK_MISSING"
        )
        
    if not API_KEY:
        # This error will be caught by the llm_plugin_manager
        # --- FIX: Pass 'error_code' and 'detail' keywords ---
        raise ConfigurationError(
            detail="GEMINI_API_KEY environment variable or runner config not set.",
            error_code="CONFIG_LOAD_KEY_MISSING"
        )
        
    return GeminiProvider(api_key=API_KEY)

# --- Test/Example Usage ---
if __name__ == "__main__":
    import argparse
    import sys
    from unittest.mock import patch, AsyncMock, MagicMock
    import unittest

    # Setup basic logging for standalone execution
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Mock config loader for tests
    mock_config = MagicMock()
    mock_config.llm_provider_api_key = ''
    
    # Mock the SDK configure call
    if HAS_GEMINI:
        gemini_configure_patch = patch('google.generativeai.configure')
    else:
        # If SDK not installed, patch a dummy
        gemini_configure_patch = patch('gemini_provider.configure', MagicMock())

    class TestGeminiProvider(unittest.IsolatedAsyncioTestCase):
        
        @patch.dict(os.environ, {'GEMINI_API_KEY': 'test-key'})
        @patch('runner.runner_config.load_config', return_value=mock_config)
        def setUp(self, mock_config_load):
            if not HAS_GEMINI: 
                self.skipTest("google-generativeai SDK not installed")
            
            # Mock the 'configure' call during GeminiProvider init
            with gemini_configure_patch:
                self.provider = GeminiProvider(api_key="test-key")

        @patch('google.generativeai.GenerativeModel.count_tokens_async', new_callable=AsyncMock)
        async def test_count_tokens(self, mock_count):
            if not HAS_GEMINI: self.skipTest("google-generativeai SDK not installed")
            mock_count.return_value = type('Resp', (), {'total_tokens': 10})
            tokens = await self.provider.count_tokens("test", "model")
            self.assertEqual(tokens, 10)

        @patch('google.generativeai.GenerativeModel.count_tokens_async', new_callable=AsyncMock)
        @patch('google.generativeai.GenerativeModel.generate_content_async', new_callable=AsyncMock)
        async def test_call_non_stream(self, mock_generate, mock_count):
            if not HAS_GEMINI: self.skipTest("google-generativeai SDK not installed")
            mock_generate.return_value = type('Resp', (), {'text': 'Hello'})
            mock_count.return_value = type('Resp', (), {'total_tokens': 1})
            
            response = await self.provider.call("test prompt", "gemini-2.5-pro")
            self.assertIn('content', response)
            self.assertEqual(response['content'], 'Hello')

        @patch('google.generativeai.GenerativeModel.count_tokens_async', new_callable=AsyncMock)
        @patch('google.generativeai.GenerativeModel.generate_content_async', new_callable=AsyncMock)
        async def test_call_stream(self, mock_generate, mock_count):
            if not HAS_GEMINI: self.skipTest("google-generativeai SDK not installed")
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

    parser = argparse.ArgumentParser(description="Gemini Provider CLI")
    parser.add_argument("--prompt", required=False, help="Input prompt")
    parser.add_argument("--model", default="gemini-2.5-pro", help="Model name")
    parser.add_argument("--stream", action="store_true", help="Stream response")
    parser.add_argument("--test", action="store_true", help="Run tests")
    parser.add_argument("--server", action="store_true", help="Start FastAPI server")
    args, unknown = parser.parse_known_args()

    async def main_cli():
        try:
            with gemini_configure_patch:
                provider = get_provider()
        except ConfigurationError as e:
            print(f"ERROR: {e}")
            return

        if args.stream:
            gen = await provider.call(args.prompt, args.model, stream=True)
            async for chunk in gen:
                print(chunk, end='', flush=True)
            print()
        else:
            response = await provider.call(args.prompt, args.model)
            print(json.dumps(response, indent=2))

    if args.test:
        unittest.main(argv=[sys.argv[0]])
    elif args.prompt:
        if not HAS_GEMINI:
            print("Cannot run CLI: google-generativeai SDK not installed.")
        else:
            asyncio.run(main_cli())
    else:
        parser.print_help()