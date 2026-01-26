# omnicore_engine/database/models.py
"""
SQLAlchemy ORM models for the Omnicore Omega Pro Engine.
Uses joined-table inheritance from the `arbiter` package when available.
All models are fully type-annotated and compatible with SQLAlchemy 2.0+.

DEFENSIVE IMPORT STRATEGY:
- Attempts to import Base and ArbiterAgentState from arbiter package
- Falls back to creating standalone models if arbiter is unavailable
- This allows omnicore_engine to run independently for testing/development
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, declarative_base

# DEFENSIVE IMPORT: Try to import from arbiter, fall back to standalone mode
_ARBITER_AVAILABLE = False
_Base = None
_ArbiterAgentState = None

try:
    from arbiter.agent_state import AgentState as ArbiterAgentState
    from arbiter.agent_state import Base

    _ARBITER_AVAILABLE = True
    _Base = Base
    _ArbiterAgentState = ArbiterAgentState
    import logging

    logging.getLogger(__name__).info(
        "Successfully imported arbiter.agent_state - using joined-table inheritance"
    )
except ImportError as e:
    import logging

    logging.getLogger(__name__).warning(
        f"Could not import arbiter.agent_state ({e}). "
        "Running in standalone mode with independent Base class. "
        "Joined-table inheritance will not be available."
    )
    # Create standalone Base for testing/development without arbiter
    _Base = declarative_base()

    # Create minimal standalone ArbiterAgentState replacement
    class _StandaloneAgentState(_Base):
        """
        Standalone replacement for ArbiterAgentState when arbiter package is unavailable.
        Provides minimal compatible interface for testing and development.
        """

        __tablename__ = "agent_state"
        __table_args__ = {"extend_existing": True}

        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        name: Mapped[str] = mapped_column(String, nullable=False)
        x: Mapped[float] = mapped_column(Float, default=0.0)
        y: Mapped[float] = mapped_column(Float, default=0.0)
        energy: Mapped[float] = mapped_column(Float, default=100.0)
        world_size: Mapped[int] = mapped_column(Integer, default=100)
        agent_type: Mapped[str] = mapped_column(String, nullable=False)
        
        # Add JSON fields that parent class expects (stored as JSON dicts/lists)
        inventory: Mapped[Optional[Dict]] = mapped_column(JSON, nullable=True, default=dict)
        language: Mapped[Optional[Dict]] = mapped_column(JSON, nullable=True, default=dict)
        memory: Mapped[Optional[Dict]] = mapped_column(JSON, nullable=True, default=dict)
        personality: Mapped[Optional[Dict]] = mapped_column(JSON, nullable=True, default=dict)
        custom_attributes: Mapped[Optional[Dict]] = mapped_column(JSON, nullable=True, default=dict)

        def __repr__(self) -> str:
            return (
                f"<AgentState(id={self.id}, name={self.name}, type={self.agent_type})>"
            )

    _ArbiterAgentState = _StandaloneAgentState

# Export for use in this module
Base = _Base
ArbiterAgentState = _ArbiterAgentState


# ----------------------------------------------------------------------
#  AgentState – Joined-Table Inheritance Child
# ----------------------------------------------------------------------
class AgentState(ArbiterAgentState):
    """
    Omnicore extension of ArbiterAgentState.
    Uses joined-table inheritance to add Omnicore-specific fields.

    Inheritance chain:
    - ArbiterAgentState (parent, table: agent_state)
      └─ AgentState (this class, table: omnicore_agent_state)
         ├─ GeneratorAgentState (table: generator_agent_state)
         └─ SFEAgentState (table: sfe_agent_state)

    The ForeignKey to agent_state.id establishes the join relationship with the parent table.
    Child classes (GeneratorAgentState, SFEAgentState) reference omnicore_agent_state.id.

    Note: The parent ArbiterAgentState uses 'agent_type' as a regular column.
    For proper polymorphic inheritance, GeneratorAgentState and SFEAgentState
    should set agent_type appropriately in their values.
    """

    __tablename__ = "omnicore_agent_state"
    __table_args__ = {"extend_existing": True}

    # In SQLAlchemy 2.0+ joined-table inheritance, the id column MUST be explicitly
    # redeclared with a ForeignKey to establish the join relationship.
    # This is the standard pattern per SQLAlchemy documentation.
    id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("agent_state.id"),
        primary_key=True,
    )

    # --- NEW Omnicore v2 encrypted fields ---
    inventory_v2: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    language_v2: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    memory_v2: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    personality_v2: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    custom_attributes_v2: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:
        return f"<AgentState(id={self.id}, name={self.name}, type={getattr(self, 'agent_type', 'unknown')})>"


# ----------------------------------------------------------------------
#  ExplainAuditRecord – Independent audit table
# ----------------------------------------------------------------------
class ExplainAuditRecord(Base):
    """
    Immutable audit record for all system events.
    Includes Merkle root for tamper-proof integrity.
    """

    __tablename__ = "explain_audit"
    # Define indexes inline with table args for proper checkfirst behavior
    # Allow table redefinition during test collection to prevent
    # "Table 'explain_audit' is already defined" errors when modules are imported multiple times
    __table_args__ = (
        Index("ix_explain_audit_kind", "kind"),
        Index("ix_explain_audit_ts", "ts"),
        Index("ix_explain_audit_agent_id", "agent_id"),
        {"extend_existing": True}
    )

    uuid: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(String, nullable=False)
    ts: Mapped[float] = mapped_column(Float, nullable=False)
    hash: Mapped[str] = mapped_column(String, nullable=False)
    sim_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    context: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    custom_attributes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    rationale: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    simulation_outcomes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    explanation_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    root_merkle_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:
        return f"<ExplainAuditRecord(uuid={self.uuid!r}, kind={self.kind!r}, name={self.name!r})>"


# ----------------------------------------------------------------------
#  GeneratorAgentState – Polymorphic child
# ----------------------------------------------------------------------
class GeneratorAgentState(AgentState):
    """
    State for code-generating agents.
    """

    __tablename__ = "generator_agent_state"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("omnicore_agent_state.id"),
        primary_key=True,
    )

    generated_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    test_results: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    deployment_config: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    docs: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "generator_agent_state",
    }

    def __repr__(self) -> str:
        code_len = len(self.generated_code) if self.generated_code else 0
        return f"<GeneratorAgentState(id={self.id}, name={self.name}, code_len={code_len})>"


# ----------------------------------------------------------------------
#  SFEAgentState – Self-Fixing Engineer polymorphic child
# ----------------------------------------------------------------------
class SFEAgentState(AgentState):
    """
    State for self-fixing engineer agents.
    """

    __tablename__ = "sfe_agent_state"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("omnicore_agent_state.id"),
        primary_key=True,
    )

    fixed_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    analysis_report: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    trust_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "sfe_agent_state",
    }

    def __repr__(self) -> str:
        return (
            f"<SFEAgentState(id={self.id}, name={self.name}, trust={self.trust_score})>"
        )


# ----------------------------------------------------------------------
#  Indexes for performance
# ----------------------------------------------------------------------
# Note: ix_agentstate_name index is already defined in the parent
# arbiter/agent_state.py model on the agent_state.name column.
# GeneratorAgentState and SFEAgentState inherit this column via joined-table
# inheritance, so they automatically benefit from the parent's index.
# No additional indexes on 'name' are needed for child tables.
# Indexes for ExplainAuditRecord are now defined inline in the class's __table_args__


# ----------------------------------------------------------------------
#  Export public API
# ----------------------------------------------------------------------
__all__ = [
    "Base",
    "AgentState",
    "ExplainAuditRecord",
    "GeneratorAgentState",
    "SFEAgentState",
]
