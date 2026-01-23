"""
Test Startup Fixes

This test validates the critical startup fixes including:
1. Configuration system with proper environment detection
2. Parallel agent loading
3. Distributed locks
4. Feature flags
"""

import os
import sys
import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock


def test_config_environment_detection():
    """Test that environment detection works properly."""
    from server.config_utils import detect_environment
    
    # Test TESTING=1 (takes precedence over pytest detection)
    with patch.dict(os.environ, {"TESTING": "1"}, clear=True):
        is_prod, is_test, is_dev = detect_environment()
        assert not is_prod
        assert is_test
        assert not is_dev
    
    # Test PRODUCTION_MODE=1 (highest priority)
    with patch.dict(os.environ, {"PRODUCTION_MODE": "1", "TESTING": "0"}, clear=True):
        is_prod, is_test, is_dev = detect_environment()
        assert is_prod
        assert not is_test
        assert not is_dev
    
    # Test APP_ENV=production
    with patch.dict(os.environ, {"APP_ENV": "production", "TESTING": "0"}, clear=True):
        # Need to temporarily remove pytest from sys.modules
        pytest_module = sys.modules.get('pytest')
        if pytest_module:
            del sys.modules['pytest']
        try:
            is_prod, is_test, is_dev = detect_environment()
            assert is_prod
            assert not is_test
            assert not is_dev
        finally:
            if pytest_module:
                sys.modules['pytest'] = pytest_module
    
    # Note: Can't test default (development) case because pytest is loaded


def test_config_feature_flags():
    """Test that feature flags are properly configured."""
    from server.config_utils import get_config
    
    # Test with all flags disabled
    with patch.dict(os.environ, {
        "ENABLE_DATABASE": "0",
        "ENABLE_FEATURE_STORE": "0",
        "ENABLE_HSM": "0",
    }, clear=True):
        config = get_config()
        assert not config.enable_database
        assert not config.enable_feature_store
        assert not config.enable_hsm
    
    # Test with all flags enabled
    with patch.dict(os.environ, {
        "ENABLE_DATABASE": "1",
        "ENABLE_FEATURE_STORE": "1",
        "ENABLE_HSM": "1",
    }, clear=True):
        config = get_config()
        assert config.enable_database
        assert config.enable_feature_store
        assert config.enable_hsm


def test_config_parallel_loading_flag():
    """Test parallel loading flag."""
    from server.config_utils import get_config
    
    # Test enabled (default)
    with patch.dict(os.environ, {}, clear=True):
        config = get_config()
        assert config.parallel_agent_loading
    
    # Test disabled
    with patch.dict(os.environ, {"PARALLEL_AGENT_LOADING": "0"}, clear=True):
        config = get_config()
        assert not config.parallel_agent_loading


def test_config_api_key_detection():
    """Test that API keys are properly detected."""
    from server.config_utils import get_config
    
    # Test with one API key
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test123"}, clear=True):
        config = get_config()
        assert "OPENAI_API_KEY" in config.available_api_keys
        assert len(config.available_api_keys) == 1
    
    # Test with multiple API keys
    with patch.dict(os.environ, {
        "OPENAI_API_KEY": "sk-test123",
        "ANTHROPIC_API_KEY": "sk-ant-test456",
        "GOOGLE_API_KEY": "test789",
    }, clear=True):
        config = get_config()
        assert "OPENAI_API_KEY" in config.available_api_keys
        assert "ANTHROPIC_API_KEY" in config.available_api_keys
        assert "GOOGLE_API_KEY" in config.available_api_keys
        assert len(config.available_api_keys) == 3


def test_api_key_validation_production():
    """Test that API key validation fails fast in production."""
    from server.config_utils import validate_required_api_keys, get_config
    
    # In production without API keys, should raise
    with patch.dict(os.environ, {"PRODUCTION_MODE": "1"}, clear=True):
        config = get_config()
        with pytest.raises(RuntimeError, match="No LLM API keys found"):
            validate_required_api_keys(config, fail_fast=True)
    
    # In production with API keys, should pass
    with patch.dict(os.environ, {
        "PRODUCTION_MODE": "1",
        "OPENAI_API_KEY": "sk-test123"
    }, clear=True):
        config = get_config()
        assert validate_required_api_keys(config, fail_fast=True)


def test_api_key_validation_development():
    """Test that API key validation only warns in development."""
    from server.config_utils import validate_required_api_keys, get_config
    
    # In development without API keys, should not raise
    with patch.dict(os.environ, {}, clear=True):
        config = get_config()
        result = validate_required_api_keys(config, fail_fast=False)
        assert not result  # Returns False but doesn't raise


def test_agent_loader_parallel_flag():
    """Test that agent loader respects parallel loading flag."""
    from server.utils.agent_loader import AgentLoader
    
    # Test with parallel loading enabled
    with patch.dict(os.environ, {"PARALLEL_AGENT_LOADING": "1"}, clear=True):
        # Create new instance to pick up env var
        loader = AgentLoader.__new__(AgentLoader)
        loader._initialized = False
        loader.__init__()
        assert loader._parallel_loading
    
    # Test with parallel loading disabled
    with patch.dict(os.environ, {"PARALLEL_AGENT_LOADING": "0"}, clear=True):
        # Create new instance to pick up env var
        loader = AgentLoader.__new__(AgentLoader)
        loader._initialized = False
        loader.__init__()
        assert not loader._parallel_loading


@pytest.mark.asyncio
async def test_distributed_lock_acquire_release():
    """Test distributed lock acquire and release."""
    from server.distributed_lock import DistributedLock
    
    # Mock Redis by patching the import in _get_redis_client
    with patch('redis.asyncio') as mock_redis_module:
        # Setup mock
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.eval = AsyncMock(return_value=1)
        mock_redis.ping = AsyncMock()
        mock_redis_module.Redis.from_url.return_value = mock_redis
        
        lock = DistributedLock("test_lock", timeout=30)
        
        # Test acquire
        acquired = await lock.acquire()
        assert acquired
        assert lock._acquired
        
        # Test release
        released = await lock.release()
        assert released
        assert not lock._acquired


@pytest.mark.asyncio
async def test_distributed_lock_context_manager():
    """Test distributed lock as context manager."""
    from server.distributed_lock import DistributedLock
    
    # Mock Redis
    with patch('redis.asyncio') as mock_redis_module:
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.eval = AsyncMock(return_value=1)
        mock_redis.ping = AsyncMock()
        mock_redis_module.Redis.from_url.return_value = mock_redis
        
        lock = DistributedLock("test_lock")
        
        async with lock as acquired:
            assert acquired
            assert lock._acquired
        
        # Should be released after context exit
        assert not lock._acquired


@pytest.mark.asyncio
async def test_distributed_lock_no_redis():
    """Test that lock works without Redis (single instance mode)."""
    from server.distributed_lock import DistributedLock
    
    # Mock the _get_redis_client method to return None
    lock = DistributedLock("test_lock")
    
    async def mock_get_redis():
        return None
    
    lock._get_redis_client = mock_get_redis
    
    # Should still "acquire" lock
    acquired = await lock.acquire()
    assert acquired
    assert lock._acquired
    
    # Should still "release" lock
    released = await lock.release()
    assert released
    assert not lock._acquired


@pytest.mark.asyncio
async def test_agent_loader_duplicate_prevention():
    """Test that agent loader prevents duplicate loading."""
    from server.utils.agent_loader import get_agent_loader
    
    loader = get_agent_loader()
    
    # Start background loading
    loader.start_background_loading()
    assert loader._loading_started
    
    # Try to start again - should be prevented
    loader.start_background_loading()
    # Should not raise, just log warning


def test_optional_dependencies_detection():
    """Test optional dependencies detection."""
    from server.config_utils import get_missing_optional_dependencies
    
    missing = get_missing_optional_dependencies()
    
    # Result should be a dict
    assert isinstance(missing, dict)
    
    # Each value should be a list
    for feature, deps in missing.items():
        assert isinstance(deps, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
