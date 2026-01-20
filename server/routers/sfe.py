"""
Self-Fixing Engineer (SFE) endpoints.

Handles code analysis, error detection, fix proposals, and automated fixing.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query

from server.schemas import (
    ArbiterControlRequest,
    ArenaCompetitionRequest,
    BugAnalysisRequest,
    BugDetectionRequest,
    BugPrioritizationRequest,
    CodebaseAnalysisRequest,
    ComplianceCheckRequest,
    Fix,
    FixApplyRequest,
    FixProposal,
    FixReviewRequest,
    FixStatus,
    ImportFixRequest,
    KnowledgeGraphQuery,
    KnowledgeGraphUpdate,
    RollbackRequest,
    SandboxExecutionRequest,
    SIEMConfigRequest,
    SuccessResponse,
)
from server.services import SFEService
from server.storage import fixes_db, jobs_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sfe", tags=["Self-Fixing Engineer"])


def get_sfe_service() -> SFEService:
    """Dependency for SFEService."""
    from server.routers.jobs import get_omnicore_service

    omnicore = get_omnicore_service()
    return SFEService(omnicore_service=omnicore)


@router.post("/{job_id}/analyze")
async def analyze_code(
    job_id: str,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Analyze code for potential issues.

    Runs the SFE codebase analyzer to detect errors, code smells,
    and potential improvements.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Returns:**
    - Analysis results with detected issues

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    jobs_db[job_id]
    code_path = f"./uploads/{job_id}"

    result = await sfe_service.analyze_code(job_id, code_path)
    return result


@router.get("/{job_id}/errors")
async def get_errors(
    job_id: str,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Get all detected errors for a job.

    Returns errors detected by the SFE bug manager during analysis.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Returns:**
    - List of detected errors with details

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    errors = await sfe_service.detect_errors(job_id)
    return {"job_id": job_id, "errors": errors, "count": len(errors)}


@router.post("/errors/{error_id}/propose-fix", response_model=FixProposal)
async def propose_fix(
    error_id: str,
    sfe_service: SFEService = Depends(get_sfe_service),
) -> FixProposal:
    """
    Propose a fix for a detected error.

    Uses Arbiter AI to analyze the error and propose an automated fix.

    **Path Parameters:**
    - error_id: Error identifier

    **Returns:**
    - Fix proposal with proposed changes

    **Errors:**
    - 404: Error not found
    """
    result = await sfe_service.propose_fix(error_id)

    # Store fix proposal
    fix = Fix(
        fix_id=result["fix_id"],
        error_id=error_id,
        job_id=result.get("job_id"),
        status=FixStatus.PROPOSED,
        description=result["description"],
        proposed_changes=result["proposed_changes"],
        confidence=result["confidence"],
        reasoning=result.get("reasoning"),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    fixes_db[fix.fix_id] = fix

    return FixProposal(
        fix_id=fix.fix_id,
        error_id=error_id,
        job_id=fix.job_id,
        description=fix.description,
        proposed_changes=fix.proposed_changes,
        confidence=fix.confidence,
        reasoning=fix.reasoning,
        created_at=fix.created_at,
    )


@router.get("/fixes/{fix_id}", response_model=Fix)
async def get_fix(fix_id: str) -> Fix:
    """
    Get details of a specific fix.

    **Path Parameters:**
    - fix_id: Fix identifier

    **Returns:**
    - Complete fix information

    **Errors:**
    - 404: Fix not found
    """
    if fix_id not in fixes_db:
        raise HTTPException(status_code=404, detail=f"Fix {fix_id} not found")

    return fixes_db[fix_id]


@router.post("/fixes/{fix_id}/review", response_model=Fix)
async def review_fix(
    fix_id: str,
    request: FixReviewRequest,
) -> Fix:
    """
    Review a proposed fix (approve or reject).

    **Path Parameters:**
    - fix_id: Fix identifier

    **Request Body:**
    - approved: Whether the fix is approved
    - comments: Optional review comments

    **Returns:**
    - Updated fix information

    **Errors:**
    - 404: Fix not found
    """
    if fix_id not in fixes_db:
        raise HTTPException(status_code=404, detail=f"Fix {fix_id} not found")

    fix = fixes_db[fix_id]

    if request.approved:
        fix.status = FixStatus.APPROVED
    else:
        fix.status = FixStatus.REJECTED

    fix.updated_at = datetime.utcnow()

    logger.info(f"Fix {fix_id} {'approved' if request.approved else 'rejected'}")

    return fix


@router.post("/fixes/{fix_id}/apply", response_model=SuccessResponse)
async def apply_fix(
    fix_id: str,
    request: FixApplyRequest,
    sfe_service: SFEService = Depends(get_sfe_service),
) -> SuccessResponse:
    """
    Apply an approved fix.

    Applies the fix to the codebase, optionally in dry-run mode.

    **Path Parameters:**
    - fix_id: Fix identifier

    **Request Body:**
    - force: Force application even if conditions aren't met
    - dry_run: Simulate application without making changes

    **Returns:**
    - Application result

    **Errors:**
    - 404: Fix not found
    - 400: Fix not approved or already applied
    """
    if fix_id not in fixes_db:
        raise HTTPException(status_code=404, detail=f"Fix {fix_id} not found")

    fix = fixes_db[fix_id]

    if not request.force and fix.status != FixStatus.APPROVED:
        raise HTTPException(
            status_code=400,
            detail=f"Fix {fix_id} is not approved (status: {fix.status.value})",
        )

    if fix.status == FixStatus.APPLIED and not request.dry_run:
        raise HTTPException(
            status_code=400,
            detail=f"Fix {fix_id} is already applied",
        )

    result = await sfe_service.apply_fix(fix_id, dry_run=request.dry_run)

    if not request.dry_run:
        fix.status = FixStatus.APPLIED
        fix.applied_at = datetime.utcnow()
        fix.applied_changes = result.get("files_modified", [])

    fix.updated_at = datetime.utcnow()

    logger.info(f"Applied fix {fix_id} (dry_run={request.dry_run})")

    return SuccessResponse(
        success=True,
        message=f"Fix {fix_id} {'simulated' if request.dry_run else 'applied'} successfully",
        data=result,
    )


@router.post("/fixes/{fix_id}/rollback", response_model=SuccessResponse)
async def rollback_fix(
    fix_id: str,
    request: RollbackRequest,
    sfe_service: SFEService = Depends(get_sfe_service),
) -> SuccessResponse:
    """
    Rollback an applied fix.

    Reverts changes made by a previously applied fix.

    **Path Parameters:**
    - fix_id: Fix identifier

    **Request Body:**
    - reason: Optional reason for rollback

    **Returns:**
    - Rollback result

    **Errors:**
    - 404: Fix not found
    - 400: Fix not applied
    """
    if fix_id not in fixes_db:
        raise HTTPException(status_code=404, detail=f"Fix {fix_id} not found")

    fix = fixes_db[fix_id]

    if fix.status != FixStatus.APPLIED:
        raise HTTPException(
            status_code=400,
            detail=f"Fix {fix_id} is not applied (status: {fix.status.value})",
        )

    result = await sfe_service.rollback_fix(fix_id)

    fix.status = FixStatus.ROLLED_BACK
    fix.rolled_back_at = datetime.utcnow()
    fix.updated_at = datetime.utcnow()

    logger.info(f"Rolled back fix {fix_id}")

    return SuccessResponse(
        success=True,
        message=f"Fix {fix_id} rolled back successfully",
        data=result,
    )


@router.get("/{job_id}/metrics")
async def get_sfe_metrics(
    job_id: str,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Get SFE metrics for a job.

    Returns metrics about errors detected, fixes proposed and applied.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Returns:**
    - SFE metrics

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    metrics = await sfe_service.get_sfe_metrics(job_id)
    return metrics


@router.get("/insights")
async def get_learning_insights(
    job_id: Optional[str] = Query(
        None, description="Optional job ID to filter insights"
    ),
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Get meta-learning insights from SFE.

    Returns insights from the meta-learning orchestrator about
    common patterns, success rates, and learned behaviors.

    **Query Parameters:**
    - job_id: Optional job ID to filter insights (if omitted, returns global insights)

    **Returns:**
    - Learning insights (global or job-specific)

    **Note:**
    This endpoint does not require job_id validation since it can return
    global insights across all jobs or filtered insights for a specific job.
    """
    insights = await sfe_service.get_learning_insights(job_id=job_id)
    return insights


@router.get("/{job_id}/status")
async def get_sfe_status(
    job_id: str,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Get detailed real-time status of SFE activities for a job.

    Provides comprehensive monitoring of what SFE is doing, including:
    - Current operations and progress
    - Recent activity history
    - Resource usage
    - Operation queue status

    Routes the request through OmniCore to the self_fixing_engineer module
    to get accurate real-time information about SFE's current state.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Returns:**
    - Detailed SFE status information

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    status = await sfe_service.get_sfe_status(job_id)
    return status


@router.get("/{job_id}/logs")
async def get_sfe_logs(
    job_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of log entries"),
    level: Optional[str] = Query(None, description="Filter by log level (ERROR, WARNING, INFO, DEBUG)"),
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Get real-time logs from SFE for a specific job.

    Retrieves logs from the self_fixing_engineer module via OmniCore,
    enabling real-time monitoring and debugging of SFE operations.

    **Path Parameters:**
    - job_id: Unique job identifier

    **Query Parameters:**
    - limit: Maximum number of log entries (default: 100, max: 1000)
    - level: Optional log level filter (ERROR, WARNING, INFO, DEBUG)

    **Returns:**
    - List of SFE log entries with timestamps and details

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    logs = await sfe_service.get_sfe_logs(job_id, limit=limit, level=level)
    return {"job_id": job_id, "logs": logs, "count": len(logs)}


@router.post("/{job_id}/interact")
async def interact_with_sfe(
    job_id: str,
    command: str,
    params: Dict[str, Any] = {},
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Send interactive commands to SFE for a job.

    Allows direct interaction with the self_fixing_engineer module through
    OmniCore's message bus. Supported commands include:
    - pause: Pause current SFE operations
    - resume: Resume paused operations
    - analyze_file: Request analysis of a specific file
    - reanalyze: Trigger complete reanalysis
    - adjust_priority: Adjust fix priority thresholds

    **Path Parameters:**
    - job_id: Unique job identifier

    **Request Body:**
    - command: Command to send to SFE
    - params: Command-specific parameters

    **Returns:**
    - Command execution result with status

    **Errors:**
    - 404: Job not found
    - 400: Invalid command or parameters
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Validate command
    valid_commands = ["pause", "resume", "analyze_file", "reanalyze", "adjust_priority"]
    if command not in valid_commands:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid command '{command}'. Valid commands: {', '.join(valid_commands)}"
        )

    result = await sfe_service.interact_with_sfe(job_id, command, params)

    logger.info(f"SFE command '{command}' executed for job {job_id}")
    return result


@router.post("/arbiter/control")
async def control_arbiter(
    request: ArbiterControlRequest,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Control Arbiter AI.

    Start, stop, pause, resume, or configure the Arbiter AI system.

    **Request Body:**
    - command: Command to execute (start, stop, pause, resume, configure, status)
    - job_id: Optional job ID
    - config: Optional configuration

    **Returns:**
    - Arbiter control result
    """
    result = await sfe_service.control_arbiter(
        command=request.command.value,
        job_id=request.job_id,
        config=request.config,
    )

    logger.info(f"Arbiter control command executed: {request.command.value}")
    return result


@router.post("/arena/compete")
async def trigger_arena_competition(
    request: ArenaCompetitionRequest,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Trigger arena agent competition.

    Initiates a competition between AI agents to solve a problem, with the best solution selected.

    **Request Body:**
    - problem_type: Type of problem (bug_fix, optimization, refactor)
    - code_path: Path to code for competition
    - agents: Optional specific agents to compete
    - rounds: Number of competition rounds
    - evaluation_criteria: Evaluation criteria

    **Returns:**
    - Competition results with winner
    """
    result = await sfe_service.trigger_arena_competition(
        problem_type=request.problem_type,
        code_path=request.code_path,
        agents=request.agents,
        rounds=request.rounds,
        evaluation_criteria=request.evaluation_criteria,
    )

    logger.info(f"Arena competition triggered for {request.problem_type}")
    return result


@router.post("/bugs/detect")
async def detect_bugs(
    request: BugDetectionRequest,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Detect bugs in code.

    Performs comprehensive bug detection using the Bug Manager.

    **Request Body:**
    - code_path: Path to code to analyze
    - scan_depth: Scan depth (quick, standard, deep)
    - include_potential: Include potential issues

    **Returns:**
    - Bug detection results
    """
    result = await sfe_service.detect_bugs(
        code_path=request.code_path,
        scan_depth=request.scan_depth,
        include_potential=request.include_potential,
    )

    logger.info(f"Bug detection completed for {request.code_path}")
    return result


@router.post("/bugs/{bug_id}/analyze")
async def analyze_bug(
    bug_id: str,
    request: BugAnalysisRequest,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Analyze a specific bug.

    Performs detailed analysis including root cause and fix suggestions.

    **Path Parameters:**
    - bug_id: Bug identifier

    **Request Body:**
    - include_root_cause: Perform root cause analysis
    - suggest_fixes: Generate fix suggestions

    **Returns:**
    - Bug analysis results
    """
    result = await sfe_service.analyze_bug(
        bug_id=bug_id,
        include_root_cause=request.include_root_cause,
        suggest_fixes=request.suggest_fixes,
    )

    logger.info(f"Bug {bug_id} analyzed")
    return result


@router.post("/{job_id}/bugs/prioritize")
async def prioritize_bugs(
    job_id: str,
    request: BugPrioritizationRequest,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Prioritize bugs for a job.

    Orders bugs by importance based on severity, impact, and effort.

    **Path Parameters:**
    - job_id: Job identifier

    **Request Body:**
    - criteria: Prioritization criteria

    **Returns:**
    - Prioritized bug list

    **Errors:**
    - 404: Job not found
    """
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    result = await sfe_service.prioritize_bugs(
        job_id=job_id,
        criteria=request.criteria,
    )

    logger.info(f"Bugs prioritized for job {job_id}")
    return result


@router.post("/codebase/analyze")
async def deep_analyze_codebase(
    request: CodebaseAnalysisRequest,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Perform deep codebase analysis.

    Comprehensive analysis of code structure, dependencies, complexity, and quality.

    **Request Body:**
    - code_path: Path to codebase
    - analysis_types: Types of analysis (structure, dependencies, complexity, quality)
    - generate_report: Generate detailed report

    **Returns:**
    - Analysis results
    """
    result = await sfe_service.deep_analyze_codebase(
        code_path=request.code_path,
        analysis_types=request.analysis_type,
        generate_report=request.generate_report,
    )

    logger.info(f"Deep codebase analysis completed for {request.code_path}")
    return result


@router.post("/knowledge-graph/query")
async def query_knowledge_graph(
    request: KnowledgeGraphQuery,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Query knowledge graph.

    Queries the SFE knowledge graph for entities, relationships, or patterns.

    **Request Body:**
    - query_type: Query type (entity, relationship, pattern)
    - query: Query string or pattern
    - depth: Traversal depth
    - limit: Maximum results

    **Returns:**
    - Query results
    """
    result = await sfe_service.query_knowledge_graph(
        query_type=request.query_type,
        query=request.query,
        depth=request.depth,
        limit=request.limit,
    )

    return result


@router.post("/knowledge-graph/update")
async def update_knowledge_graph(
    request: KnowledgeGraphUpdate,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Update knowledge graph.

    Adds, updates, or deletes entities in the knowledge graph.

    **Request Body:**
    - operation: Operation (add_node, add_edge, update, delete)
    - entity_type: Entity type
    - entity_data: Entity data

    **Returns:**
    - Update result
    """
    result = await sfe_service.update_knowledge_graph(
        operation=request.operation,
        entity_type=request.entity_type,
        entity_data=request.entity_data,
    )

    logger.info(f"Knowledge graph updated: {request.operation}")
    return result


@router.post("/sandbox/execute")
async def execute_in_sandbox(
    request: SandboxExecutionRequest,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Execute code in sandbox.

    Runs code in a secure sandboxed environment with resource limits.

    **Request Body:**
    - code: Code to execute
    - language: Programming language
    - timeout: Execution timeout (seconds)
    - resource_limits: Resource limits (memory, cpu)

    **Returns:**
    - Execution results
    """
    result = await sfe_service.execute_in_sandbox(
        code=request.code,
        language=request.language,
        timeout=request.timeout,
        resource_limits=request.resource_limits,
    )

    return result


@router.post("/compliance/check")
async def check_compliance(
    request: ComplianceCheckRequest,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Check compliance standards.

    Validates code against compliance standards (SOC2, HIPAA, GDPR, etc.).

    **Request Body:**
    - code_path: Path to code to check
    - standards: Compliance standards to check
    - generate_report: Generate compliance report

    **Returns:**
    - Compliance check results
    """
    result = await sfe_service.check_compliance(
        code_path=request.code_path,
        standards=request.standards,
        generate_report=request.generate_report,
    )

    logger.info(f"Compliance check completed for {request.code_path}")
    return result


@router.get("/dlt/audit")
async def query_dlt_audit(
    start_block: Optional[int] = None,
    end_block: Optional[int] = None,
    transaction_type: Optional[str] = None,
    limit: int = 100,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Query DLT/blockchain audit logs.

    Retrieves audit transactions from the distributed ledger.

    **Query Parameters:**
    - start_block: Starting block number
    - end_block: Ending block number
    - transaction_type: Filter by transaction type
    - limit: Maximum results

    **Returns:**
    - Audit transactions
    """
    result = await sfe_service.query_dlt_audit(
        start_block=start_block,
        end_block=end_block,
        transaction_type=transaction_type,
        limit=min(limit, 1000),
    )

    return result


@router.post("/siem/configure")
async def configure_siem(
    request: SIEMConfigRequest,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Configure SIEM integration.

    Sets up integration with SIEM systems (Splunk, QRadar, Sentinel).

    **Request Body:**
    - siem_type: SIEM type (splunk, qradar, sentinel)
    - endpoint: SIEM endpoint URL
    - api_key: SIEM API key
    - export_config: Export configuration

    **Returns:**
    - Configuration result
    """
    result = await sfe_service.configure_siem(
        siem_type=request.siem_type,
        endpoint=request.endpoint,
        api_key=request.api_key,
        export_config=request.export_config,
    )

    logger.info(f"SIEM integration configured: {request.siem_type}")
    return result


@router.get("/rl/environment/{environment_id}/status")
async def get_rl_environment_status(
    environment_id: str,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Get RL environment status.

    Returns status of reinforcement learning environment.

    **Path Parameters:**
    - environment_id: Environment identifier

    **Returns:**
    - Environment status and metrics
    """
    result = await sfe_service.get_rl_environment_status(environment_id)

    return result


@router.post("/imports/fix")
async def fix_imports(
    request: ImportFixRequest,
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Fix import issues.

    Automatically fixes import errors and style issues.

    **Request Body:**
    - code_path: Path to code with import issues
    - auto_install: Auto-install missing packages
    - fix_style: Fix import style issues

    **Returns:**
    - Import fix results
    """
    result = await sfe_service.fix_imports(
        code_path=request.code_path,
        auto_install=request.auto_install,
        fix_style=request.fix_style,
    )

    logger.info(f"Imports fixed for {request.code_path}")
    return result
