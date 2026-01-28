"""Test configuration for omnicore_engine tests."""

import os
import pytest

# Import prometheus_client safely - may not be available or may have registry issues
try:
    from prometheus_client import REGISTRY
except (ImportError, ValueError) as e:
    # If prometheus_client is not available or has registry issues, create a mock
    import sys
    import types
    if 'prometheus_client' not in sys.modules:
        # Create a minimal mock
        prom_module = types.ModuleType('prometheus_client')
        
        class MockRegistry:
            def __init__(self):
                self._collector_to_names = {}
                self._names_to_collectors = {}
            def unregister(self, collector):
                pass
        
        prom_module.REGISTRY = MockRegistry()
        sys.modules['prometheus_client'] = prom_module
        REGISTRY = prom_module.REGISTRY
    else:
        # Module exists but has issues, try to get REGISTRY
        try:
            REGISTRY = sys.modules['prometheus_client'].REGISTRY
        except AttributeError:
            # Create a mock registry
            class MockRegistry:
                def __init__(self):
                    self._collector_to_names = {}
                def unregister(self, collector):
                    pass
            REGISTRY = MockRegistry()
            sys.modules['prometheus_client'].REGISTRY = REGISTRY


def pytest_configure(config):
    """Skip expensive initialization during collection phase."""
    if config.option.collectonly:
        os.environ['SKIP_EXPENSIVE_INIT'] = '1'
        os.environ['PYTEST_COLLECTING_ONLY'] = '1'


@pytest.fixture(autouse=True, scope="function")
def reset_prometheus_collectors():
    """Reset Prometheus collectors before each test to prevent duplicates.
    
    Note: Uses private API `_collector_to_names` as the public API doesn't
    provide a way to iterate collectors for cleanup. This is wrapped in
    defensive try-except blocks to handle potential API changes gracefully.
    """
    # Store collectors to remove using a defensive approach
    try:
        collectors = list(REGISTRY._collector_to_names.keys())
    except (AttributeError, KeyError):
        # If the internal structure changes, skip cleanup
        collectors = []
    
    # Unregister all collectors
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass
    
    yield
    
    # Clean up after test
    try:
        collectors = list(REGISTRY._collector_to_names.keys())
    except (AttributeError, KeyError):
        collectors = []
        
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass
