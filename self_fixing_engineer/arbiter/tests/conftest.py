# -*- coding: utf-8 -*-
"""
Global test configuration and fixtures for the test suite.

This conftest.py handles:
1. Plugin registry isolation for tests (preventing persistence conflicts)
2. Starlette/FastAPI compatibility shims
3. Pydantic v1/v2 compatibility
4. OpenTelemetry test setup
5. Custom pytest markers

Note: pytest_plugins has been moved to the root conftest.py to avoid deprecation warnings.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# NOTE: pytest_plugins declaration removed from nested conftest
# It is now in the root conftest.py to avoid pytest deprecation warnings

# -----------------------------------------------------------------------------
# TEST ENVIRONMENT SETUP - Must happen before any imports
# -----------------------------------------------------------------------------
# Set testing flag immediately
os.environ["TESTING"] = "true"
os.environ["OTEL_ENABLED"] = "0"
os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["SFE_OTEL_EXPORTER_TYPE"] = "console"

# Create a temporary directory for test artifacts
TEST_TEMP_DIR = tempfile.mkdtemp(prefix="sfe_test_")
TEST_PLUGIN_FILE = os.path.join(TEST_TEMP_DIR, "test_plugins.json")


# -----------------------------------------------------------------------------
# OPENTELEMETRY CONTEXT FIX - Must happen before any OTel imports
# -----------------------------------------------------------------------------
def _setup_opentelemetry_context():
    """
    Fix OpenTelemetry context initialization to prevent NoneType errors.
    This must run before any code that imports OpenTelemetry.
    """
    try:
        # Try to import OpenTelemetry
        from opentelemetry import context

        # Mock Context class that provides the required interface
        class MockContext:
            def __init__(self):
                self._values = {}

            def get(self, key, default=None):
                return self._values.get(key, default)

            def set(self, key, value):
                self._values[key] = value
                return self

            def copy(self):
                new_ctx = MockContext()
                new_ctx._values = self._values.copy()
                return new_ctx

        # Mock get_current function
        _mock_context = MockContext()

        def mock_get_current():
            return _mock_context

        def mock_set_value(key, value, context=None):
            ctx = context or _mock_context
            return ctx.set(key, value)

        def mock_get_value(key, context=None):
            ctx = context or _mock_context
            return ctx.get(key)

        # Replace the context functions
        context.get_current = mock_get_current
        context.set_value = mock_set_value
        context.get_value = mock_get_value
        context._CONTEXT = _mock_context

        # Also setup a minimal tracer
        from opentelemetry import trace

        class NoOpSpan:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def set_attribute(self, key, value):
                pass

            def set_status(self, status, description=None):
                pass

            def record_exception(self, exception, attributes=None, timestamp=None, escaped=None):
                pass

            def add_event(self, name, attributes=None, timestamp=None):
                pass

            def get_span_context(self):
                # Return a mock span context
                class MockSpanContext:
                    def __init__(self):
                        self.trace_id = 0
                        self.span_id = 0
                        self.is_remote = False
                        self.trace_flags = 0
                        self.trace_state = None
                        self.is_valid = False

                return MockSpanContext()

        class NoOpTracer:
            def start_as_current_span(self, name, **kwargs):
                return NoOpSpan()

            def start_span(self, name, **kwargs):
                return NoOpSpan()

        # Mock the trace functions

        def mock_get_tracer(name, version=None):
            return NoOpTracer()

        trace.get_tracer = mock_get_tracer

        # Set up INVALID_SPAN
        if hasattr(trace, "INVALID_SPAN"):
            trace.INVALID_SPAN = NoOpSpan()

        logger.debug("OpenTelemetry context mocked successfully")

    except ImportError:
        # OpenTelemetry not installed, that's fine
        logger.debug("OpenTelemetry not installed, skipping context setup")
    except Exception as e:
        logger.warning(f"Failed to setup OpenTelemetry context: {e}")


# Run the OpenTelemetry context setup immediately
_setup_opentelemetry_context()


# -----------------------------------------------------------------------------
# PLUGIN REGISTRY ISOLATION
# -----------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def isolate_plugin_registry():
    """
    Isolate the plugin registry for testing to prevent persistence conflicts.
    This fixture runs once per test session.
    """
    # Import the registry module early
    import arbiter.arbiter_plugin_registry as registry_module

    # Ensure the registry uses our test file instead of the production one
    # We need to handle both the case where the singleton exists and where it doesn't
    # Monkey-patch the default persist path before singleton creation
    if hasattr(registry_module, "PluginRegistry"):
        # Store original for potential restoration
        if registry_module.PluginRegistry._instance:
            registry_module.PluginRegistry._instance._persist_path = TEST_PLUGIN_FILE
        else:
            # Patch the class default before instantiation
            original_new = registry_module.PluginRegistry.__new__

            def patched_new(cls, persist_path=TEST_PLUGIN_FILE):
                return original_new(cls, persist_path)

            registry_module.PluginRegistry.__new__ = patched_new

    logger.info(f"Plugin registry isolated to: {TEST_PLUGIN_FILE}")

    yield

    # Cleanup after all tests
    try:
        if os.path.exists(TEST_TEMP_DIR):
            shutil.rmtree(TEST_TEMP_DIR, ignore_errors=True)
        os.environ.pop("TESTING", None)
        logger.info("Test environment cleaned up")
    except Exception as e:
        logger.warning(f"Cleanup error (non-critical): {e}")


@pytest.fixture(autouse=True)
def clear_registry_per_test():
    """
    Plugin registry fixture. We don't aggressively clear the registry before tests
    anymore because it breaks modules that register plugins at import time.
    Instead, we just ensure the test environment is set up properly.
    """
    yield
    # Minimal cleanup after tests - don't clear the entire registry


@pytest.fixture
def mock_plugin_registry(monkeypatch):
    """
    Provides a mock plugin registry for tests that need complete isolation.
    Use this fixture when you want to prevent any plugin registration.
    """
    from unittest.mock import MagicMock

    mock_registry = MagicMock()
    mock_registry.get_metadata.return_value = None
    mock_registry.register.return_value = lambda x: x
    mock_registry.register_instance.return_value = None

    monkeypatch.setattr("arbiter.arbiter_plugin_registry.registry", mock_registry)
    monkeypatch.setattr("arbiter.arbiter_plugin_registry.PLUGIN_REGISTRY", {})

    # Also mock the register decorator
    def mock_register(kind, name, version, author):
        def decorator(func):
            return func

        return decorator

    monkeypatch.setattr("arbiter.arbiter_plugin_registry.register", mock_register)

    return mock_registry


# -----------------------------------------------------------------------------
# PROMETHEUS METRICS CLEANUP
# -----------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def reset_prometheus_registry():
    """
    Prometheus registry cleanup - but DON'T unregister collectors before tests.
    This was causing issues because modules register metrics at import time.
    We only do minimal cleanup after the test.
    """
    yield
    # Only do minimal cleanup after tests, not before
    # The safe_register in the parent conftest handles duplicates gracefully


# -----------------------------------------------------------------------------
# ASYNC TEST SUPPORT
# -----------------------------------------------------------------------------
@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# -----------------------------------------------------------------------------
# (1) Starlette testclient submodule shim
# -----------------------------------------------------------------------------
try:
    import starlette  # type: ignore

    try:
        import starlette.testclient as _starlette_testclient  # type: ignore

        if getattr(starlette, "testclient", None) is None:
            setattr(starlette, "testclient", _starlette_testclient)
        if not hasattr(_starlette_testclient, "WebSocketTestSession"):
            _starlette_testclient.WebSocketTestSession = None  # type: ignore[attr-defined]
    except Exception as e:
        logger.debug("Starlette testclient shim skipped: %s", e)
except Exception as e:
    logger.debug("Starlette not importable; skipping starlette shim: %s", e)

# -----------------------------------------------------------------------------
# (2) FastAPI ↔ Pydantic v1/v2 compatibility shim
# -----------------------------------------------------------------------------
try:
    from pydantic import BaseModel  # type: ignore

    if not hasattr(BaseModel, "model_rebuild"):

        @classmethod
        def _noop_model_rebuild(cls, *args, **kwargs):
            try:
                if hasattr(cls, "update_forward_refs"):
                    cls.update_forward_refs()  # type: ignore[attr-defined]
            except Exception:
                pass
            return None

        BaseModel.model_rebuild = _noop_model_rebuild  # type: ignore[attr-defined]

    if not hasattr(BaseModel, "model_dump") and hasattr(BaseModel, "dict"):
        BaseModel.model_dump = BaseModel.dict  # type: ignore[attr-defined]

    if not hasattr(BaseModel, "model_validate"):

        @classmethod
        def _model_validate(cls, obj):
            try:
                return cls.parse_obj(obj)  # type: ignore[attr-defined]
            except Exception:
                return cls(**obj)  # type: ignore[misc]

        BaseModel.model_validate = _model_validate  # type: ignore[attr-defined]

    try:
        import fastapi._compat as fa_compat  # type: ignore

        def _compat_model_rebuild(model):
            if hasattr(model, "model_rebuild"):
                return model.model_rebuild()
            if hasattr(model, "update_forward_refs"):
                return model.update_forward_refs()  # type: ignore[attr-defined]
            return None

        fa_compat._model_rebuild = _compat_model_rebuild  # type: ignore[assignment]
    except Exception:
        pass

except Exception as e:
    logger.debug("Pydantic shim skipped: %s", e)


# -----------------------------------------------------------------------------
# (3) OpenTelemetry InMemorySpanExporter shims (attribute + submodule)
# -----------------------------------------------------------------------------
def _ensure_module_chain(modname: str) -> types.ModuleType:
    """
    Ensure every segment in `modname` exists in sys.modules, returning the final module.
    E.g., _ensure_module_chain("a.b.c") guarantees 'a', 'a.b', and 'a.b.c'.
    """
    parts = modname.split(".")
    path = []
    parent = None
    for part in parts:
        path.append(part)
        fq = ".".join(path)
        if fq not in sys.modules:
            sys.modules[fq] = types.ModuleType(fq)
        # link attribute on parent to child module, if parent exists
        if parent is not None and not hasattr(sys.modules[parent], part):
            setattr(sys.modules[parent], part, sys.modules[fq])
        parent = fq
    return sys.modules[modname]


def _install_inmemory_exporter():
    """
    Create a minimal InMemorySpanExporter class and inject it into:
      - opentelemetry.sdk.trace.export (as an attribute)
      - opentelemetry.sdk.trace.export.in_memory_span_exporter (as a submodule attr)
    This does NOT depend on any existing OpenTelemetry symbols.
    """
    parent_name = "opentelemetry.sdk.trace.export"
    submod_name = "opentelemetry.sdk.trace.export.in_memory_span_exporter"

    parent_mod = _ensure_module_chain(parent_name)
    sub_mod = _ensure_module_chain(submod_name)

    # Define a minimal exporter
    class InMemorySpanExporter:  # pragma: no cover - trivial shim
        def __init__(self, *args, **kwargs):
            self._spans = []

        def export(self, *args, **kwargs):
            return None

        def shutdown(self, *args, **kwargs):
            return None

        def clear(self):
            self._spans = []

        def get_finished_spans(self):
            return self._spans

    # Install on parent module (attribute)
    setattr(parent_mod, "InMemorySpanExporter", InMemorySpanExporter)
    # Install on submodule too
    setattr(sub_mod, "InMemorySpanExporter", InMemorySpanExporter)


# Try the standard import first; if it fails or the attribute is absent, install our shim.
_USING_SHIM = False
try:
    # Try to import the real InMemorySpanExporter
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter as _RealExporter
    # Verify it has the methods we need
    if not hasattr(_RealExporter, 'clear'):
        _install_inmemory_exporter()
        _USING_SHIM = True
except ImportError:
    # Module not available, install our shim
    _install_inmemory_exporter()
    _USING_SHIM = True
except Exception:
    _install_inmemory_exporter()
    _USING_SHIM = True


# -----------------------------------------------------------------------------
# CLEANUP FIXTURES
# -----------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def cleanup_test_files():
    """Clean up any test-generated files after each test."""
    yield
    # Clean up common test artifacts
    test_files = [
        "test_feedback.db",
        "feedback.db",
        "feedback_log.json",
        "test_plugins.json",
        "omnicore.db",
        "arbiter_knowledge.db",
        "arrays.json",
        "arrays.db",
        "test_arrays.json",
    ]
    for file in test_files:
        if os.path.exists(file):
            try:
                os.remove(file)
            except Exception as e:
                logger.debug(f"Could not remove {file}: {e}")


# -----------------------------------------------------------------------------
# Optional: register custom pytest markers
# -----------------------------------------------------------------------------
def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "integration: marks tests as integration tests")
    config.addinivalue_line("markers", "unit: marks tests as unit tests")
    config.addinivalue_line("markers", "requires_redis: marks tests that require Redis")
    config.addinivalue_line(
        "markers", "requires_db: marks tests that require a database"
    )


# -----------------------------------------------------------------------------
# TEST DATA FIXTURES
# -----------------------------------------------------------------------------
@pytest.fixture
def sample_decision_context():
    """Provides a sample decision context for testing."""
    return {
        "decision_id": "test_decision_123",
        "action": "deploy_model",
        "risk_level": "high",
        "details": {
            "model_name": "test_model",
            "environment": "production",
            "confidence": 0.95,
        },
    }


@pytest.fixture
def sample_feedback():
    """Provides sample feedback data for testing."""
    return {
        "decision_id": "test_decision_123",
        "approved": True,
        "user_id": "test_user",
        "comment": "Looks good for deployment",
        "timestamp": "2024-01-01T00:00:00Z",
        "signature": "test_signature",
    }


# -----------------------------------------------------------------------------
# MOCK OPENTELEMETRY FIXTURE
# -----------------------------------------------------------------------------
@pytest.fixture
def mock_opentelemetry(monkeypatch):
    """
    Provides a mock OpenTelemetry setup for tests that need it.
    """

    class MockSpan:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def set_attribute(self, key, value):
            pass

        def set_status(self, status):
            pass

        def get_span_context(self):
            class MockSpanContext:
                def __init__(self):
                    self.trace_id = 0
                    self.span_id = 0
                    self.is_remote = False
                    self.trace_flags = 0
                    self.trace_state = None
                    self.is_valid = False

            return MockSpanContext()

    class MockTracer:
        def start_as_current_span(self, name, **kwargs):
            return MockSpan()

        def start_span(self, name, **kwargs):
            return MockSpan()

    class MockTrace:
        @staticmethod
        def get_tracer(name):
            return MockTracer()

    # Mock the opentelemetry module
    mock_trace = MockTrace()
    monkeypatch.setattr("opentelemetry.trace.get_tracer", lambda x: MockTracer())

    return mock_trace


# -----------------------------------------------------------------------------
# SESSION CLEANUP
# -----------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def session_cleanup():
    """Final cleanup after all tests complete."""
    yield

    # Final cleanup of any remaining test artifacts
    logger.info("Running final session cleanup")

    # Remove any SQLite databases created during tests
    for db_file in Path(".").glob("*.db"):
        if "test" in db_file.name.lower() or db_file.name in [
            "feedback.db",
            "omnicore.db",
            "arrays.db",
        ]:
            try:
                db_file.unlink()
            except:
                pass

    # Clean up any remaining json files
    for json_file in Path(".").glob("*test*.json"):
        try:
            json_file.unlink()
        except:
            pass

    # Clean up arrays.json if it exists
    if Path("arrays.json").exists():
        try:
            Path("arrays.json").unlink()
        except:
            pass


# -----------------------------------------------------------------------------
# END OF CONFTEST.PY
# -----------------------------------------------------------------------------
