"""
Self-Fixing Engineer (SFE) endpoints.

Handles code analysis, error detection, fix proposals, and automated fixing.
"""

import logging
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from server.routers.jobs import jobs_db
from server.schemas import (
    Fix,
    FixApplyRequest,
    FixProposal,
    FixReviewRequest,
    FixStatus,
    RollbackRequest,
    SuccessResponse,
)
from server.services import SFEService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sfe", tags=["Self-Fixing Engineer"])

# In-memory fix storage (would be replaced with actual database)
fixes_db: dict = {}


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

    job = jobs_db[job_id]
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
    sfe_service: SFEService = Depends(get_sfe_service),
):
    """
    Get meta-learning insights from SFE.

    Returns insights from the meta-learning orchestrator about
    common patterns, success rates, and learned behaviors.

    **Returns:**
    - Learning insights
    """
    insights = await sfe_service.get_learning_insights()
    return insights
