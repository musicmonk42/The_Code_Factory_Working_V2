import json
import structlog
from typing import Dict, Any, Optional
import sys
import traceback
import os

# Make sentry_sdk available for mocking in tests
try:
    import sentry_sdk
    SENTRY_AVAILABLE = True
except ImportError:
    sentry_sdk = None
    SENTRY_AVAILABLE = False

# A structured logger for this module, used for automatic error logging upon exception creation.
logger = structlog.get_logger(__name__)


class ReasonerErrorCode:
    """
    A collection of standard, structured error codes for the Reasoner application.
    Using a class with attributes provides a clear, discoverable, and auto-completable list of codes.
    """
    # General & Operational Errors
    GENERIC_ERROR = "GENERIC_ERROR"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
    INVALID_INPUT = "INVALID_INPUT"
    TIMEOUT = "TIMEOUT"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    CUDA_OOM = "CUDA_OOM"
    MODEL_NOT_INITIALIZED = "MODEL_NOT_INITIALIZED"
    CONTEXT_SIZE_EXCEEDED = "CONTEXT_SIZE_EXCEEDED"

    # Model Inference Errors
    MODEL_INFERENCE_FAILED = "MODEL_INFERENCE_FAILED"
    MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    PROMPT_VALIDATION_FAILED = "PROMPT_VALIDATION_FAILED"

    # Context & Sanitization Errors
    CONTEXT_SANITIZATION_FAILED = "CONTEXT_SANITIZATION_FAILED"
    CONTEXT_SCHEMA_VIOLATION = "CONTEXT_SCHEMA_VIOLATION"
    CONTEXT_MAX_DEPTH_EXCEEDED = "CONTEXT_MAX_DEPTH_EXCEEDED"
    CONTEXT_UNSUPPORTED_TYPE = "CONTEXT_UNSUPPORTED_TYPE"
    INVALID_CONTEXT_FORMAT = "INVALID_CONTEXT_FORMAT"

    # History & Persistence Errors
    DB_CONNECTION_FAILED = "DB_CONNECTION_FAILED"
    HISTORY_ERROR = "HISTORY_ERROR"  # Generic history error
    HISTORY_READ_FAILED = "HISTORY_READ_FAILED"
    HISTORY_WRITE_FAILED = "HISTORY_WRITE_FAILED"
    HISTORY_PRUNING_FAILED = "HISTORY_PRUNING_FAILED"
    HISTORY_PURGE_FAILED = "HISTORY_PURGE_FAILED"
    HISTORY_EXPORT_FAILED = "HISTORY_EXPORT_FAILED"

    # External Service Errors
    AUDIT_LOG_FAILED = "AUDIT_LOG_FAILED"
    METRICS_PUSH_FAILED = "METRICS_PUSH_FAILED"

    # Security & Compliance
    PERMISSION_DENIED = "PERMISSION_DENIED"
    SECURITY_VIOLATION = "SECURITY_VIOLATION"
    SENSITIVE_DATA_LEAK = "SENSITIVE_DATA_LEAK"


class ReasonerError(Exception):
    """
    A custom exception for the Reasoner application that includes a structured
    error code and a user-friendly message. It automatically logs itself upon
    creation and can hold a reference to the original underlying exception.
    """
    def __init__(self, message: str, code: str, original_exception: Optional[Exception] = None, **kwargs):
        """
        Initializes the ReasonerError instance and immediately logs the event.

        Args:
            message (str): A user-friendly message describing the error.
            code (str): A structured, machine-readable error code from ReasonerErrorCode.
            original_exception (Optional[Exception]): The original exception for chaining.
            **kwargs: Additional context to be included in the structured log.
        """
        self.message = message
        self.code = code
        self.original_exception = original_exception
        self.extra_kwargs = kwargs  # Store extra kwargs in a dedicated attribute.
        super().__init__(f"{message} (Code: {code})")

        # Automatically log the error with its structured context upon creation.
        # This ensures errors are captured at their source.
        # Pass True for exc_info to capture current traceback
        logger.error(
            "reasoner_error_occurred",
            message=message,
            code=code,
            exc_info=True,  # Always pass True as tests expect
            **kwargs
        )

        # If Sentry is available and configured, capture the exception
        # Check for REASONER_SENTRY_DSN environment variable as tests expect
        if SENTRY_AVAILABLE and sentry_sdk and os.environ.get("REASONER_SENTRY_DSN"):
            try:
                # The test expects a more complex Sentry integration with scope
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag("reasoner_error_code", code)
                    if original_exception:
                        scope.set_extra("original_exception", str(original_exception))
                    for key, value in kwargs.items():
                        scope.set_extra(key, value)
                    # Capture the ReasonerError itself
                    sentry_sdk.capture_exception(self)
            except Exception:
                pass  # Silently fail if Sentry isn't properly configured

    def __repr__(self) -> str:
        """Returns a detailed representation for debugging."""
        return (f"{self.__class__.__name__}(message='{self.message}', "
                f"code='{self.code}', "
                f"original_exception={repr(self.original_exception)})")

    def to_api_response(self, include_traceback: bool = False) -> Dict[str, Any]:
        """
        Converts the exception into a JSON-serializable dictionary for API responses.

        Args:
            include_traceback (bool): If True, includes the original exception's traceback.
                                      **SECURITY WARNING:** This is NOT recommended for production
                                      APIs as it can leak internal application details.
                                      Server-side logging (now automatic) should be used for debugging.

        Returns:
            Dict[str, Any]: A dictionary containing 'code' and 'message'.
        """
        response_body = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }

        # Build details dict only if there's something to include
        details = {}
        
        # Include extra_kwargs in details if any exist
        if self.extra_kwargs:
            details.update(self.extra_kwargs)
        
        if self.original_exception:
            details["original_exception"] = str(self.original_exception)

        if include_traceback and (self.original_exception or sys.exc_info()[2]):
            # Only try to include traceback if there's actually an exception to trace
            tb_str = None
            
            # First try to get the current exception traceback
            exc_type, exc_value, exc_traceback = sys.exc_info()
            if exc_traceback:
                tb_str = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
            
            # If that didn't work and we have an original exception, try to format it
            if not tb_str and self.original_exception:
                # Check if the original exception has __traceback__
                if hasattr(self.original_exception, '__traceback__'):
                    tb_str = ''.join(traceback.format_exception(
                        type(self.original_exception),
                        self.original_exception,
                        self.original_exception.__traceback__
                    ))
                else:
                    # Fallback to a simple format
                    tb_str = f"{type(self.original_exception).__name__}: {str(self.original_exception)}"
            
            # Only add traceback if we actually got one
            if tb_str:
                details["traceback"] = tb_str

        # Only add details to response if there are any
        if details:
            response_body["error"]["details"] = details
        
        return response_body

    def to_json(self, indent: Optional[int] = None) -> str:
        """
        Serializes the API response dictionary to a JSON string.

        Args:
            indent (Optional[int]): The indentation level for the JSON output.

        Returns:
            str: The JSON string representation of the error.
        """
        return json.dumps(self.to_api_response(), indent=indent)


# Example Usage & Test:
if __name__ == '__main__':
    # Configure structlog for simple console output for the example
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(indent=2),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    
    print("--- Testing ReasonerError Logging and Formatting ---")
    try:
        # Simulate an inner error
        try:
            1 / 0
        except ZeroDivisionError as e:
            # When this is created, it will be logged automatically.
            raise ReasonerError(
                "A mathematical operation failed.", 
                code=ReasonerErrorCode.UNEXPECTED_ERROR, 
                original_exception=e,
                user_id="test_user_123"  # example of extra context
            )
    except ReasonerError as re:
        print("\nCaught ReasonerError successfully.")
        print(f"Representation: {re!r}")
        print(f"Original exception was: {re.original_exception!r}")
        
        print("\n--- API Response (Secure - Default) ---")
        print(json.dumps(re.to_api_response(), indent=2))

        print("\n--- API Response (Insecure - with Traceback) ---")
        print(json.dumps(re.to_api_response(include_traceback=True), indent=2))
    print("\n--- Test Complete ---")
    print("Check the console output above for the automatically generated JSON log message.")