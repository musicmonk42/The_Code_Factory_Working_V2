# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Fix-related schemas for error handling and corrections.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class FixStatus(str, Enum):
    """Status of a fix."""

    PROPOSED = "proposed"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class FixProposal(BaseModel):
    """A proposed fix for an error."""

    fix_id: str = Field(..., description="Unique fix identifier")
    error_id: str = Field(..., description="Associated error identifier")
    job_id: Optional[str] = Field(None, description="Associated job ID")
    description: str = Field(..., description="Fix description")
    proposed_changes: List[Dict[str, Any]] = Field(
        ..., description="List of proposed changes"
    )
    confidence: float = Field(
        0.0, ge=0.0, le=1.0, description="Fix confidence score (0-1)"
    )
    reasoning: Optional[str] = Field(None, description="Reasoning behind the fix")
    created_at: datetime = Field(..., description="Proposal creation timestamp")


class Fix(BaseModel):
    """Complete fix information including status and history."""

    fix_id: str = Field(..., description="Unique fix identifier")
    error_id: str = Field(..., description="Associated error identifier")
    job_id: Optional[str] = Field(None, description="Associated job ID")
    status: FixStatus = Field(..., description="Current fix status")
    description: str = Field(..., description="Fix description")
    proposed_changes: List[Dict[str, Any]] = Field(
        ..., description="List of proposed changes"
    )
    applied_changes: Optional[List[Dict[str, Any]]] = Field(
        None, description="Changes that were actually applied"
    )
    confidence: float = Field(
        0.0, ge=0.0, le=1.0, description="Fix confidence score (0-1)"
    )
    reasoning: Optional[str] = Field(None, description="Reasoning behind the fix")
    created_at: datetime = Field(..., description="Fix creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    applied_at: Optional[datetime] = Field(None, description="Application timestamp")
    rolled_back_at: Optional[datetime] = Field(None, description="Rollback timestamp")


class FixReviewRequest(BaseModel):
    """Request to review a fix."""

    approved: bool = Field(..., description="Whether the fix is approved")
    comments: Optional[str] = Field(None, description="Review comments")


class FixApplyRequest(BaseModel):
    """Request to apply a fix."""

    force: bool = Field(
        False, description="Force application even if conditions aren't met"
    )
    dry_run: bool = Field(
        False, description="Simulate application without making changes"
    )


class RollbackRequest(BaseModel):
    """Request to rollback a fix."""

    reason: Optional[str] = Field(None, description="Reason for rollback")
