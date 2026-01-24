# conftest.py (self_fixing_engineer)

import os

import pytest

# Set environment variables early
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("PROMETHEUS_DISABLE_CREATED_SERIES", "true")


# ---- Deferred imports for OpenTelemetry ----
# Import OpenTelemetry components with fallback to avoid collection failures
# These are checked at fixture time, not import time
_OTEL_AVAILABLE = None
_OTEL_IMPORTS = {}


def _check_otel_availability():
    """Check if OpenTelemetry is available (deferred to fixture time)."""
    global _OTEL_AVAILABLE, _OTEL_IMPORTS
    if _OTEL_AVAILABLE is not None:
        return _OTEL_AVAILABLE
    
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
        
        _OTEL_IMPORTS['trace'] = trace
        _OTEL_IMPORTS['TracerProvider'] = TracerProvider
        _OTEL_IMPORTS['ConsoleSpanExporter'] = ConsoleSpanExporter
        _OTEL_IMPORTS['SimpleSpanProcessor'] = SimpleSpanProcessor
        _OTEL_AVAILABLE = True
    except ImportError:
        _OTEL_AVAILABLE = False
    
    return _OTEL_AVAILABLE


def _setup_prometheus_patching():
    """
    Setup Prometheus registry patching to allow duplicate metrics.
    Deferred to fixture time to avoid expensive imports during test collection.
    """
    try:
        from prometheus_client import REGISTRY
        from prometheus_client.registry import CollectorRegistry

        _ORIG_REGISTER = CollectorRegistry.register

        def _safe_register(self, collector):
            """
            If any metric name produced by `collector` is already present, skip
            registering (no-op) instead of raising ValueError.
            """
            try:
                try:
                    names = set(self._get_names(collector))  # type: ignore[attr-defined]
                except Exception:
                    names = set()

                present = set()
                try:
                    for _c, seq in getattr(self, "_collector_to_names", {}).items():
                        for n in seq:
                            present.add(n)
                except Exception:
                    pass

                if names & present:
                    return collector  # treat as already-registered
                return _ORIG_REGISTER(self, collector)
            except Exception:
                # Last resort: never let metrics kill the test run
                try:
                    return _ORIG_REGISTER(self, collector)
                except Exception:
                    return collector

        # Patch both the class and the default registry instance
        CollectorRegistry.register = _safe_register  # class-level
        REGISTRY.register = _safe_register.__get__(
            REGISTRY, REGISTRY.__class__
        )  # instance-level
    except Exception:
        pass


# ---- Session fixtures (run AFTER test collection) ----
@pytest.fixture(scope="session", autouse=True)
def setup_prometheus():
    """
    Setup Prometheus patching for the test session.
    Runs AFTER test collection to avoid slow imports.
    """
    _setup_prometheus_patching()
    yield


@pytest.fixture(scope="session", autouse=True)
def setup_otel():
    """
    Initializes a minimal OpenTelemetry SDK for the entire test session.
    This prevents 'NoneType' and 'NoOpSpan' AttributeErrors when code
    tries to access or record spans during tests.
    Runs AFTER test collection to avoid slow imports.
    """
    if _check_otel_availability():
        trace = _OTEL_IMPORTS['trace']
        TracerProvider = _OTEL_IMPORTS['TracerProvider']
        ConsoleSpanExporter = _OTEL_IMPORTS['ConsoleSpanExporter']
        SimpleSpanProcessor = _OTEL_IMPORTS['SimpleSpanProcessor']
        
        provider = TracerProvider()
        # Using ConsoleSpanExporter as a robust fallback.
        exporter = ConsoleSpanExporter()
        processor = SimpleSpanProcessor(exporter)
        provider.add_span_processor(processor)

        # Sets the global tracer provider
        trace.set_tracer_provider(provider)

    yield
