# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""Product routes.

Handler names are distinct from imported service function names to avoid
recursion bugs (e.g. a handler named `list_products` that calls the
imported `list_products` from the service layer would call itself).
"""

from fastapi import APIRouter, HTTPException, status

from app.schemas import ProductCreate, ProductResponse
from app.services import products as products_service

router = APIRouter(prefix="/products", tags=["products"])


@router.get("/", response_model=list[ProductResponse])
async def get_products() -> list[ProductResponse]:
    """Return all available products."""
    return [ProductResponse(**p) for p in products_service.get_all_products()]


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(product_id: int) -> ProductResponse:
    """Return a single product by ID."""
    product = products_service.get_product(product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
    return ProductResponse(**product)


@router.post("/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(payload: ProductCreate) -> ProductResponse:
    """Create and return a new product."""
    product = products_service.create_product(
        name=payload.name,
        price=payload.price,
        description=payload.description,
    )
    return ProductResponse(**product)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_product(product_id: int) -> None:
    """Delete a product by ID."""
    deleted = products_service.delete_product(product_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
