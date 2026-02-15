# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_railway_kms_mode.py
"""
Tests for Railway/PaaS mode vs AWS KMS mode detection and handling.
Verifies that USE_ENV_SECRETS and AUDIT_CRYPTO_USE_KMS environment variables
correctly control whether KMS decryption is attempted.
"""

import asyncio
import base64
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRailwayPaaSMode:
    """Tests for Railway/PaaS mode (no KMS) functionality."""

    @pytest.fixture
    def plaintext_key(self):
        """Generate a plaintext 32-byte key for testing."""
        return os.urandom(32)

    @pytest.fixture
    def base64_plaintext_key(self, plaintext_key):
        """Base64-encode the plaintext key."""
        return base64.b64encode(plaintext_key).decode('ascii')

    @pytest.mark.asyncio
    async def test_railway_mode_use_env_secrets_true(
        self, monkeypatch, plaintext_key, base64_plaintext_key
    ):
        """Test that USE_ENV_SECRETS=true skips KMS and uses plaintext key."""
        # Set Railway mode
        monkeypatch.setenv("USE_ENV_SECRETS", "true")
        monkeypatch.setenv("AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64", base64_plaintext_key)
        
        # Mock production mode
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )
        
        # Reset global state
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._SOFTWARE_KEY_MASTER",
            None,
        )
        
        # Mock the secret manager to return plaintext (already decoded)
        mock_get_secret = AsyncMock(return_value=plaintext_key)
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.secrets._get_secret_with_retries_and_rate_limit",
            mock_get_secret,
        )
        
        # Import after setting env vars
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            _ensure_software_key_master,
        )
        
        # Should succeed without KMS
        result = await _ensure_software_key_master()
        
        assert result == plaintext_key[:32]
        # Verify secret was fetched
        mock_get_secret.assert_called_once()

    @pytest.mark.asyncio
    async def test_railway_mode_audit_crypto_use_kms_false(
        self, monkeypatch, plaintext_key, base64_plaintext_key
    ):
        """Test that AUDIT_CRYPTO_USE_KMS=false skips KMS."""
        # Set Railway mode via AUDIT_CRYPTO_USE_KMS
        monkeypatch.setenv("AUDIT_CRYPTO_USE_KMS", "false")
        monkeypatch.setenv("AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64", base64_plaintext_key)
        
        # Ensure USE_ENV_SECRETS is not set to test AUDIT_CRYPTO_USE_KMS alone
        monkeypatch.delenv("USE_ENV_SECRETS", raising=False)
        
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )
        
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._SOFTWARE_KEY_MASTER",
            None,
        )
        
        # Mock the secret manager to return plaintext (already decoded)
        mock_get_secret = AsyncMock(return_value=plaintext_key)
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.secrets._get_secret_with_retries_and_rate_limit",
            mock_get_secret,
        )
        
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            _ensure_software_key_master,
        )
        
        # Should succeed without KMS
        result = await _ensure_software_key_master()
        
        assert result == plaintext_key[:32]


class TestAWSKMSMode:
    """Tests for AWS KMS mode functionality."""

    @pytest.fixture
    def mock_kms_ciphertext(self):
        """Generate mock KMS-encrypted ciphertext."""
        # This would normally be a KMS-encrypted blob, but for testing we use fake data
        return base64.b64encode(b"mock_kms_encrypted_blob").decode('ascii')

    @pytest.fixture
    def mock_plaintext_key(self):
        """Generate plaintext key that KMS would return."""
        return os.urandom(32)

    @pytest.mark.asyncio
    async def test_aws_kms_mode_default(
        self, monkeypatch, mock_kms_ciphertext, mock_plaintext_key
    ):
        """Test that AWS KMS mode is used by default (USE_ENV_SECRETS not set)."""
        # Remove Railway env vars to test default AWS mode
        monkeypatch.delenv("USE_ENV_SECRETS", raising=False)
        monkeypatch.delenv("AUDIT_CRYPTO_USE_KMS", raising=False)
        monkeypatch.setenv("AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64", mock_kms_ciphertext)
        
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )
        
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._SOFTWARE_KEY_MASTER",
            None,
        )
        
        # Mock the secret manager to return ciphertext (base64 decoded)
        mock_get_secret = AsyncMock(return_value=b"mock_kms_encrypted_blob")
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.secrets._get_secret_with_retries_and_rate_limit",
            mock_get_secret,
        )
        
        # Mock boto3 KMS client
        mock_kms_client = MagicMock()
        mock_kms_client.decrypt = MagicMock(return_value={
            "Plaintext": mock_plaintext_key
        })
        
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_kms_client
        
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory.boto3",
            mock_boto3,
        )
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory.HAS_BOTO3",
            True,
        )
        
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            _ensure_software_key_master,
        )
        
        # Should use KMS to decrypt
        result = await _ensure_software_key_master()
        
        assert result == mock_plaintext_key[:32]
        # Verify KMS was called
        mock_kms_client.decrypt.assert_called_once()


class TestModeDetectionLogging:
    """Tests that mode detection logging works correctly."""

    @pytest.mark.asyncio
    async def test_railway_mode_logging(self, monkeypatch, caplog):
        """Test that Railway mode logs the correct message."""
        import logging
        caplog.set_level(logging.INFO)
        
        monkeypatch.setenv("USE_ENV_SECRETS", "true")
        monkeypatch.setenv("AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64", 
                          base64.b64encode(os.urandom(32)).decode())
        
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )
        
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._SOFTWARE_KEY_MASTER",
            None,
        )
        
        # Mock the secret manager
        mock_get_secret = AsyncMock(return_value=os.urandom(32))
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.secrets._get_secret_with_retries_and_rate_limit",
            mock_get_secret,
        )
        
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            _ensure_software_key_master,
        )
        
        await _ensure_software_key_master()
        
        # Check that Railway mode message was logged
        assert any(
            "Railway/PaaS mode" in record.message 
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_kms_mode_logging(self, monkeypatch, caplog):
        """Test that AWS KMS mode logs the correct message."""
        import logging
        caplog.set_level(logging.INFO)
        
        # Set AWS KMS mode
        monkeypatch.delenv("USE_ENV_SECRETS", raising=False)
        monkeypatch.setenv("AUDIT_CRYPTO_USE_KMS", "true")
        monkeypatch.setenv("AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64", 
                          base64.b64encode(b"mock_ciphertext").decode())
        
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )
        
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._SOFTWARE_KEY_MASTER",
            None,
        )
        
        # Mock secret manager and KMS
        mock_get_secret = AsyncMock(return_value=b"mock_ciphertext")
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.secrets._get_secret_with_retries_and_rate_limit",
            mock_get_secret,
        )
        
        mock_kms_client = MagicMock()
        mock_kms_client.decrypt = MagicMock(return_value={
            "Plaintext": os.urandom(32)
        })
        
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_kms_client
        
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory.boto3",
            mock_boto3,
        )
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory.HAS_BOTO3",
            True,
        )
        
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            _ensure_software_key_master,
        )
        
        await _ensure_software_key_master()
        
        # Check that AWS KMS mode message was logged
        assert any(
            "AWS KMS mode" in record.message 
            for record in caplog.records
        )


class TestDoubleDecodingFix:
    """Tests that the double base64 decoding bug is fixed."""

    @pytest.mark.asyncio
    async def test_no_double_decoding_in_railway_mode(self, monkeypatch):
        """Test that base64 is only decoded once in Railway mode."""
        plaintext_key = os.urandom(32)
        base64_key = base64.b64encode(plaintext_key).decode('ascii')
        
        monkeypatch.setenv("USE_ENV_SECRETS", "true")
        monkeypatch.setenv("AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64", base64_key)
        
        # Mock EnvVarSecretManager to return already-decoded bytes
        from generator.audit_log.audit_crypto.secrets import EnvVarSecretManager
        mock_secret_manager = EnvVarSecretManager()
        
        # Patch to use our mock
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.secrets._SECRET_MANAGER",
            mock_secret_manager,
        )
        
        from generator.audit_log.audit_crypto.secrets import aget_kms_master_key_ciphertext_blob
        
        # In Railway mode, this should return the plaintext bytes without double decoding
        result = await aget_kms_master_key_ciphertext_blob()
        
        # Result should be the plaintext key (decoded once by EnvVarSecretManager)
        assert result == plaintext_key
        # NOT double-decoded garbage


class TestErrorMessages:
    """Tests that error messages correctly identify deployment mode."""

    @pytest.mark.asyncio
    async def test_invalid_ciphertext_error_in_railway_mode(self, monkeypatch):
        """Test that InvalidCiphertextException error message mentions Railway mode."""
        monkeypatch.setenv("USE_ENV_SECRETS", "false")  # Simulating misconfiguration
        monkeypatch.setenv("AUDIT_CRYPTO_USE_KMS", "true")
        
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )
        
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._SOFTWARE_KEY_MASTER",
            None,
        )
        
        # Mock to raise InvalidCiphertextException
        class MockClientError(Exception):
            def __init__(self):
                self.response = {
                    'Error': {
                        'Code': 'InvalidCiphertextException',
                        'Message': 'Test error'
                    }
                }
        
        mock_get_secret = AsyncMock(return_value=b"mock_ciphertext")
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.secrets._get_secret_with_retries_and_rate_limit",
            mock_get_secret,
        )
        
        mock_kms_client = MagicMock()
        mock_kms_client.decrypt.side_effect = MockClientError()
        
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_kms_client
        
        # Mock botocore
        mock_botocore = MagicMock()
        mock_botocore.ClientError = MockClientError
        
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory.boto3",
            mock_boto3,
        )
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory.HAS_BOTO3",
            True,
        )
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory.botocore",
            mock_botocore,
        )
        
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            CryptoInitializationError,
            _ensure_software_key_master,
        )
        
        # Should raise with improved error message
        with pytest.raises(CryptoInitializationError) as exc_info:
            await _ensure_software_key_master()
        
        error_msg = str(exc_info.value)
        # Check that error message includes configuration details
        assert "USE_ENV_SECRETS" in error_msg or "AUDIT_CRYPTO_USE_KMS" in error_msg
        assert "RAILWAY" in error_msg.upper() or "PaaS" in error_msg
