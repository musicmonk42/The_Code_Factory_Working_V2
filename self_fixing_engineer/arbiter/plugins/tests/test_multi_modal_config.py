# test_multi_modal_config.py
import pytest
import os
import tempfile
import yaml
from unittest.mock import patch
from pydantic import ValidationError

from arbiter.plugins.multi_modal_config import (
    CircuitBreakerConfig,
    ProcessorConfig,
    SecurityConfig,
    AuditLogConfig,
    MetricsConfig,
    CacheConfig,
    ComplianceConfig,
    MultiModalConfig,
)


class TestCircuitBreakerConfig:
    """Test suite for CircuitBreakerConfig."""

    def test_default_values(self):
        """Test circuit breaker config with default values."""
        config = CircuitBreakerConfig()
        assert config.enabled is True
        assert config.threshold == 5
        assert config.timeout_seconds == 300

    def test_custom_values(self):
        """Test circuit breaker config with custom values."""
        config = CircuitBreakerConfig(enabled=False, threshold=10, timeout_seconds=600)
        assert config.enabled is False
        assert config.threshold == 10
        assert config.timeout_seconds == 600


class TestProcessorConfig:
    """Test suite for ProcessorConfig."""

    def test_default_values(self):
        """Test processor config with default values."""
        config = ProcessorConfig()
        assert config.enabled is False
        assert config.default_provider == "default"
        assert config.provider_config == {}

    def test_custom_values(self):
        """Test processor config with custom values."""
        provider_config = {"api_key": "test", "model": "test-model"}
        config = ProcessorConfig(
            enabled=True, default_provider="openai", provider_config=provider_config
        )
        assert config.enabled is True
        assert config.default_provider == "openai"
        assert config.provider_config == provider_config


class TestSecurityConfig:
    """Test suite for SecurityConfig."""

    def test_default_values(self):
        """Test security config with default values."""
        config = SecurityConfig()
        assert config.sandbox_enabled is False
        assert config.mask_pii_in_logs is True
        assert config.compliance_frameworks == ["NIST", "ISO27001"]
        assert config.input_validation_rules == {}
        assert config.output_validation_rules == {}
        assert config.pii_patterns == {}

    def test_input_validation_rules_valid(self):
        """Test valid input validation rules."""
        rules = {"max_size": 1024, "max_length": 5000, "min_confidence": 0.8}
        config = SecurityConfig(input_validation_rules=rules)
        assert config.input_validation_rules == rules

    def test_input_validation_rules_invalid_max_size(self):
        """Test invalid max_size in input validation rules."""
        rules = {"max_size": -1}
        with pytest.raises(
            ValidationError, match="max_size must be a positive integer"
        ):
            SecurityConfig(input_validation_rules=rules)

    def test_input_validation_rules_invalid_max_length(self):
        """Test invalid max_length in input validation rules."""
        rules = {"max_length": 0}
        with pytest.raises(
            ValidationError, match="max_length must be a positive integer"
        ):
            SecurityConfig(input_validation_rules=rules)

    def test_output_validation_rules_invalid_confidence(self):
        """Test invalid min_confidence in output validation rules."""
        rules = {"min_confidence": 1.5}
        with pytest.raises(
            ValidationError, match="min_confidence must be a number between 0 and 1"
        ):
            SecurityConfig(output_validation_rules=rules)

    def test_valid_pii_patterns(self):
        """Test valid PII regex patterns."""
        patterns = {
            "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            "phone": r"\d{3}-\d{3}-\d{4}",
        }
        config = SecurityConfig(pii_patterns=patterns)
        assert config.pii_patterns == patterns

    def test_invalid_pii_patterns(self):
        """Test invalid regex in PII patterns."""
        patterns = {"bad_regex": r"[unclosed"}
        with pytest.raises(ValidationError, match="Invalid regex pattern"):
            SecurityConfig(pii_patterns=patterns)


class TestAuditLogConfig:
    """Test suite for AuditLogConfig."""

    def test_default_values(self):
        """Test audit log config with default values."""
        config = AuditLogConfig()
        assert config.enabled is True
        assert config.log_level == "INFO"
        assert config.destination == "console"

    def test_valid_log_levels(self):
        """Test valid log levels."""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            config = AuditLogConfig(log_level=level)
            assert config.log_level == level.upper()

    def test_invalid_log_level(self):
        """Test invalid log level."""
        with pytest.raises(ValidationError, match="Invalid log level"):
            AuditLogConfig(log_level="INVALID")

    def test_valid_destinations(self):
        """Test valid destinations."""
        for dest in ["console", "file", "kafka"]:
            config = AuditLogConfig(destination=dest)
            assert config.destination == dest

    def test_invalid_destination(self):
        """Test invalid destination."""
        with pytest.raises(ValidationError, match="Invalid destination"):
            AuditLogConfig(destination="invalid")

    def test_case_insensitive_log_level(self):
        """Test that log level is case insensitive."""
        config = AuditLogConfig(log_level="info")
        assert config.log_level == "INFO"


class TestMetricsConfig:
    """Test suite for MetricsConfig."""

    def test_default_values(self):
        """Test metrics config with default values."""
        config = MetricsConfig()
        assert config.enabled is True
        assert config.exporter_port == 9090

    def test_custom_port(self):
        """Test metrics config with custom port."""
        config = MetricsConfig(exporter_port=8080)
        assert config.exporter_port == 8080


class TestCacheConfig:
    """Test suite for CacheConfig."""

    def test_default_values(self):
        """Test cache config with default values."""
        config = CacheConfig()
        assert config.enabled is True
        assert config.type == "redis"
        assert config.host == "localhost"
        assert config.port == 6379
        assert config.ttl_seconds == 3600

    def test_custom_values(self):
        """Test cache config with custom values."""
        config = CacheConfig(
            enabled=False,
            type="redis",
            host="redis.example.com",
            port=6380,
            ttl_seconds=7200,
        )
        assert config.enabled is False
        assert config.host == "redis.example.com"
        assert config.port == 6380
        assert config.ttl_seconds == 7200


class TestComplianceConfig:
    """Test suite for ComplianceConfig."""

    def test_default_values(self):
        """Test compliance config with default values."""
        config = ComplianceConfig()
        assert config.mapping == {}

    def test_valid_compliance_mapping(self):
        """Test valid compliance control IDs."""
        mapping = {
            "image": ["NIST-AC-2", "ISO27001-A.9.1"],
            "text": ["NIST-AU-12", "ISO27001-A.12.4"],
        }
        config = ComplianceConfig(mapping=mapping)
        assert config.mapping == mapping

    def test_invalid_compliance_control_id(self):
        """Test invalid compliance control ID format."""
        mapping = {"image": ["INVALID_FORMAT"]}
        with pytest.raises(ValidationError, match="Invalid compliance control ID"):
            ComplianceConfig(mapping=mapping)


class TestMultiModalConfig:
    """Test suite for MultiModalConfig."""

    def test_default_values(self):
        """Test MultiModalConfig with all default values."""
        config = MultiModalConfig()

        # Check processor configs
        assert config.image_processing.enabled is False
        assert config.audio_processing.enabled is False
        assert config.video_processing.enabled is False
        assert config.text_processing.enabled is False

        # Check other configs
        assert config.security_config.mask_pii_in_logs is True
        assert config.audit_log_config.enabled is True
        assert config.metrics_config.enabled is True
        assert config.cache_config.enabled is True
        assert config.circuit_breaker_config.enabled is True
        assert config.user_id_for_auditing == "system_user"
        assert config.current_model_version == {}

    def test_custom_values(self):
        """Test MultiModalConfig with custom values."""
        config = MultiModalConfig(
            image_processing=ProcessorConfig(enabled=True),
            user_id_for_auditing="custom_user",
            current_model_version={"image": "v1.2", "text": "v2.0"},
        )

        assert config.image_processing.enabled is True
        assert config.user_id_for_auditing == "custom_user"
        assert config.current_model_version == {"image": "v1.2", "text": "v2.0"}

    def test_nested_configuration(self):
        """Test nested configuration settings."""
        config = MultiModalConfig(
            security_config=SecurityConfig(
                sandbox_enabled=True,
                pii_patterns={"email": r"[\w\.-]+@[\w\.-]+"},
                input_validation_rules={"max_size": 1024},
            ),
            cache_config=CacheConfig(host="redis.prod.com", port=6380),
        )

        assert config.security_config.sandbox_enabled is True
        assert "email" in config.security_config.pii_patterns
        assert config.security_config.input_validation_rules["max_size"] == 1024
        assert config.cache_config.host == "redis.prod.com"
        assert config.cache_config.port == 6380

    def test_load_from_yaml_file(self):
        """Test loading configuration from a YAML file."""
        yaml_content = """
        image_processing:
          enabled: true
          default_provider: openai
        security_config:
          sandbox_enabled: true
          mask_pii_in_logs: false
        cache_config:
          host: cache.example.com
          port: 6380
        """

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            config = MultiModalConfig.load_config(temp_path)
            assert config.image_processing.enabled is True
            assert config.image_processing.default_provider == "openai"
            assert config.security_config.sandbox_enabled is True
            assert config.security_config.mask_pii_in_logs is False
            assert config.cache_config.host == "cache.example.com"
        finally:
            os.unlink(temp_path)

    def test_environment_variable_override(self):
        """Test environment variable override of configuration."""
        env_vars = {
            "MULTI_MODAL_IMAGE_PROCESSING_ENABLED": "true",
            "MULTI_MODAL_SECURITY_MASK_PII": "false",
            "MULTI_MODAL_AUDIT_LOG_CONFIG_LOG_LEVEL": "DEBUG",
            "MULTI_MODAL_CACHE_CONFIG_PORT": "6380",
            "MULTI_MODAL_CIRCUIT_BREAKER_CONFIG_THRESHOLD": "10",
        }

        with patch.dict(os.environ, env_vars):
            config = MultiModalConfig.load_config()

            assert config.image_processing.enabled is True
            assert config.security_config.mask_pii_in_logs is False
            assert config.audit_log_config.log_level == "DEBUG"
            assert config.cache_config.port == 6380
            assert config.circuit_breaker_config.threshold == 10

    def test_yaml_and_env_precedence(self):
        """Test that environment variables override YAML config."""
        yaml_content = """
        image_processing:
          enabled: false
        cache_config:
          port: 6379
        """

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        env_vars = {
            "MULTI_MODAL_IMAGE_PROCESSING_ENABLED": "true",
            "MULTI_MODAL_CACHE_CONFIG_PORT": "6380",
        }

        try:
            with patch.dict(os.environ, env_vars):
                config = MultiModalConfig.load_config(temp_path)
                # Env vars should override YAML
                assert config.image_processing.enabled is True
                assert config.cache_config.port == 6380
        finally:
            os.unlink(temp_path)

    def test_invalid_yaml_file(self):
        """Test handling of invalid YAML file."""
        yaml_content = """
        invalid: yaml: content:
        """

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            with pytest.raises(yaml.YAMLError):
                MultiModalConfig.load_config(temp_path)
        finally:
            os.unlink(temp_path)

    def test_nonexistent_config_file(self):
        """Test loading with non-existent config file."""
        # Should not raise, just use defaults
        config = MultiModalConfig.load_config("/nonexistent/path.yaml")
        assert config.image_processing.enabled is False  # Default value

    def test_invalid_env_var_conversion(self):
        """Test handling of invalid environment variable type conversion."""
        env_vars = {
            "MULTI_MODAL_CACHE_CONFIG_PORT": "not_a_number",
            "MULTI_MODAL_IMAGE_PROCESSING_ENABLED": "invalid_bool",
        }

        with patch.dict(os.environ, env_vars):
            # Should log warning but not crash
            config = MultiModalConfig.load_config()
            # Values should remain default since conversion failed
            assert config.cache_config.port == 6379
            assert config.image_processing.enabled is False

    def test_complex_nested_validation(self):
        """Test complex nested validation scenarios."""
        config_dict = {
            "security_config": {
                "input_validation_rules": {"max_size": 1024, "min_confidence": 0.5},
                "output_validation_rules": {"min_confidence": 0.9},
                "pii_patterns": {"ssn": r"\d{3}-\d{2}-\d{4}", "phone": r"\d{10}"},
            },
            "compliance_config": {
                "mapping": {
                    "image": ["NIST-AC-2.1", "ISO27001-A.9"],
                    "text": ["NIST-AU-12.3"],
                }
            },
        }

        config = MultiModalConfig(**config_dict)
        assert config.security_config.input_validation_rules["max_size"] == 1024
        assert config.compliance_config.mapping["image"] == [
            "NIST-AC-2.1",
            "ISO27001-A.9",
        ]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
