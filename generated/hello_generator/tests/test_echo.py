# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
"""Tests for echo endpoint."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_echo_message_success():
    """Test successful echo of a valid message."""
    response = client.post("/echo", json={"message": "Hello, World!"})
    assert response.status_code == 200
    assert response.json()["message"] == "Hello, World!"


def test_echo_message_with_whitespace():
    """Test echo strips leading/trailing whitespace."""
    response = client.post("/echo", json={"message": "  Hello  "})
    assert response.status_code == 200
    assert response.json()["message"] == "Hello"


def test_echo_message_empty():
    """Test echo with empty message after trimming."""
    response = client.post("/echo", json={"message": "   "})
    assert response.status_code == 422
    # Use case-insensitive 'in' check to match Pydantic V2 error format
    # Pydantic V2 prefixes ValueError with "Value error, "
    assert "message cannot be empty after trimming whitespace" in response.json()["detail"][0]["msg"].lower()


def test_echo_message_too_short():
    """Test echo with message that's too short after validation."""
    response = client.post("/echo", json={"message": ""})
    assert response.status_code == 422
    # Pydantic V2 validates min_length first, but our validator catches empty strings
    error_msg = response.json()["detail"][0]["msg"].lower()
    # Accept either Pydantic's min_length error or our custom error
    assert ("at least 1 character" in error_msg or 
            "message cannot be empty" in error_msg)


def test_echo_message_too_long():
    """Test echo with message exceeding max length."""
    long_message = "a" * 501
    response = client.post("/echo", json={"message": long_message})
    assert response.status_code == 422
    # Use case-insensitive 'in' check for Pydantic V2 compatibility
    # Pydantic V2 uses "String should have at most N characters"
    assert "at most 500 characters" in response.json()["detail"][0]["msg"].lower()


def test_echo_message_missing():
    """Test echo with missing message field."""
    response = client.post("/echo", json={})
    assert response.status_code == 422
    assert "field required" in response.json()["detail"][0]["msg"].lower() or \
           "missing" in response.json()["detail"][0]["msg"].lower()
