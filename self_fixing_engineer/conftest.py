# conftest.py (root)

import os

import pytest

# Allow duplicate metric registration during tests to prevent collection failures
os.environ.setdefault("PROMETHEUS_DISABLE_CREATED_SERIES", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")  # keep OTEL quiet

# Try to import OpenTelemetry, but don't fail if it's not available
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None
    TracerProvider = None
    ConsoleSpanExporter = None
    SimpleSpanProcessor = None

# ---- Prometheus duplicate-metric hardening (runs before any package imports)
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
# ---- end hardening


# ---- OpenTelemetry Test Setup Fixture ---
@pytest.fixture(scope="session", autouse=True)
def setup_otel():
    """
    Initializes a minimal OpenTelemetry SDK for the entire test session.
    This prevents 'NoneType' and 'NoOpSpan' AttributeErrors when code
    tries to access or record spans during tests.
    """
    if not OTEL_AVAILABLE:
        yield
        return

    provider = TracerProvider()
    # Using ConsoleSpanExporter as a robust fallback.
    exporter = ConsoleSpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)

    # Sets the global tracer provider
    trace.set_tracer_provider(provider)

    yield
