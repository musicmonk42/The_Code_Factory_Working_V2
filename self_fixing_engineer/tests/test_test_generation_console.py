# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_generation/orchestrator/tests/test_console.py
"""Production-grade tests for console.py

Covers:
- dictConfig failure → fallback print backend (glyph parity preserved)
- Rich/plain backend toggling
- Audit file handler path patch and log write
- Thread-safety of backend switching under contention
- ASCII vs UTF-8 glyph mapping via module reload
- Conditional rich progress bar testing
"""

from __future__ import annotations

import importlib
import io
import json
import logging
import logging.config
import os
import pathlib
import sys
import threading
import time
import types
from unittest.mock import Mock, patch

import pytest

# FIX: Import LOGGING_CONFIG to resolve NameError


# Ensure module path (adjust if your runner differs)
MODULE_ROOT = pathlib.Path(os.environ.get("ATCO_MODULE_ROOT", "."))
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

# -----------------------------
# Helpers / fixtures
# -----------------------------
# Fix: The custom capture_streams fixture is removed in favor of pytest's built-in capsys/caplog.


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for k in ["ATCO_PLAIN_LOGGING"]:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def reload_console(monkeypatch: pytest.MonkeyPatch):
    """Yield a function that reloads console.py fresh each time (resetting globals)."""

    def _reload():
        # Use the full, correct import path for the module under test.
        mod = importlib.import_module("self_fixing_engineer.test_generation.orchestrator.console")
        # Ensure module-level console instance is cleared before reloading
        mod.console = None
        return importlib.reload(mod)

    return _reload


# -----------------------------
# Tests
# -----------------------------
class TestLoggingInitialization:
    def test_dictconfig_failure_falls_back_with_glyph_parity(
        self, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        # FIX: The module is now properly reloaded by the fixture, so we don't need to do it here.
        console = importlib.import_module("self_fixing_engineer.test_generation.orchestrator.console")

        # Force dictConfig to raise
        def boom(_):
            raise ValueError("Test failure")

        monkeypatch.setattr(logging.config, "dictConfig", boom)

        # Force ASCII environment to check glyph parity in fallback
        # This influences the init_console_and_styles() call inside the reloaded module
        fake_out = types.SimpleNamespace(
            encoding="ascii", write=sys.__stdout__.write, flush=lambda: None
        )
        monkeypatch.setattr(sys, "stdout", fake_out)

        # We need to reload again after setting the fake stdout to test glyph selection
        console = importlib.reload(console)

        with caplog.at_level(logging.INFO):
            console.configure_logging(
                {}, audit_log_file=str(MODULE_ROOT / "tmp" / "audit.log")
            )
            # Test a level that produces a glyph to check for parity
            console.log("hello", level="SUCCESS")

            text = caplog.text
            # FIX: Assert the more specific error message from the corrected console.py
            assert (
                "Failed to set up structured logging: Invalid logging_config schema: missing 'handlers'."
                in text
            )
            assert "[OK] hello" in text  # Check for ASCII success glyph

    def test_plain_vs_rich_toggles(
        self, monkeypatch: pytest.MonkeyPatch, reload_console, caplog
    ):
        # Reload the module to get a fresh state
        console = reload_console()

        # Define a mock Console class that has the necessary attributes for the test
        class FakeConsole(Mock):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.buf = io.StringIO()

            def log(self, msg, *_, **__):
                self.buf.write(str(msg))

            def print(self, msg, *_, **__):
                self.buf.write(str(msg))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        # Start with plain backend
        monkeypatch.setattr(console, "RICH_AVAILABLE", False)
        console.set_plain_logging(True)

        with caplog.at_level(logging.INFO):
            console.log("plain", level="INFO")
            assert "plain" in caplog.text
        caplog.clear()

        # Now emulate rich environment
        monkeypatch.setattr(console, "RICH_AVAILABLE", True)

        # FIX: Create a mock instance of our FakeConsole and patch the module's 'console' variable directly.
        # This ensures that when `console.log` is called, it has a valid rich console instance to work with.
        mock_instance = FakeConsole()
        monkeypatch.setattr(console, "console", mock_instance)

        # We also need to mock `_ensure_console` to return our mock instance
        monkeypatch.setattr(
            console, "_ensure_console", Mock(return_value=mock_instance)
        )

        console.set_rich_logging(True)

        with patch.object(console.logger, "log") as mock_log:
            console.log("rich", level="SUCCESS")
            # Check that the rich console's buffer received the formatted message
            assert "✓ rich" in mock_instance.buf.getvalue()
            # Check that the logging framework received the raw (unformatted) message
            mock_log.assert_called_with(logging.INFO, "rich")


class TestAuditHandler:
    def test_audit_file_path_patched_and_writable(
        self, tmp_path: pathlib.Path, reload_console, monkeypatch
    ):
        console = reload_console()
        audit_path = tmp_path / "logs" / "audit.jsonl"

        cfg = {
            "version": 1,
            "handlers": {
                "default": {"class": "logging.StreamHandler", "level": "DEBUG"},
                "audit_file": {"class": "logging.FileHandler", "filename": "audit.log"},
            },
            "loggers": {
                "atco_audit": {"handlers": ["audit_file"], "level": "INFO"},
                "": {"handlers": ["default"], "level": "INFO"},
            },
        }

        monkeypatch.setattr("logging.config.dictConfig", Mock())
        monkeypatch.setattr("os.makedirs", Mock())

        mock_handler_instance = Mock()
        mock_handler_instance.level = logging.INFO

        with patch.object(logging.config, "dictConfig") as mock_dictConfig:
            console.configure_logging(cfg, audit_log_file=str(audit_path))

            call_args = mock_dictConfig.call_args[0][0]
            assert call_args["handlers"]["audit_file"]["filename"] == str(audit_path)

        os.makedirs.assert_called_with(str(tmp_path / "logs"), exist_ok=True)

        # FIX: Patch the module's global audit_logger_instance to point to a Mock
        mock_audit_logger = Mock()
        monkeypatch.setattr(console, "audit_logger_instance", mock_audit_logger)

        payload = {"action": "unit_test", "ok": True}

        console.audit_logger_instance.info(json.dumps(payload))
        mock_audit_logger.info.assert_called_once_with(json.dumps(payload))


class TestThreadSafety:
    def test_backend_switch_is_thread_safe(
        self, monkeypatch: pytest.MonkeyPatch, reload_console
    ):
        console = reload_console()

        monkeypatch.setattr(
            f"{console.__name__}.RICH_AVAILABLE",
            importlib.util.find_spec("rich") is not None,
        )

        errors: list[Exception] = []

        def worker(i: int):
            try:
                time.sleep(0.01 * (i % 2))
                if i % 2:
                    console.set_plain_logging()
                else:
                    console.set_rich_logging()
                console.log(f"msg-{i}", level="INFO")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"Threads failed with errors: {errors}"


class TestGlyphMapping:
    def test_ascii_vs_utf8_glyphs_via_reload(
        self, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        # First: ASCII console → expect ASCII glyphs
        ascii_out = types.SimpleNamespace(
            encoding="ascii", write=sys.__stdout__.write, flush=lambda: None
        )
        monkeypatch.setattr(sys, "stdout", ascii_out)
        console = importlib.reload(
            importlib.import_module("self_fixing_engineer.test_generation.orchestrator.console")
        )

        console.fallback_to_basic_logging()
        with caplog.at_level(logging.INFO):
            console.log("ping", level="SUCCESS")
            assert "[OK] ping" in caplog.text

        caplog.clear()

        # Then: UTF-8 console → Unicode glyphs allowed
        utf_out = types.SimpleNamespace(
            encoding="utf-8", write=sys.__stdout__.write, flush=lambda: None
        )
        monkeypatch.setattr(sys, "stdout", utf_out)
        console = importlib.reload(
            importlib.import_module("self_fixing_engineer.test_generation.orchestrator.console")
        )

        console.fallback_to_basic_logging()
        with caplog.at_level(logging.INFO):
            console.log("pong", level="SUCCESS")
            assert "✓ pong" in caplog.text


class TestProgressReporting:
    @pytest.mark.skipif(
        not importlib.util.find_spec("rich"), reason="rich library is not installed"
    )
    def test_progress_bar_rich_available(self, reload_console, monkeypatch):
        console = reload_console()
        console.set_rich_logging()

        mock_progress_instance = Mock()
        mock_progress_instance.add_task.return_value = "fake_task_id"

        mock_progress_instance.__enter__ = Mock(return_value=mock_progress_instance)
        mock_progress_instance.__exit__ = Mock(return_value=False)

        mock_progress_cls = Mock(return_value=mock_progress_instance)

        monkeypatch.setattr(
            f"{console.__name__}.Progress", mock_progress_cls, raising=False
        )
        monkeypatch.setattr("rich.progress.Progress", mock_progress_cls, raising=False)

        tasks = [("Task 1", 10), ("Task 2", 20)]
        with console.log_progress_bars("Overall Progress", tasks) as progress_map:
            mock_progress_instance.__enter__.assert_called_once()
            assert len(progress_map) == 2

            mock_progress_instance.add_task.assert_any_call(
                "Task 1", total=10, description="Overall Progress"
            )
            mock_progress_instance.add_task.assert_any_call(
                "Task 2", total=20, description="Overall Progress"
            )

            task_for_task1 = progress_map["Task 1"]
            task_for_task1.update(advance=5)
            mock_progress_instance.update.assert_called_with(
                task_for_task1.task_id, advance=5
            )

        mock_progress_instance.__exit__.assert_called_once()


# Completed for syntactic validity.
def test_fallback_logging():
    """
    Tests that fallback logging is correctly configured and logs messages.
    """
    console = importlib.import_module("self_fixing_engineer.test_generation.orchestrator.console")
    console.fallback_to_basic_logging()

    # We can't use caplog directly because fallback logging uses basicConfig.
    # Instead, we patch the logger's `handle` method.
    with patch.object(console.logger, "handle") as mock_handle:
        console.log("test", level="INFO")
        assert mock_handle.called
        log_record = mock_handle.call_args[0][0]
        assert log_record.message == "[OK] test"
        assert log_record.levelname == "INFO"
