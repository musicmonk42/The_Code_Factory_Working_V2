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
# 3. Module mocking is now handled via pytest fixtures
#    DO NOT mock modules at import time with MagicMock as this causes
#    mypy/compile errors when MagicMock pollutes type annotations
# -------------------------------------------------

# Modules that may need to be mocked in individual tests can use fixtures
# See the mock_modules fixture below

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


@pytest.fixture
def mock_modules(monkeypatch):
    """
    Fixture to mock modules needed by tests.
    Use this in tests that need specific modules mocked, for example:

    def test_something(mock_modules):
        # Mock specific modules for this test
        mock_modules(['runner.runner_core', 'intent_parser.intent_parser'])
        # ... test code that imports those modules

    Note: This fixture uses MagicMock to create module mocks, so tests using this
    fixture should only be run with pytest, not with static type checkers.
    """

    def _mock_modules(module_names):
        from unittest.mock import MagicMock

        for name in module_names:
            monkeypatch.setitem(sys.modules, name, MagicMock(name=f"mock_{name}"))

    return _mock_modules


@pytest.fixture(autouse=True)
def protect_pydantic_decorators(monkeypatch):
    """
    Ensure pydantic decorators remain callable to prevent
    'PydanticUserError: A non-annotated attribute was detected' errors.
    """
    try:
        import pydantic

        # Create a no-op decorator that preserves function behavior
        def _noop_decorator(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        # Only patch if pydantic decorators have been replaced with non-callables
        if not callable(getattr(pydantic, "field_validator", None)):
            monkeypatch.setattr(pydantic, "field_validator", _noop_decorator)
        if not callable(getattr(pydantic, "model_validator", None)):
            monkeypatch.setattr(pydantic, "model_validator", _noop_decorator)
    except ImportError:
        pass

    yield


def pytest_configure(config):
    config.option.asyncio_mode = "auto"
