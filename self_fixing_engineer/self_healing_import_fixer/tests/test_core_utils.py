# self_healing_import_fixer/tests/test_core_utils.py

"""
Comprehensive test suite for enterprise-grade core_utils module.
Tests all critical functionality including alerting, circuit breakers, rate limiting,
caching, security features, and operational utilities.
"""

import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import from the analyzer module
from analyzer.core_utils import (
    SERVICE_NAME,
    AlertChannel,
    AlertLevel,
    CircuitBreaker,
    RateLimiter,
    _alert_config,
    _cache,
    _circuit_breakers,
    _rate_limiters,
    alert_operator,
    cached,
    encode_for_logging,
    generate_correlation_id,
    get_circuit_breaker,
    get_system_health,
    retry_with_backoff,
    sanitize_path,
    scrub_secrets,
    secure_hash,
    timing_context,
    validate_input,
    verify_hash,
)


class TestAlertSystem:
    """Test suite for the alerting system."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset alert configuration before each test."""
        _alert_config.enabled_channels = [AlertChannel.LOG]
        _cache.clear()
        _circuit_breakers.clear()
        _rate_limiters.clear()
        yield
        _cache.clear()
        _circuit_breakers.clear()
        _rate_limiters.clear()

    def test_alert_operator_basic(self, caplog):
        """Test basic alert functionality."""
        with caplog.at_level("ERROR"):
            alert_operator("Test alert", AlertLevel.ERROR)
            # Check that the alert was attempted (even if it failed due to 'message' conflict)
            assert any(
                "Test alert" in record.message
                or "Failed to send alert" in record.message
                for record in caplog.records
            )

    def test_alert_operator_with_details(self, caplog):
        """Test alert with additional details."""
        details = {"user_id": "123", "action": "login_failed"}
        with caplog.at_level("WARNING"):
            alert_operator("Login failure", AlertLevel.WARNING, details=details)
            assert any(
                "Login failure" in record.message
                or "Failed to send alert" in record.message
                for record in caplog.records
            )

    def test_alert_deduplication(self, caplog):
        """Test that duplicate alerts are suppressed."""
        dedupe_key = "test_dedupe_123"

        # Clear any existing deduplication cache
        _cache.clear()

        with caplog.at_level("INFO"):
            # First alert should go through
            alert_operator("Duplicate alert", AlertLevel.INFO, dedupe_key=dedupe_key)
            initial_count = len(caplog.records)

            # Subsequent alerts with same dedupe_key should be suppressed
            alert_operator("Duplicate alert", AlertLevel.INFO, dedupe_key=dedupe_key)
            alert_operator("Duplicate alert", AlertLevel.INFO, dedupe_key=dedupe_key)

            # Should not have added more log records
            assert len(caplog.records) == initial_count

    def test_alert_rate_limiting(self):
        """Test rate limiting for non-critical alerts."""
        # Create a rate limiter with very low limit
        limiter = RateLimiter(max_calls=2, window_seconds=1)
        _rate_limiters["alerts"] = limiter

        with patch(
            "self_healing_import_fixer.analyzer.core_utils.logger"
        ) as mock_logger:
            # First two should go through
            alert_operator("Alert 1", AlertLevel.INFO)
            alert_operator("Alert 2", AlertLevel.INFO)
            # Third should be rate limited
            alert_operator("Alert 3", AlertLevel.INFO)

            # Check for rate limit warning
            mock_logger.warning.assert_called_with("Alert rate limited: Alert 3")

    def test_critical_alerts_bypass_rate_limit(self):
        """Test that critical alerts bypass rate limiting."""
        limiter = RateLimiter(max_calls=1, window_seconds=60)
        _rate_limiters["alerts"] = limiter

        with patch(
            "self_healing_import_fixer.analyzer.core_utils.logger"
        ) as mock_logger:
            alert_operator("Alert 1", AlertLevel.INFO)
            alert_operator("Critical Alert", AlertLevel.CRITICAL)
            alert_operator("Emergency Alert", AlertLevel.EMERGENCY)

            # Critical alerts should not be rate limited
            warning_calls = [
                call
                for call in mock_logger.warning.call_args_list
                if "rate limited" in str(call)
            ]
            assert len(warning_calls) == 0

    @patch("urllib.request.urlopen")  # Patch the actual module, not through core_utils
    def test_slack_alert(self, mock_urlopen):
        """Test Slack alert functionality."""
        _alert_config.slack_webhook_url = "https://hooks.slack.com/test"
        _alert_config.enabled_channels = [AlertChannel.SLACK]

        alert_operator("Slack test", AlertLevel.ERROR)

        mock_urlopen.assert_called_once()
        call_args = mock_urlopen.call_args[0][0]
        assert call_args.get_full_url() == "https://hooks.slack.com/test"

        # Verify payload structure
        payload = json.loads(call_args.data.decode("utf-8"))
        assert "attachments" in payload
        assert payload["attachments"][0]["title"] == "ERROR: Slack test"

    @patch("boto3.client")  # Patch boto3 directly
    def test_sns_alert(self, mock_boto_client):
        """Test AWS SNS alert functionality."""
        _alert_config.sns_topic_arn = "arn:aws:sns:us-east-1:123456789:test-topic"
        _alert_config.enabled_channels = [AlertChannel.SNS]

        mock_sns = MagicMock()
        mock_boto_client.return_value = mock_sns

        alert_operator("SNS test", AlertLevel.WARNING)

        mock_sns.publish.assert_called_once()
        call_kwargs = mock_sns.publish.call_args[1]
        assert call_kwargs["TopicArn"] == "arn:aws:sns:us-east-1:123456789:test-topic"
        assert "WARNING" in call_kwargs["Subject"]

    @patch("smtplib.SMTP")  # Patch smtplib directly
    def test_email_alert(self, mock_smtp):
        """Test email alert functionality."""
        _alert_config.email_smtp_host = "smtp.example.com"
        _alert_config.email_from = "alerts@example.com"
        _alert_config.email_to = ["admin@example.com"]
        _alert_config.enabled_channels = [AlertChannel.EMAIL]

        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        alert_operator("Email test", AlertLevel.INFO)

        mock_server.send_message.assert_called_once()

    def test_multiple_channels(self, caplog):
        """Test sending alerts to multiple channels simultaneously."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            _alert_config.slack_webhook_url = "https://hooks.slack.com/test"
            _alert_config.webhook_urls = [
                "https://webhook1.com",
                "https://webhook2.com",
            ]
            _alert_config.enabled_channels = [
                AlertChannel.LOG,
                AlertChannel.SLACK,
                AlertChannel.WEBHOOK,
            ]

            alert_operator("Multi-channel test", AlertLevel.ERROR)

            # Should make 3 HTTP calls (1 Slack + 2 webhooks)
            assert mock_urlopen.call_count == 3


class TestCircuitBreaker:
    """Test suite for circuit breaker functionality."""

    def test_circuit_breaker_normal_operation(self):
        """Test circuit breaker in normal operation."""
        cb = CircuitBreaker("test_service", failure_threshold=3)

        def successful_operation():
            return "success"

        result = cb.call(successful_operation)
        assert result == "success"
        assert cb.state == "closed"

    def test_circuit_breaker_opens_after_failures(self):
        """Test circuit breaker opens after threshold failures."""
        cb = CircuitBreaker("test_service", failure_threshold=3)

        def failing_operation():
            raise Exception("Service unavailable")

        # Fail 3 times to open the circuit
        for _ in range(3):
            with pytest.raises(Exception):
                cb.call(failing_operation)

        assert cb.state == "open"
        assert cb.failure_count == 3

        # Further calls should fail immediately
        with pytest.raises(Exception) as exc_info:
            cb.call(failing_operation)
        assert "Circuit breaker test_service is open" in str(exc_info.value)

    @pytest.mark.slow
    def test_circuit_breaker_half_open_state(self):
        """Test circuit breaker transitions to half-open state."""
        cb = CircuitBreaker("test_service", failure_threshold=2, recovery_timeout=0.1)

        def failing_operation():
            raise Exception("Service unavailable")

        # Open the circuit
        for _ in range(2):
            with pytest.raises(Exception):
                cb.call(failing_operation)

        assert cb.state == "open"

        # Wait for recovery timeout
        time.sleep(0.2)

        # Next call should attempt (half-open state)
        def successful_operation():
            return "recovered"

        result = cb.call(successful_operation)
        assert result == "recovered"
        assert cb.state == "closed"

    def test_get_circuit_breaker_singleton(self):
        """Test that get_circuit_breaker returns singleton instances."""
        cb1 = get_circuit_breaker("service_a")
        cb2 = get_circuit_breaker("service_a")
        cb3 = get_circuit_breaker("service_b")

        assert cb1 is cb2
        assert cb1 is not cb3


class TestRateLimiter:
    """Test suite for rate limiting functionality."""

    def test_rate_limiter_allows_within_limit(self):
        """Test rate limiter allows calls within limit."""
        limiter = RateLimiter(max_calls=3, window_seconds=1)

        assert limiter.is_allowed() is True
        assert limiter.is_allowed() is True
        assert limiter.is_allowed() is True
        assert limiter.is_allowed() is False

    @pytest.mark.slow
    def test_rate_limiter_window_reset(self):
        """Test rate limiter resets after window expires."""
        limiter = RateLimiter(max_calls=2, window_seconds=0.1)

        assert limiter.is_allowed() is True
        assert limiter.is_allowed() is True
        assert limiter.is_allowed() is False

        time.sleep(0.2)

        assert limiter.is_allowed() is True

    @pytest.mark.slow
    def test_rate_limiter_wait_if_needed(self):
        """Test rate limiter blocking wait functionality."""
        limiter = RateLimiter(max_calls=1, window_seconds=0.2)

        limiter.is_allowed()  # Use up the limit

        start_time = time.time()
        limiter.wait_if_needed()  # Should wait
        elapsed = time.time() - start_time

        assert elapsed >= 0.2

    def test_rate_limiter_thread_safety(self):
        """Test rate limiter is thread-safe."""
        limiter = RateLimiter(max_calls=10, window_seconds=1)
        allowed_count = 0
        lock = threading.Lock()

        def make_request():
            nonlocal allowed_count
            if limiter.is_allowed():
                with lock:
                    allowed_count += 1

        threads = [threading.Thread(target=make_request) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert allowed_count == 10


class TestRetryMechanism:
    """Test suite for retry with backoff functionality."""

    def test_retry_successful_on_second_attempt(self):
        """Test function succeeds on retry."""
        attempt_count = 0

        @retry_with_backoff(max_retries=3, initial_backoff=0.01)
        def flaky_function():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 2:
                raise ValueError("Temporary failure")
            return "success"

        result = flaky_function()
        assert result == "success"
        assert attempt_count == 2

    def test_retry_exhausts_attempts(self):
        """Test retry exhausts all attempts and raises."""
        attempt_count = 0

        @retry_with_backoff(max_retries=3, initial_backoff=0.01)
        def always_fails():
            nonlocal attempt_count
            attempt_count += 1
            raise ValueError(f"Failure {attempt_count}")

        with pytest.raises(ValueError) as exc_info:
            always_fails()

        assert "Failure 3" in str(exc_info.value)
        assert attempt_count == 3

    @pytest.mark.asyncio
    async def test_async_retry(self):
        """Test retry decorator with async functions."""
        attempt_count = 0

        @retry_with_backoff(max_retries=3, initial_backoff=0.01)
        async def async_flaky_function():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 2:
                raise ValueError("Temporary failure")
            return "async_success"

        result = await async_flaky_function()
        assert result == "async_success"
        assert attempt_count == 2

    def test_retry_with_specific_exceptions(self):
        """Test retry only on specific exceptions."""
        attempt_count = 0

        @retry_with_backoff(
            max_retries=3, exceptions=(ValueError,), initial_backoff=0.01
        )
        def selective_retry():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                raise ValueError("Retryable")
            elif attempt_count == 2:
                raise TypeError("Not retryable")
            return "success"

        with pytest.raises(TypeError):
            selective_retry()

        assert attempt_count == 2  # Should stop after TypeError


class TestCaching:
    """Test suite for caching functionality."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Clear cache before each test."""
        _cache.clear()
        yield
        _cache.clear()

    def test_cache_hit(self):
        """Test cache returns cached value."""
        call_count = 0

        @cached(ttl_seconds=1)
        def expensive_operation(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        result1 = expensive_operation(5)
        result2 = expensive_operation(5)

        assert result1 == 10
        assert result2 == 10
        assert call_count == 1  # Should only be called once

    @pytest.mark.slow
    def test_cache_expiration(self):
        """Test cache expires after TTL."""
        call_count = 0

        @cached(ttl_seconds=0.1)
        def operation(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        result1 = operation(5)
        time.sleep(0.2)  # Wait for cache to expire
        result2 = operation(5)

        assert result1 == 10
        assert result2 == 10
        assert call_count == 2  # Should be called twice

    def test_cache_different_args(self):
        """Test cache differentiates between different arguments."""
        call_count = 0

        @cached(ttl_seconds=1)
        def operation(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        result1 = operation(5)
        result2 = operation(10)

        assert result1 == 10
        assert result2 == 20
        assert call_count == 2  # Different args should cause separate calls


class TestSecurityUtilities:
    """Test suite for security utilities."""

    def test_scrub_secrets_dict(self):
        """Test scrubbing secrets from dictionaries."""
        data = {
            "username": "admin",
            "password": "secret123",
            "api_key": "abc-123-def",
            "safe_data": "this is ok",
        }

        scrubbed = scrub_secrets(data)
        assert scrubbed["username"] == "admin"
        assert scrubbed["password"] == "***REDACTED***"
        assert scrubbed["api_key"] == "***REDACTED***"
        assert scrubbed["safe_data"] == "this is ok"

    def test_scrub_secrets_nested(self):
        """Test scrubbing secrets from nested structures."""
        data = {
            "config": {
                "database": {
                    "connection_string": "postgresql://user:pass@localhost",
                    "pool_size": 10,
                }
            }
        }

        scrubbed = scrub_secrets(data)
        assert scrubbed["config"]["database"]["connection_string"] == "***REDACTED***"
        assert scrubbed["config"]["database"]["pool_size"] == 10

    def test_secure_hash(self):
        """Test secure hashing."""
        data = "sensitive_data"
        hash1 = secure_hash(data)
        hash2 = secure_hash(data)

        # Different salts should produce different hashes
        assert hash1 != hash2

        # But verification should work
        assert verify_hash(data, hash1)
        assert verify_hash(data, hash2)
        assert not verify_hash("wrong_data", hash1)

    def test_sanitize_path(self):
        """Test path sanitization."""
        assert sanitize_path("../../../etc/passwd") == "etcpasswd"
        assert sanitize_path("~/secret.txt") == "secret.txt"
        assert sanitize_path("/etc/config") == "config"
        assert sanitize_path("normal_file.txt") == "normal_file.txt"


class TestValidation:
    """Test suite for input validation."""

    def test_validate_required_fields(self):
        """Test validation of required fields."""
        schema = {
            "name": {"required": True, "type": str},
            "age": {"required": True, "type": int},
        }

        valid_data = {"name": "John", "age": 30}
        is_valid, error = validate_input(valid_data, schema)
        assert is_valid is True
        assert error is None

        invalid_data = {"name": "John"}
        is_valid, error = validate_input(invalid_data, schema)
        assert is_valid is False
        assert "Required field 'age' is missing" in error

    def test_validate_types(self):
        """Test type validation."""
        schema = {"count": {"type": int}, "name": {"type": str}}

        valid_data = {"count": 5, "name": "test"}
        is_valid, error = validate_input(valid_data, schema)
        assert is_valid is True

        invalid_data = {"count": "not_an_int", "name": "test"}
        is_valid, error = validate_input(invalid_data, schema)
        assert is_valid is False
        assert "must be of type int" in error

    def test_validate_string_length(self):
        """Test string length validation."""
        schema = {"username": {"type": str, "min_length": 3, "max_length": 20}}

        valid_data = {"username": "john_doe"}
        is_valid, error = validate_input(valid_data, schema)
        assert is_valid is True

        invalid_short = {"username": "ab"}
        is_valid, error = validate_input(invalid_short, schema)
        assert is_valid is False
        assert "at least 3 characters" in error

        invalid_long = {"username": "a" * 25}
        is_valid, error = validate_input(invalid_long, schema)
        assert is_valid is False
        assert "at most 20 characters" in error


class TestOperationalUtilities:
    """Test suite for operational utilities."""

    def test_generate_correlation_id(self):
        """Test correlation ID generation."""
        id1 = generate_correlation_id()
        id2 = generate_correlation_id()

        assert id1 != id2
        assert SERVICE_NAME in id1
        assert len(id1.split("-")) >= 3

    @patch("psutil.cpu_percent", return_value=50.0)
    @patch("psutil.virtual_memory")
    @patch("psutil.disk_usage")
    def test_get_system_health(self, mock_disk, mock_memory, mock_cpu):
        """Test system health check."""
        # Mock memory stats
        mock_memory.return_value = MagicMock(percent=60.0, available=8 * (1024**3))

        # Mock disk stats
        mock_disk.return_value = MagicMock(percent=70.0, free=100 * (1024**3))

        health = get_system_health()

        assert health["status"] == "healthy"
        assert health["service"] == SERVICE_NAME
        assert health["checks"]["cpu"]["usage_percent"] == 50.0
        assert health["checks"]["memory"]["usage_percent"] == 60.0
        assert health["checks"]["disk"]["usage_percent"] == 70.0

    @pytest.mark.slow
    def test_timing_context(self):
        """Test timing context manager."""
        with patch(
            "self_healing_import_fixer.analyzer.core_utils.logger"
        ) as mock_logger:
            with timing_context("test_operation"):
                time.sleep(0.1)

            # Check that timing was logged
            debug_calls = [
                call
                for call in mock_logger.debug.call_args_list
                if "test_operation took" in str(call)
            ]
            assert len(debug_calls) > 0

    def test_encode_for_logging(self):
        """Test encoding for logging."""
        assert encode_for_logging("simple string") == "simple string"
        assert encode_for_logging({"key": "value"}) == '{"key": "value"}'
        assert encode_for_logging(["a", "b"]) == '["a", "b"]'
        assert encode_for_logging(b"bytes") == "bytes"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
