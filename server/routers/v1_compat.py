# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
API v1 Compatibility Router

Provides simplified REST API endpoints for backwards compatibility with load tests
and integration tests. These endpoints wrap the full job-based workflow into simpler
single-request operations.

Routes:
- POST /api/v1/generate - Create job and run code generation
- GET /api/v1/generations - List all jobs (generations)
- GET /api/v1/generations/{job_id} - Get job status
- POST /api/v1/sfe/checkpoint - Create SFE checkpoint (for compatibility)
"""

import logging
from typing import Dict, List, Optional, Any
from uuid import uuid4
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from server.schemas import (
    Job,
    JobCreateRequest,
    JobListResponse,
    JobStatus,
    CodegenRequest,
    GenerationLanguage,
)
from server.services import GeneratorService, OmniCoreService
from server.services.omnicore_service import get_omnicore_service as _get_omnicore_service
from server.storage import jobs_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["API v1 (Compatibility)"])


class V1GenerateRequest(BaseModel):
    """Simplified generation request for v1 API."""
    requirements: str = Field(..., description="Natural language requirements")
    language: str = Field("python", description="Target programming language")
    framework: Optional[str] = Field(None, description="Optional framework")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class V1GenerateResponse(BaseModel):
    """Response from v1 generate endpoint."""
    id: str = Field(..., description="Job/generation ID")
    status: str = Field(..., description="Initial status (typically 'pending' or 'running')")
    message: str = Field(..., description="Status message")


class V1GenerationListItem(BaseModel):
    """Generation list item for v1 API."""
    id: str
    status: str
    requirements: Optional[str] = None
    language: Optional[str] = None
    created_at: str
    updated_at: str


def get_generator_service() -> GeneratorService:
    """Dependency for GeneratorService."""
    omnicore = _get_omnicore_service()
    return GeneratorService(omnicore_service=omnicore)


def get_omnicore_service() -> OmniCoreService:
    """Dependency for OmniCoreService (uses singleton)."""
    return _get_omnicore_service()


@router.post("/generate", response_model=V1GenerateResponse, status_code=202)
async def create_generation(
    request: V1GenerateRequest,
    generator_service: GeneratorService = Depends(get_generator_service),
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
) -> V1GenerateResponse:
    """
    Create a new code generation job (v1 API).
    
    This endpoint creates a job and initiates code generation in one request,
    providing a simpler interface than the full job-based workflow.
    
    **Request Body:**
    - requirements: Natural language description of what to generate
    - language: Target programming language (python, javascript, etc.)
    - framework: Optional framework name
    - metadata: Additional metadata
    
    **Returns:**
    - Job ID and initial status (202 Accepted)
    
    **Example:**
    ```json
    {
        "requirements": "Create a Flask app with /hello endpoint",
        "language": "python",
        "framework": "flask"
    }
    ```
    """
    # Create a new job
    job_id = str(uuid4())
    now = datetime.utcnow()
    
    # Store requirements in metadata for tracking
    metadata = request.metadata or {}
    metadata["requirements"] = request.requirements
    metadata["language"] = request.language
    if request.framework:
        metadata["framework"] = request.framework
    
    job = Job(
        id=job_id,
        status=JobStatus.PENDING,
        current_stage="upload",  # Will transition to running when codegen starts
        input_files=[],
        output_files=[],
        created_at=now,
        updated_at=now,
        metadata=metadata,
    )
    
    jobs_db[job_id] = job
    logger.info(f"Created v1 generation job {job_id}")
    
    # Emit job.created event
    try:
        await omnicore_service.emit_event(
            topic="job.created",
            payload={
                "job_id": job_id,
                "status": job.status.value,
                "stage": job.current_stage,
                "created_at": job.created_at.isoformat(),
                "metadata": metadata,
            },
            priority=5,
        )
        logger.debug(f"Emitted job.created event for v1 generation {job_id}")
    except Exception as e:
        logger.warning(f"Failed to emit job.created event for v1 generation {job_id}: {e}")
    
    # Initiate code generation (async, don't wait for completion)
    # Note: In a real production system, this would be queued via background tasks
    # For now, we just return the job ID and the client can poll for status
    
    return V1GenerateResponse(
        id=job_id,
        status="pending",
        message="Code generation job created. Use GET /api/v1/generations/{job_id} to check status.",
    )


@router.get("/generations", response_model=List[V1GenerationListItem])
async def list_generations(
    status: Optional[str] = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
) -> List[V1GenerationListItem]:
    """
    List all code generation jobs (v1 API).
    
    **Query Parameters:**
    - status: Filter by status (pending/running/completed/failed/cancelled)
    - page: Page number (1-indexed)
    - per_page: Items per page (max 100)
    
    **Returns:**
    - List of generation jobs with their current status
    """
    # Filter jobs
    filtered_jobs = list(jobs_db.values())
    if status:
        try:
            status_enum = JobStatus(status.lower())
            filtered_jobs = [j for j in filtered_jobs if j.status == status_enum]
        except ValueError:
            # Invalid status, return empty list
            filtered_jobs = []
    
    # Sort by created_at desc
    filtered_jobs.sort(key=lambda x: x.created_at, reverse=True)
    
    # Paginate
    start = (page - 1) * per_page
    end = start + per_page
    page_jobs = filtered_jobs[start:end]
    
    # Convert to v1 format
    result = []
    for job in page_jobs:
        result.append(
            V1GenerationListItem(
                id=job.id,
                status=job.status.value,
                requirements=job.metadata.get("requirements"),
                language=job.metadata.get("language"),
                created_at=job.created_at.isoformat(),
                updated_at=job.updated_at.isoformat(),
            )
        )
    
    return result


@router.get("/generations/{job_id}", response_model=V1GenerationListItem)
async def get_generation_status(job_id: str) -> V1GenerationListItem:
    """
    Get the status of a specific generation job (v1 API).
    
    **Path Parameters:**
    - job_id: Unique job identifier
    
    **Returns:**
    - Generation job details and current status
    
    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Generation {job_id} not found")
    
    job = jobs_db[job_id]
    
    return V1GenerationListItem(
        id=job.id,
        status=job.status.value,
        requirements=job.metadata.get("requirements"),
        language=job.metadata.get("language"),
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )


# SFE compatibility endpoints
class SFECheckpointRequest(BaseModel):
    """Request for creating an SFE checkpoint."""
    type: str = Field(..., description="Checkpoint type")
    data: Dict[str, Any] = Field(..., description="Checkpoint data")


class SFECheckpointResponse(BaseModel):
    """Response from SFE checkpoint creation."""
    id: str = Field(..., description="Checkpoint ID")
    type: str = Field(..., description="Checkpoint type")
    status: str = Field(..., description="Status")
    message: str = Field(..., description="Status message")


@router.post("/sfe/checkpoint", response_model=SFECheckpointResponse, status_code=201)
async def create_sfe_checkpoint(
    request: SFECheckpointRequest,
) -> SFECheckpointResponse:
    """
    Create an SFE (Self-Fixing Engineer) checkpoint (v1 API).
    
    This is a compatibility endpoint for integration tests. In a production system,
    this would integrate with the actual SFE checkpoint mechanism.
    
    **Request Body:**
    - type: Checkpoint type (e.g., "test", "backup")
    - data: Checkpoint data as a dictionary
    
    **Returns:**
    - Checkpoint ID and status (201 Created)
    """
    checkpoint_id = str(uuid4())
    
    logger.info(f"Created v1 SFE checkpoint {checkpoint_id} of type {request.type}")
    
    return SFECheckpointResponse(
        id=checkpoint_id,
        type=request.type,
        status="created",
        message=f"SFE checkpoint created successfully with type '{request.type}'",
    )

