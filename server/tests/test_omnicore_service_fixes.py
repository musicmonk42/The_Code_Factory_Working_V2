# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for OmniCore service fixes.

Tests the following fixes:
1. storage_path attribute initialization
2. Clarification session cleanup mechanism
3. Kafka producer initialization
4. Configurable timeouts
5. Async/sync singleton pattern
"""

import asyncio
import os
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock
import threading

from server.services.omnicore_service import (
    OmniCoreService,
    get_omnicore_service,
    get_omnicore_service_async,
    _clarification_sessions,
    DEFAULT_TESTGEN_TIMEOUT,
    DEFAULT_DEPLOY_TIMEOUT,
    DEFAULT_DOCGEN_TIMEOUT,
    DEFAULT_CRITIQUE_TIMEOUT,
    CLARIFICATION_SESSION_TTL_SECONDS,
)


class TestStoragePathInitialization:
    """Test that storage_path is properly initialized."""

    def test_storage_path_exists(self):
        """Test that storage_path attribute is initialized."""
        service = OmniCoreService()
        assert hasattr(service, 'storage_path')
        assert service.storage_path is not None

    def test_storage_path_is_path_object(self):
        """Test that storage_path is a Path object."""
        service = OmniCoreService()
        assert isinstance(service.storage_path, Path)

    def test_storage_path_directory_created(self):
        """Test that storage_path directory is created."""
        service = OmniCoreService()
        assert service.storage_path.exists()
        assert service.storage_path.is_dir()

    def test_storage_path_uses_config(self):
        """Test that storage_path uses config when available."""
        with patch('server.services.omnicore_service.get_agent_config') as mock_config:
            # Mock config with custom upload_dir
            mock_cfg = Mock()
            mock_cfg.upload_dir = Path("./test_uploads")
            mock_config.return_value = mock_cfg
            
            service = OmniCoreService()
            assert service.storage_path == Path("./test_uploads")
            
            # Cleanup
            if service.storage_path.exists():
                service.storage_path.rmdir()

    def test_storage_path_fallback(self):
        """Test that storage_path falls back to default when config unavailable."""
        with patch('server.services.omnicore_service.get_agent_config') as mock_config:
            mock_config.return_value = None
            
            service = OmniCoreService()
            assert service.storage_path == Path("./uploads")


class TestClarificationSessionCleanup:
    """Test clarification session cleanup mechanism."""

    @pytest.mark.asyncio
    async def test_cleanup_expired_sessions(self):
        """Test that expired sessions are cleaned up."""
        service = OmniCoreService()
        
        # Create some test sessions
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        recent_time = datetime.now(timezone.utc).isoformat()
        
        _clarification_sessions["old_job"] = {
            "job_id": "old_job",
            "created_at": old_time,
            "status": "in_progress",
        }
        _clarification_sessions["recent_job"] = {
            "job_id": "recent_job",
            "created_at": recent_time,
            "status": "in_progress",
        }
        
        # Clean up sessions older than 1 hour
        cleaned = await service.cleanup_expired_clarification_sessions(max_age_seconds=3600)
        
        assert cleaned == 1  # Only old_job should be cleaned
        assert "old_job" not in _clarification_sessions
        assert "recent_job" in _clarification_sessions
        
        # Cleanup
        _clarification_sessions.clear()

    @pytest.mark.asyncio
    async def test_cleanup_invalid_timestamp(self):
        """Test that sessions with invalid timestamps are cleaned up."""
        service = OmniCoreService()
        
        # Create session with invalid timestamp
        _clarification_sessions["invalid_job"] = {
            "job_id": "invalid_job",
            "created_at": "invalid-timestamp",
            "status": "in_progress",
        }
        
        cleaned = await service.cleanup_expired_clarification_sessions()
        
        assert cleaned == 1
        assert "invalid_job" not in _clarification_sessions

    @pytest.mark.asyncio
    async def test_cleanup_missing_timestamp(self):
        """Test that sessions without timestamps are cleaned up."""
        service = OmniCoreService()
        
        # Create session without timestamp
        _clarification_sessions["no_timestamp_job"] = {
            "job_id": "no_timestamp_job",
            "status": "in_progress",
        }
        
        cleaned = await service.cleanup_expired_clarification_sessions()
        
        assert cleaned == 1
        assert "no_timestamp_job" not in _clarification_sessions

    @pytest.mark.asyncio
    async def test_periodic_cleanup_task(self):
        """Test that periodic cleanup task runs."""
        service = OmniCoreService()
        
        # Create an old session
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _clarification_sessions["test_job"] = {
            "job_id": "test_job",
            "created_at": old_time,
            "status": "in_progress",
        }
        
        # Start periodic cleanup task with short interval
        cleanup_task = asyncio.create_task(
            service.start_periodic_session_cleanup(interval_seconds=1, max_age_seconds=3600)
        )
        
        # Wait for one cleanup cycle
        await asyncio.sleep(1.5)
        
        # Cancel the task
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        
        # Session should be cleaned up
        assert "test_job" not in _clarification_sessions


class TestKafkaProducerInitialization:
    """Test Kafka producer initialization."""

    def test_kafka_producer_attribute_exists(self):
        """Test that kafka_producer attribute is initialized."""
        service = OmniCoreService()
        assert hasattr(service, 'kafka_producer')

    def test_kafka_producer_disabled_by_default(self):
        """Test that kafka_producer is None when not configured."""
        with patch.dict(os.environ, {'KAFKA_ENABLED': 'false'}, clear=False):
            service = OmniCoreService()
            assert service.kafka_producer is None

    def test_kafka_producer_enabled(self):
        """Test that kafka_producer is configured when enabled."""
        with patch.dict(os.environ, {
            'KAFKA_ENABLED': 'true',
            'KAFKA_BOOTSTRAP_SERVERS': 'test:9092'
        }, clear=False):
            with patch('server.services.omnicore_service.AIOKafkaProducer'):
                service = OmniCoreService()
                assert service.kafka_producer is not None
                assert service.kafka_producer.get('enabled') is True
                assert service.kafka_producer.get('bootstrap_servers') == 'test:9092'

    def test_kafka_producer_graceful_degradation(self):
        """Test that Kafka producer initialization fails gracefully."""
        with patch.dict(os.environ, {'KAFKA_ENABLED': 'true'}, clear=False):
            # AIOKafkaProducer not installed - should not raise exception
            service = OmniCoreService()
            # Service should initialize even if Kafka fails
            assert service is not None


class TestConfigurableTimeouts:
    """Test configurable timeout constants."""

    def test_default_timeout_values(self):
        """Test that timeout constants have default values."""
        assert DEFAULT_TESTGEN_TIMEOUT == int(os.getenv("TESTGEN_TIMEOUT_SECONDS", "120"))
        assert DEFAULT_DEPLOY_TIMEOUT == int(os.getenv("DEPLOY_TIMEOUT_SECONDS", "90"))
        assert DEFAULT_DOCGEN_TIMEOUT == int(os.getenv("DOCGEN_TIMEOUT_SECONDS", "90"))
        assert DEFAULT_CRITIQUE_TIMEOUT == int(os.getenv("CRITIQUE_TIMEOUT_SECONDS", "90"))

    def test_custom_timeout_values(self):
        """Test that timeout values can be customized via environment variables."""
        with patch.dict(os.environ, {
            'TESTGEN_TIMEOUT_SECONDS': '180',
            'DEPLOY_TIMEOUT_SECONDS': '120',
            'DOCGEN_TIMEOUT_SECONDS': '120',
            'CRITIQUE_TIMEOUT_SECONDS': '120'
        }, clear=False):
            # Reload module to pick up new env vars
            import importlib
            import server.services.omnicore_service as omnicore_module
            importlib.reload(omnicore_module)
            
            assert omnicore_module.DEFAULT_TESTGEN_TIMEOUT == 180
            assert omnicore_module.DEFAULT_DEPLOY_TIMEOUT == 120
            assert omnicore_module.DEFAULT_DOCGEN_TIMEOUT == 120
            assert omnicore_module.DEFAULT_CRITIQUE_TIMEOUT == 120


class TestSingletonPattern:
    """Test async/sync singleton pattern."""

    def test_sync_singleton_returns_same_instance(self):
        """Test that sync getter returns same instance."""
        instance1 = get_omnicore_service()
        instance2 = get_omnicore_service()
        assert instance1 is instance2

    @pytest.mark.asyncio
    async def test_async_singleton_returns_same_instance(self):
        """Test that async getter returns same instance."""
        instance1 = await get_omnicore_service_async()
        instance2 = await get_omnicore_service_async()
        assert instance1 is instance2

    @pytest.mark.asyncio
    async def test_sync_and_async_return_same_instance(self):
        """Test that sync and async getters return same instance."""
        sync_instance = get_omnicore_service()
        async_instance = await get_omnicore_service_async()
        assert sync_instance is async_instance

    def test_sync_singleton_thread_safe(self):
        """Test that sync singleton is thread-safe."""
        instances = []
        
        def get_instance():
            instances.append(get_omnicore_service())
        
        threads = [threading.Thread(target=get_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # All instances should be the same
        assert all(inst is instances[0] for inst in instances)

    @pytest.mark.asyncio
    async def test_async_singleton_concurrent_safe(self):
        """Test that async singleton is safe for concurrent access."""
        # Reset singleton for this test
        import server.services.omnicore_service as omnicore_module
        omnicore_module._instance = None
        omnicore_module._async_instance_lock = None
        
        async def get_instance():
            return await get_omnicore_service_async()
        
        # Create multiple concurrent tasks
        tasks = [get_instance() for _ in range(10)]
        instances = await asyncio.gather(*tasks)
        
        # All instances should be the same
        assert all(inst is instances[0] for inst in instances)


class TestClarificationSessionTTL:
    """Test clarification session TTL constant."""

    def test_clarification_ttl_default(self):
        """Test that TTL has a default value."""
        assert CLARIFICATION_SESSION_TTL_SECONDS == int(os.getenv("CLARIFICATION_SESSION_TTL_SECONDS", "3600"))

    def test_clarification_ttl_custom(self):
        """Test that TTL can be customized via environment variable."""
        with patch.dict(os.environ, {'CLARIFICATION_SESSION_TTL_SECONDS': '7200'}, clear=False):
            # Reload module to pick up new env var
            import importlib
            import server.services.omnicore_service as omnicore_module
            importlib.reload(omnicore_module)
            
            assert omnicore_module.CLARIFICATION_SESSION_TTL_SECONDS == 7200
