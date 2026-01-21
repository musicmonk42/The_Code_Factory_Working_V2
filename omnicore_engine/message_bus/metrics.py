# omnicore_engine/message_bus/metrics.py
"""
Prometheus metric definitions for the ShardedMessageBus and its components.

Provides resilience by implementing mock metrics if the prometheus_client
library is unavailable, preventing import crashes.

Upgrades:
- Thread-safe mock metrics with in-memory counters for testing.
- Full histogram support in mocks (with buckets).
- Metric registry to prevent duplicate creation.
- Dynamic metric creation with type safety and default buckets.
- Health and integration metrics (Redis, Kafka, Guardian).
- Time-series helpers (e.g., latency context manager).
- Exportable registry for testing and scraping.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Generic, List, Optional, Tuple, TypeVar

logger = logging.getLogger(__name__)

# --- Optional Prometheus Metrics Import ---
try:
    from prometheus_client import REGISTRY, Counter, Gauge, Histogram
    from prometheus_client.registry import CollectorRegistry

    _PROMETHEUS_AVAILABLE = True
    logger.info("Prometheus client loaded. Real metrics enabled.")
except ImportError:
    # --- Mock Metric Implementation ---
    REGISTRY = None
    _PROMETHEUS_AVAILABLE = False
    logger.warning(
        "Prometheus client not found. Using thread-safe Mock Metrics with in-memory tracking."
    )

    class _MockRegistry:
        """
        Simulates Prometheus registry for testing.

        Improvements:
        - LRU eviction to prevent memory leaks from dynamic metrics
        - Configurable max size with warning when limit is approached
        - Memory usage tracking
        """

        def __init__(self, max_collectors: int = 1000):
            """
            Initialize mock registry with size limit.

            Args:
                max_collectors: Maximum number of metrics to track (default 1000)
            """
            self.collectors: List[Any] = []
            self.max_collectors = max_collectors
            self._warned_at_threshold = False

        def register(self, collector: Any):
            """Register a collector with LRU eviction if at capacity."""
            # Check if we're approaching the limit
            if (
                len(self.collectors) >= self.max_collectors * 0.9
                and not self._warned_at_threshold
            ):
                logger.warning(
                    f"Mock metric registry approaching capacity: "
                    f"{len(self.collectors)}/{self.max_collectors}. "
                    f"Consider using real Prometheus or reducing dynamic metric creation."
                )
                self._warned_at_threshold = True

            # If at capacity, remove oldest collector (LRU eviction)
            if len(self.collectors) >= self.max_collectors:
                removed = self.collectors.pop(0)
                logger.debug(
                    f"Mock metric registry at capacity. "
                    f"Evicting oldest metric: {getattr(removed, 'name', 'unknown')}"
                )

            self.collectors.append(collector)

        def unregister(self, collector: Any):
            if collector in self.collectors:
                self.collectors.remove(collector)

        def get_memory_usage(self) -> dict:
            """
            Return memory usage statistics for monitoring.

            Returns:
                Dict with collector count, capacity, and usage percentage
            """
            return {
                "collector_count": len(self.collectors),
                "max_collectors": self.max_collectors,
                "usage_percent": (len(self.collectors) / self.max_collectors) * 100,
            }

    REGISTRY = _MockRegistry()

    T = TypeVar("T")

    class _ThreadSafeDict(Generic[T]):
        """
        Thread-safe dictionary for mock metric storage with LRU eviction.

        Improvements:
        - Configurable max size to prevent unbounded growth
        - LRU eviction when capacity is reached
        - Access time tracking for proper LRU behavior
        """

        def __init__(self, max_size: int = 10000):
            """
            Initialize thread-safe dict with size limit.

            Args:
                max_size: Maximum number of label combinations to track
            """
            self._data: Dict[Tuple, T] = {}
            self._access_times: Dict[Tuple, float] = {}
            self._lock = threading.RLock()
            self.max_size = max_size
            self._warned_at_threshold = False

        def _evict_lru(self):
            """Evict least recently used entry."""
            if not self._access_times:
                # Fallback: remove arbitrary item if access times aren't tracked
                if self._data:
                    self._data.popitem()
                return

            # Find and remove least recently used key
            lru_key = min(self._access_times, key=self._access_times.get)
            self._data.pop(lru_key, None)
            self._access_times.pop(lru_key, None)

        def get(self, key: Tuple, default: T) -> T:
            with self._lock:
                # Only record access time if key exists to avoid ghost entries
                if key in self._data:
                    self._access_times[key] = time.time()
                    return self._data[key]
                return default

        def set(self, key: Tuple, value: T):
            with self._lock:
                # Check capacity and evict if needed (only for new keys when at capacity)
                if key not in self._data and len(self._data) >= self.max_size:
                    if (
                        len(self._data) >= self.max_size * 0.9
                        and not self._warned_at_threshold
                    ):
                        logger.warning(
                            f"Mock metric storage approaching capacity: "
                            f"{len(self._data)}/{self.max_size}. "
                            f"Using LRU eviction. Consider real Prometheus for production."
                        )
                        self._warned_at_threshold = True
                    self._evict_lru()

                self._data[key] = value
                self._access_times[key] = time.time()

        def inc(self, key: Tuple, amount: float = 1.0):
            with self._lock:
                # Check capacity and evict if needed
                if key not in self._data and len(self._data) >= self.max_size:
                    self._evict_lru()

                self._data[key] = self._data.get(key, 0.0) + amount
                self._access_times[key] = time.time()

        def items(self):
            with self._lock:
                return list(self._data.items())

        def clear(self):
            """Clear all data and reset warning flag."""
            with self._lock:
                self._data.clear()
                self._access_times.clear()
                self._warned_at_threshold = False

    class MockMetric:
        """A thread-safe, no-op placeholder for Prometheus metrics with in-memory tracking."""

        def __init__(
            self,
            name: str,
            documentation: str,
            labelnames: Optional[List[str]] = None,
            metric_type: str = "counter",
        ):
            self.name = name
            self.documentation = documentation
            self.labelnames = labelnames or []
            self.metric_type = metric_type
            self._lock = threading.RLock()
            self._values: _ThreadSafeDict[float] = _ThreadSafeDict()
            self._buckets: List[float] = []
            self._bucket_values: _ThreadSafeDict[float] = (
                _ThreadSafeDict() if metric_type == "histogram" else None
            )
            self._sum: float = 0.0
            self._count: int = 0
            if metric_type == "histogram":
                self._buckets = [
                    0.005,
                    0.01,
                    0.05,
                    0.1,
                    0.5,
                    1.0,
                    5.0,
                    10.0,
                    float("inf"),
                ]
            REGISTRY.register(self)

        def labels(self, **kwargs):
            """Returns a labeled version of the metric."""
            label_values = tuple(kwargs.get(label, "") for label in self.labelnames)
            return _LabeledMockMetric(self, label_values)

        def inc(self, amount: float = 1.0):
            with self._lock:
                if self.metric_type != "counter":
                    raise ValueError("inc() only valid for Counter")
                self._values.inc((), amount)

        def set(self, value: float):
            with self._lock:
                if self.metric_type != "gauge":
                    raise ValueError("set() only valid for Gauge")
                self._values.set((), value)

        def observe(self, value: float):
            with self._lock:
                if self.metric_type != "histogram":
                    raise ValueError("observe() only valid for Histogram")
                self._sum += value
                self._count += 1
                for bucket in self._buckets:
                    key = (bucket,)
                    if value <= bucket:
                        self._bucket_values.inc(key, 1.0)

        @contextmanager
        def time(self):
            start = time.time()
            try:
                yield
            finally:
                if self.metric_type == "histogram":
                    self.observe(time.time() - start)

        def _collect(self):
            """For registry export in tests."""
            yield self

    class _LabeledMockMetric:
        def __init__(self, parent: MockMetric, label_values: Tuple):
            self.parent = parent
            self.label_values = label_values

        def inc(self, amount: float = 1.0):
            if self.parent.metric_type != "counter":
                raise ValueError("inc() only valid for Counter")
            self.parent._values.inc(self.label_values, amount)

        def set(self, value: float):
            if self.parent.metric_type != "gauge":
                raise ValueError("set() only valid for Gauge")
            self.parent._values.set(self.label_values, value)

        def observe(self, value: float):
            if self.parent.metric_type != "histogram":
                raise ValueError("observe() only valid for Histogram")
            self.parent._sum += value
            self.parent._count += 1
            for bucket in self.parent._buckets:
                key = self.label_values + (bucket,)
                if value <= bucket:
                    self.parent._bucket_values.inc(key, 1.0)

        @contextmanager
        def time(self):
            start = time.time()
            try:
                yield
            finally:
                if self.parent.metric_type == "histogram":
                    self.observe(time.time() - start)

    def Counter(name, doc, labelnames=None):
        return MockMetric(name, doc, labelnames, "counter")

    def Gauge(name, doc, labelnames=None):
        return MockMetric(name, doc, labelnames, "gauge")

    def Histogram(name, doc, labelnames=None, buckets=None):
        return MockMetric(
            name, doc, labelnames, "histogram"
        )  # buckets ignored in mock, use default


# --- Metric Registry to Prevent Duplicates ---
_metric_registry: Dict[str, Any] = {}


def _get_or_create_metric(
    name: str,
    docstring: str,
    labelnames: Optional[List[str]] = None,
    metric_type: str = "counter",
    buckets: Optional[List[float]] = None,
) -> Any:
    """
    Thread-safe factory to create or retrieve a metric.
    Prevents duplicate metric errors in Prometheus.
    """
    key = f"{metric_type}:{name}"
    if key in _metric_registry:
        return _metric_registry[key]

    labelnames = labelnames or []
    buckets = buckets or (0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, float("inf"))

    if _PROMETHEUS_AVAILABLE:
        try:
            if metric_type == "counter":
                metric = Counter(name, docstring, labelnames)
            elif metric_type == "gauge":
                metric = Gauge(name, docstring, labelnames)
            elif metric_type == "histogram":
                metric = Histogram(name, docstring, labelnames, buckets=buckets)
            else:
                raise ValueError(f"Unknown metric_type: {metric_type}")
            _metric_registry[key] = metric
            return metric
        except ValueError as e:
            if "Duplicate" in str(e):
                logger.debug(f"Metric {name} already registered. Reusing.")
                return REGISTRY._names_to_collectors[name]
            raise
    else:
        metric = {
            "counter": lambda: Counter(name, docstring, labelnames),
            "gauge": lambda: Gauge(name, docstring, labelnames),
            "histogram": lambda: Histogram(name, docstring, labelnames),
        }[metric_type]()
        _metric_registry[key] = metric
        return metric


# --- Shared Metric Definitions (Lazy Initialization) ---

# Storage for lazily-initialized metrics
_lazy_metrics: Dict[str, Any] = {}


def _lazy_get_or_create_metric(metric_name: str, *args) -> Any:
    """
    Lazy wrapper that creates metrics on first access.
    
    This defers expensive Prometheus metric creation (with threading locks
    and registry operations) until the metric is actually used, preventing
    import-time overhead that causes CI timeouts.
    
    Args:
        metric_name: Name of the metric to create
        *args: Arguments to pass to _get_or_create_metric
        
    Returns:
        The metric object (created on first access, cached thereafter)
    """
    if metric_name not in _lazy_metrics:
        _lazy_metrics[metric_name] = _get_or_create_metric(*args)
    return _lazy_metrics[metric_name]


# Metric definitions dictionary for lazy loading via __getattr__
_METRIC_DEFINITIONS = {
    # 1. ShardedMessageBus Core Metrics
    "MESSAGE_BUS_QUEUE_SIZE": (
        "message_bus_queue_size",
        "Current size of internal message queues",
        ["shard_id", "queue_type"],  # normal or high_priority
        "gauge",
    ),
    "MESSAGE_BUS_DISPATCH_DURATION": (
        "message_bus_dispatch_duration_seconds",
        "Time taken to dispatch a message to all internal and external subscribers",
        ["shard_id"],
        "histogram",
        (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, float("inf")),
    ),
    "MESSAGE_BUS_TOPIC_THROUGHPUT": (
        "message_bus_topic_throughput_total",
        "Total messages processed per topic (post-dispatch)",
        ["topic"],
        "counter",
    ),
    "MESSAGE_BUS_CALLBACK_ERRORS": (
        "message_bus_callback_errors_total",
        "Total exceptions raised by message subscribers",
        ["shard_id", "topic", "error_type"],
        "counter",
    ),
    "MESSAGE_BUS_PUBLISH_RETRIES": (
        "message_bus_publish_retries_total",
        "Total retries due to queue backpressure or transient failure",
        ["shard_id", "reason"],
        "counter",
    ),
    "MESSAGE_BUS_MESSAGE_AGE": (
        "message_bus_message_age_seconds",
        "Time elapsed between message creation and dispatch (consumer lag)",
        ["shard_id", "priority"],
        "histogram",
        (0.005, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, float("inf")),
    ),
    "MESSAGE_BUS_CALLBACK_LATENCY": (
        "message_bus_callback_latency_seconds",
        "Time spent executing a single subscriber callback",
        ["topic", "handler"],
        "histogram",
        (0.001, 0.01, 0.05, 0.1, 0.5, 1.0, float("inf")),
    ),
    # 2. Guardian/Health Metrics
    "MESSAGE_BUS_HEALTH_STATUS": (
        "message_bus_health_status",
        "Current health status of the message bus (1=Healthy, 0=Degraded, -1=Critical)",
        [],
        "gauge",
    ),
    "MESSAGE_BUS_CRITICAL_FAILURES_TOTAL": (
        "message_bus_critical_failures_total",
        "Total number of times the message bus hit the critical failure threshold",
        ["component"],
        "counter",
    ),
    # 3. Integration Metrics (Redis, Kafka)
    "MESSAGE_BUS_REDIS_PUBLISH_TOTAL": (
        "message_bus_redis_publish_total",
        "Total messages published to Redis",
        ["result", "topic"],
        "counter",
    ),
    "MESSAGE_BUS_REDIS_CONSUME_TOTAL": (
        "message_bus_redis_consume_total",
        "Total messages consumed from Redis",
        ["result", "topic"],
        "counter",
    ),
    "MESSAGE_BUS_KAFKA_PRODUCE_TOTAL": (
        "message_bus_kafka_produce_total",
        "Total messages produced to Kafka",
        ["result", "topic"],
        "counter",
    ),
    "MESSAGE_BUS_KAFKA_CONSUME_TOTAL": (
        "message_bus_kafka_consume_total",
        "Total messages consumed from Kafka",
        ["result", "topic"],
        "counter",
    ),
    "MESSAGE_BUS_KAFKA_LAG": (
        "message_bus_kafka_consumer_lag",
        "Consumer lag per partition",
        ["topic", "partition"],
        "gauge",
    ),
    # 4. Resilience Metrics
    "MESSAGE_BUS_CIRCUIT_STATE": (
        "message_bus_circuit_state",
        "State of circuit breakers (0=closed, 1=open, 2=half-open)",
        ["component"],
        "gauge",
    ),
    "MESSAGE_BUS_DLQ_TOTAL": (
        "message_bus_dlq_total",
        "Total messages sent to dead letter queue",
        ["topic", "reason"],
        "counter",
    ),
}


def __getattr__(name: str) -> Any:
    """
    Lazy load metrics on first module attribute access (PEP 562).
    
    This allows metrics to be imported and used exactly as before:
        from omnicore_engine.message_bus.metrics import MESSAGE_BUS_QUEUE_SIZE
        MESSAGE_BUS_QUEUE_SIZE.labels(shard_id="0").set(10)
    
    But defers the expensive metric creation until the metric is actually accessed,
    preventing the 18+ second import-time overhead that causes CI timeouts.
    
    Args:
        name: Name of the module attribute being accessed
        
    Returns:
        The requested metric object
        
    Raises:
        AttributeError: If the attribute doesn't exist in metric definitions
    """
    if name in _METRIC_DEFINITIONS:
        return _lazy_get_or_create_metric(name, *_METRIC_DEFINITIONS[name])
    
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


# --- Helper Context Managers ---
@contextmanager
def timer(metric: Histogram, **labels):
    """Context manager to time operations and observe in histogram."""
    start = time.time()
    try:
        yield
    finally:
        duration = time.time() - start
        if labels:
            metric.labels(**labels).observe(duration)
        else:
            metric.observe(duration)


@contextmanager
def dispatch_timer(shard_id: int):
    """Specialized timer for dispatch duration."""
    # Access the lazy-loaded metric via __getattr__
    dispatch_duration_metric = __getattr__("MESSAGE_BUS_DISPATCH_DURATION")
    with timer(dispatch_duration_metric, shard_id=str(shard_id)):
        yield


# --- Export for Testing ---
def reset_metrics():
    """
    Clears all mock metric values (for unit tests).

    Uses the new clear() method which also resets warning flags.
    """
    if not _PROMETHEUS_AVAILABLE:
        for metric in _metric_registry.values():
            if hasattr(metric, "_values") and hasattr(metric._values, "clear"):
                metric._values.clear()
            if hasattr(metric, "_bucket_values") and metric._bucket_values:
                if hasattr(metric._bucket_values, "clear"):
                    metric._bucket_values.clear()
            if hasattr(metric, "_sum"):
                metric._sum = 0.0
                metric._count = 0
        logger.debug("Mock metrics reset.")


def get_mock_metric_values(name: str) -> Dict[Tuple, float]:
    """Returns in-memory values for a mock metric (for assertions in tests)."""
    if _PROMETHEUS_AVAILABLE:
        raise RuntimeError("get_mock_metric_values only works in mock mode")
    key = f"counter:{name}"
    if key not in _metric_registry:
        return {}
    metric = _metric_registry[key]
    return dict(metric._values.items())


def get_mock_registry_stats() -> dict:
    """
    Get statistics about the mock registry for monitoring.

    Returns:
        Dict with registry usage statistics

    Raises:
        RuntimeError: If called when Prometheus is available
    """
    if _PROMETHEUS_AVAILABLE:
        raise RuntimeError("get_mock_registry_stats only works in mock mode")
    return REGISTRY.get_memory_usage()


# Aliases for backward compatibility
Metric = Counter
_get_or_create_metric_for_resilience = _get_or_create_metric
