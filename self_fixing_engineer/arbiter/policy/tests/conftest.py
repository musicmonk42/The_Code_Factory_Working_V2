import os
import sys
import pytest
import logging
from unittest.mock import MagicMock, Mock

def pytest_configure(config):
    """Configure pytest environment before tests run."""
    # Set test environment
    os.environ['ENVIRONMENT'] = 'test'
    
    # Disable OpenTelemetry endpoint to prevent connection attempts
    os.environ['OTLP_ENDPOINT'] = ''
    
    # Disable Redis for tests unless explicitly testing Redis functionality
    os.environ['REDIS_URL'] = ''
    
    # Set other test-specific environment variables
    os.environ['APP_ENV'] = 'test'
    os.environ['PAUSE_CIRCUIT_BREAKER_TASKS'] = 'true'
    os.environ['PAUSE_METRIC_REFRESH_TASKS'] = 'true'
    os.environ['PAUSE_POLICY_REFRESH_TASKS'] = 'true'
    
    # Configure logging to reduce noise during tests
    logging.getLogger('opentelemetry').setLevel(logging.ERROR)
    logging.getLogger('grpc').setLevel(logging.ERROR)
    logging.getLogger('urllib3').setLevel(logging.ERROR)
    
    # Mock external dependencies that aren't available in test environment
    if 'arbiter.policy.guardrails.audit_log' not in sys.modules:
        sys.modules['arbiter.policy.guardrails.audit_log'] = MagicMock()
    
    if 'arbiter.policy.guardrails.compliance_mapper' not in sys.modules:
        mock_compliance = MagicMock()
        mock_compliance.load_compliance_map = lambda config_path=None: {
            "FAKE-1": {"name": "FakeControl", "status": "enforced", "required": True},
            "FAKE-2": {"name": "FakeOptional", "status": "logged", "required": False},
            "PC-1": {"name": "PolicyControl", "status": "enforced", "required": True},
            "NIST_AC-1": {"name": "Access Control Policy", "status": "enforced", "required": True},
            "NIST_AC-2": {"name": "Account Management", "status": "enforced", "required": True},
            "NIST_AC-3": {"name": "Access Enforcement", "status": "enforced", "required": True},
            "NIST_AC-6": {"name": "Least Privilege", "status": "enforced", "required": True}
        }
        sys.modules['arbiter.policy.guardrails.compliance_mapper'] = mock_compliance
    
    if 'arbiter.policy.plugins.llm_client' not in sys.modules:
        sys.modules['arbiter.policy.plugins.llm_client'] = MagicMock()
    
    # Note: OpenTelemetry mocking removed - now handled by centralized otel_config
    # The centralized configuration will automatically use NoOpTracer in test environment

def pytest_sessionstart(session):
    """Called after the Session object has been created and before performing collection and entering the run test loop."""
    # Additional setup if needed
    pass

def pytest_sessionfinish(session, exitstatus):
    """Called after whole test run finished, right before returning the exit status to the system."""
    # Cleanup if needed
    pass

@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset singleton instances between tests to ensure test isolation."""
    # Reset policy engine singleton
    from arbiter.policy import core
    if hasattr(core, '_policy_engine_instance'):
        core._policy_engine_instance = None
    
    # Reset config singleton
    from arbiter.policy import config
    if hasattr(config, '_instance'):
        config._instance = None
    
    # Reset circuit breaker states
    from arbiter.policy import circuit_breaker
    if hasattr(circuit_breaker, '_breaker_states'):
        circuit_breaker._breaker_states.clear()
    
    yield
    
    # Cleanup after test
    if hasattr(core, '_policy_engine_instance'):
        core._policy_engine_instance = None
    if hasattr(config, '_instance'):
        config._instance = None
    if hasattr(circuit_breaker, '_breaker_states'):
        circuit_breaker._breaker_states.clear()

@pytest.fixture
def mock_redis(monkeypatch):
    """Mock Redis for tests that don't need actual Redis."""
    mock_redis_client = MagicMock()
    mock_redis_client.ping = MagicMock(return_value=True)
    mock_redis_client.hgetall = MagicMock(return_value={})
    mock_redis_client.hset = MagicMock(return_value=True)
    mock_redis_client.expire = MagicMock(return_value=True)
    mock_redis_client.pipeline = MagicMock()
    mock_redis_client.close = MagicMock()
    
    mock_redis_module = MagicMock()
    mock_redis_module.Redis.from_url = MagicMock(return_value=mock_redis_client)
    mock_redis_module.ConnectionPool.from_url = MagicMock()
    mock_redis_module.RedisError = Exception
    
    monkeypatch.setattr('redis.asyncio', mock_redis_module)
    return mock_redis_client

@pytest.fixture
def clean_environment(monkeypatch):
    """Provide a clean environment for tests."""
    # Store original environment
    original_env = os.environ.copy()
    
    # Set test environment
    monkeypatch.setenv('ENVIRONMENT', 'test')
    monkeypatch.setenv('APP_ENV', 'test')
    
    yield
    
    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)