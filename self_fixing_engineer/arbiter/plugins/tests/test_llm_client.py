# test_llm_client.py
import pytest
import time
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, Any, List

# Import the client and related exceptions
from arbiter.plugins.llm_client import (
    LLMClient,
    LoadBalancedLLMClient,
    LLMClientError,
    APIError,
    InputValidationError,
    CircuitBreakerOpenError
)


class TestLLMClient:
    """Test suite for LLMClient."""

    @pytest.fixture
    def valid_openai_config(self) -> Dict[str, Any]:
        """Returns valid OpenAI configuration."""
        return {
            "provider": "openai",
            "api_key": "test-openai-key",
            "model": "gpt-4o-mini",
            "timeout": 30,
            "retry_attempts": 2,
            "retry_backoff_factor": 1.5
        }

    @pytest.fixture
    def valid_anthropic_config(self) -> Dict[str, Any]:
        """Returns valid Anthropic configuration."""
        return {
            "provider": "anthropic",
            "api_key": "test-anthropic-key",
            "model": "claude-3-sonnet-20240229",
            "timeout": 30,
            "retry_attempts": 2,
            "retry_backoff_factor": 1.5
        }

    @pytest.fixture
    def valid_gemini_config(self) -> Dict[str, Any]:
        """Returns valid Gemini configuration."""
        return {
            "provider": "gemini",
            "api_key": "test-gemini-key",
            "model": "gemini-1.5-flash",
            "timeout": 30,
            "retry_attempts": 2,
            "retry_backoff_factor": 1.5
        }

    @pytest.fixture
    def valid_ollama_config(self) -> Dict[str, Any]:
        """Returns valid Ollama configuration."""
        return {
            "provider": "ollama",
            "api_key": None,
            "model": "llama3",
            "base_url": "http://localhost:11434",
            "timeout": 30,
            "retry_attempts": 2,
            "retry_backoff_factor": 1.5
        }

    # --- Initialization Tests ---

    def test_init_invalid_provider(self):
        """Test initialization with invalid provider name."""
        with pytest.raises(InputValidationError, match="Provider name must be"):
            LLMClient("", "api-key", "model")
        
        with pytest.raises(InputValidationError, match="Provider name must be"):
            LLMClient(None, "api-key", "model")

    def test_init_invalid_model(self):
        """Test initialization with invalid model name."""
        with pytest.raises(InputValidationError, match="Model name must be"):
            LLMClient("openai", "api-key", "")

    def test_init_invalid_timeout(self):
        """Test initialization with invalid timeout."""
        with pytest.raises(InputValidationError, match="Timeout must be"):
            LLMClient("openai", "api-key", "gpt-4", timeout=-1)

    def test_init_invalid_retry_attempts(self):
        """Test initialization with invalid retry attempts."""
        with pytest.raises(InputValidationError, match="Retry attempts must be"):
            LLMClient("openai", "api-key", "gpt-4", retry_attempts=-1)

    def test_init_invalid_retry_backoff(self):
        """Test initialization with invalid retry backoff factor."""
        with pytest.raises(InputValidationError, match="Retry backoff factor must be"):
            LLMClient("openai", "api-key", "gpt-4", retry_backoff_factor=0.5)

    def test_init_missing_api_key(self):
        """Test initialization requires API key for commercial providers."""
        with pytest.raises(ValueError, match="Missing API key"):
            LLMClient("openai", None, "gpt-4")
        
        with pytest.raises(ValueError, match="Missing API key"):
            LLMClient("anthropic", None, "claude-3")
        
        with pytest.raises(ValueError, match="Missing API key"):
            LLMClient("gemini", None, "gemini-1.5")

    def test_init_unsupported_provider(self):
        """Test initialization with unsupported provider."""
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            LLMClient("unknown_provider", "api-key", "model")

    @patch('arbiter.plugins.llm_client.AsyncOpenAI')
    def test_init_openai_success(self, mock_openai):
        """Test successful OpenAI client initialization."""
        client = LLMClient("openai", "test-key", "gpt-4o-mini")
        assert client.provider == "openai"
        assert client.model == "gpt-4o-mini"
        assert client.circuit_breaker_state == "closed"
        mock_openai.assert_called_once()

    @patch('arbiter.plugins.llm_client.AsyncAnthropic')
    def test_init_anthropic_success(self, mock_anthropic):
        """Test successful Anthropic client initialization."""
        client = LLMClient("anthropic", "test-key", "claude-3")
        assert client.provider == "anthropic"
        assert client.model == "claude-3"
        assert client.circuit_breaker_state == "closed"
        mock_anthropic.assert_called_once()

    @patch('arbiter.plugins.llm_client.genai.configure')
    @patch('arbiter.plugins.llm_client.genai.GenerativeModel')
    def test_init_gemini_success(self, mock_model, mock_configure):
        """Test successful Gemini client initialization."""
        mock_model.return_value = Mock()
        client = LLMClient("gemini", "test-key", "gemini-1.5-flash")
        assert client.provider == "gemini"
        assert client.model == "gemini-1.5-flash"
        mock_configure.assert_called_once_with(api_key="test-key")

    def test_init_ollama_success(self):
        """Test successful Ollama client initialization."""
        client = LLMClient("ollama", None, "llama3", base_url="http://localhost:11434")
        assert client.provider == "ollama"
        assert client.model == "llama3"
        assert client.base_url == "http://localhost:11434"

    # --- Input Validation Tests ---

    @pytest.mark.asyncio
    async def test_generate_text_invalid_prompt(self):
        """Test generate_text with invalid prompt."""
        with patch('arbiter.plugins.llm_client.AsyncOpenAI'):
            client = LLMClient("openai", "test-key", "gpt-4")
            
            with pytest.raises(InputValidationError, match="Prompt must be"):
                await client.generate_text("")
            
            with pytest.raises(InputValidationError, match="Prompt must be"):
                await client.generate_text("x" * 100001)

    @pytest.mark.asyncio
    async def test_generate_text_invalid_max_tokens(self):
        """Test generate_text with invalid max_tokens."""
        with patch('arbiter.plugins.llm_client.AsyncOpenAI'):
            client = LLMClient("openai", "test-key", "gpt-4")
            
            with pytest.raises(InputValidationError, match="max_tokens must be"):
                await client.generate_text("test", max_tokens=0)

    @pytest.mark.asyncio
    async def test_generate_text_invalid_temperature(self):
        """Test generate_text with invalid temperature."""
        with patch('arbiter.plugins.llm_client.AsyncOpenAI'):
            client = LLMClient("openai", "test-key", "gpt-4")
            
            with pytest.raises(InputValidationError, match="Temperature must be"):
                await client.generate_text("test", temperature=2.5)

    # --- PII Sanitization Tests ---

    def test_sanitize_prompt(self):
        """Test PII sanitization in prompts."""
        with patch('arbiter.plugins.llm_client.AsyncOpenAI'):
            client = LLMClient("openai", "test-key", "gpt-4")
            
            # Test email masking
            sanitized = client._sanitize_prompt("Contact john@example.com")
            assert "[EMAIL_MASKED]" in sanitized
            
            # Test phone masking
            sanitized = client._sanitize_prompt("Call (555) 123-4567")
            assert "[PHONE_MASKED]" in sanitized
            
            # Test SSN masking
            sanitized = client._sanitize_prompt("SSN: 123-45-6789")
            assert "[SSN_MASKED]" in sanitized
            
            # Test credit card masking
            sanitized = client._sanitize_prompt("Card: 4111-1111-1111-1111")
            assert "[CREDIT_CARD_MASKED]" in sanitized

    # --- Circuit Breaker Tests ---

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_threshold(self):
        """Test circuit breaker opens after failure threshold."""
        with patch('arbiter.plugins.llm_client.AsyncOpenAI') as mock_openai:
            mock_client = AsyncMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.side_effect = Exception("API Error")
            
            client = LLMClient("openai", "test-key", "gpt-4", retry_attempts=0)
            client.circuit_breaker_threshold = 3
            
            # Fail 3 times
            for _ in range(3):
                with pytest.raises(LLMClientError):
                    await client.generate_text("test")
            
            assert client.circuit_breaker_state == "open"
            
            # Next call should fail immediately
            with pytest.raises(CircuitBreakerOpenError):
                await client.generate_text("test")

    def test_circuit_breaker_half_open_after_timeout(self):
        """Test circuit breaker enters half-open state after timeout."""
        with patch('arbiter.plugins.llm_client.AsyncOpenAI'):
            client = LLMClient("openai", "test-key", "gpt-4")
            client.circuit_breaker_state = "open"
            client.circuit_breaker_last_failure_time = time.monotonic() - 301
            client.circuit_breaker_timeout = 300
            
            # Should not raise when checking
            client._check_circuit_breaker()
            assert client.circuit_breaker_state == "half-open"

    # --- Provider-Specific Generation Tests ---

    @pytest.mark.asyncio
    async def test_openai_generate_success(self):
        """Test successful OpenAI text generation."""
        with patch('arbiter.plugins.llm_client.AsyncOpenAI') as mock_openai:
            mock_client = AsyncMock()
            mock_openai.return_value = mock_client
            
            mock_response = Mock()
            mock_response.choices = [Mock(message=Mock(content="Generated text"))]
            mock_client.chat.completions.create.return_value = mock_response
            
            client = LLMClient("openai", "test-key", "gpt-4")
            result = await client.generate_text("Test prompt")
            
            assert result == "Generated text"

    @pytest.mark.asyncio
    async def test_anthropic_generate_success(self):
        """Test successful Anthropic text generation."""
        with patch('arbiter.plugins.llm_client.AsyncAnthropic') as mock_anthropic:
            mock_client = AsyncMock()
            mock_anthropic.return_value = mock_client
            
            mock_response = Mock()
            mock_response.content = [Mock(text="Claude response")]
            mock_client.messages.create.return_value = mock_response
            
            client = LLMClient("anthropic", "test-key", "claude-3")
            result = await client.generate_text("Test prompt")
            
            assert result == "Claude response"

    @pytest.mark.asyncio
    async def test_ollama_generate_success(self):
        """Test successful Ollama text generation."""
        with patch('arbiter.plugins.llm_client.LLMClient._get_ollama_session') as mock_session:
            mock_session_obj = AsyncMock()
            mock_session.return_value = mock_session_obj
            
            mock_response = AsyncMock()
            mock_response.content = AsyncMock()
            
            # Simulate streaming response
            async def mock_iter():
                yield b'{"response": "Part 1"}\n'
                yield b'{"response": " Part 2"}\n'
            
            mock_response.content.__aiter__ = mock_iter
            mock_response.raise_for_status = Mock()
            
            mock_session_obj.post.return_value.__aenter__.return_value = mock_response
            
            client = LLMClient("ollama", None, "llama3")
            result = await client.generate_text("Test prompt")
            
            assert "Part 1 Part 2" in result

    # --- Session Management Tests ---

    @pytest.mark.asyncio
    async def test_aclose_session_openai(self):
        """Test closing OpenAI session."""
        with patch('arbiter.plugins.llm_client.AsyncOpenAI') as mock_openai:
            mock_client = AsyncMock()
            mock_openai.return_value = mock_client
            
            client = LLMClient("openai", "test-key", "gpt-4")
            await client.aclose_session()
            
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_aclose_session_anthropic(self):
        """Test closing Anthropic session."""
        with patch('arbiter.plugins.llm_client.AsyncAnthropic') as mock_anthropic:
            mock_client = AsyncMock()
            mock_anthropic.return_value = mock_client
            
            client = LLMClient("anthropic", "test-key", "claude-3")
            await client.aclose_session()
            
            mock_client.aclose.assert_called_once()


class TestLoadBalancedLLMClient:
    """Test suite for LoadBalancedLLMClient."""

    @pytest.fixture
    def providers_config(self) -> List[Dict[str, Any]]:
        """Returns configuration for multiple providers."""
        return [
            {
                "provider": "openai",
                "api_key": "test-openai-key",
                "model": "gpt-4o-mini",
                "weight": 2.0,
                "timeout": 30,
                "retry_attempts": 1
            },
            {
                "provider": "anthropic",
                "api_key": "test-anthropic-key",
                "model": "claude-3",
                "weight": 1.0,
                "timeout": 30,
                "retry_attempts": 1
            }
        ]

    @pytest.fixture
    def mock_providers(self):
        """Mock the LLMClient initialization for all providers."""
        with patch('arbiter.plugins.llm_client.AsyncOpenAI'), \
             patch('arbiter.plugins.llm_client.AsyncAnthropic'), \
             patch('arbiter.plugins.llm_client.genai'):
            yield

    def test_init_no_providers(self):
        """Test initialization fails with empty provider config."""
        with pytest.raises(ValueError, match="No LLM providers successfully initialized"):
            LoadBalancedLLMClient([])

    def test_init_invalid_provider_config(self, mock_providers):
        """Test initialization skips invalid provider configs."""
        configs = [
            {"model": "gpt-4"},  # Missing provider
            {"provider": "openai"},  # Missing model
        ]
        
        with pytest.raises(ValueError, match="No LLM providers successfully initialized"):
            LoadBalancedLLMClient(configs)

    def test_init_success(self, providers_config, mock_providers):
        """Test successful initialization with multiple providers."""
        lb_client = LoadBalancedLLMClient(providers_config)
        
        assert len(lb_client.providers) == 2
        assert len(lb_client.active_providers) == 2
        assert "openai" in lb_client.provider_status
        assert "anthropic" in lb_client.provider_status

    def test_weighted_distribution(self, providers_config, mock_providers):
        """Test weighted provider distribution."""
        lb_client = LoadBalancedLLMClient(providers_config)
        
        # Count provider selections
        selections = {}
        for _ in range(30):
            provider = lb_client._select_provider()
            selections[provider.provider] = selections.get(provider.provider, 0) + 1
        
        # OpenAI has weight 2.0, Anthropic 1.0, so roughly 2:1 ratio
        assert selections.get("openai", 0) > selections.get("anthropic", 0)

    @pytest.mark.asyncio
    async def test_generate_text_success(self, providers_config, mock_providers):
        """Test successful text generation with load balancing."""
        lb_client = LoadBalancedLLMClient(providers_config)
        
        # Mock the first provider's generate
        with patch.object(lb_client.providers[0], '_handle_llm_call', return_value="Response from provider"):
            result = await lb_client.generate_text("Test prompt")
            assert result == "Response from provider"

    @pytest.mark.asyncio
    async def test_failover_to_next_provider(self, providers_config, mock_providers):
        """Test failover to next provider on failure."""
        lb_client = LoadBalancedLLMClient(providers_config)
        
        # Mock first provider to fail, second to succeed
        with patch.object(lb_client.providers[0], '_handle_llm_call', side_effect=APIError("Provider 1 failed")), \
             patch.object(lb_client.providers[1], '_handle_llm_call', return_value="Response from provider 2"):
            
            result = await lb_client.generate_text("Test prompt")
            assert result == "Response from provider 2"

    def test_provider_quarantine(self, providers_config, mock_providers):
        """Test provider quarantine after threshold failures."""
        lb_client = LoadBalancedLLMClient(providers_config)
        lb_client.FAILURE_QUARANTINE_THRESHOLD = 3
        
        # Simulate multiple failures for openai
        for _ in range(3):
            lb_client._update_provider_status("openai", success=False, is_retryable_error=False)
        
        assert lb_client.provider_status["openai"]["status"] == "unavailable"
        assert lb_client.provider_status["openai"]["consecutive_failures"] == 3

    def test_provider_recovery_from_quarantine(self, providers_config, mock_providers):
        """Test provider recovery from quarantine after timeout."""
        lb_client = LoadBalancedLLMClient(providers_config)
        
        # Put provider in quarantine
        lb_client.provider_status["openai"]["status"] = "unavailable"
        lb_client.provider_status["openai"]["last_error_time"] = time.monotonic() - 301
        lb_client.QUARANTINE_DURATION_SECONDS = 300
        
        # Select provider should attempt recovery
        lb_client._select_provider()
        # After timeout, it should try to use the provider again
        assert lb_client.provider_status["openai"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_all_providers_fail(self, providers_config, mock_providers):
        """Test behavior when all providers fail."""
        lb_client = LoadBalancedLLMClient(providers_config)
        
        # Mock all providers to fail
        for provider in lb_client.providers:
            with patch.object(provider, '_handle_llm_call', side_effect=APIError("Failed")):
                pass
        
        with patch.object(lb_client.providers[0], '_handle_llm_call', side_effect=APIError("Failed")), \
             patch.object(lb_client.providers[1], '_handle_llm_call', side_effect=APIError("Failed")):
            
            with pytest.raises(LLMClientError, match="All configured LLM providers failed"):
                await lb_client.generate_text("Test prompt")

    @pytest.mark.asyncio
    async def test_close_all_sessions(self, providers_config, mock_providers):
        """Test closing all provider sessions."""
        lb_client = LoadBalancedLLMClient(providers_config)
        
        # Mock aclose_session for all providers
        for provider in lb_client.providers:
            provider.aclose_session = AsyncMock()
        
        await lb_client.close_all_sessions()
        
        for provider in lb_client.providers:
            provider.aclose_session.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])