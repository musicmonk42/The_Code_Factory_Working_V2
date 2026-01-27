"""
test_grok_provider.py
~~~~~~~~~~~~~~~~~~~~~
Industry-grade test suite for ``grok_provider.py`` (≥ 90 % coverage).

Run with:
    pytest generator/runner/tests/test_grok_provider.py -vv
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

PROJECT_ROOT = (
    Path(__file__).resolve().parents[4]
)  # generator/runner/tests -> project root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from generator.runner.providers.grok_provider import GrokProvider, get_provider  # type: ignore
from generator.runner.runner_config import RunnerConfig  # type: ignore
from generator.runner.runner_errors import ConfigurationError, LLMError  # type: ignore


@pytest.fixture
def provider() -> GrokProvider:
    return GrokProvider(api_key="test-key-12345")


# 1. Initialization
def test_init_with_key() -> None:
    p = GrokProvider(api_key="test-key")
    assert p.api_key == "test-key"
    assert p.name == "grok"


def test_init_without_key() -> None:
    with pytest.raises(ConfigurationError):
        GrokProvider(api_key=None)


# 2. Custom models and hooks
def test_register_custom_model(provider: GrokProvider) -> None:
    provider.register_custom_model("custom", "https://custom.com", {"X-Key": "val"})
    assert "custom" in provider.custom_models


def test_add_pre_hook(provider: GrokProvider) -> None:
    provider.add_pre_hook(lambda x: x.upper())
    assert len(provider.pre_hooks) == 1


def test_add_post_hook(provider: GrokProvider) -> None:
    provider.add_post_hook(lambda x: x)
    assert len(provider.post_hooks) == 1


# 3. Token counting
@pytest.mark.asyncio
async def test_count_tokens(provider: GrokProvider) -> None:
    count = await provider.count_tokens("Hello world", "grok-4")
    assert count > 0


# 4. API call
@pytest.mark.asyncio
async def test_api_call_non_stream(provider: GrokProvider) -> None:
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(
        return_value={"choices": [{"message": {"content": "Hi"}}]}
    )

    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_post.return_value.__aenter__.return_value = mock_resp

        result = await provider._api_call(
            "https://api.x.ai/v1/chat/completions",
            {"Authorization": "Bearer test-key"},
            {"model": "grok-4", "messages": []},
            False,
            "test-run",
        )
        assert result == {"choices": [{"message": {"content": "Hi"}}]}


@pytest.mark.asyncio
async def test_api_call_stream(provider: GrokProvider) -> None:
    mock_resp = AsyncMock()
    mock_resp.status = 200

    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_post.return_value.__aenter__.return_value = mock_resp

        result = await provider._api_call(
            "https://api.x.ai/v1/chat/completions",
            {"Authorization": "Bearer test-key"},
            {"model": "grok-4", "messages": []},
            True,
            "test-run",
        )
        assert result == mock_resp


@pytest.mark.asyncio
async def test_api_call_error(provider: GrokProvider) -> None:
    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.text = AsyncMock(return_value="Server error")

    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_post.return_value.__aenter__.return_value = mock_resp

        with pytest.raises(LLMError):
            await provider._api_call(
                "https://api.x.ai/v1/chat/completions",
                {"Authorization": "Bearer test-key"},
                {"model": "grok-4", "messages": []},
                False,
                "test-run",
            )


# 5. Public call()
@pytest.mark.asyncio
async def test_call_non_stream(provider: GrokProvider) -> None:
    mock_resp = {"choices": [{"message": {"content": "Hello"}}]}

    with patch.object(provider, "_api_call", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = mock_resp

        result = await provider.call("test", "grok-4")
        assert result["content"] == "Hello"
        assert result["model"] == "grok-4"


@pytest.mark.asyncio
async def test_call_stream(provider: GrokProvider) -> None:
    mock_resp = AsyncMock()
    mock_resp.status = 200

    async def mock_content():
        yield b'data: {"choices": [{"delta": {"content": "chunk1"}}]}\n'
        yield b'data: {"choices": [{"delta": {"content": "chunk2"}}]}\n'
        yield b"data: [DONE]\n"

    mock_resp.content = mock_content()

    with patch.object(provider, "_api_call", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = mock_resp

        result = await provider.call("test", "grok-4", stream=True)
        chunks = [c async for c in result]
        assert chunks == ["chunk1", "chunk2"]


@pytest.mark.asyncio
async def test_call_with_hooks(provider: GrokProvider) -> None:
    provider.add_pre_hook(lambda x: x.upper())
    provider.add_post_hook(lambda r: {**r, "content": r["content"] + "!"})

    mock_resp = {"choices": [{"message": {"content": "hello"}}]}

    with patch.object(provider, "_api_call", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = mock_resp

        result = await provider.call("test", "grok-4")
        assert result["content"] == "hello!"


# 6. Health check
@pytest.mark.asyncio
async def test_health_check_success(provider: GrokProvider) -> None:
    mock_resp = AsyncMock()
    mock_resp.status = 200

    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_get.return_value.__aenter__.return_value = mock_resp
        assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_health_check_failure(provider: GrokProvider) -> None:
    with patch("aiohttp.ClientSession.get", side_effect=aiohttp.ClientError):
        assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_health_check_no_key() -> None:
    provider = GrokProvider(api_key="test")
    provider.api_key = None
    assert await provider.health_check() is False


# 7. get_provider()
def mock_cfg(key: str | None = None) -> MagicMock:
    cfg = MagicMock(spec=RunnerConfig)
    cfg.llm_provider_api_key = key
    return cfg


@patch("generator.runner.providers.grok_provider.load_config")
def test_get_provider_with_config_key(mock_load: MagicMock) -> None:
    mock_load.return_value = mock_cfg("cfg-key")
    p = get_provider()
    assert p.api_key == "cfg-key"


@patch("generator.runner.providers.grok_provider.load_config")
@patch.dict(os.environ, {"GROK_API_KEY": "env-key"})
def test_get_provider_with_env_key(mock_load: MagicMock) -> None:
    mock_load.return_value = mock_cfg(None)
    p = get_provider()
    assert p.api_key == "env-key"


@patch("generator.runner.providers.grok_provider.load_config")
@patch.dict(os.environ, clear=True)
def test_get_provider_no_key(mock_load: MagicMock) -> None:
    mock_load.return_value = mock_cfg(None)
    with pytest.raises(ConfigurationError):
        get_provider()
