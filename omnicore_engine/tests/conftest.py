"""Test configuration for omnicore_engine tests."""

import pytest
from prometheus_client import REGISTRY


@pytest.fixture(autouse=True, scope="function")
def reset_prometheus_collectors():
    """Reset Prometheus collectors before each test to prevent duplicates."""
    # Store collectors to remove
    collectors = list(REGISTRY._collector_to_names.keys())
    
    # Unregister all collectors
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass
    
    yield
    
    # Clean up after test
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass
