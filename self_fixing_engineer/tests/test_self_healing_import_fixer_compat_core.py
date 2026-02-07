# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
test_compat_core.py

Unit tests for compat_core.py, ensuring enterprise-grade reliability, security, and compliance.
Covers initialization, fallbacks, audit logging, metrics, tracing, and health checks.
"""

import hashlib
import hmac
import json
import logging
import os
from unittest.mock import MagicMock, patch

import pytest

# Import using the full package path
from self_healing_import_fixer.import_fixer.compat_core import (
    ENVIRONMENT,
    PRODUCTION_MODE,
    SECRETS_MANAGER,
    _NoOpMetric,
    alert_operator,
    audit_logger,
    core_statuses,
    get_core_health,
    logger,
    verify_audit_log,
)

PKG_PATH = "self_healing_import_fixer.import_fixer.compat_core"


# --- Test Fixtures ---
@pytest.fixture(autouse=True)
def setup_logging():
    """Reset logging handlers to avoid interference between tests."""
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    for handler in logging.getLogger("audit_fallback").handlers[:]:
        logging.getLogger("audit_fallback").removeHandler(handler)


@pytest.fixture
def mock_env_vars():
    """Set up default environment variables."""
    with patch.dict(
        os.environ,
        {
            "PRODUCTION_MODE": "false",
            "APP_ENV": "test",
            "AUDIT_LOG_ENABLED": "true",
            "METRICS_ENABLED": "true",
            "TRACING_ENABLED": "true",
            "LOG_LEVEL": "INFO",
            "METRICS_PORT": "8000",
            "OTLP_ENDPOINT": "https://localhost:4317",
            "OTEL_EXPORTER_OTLP_INSECURE": "false",
            "AUDIT_LOG_HMAC_KEY": "test-hmac-key",
        },
    ):
        yield


@pytest.fixture
def mock_core_modules():
    """Mock core modules to simulate successful imports."""
    with patch(f"{PKG_PATH}.__import__") as mock_import:
        mock_module = MagicMock()
        mock_module.alert_operator = MagicMock()
        mock_module.scrub_secrets = lambda x: f"scrubbed_{x}"
        mock_audit_logger = MagicMock()
        mock_module.get_audit_logger = lambda: mock_audit_logger
        mock_module.SECRETS_MANAGER = MagicMock()
        mock_module.SECRETS_MANAGER.get_secret = MagicMock(return_value="test-secret")

        def side_effect(name, fromlist=None):
            if name in [
                "analyzer.core_utils",
                "analyzer.core_audit",
                "analyzer.core_secrets",
            ]:
                return mock_module
            raise ImportError(f"No module named '{name}'")

        mock_import.side_effect = side_effect
        yield mock_import


@pytest.fixture
def mock_redis():
    """Mock Redis client for caching tests."""
    with patch(f"{PKG_PATH}.redis") as mock_redis_module:
        mock_client = MagicMock()
        mock_client.get.return_value = None
        mock_client.setex = MagicMock()
        mock_redis_module.Redis.return_value = mock_client
        yield mock_redis_module


@pytest.fixture
def mock_boto3():
    """Mock boto3 for S3 offloading."""
    with patch(f"{PKG_PATH}.boto3_client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3
        yield mock_boto


# --- Test Cases ---
@pytest.mark.asyncio
async def test_fallback_behavior(mock_env_vars, mock_redis, mock_boto3):
    """Test fallback implementations in non-production mode when core modules fail to import."""
    # The modules will already be initialized on import, but we can check the fallback state
    assert not all(status.loaded for status in core_statuses.values())

    # Test fallback secrets manager
    assert SECRETS_MANAGER.get_secret("TEST_KEY", required=False) is None

    # Test fallback alert operator
    alert_operator("Test alert", level="DEBUG")

    # Test fallback audit logger
    audit_logger.log_event("test_event", key="value")


def test_health_check(mock_env_vars, mock_redis, mock_boto3):
    """Test get_core_health output."""
    health = json.loads(get_core_health())
    assert "initialized" in health
    assert "modules" in health
    assert "error" in health


def test_verify_audit_log():
    """Test HMAC signature verification for audit logs."""
    # Create a properly signed log entry
    secret = "test-secret"
    payload = {"event": "test", "data": "value"}
    signature = hmac.new(
        secret.encode(),
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()

    log_entry = json.dumps({"payload": payload, "signature": signature})

    assert verify_audit_log(log_entry, secret) is True

    # Test with wrong secret
    assert verify_audit_log(log_entry, "wrong-secret") is False

    # Test with malformed entry
    assert verify_audit_log("{}", secret) is False


def test_environment_variables():
    """Test that environment variables are properly read."""
    assert ENVIRONMENT == "test"
    assert not PRODUCTION_MODE


def test_metrics_noop():
    """Test that NoOpMetric class works correctly."""
    # Test the actual NoOpMetric class
    noop = _NoOpMetric()

    # These should be no-ops and not raise errors
    noop.observe(1.0)
    noop.inc()
    noop.set(5)

    # Test labels method returns self
    labeled = noop.labels(test="value")
    assert labeled is noop

    # Chain operations should work
    noop.labels(a="b").inc()
    noop.labels(x="y").observe(2.0)


if __name__ == "__main__":
    pytest.main(["-v", "--asyncio-mode=auto", __file__])
