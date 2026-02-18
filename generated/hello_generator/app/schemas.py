# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
"""Pydantic models for request/response validation."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class EchoRequest(BaseModel):
    """Request model for echo endpoint."""
    
    message: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Message to echo back"
    )
    
    @field_validator('message', mode='before')
    @classmethod
    def validate_message(cls, v):
        """Validate message is not empty after stripping whitespace."""
        if not isinstance(v, str):
            raise ValueError('message must be a string')
        
        stripped = v.strip()
        if not stripped:
            raise ValueError('Message cannot be empty after trimming whitespace')
        
        return stripped


class EchoResponse(BaseModel):
    """Response model for echo endpoint."""
    
    message: str = Field(..., description="Echoed message")


class ItemRequest(BaseModel):
    """Request model for creating an item."""
    
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Item name"
    )
    description: Optional[str] = Field(
        None,
        max_length=500,
        description="Item description"
    )
    price: float = Field(
        ...,
        gt=0,
        description="Item price (must be positive)"
    )


class ItemResponse(BaseModel):
    """Response model for item operations."""
    
    id: int = Field(..., description="Item ID")
    name: str = Field(..., description="Item name")
    description: Optional[str] = Field(None, description="Item description")
    price: float = Field(..., description="Item price")


class HealthResponse(BaseModel):
    """Response model for health check."""
    
    status: str = Field(..., description="Service status")


class VersionResponse(BaseModel):
    """Response model for version endpoint."""
    
    version: str = Field(..., description="Application version")
