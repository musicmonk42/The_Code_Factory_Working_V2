"""
Generator module endpoints.

Handles file uploads and generator-specific operations.
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from server.schemas import (
    CodegenRequest,
    CritiqueRequest,
    DeployRequest,
    DocgenRequest,
    GeneratorStatus,
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


