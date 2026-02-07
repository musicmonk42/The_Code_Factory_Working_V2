# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

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

    def to_json_dict(self) -> Dict[str, Any]:
        """
        Convert to JSON-serializable dictionary with proper datetime handling.
        
        This method handles Pydantic V2 compatibility and ensures datetime objects
        are properly serialized to ISO format strings for JSON transport.
        
        Returns:
            Dict with all datetime objects converted to ISO strings
        """
        # Use model_dump() for Pydantic V2, falling back to dict() for V1
        try:
            data = self.model_dump()
        except AttributeError:
            # Fallback for Pydantic V1
            data = self.dict()
        
        # Convert datetime to ISO string
        if isinstance(data.get('timestamp'), datetime):
            data['timestamp'] = data['timestamp'].isoformat()
        
        return data
