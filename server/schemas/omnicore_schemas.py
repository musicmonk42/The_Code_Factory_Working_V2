# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
OmniCore Engine specific schemas.

Request and response models for OmniCore control endpoints.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MessageBusPublishRequest(BaseModel):
    """Request to publish message to bus."""
    topic: str = Field(..., description="Message topic/channel")
    payload: Dict[str, Any] = Field(..., description="Message payload")
    priority: int = Field(5, ge=1, le=10, description="Message priority (1-10)")
    ttl: Optional[int] = Field(None, description="Time-to-live in seconds")


class MessageBusSubscribeRequest(BaseModel):
    """Request to subscribe to message bus topic."""
    topic: str = Field(..., description="Topic to subscribe to")
    callback_url: Optional[str] = Field(None, description="Optional webhook URL")
    filters: Optional[Dict[str, Any]] = Field(None, description="Message filters")


class PluginReloadRequest(BaseModel):
    """Request to reload a plugin."""
    plugin_id: str = Field(..., description="Plugin identifier to reload")
    force: bool = Field(False, description="Force reload even if errors")


class PluginInstallRequest(BaseModel):
    """Request to install a plugin."""
    plugin_name: str = Field(..., description="Plugin name")
    version: Optional[str] = Field(None, description="Specific version (latest if omitted)")
    source: str = Field("marketplace", description="Installation source")
    config: Optional[Dict[str, Any]] = Field(None, description="Plugin configuration")


class DatabaseQueryRequest(BaseModel):
    """Request to query OmniCore database."""
    query_type: str = Field(..., description="Query type (jobs, audit, metrics)")
    filters: Optional[Dict[str, Any]] = Field(None, description="Query filters")
    limit: int = Field(100, ge=1, le=1000, description="Max results")


class DatabaseExportRequest(BaseModel):
    """Request to export database state."""
    export_type: str = Field("full", description="Export type (full, incremental)")
    format: str = Field("json", description="Export format (json, csv, sql)")
    include_audit: bool = Field(True, description="Include audit logs")


class CircuitBreakerStatus(BaseModel):
    """Circuit breaker status."""
    name: str = Field(..., description="Circuit breaker name")
    state: str = Field(..., description="State (closed, open, half-open)")
    failure_count: int = Field(..., description="Consecutive failure count")
    last_failure_time: Optional[str] = Field(None, description="Last failure timestamp")


class CircuitBreakerResetRequest(BaseModel):
    """Request to reset circuit breaker."""
    name: str = Field(..., description="Circuit breaker name to reset")


class RateLimitConfigRequest(BaseModel):
    """Request to configure rate limits."""
    endpoint: str = Field(..., description="Endpoint or service to limit")
    requests_per_second: float = Field(..., gt=0, description="Requests per second")
    burst_size: Optional[int] = Field(None, description="Burst capacity")


class DeadLetterQueueQuery(BaseModel):
    """Query parameters for dead letter queue."""
    start_time: Optional[str] = Field(None, description="Start timestamp (ISO 8601)")
    end_time: Optional[str] = Field(None, description="End timestamp (ISO 8601)")
    topic: Optional[str] = Field(None, description="Filter by topic")
    limit: int = Field(100, ge=1, le=1000, description="Max results")


class MessageRetryRequest(BaseModel):
    """Request to retry failed message."""
    message_id: str = Field(..., description="Message ID to retry")
    force: bool = Field(False, description="Force retry even if max attempts reached")


class PluginMarketplaceQuery(BaseModel):
    """Query parameters for plugin marketplace."""
    category: Optional[str] = Field(None, description="Plugin category filter")
    search: Optional[str] = Field(None, description="Search term")
    sort: str = Field("popularity", description="Sort by (popularity, date, name)")
    limit: int = Field(20, ge=1, le=100, description="Max results")


class PluginInfo(BaseModel):
    """Plugin information."""
    plugin_id: str = Field(..., description="Plugin identifier")
    name: str = Field(..., description="Plugin name")
    version: str = Field(..., description="Plugin version")
    status: str = Field(..., description="Plugin status (active, inactive, error)")
    description: Optional[str] = Field(None, description="Plugin description")
    capabilities: List[str] = Field([], description="Plugin capabilities")


class MessageBusTopics(BaseModel):
    """List of message bus topics."""
    topics: List[str] = Field(..., description="Available topics")
    topic_stats: Dict[str, Dict[str, Any]] = Field(..., description="Topic statistics")
