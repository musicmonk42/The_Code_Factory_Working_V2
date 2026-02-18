# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
"""Tests for items endpoint."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_create_item_success():
    """Test successful item creation."""
    response = client.post(
        "/items",
        json={
            "name": "Widget",
            "description": "A useful widget",
            "price": 19.99
        }
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Widget"
    assert data["description"] == "A useful widget"
    assert data["price"] == 19.99
    assert "id" in data
    assert isinstance(data["id"], int)


def test_create_item_no_description():
    """Test creating item without description (optional field)."""
    response = client.post(
        "/items",
        json={
            "name": "Simple Item",
            "price": 9.99
        }
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Simple Item"
    assert data["description"] is None
    assert data["price"] == 9.99


def test_create_item_missing_name():
    """Test creating item without required name field."""
    response = client.post(
        "/items",
        json={
            "description": "Missing name",
            "price": 10.00
        }
    )
    assert response.status_code == 422


def test_create_item_missing_price():
    """Test creating item without required price field."""
    response = client.post(
        "/items",
        json={
            "name": "No Price Item",
            "description": "Missing price"
        }
    )
    assert response.status_code == 422


def test_create_item_invalid_price():
    """Test creating item with negative price."""
    response = client.post(
        "/items",
        json={
            "name": "Invalid Price",
            "price": -5.00
        }
    )
    assert response.status_code == 422


def test_create_item_zero_price():
    """Test creating item with zero price."""
    response = client.post(
        "/items",
        json={
            "name": "Zero Price",
            "price": 0.0
        }
    )
    assert response.status_code == 422
