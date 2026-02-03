import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Set

import ujson as json  # Faster JSON
from opentelemetry import trace

# --- Global Kill-Switch for PII Redaction ---
# Provides a single environment variable to disable all redaction in emergencies or for performance profiling.
REDACTION_ENABLED = os.getenv("LOGGING_REDACTION_ENABLED", "true").lower() == "true"


# --- Custom Log Filter for Correlation ID ---
class LogCorrelationFilter(logging.Filter):
    """Adds OpenTelemetry Span ID and Trace ID to log records if available."""

    def filter(self, record):
        span = trace.get_current_span()

        # Handle different span types and contexts properly
        if span:
            try:
                # Try to get context using the standard method
                if hasattr(span, "get_span_context"):
                    context = span.get_span_context()
                elif hasattr(span, "_context"):
                    # Fallback for NonRecordingSpan in test environments
                    context = span._context
                else:
                    context = None

                if context and hasattr(context, "is_valid") and context.is_valid:
                    # Format with proper hex padding
                    record.trace_id = f"{context.trace_id:032x}"
                    record.span_id = f"{context.span_id:016x}"
                    record.correlation_id = f"{record.trace_id}-{record.span_id}"
                else:
                    self._set_no_trace_fields(record)
            except (AttributeError, Exception):
                # Failsafe for any unexpected span implementation
                self._set_no_trace_fields(record)
        else:
            self._set_no_trace_fields(record)

        return True

    def _set_no_trace_fields(self, record):
        """Set default values when no valid trace context is available."""
        record.trace_id = "no-trace"
        record.span_id = "no-span"
        record.correlation_id = "no-trace-no-span"


# --- Structured JSON Formatter ---
class JSONFormatter(logging.Formatter):
    """Formats log records as a single line of JSON."""

    def format(self, record):
        log_object = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "trace_id": getattr(record, "trace_id", "no-trace"),
            "span_id": getattr(record, "span_id", "no-span"),
        }

        # Attempt to parse the message as JSON; otherwise, treat as a plain string.
        try:
            msg_data = json.loads(record.getMessage())
            # If the message is a JSON dict, merge it into the log object.
            if isinstance(msg_data, dict):
                log_object.update(msg_data)
            else:
                log_object["message"] = msg_data
        except (json.JSONDecodeError, TypeError):
            log_object["message"] = record.getMessage()

        # Add exception info if present
        if record.exc_info:
            log_object["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_object)


# --- PII Redaction Filter ---
class PIIRedactorFilter(logging.Filter):
    """
    Redacts sensitive PII from log records. Now with recursion safety and thread-safe dynamic configuration.
    - SENSITIVE_KEYS are reloaded periodically from PII_SENSITIVE_KEYS env var.
    - EXTRA_REGEX_PATTERNS are loaded from PII_EXTRA_REGEX_PATTERNS env var.
    - Redaction can be disabled globally by setting LOGGING_REDACTION_ENABLED=false.
    """

    REDACTION_STRING = "[REDACTED]"
    MAX_RECURSION_DEPTH = 20

    BASE_PII_REGEX_PATTERNS = [
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
        re.compile(r"\b(?:\d{3}[-.\s]?){2}\d{4}\b"),
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"),
        re.compile(r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b"),
        re.compile(
            r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|6(?:011|5[0-9]{2})[0-9]{12}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|(?:2131|1800|35\d{3})\d{11})\b"
        ),
    ]

    DEFAULT_SENSITIVE_KEYS = [
        "agent_id",
        "session_id",
        "user_id",
        "decision_trace",
        "user_feedback",
        "sensitive_info_field",
        "email",
        "phone_number",
        "address",
        "ssn",
        "credit_card_number",
        "ip_address",
        "password",
        "api_key",
        "token",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sensitive_keys: Set[str] = set()
        self._all_regex_patterns: List[re.Pattern] = []
        self._config_cache_ttl_seconds = 60
        self._last_config_load_time = 0
        self._config_lock = threading.Lock()
        self._load_config()

    def _load_config(self):
        """
        Loads sensitive keys and extra regex patterns from environment variables.
        This method is thread-safe and caches the result for performance.
        """
        with self._config_lock:
            now = time.time()
            if (now - self._last_config_load_time) < self._config_cache_ttl_seconds:
                return

            keys_str = os.getenv("PII_SENSITIVE_KEYS", "")
            if keys_str:
                self._sensitive_keys = {
                    k.strip() for k in keys_str.split(",") if k.strip()
                }
            else:
                # Use defaults if env var is empty or not set
                self._sensitive_keys = set(self.DEFAULT_SENSITIVE_KEYS)

            extra_patterns_json = os.getenv("PII_EXTRA_REGEX_PATTERNS", "[]")
            extra_patterns = []
            try:
                extra_patterns_str_list = json.loads(extra_patterns_json)
                if isinstance(extra_patterns_str_list, list):
                    for pattern in extra_patterns_str_list:
                        try:
                            extra_patterns.append(re.compile(pattern))
                        except re.error as e:
                            logging.getLogger(__name__).warning(
                                f"Invalid regex in PII_EXTRA_REGEX_PATTERNS: '{pattern}'. Error: {e}"
                            )
                else:
                    logging.getLogger(__name__).warning(
                        "PII_EXTRA_REGEX_PATTERNS must be a JSON list of strings."
                    )
            except json.JSONDecodeError as e:
                logging.getLogger(__name__).warning(
                    f"Could not parse PII_EXTRA_REGEX_PATTERNS JSON: {e}"
                )

            self._all_regex_patterns = self.BASE_PII_REGEX_PATTERNS + extra_patterns
            self._last_config_load_time = now
            logging.getLogger(__name__).info("PII redaction configuration reloaded.")

    def filter(self, record):
        if not REDACTION_ENABLED:
            return True

        self._load_config()

        try:
            if hasattr(record, "msg"):
                record.msg = self._redact_value(record.msg)
            if hasattr(record, "details"):
                record.details = self._redact_value(record.details)
            if isinstance(record.args, (list, tuple)):
                record.args = tuple([self._redact_value(arg) for arg in record.args])
        except Exception:
            # Failsafe: if redaction fails for any reason, do not crash the application.
            # A more robust implementation could log this error to a separate, secure channel.
            pass

        return True

    def _redact_value(self, value: Any, seen: Set[int] = None, depth: int = 0) -> Any:
        """
        Recursively redacts a value based on its type, with protection against circular references and excessive depth.
        """
        if depth > self.MAX_RECURSION_DEPTH:
            return "[MAX RECURSION DEPTH]"

        if seen is None:
            seen = set()

        if id(value) in seen:
            return "[CIRCULAR REFERENCE]"

        if isinstance(value, dict):
            seen.add(id(value))
            redacted = self._redact_dict(value, seen, depth + 1)
            seen.remove(id(value))
            return redacted
        if isinstance(value, str):
            # First try to parse as JSON
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    redacted = self._redact_value(parsed, seen, depth + 1)
                    return json.dumps(redacted)
            except (json.JSONDecodeError, TypeError):
                pass
            # If not JSON, apply regex redaction
            return self._redact_string_with_regex(value)
        if isinstance(value, list):
            seen.add(id(value))
            redacted = [self._redact_value(item, seen, depth + 1) for item in value]
            seen.remove(id(value))
            return redacted
        return value

    def _redact_dict(
        self, data: Dict[str, Any], seen: Set[int], depth: int
    ) -> Dict[str, Any]:
        """Recursively redacts sensitive keys and values within a dictionary."""
        redacted_data = {}
        for key, value in data.items():
            if key in self._sensitive_keys:
                redacted_data[key] = self.REDACTION_STRING
            else:
                redacted_data[key] = self._redact_value(value, seen, depth)
        return redacted_data

    def _redact_string_with_regex(self, text: str) -> str:
        """Applies all defined PII regex patterns to a string."""
        for pattern in self._all_regex_patterns:
            text = pattern.sub(self.REDACTION_STRING, text)
        return text


# Example of how to use the filter (for demonstration/testing)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_logger = logging.getLogger("test_logger")
    test_logger.propagate = False

    handler = logging.StreamHandler()
    # Use the new JSONFormatter
    handler.setFormatter(JSONFormatter())

    handler.addFilter(LogCorrelationFilter())
    handler.addFilter(PIIRedactorFilter())
    test_logger.addHandler(handler)

    # Use centralized OpenTelemetry configuration
    from self_fixing_engineer.arbiter.otel_config import get_tracer

    tracer = get_tracer(__name__)

    test_logger.info("--- Starting PII Redaction and Structured Logging Test ---")

    with tracer.start_as_current_span("test_span_with_pii"):
        test_logger.info("A simple log message within a trace.")

        # Test logging a dictionary, which gets merged by the JSONFormatter
        test_logger.info(
            json.dumps(
                {
                    "event": "user_login",
                    "user_id": "user-123",
                    "email": "test@example.com",
                    "ip_address": "192.168.1.1",
                    "details": {"sensitive_info_field": "secret_data"},
                }
            )
        )

        # Test circular reference protection
        test_logger.info("\n--- Testing Circular Reference Protection ---")
        circular_obj = {}
        circular_obj["a"] = 1
        circular_obj["myself"] = circular_obj
        test_logger.info(json.dumps({"event": "circular_test", "data": circular_obj}))
