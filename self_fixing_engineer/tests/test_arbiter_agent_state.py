"""
Comprehensive tests for agent_state.py
Uses import hooks to properly isolate the module from its dependencies
"""

from __future__ import annotations

import importlib.util
import json
import random
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# ===========================
# SECTION 1: Import agent_state with mocked dependencies
# ===========================


@contextmanager
def mock_imports():
    """Context manager to mock imports during agent_state loading."""
    import builtins

    original_import = builtins.__import__

    def custom_import(name, *args, **kwargs):
        # Intercept config imports
        if name == "arbiter.config" or name == "config":
            import types

            mock_config = types.ModuleType(name)

            class MockArbiterConfig:
                DATABASE_URL = "sqlite:///:memory:"
                VALIDATION_TIMEOUT_SECONDS = 1.0
                AGENT_PERSONALITY_SCHEMA = {}
                AGENT_METADATA_SCHEMA = {}
                AGENT_STATE_TABLE = "agent_state"
                AGENT_METADATA_TABLE = "agent_metadata"
                MAX_MEMORY_SIZE = 1000
                MAX_INVENTORY_SIZE = 500

                def __call__(self):
                    return self

            mock_config.ArbiterConfig = MockArbiterConfig
            return mock_config

        # Intercept otel_config imports
        if name == "arbiter.otel_config":
            import types

            mock_otel = types.ModuleType(name)

            class MockTracer:
                def start_as_current_span(self, *args, **kwargs):
                    from contextlib import contextmanager

                    @contextmanager
                    def span():
                        yield

                    return span()

            def mock_get_tracer(name):
                return MockTracer()

            mock_otel.get_tracer = mock_get_tracer
            return mock_otel

        # Default to original import for everything else
        return original_import(name, *args, **kwargs)

    builtins.__import__ = custom_import
    try:
        yield
    finally:
        builtins.__import__ = original_import


# Load agent_state.py directly with mocked imports
def load_agent_state_module():
    """Load agent_state.py with mocked dependencies."""
    # Find the agent_state.py file
    current_dir = Path(__file__).parent
    agent_state_path = current_dir.parent / "arbiter" / "agent_state.py"

    if not agent_state_path.exists():
        # Try alternative path
        agent_state_path = current_dir.parent / "agent_state.py"

    if not agent_state_path.exists():
        raise FileNotFoundError(f"Cannot find agent_state.py at {agent_state_path}")

    # Create module spec
    spec = importlib.util.spec_from_file_location(
        "agent_state_test", str(agent_state_path)
    )
    module = importlib.util.module_from_spec(spec)

    # Mock the imports and load the module
    with mock_imports():
        spec.loader.exec_module(module)

    return module


# Load the module
agent_state_module = load_agent_state_module()

# Extract the classes and objects we need
AgentState = agent_state_module.AgentState
AgentMetadata = agent_state_module.AgentMetadata
Base = agent_state_module.Base
SCHEMA_VALIDATION_ERRORS = agent_state_module.SCHEMA_VALIDATION_ERRORS


# ===========================
# SECTION 2: Fixtures
# ===========================


@pytest.fixture
def engine():
    """Create in-memory SQLite engine for sync tests."""
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    """Provide transactional database session for sync tests."""
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s
        s.rollback()


@pytest.fixture
def async_engine():
    """Create async SQLite engine for async tests."""
    return create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)


@pytest.fixture
async def async_session(async_engine):
    """Provide async session for async tests."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(async_engine) as session:
        yield session

    await async_engine.dispose()


@pytest.fixture
def metrics():
    """Expose metrics counter for validation testing."""
    return SCHEMA_VALIDATION_ERRORS


# ===========================
# SECTION 3: Validation Tests (Sync)
# ===========================


class TestValidation:
    """Tests focused on field validation and metrics."""

    @pytest.mark.asyncio
    async def test_agent_state_validate_success(self, metrics):
        """Test successful validation of well-formed AgentState."""
        ok = AgentState(
            name="alpha",
            x=0.0,
            y=0.0,
            energy=98.5,
            inventory=[],
            language=["en"],
            memory=[],
            personality={"curiosity": 0.7},
            world_size=100,
        )
        await AgentState._validate_fields(ok)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "field, value, label",
        [
            ("inventory", "not-a-list", ("agent_state", "invalid_inventory")),
            ("language", "en", ("agent_state", "invalid_language")),
            ("memory", {"oops": True}, ("agent_state", "invalid_memory")),
            ("personality", [], ("agent_state", "invalid_personality")),
            ("world_size", 0, ("agent_state", "invalid_world_size")),
            ("energy", 200.0, ("agent_state", "invalid_energy")),
        ],
    )
    async def test_agent_state_validate_failures(self, field, value, label, metrics):
        """Test validation failures with proper metrics tracking."""
        good_agent = AgentState(
            name="beta",
            x=0.0,
            y=0.0,
            energy=50.0,
            inventory=[],
            language=[],
            memory=[],
            personality={},
            world_size=10,
        )

        table, error_type = label
        initial_count = metrics.get(table=table, error_type=error_type)

        with pytest.raises(ValueError):
            setattr(good_agent, field, value)

        final_count = metrics.get(table=table, error_type=error_type)
        assert final_count > initial_count

    @pytest.mark.asyncio
    async def test_agent_metadata_validate_success(self, metrics):
        """Test successful AgentMetadata validation."""
        ok = AgentMetadata(key="k1", value={"a": 1})
        await AgentMetadata._validate_fields(ok)


# ===========================
# SECTION 4: Database Tests (Sync)
# ===========================


class TestDatabaseSync:
    """Synchronous database integration tests."""

    def test_db_insert_valid_agent_state(self, session):
        """Test database insertion with JSON field serialization."""
        obj = AgentState(
            name="delta",
            x=1.0,
            y=2.0,
            energy=75.0,
            inventory=[],
            language=["en"],
            memory=[],
            personality={"agreeableness": 0.3},
            world_size=50,
        )
        session.add(obj)
        session.commit()
        assert obj.id is not None

    def test_db_check_constraint_energy(self, session):
        """Test field validation prevents invalid energy values."""
        with pytest.raises(ValueError, match="energy must be between"):
            AgentState(
                name="epsilon",
                x=0.0,
                y=0.0,
                energy=150.0,
                inventory=[],
                language=[],
                memory=[],
                personality={},
                world_size=10,
            )

    def test_json_field_serialization(self, session):
        """Test JSON field storage and retrieval mechanisms."""
        test_inventory = ["item1", "item2"]
        test_personality = {"openness": 0.8, "conscientiousness": 0.6}

        obj = AgentState(
            name="json_test",
            x=0.0,
            y=0.0,
            energy=50.0,
            inventory=test_inventory,
            language=["en", "es"],
            memory=["memory1"],
            personality=test_personality,
            world_size=100,
        )
        session.add(obj)
        session.commit()

        retrieved = session.query(AgentState).filter_by(name="json_test").first()
        assert retrieved is not None

        assert isinstance(retrieved.inventory, str)
        assert isinstance(retrieved.personality, str)

        assert json.loads(retrieved.inventory) == test_inventory
        assert json.loads(retrieved.personality) == test_personality


# ===========================
# SECTION 5: Async Model Tests
# ===========================


class TestAsyncModels:
    """Async SQLAlchemy model tests."""

    @pytest.mark.asyncio
    async def test_agent_state_init(self, async_session):
        """Test AgentState model initialization."""
        agent = AgentState(
            name="TestAgent",
            x=10.0,
            y=20.0,
            energy=50.0,
            inventory=["item1"],
            language=["en"],
            memory=["mem1"],
            personality={"trait": "bold"},
            world_size=200,
        )
        # Check values before committing (while still in memory)
        assert agent.name == "TestAgent"
        assert agent.x == 10.0

        # Commit to database
        async with async_session.begin():
            async_session.add(agent)

    @pytest.mark.asyncio
    async def test_agent_state_defaults(self, async_session):
        """Test AgentState defaults."""
        agent = AgentState(name="DefaultAgent")

        # SQLAlchemy column defaults are applied when the object is added to a session
        async_session.add(agent)
        await async_session.flush()  # Flush to apply defaults without committing

        # Now check the defaults
        assert agent.x == 0.0
        assert agent.energy == 100.0

        # Check JSON fields - they should be stored as strings
        assert agent.inventory == "[]"  # Default empty list as JSON string
        assert agent.language == "[]"
        assert agent.memory == "[]"
        assert agent.personality == "{}"

        # Commit to database
        await async_session.commit()

    @pytest.mark.asyncio
    async def test_agent_state_unique_name(self, async_session):
        """Test unique name constraint."""
        agent1 = AgentState(name="UniqueAgent")
        async with async_session.begin():
            async_session.add(agent1)

        agent2 = AgentState(name="UniqueAgent")
        with pytest.raises(IntegrityError):
            async with async_session.begin():
                async_session.add(agent2)

    @pytest.mark.asyncio
    async def test_concurrent_operations(self, async_session):
        """Test concurrent AgentState operations."""
        agents = []
        for i in range(10):
            agent = AgentState(name=f"Agent{i}", x=float(random.randint(0, 100)))
            agents.append(agent)

        # Add all agents in a single transaction
        async with async_session.begin():
            for agent in agents:
                async_session.add(agent)

        # Verify they were all added
        result = await async_session.execute(select(AgentState))
        retrieved_agents = result.scalars().all()
        assert len(retrieved_agents) == 10


# ===========================
# SECTION 6: Representation Tests
# ===========================


class TestRepresentations:
    """Test model string representations."""

    def test_agent_state_repr(self):
        """Test AgentState __repr__ method."""
        agent = AgentState(
            id=1, name="TestAgent", x=10.0, y=20.0, energy=50.0, world_size=100
        )
        repr_str = repr(agent)
        # Check that key components are present
        assert "AgentState" in repr_str
        assert "id=1" in repr_str
        assert "name='TestAgent'" in repr_str
        assert "x=10.0" in repr_str
        assert "y=20.0" in repr_str
        assert "energy=50.0" in repr_str

    def test_agent_metadata_repr(self):
        """Test AgentMetadata __repr__ method."""
        metadata = AgentMetadata(id=1, key="test_key", value={})
        repr_str = repr(metadata)
        # Check that key components are present
        assert "AgentMetadata" in repr_str
        assert "id=1" in repr_str
        assert "key='test_key'" in repr_str
