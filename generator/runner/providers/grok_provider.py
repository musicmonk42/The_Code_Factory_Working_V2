# grok_provider.py
"""
grok_provider.py
xAI Grok LLM provider plugin.

This is a "dumb" plugin. All reliability (circuit breaking),
observability (metrics, logging, tracing), and security (redaction)
are handled by the llm_client.py manager that calls this plugin.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, AsyncGenerator, Callable, Dict, List, Union

import aiohttp
import tiktoken
import yaml

# ---- Runner foundation imports ------------------------------------------------
from runner.llm_provider_base import LLMProvider
from runner.runner_config import load_config  # For loading API key in get_provider
from runner.runner_errors import ConfigurationError, LLMError

# --- FIX: Update import to point to central metrics module ---
from runner.runner_metrics import stream_chunk_latency, stream_chunks_total
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# --- END FIX ---
# -------------------------------------------------------------------------------

# Get the logger for this module
logger = logging.getLogger(__name__)

# Metrics are imported from runner_metrics to avoid collision


class GrokProvider(LLMProvider):
    """
    Dumb plugin for xAI Grok.

    Handles the direct API interaction with the Grok API.
    Assumes it is managed by a higher-level client that provides
    logging, metrics, tracing, circuit breaking, and scrubbing.
    """

    name = "grok"

    def __init__(self, api_key: str):
        """
        Initialize the Grok provider with API key validation and initial setup.

        Args:
            api_key (str): The xAI API key.
        """
        super().__init__()

        if not api_key:
            # --- FIX: Pass 'error_code' and 'detail' keywords ---
            raise ConfigurationError(
                detail="GrokProvider initialized without an API key.",
                error_code="CONFIG_INIT_KEY_MISSING",
            )

        self.api_key = api_key
        self.tokenizer = tiktoken.get_encoding("cl100k_base")  # Approximate tokenizer
        self.custom_models: Dict[str, Dict[str, Any]] = {}
        self.pre_hooks: List[Callable[[str], str]] = []
        self.post_hooks: List[Callable[[Any], Any]] = []
        self.load_plugins()  # Initial load

    def load_config(self, file_path: str):
        """
        Load external configuration for model aliases and endpoints from YAML or JSON file.
        """
        if file_path.endswith(".yaml") or file_path.endswith(".yml"):
            with open(file_path, "r") as f:
                config = yaml.safe_load(f)
        elif file_path.endswith(".json"):
            with open(file_path, "r") as f:
                config = json.load(f)
        else:
            raise ValueError("Unsupported config format. Use YAML or JSON.")
        for model_name, details in config.get("models", {}).items():
            self.register_custom_model(
                model_name, details["endpoint"], details.get("headers", {})
            )

    def register_custom_model(
        self, model_name: str, endpoint: str, headers: Dict[str, str] = None
    ):
        """
        Register a custom model with alternative endpoint and headers for extensibility.
        """
        self.custom_models[model_name] = {
            "endpoint": endpoint,
            "headers": headers or {},
        }
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
        logger.info("Plugins loaded (placeholder implementation).")

    @retry(
        retry=retry_if_exception_type(aiohttp.ClientError),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    async def _api_call(
        self,
        endpoint: str,
        headers: Dict[str, str],
        data: Dict[str, Any],
        stream: bool,
        run_id: str,
    ):
        """
        Internal API call with retry and tracing.
        """
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(endpoint, headers=headers, json=data) as resp:
                    if resp.status != 200:
                        error_msg = f"API error: {resp.status} - {await resp.text()}"
                        logger.error(error_msg, extra={"run_id": run_id})
                        # Raise LLMError here to ensure the top-level handler catches it
                        raise LLMError(
                            detail=error_msg,
                            provider=self.name,
                            error_code="LLM_PROVIDER_ERROR",
                        )
                    if stream:
                        # Return the response object itself for streaming
                        return resp
                    # For non-streaming, read the JSON
                    return await resp.json()
            except aiohttp.ClientError as e:
                # Retriable network error
                logger.warning(
                    f"Grok API call failed (ClientError): {e}. Retrying...",
                    extra={"run_id": run_id},
                )
                raise  # Re-raise to trigger tenacity retry
            except Exception as e:
                # Non-retriable error
                error_msg = f"Unexpected error during Grok API call: {e}"
                logger.error(error_msg, extra={"run_id": run_id})
                raise LLMError(
                    detail=error_msg,
                    provider=self.name,
                    error_code="LLM_PROVIDER_ERROR",
                ) from e

    async def call(
        self, prompt: str, model: str, stream: bool = False, **kwargs
    ) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
        """
        Call the LLM with reliability, security, and observability features.
        The prompt is assumed to be pre-scrubbed by the llm_client.
        """

        # Apply any registered pre-processing hooks
        processed_prompt = prompt
        for hook in self.pre_hooks:
            processed_prompt = hook(processed_prompt)

        messages = [{"role": "user", "content": processed_prompt}]

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = {"model": model, "messages": messages, "stream": stream, **kwargs}
        endpoint = "https://api.x.ai/v1/chat/completions"

        if model in self.custom_models:
            custom = self.custom_models[model]
            endpoint = custom["endpoint"]
            headers.update(custom["headers"])

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
                            if line.startswith(b"data: "):
                                # Strip leading "data: " and decode
                                try:
                                    # Handle [DONE] signal
                                    if line[6:].strip() == b"[DONE]":
                                        break
                                    chunk_data = json.loads(line[6:])
                                except json.JSONDecodeError:
                                    logger.warning(
                                        f"Failed to decode stream chunk: {line.decode()}",
                                        extra={"run_id": run_id},
                                    )
                                    continue

                                if "choices" in chunk_data and chunk_data["choices"][0][
                                    "delta"
                                ].get("content"):
                                    chunk_text = chunk_data["choices"][0]["delta"][
                                        "content"
                                    ]
                                    yield chunk_text
                                    partial_response += chunk_text

                                    # Keep local, plugin-specific stream metrics
                                    chunk_output_tokens = await self.count_tokens(
                                        chunk_text, model
                                    )
                                    output_tokens += chunk_output_tokens
                                    chunk_latency = time.time() - chunk_start
                                    stream_chunk_latency.labels(model=model).observe(
                                        chunk_latency
                                    )
                                    stream_chunks_total.labels(model=model).inc()
                                    chunk_start = time.time()
                    except Exception as e:
                        # Let the llm_client handle logging this error
                        raise LLMError(
                            detail=f"Error during streaming: {e}", provider=self.name
                        ) from e

                return gen()
            else:
                content = response["choices"][0]["message"]["content"]

                # Apply post-processing hooks
                stamped_response = {"content": content, "model": model}
                for hook in self.post_hooks:
                    stamped_response = hook(stamped_response)

                # Return the simple, hook-modified dictionary
                return {
                    "content": stamped_response["content"],
                    "model": stamped_response["model"],
                }

        except Exception as e:
            # Let the llm_client handle logging, metrics, and circuit breaking
            if isinstance(e, LLMError):
                raise  # Re-raise errors we've already translated
            else:
                # Wrap unexpected errors
                raise LLMError(
                    detail=f"Unexpected error in call: {e}", provider=self.name
                ) from e

    async def count_tokens(self, text: str, model: str) -> int:
        """
        Count tokens using approximate tokenizer (cl100k_base).
        """
        # Kept async signature for consistency
        return await asyncio.to_thread(lambda: len(self.tokenizer.encode(text)))

    async def health_check(self) -> bool:
        """
        Perform health check and update metrics/logs.
        """
        if not self.api_key:
            return False

        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                async with session.get(
                    "https://api.x.ai/v1/models", headers=headers, timeout=5
                ) as resp:
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
    API_KEY = config.llm_provider_api_key or os.getenv("GROK_API_KEY")

    if not API_KEY:
        # This error will be caught by the llm_plugin_manager
        # --- FIX: Pass 'error_code' and 'detail' keywords ---
        raise ConfigurationError(
            detail="GROK_API_KEY environment variable or runner config not set.",
            error_code="CONFIG_LOAD_KEY_MISSING",
        )

    return GrokProvider(api_key=API_KEY)
