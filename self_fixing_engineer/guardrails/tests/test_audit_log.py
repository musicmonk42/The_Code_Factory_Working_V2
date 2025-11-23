# tests/test_audit_log.py
import os
import json
import hashlib
import asyncio
import base64
import sys
import logging
from unittest.mock import patch, MagicMock, AsyncMock
import pytest
import shutil

# Fix import path for audit_log module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import audit_log


# Mock dependencies that may not be available in test env
@pytest.fixture(autouse=True)
def mock_dependencies(monkeypatch):
    """Mock optional dependencies to simulate availability."""
    # Clear any state from previous tests
    audit_log.REVOKED_KEYS.clear()
    audit_log.PUBLIC_KEY_STORE.clear()
    audit_log._cached_private_key = None
    audit_log._last_hashes.clear()

    # Mock cryptography
    class MockEd25519PrivateKey:
        def __init__(self):
            self.public_key_obj = MockEd25519PublicKey()

        def public_key(self):
            return self.public_key_obj

        def sign(self, data):
            return b"test_signature"

        def private_bytes(self, encoding, format, encryption_algorithm):
            return b"mock_private_key_bytes"

        @staticmethod
        def generate():
            return MockEd25519PrivateKey()

    class MockEd25519PublicKey:
        def verify(self, signature, data):
            pass  # Success by default

        def public_bytes(self, encoding, format):
            return b"test_pub_bytes"

        @classmethod
        def from_public_bytes(cls, data):
            return cls()

    monkeypatch.setattr("audit_log.CRYPTO_AVAILABLE", True)
    monkeypatch.setattr("audit_log.Ed25519PrivateKey", MockEd25519PrivateKey)
    monkeypatch.setattr("audit_log.Ed25519PublicKey", MockEd25519PublicKey)

    # Mock serialization
    mock_serialization = MagicMock()
    mock_serialization.load_pem_private_key = MagicMock(return_value=MockEd25519PrivateKey())
    mock_serialization.Encoding.PEM = MagicMock()
    mock_serialization.Encoding.Raw = MagicMock()
    mock_serialization.PrivateFormat.PKCS8 = MagicMock()
    mock_serialization.PublicFormat.Raw = MagicMock()
    mock_serialization.BestAvailableEncryption = MagicMock(return_value=MagicMock())
    monkeypatch.setattr("audit_log.serialization", mock_serialization)

    # Mock aiofiles - disable it to force sync fallback
    monkeypatch.setattr("audit_log.aiofiles", None)

    # Mock portalocker
    class MockPortalocker:
        LOCK_EX = 1

        def lock(self, *args):
            pass

        def unlock(self, *args):
            pass

    monkeypatch.setattr("audit_log.portalocker", MockPortalocker())

    # Mock other integrations
    monkeypatch.setattr("audit_log.KAFKA_AVAILABLE", False)
    monkeypatch.setattr("audit_log.DLT_BACKEND_AVAILABLE", False)
    monkeypatch.setattr("audit_log.REQUESTS_AVAILABLE", False)
    monkeypatch.setattr("audit_log.ETHEREUM_AVAILABLE", False)
    monkeypatch.setattr("audit_log.ELASTIC_AVAILABLE", False)


@pytest.fixture
def temp_log_path(tmp_path):
    """Fixture for temporary log path."""
    return str(tmp_path / "test_audit.log")


@pytest.fixture
def mock_env(monkeypatch):
    """Fixture to mock environment variables."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("PRIVATE_KEY_B64", base64.b64encode(b"test_key").decode())
    monkeypatch.setenv("PRIVATE_KEY_PASSWORD", "test_pass")
    monkeypatch.setenv("PUBLIC_KEY_B64", base64.b64encode(b"test_pub").decode())
    monkeypatch.setenv("ALERT_WEBHOOK", "http://test.webhook")


@pytest.fixture
def caplog(caplog):
    """Fixture to capture logs."""
    caplog.set_level(logging.INFO)
    return caplog


@pytest.mark.asyncio
async def test_validate_dependencies_production(mock_env, monkeypatch, caplog):
    """Test dependency validation in production."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr("audit_log.CRYPTO_AVAILABLE", False)
    with pytest.raises(SystemExit):
        audit_log.validate_dependencies()
    assert "cryptography not installed" in caplog.text


@pytest.mark.asyncio
async def test_validate_sensitive_env_vars_production(mock_env, monkeypatch, caplog):
    """Test sensitive env var validation in production."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_KEY_B64", "dummy_key")
    with pytest.raises(SystemExit):
        audit_log.validate_sensitive_env_vars()
    assert "Dummy value detected" in caplog.text


@pytest.mark.asyncio
async def test_load_public_keys(mock_env, monkeypatch):
    """Test loading public keys."""
    public_keys = audit_log.load_public_keys()
    assert isinstance(public_keys, dict)
    assert len(public_keys) == 1  # From env var


@pytest.mark.asyncio
async def test_load_private_key(mock_env, monkeypatch):
    """Test loading private key."""
    private_key = audit_log.load_private_key()
    assert private_key is not None


@pytest.mark.asyncio
async def test_load_private_key_missing_vars(mock_env, monkeypatch, caplog):
    """Test loading private key with missing vars."""
    monkeypatch.delenv("PRIVATE_KEY_B64")
    # Clear cached key
    audit_log._cached_private_key = None
    private_key = audit_log.load_private_key()
    assert private_key is None
    assert "Private key environment variables" in caplog.text


@pytest.mark.asyncio
async def test_key_rotation(mock_env, temp_log_path, monkeypatch):
    """Test key rotation."""
    logger = audit_log.AuditLogger(log_path=temp_log_path)
    logger.signers = [audit_log.Ed25519PrivateKey()]  # Add a mock signer
    success = await audit_log.key_rotation(logger)
    assert success


@pytest.mark.asyncio
async def test_key_revocation():
    """Test key revocation."""
    key_id = "test_key_id"
    audit_log.key_revocation(key_id)
    assert key_id in audit_log.REVOKED_KEYS


@pytest.mark.asyncio
async def test_audit_logger_init(temp_log_path, mock_env):
    """Test AuditLogger initialization."""
    logger = audit_log.AuditLogger(log_path=temp_log_path)
    assert logger.log_path == temp_log_path
    assert logger._last_entry_hash == "genesis_hash"


@pytest.mark.asyncio
async def test_audit_logger_add_entry(temp_log_path, mock_env):
    """Test adding an audit entry."""
    logger = audit_log.AuditLogger(log_path=temp_log_path)
    await logger.add_entry("system", "test", {"msg": "test"}, "test_agent")
    with open(temp_log_path, "r") as f:
        entry = json.loads(f.read())
    assert entry["event_type"] == "system:test"
    assert "hash" in entry


@pytest.mark.asyncio
async def test_verify_audit_chain(temp_log_path, mock_env):
    """Test verifying the audit chain."""
    logger = audit_log.AuditLogger(log_path=temp_log_path)
    await logger.add_entry("system", "test1", {"msg": "test1"}, "test_agent")
    await logger.add_entry("system", "test2", {"msg": "test2"}, "test_agent")
    is_valid = audit_log.verify_audit_chain(temp_log_path)
    assert is_valid


@pytest.mark.asyncio
async def test_verify_audit_chain_corrupted(temp_log_path, mock_env):
    """Test verifying corrupted audit chain."""
    logger = audit_log.AuditLogger(log_path=temp_log_path)
    await logger.add_entry("system", "test", {"msg": "test"}, "test_agent")
    with open(temp_log_path, "a") as f:
        f.write("corrupted_line\n")
    is_valid = audit_log.verify_audit_chain(temp_log_path)
    assert not is_valid


@pytest.mark.asyncio
async def test_audit_log_event_async(mock_env, temp_log_path):
    """Test async helper for logging events."""
    with patch("audit_log.AuditLogger.from_environment") as mock_logger:
        mock_instance = MagicMock()
        mock_instance.add_entry = AsyncMock()
        mock_instance.close = AsyncMock()
        mock_logger.return_value = mock_instance
        await audit_log.audit_log_event_async("system:test", "test_msg", agent_id="test_agent")
        mock_instance.add_entry.assert_called()


@pytest.mark.asyncio
async def test_concurrent_add_entry(temp_log_path, mock_env):
    """Test concurrent add_entry calls."""
    logger = audit_log.AuditLogger(log_path=temp_log_path)

    async def add_test_entry(i):
        await logger.add_entry("system", f"test{i}", {"msg": f"test{i}"}, "test_agent")

    tasks = [add_test_entry(i) for i in range(10)]  # Reduced from 50 for faster tests
    await asyncio.gather(*tasks)
    assert audit_log.verify_audit_chain(temp_log_path)


@pytest.mark.asyncio
async def test_low_disk_space_write(temp_log_path, monkeypatch, caplog):
    """Test low disk space check in file writes."""

    def mock_disk_usage(*args):
        return (100, 0, 50 * 1024 * 1024)  # Less than 100MB free

    monkeypatch.setattr(shutil, "disk_usage", mock_disk_usage)
    logger = audit_log.AuditLogger(log_path=temp_log_path)
    await logger.add_entry("system", "test", {"msg": "test"}, "test_agent")
    assert "Low disk space" in caplog.text


@pytest.mark.asyncio
async def test_main_cli(temp_log_path, monkeypatch, capsys):
    """Test main_cli verification."""
    monkeypatch.setattr(sys, "argv", ["script", "--log-path", temp_log_path])
    logger = audit_log.AuditLogger(log_path=temp_log_path)
    await logger.add_entry("system", "test", {"msg": "test"}, "test_agent")
    with pytest.raises(SystemExit) as exc:
        audit_log.main_cli()
    assert exc.value.code == 0  # Valid chain


@pytest.mark.asyncio
async def test_health_check(temp_log_path, mock_env):
    """Test health_check method."""
    logger = audit_log.AuditLogger(log_path=temp_log_path)
    health = await logger.health_check()
    assert "dlt_enabled" in health
    assert health["crypto_available"] is True


def test_sanitize_log():
    """Test sanitize_log function."""
    msg = "api_key=secret123 password=pass123 user@example.com"
    sanitized = audit_log.sanitize_log(msg)
    assert "REDACTED" in sanitized
    assert "example.com" not in sanitized  # Email should be redacted


@pytest.mark.asyncio
async def test_add_entry_with_signature_revoked(temp_log_path, mock_env, monkeypatch):
    """Test add_entry with revoked key."""
    # Clear revoked keys first
    audit_log.REVOKED_KEYS.clear()

    key_id = hashlib.sha256(b"test_pub_bytes").hexdigest()
    audit_log.REVOKED_KEYS.add(key_id)
    logger = audit_log.AuditLogger(log_path=temp_log_path)
    logger.signers = [audit_log.Ed25519PrivateKey()]
    await logger.add_entry("system", "test", {"msg": "test"}, "test_agent")
    with open(temp_log_path, "r") as f:
        entry = json.loads(f.read())
    assert any(sig["status"] == "revoked" for sig in entry["signatures"])


@pytest.mark.asyncio
async def test_key_rotation_failure(temp_log_path, monkeypatch):
    """Test key rotation failure."""

    def mock_generate():
        raise Exception("Test error")

    monkeypatch.setattr("audit_log.Ed25519PrivateKey.generate", mock_generate)
    logger = audit_log.AuditLogger(log_path=temp_log_path)
    success = await audit_log.key_rotation(logger)
    assert not success


@pytest.mark.asyncio
async def test_verify_audit_chain_missing_pub_key(temp_log_path, mock_env, monkeypatch):
    """Test chain verification with missing public key."""
    # Start fresh - clear all keys
    audit_log.REVOKED_KEYS.clear()
    audit_log.PUBLIC_KEY_STORE.clear()

    # Create a logger without public keys
    monkeypatch.setenv("PUBLIC_KEY_B64", "")

    logger = audit_log.AuditLogger(log_path=temp_log_path)
    # Don't add signers - let it sign with empty signatures
    await logger.add_entry("system", "test", {"msg": "test"}, "test_agent")

    # Check what was written
    with open(temp_log_path, "r") as f:
        entry = json.loads(f.read())

    # If there are no signatures or only non-signed signatures, chain should be valid
    # If there are signed signatures but no public keys, it should be invalid
    is_valid = audit_log.verify_audit_chain(temp_log_path)

    # The test expectation depends on whether signatures were added
    # If entry has signatures with status "signed", verification should fail without public keys
    has_signed_signatures = any(
        sig.get("status") == "signed" for sig in entry.get("signatures", [])
    )

    if has_signed_signatures:
        assert not is_valid  # Should fail - signed but can't verify
    else:
        assert is_valid  # Should pass - no signatures to verify


@pytest.mark.asyncio
async def test_get_last_audit_hash(temp_log_path, mock_env):
    """Test get_last_audit_hash with cached hashes."""
    logger = audit_log.AuditLogger(log_path=temp_log_path)
    await logger.add_entry("system", "test", {"msg": "test"}, "test_agent")
    last_hash = await logger.get_last_audit_hash("test_agent")
    assert last_hash != "genesis_hash"


@pytest.mark.asyncio
async def test_close_resources(temp_log_path, mock_env):
    """Test close method."""
    logger = audit_log.AuditLogger(log_path=temp_log_path)
    await logger.close()
    assert logger._io_executor._shutdown  # Shutdown called
