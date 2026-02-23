# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Canonical null-tracer / null-span stubs for OpenTelemetry.

Use these when ``opentelemetry-api`` is not installed or when a lightweight
no-op tracer is needed without taking a hard dependency on the OTel SDK.

Usage::

    from shared.noop_tracing import NullSpan, NullTracer

    try:
        from opentelemetry import trace
        _tracer = trace.get_tracer(__name__)
    except ImportError:
        _tracer = NullTracer()
"""

from __future__ import annotations

from typing import Any


class NullSpan:
    """No-op span that satisfies the OpenTelemetry ``Span`` interface."""

    def __enter__(self) -> "NullSpan":
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def set_attribute(self, *_: Any, **__: Any) -> None:
        pass

    def record_exception(self, *_: Any, **__: Any) -> None:
        pass

    def set_status(self, *_: Any, **__: Any) -> None:
        pass

    def add_event(self, *_: Any, **__: Any) -> None:
        pass


class NullTracer:
    """No-op tracer that returns :class:`NullSpan` for every span request."""

    def start_as_current_span(self, *_: Any, **__: Any) -> NullSpan:
        return NullSpan()
