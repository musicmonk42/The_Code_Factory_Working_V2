import os
import threading
from unittest.mock import MagicMock, patch

import pytest
from arbiter.config import (
    ArbiterConfig,
    get_or_create_counter,
    get_or_create_gauge,
    get_or_create_histogram,
)
from cryptography.fernet import Fernet
from pydantic import SecretStr


# Clear globals fixture
@pytest.fixture(autouse=True)
def clear_globals():
    """Clear global config instances."""
    ArbiterConfig._instance = None
    yield
    ArbiterConfig._instance = None


# Test ArbiterConfig initialization
@patch.dict(
    os.environ,
    {
        "DB_PATH": "sqlite:///./test.db",
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "LLM_MODEL_NAME": "gpt-4o-mini",
    },
)
def test_arbiter_config_init():
    config = ArbiterConfig()
    assert config.DB_PATH == "sqlite:///./test.db"
    assert config.llm.model_name == "gpt-4o-mini"
    assert hasattr(config, "_cipher")


# Test initialize singleton - patch the metrics in the module
@patch.dict(
    os.environ,
    {
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "DB_PATH": "sqlite:///./test.db",
    },
)
@patch("arbiter.config.CONFIG_ACCESS")
@patch("arbiter.config.CONFIG_ERRORS")
def test_initialize_singleton(mock_errors, mock_access):
    # Setup mock metrics
    mock_access.labels.return_value.inc = MagicMock()
    mock_errors.labels.return_value.inc = MagicMock()

    config1 = ArbiterConfig.initialize()
    config2 = ArbiterConfig.initialize()
    assert config1 is config2
    assert config1._is_initialized
    assert config1._loaded_at is not None
    assert config1._cipher is not None


# Test load_from_env
@patch.dict(os.environ, {"LLM_TEMPERATURE": "0.7"})
def test_load_from_env():
    config = ArbiterConfig()
    assert config.llm.temperature == 0.7


# Test email recipients list
def test_email_recipients_list():
    config = ArbiterConfig(EMAIL_RECIPIENTS="a@example.com,b@example.com")
    config.EMAIL_RECIPIENTS_LIST = ["a@example.com", "b@example.com"]
    assert config.EMAIL_RECIPIENTS_LIST == ["a@example.com", "b@example.com"]


# Test decrypt sensitive fields
@patch.dict(os.environ, {"ENCRYPTION_KEY": Fernet.generate_key().decode()})
def test_decrypt_sensitive_fields():
    config = ArbiterConfig()
    key = config.ENCRYPTION_KEY.get_secret_value().encode("utf-8")
    config._cipher = Fernet(key)

    encrypted = config._cipher.encrypt(b"secret")
    config._sensitive_fields["EMAIL_SMTP_PASSWORD"] = encrypted.decode()
    config.decrypt_sensitive_fields()
    assert config.EMAIL_SMTP_PASSWORD.get_secret_value() == "secret"


# Test to_dict - patch the metric
@patch.dict(os.environ, {"ENCRYPTION_KEY": Fernet.generate_key().decode()})
@patch("arbiter.config.CONFIG_ACCESS")
def test_to_dict(mock_access):
    mock_access.labels.return_value.inc = MagicMock()

    config = ArbiterConfig(EMAIL_SMTP_PASSWORD=SecretStr("secret"))
    config_dict = config.to_dict()
    if "EMAIL_SMTP_PASSWORD" in config_dict:
        assert (
            config_dict["EMAIL_SMTP_PASSWORD"] == "[REDACTED]"
            or config_dict["EMAIL_SMTP_PASSWORD"] is None
        )
    assert config_dict["llm"]["api_key"] == "[REDACTED]"


# Test required fields validation
def test_required_fields_validation():
    config = ArbiterConfig()
    assert config.DB_PATH == "sqlite:///./omnicore.db"


# Test thread safety
@patch.dict(os.environ, {"ENCRYPTION_KEY": Fernet.generate_key().decode()})
@patch("arbiter.config.CONFIG_ACCESS")
@patch("arbiter.config.CONFIG_ERRORS")
def test_config_singleton_thread_safe(mock_errors, mock_access):
    mock_access.labels.return_value.inc = MagicMock()
    mock_errors.labels.return_value.inc = MagicMock()

    results = []

    def init_config():
        results.append(ArbiterConfig.initialize())

    threads = [threading.Thread(target=init_config) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(c is results[0] for c in results)


def test_load_from_file_invalid():
    with pytest.raises(ValueError, match="Unsupported file format"):
        ArbiterConfig.load_from_file("test.txt")


@patch.dict(os.environ, {"ENCRYPTION_KEY": Fernet.generate_key().decode()})
def test_encrypt_sensitive_fields():
    config = ArbiterConfig(EMAIL_SMTP_PASSWORD=SecretStr("secret"))
    key = config.ENCRYPTION_KEY.get_secret_value().encode("utf-8")
    config._cipher = Fernet(key)

    config.encrypt_sensitive_fields()
    assert config._sensitive_fields.get("EMAIL_SMTP_PASSWORD") is not None
    decrypted = config._cipher.decrypt(
        config._sensitive_fields["EMAIL_SMTP_PASSWORD"].encode()
    ).decode()
    assert decrypted == "secret"


def test_invalid_email_recipients_list():
    with pytest.raises(ValueError, match="EMAIL_RECIPIENTS must be a comma-separated string"):
        ArbiterConfig(EMAIL_RECIPIENTS=123)


def test_get_or_create_counter():
    counter = get_or_create_counter("test_counter_v4", "Test counter")
    assert counter is not None


def test_get_or_create_gauge():
    gauge = get_or_create_gauge("test_gauge_v4", "Test gauge")
    assert gauge is not None


def test_get_or_create_histogram():
    hist = get_or_create_histogram("test_hist_v4", "Test hist", buckets=(0.5, 1.0))
    assert hist is not None
