# arbiter/policy/tests/test_circuit_breaker.py

import pytest
import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, Mock
import gc

# Set test environment variable before ANY imports
os.environ["PYTEST_CURRENT_TEST"] = "test"
os.environ["PAUSE_CIRCUIT_BREAKER_TASKS"] = "true"

# Mock asyncio.create_task before importing the module
original_create_task = asyncio.create_task


def mock_create_task(coro):
    """Mock create_task to prevent background tasks from starting"""
    # Just return a mock task that's already done
    mock_task = Mock()
    mock_task.done.return_value = True
    mock_task.cancel.return_value = True
    # Close the coroutine to prevent warnings
    coro.close()
    return mock_task


# Patch asyncio.create_task globally
asyncio.create_task = mock_create_task

try:
    # Now safe to import
    from arbiter.policy.circuit_breaker import (
        InMemoryBreakerStateManager,
        CircuitBreakerState,
        sanitize_log_message,
        _sanitize_provider,
        get_breaker_state,
        is_llm_policy_circuit_breaker_open,
        record_llm_policy_api_success,
        record_llm_policy_api_failure,
        validate_config,
        _breaker_states,
        _breaker_states_lock,
        _connection_pool_lock,
    )
finally:
    # Restore original create_task after import
    asyncio.create_task = original_create_task


@pytest.fixture(scope="session", autouse=True)
def cleanup_at_exit():
    """Clean up at the end of the test session."""
    yield
    # Force garbage collection to clean up any remaining objects
    gc.collect()


@pytest.fixture
def mock_config():
    """Create a mock ArbiterConfig instance."""
    config = MagicMock()
    config.REDIS_URL = None
    config.LLM_API_FAILURE_THRESHOLD = 3
    config.LLM_API_BACKOFF_MAX_SECONDS = 60.0
    config.CIRCUIT_BREAKER_STATE_TTL_SECONDS = 86400
    config.CIRCUIT_BREAKER_CLEANUP_INTERVAL_SECONDS = 3600
    config.REDIS_MAX_CONNECTIONS = 100
    config.REDIS_SOCKET_TIMEOUT = 5.0
    config.REDIS_SOCKET_CONNECT_TIMEOUT = 5.0
    config.CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL = 0.001
    config.CIRCUIT_BREAKER_CRITICAL_PROVIDERS = ""
    config.CIRCUIT_BREAKER_VALIDATION_ERROR_INTERVAL = 300.0
    config.CIRCUIT_BREAKER_MAX_PROVIDERS = 1000
    config.PAUSE_CIRCUIT_BREAKER_TASKS = "false"
    config.CONFIG_REFRESH_INTERVAL_SECONDS = 300
    return config


@pytest.fixture
def mock_config_with_redis(mock_config):
    """Create a mock config with Redis URL."""
    mock_config.REDIS_URL = "redis://localhost:6379"
    return mock_config


@pytest.fixture
async def cleanup_states():
    """Clean up all breaker states after each test."""
    yield
    # Clean up global state
    with _breaker_states_lock:
        _breaker_states.clear()
    # Reset global connection pool
    global _global_connection_pool
    with _connection_pool_lock:
        _global_connection_pool = None


class TestSanitizationFunctions:
    """Test input sanitization functions."""

    def test_sanitize_log_message_none(self):
        assert sanitize_log_message(None) == ""

    def test_sanitize_log_message_empty(self):
        assert sanitize_log_message("") == ""

    def test_sanitize_log_message_normal(self):
        assert sanitize_log_message("normal message") == "normal message"

    def test_sanitize_log_message_control_characters(self):
        assert sanitize_log_message("line\nbreak\ttab\rcarriage") == "linebreaktabcarriage"

    def test_sanitize_log_message_truncation(self):
        long_message = "x" * 300
        result = sanitize_log_message(long_message)
        assert len(result) == 200
        assert result == "x" * 200

    def test_sanitize_provider_valid(self):
        assert _sanitize_provider("valid_provider-123") == "valid_provider-123"

    def test_sanitize_provider_invalid_characters(self):
        assert _sanitize_provider("invalid/provider@test") == "invalid_provider_test"

    def test_sanitize_provider_truncation(self):
        long_provider = "x" * 100
        result = _sanitize_provider(long_provider)
        assert len(result) == 50
        assert result == "x" * 50


class TestInMemoryBreakerStateManager:
    """Test the in-memory state manager."""

    @pytest.mark.asyncio
    async def test_initial_state(self):
        manager = InMemoryBreakerStateManager("test_provider")
        state = await manager.get_state()

        assert state["failures"] == 0
        assert isinstance(state["last_failure_time"], datetime)
        assert state["last_failure_time"].tzinfo == timezone.utc
        assert isinstance(state["next_try_after"], datetime)
        assert state["next_try_after"].tzinfo == timezone.utc

    @pytest.mark.asyncio
    async def test_set_and_get_state(self):
        manager = InMemoryBreakerStateManager("test_provider")

        new_state = {
            "failures": 5,
            "last_failure_time": datetime.now(timezone.utc),
            "next_try_after": datetime.now(timezone.utc) + timedelta(seconds=60),
        }

        await manager.set_state(new_state)
        retrieved_state = await manager.get_state()

        assert retrieved_state["failures"] == 5
        assert retrieved_state["last_failure_time"] == new_state["last_failure_time"]
        assert retrieved_state["next_try_after"] == new_state["next_try_after"]

    @pytest.mark.asyncio
    async def test_state_lock(self):
        manager = InMemoryBreakerStateManager("test_provider")

        async with manager.state_lock():
            assert manager._lock.locked()

        assert not manager._lock.locked()

    @pytest.mark.asyncio
    async def test_close_method(self):
        manager = InMemoryBreakerStateManager("test_provider")
        await manager.close()  # Should not raise any errors


class TestConfigValidation:
    """Test configuration validation."""

    def test_validate_config_with_valid_config(self, mock_config):
        validate_config(mock_config)  # Should not raise

    def test_validate_config_with_invalid_types(self):
        config = MagicMock()
        config.LLM_API_FAILURE_THRESHOLD = "not_an_int"
        config.LLM_API_BACKOFF_MAX_SECONDS = "not_a_float"

        validate_config(config)

        # Should set defaults for invalid types
        assert config.LLM_API_FAILURE_THRESHOLD == 3
        assert config.LLM_API_BACKOFF_MAX_SECONDS == 60.0


class TestBreakerStateManagement:
    """Test breaker state creation and management."""

    @pytest.mark.asyncio
    async def test_get_breaker_state_creates_new(self, mock_config, cleanup_states):
        state = await get_breaker_state("test_provider", mock_config)
        assert state is not None
        assert isinstance(state, InMemoryBreakerStateManager)

    @pytest.mark.asyncio
    async def test_get_breaker_state_returns_existing(self, mock_config, cleanup_states):
        state1 = await get_breaker_state("test_provider", mock_config)
        state2 = await get_breaker_state("test_provider", mock_config)
        assert state1 is state2

    @pytest.mark.asyncio
    async def test_invalid_provider_names(self, mock_config, cleanup_states):
        invalid_names = [
            "provider/with/slashes",
            "provider with spaces",
            "provider@email",
            "!invalid",
            "",
            "x" * 51,  # Too long
        ]

        for name in invalid_names:
            with pytest.raises(ValueError, match="Provider name must be alphanumeric"):
                await get_breaker_state(name, mock_config)

    @pytest.mark.asyncio
    async def test_provider_limit(self, mock_config, cleanup_states):
        # Temporarily set max providers to a small number
        with patch("arbiter.policy.circuit_breaker._MAX_PROVIDERS", 3):
            # Create up to the limit
            for i in range(3):
                await get_breaker_state(f"provider_{i}", mock_config)

            # Should fail when exceeding limit
            with pytest.raises(RuntimeError, match="Maximum provider limit"):
                await get_breaker_state("provider_extra", mock_config)


class TestCircuitBreakerLogic:
    """Test the main circuit breaker functionality."""

    @pytest.mark.asyncio
    async def test_breaker_closed_initially(self, mock_config, cleanup_states):
        with patch("arbiter.policy.circuit_breaker.get_config", return_value=mock_config):
            is_open = await is_llm_policy_circuit_breaker_open("test_provider", mock_config)
            assert not is_open

    @pytest.mark.asyncio
    async def test_breaker_opens_after_threshold(self, mock_config, cleanup_states):
        with patch("arbiter.policy.circuit_breaker.get_config", return_value=mock_config):
            provider = "test_provider"

            # Record failures up to threshold
            for i in range(mock_config.LLM_API_FAILURE_THRESHOLD):
                await record_llm_policy_api_failure(provider, f"Error {i}", mock_config)

            # Breaker should be open
            is_open = await is_llm_policy_circuit_breaker_open(provider, mock_config)
            assert is_open

    @pytest.mark.asyncio
    async def test_breaker_half_open_after_timeout(self, mock_config, cleanup_states):
        with patch("arbiter.policy.circuit_breaker.get_config", return_value=mock_config):
            provider = "test_provider"
            mock_config.LLM_API_BACKOFF_MAX_SECONDS = 0.01  # Very short for testing

            # Open the breaker
            for _ in range(mock_config.LLM_API_FAILURE_THRESHOLD):
                await record_llm_policy_api_failure(provider, config=mock_config)

            # Wait for backoff period
            await asyncio.sleep(0.02)

            # Should be half-open (allows one test request)
            is_open = await is_llm_policy_circuit_breaker_open(provider, mock_config)
            assert not is_open

    @pytest.mark.asyncio
    async def test_breaker_resets_on_success(self, mock_config, cleanup_states):
        with patch("arbiter.policy.circuit_breaker.get_config", return_value=mock_config):
            provider = "test_provider"

            # Record some failures (but not enough to open)
            await record_llm_policy_api_failure(provider, "Error 1", mock_config)
            await record_llm_policy_api_failure(provider, "Error 2", mock_config)

            # Verify failures were recorded
            state = await get_breaker_state(provider, mock_config)
            current_state = await state.get_state()
            assert current_state["failures"] == 2

            # Record success
            await record_llm_policy_api_success(provider, mock_config)

            # Verify reset
            current_state = await state.get_state()
            assert current_state["failures"] == 0
            assert current_state["last_failure_time"] == datetime.min.replace(tzinfo=timezone.utc)
            assert current_state["next_try_after"] == datetime.min.replace(tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_exponential_backoff(self, mock_config, cleanup_states):
        with patch("arbiter.policy.circuit_breaker.get_config", return_value=mock_config):
            provider = "test_provider"
            mock_config.LLM_API_BACKOFF_MAX_SECONDS = 100

            backoffs = []
            for i in range(1, 6):
                await record_llm_policy_api_failure(provider, f"Error {i}", mock_config)
                state = await get_breaker_state(provider, mock_config)
                current_state = await state.get_state()

                backoff = (
                    current_state["next_try_after"] - current_state["last_failure_time"]
                ).total_seconds()
                backoffs.append(backoff)

                # Verify exponential growth (2^failures) up to max
                expected = min(2**i, mock_config.LLM_API_BACKOFF_MAX_SECONDS)
                assert backoff == expected

    @pytest.mark.asyncio
    @pytest.mark.slow  # Mark as slow test
    async def test_failure_count_cap(self, mock_config, cleanup_states):
        """Test that failure count is capped at 1000. This is a slow test."""
        with patch("arbiter.policy.circuit_breaker.get_config", return_value=mock_config):
            provider = "test_provider"

            # Directly set state instead of recording 1500 failures
            state_manager = await get_breaker_state(provider, mock_config)
            state = await state_manager.get_state()
            state["failures"] = 1500
            await state_manager.set_state(state)

            # Verify it was capped
            current_state = await state_manager.get_state()
            assert current_state["failures"] == 1000


class TestCircuitBreakerState:
    """Test the CircuitBreakerState class."""

    @pytest.mark.asyncio
    async def test_initialization_without_redis(self, mock_config):
        state = CircuitBreakerState("test_provider", mock_config)
        await state.initialize()
        assert state.redis_client is None

    @pytest.mark.asyncio
    async def test_initialization_with_invalid_redis_url(self, mock_config):
        mock_config.REDIS_URL = "invalid://url"
        state = CircuitBreakerState("test_provider", mock_config)
        await state.initialize()
        assert state.redis_client is None

    @pytest.mark.asyncio
    async def test_state_validation(self, mock_config):
        state = CircuitBreakerState("test_provider", mock_config)
        await state.initialize()

        # Test invalid failures value (negative)
        invalid_state = {
            "failures": -5,
            "last_failure_time": datetime.now(timezone.utc),
            "next_try_after": datetime.now(timezone.utc),
        }
        await state.set_state(invalid_state)
        # Should be clamped to 0
        assert state._in_memory_state["failures"] == 0

        # Test invalid failures value (too high)
        invalid_state["failures"] = 2000
        await state.set_state(invalid_state)
        # Should be clamped to 1000
        assert state._in_memory_state["failures"] == 1000


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "--timeout=30", "-m", "not slow"])
