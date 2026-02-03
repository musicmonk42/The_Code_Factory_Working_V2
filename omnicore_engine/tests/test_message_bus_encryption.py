# test_encryption.py

# Note: This test file uses context managers (with patch(...)) instead of
# @patch decorators to ensure compatibility with pytest-xdist parallel execution.
# Decorators cause issues when pytest-xdist forks worker processes because mocked
# modules cannot be properly serialized across process boundaries.

import base64
import time

import pytest
from cryptography.fernet import Fernet, InvalidToken

from omnicore_engine.message_bus.encryption import FernetEncryption


class TestFernetEncryption:
    """Test suite for FernetEncryption class."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Set up test fixtures before each test."""
        # Generate test keys
        self.key1 = Fernet.generate_key()
        self.key2 = Fernet.generate_key()
        self.key3 = Fernet.generate_key()

        # Create encryption instance with single key
        self.encryption_single = FernetEncryption([self.key1])

        # Create encryption instance with multiple keys for rotation
        self.encryption_multi = FernetEncryption([self.key1, self.key2, self.key3])

    def test_initialization_single_key(self):
        """Test initialization with a single key."""
        encryption = FernetEncryption([self.key1])
        assert encryption.multi_fernet is not None

        # Test encryption/decryption works
        data = b"test data"
        encrypted = encryption.encrypt(data)
        decrypted = encryption.decrypt(encrypted)
        assert data == decrypted

    def test_initialization_multiple_keys(self):
        """Test initialization with multiple keys for rotation."""
        encryption = FernetEncryption([self.key1, self.key2, self.key3])
        assert encryption.multi_fernet is not None

        # Test encryption/decryption works
        data = b"test data with multiple keys"
        encrypted = encryption.encrypt(data)
        decrypted = encryption.decrypt(encrypted)
        assert data == decrypted

    def test_initialization_empty_keys(self):
        """Test initialization with empty key list."""
        with pytest.raises(ValueError) as context:
            FernetEncryption([])
        assert "At least one encryption key is required" in str(context.value)

    def test_initialization_none_key(self):
        """Test initialization with None in key list."""
        with pytest.raises(ValueError) as context:
            FernetEncryption([self.key1, None, self.key2])
        assert "none can be empty" in str(context.value)

    def test_initialization_empty_string_key(self):
        """Test initialization with empty string key."""
        with pytest.raises(ValueError) as context:
            FernetEncryption([self.key1, b"", self.key2])
        assert "none can be empty" in str(context.value)

    def test_initialization_invalid_key_format(self):
        """Test initialization with invalid key format."""
        with pytest.raises(Exception):  # Fernet will raise an exception
            FernetEncryption([b"invalid_key_format"])

    def test_encrypt_basic(self):
        """Test basic encryption."""
        data = b"Hello, World!"
        encrypted = self.encryption_single.encrypt(data)

        # Encrypted data should be different from original
        assert data != encrypted

        # Encrypted data should be bytes
        assert isinstance(encrypted, bytes)

        # Should be able to decrypt back
        decrypted = self.encryption_single.decrypt(encrypted)
        assert data == decrypted

    def test_encrypt_empty_data(self):
        """Test encryption of empty data."""
        data = b""
        encrypted = self.encryption_single.encrypt(data)
        decrypted = self.encryption_single.decrypt(encrypted)
        assert data == decrypted

    def test_encrypt_large_data(self):
        """Test encryption of large data."""
        # Create 1MB of data
        data = b"x" * (1024 * 1024)
        encrypted = self.encryption_single.encrypt(data)
        decrypted = self.encryption_single.decrypt(encrypted)
        assert data == decrypted

    def test_encrypt_various_data_types(self):
        """Test encryption with various byte patterns."""
        test_cases = [
            b"ASCII text",
            "UTF-8 text with émojis 🔐".encode("utf-8"),
            bytes(range(256)),  # All byte values
            b"\x00\x01\x02\x03",  # Binary data
            b"Line 1\nLine 2\rLine 3\r\n",  # Different line endings
        ]

        for data in test_cases:
            encrypted = self.encryption_single.encrypt(data)
            decrypted = self.encryption_single.decrypt(encrypted)
            assert data == decrypted, f"Failed for data: {data[:20]}..."

    def test_decrypt_with_wrong_key(self):
        """Test decryption with wrong key."""
        data = b"Secret data"
        encrypted = self.encryption_single.encrypt(data)

        # Try to decrypt with different key
        wrong_key = Fernet.generate_key()
        wrong_encryption = FernetEncryption([wrong_key])

        with pytest.raises(InvalidToken):
            wrong_encryption.decrypt(encrypted)

    def test_decrypt_invalid_data(self):
        """Test decryption of invalid data."""
        invalid_data = b"This is not encrypted data"

        with pytest.raises(InvalidToken):
            self.encryption_single.decrypt(invalid_data)

    def test_key_rotation_encrypt_with_new_decrypt_with_old(self):
        """Test key rotation: encrypt with new key, decrypt with old keys."""
        data = b"Rotation test data"

        # Encrypt with the first (newest) key
        encrypted = self.encryption_multi.encrypt(data)

        # Create new encryption with same keys in different order
        # MultiFernet tries keys in order until one works
        encryption_rotated = FernetEncryption([self.key2, self.key3, self.key1])

        # Should still be able to decrypt
        decrypted = encryption_rotated.decrypt(encrypted)
        assert data == decrypted

    def test_key_rotation_old_encrypted_data(self):
        """Test decrypting data encrypted with old keys."""
        data = b"Old encrypted data"

        # Encrypt with only the old key
        old_encryption = FernetEncryption([self.key3])
        encrypted = old_encryption.encrypt(data)

        # Create new encryption with new keys added
        new_encryption = FernetEncryption([self.key1, self.key2, self.key3])

        # Should still decrypt data encrypted with old key
        decrypted = new_encryption.decrypt(encrypted)
        assert data == decrypted

    def test_multiple_encryption_decryption_cycles(self):
        """Test multiple encryption/decryption cycles."""
        data = b"Cycle test data"

        # Perform multiple cycles
        for i in range(10):
            encrypted = self.encryption_single.encrypt(data)
            decrypted = self.encryption_single.decrypt(encrypted)
            assert data == decrypted, f"Failed at cycle {i}"

    def test_concurrent_encryption(self):
        """Test thread safety of encryption operations."""
        import concurrent.futures

        data_samples = [f"Thread {i} data".encode() for i in range(100)]
        results = {}
        errors = []

        def encrypt_decrypt(thread_id, data):
            try:
                encrypted = self.encryption_multi.encrypt(data)
                decrypted = self.encryption_multi.decrypt(encrypted)
                results[thread_id] = (data, decrypted)
            except Exception as e:
                errors.append((thread_id, str(e)))

        # Run concurrent operations
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(encrypt_decrypt, i, data)
                for i, data in enumerate(data_samples)
            ]
            concurrent.futures.wait(futures)

        # Verify no errors occurred
        assert len(errors) == 0, f"Errors occurred: {errors}"

        # Verify all operations succeeded
        for thread_id, (original, decrypted) in results.items():
            assert original == decrypted

    def test_encryption_determinism(self):
        """Test that encryption is non-deterministic (different each time)."""
        data = b"Test determinism"

        # Encrypt same data multiple times
        encrypted1 = self.encryption_single.encrypt(data)
        encrypted2 = self.encryption_single.encrypt(data)
        encrypted3 = self.encryption_single.encrypt(data)

        # Encrypted values should be different (Fernet includes timestamp)
        assert encrypted1 != encrypted2
        assert encrypted2 != encrypted3
        assert encrypted1 != encrypted3

        # But all should decrypt to same value
        assert self.encryption_single.decrypt(encrypted1) == data
        assert self.encryption_single.decrypt(encrypted2) == data
        assert self.encryption_single.decrypt(encrypted3) == data

    def test_key_generation(self):
        """Test generating valid Fernet keys."""
        # Generate multiple keys
        keys = [Fernet.generate_key() for _ in range(5)]

        # All keys should be valid
        for key in keys:
            assert isinstance(key, bytes)
            assert len(key) == 44  # Fernet keys are 44 bytes (base64)

            # Should be valid base64
            try:
                base64.urlsafe_b64decode(key)
            except Exception:
                pytest.fail(f"Invalid base64 key: {key}")

            # Should work for encryption
            encryption = FernetEncryption([key])
            test_data = b"test"
            encrypted = encryption.encrypt(test_data)
            decrypted = encryption.decrypt(encrypted)
            assert test_data == decrypted

    def test_protocol_implementation(self):
        """Test that FernetEncryption implements EncryptionStrategy protocol."""
        # Check that required methods exist
        assert hasattr(self.encryption_single, "encrypt")
        assert hasattr(self.encryption_single, "decrypt")

        # Check method signatures
        assert callable(self.encryption_single.encrypt)
        assert callable(self.encryption_single.decrypt)

    def test_encryption_with_time_delay(self):
        """Test that old encrypted data can still be decrypted."""
        data = b"Time-sensitive data"
        encrypted = self.encryption_single.encrypt(data)

        # Simulate time passing (Fernet includes timestamp)
        time.sleep(0.1)

        # Should still decrypt successfully
        decrypted = self.encryption_single.decrypt(encrypted)
        assert data == decrypted

    def test_key_rotation_remove_old_key(self):
        """Test removing old keys from rotation."""
        data = b"Key removal test"

        # Encrypt with all keys available
        encrypted = self.encryption_multi.encrypt(data)

        # Create new instance without the first key
        # This simulates removing the newest key
        reduced_encryption = FernetEncryption([self.key2, self.key3])

        # Should fail to decrypt since it was encrypted with key1
        with pytest.raises(InvalidToken):
            reduced_encryption.decrypt(encrypted)

    def test_edge_case_single_byte(self):
        """Test encryption of single byte."""
        data = b"a"
        encrypted = self.encryption_single.encrypt(data)
        decrypted = self.encryption_single.decrypt(encrypted)
        assert data == decrypted

    def test_edge_case_max_fernet_message(self):
        """Test encryption near Fernet's practical limits."""
        # Fernet can handle large messages, but let's test a reasonable size
        data = b"x" * (10 * 1024 * 1024)  # 10MB
        encrypted = self.encryption_single.encrypt(data)
        decrypted = self.encryption_single.decrypt(encrypted)
        assert data == decrypted


class TestEncryptionIntegration:
    """Integration tests for encryption in message bus context."""

    def test_message_payload_encryption(self):
        """Test encrypting message payloads."""
        import json

        key = Fernet.generate_key()
        encryption = FernetEncryption([key])

        # Simulate message payload
        payload = {"user_id": "12345", "action": "update", "data": {"field": "value"}}

        # Convert to JSON bytes
        payload_bytes = json.dumps(payload).encode("utf-8")

        # Encrypt
        encrypted = encryption.encrypt(payload_bytes)

        # Decrypt
        decrypted = encryption.decrypt(encrypted)

        # Parse back to dict
        decrypted_payload = json.loads(decrypted.decode("utf-8"))

        assert payload == decrypted_payload

    def test_key_rotation_migration(self):
        """Test migrating from old to new encryption keys."""
        old_key = Fernet.generate_key()
        new_key = Fernet.generate_key()

        # Start with old key only
        old_encryption = FernetEncryption([old_key])

        # Encrypt some data with old key
        data_items = [
            b"Data item 1",
            b"Data item 2",
            b"Data item 3",
        ]

        encrypted_items = [old_encryption.encrypt(item) for item in data_items]

        # Migrate to new key (keep old for decryption)
        migrated_encryption = FernetEncryption([new_key, old_key])

        # Should be able to decrypt old data
        for i, encrypted in enumerate(encrypted_items):
            decrypted = migrated_encryption.decrypt(encrypted)
            assert data_items[i] == decrypted

        # New encryptions use the new key
        new_data = b"New data"
        new_encrypted = migrated_encryption.encrypt(new_data)

        # Old encryption (without new key) can't decrypt new data
        with pytest.raises(InvalidToken):
            old_encryption.decrypt(new_encrypted)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
