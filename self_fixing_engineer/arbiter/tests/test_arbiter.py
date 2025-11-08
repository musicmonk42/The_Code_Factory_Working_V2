"""
Test suite for the Arbiter module.
This uses extensive mocking to avoid external dependencies.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock, Mock, PropertyMock, ANY, create_autospec
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
import tempfile
import importlib
import base64
from cryptography.fernet import Fernet

# Add parent directory to path to import arbiter
current_dir = os.path.dirname(os.path.abspath(__file__))
arbiter_dir = os.path.dirname(current_dir)
parent_dir = os.path.dirname(arbiter_dir)

for path in [parent_dir, arbiter_dir]:
    if path not in sys.path:
        sys.path.insert(0, path)

# Create a more comprehensive mock for numpy
class MockNdarray:
    def __init__(self, data, dtype=None):
        self.data = data
        self.dtype = dtype or float
        self.shape = (len(data),) if hasattr(data, '__len__') else ()
    
    def all(self):
        return True

mock_np = MagicMock()
mock_np.array = lambda x, dtype=None: MockNdarray(x, dtype=dtype)
mock_np.random = MagicMock()
mock_np.random.rand = Mock(return_value=0.5)
mock_np.random.uniform = Mock(return_value=5.0)
mock_np.clip = Mock(side_effect=lambda x, min_val, max_val: x)
mock_np.zeros = lambda shape: MockNdarray([0] * shape if isinstance(shape, int) else [0] * shape[0])
mock_np.float32 = float
mock_np.mean = Mock(return_value=0.5)
mock_np.std = Mock(return_value=0.1)
sys.modules['numpy'] = mock_np

# Setup SQLAlchemy mocks
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# Create comprehensive mocks for all dependencies
def setup_mocks():
    """Setup all necessary mocks before importing arbiter."""
    
    # Pydantic mocks
    class MockSecretStr:
        def __init__(self, value):
            self._value = value
        def get_secret_value(self):
            return self._value
    
    class MockField:
        def __init__(self, default=None, **kwargs):
            self.default = default
    
    mock_pydantic = MagicMock()
    mock_pydantic.BaseModel = MagicMock
    mock_pydantic.HttpUrl = str
    mock_pydantic.SecretStr = MockSecretStr
    mock_pydantic.Field = MockField
    mock_pydantic.validator = lambda *args, **kwargs: lambda f: f
    sys.modules['pydantic'] = mock_pydantic
    
    # Pydantic settings mock
    mock_pydantic_settings = MagicMock()
    mock_pydantic_settings.BaseSettings = MagicMock
    sys.modules['pydantic_settings'] = mock_pydantic_settings
    
    # All other dependencies
    mocks = {
        'dotenv': MagicMock(),
        'tenacity': MagicMock(),
        'httpx': MagicMock(),
        'aiohttp': MagicMock(),
        'aioredis': MagicMock(),
        'prometheus_client': MagicMock(),
        'sentry_sdk': MagicMock(),
        'sklearn': MagicMock(),
        'sklearn.linear_model': MagicMock(),
        'sklearn.model_selection': MagicMock(),
        'uvloop': MagicMock(),
        'aiolimiter': MagicMock(),
        'cryptography': MagicMock(),
        'cryptography.fernet': MagicMock(),
        'gymnasium': MagicMock(),
        'stable_baselines3': MagicMock(),
        'stable_baselines3.common': MagicMock(),
        'stable_baselines3.common.env_util': MagicMock(),
        'stable_baselines3.common.evaluation': MagicMock(),
        'stable_baselines3.common.vec_env': MagicMock(),
    }
    
    # Configure specific mock behaviors
    mocks['dotenv'].load_dotenv = MagicMock()
    mocks['dotenv'].dotenv_values = MagicMock(return_value={})
    
    mocks['tenacity'].retry = lambda *args, **kwargs: lambda f: f
    mocks['tenacity'].stop_after_attempt = MagicMock()
    mocks['tenacity'].wait_exponential = MagicMock()
    
    # Prometheus mocks
    counter_mock = MagicMock()
    counter_mock.labels = MagicMock(return_value=MagicMock())
    mocks['prometheus_client'].Counter = MagicMock(return_value=counter_mock)
    
    gauge_mock = MagicMock()
    gauge_mock.labels = MagicMock(return_value=MagicMock())
    mocks['prometheus_client'].Gauge = MagicMock(return_value=gauge_mock)
    
    summary_mock = MagicMock()
    summary_mock.labels = MagicMock(return_value=MagicMock())
    mocks['prometheus_client'].Summary = MagicMock(return_value=summary_mock)
    
    # Cryptography mocks - Keep the original Fernet for now
    # We'll handle it differently in the fixtures
    
    # Gym mocks
    class MockSpaces:
        class Discrete:
            def __init__(self, n):
                self.n = n
        class Box:
            def __init__(self, low, high, shape, dtype):
                self.low = low
                self.high = high
                self.shape = shape
                self.dtype = dtype
    
    mocks['gymnasium'].Env = object
    mocks['gymnasium'].spaces = MockSpaces()
    
    # Apply all mocks
    for name, mock_obj in mocks.items():
        sys.modules[name] = mock_obj
    
    # Mock arbiter submodules
    arbiter_mocks = {
        'arbiter.feedback': MagicMock(),
        'arbiter.agent_state': MagicMock(),
        'arbiter.monitoring': MagicMock(),
        'arbiter.human_loop': MagicMock(),
        'arbiter.config': MagicMock(),
        'arbiter.utils': MagicMock(),
        'arbiter.arbiter_plugin_registry': MagicMock(),
        'arbiter.models.postgres_client': MagicMock(),
        'arbiter.models.knowledge_graph_db': MagicMock(),
        'arbiter.plugins.multi_modal_plugin': MagicMock(),
        'arbiter.codebase_analyzer': MagicMock(),
        'arbiter.metrics': MagicMock(),
        'simulation.simulation_module': MagicMock(),
        'envs.code_health_env': MagicMock(),
        'envs.evolution': MagicMock(),
    }
    
    # Setup metrics module
    def mock_get_or_create_counter(name, desc, labels=None):
        counter = MagicMock()
        counter.labels = MagicMock(return_value=MagicMock())
        return counter
    
    def mock_get_or_create_gauge(name, desc, labels=None):
        gauge = MagicMock()
        gauge.labels = MagicMock(return_value=MagicMock())
        gauge.set = MagicMock()
        return gauge
    
    def mock_get_or_create_summary(name, desc, labels=None):
        summary = MagicMock()
        summary.labels = MagicMock(return_value=MagicMock())
        summary.observe = MagicMock()
        return summary
    
    def mock_get_or_create_histogram(name, desc, labels=None, buckets=None):
        histogram = MagicMock()
        histogram.labels = MagicMock(return_value=MagicMock())
        histogram.observe = MagicMock()
        return histogram
    
    arbiter_mocks['arbiter.metrics'].get_or_create_counter = mock_get_or_create_counter
    arbiter_mocks['arbiter.metrics'].get_or_create_gauge = mock_get_or_create_gauge
    arbiter_mocks['arbiter.metrics'].get_or_create_summary = mock_get_or_create_summary
    arbiter_mocks['arbiter.metrics'].get_or_create_histogram = mock_get_or_create_histogram
    
    # Setup arbiter specific mocks
    arbiter_mocks['arbiter.agent_state'].Base = Base
    arbiter_mocks['arbiter.agent_state'].AgentStateModel = MagicMock()
    
    registry_mock = MagicMock()
    registry_mock.get = MagicMock(return_value=None)
    registry_mock.get_metadata = MagicMock(return_value=None)
    registry_mock.register_instance = MagicMock()
    arbiter_mocks['arbiter.arbiter_plugin_registry'].registry = registry_mock
    arbiter_mocks['arbiter.arbiter_plugin_registry'].PLUGIN_REGISTRY = registry_mock
    arbiter_mocks['arbiter.arbiter_plugin_registry'].PlugInKind = MagicMock()
    arbiter_mocks['arbiter.arbiter_plugin_registry'].PluginBase = MagicMock()
    
    # Mock PostgresClient
    class MockPostgresClient:
        def __init__(self, *args, **kwargs):
            self.connect = AsyncMock()
            self.disconnect = AsyncMock()
            self.check_health = AsyncMock(return_value={"status": "healthy"})
            self.log_error = AsyncMock()
            self.get_session = MagicMock()
    
    arbiter_mocks['arbiter.models.postgres_client'].PostgresClient = MockPostgresClient
    arbiter_mocks['envs.evolution'].evolve_configs = MagicMock(return_value={"test": "config"})
    
    for name, mock_obj in arbiter_mocks.items():
        sys.modules[name] = mock_obj
    
    return mocks, arbiter_mocks

# Setup all mocks before import
mocks, arbiter_mocks = setup_mocks()

# Now import arbiter.arbiter specifically
from arbiter import arbiter

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
    config.ENCRYPTION_KEY.get_secret_value = MagicMock(return_value=generate_fernet_key())
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
    assert arbiter is not None
    assert hasattr(arbiter, 'Arbiter')

def test_available_classes():
    """Test what classes are available in the arbiter module."""
    # List classes we expect to be available
    expected_classes = ['Arbiter', 'Monitor', 'SimulationEngine', 'AgentStateManager']
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
    assert hasattr(arbiter, 'Arbiter'), "Arbiter class should be available"

@pytest.mark.asyncio
async def test_minimal_arbiter_creation(test_config, mock_engine):
    """Test creating an Arbiter instance with minimal parameters."""
    # Patch PostgresClient at the correct import path
    with patch('arbiter.models.postgres_client.PostgresClient') as mock_pg_class:
        mock_pg_client = MagicMock()
        mock_pg_client.connect = AsyncMock()
        mock_pg_client.disconnect = AsyncMock()
        mock_pg_client.check_health = AsyncMock(return_value={"status": "healthy"})
        mock_pg_client.get_session = MagicMock()
        mock_pg_class.return_value = mock_pg_client
        
        # Mock MultiModalPlugin to avoid initialization issues
        with patch('arbiter.arbiter.MultiModalPlugin') as mock_multimodal:
            mock_multimodal.return_value = MagicMock()
            
            # Mock Neo4jKnowledgeGraph to avoid Neo4j connection issues
            with patch('arbiter.arbiter.Neo4jKnowledgeGraph') as mock_neo4j:
                mock_neo4j.return_value = MagicMock()
                
                # Try to create an Arbiter instance
                try:
                    agent = arbiter.Arbiter(
                        name="TestAgent",
                        db_engine=mock_engine,
                        settings=test_config,
                        world_size=10
                    )
                    assert agent.name == "TestAgent"
                    assert agent.world_size == 10
                except Exception as e:
                    pytest.fail(f"Failed to create Arbiter: {e}")

def test_monitor_class_exists(tmp_path):
    """Test if Monitor class exists and can be instantiated."""
    if hasattr(arbiter, 'Monitor'):
        # Use a valid file path instead of just "test.log"
        log_file = str(tmp_path / "test.log")
        monitor = arbiter.Monitor(log_file, None)
        assert monitor.log_file == log_file
    else:
        pytest.skip("Monitor class not available")

def test_simulation_engine_exists():
    """Test if SimulationEngine class exists."""
    if hasattr(arbiter, 'SimulationEngine'):
        engine = arbiter.SimulationEngine()
        assert engine.name == "SimulationEngine"
    else:
        pytest.skip("SimulationEngine class not available")

@pytest.mark.asyncio
async def test_simulation_run_if_exists():
    """Test SimulationEngine run method if class exists."""
    if hasattr(arbiter, 'SimulationEngine'):
        engine = arbiter.SimulationEngine()
        result = await engine.run(
            {"type": "monte_carlo", "params": {"iterations": 5, "alpha": 1.0}},
            {"agent_name": "test", "energy": 100}
        )
        assert result["status"] == "success"
        assert "result" in result
    else:
        pytest.skip("SimulationEngine class not available")

def test_agent_state_manager_exists():
    """Test if AgentStateManager class exists."""
    if hasattr(arbiter, 'AgentStateManager'):
        # Create a minimal mock config with proper Fernet key
        mock_config = MagicMock()
        mock_config.ENCRYPTION_KEY = MagicMock()
        mock_config.ENCRYPTION_KEY.get_secret_value = MagicMock(return_value=generate_fernet_key())
        
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
async def test_arbiter_with_mocked_dependencies(test_config, mock_engine, mock_db_client):
    """Test Arbiter with fully mocked dependencies."""
    # PostgresClient is imported within arbiter.py, so patch it there
    with patch('arbiter.arbiter.PostgresClient', return_value=mock_db_client):
        # Mock the Fernet class to avoid encryption issues
        with patch('arbiter.arbiter.Fernet') as mock_fernet_class:
            mock_fernet = MagicMock()
            mock_fernet.encrypt.return_value.decode.return_value = "encrypted"
            mock_fernet.decrypt.return_value.decode.return_value = "[]"
            mock_fernet_class.return_value = mock_fernet
            
            # Mock MultiModalPlugin to avoid initialization issues
            with patch('arbiter.arbiter.MultiModalPlugin') as mock_multimodal:
                mock_multimodal.return_value = MagicMock()
                
                # Mock Neo4jKnowledgeGraph to avoid Neo4j connection issues
                with patch('arbiter.arbiter.Neo4jKnowledgeGraph') as mock_neo4j:
                    mock_neo4j.return_value = MagicMock()
                    
                    try:
                        agent = arbiter.Arbiter(
                            name="TestAgent",
                            db_engine=mock_engine,
                            settings=test_config,
                            world_size=100,
                            role="user",
                            agent_type="Arbiter"
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
    if hasattr(arbiter, 'Monitor'):
        log_file = str(tmp_path / "test.json")
        monitor = arbiter.Monitor(log_file, None)
        
        await monitor.log_action({
            "type": "test",
            "agent": "test_agent",
            "description": "test action"
        })
        
        # Check file was created
        assert os.path.exists(log_file)
        with open(log_file, 'r') as f:
            data = json.loads(f.readline())
            assert data["type"] == "test"
    else:
        pytest.skip("Monitor class not available")

def test_list_all_arbiter_attributes():
    """List all attributes available in the arbiter module for debugging."""
    attrs = dir(arbiter)
    classes = [attr for attr in attrs if attr[0].isupper() and not attr.startswith('_')]
    functions = [attr for attr in attrs if attr[0].islower() and not attr.startswith('_')]
    
    print(f"\nClasses found in arbiter module: {classes}")
    print(f"Functions found in arbiter module: {functions}")
    
    # At least Arbiter should be present
    assert 'Arbiter' in classes

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])