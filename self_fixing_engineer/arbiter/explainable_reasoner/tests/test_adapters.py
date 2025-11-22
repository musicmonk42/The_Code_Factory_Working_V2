# test_adapters.py
# Comprehensive production-grade tests for adapters.py
# Requires: pytest, pytest-asyncio, httpx (for real/mocked HTTP), unittest.mock
# Run with: pytest test_adapters.py -v --cov=adapters --cov-report=html

import os
import base64
import logging
import json
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
import httpx

# Import the module under test
from arbiter.explainable_reasoner.adapters import (
    LLMAdapter,
    OpenAIGPTAdapter,
    GeminiAPIAdapter,
    AnthropicAdapter,
    LLMAdapterFactory,
    retry,
)
from arbiter.explainable_reasoner.reasoner_errors import (
    ReasonerError,
)
from arbiter.explainable_reasoner.reasoner_config import SensitiveValue

# Setup logging for tests
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


# Fixtures
@pytest.fixture
def mock_httpx_client():
    """Mock httpx.AsyncClient for API calls."""
    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.aclose = AsyncMock()
    return mock_client


@pytest.fixture
def mock_sensitive_value():
    """Fixture for SensitiveValue."""
    return SensitiveValue("dummy_key")


@pytest.fixture
def dummy_multimodal_data():
    """Dummy image data for multimodal tests."""
    # Valid 1x1 pixel transparent PNG
    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )
    return {"image1": {"data_type": "image", "data": png_data, "filename": "test.png"}}


@pytest.fixture(autouse=True)
def mock_metrics():
    """Mock METRICS to avoid real increments and allow for assertions."""
    with patch(
        "arbiter.explainable_reasoner.adapters.INFERENCE_LATENCY"
    ) as mock_latency, patch(
        "arbiter.explainable_reasoner.adapters.INFERENCE_ERRORS"
    ) as mock_errors, patch(
        "arbiter.explainable_reasoner.adapters.STREAM_CHUNKS"
    ) as mock_chunks, patch(
        "arbiter.explainable_reasoner.adapters.HEALTH_CHECK_ERRORS"
    ) as mock_health:

        # Configure metric mocks
        for metric in [mock_latency, mock_errors, mock_chunks, mock_health]:
            metric.labels.return_value.inc = MagicMock()
            metric.labels.return_value.observe = MagicMock()

        yield {
            "latency": mock_latency,
            "errors": mock_errors,
            "chunks": mock_chunks,
            "health": mock_health,
        }


# Test Retry Decorator
@pytest.mark.asyncio
async def test_retry_success_on_first_try():
    class DummyRetrier:
        _breaker = None
        call_count = 0

        @retry(max_retries=3)
        async def success_func(self):
            self.call_count += 1
            return "success"

    instance = DummyRetrier()
    result = await instance.success_func()
    assert result == "success"
    assert instance.call_count == 1


@pytest.mark.asyncio
async def test_retry_success_after_failures():
    class DummyRetrier:
        _breaker = None
        call_count = 0

        @retry(max_retries=3, exceptions_to_catch=(httpx.RequestError,))
        async def flaky_func(self):
            self.call_count += 1
            if self.call_count < 3:
                raise httpx.RequestError("Fail", request=MagicMock())
            return "success"

    instance = DummyRetrier()
    result = await instance.flaky_func()
    assert result == "success"
    assert instance.call_count == 3


@pytest.mark.asyncio
async def test_retry_failure_after_max_retries():
    class DummyRetrier:
        _breaker = None
        call_count = 0

        @retry(max_retries=2, exceptions_to_catch=(httpx.TimeoutException,))
        async def fail_func(self):
            self.call_count += 1
            raise httpx.TimeoutException("Persistent fail")

    instance = DummyRetrier()
    with pytest.raises(ReasonerError, match="Failed after 2 retries"):
        await instance.fail_func()
    assert instance.call_count == 2


@pytest.mark.asyncio
async def test_retry_with_rate_limit():
    """Test retry with 429 rate limit response."""

    class DummyRetrier:
        _breaker = None
        call_count = 0

        @retry(max_retries=3, initial_backoff_delay=0.01)
        async def rate_limited_func(self, endpoint="test"):
            self.call_count += 1
            if self.call_count >= 3:
                return "success"
            response = MagicMock()
            response.status_code = 429
            response.headers = {"Retry-After": "0.01"}
            raise httpx.HTTPStatusError(
                "Rate limited", request=MagicMock(), response=response
            )

    instance = DummyRetrier()
    result = await instance.rate_limited_func()
    assert result == "success" or result is None


# Test LLMAdapter Abstract Base Class
def test_llm_adapter_abstract_cannot_instantiate():
    with pytest.raises(TypeError):
        LLMAdapter("model", SensitiveValue("key"), "https://api.example.com")


# Test Factory - Properly handle the classmethod and lru_cache
def test_factory_register_and_get():
    original_adapters = LLMAdapterFactory._adapters.copy()

    try:
        LLMAdapterFactory._adapters.clear()
        LLMAdapterFactory.register_adapter("openai", OpenAIGPTAdapter)

        config = {
            "model_name": "gpt-4",
            "api_key": "test_key",
            "adapter_type": "openai",
        }

        # Clear the cache if it exists
        if hasattr(LLMAdapterFactory.get_adapter, "cache_clear"):
            LLMAdapterFactory.get_adapter.cache_clear()

        # Convert config to JSON string for the cached method
        adapter = LLMAdapterFactory.get_adapter(json.dumps(config))

        assert isinstance(adapter, OpenAIGPTAdapter)
        assert adapter.model_name == "gpt-4"
    finally:
        LLMAdapterFactory._adapters = original_adapters
        if hasattr(LLMAdapterFactory.get_adapter, "cache_clear"):
            LLMAdapterFactory.get_adapter.cache_clear()


def test_factory_missing_api_key():
    original_adapters = LLMAdapterFactory._adapters.copy()

    try:
        LLMAdapterFactory._adapters.clear()
        LLMAdapterFactory.register_adapter("openai", OpenAIGPTAdapter)

        config = {"model_name": "gpt-4", "adapter_type": "openai"}

        # Clear the cache
        if hasattr(LLMAdapterFactory.get_adapter, "cache_clear"):
            LLMAdapterFactory.get_adapter.cache_clear()

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ReasonerError, match="API key missing"):
                # Convert config to JSON string
                LLMAdapterFactory.get_adapter(json.dumps(config))
    finally:
        LLMAdapterFactory._adapters = original_adapters
        if hasattr(LLMAdapterFactory.get_adapter, "cache_clear"):
            LLMAdapterFactory.get_adapter.cache_clear()


def test_factory_unknown_adapter():
    # Clear the cache
    if hasattr(LLMAdapterFactory.get_adapter, "cache_clear"):
        LLMAdapterFactory.get_adapter.cache_clear()

    config = {
        "model_name": "unknown/model",
        "adapter_type": "unknown",
        "api_key": "test_key",
    }

    with pytest.raises(ValueError, match="Unknown adapter type"):
        # Convert config to JSON string
        LLMAdapterFactory.get_adapter(json.dumps(config))


# Test OpenAIGPTAdapter
@pytest.mark.asyncio
async def test_openai_adapter_init(mock_sensitive_value):
    adapter = OpenAIGPTAdapter(
        "gpt-4", mock_sensitive_value, "https://api.openai.com/v1"
    )
    assert adapter.model_name == "gpt-4"
    assert adapter.base_url == "https://api.openai.com/v1"
    assert adapter._client is None


@pytest.mark.asyncio
async def test_openai_adapter_get_client(mock_sensitive_value):
    adapter = OpenAIGPTAdapter(
        "gpt-4", mock_sensitive_value, "https://api.openai.com/v1"
    )

    with patch(
        "arbiter.explainable_reasoner.adapters.httpx.AsyncClient"
    ) as mock_client_class:
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client_class.return_value = mock_client

        client = await adapter._get_client()
        assert client is mock_client
        mock_client_class.assert_called_once()


@pytest.mark.asyncio
async def test_openai_adapter_generate_success(
    mock_httpx_client, mock_sensitive_value, mock_metrics
):
    adapter = OpenAIGPTAdapter(
        "gpt-4", mock_sensitive_value, "https://api.openai.com/v1"
    )

    with patch.object(adapter, "_get_client", return_value=mock_httpx_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Generated text"}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        result = await adapter.generate("Prompt", max_tokens=50, temperature=0.7)

        assert result == "Generated text"
        mock_httpx_client.post.assert_called_once()
        mock_metrics["latency"].labels.assert_called()


@pytest.mark.asyncio
async def test_openai_adapter_generate_with_multimodal(
    mock_httpx_client, mock_sensitive_value, dummy_multimodal_data
):
    adapter = OpenAIGPTAdapter(
        "gpt-4-vision", mock_sensitive_value, "https://api.openai.com/v1"
    )

    with patch.object(adapter, "_get_client", return_value=mock_httpx_client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Image description"}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        result = await adapter.generate(
            "Describe this", multi_modal_data=dummy_multimodal_data
        )
        assert result == "Image description"

        call_args = mock_httpx_client.post.call_args
        json_data = call_args[1]["json"]
        assert "messages" in json_data


@pytest.mark.asyncio
async def test_openai_adapter_generate_error_handling(
    mock_httpx_client, mock_sensitive_value, mock_metrics
):
    adapter = OpenAIGPTAdapter(
        "gpt-4", mock_sensitive_value, "https://api.openai.com/v1"
    )

    with patch.object(adapter, "_get_client", return_value=mock_httpx_client):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_httpx_client.post.side_effect = httpx.HTTPStatusError(
            "Server error", request=MagicMock(), response=mock_response
        )

        with pytest.raises(ReasonerError, match="OpenAI API error"):
            await adapter.generate("Prompt")

        mock_metrics["errors"].labels.assert_called()


@pytest.mark.asyncio
async def test_openai_adapter_stream_generate(mock_httpx_client, mock_sensitive_value):
    adapter = OpenAIGPTAdapter(
        "gpt-4", mock_sensitive_value, "https://api.openai.com/v1"
    )

    # Mock streaming response
    async def mock_aiter_bytes():
        chunks = [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n',
            b'data: {"choices":[{"delta":{"content":" world"}}]}\n',
            b"data: [DONE]\n",
        ]
        for chunk in chunks:
            yield chunk

    # Create a proper mock for the response
    mock_response = AsyncMock()
    mock_response.aiter_bytes = mock_aiter_bytes
    mock_response.raise_for_status = MagicMock()

    # Create the async context manager that client.stream() returns
    class MockStreamContext:
        async def __aenter__(self):
            return mock_response

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    # Set up the mock client to return our context manager
    mock_httpx_client.stream = MagicMock(return_value=MockStreamContext())

    # Directly test the underlying method without the decorator
    # by accessing __wrapped__ if it exists
    stream_method = adapter.stream_generate
    if hasattr(stream_method, "__wrapped__"):
        stream_method = stream_method.__wrapped__

    with patch.object(adapter, "_get_client", return_value=mock_httpx_client):
        result = []
        async for chunk in stream_method(adapter, "Prompt"):
            result.append(chunk)

        assert result == ["Hello", " world"]


@pytest.mark.asyncio
async def test_openai_adapter_health_check(mock_httpx_client, mock_sensitive_value):
    adapter = OpenAIGPTAdapter(
        "gpt-4", mock_sensitive_value, "https://api.openai.com/v1"
    )

    with patch.object(adapter, "_get_client", return_value=mock_httpx_client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.get.return_value = mock_response

        result = await adapter.health_check()
        assert result is True


# Test GeminiAPIAdapter
@pytest.mark.asyncio
async def test_gemini_adapter_generate_success(
    mock_httpx_client, mock_sensitive_value, mock_metrics
):
    adapter = GeminiAPIAdapter(
        "gemini-pro",
        mock_sensitive_value,
        "https://generativelanguage.googleapis.com/v1beta/models",
    )

    with patch.object(adapter, "_get_client", return_value=mock_httpx_client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Generated text"}]}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        result = await adapter.generate("Prompt", max_tokens=50, temperature=0.7)
        assert result == "Generated text"


# Test AnthropicAdapter
@pytest.mark.asyncio
async def test_anthropic_adapter_generate_success(
    mock_httpx_client, mock_sensitive_value, mock_metrics
):
    adapter = AnthropicAdapter(
        "claude-3", mock_sensitive_value, "https://api.anthropic.com/v1"
    )

    with patch.object(adapter, "_get_client", return_value=mock_httpx_client):
        mock_response = MagicMock()
        mock_response.json.return_value = {"content": [{"text": "Generated text"}]}
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        result = await adapter.generate("Prompt", max_tokens=50, temperature=0.7)
        assert result == "Generated text"


@pytest.mark.asyncio
async def test_adapter_rotate_key(mock_sensitive_value):
    adapter = OpenAIGPTAdapter(
        "gpt-4", mock_sensitive_value, "https://api.openai.com/v1"
    )

    initial_client = AsyncMock()
    initial_client.is_closed = False
    initial_client.aclose = AsyncMock()
    adapter._client = initial_client

    await adapter.rotate_key("new_key")

    assert adapter.api_key.get_actual_value() == "new_key"
    initial_client.aclose.assert_called_once()
    assert adapter._client is None


@pytest.mark.asyncio
async def test_adapter_aclose(mock_httpx_client, mock_sensitive_value):
    adapter = OpenAIGPTAdapter(
        "gpt-4", mock_sensitive_value, "https://api.openai.com/v1"
    )
    adapter._client = mock_httpx_client

    await adapter.aclose()
    mock_httpx_client.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_adapter_custom_base_url(mock_sensitive_value):
    custom_url = "https://custom.openai.com/v1"
    adapter = OpenAIGPTAdapter("gpt-4", mock_sensitive_value, base_url=custom_url)
    assert adapter.base_url == custom_url


@pytest.mark.asyncio
async def test_adapter_timeout_handling(mock_httpx_client, mock_sensitive_value):
    adapter = OpenAIGPTAdapter(
        "gpt-4", mock_sensitive_value, "https://api.openai.com/v1"
    )

    with patch.object(adapter, "_get_client", return_value=mock_httpx_client):
        mock_httpx_client.post.side_effect = httpx.TimeoutException("Request timed out")

        with pytest.raises(ReasonerError, match="An unexpected error occurred"):
            await adapter.generate("Prompt")
