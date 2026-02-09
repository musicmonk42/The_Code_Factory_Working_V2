# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for Citus extension optional initialization fix.

This test validates that the application can start successfully even when
the Citus extension is not available on a standard PostgreSQL instance.
"""

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
import pytest_asyncio
import sqlalchemy
from cryptography.fernet import Fernet
from sqlalchemy import text

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from omnicore_engine.database import Database


@pytest.fixture
def mock_settings():
    """Mock ArbiterConfig settings for PostgreSQL."""
    settings = Mock()
    settings.LOG_LEVEL = "INFO"
    settings.DB_POOL_SIZE = 5
    settings.DB_POOL_MAX_OVERFLOW = 10
    settings.database_path = "postgresql+asyncpg://user:pass@localhost/testdb"
    settings.redis_url = "redis://localhost:6379"
    settings.ENCRYPTION_KEY = Mock(
        get_secret_value=lambda: Fernet.generate_key().decode()
    )
    settings.FERNET_KEYS = Mock(get_secret_value=lambda: Fernet.generate_key().decode())
    settings.DB_RETRY_ATTEMPTS = 3
    settings.DB_RETRY_DELAY = 1
    settings.DB_CIRCUIT_THRESHOLD = 5
    settings.DB_CIRCUIT_TIMEOUT = 60
    settings.EXPERIMENTAL_FEATURES_ENABLED = True
    settings.MAX_BACKUPS = 10
    return settings


@pytest.fixture
def mock_security_config():
    """Mock security configuration."""
    config = Mock()
    config.dict = Mock(
        return_value={
            "encryption_key": Fernet.generate_key().decode(),
            "key_rotation_interval": 30,
            "audit_enabled": True,
        }
    )
    return config


@pytest.mark.asyncio
async def test_migrate_to_citus_handles_missing_extension(mock_settings, mock_security_config, caplog):
    """Test that migrate_to_citus() gracefully handles missing Citus extension."""
    with patch("omnicore_engine.database.database._get_settings", return_value=mock_settings):
        with patch("omnicore_engine.database.database.get_security_config", return_value=mock_security_config):
            with patch("omnicore_engine.database.database.settings", mock_settings):
                with patch("omnicore_engine.database.database.EnterpriseSecurityUtils") as mock_security:
                    mock_security_instance = Mock()
                    mock_security_instance.encrypt = lambda x: x
                    mock_security_instance.decrypt = lambda x: x
                    mock_security.return_value = mock_security_instance

                    # Create a database instance
                    db = Database("postgresql+asyncpg://user:pass@localhost/testdb")
                    
                    # Mock AsyncSessionLocal to simulate Citus extension not available
                    mock_session = AsyncMock()
                    mock_session.__aenter__.return_value = mock_session
                    mock_session.__aexit__.return_value = None
                    
                    # Simulate Citus extension not available error
                    mock_session.execute.side_effect = sqlalchemy.exc.ProgrammingError(
                        "extension \"citus\" is not available",
                        params=None,
                        orig=Exception("Could not open extension control file")
                    )
                    
                    db.AsyncSessionLocal = Mock(return_value=mock_session)
                    
                    # Test that migrate_to_citus does not raise an exception
                    with caplog.at_level(logging.WARNING):
                        await db.migrate_to_citus()
                    
                    # Verify warning was logged
                    assert any("Citus extension not available" in record.message for record in caplog.records)
                    assert any("Continuing with standard PostgreSQL" in record.message for record in caplog.records)
                    
                    # Verify session was rolled back
                    mock_session.rollback.assert_called_once()
                    
                    # Verify commit was NOT called after rollback
                    assert mock_session.commit.call_count == 0


@pytest.mark.asyncio
async def test_migrate_to_citus_handles_distributed_table_failure(mock_settings, mock_security_config, caplog):
    """Test that migrate_to_citus() gracefully handles distributed table creation failure."""
    with patch("omnicore_engine.database.database._get_settings", return_value=mock_settings):
        with patch("omnicore_engine.database.database.get_security_config", return_value=mock_security_config):
            with patch("omnicore_engine.database.database.settings", mock_settings):
                with patch("omnicore_engine.database.database.EnterpriseSecurityUtils") as mock_security:
                    mock_security_instance = Mock()
                    mock_security_instance.encrypt = lambda x: x
                    mock_security_instance.decrypt = lambda x: x
                    mock_security.return_value = mock_security_instance

                    db = Database("postgresql+asyncpg://user:pass@localhost/testdb")
                    
                    mock_session = AsyncMock()
                    mock_session.__aenter__.return_value = mock_session
                    mock_session.__aexit__.return_value = None
                    
                    # First call succeeds (CREATE EXTENSION), second call fails (create_distributed_table)
                    call_count = 0
                    def execute_side_effect(*args, **kwargs):
                        nonlocal call_count
                        call_count += 1
                        if call_count == 1:
                            # CREATE EXTENSION succeeds
                            return AsyncMock()
                        else:
                            # create_distributed_table fails
                            raise sqlalchemy.exc.ProgrammingError(
                                "function create_distributed_table does not exist",
                                params=None,
                                orig=Exception()
                            )
                    
                    mock_session.execute.side_effect = execute_side_effect
                    db.AsyncSessionLocal = Mock(return_value=mock_session)
                    
                    # Test that migrate_to_citus does not raise an exception
                    with caplog.at_level(logging.WARNING):
                        await db.migrate_to_citus()
                    
                    # Verify warning was logged for distributed table failure
                    assert any("Failed to create distributed tables" in record.message for record in caplog.records)
                    assert any("Continuing with standard PostgreSQL" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_initialize_continues_when_citus_fails(mock_settings, mock_security_config, caplog):
    """Test that initialize() continues when migrate_to_citus() fails."""
    with patch("omnicore_engine.database.database._get_settings", return_value=mock_settings):
        with patch("omnicore_engine.database.database.get_security_config", return_value=mock_security_config):
            with patch("omnicore_engine.database.database.settings", mock_settings):
                with patch("omnicore_engine.database.database.EnterpriseSecurityUtils") as mock_security:
                    mock_security_instance = Mock()
                    mock_security_instance.encrypt = lambda x: x
                    mock_security_instance.decrypt = lambda x: x
                    mock_security.return_value = mock_security_instance

                    db = Database("postgresql+asyncpg://user:pass@localhost/testdb")
                    
                    # Mock the required methods
                    db.test_connection = AsyncMock()
                    db.create_tables = AsyncMock()
                    db.migrate_to_citus = AsyncMock(
                        side_effect=Exception("Citus extension not available")
                    )
                    
                    # Test that initialize completes successfully despite migrate_to_citus failing
                    with caplog.at_level(logging.WARNING):
                        await db.initialize()
                    
                    # Verify initialization proceeded
                    db.test_connection.assert_called_once()
                    db.create_tables.assert_called_once()
                    db.migrate_to_citus.assert_called_once()
                    
                    # Verify warning was logged
                    assert any("Citus migration skipped (non-fatal)" in record.message for record in caplog.records)
                    assert any("Continuing with standard PostgreSQL" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_migrate_to_citus_succeeds_when_citus_available(mock_settings, mock_security_config):
    """Test that migrate_to_citus() works correctly when Citus is available."""
    with patch("omnicore_engine.database.database._get_settings", return_value=mock_settings):
        with patch("omnicore_engine.database.database.get_security_config", return_value=mock_security_config):
            with patch("omnicore_engine.database.database.settings", mock_settings):
                with patch("omnicore_engine.database.database.EnterpriseSecurityUtils") as mock_security:
                    mock_security_instance = Mock()
                    mock_security_instance.encrypt = lambda x: x
                    mock_security_instance.decrypt = lambda x: x
                    mock_security.return_value = mock_security_instance

                    db = Database("postgresql+asyncpg://user:pass@localhost/testdb")
                    
                    mock_session = AsyncMock()
                    mock_session.__aenter__.return_value = mock_session
                    mock_session.__aexit__.return_value = None
                    mock_session.execute.return_value = AsyncMock()
                    mock_session.commit.return_value = None
                    
                    db.AsyncSessionLocal = Mock(return_value=mock_session)
                    
                    # Test that migrate_to_citus completes successfully
                    await db.migrate_to_citus()
                    
                    # Verify CREATE EXTENSION was called
                    assert mock_session.execute.call_count >= 1
                    # Verify commit was called (should be at least 2 times: after CREATE EXTENSION and after distributed tables)
                    assert mock_session.commit.call_count >= 1
