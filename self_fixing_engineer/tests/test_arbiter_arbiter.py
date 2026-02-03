"""
Test suite for the Arbiter module.
This uses extensive mocking to avoid external dependencies.
"""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from cryptography.fernet import Fernet

# Add parent directory to path to import arbiter
current_dir = os.path.dirname(os.path.abspath(__file__))
arbiter_dir = os.path.dirname(current_dir)
parent_dir = os.path.dirname(arbiter_dir)  # self_fixing_engineer directory

# Only add self_fixing_engineer to path, NOT arbiter (arbiter.py would shadow the package)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Setup SQLAlchemy mocks
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# Import arbiter module - it should handle its own optional dependencies gracefully
from self_fixing_engineer.arbiter import arbiter

# ===== TEST FIXTURES =====


def generate_fernet_key():
    """Generate a properly formatted Fernet key."""
    return Fernet.generate_key().decode()


@pytest.fixture
def test_config():
    """Create a test configuration using a mock."""
    config = MagicMock()
    config.DATABASE_URL = "sqlite+aiosqlite:///:memory:"
    config.REDIS_URL = "redis://localhost:6379"
    config.ENCRYPTION_KEY = MagicMock()
    # Generate a proper base64-encoded 32-byte key
    config.ENCRYPTION_KEY.get_secret_value = MagicMock(
        return_value=generate_fernet_key()
    )
    config.REPORTS_DIRECTORY = "./test_reports"
    config.FRONTEND_URL = "http://localhost:3000"
    config.ARENA_PORT = 8080
    config.CODEBASE_PATHS = ["/test/path"]
    config.MEMORY_LIMIT = 40
    config.OMNICORE_URL = "http://localhost:8000"
    config.SLACK_WEBHOOK_URL = None
    config.ALERT_WEBHOOK_URL = None
    config.PROMETHEUS_GATEWAY = None
    config.AI_API_TIMEOUT = 30
    config.ENABLE_CRITICAL_FAILURES = False
    config.RL_MODEL_PATH = "./models/test.zip"
    config.REDIS_MAX_CONNECTIONS = 10
    config.ALPHA_VANTAGE_API_KEY = None
    config.PERIODIC_SCAN_INTERVAL_S = 3600
    config.WEBHOOK_URL = None
    config.ARBITER_MODES = ["sandbox", "live"]
    config.LLM_ADAPTER = "mock_ollama_adapter"
    config.OLLAMA_API_URL = "http://localhost:1144"
    config.LLM_MODEL = "llama3"
    config.ROLE_MAP = {"guest": 0, "user": 1, "explorer_user": 2, "admin": 3}
    config.SLACK_AUTH_TOKEN = None
    config.EMAIL_SMTP_SERVER = None
    config.EMAIL_SMTP_PORT = None
    config.EMAIL_SMTP_USERNAME = None
    config.EMAIL_SMTP_PASSWORD = None
    config.EMAIL_SENDER = None
    config.EMAIL_USE_TLS = False
    config.EMAIL_RECIPIENTS = {}
    config.SENTRY_DSN = None
    return config


@pytest.fixture
def mock_db_client():
    """Create a mock database client."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.check_health = AsyncMock(return_value={"status": "healthy"})
    client.log_error = AsyncMock()

    # Mock session
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()

    # Mock scalar_one_or_none to return None
    result_mock = MagicMock()
    result_mock.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=result_mock)

    client.get_session = MagicMock(return_value=session)
    return client


@pytest.fixture
def mock_engine(tmp_path):
    """Create a mock SQLAlchemy engine."""
    return create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db", echo=False)


# ===== SIMPLE UNIT TESTS =====


def test_arbiter_module_loaded():
    """Test that arbiter module was loaded."""
    # Import Arbiter class directly - this triggers lazy loading via __getattr__
    from self_fixing_engineer.arbiter import Arbiter
    assert Arbiter is not None, "Arbiter class should be importable"


def test_available_classes():
    """Test what classes are available in the arbiter module."""
    # List classes we expect to be available
    expected_classes = ["Arbiter", "Monitor", "SimulationEngine", "AgentStateManager"]
    available = []
    missing = []

    for cls_name in expected_classes:
        if hasattr(arbiter, cls_name):
            available.append(cls_name)
        else:
            missing.append(cls_name)

    print(f"Available classes: {available}")
    print(f"Missing classes: {missing}")

    # At minimum, Arbiter should be available
    assert hasattr(arbiter, "Arbiter"), "Arbiter class should be available"


@pytest.mark.asyncio
async def test_minimal_arbiter_creation(test_config, mock_engine):
    """Test creating an Arbiter instance with minimal parameters."""
    # Patch PostgresClient at the correct import path
    with patch("self_fixing_engineer.arbiter.models.postgres_client.PostgresClient") as mock_pg_class:
        mock_pg_client = MagicMock()
        mock_pg_client.connect = AsyncMock()
        mock_pg_client.disconnect = AsyncMock()
        mock_pg_client.check_health = AsyncMock(return_value={"status": "healthy"})
        mock_pg_client.get_session = MagicMock()
        mock_pg_class.return_value = mock_pg_client

        # Mock MultiModalPlugin to avoid initialization issues
        with patch("self_fixing_engineer.arbiter.arbiter.MultiModalPlugin") as mock_multimodal:
            mock_multimodal.return_value = MagicMock()

            # Mock Neo4jKnowledgeGraph to avoid Neo4j connection issues
            with patch("self_fixing_engineer.arbiter.arbiter.Neo4jKnowledgeGraph") as mock_neo4j:
                mock_neo4j.return_value = MagicMock()

                # Try to create an Arbiter instance
                try:
                    agent = arbiter.Arbiter(
                        name="TestAgent",
                        db_engine=mock_engine,
                        settings=test_config,
                        world_size=10,
                    )
                    assert agent.name == "TestAgent"
                    assert agent.world_size == 10
                except Exception as e:
                    pytest.fail(f"Failed to create Arbiter: {e}")


def test_monitor_class_exists(tmp_path):
    """Test if Monitor class exists and can be instantiated."""
    if hasattr(arbiter, "Monitor"):
        # Use a valid file path instead of just "test.log"
        log_file = str(tmp_path / "test.log")
        monitor = arbiter.Monitor(log_file, None)
        assert monitor.log_file == log_file
    else:
        pytest.skip("Monitor class not available")


def test_simulation_engine_exists():
    """Test if SimulationEngine class exists."""
    if hasattr(arbiter, "SimulationEngine"):
        engine = arbiter.SimulationEngine()
        assert engine.name == "SimulationEngine"
    else:
        pytest.skip("SimulationEngine class not available")


@pytest.mark.asyncio
async def test_simulation_run_if_exists():
    """Test SimulationEngine run method if class exists."""
    if hasattr(arbiter, "SimulationEngine"):
        engine = arbiter.SimulationEngine()
        result = await engine.run(
            {"type": "monte_carlo", "params": {"iterations": 5, "alpha": 1.0}},
            {"agent_name": "test", "energy": 100},
        )
        assert result["status"] == "success"
        assert "result" in result
    else:
        pytest.skip("SimulationEngine class not available")


def test_agent_state_manager_exists():
    """Test if AgentStateManager class exists."""
    if hasattr(arbiter, "AgentStateManager"):
        # Create a minimal mock config with proper Fernet key
        mock_config = MagicMock()
        mock_config.ENCRYPTION_KEY = MagicMock()
        mock_config.ENCRYPTION_KEY.get_secret_value = MagicMock(
            return_value=generate_fernet_key()
        )

        mock_db = MagicMock()
        # Mock the session for AgentStateManager
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock()
        mock_db.get_session = MagicMock(return_value=session)

        manager = arbiter.AgentStateManager(mock_db, "test", mock_config)
        assert manager.name == "test"
    else:
        pytest.skip("AgentStateManager class not available")


@pytest.mark.asyncio
async def test_arbiter_with_mocked_dependencies(
    test_config, mock_engine, mock_db_client
):
    """Test Arbiter with fully mocked dependencies."""
    # PostgresClient is imported within arbiter.py, so patch it there
    with patch("self_fixing_engineer.arbiter.arbiter.PostgresClient", return_value=mock_db_client):
        # Mock the Fernet class to avoid encryption issues
        with patch("self_fixing_engineer.arbiter.arbiter.Fernet") as mock_fernet_class:
            mock_fernet = MagicMock()
            mock_fernet.encrypt.return_value.decode.return_value = "encrypted"
            mock_fernet.decrypt.return_value.decode.return_value = "[]"
            mock_fernet_class.return_value = mock_fernet

            # Mock MultiModalPlugin to avoid initialization issues
            with patch("self_fixing_engineer.arbiter.arbiter.MultiModalPlugin") as mock_multimodal:
                mock_multimodal.return_value = MagicMock()

                # Mock Neo4jKnowledgeGraph to avoid Neo4j connection issues
                with patch("self_fixing_engineer.arbiter.arbiter.Neo4jKnowledgeGraph") as mock_neo4j:
                    mock_neo4j.return_value = MagicMock()

                    try:
                        agent = arbiter.Arbiter(
                            name="TestAgent",
                            db_engine=mock_engine,
                            settings=test_config,
                            world_size=100,
                            role="user",
                            agent_type="Arbiter",
                        )

                        # Test basic properties
                        assert agent.name == "TestAgent"
                        assert agent.world_size == 100

                        # Test health check
                        health = await agent.health_check()
                        assert "status" in health

                    except Exception as e:
                        pytest.fail(f"Failed to test Arbiter: {e}")


@pytest.mark.asyncio
async def test_monitor_log_action_if_exists(tmp_path):
    """Test Monitor log_action if class exists."""
    if hasattr(arbiter, "Monitor"):
        log_file = str(tmp_path / "test.json")
        monitor = arbiter.Monitor(log_file, None)

        await monitor.log_action(
            {"type": "test", "agent": "test_agent", "description": "test action"}
        )

        # Check file was created
        assert os.path.exists(log_file)
        with open(log_file, "r") as f:
            data = json.loads(f.readline())
            assert data["type"] == "test"
    else:
        pytest.skip("Monitor class not available")


def test_list_all_arbiter_attributes():
    """List all attributes available in the arbiter module for debugging."""
    attrs = dir(arbiter)
    classes = [attr for attr in attrs if attr[0].isupper() and not attr.startswith("_")]
    functions = [
        attr for attr in attrs if attr[0].islower() and not attr.startswith("_")
    ]

    print(f"\nClasses found in arbiter module: {classes}")
    print(f"Functions found in arbiter module: {functions}")

    # At least Arbiter should be present
    assert "Arbiter" in classes


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
