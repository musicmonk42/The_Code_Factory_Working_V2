# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""Order service functions."""

from typing import Any, Optional

# In-memory store used as a stub; replace with a real database in production.
_ORDERS: list[dict] = []
_NEXT_ID: int = 1


def get_all_orders() -> list[dict]:
    """Return all orders from the store."""
    return list(_ORDERS)


def get_order(order_id: int) -> Optional[dict]:
    """Return a single order by ID, or ``None`` if not found."""
    return next((o for o in _ORDERS if o["id"] == order_id), None)


def create_order(items: list[dict]) -> dict:
    """Place a new order and return it."""
    global _NEXT_ID
    order = {"id": _NEXT_ID, "status": "pending", "items": items}
    _ORDERS.append(order)
    _NEXT_ID += 1
    return order
