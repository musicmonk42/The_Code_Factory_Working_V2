# main/gui.py
import asyncio  # For async operations and Queue
import gettext
import json
import logging  # Import logging directly
import os  # For environment variables
import sys
import threading  # FIX: Import threading for thread identification
import uuid  # For generating run IDs
from pathlib import Path
from typing import Dict, Optional

import aiofiles  # For async file I/O operations

# Import HTTPException and status for consistent error handling with backend API
# Guard FastAPI imports
try:
    from fastapi import HTTPException, status
except ImportError:
    logging.getLogger(__name__).warning(
        "FastAPI not found. Using dummy HTTPException/status."
    )

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)

    class status:
        HTTP_401_UNAUTHORIZED = 401
        HTTP_500_INTERNAL_SERVER_ERROR = 500


# --- Guard Textual and Async HTTP imports ---
_TEXTUAL_AVAILABLE = False
try:
    import aiohttp  # For making async HTTP requests to the backend API
    from textual.app import App, on
    from textual.binding import Binding
    from textual.containers import Container, Grid, Horizontal, Vertical
    from textual.css.query import NoMatches
    from textual.events import Mount
    from textual.widgets import (  # Keep Select for interval
        Button,
        DataTable,
        Footer,
        Header,
        Input,
        Label,
        Markdown,
        ProgressBar,
        RichLog,
        Select,
        Static,
        TabbedContent,
        TabPane,
        TextArea,
    )

    _TEXTUAL_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).warning(
        "Textual, aiohttp, or dependencies not found. GUI cannot run."
    )

    # Define dummy classes to allow file import
    class App:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            pass

        def query_one(self, *args, **kwargs):
            return self

        def focus(self, *args, **kwargs):
            pass

        def push_screen(self, *args, **kwargs):
            pass

        def pop_screen(self, *args, **kwargs):
            pass

        def set_interval(self, *args, **kwargs):
            return self

        def update(self, *args, **kwargs):
            pass

        def write(self, *args, **kwargs):
            pass

        def clear(self, *args, **kwargs):
            pass

        def add_columns(self, *args, **kwargs):
            pass

        def add_row(self, *args, **kwargs):
            pass

        def update_cell_at(self, *args, **kwargs):
            pass

        def call_soon(self, *args, **kwargs):
            pass

        def call_from_thread(self, *args, **kwargs):
            pass

        def create_task(self, *args, **kwargs):
            pass

        @property
        def text(self):
            return ""

        @text.setter
        def text(self, val):
            pass

        @property
        def value(self):
            return ""

        @value.setter
        def value(self, val):
            pass

        @property
        def classes(self):
            return ""

        @classes.setter
        def classes(self, val):
            pass

        @property
        def visible(self):
            return False

        @visible.setter
        def visible(self, val):
            pass

        @property
        def cursor_row(self):
            return 0

        @cursor_row.setter
        def cursor_row(self, val):
            pass

        @property
        def row_count(self):
            return 0

        @property
        def title(self):
            return ""

        @title.setter
        def title(self, val):
            pass

        @property
        def sub_title(self):
            return ""

        @sub_title.setter
        def sub_title(self, val):
            pass

    class Binding:
        def __init__(self, *args, **kwargs):
            pass

    class Container:
        pass

    class Horizontal(Container):
        pass

    class Vertical(Container):
        pass

    class Grid(Container):
        pass

    class Header:
        pass

    class Footer:
        pass

    class RichLog:
        def __init__(self, *args, **kwargs):
            pass

        def write(self, *args, **kwargs):
            pass  # Note: This is NOT async

    class DataTable:
        def __init__(self, *args, **kwargs):
            pass

        def add_columns(self, *args, **kwargs):
            pass

        def clear(self, *args, **kwargs):
            pass

        def add_row(self, *args, **kwargs):
            pass

        @property
        def cursor_row(self):
            return 0

        @cursor_row.setter
        def cursor_row(self, val):
            pass

        @property
        def row_count(self):
            return 0

        def get_row_at(self, *args, **kwargs):
            return ["dummy_id"]

        def update_cell_at(self, *args, **kwargs):
            pass

    class Button:
        # Nested Pressed event class for @on(Button.Pressed, ...) decorators
        class Pressed:
            def __init__(self, button=None, *args, **kwargs):
                self.button = button

    class Input:
        def __init__(self, *args, **kwargs):
            pass

        @property
        def value(self):
            return ""

        @value.setter
        def value(self, val):
            pass

        def focus(self, *args, **kwargs):
            pass

        # Nested Submitted event class for @on(Input.Submitted, ...) decorators
        class Submitted:
            def __init__(self, input=None, value="", *args, **kwargs):
                self.value = value
                self.input = input

    class TextArea:
        def __init__(self, *args, **kwargs):
            pass

        @property
        def text(self):
            return ""

        @text.setter
        def text(self, val):
            pass

    class Label:
        def __init__(self, *args, **kwargs):
            pass

        def update(self, *args, **kwargs):
            pass

        @property
        def classes(self):
            return ""

        @classes.setter
        def classes(self, val):
            pass

    class ProgressBar:
        def __init__(self, *args, **kwargs):
            pass

        def update(self, *args, **kwargs):
            pass

        @property
        def visible(self):
            return False

        @visible.setter
        def visible(self, val):
            pass

    class TabbedContent:
        def __init__(self, *args, **kwargs):
            pass

        @property
        def active(self):
            return ""

        @active.setter
        def active(self, val):
            pass

    class TabPane:
        pass

    class Static:
        def __init__(self, *args, **kwargs):
            pass

        def update(self, *args, **kwargs):
            pass

    class Markdown:
        def __init__(self, *args, **kwargs):
            pass

    class Select:
        def __init__(self, *args, **kwargs):
            pass

        # Nested Changed event class for @on(Select.Changed, ...) decorators
        class Changed:
            def __init__(self, select=None, value=None, *args, **kwargs):
                self.value = value
                self.select = select

    class Mount:
        pass

    class NoMatches(Exception):
        pass

    def on(*args, **kwargs):
        def decorator(func):
            return func

        return decorator


# --- Custom Module Imports (Guarded for Test Safety) ---
try:
    from generator.intent_parser.intent_parser import IntentParser
    from generator.runner.runner_config import ConfigWatcher, load_config
    from generator.runner.runner_core import Runner
    from generator.runner.runner_logging import (
        logger as runner_logger_instance,
    )  # Use alias
    from generator.runner.runner_metrics import (
        HEALTH_STATUS,
        RUN_PASS_RATE,
        RUN_QUEUE,
        RUN_RESOURCE_USAGE,
        get_metrics_dict,
    )

    _RUNNER_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).warning(
        "Runner or IntentParser modules not found. GUI logic will be dummied."
    )
    _RUNNER_AVAILABLE = False

    # Dummy runner components
    class DummyRunner:
        pass

    class DummyConfigWatcher:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self):
            pass

        def stop(self):
            pass

        def _reload(self, *args, **kwargs):
            pass

    class DummyIntentParser:
        pass

    def load_config(*args, **kwargs):
        return {}

    def get_metrics_dict():
        return {"dummy_metric": 0}

    class DummyMetric:
        def get(self):
            return "N/A"

        def get_size(self):
            return 0

    RUN_QUEUE, RUN_PASS_RATE, RUN_RESOURCE_USAGE, HEALTH_STATUS = (
        DummyMetric(),
        DummyMetric(),
        DummyMetric(),
        DummyMetric(),
    )

    Runner = DummyRunner
    ConfigWatcher = DummyConfigWatcher
    IntentParser = DummyIntentParser
    runner_logger_instance = logging.getLogger("dummy_runner")


# Get module logger - follows Python logging best practices.
# Do NOT call basicConfig() at module level to avoid duplicate logs.
# The application entry point should configure the root logger.
app_logger = logging.getLogger(__name__)  # Logger for the GUI itself

gettext.bindtextdomain("runner", "locale")
gettext.textdomain("runner")
_ = gettext.gettext

# --- API and Configuration Setup ---
# Load API Key from environment variable
GENERATOR_API_KEY = os.getenv("GENERATOR_API_KEY")
if not GENERATOR_API_KEY and _TEXTUAL_AVAILABLE:  # Only warn if textual is available
    app_logger.critical(
        "GENERATOR_API_KEY environment variable not set. API calls will fail."
    )

# Define API Endpoints
API_BASE_URL = os.getenv("GENERATOR_API_BASE_URL", "http://127.0.0.1:8000/api/v1")
API_ENDPOINTS = {
    "run": f"{API_BASE_URL}/run",
    "parse_text": f"{API_BASE_URL}/parse/text",
    "parse_file": f"{API_BASE_URL}/parse/file",
    "parse_feedback": f"{API_BASE_URL}/parse/feedback",  # Note: this is a base for /{item_id}
    "parser_reload_config": f"{API_BASE_URL}/parse/reload_config",
    "runner_feedback": f"{API_BASE_URL}/feedback",  # General feedback on runs
    "metrics": f"{API_BASE_URL}/metrics",
    "api_version": f"{API_BASE_URL}/version",  # Conceptual API version endpoint
}

# PRODUCTION FIX: Make config file paths configurable via environment variables
RUNNER_CONFIG_PATH = os.getenv("RUNNER_CONFIG_PATH", "config.yaml")
PARSER_CONFIG_PATH = os.getenv("PARSER_CONFIG_PATH", "intent_parser.yaml")


class TuiLogHandler(logging.Handler):
    """A logging handler that writes log records to a RichLog widget."""

    def __init__(self, log_widget: RichLog, app: App):  # FIX: Accept app instance
        super().__init__()
        self.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        self.log_widget = log_widget
        self.app = app  # FIX: Store app instance
        self.queue = asyncio.Queue()
        self.worker_task = None
        self._lock = asyncio.Lock()

    async def _process_queue(self):
        """Process log records from the queue."""
        while True:
            try:
                record = await self.queue.get()
                formatted = self.format(record)
                self.log_widget.write(formatted)  # Remove await, as write is not async
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Use print for critical handler errors, as logging might recurse
                print(f"TuiLogHandler error: {e}", file=sys.stderr)

    def emit(self, record):
        """Emit a log record to the queue."""
        try:
            # FIX: Detect execution context (Main Thread vs Background Thread)
            # Textual runs on the Main Thread. call_from_thread MUST NOT be called from the Main Thread.
            is_app_thread = False
            if _TEXTUAL_AVAILABLE and hasattr(self.app, "_thread_id"):
                # _thread_id stores the ID of the thread running the App loop
                is_app_thread = self.app._thread_id == threading.get_ident()

            if _TEXTUAL_AVAILABLE and not is_app_thread:
                # We are in a background thread (e.g., worker, asyncio task in executor), safely switch to app thread
                if hasattr(self.app, "call_from_thread"):
                    self.app.call_from_thread(self.queue.put_nowait, record)
                else:
                    # Fallback if app isn't fully initialized or testing with mocks
                    self.queue.put_nowait(record)
            else:
                # We are already on the App thread (e.g., on_mount, button press callback)
                # Just put directly into queue; it's thread-safe enough for simple object passing
                self.queue.put_nowait(record)

            if self.worker_task is None or self.worker_task.done():
                # FIX: Use app.create_task to run the worker on the app's event loop
                # Ensure loop is running
                try:
                    if (
                        hasattr(self.app, "_loop")
                        and self.app._loop
                        and not self.app._loop.is_closed()
                    ):
                        self.worker_task = self.app.create_task(self._process_queue())
                    elif hasattr(self.app, "create_task"):  # Fallback for mocks
                        self.worker_task = self.app.create_task(self._process_queue())
                except Exception:
                    pass  # Loop might not be ready
        except Exception as e:
            print(f"TuiLogHandler emit error: {e}", file=sys.stderr)

    async def _flush_queue(self):
        """Flush remaining logs in the queue."""
        # This lock prevents flushing while another flush is ongoing
        async with self._lock:
            while not self.queue.empty():
                try:
                    record = await self.queue.get()
                    formatted = self.format(record)
                    self.log_widget.write(formatted)  # Remove await
                    self.queue.task_done()
                except Exception as e:
                    print(f"TuiLogHandler flush error: {e}", file=sys.stderr)

    def close(self):
        """Clean up the handler."""
        if self.worker_task:
            self.worker_task.cancel()
            self.worker_task = None
        try:
            # FIX: Check if the app's loop is still running and use create_task
            # Use simpler check that works with both real Textual apps and basic mocks
            if hasattr(self.app, "create_task"):
                self.app.create_task(self._flush_queue())
        except RuntimeError:
            pass  # Supress error if no loop is available
        super().close()


class MainApp(App):
    """✅ UNIFIED TUI FOR WORKFLOW ORCHESTRATION, INTENT PARSING, AND CLARIFICATION."""

    CSS = """
    RichLog { border: tall $accent; padding: 1; }
    DataTable { height: 20; border: tall $secondary; }
    ProgressBar { height: 1; margin-top: 1; }
    TabbedContent { height: 100%; }
    TabPane { padding: 1; }
    #runner-log, #parser-log, #clarifier-log { height: 1fr; }
    #runner-input, #intent-parser-input, #clarifier-input { margin-top: 1; }
    #clarifier-questions { height: 10; margin-bottom: 1; }
    Button { margin: 1 0; }
    .error { color: red; text-style: bold; }
    .success { color: green; text-style: bold; }
    #metrics-display { border: round $panel; padding: 1; margin: 1;}
    #metrics-refresh-interval { width: 10%; margin-left: 2; }
    """

    BINDINGS = [
        Binding("ctrl+r", "focus_runner", _("Focus Runner Input"), show=True),
        Binding("ctrl+p", "focus_parser", _("Focus Parser Input"), show=True),
        Binding("ctrl+c", "focus_clarifier", _("Focus Clarifier Input"), show=True),
        Binding("ctrl+q", "quit", _("Quit"), show=True),
        Binding("f1", "help", _("Help"), show=True),  # New help binding
    ]

    def __init__(self, production_mode: bool = False):
        super().__init__()
        self.production_mode = production_mode
        self._thread_id = (
            threading.get_ident()
        )  # Store main thread ID for TuiLogHandler
        self._app_initialized = False
        self.config_watcher = None
        self.parser_config_watcher = None
        self.runner = None
        self.intent_parser = None
        # --- Widget Attributes (must be initialized before compose) ---
        self.runner_input = None
        self.runner_progress = None
        self.runner_error = None
        self.runner_log = None
        self.intent_parser_input = None
        self.parser_error = None
        self.clarifier_input = None
        self.clarifier_table = None
        self.clarifier_error = None
        self.metrics_display = None
        self.metrics_error = None
        self.metrics_display_api_version = None
        # Feedback inputs
        self.feedback_input_run_id = None
        self.feedback_input_rating = None
        self.feedback_input_comments = None
        self.feedback_error = None
        self.tui_log_handler = None  # Add handler attribute
        self.metrics_update_interval_task = None  # Initialize timer attribute

    async def on_mount(self) -> None:
        if not self._app_initialized:
            self.config_watcher = ConfigWatcher(RUNNER_CONFIG_PATH, self)
            self.parser_config_watcher = ConfigWatcher(PARSER_CONFIG_PATH, self)
            asyncio.create_task(self.config_watcher.start())
            asyncio.create_task(self.parser_config_watcher.start())
            self._app_initialized = True
            app_logger.info("Textual App and ConfigWatchers initialized.")

        # --- Query and assign widgets ---
        try:
            self.runner_input = self.query_one("#runner_input", Input)
            self.runner_progress = self.query_one("#runner_progress", ProgressBar)
            self.runner_error = self.query_one("#runner_error", Label)
            self.runner_log = self.query_one("#log_output", RichLog)
            self.intent_parser_input = self.query_one("#intent_parser_input", Input)
            self.parser_error = self.query_one("#parser_error", Label)
            self.clarifier_input = self.query_one("#clarifier_input", Input)
            self.clarifier_table = self.query_one("#clarifier_table", DataTable)
            self.clarifier_error = self.query_one("#clarifier_error", Label)
            self.metrics_display = self.query_one("#metrics_display", Static)
            self.metrics_error = self.query_one("#metrics_error", Label)
            self.metrics_display_api_version = self.query_one(
                "#metrics_display_api_version", Static
            )
            # Feedback inputs
            self.feedback_input_run_id = self.query_one("#feedback_input_run_id", Input)
            self.feedback_input_rating = self.query_one("#feedback_input_rating", Input)
            self.feedback_input_comments = self.query_one(
                "#feedback_input_comments", TextArea
            )
            self.feedback_error = self.query_one("#feedback_error", Label)
        except NoMatches as e:
            app_logger.error(
                f"Failed to query critical widget: {e}. TUI will likely be unusable."
            )
            # Optionally re-raise or handle gracefully if critical

        # --- Setup logging and core ---
        if self.runner_log:
            self.tui_log_handler = TuiLogHandler(
                self.runner_log, self
            )  # FIX: Pass self (the app)
            app_logger.addHandler(self.tui_log_handler)

        self.runner = Runner(load_config(RUNNER_CONFIG_PATH))
        from generator.intent_parser.intent_parser import IntentParser

        self.intent_parser = IntentParser()
        self.clarifier_table.add_columns("ID", "Question", "Status")

        await self._update_metrics()
        self.metrics_update_interval_task = self.set_interval(
            5, self._update_metrics
        )  # Store interval task

    async def on_unmount(self) -> None:
        """Clean up resources when the app unmounts."""
        app_logger.info("Textual App unmounting.")
        if hasattr(self, "config_watcher") and self.config_watcher:
            self.config_watcher.stop()
        if hasattr(self, "parser_config_watcher") and self.parser_config_watcher:
            self.parser_config_watcher.stop()
        if (
            hasattr(self, "metrics_update_interval_task")
            and self.metrics_update_interval_task
        ):
            self.metrics_update_interval_task.stop()  # Use .stop() for Timers
        if hasattr(self, "tui_log_handler") and self.tui_log_handler:
            # FIX: Remove handler to prevent zombie writes during tests
            app_logger.removeHandler(self.tui_log_handler)
            self.tui_log_handler.close()

    async def _update_metrics(self) -> None:
        """Update metrics display with current system metrics."""
        try:
            # Example metrics retrieval (replace with actual metrics logic)
            metrics = {
                "queue_size": RUN_QUEUE.get_size(),
                "pass_rate": RUN_PASS_RATE.get(),
                "resource_usage": RUN_RESOURCE_USAGE.get(),
                "health_status": HEALTH_STATUS.get(),
            }
            metrics_text = "\n".join(
                f"{key}: {value}" for key, value in metrics.items()
            )
            if self.metrics_display:
                self.metrics_display.update(metrics_text)
            if self.metrics_error:
                await self._set_success_message(
                    self.metrics_error, "", clear_after=None
                )
        except Exception as e:
            if self.metrics_display:
                self.metrics_display.update(f"[red]Error updating metrics: {e}[/red]")
            if self.metrics_error:
                await self._set_error_message(
                    self.metrics_error, f"Error updating metrics: {e}"
                )
            app_logger.error(f"TUI metrics update error: {e}", exc_info=True)

    async def _on_config_reload(self, new_config, diff):
        # This callback is from ConfigWatcher, applies to CLI/TUI side config
        if self.runner_log:
            await self.runner_log.write(
                _(f"[yellow]CLI/TUI config reloaded. Diff: {json.dumps(diff)}[/yellow]")
            )

    async def _on_parser_config_reload(self, new_config, diff):
        # This callback is from ConfigWatcher for parser's config file
        # We need to trigger the backend API to reload the parser config
        try:
            await self._trigger_backend_config_reload(
                "parser", API_ENDPOINTS["parser_reload_config"]
            )
            if self.runner_log:  # Log to main log
                self.runner_log.write(
                    _("[green]Intent Parser config reload triggered via API.[/green]")
                )
        except Exception as e:
            if self.runner_log:  # Log to main log
                self.runner_log.write(
                    _(f"[red]Error triggering parser config reload via API: {e}[/red]")
                )
            if self.parser_error:
                await self._set_error_message(
                    self.parser_error, f"Parser config reload failed: {e}"
                )

    async def _set_error_message(
        self, label_widget: Label, message: str, clear_after: Optional[float] = 5
    ):
        """Helper to display an error message in a label and clear it after a delay."""
        if not label_widget:
            return
        label_widget.update(f"[red][!] {message}[/red]")  # Added [!] icon
        label_widget.classes = "error"

        async def clear_message():
            await asyncio.sleep(clear_after)
            if label_widget.classes == "error":  # Only clear if it's still our error
                label_widget.update("")
                label_widget.classes = ""

        if clear_after:
            asyncio.create_task(clear_message())

    async def _set_success_message(
        self, label_widget: Label, message: str, clear_after: Optional[float] = 3
    ):
        """Helper to display a success message in a label and clear it after a delay."""
        if not label_widget:
            return
        label_widget.update(f"[green][✓] {message}[/green]")  # Added [✓] icon
        label_widget.classes = "success"

        async def clear_message():
            await asyncio.sleep(clear_after)
            if (
                label_widget.classes == "success"
            ):  # Only clear if it's still our success msg
                label_widget.update("")
                label_widget.classes = ""

        if clear_after:
            asyncio.create_task(clear_message())

    async def _make_api_request(
        self,
        method: str,
        url: str,
        json_data: Optional[Dict] = None,
        files: Optional[Dict] = None,
        timeout: int = 30,
    ) -> Dict:
        """Centralized helper to make authenticated API requests."""
        if not _TEXTUAL_AVAILABLE:  # aiohttp is guarded with textual
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="aiohttp not available",
            )

        if not GENERATOR_API_KEY:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API Key not configured. Please set GENERATOR_API_KEY env var.",
            )

        headers = {
            "X-API-Key": GENERATOR_API_KEY
        }  # Use X-API-Key matching previous logic

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                if files:  # For multipart form data (file uploads)
                    data = aiohttp.FormData()
                    if json_data:  # Add form fields if present
                        for key, value in json_data.items():
                            data.add_field(key, str(value))
                    for field_name, (
                        filename,
                        file_content,
                        content_type,
                    ) in files.items():
                        # Ensure filename and content_type are provided
                        data.add_field(
                            field_name,
                            file_content,
                            filename=filename,
                            content_type=content_type,
                        )

                    async with session.request(
                        method.upper(), url, data=data, timeout=timeout
                    ) as response:
                        response.raise_for_status()  # Raises for 4xx/5xx responses
                        return await response.json()
                else:  # For JSON payloads
                    async with session.request(
                        method.upper(), url, json=json_data, timeout=timeout
                    ) as response:
                        response.raise_for_status()
                        return await response.json()
        except aiohttp.ClientError as e:  # Catch broad client errors
            app_logger.error(
                f"API request to {url} failed with client error: {e}", exc_info=True
            )
            if self.runner_error:
                # Use call_later to schedule UI update from non-main thread if needed
                self.call_later(
                    self._set_error_message,
                    self.runner_error,
                    f"API request failed: {e}",
                )
            raise  # Re-raise original exception
        except json.JSONDecodeError as e:
            app_logger.error(
                f"API returned invalid JSON from {url}: {e}", exc_info=True
            )
            if self.runner_error:
                self.call_later(
                    self._set_error_message,
                    self.runner_error,
                    f"API response invalid: {e}",
                )
            raise  # Re-raise original exception
        except Exception as e:
            app_logger.critical(
                f"Unexpected error during API request to {url}: {e}", exc_info=True
            )
            if self.runner_error:
                self.call_later(
                    self._set_error_message,
                    self.runner_error,
                    f"Unexpected API error: {e}",
                )
            raise  # Re-raise original exception

    async def _trigger_backend_config_reload(self, config_type: str, api_url: str):
        """Triggers a config reload on the backend API."""
        if self.runner_log:
            await self.runner_log.write(
                f"[yellow]Triggering {config_type} config reload on backend API...[/yellow]"
            )
        try:
            response = await self._make_api_request("POST", api_url)
            app_logger.info(f"Backend {config_type} config reload response: {response}")
        except (HTTPException, aiohttp.ClientError) as e:  # Catch API errors
            detail = getattr(e, "detail", str(e))
            app_logger.error(
                f"Failed to trigger backend {config_type} config reload: {detail}"
            )
            raise

    def compose(self):
        yield Header(show_clock=True)
        with TabbedContent(initial="runner-tab"):
            with TabPane(_("Runner"), id="runner-tab"):
                yield Vertical(
                    Label(_("[b]Run Workflow[/b]")),
                    Input(placeholder=_("Enter JSON payload..."), id="runner_input"),
                    ProgressBar(id="runner_progress"),
                    Horizontal(
                        Button(_("Run Workflow"), id="run-button", variant="primary"),
                        Button(
                            _("Reload CLI/TUI Config"),
                            id="reload-runner-config",
                            variant="default",
                        ),
                        Button(
                            _("Submit Feedback"),
                            id="submit-runner-feedback-button",
                            variant="default",
                        ),
                    ),
                    Label(id="runner_error"),
                    Vertical(  # For feedback inputs
                        Label(_("[b]Submit Feedback[/b]")),
                        Input(
                            placeholder=_("Run ID (optional)"),
                            id="feedback_input_run_id",
                        ),
                        Input(
                            placeholder=_("Rating (1-5)"), id="feedback_input_rating"
                        ),
                        TextArea(
                            placeholder=_("Comments (optional)"),
                            id="feedback_input_comments",
                            classes="feedback-comments",
                        ),  # Added class for potential styling
                        Label(id="feedback_error"),
                    ),
                    RichLog(
                        id="log_output", wrap=True, highlight=True
                    ),  # Use single log output
                )

            with TabPane(_("Intent Parser"), id="parser-tab"):
                yield Vertical(
                    Label(_("[b]Parse Document for Intent[/b]")),
                    Input(
                        placeholder=_("Enter text or /path/to/file..."),
                        id="intent_parser_input",
                    ),
                    Horizontal(
                        Button(
                            _("Parse Text/File"), id="parse-button", variant="primary"
                        ),
                        Button(
                            _("Reload Parser Config"),
                            id="reload-parser-config",
                            variant="default",
                        ),
                    ),
                    Label(id="parser_error"),
                    # Note: This tab will also log to the main #log_output
                )

            with TabPane(_("Clarifier"), id="clarifier-tab"):
                yield Vertical(
                    Label(_("[b]Clarify Ambiguities[/b]")),
                    DataTable(id="clarifier_table"),
                    Input(
                        placeholder=_("Enter clarification response..."),
                        id="clarifier_input",
                    ),
                    Button(
                        _("Submit Clarification"),
                        id="clarify-button",
                        variant="primary",
                    ),
                    Label(id="clarifier_error"),
                    # Note: This tab will also log to the main #log_output
                )

            with TabPane(_("Metrics"), id="metrics-tab"):
                yield Vertical(
                    Label(_("[b]System Metrics[/b]")),
                    Static(id="metrics_display"),
                    Horizontal(
                        Button(
                            _("Refresh Metrics"),
                            id="refresh-metrics-button",
                            variant="default",
                        ),
                        Select(
                            [(f"{s}s", s) for s in [5, 10, 30, 60]],
                            value=5,
                            id="metrics_refresh_interval",
                        ),
                    ),
                    Label(id="metrics_error"),  # Error label for metrics
                    Static(id="metrics_display_api_version"),
                )
        yield Footer()

    def action_focus_runner(self) -> None:
        try:
            self.query_one("#runner_input", Input).focus()
            app_logger.info("Focused runner input.")
        except Exception as e:
            app_logger.error(f"Failed to focus runner input: {e}", exc_info=True)
            if self.runner_error:
                self.call_later(
                    self._set_error_message,
                    self.runner_error,
                    f"Failed to focus input: {e}",
                )

    def action_focus_parser(self) -> None:
        try:
            self.query_one("#intent_parser_input", Input).focus()
            app_logger.info("Focused parser input.")
        except Exception as e:
            app_logger.error(f"Failed to focus parser input: {e}", exc_info=True)
            if self.parser_error:
                self.call_later(
                    self._set_error_message,
                    self.parser_error,
                    f"Failed to focus input: {e}",
                )

    def action_focus_clarifier(self) -> None:
        try:
            self.query_one("#clarifier_input", Input).focus()
            app_logger.info("Focused clarifier input.")
        except Exception as e:
            app_logger.error(f"Failed to focus clarifier input: {e}", exc_info=True)
            if self.clarifier_error:
                self.call_later(
                    self._set_error_message,
                    self.clarifier_error,
                    f"Failed to focus input: {e}",
                )

    @on(Input.Submitted, "#runner-input")
    async def run_workflow_from_submit(self, event: Input.Submitted):
        # This wrapper ensures the button press and enter key do the same thing
        await self.run_workflow(event.value)

    @on(Button.Pressed, "#run-button")
    async def run_workflow_from_button(self):
        await self.run_workflow(self.runner_input.value)

    async def run_workflow(self, payload_str: str):
        """Core logic for running a workflow."""
        payload_str = payload_str.strip()
        self.runner_input.value = ""
        if self.runner_progress:
            self.runner_progress.update(progress=0, total=100)
            self.runner_progress.visible = True  # Ensure progress bar is visible
        if self.runner_error:
            await self._set_error_message(
                self.runner_error, "", clear_after=None
            )  # Clear previous errors

        try:
            payload = json.loads(payload_str)
            if self.runner_log:
                self.runner_log.write(
                    f"[blue]Sending run request to API: {API_ENDPOINTS['run']}...[/blue]"
                )

            # Call backend API /api/v1/run
            result = await self._make_api_request(
                "POST", API_ENDPOINTS["run"], json_data=payload
            )

            if self.runner_log:
                self.runner_log.write(
                    f"[green]Run Result from API:\n{json.dumps(result, indent=2)}[/green]"
                )
            if self.runner_error:
                await self._set_success_message(
                    self.runner_error, _("Workflow run submitted successfully!")
                )

            # Update progress based on API response if it provides progress
            if self.runner_progress:
                self.runner_progress.update(
                    progress=100
                )  # Assuming full completion for simple demo

        except json.JSONDecodeError as e:
            if self.runner_log:
                self.runner_log.write(f"[red]Invalid JSON payload: {e}[/red]")
            if self.runner_error:
                await self._set_error_message(
                    self.runner_error, f"Invalid JSON payload: {e}"
                )
        except (HTTPException, aiohttp.ClientError) as e:  # Catch API errors
            detail = getattr(e, "detail", str(e))
            if self.runner_log:
                self.runner_log.write(f"[red]API Error: {detail}[/red]")
            # _make_api_request already sets the error message
        except Exception as e:
            if self.runner_log:
                self.runner_log.write(f"[red]Run Error: {e}[/red]")
            if self.runner_error:
                await self._set_error_message(
                    self.runner_error, f"Unexpected error: {e}"
                )
            app_logger.error(f"TUI run workflow error: {e}", exc_info=True)
        finally:
            if self.runner_progress:
                self.runner_progress.visible = False

    @on(Button.Pressed, "#reload-runner-config")
    async def reload_runner_config_button(self):
        # This reloads the CLI/TUI's own config.yaml
        try:
            if self.config_watcher:
                self.config_watcher._reload(force=True)  # Force immediate reload
            if self.runner_log:
                self.runner_log.write(
                    f"\n[green]CLI/TUI configuration reloaded successfully from {RUNNER_CONFIG_PATH}![/green]"
                )
            if self.runner_error:
                await self._set_success_message(
                    self.runner_error, _("CLI/TUI config reloaded!")
                )
        except Exception as e:
            if self.runner_log:
                self.runner_log.write(
                    f"\n[red]Error reloading CLI/TUI config: {e}[/red]"
                )
            if self.runner_error:
                await self._set_error_message(
                    self.runner_error, f"CLI/TUI config reload failed: {e}"
                )
            app_logger.error(f"TUI CLI/TUI config reload error: {e}", exc_info=True)

    @on(Button.Pressed, "#submit-runner-feedback-button")
    async def submit_runner_feedback_button(self):
        run_id = self.feedback_input_run_id.value.strip() or str(
            uuid.uuid4()
        )  # Generate if empty
        rating_str = self.feedback_input_rating.value.strip()
        comments = self.feedback_input_comments.text.strip()

        # Input validation
        if not rating_str:
            await self._set_error_message(
                self.feedback_error, _("Rating is required (1-5).")
            )
            return
        try:
            rating = int(rating_str)
            if not (1 <= rating <= 5):
                raise ValueError(_("Rating must be an integer between 1 and 5."))
        except ValueError as e:
            await self._set_error_message(self.feedback_error, str(e))
            return

        await self._set_error_message(
            self.feedback_error, "", clear_after=None
        )  # Clear previous errors
        if self.runner_log:
            self.runner_log.write(
                f"[blue]Submitting feedback for run {run_id}...[/blue]"
            )

        feedback_payload = {"run_id": run_id, "rating": rating, "comments": comments}

        try:
            # Call backend API /api/v1/feedback
            result = await self._make_api_request(
                "POST", API_ENDPOINTS["runner_feedback"], json_data=feedback_payload
            )
            if self.runner_log:
                self.runner_log.write(
                    f"[green]Feedback submitted. API Response: {result.get('message', 'N/A')}[/green]"
                )
            await self._set_success_message(
                self.feedback_error, _("Feedback submitted successfully!")
            )
            # Clear input fields on success
            if (
                self.feedback_input_run_id.value == run_id
            ):  # Only clear if it was auto-generated or explicitly matches
                self.feedback_input_run_id.value = ""
            self.feedback_input_rating.value = ""
            self.feedback_input_comments.text = ""
        except (HTTPException, aiohttp.ClientError) as e:  # Catch API errors
            detail = getattr(e, "detail", str(e))
            if self.runner_log:
                self.runner_log.write(f"[red]Feedback API Error: {detail}[/red]")
            await self._set_error_message(
                self.feedback_error, f"Feedback API Error: {detail}"
            )
        except Exception as e:
            if self.runner_log:
                self.runner_log.write(f"[red]Feedback Submission Error: {e}[/red]")
            await self._set_error_message(self.feedback_error, f"Unexpected error: {e}")
            app_logger.error(f"TUI feedback submission error: {e}", exc_info=True)

    @on(Input.Submitted, "#intent-parser-input")
    async def run_intent_parser_from_submit(self, event: Input.Submitted):
        await self.run_intent_parser(event.value)

    @on(Button.Pressed, "#parse-button")
    async def run_intent_parser_from_button(self):
        await self.run_intent_parser(self.intent_parser_input.value)

    async def run_intent_parser(self, text_input: str):
        """Core logic for parsing intent."""
        text_input = text_input.strip()
        self.intent_parser_input.value = ""
        await self._set_error_message(
            self.parser_error, "", clear_after=None
        )  # Clear previous errors

        if not text_input:
            await self._set_error_message(
                self.parser_error, _("Input text or file path cannot be empty.")
            )
            return

        # PRODUCTION FIX: Improved file path validation logic
        path = Path(text_input)
        is_file = False
        try:
            # Heuristic to check if input is intended as a path
            if os.path.sep in text_input or (
                os.path.altsep and os.path.altsep in text_input
            ):
                if not path.exists():
                    await self._set_error_message(
                        self.parser_error, f"Path does not exist: {path}"
                    )
                    return
                if not path.is_file():
                    await self._set_error_message(
                        self.parser_error, f"Path is a directory, not a file: {path}"
                    )
                    return
                is_file = True
        except (IOError, OSError) as e:
            # Handle invalid path characters (e.g., "a:b")
            app_logger.warning(
                f"Input '{text_input}' is not a valid path. Treating as text. Error: {e}"
            )
            is_file = False

        api_endpoint = (
            API_ENDPOINTS["parse_file"] if is_file else API_ENDPOINTS["parse_text"]
        )
        if self.runner_log:  # Log to main log
            self.runner_log.write(
                f"[blue]Sending parse request to API: {api_endpoint}...[/blue]"
            )

        try:
            if is_file:
                # For file upload, use multipart/form-data
                async with aiofiles.open(path, "rb") as f:
                    file_content = await f.read()

                files = {"file": (path.name, file_content, "application/octet-stream")}
                form_data = {"format_hint": None, "dry_run": False}  # Other form fields
                result = await self._make_api_request(
                    "POST",
                    API_ENDPOINTS["parse_file"],
                    json_data=form_data,
                    files=files,
                )
            else:
                # For text parsing, use JSON payload
                json_data = {
                    "content": text_input,
                    "format_hint": None,
                    "dry_run": False,
                }
                result = await self._make_api_request(
                    "POST", API_ENDPOINTS["parse_text"], json_data=json_data
                )

            if self.runner_log:  # Log to main log
                self.runner_log.write(
                    f"[green]Parsed Result from API:\n{json.dumps(result, indent=2)}[/green]"
                )
            await self._set_success_message(
                self.parser_error, _("Parsing completed successfully!")
            )

            # Update clarifier questions if ambiguities are returned
            if "ambiguities" in result and result["ambiguities"]:
                self.clarifier_table.clear()
                for q in result["ambiguities"]:
                    question_id = q.get("id", str(uuid.uuid4()))
                    self.clarifier_table.add_row(
                        question_id,
                        q.get("question", "N/A"),
                        "Pending",
                        key=question_id,
                    )

                if self.clarifier_table.row_count > 0:
                    self.clarifier_table.cursor_row = 0  # Focus first question
                if self.runner_log:
                    self.runner_log.write(
                        _("[yellow]Ambiguities detected! Check Clarifier tab.[/yellow]")
                    )
                self.query_one(TabbedContent).active = (
                    "clarifier-tab"  # Auto-switch to clarifier tab
                )
        except (HTTPException, aiohttp.ClientError) as e:  # Catch API errors
            detail = getattr(e, "detail", str(e))
            if self.runner_log:
                self.runner_log.write(f"[red]Parse API Error: {detail}[/red]")
            await self._set_error_message(
                self.parser_error, f"Parse API Error: {detail}"
            )
        except Exception as e:
            if self.runner_log:
                self.runner_log.write(f"[red]Parse Error: {e}[/red]")
            await self._set_error_message(self.parser_error, f"Unexpected error: {e}")
            app_logger.error(f"TUI parse error: {e}", exc_info=True)

    @on(Button.Pressed, "#reload-parser-config")
    async def reload_parser_config_button(self):
        # This reloads the parser's config.yaml from the CLI/TUI side, then triggers backend API reload.
        try:
            if self.parser_config_watcher:
                self.parser_config_watcher._reload(
                    force=True
                )  # Force immediate reload of local config
            if self.runner_log:
                self.runner_log.write(
                    f"\n[green]CLI/TUI parser config reloaded from {PARSER_CONFIG_PATH}.[/green]"
                )
            await self._trigger_backend_config_reload(
                "parser", API_ENDPOINTS["parser_reload_config"]
            )
            await self._set_success_message(
                self.parser_error, _("Parser config reloaded and API triggered!")
            )
        except Exception as e:
            if self.runner_log:
                self.runner_log.write(
                    f"\n[red]Error reloading parser config: {e}[/red]"
                )
            await self._set_error_message(
                self.parser_error, f"Parser config reload failed: {e}"
            )
            app_logger.error(f"TUI parser config reload error: {e}", exc_info=True)

    @on(Input.Submitted, "#clarifier-input")
    async def submit_clarification_from_submit(self, event: Input.Submitted):
        await self.submit_clarification(event.value)

    @on(Button.Pressed, "#clarify-button")
    async def submit_clarification_from_button(self):
        await self.submit_clarification(self.clarifier_input.value)

    async def submit_clarification(self, response: str):
        """Core logic for submitting clarification."""
        response = response.strip()
        self.clarifier_input.value = ""
        await self._set_error_message(
            self.clarifier_error, "", clear_after=None
        )  # Clear previous errors

        try:
            selected_row_index = self.clarifier_table.cursor_row
            if (
                selected_row_index is None or self.clarifier_table.row_count == 0
            ):  # Handle no selection or empty table
                await self._set_error_message(
                    self.clarifier_error,
                    _("No question selected or no questions available to clarify."),
                )
                return
        except NoMatches:
            await self._set_error_message(
                self.clarifier_error, _("Clarifier table not found.")
            )
            return

        try:
            question_id = self.clarifier_table.get_cell_at((selected_row_index, 0))

            api_url = f"{API_ENDPOINTS['parse_feedback']}/q123"  # Example API for clarifying item

            if self.runner_log:
                self.runner_log.write(
                    f"[blue]Submitting clarification for question {question_id}: '{response}'...[/blue]"
                )

            rating_for_api = (
                1.0
                if response.lower() == "yes"
                else (0.0 if response.lower() == "no" else 0.5)
            )

            result = await self._make_api_request(
                "POST", api_url, json_data={"rating": rating_for_api}
            )

            if self.runner_log:
                self.runner_log.write(
                    f"[green]Clarification submitted. API Response: {result.get('message', 'N/A')}[/green]"
                )
            self.clarifier_table.update_cell_at(
                (selected_row_index, 2), "Resolved [green]✓[/green]"
            )
            await self._set_success_message(
                self.clarifier_error,
                _(f"Clarified question {question_id} successfully!"),
            )

        except (HTTPException, aiohttp.ClientError) as e:  # Catch API errors
            detail = getattr(e, "detail", str(e))
            if self.runner_log:
                self.runner_log.write(f"[red]Clarifier API Error: {detail}[/red]")
            await self._set_error_message(
                self.clarifier_error, f"Clarifier API Error: {detail}"
            )
        except Exception as e:
            if self.runner_log:
                self.runner_log.write(f"[red]Clarification Error: {e}[/red]")
            await self._set_error_message(
                self.clarifier_error, f"Unexpected error: {e}"
            )
            app_logger.error(f"TUI clarification error: {e}", exc_info=True)

    @on(Select.Changed, "#metrics-refresh-interval")
    async def on_metrics_refresh_interval_changed(self, event: Select.Changed):
        """Handle change in metrics refresh interval."""
        if (
            hasattr(self, "metrics_update_interval_task")
            and self.metrics_update_interval_task
        ):
            self.metrics_update_interval_task.stop()  # Use .stop() for Timers

        # Ensure event.value is valid before conversion
        try:
            interval_seconds = int(event.value)
            self.metrics_update_interval_task = self.set_interval(
                interval_seconds, self._update_metrics
            )  # Set new interval
            await self._set_success_message(
                self.metrics_error,
                _(f"Metrics refresh interval set to {interval_seconds}s!"),
                clear_after=2,
            )
        except (ValueError, TypeError):
            app_logger.error(
                f"Invalid metrics refresh interval received: {event.value}"
            )
            await self._set_error_message(
                self.metrics_error, _(f"Invalid interval: {event.value}")
            )

    @on(Button.Pressed, "#refresh-metrics-button")
    async def refresh_metrics_button(self):
        await self._update_metrics()  # Call the internal metrics update
        await self.update_metrics_display()  # Call the API metrics update
        await self._set_success_message(
            self.metrics_error, _("Metrics refreshed!"), clear_after=2
        )

    async def update_metrics_display(self):
        """Fetches and displays API-side system metrics."""
        await self._set_error_message(
            self.metrics_error, "", clear_after=None
        )  # Clear previous errors
        try:
            # Call backend API /api/v1/metrics
            metrics_data = await self._make_api_request("GET", API_ENDPOINTS["metrics"])

            # Fetch API version (conceptual endpoint)
            api_version_str = "N/A"
            try:
                version_info = await self._make_api_request(
                    "GET", API_ENDPOINTS["api_version"]
                )
                api_version_str = version_info.get("version", "N/A")
            except (
                Exception
            ):  # Don't fail entire metrics display if version endpoint is missing
                pass

            if self.metrics_display_api_version:  # Check if widget exists
                self.metrics_display_api_version.update(
                    _(f"API Version: [green]{api_version_str}[/green]")
                )

            # Format metrics for display
            display_text_parts = ["[bold blue]API Metrics:[/bold blue]\n"]
            if metrics_data:
                for key, value in metrics_data.items():
                    if isinstance(value, dict):  # For nested metrics
                        display_text_parts.append(f"[cyan]{key}:[/cyan]\n")
                        for sub_key, sub_value in value.items():
                            display_text_parts.append(
                                f"  - {sub_key}: [green]{sub_value}[/green]\n"
                            )
                    else:
                        display_text_parts.append(
                            f"[cyan]{key}:[/cyan] [green]{value}[/green]\n"
                        )
            else:
                display_text_parts.append(
                    "[yellow]No metrics data returned from API.[/yellow]"
                )

            if self.metrics_display:  # Check if widget exists
                # Combine with local metrics
                local_metrics_text = self.metrics_display.renderable
                if isinstance(local_metrics_text, str):
                    self.metrics_display.update(
                        local_metrics_text + "\n" + "".join(display_text_parts)
                    )
                else:
                    self.metrics_display.update("".join(display_text_parts))

            app_logger.debug("API Metrics display updated.")
        except (HTTPException, aiohttp.ClientError) as e:  # Catch API errors
            detail = getattr(e, "detail", str(e))
            if self.metrics_display:
                self.metrics_display.update(
                    f"[red]Failed to load API metrics: {detail}[/red]"
                )
            await self._set_error_message(
                self.metrics_error, f"Failed to load API metrics: {detail}"
            )
            app_logger.error(f"TUI API metrics load error: {detail}", exc_info=True)
        except Exception as e:
            if self.metrics_display:
                self.metrics_display.update(
                    f"[red]Error updating API metrics: {e}[/red]"
                )
            await self._set_error_message(
                self.metrics_error, f"Error updating API metrics: {e}"
            )
            app_logger.error(f"TUI API metrics update error: {e}", exc_info=True)

    def action_help(self) -> None:
        """Action to display a help dialog."""
        self.push_screen(HelpScreen())


class HelpScreen(App):
    """A simple help screen."""

    BINDINGS = [Binding("escape", "quit", "Close")]

    def compose(self):
        yield Header()
        yield Container(Markdown(_("""
            # AI Generator TUI Help

            This is a unified interface for managing the AI Generator workflow.

            ## Tabs:
            - **Runner**: Trigger the main generation workflow.
            - **Intent Parser**: Parse text or files to extract intent.
            - **Clarifier**: Address ambiguities found during parsing.
            - **Metrics**: View real-time system metrics.

            ## Key Bindings:
            - `Ctrl+R`: Focus Runner input
            - `Ctrl+P`: Focus Intent Parser input
            - `Ctrl+C`: Focus Clarifier input
            - `Ctrl+Q`: Quit application
            - `Esc`: Close this help screen
            - `Enter`: Submit input in focused field or activate button
            - `Tab`: Navigate between interactive elements

            ## Usage:
            - **Runner**: Enter a JSON payload for the generator.
            - **Intent Parser**: Type text directly or a full path to a file.
            * **Clarifier**: Select a question from the table and type your response.

            ---
            [dim]Press ESC to close this help screen.[/dim]
            """)))
        yield Footer()

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed):
        # Filter by ID if more buttons are added later: if event.button.id == "my_button_id":
        self.app.pop_screen()  # Go back to the main app


# --- Run the Server ---
if __name__ == "__main__":
    if not _TEXTUAL_AVAILABLE:
        print("Textual library not found. Cannot run TUI.", file=sys.stderr)
        sys.exit(1)
    app = MainApp()
    app.run()
