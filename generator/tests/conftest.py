"""
Conftest for generator tests - ensures prometheus stubs are initialized before test imports.

This conftest is loaded by pytest before any test files in this directory,
ensuring that prometheus_client stubs are available during test collection.
"""

import sys
import os
import importlib.machinery
import importlib.util
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


# Create mock watchdog modules
_mock_watchdog_observers = MagicMock()
_mock_watchdog_observers.Observer = MockObserver

_mock_watchdog_events = MagicMock()
_mock_watchdog_events.FileSystemEventHandler = MockFileSystemEventHandler
_mock_watchdog_events.FileCreatedEvent = MagicMock
_mock_watchdog_events.FileModifiedEvent = MagicMock
_mock_watchdog_events.FileDeletedEvent = MagicMock
_mock_watchdog_events.FileMovedEvent = MagicMock

# Pre-register the mocks BEFORE any code imports watchdog
# This ensures that when modules do `from watchdog.observers import Observer`,
# they get our mock instead of the real one
if os.environ.get("TESTING") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
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
            def __init__(self, name, labels, value, timestamp=None):
                self.name = name
                self.labels = labels
                self.value = value
                self.timestamp = timestamp
        
        # Define Metric class for better readability  
        class _Metric:
            def __init__(self, name, documentation, metric_type, samples):
                self.name = name
                self.documentation = documentation
                self.type = metric_type
                self.samples = samples
        
        class _MockCounter:
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

# Add root directory to path if not already there
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

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
    except ImportError:
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


@pytest.fixture(scope="session", autouse=True)
def cleanup_watchdog_at_session_end():
    """Clean up watchdog observers at the end of the test session."""
    yield
    _cleanup_watchdog_observers()
