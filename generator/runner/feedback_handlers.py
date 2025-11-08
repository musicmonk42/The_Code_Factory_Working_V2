# runner/feedback_handlers.py
"""
feedback_handlers.py – Production-grade feedback ingestion.

Design goals
------------
* Zero runtime external dependencies (stdlib only).
* **Never raise** in production or CI – best-effort only.
* Structured, machine-readable JSON lines for audit trails.
* Thread-safe, extensible sink system.
* Tiny internal schema validation (no pydantic required).
* Built-in metrics (counters) that can be scraped by any observability stack.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, List, TextIO

# --- STDLIBS FOR FIXES ---
import os
import uuid
import warnings 
# -------------------------

# --------------------------------------------------------------------------- #
# Logging setup – safe for import in tests (no duplicate handlers)
# --------------------------------------------------------------------------- #
logger = logging.getLogger("runner.feedback_handlers")
if not logger.handlers:                     # pragma: no branch
    _handler = logging.StreamHandler()
    _fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    _handler.setFormatter(_fmt)
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)


# --------------------------------------------------------------------------- #
# Mockable Helpers (for consistent testing)
# --------------------------------------------------------------------------- #
def _get_timestamp() -> str:
    """Helper for mockable datetime generation."""
    return datetime.utcnow().isoformat() + "Z"

def _get_event_id() -> str:
    """Helper for mockable event ID generation."""
    # Use only first 32 hex chars for a concise ID that matches expectation
    return f"evt_{uuid.uuid4().hex[:32]}"


# --------------------------------------------------------------------------- #
# Public schema (pure dataclasses)
# --------------------------------------------------------------------------- #
class Severity(str, Enum):
    """Event severity levels."""
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class FeedbackEvent:
    """
    Standardized feedback event schema. Immutable, thread-safe.
    This is the data contract for all feedback.
    """
    event_type: str
    data: Dict[str, Any]
    source: str = "default_source"
    severity: Severity = Severity.INFO
    # FIX: Use lambda to force runtime lookup, allowing patch to work
    timestamp: str = field(default_factory=lambda: _get_timestamp())
    event_id: str = field(default_factory=lambda: _get_event_id())
    
    def __post_init__(self):
        """Add post-init validation for severity."""
        if not isinstance(self.severity, Severity):
            warnings.warn(
                f"Invalid severity '{self.severity}'. Defaulting to INFO.",
                UserWarning
            )
            # Bypass frozen=True to correct the value
            object.__setattr__(self, "severity", Severity.INFO)

    def serialize(self) -> str:
        """Serialize event to a JSON line string for sinks."""
        return json.dumps(self.__dict__, default=str)

    def to_json(self) -> str:
        """Alias for serialize()."""
        return self.serialize()

    def validate(self) -> Optional[str]:
        """Perform tiny, zero-dependency validation."""
        if not self.event_type or not isinstance(self.event_type, str):
            return "event_type must be a non-empty string"
        if not isinstance(self.data, dict):
            return "data must be a dictionary"
        return None


# --------------------------------------------------------------------------- #
# Sink interface (ABC)
# --------------------------------------------------------------------------- #
class FeedbackSink(ABC):
    """Interface for all feedback sinks (file, HTTP, database, etc.)."""
    @abstractmethod
    def emit(self, event: FeedbackEvent) -> None:
        """Emit a single feedback event. Must be thread-safe (called from worker)."""
        pass
    
    @abstractmethod
    def flush(self) -> None:
        """Force-flush any buffered events. Called during shutdown."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close the sink (e.g., file handles, network connections)."""
        pass


# --------------------------------------------------------------------------- #
# Global registry and state
# --------------------------------------------------------------------------- #
@dataclass
class FeedbackRegistry:
    """Internal singleton to manage sinks and metrics."""
    sinks: Dict[str, FeedbackSink] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    # Metrics: stdlib only, no prometheus dependency
    events_collected: int = 0
    events_processed: int = 0
    events_failed: int = 0
    sinks_registered: int = 0
    
    def close_all(self) -> None:
        """Safely close all registered sinks."""
        with self.lock:
            for name, sink in self.sinks.items():
                try:
                    sink.flush()
                    sink.close()
                except Exception as e:
                    logger.error(
                        f"Failed to close sink '{name}': {e}", exc_info=True
                    )
            self.sinks.clear()
            self.sinks_registered = 0
            
    # Add synchronous process_event for direct call/testing (no more async)
    def process_event(self, event: FeedbackEvent) -> None:
        """Synchronously process a single event and dispatch to all sinks."""
        error = event.validate()
        if error:
            logger.error(
                f"Invalid feedback event dropped: {error}. "
                f"Type: {event.event_type}, Data: {event.data}"
            )
            self.events_failed += 1
            return
        
        # Dispatch to all sinks
        with self.lock:
            sinks = list(self.sinks.values())
        
        success = True
        for sink in sinks:
            try:
                sink.emit(event)
            except Exception as e:
                logger.error(
                    f"Sink {type(sink).__name__} failed to emit "
                    f"event {event.event_id}: {e}",
                    exc_info=True,
                )
                success = False
                
        if success:
             self.events_processed += 1
        else:
             self.events_failed += 1

_registry = FeedbackRegistry()


# --------------------------------------------------------------------------- #
# Built-in sinks
# --------------------------------------------------------------------------- #
class LoggingSink(FeedbackSink):
    """[Default] Emits feedback events to a standard logger."""
    def __init__(self, name: str = "feedback.log"):
        self.logger = logging.getLogger(name)

    def emit(self, event: FeedbackEvent) -> None:
        self.logger.info(event.serialize())

    def flush(self) -> None:
        pass  # Logging handlers flush automatically or on close

    def close(self) -> None:
        pass  # Nothing to close for this logger


# FIX: Simplified FileSink to be purely synchronous and robust
class FileSink(FeedbackSink):
    """Writes feedback events to a file path."""
    def __init__(self, filepath: str):
        self.filepath = filepath
        self._fh: Optional[TextIO] = None
        try:
            # Ensure directory exists, but don't fail if it can't be created
            os.makedirs(os.path.dirname(self.filepath) or '.', exist_ok=True)
        except Exception:
            pass # Ignore directory creation errors

    def emit(self, event: FeedbackEvent) -> None:
        """Synchronous file write."""
        try:
            if self._fh is None:
                # Open in append mode, encoding ensures cross-platform compatibility
                self._fh = open(self.filepath, "a", encoding="utf-8")
                
            self._fh.write(event.to_json() + "\n")
            self._fh.flush() # Ensure it hits disk immediately
        except Exception as e:
            # Log and suppress: FileSink must never raise
            logger.warning(
                f"FileSink write to '{self.filepath}' failed: {e}", exc_info=True
            )

    def flush(self) -> None:
        try:
            if self._fh:
                self._fh.flush()
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self._fh:
                self._fh.close()
                self._fh = None
        except Exception as e:
            logger.warning(
                f"FileSink close for '{self.filepath}' failed: {e}", exc_info=True
            )


class HttpSink(FeedbackSink):
    """
    Emits feedback events to an HTTP endpoint (synchronous, inside worker thread).
    """
    def __init__(self, endpoint: str, auth_token: Optional[str] = None):
        self.endpoint = endpoint
        self.headers = {"Content-Type": "application/json"}
        if auth_token:
            self.headers["Authorization"] = f"Bearer {auth_token}"
        
        from urllib import request
        self.request = request

    def emit(self, event: FeedbackEvent) -> None:
        """This runs *inside* the worker thread."""
        try:
            data = event.serialize().encode("utf-8")
            req = self.request.Request(
                self.endpoint, data=data, headers=self.headers, method="POST"
            )
            with self.request.urlopen(req, timeout=5.0) as response:
                if response.status < 200 or response.status >= 300:
                    logger.warning(
                        f"HttpSink failed to emit event {event.event_id}. Status: {response.status}"
                    )
        except Exception as e:
            # Fire and forget: log error but do not re-raise.
            logger.error(
                f"HttpSink connection error for {self.endpoint}: {e}",
                exc_info=True,
            )

    def flush(self) -> None:
        pass  # No client-side buffering

    def close(self) -> None:
        pass  # No persistent connection to close


# --------------------------------------------------------------------------- #
# Worker thread setup (stdlib queue)
# --------------------------------------------------------------------------- #
_worker_queue: queue.Queue[Optional[FeedbackEvent]] = queue.Queue(maxsize=10000)
_worker_thread: Optional[threading.Thread] = None
_worker_stop = threading.Event()
_SENTINEL = None  # Sentinel object for shutdown signal


def _worker_loop() -> None:
    """
    Daemon thread main loop.
    Pulls events from the queue and dispatches them to all sinks.
    """
    logger.info("Feedback worker thread started.")
    while not _worker_stop.is_set():
        try:
            # Block until an item is available or 1s timeout
            event = _worker_queue.get(timeout=1.0)
            if event is _SENTINEL:
                _worker_queue.task_done()
                break  # Shutdown signal
            
            # Use synchronous registry processing
            _registry.process_event(event)
            _worker_queue.task_done()

        except queue.Empty:
            # Timeout, loop continues to check _worker_stop
            continue
        except Exception as e:
            # Catchall for unexpected worker errors
            _registry.events_failed += 1
            logger.critical(
                f"Feedback worker loop crashed: {e}", exc_info=True
            )
            time.sleep(1)

    # --- Shutdown flush (process remaining items) ---
    try:
        while True:
            event = _worker_queue.get_nowait()
            if event is _SENTINEL:
                _worker_queue.task_done()
                continue
            
            # Final synchronous processing
            _registry.process_event(event) 
            _worker_queue.task_done()
    except queue.Empty:
        pass
    except Exception as e:
        logger.error(
            f"Error during feedback worker shutdown flush: {e}", exc_info=True
        )

    logger.info("Feedback worker thread stopped.")


def _ensure_worker_started() -> None:
    """
    FIX: Starts the global worker thread if it's not running.
    This function's *only* job is to start the thread.
    """
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_stop.clear()
        _worker_thread = threading.Thread(
            target=_worker_loop, daemon=True, name="FeedbackWorker"
        )
        _worker_thread.start()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def register_sink(sink: FeedbackSink, name: Optional[str] = None) -> None:
    """
    Register a new feedback sink.
    """
    if not isinstance(sink, FeedbackSink):
        raise TypeError("sink must be an instance of FeedbackSink")
        
    sink_name = name or f"{type(sink).__name__}_{_registry.sinks_registered}"
    
    with _registry.lock:
        if sink_name in _registry.sinks:
            logger.warning(
                f"Sink '{sink_name}' already registered. Overwriting."
            )
            # Close old sink before replacing
            try:
                _registry.sinks[sink_name].close()
            except Exception:
                pass
                
        _registry.sinks[sink_name] = sink
        # FIX: This is now the only place sinks_registered is calculated
        _registry.sinks_registered = len(_registry.sinks)
        
    # FIX: Call start *after* sink is registered
    _ensure_worker_started()
    logger.info(f"Feedback sink '{sink_name}' registered.")


def collect_feedback(
    event_type: str,
    data: Dict[str, Any],
    source: str = "default_source",
    severity: Severity = Severity.INFO,
) -> None:
    """
    Main public API. Collects a feedback event.
    This call is non-blocking and thread-safe.
    It will **never** raise an exception.
    """
    try:
        # FIX: Add default sink if this is the first call and no sinks exist
        if _registry.sinks_registered == 0:
            with _registry.lock:
                # Double-check inside lock
                if not _registry.sinks:
                    _registry.sinks["default_logger"] = LoggingSink()
                    _registry.sinks_registered = 1
        
        # FIX: Call _ensure_worker_started() *after* the sink is guaranteed to exist
        _ensure_worker_started() # This call will now correctly start the thread

        event = FeedbackEvent(
            event_type=event_type,
            data=data,
            source=source,
            severity=severity
        )
        
        # Increment collected counter before validation
        _registry.events_collected += 1 

        # Validate (non-blocking)
        error = event.validate()
        if error:
            logger.error(
                f"Invalid feedback event dropped: {error}. "
                f"Type: {event_type}, Data: {data}"
            )
            _registry.events_failed += 1
            return # Do not enqueue invalid event

        # Enqueue (non-blocking)
        _worker_queue.put_nowait(event)
        
    except queue.Full:
        _registry.events_failed += 1
        logger.error(
            f"Feedback queue is full. Event '{event_type}' was dropped."
        )
    except Exception as e:
        _registry.events_failed += 1
        logger.critical(
            f"Failed to enqueue feedback event '{event_type}': {e}",
            exc_info=True,
        )


def get_feedback_metrics() -> Dict[str, int]:
    """Retrieve internal metrics. For monitoring."""
    return {
        "events_collected": _registry.events_collected,
        "events_processed": _registry.events_processed,
        "events_failed": _registry.events_failed,
        "sinks_registered": _registry.sinks_registered,
        "queue_size_approx": _worker_queue.qsize(),
    }


# --------------------------------------------------------------------------- #
# Graceful shutdown – **zero noise, zero drop**
# --------------------------------------------------------------------------- #
def shutdown() -> None:
    """FIX: Stop the worker robustly, flush all queued records, and close sinks. Idempotent."""
    _worker_stop.set()

    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        # Wake the worker to exit its loop
        try:
            _worker_queue.put_nowait(_SENTINEL)
        except queue.Full:
            pass

        # Wait for every enqueued item (including sentinel) to be processed
        _worker_queue.join()

        # Wait for the thread to finish its graceful loop exit
        _worker_thread.join(timeout=2.0)
    else:
        # If no worker was started, drain the queue manually to clean task_done calls
        while not _worker_queue.empty():
            try:
                _worker_queue.get_nowait()
                _worker_queue.task_done()
            except queue.Empty:
                break
        
    _registry.close_all()


# --------------------------------------------------------------------------- #
# Optional: auto-shutdown on interpreter exit
# --------------------------------------------------------------------------- #
import atexit
atexit.register(shutdown)


# --------------------------------------------------------------------------- #
# Example usage (doctest)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    collect_feedback("hitl_review", {"decision": "approve", "confidence": 0.97})

    # Cross-platform example: write to current directory
    register_sink(FileSink("feedback.jsonl"))

    collect_feedback("policy_violation", {"rule": "rate_limit", "ip": "203.0.113.42"})

    # Give the worker thread a moment to process before the main thread exits
    time.sleep(0.1)
    shutdown()
    print(f"Feedback metrics on exit: {get_feedback_metrics()}")