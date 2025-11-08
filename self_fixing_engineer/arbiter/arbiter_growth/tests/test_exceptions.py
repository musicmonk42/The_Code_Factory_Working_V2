import json
import logging
import pytest

# Assuming all modules are in a discoverable path
from arbiter.arbiter_growth.exceptions import (
    ArbiterGrowthError,
    OperationQueueFullError,
    RateLimitError,
    CircuitBreakerOpenError,
    AuditChainTamperedError,
)

# Define a logger for use in tests that check logging behavior
logger = logging.getLogger(__name__)

# Fixture for capturing logs
@pytest.fixture
def caplog(caplog):
    """A fixture to capture log output during tests."""
    caplog.set_level(logging.ERROR)
    yield caplog
    caplog.clear()

# --- Test Cases ---

def test_arbiter_growth_error_init_no_details(caplog):
    """Tests ArbiterGrowthError initialization without details."""
    error = ArbiterGrowthError("Base error occurred")
    assert error.message == "Base error occurred"
    assert error.details == {}
    assert str(error) == "Base error occurred"
    assert "Exception raised: ArbiterGrowthError, Details: {}" in caplog.text

def test_arbiter_growth_error_init_with_details(caplog):
    """Tests ArbiterGrowthError initialization with details."""
    details = {"context": "test", "value": 42}
    error = ArbiterGrowthError("Error with context", details)
    assert error.message == "Error with context"
    assert error.details == details
    assert str(error) == "Error with context (Details: {'context': 'test', 'value': 42})"
    assert "Exception raised: ArbiterGrowthError, Details: {'context': 'test', 'value': 42}" in caplog.text

def test_arbiter_growth_error_subclassing():
    """Tests that ArbiterGrowthError is a proper base class for other exceptions."""
    error = OperationQueueFullError("Queue full")
    assert isinstance(error, ArbiterGrowthError)
    assert isinstance(error, Exception)

def test_operation_queue_full_error_init(caplog):
    """Tests OperationQueueFullError initialization."""
    details = {"queue_size": 100, "max_size": 100}
    error = OperationQueueFullError("Queue at capacity", details)
    assert error.message == "Queue at capacity"
    assert error.details == details
    assert str(error) == "Queue at capacity (Details: {'queue_size': 100, 'max_size': 100})"
    assert "Exception raised: OperationQueueFullError" in caplog.text

def test_rate_limit_error_init(caplog):
    """Tests RateLimitError initialization."""
    details = {"limit": 10, "current_rate": 12}
    error = RateLimitError("Rate limit exceeded", details)
    assert error.message == "Rate limit exceeded"
    assert error.details == details
    assert str(error) == "Rate limit exceeded (Details: {'limit': 10, 'current_rate': 12})"
    assert "Exception raised: RateLimitError" in caplog.text

def test_circuit_breaker_open_error_init(caplog):
    """Tests CircuitBreakerOpenError initialization."""
    details = {"breaker_name": "snapshot", "reset_timeout": 60}
    error = CircuitBreakerOpenError("Circuit breaker open", details)
    assert error.message == "Circuit breaker open"
    assert error.details == details
    assert str(error) == "Circuit breaker open (Details: {'breaker_name': 'snapshot', 'reset_timeout': 60})"
    assert "Exception raised: CircuitBreakerOpenError" in caplog.text

def test_audit_chain_tampered_error_init(caplog):
    """Tests AuditChainTamperedError initialization."""
    details = {"expected_hash": "abc123", "actual_hash": "xyz789"}
    error = AuditChainTamperedError("Audit chain validation failed", details)
    assert error.message == "Audit chain validation failed"
    assert error.details == details
    assert str(error) == "Audit chain validation failed (Details: {'expected_hash': 'abc123', 'actual_hash': 'xyz789'})"
    assert "Exception raised: AuditChainTamperedError" in caplog.text

def test_arbiter_growth_error_none_details(caplog):
    """Tests ArbiterGrowthError initialization with None for details."""
    error = ArbiterGrowthError("Test error", None)
    assert error.details == {}
    assert str(error) == "Test error"
    assert "Exception raised: ArbiterGrowthError, Details: {}" in caplog.text

def test_arbiter_growth_error_serialization():
    """Tests that the string representation of an ArbiterGrowthError is JSON serializable."""
    error = ArbiterGrowthError("Serializable error", {"key": "value"})
    # The str(error) will produce a string like: "Serializable error (Details: {'key': 'value'})"
    # json.dumps will correctly handle this string, including the single quotes.
    serialized = json.dumps({"error": str(error)})
    expected_fragment = '"error": "Serializable error (Details: {\'key\': \'value\'})"'
    assert expected_fragment in serialized

def test_exception_hierarchy():
    """Tests that all specific exceptions inherit from ArbiterGrowthError."""
    errors = [
        OperationQueueFullError("Queue full"),
        RateLimitError("Rate limit"),
        CircuitBreakerOpenError("Breaker open"),
        AuditChainTamperedError("Chain tampered"),
    ]
    for error in errors:
        assert isinstance(error, ArbiterGrowthError)
        assert isinstance(error, Exception)

def test_logging_stack_trace(caplog):
    """Tests that a traceback is included in the log when exc_info=True is used."""
    try:
        raise ArbiterGrowthError("Test with stack", {"context": "stack_test"})
    except ArbiterGrowthError:
        # The logger in the exception class itself will log the creation.
        # This logger.error call demonstrates how a consumer would log the caught exception with a stack trace.
        logger.error("Caught error with stack trace", exc_info=True)
    
    assert "Traceback" in caplog.text
    assert "ArbiterGrowthError: Test with stack" in caplog.text

def test_catch_arbiter_growth_error():
    """Tests catching a specific exception via its base class."""
    try:
        raise RateLimitError("Rate limit hit", {"limit": 10})
    except ArbiterGrowthError as e:
        assert e.message == "Rate limit hit"
        assert e.details == {"limit": 10}
        assert isinstance(e, RateLimitError)

# --- Reconstructed and New Tests ---

def test_arbiter_growth_error_complex_details(caplog):
    """Tests ArbiterGrowthError initialization with complex, nested details."""
    details = {"nested": {"key": "value", "list_of_ints": [1, 2, 3]}}
    error = ArbiterGrowthError("Complex error", details)
    assert error.message == "Complex error"
    assert error.details == details
    assert "Complex error" in str(error)
    assert "nested" in str(error)
    assert "Exception raised: ArbiterGrowthError, Details: {'nested': {'key': 'value', 'list_of_ints': [1, 2, 3]}}" in caplog.text


def test_rate_limit_error_metrics(caplog):
    """Tests that RateLimitError details can include metrics data."""
    details = {"limit": 10, "current_rate": 12, "exceeds_by": 2}
    error = RateLimitError("Rate limit", details)
    assert error.message == "Rate limit"
    assert error.details == details
    assert "limit" in str(error)
    assert "Rate limit" in str(error)
    assert "exceeds_by" in str(error)
    assert "Exception raised: RateLimitError, Details: {'limit': 10, 'current_rate': 12, 'exceeds_by': 2}" in caplog.text