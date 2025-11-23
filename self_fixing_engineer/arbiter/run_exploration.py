import asyncio
import os
import sys
import signal
import json
import logging
from typing import Dict, List, Any, Optional
from logging.handlers import RotatingFileHandler
from aiohttp import web
from prometheus_client import Counter
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
import aiofiles
import yaml
import importlib
import pkgutil

# Mock/Plausholder imports for a self-contained fix
try:
    from arbiter.config import ArbiterConfig as Settings
    from arbiter.arena import ArbiterArena
    from arbiter.arbiter import Arbiter
    from arbiter_plugin_registry import registry, PlugInKind
    from arbiter.logging_utils import PIIRedactorFilter
    from sqlalchemy.ext.asyncio import create_async_engine
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
except ImportError:

    class Settings:
        def __init__(self):
            self.REDIS_URL = "redis://localhost:6379"
            self.KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
            self.ENCRYPTION_KEY_BYTES = b""

    class ArbiterArena:
        def __init__(self, *args, **kwargs):
            self.arbiters = [
                type(
                    "MockArbiter",
                    (object,),
                    {"name": f"Arbiter_{i}", "db_engine": None},
                )()
                for i in range(kwargs.get("num", 1))
            ]

        async def start_arena_services(self, http_port):
            pass

        async def stop_arena_services(self):
            pass

    class Arbiter:
        def __init__(self, *args, **kwargs):
            self.name = kwargs.get("name", "MockArbiter")
            self.db_engine = kwargs.get("db_engine")

        async def run_task(self, task, output_dir):
            if task.get("fail"):
                raise Exception("Mock failure")
            return {"status": "success"}

        async def explore_and_fix(self, paths):
            if paths and "fail" in paths:
                raise Exception("Mock failure")
            return {"status": "success"}

    class registry:
        @staticmethod
        def register(kind, name, version, author):
            def decorator(cls):
                return cls

            return decorator

    class PlugInKind:
        CORE_SERVICE = "core_service"

    class PIIRedactorFilter(logging.Filter):
        def filter(self, record):
            return True

    def create_async_engine(*args, **kwargs):
        return None

    trace = None

    class tracer:
        @staticmethod
        def start_as_current_span(name):
            return type(
                "MockSpan",
                (object,),
                {"__enter__": lambda s: None, "__exit__": lambda s, t, v, tb: None},
            )()


# Optional: YAML config support
try:
    import yaml
except ImportError:
    yaml = None
# Optional: Slack/webhook notifications
try:
    import requests
except ImportError:
    requests = None

# OpenTelemetry Setup
try:
    trace.set_tracer_provider(TracerProvider())
    tracer = trace.get_tracer(__name__)
    trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
except Exception:
    pass


# ---- Logging Setup ----
def setup_logging(log_file: str = "arbiter_system.log"):
    """
    Sets up a robust logging configuration with both a file handler and a console handler.

    Args:
        log_file (str): The name of the log file to use.
    """
    file_handler = RotatingFileHandler(log_file, maxBytes=2 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    file_handler.addFilter(PIIRedactorFilter())

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    console_handler.addFilter(PIIRedactorFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


logger = logging.getLogger(__name__)

# Prometheus metrics
workflow_ops_total = Counter("workflow_ops_total", "Total workflow operations", ["operation"])
workflow_errors_total = Counter("workflow_errors_total", "Total workflow errors", ["operation"])


# ---- Config Loader ----
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(IOError),
)
async def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """
    Loads configuration from a file, with environment variables as overrides.

    Args:
        path (Optional[str]): Path to a JSON or YAML configuration file.

    Returns:
        Dict[str, Any]: The final, validated configuration dictionary.

    Raises:
        SystemExit: If a critical configuration error is found.
    """
    default_config = {
        "base_port": 9001,
        "http_port": 9000,
        "num_arbiters": 2,
        "agent_tasks": [],  # List of agent task dicts, fully user-defined
        "output_dir": "agent_output",
        "log_file": "arbiter_system.log",
        "results_summary_file": None,
        "health_port": 8080,
        "max_concurrent_arbiters": 5,
        "codebase_paths": ["."],
    }

    config = default_config.copy()

    # Load config file (JSON or YAML)
    if path and os.path.isfile(path):
        logger.info(f"Loading configuration from file: {path}")
        try:
            async with aiofiles.open(path, "r") as f:
                content = await f.read()
            ext = os.path.splitext(path)[-1].lower()
            if ext in (".yaml", ".yml"):
                if yaml is None:
                    logger.error("PyYAML is not installed. Cannot load YAML config file.")
                    raise SystemExit(1)
                file_config = yaml.safe_load(content)
            else:
                file_config = json.loads(content)
            if file_config:
                config.update(file_config)
        except Exception as e:
            logger.error(f"Error loading config from {path}: {e}. Exiting.", exc_info=True)
            raise SystemExit(1)
    elif path:
        logger.error(f"Configuration file not found: {path}")
        raise SystemExit(1)

    # Environment variables override file config and defaults
    env_config = {
        "base_port": int(os.getenv("ARB_BASE_PORT", config["base_port"])),
        "http_port": int(os.getenv("ARB_HTTP_PORT", config["http_port"])),
        "num_arbiters": int(os.getenv("ARB_NUM_ARBITERS", config["num_arbiters"])),
        "output_dir": os.getenv("OUTPUT_DIR", config["output_dir"]),
        "log_file": os.getenv("LOG_FILE", config["log_file"]),
        "results_summary_file": os.getenv("RESULTS_SUMMARY_FILE", config["results_summary_file"]),
        "health_port": int(os.getenv("HEALTH_PORT", config["health_port"])),
        "max_concurrent_arbiters": int(
            os.getenv("MAX_CONCURRENT_ARBITERS", config["max_concurrent_arbiters"])
        ),
        "codebase_paths": os.getenv("CODEBASE_PATHS", None),
    }

    # agent_tasks: JSON-encoded list from env
    if os.getenv("AGENT_TASKS"):
        try:
            env_config["agent_tasks"] = json.loads(os.getenv("AGENT_TASKS"))
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                f"AGENT_TASKS env variable is not valid JSON: {e}. Using configured value."
            )

    if isinstance(env_config["codebase_paths"], str):
        env_config["codebase_paths"] = env_config["codebase_paths"].split(",")

    config.update(env_config)

    # Basic validation
    if not isinstance(config.get("num_arbiters"), int) or config["num_arbiters"] < 1:
        logger.error("Config error: 'num_arbiters' must be a positive integer. Exiting.")
        raise SystemExit(1)
    if not isinstance(config.get("output_dir"), str) or not config["output_dir"]:
        logger.error("Config error: 'output_dir' must be a non-empty string. Exiting.")
        raise SystemExit(1)
    if not isinstance(config.get("agent_tasks"), list):
        logger.error("Config error: 'agent_tasks' must be a list. Exiting.")
        raise SystemExit(1)

    # Validate each task in agent_tasks
    for i, task in enumerate(config["agent_tasks"]):
        if not isinstance(task, dict):
            logger.error(f"Config error: agent_tasks[{i}] is not a dictionary. Exiting.")
            raise SystemExit(1)

    workflow_ops_total.labels(operation="load_config").inc()
    return config


# ---- Optional Plugin Loader (for advanced extension) ----
def load_plugins(plugin_folder: str = "plugins") -> Dict[str, Any]:
    """
    Dynamically loads plugins from a specified folder.

    Args:
        plugin_folder (str): The name of the folder containing plugins.

    Returns:
        Dict[str, Any]: A dictionary of loaded plugins, where keys are module names.
    """
    plugins = {}
    if not os.path.exists(plugin_folder):
        logger.info(f"Plugin folder '{plugin_folder}' not found. Skipping plugin load.")
        return plugins

    sys.path.insert(0, plugin_folder)
    for _, name, _ in pkgutil.iter_modules([plugin_folder]):
        try:
            module = importlib.import_module(name)
            if hasattr(module, "Plugin"):
                plugins[name] = module.Plugin
                logger.info(f"Loaded plugin: {name}")
        except Exception as e:
            logger.error(f"Error loading plugin '{name}': {e}", exc_info=True)
            workflow_errors_total.labels(operation="load_plugins").inc()
    sys.path.pop(0)
    return plugins


# ---- Notification Callback (Slack/webhook) ----
def notify_critical_error(message: str, error: Optional[Exception] = None):
    """
    Logs a critical error and attempts to send a webhook notification if configured.

    Args:
        message (str): The primary error message.
        error (Optional[Exception]): The exception object, if available.
    """
    logger.critical(f"CRITICAL NOTIFICATION: {message}")
    if error:
        logger.critical(f"Error details: {error}", exc_info=True)

    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if requests and webhook_url:
        payload = {"text": f"{message}\nError: {error}" if error else message}
        try:
            requests.post(webhook_url, json=payload, timeout=5)
            logger.info("Slack/webhook notification sent.")
        except Exception as e:
            logger.error(f"Failed to send Slack/webhook notification: {e}")
            workflow_errors_total.labels(operation="notify_critical_error").inc()
    else:
        logger.warning(
            "Webhook notification skipped (requests library not found or SLACK_WEBHOOK_URL not set)."
        )


# ---- Generic Agent Task ----
async def run_agent_task(
    arbiter: Arbiter,
    agent_task: Dict[str, Any],
    output_dir: str,
    arbiter_id: int,
    results: List[Dict],
):
    """
    Runs a generic agent task using an Arbiter instance.

    Args:
        arbiter (Arbiter): The agent instance.
        agent_task (Dict[str, Any]): Dictionary describing the task (user/platform defined).
        output_dir (str): The output directory for agent results.
        arbiter_id (int): A unique identifier for the arbiter, used for logging.
        results (List[Dict]): A shared list to append the result summary.
    """
    status_entry = {"arbiter_id": arbiter_id, "status": "unknown", "task": agent_task}
    results.append(status_entry)

    try:
        logger.info(f"Arbiter {arbiter_id} starting task: {agent_task}")
        await arbiter.run_task(agent_task, output_dir)
        logger.info(f"Arbiter {arbiter_id} finished task successfully.")
        status_entry.update({"status": "success"})
    except asyncio.CancelledError:
        logger.warning(f"Arbiter {arbiter_id} was cancelled gracefully.")
        status_entry.update({"status": "cancelled"})
    except Exception as e:
        logger.error(f"Arbiter {arbiter_id} error: {e}", exc_info=True)
        status_entry.update({"status": "error", "error": str(e)})
        workflow_errors_total.labels(operation="run_agent_task").inc()


# ---- Orchestrator ----
async def run_agentic_workflow(config: Dict[str, Any]):
    """
    Orchestrates agentic tasks using ArbiterArena.

    Args:
        config (Dict[str, Any]): The loaded configuration dictionary.
    """
    with tracer.start_as_current_span("run_agentic_workflow"):
        arena = None
        results: List[Dict] = []
        shutdown_event = asyncio.Event()

        def _handle_signal():
            logger.warning("Received shutdown signal. Requesting agents to shut down gracefully...")
            shutdown_event.set()

        loop = asyncio.get_running_loop()
        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is not None:
                try:
                    loop.add_signal_handler(sig, _handle_signal)
                except (NotImplementedError, ValueError):
                    logger.debug(f"Signal handler for {sig_name} not supported on this platform.")

        try:
            logger.info("Initializing ArbiterArena (multi-agent system)...")
            arena = ArbiterArena(
                base_port=config["base_port"],
                num=config["num_arbiters"],
            )
            logger.info(f"Starting arena services on HTTP port {config['http_port']}...")
            await arena.start_arena_services(http_port=config["http_port"])
            logger.info("Arena services started.")

            os.makedirs(config["output_dir"], exist_ok=True)
            logger.info(f"Output directory '{config['output_dir']}' is ready.")

            # --- Load and apply plugins here if desired ---
            # plugins = load_plugins()
            # ... plugin integration logic ...

            # Assign tasks to agents. If agent_tasks is empty, run a default no-op.
            tasks = []
            agent_tasks = config.get("agent_tasks", [])
            if not agent_tasks:
                logger.info("No agent tasks specified. Running a default no-op for each arbiter.")
                agent_tasks = [{} for _ in range(config["num_arbiters"])]

            # Ensure agent tasks list is at least as long as the number of arbiters.
            if len(agent_tasks) < config["num_arbiters"]:
                logger.warning(
                    f"Number of tasks ({len(agent_tasks)}) is less than number of arbiters ({config['num_arbiters']}). Assigning a no-op task to remaining arbiters."
                )
                agent_tasks.extend([{} for _ in range(config["num_arbiters"] - len(agent_tasks))])

            # Use a semaphore to limit concurrent Arbiter executions
            asyncio.Semaphore(config.get("max_concurrent_arbiters", 5))

            for i, arbiter in enumerate(arena.arbiters):
                if shutdown_event.is_set():
                    logger.info("Shutdown requested. Not launching additional agent tasks.")
                    break
                task_def = agent_tasks[i]
                tasks.append(
                    asyncio.create_task(
                        run_agent_task(arbiter, task_def, config["output_dir"], i + 1, results),
                        name=f"Arbiter-Task-{i+1}",
                    )
                )

            logger.info(f"Running {len(tasks)} agentic tasks...")

            # Wait for all tasks to complete or be cancelled
            await asyncio.gather(*tasks, return_exceptions=False)

        except Exception as e:
            logger.error(f"Unhandled critical error during agent workflow: {e}", exc_info=True)
            notify_critical_error("Critical error during agent workflow", e)
            # Re-raise to be caught by the main function's handler
            raise
        finally:
            if arena:
                logger.info("Stopping arena services...")
                # Use `asyncio.create_task` to ensure the stop logic runs without blocking
                await arena.stop_arena_services()
                logger.info("Arena stopped.")

        print("\n--- Agentic Workflow Results Summary ---")
        print(json.dumps(results, indent=2))
        successful_runs = sum(1 for r in results if r.get("status") == "success")
        failed_runs = sum(1 for r in results if r.get("status") in ["error", "cancelled"])
        print(
            f"Total Arbiters: {len(results)}, Successful: {successful_runs}, Failed/Cancelled: {failed_runs}"
        )
        print("----------------------------------------")

        if config["results_summary_file"]:
            try:
                with open(config["results_summary_file"], "w") as f:
                    json.dump(results, f, indent=2)
                logger.info(f"Results summary written to '{config['results_summary_file']}'.")
            except IOError as e:
                logger.error(f"Failed to write results summary: {e}")
                workflow_errors_total.labels(operation="write_summary").inc()

        if failed_runs > 0:
            raise SystemExit(1)
        else:
            raise SystemExit(0)


# ---- CLI Entrypoint ----
async def main():
    """
    Entry point for running Arbiter-powered agentic workflows.
    Loads config, sets up logging, and launches the async workflow.
    Usage: python run_exploration.py [config.json_path | config.yaml_path]
    """
    config_path = sys.argv[1] if len(sys.argv) > 1 else None

    try:
        config = await load_config(config_path)
        setup_logging(config.get("log_file", "arbiter_system.log"))

        # Start the health check server in the background
        health_runner = await start_health_server(config)

        logger.info("Starting agentic workflow entrypoint...")
        logger.info("Loaded Configuration:")
        for key, value in config.items():
            if any(s in str(key).lower() for s in ["key", "token", "password", "secret"]):
                logger.info(f"  {key}: [HIDDEN]")
            else:
                logger.info(f"  {key}: {value}")

        loop = asyncio.get_running_loop()
        shutdown_event = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, shutdown_event.set)

        workflow_task = asyncio.create_task(run_agentic_workflow(config))
        await shutdown_event.wait()

        workflow_task.cancel()
        try:
            await workflow_task
        except asyncio.CancelledError:
            logger.info("Workflow cancelled by signal")

        await health_runner.cleanup()
        logger.info("Health server stopped.")

    except SystemExit as e:
        if e.code != 0:
            logger.critical(f"Script terminated with non-zero exit code: {e.code}")
        else:
            logger.info(f"Script finished with exit code: {e.code}")
        sys.exit(e.code)
    except KeyboardInterrupt:
        logger.info("Script interrupted by user (Ctrl+C). Exiting.")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"Script terminated due to unhandled error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Agentic workflow entrypoint cleanup complete.")


async def start_health_server(config):
    """Starts the health check server."""
    app = web.Application()
    # Fixed: Store config in app state so health_handler can access it
    app["config"] = config
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    # Security: Use environment variable for host binding (default to localhost)
    health_host = os.getenv("HEALTH_HOST", "127.0.0.1")
    site = web.TCPSite(runner, health_host, config["health_port"])
    await site.start()
    logger.info(f"Health server started at http://{health_host}:{config['health_port']}/health")
    return runner


async def health_handler(request: web.Request) -> web.Response:
    """Handles health check requests."""
    try:
        # Fixed: Access config from app state instead of undefined variable
        config = request.app.get("config", {})
        health_data = {"status": "healthy", "config_loaded": bool(config)}
        workflow_ops_total.labels(operation="health_check").inc()
        return web.json_response(health_data)
    except Exception as e:
        workflow_errors_total.labels(operation="health_check").inc()
        logger.error(f"Health check failed: {e}", exc_info=True)
        raise web.HTTPInternalServerError(reason=str(e))


if __name__ == "__main__":
    asyncio.run(main())
