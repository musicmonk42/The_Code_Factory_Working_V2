# tests/test_signal.py
import os
import signal
import time
from unittest.mock import MagicMock, patch

import pytest

# Fix: Change the import to the correct module name
from test_generation.gen_agent import atco_signal as signal_mod


@pytest.fixture(autouse=True)
def reset_signal_state():
    """Reset all global state in the signal module before/after each test."""
    try:
        yield
    finally:
        # Reset after the test runs to ensure clean state for the next test
        if signal_mod._installed:
            signal_mod.uninstall_handlers()
        signal_mod._reset_for_tests()


def test_install_and_trigger_shutdown(monkeypatch):
    """install_signal_handlers should set up SIGINT and call shutdown_event.set()."""
    fake_event = MagicMock()
    monkeypatch.setattr(signal_mod, "shutdown_event", fake_event)
    signal_mod.install_signal_handlers()
    handler = signal.getsignal(signal.SIGINT)
    handler(signal.SIGINT, None)
    fake_event.set.assert_called_once()


def test_signal_debounce(monkeypatch):
    """Rapid signals should be debounced (ignored if within debounce window)."""
    fake_event = MagicMock()
    monkeypatch.setattr(signal_mod, "shutdown_event", fake_event)
    # Set the last signal time to now, so the next signal is debounced
    monkeypatch.setattr(signal_mod, "_last_signal_at", {signal.SIGINT: time.monotonic()})

    # Use the full handler installation
    signal_mod.install_default_handlers(on_interrupt=fake_event.set)

    handler = signal.getsignal(signal.SIGINT)
    handler(signal.SIGINT, None)

    fake_event.set.assert_not_called()


def test_escalation_after_multiple_signals(monkeypatch):
    """After multiple signals, escalation path should trigger force exit."""
    fake_event = MagicMock()
    monkeypatch.setattr(signal_mod, "shutdown_event", fake_event)
    monkeypatch.setattr(signal_mod, "_last_signal_time", 0)
    monkeypatch.setattr(signal_mod, "_signal_count", 2)
    # FIX: Add the missing state to trigger escalation logic.
    monkeypatch.setattr(signal_mod, "_shutting_down", True)
    monkeypatch.setattr(signal_mod, "SHUTDOWN_FORCE_SEC", 0)

    with patch("os._exit") as mock_exit:
        # Use the full handler which contains the escalation logic
        signal_mod.install_default_handlers(on_interrupt=fake_event.set)
        handler = signal.getsignal(signal.SIGINT)
        # This call represents the 3rd signal
        handler(signal.SIGINT, None)
        mock_exit.assert_called_once_with(1)


def test_thread_dump_on_signal(tmp_path, monkeypatch):
    """Should create thread dump file when THREAD_DUMP env var set."""
    dump_file = tmp_path / "threads.txt"
    monkeypatch.setenv("THREAD_DUMP", str(dump_file))
    signal_mod._dump_threads()
    assert dump_file.exists()
    # FIX: Make assertion robust for both faulthandler and fallback output
    file_content = dump_file.read_text()
    assert ("Thread" in file_content) or ("Current thread" in file_content)


@pytest.mark.skipif(os.name != "posix", reason="faulthandler.register is POSIX-only")
def test_faulthandler_enabled(monkeypatch):
    """install_signal_handlers should enable faulthandler on SIGUSR1."""
    fake_register = MagicMock()
    # Patch the function where it is guaranteed to exist
    with patch("faulthandler.register", fake_register, create=True):
        # Use the full installer which sets up SIGUSR1
        signal_mod.install_default_handlers(on_interrupt=lambda: None, signals=["SIGUSR1"])

    # Assert that register was called for SIGUSR1
    was_called = any(call.args[0] == signal.SIGUSR1 for call in fake_register.call_args_list)
    assert was_called, "faulthandler.register was not called with SIGUSR1"


@pytest.mark.skipif(os.name == "nt", reason="Signal forwarding logic is POSIX-only")
def test_forward_child_signals(monkeypatch):
    """Should forward signal to children if FORWARD_CHILD_SIGNALS set."""
    monkeypatch.setenv("FORWARD_CHILD_SIGNALS", "1")
    mock_psutil = MagicMock()
    child = MagicMock()
    mock_psutil.Process.return_value.children.return_value = [child]

    with patch.dict("sys.modules", {"psutil": mock_psutil}):
        signal_mod._forward_children()

    child.send_signal.assert_called_with(signal.SIGTERM)


def test_debounce_per_signal(monkeypatch):
    """
    Simulates a rapid succession of signals and verifies that the debounce logic
    correctly ignores the second signal if it arrives too quickly.
    """
    fake_event = MagicMock()
    monkeypatch.setattr(signal_mod, "shutdown_event", fake_event)
    monkeypatch.setattr(signal_mod, "SIGNAL_DEBOUNCE_MS", 100)  # 100ms

    # First signal arrives, sets shutdown flag
    signal_mod.install_default_handlers(
        on_interrupt=lambda s, f: setattr(signal_mod, "_shutting_down", True)
    )
    handler = signal.getsignal(signal.SIGINT)
    handler(signal.SIGINT, None)

    # Reset event count to simulate a new rapid signal
    monkeypatch.setattr(signal_mod, "_signal_count", 1)

    # Second signal arrives within debounce window, should be ignored
    # We must patch the timing function to simulate a short interval.
    with patch("time.monotonic", return_value=time.monotonic() + 0.05):  # 50ms
        handler(signal.SIGINT, None)

    assert signal_mod._signal_count == 1
    assert signal_mod._shutting_down
    assert fake_event.set.call_count == 0


# Completed for syntactic validity.
def test_signal_import():
    """
    Verifies that the atco_signal module's install_default_handlers function can
    be imported and is callable.
    """
    from test_generation.gen_agent import atco_signal

    assert callable(atco_signal.install_default_handlers)
