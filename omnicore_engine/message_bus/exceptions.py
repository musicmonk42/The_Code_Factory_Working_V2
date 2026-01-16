# omnicore_engine/message_bus/exceptions.py
"""
Centralized exception hierarchy for the OmniCore Message Bus.

This module provides a unified, project-wide exception hierarchy to replace
ad-hoc mock exceptions scattered across different modules. This improves
maintainability, type safety, and error handling consistency.

Industry Standard Practices:
- Hierarchical exception design for granular error handling
- Clear exception naming following Python conventions (Error suffix)
- Comprehensive docstrings for each exception type
- Proper exception chaining with 'from' clause support
"""

from typing import Optional


class OmniCoreMessageBusError(Exception):
    """
    Base exception for all OmniCore Message Bus errors.
    
    All message bus exceptions inherit from this base class, enabling
    catch-all error handling when needed while maintaining the ability
    to handle specific errors granularly.
    """
    
    def __init__(self, message: str, component: Optional[str] = None):
        """
        Initialize the base message bus error.
        
        Args:
            message: Human-readable error description
            component: Optional component name where error occurred (e.g., 'RedisBridge')
        """
        self.component = component
        if component:
            message = f"[{component}] {message}"
        super().__init__(message)


class OmniCoreConnectionError(OmniCoreMessageBusError):
    """
    Base exception for all connection-related errors.
    
    Raised when the message bus cannot establish or maintain a connection
    to an external service (Redis, Kafka, etc.). This replaces the various
    ad-hoc ConnectionError mock types throughout the codebase.
    
    Examples:
        - Network timeout connecting to Redis
        - Kafka broker unreachable
        - Connection pool exhausted
    """
    pass


class RedisConnectionError(OmniCoreConnectionError):
    """
    Redis-specific connection error.
    
    Raised when Redis connection fails, times out, or is lost.
    This provides a typed exception for Redis operations while
    maintaining compatibility with the generic ConnectionError handling.
    """
    
    def __init__(self, message: str, redis_url: Optional[str] = None):
        """
        Initialize Redis connection error.
        
        Args:
            message: Error description
            redis_url: Optional Redis URL that failed to connect (sanitized)
        """
        self.redis_url = redis_url
        super().__init__(message, component="RedisBridge")


class RedisTimeoutError(OmniCoreConnectionError):
    """
    Redis operation timeout error.
    
    Raised when a Redis operation exceeds its configured timeout.
    """
    
    def __init__(self, message: str, operation: Optional[str] = None):
        """
        Initialize Redis timeout error.
        
        Args:
            message: Error description
            operation: Optional operation that timed out (e.g., 'publish', 'subscribe')
        """
        self.operation = operation
        super().__init__(message, component="RedisBridge")


class KafkaConnectionError(OmniCoreConnectionError):
    """
    Kafka-specific connection error.
    
    Raised when Kafka connection fails or broker is unreachable.
    """
    
    def __init__(self, message: str, broker: Optional[str] = None):
        """
        Initialize Kafka connection error.
        
        Args:
            message: Error description
            broker: Optional Kafka broker address that failed
        """
        self.broker = broker
        super().__init__(message, component="KafkaBridge")


class KafkaTimeoutError(OmniCoreConnectionError):
    """
    Kafka operation timeout error.
    
    Raised when a Kafka operation (produce/consume) exceeds timeout.
    """
    
    def __init__(self, message: str, operation: Optional[str] = None):
        """
        Initialize Kafka timeout error.
        
        Args:
            message: Error description
            operation: Optional operation that timed out
        """
        self.operation = operation
        super().__init__(message, component="KafkaBridge")


class CircuitBreakerError(OmniCoreMessageBusError):
    """
    Circuit breaker open error.
    
    Raised when attempting an operation while the circuit breaker is open,
    indicating that the service is currently unavailable due to repeated failures.
    
    This follows the Circuit Breaker pattern from Michael Nygard's
    "Release It!" and Netflix's Hystrix implementation.
    """
    
    def __init__(self, message: str, breaker_state: str = "open"):
        """
        Initialize circuit breaker error.
        
        Args:
            message: Error description
            breaker_state: Current state of the breaker ('open', 'half-open')
        """
        self.breaker_state = breaker_state
        super().__init__(message, component="CircuitBreaker")


class RateLimitExceededError(OmniCoreMessageBusError):
    """
    Rate limit exceeded error.
    
    Raised when a client exceeds their configured rate limit.
    Includes retry-after information for proper backoff handling.
    """
    
    def __init__(self, message: str, retry_after: Optional[float] = None):
        """
        Initialize rate limit error.
        
        Args:
            message: Error description
            retry_after: Seconds to wait before retrying (for backoff)
        """
        self.retry_after = retry_after
        super().__init__(message, component="RateLimiter")


class MessageValidationError(OmniCoreMessageBusError):
    """
    Message validation error.
    
    Raised when a message fails validation (e.g., too large, missing required fields).
    """
    
    def __init__(self, message: str, validation_errors: Optional[dict] = None):
        """
        Initialize message validation error.
        
        Args:
            message: Error description
            validation_errors: Optional dict of field-level validation errors
        """
        self.validation_errors = validation_errors or {}
        super().__init__(message, component="MessageBus")


class DeadLetterQueueError(OmniCoreMessageBusError):
    """
    Dead letter queue error.
    
    Raised when a message cannot be processed after maximum retries
    and fails to be placed in the dead letter queue.
    """
    
    def __init__(self, message: str, original_error: Optional[Exception] = None):
        """
        Initialize dead letter queue error.
        
        Args:
            message: Error description
            original_error: Optional original exception that caused DLQ routing
        """
        self.original_error = original_error
        super().__init__(message, component="DeadLetterQueue")


class EncryptionError(OmniCoreMessageBusError):
    """
    Message encryption/decryption error.
    
    Raised when message encryption or decryption fails.
    """
    
    def __init__(self, message: str, operation: str = "encrypt"):
        """
        Initialize encryption error.
        
        Args:
            message: Error description
            operation: Operation that failed ('encrypt' or 'decrypt')
        """
        self.operation = operation
        super().__init__(message, component="MessageEncryption")


# Legacy compatibility aliases (can be deprecated in future versions)
# These maintain backward compatibility while transitioning to new exception types
MockConnectionError = OmniCoreConnectionError
MockTimeoutError = OmniCoreConnectionError
