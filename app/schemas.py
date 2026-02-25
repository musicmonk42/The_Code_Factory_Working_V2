# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Pydantic schemas for request/response models.

All models inherit from BaseModel to ensure FastAPI can generate
valid OpenAPI documentation and perform request validation.
"""

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Auth schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    """Credentials for user login."""

    username: str = Field(..., min_length=1, description="Username")
    password: str = Field(..., min_length=1, description="Password")


class TokenResponse(BaseModel):
    """JWT access token returned after successful login."""

    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Public user information."""

    id: int
    username: str


# ---------------------------------------------------------------------------
# Product schemas
# ---------------------------------------------------------------------------

class ProductBase(BaseModel):
    """Shared product fields."""

    name: str = Field(..., min_length=1)
    price: float = Field(..., gt=0)
    description: str = ""


class ProductCreate(ProductBase):
    """Fields required when creating a new product."""


class ProductResponse(ProductBase):
    """Product record as returned by the API."""

    id: int


# ---------------------------------------------------------------------------
# Order schemas
# ---------------------------------------------------------------------------

class OrderItemBase(BaseModel):
    """A single line item within an order."""

    product_id: int
    quantity: int = Field(..., gt=0)


class OrderCreate(BaseModel):
    """Fields required when placing a new order."""

    items: list[OrderItemBase]


class OrderResponse(BaseModel):
    """Order record as returned by the API."""

    id: int
    status: str
    items: list[OrderItemBase]


# ---------------------------------------------------------------------------
# Audit schemas
# ---------------------------------------------------------------------------

class AuditLogEntry(BaseModel):
    """A single entry in the audit log."""

    id: int
    action: str
    actor: str
    timestamp: str
