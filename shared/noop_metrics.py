# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Canonical no-op Prometheus metric stubs.

Use these when Prometheus is not available or when a lightweight fallback
is needed without taking a dependency on ``prometheus_client``.

Usage::

    from shared.noop_metrics import NoopMetric, NOOP, safe_metric

    # Drop-in no-op that accepts any call silently
    metric = NOOP

    # Idempotent factory with automatic NOOP fallback
    counter = safe_metric(Counter, "my_counter_total", "Description", labelnames=["status"])
"""

from __future__ import annotations

import contextlib
from typing import Any, Dict, List, Optional, Sequence, Union


class NoopTimer:
    """Context manager returned by :meth:`NoopMetric.time` — does nothing."""

    def __enter__(self) -> "NoopTimer":
        return self

    def __exit__(self, *_: Any) -> None:
        pass


class NoopMetric:
    """Universal no-op metric that silently accepts any Prometheus-style call."""

    DEFAULT_BUCKETS = (
        0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0,
        2.5, 5.0, 7.5, 10.0, float("inf"),
    )

    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    def labels(self, *_: Any, **__: Any) -> "NoopMetric":
        return self

    def inc(self, *_: Any, **__: Any) -> None:
        pass

    def dec(self, *_: Any, **__: Any) -> None:
        pass

    def set(self, *_: Any, **__: Any) -> None:
        pass

    def observe(self, *_: Any, **__: Any) -> None:
        pass

    def time(self, *_: Any, **__: Any) -> NoopTimer:
        return NoopTimer()

    def collect(self, *_: Any, **__: Any) -> List[Any]:
        return []

    def clear(self, *_: Any, **__: Any) -> None:
        pass


#: Singleton no-op metric instance.
NOOP: NoopMetric = NoopMetric()


def safe_metric(
    factory: Any,
    name: str,
    doc: str,
    labelnames: Optional[Union[List[str], Sequence[str]]] = None,
    buckets: Optional[Union[List[float], Sequence[float]]] = None,
) -> Any:
    """Create a Prometheus metric idempotently; return :data:`NOOP` if unavailable.

    Args:
        factory: A Prometheus metric class (e.g. ``Counter``, ``Histogram``).
        name: Metric name.
        doc: Help text / documentation string.
        labelnames: Optional list of label names.
        buckets: Optional histogram bucket sequence.

    Returns:
        The newly created (or already registered) metric, or :data:`NOOP` when
        ``factory`` is ``None`` or Prometheus is not installed.
    """
    if factory is None:
        return NOOP
    kw: Dict[str, Any] = {}
    if labelnames:
        kw["labelnames"] = labelnames
    if buckets is not None:
        kw["buckets"] = buckets
    try:
        return factory(name, doc, **kw)
    except ValueError:
        # Metric already registered — look it up from the default registry.
        with contextlib.suppress(Exception):
            from prometheus_client import REGISTRY as _R  # type: ignore[import]

            return _R._names_to_collectors.get(name, NOOP)
        return NOOP
    except Exception:
        return NOOP
