# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Global pytest configuration for self_fixing_engineer tests.

This module:
- Sets test environment variables to disable heavy components
- Mocks HuggingFace transformers pipeline to prevent model loading
- Mocks Pinecone to prevent vector store initialization
- Mocks HuggingFaceEmbeddings to prevent model downloads
- Implements aggressive memory cleanup after each test
"""

import gc
import os
import sys
from unittest.mock import MagicMock

import pytest

# ---- Set environment variables BEFORE any imports ----
os.environ["TEST_MODE"] = "true"
os.environ["TESTING"] = "1"
os.environ["USE_VECTOR_MEMORY"] = "false"
os.environ["DISABLE_SENTRY"] = "1"
os.environ["OTEL_SDK_DISABLED"] = "1"
os.environ["SKIP_AUDIT_INIT"] = "1"
os.environ["SKIP_BACKGROUND_TASKS"] = "1"
os.environ["NO_MONITORING"] = "1"
os.environ["DISABLE_TELEMETRY"] = "1"
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.environ["SKIP_IMPORT_TIME_VALIDATION"] = "1"
# Set PYTEST_COLLECTING to prevent expensive arbiter module initialization
# during test collection phase (helps prevent CI CPU timeouts)
os.environ["PYTEST_COLLECTING"] = "1"

# ---- Mock HuggingFace Transformers Pipeline ----
try:
    if "transformers" in sys.modules:
        def mock_pipeline(*args, **kwargs):
            mock_pipe = MagicMock()
            mock_pipe.return_value = [{"label": "SAFE", "score": 0.99}]
            return mock_pipe
        sys.modules["transformers"].pipeline = mock_pipeline
except (ImportError, KeyError):
    pass


# ---- Mock Pinecone ----
try:
    if "pinecone" not in sys.modules:
        mock_pinecone = MagicMock()
        mock_pinecone.Pinecone = MagicMock()
        sys.modules["pinecone"] = mock_pinecone
    else:
        sys.modules["pinecone"].Pinecone = MagicMock()
except Exception:
    pass

# ---- Mock langchain_pinecone ----
try:
    if "langchain_pinecone" not in sys.modules:
        sys.modules["langchain_pinecone"] = MagicMock()
except Exception:
    pass


# ---- Mock HuggingFaceEmbeddings ----
try:
    if "langchain_community.embeddings" in sys.modules:
        class MockHuggingFaceEmbeddings:
            def __init__(self, *args, **kwargs):
                self.model_name = kwargs.get("model_name", "mock-model")
            
            def embed_documents(self, texts):
                return [[0.0] * 384 for _ in texts]
            
            def embed_query(self, text):
                return [0.0] * 384
        
        sys.modules["langchain_community.embeddings"].HuggingFaceEmbeddings = MockHuggingFaceEmbeddings
except (ImportError, KeyError):
    pass


# ---- Pytest Configuration ----

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "heavy: mark test as heavy/resource-intensive (skipped by default)"
    )


def pytest_collection_finish(session):
    """
    Called after collection is complete, before tests start running.
    Clear PYTEST_COLLECTING so that actual test runs can load components.
    """
    os.environ.pop("PYTEST_COLLECTING", None)


@pytest.fixture(scope="session", autouse=True)
def initialize_arbiter_components():
    """
    Initialize arbiter components after collection is complete.
    
    This fixture runs once per session, after collection and after mocks are cleaned up.
    It respects the PYTEST_COLLECTING_ONLY environment variable to avoid loading
    components during --collect-only runs.
    
    The fixture is autouse so it runs automatically before any test without needing
    to be explicitly requested.
    """
    # Skip component loading if we're only collecting tests
    if os.getenv("PYTEST_COLLECTING_ONLY"):
        yield
        return
    
    # Load components now that collection is complete and mocks are cleaned up
    try:
        from self_fixing_engineer.arbiter import _load_components, _components_loaded
        if not _components_loaded:
            _load_components()
    except ImportError:
        pass
    
    yield


@pytest.fixture(scope="function", autouse=True)
def aggressive_memory_cleanup():
    """
    Memory cleanup after each test.
    
    Runs at function scope to ensure complete isolation between tests.
    This is critical for preventing OOM failures in memory-constrained CI.
    
    Optimized to avoid expensive per-test operations:
    - Single gc.collect() pass instead of multiple
    - Removed per-test module cache scanning and Prometheus registry cleanup
    """
    yield
    
    # Single garbage collection pass is sufficient for most tests
    gc.collect()


@pytest.fixture(scope="session")
def session_cleanup():
    """Final cleanup at session end."""
    yield

    # Shutdown OpenTelemetry to terminate background threads
    try:
        from opentelemetry import trace
        provider = trace.get_tracer_provider()
        if hasattr(provider, 'shutdown'):
            provider.shutdown()
    except Exception:
        pass

    # Final aggressive cleanup
    gc.collect()
    gc.collect()
    gc.collect()


# ---- OpenTelemetry Tracing Setup for Tests ----

@pytest.fixture(scope="session", autouse=True)
def setup_opentelemetry_tracer():
    """
    Set up a minimal OpenTelemetry tracer provider for the entire test session.

    This ensures that trace.get_current_span() and tracer.start_as_current_span()
    work properly even when OTEL_SDK_DISABLED is set. Many tests in the arbiter
    modules rely on OpenTelemetry tracing.
    """
    provider = None
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        # Set up a minimal provider
        provider = TracerProvider()
        # Use in-memory exporter (lightweight, no I/O)
        exporter = InMemorySpanExporter()
        processor = SimpleSpanProcessor(exporter)
        provider.add_span_processor(processor)

        # Set as the global provider
        trace.set_tracer_provider(provider)

    except ImportError:
        # OpenTelemetry not available, skip setup
        pass

    yield

    # Shutdown OpenTelemetry to terminate any background threads
    if provider is not None:
        try:
            provider.shutdown()
        except Exception:
            pass


def pytest_sessionfinish(session, exitstatus):
    """
    Called after whole test run finished, right before returning exit status.
    Ensures OpenTelemetry is properly shut down even if fixtures don't run.
    """
    try:
        from opentelemetry import trace
        provider = trace.get_tracer_provider()
        if hasattr(provider, 'shutdown'):
            provider.shutdown()
    except Exception:
        pass

