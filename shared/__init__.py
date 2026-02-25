# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Shared Utilities — Code Factory Platform Cross-Cutting Infrastructure
======================================================================

This package provides lightweight, zero-external-dependency helpers that are
used across all three top-level packages of the platform:

- ``generator`` — README-to-App Code Generator (RCG)
- ``omnicore_engine`` — OmniCore Omega Pro Engine
- ``self_fixing_engineer`` — Self-Fixing Engineer (SFE)
- ``server`` — FastAPI application layer

Problem
-------
Before this package existed, every module that needed a fallback Prometheus
metric or a null OTel tracer defined its own private copy.  Fifteen or more
independent ``_NoopMetric`` / ``DummyMetric`` / ``_DummyMetric`` classes and
eight or more ``_NullTracer`` / ``_NoOpTracer`` / ``_NullContext`` classes were
scattered across the codebase — each with slightly different method sets,
docstrings, and fallback strategies.

Solution
--------
``shared`` provides single, production-quality implementations of every
cross-cutting stub:

:mod:`shared.noop_metrics`
    :class:`~shared.noop_metrics.NoopMetric` — universal no-op Prometheus
    metric with the full ``Counter / Gauge / Histogram / Summary`` interface.

    :data:`~shared.noop_metrics.NOOP` — singleton instance safe for direct
    assignment (e.g. ``MY_COUNTER = NOOP``).

    :func:`~shared.noop_metrics.safe_metric` — thread-safe, idempotent
    Prometheus metric factory with automatic NOOP fallback.

:mod:`shared.noop_tracing`
    :class:`~shared.noop_tracing.NullSpan` — no-op span implementing the
    complete OpenTelemetry ``Span`` interface.

    :class:`~shared.noop_tracing.NullTracer` — no-op tracer; drop-in
    replacement for ``opentelemetry.trace.get_tracer(__name__)``.

Architecture
------------
::

    ┌───────────────────────────────────────────────────────────────┐
    │                        shared/                                │
    │                                                               │
    │  ┌─────────────────────────┐  ┌──────────────────────────┐   │
    │  │    noop_metrics.py      │  │    noop_tracing.py        │   │
    │  │                         │  │                           │   │
    │  │  NoopMetric             │  │  NullSpan                 │   │
    │  │  NoopTimer              │  │  NullTracer               │   │
    │  │  NOOP (singleton)       │  │                           │   │
    │  │  safe_metric()          │  │                           │   │
    │  └────────────┬────────────┘  └──────────────┬────────────┘   │
    │               │                              │               │
    └───────────────┼──────────────────────────────┼───────────────┘
                    │                              │
          ┌─────────┼──────────────────────────────┼──────────┐
          │         ▼                              ▼          │
          │  omnicore_engine  generator  self_fixing_engineer │
          │  server                                           │
          └───────────────────────────────────────────────────┘

Key Design Principles
---------------------
* **Zero external dependencies at import time** — only stdlib modules are
  imported at the top level; ``prometheus_client`` and ``opentelemetry``
  are resolved lazily on first use.
* **Thread-safe** — :func:`~shared.noop_metrics.safe_metric` uses a
  module-level :class:`threading.Lock` to serialise metric registration.
* **Graceful degradation** — every public function silently returns a
  no-op stub when the optional library is absent, so callers never need
  ``if metric is None`` guards.
* **Full interface compliance** — stubs implement the complete
  ``prometheus_client`` and ``opentelemetry`` public surfaces so that
  code paths exercised without those libraries installed still behave
  correctly.

Usage
-----
::

    # ── Prometheus ────────────────────────────────────────────────
    from shared.noop_metrics import NOOP, safe_metric

    try:
        from prometheus_client import Counter
    except ImportError:
        Counter = None

    REQUESTS = safe_metric(
        Counter,
        "api_requests_total",
        "Total API requests processed",
        labelnames=["service", "status"],
    )
    REQUESTS.labels(service="generator", status="200").inc()

    # ── OpenTelemetry ─────────────────────────────────────────────
    from shared.noop_tracing import NullTracer

    try:
        from opentelemetry import trace
        _tracer = trace.get_tracer(__name__)
    except ImportError:
        _tracer = NullTracer()

    with _tracer.start_as_current_span("generate_code") as span:
        span.set_attribute("language", "python")

Industry Standards Applied
--------------------------
* **PEP 420 / Implicit Namespace Packages** — the package ships a proper
  ``__init__.py`` with an explicit ``__all__`` and ``__version__``.
* **PEP 484** — all public APIs carry full type annotations.
* **Twelve-Factor App** — no configuration baked in; behaviour is entirely
  determined by whether optional libraries are available in the environment.
* **Prometheus Data Model** — metric stubs mirror the official
  ``prometheus_client`` interface exactly.
* **OpenTelemetry Specification** — tracer / span stubs implement the
  published Python OTel API surface.

See Also
--------
:mod:`omnicore_engine.metrics_utils` — the more feature-rich counterpart
for callers that need explicit ``safe_counter`` / ``safe_gauge`` /
``safe_histogram`` helpers with full per-type type signatures.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Package metadata
# ---------------------------------------------------------------------------

__version__: str = "1.0.0"
__author__: str = "Novatrax Labs"

# ---------------------------------------------------------------------------
# Public re-exports — callers may import from the top-level package:
#
#   from shared import NOOP, safe_metric, NullTracer
#
# rather than from the sub-modules directly.  Both forms are supported.
# ---------------------------------------------------------------------------

try:
    from shared.noop_metrics import NOOP, NoopMetric, NoopTimer, safe_metric
    from shared.noop_tracing import NullSpan, NullTracer
    from shared.plugin_registry_base import (
        BasePluginRegistry,
        DependencyAwareRegistryMixin,
        HotReloadableRegistryMixin,
    )

    _SHARED_AVAILABLE: bool = True
except ImportError as _err:  # pragma: no cover
    # Should never happen — these modules have zero external dependencies.
    logger.error(
        "Failed to import shared sub-modules: %s.  "
        "Verify that the 'shared' package is on sys.path.",
        _err,
    )
    _SHARED_AVAILABLE = False

    # Define minimal stubs so that ``from shared import NOOP`` never raises
    # ImportError even in the most broken environments.
    class NoopMetric:  # type: ignore[no-redef]
        def __getattr__(self, _: str) -> Any:
            return lambda *a, **k: self

    class NoopTimer:  # type: ignore[no-redef]
        def __enter__(self) -> "NoopTimer":
            return self

        def __exit__(self, *_: Any) -> None:
            pass

    NOOP: Any = NoopMetric()  # type: ignore[assignment]

    def safe_metric(*_: Any, **__: Any) -> Any:  # type: ignore[misc]
        return NOOP

    class NullSpan:  # type: ignore[no-redef]
        def __enter__(self) -> "NullSpan":
            return self

        def __exit__(self, *_: Any) -> None:
            pass

        def __getattr__(self, _: str) -> Any:
            return lambda *a, **k: None

    class NullTracer:  # type: ignore[no-redef]
        def start_as_current_span(self, *_: Any, **__: Any) -> NullSpan:
            return NullSpan()


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    # Package metadata
    "__version__",
    "__author__",
    # noop_metrics
    "NoopMetric",
    "NoopTimer",
    "NOOP",
    "safe_metric",
    # noop_tracing
    "NullSpan",
    "NullTracer",
    # plugin_registry_base
    "BasePluginRegistry",
    "HotReloadableRegistryMixin",
    "DependencyAwareRegistryMixin",
    # shared.registry
    "Registry",
    # shared.circuit_breaker
    "CircuitBreaker",
    "get_circuit_breaker",
]

# Lazy exports for new consolidated sub-modules (avoids adding heavy imports
# to the top-level package that previously had zero external dependencies).
try:
    from shared.registry import Registry
    from shared.circuit_breaker import CircuitBreaker, get_circuit_breaker
except ImportError:  # pragma: no cover
    pass
