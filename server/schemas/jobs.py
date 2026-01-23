"""
Job-related schemas for the API.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Job execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(str, Enum):
    """Pipeline stages for a job."""

    UPLOAD = "upload"
    GENERATOR_CLARIFICATION = "generator_clarification"
    GENERATOR_GENERATION = "generator_generation"
    OMNICORE_PROCESSING = "omnicore_processing"
    SFE_ANALYSIS = "sfe_analysis"
    SFE_FIXING = "sfe_fixing"
    COMPLETED = "completed"


class StageProgress(BaseModel):
    """Progress information for a specific stage."""

    stage: JobStage = Field(..., description="Stage identifier")
    status: JobStatus = Field(..., description="Stage execution status")
    progress_percent: float = Field(
        0.0, ge=0.0, le=100.0, description="Progress percentage (0-100)"
    )
    message: Optional[str] = Field(None, description="Current stage message")
    started_at: Optional[datetime] = Field(None, description="Stage start timestamp")
    completed_at: Optional[datetime] = Field(
        None, description="Stage completion timestamp"
    )
    error: Optional[str] = Field(None, description="Error message if stage failed")


class JobProgress(BaseModel):
    """Overall job progress with per-stage details."""

    job_id: str = Field(..., description="Unique job identifier")
    status: JobStatus = Field(..., description="Overall job status")
    current_stage: Optional[JobStage] = Field(None, description="Current active stage")
    overall_progress: float = Field(
        0.0, ge=0.0, le=100.0, description="Overall progress percentage"
    )
    stages: List[StageProgress] = Field(
        default_factory=list, description="Progress for each stage"
    )
    created_at: datetime = Field(..., description="Job creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class Job(BaseModel):
    """Complete job information."""

    id: str = Field(..., description="Unique job identifier")
    status: JobStatus = Field(..., description="Job execution status")
    current_stage: Optional[JobStage] = Field(None, description="Current active stage")
    input_files: List[str] = Field(
        default_factory=list, description="List of input file names"
    )
    output_files: List[str] = Field(
        default_factory=list, description="List of generated output file names"
    )
    created_at: datetime = Field(..., description="Job creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    completed_at: Optional[datetime] = Field(
        None, description="Job completion timestamp"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional job metadata"
    )


class JobCreateRequest(BaseModel):
    """Request to create a new job."""

    description: Optional[str] = Field(None, description="Optional job description")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional job metadata"
    )


class JobListResponse(BaseModel):
    """Response for listing jobs."""

    jobs: List[Job] = Field(..., description="List of jobs")
    total: int = Field(..., description="Total number of jobs")
    page: int = Field(..., description="Current page number")
    per_page: int = Field(..., description="Items per page")
    total_pages: int = Field(..., description="Total number of pages")


class GeneratorStatus(BaseModel):
    """Generator-specific status information."""

    job_id: str = Field(..., description="Unique job identifier")
    stage: str = Field(..., description="Current generation stage")
    progress_percent: float = Field(
        0.0, ge=0.0, le=100.0, description="Progress percentage"
    )
    status: str = Field(..., description="Current status")
    message: Optional[str] = Field(None, description="Status message")
    artifacts_generated: List[str] = Field(
        default_factory=list, description="List of generated artifacts"
    )
    updated_at: datetime = Field(..., description="Last update timestamp")


class LogEntry(BaseModel):
    """Single log entry."""

    timestamp: datetime = Field(..., description="Log timestamp")
    level: str = Field(..., description="Log level (INFO, WARNING, ERROR, etc.)")
    message: str = Field(..., description="Log message")
    source: Optional[str] = Field(None, description="Log source/module")


class LogsResponse(BaseModel):
    """Response for logs endpoint."""

    job_id: str = Field(..., description="Job identifier")
    logs: List[LogEntry] = Field(..., description="List of log entries")
    count: int = Field(..., description="Number of log entries returned")
