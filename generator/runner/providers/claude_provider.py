# claude_provider.py
"""
claude_provider.py
Anthropic Claude LLM provider plugin.

This is a "dumb" plugin. All reliability (circuit breaking),
observability (metrics, logging, tracing), and security (redaction)
are handled by the llm_client.py manager that calls this plugin.

Extension:
- Custom Models: Use `register_custom_model` to add runtime configurations for custom endpoints.
- Hooks: Use `add_pre_hook` and `add_post_hook` for custom transformations.
"""

import os
import logging
import yaml
import json
import asyncio
from typing import Union, Dict, Any, AsyncGenerator, Callable, List, Optional
import aiohttp

# --- Conditional SDK Import ---
try:
    # --- FIX: Import synchronous Anthropic client for count_tokens ---
    from anthropic import AsyncAnthropic, Anthropic, AnthropicError, AuthenticationError, RateLimitError, APIConnectionError
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False
    # Define dummy types for the class to load without crashing
    class Anthropic: pass # Dummy
    class AnthropicError(Exception): pass
    class AuthenticationError(AnthropicError): pass
    class RateLimitError(AnthropicError): pass
    class APIConnectionError(AnthropicError): pass

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ---- Runner foundation imports ------------------------------------------------
from runner.llm_provider_base import LLMProvider
from runner.runner_errors import LLMError, ConfigurationError
from runner.runner_config import load_config # For loading API key in get_provider
# -------------------------------------------------------------------------------

# Get the logger for this module
logger = logging.getLogger(__name__)

# Cost awareness: Pricing per model (USD per token).
PRICING = {
    'claude-3-opus-20240229': {'input': 0.000015, 'output': 0.000075},
    'claude-3-sonnet-20240229': {'input': 0.000003, 'output': 0.000015},
    'claude-3-haiku-20240307': {'input': 0.00000025, 'output': 0.00000125},
    'claude-3.5-sonnet-20240620': {'input': 0.000003, 'output': 0.000015},
    'claude-3.5-haiku-20241022': {'input': 0.00000025, 'output': 0.00000125},
}

class ClaudeProvider(LLMProvider):
    """
    ClaudeProvider: Anthropic Claude LLM provider plugin.
    
    Handles the direct API interaction with Anthropic.
    Assumes it is managed by a higher-level client that provides
    logging, metrics, tracing, circuit breaking, and scrubbing.
    """
    
    name = "claude"

    def __init__(self, api_key: str):
        """
        Initialize the Claude provider.
        
        Args:
            api_key (str): The Anthropic API key.
        """
        
        if not HAS_ANTHROPIC:
             # --- FIX: Pass 'error_code' and 'detail' keyword arguments ---
             raise ConfigurationError(
                 error_code="CONFIG_SDK_MISSING", 
                 detail="Anthropic SDK not found. Please run 'pip install anthropic'."
             )
        
        if not api_key:
            # --- FIX: Pass 'error_code' and 'detail' keyword arguments ---
            raise ConfigurationError(
                error_code="CONFIG_INIT_KEY_MISSING", 
                detail="ClaudeProvider initialized without an API key."
            )
        
        self.api_key = api_key
        self.client = AsyncAnthropic(api_key=self.api_key)
        # --- FIX: Add sync client for count_tokens ---
        self.sync_client = Anthropic(api_key=self.api_key) 
        
        self.custom_models: Dict[str, Dict[str, Any]] = {}
        self.pre_hooks: List[Callable[[str], str]] = []
        self.post_hooks: List[Callable[[Dict[str, Any]], Dict[str, Any]]] = []

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
        """
        Register a custom model alias for a different endpoint.
        """
        self.custom_models[model_name] = {'endpoint': endpoint, 'headers': headers or {}}

    def add_pre_hook(self, hook: Callable[[str], str]):
        """
        Add a pre-processing hook for prompts.
        """
        self.pre_hooks.append(hook)

    def add_post_hook(self, hook: Callable[[Dict[str, Any]], Dict[str, Any]]):
        """
        Add a post-processing hook for responses.
        """
        self.post_hooks.append(hook)

    def _apply_pre_hooks(self, prompt: str) -> str:
        for hook in self.pre_hooks:
            prompt = hook(prompt)
        return prompt

    def _apply_post_hooks(self, response: Dict[str, Any]) -> Dict[str, Any]:
        for hook in self.post_hooks:
            response = hook(response)
        return response

    # --- FIX: Only retry on transient SDK errors, not all Exceptions ---
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type((RateLimitError, APIConnectionError)))
    async def _api_call(self, model: str, processed_prompt: str, stream: bool, **kwargs):
        """
        Internal, retriable API call.
        Translates SDK-specific errors to general LLMError.
        """
        try:
            if model in self.custom_models:
                # --- Custom Endpoint Logic ---
                custom = self.custom_models[model]
                async with aiohttp.ClientSession() as session:
                    headers = {"x-api-key": self.api_key, **custom['headers']}
                    payload = {"model": model, "max_tokens": 4096, "messages": [{"role": "user", "content": processed_prompt}]}
                    # Note: Custom endpoint logic assumes non-streaming for simplicity
                    if stream:
                        logger.warning(f"Streaming not fully supported for custom Claude endpoint '{model}'. Returning full response.")
                    
                    async with session.post(custom['endpoint'], json=payload, headers=headers) as resp:
                        if resp.status != 200:
                            raise APIConnectionError(f"Custom endpoint error: {resp.status} - {await resp.text()}")
                        response = await resp.json()
                        return response, False # (response, is_stream=False)
            else:
                # --- Standard Anthropic SDK Logic ---
                if stream:
                    return await self.client.messages.create(model=model, max_tokens=4096, messages=[{"role": "user", "content": processed_prompt}], stream=True, **kwargs), True
                else:
                    return await self.client.messages.create(model=model, max_tokens=4096, messages=[{"role": "user", "content": processed_prompt}], **kwargs), False

        # --- SDK Error Translation ---
        except AuthenticationError as e:
            raise LLMError(detail="Authentication failed: Invalid or missing API key.", provider=self.name) from e
        except RateLimitError as e:
            raise LLMError(detail="Rate limit exceeded. Check Anthropic dashboard for limits.", provider=self.name) from e
        except APIConnectionError as e:
            raise LLMError(detail="Connection error. Check network or API endpoint status.", provider=self.name) from e
        except AnthropicError as e:
            error_type = type(e).__name__
            raise LLMError(detail=f"Anthropic API error ({error_type}): {str(e)}", provider=self.name) from e
        except Exception as e:
            # Catch-all for unexpected errors during the call
            raise LLMError(detail=f"Unexpected error in Claude SDK: {str(e)}", provider=self.name) from e

    async def call(self, prompt: str, model: str, stream: bool = False, **kwargs) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
        """
        Call the Claude API with the given prompt and model.
        The prompt is assumed to be pre-scrubbed by the llm_client.
        """
        
        # Apply any registered pre-processing hooks
        processed_prompt = self._apply_pre_hooks(prompt)

        try:
            api_response, is_stream = await self._api_call(model, processed_prompt, stream, **kwargs)
            
            if stream and is_stream:
                # --- Streaming Logic ---
                async def gen():
                    try:
                        async for chunk in api_response:
                            if chunk.type == 'content_block_delta':
                                yield chunk.delta.text
                    except Exception as e:
                        # Let the llm_client handle logging this error
                        raise LLMError(detail=f"Error during streaming: {e}", provider=self.name) from e
                return gen()
            else:
                # --- Non-Streaming Logic ---
                if is_stream:
                    # Custom endpoint that didn't support streaming (but was flagged as stream)
                    content = api_response.get('content', [{}])[0].get('text', '')
                else:
                    # Standard SDK response
                    content = api_response.content[0].text
                
                # Apply post-processing hooks
                result = {"content": content, "model": model}
                result = self._apply_post_hooks(result)
                
                # Return the simple, hook-modified dictionary
                return {"content": result["content"], "model": result["model"]}
        
        except Exception as e:
            # Let the llm_client handle logging, metrics, and circuit breaking
            if isinstance(e, LLMError):
                raise  # Re-raise errors we've already translated
            else:
                # Wrap unexpected errors
                raise LLMError(detail=f"Unexpected error in call: {e}", provider=self.name) from e

    async def count_tokens(self, text: str, model: str) -> int:
        """
        Count tokens using Anthropic's client.
        """
        try:
            # --- FIX: Use the synchronous client for count_tokens ---
            return await asyncio.to_thread(self.sync_client.count_tokens, text)
        except Exception as e:
            logger.warning(f"Claude token counting failed: {str(e)}. Approximating tokens.")
            # Fallback to a simple approximation
            return len(text.split())

    async def health_check(self) -> bool:
        """
        Perform a health check against the Anthropic API.
        """
        if not self.api_key:
            return False
            
        try:
            # Use aiohttp for a lightweight, non-SDK health check
            async with aiohttp.ClientSession() as session:
                url = "https://api.anthropic.com/v1/models" 
                headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
                
                async with session.get(url, headers=headers, timeout=5) as resp:
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
    API_KEY = config.llm_provider_api_key or os.getenv("CLAUDE_API_KEY")
    
    if not HAS_ANTHROPIC:
        logger.error("Anthropic SDK not found. Skipping ClaudeProvider.")
        # --- FIX: Pass 'error_code' and 'detail' keyword arguments ---
        raise ConfigurationError(
            error_code="CONFIG_SDK_MISSING", 
            detail="Anthropic SDK not found. Please run 'pip install anthropic'."
        )
        
    if not API_KEY:
        # This error will be caught by the llm_plugin_manager
        # --- FIX: Pass 'error_code' and 'detail' keyword arguments ---
        raise ConfigurationError(
            error_code="CONFIG_LOAD_KEY_MISSING", 
            detail="CLAUDE_API_KEY environment variable or runner config not set."
        )
        
    return ClaudeProvider(api_key=API_KEY)

# --- Test/Example Usage ---
if __name__ == "__main__":
    import argparse
    import asyncio
    from unittest.mock import AsyncMock, patch, MagicMock
    import unittest

    # Setup basic logging for standalone execution
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    class TestClaudeProvider(unittest.IsolatedAsyncioTestCase):
        
        @patch.dict(os.environ, {'CLAUDE_API_KEY': 'test-key'})
        @patch('runner.runner_config.load_config', return_value=MagicMock(llm_provider_api_key=''))
        async def test_call_non_stream(self, mock_config_load):
            if not HAS_ANTHROPIC: self.skipTest("Anthropic SDK not installed")
            
            provider = ClaudeProvider(api_key="test-key")
            
            with patch('anthropic.AsyncAnthropic.messages.create', new_callable=AsyncMock) as mock_create:
                mock_response_obj = MagicMock()
                mock_response_obj.content = [MagicMock(text="Claude response")]
                mock_create.return_value = mock_response_obj
                
                with patch.object(provider.sync_client, 'count_tokens', new_callable=MagicMock, return_value=3):
                    result = await provider.call("Hello", "claude-3-haiku-20240307")
                    self.assertIn("content", result)
                    self.assertEqual(result["content"], "Claude response")
                    self.assertIn("model", result)

        @patch.dict(os.environ, {'CLAUDE_API_KEY': 'test-key'})
        @patch('runner.runner_config.load_config', return_value=MagicMock(llm_provider_api_key=''))
        async def test_count_tokens(self, mock_config_load):
            if not HAS_ANTHROPIC: self.skipTest("Anthropic SDK not installed")
            provider = ClaudeProvider(api_key="test-key")
            # --- FIX: Patch the sync_client, not the async one ---
            with patch.object(provider.sync_client, 'count_tokens', return_value=3) as mock_count:
                tokens = await provider.count_tokens("Hello world", "claude-3-haiku-20240307")
                self.assertEqual(tokens, 3)
                mock_count.assert_called_once()
                
        @patch.dict(os.environ, {'CLAUDE_API_KEY': 'test-key'})
        @patch('runner.runner_config.load_config', return_value=MagicMock(llm_provider_api_key=''))
        async def test_health_check(self, mock_config_load):
            if not HAS_ANTHROPIC: self.skipTest("Anthropic SDK not installed")
            provider = ClaudeProvider(api_key="test-key")
            with patch('aiohttp.ClientSession.get', new_callable=AsyncMock) as mock_get:
                mock_get.return_value.__aenter__.return_value.status = 200
                self.assertTrue(await provider.health_check())

    parser = argparse.ArgumentParser(description="ClaudeProvider CLI")
    parser.add_argument('--prompt', type=str, required=False, help='Prompt text')
    parser.add_argument('--model', type=str, default='claude-3-haiku-20240307', help='Claude model name')
    parser.add_narrow_to_wide('--stream', action='store_true', help='Stream response')
    parser.add_argument('--test', action='store_true', help="Run tests")
    args, unknown = parser.parse_known_args() # Use parse_known_args for unittest compatibility

    async def main_cli():
        # This init will fail if CLAUDE_API_KEY is not set
        try:
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
            result = await provider.call(args.prompt, args.model)
            print(json.dumps(result, indent=2))

    if args.test:
        # Pass only the script name to unittest.main
        unittest.main(argv=[sys.argv[0]])
    elif args.prompt:
        if not HAS_ANTHROPIC:
            print("Cannot run CLI: Anthropic SDK not installed.")
        else:
            asyncio.run(main_cli())
    else:
        parser.print_help()