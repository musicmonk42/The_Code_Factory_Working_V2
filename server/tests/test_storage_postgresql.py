# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for the PostgreSQL-backed job storage system.

Tests both in-memory and PostgreSQL storage modes to ensure
multi-worker safety and backward compatibility.
"""

import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.schemas import Job, JobStatus


@pytest.mark.asyncio
async def test_storage_in_memory_mode():
    """Test that storage works in in-memory mode when DATABASE_URL is not set."""
    # Temporarily unset DATABASE_URL to force in-memory mode
    with patch.dict(os.environ, {}, clear=False):
        if "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]
        
        # Reimport to reinitialize storage
        import importlib
        from server import storage
        importlib.reload(storage)
        
        # Create a test job
        job = Job(
            id="test-memory-job",
            status=JobStatus.PENDING,
            input_files=[],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            metadata={},
        )
        
        # Test dict-like operations
        storage.jobs_db[job.id] = job
        assert job.id in storage.jobs_db
        assert storage.jobs_db.get(job.id) == job
        assert job.id in list(storage.jobs_db.keys())
        assert job in list(storage.jobs_db.values())
        
        # Test deletion
        del storage.jobs_db[job.id]
        assert job.id not in storage.jobs_db


@pytest.mark.asyncio
async def test_add_job_function():
    """Test the add_job helper function."""
    with patch.dict(os.environ, {}, clear=False):
        if "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]
        
        # Reimport to reinitialize storage
        import importlib
        from server import storage
        importlib.reload(storage)
        
        # Create a test job
        job = Job(
            id="test-add-job",
            status=JobStatus.PENDING,
            input_files=[],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            metadata={},
        )
        
        # Use add_job function
        await storage.add_job(job)
        
        # Verify job was added
        assert job.id in storage.jobs_db
        assert storage.jobs_db.get(job.id) == job
        
        # Cleanup
        del storage.jobs_db[job.id]


@pytest.mark.asyncio
async def test_storage_eviction():
    """Test that old jobs are evicted when MAX_JOBS is exceeded."""
    with patch.dict(os.environ, {}, clear=False):
        if "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]
        
        # Reimport to reinitialize storage
        import importlib
        from server import storage
        importlib.reload(storage)
        
        # Temporarily reduce MAX_JOBS for testing
        original_max = storage.MAX_JOBS
        storage.MAX_JOBS = 5
        
        try:
            # Add 6 completed jobs (should trigger eviction)
            jobs = []
            for i in range(6):
                job = Job(
                    id=f"test-evict-{i}",
                    status=JobStatus.COMPLETED if i < 3 else JobStatus.PENDING,
                    input_files=[],
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                    metadata={},
                )
                jobs.append(job)
                await storage.add_job(job)
            
            # Should have evicted completed jobs, keeping active ones
            assert len(storage._jobs_memory_cache) <= storage.MAX_JOBS
            
            # Pending jobs should still be present
            for job in jobs:
                if job.status == JobStatus.PENDING:
                    assert job.id in storage.jobs_db
            
            # Cleanup
            for job in jobs:
                if job.id in storage.jobs_db:
                    del storage.jobs_db[job.id]
        
        finally:
            storage.MAX_JOBS = original_max


@pytest.mark.asyncio
@patch('sqlalchemy.ext.asyncio.async_sessionmaker')
@patch('sqlalchemy.ext.asyncio.create_async_engine')
async def test_postgresql_initialization(mock_create_engine, mock_sessionmaker):
    """Test that PostgreSQL storage initializes correctly."""
    # Mock DATABASE_URL
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:pass@localhost/testdb"}):
        # Mock the engine and session with proper async context manager support
        from unittest.mock import MagicMock
        mock_engine = AsyncMock()
        mock_conn = AsyncMock()
        mock_ctx_manager = MagicMock()
        mock_ctx_manager.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx_manager.__aexit__ = AsyncMock(return_value=None)
        mock_engine.begin = MagicMock(return_value=mock_ctx_manager)
        mock_create_engine.return_value = mock_engine
        
        mock_session = AsyncMock()
        mock_sessionmaker.return_value = mock_session
        
        # Reimport to trigger initialization
        import importlib
        from server import storage
        importlib.reload(storage)
        
        # Initialize PostgreSQL
        await storage._initialize_postgresql()
        
        # Verify engine was created
        assert storage._pg_initialized
        assert storage._pg_enabled


@pytest.mark.asyncio
async def test_fallback_config_get_api_key_for_provider():
    """Test that fallback ArbiterConfig has get_api_key_for_provider method."""
    from omnicore_engine.database.database import _create_fallback_settings
    
    # Create fallback config
    config = _create_fallback_settings()
    
    # Verify method exists
    assert hasattr(config, 'get_api_key_for_provider')
    assert callable(config.get_api_key_for_provider)
    
    # Test with mock environment variables
    with patch.dict(os.environ, {
        "OPENAI_API_KEY": "test-openai-key",
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "GOOGLE_API_KEY": "test-google-key",
        "LLM_API_KEY": "test-llm-key",
    }):
        assert config.get_api_key_for_provider("openai") == "test-openai-key"
        assert config.get_api_key_for_provider("anthropic") == "test-anthropic-key"
        assert config.get_api_key_for_provider("gemini") == "test-google-key"
        assert config.get_api_key_for_provider("google") == "test-google-key"
        assert config.get_api_key_for_provider("unknown") == "test-llm-key"


@pytest.mark.asyncio
async def test_fallback_config_llm_attributes():
    """Test that fallback ArbiterConfig has LLM_PROVIDER and LLM_MODEL attributes."""
    from omnicore_engine.database.database import _create_fallback_settings
    
    # Create fallback config
    config = _create_fallback_settings()
    
    # Verify attributes exist
    assert hasattr(config, 'LLM_PROVIDER')
    assert hasattr(config, 'LLM_MODEL')
    
    # Test with custom environment variables
    with patch.dict(os.environ, {
        "LLM_PROVIDER": "anthropic",
        "LLM_MODEL": "claude-3-opus",
    }):
        # Recreate config to pick up new env vars
        config = _create_fallback_settings()
        assert config.LLM_PROVIDER == "anthropic"
        assert config.LLM_MODEL == "claude-3-opus"
