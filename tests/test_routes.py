from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_create_item_valid():
    response = client.post("/items", json={"name": "Test Item", "price": 19.99, "quantity": 10})
    assert response.status_code == 200


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
