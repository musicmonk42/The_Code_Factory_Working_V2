# test_anthropic_adapter.py
import time
from typing import Any, Dict
from unittest.mock import AsyncMock, Mock, patch

import anthropic
import pytest

# Import the adapter and related exceptions
from self_fixing_engineer.arbiter.plugins.anthropic_adapter import AnthropicAdapter
from self_fixing_engineer.arbiter.plugins.llm_client import (
    APIError,
    AuthError,
    CircuitBreakerOpenError,
    LLMClientError,
    RateLimitError,
    TimeoutError,
)
from tenacity import RetryError


class TestAnthropicAdapter:
    """Test suite for AnthropicAdapter."""

    @pytest.fixture
    def valid_settings(self) -> Dict[str, Any]:
        """Returns valid settings for initializing AnthropicAdapter."""
        return {
            "ANTHROPIC_API_KEY": "test-api-key",
            "LLM_MODEL": "claude-3-sonnet-20240229",
            "LLM_API_TIMEOUT_SECONDS": 30,
            "LLM_API_RETRY_ATTEMPTS": 2,
            "LLM_API_RETRY_BACKOFF_FACTOR": 1.5,
            "CIRCUIT_BREAKER_THRESHOLD": 3,
            "CIRCUIT_BREAKER_TIMEOUT_SECONDS": 60,
        }

    @pytest.fixture
    async def adapter(self, valid_settings):
        """Creates an AnthropicAdapter instance with mocked LLMClient."""
        with patch("self_fixing_engineer.arbiter.plugins.anthropic_adapter.LLMClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.model = "claude-3-sonnet-20240229"

            adapter = AnthropicAdapter(valid_settings)
            adapter.client = mock_instance
            yield adapter

    # --- Initialization Tests ---

    def test_init_with_valid_settings(self, valid_settings):
        """Test successful initialization with valid settings."""
        with patch("self_fixing_engineer.arbiter.plugins.anthropic_adapter.LLMClient") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            adapter = AnthropicAdapter(valid_settings)

            assert adapter.circuit_breaker_state == "closed"
            assert adapter.circuit_breaker_failures == 0
            assert adapter.circuit_breaker_threshold == 3
            assert adapter.circuit_breaker_timeout == 60
            mock_client.assert_called_once()

    def test_init_missing_api_key(self):
        """Test initialization fails when API key is missing."""
        settings = {"LLM_MODEL": "claude-3"}

        with pytest.raises(ValueError, match="Missing API key"):
            AnthropicAdapter(settings)

    def test_init_with_none_client(self, valid_settings):
        """Test initialization fails when LLMClient returns None."""
        with patch("self_fixing_engineer.arbiter.plugins.anthropic_adapter.LLMClient", return_value=None):
            with pytest.raises(ValueError, match="Failed to initialize LLMClient"):
                AnthropicAdapter(valid_settings)

    # --- Input Validation Tests ---

    @pytest.mark.asyncio
    async def test_generate_empty_prompt(self, adapter):
        """Test that empty prompt raises ValueError."""
        with pytest.raises(ValueError, match="Prompt must be a non-empty string"):
            await adapter.generate("", max_tokens=100)

    @pytest.mark.asyncio
    async def test_generate_none_prompt(self, adapter):
        """Test that None prompt raises ValueError."""
        with pytest.raises(ValueError, match="Prompt must be a non-empty string"):
            await adapter.generate(None, max_tokens=100)

    @pytest.mark.asyncio
    async def test_generate_prompt_too_long(self, adapter):
        """Test that overly long prompt raises ValueError."""
        long_prompt = "x" * 100001
        with pytest.raises(ValueError, match="exceeds the maximum length"):
            await adapter.generate(long_prompt)

    @pytest.mark.asyncio
    async def test_generate_invalid_max_tokens(self, adapter):
        """Test that invalid max_tokens raises ValueError."""
        with pytest.raises(ValueError, match="max_tokens must be between 1 and 4096"):
            await adapter.generate("test", max_tokens=5000)

        with pytest.raises(ValueError, match="max_tokens must be between 1 and 4096"):
            await adapter.generate("test", max_tokens=0)

    @pytest.mark.asyncio
    async def test_generate_invalid_temperature(self, adapter):
        """Test that invalid temperature raises ValueError."""
        with pytest.raises(ValueError, match="temperature must be between 0.0 and 1.0"):
            await adapter.generate("test", temperature=1.5)

        with pytest.raises(ValueError, match="temperature must be between 0.0 and 1.0"):
            await adapter.generate("test", temperature=-0.1)

    # --- Successful Generation Tests ---

    @pytest.mark.asyncio
    async def test_generate_success(self, adapter):
        """Test successful text generation."""
        adapter.client.generate_text.return_value = "Generated text response"

        result = await adapter.generate(
            "Test prompt", max_tokens=100, temperature=0.7, correlation_id="test-123"
        )

        assert result == "Generated text response"
        adapter.client.generate_text.assert_called_once()
        assert adapter.circuit_breaker_state == "closed"
        assert adapter.circuit_breaker_failures == 0

    # --- Error Handling Tests ---

    @pytest.mark.asyncio
    async def test_generate_retry_error(self, adapter):
        """Test handling of RetryError."""
        adapter.client.generate_text.side_effect = RetryError("All retries exhausted")

        with pytest.raises(APIError, match="failed after multiple retries"):
            await adapter.generate("Test prompt", correlation_id="test-retry")

        assert adapter.circuit_breaker_failures == 1

    @pytest.mark.asyncio
    async def test_generate_timeout_error(self, adapter):
        """Test handling of timeout errors."""
        timeout_exception = anthropic.APITimeoutError("Request timed out")
        adapter.client.generate_text.side_effect = LLMClientError("Timeout")
        adapter.client.generate_text.side_effect.__cause__ = timeout_exception

        with pytest.raises(TimeoutError, match="API call timed out"):
            await adapter.generate("Test prompt", correlation_id="test-timeout")

        assert adapter.circuit_breaker_failures == 1

    @pytest.mark.asyncio
    async def test_generate_auth_error(self, adapter):
        """Test handling of authentication errors."""
        auth_exception = anthropic.APIStatusError(
            message="Unauthorized", response=Mock(status_code=401), body=None
        )
        auth_exception.status_code = 401
        auth_exception.message = "Unauthorized"

        adapter.client.generate_text.side_effect = LLMClientError("Auth error")
        adapter.client.generate_text.side_effect.__cause__ = auth_exception

        with pytest.raises(AuthError, match="authentication error"):
            await adapter.generate("Test prompt", correlation_id="test-auth")

        assert adapter.circuit_breaker_failures == 1

    @pytest.mark.asyncio
    async def test_generate_rate_limit_error(self, adapter):
        """Test handling of rate limit errors."""
        rate_limit_exception = anthropic.APIStatusError(
            message="Rate limit exceeded", response=Mock(status_code=429), body=None
        )
        rate_limit_exception.status_code = 429
        rate_limit_exception.message = "Rate limit exceeded"

        adapter.client.generate_text.side_effect = LLMClientError("Rate limited")
        adapter.client.generate_text.side_effect.__cause__ = rate_limit_exception

        with pytest.raises(RateLimitError, match="rate limit exceeded"):
            await adapter.generate("Test prompt", correlation_id="test-rate")

        assert adapter.circuit_breaker_failures == 1

    @pytest.mark.asyncio
    async def test_generate_generic_api_error(self, adapter):
        """Test handling of generic API errors."""
        api_exception = anthropic.APIStatusError(
            message="Server error", response=Mock(status_code=500), body=None
        )
        api_exception.status_code = 500
        api_exception.message = "Server error"

        adapter.client.generate_text.side_effect = LLMClientError("API error")
        adapter.client.generate_text.side_effect.__cause__ = api_exception

        with pytest.raises(APIError, match="API error.*status 500"):
            await adapter.generate("Test prompt", correlation_id="test-api")

    @pytest.mark.asyncio
    async def test_generate_unexpected_error(self, adapter):
        """Test handling of unexpected errors."""
        adapter.client.generate_text.side_effect = Exception("Unexpected error")

        with pytest.raises(APIError, match="Critical unhandled error"):
            await adapter.generate("Test prompt", correlation_id="test-unexpected")

        assert adapter.circuit_breaker_failures == 1

    # --- Circuit Breaker Tests ---

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_threshold(self, adapter):
        """Test that circuit breaker opens after reaching failure threshold."""
        adapter.circuit_breaker_threshold = 3
        adapter.client.generate_text.side_effect = Exception("Test error")

        # Fail 3 times to open the circuit
        for i in range(3):
            with pytest.raises(APIError):
                await adapter.generate(f"Test {i}")

        assert adapter.circuit_breaker_state == "open"
        assert adapter.circuit_breaker_failures == 3

        # Next call should fail immediately
        with pytest.raises(CircuitBreakerOpenError):
            await adapter.generate("Test when open")

    @pytest.mark.asyncio
    async def test_circuit_breaker_half_open_after_timeout(self, adapter):
        """Test that circuit breaker enters half-open state after timeout."""
        adapter.circuit_breaker_state = "open"
        adapter.circuit_breaker_last_failure_time = time.time() - 61  # 61 seconds ago
        adapter.circuit_breaker_timeout = 60

        adapter.client.generate_text.return_value = "Success after recovery"

        result = await adapter.generate("Test recovery")

        assert result == "Success after recovery"
        assert adapter.circuit_breaker_state == "closed"
        assert adapter.circuit_breaker_failures == 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_resets_on_success(self, adapter):
        """Test that circuit breaker resets on successful call."""
        adapter.circuit_breaker_failures = 2
        adapter.circuit_breaker_state = "half-open"
        adapter.client.generate_text.return_value = "Success"

        result = await adapter.generate("Test reset")

        assert result == "Success"
        assert adapter.circuit_breaker_state == "closed"
        assert adapter.circuit_breaker_failures == 0

    # --- PII Sanitization Tests ---

    def test_sanitize_prompt_removes_email(self, adapter):
        """Test that email addresses are masked in prompt."""
        prompt = "Contact me at john.doe@example.com for details"
        sanitized = adapter._sanitize_prompt(prompt)
        assert "[EMAIL]" in sanitized
        assert "john.doe@example.com" not in sanitized

    def test_sanitize_prompt_removes_phone(self, adapter):
        """Test that phone numbers are masked in prompt."""
        prompt = "Call me at (555) 123-4567 or 555.123.4567"
        sanitized = adapter._sanitize_prompt(prompt)
        assert "[PHONE]" in sanitized
        assert "555" not in sanitized

    def test_sanitize_prompt_removes_ssn(self, adapter):
        """Test that SSNs are masked in prompt."""
        prompt = "SSN: 123-45-6789"
        sanitized = adapter._sanitize_prompt(prompt)
        assert "[SSN]" in sanitized
        assert "123-45-6789" not in sanitized

    def test_sanitize_prompt_removes_control_chars(self, adapter):
        """Test that control characters are removed from prompt."""
        prompt = "Test\x00with\x1fcontrol\x7fchars"
        sanitized = adapter._sanitize_prompt(prompt)
        assert sanitized == "Testwithcontrolchars"

    # --- Context Manager Tests ---

    @pytest.mark.asyncio
    async def test_async_context_manager(self, valid_settings):
        """Test async context manager functionality."""
        with patch("self_fixing_engineer.arbiter.plugins.anthropic_adapter.LLMClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.aclose_session = AsyncMock()

            async with AnthropicAdapter(valid_settings) as adapter:
                assert adapter is not None

            mock_instance.aclose_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_handles_close_error(self, valid_settings):
        """Test that context manager handles errors during session close."""
        with patch("self_fixing_engineer.arbiter.plugins.anthropic_adapter.LLMClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.aclose_session = AsyncMock(
                side_effect=Exception("Close error")
            )

            async with AnthropicAdapter(valid_settings):
                pass  # Should not raise even if close fails

    # --- Metrics Tests ---

    @pytest.mark.asyncio
    async def test_metrics_recorded_on_success(self, adapter):
        """Test that metrics are recorded on successful generation."""
        with (
            patch(
                "self_fixing_engineer.arbiter.plugins.anthropic_adapter.anthropic_call_latency_seconds"
            ) as mock_latency,
            patch(
                "self_fixing_engineer.arbiter.plugins.anthropic_adapter.anthropic_call_success_total"
            ) as mock_success,
        ):

            adapter.client.generate_text.return_value = "Success"
            await adapter.generate("Test", correlation_id="metrics-test")

            mock_latency.labels.assert_called()
            mock_success.labels.assert_called()

    @pytest.mark.asyncio
    async def test_metrics_recorded_on_failure(self, adapter):
        """Test that metrics are recorded on failed generation."""
        with (
            patch(
                "self_fixing_engineer.arbiter.plugins.anthropic_adapter.anthropic_call_latency_seconds"
            ) as mock_latency,
            patch(
                "self_fixing_engineer.arbiter.plugins.anthropic_adapter.anthropic_call_errors_total"
            ) as mock_errors,
        ):

            adapter.client.generate_text.side_effect = Exception("Test error")

            with pytest.raises(APIError):
                await adapter.generate("Test", correlation_id="metrics-fail-test")

            mock_latency.labels.assert_called()
            mock_errors.labels.assert_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
