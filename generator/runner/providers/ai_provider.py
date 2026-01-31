# ai_provider.py
"""
OpenAI LLM provider plugin.
This plugin provides integration with OpenAI's API for LLM calls.
It is designed to be called by the central llm_client, which handles
observability, error handling, security, and cost estimation.
"""

import asyncio
import os  # <-- ADDED
from typing import Any, AsyncGenerator, Dict, Union

import aiohttp
from openai import (  # <-- ADDED SDK ERRORS
    APIConnectionError,
    AsyncOpenAI,
    AuthenticationError,
    OpenAIError,
    RateLimitError,
)
from generator.runner.llm_provider_base import LLMProvider
from generator.runner.runner_config import load_config  # <-- ADDED
from generator.runner.runner_errors import ConfigurationError, LLMError  # <-- ADDED
from tiktoken import Encoding, get_encoding


class OpenAIProvider(LLMProvider):
    """
    OpenAI LLM Provider.

    Handles the direct API interaction with OpenAI.
    Assumes it is managed by a higher-level client that provides
    logging, metrics, tracing, circuit breaking, and scrubbing.
    """

    # Provider name for metrics and logging
    name = "openai"

    def __init__(self, api_key: str):
        """
        Initialize the OpenAI provider.
        """

        if not api_key:
            # This check is good, but get_provider() will also check
            # --- FIX: Pass 'error_code' and 'detail' keyword arguments ---
            raise ConfigurationError(
                error_code="CONFIG_INIT_KEY_MISSING",
                detail="OpenAIProvider initialized without an API key.",
            )

        self.api_key = api_key

        # Initialize client with the API key
        self.client = AsyncOpenAI(api_key=self.api_key)
        self.tokenizer_cache: Dict[str, Encoding] = {}
        self.custom_headers: Dict[str, str] = {}
        self.custom_endpoint: str = None
        self.registered_models: set = {"gpt-3.5-turbo", "gpt-4", "gpt-4o"}

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
            if "gpt-4" in model or "gpt-3.5" in model or "gpt-4o" in model:
                encoding_name = "cl100k_base"
            else:
                encoding_name = "p50k_base"  # Older models

            self.tokenizer_cache[model] = get_encoding(encoding_name)
        return self.tokenizer_cache[model]

    async def _api_call(self, model: str, messages: list, stream: bool, **kwargs):
        """
        Internal API call with headers and endpoint handling.
        Translates SDK-specific errors into LLMErrors.
        """
        if self.custom_endpoint:
            kwargs["base_url"] = self.custom_endpoint
        if self.custom_headers:
            kwargs["extra_headers"] = self.custom_headers

        try:
            if stream:
                return await self.client.chat.completions.create(
                    model=model, messages=messages, stream=True, **kwargs
                )
            else:
                return await self.client.chat.completions.create(
                    model=model, messages=messages, **kwargs
                )

        # --- Translate SDK-specific errors to general LLMErrors ---
        except AuthenticationError as e:
            raise LLMError(
                detail="Authentication failed: Invalid or missing API key.",
                provider=self.name,
            ) from e
        except RateLimitError as e:
            raise LLMError(
                detail="Rate limit exceeded. Check OpenAI dashboard for limits.",
                provider=self.name,
            ) from e
        except APIConnectionError as e:
            raise LLMError(
                detail="Connection error. Check network or custom endpoint.",
                provider=self.name,
            ) from e
        except OpenAIError as e:
            raise LLMError(
                detail=f"OpenAI API error: {str(e)}", provider=self.name
            ) from e
        except Exception as e:
            raise LLMError(
                detail=f"Unexpected error in OpenAI SDK: {str(e)}", provider=self.name
            ) from e

    async def call(
        self, prompt: str, model: str, stream: bool = False, **kwargs
    ) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
        """
        Call the OpenAI API with prompt and model.
        """
        if model not in self.registered_models:
            raise ValueError(
                f"Model {model} not registered. Available models: {self.registered_models}"
            )

        messages = [{"role": "user", "content": prompt}]

        if stream:

            async def gen():
                api_response = await self._api_call(
                    model, messages, stream=True, **kwargs
                )

                try:
                    async for chunk in api_response:
                        content = chunk.choices[0].delta.content or ""
                        yield content
                except Exception as e:
                    raise LLMError(
                        detail=f"Error during streaming: {e}", provider=self.name
                    ) from e

            return gen()
        else:
            completion = await self._api_call(model, messages, stream=False, **kwargs)

            if not completion.choices:
                raise LLMError(detail="Empty response from OpenAI", provider=self.name)
            content = completion.choices[0].message.content or ""

            # --- FIX: RETURN A DICTIONARY ---
            return {"content": content, "model": model}

    async def count_tokens(self, text: str, model: str) -> int:
        """
        Count tokens using model-specific tokenizer.
        """
        tokenizer = self._get_tokenizer(model)
        return await asyncio.to_thread(lambda: len(tokenizer.encode(text)))

    async def health_check(self) -> bool:
        """
        Health check.
        """
        try:
            url = (
                f"{self.client.base_url}/models"
                if self.client.base_url
                else "https://api.openai.com/v1/models"
            )
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                headers.update(self.custom_headers)
                async with session.get(url, headers=headers, timeout=5) as resp:
                    return resp.status == 200
        except Exception:
            return False


# --- FIX: ADD THIS FUNCTION ---
def get_provider():
    """
    Plugin manager entry point.
    Loads the API key from config/env and instantiates the provider.
    """
    config = load_config()
    API_KEY = config.llm_provider_api_key or os.getenv("OPENAI_API_KEY")

    if not API_KEY:
        # This error will be caught by the llm_plugin_manager
        # --- FIX: Pass 'error_code' and 'detail' keyword arguments ---
        raise ConfigurationError(
            error_code="CONFIG_LOAD_KEY_MISSING",
            detail="OPENAI_API_KEY environment variable or runner config not set.",
        )

    return OpenAIProvider(api_key=API_KEY)
