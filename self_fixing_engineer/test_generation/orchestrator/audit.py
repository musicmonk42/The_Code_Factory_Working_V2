# test_generation/orchestrator/audit.py
from __future__ import annotations

import inspect
import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Union, Optional

# Always read config at runtime so monkeypatches (AUDIT_LOG_FILE) take effect.
from . import config as _cfg
from .console import audit_logger_instance

__all__ = [
    "RUN_ID",
    "audit_event",
    "_json_serializable_default",
    "AUDIT_LOGGER_AVAILABLE",
    "AuditLogger",
    "append_to_feedback_log",
    "FEEDBACK_LOG_FILE",
]

# Stable run id for grouping events (tests import this).
RUN_ID = str(uuid.uuid4())

LOGGER_NAME = "atco_audit"
_logger = logging.getLogger(LOGGER_NAME)

# Arbiter (external) audit logger availability probe
try:
    from arbiter.audit_log import audit_logger as arbiter_audit
    AUDIT_LOGGER_AVAILABLE = True
except ImportError:
    arbiter_audit = None
    AUDIT_LOGGER_AVAILABLE = False


class AuditLogger:
    """Stub for compatibility when the full arbiter logger isn't available."""
    @staticmethod
    def from_environment():
        return logging.getLogger(LOGGER_NAME)


def _get_audit_log_file() -> Union[str, os.PathLike, None]:
    """Read the audit log file path at runtime (supports monkeypatch in tests)."""
    return getattr(_cfg, "AUDIT_LOG_FILE", None)


def _json_serializable_default(obj: Any) -> Any:
    """
    json.dumps(default=...) handler:
      - If the object provides __json__(), call it (and propagate any error).
      - Datetime -> ISO8601.
      - Otherwise, return a descriptive placeholder so most odd types serialize.
    """
    if hasattr(obj, "__json__"):
        # Intentionally call __json__ so tests can simulate failure.
        return obj.__json__()  # may raise; we want that behavior for the test

    if isinstance(obj, datetime):
        return obj.isoformat()

    # Best-effort placeholder for non-serializable objects
    try:
        # ensure __str__ doesn't explode
        _ = str(obj)
        return f"<Non-serializable object of type: {type(obj).__name__}>"
    except Exception:
        # Force json.dumps to fail so our caller logs an ERROR
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


async def audit_event(
    event_type: str,
    details: Dict[str, Any],
    critical: bool = False,
    **kwargs: Any,
) -> None:
    """
    Structured audit logging with arbiter first, local fallback otherwise.
    Never crashes the caller; on serialization failure, logs an ERROR and returns.
    """
    # Copy to avoid mutating caller state.
    payload = dict(details or {})
    run_id = kwargs.pop("run_id", RUN_ID)

    payload["run_id"] = run_id
    payload["timestamp"] = int(time.time())
    payload["timestamp_iso"] = datetime.utcnow().isoformat() + "Z"

    # Try arbiter first (if present)
    if AUDIT_LOGGER_AVAILABLE and arbiter_audit is not None:
        try:
            res = arbiter_audit.log_event(
                event_type=event_type,
                details=payload,
                run_id=run_id,
                critical=critical,
                **kwargs,
            )
            if inspect.isawaitable(res):
                await res
            return
        except Exception as e:
            # Include the error in the payload; local fallback will record it.
            payload["arbiter_error"] = str(e)
            _logger.warning(
                "Arbiter audit logging failed for event '%s'. Falling back to local logging.",
                event_type,
            )

    level = "CRITICAL" if critical else "INFO"
    log_level = getattr(logging, level, logging.INFO)

    log_entry = {"event": event_type, "level": level, **payload}

    # Serialize; if this fails, the test expects an ERROR log and graceful return.
    try:
        line = json.dumps(log_entry, default=_json_serializable_default, ensure_ascii=False)
    except Exception as e:
        _logger.error("Failed to serialize audit log for event '%s': %s", event_type, e)
        return

    # Console/logger line (captured by caplog in tests)
    audit_logger_instance.log(log_level, line)

    # Best-effort durable write for tests to assert on.
    path = _get_audit_log_file()
    if path:
        p = os.fspath(path)  # handles Path or str
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            _logger.warning("Failed to write audit log file '%s': %s", p, e)


# Back-compat alias for tests that import _audit:
_audit = audit_event
try:
    __all__.append("_audit")  # type: ignore[attr-defined]
except Exception:
    pass

# Define a constant for the feedback log file path
FEEDBACK_LOG_FILE = os.getenv("FEEDBACK_LOG_FILE", os.path.join(
    os.path.expanduser("~"), ".local", "state", "test-agent-cli", "feedback_log.jsonl"
))

async def append_to_feedback_log(feedback_log_path: str, feedback_data: Dict[str, Any], config: Optional[Dict] = None) -> None:
    """
    Delegate to the io_utils implementation for backwards compatibility.
    This function exists to maintain compatibility with existing imports.
    """
    from test_generation.gen_agent.io_utils import append_to_feedback_log as io_append
    await io_append(feedback_log_path, feedback_data, config)
