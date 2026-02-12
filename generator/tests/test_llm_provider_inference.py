# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test provider inference in call_ensemble_api
Tests that model configurations without provider are automatically inferred.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from runner.llm_client import LLMClient
from runner.runner_config import RunnerConfig
from runner.runner_errors import LLMError


@pytest.fixture
def mock_client_setup():
    """Set up a mocked LLMClient for testing."""
    with (
        patch("runner.llm_client.aioredis.from_url") as mock_redis_from_url,
        patch("runner.llm_client.LLMPluginManager") as mock_plugin_manager,
        patch("runner.llm_client.log_audit_event", new_callable=AsyncMock),
        patch("runner.llm_client.metrics") as mock_metrics,
    ):
        # Mock Redis
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

        # Mock plugin manager
        mock_plugin_manager.return_value.registry = {}
        future = asyncio.get_event_loop().create_future()
        future.set_result(None)
        mock_plugin_manager.return_value._load_task = future

        yield {
            "redis": mock_redis,
            "plugin_manager": mock_plugin_manager,
            "metrics": mock_metrics,
        }


@pytest.fixture
async def initialized_client(mock_client_setup):
    """Create and initialize an LLMClient."""
    config = RunnerConfig(
        redis_url="redis://localhost:6379/0",
        llm_provider="openai",
        default_llm_model="gpt-4",
        backend="local",
        framework="pytest",
        instance_id="test-provider-inference",
    )
    client = LLMClient(config)
    # Wait for initialization
    await client._is_initialized.wait()
    yield client
    await client.close()


@pytest.mark.asyncio
@pytest.mark.unit
class TestProviderInference:
    """Test provider inference in call_ensemble_api."""

    async def test_infer_provider_for_gpt_models(self, initialized_client):
        """Test that gpt- prefixed models are inferred as openai provider."""
        
        async def mock_call_llm_api(prompt, model=None, stream=False, provider=None, **kwargs):
            # Verify provider was inferred correctly
            assert provider == "openai", f"Expected provider='openai', got provider='{provider}'"
            return {"content": "test_response"}

        with patch.object(initialized_client, "call_llm_api", side_effect=mock_call_llm_api):
            models = [{"model": "gpt-4o"}]
            result = await initialized_client.call_ensemble_api("test prompt", models, "majority")
            
            assert result["content"] == "test_response"
            assert "ensemble_results" in result

    async def test_infer_provider_for_o1_models(self, initialized_client):
        """Test that o1 prefixed models are inferred as openai provider."""
        
        async def mock_call_llm_api(prompt, model=None, stream=False, provider=None, **kwargs):
            assert provider == "openai", f"Expected provider='openai', got provider='{provider}'"
            return {"content": "test_response"}

        with patch.object(initialized_client, "call_llm_api", side_effect=mock_call_llm_api):
            models = [{"model": "o1-preview"}]
            result = await initialized_client.call_ensemble_api("test prompt", models, "majority")
            
            assert result["content"] == "test_response"

    async def test_infer_provider_for_claude_models(self, initialized_client):
        """Test that claude prefixed models are inferred as claude provider."""
        
        async def mock_call_llm_api(prompt, model=None, stream=False, provider=None, **kwargs):
            assert provider == "claude", f"Expected provider='claude', got provider='{provider}'"
            return {"content": "test_response"}

        with patch.object(initialized_client, "call_llm_api", side_effect=mock_call_llm_api):
            models = [{"model": "claude-3-opus"}]
            result = await initialized_client.call_ensemble_api("test prompt", models, "majority")
            
            assert result["content"] == "test_response"

    async def test_infer_provider_for_gemini_models(self, initialized_client):
        """Test that gemini prefixed models are inferred as gemini provider."""
        
        async def mock_call_llm_api(prompt, model=None, stream=False, provider=None, **kwargs):
            assert provider == "gemini", f"Expected provider='gemini', got provider='{provider}'"
            return {"content": "test_response"}

        with patch.object(initialized_client, "call_llm_api", side_effect=mock_call_llm_api):
            models = [{"model": "gemini-pro"}]
            result = await initialized_client.call_ensemble_api("test prompt", models, "majority")
            
            assert result["content"] == "test_response"

    async def test_infer_provider_for_grok_models(self, initialized_client):
        """Test that grok prefixed models are inferred as grok provider."""
        
        async def mock_call_llm_api(prompt, model=None, stream=False, provider=None, **kwargs):
            assert provider == "grok", f"Expected provider='grok', got provider='{provider}'"
            return {"content": "test_response"}

        with patch.object(initialized_client, "call_llm_api", side_effect=mock_call_llm_api):
            models = [{"model": "grok-beta"}]
            result = await initialized_client.call_ensemble_api("test prompt", models, "majority")
            
            assert result["content"] == "test_response"

    async def test_fallback_to_config_default_provider(self, initialized_client):
        """Test that unknown models fallback to config default provider."""
        
        async def mock_call_llm_api(prompt, model=None, stream=False, provider=None, **kwargs):
            # Should fallback to config's llm_provider (openai)
            assert provider == "openai", f"Expected provider='openai' (config default), got provider='{provider}'"
            return {"content": "test_response"}

        with patch.object(initialized_client, "call_llm_api", side_effect=mock_call_llm_api):
            models = [{"model": "unknown-model-xyz"}]
            result = await initialized_client.call_ensemble_api("test prompt", models, "majority")
            
            assert result["content"] == "test_response"

    async def test_explicit_provider_not_overridden(self, initialized_client):
        """Test that explicitly specified providers are not overridden by inference."""
        
        async def mock_call_llm_api(prompt, model=None, stream=False, provider=None, **kwargs):
            # Should use the explicit provider, not infer
            assert provider == "claude", f"Expected provider='claude' (explicit), got provider='{provider}'"
            return {"content": "test_response"}

        with patch.object(initialized_client, "call_llm_api", side_effect=mock_call_llm_api):
            models = [{"provider": "claude", "model": "gpt-4o"}]  # Explicit claude despite gpt- prefix
            result = await initialized_client.call_ensemble_api("test prompt", models, "majority")
            
            assert result["content"] == "test_response"

    async def test_mixed_explicit_and_inferred_providers(self, initialized_client):
        """Test ensemble with mix of explicit and inferred providers."""
        
        call_count = {"count": 0}
        
        async def mock_call_llm_api(prompt, model=None, stream=False, provider=None, **kwargs):
            call_count["count"] += 1
            # First call should be explicit openai
            if call_count["count"] == 1:
                assert provider == "openai", f"Expected provider='openai' (explicit)"
            # Second call should infer claude
            elif call_count["count"] == 2:
                assert provider == "claude", f"Expected provider='claude' (inferred)"
            return {"content": "response_" + str(call_count["count"])}

        with patch.object(initialized_client, "call_llm_api", side_effect=mock_call_llm_api):
            models = [
                {"provider": "openai", "model": "gpt-4"},  # Explicit
                {"model": "claude-3-sonnet"},  # Inferred
            ]
            result = await initialized_client.call_ensemble_api("test prompt", models, "majority")
            
            assert call_count["count"] == 2
            assert "ensemble_results" in result
            assert len(result["ensemble_results"]) == 2

    async def test_error_when_no_valid_models(self, initialized_client):
        """Test that LLMError is raised when all models are invalid."""
        
        with pytest.raises(LLMError, match="No valid model configurations found"):
            # Missing both provider and model
            models = [{}]
            await initialized_client.call_ensemble_api("test prompt", models, "majority")

    async def test_error_when_model_missing(self, initialized_client):
        """Test that models without 'model' key are skipped."""
        
        async def mock_call_llm_api(prompt, model=None, stream=False, provider=None, **kwargs):
            return {"content": "test_response"}

        with patch.object(initialized_client, "call_llm_api", side_effect=mock_call_llm_api):
            # Mix of valid and invalid models
            models = [
                {"provider": "openai"},  # Missing model key
                {"model": "gpt-4o"},  # Valid, will infer provider
            ]
            result = await initialized_client.call_ensemble_api("test prompt", models, "majority")
            
            # Should succeed with only the valid model
            assert result["content"] == "test_response"
            assert len(result["ensemble_results"]) == 1
