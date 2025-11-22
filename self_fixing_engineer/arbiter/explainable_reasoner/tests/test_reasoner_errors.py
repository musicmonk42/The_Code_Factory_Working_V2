# test_reasoner_errors.py
# Comprehensive production-grade tests for reasoner_errors.py
# Requires: pytest, unittest.mock, structlog, sentry-sdk (optional for real; mock otherwise)
# Run with: pytest test_reasoner_errors.py -v --cov=reasoner_errors --cov-report=html

import os
import json
from unittest.mock import patch, MagicMock

import pytest

# Import the module under test
from arbiter.explainable_reasoner.reasoner_errors import (
    ReasonerErrorCode,
    ReasonerError
)

# --- Fixtures ---
@pytest.fixture(autouse=True)
def mock_logger():
    """Mock logger to capture calls."""
    with patch("arbiter.explainable_reasoner.reasoner_errors.logger") as mock_log:
        mock_log.error = MagicMock()
        mock_log.critical = MagicMock()
        mock_log.warning = MagicMock()
        mock_log.info = MagicMock()
        yield mock_log

@pytest.fixture
def mock_sentry():
    """Mock sentry_sdk for capture_exception."""
    with patch("arbiter.explainable_reasoner.reasoner_errors.SENTRY_AVAILABLE", True), \
         patch("arbiter.explainable_reasoner.reasoner_errors.sentry_sdk") as mock_sentry_sdk:
        mock_scope = MagicMock()
        mock_scope.set_tag = MagicMock()
        mock_scope.set_extra = MagicMock()
        mock_sentry_sdk.push_scope.return_value.__enter__.return_value = mock_scope
        mock_sentry_sdk.push_scope.return_value.__exit__.return_value = None
        yield mock_sentry_sdk

# --- Test Cases ---

# Test ReasonerErrorCode
def test_reasoner_error_code_attributes():
    """Test that error codes are defined correctly."""
    # Test that some expected codes exist
    assert hasattr(ReasonerErrorCode, "UNEXPECTED_ERROR")
    assert hasattr(ReasonerErrorCode, "INVALID_INPUT")
    assert hasattr(ReasonerErrorCode, "GENERIC_ERROR")
    
    # Dynamically discover all error codes
    error_codes = [attr for attr in dir(ReasonerErrorCode) 
                   if not attr.startswith('_') and attr.isupper()]
    
    # Verify each code
    for code_name in error_codes:
        code_value = getattr(ReasonerErrorCode, code_name)
        assert isinstance(code_value, str)
        assert code_value == code_name  # Value should match attribute name

def test_error_code_values():
    """Test error code format."""
    # Get all actual error codes
    error_codes = [getattr(ReasonerErrorCode, attr) 
                   for attr in dir(ReasonerErrorCode) 
                   if not attr.startswith('_') and attr.isupper()]
    
    for code in error_codes:
        assert isinstance(code, str)
        assert code.isupper()
        # Most codes have underscores except TIMEOUT
        if code != "TIMEOUT":
            assert "_" in code or len(code.split("_")) == 1

# Test ReasonerError Initialization and Logging
@pytest.mark.parametrize(
    "message, code, original_exception, extra_kwargs",
    [
        ("Test error", ReasonerErrorCode.UNEXPECTED_ERROR, None, {}),
        ("Input fail", ReasonerErrorCode.INVALID_INPUT, ValueError("Inner error"), {"user_id": "123"}),
        ("Timeout occurred", ReasonerErrorCode.TIMEOUT, None, {"request_id": "abc"}),
        ("Generic error", ReasonerErrorCode.GENERIC_ERROR, None, {"resource": "test"}),
    ]
)
def test_reasoner_error_init_and_log(mock_logger, message, code, original_exception, extra_kwargs):
    """Test error initialization and logging."""
    error = ReasonerError(message, code, original_exception, **extra_kwargs)
    
    assert error.message == message
    assert error.code == code
    assert error.original_exception == original_exception
    assert str(error) == f"{message} (Code: {code})"
    
    mock_logger.error.assert_called_once_with(
        "reasoner_error_occurred",
        code=code,
        message=message,
        exc_info=True,
        **extra_kwargs
    )

# Test Sentry Integration
def test_reasoner_error_sentry_capture(mock_sentry, mock_logger):
    """Test Sentry integration when enabled."""
    with patch.dict(os.environ, {"REASONER_SENTRY_DSN": "https://test.dsn/1"}):
        original_exc = ValueError("Inner")
        error = ReasonerError(
            "Sentry test",
            ReasonerErrorCode.UNEXPECTED_ERROR,
            original_exc,
            user_id="456",
            request_id="xyz"
        )
        
        # Check that exception was captured
        mock_sentry.capture_exception.assert_called_once_with(error)
        
        # Check that the scope was configured with all context
        scope = mock_sentry.push_scope.return_value.__enter__.return_value
        scope.set_tag.assert_called_with("reasoner_error_code", ReasonerErrorCode.UNEXPECTED_ERROR)
        scope.set_extra.assert_any_call("original_exception", "Inner")
        scope.set_extra.assert_any_call("user_id", "456")
        scope.set_extra.assert_any_call("request_id", "xyz")

def test_sentry_not_captured_no_dsn(mock_logger):
    """Test that Sentry is not used when DSN is not configured."""
    with patch("arbiter.explainable_reasoner.reasoner_errors.SENTRY_AVAILABLE", True), \
         patch("arbiter.explainable_reasoner.reasoner_errors.sentry_sdk") as mock_sentry, \
         patch.dict(os.environ, {}, clear=True):
        
        ReasonerError("No sentry", ReasonerErrorCode.GENERIC_ERROR)
        mock_sentry.capture_exception.assert_not_called()

def test_sentry_not_available(mock_logger):
    """Test behavior when Sentry is not installed."""
    with patch("arbiter.explainable_reasoner.reasoner_errors.SENTRY_AVAILABLE", False), \
         patch("arbiter.explainable_reasoner.reasoner_errors.sentry_sdk") as mock_sentry:
        
        ReasonerError("No sentry", ReasonerErrorCode.GENERIC_ERROR)
        mock_sentry.capture_exception.assert_not_called()

# Test to_api_response
def test_to_api_response_without_traceback():
    """Test API response generation without traceback."""
    original_exc = ValueError("Inner error")
    error = ReasonerError("API test", ReasonerErrorCode.INVALID_INPUT, original_exc)
    response = error.to_api_response(include_traceback=False)
    
    expected = {
        "error": {
            "code": "INVALID_INPUT",
            "message": "API test",
            "details": {"original_exception": "Inner error"}
        }
    }
    assert response == expected

def test_to_api_response_with_traceback():
    """Test API response generation with traceback."""
    try:
        1 / 0
    except ZeroDivisionError as e:
        error = ReasonerError("API test", ReasonerErrorCode.UNEXPECTED_ERROR, e)
        response = error.to_api_response(include_traceback=True)

    assert response["error"]["code"] == "UNEXPECTED_ERROR"
    assert "details" in response["error"]
    assert "original_exception" in response["error"]["details"]
    assert "traceback" in response["error"]["details"]
    assert "Traceback (most recent call last)" in response["error"]["details"]["traceback"]
    assert "ZeroDivisionError: division by zero" in response["error"]["details"]["traceback"]

def test_to_api_response_no_original_exc():
    """Test API response when there's no original exception."""
    error = ReasonerError("No original", ReasonerErrorCode.GENERIC_ERROR)
    response = error.to_api_response(include_traceback=True)
    
    # "details" key should not be present if there's no original exception
    assert "details" not in response["error"]

# Test to_json
def test_to_json():
    """Test JSON serialization of error."""
    error = ReasonerError("JSON test", ReasonerErrorCode.TIMEOUT)
    
    # Test with indentation
    json_str_indent = error.to_json(indent=2)
    data = json.loads(json_str_indent)
    assert data == {"error": {"code": "TIMEOUT", "message": "JSON test"}}
    assert "\n" in json_str_indent
    
    # Test without indentation
    json_str_no_indent = error.to_json()
    assert "\n" not in json_str_no_indent
    data_no_indent = json.loads(json_str_no_indent)
    assert data_no_indent == {"error": {"code": "TIMEOUT", "message": "JSON test"}}

# Test error wrapping and representation
def test_error_wrapping_and_representation():
    """Tests catching a base exception, wrapping it, and checking representations."""
    try:
        1 / 0
    except ZeroDivisionError as e:
        wrapped_error = ReasonerError(
            "A mathematical operation failed.",
            code=ReasonerErrorCode.UNEXPECTED_ERROR,
            original_exception=e,
            user_id="test_user_123"
        )

    # Check basic properties
    assert wrapped_error.code == ReasonerErrorCode.UNEXPECTED_ERROR
    assert isinstance(wrapped_error.original_exception, ZeroDivisionError)
    
    # Check string representation
    assert "A mathematical operation failed." in str(wrapped_error)
    assert "UNEXPECTED_ERROR" in str(wrapped_error)

    # Check API response
    api_response = wrapped_error.to_api_response(include_traceback=True)
    details = api_response["error"]["details"]
    assert "division by zero" in details["original_exception"]
    assert "Traceback (most recent call last)" in details["traceback"]

# Test error inheritance
def test_error_inheritance():
    """Test that ReasonerError properly inherits from Exception."""
    error = ReasonerError("Test", ReasonerErrorCode.GENERIC_ERROR)
    assert isinstance(error, Exception)
    assert isinstance(error, ReasonerError)

# Test with extra kwargs
def test_error_with_extra_kwargs():
    """Test that extra kwargs are properly stored and logged."""
    extra_data = {
        "user_id": "123",
        "session_id": "abc",
        "resource": "database",
        "action": "read"
    }
    
    error = ReasonerError(
        "Access denied",
        ReasonerErrorCode.SECURITY_VIOLATION,  # Use an existing error code
        **extra_data
    )
    
    # Check that extra kwargs are stored
    for key, value in extra_data.items():
        assert key in error.extra_kwargs
        assert error.extra_kwargs[key] == value

# Edge Cases
def test_reasoner_error_no_message_or_code():
    """Test that error requires message and code."""
    with pytest.raises(TypeError, match="missing 2 required positional arguments: 'message' and 'code'"):
        ReasonerError()

def test_reasoner_error_empty_message():
    """Test error with empty message."""
    error = ReasonerError("", ReasonerErrorCode.GENERIC_ERROR)
    assert error.message == ""
    assert str(error) == f" (Code: {ReasonerErrorCode.GENERIC_ERROR})"

def test_reasoner_error_none_values():
    """Test error with None values in kwargs."""
    error = ReasonerError(
        "Test",
        ReasonerErrorCode.GENERIC_ERROR,
        user_id=None,
        session_id=None
    )
    assert error.extra_kwargs["user_id"] is None
    assert error.extra_kwargs["session_id"] is None

# Test all known error codes exist
def test_known_error_codes_exist():
    """Test that commonly used error codes exist."""
    known_codes = [
        "GENERIC_ERROR",
        "UNEXPECTED_ERROR",
        "INVALID_INPUT",
        "MODEL_LOAD_FAILED",
        "MODEL_INFERENCE_FAILED",
        "HISTORY_ERROR",
        "TIMEOUT",
        "SECURITY_VIOLATION",
        "AUDIT_LOG_FAILED",
        "SENSITIVE_DATA_LEAK"
    ]
    
    for code_name in known_codes:
        assert hasattr(ReasonerErrorCode, code_name), f"Missing expected error code: {code_name}"