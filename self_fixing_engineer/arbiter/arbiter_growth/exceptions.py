import logging
from typing import Any, Dict, Optional

# Configure a logger for this module.
logger = logging.getLogger(__name__)


class ArbiterGrowthError(Exception):
    """
    Base exception for all errors originating from the ArbiterGrowthManager.

    This exception is not typically raised directly but serves as a parent class
    for more specific exceptions, allowing for consolidated error handling.
    """

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        """
        Initializes the base exception.

        Args:
            message (str): A human-readable error message.
            details (Optional[Dict[str, Any]]): A dictionary containing
                context-specific details about the error.
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}
        logger.error(
            "Exception raised: %s, Details: %s", self.__class__.__name__, self.details
        )

    def __str__(self) -> str:
        """Returns a string representation of the exception, including details."""
        if self.details:
            return f"{self.message} (Details: {self.details})"
        return self.message


class OperationQueueFullError(ArbiterGrowthError):
    """
    Raised when an operation cannot be added because the pending operations queue is full.

    This indicates that the system is currently processing at its maximum configured
    capacity and cannot accept new work until some existing operations complete.
    """

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        """
        Initializes the OperationQueueFullError.

        Args:
            message (str): A message indicating the queue is full.
            details (Optional[Dict[str, Any]]): Typically includes queue size,
                current load, and the operation that was rejected.
        """
        super().__init__(message, details)


class RateLimitError(ArbiterGrowthError):
    """
    Raised when an operation is rejected due to rate limiting.

    This error occurs when the number of operations exceeds a predefined threshold
    within a specific time window, protecting the system from being overwhelmed.
    """

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        """
        Initializes the RateLimitError.

        Args:
            message (str): A message indicating the rate limit was exceeded.
            details (Optional[Dict[str, Any]]): May include the specific rate limit
                that was triggered and the client's request rate.
        """
        super().__init__(message, details)


class CircuitBreakerOpenError(ArbiterGrowthError):
    """
    Raised when an operation fails because the circuit breaker is in the 'open' state.

    This signifies that a downstream service or a critical component has been
    experiencing repeated failures, and the circuit breaker has tripped to prevent
    further requests, allowing the failing component time to recover.
    """

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        """
        Initializes the CircuitBreakerOpenError.

        Args:
            message (str): A message indicating the circuit is open.
            details (Optional[Dict[str, Any]]): Can include which circuit breaker
                is open and when it is expected to transition to half-open.
        """
        super().__init__(message, details)


class AuditChainTamperedError(ArbiterGrowthError):
    """
    Raised when a validation check of the audit log's hash chain fails.

    This is a critical security exception, indicating that the integrity of the
    audit log may have been compromised and the log has been tampered with.
    """

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        """
        Initializes the AuditChainTamperedError.

        Args:
            message (str): A message indicating the audit chain is invalid.
            details (Optional[Dict[str, Any]]): Should include details about the
                validation failure, such as the expected vs. actual hash.
        """
        super().__init__(message, details)
