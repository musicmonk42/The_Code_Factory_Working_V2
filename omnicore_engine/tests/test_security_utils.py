"""
Test suite for omnicore_engine/security_utils.py
Tests enterprise security utilities including crypto, auth, and validation.
"""

import base64
import json
import os

# Add the parent directory to path for imports
import sys
import time
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnicore_engine.security_utils import (
    AuthenticationError,
    AuthorizationError,
    EncryptionAlgorithm,
    EncryptionError,
    EnterpriseSecurityUtils,
    HashAlgorithm,
    RateLimiter,
    RateLimitError,
    SecureSessionManager,
    SecurityAuditLogger,
    SecurityException,
    ValidationError,
    get_security_utils,
    require_authentication,
    require_authorization,
)


class TestExceptions:
    """Test custom exception classes"""

    def test_exception_hierarchy(self):
        """Test exception inheritance"""
        assert issubclass(AuthenticationError, SecurityException)
        assert issubclass(AuthorizationError, SecurityException)
        assert issubclass(EncryptionError, SecurityException)
        assert issubclass(ValidationError, SecurityException)
        assert issubclass(RateLimitError, SecurityException)

    def test_exception_messages(self):
        """Test exception message handling"""
        exc = AuthenticationError("Test authentication error")
        assert str(exc) == "Test authentication error"


class TestEnums:
    """Test enum definitions"""

    def test_hash_algorithms(self):
        """Test HashAlgorithm enum values"""
        assert HashAlgorithm.SHA256 == "sha256"
        assert HashAlgorithm.SHA512 == "sha512"
        assert HashAlgorithm.SHA3_512 == "sha3_512"
        assert HashAlgorithm.BLAKE2B == "blake2b"

    def test_encryption_algorithms(self):
        """Test EncryptionAlgorithm enum values"""
        assert EncryptionAlgorithm.AES_256_GCM == "aes_256_gcm"
        assert EncryptionAlgorithm.CHACHA20_POLY1305 == "chacha20_poly1305"
        assert EncryptionAlgorithm.RSA_4096 == "rsa_4096"


class TestEnterpriseSecurityUtils:
    """Test EnterpriseSecurityUtils class"""

    @pytest.fixture
    def security_utils(self):
        """Create security utils instance"""
        config = {
            "encryption_keys": [b"test_key_32_bytes_long_for_fernet"],
            "allowed_mime_types": ["application/pdf", "text/plain"],
        }
        with patch("omnicore_engine.security_utils.Fernet") as mock_fernet:
            mock_fernet.generate_key.return_value = b"test_key_32_bytes_long_for_fernet"
            utils = EnterpriseSecurityUtils(config)
            return utils

    def test_initialization(self, security_utils):
        """Test security utils initialization"""
        assert security_utils.config is not None
        assert security_utils.password_hasher is not None
        assert security_utils._rate_limiter is not None
        assert security_utils._session_manager is not None
        assert security_utils._audit_logger is not None

    def test_hash_password(self, security_utils):
        """Test password hashing"""
        with patch.object(security_utils, "check_password_strength") as mock_check:
            mock_check.return_value = {"score": 4, "feedback": []}

            password = "Test@Password123"
            hashed = security_utils.hash_password(password)

            assert hashed is not None
            assert hashed != password
            assert "$argon2" in hashed  # Argon2 hash format

    def test_hash_password_weak(self, security_utils):
        """Test weak password rejection"""
        with patch.object(security_utils, "check_password_strength") as mock_check:
            mock_check.return_value = {"score": 2, "feedback": ["Too weak"]}

            with pytest.raises(ValidationError, match="Password too weak"):
                security_utils.hash_password("weak")

    def test_verify_password(self, security_utils):
        """Test password verification"""
        password = "Test@Password123"

        # Mock the hasher
        security_utils.password_hasher.verify = Mock()
        security_utils.password_hasher.check_needs_rehash = Mock(return_value=False)

        is_valid, needs_rehash = security_utils.verify_password(password, "hashed")

        assert is_valid == True
        assert needs_rehash == False
        security_utils.password_hasher.verify.assert_called_once()

    def test_check_password_strength(self, security_utils):
        """Test password strength checking"""
        # Strong password
        result = security_utils.check_password_strength("MyStr0ng!P@ssw0rd123")
        assert "score" in result
        assert "entropy" in result
        assert "feedback" in result

        # Weak password - too short
        result = security_utils.check_password_strength("Pass123!")
        assert len(result["feedback"]) > 0
        assert any("14 characters" in str(f) for f in result["feedback"])

        # Missing uppercase
        result = security_utils.check_password_strength("mypassword123456!")
        assert any("uppercase" in str(f) for f in result["feedback"])

        # Repeated characters
        result = security_utils.check_password_strength("Passssword123456!")
        assert any("repeated" in str(f) for f in result["feedback"])

    def test_encrypt_decrypt_data(self, security_utils):
        """Test data encryption and decryption"""
        # Mock Fernet cipher
        mock_cipher = Mock()
        mock_cipher.encrypt.return_value = b"encrypted_data"
        mock_cipher.decrypt.return_value = b"test data"
        security_utils.cipher = mock_cipher

        # Test encryption
        encrypted = security_utils.encrypt_data("test data")
        assert encrypted is not None
        mock_cipher.encrypt.assert_called_once()

        # Test decryption
        decrypted = security_utils.decrypt_data(
            base64.urlsafe_b64encode(b"encrypted_data").decode()
        )
        assert decrypted == b"test data"
        mock_cipher.decrypt.assert_called_once()

    def test_encrypt_decrypt_with_context(self, security_utils):
        """Test encryption with context binding"""
        mock_cipher = Mock()
        security_utils.cipher = mock_cipher

        # Encrypt with context
        mock_cipher.encrypt.return_value = b"encrypted"
        encrypted = security_utils.encrypt_data("data", context="user_data")

        # Verify context was added
        call_args = mock_cipher.encrypt.call_args[0][0]
        assert b"user_data::data" in call_args

    def test_generate_secure_token(self, security_utils):
        """Test secure token generation"""
        token = security_utils.generate_secure_token(32)
        assert len(token) > 0

        # Test with prefix
        token = security_utils.generate_secure_token(32, prefix="api")
        assert token.startswith("api_")

    def test_generate_api_key(self, security_utils):
        """Test API key generation"""
        result = security_utils.generate_api_key("user123", ["read", "write"])

        assert "api_key" in result
        assert "key_id" in result
        assert "metadata" in result
        assert result["metadata"]["uid"] == "user123"
        assert result["metadata"]["scopes"] == ["read", "write"]

    def test_verify_api_key(self, security_utils):
        """Test API key verification"""
        api_key = "key123.secret456.signature789"
        result = security_utils.verify_api_key(api_key)

        assert result is not None
        assert result["key_id"] == "key123"

    def test_generate_totp_secret(self, security_utils):
        """Test TOTP secret generation"""
        result = security_utils.generate_totp_secret("user123", "TestApp")

        assert "secret" in result
        assert "uri" in result
        assert "backup_codes" in result
        assert len(result["backup_codes"]) == 10
        assert "otpauth://" in result["uri"]

    @patch("omnicore_engine.security_utils.pyotp")
    def test_verify_totp(self, mock_pyotp, security_utils):
        """Test TOTP verification"""
        mock_totp = Mock()
        mock_totp.verify.return_value = True
        mock_pyotp.TOTP.return_value = mock_totp

        result = security_utils.verify_totp("123456", "secret")

        assert result == True
        mock_totp.verify.assert_called_once_with("123456", valid_window=1)

    def test_sanitize_html(self, security_utils):
        """Test HTML sanitization"""
        # Test XSS prevention
        dirty_html = '<script>alert("xss")</script><p>Hello</p>'
        clean_html = security_utils.sanitize_html(dirty_html)

        assert "<script>" not in clean_html
        assert "<p>Hello</p>" in clean_html

        # Test allowed tags
        html = "<strong>Bold</strong> <em>Italic</em>"
        result = security_utils.sanitize_html(html)
        assert "<strong>" in result
        assert "<em>" in result

    def test_sanitize_filename(self, security_utils):
        """Test filename sanitization"""
        # Test path traversal prevention
        filename = "../../../etc/passwd"
        sanitized = security_utils.sanitize_filename(filename)
        assert ".." not in sanitized
        assert "/" not in sanitized

        # Test special character removal
        filename = "test@file#name$.txt"
        sanitized = security_utils.sanitize_filename(filename)
        assert "@" not in sanitized
        assert "#" not in sanitized

        # Test length limiting
        long_name = "a" * 150 + ".txt"
        sanitized = security_utils.sanitize_filename(long_name)
        assert len(sanitized) <= 104  # 100 + ".txt"

    def test_validate_email(self, security_utils):
        """Test email validation"""
        # Valid emails
        assert security_utils.validate_email("user@example.com") == True
        assert security_utils.validate_email("user.name+tag@example.co.uk") == True

        # Invalid emails
        assert security_utils.validate_email("invalid") == False
        assert security_utils.validate_email("@example.com") == False
        assert security_utils.validate_email("user@") == False
        assert security_utils.validate_email("user@.com") == False

        # Length limits
        long_local = "a" * 65 + "@example.com"
        assert security_utils.validate_email(long_local) == False

    def test_validate_url(self, security_utils):
        """Test URL validation"""
        # Valid URLs
        assert security_utils.validate_url("https://example.com") == True
        assert security_utils.validate_url("http://sub.example.com/path") == True

        # Invalid URLs
        assert security_utils.validate_url("javascript:alert(1)") == False
        assert security_utils.validate_url("file:///etc/passwd") == False
        assert security_utils.validate_url("https://example.com/../etc") == False

    @patch("omnicore_engine.security_utils.magic")
    def test_validate_file_type(self, mock_magic, security_utils):
        """Test file type validation"""
        mock_magic.from_buffer.return_value = "application/pdf"

        # Valid file type
        is_valid, mime_type = security_utils.validate_file_type(
            "document.pdf", b"PDF content"
        )
        assert is_valid == True
        assert mime_type == "application/pdf"

        # Invalid file type
        mock_magic.from_buffer.return_value = "application/x-executable"
        is_valid, mime_type = security_utils.validate_file_type(
            "malware.exe", b"EXE content"
        )
        assert is_valid == False

    def test_sanitize_sql_identifier(self, security_utils):
        """Test SQL identifier sanitization"""
        # Valid identifiers
        assert security_utils.sanitize_sql_identifier("user_table") == "user_table"
        assert security_utils.sanitize_sql_identifier("column_1") == "column_1"

        # Invalid identifiers
        with pytest.raises(ValidationError, match="Invalid SQL identifier"):
            security_utils.sanitize_sql_identifier("user'; DROP TABLE--")

        with pytest.raises(ValidationError, match="Reserved SQL word"):
            security_utils.sanitize_sql_identifier("SELECT")

        # Too long
        with pytest.raises(ValidationError, match="too long"):
            security_utils.sanitize_sql_identifier("a" * 65)


class TestRateLimiter:
    """Test RateLimiter class"""

    def test_rate_limiting(self):
        """Test basic rate limiting"""
        limiter = RateLimiter(rate=10.0, burst=5)

        # Should allow burst
        for _ in range(5):
            assert limiter.is_allowed("user1") == True

        # Should be rate limited
        assert limiter.is_allowed("user1") == False

    def test_token_refill(self):
        """Test token refill over time"""
        limiter = RateLimiter(rate=10.0, burst=2)

        # Use all tokens
        assert limiter.is_allowed("user1", tokens=2) == True
        assert limiter.is_allowed("user1") == False

        # Wait for refill
        time.sleep(0.2)  # Should refill ~2 tokens
        assert limiter.is_allowed("user1") == True

    def test_multiple_keys(self):
        """Test rate limiting with multiple keys"""
        limiter = RateLimiter(rate=10.0, burst=2)

        # Different keys have separate buckets
        assert limiter.is_allowed("user1", tokens=2) == True
        assert limiter.is_allowed("user2", tokens=2) == True

        assert limiter.is_allowed("user1") == False
        assert limiter.is_allowed("user2") == False


class TestSecureSessionManager:
    """Test SecureSessionManager class"""

    def test_create_session(self):
        """Test session creation"""
        manager = SecureSessionManager()

        session_id = manager.create_session(
            "user123", {"user_agent": "TestBrowser/1.0", "ip_address": "192.168.1.1"}
        )

        assert session_id is not None
        assert len(session_id) > 0
        assert session_id in manager.sessions

    def test_verify_session(self):
        """Test session verification"""
        manager = SecureSessionManager()

        session_id = manager.create_session(
            "user123", {"user_agent": "TestBrowser/1.0"}
        )

        # Valid session
        session_data = manager.verify_session(session_id)
        assert session_data is not None
        assert session_data["user_id"] == "user123"

        # Invalid session
        assert manager.verify_session("invalid_id") is None

    def test_session_tampering_detection(self):
        """Test session tampering detection"""
        manager = SecureSessionManager()

        session_id = manager.create_session("user123", {})

        # Tamper with session data
        manager.sessions[session_id]["user_id"] = "hacker"

        # Should detect tampering
        with patch(
            "omnicore_engine.security_utils.security_violations"
        ) as mock_violations:
            result = manager.verify_session(session_id)
            assert result is None
            mock_violations.labels.assert_called()


class TestSecurityAuditLogger:
    """Test SecurityAuditLogger class"""

    def test_log_event(self):
        """Test audit event logging"""
        with patch(
            "omnicore_engine.security_utils.logging.getLogger"
        ) as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger

            logger = SecurityAuditLogger()
            logger.logger = mock_logger

            logger.log("login_attempt", {"user": "user123", "ip": "192.168.1.1"})

            mock_logger.log.assert_called_once()
            call_args = mock_logger.log.call_args
            log_data = json.loads(call_args[0][1])

            assert log_data["event"] == "login_attempt"
            assert log_data["details"]["user"] == "user123"
            assert "hash" in log_data

    def test_hash_chain_integrity(self):
        """Test audit log hash chain"""
        logger = SecurityAuditLogger()

        # Log multiple events
        logger.log("event1", {"data": "1"})
        logger.log("event2", {"data": "2"})
        logger.log("event3", {"data": "3"})

        # Verify hash chain
        assert len(logger.hash_chain) == 3
        assert all(isinstance(h, str) for h in logger.hash_chain)

        # Each hash should be unique
        assert len(set(logger.hash_chain)) == 3

    def test_verify_integrity(self):
        """Test audit log integrity verification"""
        logger = SecurityAuditLogger()

        # Should return True (simplified implementation)
        assert logger.verify_integrity() == True


class TestSingleton:
    """Test singleton pattern"""

    def test_get_security_utils_singleton(self):
        """Test security utils singleton"""
        utils1 = get_security_utils()
        utils2 = get_security_utils()

        assert utils1 is utils2


class TestDecorators:
    """Test security decorators"""

    def test_require_authentication_decorator(self):
        """Test authentication decorator"""

        @require_authentication
        def protected_function():
            return "secret"

        # Should work (placeholder implementation)
        result = protected_function()
        assert result == "secret"

    def test_require_authorization_decorator(self):
        """Test authorization decorator"""

        @require_authorization("admin")
        def admin_function():
            return "admin_only"

        # Should work (placeholder implementation)
        result = admin_function()
        assert result == "admin_only"


class TestUtilityFunctions:
    """Test utility functions"""

    def test_convenience_functions(self):
        """Test convenience function exports"""
        from omnicore_engine.security_utils import (
            decrypt,
            encrypt,
            generate_token,
            hash_password,
            verify_password,
        )

        # These should be callable
        assert callable(hash_password)
        assert callable(verify_password)
        assert callable(generate_token)
        assert callable(encrypt)
        assert callable(decrypt)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
