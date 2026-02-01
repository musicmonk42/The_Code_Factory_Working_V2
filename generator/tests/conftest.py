"""
Conftest for generator tests - ensures prometheus stubs are initialized before test imports.

This conftest is loaded by pytest before any test files in this directory,
ensuring that prometheus_client stubs are available during test collection.
"""

import sys
import os
import importlib.machinery
import importlib.util

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
            def register(self, collector): pass
            def unregister(self, collector): pass
            def get_sample_value(self, *args, **kwargs): return None
        
        class _MockCounter:
            def __init__(self, *args, **kwargs): pass
            def labels(self, *args, **kwargs): return self
            def inc(self, *args, **kwargs): pass
        
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
