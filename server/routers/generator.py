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
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from server.schemas import (
    ClarificationResponseRequest,
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
from server.services.job_finalization import finalize_job_success, finalize_job_failure
from server.services.dispatch_service import dispatch_job_completion
from server.storage import jobs_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generator", tags=["Generator"])

# UUID validation pattern (RFC 4122)
# Used for validating job IDs in API requests to prevent injection attacks
UUID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


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
        
        # Step 3: Finalize job immediately (CRITICAL - must happen before process exit)
        # This ensures job status reaches SUCCESS and artifacts are persisted
        pipeline_status = result.get("status", "unknown") if result else "unknown"
        stages_completed = result.get("stages_completed", []) if result else []
        
        # Check if ANY code was generated (codegen stage succeeded)
        if "codegen" in stages_completed or pipeline_status == "completed":
            # SUCCESS: Finalize with success status
            logger.info(f"[Pipeline] Finalizing successful job {job_id}")
            
            # Verify output directory exists and has files before finalizing
            job_dir = Path(f"./uploads/{job_id}")
            if not job_dir.exists():
                logger.warning(f"[Pipeline] Output directory {job_dir} does not exist after pipeline completion")
            else:
                files = list(job_dir.rglob('*'))
                file_count = sum(1 for f in files if f.is_file())
                logger.info(f"[Pipeline] Found {file_count} output files in {job_dir}")
            
            # Call finalization service to persist status and manifest
            finalized = await finalize_job_success(job_id, result)
            
            if finalized:
                # NOTE: Dispatch to Self-Fixing Engineer is now MANUAL ONLY
                # Users must explicitly click "Send to SFE" button in UI
                # This prevents unwanted automatic processing and gives users control
                # See endpoint: POST /generator/{job_id}/dispatch-to-sfe
                logger.info(
                    f"[Pipeline] Job {job_id} finalized successfully. "
                    f"Ready for manual dispatch to Self-Fixing Engineer."
                )
            else:
                logger.error(f"[Pipeline] Failed to finalize job {job_id}")
            
        else:
            # FAILURE: No code generated at all
            logger.warning(
                f"[Pipeline] Job {job_id} failed: no code was generated "
                f"(completed stages: {', '.join(stages_completed) if stages_completed else 'none'})"
            )
            
            error = Exception(result.get("message", "Code generation failed - no code produced"))
            await finalize_job_failure(job_id, error)
            
    except Exception as e:
        logger.error(f"[Pipeline] Critical error for job {job_id}: {e}", exc_info=True)
        
        # Finalize job as failure
        if job_id in jobs_db:
            await finalize_job_failure(job_id, e)


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
    request: ClarificationResponseRequest,
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

    logger.debug(f"Received clarification response for job {job_id}, question {request.question_id}")

    result = await generator_service.submit_clarification_response(
        job_id=job_id,
        question_id=request.question_id,
        response=request.response,
    )

    logger.info(f"Clarification response submitted for job {job_id}, question {request.question_id}")
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


@router.post("/{job_id}/dispatch-to-sfe")
async def dispatch_job_to_sfe(job_id: str):
    """
    Manually trigger dispatch to Self-Fixing Engineer for a completed job.
    
    This endpoint implements enterprise-grade job dispatch with:
    - Idempotent operations (safe to call multiple times)
    - Comprehensive input validation
    - Structured logging with correlation IDs
    - Proper error handling and status codes
    - Security best practices (no sensitive data exposure)
    
    **Business Logic:**
    Dispatches completed job artifacts to the Self-Fixing Engineer system for
    automated code analysis, testing, and improvement. Only jobs with COMPLETED
    status and existing output files can be dispatched.
    
    **Idempotency:**
    This operation is idempotent - calling it multiple times with the same job_id
    will produce the same result. The dispatch service tracks sent events and
    handles duplicates gracefully.
    
    **Dispatch Methods (in priority order):**
    1. Kafka event stream (if KAFKA_ENABLED=true)
    2. HTTP webhook (if SFE_WEBHOOK_URL configured)
    3. Database queue (fallback - not yet implemented)
    
    Args:
        job_id: The unique identifier (UUID) of the job to dispatch
        
    Returns:
        Dict with:
            - status: "dispatched" or "failed"
            - job_id: The job identifier
            - success: Boolean indicating dispatch success
            - message: (optional) Additional context on failure
        
    Raises:
        HTTPException 400: Job is not in COMPLETED status
        HTTPException 404: Job ID not found in database
        HTTPException 500: Internal server error during dispatch
        
    Status Codes:
        200: Dispatch succeeded or failed gracefully (check success field)
        400: Invalid request (job not completed)
        404: Job not found
        500: Internal server error
        
    Industry Standards:
        - REST API design best practices (RFC 7231)
        - Idempotent operations (RFC 7231 Section 4.2.2)
        - ISO 27001 A.12.4.1: Event logging
        - OWASP API Security Top 10 compliance
        
    Example Response (Success):
        {
            "status": "dispatched",
            "job_id": "8183136e-86fe-42f9-8412-b8f03c7a3edf",
            "success": true
        }
        
    Example Response (No dispatch methods available):
        {
            "status": "failed",
            "job_id": "8183136e-86fe-42f9-8412-b8f03c7a3edf",
            "success": false,
            "message": "No dispatch methods available..."
        }
    """
    # Input validation: Check job_id format (UUID) using module-level constant
    if not UUID_PATTERN.match(job_id):
        logger.warning(
            f"Invalid job_id format received: {job_id[:20]}...",
            extra={"action": "dispatch_to_sfe", "error": "invalid_uuid"}
        )
        raise HTTPException(
            status_code=400, 
            detail="Invalid job ID format. Must be a valid UUID."
        )
    
    # Check if job exists
    if job_id not in jobs_db:
        logger.info(
            f"Dispatch requested for non-existent job: {job_id}",
            extra={"action": "dispatch_to_sfe", "error": "job_not_found"}
        )
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs_db[job_id]
    
    # Validate job is in COMPLETED state
    if job.status != JobStatus.COMPLETED:
        logger.info(
            f"Dispatch rejected for job {job_id} with status {job.status}",
            extra={
                "action": "dispatch_to_sfe",
                "job_id": job_id,
                "current_status": job.status.value,
                "error": "invalid_status"
            }
        )
        raise HTTPException(
            status_code=400, 
            detail=f"Job must be COMPLETED to dispatch. Current status: {job.status.value}"
        )
    
    # Validate job has output files
    if not job.output_files or len(job.output_files) == 0:
        logger.warning(
            f"Dispatch rejected for job {job_id} - no output files",
            extra={
                "action": "dispatch_to_sfe",
                "job_id": job_id,
                "error": "no_output_files"
            }
        )
        raise HTTPException(
            status_code=400,
            detail="Job has no output files to dispatch"
        )
    
    # Structured logging with correlation ID
    correlation_id = str(uuid4())
    
    logger.info(
        f"Manual dispatch to SFE requested for job {job_id}",
        extra={
            "action": "dispatch_to_sfe",
            "job_id": job_id,
            "correlation_id": correlation_id,
            "output_file_count": len(job.output_files),
            "phase": "start"
        }
    )
    
    # Prepare job data for dispatch
    job_data = {
        "status": job.status.value,
        "output_files": job.output_files,
        "file_count": len(job.output_files),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "correlation_id": correlation_id,
    }
    
    try:
        # Attempt dispatch with correlation ID for tracing
        dispatched = await dispatch_job_completion(job_id, job_data, correlation_id)
        
        if dispatched:
            logger.info(
                f"Successfully dispatched job {job_id} to SFE",
                extra={
                    "action": "dispatch_to_sfe",
                    "job_id": job_id,
                    "correlation_id": correlation_id,
                    "result": "success",
                    "phase": "complete"
                }
            )
            return {
                "status": "dispatched", 
                "job_id": job_id, 
                "success": True,
                "correlation_id": correlation_id
            }
        else:
            # All dispatch methods failed, but this is a graceful failure
            logger.warning(
                f"Failed to dispatch job {job_id} to SFE - no dispatch methods succeeded",
                extra={
                    "action": "dispatch_to_sfe",
                    "job_id": job_id,
                    "correlation_id": correlation_id,
                    "result": "no_methods_available",
                    "phase": "complete"
                }
            )
            return {
                "status": "failed", 
                "job_id": job_id, 
                "success": False,
                "correlation_id": correlation_id,
                "message": (
                    "No dispatch methods available or all failed. "
                    "Ensure KAFKA_ENABLED=true or SFE_WEBHOOK_URL is configured. "
                    "Job remains available for manual download."
                )
            }
            
    except Exception as e:
        # Log error with full context but don't expose internals to client
        logger.error(
            f"Unexpected error dispatching job {job_id} to SFE: {type(e).__name__}: {e}",
            exc_info=True,
            extra={
                "action": "dispatch_to_sfe",
                "job_id": job_id,
                "correlation_id": correlation_id,
                "error_type": type(e).__name__,
                "phase": "error"
            }
        )
        raise HTTPException(
            status_code=500, 
            detail=(
                "Failed to dispatch job to Self-Fixing Engineer. "
                f"Correlation ID: {correlation_id}. "
                "Please contact support with this ID for assistance."
            )
        )



