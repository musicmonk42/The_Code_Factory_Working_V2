# test_kms_invalid_ciphertext_exception.py
"""
Tests for AWS KMS InvalidCiphertextException handling and rate-limited logging.
"""

import asyncio
import logging
import time
from unittest.mock import MagicMock, patch

import pytest


class TestInvalidCiphertextException:
    """Tests for InvalidCiphertextException handling in _ensure_software_key_master."""

    @pytest.fixture
    def mock_botocore_client_error(self):
        """Create a mock ClientError with InvalidCiphertextException."""
        # Mock the botocore ClientError
        mock_error = MagicMock()
        mock_error.response = {
            'Error': {
                'Code': 'InvalidCiphertextException',
                'Message': 'The ciphertext refers to a customer master key that does not exist'
            }
        }
        return mock_error

    @pytest.mark.asyncio
    async def test_invalid_ciphertext_exception_handling(
        self, monkeypatch, mock_secrets, mock_boto, mock_botocore_client_error
    ):
        """Tests that InvalidCiphertextException is properly detected and logged."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            CryptoInitializationError,
            _ensure_software_key_master,
        )

        # Set up production mode
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )

        # Mock botocore to have ClientError
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory.HAS_BOTO3",
            True,
        )
        
        # Create a mock botocore module with ClientError
        mock_botocore_module = MagicMock()
        mock_botocore_module.ClientError = type(mock_botocore_client_error)
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory.botocore",
            mock_botocore_module,
        )

        # Make the KMS decrypt call raise InvalidCiphertextException
        mock_boto[1].side_effect = mock_botocore_client_error

        # Test that the exception is caught and re-raised with proper message
        with pytest.raises(
            CryptoInitializationError, 
            match="InvalidCiphertextException: Master key encrypted with different KMS key"
        ):
            await _ensure_software_key_master()

    @pytest.mark.asyncio
    async def test_generic_kms_error_handling(
        self, monkeypatch, mock_secrets, mock_boto
    ):
        """Tests that generic KMS errors are still handled properly."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            CryptoInitializationError,
            _ensure_software_key_master,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )

        # Raise a generic exception (not InvalidCiphertextException)
        mock_boto[1].side_effect = Exception("Generic KMS error")

        with pytest.raises(
            CryptoInitializationError, 
            match="Failed to initialize software key master"
        ):
            await _ensure_software_key_master()


class TestRateLimitedLogger:
    """Tests for the RateLimitedLogger class."""

    def test_rate_limited_logger_basic(self):
        """Tests basic rate limiting functionality."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import RateLimitedLogger

        # Create a rate limiter with 1 second interval
        limiter = RateLimitedLogger(interval_seconds=1)
        
        # Mock logger
        mock_log_func = MagicMock()

        # First call should log
        result1 = limiter.rate_limited_log(mock_log_func, "test_key", "Test message 1")
        assert result1 is True
        assert mock_log_func.call_count == 1

        # Immediate second call should be rate-limited
        result2 = limiter.rate_limited_log(mock_log_func, "test_key", "Test message 2")
        assert result2 is False
        assert mock_log_func.call_count == 1  # Still 1, not incremented

    def test_rate_limited_logger_after_interval(self):
        """Tests that logging works again after the interval passes."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import RateLimitedLogger

        # Create a rate limiter with very short interval for testing
        limiter = RateLimitedLogger(interval_seconds=0.1)
        
        mock_log_func = MagicMock()

        # First call should log
        limiter.rate_limited_log(mock_log_func, "test_key", "Test message 1")
        assert mock_log_func.call_count == 1

        # Wait for interval to pass
        time.sleep(0.15)

        # Second call should also log
        limiter.rate_limited_log(mock_log_func, "test_key", "Test message 2")
        assert mock_log_func.call_count == 2

    def test_rate_limited_logger_different_keys(self):
        """Tests that different error keys are tracked independently."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import RateLimitedLogger

        limiter = RateLimitedLogger(interval_seconds=60)
        
        mock_log_func = MagicMock()

        # Log with first key
        limiter.rate_limited_log(mock_log_func, "key1", "Message 1")
        assert mock_log_func.call_count == 1

        # Log with second key - should also log since it's a different key
        limiter.rate_limited_log(mock_log_func, "key2", "Message 2")
        assert mock_log_func.call_count == 2

        # Try logging with first key again - should be rate-limited
        limiter.rate_limited_log(mock_log_func, "key1", "Message 3")
        assert mock_log_func.call_count == 2  # Still 2

    def test_rate_limited_logger_thread_safety(self):
        """Tests that the rate limiter is thread-safe."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import RateLimitedLogger
        import threading

        limiter = RateLimitedLogger(interval_seconds=60)
        mock_log_func = MagicMock()
        results = []

        def log_from_thread():
            result = limiter.rate_limited_log(mock_log_func, "test_key", "Test message")
            results.append(result)

        # Start multiple threads trying to log at the same time
        threads = [threading.Thread(target=log_from_thread) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only one thread should have successfully logged
        assert sum(results) == 1
        assert mock_log_func.call_count == 1


# Fixtures from test_audit_crypto_factory.py for compatibility

@pytest.fixture
def mock_secrets(monkeypatch):
    """Mocks the secret fetching functions from secrets.py."""
    mock_aget_kms = MagicMock(return_value=b"mock_encrypted_ciphertext_blob")
    mock_aget_hmac = MagicMock(return_value=b"mock_hmac_secret_bytes")

    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_factory.aget_kms_master_key_ciphertext_blob",
        mock_aget_kms,
    )
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_factory.aget_fallback_hmac_secret",
        mock_aget_hmac,
    )

    return {"aget_kms": mock_aget_kms, "aget_hmac": mock_aget_hmac}


@pytest.fixture
def mock_boto(monkeypatch):
    """Mocks boto3 and botocore."""
    mock_kms_client = MagicMock()
    mock_kms_client.decrypt.return_value = {
        "Plaintext": b"0123456789abcdef0123456789abcdef"  # 32 bytes
    }

    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_kms_client

    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_factory.boto3", mock_boto3
    )
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_factory.HAS_BOTO3", True
    )

    return (mock_boto3, mock_kms_client)
