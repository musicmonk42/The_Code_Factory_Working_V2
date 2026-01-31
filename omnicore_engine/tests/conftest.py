"""Test configuration for omnicore_engine tests."""

import os
import sys
from pathlib import Path
import pytest

# ---- Ensure paths are set up correctly ----
# This is defensive: the root conftest.py should handle path setup,
# but we ensure it here in case pytest is run from the omnicore_engine/tests/ directory
_tests_dir = Path(__file__).parent.absolute()
_omnicore_dir = _tests_dir.parent
_project_root = _omnicore_dir.parent

# Add project root to sys.path if not already present (highest priority)
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Import path_setup module to ensure all component paths are configured
try:
    import path_setup
except ImportError as e:
    # If path_setup is not available, continue without it
    # The root conftest.py should have already set up paths
    import warnings
    warnings.warn(f"omnicore_engine/tests/conftest.py: Could not import path_setup module: {e}. Using basic path configuration.")

# FIX: Lazy import prometheus_client to avoid collection-time failures
# This prevents AttributeError: __spec__ when the root conftest mocks prometheus_client
def _get_prometheus_registry():
    """Lazy getter for Prometheus registry."""
    try:
        from prometheus_client import REGISTRY
        return REGISTRY
    except (ImportError, AttributeError):
        # Return a mock registry if prometheus is not available
        class MockRegistry:
            def __init__(self):
                self._collector_to_names = {}
            def unregister(self, collector):
                pass
        return MockRegistry()


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
    REGISTRY = _get_prometheus_registry()
    
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
