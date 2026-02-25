# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""Product service functions."""

from typing import Any, Optional

# In-memory store used as a stub; replace with a real database in production.
_PRODUCTS: list[dict] = [
    {"id": 1, "name": "Widget A", "price": 9.99, "description": "A basic widget"},
    {"id": 2, "name": "Widget B", "price": 19.99, "description": "A premium widget"},
]
_NEXT_ID: int = 3


def get_all_products() -> list[dict]:
    """Return all products from the store."""
    return list(_PRODUCTS)


def get_product(product_id: int) -> Optional[dict]:
    """Return a single product by ID, or ``None`` if not found."""
    return next((p for p in _PRODUCTS if p["id"] == product_id), None)


def create_product(name: str, price: float, description: str = "") -> dict:
    """Add a new product to the store and return it."""
    global _NEXT_ID
    product = {"id": _NEXT_ID, "name": name, "price": price, "description": description}
    _PRODUCTS.append(product)
    _NEXT_ID += 1
    return product


def delete_product(product_id: int) -> bool:
    """Remove a product from the store.  Returns ``True`` if it existed."""
    global _PRODUCTS
    before = len(_PRODUCTS)
    _PRODUCTS = [p for p in _PRODUCTS if p["id"] != product_id]
    return len(_PRODUCTS) < before
