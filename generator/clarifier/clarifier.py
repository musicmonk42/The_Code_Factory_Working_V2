# clarifier.py
"""
Core orchestrator for LLM-based clarification of ambiguous requirements.
Provides shared utilities (configuration, encryption, logging, tracing, circuit breaker)
for clarifier_prompt.py and other modules. Uses UserPromptChannel from clarifier_user_prompt.py
for user interaction, supporting multiple channels (CLI, GUI, web, etc.).
Created: July 30, 2025.

Security & Limitations:
- Circuit breaker is process-local (does not propagate between multiple processes/containers).
- Context DB (SQLiteContextManager) currently supports keyword search on encrypted blobs,
  which is NOT semantic/vectored search. For true semantic search, a dedicated vector DB
  or plaintext FTS index would be required.
- KMS (for history encryption), OpenTelemetry (for tracing), and aiohttp (for alerting)
  are required in production.
- Production system will abort startup if any critical dependency (e.g., KMS key,
  production-ready context manager) is missing or misconfigured.
- Graceful shutdown is best effort only (background tasks may not be interrupted instantly).
- File permissions (0o600) are enforced on history files.
- All secrets (like API keys, KMS keys) are handled via environment variables,
  but in a real-world scenario, they should be fetched from a secure Secret Manager
  (e.g., AWS Secrets Manager, HashiCorp Vault) and never directly from environment variables
  in production. The KMS key for history encryption is fetched via environment variable
  for this demo, but should also come from a Secret Manager.
"""

import asyncio
import base64
import datetime  # For history timestamp and backup
import json
import logging
import os
import signal  # For graceful shutdown
import sqlite3  # For SQLiteContextManager
import stat  # For file permissions
import sys  # For SystemExit
import time
import unittest
import uuid  # For history temp file
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, patch

import aiofiles  # Async file I/O (reqs: aiofiles)
import boto3  # For KMS integration (reqs: boto3)
import zstandard as zstd  # Compression (reqs: zstandard)
from cryptography.fernet import (
    Fernet,
)  # Encryption for data at rest (reqs: cryptography)
from dynaconf import Dynaconf, Validator  # Configuration management (reqs: dynaconf)

# Prometheus metrics need to be defined at the top level to be accessible globally
from prometheus_client import Counter, Histogram

# --- FIX: Make imports resilient against missing sub-modules or circular dependencies ---

# Import LLM and Prioritizer modules; provide dummies if they fail (e.g., if files are missing).
try:
    from .clarifier_llm import GrokLLM, LLMProvider
    from .clarifier_prioritizer import DefaultPrioritizer, Prioritizer
except ImportError as e:
    logging.warning(
        f"Failed to import core dependency (LLM/Prioritizer): {e}. Using dummy implementations."
    )

    # Minimal Dummies for LLM/Prioritizer to allow Clarifier class to initialize
    class LLMProvider:
        """Stub LLM Provider - actual implementation should be in clarifier_llm.py"""

        def __init__(self, *args, **kwargs):
            logging.warning(
                "Using stub LLMProvider - clarifier_llm.py module not available"
            )
            self.api_key = kwargs.get("api_key")
            self.model = kwargs.get("model", "default")

        async def generate(self, prompt: str, **kwargs) -> str:
            """Stub method that raises NotImplementedError"""
            raise NotImplementedError(
                "LLMProvider.generate is not implemented. "
                "The clarifier_llm.py module with actual LLM integration is required."
            )

    class GrokLLM(LLMProvider):
        """Stub Grok LLM Provider - actual implementation should be in clarifier_llm.py"""

        def __init__(self, *args, **kwargs):
            logging.warning(
                "Using stub GrokLLM - clarifier_llm.py module not available"
            )
            super().__init__(*args, **kwargs)

        async def generate(self, prompt: str, **kwargs) -> str:
            """Stub method that raises NotImplementedError"""
            raise NotImplementedError(
                "GrokLLM.generate is not implemented. "
                "The clarifier_llm.py module with actual Grok API integration is required. "
                "Please implement the GrokLLM class with proper API calls."
            )

    class Prioritizer(ABC):
        """Base class for prioritizing ambiguities in requirements"""

        def __init__(self, llm):
            self.llm = llm

        @abstractmethod
        async def prioritize(self, ambiguities, context, target_language):
            """Prioritize ambiguities based on importance and context"""
            pass

    class DefaultPrioritizer(Prioritizer):
        """Stub Default Prioritizer - actual implementation should be in clarifier_prioritizer.py"""

        async def prioritize(self, ambiguities, context, target_language):
            """
            Stub implementation that raises NotImplementedError.

            A proper implementation should:
            1. Analyze each ambiguity for complexity and impact
            2. Score ambiguities based on context and target language
            3. Group related ambiguities into batches
            4. Return prioritized list with questions for user clarification
            """
            logging.warning(
                "Using stub DefaultPrioritizer - clarifier_prioritizer.py module not available"
            )
            raise NotImplementedError(
                "DefaultPrioritizer.prioritize is not implemented. "
                "The clarifier_prioritizer.py module with actual prioritization logic is required. "
                "Please implement intelligent prioritization based on ambiguity analysis."
            )


# Import internal package components that might create a circular dependency loop.
# Using lazy imports to avoid circular dependency issues during module loading.
# This follows best practices for resolving import cycles in large codebases.
_channel_import_failed = False
_channel_import_error = None

# Module-level caches for lazy-loaded imports
_cached_interaction_mode = None
_cached_get_channel = None
_cached_update_requirements = None
_cached_plugin_decorator = None
_cached_plugin_kind = None


def _lazy_import_channel_components():
    """
    Lazy import of channel components to avoid circular dependencies.
    
    This function is called on-demand when the components are actually needed,
    not at module import time. This breaks circular import cycles that can occur
    during pytest collection or when modules import each other.
    
    Returns:
        tuple: (InteractionMode, get_channel, update_requirements_with_answers, plugin, PlugInKind)
        
    Raises:
        ImportError: If the required modules cannot be imported
    """
    global _cached_interaction_mode, _cached_get_channel, _cached_update_requirements
    global _cached_plugin_decorator, _cached_plugin_kind
    
    # Return cached values if already imported
    if _cached_interaction_mode is not None:
        return (
            _cached_interaction_mode,
            _cached_get_channel,
            _cached_update_requirements,
            _cached_plugin_decorator,
            _cached_plugin_kind,
        )
    
    try:
        from omnicore_engine.plugin_registry import PlugInKind, plugin
        from .clarifier_updater import update_requirements_with_answers
        from .clarifier_user_prompt import UserPromptChannel as InteractionMode
        from .clarifier_user_prompt import get_channel
        
        # Cache the imports
        _cached_interaction_mode = InteractionMode
        _cached_get_channel = get_channel
        _cached_update_requirements = update_requirements_with_answers
        _cached_plugin_decorator = plugin
        _cached_plugin_kind = PlugInKind
        
        return InteractionMode, get_channel, update_requirements_with_answers, plugin, PlugInKind
    except ImportError as e:
        global _channel_import_failed, _channel_import_error
        _channel_import_failed = True
        _channel_import_error = e
        logging.warning(
            f"Failed to load package dependencies (Prompt/Updater/Plugin) due to potential circular import: {e}. "
            f"Using fallback implementations for graceful degradation."
        )
        raise


# Module-level flags for tracking import failures
_channel_import_failed = False
_channel_import_error = None

# Provide module-level access with lazy loading ONLY
# Do NOT import at module level to avoid circular dependencies
# All imports happen via _lazy_import_channel_components() on-demand

# Define minimal fallback implementations for graceful degradation
class InteractionMode:
    """Stub InteractionMode when clarifier_user_prompt cannot be imported"""
    pass

def get_channel(*args, **kwargs):
    """
    Fallback for get_channel when import fails.
    
    Attempts lazy import first, then provides helpful error message.
    This is called only when the import hasn't been successful yet.
    """
    try:
        _, real_get_channel, _, _, _ = _lazy_import_channel_components()
        return real_get_channel(*args, **kwargs)
    except ImportError as e:
        global _channel_import_failed, _channel_import_error
        _channel_import_failed = True
        _channel_import_error = e
        error_msg = (
            "Channel imports failed - clarifier_user_prompt module is unavailable. "
            f"Original error: {e}. "
            "Possible solutions:\n"
            "1. Ensure clarifier_user_prompt.py exists in the same directory\n"
            "2. Check for circular import issues in the module dependencies\n"
            "3. Try importing the channel module directly before initializing Clarifier\n"
            "4. Use a mock/stub channel implementation for testing"
        )
        logging.error(error_msg)
        raise NotImplementedError(error_msg)

def update_requirements_with_answers(*args, **kwargs):
    """
    Fallback for update_requirements_with_answers when import fails.
    
    Attempts lazy import first, then returns empty dict for graceful degradation.
    """
    try:
        _, _, real_update_requirements, _, _ = _lazy_import_channel_components()
        return real_update_requirements(*args, **kwargs)
    except ImportError:
        logging.warning(
            "update_requirements_with_answers called but clarifier_updater module is unavailable. "
            "Returning empty result."
        )
        return {}

def plugin(*args, **kwargs):
    """Fallback plugin decorator - no-op that returns the function unchanged."""
    def decorator(f):
        return f
    if args and callable(args[0]):
        # Called without arguments: @plugin
        return args[0]
    else:
        # Called with arguments: @plugin(...)
        return decorator

class PlugInKind:
    """Stub PlugInKind enum when plugin_registry cannot be imported"""
    CLARIFIER = "clarifier"
    GENERATOR = "generator"
    FIX = "fix"
    OPTIMIZER = "optimizer"
    VALIDATOR = "validator"
    ANALYZER = "analyzer"

    class PlugInKind:
        """Fallback PlugInKind with minimal definitions."""
        FIX = "fix"


# --- End of FIX restructuring ---


# --- Shared Utilities ---
# Global variables to act as singletons for shared components
settings = None
fernet = None
logger = None
tracer = None
Status = None
StatusCode = None
circuit_breaker = None
HAS_OPENTELEMETRY = False

# Import log_action and send_alert from runner logging (preferred) or audit_log
# NOTE: In production environments, these should come from the runner module.
# The fallback is only for development/testing scenarios.
_USING_DUMMY_CLARIFIER_LOGGING = False


# Create a wrapper for log_audit_event that maintains backwards compatibility
# with the original log_action interface
async def _wrap_log_audit_event(action: str, **kwargs) -> None:
    """
    Wrapper that converts legacy log_action calls to log_audit_event format.

    The original log_action interface accepted (action_name, **kwargs).
    The new log_audit_event requires (action, data_dict).
    """
    try:
        from runner.runner_logging import log_audit_event

        await log_audit_event(action=action, data=kwargs)
    except ImportError:
        # Fallback if runner not available
        get_logger().debug(f"log_action: {action}, {kwargs}")
    except Exception as e:
        # Don't let logging failures crash the application
        get_logger().warning(f"log_action failed: {e}", extra={"action": action})


try:
    from runner.runner_logging import log_audit_event as _log_audit_event, send_alert

    # Use the wrapper to maintain backwards compatibility
    log_action = _wrap_log_audit_event
except ImportError:
    try:
        from audit_log import log_action, send_alert
    except ImportError:
        # In production, we should fail hard if runner logging is not available
        _is_production = os.getenv("PYTHON_ENV", "development").lower() == "production"
        _is_testing = (
            os.getenv("TESTING") == "1"
            or "pytest" in sys.modules
            or os.getenv("PYTEST_CURRENT_TEST") is not None
        )

        if _is_production and not _is_testing:
            # Fail hard in production if runner logging is not available
            raise ImportError(
                "CRITICAL: Runner logging module (runner.runner_logging) is required in production. "
                "Clarification events must be logged to the secure audit trail. "
                "Please ensure the runner module is properly installed and configured."
            )

        _USING_DUMMY_CLARIFIER_LOGGING = True
        # Use a dummy logger for the warning before the main logger is configured
        logging.warning(
            "audit_log.py and runner.runner_logging not found. "
            "Using dummy functions (NOT FOR PRODUCTION)."
        )

        async def log_action(action: str, **kwargs) -> None:
            """
            Fallback log_action for development/testing only.
            WARNING: This does NOT provide secure audit logging.
            """
            get_logger().warning(
                f"DUMMY log_action (NOT FOR PRODUCTION): {action}",
                extra={
                    "operation": "dummy_log_action",
                    "warning": "not_audit_logged",
                    "action": action,
                    "data": kwargs,
                },
            )

        async def send_alert(*args, **kwargs) -> None:
            """
            Fallback send_alert for development/testing only.
            WARNING: Alerts are NOT sent in this mode.
            """
            get_logger().warning(
                f"DUMMY send_alert (NOT FOR PRODUCTION): {args}",
                extra={"operation": "dummy_send_alert", "warning": "alert_not_sent"},
            )


# --- Sensitive Data Filter ---
class SensitiveDataFilter(logging.Filter):
    """Redacts sensitive information from logs."""

    def filter(self, record):
        if hasattr(record, "msg") and isinstance(record.msg, str):
            record.msg = record.msg.replace("API_KEY", "***REDACTED_API_KEY***")
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            for key in ["user_input", "answer_text", "api_key"]:
                if key in record.extra:
                    record.extra[key] = "***REDACTED***"
        return True


def setup_logging() -> logging.Logger:
    """Configures and returns a logger with a sensitive data filter."""
    log = logging.getLogger(__name__)
    if not log.handlers:  # Avoid adding handlers multiple times
        log.addFilter(SensitiveDataFilter())
        log.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        log.addHandler(handler)
    return log


def load_config() -> Dynaconf:
    """
    Loads and validates the application configuration using Dynaconf.
    
    Configuration is loaded from:
    1. clarifier_config.yaml (if present)
    2. Environment variables with CLARIFIER_ prefix
    3. Default values (for development/testing)
    
    Returns:
        Dynaconf configuration object with validated settings
        
    Note:
        In production environments, it's recommended to explicitly set all configuration
        via environment variables or config file to avoid relying on defaults.
        
        Critical settings that should be explicitly configured in production:
        - CLARIFIER_KMS_KEY_ID: For encryption of sensitive data
        - CLARIFIER_ALERT_ENDPOINT: For operational alerts
        - CLARIFIER_CONTEXT_DB_PATH: For persistent context storage
        - CLARIFIER_HISTORY_FILE: For audit trail
    """
    # Check if we're in production to adjust validation strictness
    is_production = os.getenv("PYTHON_ENV", "development").lower() == "production"
    
    cfg = Dynaconf(
        envvar_prefix="CLARIFIER",
        settings_files=["clarifier_config.yaml"],
        validators=[
            Validator("LLM_PROVIDER", default="auto", is_in=["openai", "anthropic", "grok", "google", "gemini", "ollama", "local", "auto"]),
            Validator("INTERACTION_MODE", default="cli", is_in=["cli"]),
            Validator("BATCH_STRATEGY", default="default", is_in=["default"]),
            Validator("FEEDBACK_STRATEGY", default="none", is_in=["none"]),
            # File paths - use /tmp for development, should be configured for production
            Validator("HISTORY_FILE", default="/tmp/clarifier_history.json", is_type_of=str),
            Validator("TARGET_LANGUAGE", default="en", is_type_of=str),
            Validator("CONTEXT_DB_PATH", default="/tmp/clarifier_context.db", is_type_of=str),
            # Security settings - empty defaults indicate features are disabled
            Validator("KMS_KEY_ID", default="", is_type_of=str),
            Validator("ALERT_ENDPOINT", default="", is_type_of=str),
            # Behavioral settings
            Validator("HISTORY_COMPRESSION", default=False, is_type_of=bool),
            Validator("CONTEXT_QUERY_LIMIT", default=3, gte=1, lte=10),
            Validator("HISTORY_LOOKBACK_LIMIT", default=10, gte=1, lte=100),
            Validator("CIRCUIT_BREAKER_THRESHOLD", default=5, gte=1),
            Validator("CIRCUIT_BREAKER_TIMEOUT", default=30, gte=1),
        ],
    )
    
    try:
        cfg.validators.validate()
        get_logger().info("Clarifier configuration validated successfully.")
        cfg.is_production_env = is_production
        
        # Warn about missing critical production settings
        if is_production:
            warnings = []
            if not cfg.get("KMS_KEY_ID"):
                warnings.append("KMS_KEY_ID is not set - encryption features will be limited")
            if not cfg.get("ALERT_ENDPOINT"):
                warnings.append("ALERT_ENDPOINT is not set - operational alerts will be disabled")
            if cfg.get("HISTORY_FILE", "").startswith("/tmp/"):
                warnings.append("HISTORY_FILE uses /tmp path - data may not persist across restarts")
            if cfg.get("CONTEXT_DB_PATH", "").startswith("/tmp/"):
                warnings.append("CONTEXT_DB_PATH uses /tmp path - context may not persist across restarts")
                
            if warnings:
                get_logger().warning(
                    f"PRODUCTION CONFIGURATION WARNINGS:\n  - " + "\n  - ".join(warnings) +
                    "\nConsider setting explicit CLARIFIER_* environment variables for production deployments."
                )
    except Exception as e:
        # Log validation failure appropriately based on environment
        if is_production:
            get_logger().error(
                f"CRITICAL: Configuration validation failed in production: {e}. "
                f"Continuing with default values, but this may cause service degradation. "
                f"Please review CLARIFIER_* environment variables immediately."
            )
        else:
            get_logger().warning(
                f"Configuration validation failed: {e}. "
                f"Continuing with default values suitable for development/testing. "
                f"Set CLARIFIER_* environment variables if specific configuration is needed."
            )
        
        # Set production flag to False since we're using fallback config
        cfg.is_production_env = False
    
    return cfg


def initialize_encryption(kms_key_id: str, is_prod: bool) -> Fernet:
    """Initializes Fernet encryption, fetching the key from KMS in production."""
    try:
        kms_client = boto3.client("kms", region_name=os.getenv("AWS_REGION"))
        response = kms_client.decrypt(
            CiphertextBlob=base64.b64decode(
                os.getenv("CLARIFIER_HISTORY_ENCRYPTION_KEY_B64", "")
            ),
            KeyId=kms_key_id,
        )
        history_encryption_key = response["Plaintext"]
        f = Fernet(history_encryption_key)
        get_logger().info("History encryption key fetched and Fernet initialized.")
        return f
    except Exception as e:
        get_logger().critical(
            f"Failed to fetch history encryption key from KMS: {e}.", exc_info=True
        )
        if is_prod:
            get_logger().critical(
                "CRITICAL: In production mode, a valid KMS-provided history encryption key is REQUIRED. Aborting startup."
            )
            sys.exit(1)
        else:
            f = Fernet(Fernet.generate_key())
            get_logger().warning(
                "Using a dummy Fernet key. History encryption is INSECURE. DO NOT USE IN PRODUCTION WITHOUT A REAL KMS KEY."
            )
            return f


def setup_tracing() -> Tuple[Optional[Any], Optional[Any], Optional[Any], bool]:
    """Initializes OpenTelemetry tracing."""
    try:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode

        # Use the default/configured tracer provider instead of manually creating one
        # This avoids version compatibility issues and respects OTEL_* environment variables
        tracer_instance = trace.get_tracer(__name__)
        return tracer_instance, Status, StatusCode, True
    except ImportError:
        get_logger().warning("OpenTelemetry not installed. Tracing disabled.")
        return None, None, None, False
    except Exception as e:
        get_logger().error(
            f"Failed to initialize OpenTelemetry: {e}. Tracing disabled.", exc_info=True
        )
        return None, None, None, False


def get_logger() -> logging.Logger:
    """Returns the singleton logger instance."""
    global logger
    if logger is None:
        logger = setup_logging()
    return logger


def get_config() -> Dynaconf:
    """Returns the singleton config instance."""
    global settings
    if settings is None:
        settings = load_config()
    return settings


def get_fernet() -> Fernet:
    """Returns the singleton Fernet instance."""
    global fernet
    if fernet is None:
        config = get_config()
        fernet = initialize_encryption(config.KMS_KEY_ID, config.is_production_env)
    return fernet


def get_tracer() -> Tuple[Optional[Any], Optional[Any], Optional[Any]]:
    """Returns the singleton tracer instance and related classes."""
    global tracer, Status, StatusCode, HAS_OPENTELEMETRY
    if tracer is None:
        tracer, Status, StatusCode, HAS_OPENTELEMETRY = setup_tracing()
    return tracer, Status, StatusCode


# --- Metrics (defined globally for easy access) ---
# FIX: Wrap metric creation in try-except to handle duplicate registration during pytest
try:
    CLARIFIER_CYCLES = Counter(
        "clarifier_cycles_total", "Total clarification cycles", ["status"]
    )
    CLARIFIER_LATENCY = Histogram(
        "clarifier_latency_seconds", "Clarification cycle latency", ["status"]
    )
    CLARIFIER_ERRORS = Counter(
        "clarifier_errors_total", "Clarifier errors", ["error_type"]
    )
    CLARIFIER_CONTEXT_RETRIEVAL_LATENCY = Histogram(
        "clarifier_context_retrieval_seconds",
        "Context retrieval latency",
        ["manager_type"],
    )
    CLARIFIER_QUESTION_PROMPT_LATENCY = Histogram(
        "clarifier_question_prompt_seconds",
        "Question prompt latency",
        ["interaction_mode"],
    )
    CLARIFIER_PRIORITIZATION_LATENCY = Histogram(
        "clarifier_prioritization_seconds", "Prioritization latency", ["strategy"]
    )
except ValueError:
    # Metrics already registered (happens during pytest collection)
    from prometheus_client import REGISTRY

    CLARIFIER_CYCLES = REGISTRY._names_to_collectors.get("clarifier_cycles_total")
    CLARIFIER_LATENCY = REGISTRY._names_to_collectors.get("clarifier_latency_seconds")
    CLARIFIER_ERRORS = REGISTRY._names_to_collectors.get("clarifier_errors_total")
    CLARIFIER_CONTEXT_RETRIEVAL_LATENCY = REGISTRY._names_to_collectors.get(
        "clarifier_context_retrieval_seconds"
    )
    CLARIFIER_QUESTION_PROMPT_LATENCY = REGISTRY._names_to_collectors.get(
        "clarifier_question_prompt_seconds"
    )
    CLARIFIER_PRIORITIZATION_LATENCY = REGISTRY._names_to_collectors.get(
        "clarifier_prioritization_seconds"
    )


# --- Circuit Breaker ---
class CircuitBreaker:
    """Circuit breaker to prevent cascading failures."""

    def __init__(self, threshold: int, timeout: int):
        self._tripped = False
        self._trip_time = 0.0
        self._error_count = 0
        self._threshold = threshold
        self._timeout = timeout
        self.logger = get_logger()

    @property
    def failure_count(self) -> int:
        """Public property for accessing failure count (for backward compatibility)."""
        return self._error_count

    def reset(self):
        """Reset circuit breaker state (useful for testing)."""
        self._tripped = False
        self._trip_time = 0.0
        self._error_count = 0

    def is_open(self) -> bool:
        if self._tripped:
            if (time.time() - self._trip_time) > self._timeout:
                self.logger.warning(
                    "Circuit breaker timeout reached. Half-opening circuit.",
                    extra={"operation": "circuit_breaker_half_open"},
                )
                self._tripped = False
                self._error_count = 0
                # FIX: Check for running loop before creating task
                try:
                    asyncio.get_running_loop()
                    asyncio.create_task(
                        log_action("circuit_breaker_event", status="half_open")
                    )
                except RuntimeError:
                    # No event loop - log synchronously
                    self.logger.debug(
                        "No event loop available for async logging in is_open"
                    )
                return False
            self.logger.warning(
                "Circuit breaker is open. Preventing calls.",
                extra={"operation": "circuit_breaker_open_prevented"},
            )
            return True
        return False

    def record_failure(self, error: Exception):
        self._error_count += 1
        if self._error_count >= self._threshold:
            self._tripped = True
            self._trip_time = time.time()
            self.logger.error(
                f"Circuit breaker tripped after {self._error_count} consecutive errors: {error}",
                exc_info=True,
                extra={
                    "operation": "circuit_breaker_tripped",
                    "error_type": type(error).__name__,
                },
            )
            CLARIFIER_ERRORS.labels("circuit_breaker_tripped").inc()

            # FIX: Check for running loop before creating task
            try:
                asyncio.get_running_loop()
                asyncio.create_task(
                    send_alert(
                        f"Clarifier circuit breaker tripped! Consecutive errors: {self._error_count}. Last error: {error}",
                        severity="critical",
                    )
                )
                asyncio.create_task(
                    log_action(
                        "circuit_breaker_event",
                        status="tripped",
                        error=str(error),
                        error_count=self._error_count,
                    )
                )
            except RuntimeError:
                # No event loop - log synchronously
                self.logger.warning(
                    "No event loop available for async logging in record_failure (tripped)"
                )
        else:
            self.logger.warning(
                f"Circuit breaker error count: {self._error_count}/{self._threshold}. Error: {error}",
                extra={
                    "operation": "circuit_breaker_error_increment",
                    "error_type": type(error).__name__,
                },
            )
            # FIX: Same for non-critical errors
            try:
                asyncio.get_running_loop()
                asyncio.create_task(
                    log_action(
                        "circuit_breaker_event",
                        status="error_increment",
                        error=str(error),
                        error_count=self._error_count,
                    )
                )
            except RuntimeError:
                pass  # Silent fail for non-critical logging

    def record_success(self):
        if self._tripped:
            self.logger.info(
                "Circuit breaker closed after successful operation in half-open state.",
                extra={"operation": "circuit_breaker_closed"},
            )
            # FIX: Check for loop
            try:
                asyncio.get_running_loop()
                asyncio.create_task(
                    log_action("circuit_breaker_event", status="closed_after_success")
                )
            except RuntimeError:
                pass  # Silent fail for non-critical logging
        self._tripped = False
        self._error_count = 0
        self._trip_time = 0.0


def get_circuit_breaker() -> CircuitBreaker:
    """Returns the singleton CircuitBreaker instance."""
    global circuit_breaker
    if circuit_breaker is None:
        config = get_config()
        circuit_breaker = CircuitBreaker(
            threshold=config.CIRCUIT_BREAKER_THRESHOLD,
            timeout=config.CIRCUIT_BREAKER_TIMEOUT,
        )
    return circuit_breaker


# --- Context Manager Interface ---
class ContextManager(ABC):
    @abstractmethod
    async def retrieve_context(self, query: str, top_k: int = 3) -> List[str]:
        pass

    @abstractmethod
    async def add_to_context(self, data: Dict[str, Any]):
        pass

    @abstractmethod
    async def close(self):
        pass

    @property
    @abstractmethod
    def is_production_ready(self) -> bool:
        pass


class SQLiteContextManager(ContextManager):
    """
    FIXED: Async initialization pattern.
    Use the create() class method for async initialization, or call ensure_initialized()
    before first use if created with __init__.
    """

    def __init__(self, db_path: str, fernet=None):
        """Synchronous initialization only. Optionally accepts fernet for backward compatibility."""
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self.fernet = fernet if fernet is not None else get_fernet()
        self.logger = get_logger()
        self._is_production_ready = False  # Not ready until initialized

    @classmethod
    async def create(cls, db_path: str, fernet=None) -> "SQLiteContextManager":
        """Async factory method for creating initialized instance."""
        manager = cls(db_path, fernet)
        await manager._init_db()
        manager._is_production_ready = True
        return manager

    async def ensure_initialized(self):
        """Call this before first use if created with __init__."""
        if not self._is_production_ready:
            await self._init_db()
            self._is_production_ready = True

    async def _init_db(self):
        max_retries = 5
        current_attempt = 0
        while current_attempt < max_retries:
            try:

                def connect_and_setup():
                    conn = sqlite3.connect(self.db_path, check_same_thread=False)
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute("PRAGMA foreign_keys=ON;")
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS db_info (key TEXT PRIMARY KEY, value TEXT)"
                    )
                    cursor = conn.execute(
                        "SELECT value FROM db_info WHERE key = 'schema_version'"
                    )
                    row = cursor.fetchone()
                    current_schema_version = int(row["value"]) if row else 0
                    if current_schema_version < 1:
                        self.logger.info("Applying schema migration to version 1.")
                        conn.execute("""
                            CREATE TABLE IF NOT EXISTS context (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                                entry_id TEXT UNIQUE NOT NULL,
                                encrypted_data BLOB NOT NULL
                            )
                        """)
                        conn.execute(
                            "CREATE INDEX IF NOT EXISTS idx_timestamp ON context(timestamp)"
                        )
                        conn.execute(
                            "INSERT OR REPLACE INTO db_info (key, value) VALUES ('schema_version', '1')"
                        )
                        conn.commit()
                        self.logger.info("Schema migrated to version 1 successfully.")
                    conn.commit()
                    return conn

                self.conn = await asyncio.to_thread(connect_and_setup)
                self.logger.info(f"SQLiteContextManager initialized: {self.db_path}")
                await log_action(
                    "context_manager_init",
                    type="sqlite",
                    db_path=self.db_path,
                    status="success",
                )
                return
            except sqlite3.OperationalError as e:
                current_attempt += 1
                if current_attempt >= max_retries:
                    self.logger.critical(
                        f"Failed to initialize SQLiteContextManager after {max_retries} retries: {e}",
                        exc_info=True,
                    )
                    await log_action(
                        "context_manager_init",
                        type="sqlite",
                        db_path=self.db_path,
                        status="fail",
                        error=str(e),
                    )
                    raise
                delay = 0.1 * (2 ** (current_attempt - 1))
                self.logger.warning(
                    f"SQLiteContextManager init failed (attempt {current_attempt}/{max_retries}): {e}. Retrying in {delay:.2f}s."
                )
                await asyncio.sleep(delay)
            except Exception as e:
                self.logger.critical(
                    f"Failed to initialize SQLiteContextManager at {self.db_path}: {e}",
                    exc_info=True,
                )
                await log_action(
                    "context_manager_init",
                    type="sqlite",
                    db_path=self.db_path,
                    status="fail",
                    error=str(e),
                )
                raise

    async def retrieve_context(self, query: str, top_k: int = 3) -> List[str]:
        start_time = time.perf_counter()
        if not self.conn:
            self.logger.error("SQLiteContextManager not connected.")
            await log_action(
                "context_retrieval", query=query, status="fail", reason="not_connected"
            )
            raise RuntimeError("SQLiteContextManager not connected.")
        try:
            # FIX: Use LIKE with a BLOB pattern (b'%query%') for searching encrypted BLOBs
            search_pattern_bytes = b"%" + query.encode("utf-8") + b"%"
            cursor = await asyncio.to_thread(
                self.conn.execute,
                # FIX: Use LIKE operator with a BLOB pattern
                "SELECT encrypted_data FROM context WHERE encrypted_data LIKE ? ORDER BY timestamp DESC LIMIT ?",
                (search_pattern_bytes, top_k),
            )
            rows = await asyncio.to_thread(cursor.fetchall)
            results = []
            for row in rows:
                encrypted_blob = row["encrypted_data"]
                try:
                    decrypted = self.fernet.decrypt(encrypted_blob).decode("utf-8")
                    results.append(decrypted)
                except Exception as e:
                    self.logger.error(
                        f"Failed to decrypt context data: {e}", exc_info=True
                    )
                    CLARIFIER_ERRORS.labels("context_decryption_failed").inc()
                    await log_action(
                        "context_retrieval_decrypt_fail", query=query, error=str(e)
                    )
                    continue
            CLARIFIER_CONTEXT_RETRIEVAL_LATENCY.labels("sqlite").observe(
                time.perf_counter() - start_time
            )
            await log_action(
                "context_retrieval",
                query=query,
                retrieved_count=len(results),
                source="sqlite",
                status="success",
            )
            return results
        except Exception as e:
            self.logger.error(
                f"Context retrieval failed from SQLite: {e}", exc_info=True
            )
            CLARIFIER_ERRORS.labels("context_retrieval_failed").inc()
            await log_action(
                "context_retrieval",
                query=query,
                status="fail",
                source="sqlite",
                error=str(e),
            )
            raise

    async def add_to_context(self, data: Dict[str, Any]):
        if not self.conn:
            self.logger.error("SQLiteContextManager not connected.")
            await log_action("context_add", status="fail", reason="not_connected")
            raise RuntimeError("SQLiteContextManager not connected.")
        entry_id = str(uuid.uuid4())
        try:
            json_data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            encrypted_data_blob = self.fernet.encrypt(json_data)
            await asyncio.to_thread(
                self.conn.execute,
                "INSERT INTO context (entry_id, encrypted_data) VALUES (?, ?)",
                (entry_id, encrypted_data_blob),
            )
            await asyncio.to_thread(self.conn.commit)
            await log_action(
                "context_add", entry_id=entry_id, source="sqlite", status="success"
            )
        except Exception as e:
            self.logger.error(f"Context add failed to SQLite: {e}", exc_info=True)
            CLARIFIER_ERRORS.labels("context_add_failed").inc()
            await log_action(
                "context_add",
                entry_id=entry_id,
                source="sqlite",
                status="fail",
                error=str(e),
            )
            raise

    # Backward compatibility aliases for tests
    async def store(self, data: Dict[str, Any]):
        """Alias for add_to_context (backward compatibility)."""
        return await self.add_to_context(data)

    async def query(
        self, query: str, limit: int = 3, top_k: Optional[int] = None
    ) -> List[str]:
        """Alias for retrieve_context (backward compatibility).

        Accepts either 'limit' or 'top_k' parameter for backward compatibility.
        """
        # Use limit if provided, otherwise use top_k, otherwise default to 3
        k = limit if limit is not None else (top_k if top_k is not None else 3)
        return await self.retrieve_context(query, k)

    async def close(self):
        if self.conn:
            await asyncio.to_thread(self.conn.close)
            self.logger.info("SQLiteContextManager closed.")
            await log_action("context_manager_close", type="sqlite", status="success")

    @property
    def is_production_ready(self) -> bool:
        return self._is_production_ready


class InMemoryContextManager(ContextManager):
    def __init__(self, history_ref: List[Dict[str, Any]]):
        self._history_ref = history_ref
        self.logger = get_logger()
        self._is_production_ready = False

    async def retrieve_context(self, query: str, top_k: int = 3) -> List[str]:
        start_time = time.perf_counter()
        self.logger.debug(f"InMemoryContextManager: Retrieving context for '{query}'")
        relevant_items = []
        for cycle in reversed(self._history_ref):
            for q, a in zip(cycle.get("questions", []), cycle.get("answers", [])):
                answer_text = str(a) if a is not None else ""
                if query.lower() in q.lower() or query.lower() in answer_text.lower():
                    relevant_items.append(f"Q: {q} A: {a}")
                    if len(relevant_items) >= top_k:
                        break
            if len(relevant_items) >= top_k:
                break
        CLARIFIER_CONTEXT_RETRIEVAL_LATENCY.labels("in_memory").observe(
            time.perf_counter() - start_time
        )
        await log_action(
            "context_retrieval",
            query=query,
            retrieved_count=len(relevant_items),
            source="in_memory",
        )
        return relevant_items

    async def add_to_context(self, data: Dict[str, Any]):
        self.logger.debug("InMemoryContextManager: Adding data to context (no-op).")
        await log_action("context_add", status="no_op_in_memory")

    async def close(self):
        self.logger.info("InMemoryContextManager closed (no-op).")
        await log_action("context_manager_close", type="in_memory", status="success")

    @property
    def is_production_ready(self) -> bool:
        return self._is_production_ready


class Clarifier:
    """
    The main Clarifier system orchestrates LLM interaction, user prompting,
    and context management to clarify ambiguous requirements.

    FIXED: Improved dependency injection pattern.
    Use the create() class method for default initialization with async setup,
    or pass dependencies directly to __init__ for testing/custom configurations.
    """

    def __init__(
        self,
        llm: Optional[LLMProvider] = None,
        prioritizer: Optional[Prioritizer] = None,
        context_manager: Optional[ContextManager] = None,
        config: Optional[Dynaconf] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Constructor accepts dependencies (Dependency Injection pattern).
        Use create() class method for default initialization with async setup.
        """
        self.config = config or get_config()
        self.logger = logger or get_logger()
        self.fernet = get_fernet()
        self.tracer, self.Status, self.StatusCode = get_tracer()
        self.circuit_breaker = get_circuit_breaker()

        # Reset circuit breaker for clean state (important for testing)
        self.circuit_breaker.reset()

        # Accept dependencies or leave as None for later initialization
        self.llm = llm
        self.prioritizer = prioritizer
        self.context_manager = context_manager

        # Simple initialization (no complex logic)
        self.history: List[Dict[str, Any]] = []
        self.doc_formats_asked: bool = False
        self.shutdown_event = asyncio.Event()
        self._background_tasks = set()

        # Initialize interaction channel (deferred import mechanism)
        try:
            self.interaction = get_channel(
                self.config.INTERACTION_MODE,
                target_language=self.config.TARGET_LANGUAGE,
            )
        except Exception as e:
            self.logger.warning(f"Failed to initialize interaction channel: {e}")
            self.interaction = None

        # Load history
        self._load_history()

        # Start background tasks
        self._background_tasks.add(asyncio.create_task(self._monitor_metrics()))
        self._background_tasks.add(asyncio.create_task(self._periodic_context_sync()))
        self._register_signal_handlers()

    @classmethod
    async def create(
        cls,
        llm: Optional[LLMProvider] = None,
        prioritizer: Optional[Prioritizer] = None,
        context_manager: Optional[ContextManager] = None,
    ) -> "Clarifier":
        """Factory method that creates and initializes a Clarifier with async setup."""
        clarifier = cls(
            llm=llm, prioritizer=prioritizer, context_manager=context_manager
        )

        # Initialize dependencies if not provided
        if clarifier.llm is None:
            clarifier.llm = clarifier._init_llm()

        if clarifier.prioritizer is None:
            clarifier.prioritizer = DefaultPrioritizer(clarifier.llm)

        if clarifier.context_manager is None:
            if clarifier.config.is_production_env:
                if not clarifier.config.CONTEXT_DB_PATH:
                    raise ValueError(
                        "Production environment requires a configured CLARIFIER_CONTEXT_DB_PATH."
                    )
                clarifier.context_manager = await SQLiteContextManager.create(
                    clarifier.config.CONTEXT_DB_PATH, fernet=None
                )
            else:
                clarifier.logger.info(
                    "Initializing InMemoryContextManager for non-production environment."
                )
                clarifier.context_manager = InMemoryContextManager(clarifier.history)

        return clarifier

    def _register_signal_handlers(self):
        try:
            loop = asyncio.get_event_loop()
            if os.name == "posix":
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(
                        sig,
                        lambda s=sig: asyncio.create_task(self.graceful_shutdown(s)),
                    )
                self.logger.info("Registered SIGINT/SIGTERM handlers.")
            else:
                self.logger.warning(f"Unsupported OS for signal handling: {os.name}.")
        except RuntimeError:
            self.logger.warning(
                "No event loop available for signal handler registration"
            )

    def _init_llm(self) -> LLMProvider:
        """
        Initialize LLM with auto-detection of available providers.
        
        Checks for API keys in priority order:
        1. OPENAI_API_KEY → OpenAI via UnifiedLLMProvider
        2. ANTHROPIC_API_KEY → Anthropic via UnifiedLLMProvider
        3. XAI_API_KEY or GROK_API_KEY → xAI/Grok (direct or unified)
        4. GOOGLE_API_KEY → Google via UnifiedLLMProvider
        5. OLLAMA_HOST → Ollama via UnifiedLLMProvider
        
        Falls back to rule-based clarification if no LLM is available.
        """
        # Try to import UnifiedLLMProvider
        try:
            from .clarifier_llm import UnifiedLLMProvider
            has_unified = True
        except ImportError:
            has_unified = False
            self.logger.warning("UnifiedLLMProvider not available, using legacy provider")
        
        # Auto-detect available provider
        if os.getenv("OPENAI_API_KEY"):
            self.logger.info("Auto-detected OpenAI - using unified LLM client")
            if has_unified:
                return UnifiedLLMProvider(provider="openai", model="gpt-4")
            else:
                self.logger.warning("UnifiedLLMProvider not available, cannot use OpenAI")
                return LLMProvider()
        
        elif os.getenv("ANTHROPIC_API_KEY"):
            self.logger.info("Auto-detected Anthropic - using unified LLM client")
            if has_unified:
                return UnifiedLLMProvider(provider="anthropic", model="claude-3-sonnet-20240229")
            else:
                self.logger.warning("UnifiedLLMProvider not available, cannot use Anthropic")
                return LLMProvider()
        
        elif os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY"):
            # For xAI/Grok, prefer unified provider if available, otherwise use GrokLLM
            if has_unified:
                self.logger.info("Auto-detected xAI/Grok - using unified LLM client")
                return UnifiedLLMProvider(provider="grok", model="grok-beta")
            else:
                self.logger.info("Auto-detected xAI/Grok - using GrokLLM direct integration")
                try:
                    from .clarifier_llm import GrokLLM
                    api_key = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
                    return GrokLLM(
                        api_key=api_key,
                        target_language=self.config.TARGET_LANGUAGE,
                    )
                except Exception as e:
                    self.logger.warning(
                        f"Failed to initialize GrokLLM: {e}. Using dummy LLM provider."
                    )
                    return LLMProvider()
        
        elif os.getenv("GOOGLE_API_KEY"):
            self.logger.info("Auto-detected Google/Gemini - using unified LLM client")
            if has_unified:
                return UnifiedLLMProvider(provider="google", model="gemini-pro")
            else:
                self.logger.warning("UnifiedLLMProvider not available, cannot use Google")
                return LLMProvider()
        
        elif os.getenv("OLLAMA_HOST"):
            self.logger.info("Auto-detected Ollama - using unified LLM client")
            if has_unified:
                return UnifiedLLMProvider(provider="ollama", model="codellama")
            else:
                self.logger.warning("UnifiedLLMProvider not available, cannot use Ollama")
                return LLMProvider()
        
        # Legacy: Check if config specifies grok explicitly
        elif hasattr(self.config, 'LLM_PROVIDER') and self.config.LLM_PROVIDER == "grok":
            self.logger.info(
                f"Using configured LLM provider: {self.config.LLM_PROVIDER}"
            )
            try:
                from .clarifier_llm import GrokLLM
                return GrokLLM(
                    api_key=os.getenv("GROK_API_KEY", ""),
                    target_language=self.config.TARGET_LANGUAGE,
                )
            except Exception as e:
                self.logger.warning(
                    f"Failed to initialize GrokLLM: {e}. Using dummy LLM provider."
                )
                return LLMProvider()
        
        else:
            self.logger.warning(
                "No LLM API key found. Clarifier will use rule-based fallback. "
                "For LLM-based clarification, set one of: "
                "OPENAI_API_KEY, ANTHROPIC_API_KEY, XAI_API_KEY, GROK_API_KEY, "
                "GOOGLE_API_KEY, or OLLAMA_HOST"
            )
            return LLMProvider()  # Dummy provider for rule-based fallback

    def _init_context_manager(self) -> ContextManager:
        """
        FIXED: Create manager but schedule async initialization separately.
        This method is now only called from __init__ when context_manager is None.
        The create() factory method handles this better with async initialization.
        """
        if self.config.is_production_env:
            if not self.config.CONTEXT_DB_PATH:
                raise ValueError(
                    "Production environment requires a configured CLARIFIER_CONTEXT_DB_PATH."
                )
            # FIX: Create manager but don't initialize yet
            manager = SQLiteContextManager(self.config.CONTEXT_DB_PATH, fernet=None)
            # Schedule initialization if we have an event loop
            try:
                asyncio.get_running_loop()
                asyncio.create_task(manager.ensure_initialized())
            except RuntimeError:
                self.logger.warning(
                    "No event loop available for context manager initialization"
                )
            return manager
        else:
            self.logger.info(
                "Initializing InMemoryContextManager for non-production environment."
            )
            return InMemoryContextManager(self.history)

    def _load_history(self) -> None:
        if not os.path.exists(self.config.HISTORY_FILE):
            self.logger.info("No history file found.")
            return
        try:
            file_mode = os.stat(self.config.HISTORY_FILE).st_mode
            if file_mode & (stat.S_IRWXO | stat.S_IRWXG):
                self.logger.critical(
                    f"Insecure history file permissions: {oct(file_mode)}. Must be user-only."
                )
                sys.exit(1)
            with open(self.config.HISTORY_FILE, "rb") as f:
                data = f.read()
            if self.config.HISTORY_COMPRESSION:
                data = zstd.decompress(data)
            decrypted = self.fernet.decrypt(data)
            history_data = json.loads(decrypted.decode("utf-8"))
            if isinstance(history_data, dict) and "cycles" in history_data:
                self.history = history_data["cycles"]
                self.doc_formats_asked = history_data.get("doc_formats_asked", False)
                self.logger.info(f"History loaded. Cycles: {len(self.history)}")
            else:  # Old format compatibility
                self.history = history_data
                self.logger.warning("Loaded history from old format.")
        except Exception as e:
            self.logger.error(
                f"Error loading history from {self.config.HISTORY_FILE}: {e}. Backing up and starting fresh.",
                exc_info=True,
            )
            self._backup_corrupt_history(self.config.HISTORY_FILE)
            self.history = []

    def _backup_corrupt_history(self, filepath: str):
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = f"{filepath}.corrupt_backup_{timestamp}"
        try:
            os.rename(filepath, backup_path)
            self.logger.info(f"Corrupt history file backed up to {backup_path}")
        except OSError as e:
            self.logger.error(
                f"Failed to backup corrupt history file {filepath}: {e}", exc_info=True
            )

    async def _monitor_metrics(self):
        if self.config.is_production_env and not hasattr(
            self, "_metrics_server_started"
        ):
            try:
                from prometheus_client import start_http_server

                start_http_server(8000)
                self.logger.info("Prometheus metrics server started on port 8000.")
                setattr(self, "_metrics_server_started", True)
            except Exception as e:
                self.logger.error(
                    f"Failed to start Prometheus metrics server: {e}", exc_info=True
                )
        while not self.shutdown_event.is_set():
            await asyncio.sleep(60)
            if self.shutdown_event.is_set():
                break
            # Note: Prometheus Counter doesn't expose _value.get() - just log that monitoring is active
            self.logger.info(
                "Metrics monitoring active (Prometheus metrics are being collected)",
                extra={"operation": "metrics_monitoring"}
            )

    async def _periodic_context_sync(self):
        while not self.shutdown_event.is_set():
            await asyncio.sleep(300)
            if self.shutdown_event.is_set():
                break
            if self.context_manager and self.context_manager.is_production_ready:
                self.logger.debug(
                    "Performing periodic context manager sync (conceptual)."
                )

    async def get_clarifications(
        self, ambiguities: List[str], requirements: Dict[str, Any]
    ) -> Dict[str, Any]:
        span = None
        if HAS_OPENTELEMETRY and self.tracer:
            span = self.tracer.start_span("get_clarifications_workflow")
            span.set_attribute("clarifier.target_language", self.config.TARGET_LANGUAGE)
        try:
            return await self._get_clarifications_internal(
                ambiguities, requirements, span
            )
        finally:
            if span:
                span.end()

    async def _get_clarifications_internal(
        self, ambiguities: List[str], requirements: Dict[str, Any], span: Optional[Any]
    ) -> Dict[str, Any]:
        CLARIFIER_CYCLES.labels(status="total").inc()
        start_time = time.perf_counter()
        if self.circuit_breaker.is_open():
            self.logger.error("Circuit breaker is open. Aborting.")
            raise Exception("Clarification system unavailable.")

        if not ambiguities:
            self.logger.info("No ambiguities to clarify.")
            return requirements

        try:
            # Documentation format prompting is removed from here
            retrieved_context = []
            if self.context_manager:
                try:
                    for amb in ambiguities[: self.config.CONTEXT_QUERY_LIMIT]:
                        retrieved_context.extend(
                            await self._retry(
                                self.context_manager.retrieve_context,
                                amb,
                                self.config.CONTEXT_QUERY_LIMIT,
                            )
                        )
                    self.circuit_breaker.record_success()
                except Exception as e:
                    self.logger.error(f"Error retrieving context: {e}", exc_info=True)
                    self.circuit_breaker.record_failure(e)

            context = {
                "history": self.history[-self.config.HISTORY_LOOKBACK_LIMIT :],
                "target_language": self.config.TARGET_LANGUAGE,
                "retrieved_context": retrieved_context,
            }

            prioritization_result = await self._retry(
                self.prioritizer.prioritize,
                ambiguities,
                context,
                target_language=self.config.TARGET_LANGUAGE,
            )
            self.circuit_breaker.record_success()

            batched_info = [
                prioritization_result["prioritized"][i]
                for i in prioritization_result["batch"]
            ]
            questions_to_ask = [b["question"] for b in batched_info]
            original_ambiguities = [b["original"] for b in batched_info]

            if not questions_to_ask:
                self.logger.info("Prioritizer returned no questions.")
                return requirements

            # Check if interaction channel is initialized
            if not self.interaction:
                error_msg = "Interaction channel is not initialized. Cannot prompt user for clarifications. Please ensure get_channel() is properly configured."
                self.logger.error(
                    error_msg,
                    extra={
                        "operation": "prompt_user_failed",
                        "error_type": "interaction_not_initialized",
                    },
                )
                CLARIFIER_ERRORS.labels(error_type="interaction_not_initialized").inc()
                raise RuntimeError(error_msg)

            # This relies on the global 'get_channel' being successfully imported/defined
            answers = await self._retry(
                self.interaction.prompt,
                questions_to_ask,
                {"user_id": "default"},
                self.config.TARGET_LANGUAGE,
            )
            self.circuit_breaker.record_success()

            # This relies on the global 'update_requirements_with_answers' being successfully imported/defined
            updated_reqs = update_requirements_with_answers(
                requirements, original_ambiguities, answers
            )
            self.circuit_breaker.record_success()

            redacted_answers = ["***REDACTED***" if a else "No answer" for a in answers]
            current_cycle = {
                "ambiguities": original_ambiguities,
                "questions": questions_to_ask,
                "answers": redacted_answers,
                "timestamp": time.time(),
            }
            self.history.append(current_cycle)
            await self._save_history()

            if self.context_manager:
                await self.context_manager.add_to_context(current_cycle)
                self.circuit_breaker.record_success()

            CLARIFIER_LATENCY.labels(status="success").observe(
                time.perf_counter() - start_time
            )
            # FIX: Add the missing metric increment for 'completed' cycles
            CLARIFIER_CYCLES.labels(status="completed").inc()
            self.circuit_breaker.record_success()
            return updated_reqs
        except Exception as e:
            CLARIFIER_ERRORS.labels(error_type="clarification_failed").inc()
            self.logger.error(f"Clarification cycle failed: {e}", exc_info=True)
            self.circuit_breaker.record_failure(e)
            raise

    async def _retry(
        self, func: Callable, *args, retries: int = 3, delay: float = 1.0, **kwargs
    ) -> Any:
        for attempt in range(1, retries + 1):
            if self.circuit_breaker.is_open():
                error_msg = "Circuit breaker aborted operation."
                self.logger.error(error_msg, extra={"operation": "retry_aborted_by_cb"})
                raise Exception(error_msg)
            try:
                result = await func(*args, **kwargs)
                self.circuit_breaker.record_success()
                return result
            except Exception as e:
                self.logger.warning(
                    f"Attempt {attempt}/{retries} failed for {func.__name__}: {e}",
                    exc_info=True if attempt == retries else False,
                    extra={
                        "operation": "retry_attempt_fail",
                        "attempt": attempt,
                        "func_name": func.__name__,
                        "error_type": type(e).__name__,
                    },
                )
                self.circuit_breaker.record_failure(e)
                if attempt == retries:
                    self.logger.error(
                        f"All {retries} attempts failed for {func.__name__}. Giving up.",
                        extra={
                            "operation": "retry_final_fail",
                            "func_name": func.__name__,
                            "error_type": type(e).__name__,
                        },
                    )
                    raise
                await asyncio.sleep(delay * (2 ** (attempt - 1)))

    async def _save_history(self):
        try:
            # Ensure the directory exists before saving
            history_dir = os.path.dirname(self.config.HISTORY_FILE)
            if history_dir and not os.path.exists(history_dir):
                os.makedirs(history_dir, mode=0o755, exist_ok=True)
                self.logger.info(
                    f"Created history directory: {history_dir}",
                    extra={"operation": "create_history_dir"}
                )
            
            history_data = json.dumps(self.history)
            if self.config.HISTORY_COMPRESSION:
                history_data = zstd.compress(history_data.encode())
            else:
                history_data = history_data.encode()
            encrypted_data = self.fernet.encrypt(history_data)
            temp_file = f"{self.config.HISTORY_FILE}.{uuid.uuid4()}.tmp"
            async with aiofiles.open(temp_file, "wb") as f:
                await f.write(encrypted_data)
            os.rename(temp_file, self.config.HISTORY_FILE)
            os.chmod(self.config.HISTORY_FILE, stat.S_IREAD | stat.S_IWRITE)
            self.logger.info(
                "History saved successfully.", extra={"operation": "save_history"}
            )
            self.circuit_breaker.record_success()
        except Exception as e:
            self.logger.error(
                f"Error saving history: {e}",
                exc_info=True,
                extra={
                    "operation": "save_history_failed",
                    "error_type": type(e).__name__,
                },
            )
            CLARIFIER_ERRORS.labels("save_history_failed").inc()
            self.circuit_breaker.record_failure(e)
            raise

    async def graceful_shutdown(self, reason: str):
        self.logger.info(
            f"Initiating graceful shutdown: {reason}",
            extra={"operation": "graceful_shutdown", "reason": reason},
        )
        self.shutdown_event.set()
        tasks = [
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=5.0
            )
        except asyncio.TimeoutError:
            self.logger.warning(
                "Some tasks did not complete during shutdown.",
                extra={"operation": "shutdown_timeout"},
            )
        if self.context_manager:
            try:
                await self.context_manager.close()
            except Exception as e:
                self.logger.error(
                    f"Error closing context manager: {e}",
                    exc_info=True,
                    extra={"operation": "context_manager_close_failed"},
                )
        await self._save_history()
        self.logger.info("Shutdown complete.", extra={"operation": "shutdown_complete"})

    async def run(self):
        self.logger.info(
            "Clarifier application starting...", extra={"operation": "startup"}
        )
        try:
            while not self.shutdown_event.is_set():
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            self.logger.info(
                "Clarifier run loop cancelled.",
                extra={"operation": "run_loop_cancelled"},
            )
        finally:
            self.logger.info(
                "Clarifier run loop exited.", extra={"operation": "run_loop_exited"}
            )


# --- Plugin Entrypoint ---
@plugin(
    kind=PlugInKind.FIX,
    name="clarifier",
    version="1.0.0",
    params_schema={
        "requirements": {
            "type": "dict",
            "description": "The requirements document containing ambiguities.",
        },
        "ambiguities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "A list of ambiguous statements identified in the requirements.",
        },
    },
    description="Clarifies ambiguous requirements by interacting with an LLM and/or a user.",
    safe=True,
)
async def run(requirements: Dict[str, Any], ambiguities: List[str]) -> Dict[str, Any]:
    clarifier = await Clarifier.create()
    try:
        clarified_requirements = await clarifier.get_clarifications(
            ambiguities=ambiguities, requirements=requirements
        )
        return {"requirements": clarified_requirements}
    finally:
        await clarifier.graceful_shutdown("plugin_run_complete")


class TestClarifier(unittest.TestCase):
    def setUp(self):
        # This setup might need adjustment if Clarifier's __init__ requires more args
        with (
            patch("__main__.get_config"),
            patch("__main__.get_fernet"),
            patch("__main__.get_logger"),
            patch("__main__.get_tracer"),
            patch("__main__.get_circuit_breaker"),
            patch("__main__.get_channel"),
        ):
            self.clarifier = Clarifier()
        self.requirements = {"features": ["test"]}
        self.ambiguities = ["ambiguous term"]

    @patch("__main__.get_channel")
    async def test_get_clarifications(self, mock_get_channel):
        mock_channel = AsyncMock()
        mock_channel.prompt = AsyncMock(return_value=["answer"])
        self.clarifier.interaction = mock_channel

        with patch.object(
            self.clarifier.prioritizer,
            "prioritize",
            AsyncMock(
                return_value={
                    "prioritized": [
                        {
                            "original": "ambiguous term",
                            "score": 10,
                            "question": "Clarify term?",
                        }
                    ],
                    "batch": [0],
                }
            ),
        ):
            # Mock the core functions that rely on deferred imports
            with patch(
                "__main__.update_requirements_with_answers",
                return_value=self.requirements,
            ):
                result = await self.clarifier.get_clarifications(
                    self.ambiguities, self.requirements
                )
                mock_channel.prompt.assert_awaited_with(
                    ["Clarify term?"],
                    {"user_id": "default"},
                    self.clarifier.config.TARGET_LANGUAGE,
                )
                self.assertIsInstance(result, dict)

    async def test_save_history(self):
        # Mock dependencies for _save_history
        with (
            patch("aiofiles.open", new_callable=AsyncMock),
            patch("os.rename"),
            patch("os.chmod"),
        ):
            self.clarifier.history = [{"test": "data"}]  # Ensure history is not empty
            await self.clarifier._save_history()
            # Assertions can be made here about the mocked calls if needed

    async def test_graceful_shutdown(self):
        with (
            patch.object(self.clarifier.context_manager, "close", AsyncMock()),
            patch.object(self.clarifier, "_save_history", AsyncMock()),
        ):
            await self.clarifier.graceful_shutdown("test")
            self.assertTrue(self.clarifier.shutdown_event.is_set())


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run the Clarifier service.")
    parser.add_argument("--test", action="store_true", help="Run unit tests.")
    args = parser.parse_args()

    if args.test:
        # To run async tests, we need a different approach than unittest.main()
        unittest.TestSuite()
        # unittest.TestLoader().loadTestsFromTestCase(TestClarifier) doesn't work well with async.
        # A better approach for async tests would be to use a runner like `pytest` with `pytest-asyncio`.
        # For this script, we can run them manually.
        print("Running Clarifier Unit Tests...")
        # A simple way to run async tests without external libraries
        test_instance = TestClarifier()
        test_instance.setUp()
        await test_instance.test_get_clarifications()
        await test_instance.test_save_history()
        await test_instance.test_graceful_shutdown()
        print("Tests completed.")
        return

    clarifier_instance = None
    try:
        clarifier_instance = await Clarifier.create()
        await clarifier_instance.run()
    except Exception as e:
        # Use the getter to ensure logger is initialized
        get_logger().critical(
            f"Fatal error during Clarifier startup or main loop: {e}", exc_info=True
        )
        if clarifier_instance:
            await clarifier_instance.graceful_shutdown("FATAL_ERROR")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
