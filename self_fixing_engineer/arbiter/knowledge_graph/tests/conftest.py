"""
Test runner setup and configuration for knowledge_graph module
Place this file as: arbiter/knowledge_graph/tests/conftest.py
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add the parent directories to Python path
current_dir = Path(__file__).parent
knowledge_graph_dir = current_dir.parent
arbiter_dir = knowledge_graph_dir.parent
project_root = arbiter_dir.parent

# Add paths to sys.path
sys.path.insert(0, str(knowledge_graph_dir))
sys.path.insert(0, str(arbiter_dir))
sys.path.insert(0, str(project_root))


@pytest.fixture(autouse=True)
def mock_agent_metrics(monkeypatch):
    """Automatically mock AGENT_METRICS for all tests"""
    mock_metrics = {
        "agent_predict_total": MagicMock(
            labels=MagicMock(return_value=MagicMock(inc=MagicMock()))
        ),
        "agent_predict_success": MagicMock(
            labels=MagicMock(return_value=MagicMock(inc=MagicMock()))
        ),
        "agent_predict_errors": MagicMock(
            labels=MagicMock(return_value=MagicMock(inc=MagicMock()))
        ),
        "agent_predict_duration_seconds": MagicMock(
            labels=MagicMock(return_value=MagicMock(observe=MagicMock()))
        ),
        "agent_step_duration_seconds": MagicMock(
            labels=MagicMock(return_value=MagicMock(observe=MagicMock()))
        ),
        "agent_team_task_duration_seconds": MagicMock(observe=MagicMock()),
        "agent_team_task_errors_total": MagicMock(
            labels=MagicMock(return_value=MagicMock(inc=MagicMock()))
        ),
        "agent_creation_duration_seconds": MagicMock(observe=MagicMock()),
        "llm_calls_total": MagicMock(
            labels=MagicMock(return_value=MagicMock(inc=MagicMock()))
        ),
        "llm_errors_total": MagicMock(
            labels=MagicMock(return_value=MagicMock(inc=MagicMock()))
        ),
        "llm_call_latency_seconds": MagicMock(
            labels=MagicMock(return_value=MagicMock(observe=MagicMock()))
        ),
        "state_backend_operations_total": MagicMock(
            labels=MagicMock(return_value=MagicMock(inc=MagicMock()))
        ),
        "state_backend_errors_total": MagicMock(
            labels=MagicMock(return_value=MagicMock(inc=MagicMock()))
        ),
        "state_backend_latency_seconds": MagicMock(
            labels=MagicMock(return_value=MagicMock(observe=MagicMock()))
        ),
        "meta_learning_corrections_logged_total": MagicMock(inc=MagicMock()),
        "meta_learning_train_duration_seconds": MagicMock(observe=MagicMock()),
        "meta_learning_train_errors_total": MagicMock(inc=MagicMock()),
        "sensitive_data_redaction_total": MagicMock(
            labels=MagicMock(return_value=MagicMock(inc=MagicMock()))
        ),
        "multimodal_data_processed_total": MagicMock(
            labels=MagicMock(return_value=MagicMock(inc=MagicMock()))
        ),
        "mm_processor_failures_total": MagicMock(
            labels=MagicMock(return_value=MagicMock(inc=MagicMock()))
        ),
        "agent_last_success_timestamp": MagicMock(
            labels=MagicMock(return_value=MagicMock(set=MagicMock()))
        ),
        "agent_last_error_timestamp": MagicMock(
            labels=MagicMock(return_value=MagicMock(set=MagicMock()))
        ),
        "agent_active_sessions_current": MagicMock(inc=MagicMock(), dec=MagicMock()),
        "agent_heartbeat_timestamp": MagicMock(
            labels=MagicMock(return_value=MagicMock(set=MagicMock()))
        ),
    }
    monkeypatch.setattr("arbiter.knowledge_graph.core.AGENT_METRICS", mock_metrics)
    monkeypatch.setattr("arbiter.knowledge_graph.utils.AGENT_METRICS", mock_metrics)
    return mock_metrics


@pytest.fixture(autouse=True)
def mock_meta_learning_persistence(monkeypatch):
    """Mock file operations for MetaLearning to prevent loading persisted data"""
    import builtins

    original_open = builtins.open

    def mock_open_wrapper(*args, **kwargs):
        # If trying to open meta_learning.pkl for reading, raise FileNotFoundError
        if args and "meta_learning.pkl" in str(args[0]):
            if "rb" in str(args[1] if len(args) > 1 else kwargs.get("mode", "r")):
                raise FileNotFoundError("No persisted meta-learning data")
        return original_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", mock_open_wrapper)


@pytest.fixture
def mock_config(monkeypatch):
    """Mock Config for all tests"""
    from arbiter.knowledge_graph import config

    monkeypatch.setattr(config.Config, "REDIS_URL", None)
    monkeypatch.setattr(config.Config, "POSTGRES_DB_URL", None)
    monkeypatch.setattr(config.Config, "MAX_MM_DATA_SIZE_MB", 100)
    monkeypatch.setattr(config.Config, "CACHE_EXPIRATION_SECONDS", 3600)
    monkeypatch.setattr(
        config.Config, "PII_SENSITIVE_KEYS", ["password", "email", "ssn"]
    )
    monkeypatch.setattr(config.Config, "GDPR_MODE", True)
    monkeypatch.setattr(config.Config, "DEFAULT_PROVIDER", "openai")
    monkeypatch.setattr(config.Config, "DEFAULT_LLM_MODEL", "gpt-3.5-turbo")
    monkeypatch.setattr(config.Config, "DEFAULT_TEMP", 0.7)
    monkeypatch.setattr(config.Config, "DEFAULT_LANGUAGE", "en")
    monkeypatch.setattr(config.Config, "MEMORY_WINDOW", 5)
    monkeypatch.setattr(config.Config, "MAX_META_LEARNING_CORRECTIONS", 10)
    monkeypatch.setattr(config.Config, "MAX_CORRECTION_ENTRY_SIZE", 10000)
    monkeypatch.setattr(config.Config, "MIN_RECORDS_FOR_TRAINING", 2)
    monkeypatch.setattr(config.Config, "LLM_RATE_LIMIT_CALLS", 10)
    monkeypatch.setattr(config.Config, "LLM_RATE_LIMIT_PERIOD", 60)
    monkeypatch.setattr(config.Config, "FALLBACK_PROVIDER", None)
    monkeypatch.setattr(
        config.Config, "FALLBACK_LLM_CONFIG", {"model": "claude-2", "temperature": 0.7}
    )
    monkeypatch.setattr(
        config.Config, "AUDIT_LEDGER_URL", "http://localhost:8000/audit"
    )
    monkeypatch.setattr(config.Config, "AUDIT_SIGNING_PUBLIC_KEY", None)

    return config.Config


@pytest.fixture
def mock_external_clients(monkeypatch):
    """Mock external client classes"""
    mock_redis_client = MagicMock()
    mock_redis_instance = AsyncMock()
    mock_redis_instance.ping = AsyncMock(return_value=True)
    mock_redis_instance.get = AsyncMock(return_value=None)
    mock_redis_instance.set = AsyncMock(return_value=True)
    mock_redis_instance.setex = AsyncMock(return_value=True)
    mock_redis_client.return_value = mock_redis_instance

    mock_postgres_client = MagicMock()
    mock_postgres_instance = AsyncMock()
    mock_postgres_instance.connect = AsyncMock()
    mock_postgres_instance.save = AsyncMock()
    mock_postgres_instance.load = AsyncMock(return_value=None)
    mock_postgres_client.return_value = mock_postgres_instance

    mock_audit_client = MagicMock()
    mock_audit_instance = AsyncMock()
    mock_audit_instance.log_event = AsyncMock(return_value=True)
    mock_audit_client.return_value = mock_audit_instance

    monkeypatch.setattr("arbiter.knowledge_graph.core.RedisClient", mock_redis_client)
    monkeypatch.setattr(
        "arbiter.knowledge_graph.core.PostgresClient", mock_postgres_client
    )
    monkeypatch.setattr(
        "arbiter.knowledge_graph.core.AuditLedgerClient", mock_audit_client
    )

    return {
        "redis": mock_redis_client,
        "postgres": mock_postgres_client,
        "audit": mock_audit_client,
    }


@pytest.fixture
def mock_llm_providers(monkeypatch):
    """Mock LLM provider classes"""
    mock_openai = MagicMock()
    mock_openai.return_value = MagicMock()

    mock_anthropic = MagicMock()
    mock_anthropic.return_value = MagicMock()

    mock_google = MagicMock()
    mock_google.return_value = MagicMock()

    mock_xai = MagicMock()
    mock_xai.return_value = MagicMock()

    monkeypatch.setattr("arbiter.knowledge_graph.core.ChatOpenAI", mock_openai)
    monkeypatch.setattr("arbiter.knowledge_graph.core.ChatAnthropic", mock_anthropic)
    monkeypatch.setattr(
        "arbiter.knowledge_graph.core.ChatGoogleGenerativeAI", mock_google
    )
    monkeypatch.setattr("arbiter.knowledge_graph.core.ChatXAI", mock_xai)

    return {
        "openai": mock_openai,
        "anthropic": mock_anthropic,
        "google": mock_google,
        "xai": mock_xai,
    }


@pytest.fixture
def mock_multimodal_processors(monkeypatch):
    """Mock multimodal processor classes"""
    mock_default_processor = MagicMock()
    mock_default_processor.return_value = MagicMock()
    mock_default_processor.return_value.summarize = AsyncMock(return_value="Summary")

    mock_default_strategy = MagicMock()
    mock_default_strategy.return_value = MagicMock()
    mock_default_strategy.return_value.create_agent_prompt = AsyncMock(
        return_value="Prompt"
    )

    mock_concise_strategy = MagicMock()
    mock_concise_strategy.return_value = MagicMock()
    mock_concise_strategy.return_value.create_agent_prompt = AsyncMock(
        return_value="Concise Prompt"
    )

    monkeypatch.setattr(
        "arbiter.knowledge_graph.core.DefaultMultiModalProcessor",
        mock_default_processor,
    )
    monkeypatch.setattr(
        "arbiter.knowledge_graph.core.DefaultPromptStrategy", mock_default_strategy
    )
    monkeypatch.setattr(
        "arbiter.knowledge_graph.core.ConcisePromptStrategy", mock_concise_strategy
    )

    return {
        "processor": mock_default_processor,
        "default_strategy": mock_default_strategy,
        "concise_strategy": mock_concise_strategy,
    }


@pytest.fixture
def mock_langchain_components(monkeypatch):
    """Mock langchain components"""
    monkeypatch.setattr(
        "arbiter.knowledge_graph.core.messages_to_dict", MagicMock(return_value=[])
    )
    monkeypatch.setattr(
        "arbiter.knowledge_graph.core.messages_from_dict", MagicMock(return_value=[])
    )
    monkeypatch.setattr(
        "arbiter.knowledge_graph.core.load_persona_dict",
        MagicMock(
            return_value={
                "default": "Test persona",
                "expert": "Expert persona",
                "scrum_master": "Scrum Master persona",
            }
        ),
    )


@pytest.fixture
def mock_logger():
    """Mock logger for tests"""
    return MagicMock()


# Ensure asyncio event loop is available for all async tests
@pytest.fixture(scope="function")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    import asyncio

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# Skip tests that require actual external services
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: mark test as an integration test (requires external services)",
    )
    config.addinivalue_line("markers", "slow: mark test as slow running")


# Automatically use all fixtures for tests
@pytest.fixture(autouse=True)
def setup_test_environment(
    mock_config,
    mock_external_clients,
    mock_llm_providers,
    mock_multimodal_processors,
    mock_langchain_components,
    mock_agent_metrics,
):
    """Automatically set up the test environment with all mocks"""
    yield
