"""
Generator module endpoints.

Handles file uploads and generator-specific operations.
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from server.schemas import GeneratorStatus, JobStatus, LogsResponse, SuccessResponse
from server.services import GeneratorService
from server.storage import jobs_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generator", tags=["Generator"])


def get_generator_service() -> GeneratorService:
    """Dependency for GeneratorService."""
    from server.routers.jobs import get_omnicore_service

    omnicore = get_omnicore_service()
    return GeneratorService(omnicore_service=omnicore)


@router.post("/{job_id}/upload", response_model=SuccessResponse)
async def upload_files(
    job_id: str,
    files: List[UploadFile] = File(
        ..., description="Files to upload (e.g., README.md, test files)"
    ),
    generator_service: GeneratorService = Depends(get_generator_service),
) -> SuccessResponse:
    """
    Upload files for a generator job.

    Accepts multiple files including:
    - README.md or other markdown files with requirements
    - Test files (*.test.js, *_test.py, *.spec.ts, etc.)
    - Configuration files
    - Documentation files

    Triggers job creation in the generator module via OmniCore.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Request Body:**
    - files: List of files to upload (multipart/form-data)

    **Returns:**
    - Success confirmation with uploaded file details

    **Errors:**
    - 404: Job not found
    - 400: No files provided
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    job = jobs_db[job_id]
    uploaded_files = []

    # Categorize uploaded files by type
    readme_files = []
    test_files = []
    other_files = []

    for file in files:
        # Read file content
        content = await file.read()
        
        # Categorize file
        filename_lower = file.filename.lower()
        if filename_lower.endswith('.md'):
            readme_files.append(file.filename)
        elif any(pattern in filename_lower for pattern in [
            'test', 'spec', '.test.', '_test.', '.spec.', '_spec.'
        ]):
            test_files.append(file.filename)
        else:
            other_files.append(file.filename)

        # Save file via generator service
        result = await generator_service.save_upload(
            job_id=job_id,
            filename=file.filename,
            content=content,
        )

        uploaded_files.append(result)
        job.input_files.append(file.filename)

    # Trigger generator job creation via OmniCore
    await generator_service.create_generation_job(
        job_id=job_id,
        files=[f["path"] for f in uploaded_files],
        metadata={
            **job.metadata,
            "readme_files": readme_files,
            "test_files": test_files,
            "other_files": other_files,
        },
    )

    # Update job status
    job.status = JobStatus.RUNNING
    job.updated_at = datetime.utcnow()

    logger.info(
        f"Uploaded {len(files)} files for job {job_id}: "
        f"{len(readme_files)} README, {len(test_files)} test, {len(other_files)} other"
    )

    return SuccessResponse(
        success=True,
        message=f"Uploaded {len(files)} files successfully",
        data={
            "uploaded_files": uploaded_files,
            "categorization": {
                "readme_files": readme_files,
                "test_files": test_files,
                "other_files": other_files,
            },
        },
    )


@router.get("/{job_id}/status", response_model=GeneratorStatus)
async def get_generator_status(
    job_id: str,
    generator_service: GeneratorService = Depends(get_generator_service),
) -> GeneratorStatus:
    """
    Get generator-specific status for a job.

    Returns detailed status from the generator module including:
    - Current generation stage
    - Progress percentage
    - Recent log messages

    **Path Parameters:**
    - job_id: Unique job identifier

    **Returns:**
    - Generator status information

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    status = await generator_service.get_job_status(job_id)
    return status


@router.get("/{job_id}/logs", response_model=LogsResponse)
async def get_generator_logs(
    job_id: str,
    limit: int = 100,
    generator_service: GeneratorService = Depends(get_generator_service),
) -> LogsResponse:
    """
    Get logs from the generator module for a specific job.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Query Parameters:**
    - limit: Maximum number of log entries (default: 100)

    **Returns:**
    - List of log entries

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    logs = await generator_service.get_job_logs(job_id, limit=limit)
    return LogsResponse(job_id=job_id, logs=logs, count=len(logs))


@router.post("/{job_id}/clarify")
async def clarify_requirements(
    job_id: str,
    generator_service: GeneratorService = Depends(get_generator_service),
):
    """
    Trigger the clarifier to analyze and clarify requirements.

    Initiates the clarification process through OmniCore, which routes
    the request to the generator's clarifier module. The clarifier uses
    LLM-based analysis and interactive user feedback to resolve ambiguities
    in requirements.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Returns:**
    - Clarification initiation status and detected ambiguities

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job = jobs_db[job_id]

    # Get README content from uploaded files
    readme_content = ""
    for filename in job.input_files:
        if filename.lower().endswith('.md'):
            file_path = f"./uploads/{job_id}/{filename}"
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    readme_content = f.read()
                    break
            except Exception as e:
                logger.warning(f"Could not read file {filename}: {e}")

    if not readme_content:
        raise HTTPException(
            status_code=400,
            detail="No README content found for clarification"
        )

    result = await generator_service.clarify_requirements(
        job_id=job_id,
        readme_content=readme_content,
    )

    logger.info(f"Clarification initiated for job {job_id}")
    return result


@router.get("/{job_id}/clarification/feedback")
async def get_clarification_feedback(
    job_id: str,
    interaction_id: Optional[str] = None,
    generator_service: GeneratorService = Depends(get_generator_service),
):
    """
    Get feedback from the clarifier's interactive process.

    Retrieves the current status of clarification, including questions
    asked to users, responses received, and overall progress. This enables
    monitoring the clarification feedback loop through OmniCore.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Query Parameters:**
    - interaction_id: Optional specific interaction ID to query

    **Returns:**
    - Clarification feedback status and interaction history

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    feedback = await generator_service.get_clarification_feedback(
        job_id=job_id,
        interaction_id=interaction_id,
    )

    return feedback


@router.post("/{job_id}/clarification/respond")
async def submit_clarification_response(
    job_id: str,
    question_id: str,
    response: str,
    generator_service: GeneratorService = Depends(get_generator_service),
):
    """
    Submit a response to a clarification question.

    Allows users to provide answers to clarification questions through
    the API. The response is routed through OmniCore to the clarifier
    module for processing.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Request Body:**
    - question_id: ID of the question being answered
    - response: User's response to the question

    **Returns:**
    - Response submission confirmation

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    result = await generator_service.submit_clarification_response(
        job_id=job_id,
        question_id=question_id,
        response=response,
    )

    logger.info(f"Clarification response submitted for job {job_id}, question {question_id}")
    return result
