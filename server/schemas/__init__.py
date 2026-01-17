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
    GeneratorStatus,
    Job,
    JobCreateRequest,
    JobListResponse,
    JobProgress,
    JobStage,
    JobStatus,
    LogEntry,
    LogsResponse,
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
    "GeneratorStatus",
    "Job",
    "JobCreateRequest",
    "JobListResponse",
    "JobProgress",
    "JobStage",
    "JobStatus",
    "LogEntry",
    "LogsResponse",
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
