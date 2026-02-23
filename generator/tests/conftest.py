# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Conftest for generator tests - ensures prometheus stubs are initialized before test imports.

This conftest is loaded by pytest before any test files in this directory,
ensuring that prometheus_client stubs are available during test collection.
"""

import sys
import os
import gc
import importlib.machinery
import importlib.util
import multiprocessing
import threading
from unittest.mock import MagicMock, Mock

import pytest


# ---- CRITICAL: Mock watchdog Observer BEFORE any imports ----
# This prevents real observers from being created during test collection/execution
class MockObserver:
    """Mock Observer that doesn't create background threads."""
    def __init__(self, *args, **kwargs):
        self._handlers = {}
        self._is_alive = False
    
    def schedule(self, handler, path, recursive=False):
        self._handlers[path] = handler
    
    def unschedule(self, watch):
        pass
    
    def unschedule_all(self):
        self._handlers.clear()
    
    def start(self):
        self._is_alive = True
    
    def stop(self):
        self._is_alive = False
    
    def join(self, timeout=None):
        pass
    
    def is_alive(self):
        return self._is_alive


class MockFileSystemEventHandler:
    """Mock FileSystemEventHandler."""
    def __init__(self, *args, **kwargs):
        pass
    
    def on_created(self, event):
        pass
    
    def on_modified(self, event):
        pass
    
    def on_deleted(self, event):
        pass
    
    def on_moved(self, event):
        pass
    
    def on_any_event(self, event):
        pass


# Create mock watchdog modules with proper attributes for pytest
_mock_watchdog_observers = MagicMock()
_mock_watchdog_observers.__path__ = []  # Required for package-like behavior
_mock_watchdog_observers.__name__ = 'watchdog.observers'
_mock_watchdog_observers.Observer = MockObserver

_mock_watchdog_events = MagicMock()
_mock_watchdog_events.__path__ = []  # Required for package-like behavior
_mock_watchdog_events.__name__ = 'watchdog.events'
_mock_watchdog_events.FileSystemEventHandler = MockFileSystemEventHandler
_mock_watchdog_events.FileCreatedEvent = MagicMock
_mock_watchdog_events.FileModifiedEvent = MagicMock
_mock_watchdog_events.FileDeletedEvent = MagicMock
_mock_watchdog_events.FileMovedEvent = MagicMock

# Create parent watchdog module to ensure full hierarchy
_mock_watchdog = MagicMock()
_mock_watchdog.__path__ = []
_mock_watchdog.__name__ = 'watchdog'
_mock_watchdog.observers = _mock_watchdog_observers
_mock_watchdog.events = _mock_watchdog_events

# Pre-register the mocks BEFORE any code imports watchdog
# This ensures that when modules do `from watchdog.observers import Observer`,
# they get our mock instead of the real one
if os.environ.get("TESTING") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
    sys.modules["watchdog"] = _mock_watchdog
    sys.modules["watchdog.observers"] = _mock_watchdog_observers
    sys.modules["watchdog.events"] = _mock_watchdog_events


# ---- CRITICAL: Save original runner module references ----
# Some test files (test_agents_docgen_*.py, test_intent_parser_*.py, etc.)
# replace sys.modules["runner"] and sys.modules["runner.*"] with MagicMock()
# at module level during import. This poisons sys.modules for ALL subsequent
# test files, causing "cannot import from runner.runner_errors" errors.
# We save the originals here (before any test files are imported) so they
# can be restored by the individual test files after their imports complete.
_RUNNER_MODULE_KEYS = [
    "runner", "runner.llm_client", "runner.runner_logging",
    "runner.runner_metrics", "runner.runner_file_utils",
    "runner.summarize_utils", "runner.runner_errors",
    "runner.runner_security_utils", "runner.tracer",
    "runner.runner_audit",
]
_ORIGINAL_RUNNER_MODULES = {}
for _key in _RUNNER_MODULE_KEYS:
    if _key in sys.modules:
        _ORIGINAL_RUNNER_MODULES[_key] = sys.modules[_key]


def restore_runner_modules():
    """Restore original runner modules in sys.modules.

    Called by test files that mock runner modules at module level
    to prevent pollution of subsequent test files.
    """
    for k in _RUNNER_MODULE_KEYS:
        if k in _ORIGINAL_RUNNER_MODULES:
            sys.modules[k] = _ORIGINAL_RUNNER_MODULES[k]
        else:
            sys.modules.pop(k, None)


# Initialize prometheus_client stubs inline BEFORE importing root conftest
# This ensures stubs exist before test files are imported
if "prometheus_client" not in sys.modules:
    try:
        import prometheus_client as _test
    except ImportError:
        # Create prometheus_client package stub
        prom_spec = importlib.machinery.ModuleSpec(name="prometheus_client", loader=None, is_package=True)
        prom_module = importlib.util.module_from_spec(prom_spec)
        prom_module.__file__ = "<mocked prometheus_client>"
        prom_module.__path__ = []
        sys.modules["prometheus_client"] = prom_module
        
        # Create core submodule
        core_spec = importlib.machinery.ModuleSpec(name="prometheus_client.core", loader=None, is_package=False)
        prom_core = importlib.util.module_from_spec(core_spec)
        prom_core.__file__ = "<mocked prometheus_client.core>"
        sys.modules["prometheus_client.core"] = prom_core
        prom_module.core = prom_core
        
        # Create registry submodule
        registry_spec = importlib.machinery.ModuleSpec(name="prometheus_client.registry", loader=None, is_package=False)
        prom_registry = importlib.util.module_from_spec(registry_spec)
        prom_registry.__file__ = "<mocked prometheus_client.registry>"
        sys.modules["prometheus_client.registry"] = prom_registry
        prom_module.registry = prom_registry
        
        # Add mock classes
        class _MockHistogramMetricFamily:
            def __init__(self, *args, **kwargs): pass
        
        class _MockCollectorRegistry:
            def __init__(self, *args, **kwargs):
                self._names_to_collectors = {}
                self._collector_to_names = {}
            
            def register(self, collector):
                """Register a collector with this registry."""
                # Store the collector by its name
                if hasattr(collector, 'name'):
                    self._names_to_collectors[collector.name] = collector
                    self._collector_to_names[collector] = collector.name
            
            def unregister(self, collector):
                """Unregister a collector from this registry."""
                if collector in self._collector_to_names:
                    name = self._collector_to_names[collector]
                    del self._names_to_collectors[name]
                    del self._collector_to_names[collector]
            
            def get_sample_value(self, metric_name, labels=None):
                """Get the value of a specific metric sample."""
                # Find the collector by name
                if metric_name not in self._names_to_collectors:
                    return None
                
                collector = self._names_to_collectors[metric_name]
                
                # Collect samples from the collector
                try:
                    for metric in collector.collect():
                        for sample in metric.samples:
                            # Check if labels match
                            if labels is None or sample.labels == labels:
                                return sample.value
                except (AttributeError, TypeError):
                    pass
                
                return None
        
        # Helper function to safely convert label_key to dict
        def _label_key_to_dict(label_key):
            """Convert label_key (tuple of tuples) to dict, with fallback for unexpected structures.
            
            Args:
                label_key: A tuple of (key, value) tuples representing metric labels
                
            Returns:
                dict: Labels as a dictionary, or empty dict if conversion fails
            """
            try:
                return dict(label_key) if label_key else {}
            except (TypeError, ValueError):
                # Fallback if label_key structure is unexpected
                return {}
        
        class _Sample:
            """Mock Prometheus sample representing a single metric data point.
            
            Args:
                name: Metric name
                labels: Dictionary of label key-value pairs
                value: The metric value
                timestamp: Unix timestamp in seconds since epoch, None means "now"
            """
            def __init__(self, name, labels, value, timestamp=None):
                self.name = name
                self.labels = labels
                self.value = value
                self.timestamp = timestamp
        
        # Define Metric class for better readability  
        class _Metric:
            """Mock Prometheus metric family containing multiple samples."""
            def __init__(self, name, documentation, metric_type, samples):
                self.name = name
                self.documentation = documentation
                self.type = metric_type
                self.samples = samples
        
        class _MockCounter:
            """Mock Prometheus Counter that tracks increments and supports label-based metrics."""
            def __init__(self, name, description, labelnames=(), *args, **kwargs):
                self.name = name
                self.description = description
                self.labelnames = labelnames
                self._metrics = {}  # Store metrics by label values
            
            def labels(self, **label_values):
                # Create a unique key for this label combination
                label_key = tuple(sorted(label_values.items()))
                if label_key not in self._metrics:
                    self._metrics[label_key] = _MockCounterChild(self, label_key, label_values)
                return self._metrics[label_key]
            
            def inc(self, amount=1):
                # For unlabeled counter
                label_key = ()
                if label_key not in self._metrics:
                    self._metrics[label_key] = _MockCounterChild(self, label_key, {})
                self._metrics[label_key].inc(amount)
            
            def collect(self):
                # Return metrics in prometheus format
                samples = []
                for label_key, child in self._metrics.items():
                    # Create a sample object
                    sample = _Sample(
                        name=self.name,
                        labels=_label_key_to_dict(label_key),
                        value=child._value,
                        timestamp=None
                    )
                    samples.append(sample)
                
                # Return a metric family
                metric = _Metric(
                    name=self.name,
                    documentation=self.description,
                    metric_type='counter',
                    samples=samples
                )
                return [metric]
        
        class _MockCounterChild:
            """Child counter for a specific label combination."""
            def __init__(self, parent, label_key, label_values):
                self.parent = parent
                self.label_key = label_key
                self.label_values = label_values
                self._value = 0
            
            def inc(self, amount=1):
                self._value += amount
            
            def labels(self, **kwargs):
                # If labels are called on child, create new child
                return self.parent.labels(**kwargs)
            
            def collect(self):
                return self.parent.collect()
        
        class _MockHistogram:
            DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0, float("inf"))
            def __init__(self, *args, **kwargs): pass
            def labels(self, *args, **kwargs): return self
            def observe(self, *args, **kwargs): pass
            def time(self, *args, **kwargs):
                def decorator(func): return func
                decorator.__enter__ = lambda: None
                decorator.__exit__ = lambda *args: None
                return decorator
        
        class _MockGauge:
            """Mock Prometheus Gauge that tracks values and supports label-based metrics."""
            def __init__(self, name, description, labelnames=(), *args, **kwargs):
                self.name = name
                self.description = description
                self.labelnames = labelnames
                self._metrics = {}  # Store metrics by label values
                self._value = 0  # For unlabeled gauge
            
            def labels(self, **label_values):
                # Create a unique key for this label combination
                label_key = tuple(sorted(label_values.items()))
                if label_key not in self._metrics:
                    self._metrics[label_key] = _MockGaugeChild(self, label_key, label_values)
                return self._metrics[label_key]
            
            def set(self, value):
                # For unlabeled gauge
                self._value = value
            
            def inc(self, amount=1):
                # For unlabeled gauge
                self._value += amount
            
            def dec(self, amount=1):
                # For unlabeled gauge
                self._value -= amount
            
            def collect(self):
                # Return metrics in prometheus format
                samples = []
                
                # Add unlabeled metric if it was set (even if value is 0)
                # We track if any labeled metrics exist to determine if unlabeled should be included
                if not self._metrics:
                    sample = _Sample(
                        name=self.name,
                        labels={},
                        value=self._value,
                        timestamp=None
                    )
                    samples.append(sample)
                
                # Add labeled metrics
                for label_key, child in self._metrics.items():
                    sample = _Sample(
                        name=self.name,
                        labels=_label_key_to_dict(label_key),
                        value=child._value,
                        timestamp=None
                    )
                    samples.append(sample)
                
                # Return a metric family
                metric = _Metric(
                    name=self.name,
                    documentation=self.description,
                    metric_type='gauge',
                    samples=samples
                )
                return [metric]
        
        class _MockGaugeChild:
            """Child gauge for a specific label combination."""
            def __init__(self, parent, label_key, label_values):
                self.parent = parent
                self.label_key = label_key
                self.label_values = label_values
                self._value = 0
            
            def set(self, value):
                self._value = value
            
            def inc(self, amount=1):
                self._value += amount
            
            def dec(self, amount=1):
                self._value -= amount
            
            def labels(self, **kwargs):
                # If labels are called on child, create new child
                return self.parent.labels(**kwargs)
            
            def collect(self):
                return self.parent.collect()
        
        class _MockInfo:
            def __init__(self, *args, **kwargs): pass
            def labels(self, *args, **kwargs): return self
            def info(self, *args, **kwargs): pass
        
        _shared_registry = _MockCollectorRegistry()
        
        prom_module.CollectorRegistry = _MockCollectorRegistry
        prom_module.Counter = _MockCounter
        prom_module.Histogram = _MockHistogram
        prom_module.Gauge = _MockGauge
        prom_module.Info = _MockInfo
        prom_module.Summary = _MockHistogram
        prom_module.REGISTRY = _shared_registry
        prom_module.CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
        prom_module.push_to_gateway = lambda *args, **kwargs: None
        
        prom_core.HistogramMetricFamily = _MockHistogramMetricFamily
        prom_core.Counter = _MockCounter
        prom_core.Histogram = _MockHistogram
        prom_core.Gauge = _MockGauge
        prom_core.REGISTRY = _shared_registry
        prom_registry.REGISTRY = _shared_registry

# Initialize OpenTelemetry stubs inline
# This ensures OpenTelemetry mocks exist before test files are imported
if "opentelemetry" not in sys.modules:
    try:
        import opentelemetry as _test
    except ImportError:
        # Create opentelemetry package stub
        otel_spec = importlib.machinery.ModuleSpec(name="opentelemetry", loader=None, is_package=True)
        otel_module = importlib.util.module_from_spec(otel_spec)
        otel_module.__file__ = "<mocked opentelemetry>"
        otel_module.__path__ = []  # Required for package-like behavior
        sys.modules["opentelemetry"] = otel_module


# Initialize tiktoken stub for token counting (optional dependency)
if "tiktoken" not in sys.modules:
    try:
        import tiktoken as _test
    except ImportError:
        # Create tiktoken package stub
        tiktoken_spec = importlib.machinery.ModuleSpec(name="tiktoken", loader=None, is_package=False)
        tiktoken_module = importlib.util.module_from_spec(tiktoken_spec)
        tiktoken_module.__file__ = "<mocked tiktoken>"
        
        # Mock encoding class
        class _MockEncoding:
            def encode(self, text: str):
                # Simple heuristic: ~1 token per 4 characters
                return list(range(len(text) // 4 + 1))
            
            def decode(self, tokens):
                return ""
        
        # Mock functions
        def _mock_get_encoding(encoding_name):
            return _MockEncoding()
        
        def _mock_encoding_for_model(model_name):
            return _MockEncoding()
        
        tiktoken_module.get_encoding = _mock_get_encoding
        tiktoken_module.encoding_for_model = _mock_encoding_for_model
        tiktoken_module.Encoding = _MockEncoding
        
        sys.modules["tiktoken"] = tiktoken_module

# Create OpenTelemetry submodules if opentelemetry was mocked
if "opentelemetry" in sys.modules:
    otel_module = sys.modules["opentelemetry"]
    # Only create submodules if they don't exist and opentelemetry is mocked
    if not hasattr(otel_module, '__file__') or otel_module.__file__ == "<mocked opentelemetry>":
        # Create trace submodule with proper hierarchy
        if "opentelemetry.trace" not in sys.modules:
            trace_spec = importlib.machinery.ModuleSpec(name="opentelemetry.trace", loader=None, is_package=True)
            otel_trace = importlib.util.module_from_spec(trace_spec)
            otel_trace.__file__ = "<mocked opentelemetry.trace>"
            otel_trace.__path__ = []
            sys.modules["opentelemetry.trace"] = otel_trace
            otel_module.trace = otel_trace
        else:
            otel_trace = sys.modules["opentelemetry.trace"]
        
        # Create trace.status submodule
        if "opentelemetry.trace.status" not in sys.modules:
            trace_status_spec = importlib.machinery.ModuleSpec(name="opentelemetry.trace.status", loader=None, is_package=False)
            otel_trace_status = importlib.util.module_from_spec(trace_status_spec)
            otel_trace_status.__file__ = "<mocked opentelemetry.trace.status>"
            sys.modules["opentelemetry.trace.status"] = otel_trace_status
            otel_trace.status = otel_trace_status
        else:
            otel_trace_status = sys.modules["opentelemetry.trace.status"]
        
        # Create metrics submodule
        if "opentelemetry.metrics" not in sys.modules:
            metrics_spec = importlib.machinery.ModuleSpec(name="opentelemetry.metrics", loader=None, is_package=False)
            otel_metrics = importlib.util.module_from_spec(metrics_spec)
            otel_metrics.__file__ = "<mocked opentelemetry.metrics>"
            sys.modules["opentelemetry.metrics"] = otel_metrics
            otel_module.metrics = otel_metrics
        else:
            otel_metrics = sys.modules["opentelemetry.metrics"]
        
        # Add mock classes for trace
        class MockSpan:
            """Mock OpenTelemetry Span that can be used as a context manager and is callable."""
            def __init__(self, *args, **kwargs):
                self.attributes = {}
                self.events = []
                self.status = None
                
            def __enter__(self):
                return self
                
            def __exit__(self, *args):
                pass
                
            def __call__(self, *args, **kwargs):
                """Make MockSpan callable for decorator usage."""
                if len(args) == 1 and callable(args[0]):
                    # Used as decorator
                    func = args[0]
                    def wrapper(*inner_args, **inner_kwargs):
                        with self:
                            return func(*inner_args, **inner_kwargs)
                    return wrapper
                return self
                
            def set_attribute(self, key, value):
                self.attributes[key] = value
                
            def set_attributes(self, attributes):
                self.attributes.update(attributes)
                
            def add_event(self, name, attributes=None):
                self.events.append({"name": name, "attributes": attributes or {}})
                
            def set_status(self, status):
                self.status = status
                
            def record_exception(self, exception):
                self.events.append({"name": "exception", "exception": exception})

            def end(self):
                """End the span."""
                pass
        
        class MockTracer:
            """Mock OpenTelemetry Tracer."""
            def __init__(self, *args, **kwargs):
                pass
                
            def start_as_current_span(self, name, *args, **kwargs):
                return MockSpan()
                
            def start_span(self, name, *args, **kwargs):
                return MockSpan()
        
        class MockTracerProvider:
            """Mock OpenTelemetry TracerProvider."""
            def __init__(self, *args, **kwargs):
                pass
                
            def get_tracer(self, *args, **kwargs):
                return MockTracer()
        
        class MockStatus:
            """Mock OpenTelemetry Status."""
            def __init__(self, status_code, description=None):
                self.status_code = status_code
                self.description = description
        
        class MockStatusCode:
            """Mock OpenTelemetry StatusCode enum."""
            OK = "OK"
            ERROR = "ERROR"
            UNSET = "UNSET"
        
        # Add mock classes for metrics
        class MockMeter:
            """Mock OpenTelemetry Meter."""
            def __init__(self, *args, **kwargs):
                pass
                
            def create_counter(self, name, *args, **kwargs):
                return MockCounter()
                
            def create_histogram(self, name, *args, **kwargs):
                return MockHistogram()
                
            def create_observable_gauge(self, name, callback, *args, **kwargs):
                return MockObservableGauge()
        
        class MockMeterProvider:
            """Mock OpenTelemetry MeterProvider."""
            def __init__(self, *args, **kwargs):
                pass
                
            def get_meter(self, *args, **kwargs):
                return MockMeter()
        
        class MockCounter:
            """Mock OpenTelemetry Counter."""
            def __init__(self, *args, **kwargs):
                pass
                
            def add(self, value, attributes=None):
                pass
        
        class MockHistogram:
            """Mock OpenTelemetry Histogram."""
            def __init__(self, *args, **kwargs):
                pass
                
            def record(self, value, attributes=None):
                pass
        
        class MockObservableGauge:
            """Mock OpenTelemetry ObservableGauge."""
            def __init__(self, *args, **kwargs):
                pass
        
        # Set up trace module
        otel_trace.get_tracer = lambda *args, **kwargs: MockTracer()
        otel_trace.get_tracer_provider = lambda: MockTracerProvider()
        otel_trace.set_tracer_provider = lambda provider: None
        otel_trace.Span = MockSpan
        otel_trace.Tracer = MockTracer
        otel_trace.TracerProvider = MockTracerProvider
        
        # Set up trace.status module
        otel_trace_status.Status = MockStatus
        otel_trace_status.StatusCode = MockStatusCode
        
        # Set up metrics module
        otel_metrics.get_meter = lambda *args, **kwargs: MockMeter()
        otel_metrics.get_meter_provider = lambda: MockMeterProvider()
        otel_metrics.set_meter_provider = lambda provider: None
        otel_metrics.Meter = MockMeter
        otel_metrics.MeterProvider = MockMeterProvider
        otel_metrics.Counter = MockCounter
        otel_metrics.Histogram = MockHistogram
        otel_metrics.ObservableGauge = MockObservableGauge

# Add root directory to path if not already there
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# Add generator/scripts directory to path for script tests
scripts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

# This will trigger other initialization in the root conftest
import conftest as root_conftest


def _cleanup_watchdog_observers():
    """
    Clean up any leftover watchdog observers that may be blocking test completion.
    
    Watchdog observers run in background threads and can cause test timeouts
    if not properly stopped. This function forcefully stops all observer threads.
    """
    try:
        from watchdog.observers.api import BaseObserver
        
        # Find and stop all observer threads
        for thread in threading.enumerate():
            if isinstance(thread, BaseObserver):
                try:
                    thread.stop()
                    thread.join(timeout=1.0)
                except Exception:
                    pass
    except (ImportError, AttributeError):
        pass  # watchdog not installed or mocked
    
    # Also try to stop any threads that look like watchdog threads by name
    for thread in threading.enumerate():
        thread_name = thread.name.lower()
        if 'observer' in thread_name or 'inotify' in thread_name or 'watchdog' in thread_name:
            if hasattr(thread, 'stop'):
                try:
                    thread.stop()
                    thread.join(timeout=1.0)
                except Exception:
                    pass


@pytest.fixture(autouse=True)
def cleanup_watchdog_after_test():
    """Automatically clean up watchdog observers after each test."""
    yield
    _cleanup_watchdog_observers()


def _cleanup_multiprocessing_resources():
    """
    Clean up any leftover multiprocessing processes and queues that may be blocking test completion.

    Multiprocessing processes and queues can cause test timeouts if not properly cleaned up.
    This function forcefully terminates all non-main processes and attempts to close queues.
    """
    try:
        # Get all active processes
        active_children = multiprocessing.active_children()
        if active_children:
            for process in active_children:
                try:
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=1.0)
                    # If still alive, kill it
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=0.5)
                except Exception:
                    pass
    except Exception:
        pass


@pytest.fixture(autouse=True)
def cleanup_multiprocessing_after_test():
    """Automatically clean up multiprocessing resources after each test."""
    yield
    _cleanup_multiprocessing_resources()


@pytest.fixture(scope="session", autouse=True)
def cleanup_watchdog_at_session_end():
    """Clean up watchdog observers at the end of the test session."""
    yield
    _cleanup_watchdog_observers()
    _cleanup_multiprocessing_resources()
    _cleanup_leaked_threads()


# Known safe thread-name substrings to stop after a test session.
# These are background service threads that should not outlive the test run.
# WARNING: Only add thread name patterns that are safe to stop (non-critical daemon threads).
_LEAKED_THREAD_NAMES = (
    "secret_sync_bridge",  # LLMClient background sync thread
    "event_bus",           # self_fixing_engineer event bus worker
)


def _cleanup_leaked_threads():
    """Stop known leaked daemon threads that survive between test runs.

    Some modules (LLMClient, EventBus) start background threads that are not
    properly terminated when their owning object is garbage-collected.  This
    causes subsequent tests—especially async ones—to hang because the leaked
    threads hold references to closed file-descriptors, event-loops, or locks.

    This cleanup is best-effort: we attempt a graceful stop() then a short
    join(); if the thread ignores the stop we leave it (it's a daemon thread
    so it won't prevent process exit).

    TODO: Remove entries from _LEAKED_THREAD_NAMES once the owning module is
    fixed to properly shut down its threads in its own __del__ / close().
    """
    for thread in threading.enumerate():
        tname = thread.name.lower()
        # Patterns are already lowercase; tname is also lowercased for safe comparison.
        if any(pattern in tname for pattern in _LEAKED_THREAD_NAMES):
            try:
                if hasattr(thread, "stop"):
                    thread.stop()
                thread.join(timeout=0.5)
            except Exception:
                # Best-effort only: we intentionally swallow exceptions here.
                # These are daemon threads so they will be reaped when the process exits.
                pass


@pytest.fixture(autouse=True)
def cleanup_memory_after_test():
    """Force garbage collection after each test to prevent memory accumulation."""
    yield
    # Run GC twice to catch circular references
    gc.collect()
    gc.collect()


@pytest.fixture(autouse=True)
def clear_runner_config_cache():
    """Clear the runner config cache before each test to ensure clean state.
    
    This prevents config caching issues where one test's loaded config
    affects subsequent tests, especially when tests use mocked load_config().
    """
    try:
        from generator.runner.runner_config import clear_config_cache
        clear_config_cache()
    except ImportError:
        # If runner_config isn't available, skip
        pass
    yield
    # Also clear after test to prevent cache pollution
    try:
        from generator.runner.runner_config import clear_config_cache
        clear_config_cache()
    except ImportError:
        pass


# ---- Global Async Mock Fixtures ----
# These fixtures automatically mock commonly awaited async functions
# to prevent "TypeError: object MagicMock can't be used in 'await' expression"
# errors throughout the test suite.

@pytest.fixture(autouse=True)
def mock_async_file_utils():
    """Automatically mock async functions in runner_file_utils for all tests."""
    from unittest.mock import AsyncMock, patch
    
    # Try to patch runner_file_utils if it exists, otherwise skip
    try:
        import runner.runner_file_utils
        with patch("runner.runner_file_utils.verify_file_integrity", new_callable=AsyncMock, return_value=True) as mock_verify, \
             patch("runner.runner_file_utils.add_provenance", new_callable=AsyncMock) as mock_prov, \
             patch("runner.runner_file_utils.scan_for_vulnerabilities", new_callable=AsyncMock, return_value={"vulnerabilities_found": 0}) as mock_scan:
            yield {
                "verify_file_integrity": mock_verify,
                "add_provenance": mock_prov,
                "scan_for_vulnerabilities": mock_scan,
            }
    except (ImportError, AttributeError):
        # runner_file_utils not available, skip mocking
        yield {}


@pytest.fixture(autouse=True)
def mock_async_security_utils():
    """Automatically mock async functions in runner_security_utils for all tests."""
    from unittest.mock import AsyncMock, patch
    
    # Only mock if the functions are called in async context
    # This fixture provides safety net without breaking tests that mock these themselves
    try:
        with patch("runner.runner_security_utils.fetch_secret", new_callable=AsyncMock, return_value=None) as mock_fetch, \
             patch("runner.runner_security_utils.monitor_for_leaks", new_callable=AsyncMock) as mock_monitor:
            yield {
                "fetch_secret": mock_fetch,
                "monitor_for_leaks": mock_monitor,
            }
    except (ImportError, AttributeError):
        # Module not available or already mocked
        yield {}


# ---- Textual App.run_test Compatibility ----
# Some versions of Textual don't have run_test() method on App class
# Add it as a mock if it's missing to prevent AttributeError

@pytest.fixture(scope="session", autouse=True)
def ensure_textual_run_test():
    """Ensure Textual App has run_test method for testing compatibility."""
    try:
        from textual.app import App
        if not hasattr(App, 'run_test'):
            # Add a mock run_test method
            from unittest.mock import AsyncMock
            import asyncio
            from contextlib import asynccontextmanager
            
            @asynccontextmanager
            async def mock_run_test(self):
                """Mock run_test that provides a basic pilot for testing."""
                from unittest.mock import MagicMock
                pilot = MagicMock()
                pilot.app = self
                pilot.click = AsyncMock()
                pilot.press = AsyncMock()
                pilot.pause = AsyncMock()
                try:
                    yield pilot
                finally:
                    pass
            
            App.run_test = mock_run_test
    except ImportError:
        # Textual not installed, tests will be skipped anyway
        pass
    yield
