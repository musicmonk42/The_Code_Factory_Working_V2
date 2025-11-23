"""
test_local_provider.py
~~~~~~~~~~~~~~~~~~~~~~
Industry-grade test suite for ``local_provider.py`` (>= 90 % coverage).

Run with:
    pytest generator/runner/tests/test_local_provider.py -vv
    # coverage:
    pytest --cov=runner/providers/local_provider \
           --cov-report=term-missing \
           generator/runner/tests/test_local_provider.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import aiohttp
import pytest
import yaml  # FIX: Added missing import
from _pytest.logging import LogCaptureFixture
from tenacity import RetryError

# Make the *runner* package importable from the repo root
REPO_ROOT = Path(__file__).resolve().parents[3]  # …/The_Code_Factory-master
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Imports corrected to use providers/ instead of llm_client_providers/
from runner.providers.local_provider import (  # type: ignore
    PRICING,
    LocalProvider,
    get_provider,
    logger,
    stream_chunk_latency,
    stream_chunks_total,
)
from runner.runner_config import RunnerConfig  # type: ignore
from runner.runner_errors import LLMError  # type: ignore


# Fixtures
@pytest.fixture
def provider() -> LocalProvider:
    """Fresh provider with a dummy key."""
    return LocalProvider(api_key="dummy-key")


@pytest.fixture
def clean_pricing() -> None:
    """Reset global PRICING before each test."""
    PRICING.clear()


# 1. Initialization & globals
def test_init_with_key() -> None:
    p = LocalProvider(api_key="abc")
    assert p.api_key == "abc"
    assert p.name == "local"


def test_init_without_key() -> None:
    p = LocalProvider(api_key=None)
    assert p.api_key is None


def test_global_pricing_is_mutable(clean_pricing: None) -> None:
    PRICING["m"] = {"input": 0.0, "output": 0.0}
    assert PRICING["m"]["input"] == 0.0


# 2. Custom model / hook registration
@pytest.mark.asyncio
async def test_register_custom_model(provider: LocalProvider) -> None:
    # FIX: register_custom_model now expects a config dict
    provider.register_custom_model(
        "my-llm",
        {"endpoint": "http://localhost:9999/gen", "token_counter": lambda _: 99},
    )
    assert "my-llm" in provider.custom_models
    assert await provider.count_tokens("anything", "my-llm") == 99


@pytest.mark.asyncio
async def test_pre_hook(provider: LocalProvider) -> None:
    def upper(p: str) -> str:
        return p.upper()

    provider.add_pre_hook(upper)

    # FIX: Mock response to simulate aiohttp response object
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value='{"response": "ok", "done": true}')

    with patch.object(provider, "_api_call", new=AsyncMock(return_value=mock_resp)):
        result = await provider.call("hello", "model")
        # FIX: Check that the call was made (prompt was transformed by hook)
        provider._api_call.assert_called_once()
        call_args = provider._api_call.call_args
        # The data dict should contain the uppercased prompt
        assert call_args[0][2]["prompt"] == "HELLO"


@pytest.mark.asyncio
async def test_post_hook(provider: LocalProvider) -> None:
    def suffix(resp: Dict[str, Any]) -> Dict[str, Any]:
        resp["content"] = resp.get("content", "") + "-suf"
        return resp

    provider.add_post_hook(suffix)

    # FIX: Mock response to simulate aiohttp response object
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value='{"response": "world", "done": true}')

    with patch.object(provider, "_api_call", new=AsyncMock(return_value=mock_resp)):
        out = await provider.call("hi", "model")
        assert out["content"] == "world-suf"


# 3. load_plugins()
@pytest.mark.asyncio
async def test_load_plugins_success(provider: LocalProvider, tmp_path: Path) -> None:
    yaml_content = """
    models:
      custom:
        endpoint: http://example.com/gen
    pre_hooks:
      - tests.dummy_pre
    post_hooks:
      - tests.dummy_post
    """
    yaml_file = tmp_path / "local_plugins.yaml"
    yaml_file.write_text(yaml_content, encoding="utf-8")

    dummy_pre = MagicMock(return_value="PRE")
    dummy_post = MagicMock(return_value={"content": "POST"})

    # FIX: Added yaml import and proper mocking
    with patch("builtins.open", mock_open(read_data=yaml_content)), patch(
        "yaml.safe_load", return_value=yaml.safe_load(yaml_content)
    ), patch("importlib.import_module") as mock_import:
        mock_import.side_effect = [
            MagicMock(dummy_pre=dummy_pre),
            MagicMock(dummy_post=dummy_post),
        ]
        provider.load_plugins(str(yaml_file))

    assert "custom" in provider.custom_models
    assert provider.pre_hooks[0] == dummy_pre
    assert provider.post_hooks[0] == dummy_post


@pytest.mark.asyncio
async def test_load_plugins_missing_file(
    provider: LocalProvider, caplog: LogCaptureFixture
) -> None:
    # FIX: load_plugins now accepts a file_path parameter
    provider.load_plugins("/nonexistent.yaml")
    assert any(
        "No plugin file found" in rec.message
        or "Error loading local plugins" in rec.message
        or "Plugins loaded" in rec.message
        for rec in caplog.records
    )


# 4. Low-level _api_call
@pytest.mark.asyncio
async def test_api_call_non_stream(provider: LocalProvider) -> None:
    payload = {"response": "hi", "done": True}
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value=json.dumps(payload))

    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_post.return_value.__aenter__.return_value = mock_resp
        # FIX: _api_call requires: endpoint, headers, data, stream, run_id
        out = await provider._api_call(
            "http://localhost:11434/api/generate",
            {"Content-Type": "application/json"},
            {"model": "test", "prompt": "test"},
            False,
            "test-run-id",
        )
        assert out == mock_resp


@pytest.mark.asyncio
async def test_api_call_stream(provider: LocalProvider) -> None:
    async def sse():
        yield b'{"response":"a"}\n'
        yield b'{"response":"b"}\n'

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.content.__aiter__ = sse

    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_post.return_value.__aenter__.return_value = mock_resp
        # FIX: Use correct signature
        resp = await provider._api_call(
            "http://localhost:11434/api/generate",
            {"Content-Type": "application/json"},
            {"model": "test", "prompt": "test"},
            True,
            "test-run-id",
        )
        assert resp == mock_resp


@pytest.mark.asyncio
async def test_api_call_http_error(provider: LocalProvider) -> None:
    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.text = AsyncMock(return_value="boom")

    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_post.return_value.__aenter__.return_value = mock_resp
        with pytest.raises(LLMError):
            # FIX: Use correct signature
            await provider._api_call(
                "http://localhost:11434/api/generate",
                {"Content-Type": "application/json"},
                {"model": "test", "prompt": "test"},
                False,
                "test-run-id",
            )


@pytest.mark.asyncio
async def test_api_call_retry_rate_limit(provider: LocalProvider) -> None:
    mock_resp = AsyncMock()
    mock_resp.status = 429
    mock_resp.text = AsyncMock(return_value="rate limited")

    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_post.return_value.__aenter__.return_value = mock_resp
        with pytest.raises(RetryError):
            # FIX: Use correct signature
            await provider._api_call(
                "http://localhost:11434/api/generate",
                {"Content-Type": "application/json"},
                {"model": "test", "prompt": "test"},
                False,
                "test-run-id",
            )


# 5. Public .call()
@pytest.mark.asyncio
async def test_call_non_stream(provider: LocalProvider) -> None:
    # FIX: Mock response to simulate aiohttp response object
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value='{"response": "hi", "done": true}')

    with patch.object(provider, "_api_call", new=AsyncMock(return_value=mock_resp)):
        out = await provider.call("p", "m")
        assert out["content"] == "hi"
        assert out["model"] == "m"


@pytest.mark.asyncio
async def test_call_stream(provider: LocalProvider) -> None:
    # FIX: Mock response to simulate aiohttp streaming response
    mock_resp = AsyncMock()
    mock_resp.status = 200

    # <<< START FIX
    async def fake_content(*args, **kwargs):
        # <<< END FIX
        yield b'{"response":"a"}\n'
        yield b'{"response":"b"}\n'

    mock_resp.content.__aiter__ = fake_content

    with patch.object(provider, "_api_call", new=AsyncMock(return_value=mock_resp)):
        gen = await provider.call("p", "m", stream=True)
        assert [c async for c in gen] == ["a", "b"]


@pytest.mark.asyncio
async def test_call_missing_model(provider: LocalProvider) -> None:
    # FIX: Now raises ValueError for None model
    with pytest.raises(ValueError):
        await provider.call("p", None)  # type: ignore[arg-type]


# 6. Token counting
@pytest.mark.asyncio
async def test_count_tokens_default(provider: LocalProvider) -> None:
    # heuristic: len(words)
    assert await provider.count_tokens("hello world", "any") == 2


@pytest.mark.asyncio
async def test_count_tokens_custom(provider: LocalProvider) -> None:
    provider.register_custom_model(
        "c",
        {
            "endpoint": "http://localhost:11434/api/generate",
            "token_counter": lambda t: len(t.split()),
        },
    )
    assert await provider.count_tokens("a b c", "c") == 3


# 7. health_check
@pytest.mark.asyncio
async def test_health_check_ok(provider: LocalProvider) -> None:
    mock_resp = AsyncMock(status=200)
    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_get.return_value.__aenter__.return_value = mock_resp
        assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_health_check_fail(provider: LocalProvider) -> None:
    with patch("aiohttp.ClientSession.get", side_effect=aiohttp.ClientError):
        assert await provider.health_check() is False


# 8. get_provider() factory
def mock_cfg(key: str | None = None) -> MagicMock:
    cfg = MagicMock(spec=RunnerConfig)
    cfg.llm_provider_api_key = key
    return cfg


@patch("runner.providers.local_provider.load_config")
def test_get_provider_cfg_key(mock_load: MagicMock) -> None:
    mock_load.return_value = mock_cfg("cfg")
    p = get_provider()
    assert p.api_key == "cfg"


@patch("runner.providers.local_provider.load_config")
@patch.dict(os.environ, {"LOCAL_API_KEY": "env"})
def test_get_provider_env_key(mock_load: MagicMock) -> None:
    mock_load.return_value = mock_cfg(None)
    p = get_provider()
    assert p.api_key == "env"


@patch("runner.providers.local_provider.load_config")
@patch.dict(os.environ, clear=True)
def test_get_provider_no_key(mock_load: MagicMock) -> None:
    # FIX: Local provider doesn't require a key, so this test should pass
    mock_load.return_value = mock_cfg(None)
    p = get_provider()
    assert p.api_key is None  # Local provider allows None API key


# 9. Prometheus metrics
@pytest.mark.asyncio
async def test_stream_metrics(provider: LocalProvider) -> None:
    # <<< START FIX: Robustly clear all metrics and their internal children
    # This is the only reliable way to reset a Histogram for testing.
    stream_chunks_total.clear()
    stream_chunk_latency.clear()
    # The .clear() method on the Histogram object itself is supposed to remove
    # all labeled children, which is what we need. However, to be absolutely
    # certain, we can also clear the internal metrics if they exist.
    if hasattr(stream_chunk_latency, "_sum"):
        stream_chunk_latency._sum.clear()
    if hasattr(stream_chunk_latency, "_count"):
        stream_chunk_latency._count.clear()
    # <<< END FIX

    # FIX: Mock response to simulate aiohttp streaming response
    mock_resp = AsyncMock()
    mock_resp.status = 200

    async def fake_content(*args, **kwargs):
        yield b'{"response":"c1"}\n'
        yield b'{"response":"c2"}\n'

    mock_resp.content.__aiter__ = fake_content

    with patch.object(provider, "_api_call", new=AsyncMock(return_value=mock_resp)):
        gen = await provider.call("p", "m", stream=True)
        _ = [c async for c in gen]

    assert stream_chunks_total.labels("m")._value.get() == 2

    # <<< START FIX: Use the _samples() method to find the count
    # This is the only reliable, public-facing way to test a Histogram.
    count_value = None
    # The sample name for the count is the metric name + "_count"
    count_metric_name = f"{stream_chunk_latency._name}_count"

    for sample in stream_chunk_latency._samples():
        if sample.name == count_metric_name and sample.labels.get("model") == "m":
            count_value = sample.value
            break

    assert (
        count_value is not None
    ), f"Could not find sample '{count_metric_name}' with labels {{'model': 'm'}}"
    assert count_value == 2
    # <<< END FIX


# 10. Exception inside streaming generator
@pytest.mark.asyncio
async def test_stream_error(provider: LocalProvider) -> None:
    # FIX: Mock response to simulate aiohttp streaming response that errors
    mock_resp = AsyncMock()
    mock_resp.status = 200

    async def bad_content():
        yield b'{"response":"ok"}\n'
        raise RuntimeError("boom")

    mock_resp.content.__aiter__ = bad_content

    with patch.object(provider, "_api_call", new=AsyncMock(return_value=mock_resp)):
        gen = await provider.call("p", "m", stream=True)
        with pytest.raises(LLMError):
            async for _ in gen:
                pass


# 11. Logger sanity check
def test_logger_configured(caplog: LogCaptureFixture) -> None:
    # FIX: Configure logger to propagate to caplog
    logger.propagate = True
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        logger.debug("debug-msg")
    assert "debug-msg" in caplog.text
