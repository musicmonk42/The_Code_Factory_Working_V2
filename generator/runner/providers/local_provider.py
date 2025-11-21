# runner/llm_client_providers/local_provider.py
"""
local_provider.py
Local LLM provider plugin (e.g., Ollama).

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
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ---- Runner foundation imports ------------------------------------------------
# [FIX] Import base class
from runner.llm_provider_base import LLMProvider
from runner.runner_errors import LLMError, ConfigurationError
from runner.runner_config import load_config # For loading API key in get_provider
# -------------------------------------------------------------------------------

# Get the logger for this module
logger = logging.getLogger(__name__)

# Metrics initialization (Prometheus) - Retain local stream metrics
# These are plugin-specific and not managed by the central llm_client
from prometheus_client import Counter, Histogram
# --- FIX: Import shared metrics from runner_metrics ---
from runner.runner_metrics import stream_chunks_total, stream_chunk_latency
# --- END FIX ---

# --- FIX: REMOVE LOCAL DEFINITIONS ---
# stream_chunks_total = Counter('llm_stream_chunks_total', 'Total number of stream chunks', ['model'])
# stream_chunk_latency = Histogram('llm_stream_chunk_latency_seconds', 'Latency per stream chunk in seconds', ['model'])
# --- END FIX ---

# Cost awareness: For local models, cost is typically 0, but allow custom pricing
PRICING: Dict[str, Dict[str, float]] = {}  # User can populate: model: {'input': cost_per_token, 'output': cost_per_token}


class LocalProvider(LLMProvider):
    """
    Dumb plugin for Local LLMs (e.g., Ollama).
    
    Handles the direct API interaction with a local HTTP endpoint.
    Assumes it is managed by a higher-level client that provides
    logging, metrics, tracing, circuit breaking, and scrubbing.
    """
    name = "local"

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the Local provider with initial setup.
        
        Args:
            api_key (Optional[str]): A bearer token or API key, if the local
                                     server requires one.
        """
        super().__init__()
        self.api_key = api_key # Retained for local servers with auth
        self.custom_models: Dict[str, Dict[str, Any]] = {
            'llama2': {'endpoint': 'http://localhost:11434/api/generate', 'headers': {}},
            'mistral': {'endpoint': 'http://localhost:11434/api/generate', 'headers': {}}
        }  # Default supported models
        self.pre_hooks: List[Callable[[str], str]] = []
        self.post_hooks: List[Callable[[Any], Any]] = []
        self.load_plugins()  # Initial load

    def load_config(self, file_path: str):
        """
        Load external configuration for model aliases and endpoints from YAML or JSON file.
        """
        if not (file_path.endswith('.yaml') or file_path.endswith('.yml') or file_path.endswith('.json')):
             raise ValueError("Unsupported config format. Use YAML or JSON.")

        with open(file_path, 'r') as f:
            if file_path.endswith('.yaml') or file_path.endswith('.yml'):
                config = yaml.safe_load(f)
            else: # .json
                config = json.load(f)

        for model_name, details in config.get('models', {}).items():
            # [FIX] Use the correct arguments for register_custom_model
            self.register_custom_model(model_name, details)

    def register_custom_model(self, model_name: str, config: Dict[str, Any]):
        """
        Register a custom model with endpoint, headers, and optional token_counter.
        
        Args:
            model_name: Name of the model
            config: Dictionary with 'endpoint', 'headers' (optional), and 'token_counter' (optional)
        """
        self.custom_models[model_name] = {
            'endpoint': config.get('endpoint', 'http://localhost:11434/api/generate'),
            'headers': config.get('headers', {})
        }
        # Store token_counter if provided
        if 'token_counter' in config:
            self.custom_models[model_name]['token_counter'] = config['token_counter']
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

    def load_plugins(self, file_path: str = None):
        """
        Auto-discover and hot-reload plugins/extensions from YAML file.
        
        Args:
            file_path: Optional path to plugin configuration YAML file
        """
        if file_path is None:
            # Default plugin file location
            file_path = os.path.join(os.path.dirname(__file__), 'local_plugins.yaml')
        
        if not os.path.exists(file_path):
            logger.info("Plugins loaded (placeholder implementation).")
            return
        
        try:
            with open(file_path, 'r') as f:
                config = yaml.safe_load(f)
            
            # Load custom models
            for model_name, model_config in config.get('models', {}).items():
                self.register_custom_model(model_name, model_config)
            
            # Load pre-hooks
            for hook_path in config.get('pre_hooks', []):
                try:
                    module_name, func_name = hook_path.rsplit('.', 1)
                    import importlib
                    module = importlib.import_module(module_name)
                    hook = getattr(module, func_name)
                    self.add_pre_hook(hook)
                except Exception as e:
                    logger.warning(f"Failed to load pre-hook {hook_path}: {e}")
            
            # Load post-hooks
            for hook_path in config.get('post_hooks', []):
                try:
                    module_name, func_name = hook_path.rsplit('.', 1)
                    import importlib
                    module = importlib.import_module(module_name)
                    hook = getattr(module, func_name)
                    self.add_post_hook(hook)
                except Exception as e:
                    logger.warning(f"Failed to load post-hook {hook_path}: {e}")
            
            logger.info(f"Plugins loaded from {file_path}")
        except Exception as e:
            logger.error(f"Error loading local plugins from {file_path}: {e}")

    @retry(retry=retry_if_exception_type(aiohttp.ClientError), stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def _api_call(self, endpoint: str, headers: Dict[str, str], data: Dict[str, Any], stream: bool, run_id: str):
        """
        Internal API call with retry and error translation.
        """
        async with aiohttp.ClientSession(headers=headers) as session:
            try:
                # Use a reasonable timeout for local connections
                async with session.post(endpoint, json=data, timeout=60) as resp:
                    if resp.status != 200:
                        # [FIX] Handle 429 Rate Limit specifically to trigger retry
                        if resp.status == 429:
                            error_msg = f"API error: {resp.status} - Rate Limited. {await resp.text()}"
                            logger.warning(error_msg, extra={'run_id': run_id})
                            raise aiohttp.ClientError(error_msg) # Raise to trigger tenacity retry
                        
                        error_msg = f"API error: {resp.status} - {await resp.text()}"
                        logger.error(error_msg, extra={'run_id': run_id})
                        raise LLMError(detail=error_msg, provider=self.name, error_code="LLM_PROVIDER_ERROR")
                    # Return the response object itself to be awaited in the stream
                    return resp
            except aiohttp.ClientConnectorError as e:
                # This is the most common error (e.g., Ollama not running)
                error_msg = f"ClientConnectorError: {str(e)}. Is the local server (e.g., Ollama) running at {endpoint}?"
                logger.error(error_msg, extra={'run_id': run_id})
                raise LLMError(detail=error_msg, provider=self.name, error_code="LLM_PROVIDER_ERROR") from e
            except aiohttp.ClientError as e:
                # Retriable network error
                error_msg = f"Client error: {str(e)}. Check server status or endpoint configuration."
                logger.warning(error_msg, extra={'run_id': run_id})
                raise # Re-raise to trigger tenacity retry
            except asyncio.TimeoutError as e:
                error_msg = "API call timed out. The local model may be too slow or stuck."
                logger.error(error_msg, extra={'run_id': run_id})
                raise LLMError(detail=error_msg, provider=self.name, error_code="LLM_PROVIDER_ERROR") from e
            except Exception as e:
                # Non-retriable error
                error_msg = f"Unexpected error during local API call: {e}"
                logger.error(error_msg, extra={'run_id': run_id})
                raise LLMError(detail=error_msg, provider=self.name, error_code="LLM_PROVIDER_ERROR") from e


    async def call(self, prompt: str, model: str, stream: bool = False, **kwargs) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
        """
        Call the Local LLM API with the given prompt and model.
        The prompt is assumed to be pre-scrubbed by the llm_client.
        """
        if model is None:
            raise ValueError("Model name is required")
        
        # Apply any registered pre-processing hooks
        processed_prompt = prompt
        for hook in self.pre_hooks:
            processed_prompt = hook(processed_prompt)

        # This assumes an Ollama-compatible API
        data = {"model": model, "prompt": processed_prompt, "stream": stream}
        data.update(kwargs)  # Support additional Ollama options
        
        # Get endpoint and headers from custom models, or use default
        model_config = self.custom_models.get(model, {'endpoint': 'http://localhost:11434/api/generate', 'headers': {}})
        endpoint = model_config['endpoint']
        headers = model_config.get('headers', {}).copy() # [FIX] Use .get() for headers
        headers.update({"Content-Type": "application/json"})
        
        # Add API Key if it exists in config/env (for local servers with auth)
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'

        # A run_id just for this call, in case _api_call needs it for logging retries
        run_id = str(uuid.uuid4())[:8]

        try:
            response = await self._api_call(endpoint, headers, data, stream, run_id)

            if stream:
                async def gen():
                    partial_response = ""
                    chunk_start = time.time()
                    output_tokens = 0
                    try:
                        async for line in response.content:
                            if line.strip():
                                try:
                                    chunk = json.loads(line)
                                except json.JSONDecodeError:
                                    logger.warning(f"Failed to decode stream chunk: {line.decode()}", extra={'run_id': run_id})
                                    continue

                                chunk_text = chunk.get('response', '')
                                if chunk_text:
                                    yield chunk_text
                                    partial_response += chunk_text
                                    
                                    # Keep local, plugin-specific stream metrics
                                    chunk_output_tokens = await self.count_tokens(chunk_text, model)
                                    output_tokens += chunk_output_tokens
                                    chunk_latency = time.time() - chunk_start
                                    stream_chunk_latency.labels(model=model).observe(chunk_latency)
                                    stream_chunks_total.labels(model=model).inc()
                                    chunk_start = time.time()
                    except Exception as e:
                        # Let the llm_client handle logging this error
                        raise LLMError(detail=f"Error during streaming: {e}", provider=self.name) from e
                return gen()
            else:
                lines = await response.text()
                # Non-streaming response may contain multiple JSON objects (Ollama format)
                # Parse each line separately to handle multiple JSON objects correctly
                responses = []
                for line in lines.splitlines():
                    if line.strip():
                        try:
                            responses.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                
                # Extract content from all response objects
                if responses:
                    content = "".join([r.get('response', '') for r in responses])
                else:
                    # Fallback: try parsing the entire response as single JSON
                    try:
                        final_response_obj = json.loads(lines)
                        content = final_response_obj.get('response', '')
                    except json.JSONDecodeError:
                        logger.warning("Failed to parse non-streaming response", extra={'run_id': run_id})
                        content = ""
                
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
        Approximate token count.
        [FIX] Now correctly uses a custom 'token_counter' if available.
        """
        model_config = self.custom_models.get(model)
        if model_config and 'token_counter' in model_config:
            counter = model_config['token_counter']
            try:
                return await asyncio.to_thread(counter, text)
            except Exception as e:
                logger.warning(f"Custom token_counter for model '{model}' failed: {e}. Falling back to default.")

        # Simple fallback: count words as rough approximation
        return len(text.split())

    async def health_check(self) -> bool:
        """
        Perform health check against the default local endpoint.
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Default Ollama root endpoint check
                async with session.get("http://localhost:11434", timeout=3) as resp:
                    return resp.status == 200
        except Exception:
            # This is expected if the server isn't running
            return False

# --- Plugin Manager Entry Point ---
def get_provider():
    """
    Plugin manager entry point.
    Loads the (optional) API key from config/env and instantiates the provider.
    
    Raises:
        ConfigurationError: If no API key is found (when required)
    """
    config = load_config()
    # API_KEY is optional for most local setups
    API_KEY = config.llm_provider_api_key or os.getenv("LOCAL_API_KEY")
    
    # For local providers, we typically don't require an API key
    # But if you want to enforce it, uncomment the following:
    # if not API_KEY:
    #     raise ConfigurationError("No API key found for Local provider. Set LOCAL_API_KEY env var or configure llm_provider_api_key.")
        
    return LocalProvider(api_key=API_KEY)