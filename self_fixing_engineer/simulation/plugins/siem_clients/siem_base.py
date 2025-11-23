import os
import asyncio
import logging
import time
import datetime
import re
import uuid
import sys
import json
from typing import Dict, Any, Optional, List, Tuple, Callable
from concurrent.futures import ThreadPoolExecutor
from abc import ABC, abstractmethod

# --- Global Production Mode Flag ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"


# --- Structured Logging Setup ---
class SIEMClientLoggerAdapter(logging.LoggerAdapter):
    """
    A LoggerAdapter that automatically injects client_type and correlation_id
    into log records.
    """

    def process(self, msg, kwargs):
        extra = kwargs.get("extra", {})
        extra["client_type"] = self.extra.get("client_type", "N/A")
        extra["correlation_id"] = self.extra.get("correlation_id", "N/A")
        kwargs["extra"] = extra
        return msg, kwargs


_base_logger = logging.getLogger(__name__)
_base_logger.setLevel(logging.INFO)


# Define JSON formatter class up front; it will reference scrub_secrets at runtime.
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "name": record.name,
            "client_type": getattr(record, "client_type", "N/A"),
            "correlation_id": getattr(record, "correlation_id", "N/A"),
            "message": record.getMessage(),
        }
        # Add extra attributes (scrubbed later)
        for k, v in record.__dict__.items():
            if k not in [
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "taskName",
            ] and not k.startswith("_"):
                log_entry[k] = v
        # Scrub sensitive info (function defined later; resolved at call time)
        return json.dumps(scrub_secrets(log_entry), ensure_ascii=False)


if not _base_logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    # Use a simple formatter for now; we will swap to JsonFormatter after scrub_secrets is defined
    if PRODUCTION_MODE:
        handler.setFormatter(logging.Formatter("%(message)s"))
    else:
        formatter = logging.Formatter(
            "%(asctime)s - [%(levelname)s] - %(name)s - client:%(client_type)s - cid:%(correlation_id)s - %(message)s"
        )
        handler.setFormatter(formatter)
    _base_logger.addHandler(handler)


# --- Placeholder for Operator Alerting (Centralized) ---
def alert_operator(message: str, level: str = "CRITICAL"):
    """
    Placeholder function to alert operations team.
    In a real system, this would integrate with PagerDuty, Slack, Email, etc.
    This default implementation just logs a critical message.
    """
    _base_logger.critical(
        f"[OPS ALERT - {level}] {message}",
        extra={"client_type": "SIEM_Alerting", "correlation_id": "N/A"},
    )


# Placeholder for a secure audit trail logger
class AuditLogger:
    async def log_event(self, event_type: str, **kwargs):
        # In a real system, this would send logs to a secure, immutable log store
        event = {
            "event_type": event_type,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            **kwargs,
        }
        _base_logger.info(
            f"[AUDIT] {json.dumps(event)}",
            extra={
                "client_type": "AUDIT",
                "correlation_id": kwargs.get("correlation_id", "N/A"),
            },
        )


AUDIT = AuditLogger()

# --- Secret Scrubbing Utility (REQUIRED) ---
_global_secret_patterns = [
    r'(?:[Aa]pi)?[_]?([Kk]ey|[Ss]ecret|[Tt]oken|[Pp]ass(?:word)?)[:=]?\s*[\'"]?([a-zA-Z0-9_-]{16,128})[\'"]?',  # Generic API keys/tokens
    r'([Ss]hared[Kk]ey)[:=][\'"]?([a-zA-Z0-9\/+=]{40,})[\'"]?',  # Azure Shared Key
    r"AKIA[0-9A-Z]{16}[A-Z0-9]{8}",  # AWS Access Key ID
    r"[A-Za-z0-9+/]{40}=",  # AWS Secret Access Key (base64)
    r"eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?([A-Za-z0-9-_.+/=])*",  # JWTs
    r"(pk|sk)_[a-zA-Z0-9_]{16,128}",  # Stripe-like secret keys
    r"Bearer\s+[A-Za-z0-9-._~+/]{30,}",  # Bearer tokens
    r"\b(?:[0-9]{4}[ -]?){3}[0-9]{4}\b",  # Credit card numbers (simple)
    r"(\d{3}[-\s]?\d{2}[-\s]?\d{4})",  # US SSN (simple)
    r"\bemail=([^&\s]+)\b",  # email=value
    r"user=([^&\s]+)\b",  # user=value
    r"client_id=\S+",  # Client IDs
    r"client_secret=\S+",  # Client Secrets
    r"connectionstring=([^;]+)",  # Azure connection strings
    r"(\b[A-Fa-f0-9]{64}\b)",  # 64-char hex string
    # NOTE: Removed generic domain scrubber to reduce false positives
]
# Pre-compile the global patterns for efficiency
_compiled_global_secret_patterns = [re.compile(p, re.IGNORECASE) for p in _global_secret_patterns]

# A set to hold patterns for environment variable scrubbing on init
_env_secret_patterns_on_init = [
    r".*_KEY$",
    r".*_SECRET$",
    r".*_TOKEN$",
    r".*_PASSWORD$",
    r".*_CONN_STRING$",
    r"SIEM_.*_KEY",
    r"SIEM_.*_SECRET",
    r"SIEM_.*_TOKEN",
    r"SIEM_.*_PASSWORD",
    r"SIEM_.*_CONN_STRING",
    r"AWS_ACCESS_KEY_ID",
    r"AWS_SECRET_ACCESS_KEY",
    r"AZURE_CLIENT_SECRET",
    r"GCP_CREDENTIALS",
]
_compiled_env_secret_patterns = [re.compile(p, re.IGNORECASE) for p in _env_secret_patterns_on_init]


def scrub_secrets(data: Any, patterns: Optional[List[str]] = None) -> Any:
    """
    Recursively scrubs sensitive information from data based on provided regex patterns.
    If no patterns are provided, uses a set of global default patterns.
    Applies to string values in dictionaries and lists.
    Optimized by using pre-compiled regex caching.
    """
    all_patterns = _compiled_global_secret_patterns + [
        re.compile(p, re.IGNORECASE) for p in (patterns or [])
    ]

    def _scrub(item: Any) -> Any:
        if isinstance(item, dict):
            return {k: _scrub(v) for k, v in item.items()}
        elif isinstance(item, list):
            return [_scrub(elem) for elem in item]
        elif isinstance(item, str):
            for pattern in all_patterns:
                item = pattern.sub("[SCRUBBED]", item)
            return item
        else:
            return item

    return _scrub(data)


# Re-apply JSON formatter with scrubber now defined (restore structured logging)
if PRODUCTION_MODE:
    for h in _base_logger.handlers:
        h.setFormatter(JsonFormatter())


# --- Strict Dependency Checks ---
def _check_and_import_critical(package_name: str, module_name: Optional[str] = None):
    try:
        if module_name:
            import importlib

            return importlib.import_module(module_name)
        else:
            return __import__(package_name)
    except ImportError:
        _base_logger.critical(
            f"CRITICAL: Required dependency '{package_name}' not found.",
            extra={"client_type": "SIEM_Base", "correlation_id": "N/A"},
        )
        alert_operator(
            f"CRITICAL: Missing required dependency '{package_name}'. SIEM client cannot start.",
            level="CRITICAL",
        )
        # Raise ImportError so callers/factory can decide lifecycle
        raise


# Critical dependencies for the core SIEM client functionality
aiohttp = _check_and_import_critical("aiohttp")
tenacity = _check_and_import_critical("tenacity")

pydantic = _check_and_import_critical("pydantic")
from pydantic import BaseModel, Field, Extra

try:
    # pydantic v1 path
    pass
except Exception:
    # pydantic v2 compatibility
    pass  # type: ignore

opentelemetry = _check_and_import_critical("opentelemetry")

# --- PYDANTIC_AVAILABLE Flag ---
PYDANTIC_AVAILABLE = True  # Set to True since pydantic was successfully imported above


# --- Exception Hierarchy ---
class SIEMClientError(Exception):
    """Base exception for all SIEM client errors."""

    def __init__(
        self,
        message: str,
        client_type: str = "Unknown",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.client_type = client_type
        self.details = details or {}


class SIEMClientConfigurationError(SIEMClientError):
    """Raised when there's a configuration issue."""

    pass


class SIEMClientAuthError(SIEMClientError):
    """Raised when authentication fails."""

    pass


class SIEMClientConnectivityError(SIEMClientError):
    """Raised when there's a network connectivity issue."""

    pass


class SIEMClientQueryError(SIEMClientError):
    """Raised when a query operation fails."""

    pass


class SIEMClientPublishError(SIEMClientError):
    """Raised when publishing/sending logs fails."""

    pass


class SIEMClientResponseError(SIEMClientError):
    """Raised when the SIEM service returns an unexpected response."""

    pass


# --- Secrets Manager (Placeholder) ---
class SecretsManager:
    """
    Placeholder secrets manager for retrieving secrets from environment variables.
    In production, this should integrate with AWS Secrets Manager, Azure Key Vault, etc.
    """

    def get_secret(self, key: str, required: bool = False, default: Any = None) -> Any:
        """Get a secret from environment variables."""
        value = os.getenv(key, default)
        if required and value is None:
            raise SIEMClientConfigurationError(
                f"Required secret '{key}' not found in environment",
                client_type="SecretsManager",
            )
        return value


SECRETS_MANAGER = SecretsManager()


# --- Generic Log Event Model ---
class GenericLogEvent(BaseModel):
    """Generic log event structure for SIEM clients."""

    timestamp: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")
    level: str = "INFO"
    message: str
    source: str = "siem_client"
    event_type: str = "generic"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = Extra.allow


# --- Aiohttp Client Mixin ---
class AiohttpClientMixin:
    """
    Mixin class providing aiohttp session management for SIEM clients.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp session."""
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    self._session = aiohttp.ClientSession()
        return self._session

    async def _close_session(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


# --- Base SIEM Client ---
class BaseSIEMClient(ABC):
    """
    Abstract base class for all SIEM clients.
    Provides common functionality and defines the interface that all clients must implement.
    """

    def __init__(self, config: Dict[str, Any], client_type: str = "generic"):
        self.config = config
        self.client_type = client_type
        self.logger = SIEMClientLoggerAdapter(
            _base_logger,
            {"client_type": client_type, "correlation_id": str(uuid.uuid4())},
        )
        self._executor = ThreadPoolExecutor(max_workers=4)

    def _run_blocking_in_executor(self, func: Callable, *args, **kwargs):
        """Run a blocking function in a thread pool executor."""
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(self._executor, lambda: func(*args, **kwargs))

    def _parse_relative_time_range_to_ms(self, time_range: str) -> Tuple[int, int]:
        """
        Parse relative time range string (e.g., '1h', '24h', '7d') to millisecond timestamps.
        Returns (start_ms, end_ms) tuple.
        """
        match = re.match(r"(\d+)([smhd])", time_range.lower())
        if not match:
            raise ValueError(f"Invalid time range format: {time_range}")

        value, unit = int(match.group(1)), match.group(2)
        unit_to_seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        seconds = value * unit_to_seconds[unit]

        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (seconds * 1000)
        return start_ms, end_ms

    def _parse_relative_time_range_to_timedelta(self, time_range: str) -> datetime.timedelta:
        """Parse relative time range string to timedelta."""
        match = re.match(r"(\d+)([smhd])", time_range.lower())
        if not match:
            raise ValueError(f"Invalid time range format: {time_range}")

        value, unit = int(match.group(1)), match.group(2)
        if unit == "s":
            return datetime.timedelta(seconds=value)
        elif unit == "m":
            return datetime.timedelta(minutes=value)
        elif unit == "h":
            return datetime.timedelta(hours=value)
        elif unit == "d":
            return datetime.timedelta(days=value)
        else:
            raise ValueError(f"Unknown time unit: {unit}")

    @abstractmethod
    async def health_check(self) -> Tuple[bool, str]:
        """Check if the SIEM service is healthy and reachable."""
        pass

    @abstractmethod
    async def send_log(self, log_data: Dict[str, Any]) -> Tuple[bool, str]:
        """Send a log event to the SIEM service."""
        pass

    @abstractmethod
    async def query_logs(
        self, query: str, time_range: str = "1h", limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Query logs from the SIEM service."""
        pass

    async def close(self):
        """Cleanup resources."""
        if hasattr(self, "_executor"):
            self._executor.shutdown(wait=False)
