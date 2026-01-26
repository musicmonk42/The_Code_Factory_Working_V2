"""Test configuration for omnicore_engine tests."""

import pytest
from prometheus_client import REGISTRY


@pytest.fixture(autouse=True, scope="function")
def reset_prometheus_collectors():
    """Reset Prometheus collectors before each test to prevent duplicates."""
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
