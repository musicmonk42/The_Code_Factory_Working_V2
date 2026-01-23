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
        output_files=[],
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

    # Determine stage statuses based on job status
    is_completed = job.status == JobStatus.COMPLETED
    is_failed = job.status == JobStatus.FAILED
    is_running = job.status == JobStatus.RUNNING
    is_pending = job.status == JobStatus.PENDING

    # Build stages list based on actual job state
    stages = []

    # Stage 1: Upload - always completed if job exists with files
    upload_completed = len(job.input_files) > 0
    stages.append(
        StageProgress(
            stage=JobStage.UPLOAD,
            status=JobStatus.COMPLETED if upload_completed else JobStatus.PENDING,
            progress_percent=100.0 if upload_completed else 0.0,
            message="Files uploaded successfully" if upload_completed else "Waiting for file upload",
            started_at=job.created_at,
            completed_at=job.created_at if upload_completed else None,
        )
    )

    # Stage 2: Generator Clarification
    clarification_status = JobStatus.PENDING
    clarification_progress = 0.0
    clarification_message = "Waiting for clarification"
    if is_completed or is_failed:
        clarification_status = JobStatus.COMPLETED
        clarification_progress = 100.0
        clarification_message = "Requirements clarified"
    elif is_running and upload_completed:
        clarification_status = JobStatus.COMPLETED
        clarification_progress = 100.0
        clarification_message = "Requirements clarified"
    stages.append(
        StageProgress(
            stage=JobStage.GENERATOR_CLARIFICATION,
            status=clarification_status,
            progress_percent=clarification_progress,
            message=clarification_message,
            started_at=job.created_at if is_running or is_completed else None,
            completed_at=job.completed_at if is_completed else None,
        )
    )

    # Stage 3: Generator Generation
    generation_status = JobStatus.PENDING
    generation_progress = 0.0
    generation_message = "Waiting for code generation"
    if is_completed or is_failed:
        generation_status = JobStatus.COMPLETED if is_completed else JobStatus.FAILED
        generation_progress = 100.0 if is_completed else 50.0
        generation_message = "Code generated successfully" if is_completed else "Code generation failed"
    elif is_running:
        generation_status = JobStatus.RUNNING
        generation_progress = 50.0
        generation_message = "Generating code..."
    stages.append(
        StageProgress(
            stage=JobStage.GENERATOR_GENERATION,
            status=generation_status,
            progress_percent=generation_progress,
            message=generation_message,
            started_at=job.created_at if is_running or is_completed else None,
            completed_at=job.completed_at if is_completed else None,
            error=job.metadata.get("error") if is_failed else None,
        )
    )

    # Stage 4: OmniCore Processing
    processing_status = JobStatus.PENDING
    processing_progress = 0.0
    processing_message = "Waiting for processing"
    if is_completed or is_failed:
        processing_status = JobStatus.COMPLETED if is_completed else JobStatus.FAILED
        processing_progress = 100.0 if is_completed else 0.0
        processing_message = "Processing completed" if is_completed else "Processing failed"
    elif is_running:
        processing_status = JobStatus.RUNNING
        processing_progress = 50.0
        processing_message = "Processing through OmniCore..."
    stages.append(
        StageProgress(
            stage=JobStage.OMNICORE_PROCESSING,
            status=processing_status,
            progress_percent=processing_progress,
            message=processing_message,
            started_at=job.created_at if is_running or is_completed else None,
            completed_at=job.completed_at if is_completed else None,
        )
    )

    # Stage 5: Completed
    completed_status = JobStatus.COMPLETED if is_completed else (JobStatus.FAILED if is_failed else JobStatus.PENDING)
    completed_progress = 100.0 if is_completed else 0.0
    completed_message = "Job completed successfully" if is_completed else ("Job failed" if is_failed else "Waiting for completion")
    stages.append(
        StageProgress(
            stage=JobStage.COMPLETED,
            status=completed_status,
            progress_percent=completed_progress,
            message=completed_message,
            started_at=job.completed_at if is_completed else None,
            completed_at=job.completed_at if is_completed else None,
            error=job.metadata.get("error") if is_failed else None,
        )
    )

    # Calculate overall progress
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


@router.delete("/{job_id}", response_model=SuccessResponse)
async def delete_job(job_id: str) -> SuccessResponse:
    """
    Delete a job and all associated data.

    Removes the job from the system including all generated files and metadata.
    Can delete jobs in any state (running, completed, failed, etc.).

    **Path Parameters:**
    - job_id: Unique job identifier

    **Returns:**
    - Success confirmation

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job = jobs_db[job_id]

    # Delete job files if they exist
    import shutil
    from pathlib import Path

    job_dir = Path(f"./uploads/{job_id}")
    if job_dir.exists():
        try:
            shutil.rmtree(job_dir)
            logger.info(f"Deleted job files for {job_id}")
        except Exception as e:
            logger.error(f"Error deleting job files for {job_id}: {e}")

    # Remove from database
    del jobs_db[job_id]

    logger.info(f"Deleted job {job_id}")

    return SuccessResponse(
        success=True,
        message=f"Job {job_id} deleted successfully",
        data={"deleted_files": not job_dir.exists()},
    )


@router.get("/{job_id}/download")
async def download_job_files(job_id: str):
    """
    Download generated files for a completed job.

    Returns a ZIP archive containing all generated files (code, tests, configs, docs).

    **Path Parameters:**
    - job_id: Unique job identifier

    **Returns:**
    - ZIP file download (application/zip)

    **Errors:**
    - 404: Job not found or no files available
    - 400: Job not completed yet
    """
    from fastapi.responses import FileResponse
    from pathlib import Path
    import zipfile
    import tempfile

    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job = jobs_db[job_id]

    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Job {job_id} is {job.status.value}, cannot download until completed",
        )

    job_dir = Path(f"./uploads/{job_id}")
    if not job_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No files found for job {job_id}",
        )

    # Create temporary ZIP file
    with tempfile.NamedTemporaryFile(mode='w+b', suffix='.zip', delete=False) as tmp_file:
        zip_path = tmp_file.name

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Add all files from job directory
            for file_path in job_dir.rglob('*'):
                if file_path.is_file():
                    arcname = file_path.relative_to(job_dir)
                    zipf.write(file_path, arcname=arcname)

        logger.info(f"Created ZIP archive for job {job_id}")

    return FileResponse(
        path=zip_path,
        media_type='application/zip',
        filename=f'job_{job_id}_files.zip',
    )


@router.get("/{job_id}/files")
async def list_job_files(job_id: str):
    """
    List all generated files for a job.

    Returns a list of file paths and metadata for all files generated by the job.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Returns:**
    - List of files with paths, sizes, and modification times

    **Errors:**
    - 404: Job not found
    """
    from pathlib import Path

    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job_dir = Path(f"./uploads/{job_id}")
    if not job_dir.exists():
        return {"job_id": job_id, "files": [], "count": 0}

    files = []
    for file_path in job_dir.rglob('*'):
        if file_path.is_file():
            stat = file_path.stat()
            files.append({
                "path": str(file_path.relative_to(job_dir)),
                "full_path": str(file_path),
                "size": stat.st_size,
                "size_human": f"{stat.st_size / 1024:.2f} KB",
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })

    return {
        "job_id": job_id,
        "files": sorted(files, key=lambda x: x['path']),
        "count": len(files),
        "total_size": sum(f['size'] for f in files),
    }
