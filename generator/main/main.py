# main.py
# Main entry point for the AI README-to-App Generator.
# Orchestrates service startup (CLI, GUI, API), configuration management,
# logging, metrics, tracing, and graceful shutdown.
# REFACTORED: Now uses central runner for all logging, metrics, config, and core components.
# REFACTORED: Imports from main/__init__.py to prevent circular dependencies.
# Created: July 30, 2025.

from __future__ import annotations  # Enable forward references for type hints

import sys
import os
# Add the project's root directory to the Python path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import asyncio
import click
import sys
import os
import json
import signal
import logging
import hashlib
import datetime
import uuid # For provenance launch_id
import multiprocessing # For launching API in separate process for 'all' interface
import time # For polling readiness
from pathlib import Path
from functools import partial
from typing import Dict, Any, Optional, Callable

# Logging handlers for file rotation
from logging.handlers import RotatingFileHandler

# --- FIX: Guard optional/heavy imports ---
try:
    import uvicorn
except ImportError:
    uvicorn = None
    logging.getLogger(__name__).warning("uvicorn not found. API interface will be unavailable.")

try:
    import aiohttp # For API health checks
except ImportError:
    aiohttp = None
    logging.getLogger(__name__).warning("aiohttp not found. 'all' interface health checks will fail.")

try:
    from textual.app import App as TextualApp # Alias to avoid name clash with main_app
except ImportError:
    TextualApp = object # Dummy for tests
    logging.getLogger(__name__).warning("textual not found. GUI interface will be unavailable.")


# Observability imports
# FIX: Create a dummy MagicMock for fallbacks
class _DummyMagicMock:
    def __call__(self, *args, **kwargs):
        return self
    def __getattr__(self, name):
        return self
    def __setattr__(self, name, value):
        pass
    def instrument_app(self, *args, **kwargs):
        pass
    def instrument(self, *args, **kwargs):
        pass
    def set_status(self, *args, **kwargs):
        pass
    def record_exception(self, *args, **kwargs):
        pass
    def set_attribute(self, *args, **kwargs):
        pass
    def labels(self, *args, **kwargs):
        return self
    def set(self, *args, **kwargs):
        pass
    def observe(self, *args, **kwargs):
        pass
MagicMock = _DummyMagicMock()


try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter 
    from opentelemetry.semconv.trace import SpanAttributes
    from opentelemetry.trace import StatusCode
    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False
    # Create dummy objects so the rest of the file doesn't crash
    logging.getLogger(__name__).warning("OpenTelemetry packages not found. Tracing will be disabled.")
    trace = MagicMock()
    TracerProvider = object
    BatchSpanProcessor = object
    ConsoleSpanExporter = object
    Resource = MagicMock()
    FastAPIInstrumentor = MagicMock()
    LoggingInstrumentor = MagicMock
    OTLPSpanExporter = object
    SpanAttributes = MagicMock()
    StatusCode = MagicMock()
    StatusCode.OK = "OK"
    StatusCode.ERROR = "ERROR"

# --- FIX: Define module-level tracer ---
# This was the source of the NameError: 'tracer' is not defined
tracer = trace.get_tracer(__name__)

try:
    from jsonschema import validate as json_validate, ValidationError, Draft7Validator
except ImportError:
    jsonschema = None
    Draft7Validator = object
    ValidationError = Exception
    logging.getLogger(__name__).warning("jsonschema not found. Config validation will be skipped.")
    def json_validate(instance, schema, cls=None): # Added cls=None for compatibility
        pass # No-op
# --- END Guarded Imports ---


# --- Runner Foundation & Project Imports ---
try:
    # Import central runner components directly
    from runner.runner_config import RunnerConfig, load_config, ConfigWatcher
    from runner.runner_core import Runner
    from runner.runner_logging import logger as runner_logger_instance, log_action
    from runner.runner_metrics import get_metrics_dict, bootstrap_metrics # FIX: Import bootstrap_metrics
    from runner.alerting import send_alert
    
    # --- FIX: Import from the package __init__ to avoid circular dependency ---
    # from . import main_cli, MainApp, fastapi_app, api_create_db_tables
    
    # --- START FIX 1: Break circular dependency ---
    # Import directly from sibling modules instead of __init__.py
    from .cli import cli as main_cli
    from .gui import MainApp
    from .api import api as fastapi_app, create_db_tables as api_create_db_tables
    # --- END FIX 1 ---

    # --- START FIX 1: Add IntentParser for test patching ---
    try:
        from intent_parser.intent_parser import IntentParser
    except ImportError as e:
        IntentParser = MagicMock() # Use the dummy mock
        logging.critical(f"Failed to import IntentParser (will use dummy): {e}")
    # --- END FIX 1 ---

except ImportError as e:
    logging.critical(
        f"Failed to import core project modules: {e}. Ensure PYTHONPATH is correct and all dependencies are installed.",
        exc_info=True
    )
    IMPORT_ERROR = e
else:
    IMPORT_ERROR = None

# ********** FIX 1: Explicitly expose MainApp globally **********
# Required to allow tests to patch main.main.MainApp
try:
    MainApp = MainApp
except NameError:
    MainApp = TextualApp # Use the aliased TextualApp as the base fallback
# ****************************************

# ********** FIX 2: Explicitly expose send_alert globally **********
# Required to allow tests to patch main.main.send_alert
try:
    send_alert = send_alert
except NameError:
    async def send_alert(*args, **kwargs):
        logging.warning("send_alert (dummy) called.")
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
        logging.warning("load_config (dummy) called.")
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
    def api_create_db_tables(): pass
try:
    get_metrics_dict = get_metrics_dict
except NameError:
    def get_metrics_dict(): return {}
try:
    log_action = log_action
except NameError:
    def log_action(*args, **kwargs): pass
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
logger = runner_logger_instance if 'runner_logger_instance' in globals() else logging.getLogger(__name__)


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
            file_handler = RotatingFileHandler(LOG_FILE_PATH, maxBytes=int(os.getenv("APP_LOG_MAX_BYTES", 10*1024*1024)), backupCount=int(os.getenv("APP_LOG_BACKUP_COUNT", 5)))
            file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
            logger.addHandler(file_handler)
            logger.info(f"File logging enabled: {LOG_FILE_PATH}")
        except Exception as e:
            logger.error(f"Failed to set up file logging: {e}. Continuing with console logging.", exc_info=True)

    # --- OpenTelemetry Tracing Configuration ---
    if _HAS_OTEL:
        resource = Resource.create({"service.name": "ai-generator", "service.version": __version__})
        tracer_provider = TracerProvider(resource=resource)
        OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

        if OTEL_EXPORTER_OTLP_ENDPOINT:
            try:
                tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True)))
                logger.info(f"OpenTelemetry traces configured to export to OTLP endpoint: {OTEL_EXPORTER_OTLP_ENDPOINT}")
            except Exception as e:
                logger.error(f"Failed to configure OTLP exporter: {e}", exc_info=True)
        else:
            tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            logger.info("OpenTelemetry traces configured for Console export (no OTEL_EXPORTER_OTLP_ENDPOINT specified).")

        trace.set_tracer_provider(tracer_provider)
        LoggingInstrumentor().instrument(set_logging_format=True)
    
    # --- Prometheus Metrics Setup (from Runner) ---
    try:
        if 'bootstrap_metrics' in globals() and callable(bootstrap_metrics):
            bootstrap_metrics()
            logger.info("Runner metrics registry bootstrapped.")
        else:
            logger.info("bootstrap_metrics not found in runner, assuming registry is pre-initialized.")
    except Exception as e:
        logger.error(f"Failed to bootstrap metrics: {e}", exc_info=True)

# --- FIX: Define metric objects at module level, but safely ---
# These need to exist for patching, even if bootstrap fails.
try:
    if 'bootstrap_metrics' in globals() and callable(bootstrap_metrics):
        bootstrap_metrics() # Ensure they are created
    metrics_dict = get_metrics_dict()
    APP_RUNNING_GAUGE = metrics_dict['app_running_status']
    APP_STARTUP_DURATION = metrics_dict['app_startup_duration_seconds']
    logger.info("Loaded metrics from central runner registry.")
except Exception as e:
    logger.critical(f"Failed to load required metrics from runner.runner_metrics: {e}. Using dummy metrics for patching.", exc_info=True)
    # Define dummies for patching
    class DummyGauge:
        def labels(self, *args, **kwargs): return self
        def set(self, *args, **kwargs): pass
    class DummyHistogram:
        def labels(self, *args, **kwargs): return self
        def observe(self, *args, **kwargs): pass
    APP_RUNNING_GAUGE = DummyGauge()
    APP_STARTUP_DURATION = DummyHistogram()

# --- Global Config Watcher ---
config_watcher = None

# --- Graceful Shutdown Handler ---
async def shutdown(signal_name: str, loop: asyncio.AbstractEventLoop, runner_instance: Optional[Runner] = None, api_process: Optional[multiprocessing.Process] = None):
    """Handles graceful shutdown of the application."""
    with tracer.start_as_current_span("app_shutdown", attributes={"signal.received": signal_name, "app.interface": os.getenv("APP_INTERFACE", "unknown")}) as span:
        logger.info(f"Received exit signal {signal_name}... Initiating graceful shutdown.")

        APP_RUNNING_GAUGE.labels(component=os.getenv("APP_INTERFACE", "unknown"), version=__version__, interface=os.getenv("APP_INTERFACE", "unknown"), hostname=os.getenv('HOSTNAME', 'unknown')).set(0)

        logger.info("Triggering pre-shutdown events (flushing logs, committing data)...")

        try:
            if _HAS_OTEL and 'tracer_provider' in locals() and hasattr(tracer_provider, 'force_flush'):
                tracer_provider.force_flush()
                logger.info("OpenTelemetry traces flushed.")
        except Exception as e:
            logger.error(f"Failed to flush OpenTelemetry traces: {e}", exc_info=True)

        # FIX: Only cancel 'owned' background tasks (like config_watcher)
        # to avoid killing uvicorn/textual tasks prematurely.
        tasks = [t for t in asyncio.all_tasks(loop=loop) 
                 if t is not asyncio.current_task() 
                 and not t.done()
                 and getattr(t, "_owned_by_main", False)] # Check for our custom flag
        
        if tasks:
            logger.info(f"Cancelling {len(tasks)} outstanding 'owned' background tasks...")
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
        span.set_status(StatusCode.OK, f"Application gracefully shut down by signal {signal_name}")
        logger.info("Application shutdown complete.")

def setup_signals(loop: asyncio.AbstractEventLoop, runner_instance: Optional[Runner] = None, api_process: Optional[multiprocessing.Process] = None):
    """Sets up signal handlers for graceful shutdown, with Windows compatibility."""
    signals_to_handle = [signal.SIGINT, signal.SIGTERM]
    if sys.platform != "win32":
        signals_to_handle.extend([signal.SIGHUP, signal.SIGQUIT])
    
    _shutdown_handler = partial(shutdown, loop=loop, runner_instance=runner_instance, api_process=api_process)
    
    for sig in signals_to_handle:
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(_shutdown_handler(s.name)))
            logger.debug(f"Added signal handler for {sig.name}")
        except (NotImplementedError, RuntimeError) as e:
            logger.warning(f"Could not add signal handler for {sig.name} (loop might be closing or OS unsupported): {e}")

# --- Provenance Generator ---
def generate_launch_provenance(interface: str, config: Dict[str, Any], config_path: Path, user: str = os.getenv('USER', 'unknown')) -> Dict[str, Any]:
    """Generates a unique provenance record for each application launch."""
    with tracer.start_as_current_span("generate_launch_provenance", attributes={"app.interface": interface}) as span:
        timestamp = datetime.datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'
        
        try:
            config_model = load_config(config_path)
            config_hash = hashlib.sha256(config_model.model_dump_json(sort_keys=True).encode('utf-8')).hexdigest()
        except Exception as e:
            logger.warning(f"Failed to hash config model, falling back to dict hash: {e}")
            config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode('utf-8')).hexdigest()
        
        env_details = {
            "os_name": os.name, "platform": sys.platform,
            "python_version": sys.version.split()[0],
            "hostname": os.getenv('HOSTNAME', 'unknown'),
            "user_id": user, "cwd": os.getcwd()
        }
        
        provenance = {
            "launch_id": str(uuid.uuid4()), "timestamp": timestamp,
            "interface": interface, "config_hash": config_hash,
            "app_version": __version__, "environment": env_details
        }
        
        # FIX: Add robust check for log_action before calling
        try:
            if 'log_action' in globals() and callable(log_action):
                log_action("Launch Provenance", category="startup", **provenance)
            else:
                logger.info(f"Launch Provenance (log_action not available): {provenance}")
        except Exception as e:
            logger.warning(f"Failed to log provenance: {e}", exc_info=True)
            
        span.set_status(StatusCode.OK)
        return provenance

# --- Config Validation ---
def validate_config(config: Dict[str, Any]):
    """Performs strict validation of the loaded configuration, including environment checks."""
    with tracer.start_as_current_span("validate_config") as span:
        logger.info("Validating configuration...")
        
        config_schema = {
            "type": "object",
            "properties": {
                "backend": {"type": "string", "enum": ["local", "kubernetes", "distributed"]},
                "framework": {"type": "string"},
                "logging": {"type": "object", "properties": {"level": {"type": "string"}}},
                "metrics": {"type": "object", "properties": {"port": {"type": "integer"}}},
                "security": {"type": "object", "properties": {"jwt_secret_key_env_var": {"type": "string"}}},
                "external_services": {
                    "type": "object",
                    "properties": {
                        "llm_api_url": {"type": "string", "format": "uri"},
                        "database_connection_string": {"type": "string"}
                    }
                }
            },
            "required": ["backend", "framework", "logging", "metrics", "security"]
        }

        try:
            json_validate(instance=config, schema=config_schema, cls=Draft7Validator)
        except ImportError:
            logger.warning("jsonschema library not found for config validation. Skipping schema validation.")
        except ValidationError as e:
            span.set_status(StatusCode.ERROR, f"Config schema validation failed: {e.message}")
            logger.critical(f"Configuration validation failed: {e.message}", exc_info=True)
            raise ValueError(f"Config schema validation failed: {e.message}") from e

        if config.get('security', {}).get('jwt_secret_key_env_var'):
            jwt_env_var = config['security']['jwt_secret_key_env_var']
            jwt_secret = os.getenv(jwt_env_var)
            if not jwt_secret:
                span.set_status(StatusCode.ERROR, f"JWT secret key env var '{jwt_env_var}' not set.")
                logger.critical(f"Configuration validation failed: JWT secret key environment variable '{jwt_env_var}' not set. This is critical for API security.")
                raise ValueError(f"JWT secret key environment variable '{jwt_env_var}' is not set.")
            
            known_insecure_defaults = ["your-super-secret-key-that-should-be-in-env", "changeme", "supersecretkey"]
            if jwt_secret in known_insecure_defaults or len(jwt_secret) < 32:
                 logger.warning(f"Insecure JWT_SECRET_KEY detected. Length: {len(jwt_secret)}. Change it immediately for production!")
        
        logger.info("Config validated successfully.")
        span.set_status(StatusCode.OK)

# --- Health Check Logic ---
async def perform_health_check(config: Dict[str, Any], check_api: bool = False, api_url: Optional[str] = None, is_canary: bool = False) -> bool:
    """Performs a comprehensive health check of the application components."""
    overall_health = True
    api_health_url = api_url if api_url else os.getenv("GENERATOR_API_BASE_URL", "http://127.0.0.1:8000/api/v1") + "/health"
    timeout = 2 if is_canary else 5
    
    with tracer.start_as_current_span("perform_health_check", attributes={"check.api": check_api, "api.url": api_health_url, "health_check.is_canary": is_canary}) as span:
        logger.info("Starting application health check...")
        
        # 1. Check Runner's self-test
        try:
            runner_config = RunnerConfig(**config)
            runner = Runner(runner_config)
            
            runner_health = await asyncio.to_thread(runner.self_test) 
            if runner_health:
                logger.info("Runner self-test: PASSED")
                APP_RUNNING_GAUGE.labels(component='runner', version=__version__, interface='health_check', hostname=os.getenv('HOSTNAME', 'unknown')).set(1)
            else:
                logger.error("Runner self-test: FAILED. Check Runner logs for details.")
                overall_health = False
                APP_RUNNING_GAUGE.labels(component='runner', version=__version__, interface='health_check', hostname=os.getenv('HOSTNAME', 'unknown')).set(0)
                await send_alert("Runner self-test failed during health check.", severity="critical")
        except Exception as e:
            logger.error(f"Runner self-test encountered an exception: {e}", exc_info=True)
            overall_health = False
            APP_RUNNING_GAUGE.labels(component='runner', version=__version__, interface='health_check', hostname=os.getenv('HOSTNAME', 'unknown')).set(0)
            await send_alert(f"Runner self-test exception during health check: {e}", severity="critical")
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
                            logger.info(f"API health check ({api_health_url}): PASSED. Details: {api_status}")
                            APP_RUNNING_GAUGE.labels(component='api', version=__version__, interface='health_check', hostname=os.getenv('HOSTNAME', 'unknown')).set(1)
                        else:
                            logger.error(f"API health check ({api_health_url}): FAILED. Details: {api_status}")
                            overall_health = False
                            APP_RUNNING_GAUGE.labels(component='api', version=__version__, interface='health_check', hostname=os.getenv('HOSTNAME', 'unknown')).set(0)
                            await send_alert(f"API health check failed: {api_status}", severity="critical")
                            span.set_status(StatusCode.ERROR, "API health endpoint reported unhealthy")
                    span.set_attribute("api.health.status", api_status.get('status'))
                except aiohttp.ClientError as e:
                    logger.error(f"API health check ({api_health_url}): Connection failed: {e}", exc_info=True)
                    overall_health = False
                    APP_RUNNING_GAUGE.labels(component='api', version=__version__, interface='health_check', hostname=os.getenv('HOSTNAME', 'unknown')).set(0)
                    await send_alert(f"API health check connection failed: {e}", severity="critical")
                    span.record_exception(e)
                    span.set_status(StatusCode.ERROR, "API health check connection failed")
                except Exception as e:
                    logger.error(f"API health check ({api_health_url}): Unexpected error: {e}", exc_info=True)
                    overall_health = False
                    APP_RUNNING_GAUGE.labels(component='api', version=__version__, interface='health_check', hostname=os.getenv('HOSTNAME', 'unknown')).set(0)
                    await send_alert(f"API health check unexpected error: {e}", severity="critical")
                    span.record_exception(e)
                    span.set_status(StatusCode.ERROR, "API health check failed with unexpected exception")
        
        logger.info(f"Overall Health Check: {'PASSED' if overall_health else 'FAILED'}")
        if overall_health:
            span.set_status(StatusCode.OK, "All health checks passed")
        else:
            span.set_status(StatusCode.ERROR, "Some health checks failed")
        return overall_health

# --- Click Commands for Main Entry Point ---
@click.group(invoke_without_command=True)
@click.option('--interface', type=click.Choice(['cli', 'gui', 'api', 'all']), default='cli', help='The interface to launch.')
@click.option('--config-path', default='config.yaml', type=click.Path(exists=True, readable=True, path_type=Path), help='Path to configuration file.')
@click.option('--version', is_flag=True, help='Show version information and exit.')
@click.option('--health-check', is_flag=True, help='Perform health check and exit.')
@click.option('--log-level', type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']), default='INFO', help='Set the logging level.')
@click.option('--canary', is_flag=True, help='Run in canary mode (e.g., reduced health check timeouts).')
def main(interface: str, config_path: Path, version: bool, health_check: bool, log_level: str, canary: bool):
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
        asyncio.run(send_alert(f"Config validation failed at startup: {e}", severity="critical"))
        sys.exit(1)

    provenance = generate_launch_provenance(interface, config_dict, config_path)

    try:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()
        console.print(Panel(
            f"AI README-to-App Generator v[bold green]{__version__}[/bold green]\n"
            f"Launch Interface: [bold cyan]{interface}[/bold cyan]\n"
            f"Config Path: {config_path} (Hash: [dim]{provenance['config_hash'][:8]}[/dim])\n"
            f"Provenance ID: [dim]{provenance['launch_id'][:8]}[/dim]\n"
            f"Environment: Python {sys.version.split()[0]} on {os.name} ({sys.platform})",
            title="[bold blue]Welcome![/bold blue]",
            title_align="left",
            border_style="bold green"
        ))
    except ImportError:
        logger.info("`rich` not installed. Skipping rich welcome panel.")
        print(f"AI README-to-App Generator v{__version__} | Interface: {interface}")


    logger.info("Metrics server assumed to be started by central runner.")
    APP_RUNNING_GAUGE.labels(component='prometheus_server_check', version=__version__, interface=interface, hostname=os.getenv('HOSTNAME', 'unknown')).set(1)

    loop = asyncio.get_event_loop()
    
    if health_check:
        logger.info("Performing requested health check...")
        health_status = loop.run_until_complete(perform_health_check(config_dict, check_api=(interface == 'api' or interface == 'all'), is_canary=canary))
        if not health_status:
            logger.critical("Health check failed. Exiting.")
            sys.exit(1)
        else:
            logger.info("Health check passed. Exiting.")
            sys.exit(0)

    APP_RUNNING_GAUGE.labels(component='main_process', version=__version__, interface=interface, hostname=os.getenv('HOSTNAME', 'unknown')).set(1)

    config_watcher = ConfigWatcher(config_path, partial(on_config_reload, config_path))
    # FIX: Flag the config_watcher task as an 'owned' background task for graceful shutdown
    config_watcher_task = asyncio.create_task(config_watcher.start())
    config_watcher_task._owned_by_main = True


    # --- Launch Interface ---
    if interface == 'gui':
        logger.info("Launching GUI interface...")
        setup_signals(loop, runner_instance=None, api_process=None)
        app = MainApp()
        try:
            APP_STARTUP_DURATION.labels(interface=interface, version=__version__).observe(time.monotonic() - startup_start_time)
            app.run()
        except Exception as e:
            logger.critical(f"GUI application crashed: {e}", exc_info=True)
            asyncio.run(send_alert(f"GUI crashed: {e}", severity="critical"))
            sys.exit(1)
        finally:
            logger.info("GUI application exited.")
            APP_RUNNING_GAUGE.labels(component='gui', version=__version__, interface=interface, hostname=os.getenv('HOSTNAME', 'unknown')).set(0)

    elif interface == 'api':
        if not uvicorn:
            logger.critical("uvicorn not found. Cannot start API interface.")
            sys.exit(1)
        logger.info("Launching API interface...")
        api_create_db_tables()
        if _HAS_OTEL:
            FastAPIInstrumentor.instrument_app(fastapi_app)
        
        uvicorn_config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=8000, log_level=log_level.lower(), reload=False)
        server = uvicorn.Server(uvicorn_config)
        
        setup_signals(loop, runner_instance=None, api_process=None)
        try:
            APP_STARTUP_DURATION.labels(interface=interface, version=__version__).observe(time.monotonic() - startup_start_time)
            loop.run_until_complete(server.serve())
        except Exception as e:
            logger.critical(f"API server crashed: {e}", exc_info=True)
            asyncio.run(send_alert(f"API server crashed: {e}", severity="critical"))
            sys.exit(1)
        finally:
            logger.info("API server exited.")
            APP_RUNNING_GAUGE.labels(component='api', version=__version__, interface=interface, hostname=os.getenv('HOSTNAME', 'unknown')).set(0)

    elif interface == 'all':
        if not uvicorn or not aiohttp:
            logger.critical("uvicorn or aiohttp not found. Cannot start 'all' interface.")
            sys.exit(1)
            
        logger.info("Launching ALL interfaces (API + GUI)...")
        api_target_port = int(os.getenv("API_TARGET_PORT", 8000))
        
        api_process_target = partial(uvicorn.run, fastapi_app, host="0.0.0.0", port=api_target_port, log_level=log_level.lower(), reload=False)
        api_process_handle = multiprocessing.Process(target=api_process_target)
        api_process_handle.start()
        logger.info(f"API process started with PID: {api_process_handle.pid} on port {api_target_port}.")
        
        api_ready_url = f"http://127.0.0.1:{api_target_port}/api/v1/health"
        ready_timeout = int(os.getenv("API_READINESS_TIMEOUT_SECONDS", 120))
        poll_interval = float(os.getenv("API_READINESS_POLL_INTERVAL_SECONDS", 1.0))
        api_ready = False
        start_wait_time = time.monotonic()

        logger.info(f"Polling API readiness at {api_ready_url} for up to {ready_timeout} seconds...")
        while not api_ready and (time.monotonic() - start_wait_time < ready_timeout):
            try:
                async def check_api_readiness():
                    async with aiohttp.ClientSession() as session:
                        async with session.get(api_ready_url, timeout=poll_interval) as response:
                            response.raise_for_status()
                            status_json = await response.json()
                            return status_json.get("status") == "healthy"
                api_ready = loop.run_until_complete(check_api_readiness())
                if api_ready:
                    logger.info("API is ready!")
                    break
            except Exception as e:
                logger.debug(f"API not yet ready: {e}. Retrying in {poll_interval}s.")
            time.sleep(poll_interval)

        if not api_ready:
            logger.critical(f"API did not become ready within {ready_timeout} seconds. Terminating 'all' mode.")
            api_process_handle.terminate()
            api_process_handle.join(timeout=5)
            asyncio.run(send_alert("API did not become ready for 'all' mode startup. Check API logs.", severity="critical"))
            sys.exit(1)

        logger.info("Launching GUI interface (main process)...")
        setup_signals(loop, runner_instance=None, api_process=api_process_handle)
        app = MainApp()
        try:
            APP_STARTUP_DURATION.labels(interface=interface, version=__version__).observe(time.monotonic() - startup_start_time)
            app.run()
        except Exception as e:
            logger.critical(f"GUI application crashed: {e}", exc_info=True)
            asyncio.run(send_alert(f"GUI crashed in 'all' mode: {e}", severity="critical"))
            sys.exit(1)
        finally:
            logger.info("GUI application exited in 'all' mode. Terminating API process...")
            if api_process_handle and api_process_handle.is_alive():
                api_process_handle.terminate()
                api_process_handle.join(timeout=5)
            APP_RUNNING_GAUGE.labels(component='all_mode', version=__version__, interface=interface, hostname=os.getenv('HOSTNAME', 'unknown')).set(0)

    else:  # cli interface
        logger.info("Launching CLI interface...")
        setup_signals(loop, runner_instance=None, api_process=None)
        try:
            APP_STARTUP_DURATION.labels(interface=interface, version=__version__).observe(time.monotonic() - startup_start_time)
            main_cli(obj={})
        except Exception as e:
            logger.critical(f"CLI execution failed: {e}", exc_info=True)
            asyncio.run(send_alert(f"CLI execution failed: {e}", severity="critical"))
            sys.exit(1)
        finally:
            logger.info("CLI execution completed.")
            APP_RUNNING_GAUGE.labels(component='cli', version=__version__, interface=interface, hostname=os.getenv('HOSTNAME', 'unknown')).set(0)


def on_config_reload(config_path: Path, new_config: Dict[str, Any], diff: Dict[str, Any]):
    """Callback for configuration reloads from ConfigWatcher."""
    with tracer.start_as_current_span("config_reload_callback", attributes={"config.path": str(config_path)}) as span:
        logger.info(f"Config reloaded: {config_path}. Diff: {json.dumps(diff)}", extra={"category": "config"})
        
        try:
            validate_config(new_config)
            logger.info("New configuration validated successfully upon reload.")
            span.set_status(StatusCode.OK, "Config reloaded and validated")
        except ValueError as e:
            logger.error(f"New configuration failed validation upon reload: {e}. Changes not applied.", exc_info=True)
            asyncio.run(send_alert(f"Config reload failed validation: {e}. Changes NOT applied.", severity="high"))
            span.set_status(StatusCode.ERROR, f"Config reload validation failed: {e}")
            return

        log_action("Config Reloaded", category="config_management", path=str(config_path), diff=diff)


# --- Main Entry Point Execution ---
if __name__ == "__main__":
    if IMPORT_ERROR is not None:
        logger.critical(f"Exiting due to critical import error: {IMPORT_ERROR}")
        sys.exit(1)
    
    try:
        ctx = main.make_context(main.name, sys.argv[1:])
        with ctx:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(main.callback(**ctx.params))

    except Exception as e:
        logger.critical(f"Unhandled exception at application top level: {e}", exc_info=True)
        asyncio.run(send_alert(f"Unhandled critical application error at startup: {e}", severity="critical"))
        sys.exit(1)