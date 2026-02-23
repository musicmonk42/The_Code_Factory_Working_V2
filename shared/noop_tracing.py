# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Canonical null-tracer / null-span stubs for OpenTelemetry.

Problem
-------
Eight or more independent null-tracer / null-span implementations were
spread across the codebase:

- ``server/routers/jobs_ws.py``
- ``self_fixing_engineer/main.py``
- ``self_fixing_engineer/mesh/graph_rag_policy.py``

Each defined its own ``_NullSpan`` / ``_NullTracer`` / ``_NoOpTracer`` /
``_NullContext`` class with slightly different method sets.  Code that used
``set_status`` or ``add_event`` (part of the official OTel SDK interface)
would silently miss the method and raise ``AttributeError`` at runtime.

Solution
--------
This module provides a single, production-quality implementation with:

* **Complete OpenTelemetry ``Span`` interface** — ``set_attribute``,
  ``record_exception``, ``set_status``, ``add_event``, and context-manager
  protocol (``__enter__`` / ``__exit__``).
* **Correct return types** — :meth:`NullTracer.start_as_current_span`
  returns a :class:`NullSpan`, matching the OTel SDK type contract.
* **Zero external dependencies** — imports only stdlib so the module is
  safe to load in any environment, including test runners that do not have
  ``opentelemetry-api`` installed.
* **Drop-in replacement** — ``NullTracer().start_as_current_span(...)``
  behaves identically to a real tracer call from the caller's perspective.

Architecture
------------
::

    ┌─────────────────────────────────────────────────────────────────┐
    │                  Null-Tracing Stubs                             │
    │                                                                 │
    │  try:                                                           │
    │      from opentelemetry import trace                            │
    │      _tracer = trace.get_tracer(__name__)   ──► real tracer    │
    │  except ImportError:                                            │
    │      from shared.noop_tracing import NullTracer                │
    │      _tracer = NullTracer()                 ──► null tracer    │
    │                                                 │               │
    │         ┌───────────────────────────────────────┘               │
    │         ▼                                                       │
    │  with _tracer.start_as_current_span("op") as span:             │
    │      span.set_attribute("db.name", "postgres")   ──► no-op    │
    │      span.record_exception(exc)                  ──► no-op    │
    │      span.set_status(StatusCode.ERROR, "msg")    ──► no-op    │
    │      span.add_event("cache.hit", {"key": "x"})   ──► no-op    │
    └─────────────────────────────────────────────────────────────────┘

Key Features
~~~~~~~~~~~~
* **Context-manager protocol** — ``NullSpan`` implements ``__enter__`` and
  ``__exit__`` so it is valid in both ``with`` and ``as`` expressions.
* **Attribute safety** — all methods accept ``*args`` and ``**kwargs`` to
  avoid ``TypeError`` if callers pass keyword arguments that differ between
  OTel SDK versions.
* **No-op guarantee** — every method body is a single ``pass`` (or ``return
  self`` for chaining) — no hidden side effects.

Industry Standards Applied
~~~~~~~~~~~~~~~~~~~~~~~~~~
* **OpenTelemetry Specification** — method names and signatures mirror the
  `OpenTelemetry Python API <https://opentelemetry-python.readthedocs.io>`_.
* **PEP 484** — full type annotations on public methods.
* **PEP 517 / 518** — zero mandatory runtime dependencies.

See Also
--------
:mod:`shared.noop_metrics` — companion module for Prometheus no-op stubs.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Null span
# ---------------------------------------------------------------------------


class NullSpan:
    """No-op span that satisfies the full OpenTelemetry ``Span`` interface.

    All methods are silent no-ops.  The class implements the context-manager
    protocol so it is valid in ``with tracer.start_as_current_span(...) as
    span:`` blocks.

    Examples
    --------
    ::

        from shared.noop_tracing import NullSpan

        span = NullSpan()
        with span:
            span.set_attribute("http.method", "GET")
            span.set_status(...)          # no-op
            span.add_event("cache.hit")   # no-op
    """

    # ------------------------------------------------------------------
    # Context-manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> "NullSpan":
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    # ------------------------------------------------------------------
    # OpenTelemetry Span interface
    # ------------------------------------------------------------------

    def set_attribute(self, *_: Any, **__: Any) -> None:
        """No-op ``set_attribute``."""
        pass

    def record_exception(self, *_: Any, **__: Any) -> None:
        """No-op ``record_exception``."""
        pass

    def set_status(self, *_: Any, **__: Any) -> None:
        """No-op ``set_status``."""
        pass

    def add_event(self, *_: Any, **__: Any) -> None:
        """No-op ``add_event``."""
        pass

    def update_name(self, *_: Any, **__: Any) -> None:
        """No-op ``update_name``."""
        pass

    def is_recording(self) -> bool:
        """Always returns ``False`` — a null span never records anything."""
        return False

    def end(self, *_: Any, **__: Any) -> None:
        """No-op ``end``."""
        pass

    def get_span_context(self) -> None:  # type: ignore[override]
        """Return ``None`` — no real context exists."""
        return None

    def __repr__(self) -> str:  # pragma: no cover
        return "NullSpan()"


# ---------------------------------------------------------------------------
# Null tracer
# ---------------------------------------------------------------------------


class NullTracer:
    """No-op tracer that returns a :class:`NullSpan` for every span request.

    Provides a drop-in replacement for ``opentelemetry.trace.Tracer`` in
    environments where the OTel SDK is not installed.

    Examples
    --------
    ::

        from shared.noop_tracing import NullTracer

        try:
            from opentelemetry import trace
            _tracer = trace.get_tracer(__name__)
        except ImportError:
            _tracer = NullTracer()

        with _tracer.start_as_current_span("my_operation") as span:
            span.set_attribute("result", "ok")
    """

    def start_as_current_span(self, *_: Any, **__: Any) -> NullSpan:
        """Return a new :class:`NullSpan` — no actual span is created."""
        return NullSpan()

    def start_span(self, *_: Any, **__: Any) -> NullSpan:
        """Return a new :class:`NullSpan` — mirrors the real ``Tracer.start_span`` API."""
        return NullSpan()

    def __repr__(self) -> str:  # pragma: no cover
        return "NullTracer()"


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "NullSpan",
    "NullTracer",
]
