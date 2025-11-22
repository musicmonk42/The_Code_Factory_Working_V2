# test_encryption.py

import pytest
import os
import json
from unittest.mock import Mock, patch, MagicMock
from cryptography.fernet import Fernet, InvalidToken
from botocore.exceptions import NoCredentialsError

from arbiter.learner.encryption import (
    ArbiterConfig,
    encrypt_value,
    decrypt_value,
    key_rotation_counter,
    learn_error_counter
)


class TestArbiterConfig:
    """Test suite for ArbiterConfig class."""
    
    def test_default_configuration(self):
        """Test default configuration values."""
        assert ArbiterConfig.VALID_DOMAIN_PATTERN == r"^[A-Za-z0-9_.-]+$"
        assert ArbiterConfig.KNOWLEDGE_REDIS_TTL_SECONDS == 3600
        assert ArbiterConfig.MAX_LEARN_RETRIES == 3
        assert ArbiterConfig.SELF_AUDIT_INTERVAL_SECONDS == 3600
        assert "FinancialData" in ArbiterConfig.ENCRYPTED_DOMAINS
        assert "PersonalData" in ArbiterConfig.ENCRYPTED_DOMAINS
        assert "SecretProject" in ArbiterConfig.ENCRYPTED_DOMAINS
    
    def test_environment_variable_defaults(self):
        """Test that environment variables have defaults."""
        # These should have defaults even if env vars not set
        assert ArbiterConfig.JIRA_URL is not None
        assert ArbiterConfig.NEO4J_URL is not None
        assert ArbiterConfig.LLM_PROVIDER == "openai"
        assert ArbiterConfig.LLM_MODEL == "gpt-4o-mini"
    
    @patch('arbiter.learner.encryption.boto3.client')
    def test_load_keys_from_ssm_success(self, mock_boto_client):
        """Test successful key loading from AWS SSM."""
        # Setup mock SSM client
        mock_ssm = MagicMock()
        mock_boto_client.return_value = mock_ssm
        
        # Mock SSM responses
        test_key = Fernet.generate_key().decode('utf-8')
        mock_ssm.get_parameter.return_value = {
            'Parameter': {'Value': test_key}
        }
        
        # Set environment variables
        with patch.dict(os.environ, {
            'AWS_REGION': 'us-east-1',
            'ENCRYPTION_KEY_VERSIONS': 'v1,v2',
            'ENCRYPTION_KEY_V1_PATH': '/test/key/v1',
            'ENCRYPTION_KEY_V2_PATH': '/test/key/v2'
        }):
            # Clear existing keys
            ArbiterConfig.ENCRYPTION_KEYS = {}
            
            # Load keys
            keys = ArbiterConfig.load_keys()
            
            # Verify SSM was called correctly
            assert mock_ssm.get_parameter.call_count == 2
            mock_ssm.get_parameter.assert_any_call(
                Name='/test/key/v1',
                WithDecryption=True
            )
            mock_ssm.get_parameter.assert_any_call(
                Name='/test/key/v2',
                WithDecryption=True
            )
            
            # Verify keys were loaded
            assert 'v1' in keys
            assert 'v2' in keys
            assert all(isinstance(k, Fernet) for k in keys.values())
    
    @patch('arbiter.learner.encryption.boto3.client')
    def test_load_keys_from_ssm_failure_fallback(self, mock_boto_client):
        """Test fallback to in-memory key when SSM fails."""
        # Make SSM client raise an error
        mock_boto_client.side_effect = NoCredentialsError()
        
        with patch.dict(os.environ, {
            'AWS_REGION': 'us-east-1',
            'FALLBACK_ENCRYPTION_KEY': Fernet.generate_key().decode('utf-8')
        }):
            # Clear existing keys
            ArbiterConfig.ENCRYPTION_KEYS = {}
            
            # Load keys - should fall back
            keys = ArbiterConfig.load_keys()
            
            # Verify fallback key was loaded
            assert 'v1' in keys
            assert isinstance(keys['v1'], Fernet)
    
    @patch('arbiter.learner.encryption.boto3.client')
    def test_load_keys_no_ssm_paths(self, mock_boto_client):
        """Test handling when SSM paths are not configured."""
        mock_ssm = MagicMock()
        mock_boto_client.return_value = mock_ssm
        
        with patch.dict(os.environ, {
            'AWS_REGION': 'us-east-1',
            'ENCRYPTION_KEY_VERSIONS': 'v1'
            # Note: No ENCRYPTION_KEY_V1_PATH set
        }):
            # Clear existing keys
            ArbiterConfig.ENCRYPTION_KEYS = {}
            
            # Should raise ValueError and fall back
            keys = ArbiterConfig.load_keys()
            
            # Should have fallback key
            assert 'v1' in keys
            assert isinstance(keys['v1'], Fernet)
    
    @pytest.mark.asyncio
    async def test_rotate_keys(self):
        """Test key rotation functionality."""
        # Set up initial keys
        ArbiterConfig.ENCRYPTION_KEYS = {'v1': Fernet(Fernet.generate_key())}
        
        with patch.object(key_rotation_counter, 'labels') as mock_counter:
            mock_labels = MagicMock()
            mock_counter.return_value = mock_labels
            
            # Rotate keys
            await ArbiterConfig.rotate_keys('v2')
            
            # Verify new key was added
            assert 'v2' in ArbiterConfig.ENCRYPTION_KEYS
            # FIX: Should be Fernet instance, not bytes
            assert isinstance(ArbiterConfig.ENCRYPTION_KEYS['v2'], Fernet)
            
            # Verify metric was incremented
            mock_counter.assert_called_with(version='v2')
            mock_labels.inc.assert_called_once()


class TestEncryptValue:
    """Test suite for encrypt_value function."""
    
    @pytest.mark.asyncio
    async def test_encrypt_simple_value(self):
        """Test encrypting a simple value."""
        cipher = Fernet(Fernet.generate_key())
        value = {"test": "data", "number": 42}
        
        encrypted = await encrypt_value(value, cipher, "v1")
        
        assert isinstance(encrypted, bytes)
        assert encrypted.startswith(b"v1:")
        
        # Verify we can decrypt it back
        encrypted_data = encrypted[3:]  # Remove "v1:" prefix
        decrypted_json = cipher.decrypt(encrypted_data).decode('utf-8')
        decrypted_value = json.loads(decrypted_json)
        assert decrypted_value == value
    
    @pytest.mark.asyncio
    async def test_encrypt_with_different_key_ids(self):
        """Test encryption with different key IDs."""
        cipher = Fernet(Fernet.generate_key())
        value = {"data": "test"}
        
        encrypted_v1 = await encrypt_value(value, cipher, "v1")
        encrypted_v2 = await encrypt_value(value, cipher, "v2")
        
        assert encrypted_v1.startswith(b"v1:")
        assert encrypted_v2.startswith(b"v2:")
        assert encrypted_v1 != encrypted_v2  # Different due to Fernet's timestamp
    
    @pytest.mark.asyncio
    async def test_encrypt_complex_types(self):
        """Test encrypting complex data types."""
        cipher = Fernet(Fernet.generate_key())
        
        # Test with datetime (uses default=str)
        from datetime import datetime
        value = {
            "timestamp": datetime.now(),
            "list": [1, 2, 3],
            "nested": {"key": "value"}
        }
        
        encrypted = await encrypt_value(value, cipher)
        assert isinstance(encrypted, bytes)
    
    @pytest.mark.asyncio
    async def test_encrypt_failure(self):
        """Test encryption failure handling."""
        cipher = Mock()
        cipher.encrypt.side_effect = Exception("Encryption failed")
        
        with patch.object(learn_error_counter, 'labels') as mock_counter:
            mock_labels = MagicMock()
            mock_counter.return_value = mock_labels
            
            with pytest.raises(ValueError, match="Failed to encrypt value"):
                await encrypt_value({"test": "data"}, cipher)
            
            # Verify error counter was incremented
            mock_counter.assert_called_with(
                domain='encryption',
                error_type='serialization_failed'
            )
            mock_labels.inc.assert_called_once()


class TestDecryptValue:
    """Test suite for decrypt_value function."""
    
    @pytest.mark.asyncio
    async def test_decrypt_simple_value(self):
        """Test decrypting a simple value."""
        key = Fernet.generate_key()
        cipher = Fernet(key)
        ciphers = {"v1": cipher}
        
        # Encrypt a value first
        original = {"test": "data", "number": 42}
        serialized = json.dumps(original).encode('utf-8')
        encrypted_data = cipher.encrypt(serialized)
        encrypted = b"v1:" + encrypted_data
        
        # Decrypt it
        decrypted = await decrypt_value(encrypted, ciphers)
        assert decrypted == original
    
    @pytest.mark.asyncio
    async def test_decrypt_with_multiple_keys(self):
        """Test decryption with multiple key versions."""
        key1 = Fernet.generate_key()
        key2 = Fernet.generate_key()
        ciphers = {
            "v1": Fernet(key1),
            "v2": Fernet(key2)
        }
        
        # Encrypt with v2
        original = {"data": "test"}
        serialized = json.dumps(original).encode('utf-8')
        encrypted_data = ciphers["v2"].encrypt(serialized)
        encrypted = b"v2:" + encrypted_data
        
        # Decrypt should use v2 key
        decrypted = await decrypt_value(encrypted, ciphers)
        assert decrypted == original
    
    @pytest.mark.asyncio
    async def test_decrypt_without_key_id(self):
        """Test decryption of data without key ID prefix."""
        key = Fernet.generate_key()
        cipher = Fernet(key)
        ciphers = {"v1": cipher}
        
        # Encrypt without prefix (legacy format)
        original = {"legacy": "data"}
        serialized = json.dumps(original).encode('utf-8')
        encrypted = cipher.encrypt(serialized)
        
        # Should default to v1
        decrypted = await decrypt_value(encrypted, ciphers)
        assert decrypted == original
    
    @pytest.mark.asyncio
    async def test_decrypt_invalid_input_type(self):
        """Test decryption with invalid input type."""
        ciphers = {"v1": Fernet(Fernet.generate_key())}
        
        with pytest.raises(TypeError, match="Expected bytes"):
            await decrypt_value("not bytes", ciphers)
        
        with pytest.raises(TypeError, match="Expected bytes"):
            await decrypt_value(12345, ciphers)
    
    @pytest.mark.asyncio
    async def test_decrypt_unknown_key_id(self):
        """Test decryption with unknown key ID."""
        ciphers = {"v1": Fernet(Fernet.generate_key())}
        encrypted = b"v99:somedata"
        
        with patch.object(learn_error_counter, 'labels') as mock_counter:
            mock_labels = MagicMock()
            mock_counter.return_value = mock_labels
            
            with pytest.raises(InvalidToken, match="Unknown encryption key ID: v99"):
                await decrypt_value(encrypted, ciphers)
            
            mock_counter.assert_called_with(
                domain='decryption',
                error_type='unknown_key_id'
            )
            mock_labels.inc.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_decrypt_invalid_token(self):
        """Test decryption with invalid encrypted data."""
        ciphers = {"v1": Fernet(Fernet.generate_key())}
        encrypted = b"v1:invalid_encrypted_data"
        
        with patch.object(learn_error_counter, 'labels') as mock_counter:
            mock_labels = MagicMock()
            mock_counter.return_value = mock_labels
            
            with pytest.raises(InvalidToken):
                await decrypt_value(encrypted, ciphers)
            
            mock_counter.assert_called_with(
                domain='decryption',
                error_type='invalid_token'
            )
            mock_labels.inc.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_decrypt_deserialization_failure(self):
        """Test decryption with JSON deserialization failure."""
        key = Fernet.generate_key()
        cipher = Fernet(key)
        ciphers = {"v1": cipher}
        
        # Encrypt non-JSON data
        encrypted_data = cipher.encrypt(b"not json data")
        encrypted = b"v1:" + encrypted_data
        
        with patch.object(learn_error_counter, 'labels') as mock_counter:
            mock_labels = MagicMock()
            mock_counter.return_value = mock_labels
            
            with pytest.raises(InvalidToken, match="Decryption or deserialization failed"):
                await decrypt_value(encrypted, ciphers)
            
            mock_counter.assert_called_with(
                domain='decryption',
                error_type='deserialization_failed'
            )
            mock_labels.inc.assert_called_once()


class TestIntegration:
    """Integration tests for encryption and decryption."""
    
    @pytest.mark.asyncio
    async def test_encrypt_decrypt_roundtrip(self):
        """Test full encryption and decryption cycle."""
        # Set up keys
        key1 = Fernet.generate_key()
        key2 = Fernet.generate_key()
        ciphers = {
            "v1": Fernet(key1),
            "v2": Fernet(key2)
        }
        
        # Test data
        test_data = {
            "string": "test value",
            "number": 42,
            "float": 3.14,
            "list": [1, 2, 3],
            "nested": {
                "key": "value",
                "deep": {
                    "level": 3
                }
            }
        }
        
        # Test with both key versions
        for key_id, cipher in ciphers.items():
            encrypted = await encrypt_value(test_data, cipher, key_id)
            decrypted = await decrypt_value(encrypted, ciphers)
            assert decrypted == test_data
    
    @pytest.mark.asyncio
    async def test_key_rotation_scenario(self):
        """Test a key rotation scenario."""
        # Start with v1 key
        key_v1 = Fernet.generate_key()
        ArbiterConfig.ENCRYPTION_KEYS = {"v1": Fernet(key_v1)}
        
        # Encrypt data with v1
        data = {"sensitive": "information"}
        encrypted_v1 = await encrypt_value(
            data,
            ArbiterConfig.ENCRYPTION_KEYS["v1"],
            "v1"
        )
        
        # Rotate to v2
        await ArbiterConfig.rotate_keys("v2")
        
        # Should still be able to decrypt v1 data
        decrypted = await decrypt_value(encrypted_v1, ArbiterConfig.ENCRYPTION_KEYS)
        assert decrypted == data
        
        # New data should use v2
        encrypted_v2 = await encrypt_value(
            data,
            ArbiterConfig.ENCRYPTION_KEYS["v2"],
            "v2"
        )
        assert encrypted_v2.startswith(b"v2:")
        
        # Both should decrypt correctly
        assert await decrypt_value(encrypted_v1, ArbiterConfig.ENCRYPTION_KEYS) == data
        assert await decrypt_value(encrypted_v2, ArbiterConfig.ENCRYPTION_KEYS) == data


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=arbiter.learner.encryption"])