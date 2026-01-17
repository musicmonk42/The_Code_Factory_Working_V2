# test_claude_provider.py
"""
test_claude_provider.py
~~~~~~~~~~~~~~~~~~~~~~~
Industry-grade test suite for ``claude_provider.py`` (≥ 90 % coverage).

Run with:
    pytest generator/runner/tests/test_claude_provider.py -vv
    # coverage:
    pytest --cov=runner/providers/claude_provider \
           --cov-report=term-missing \
           generator/runner/tests/test_claude_provider.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

# Make the *runner* package importable by adding generator/ to sys.path
GENERATOR_ROOT = (
    Path(__file__).resolve().parents[2]
)  # generator/runner/tests -> generator/
if str(GENERATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(GENERATOR_ROOT))

# Mock Anthropic SDK if not available
try:
    from anthropic import AsyncAnthropic

    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

from runner.providers.claude_provider import PRICING, ClaudeProvider, get_provider  # type: ignore
from runner.runner_config import RunnerConfig  # type: ignore
from runner.runner_errors import ConfigurationError, LLMError  # type: ignore


# Fixtures
@pytest.fixture
def provider() -> ClaudeProvider:
    """Fresh provider with a dummy key."""
    if not HAS_ANTHROPIC:
        pytest.skip("Anthropic SDK not installed")
    return ClaudeProvider(api_key="test-key-12345")


@pytest.fixture
def mock_claude_response():
    """Mock Claude API response object."""
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="Hello from Claude")]
    return mock_resp


@pytest.fixture
def mock_claude_stream():
    """Mock Claude streaming response."""

    async def mock_stream():
        chunk1 = MagicMock()
        chunk1.type = "content_block_delta"
        chunk1.delta = MagicMock(text="chunk1")

        chunk2 = MagicMock()
        chunk2.type = "content_block_delta"
        chunk2.delta = MagicMock(text="chunk2")

        yield chunk1
        yield chunk2

    return mock_stream()


# 1. Initialization & configuration
def test_init_with_key() -> None:
    if not HAS_ANTHROPIC:
        pytest.skip("Anthropic SDK not installed")
    p = ClaudeProvider(api_key="test-key")
    assert p.api_key == "test-key"
    assert p.name == "claude"
    assert p.client is not None


def test_init_without_key() -> None:
    if not HAS_ANTHROPIC:
        pytest.skip("Anthropic SDK not installed")
    with pytest.raises(ConfigurationError):
        ClaudeProvider(api_key=None)


def test_init_with_empty_key() -> None:
    if not HAS_ANTHROPIC:
        pytest.skip("Anthropic SDK not installed")
    with pytest.raises(ConfigurationError):
        ClaudeProvider(api_key="")


def test_pricing_loaded() -> None:
    assert "claude-3-opus-20240229" in PRICING
    assert "claude-3-sonnet-20240229" in PRICING
    assert "claude-3-haiku-20240307" in PRICING
    assert (
        PRICING["claude-3-haiku-20240307"]["input"]
        < PRICING["claude-3-opus-20240229"]["input"]
    )


# 2. Custom model registration
def test_register_custom_model(provider: ClaudeProvider) -> None:
    provider.register_custom_model(
        "custom-claude", "https://custom.endpoint.com", {"X-Custom": "header"}
    )
    assert "custom-claude" in provider.custom_models
    assert (
        provider.custom_models["custom-claude"]["endpoint"]
        == "https://custom.endpoint.com"
    )
    assert provider.custom_models["custom-claude"]["headers"]["X-Custom"] == "header"


def test_register_custom_model_no_headers(provider: ClaudeProvider) -> None:
    provider.register_custom_model("custom", "https://endpoint.com")
    assert provider.custom_models["custom"]["headers"] == {}


# 3. Hooks
def test_add_pre_hook(provider: ClaudeProvider) -> None:
    def upper_hook(text: str) -> str:
        return text.upper()

    provider.add_pre_hook(upper_hook)
    assert len(provider.pre_hooks) == 1
    assert provider.pre_hooks[0]("hello") == "HELLO"


def test_add_post_hook(provider: ClaudeProvider) -> None:
    def suffix_hook(response: Dict[str, Any]) -> Dict[str, Any]:
        response["content"] += "-suffix"
        return response

    provider.add_post_hook(suffix_hook)
    assert len(provider.post_hooks) == 1


def test_apply_pre_hooks(provider: ClaudeProvider) -> None:
    provider.add_pre_hook(lambda x: x.upper())
    provider.add_pre_hook(lambda x: x + "!")

    result = provider._apply_pre_hooks("hello")
    assert result == "HELLO!"


def test_apply_post_hooks(provider: ClaudeProvider) -> None:
    provider.add_post_hook(lambda r: {**r, "content": r["content"] + "1"})
    provider.add_post_hook(lambda r: {**r, "content": r["content"] + "2"})

    result = provider._apply_post_hooks({"content": "test"})
    assert result["content"] == "test12"


# 4. Configuration loading
def test_load_config_yaml(provider: ClaudeProvider, tmp_path: Path) -> None:
    yaml_content = """
    models:
      custom-model:
        endpoint: https://custom.com/api
        headers:
          X-Custom: value
    """
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content)

    provider.load_config(str(config_file))
    assert "custom-model" in provider.custom_models


def test_load_config_json(provider: ClaudeProvider, tmp_path: Path) -> None:
    json_content = {
        "models": {
            "custom-model": {
                "endpoint": "https://custom.com/api",
                "headers": {"X-Custom": "value"},
            }
        }
    }
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(json_content))

    provider.load_config(str(config_file))
    assert "custom-model" in provider.custom_models


def test_load_config_unsupported_format(provider: ClaudeProvider) -> None:
    with pytest.raises(ValueError, match="Unsupported config format"):
        provider.load_config("config.txt")


# 5. API call method with error translation
@pytest.mark.asyncio
async def test_api_call_non_stream_standard(
    provider: ClaudeProvider, mock_claude_response
) -> None:
    with patch.object(
        provider.client.messages, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_claude_response

        result, is_stream = await provider._api_call(
            "claude-3-haiku-20240307", "test prompt", False
        )
        assert result == mock_claude_response
        assert is_stream is False
        mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_api_call_stream_standard(
    provider: ClaudeProvider, mock_claude_stream
) -> None:
    with patch.object(
        provider.client.messages, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_claude_stream

        result, is_stream = await provider._api_call(
            "claude-3-haiku-20240307", "test", True
        )
        assert is_stream is True

        # Verify stream works
        chunks = []
        async for chunk in result:
            chunks.append(chunk)
        assert len(chunks) == 2


@pytest.mark.asyncio
async def test_api_call_custom_endpoint(provider: ClaudeProvider) -> None:
    provider.register_custom_model(
        "custom", "https://custom.endpoint.com/v1/messages", {"X-Key": "val"}
    )

    mock_resp_json = {"content": [{"text": "Custom response"}]}
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_resp_json)

    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_post.return_value.__aenter__.return_value = mock_resp

        result, is_stream = await provider._api_call("custom", "test", False)
        assert result == mock_resp_json
        assert is_stream is False


@pytest.mark.asyncio
async def test_api_call_authentication_error(provider: ClaudeProvider) -> None:
    if not HAS_ANTHROPIC:
        pytest.skip("Anthropic SDK not installed")
    from anthropic import AuthenticationError

    with patch.object(
        provider.client.messages, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.side_effect = AuthenticationError(
            "Invalid API key", response=MagicMock(), body=None
        )

        with pytest.raises(LLMError) as exc_info:
            await provider._api_call("claude-3-haiku-20240307", "test", False)

        assert "Authentication failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_api_call_rate_limit_error(provider: ClaudeProvider) -> None:
    if not HAS_ANTHROPIC:
        pytest.skip("Anthropic SDK not installed")
    from anthropic import RateLimitError

    with patch.object(
        provider.client.messages, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.side_effect = RateLimitError(
            "Rate limit exceeded", response=MagicMock(), body=None
        )

        with pytest.raises(LLMError) as exc_info:
            await provider._api_call("claude-3-haiku-20240307", "test", False)

        assert "Rate limit" in str(exc_info.value)


@pytest.mark.asyncio
async def test_api_call_connection_error(provider: ClaudeProvider) -> None:
    if not HAS_ANTHROPIC:
        pytest.skip("Anthropic SDK not installed")
    from anthropic import APIConnectionError

    with patch.object(
        provider.client.messages, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.side_effect = APIConnectionError(
            message="Connection failed", request=MagicMock()
        )

        with pytest.raises(LLMError) as exc_info:
            await provider._api_call("claude-3-haiku-20240307", "test", False)

        assert "Connection error" in str(exc_info.value)


# 6. Public call() method
@pytest.mark.asyncio
async def test_call_non_stream(provider: ClaudeProvider, mock_claude_response) -> None:
    with patch.object(provider, "_api_call", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = (mock_claude_response, False)

        result = await provider.call("test prompt", "claude-3-haiku-20240307")

        assert isinstance(result, dict)
        assert "content" in result
        assert "model" in result
        assert result["content"] == "Hello from Claude"


@pytest.mark.asyncio
async def test_call_stream(provider: ClaudeProvider, mock_claude_stream) -> None:
    with patch.object(provider, "_api_call", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = (mock_claude_stream, True)

        result = await provider.call(
            "test prompt", "claude-3-haiku-20240307", stream=True
        )

        chunks = []
        async for chunk in result:
            chunks.append(chunk)

        assert chunks == ["chunk1", "chunk2"]


@pytest.mark.asyncio
async def test_call_with_pre_hook(
    provider: ClaudeProvider, mock_claude_response
) -> None:
    provider.add_pre_hook(lambda x: x.upper())

    with patch.object(provider, "_api_call", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = (mock_claude_response, False)

        await provider.call("hello", "claude-3-haiku-20240307")

        # Verify the prompt was uppercased
        call_args = mock_api.call_args[0]
        assert call_args[1] == "HELLO"


@pytest.mark.asyncio
async def test_call_with_post_hook(
    provider: ClaudeProvider, mock_claude_response
) -> None:
    provider.add_post_hook(lambda r: {**r, "content": r["content"] + "-modified"})

    with patch.object(provider, "_api_call", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = (mock_claude_response, False)

        result = await provider.call("test", "claude-3-haiku-20240307")

        assert result["content"] == "Hello from Claude-modified"


@pytest.mark.asyncio
async def test_call_stream_error(provider: ClaudeProvider) -> None:
    async def bad_stream():
        chunk = MagicMock()
        chunk.type = "content_block_delta"
        chunk.delta = MagicMock(text="ok")
        yield chunk
        raise RuntimeError("Stream error")

    with patch.object(provider, "_api_call", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = (bad_stream(), True)

        result = await provider.call("test", "claude-3-haiku-20240307", stream=True)

        with pytest.raises(LLMError):
            async for _ in result:
                pass


# 7. Token counting
@pytest.mark.asyncio
async def test_count_tokens_success(provider: ClaudeProvider) -> None:
    # --- FIX: Add create=True to patch the attribute even if it doesn't exist ---
    with patch.object(
        provider.sync_client, "count_tokens", return_value=10, create=True
    ) as mock_count:
        count = await provider.count_tokens("Hello world", "claude-3-haiku-20240307")
        assert count == 10
        mock_count.assert_called_once_with("Hello world")


@pytest.mark.asyncio
async def test_count_tokens_fallback(provider: ClaudeProvider) -> None:
    # --- FIX: Add create=True to patch the attribute even if it doesn't exist ---
    with patch.object(
        provider.sync_client,
        "count_tokens",
        side_effect=RuntimeError("API error"),
        create=True,
    ):
        count = await provider.count_tokens("Hello world", "claude-3-haiku-20240307")
        # Should fall back to word count
        assert count == 2


# 8. Health check
@pytest.mark.asyncio
async def test_health_check_success(provider: ClaudeProvider) -> None:
    mock_resp = AsyncMock()
    mock_resp.status = 200

    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_get.return_value.__aenter__.return_value = mock_resp

        result = await provider.health_check()
        assert result is True


@pytest.mark.asyncio
async def test_health_check_failure(provider: ClaudeProvider) -> None:
    with patch("aiohttp.ClientSession.get", side_effect=aiohttp.ClientError):
        result = await provider.health_check()
        assert result is False


@pytest.mark.asyncio
async def test_health_check_no_key() -> None:
    if not HAS_ANTHROPIC:
        pytest.skip("Anthropic SDK not installed")
    provider = ClaudeProvider(api_key="test-key")
    provider.api_key = None

    result = await provider.health_check()
    assert result is False


# 9. get_provider() factory
def mock_cfg(key: str | None = None) -> MagicMock:
    cfg = MagicMock(spec=RunnerConfig)
    cfg.llm_provider_api_key = key
    return cfg


@patch("runner.providers.claude_provider.load_config")
def test_get_provider_cfg_key(mock_load: MagicMock) -> None:
    if not HAS_ANTHROPIC:
        pytest.skip("Anthropic SDK not installed")
    mock_load.return_value = mock_cfg("cfg-key")
    p = get_provider()
    assert p.api_key == "cfg-key"


@patch("runner.providers.claude_provider.load_config")
@patch.dict(os.environ, {"CLAUDE_API_KEY": "env-key"})
def test_get_provider_env_key(mock_load: MagicMock) -> None:
    if not HAS_ANTHROPIC:
        pytest.skip("Anthropic SDK not installed")
    mock_load.return_value = mock_cfg(None)
    p = get_provider()
    assert p.api_key == "env-key"


@patch("runner.providers.claude_provider.load_config")
@patch.dict(os.environ, clear=True)
def test_get_provider_no_key(mock_load: MagicMock) -> None:
    if not HAS_ANTHROPIC:
        pytest.skip("Anthropic SDK not installed")
    mock_load.return_value = mock_cfg(None)
    with pytest.raises(ConfigurationError):
        get_provider()


def test_get_provider_no_sdk() -> None:
    if HAS_ANTHROPIC:
        pytest.skip("Anthropic SDK is installed")

    with patch("runner.providers.claude_provider.HAS_ANTHROPIC", False):
        with patch("runner.providers.claude_provider.load_config"):
            with pytest.raises(ConfigurationError, match="Anthropic SDK not found"):
                get_provider()
