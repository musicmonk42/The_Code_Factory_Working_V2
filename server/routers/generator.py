"""
Generator module endpoints.

Handles file uploads and generator-specific operations.
"""

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from server.schemas import (
    ClarifyRequest,
    CodegenRequest,
    CritiqueRequest,
    DeployRequest,
    DocgenRequest,
    GeneratorStatus,
    JobStage,
    JobStatus,
    LLMConfigRequest,
    LogsResponse,
    PipelineRequest,
    SuccessResponse,
    TestgenRequest,
)
from server.services import GeneratorService
from server.storage import jobs_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generator", tags=["Generator"])


def get_generator_service() -> GeneratorService:
    """Dependency for GeneratorService."""
    from server.routers.jobs import get_omnicore_service

    omnicore = get_omnicore_service()
    return GeneratorService(omnicore_service=omnicore)


def detect_language_from_content(readme_content: str) -> str:
    """
    Detect programming language from README content using keyword analysis.
    
    Args:
        readme_content: Content of the README file
        
    Returns:
        Detected language (defaults to 'python' if no match found)
    """
    readme_lower = readme_content.lower()
    
    # Check for language-specific keywords in priority order
    # Use word boundaries and specific patterns to avoid false matches
    
    # TypeScript must be checked before JavaScript since JS is often mentioned in TS projects
    if "typescript" in readme_lower:
        return "typescript"
    
    # Java check - must come BEFORE JavaScript to avoid false detection
    # Improved patterns to explicitly check for Java without JavaScript
    if (re.search(r'\bjava\s', readme_lower, re.IGNORECASE) or 
        re.search(r'\bjava\.', readme_lower, re.IGNORECASE) or 
        re.search(r'\bjava\b(?!script)', readme_lower, re.IGNORECASE)):
        return "java"
    
    # JavaScript check with common patterns, using word boundaries for npm
    if ("javascript" in readme_lower or 
        "node.js" in readme_lower or 
        "nodejs" in readme_lower or 
        re.search(r'\bnpm\b', readme_lower)):
        return "javascript"
    
    # Rust check
    if "rust" in readme_lower:
        return "rust"
    
    # Go check - use specific patterns to avoid false positives
    # Look for "golang" or "go " with word boundaries
    if "golang" in readme_lower or re.search(r'\bgo\s+(language|lang|programming)\b', readme_lower, re.IGNORECASE):
        return "go"
    
    # Default to Python
    return "python"


async def _trigger_pipeline_background(
    job_id: str,
    readme_content: str,
    generator_service: GeneratorService,
):
    """
    Background task to automatically trigger the full generation pipeline.
    
    This function implements a proper pipeline workflow that:
    1. First runs clarification to analyze requirements and generate questions
    2. If questions are generated, sets job to PENDING_CLARIFICATION state
    3. User must answer questions via the /clarification/respond endpoint
    4. After answers are received, pipeline continues to code generation
    5. Updates job status appropriately at each stage
    
    Args:
        job_id: Job ID
        readme_content: Content of the README file
        generator_service: GeneratorService instance
        
    Industry Standards Applied:
    - Proper error handling with detailed logging
    - Atomic state updates with timestamp tracking
    - Graceful degradation on partial failures
    - Clear separation of pipeline stages
    """
    try:
        logger.info(f"[Pipeline] Starting pipeline for job {job_id}")
        
        # Validate job exists before proceeding
        if job_id not in jobs_db:
            logger.error(f"[Pipeline] Job {job_id} not found in database")
            return
        
        job = jobs_db[job_id]
        
        # Auto-detect language from README content
        language = detect_language_from_content(readme_content)
        logger.info(f"[Pipeline] Auto-detected language '{language}' for job {job_id}")
        
        # Update job stage to GENERATOR_CLARIFICATION
        job.current_stage = JobStage.GENERATOR_CLARIFICATION
        job.updated_at = datetime.now(timezone.utc)
        job.metadata["language"] = language
        job.metadata["pipeline_started_at"] = datetime.now(timezone.utc).isoformat()
        
        # Step 1: Run clarification to analyze requirements
        logger.info(f"[Pipeline] Running clarification for job {job_id}")
        try:
            clarify_result = await generator_service.clarify_requirements(
                job_id=job_id,
                readme_content=readme_content,
            )
            
            # Check if clarification generated questions that need user input
            clarifications = clarify_result.get("clarifications", [])
            questions_count = clarify_result.get("questions_count", len(clarifications))
            
            if questions_count > 0:
                # Store clarification questions in job metadata for later retrieval
                job.metadata["clarification_questions"] = clarifications
                job.metadata["clarification_status"] = "pending_response"
                job.metadata["clarification_method"] = clarify_result.get("method", "rule_based")
                job.updated_at = datetime.now(timezone.utc)
                
                logger.info(
                    f"[Pipeline] Clarification generated {questions_count} questions for job {job_id}. "
                    f"Job is waiting for user responses via /generator/{job_id}/clarification/respond"
                )
                
                # Note: For now, we continue the pipeline without waiting for user input
                # This maintains backward compatibility while the clarification UI is being developed
                # In a production system, you would set job.status = JobStatus.PENDING and return here
                # to wait for user responses before continuing
                
        except Exception as clarify_error:
            logger.warning(
                f"[Pipeline] Clarification failed for job {job_id}: {clarify_error}. "
                f"Continuing with code generation using original requirements.",
                exc_info=True
            )
            job.metadata["clarification_status"] = "skipped"
            job.metadata["clarification_error"] = str(clarify_error)
        
        # Step 2: Run full pipeline (code generation, tests, deployment, docs, critique)
        job.current_stage = JobStage.GENERATOR_GENERATION
        job.updated_at = datetime.now(timezone.utc)
        logger.info(f"[Pipeline] Running code generation for job {job_id}")
        
        result = await generator_service.run_full_pipeline(
            job_id=job_id,
            readme_content=readme_content,
            language=language,
            include_tests=True,
            include_deployment=True,
            include_docs=True,
            run_critique=True,
        )
        
        logger.info(f"[Pipeline] Full pipeline completed for job {job_id}: {result}")
        
        # Step 3: Update job status based on pipeline result with graceful degradation
        pipeline_status = result.get("status", "unknown") if result else "unknown"
        stages_completed = result.get("stages_completed", []) if result else []
        
        # NEW: Always set to COMPLETED if ANY code was generated (codegen stage succeeded)
        # This enables partial downloads and graceful degradation
        if "codegen" in stages_completed or pipeline_status == "completed":
            job.status = JobStatus.COMPLETED
            job.current_stage = JobStage.COMPLETED
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
            
            # Mark as partial completion if pipeline didn't fully complete
            is_partial = pipeline_status != "completed"
            job.metadata["partial_completion"] = is_partial
            job.metadata["completed_stages"] = stages_completed
            
            # Store pipeline results in metadata
            if result and isinstance(result, dict):
                output_path = result.get("output_path")
                if output_path:
                    job.metadata["output_path"] = output_path
                if result.get("message"):
                    job.metadata["pipeline_message"] = result.get("message")
            
            # Scan job directory for generated output files
            from pathlib import Path
            job_dir = Path(f"./uploads/{job_id}")
            if job_dir.exists():
                output_files = []
                for file_path in job_dir.rglob('*'):
                    if file_path.is_file():
                        # Store relative path from job directory
                        rel_path = str(file_path.relative_to(job_dir))
                        output_files.append(rel_path)
                job.output_files = output_files
                logger.info(
                    f"[FileDiscovery] Found {len(output_files)} output files for job {job_id} in {job_dir}"
                )
                if output_files:
                    # Log first few files for debugging
                    sample_files = output_files[:5]
                    logger.info(f"[FileDiscovery] Sample files: {', '.join(sample_files)}")
            else:
                logger.warning(
                    f"[FileDiscovery] Job directory {job_dir} does not exist - no output files found"
                )
            
            job.metadata["pipeline_completed_at"] = datetime.now(timezone.utc).isoformat()
            
            if is_partial:
                logger.info(
                    f"[Pipeline] Job {job_id} marked as COMPLETED with partial results "
                    f"(stages: {', '.join(stages_completed)})"
                )
            else:
                logger.info(f"[Pipeline] Job {job_id} marked as COMPLETED successfully")
            
        else:
            # No code generated at all - mark as failed
            job.status = JobStatus.FAILED
            job.updated_at = datetime.now(timezone.utc)
            job.metadata["error"] = result.get("message", "Code generation failed - no code produced")
            job.metadata["stages_completed"] = stages_completed
            logger.warning(
                f"[Pipeline] Job {job_id} marked as FAILED: no code was generated "
                f"(completed stages: {', '.join(stages_completed) if stages_completed else 'none'})"
            )
            
    except Exception as e:
        logger.error(f"[Pipeline] Critical error for job {job_id}: {e}", exc_info=True)
        
        # Update job status - try to mark as COMPLETED if files exist
        if job_id in jobs_db:
            job = jobs_db[job_id]
            
            # Check if code files were generated before the error
            from pathlib import Path
            job_dir = Path(f"./uploads/{job_id}")
            has_code_files = False
            
            if job_dir.exists():
                # Check for Python, JavaScript, TypeScript, Java, etc. code files
                code_extensions = ['.py', '.js', '.ts', '.tsx', '.jsx', '.java', '.go', '.rs', '.cpp', '.c', '.h']
                for ext in code_extensions:
                    if any(job_dir.rglob(f'*{ext}')):
                        has_code_files = True
                        break
            
            if has_code_files:
                # Mark as COMPLETED with partial results
                job.status = JobStatus.COMPLETED
                job.current_stage = JobStage.COMPLETED
                job.completed_at = datetime.now(timezone.utc)
                job.updated_at = datetime.now(timezone.utc)
                job.metadata["partial_completion"] = True
                job.metadata["error"] = str(e)
                job.metadata["error_type"] = type(e).__name__
                job.metadata["pipeline_failed_at"] = datetime.now(timezone.utc).isoformat()
                
                # Scan for output files
                output_files = []
                for file_path in job_dir.rglob('*'):
                    if file_path.is_file():
                        rel_path = str(file_path.relative_to(job_dir))
                        output_files.append(rel_path)
                job.output_files = output_files
                
                logger.info(
                    f"[Pipeline] Job {job_id} marked as COMPLETED despite error - "
                    f"code files were generated ({len(output_files)} files)"
                )
            else:
                # No code generated - mark as failed
                job.status = JobStatus.FAILED
                job.updated_at = datetime.now(timezone.utc)
                job.metadata["error"] = str(e)
                job.metadata["error_type"] = type(e).__name__
                job.metadata["pipeline_failed_at"] = datetime.now(timezone.utc).isoformat()
                logger.info(f"[Pipeline] Job {job_id} marked as FAILED due to critical error: {e}")


@router.post("/llm/configure")
async def configure_llm_provider(
    request: LLMConfigRequest,
    generator_service: GeneratorService = Depends(get_generator_service),
):
    """
    Configure LLM provider for generator.

    Switches between or configures LLM providers (OpenAI, Anthropic, Google, xAI, Ollama).

    **Request Body:**
    - provider: LLM provider to configure
    - api_key: API key (if required)
    - model: Specific model to use
    - config: Additional provider configuration

    **Returns:**
    - Configuration confirmation
    """
    result = await generator_service.configure_llm_provider(
        provider=request.provider.value,
        api_key=request.api_key,
        model=request.model,
        config=request.config,
    )

    logger.info(f"LLM provider configured: {request.provider.value}")
    return result


@router.get("/llm/status")
async def get_llm_status(
    generator_service: GeneratorService = Depends(get_generator_service),
):
    """
    Get status of configured LLM providers.

    Returns information about available and configured LLM providers.

    **Returns:**
    - LLM provider status including active provider and configurations
    """
    status = await generator_service.get_llm_provider_status()
    return status


@router.get("/audit/logs")
async def query_audit_logs(
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    event_type: Optional[str] = None,
    job_id: Optional[str] = None,
    limit: int = 100,
    generator_service: GeneratorService = Depends(get_generator_service),
):
    """
    Query generator audit logs.

    Retrieves audit trail from the generator module.

    **Query Parameters:**
    - start_time: Start timestamp (ISO 8601)
    - end_time: End timestamp (ISO 8601)
    - event_type: Filter by event type
    - job_id: Filter by job ID
    - limit: Maximum number of results (default: 100, max: 1000)

    **Returns:**
    - Audit log entries
    """
    result = await generator_service.query_audit_logs(
        start_time=start_time,
        end_time=end_time,
        event_type=event_type,
        job_id=job_id,
        limit=min(limit, 1000),
    )

    return result


@router.post("/{job_id}/upload", response_model=SuccessResponse)
async def upload_files(
    job_id: str,
    background_tasks: BackgroundTasks,
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
    After successful upload, automatically triggers the full generation pipeline.

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
    readme_content = ""

    for file in files:
        # Read file content
        content = await file.read()
        
        # Categorize file
        filename_lower = file.filename.lower()
        if filename_lower.endswith('.md'):
            readme_files.append(file.filename)
            # Store README content for pipeline trigger
            # Check if this is a README file using proper path handling
            base_name, _ = os.path.splitext(filename_lower)
            if not readme_content and 'readme' in base_name:
                try:
                    readme_content = content.decode('utf-8')
                except UnicodeDecodeError:
                    logger.warning(f"Could not decode {file.filename} as UTF-8")
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
    job.updated_at = datetime.now(timezone.utc)
    
    # Auto-trigger pipeline if README content is available
    if readme_content:
        logger.info(f"Extracted README for job {job_id}")
        logger.info(f"Auto-triggering full pipeline for job {job_id} after upload")
        background_tasks.add_task(
            _trigger_pipeline_background,
            job_id=job_id,
            readme_content=readme_content,
            generator_service=generator_service,
        )
    else:
        logger.warning(
            f"No README.md found in uploaded files for job {job_id}. "
            "Pipeline will not be auto-triggered."
        )

    logger.info(
        f"Uploaded {len(files)} files for job {job_id}: "
        f"{len(readme_files)} README, {len(test_files)} test, {len(other_files)} other"
    )

    return SuccessResponse(
        success=True,
        message=f"Uploaded {len(files)} files successfully. Pipeline auto-triggered." if readme_content else f"Uploaded {len(files)} files successfully.",
        data={
            "uploaded_files": uploaded_files,
            "categorization": {
                "readme_files": readme_files,
                "test_files": test_files,
                "other_files": other_files,
            },
            "pipeline_triggered": bool(readme_content),
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
    request: ClarifyRequest = None,
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

    **Request Body (optional):**
    - readme_content: README/requirements content (if not provided, reads from uploaded files)
    - ambiguities: Specific ambiguities to clarify
    - metadata: Additional metadata

    **Returns:**
    - Clarification initiation status and detected ambiguities

    **Errors:**
    - 400: No README content found or file read error
    - 404: Job not found
    """
    if job_id not in jobs_db:
        logger.warning(f"Clarify request for non-existent job: {job_id}")
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job = jobs_db[job_id]
    
    # Extract request parameters (with defaults for missing request)
    readme_content = (request.readme_content or "").strip() if request else ""
    ambiguities = request.ambiguities if request else None
    
    if readme_content:
        logger.info(f"Using README content from request body for job {job_id} (length={len(readme_content)})")
    
    # If no content in request body, try to read from uploaded files
    if not readme_content:
        upload_base_path = Path("./uploads")
        job_upload_path = upload_base_path / job_id
        
        # Check if upload directory exists
        if job_upload_path.exists():
            # Search for README files with detailed logging
            readme_candidates = []
            for filename in job.input_files:
                if filename.lower().endswith('.md'):
                    readme_candidates.append(filename)
                    file_path = job_upload_path / filename
                    
                    logger.debug(
                        "Checking README candidate: %s (file_path=%s, exists=%s, is_file=%s)",
                        filename,
                        str(file_path.absolute()),
                        file_path.exists(),
                        file_path.is_file() if file_path.exists() else False
                    )
                    
                    try:
                        # Validate file exists and is readable
                        if not file_path.exists():
                            logger.warning(f"README file does not exist: {file_path.absolute()}")
                            continue
                            
                        if not file_path.is_file():
                            logger.warning(f"README path is not a file: {file_path.absolute()}")
                            continue
                        
                        # Read file with proper encoding handling
                        with open(file_path, 'r', encoding='utf-8') as f:
                            readme_content = f.read()
                            
                        if readme_content.strip():
                            logger.info(
                                "Successfully read README for job %s: %s (content_length=%d, file_path=%s)",
                                job_id,
                                filename,
                                len(readme_content),
                                str(file_path.absolute())
                            )
                            break
                        else:
                            logger.warning(f"README file is empty: {filename}")
                            
                    except UnicodeDecodeError as e:
                        logger.error(
                            "Encoding error reading file %s for job %s: %s (file_path=%s)",
                            filename,
                            job_id,
                            str(e),
                            str(file_path.absolute())
                        )
                        # Try with different encoding
                        try:
                            with open(file_path, 'r', encoding='latin-1') as f:
                                readme_content = f.read()
                            logger.info(f"Successfully read {filename} with latin-1 encoding")
                            if readme_content.strip():
                                break
                        except Exception as e2:
                            logger.error(f"Failed to read {filename} with fallback encoding: {e2}")
                    except PermissionError as e:
                        logger.error(
                            "Permission denied reading file %s for job %s: %s (file_path=%s)",
                            filename,
                            job_id,
                            str(e),
                            str(file_path.absolute())
                        )
                    except Exception as e:
                        logger.error(
                            "Unexpected error reading file %s for job %s: %s (file_path=%s)",
                            filename,
                            job_id,
                            str(e),
                            str(file_path.absolute()),
                            exc_info=True
                        )

    # Validate README content was found
    if not readme_content or not readme_content.strip():
        error_detail = {
            "message": "No README content found for clarification. Please provide readme_content in request body or upload .md files first.",
            "job_id": job_id,
            "hint": "Include 'readme_content' in the request body with your requirements text",
        }
        logger.error(
            "No valid README content found for job %s: %s",
            job_id,
            error_detail,
            extra={"error_detail": error_detail}
        )
        raise HTTPException(
            status_code=400,
            detail=error_detail
        )

    # Process clarification request
    try:
        result = await generator_service.clarify_requirements(
            job_id=job_id,
            readme_content=readme_content,
            ambiguities=ambiguities,
        )
        
        # Check if questions were generated and return proper response
        questions = result.get("clarifications", [])
        questions_count = result.get("questions_count", len(questions))
        
        if questions and questions_count > 0:
            # Questions generated - return them for UI display
            logger.info(
                f"Clarification initiated with {questions_count} questions for job {job_id}",
                extra={"result_keys": list(result.keys()) if isinstance(result, dict) else None}
            )
            return {
                "status": "questions_generated",
                "job_id": job_id,
                "clarifications": questions,
                "total_questions": questions_count,
                "method": result.get("method", "rule_based"),
            }
        else:
            # No questions - requirements are clear
            logger.info(
                f"No clarification needed for job {job_id} - requirements are clear",
                extra={"result_keys": list(result.keys()) if isinstance(result, dict) else None}
            )
            return {
                "status": "no_clarification_needed",
                "job_id": job_id,
                "message": "No ambiguities detected - requirements are clear",
            }
    except Exception as e:
        logger.error(
            f"Error processing clarification for job {job_id}: {e}",
            exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process clarification: {str(e)}"
        )


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


@router.post("/{job_id}/codegen")
async def run_codegen(
    job_id: str,
    request: CodegenRequest,
    generator_service: GeneratorService = Depends(get_generator_service),
):
    """
    Run the code generation agent directly.

    Triggers the codegen agent to generate source code from requirements
    via OmniCore message bus routing.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Request Body:**
    - requirements: Natural language requirements
    - language: Target programming language
    - framework: Optional framework specification
    - include_tests: Whether to generate tests alongside code
    - metadata: Additional metadata

    **Returns:**
    - Code generation results with output paths

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    result = await generator_service.run_codegen_agent(
        job_id=job_id,
        requirements=request.requirements,
        language=request.language.value,
        framework=request.framework,
    )

    logger.info(f"Codegen agent executed for job {job_id}")
    return result


@router.post("/{job_id}/testgen")
async def run_testgen(
    job_id: str,
    request: TestgenRequest,
    generator_service: GeneratorService = Depends(get_generator_service),
):
    """
    Run the test generation agent.

    Triggers the testgen agent to create comprehensive tests for generated code.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Request Body:**
    - code_path: Path to code files to test
    - test_type: Type of tests (unit, integration, e2e)
    - coverage_target: Target code coverage percentage
    - metadata: Additional metadata

    **Returns:**
    - Test generation results

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    result = await generator_service.run_testgen_agent(
        job_id=job_id,
        code_path=request.code_path,
        test_type=request.test_type,
        coverage_target=request.coverage_target,
    )

    logger.info(f"Testgen agent executed for job {job_id}")
    return result


@router.post("/{job_id}/deploy")
async def run_deploy(
    job_id: str,
    request: DeployRequest,
    generator_service: GeneratorService = Depends(get_generator_service),
):
    """
    Run the deployment configuration generation agent.

    Generates Docker, Kubernetes, or cloud platform deployment configurations.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Request Body:**
    - code_path: Path to application code
    - platform: Deployment platform (docker, kubernetes, aws)
    - include_ci_cd: Whether to include CI/CD configuration
    - metadata: Additional metadata

    **Returns:**
    - Deployment configuration results

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    result = await generator_service.run_deploy_agent(
        job_id=job_id,
        code_path=request.code_path,
        platform=request.platform,
        include_ci_cd=request.include_ci_cd,
    )

    logger.info(f"Deploy agent executed for job {job_id}")
    return result


@router.post("/{job_id}/docgen")
async def run_docgen(
    job_id: str,
    request: DocgenRequest,
    generator_service: GeneratorService = Depends(get_generator_service),
):
    """
    Run the documentation generation agent.

    Generates API documentation, user guides, or developer documentation.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Request Body:**
    - code_path: Path to code to document
    - doc_type: Documentation type (api, user, developer)
    - format: Output format (markdown, html, pdf)
    - metadata: Additional metadata

    **Returns:**
    - Documentation generation results

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    result = await generator_service.run_docgen_agent(
        job_id=job_id,
        code_path=request.code_path,
        doc_type=request.doc_type,
        format=request.format,
    )

    logger.info(f"Docgen agent executed for job {job_id}")
    return result


@router.post("/{job_id}/critique")
async def run_critique(
    job_id: str,
    request: CritiqueRequest,
    generator_service: GeneratorService = Depends(get_generator_service),
):
    """
    Run the critique agent for security and quality scanning.

    Performs security scanning, code quality analysis, and performance checks.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Request Body:**
    - code_path: Path to code to analyze
    - scan_types: Types of scans (security, quality, performance)
    - auto_fix: Whether to automatically apply fixes
    - metadata: Additional metadata

    **Returns:**
    - Critique analysis results

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    result = await generator_service.run_critique_agent(
        job_id=job_id,
        code_path=request.code_path,
        scan_types=request.scan_types,
        auto_fix=request.auto_fix,
    )

    logger.info(f"Critique agent executed for job {job_id}")
    return result


@router.post("/{job_id}/pipeline")
async def run_full_pipeline(
    job_id: str,
    request: PipelineRequest,
    generator_service: GeneratorService = Depends(get_generator_service),
):
    """
    Run the full generation pipeline.

    Orchestrates the complete generation workflow: clarify → codegen → testgen → deploy → docgen → critique.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Request Body:**
    - readme_content: README/requirements content
    - language: Target programming language
    - include_tests: Whether to generate tests
    - include_deployment: Whether to generate deployment configs
    - include_docs: Whether to generate documentation
    - run_critique: Whether to run security/quality checks
    - metadata: Additional metadata

    **Returns:**
    - Full pipeline execution results

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    result = await generator_service.run_full_pipeline(
        job_id=job_id,
        readme_content=request.readme_content,
        language=request.language.value,
        include_tests=request.include_tests,
        include_deployment=request.include_deployment,
        include_docs=request.include_docs,
        run_critique=request.run_critique,
    )

    logger.info(f"Full pipeline executed for job {job_id}")
    return result


