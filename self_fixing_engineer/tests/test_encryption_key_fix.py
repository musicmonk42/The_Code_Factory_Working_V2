# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test fixes for ENCRYPTION_KEY validation and audit log corruption handling.
This test file validates:
1. ENCRYPTION_KEY validation handles dict/SecretStr/str types correctly
2. Corrupted audit trails at entry 0 are reset gracefully
"""

import os
import tempfile
import json
from unittest.mock import patch, MagicMock
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from self_fixing_engineer.arbiter.policy.config import ArbiterConfig


# Test 1: ENCRYPTION_KEY validation with different types
@pytest.fixture(autouse=True)
def clear_config_globals():
    """Clear global config instances."""
    ArbiterConfig._instance = None
    yield
    ArbiterConfig._instance = None


class TestEncryptionKeyValidation:
    """Test ENCRYPTION_KEY validation with different input types."""

    def test_encryption_key_with_string(self):
        """Test ENCRYPTION_KEY validation with string input."""
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "ENCRYPTION_KEY": key,
            "REDIS_URL": "redis://localhost:6379",
            "OPENAI_API_KEY": "test-key",
        }):
            config = ArbiterConfig()
            assert config.ENCRYPTION_KEY.get_secret_value() == key

    def test_encryption_key_with_secret_str(self):
        """Test ENCRYPTION_KEY validation with SecretStr input."""
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "REDIS_URL": "redis://localhost:6379",
            "OPENAI_API_KEY": "test-key",
        }):
            # Directly pass SecretStr
            config = ArbiterConfig(ENCRYPTION_KEY=SecretStr(key))
            assert config.ENCRYPTION_KEY.get_secret_value() == key

    def test_config_init_with_double_underscore_env_vars(self):
        """Test that config initializes correctly with __-delimited env vars present."""
        key = Fernet.generate_key().decode()
        
        # After removing env_nested_delimiter="__", __-delimited env vars like
        # KAFKA__BOOTSTRAP_SERVERS (Kafka config) and PYTHON__HASH_SEED (Python internals)
        # should no longer cause pydantic-settings to pass dict objects as field values.
        # These represent common patterns found in production environments.
        
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "ENCRYPTION_KEY": key,
            "REDIS_URL": "redis://localhost:6379",
            "OPENAI_API_KEY": "test-key",
            # Add __-delimited env vars that would have caused the bug
            "KAFKA__BOOTSTRAP_SERVERS": "localhost:9092",
            "PYTHON__HASH_SEED": "random",
        }):
            # Config should initialize correctly and ENCRYPTION_KEY should be the string value
            config = ArbiterConfig()
            assert config.ENCRYPTION_KEY is not None
            assert config.ENCRYPTION_KEY.get_secret_value() == key
            
            # Verify __-delimited env vars are still accessible in the environment
            assert os.environ.get("KAFKA__BOOTSTRAP_SERVERS") == "localhost:9092"
            assert os.environ.get("PYTHON__HASH_SEED") == "random"

    def test_encryption_key_invalid_length(self):
        """Test ENCRYPTION_KEY validation fails for invalid length."""
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "ENCRYPTION_KEY": "invalid_short_key",
            "REDIS_URL": "redis://localhost:6379",
            "OPENAI_API_KEY": "test-key",
        }):
            with pytest.raises(ValueError, match="Invalid ENCRYPTION_KEY"):
                ArbiterConfig()

    def test_encryption_key_not_required_in_development(self):
        """Test ENCRYPTION_KEY is not required in development."""
        with patch.dict(os.environ, {
            "APP_ENV": "development",
        }, clear=True):
            config = ArbiterConfig()
            # Should not crash in development mode
            assert config is not None


class TestAuditLogCorruptionHandling:
    """Test audit log corruption handling at startup."""

    def test_verify_audit_chain_returns_failure_index(self):
        """Test verify_audit_chain returns failure index when requested."""
        from self_fixing_engineer.guardrails.audit_log import verify_audit_chain, hash_entry
        
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            
            # Create a corrupted entry at position 0
            corrupted_entry = {
                "timestamp": "2025-01-01T00:00:00",
                "kind": "test",
                "name": "test_event",
                "detail": {},
                "hash": "wrong_hash",  # Intentionally wrong
                "previous_log_hash": "genesis_hash",
            }
            
            with open(log_path, "w") as f:
                f.write(json.dumps(corrupted_entry) + "\n")
            
            # Verify with return_failure_index=True
            is_valid, failure_index = verify_audit_chain(log_path, return_failure_index=True)
            
            assert is_valid is False
            assert failure_index == 0

    def test_audit_logger_resets_corrupted_chain_at_entry_0(self):
        """Test that AuditLogger resets chain when corruption is at entry 0."""
        from self_fixing_engineer.guardrails.audit_log import AuditLogger
        
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            
            # Create a corrupted entry at position 0
            corrupted_entry = {
                "timestamp": "2025-01-01T00:00:00",
                "kind": "test",
                "name": "test_event",
                "detail": {},
                "hash": None,  # Corrupted hash
                "previous_log_hash": "genesis_hash",
            }
            
            with open(log_path, "w") as f:
                f.write(json.dumps(corrupted_entry) + "\n")
            
            # Initialize AuditLogger in production mode
            with patch.dict(os.environ, {"APP_ENV": "production"}):
                logger = AuditLogger(log_path=log_path)
                
                # Check that the corrupted file was backed up
                backup_files = [f for f in os.listdir(tmpdir) if f.startswith("audit.log.corrupted")]
                assert len(backup_files) == 1, "Corrupted file should be backed up"
                
                # Check that logger is not in degraded mode (chain was reset)
                assert logger.degraded_mode is False, "Logger should not be in degraded mode after reset"
                
                # Check that chain was reset to genesis
                assert logger._last_entry_hash == "genesis_hash"

    def test_audit_logger_degraded_mode_for_non_zero_corruption(self):
        """Test that AuditLogger enters degraded mode for corruption not at entry 0."""
        from self_fixing_engineer.guardrails.audit_log import AuditLogger, hash_entry
        
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            
            # Create a valid first entry
            entry1 = {
                "timestamp": "2025-01-01T00:00:00",
                "kind": "test",
                "name": "event1",
                "detail": {},
                "previous_log_hash": "genesis_hash",
            }
            entry1["hash"] = hash_entry(entry1)
            
            # Create a corrupted second entry
            entry2 = {
                "timestamp": "2025-01-01T00:01:00",
                "kind": "test",
                "name": "event2",
                "detail": {},
                "hash": "wrong_hash",  # Intentionally wrong
                "previous_log_hash": entry1["hash"],
            }
            
            with open(log_path, "w") as f:
                f.write(json.dumps(entry1) + "\n")
                f.write(json.dumps(entry2) + "\n")
            
            # Initialize AuditLogger in production mode
            with patch.dict(os.environ, {"APP_ENV": "production"}):
                logger = AuditLogger(log_path=log_path)
                
                # Check that logger is in degraded mode (chain not reset)
                assert logger.degraded_mode is True, "Logger should be in degraded mode"
                
                # Check that corrupted file was NOT backed up (only happens at entry 0)
                backup_files = [f for f in os.listdir(tmpdir) if f.startswith("audit.log.corrupted")]
                assert len(backup_files) == 0, "File should not be backed up for non-zero corruption"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
