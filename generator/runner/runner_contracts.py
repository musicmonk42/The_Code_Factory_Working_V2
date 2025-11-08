# runner/contracts.py
# World-class, gold-standard schemas for test execution tasks.
# This module defines the data contracts (Pydantic models) only.
# It is designed to be pure, dependency-light, and universally importable.

import uuid
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

# Define the current schema version for these contracts.
# This constant lives here as it's directly tied to the model's definition.
CURRENT_SCHEMA_VERSION = 2

class TaskPayload(BaseModel):
    """
    Schema for a test execution task payload.
    This model defines the structure and basic validation rules for incoming tasks.
    """
    # Core Task Fields
    test_files: Dict[str, str] = Field(..., description="Dictionary of test file paths to their content.")
    code_files: Dict[str, str] = Field(..., description="Dictionary of code file paths to their content.")
    output_path: str = Field(..., description="Local path where results should be stored.")
    
    # Execution Control Fields
    timeout: Optional[int] = Field(None, description="Override default execution timeout in seconds.")
    dry_run: bool = Field(False, description="If true, simulate the test run without actual execution.")
    priority: int = Field(0, description="Priority of the task (lower number indicates higher priority).")
    
    # Identification and Metadata
    task_id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier for the task. Auto-generated if not provided.")
    tags: List[str] = Field(default_factory=list, description="Metadata tags for querying and categorization.")
    environment: str = Field("production", description="The execution environment associated with this task (e.g., 'dev', 'staging', 'production').")

    # Schema Versioning for Data Compatibility
    schema_version: int = Field(CURRENT_SCHEMA_VERSION, description="Schema version of this TaskPayload. Used for external migration logic.")

    # Note: Sensitive data encryption, digital signatures, file size validation, and
    # allowed extensions should be handled at the *API/Service layer* before
    # model instantiation, or as part of data persistence/transfer logic.
    # This model defines the structure, not the operational security or transport.

class TaskResult(BaseModel):
    """
    Schema for the result of a test execution task.
    This model defines the structure for output data from a task.
    """
    # Core Result Fields
    task_id: str = Field(..., description="Unique identifier of the completed task.")
    status: str = Field(..., description="Overall status of the task ('completed', 'enqueued', 'failed', 'timed_out').")
    
    # Detailed Results
    results: Optional[Dict[str, Any]] = Field(None, description="Detailed test results and metrics from execution.")
    error: Optional[Dict[str, Any]] = Field(None, description="Structured error information if the task failed.")
    
    # Timestamps
    started_at: Optional[float] = Field(None, description="Unix epoch timestamp when the task started processing.")
    finished_at: Optional[float] = Field(None, description="Unix epoch timestamp when the task finished processing.")
    
    # Metadata (copied from TaskPayload or derived)
    tags: List[str] = Field(default_factory=list, description="Metadata tags inherited from the task payload.")
    environment: str = Field("production", description="Execution environment where the task was processed.")

    # Schema Versioning
    schema_version: int = Field(CURRENT_SCHEMA_VERSION, description="Schema version of this TaskResult. Used for external migration logic.")

    # Note: The 'status' validation is a simple enum check, suitable for a contracts file.
    # More complex business logic validation would typically be in service layer.
    
class BatchTaskPayload(BaseModel):
    """
    Schema for a batch of test execution tasks.
    This groups multiple TaskPayloads for efficient submission.
    """
    tasks: List[TaskPayload] = Field(..., description="A list of TaskPayload objects within this batch.")
    batch_id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier for the batch. Auto-generated if not provided.")

    # Schema Versioning
    schema_version: int = Field(CURRENT_SCHEMA_VERSION, description="Schema version of this BatchTaskPayload.")

    # Note: Validation for unique task IDs within a batch should be handled at the
    # service layer where the batch is processed, not strictly within the Pydantic model
    # definition itself, to keep models pure. Pydantic's List validation will ensure
    # all items are TaskPayloads.