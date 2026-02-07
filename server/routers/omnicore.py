# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
OmniCore Engine endpoints.

Handles engine coordination, plugin management, and system-level operations.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from server.schemas import (
    CircuitBreakerResetRequest,
    DatabaseExportRequest,
    DatabaseQueryRequest,
    MessageBusPublishRequest,
    MessageBusSubscribeRequest,
    PluginInstallRequest,
    PluginReloadRequest,
    RateLimitConfigRequest,
)
from server.services import OmniCoreService
from server.storage import jobs_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/omnicore", tags=["OmniCore Engine"])


def get_omnicore_service() -> OmniCoreService:
    """Dependency for OmniCoreService."""
    return OmniCoreService()


@router.get("/plugins")
async def get_plugins(
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Get status of registered plugins.

    Returns information about all plugins registered with the OmniCore Engine,
    including their active status and capabilities.

    **Returns:**
    - Plugin registry information
    """
    status = await omnicore_service.get_plugin_status()
    return status


@router.get("/{job_id}/metrics")
async def get_job_metrics(
    job_id: str,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Get OmniCore metrics for a specific job.

    Returns detailed metrics collected by the OmniCore Engine for a job,
    including processing time, resource usage, and performance indicators.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Returns:**
    - Job metrics from OmniCore

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    metrics = await omnicore_service.get_job_metrics(job_id)
    return metrics


@router.get("/{job_id}/audit")
async def get_audit_trail(
    job_id: str,
    limit: int = 100,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Get audit trail for a job.

    Returns the tamper-evident audit trail from the OmniCore Engine,
    showing all actions and state changes for the job.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Query Parameters:**
    - limit: Maximum number of audit entries (default: 100)

    **Returns:**
    - List of audit trail entries

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    trail = await omnicore_service.get_audit_trail(job_id, limit=limit)
    return {"job_id": job_id, "audit_trail": trail, "count": len(trail)}


@router.get("/system-health")
async def get_system_health(
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Get detailed system health from OmniCore perspective.

    Returns detailed health status of all OmniCore components including:
    - Message bus
    - Database
    - Plugin registry
    - Module connectivity

    This endpoint provides more granular health information than the main /health endpoint.

    **Returns:**
    - Detailed system health information
    """
    health = await omnicore_service.get_system_health()
    return health


@router.post("/{job_id}/workflow/{workflow_name}")
async def trigger_workflow(
    job_id: str,
    workflow_name: str,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Trigger a specific workflow for a job.

    Initiates a named workflow in the OmniCore Engine for the specified job.

    **Path Parameters:**
    - job_id: Unique job identifier
    - workflow_name: Name of the workflow to trigger

    **Returns:**
    - Workflow execution status

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    result = await omnicore_service.trigger_workflow(
        workflow_name=workflow_name,
        job_id=job_id,
        params={},
    )
    return result


@router.post("/message-bus/publish")
async def publish_message(
    request: MessageBusPublishRequest,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Publish message to message bus.

    Allows direct publishing of messages to the OmniCore message bus for inter-module communication.

    **Request Body:**
    - topic: Message topic/channel
    - payload: Message payload
    - priority: Message priority (1-10)
    - ttl: Time-to-live in seconds

    **Returns:**
    - Publication confirmation with message ID
    """
    result = await omnicore_service.publish_message(
        topic=request.topic,
        payload=request.payload,
        priority=request.priority,
        ttl=request.ttl,
    )

    logger.info(f"Message published to topic {request.topic}")
    return result


@router.post("/message-bus/subscribe")
async def subscribe_to_topic(
    request: MessageBusSubscribeRequest,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Subscribe to message bus topic.

    Creates a subscription to receive messages from a specific topic.

    **Request Body:**
    - topic: Topic to subscribe to
    - callback_url: Optional webhook URL for message delivery
    - filters: Optional message filters

    **Returns:**
    - Subscription confirmation with subscription ID
    """
    result = await omnicore_service.subscribe_to_topic(
        topic=request.topic,
        callback_url=request.callback_url,
        filters=request.filters,
    )

    logger.info(f"Subscribed to topic {request.topic}")
    return result


@router.get("/message-bus/topics")
async def list_topics(
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    List all message bus topics.

    Returns available topics and their statistics (subscriber count, message count).

    **Returns:**
    - Topics list and statistics
    """
    result = await omnicore_service.list_topics()
    return result


@router.post("/plugins/{plugin_id}/reload")
async def reload_plugin(
    plugin_id: str,
    request: PluginReloadRequest,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Hot-reload a plugin.

    Dynamically reloads a plugin without restarting the server.

    **Path Parameters:**
    - plugin_id: Plugin identifier to reload

    **Request Body:**
    - force: Force reload even if errors

    **Returns:**
    - Reload result
    """
    result = await omnicore_service.reload_plugin(
        plugin_id=plugin_id,
        force=request.force,
    )

    logger.info(f"Plugin {plugin_id} reloaded")
    return result


@router.get("/plugins/marketplace")
async def browse_marketplace(
    category: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "popularity",
    limit: int = 20,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Browse plugin marketplace.

    Lists available plugins from the marketplace with filtering and search.

    **Query Parameters:**
    - category: Filter by plugin category
    - search: Search term
    - sort: Sort by (popularity, date, name)
    - limit: Maximum results (default: 20, max: 100)

    **Returns:**
    - Plugin listings
    """
    result = await omnicore_service.browse_marketplace(
        category=category,
        search=search,
        sort=sort,
        limit=min(limit, 100),
    )

    return result


@router.post("/plugins/install")
async def install_plugin(
    request: PluginInstallRequest,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Install a plugin.

    Installs a plugin from the marketplace or custom source.

    **Request Body:**
    - plugin_name: Plugin name
    - version: Specific version (latest if omitted)
    - source: Installation source
    - config: Plugin configuration

    **Returns:**
    - Installation result
    """
    result = await omnicore_service.install_plugin(
        plugin_name=request.plugin_name,
        version=request.version,
        source=request.source,
        config=request.config,
    )

    logger.info(f"Plugin {request.plugin_name} installed")
    return result


@router.post("/database/query")
async def query_database(
    request: DatabaseQueryRequest,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Query OmniCore database.

    Allows querying of OmniCore's internal state database.

    **Request Body:**
    - query_type: Query type (jobs, audit, metrics)
    - filters: Query filters
    - limit: Maximum results

    **Returns:**
    - Query results
    """
    result = await omnicore_service.query_database(
        query_type=request.query_type,
        filters=request.filters,
        limit=request.limit,
    )

    return result


@router.post("/database/export")
async def export_database(
    request: DatabaseExportRequest,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Export database state.

    Exports OmniCore database to file for backup or analysis.

    **Request Body:**
    - export_type: Export type (full, incremental)
    - format: Export format (json, csv, sql)
    - include_audit: Include audit logs

    **Returns:**
    - Export result with download path
    """
    result = await omnicore_service.export_database(
        export_type=request.export_type,
        format=request.format,
        include_audit=request.include_audit,
    )

    logger.info(f"Database export initiated: {request.export_type}")
    return result


@router.get("/circuit-breakers")
async def get_circuit_breakers(
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Get status of all circuit breakers.

    Returns the status of all registered circuit breakers.

    **Returns:**
    - Circuit breaker statuses
    """
    result = await omnicore_service.get_circuit_breakers()
    return result


@router.post("/circuit-breakers/{name}/reset")
async def reset_circuit_breaker(
    name: str,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Reset a circuit breaker.

    Resets a circuit breaker to closed state.

    **Path Parameters:**
    - name: Circuit breaker name

    **Returns:**
    - Reset result
    """
    result = await omnicore_service.reset_circuit_breaker(name)

    logger.info(f"Circuit breaker {name} reset")
    return result


@router.post("/rate-limits/configure")
async def configure_rate_limit(
    request: RateLimitConfigRequest,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Configure rate limits.

    Sets rate limiting configuration for endpoints or services.

    **Request Body:**
    - endpoint: Endpoint or service to limit
    - requests_per_second: Requests per second
    - burst_size: Burst capacity

    **Returns:**
    - Configuration result
    """
    result = await omnicore_service.configure_rate_limit(
        endpoint=request.endpoint,
        requests_per_second=request.requests_per_second,
        burst_size=request.burst_size,
    )

    logger.info(f"Rate limit configured for {request.endpoint}")
    return result


@router.get("/dead-letter-queue")
async def query_dead_letter_queue(
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    topic: Optional[str] = None,
    limit: int = 100,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Query dead letter queue.

    Retrieves failed messages from the dead letter queue.

    **Query Parameters:**
    - start_time: Start timestamp (ISO 8601)
    - end_time: End timestamp (ISO 8601)
    - topic: Filter by topic
    - limit: Maximum results

    **Returns:**
    - Failed messages
    """
    result = await omnicore_service.query_dead_letter_queue(
        start_time=start_time,
        end_time=end_time,
        topic=topic,
        limit=min(limit, 1000),
    )

    return result


@router.post("/dead-letter-queue/{message_id}/retry")
async def retry_message(
    message_id: str,
    force: bool = False,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
):
    """
    Retry failed message.

    Retries a message from the dead letter queue.

    **Path Parameters:**
    - message_id: Message ID to retry

    **Query Parameters:**
    - force: Force retry even if max attempts reached

    **Returns:**
    - Retry result
    """
    result = await omnicore_service.retry_message(
        message_id=message_id,
        force=force,
    )

    logger.info(f"Message {message_id} retry initiated")
    return result
