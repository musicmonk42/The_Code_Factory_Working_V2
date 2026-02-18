# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
"""Pytest configuration and fixtures."""

import sys
from pathlib import Path

# Add the app directory to Python path
app_dir = Path(__file__).parent.parent
sys.path.insert(0, str(app_dir))

import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    """Create a test client for the FastAPI application."""
    return TestClient(app)
