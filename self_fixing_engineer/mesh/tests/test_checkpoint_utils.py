"""
Test suite for checkpoint_utils.py - Cryptographic and utility functions.

Tests cover:
- Cryptographic operations (hashing, encryption, signing)
- Data compression and serialization
- Data scrubbing and privacy
- Performance characteristics
- Security compliance
"""

import json
import os
from datetime import datetime, timedelta

import pytest
from cryptography.fernet import Fernet

# Test configuration
TEST_KEYS = [Fernet.generate_key().decode() for _ in range(3)]
TEST_HMAC_KEY = os.urandom(32).hex()

# Configure environment before imports
TEST_ENV = {
    "CHECKPOINT_ENCRYPTION_KEYS": ",".join(TEST_KEYS[:2]),
    "CHECKPOINT_HMAC_KEY": TEST_HMAC_KEY,
    "PROD_MODE": "false",
    "FIPS_MODE": "false",
    "ENV": "test",
    "TENANT": "test_tenant",
    "DATA_CLASSIFICATION": "CONFIDENTIAL",
}

for key, value in TEST_ENV.items():
    os.environ[key] = value


# ---- Test Data ----

class TestData:
    """Standard test data for consistency."""
    
    SIMPLE_DICT = {"key": "value", "number": 42}
    
    NESTED_DICT = {
        "level1": {
            "level2": {
                "level3": "deep_value"
            },
            "array": [1, 2, 3]
        }
    }
    
    SENSITIVE_DATA = {
        "user_id": "12345",
        "password": "super_secret_123",
        "api_key": "sk-1234567890abcdef",
        "token": "bearer_xyz789",
        "credit_card": "4111-1111-1111-1111",
        "ssn": "123-45-6789",
        "email": "test@example.com",
        "safe_field": "public_data",
        "nested": {
            "secret": "hidden_value",
            "public": "visible_value"
        }
    }
    
    LARGE_DATA = {
        "repeated": "x" * 10000,
        "numbers": list(range(1000)),
        "nested": {f"key_{i}": f"value_{i}" * 10 for i in range(100)}
    }


# ---- Fixtures ----

@pytest.fixture
def crypto_provider():
    """Get CryptoProvider instance."""
    from mesh.checkpoint.checkpoint_utils import CryptoProvider
    return CryptoProvider()


@pytest.fixture
def test_payload():
    """Standard test payload."""
    return json.dumps(TestData.SIMPLE_DICT).encode()


# ---- Cryptographic Tests ----

class TestCryptography:
    """Test cryptographic operations."""
    
    def test_key_generation(self, crypto_provider):
        """Test secure key generation."""
        key1 = crypto_provider.generate_key(32)
        key2 = crypto_provider.generate_key(32)
        
        assert len(key1) == 32
        assert len(key2) == 32
        assert key1 != key2  # Should be random
        
        # Test entropy quality
        assert len(set(key1)) > 8  # Should have good entropy
    
    def test_key_derivation(self, crypto_provider):
        """Test key derivation from password."""
        password = "test_password"
        salt = crypto_provider.generate_key(16)
        
        key1 = crypto_provider.derive_key(password, salt)
        key2 = crypto_provider.derive_key(password, salt)
        
        assert key1 == key2  # Same inputs = same output
        assert len(key1) == 32
        
        # Different salt = different key
        key3 = crypto_provider.derive_key(password, crypto_provider.generate_key(16))
        assert key1 != key3
    
    def test_aes_gcm_encryption(self, crypto_provider):
        """Test AES-GCM encryption/decryption."""
        plaintext = b"sensitive data to encrypt"
        key = crypto_provider.generate_key(32)
        
        # Encrypt
        ciphertext, nonce, tag = crypto_provider.encrypt_aes_gcm(plaintext, key)
        
        assert ciphertext != plaintext
        assert len(nonce) == 12  # 96-bit nonce
        assert len(tag) == 16  # 128-bit tag
        
        # Decrypt
        decrypted = crypto_provider.decrypt_aes_gcm(ciphertext, key, nonce, tag)
        assert decrypted == plaintext
        
        # Tamper detection
        with pytest.raises(Exception):
            crypto_provider.decrypt_aes_gcm(ciphertext + b"x", key, nonce, tag)
    
    def test_secure_compare(self, crypto_provider):
        """Test constant-time comparison."""
        a = b"test_value"
        b = b"test_value"
        c = b"different"
        
        assert crypto_provider.secure_compare(a, b)
        assert not crypto_provider.secure_compare(a, c)


# ---- Hashing Tests ----

class TestHashing:
    """Test hashing functions."""
    
    def test_hash_data_consistency(self):
        """Test hash consistency."""
        from mesh.checkpoint.checkpoint_utils import hash_data
        
        data = TestData.SIMPLE_DICT
        hash1 = hash_data(data)
        hash2 = hash_data(data)
        
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex
    
    def test_hash_dict_with_chaining(self):
        """Test hash chaining."""
        from mesh.checkpoint.checkpoint_utils import hash_dict
        
        data = TestData.SIMPLE_DICT
        hash1 = hash_dict(data)
        hash2 = hash_dict(data, prev_hash="previous_hash")
        
        assert hash1 != hash2
        assert len(hash2) == 64
    
    def test_hmac_signing(self):
        """Test HMAC signing and verification."""
        from mesh.checkpoint.checkpoint_utils import compute_hmac, verify_hmac
        
        data = b"test payload"
        key = os.urandom(32)
        
        signature = compute_hmac(data, key)
        assert len(signature) == 64  # SHA256 HMAC hex
        
        assert verify_hmac(data, key, signature)
        assert not verify_hmac(b"tampered", key, signature)
    
    @pytest.mark.parametrize("algorithm", ["SHA256", "SHA512", "SHA3-256", "BLAKE2B"])
    def test_multiple_hash_algorithms(self, algorithm):
        """Test different hash algorithms."""
        from mesh.checkpoint.checkpoint_utils import hash_data
        
        data = "test data"
        hash_val = hash_data(data, algorithm=algorithm)
        
        assert hash_val is not None
        assert len(hash_val) > 0


# ---- Compression Tests ----

class TestCompression:
    """Test compression functions."""
    
    def test_compress_decompress_roundtrip(self):
        """Test compression roundtrip."""
        from mesh.checkpoint.checkpoint_utils import compress_data, decompress_data
        
        data = json.dumps(TestData.LARGE_DATA).encode()
        
        compressed = compress_data(data)
        assert len(compressed) < len(data)
        
        decompressed = decompress_data(compressed)
        assert decompressed == data
    
    def test_compress_json(self):
        """Test JSON compression."""
        from mesh.checkpoint.checkpoint_utils import compress_json, decompress_json
        
        data = TestData.LARGE_DATA
        
        compressed = compress_json(data)
        assert isinstance(compressed, bytes)
        
        decompressed = decompress_json(compressed)
        assert decompressed == data
    
    @pytest.mark.parametrize("algorithm", ["GZIP", "ZLIB", "BZ2", "LZMA"])
    def test_compression_algorithms(self, algorithm):
        """Test different compression algorithms."""
        from mesh.checkpoint.checkpoint_utils import compress_data, decompress_data
        
        data = b"x" * 1000  # Highly compressible
        
        compressed = compress_data(data, algorithm=algorithm)
        assert len(compressed) < len(data)
        
        decompressed = decompress_data(compressed, algorithm=algorithm)
        assert decompressed == data
    
    def test_auto_detect_compression(self):
        """Test automatic compression detection."""
        from mesh.checkpoint.checkpoint_utils import compress_data, decompress_data
        
        data = b"test data"
        
        # Try different algorithms
        for algo in ["GZIP", "ZLIB", "BZ2"]:
            compressed = compress_data(data, algorithm=algo)
            # Auto-detect without specifying algorithm
            decompressed = decompress_data(compressed)
            assert decompressed == data


# ---- Data Scrubbing Tests ----

class TestDataScrubbing:
    """Test data privacy and scrubbing."""
    
    def test_scrub_sensitive_fields(self):
        """Test scrubbing of sensitive field names."""
        from mesh.checkpoint.checkpoint_utils import scrub_data
        
        scrubbed = scrub_data(TestData.SENSITIVE_DATA)
        
        # Sensitive fields should be redacted
        assert scrubbed["password"] == "[REDACTED]"
        assert scrubbed["api_key"] == "[REDACTED]"
        assert scrubbed["token"] == "[REDACTED]"
        assert scrubbed["credit_card"] == "[REDACTED]"
        assert scrubbed["ssn"] == "[REDACTED]"
        assert scrubbed["nested"]["secret"] == "[REDACTED]"
        
        # Safe fields preserved
        assert scrubbed["user_id"] == "12345"
        assert scrubbed["safe_field"] == "public_data"
        assert scrubbed["nested"]["public"] == "visible_value"
    
    def test_scrub_patterns(self):
        """Test pattern-based scrubbing."""
        from mesh.checkpoint.checkpoint_utils import scrub_data
        
        data = {
            "text": "My SSN is 123-45-6789 and card is 4111-1111-1111-1111",
            "email": "user@example.com",
            "phone": "555-123-4567"
        }
        
        scrubbed = scrub_data(data)
        
        # Patterns should be detected and scrubbed
        assert "123-45-6789" not in scrubbed["text"]
        assert "4111-1111-1111-1111" not in scrubbed["text"]
        assert "[REDACTED]" in scrubbed["text"]
    
    def test_anonymize_data(self):
        """Test data anonymization."""
        from mesh.checkpoint.checkpoint_utils import anonymize_data
        
        data = {
            "user": {
                "name": "John Doe",
                "email": "john@example.com",
                "age": 30
            }
        }
        
        # Hash method
        anonymized = anonymize_data(data, ["user.name", "user.email"], method="hash")
        assert anonymized["user"]["name"] != "John Doe"
        assert len(anonymized["user"]["name"]) == 16  # Truncated hash
        
        # Generalize method
        anonymized = anonymize_data(data, ["user.age"], method="generalize")
        assert anonymized["user"]["age"] == 30  # Rounded


# ---- Data Comparison Tests ----

class TestDataComparison:
    """Test data comparison and diffing."""
    
    def test_deep_diff_additions(self):
        """Test deep diff detecting additions."""
        from mesh.checkpoint.checkpoint_utils import deep_diff
        
        old = {"a": 1}
        new = {"a": 1, "b": 2}
        
        diff = deep_diff(old, new)
        assert "b" in diff["added"]
        assert diff["added"]["b"] == 2
    
    def test_deep_diff_modifications(self):
        """Test deep diff detecting modifications."""
        from mesh.checkpoint.checkpoint_utils import deep_diff
        
        old = {"a": 1, "b": {"c": 3}}
        new = {"a": 2, "b": {"c": 4}}
        
        diff = deep_diff(old, new)
        assert "a" in diff["modified"]
        assert diff["modified"]["a"]["old"] == 1
        assert diff["modified"]["a"]["new"] == 2
    
    def test_deep_diff_type_changes(self):
        """Test deep diff detecting type changes."""
        from mesh.checkpoint.checkpoint_utils import deep_diff
        
        old = {"a": 1}
        new = {"a": "1"}
        
        diff = deep_diff(old, new, track_type_changes=True)
        assert "a" in diff["type_changed"]
        assert diff["type_changed"]["a"]["old_type"] == "int"
        assert diff["type_changed"]["a"]["new_type"] == "str"


# ---- Key Rotation Tests ----

class TestKeyRotation:
    """Test encryption key rotation."""
    
    def test_create_fernet_key(self):
        """Test Fernet key creation."""
        from mesh.checkpoint.checkpoint_utils import create_fernet_key
        
        # Random key
        key1 = create_fernet_key()
        key2 = create_fernet_key()
        assert key1 != key2
        assert len(key1) == 44  # Base64 encoded
        
        # Derived key
        create_fernet_key("passphrase")
        create_fernet_key("passphrase")
        # Note: Will be different due to random salt
    
    def test_rotate_keys(self):
        """Test key rotation."""
        from mesh.checkpoint.checkpoint_utils import rotate_fernet_keys
        
        current_keys = [Fernet.generate_key() for _ in range(2)]
        new_key = Fernet.generate_key()
        
        multi_fernet, rotated_keys = rotate_fernet_keys(current_keys, new_key)
        
        assert len(rotated_keys) >= len(current_keys)
        assert rotated_keys[0] == new_key  # New key is primary
        
        # Test encryption with rotated keys
        plaintext = b"test data"
        encrypted = multi_fernet.encrypt(plaintext)
        decrypted = multi_fernet.decrypt(encrypted)
        assert decrypted == plaintext


# ---- Validation Tests ----

class TestValidation:
    """Test data validation."""
    
    def test_validate_checkpoint_data(self):
        """Test checkpoint data validation."""
        from mesh.checkpoint.checkpoint_utils import validate_checkpoint_data
        
        valid_data = {
            "state": {"key": "value"},
            "metadata": {
                "timestamp": datetime.now().isoformat(),
                "hash": "test_hash"
            }
        }
        
        assert validate_checkpoint_data(valid_data)
        
        # Missing required field
        invalid_data = {"metadata": {}}
        with pytest.raises(ValueError, match="Missing required field"):
            validate_checkpoint_data(invalid_data)
        
        # Size limit
        large_data = {"state": "x" * 10000, "metadata": {}}
        with pytest.raises(ValueError, match="exceeds limit"):
            validate_checkpoint_data(large_data, max_size=1000)


# ---- Utility Tests ----

class TestUtilities:
    """Test utility functions."""
    
    def test_generate_checkpoint_id(self):
        """Test checkpoint ID generation."""
        from mesh.checkpoint.checkpoint_utils import generate_checkpoint_id
        
        id1 = generate_checkpoint_id()
        id2 = generate_checkpoint_id()
        
        assert id1 != id2
        assert "_" in id1  # Format: timestamp_uuid
    
    def test_format_size(self):
        """Test size formatting."""
        from mesh.checkpoint.checkpoint_utils import format_size
        
        assert format_size(100) == "100.00 B"
        assert format_size(1024) == "1.00 KB"
        assert format_size(1024 * 1024) == "1.00 MB"
        assert format_size(1024 ** 3) == "1.00 GB"
    
    def test_parse_duration(self):
        """Test duration parsing."""
        from mesh.checkpoint.checkpoint_utils import parse_duration
        
        assert parse_duration("30s") == timedelta(seconds=30)
        assert parse_duration("5m") == timedelta(minutes=5)
        assert parse_duration("2h") == timedelta(hours=2)
        assert parse_duration("1d") == timedelta(days=1)
        assert parse_duration("1w") == timedelta(weeks=1)
        
        with pytest.raises(ValueError):
            parse_duration("invalid")
    
    def test_valid_identifier(self):
        """Test identifier validation."""
        from mesh.checkpoint.checkpoint_utils import is_valid_identifier
        
        assert is_valid_identifier("valid_name-123.test")
        assert is_valid_identifier("checkpoint_v1")
        assert not is_valid_identifier("invalid name")  # Space
        assert not is_valid_identifier("invalid@name")  # Special char


# ---- Performance Tests ----

class TestPerformance:
    """Test performance characteristics."""
    
    def test_hash_performance(self, benchmark):
        """Benchmark hashing performance."""
        from mesh.checkpoint.checkpoint_utils import hash_data
        
        data = TestData.LARGE_DATA
        result = benchmark(hash_data, data)
        assert result is not None
    
    def test_compression_performance(self, benchmark):
        """Benchmark compression performance."""
        from mesh.checkpoint.checkpoint_utils import compress_json
        
        data = TestData.LARGE_DATA
        result = benchmark(compress_json, data)
        assert isinstance(result, bytes)
    
    def test_scrubbing_performance(self, benchmark):
        """Benchmark data scrubbing performance."""
        from mesh.checkpoint.checkpoint_utils import scrub_data
        
        # Large data with many sensitive fields
        large_sensitive = {
            f"password_{i}": f"secret_{i}"
            for i in range(100)
        }
        
        result = benchmark(scrub_data, large_sensitive)
        assert all(v == "[REDACTED]" for k, v in result.items() if "password" in k)


# ---- Security Compliance Tests ----

class TestSecurityCompliance:
    """Test security compliance features."""
    
    def test_fips_mode(self):
        """Test FIPS mode compliance."""
        os.environ["FIPS_MODE"] = "true"
        
        from mesh.checkpoint.checkpoint_utils import SecurityConfig
        
        # Should only allow FIPS-approved algorithms
        assert SecurityConfig.ENCRYPTION_ALGORITHM in ["AES-256-GCM", "AES-256-CBC"]
        assert SecurityConfig.HASH_ALGORITHM in ["SHA256", "SHA384", "SHA512", "SHA3-256", "SHA3-384", "SHA3-512"]
        
        os.environ["FIPS_MODE"] = "false"
    
    def test_secure_deletion(self):
        """Test secure memory erasure."""
        from mesh.checkpoint.checkpoint_utils import CryptoProvider
        
        crypto = CryptoProvider()
        
        # Create sensitive data
        sensitive = bytearray(b"sensitive_data")
        original = bytes(sensitive)
        
        # Secure erase
        crypto.secure_erase(sensitive)
        
        # Data should be overwritten
        assert bytes(sensitive) != original
        assert all(b == 0 for b in sensitive)  # Final overwrite with zeros


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])