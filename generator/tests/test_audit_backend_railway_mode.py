# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_audit_backend_railway_mode.py
"""
Tests for Railway/PaaS mode vs AWS KMS mode detection and handling in audit_backend_core.
Verifies that USE_ENV_SECRETS and AUDIT_CRYPTO_USE_KMS environment variables
correctly control whether KMS decryption is attempted in the audit backend.
"""

import base64
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


class TestAuditBackendRailwayPaaSMode:
    """Tests for Railway/PaaS mode (no KMS) functionality in audit_backend_core."""

    @pytest.fixture
    def plaintext_fernet_key(self):
        """Generate a valid Fernet key for testing."""
        from cryptography.fernet import Fernet
        return Fernet.generate_key()

    @pytest.fixture
    def base64_fernet_key(self, plaintext_fernet_key):
        """Base64-encode the Fernet key (as it would be in ENCRYPTION_KEYS)."""
        return base64.b64encode(plaintext_fernet_key).decode('ascii')

    def test_railway_mode_use_env_secrets_true(
        self, monkeypatch, plaintext_fernet_key, base64_fernet_key
    ):
        """Test that USE_ENV_SECRETS=true skips KMS and uses plaintext key."""
        # Set Railway mode
        monkeypatch.setenv("USE_ENV_SECRETS", "true")
        monkeypatch.setenv("ENCRYPTION_KEYS", f'["{base64_fernet_key}"]')
        monkeypatch.setenv("TESTING", "0")  # Simulate production mode
        
        # Mock settings to ensure we're not in test/dev mode
        mock_settings = MagicMock()
        mock_settings.get.side_effect = lambda key, default=None: {
            "ENVIRONMENT": "production",
            "ENCRYPTION_KEYS": [base64_fernet_key],
            "COMPRESSION_ALGO": "gzip",
            "AWS_REGION": None,
        }.get(key, default)
        
        # Remove the module from sys.modules to force reimport
        module_name = "generator.audit_log.audit_backend.audit_backend_core"
        if module_name in sys.modules:
            del sys.modules[module_name]
        
        # Mock boto3 KMS client to ensure it's NOT called
        mock_kms_client = MagicMock()
        mock_kms_client.decrypt = MagicMock()
        
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_kms_client
        
        with patch("generator.audit_log.audit_backend.audit_backend_core.settings", mock_settings), \
             patch("generator.audit_log.audit_backend.audit_backend_core.boto3", mock_boto3):
            
            # Import the module - this triggers the module-level initialization
            from generator.audit_log.audit_backend import audit_backend_core
            
            # Verify KMS client was NOT created (since we're in Railway mode)
            assert not mock_boto3.client.called, "KMS client should not be created in Railway/PaaS mode"
            assert not mock_kms_client.decrypt.called, "KMS decrypt should not be called in Railway/PaaS mode"
            
            # Verify ENCRYPTER was initialized
            assert audit_backend_core.ENCRYPTER is not None, "ENCRYPTER should be initialized"
            assert len(audit_backend_core._decrypted_keys) > 0, "Should have decrypted keys"

    def test_railway_mode_audit_crypto_use_kms_false(
        self, monkeypatch, plaintext_fernet_key, base64_fernet_key
    ):
        """Test that AUDIT_CRYPTO_USE_KMS=false skips KMS."""
        # Set Railway mode via AUDIT_CRYPTO_USE_KMS
        monkeypatch.setenv("AUDIT_CRYPTO_USE_KMS", "false")
        monkeypatch.setenv("ENCRYPTION_KEYS", f'["{base64_fernet_key}"]')
        monkeypatch.setenv("TESTING", "0")
        
        # Ensure USE_ENV_SECRETS is not set to test AUDIT_CRYPTO_USE_KMS alone
        monkeypatch.delenv("USE_ENV_SECRETS", raising=False)
        
        mock_settings = MagicMock()
        mock_settings.get.side_effect = lambda key, default=None: {
            "ENVIRONMENT": "production",
            "ENCRYPTION_KEYS": [base64_fernet_key],
            "COMPRESSION_ALGO": "gzip",
            "AWS_REGION": None,
        }.get(key, default)
        
        # Remove the module from sys.modules to force reimport
        module_name = "generator.audit_log.audit_backend.audit_backend_core"
        if module_name in sys.modules:
            del sys.modules[module_name]
        
        mock_kms_client = MagicMock()
        mock_kms_client.decrypt = MagicMock()
        
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_kms_client
        
        with patch("generator.audit_log.audit_backend.audit_backend_core.settings", mock_settings), \
             patch("generator.audit_log.audit_backend.audit_backend_core.boto3", mock_boto3):
            
            from generator.audit_log.audit_backend import audit_backend_core
            
            # Verify KMS was not called
            assert not mock_boto3.client.called, "KMS client should not be created when AUDIT_CRYPTO_USE_KMS=false"
            assert not mock_kms_client.decrypt.called, "KMS decrypt should not be called when AUDIT_CRYPTO_USE_KMS=false"
            
            # Verify ENCRYPTER was initialized
            assert audit_backend_core.ENCRYPTER is not None


class TestAuditBackendAWSKMSMode:
    """Tests for AWS KMS mode functionality in audit_backend_core."""

    @pytest.fixture
    def mock_kms_ciphertext(self):
        """Generate mock KMS-encrypted ciphertext."""
        return base64.b64encode(b"mock_kms_encrypted_blob").decode('ascii')

    @pytest.fixture
    def mock_plaintext_fernet_key(self):
        """Generate plaintext Fernet key that KMS would return."""
        from cryptography.fernet import Fernet
        return Fernet.generate_key()

    def test_aws_kms_mode_default(
        self, monkeypatch, mock_kms_ciphertext, mock_plaintext_fernet_key
    ):
        """Test that AWS KMS mode is used by default (USE_ENV_SECRETS not set)."""
        # Remove Railway env vars to test default AWS mode
        monkeypatch.delenv("USE_ENV_SECRETS", raising=False)
        monkeypatch.delenv("AUDIT_CRYPTO_USE_KMS", raising=False)
        monkeypatch.setenv("ENCRYPTION_KEYS", f'["{mock_kms_ciphertext}"]')
        monkeypatch.setenv("TESTING", "0")
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        
        mock_settings = MagicMock()
        mock_settings.get.side_effect = lambda key, default=None: {
            "ENVIRONMENT": "production",
            "ENCRYPTION_KEYS": [mock_kms_ciphertext],
            "COMPRESSION_ALGO": "gzip",
            "AWS_REGION": "us-east-1",
        }.get(key, default)
        
        # Remove the module from sys.modules to force reimport
        module_name = "generator.audit_log.audit_backend.audit_backend_core"
        if module_name in sys.modules:
            del sys.modules[module_name]
        
        # Mock boto3 KMS client
        mock_kms_client = MagicMock()
        mock_kms_client.decrypt = MagicMock(return_value={
            "Plaintext": mock_plaintext_fernet_key
        })
        
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_kms_client
        
        with patch("generator.audit_log.audit_backend.audit_backend_core.settings", mock_settings), \
             patch("generator.audit_log.audit_backend.audit_backend_core.boto3", mock_boto3):
            
            from generator.audit_log.audit_backend import audit_backend_core
            
            # Verify KMS was called
            mock_boto3.client.assert_called_once_with("kms", region_name="us-east-1")
            mock_kms_client.decrypt.assert_called_once()
            
            # Verify ENCRYPTER was initialized with decrypted key
            assert audit_backend_core.ENCRYPTER is not None
            assert mock_plaintext_fernet_key in audit_backend_core._decrypted_keys


class TestModeDetectionLogging:
    """Tests that mode detection logging works correctly in audit_backend_core."""

    def test_railway_mode_logging(self, monkeypatch, caplog):
        """Test that Railway mode logs the correct message."""
        import logging
        caplog.set_level(logging.INFO)
        
        from cryptography.fernet import Fernet
        test_key = Fernet.generate_key()
        base64_key = base64.b64encode(test_key).decode('ascii')
        
        monkeypatch.setenv("USE_ENV_SECRETS", "true")
        monkeypatch.setenv("ENCRYPTION_KEYS", f'["{base64_key}"]')
        monkeypatch.setenv("TESTING", "0")
        
        mock_settings = MagicMock()
        mock_settings.get.side_effect = lambda key, default=None: {
            "ENVIRONMENT": "production",
            "ENCRYPTION_KEYS": [base64_key],
            "COMPRESSION_ALGO": "gzip",
            "AWS_REGION": None,
        }.get(key, default)
        
        # Remove the module from sys.modules to force reimport
        module_name = "generator.audit_log.audit_backend.audit_backend_core"
        if module_name in sys.modules:
            del sys.modules[module_name]
        
        with patch("generator.audit_log.audit_backend.audit_backend_core.settings", mock_settings):
            from generator.audit_log.audit_backend import audit_backend_core
            
            # Check that Railway mode message was logged
            assert any(
                "Railway/PaaS mode" in record.message 
                for record in caplog.records
            ), "Should log Railway/PaaS mode detection"

    def test_kms_mode_logging(self, monkeypatch, caplog):
        """Test that AWS KMS mode logs the correct message."""
        import logging
        caplog.set_level(logging.INFO)
        
        from cryptography.fernet import Fernet
        plaintext_key = Fernet.generate_key()
        mock_ciphertext = base64.b64encode(b"mock_ciphertext").decode('ascii')
        
        # Set AWS KMS mode
        monkeypatch.delenv("USE_ENV_SECRETS", raising=False)
        monkeypatch.setenv("AUDIT_CRYPTO_USE_KMS", "true")
        monkeypatch.setenv("ENCRYPTION_KEYS", f'["{mock_ciphertext}"]')
        monkeypatch.setenv("TESTING", "0")
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        
        mock_settings = MagicMock()
        mock_settings.get.side_effect = lambda key, default=None: {
            "ENVIRONMENT": "production",
            "ENCRYPTION_KEYS": [mock_ciphertext],
            "COMPRESSION_ALGO": "gzip",
            "AWS_REGION": "us-east-1",
        }.get(key, default)
        
        # Remove the module from sys.modules to force reimport
        module_name = "generator.audit_log.audit_backend.audit_backend_core"
        if module_name in sys.modules:
            del sys.modules[module_name]
        
        # Mock KMS
        mock_kms_client = MagicMock()
        mock_kms_client.decrypt = MagicMock(return_value={
            "Plaintext": plaintext_key
        })
        
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_kms_client
        
        with patch("generator.audit_log.audit_backend.audit_backend_core.settings", mock_settings), \
             patch("generator.audit_log.audit_backend.audit_backend_core.boto3", mock_boto3):
            
            from generator.audit_log.audit_backend import audit_backend_core
            
            # Check that AWS KMS mode message was logged
            assert any(
                "AWS KMS mode" in record.message 
                for record in caplog.records
            ), "Should log AWS KMS mode detection"


class TestIsRailwayOrPaaSModeFunction:
    """Tests for the _is_railway_or_paas_mode() helper function."""

    def test_use_env_secrets_true_returns_correct_values(self, monkeypatch):
        """Test that USE_ENV_SECRETS=true is correctly detected."""
        monkeypatch.setenv("USE_ENV_SECRETS", "true")
        monkeypatch.delenv("AUDIT_CRYPTO_USE_KMS", raising=False)
        
        from generator.audit_log.audit_backend.audit_backend_core import _is_railway_or_paas_mode
        
        use_env_secrets, use_kms = _is_railway_or_paas_mode()
        assert use_env_secrets is True
        assert use_kms is True  # Default value

    def test_audit_crypto_use_kms_false_returns_correct_values(self, monkeypatch):
        """Test that AUDIT_CRYPTO_USE_KMS=false is correctly detected."""
        monkeypatch.delenv("USE_ENV_SECRETS", raising=False)
        monkeypatch.setenv("AUDIT_CRYPTO_USE_KMS", "false")
        
        from generator.audit_log.audit_backend.audit_backend_core import _is_railway_or_paas_mode
        
        use_env_secrets, use_kms = _is_railway_or_paas_mode()
        assert use_env_secrets is False
        assert use_kms is False

    def test_both_env_vars_set_returns_correct_values(self, monkeypatch):
        """Test that both env vars set correctly returns values."""
        monkeypatch.setenv("USE_ENV_SECRETS", "true")
        monkeypatch.setenv("AUDIT_CRYPTO_USE_KMS", "false")
        
        from generator.audit_log.audit_backend.audit_backend_core import _is_railway_or_paas_mode
        
        use_env_secrets, use_kms = _is_railway_or_paas_mode()
        assert use_env_secrets is True
        assert use_kms is False

    def test_default_values_when_no_env_vars(self, monkeypatch):
        """Test default values when no env vars are set."""
        monkeypatch.delenv("USE_ENV_SECRETS", raising=False)
        monkeypatch.delenv("AUDIT_CRYPTO_USE_KMS", raising=False)
        
        from generator.audit_log.audit_backend.audit_backend_core import _is_railway_or_paas_mode
        
        use_env_secrets, use_kms = _is_railway_or_paas_mode()
        assert use_env_secrets is False
        assert use_kms is True  # Default is to use KMS


class TestNoKMSCallInRailwayMode:
    """Tests that specifically verify KMS is not called in Railway/PaaS mode."""

    def test_no_kms_decrypt_called_in_railway_mode(self, monkeypatch):
        """Verify that kms_client.decrypt() is never called in Railway mode."""
        from cryptography.fernet import Fernet
        test_key = Fernet.generate_key()
        base64_key = base64.b64encode(test_key).decode('ascii')
        
        monkeypatch.setenv("USE_ENV_SECRETS", "true")
        monkeypatch.setenv("ENCRYPTION_KEYS", f'["{base64_key}"]')
        monkeypatch.setenv("TESTING", "0")
        
        mock_settings = MagicMock()
        mock_settings.get.side_effect = lambda key, default=None: {
            "ENVIRONMENT": "production",
            "ENCRYPTION_KEYS": [base64_key],
            "COMPRESSION_ALGO": "gzip",
            "AWS_REGION": None,
        }.get(key, default)
        
        # Create a mock that will fail if decrypt is called
        mock_kms_client = MagicMock()
        def decrypt_should_not_be_called(*args, **kwargs):
            raise AssertionError("KMS decrypt() should not be called in Railway/PaaS mode!")
        mock_kms_client.decrypt = decrypt_should_not_be_called
        
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_kms_client
        
        # Remove the module from sys.modules to force reimport
        module_name = "generator.audit_log.audit_backend.audit_backend_core"
        if module_name in sys.modules:
            del sys.modules[module_name]
        
        with patch("generator.audit_log.audit_backend.audit_backend_core.settings", mock_settings), \
             patch("generator.audit_log.audit_backend.audit_backend_core.boto3", mock_boto3):
            
            # This should succeed without calling KMS
            from generator.audit_log.audit_backend import audit_backend_core
            
            # If we get here, KMS was not called (good!)
            assert audit_backend_core.ENCRYPTER is not None
            assert len(audit_backend_core._decrypted_keys) > 0
