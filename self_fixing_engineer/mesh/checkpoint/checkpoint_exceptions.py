# checkpoint_exceptions.py
"""
Custom exception handling module for the checkpoint management system.

This module provides a structured hierarchy of exceptions, integrated with observability
tools like OpenTelemetry, Prometheus, and structured logging with Structlog. It is
designed for robustness in mission-critical, distributed environments.

V2.1.0 Enhancements:
- Security: HMAC signing secret can now be sourced from the EXCEPTION_HMAC_SECRET
  environment variable. Integrated rotating file handler for logs to prevent disk exhaustion.
- Reliability: Added a circuit breaker pattern (PyBreaker) to the retry decorator
  to prevent cascading failures when downstream services are unavailable.
- Observability: Implemented alert throttling to prevent notification floods during
  system-wide outages. OpenTelemetry spans are now explicitly marked with an ERROR status.
- Maintainability: Expanded dependency version checks and improved documentation.

Setup and Configuration:
1.  Install dependencies:
    `pip install -r requirements.txt`

2.  Set environment variables:
    - CHECKPOINT_MAX_CONTEXT_SIZE: Max size of context data in bytes (e.g., 2048).
    - TENANT: The tenant identifier for metrics (e.g., 'production-api').
    - EXCEPTION_HMAC_SECRET: A secret key used for signing exception contexts.

3.  Configure alerting and logging in your application's entry point.
"""

__version__ = "2.1.0"

import asyncio
import hashlib
import hmac

# ---- Standard Library Imports ----
import json
import logging
import os
import time
from enum import Enum
from logging.handlers import RotatingFileHandler
from typing import Any, Awaitable, Callable, Dict, Optional

# ---- Third-Party Imports ----
import structlog

# Optional dependencies with availability flags and version checks
MIN_VERSIONS = {
    "opentelemetry.trace": "1.27.0",
    "structlog": "25.4.0",
}

try:
    from opentelemetry import __version__ as otel_version
    from opentelemetry import trace

    if otel_version < MIN_VERSIONS["opentelemetry.trace"]:
        logging.warning(
            f"OpenTelemetry version {otel_version} is outdated. Update to >= {MIN_VERSIONS['opentelemetry.trace']} is recommended."
        )
    OPENTELEMETRY_AVAILABLE = True
    TRACING_AVAILABLE = True  # Alias for test compatibility
except ImportError:
    trace = None
    OPENTELEMETRY_AVAILABLE = False
    TRACING_AVAILABLE = False

try:
    from prometheus_client import Counter

    PROMETHEUS_AVAILABLE = True
except ImportError:
    Counter = None
    PROMETHEUS_AVAILABLE = False

try:
    from tenacity import retry, stop_after_attempt, wait_exponential

    TENACITY_AVAILABLE = True
except ImportError:
    retry = None
    TENACITY_AVAILABLE = False

try:
    from pybreaker import CircuitBreaker, CircuitBreakerError

    PYBREAKER_AVAILABLE = True
except ImportError:
    CircuitBreaker = None
    CircuitBreakerError = None
    PYBREAKER_AVAILABLE = False

try:
    from cachetools import TTLCache

    CACHE_AVAILABLE = True
except ImportError:
    TTLCache = None
    CACHE_AVAILABLE = False


# ---- Local Application Imports ----
from .checkpoint_utils import scrub_data

# ---- Module-level Setup ----

# Configure structured logging with a rotating file handler to prevent log file bloat
log_handler = RotatingFileHandler(
    "exceptions.log", maxBytes=10 * 1024 * 1024, backupCount=5
)  # 10MB per file, 5 backups
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
root_logger = logging.getLogger()
root_logger.addHandler(log_handler)
root_logger.setLevel(logging.INFO)
logger = structlog.get_logger(__name__)
audit_logger = structlog.get_logger("audit")  # Add audit logger


# Prometheus metric (defined only if available)
EXCEPTION_COUNT = None
if PROMETHEUS_AVAILABLE:
    EXCEPTION_COUNT = Counter(
        "checkpoint_exceptions_total",
        "Total checkpoint exceptions",
        ["error_type", "error_code", "tenant", "severity"],
    )

# Circuit breaker for backend operations (defined only if available)
BREAKER = None
if PYBREAKER_AVAILABLE:
    BREAKER = CircuitBreaker(
        fail_max=5, reset_timeout=60
    )  # Opens after 5 failures, closes after 60s

# Alert throttling cache (defined only if available)
ALERT_CACHE = None
if CACHE_AVAILABLE:
    ALERT_CACHE = TTLCache(maxsize=10, ttl=60)  # Throttle alerts per error_code for 60s

# ---- Configurable Alerting System ----
ALERT_CALLBACK: Optional[Callable[[str, Dict], Awaitable[None]]] = None


def set_alert_callback(callback: Callable[[str, Dict], Awaitable[None]]):
    """Sets a global asynchronous callback function for operator alerts."""
    global ALERT_CALLBACK
    if not asyncio.iscoroutinefunction(callback):
        raise TypeError("Alert callback must be an async function (awaitable).")
    ALERT_CALLBACK = callback


# ---- Helper Functions ----
def _mask_long_string_values(data: Any) -> Any:
    """Recursively masks string values in a dict/list that look like tokens."""
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if isinstance(v, str):
                # Check for JWT pattern (three base64 parts separated by dots)
                if "." in v and len(v.split(".")) == 3 and len(v) > 30:
                    result[k] = "[MASKED]"
                elif len(v) > 20 and "-" in v:
                    result[k] = "[MASKED]"
                else:
                    result[k] = v
            else:
                result[k] = _mask_long_string_values(v)
        return result
    elif isinstance(data, list):
        return [_mask_long_string_values(item) for item in data]
    return data


# ---- Error Code Enumeration ----
class CheckpointErrorCode(Enum):
    GENERIC_ERROR = "GENERIC_ERROR"
    HASH_MISMATCH = "HASH_MISMATCH"
    HMAC_MISMATCH = "HMAC_MISMATCH"
    AUDIT_FAILURE = "AUDIT_FAILURE"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"


# ---- Base Exception Class ----
class CheckpointError(Exception):
    """
    Base class for all custom exceptions in the checkpoint management system.

    Example (Exception Chaining):
        try:
            risky_operation()
        except KeyError as e:
            raise CheckpointError("Config key missing", context={"key": "db_host"}) from e
    """

    MAX_CONTEXT_SIZE = int(os.environ.get("CHECKPOINT_MAX_CONTEXT_SIZE", 2048))

    def __init__(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        error_code: Optional[CheckpointErrorCode] = None,
        severity: str = "error",
    ):
        super().__init__(message)
        self.message = message
        scrubbed_context = scrub_data(context if context is not None else {})
        self.context = _mask_long_string_values(scrubbed_context)

        self.error_code = (
            error_code.value
            if isinstance(error_code, CheckpointErrorCode)
            else CheckpointErrorCode.GENERIC_ERROR.value
        )
        self.severity = severity
        self.context.update(
            {
                "timestamp": time.time(),
                "error_code": self.error_code,
                "error_type": self.__class__.__name__,
            }
        )

        # Check context size after adding metadata
        context_str = json.dumps(self.context, default=str)
        if len(context_str) > self.MAX_CONTEXT_SIZE:
            raise ValueError(f"Context size ({len(context_str)} bytes) exceeds limit.")

        if EXCEPTION_COUNT:
            tenant = self.context.get("tenant", os.environ.get("TENANT", "unknown"))
            EXCEPTION_COUNT.labels(
                self.__class__.__name__, self.error_code, tenant, self.severity
            ).inc()

        if OPENTELEMETRY_AVAILABLE and trace:
            span = trace.get_current_span()
            if span and span.is_recording():
                span.record_exception(self)
                # Mark span status as ERROR for better visibility in APM tools
                span.set_status(
                    trace.Status(
                        trace.StatusCode.ERROR,
                        description=f"{self.__class__.__name__}: {message}",
                    )
                )
                span.set_attribute("error.type", self.__class__.__name__)
                for k, v in self.context.items():
                    span.set_attribute(f"error.context.{k}", str(v))

    def __str__(self) -> str:
        base_info = {
            "message": self.message,
            "error_type": self.__class__.__name__,
            "error_code": self.error_code,
            "context": self.context,
        }
        # Include the cause of the exception if it exists (from chaining)
        if self.__cause__:
            base_info["cause"] = f"{type(self.__cause__).__name__}: {self.__cause__}"
        return json.dumps(base_info, default=str)

    def sign_context(self, secret: Optional[str] = None) -> str:
        """
        Generates an HMAC-SHA256 signature for the exception context.
        Pulls secret from the EXCEPTION_HMAC_SECRET environment variable if not provided.
        """
        secret = secret or os.environ.get("EXCEPTION_HMAC_SECRET")
        if not secret:
            raise ValueError(
                "HMAC secret not provided and EXCEPTION_HMAC_SECRET env var is not set."
            )
        context_bytes = json.dumps(self.context, sort_keys=True, default=str).encode("utf-8")
        return hmac.new(secret.encode("utf-8"), context_bytes, hashlib.sha256).hexdigest()

    @classmethod
    async def raise_with_alert(
        cls,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        error_code: Optional[CheckpointErrorCode] = None,
    ):
        """Initializes and raises an exception while triggering a throttled operator alert."""
        instance = cls(message, context, error_code)

        # Alert Throttling Logic
        alert_key = instance.error_code
        if ALERT_CACHE is not None:
            count = ALERT_CACHE.get(alert_key, 0) + 1
            ALERT_CACHE[alert_key] = count
            if count > 5:
                logger.warning(
                    "Alert flood detected, suppressing notification.",
                    alert_key=alert_key,
                    count=count,
                )
                raise instance  # Raise without alerting to avoid spam

        logger.critical(
            "Raising exception with operator alert",
            message=message,
            context=instance.context,
        )

        if ALERT_CALLBACK:
            try:
                await ALERT_CALLBACK(f"{cls.__name__}: {message}", instance.context)
            except Exception as e:
                logger.exception("Failed to execute alert callback", error=str(e))
        else:
            logger.warning("No alert callback configured.")

        if OPENTELEMETRY_AVAILABLE and trace:
            span = trace.get_current_span()
            if span and span.is_recording():
                span.add_event("alert_triggered", attributes={"message": message})

        raise instance


# ---- Specific Exception Subclasses ----


class CheckpointAuditError(CheckpointError):
    """Raised for critical audit or security-related failures."""

    def __init__(self, message: str, context: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Audit Failure: {message}",
            context,
            CheckpointErrorCode.AUDIT_FAILURE,
            severity="critical",
        )
        audit_logger.critical(
            "Security incident detected", audit_message=message, context=self.context
        )


class CheckpointBackendError(CheckpointError):
    """Raised when an underlying storage backend operation fails."""

    def __init__(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        error_code: CheckpointErrorCode = CheckpointErrorCode.BACKEND_UNAVAILABLE,
    ):
        super().__init__(f"Backend Error: {message}", context, error_code)


class CheckpointRetryableError(CheckpointBackendError):
    """A specialization for transient backend failures that are safe to retry."""

    pass


class CheckpointValidationError(CheckpointError):
    """Raised when checkpoint data fails schema validation."""

    def __init__(self, message: str, context: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Validation Error: {message}",
            context,
            CheckpointErrorCode.VALIDATION_FAILURE,
            severity="warning",
        )


# ---- Reliability Decorators ----


def retry_on_exception(max_attempts: int = 3, max_delay_seconds: int = 10):
    """
    Decorator to retry async functions with circuit breaker protection.

    It first checks the circuit breaker. If the circuit is closed, it attempts the
    operation. If a `CheckpointRetryableError` occurs, Tenacity will handle retries
    with exponential backoff. If failures cause the breaker to open, subsequent
    calls will fail instantly until the breaker's reset timeout expires.
    """
    if not TENACITY_AVAILABLE:
        logger.warning("Tenacity is not installed. @retry_on_exception is a no-op.")

        def noop_decorator(func):
            return func

        return noop_decorator

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def wrapper(*args, **kwargs):
            # Check circuit breaker BEFORE tenacity retry wrapper
            if BREAKER:
                # Check circuit state before calling
                if BREAKER.state == "open":
                    raise CheckpointBackendError(
                        "Circuit breaker is open",
                        context={"function": func.__name__},
                        error_code=CheckpointErrorCode.CIRCUIT_OPEN,
                    )

                try:
                    # Create the tenacity-wrapped function
                    @retry(
                        stop=stop_after_attempt(max_attempts),
                        wait=wait_exponential(multiplier=1, min=2, max=max_delay_seconds),
                        retry_error_cls=CheckpointRetryableError,
                    )
                    async def retryable_func(*inner_args, **inner_kwargs):
                        try:
                            result = await func(*inner_args, **inner_kwargs)
                            # Record success
                            BREAKER.call(lambda: None)
                            return result
                        except Exception:
                            # Record failure
                            try:
                                BREAKER.call(lambda: (_ for _ in ()).throw(e))
                            except:
                                pass
                            raise

                    return await retryable_func(*args, **kwargs)
                except Exception as e:
                    if BREAKER.state == "open":
                        raise CheckpointBackendError(
                            "Circuit breaker is open",
                            context={"function": func.__name__},
                            error_code=CheckpointErrorCode.CIRCUIT_OPEN,
                        ) from e
                    # Check if this is a circuit breaker error
                    # PyBreaker raises CircuitBreakerError when open
                    if (
                        PYBREAKER_AVAILABLE
                        and CircuitBreakerError
                        and isinstance(e, CircuitBreakerError)
                    ):
                        raise CheckpointBackendError(
                            "Circuit breaker is open",
                            context={"function": func.__name__},
                            error_code=CheckpointErrorCode.CIRCUIT_OPEN,
                        ) from e
                    raise
            else:
                # No circuit breaker, just use tenacity
                @retry(
                    stop=stop_after_attempt(max_attempts),
                    wait=wait_exponential(multiplier=1, min=2, max=max_delay_seconds),
                    retry_error_cls=CheckpointRetryableError,
                )
                async def retryable_func(*inner_args, **inner_kwargs):
                    return await func(*inner_args, **inner_kwargs)

                return await retryable_func(*args, **kwargs)

        return wrapper

    return decorator
