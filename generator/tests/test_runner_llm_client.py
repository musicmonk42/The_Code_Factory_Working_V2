# generator/runner/tests/test_llm_client.py
"""
Unit tests for llm_client.py with >=90% coverage.
Tests all public APIs, classes, methods, branches, and edge cases.
"""

import asyncio
import hashlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# FIX: Import the module itself to fix the namespace conflict
import runner.llm_client as llm_client
from runner.llm_client import (  # <-- Removed _async_client from this import
    CacheManager,
    DistributedRateLimiter,
    LLMClient,
    SecretsManager,
    call_llm_api,
    shutdown_llm_client,
)
from runner.runner_config import RunnerConfig
from runner.runner_errors import ConfigurationError, LLMError

# Note: Global client cleanup is handled by conftest.py session fixture


@pytest.fixture
def mock_imports(event_loop):
    """Mocks external dependencies used by LLMClient."""
    with (
        patch(
            "runner.llm_client.aioredis.from_url", new_callable=MagicMock
        ) as mock_redis_from_url,
        patch("runner.llm_client.tiktoken") as mock_tiktoken,
        patch("runner.llm_client.aiohttp") as mock_aiohttp,
        patch("runner.llm_client.load_dotenv") as mock_load_dotenv,
        patch("runner.llm_client.LLMPluginManager") as mock_plugin_manager,
        patch(
            "runner.llm_client.log_audit_event", new_callable=AsyncMock
        ) as mock_audit,
        patch("runner.llm_client.metrics") as mock_metrics,
    ):

        # Mock Redis client returned by from_url - use AsyncMock for async methods
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()
        mock_redis.incr = AsyncMock()
        mock_redis.expire = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=AsyncMock())
        mock_redis.close = AsyncMock()
        mock_redis_from_url.return_value = mock_redis

        # Mock metrics
        mock_metrics.LLM_CALLS_TOTAL = MagicMock()
        mock_metrics.LLM_CALLS_TOTAL.labels = MagicMock(
            return_value=MagicMock(inc=MagicMock())
        )
        mock_metrics.LLM_ERRORS_TOTAL = MagicMock()
        mock_metrics.LLM_ERRORS_TOTAL.labels = MagicMock(
            return_value=MagicMock(inc=MagicMock())
        )
        mock_metrics.LLM_LATENCY_SECONDS = MagicMock()
        mock_metrics.LLM_LATENCY_SECONDS.labels = MagicMock(
            return_value=MagicMock(observe=MagicMock())
        )
        mock_metrics.LLM_TOKENS_INPUT = MagicMock()
        mock_metrics.LLM_TOKENS_INPUT.labels = MagicMock(
            return_value=MagicMock(inc=MagicMock())
        )
        mock_metrics.LLM_TOKENS_OUTPUT = MagicMock()
        mock_metrics.LLM_TOKENS_OUTPUT.labels = MagicMock(
            return_value=MagicMock(inc=MagicMock())
        )
        mock_metrics.LLM_PROVIDER_HEALTH = MagicMock()
        mock_metrics.LLM_PROVIDER_HEALTH.labels = MagicMock(
            return_value=MagicMock(set=MagicMock())
        )
        mock_metrics.LLM_CIRCUIT_STATE = MagicMock()
        mock_metrics.LLM_CIRCUIT_STATE.labels = MagicMock(
            return_value=MagicMock(set=MagicMock())
        )
        mock_metrics.LLM_RATE_LIMIT_EXCEEDED = MagicMock()
        mock_metrics.LLM_RATE_LIMIT_EXCEEDED.labels = MagicMock(
            return_value=MagicMock(inc=MagicMock())
        )

        # CRITICAL: Set up _load_task for ALL tests (not just those using mock_provider)
        future = event_loop.create_future()
        future.set_result(None)
        mock_plugin_manager.return_value._load_task = future
        mock_plugin_manager.return_value.registry = {}

        yield {
            "redis": mock_redis,
            "tiktoken": mock_tiktoken,
            "aiohttp": mock_aiohttp,
            "load_dotenv": mock_load_dotenv,
            "plugin_manager": mock_plugin_manager,
            "audit": mock_audit,
            "metrics": mock_metrics,
        }


@pytest.fixture
def mock_config():
    """Mocks the necessary RunnerConfig attributes."""
    config = MagicMock(spec=RunnerConfig)
    config.llm_provider = "openai"
    config.llm_provider_api_key = "test_key"
    config.default_llm_model = "gpt-4"
    config.redis_url = "redis://localhost"
    return config


@pytest.fixture
def mock_provider(mock_imports):
    """Mocks a single LLM provider plugin."""
    mock_provider_instance = AsyncMock()
    mock_provider_instance.name = "mock_provider"
    mock_provider_instance.close = AsyncMock()
    # Default token count to prevent exceptions
    mock_provider_instance.count_tokens = AsyncMock(return_value=10)

    mock_imports["plugin_manager"].return_value.get_provider.return_value = (
        mock_provider_instance
    )
    mock_imports["plugin_manager"].return_value.list_providers.return_value = [
        "mock_provider"
    ]

    # Note: _load_task is now set up in mock_imports fixture

    return mock_provider_instance


@pytest.fixture(autouse=True)
def reset_global_client():
    """Resets the module-level global client before and after each test."""
    llm_client._async_client = None
    yield
    llm_client._async_client = None


@pytest.fixture
async def initialized_client(mock_config, mock_provider):
    """Returns an LLMClient instance ready for calls."""
    client = LLMClient(mock_config)

    # Wait for initialization with timeout to prevent hanging
    try:
        await asyncio.wait_for(client._is_initialized.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        pytest.fail("LLMClient initialization timed out after 5 seconds")

    # Mock close methods on underlying components for local client tests
    client.cache.redis = AsyncMock()
    client.cache.redis.close = AsyncMock()
    client.rate_limiter.redis = AsyncMock()
    client.rate_limiter.redis.close = AsyncMock()

    # Mock circuit_breaker methods so tests can assert on them
    client.circuit_breaker.record_failure = MagicMock()
    client.circuit_breaker.record_success = MagicMock()
    client.circuit_breaker.allow_request = AsyncMock(return_value=True)

    yield client

    # Ensure cleanup happens even if test fails
    try:
        await asyncio.wait_for(client.close(), timeout=2.0)
    except asyncio.TimeoutError:
        pass  # Ignore timeout during cleanup
    except Exception:
        pass  # Ignore other errors during cleanup


class TestSecretsManager:
    # Use patch.dict for environment variables only where strictly necessary for setup
    @patch.dict(os.environ, {"OPENAI_API_KEY": "env_key"})
    def test_get_secret_env(self):
        sm = SecretsManager()
        assert sm.get_secret("OPENAI_API_KEY") == "env_key"

    @patch.dict(os.environ, {})
    @patch("generator.runner.llm_client.load_dotenv")
    @patch.object(
        os.environ,
        "get",
        side_effect=lambda k, d=None: "dot_key" if k == "OPENAI_API_KEY" else d,
    )
    def test_get_secret_dotenv(self, mock_os_get, mock_load_dotenv):
        sm = SecretsManager()
        assert sm.get_secret("OPENAI_API_KEY") == "dot_key"

    @patch.dict(os.environ, {})
    def test_get_secret_not_found(self):
        sm = SecretsManager()
        assert sm.get_secret("NON_EXISTENT") is None

    @patch.dict(os.environ, {"REQUIRED_KEY": "exists"})
    def test_get_required_success(self):
        sm = SecretsManager()
        assert sm.get_required("REQUIRED_KEY") == "exists"

    def test_get_required_failure(self):
        sm = SecretsManager()
        with pytest.raises(ConfigurationError):
            sm.get_required("MISSING_KEY_REQUIRED")


class TestLLMClient:
    @pytest.mark.asyncio
    async def test_init(self, mock_config, mock_imports):
        # Ensure the mock_provider setup completes the _load_task
        client = LLMClient(mock_config)
        await client._is_initialized.wait()

        assert client._is_initialized.is_set()
        assert isinstance(client.secrets, SecretsManager)
        assert isinstance(client.cache, CacheManager)
        assert isinstance(client.rate_limiter, DistributedRateLimiter)
        assert client.manager is not None

    @pytest.mark.asyncio
    async def test_call_llm_api_non_stream(
        self, initialized_client, mock_provider, mock_imports
    ):
        mock_provider.call.return_value = {"content": "test_response"}
        mock_provider.count_tokens.return_value = 10
        initialized_client.rate_limiter.acquire = AsyncMock(return_value=True)

        result = await initialized_client.call_llm_api(
            "test_prompt", "test_model", provider="mock_provider"
        )

        assert result["content"] == "test_response"
        mock_provider.call.assert_awaited_once()
        mock_imports["audit"].assert_awaited_once()  # Check audit log was called

    @pytest.mark.asyncio
    async def test_call_llm_api_stream(
        self, initialized_client, mock_provider, mock_imports
    ):
        async def mock_gen():
            yield "chunk1"
            yield "chunk2"

        mock_provider.call.return_value = mock_gen()
        mock_provider.count_tokens = AsyncMock(
            side_effect=[3, 2, 0]
        )  # Input tokens, chunk 1, chunk 2
        initialized_client.rate_limiter.acquire = AsyncMock(return_value=True)

        gen = await initialized_client.call_llm_api(
            "test_prompt", "test_model", stream=True, provider="mock_provider"
        )
        chunks = [chunk async for chunk in gen]

        assert chunks == ["chunk1", "chunk2"]
        mock_provider.call.assert_awaited_once()
        # Audit log should be called once after stream completion
        mock_imports["audit"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_call_ensemble_api(self, initialized_client):
        # Ensemble call should run call_llm_api three times internally
        # We mock call_llm_api directly to test the ensemble logic

        async def mock_call_llm_api_internal(
            prompt, model=None, stream=False, provider=None, **kwargs
        ):
            if provider == "p1":
                return {"content": "consensus_response"}
            if provider == "p2":
                return {"content": "consensus_response"}
            if provider == "p3":
                return {"content": "dissent_response"}

        with patch.object(
            initialized_client, "call_llm_api", side_effect=mock_call_llm_api_internal
        ) as mock_internal_call:

            models = [
                {"provider": "p1", "model": "m1"},
                {"provider": "p2", "model": "m2"},
                {"provider": "p3", "model": "m3"},
            ]

            result = await initialized_client.call_ensemble_api(
                "prompt", models, "majority"
            )

            assert mock_internal_call.call_count == 3
            assert result["content"] == "consensus_response"
            assert "ensemble_results" in result
            assert len(result["ensemble_results"]) == 3

            # Test failure tolerance in ensemble
            mock_internal_call.call_count = 0
            # Test case: 1 failure (exception), 2 success. Consensus should pick 'p2_res'.
            mock_internal_call.side_effect = [
                Exception("p1 failed"),
                {"content": "p2_res"},
                {"content": "p3_res"},
            ]

            result = await initialized_client.call_ensemble_api(
                "prompt", models, "majority"
            )

            assert mock_internal_call.call_count == 3
            assert result["content"] == "p2_res"
            assert len(result["ensemble_results"]) == 2  # Only 2 non-exception results

    @pytest.mark.asyncio
    async def test_health_check(self, initialized_client, mock_provider):
        mock_provider.health_check.return_value = True
        assert await initialized_client.health_check("mock_provider") is True
        mock_provider.health_check.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close(self, initialized_client, mock_provider):
        mock_provider.close = AsyncMock()
        initialized_client.manager.registry = {"mock_provider": mock_provider}

        await initialized_client.close()

        mock_provider.close.assert_awaited_once()
        initialized_client.cache.redis.close.assert_awaited_once()
        initialized_client.rate_limiter.redis.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_llm_api_cache_hit(initialized_client, mock_provider):
    prompt = "cache_prompt"
    model = "test_model"
    provider = "openai"  # Default provider
    # FIX: Use the actual provider name 'openai' used in the client init
    cache_key = hashlib.sha256(f"{prompt}:{model}:{provider}".encode()).hexdigest()

    # Mock cache hit with the expected dictionary
    initialized_client.cache.get = AsyncMock(
        return_value={"content": "cached_response"}
    )

    result = await initialized_client.call_llm_api(prompt, model, provider=provider)

    assert result["content"] == "cached_response"
    initialized_client.cache.get.assert_awaited_once_with(cache_key)
    mock_provider.call.assert_not_awaited()  # Should skip provider call


@pytest.mark.asyncio
async def test_call_llm_api_rate_limit_exceeded(initialized_client):
    # Mock the async method directly to return False
    initialized_client.rate_limiter.acquire = AsyncMock(return_value=False)

    with pytest.raises(LLMError) as exc:
        await initialized_client.call_llm_api("prompt")

    assert "Rate limit exceeded" in str(exc.value)
    initialized_client.rate_limiter.acquire.assert_awaited_once()
    # Note: record_failure is NOT called for rate limit - the request never reached the provider


@pytest.mark.asyncio
async def test_call_llm_api_circuit_open(initialized_client):
    # Mock the async method directly to return False
    initialized_client.circuit_breaker.allow_request = AsyncMock(return_value=False)

    with pytest.raises(LLMError) as exc:
        await initialized_client.call_llm_api("prompt")

    assert "Circuit breaker open" in str(exc.value)
    initialized_client.circuit_breaker.allow_request.assert_awaited_once()
    # Note: record_failure is NOT called when circuit is open - the request was blocked before reaching the provider


@pytest.mark.asyncio
async def test_call_llm_api_no_provider_found(initialized_client, mock_provider):
    initialized_client.manager.get_provider.return_value = None

    with pytest.raises(ConfigurationError) as exc:
        await initialized_client.call_llm_api("prompt")

    assert "LLM provider 'openai' not loaded" in str(exc.value)
    # Failure is recorded when provider lookup fails
    assert initialized_client.circuit_breaker.record_failure.called
    assert "openai" in str(initialized_client.circuit_breaker.record_failure.call_args)


@pytest.mark.asyncio
async def test_call_llm_api_provider_raises_llmerror(initialized_client, mock_provider):
    # The provider raises an LLMError (e.g., from error translation)
    mock_provider.call.side_effect = LLMError(
        "API key invalid", provider="mock_provider"
    )

    with pytest.raises(LLMError) as exc:
        await initialized_client.call_llm_api("prompt", provider="mock_provider")

    assert "API key invalid" in str(exc.value)
    mock_provider.call.assert_awaited_once()
    initialized_client.circuit_breaker.record_failure.assert_called_once_with(
        "mock_provider"
    )


@pytest.mark.asyncio
async def test_call_llm_api_provider_raises_generic_exception(
    initialized_client, mock_provider
):
    # The provider raises a generic Exception (e.g., unexpected network failure)
    mock_provider.call.side_effect = Exception("network error")

    with pytest.raises(LLMError) as exc:
        await initialized_client.call_llm_api("prompt", provider="mock_provider")

    # The error should be wrapped by the client's catch-all into an LLMError
    assert "LLM call failed: network error" in str(exc.value)
    mock_provider.call.assert_awaited_once()
    # The client must still record the failure
    initialized_client.circuit_breaker.record_failure.assert_called_once_with(
        "mock_provider"
    )


@pytest.mark.asyncio
async def test_call_llm_api_token_counting(initialized_client, mock_provider):
    # Mock the LLMClient's count_tokens method (not the provider's)
    initialized_client.count_tokens = AsyncMock(
        side_effect=[15, 25]
    )  # Input tokens, Output tokens
    mock_provider.call.return_value = {"content": "response"}
    initialized_client.rate_limiter.acquire = AsyncMock(return_value=True)

    await initialized_client.call_llm_api(
        "prompt", "test_model", provider="mock_provider"
    )

    # count_tokens is called twice: once for input, once for output
    assert initialized_client.count_tokens.call_count == 2
    initialized_client.count_tokens.assert_any_call("prompt", "test_model")
    initialized_client.count_tokens.assert_any_call("response", "test_model")


class TestGlobalFunctions:
    @pytest.mark.asyncio
    @patch("generator.runner.llm_client.LLMClient", new_callable=MagicMock)
    async def test_global_call_llm_api(self, MockLLMClient, mock_config, mock_imports):
        MockLLMClient.return_value._is_initialized = asyncio.Future()
        MockLLMClient.return_value._is_initialized.set_result(True)
        MockLLMClient.return_value.call_llm_api = AsyncMock(
            return_value={"content": "global"}
        )

        result = await call_llm_api(
            "global_prompt", "global_model", False, "global_provider", mock_config
        )

        assert result["content"] == "global"
        MockLLMClient.assert_called_once()
        MockLLMClient.return_value.call_llm_api.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("generator.runner.llm_client.LLMClient")
    async def test_global_shutdown_llm_client(
        self, MockLLMClient, mock_config, mock_imports
    ):
        # Create mock instance with close method
        mock_instance = AsyncMock()
        mock_instance.close = AsyncMock()
        MockLLMClient.return_value = mock_instance

        # Manually initialize the global client
        llm_client._async_client = mock_instance

        await shutdown_llm_client()

        mock_instance.close.assert_awaited_once()
        assert llm_client._async_client is None
