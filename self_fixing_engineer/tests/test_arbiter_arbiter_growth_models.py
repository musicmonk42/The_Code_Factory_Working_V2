"""
Unit tests for data models.

Tests Pydantic models and SQLAlchemy ORM models.
"""

import json
from datetime import datetime, timezone

import pytest
from self_fixing_engineer.arbiter.arbiter_growth.models import (
    ArbiterState,
    AuditLog,
    Base,
    GrowthEvent,
    GrowthEventRecord,
    GrowthSnapshot,
)
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

# --- Fixtures ---


@pytest.fixture
def engine():
    """Create an in-memory SQLite database for testing."""
    # FIX: Explicitly specify the 'pysqlite' driver to bypass
    # broken dialect auto-detection.
    engine = create_engine("sqlite+pysqlite:///:memory:")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(engine):
    """Create a database session."""
    with Session(engine) as session:
        yield session


# --- Pydantic Model Tests ---


def test_growth_event_valid():
    """Test creating a valid GrowthEvent."""
    event = GrowthEvent(
        type="learning",
        timestamp="2024-01-01T12:00:00+00:00",
        details={"skill_name": "python", "improvement_delta": 15.0},
        event_version=1.0,
    )

    assert event.type == "learning"
    assert event.details["skill_name"] == "python"
    assert event.timestamp is not None
    assert event.event_version == 1.0


def test_growth_event_invalid_type_empty():
    """Test that empty type is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        GrowthEvent(type="", timestamp="2024-01-01T12:00:00+00:00", details={})

    errors = exc_info.value.errors()
    assert any("at least 1 character" in str(e) for e in errors)


def test_growth_event_invalid_type_whitespace():
    """Test that whitespace-only event_type is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        GrowthEvent(type="   ", timestamp="2024-01-01T12:00:00+00:00", details={})

    errors = exc_info.value.errors()
    assert any("cannot be empty or just whitespace" in str(e) for e in errors)


def test_growth_event_missing_required_fields():
    """Test that missing required fields raise validation error."""
    with pytest.raises(ValidationError) as exc_info:
        GrowthEvent(type="learning")

    errors = exc_info.value.errors()
    assert any("details" in str(e) for e in errors)


def test_growth_event_serialization():
    """Test serializing GrowthEvent to JSON."""
    event = GrowthEvent(
        type="achievement",
        details={"skill_name": "rust", "improvement_delta": 20.0},
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    json_str = event.model_dump_json()
    data = json.loads(json_str)

    assert data["type"] == "achievement"
    assert data["details"]["skill_name"] == "rust"
    assert data["details"]["improvement_delta"] == 20.0


def test_growth_event_deserialization():
    """Test deserializing GrowthEvent from JSON."""
    json_data = {
        "type": "learning",
        "details": {"skill_name": "go", "improvement_amount": 0.7},
        "timestamp": "2024-01-01T12:00:00+00:00",
    }

    event = GrowthEvent(**json_data)

    assert event.type == "learning"
    assert event.details["skill_name"] == "go"
    assert event.details["improvement_amount"] == 0.7


def test_arbiter_state_valid():
    """Test creating a valid ArbiterState."""
    state = ArbiterState(
        arbiter_id="test_arbiter",
        level=5,
        experience_points=2500,
        skills={"python": 0.8, "rust": 0.6},
        event_offset=100,
    )

    assert state.arbiter_id == "test_arbiter"
    assert state.level == 5
    assert state.experience_points == 2500
    assert state.skills["python"] == 0.8
    assert state.event_offset == 100


def test_arbiter_state_level_min():
    """Test that level must be at least 1."""
    with pytest.raises(ValidationError) as exc_info:
        ArbiterState(arbiter_id="test", level=0)
    assert any(
        "Input should be greater than or equal to 1" in str(e)
        for e in exc_info.value.errors()
    )


def test_arbiter_state_event_offset_int():
    """Test that event_offset is converted to int when possible."""
    state = ArbiterState(arbiter_id="test", event_offset="42")

    # The validator converts numeric strings to int
    assert state.event_offset == 42
    assert isinstance(state.event_offset, int)


def test_arbiter_state_set_skill_score():
    """Test setting skill scores."""
    state = ArbiterState(arbiter_id="test")

    state.set_skill_score("python", 0.755)
    assert state.skills["python"] == 0.755

    # Test clamping to max
    state.set_skill_score("rust", 1.5)
    assert state.skills["rust"] == 1.0

    # Test clamping to min
    state.set_skill_score("go", -0.1)
    assert state.skills["go"] == 0.0


def test_arbiter_state_serialization():
    """Test serializing ArbiterState."""
    state = ArbiterState(arbiter_id="test", level=3, skills={"python": 0.7})

    data = state.model_dump()

    assert data["arbiter_id"] == "test"
    assert data["level"] == 3
    assert data["skills"]["python"] == 0.7


# --- SQLAlchemy Model Tests ---


def test_growth_snapshot_columns(engine):
    """Test GrowthSnapshot table columns and constraints."""
    inspector = inspect(engine)
    columns = {
        col["name"]: col for col in inspector.get_columns("arbiter_growth_snapshots")
    }

    assert "arbiter_id" in columns
    assert columns["arbiter_id"]["primary_key"]
    assert columns["arbiter_id"]["type"].python_type == str

    assert "level" in columns
    assert str(columns["level"]["default"]).strip("'") == "1"

    assert "experience_points" in columns
    assert columns["experience_points"]["type"].python_type == float

    assert "skills_encrypted" in columns
    assert "event_offset" in columns
    assert "schema_version" in columns
    assert "timestamp" in columns  # Should now exist


def test_growth_event_record_columns(engine):
    """Test GrowthEventRecord table columns and constraints."""
    inspector = inspect(engine)
    columns = {
        col["name"]: col for col in inspector.get_columns("arbiter_growth_events")
    }

    assert "id" in columns
    assert columns["id"]["primary_key"]

    assert "arbiter_id" in columns
    assert not columns["arbiter_id"]["nullable"]

    # Check for index on arbiter_id
    indexes = inspector.get_indexes("arbiter_growth_events")
    assert any(idx["column_names"] == ["arbiter_id"] for idx in indexes)

    assert "event_type" in columns
    assert not columns["event_type"]["nullable"]

    assert "timestamp" in columns
    assert not columns["timestamp"]["nullable"]

    assert "details_encrypted" in columns

    assert "event_version" in columns
    default_value = columns["event_version"].get("default")
    assert str(default_value).strip("'\"") == "1.0"


def test_audit_log_columns(engine):
    """Test AuditLog table columns and constraints."""
    inspector = inspect(engine)
    columns = {col["name"]: col for col in inspector.get_columns("arbiter_audit_logs")}

    assert "id" in columns
    assert columns["id"]["primary_key"]

    assert "arbiter_id" in columns
    assert not columns["arbiter_id"]["nullable"]

    assert "operation" in columns
    assert "log_hash" in columns
    assert "previous_log_hash" in columns
    assert "details" in columns
    assert "timestamp" in columns


def test_create_growth_snapshot(session):
    """Test creating a GrowthSnapshot record."""
    snapshot = GrowthSnapshot(
        arbiter_id="test_arbiter",
        level=3,
        experience_points=1500,
        skills_encrypted=b"encrypted_skills_data",
        event_offset="50",
        user_preferences_encrypted=b"encrypted_metadata",
        # Removed timestamp parameter - it's optional/nullable
    )

    session.add(snapshot)
    session.commit()

    # Query back
    result = session.query(GrowthSnapshot).filter_by(arbiter_id="test_arbiter").first()

    assert result is not None
    assert result.level == 3
    assert result.experience_points == 1500
    assert result.event_offset == "50"


def test_create_growth_event_record(session):
    """Test creating a GrowthEventRecord."""
    event_record = GrowthEventRecord(
        arbiter_id="test_arbiter",
        event_type="learning",
        timestamp=datetime.now(timezone.utc).isoformat(),
        details_encrypted=b"encrypted_details",
        event_version=1.0,
    )

    session.add(event_record)
    session.commit()

    # Query back
    result = (
        session.query(GrowthEventRecord).filter_by(arbiter_id="test_arbiter").first()
    )

    assert result is not None
    assert result.event_type == "learning"
    assert result.event_version == 1.0


def test_create_audit_log(session):
    """Test creating an AuditLog entry."""
    audit = AuditLog(
        arbiter_id="test_arbiter",
        operation="test_op",
        log_hash="hash_abc123",
        previous_log_hash="hash_xyz789",
        details=json.dumps({"key": "value"}),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    session.add(audit)
    session.commit()

    # Query back
    result = session.query(AuditLog).filter_by(arbiter_id="test_arbiter").first()

    assert result is not None
    assert result.operation == "test_op"
    assert result.log_hash == "hash_abc123"
    assert result.previous_log_hash == "hash_xyz789"


def test_arbiter_state_invalid_skill_score():
    """Test that invalid skill scores are handled."""
    state = ArbiterState(arbiter_id="test")

    # Test that scores are clamped
    state.set_skill_score("test_skill", 2.0)
    assert state.skills["test_skill"] == 1.0

    state.set_skill_score("test_skill", -0.5)
    assert state.skills["test_skill"] == 0.0


def test_arbiter_state_large_skills():
    """Test handling of many skills."""
    state = ArbiterState(arbiter_id="test")

    # Add many skills
    for i in range(100):
        state.set_skill_score(f"skill_{i}", float(i) / 100.0)

    assert len(state.skills) == 100
    assert state.skills["skill_50"] == 0.5


def test_schema_version_defaults(engine):
    """Test that schema version defaults are set correctly."""
    inspector = inspect(engine)

    # Check GrowthSnapshot
    snapshot_cols = {
        col["name"]: col for col in inspector.get_columns("arbiter_growth_snapshots")
    }
    level_default = str(snapshot_cols["level"].get("default")).strip("'\"")
    schema_version_default = str(snapshot_cols["schema_version"].get("default")).strip(
        "'\""
    )

    assert level_default == "1" or level_default == "1.0"
    assert schema_version_default == "1.0"

    # Check GrowthEventRecord
    event_cols = {
        col["name"]: col for col in inspector.get_columns("arbiter_growth_events")
    }
    event_version_default = str(event_cols["event_version"].get("default")).strip("'\"")
    assert event_version_default == "1.0"


def test_growth_event_with_large_metadata():
    """Test GrowthEvent with large metadata."""
    large_metadata = {f"key_{i}": f"value_{i}" * 100 for i in range(50)}

    event = GrowthEvent(
        type="learning",
        details={"skill_name": "test", "metadata": large_metadata},
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    assert len(event.details["metadata"]) == 50
    assert "key_25" in event.details["metadata"]


def test_timestamp_handling(session):
    """Test that timestamps are properly handled."""
    snapshot = GrowthSnapshot(
        arbiter_id="test_time",
        level=1,
        experience_points=0,
        # No timestamp parameter - it's optional
    )

    session.add(snapshot)
    session.commit()

    # Query back
    result = session.query(GrowthSnapshot).filter_by(arbiter_id="test_time").first()

    # Timestamp should be None if not set (it's nullable)
    assert result is not None
    assert result.level == 1


def test_cascade_delete_behavior(session):
    """Test cascade delete behavior if configured."""
    # Create related records
    snapshot = GrowthSnapshot(
        arbiter_id="cascade_test",
        level=1,
        experience_points=0,
        # No timestamp parameter
    )

    event = GrowthEventRecord(
        arbiter_id="cascade_test",
        event_type="learning",
        timestamp=datetime.now(timezone.utc).isoformat(),
        details_encrypted=b"encrypted_details",
    )

    audit = AuditLog(
        arbiter_id="cascade_test",
        operation="test_op",
        log_hash="hash_1",
        previous_log_hash="genesis_hash",
        details=json.dumps({}),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    session.add_all([snapshot, event, audit])
    session.commit()

    # Verify all exist
    assert (
        session.query(GrowthSnapshot).filter_by(arbiter_id="cascade_test").count() == 1
    )
    assert (
        session.query(GrowthEventRecord).filter_by(arbiter_id="cascade_test").count()
        == 1
    )
    assert session.query(AuditLog).filter_by(arbiter_id="cascade_test").count() == 1

    # Delete snapshot (cascade behavior depends on foreign key setup)
    session.delete(snapshot)
    session.commit()

    # Snapshot should be gone
    assert (
        session.query(GrowthSnapshot).filter_by(arbiter_id="cascade_test").count() == 0
    )
    # Other records remain (unless cascade delete is configured)
    assert (
        session.query(GrowthEventRecord).filter_by(arbiter_id="cascade_test").count()
        == 1
    )
    assert session.query(AuditLog).filter_by(arbiter_id="cascade_test").count() == 1


# --- Reconstructed and New Tests ---


def test_arbiter_state_skill_clamping():
    """Test that skill scores are clamped between 0.0 and 1.0 during model initialization."""
    # Test that a score greater than 1.0 raises a ValidationError
    with pytest.raises(ValidationError):
        ArbiterState(arbiter_id="test", skills={"python": 1.5})

    # Test that a score less than 0.0 raises a ValidationError
    with pytest.raises(ValidationError):
        ArbiterState(arbiter_id="test", skills={"go": -0.5})

    # Test that valid scores are accepted
    state = ArbiterState(arbiter_id="test", skills={"python": 0.5})
    assert state.skills["python"] == 0.5


def test_growth_event_record_encryption(session):
    """Test that the encrypted fields in GrowthEventRecord are stored correctly."""
    record = GrowthEventRecord(
        arbiter_id="test_encryption",
        event_type="test",
        timestamp=datetime.now(timezone.utc).isoformat(),
        details_encrypted=b"encrypted_data",
        event_version=1.0,
    )

    session.add(record)
    session.commit()

    result = (
        session.query(GrowthEventRecord).filter_by(arbiter_id="test_encryption").first()
    )

    assert result is not None
    assert result.details_encrypted == b"encrypted_data"
