# test_gemini_adapter.py
import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, Mock, patch

import google.api_core.exceptions as google_exceptions
import pytest

# Import the adapter and related exceptions
from self_fixing_engineer.arbiter.plugins.gemini_adapter import GeminiAdapter
from self_fixing_engineer.arbiter.plugins.llm_client import (
    APIError,
    AuthError,
    CircuitBreakerOpenError,
    LLMClientError,
    RateLimitError,
    TimeoutError,
)
from tenacity import RetryError


class TestGeminiAdapter:
    """Test suite for GeminiAdapter."""

    @pytest.fixture
    def valid_settings(self) -> Dict[str, Any]:
        """Returns valid settings for initializing GeminiAdapter."""
        return {
            "GEMINI_API_KEY": "test-gemini-api-key",
            "LLM_MODEL": "gemini-1.5-flash",
            "LLM_API_TIMEOUT_SECONDS": 60,
            "LLM_API_RETRY_ATTEMPTS": 3,
            "LLM_API_RETRY_BACKOFF_FACTOR": 2.0,
            "CIRCUIT_BREAKER_THRESHOLD": 5,
            "CIRCUIT_BREAKER_TIMEOUT_SECONDS": 300,
            "SECURITY_CONFIG": {
                "compliance_frameworks": ["GDPR", "CCPA"],
                "pii_patterns": {"CUSTOM_ID": r"ID-\d{6}"},
            },
        }

    @pytest.fixture
    async def adapter(self, valid_settings):
        """Creates a GeminiAdapter instance with mocked LLMClient."""
        with patch("self_fixing_engineer.arbiter.plugins.gemini_adapter.LLMClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.model = "gemini-1.5-flash"

            adapter = GeminiAdapter(valid_settings)
            adapter.client = mock_instance
            yield adapter

    # --- Initialization Tests ---

    def test_init_with_valid_settings(self, valid_settings):
        """Test successful initialization with valid settings."""
        with patch("self_fixing_engineer.arbiter.plugins.gemini_adapter.LLMClient") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            adapter = GeminiAdapter(valid_settings)

            assert adapter.provider == "gemini"
            assert adapter.circuit_breaker_state == "closed"
            assert adapter.circuit_breaker_failures == 0
            assert adapter.circuit_breaker_threshold == 5
            assert adapter.circuit_breaker_timeout == 300
            assert adapter.security_config["compliance_frameworks"] == ["GDPR", "CCPA"]
            mock_client.assert_called_once()

    def test_init_missing_api_key(self):
        """Test initialization fails when API key is missing."""
        settings = {"LLM_MODEL": "gemini-1.5-flash"}

        with pytest.raises(ValueError, match="Missing API key for Gemini provider"):
            GeminiAdapter(settings)

    def test_init_with_llm_client_failure(self, valid_settings):
        """Test initialization fails when LLMClient raises exception."""
        with patch(
            "self_fixing_engineer.arbiter.plugins.gemini_adapter.LLMClient",
            side_effect=Exception("Connection failed"),
        ):
            with pytest.raises(ValueError, match="Failed to initialize LLMClient"):
                GeminiAdapter(valid_settings)

    def test_init_with_none_client(self, valid_settings):
        """Test initialization fails when LLMClient returns None."""
        with patch("self_fixing_engineer.arbiter.plugins.gemini_adapter.LLMClient", return_value=None):
            with pytest.raises(ValueError, match="LLMClient initialization failed"):
                GeminiAdapter(valid_settings)

    def test_init_with_default_values(self):
        """Test initialization with minimal settings uses defaults."""
        settings = {"GEMINI_API_KEY": "test-key"}

        with patch("self_fixing_engineer.arbiter.plugins.gemini_adapter.LLMClient") as mock_client:
            mock_instance = Mock()
            mock_instance.model = "gemini-1.5-flash"
            mock_client.return_value = mock_instance

            adapter = GeminiAdapter(settings)

            assert adapter.circuit_breaker_threshold == 5
            assert adapter.circuit_breaker_timeout == 300
            assert adapter.security_config == {}

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
        with pytest.raises(ValueError, match="Prompt is too long"):
            await adapter.generate(long_prompt)

    @pytest.mark.asyncio
    async def test_generate_invalid_max_tokens(self, adapter):
        """Test that invalid max_tokens raises ValueError."""
        with pytest.raises(ValueError, match="max_tokens must be between 1 and 8192"):
            await adapter.generate("test", max_tokens=9000)

        with pytest.raises(ValueError, match="max_tokens must be between 1 and 8192"):
            await adapter.generate("test", max_tokens=0)

    @pytest.mark.asyncio
    async def test_generate_invalid_temperature(self, adapter):
        """Test that invalid temperature raises ValueError."""
        with pytest.raises(ValueError, match="temperature must be between 0.0 and 2.0"):
            await adapter.generate("test", temperature=2.5)

        with pytest.raises(ValueError, match="temperature must be between 0.0 and 2.0"):
            await adapter.generate("test", temperature=-0.1)

    # --- Successful Generation Tests ---

    @pytest.mark.asyncio
    async def test_generate_success(self, adapter):
        """Test successful text generation."""
        adapter.client.generate_text.return_value = "Generated response from Gemini"

        result = await adapter.generate(
            "Test prompt for Gemini",
            max_tokens=1000,
            temperature=0.7,
            correlation_id="test-gemini-123",
        )

        assert result == "Generated response from Gemini"
        adapter.client.generate_text.assert_called_once_with(
            "Test prompt for Gemini",
            max_tokens=1000,
            temperature=0.7,
            correlation_id="test-gemini-123",
        )
        assert adapter.circuit_breaker_state == "closed"
        assert adapter.circuit_breaker_failures == 0

    @pytest.mark.asyncio
    async def test_generate_with_high_temperature(self, adapter):
        """Test generation with maximum allowed temperature."""
        adapter.client.generate_text.return_value = "Creative response"

        result = await adapter.generate("Be creative", temperature=2.0)

        assert result == "Creative response"
        assert adapter.circuit_breaker_state == "closed"

    # --- Error Handling Tests ---

    @pytest.mark.asyncio
    async def test_generate_retry_error(self, adapter):
        """Test handling of RetryError when all retries are exhausted."""
        retry_error = RetryError("All retries exhausted")
        retry_error.__cause__ = Exception("Connection failed")
        adapter.client.generate_text.side_effect = retry_error

        with pytest.raises(APIError, match="failed after multiple retries"):
            await adapter.generate("Test prompt", correlation_id="test-retry")

        assert adapter.circuit_breaker_failures == 1
        assert adapter.circuit_breaker_state == "closed"

    @pytest.mark.asyncio
    async def test_generate_timeout_error(self, adapter):
        """Test handling of timeout errors."""
        timeout_exception = asyncio.TimeoutError("Request timed out")
        llm_error = LLMClientError("Timeout")
        llm_error.__cause__ = timeout_exception
        adapter.client.generate_text.side_effect = llm_error

        with pytest.raises(TimeoutError, match="API call timed out"):
            await adapter.generate("Test prompt", correlation_id="test-timeout")

        assert adapter.circuit_breaker_failures == 1

    @pytest.mark.asyncio
    async def test_generate_google_auth_error(self, adapter):
        """Test handling of Google authentication errors."""
        auth_exception = google_exceptions.GoogleAPICallError("Unauthorized")
        auth_exception.code = 401
        llm_error = LLMClientError("Auth error")
        llm_error.__cause__ = auth_exception
        adapter.client.generate_text.side_effect = llm_error

        with pytest.raises(AuthError, match="authentication error"):
            await adapter.generate("Test prompt", correlation_id="test-auth")

        assert adapter.circuit_breaker_failures == 1

    @pytest.mark.asyncio
    async def test_generate_google_rate_limit_error(self, adapter):
        """Test handling of Google rate limit errors."""
        rate_exception = google_exceptions.GoogleAPICallError("Rate limit exceeded")
        rate_exception.code = 429
        llm_error = LLMClientError("Rate limited")
        llm_error.__cause__ = rate_exception
        adapter.client.generate_text.side_effect = llm_error

        with pytest.raises(RateLimitError, match="rate limit exceeded"):
            await adapter.generate("Test prompt", correlation_id="test-rate")

        assert adapter.circuit_breaker_failures == 1

    @pytest.mark.asyncio
    async def test_generate_google_api_error_with_status(self, adapter):
        """Test handling of Google API errors with status codes."""
        api_exception = google_exceptions.GoogleAPICallError("Internal server error")
        api_exception.code = 500
        llm_error = LLMClientError("API error")
        llm_error.__cause__ = api_exception
        adapter.client.generate_text.side_effect = llm_error

        with pytest.raises(APIError, match="API error.*status 500"):
            await adapter.generate("Test prompt", correlation_id="test-api")

        assert adapter.circuit_breaker_failures == 1

    @pytest.mark.asyncio
    async def test_generate_google_api_error_no_code(self, adapter):
        """Test handling of Google API errors without status code."""
        api_exception = google_exceptions.GoogleAPICallError("Unknown error")
        # Don't set a code
        llm_error = LLMClientError("API error")
        llm_error.__cause__ = api_exception
        adapter.client.generate_text.side_effect = llm_error

        with pytest.raises(APIError, match="Gemini API error"):
            await adapter.generate("Test prompt", correlation_id="test-api-no-code")

    @pytest.mark.asyncio
    async def test_generate_unexpected_llm_error(self, adapter):
        """Test handling of unexpected LLMClient errors."""
        unexpected_exception = RuntimeError("Unexpected internal error")
        llm_error = LLMClientError("Unexpected")
        llm_error.__cause__ = unexpected_exception
        adapter.client.generate_text.side_effect = llm_error

        with pytest.raises(APIError, match="Unexpected Gemini API error"):
            await adapter.generate("Test prompt", correlation_id="test-unexpected")

    @pytest.mark.asyncio
    async def test_generate_critical_error(self, adapter):
        """Test handling of critical unhandled errors."""
        adapter.client.generate_text.side_effect = Exception("Critical system failure")

        with pytest.raises(APIError, match="Critical unhandled error"):
            await adapter.generate("Test prompt", correlation_id="test-critical")

        assert adapter.circuit_breaker_failures == 1

    # --- Circuit Breaker Tests ---

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_threshold(self, adapter):
        """Test that circuit breaker opens after reaching failure threshold."""
        adapter.circuit_breaker_threshold = 3
        adapter.client.generate_text.side_effect = Exception("API failure")

        # Fail 3 times to open the circuit
        for i in range(3):
            with pytest.raises(APIError):
                await adapter.generate(f"Test {i}")

        assert adapter.circuit_breaker_state == "open"
        assert adapter.circuit_breaker_failures == 3

        # Next call should fail immediately with CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError, match="Circuit breaker is open"):
            await adapter.generate("Test when open")

    @pytest.mark.asyncio
    async def test_circuit_breaker_half_open_after_timeout(self, adapter):
        """Test that circuit breaker enters half-open state after timeout."""
        # Set circuit breaker to open state
        adapter.circuit_breaker_state = "open"
        adapter.circuit_breaker_last_failure_time = (
            asyncio.get_event_loop().time() - 301
        )
        adapter.circuit_breaker_timeout = 300

        adapter.client.generate_text.return_value = "Success after recovery"

        result = await adapter.generate("Test recovery")

        assert result == "Success after recovery"
        assert adapter.circuit_breaker_state == "closed"
        assert adapter.circuit_breaker_failures == 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_stays_closed_on_success(self, adapter):
        """Test that circuit breaker stays closed on successful calls."""
        adapter.client.generate_text.return_value = "Success"

        for i in range(3):
            result = await adapter.generate(f"Test {i}")
            assert result == "Success"

        assert adapter.circuit_breaker_state == "closed"
        assert adapter.circuit_breaker_failures == 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_half_open_to_open_on_failure(self, adapter):
        """Test circuit breaker goes from half-open to open on failure."""
        adapter.circuit_breaker_state = "half-open"
        adapter.client.generate_text.side_effect = Exception("Still failing")

        with pytest.raises(APIError):
            await adapter.generate("Test half-open failure")

        assert adapter.circuit_breaker_state == "open"

    # --- PII Sanitization Tests ---

    def test_sanitize_prompt_masks_email(self, adapter):
        """Test that email addresses are masked."""
        prompt = "Contact john.doe@example.com for info"
        sanitized = adapter._sanitize_prompt(prompt)
        assert "[EMAIL]" in sanitized
        assert "john.doe@example.com" not in sanitized

    def test_sanitize_prompt_masks_phone(self, adapter):
        """Test that phone numbers are masked."""
        prompt = "Call (555) 123-4567 or 555.987.6543"
        sanitized = adapter._sanitize_prompt(prompt)
        assert sanitized.count("[PHONE]") == 2
        assert "555" not in sanitized

    def test_sanitize_prompt_masks_ssn(self, adapter):
        """Test that SSNs are masked."""
        prompt = "SSN: 123-45-6789"
        sanitized = adapter._sanitize_prompt(prompt)
        assert "[SSN]" in sanitized
        assert "123-45-6789" not in sanitized

    def test_sanitize_prompt_masks_credit_card(self, adapter):
        """Test that credit card numbers are masked."""
        prompt = "Card: 4111 1111 1111 1111"
        sanitized = adapter._sanitize_prompt(prompt)
        assert "[CREDIT_CARD]" in sanitized
        assert "4111" not in sanitized

    def test_sanitize_prompt_masks_address(self, adapter):
        """Test that addresses are masked."""
        prompt = "Lives at 123 Main Street"
        sanitized = adapter._sanitize_prompt(prompt)
        assert "[ADDRESS]" in sanitized
        assert "Main Street" not in sanitized

    def test_sanitize_prompt_custom_pii_pattern(self, valid_settings):
        """Test that custom PII patterns from config are applied."""
        with patch("self_fixing_engineer.arbiter.plugins.gemini_adapter.LLMClient") as mock_client:
            mock_client.return_value = Mock()
            adapter = GeminiAdapter(valid_settings)

            prompt = "User ID: ID-123456"
            sanitized = adapter._sanitize_prompt(prompt)
            assert "[CUSTOM_ID]" in sanitized
            assert "ID-123456" not in sanitized

    def test_sanitize_prompt_removes_control_chars(self, adapter):
        """Test that control characters are removed."""
        prompt = "Test\x00with\x1fcontrol\x7f\x9fchars"
        sanitized = adapter._sanitize_prompt(prompt)
        assert sanitized == "Testwithcontrolchars"

    # --- Context Manager Tests ---

    @pytest.mark.asyncio
    async def test_async_context_manager(self, valid_settings):
        """Test async context manager functionality."""
        with patch("self_fixing_engineer.arbiter.plugins.gemini_adapter.LLMClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.aclose_session = AsyncMock()

            async with GeminiAdapter(valid_settings) as adapter:
                assert adapter is not None

            mock_instance.aclose_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_handles_close_error(self, valid_settings):
        """Test that context manager handles errors during session close."""
        with patch("self_fixing_engineer.arbiter.plugins.gemini_adapter.LLMClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance
            mock_instance.aclose_session = AsyncMock(
                side_effect=Exception("Close failed")
            )

            async with GeminiAdapter(valid_settings):
                pass  # Should not raise even if close fails

    # --- Metrics Tests ---

    @pytest.mark.asyncio
    async def test_metrics_recorded_on_success(self, adapter):
        """Test that metrics are recorded on successful generation."""
        with (
            patch(
                "self_fixing_engineer.arbiter.plugins.gemini_adapter.gemini_call_latency_seconds"
            ) as mock_latency,
            patch(
                "self_fixing_engineer.arbiter.plugins.gemini_adapter.gemini_call_success_total"
            ) as mock_success,
        ):

            adapter.client.generate_text.return_value = "Success"
            await adapter.generate("Test", correlation_id="metrics-test")

            mock_latency.labels.assert_called_with(
                provider="gemini",
                model="gemini-1.5-flash",
                correlation_id="metrics-test",
            )
            mock_success.labels.assert_called_with(
                provider="gemini",
                model="gemini-1.5-flash",
                correlation_id="metrics-test",
            )

    @pytest.mark.asyncio
    async def test_metrics_recorded_on_failure(self, adapter):
        """Test that error metrics are recorded on failed generation."""
        with (
            patch(
                "self_fixing_engineer.arbiter.plugins.gemini_adapter.gemini_call_latency_seconds"
            ) as mock_latency,
            patch(
                "self_fixing_engineer.arbiter.plugins.gemini_adapter.gemini_call_errors_total"
            ) as mock_errors,
        ):

            adapter.client.generate_text.side_effect = Exception("Test error")

            with pytest.raises(APIError):
                await adapter.generate("Test", correlation_id="metrics-fail")

            mock_latency.labels.assert_called()
            mock_errors.labels.assert_called()

    @pytest.mark.asyncio
    async def test_metrics_circuit_breaker_error(self, adapter):
        """Test that circuit breaker errors are recorded in metrics."""
        adapter.circuit_breaker_state = "open"
        adapter.circuit_breaker_last_failure_time = asyncio.get_event_loop().time()

        with patch(
            "self_fixing_engineer.arbiter.plugins.gemini_adapter.gemini_call_errors_total"
        ) as mock_errors:
            with pytest.raises(CircuitBreakerOpenError):
                await adapter.generate("Test", correlation_id="cb-test")

            mock_errors.labels.assert_called_with(
                provider="gemini",
                model="gemini-1.5-flash",
                correlation_id="cb-test",
                error_type="circuit_breaker",
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
