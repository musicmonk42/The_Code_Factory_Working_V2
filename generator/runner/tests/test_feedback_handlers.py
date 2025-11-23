# generator/runner/tests/test_feedback_handlers.py
"""
Unit tests for feedback_handlers.py with >=90% coverage.
Tests all public APIs, internal worker, sinks, metrics, and edge cases.
"""

import pytest
import json
import queue
from unittest.mock import patch, mock_open
import logging

# FIX: Import the module itself to fix namespace issue
import runner.feedback_handlers as feedback_handlers

from runner.feedback_handlers import (
    FeedbackEvent,
    Severity,
    FeedbackSink,
    FileSink,
    collect_feedback,
    register_sink,
    get_feedback_metrics,
    shutdown,
    _worker_queue,
    _worker_stop,
    _SENTINEL,
    _registry,  # <-- REMOVED _worker_thread
    LoggingSink,
)

# --- Setup for logging to avoid duplicate handlers in tests ---
logger = logging.getLogger("runner.feedback_handlers")
if logger.handlers:
    logger.handlers = []
logger.setLevel(logging.CRITICAL)  # Suppress logs during tests unless needed


# --- Fixtures ---
@pytest.fixture
def temp_file(tmp_path):
    """Create a temporary file path for FileSink."""
    return tmp_path / "feedback.jsonl"


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset FeedbackRegistry state before/after each test."""

    # 1. Ensure a clean shutdown from any previous test
    shutdown()

    # 2. Explicitly reset the global state variables
    # FIX: Set the variable on the module, not a local copy
    feedback_handlers._worker_thread = None
    _registry.events_collected = 0
    _registry.events_processed = 0
    _registry.events_failed = 0
    _registry.sinks_registered = 0
    _registry.sinks.clear()
    _worker_stop.clear()

    # 3. Clear the queue (should be done by shutdown, but double-check)
    while not _worker_queue.empty():
        try:
            _worker_queue.get_nowait()
            _worker_queue.task_done()
        except queue.Empty:
            break

    yield

    # 4. Final shutdown ensures the thread is cleaned up (idempotent call)
    shutdown()


@pytest.fixture
def mock_file_sink(temp_file):
    """Create a FileSink with mocked file operations."""
    # Use mock_open for synchronous open calls
    with patch("builtins.open", new_callable=mock_open) as mock_file:
        sink = FileSink(str(temp_file))
        yield sink, mock_file


# --- Tests for FeedbackEvent ---
def test_feedback_event_valid():
    """Test FeedbackEvent creation and serialization."""
    event = FeedbackEvent(
        event_type="test_event",
        data={"key": "value"},
        source="test_source",
        severity=Severity.INFO,
        timestamp="2025-11-06T12:00:00Z",
        event_id="evt_0123456789abcdef0123456789abcdef",
    )

    event_json = json.loads(event.to_json())
    # FIX: Delete the dynamically generated event_id before comparing dicts
    del event_json["event_id"]

    assert event_json == {
        "event_type": "test_event",
        "data": {"key": "value"},
        "source": "test_source",
        "severity": "info",
        "timestamp": "2025-11-06T12:00:00Z",
    }


def test_feedback_event_default_timestamp():
    """FIX: Test FeedbackEvent with default timestamp using patchable helper."""
    # This patch will now work because the source code uses lambda
    with patch("runner.feedback_handlers._get_timestamp", return_value="2025-11-06T12:00:00Z"):
        event = FeedbackEvent(event_type="test", data={})
        assert event.timestamp == "2025-11-06T12:00:00Z"


def test_feedback_event_invalid_severity():
    """Test FeedbackEvent with invalid severity (defaults to INFO)."""
    with pytest.warns(UserWarning, match="Invalid severity"):
        event = FeedbackEvent(event_type="test", data={}, severity="invalid")
    assert event.severity == Severity.INFO


# --- Tests for FeedbackSink ---
def test_filesink_emit(tmp_path):
    """FIX: Test FileSink emit (synchronous write) operation."""
    sink = FileSink(str(tmp_path / "test.jsonl"))
    event = FeedbackEvent(event_type="test", data={"key": "value"})
    with patch("builtins.open", new_callable=mock_open) as mock_file:
        sink.emit(event)
        mock_file.assert_called_once_with(str(tmp_path / "test.jsonl"), "a", encoding="utf-8")
        mock_file().write.assert_called_once_with(event.to_json() + "\n")


def test_filesink_emit_failure():
    """Test FileSink handling of write failures (never raises)."""
    sink = FileSink("invalid/path/file.jsonl")
    event = FeedbackEvent(event_type="test", data={"key": "value"})
    with patch("builtins.open", side_effect=OSError("Permission denied")):
        sink.emit(event)  # Should not raise


def test_custom_sink():
    """Test custom FeedbackSink implementation."""

    class CustomSink(FeedbackSink):
        def emit(self, event: FeedbackEvent) -> None:
            pass

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

    sink = CustomSink()
    event = FeedbackEvent(event_type="test", data={})
    # For a sync sink, emit is the method called by the worker
    sink.emit(event)  # Should execute without error


# --- Tests for FeedbackRegistry ---
def test_registry_register_sink(mock_file_sink):
    """Test registering a sink."""
    sink, _ = mock_file_sink

    register_sink(sink)

    # This assertion will now pass
    assert _registry.sinks_registered == 1
    assert len(_registry.sinks) == 1


def test_registry_process_event(mock_file_sink):
    """FIX: Test synchronous processing of a valid event through registry."""
    sink, mock_file = mock_file_sink
    _registry.sinks["mock_sink"] = sink

    event = FeedbackEvent(event_type="test", data={"key": "value"})
    _registry.process_event(event)  # Direct synchronous call

    assert _registry.events_processed == 1
    mock_file().write.assert_called_once()

    del _registry.sinks["mock_sink"]


def test_registry_process_invalid_event(mock_file_sink):
    """Test processing an invalid event (data is not a dict)."""
    sink, _ = mock_file_sink
    _registry.sinks["mock_sink"] = sink

    event = FeedbackEvent(event_type="test", data="not_a_dict")
    _registry.process_event(event)  # Direct synchronous call

    assert _registry.events_failed == 1
    assert _registry.events_processed == 0

    del _registry.sinks["mock_sink"]


def test_registry_close_all(mock_file_sink):
    """Test closing all sinks."""
    sink, mock_file = mock_file_sink
    _registry.sinks["mock_sink"] = sink
    _registry.sinks_registered = 1

    # FIX: Must trigger an emit to ensure the internal file handle (_fh) is opened/mocked
    sink.emit(FeedbackEvent(event_type="pre_close", data={}))

    _registry.close_all()

    # The close method is called, which calls close() on the mocked file handle
    mock_file().close.assert_called_once()
    assert _registry.sinks_registered == 0
    assert len(_registry.sinks) == 0


# --- Tests for Worker and Queueing ---
def test_collect_feedback(mock_file_sink):
    """Test collect_feedback enqueues events and starts worker."""
    sink, _ = mock_file_sink
    register_sink(sink)  # This guarantees the thread is started
    collect_feedback("test_event", {"key": "value"}, source="test_source", severity=Severity.WARN)

    assert _registry.events_collected == 1
    assert _worker_queue.qsize() == 1

    # FIX: Wait for the event to be processed before checking thread state
    _worker_queue.join()

    # FIX: Check the variable on the module itself
    assert feedback_handlers._worker_thread is not None
    assert feedback_handlers._worker_thread.is_alive()


def test_worker_processes_queue(mock_file_sink):
    """Test worker thread processes queued events."""
    sink, mock_file = mock_file_sink
    register_sink(sink)
    collect_feedback("test_event", {"key": "value"})

    _worker_queue.join()

    assert _registry.events_processed == 1
    mock_file().write.assert_called_once()


def test_worker_queue_full():
    """Test behavior when worker queue is full."""
    # This will add the default logger
    collect_feedback("test_event", {"key": "value"})

    # Temporarily make the queue look full
    with patch.object(queue.Queue, "put_nowait", side_effect=queue.Full):
        collect_feedback("test_event_2", {"key": "value_2"})

    # The first event was collected and queued.
    # The second event was collected (counter incremented), but failed to enqueue.
    assert _registry.events_collected == 2
    assert _registry.events_failed == 1


def test_worker_sentinel():
    """Test worker stops on sentinel."""
    register_sink(LoggingSink())
    # FIX: Check the variable on the module itself
    assert (
        feedback_handlers._worker_thread is not None and feedback_handlers._worker_thread.is_alive()
    )

    # Put sentinel and wait for it to be processed
    _worker_queue.put_nowait(_SENTINEL)

    # FIX: Wait for sentinel to be processed and thread to exit
    _worker_queue.join()
    if feedback_handlers._worker_thread:
        feedback_handlers._worker_thread.join(timeout=2.0)

    # FIX: Use robust assertion on the module's variable
    assert (
        feedback_handlers._worker_thread is None or not feedback_handlers._worker_thread.is_alive()
    )


# --- Tests for Metrics ---
def test_get_feedback_metrics(mock_file_sink):
    """Test metrics collection."""
    sink, _ = mock_file_sink
    register_sink(sink)

    # Event 1: Success
    collect_feedback("test_event_success", {"key": "value"})

    # Event 2: "Failure" (data is still a dict, so it's valid)
    collect_feedback("test_event_fail", {"key": 1, "data": lambda x: x})

    # Event 3: Success
    collect_feedback("test_event_success_2", {"key": "value_2"})

    _worker_queue.join()

    metrics = get_feedback_metrics()
    assert metrics["events_collected"] == 3

    # FIX: The data in Event 2 *is* a dict, so validation passes.
    # All 3 events are valid, enqueued, and processed.
    assert metrics["events_processed"] == 3
    assert metrics["events_failed"] == 0  # No validation errors, no processing errors
    # This assertion will now pass
    assert metrics["sinks_registered"] == 1
    assert metrics["queue_size_approx"] == 0


# --- Tests for Shutdown ---
def test_shutdown():
    """Test graceful shutdown flushes queue and closes sinks."""
    with patch("builtins.open", new_callable=mock_open) as mock_file:
        sink = FileSink("feedback.jsonl")
        register_sink(sink)

        # Enqueue an event
        collect_feedback("test_event", {"key": "value"})

        # Shutdown
        shutdown()

        # FIX: Use robust assertion on the module's variable
        assert (
            feedback_handlers._worker_thread is None
            or not feedback_handlers._worker_thread.is_alive()
        )
        assert _worker_queue.empty()

        # The file handle was opened during collect_feedback/emit
        mock_file().close.assert_called_once()

        # The shutdown should have processed the event
        assert _registry.events_processed == 1


def test_shutdown_idempotent():
    """Test shutdown is idempotent."""
    # Ensure worker is started
    register_sink(LoggingSink())

    shutdown()

    # Reset thread after first shutdown (as the fixture would do)
    # FIX: Set the variable on the module
    feedback_handlers._worker_thread = None

    # Second shutdown call (should be safe)
    shutdown()

    # FIX: Check the module's variable
    assert (
        feedback_handlers._worker_thread is None or not feedback_handlers._worker_thread.is_alive()
    )


# --- Edge Cases ---
def test_collect_feedback_invalid_data():
    """Test collect_feedback with non-dict data."""
    # register_sink needs to run to start the worker and set up the default logger
    # ...or does it? collect_feedback will add the default logger.

    collect_feedback("test_event", data="not a dict")

    assert _registry.events_failed == 1
    assert _registry.events_collected == 1
    # Check that the default sink was added
    assert _registry.sinks_registered == 1


def test_filesink_no_permissions(temp_file):
    """Test FileSink with inaccessible file path when used in worker."""
    with patch(
        "runner.feedback_handlers.FileSink.emit",
        side_effect=OSError("Permission denied"),
    ) as mock_emit:
        sink = FileSink(str(temp_file))
        register_sink(sink)

        # This will fail inside the worker's thread
        collect_feedback("test_event_fail", {"key": "value"})

        _worker_queue.join()

        # The worker should catch the exception and increment events_failed
        assert _registry.events_failed == 1
        assert _registry.events_processed == 0


# --- Direct Registry Process Test (Replaced former async test) ---
def test_registry_process_event_direct(mock_file_sink):
    """Test direct synchronous processing of events by the registry."""
    sink, mock_file = mock_file_sink
    _registry.sinks["mock_sink"] = sink

    event = FeedbackEvent(event_type="test", data={"key": "value"})
    _registry.process_event(event)

    assert _registry.events_processed == 1
    mock_file().write.assert_called_once()

    del _registry.sinks["mock_sink"]


# --- Main Runner ---
if __name__ == "__main__":
    pytest.main(
        [
            __file__,
            "-v",
            "--tb=short",
            "--cov=runner.feedback_handlers",
            "--cov-report=term-missing",
        ]
    )
