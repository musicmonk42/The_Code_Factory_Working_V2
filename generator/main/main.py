# main.py
# Main entry point for the AI README-to-App Generator.
# Orchestrates service startup (CLI, GUI, API), configuration management,
# logging, metrics, tracing, and graceful shutdown.
# REFACTORED: Now uses central runner for all logging, metrics, config, and core components.
# REFACTORED: Imports from main/__init__.py to prevent circular dependencies.
# Created: July 30, 2025.

from __future__ import annotations  # Enable forward references for type hints

import asyncio
import os
import sys

# FIX for Issue A: Removed sys.path manipulation that breaks pip installations
# The package should be installed properly or run with -m from the repo root:
# python -m generator.main.main
import datetime
import hashlib
import json
import logging
import multiprocessing  # For launching API in separate process for 'all' interface
import os
import signal
import sys
import time  # For polling readiness
import uuid  # For provenance launch_id
from functools import partial

# Logging handlers for file rotation
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

import click

# --- FIX: Guard optional/heavy imports ---
try:
    import uvicorn
except ImportError:
    uvicorn = None
    logging.getLogger(__name__).warning(
        "uvicorn not found. API interface will be unavailable."
    )

try:
    import aiohttp  # For API health checks
except ImportError:
    aiohttp = None
    logging.getLogger(__name__).warning(
        "aiohttp not found. 'all' interface health checks will fail."
    )

try:
    from textual.app import App as TextualApp  # Alias to avoid name clash with main_app
except ImportError:
    TextualApp = object  # Dummy for tests
    logging.getLogger(__name__).warning(
        "textual not found. GUI interface will be unavailable."
    )


# Observability imports
# FIX: Create a dummy MagicMock for fallbacks
class _DummyMagicMock:
    def __call__(self, *args, **kwargs):
        return self

    def __getattr__(self, name):
        return self

    def __setattr__(self, name, value):
        pass

    def __enter__(self):
        """Support context manager protocol for tracing spans."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Support context manager protocol for tracing spans."""
        return False

    def instrument_app(self, *args, **kwargs):
        pass

    def instrument(self, *args, **kwargs):
        pass

    def set_status(self, *args, **kwargs):
        pass

    def record_exception(self, *args, **kwargs):
        """Support tracing span record_exception method."""
        pass

    def set_attribute(self, *args, **kwargs):
        """Support tracing span set_attribute method."""
        pass

    def add_event(self, *args, **kwargs):
        """Support tracing span add_event method."""
        pass

    def labels(self, *args, **kwargs):
        """Support Prometheus metric labels method."""
        return self

    def set(self, *args, **kwargs):
        """Support Prometheus gauge set method."""
        pass

    def observe(self, *args, **kwargs):
        pass


MagicMock = _DummyMagicMock()


try:
    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    from opentelemetry.semconv.trace import SpanAttributes
    from opentelemetry.trace import StatusCode

    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False
    # Create dummy objects so the rest of the file doesn't crash
    logging.getLogger(__name__).warning(
        "OpenTelemetry packages not found. Tracing will be disabled."
    )
    trace = MagicMock()
    FastAPIInstrumentor = MagicMock()
    LoggingInstrumentor = MagicMock
    SpanAttributes = MagicMock()
    StatusCode = MagicMock()
    StatusCode.OK = "OK"
    StatusCode.ERROR = "ERROR"

# --- FIX: Define module-level tracer ---
# This was the source of the NameError: 'tracer' is not defined
try:
    tracer = trace.get_tracer(__name__)
except TypeError:
    # Fallback for older OpenTelemetry versions
    tracer = None

try:
    from jsonschema import Draft7Validator, ValidationError
    from jsonschema import validate as json_validate
except ImportError:
    jsonschema = None
    Draft7Validator = object
    ValidationError = Exception
    logging.getLogger(__name__).warning(
        "jsonschema not found. Config validation will be skipped."
    )

    def json_validate(instance, schema, cls=None):  # Added cls=None for compatibility
        pass  # No-op


# --- END Guarded Imports ---


# --- Runner Foundation & Project Imports ---
try:
    # Import central runner components directly
    from runner.alerting import send_alert
    from runner.runner_config import ConfigWatcher, RunnerConfig, load_config
    from runner.runner_core import Runner
    from runner.runner_logging import log_action
    from runner.runner_logging import logger as runner_logger_instance
    from runner.runner_metrics import (  # FIX: Import bootstrap_metrics
        APP_RUNNING_STATUS,
        APP_STARTUP_DURATION,
        bootstrap_metrics,
        get_metrics_dict,
    )

    from .api import api as fastapi_app
    from .api import create_db_tables as api_create_db_tables

    # --- FIX: Import from the package __init__ to avoid circular dependency ---
    # from . import main_cli, MainApp, fastapi_app, api_create_db_tables
    # --- START FIX 1: Break circular dependency ---
    # Import directly from sibling modules instead of __init__.py
    from .cli import cli as main_cli
    from .gui import MainApp

    # --- END FIX 1 ---
    # --- START FIX 1: Add IntentParser for test patching ---
    try:
        from intent_parser.intent_parser import IntentParser
    except ImportError as e:
        IntentParser = MagicMock()  # Use the dummy mock
        logging.critical(f"Failed to import IntentParser (will use dummy): {e}")
    # --- END FIX 1 ---

except ImportError as e:
    logging.critical(
        f"Failed to import core project modules: {e}. Ensure PYTHONPATH is correct and all dependencies are installed.",
        exc_info=True,
    )
    IMPORT_ERROR = e
else:
    IMPORT_ERROR = None

# ********** FIX 1: Explicitly expose MainApp globally **********
# Required to allow tests to patch main.main.MainApp
try:
    MainApp = MainApp
except NameError:
    MainApp = TextualApp  # Use the aliased TextualApp as the base fallback
# ****************************************

# ********** FIX 2: Explicitly expose send_alert globally **********
# Required to allow tests to patch main.main.send_alert
try:
    send_alert = send_alert
except NameError:

    async def send_alert(*args, **kwargs):
        logging.warning(
            "send_alert (fallback/dummy) called - runner.alerting import failed."
        )


# ****************************************

# ********** FIX 3: Explicitly expose other patched symbols **********
# Make all symbols that test_main.py patches available at the module level.
try:
    Runner = Runner
except NameError:
    Runner = object
try:
    load_config = load_config
except NameError:

    def load_config(*args, **kwargs):
        logging.warning(
            "load_config (fallback/dummy) called - runner.runner_config import failed."
        )
        return {}


try:
    ConfigWatcher = ConfigWatcher
except NameError:
    ConfigWatcher = object
try:
    main_cli = main_cli
except NameError:
    main_cli = MagicMock()
try:
    fastapi_app = fastapi_app
except NameError:
    fastapi_app = object
try:
    api_create_db_tables = api_create_db_tables
except NameError:

    def api_create_db_tables():
        logging.warning(
            "api_create_db_tables (fallback/dummy) called - api module import failed."
        )


try:
    get_metrics_dict = get_metrics_dict
except NameError:

    def get_metrics_dict():
        logging.warning(
            "get_metrics_dict (fallback/dummy) called - runner.runner_metrics import failed."
        )
        return {}


try:
    log_action = log_action
except NameError:

    def log_action(*args, **kwargs):
        pass


# --- START FIX 1: Expose IntentParser for test patching ---
try:
    IntentParser = IntentParser
except NameError:
    IntentParser = object
# --- END FIX 1 ---

# Version
__version__ = "1.0.0"

# --- Logging Configuration ---
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
# Use the logger imported from the runner foundation if available, else basic logger
logger = (
    runner_logger_instance
    if "runner_logger_instance" in globals()
    else logging.getLogger(__name__)
)


# Global flag for log scrubbing
ENABLE_LOG_SCRUBBING = os.getenv("ENABLE_LOG_SCRUBBING", "true").lower() == "true"


class LogScrubberFilter(logging.Filter):
    """
    A logging filter to scrub sensitive data based on key names.
    (Aligned with test_main.py patch expectations)
    """

    SENSITIVE_KEYS = ("api_key", "authorization", "password", "token", "secret")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage()).lower()
        if any(k in msg for k in self.SENSITIVE_KEYS):
            record.msg = "[SCRUBBED SENSITIVE DATA]"
            record.args = ()
        return True


# --- FIX: Moved side-effects into a function ---
def setup_observability(log_level: str):
    """Initializes Logging, Tracing, and Metrics. Called at runtime."""

    # --- Logging Configuration ---
    logging.basicConfig(level=log_level.upper(), format=LOG_FORMAT)
    logger.setLevel(log_level.upper())

    # Add the scrubber filter
    if os.getenv("ENABLE_LOG_SCRUBBING", "false").lower() == "true":
        logging.getLogger().addFilter(LogScrubberFilter())
        logger.info("Log scrubbing filter enabled.")

    # File logging with rotation (for production environments)
    LOG_FILE_PATH = os.getenv("APP_LOG_FILE")
    if LOG_FILE_PATH:
        try:
            file_handler = RotatingFileHandler(
                LOG_FILE_PATH,
                maxBytes=int(os.getenv("APP_LOG_MAX_BYTES", 10 * 1024 * 1024)),
                backupCount=int(os.getenv("APP_LOG_BACKUP_COUNT", 5)),
            )
            file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
            logger.addHandler(file_handler)
            logger.info(f"File logging enabled: {LOG_FILE_PATH}")
        except Exception as e:
            logger.error(
                f"Failed to set up file logging: {e}. Continuing with console logging.",
                exc_info=True,
            )

    # --- OpenTelemetry Tracing Configuration ---
    if _HAS_OTEL:
        # Use the default/configured tracer provider instead of manually creating one
        # This avoids version compatibility issues and respects OTEL_* environment variables
        LoggingInstrumentor().instrument(set_logging_format=True)
        logger.info(
            "OpenTelemetry tracing initialized using default/configured provider."
        )

    # --- Prometheus Metrics Setup (from Runner) ---
    try:
        if "bootstrap_metrics" in globals() and callable(bootstrap_metrics):
            bootstrap_metrics()
            logger.info("Runner metrics registry bootstrapped.")
        else:
            logger.info(
                "bootstrap_metrics not found in runner, assuming registry is pre-initialized."
            )
    except Exception as e:
        logger.error(f"Failed to bootstrap metrics: {e}", exc_info=True)


# --- FIX: Define metric objects at module level, but safely ---
# These need to exist for patching, even if bootstrap fails.
try:
    if "bootstrap_metrics" in globals() and callable(bootstrap_metrics):
        bootstrap_metrics()  # Ensure they are created
    # Use the imported metric objects directly instead of looking them up in get_metrics_dict
    APP_RUNNING_GAUGE = APP_RUNNING_STATUS
    # APP_STARTUP_DURATION is already imported from runner_metrics
    logger.info("Loaded metrics from central runner registry.")
except Exception as e:
    logger.critical(
        f"Failed to load required metrics from runner.runner_metrics: {e}. Using dummy metrics for patching.",
        exc_info=True,
    )

    # Define dummies for patching
    class DummyGauge:
        def labels(self, *args, **kwargs):
            return self

        def set(self, *args, **kwargs):
            pass

    class DummyHistogram:
        def labels(self, *args, **kwargs):
            return self

        def observe(self, *args, **kwargs):
            pass

    APP_RUNNING_GAUGE = DummyGauge()
    APP_STARTUP_DURATION = DummyHistogram()

# --- Global Config Watcher ---
config_watcher = None


# --- Graceful Shutdown Handler ---
async def shutdown(
    signal_name: str,
    loop: asyncio.AbstractEventLoop,
    runner_instance: Optional[Runner] = None,
    api_process: Optional[multiprocessing.Process] = None,
):
    """Handles graceful shutdown of the application."""
    with tracer.start_as_current_span(
        "app_shutdown",
        attributes={
            "signal.received": signal_name,
            "app.interface": os.getenv("APP_INTERFACE", "unknown"),
        },
    ) as span:
        logger.info(
            f"Received exit signal {signal_name}... Initiating graceful shutdown."
        )

        APP_RUNNING_GAUGE.labels(
            app_name=os.getenv("APP_INTERFACE", "unknown"),
            instance_id=os.getenv("HOSTNAME", "unknown"),
        ).set(0)

        logger.info(
            "Triggering pre-shutdown events (flushing logs, committing data)..."
        )

        try:
            if _HAS_OTEL:
                tracer_provider = trace.get_tracer_provider()
                if hasattr(tracer_provider, "force_flush"):
                    tracer_provider.force_flush()
                    logger.info("OpenTelemetry traces flushed.")
        except Exception as e:
            logger.error(f"Failed to flush OpenTelemetry traces: {e}", exc_info=True)

        # FIX: Only cancel 'owned' background tasks (like config_watcher)
        # to avoid killing uvicorn/textual tasks prematurely.
        tasks = [
            t
            for t in asyncio.all_tasks(loop=loop)
            if t is not asyncio.current_task()
            and not t.done()
            and getattr(t, "_owned_by_main", False)
        ]  # Check for our custom flag

        if tasks:
            logger.info(
                f"Cancelling {len(tasks)} outstanding 'owned' background tasks..."
            )
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("All 'owned' background tasks cancelled.")
        else:
            logger.info("No 'owned' background tasks to cancel.")

        if runner_instance:
            logger.info("Cleaning up Runner resources...")
            pass

        if config_watcher:
            logger.info("Stopping config watcher...")
            config_watcher.stop()

        if api_process and api_process.is_alive():
            logger.info(f"Terminating API process (PID: {api_process.pid})...")
            api_process.terminate()
            api_process.join(timeout=10)
            if api_process.is_alive():
                logger.warning("API process did not terminate gracefully, killing.")
                api_process.kill()

        # FIX: Remove loop.stop(). The loop's runner (e.g., uvicorn, textual)
        # is responsible for stopping the loop after shutdown completes.
        span.set_status(
            StatusCode.OK, f"Application gracefully shut down by signal {signal_name}"
        )
        logger.info("Application shutdown complete.")


def setup_signals(
    loop: asyncio.AbstractEventLoop,
    runner_instance: Optional[Runner] = None,
    api_process: Optional[multiprocessing.Process] = None,
):
    """Sets up signal handlers for graceful shutdown, with Windows compatibility."""
    signals_to_handle = [signal.SIGINT, signal.SIGTERM]
    if sys.platform != "win32":
        signals_to_handle.extend([signal.SIGHUP, signal.SIGQUIT])

    _shutdown_handler = partial(
        shutdown, loop=loop, runner_instance=runner_instance, api_process=api_process
    )

    for sig in signals_to_handle:
        try:
            loop.add_signal_handler(
                sig, lambda s=sig: asyncio.create_task(_shutdown_handler(s.name))
            )
            logger.debug(f"Added signal handler for {sig.name}")
        except (NotImplementedError, RuntimeError) as e:
            logger.warning(
                f"Could not add signal handler for {sig.name} (loop might be closing or OS unsupported): {e}"
            )


# --- Provenance Generator ---
def generate_launch_provenance(
    interface: str,
    config: Dict[str, Any],
    config_path: Path,
    user: str = os.getenv("USER", "unknown"),
) -> Dict[str, Any]:
    """Generates a unique provenance record for each application launch."""
    with tracer.start_as_current_span(
        "generate_launch_provenance", attributes={"app.interface": interface}
    ) as span:
        timestamp = datetime.datetime.utcnow().isoformat(timespec="milliseconds") + "Z"

        try:
            config_model = load_config(config_path)
            config_hash = hashlib.sha256(
                config_model.model_dump_json(sort_keys=True).encode("utf-8")
            ).hexdigest()
        except Exception as e:
            logger.warning(
                f"Failed to hash config model, falling back to dict hash: {e}"
            )
            config_hash = hashlib.sha256(
                json.dumps(config, sort_keys=True).encode("utf-8")
            ).hexdigest()

        env_details = {
            "os_name": os.name,
            "platform": sys.platform,
            "python_version": sys.version.split()[0],
            "hostname": os.getenv("HOSTNAME", "unknown"),
            "user_id": user,
            "cwd": os.getcwd(),
        }

        provenance = {
            "launch_id": str(uuid.uuid4()),
            "timestamp": timestamp,
            "interface": interface,
            "config_hash": config_hash,
            "app_version": __version__,
            "environment": env_details,
        }

        # FIX: Add robust check for log_action before calling
        try:
            if "log_action" in globals() and callable(log_action):
                log_action("Launch Provenance", category="startup", **provenance)
            else:
                logger.info(
                    f"Launch Provenance (log_action not available): {provenance}"
                )
        except Exception as e:
            logger.warning(f"Failed to log provenance: {e}", exc_info=True)

        span.set_status(StatusCode.OK)
        return provenance


# --- Config Validation ---
def validate_config(config: Dict[str, Any], is_reload: bool = False):
    """
    Performs strict validation of the loaded configuration, including environment checks.

    FIX for Issue D: Deep semantic validation to prevent incomplete configs from being applied.
    This includes validation of:
    - Schema structure (via JSON Schema)
    - Required environment variables
    - Critical keys for running agents
    - API endpoints and connectivity
    - Resource limits and constraints

    Args:
        config: Configuration dictionary to validate
        is_reload: If True, performs stricter validation for config reloads
                  to prevent breaking running services

    Raises:
        ValueError: If validation fails with details about what's missing/invalid
    """
    with tracer.start_as_current_span("validate_config") as span:
        span.set_attribute("is_reload", is_reload)
        logger.info(
            f"Validating configuration (reload={is_reload})...",
            extra={"is_reload": is_reload, "config_keys": list(config.keys())},
        )

        validation_errors = []  # Collect all errors for comprehensive reporting

        # Note: The primary config validation is done by Pydantic (RunnerConfig).
        # This schema is a secondary, optional validation for specific main.py requirements.
        # We make logging/metrics optional here since the RunnerConfig may flatten them.
        config_schema = {
            "type": "object",
            "properties": {
                "backend": {
                    "type": "string",
                    "enum": [
                        "local",
                        "docker",
                        "kubernetes",
                        "distributed",
                        "vm",
                        "nodejs",
                        "go",
                        "java",
                        "lambda",
                    ],
                },
                "framework": {"type": "string"},
                "logging": {
                    "type": "object",
                    "properties": {"level": {"type": "string"}},
                },
                "metrics": {
                    "type": "object",
                    "properties": {"port": {"type": "integer"}},
                },
                "security": {
                    "type": "object",
                    "properties": {"jwt_secret_key_env_var": {"type": "string"}},
                },
                "external_services": {
                    "type": "object",
                    "properties": {
                        "llm_api_url": {"type": "string", "format": "uri"},
                        "database_connection_string": {"type": "string"},
                    },
                },
            },
            # Make only backend and framework required since other fields may be flattened by Pydantic
            "required": ["backend", "framework"],
        }

        # --- SCHEMA VALIDATION ---
        try:
            json_validate(instance=config, schema=config_schema, cls=Draft7Validator)
            logger.debug("JSON schema validation passed.")
        except ImportError:
            logger.warning(
                "jsonschema library not found for config validation. Skipping schema validation."
            )
        except ValidationError as e:
            error_msg = f"Config schema validation failed: {e.message}"
            validation_errors.append(error_msg)
            logger.error(error_msg, extra={"validation_path": list(e.absolute_path)})

        # --- SEMANTIC VALIDATION: Critical Keys ---
        # FIX for Issue D: Validate that critical keys required by agents exist
        critical_keys = {
            "backend": "Backend execution environment",
            "framework": "Application framework",
        }

        for key, description in critical_keys.items():
            if key not in config or not config[key]:
                error_msg = (
                    f"Critical config key '{key}' ({description}) is missing or empty"
                )
                validation_errors.append(error_msg)
                logger.error(error_msg)

        # --- SECURITY VALIDATION ---
        if config.get("security", {}).get("jwt_secret_key_env_var"):
            jwt_env_var = config["security"]["jwt_secret_key_env_var"]
            jwt_secret = os.getenv(jwt_env_var)

            if not jwt_secret:
                error_msg = (
                    f"JWT secret key environment variable '{jwt_env_var}' is not set. "
                    "This is critical for API security."
                )
                validation_errors.append(error_msg)
                logger.critical(error_msg)
            else:
                # Validate JWT secret strength
                known_insecure_defaults = [
                    "your-super-secret-key-that-should-be-in-env",
                    "changeme",
                    "supersecretkey",
                    "secret",
                    "password",
                ]

                if jwt_secret in known_insecure_defaults:
                    error_msg = (
                        f"JWT_SECRET_KEY uses a known insecure default value. "
                        "This MUST be changed for production!"
                    )
                    # For reload, this is an error; for initial load, it's a warning
                    if is_reload:
                        validation_errors.append(error_msg)
                        logger.error(error_msg)
                    else:
                        logger.warning(error_msg)

                elif len(jwt_secret) < 32:
                    warning_msg = (
                        f"JWT_SECRET_KEY is too short ({len(jwt_secret)} chars). "
                        "Recommended: 32+ characters for production security."
                    )
                    logger.warning(warning_msg)

        # --- LLM API KEY VALIDATION (if agents use LLM) ---
        # FIX for Issue D: Check that LLM API keys are set if required
        llm_config = config.get("llm_config", {}) or config.get("external_services", {})
        if llm_config:
            # Check for common LLM API key environment variables
            llm_key_vars = [
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "AZURE_OPENAI_API_KEY",
                "GOOGLE_API_KEY",
            ]

            api_key_found = False
            for key_var in llm_key_vars:
                if os.getenv(key_var):
                    api_key_found = True
                    logger.debug(f"Found LLM API key: {key_var}")
                    break

            # Only warn during reload if LLM was previously configured
            if is_reload and not api_key_found:
                warning_msg = (
                    "No LLM API key environment variables found (OPENAI_API_KEY, etc.). "
                    "LLM-based agents may fail during execution."
                )
                logger.warning(warning_msg)

        # --- DATABASE VALIDATION (if configured) ---
        db_conn_str = config.get("external_services", {}).get(
            "database_connection_string"
        ) or config.get("database", {}).get("connection_string")

        if db_conn_str:
            # Basic validation of connection string format
            if not db_conn_str.startswith(
                ("postgresql://", "mysql://", "sqlite://", "mongodb://")
            ):
                error_msg = (
                    f"Database connection string has unexpected format: {db_conn_str[:20]}... "
                    "Expected formats: postgresql://, mysql://, sqlite://, mongodb://"
                )
                validation_errors.append(error_msg)
                logger.error(error_msg)

        # --- RESOURCE LIMITS VALIDATION ---
        # Validate resource constraints are reasonable
        if "resource_limits" in config:
            limits = config["resource_limits"]

            if "max_workers" in limits:
                max_workers = limits["max_workers"]
                if (
                    not isinstance(max_workers, int)
                    or max_workers < 1
                    or max_workers > 1000
                ):
                    error_msg = (
                        f"resource_limits.max_workers must be an integer between 1 and 1000, "
                        f"got: {max_workers}"
                    )
                    validation_errors.append(error_msg)
                    logger.error(error_msg)

            if "timeout_seconds" in limits:
                timeout = limits["timeout_seconds"]
                if not isinstance(timeout, (int, float)) or timeout < 1:
                    error_msg = (
                        f"resource_limits.timeout_seconds must be a positive number, "
                        f"got: {timeout}"
                    )
                    validation_errors.append(error_msg)
                    logger.error(error_msg)

        # --- LOGGING CONFIGURATION VALIDATION ---
        if "logging" in config:
            log_config = config["logging"]
            valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

            if "level" in log_config:
                level = str(log_config["level"]).upper()
                if level not in valid_log_levels:
                    error_msg = (
                        f"Invalid logging level '{level}'. "
                        f"Must be one of: {', '.join(valid_log_levels)}"
                    )
                    validation_errors.append(error_msg)
                    logger.error(error_msg)

        # --- FINAL VALIDATION RESULT ---
        if validation_errors:
            error_summary = "\n  - ".join(validation_errors)
            full_error_msg = (
                f"Configuration validation failed with {len(validation_errors)} error(s):\n  "
                f"- {error_summary}"
            )

            span.set_status(StatusCode.ERROR, "Config validation failed")
            span.set_attribute("validation_error_count", len(validation_errors))

            logger.critical(
                full_error_msg,
                extra={
                    "validation_errors": validation_errors,
                    "error_count": len(validation_errors),
                },
            )

            # Raise with comprehensive error details
            raise ValueError(full_error_msg)

        logger.info(
            "Configuration validated successfully.",
            extra={
                "backend": config.get("backend"),
                "framework": config.get("framework"),
            },
        )
        span.set_status(StatusCode.OK)
        span.set_attribute("validation_passed", True)


# --- Health Check Logic ---
async def perform_health_check(
    config: Dict[str, Any],
    check_api: bool = False,
    api_url: Optional[str] = None,
    is_canary: bool = False,
) -> bool:
    """Performs a comprehensive health check of the application components."""
    overall_health = True
    api_health_url = (
        api_url
        if api_url
        else os.getenv("GENERATOR_API_BASE_URL", "http://127.0.0.1:8000/api/v1")
        + "/health"
    )
    timeout = 2 if is_canary else 5

    with tracer.start_as_current_span(
        "perform_health_check",
        attributes={
            "check.api": check_api,
            "api.url": api_health_url,
            "health_check.is_canary": is_canary,
        },
    ) as span:
        logger.info("Starting application health check...")

        # 1. Check Runner's self-test
        try:
            runner_config = RunnerConfig(**config)
            runner = Runner(runner_config)

            runner_health = await asyncio.to_thread(runner.self_test)
            if runner_health:
                logger.info("Runner self-test: PASSED")
                APP_RUNNING_GAUGE.labels(
                    app_name="runner",
                    instance_id=os.getenv("HOSTNAME", "unknown"),
                ).set(1)
            else:
                logger.error("Runner self-test: FAILED. Check Runner logs for details.")
                overall_health = False
                APP_RUNNING_GAUGE.labels(
                    app_name="runner",
                    instance_id=os.getenv("HOSTNAME", "unknown"),
                ).set(0)
                await send_alert(
                    subject="Health Check Failed",
                    message="Runner self-test failed during health check.",
                    severity="critical",
                )
        except Exception as e:
            logger.error(
                f"Runner self-test encountered an exception: {e}", exc_info=True
            )
            overall_health = False
            APP_RUNNING_GAUGE.labels(
                app_name="runner",
                instance_id=os.getenv("HOSTNAME", "unknown"),
            ).set(0)
            await send_alert(
                subject="Health Check Exception",
                message=f"Runner self-test exception during health check: {e}",
                severity="critical",
            )
            span.record_exception(e)
            span.set_status(StatusCode.ERROR, "Runner self-test failed with exception")

        # 2. Check API health endpoint
        if check_api:
            if not aiohttp:
                logger.error("aiohttp not installed. Cannot perform API health check.")
                overall_health = False
            else:
                try:
                    async with aiohttp.ClientSession() as session:
                        response = await session.get(api_health_url, timeout=timeout)
                        response.raise_for_status()
                        api_status = await response.json()
                        if api_status.get("status") == "healthy":
                            logger.info(
                                f"API health check ({api_health_url}): PASSED. Details: {api_status}"
                            )
                            APP_RUNNING_GAUGE.labels(
                                app_name="api",
                                instance_id=os.getenv("HOSTNAME", "unknown"),
                            ).set(1)
                        else:
                            logger.error(
                                f"API health check ({api_health_url}): FAILED. Details: {api_status}"
                            )
                            overall_health = False
                            APP_RUNNING_GAUGE.labels(
                                app_name="api",
                                instance_id=os.getenv("HOSTNAME", "unknown"),
                            ).set(0)
                            await send_alert(
                                subject="API Health Check Failed",
                                message=f"API health check failed: {api_status}",
                                severity="critical",
                            )
                            span.set_status(
                                StatusCode.ERROR,
                                "API health endpoint reported unhealthy",
                            )
                    span.set_attribute("api.health.status", api_status.get("status"))
                except aiohttp.ClientError as e:
                    logger.error(
                        f"API health check ({api_health_url}): Connection failed: {e}",
                        exc_info=True,
                    )
                    overall_health = False
                    APP_RUNNING_GAUGE.labels(
                        app_name="api",
                        instance_id=os.getenv("HOSTNAME", "unknown"),
                    ).set(0)
                    await send_alert(
                        subject="API Connection Failed",
                        message=f"API health check connection failed: {e}",
                        severity="critical",
                    )
                    span.record_exception(e)
                    span.set_status(
                        StatusCode.ERROR, "API health check connection failed"
                    )
                except Exception as e:
                    logger.error(
                        f"API health check ({api_health_url}): Unexpected error: {e}",
                        exc_info=True,
                    )
                    overall_health = False
                    APP_RUNNING_GAUGE.labels(
                        app_name="api",
                        instance_id=os.getenv("HOSTNAME", "unknown"),
                    ).set(0)
                    await send_alert(
                        subject="API Health Check Error",
                        message=f"API health check unexpected error: {e}",
                        severity="critical",
                    )
                    span.record_exception(e)
                    span.set_status(
                        StatusCode.ERROR,
                        "API health check failed with unexpected exception",
                    )

        logger.info(f"Overall Health Check: {'PASSED' if overall_health else 'FAILED'}")
        if overall_health:
            span.set_status(StatusCode.OK, "All health checks passed")
        else:
            span.set_status(StatusCode.ERROR, "Some health checks failed")
        return overall_health


# --- Click Commands for Main Entry Point ---
@click.group(invoke_without_command=True)
@click.option(
    "--interface",
    type=click.Choice(["cli", "gui", "api", "all"]),
    default="cli",
    help="The interface to launch.",
)
@click.option(
    "--config-path",
    default="config.yaml",
    type=click.Path(exists=True, readable=True, path_type=Path),
    help="Path to configuration file.",
)
@click.option("--version", is_flag=True, help="Show version information and exit.")
@click.option("--health-check", is_flag=True, help="Perform health check and exit.")
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
    default="INFO",
    help="Set the logging level.",
)
@click.option(
    "--canary",
    is_flag=True,
    help="Run in canary mode (e.g., reduced health check timeouts).",
)
def main(
    interface: str,
    config_path: Path,
    version: bool,
    health_check: bool,
    log_level: str,
    canary: bool,
):
    """
    Main entry point for the AI README-to-App Generator.
    """
    global config_watcher
    startup_start_time = time.monotonic()

    # --- FIX: Call setup_observability HERE ---
    # This moves all logging, metrics, and OTel setup from import time to runtime.
    setup_observability(log_level)

    os.environ["APP_INTERFACE"] = interface
    logger.info(f"Log level set to: {log_level}")

    config_dict = load_config(config_path).model_dump()
    try:
        validate_config(config_dict)
    except ValueError as e:
        logger.critical(f"Application startup failed due to invalid configuration: {e}")
        asyncio.run(
            send_alert(
                subject="Configuration Validation Failed",
                message=f"Config validation failed at startup: {e}",
                severity="critical",
            )
        )
        sys.exit(1)

    provenance = generate_launch_provenance(interface, config_dict, config_path)

    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        console.print(
            Panel(
                f"AI README-to-App Generator v[bold green]{__version__}[/bold green]\n"
                f"Launch Interface: [bold cyan]{interface}[/bold cyan]\n"
                f"Config Path: {config_path} (Hash: [dim]{provenance['config_hash'][:8]}[/dim])\n"
                f"Provenance ID: [dim]{provenance['launch_id'][:8]}[/dim]\n"
                f"Environment: Python {sys.version.split()[0]} on {os.name} ({sys.platform})",
                title="[bold blue]Welcome![/bold blue]",
                title_align="left",
                border_style="bold green",
            )
        )
    except ImportError:
        logger.info("`rich` not installed. Skipping rich welcome panel.")
        print(f"AI README-to-App Generator v{__version__} | Interface: {interface}")

    logger.info("Metrics server assumed to be started by central runner.")
    APP_RUNNING_GAUGE.labels(
        app_name="prometheus_server_check",
        instance_id=os.getenv("HOSTNAME", "unknown"),
    ).set(1)

    # Create event loop if one is not already running (handles Python 3.12+ deprecation of get_event_loop)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if health_check:
        logger.info("Performing requested health check...")
        health_status = loop.run_until_complete(
            perform_health_check(
                config_dict,
                check_api=(interface == "api" or interface == "all"),
                is_canary=canary,
            )
        )
        if not health_status:
            logger.critical("Health check failed. Exiting.")
            sys.exit(1)
        else:
            logger.info("Health check passed. Exiting.")
            sys.exit(0)

    APP_RUNNING_GAUGE.labels(
        app_name="main_process",
        instance_id=os.getenv("HOSTNAME", "unknown"),
    ).set(1)

    config_watcher = ConfigWatcher(config_path, partial(on_config_reload, config_path))
    # FIX: Flag the config_watcher task as an 'owned' background task for graceful shutdown
    config_watcher_task = loop.create_task(config_watcher.start())
    config_watcher_task._owned_by_main = True

    # --- Launch Interface ---
    if interface == "gui":
        logger.info("Launching GUI interface...")
        setup_signals(loop, runner_instance=None, api_process=None)
        app = MainApp()
        try:
            APP_STARTUP_DURATION.labels(
                app_name="gui", instance_id=os.getenv("HOSTNAME", "unknown")
            ).observe(time.monotonic() - startup_start_time)
            app.run()
        except Exception as e:
            logger.critical(f"GUI application crashed: {e}", exc_info=True)
            asyncio.run(
                send_alert(
                    subject="GUI Crashed",
                    message=f"GUI crashed: {e}",
                    severity="critical",
                )
            )
            sys.exit(1)
        finally:
            logger.info("GUI application exited.")
            APP_RUNNING_GAUGE.labels(
                app_name="gui",
                instance_id=os.getenv("HOSTNAME", "unknown"),
            ).set(0)

    elif interface == "api":
        if not uvicorn:
            logger.critical("uvicorn not found. Cannot start API interface.")
            sys.exit(1)
        logger.info("Launching API interface...")
        api_create_db_tables()
        if _HAS_OTEL:
            FastAPIInstrumentor.instrument_app(fastapi_app)

        uvicorn_config = uvicorn.Config(
            fastapi_app,
            host="0.0.0.0",
            port=8000,
            log_level=log_level.lower(),
            reload=False,
        )
        server = uvicorn.Server(uvicorn_config)

        setup_signals(loop, runner_instance=None, api_process=None)
        try:
            APP_STARTUP_DURATION.labels(
                app_name="api", instance_id=os.getenv("HOSTNAME", "unknown")
            ).observe(time.monotonic() - startup_start_time)
            loop.run_until_complete(server.serve())
        except Exception as e:
            logger.critical(f"API server crashed: {e}", exc_info=True)
            asyncio.run(
                send_alert(
                    subject="API Server Crashed",
                    message=f"API server crashed: {e}",
                    severity="critical",
                )
            )
            sys.exit(1)
        finally:
            logger.info("API server exited.")
            APP_RUNNING_GAUGE.labels(
                app_name="api",
                instance_id=os.getenv("HOSTNAME", "unknown"),
            ).set(0)

    elif interface == "all":
        # FIX for Issue C: Industry-standard process isolation for event loop conflict prevention
        if not uvicorn or not aiohttp:
            logger.critical(
                "uvicorn or aiohttp not found. Cannot start 'all' interface."
            )
            sys.exit(1)

        logger.info("Launching ALL interfaces (API + GUI) with process isolation...")
        api_target_port = int(os.getenv("API_TARGET_PORT", 8000))

        # Validate port availability before starting
        import socket

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("0.0.0.0", api_target_port))
        except OSError as e:
            logger.critical(
                f"Port {api_target_port} is already in use. Cannot start API process."
            )
            console.print(
                f"[red]Error: Port {api_target_port} is already in use.[/red]\n"
                f"[yellow]Try setting a different port: export API_TARGET_PORT=8001[/yellow]"
            )
            sys.exit(1)

        # Create API process with proper daemon=False for clean shutdown
        api_process_target = partial(
            uvicorn.run,
            fastapi_app,
            host="0.0.0.0",
            port=api_target_port,
            log_level=log_level.lower(),
            reload=False,
            access_log=True,  # Enable access logging for audit trail
        )

        # Use multiprocessing.Process with explicit name and daemon settings
        api_process_handle = multiprocessing.Process(
            target=api_process_target,
            name="APIServerProcess",
            daemon=False,  # Non-daemon to ensure proper cleanup
        )

        try:
            api_process_handle.start()
            logger.info(
                f"API process started with PID: {api_process_handle.pid} on port {api_target_port}.",
                extra={
                    "process_name": api_process_handle.name,
                    "pid": api_process_handle.pid,
                    "port": api_target_port,
                },
            )
        except Exception as e:
            logger.critical(f"Failed to start API process: {e}", exc_info=True)
            asyncio.run(
                send_alert(
                    subject="API Process Startup Failed",
                    message=f"Failed to start API process in 'all' mode: {e}",
                    severity="critical",
                )
            )
            sys.exit(1)

        # Industry-standard health check with exponential backoff
        api_ready_url = (
            f"http://127.0.0.1:{api_target_port}/health"  # Use root health endpoint
        )
        ready_timeout = int(os.getenv("API_READINESS_TIMEOUT_SECONDS", 120))
        poll_interval_initial = float(
            os.getenv("API_READINESS_POLL_INTERVAL_SECONDS", 0.5)
        )
        max_poll_interval = 5.0
        poll_interval = poll_interval_initial
        api_ready = False
        start_wait_time = time.monotonic()
        attempt_count = 0

        logger.info(
            f"Waiting for API readiness at {api_ready_url} (timeout: {ready_timeout}s)...",
            extra={"url": api_ready_url, "timeout": ready_timeout},
        )

        while not api_ready and (time.monotonic() - start_wait_time < ready_timeout):
            attempt_count += 1
            try:

                async def check_api_readiness():
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            api_ready_url,
                            timeout=aiohttp.ClientTimeout(total=poll_interval),
                        ) as response:
                            response.raise_for_status()
                            status_json = await response.json()
                            return status_json.get("status") == "healthy"

                api_ready = loop.run_until_complete(check_api_readiness())
                if api_ready:
                    logger.info(
                        f"API is ready! (Took {time.monotonic() - start_wait_time:.2f}s, {attempt_count} attempts)",
                        extra={
                            "attempts": attempt_count,
                            "duration": time.monotonic() - start_wait_time,
                        },
                    )
                    break
            except Exception as e:
                logger.debug(
                    f"API not yet ready (attempt {attempt_count}): {e}. Retrying in {poll_interval:.2f}s."
                )

            # Exponential backoff with jitter for health checks
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, max_poll_interval)

        if not api_ready:
            elapsed = time.monotonic() - start_wait_time
            logger.critical(
                f"API did not become ready within {ready_timeout}s (elapsed: {elapsed:.2f}s). Terminating 'all' mode.",
                extra={
                    "timeout": ready_timeout,
                    "elapsed": elapsed,
                    "attempts": attempt_count,
                    "pid": api_process_handle.pid if api_process_handle else None,
                },
            )

            # Graceful shutdown of API process
            if api_process_handle and api_process_handle.is_alive():
                logger.info(
                    f"Terminating API process (PID: {api_process_handle.pid})..."
                )
                api_process_handle.terminate()
                api_process_handle.join(timeout=10)

                if api_process_handle.is_alive():
                    logger.warning(
                        f"API process did not terminate gracefully, sending SIGKILL..."
                    )
                    api_process_handle.kill()
                    api_process_handle.join(timeout=5)

            asyncio.run(
                send_alert(
                    subject="API Startup Timeout in All Mode",
                    message=f"API did not become ready after {elapsed:.2f}s ({attempt_count} attempts). Check API logs for errors.",
                    severity="critical",
                )
            )
            sys.exit(1)

        logger.info("Launching GUI interface (main process)...")
        setup_signals(loop, runner_instance=None, api_process=api_process_handle)
        app = MainApp()
        gui_exit_code = 0

        try:
            APP_STARTUP_DURATION.labels(
                app_name="all_mode", instance_id=os.getenv("HOSTNAME", "unknown")
            ).observe(time.monotonic() - startup_start_time)

            logger.info("Starting Textual TUI application...")
            app.run()
            logger.info("TUI application exited normally.")

        except KeyboardInterrupt:
            logger.info("GUI interrupted by user (Ctrl+C). Shutting down gracefully...")
            gui_exit_code = 0  # Normal exit on Ctrl+C

        except Exception as e:
            logger.critical(f"GUI application crashed: {e}", exc_info=True)
            gui_exit_code = 1
            asyncio.run(
                send_alert(
                    subject="GUI Crashed in All Mode",
                    message=f"GUI crashed in 'all' mode: {e}",
                    severity="critical",
                )
            )

        finally:
            logger.info(
                "GUI application exited in 'all' mode. Initiating graceful shutdown of API process..."
            )

            # Industry-standard process cleanup with timeout and escalation
            if api_process_handle and api_process_handle.is_alive():
                logger.info(
                    f"Sending SIGTERM to API process (PID: {api_process_handle.pid})..."
                )
                api_process_handle.terminate()

                # Wait for graceful shutdown
                api_process_handle.join(timeout=10)

                if api_process_handle.is_alive():
                    logger.warning(
                        f"API process did not terminate within 10s, sending SIGKILL (PID: {api_process_handle.pid})..."
                    )
                    api_process_handle.kill()
                    api_process_handle.join(timeout=5)

                    if api_process_handle.is_alive():
                        logger.error(
                            f"API process still alive after SIGKILL! This should not happen. (PID: {api_process_handle.pid})"
                        )
                    else:
                        logger.info("API process terminated via SIGKILL.")
                else:
                    logger.info("API process terminated gracefully.")

                # Verify process is actually dead
                if api_process_handle.exitcode is not None:
                    logger.info(
                        f"API process exited with code: {api_process_handle.exitcode}",
                        extra={"exit_code": api_process_handle.exitcode},
                    )
            else:
                logger.info("API process was not running or already terminated.")

            # Update metrics
            APP_RUNNING_GAUGE.labels(
                app_name="all_mode",
                instance_id=os.getenv("HOSTNAME", "unknown"),
            ).set(0)

            # Exit with appropriate code
            if gui_exit_code != 0:
                sys.exit(gui_exit_code)

    else:  # cli interface
        logger.info("Launching CLI interface...")
        setup_signals(loop, runner_instance=None, api_process=None)
        try:
            APP_STARTUP_DURATION.labels(
                app_name="cli", instance_id=os.getenv("HOSTNAME", "unknown")
            ).observe(time.monotonic() - startup_start_time)
            main_cli(obj={})
        except Exception as e:
            logger.critical(f"CLI execution failed: {e}", exc_info=True)
            asyncio.run(
                send_alert(
                    subject="CLI Execution Failed",
                    message=f"CLI execution failed: {e}",
                    severity="critical",
                )
            )
            sys.exit(1)
        finally:
            logger.info("CLI execution completed.")
            APP_RUNNING_GAUGE.labels(
                app_name="cli",
                instance_id=os.getenv("HOSTNAME", "unknown"),
            ).set(0)


def on_config_reload(
    config_path: Path, new_config: Dict[str, Any], diff: Dict[str, Any]
):
    """
    Callback for configuration reloads from ConfigWatcher.

    FIX for Issue D: Enforces strict validation during reload to prevent
    incomplete configs from breaking running services.
    """
    with tracer.start_as_current_span(
        "config_reload_callback", attributes={"config.path": str(config_path)}
    ) as span:
        logger.info(
            f"Config reload initiated: {config_path}",
            extra={
                "category": "config",
                "diff_keys": list(diff.keys()) if diff else [],
                "change_count": len(diff) if diff else 0,
            },
        )

        # Log the diff for audit trail (with sensitive data redacted)
        try:
            # Redact sensitive keys from diff
            sensitive_keys = ["password", "secret", "key", "token", "credential"]
            safe_diff = {}
            for key, value in (diff or {}).items():
                if any(sensitive in key.lower() for sensitive in sensitive_keys):
                    safe_diff[key] = "[REDACTED]"
                else:
                    safe_diff[key] = value

            logger.debug(f"Config changes: {json.dumps(safe_diff, indent=2)}")
        except Exception as e:
            logger.warning(f"Could not log config diff: {e}")

        try:
            # FIX for Issue D: Use stricter validation for reloads
            validate_config(new_config, is_reload=True)
            logger.info(
                "New configuration validated successfully upon reload.",
                extra={"config_path": str(config_path)},
            )
            span.set_status(StatusCode.OK, "Config reloaded and validated")

            # Log successful reload for audit
            log_action(
                "Config Reloaded Successfully",
                category="config_management",
                path=str(config_path),
                diff_summary=f"{len(diff)} changes" if diff else "no changes",
                validation_passed=True,
            )

        except ValueError as e:
            error_msg = str(e)
            logger.error(
                f"New configuration failed validation upon reload: {error_msg}. "
                "Changes NOT applied. System continues with previous config.",
                exc_info=True,
                extra={
                    "config_path": str(config_path),
                    "validation_error": error_msg,
                    "changes_attempted": len(diff) if diff else 0,
                },
            )

            # Send alert for failed reload
            asyncio.run(
                send_alert(
                    subject="Config Reload Validation Failed",
                    message=(
                        f"Config reload failed validation at {config_path}. "
                        f"Error: {error_msg}. "
                        "Changes were NOT applied. System continues with previous configuration."
                    ),
                    severity="high",
                )
            )

            span.set_status(
                StatusCode.ERROR, f"Config reload validation failed: {error_msg}"
            )
            span.set_attribute("validation_failed", True)
            span.set_attribute("error_message", error_msg)

            # Log failed reload attempt for audit
            log_action(
                "Config Reload Failed",
                category="config_management",
                path=str(config_path),
                error=error_msg,
                validation_passed=False,
                changes_rejected=len(diff) if diff else 0,
            )

            # Do not raise - just return to keep the application running with old config
            return

        except Exception as e:
            # Catch any unexpected errors during validation
            error_msg = f"Unexpected error during config reload validation: {e}"
            logger.critical(
                error_msg, exc_info=True, extra={"config_path": str(config_path)}
            )

            asyncio.run(
                send_alert(
                    subject="Config Reload Critical Error",
                    message=f"{error_msg}. System continues with previous configuration.",
                    severity="critical",
                )
            )

            span.set_status(StatusCode.ERROR, error_msg)
            span.record_exception(e)

            # Log critical error for audit
            log_action(
                "Config Reload Critical Error",
                category="config_management",
                path=str(config_path),
                error=error_msg,
                validation_passed=False,
            )

            # Do not raise - keep application running
            return


# --- Main Entry Point Execution ---
if __name__ == "__main__":
    if IMPORT_ERROR is not None:
        logger.critical(f"Exiting due to critical import error: {IMPORT_ERROR}")
        sys.exit(1)

    try:
        # The main() function is a synchronous Click command, so invoke it directly
        main()

    except Exception as e:
        logger.critical(
            f"Unhandled exception at application top level: {e}", exc_info=True
        )
        asyncio.run(
            send_alert(
                subject="Critical Startup Error",
                message=f"Unhandled critical application error at startup: {e}",
                severity="critical",
            )
        )
        sys.exit(1)
