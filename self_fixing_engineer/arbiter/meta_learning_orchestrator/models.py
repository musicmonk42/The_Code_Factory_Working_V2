import logging
from datetime import datetime, timezone

# --- Enums for Finite Fields ---
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    model_validator,
    ConfigDict,
    field_serializer,
)


class EventType(str, Enum):
    DECISION_MADE = "decision_made"
    FEEDBACK_RECEIVED = "feedback_received"
    ACTION_TAKEN = "action_taken"


class DeploymentStatus(str, Enum):
    PENDING = "pending"
    DEPLOYED = "deployed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


# --- Core Data Models ---
class LearningRecord(BaseModel):
    """
    Represents a single record of agent learning data.
    Uses enums for type safety and Pydantic's frozen config for immutability.
    """

    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    agent_id: str = Field(..., description="Unique identifier for the agent")
    session_id: str = Field(..., description="Session identifier for tracking")
    decision_trace: Dict[str, Any] = Field(
        ..., description="Trace of decision-making process"
    )
    user_feedback: Optional[str] = Field(None, description="Optional user feedback")
    event_type: EventType = Field(..., description="Type of event recorded")
    lineage_id: Optional[str] = Field(None, description="Lineage tracking identifier")

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=True,  # Serialize enums as their values
    )

    @field_serializer("*", when_used="json")
    def serialize_datetime(self, value: Any) -> Any:
        """Serialize datetime objects to ISO format strings."""
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class ModelVersion(BaseModel):
    """
    Represents a trained ML model version with metadata.
    Includes validation to ensure deployed models meet quality thresholds.
    """

    model_id: str = Field(..., description="Unique model identifier")
    version: str = Field(..., description="Model version string")
    training_timestamp: str = Field(..., description="When the model was trained")
    evaluation_metrics: Dict[str, float] = Field(
        ..., description="Model evaluation metrics"
    )
    deployment_status: DeploymentStatus = Field(
        default=DeploymentStatus.PENDING, description="Current deployment status"
    )
    deployment_timestamp: Optional[str] = Field(
        None, description="When model was deployed"
    )
    is_active: bool = Field(False, description="Whether model is currently active")
    retry_count: int = Field(
        default=0, ge=0, description="Number of deployment retries"
    )
    lineage_id: Optional[str] = Field(None, description="Data lineage identifier")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=True,  # Serialize enums as their values
    )

    @field_serializer("*", when_used="json")
    def serialize_datetime(self, value: Any) -> Any:
        """Serialize datetime objects to ISO format strings."""
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    @model_validator(mode="after")
    def validate_metrics_and_status(self) -> "ModelVersion":
        """
        Custom validation to ensure consistency between evaluation metrics and deployment status.
        For deployed models, they must be marked as active and meet performance thresholds.
        """
        if self.deployment_status == DeploymentStatus.DEPLOYED:
            # Check that deployed models are marked as active
            if not self.is_active:
                raise ValueError(
                    "A model with 'deployed' status must also be 'is_active: True'."
                )

            # Check for accuracy metric
            if "accuracy" not in self.evaluation_metrics:
                raise ValueError(
                    "Deployed models must have 'accuracy' in evaluation_metrics."
                )

            # Check accuracy threshold
            min_threshold = 0.8
            if self.evaluation_metrics["accuracy"] < min_threshold:
                raise ValueError(
                    f"Model accuracy ({self.evaluation_metrics['accuracy']}) is below deployment threshold of {min_threshold}."
                )
        return self


# --- Custom Exceptions ---
class DataIngestionError(Exception):
    """Raised when data ingestion fails due to invalid input or file corruption."""

    pass


class ModelDeploymentError(Exception):
    """Raised when a model deployment fails after all retries are exhausted."""

    pass


class LeaderElectionError(Exception):
    """Raised when leader election fails in distributed orchestrator setup."""

    pass


# Example usage and testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        logging.info("--- Testing LearningRecord ---")
        record = LearningRecord(
            agent_id="agent-001",
            session_id="session-xyz",
            decision_trace={"input": [1, 2], "output": 3},
            event_type=EventType.ACTION_TAKEN,
            user_feedback="positive",
        )
        logging.info(f"LearningRecord created: {record.model_dump_json(indent=2)}")

        # Test immutability
        try:
            record.agent_id = "new-agent"
        except ValidationError:
            logging.info("Caught expected error (immutability): Field is frozen")

        # Test invalid event type
        try:
            LearningRecord(
                agent_id="a",
                session_id="s",
                decision_trace={},
                event_type="invalid_event",
            )
        except ValidationError as e:
            logging.info(f"Caught expected error (invalid enum): {e}")

    except Exception as e:
        logging.error(f"Unexpected error: {e}")

    print("\n" + "=" * 50 + "\n")

    try:
        logging.info("--- Testing ModelVersion ---")
        model_v1 = ModelVersion(
            model_id="rl-policy-v1",
            version="1.0.0",
            training_timestamp=datetime.now(timezone.utc).isoformat(),
            evaluation_metrics={"accuracy": 0.92, "precision": 0.88},
        )
        logging.info(f"ModelVersion created: {model_v1.model_dump_json(indent=2)}")

        # Test deploying with high accuracy
        model_v1_deployed = ModelVersion(
            model_id="rl-policy-v1",
            version="1.0.0",
            training_timestamp=datetime.now(timezone.utc).isoformat(),
            evaluation_metrics={"accuracy": 0.92, "precision": 0.88},
            deployment_status=DeploymentStatus.DEPLOYED,
            is_active=True,
            deployment_timestamp=datetime.now(timezone.utc).isoformat(),
        )
        logging.info(
            f"ModelVersion deployed: {model_v1_deployed.model_dump_json(indent=2)}"
        )

        # Test missing accuracy for deployed model
        try:
            ModelVersion(
                model_id="v2",
                version="2.0.0",
                training_timestamp=datetime.now(timezone.utc).isoformat(),
                evaluation_metrics={"precision": 0.85},  # Missing accuracy
                deployment_status=DeploymentStatus.DEPLOYED,
                is_active=True,
            )
        except ValueError as e:
            logging.info(f"Caught expected error (missing accuracy): {e}")

        # Test low accuracy for deployed model
        try:
            ModelVersion(
                model_id="v3",
                version="3.0.0",
                training_timestamp=datetime.now(timezone.utc).isoformat(),
                evaluation_metrics={"accuracy": 0.70},  # Low accuracy
                deployment_status=DeploymentStatus.DEPLOYED,
                is_active=True,
            )
        except ValueError as e:
            logging.info(f"Caught expected error (low accuracy): {e}")

    except Exception as e:
        logging.error(f"Unexpected error: {e}")

    print("\n" + "=" * 50 + "\n")

    logging.info("--- Testing Custom Exceptions ---")
    try:
        raise DataIngestionError("Failed to parse input file.")
    except DataIngestionError as e:
        logging.info(f"Caught DataIngestionError: {e}")

    try:
        raise ModelDeploymentError("Endpoint returned 503 Service Unavailable.")
    except ModelDeploymentError as e:
        logging.info(f"Caught ModelDeploymentError: {e}")

    try:
        raise LeaderElectionError("Failed to acquire distributed lock.")
    except LeaderElectionError as e:
        logging.info(f"Caught LeaderElectionError: {e}")
