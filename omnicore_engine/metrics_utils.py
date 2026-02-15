# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Consolidated, thread-safe Prometheus metric helpers for the OmniCore Engine.

Problem
-------
Six nearly-identical ``get_or_create_metric`` / ``safe_counter`` helper functions
were duplicated across the codebase:

- ``generator/runner/llm_plugin_manager.py``
- ``generator/agents/generator_plugin_wrapper.py``
- ``self_fixing_engineer/arbiter/plugins/llm_client.py``
- ``self_fixing_engineer/arbiter/plugins/multi_modal_plugin.py``
- ``self_fixing_engineer/arbiter/plugin_config.py``
- ``generator/audit_log/audit_plugins.py``

Every copy used subtly different locking, error-handling, and fallback
strategies — leading to occasional ``Duplicated timeseries`` crashes during
pytest collection and hot-reload cycles.

Solution
--------
This module provides a single, production-quality implementation with:

* **Thread-safe, idempotent creation** — a module-level ``threading.Lock`` and
  a two-phase lookup (registry check → create → collision fallback) guarantee
  no duplicate registration regardless of import order or concurrency.
* **Lazy prometheus_client import** — the heavy ``prometheus_client`` package
  is imported on first use, not at module-load time.  This prevents CPU
  timeout (exit code 152) during ``pytest --collect-only``.
* **Graceful degradation** — when ``prometheus_client`` is not installed,
  every public function silently returns a :class:`DummyMetric` no-op stub
  so that callers never need ``if metric is None`` guards.
* **Zero external dependencies at import time** — only stdlib modules are
  imported at the top level.

Usage
-----
::

    from omnicore_engine.metrics_utils import get_or_create_metric, safe_counter
    from prometheus_client import Counter, Histogram

    MY_COUNTER = safe_counter("my_ops_total", "Total operations", ("subsystem",))
    MY_HIST    = get_or_create_metric(
        Histogram, "my_latency_seconds", "Latency",
        labelnames=("endpoint",),
        buckets=(0.01, 0.05, 0.1, 0.5, 1, 5, 10),
    )

    # Both are safe even if prometheus_client is missing — they return DummyMetric.
    MY_COUNTER.labels(subsystem="engine").inc()
    MY_HIST.labels(endpoint="/api/v1/fix").observe(0.042)

Static type checking with ``mypy --strict`` is recommended for maximum safety.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional, Sequence, Tuple, Type, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy prometheus_client resolution
# ---------------------------------------------------------------------------
# We cache the import result so the try/except runs at most once, even under
# concurrent access (the GIL protects the first assignment, and subsequent
# reads are plain attribute loads).

_prometheus_available: Optional[bool] = None
_REGISTRY: Any = None
_Counter: Any = None
_Gauge: Any = None
_Histogram: Any = None
_Summary: Any = None

# Module-level lock protecting *metric creation*.  This serialises the
# create-or-retrieve logic and prevents two threads from racing to register
# the same metric name.
_metrics_lock = threading.Lock()


def _ensure_prometheus() -> bool:
    """Lazy-import ``prometheus_client`` and cache the result.

    Returns ``True`` if the library is available, ``False`` otherwise.
    Thread-safe: the worst case is two concurrent calls both perform the
    import, but the result is idempotent.
    """
    global _prometheus_available, _REGISTRY, _Counter, _Gauge, _Histogram, _Summary
    if _prometheus_available is not None:
        return _prometheus_available
    try:
        from prometheus_client import REGISTRY, Counter, Gauge, Histogram, Summary

        _REGISTRY = REGISTRY
        _Counter = Counter
        _Gauge = Gauge
        _Histogram = Histogram
        _Summary = Summary
        _prometheus_available = True
    except ImportError:
        logger.debug(
            "prometheus_client not installed — all metrics will be no-op stubs."
        )
        _prometheus_available = False
    return _prometheus_available


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_or_create_metric(
    metric_class: type,
    name: str,
    documentation: str,
    labelnames: Union[Tuple[str, ...], Sequence[str]] = (),
    buckets: Optional[Tuple[float, ...]] = None,
) -> Any:
    """Create **or** retrieve a Prometheus metric — thread-safe and idempotent.

    This is the primary entry-point.  It handles:

    1. **Registry lookup** — if a collector with *name* already exists, it is
       returned immediately (fast path, no allocation).
    2. **Creation** — if not found, a new metric is registered.  ``buckets``
       are forwarded only when *metric_class* is ``Histogram`` or ``Summary``.
    3. **Collision recovery** — if another thread registered the same name
       between our check and our create (narrow race window), we catch the
       ``ValueError`` raised by ``prometheus_client`` and retrieve the
       existing collector.
    4. **Graceful fallback** — if ``prometheus_client`` is not installed,
       a :class:`DummyMetric` is returned so callers can call ``.labels()``
       / ``.inc()`` / ``.observe()`` unconditionally.

    Parameters
    ----------
    metric_class : type
        One of ``Counter``, ``Gauge``, ``Histogram``, ``Summary`` from
        ``prometheus_client``.
    name : str
        Prometheus metric name (e.g. ``"llm_call_latency_seconds"``).
        Must be unique within the process.
    documentation : str
        Human-readable help string shown in ``/metrics`` output.
    labelnames : tuple[str, ...] | list[str]
        Label names for the metric.  Defaults to ``()``.
    buckets : tuple[float, ...] | None
        Optional histogram / summary bucket boundaries.

    Returns
    -------
    The metric collector instance, or a :class:`DummyMetric` no-op if
    ``prometheus_client`` is unavailable or registration fails.
    """
    if not _ensure_prometheus():
        return _DummyMetric()

    labelnames = tuple(labelnames) if labelnames else ()

    with _metrics_lock:
        # --- Fast path: already registered ---------------------------------
        try:
            if (
                hasattr(_REGISTRY, "_names_to_collectors")
                and name in _REGISTRY._names_to_collectors
            ):
                return _REGISTRY._names_to_collectors[name]
        except (AttributeError, KeyError, TypeError):
            pass  # Registry internals may vary across prometheus_client versions

        # --- Slow path: create the metric ----------------------------------
        try:
            if buckets is not None and metric_class in (_Histogram, _Summary):
                return metric_class(
                    name, documentation, labelnames=labelnames, buckets=buckets
                )
            return metric_class(name, documentation, labelnames=labelnames)
        except ValueError:
            # Another import path already registered this name — retrieve it.
            try:
                existing = _REGISTRY._names_to_collectors.get(name)
                if existing is not None:
                    return existing
            except (AttributeError, KeyError, TypeError):
                pass
            logger.debug(
                "Metric %r already registered but could not be retrieved; "
                "returning no-op stub.",
                name,
            )
            return _DummyMetric()


def safe_counter(
    name: str,
    documentation: str,
    labelnames: Union[Tuple[str, ...], Sequence[str]] = (),
) -> Any:
    """Shorthand for ``get_or_create_metric(Counter, …)``.

    Identical to calling::

        get_or_create_metric(Counter, name, documentation, labelnames)

    Returns a :class:`DummyMetric` when ``prometheus_client`` is absent.
    """
    if not _ensure_prometheus():
        return _DummyMetric()
    return get_or_create_metric(_Counter, name, documentation, labelnames)


def safe_gauge(
    name: str,
    documentation: str,
    labelnames: Union[Tuple[str, ...], Sequence[str]] = (),
) -> Any:
    """Shorthand for ``get_or_create_metric(Gauge, …)``.

    Identical to calling::

        get_or_create_metric(Gauge, name, documentation, labelnames)

    Returns a :class:`DummyMetric` when ``prometheus_client`` is absent.
    """
    if not _ensure_prometheus():
        return _DummyMetric()
    return get_or_create_metric(_Gauge, name, documentation, labelnames)


def safe_histogram(
    name: str,
    documentation: str,
    labelnames: Union[Tuple[str, ...], Sequence[str]] = (),
    buckets: Optional[Tuple[float, ...]] = None,
) -> Any:
    """Shorthand for ``get_or_create_metric(Histogram, …)``.

    Returns a :class:`DummyMetric` when ``prometheus_client`` is absent.
    """
    if not _ensure_prometheus():
        return _DummyMetric()
    return get_or_create_metric(
        _Histogram, name, documentation, labelnames, buckets=buckets
    )


# ---------------------------------------------------------------------------
# No-op fallback — matches the full prometheus_client Metric interface
# ---------------------------------------------------------------------------


class _DummyMetric:
    """Lightweight no-op metric that silently accepts any Prometheus-style call.

    Implements the ``labels → child-metric → inc/dec/set/observe`` chain so
    that call-sites like ``MY_COUNTER.labels(kind="fix").inc()`` work without
    ``None`` checks even when ``prometheus_client`` is not installed.

    ``DEFAULT_BUCKETS`` mirrors ``prometheus_client.Histogram.DEFAULT_BUCKETS``
    so that code referencing it at class level does not raise.
    """

    DEFAULT_BUCKETS = (
        0.005,
        0.01,
        0.025,
        0.05,
        0.075,
        0.1,
        0.25,
        0.5,
        0.75,
        1.0,
        2.5,
        5.0,
        7.5,
        10.0,
        float("inf"),
    )

    def labels(self, *args: Any, **kwargs: Any) -> "_DummyMetric":
        return self

    def inc(self, *args: Any, **kwargs: Any) -> None:
        pass

    def dec(self, *args: Any, **kwargs: Any) -> None:
        pass

    def set(self, *args: Any, **kwargs: Any) -> None:
        pass

    def observe(self, *args: Any, **kwargs: Any) -> None:
        pass

    def time(self) -> "_DummyTimer":
        """Return a no-op context-manager for ``.time()`` usage."""
        return _DummyTimer()


class _DummyTimer:
    """No-op context manager returned by :meth:`_DummyMetric.time`."""

    def __enter__(self) -> "_DummyTimer":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


# Public alias so callers can write ``from omnicore_engine.metrics_utils import DummyMetric``
# and use it in type annotations or isinstance checks.
DummyMetric = _DummyMetric


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "get_or_create_metric",
    "safe_counter",
    "safe_gauge",
    "safe_histogram",
    "DummyMetric",
]
