# tests/test_dlt_base.py

import hashlib
import hmac
import json
import os
import time
from collections import namedtuple
from unittest.mock import AsyncMock, MagicMock, mock_open

import pytest

# Import the main file under test
from simulation.plugins.dlt_clients.dlt_base import (
    AuditManager,
    CircuitBreaker,
    DLTClientCircuitBreakerError,
    DLTClientQueryError,
    SecretsManager,
    async_retry,
    scrub_secrets,
)


# Mock external dependencies for isolated testing
@pytest.fixture(autouse=True)
def mock_external_deps(mocker):
    mocker.patch.object(os, "getenv", return_value="false")
    mocker.patch("simulation.plugins.dlt_clients.dlt_base._base_logger")
    mocker.patch("simulation.plugins.dlt_clients.dlt_base.alert_operator", new=AsyncMock())
    mocker.patch("simulation.plugins.dlt_clients.dlt_base.time.time", return_value=1672531200.0)
    mocker.patch("simulation.plugins.dlt_clients.dlt_base.os.makedirs")
    mocker.patch("simulation.plugins.dlt_clients.dlt_base.os.path.exists", return_value=False)
    mocker.patch("simulation.plugins.dlt_clients.dlt_base.os.path.dirname", return_value=".")
    mocker.patch(
        "simulation.plugins.dlt_clients.dlt_base.os.path.abspath",
        side_effect=lambda x: f"/mock/path/{x}",
    )
    mocker.patch(
        "simulation.plugins.dlt_clients.dlt_base.os.urandom",
        return_value=b"test-hmac-key-for-testing-only-123",
    )
    mocker.patch("simulation.plugins.dlt_clients.dlt_base.json.dump")
    mocker.patch(
        "simulation.plugins.dlt_clients.dlt_base.json.load",
        return_value={
            "last_verified_entry_count": 0,
            "last_verification_time": "2025-01-01T00:00:00",
        },
    )
    mocker.patch("simulation.plugins.dlt_clients.dlt_base.asyncio.create_task")
    mocker.patch("simulation.plugins.dlt_clients.dlt_base.atexit.register")

    # Mock Prometheus metrics to prevent real registration errors during tests
    namedtuple("MockCounter", ["labels", "inc"])
    namedtuple("MockHistogram", ["labels", "observe"])
    namedtuple("MockGauge", ["labels", "set"])
    mocker.patch(
        "simulation.plugins.dlt_clients.dlt_base.Counter",
        return_value=MagicMock(labels=MagicMock(return_value=MagicMock())),
    )
    mocker.patch(
        "simulation.plugins.dlt_clients.dlt_base.Histogram",
        return_value=MagicMock(labels=MagicMock(return_value=MagicMock())),
    )
    mocker.patch(
        "simulation.plugins.dlt_clients.dlt_base.Gauge",
        return_value=MagicMock(labels=MagicMock(return_value=MagicMock())),
    )


@pytest.mark.asyncio
async def test_circuit_breaker_trip_and_recovery(mocker):
    """
    Test scenario: Circuit breaker trips, blocks subsequent calls, then enters
    HALF_OPEN state and recovers after a successful call.
    """
    cb = CircuitBreaker("TestClient", failure_threshold=2, reset_timeout=1)
    mock_operation = AsyncMock(side_effect=Exception("Simulated failure"))

    # Stage 1: Trip the circuit breaker
    with pytest.raises(Exception):
        await cb.execute(mock_operation)
    assert cb.state == "CLOSED"
    assert cb.failures == 1

    with pytest.raises(Exception):
        await cb.execute(mock_operation)
    assert cb.state == "OPEN"
    assert cb.failures == 2
    cb.last_failure_time = time.time()  # Ensure time is set

    # Stage 2: Verify requests are blocked in OPEN state
    with pytest.raises(DLTClientCircuitBreakerError):
        await cb.execute(mock_operation)
    assert cb.state == "OPEN"
    assert mock_operation.call_count == 2  # No new call made

    # Stage 3: Advance time and verify HALF_OPEN state
    mocker.patch("simulation.plugins.dlt_clients.dlt_base.time.time", return_value=1672531202.0)
    mock_operation.side_effect = None  # Reset mock for successful call
    mock_operation.return_value = "Success"

    result = await cb.execute(mock_operation)
    assert cb.state == "CLOSED"
    assert cb.failures == 0
    assert result == "Success"


@pytest.mark.asyncio
async def test_audit_manager_integrity_check_success(mocker):
    """
    Test scenario: Audit log integrity check passes with a valid log file.
    """
    # Setup mock files
    mock_hmac_key = b"test-hmac-key-for-testing-only-123"
    mocker.patch(
        "simulation.plugins.dlt_clients.dlt_base._get_dlt_audit_hmac_key",
        return_value=mock_hmac_key,
    )

    event_data = {
        "event_type": "test",
        "payload": {"key": "value"},
        "timestamp": "2025-01-01T00:00:00.000000",
    }
    event_json_str = json.dumps(event_data, sort_keys=True, ensure_ascii=False)
    signature = hmac.new(mock_hmac_key, event_json_str.encode("utf-8"), hashlib.sha256).hexdigest()
    signed_event = {"event": event_data, "signature": signature}

    # Mock file reading
    mock_file = mock_open(read_data=json.dumps(signed_event) + "\n")
    mocker.patch("builtins.open", mock_file)
    mocker.patch("simulation.plugins.dlt_clients.dlt_base.os.path.exists", return_value=True)

    # Create AuditManager instance
    am = AuditManager()

    # Run the test
    result = await am.verify_integrity(max_age_hours=0)  # Force a check

    assert result is True


@pytest.mark.asyncio
async def test_audit_manager_integrity_check_failure(mocker):
    """
    Test scenario: Audit log integrity check fails due to a tampered entry.
    """
    # Setup mock files
    mock_hmac_key = b"test-hmac-key-for-testing-only-123"
    mocker.patch(
        "simulation.plugins.dlt_clients.dlt_base._get_dlt_audit_hmac_key",
        return_value=mock_hmac_key,
    )

    tampered_event = {
        "event_type": "test",
        "payload": {"key": "malicious"},
        "timestamp": "2025-01-01T00:00:00.000000",
    }
    tampered_signature = "invalid_signature_123"
    signed_event = {"event": tampered_event, "signature": tampered_signature}

    # Mock file reading
    mock_file = mock_open(read_data=json.dumps(signed_event) + "\n")
    mocker.patch("builtins.open", mock_file)
    mocker.patch("simulation.plugins.dlt_clients.dlt_base.os.path.exists", return_value=True)

    # Mock logger
    mock_logger = MagicMock()
    mocker.patch("simulation.plugins.dlt_clients.dlt_base._base_logger", mock_logger)

    # Create AuditManager instance
    am = AuditManager()

    # Run the test
    result = await am.verify_integrity(max_age_hours=0)

    assert result is False
    # Check that critical log was called
    assert mock_logger.critical.called


@pytest.mark.asyncio
async def test_secrets_manager_production_mode_enforcement(mocker):
    """
    Test scenario: Raises RuntimeError if a required secret is missing.
    """
    mocker.patch.object(os, "getenv", side_effect=lambda key, default=None: None)

    sm = SecretsManager()
    with pytest.raises(RuntimeError, match="Missing required secret"):
        sm.get_secret("MISSING_REQUIRED_SECRET", required=True)


def test_scrub_secrets_utility():
    """
    Test scenario: Verify sensitive data is correctly scrubbed from various data types.
    """
    # Clear the LRU cache first
    scrub_secrets.cache_clear()

    data = {
        "user_data": "some_data",
        "api_key": "Akia1234567890123456",
        "password": "my-secret-password-1234",
        "nested_dict": {
            "bearer_token": "Bearer some-long-jwt-token-with.dots.and-dashes",
            "private_key": "0x1234567890123456789012345678901234567890123456789012345678901234",
        },
        "id_list": ["id-123", "secret_key=shh-don't-tell"],
        "credit_card": "1234-5678-9012-3456",
        "ssn": "999-99-9999",
    }

    # Convert to JSON string to make it hashable for LRU cache
    data_str = json.dumps(data, sort_keys=True)
    scrubbed_str = scrub_secrets(data_str)

    # Verify scrubbing worked
    assert "Akia1234567890123456" not in scrubbed_str
    assert "my-secret-password-1234" not in scrubbed_str
    assert "Bearer some-long-jwt-token-with.dots.and-dashes" not in scrubbed_str
    assert "[SCRUBBED]" in scrubbed_str
    assert "some_data" in scrubbed_str  # Non-sensitive data should remain


@pytest.mark.asyncio
async def test_async_retry_decorator(mocker):
    """
    Test scenario: An async function is retried correctly until it succeeds.
    """
    call_count = 0

    class MockClient:
        def __init__(self):
            self.client_type = "TestClient"
            self.config = {
                "retry_policy": {
                    "retries": 3,
                    "delay": 0.1,
                    "backoff": 2,
                    "jitter": False,
                }
            }

        @async_retry(catch_exceptions=DLTClientQueryError)
        async def flaky_operation(self):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise DLTClientQueryError("Flaky network call", "TestClient")
            return "Success"

    mock_client = MockClient()

    result = await mock_client.flaky_operation()

    assert result == "Success"
    assert call_count == 3

    # Test failure after max retries
    call_count = 0
    mock_client.config = {
        "retry_policy": {"retries": 2, "delay": 0.1, "backoff": 2, "jitter": False}
    }

    with pytest.raises(DLTClientQueryError):
        await mock_client.flaky_operation()

    assert call_count == 2
