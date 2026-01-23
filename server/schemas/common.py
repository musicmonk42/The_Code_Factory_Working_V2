"""
Common schemas used across the API.
"""

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class StatusResponse(BaseModel):
    """Generic status response."""

    status: str = Field(..., description="Status indicator (e.g., 'ok', 'error')")
    message: Optional[str] = Field(None, description="Optional status message")


class SuccessResponse(BaseModel):
    """Generic success response."""

    success: bool = Field(True, description="Operation success indicator")
    message: str = Field(..., description="Success message")
    data: Optional[Dict[str, Any]] = Field(None, description="Optional response data")


class ErrorResponse(BaseModel):
    """Generic error response."""

    error: str = Field(..., description="Error type or code")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[Dict[str, Any]] = Field(
        None, description="Optional error details"
    )


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(..., description="Health status (healthy/unhealthy)")
    version: str = Field(..., description="API version")
    components: Dict[str, str] = Field(..., description="Component health statuses")
    timestamp: str = Field(..., description="Health check timestamp (ISO 8601)")


class ReadinessResponse(BaseModel):
    """Readiness check response."""

    ready: bool = Field(..., description="Whether the application is ready to accept traffic")
    status: str = Field(..., description="Overall readiness status")
    checks: Dict[str, str] = Field(..., description="Individual readiness check results")
    timestamp: str = Field(..., description="Readiness check timestamp (ISO 8601)")


class DetailedHealthResponse(BaseModel):
    """Detailed health check response with dependency and feature status."""

    status: str = Field(..., description="Overall health status")
    version: str = Field(..., description="API version")
    timestamp: str = Field(..., description="Health check timestamp (ISO 8601)")
    agents: Dict[str, str] = Field(..., description="Agent availability status")
    dependencies: Dict[str, str] = Field(..., description="External dependency status")
    optional_features: Dict[str, str] = Field(..., description="Optional feature status")


class PaginationParams(BaseModel):
    """Pagination parameters for list endpoints."""

    page: int = Field(1, ge=1, description="Page number (1-indexed)")
    per_page: int = Field(20, ge=1, le=100, description="Items per page (max 100)")
