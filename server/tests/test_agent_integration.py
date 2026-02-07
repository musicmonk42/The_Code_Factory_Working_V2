# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Integration tests for generator agent integration.

Tests verify that:
1. Agents can be imported when dependencies are available
2. Configuration is properly loaded and validated
3. Service initializes with correct agent availability
4. Proper error messages when agents unavailable
5. LLM configuration is properly passed to agents
"""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from server.config import (
    AgentConfig,
    LLMProviderConfig,
    ServerConfig,
    validate_configuration,
)
from server.services.omnicore_service import OmniCoreService


class TestConfigurationManagement:
    """Test configuration loading and validation."""

    def test_llm_config_loads_from_env(self, monkeypatch):
        """Test that LLM config loads from environment variables."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-123")
        monkeypatch.setenv("DEFAULT_LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4-turbo")
        
        config = LLMProviderConfig()
        
        assert config.default_llm_provider == "openai"
        assert config.openai_model == "gpt-4-turbo"
        assert config.get_provider_api_key("openai") == "test-key-123"

    def test_llm_config_masks_secrets(self, monkeypatch):
        """Test that API keys are properly masked in config."""
        monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
        
        config = LLMProviderConfig()
        
        # Secret should be masked in string representation
        config_str = str(config.openai_api_key)
        assert "secret-key" not in config_str
        assert "**" in config_str or "SecretStr" in config_str

    def test_llm_config_validates_provider(self):
        """Test that invalid provider raises validation error."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            LLMProviderConfig(default_llm_provider="invalid_provider")

    def test_llm_config_get_available_providers(self, monkeypatch):
        """Test getting list of configured providers."""
        monkeypatch.setenv("OPENAI_API_KEY", "key1")
        monkeypatch.setenv("GROK_API_KEY", "key2")
        
        config = LLMProviderConfig()
        available = config.get_available_providers()
        
        assert "openai" in available
        assert "grok" in available
        assert len(available) == 2

    def test_llm_config_is_provider_configured(self, monkeypatch):
        """Test checking if specific provider is configured."""
        monkeypatch.setenv("OPENAI_API_KEY", "key1")
        
        config = LLMProviderConfig()
        
        assert config.is_provider_configured("openai") is True
        assert config.is_provider_configured("grok") is False

    def test_agent_config_defaults(self):
        """Test agent configuration defaults."""
        config = AgentConfig()
        
        assert config.enable_codegen_agent is True
        assert config.enable_testgen_agent is True
        assert config.strict_mode is False
        assert config.use_llm_clarifier is True

    def test_agent_config_creates_upload_dir(self, tmp_path):
        """Test that upload directory is created."""
        upload_dir = tmp_path / "test_uploads"
        config = AgentConfig(upload_dir=upload_dir)
        
        assert config.upload_dir.exists()
        assert config.upload_dir.is_dir()

    def test_server_config_defaults(self):
        """Test server configuration defaults."""
        config = ServerConfig()
        
        assert config.app_env in ["development", "staging", "production"]
        assert config.log_level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

    def test_server_config_validates_log_level(self):
        """Test that invalid log level defaults to INFO."""
        config = ServerConfig(log_level="INVALID")
        
        assert config.log_level == "INFO"

    def test_validate_configuration_no_providers(self, monkeypatch):
        """Test validation warning when no providers configured."""
        # Clear any existing API keys
        for key in ["OPENAI_API_KEY", "GROK_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"]:
            monkeypatch.delenv(key, raising=False)
        
        # Clear cache to force reload
        from server.config import get_llm_config
        get_llm_config.cache_clear()
        
        results = validate_configuration()
        
        assert len(results["warnings"]) > 0
        assert any("No LLM providers" in w for w in results["warnings"])
        assert results["valid"] is True  # Still valid, just warning

    def test_validate_configuration_with_providers(self, monkeypatch):
        """Test validation passes with configured provider."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        
        # Clear cache to force reload
        from server.config import get_llm_config
        get_llm_config.cache_clear()
        
        results = validate_configuration()
        
        assert "openai" in results["available_providers"]
        assert results["valid"] is True


class TestOmniCoreServiceInitialization:
    """Test OmniCore service initialization and agent loading."""

    @patch("server.services.omnicore_service.CONFIG_AVAILABLE", True)
    @patch("server.services.omnicore_service.get_agent_config")
    @patch("server.services.omnicore_service.get_llm_config")
    def test_service_initializes_with_config(self, mock_llm_config, mock_agent_config):
        """Test service initializes with configuration."""
        mock_agent_config.return_value = Mock(
            strict_mode=False,
            use_llm_clarifier=False,
        )
        mock_llm_config.return_value = Mock(
            get_available_providers=Mock(return_value=[]),
        )
        
        service = OmniCoreService()
        
        assert service.agent_config is not None
        assert service.llm_config is not None
        assert isinstance(service.agents_available, dict)

    @patch("server.services.omnicore_service.CONFIG_AVAILABLE", True)
    @patch("server.services.omnicore_service.get_agent_config")
    @patch("server.services.omnicore_service.get_llm_config")
    def test_service_tracks_agent_availability(self, mock_llm_config, mock_agent_config):
        """Test service tracks which agents are available."""
        mock_agent_config.return_value = Mock(
            strict_mode=False,
            use_llm_clarifier=False,
        )
        mock_llm_config.return_value = Mock(
            get_available_providers=Mock(return_value=[]),
        )
        
        service = OmniCoreService()
        
        expected_agents = ["codegen", "testgen", "deploy", "docgen", "critique", "clarifier"]
        for agent in expected_agents:
            assert agent in service.agents_available

    @patch("server.services.omnicore_service.CONFIG_AVAILABLE", True)
    @patch("server.services.omnicore_service.get_agent_config")
    @patch("server.services.omnicore_service.get_llm_config")
    def test_service_logs_unavailable_agents(self, mock_llm_config, mock_agent_config, caplog):
        """Test service logs when agents are unavailable."""
        mock_agent_config.return_value = Mock(
            strict_mode=False,
            use_llm_clarifier=False,
        )
        mock_llm_config.return_value = Mock(
            get_available_providers=Mock(return_value=[]),
        )
        
        service = OmniCoreService()
        
        # Trigger lazy loading to get the log messages
        service._ensure_agents_loaded()
        
        # Check that unavailable agents were logged
        unavailable = [k for k, v in service.agents_available.items() if not v]
        if unavailable:
            assert any("unavailable" in record.message.lower() for record in caplog.records)

    @patch("server.services.omnicore_service.CONFIG_AVAILABLE", True)
    @patch("server.services.omnicore_service.get_agent_config")
    @patch("server.services.omnicore_service.get_llm_config")
    @patch.object(OmniCoreService, "_load_agents")
    def test_service_strict_mode_raises_on_missing_agents(self, mock_load_agents, mock_llm_config, mock_agent_config):
        """Test strict mode raises exception when agents unavailable."""
        mock_agent_config.return_value = Mock(
            strict_mode=True,
            use_llm_clarifier=False,
        )
        mock_llm_config.return_value = Mock(
            get_available_providers=Mock(return_value=[]),
        )
        
        # Mock _load_agents to simulate agents being unavailable
        # The agents_available dict will remain False (initialized to False in __init__)
        mock_load_agents.return_value = None
        
        # Create service (lazy loading means no immediate error)
        service = OmniCoreService()
        
        # Should raise when agents are lazy-loaded in strict mode with unavailable agents
        with pytest.raises(RuntimeError, match="STRICT_MODE"):
            service._ensure_agents_loaded()


class TestAgentIntegration:
    """Test agent method integration."""

    @pytest.fixture
    def mock_service(self):
        """Create service with mocked agents."""
        with patch("server.services.omnicore_service.CONFIG_AVAILABLE", True), \
             patch("server.services.omnicore_service.get_agent_config") as mock_agent_cfg, \
             patch("server.services.omnicore_service.get_llm_config") as mock_llm_cfg, \
             patch("server.services.omnicore_service.get_agent_loader") as mock_get_loader:
            
            # Configure mocks
            mock_agent_cfg.return_value = Mock(
                strict_mode=False,
                use_llm_clarifier=False,
            )
            mock_llm_cfg.return_value = Mock(
                get_available_providers=Mock(return_value=["openai"]),
                default_llm_provider="openai",
                get_provider_model=Mock(return_value="gpt-4"),
                get_provider_api_key=Mock(return_value="test-key"),
                enable_ensemble_mode=False,
                llm_timeout=300,
                llm_max_retries=3,
                llm_temperature=0.7,
                openai_base_url=None,
            )
            
            # Mock the agent loader to return actual mock functions
            mock_loader = MagicMock()
            mock_loader.is_agent_available = MagicMock(return_value=True)
            mock_loader.get_agent_error = MagicMock(return_value=None)
            
            # Create mock agent functions
            mock_codegen_func = AsyncMock(return_value={"main.py": "print('hello')"})
            mock_loader.get_agent = MagicMock(return_value=mock_codegen_func)
            
            mock_get_loader.return_value = mock_loader
            
            service = OmniCoreService()
            
            # Mark agents as already loaded to prevent _ensure_agents_loaded() from resetting state
            service._agents_loaded = True
            
            # Manually set agent availability and functions
            service.agents_available = {
                "codegen": True,
                "testgen": True,
                "deploy": True,
                "docgen": True,
                "critique": True,
                "clarifier": True,
            }
            
            service._codegen_func = mock_codegen_func
            service._testgen_class = Mock
            service._deploy_class = Mock
            service._docgen_class = Mock
            service._critique_class = Mock
            service._clarifier_llm_class = None
            
            return service

    @pytest.mark.asyncio
    async def test_run_codegen_success(self, mock_service, tmp_path):
        """Test successful code generation."""
        payload = {
            "requirements": "Create a hello world program",
            "language": "python",
        }
        
        result = await mock_service._run_codegen("test-job-1", payload)
        
        assert result["status"] == "completed"
        assert "generated_files" in result
        assert mock_service._codegen_func.called

    @pytest.mark.asyncio
    async def test_run_codegen_agent_unavailable(self, mock_service):
        """Test code generation when agent unavailable."""
        mock_service.agents_available["codegen"] = False
        
        payload = {
            "requirements": "Create a hello world program",
            "language": "python",
        }
        
        result = await mock_service._run_codegen("test-job-1", payload)
        
        assert result["status"] == "error"
        assert "not available" in result["message"]
        assert result["agent_available"] is False

    @pytest.mark.asyncio
    async def test_run_testgen_agent_unavailable(self, mock_service):
        """Test test generation when agent unavailable."""
        mock_service.agents_available["testgen"] = False
        
        payload = {
            "code_path": "./test/path",
        }
        
        result = await mock_service._run_testgen("test-job-1", payload)
        
        assert result["status"] == "error"
        assert "not available" in result["message"]

    @pytest.mark.asyncio
    async def test_run_clarifier_rule_based(self, mock_service):
        """Test clarifier using rule-based approach."""
        mock_service._clarifier_llm_class = None  # Force rule-based
        
        payload = {
            "readme_content": "Build a web application with database",
        }
        
        result = await mock_service._run_clarifier("test-job-1", payload)
        
        assert result["status"] == "clarification_initiated"
        assert "clarifications" in result
        assert len(result["clarifications"]) > 0

    @pytest.mark.asyncio
    async def test_check_agent_available(self, mock_service):
        """Test agent availability check helper."""
        # Test available agent
        available, error = mock_service._check_agent_available("codegen")
        assert available is True
        assert error is None
        
        # Test unavailable agent
        mock_service.agents_available["codegen"] = False
        available, error = mock_service._check_agent_available("codegen")
        assert available is False
        assert error is not None
        assert "not available" in error

    def test_build_llm_config(self, mock_service):
        """Test LLM configuration building."""
        config = mock_service._build_llm_config()
        
        assert "backend" in config
        assert "model" in config
        assert config["backend"] == "openai"
        assert config["model"]["openai"] == "gpt-4"

    def test_build_llm_config_sets_env_var(self, mock_service, monkeypatch):
        """Test that API key is set in environment."""
        config = mock_service._build_llm_config()
        
        # Check that env var was set
        assert os.environ.get("OPENAI_API_KEY") == "test-key"


class TestDispatcherIntegration:
    """Test dispatcher and routing integration."""

    @pytest.fixture
    def mock_service(self):
        """Create service with mocked agents."""
        with patch("server.services.omnicore_service.CONFIG_AVAILABLE", True), \
             patch("server.services.omnicore_service.get_agent_config") as mock_agent_cfg, \
             patch("server.services.omnicore_service.get_llm_config") as mock_llm_cfg, \
             patch("server.services.omnicore_service.get_agent_loader") as mock_get_loader:
            
            # Configure mocks
            mock_agent_cfg.return_value = Mock(
                strict_mode=False,
                use_llm_clarifier=False,
            )
            mock_llm_cfg.return_value = Mock(
                get_available_providers=Mock(return_value=["openai"]),
                default_llm_provider="openai",
                get_provider_model=Mock(return_value="gpt-4"),
                get_provider_api_key=Mock(return_value="key"),
                enable_ensemble_mode=False,
                llm_timeout=300,
                llm_max_retries=3,
                llm_temperature=0.7,
                openai_base_url=None,
            )
            
            # Mock the agent loader
            mock_loader = MagicMock()
            mock_loader.is_agent_available = MagicMock(return_value=True)
            mock_loader.get_agent_error = MagicMock(return_value=None)
            
            # Create mock agent function
            mock_codegen_func = AsyncMock(return_value={"main.py": "code"})
            mock_loader.get_agent = MagicMock(return_value=mock_codegen_func)
            
            mock_get_loader.return_value = mock_loader
            
            service = OmniCoreService()
            
            # Mark agents as already loaded to prevent _ensure_agents_loaded from resetting state
            service._agents_loaded = True
            
            service.agents_available["codegen"] = True
            service._codegen_func = mock_codegen_func
            
            return service

    @pytest.mark.asyncio
    async def test_route_job_to_generator(self, mock_service):
        """Test routing job to generator module."""
        payload = {
            "action": "run_codegen",
            "requirements": "Create a hello world",
        }
        
        result = await mock_service.route_job(
            job_id="test-123",
            source_module="api",
            target_module="generator",
            payload=payload,
        )
        
        assert result.get("routed") is True or "error" in result.get("data", {}).get("status", "")
        assert result["job_id"] == "test-123"
        assert "data" in result

    @pytest.mark.asyncio
    async def test_dispatch_generator_action(self, mock_service):
        """Test dispatching to specific generator agent."""
        payload = {
            "requirements": "Test requirement",
        }
        
        result = await mock_service._dispatch_generator_action(
            job_id="test-123",
            action="run_codegen",
            payload=payload,
        )
        
        assert result["status"] == "completed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
