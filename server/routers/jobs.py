"""
Job management endpoints.

Handles job lifecycle: creation, listing, viewing, status, and progress tracking.
"""

import logging
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from server.schemas import (
    GeneratedFile,
    Job,
    JobCreateRequest,
    JobFilesResponse,
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


def _is_path_safe(file_path: Path, base_dir: Path) -> bool:
    """
    Securely check if a file path is within a base directory.
    
    This function provides robust path traversal prevention that:
    - Uses is_relative_to() on Python 3.9+ for accurate validation
    - Handles symlinks safely by checking the resolved path
    - Falls back to path component comparison on older Python versions
    
    Args:
        file_path: The resolved path to check (must be already resolved)
        base_dir: The base directory (must be already resolved)
        
    Returns:
        True if file_path is safely within base_dir, False otherwise
    """
    try:
        # Python 3.9+ provides is_relative_to method
        return file_path.is_relative_to(base_dir)
    except AttributeError:
        # Fallback for Python < 3.9: compare path parts
        try:
            file_path.relative_to(base_dir)
            return True
        except ValueError:
            return False


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
    is_pending = job.status == JobStatus.PENDING  # noqa: F841 - reserved for future use

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


@router.post("/{job_id}/cancel", response_model=SuccessResponse)
async def cancel_job_post(job_id: str) -> SuccessResponse:
    """
    Cancel a running job (POST endpoint).

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

    _ = jobs_db[job_id]  # Verify job exists before deletion

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


@router.get("/{job_id}/files", response_model=JobFilesResponse)
async def list_job_files(job_id: str) -> JobFilesResponse:
    """
    List all generated files for a job.

    Returns detailed information about all files generated by the job,
    including file names, sizes, MIME types, and a download URL for the
    complete archive.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Returns:**
    - List of files with paths, sizes, MIME types, and metadata
    - Total file count and size
    - URL to download all files as a ZIP archive

    **Errors:**
    - 404: Job not found
    
    **Example Response:**
    ```json
    {
      "job_id": "abc123",
      "status": "completed",
      "output_directory": "./uploads/abc123/generated",
      "files": [
        {
          "name": "main.py",
          "path": "generated/main.py",
          "size": 1234,
          "mime_type": "text/x-python",
          "created_at": "2026-01-25T12:00:00Z"
        }
      ],
      "total_files": 5,
      "total_size": 12345,
      "download_url": "/api/jobs/abc123/download"
    }
    ```
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job = jobs_db[job_id]
    job_dir = Path(f"./uploads/{job_id}")
    
    if not job_dir.exists():
        return JobFilesResponse(
            job_id=job_id,
            status=job.status,
            output_directory=str(job_dir),
            files=[],
            total_files=0,
            total_size=0,
            download_url=None,
        )

    files = []
    total_size = 0
    
    for file_path in job_dir.rglob('*'):
        if file_path.is_file():
            stat = file_path.stat()
            mime_type, _ = mimetypes.guess_type(str(file_path))
            
            files.append(GeneratedFile(
                name=file_path.name,
                path=str(file_path.relative_to(job_dir)),
                size=stat.st_size,
                mime_type=mime_type,
                created_at=datetime.fromtimestamp(stat.st_mtime),
            ))
            total_size += stat.st_size

    # Sort files by path for consistent ordering
    files.sort(key=lambda f: f.path)
    
    # Only provide download URL if job is completed and has files
    download_url = None
    if job.status == JobStatus.COMPLETED and files:
        download_url = f"/api/jobs/{job_id}/download"

    return JobFilesResponse(
        job_id=job_id,
        status=job.status,
        output_directory=str(job_dir),
        files=files,
        total_files=len(files),
        total_size=total_size,
        download_url=download_url,
    )


@router.get("/{job_id}/files/{file_path:path}")
async def download_single_file(job_id: str, file_path: str):
    """
    Download a single generated file from a job.

    This endpoint allows downloading individual files from the job's
    output directory. Useful when you only need specific files instead
    of the complete archive.

    **Path Parameters:**
    - job_id: Unique job identifier
    - file_path: Relative path to the file within the job directory

    **Returns:**
    - File content with appropriate MIME type

    **Errors:**
    - 404: Job or file not found
    - 400: Invalid file path (path traversal attempt)
    
    **Example:**
    - GET /api/jobs/abc123/files/generated/main.py
    - GET /api/jobs/abc123/files/tests/test_main.py
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job_dir = Path(f"./uploads/{job_id}")
    
    # Construct file path and validate it's within job directory (prevent path traversal)
    try:
        requested_file = (job_dir / file_path).resolve()
        job_dir_resolved = job_dir.resolve()
        
        # Security check: ensure the file is within the job directory
        if not _is_path_safe(requested_file, job_dir_resolved):
            raise HTTPException(
                status_code=400,
                detail="Invalid file path: path traversal not allowed"
            )
        
        # Additional security: reject symlinks that point outside the job directory
        if requested_file.is_symlink():
            real_path = requested_file.resolve()
            if not _is_path_safe(real_path, job_dir_resolved):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid file path: symlinks pointing outside job directory not allowed"
                )
                    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid file path: {e}")

    if not requested_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {file_path}"
        )

    if not requested_file.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"Path is not a file: {file_path}"
        )

    # Determine MIME type
    mime_type, _ = mimetypes.guess_type(str(requested_file))
    if mime_type is None:
        mime_type = "application/octet-stream"

    logger.info(f"Serving file {file_path} for job {job_id}")
    
    return FileResponse(
        path=str(requested_file),
        media_type=mime_type,
        filename=requested_file.name,
    )
