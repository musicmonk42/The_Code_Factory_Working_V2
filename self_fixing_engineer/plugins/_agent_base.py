# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
_agent_base.py — Shared infrastructure for all plugin agents.

Architecture
------------
Provides a single, production-quality foundation for every plugin agent in the
``self_fixing_engineer.plugins`` package:

* **AgentMetrics** — per-agent Prometheus metrics (calls counter, errors counter,
  latency histogram) using ``safe_metric`` from ``shared.noop_metrics`` so that
  the metrics degrade gracefully when ``prometheus_client`` is not installed.
* **structured_log** — JSON-structured logging with automatic PII redaction for
  any field whose key contains the substrings ``key``, ``secret``, ``password``,
  or ``token``.
* **emit_audit_event_safe** — async wrapper around
  ``self_fixing_engineer.arbiter.audit_log.emit_audit_event`` that silently
  swallows every exception so agents never crash on audit failure.
* **OpenTelemetry tracer** — module-level tracer with no-op fallback (same
  pattern as ``crew_manager.py``).
* **_validate_path / _validate_command** — shared security helpers previously
  duplicated in every agent file.

Label names used on all metrics: ``agent_name``, ``agent_type``, ``status``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "AgentMetrics",
    "agent_span",
    "structured_log",
    "emit_audit_event_safe",
    "_validate_path",
    "_validate_command",
    "_tracer",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenTelemetry tracer — no-op fallback (crew_manager.py pattern lines 108-111)
# ---------------------------------------------------------------------------

try:
    from opentelemetry import trace as _otel_trace  # type: ignore[import]

    _tracer = _otel_trace.get_tracer(__name__)
except ImportError:
    _tracer = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Prometheus metric factories — lazy import with graceful degradation
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import]
    from prometheus_client import Histogram as _Histogram  # type: ignore[import]
except ImportError:
    _Counter = None  # type: ignore[assignment]
    _Histogram = None  # type: ignore[assignment]

from shared.noop_metrics import safe_metric

# Shared metric names — one set of collectors, differentiated by labels.
_AGENT_CALLS = safe_metric(
    _Counter,
    "plugin_agent_calls_total",
    "Total number of plugin agent process() invocations",
    labelnames=["agent_name", "agent_type", "status"],
)
_AGENT_ERRORS = safe_metric(
    _Counter,
    "plugin_agent_errors_total",
    "Total number of plugin agent process() errors",
    labelnames=["agent_name", "agent_type", "status"],
)
_AGENT_LATENCY = safe_metric(
    _Histogram,
    "plugin_agent_latency_seconds",
    "Latency of plugin agent process() calls in seconds",
    labelnames=["agent_name", "agent_type", "status"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# ---------------------------------------------------------------------------
# AgentMetrics
# ---------------------------------------------------------------------------

_METRICS_LOCK = threading.Lock()
_METRICS_REGISTRY: Dict[str, "AgentMetrics"] = {}


@dataclass
class AgentMetrics:
    """Per-agent Prometheus metrics with graceful no-op degradation.

    Parameters
    ----------
    agent_type:
        Short identifier for the agent type, e.g. ``"smart_refactor"``.
        Used as the ``agent_type`` label value on every metric observation.
    """

    agent_type: str
    calls: Any = field(init=False)
    errors: Any = field(init=False)
    latency: Any = field(init=False)

    def __post_init__(self) -> None:
        self.calls = _AGENT_CALLS
        self.errors = _AGENT_ERRORS
        self.latency = _AGENT_LATENCY

    @classmethod
    def for_agent(cls, agent_type: str) -> "AgentMetrics":
        """Return a cached :class:`AgentMetrics` instance for *agent_type*."""
        with _METRICS_LOCK:
            if agent_type not in _METRICS_REGISTRY:
                _METRICS_REGISTRY[agent_type] = cls(agent_type=agent_type)
            return _METRICS_REGISTRY[agent_type]


# ---------------------------------------------------------------------------
# PII redaction constants
# ---------------------------------------------------------------------------

_PII_SUBSTRINGS = ("key", "secret", "password", "token")
_REDACTED = "[REDACTED]"


def _redact_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *fields* with sensitive values replaced by ``[REDACTED]``."""
    out: Dict[str, Any] = {}
    for k, v in fields.items():
        k_lower = k.lower()
        if any(sub in k_lower for sub in _PII_SUBSTRINGS):
            out[k] = _REDACTED
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# structured_log
# ---------------------------------------------------------------------------


def structured_log(event: str, **fields: Any) -> None:
    """Emit a JSON-structured log record with automatic PII redaction.

    Any field whose key contains ``key``, ``secret``, ``password``, or
    ``token`` (case-insensitive) has its value replaced by ``[REDACTED]``
    before the record is serialised.

    Parameters
    ----------
    event:
        Short event name, e.g. ``"agent.process.start"``.
    **fields:
        Arbitrary structured fields to include in the log payload.
    """
    safe_fields = _redact_fields(fields)
    payload = {"event": event, **safe_fields}
    logger.info(json.dumps(payload))


# ---------------------------------------------------------------------------
# emit_audit_event_safe
# ---------------------------------------------------------------------------


async def emit_audit_event_safe(event_type: str, details: Optional[Dict[str, Any]] = None) -> None:
    """Emit an audit event without raising on failure.

    Wraps ``self_fixing_engineer.arbiter.audit_log.emit_audit_event`` in a
    try/except so that a broken audit pipeline never propagates an exception
    to the calling agent.

    Parameters
    ----------
    event_type:
        Audit event category string, e.g. ``"refactor_completed"``.
    details:
        Arbitrary metadata dict forwarded to the audit log.
    """
    _details = details or {}
    try:
        from self_fixing_engineer.arbiter.audit_log import (  # type: ignore[import]
            emit_audit_event,
        )

        await emit_audit_event(event_type, _details)
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("emit_audit_event_safe: audit emission suppressed: %s", exc)


# ---------------------------------------------------------------------------
# OpenTelemetry span context manager
# ---------------------------------------------------------------------------

from contextlib import contextmanager as _contextmanager  # noqa: E402
from typing import Generator as _Generator


@_contextmanager
def agent_span(span_name: str, agent_name: str, task_keys: Sequence[str]) -> _Generator[Any, None, None]:
    """Context manager that opens an OTel span and sets standard attributes.

    Falls back to a no-op ``contextlib.nullcontext`` when OpenTelemetry is not
    installed, so callers never need ``if _tracer`` guards.

    Usage::

        with agent_span("MyAgent.process", self.name, list(task.keys())):
            ...  # agent processing logic

    Parameters
    ----------
    span_name:
        Span name, typically ``"ClassName.process"``.
    agent_name:
        Value for the ``agent.name`` span attribute.
    task_keys:
        Keys of the incoming task dict, recorded as ``task.keys``.
    """
    if _tracer is None:
        from contextlib import nullcontext

        with nullcontext():
            yield
        return

    with _tracer.start_as_current_span(span_name) as span:
        try:
            span.set_attribute("agent.name", agent_name)
            span.set_attribute("task.keys", str(list(task_keys)))
        except Exception:  # pragma: no cover — OTel internals
            pass
        yield span


# ---------------------------------------------------------------------------
# Shared security validators
# ---------------------------------------------------------------------------


def _validate_path(path: str, patterns: List[str]) -> bool:
    """Return ``True`` if *path* matches at least one regex in *patterns*."""
    return any(re.match(p, path) for p in patterns)


def _validate_command(command: str, patterns: List[str]) -> bool:
    """Return ``True`` if *command* matches at least one regex in *patterns*."""
    if not patterns:
        return False
    return any(re.match(p, command) for p in patterns)
