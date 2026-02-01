"""
Test to verify that prometheus_client and opentelemetry are properly imported
and not mocked when they are installed dependencies.

This test validates the fix for the test collection failures where these
packages were being incorrectly mocked even though they were installed.
"""
import sys
import pytest


def test_prometheus_client_not_mocked():
    """Verify prometheus_client is the real module, not a mock."""
    # Import prometheus_client
    import prometheus_client
    
    # Verify it has real attributes, not mock attributes
    assert hasattr(prometheus_client, '__spec__'), "prometheus_client should have __spec__"
    assert hasattr(prometheus_client, '__file__'), "prometheus_client should have __file__"
    
    # Verify __file__ is not a mock file
    assert "<mocked" not in str(prometheus_client.__file__).lower(), \
        f"prometheus_client should not be mocked, but got: {prometheus_client.__file__}"
    assert "<stub" not in str(prometheus_client.__file__).lower(), \
        f"prometheus_client should not be stubbed, but got: {prometheus_client.__file__}"
    
    # Verify we can import from submodules
    from prometheus_client.registry import REGISTRY
    assert REGISTRY is not None, "Should be able to import REGISTRY from prometheus_client.registry"
    
    # Verify it's not a mock/magic mock
    assert "Mock" not in type(prometheus_client).__name__, \
        f"prometheus_client should not be a Mock, but got type: {type(prometheus_client)}"


def test_opentelemetry_not_mocked():
    """Verify opentelemetry is the real module, not a mock."""
    # Import opentelemetry
    import opentelemetry
    
    # Verify it has real attributes, not mock attributes
    assert hasattr(opentelemetry, '__spec__'), "opentelemetry should have __spec__"
    assert hasattr(opentelemetry, '__file__'), "opentelemetry should have __file__"
    
    # Verify __file__ is not a mock file
    assert "<mocked" not in str(opentelemetry.__file__).lower(), \
        f"opentelemetry should not be mocked, but got: {opentelemetry.__file__}"
    assert "<stub" not in str(opentelemetry.__file__).lower(), \
        f"opentelemetry should not be stubbed, but got: {opentelemetry.__file__}"
    
    # Verify we can import from submodules
    from opentelemetry import trace
    assert trace is not None, "Should be able to import trace from opentelemetry"
    
    # Verify it's not a mock/magic mock
    assert "Mock" not in type(opentelemetry).__name__, \
        f"opentelemetry should not be a Mock, but got type: {type(opentelemetry)}"


def test_prometheus_client_functional():
    """Verify prometheus_client can be used functionally."""
    from prometheus_client import Counter, Histogram, CollectorRegistry
    import uuid
    
    # Use a custom registry to avoid conflicts with global metrics
    registry = CollectorRegistry()
    
    # Create a counter with unique name
    unique_id = str(uuid.uuid4()).replace('-', '_')
    counter = Counter(f'test_counter_{unique_id}', 'A test counter', registry=registry)
    counter.inc()
    
    # Create a histogram with unique name
    histogram = Histogram(f'test_histogram_{unique_id}', 'A test histogram', registry=registry)
    histogram.observe(1.5)
    
    # Verify these are real objects, not mocks
    assert hasattr(counter, 'inc'), "Counter should have inc method"
    assert hasattr(histogram, 'observe'), "Histogram should have observe method"


def test_opentelemetry_functional():
    """Verify opentelemetry can be used functionally."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    
    # Set up a tracer provider
    tracer_provider = TracerProvider()
    trace.set_tracer_provider(tracer_provider)
    
    # Get a tracer
    tracer = trace.get_tracer(__name__)
    
    # Create a span
    with tracer.start_as_current_span("test_span") as span:
        assert span is not None
        assert hasattr(span, 'set_attribute'), "Span should have set_attribute method"


if __name__ == "__main__":
    # Run the tests
    pytest.main([__file__, "-v"])
