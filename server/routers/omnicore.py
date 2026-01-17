"""
OmniCore Engine endpoints.

Handles engine coordination, plugin management, and system-level operations.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

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
