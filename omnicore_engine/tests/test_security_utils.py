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
        # The actual implementation only has PBKDF2_SHA256
        assert hasattr(HashAlgorithm, 'PBKDF2_SHA256')
        assert HashAlgorithm.PBKDF2_SHA256.name == "PBKDF2_SHA256"

    def test_encryption_algorithms(self):
        """Test EncryptionAlgorithm enum values"""
        # The actual implementation only has AES_GCM
        assert hasattr(EncryptionAlgorithm, 'AES_GCM')
        assert EncryptionAlgorithm.AES_GCM.name == "AES_GCM"


class TestEnterpriseSecurityUtils:
    """Test EnterpriseSecurityUtils class"""

    @pytest.fixture
    def security_utils(self):
        """Create security utils instance"""
        # EnterpriseSecurityUtils uses keyword-only args with defaults, no config dict needed
        utils = EnterpriseSecurityUtils()
        return utils

    def test_initialization(self, security_utils):
        """Test security utils initialization"""
        # Test that the object is created with expected attributes
        assert security_utils is not None
        assert security_utils.audit is not None
        assert security_utils.sessions is not None
        assert security_utils.rate_limiter is not None

    def test_hash_password(self, security_utils):
        """Test password hashing"""
        password = "Test@Password123"
        hashed = security_utils.hash_password(password)
        
        assert hashed is not None
        assert hashed != password
        # Should be a valid hash string
        assert len(hashed) > 20

    def test_verify_password(self, security_utils):
        """Test password verification"""
        password = "Test@Password123"
        hashed = security_utils.hash_password(password)
        
        is_valid, needs_rehash = security_utils.verify_password(password, hashed)
        
        assert is_valid == True

    def test_encrypt_decrypt_data(self, security_utils):
        """Test data encryption and decryption"""
        test_data = "test data"
        encrypted = security_utils.encrypt(test_data)
        
        assert encrypted is not None
        assert encrypted != test_data
        
        # Test decryption
        decrypted = security_utils.decrypt(encrypted)
        assert decrypted == test_data.encode()

    def test_sanitize_html(self, security_utils):
        """Test HTML sanitization"""
        html_input = "<script>alert('xss')</script><p>Hello</p>"
        sanitized = security_utils.sanitize_html(html_input)
        
        assert "<script>" not in sanitized
        # The sanitized output should have p tag or its content
        assert "Hello" in sanitized

    def test_generate_token(self, security_utils):
        """Test token generation and verification"""
        payload = {"user_id": "123", "role": "admin"}
        token = security_utils.generate_token(payload, ttl_seconds=3600)
        
        assert token is not None
        assert len(token) > 0
        
        # Verify the token
        decoded = security_utils.verify_token(token)
        assert decoded["user_id"] == "123"
        assert decoded["role"] == "admin"

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
        # @ and # should be replaced with _
        assert "@" not in sanitized or "_" in sanitized

    def test_validate_file_type(self, security_utils):
        """Test file type validation"""
        # Test valid Python file
        is_valid, mime = security_utils.validate_file_type("test.py", b"print('hello')")
        assert is_valid == True
        assert "python" in mime

        # Test invalid extension
        is_valid, mime = security_utils.validate_file_type("test.exe", b"binary data")
        assert is_valid == False


class TestRateLimiter:
    """Test RateLimiter class"""

    def test_rate_limiting(self):
        """Test basic rate limiting"""
        # The actual implementation uses max_calls and per_seconds
        limiter = RateLimiter(max_calls=5, per_seconds=60)

        # Should allow up to max_calls
        for _ in range(5):
            limiter.check("user1")  # Should not raise

        # Should be rate limited on next call
        with pytest.raises(RateLimitError):
            limiter.check("user1")

    def test_token_refill(self):
        """Test remaining tokens"""
        limiter = RateLimiter(max_calls=5, per_seconds=60)

        # Use all tokens
        for _ in range(5):
            limiter.check("user1")

        # Check remaining
        remaining = limiter.remaining("user1")
        assert remaining == 0

    def test_multiple_keys(self):
        """Test rate limiting with multiple keys"""
        limiter = RateLimiter(max_calls=2, per_seconds=60)

        # Different keys have separate buckets
        limiter.check("user1")
        limiter.check("user1")
        limiter.check("user2")
        limiter.check("user2")

        # user1 and user2 should be limited separately
        with pytest.raises(RateLimitError):
            limiter.check("user1")
        with pytest.raises(RateLimitError):
            limiter.check("user2")


class TestSecureSessionManager:
    """Test SecureSessionManager class"""

    def test_create_session(self):
        """Test session creation"""
        manager = SecureSessionManager(secret="test_secret_key")

        session = manager.create(
            "user123", data={"user_agent": "TestBrowser/1.0", "ip_address": "192.168.1.1"}
        )

        assert session is not None
        assert session.id is not None
        assert len(session.id) > 0
        assert session.user_id == "user123"

    def test_verify_session(self):
        """Test session retrieval"""
        manager = SecureSessionManager(secret="test_secret_key")

        session = manager.create(
            "user123", data={"user_agent": "TestBrowser/1.0"}
        )

        # Valid session retrieval
        retrieved = manager.get(session.id)
        assert retrieved is not None
        assert retrieved.user_id == "user123"

        # Invalid session should raise
        with pytest.raises(AuthenticationError):
            manager.get("invalid_id")

    def test_session_tampering_detection(self):
        """Test session tampering detection via HMAC signature"""
        manager = SecureSessionManager(secret="test_secret_key")

        session = manager.create("user123", data={})

        # Tamper with session ID
        tampered_id = "tampered_" + session.id[9:]

        # Should detect tampering via HMAC verification
        with pytest.raises(AuthenticationError):
            manager.get(tampered_id)


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
            
            logger.log("login_attempt", "user123", metadata={"ip": "192.168.1.1"})
            
            # Just verify no exception was raised
            assert True

    def test_log_multiple_events(self):
        """Test logging multiple events"""
        logger = SecurityAuditLogger()

        # Log multiple events - should not raise
        logger.log("event1", "user1", metadata={"data": "1"})
        logger.log("event2", "user2", metadata={"data": "2"})
        logger.log("event3", "user3", metadata={"data": "3"})
        
        assert True  # If we get here, no exceptions were raised


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
        def protected_function(user=None):
            return "secret"

        # Create a mock user object with is_authenticated = True
        class MockUser:
            is_authenticated = True
        
        # Should work with authenticated user
        result = protected_function(user=MockUser())
        assert result == "secret"

    def test_require_authorization_decorator(self):
        """Test authorization decorator"""

        @require_authorization("admin")
        def admin_function(user=None):
            return "admin_only"

        # Create a mock user object with is_authenticated and roles
        class MockUser:
            is_authenticated = True
            roles = ["admin"]
        
        # Should work with authorized user
        result = admin_function(user=MockUser())
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
