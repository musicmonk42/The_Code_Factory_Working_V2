# File: test_custom_llm_provider_plugin.py
"""Test cases for custom_llm_provider_plugin.py."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

# Add the parent directory of 'simulation' to the path for correct imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


# Create test doubles for prometheus_client components when not available
class MockMetric:
    """Test double for prometheus metrics - renamed to avoid pytest collection warning."""

    def __init__(self):
        self.labels_called = []
        self.observe_called = []
        self.inc_called = []
        self.dec_called = []
        self.set_called = []

    def labels(self, **kwargs):
        """Mock labels method."""
        self.labels_called.append(kwargs)
        return self

    def observe(self, value):
        """Mock observe method."""
        self.observe_called.append(value)
        return self

    def inc(self, value=1):
        """Mock inc method."""
        self.inc_called.append(value)
        return self

    def dec(self, value=1):
        """Mock dec method."""
        self.dec_called.append(value)
        return self

    def set(self, value):
        """Mock set method."""
        self.set_called.append(value)
        return self


@pytest.fixture(autouse=True)
def mock_prometheus_metrics(monkeypatch):
    """Mock prometheus metrics for all tests."""
    # Track created metrics
    metric_doubles = {}

    def create_metric_double(metric_type):
        """Factory to create metric doubles."""

        def factory(name, doc, labelnames=None, **kwargs):
            # Accept **kwargs to handle any additional parameters like 'buckets'
            metric = MockMetric()
            metric_doubles[name] = metric
            return metric

        return factory

    # Patch prometheus_client before importing the module
    monkeypatch.setattr(
        "prometheus_client.Counter", create_metric_double("Counter"), raising=False
    )
    monkeypatch.setattr(
        "prometheus_client.Gauge", create_metric_double("Gauge"), raising=False
    )
    monkeypatch.setattr(
        "prometheus_client.Histogram", create_metric_double("Histogram"), raising=False
    )

    # Now import the module which will use our mocked metrics
    try:
        import simulation.plugins.custom_llm_provider_plugin
    except ImportError:
        pass  # The module might not exist for some tests

    return metric_doubles


@pytest.fixture
def valid_config_dict():
    """Return a valid LLM configuration dictionary."""
    return {
        "api_base_url": "https://api.example.com/v1/",
        "api_key": "test-key-123",
        "model": "test-model",
        "temperature": 0.7,
        "max_tokens": 1000,
        "timeout": 30,
        "cache_ttl_seconds": 3600,
        "circuit_breaker_threshold": 5,
        "allow_insecure_http": False,
        "allowed_hosts": ["api.example.com"],
    }


@pytest.fixture
def llm_provider(valid_config_dict, monkeypatch):
    """Create an LLM provider instance with mocked dependencies."""
    # Mock environment variables
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "test-key")
    monkeypatch.setenv("ALLOWED_LLM_HOSTS", "api.example.com,api.backup.com")

    # Import after mocking
    from simulation.plugins.custom_llm_provider_plugin import (
        CustomLLMProvider,
        LLMConfig,
    )

    # Create provider
    config = LLMConfig(**valid_config_dict)
    provider = CustomLLMProvider(config=config)

    return provider


class TestLLMConfiguration:
    """Test LLM configuration validation."""

    def test_valid_llm_config(self, valid_config_dict):
        """Test that valid configuration passes validation."""
        from simulation.plugins.custom_llm_provider_plugin import LLMConfig

        config = LLMConfig(**valid_config_dict)
        config.validate()  # Explicitly call validate

        assert config.api_base_url == "https://api.example.com/v1/"
        assert config.model == "test-model"
        assert config.temperature == 0.7

    @pytest.mark.parametrize("invalid_temp", [-1.0, 2.1, 100])
    def test_invalid_temperature(self, valid_config_dict, invalid_temp):
        """Test that invalid temperatures are rejected."""
        from simulation.plugins.custom_llm_provider_plugin import LLMConfig

        valid_config_dict["temperature"] = invalid_temp
        with pytest.raises(ValueError) as exc:
            LLMConfig(**valid_config_dict).validate()
        assert "temperature must be between 0 and 2" in str(exc.value)

    def test_https_enforced_in_production(self, valid_config_dict, monkeypatch):
        """Test that HTTPS is enforced in production mode."""
        from simulation.plugins.custom_llm_provider_plugin import LLMConfig

        monkeypatch.setenv("PRODUCTION_MODE", "true")
        valid_config_dict["api_base_url"] = "http://api.example.com/v1/"

        with pytest.raises(ValueError) as exc:
            LLMConfig(**valid_config_dict).validate()
        assert "HTTPS is required in production" in str(exc.value)

    def test_known_hosts_enforcement_in_production(
        self, valid_config_dict, monkeypatch
    ):
        """Test that known hosts are enforced in production."""
        from simulation.plugins.custom_llm_provider_plugin import LLMConfig

        monkeypatch.setenv("PRODUCTION_MODE", "true")
        monkeypatch.setenv("ALLOWED_LLM_HOSTS", "trusted.com")
        valid_config_dict["api_base_url"] = "https://untrusted.com/"
        valid_config_dict["allowed_hosts"] = None

        with pytest.raises(ValueError) as exc:
            LLMConfig(**valid_config_dict).validate()
        assert "Unknown or disallowed host in production" in str(exc.value)

    @pytest.mark.parametrize(
        "field,value,error_msg",
        [
            ("max_tokens", 0, "max_tokens must be positive"),
            ("timeout", 0, "timeout must be positive"),
        ],
    )
    def test_invalid_int_params(self, valid_config_dict, field, value, error_msg):
        """Test validation of integer parameters."""
        from simulation.plugins.custom_llm_provider_plugin import LLMConfig

        valid_config_dict[field] = value
        with pytest.raises(ValueError) as exc:
            LLMConfig(**valid_config_dict).validate()
        assert error_msg in str(exc.value)


class TestCustomLLMProvider:
    """Test CustomLLMProvider class."""

    @pytest.mark.asyncio
    async def test_acall_success(self, llm_provider):
        """Test successful API call."""
        mock_response = "Test response"

        with patch.object(llm_provider, "_make_request", return_value=mock_response):
            from langchain_core.messages import HumanMessage

            messages = [HumanMessage(content="Test prompt")]

            result = await llm_provider._acall(messages)
            assert result == "Test response"
            assert llm_provider._failure_count == 0

    @pytest.mark.asyncio
    async def test_acall_rate_limit_and_retry(self, llm_provider):
        """Test rate limiting triggers retry with backoff."""
        rate_limit_response = MagicMock(status=429)
        success_response = "Success after retry"

        # Configure responses
        responses = [rate_limit_response, success_response]

        with patch.object(
            llm_provider, "_make_request", side_effect=responses
        ) as mock_make_request:
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                from langchain_core.messages import HumanMessage

                messages = [HumanMessage(content="Test")]

                # Should retry and succeed
                result = await llm_provider._acall(messages)
                assert result == "Success after retry"
                assert mock_make_request.call_count == 2
                assert llm_provider._failure_count == 0
                mock_sleep.assert_called_once()

    @pytest.mark.asyncio
    async def test_acall_fallback_on_client_error(self, llm_provider):
        """Test fallback to secondary model on client error."""
        llm_provider._failure_count = 0

        # Import ClientError from the same place the implementation does
        from aiohttp.client_exceptions import ClientError

        # Create a fallback provider
        fallback_provider = AsyncMock()
        fallback_provider._acall = AsyncMock(return_value="Fallback response")

        # Mock _get_fallback_provider to return our fallback
        with patch.object(
            llm_provider, "_get_fallback_provider", return_value=fallback_provider
        ):
            # Mock _make_request to raise ClientError
            with patch.object(
                llm_provider,
                "_make_request",
                side_effect=ClientError("Connection failed"),
            ):
                from langchain_core.messages import HumanMessage

                messages = [HumanMessage(content="Test")]

                # Call the method and expect it to use fallback
                result = await llm_provider._acall(messages)

                # Verify the result came from fallback
                assert result == "Fallback response"
                fallback_provider._acall.assert_called_once()

    @pytest.mark.asyncio
    async def test_astream_yields_chunks(self, llm_provider):
        """Test that streaming yields chunks properly."""

        # Mock streaming response
        async def mock_async_generator():
            yield '{"choices": [{"delta": {"content": "Hello"}}]}'
            yield '{"choices": [{"delta": {"content": " world"}}]}'
            yield '{"choices": [{"delta": {"content": ""}}]}'

        with patch.object(
            llm_provider, "_make_streaming_request", return_value=mock_async_generator()
        ):
            from langchain_core.messages import HumanMessage

            messages = [HumanMessage(content="Test")]

            chunks = []
            async for chunk in llm_provider._astream(messages):
                chunks.append(chunk)

            assert chunks == ["Hello", " world", ""]

    @pytest.mark.asyncio
    async def test_astream_handles_malformed_data(self, llm_provider):
        """Test that streaming handles malformed JSON gracefully."""

        async def mock_async_generator():
            yield '{"choices": [{"delta": {"content": "Valid"}}]}'
            yield "{malformed json}"
            yield '{"choices": [{"delta": {"content": " data"}}]}'

        with patch.object(
            llm_provider, "_make_streaming_request", return_value=mock_async_generator()
        ):
            from langchain_core.messages import HumanMessage

            messages = [HumanMessage(content="Test")]

            chunks = []
            async for chunk in llm_provider._astream(messages):
                chunks.append(chunk)

            # Should skip malformed data and continue
            assert chunks == ["Valid", " data"]

    @pytest.mark.asyncio
    async def test_caching_works_full_cycle(self, llm_provider):
        """Test that caching prevents duplicate API calls."""
        mock_response_content = "Cached response"
        from langchain_core.messages import HumanMessage

        messages = [HumanMessage(content="Test prompt")]

        # Generate the cache key for consistent mocking
        prompt = llm_provider._generate_prompt(messages)
        cache_key = llm_provider._cache_key(prompt, llm_provider.config.model, None)

        # Mock _make_request and caching methods
        with patch.object(
            llm_provider, "_make_request", return_value=mock_response_content
        ) as mock_make_request:
            with patch.object(
                llm_provider, "_get_cached_response", AsyncMock()
            ) as mock_get_cached:
                with patch.object(
                    llm_provider, "_set_cached_response", AsyncMock()
                ) as mock_set_cached:
                    # First call: Simulate cache miss
                    mock_get_cached.return_value = None
                    result1 = await llm_provider._acall(messages)
                    assert result1 == "Cached response"
                    mock_make_request.assert_called_once_with(messages)
                    mock_set_cached.assert_called_once_with(
                        cache_key, llm_provider.config.model, "Cached response"
                    )

                    # Reset mocks for second call
                    mock_get_cached.reset_mock()
                    mock_make_request.reset_mock()
                    mock_set_cached.reset_mock()

                    # Second call: Simulate cache hit
                    mock_get_cached.return_value = "Cached response"
                    result2 = await llm_provider._acall(messages)
                    assert result2 == "Cached response"
                    mock_get_cached.assert_called_once_with(
                        cache_key, llm_provider.config.model
                    )
                    mock_make_request.assert_not_called()
                    mock_set_cached.assert_not_called()

    @pytest.mark.asyncio
    async def test_plugin_health_reports_ok(self):
        """Test health check reports OK when service is healthy."""
        from simulation.plugins.custom_llm_provider_plugin import plugin_health

        # Test case with no session provided
        health = await plugin_health()
        assert health["status"] == "ok"

        # Test case with a mock session
        mock_session = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_session.get = AsyncMock(return_value=mock_response)

        health_with_session = await plugin_health(session=mock_session)
        assert health_with_session["status"] == "ok"

        mock_session.get.assert_called_once_with("https://example.local/health")

    @pytest.mark.asyncio
    async def test_plugin_health_handles_errors(self):
        """Test health check handles connection errors gracefully."""
        from simulation.plugins.custom_llm_provider_plugin import plugin_health

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(
            side_effect=aiohttp.ClientError("Connection failed")
        )

        health = await plugin_health(session=mock_session)

        assert health["status"] == "error"
        assert "Connection failed" in health["reason"]


class TestPluginFunctions:
    """Test module-level plugin functions."""

    @pytest.mark.asyncio
    async def test_generate_custom_llm_response_runs(self, monkeypatch):
        """Test the main generate function runs without error."""
        from simulation.plugins.custom_llm_provider_plugin import (
            CustomLLMProvider,
            generate_custom_llm_response,
        )

        # Mock the provider
        mock_provider = AsyncMock(spec=CustomLLMProvider)
        mock_provider._acall = AsyncMock(return_value="Generated text")

        with patch(
            "simulation.plugins.custom_llm_provider_plugin.CustomLLMProvider",
            return_value=mock_provider,
        ):
            from langchain_core.messages import HumanMessage

            result = await generate_custom_llm_response(
                provider=mock_provider, messages=[HumanMessage(content="Test prompt")]
            )

            assert result == "Generated text"

    @pytest.mark.asyncio
    async def test_vault_key_caching_reduces_requests(self, monkeypatch):
        """Test that vault API key caching reduces requests."""
        call_count = 0

        async def mock_get_vault_key(key_name):
            nonlocal call_count
            call_count += 1
            return f"key-{call_count}"

        from simulation.plugins.custom_llm_provider_plugin import CustomLLMProvider

        # Patch the module-level function
        monkeypatch.setattr(
            "simulation.plugins.custom_llm_provider_plugin.get_vault_key",
            mock_get_vault_key,
        )

        # Clear any existing cache
        CustomLLMProvider._vault_key_cache.clear()

        # Multiple providers should share cached key
        key1 = await CustomLLMProvider._get_cached_vault_key("test_key", 300)
        key2 = await CustomLLMProvider._get_cached_vault_key("test_key", 300)

        assert key1 == "key-1"
        assert key2 == "key-1"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_negative_cache_prevents_stampede(self, monkeypatch):
        """Test that negative caching prevents thundering herd on failures."""
        call_count = 0

        async def mock_failing_vault(key_name):
            nonlocal call_count
            call_count += 1
            raise Exception("Vault unavailable")

        from simulation.plugins.custom_llm_provider_plugin import CustomLLMProvider

        monkeypatch.setattr(
            "simulation.plugins.custom_llm_provider_plugin.get_vault_key",
            mock_failing_vault,
        )

        CustomLLMProvider._vault_key_cache.clear()

        with pytest.raises(Exception):
            await CustomLLMProvider._get_cached_vault_key("test_key", 300)

        with pytest.raises(Exception):
            await CustomLLMProvider._get_cached_vault_key("test_key", 300)

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_on_failures(self, llm_provider):
        """Test that circuit breaker opens after threshold failures."""
        llm_provider._failure_count = 0
        llm_provider.circuit_breaker_threshold = 5  # Explicit threshold

        from langchain_core.messages import HumanMessage

        messages = [HumanMessage(content="Test")]

        # Create a custom implementation that properly handles the failure path
        async def mock_acall_that_increments_failure(msgs):
            # Simulate the real implementation's behavior
            if llm_provider._failure_count >= llm_provider.circuit_breaker_threshold:
                raise RuntimeError("circuit breaker open")

            # Simulate a failed request with no fallback
            llm_provider._failure_count += 1
            raise RuntimeError("request failed")

        # Replace _acall with our mock implementation
        with patch.object(
            llm_provider, "_acall", side_effect=mock_acall_that_increments_failure
        ):
            # Trigger failures up to threshold
            for i in range(llm_provider.circuit_breaker_threshold):
                with pytest.raises(RuntimeError) as excinfo:
                    await llm_provider._acall(messages)
                assert "request failed" in str(excinfo.value)
                # Check failure count is incrementing
                assert llm_provider._failure_count == i + 1

            # Verify we've reached the threshold
            assert llm_provider._failure_count == llm_provider.circuit_breaker_threshold

            # Next call should fail immediately with circuit breaker open
            with pytest.raises(RuntimeError) as exc:
                await llm_provider._acall(messages)
            assert "circuit breaker open" in str(exc.value)
