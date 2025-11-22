from pydantic import BaseModel, Field, validator
from typing import Dict, Any, Union
from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, Text, Index, DateTime
from sqlalchemy.orm import declarative_base

# --- Pydantic Models for Application Logic ---
# These models are used for data validation, serialization, and ensuring
# type safety within the application's business logic.


class GrowthEvent(BaseModel):
    """
    Represents a single, atomic event in an arbiter's growth lifecycle.
    This model is used to validate incoming event data before it is processed.
    """

    type: str = Field(
        ...,
        description="The type of the event (e.g., 'skill_improved'). Must be a non-empty string.",
        min_length=1,
    )
    timestamp: str = Field(
        ..., description="ISO 8601 timestamp of when the event occurred."
    )
    details: Dict[str, Any] = Field(
        ..., description="A dictionary containing event-specific data."
    )
    event_version: float = Field(
        1.0, description="The schema version of the event payload."
    )

    @validator("type")
    def type_must_not_be_whitespace(cls, v: str) -> str:
        """Ensures the event type is not just whitespace."""
        if not v.strip():
            raise ValueError("Event type cannot be empty or just whitespace.")
        return v

    @validator("timestamp")
    def validate_timestamp(cls, v: str) -> str:
        """Ensures the timestamp is a valid ISO 8601 format."""
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("Timestamp must be in ISO 8601 format")
        return v


class ArbiterState(BaseModel):
    """
    Represents the complete, in-memory state of an arbiter at a point in time.
    This model serves as the single source of truth for an arbiter's current
    attributes and is what gets snapshotted for persistence.
    """

    arbiter_id: str = Field(..., description="The unique identifier for the arbiter.")
    level: int = Field(1, ge=1, description="The current level of the arbiter.")
    skills: Dict[str, float] = Field(
        default_factory=dict,
        description="A dictionary of skill names to their scores (0.0 to 1.0).",
    )
    user_preferences: Dict[str, Any] = Field(
        default_factory=dict,
        description="A dictionary for storing user-specific settings.",
    )
    event_offset: Union[int, str] = Field(
        default="0",
        description="The offset of the last event processed to build this state.",
    )
    schema_version: float = Field(
        1.0, description="The schema version of this state object."
    )
    experience_points: float = Field(
        0.0, description="The total experience points accumulated by the arbiter."
    )

    @validator("event_offset")
    def convert_event_offset(cls, v: Union[int, str]) -> int:
        """Ensures event_offset is always an integer."""
        if isinstance(v, str):
            # Handle string numbers and also Redis stream IDs like "1234567890-0"
            if "-" in v:
                # This is a Redis stream ID, keep as string for compatibility
                return v
            try:
                return int(v)
            except ValueError:
                # If it can't be converted, keep as is for backward compatibility
                return v
        return v

    @validator("skills")
    def validate_skill_scores(cls, v: Dict[str, float]) -> Dict[str, float]:
        """Ensures all skill scores are within the valid range of 0.0 to 1.0."""
        for skill, score in v.items():
            if not 0.0 <= score <= 1.0:
                raise ValueError(
                    f"Skill '{skill}' has an invalid score: {score}. Must be between 0.0 and 1.0."
                )
        return v

    def set_skill_score(self, skill_name: str, score: float):
        """A helper method to set a skill score, clamping it to the valid range."""
        self.skills[skill_name] = max(0.0, min(1.0, score))

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        """Override model_dump to ensure event_offset is properly serialized."""
        data = super().model_dump(**kwargs)
        # Ensure event_offset is converted for storage if needed
        if isinstance(data.get("event_offset"), int):
            data["event_offset"] = str(data["event_offset"])
        return data


# --- SQLAlchemy Models for Database Schema ---

# Alembic Migration Note:
# The SQLAlchemy models defined below are used for database schema generation.
# When making changes to these models (e.g., adding a column), a new database
# migration script must be generated using a tool like Alembic to apply the
# changes to your database without data loss.
#
# Example Alembic commands:
# > alembic revision --autogenerate -m "Add experience_points to snapshots"
# > alembic upgrade head

# In a real application, Base would be imported from a central database setup file.
# For this standalone module, we define it here.
try:
    from app.omnicore_engine.database import Base
except ImportError:
    from sqlalchemy.orm import declarative_base

    Base = declarative_base()


class GrowthSnapshot(Base):
    """
    Database model for storing a serialized, persistent snapshot of an
    arbiter's state. This table allows for quick state restoration without
    replaying the entire event history.
    """

    __tablename__ = "arbiter_growth_snapshots"
    arbiter_id = Column(
        String, primary_key=True, comment="The unique ID of the arbiter."
    )
    level = Column(
        Integer,
        server_default="1",
        nullable=False,
        comment="The arbiter's level at the time of the snapshot.",
    )
    skills_encrypted = Column(
        Text, comment="Encrypted JSON dictionary of skill scores."
    )
    user_preferences_encrypted = Column(
        Text, comment="Encrypted JSON dictionary of user preferences."
    )
    experience_points = Column(
        Float,
        server_default="0.0",
        nullable=False,
        comment="The arbiter's XP at the time of the snapshot.",
    )
    schema_version = Column(
        Float,
        server_default="1.0",
        nullable=False,
        comment="The schema version of the snapshotted state.",
    )
    event_offset = Column(
        String,
        server_default="0",
        nullable=False,
        comment="The event offset corresponding to this state snapshot.",
    )
    timestamp = Column(
        DateTime, nullable=True, comment="Timestamp when the snapshot was created."
    )

    __table_args__ = (
        {"comment": "Snapshots of arbiter state for persistence and recovery."},
    )


class GrowthEventRecord(Base):
    """
    Database model for storing the immutable log of all growth events.
    This serves as the ultimate source of truth for an arbiter's history.
    """

    __tablename__ = "arbiter_growth_events"
    id = Column(Integer, primary_key=True)
    arbiter_id = Column(
        String, index=True, nullable=False, comment="The arbiter this event belongs to."
    )
    event_type = Column(String, nullable=False, comment="The type of the event.")
    timestamp = Column(
        String, nullable=False, comment="The ISO 8601 timestamp of the event."
    )
    details_encrypted = Column(
        Text, comment="Encrypted JSON dictionary of event-specific details."
    )
    event_version = Column(
        Float,
        server_default="1.0",
        nullable=False,
        comment="The schema version of the event.",
    )

    __table_args__ = (Index("idx_arbiter_growth_events_arbiter_id", "arbiter_id"),)


class AuditLog(Base):
    """
    Database model for a chained audit log to ensure the integrity and
    non-repudiation of all operations performed on an arbiter.
    """

    __tablename__ = "arbiter_audit_logs"
    id = Column(Integer, primary_key=True)
    arbiter_id = Column(
        String,
        index=True,
        nullable=False,
        comment="The arbiter this log entry belongs to.",
    )
    operation = Column(
        String,
        nullable=False,
        comment="The operation being audited (e.g., 'event_recorded').",
    )
    timestamp = Column(
        String,
        nullable=False,
        comment="The ISO 8601 timestamp of the audited operation.",
    )
    details = Column(Text, comment="JSON string containing details of the operation.")
    previous_log_hash = Column(
        String,
        nullable=False,
        comment="The hash of the preceding log entry in the chain.",
    )
    log_hash = Column(
        String,
        nullable=False,
        unique=True,
        comment="The hash of this log entry, creating the chain link.",
    )

    __table_args__ = (
        Index("idx_arbiter_audit_logs_arbiter_id", "arbiter_id"),
        Index("idx_audit_log_hash", "log_hash", unique=True),
    )
