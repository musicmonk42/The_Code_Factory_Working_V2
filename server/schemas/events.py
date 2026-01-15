"""
Event-related schemas for real-time updates.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Types of events that can be streamed."""

    JOB_CREATED = "job_created"
    JOB_UPDATED = "job_updated"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"
    ERROR_DETECTED = "error_detected"
    FIX_PROPOSED = "fix_proposed"
    FIX_APPLIED = "fix_applied"
    FIX_ROLLBACK = "fix_rollback"
    LOG_MESSAGE = "log_message"
    PLATFORM_STATUS = "platform_status"


class EventMessage(BaseModel):
    """Event message for real-time streaming."""

    event_type: EventType = Field(..., description="Type of event")
    timestamp: datetime = Field(..., description="Event timestamp")
    job_id: Optional[str] = Field(None, description="Associated job ID if applicable")
    message: str = Field(..., description="Event message")
    data: Dict[str, Any] = Field(
        default_factory=dict, description="Additional event data"
    )
    severity: str = Field(
        "info", description="Event severity (debug/info/warning/error/critical)"
    )
