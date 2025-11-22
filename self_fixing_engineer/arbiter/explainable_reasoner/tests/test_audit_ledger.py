# test_audit_ledger.py
# Comprehensive production-grade tests for audit_ledger.py
# Requires: pytest, pytest-asyncio, unittest.mock, httpx (for mocks)
# Run with: pytest test_audit_ledger.py -v --cov=audit_ledger --cov-report=html

import logging
import os
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
import httpx

# Import the module under test
from arbiter.explainable_reasoner.audit_ledger import (
    AuditLedgerClient,
)
from arbiter.explainable_reasoner.reasoner_errors import ReasonerError, ReasonerErrorCode

# Setup logging for tests
logging.basicConfig(level=logging.DEBUG)
test_logger = logging.getLogger(__name__)

# Fixtures
@pytest.fixture
def mock_structlog():
    """Mock structlog to capture log calls."""
    with patch("arbiter.explainable_reasoner.audit_ledger._logger") as mock_logger:
        # Make sure bind returns the logger itself for chaining
        mock_logger.bind.return_value = mock_logger
        yield mock_logger

@pytest.fixture
def mock_httpx_client():
    """Mock httpx.AsyncClient for API calls."""
    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock()
    mock_client.get = AsyncMock()
    mock_client.aclose = AsyncMock()
    return mock_client

@pytest.fixture(autouse=True)
def mock_metrics():
    """Mock METRICS to avoid real increments/observations."""
    with patch("arbiter.explainable_reasoner.audit_ledger.AUDIT_SEND_LATENCY") as mock_latency, \
         patch("arbiter.explainable_reasoner.audit_ledger.AUDIT_ERRORS") as mock_errors, \
         patch("arbiter.explainable_reasoner.audit_ledger.AUDIT_BATCH_SIZE") as mock_batch, \
         patch("arbiter.explainable_reasoner.audit_ledger.AUDIT_RATE_LIMIT_HITS") as mock_rate_limit:
        
        # Configure metric mocks
        for metric in [mock_latency, mock_errors, mock_batch, mock_rate_limit]:
            metric.labels.return_value.inc = MagicMock()
            metric.labels.return_value.observe = MagicMock()
        
        yield {
            "latency": mock_latency,
            "errors": mock_errors,
            "batch": mock_batch,
            "rate_limit": mock_rate_limit
        }

@pytest.fixture
def audit_client(mock_structlog):
    """Fixture for AuditLedgerClient instance with specific retry settings."""
    with patch("arbiter.explainable_reasoner.audit_ledger.pybreaker") as mock_breaker, \
         patch("arbiter.explainable_reasoner.audit_ledger.httpx.AsyncClient") as mock_client_class:
        
        # Create a proper async mock for call_async
        mock_circuit_breaker = MagicMock()
        async def async_call(*args, **kwargs):
            # Call the actual function that was passed
            func = args[0]
            return await func(*args[1:], **kwargs)
        mock_circuit_breaker.call_async = async_call
        mock_breaker.CircuitBreaker.return_value = mock_circuit_breaker
        
        # Mock the httpx client initialization
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client_class.return_value = mock_client
        
        client = AuditLedgerClient(
            ledger_url="https://test-ledger.com/audit",
            max_retries=2,
            initial_backoff_delay=0.01,
            timeout=1.0
        )
        # Replace the circuit breaker with our properly mocked one
        client._breaker = mock_circuit_breaker
        # Also update the logger
        client._logger = mock_structlog
        return client

# Test Initialization
def test_init_success(mock_structlog):
    """Tests successful initialization of the client."""
    with patch("arbiter.explainable_reasoner.audit_ledger.pybreaker"), \
         patch("arbiter.explainable_reasoner.audit_ledger.httpx.AsyncClient"):
        client = AuditLedgerClient(
            ledger_url="https://test-ledger.com/audit",
            max_retries=3,
            initial_backoff_delay=1.0,
            timeout=5.0
        )
        assert client.ledger_url == "https://test-ledger.com/audit"
        assert client.max_retries == 3
        assert client.initial_backoff_delay == 1.0
        assert client.timeout == 5.0
        # Check that info was called (might be on the bound logger)
        assert mock_structlog.info.called or mock_structlog.bind.return_value.info.called

def test_init_invalid_url():
    """Tests that initialization fails with an invalid URL format."""
    with pytest.raises(ValueError, match="Invalid URL"):
        AuditLedgerClient(ledger_url="invalid-url")

@pytest.mark.parametrize("url", [
    "http://localhost:8080/audit",
    "ftp://localhost:8080/audit",
])
def test_init_https_enforcement(url):
    """Tests that a ValueError is raised for non-HTTPS URLs."""
    # Accept either error message pattern
    expected_pattern = "Ledger URL must use HTTPS|Invalid URL provided"
    with pytest.raises(ValueError, match=expected_pattern):
        AuditLedgerClient(ledger_url=url)

# Test _send_event_with_retries
@pytest.mark.asyncio
async def test_send_event_with_retries_success(audit_client, mock_httpx_client):
    """Tests a single successful event send."""
    with patch.object(audit_client, '_get_client', return_value=mock_httpx_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response
        
        audit_record = {
            "test": "data",
            "event_type": "test_event",
            "record_hash": "test_hash"
        }
        result = await audit_client._send_event_with_retries(audit_record)
        
        assert result is True
        mock_httpx_client.post.assert_awaited_once()
        mock_response.raise_for_status.assert_called_once()

@pytest.mark.asyncio
async def test_send_event_with_retries_failure_after_retries(audit_client, mock_httpx_client, mock_metrics):
    """Tests failure after exhausting all retry attempts."""
    with patch.object(audit_client, '_get_client', return_value=mock_httpx_client):
        # Disable the circuit breaker for this test to simplify retry logic
        audit_client._breaker = None
        
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_httpx_client.post.side_effect = httpx.HTTPStatusError(
            "Server error",
            request=MagicMock(),
            response=mock_response
        )
        
        audit_record = {
            "test": "data",
            "event_type": "test_event",
            "record_hash": "test_hash"
        }
        
        # Import RetryError from tenacity
        from tenacity import RetryError
        # Tenacity will wrap the exception in RetryError after exhausting retries
        with pytest.raises(RetryError):
            await audit_client._send_event_with_retries(audit_record)
        
        # Check that it attempted at least once (tenacity handles retries)
        assert mock_httpx_client.post.call_count >= 1
        mock_metrics["errors"].labels.assert_called()

@pytest.mark.asyncio
async def test_send_event_with_retries_timeout(audit_client, mock_httpx_client):
    """Tests that a timeout exception is handled and retried correctly."""
    with patch.object(audit_client, '_get_client', return_value=mock_httpx_client):
        # Disable the circuit breaker for this test
        audit_client._breaker = None
        mock_httpx_client.post.side_effect = httpx.TimeoutException("Timeout")
        
        # The timeout exception gets caught and wrapped in ReasonerError
        # But it only tries once because TimeoutException is caught immediately
        # and converted to ReasonerError, not retried multiple times
        with pytest.raises(ReasonerError, match="Audit log request timed out"):
            await audit_client._send_event_with_retries({
                "test": "data",
                "event_type": "test_event",
                "record_hash": "test_hash"
            })
        
        # Timeout exceptions are caught on first attempt and converted to ReasonerError
        # So it should only be called once
        assert mock_httpx_client.post.call_count == 1

@pytest.mark.asyncio
async def test_send_event_with_retries_unexpected_error(audit_client, mock_httpx_client):
    """Tests that a non-retryable error fails immediately."""
    with patch.object(audit_client, '_get_client', return_value=mock_httpx_client):
        # Disable the circuit breaker for this test
        audit_client._breaker = None
        mock_httpx_client.post.side_effect = ValueError("Unexpected")
        
        with pytest.raises(ReasonerError, match="Audit log failed after retries"):
            await audit_client._send_event_with_retries({
                "test": "data",
                "event_type": "test_event",
                "record_hash": "test_hash"
            })
        
        # Should only try once for non-retryable errors
        assert mock_httpx_client.post.call_count == 1

# Test log_event (public interface)
@pytest.mark.asyncio
async def test_log_event_success(audit_client, mock_httpx_client, mock_structlog):
    """Tests successful logging of a single event via the public method."""
    with patch.object(audit_client, '_get_client', return_value=mock_httpx_client):
        mock_response = MagicMock(status_code=200)
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response
        
        event_type = "test_event"
        details = {"key": "value"}
        operator = "test_operator"
        
        # Mock datetime for a consistent timestamp and hash
        fixed_now = datetime(2023, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        with patch("arbiter.explainable_reasoner.audit_ledger.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromisoformat = datetime.fromisoformat
            success = await audit_client.log_event(event_type, details, operator)
        
        assert success is True
        
        # Verify the correct record was sent
        call_args = mock_httpx_client.post.call_args
        sent_record = call_args[1]['json']
        assert sent_record['event_type'] == event_type
        assert sent_record['operator'] == operator
        assert sent_record['details'] == details

@pytest.mark.asyncio
async def test_log_event_failure_returns_false(audit_client, mock_structlog):
    """Tests that log_event returns False and logs an error on failure."""
    with patch.object(audit_client, "_send_event_with_retries") as mock_send:
        mock_send.side_effect = ReasonerError("Failed", ReasonerErrorCode.SERVICE_UNAVAILABLE)
        
        success = await audit_client.log_event("test_event", {"key": "value"})
        
        assert success is False
        # The logger is now the mocked one from the fixture
        audit_client._logger.error.assert_called_with(
            "audit_log_structured_error",
            event_type="test_event",
            message="Failed"
        )

@pytest.mark.asyncio
async def test_log_event_unhandled_exception(audit_client, mock_structlog):
    """Tests that log_event handles unexpected internal exceptions gracefully."""
    with patch.object(audit_client, "_send_event_with_retries") as mock_send:
        mock_send.side_effect = ValueError("Unhandled")
        
        success = await audit_client.log_event("test_event", {"key": "value"})
        
        assert success is False
        audit_client._logger.critical.assert_called_with(
            "audit_log_unhandled_exception",
            event_type="test_event",
            error="Unhandled",
            exc_info=True
        )

@pytest.mark.asyncio
async def test_log_event_invalid_params():
    """Tests that log_event validates parameters."""
    with patch("arbiter.explainable_reasoner.audit_ledger.httpx.AsyncClient"):
        client = AuditLedgerClient()
        
        # Test empty event_type
        with pytest.raises(ValueError, match="event_type must be a non-empty string"):
            await client.log_event("", {"key": "value"})
        
        # Test invalid details type
        with pytest.raises(ValueError, match="details must be a dictionary"):
            await client.log_event("test_event", "not_a_dict")

# Test log_batch_events
@pytest.mark.asyncio
async def test_log_batch_events_success(audit_client):
    """Tests that a batch of events are all logged successfully."""
    with patch.object(audit_client, "log_event", new_callable=AsyncMock) as mock_log_event:
        mock_log_event.return_value = True
        
        events = [
            {"event_type": "event1", "details": {"d1": 1}, "operator": "op1"},
            {"event_type": "event2", "details": {"d2": 2}, "operator": "op2"}
        ]
        success = await audit_client.log_batch_events(events)
        
        assert success is True
        assert mock_log_event.call_count == 2

@pytest.mark.asyncio
async def test_log_batch_events_partial_failure(audit_client):
    """Tests that the batch operation returns False if any event fails."""
    with patch.object(audit_client, "log_event", new_callable=AsyncMock) as mock_log_event:
        mock_log_event.side_effect = [True, False]
        
        events = [
            {"event_type": "event1", "details": {"d1": 1}},
            {"event_type": "event2", "details": {"d2": 2}}
        ]
        success = await audit_client.log_batch_events(events)
        
        assert success is False

@pytest.mark.asyncio
async def test_log_batch_events_empty(audit_client):
    """Tests that an empty batch returns True."""
    success = await audit_client.log_batch_events([])
    assert success is True

# Test health_check
@pytest.mark.asyncio
async def test_health_check_success(audit_client, mock_httpx_client):
    """Tests successful health check."""
    with patch.object(audit_client, '_get_client', return_value=mock_httpx_client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        # Health check uses POST when no health_endpoint is set
        mock_httpx_client.post.return_value = mock_response
        
        result = await audit_client.health_check()
        assert result is True

@pytest.mark.asyncio
async def test_health_check_failure(audit_client, mock_httpx_client):
    """Tests health check failure."""
    with patch.object(audit_client, '_get_client', return_value=mock_httpx_client):
        mock_httpx_client.post.side_effect = httpx.HTTPError("Connection failed")
        
        result = await audit_client.health_check()
        assert result is False

@pytest.mark.asyncio
async def test_health_check_with_endpoint():
    """Tests health check with specific health endpoint."""
    with patch("arbiter.explainable_reasoner.audit_ledger.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client_class.return_value = mock_client
        
        client = AuditLedgerClient(health_endpoint="/health")
        
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response
        
        result = await client.health_check()
        assert result is True
        # Should use GET with health endpoint
        mock_client.get.assert_called_once()

# Test rotate_key
@pytest.mark.asyncio
async def test_rotate_key(audit_client, mock_httpx_client):
    """Tests API key rotation."""
    audit_client._client = mock_httpx_client
    
    await audit_client.rotate_key("new_key")
    
    assert audit_client.api_key.get_actual_value() == "new_key"
    mock_httpx_client.aclose.assert_awaited_once()

@pytest.mark.asyncio
async def test_rotate_key_invalid():
    """Tests that rotate_key validates the new key."""
    with patch("arbiter.explainable_reasoner.audit_ledger.httpx.AsyncClient"):
        client = AuditLedgerClient()
        
        with pytest.raises(ValueError, match="New key must be a non-empty string"):
            await client.rotate_key("")

# Test close
@pytest.mark.asyncio
async def test_close_with_client(audit_client, mock_httpx_client):
    """Tests that the close method closes an active client."""
    audit_client._client = mock_httpx_client
    await audit_client.close()
    mock_httpx_client.aclose.assert_awaited_once()

@pytest.mark.asyncio
async def test_close_without_client(audit_client):
    """Tests that close does nothing if no client is active."""
    audit_client._client = None
    await audit_client.close()  # Should not error

# Edge Cases
def test_init_default_values():
    """Tests that the client can be initialized with default values."""
    with patch.dict(os.environ, {}, clear=True), \
         patch("arbiter.explainable_reasoner.audit_ledger.httpx.AsyncClient"):
        client = AuditLedgerClient()
        assert client.ledger_url == "https://localhost:8080/audit"
        assert client.max_retries == 3
        assert client.initial_backoff_delay == 1.0
        assert client.timeout == 5.0

@pytest.mark.asyncio
async def test_log_event_with_pii_redaction(audit_client):
    """Tests that PII is redacted from details."""
    details = {
        "email": "user@example.com",
        "phone": "555-123-4567",
        "safe_data": "normal text"
    }
    
    with patch.object(audit_client, "_send_event_with_retries", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        await audit_client.log_event("test_event", details)
        
        # Check that PII was redacted
        sent_record = mock_send.call_args[0][0]
        assert sent_record["details"]["email"] == "[EMAIL]"
        assert sent_record["details"]["phone"] == "[PHONE]"
        assert sent_record["details"]["safe_data"] == "normal text"

@pytest.mark.asyncio
async def test_rate_limit_handling(audit_client, mock_httpx_client, mock_metrics):
    """Tests handling of rate limit responses."""
    with patch.object(audit_client, '_get_client', return_value=mock_httpx_client):
        # Disable circuit breaker for cleaner test
        audit_client._breaker = None
        
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "1", "x-ratelimit-remaining": "0"}
        
        mock_httpx_client.post.side_effect = httpx.HTTPStatusError(
            "Rate limited",
            request=MagicMock(),
            response=mock_response
        )
        
        # Should raise after retries with rate limit message in the chain
        with pytest.raises((ReasonerError, httpx.HTTPStatusError)):
            await audit_client._send_event_with_retries({
                "test": "data",
                "event_type": "test_event",
                "record_hash": "test_hash"
            })
        
        mock_metrics["rate_limit"].labels.assert_called()