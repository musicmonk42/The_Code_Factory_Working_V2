# cli.py
# Fully featured command-line interface for the AI README-to-App Generator.
# Provides workflow execution, status monitoring, logging, configuration management,
# and dynamic plugin handling.
# Created: July 30, 2025.

import asyncio
import contextlib  # FIX: Added for watcher task cancellation
import copy  # FIX: Moved import for feedback command
import datetime
import importlib  # For dynamic plugin loading
import json
import logging  # Explicitly import logging here for use in try/except block
import os
import sys
import time  # FIX: Added missing import for logs command
import uuid  # FIX: Moved import for feedback command
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import aiohttp  # For sending feedback to API
import click
import yaml  # Added for config editing
from prompt_toolkit import prompt as pt_prompt  # Renamed to avoid conflict with rich.prompt.Prompt
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax  # For displaying code/config with highlighting
from rich.traceback import install as rich_traceback_install  # For better error traces

# --- Custom Module Imports (conceptual, as they would be separate files) ---
# Assuming 'engine' provides WorkflowEngine, AGENT_REGISTRY, etc.
try:
    from engine import AGENT_REGISTRY, WorkflowEngine, hot_swap_agent, register_agent
    from runner.alerting import send_alert  # FIX: Standardized import
    from runner.runner_config import ConfigWatcher, load_config
    from runner.runner_logging import log_action, logger, search_logs  # Using runner's logger
    from runner.runner_metrics import get_metrics_dict
    from runner.runner_utils import redact_secrets  # Assuming redact_secrets is available
except ImportError as e:
    # Fallback to dummy implementations if modules aren't found, for easier CLI development
    # In production, these should ideally be resolved.
    class DummyWorkflowEngine:
        def __init__(self, config):
            logging.warning("DummyWorkflowEngine: Initialized.")

        def health_check(self) -> bool:
            logging.warning("DummyWorkflowEngine: Health check not implemented.")
            return True

        async def orchestrate(self, input_file, max_iterations, output_path, dry_run, user_id):
            logging.warning(f"DummyWorkflowEngine: Orchestrating for {user_id}, dry_run={dry_run}")
            await asyncio.sleep(0.5)
            return {"status": "dummy_completed"}

        def _tune_from_feedback(self, rating):
            logging.warning(f"DummyWorkflowEngine: Tuning from feedback: {rating}")

    class DummyConfigWatcher:
        def __init__(self, path, callback):
            logging.warning("DummyConfigWatcher: Initialized.")
            self.path = path
            self.callback = callback

        async def start(self):
            logging.warning("DummyConfigWatcher: Started.")

        async def _reload(self):
            logging.warning("DummyConfigWatcher: Reloading config (dummy).")
            self.callback("new_config", {"dummy": "reloaded"})

        def stop(self):
            logging.warning("DummyConfigWatcher: Stopped.")  # Add dummy stop

    AGENT_REGISTRY = {"dummy_agent": "DummyAgentClass"}

    def register_agent(name, agent_class):
        logging.warning(f"Dummy register_agent: {name}")

    def hot_swap_agent(name, new_agent_class):
        logging.warning(f"Dummy hot_swap_agent: {name}")

    # Dummy logging, metrics, utils if not found
    _cli_logger = logging.getLogger("cli")

    def logger_dummy_info(*args, **kwargs):
        _cli_logger.info(*args, **kwargs)

    def logger_dummy_error(*args, **kwargs):
        _cli_logger.error(*args, **kwargs)

    def logger_dummy_debug(*args, **kwargs):
        _cli_logger.debug(*args, **kwargs)

    def logger_dummy_warning(*args, **kwargs):
        _cli_logger.warning(*args, **kwargs)

    logger = logging.getLogger("cli_fallback")  # Use a distinct logger name for fallback
    logger.info = logger_dummy_info
    logger.error = logger_dummy_error
    logger.debug = logger_dummy_debug
    logger.warning = logger_dummy_warning

    def search_logs(query: str, limit: int = 10):
        logger.warning("Dummy search_logs: Not implemented.")
        return [f"Dummy log entry for query: {query}"] * min(
            limit, 1
        )  # Return at least one dummy log

    def get_metrics_dict():
        logger.warning("Dummy get_metrics_dict: Not implemented.")
        return {"cli_dummy_metric": 1}

    def redact_secrets(data: Any):
        logger.warning("Dummy redact_secrets: Not implemented.")
        return str(data).replace("SECRET", "[REDACTED]")

    async def send_alert(message: str, severity: str = "info"):
        logger.warning(f"Dummy send_alert: {message} (Severity: {severity})")

    # FIX: Added dummy log_action
    def log_action(action_type: str, category: str = "general", **kwargs):
        logger.warning(f"Dummy log_action: {action_type} (Category: {category}) | Data: {kwargs}")

    WorkflowEngine = DummyWorkflowEngine
    ConfigWatcher = DummyConfigWatcher

    logger.warning(
        f"Failed to import core runner/engine modules: {e}. Using dummy implementations for CLI functionality."
    )


# Setup rich console for all output
console = Console()
# Install rich tracebacks for better error reporting
rich_traceback_install(console=console, show_locals=True)

# Colorful help for Click commands
# FIX: Removed HelpColorsGroup/HelpColorsCommand assignments to fix compatibility issue
# click.Group.cls = HelpColorsGroup
# click.Command.cls = HelpColorsCommand
# help_colors = { # Note: These are set on the group/command, not globally
#     'headers_color': 'yellow',
#     'options_color': 'blue',
#     'doc_color': 'bright_green',
#     'group_help_color': 'cyan'
# }


# FIX: Made --config-file a group option and passed context
@click.group()
@click.option(
    "--config-file",
    "config_path",
    default="config.yaml",
    type=click.Path(exists=True, readable=True, path_type=Path),
    help="Path to the configuration file (e.g., config.yaml).",
)
@click.pass_context
def cli(ctx, config_path):
    """Legendary AI README-to-App Generator CLI"""
    ctx.obj = {"config_path": config_path}
    pass


# --- CLI Extensibility: Dynamic Command Registry ---
_command_registry: Dict[str, click.Command] = {}


def register_cli_command(name: str, help_text: str, group: click.Group = cli):
    """
    Decorator to dynamically register a new CLI command.
    Args:
        name (str): The name of the command.
        help_text (str): Help text for the command.
        group (click.Group): The Click group to add the command to (defaults to top-level cli).
    """

    def decorator(func: Callable):
        command = click.command(name=name, help=help_text)(func)
        group.add_command(command)
        _command_registry[name] = command
        logger.info(f"Dynamically registered CLI command: '{name}'")
        return func

    return decorator


# --- Core Workflow Commands ---
@cli.command(help="Run the main workflow to generate an app from a README.")
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True, readable=True, path_type=Path),
    help="Path to the input README.md file.",
)
@click.option(
    "--max-iterations",
    type=int,
    default=10,
    help="Maximum iterations for the workflow engine's refinement loop.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Simulate workflow execution without making actual changes or external calls.",
)
@click.option(
    "--output-dir",
    default="output",
    type=click.Path(path_type=Path),
    help="Directory to store generated artifacts and logs.",
)
# FIX: Removed standalone --config option, will use context
@click.option(
    "--parallel",
    type=int,
    default=1,
    help="Number of workflow instances to run in parallel using multiprocessing (ignored if distributed).",
)
@click.option(
    "--distributed",
    is_flag=True,
    help="Enable distributed backend for workflow execution (e.g., Kubernetes). Overrides --parallel.",
)
@click.option(
    "--interactive",
    is_flag=True,
    help="Run in interactive mode, prompting for user confirmations and inputs for parameters.",
)
@click.option(
    "--user-id",
    default="cli_user",
    help="User ID for audit logging and personalization.",
)
@click.pass_context  # FIX: Added context pass
def run(
    ctx,
    input_path: Path,
    max_iterations: int,
    dry_run: bool,
    output_dir: Path,
    parallel: int,
    distributed: bool,
    interactive: bool,
    user_id: str,
):
    # FIX: Get config_path from context
    config_path = ctx.obj["config_path"]
    config_data = load_config(config_path)

    # Override config based on CLI flags
    if distributed:
        config_data["backend"] = "kubernetes"  # Example override for distributed backend
        logger.info("Distributed backend enabled via CLI flag. Disabling local parallel.")
        parallel = 1  # Distributed implies external orchestration, not local multiprocessing

    # FIX: Config watcher is now started inside the async task

    if interactive:
        console.print(
            Panel(
                "Interactive Mode: Confirm workflow parameters and steps.",
                title="Workflow Configuration",
                style="bold green",
            )
        )
        input_path = Prompt.ask("Enter README path", default=str(input_path), type=Path)
        max_iterations = Prompt.ask("Max iterations", default=str(max_iterations), type=int)
        dry_run = Confirm.ask("Dry run?", default=dry_run)
        output_dir = Prompt.ask("Output directory", default=str(output_dir), type=Path)
        if not distributed:  # Only ask for parallel if not distributed
            parallel = Prompt.ask("Parallel runs (local)", default=str(parallel), type=int)
        distributed = Confirm.ask("Enable distributed backend?", default=distributed)

        if not Confirm.ask("Proceed with these settings?", default=True):
            console.print("[red]Workflow cancelled by user.[/red]")
            sys.exit(0)

    async def single_run_task_async(run_id: int):
        """Asynchronous task for a single workflow run."""

        # FIX: Start config watcher inside the async task
        watcher = ConfigWatcher(
            config_path,
            lambda new, diff: console.print(
                f"[yellow]Config reloaded: {json.dumps(diff)}[/yellow]"
            ),
        )
        watcher_task = asyncio.create_task(watcher.start())

        try:
            engine = WorkflowEngine(config_data)
            with console.status(f"[bold blue]Run {run_id}: Performing health check...[/bold blue]"):
                if not engine.health_check():
                    console.print(f"[red]Run {run_id}: Health check failed.[/red]")
                    suggest_recovery_cli()
                    return {
                        "status": "failed",
                        "run_id": run_id,
                        "error": "Health check failed",
                    }
                console.print(f"[bold green]Run {run_id}: Health check passed.[/bold green]")

            current_output_path = create_timestamped_output_dir(output_dir)
            console.print(
                f"[bold magenta]Run {run_id}: Output will be saved to: {current_output_path}[/bold magenta]"
            )

            # Use Rich Progress for a more granular progress bar within the task
            with Progress(
                console=console,
                transient=False,
                redirect_stdout=False,
                redirect_stderr=False,
            ) as progress:
                task = progress.add_task(
                    f"[cyan]Run {run_id}: Running workflow...", total=max_iterations
                )

                try:
                    # Pass user_id to orchestrate for detailed audit logging
                    final_status = await engine.orchestrate(
                        input_file=str(input_path),
                        max_iterations=max_iterations,
                        output_path=current_output_path,
                        dry_run=dry_run,
                        user_id=user_id,  # Pass user ID
                    )
                    progress.update(
                        task, completed=max_iterations
                    )  # Ensure completion if early exit
                    console.print(
                        f"[green]Run {run_id}: Workflow completed successfully! Final Status: {final_status.get('status', 'N/A')}[/green]"
                    )
                    logger.info(
                        f"CLI Run {run_id} completed.",
                        status="success",
                        run_id=run_id,
                        output_path=str(current_output_path),
                        user_id=user_id,
                    )
                    return {
                        "status": "completed",
                        "run_id": run_id,
                        "output_path": str(current_output_path),
                        "final_status": final_status,
                    }
                except Exception as e:
                    progress.update(
                        task,
                        completed=max_iterations,
                        description=f"[red]Run {run_id}: Workflow failed[/red]",
                    )
                    logger.error(
                        f"CLI Run {run_id}: Workflow execution failed: {e}",
                        exc_info=True,
                        run_id=run_id,
                        user_id=user_id,
                    )
                    console.print(f"[red]Run {run_id}: Error during workflow execution: {e}[/red]")
                    suggest_recovery_cli(e)
                    return {"status": "failed", "run_id": run_id, "error": str(e)}
        finally:
            # FIX: Ensure watcher is stopped and task is cancelled
            if watcher:
                watcher.stop()
            if watcher_task:
                watcher_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher_task

    try:
        if parallel > 1:
            console.print(
                f"[bold yellow]Running {parallel} workflows in parallel using multiprocessing...[/bold yellow]"
            )

            def sync_worker_wrapper(worker_id: int):
                # Each worker needs its own event loop and should call asyncio.run()
                return asyncio.run(single_run_task_async(worker_id))

            # Use ProcessPoolExecutor for better handling of async tasks in multiprocessing
            from concurrent.futures import ProcessPoolExecutor

            # FIX: Use a new event loop for managing the futures
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                with ProcessPoolExecutor(max_workers=parallel) as executor:
                    futures = [
                        loop.run_in_executor(executor, sync_worker_wrapper, i)
                        for i in range(parallel)
                    ]
                    results = loop.run_until_complete(asyncio.gather(*futures))
                console.print(
                    Panel(
                        f"[green]Parallel runs completed. Results:[/green]\n{json.dumps(results, indent=2)}",
                        title="Parallel Run Summary",
                    )
                )
            finally:
                loop.close()
        else:
            # Single run, directly await the async task
            result = asyncio.run(single_run_task_async(1))
            if result.get("status") != "completed":
                sys.exit(1)  # Exit with error code if single run failed

    except Exception as e:
        logger.error(f"Top-level CLI run command failed: {e}", exc_info=True)
        console.print(f"[red]Critical error in CLI run command: {e}[/red]")
        suggest_recovery_cli(e)
        asyncio.run(send_alert(f"CLI Run command critical failure: {e}", severity="critical"))
        sys.exit(1)


@cli.command(help="Check status and metrics of the running application.")
def status():
    metrics = get_metrics_dict()
    console.print(
        Panel(
            json.dumps(metrics, indent=2),
            title="Application Metrics",
            style="bold blue",
        )
    )


# FIX: Added missing 'metrics' command
@cli.command(name="metrics", help="Display detailed application metrics in JSON format.")
def metrics():
    metrics_data = get_metrics_dict()
    console.print(json.dumps(metrics_data, indent=2))


@cli.command(help="View application logs with optional filtering.")
@click.option(
    "--query",
    default="",
    help="Search query to filter logs. (e.g., 'error', 'workflow_id:xyz')",
)
@click.option("--limit", type=int, default=10, help="Maximum number of log entries to display.")
@click.option(
    "--follow",
    is_flag=True,
    help="Continuously stream new log entries (like `tail -f`).",
)
def logs(query, limit, follow):
    if follow:
        console.print("[bold blue]Streaming logs (Ctrl+C to stop)...[/bold blue]")
        # This is a basic tail. For robust streaming, it would connect to a WebSocket or message queue.
        # For demo, just loop and print new logs.
        last_log_time = datetime.datetime.min.replace(
            tzinfo=datetime.timezone.utc
        )  # Track last log time to avoid duplicates
        while True:
            # Fetch logs with a slightly higher limit to ensure we catch all new ones since last check
            new_logs_raw = search_logs(
                query, limit=limit * 2
            )  # Fetch more, then filter by timestamp

            # Sort logs by timestamp to process oldest new logs first
            new_logs_raw.sort(
                key=lambda x: (
                    datetime.datetime.fromisoformat(x.get("timestamp").replace("Z", "+00:00"))
                    if x.get("timestamp")
                    else datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
                )
            )

            for log_entry in new_logs_raw:
                log_time_str = log_entry.get("timestamp")  # Assuming timestamp key in ISO format
                if log_time_str:
                    log_time = datetime.datetime.fromisoformat(
                        log_time_str.replace("Z", "+00:00")
                    )  # Handle Z for UTC
                    if log_time > last_log_time:
                        console.print(
                            Panel(
                                json.dumps(log_entry, indent=2),
                                style="cyan",
                                title="Log Entry",
                                expand=False,
                            )
                        )
                        last_log_time = log_time
            time.sleep(2)  # Poll every 2 seconds
    else:
        results = search_logs(query, limit)
        if not results:
            console.print("[yellow]No logs found matching your query.[/yellow]")
            return
        for log in results:
            console.print(
                Panel(
                    json.dumps(log, indent=2),
                    style="cyan",
                    title="Log Entry",
                    expand=False,
                )
            )


@cli.command(help="Submit feedback to improve the generator.")
@click.option(
    "--message",
    prompt="Your feedback message",
    help="The feedback message you want to submit.",
)
@click.option(
    "--rating",
    type=click.FloatRange(0, 1),
    default=None,
    help="Optional: A rating from 0.0 to 1.0 for the last workflow run.",
)
@click.option(
    "--run-id",
    default=None,
    help="Optional: ID of the specific run this feedback is for.",
)
@click.option(
    "--user-id",
    default="cli_user",
    help="User ID for audit logging and personalization.",
)
@click.option(
    "--api-endpoint",
    default="http://127.0.0.1:8000/api/v1/feedback",
    help="API endpoint for feedback submission.",
)
@click.pass_context  # FIX: Added context pass
async def feedback(ctx, message, rating, run_id, user_id, api_endpoint):
    """
    Submits feedback to a remote API endpoint, not just local logging.
    """
    feedback_data = {
        "run_id": (run_id if run_id else str(uuid.uuid4())),  # Generate ID if not provided
        "rating": rating,
        "comments": message,
        "user_id": user_id,  # Include user ID in feedback
    }

    redacted_feedback = redact_secrets(
        copy.deepcopy(feedback_data)
    )  # Redact before logging locally
    logger.info("Attempting to send feedback via CLI", extra=redacted_feedback)

    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Content-Type": "application/json"}
            # For production, you'd add Authorization headers (JWT/API Key) here
            # headers['Authorization'] = f"Bearer {your_jwt_token}"

            response = await session.post(
                api_endpoint, json=feedback_data, headers=headers, timeout=5
            )
            response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)

            response_json = await response.json()
            console.print(
                f"[green]Feedback submitted successfully! API Response: {response_json.get('message', 'N/A')}[/green]"
            )
            logger.info(
                "Feedback sent successfully via CLI API.",
                extra={"api_response": response_json, "user_id": user_id},
            )

        # Simulate local application of feedback (e.g., to WorkflowEngine for tuning)
        if rating is not None:
            # FIX: Get config_path from context
            config_path = ctx.obj["config_path"]
            config_data = load_config(config_path)
            engine = WorkflowEngine(config_data)
            engine._tune_from_feedback(rating)  # Pass rating to engine's tuning
            console.print(
                f"[green]Feedback rating {rating} locally applied to engine tuning.[/green]"
            )

    except aiohttp.ClientError as e:
        console.print(f"[red]Error sending feedback to API: Network or HTTP error: {e}[/red]")
        logger.error(f"Feedback API submission failed: {e}", exc_info=True, user_id=user_id)
        await send_alert(f"CLI feedback submission failed to API: {e}", severity="medium")
    except json.JSONDecodeError:
        response_text = await response.text()  # Get raw text
        console.print(f"[red]Error: API returned invalid JSON. Response: {response_text}[/red]")
        logger.error("Feedback API returned invalid JSON.", exc_info=True, user_id=user_id)
    except Exception as e:
        console.print(f"[red]An unexpected error occurred during feedback submission: {e}[/red]")
        logger.critical(
            f"Unexpected error in CLI feedback command: {e}",
            exc_info=True,
            user_id=user_id,
        )
        await send_alert(f"CLI feedback command critical failure: {e}", severity="critical")


@cli.group()  # FIX: Replaced HelpColorsGroup with standard click.Group
def config():
    """Manage application configuration (view, edit, reload, audit)."""
    pass


@config.command(name="show", help="Display the current active configuration.")
@click.option("--raw", is_flag=True, help="Display raw YAML without syntax highlighting.")
@click.pass_context  # FIX: Added context pass
def config_show(ctx, raw):
    # FIX: Get config_path from context
    config_path = ctx.obj["config_path"]
    config_data = load_config(config_path)
    yaml_content = yaml.dump(config_data, indent=2, sort_keys=False)
    if raw:
        console.print(
            Panel(yaml_content, title="Current Configuration (Raw)", style="bold magenta")
        )
    else:
        syntax = Syntax(yaml_content, "yaml", theme="monokai", line_numbers=True)
        console.print(Panel(syntax, title="Current Configuration", style="bold magenta"))


@config.command(name="edit", help="Interactively edit a configuration key-value pair.")
@click.option("--key", help="The configuration key to edit (e.g., 'llm_config.model').")
@click.option(
    "--value",
    help="The new value for the configuration key. (Optional, will prompt if not provided).",
)
@click.option("--user-id", default="cli_admin", help="User ID for audit logging.")
@click.pass_context  # FIX: Added context pass
def config_edit(ctx, key, value, user_id):
    # FIX: Get config_path from context
    config_path = ctx.obj["config_path"]

    # Use a file lock for config editing to prevent race conditions with other processes/threads
    # In a production distributed system, this would be a distributed lock (e.g., ZooKeeper, Redis lock).
    lock_file = Path(str(config_path) + ".lock")
    try:
        with open(lock_file, "x") as f:  # Use 'x' mode to create exclusively
            logger.info(f"Acquired lock for config file: {lock_file}")

            config_data = load_config(config_path)

            if not key:
                key = Prompt.ask("Enter the key to edit (e.g., 'llm_config.model')")

            # Nested key handling
            current_level = config_data
            keys = key.split(".")
            for i, k in enumerate(keys):
                if not isinstance(current_level, dict):
                    console.print(
                        f"[red]Error: Key '{key}' invalid. '{'.'.join(keys[:i])}' is not a dictionary.[/red]"
                    )
                    logger.error(
                        f"Config edit failed: Invalid path, non-dict element encountered for key '{key}' by user '{user_id}'."
                    )
                    return
                if k not in current_level:
                    console.print(
                        f"[red]Error: Key '{key}' not found. Path segment '{k}' missing.[/red]"
                    )
                    logger.error(f"Config edit failed: Key '{key}' not found by user '{user_id}'.")
                    return
                if i < len(keys) - 1:
                    current_level = current_level[k]

            old_value = current_level[keys[-1]]
            if value is None:  # If value not provided as CLI arg, prompt interactively
                value_str = Prompt.ask(
                    f"Enter new value for '{key}' (current: [yellow]{repr(old_value)}[/yellow])"
                )
            else:
                value_str = value

            # Attempt to cast value to appropriate type based on old value's type
            new_value = value_str
            try:
                # Attempt to convert to appropriate Python type based on original type
                if isinstance(old_value, bool):
                    if value_str.lower() in ("true", "1", "yes"):
                        new_value = True
                    elif value_str.lower() in ("false", "0", "no"):
                        new_value = False
                    else:
                        raise ValueError(f"Cannot convert '{value_str}' to boolean.")
                elif isinstance(old_value, int):
                    new_value = int(value_str)
                elif isinstance(old_value, float):
                    new_value = float(value_str)
                elif isinstance(old_value, list):
                    new_value = (
                        json.loads(value_str)
                        if value_str.startswith("[")
                        else [item.strip() for item in value_str.split(",")]
                    )
                elif isinstance(old_value, dict):
                    new_value = json.loads(value_str)
            except ValueError:
                console.print(
                    f"[yellow]Warning: Could not auto-cast '{value_str}' to original type '{type(old_value).__name__}'. Storing as string.[/yellow]"
                )
            except json.JSONDecodeError:
                console.print(
                    f"[yellow]Warning: Could not parse '{value_str}' as JSON for dict/list. Storing as string.[/yellow]"
                )

            current_level[keys[-1]] = new_value

            # Save config with audit trail
            with open(config_path, "w") as f:
                yaml.dump(config_data, f, indent=2)

            log_action(
                "Config Edited",
                category="config",
                key=key,
                old_value=repr(old_value),
                new_value=repr(new_value),
                user_id=user_id,
            )
            console.print(
                f"[green]Config key '[bold]{key}[/bold]' updated from '[dim]{repr(old_value)}[/dim]' to '[bold]{repr(new_value)}[/bold]'.[/green]"
            )
            console.print(
                "[yellow]Remember to run 'config reload' for changes to take effect if the app is already running.[/yellow]"
            )

    except FileExistsError:
        console.print(
            f"[red]Error: Could not acquire config lock. Another process might be editing the config. Lock file exists: {lock_file}[/red]"
        )
        logger.error(
            f"Config edit failed: Lock already exists for user '{user_id}'.",
            exc_info=True,
        )
    except Exception as e:
        console.print(f"[red]An unexpected error occurred during config edit: {e}[/red]")
        logger.critical(
            f"Unexpected error in config edit command: {e}",
            exc_info=True,
            user_id=user_id,
        )
        asyncio.run(send_alert(f"CLI config edit critical failure: {e}", severity="critical"))
    finally:
        if lock_file.exists():
            try:
                os.remove(lock_file)
                logger.info(f"Released config lock: {lock_file}")
            except OSError as e:
                logger.error(f"Failed to release config lock {lock_file}: {e}", exc_info=True)


@config.command(name="reload", help="Trigger a dynamic reload of the application's configuration.")
@click.option(
    "--api-endpoint",
    default="http://127.0.0.1:8000/api/v1/parse/reload_config",
    help="API endpoint to trigger config reload.",
)
@click.option("--user-id", default="cli_user", help="User ID for audit logging.")
async def config_reload(api_endpoint, user_id):
    console.print("[bold yellow]Attempting to reload configuration...[/bold yellow]")
    try:
        # This now explicitly hits the API endpoint for dynamic reload
        async with aiohttp.ClientSession() as session:
            # In production, you'd add Authorization headers (JWT/API Key) here
            # headers = {'Authorization': f"Bearer {your_jwt_token}"}
            response = await session.post(api_endpoint, headers={}, timeout=5)  # No headers for now
            response.raise_for_status()
            response_json = await response.json()
            console.print(
                f"[green]Configuration reload triggered successfully via API. Response: {response_json.get('message', 'N/A')}[/green]"
            )
            log_action(
                "Config Reload Triggered",
                category="config",
                source="cli",
                api_response=response_json,
                user_id=user_id,
            )
    except aiohttp.ClientError as e:
        console.print(
            f"[red]Error triggering config reload via API: Network or HTTP error: {e}[/red]"
        )
        logger.error(
            f"Config reload CLI command failed via API: {e}",
            exc_info=True,
            user_id=user_id,
        )
        await send_alert(f"CLI config reload failed to API: {e}", severity="critical")
    except json.JSONDecodeError:
        response_text = await response.text()
        console.print(
            f"[red]Error: API returned invalid JSON during config reload. Response: {response_text}[/red]"
        )
        logger.error("Config reload API returned invalid JSON.", exc_info=True, user_id=user_id)
    except Exception as e:
        console.print(f"[red]An unexpected error occurred during config reload: {e}[/red]")
        logger.critical(
            f"Unexpected error in config reload CLI command: {e}",
            exc_info=True,
            user_id=user_id,
        )
        await send_alert(f"CLI config reload command critical failure: {e}", severity="critical")


@config.command(name="audit", help="View audit trail of configuration changes.")
@click.option("--limit", type=int, default=10, help="Number of recent audit entries to display.")
@click.option("--user-id", default="cli_user", help="User ID for audit query.")
def config_audit(limit, user_id):
    console.print(Panel("Configuration Audit Trail", title="Config Audit", style="bold blue"))
    # Filter logs for "Config Edited" actions
    audit_logs = [log for log in search_logs(query="category:config", limit=limit)]

    if not audit_logs:
        console.print("[yellow]No configuration audit entries found.[/yellow]")
        return
    for log_entry in audit_logs:
        # Use rich to print structured audit log entries
        panel_title = (
            f"Change by {log_entry.get('user_id', 'N/A')} at {log_entry.get('timestamp', 'N/A')}"
        )
        panel_content = (
            f"Action: {log_entry.get('action_type', 'N/A')}\n"
            f"Key: {log_entry.get('key', 'N/A')}\n"
            f"Old Value: {log_entry.get('old_value', 'N/A')}\n"
            f"New Value: {log_entry.get('new_value', 'N/A')}"
        )
        console.print(Panel(panel_content, style="magenta", title=panel_title, expand=False))


@cli.command(help="Perform a health check of the workflow engine and its components.")
@click.pass_context  # FIX: Added context pass
async def health(ctx):
    # FIX: Get config_path from context
    config_path = ctx.obj["config_path"]
    config_data = load_config(config_path)
    engine = WorkflowEngine(config_data)
    with console.status("[bold blue]Running health checks...[/bold blue]"):
        status = engine.health_check()  # This would check internal engine health
        # For a more comprehensive health check, also hit the API's /health endpoint
        try:
            async with aiohttp.ClientSession() as session:
                response = await session.get("http://127.0.0.1:8000/health", timeout=5)
                response.raise_for_status()
                api_health_status = await response.json()
                api_ok = api_health_status.get("status") == "healthy"
                console.print(f"[bold green]API Health: {api_health_status}[/bold green]")
        except Exception as e:
            api_ok = False
            console.print(f"[red]API Health check failed: {e}[/red]")
            logger.error(f"API health check from CLI failed: {e}", exc_info=True)
            await send_alert(f"CLI API health check failed: {e}", severity="critical")

    overall_status = status and api_ok
    console.print(
        f"[{'green' if overall_status else 'red'}]Overall Health: {'OK' if overall_status else 'FAIL'}[/{'green' if overall_status else 'red'}]"
    )
    if not overall_status:
        sys.exit(1)


@cli.group()  # FIX: Replaced HelpColorsGroup with standard click.Group
def plugin():
    """Manage plugins (list, install, uninstall, enable/disable)."""
    pass


@plugin.command(name="list", help="List all installed and available plugins.")
def plugin_list():
    console.print(Panel("Registered Agents/Plugins:", title="Plugins List", style="bold green"))
    if AGENT_REGISTRY:
        for name in AGENT_REGISTRY:
            console.print(f"- [green]{name}[/green] (Active)")
    else:
        console.print("[dim]No agents/plugins currently registered.[/dim]")

    console.print(
        "\n[bold yellow]Available for Installation (Conceptual from known sources):[/bold yellow]"
    )
    console.print("- [dim]example_nlp_plugin[/dim] (Provides advanced NLP extraction)")
    console.print("- [dim]aws_deploy_plugin[/dim] (Enables deployment to AWS)")


@plugin.command(name="install", help="Install a new plugin by name or path.")
@click.argument("plugin_identifier", type=str)  # FIX: Removed conflicting 'help' keyword
@click.option(
    "--verify-signature",
    is_flag=True,
    help="Verify digital signature of the plugin package (conceptual).",
)
async def plugin_install(plugin_identifier, verify_signature):
    console.print(f"[yellow]Attempting to install plugin: {plugin_identifier}...[/yellow]")

    # In a real system, this would involve:
    # 1. Download/locate plugin package (e.g., from a trusted registry or local path).
    # 2. (Optional) Verify digital signature if verify_signature is true.
    # 3. (Optional) Create an isolated environment (venv) for the plugin if it has complex dependencies.
    # 4. Install dependencies (pip install).
    # 5. Dynamically load the module.
    # 6. Call its registration function.

    try:
        if verify_signature:
            console.print(
                "[red]Signature verification is conceptual and not fully implemented.[/red]"
            )
            # Real implementation would involve:
            # - Fetching public key
            # - Verifying signature on package hash
            # - if not valid: raise Exception("Invalid signature")

        # Assume plugin_identifier is a Python module name or path to a .py file for hot-loading
        if Path(plugin_identifier).suffix == ".py" and Path(plugin_identifier).exists():
            # For local .py file, create a temporary module to import
            spec = importlib.util.spec_from_file_location(
                Path(plugin_identifier).stem, plugin_identifier
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            logger.info(f"Dynamically loaded plugin module from file: '{plugin_identifier}'")
        else:
            module = importlib.import_module(plugin_identifier)
            logger.info(f"Dynamically loaded plugin module: '{plugin_identifier}'")

        # Plugins should expose a 'register' function to register their components (agents, runners, etc.)
        if hasattr(module, "register") and callable(module.register):
            await asyncio.to_thread(module.register)  # Assume register might be sync
            console.print(f"[green]Plugin '{plugin_identifier}' registered successfully.[/green]")
            log_action("Plugin Installed", category="plugin", plugin_name=plugin_identifier)
        else:
            console.print(
                f"[yellow]Module '{plugin_identifier}' loaded, but no 'register()' function found. Cannot install.[/yellow]"
            )
            log_action(
                "Plugin Install Failed",
                category="plugin",
                plugin_name=plugin_identifier,
                reason="No register() function",
            )
            await send_alert(
                f"Plugin install failed: '{plugin_identifier}' no register() found",
                severity="medium",
            )
    except ImportError as e:
        console.print(
            f"[red]Error: Plugin module '{plugin_identifier}' not found. Ensure it's in PYTHONPATH or correctly named: {e}[/red]"
        )
        logger.error(f"Plugin installation failed: {e}", exc_info=True)
        log_action(
            "Plugin Install Failed",
            category="plugin",
            plugin_name=plugin_identifier,
            reason=f"Module not found: {e}",
        )
        await send_alert(
            f"Plugin install failed: '{plugin_identifier}' module not found",
            severity="critical",
        )
    except Exception as e:
        console.print(f"[red]Error installing plugin '{plugin_identifier}': {e}[/red]")
        logger.error(f"Plugin installation failed: {e}", exc_info=True)
        log_action(
            "Plugin Install Failed",
            category="plugin",
            plugin_name=plugin_identifier,
            reason=str(e),
        )
        await send_alert(f"Plugin install critical failure: {e}", severity="critical")


@plugin.command(name="uninstall", help="Uninstall an existing plugin by name.")
@click.argument("plugin_name", type=str)  # FIX: Removed conflicting 'help' keyword
def plugin_uninstall(plugin_name):
    console.print(f"[yellow]Attempting to uninstall plugin: {plugin_name}...[/yellow]")
    if plugin_name in AGENT_REGISTRY:  # Simple deregistration from AGENT_REGISTRY
        del AGENT_REGISTRY[plugin_name]
        console.print(f"[green]Agent/Plugin '{plugin_name}' uninstalled (deregistered).[/green]")
        log_action("Plugin Uninstalled", category="plugin", plugin_name=plugin_name)
    else:
        console.print(
            f"[red]Agent/Plugin '{plugin_name}' not found in registry. Cannot uninstall.[/red]"
        )
        log_action(
            "Plugin Uninstall Failed",
            category="plugin",
            plugin_name=plugin_name,
            reason="Not found in registry",
        )
        asyncio.run(
            send_alert(f"Plugin uninstall failed: '{plugin_name}' not found", severity="medium")
        )


@plugin.command(name="enable", help="Enable a disabled plugin.")
@click.argument("plugin_name", type=str)
def plugin_enable(plugin_name):
    console.print(f"[yellow]Enabling plugin '{plugin_name}' (conceptual)...[/yellow]")
    # This would involve updating config or a plugin state, then hot-swapping if needed.
    console.print(f"[green]Plugin '{plugin_name}' enabled (simulated).[/green]")
    log_action("Plugin Enabled", category="plugin", plugin_name=plugin_name)


@plugin.command(name="disable", help="Disable an active plugin.")
@click.argument("plugin_name", type=str)
def plugin_disable(plugin_name):
    console.print(f"[yellow]Disabling plugin '{plugin_name}' (conceptual)...[/yellow]")
    # This would involve updating config or a plugin state, then hot-swapping/removing from registry.
    console.print(f"[green]Plugin '{plugin_name}' disabled (simulated).[/green]")
    log_action("Plugin Disabled", category="plugin", plugin_name=plugin_name)


@cli.group()  # FIX: Replaced HelpColorsGroup with standard click.Group
def docs():
    """Commands for generating and managing project documentation."""
    pass


@docs.command(name="generate", help="Generate project documentation in various formats.")
@click.option(
    "--output-format",
    type=click.Choice(["markdown", "html", "pdf"]),
    default="html",
    help="Format for the generated documentation.",
)
@click.option(
    "--output-dir",
    default="docs_output",
    type=click.Path(path_type=Path),
    help="Directory to save the generated documentation.",
)
@click.option(
    "--source-dir",
    default="src",
    type=click.Path(exists=True, readable=True, path_type=Path),
    help="Source directory for documentation content (e.g., code, Markdown files).",
)
@click.option("--user-id", default="cli_user", help="User ID for audit logging.")
async def docs_generate(output_format: str, output_dir: Path, source_dir: Path, user_id: str):
    output_path = output_dir
    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        console.print(f"[red]Error creating output directory '{output_path}': {e}[/red]")
        logger.error(
            f"Doc gen failed: Output directory creation error: {e}",
            exc_info=True,
            user_id=user_id,
        )
        await send_alert("CLI doc gen failed: Output directory not writable", severity="critical")
        sys.exit(1)

    console.print(
        f"[bold blue]Generating documentation in '{output_format}' format to '{output_path}' from '{source_dir}'...[/bold blue]"
    )

    with Progress(console=console, transient=False) as progress:
        task = progress.add_task("[cyan]Processing source files and generating docs...", total=100)

        try:
            # --- REAL DOCUMENTATION GENERATION INTEGRATION ---
            # This is where you would invoke your actual documentation engine (e.g., Sphinx, MkDocs, or a custom tool).
            # Example conceptual call to an internal doc generator agent:
            # doc_generator_agent = WorkflowEngine.get_agent('DocGeneratorAgent') # Assuming you register such an agent
            # generated_files = await doc_generator_agent.generate(source_dir, output_path, output_format)

            # For demonstration, we'll simulate the process and create dummy output.
            # In a real setup, `doc_generator_agent` would:
            # 1. Read source_dir content.
            # 2. Parse code/markdown.
            # 3. Apply templates.
            # 4. Write to output_path.

            await asyncio.sleep(1)  # Simulate initial setup
            progress.update(task, advance=20)

            # Simulate parsing source files
            for i in range(5):
                await asyncio.sleep(0.5)
                progress.update(task, advance=10)
                logger.debug(f"Doc gen: Processed source chunk {i+1}")

            # Simulate rendering and writing output
            await asyncio.sleep(1.5)
            progress.update(task, advance=30)

            # Create dummy output file
            dummy_doc_file = output_path / f"project_doc.{output_format}"
            dummy_doc_file.write_text(
                f"<h1>Project Documentation ({output_format})</h1>\n\nThis is auto-generated content from source: {source_dir}",
                encoding="utf-8",
            )

            generated_files = [dummy_doc_file]  # List of generated files

            console.print(
                f"[green]Documentation generated successfully: {', '.join(str(f) for f in generated_files)}[/green]"
            )
            logger.info(
                f"Documentation generated: format={output_format}, path={str(output_path)}, user_id={user_id}",
                category="docs",
            )
            if output_format == "html" and generated_files:
                console.print(
                    f"[yellow]You can view it by opening: file://{generated_files[0].resolve()}[/yellow]"
                )
            console.print("[bold green]Doc generation complete![/bold green]")
        except Exception as e:
            console.print(f"[red]Error generating documentation: {e}[/red]")
            logger.error(f"Doc generation failed: {e}", exc_info=True, user_id=user_id)
            suggest_recovery_cli(e)
            await send_alert(f"CLI doc generation failed: {e}", severity="critical")
            sys.exit(1)


@docs.command(name="view", help="Open or display generated documentation.")
@click.argument(
    "path", type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path)
)  # FIX: Removed conflicting 'help' keyword
def docs_view(path: Path):
    doc_path = path
    console.print(f"[bold blue]Attempting to view documentation: {doc_path}[/bold blue]")

    if doc_path.suffix.lower() in [".html", ".pdf"]:
        try:
            import webbrowser

            webbrowser.open_new_tab(f"file://{doc_path.resolve()}")
            console.print(f"[green]Opened '{doc_path}' in your web browser.[/green]")
        except Exception as e:
            console.print(f"[red]Failed to open in browser: {e}. Trying to display content.[/red]")
            try:
                content = doc_path.read_text(encoding="utf-8")
                console.print(Panel(content, title=f"Content of {doc_path.name}", style="cyan"))
            except Exception as read_e:
                console.print(f"[red]Could not read file content: {read_e}[/red]")
    elif doc_path.suffix.lower() in [
        ".md",
        ".txt",
        ".rst",
        ".yaml",
        ".json",
    ]:  # Also display other text formats
        try:
            content = doc_path.read_text(encoding="utf-8")
            # Use Syntax highlighting for code/config files
            if doc_path.suffix.lower() in [".py", ".js", ".json", "yaml", ".yml"]:
                syntax = Syntax(
                    content,
                    doc_path.suffix.lower().strip("."),
                    theme="monokai",
                    line_numbers=True,
                )
                console.print(Panel(syntax, title=f"Content of {doc_path.name}", style="cyan"))
            else:
                console.print(Panel(content, title=f"Content of {doc_path.name}", style="cyan"))
        except Exception as read_e:
            console.print(f"[red]Could not read file content: {read_e}[/red]")
    else:
        console.print(
            f"[yellow]Unsupported format for direct viewing: {doc_path.suffix}. Displaying raw content.[/yellow]"
        )
        try:
            content = doc_path.read_text(encoding="utf-8")
            console.print(Panel(content, title=f"Raw Content of {doc_path.name}", style="cyan"))
        except Exception as read_e:
            console.print(f"[red]Could not read file content: {read_e}[/red]")


@cli.command(
    name="shell",
    help="Enter an interactive shell mode for continuous CLI command input.",
)
def shell_mode():
    console.print(
        Panel(
            "[bold green]Entering interactive shell mode. Type 'exit' or 'quit' to leave. This shell only executes CLI commands, not arbitrary system commands.[/bold green]",
            style="green",
        )
    )
    # This uses prompt_toolkit for a richer interactive experience
    while True:
        try:
            command_line = pt_prompt("> ").strip()  # Use pt_prompt for interactive input
            if command_line.lower() in ["exit", "quit"]:
                console.print("[bold blue]Exiting shell mode.[/bold blue]")
                break

            args = command_line.split()
            if not args:
                continue

            try:
                # Use Click's invoke to run internal CLI commands
                # This ensures Click's parsing, validation, and help messages work.
                # It does NOT run arbitrary shell commands.
                cli.main(
                    args=args, standalone_mode=False
                )  # standalone_mode=False prevents sys.exit
            except click.exceptions.Exit as e:  # Catch Click's internal exit
                if e.exit_code != 0:
                    console.print(f"[red]Command exited with error code {e.exit_code}.[/red]")
            except click.exceptions.MissingParameter as e:
                console.print(f"[red]Missing parameter: {e.param.name}.[/red]")
            except click.exceptions.BadParameter as e:
                console.print(f"[red]Bad parameter: {e.param.name} - {e.message}[/red]")
            except Exception as e:
                console.print(f"[red]Error executing command: {e}[/red]")
                logger.error(f"Shell command execution failed: {e}", exc_info=True)
        except EOFError:  # Ctrl+D
            console.print("[bold blue]Exiting shell mode (EOF).[/bold blue]")
            break
        except KeyboardInterrupt:  # Ctrl+C
            console.print("[bold blue]Interrupted. Type 'exit' to leave shell mode.[/bold blue]")
            continue
        except Exception as e:
            console.print(f"[red]An unexpected error occurred in shell: {e}[/red]")
            logger.critical(f"Unexpected error in shell mode: {e}", exc_info=True)


def create_timestamped_output_dir(base_dir: Path) -> Path:
    """Creates a timestamped subdirectory within the base_dir for organizing output."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = base_dir / f"run_{timestamp}"
    try:
        output_path.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Created output directory: {output_path}")
    except OSError as e:
        logger.error(f"Failed to create output directory {output_path}: {e}", exc_info=True)
        asyncio.run(send_alert(f"CLI output directory creation failed: {e}", severity="critical"))
        raise  # Re-raise to halt execution if critical
    return output_path


def suggest_recovery_cli(error: Optional[Exception] = None):
    """Provides user-friendly recovery suggestions based on common errors."""
    suggestions = [
        "Ensure your 'config.yaml' file is correctly formatted and paths are valid.",
        "Check if all necessary Python dependencies are installed (refer to 'requirements.txt').",
        "Run 'cli health' to diagnose issues with the workflow engine components or API connectivity.",
        "Try running with '--dry-run' mode to simulate execution and identify early failures.",
        "Consult the application logs using 'cli logs --limit 50 --query error' for detailed error messages.",
        "Ensure the API service (api.py) is running if you are using 'config reload' or submitting feedback.",
    ]
    if error:
        error_type = type(error).__name__
        if "FileNotFound" in error_type:
            suggestions.insert(
                0,
                "[bold red]File not found error:[/bold red] Double-check the input path or config file path.",
            )
        elif (
            "Permission" in error_type or "OSError" in error_type
        ):  # Catch general OS errors for permissions/disk issues
            suggestions.insert(
                0,
                "[bold red]Permission/OS error:[/bold red] Ensure the CLI has necessary read/write permissions for specified directories, or check disk space/integrity.",
            )
        elif (
            "yaml" in str(error).lower() or "Config" in error_type
        ):  # More specific for config errors
            suggestions.insert(
                0,
                "[bold red]Configuration error:[/bold red] Review 'config.yaml' for syntax mistakes (e.g., indentation, missing colons). Use an online YAML validator if unsure.",
            )
        elif "HTTPException" in error_type or "ClientError" in error_type:  # For API related errors
            suggestions.insert(
                0,
                "[bold red]API communication error:[/bold red] Verify the API service is running and accessible at the configured endpoint (e.g., 'http://127.0.0.1:8000').",
            )
        suggestions.append(f"Error-specific detail: [bold yellow]{str(error)}[/bold yellow]")

    console.print(Panel("\n".join(suggestions), title="Recovery Suggestions", style="bold red"))
    console.print(
        "\n[dim]For further assistance, check the project's documentation or open an issue on GitHub.[/dim]"
    )


if __name__ == "__main__":
    # Ensure asyncio is available for any async operations, especially for Click commands that are async.
    # Click 8.0+ supports async commands directly.
    cli(_anyio_backend="asyncio")  # Specify asyncio backend for Click's async support
