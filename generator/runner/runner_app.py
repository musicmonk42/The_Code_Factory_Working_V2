# runner/app.py
# World-class, gold-standard TUI application for the runner system.
# Integrates with a robust backend using structured contracts and error handling.

import sys
import gettext
import aiohttp
import json
from textual.app import App, on
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, Grid
from textual.widgets import Header, Footer, RichLog, DataTable, Button, Input, TextArea, Label, ProgressBar, TabbedContent, TabPane, Tree, TreeNode, Static, Markdown, Switch, Select, Screen
from textual.events import Mount
from textual.css import CSS
from textual.worker import Worker
from textual.timer import Timer
from textual import events
import asyncio
import os
from pathlib import Path
from typing import Any, Optional, Dict, List, Tuple, Callable
import logging
import importlib
import uuid # For generating task_id where needed

# Assume these are available from runner.core
# Import RunnerConfig for ConfigWatcher, Runner for the core logic.
# Import the ConfigWatcher and _on_config_reload_callback directly from runner.config for the TUI setup
from runner.config import RunnerConfig, load_config, ConfigWatcher
# Import necessary components from runner.core, specifically for Runner instance management
from runner.core import Runner
# Import metrics for UI display
from runner.metrics import RUN_QUEUE, RUN_PASS_RATE, RUN_RESOURCE_USAGE, HEALTH_STATUS
# Import our structured logger
from runner.logging import logger
# Import contracts and errors for robust type and error handling
from runner.contracts import TaskPayload, TaskResult, BatchTaskPayload
from runner.errors import RunnerError, TestExecutionError, TimeoutError # Import specific error types


# i18n setup (assuming translations in locale dir)
gettext.bindtextdomain('runner', 'locale')
gettext.textdomain('runner')
_ = gettext.gettext 

# --- Custom Log Handler for TUI ---
class TuiLogHandler(logging.Handler):
    """A logging handler that writes log records to a RichLog widget."""
    def __init__(self, log_widget: RichLog):
        super().__init__()
        # Use a more detailed formatter for TUI logs to include level, time etc.
        self.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")) 
        self.log_widget = log_widget
        self._loop = asyncio.get_event_loop() # Capture the event loop at handler creation

    def emit(self, record: logging.LogRecord) -> None:
        # Avoid logging Textual's own internal messages to prevent recursion/noise
        if record.name == 'textual':
            return

        try:
            # Format the record. This will apply redaction/signing if configured on root logger.
            log_entry = self.format(record)
            # Use call_soon_threadsafe to schedule write in the Textual's event loop
            if self._loop.is_running():
                self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self.log_widget.write(log_entry)))
            else:
                # Fallback for very early logs before event loop is fully running (or on shutdown)
                print(log_entry, file=sys.stderr)
        except Exception as e:
            # Print to stderr as a last resort if TUI logging fails
            print(f"Error in TuiLogHandler: {e} - Original record: {record.getMessage()}", file=sys.stderr)


# --- Endpoint for Plugin Registration ---
# These are module-level registries, shared across instances and re-used for recompose
_plugin_widget_registry: Dict[str, Callable[..., Static]] = {}
_plugin_theme_registry: Dict[str, str] = {}

def register_tui_widget(name: str, widget_class: Callable[..., Static]):
    """Registers a custom Textual widget for dynamic loading."""
    if name in _plugin_widget_registry:
        logger.warning(f"TUI Widget '{name}' already registered. Overwriting.")
    _plugin_widget_registry[name] = widget_class
    logger.info(f"TUI Widget '{name}' registered to module registry.")

def register_tui_theme(name: str, css_class: str):
    """Registers a custom CSS theme."""
    if name in _plugin_theme_registry:
        logger.warning(f"TUI Theme '{name}' already registered. Overwriting.")
    _plugin_theme_registry[name] = css_class
    logger.info(f"TUI Theme '{name}' registered to module registry.")


# --- Main Runner App ---
class RunnerApp(App):
    """The ultimate TUI for orchestration, metrics, logs, config, docs, and feedback."""

    CSS = """
    /* Overall screen layout */
    Screen {
        layout: grid;
        grid-size: 3 3; /* 3 columns, 3 rows for main grid areas */
        grid-gutter: 1 1; /* Gutter between grid cells */
    }

    Header {
        dock: top;
        height: 3;
        grid-column-span: 3; /* Header spans all columns */
    }

    Footer {
        dock: bottom;
        height: 3;
        grid-column-span: 3; /* Footer spans all columns */
    }

    /* Main grid areas */
    #sidebar {
        grid-column: 1;
        grid-row: 1 / span 2; /* Spans first two rows */
        width: 30%;
        border: solid green;
        overflow: auto;
        padding: 1;
    }

    #main-content-tabs { /* Container for main tabs */
        grid-column: 2;
        grid-row: 1 / span 2;
        border: solid blue;
        overflow: auto;
        padding: 1;
    }

    #right-pane {
        grid-column: 3;
        grid-row: 1 / span 2;
        width: 30%;
        border: solid red;
        overflow: auto;
        padding: 1;
    }

    #logs-container { /* Container for RichLog at the bottom */
        grid-column-span: 3; /* Spans all columns */
        grid-row: 3; /* Third row */
        height: 25%; /* Give logs a fixed height */
        border: solid yellow;
        overflow: auto;
    }

    /* Theming and specific widget styles */
    .high-contrast {
        color: white;
        background: black;
    }

    .high-contrast Button {
        background: black;
        border: solid white;
        color: white;
    }

    /* Theming overrides (dynamic classes added by Python) */
    .dark-theme {
        background: #1e1e1e;
        color: white;
    }
    .light-theme {
        background: #f0f0f0;
        color: black;
    }
    /* Add more themes here, e.g., for plugins */
    .ocean-theme {
        background: #001f3f; /* Deep blue */
        color: #7fdbff; /* Light blue */
    }


    /* New styles for documentation tab */
    #docs-tab-content { /* Inner container for docs tab */
        padding: 1;
        overflow: auto;
    }
    #doc-markdown-viewer { /* Markdown widget for docs */
        height: 1fr; /* Takes all available height */
    }

    /* Style for config editor */
    #config-area {
        height: 1fr; /* Take all available space in its pane */
        width: 1fr;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", _("Quit")),
        Binding("ctrl+h", "toggle_high_contrast", _("Toggle High Contrast")),
        Binding("ctrl+l", "toggle_language", _("Toggle Language")),
        Binding("ctrl+t", "toggle_theme", _("Toggle Theme")),
        Binding("ctrl+s", "save_workspace", _("Save Workspace")),
    ]

    def __init__(self, config_path: str = 'runner.yaml', production_mode: bool = False):
        super().__init__()
        self.config_path = Path(config_path)
        self.config: RunnerConfig = load_config(str(self.config_path)) # Type hint config
        self.runner: Runner = Runner(self.config) # Type hint runner
        
        self.production_mode = production_mode
        
        self.log_widget = RichLog(id="main-log", auto_scroll=True, highlight=True)
        self.queue_table = DataTable(id="queue-table")
        self.feedback_input = Input(placeholder=_("Rate (0-1)"), id="feedback-input")
        self.config_area = TextArea(id="config-area")
        self.health_label = Label(_("Health: Unknown"), id="health-label")
        self.cpu_progress = ProgressBar(total=100, id="cpu-progress", show_percentage=True)
        self.mem_progress = ProgressBar(total=100, id="mem-progress", show_percentage=True)
        self.coverage_tree = Tree(_("Coverage Heatmap"), id="coverage-tree")
        self.doc_markdown_viewer = Markdown(_("# Documentation Loading..."), id="doc-markdown-viewer")
        self.doc_output_dir = Path('output') / 'docs'
        self.last_loaded_doc_path: Optional[Path] = None
        self.update_timer: Timer | None = None
        self.remote_session = aiohttp.ClientSession()

        self.workspace_file = Path("workspace.json")
        self.current_theme = 'dark'
        self.current_language = 'en'
        self.current_high_contrast = False
        self.active_tab_id: str = "dashboard-tab"

        self._plugin_widgets = _plugin_widget_registry
        self._plugin_themes = _plugin_theme_registry


    # Gold Standard: Local wrapper for core's config reload callback, ensuring UI refresh
    def _app_config_reload_callback(self, new_config: RunnerConfig, diff: Optional[Dict[str, Any]]) -> None:
        """
        Local wrapper callback for ConfigWatcher. Updates the UI and the app's Runner instance.
        """
        # Call the core's config reload function which updates the Runner instance itself
        # This requires the core's _on_config_reload_callback to accept the runner instance.
        # (Assuming core.py's _on_config_reload_callback now takes `runner_instance_ref` as an argument)
        from runner.core import _on_config_reload_callback as core_config_reload_cb
        core_config_reload_cb(new_config, diff, runner_instance_ref=self.runner)
        
        # After core's runner is updated, also update app's own config reference
        self.config = new_config
        
        # Update UI elements that depend on config
        self.refresh(layout=True) # Re-render to pick up new theme/language changes based on new config
        
        # Update config editor content if it's not the source of change
        try:
            config_editor = self.query_one("#config-area", TextArea)
            new_config_json = new_config.model_dump_json(indent=2)
            if config_editor.text != new_config_json:
                config_editor.text = new_config_json
                self.log_widget.write(_("[yellow]Config editor updated with reloaded config.[/yellow]"))
        except Exception as e:
            logger.error(f"Error updating config editor during reload UI refresh: {e}", exc_info=True)
            self.log_widget.write(_(f"[red]Error updating config editor: {e}[/red]"))

        self.log_widget.write(_("[yellow]UI updated based on reloaded config.[/yellow]"))


    async def on_shutdown(self) -> None:
        """Called when the application is shutting down. Performs graceful cleanup."""
        logger.info("RunnerApp shutting down. Cleaning up resources.")
        
        if self.update_timer:
            self.update_timer.cancel()
            logger.info("Update timer cancelled.")
        
        if self.remote_session and not self.remote_session.closed:
            await self.remote_session.close()
            logger.info("aiohttp.ClientSession closed.")
        
        await self._save_workspace_state()
        logger.info("Workspace state saved on shutdown.")
        
        # Remove the TuiLogHandler to prevent errors if logging continues during app teardown
        if hasattr(self, '_tui_log_handler') and self._tui_log_handler in logger.handlers:
            logger.removeHandler(self._tui_log_handler)
            # TuiLogHandler.close() method could be added if it has specific async cleanup
            logger.info("TuiLogHandler removed.")

        # Gold Standard: Also shut down the metrics exporter gracefully
        if hasattr(self, 'metrics_exporter') and hasattr(self.metrics_exporter, 'shutdown') and callable(self.metrics_exporter.shutdown):
            logger.info("Shutting down metrics exporter.")
            await self.metrics_exporter.shutdown()
        
        logger.info("RunnerApp shutdown complete.")


    def compose(self):
        yield Header(show_clock=True)
        with Grid(id="main-grid"):
            # Left Sidebar
            with Vertical(id="sidebar"):
                yield Label(_("[b]Workflow Control[/b]"))
                yield Button(_("Run Tests"), id="run-tests", variant="primary")
                yield Button(_("Health Check"), id="health-check", variant="default")
                yield Button(_("Edit Config"), id="edit-config", variant="default")

                # Conditionally hide Pause/Resume in production mode
                if not self.production_mode:
                    yield Button(_("Pause (Dev)"), id="pause", variant="default")
                    yield Button(_("Resume (Dev)"), id="resume", variant="default")

                yield Label(_("\n[b]Test Queue[/b]"))
                yield self.queue_table
                yield Label(_("\n[b]Submit Feedback[/b]"))
                yield self.feedback_input
                yield Button(_("Submit Feedback"), id="submit-feedback", variant="default")
                yield Label(_("\n[b]System Health[/b]"))
                yield self.health_label

                # Preferences
                yield Label(_("\n[b]Preferences[/b]"))
                yield Select(
                    [(_("English"), "en"), (_("Spanish"), "es")],
                    id="lang-select",
                    prompt=_("Select Language"),
                    value=self.current_language,
                )
                yield Select(
                    [(_("Dark"), "dark"), (_("Light"), "light"), (_("Ocean"), "ocean")],
                    id="theme-select",
                    prompt=_("Select Theme"),
                    value=self.current_theme,
                )
                yield Switch(id="high-contrast", value=self.current_high_contrast)
                yield Label(_("High Contrast Mode"))

                # Plugin Management - Only show load button if not in strict production mode
                if not self.production_mode:
                    yield Label(_("\n[b]Plugin Management (Dev)[/b]"))
                    yield Button(_("Load Plugin (Dev)"), id="load-plugin", variant="default")


            # Main Content Area (Tabs)
            with TabbedContent(initial_tab=self.active_tab_id, id="main-content-tabs") as tabs:
                tabs.set_class(True, self.current_theme + '-theme')
                if self.current_high_contrast: tabs.add_class('high-contrast')

                with TabPane(_("Dashboard"), id="dashboard-tab"):
                    yield Markdown(_("# Runner Dashboard\n*Overview of current test runner status and performance.*"))
                    yield Label(_("Overall Test Pass Rate:"))
                    self.pass_rate_label = Label(_("[bold]N/A[/bold]"))
                    yield self.pass_rate_label
                    yield Label(_("Overall Performance:"))
                    self.overall_perf_progress = ProgressBar(total=100)
                    yield self.overall_perf_progress

                with TabPane(_("Coverage"), id="coverage-tab"):
                    yield self.coverage_tree

                with TabPane(_("Documentation"), id="docs-tab"):
                    with Vertical(id="docs-tab-content"):
                        yield self.doc_markdown_viewer
                        yield Button(_("Reload Docs"), id="reload-docs", variant="default")

                with TabPane(_("Provenance Explorer"), id="provenance-tab"):
                    self.provenance_tree = Tree(_("[b]Provenance Chain[/b]"))
                    yield self.provenance_tree
                    yield Button(_("Reload Provenance"), id="reload-provenance", variant="default")
                
                with TabPane(_("Configuration"), id="config-tab"):
                    yield Label(_("[b]Edit Configuration (config.yaml)[/b]"))
                    yield self.config_area
                    yield Button(_("Save Config"), id="save-config", variant="success")

                for widget_name, widget_class in self._plugin_widgets.items():
                    try:
                        widget_instance = widget_class() 
                        with TabPane(_(widget_name), id=f"plugin-tab-{widget_name.lower()}"):
                            yield widget_instance
                    except Exception as e:
                        logger.error(f"Error loading plugin widget '{widget_name}': {e}", exc_info=True)
                        yield TabPane(_(f"Plugin Error: {widget_name}"), id=f"plugin-error-tab-{widget_name.lower()}")
                        yield Label(_(f"[red]Error loading plugin widget: {e}[/red]"))


            # Right Pane (Resource Graphs/Live Logs)
            with Vertical(id="right-pane"):
                yield Label(_("[b]Resource Usage[/b]"))
                yield Label(_("CPU Usage"))
                yield self.cpu_progress
                yield Label(_("Memory Usage"))
                yield self.mem_progress
                yield Label(_("\n[b]Live Log Stream[/b]"))
                yield self.log_widget

        yield Footer()

    async def on_mount(self) -> None:
        self._load_workspace_state()

        self.add_class(f"{self.current_theme}-theme")
        if self.current_high_contrast: self.add_class("high-contrast")
        
        try:
            gettext.install('runner', 'locale', names=(self.current_language,))
        except Exception as e:
            logger.error(f"Error installing gettext for language '{self.current_language}': {e}", exc_info=True)
            await self.log_widget.write(_(f"[red]Error setting language: {e}. Defaulting to English.[/red]"))
            self.current_language = 'en'
            gettext.install('runner', 'locale', names=('en',))

        self.refresh(layout=True)

        self.queue_table.add_columns(_("Task ID"), _("Status"), _("Description"))
        
        if self.update_timer:
            self.update_timer.cancel()
        self.update_timer = self.set_interval(1.0, self.update_ui)
        
        if not any(isinstance(h, TuiLogHandler) for h in logger.handlers):
            try:
                self._tui_log_handler = TuiLogHandler(self.log_widget)
                logger.addHandler(self._tui_log_handler)
                logger.setLevel(logging.INFO)
                logger.info("TUI Log Handler initialized and added.")
            except Exception as e:
                print(f"FATAL: Could not set up TUI log handler: {e}", file=sys.stderr)
                logging.basicConfig(level=logging.INFO)
                logger.warning("Falling back to basic console logging due to TUI log handler error.")

        try:
            self.queue_table.aria_label = _("Test queue table")
            self.config_area.aria_label = _("Configuration editor text area")
            self.feedback_input.aria_label = _("Feedback input field")
        except Exception as e:
            logger.error(f"Error setting ARIA labels: {e}", exc_info=True)
            await self.log_widget.write(_(f"[red]Error setting ARIA labels: {e}[/red]"))

        try:
            if self.config_path.exists():
                self.config_area.text = self.config_path.read_text(encoding='utf-8')
                logger.info(f"Loaded config from {self.config_path} into editor.")
            else:
                self.config_area.text = _("# config.yaml not found. Please create one with your settings.")
                await self.log_widget.write(_("[red]Error: config.yaml not found. Please ensure it exists.[/red]"))
                logger.warning("config.yaml not found for TUI display.")
        except Exception as e:
            self.config_area.text = _(f"# Error loading config.yaml: {e}")
            await self.log_widget.write(_(f"[red]Error loading config.yaml: {e}[/red]"))
            logger.error(f"Error loading config.yaml for TUI display: {e}", exc_info=True)

        # Initialize ConfigWatcher and pass the local wrapper callback
        self.config_watcher = ConfigWatcher(str(self.config_path), self._app_config_reload_callback)
        asyncio.create_task(self.config_watcher.start())
        logger.info("ConfigWatcher started for RunnerApp.")

        # Initialize metrics exporter and start its background loop
        from runner.metrics import MetricsExporter
        self.metrics_exporter = MetricsExporter(self.config)
        asyncio.create_task(self.metrics_exporter.export_all_periodically()) # Assuming this method exists and starts a loop

        await self.action_health_check()

        await self._load_documentation()
        await self._update_provenance_explorer()

        try:
            self.query_one(TabbedContent).active = self.active_tab_id
        except Exception as e:
            logger.warning(f"Initial tab '{self.active_tab_id}' not found or error setting: {e}. Defaulting to first tab.", exc_info=True)
            if self.query("#dashboard-tab"):
                self.query_one(TabbedContent).active = "dashboard-tab"
            else:
                logger.critical("Dashboard tab not found. UI likely misconfigured.")


    async def update_ui(self):
        """Periodically updates the UI elements with current metrics and runner state."""
        self.queue_table.clear(rows=True)
        try:
            # Gold Standard: Fetch task queue snapshot using contract-aware method
            if hasattr(self.runner, 'get_task_queue_snapshot') and callable(self.runner.get_task_queue_snapshot):
                tasks_snapshot: List[Dict[str, Any]] = self.runner.get_task_queue_snapshot()
                if tasks_snapshot:
                    for task_info in tasks_snapshot:
                        # task_info is a dict representation of TaskResult for display
                        self.queue_table.add_row(
                            task_info.get('task_id', 'N/A'),
                            task_info.get('status', _("Pending")),
                            task_info.get('description', _("N/A"))
                        )
                else:
                    self.queue_table.add_row("N/A", _("No tasks queued"), "")
            else: # Fallback if runner doesn't have the method or it's not callable
                queue_size = 0
                try:
                    queue_size = RUN_QUEUE.get() # Directly get Prometheus gauge value
                except Exception as e:
                    logger.warning(f"Could not get RUN_QUEUE metric: {e}")
                    self.queue_table.add_row("Error", _("Metrics unavailable"), "Queue size unknown")
                
                if queue_size > 0:
                    self.queue_table.add_row("N/A", _("Pending"), f"Queue size: {int(queue_size)}")
                else:
                    self.queue_table.add_row("N/A", _("No tasks queued"), "")
        except Exception as e:
            self.queue_table.add_row("Error", _("Failed to load queue data"), str(e))
            logger.error(f"Error updating queue table: {e}", exc_info=True)


        try:
            # Gold Standard: Use config.instance_id directly, as it's typed
            instance_id = self.config.instance_id
            cpu_usage = RUN_RESOURCE_USAGE.labels(resource_type='cpu', instance_id=instance_id)._value if hasattr(RUN_RESOURCE_USAGE, '_value') else 0
            mem_usage = RUN_RESOURCE_USAGE.labels(resource_type='mem', instance_id=instance_id)._value if hasattr(RUN_RESOURCE_USAGE, '_value') else 0
            pass_rate = RUN_PASS_RATE._value if hasattr(RUN_PASS_RATE, '_value') else 0

            self.cpu_progress.update(progress=cpu_usage)
            self.mem_progress.update(progress=mem_usage)
            self.pass_rate_label.update(_("Overall Test Pass Rate: [bold]{:.2f}%[/bold]").format(pass_rate * 100))
        except Exception as e:
            logger.error(f"Error updating metrics widgets: {e}", exc_info=True)
            self.pass_rate_label.update(_("Overall Test Pass Rate: [bold]Error[/bold]"))

        try:
            instance_id = self.config.instance_id
            health_status_value = HEALTH_STATUS.labels(component_name='overall', instance_id=instance_id)._value if hasattr(HEALTH_STATUS, '_value') else -1
            self.health_label.update(_("Health: [bold]{}[/bold]").format(_("Good") if health_status_value == 1 else _("Bad") if health_status_value == 0 else _("Unknown")))
        except Exception as e:
            logger.error(f"Error updating health status: {e}", exc_info=True)
            self.health_label.update(_("Health: [bold]Error[/bold]"))

        self.coverage_tree.clear()
        root: TreeNode = self.coverage_tree.root
        try:
            if hasattr(self.runner, 'get_coverage_data') and callable(self.runner.get_coverage_data):
                coverage_data = self.runner.get_coverage_data() # This method should return CoverageReportSchema
                if coverage_data and coverage_data.coverage_details: # Access via model attribute
                    for filename, details in coverage_data.coverage_details.items(): # Iterate over CoverageDetail models
                        # Access via model attributes
                        file_node = root.add(f"{filename} ({details.percentage:.2f}%)")
                        file_node.add(f"[green]Covered: {details.lines_covered}[/green]")
                        file_node.add(f"[red]Uncovered: {details.lines_total - details.lines_covered}[/red]")
                        file_node.expand()
                    root.add_label(_("[dim]Detailed coverage data below.[/dim]"))
                else:
                    root.add_label(_("[dim]No detailed coverage data available.[/dim]"))
            else:
                root.add_label(_("[dim]Coverage data not yet available or configured.[/dim]"))
            self.coverage_tree.show_root = False
        except Exception as e:
            root.add_label(_(f"[red]Error loading coverage: {e}[/red]"))
            logger.error(f"Error updating coverage tree: {e}", exc_info=True)

        if self.config.get('remote_api_url'):
            try:
                if not self.remote_session or self.remote_session.closed:
                    self.remote_session = aiohttp.ClientSession()
                    logger.warning("aiohttp.ClientSession was closed, recreated it.")

                async with self.remote_session.get(self.config['remote_api_url'] + '/status', timeout=5) as resp:
                    if resp.status == 200:
                        remote_metrics = await resp.json()
                        await self.log_widget.write(_("Remote Status: ") + json.dumps(remote_metrics))
                    else:
                        await self.log_widget.write(_(f"Remote status error ({resp.status}): {await resp.text()}"))
            except aiohttp.ClientError as e:
                await self.log_widget.write(_(f"[red]Remote connection error: {e}[/red]"))
                logger.error(f"AIOHTTP ClientError: {e}", exc_info=True)
            except Exception as e:
                await self.log_widget.write(_(f"[red]Unexpected error getting remote status: {e}[/red]"))
                logger.error(f"Unexpected error in remote status check: {e}", exc_info=True)

    async def _load_documentation(self):
        """Loads and displays the latest generated documentation from a predefined path."""
        doc_file_path = self.doc_output_dir / "project_doc.md"
        html_doc_path = self.doc_output_dir / "project_doc.html"

        loaded_content: Optional[str] = None
        current_doc_path_to_load: Optional[Path] = None

        if doc_file_path.exists():
            current_doc_path_to_load = doc_file_path
            try:
                loaded_content = doc_file_path.read_text(encoding='utf-8')
                self.doc_markdown_viewer.update(loaded_content)
                await self.log_widget.write(_(f"Loaded Markdown documentation from: {doc_file_path}"))
            except Exception as e:
                loaded_content = _(f"# Error Loading Documentation\n*Failed to load Markdown documentation from {doc_file_path}: {e}*")
                logger.error(f"Error loading Markdown documentation: {e}", exc_info=True)
                self.doc_markdown_viewer.update(loaded_content)

        elif html_doc_path.exists():
            current_doc_path_to_load = html_doc_path
            loaded_content = _(f"# Documentation Available (HTML)\n\n*Please open [link=file://{html_doc_path.resolve()}]this file[/link] in a web browser to view the HTML documentation.*")
            self.doc_markdown_viewer.update(loaded_content)
            await self.log_widget.write(_(f"HTML documentation found at: {html_doc_path} (not directly rendered in TUI)."))
        else:
            loaded_content = _("# No Documentation Found\n\n*Auto-generated documentation will appear here after a successful workflow run (e.g., in `output/docs/`).*")
            self.doc_markdown_viewer.update(loaded_content)
            await self.log_widget.write(_("No auto-generated documentation files found."))
        
        self.last_loaded_doc_path = current_doc_path_to_load

    async def _update_provenance_explorer(self):
        """Populates the Provenance Explorer tab with data from runner.provenance_chain."""
        self.provenance_tree.clear()
        root: TreeNode = self.provenance_tree.root
        root.set_label(_("[b]Workflow Provenance Chain[/b]"))

        try:
            if hasattr(self.runner, 'provenance_chain') and self.runner.provenance_chain:
                for i, record in enumerate(self.runner.provenance_chain):
                    # Record is a dict, access directly
                    record_label = _(f"[{i+1}] {record.get('data', {}).get('stage_name', 'Unknown Stage')} (Hash: {record.get('hash', 'N/A')[:8]})")
                    node = root.add(record_label)
                    node.add_label(_(f"  Prev Hash: {record.get('prev_hash', 'N/A')[:8]}"))
                    node.add_label(_(f"  Timestamp: {record.get('timestamp_utc', 'N/A')}"))
                    result_summary = record.get('data', {}).get('result_summary', {})
                    if result_summary:
                        for key, val in result_summary.items():
                            if isinstance(val, (str, int, float, bool)):
                                node.add_label(_(f"  {key.capitalize()}: {str(val)[:50]}"))
                    node.expand()
                root.add_label(_("[dim]End of Provenance Chain.[/dim]"))
            else:
                root.add_label(_("[dim]No provenance data available yet.[/dim]"))
            self.provenance_tree.show_root = False
        except Exception as e:
            root.add_label(_(f"[red]Error loading provenance data: {e}[/red]"))
            logger.error(f"Error updating provenance explorer: {e}", exc_info=True)

    async def _save_workspace_state(self):
        """Saves current UI preferences and workspace state to a JSON file."""
        try:
            active_tab_id: str = "dashboard-tab"
            try:
                tab_content = self.query_one(TabbedContent)
                active_tab_id = tab_content.active
            except Exception as e:
                logger.warning(f"Could not get active tab ID for workspace save: {e}. Defaulting to dashboard-tab.")

            workspace_state = {
                "theme": self.current_theme,
                "language": self.current_language,
                "high_contrast": self.current_high_contrast,
                "active_tab_id": active_tab_id,
            }
            with open(self.workspace_file, 'w', encoding='utf-8') as f:
                json.dump(workspace_state, f, indent=4)
            logger.info("Workspace state saved.")
        except Exception as e:
            logger.error(f"Failed to save workspace state: {e}", exc_info=True)
            await self.log_widget.write(_(f"[red]Failed to save workspace state: {e}[/red]"))

    def _load_workspace_state(self):
        """Loads UI preferences and workspace state from a JSON file on startup."""
        if self.workspace_file.exists():
            try:
                with open(self.workspace_file, 'r', encoding='utf-8') as f:
                    workspace_state = json.load(f)
                
                self.current_theme = workspace_state.get("theme", "dark")
                self.current_language = workspace_state.get("language", "en")
                self.current_high_contrast = workspace_state.get("high_contrast", False)
                self.active_tab_id = workspace_state.get("active_tab_id", "dashboard-tab")

                logger.info("Workspace state loaded.")
            except Exception as e:
                logger.warning(f"Failed to load workspace state: {e}. Starting with default preferences.", exc_info=True)
                self.current_theme = 'dark'
                self.current_language = 'en'
                self.current_high_contrast = False
                self.active_tab_id = "dashboard-tab"
        else:
            logger.info(f"No workspace file found at {self.workspace_file}. Starting with default preferences.")


    # --- Event Handlers (Decorated methods for Textual events) ---
    @on(Button.Pressed, "#run-tests")
    async def start_workflow(self):
        self.log_widget.clear()
        await self.log_widget.write(_("[bold green]Starting workflow...[/bold green]"))

        input_file_path_str: Optional[str] = await self.prompt_for_input_file()
        if not input_file_path_str:
            await self.log_widget.write(_("[yellow]Workflow start cancelled by user.[/yellow]"))
            logger.info("Workflow start cancelled by user input.")
            return

        input_file_path = Path(input_file_path_str)
        output_dir = Path('output') # Fixed output directory
        output_dir.mkdir(parents=True, exist_ok=True)

        # Gold Standard: Read file contents for TaskPayload
        code_files: Dict[str, str] = {}
        test_files: Dict[str, str] = {}
        try:
            # Assuming input_file_path is the main test/code entry point (e.g., a README or directory)
            # For a real application, you'd likely have structured input for code_files/test_files
            # For this demo, let's just use a dummy content.
            test_files[input_file_path.name] = input_file_path.read_text(encoding='utf-8') if input_file_path.is_file() else "# Dummy Test Content"
            code_files['dummy_code.py'] = "def main(): pass" # Provide some dummy code
            logger.debug(f"Prepared dummy code/test files for payload. Input path: {input_file_path_str}")
        except Exception as e:
            await self.log_widget.write(_(f"[red]Error preparing input files: {e}[/red]"))
            logger.error(f"Error preparing input files for task payload: {e}", exc_info=True)
            return

        # Gold Standard: Create TaskPayload instance
        task_payload = TaskPayload(
            test_files=test_files,
            code_files=code_files,
            output_path=str(output_dir),
            timeout=self.config.timeout, # Use config timeout
            dry_run=self.config.get('dry_run', False), # Use config dry_run
            priority=0, # Default priority
            task_id=f"tui_task_{uuid.uuid4()}" # Generate unique task_id
        )

        try:
            # Gold Standard: Call runner.run_tests with TaskPayload and handle TaskResult
            if self.config.distributed:
                # If distributed, enqueue and get immediate TaskResult (enqueued status)
                task_result: TaskResult = await self.runner.enqueue(task_payload)
                await self.log_widget.write(_(f"[bold green]Workflow task {task_result.task_id} enqueued for distributed processing.[/bold green]"))
            else:
                # If not distributed, run directly and get final TaskResult
                task_result: TaskResult = await self.runner.run_tests(task_payload)
                if task_result.status == "completed":
                    await self.log_widget.write(_(f"[bold green]Workflow task {task_result.task_id} completed successfully.[/bold green]"))
                    # Display summary of results
                    if task_result.results:
                        pass_rate = task_result.results.get('pass_rate', 0.0) * 100
                        await self.log_widget.write(_(f"  Pass Rate: [bold]{pass_rate:.2f}%[/bold]"))
                        coverage = task_result.results.get('coverage_percentage', 0.0)
                        await self.log_widget.write(_(f"  Coverage: [bold]{coverage:.2f}%[/bold]"))
                else: # Failed or timed out
                    await self.log_widget.write(_(f"[red]Workflow task {task_result.task_id} {task_result.status.replace('_', ' ')}.[/red]"))
                    if task_result.error:
                        await self.log_widget.write(_(f"  Error: {task_result.error.get('detail', 'Unknown error')} (Code: {task_result.error.get('error_code', 'N/A')})"))
                        logger.error(f"Workflow task failed: {task_result.error}", extra={'task_id': task_result.task_id})

        except RunnerError as e: # Catch structured Runner errors
            await self.log_widget.write(_(f"[red]Error starting workflow: {e.detail} (Code: {e.error_code})[/red]"))
            logger.error(f"Error starting workflow: {e.as_dict()}", exc_info=True)
        except Exception as e: # Catch any unexpected Python errors
            await self.log_widget.write(_(f"[red]An unexpected error occurred: {e}[/red]"))
            logger.error(f"Unexpected error starting workflow: {e}", exc_info=True)
        finally:
            await self._load_documentation()
            await self._update_provenance_explorer()


    async def prompt_for_input_file(self) -> Optional[str]:
        """Prompts the user for the input README file path using the Input widget."""
        input_widget = self.query_one("#feedback-input", Input)
        original_placeholder = input_widget.placeholder
        original_value = input_widget.value
        input_widget.value = ""
        input_widget.placeholder = _("Enter README.md path (or leave empty for 'README.md'):")
        input_widget.focus()
        
        result_future = asyncio.Future()
        
        @on(Input.Submitted, "#feedback-input")
        def _on_input_submitted(event: Input.Submitted):
            if not result_future.done():
                result_future.set_result(event.value)

        try:
            value = await asyncio.wait_for(result_future, timeout=60)
            input_path = value.strip()
            if not input_path:
                input_path = 'README.md'
            return input_path
        except asyncio.TimeoutError:
            await self.log_widget.write(_("[red]Input timeout. Workflow start cancelled.[/red]"))
            logger.warning("User input for workflow path timed out.")
            return None
        finally:
            input_widget.placeholder = original_placeholder
            input_widget.value = original_value
            if not result_future.done():
                result_future.cancel()

    @on(Button.Pressed, "#reload-docs")
    async def reload_docs(self):
        await self.log_widget.write(_("[bold blue]Reloading documentation...[/bold blue]"))
        await self._load_documentation()

    @on(Button.Pressed, "#reload-provenance")
    async def reload_provenance(self):
        await self.log_widget.write(_("[bold blue]Reloading provenance explorer...[/bold blue]"))
        await self._update_provenance_explorer()

    @on(Button.Pressed, "#save-config")
    async def save_config(self):
        try:
            config_content = self.query_one("#config-area", TextArea).text
            self.config_path.write_text(config_content, encoding='utf-8')
            
            await self.log_widget.write(_("[green]Configuration saved to config.yaml. Reload will be triggered automatically.[/green]"))
            logger.info(f"Config saved to {self.config_path}. ConfigWatcher should trigger reload.")
        except Exception as e:
            await self.log_widget.write(_(f"[red]Error saving config: {e}[/red]"))
            logger.error(f"Error saving config from TUI: {e}", exc_info=True)


    @on(Button.Pressed, "#submit-feedback")
    async def submit_feedback_action(self):
        feedback_value = self.feedback_input.value.strip()
        if not feedback_value:
            await self.log_widget.write(_("[yellow]Feedback field is empty. Not submitting.[/yellow]"))
            logger.info("Feedback submission cancelled: empty input.")
            return
        
        try:
            score = float(feedback_value)
            if not (0 <= score <= 1):
                raise ValueError(_("Rating must be between 0 and 1."))
            
            if hasattr(self.runner, '_tune_from_feedback') and callable(self.runner._tune_from_feedback):
                self.runner._tune_from_feedback(score)
                await self.log_widget.write(_(f"[green]Feedback rating {score} submitted and applied to engine tuning.[/green]"))
                logger.info(f"Feedback submitted: {score}")
            else:
                await self.log_widget.write(_("[yellow]Engine feedback tuning not available.[/yellow]"))
                logger.warning("Feedback tuning feature not available on runner instance.")
        except ValueError as e:
            await self.log_widget.write(_(f"[red]Invalid feedback: {e}. Please enter a number between 0 and 1.[/red]"))
            logger.warning(f"Invalid feedback input: {feedback_value}. Error: {e}")
        except Exception as e:
            await self.log_widget.write(_(f"[red]Error submitting feedback: {e}[/red]"))
            logger.error(f"Error submitting feedback: {e}", exc_info=True)

        self.feedback_input.value = ""
        self.feedback_input.focus()

    @on(Select.Changed, "#lang-select")
    async def change_language(self, event: Select.Changed):
        new_lang = event.value
        if new_lang != self.current_language:
            self.current_language = new_lang
            try:
                gettext.install('runner', 'locale', names=(new_lang,))
                self.refresh(layout=True)
                await self.log_widget.write(_(f"[bold blue]Language changed to {new_lang}. UI refreshed.[/bold blue]"))
                logger.info(f"Language changed to: {new_lang}")
            except Exception as e:
                logger.error(f"Error changing language to '{new_lang}': {e}", exc_info=True)
                await self.log_widget.write(_(f"[red]Error changing language: {e}. Reverting to {self.current_language}.[/red]"))

    @on(Select.Changed, "#theme-select")
    def change_theme(self, event: Select.Changed):
        new_theme = event.value
        if self.current_theme and self.current_theme != 'dark':
            self.remove_class(f"{self.current_theme}-theme")
        if new_theme != 'dark':
            self.add_class(f"{new_theme}-theme")
        self.current_theme = new_theme
        self.log_widget.write(_(f"Theme changed to {new_theme}."))
        logger.info(f"Theme changed to: {new_theme}")

    @on(Switch.Changed, "#high-contrast")
    def toggle_high_contrast(self, event: Switch.Changed):
        self.current_high_contrast = event.value
        if event.value:
            self.add_class("high-contrast")
            self.log_widget.write(_("High contrast mode enabled."))
            logger.info("High contrast mode enabled.")
        else:
            self.remove_class("high-contrast")
            self.log_widget.write(_("High contrast mode disabled."))
            logger.info("High contrast mode disabled.")

    @on(Button.Pressed, "#load-plugin")
    async def load_plugin(self):
        # Define a whitelist of allowed plugins for production safety.
        TRUSTED_PLUGINS = [
            "runner.plugins.example_plugin",
            "my_custom_plugin",
        ]

        input_widget = self.query_one("#feedback-input", Input)
        original_placeholder = input_widget.placeholder
        original_value = input_widget.value
        input_widget.value = ""
        input_widget.placeholder = _("Enter whitelisted plugin module name:")
        input_widget.focus()
        
        result_future = asyncio.Future()
        @on(Input.Submitted, "#feedback-input")
        def _on_input_submitted(event: Input.Submitted):
            if not result_future.done():
                result_future.set_result(event.value)
        
        try:
            plugin_name = await asyncio.wait_for(result_future, timeout=30)
            plugin_name = plugin_name.strip()
            if not plugin_name:
                await self.log_widget.write(_("[yellow]Plugin load cancelled. Input was empty.[/yellow]"))
                logger.info("Plugin load cancelled: empty input.")
                return

            logger.info(f"Plugin load requested for: {plugin_name}")
            if self.production_mode and plugin_name not in TRUSTED_PLUGINS:
                await self.log_widget.write(_(f"[red]Error: Plugin '{plugin_name}' is not in the trusted whitelist for production.[/red]"))
                logger.warning(f"Attempted to load untrusted plugin in production mode: {plugin_name}")
                return

            try:
                module = importlib.import_module(plugin_name)
                
                if hasattr(module, 'register_tui_widgets') and callable(module.register_tui_widgets):
                    module.register_tui_widgets(register_tui_widget)
                    await self.log_widget.write(_(f"[blue]Plugin '{plugin_name}' registered TUI widgets.[/blue]"))
                else:
                    await self.log_widget.write(_(f"[yellow]Plugin '{plugin_name}' has no 'register_tui_widgets' function.[/yellow]"))

                if hasattr(module, 'register_tui_themes') and callable(module.register_tui_themes):
                    module.register_tui_themes(register_tui_theme)
                    await self.log_widget.write(_(f"[blue]Plugin '{plugin_name}' registered TUI themes.[/blue]"))
                else:
                    await self.log_widget.write(_(f"[yellow]Plugin '{plugin_name}' has no 'register_tui_themes' function.[/yellow]"))
                
                self.recompose()
                self.refresh(layout=True)
                await self.log_widget.write(_(f"[green]Plugin '{plugin_name}' loaded successfully. UI recomposed and refreshed.[/green]"))
                logger.info(f"Plugin loaded and UI recomposed: {plugin_name}")

            except ModuleNotFoundError:
                await self.log_widget.write(_(f"[red]Error: Plugin module '{plugin_name}' not found. Check if it's installed or correctly named.[/red]"))
                logger.error(f"ModuleNotFoundError: Plugin '{plugin_name}' not found.", exc_info=True)
            except AttributeError as ae:
                await self.log_widget.write(_(f"[red]Error loading plugin '{plugin_name}': Missing expected registration function. {ae}[/red]"))
                logger.error(f"AttributeError loading plugin '{plugin_name}': {ae}", exc_info=True)
            except Exception as e:
                await self.log_widget.write(_(f"[red]An unexpected error occurred loading plugin '{plugin_name}': {e}[/red]"))
                logger.error(f"Unexpected error loading plugin '{plugin_name}': {e}", exc_info=True)
        finally:
            input_widget.placeholder = original_placeholder
            input_widget.value = original_value
            if not result_future.done():
                result_future.cancel()

    # --- Global Actions (for keyboard shortcuts) ---
    def action_quit(self) -> None:
        """Called in response to key binding."""
        logger.info("RunnerApp exiting via Ctrl+Q.")
        self.exit()

    def action_toggle_language(self) -> None:
        """Toggle language through available options."""
        try:
            select_widget = self.query_one("#lang-select", Select)
            options = [opt[1] for opt in select_widget.options]
            if not options:
                logger.warning("No language options available to toggle via shortcut.")
                self.log_widget.write(_("[yellow]No language options available to toggle.[/yellow]"))
                return

            current_idx = options.index(self.current_language)
            new_idx = (current_idx + 1) % len(options)
            new_lang = options[new_idx]
            
            select_widget.value = new_lang
            logger.info(f"Toggled language to {new_lang} via keyboard shortcut.")
        except Exception as e:
            logger.error(f"Error toggling language via shortcut: {e}", exc_info=True)
            self.log_widget.write(_(f"[red]Error toggling language: {e}[/red]"))

    def action_toggle_high_contrast(self) -> None:
        """Toggle high contrast mode."""
        try:
            switch_widget = self.query_one("#high-contrast", Switch)
            switch_widget.value = not switch_widget.value
            logger.info(f"Toggled high contrast mode to {switch_widget.value} via keyboard shortcut.")
        except Exception as e:
            logger.error(f"Error toggling high contrast via shortcut: {e}", exc_info=True)
            self.log_widget.write(_(f"[red]Error toggling high contrast: {e}[/red]"))

    def action_toggle_theme(self) -> None:
        """Toggle theme between dark, light, ocean."""
        try:
            select_widget = self.query_one("#theme-select", Select)
            options = [opt[1] for opt in select_widget.options]
            if not options:
                logger.warning("No theme options available to toggle via shortcut.")
                self.log_widget.write(_("[yellow]No theme options available to toggle.[/yellow]"))
                return

            current_idx = options.index(self.current_theme)
            new_idx = (current_idx + 1) % len(options)
            new_theme = options[new_idx]
            
            select_widget.value = new_theme
            logger.info(f"Toggled theme to {new_theme} via keyboard shortcut.")
        except Exception as e:
            logger.error(f"Error toggling theme via shortcut: {e}", exc_info=True)
            self.log_widget.write(_(f"[red]Error toggling theme: {e}[/red]"))


    def action_save_workspace(self) -> None:
        """Save workspace state via key binding."""
        asyncio.create_task(self._save_workspace_state())
        logger.info("Workspace save initiated via keyboard shortcut.")


if __name__ == "__main__":
    import yaml
    import logging
    from runner.logging import configure_logging_from_config
    # Mock the necessary parts for standalone execution if contracts/errors/metrics are not fully set up
    from runner.config import RunnerConfig # Import RunnerConfig
    
    # Minimal mocks for RunnerApp if running standalone
    class MockRunner:
        def __init__(self, config):
            self.config = config
            self.task_status_map = {}
            self.provenance_chain = []
        async def run_tests(self, payload: TaskPayload) -> TaskResult:
            print(f"MockRunner: Running tests for {payload.task_id}")
            await asyncio.sleep(0.5)
            if payload.dry_run:
                return TaskResult(task_id=payload.task_id, status="completed", results={"dry_run_result": True, "pass_rate": 1.0, "coverage_percentage": 0.9}, started_at=time.time(), finished_at=time.time())
            if "fail_me" in payload.task_id:
                raise TestExecutionError("Simulated failure", task_id=payload.task_id)
            return TaskResult(task_id=payload.task_id, status="completed", results={"pass_rate": 0.8, "coverage_percentage": 0.7}, started_at=time.time(), finished_at=time.time())
        async def enqueue(self, payload: TaskPayload) -> TaskResult:
            print(f"MockRunner: Enqueuing task {payload.task_id}")
            self.task_status_map[payload.task_id] = TaskResult(task_id=payload.task_id, status="enqueued", started_at=time.time())
            await asyncio.sleep(0.1)
            return self.task_status_map[payload.task_id]
        def get_task_queue_snapshot(self) -> List[Dict[str, Any]]:
            return [{"task_id": k, "status": v.status, "description": "Mock Task"} for k, v in self.task_status_map.items()]
        def get_coverage_data(self) -> Any: # Returns CoverageReportSchema in real scenario
            class MockCoverageData:
                coverage_details = {"file.py": {"percentage": 75.0, "lines_covered": 75, "lines_total": 100}}
            return MockCoverageData()
        @property
        def provenance_chain(self):
            return [{"data": {"stage_name": "Mock Stage", "result_summary": {"status": "success"}}, "hash": "abc", "prev_hash": "def", "timestamp_utc": "2025-07-31T12:00:00Z"}]
        
    # Mock MetricsExporter
    class MockMetricsExporter:
        def __init__(self, config):
            print("MockMetricsExporter initialized.")
        async def export_all_periodically(self):
            print("MockMetricsExporter: Periodically exporting metrics...")
            while True:
                await asyncio.sleep(self.config.metrics_interval_seconds)
        async def shutdown(self):
            print("MockMetricsExporter shutdown.")

    # Patch modules if they are not fully available for standalone run
    try:
        from runner.contracts import TaskPayload, TaskResult # Attempt real import
    except ImportError:
        print("runner.contracts not found. Using dummy TaskPayload/TaskResult.")
        # Minimal dummy classes if contracts.py is not available
        class TaskPayload(dict):
            def __init__(self, **kwargs): super().__init__(**kwargs); self.__dict__ = self
        class TaskResult(dict):
            def __init__(self, **kwargs): super().__init__(**kwargs); self.__dict__ = self

    try:
        from runner.errors import RunnerError, TestExecutionError, TimeoutError # Attempt real import
    except ImportError:
        print("runner.errors not found. Using dummy errors.")
        class RunnerError(Exception):
            def __init__(self, message, **kwargs): self.detail = message; self.error_code = 'DUMMY'; self.__dict__.update(kwargs); super().__init__(message)
            def as_dict(self): return {'detail': self.detail, 'error_code': self.error_code}
        class TestExecutionError(RunnerError): pass
        class TimeoutError(RunnerError): pass

    # Ensure output/docs directory exists for testing documentation loading
    Path('output/docs').mkdir(parents=True, exist_ok=True)
    (Path('output/docs') / "project_doc.md").write_text("# Initial Project Documentation\n\nThis is a placeholder document.", encoding='utf-8')
    
    config_file = Path('config.yaml')
    if not config_file.exists():
        config_file.write_text("""
version: 4
backend: docker
framework: pytest
parallel_workers: 1
timeout: 300
mutation: false
fuzz: false
distributed: false
log_sinks:
  - type: stream
    config: {}
real_time_log_streaming: true
user_subscription_level: free
instance_id: tui_dev_instance
metrics_interval_seconds: 5
""")
        print(f"Created a dummy config.yaml at {config_file.resolve()}")
    else:
        print(f"Using existing config.yaml at {config_file.resolve()}")

    IS_PRODUCTION_MODE = os.environ.get("RUNNER_ENV", "development").lower() == "production"
    print(f"Starting in {'PRODUCTION' if IS_PRODUCTION_MODE else 'DEVELOPMENT'} mode.")

    try:
        config_for_logging = load_config(str(config_file))
        configure_logging_from_config(config_for_logging)
    except Exception as e:
        print(f"Error configuring logging: {e}", file=sys.stderr)
        logging.basicConfig(level=logging.INFO)
    
    # Patch the real Runner and MetricsExporter with mocks for standalone app.py execution
    with patch('runner.core.Runner', new=MockRunner), \
         patch('runner.metrics.MetricsExporter', new=MockMetricsExporter):
        app = RunnerApp(production_mode=IS_PRODUCTION_MODE)
        app.run()