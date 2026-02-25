# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""Order routes."""

from fastapi import APIRouter, HTTPException, status

from app.schemas import OrderCreate, OrderResponse
from app.services import orders as orders_service

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("/", response_model=list[OrderResponse])
async def get_orders() -> list[OrderResponse]:
    """Return all orders."""
    return [OrderResponse(**o) for o in orders_service.get_all_orders()]


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(order_id: int) -> OrderResponse:
    """Return a single order by ID."""
    order = orders_service.get_order(order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order {order_id} not found",
        )
    return OrderResponse(**order)


@router.post("/", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def place_order(payload: OrderCreate) -> OrderResponse:
    """Place a new order."""
    items = [item.model_dump() for item in payload.items]
    order = orders_service.create_order(items)
    return OrderResponse(**order)
