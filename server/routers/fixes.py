"""
Fix management endpoints.

Handles error detection, fix proposals, reviews, applications, and rollbacks.
Centralizes error and fix workflow across all modules through OmniCore.
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from server.schemas import Fix, FixStatus
from server.services import OmniCoreService
from server.storage import fixes_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fixes", tags=["Fixes"])


def get_omnicore_service() -> OmniCoreService:
    """Dependency for OmniCoreService."""
    return OmniCoreService()


@router.get("/", response_model=List[Fix])
async def list_fixes(
    job_id: Optional[str] = Query(None, description="Filter by job ID"),
    status: Optional[FixStatus] = Query(None, description="Filter by status"),
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
) -> List[Fix]:
    """
    List all fixes with optional filtering.

    Query fixes across all modules through OmniCore's centralized tracking.

    **Query Parameters:**
    - job_id: Filter fixes by job ID
    - status: Filter fixes by status

    **Returns:**
    - List of fixes matching the criteria
    """
    logger.debug("Listing fixes (job_id=%s, status=%s)", job_id, status)

    # Query through OmniCore for centralized fix tracking
    # In real implementation:
    # fixes = await omnicore_service.get_fixes(job_id=job_id, status=status)

    # Filter in-memory storage
    filtered = list(fixes_db.values())

    if job_id:
        filtered = [f for f in filtered if f.job_id == job_id]

    if status:
        filtered = [f for f in filtered if f.status == status]

    # Sort by created_at desc
    filtered.sort(key=lambda x: x.created_at, reverse=True)

    return filtered


@router.get("/{fix_id}", response_model=Fix)
async def get_fix_detail(
    fix_id: str,
    omnicore_service: OmniCoreService = Depends(get_omnicore_service),
) -> Fix:
    """
    Get detailed information about a specific fix.

    **Path Parameters:**
    - fix_id: Unique fix identifier

    **Returns:**
    - Complete fix information

    **Errors:**
    - 404: Fix not found
    """
    if fix_id not in fixes_db:
        # Try querying through OmniCore
        # fix = await omnicore_service.get_fix(fix_id)
        raise HTTPException(status_code=404, detail=f"Fix {fix_id} not found")

    return fixes_db[fix_id]
