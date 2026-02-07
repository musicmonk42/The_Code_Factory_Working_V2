# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# generator/runner/runner_contracts.py
# Defines the data structures (Pydantic models) for tasks and results.

import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class TaskPayload(BaseModel):
    """
    Data contract for submitting a new test/code execution task.
    """

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    test_files: Dict[str, str] = Field(
        ..., description="Filenames mapped to their string content for test files."
    )
    code_files: Dict[str, str] = Field(
        ..., description="Filenames mapped to their string content for source code."
    )
    output_path: str = Field(
        ..., description="The designated output directory for results."
    )

    # --- ADD THIS LINE ---
    command: Optional[List[str]] = Field(
        None, description="The command to execute in the backend."
    )

    timeout: Optional[int] = Field(
        None, description="Task-specific timeout in seconds."
    )
    dry_run: bool = Field(
        False, description="If true, backend should simulate execution."
    )
    priority: int = Field(
        0, description="Task priority (higher numbers = higher priority)."
    )
    tags: List[str] = Field(
        default_factory=list, description="Arbitrary tags for grouping/filtering."
    )
    environment: str = Field(
        "production",
        description="Execution environment (e.g., 'production', 'staging').",
    )
    schema_version: int = Field(2, description="The version of this payload schema.")

    @field_validator("output_path")
    @classmethod
    def validate_output_path(cls, v: str) -> str:
        """
        Ensure output_path is a meaningful, non-empty string.
        """
        if not v or not v.strip():
            raise ValueError("output_path must be a non-empty string.")
        return v

    @model_validator(mode="after")
    def validate_has_files(self):
        """
        Ensure the task is not an empty shell: require at least one of
        test_files or code_files to be populated.
        """
        if not self.test_files and not self.code_files:
            raise ValueError(
                "At least one of 'test_files' or 'code_files' must be provided."
            )
        return self


class TaskResult(BaseModel):
    """
    Data contract for the result of an executed task.
    """

    task_id: str
    status: str = Field(
        ...,
        description="Status of the task (e.g., 'completed', 'failed', 'enqueued', 'timeout').",
    )

    results: Optional[Dict[str, Any]] = Field(
        None, description="Structured results (e.g., pass/fail counts, coverage)."
    )
    error: Optional[Dict[str, Any]] = Field(
        None, description="Structured error information if status is 'failed'."
    )

    started_at: float = Field(default_factory=time.time)
    finished_at: Optional[float] = Field(
        None, description="Timestamp when the task finished processing."
    )

    tags: List[str] = Field(default_factory=list)

    # These fields might be in the 'results' dict, but adding them here based on test mocks
    pass_rate: Optional[float] = Field(
        None, description="Overall pass rate, if applicable."
    )
    coverage_percentage: Optional[float] = Field(
        None, description="Overall coverage, if applicable."
    )


class BatchTaskPayload(BaseModel):
    """
    Data contract for submitting a batch of tasks.
    """

    batch_id: str = Field(default_factory=lambda: f"batch_{uuid.uuid4()}")
    tasks: List[TaskPayload] = Field(..., min_length=1)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def validate_non_empty_tasks(self):
        """
        Ensure a batch has at least one task.
        """
        if not self.tasks:
            raise ValueError("BatchTaskPayload must contain at least one task.")
        return self
