"""Tests for FastAPI endpoints."""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_check():
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_echo_valid_message():
    """Test echo endpoint with valid message."""
    response = client.post("/echo", json={"message": "Hello World"})
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Hello World"
    assert data["length"] == 11


def test_echo_trim_whitespace():
    """Test that whitespace is trimmed."""
    response = client.post("/echo", json={"message": "  Hello World  "})
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Hello World"


def test_echo_empty_message():
    """Test that empty message returns 422 (Pydantic validation error)."""
    response = client.post("/echo", json={"message": ""})
    assert response.status_code == 422  # Pydantic validation error
    error_detail = str(response.json()).lower()
    # Pydantic V2: Check for "message cannot be empty" from custom validator
    assert "message cannot be empty" in error_detail or "value error" in error_detail


def test_echo_whitespace_only():
    """Test that whitespace-only message returns 422."""
    response = client.post("/echo", json={"message": "   "})
    assert response.status_code == 422  # Pydantic validation error
    error_detail = str(response.json()).lower()
    assert "message cannot be empty" in error_detail or "value error" in error_detail


def test_echo_max_length():
    """Test that messages over 500 characters return 422."""
    over_length_message = "x" * 501
    response = client.post("/echo", json={"message": over_length_message})
    assert response.status_code == 422  # Pydantic validation error
    error_detail = str(response.json()).lower()
    # Pydantic V2: "String should have at most 500 characters" - no "value error" prefix
    assert "at most 500 characters" in error_detail or "string_too_long" in error_detail


def test_echo_boundary_min():
    """Test minimum valid message length (1 character)."""
    response = client.post("/echo", json={"message": "x"})
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "x"
    assert data["length"] == 1


def test_echo_boundary_max():
    """Test maximum valid message length (500 characters)."""
    max_message = "x" * 500
    response = client.post("/echo", json={"message": max_message})
    assert response.status_code == 200
    data = response.json()
    assert len(data["message"]) == 500


def test_echo_missing_field():
    """Test that missing message field returns 422."""
    response = client.post("/echo", json={})
    assert response.status_code == 422
    error_detail = str(response.json()).lower()
    # Pydantic V2 uses "Field required" (capital F)
    assert "required" in error_detail or "missing" in error_detail


def test_echo_invalid_type():
    """Test that non-string message returns 422."""
    response = client.post("/echo", json={"message": 123})
    assert response.status_code == 422
    error_detail = str(response.json()).lower()
    assert "string" in error_detail or "str" in error_detail


def test_echo_unicode():
    """Test that unicode characters are handled."""
    response = client.post("/echo", json={"message": "Hello 世界 🌍"})
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Hello 世界 🌍"


def test_echo_special_characters():
    """Test that special characters are allowed."""
    response = client.post("/echo", json={"message": "Hello! @#$%^&*()"})
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Hello! @#$%^&*()"
