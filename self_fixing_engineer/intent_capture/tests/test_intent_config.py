import json
import logging
from unittest.mock import patch, MagicMock, mock_open
import pytest
from pydantic import ValidationError

# Import the module under test
import intent_capture.config as config_module  # Import the module itself to patch its internals

# Import what's actually available from the config module
from intent_capture.config import (
    PiiMaskingFormatter,  # This exists
    setup_logging,
    fetch_from_vault,
    _fetch_config_from_service,
    ConfigEncryptor,
    Config,
    PluginManager,
    ConfigChangeHandler,
    GlobalConfigManager,
    log_audit_event,
    prune_audit_logs,
    startup_validation,
)


# --- Test Fixtures ---
@pytest.fixture
def mock_env(monkeypatch):
    """Mock environment variables."""
    monkeypatch.setenv("TEST_SECRET", "env_value")
    monkeypatch.setenv("VAULT_URL", "http://mock-vault:8200")
    monkeypatch.setenv("VAULT_TOKEN", "mock_token")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("CONFIG_SERVICE_URL", "http://mock-service/config")
    monkeypatch.setenv("PROD_MODE", "true")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    # Ensure required prod mode secrets are set for tests
    monkeypatch.setenv(
        "INTENT_AGENT_ENCRYPTION_KEY", "A" * 32 + "=" * 12
    )  # Valid Fernet key format
    monkeypatch.setenv("INTENT_AGENT_LLM_API_KEY", "mock_api_key")
    monkeypatch.setenv("INTENT_AGENT_REDIS_URL", "redis://localhost:6379/0")
    yield


@pytest.fixture
def mock_hvac():
    """Mock hvac (Vault) client."""
    mock_client = MagicMock()
    mock_client.is_authenticated.return_value = True
    mock_client.secrets.kv.read_secret_version.return_value = {
        "data": {"data": {"TEST_SECRET": "vault_value"}}
    }
    with patch("intent_capture.config.hvac.Client", return_value=mock_client):
        yield mock_client


@pytest.fixture
def mock_boto3():
    """Mock boto3 (AWS) client."""
    mock_s3 = MagicMock()
    mock_s3.put_object = MagicMock()
    mock_s3.list_objects_v2.return_value = {"Contents": []}
    mock_s3.delete_objects = MagicMock()

    mock_boto3_module = MagicMock()
    mock_boto3_module.client.return_value = mock_s3

    with patch("intent_capture.config.boto3", mock_boto3_module):
        yield mock_s3


@pytest.fixture
def mock_requests():
    """Mock requests for config service."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"INTENT_AGENT_REDIS_URL": "redis://test:6379/0"}
    mock_resp.raise_for_status = MagicMock()
    with patch("intent_capture.config.requests.get", return_value=mock_resp):
        yield mock_resp


@pytest.fixture
def mock_redis():
    """Mock redis client."""
    mock_redis_client = MagicMock()
    mock_redis_client.get.return_value = None
    mock_redis_client.set = MagicMock()

    mock_redis_module = MagicMock()
    mock_redis_module.from_url.return_value = mock_redis_client

    with patch("intent_capture.config.redis", mock_redis_module):
        with patch("intent_capture.config.REDIS_AVAILABLE", True):
            yield mock_redis_client


@pytest.fixture
def temp_plugin_dir(tmp_path):
    """Create temporary plugin directory."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    plugin_config = plugin_dir / "test_plugin" / "plugin_config.json"
    plugin_config.parent.mkdir()
    plugin_config.write_text(json.dumps({"name": "test_plugin", "enabled": True}))

    yield plugin_dir


@pytest.fixture
def mock_logger():
    """Mock logger to capture logs."""
    with patch("intent_capture.config.config_logger") as mock_log:
        yield mock_log


@pytest.fixture
def temp_config_file(tmp_path):
    """Create temporary config file."""
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "INTENT_AGENT_REDIS_URL": "redis://localhost:6379/0",
                "INTENT_AGENT_LOG_LEVEL": "INFO",
            }
        )
    )
    yield config_file


# --- Tests for Logging Setup ---
def test_pii_masking_formatter():
    """Test PiiMaskingFormatter masks PII."""
    formatter = PiiMaskingFormatter()
    record = logging.LogRecord(
        "name",
        logging.INFO,
        "path",
        10,
        "Email: test@example.com, IP: 192.168.1.1",
        (),
        None,
    )
    formatted = formatter.format(record)
    assert "[REDACTED_EMAIL]" in formatted
    assert "[REDACTED_IP]" in formatted
    assert "test@example.com" not in formatted
    assert "192.168.1.1" not in formatted


def test_setup_logging(tmp_path, monkeypatch):
    """Test logging setup."""
    log_file = tmp_path / "test.log"
    monkeypatch.setenv("LOG_FILE_PATH", str(log_file))
    setup_logging()
    assert config_module.config_logger.handlers  # Should have handlers


# --- Tests for Vault Integration ---
def test_fetch_from_vault_success(mock_hvac, monkeypatch):
    """Test successful fetch from Vault."""
    monkeypatch.setenv("USE_VAULT", "true")
    with patch("intent_capture.config.CRYPTOGRAPHY_AVAILABLE", True):
        result = fetch_from_vault("test_path")
        assert result == {"TEST_SECRET": "vault_value"}


def test_fetch_from_vault_disabled(monkeypatch):
    """Test Vault fetch when disabled."""
    monkeypatch.setenv("USE_VAULT", "false")
    result = fetch_from_vault("test_path")
    assert result == {}


def test_fetch_from_vault_not_authenticated(mock_hvac, monkeypatch):
    """Test Vault fetch when not authenticated."""
    monkeypatch.setenv("USE_VAULT", "true")
    mock_hvac.is_authenticated.return_value = False
    with patch("intent_capture.config.CRYPTOGRAPHY_AVAILABLE", True):
        result = fetch_from_vault("test_path")
        assert result == {}


# --- Tests for Config Service ---
def test_fetch_config_from_service_success(mock_requests, monkeypatch):
    """Test successful config fetch from service."""
    monkeypatch.setenv("CONFIG_SERVICE_URL", "http://mock-service/config")
    config_data = _fetch_config_from_service()
    assert config_data == {"INTENT_AGENT_REDIS_URL": "redis://test:6379/0"}


def test_fetch_config_from_service_no_url(monkeypatch):
    """Test config fetch when no service URL."""
    monkeypatch.delenv("CONFIG_SERVICE_URL", raising=False)
    result = _fetch_config_from_service()
    assert result is None


def test_fetch_config_from_service_error(mock_requests):
    """Test config fetch on error."""
    mock_requests.status_code = 500
    result = _fetch_config_from_service()
    assert result is None


# --- Tests for ConfigEncryptor ---
def test_config_encryptor_encrypt_decrypt(tmp_path, monkeypatch):
    """Test ConfigEncryptor encrypt and decrypt."""
    # Generate a valid Fernet key
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()

    encryptor = ConfigEncryptor(key)
    test_data = {"test": "data"}
    file_path = tmp_path / "encrypted.json"

    encryptor.encrypt_config(str(file_path), test_data)
    assert file_path.exists()

    decrypted = encryptor.decrypt_config(str(file_path))
    assert decrypted == test_data


def test_config_encryptor_no_key():
    """Test ConfigEncryptor without key raises error."""
    with pytest.raises(ValueError, match="CONFIG_ENCRYPTION_KEY"):
        ConfigEncryptor(None)


# --- Tests for Config Class ---
def test_config_validation_success(mock_env):
    """Test Config validation with valid data."""
    config = Config()
    assert config.ENCRYPTION_KEY.get_secret_value() == "A" * 32 + "=" * 12
    assert config.LLM_API_KEY.get_secret_value() == "mock_api_key"
    assert config.REDIS_URL == "redis://localhost:6379/0"


def test_config_validation_invalid_redis_url(mock_env):
    """Test Config validation with invalid Redis URL."""
    with pytest.raises(ValidationError):
        Config(REDIS_URL="invalid://url")


def test_config_validation_invalid_log_level(mock_env):
    """Test Config validation with invalid log level."""
    with pytest.raises(ValidationError):
        Config(LOG_LEVEL="INVALID")


# --- Tests for PluginManager ---
def test_plugin_manager_discover_and_apply_plugins(temp_plugin_dir, monkeypatch):
    """Test PluginManager discovers plugins."""
    monkeypatch.setattr("os.path.isdir", lambda x: True if "plugins" in x else False)
    monkeypatch.setattr("os.listdir", lambda x: ["test_plugin"])
    monkeypatch.setattr(
        "os.path.exists", lambda x: True if "plugin_config.json" in x else False
    )

    with patch("builtins.open", mock_open(read_data='{"name": "test_plugin"}')):
        PluginManager.discover_and_apply_plugins(None)
        assert "test_plugin" in PluginManager._plugins


def test_plugin_manager_verify_signature_disabled(monkeypatch):
    """Test plugin signature verification when disabled."""
    monkeypatch.setenv("VERIFY_PLUGINS", "false")
    result = PluginManager._verify_plugin_signature("test", "path")
    assert result is True


# --- Tests for GlobalConfigManager ---
def test_global_config_manager_get_config(mock_env, mock_requests):
    """Test GlobalConfigManager get_config."""
    # Reset the singleton
    GlobalConfigManager._instance = None

    config = GlobalConfigManager.get_config()
    assert config is not None
    assert isinstance(config, Config)


def test_global_config_manager_singleton(mock_env, mock_requests):
    """Test GlobalConfigManager returns singleton."""
    # Reset the singleton
    GlobalConfigManager._instance = None

    config1 = GlobalConfigManager.get_config()
    config2 = GlobalConfigManager.get_config()
    assert config1 is config2


def test_global_config_manager_reload(mock_env, mock_requests, mock_logger):
    """Test GlobalConfigManager reload."""
    # Reset the singleton
    GlobalConfigManager._instance = None
    GlobalConfigManager.get_config()

    # Set last reload time in the past
    GlobalConfigManager._last_reload_time = 0

    GlobalConfigManager.reload_config()
    mock_logger.error.assert_not_called()  # Should not have errors


# --- Tests for Audit Logging ---
def test_log_audit_event_enabled(mock_boto3, monkeypatch):
    """Test audit logging when enabled."""
    monkeypatch.setenv("ENABLE_AUDIT", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test_key")
    monkeypatch.setenv("AWS_SECRET_KEY", "test_secret")

    with patch("os.getlogin", return_value="testuser"):
        log_audit_event("test_event", {"data": "test"})
        mock_boto3.put_object.assert_called_once()


def test_log_audit_event_disabled(mock_boto3, monkeypatch):
    """Test audit logging when disabled."""
    monkeypatch.setenv("ENABLE_AUDIT", "false")
    log_audit_event("test_event", {"data": "test"})
    mock_boto3.put_object.assert_not_called()


def test_prune_audit_logs(mock_boto3, monkeypatch):
    """Test pruning audit logs."""
    monkeypatch.setenv("CONSENT_PRUNE", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test_key")
    monkeypatch.setenv("AWS_SECRET_KEY", "test_secret")

    prune_audit_logs(retention_days=30)
    mock_boto3.list_objects_v2.assert_called_once()


# --- Tests for Startup Validation ---
def test_startup_validation_success(mock_env):
    """Test successful startup validation."""
    # Reset and create config
    GlobalConfigManager._instance = None
    startup_validation()  # Should not raise


def test_startup_validation_missing_fields(monkeypatch):
    """Test startup validation with missing fields."""
    monkeypatch.delenv("INTENT_AGENT_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("INTENT_AGENT_LLM_API_KEY", raising=False)

    # Reset the singleton
    GlobalConfigManager._instance = None

    with pytest.raises(
        Exception
    ):  # Will raise either ValidationError or pydantic error
        startup_validation()


# --- Tests for ConfigChangeHandler ---
def test_config_change_handler(mock_env, temp_config_file):
    """Test ConfigChangeHandler triggers reload."""
    handler = ConfigChangeHandler()
    event = MagicMock()
    event.src_path = str(temp_config_file)

    with patch.object(GlobalConfigManager, "reload_config") as mock_reload:
        handler.on_modified(event)
        mock_reload.assert_called_once()
