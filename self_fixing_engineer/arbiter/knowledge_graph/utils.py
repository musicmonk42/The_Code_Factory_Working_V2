import asyncio
import base64
import collections
import contextvars
import datetime
import json
import logging
import re
import sys
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from opentelemetry import trace
from arbiter.otel_config import get_tracer_safe
from prometheus_client import REGISTRY, Counter, Gauge, Histogram

from .config import Config, MultiModalData, SensitiveValue

try:
    from pydantic import BaseModel as PydanticBaseModel
    from pydantic import ValidationError
except ImportError:
    ValidationError = None
    PydanticBaseModel = None

tracer = get_tracer_safe(__name__)
trace_id_var = contextvars.ContextVar("trace_id", default=None)


# --- Logging Setup ---
class ContextVarFormatter(logging.Formatter):
    def format(self, record):
        record.trace_id = trace_id_var.get()
        return super().format(record)


logging.basicConfig(level=logging.INFO, handlers=[])
handler = logging.StreamHandler(sys.stdout)
formatter = ContextVarFormatter(
    "%(asctime)s - [%(levelname)s] - [%(trace_id)s] - %(message)s"
)
handler.setFormatter(formatter)
logging.getLogger().addHandler(handler)
logger = logging.getLogger(__name__)


# --- Prometheus Metrics ---
def get_or_create_metric(
    metric_type, name, documentation, labelnames=None, buckets=None
):
    labelnames = labelnames or []
    if name in REGISTRY._names_to_collectors:
        existing_metric = REGISTRY._names_to_collectors[name]
        if isinstance(existing_metric, metric_type):
            return existing_metric
        logger.warning(f"Metric '{name}' already registered with a different type.")
        return existing_metric
    if metric_type == Histogram:
        return metric_type(
            name,
            documentation,
            labelnames=labelnames,
            buckets=buckets or Histogram.DEFAULT_BUCKETS,
        )
    elif metric_type == Counter:
        return metric_type(name, documentation, labelnames=labelnames)
    elif metric_type == Gauge:
        return metric_type(name, documentation, labelnames=labelnames)
    raise ValueError(f"Unsupported metric type: {metric_type}")


AGENT_METRICS = {
    "agent_predict_total": get_or_create_metric(
        Counter, "agent_predict_total", "Total agent prediction calls", ["agent_id"]
    ),
    "agent_predict_success": get_or_create_metric(
        Counter,
        "agent_predict_success_total",
        "Total successful agent prediction calls",
        ["agent_id"],
    ),
    "agent_predict_errors": get_or_create_metric(
        Counter,
        "agent_predict_errors_total",
        "Total agent prediction errors",
        ["agent_id", "error_code"],
    ),
    "agent_predict_duration_seconds": get_or_create_metric(
        Histogram,
        "agent_predict_duration_seconds",
        "Agent prediction duration in seconds",
        ["agent_id"],
    ),
    "agent_step_duration_seconds": get_or_create_metric(
        Histogram, "agent_step_duration_seconds", "Duration of agent steps", ["step"]
    ),
    "agent_team_task_duration_seconds": get_or_create_metric(
        Histogram, "agent_team_task_duration_seconds", "Duration of agent team tasks"
    ),
    "agent_team_task_errors_total": get_or_create_metric(
        Counter,
        "agent_team_task_errors_total",
        "Total agent team task errors",
        ["error_code"],
    ),
    "agent_creation_duration_seconds": get_or_create_metric(
        Histogram, "agent_creation_duration_seconds", "Duration of agent creation"
    ),
    "llm_calls_total": get_or_create_metric(
        Counter, "llm_calls_total", "Total LLM API calls", ["provider", "model"]
    ),
    "llm_errors_total": get_or_create_metric(
        Counter, "llm_errors_total", "Total LLM API call errors", ["provider", "model"]
    ),
    "llm_call_latency_seconds": get_or_create_metric(
        Histogram,
        "llm_call_latency_seconds",
        "LLM API call latency in seconds",
        ["provider", "model"],
    ),
    "key_rotation_events": get_or_create_metric(
        Counter, "llm_key_rotation_events_total", "Total API key rotations"
    ),
    "bad_keys_marked": get_or_create_metric(
        Counter,
        "llm_bad_keys_marked_total",
        "Total API keys marked as bad",
        ["provider"],
    ),
    "state_backend_operations_total": get_or_create_metric(
        Counter,
        "state_backend_operations_total",
        "Total state backend operations",
        ["operation", "backend_type"],
    ),
    "state_backend_errors_total": get_or_create_metric(
        Counter,
        "state_backend_errors_total",
        "Total state backend errors",
        ["operation", "backend_type", "error_code"],
    ),
    "state_backend_latency_seconds": get_or_create_metric(
        Histogram,
        "state_backend_latency_seconds",
        "Latency of state backend operations",
        ["operation", "backend_type"],
    ),
    "meta_learning_corrections_logged_total": get_or_create_metric(
        Counter, "meta_learning_corrections_logged_total", "Total corrections logged"
    ),
    "meta_learning_train_duration_seconds": get_or_create_metric(
        Histogram,
        "meta_learning_train_duration_seconds",
        "Duration of meta-learning training",
    ),
    "meta_learning_train_errors_total": get_or_create_metric(
        Counter,
        "meta_learning_train_errors_total",
        "Total meta-learning training errors",
    ),
    "sensitive_data_redaction_total": get_or_create_metric(
        Counter,
        "sensitive_data_redaction_total",
        "Total sensitive data redactions",
        ["redaction_type"],
    ),
    "multimodal_data_processed_total": get_or_create_metric(
        Counter,
        "multimodal_data_processed_total",
        "Total multi-modal data items processed",
        ["data_type"],
    ),
    "mm_processor_failures_total": get_or_create_metric(
        Counter,
        "mm_processor_failures_total",
        "Total multi-modal processing failures",
        ["data_type", "error_type"],
    ),
    "agent_last_success_timestamp": get_or_create_metric(
        Gauge,
        "agent_last_success_timestamp",
        "Last successful prediction timestamp",
        ["agent_id"],
    ),
    "agent_last_error_timestamp": get_or_create_metric(
        Gauge, "agent_last_error_timestamp", "Last error timestamp", ["agent_id"]
    ),
    "agent_active_sessions_current": get_or_create_metric(
        Gauge, "agent_active_sessions_current", "Current active sessions"
    ),
    "agent_heartbeat_timestamp": get_or_create_metric(
        Gauge,
        "agent_heartbeat_timestamp",
        "Last agent activity timestamp",
        ["agent_id"],
    ),
}


class AgentErrorCode(str, Enum):
    UNEXPECTED_ERROR = "AGENT_UNEXPECTED_ERROR"
    TIMEOUT = "AGENT_TIMEOUT"
    INVALID_INPUT = "AGENT_INVALID_INPUT"
    UNSUPPORTED_PERSONA = "AGENT_UNSUPPORTED_PERSONA"
    STATE_LOAD_FAILED = "AGENT_STATE_LOAD_FAILED"
    STATE_SAVE_FAILED = "AGENT_STATE_SAVE_FAILED"
    LLM_INIT_FAILED = "LLM_INIT_FAILED"
    LLM_CALL_FAILED = "LLM_CALL_FAILED"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_KEY_INVALID = "LLM_KEY_INVALID"
    LLM_UNSUPPORTED_PROVIDER = "LLM_UNSUPPORTED_PROVIDER"
    LLM_BAD_RESPONSE = "LLM_BAD_RESPONSE"
    LIB_IMPORT_FAILED = "LIB_IMPORT_FAILED"
    MM_PROCESSING_FAILED = "MM_PROCESSING_FAILED"
    MM_UNSUPPORTED_DATA = "MM_UNSUPPORTED_DATA"
    MM_DATA_TOO_LARGE = "MM_DATA_TOO_LARGE"
    REFLECTION_FAILED = "REFLECTION_FAILED"
    CRITIQUE_FAILED = "CRITIQUE_FAILED"
    CORRECTION_FAILED = "CORRECTION_FAILED"
    PROMPT_INJECTION_DETECTED = "PROMPT_INJECTION_DETECTED"
    INVALID_CONTEXT_FORMAT = "INVALID_CONTEXT_FORMAT"
    CONTEXT_SANITIZATION_FAILED = "CONTEXT_SANITIZATION_FAILED"
    CONTEXT_MAX_DEPTH_EXCEEDED = "CONTEXT_MAX_DEPTH_EXCEEDED"
    CONTEXT_UNSUPPORTED_TYPE = "CONTEXT_UNSUPPORTED_TYPE"
    CONTEXT_SCHEMA_VIOLATION = "CONTEXT_SCHEMA_VIOLATION"
    PII_EXPOSED = "PII_EXPOSED"
    EXECUTOR_BROKEN = "EXECUTOR_BROKEN"


class AgentCoreException(Exception):
    def __init__(
        self,
        message: str,
        code: AgentErrorCode,
        original_exception: Optional[Exception] = None,
    ):
        self.message = message
        self.code = code
        self.original_exception = original_exception
        super().__init__(f"{message} (Code: {code.value})")


def datetime_now() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")


async def async_with_retry(
    func: Callable[..., Awaitable[Any]],
    retries: int = 3,
    delay: float = 1,
    backoff: int = 2,
    log_context: Optional[Dict[str, Any]] = None,
) -> Any:
    """
    Retry an async function with exponential backoff.

    Args:
        func: Async function to retry
        retries: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Backoff multiplier for exponential delay
        log_context: Optional context for logging

    Returns:
        Result from the function

    Raises:
        The last exception if all retries fail
    """
    log_context = log_context or {}
    for attempt in range(retries):
        try:
            return await func()
        except Exception as e:
            current_trace_id = trace_id_var.get()
            logger.warning(
                json.dumps(
                    {
                        "event": "async_retry_failed",
                        "attempt": attempt + 1,
                        "max_retries": retries,
                        "error": str(e),
                        "context": log_context,
                        "trace_id": current_trace_id,
                    }
                )
            )
            if attempt == retries - 1:
                logger.error(
                    json.dumps(
                        {
                            "event": "async_operation_failed_after_retries",
                            "error": str(e),
                            "context": log_context,
                            "trace_id": current_trace_id,
                        }
                    )
                )
                raise
            await asyncio.sleep(delay * (backoff**attempt))


# --- PII Redaction ---
# Lazy initialization to avoid issues during test collection
_PII_SENSITIVE_KEYS = None
_PII_SENSITIVE_PATTERNS = None


def _get_pii_sensitive_keys():
    """Get PII sensitive keys with lazy initialization."""
    global _PII_SENSITIVE_KEYS
    if _PII_SENSITIVE_KEYS is None:
        try:
            _PII_SENSITIVE_KEYS = [k.lower() for k in Config.PII_SENSITIVE_KEYS]
        except (AttributeError, TypeError):
            # Fallback if Config is not properly initialized (e.g., during tests)
            _PII_SENSITIVE_KEYS = ["email", "password", "name", "ssn", "credit_card", "api_key"]
    return _PII_SENSITIVE_KEYS


def _get_pii_sensitive_patterns():
    """Get PII sensitive patterns with lazy initialization."""
    global _PII_SENSITIVE_PATTERNS
    if _PII_SENSITIVE_PATTERNS is None:
        try:
            _PII_SENSITIVE_PATTERNS = [
                re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),  # Email
                re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),  # Phone number
                re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),  # Credit card
            ]
        except Exception:
            # Fallback if re.compile fails
            _PII_SENSITIVE_PATTERNS = []
    return _PII_SENSITIVE_PATTERNS


def _redact_sensitive_pii(key: str, value: Any) -> Any:
    key_lower = key.lower()
    pii_keys = _get_pii_sensitive_keys()
    if key_lower in pii_keys:
        AGENT_METRICS["sensitive_data_redaction_total"].labels(
            redaction_type="key"
        ).inc()
        logger.warning(
            f"PII redacted for key '{key}' (GDPR mode: {Config.GDPR_MODE}). Trace ID: {trace_id_var.get()}"
        )
        return "[PII_REDACTED_KEY]"
    if isinstance(value, str):
        pii_patterns = _get_pii_sensitive_patterns()
        for pattern in pii_patterns:
            if pattern.search(value):
                AGENT_METRICS["sensitive_data_redaction_total"].labels(
                    redaction_type="pattern"
                ).inc()
                logger.warning(
                    f"PII pattern redacted for key '{key}' (GDPR mode: {Config.GDPR_MODE}). Trace ID: {trace_id_var.get()}"
                )
                return "[PII_REDACTED_PATTERN_MATCH]"
    return value


async def _sanitize_context(
    context: Dict[str, Any],
    max_size_bytes: int = 4096,
    redact_keys: Optional[List[str]] = None,
    redact_patterns: Optional[List[str]] = None,
    max_nesting_depth: int = 10,
    allowed_primitive_types: Tuple[type, ...] = (
        str,
        int,
        float,
        bool,
        type(None),
        SensitiveValue,
    ),
    context_schema_model: Optional[Any] = None,
) -> Dict[str, Any]:
    with tracer.start_as_current_span("sanitize_context"):
        redact_keys_lower = [k.lower() for k in (redact_keys or [])]
        compiled_patterns = [
            re.compile(p, re.IGNORECASE) for p in (redact_patterns or [])
        ]

        def _redact_value_and_pii(key: str, value: Any) -> Any:
            if isinstance(value, SensitiveValue):
                AGENT_METRICS["sensitive_data_redaction_total"].labels(
                    redaction_type="sensitive_value"
                ).inc()
                return str(value)
            value_to_check = (
                value.get_actual_value() if isinstance(value, SensitiveValue) else value
            )
            value_checked_for_pii = _redact_sensitive_pii(key, value_to_check)
            if value_checked_for_pii != value_to_check:
                return value_checked_for_pii
            key_lower = key.lower()
            if key_lower in redact_keys_lower:
                AGENT_METRICS["sensitive_data_redaction_total"].labels(
                    redaction_type="custom_key"
                ).inc()
                return "[REDACTED_CUSTOM_KEY]"
            if isinstance(value, str):
                for pattern in compiled_patterns:
                    if pattern.search(value):
                        AGENT_METRICS["sensitive_data_redaction_total"].labels(
                            redaction_type="custom_pattern"
                        ).inc()
                        return "[REDACTED_CUSTOM_PATTERN_MATCH]"
            return value

        def _json_serializable_converter(obj: Any, current_depth: int = 0) -> Any:
            if current_depth > max_nesting_depth:
                logger.warning(
                    f"Max nesting depth exceeded. Trace ID: {trace_id_var.get()}"
                )
                AGENT_METRICS["agent_predict_errors"].labels(
                    agent_id="context_sanitization",
                    error_code=AgentErrorCode.CONTEXT_MAX_DEPTH_EXCEEDED.value,
                ).inc()
                return "[MAX_DEPTH_EXCEEDED]"
            if isinstance(obj, allowed_primitive_types):
                return _redact_value_and_pii("root", obj)
            if isinstance(obj, MultiModalData):
                return obj.model_dump_for_log()
            if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
                return obj.isoformat()
            if isinstance(obj, collections.abc.Coroutine):
                logger.warning(
                    f"Coroutine found in context. Trace ID: {trace_id_var.get()}"
                )
                return f"<Coroutine at {hex(id(obj))}>"
            if isinstance(obj, dict):
                return {
                    k: _redact_value_and_pii(
                        k, _json_serializable_converter(v, current_depth + 1)
                    )
                    for k, v in obj.items()
                }
            if isinstance(obj, list):
                return [
                    _redact_value_and_pii(
                        str(index),
                        _json_serializable_converter(elem, current_depth + 1),
                    )
                    for index, elem in enumerate(obj)
                ]
            if PydanticBaseModel and isinstance(obj, PydanticBaseModel):
                return obj.model_dump()
            logger.warning(
                f"Unsupported type {type(obj)}. Trace ID: {trace_id_var.get()}"
            )
            AGENT_METRICS["agent_predict_errors"].labels(
                agent_id="context_sanitization",
                error_code=AgentErrorCode.CONTEXT_UNSUPPORTED_TYPE.value,
            ).inc()
            return str(obj)

        try:
            if context_schema_model:
                context = context_schema_model(**context).model_dump()
                logger.debug(
                    f"Context validated against schema. Trace ID: {trace_id_var.get()}"
                )
            sanitized_context = _json_serializable_converter(context)
            context_json = json.dumps(
                sanitized_context, sort_keys=True, ensure_ascii=False
            )
            if len(context_json.encode("utf-8")) > max_size_bytes:
                logger.warning(
                    f"Context size exceeds limit ({len(context_json.encode('utf-8'))} bytes). Truncating. Trace ID: {trace_id_var.get()}"
                )
                truncated_bytes = context_json.encode("utf-8")[:max_size_bytes]
                truncated_json_str = truncated_bytes.decode("utf-8", errors="ignore")
                if truncated_json_str.endswith(('"', "'", "\\")):
                    last_quote_idx = max(
                        truncated_json_str.rfind('"'), truncated_json_str.rfind("'")
                    )
                    if last_quote_idx > truncated_json_str.rfind(
                        "{"
                    ) and last_quote_idx > truncated_json_str.rfind("["):
                        truncated_json_str = truncated_json_str[:last_quote_idx]
                open_braces = truncated_json_str.count("{")
                close_braces = truncated_json_str.count("}")
                open_brackets = truncated_json_str.count("[")
                close_brackets = truncated_json_str.count("]")
                if open_braces > close_braces:
                    truncated_json_str += "}" * (open_braces - close_braces)
                if open_brackets > close_brackets:
                    truncated_json_str += "]" * (open_brackets - close_brackets)
                try:
                    return json.loads(truncated_json_str)
                except json.JSONDecodeError:
                    logger.error(
                        f"Truncation broke JSON. Trace ID: {trace_id_var.get()}"
                    )
                    AGENT_METRICS["agent_predict_errors"].labels(
                        agent_id="context_sanitization",
                        error_code=AgentErrorCode.INVALID_CONTEXT_FORMAT.value,
                    ).inc()
                    return {
                        "_truncated_context_error": "Context too large and could not be truncated."
                    }
            return json.loads(context_json)
        except AgentCoreException:
            raise
        except json.JSONDecodeError as e:
            raise AgentCoreException(
                f"Invalid context format: {e}",
                code=AgentErrorCode.INVALID_CONTEXT_FORMAT,
                original_exception=e,
            )
        except Exception as e:
            raise AgentCoreException(
                f"Failed to sanitize context: {e}",
                code=AgentErrorCode.CONTEXT_SANITIZATION_FAILED,
                original_exception=e,
            )


def _sanitize_user_input(user_input: str) -> str:
    original_input = user_input

    # Remove HTML tags including script tags
    html_tag_pattern = r"<[^>]+>"
    user_input = re.sub(html_tag_pattern, "", user_input)

    # Remove path traversal attempts
    user_input = user_input.replace("../", "").replace("..\\", "")

    injection_patterns = [
        r"ignore all previous instructions",
        r"disregard all prior rules",
        r"you are now a different persona",
        r"act as a",
        r"ignore the above",
        r"forget everything",
        r"\[INST\]",
        r"\[/INST\]",
        r"';\s*DROP\s*TABLE",
        r"`?rm\s+-rf`?",
        r"sudo\s+.*",
        r"eval\s*\(",
    ]
    for pattern in injection_patterns:
        user_input = re.sub(pattern, "", user_input, flags=re.IGNORECASE)

    user_input = user_input.replace("```", "` ` `").replace("---", "- - -")

    if original_input != user_input:
        logger.warning(f"Sanitized prompt injection. Trace ID: {trace_id_var.get()}")
        AGENT_METRICS["sensitive_data_redaction_total"].labels(
            redaction_type="prompt_injection"
        ).inc()

    return user_input


class AuditLedgerClient:
    def __init__(self, ledger_url: str = str(Config.AUDIT_LEDGER_URL)):
        self.ledger_url = ledger_url
        self._logger = logging.getLogger("AuditLedgerClient")
        self._logger.info(f"AuditLedgerClient initialized for {ledger_url}.")

    async def log_event(
        self, event_type: str, details: Dict[str, Any], operator: str = "system"
    ) -> bool:
        try:
            current_trace_id = trace_id_var.get()
            log_entry = {
                "AUDIT_LOG_EVENT": event_type,
                "operator": operator,
                "details": details,
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "trace_id": current_trace_id,
            }
            # Simulate encryption for audit logs
            encrypted_entry = base64.b64encode(json.dumps(log_entry).encode()).decode()
            self._logger.info(f"Encrypted audit log: {encrypted_entry}")
            await asyncio.sleep(0.01)  # Simulate network latency
            return True
        except Exception as e:
            self._logger.error(f"Failed to log event: {e}", exc_info=True)
            return False


audit_ledger_client = AuditLedgerClient()
