"""
Unit tests for LLM Provider Utilities

Industry Standards Applied:
- Comprehensive coverage: Tests for happy path, edge cases, and error conditions
- Clear test names: Each test name describes what it tests
- Arrange-Act-Assert pattern: Clear test structure
- Parameterized tests: Efficient testing of multiple scenarios
- Documentation: Docstrings explain test purposes
"""

import pytest
from generator.utils.llm_provider_utils import (
    infer_provider_from_model,
    create_model_config,
    create_model_configs,
    validate_model_config,
    DEFAULT_PROVIDER,
    PROVIDER_MODEL_PREFIXES,
)


class TestInferProviderFromModel:
    """Test suite for provider inference logic."""
    
    @pytest.mark.parametrize("model_name,expected_provider", [
        # OpenAI models
        ("gpt-4o", "openai"),
        ("gpt-3.5-turbo", "openai"),
        ("gpt-4", "openai"),
        ("o1-preview", "openai"),
        ("o1-mini", "openai"),
        ("text-davinci-003", "openai"),
        
        # Claude models
        ("claude-3-opus", "claude"),
        ("claude-3-sonnet", "claude"),
        ("claude-2", "claude"),
        ("claude-instant-1", "claude"),
        
        # Gemini models
        ("gemini-pro", "gemini"),
        ("gemini-2.5-pro", "gemini"),
        ("gemini-ultra", "gemini"),
        
        # Grok models
        ("grok-4", "grok"),
        ("grok-beta", "grok"),
        
        # Case insensitivity
        ("GPT-4O", "openai"),
        ("Claude-3-Opus", "claude"),
        ("GEMINI-PRO", "gemini"),
    ])
    def test_infer_provider_from_known_models(self, model_name, expected_provider):
        """Test that known model names are correctly mapped to providers."""
        result = infer_provider_from_model(model_name)
        assert result == expected_provider
    
    def test_infer_provider_with_whitespace(self):
        """Test that whitespace is handled correctly."""
        assert infer_provider_from_model("  gpt-4o  ") == "openai"
        assert infer_provider_from_model("\tgemini-pro\n") == "gemini"
    
    def test_infer_provider_unknown_model(self):
        """Test that unknown models return the default provider."""
        result = infer_provider_from_model("unknown-model-xyz")
        assert result == DEFAULT_PROVIDER
    
    def test_infer_provider_empty_string_raises(self):
        """Test that empty string raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            infer_provider_from_model("")
    
    def test_infer_provider_none_raises(self):
        """Test that None raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            infer_provider_from_model(None)  # type: ignore
    
    def test_infer_provider_non_string_raises(self):
        """Test that non-string input raises TypeError."""
        with pytest.raises(TypeError, match="must be a string"):
            infer_provider_from_model(123)  # type: ignore
    
    def test_infer_provider_logs_warning_for_unknown(self, caplog):
        """Test that unknown models log a warning."""
        infer_provider_from_model("completely-unknown-model")
        assert "Could not infer provider" in caplog.text
        assert "completely-unknown-model" in caplog.text


class TestCreateModelConfig:
    """Test suite for model configuration creation."""
    
    def test_create_config_infers_provider(self):
        """Test that provider is inferred when not specified."""
        config = create_model_config("gpt-4o")
        assert config == {"provider": "openai", "model": "gpt-4o"}
    
    def test_create_config_respects_override(self):
        """Test that explicit provider overrides inference."""
        config = create_model_config("gpt-4o", provider="local")
        assert config == {"provider": "local", "model": "gpt-4o"}
    
    def test_create_config_various_models(self):
        """Test configuration creation for various model types."""
        configs = [
            create_model_config("claude-3-opus"),
            create_model_config("gemini-pro"),
            create_model_config("grok-4"),
        ]
        
        assert configs[0]["provider"] == "claude"
        assert configs[1]["provider"] == "gemini"
        assert configs[2]["provider"] == "grok"
    
    def test_create_config_empty_model_raises(self):
        """Test that empty model name raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            create_model_config("")
    
    def test_create_config_none_model_raises(self):
        """Test that None model name raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            create_model_config(None)  # type: ignore


class TestCreateModelConfigs:
    """Test suite for batch model configuration creation."""
    
    def test_create_configs_multiple_models(self):
        """Test creating configurations for multiple models."""
        model_names = ["gpt-4o", "claude-3-opus", "gemini-pro"]
        configs = create_model_configs(model_names)
        
        assert len(configs) == 3
        assert configs[0]["provider"] == "openai"
        assert configs[1]["provider"] == "claude"
        assert configs[2]["provider"] == "gemini"
    
    def test_create_configs_with_overrides(self):
        """Test creating configurations with provider overrides."""
        model_names = ["gpt-4o", "custom-model"]
        overrides = {"custom-model": "local"}
        
        configs = create_model_configs(model_names, overrides)
        
        assert configs[0]["provider"] == "openai"
        assert configs[1]["provider"] == "local"
    
    def test_create_configs_empty_list_raises(self):
        """Test that empty model list raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            create_model_configs([])
    
    def test_create_configs_preserves_order(self):
        """Test that model order is preserved in configs."""
        model_names = ["gemini-pro", "gpt-4o", "claude-3-opus"]
        configs = create_model_configs(model_names)
        
        assert configs[0]["model"] == "gemini-pro"
        assert configs[1]["model"] == "gpt-4o"
        assert configs[2]["model"] == "claude-3-opus"


class TestValidateModelConfig:
    """Test suite for model configuration validation."""
    
    def test_validate_valid_config(self):
        """Test that valid configurations pass validation."""
        config = {"provider": "openai", "model": "gpt-4o"}
        assert validate_model_config(config) is True
    
    def test_validate_missing_provider(self):
        """Test that config missing provider fails validation."""
        config = {"model": "gpt-4o"}
        assert validate_model_config(config) is False
    
    def test_validate_missing_model(self):
        """Test that config missing model fails validation."""
        config = {"provider": "openai"}
        assert validate_model_config(config) is False
    
    def test_validate_empty_values(self):
        """Test that empty string values fail validation."""
        config = {"provider": "", "model": "gpt-4o"}
        assert validate_model_config(config) is False
        
        config = {"provider": "openai", "model": ""}
        assert validate_model_config(config) is False
    
    def test_validate_whitespace_values(self):
        """Test that whitespace-only values fail validation."""
        config = {"provider": "   ", "model": "gpt-4o"}
        assert validate_model_config(config) is False
    
    def test_validate_non_dict(self):
        """Test that non-dict input fails validation."""
        assert validate_model_config(None) is False  # type: ignore
        assert validate_model_config("not a dict") is False  # type: ignore
        assert validate_model_config([]) is False  # type: ignore
    
    def test_validate_extra_keys_allowed(self):
        """Test that extra keys don't cause validation to fail."""
        config = {
            "provider": "openai",
            "model": "gpt-4o",
            "temperature": 0.7,  # Extra key
        }
        assert validate_model_config(config) is True


class TestProviderPrefixConfiguration:
    """Test suite for provider prefix configuration."""
    
    def test_all_providers_have_prefixes(self):
        """Test that all providers in configuration have at least one prefix."""
        for provider, prefixes in PROVIDER_MODEL_PREFIXES.items():
            assert len(prefixes) > 0, f"Provider '{provider}' has no prefixes"
            assert all(isinstance(p, str) for p in prefixes), \
                f"Provider '{provider}' has non-string prefixes"
    
    def test_no_overlapping_prefixes(self):
        """Test that provider prefixes don't overlap (best practice)."""
        all_prefixes = []
        for prefixes in PROVIDER_MODEL_PREFIXES.values():
            all_prefixes.extend(prefixes)
        
        # Check for exact duplicates
        assert len(all_prefixes) == len(set(all_prefixes)), \
            "Found duplicate prefixes across providers"
    
    def test_default_provider_is_valid(self):
        """Test that the default provider exists in the configuration."""
        assert DEFAULT_PROVIDER in PROVIDER_MODEL_PREFIXES, \
            f"Default provider '{DEFAULT_PROVIDER}' not in configured providers"


class TestIntegration:
    """Integration tests for realistic usage scenarios."""
    
    def test_ensemble_api_workflow(self):
        """Test complete workflow for creating ensemble API call."""
        # Scenario: User wants to call 3 different models
        model_names = ["gpt-4o", "claude-3-opus", "gemini-pro"]
        
        # Create configurations
        configs = create_model_configs(model_names)
        
        # Validate all configurations
        assert all(validate_model_config(config) for config in configs)
        
        # Verify structure matches expected API format
        for config in configs:
            assert "provider" in config
            assert "model" in config
            assert isinstance(config["provider"], str)
            assert isinstance(config["model"], str)
    
    def test_backward_compatibility_with_manual_config(self):
        """Test that manually created configs work with validation."""
        # Old-style manual configuration
        manual_config = {"provider": "openai", "model": "gpt-4o"}
        
        # Should pass validation
        assert validate_model_config(manual_config)
    
    def test_error_recovery_for_unknown_model(self):
        """Test that system gracefully handles unknown models."""
        # Unknown model should still create valid config with default provider
        config = create_model_config("proprietary-model-xyz")
        
        assert validate_model_config(config)
        assert config["provider"] == DEFAULT_PROVIDER
        assert config["model"] == "proprietary-model-xyz"
