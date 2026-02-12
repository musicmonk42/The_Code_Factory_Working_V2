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
                pass
            
            def unregister(self, collector): 
                pass
            
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
        
        # Define Sample class for better readability
        class _Sample:
            """Mock Prometheus sample representing a single metric data point."""
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
                        labels=dict(label_key) if label_key else {},
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
            def __init__(self, *args, **kwargs): pass
            def labels(self, *args, **kwargs): return self
            def set(self, *args, **kwargs): pass
            def inc(self, *args, **kwargs): pass
            def dec(self, *args, **kwargs): pass
        
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
        
        # Create trace submodule with proper hierarchy
        trace_spec = importlib.machinery.ModuleSpec(name="opentelemetry.trace", loader=None, is_package=True)
        otel_trace = importlib.util.module_from_spec(trace_spec)
        otel_trace.__file__ = "<mocked opentelemetry.trace>"
        otel_trace.__path__ = []
        sys.modules["opentelemetry.trace"] = otel_trace
        otel_module.trace = otel_trace
        
        # Create trace.status submodule
        trace_status_spec = importlib.machinery.ModuleSpec(name="opentelemetry.trace.status", loader=None, is_package=False)
        otel_trace_status = importlib.util.module_from_spec(trace_status_spec)
        otel_trace_status.__file__ = "<mocked opentelemetry.trace.status>"
        sys.modules["opentelemetry.trace.status"] = otel_trace_status
        otel_trace.status = otel_trace_status
        
        # Create metrics submodule
        metrics_spec = importlib.machinery.ModuleSpec(name="opentelemetry.metrics", loader=None, is_package=False)
        otel_metrics = importlib.util.module_from_spec(metrics_spec)
        otel_metrics.__file__ = "<mocked opentelemetry.metrics>"
        sys.modules["opentelemetry.metrics"] = otel_metrics
        otel_module.metrics = otel_metrics
        
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


@pytest.fixture(autouse=True)
def cleanup_memory_after_test():
    """Force garbage collection after each test to prevent memory accumulation."""
    yield
    # Run GC twice to catch circular references
    gc.collect()
    gc.collect()


# ---- Global Async Mock Fixtures ----
# These fixtures automatically mock commonly awaited async functions
# to prevent "TypeError: object MagicMock can't be used in 'await' expression"
# errors throughout the test suite.

@pytest.fixture(autouse=True)
def mock_async_file_utils():
    """Automatically mock async functions in runner_file_utils for all tests."""
    from unittest.mock import AsyncMock, patch
    
    with patch("runner.runner_file_utils.verify_file_integrity", new_callable=AsyncMock, return_value=True) as mock_verify, \
         patch("runner.runner_file_utils.add_provenance", new_callable=AsyncMock) as mock_prov, \
         patch("runner.runner_file_utils.scan_for_vulnerabilities", new_callable=AsyncMock, return_value={"vulnerabilities_found": 0}) as mock_scan:
        yield {
            "verify_file_integrity": mock_verify,
            "add_provenance": mock_prov,
            "scan_for_vulnerabilities": mock_scan,
        }


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
