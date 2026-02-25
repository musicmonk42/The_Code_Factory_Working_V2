# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Canonical no-op Prometheus metric stubs for the Code Factory platform.

Problem
-------
Fifteen or more independent implementations of the same no-op Prometheus
metric pattern were scattered across the codebase:

- ``omnicore_engine/sharding.py``
- ``server/routers/jobs_ws.py``
- ``generator/main/post_materialize.py``
- ``generator/runner/runner_file_utils.py``
- ``generator/runner/runner_audit.py``
- ``self_fixing_engineer/main.py``
- ``self_fixing_engineer/mesh/graph_rag_policy.py``
- ``self_fixing_engineer/simulation/plugins/web_ui_dashboard_plugin_template.py``

Every copy was slightly different — different method sets, different
docstrings, different fallback strategies — creating a maintenance burden
and subtle behavioural inconsistencies.

Solution
--------
This module provides a single, production-quality implementation with:

* **Thread-safe, idempotent metric creation** — a module-level
  ``threading.Lock`` serialises the create-or-retrieve path and prevents
  duplicate registration races.
* **Lazy ``prometheus_client`` import** — the library is resolved on first
  use of :func:`safe_metric`, not at module-load time, keeping
  ``pytest --collect-only`` fast.
* **Graceful degradation** — when ``prometheus_client`` is absent every
  call silently returns the :data:`NOOP` singleton, so callers never need
  ``if metric is None`` guards.
* **Full Prometheus interface** — :class:`NoopMetric` mirrors the complete
  ``Counter / Gauge / Histogram / Summary`` child-metric surface
  (``labels``, ``inc``, ``dec``, ``set``, ``observe``, ``time``,
  ``collect``, ``clear``, ``DEFAULT_BUCKETS``).
* **Zero external dependencies at import time** — only stdlib modules
  are imported at the top level.

Architecture
------------
::

    Caller
      │
      ├── safe_metric(Counter, "my_total", "desc", labelnames=["env"])
      │         │
      │         ├── prometheus_client available? ──Yes──► Counter("my_total", ...)
      │         │                                               │
      │         │         already registered?  ──Yes──► REGISTRY lookup
      │         │
      │         └── prometheus_client absent? ──────────► NOOP  ◄─┐
      │                                                            │
      └── direct NOOP usage ──────────────────────────────────────┘
              │
              ├── .labels("prod") ──► NOOP   (returns self)
              ├── .inc()           ──► None
              ├── .observe(0.042)  ──► None
              └── .time()          ──► NoopTimer (context manager no-op)

Usage
-----
::

    from shared.noop_metrics import NoopMetric, NOOP, safe_metric

    # Direct singleton — guaranteed no-op
    requests_total = NOOP
    requests_total.labels(env="prod").inc()   # silent

    # Idempotent factory — real metric when prometheus_client is present
    try:
        from prometheus_client import Counter
    except ImportError:
        Counter = None

    MY_COUNTER = safe_metric(
        Counter,
        "platform_requests_total",
        "Total platform requests",
        labelnames=["service", "status"],
    )
    MY_COUNTER.labels(service="generator", status="200").inc()

Industry Standards Applied
--------------------------
* **Prometheus Data Model** — ``DEFAULT_BUCKETS`` mirrors
  ``prometheus_client.Histogram.DEFAULT_BUCKETS``.
* **PEP 484** — full type annotations for public API.
* **PEP 517 / 518** — zero mandatory runtime dependencies.
* **Twelve-Factor App** — configuration via environment, not hard-coded
  library availability assumptions.

See Also
--------
:mod:`omnicore_engine.metrics_utils` — the more feature-rich counterpart
for callers that need per-type helpers (``safe_counter``, ``safe_gauge``,
``safe_histogram``) and explicit thread-locking over the full metric
lifecycle.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Iterator, List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level lock — serialises metric creation so two threads racing to
# register the same name do not both attempt the allocation.
# ---------------------------------------------------------------------------
_metrics_lock = threading.Lock()


# ---------------------------------------------------------------------------
# No-op timer — returned by NoopMetric.time()
# ---------------------------------------------------------------------------


class NoopTimer:
    """Context manager returned by :meth:`NoopMetric.time` — does nothing.

    Implements the same ``__enter__`` / ``__exit__`` contract as
    ``prometheus_client``'s ``_Timer`` so it is safe to use in
    ``with metric.time():`` blocks.
    """

    def __enter__(self) -> "NoopTimer":
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def __repr__(self) -> str:  # pragma: no cover
        return "NoopTimer()"


# ---------------------------------------------------------------------------
# Universal no-op metric
# ---------------------------------------------------------------------------


class NoopMetric:
    """Universal no-op metric that silently accepts any Prometheus-style call.

    Implements the ``labels → child-metric → inc/dec/set/observe`` chain so
    that call-sites like ``MY_COUNTER.labels(kind="fix").inc()`` work without
    ``None`` checks, regardless of whether ``prometheus_client`` is installed.

    ``DEFAULT_BUCKETS`` mirrors ``prometheus_client.Histogram.DEFAULT_BUCKETS``
    so that code referencing the class attribute does not raise
    ``AttributeError``.

    This class is intentionally public so that call-sites can use it in type
    annotations::

        from shared.noop_metrics import NoopMetric
        my_metric: NoopMetric = NOOP
    """

    DEFAULT_BUCKETS: tuple = (
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

    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    # ------------------------------------------------------------------
    # Label child — returns *self* so the chain .labels(...).inc() works
    # ------------------------------------------------------------------

    def labels(self, *_: Any, **__: Any) -> "NoopMetric":
        """Return *self* — no child metric is ever created."""
        return self

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    def inc(self, *_: Any, **__: Any) -> None:
        """No-op increment."""
        pass

    def dec(self, *_: Any, **__: Any) -> None:
        """No-op decrement."""
        pass

    def set(self, *_: Any, **__: Any) -> None:
        """No-op set."""
        pass

    def observe(self, *_: Any, **__: Any) -> None:
        """No-op observation."""
        pass

    # ------------------------------------------------------------------
    # Timer context manager
    # ------------------------------------------------------------------

    def time(self, *_: Any, **__: Any) -> NoopTimer:
        """Return a no-op context manager compatible with ``.time()`` usage."""
        return NoopTimer()

    # ------------------------------------------------------------------
    # Collection / registry helpers (used by some callers)
    # ------------------------------------------------------------------

    def collect(self, *_: Any, **__: Any) -> List[Any]:
        """Return an empty metric-family list."""
        return []

    def clear(self, *_: Any, **__: Any) -> None:
        """No-op clear."""
        pass

    def __repr__(self) -> str:  # pragma: no cover
        return "NoopMetric()"

    def __iter__(self) -> Iterator[Any]:  # pragma: no cover
        """Support ``for sample in metric`` iteration used by some collectors."""
        return iter([])


# ---------------------------------------------------------------------------
# Public singleton
# ---------------------------------------------------------------------------

#: Shared singleton no-op metric.  Prefer this over creating ``NoopMetric()``
#: instances directly — it is safe for concurrent use.
NOOP: NoopMetric = NoopMetric()


# ---------------------------------------------------------------------------
# Idempotent metric factory
# ---------------------------------------------------------------------------


def safe_metric(
    factory: Any,
    name: str,
    doc: str,
    labelnames: Optional[Union[List[str], Sequence[str]]] = None,
    buckets: Optional[Union[List[float], Sequence[float]]] = None,
) -> Any:
    """Create a Prometheus metric idempotently; return :data:`NOOP` on failure.

    This function is the primary public interface for modules that need a
    Prometheus metric but must degrade gracefully when ``prometheus_client``
    is not installed (e.g. in lightweight worker images or test environments).

    The implementation follows the same two-phase *lookup → create →
    collision-fallback* pattern as
    :func:`omnicore_engine.metrics_utils.get_or_create_metric`.

    Parameters
    ----------
    factory : type | None
        A ``prometheus_client`` metric class — ``Counter``, ``Gauge``,
        ``Histogram``, or ``Summary``.  Pass ``None`` to always receive
        :data:`NOOP` (useful when the class could not be imported).
    name : str
        Prometheus metric name, e.g. ``"http_requests_total"``.
    doc : str
        Human-readable documentation string shown in ``/metrics`` output.
    labelnames : list[str] | tuple[str, ...] | None
        Optional label dimension names.  Accepts both lists and tuples for
        caller convenience.
    buckets : list[float] | tuple[float, ...] | None
        Optional histogram bucket boundaries forwarded verbatim to the
        ``Histogram`` constructor.

    Returns
    -------
    Any
        The real ``prometheus_client`` metric collector, or :data:`NOOP`
        when the factory is ``None``, Prometheus is not installed, or
        registration fails.

    Notes
    -----
    * The function is **thread-safe** — a module-level :class:`threading.Lock`
      serialises the creation step.
    * Calling the function with the same *name* multiple times is safe: the
      existing collector is retrieved from ``REGISTRY`` on the second call.
    * ``buckets`` is forwarded only when present; this keeps the function
      compatible with ``Counter`` / ``Gauge`` as well as ``Histogram``.

    Examples
    --------
    ::

        try:
            from prometheus_client import Counter, Histogram
        except ImportError:
            Counter = Histogram = None

        REQUESTS = safe_metric(
            Counter,
            "api_requests_total",
            "Total API requests",
            labelnames=["endpoint", "status"],
        )
        LATENCY = safe_metric(
            Histogram,
            "api_request_duration_seconds",
            "API request latency",
            buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
        )

        REQUESTS.labels(endpoint="/generate", status="200").inc()
        LATENCY.observe(0.042)
    """
    if factory is None:
        return NOOP

    kw: Dict[str, Any] = {}
    if labelnames:
        kw["labelnames"] = list(labelnames)
    if buckets is not None:
        kw["buckets"] = list(buckets)

    with _metrics_lock:
        # --- Fast path: already registered --------------------------------
        try:
            from prometheus_client import REGISTRY as _R  # type: ignore[import]

            if hasattr(_R, "_names_to_collectors") and name in _R._names_to_collectors:
                existing = _R._names_to_collectors[name]
                if factory is not None and not isinstance(existing, factory):
                    logger.warning(
                        "Metric %r already registered as %s but %s was requested; "
                        "returning existing metric.",
                        name,
                        type(existing).__name__,
                        getattr(factory, "__name__", str(factory)),
                    )
                return existing
        except ImportError:
            logger.debug(
                "prometheus_client not installed — returning NOOP for %r", name
            )
            return NOOP
        except (AttributeError, KeyError, TypeError):
            pass  # Registry internals may vary across prometheus_client versions

        # --- Slow path: create the metric ----------------------------------
        try:
            return factory(name, doc, **kw)
        except ValueError:
            # Narrow race: another thread registered *name* between our check
            # and our create.  Retrieve the existing collector.
            try:
                from prometheus_client import REGISTRY as _R2  # type: ignore[import]

                existing = _R2._names_to_collectors.get(name)  # type: ignore[union-attr]
                if existing is not None:
                    return existing
            except Exception:
                pass
            logger.debug(
                "Metric %r already registered but could not be retrieved; "
                "returning NOOP stub.",
                name,
            )
            return NOOP
        except Exception as exc:
            logger.debug(
                "Failed to create metric %r (%s: %s); returning NOOP stub.",
                name,
                type(exc).__name__,
                exc,
            )
            return NOOP


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "NoopMetric",
    "NoopTimer",
    "NOOP",
    "safe_metric",
]
