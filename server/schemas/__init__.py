"""
Pydantic schemas for API request/response models.
"""

from .common import (
    ErrorResponse,
    HealthResponse,
    PaginationParams,
    StatusResponse,
    SuccessResponse,
)
from .events import EventMessage, EventType
from .fixes import (
    Fix,
    FixApplyRequest,
    FixProposal,
    FixReviewRequest,
    FixStatus,
    RollbackRequest,
)
from .jobs import (
    Job,
    JobCreateRequest,
    JobListResponse,
    JobProgress,
    JobStage,
    JobStatus,
    StageProgress,
)

__all__ = [
    # Common
    "ErrorResponse",
    "HealthResponse",
    "PaginationParams",
    "StatusResponse",
    "SuccessResponse",
    # Jobs
    "Job",
    "JobCreateRequest",
    "JobListResponse",
    "JobProgress",
    "JobStage",
    "JobStatus",
    "StageProgress",
    # Events
    "EventMessage",
    "EventType",
    # Fixes
    "Fix",
    "FixApplyRequest",
    "FixProposal",
    "FixReviewRequest",
    "FixStatus",
    "RollbackRequest",
]
