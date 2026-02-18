"""Tests for Pydantic schemas."""
import pytest
from pydantic import ValidationError
from app.schemas import EchoRequest


def test_echo_request_model_valid():
    """Test that valid EchoRequest is accepted."""
    request = EchoRequest(message="Hello World")
    assert request.message == "Hello World"


def test_echo_request_model_trim():
    """Test that whitespace is trimmed."""
    request = EchoRequest(message="  Hello World  ")
    assert request.message == "Hello World"


def test_echo_request_model_empty_string():
    """Test that empty string is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        EchoRequest(message="")
    error_str = str(exc_info.value).lower()
    assert "message cannot be empty" in error_str or "value error" in error_str


def test_echo_request_model_whitespace_only():
    """Test that whitespace-only string is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        EchoRequest(message="   ")
    error_str = str(exc_info.value).lower()
    assert "message cannot be empty" in error_str or "value error" in error_str


# THESE ARE THE TWO FAILING TESTS
def test_echo_request_model_min_length():
    """Test that empty message is rejected (min_length constraint)."""
    with pytest.raises(ValidationError) as exc_info:
        EchoRequest(message="")
    error_str = str(exc_info.value).lower()
    assert "message cannot be empty" in error_str or "at least 1 character" in error_str


def test_echo_request_model_max_length():
    """Test that messages over 500 characters are rejected."""
    over_length_message = "x" * 501
    with pytest.raises(ValidationError) as exc_info:
        EchoRequest(message=over_length_message)
    error_str = str(exc_info.value).lower()
    assert "at most 500 characters" in error_str or "string_too_long" in error_str


def test_echo_request_model_boundary_min():
    """Test minimum valid length (1 character)."""
    request = EchoRequest(message="x")
    assert request.message == "x"


def test_echo_request_model_boundary_max():
    """Test maximum valid length (500 characters)."""
    max_length_message = "x" * 500
    request = EchoRequest(message=max_length_message)
    assert len(request.message) == 500


def test_echo_request_model_not_string():
    """Test that non-string values are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        EchoRequest(message=123)
    error_str = str(exc_info.value).lower()
    assert "string" in error_str or "str" in error_str


def test_echo_request_model_none():
    """Test that None is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        EchoRequest(message=None)
    error_str = str(exc_info.value).lower()
    assert "none" in error_str or "null" in error_str or "string" in error_str


def test_echo_request_model_missing_field():
    """Test that missing message field is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        EchoRequest()
    error_str = str(exc_info.value).lower()
    assert "required" in error_str or "missing" in error_str


def test_echo_request_model_valid_unicode():
    """Test that unicode characters are handled."""
    request = EchoRequest(message="Hello 世界 🌍")
    assert request.message == "Hello 世界 🌍"


def test_echo_request_model_trim_preserves_internal_spaces():
    """Test that internal spaces are preserved after trimming."""
    request = EchoRequest(message="  Hello   World  ")
    assert request.message == "Hello   World"


def test_echo_request_model_special_characters():
    """Test that special characters are allowed."""
    request = EchoRequest(message="Hello! @#$%^&*()")
    assert request.message == "Hello! @#$%^&*()"


def test_echo_request_model_boundary_with_trim():
    """Test that trimming doesn't affect boundary validation."""
    # 500 chars + 2 spaces should trim to exactly 500 chars
    message_with_spaces = " " + ("x" * 500) + " "
    request = EchoRequest(message=message_with_spaces)
    assert len(request.message) == 500
    assert request.message == "x" * 500

