# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Generator module endpoints.

Handles file uploads and generator-specific operations.

[GAP #9] Sensitive routes now protected by ArbiterPolicyMiddleware.
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from server.middleware import arbiter_policy_check
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

# Maximum number of concurrent pipeline tasks to prevent event loop saturation
# This limits the number of pipeline coroutines that can run simultaneously
# Tune this value based on your system resources and load requirements
MAX_CONCURRENT_PIPELINES = 10

# Semaphore to limit concurrent pipeline tasks
_pipeline_semaphore = asyncio.Semaphore(MAX_CONCURRENT_PIPELINES)

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


def _filter_empty_questions(questions: list) -> list:
    """
    Filter out clarification questions with empty or missing text.

    Handles both string questions and dict questions (with a 'question' or
    'text' key).  Returns only questions that have non-blank content.
    """
    filtered = []
    for q in questions:
        if isinstance(q, str):
            if q.strip():
                filtered.append(q)
        elif isinstance(q, dict):
            text = q.get("question") or q.get("text") or ""
            if text.strip():
                filtered.append(q)
        # Silently drop anything that is neither str nor dict
    return filtered


async def _run_pipeline_with_semaphore(
    job_id: str,
    readme_content: str,
    generator_service: GeneratorService,
) -> None:
    """
    Wrapper to run pipeline with semaphore to limit concurrent executions.
    
    This prevents unbounded pipeline tasks from saturating the event loop.
    When the semaphore limit is reached, new pipeline tasks will wait until
    a slot becomes available.
    
    Args:
        job_id: Job ID
        readme_content: Content of the README file
        generator_service: GeneratorService instance
    """
    try:
        # Try to acquire the semaphore
        if _pipeline_semaphore.locked():
            logger.warning(
                f"[Pipeline] Pipeline semaphore at capacity ({MAX_CONCURRENT_PIPELINES}). "
                f"Job {job_id} waiting for available slot..."
            )
        
        async with _pipeline_semaphore:
            logger.info(
                f"[Pipeline] Starting pipeline for job {job_id} "
                f"(active: {MAX_CONCURRENT_PIPELINES - _pipeline_semaphore._value})"
            )
            await _trigger_pipeline_background(job_id, readme_content, generator_service)
    except Exception as e:
        logger.error(f"[Pipeline] Uncaught error in pipeline wrapper for job {job_id}: {e}", exc_info=True)


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

            # Filter out empty/blank questions (Fix C: prevents blank Q2 bug)
            original_count = len(clarifications)
            clarifications = _filter_empty_questions(clarifications)
            if len(clarifications) < original_count:
                logger.warning(
                    f"[Pipeline] Dropped {original_count - len(clarifications)} empty "
                    f"clarification question(s) for job {job_id}"
                )

            questions_count = len(clarifications)
            
            if questions_count > 0:
                # Store clarification questions in job metadata for later retrieval
                job.metadata["clarification_questions"] = clarifications
                job.metadata["clarification_status"] = "pending_response"
                job.metadata["clarification_method"] = clarify_result.get("method", "rule_based")
                job.metadata["readme_content"] = readme_content
                job.metadata["language"] = language
                job.status = JobStatus.NEEDS_CLARIFICATION
                job.updated_at = datetime.now(timezone.utc)
                
                logger.info(
                    f"[Pipeline] Clarification generated {questions_count} questions for job {job_id}. "
                    f"Job is waiting for user responses via /generator/{job_id}/clarification/respond"
                )
                
                # Pause pipeline: wait for user to answer questions before continuing
                # The pipeline will resume via submit_clarification_response once all
                # questions are answered.
                return
                
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
        
        # Handle skipped pipelines (duplicate request detected by OmniCoreService)
        if pipeline_status == "skipped":
            logger.info(
                f"[Pipeline] Job {job_id} pipeline was skipped (already running). "
                f"Not finalizing - the original pipeline run will handle finalization."
            )
            return
        
        stages_completed = result.get("stages_completed", []) if result else []

        # Distinguish between CRITICAL and AUXILIARY stages
        # CRITICAL stages: codegen (always), testgen (if tests requested)
        # AUXILIARY stages: deploy, docgen, critique (non-blocking, can fail without failing the job)
        critical_stages = ["codegen"]  # codegen is always critical
        if include_tests:
            critical_stages.append("testgen")
        
        auxiliary_stages = []
        if include_deployment:
            auxiliary_stages.append("deploy")
        if include_docs:
            auxiliary_stages.append("docgen")
        if run_critique:
            auxiliary_stages.append("critique")

        # Check if ALL CRITICAL stages completed successfully
        # BUG FIX: Removed pipeline_status == "completed" short-circuit
        # We must verify actual stage completion, not just trust pipeline status
        all_critical_completed = all(stage in stages_completed for stage in critical_stages)

        # At minimum, codegen must have succeeded to finalize as success
        # BUG FIX: Removed pipeline_status == "completed" short-circuit
        codegen_succeeded = "codegen" in stages_completed
        
        # Identify any auxiliary stages that failed (for warning logging)
        failed_auxiliary = [s for s in auxiliary_stages if s not in stages_completed]

        # FIX: Only finalize as SUCCESS if codegen succeeded AND all CRITICAL stages completed
        # Auxiliary stage failures are logged as warnings but do NOT prevent success
        if codegen_succeeded and all_critical_completed:
            # SUCCESS: All critical stages completed - finalize with success status
            # Log warnings for any auxiliary stages that failed
            if failed_auxiliary:
                logger.warning(
                    f"[Pipeline] Job {job_id}: The following auxiliary stages did not complete "
                    f"but job will still be marked as SUCCESS: {', '.join(failed_auxiliary)}"
                )
            
            logger.info(
                f"[Pipeline] Finalizing successful job {job_id}. "
                f"Completed stages: {', '.join(stages_completed)}. "
                f"Critical stages: {', '.join(critical_stages)}"
            )

            # Verify output directory exists and has files before finalizing
            job_dir = Path(f"./uploads/{job_id}")
            if not job_dir.exists():
                logger.warning(f"[Pipeline] Output directory {job_dir} does not exist after pipeline completion")
                # Missing output directory is a FAILURE
                error = Exception(f"Output directory {job_dir} does not exist after pipeline completion")
                await finalize_job_failure(job_id, error)
            else:
                files = list(job_dir.rglob('*'))
                file_count = sum(1 for f in files if f.is_file())
                logger.info(f"[Pipeline] Found {file_count} output files in {job_dir}")

                # Check for minimum required files
                if file_count == 0:
                    logger.error(f"[Pipeline] No output files generated for job {job_id}")
                    error = Exception("No output files generated - code generation produced no files")
                    await finalize_job_failure(job_id, error)
                else:
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

        elif codegen_succeeded and not all_critical_completed:
            # FAILURE: Codegen succeeded but some CRITICAL stages failed
            # FIX: This is now treated as FAILURE only for critical stages
            failed_critical = [stage for stage in critical_stages if stage not in stages_completed]
            logger.error(
                f"[Pipeline] Job {job_id} FAILED: Codegen succeeded but the following "
                f"CRITICAL stages failed: {', '.join(failed_critical)}. "
                f"Completed stages: {', '.join(stages_completed)}"
            )

            # Mark job as FAILED when critical stages do not complete
            error_message = (
                f"Pipeline failed: {len(failed_critical)} critical stage(s) did not complete "
                f"({', '.join(failed_critical)}). Completed stages: {', '.join(stages_completed)}"
            )
            error = Exception(error_message)
            await finalize_job_failure(job_id, error)

        else:
            # FAILURE: No code generated at all
            logger.error(
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


async def _resume_pipeline_after_clarification(
    job_id: str,
    generator_service: GeneratorService,
    clarified_requirements: dict,
):
    """
    Resume the pipeline after all clarification questions have been answered.

    Continues from the code generation stage using the original README content
    supplemented with clarified requirements.

    Args:
        job_id: Job ID
        generator_service: GeneratorService instance
        clarified_requirements: Requirements refined from user answers
    """
    try:
        if job_id not in jobs_db:
            logger.error(f"[Pipeline] Job {job_id} not found for resumption")
            return

        job = jobs_db[job_id]

        readme_content = job.metadata.get("readme_content", "")
        language = job.metadata.get("language", "python")

        # If README content is missing from metadata, try to load it from disk
        if not readme_content or len(readme_content.strip()) == 0:
            logger.warning(
                f"[Pipeline] README content not found in metadata for job {job_id}. "
                f"Attempting to load from disk."
            )
            readme_content = await generator_service.get_readme_content(job_id)
            if not readme_content:
                error_msg = (
                    f"Cannot resume pipeline: README content not found in metadata "
                    f"or job directory for job {job_id}"
                )
                logger.error(f"[Pipeline] {error_msg}")
                job.status = JobStatus.FAILED
                job.metadata["error"] = error_msg
                job.updated_at = datetime.now(timezone.utc)
                return

        # Supplement README with clarified requirements
        if clarified_requirements:
            clarified = clarified_requirements.get("clarified_requirements", {})
            if clarified:
                supplement = "\n\n## Clarified Requirements\n"
                for key, value in clarified.items():
                    supplement += f"- **{key}**: {value}\n"
                readme_content = readme_content + supplement

        # Update job status to RUNNING and stage to code generation
        job.status = JobStatus.RUNNING
        job.current_stage = JobStage.GENERATOR_GENERATION
        job.metadata["clarification_status"] = "completed"
        job.updated_at = datetime.now(timezone.utc)
        logger.info(f"[Pipeline] Resuming code generation for job {job_id} after clarification")

        result = await generator_service.run_full_pipeline(
            job_id=job_id,
            readme_content=readme_content,
            language=language,
            include_tests=True,
            include_deployment=True,
            include_docs=True,
            run_critique=True,
            skip_clarification=True,
        )

        logger.info(f"[Pipeline] Full pipeline completed for job {job_id}: {result}")

        pipeline_status = result.get("status", "unknown") if result else "unknown"

        if pipeline_status == "skipped":
            logger.info(
                f"[Pipeline] Job {job_id} pipeline was skipped (already running). "
                f"Not finalizing - the original pipeline run will handle finalization."
            )
            return

        stages_completed = result.get("stages_completed", []) if result else []

        # Distinguish between CRITICAL and AUXILIARY stages
        # CRITICAL stages: codegen (always), testgen (if tests requested)
        # AUXILIARY stages: deploy, docgen, critique (non-blocking, can fail without failing the job)
        critical_stages = ["codegen"]  # codegen is always critical
        # In clarification flow, all stages are always requested (see lines 400-403)
        # Therefore, testgen is always a critical stage in this flow
        critical_stages.append("testgen")
        
        # In clarification flow, all auxiliary stages are always requested (see lines 400-403)
        auxiliary_stages = ["deploy", "docgen", "critique"]

        # Check if ALL CRITICAL stages completed
        # BUG FIX: Removed pipeline_status == "completed" short-circuit
        # We must verify actual stage completion, not just trust pipeline status
        all_critical_completed = all(stage in stages_completed for stage in critical_stages)

        # BUG FIX: Removed pipeline_status == "completed" short-circuit
        codegen_succeeded = "codegen" in stages_completed
        
        # Identify any auxiliary stages that failed (for warning logging)
        failed_auxiliary = [s for s in auxiliary_stages if s not in stages_completed]

        # FIX: Apply same logic as _trigger_pipeline_background
        # Only finalize as SUCCESS if ALL CRITICAL stages completed
        if codegen_succeeded and all_critical_completed:
            # Log warnings for any auxiliary stages that failed
            if failed_auxiliary:
                logger.warning(
                    f"[Pipeline] Job {job_id}: The following auxiliary stages did not complete "
                    f"but job will still be marked as SUCCESS: {', '.join(failed_auxiliary)}"
                )
            
            logger.info(f"[Pipeline] Finalizing successful job {job_id} after clarification")
            finalized = await finalize_job_success(job_id, result)
            if finalized:
                logger.info(
                    f"[Pipeline] Job {job_id} finalized successfully after clarification. "
                    f"Ready for manual dispatch to Self-Fixing Engineer."
                )
            else:
                logger.error(f"[Pipeline] Failed to finalize job {job_id}")
        elif codegen_succeeded and not all_critical_completed:
            # FAILURE: Some CRITICAL stages failed
            failed_critical = [stage for stage in critical_stages if stage not in stages_completed]
            logger.error(
                f"[Pipeline] Job {job_id} FAILED after clarification: Codegen succeeded but "
                f"the following CRITICAL stages failed: {', '.join(failed_critical)}"
            )
            error = Exception(
                f"Pipeline failed: {len(failed_critical)} critical stage(s) did not complete "
                f"({', '.join(failed_critical)})"
            )
            await finalize_job_failure(job_id, error)
        else:
            logger.error(
                f"[Pipeline] Job {job_id} failed after clarification: no code was generated "
                f"(completed stages: {', '.join(stages_completed) if stages_completed else 'none'})"
            )
            error = Exception(result.get("message", "Code generation failed - no code produced"))
            await finalize_job_failure(job_id, error)

    except Exception as e:
        logger.error(f"[Pipeline] Critical error resuming job {job_id}: {e}", exc_info=True)
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
            base_name, _ = os.path.splitext(filename_lower)
            is_readme_file = 'readme' in base_name
            
            # Prioritize files with 'readme' in name, but accept any .md if none found yet
            if not readme_content or is_readme_file:
                try:
                    readme_content = content.decode('utf-8')
                    if is_readme_file:
                        logger.info(f"Found explicit README file: {file.filename}")
                    else:
                        logger.info(f"Using {file.filename} as specification content (no explicit README.md found)")
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
        # Use asyncio.create_task instead of BackgroundTasks to prevent event loop blocking
        asyncio.create_task(
            _run_pipeline_with_semaphore(
                job_id=job_id,
                readme_content=readme_content,
                generator_service=generator_service,
            )
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
        # Extract channel from request if provided
        channel = request.channel.value if request and request.channel else None
        
        result = await generator_service.clarify_requirements(
            job_id=job_id,
            readme_content=readme_content,
            ambiguities=ambiguities,
            channel=channel,
        )
        
        # Check if questions were generated and return proper response
        questions = result.get("clarifications", [])

        # Filter out empty/blank questions (Fix C: prevents blank Q2 bug)
        original_count = len(questions)
        questions = _filter_empty_questions(questions)
        if len(questions) < original_count:
            logger.warning(
                f"Dropped {original_count - len(questions)} empty "
                f"clarification question(s) for job {job_id}"
            )

        questions_count = len(questions)
        
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
    background_tasks: BackgroundTasks,
    generator_service: GeneratorService = Depends(get_generator_service),
):
    """
    Submit a response to a clarification question, or skip all clarification.

    Allows users to provide answers to clarification questions through
    the API. The response is routed through OmniCore to the clarifier
    module for processing. When all questions have been answered (or skipped),
    the pipeline automatically resumes with code generation.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Request Body (one of):**
    - question_id + response: Answer a single question
    - responses: Bulk answers keyed by question ID
    - skip: If true, skip all remaining clarification questions

    **Returns:**
    - Response submission confirmation

    **Errors:**
    - 400: Invalid request (skip=false with no question_id/response)
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job = jobs_db[job_id]

    # Handle skip: mark clarification resolved and resume pipeline
    if request.skip:
        logger.info(f"[Pipeline] Clarification skipped for job {job_id}. Resuming pipeline.")
        job.metadata["clarification_status"] = "resolved"
        job.status = JobStatus.RUNNING
        job.updated_at = datetime.now(timezone.utc)

        background_tasks.add_task(
            _resume_pipeline_after_clarification,
            job_id=job_id,
            generator_service=generator_service,
            clarified_requirements={},
        )

        return {
            "job_id": job_id,
            "status": "skipped",
            "message": "Clarification skipped. Pipeline resuming.",
        }

    # Validate that either question_id+response or responses is provided
    if request.question_id and not request.response:
        raise HTTPException(
            status_code=400,
            detail="Must provide response when question_id is specified",
        )
    if not request.question_id and not request.responses:
        raise HTTPException(
            status_code=400,
            detail="Must provide question_id+response, responses, or skip=true",
        )

    # Initialize local answer tracking if not present
    if "clarification_answers" not in job.metadata:
        job.metadata["clarification_answers"] = {}

    # Build a map of question_id -> answer from this request
    answers_to_record: dict = {}
    if request.responses:
        # Bulk answers keyed by question ID
        answers_to_record = dict(request.responses)
    elif request.question_id:
        answers_to_record = {request.question_id: request.response or ""}

    logger.debug(
        f"Received clarification response for job {job_id}, "
        f"questions: {list(answers_to_record.keys())}"
    )

    # Record each answer via OmniCore and store locally
    last_result = {}
    for qid, answer in answers_to_record.items():
        result = await generator_service.submit_clarification_response(
            job_id=job_id,
            question_id=qid,
            response=answer,
        )
        job.metadata["clarification_answers"][qid] = answer
        last_result = result

    job.updated_at = datetime.now(timezone.utc)

    # Determine the total number of clarification questions
    questions = job.metadata.get("clarification_questions", [])
    total_questions = len(questions)
    answered_count = len(job.metadata["clarification_answers"])
    
    # Check if bulk responses were provided that cover all questions
    # This handles the case where the frontend sends all answers at once
    bulk_responses_cover_all = (
        request.responses
        and total_questions > 0
        and len(request.responses) >= total_questions
    )

    # Check if all questions are now answered (locally tracked) or
    # if OmniCore signalled completion or bulk responses cover all
    all_answered = (
        (total_questions > 0 and answered_count >= total_questions)
        or last_result.get("status") == "completed"
        or bulk_responses_cover_all
    )

    if all_answered:
        # Build clarified requirements from recorded answers
        clarified_requirements = last_result.get("clarified_requirements", {})
        if not clarified_requirements:
            clarified_requirements = {
                "clarified_requirements": dict(job.metadata["clarification_answers"])
            }
        logger.info(
            f"[Pipeline] All clarification questions answered for job {job_id} "
            f"({answered_count}/{total_questions}). Resuming pipeline."
        )
        job.metadata["clarification_status"] = "resolved"
        job.status = JobStatus.RUNNING

        background_tasks.add_task(
            _resume_pipeline_after_clarification,
            job_id=job_id,
            generator_service=generator_service,
            clarified_requirements=clarified_requirements,
        )

        return {
            "job_id": job_id,
            "status": "completed",
            "message": "All clarification questions answered. Pipeline resuming.",
            "answered": answered_count,
            "total": total_questions,
        }

    logger.info(
        f"Clarification response submitted for job {job_id} "
        f"({answered_count}/{total_questions} answered)"
    )
    return {
        "job_id": job_id,
        "status": "answer_recorded",
        "message": f"Answer recorded ({answered_count}/{total_questions}).",
        "answered": answered_count,
        "total": total_questions,
    }


@router.post("/{job_id}/codegen")
async def run_codegen(
    job_id: str,
    request: CodegenRequest,
    generator_service: GeneratorService = Depends(get_generator_service),
    policy: dict = Depends(arbiter_policy_check("codegen")),
):
    """
    Run the code generation agent directly.

    Triggers the codegen agent to generate source code from requirements
    via OmniCore message bus routing.
    
    [GAP #9] Protected by ArbiterPolicyMiddleware for governance.

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
    - 403: Policy denied
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
    policy: dict = Depends(arbiter_policy_check("deploy")),
):
    """
    Run the deployment configuration generation agent.

    Generates Docker, Kubernetes, or cloud platform deployment configurations.
    
    [GAP #9] Protected by ArbiterPolicyMiddleware - critical for deployment security.

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
    - 403: Policy denied
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

    # Check if the pipeline paused for clarification
    job = jobs_db[job_id]
    if job.status == JobStatus.NEEDS_CLARIFICATION:
        questions = job.metadata.get("clarification_questions", [])
        logger.info(
            f"Pipeline paused for clarification on job {job_id} "
            f"({len(questions)} questions)"
        )
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job_id,
                "status": "needs_clarification",
                "questions": questions,
                "message": "Pipeline paused — clarification required before code generation.",
            },
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



