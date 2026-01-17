"""
Job management endpoints.

Handles job lifecycle: creation, listing, viewing, status, and progress tracking.
"""

import logging
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query

from server.schemas import (
    Job,
    JobCreateRequest,
    JobListResponse,
    JobProgress,
    JobStage,
    JobStatus,
    PaginationParams,
    StageProgress,
    SuccessResponse,
)
from server.services import GeneratorService, OmniCoreService
from server.storage import jobs_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["Jobs"])


def get_generator_service() -> GeneratorService:
    """Dependency for GeneratorService."""
    omnicore = get_omnicore_service()
    return GeneratorService(omnicore_service=omnicore)


def get_omnicore_service() -> OmniCoreService:
    """Dependency for OmniCoreService."""
    return OmniCoreService()


@router.post("/", response_model=Job, status_code=201)
async def create_job(
    request: JobCreateRequest,
    generator_service: GeneratorService = Depends(get_generator_service),
) -> Job:
    """
    Create a new job.

    Creates a new job entry in the system. Files can be uploaded separately
    via the upload endpoint.

    **Request Body:**
    - description: Optional job description
    - metadata: Additional job metadata

    **Returns:**
    - Job object with unique ID and initial status
    """
    job_id = str(uuid4())
    now = datetime.utcnow()

    job = Job(
        id=job_id,
        status=JobStatus.PENDING,
        current_stage=JobStage.UPLOAD,
        input_files=[],
        created_at=now,
        updated_at=now,
        metadata=request.metadata or {},
    )

    jobs_db[job_id] = job
    logger.info(f"Created job {job_id}")

    return job


@router.get("/", response_model=JobListResponse)
async def list_jobs(
    status: Optional[JobStatus] = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
) -> JobListResponse:
    """
    List all jobs with optional filtering and pagination.

    **Query Parameters:**
    - status: Filter jobs by status (pending/running/completed/failed/cancelled)
    - page: Page number (1-indexed)
    - per_page: Items per page (max 100)

    **Returns:**
    - Paginated list of jobs with total count
    """
    # Filter jobs
    filtered_jobs = list(jobs_db.values())
    if status:
        filtered_jobs = [j for j in filtered_jobs if j.status == status]

    # Sort by created_at desc
    filtered_jobs.sort(key=lambda x: x.created_at, reverse=True)

    # Paginate
    total = len(filtered_jobs)
    start = (page - 1) * per_page
    end = start + per_page
    paginated = filtered_jobs[start:end]

    total_pages = (total + per_page - 1) // per_page

    return JobListResponse(
        jobs=paginated,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


@router.get("/{job_id}", response_model=Job)
async def get_job(job_id: str) -> Job:
    """
    Get detailed information about a specific job.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Returns:**
    - Complete job information

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return jobs_db[job_id]


@router.get("/{job_id}/progress", response_model=JobProgress)
async def get_job_progress(
    job_id: str,
    generator_service: GeneratorService = Depends(get_generator_service),
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
) -> JobProgress:
    """
    Get detailed progress information for a job across all pipeline stages.

    This endpoint provides per-stage progress tracking including:
    - Generator clarification and generation stages
    - OmniCore processing stage
    - SFE analysis and fixing stages

    **Path Parameters:**
    - job_id: Unique job identifier

    **Returns:**
    - Detailed progress with per-stage breakdown

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job = jobs_db[job_id]

    # Get stage-specific progress from services
    # In a real implementation, these would query actual service state
    stages = [
        StageProgress(
            stage=JobStage.UPLOAD,
            status=JobStatus.COMPLETED,
            progress_percent=100.0,
            message="Files uploaded successfully",
            started_at=job.created_at,
            completed_at=job.created_at,
        ),
        StageProgress(
            stage=JobStage.GENERATOR_CLARIFICATION,
            status=(
                JobStatus.RUNNING
                if job.status == JobStatus.RUNNING
                else JobStatus.PENDING
            ),
            progress_percent=50.0 if job.status == JobStatus.RUNNING else 0.0,
            message="Clarifying requirements",
            started_at=job.created_at if job.status == JobStatus.RUNNING else None,
        ),
        # Additional stages would be added here
    ]

    overall_progress = sum(s.progress_percent for s in stages) / len(stages)

    return JobProgress(
        job_id=job_id,
        status=job.status,
        current_stage=job.current_stage,
        overall_progress=overall_progress,
        stages=stages,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.delete("/{job_id}", response_model=SuccessResponse)
async def cancel_job(job_id: str) -> SuccessResponse:
    """
    Cancel a running job.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Returns:**
    - Success confirmation

    **Errors:**
    - 404: Job not found
    - 400: Job cannot be cancelled (already completed/failed)
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job = jobs_db[job_id]

    if job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]:
        raise HTTPException(
            status_code=400,
            detail=f"Job {job_id} is {job.status.value} and cannot be cancelled",
        )

    job.status = JobStatus.CANCELLED
    job.updated_at = datetime.utcnow()

    logger.info(f"Cancelled job {job_id}")

    return SuccessResponse(
        success=True,
        message=f"Job {job_id} cancelled successfully",
    )
