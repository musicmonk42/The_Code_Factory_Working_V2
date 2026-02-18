# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
"""Additional test file for comprehensive coverage."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_endpoint_health():
    """Test health endpoint is accessible."""
    response = client.get("/health")
    assert response.status_code == 200


def test_endpoint_version():
    """Test version endpoint is accessible."""
    response = client.get("/version")
    assert response.status_code == 200


def test_endpoint_echo():
    """Test echo endpoint is accessible."""
    response = client.post("/echo", json={"message": "test"})
    assert response.status_code == 200
