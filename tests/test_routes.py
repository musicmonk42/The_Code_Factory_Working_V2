from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_create_item_valid():
    response = client.post("/items", json={"name": "Test Item", "price": 19.99, "quantity": 10})
    assert response.status_code == 200


def test_create_item_long_name():
    response = client.post("/items", json={"name": "x" * 501, "price": 19.99, "quantity": 10})
    assert response.status_code == 422
