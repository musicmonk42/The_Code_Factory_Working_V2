"""Tests for app/routes.py – full CRUD coverage."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import routes as _routes_module  # used to reset store between tests

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_store():
    """Wipe the in-memory item store before every test."""
    _routes_module._items.clear()
    _routes_module._next_id = 1
    yield


# ---------------------------------------------------------------------------
# POST /items – create
# ---------------------------------------------------------------------------

def test_create_item_valid():
    response = client.post("/items", json={"name": "Test Item", "price": 19.99, "quantity": 10})
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == 1
    assert data["name"] == "Test Item"
    assert data["price"] == 19.99
    assert data["quantity"] == 10


def test_create_item_long_name():
    response = client.post("/items", json={"name": "x" * 501, "price": 19.99, "quantity": 10})
    assert response.status_code == 422


def test_item_model_name_too_short():
    response = client.post("/items", json={"name": "", "price": 19.99, "quantity": 10})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list) and len(detail) > 0
    assert any(err.get("type") == "value_error" for err in detail)


def test_create_item_negative_price():
    response = client.post("/items", json={"name": "Test Item", "price": -5.0, "quantity": 10})
    assert response.status_code == 422


def test_create_item_zero_price():
    response = client.post("/items", json={"name": "Test Item", "price": 0.0, "quantity": 10})
    assert response.status_code == 422


def test_create_item_negative_quantity():
    response = client.post("/items", json={"name": "Test Item", "price": 1.0, "quantity": -1})
    assert response.status_code == 422


def test_create_item_assigns_incrementing_ids():
    r1 = client.post("/items", json={"name": "A", "price": 1.0, "quantity": 1})
    r2 = client.post("/items", json={"name": "B", "price": 2.0, "quantity": 2})
    assert r1.json()["id"] == 1
    assert r2.json()["id"] == 2


# ---------------------------------------------------------------------------
# GET /items – list
# ---------------------------------------------------------------------------

def test_list_items_empty():
    response = client.get("/items")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_items_after_create():
    client.post("/items", json={"name": "Widget", "price": 9.99, "quantity": 5})
    response = client.get("/items")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Widget"


# ---------------------------------------------------------------------------
# GET /items/{item_id} – retrieve
# ---------------------------------------------------------------------------

def test_get_item_found():
    client.post("/items", json={"name": "Gadget", "price": 29.99, "quantity": 3})
    response = client.get("/items/1")
    assert response.status_code == 200
    assert response.json()["name"] == "Gadget"


def test_get_item_not_found():
    response = client.get("/items/999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PUT /items/{item_id} – update
# ---------------------------------------------------------------------------

def test_update_item():
    client.post("/items", json={"name": "Old", "price": 1.0, "quantity": 1})
    response = client.put("/items/1", json={"name": "New", "price": 2.0, "quantity": 2})
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "New"
    assert data["price"] == 2.0
    assert data["id"] == 1


def test_update_item_not_found():
    response = client.put("/items/999", json={"name": "X", "price": 1.0, "quantity": 1})
    assert response.status_code == 404


def test_update_item_validation_error():
    client.post("/items", json={"name": "Item", "price": 1.0, "quantity": 1})
    response = client.put("/items/1", json={"name": "", "price": 1.0, "quantity": 1})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /items/{item_id} – remove
# ---------------------------------------------------------------------------

def test_delete_item():
    client.post("/items", json={"name": "Doomed", "price": 1.0, "quantity": 1})
    response = client.delete("/items/1")
    assert response.status_code == 204
    # Confirm it's gone
    assert client.get("/items/1").status_code == 404


def test_delete_item_not_found():
    response = client.delete("/items/999")
    assert response.status_code == 404
