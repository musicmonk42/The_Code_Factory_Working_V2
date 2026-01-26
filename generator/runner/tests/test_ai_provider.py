# test_ai_provider.py
"""
test_ai_provider.py
~~~~~~~~~~~~~~~~~~~
Industry-grade test suite for ``ai_provider.py`` (OpenAI) (≥ 90 % coverage).

Run with:
    pytest generator/runner/tests/test_ai_provider.py -vv
    # coverage:
    pytest --cov=runner/providers/ai_provider \
           --cov-report=term-missing \
           generator/runner/tests/test_ai_provider.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

# Make the *runner* package importable by adding generator/ to sys.path
GENERATOR_ROOT = (
    Path(__file__).resolve().parents[2]
)  # generator/runner/tests -> generator/
if str(GENERATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(GENERATOR_ROOT))

from runner.providers.ai_provider import OpenAIProvider, get_provider  # type: ignore
from runner.runner_config import RunnerConfig  # type: ignore
from runner.runner_errors import ConfigurationError, LLMError  # type: ignore


# Fixtures
@pytest.fixture
def provider() -> OpenAIProvider:
    """Fresh provider with a dummy key."""
    return OpenAIProvider(api_key="test-key-12345")


@pytest.fixture
def mock_openai_response():
    """Mock OpenAI API response object."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "Hello from OpenAI"
    return mock_resp


@pytest.fixture
def mock_openai_stream():
    """Mock OpenAI streaming response."""

    async def mock_stream():
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta.content = "chunk1"

        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta.content = "chunk2"

        yield chunk1
        yield chunk2

    return mock_stream()


# 1. Initialization & configuration
def test_init_with_key() -> None:
    p = OpenAIProvider(api_key="test-key")
    assert p.api_key == "test-key"
    assert p.name == "openai"
    assert p.client is not None


def test_init_without_key() -> None:
    with pytest.raises(ConfigurationError):
        OpenAIProvider(api_key=None)


def test_init_with_empty_key() -> None:
    with pytest.raises(ConfigurationError):
        OpenAIProvider(api_key="")


def test_register_custom_headers(provider: OpenAIProvider) -> None:
    provider.register_custom_headers({"X-Custom": "value"})
    assert "X-Custom" in provider.custom_headers
    assert provider.custom_headers["X-Custom"] == "value"


def test_register_custom_endpoint(provider: OpenAIProvider) -> None:
    custom_url = "https://custom.openai.endpoint.com/v1"
    provider.register_custom_endpoint(custom_url)
    assert provider.custom_endpoint == custom_url

    # --- FIX: The client.base_url is an httpx.URL object with a trailing slash ---
    assert str(provider.client.base_url) == custom_url + "/"


def test_register_model(provider: OpenAIProvider) -> None:
    provider.register_model("custom-gpt-5")
    assert "custom-gpt-5" in provider.registered_models


# 2. Tokenizer
def test_get_tokenizer_gpt4(provider: OpenAIProvider) -> None:
    tokenizer = provider._get_tokenizer("gpt-4")
    assert tokenizer is not None
    assert "gpt-4" in provider.tokenizer_cache


def test_get_tokenizer_gpt35(provider: OpenAIProvider) -> None:
    tokenizer = provider._get_tokenizer("gpt-3.5-turbo")
    assert tokenizer is not None


def test_get_tokenizer_unknown_model(provider: OpenAIProvider) -> None:
    tokenizer = provider._get_tokenizer("unknown-model")
    assert tokenizer is not None  # Should use fallback


def test_get_tokenizer_caches(provider: OpenAIProvider) -> None:
    tok1 = provider._get_tokenizer("gpt-4")
    tok2 = provider._get_tokenizer("gpt-4")
    assert tok1 is tok2  # Should return same cached instance


# 3. Token counting
@pytest.mark.asyncio
async def test_count_tokens_basic(provider: OpenAIProvider) -> None:
    count = await provider.count_tokens("Hello world", "gpt-4")
    assert count > 0
    assert isinstance(count, int)


@pytest.mark.asyncio
async def test_count_tokens_empty_string(provider: OpenAIProvider) -> None:
    count = await provider.count_tokens("", "gpt-4")
    assert count == 0


# 4. API call method with SDK error translation
@pytest.mark.asyncio
async def test_api_call_non_stream(
    provider: OpenAIProvider, mock_openai_response
) -> None:
    with patch.object(
        provider.client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_openai_response

        result = await provider._api_call(
            "gpt-4", [{"role": "user", "content": "test"}], False
        )
        assert result == mock_openai_response
        mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_api_call_stream(provider: OpenAIProvider, mock_openai_stream) -> None:
    with patch.object(
        provider.client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_openai_stream

        result = await provider._api_call(
            "gpt-4", [{"role": "user", "content": "test"}], True
        )
        # Result should be the stream itself
        chunks = []
        async for chunk in result:
            chunks.append(chunk)
        assert len(chunks) == 2


@pytest.mark.asyncio
async def test_api_call_authentication_error(provider: OpenAIProvider) -> None:
    from openai import AuthenticationError

    with patch.object(
        provider.client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.side_effect = AuthenticationError(
            "Invalid API key", response=MagicMock(), body=None
        )

        with pytest.raises(LLMError) as exc_info:
            await provider._api_call(
                "gpt-4", [{"role": "user", "content": "test"}], False
            )

        assert "Authentication failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_api_call_rate_limit_error(provider: OpenAIProvider) -> None:
    from openai import RateLimitError

    with patch.object(
        provider.client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.side_effect = RateLimitError(
            "Rate limit exceeded", response=MagicMock(), body=None
        )

        with pytest.raises(LLMError) as exc_info:
            await provider._api_call(
                "gpt-4", [{"role": "user", "content": "test"}], False
            )

        assert "Rate limit" in str(exc_info.value)


@pytest.mark.asyncio
async def test_api_call_connection_error(provider: OpenAIProvider) -> None:
    from openai import APIConnectionError

    with patch.object(
        provider.client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        # --- FIX: APIConnectionError requires keyword arguments (message, request) ---
        mock_create.side_effect = APIConnectionError(
            message="Connection failed", request=MagicMock()
        )

        with pytest.raises(LLMError) as exc_info:
            await provider._api_call(
                "gpt-4", [{"role": "user", "content": "test"}], False
            )

        assert "Connection error" in str(exc_info.value)


@pytest.mark.asyncio
async def test_api_call_generic_openai_error(provider: OpenAIProvider) -> None:
    from openai import OpenAIError

    with patch.object(
        provider.client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.side_effect = OpenAIError("Generic error")

        with pytest.raises(LLMError) as exc_info:
            await provider._api_call(
                "gpt-4", [{"role": "user", "content": "test"}], False
            )

        assert "OpenAI API error" in str(exc_info.value)


@pytest.mark.asyncio
async def test_api_call_unexpected_error(provider: OpenAIProvider) -> None:
    with patch.object(
        provider.client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.side_effect = RuntimeError("Unexpected error")

        with pytest.raises(LLMError) as exc_info:
            await provider._api_call(
                "gpt-4", [{"role": "user", "content": "test"}], False
            )

        assert "Unexpected error" in str(exc_info.value)


# 5. Public call() method
@pytest.mark.asyncio
async def test_call_non_stream(provider: OpenAIProvider, mock_openai_response) -> None:
    with patch.object(provider, "_api_call", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = mock_openai_response

        result = await provider.call("test prompt", "gpt-4")

        assert isinstance(result, dict)
        assert "content" in result
        assert "model" in result
        assert result["content"] == "Hello from OpenAI"
        assert result["model"] == "gpt-4"


@pytest.mark.asyncio
async def test_call_stream(provider: OpenAIProvider, mock_openai_stream) -> None:
    with patch.object(provider, "_api_call", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = mock_openai_stream

        result = await provider.call("test prompt", "gpt-4", stream=True)

        chunks = []
        async for chunk in result:
            chunks.append(chunk)

        assert chunks == ["chunk1", "chunk2"]


@pytest.mark.asyncio
async def test_call_unregistered_model(provider: OpenAIProvider) -> None:
    with pytest.raises(ValueError) as exc_info:
        await provider.call("test", "unregistered-model")

    assert "not registered" in str(exc_info.value)


@pytest.mark.asyncio
async def test_call_stream_error(provider: OpenAIProvider) -> None:
    async def bad_stream():
        yield MagicMock(choices=[MagicMock(delta=MagicMock(content="ok"))])
        raise RuntimeError("Stream error")

    with patch.object(provider, "_api_call", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = bad_stream()

        result = await provider.call("test", "gpt-4", stream=True)

        with pytest.raises(LLMError):
            async for _ in result:
                pass


@pytest.mark.asyncio
async def test_call_with_custom_headers(
    provider: OpenAIProvider, mock_openai_response
) -> None:
    provider.register_custom_headers({"X-Custom": "test"})

    # --- FIX: Mock the client 'create' method, which receives the headers, ---
    # --- not '_api_call', which adds them. ---
    with patch.object(
        provider.client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_openai_response

        result = await provider.call("test", "gpt-4")

        # Verify custom headers were passed
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        assert "extra_headers" in call_kwargs
        assert call_kwargs["extra_headers"]["X-Custom"] == "test"


# 6. Health check
@pytest.mark.asyncio
async def test_health_check_success(provider: OpenAIProvider) -> None:
    mock_resp = AsyncMock()
    mock_resp.status = 200

    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_get.return_value.__aenter__.return_value = mock_resp

        result = await provider.health_check()
        assert result is True


@pytest.mark.asyncio
async def test_health_check_failure(provider: OpenAIProvider) -> None:
    with patch("aiohttp.ClientSession.get", side_effect=aiohttp.ClientError):
        result = await provider.health_check()
        assert result is False


@pytest.mark.asyncio
async def test_health_check_bad_status(provider: OpenAIProvider) -> None:
    mock_resp = AsyncMock()
    mock_resp.status = 401

    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_get.return_value.__aenter__.return_value = mock_resp

        result = await provider.health_check()
        assert result is False


@pytest.mark.asyncio
async def test_health_check_with_custom_endpoint(provider: OpenAIProvider) -> None:
    provider.register_custom_endpoint("https://custom.endpoint.com/v1")
    mock_resp = AsyncMock()
    mock_resp.status = 200

    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_get.return_value.__aenter__.return_value = mock_resp

        result = await provider.health_check()
        assert result is True


# 7. get_provider() factory
def mock_cfg(key: str | None = None) -> MagicMock:
    cfg = MagicMock(spec=RunnerConfig)
    cfg.llm_provider_api_key = key
    return cfg


@patch("generator.runner.providers.ai_provider.load_config")
def test_get_provider_cfg_key(mock_load: MagicMock) -> None:
    mock_load.return_value = mock_cfg("cfg-key")
    p = get_provider()
    assert p.api_key == "cfg-key"


@patch("generator.runner.providers.ai_provider.load_config")
@patch.dict(os.environ, {"OPENAI_API_KEY": "env-key"})
def test_get_provider_env_key(mock_load: MagicMock) -> None:
    mock_load.return_value = mock_cfg(None)
    p = get_provider()
    assert p.api_key == "env-key"


@patch("generator.runner.providers.ai_provider.load_config")
@patch.dict(os.environ, clear=True)
def test_get_provider_no_key(mock_load: MagicMock) -> None:
    mock_load.return_value = mock_cfg(None)
    with pytest.raises(ConfigurationError):
        get_provider()


# 8. Edge cases and integration
@pytest.mark.asyncio
async def test_call_with_kwargs(provider: OpenAIProvider, mock_openai_response) -> None:
    with patch.object(provider, "_api_call", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = mock_openai_response

        result = await provider.call("test", "gpt-4", temperature=0.7, max_tokens=100)

        call_kwargs = mock_api.call_args[1]
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["max_tokens"] == 100


@pytest.mark.asyncio
async def test_multiple_tokenizers(provider: OpenAIProvider) -> None:
    # Test that different models use appropriate tokenizers
    count1 = await provider.count_tokens("test", "gpt-4")
    count2 = await provider.count_tokens("test", "gpt-3.5-turbo")

    # Both should work
    assert count1 > 0
    assert count2 > 0


def test_registered_models_default(provider: OpenAIProvider) -> None:
    assert "gpt-3.5-turbo" in provider.registered_models
    assert "gpt-4" in provider.registered_models
    assert "gpt-4o" in provider.registered_models
