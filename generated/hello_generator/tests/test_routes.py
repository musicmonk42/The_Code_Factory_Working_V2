# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
"""Tests for basic route functionality."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_check():
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version_endpoint():
    """Test version endpoint."""
    response = client.get("/version")
    assert response.status_code == 200
    assert "version" in response.json()
    assert response.json()["version"] == "1.0.0"
