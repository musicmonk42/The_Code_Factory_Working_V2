import json
import os
import tempfile
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

# Import the module components
from self_fixing_engineer.arbiter.knowledge_graph.config import (
    MetaLearningConfig,
    MultiModalData,
    SensitiveValue,
    load_persona_dict,
)
from pydantic import ValidationError


# Module-level fixtures that can be used by all test classes
@pytest.fixture
def temp_env_file():
    """Create a temporary .env file for testing"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("ML_DEFAULT_PROVIDER=openai\n")
        f.write("ML_DEFAULT_LLM_MODEL=gpt-4\n")
        f.write("ML_DEFAULT_TEMP=0.8\n")
        f.write("ML_REDIS_URL=redis://localhost:6380/0\n")
        f.flush()
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class TestSensitiveValue:
    """Test suite for SensitiveValue class"""

    def test_sensitive_value_creation(self):
        """Test creating a SensitiveValue instance"""
        sensitive = SensitiveValue(root="secret_api_key")
        assert sensitive.get_actual_value() == "secret_api_key"

    def test_sensitive_value_string_representation(self):
        """Test that string representation is redacted"""
        sensitive = SensitiveValue(root="secret_api_key")
        assert str(sensitive) == "[SENSITIVE]"
        assert repr(sensitive) == "SensitiveValue('[SENSITIVE]')"

    def test_sensitive_value_json_serialization(self):
        """Test that JSON serialization is redacted"""
        sensitive = SensitiveValue(root="secret_api_key")
        assert sensitive.model_dump_json() == '"[SENSITIVE]"'

    def test_sensitive_value_equality(self):
        """Test equality comparison between SensitiveValue instances"""
        sensitive1 = SensitiveValue(root="secret_api_key")
        sensitive2 = SensitiveValue(root="secret_api_key")
        sensitive3 = SensitiveValue(root="different_key")

        assert sensitive1 == sensitive2
        assert sensitive1 != sensitive3
        assert sensitive1 != "secret_api_key"  # Should not equal plain string

    def test_sensitive_value_hash(self):
        """Test that SensitiveValue instances can be hashed"""
        sensitive1 = SensitiveValue(root="secret_api_key")
        sensitive2 = SensitiveValue(root="secret_api_key")

        assert hash(sensitive1) == hash(sensitive2)

        # Should work in sets/dicts
        sensitive_set = {sensitive1, sensitive2}
        assert len(sensitive_set) == 1


class TestMetaLearningConfig:
    """Test suite for MetaLearningConfig class"""

    def test_default_config_creation(self, temp_data_dir):
        """Test creating config with default values"""
        with patch.dict(
            os.environ,
            {
                "ML_DATA_LAKE_PATH": f"{temp_data_dir}/test_data.jsonl",
                "ML_LOCAL_AUDIT_LOG_PATH": f"{temp_data_dir}/test_audit.jsonl",
            },
        ):
            config = MetaLearningConfig()
            assert config.DEFAULT_PROVIDER == "openai"
            assert config.DEFAULT_LLM_MODEL == "gpt-3.5-turbo"
            assert config.DEFAULT_TEMP == 0.7
            assert config.MEMORY_WINDOW == 5
            assert config.GDPR_MODE is True

    def test_config_from_env_variables(self, temp_data_dir):
        """Test loading config from environment variables"""
        with patch.dict(
            os.environ,
            {
                "ML_DEFAULT_PROVIDER": "anthropic",
                "ML_DEFAULT_LLM_MODEL": "claude-3",
                "ML_DEFAULT_TEMP": "0.9",
                "ML_MEMORY_WINDOW": "10",
                "ML_GDPR_MODE": "false",
                "ML_DATA_LAKE_PATH": f"{temp_data_dir}/test_data.jsonl",
                "ML_LOCAL_AUDIT_LOG_PATH": f"{temp_data_dir}/test_audit.jsonl",
            },
        ):
            config = MetaLearningConfig()
            assert config.DEFAULT_PROVIDER == "anthropic"
            assert config.DEFAULT_LLM_MODEL == "claude-3"
            assert config.DEFAULT_TEMP == 0.9
            assert config.MEMORY_WINDOW == 10
            assert config.GDPR_MODE is False

    def test_config_file_path_validation(self, temp_data_dir):
        """Test that file paths are validated and directories are created"""
        test_path = Path(temp_data_dir) / "nested" / "dir" / "test.jsonl"
        with patch.dict(
            os.environ,
            {
                "ML_DATA_LAKE_PATH": str(test_path),
                "ML_LOCAL_AUDIT_LOG_PATH": f"{temp_data_dir}/audit.jsonl",
            },
        ):
            MetaLearningConfig()
            assert test_path.parent.exists()
            assert test_path.exists()

    def test_sensitive_value_handling(self, temp_data_dir):
        """Test that sensitive values are properly wrapped"""
        with patch.dict(
            os.environ,
            {
                "ML_AUDIT_ENCRYPTION_KEY": "secret_key_123",
                "ML_AUDIT_SIGNING_PRIVATE_KEY": "private_key_456",
                "ML_DATA_LAKE_PATH": f"{temp_data_dir}/test_data.jsonl",
                "ML_LOCAL_AUDIT_LOG_PATH": f"{temp_data_dir}/test_audit.jsonl",
            },
        ):
            config = MetaLearningConfig()
            assert isinstance(config.AUDIT_ENCRYPTION_KEY, SensitiveValue)
            assert config.AUDIT_ENCRYPTION_KEY.get_actual_value() == "secret_key_123"
            assert str(config.AUDIT_ENCRYPTION_KEY) == "[SENSITIVE]"

    def test_kafka_validation(self, temp_data_dir):
        """Test Kafka configuration validation"""
        with patch.dict(
            os.environ,
            {
                "ML_USE_KAFKA_INGESTION": "true",
                "ML_KAFKA_BOOTSTRAP_SERVERS": "",
                "ML_DATA_LAKE_PATH": f"{temp_data_dir}/test_data.jsonl",
                "ML_LOCAL_AUDIT_LOG_PATH": f"{temp_data_dir}/test_audit.jsonl",
            },
        ):
            with pytest.raises(ValidationError) as exc_info:
                MetaLearningConfig()
            assert "KAFKA_BOOTSTRAP_SERVERS must be set" in str(exc_info.value)

    def test_redis_url_validation(self, temp_data_dir):
        """Test Redis URL validation"""
        with patch.dict(
            os.environ,
            {
                "ML_REDIS_URL": "invalid://localhost:6379",
                "ML_DATA_LAKE_PATH": f"{temp_data_dir}/test_data.jsonl",
                "ML_LOCAL_AUDIT_LOG_PATH": f"{temp_data_dir}/test_audit.jsonl",
            },
        ):
            with pytest.raises(ValidationError) as exc_info:
                MetaLearningConfig()
            assert "REDIS_URL must be a valid" in str(exc_info.value)

    def test_http_endpoint_validation(self, temp_data_dir):
        """Test HTTP endpoint validation"""
        with patch.dict(
            os.environ,
            {
                "ML_ML_PLATFORM_ENDPOINT": "ftp://invalid.com",
                "ML_DATA_LAKE_PATH": f"{temp_data_dir}/test_data.jsonl",
                "ML_LOCAL_AUDIT_LOG_PATH": f"{temp_data_dir}/test_audit.jsonl",
            },
        ):
            with pytest.raises(ValidationError) as exc_info:
                MetaLearningConfig()
            assert "must use http or https scheme" in str(exc_info.value)

    def test_config_reload_from_file(self, temp_data_dir):
        """Test reloading configuration from a JSON file"""
        config_file = Path(temp_data_dir) / "config.json"
        config_data = {
            "DEFAULT_PROVIDER": "google",
            "DEFAULT_LLM_MODEL": "gemini-pro",
            "DEFAULT_TEMP": 0.5,
            "DATA_LAKE_PATH": f"{temp_data_dir}/data.jsonl",
            "LOCAL_AUDIT_LOG_PATH": f"{temp_data_dir}/audit.jsonl",
        }
        config_file.write_text(json.dumps(config_data))

        with patch.dict(
            os.environ,
            {
                "ML_CONFIG_SOURCE": "file",
                "ML_CONFIG_FILE_PATH": str(config_file),
                "ML_DATA_LAKE_PATH": f"{temp_data_dir}/test_data.jsonl",
                "ML_LOCAL_AUDIT_LOG_PATH": f"{temp_data_dir}/test_audit.jsonl",
            },
        ):
            config = MetaLearningConfig()

            # Simulate reload
            config.reload_config()

            assert config.DEFAULT_PROVIDER == "google"
            assert config.DEFAULT_LLM_MODEL == "gemini-pro"
            assert config.DEFAULT_TEMP == 0.5

    def test_numeric_field_constraints(self, temp_data_dir):
        """Test numeric field constraints (min/max values)"""
        with patch.dict(
            os.environ,
            {
                "ML_LLM_RATE_LIMIT_CALLS": "0",  # Should fail, min is 1
                "ML_DATA_LAKE_PATH": f"{temp_data_dir}/test_data.jsonl",
                "ML_LOCAL_AUDIT_LOG_PATH": f"{temp_data_dir}/test_audit.jsonl",
            },
        ):
            with pytest.raises(ValidationError) as exc_info:
                MetaLearningConfig()
            assert "greater than or equal to 1" in str(exc_info.value)

        with patch.dict(
            os.environ,
            {
                "ML_MODEL_BENCHMARK_THRESHOLD": "1.5",  # Should fail, max is 1.0
                "ML_DATA_LAKE_PATH": f"{temp_data_dir}/test_data.jsonl",
                "ML_LOCAL_AUDIT_LOG_PATH": f"{temp_data_dir}/test_audit.jsonl",
            },
        ):
            with pytest.raises(ValidationError) as exc_info:
                MetaLearningConfig()
            assert "less than or equal to 1" in str(exc_info.value)


class TestMultiModalData:
    """Test suite for MultiModalData class"""

    def test_multimodal_data_creation(self):
        """Test creating MultiModalData instances"""
        data = MultiModalData(
            data_type="image",
            data=b"fake_image_data",
            metadata={"width": 1920, "height": 1080},
        )
        assert data.data_type == "image"
        assert data.data == b"fake_image_data"
        assert data.metadata["width"] == 1920

    def test_multimodal_data_types(self):
        """Test all valid data types"""
        valid_types = ["image", "audio", "video", "text_file", "pdf_file"]
        for dtype in valid_types:
            data = MultiModalData(data_type=dtype, data=b"test_data")
            assert data.data_type == dtype

    def test_invalid_data_type(self):
        """Test that invalid data types raise ValidationError"""
        with pytest.raises(ValidationError):
            MultiModalData(data_type="invalid_type", data=b"test_data")

    def test_model_dump_for_log(self):
        """Test the model_dump_for_log method"""
        test_data = b"test_data_content"
        data = MultiModalData(
            data_type="text_file",
            data=test_data,
            metadata={"filename": "test.txt", "size": len(test_data)},
        )

        log_dump = data.model_dump_for_log()
        assert log_dump["data_type"] == "text_file"
        assert "data_hash" in log_dump
        assert log_dump["metadata"]["filename"] == "test.txt"
        assert log_dump["metadata"]["size"] == len(test_data)

        # Verify hash is consistent
        import hashlib

        expected_hash = hashlib.sha256(test_data).hexdigest()
        assert log_dump["data_hash"] == expected_hash

    def test_model_dump_for_log_empty_data(self):
        """Test model_dump_for_log with empty data"""
        data = MultiModalData(data_type="image", data=b"", metadata={})

        log_dump = data.model_dump_for_log()
        assert log_dump["data_hash"] is not None  # Empty data still has a hash

        # Verify it's the correct hash for empty bytes
        import hashlib

        expected_hash = hashlib.sha256(b"").hexdigest()
        assert log_dump["data_hash"] == expected_hash


class TestLoadPersonaDict:
    """Test suite for load_persona_dict function"""

    def test_load_default_persona(self):
        """Test loading default persona when file doesn't exist"""
        with patch("os.path.exists", return_value=False):
            personas = load_persona_dict()
            assert personas == {"default": "You are a helpful AI assistant."}

    def test_load_persona_from_file(self):
        """Test loading personas from a JSON file"""
        persona_data = {
            "default": "Default assistant",
            "expert": "You are an expert assistant",
            "friendly": "You are a friendly assistant",
        }

        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=json.dumps(persona_data))):
                personas = load_persona_dict()
                assert personas == persona_data

    def test_invalid_persona_file_format(self):
        """Test handling of invalid persona file format"""
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data='["not", "a", "dict"]')):
                personas = load_persona_dict()
                assert personas == {"default": "You are a helpful AI assistant."}

    def test_persona_file_read_error(self):
        """Test handling of file read errors"""
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", side_effect=IOError("File read error")):
                personas = load_persona_dict()
                assert personas == {"default": "You are a helpful AI assistant."}

    def test_invalid_json_in_persona_file(self):
        """Test handling of invalid JSON in persona file"""
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data='{"invalid json}')):
                personas = load_persona_dict()
                assert personas == {"default": "You are a helpful AI assistant."}


class TestConfigIntegration:
    """Integration tests for the config module"""

    def test_full_config_with_all_features(self, temp_data_dir):
        """Test a full configuration with all features enabled"""
        with patch.dict(
            os.environ,
            {
                # Data Lake
                "ML_DATA_LAKE_PATH": f"{temp_data_dir}/data.jsonl",
                "ML_USE_S3_DATA_LAKE": "true",
                "ML_DATA_LAKE_S3_BUCKET": "test-bucket",
                # Audit
                "ML_LOCAL_AUDIT_LOG_PATH": f"{temp_data_dir}/audit.jsonl",
                "ML_AUDIT_ENCRYPTION_KEY": "test_encryption_key",
                "ML_AUDIT_LOG_ROTATION_SIZE_MB": "50",
                # Kafka
                "ML_USE_KAFKA_INGESTION": "true",
                "ML_KAFKA_BOOTSTRAP_SERVERS": "localhost:9092,localhost:9093",
                "ML_KAFKA_TOPIC": "test_topic",
                # Redis
                "ML_REDIS_URL": "redis://localhost:6379/1",
                "ML_REDIS_LOCK_TTL_SECONDS": "120",
                # ML Settings
                "ML_MIN_RECORDS_FOR_TRAINING": "1000",
                "ML_MODEL_BENCHMARK_THRESHOLD": "0.9",
                # PII
                "ML_REDACT_PII_IN_LOGS": "true",
                # LLM
                "ML_DEFAULT_PROVIDER": "anthropic",
                "ML_FALLBACK_PROVIDER": "openai",
            },
        ):
            config = MetaLearningConfig()

            # Verify all settings
            assert config.USE_S3_DATA_LAKE is True
            assert config.DATA_LAKE_S3_BUCKET == "test-bucket"
            assert config.USE_KAFKA_INGESTION is True
            assert config.KAFKA_BOOTSTRAP_SERVERS == "localhost:9092,localhost:9093"
            assert config.REDIS_LOCK_TTL_SECONDS == 120
            assert config.MIN_RECORDS_FOR_TRAINING == 1000
            assert config.MODEL_BENCHMARK_THRESHOLD == 0.9
            assert config.REDACT_PII_IN_LOGS is True
            assert config.DEFAULT_PROVIDER == "anthropic"
            assert config.FALLBACK_PROVIDER == "openai"

            # Check sensitive value handling
            assert isinstance(config.AUDIT_ENCRYPTION_KEY, SensitiveValue)
            assert (
                config.AUDIT_ENCRYPTION_KEY.get_actual_value() == "test_encryption_key"
            )

    def test_config_persistence(self, temp_data_dir):
        """Test that config values persist correctly"""
        with patch.dict(
            os.environ,
            {
                "ML_DATA_LAKE_PATH": f"{temp_data_dir}/test_data.jsonl",
                "ML_LOCAL_AUDIT_LOG_PATH": f"{temp_data_dir}/test_audit.jsonl",
                "ML_DEFAULT_TEMP": "0.85",
                "ML_MEMORY_WINDOW": "7",
            },
        ):
            config1 = MetaLearningConfig()
            config2 = MetaLearningConfig()

            # Both instances should have the same values
            assert config1.DEFAULT_TEMP == config2.DEFAULT_TEMP == 0.85
            assert config1.MEMORY_WINDOW == config2.MEMORY_WINDOW == 7


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
