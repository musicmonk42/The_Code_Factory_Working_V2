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

Design Principles:
- Follows RESTful API design patterns
- Provides clear error messages with appropriate HTTP status codes
- Implements proper input validation using Pydantic models
- Uses dependency injection for service layer integration
- Maintains consistency with existing router patterns in the codebase
- Logs all significant operations for observability
- Gracefully handles errors without exposing internal details
"""

import asyncio
import logging
import re
from typing import Dict, List, Optional, Any
from uuid import uuid4
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

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
from server.storage import jobs_db, add_job
from server.routers.generator import _run_pipeline_with_semaphore

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1",
    tags=["API v1 (Compatibility)"],
    responses={
        500: {"description": "Internal server error"},
        503: {"description": "Service temporarily unavailable"},
    },
)

# UUID validation pattern (RFC 4122)
UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE
)

# Supported programming languages
SUPPORTED_LANGUAGES = {"python", "javascript", "typescript", "go", "java", "rust"}


async def _emit_event_fire_and_forget(
    omnicore_service: OmniCoreService,
    topic: str,
    payload: Dict[str, Any],
    priority: int = 5,
) -> None:
    """
    Fire-and-forget wrapper for emitting OmniCore events.
    
    This function runs in the background without blocking the response.
    Errors are logged but do not propagate to the caller.
    
    Args:
        omnicore_service: OmniCore service instance
        topic: Event topic
        payload: Event payload
        priority: Event priority (default: 5)
    """
    try:
        await omnicore_service.emit_event(
            topic=topic,
            payload=payload,
            priority=priority,
        )
        logger.debug(f"Emitted {topic} event in background")
    except Exception as e:
        logger.warning(f"Failed to emit {topic} event in background: {e}")


class V1GenerateRequest(BaseModel):
    """
    Simplified generation request for v1 API.
    
    Validates input to ensure requirements are non-empty and language is supported.
    """
    requirements: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Natural language requirements for code generation"
    )
    language: str = Field(
        "python",
        description="Target programming language"
    )
    framework: Optional[str] = Field(
        None,
        max_length=100,
        description="Optional framework (e.g., flask, react)"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional metadata for the generation job"
    )
    
    @classmethod
    @field_validator('requirements')
    def validate_requirements(cls, v):
        """Ensure requirements are not just whitespace."""
        if not v or not v.strip():
            raise ValueError("Requirements cannot be empty or whitespace only")
        return v.strip()
    
    @classmethod
    @field_validator('language')
    def validate_language(cls, v):
        """Validate programming language is supported."""
        language_lower = v.lower()
        if language_lower not in SUPPORTED_LANGUAGES:
            supported = ", ".join(sorted(SUPPORTED_LANGUAGES))
            raise ValueError(
                f"Language '{v}' is not supported. Supported languages: {supported}"
            )
        return language_lower


class V1GenerateResponse(BaseModel):
    """
    Response from v1 generate endpoint.
    
    Returns the newly created job ID and status information.
    """
    id: str = Field(..., description="Job/generation ID (UUID format)")
    status: str = Field(..., description="Initial status (typically 'pending' or 'running')")
    message: str = Field(..., description="Human-readable status message")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "pending",
                "message": "Code generation job created. Use GET /api/v1/generations/{job_id} to check status."
            }
        }


class V1GenerationListItem(BaseModel):
    """
    Generation list item for v1 API.
    
    Represents a single generation job in list responses.
    """
    id: str = Field(..., description="Job/generation ID")
    status: str = Field(..., description="Current job status")
    requirements: Optional[str] = Field(None, description="Original requirements")
    language: Optional[str] = Field(None, description="Target programming language")
    created_at: str = Field(..., description="ISO 8601 timestamp of creation")
    updated_at: str = Field(..., description="ISO 8601 timestamp of last update")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "completed",
                "requirements": "Create a Flask app with /hello endpoint",
                "language": "python",
                "created_at": "2025-02-11T04:12:00Z",
                "updated_at": "2025-02-11T04:15:30Z"
            }
        }


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
    background_tasks: BackgroundTasks,
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
    now = datetime.now(timezone.utc)
    
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
    
    add_job(job)
    logger.info(f"Created v1 generation job {job_id}")
    
    # Emit job.created event in background (fire-and-forget)
    asyncio.create_task(
        _emit_event_fire_and_forget(
            omnicore_service=omnicore_service,
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
    )
    
    # Trigger the generation pipeline as a background task
    # Uses the requirements text as the README content for the pipeline
    # Use asyncio.create_task instead of BackgroundTasks to prevent event loop blocking
    asyncio.create_task(
        _run_pipeline_with_semaphore(
            job_id=job_id,
            readme_content=request.requirements,
            generator_service=generator_service,
        )
    )
    logger.info(f"Background pipeline triggered for v1 generation job {job_id}")
    
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


@router.get(
    "/generations/{job_id}",
    response_model=V1GenerationListItem,
    responses={
        200: {"description": "Generation job found"},
        400: {"description": "Invalid job ID format"},
        404: {"description": "Generation job not found"},
    }
)
async def get_generation_status(job_id: str) -> V1GenerationListItem:
    """
    Get the status of a specific generation job (v1 API).
    
    **Path Parameters:**
    - job_id: Unique job identifier (UUID format)
    
    **Returns:**
    - Generation job details and current status
    
    **Errors:**
    - 400: Invalid job ID format (not a valid UUID)
    - 404: Job not found
    """
    # Validate job_id is a valid UUID
    if not UUID_PATTERN.match(job_id):
        logger.warning(f"Invalid job ID format received: {job_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid job ID format. Expected UUID, got: {job_id[:50]}"
        )
    
    if job_id not in jobs_db:
        logger.info(f"Job not found: {job_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Generation {job_id} not found"
        )
    
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
    """
    Request for creating an SFE checkpoint.
    
    Validates checkpoint type and data structure.
    """
    type: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Checkpoint type (e.g., 'test', 'backup', 'milestone')"
    )
    data: Dict[str, Any] = Field(
        ...,
        description="Checkpoint data as a dictionary"
    )
    
    @classmethod
    @field_validator('type')
    def validate_type(cls, v):
        """Ensure checkpoint type is not just whitespace."""
        if not v or not v.strip():
            raise ValueError("Checkpoint type cannot be empty or whitespace only")
        return v.strip()


class SFECheckpointResponse(BaseModel):
    """
    Response from SFE checkpoint creation.
    
    Returns the checkpoint ID and confirmation message.
    """
    id: str = Field(..., description="Checkpoint ID (UUID format)")
    type: str = Field(..., description="Checkpoint type")
    status: str = Field(..., description="Checkpoint status")
    message: str = Field(..., description="Human-readable confirmation message")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "type": "test",
                "status": "created",
                "message": "SFE checkpoint created successfully with type 'test'"
            }
        }


@router.post(
    "/sfe/checkpoint",
    response_model=SFECheckpointResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Checkpoint created successfully"},
        400: {"description": "Invalid request data"},
        500: {"description": "Internal server error"},
    }
)
async def create_sfe_checkpoint(
    request: SFECheckpointRequest,
) -> SFECheckpointResponse:
    """
    Create an SFE (Self-Fixing Engineer) checkpoint (v1 API).
    
    This is a compatibility endpoint for integration tests. In a production system,
    this would integrate with the actual SFE checkpoint mechanism.
    
    **Request Body:**
    - type: Checkpoint type (e.g., "test", "backup", "milestone")
    - data: Checkpoint data as a dictionary
    
    **Returns:**
    - Checkpoint ID and status (201 Created)
    
    **Example Request:**
    ```json
    {
        "type": "test",
        "data": {"test": "checkpoint", "version": "1.0"}
    }
    ```
    """
    try:
        checkpoint_id = str(uuid4())
        
        logger.info(
            f"Created v1 SFE checkpoint {checkpoint_id} of type {request.type}",
            extra={
                "checkpoint_id": checkpoint_id,
                "checkpoint_type": request.type,
                "data_keys": list(request.data.keys())
            }
        )
        
        return SFECheckpointResponse(
            id=checkpoint_id,
            type=request.type,
            status="created",
            message=f"SFE checkpoint created successfully with type '{request.type}'",
        )
    except Exception as e:
        logger.error(
            f"Failed to create SFE checkpoint: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create checkpoint. Please try again later."
        )

