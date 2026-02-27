# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
generator/tui_stubs.py

Single source of truth for Textual TUI widget stubs and the shared TuiLogHandler.

When the ``textual`` library (and/or ``aiohttp``) is not installed, this module
provides minimal no-op stub classes for every widget/type that the generator
package imports, so that the rest of the code can still be *imported* without
error.

Usage::

    from generator.tui_stubs import (
        _TEXTUAL_AVAILABLE,
        App, Binding, Container, Horizontal, Vertical, Grid,
        Header, Footer, RichLog, DataTable, Button, Input, TextArea, Label,
        Static, Markdown, ProgressBar, Select, TabbedContent, TabPane,
        Screen, Switch, Tree, TreeNode, VerticalScroll,
        ComposeResult, reactive, Timer, on,
        NoMatches, Mount,
        TuiLogHandler,
    )
"""

import asyncio
import inspect
import logging
import re
import sys
import threading
from typing import Any, List, Optional

_TEXTUAL_AVAILABLE = False
try:
    import aiohttp  # noqa: F401  (checked so _TEXTUAL_AVAILABLE mirrors gui.py semantics)
    from textual.app import App, ComposeResult, on
    from textual.binding import Binding
    from textual.containers import Container, Grid, Horizontal, Vertical, VerticalScroll
    from textual.css.query import NoMatches
    from textual.events import Mount
    from textual.reactive import reactive
    from textual.timer import Timer
    from textual.widgets import (
        Button,
        DataTable,
        Footer,
        Header,
        Input,
        Label,
        Markdown,
        ProgressBar,
        RichLog,
        Screen,
        Select,
        Static,
        Switch,
        TabbedContent,
        TabPane,
        TextArea,
        Tree,
        TreeNode,
    )

    _TEXTUAL_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).warning(
        "Textual, aiohttp, or dependencies not found. TUI features will be unavailable."
    )

    # ---------------------------------------------------------------------------
    # Stub implementations — minimal no-op classes that mirror the public API
    # surface used by generator.main.gui and generator.runner.runner_app.
    # ---------------------------------------------------------------------------

    class App:
        """Stub for textual.app.App."""

        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            raise NotImplementedError(
                "TUI is unavailable: 'textual' package is not installed. "
                "Install it with: pip install textual"
            )

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

    class ComposeResult:
        """Stub for textual.app.ComposeResult."""

        pass

    class Binding:
        """Stub for textual.binding.Binding."""

        def __init__(self, *args, **kwargs):
            pass

    class Container:
        """Stub for textual.containers.Container."""

        pass

    class Horizontal(Container):
        """Stub for textual.containers.Horizontal."""

        pass

    class Vertical(Container):
        """Stub for textual.containers.Vertical."""

        pass

    class Grid(Container):
        """Stub for textual.containers.Grid."""

        pass

    class VerticalScroll(Container):
        """Stub for textual.containers.VerticalScroll."""

        pass

    class Header:
        """Stub for textual.widgets.Header."""

        pass

    class Footer:
        """Stub for textual.widgets.Footer."""

        pass

    class Screen:
        """Stub for textual.widgets.Screen."""

        pass

    class RichLog:
        """Stub for textual.widgets.RichLog."""

        def __init__(self, *args, **kwargs):
            pass

        def write(self, *args, **kwargs):
            pass  # Note: NOT async in the real widget either

        def clear(self, *args, **kwargs):
            pass

    class DataTable:
        """Stub for textual.widgets.DataTable."""

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
        """Stub for textual.widgets.Button."""

        # Nested Pressed event class for @on(Button.Pressed, ...) decorators
        class Pressed:
            def __init__(self, button=None, *args, **kwargs):
                self.button = button

    class Input:
        """Stub for textual.widgets.Input."""

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
        """Stub for textual.widgets.TextArea."""

        def __init__(self, *args, **kwargs):
            pass

        @property
        def text(self):
            return ""

        @text.setter
        def text(self, val):
            pass

    class Label:
        """Stub for textual.widgets.Label."""

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
        """Stub for textual.widgets.ProgressBar."""

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
        """Stub for textual.widgets.TabbedContent."""

        def __init__(self, *args, **kwargs):
            pass

        @property
        def active(self):
            return ""

        @active.setter
        def active(self, val):
            pass

    class TabPane:
        """Stub for textual.widgets.TabPane."""

        pass

    class Static:
        """Stub for textual.widgets.Static."""

        def __init__(self, *args, **kwargs):
            pass

        def update(self, *args, **kwargs):
            pass

    class Markdown:
        """Stub for textual.widgets.Markdown."""

        def __init__(self, *args, **kwargs):
            pass

        def update(self, *args, **kwargs):
            pass

    class Select:
        """Stub for textual.widgets.Select."""

        def __init__(self, *args, **kwargs):
            pass

        # Nested Changed event class for @on(Select.Changed, ...) decorators
        class Changed:
            def __init__(self, select=None, value=None, *args, **kwargs):
                self.value = value
                self.select = select

    class Switch:
        """Stub for textual.widgets.Switch."""

        def __init__(self, *args, **kwargs):
            pass

        # Nested Changed event class for @on(Switch.Changed, ...) decorators
        class Changed:
            def __init__(self, switch=None, value=None, *args, **kwargs):
                self.value = value
                self.switch = switch

    class Tree:
        """Stub for textual.widgets.Tree."""

        def __init__(self, *args, **kwargs):
            pass

    class TreeNode:
        """Stub for textual.widgets.TreeNode."""

        def __init__(self, *args, **kwargs):
            pass

    class Mount:
        """Stub for textual.events.Mount."""

        pass

    class NoMatches(Exception):
        """Stub for textual.css.query.NoMatches."""

        pass

    class Timer:
        """Stub for textual.timer.Timer."""

        def __init__(self, *args, **kwargs):
            pass

        def stop(self, *args, **kwargs):
            pass

    def reactive(initial=None, **_kwargs):
        """Stub for textual.reactive.reactive — returns the initial value as-is."""
        return initial

    def on(*args, **kwargs):
        """Stub for textual.app.on decorator — passes through the decorated function."""

        def decorator(func):
            return func

        return decorator


# ---------------------------------------------------------------------------
# Shared TuiLogHandler
# ---------------------------------------------------------------------------


class TuiLogHandler(logging.Handler):
    """
    A logging handler that writes formatted log records to a Textual RichLog widget.

    Supports two usage patterns:

    1. **With an app reference** (``generator.main.gui`` pattern):
       ``TuiLogHandler(log_widget, app=app_instance)``
       Uses an asyncio Queue and ``app.call_from_thread`` / ``app.create_task``
       for thread-safe delivery to the Textual event loop.

    2. **Without an app reference** (``generator.runner.runner_app`` pattern):
       ``TuiLogHandler(log_widget)``
       Writes directly to the widget, handling sync and async ``write()``
       implementations transparently.

    An optional *log_history* list can be passed; when set, each formatted and
    redacted message is appended as ``{"message": msg}`` — matching the
    ``LOG_HISTORY`` behaviour in ``runner_app.py``.
    """

    def __init__(
        self,
        log_widget: Any,
        app: Any = None,
        log_history: Optional[List] = None,
    ):
        super().__init__()
        self.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        self.log_widget = log_widget
        self.app = app
        self.log_history = log_history
        # Queue-based attributes used when app is provided (gui.py pattern)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.worker_task: Optional[Any] = None
        self._lock: asyncio.Lock = asyncio.Lock()
        # Loop reference used for direct-write path (runner_app.py pattern)
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    @staticmethod
    def _redact(message: str) -> str:
        """Redact OpenAI-style API keys from log messages."""
        return re.sub(r"sk-[A-Za-z0-9]{3,}", "[REDACTED]", message)

    async def _process_queue(self) -> None:
        """Drain the queue, writing each record to the log widget."""
        while True:
            try:
                record = await self.queue.get()
                formatted = self.format(record)
                self.log_widget.write(formatted)
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"TuiLogHandler error: {e}", file=sys.stderr)

    async def _flush_queue(self) -> None:
        """Flush any remaining records in the queue."""
        async with self._lock:
            while not self.queue.empty():
                try:
                    record = await self.queue.get()
                    formatted = self.format(record)
                    self.log_widget.write(formatted)
                    self.queue.task_done()
                except Exception as e:
                    print(f"TuiLogHandler flush error: {e}", file=sys.stderr)

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record, routing via the appropriate delivery strategy."""
        # Suppress noisy textual internal logs
        if record.name == "textual":
            return

        try:
            raw = self.format(record)
            msg = self._redact(raw)

            # Optionally append to an external log-history list
            if self.log_history is not None:
                self.log_history.append({"message": msg})

            if self.app is not None:
                # --- Queue-based, thread-safe path (gui.py usage) ---
                is_app_thread = False
                if _TEXTUAL_AVAILABLE and hasattr(self.app, "_thread_id"):
                    is_app_thread = (
                        self.app._thread_id == threading.get_ident()
                    )

                if _TEXTUAL_AVAILABLE and not is_app_thread:
                    if hasattr(self.app, "call_from_thread"):
                        self.app.call_from_thread(self.queue.put_nowait, record)
                    else:
                        self.queue.put_nowait(record)
                else:
                    self.queue.put_nowait(record)

                if self.worker_task is None or self.worker_task.done():
                    try:
                        if (
                            hasattr(self.app, "_loop")
                            and self.app._loop
                            and not self.app._loop.is_closed()
                        ):
                            self.worker_task = self.app.create_task(
                                self._process_queue()
                            )
                        elif hasattr(self.app, "create_task"):
                            self.worker_task = self.app.create_task(
                                self._process_queue()
                            )
                    except Exception:
                        pass
            else:
                # --- Direct-write path (runner_app.py usage) ---
                write = getattr(self.log_widget, "write", None)
                if not write:
                    return

                if not self._loop:
                    try:
                        self._loop = asyncio.get_event_loop()
                    except RuntimeError:
                        self._loop = None

                result = write(msg)

                if inspect.isawaitable(result):
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(result, self._loop)
                    else:
                        asyncio.run(result)

        except Exception:
            # Absolute last resort — never crash the logging system
            try:
                msg_to_write = raw if "raw" in locals() else record.getMessage()
                result = self.log_widget.write(msg_to_write)  # type: ignore
                if inspect.isawaitable(result):
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(result, self._loop)
                    else:
                        asyncio.run(result)
            except Exception:
                print(record.getMessage(), file=sys.stderr)

    def close(self) -> None:
        """Cancel the queue worker and flush remaining records."""
        if self.worker_task:
            self.worker_task.cancel()
            self.worker_task = None
        try:
            if self.app is not None and hasattr(self.app, "create_task"):
                self.app.create_task(self._flush_queue())
        except RuntimeError:
            pass
        super().close()


__all__ = [
    "_TEXTUAL_AVAILABLE",
    "App",
    "Binding",
    "Button",
    "ComposeResult",
    "Container",
    "DataTable",
    "Footer",
    "Grid",
    "Header",
    "Horizontal",
    "Input",
    "Label",
    "Markdown",
    "Mount",
    "NoMatches",
    "ProgressBar",
    "RichLog",
    "Screen",
    "Select",
    "Static",
    "Switch",
    "TabbedContent",
    "TabPane",
    "TextArea",
    "Timer",
    "Tree",
    "TreeNode",
    "TuiLogHandler",
    "Vertical",
    "VerticalScroll",
    "on",
    "reactive",
]
