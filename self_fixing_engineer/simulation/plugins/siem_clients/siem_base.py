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
        extra = kwargs.get('extra', {})
        extra['client_type'] = self.extra.get('client_type', 'N/A')
        extra['correlation_id'] = self.extra.get('correlation_id', 'N/A')
        kwargs['extra'] = extra
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
            "client_type": getattr(record, 'client_type', 'N/A'),
            "correlation_id": getattr(record, 'correlation_id', 'N/A'),
            "message": record.getMessage(),
        }
        # Add extra attributes (scrubbed later)
        for k, v in record.__dict__.items():
            if k not in [
                'name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename', 'module',
                'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName', 'created', 'msecs',
                'relativeCreated', 'thread', 'threadName', 'processName', 'process', 'taskName'
            ] and not k.startswith('_'):
                log_entry[k] = v
        # Scrub sensitive info (function defined later; resolved at call time)
        return json.dumps(scrub_secrets(log_entry), ensure_ascii=False)

if not _base_logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    # Use a simple formatter for now; we will swap to JsonFormatter after scrub_secrets is defined
    if PRODUCTION_MODE:
        handler.setFormatter(logging.Formatter('%(message)s'))
    else:
        formatter = logging.Formatter('%(asctime)s - [%(levelname)s] - %(name)s - client:%(client_type)s - cid:%(correlation_id)s - %(message)s')
        handler.setFormatter(formatter)
    _base_logger.addHandler(handler)

# --- Placeholder for Operator Alerting (Centralized) ---
def alert_operator(message: str, level: str = "CRITICAL"):
    """
    Placeholder function to alert operations team.
    In a real system, this would integrate with PagerDuty, Slack, Email, etc.
    This default implementation just logs a critical message.
    """
    _base_logger.critical(f"[OPS ALERT - {level}] {message}", extra={'client_type': 'SIEM_Alerting', 'correlation_id': 'N/A'})

# Placeholder for a secure audit trail logger
class AuditLogger:
    async def log_event(self, event_type: str, **kwargs):
        # In a real system, this would send logs to a secure, immutable log store
        event = {"event_type": event_type, "timestamp": datetime.datetime.utcnow().isoformat() + "Z", **kwargs}
        _base_logger.info(f"[AUDIT] {json.dumps(event)}", extra={'client_type': 'AUDIT', 'correlation_id': kwargs.get('correlation_id', 'N/A')})

AUDIT = AuditLogger()

# --- Secret Scrubbing Utility (REQUIRED) ---
_global_secret_patterns = [
    r'(?:[Aa]pi)?[_]?([Kk]ey|[Ss]ecret|[Tt]oken|[Pp]ass(?:word)?)[:=]?\s*[\'"]?([a-zA-Z0-9_-]{16,128})[\'"]?',  # Generic API keys/tokens
    r'([Ss]hared[Kk]ey)[:=][\'"]?([a-zA-Z0-9\/+=]{40,})[\'"]?',  # Azure Shared Key
    r'AKIA[0-9A-Z]{16}[A-Z0-9]{8}',  # AWS Access Key ID
    r'[A-Za-z0-9+/]{40}=',  # AWS Secret Access Key (base64)
    r'eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?([A-Za-z0-9-_.+/=])*',  # JWTs
    r'(pk|sk)_[a-zA-Z0-9_]{16,128}',  # Stripe-like secret keys
    r'Bearer\s+[A-Za-z0-9-._~+/]{30,}',  # Bearer tokens
    r'\b(?:[0-9]{4}[ -]?){3}[0-9]{4}\b',  # Credit card numbers (simple)
    r'(\d{3}[-\s]?\d{2}[-\s]?\d{4})',  # US SSN (simple)
    r'\bemail=([^&\s]+)\b',  # email=value
    r'user=([^&\s]+)\b',  # user=value
    r'client_id=\S+',  # Client IDs
    r'client_secret=\S+',  # Client Secrets
    r'connectionstring=([^;]+)',  # Azure connection strings
    r'(\b[A-Fa-f0-9]{64}\b)',  # 64-char hex string
    # NOTE: Removed generic domain scrubber to reduce false positives
]
# Pre-compile the global patterns for efficiency
_compiled_global_secret_patterns = [re.compile(p, re.IGNORECASE) for p in _global_secret_patterns]

# A set to hold patterns for environment variable scrubbing on init
_env_secret_patterns_on_init = [
    r'.*_KEY$', r'.*_SECRET$', r'.*_TOKEN$', r'.*_PASSWORD$', r'.*_CONN_STRING$',
    r'SIEM_.*_KEY', r'SIEM_.*_SECRET', r'SIEM_.*_TOKEN', r'SIEM_.*_PASSWORD', r'SIEM_.*_CONN_STRING',
    r'AWS_ACCESS_KEY_ID', r'AWS_SECRET_ACCESS_KEY', r'AZURE_CLIENT_SECRET', r'GCP_CREDENTIALS',
]
_compiled_env_secret_patterns = [re.compile(p, re.IGNORECASE) for p in _env_secret_patterns_on_init]

def scrub_secrets(data: Any, patterns: Optional[List[str]] = None) -> Any:
    """
    Recursively scrubs sensitive information from data based on provided regex patterns.
    If no patterns are provided, uses a set of global default patterns.
    Applies to string values in dictionaries and lists.
    Optimized by using pre-compiled regex caching.
    """
    all_patterns = _compiled_global_secret_patterns + [re.compile(p, re.IGNORECASE) for p in (patterns or [])]

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
    except ImportError as e:
        _base_logger.critical(f"CRITICAL: Required dependency '{package_name}' not found.", extra={'client_type': 'SIEM_Base', 'correlation_id': 'N/A'})
        alert_operator(f"CRITICAL: Missing required dependency '{package_name}'. SIEM client cannot start.", level="CRITICAL")
        # Raise ImportError so callers/factory can decide lifecycle
        raise

# Critical dependencies for the core SIEM client functionality
aiohttp = _check_and_import_critical("aiohttp")
tenacity = _check_and_import_critical("tenacity")
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

pydantic = _check_and_import_critical("pydantic")
from pydantic import BaseModel, Field, ValidationError, Extra
try:
    # pydantic v1 path
    from pydantic.networks import IPvAnyAddress
except Exception:
    # pydantic v2 compatibility
    from pydantic import IPvAnyAddress  # type: ignore

opentelemetry = _check_and_import_critical("opentelemetry")
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
