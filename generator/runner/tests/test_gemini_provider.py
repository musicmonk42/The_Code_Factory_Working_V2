"""
test_gemini_provider.py
~~~~~~~~~~~~~~~~~~~~~~~
Industry-grade test suite for ``gemini_provider.py`` (≥ 90 % coverage).

Run with:
    pytest generator/runner/tests/test_gemini_provider.py -vv
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Check if Gemini SDK is available
try:
    from google.generativeai import GenerativeModel
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

from runner.providers.gemini_provider import GeminiProvider, get_provider  # type: ignore
from runner.runner_errors import LLMError, ConfigurationError  # type: ignore
from runner.runner_config import RunnerConfig  # type: ignore


@pytest.fixture
def provider() -> GeminiProvider:
    if not HAS_GEMINI:
        pytest.skip("Gemini SDK not installed")
    with patch('google.generativeai.configure'):
        return GeminiProvider(api_key="test-key-12345")


# 1. Initialization
def test_init_with_key() -> None:
    if not HAS_GEMINI:
        pytest.skip("Gemini SDK not installed")
    with patch('google.generativeai.configure'):
        p = GeminiProvider(api_key="test-key")
        assert p.api_key == "test-key"
        assert p.name == "gemini"


def test_init_without_key() -> None:
    if not HAS_GEMINI:
        pytest.skip("Gemini SDK not installed")
    with patch('google.generativeai.configure'):
        with pytest.raises(ConfigurationError):
            GeminiProvider(api_key=None)


def test_init_no_sdk() -> None:
    if HAS_GEMINI:
        pytest.skip("Gemini SDK is installed")
    
    with patch("runner.providers.gemini_provider.HAS_GEMINI", False):
        with pytest.raises(ConfigurationError, match="SDK.*missing"):
            GeminiProvider(api_key="test-key")


# 2. Custom models and hooks
def test_register_custom_model(provider: GeminiProvider) -> None:
    provider.register_custom_model("custom-alias", "gemini-pro")
    assert "custom-alias" in provider.custom_models
    assert provider.custom_models["custom-alias"] == "gemini-pro"


def test_add_pre_hook(provider: GeminiProvider) -> None:
    provider.add_pre_hook(lambda x: x.upper())
    assert len(provider.pre_hooks) == 1


def test_add_post_hook(provider: GeminiProvider) -> None:
    provider.add_post_hook(lambda x: x)
    assert len(provider.post_hooks) == 1


# 3. Configuration loading
def test_load_config_yaml(provider: GeminiProvider, tmp_path: Path) -> None:
    yaml_content = """
    models:
      my-alias: gemini-pro
      another: gemini-1.5-pro
    """
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content)
    
    provider.load_config(str(config_file))
    assert "my-alias" in provider.custom_models
    assert provider.custom_models["my-alias"] == "gemini-pro"


def test_load_config_unsupported(provider: GeminiProvider) -> None:
    with pytest.raises(ValueError, match="Unsupported config format"):
        provider.load_config("config.txt")


# 4. Token counting
@pytest.mark.asyncio
async def test_count_tokens_success(provider: GeminiProvider) -> None:
    mock_response = MagicMock()
    mock_response.total_tokens = 42
    
    # --- FIX: Patch the class where it's *used* (in the provider's namespace) ---
    with patch('runner.providers.gemini_provider.GenerativeModel') as MockModel:
        mock_instance = MockModel.return_value
        mock_instance.count_tokens_async = AsyncMock(return_value=mock_response)
        
        count = await provider.count_tokens("Hello world", "gemini-pro")
        assert count == 42


@pytest.mark.asyncio
async def test_count_tokens_fallback(provider: GeminiProvider) -> None:
    # --- FIX: Patch the class where it's *used* (in the provider's namespace) ---
    with patch('runner.providers.gemini_provider.GenerativeModel') as MockModel:
        mock_instance = MockModel.return_value
        mock_instance.count_tokens_async = AsyncMock(side_effect=RuntimeError("API error"))
        
        count = await provider.count_tokens("Hello world", "gemini-pro")
        # Should fall back to approximation
        assert count > 0


@pytest.mark.asyncio
async def test_count_tokens_no_sdk() -> None:
    if HAS_GEMINI:
        pytest.skip("Gemini SDK is installed")
    
    with patch("runner.providers.gemini_provider.HAS_GEMINI", False):
        with patch('google.generativeai.configure'):
            provider = GeminiProvider.__new__(GeminiProvider)
            provider.api_key = "test"
            provider.custom_models = {}
            
            count = await provider.count_tokens("Hello", "model")
            assert count > 0  # Fallback approximation


# 5. API call with error translation
@pytest.mark.asyncio
async def test_api_call_non_stream(provider: GeminiProvider) -> None:
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Hello from Gemini"
    
    mock_client.generate_content_async = AsyncMock(return_value=mock_response)
    
    result = await provider._api_call(mock_client, "test prompt", False, "test-run")
    assert result.text == "Hello from Gemini"


@pytest.mark.asyncio
async def test_api_call_stream(provider: GeminiProvider) -> None:
    if not HAS_GEMINI:
        pytest.skip("Gemini SDK not installed")
    
    from google.api_core.exceptions import ServiceUnavailable
    
    mock_client = MagicMock()
    
    async def mock_stream():
        chunk = MagicMock()
        chunk.text = "chunk1"
        yield chunk
    
    mock_client.generate_content_async = AsyncMock(return_value=mock_stream())
    
    result = await provider._api_call(mock_client, "test", True, "test-run")
    chunks = [c async for c in result]
    assert len(chunks) == 1


@pytest.mark.asyncio
async def test_api_call_invalid_argument(provider: GeminiProvider) -> None:
    if not HAS_GEMINI:
        pytest.skip("Gemini SDK not installed")
    
    from google.api_core.exceptions import InvalidArgument
    
    mock_client = MagicMock()
    mock_client.generate_content_async = AsyncMock(side_effect=InvalidArgument("Invalid"))
    
    with pytest.raises(LLMError, match="Invalid request"):
        await provider._api_call(mock_client, "test", False, "test-run")


@pytest.mark.asyncio
async def test_api_call_permission_denied(provider: GeminiProvider) -> None:
    if not HAS_GEMINI:
        pytest.skip("Gemini SDK not installed")
    
    from google.api_core.exceptions import PermissionDenied
    
    mock_client = MagicMock()
    mock_client.generate_content_async = AsyncMock(side_effect=PermissionDenied("Denied"))
    
    with pytest.raises(LLMError, match="Invalid API Key"):
        await provider._api_call(mock_client, "test", False, "test-run")


# 6. Public call()
@pytest.mark.asyncio
async def test_call_non_stream(provider: GeminiProvider) -> None:
    mock_response = MagicMock()
    mock_response.text = "Hello"
    
    with patch.object(provider, '_api_call', new_callable=AsyncMock) as mock_api:
        mock_api.return_value = mock_response
        
        result = await provider.call("test", "gemini-pro")
        assert result["content"] == "Hello"
        assert result["model"] == "gemini-pro"


@pytest.mark.asyncio
async def test_call_stream(provider: GeminiProvider) -> None:
    async def mock_stream():
        chunk1 = MagicMock()
        chunk1.text = "chunk1"
        yield chunk1
        
        chunk2 = MagicMock()
        chunk2.text = "chunk2"
        yield chunk2
    
    with patch.object(provider, '_api_call', new_callable=AsyncMock) as mock_api:
        mock_api.return_value = mock_stream()
        
        result = await provider.call("test", "gemini-pro", stream=True)
        chunks = [c async for c in result]
        assert chunks == ["chunk1", "chunk2"]


@pytest.mark.asyncio
async def test_call_with_alias(provider: GeminiProvider) -> None:
    provider.register_custom_model("my-model", "gemini-1.5-pro")
    
    mock_response = MagicMock()
    mock_response.text = "Response"
    
    with patch.object(provider, '_api_call', new_callable=AsyncMock) as mock_api:
        mock_api.return_value = mock_response
        
        result = await provider.call("test", "my-model")
        assert result["content"] == "Response"


@pytest.mark.asyncio
async def test_call_with_pre_hook(provider: GeminiProvider) -> None:
    provider.add_pre_hook(lambda x: x.upper())
    
    mock_response = MagicMock()
    mock_response.text = "Response"
    
    with patch.object(provider, '_api_call', new_callable=AsyncMock) as mock_api:
        mock_api.return_value = mock_response
        
        await provider.call("hello", "gemini-pro")
        
        # Verify prompt was uppercased
        call_args = mock_api.call_args[0]
        assert call_args[1] == "HELLO"


@pytest.mark.asyncio
async def test_call_with_post_hook(provider: GeminiProvider) -> None:
    provider.add_post_hook(lambda r: {**r, "content": r["content"] + "!"})
    
    mock_response = MagicMock()
    mock_response.text = "Hello"
    
    with patch.object(provider, '_api_call', new_callable=AsyncMock) as mock_api:
        mock_api.return_value = mock_response
        
        result = await provider.call("test", "gemini-pro")
        assert result["content"] == "Hello!"


# 7. Health check
@pytest.mark.asyncio
async def test_health_check_success(provider: GeminiProvider) -> None:
    mock_resp = AsyncMock()
    mock_resp.status = 200
    
    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_get.return_value.__aenter__.return_value = mock_resp
        assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_health_check_failure(provider: GeminiProvider) -> None:
    with patch("aiohttp.ClientSession.get", side_effect=aiohttp.ClientError):
        assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_health_check_no_key() -> None:
    if not HAS_GEMINI:
        pytest.skip("Gemini SDK not installed")
    
    with patch('google.generativeai.configure'):
        provider = GeminiProvider(api_key="test")
        provider.api_key = None
        assert await provider.health_check() is False


# 8. get_provider()
def mock_cfg(key: str | None = None) -> MagicMock:
    cfg = MagicMock(spec=RunnerConfig)
    cfg.llm_provider_api_key = key
    return cfg


@patch("runner.providers.gemini_provider.load_config")
def test_get_provider_with_config_key(mock_load: MagicMock) -> None:
    if not HAS_GEMINI:
        pytest.skip("Gemini SDK not installed")
    
    with patch('google.generativeai.configure'):
        mock_load.return_value = mock_cfg("cfg-key")
        p = get_provider()
        assert p.api_key == "cfg-key"


@patch("runner.providers.gemini_provider.load_config")
@patch.dict(os.environ, {"GEMINI_API_KEY": "env-key"})
def test_get_provider_with_env_key(mock_load: MagicMock) -> None:
    if not HAS_GEMINI:
        pytest.skip("Gemini SDK not installed")
    
    with patch('google.generativeai.configure'):
        mock_load.return_value = mock_cfg(None)
        p = get_provider()
        assert p.api_key == "env-key"


@patch("runner.providers.gemini_provider.load_config")
@patch.dict(os.environ, clear=True)
def test_get_provider_no_key(mock_load: MagicMock) -> None:
    if not HAS_GEMINI:
        pytest.skip("Gemini SDK not installed")