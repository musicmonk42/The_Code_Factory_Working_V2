# test_openai_adapter.py
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, Any

# Import the adapter and related exceptions
from arbiter.plugins.openai_adapter import (
    OpenAIAdapter,
    AuthError,
    TimeoutError,
    RateLimitError,
    APIError,
)
from arbiter.plugins.llm_client import LLMClientError
import openai


class TestOpenAIAdapter:
    """Test suite for OpenAIAdapter."""

    @pytest.fixture
    def valid_settings(self) -> Dict[str, Any]:
        """Returns valid settings for initializing OpenAIAdapter."""
        return {
            "OPENAI_API_KEY": "test-openai-api-key",
            "LLM_MODEL": "gpt-4o-mini",
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
                    r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",  # Phone
                ],
            },
        }

    @pytest.fixture
    async def adapter(self, valid_settings):
        """Creates an OpenAIAdapter instance with mocked LLMClient."""
        with patch("arbiter.plugins.openai_adapter.LLMClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.model = "gpt-4o-mini"

            adapter = OpenAIAdapter(valid_settings)
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
        with patch("arbiter.plugins.openai_adapter.LLMClient") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            adapter = OpenAIAdapter(valid_settings)

            assert adapter._circuit_breaker_state == "closed"
            assert adapter._circuit_breaker_failures == 0
            assert adapter._circuit_breaker_threshold == 3
            assert adapter._circuit_breaker_timeout == 30
            assert adapter.security_config["mask_pii_in_logs"] is True

            mock_client.assert_called_once_with(
                provider="openai",
                api_key="test-openai-api-key",
                model="gpt-4o-mini",
                timeout=60,
                retry_attempts=3,
                retry_backoff_factor=2.0,
            )

    def test_init_missing_api_key(self):
        """Test initialization fails when API key is missing."""
        settings = {"LLM_MODEL": "gpt-4"}

        with pytest.raises(ValueError, match="Missing API key"):
            OpenAIAdapter(settings)

    def test_init_with_default_values(self):
        """Test initialization with minimal settings uses defaults."""
        settings = {"OPENAI_API_KEY": "test-key"}

        with patch("arbiter.plugins.openai_adapter.LLMClient") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            adapter = OpenAIAdapter(settings)

            # Check defaults were used
            assert mock_client.call_args[1]["model"] == "gpt-4o-mini"
            assert mock_client.call_args[1]["timeout"] == 60
            assert adapter._circuit_breaker_threshold == 3
            assert adapter._circuit_breaker_timeout == 30
            assert adapter.security_config == {}

    # --- Circuit Breaker Tests ---

    def test_circuit_breaker_closed_allows_requests(self, adapter):
        """Test circuit breaker allows requests when closed."""
        adapter._circuit_breaker_state = "closed"

        # Should not raise
        adapter._check_circuit_breaker()

    def test_circuit_breaker_open_blocks_requests(self, adapter):
        """Test circuit breaker blocks requests when open."""
        adapter._circuit_breaker_state = "open"
        adapter._circuit_breaker_last_failure_time = asyncio.get_event_loop().time()

        with pytest.raises(APIError, match="Circuit breaker is open"):
            adapter._check_circuit_breaker()

    def test_circuit_breaker_transitions_to_half_open(self, adapter):
        """Test circuit breaker transitions to half-open after timeout."""
        adapter._circuit_breaker_state = "open"
        adapter._circuit_breaker_last_failure_time = asyncio.get_event_loop().time() - 31
        adapter._circuit_breaker_timeout = 30

        adapter._check_circuit_breaker()

        assert adapter._circuit_breaker_state == "half-open"
        adapter.circuit_breaker_state_gauge.set.assert_called_with(1)

    def test_circuit_breaker_closes_on_success(self, adapter):
        """Test circuit breaker closes on successful request."""
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
        adapter.client.generate_text.return_value = "Generated response from OpenAI"

        result = await adapter.generate(
            "Test prompt", max_tokens=500, temperature=0.8, correlation_id="test-123"
        )

        assert result == "Generated response from OpenAI"
        adapter.client.generate_text.assert_called_once()
        assert adapter._circuit_breaker_state == "closed"

        # Check metrics
        adapter.requests_total.labels.assert_called_with(
            status="success", correlation_id="test-123"
        )
        adapter.processing_latency_seconds.labels.assert_called()

    @pytest.mark.asyncio
    async def test_generate_with_pii_masking(self, adapter):
        """Test that PII is masked in prompts when configured."""
        adapter.client.generate_text.return_value = "Response"
        adapter.security_config = {
            "mask_pii_in_logs": True,
            "pii_patterns": [
                r"\b[\w\.-]+@[\w\.-]+\.\w+\b",  # Email
                r"\d{3}-\d{2}-\d{4}",  # SSN
            ],
        }

        prompt = "Contact john@example.com or SSN: 123-45-6789"
        await adapter.generate(prompt)

        # The masked prompt should be passed to the client
        called_prompt = adapter.client.generate_text.call_args[0][0]
        assert "[PII_MASKED]" in called_prompt
        assert "john@example.com" not in called_prompt
        assert "123-45-6789" not in called_prompt

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
        timeout_error.__cause__ = openai.APITimeoutError("Request timed out")
        adapter.client.generate_text.side_effect = timeout_error

        with pytest.raises(TimeoutError, match="API call timed out"):
            await adapter.generate("Test prompt", correlation_id="timeout-test")

        assert adapter._circuit_breaker_failures == 1
        adapter.requests_total.labels.assert_called_with(
            status="failure", correlation_id="timeout-test"
        )

    @pytest.mark.asyncio
    async def test_generate_auth_error(self, adapter):
        """Test handling of authentication errors."""
        auth_error = LLMClientError("Auth error")
        api_status_error = openai.APIStatusError(
            message="Unauthorized", response=Mock(status_code=401), body=None
        )
        api_status_error.status_code = 401
        api_status_error.message = "Unauthorized"
        auth_error.__cause__ = api_status_error
        adapter.client.generate_text.side_effect = auth_error

        with pytest.raises(AuthError, match="authentication error"):
            await adapter.generate("Test prompt", correlation_id="auth-test")

        assert adapter._circuit_breaker_failures == 1

    @pytest.mark.asyncio
    async def test_generate_rate_limit_error(self, adapter):
        """Test handling of rate limit errors."""
        rate_error = LLMClientError("Rate limited")
        api_status_error = openai.APIStatusError(
            message="Rate limit exceeded", response=Mock(status_code=429), body=None
        )
        api_status_error.status_code = 429
        api_status_error.message = "Rate limit exceeded"
        rate_error.__cause__ = api_status_error
        adapter.client.generate_text.side_effect = rate_error

        with pytest.raises(RateLimitError, match="rate limit exceeded"):
            await adapter.generate("Test prompt", correlation_id="rate-test")

    @pytest.mark.asyncio
    async def test_generate_generic_api_error(self, adapter):
        """Test handling of generic API errors."""
        api_error = LLMClientError("API error")
        api_status_error = openai.APIStatusError(
            message="Internal server error", response=Mock(status_code=500), body=None
        )
        api_status_error.status_code = 500
        api_status_error.message = "Internal server error"
        api_error.__cause__ = api_status_error
        adapter.client.generate_text.side_effect = api_error

        with pytest.raises(APIError, match="API error.*status 500"):
            await adapter.generate("Test prompt", correlation_id="api-test")

    @pytest.mark.asyncio
    async def test_generate_unexpected_llm_client_error(self, adapter):
        """Test handling of unexpected LLMClient errors."""
        unexpected_error = LLMClientError("Unexpected")
        unexpected_error.__cause__ = RuntimeError("Something went wrong")
        adapter.client.generate_text.side_effect = unexpected_error

        with pytest.raises(APIError, match="Unexpected OpenAI API error"):
            await adapter.generate("Test prompt", correlation_id="unexpected-test")

    @pytest.mark.asyncio
    async def test_generate_critical_error(self, adapter):
        """Test handling of critical unhandled errors."""
        adapter.client.generate_text.side_effect = RuntimeError("Critical failure")

        with pytest.raises(APIError, match="Critical unhandled error"):
            await adapter.generate("Test prompt", correlation_id="critical-test")

        assert adapter._circuit_breaker_failures == 1

    # --- Context Manager Tests ---

    @pytest.mark.asyncio
    async def test_async_context_manager(self, valid_settings):
        """Test async context manager functionality."""
        with patch("arbiter.plugins.openai_adapter.LLMClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.aclose_session = AsyncMock()

            async with OpenAIAdapter(valid_settings) as adapter:
                assert adapter is not None

            mock_instance.aclose_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_with_exception(self, valid_settings):
        """Test context manager handles exceptions properly."""
        with patch("arbiter.plugins.openai_adapter.LLMClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.aclose_session = AsyncMock()

            with pytest.raises(ValueError):
                async with OpenAIAdapter(valid_settings):
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
        adapter.processing_latency_seconds.labels.assert_called_with(correlation_id="metrics-test")

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
