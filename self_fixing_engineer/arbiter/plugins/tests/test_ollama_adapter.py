# test_ollama_adapter.py
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, Any
import aiohttp

# Import the adapter and related exceptions
from arbiter.plugins.ollama_adapter import OllamaAdapter, AuthError, RateLimitError
from arbiter.plugins.llm_client import LLMClientError, TimeoutError, APIError


class TestOllamaAdapter:
    """Test suite for OllamaAdapter."""

    @pytest.fixture
    def valid_settings(self) -> Dict[str, Any]:
        """Returns valid settings for initializing OllamaAdapter."""
        return {
            "LLM_MODEL": "llama3",
            "OLLAMA_API_URL": "http://localhost:11434",
            "LLM_API_TIMEOUT_SECONDS": 60,
            "LLM_API_RETRY_ATTEMPTS": 3,
            "LLM_API_RETRY_BACKOFF_FACTOR": 2.0,
            "CIRCUIT_BREAKER_THRESHOLD": 3,
            "CIRCUIT_BREAKER_TIMEOUT_SECONDS": 30,
            "security_config": {
                "mask_pii_in_logs": True,
                "pii_patterns": [
                    r"\b[\w\.-]+@[\w\.-]+\.\w+\b",  # Email
                    r"\d{3}-\d{2}-\d{4}",  # SSN
                ],
            },
        }

    @pytest.fixture
    async def adapter(self, valid_settings):
        """Creates an OllamaAdapter instance with mocked LLMClient."""
        with patch("arbiter.plugins.ollama_adapter.LLMClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.model = "llama3"

            adapter = OllamaAdapter(valid_settings)
            adapter.client = mock_instance

            # Fix the gauge mock
            mock_gauge = Mock()
            mock_gauge.set = Mock()
            adapter.circuit_breaker_state_gauge = mock_gauge

            # Fix the other metrics too
            mock_counter = Mock()
            mock_counter.labels = Mock(return_value=mock_counter)
            mock_counter.inc = Mock()
            adapter.requests_total = mock_counter

            mock_histogram = Mock()
            mock_histogram.labels = Mock(return_value=mock_histogram)
            mock_histogram.observe = Mock()
            adapter.processing_latency_seconds = mock_histogram

            yield adapter

    # --- Initialization Tests ---

    def test_init_with_valid_settings(self, valid_settings):
        """Test successful initialization with valid settings."""
        with patch("arbiter.plugins.ollama_adapter.LLMClient") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            adapter = OllamaAdapter(valid_settings)

            assert adapter._circuit_breaker_state == "closed"
            assert adapter._circuit_breaker_failures == 0
            assert adapter._circuit_breaker_threshold == 3
            assert adapter._circuit_breaker_timeout == 30
            assert adapter.security_config["mask_pii_in_logs"] is True

            mock_client.assert_called_once_with(
                provider="ollama",
                api_key=None,
                model="llama3",
                base_url="http://localhost:11434",
                timeout=60,
                retry_attempts=3,
                retry_backoff_factor=2.0,
            )

    def test_init_with_default_model(self):
        """Test initialization uses default model when not specified."""
        settings = {}

        with patch("arbiter.plugins.ollama_adapter.LLMClient") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            adapter = OllamaAdapter(settings)

            mock_client.assert_called_once()
            # Should use "llama3" as default
            assert mock_client.call_args[1]["model"] == "llama3"

    def test_init_with_empty_model_name(self):
        """Test initialization with empty model name raises ValueError."""
        settings = {"LLM_MODEL": ""}

        with pytest.raises(ValueError, match="Missing model name"):
            OllamaAdapter(settings)

    def test_init_with_minimal_settings(self):
        """Test initialization with minimal settings uses defaults."""
        settings = {"LLM_MODEL": "mistral"}

        with patch("arbiter.plugins.ollama_adapter.LLMClient") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            adapter = OllamaAdapter(settings)

            assert adapter._circuit_breaker_threshold == 3  # Default
            assert adapter._circuit_breaker_timeout == 30  # Default
            assert adapter.security_config == {}  # Empty by default

    # --- Circuit Breaker Tests ---

    def test_circuit_breaker_check_when_closed(self, adapter):
        """Test circuit breaker allows requests when closed."""
        adapter._circuit_breaker_state = "closed"

        # Should not raise
        adapter._check_circuit_breaker()

    def test_circuit_breaker_check_when_open_and_timeout_not_reached(self, adapter):
        """Test circuit breaker blocks requests when open and timeout not reached."""
        adapter._circuit_breaker_state = "open"
        adapter._circuit_breaker_last_failure_time = asyncio.get_event_loop().time()

        with pytest.raises(LLMClientError, match="Circuit breaker is open"):
            adapter._check_circuit_breaker()

    def test_circuit_breaker_check_transitions_to_half_open(self, adapter):
        """Test circuit breaker transitions to half-open after timeout."""
        adapter._circuit_breaker_state = "open"
        adapter._circuit_breaker_last_failure_time = (
            asyncio.get_event_loop().time() - 31
        )
        adapter._circuit_breaker_timeout = 30

        adapter._check_circuit_breaker()

        assert adapter._circuit_breaker_state == "half-open"
        adapter.circuit_breaker_state_gauge.set.assert_called_with(1)

    def test_circuit_breaker_update_on_success(self, adapter):
        """Test circuit breaker updates correctly on success."""
        adapter._circuit_breaker_state = "half-open"
        adapter._circuit_breaker_failures = 2

        adapter._update_circuit_breaker(success=True)

        assert adapter._circuit_breaker_state == "closed"
        assert adapter._circuit_breaker_failures == 0
        adapter.circuit_breaker_state_gauge.set.assert_called_with(0)

    def test_circuit_breaker_opens_after_threshold(self, adapter):
        """Test circuit breaker opens after reaching failure threshold."""
        adapter._circuit_breaker_failures = 2
        adapter._circuit_breaker_threshold = 3

        adapter._update_circuit_breaker(success=False)

        assert adapter._circuit_breaker_failures == 3
        assert adapter._circuit_breaker_state == "open"
        adapter.circuit_breaker_state_gauge.set.assert_called_with(2)

    def test_circuit_breaker_half_open_to_open_on_failure(self, adapter):
        """Test circuit breaker goes from half-open to open on failure."""
        adapter._circuit_breaker_state = "half-open"

        adapter._update_circuit_breaker(success=False)

        assert adapter._circuit_breaker_state == "open"
        adapter.circuit_breaker_state_gauge.set.assert_called_with(2)

    # --- Health Check Tests ---

    @pytest.mark.asyncio
    async def test_health_check_success(self, adapter):
        """Test successful health check."""
        adapter.client.ping = AsyncMock(return_value=True)

        result = await adapter.health_check()

        assert result is True
        adapter.client.ping.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check_failure(self, adapter):
        """Test failed health check."""
        adapter.client.ping = AsyncMock(side_effect=Exception("Connection failed"))

        result = await adapter.health_check()

        assert result is False

    # --- Generation Tests ---

    @pytest.mark.asyncio
    async def test_generate_success(self, adapter):
        """Test successful text generation."""
        adapter.client.generate_text.return_value = "Generated response from Ollama"

        result = await adapter.generate(
            "Test prompt", max_tokens=500, temperature=0.8, correlation_id="test-123"
        )

        assert result == "Generated response from Ollama"
        adapter.client.generate_text.assert_called_once()
        assert adapter._circuit_breaker_state == "closed"
        assert adapter._circuit_breaker_failures == 0

        # Check metrics
        adapter.requests_total.labels.assert_called()
        adapter.processing_latency_seconds.labels.assert_called()

    @pytest.mark.asyncio
    async def test_generate_with_pii_masking(self, adapter):
        """Test that PII is masked in prompts when configured."""
        adapter.client.generate_text.return_value = "Response"
        adapter.security_config = {
            "mask_pii_in_logs": True,
            "pii_patterns": [r"\b[\w\.-]+@[\w\.-]+\.\w+\b"],  # Email pattern
        }

        prompt = "Contact john@example.com for details"
        await adapter.generate(prompt)

        # The masked prompt should be passed to the client
        called_prompt = adapter.client.generate_text.call_args[0][0]
        assert "[PII_MASKED]" in called_prompt
        assert "john@example.com" not in called_prompt

    @pytest.mark.asyncio
    async def test_generate_without_pii_masking(self, adapter):
        """Test that PII is not masked when disabled."""
        adapter.client.generate_text.return_value = "Response"
        adapter.security_config = {"mask_pii_in_logs": False}

        prompt = "Contact john@example.com"
        await adapter.generate(prompt)

        called_prompt = adapter.client.generate_text.call_args[0][0]
        assert called_prompt == prompt

    # --- Error Handling Tests ---

    @pytest.mark.asyncio
    async def test_generate_timeout_error(self, adapter):
        """Test handling of timeout errors."""
        timeout_error = LLMClientError("Timeout")
        timeout_error.__cause__ = asyncio.TimeoutError("Request timed out")
        adapter.client.generate_text.side_effect = timeout_error

        with pytest.raises(TimeoutError, match="timed out"):
            await adapter.generate("Test prompt", correlation_id="timeout-test")

        assert adapter._circuit_breaker_failures == 1
        adapter.requests_total.labels.assert_called_with(
            status="failure", correlation_id="timeout-test"
        )

    @pytest.mark.asyncio
    async def test_generate_connection_error(self, adapter):
        """Test handling of connection errors."""
        connection_error = LLMClientError("Connection failed")
        connection_error.__cause__ = aiohttp.ClientConnectionError()
        adapter.client.generate_text.side_effect = connection_error

        with pytest.raises(TimeoutError, match="failed to connect"):
            await adapter.generate("Test prompt", correlation_id="conn-test")

        assert adapter._circuit_breaker_failures == 1

    @pytest.mark.asyncio
    async def test_generate_auth_error(self, adapter):
        """Test handling of authentication errors (unlikely for Ollama)."""
        auth_error = LLMClientError("Auth error")
        response_error = aiohttp.ClientResponseError(
            request_info=None, history=None, status=401, message="Unauthorized"
        )
        auth_error.__cause__ = response_error
        adapter.client.generate_text.side_effect = auth_error

        with pytest.raises(AuthError, match="authentication error"):
            await adapter.generate("Test prompt", correlation_id="auth-test")

    @pytest.mark.asyncio
    async def test_generate_rate_limit_error(self, adapter):
        """Test handling of rate limit errors (unlikely for Ollama)."""
        rate_error = LLMClientError("Rate limited")
        response_error = aiohttp.ClientResponseError(
            request_info=None, history=None, status=429, message="Too many requests"
        )
        rate_error.__cause__ = response_error
        adapter.client.generate_text.side_effect = rate_error

        with pytest.raises(RateLimitError, match="rate limit exceeded"):
            await adapter.generate("Test prompt", correlation_id="rate-test")

    @pytest.mark.asyncio
    async def test_generate_generic_api_error(self, adapter):
        """Test handling of generic API errors."""
        api_error = LLMClientError("API error")
        response_error = aiohttp.ClientResponseError(
            request_info=None, history=None, status=500, message="Internal server error"
        )
        api_error.__cause__ = response_error
        adapter.client.generate_text.side_effect = api_error

        with pytest.raises(APIError, match="API error.*status 500"):
            await adapter.generate("Test prompt", correlation_id="api-test")

    @pytest.mark.asyncio
    async def test_generate_unexpected_error(self, adapter):
        """Test handling of unexpected errors."""
        adapter.client.generate_text.side_effect = RuntimeError("Unexpected error")

        with pytest.raises(APIError, match="Critical unhandled error"):
            await adapter.generate("Test prompt", correlation_id="unexpected-test")

        assert adapter._circuit_breaker_failures == 1

    # --- Context Manager Tests ---

    @pytest.mark.asyncio
    async def test_async_context_manager(self, valid_settings):
        """Test async context manager functionality."""
        with patch("arbiter.plugins.ollama_adapter.LLMClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.aclose_session = AsyncMock()

            async with OllamaAdapter(valid_settings) as adapter:
                assert adapter is not None

            mock_instance.aclose_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_with_exception(self, valid_settings):
        """Test context manager handles exceptions properly."""
        with patch("arbiter.plugins.ollama_adapter.LLMClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.aclose_session = AsyncMock()

            with pytest.raises(ValueError):
                async with OllamaAdapter(valid_settings) as adapter:
                    raise ValueError("Test error")

            # Should still close session even with exception
            mock_instance.aclose_session.assert_called_once()

    # --- Metrics Tests ---

    @pytest.mark.asyncio
    async def test_metrics_recorded_on_success(self, adapter):
        """Test that metrics are recorded on successful generation."""
        adapter.client.generate_text.return_value = "Success"

        await adapter.generate("Test", correlation_id="metrics-test")

        adapter.requests_total.labels.assert_called_with(
            status="success", correlation_id="metrics-test"
        )
        adapter.processing_latency_seconds.labels.assert_called_with(
            correlation_id="metrics-test"
        )

    @pytest.mark.asyncio
    async def test_metrics_recorded_on_failure(self, adapter):
        """Test that metrics are recorded on failed generation."""
        adapter.client.generate_text.side_effect = LLMClientError("Test error")

        with pytest.raises(APIError):
            await adapter.generate("Test", correlation_id="fail-metrics")

        adapter.requests_total.labels.assert_called_with(
            status="failure", correlation_id="fail-metrics"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
