"""
Items router
============

Provides full CRUD operations for the Item resource.

Endpoints:
----------
- GET  /items           – list all items
- GET  /items/{item_id} – retrieve a single item
- POST /items           – create a new item
- PUT  /items/{item_id} – replace an existing item
- DELETE /items/{item_id} – remove an item
"""

import logging
from typing import Dict, List

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/items", tags=["items"])

# ---------------------------------------------------------------------------
# In-memory store (keyed by integer id)
# ---------------------------------------------------------------------------
_items: Dict[int, dict] = {}
_next_id: int = 1


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ItemBase(BaseModel):
    """Fields shared between request and response bodies."""

    name: str = Field(..., max_length=500, description="Human-readable item name")
    price: float = Field(..., gt=0, description="Unit price (must be positive)")
    quantity: int = Field(..., ge=0, description="Stock quantity (0 or more)")

    @field_validator("name", mode="before")
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be empty")
        return v


class ItemCreate(ItemBase):
    """Request body for creating a new item."""


class ItemUpdate(ItemBase):
    """Request body for replacing an existing item."""


class ItemResponse(ItemBase):
    """Response body that includes the server-assigned id."""

    id: int = Field(..., description="Unique item identifier")


class ItemListResponse(BaseModel):
    """Paginated list of items."""

    items: List[ItemResponse]
    total: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_item_or_404(item_id: int) -> dict:
    item = _items.get(item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item {item_id} not found",
        )
    return item


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=ItemListResponse, summary="List all items")
def list_items() -> ItemListResponse:
    """Return every item in the store."""
    records = list(_items.values())
    logger.debug("list_items: returning %d items", len(records))
    return ItemListResponse(items=records, total=len(records))


@router.get("/{item_id}", response_model=ItemResponse, summary="Get a single item")
def get_item(item_id: int) -> ItemResponse:
    """Retrieve one item by its id. Raises **404** if not found."""
    item = _get_item_or_404(item_id)
    logger.debug("get_item: id=%d", item_id)
    return ItemResponse(**item)


@router.post("", response_model=ItemResponse, status_code=status.HTTP_201_CREATED, summary="Create an item")
def create_item(item: ItemCreate) -> ItemResponse:
    """Persist a new item and return it with its assigned id."""
    global _next_id
    record = {"id": _next_id, **item.model_dump()}
    _items[_next_id] = record
    logger.info("create_item: id=%d name=%r", _next_id, item.name)
    _next_id += 1
    return ItemResponse(**record)


@router.put("/{item_id}", response_model=ItemResponse, summary="Replace an item")
def update_item(item_id: int, item: ItemUpdate) -> ItemResponse:
    """Fully replace an existing item. Raises **404** if not found."""
    _get_item_or_404(item_id)
    record = {"id": item_id, **item.model_dump()}
    _items[item_id] = record
    logger.info("update_item: id=%d name=%r", item_id, item.name)
    return ItemResponse(**record)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an item")
def delete_item(item_id: int) -> None:
    """Remove an item from the store. Raises **404** if not found."""
    _get_item_or_404(item_id)
    del _items[item_id]
    logger.info("delete_item: id=%d", item_id)
