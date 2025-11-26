# omnicore_engine/database/models.py
"""
SQLAlchemy ORM models for the Omnicore Omega Pro Engine.
Uses joined-table inheritance from the `arbiter` package.
All models are fully type-annotated and compatible with SQLAlchemy 2.0+.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Import the EXACT Base and parent model from arbiter
from arbiter.agent_state import AgentState as ArbiterAgentState
from arbiter.agent_state import Base
from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


# ----------------------------------------------------------------------
#  AgentState – Joined-Table Inheritance Child
# ----------------------------------------------------------------------
class AgentState(ArbiterAgentState):
    """
    Omnicore extension of ArbiterAgentState.
    DO NOT set __tablename__.
    DO NOT redeclare id, name, x, y, energy, world_size, agent_type, etc.
    Only add NEW columns that do NOT exist in the parent.

    Note: The parent ArbiterAgentState uses 'agent_type' as a regular column.
    For proper polymorphic inheritance, GeneratorAgentState and SFEAgentState
    should set agent_type appropriately in their values.
    """

    # --- NO __tablename__ ---
    # --- NO id column ---
    # --- NO world_size, agent_type, etc. if already in parent ---

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

    id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("agent_state.id"),
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

    id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("agent_state.id"),
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
# Note: ix_agent_state_name and ix_agentstate_name indexes are defined
# in the parent arbiter/agent_state.py model (line 137, 558).
# Only add indexes for columns that don't already have them.
Index("ix_explain_audit_kind", ExplainAuditRecord.kind)
Index("ix_explain_audit_ts", ExplainAuditRecord.ts)
Index("ix_explain_audit_agent_id", ExplainAuditRecord.agent_id)
Index("ix_generator_agent_state_name", GeneratorAgentState.name)
Index("ix_sfe_agent_state_name", SFEAgentState.name)


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
