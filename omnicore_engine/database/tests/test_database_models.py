"""
Comprehensive test suite for omnicore_engine/database/models.py
"""

import hashlib
import os
import sys
from datetime import datetime

import pytest

# Import models from the correct package path to avoid duplicate Base objects
from omnicore_engine.database.models import (
    AgentState,
    Base,
    ExplainAuditRecord,
    GeneratorAgentState,
    SFEAgentState,
)
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def engine():
    """Create an in-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(engine):
    """Create a database session for testing."""
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestAgentState:
    """Test AgentState model."""

    def test_create_agent_state(self, session):
        """Test creating a basic AgentState."""
        agent = AgentState(
            name="test_agent",
            x=10,
            y=20,
            energy=100,
            world_size=1000,
            agent_type="explorer",
        )

        session.add(agent)
        session.commit()

        assert agent.id is not None
        assert agent.name == "test_agent"
        assert agent.x == 10
        assert agent.y == 20
        assert agent.energy == 100
        assert agent.world_size == 1000
        assert agent.agent_type == "explorer"

    def test_agent_state_defaults(self, session):
        """Test AgentState default values."""
        agent = AgentState(name="minimal_agent", x=0, y=0, energy=50, world_size=100)

        session.add(agent)
        session.commit()

        assert agent.agent_type == "generic"
        assert agent.inventory == {}
        assert agent.language == {}
        assert agent.memory == {}
        assert agent.personality == {}
        assert agent.custom_attributes == {}

    def test_agent_state_with_json_fields(self, session):
        """Test AgentState with JSON fields."""
        inventory = {"sword": 1, "potion": 3}
        language = {"en": True, "es": False}
        memory = ["found_treasure", "defeated_boss"]
        personality = {"courage": 0.8, "wisdom": 0.6}
        custom_attrs = {"level": 10, "class": "warrior"}

        agent = AgentState(
            name="complex_agent",
            x=100,
            y=200,
            energy=75,
            world_size=5000,
            agent_type="warrior",
            inventory=inventory,
            language=language,
            memory=memory,
            personality=personality,
            custom_attributes=custom_attrs,
        )

        session.add(agent)
        session.commit()

        # Retrieve and verify
        retrieved = session.query(AgentState).filter_by(name="complex_agent").first()
        assert retrieved.inventory == inventory
        assert retrieved.language == language
        assert retrieved.memory == memory
        assert retrieved.personality == personality
        assert retrieved.custom_attributes == custom_attrs

    def test_agent_state_unique_name_constraint(self, session):
        """Test that agent names must be unique."""
        agent1 = AgentState(name="unique_agent", x=0, y=0, energy=100, world_size=100)
        agent2 = AgentState(
            name="unique_agent", x=10, y=10, energy=50, world_size=100
        )  # Same name

        session.add(agent1)
        session.commit()

        session.add(agent2)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_agent_state_v2_encrypted_fields(self, session):
        """Test V2 encrypted fields."""
        encrypted_data = "encrypted_base64_string"

        agent = AgentState(
            name="encrypted_agent",
            x=0,
            y=0,
            energy=100,
            world_size=100,
            inventory_v2=encrypted_data,
            language_v2=encrypted_data,
            memory_v2=encrypted_data,
            personality_v2=encrypted_data,
            custom_attributes_v2=encrypted_data,
        )

        session.add(agent)
        session.commit()

        retrieved = session.query(AgentState).filter_by(name="encrypted_agent").first()
        assert retrieved.inventory_v2 == encrypted_data
        assert retrieved.language_v2 == encrypted_data
        assert retrieved.memory_v2 == encrypted_data
        assert retrieved.personality_v2 == encrypted_data
        assert retrieved.custom_attributes_v2 == encrypted_data

    # DISABLED: last_updated field does not exist in current model
    # def test_agent_state_last_updated(self, session):
    #     """Test last_updated timestamp."""
    #     agent = AgentState(name="timestamp_agent", x=0, y=0, energy=100, world_size=100)
    # 
    #     session.add(agent)
    #     session.commit()
    # 
    #     initial_timestamp = agent.last_updated
    #     assert initial_timestamp is not None
    # 
    #     # Update the agent
    #     agent.energy = 50
    #     session.commit()
    # 
    #     # Note: The onupdate trigger might not work automatically in SQLite
    #     # In production with proper database, this would update automatically

    def test_agent_state_repr(self):
        """Test string representation of AgentState."""
        agent = AgentState(
            name="repr_agent",
            x=15,
            y=25,
            energy=80,
            world_size=1000,
            agent_type="scout",
        )

        repr_str = repr(agent)
        assert "AgentState" in repr_str
        assert "repr_agent" in repr_str
        assert "scout" in repr_str
        # Note: x and y coordinates are not included in __repr__ format
        # The format is: <AgentState(id=..., name=..., type=...)>


class TestExplainAuditRecord:
    """Test ExplainAuditRecord model."""

    def test_create_audit_record(self, session):
        """Test creating a basic audit record."""
        record = ExplainAuditRecord(
            uuid="audit_123",
            kind="action",
            name="test_action",
            detail="Action performed successfully",
            ts=datetime.utcnow().timestamp(),
            hash=hashlib.sha256(b"test_data").hexdigest(),
        )

        session.add(record)
        session.commit()

        assert record.uuid == "audit_123"
        assert record.kind == "action"
        assert record.name == "test_action"
        assert record.detail == "Action performed successfully"

    def test_audit_record_with_optional_fields(self, session):
        """Test audit record with all optional fields."""
        record = ExplainAuditRecord(
            uuid="audit_full",
            kind="complex_action",
            name="full_test",
            detail="Detailed information",
            ts=datetime.utcnow().timestamp(),
            hash=hashlib.sha256(b"complex").hexdigest(),
            sim_id="sim_123",
            error="No error",
            agent_id="agent_456",
            context="Test context",
            custom_attributes="Custom data",
            rationale="Test rationale",
            simulation_outcomes="Positive outcomes",
            tenant_id="tenant_789",
            explanation_id="explain_abc",
            root_merkle_hash="merkle_hash_xyz",
        )

        session.add(record)
        session.commit()

        retrieved = (
            session.query(ExplainAuditRecord).filter_by(uuid="audit_full").first()
        )
        assert retrieved.sim_id == "sim_123"
        assert retrieved.agent_id == "agent_456"
        assert retrieved.tenant_id == "tenant_789"
        assert retrieved.explanation_id == "explain_abc"
        assert retrieved.root_merkle_hash == "merkle_hash_xyz"

    def test_audit_record_unique_uuid(self, session):
        """Test that audit UUIDs must be unique."""
        record1 = ExplainAuditRecord(
            uuid="unique_uuid",
            kind="action",
            name="test1",
            detail="Detail 1",
            ts=datetime.utcnow().timestamp(),
            hash="hash1",
        )
        record2 = ExplainAuditRecord(
            uuid="unique_uuid",  # Same UUID
            kind="action",
            name="test2",
            detail="Detail 2",
            ts=datetime.utcnow().timestamp(),
            hash="hash2",
        )

        session.add(record1)
        session.commit()

        session.add(record2)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_audit_record_repr(self):
        """Test string representation of ExplainAuditRecord."""
        record = ExplainAuditRecord(
            uuid="repr_audit",
            kind="test_kind",
            name="repr_test",
            detail="Test detail",
            ts=datetime.utcnow().timestamp(),
            hash="test_hash",
        )

        repr_str = repr(record)
        assert "ExplainAuditRecord" in repr_str
        assert "repr_audit" in repr_str
        assert "test_kind" in repr_str
        assert "repr_test" in repr_str


class TestGeneratorAgentState:
    """Test GeneratorAgentState model (inherits from AgentState)."""

    def test_create_generator_agent(self, session):
        """Test creating a GeneratorAgentState."""
        generator = GeneratorAgentState(
            name="generator_001",
            x=50,
            y=50,
            energy=100,
            world_size=1000,
            agent_type="generator",
            generated_code="def hello(): return 'world'",
            test_results={"test_hello": "passed"},
            deployment_config="production",
            docs="Function that returns 'world'",
        )

        session.add(generator)
        session.commit()

        assert generator.id is not None
        assert generator.name == "generator_001"
        assert generator.generated_code == "def hello(): return 'world'"
        assert generator.test_results == {"test_hello": "passed"}
        assert generator.deployment_config == "production"
        assert generator.docs == "Function that returns 'world'"

    def test_generator_inherits_agent_state_fields(self, session):
        """Test that GeneratorAgentState inherits all AgentState fields."""
        generator = GeneratorAgentState(
            name="generator_002",
            x=100,
            y=200,
            energy=75,
            world_size=5000,
            inventory={"tools": ["compiler", "debugger"]},
            memory=["generated_function_1", "generated_class_1"],
            generated_code="class MyClass: pass",
        )

        session.add(generator)
        session.commit()

        # Test inherited fields
        assert generator.x == 100
        assert generator.y == 200
        assert generator.energy == 75
        assert generator.inventory == {"tools": ["compiler", "debugger"]}
        assert generator.memory == ["generated_function_1", "generated_class_1"]

        # Test generator-specific fields
        assert generator.generated_code == "class MyClass: pass"


class TestSFEAgentState:
    """Test SFEAgentState model (inherits from AgentState)."""

    def test_create_sfe_agent(self, session):
        """Test creating an SFEAgentState."""
        sfe = SFEAgentState(
            name="sfe_001",
            x=30,
            y=40,
            energy=90,
            world_size=2000,
            agent_type="sfe",
            fixed_code="def fixed_func(): return True",
            analysis_report={"bugs_fixed": 5, "performance": "improved"},
            trust_score=0.95,
        )

        session.add(sfe)
        session.commit()

        assert sfe.id is not None
        assert sfe.name == "sfe_001"
        assert sfe.fixed_code == "def fixed_func(): return True"
        assert sfe.analysis_report == {"bugs_fixed": 5, "performance": "improved"}
        assert sfe.trust_score == 0.95

    def test_sfe_inherits_agent_state_fields(self, session):
        """Test that SFEAgentState inherits all AgentState fields."""
        sfe = SFEAgentState(
            name="sfe_002",
            x=150,
            y=250,
            energy=60,
            world_size=3000,
            personality={"analytical": 0.9, "cautious": 0.7},
            custom_attributes={"specialty": "security_analysis"},
            trust_score=0.88,
        )

        session.add(sfe)
        session.commit()

        # Test inherited fields
        assert sfe.x == 150
        assert sfe.y == 250
        assert sfe.energy == 60
        assert sfe.personality == {"analytical": 0.9, "cautious": 0.7}
        assert sfe.custom_attributes == {"specialty": "security_analysis"}

        # Test SFE-specific fields
        assert sfe.trust_score == 0.88


class TestModelRelationships:
    """Test relationships between models."""

    def test_multiple_agent_types_coexist(self, session):
        """Test that different agent types can coexist in the database."""
        base_agent = AgentState(name="base_agent", x=0, y=0, energy=100, world_size=100)

        generator = GeneratorAgentState(
            name="gen_agent",
            x=10,
            y=10,
            energy=100,
            world_size=100,
            generated_code="code",
        )

        sfe = SFEAgentState(
            name="sfe_agent", x=20, y=20, energy=100, world_size=100, trust_score=0.9
        )

        session.add_all([base_agent, generator, sfe])
        session.commit()

        # Query all agents
        all_agents = session.query(AgentState).all()
        assert len(all_agents) >= 3

        # Query specific types
        generators = session.query(GeneratorAgentState).all()
        assert len(generators) >= 1

        sfes = session.query(SFEAgentState).all()
        assert len(sfes) >= 1


class TestModelValidation:
    """Test model field validation and constraints."""

    def test_required_fields(self, session):
        """Test that required fields must be provided."""
        # AgentState without required fields
        agent = AgentState()  # Missing required fields

        session.add(agent)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_json_field_serialization(self, session):
        """Test JSON field serialization and deserialization."""
        complex_data = {
            "nested": {
                "list": [1, 2, 3],
                "dict": {"key": "value"},
                "bool": True,
                "null": None,
            }
        }

        agent = AgentState(
            name="json_test",
            x=0,
            y=0,
            energy=100,
            world_size=100,
            custom_attributes=complex_data,
        )

        session.add(agent)
        session.commit()

        # Clear session to force reload from database
        session.expire_all()

        retrieved = session.query(AgentState).filter_by(name="json_test").first()
        assert retrieved.custom_attributes == complex_data
        assert retrieved.custom_attributes["nested"]["list"] == [1, 2, 3]
        assert retrieved.custom_attributes["nested"]["dict"]["key"] == "value"


class TestModelQueries:
    """Test various query patterns with the models."""

    def test_filter_agents_by_type(self, session):
        """Test filtering agents by type."""
        agents_data = [
            ("agent1", "explorer"),
            ("agent2", "warrior"),
            ("agent3", "explorer"),
            ("agent4", "scout"),
        ]

        for name, agent_type in agents_data:
            agent = AgentState(
                name=name, x=0, y=0, energy=100, world_size=100, agent_type=agent_type
            )
            session.add(agent)

        session.commit()

        # Query explorers
        explorers = session.query(AgentState).filter_by(agent_type="explorer").all()
        assert len(explorers) == 2
        assert all(a.agent_type == "explorer" for a in explorers)

    def test_filter_agents_by_energy_range(self, session):
        """Test filtering agents by energy range."""
        for i in range(5):
            agent = AgentState(
                name=f"energy_agent_{i}",
                x=0,
                y=0,
                energy=i * 20,  # 0, 20, 40, 60, 80
                world_size=100,
            )
            session.add(agent)

        session.commit()

        # Query agents with energy >= 40
        high_energy = session.query(AgentState).filter(AgentState.energy >= 40).all()
        assert len(high_energy) == 3
        assert all(a.energy >= 40 for a in high_energy)

    def test_filter_audit_records_by_time(self, session):
        """Test filtering audit records by timestamp."""
        base_time = datetime.utcnow().timestamp()

        for i in range(5):
            record = ExplainAuditRecord(
                uuid=f"audit_{i}",
                kind="action",
                name=f"action_{i}",
                detail=f"Detail {i}",
                ts=base_time + (i * 3600),  # Add hours
                hash=f"hash_{i}",
            )
            session.add(record)

        session.commit()

        # Query records after base_time + 2 hours
        cutoff_time = base_time + (2 * 3600)
        recent_records = (
            session.query(ExplainAuditRecord)
            .filter(ExplainAuditRecord.ts > cutoff_time)
            .all()
        )

        assert len(recent_records) == 3
        assert all(r.ts > cutoff_time for r in recent_records)
