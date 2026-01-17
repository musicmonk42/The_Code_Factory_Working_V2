# generator/runner/tests/conftest.py
import os
import pathlib
import tempfile

# --- CRITICAL ENVIRONMENT SETUP (MUST BE FIRST) ---
# Set TESTING flags for conditional logic in runner modules
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DEV_MODE", "1")

# Create and set the LLM plugin directory (Dynaconf/LLMPluginManager dependency)
# Setting both formats for Dynaconf compatibility
tmp_plugins = pathlib.Path(tempfile.gettempdir()) / f"plugins_pytest_{os.getpid()}"
tmp_plugins.mkdir(exist_ok=True)
os.environ.setdefault("LLM_PLUGIN__PLUGIN_DIR", str(tmp_plugins))
os.environ.setdefault("LLM_PLUGIN_PLUGIN_DIR", str(tmp_plugins))

# Silence noisy third-party libraries (OTEL, Audit Crypto)
os.environ.setdefault("OTEL_SDK_DISABLED", "1")
os.environ.setdefault("AUDIT_CRYPTO_DISABLE_IMPORT_VALIDATE", "1")

# === 2. Set Dynaconf DEVELOPMENT environment variables ===
# These configurations ensure audit/crypto functions fall back gracefully in DEV mode
# --- FIX: Corrected variable name ---
os.environ["PROVIDER_TYPE"] = "software"
# --- END FIX ---
os.environ["AUDIT_CRYPTO_DEVELOPMENT_DEFAULT_ALGO"] = "hmac"
os.environ["AUDIT_CRYPTO_DEVELOPMENT_KEY_ROTATION_INTERVAL_SECONDS"] = "86400"
os.environ["AUDIT_CRYPTO_DEVELOPMENT_SOFTWARE_KEY_DIR"] = str(
    pathlib.Path(tempfile.gettempdir()) / "pytest-keys"
)  # Use tempdir
os.environ["AUDIT_CRYPTO_DEVELOPMENT_KMS_KEY_ID"] = "dummy-kms-key"
os.environ["AUDIT_CRYPTO_DEVELOPMENT_AWS_REGION"] = "us-east-1"


# === 3. Add project root to path ===
project_root = pathlib.Path(__file__).parent.parent.parent
import sys

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio

# === 3.5. Setup OpenTelemetry mocks BEFORE importing runner modules ===
# This prevents ImportError when runner modules try to import opentelemetry.trace
if "opentelemetry" not in sys.modules:
    try:
        __import__("opentelemetry")
    except ImportError:
        # Create minimal OpenTelemetry stubs required by runner modules
        import types
        import importlib.util

        # Create no-op tracer and span classes
        class _NoOpTracer:
            def start_as_current_span(self, name, **kwargs):
                from contextlib import nullcontext

                return nullcontext()

        class _NoOpSpan:
            def set_attribute(self, *args, **kwargs):
                pass

            def add_event(self, *args, **kwargs):
                pass

            def set_status(self, *args, **kwargs):
                pass

            def record_exception(self, *args, **kwargs):
                pass

        # Create trace module
        trace_module = types.ModuleType("opentelemetry.trace")
        trace_module.__file__ = "<mocked opentelemetry.trace>"
        trace_module.__path__ = []
        trace_module.__spec__ = importlib.util.spec_from_loader(
            "opentelemetry.trace", loader=None
        )
        trace_module.get_tracer = lambda *args, **kwargs: _NoOpTracer()
        trace_module.get_current_span = lambda: _NoOpSpan()
        trace_module.get_tracer_provider = lambda: None

        # Create main opentelemetry module
        otel_module = types.ModuleType("opentelemetry")
        otel_module.__file__ = "<mocked opentelemetry>"
        otel_module.__path__ = []
        otel_module.__spec__ = importlib.util.spec_from_loader(
            "opentelemetry", loader=None
        )
        otel_module.trace = trace_module

        # Register modules
        sys.modules["opentelemetry"] = otel_module
        sys.modules["opentelemetry.trace"] = trace_module

# === 4. Pytest config & Fixtures ===
import pytest
from runner import (
    llm_client,
)  # Import the module namespace to access the global variable


def pytest_configure(config):
    """Sets pytest configuration options."""
    config.option.asyncio_mode = "auto"


# CRITICAL FIX for asynchronous cleanup hang during teardown
# We must explicitly shut down the global LLMClient singleton if it was initialized.
@pytest.fixture(scope="session")
def event_loop():
    """Ensure a session-scoped event loop for cleaner async finalizers."""
    # Get or create event loop
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    yield loop

    # Minimal cleanup - just close the loop at the very end
    try:
        loop.close()
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
async def async_cleanup_global_client():
    """
    Session-scoped async fixture to explicitly await LLMClient.close()
    on the global singleton instance, resolving the KeyboardInterrupt issue.
    """
    yield
    # CRITICAL FIX: If the global client was initialized, await its close method.
    if llm_client._async_client:
        try:
            # Explicitly await the cleanup of all aiohttp/redis resources with timeout.
            await asyncio.wait_for(llm_client._async_client.close(), timeout=5.0)
        except asyncio.TimeoutError:
            print(
                "\n[CLEANUP TIMEOUT] Global LLMClient cleanup timed out after 5s",
                file=sys.stderr,
            )
        except Exception as e:
            # Log the error but continue teardown
            print(
                f"\n[CLEANUP ERROR] Failed to close global LLMClient: {e}",
                file=sys.stderr,
            )
        finally:
            llm_client._async_client = None
