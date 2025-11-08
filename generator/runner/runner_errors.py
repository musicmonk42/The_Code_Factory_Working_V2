# runner/errors.py
# World-class, gold-standard structured error definitions for the runner system.
# Defines a custom exception hierarchy for consistent error reporting, logging, and API responses.

import datetime  # For timestamp_utc in error dictionary
from typing import Optional, Dict, Any, Type, List

# --- Error Code Registry (Gold Standard: Centralized, Unique Error Codes) ---
# This dictionary will store a mapping of unique error codes to their descriptions
# to prevent clashes and provide a single source of truth for all defined error codes.
ERROR_CODE_REGISTRY: Dict[str, str] = {}

# Alias for backward compatibility with code that imports error_codes
error_codes = ERROR_CODE_REGISTRY

def register_error_code(code: str, description: str):
    """Registers a unique error code with a description.
    Raises ValueError if the code is already registered.
    """
    if code in ERROR_CODE_REGISTRY:
        raise ValueError(f"Error code '{code}' is already registered with description: {ERROR_CODE_REGISTRY[code]}")
    ERROR_CODE_REGISTRY[code] = description

# --- OpenTelemetry Integration (Optional, but Gold Standard for Observability) ---
try:
    import opentelemetry.trace as trace
    # Assuming _tracer is initialized elsewhere (e.g., in runner/logging or main entrypoint)
    # If not, a basic no-op tracer should be used or passed in.
    _tracer = trace.get_tracer(__name__)
    HAS_OPENTELEMETRY = True
except ImportError:
    # Use a dummy tracer if OpenTelemetry is not installed to avoid runtime errors
    class NoOpSpan:
        def set_attribute(self, key, value): pass
        def set_status(self, status): pass
        def record_exception(self, exception): pass
        def end(self): pass
        def __enter__(self): return self
        def __exit__(self, exc_type, exc_val, exc_tb): pass

    class NoOpTracer:
        def start_as_current_span(self, name, *args, **kwargs): return NoOpSpan()
        def start_span(self, name, *args, **kwargs): return NoOpSpan()

    _tracer = NoOpTracer()
    HAS_OPENTELEMETRY = False
    # In a real system, you might want a more robust logging framework for missing deps
    import logging
    logging.getLogger(__name__).warning("OpenTelemetry not installed. Error tracing will be disabled.")


# --- Base Runner Exception ---
class RunnerError(Exception):
    """
    Base exception for all custom runner errors.
    Provides a standardized structure for error reporting and extensibility.
    """
    def __init__(self, error_code: str, detail: str, task_id: Optional[str] = None,
                 cause: Optional[Exception] = None, **kwargs: Any):
        # Register the error code if it hasn't been already
        # This is a defensive check; ideally, all codes are pre-registered below.
        if error_code not in ERROR_CODE_REGISTRY:
            register_error_code(error_code, detail) # Use detail as description for unknown codes

        self.error_code: str = error_code
        self.detail: str = detail
        self.task_id: Optional[str] = task_id
        self.cause: Optional[Exception] = cause
        self.extra_info: Dict[str, Any] = kwargs

        # Message for general exception logging/printing
        super().__init__(f"[{self.error_code}] {self.detail}" + (f" (Task: {self.task_id})" if self.task_id else ""))

        # Record the exception in the current OpenTelemetry span if available
        current_span = trace.get_current_span()
        if current_span:
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, self.detail))
            current_span.set_attribute("error.code", self.error_code)
            current_span.set_attribute("error.task_id", str(self.task_id))
            current_span.set_attribute("error.type", self.__class__.__name__)
            if self.cause:
                current_span.record_exception(self.cause)
            else:
                current_span.record_exception(self) # Record self if no specific cause given
            for key, value in self.extra_info.items():
                current_span.set_attribute(f"error.info.{key}", str(value)) # Convert to string for OTel attributes

    def as_dict(self) -> Dict[str, Any]:
        """
        Returns a dictionary representation of the error, suitable for
        structured logging, API responses, or inter-service communication.
        """
        error_dict = {
            "error_type": self.__class__.__name__,
            "error_code": self.error_code,
            "detail": self.detail,
            "task_id": self.task_id,
            "timestamp_utc": datetime.datetime.utcnow().isoformat() + 'Z'
        }
        if self.cause:
            error_dict["cause_exception"] = str(self.cause)
            error_dict["cause_type"] = type(self.cause).__name__
            # Add traceback if available and explicitly requested (e.g., in dev/debug mode)
            # import traceback
            # error_dict["cause_traceback"] = traceback.format_exc()
        error_dict.update(self.extra_info)
        return error_dict

# --- Specific Runner Exception Types ---

# Register common error codes
register_error_code("BACKEND_INIT_FAILURE", "Failed to initialize the execution backend.")
register_error_code("FRAMEWORK_UNSUPPORTED", "The specified or auto-detected test framework is not supported.")
register_error_code("TEST_EXECUTION_FAILED", "The test execution command returned a non-zero exit code or failed unexpectedly.")
register_error_code("PARSING_ERROR", "Failed to parse test results or coverage data.")
register_error_code("SETUP_FAILURE", "Environment setup within the backend failed.")
register_error_code("TASK_TIMEOUT", "The task exceeded its allocated execution time.")
register_error_code("DISTRIBUTED_COMMUNICATION_ERROR", "An error occurred during communication with a distributed worker or endpoint.")
register_error_code("PERSISTENCE_FAILURE", "Failed to load or save a persistent state (e.g., task queue).")
register_error_code("CONFIGURATION_ERROR", "An error occurred during configuration loading or validation.")
register_error_code("UNEXPECTED_ERROR", "An unhandled or unexpected error occurred within the runner.")
register_error_code("VALIDATION_ERROR", "Data validation failed for input or output contracts.") # For Pydantic validation errors
register_error_code("EXPORTER_FAILURE", "Failed to export metrics to an external system.")

# FIX: Register the missing LLM-related error codes
register_error_code("LLM_PROVIDER_ERROR", "The LLM provider API call failed.")
register_error_code("LLM_RATE_LIMIT", "Rate limit exceeded for the LLM provider.")
register_error_code("LLM_CIRCUIT_OPEN", "Circuit breaker is open for the LLM provider.")
register_error_code("LLM_PLUGIN_NOT_FOUND", "The specified LLM provider plugin is not loaded or available.")


class BackendError(RunnerError):
    """
    Raised when an issue occurs with the selected execution backend
    (e.g., Docker daemon unreachable, Kubernetes API error).
    """
    def __init__(self, detail: str, task_id: Optional[str] = None,
                 backend_type: Optional[str] = None, cause: Optional[Exception] = None, **kwargs: Any):
        super().__init__("BACKEND_INIT_FAILURE", detail, task_id=task_id,
                         backend_type=backend_type, cause=cause, **kwargs)

class FrameworkError(RunnerError):
    """
    Raised when the test framework is unsupported, not detected, or misconfigured.
    """
    def __init__(self, detail: str, task_id: Optional[str] = None,
                 framework_name: Optional[str] = None, cause: Optional[Exception] = None, **kwargs: Any):
        super().__init__("FRAMEWORK_UNSUPPORTED", detail, task_id=task_id,
                         framework_name=framework_name, cause=cause, **kwargs)

class TestExecutionError(RunnerError):
    """
    Raised when the test execution command fails, returns a non-zero code, or times out.
    """
    def __init__(self, detail: str, task_id: Optional[str] = None,
                 returncode: Optional[int] = None, stdout: str = "", stderr: str = "",
                 cause: Optional[Exception] = None, **kwargs: Any):
        # The base class also takes a 'cause' argument, which is now explicitly passed
        super().__init__("TEST_EXECUTION_FAILED", detail, task_id=task_id,
                         returncode=returncode, stdout_snippet=stdout[:500], stderr_snippet=stderr[:500],
                         cause=cause, **kwargs)

class ParsingError(RunnerError):
    """
    Raised when parsing test results, coverage data, or other output files fails.
    """
    def __init__(self, detail: str, task_id: Optional[str] = None,
                 parser_type: Optional[str] = None, cause: Optional[Exception] = None, **kwargs: Any):
        super().__init__("PARSING_ERROR", detail, task_id=task_id,
                         parser_type=parser_type, cause=cause, **kwargs)

class SetupError(RunnerError):
    """
    Raised when the execution environment setup (e.g., file transfer,
    custom setup command within the backend) fails.
    """
    def __init__(self, detail: str, task_id: Optional[str] = None,
                 stage: Optional[str] = None, cause: Optional[Exception] = None, **kwargs: Any):
        super().__init__("SETUP_FAILURE", detail, task_id=task_id,
                         setup_stage=stage, cause=cause, **kwargs)

class TimeoutError(RunnerError): # Renamed to TimeoutError to avoid conflict with Python's built-in
    """
    Raised when a task exceeds its allocated execution time.
    """
    def __init__(self, detail: str, task_id: Optional[str] = None,
                 timeout_seconds: Optional[int] = None, cause: Optional[Exception] = None, **kwargs: Any):
        super().__init__("TASK_TIMEOUT", detail, task_id=task_id,
                         timeout_seconds=timeout_seconds, cause=cause, **kwargs)

class DistributedError(RunnerError):
    """
    Raised when an issue occurs in distributed task processing
    (e.g., network error to coordinator, remote worker failure).
    """
    def __init__(self, detail: str, task_id: Optional[str] = None,
                 endpoint: Optional[str] = None, cause: Optional[Exception] = None, **kwargs: Any):
        super().__init__("DISTRIBUTED_COMMUNICATION_ERROR", detail, task_id=task_id,
                         endpoint=endpoint, cause=cause, **kwargs)

class PersistenceError(RunnerError):
    """
    Raised when saving or loading persistent state (e.g., task queue, results) fails.
    """
    def __init__(self, detail: str, task_id: Optional[str] = None,
                 file_path: Optional[str] = None, cause: Optional[Exception] = None, **kwargs: Any):
        super().__init__("PERSISTENCE_FAILURE", detail, task_id=task_id,
                         file_path=file_path, cause=cause, **kwargs)

class ConfigurationError(RunnerError):
    """
    Raised when an issue occurs during configuration loading or validation.
    """
    def __init__(self, detail: str, config_file: Optional[str] = None,
                 cause: Optional[Exception] = None, **kwargs: Any):
        super().__init__("CONFIGURATION_ERROR", detail, config_file=config_file,
                         cause=cause, **kwargs)

class ValidationError(RunnerError):
    """
    Raised when data validation fails for input payloads or internal data structures.
    This often wraps Pydantic validation errors.
    """
    def __init__(self, detail: str, task_id: Optional[str] = None,
                 field: Optional[str] = None, value: Any = None, cause: Optional[Exception] = None, **kwargs: Any):
        super().__init__("VALIDATION_ERROR", detail, task_id=task_id,
                         field=field, value=value, cause=cause, **kwargs)

# FIX: Add the missing LLMError class definition
class LLMError(RunnerError):
    """
    Raised when an issue occurs with an LLM provider, rate limit, or circuit breaker.
    """
    def __init__(self, detail: str, error_code: str = "LLM_PROVIDER_ERROR", 
                 task_id: Optional[str] = None, provider: Optional[str] = None, 
                 cause: Optional[Exception] = None, **kwargs: Any):
        # Ensure the error code is one of the valid LLM codes
        if error_code not in ["LLM_PROVIDER_ERROR", "LLM_RATE_LIMIT", "LLM_CIRCUIT_OPEN", "LLM_PLUGIN_NOT_FOUND"]:
            error_code = "LLM_PROVIDER_ERROR" # Default
        super().__init__(error_code, detail, task_id=task_id,
                         provider=provider, cause=cause, **kwargs)

# FIX: Add the missing ExporterError class definition
class ExporterError(RunnerError):
    """
    Raised when exporting metrics to an external system (e.g., Datadog, CloudWatch) fails.
    """
    def __init__(self, detail: str, task_id: Optional[str] = None,
                 exporter_name: Optional[str] = None, cause: Optional[Exception] = None, **kwargs: Any):
        super().__init__("EXPORTER_FAILURE", detail, task_id=task_id,
                         exporter_name=exporter_name, cause=cause, **kwargs)