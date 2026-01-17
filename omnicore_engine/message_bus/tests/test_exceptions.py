# test_exceptions.py
"""
Test suite for the unified exception hierarchy in message_bus/exceptions.py

Tests verify:
- Exception hierarchy and inheritance
- Proper exception attributes
- Exception message formatting
- Backward compatibility aliases
"""

import pytest

from omnicore_engine.message_bus.exceptions import (
    CircuitBreakerError,
    DeadLetterQueueError,
    EncryptionError,
    KafkaConnectionError,
    KafkaTimeoutError,
    MessageValidationError,
    MockConnectionError,
    MockTimeoutError,
    OmniCoreConnectionError,
    OmniCoreMessageBusError,
    RateLimitExceededError,
    RedisConnectionError,
    RedisTimeoutError,
)


class TestExceptionHierarchy:
    """Test exception inheritance and hierarchy."""

    def test_base_exception_is_exception(self):
        """OmniCoreMessageBusError should inherit from Exception."""
        assert issubclass(OmniCoreMessageBusError, Exception)

    def test_connection_error_hierarchy(self):
        """Connection errors should inherit from base message bus error."""
        assert issubclass(OmniCoreConnectionError, OmniCoreMessageBusError)
        assert issubclass(RedisConnectionError, OmniCoreConnectionError)
        assert issubclass(RedisTimeoutError, OmniCoreConnectionError)
        assert issubclass(KafkaConnectionError, OmniCoreConnectionError)
        assert issubclass(KafkaTimeoutError, OmniCoreConnectionError)

    def test_other_errors_hierarchy(self):
        """Other specific errors should inherit from base."""
        assert issubclass(CircuitBreakerError, OmniCoreMessageBusError)
        assert issubclass(RateLimitExceededError, OmniCoreMessageBusError)
        assert issubclass(MessageValidationError, OmniCoreMessageBusError)
        assert issubclass(DeadLetterQueueError, OmniCoreMessageBusError)
        assert issubclass(EncryptionError, OmniCoreMessageBusError)

    def test_legacy_aliases(self):
        """Legacy compatibility aliases should work."""
        assert MockConnectionError is OmniCoreConnectionError
        assert MockTimeoutError is OmniCoreConnectionError


class TestBaseMessageBusError:
    """Test OmniCoreMessageBusError base class."""

    def test_simple_message(self):
        """Test basic error with just a message."""
        error = OmniCoreMessageBusError("Test error")
        assert str(error) == "Test error"
        assert error.component is None

    def test_message_with_component(self):
        """Test error with component name."""
        error = OmniCoreMessageBusError("Test error", component="TestComponent")
        assert str(error) == "[TestComponent] Test error"
        assert error.component == "TestComponent"

    def test_exception_raising(self):
        """Test that exception can be raised and caught."""
        with pytest.raises(OmniCoreMessageBusError) as exc_info:
            raise OmniCoreMessageBusError("Test", component="Test")

        assert "Test" in str(exc_info.value)
        assert exc_info.value.component == "Test"


class TestRedisExceptions:
    """Test Redis-specific exceptions."""

    def test_redis_connection_error_basic(self):
        """Test RedisConnectionError with basic message."""
        error = RedisConnectionError("Connection failed")
        assert "RedisBridge" in str(error)
        assert "Connection failed" in str(error)
        assert error.redis_url is None

    def test_redis_connection_error_with_url(self):
        """Test RedisConnectionError with Redis URL."""
        error = RedisConnectionError(
            "Connection failed", redis_url="redis://localhost:6379"
        )
        assert error.redis_url == "redis://localhost:6379"
        assert "RedisBridge" in str(error)

    def test_redis_timeout_error_basic(self):
        """Test RedisTimeoutError with basic message."""
        error = RedisTimeoutError("Operation timeout")
        assert "RedisBridge" in str(error)
        assert error.operation is None

    def test_redis_timeout_error_with_operation(self):
        """Test RedisTimeoutError with operation name."""
        error = RedisTimeoutError("Timeout", operation="publish")
        assert error.operation == "publish"

    def test_redis_error_inheritance(self):
        """Test that Redis errors can be caught as connection errors."""
        with pytest.raises(OmniCoreConnectionError):
            raise RedisConnectionError("Test")


class TestKafkaExceptions:
    """Test Kafka-specific exceptions."""

    def test_kafka_connection_error_basic(self):
        """Test KafkaConnectionError with basic message."""
        error = KafkaConnectionError("Broker unreachable")
        assert "KafkaBridge" in str(error)
        assert error.broker is None

    def test_kafka_connection_error_with_broker(self):
        """Test KafkaConnectionError with broker address."""
        error = KafkaConnectionError("Failed", broker="localhost:9092")
        assert error.broker == "localhost:9092"

    def test_kafka_timeout_error_basic(self):
        """Test KafkaTimeoutError with basic message."""
        error = KafkaTimeoutError("Produce timeout")
        assert "KafkaBridge" in str(error)
        assert error.operation is None

    def test_kafka_timeout_error_with_operation(self):
        """Test KafkaTimeoutError with operation name."""
        error = KafkaTimeoutError("Timeout", operation="consume")
        assert error.operation == "consume"


class TestCircuitBreakerError:
    """Test CircuitBreakerError."""

    def test_circuit_breaker_error_default_state(self):
        """Test CircuitBreakerError with default state."""
        error = CircuitBreakerError("Circuit is open")
        assert "CircuitBreaker" in str(error)
        assert error.breaker_state == "open"

    def test_circuit_breaker_error_custom_state(self):
        """Test CircuitBreakerError with custom state."""
        error = CircuitBreakerError("Testing recovery", breaker_state="half-open")
        assert error.breaker_state == "half-open"


class TestRateLimitExceededError:
    """Test RateLimitExceededError."""

    def test_rate_limit_error_basic(self):
        """Test RateLimitExceededError with basic message."""
        error = RateLimitExceededError("Rate limit exceeded")
        assert "RateLimiter" in str(error)
        assert error.retry_after is None

    def test_rate_limit_error_with_retry_after(self):
        """Test RateLimitExceededError with retry_after."""
        error = RateLimitExceededError("Limit exceeded", retry_after=30.0)
        assert error.retry_after == 30.0


class TestMessageValidationError:
    """Test MessageValidationError."""

    def test_validation_error_basic(self):
        """Test MessageValidationError with basic message."""
        error = MessageValidationError("Invalid message")
        assert "MessageBus" in str(error)
        assert error.validation_errors == {}

    def test_validation_error_with_errors(self):
        """Test MessageValidationError with validation errors dict."""
        errors = {"field1": "too long", "field2": "required"}
        error = MessageValidationError("Validation failed", validation_errors=errors)
        assert error.validation_errors == errors


class TestDeadLetterQueueError:
    """Test DeadLetterQueueError."""

    def test_dlq_error_basic(self):
        """Test DeadLetterQueueError with basic message."""
        error = DeadLetterQueueError("Failed to route to DLQ")
        assert "DeadLetterQueue" in str(error)
        assert error.original_error is None

    def test_dlq_error_with_original(self):
        """Test DeadLetterQueueError with original exception."""
        original = ValueError("Original error")
        error = DeadLetterQueueError("DLQ failed", original_error=original)
        assert error.original_error is original


class TestEncryptionError:
    """Test EncryptionError."""

    def test_encryption_error_encrypt(self):
        """Test EncryptionError for encryption operation."""
        error = EncryptionError("Encryption failed", operation="encrypt")
        assert "MessageEncryption" in str(error)
        assert error.operation == "encrypt"

    def test_encryption_error_decrypt(self):
        """Test EncryptionError for decryption operation."""
        error = EncryptionError("Decryption failed", operation="decrypt")
        assert error.operation == "decrypt"

    def test_encryption_error_default_operation(self):
        """Test EncryptionError with default operation."""
        error = EncryptionError("Failed")
        assert error.operation == "encrypt"


class TestExceptionChaining:
    """Test exception chaining with 'from' clause."""

    def test_chaining_with_from(self):
        """Test that exceptions can be chained properly."""
        original = ValueError("Original error")

        try:
            try:
                raise original
            except ValueError as e:
                raise RedisConnectionError("Wrapped error") from e
        except RedisConnectionError as wrapped:
            assert wrapped.__cause__ is original
            assert "Wrapped error" in str(wrapped)

    def test_catching_by_base_class(self):
        """Test catching specific errors by base class."""
        try:
            raise RedisConnectionError("Test")
        except OmniCoreMessageBusError as e:
            assert isinstance(e, RedisConnectionError)
            assert isinstance(e, OmniCoreConnectionError)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
