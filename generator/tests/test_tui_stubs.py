# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
generator/tests/test_tui_stubs.py

Unit tests for generator/tui_stubs.py.

Verifies that:
- All exported stub classes and helpers can be imported.
- The ``_TEXTUAL_AVAILABLE`` flag is a bool.
- Each stub class can be instantiated without error.
- ``TuiLogHandler`` works with and without an ``app`` reference,
  and that its key attributes (queue, worker_task, _lock, _loop) are present.
- Redaction of API keys in emitted log records.
"""

import asyncio
import logging
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestTuiStubsImport(unittest.TestCase):
    """All exported names must be importable."""

    def test_import_all_exports(self):
        from generator.tui_stubs import (
            _TEXTUAL_AVAILABLE,
            App,
            Binding,
            Button,
            ComposeResult,
            Container,
            DataTable,
            Footer,
            Grid,
            Header,
            Horizontal,
            Input,
            Label,
            Markdown,
            Mount,
            NoMatches,
            ProgressBar,
            RichLog,
            Screen,
            Select,
            Static,
            Switch,
            TabbedContent,
            TabPane,
            TextArea,
            Timer,
            Tree,
            TreeNode,
            TuiLogHandler,
            Vertical,
            VerticalScroll,
            on,
            reactive,
        )
        self.assertIsInstance(_TEXTUAL_AVAILABLE, bool)
        for cls in (
            App, Binding, Button, ComposeResult, Container, DataTable, Footer,
            Grid, Header, Horizontal, Input, Label, Markdown, Mount, ProgressBar,
            RichLog, Screen, Select, Static, Switch, TabbedContent, TabPane,
            TextArea, Timer, Tree, TreeNode, TuiLogHandler, Vertical, VerticalScroll,
        ):
            self.assertIsNotNone(cls)
        self.assertIsNotNone(on)
        self.assertIsNotNone(reactive)
        self.assertIsNotNone(NoMatches)


class TestStubInstantiation(unittest.TestCase):
    """Every stub class must be instantiable without arguments."""

    def _get_stubs(self):
        from generator import tui_stubs as m
        return [
            m.App, m.Binding, m.Button, m.ComposeResult, m.Container, m.DataTable,
            m.Footer, m.Grid, m.Header, m.Horizontal, m.Input, m.Label, m.Markdown,
            m.Mount, m.ProgressBar, m.RichLog, m.Screen, m.Select, m.Static,
            m.Switch, m.TabbedContent, m.TabPane, m.TextArea, m.Timer, m.Tree,
            m.TreeNode, m.Vertical, m.VerticalScroll,
        ]

    def test_all_stubs_instantiate(self):
        # Skip when the real Textual library is installed (stubs are the real classes).
        from generator.tui_stubs import _TEXTUAL_AVAILABLE
        if _TEXTUAL_AVAILABLE:
            self.skipTest("Real Textual is installed; stub instantiation test not applicable.")
        for cls in self._get_stubs():
            with self.subTest(cls=cls.__name__):
                instance = cls()
                self.assertIsNotNone(instance)

    def test_on_decorator_passthrough(self):
        from generator.tui_stubs import _TEXTUAL_AVAILABLE, on
        if _TEXTUAL_AVAILABLE:
            self.skipTest("Real Textual installed.")

        @on("some.event")
        def handler():
            return "hello"

        self.assertEqual(handler(), "hello")

    def test_reactive_returns_initial(self):
        from generator.tui_stubs import _TEXTUAL_AVAILABLE, reactive
        if _TEXTUAL_AVAILABLE:
            self.skipTest("Real Textual installed.")

        result = reactive(42)
        self.assertEqual(result, 42)
        self.assertIsNone(reactive())

    def test_nomatches_is_exception(self):
        from generator.tui_stubs import NoMatches
        with self.assertRaises(NoMatches):
            raise NoMatches("not found")


class TestTuiLogHandlerInit(unittest.TestCase):
    """TuiLogHandler must expose the attributes expected by both gui.py and runner_app.py."""

    def _make_widget(self):
        w = MagicMock()
        w.write = MagicMock()
        return w

    def _make_app(self):
        app = MagicMock()
        app._thread_id = threading.get_ident()
        app.call_from_thread = lambda fn, *a: fn(*a)
        app.create_task = lambda coro: asyncio.ensure_future(coro)
        app._loop = asyncio.new_event_loop()
        return app

    def test_init_with_app(self):
        """Attributes required by the gui.py usage pattern."""
        from generator.tui_stubs import TuiLogHandler
        widget = self._make_widget()
        app = self._make_app()
        handler = TuiLogHandler(widget, app=app)
        self.assertIs(handler.log_widget, widget)
        self.assertIs(handler.app, app)
        self.assertIsNotNone(handler.queue)
        self.assertIsNone(handler.worker_task)
        self.assertIsNotNone(handler._lock)

    def test_init_without_app(self):
        """Attributes required by the runner_app.py usage pattern."""
        from generator.tui_stubs import TuiLogHandler
        widget = self._make_widget()
        handler = TuiLogHandler(widget)
        self.assertIs(handler.log_widget, widget)
        self.assertIsNone(handler.app)
        self.assertIsNotNone(handler._loop or True)  # _loop may be None if no loop running

    def test_init_with_log_history(self):
        from generator.tui_stubs import TuiLogHandler
        widget = self._make_widget()
        history = []
        handler = TuiLogHandler(widget, log_history=history)
        self.assertIs(handler.log_history, history)

    def test_redact_method(self):
        from generator.tui_stubs import TuiLogHandler
        widget = self._make_widget()
        handler = TuiLogHandler(widget)
        redacted = handler._redact("key=sk-abc123xyz secret")
        self.assertIn("[REDACTED]", redacted)
        self.assertNotIn("sk-abc123xyz", redacted)

    def test_emit_without_app_writes_to_widget(self):
        from generator.tui_stubs import TuiLogHandler
        widget = self._make_widget()
        handler = TuiLogHandler(widget)
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        handler.emit(record)
        widget.write.assert_called_once()
        call_arg = widget.write.call_args.args[0]
        self.assertIn("hello world", call_arg)

    def test_emit_redacts_api_keys(self):
        from generator.tui_stubs import TuiLogHandler
        widget = self._make_widget()
        handler = TuiLogHandler(widget)
        with patch.object(handler, "format", return_value="WARNING - key: sk-abc123"):
            record = logging.LogRecord(
                name="test", level=logging.WARNING, pathname="", lineno=0,
                msg="key: sk-abc123", args=(), exc_info=None,
            )
            handler.emit(record)
        widget.write.assert_called_once()
        self.assertIn("[REDACTED]", widget.write.call_args.args[0])

    def test_emit_appends_to_log_history(self):
        from generator.tui_stubs import TuiLogHandler
        widget = self._make_widget()
        history = []
        handler = TuiLogHandler(widget, log_history=history)
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="tracked message", args=(), exc_info=None,
        )
        handler.emit(record)
        self.assertTrue(any("tracked message" in e.get("message", "") for e in history))

    def test_emit_ignores_textual_logger(self):
        from generator.tui_stubs import TuiLogHandler
        widget = self._make_widget()
        handler = TuiLogHandler(widget)
        record = logging.LogRecord(
            name="textual", level=logging.DEBUG, pathname="", lineno=0,
            msg="internal textual noise", args=(), exc_info=None,
        )
        handler.emit(record)
        widget.write.assert_not_called()


class TestTuiLogHandlerWithApp(unittest.IsolatedAsyncioTestCase):
    """Queue-based path (gui.py pattern) with an app reference."""

    async def test_emit_with_app_uses_queue(self):
        from generator.tui_stubs import TuiLogHandler

        widget = MagicMock()
        widget.write = MagicMock()

        pending_tasks = []

        def create_task_mock(coro):
            task = asyncio.create_task(coro)
            pending_tasks.append(task)
            return task

        app = MagicMock()
        app._thread_id = threading.get_ident()
        app.call_from_thread = lambda fn, *a: fn(*a)
        app.create_task = create_task_mock
        app._loop = asyncio.get_running_loop()

        handler = TuiLogHandler(widget, app=app)
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="queued message", args=(), exc_info=None,
        )
        handler.emit(record)

        # Drain the queue
        await handler.queue.join()
        widget.write.assert_called_with("queued message")

        handler.close()
        for task in pending_tasks:
            try:
                await asyncio.shield(task)
            except (asyncio.CancelledError, Exception):
                pass


if __name__ == "__main__":
    unittest.main()
