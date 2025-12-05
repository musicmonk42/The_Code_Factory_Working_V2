# generator/main/tests/conftest.py
# -------------------------------------------------
# 1. Environment – set BEFORE any imports
# -------------------------------------------------
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Core test flags
os.environ["TESTING"] = "1"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-testing"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

# Dynaconf "main" environment (the one that crashes)
os.environ["AUDIT_CRYPTO_MAIN_PROVIDER_TYPE"] = "software"
os.environ["AUDIT_CRYPTO_MAIN_DEFAULT_ALGO"] = "hmac"
os.environ["AUDIT_CRYPTO_MAIN_KEY_ROTATION_INTERVAL_SECONDS"] = "86400"
os.environ["AUDIT_CRYPTO_MAIN_SOFTWARE_KEY_DIR"] = "/tmp/pytest-keys"
os.environ["AUDIT_CRYPTO_MAIN_KMS_KEY_ID"] = "dummy-kms-key-for-test"
os.environ["AUDIT_CRYPTO_MAIN_AWS_REGION"] = "us-east-1"

# Default environment (still used by many modules)
os.environ["AUDIT_CRYPTO_PROVIDER_TYPE"] = "software"
os.environ["AUDIT_CRYPTO_DEFAULT_ALGO"] = "hmac"
os.environ["AUDIT_CRYPTO_KEY_ROTATION_INTERVAL_SECONDS"] = "86400"
os.environ["AUDIT_CRYPTO_SOFTWARE_KEY_DIR"] = "/tmp/pytest-keys"
os.environ["AUDIT_CRYPTO_KMS_KEY_ID"] = "dummy-kms-key-for-test"
os.environ["AUDIT_CRYPTO_AWS_REGION"] = "us-east-1"

# -------------------------------------------------
# 2. Project root on PYTHONPATH
# -------------------------------------------------
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# -------------------------------------------------
# 3. Early module mocking (before test collection)
#    These must be mocked BEFORE any test files try to import them
# -------------------------------------------------

# List of modules that need to be mocked for tests
MOCKED_MODULES = [
    "generator.runner.runner_config",
    "generator.runner.runner_core",
    "generator.runner.runner_logging",
    "generator.runner.runner_metrics",
    "generator.runner.alerting",
    "generator.runner.llm_plugin_manager",
    "generator.audit_log.audit_log",
    "generator.audit_log.audit_crypto.audit_crypto_factory",
    "generator.audit_log.audit_crypto.audit_crypto_ops",
    "generator.audit_log.audit_crypto.audit_crypto_provider",
    "generator.audit_log.audit_crypto.audit_keystore",
    "generator.audit_log.audit_crypto.secrets",
    "generator.engine",  # fixes "No module named 'engine'"
    "generator.main.api",
    "generator.main.cli",
    "generator.main.gui",
    # External libs used inside the code under test
    "uvicorn",
    "aiofiles",  # Required by generator.main.api
    "opentelemetry",
    "opentelemetry.sdk",
    "opentelemetry.trace",
    "opentelemetry.sdk.trace",
    "opentelemetry.sdk.trace.export",
    "opentelemetry.exporter.otlp.proto.grpc",
    "opentelemetry.semconv.trace",
    "boto3",
    # Additional mocks needed by test_api.py (without generator. prefix)
    "runner.runner_core",
    "runner.runner_config",
    "runner.runner_logging",
    "runner.runner_metrics",
    "runner.runner_utils",
    "intent_parser.intent_parser",
]

# Mock modules ONLY if they're not already imported AND we're in test mode
# This needs to happen at conftest import time to prevent import errors
if os.environ.get("TESTING") == "1":
    from unittest.mock import MagicMock
    for name in MOCKED_MODULES:
        if name not in sys.modules:
            # Use a dedicated mock for each module to avoid shared state
            sys.modules[name] = MagicMock(name=f"mock_{name}")

# -------------------------------------------------
# 4. Pytest fixtures
# -------------------------------------------------
import pytest
from prometheus_client import REGISTRY


@pytest.fixture(autouse=True)
def clear_prometheus_registry():
    """Remove all Prometheus collectors before/after each test."""
    for collector in list(REGISTRY._names_to_collectors.values()):
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass
    yield


@pytest.fixture(autouse=True)
def reset_test_env():
    """Guarantee the env vars are present for every test."""
    os.environ["TESTING"] = "1"
    os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-testing"
    for k, v in {
        "AUDIT_CRYPTO_MAIN_PROVIDER_TYPE": "software",
        "AUDIT_CRYPTO_MAIN_DEFAULT_ALGO": "hmac",
        "AUDIT_CRYPTO_MAIN_KEY_ROTATION_INTERVAL_SECONDS": "86400",
        "AUDIT_CRYPTO_MAIN_SOFTWARE_KEY_DIR": "/tmp/pytest-keys",
        "AUDIT_CRYPTO_MAIN_KMS_KEY_ID": "dummy-kms-key-for-test",
        "AUDIT_CRYPTO_MAIN_AWS_REGION": "us-east-1",
    }.items():
        os.environ[k] = v
    yield


def pytest_configure(config):
    config.option.asyncio_mode = "auto"
