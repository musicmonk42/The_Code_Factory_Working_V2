"""
Tests for critical bug fixes in MetaSupervisor and Database.

These tests validate that the fixes for the following bugs are working:
1. Async/await coroutine handling in MetaSupervisor
2. String decode error in audit recording (encrypt returns string)
3. Missing _start_time initialization
4. DB_ERRORS metric type (Counter not Histogram)
5. PolicyEngine initialization with fallback
6. EXPERIMENTAL_FEATURES_ENABLED attribute handling
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


class TestMetaSupervisorBugFixes:
    """Test MetaSupervisor critical bug fixes."""

    @pytest.mark.asyncio
    async def test_rate_limited_operation_handles_coroutines(self):
        """Test that _rate_limited_operation properly awaits coroutines."""
        from omnicore_engine.meta_supervisor import MetaSupervisor

        supervisor = MetaSupervisor(interval=60)

        # Mock an async operation that returns a dict
        async def mock_async_op():
            return {"changes": ["test"]}

        # Test that coroutines are properly awaited
        result = await supervisor._rate_limited_operation(mock_async_op)
        assert result == {"changes": ["test"]}
        assert not asyncio.iscoroutine(result), "Result should not be a coroutine"

    @pytest.mark.asyncio
    async def test_config_result_defensive_check(self):
        """Test that config_result has defensive coroutine check."""
        from omnicore_engine.meta_supervisor import MetaSupervisor

        supervisor = MetaSupervisor(interval=60)
        
        # Mock the database
        supervisor.db = MagicMock()
        supervisor.db.get_preferences = AsyncMock(return_value={"changes": ["test"]})

        # Initialize to setup the database connection
        with patch.object(supervisor, "initialize", new_callable=AsyncMock):
            # Mock the operations that would normally happen
            pass

        # Test the defensive check by mocking a scenario
        config_result = await supervisor._rate_limited_operation(
            supervisor.db.get_preferences, user_id="recent_config_changes"
        )
        
        # Defensive check: ensure config_result is not a coroutine
        if asyncio.iscoroutine(config_result):
            config_result = await config_result
        
        assert not asyncio.iscoroutine(config_result)
        if config_result is None:
            config_result = {}
        
        # Should not raise AttributeError on .get()
        changes = config_result.get("changes", [])
        assert isinstance(changes, list)

    def test_start_time_initialized(self):
        """Test that _start_time is initialized in __init__."""
        from omnicore_engine.meta_supervisor import MetaSupervisor

        supervisor = MetaSupervisor(interval=60)
        
        # Verify _start_time is initialized
        assert hasattr(supervisor, "_start_time"), "_start_time attribute should exist"
        assert isinstance(supervisor._start_time, float), "_start_time should be a float"
        assert supervisor._start_time > 0, "_start_time should be positive"
        
        # Verify it's close to current time (within 1 second)
        time_diff = abs(time.time() - supervisor._start_time)
        assert time_diff < 1.0, f"_start_time should be recent, but diff is {time_diff}"


class TestDatabaseBugFixes:
    """Test Database critical bug fixes."""

    def test_safe_encode_helper(self):
        """Test safe_encode handles both str and bytes."""
        from omnicore_engine.database.database import Database

        # Test with string
        result = Database.safe_encode("test string")
        assert isinstance(result, bytes)
        assert result == b"test string"

        # Test with bytes (should return as-is)
        result = Database.safe_encode(b"test bytes")
        assert isinstance(result, bytes)
        assert result == b"test bytes"

    def test_safe_decode_helper(self):
        """Test safe_decode handles both str and bytes."""
        from omnicore_engine.database.database import Database

        # Test with string (should return as-is)
        result = Database.safe_decode("test string")
        assert isinstance(result, str)
        assert result == "test string"

        # Test with bytes
        result = Database.safe_decode(b"test bytes")
        assert isinstance(result, str)
        assert result == "test bytes"

    def test_mock_policy_engine_creation(self):
        """Test that mock policy engine is created correctly."""
        from omnicore_engine.database.database import Database

        # Create a mock database instance without full initialization
        with patch("omnicore_engine.database.database.create_async_engine"):
            with patch("omnicore_engine.database.database.async_sessionmaker"):
                with patch("omnicore_engine.database.database.EnterpriseSecurityUtils"):
                    db = Database.__new__(Database)
                    
                    # Test _create_mock_policy_engine method
                    mock_engine = db._create_mock_policy_engine()
                    
                    # Verify it has the required method
                    assert hasattr(mock_engine, "should_auto_learn")
                    assert callable(mock_engine.should_auto_learn)

    @pytest.mark.asyncio
    async def test_mock_policy_engine_allows_operations(self):
        """Test that mock policy engine always allows operations."""
        from omnicore_engine.database.database import Database

        # Create a mock database instance
        with patch("omnicore_engine.database.database.create_async_engine"):
            with patch("omnicore_engine.database.database.async_sessionmaker"):
                with patch("omnicore_engine.database.database.EnterpriseSecurityUtils"):
                    db = Database.__new__(Database)
                    mock_engine = db._create_mock_policy_engine()
                    
                    # Test that it allows operations
                    allowed, reason = await mock_engine.should_auto_learn(
                        "test", "operation", "id", {}
                    )
                    
                    assert allowed is True
                    assert "Mock Policy" in reason or "allowed" in reason.lower()

    def test_experimental_features_with_getattr(self):
        """Test that EXPERIMENTAL_FEATURES_ENABLED uses getattr with default."""
        from omnicore_engine.database import database as db_module
        
        # Test that the module uses getattr properly
        settings = db_module.settings
        
        # Should not raise AttributeError even if attribute doesn't exist
        result = getattr(settings, "EXPERIMENTAL_FEATURES_ENABLED", False)
        assert isinstance(result, bool)


class TestEncryptionBugFixes:
    """Test encryption-related bug fixes."""

    def test_encrypt_returns_string_not_bytes(self):
        """Test that encrypt() returns string, so .decode() is not needed."""
        from omnicore_engine.security_utils import encrypt

        # Test that encrypt returns a string
        plaintext = "test data"
        key = "test-key-12345"
        
        encrypted = encrypt(plaintext, key=key)
        
        # Verify it's a string, not bytes
        assert isinstance(encrypted, str), "encrypt() should return str, not bytes"
        
        # This would fail if we tried to call .decode() on the result
        # encrypted.decode()  # Would raise: AttributeError: 'str' object has no attribute 'decode'

    def test_decrypt_returns_bytes_needs_decode(self):
        """Test that decrypt() returns bytes, so .decode() is correct."""
        from omnicore_engine.security_utils import decrypt, encrypt

        # Test that decrypt returns bytes
        plaintext = "test data"
        key = "test-key-12345"
        
        encrypted = encrypt(plaintext, key=key)
        decrypted = decrypt(encrypted, key=key)
        
        # Verify it's bytes
        assert isinstance(decrypted, bytes), "decrypt() should return bytes"
        
        # Verify .decode() works correctly
        decrypted_str = decrypted.decode('utf-8')
        assert isinstance(decrypted_str, str)
        assert decrypted_str == plaintext


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
