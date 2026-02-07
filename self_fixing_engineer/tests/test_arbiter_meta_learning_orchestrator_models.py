# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import json
from datetime import datetime, timezone

import pytest

# Import the models and custom exceptions
from self_fixing_engineer.arbiter.meta_learning_orchestrator.models import (
    DataIngestionError,
    DeploymentStatus,
    EventType,
    LeaderElectionError,
    LearningRecord,
    ModelDeploymentError,
    ModelVersion,
)
from pydantic import ValidationError

# Sample data for testing
SAMPLE_LEARNING_RECORD = {
    "agent_id": "agent-001",
    "session_id": "session-xyz",
    "decision_trace": {"step1": "data", "step2": "decision"},
    "event_type": EventType.ACTION_TAKEN,
    "user_feedback": "positive",
    "lineage_id": "training-run-123",
    "timestamp": datetime.now(timezone.utc).isoformat(),
}

SAMPLE_MODEL_VERSION = {
    "model_id": "rl-policy-v1",
    "version": "1.0.0",
    "training_timestamp": datetime.now(timezone.utc).isoformat(),
    "evaluation_metrics": {"accuracy": 0.92, "precision": 0.88},
    "deployment_status": DeploymentStatus.PENDING,
    "lineage_id": "data-batch-2023-Q2",
    "metadata": {"framework": "tensorflow", "dataset_size": 10000},
}


@pytest.fixture
def learning_record_data():
    """Fixture for a valid LearningRecord data dictionary."""
    return SAMPLE_LEARNING_RECORD.copy()


@pytest.fixture
def model_version_data():
    """Fixture for a valid ModelVersion data dictionary."""
    return SAMPLE_MODEL_VERSION.copy()


def test_learning_record_initialization_success(learning_record_data):
    """Test successful initialization of LearningRecord."""
    record = LearningRecord(**learning_record_data)
    assert record.agent_id == "agent-001"
    assert record.session_id == "session-xyz"
    assert record.decision_trace == {"step1": "data", "step2": "decision"}
    assert record.event_type == EventType.ACTION_TAKEN
    assert record.user_feedback == "positive"
    assert record.lineage_id == "training-run-123"
    assert isinstance(record.timestamp, str)
    assert "T" in record.timestamp  # ISO format check


def test_learning_record_missing_required_fields(learning_record_data):
    """Test validation failure for missing required fields."""
    for field in ["agent_id", "session_id", "decision_trace", "event_type"]:
        invalid_data = learning_record_data.copy()
        del invalid_data[field]
        with pytest.raises(ValidationError) as exc_info:
            LearningRecord(**invalid_data)
        assert field in str(exc_info.value)


def test_learning_record_extra_fields(learning_record_data):
    """Test validation failure for extra fields."""
    invalid_data = learning_record_data.copy()
    invalid_data["extra_field"] = "invalid"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LearningRecord(**invalid_data)


def test_learning_record_immutability(learning_record_data):
    """Test immutability of LearningRecord."""
    record = LearningRecord(**learning_record_data)
    with pytest.raises(ValidationError, match="frozen"):
        record.agent_id = "new-agent"


def test_learning_record_json_serialization(learning_record_data):
    """Test JSON serialization and deserialization of LearningRecord."""
    record = LearningRecord(**learning_record_data)
    json_str = record.model_dump_json()
    parsed = json.loads(json_str)
    assert parsed["agent_id"] == "agent-001"
    assert parsed["event_type"] == "action_taken"  # Enum serialized as value

    # Deserialization
    deserialized = LearningRecord.model_validate_json(json_str)
    assert deserialized.agent_id == record.agent_id
    assert deserialized.event_type == record.event_type


def test_learning_record_default_timestamp(learning_record_data):
    """Test default timestamp generation."""
    del learning_record_data["timestamp"]
    record = LearningRecord(**learning_record_data)
    assert "T" in record.timestamp  # ISO format
    # Parse to check it's a valid datetime
    datetime.fromisoformat(record.timestamp.replace("Z", "+00:00"))


def test_learning_record_invalid_event_type():
    """Test validation failure for invalid event_type."""
    invalid_data = SAMPLE_LEARNING_RECORD.copy()
    invalid_data["event_type"] = "invalid_event"
    with pytest.raises(ValidationError) as exc_info:
        LearningRecord(**invalid_data)
    assert "event_type" in str(exc_info.value)


def test_model_version_initialization_success(model_version_data):
    """Test successful initialization of ModelVersion."""
    model = ModelVersion(**model_version_data)
    assert model.model_id == "rl-policy-v1"
    assert model.version == "1.0.0"
    assert model.evaluation_metrics["accuracy"] == 0.92
    assert model.evaluation_metrics["precision"] == 0.88
    assert model.deployment_status == DeploymentStatus.PENDING
    assert model.lineage_id == "data-batch-2023-Q2"
    assert model.metadata == {"framework": "tensorflow", "dataset_size": 10000}
    assert model.is_active is False
    assert model.retry_count == 0
    assert model.deployment_timestamp is None


def test_model_version_missing_required_fields(model_version_data):
    """Test validation failure for missing required fields."""
    for field in ["model_id", "version", "training_timestamp", "evaluation_metrics"]:
        invalid_data = model_version_data.copy()
        del invalid_data[field]
        with pytest.raises(ValidationError) as exc_info:
            ModelVersion(**invalid_data)
        assert field in str(exc_info.value)


def test_model_version_extra_fields(model_version_data):
    """Test validation failure for extra fields."""
    invalid_data = model_version_data.copy()
    invalid_data["extra_field"] = "invalid"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ModelVersion(**invalid_data)


def test_model_version_immutability(model_version_data):
    """Test immutability of ModelVersion."""
    model = ModelVersion(**model_version_data)
    with pytest.raises(ValidationError, match="frozen"):
        model.model_id = "new-model"


def test_model_version_json_serialization(model_version_data):
    """Test JSON serialization and deserialization of ModelVersion."""
    model = ModelVersion(**model_version_data)
    json_str = model.model_dump_json()
    parsed = json.loads(json_str)
    assert parsed["model_id"] == "rl-policy-v1"
    assert parsed["deployment_status"] == "pending"  # Enum serialized as value

    # Deserialization
    deserialized = ModelVersion.model_validate_json(json_str)
    assert deserialized.model_id == model.model_id
    assert deserialized.deployment_status == model.deployment_status


def test_model_version_deployed_no_accuracy(model_version_data):
    """Test validation failure for deployed model without accuracy metric."""
    invalid_data = model_version_data.copy()
    invalid_data["evaluation_metrics"] = {"f1_score": 0.85}
    invalid_data["deployment_status"] = DeploymentStatus.DEPLOYED
    invalid_data["is_active"] = True
    with pytest.raises(ValueError, match="Deployed models must have 'accuracy'"):
        ModelVersion(**invalid_data)


def test_model_version_low_accuracy(model_version_data):
    """Test validation failure for deployed model with low accuracy."""
    invalid_data = model_version_data.copy()
    invalid_data["evaluation_metrics"] = {"accuracy": 0.70, "precision": 0.65}
    invalid_data["deployment_status"] = DeploymentStatus.DEPLOYED
    invalid_data["is_active"] = True
    with pytest.raises(
        ValueError, match="Model accuracy.*is below deployment threshold"
    ):
        ModelVersion(**invalid_data)


def test_model_version_deployed_not_active(model_version_data):
    """Test validation failure for deployed model that's not active."""
    invalid_data = model_version_data.copy()
    invalid_data["deployment_status"] = DeploymentStatus.DEPLOYED
    invalid_data["is_active"] = False
    with pytest.raises(
        ValueError, match="'deployed' status must also be 'is_active: True'"
    ):
        ModelVersion(**invalid_data)


def test_model_version_valid_deployed(model_version_data):
    """Test successful validation for deployed model with sufficient accuracy."""
    valid_data = model_version_data.copy()
    valid_data["deployment_status"] = DeploymentStatus.DEPLOYED
    valid_data["is_active"] = True
    valid_data["deployment_timestamp"] = datetime.now(timezone.utc).isoformat()
    model = ModelVersion(**valid_data)
    assert model.deployment_status == DeploymentStatus.DEPLOYED
    assert model.is_active is True
    # The custom validator in ModelVersion assumes a threshold of 0.8
    assert model.evaluation_metrics["accuracy"] >= 0.8


def test_model_version_retry_count_validation(model_version_data):
    """Test validation for retry_count >= 0."""
    invalid_data = model_version_data.copy()
    invalid_data["retry_count"] = -1
    with pytest.raises(ValidationError) as exc_info:
        ModelVersion(**invalid_data)
    assert "greater than or equal to 0" in str(exc_info.value)


def test_model_version_valid_timestamp(model_version_data):
    """Test that timestamp fields accept valid ISO format strings."""
    valid_data = model_version_data.copy()
    valid_data["training_timestamp"] = "2023-10-15T10:30:00Z"
    model = ModelVersion(**valid_data)
    assert model.training_timestamp == "2023-10-15T10:30:00Z"


def test_learning_record_valid_timestamp(learning_record_data):
    """Test that timestamp field accepts valid ISO format strings."""
    valid_data = learning_record_data.copy()
    valid_data["timestamp"] = "2023-10-15T10:30:00Z"
    record = LearningRecord(**valid_data)
    assert record.timestamp == "2023-10-15T10:30:00Z"


def test_data_ingestion_error():
    """Test DataIngestionError instantiation."""
    error_msg = "Invalid data format"
    error = DataIngestionError(error_msg)
    assert str(error) == error_msg


def test_model_deployment_error():
    """Test ModelDeploymentError instantiation."""
    error_msg = "Deployment failed"
    error = ModelDeploymentError(error_msg)
    assert str(error) == error_msg


def test_leader_election_error():
    """Test LeaderElectionError instantiation."""
    error_msg = "Leader lock failed"
    error = LeaderElectionError(error_msg)
    assert str(error) == error_msg


def test_invalid_deployment_status():
    """Test validation failure for invalid deployment status."""
    invalid_data = SAMPLE_MODEL_VERSION.copy()
    invalid_data["deployment_status"] = "invalid_status"
    with pytest.raises(ValidationError) as exc_info:
        ModelVersion(**invalid_data)
    assert "deployment_status" in str(exc_info.value)


def test_model_version_failed_status(model_version_data):
    """Test model with failed deployment status."""
    data = model_version_data.copy()
    data["deployment_status"] = DeploymentStatus.FAILED
    data["retry_count"] = 3
    model = ModelVersion(**data)
    assert model.deployment_status == DeploymentStatus.FAILED
    assert model.retry_count == 3
    assert model.is_active is False


def test_model_version_rolled_back_status(model_version_data):
    """Test model with rolled back deployment status."""
    data = model_version_data.copy()
    data["deployment_status"] = DeploymentStatus.ROLLED_BACK
    data["deployment_timestamp"] = datetime.now(timezone.utc).isoformat()
    model = ModelVersion(**data)
    assert model.deployment_status == DeploymentStatus.ROLLED_BACK
    assert model.is_active is False


def test_learning_record_all_event_types():
    """Test all valid event types for LearningRecord."""
    base_data = {
        "agent_id": "test-agent",
        "session_id": "test-session",
        "decision_trace": {"test": "data"},
    }

    for event_type in EventType:
        data = base_data.copy()
        data["event_type"] = event_type
        record = LearningRecord(**data)
        assert record.event_type == event_type


def test_model_version_empty_metadata(model_version_data):
    """Test ModelVersion with empty metadata."""
    data = model_version_data.copy()
    data["metadata"] = {}
    model = ModelVersion(**data)
    assert model.metadata == {}


def test_model_version_complex_metadata(model_version_data):
    """Test ModelVersion with complex nested metadata."""
    data = model_version_data.copy()
    data["metadata"] = {
        "framework": {"name": "tensorflow", "version": "2.10.0"},
        "training": {"epochs": 100, "batch_size": 32, "learning_rate": 0.001},
        "hardware": {"gpu": "NVIDIA A100", "memory": "40GB"},
    }
    model = ModelVersion(**data)
    assert model.metadata["framework"]["name"] == "tensorflow"
    assert model.metadata["training"]["epochs"] == 100


def test_learning_record_without_optional_fields():
    """Test LearningRecord with only required fields."""
    data = {
        "agent_id": "minimal-agent",
        "session_id": "minimal-session",
        "decision_trace": {"action": "test"},
        "event_type": EventType.DECISION_MADE,
    }
    record = LearningRecord(**data)
    assert record.user_feedback is None
    assert record.lineage_id is None
    assert record.timestamp is not None  # Has default value


def test_model_version_high_accuracy_not_deployed(model_version_data):
    """Test that high accuracy models can exist without being deployed."""
    data = model_version_data.copy()
    data["evaluation_metrics"] = {"accuracy": 0.95, "precision": 0.93}
    data["deployment_status"] = DeploymentStatus.PENDING
    model = ModelVersion(**data)
    assert model.evaluation_metrics["accuracy"] == 0.95
    assert model.deployment_status == DeploymentStatus.PENDING
    assert model.is_active is False
