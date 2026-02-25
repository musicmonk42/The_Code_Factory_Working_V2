# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import datetime
import hashlib
import json
import logging
import os
import re
from collections import deque
from enum import Enum
from typing import Any, Dict, List, Optional, Union, get_args, get_origin

from prometheus_client import REGISTRY, Counter

# Get logger for this module - follows Python best practices by not configuring
# the root logger at module level, allowing the application entry point to control
# logging configuration and avoiding duplicate log messages
logger = logging.getLogger(__name__)


class Severity(str, Enum):
    """Enum representing severity levels for bug reports."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @classmethod
    def from_string(cls, value: str) -> "Severity":
        """Convert a string to a Severity enum, defaulting to MEDIUM if invalid."""
        try:
            return cls(value.lower())
        except ValueError:
            logger.warning(f"Invalid severity string '{value}', defaulting to MEDIUM.")
            return cls.MEDIUM


def get_or_create_metric(metric_class, name, documentation, labelnames=None):
    """
    Idempotently create or retrieve a Prometheus metric from the global registry.
    This prevents errors when a module is imported multiple times in the same process.
    """
    # Use REGISTRY._metrics to avoid issues with internal API changes
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]

    # If not found, create it
    try:
        if labelnames:
            metric = metric_class(name, documentation, labelnames)
        else:
            metric = metric_class(name, documentation)
        return metric
    except ValueError:
        # In a race condition, another thread might have created it.
        if name in REGISTRY._names_to_collectors:
            return REGISTRY._names_to_collectors[name]
        raise


# --- Prometheus Metrics Definitions ---
PII_REDACTION_COUNT = get_or_create_metric(
    Counter, "pii_redaction_count", "Total PII redactions", ["type"]
)
SETTINGS_VALIDATION_ERRORS = get_or_create_metric(
    Counter,
    "settings_validation_errors",
    "Total settings validation errors",
    ["setting_name"],
)


# Dummy SecretStr for local testing
try:
    from pydantic import SecretStr as PydanticSecretStr

    SecretStrBase = PydanticSecretStr
except ImportError:
    logger.warning(
        "Pydantic not available; using dummy SecretStr. Install Pydantic for production security."
    )

    class SecretStrBase:
        """
        A dummy class to simulate Pydantic's SecretStr for handling sensitive information.
        Prevents raw value from being logged. For production, consider Pydantic's SecretStr.
        """

        def __init__(self, value: str):
            # Emulate Pydantic's type coercion
            self._value = str(value)

        def get_secret_value(self) -> str:
            return self._value

        def __repr__(self) -> str:
            return "SecretStr('**********')"

        def __str__(self) -> str:
            return "**********"


class SecretStr(SecretStrBase):
    def __init__(self, value):
        if isinstance(value, str):
            super().__init__(value)
        else:
            super().__init__(str(value))


def parse_env(var: str, default: Any, type_hint: type) -> Any:
    """
    Parses an environment variable based on a type hint.
    """
    value = os.environ.get(var)
    if value is None:
        return default

    # Handle Optional[T] by getting the actual type
    origin_type = get_origin(type_hint)
    if origin_type is Union and type(None) in get_args(type_hint):
        type_hint = tuple(t for t in get_args(type_hint) if t is not type(None))

    # Special handling for common types
    if type_hint is bool:
        return value.strip().lower() in ("1", "true", "yes", "on")
    elif type_hint is int:
        return int(value)
    elif type_hint is float:
        return float(value)
    elif type_hint in (list, tuple):
        return [item.strip() for item in value.split(",")]
    elif type_hint is SecretStr:
        return SecretStr(value)
    else:
        return value


def parse_bool_env(var: str, default: bool = False) -> bool:
    """
    Parses a boolean environment variable with a default.
    Recognizes 'true', '1', 'yes', 'on' as True. Case-insensitive.
    """
    value = os.environ.get(var)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


# redact_pii — imported from shared to avoid duplicate implementations
from shared.security.pii_redactor import redact_pii  # noqa: E402


def validate_settings(settings_obj: Any, required_fields: Dict[str, type]) -> List[str]:
    """
    Validates a settings object against required fields and their types using modern type hints.
    """
    errors = []
    for field, expected_type in required_fields.items():
        if not hasattr(settings_obj, field):
            errors.append(f"Missing required setting: '{field}'")
            SETTINGS_VALIDATION_ERRORS.labels(setting_name=field).inc()
            continue

        value = getattr(settings_obj, field)
        origin_type = get_origin(expected_type)
        type_args = get_args(expected_type)

        is_valid = False
        # Handle Optional[T], which is Union[T, None]
        if origin_type is Union and type(None) in type_args:
            actual_types = tuple(t for t in type_args if t is not type(None))
            if value is None or isinstance(value, actual_types):
                is_valid = True
            else:
                errors.append(
                    f"Setting '{field}' has incorrect type. Expected one of {actual_types} or None, got {type(value)}."
                )
        # Handle regular types (int, str, bool, list, etc.)
        elif origin_type is None:
            if isinstance(value, expected_type):
                is_valid = True
            else:
                errors.append(
                    f"Setting '{field}' has incorrect type. Expected {expected_type}, got {type(value)}."
                )
        # Handle other generics like list, tuple, etc.
        else:
            if isinstance(value, origin_type):
                is_valid = True
            else:
                errors.append(
                    f"Setting '{field}' has incorrect container type. Expected {origin_type}, got {type(value)}."
                )

        if not is_valid:
            SETTINGS_VALIDATION_ERRORS.labels(setting_name=field).inc()

    return errors


def apply_settings_validation(settings_obj: Any) -> None:
    """
    Validates settings and raises a ValueError if any checks fail.
    First, it loads values from environment variables as overrides.
    """
    # 1. Define required fields and their types
    required_fields = {
        "DEBUG_MODE": bool,
        "SLACK_WEBHOOK_URL": Optional[str],
        "EMAIL_RECIPIENTS": List[str],
        "EMAIL_ENABLED": bool,
        "EMAIL_SENDER": str,
        "EMAIL_SMTP_SERVER": Optional[str],
        "EMAIL_SMTP_PORT": int,
        "EMAIL_USE_STARTTLS": bool,
        "EMAIL_SMTP_USERNAME": Optional[str],
        "EMAIL_SMTP_PASSWORD": Optional[SecretStr],
        "PAGERDUTY_ENABLED": bool,
        "PAGERDUTY_ROUTING_KEY": Optional[SecretStr],
        "ENABLED_NOTIFICATION_CHANNELS": tuple,
        "AUDIT_LOG_FILE_PATH": str,
        "AUDIT_DEAD_LETTER_FILE_PATH": str,
        "AUTO_FIX_ENABLED": bool,
        "NOTIFICATION_FAILURE_THRESHOLD": int,
        "NOTIFICATION_FAILURE_WINDOW_SECONDS": int,
        "RATE_LIMIT_ENABLED": bool,
        "RATE_LIMIT_WINDOW_SECONDS": int,
        "RATE_LIMIT_MAX_REPORTS": int,
        "AUDIT_LOG_ENABLED": bool,
        "AUDIT_LOG_FLUSH_INTERVAL_SECONDS": float,
        "AUDIT_LOG_BUFFER_SIZE": int,
        "AUDIT_LOG_MAX_FILE_SIZE_MB": int,
        "AUDIT_LOG_BACKUP_COUNT": int,
        "REMOTE_AUDIT_SERVICE_ENABLED": bool,
        "REMOTE_AUDIT_SERVICE_URL": Optional[str],
        "REMOTE_AUDIT_SERVICE_TIMEOUT": float,
        "REMOTE_AUDIT_DEAD_LETTER_ENABLED": bool,
        "SLACK_API_TIMEOUT_SECONDS": float,
        "EMAIL_API_TIMEOUT_SECONDS": float,
        "PAGERDUTY_API_TIMEOUT_SECONDS": float,
        "SLACK_FAILURE_RATE": float,
        "EMAIL_FAILURE_RATE": float,
        "PAGERDUTY_FAILURE_RATE": float,
        "ML_REMEDIATION_ENABLED": bool,
        "ML_MODEL_ENDPOINT": str,
    }

    # 2. Load environment variable overrides
    for field, expected_type in required_fields.items():
        env_var = f"ARBITER_{field.upper()}"
        if env_var in os.environ:
            try:
                env_value = parse_env(
                    env_var, getattr(settings_obj, field), expected_type
                )
                setattr(settings_obj, field, env_value)
            except (ValueError, TypeError) as e:
                logger.error(
                    json.dumps(
                        {
                            "event": "env_parse_error",
                            "field": field,
                            "env_var": env_var,
                            "error": str(e),
                        }
                    )
                )

    # 3. Validate the final settings object
    errors = validate_settings(settings_obj, required_fields)
    if errors:
        error_message = f"Invalid settings: {', '.join(errors)}"
        logger.error(
            json.dumps({"event": "settings_validation_failed", "errors": errors})
        )
        raise ValueError(error_message)


def validate_input_details(details: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Validates and sanitizes user-provided custom_details dictionary.

    Args:
        details (Optional[Dict[str, Any]]): The input dictionary to validate.

    Returns:
        Dict[str, Any]: The sanitized and validated dictionary.

    Raises:
        ValueError: If the input details are invalid or exceed the maximum depth.
    """
    if details is None:
        return {}
    if not isinstance(details, dict):
        logger.error(
            f"Invalid custom_details type: expected dict, got {type(details).__name__}"
        )
        raise ValueError(
            f"custom_details must be a dictionary, got {type(details).__name__}"
        )

    # Limit depth to prevent recursion issues
    def check_depth(obj: Any, depth: int, max_depth: int = 5) -> None:
        if depth > max_depth:
            raise ValueError(
                f"custom_details exceeds maximum nesting depth of {max_depth}"
            )
        if isinstance(obj, dict):
            for v in obj.values():
                check_depth(v, depth + 1, max_depth)
        elif isinstance(obj, list):
            for item in obj:
                check_depth(item, depth + 1, max_depth)

    check_depth(details, 0)
    return redact_pii(details)


# --- Error Classes ---
class BugManagerError(Exception):
    """Base class for all custom errors in the BugManager."""

    def __init__(
        self,
        message: str,
        error_id: Optional[str] = None,
        timestamp: Optional[str] = None,
    ):
        if not isinstance(message, str):
            raise TypeError("Error message must be a string.")
        super().__init__(message)
        self.error_id = (
            error_id
            if error_id is not None
            else hashlib.sha256(message.encode()).hexdigest()[:8]
        )
        self.timestamp = (
            timestamp
            if timestamp is not None
            else datetime.datetime.now(datetime.timezone.utc).isoformat()
        )
        self.message = message

    def __str__(self) -> str:
        return f"[{self.timestamp}] ({self.error_id}) {self.message}"


class NotificationError(BugManagerError):
    """Raised when a notification channel fails to send a message."""

    def __init__(
        self,
        message: str,
        channel: str,
        error_code: str = "GENERIC_ERROR",
        **kwargs: Any,
    ):
        super().__init__(message, **kwargs)
        self.channel = channel
        self.error_code = error_code


class CircuitBreakerOpenError(BugManagerError):
    """Raised when a circuit breaker is in an OPEN state."""

    def __init__(self, message: str, channel: Optional[str] = None, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.channel = channel


class RateLimitExceededError(BugManagerError):
    """Raised when a rate limit is exceeded."""

    def __init__(self, message: str, key: Optional[str] = None, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.key = key


class AuditLogError(BugManagerError):
    """Raised when an audit log operation fails."""

    def __init__(self, message: str, log_path: Optional[str] = None, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.log_path = log_path


class RemediationError(BugManagerError):
    """Raised when a remediation step or playbook fails."""

    def __init__(
        self,
        message: str,
        step_name: Optional[str] = None,
        playbook_name: Optional[str] = None,
        original_exception: Optional[Exception] = None,
        **kwargs: Any,
    ):
        super().__init__(message, **kwargs)
        self.step_name = step_name
        self.playbook_name = playbook_name
        self.original_exception = original_exception


class MLRemediationError(BugManagerError):
    """Raised when an ML-based remediation operation fails."""

    def __init__(
        self, message: str, model_endpoint: Optional[str] = None, **kwargs: Any
    ):
        super().__init__(message, **kwargs)
        self.model_endpoint = model_endpoint
