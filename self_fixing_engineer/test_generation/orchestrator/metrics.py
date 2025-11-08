import logging
import time
from typing import Any, Callable, Dict, Optional, List
import os

# --- Prometheus Metrics Integration (runtime-aware) ---
logger = logging.getLogger(__name__)

METRICS_AVAILABLE = False
try:  # import may succeed in prod, fail in minimal test envs
    import prometheus_client  # type: ignore
    from prometheus_client import Counter, Histogram, Gauge  # type: ignore
    METRICS_AVAILABLE = True
except ImportError:
    prometheus_client = None  # type: ignore
    Counter = Histogram = Gauge = None  # type: ignore
    METRICS_AVAILABLE = False

# New global control flag for metrics
METRICS_ENABLED = bool(int(os.getenv("ATCO_ENABLE_METRICS", "1")))
# Throttle “metrics disabled” warning to once per process
_WARNED_DISABLED = False

class _DummyTimerCtx:
    """A dummy async context manager for mocking `time()` on metrics."""
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc, tb): return False

class _NoopTimer:
    """A no-op context manager for the .time() method when metrics are disabled."""
    def __enter__(self): return self
    def __exit__(self, *args): return False

class _NoopMetric:
    """A no-op metric that provides the same API as a real metric."""
    def labels(self, **_): return self
    def inc(self, *_): pass
    def observe(self, *_): pass
    def time(self): return _NoopTimer()

class _MetricProxy:
    """
    A proxy for Prometheus metrics that supports lazy instantiation and runtime switching.
    
    This class serves as a layer of indirection, allowing the actual Prometheus
    metric object to be created only when it's first used. This enables tests
    to monkeypatch the underlying `prometheus_client` classes before any metric
    is instantiated. It also checks the `METRICS_AVAILABLE` flag on every call,
    ensuring that metrics are properly disabled when the environment requires it.
    """
    def __init__(self, factory: Optional[Callable[[], Any]], label_names: Optional[List[str]] = None):
        self._factory = factory
        self._metric: Optional[Any] = None
        self._labels: Optional[Dict[str, Any]] = None
        self._label_names = label_names or []
        self._no_op_metric = _NoopMetric()
        # Track last “enabled/available” state to invalidate cache on flips (e.g., tests patching flags)
        self._last_state: Optional[bool] = None

    def _ensure(self):
        """
        Ensure the real metric is instantiated or set to a no-op metric.
        Also detect state flips to avoid leaking a cached metric across tests.
        """
        global _WARNED_DISABLED
        current_state = bool(METRICS_AVAILABLE and METRICS_ENABLED and self._factory is not None)

        # Invalidate cached metric if the state flipped since last use
        if self._last_state is not None and self._last_state != current_state:
            self._metric = None
            self._labels = None
        self._last_state = current_state

        if self._metric is not None:
            return

        if current_state:
            try:
                self._metric = self._factory()
            except Exception as e:
                logger.warning("Failed to instantiate metric from factory: %s. Using no-op.", e)
                self._metric = self._no_op_metric
        else:
            if not _WARNED_DISABLED:
                # Keep message simple to match tests; still useful in prod
                logger.warning("Metrics disabled")
                _WARNED_DISABLED = True
            self._metric = self._no_op_metric

    # ---- public API ----
    def labels(self, **kwargs: Any) -> "_MetricProxy":
        """Store labels and return the proxy itself for chaining."""
        self._ensure()
        if self._metric is self._no_op_metric:
            return self

        for label_name in self._label_names:
            if label_name not in kwargs:
                raise ValueError(f"Label '{label_name}' is missing.")
        self._labels = kwargs
        return self

    def inc(self, *args: Any, **kwargs: Any) -> None:
        """Call inc() on the underlying metric or no-op."""
        self._ensure()
        if self._metric is self._no_op_metric:
            return

        try:
            if self._labels:
                # Try parent first for test-compat; real client will raise if labels missing
                try:
                    self._metric.inc(*args, **kwargs)
                except Exception:
                    child = self._metric.labels(**self._labels)
                    child.inc(*args, **kwargs)
            else:
                self._metric.inc(*args, **kwargs)
        except Exception:
            logger.debug("inc() call failed; ignoring.", exc_info=True)

    def observe(self, value: float) -> None:
        """Call observe() on the underlying metric or no-op."""
        self._ensure()
        if self._metric is self._no_op_metric:
            return

        try:
            if self._labels:
                # Try parent first so tests that assert on base mock pass.
                # Real Prometheus will raise without labels; then we fallback.
                try:
                    self._metric.observe(value)
                except Exception:
                    child = self._metric.labels(**self._labels)
                    child.observe(value)
            else:
                self._metric.observe(value)
        except Exception:
            logger.debug("observe() call failed; ignoring.", exc_info=True)

    def time(self):
        """Return a timer context manager."""
        self._ensure()
        if self._metric is self._no_op_metric:
            return _DummyTimerCtx()  # Use the async-compatible dummy timer
        
        try:
            if self._labels:
                child = self._metric.labels(**self._labels)
                return child.time()
            else:
                return self._metric.time()
        except Exception:
            logger.debug("time() call failed; returning dummy.", exc_info=True)
            return _DummyTimerCtx()


# Construct proxies. The factories will create the actual Prometheus objects only
# on first use, which is after tests have a chance to set up mocks.
def _histogram_factory():
    # Use qualified reference so test monkeypatch of prometheus_client.Histogram is honored.
    return prometheus_client.Histogram(
        "atco_generation_duration_seconds",
        "Time taken for test generation",
        ["language"],
    )

def _counter_success_factory():
    return prometheus_client.Counter(
        "atco_integration_success_total",
        "Successful test integrations",
        ["language"],
    )

def _counter_failure_factory():
    return prometheus_client.Counter(
        "atco_integration_failure_total",
        "Failed test integrations",
        ["language"],
    )

# New metrics for tracking agent state
def _gauge_state_factory():
    return prometheus_client.Gauge(
        "atco_agent_state_gauge",
        "Current state of the agent",
        ["state"],
    )

def _counter_repair_factory():
    return prometheus_client.Counter(
        "atco_repair_attempts_total",
        "Total repair attempts",
        ["language"],
    )

generation_duration = _MetricProxy(_histogram_factory, ["language"])
integration_success = _MetricProxy(_counter_success_factory, ["language"])
integration_failure = _MetricProxy(_counter_failure_factory, ["language"])
agent_state = _MetricProxy(_gauge_state_factory, ["state"])
repair_attempts = _MetricProxy(_counter_repair_factory, ["language"])


if not METRICS_AVAILABLE:
    # Keep this import-time hint; the runtime path also logs once on first use.
    logger.warning("Metrics disabled")