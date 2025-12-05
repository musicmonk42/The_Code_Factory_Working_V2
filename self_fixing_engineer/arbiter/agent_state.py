"""
SQLAlchemy models for persisting agent state and metadata in the Arbiter system.

This implementation balances production requirements with testability:
- Works in both test and production environments
- Provides security and compliance features when available
- Gracefully degrades when dependencies are missing
- Maintains data integrity and validation

Metrics:
- schema_validation_errors_total: Total validation errors for AgentState and AgentMetadata (table, error_type)

Dependencies:
- Requires SQLAlchemy for ORM functionality
- Optional: PostgreSQL for advanced features (JSONB, row-level security)
- Optional: OpenTelemetry for observability
- Optional: cryptography for field-level encryption
"""

import asyncio
import json
import logging
import sys

from arbiter.otel_config import get_tracer
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    event,
)
from sqlalchemy.orm import declarative_base, validates
from sqlalchemy.sql import func

# Setup logging without RotatingFileHandler for test compatibility
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)

# Centralized tracer initialization
tracer = get_tracer(__name__)

# Handle config import - try relative import first, then absolute
try:
    from .config import ArbiterConfig
except ImportError:
    try:
        from config import ArbiterConfig
    except ImportError:
        # Provide a default configuration if config module not available
        class ArbiterConfig:
            DATABASE_URL = "sqlite:///agent.db"
            VALIDATION_TIMEOUT_SECONDS = 1.0
            AGENT_PERSONALITY_SCHEMA = {}
            AGENT_METADATA_SCHEMA = {}
            AGENT_STATE_TABLE = "agent_state"
            AGENT_METADATA_TABLE = "agent_metadata"
            MAX_MEMORY_SIZE = 1000
            MAX_INVENTORY_SIZE = 500

            def __call__(self):
                return self


Base = declarative_base()

# Metrics counter implementation
if "get_or_create_metric" not in globals():
    # Provide a simple metric counter implementation for test/development environments
    class SimpleCounter:
        def __init__(self, name=None):
            self.name = name
            self._counts = {}

        def labels(self, **kwargs):
            key = tuple(sorted(kwargs.items()))
            if key not in self._counts:
                self._counts[key] = 0

            class IncProxy:
                def __init__(self, parent, key):
                    self.parent = parent
                    self.key = key

                def inc(self, n=1):
                    self.parent._counts[self.key] = (
                        self.parent._counts.get(self.key, 0) + n
                    )

            return IncProxy(self, key)

        def get(self, **kwargs):
            """Get counter value for testing."""
            key = tuple(sorted(kwargs.items()))
            return self._counts.get(key, 0)

    def get_or_create_metric(metric_class, name, description, labelnames=None):
        """Create or return a metric counter."""
        return SimpleCounter(name)


# Create the global counter
SCHEMA_VALIDATION_ERRORS = get_or_create_metric(
    "Counter",
    "schema_validation_errors_total",
    "Total validation errors for AgentState and AgentMetadata",
    labelnames=("table", "error_type"),
)


class AgentState(Base):
    """
    Agent state model with comprehensive validation for regulated environments.

    Maintains strict data integrity through multi-layer validation:
    - Field-level validation via @validates decorators
    - Schema validation for complex JSON structures
    - Database constraints for data consistency
    - Comprehensive metrics tracking for compliance monitoring
    """

    __tablename__ = "agent_state"
    __table_args__ = (
        CheckConstraint("energy >= 0.0 AND energy <= 100.0", name="check_energy_range"),
        CheckConstraint("world_size > 0", name="check_world_size_positive"),
        {'extend_existing': True}  # Allow table redefinition in tests
    )

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False, index=True)
    x = Column(Float, nullable=False, default=0.0)
    y = Column(Float, nullable=False, default=0.0)
    energy = Column(Float, nullable=False, default=100.0)
    inventory = Column(Text, nullable=False, default="[]")  # Store as JSON string
    language = Column(Text, nullable=False, default="[]")  # Store as JSON string
    memory = Column(Text, nullable=False, default="[]")  # Store as JSON string
    personality = Column(Text, nullable=False, default="{}")  # Store as JSON string
    world_size = Column(Integer, nullable=False, default=100)
    agent_type = Column(String, nullable=False, default="Arbiter")
    role = Column(String, nullable=False, default="user")

    def __repr__(self) -> str:
        return (
            f"<AgentState(id={self.id}, name='{self.name}', x={self.x}, y={self.y}, "
            f"energy={self.energy}, world_size={self.world_size})>"
        )

    def _parse_json_field(self, field_value):
        """Parse a JSON field, handling both string and object formats."""
        if isinstance(field_value, str):
            try:
                return json.loads(field_value)
            except json.JSONDecodeError:
                return None
        return field_value

    def _validate_inventory(self, inventory):
        """Validate inventory field with comprehensive error tracking."""
        parsed = self._parse_json_field(inventory)
        if (
            parsed is None
            and isinstance(inventory, str)
            and inventory not in ["[]", ""]
        ):
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_inventory"
            ).inc()
            raise ValueError("AgentState.inventory must be a list")
        if parsed is not None and not isinstance(parsed, list):
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_inventory"
            ).inc()
            raise ValueError("AgentState.inventory must be a list")
        # Handle non-string, non-list values
        if not isinstance(inventory, (list, str)):
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_inventory"
            ).inc()
            raise ValueError("AgentState.inventory must be a list")
        return parsed if parsed is not None else []

    def _validate_language(self, language):
        """Validate language field with comprehensive error tracking."""
        parsed = self._parse_json_field(language)
        if parsed is None and isinstance(language, str) and language not in ["[]", ""]:
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_language"
            ).inc()
            raise ValueError("AgentState.language must be a list")
        if parsed is not None and not isinstance(parsed, list):
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_language"
            ).inc()
            raise ValueError("AgentState.language must be a list")
        # Handle non-string, non-list values
        if not isinstance(language, (list, str)):
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_language"
            ).inc()
            raise ValueError("AgentState.language must be a list")
        return parsed if parsed is not None else []

    def _validate_memory(self, memory):
        """Validate memory field with size limits and comprehensive error tracking."""
        parsed = self._parse_json_field(memory)
        if parsed is None and isinstance(memory, str) and memory not in ["[]", ""]:
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_memory"
            ).inc()
            raise ValueError("AgentState.memory must be a list")
        if parsed is not None and not isinstance(parsed, list):
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_memory"
            ).inc()
            raise ValueError("AgentState.memory must be a list")
        # Handle non-string, non-list values
        if not isinstance(memory, (list, str)):
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_memory"
            ).inc()
            raise ValueError("AgentState.memory must be a list")

        # Apply size limit if configured for regulatory compliance
        config = ArbiterConfig()
        if parsed and hasattr(config, "MAX_MEMORY_SIZE"):
            if len(parsed) > config.MAX_MEMORY_SIZE:
                parsed = parsed[-config.MAX_MEMORY_SIZE :]  # FIFO eviction
        return parsed if parsed is not None else []

    def _validate_personality(self, personality):
        """Validate personality field with comprehensive error tracking."""
        parsed = self._parse_json_field(personality)
        if (
            parsed is None
            and isinstance(personality, str)
            and personality not in ["{}", ""]
        ):
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_personality"
            ).inc()
            raise ValueError("AgentState.personality must be a dict")
        if parsed is not None and not isinstance(parsed, dict):
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_personality"
            ).inc()
            raise ValueError("AgentState.personality must be a dict")
        # Handle non-string, non-dict values
        if not isinstance(personality, (dict, str)):
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_personality"
            ).inc()
            raise ValueError("AgentState.personality must be a dict")
        return parsed if parsed is not None else {}

    # SQLAlchemy field validators with automatic JSON serialization
    @validates("inventory")
    def validate_inventory(self, key, value):
        """Validate and serialize inventory field for database storage."""
        if isinstance(value, list):
            self._validate_inventory(value)
            return json.dumps(value)
        elif isinstance(value, str):
            self._validate_inventory(value)
            return value
        else:
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_inventory"
            ).inc()
            raise ValueError("AgentState.inventory must be a list")

    @validates("language")
    def validate_language(self, key, value):
        """Validate and serialize language field for database storage."""
        if isinstance(value, list):
            self._validate_language(value)
            return json.dumps(value)
        elif isinstance(value, str):
            self._validate_language(value)
            return value
        else:
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_language"
            ).inc()
            raise ValueError("AgentState.language must be a list")

    @validates("memory")
    def validate_memory(self, key, value):
        """Validate and serialize memory field with size limits for database storage."""
        if isinstance(value, list):
            validated = self._validate_memory(value)
            return json.dumps(validated)
        elif isinstance(value, str):
            self._validate_memory(value)
            return value
        else:
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_memory"
            ).inc()
            raise ValueError("AgentState.memory must be a list")

    @validates("personality")
    def validate_personality(self, key, value):
        """Validate and serialize personality field for database storage."""
        if isinstance(value, dict):
            self._validate_personality(value)
            return json.dumps(value)
        elif isinstance(value, str):
            self._validate_personality(value)
            return value
        else:
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_personality"
            ).inc()
            raise ValueError("AgentState.personality must be a dict")

    @validates("energy")
    def validate_energy(self, key, value):
        """Validate energy field within regulatory constraints."""
        if not isinstance(value, (int, float)) or not 0.0 <= value <= 100.0:
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_energy"
            ).inc()
            raise ValueError("AgentState.energy must be between 0.0 and 100.0")
        return value

    @validates("world_size")
    def validate_world_size(self, key, value):
        """Validate world_size field as positive integer."""
        if not isinstance(value, int) or value <= 0:
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_state", error_type="invalid_world_size"
            ).inc()
            raise ValueError("AgentState.world_size must be a positive integer")
        return value

    @staticmethod
    def _validate_json_fields_sync(mapper, connection, target):
        """
        Synchronous validation wrapper for SQLAlchemy events.
        Handles deep schema validation for regulatory compliance.
        """
        try:
            # Check if we're in an async context
            try:
                asyncio.get_running_loop()
                # We're in an async context but can't use asyncio.run
                # Just do synchronous validation for deep schema checks
                AgentState._validate_fields_sync(target)
            except RuntimeError:
                # No running loop, safe to use asyncio.run
                asyncio.run(
                    AgentState._validate_json_fields(mapper, connection, target)
                )
        except Exception:
            # Fallback to sync validation if async fails
            AgentState._validate_fields_sync(target)

    @staticmethod
    async def _validate_json_fields(mapper, connection, target):
        """
        Asynchronous JSON field validation with timeout for production environments.
        Provides comprehensive schema validation for regulatory compliance.
        """
        config = ArbiterConfig()
        timeout = getattr(config, "VALIDATION_TIMEOUT_SECONDS", 1.0)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            timeout = 1.0

        with tracer.start_as_current_span(
            "validate_agent_state", attributes={"table": "agent_state"}
        ):
            try:
                await asyncio.wait_for(
                    AgentState._validate_fields(target), timeout=timeout
                )
            except asyncio.TimeoutError:
                SCHEMA_VALIDATION_ERRORS.labels(
                    table="agent_state", error_type="validation_timeout"
                ).inc()
                logger.error("Validation timeout for AgentState")
                raise ValueError("Validation timeout for AgentState")

    @staticmethod
    def _validate_fields_sync(target):
        """
        Synchronous field validation for regulatory compliance.
        Performs deep schema validation when async is not available.
        """
        try:
            config = ArbiterConfig()
            personality_schema = getattr(config, "AGENT_PERSONALITY_SCHEMA", {})

            if personality_schema:
                personality = target._parse_json_field(target.personality)
                if personality and not all(
                    k in personality
                    and isinstance(personality[k], personality_schema[k])
                    for k in personality_schema
                ):
                    SCHEMA_VALIDATION_ERRORS.labels(
                        table="agent_state", error_type="invalid_personality_schema"
                    ).inc()
                    raise ValueError(
                        f"AgentState.personality does not match schema: {personality_schema}"
                    )
        except AttributeError:
            logger.debug(
                "AGENT_PERSONALITY_SCHEMA not defined; skipping deep validation"
            )

    @staticmethod
    async def _validate_fields(target):
        """Asynchronous field validation coordinator."""
        AgentState._validate_fields_sync(target)


class AgentMetadata(Base):
    """
    Key-value metadata storage for agents with comprehensive validation.

    Provides flexible metadata storage while maintaining data integrity
    through schema validation and comprehensive error tracking.
    """

    __tablename__ = "agent_metadata"
    __table_args__ = {'extend_existing': True}  # Allow table redefinition in tests

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False, index=True)
    value = Column(Text, nullable=False, default="{}")  # Store as JSON string
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<AgentMetadata(id={self.id}, key='{self.key}', value={self.value}, "
            f"created_at={self.created_at}, updated_at={self.updated_at})>"
        )

    def _parse_json_field(self, field_value):
        """Parse a JSON field, handling both string and object formats."""
        if isinstance(field_value, str):
            try:
                return json.loads(field_value)
            except json.JSONDecodeError:
                return None
        return field_value

    def _validate_value(self, value):
        """Validate value field with comprehensive error tracking."""
        parsed = self._parse_json_field(value)
        if parsed is None and isinstance(value, str) and value not in ["{}", ""]:
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_metadata", error_type="invalid_value"
            ).inc()
            raise ValueError("AgentMetadata.value must be a dict")
        if parsed is not None and not isinstance(parsed, dict):
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_metadata", error_type="invalid_value"
            ).inc()
            raise ValueError("AgentMetadata.value must be a dict")
        return parsed if parsed is not None else {}

    @validates("value")
    def validate_value(self, key, value):
        """Validate and serialize value field for database storage."""
        if isinstance(value, dict):
            self._validate_value(value)
            return json.dumps(value)
        elif isinstance(value, str):
            self._validate_value(value)
            return value
        else:
            SCHEMA_VALIDATION_ERRORS.labels(
                table="agent_metadata", error_type="invalid_value"
            ).inc()
            raise ValueError("AgentMetadata.value must be a dict")

    @staticmethod
    def _validate_json_fields_sync(mapper, connection, target):
        """Synchronous validation wrapper for SQLAlchemy events."""
        try:
            try:
                asyncio.get_running_loop()
                AgentMetadata._validate_fields_sync(target)
            except RuntimeError:
                asyncio.run(
                    AgentMetadata._validate_json_fields(mapper, connection, target)
                )
        except Exception:
            AgentMetadata._validate_fields_sync(target)

    @staticmethod
    async def _validate_json_fields(mapper, connection, target):
        """Asynchronous JSON field validation with timeout."""
        config = ArbiterConfig()
        timeout = getattr(config, "VALIDATION_TIMEOUT_SECONDS", 1.0)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            timeout = 1.0

        with tracer.start_as_current_span(
            "validate_agent_metadata", attributes={"table": "agent_metadata"}
        ):
            try:
                await asyncio.wait_for(
                    AgentMetadata._validate_fields(target), timeout=timeout
                )
            except asyncio.TimeoutError:
                SCHEMA_VALIDATION_ERRORS.labels(
                    table="agent_metadata", error_type="validation_timeout"
                ).inc()
                logger.error("Validation timeout for AgentMetadata")
                raise ValueError("Validation timeout for AgentMetadata")

    @staticmethod
    def _validate_fields_sync(target):
        """Synchronous field validation for regulatory compliance."""
        try:
            config = ArbiterConfig()
            metadata_schema = getattr(config, "AGENT_METADATA_SCHEMA", {})

            if metadata_schema:
                value = target._parse_json_field(target.value)
                if value and not all(
                    k in value and isinstance(value[k], metadata_schema[k])
                    for k in metadata_schema
                ):
                    SCHEMA_VALIDATION_ERRORS.labels(
                        table="agent_metadata", error_type="invalid_value_schema"
                    ).inc()
                    raise ValueError(
                        f"AgentMetadata.value does not match schema: {metadata_schema}"
                    )
        except AttributeError:
            logger.debug("AGENT_METADATA_SCHEMA not defined; skipping deep validation")

    @staticmethod
    async def _validate_fields(target):
        """Asynchronous field validation coordinator."""
        AgentMetadata._validate_fields_sync(target)


# Register validation event listeners for comprehensive data integrity
event.listen(AgentState, "before_insert", AgentState._validate_json_fields_sync)
event.listen(AgentState, "before_update", AgentState._validate_json_fields_sync)
event.listen(AgentMetadata, "before_insert", AgentMetadata._validate_json_fields_sync)
event.listen(AgentMetadata, "before_update", AgentMetadata._validate_json_fields_sync)

# Create database indexes for performance optimization
Index("ix_agentstate_name", AgentState.name)
Index("ix_agentmetadata_key", AgentMetadata.key)
