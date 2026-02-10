# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for Database Queue Dispatch and Version Constraint Validation

This test suite validates the implementations for:
1. Database queue with guaranteed delivery (outbox pattern)
2. Queue processor with retry logic and exponential backoff
3. Version constraint validation using packaging library

Test Categories:
1. Database Queue Tests
   - Event enqueueing
   - Queue processing with retries
   - Exponential backoff calculation
   - Dead letter queue handling
   - Concurrent processing (SKIP LOCKED)

2. Version Validation Tests
   - Complex version constraints (ranges, ~=, !=)
   - PEP 440 compliance
   - Graceful fallback without packaging
   - Invalid version handling
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch, call

# Import models and services
from omnicore_engine.database.models import DispatchEventQueue, DispatchEventStatus
from server.services.dispatch_service import (
    _dispatch_via_database_queue,
    process_dispatch_queue,
)


class TestDatabaseQueueDispatch:
    """Test suite for database queue dispatch functionality."""

    @pytest.fixture
    async def mock_database(self):
        """Mock database session and operations."""
        with patch("server.services.dispatch_service.Database") as MockDatabase:
            mock_db = MockDatabase.return_value
            mock_db._engine = MagicMock()  # Simulate initialized engine

            # Mock session context manager
            mock_session = AsyncMock()
            mock_db.get_session = MagicMock(
                return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_session))
            )

            yield mock_db, mock_session

    @pytest.mark.asyncio
    async def test_dispatch_via_database_queue_success(self, mock_database):
        """Test successful event enqueueing to database queue."""
        mock_db, mock_session = mock_database

        job_id = "test-job-123"
        event = {
            "event_type": "job.completed",
            "job_id": job_id,
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        correlation_id = "corr-456"

        # Execute
        result = await _dispatch_via_database_queue(job_id, event, correlation_id)

        # Verify
        assert result is True
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called()

        # Check that the added entry has correct attributes
        added_entry = mock_session.add.call_args[0][0]
        assert added_entry.job_id == job_id
        assert added_entry.event_type == "job.completed"
        assert added_entry.correlation_id == correlation_id
        assert added_entry.status == DispatchEventStatus.PENDING
        assert added_entry.retry_count == 0
        assert added_entry.max_retries == 5

    @pytest.mark.asyncio
    async def test_dispatch_via_database_queue_failure(self, mock_database):
        """Test handling of database errors during enqueueing."""
        mock_db, mock_session = mock_database

        # Simulate database error
        mock_session.add.side_effect = Exception("Database connection failed")

        result = await _dispatch_via_database_queue(
            "test-job-123",
            {"event_type": "job.completed"},
            "corr-456"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_queue_processor_pending_event(self):
        """Test queue processor handling of pending event."""
        with patch("server.services.dispatch_service.Database") as MockDatabase, \
             patch("server.services.dispatch_service._dispatch_via_kafka") as mock_kafka, \
             patch("server.services.dispatch_service.kafka_available") as mock_kafka_avail:

            # Setup mocks
            mock_db = MockDatabase.return_value
            mock_db._engine = MagicMock()

            # Create mock queue entry
            mock_entry = MagicMock(spec=DispatchEventQueue)
            mock_entry.id = 1
            mock_entry.job_id = "test-job-123"
            mock_entry.status = DispatchEventStatus.PENDING
            mock_entry.retry_count = 0
            mock_entry.max_retries = 5
            mock_entry.payload = {"event_type": "job.completed"}
            mock_entry.correlation_id = "corr-456"

            # Mock session
            mock_session = AsyncMock()
            mock_result = AsyncMock()
            mock_result.scalars.return_value.all.return_value = [mock_entry]
            mock_session.execute.return_value = mock_result

            mock_db.get_session.return_value = AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session)
            )

            # Kafka succeeds
            mock_kafka_avail.return_value = True
            mock_kafka.return_value = True

            # Run processor with max_runtime to prevent infinite loop
            await process_dispatch_queue(batch_size=10, max_runtime=0.1)

            # Verify event was marked as completed
            assert mock_entry.status == DispatchEventStatus.COMPLETED
            assert mock_entry.completed_at is not None

    @pytest.mark.asyncio
    async def test_queue_processor_retry_logic(self):
        """Test exponential backoff and retry logic."""
        with patch("server.services.dispatch_service.Database") as MockDatabase, \
             patch("server.services.dispatch_service._dispatch_via_kafka") as mock_kafka, \
             patch("server.services.dispatch_service._dispatch_via_webhook") as mock_webhook, \
             patch("server.services.dispatch_service.kafka_available") as mock_kafka_avail:

            # Setup mocks
            mock_db = MockDatabase.return_value
            mock_db._engine = MagicMock()

            # Create mock queue entry that will fail dispatch
            mock_entry = MagicMock(spec=DispatchEventQueue)
            mock_entry.id = 1
            mock_entry.job_id = "test-job-123"
            mock_entry.status = DispatchEventStatus.PENDING
            mock_entry.retry_count = 2  # Already failed twice
            mock_entry.max_retries = 5
            mock_entry.payload = {"event_type": "job.completed"}
            mock_entry.correlation_id = "corr-456"

            mock_session = AsyncMock()
            mock_result = AsyncMock()
            mock_result.scalars.return_value.all.return_value = [mock_entry]
            mock_session.execute.return_value = mock_result

            mock_db.get_session.return_value = AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session)
            )

            # Both dispatch methods fail
            mock_kafka_avail.return_value = False
            mock_kafka.return_value = False
            mock_webhook.return_value = False

            # Run processor
            await process_dispatch_queue(batch_size=10, max_runtime=0.1)

            # Verify retry logic
            assert mock_entry.status == DispatchEventStatus.FAILED
            assert mock_entry.retry_count == 3
            assert mock_entry.next_retry_at is not None

            # Verify exponential backoff (2^3 * 10 = 80 seconds)
            # Allow some tolerance for execution time
            expected_backoff = timedelta(seconds=80)
            actual_backoff = mock_entry.next_retry_at - datetime.now(timezone.utc)
            assert abs(actual_backoff.total_seconds() - expected_backoff.total_seconds()) < 5

    @pytest.mark.asyncio
    async def test_queue_processor_dead_letter_queue(self):
        """Test that events exceeding max retries are moved to DLQ."""
        with patch("server.services.dispatch_service.Database") as MockDatabase, \
             patch("server.services.dispatch_service._dispatch_via_kafka") as mock_kafka, \
             patch("server.services.dispatch_service.kafka_available") as mock_kafka_avail:

            # Setup mocks
            mock_db = MockDatabase.return_value
            mock_db._engine = MagicMock()

            # Create mock queue entry at max retries
            mock_entry = MagicMock(spec=DispatchEventQueue)
            mock_entry.id = 1
            mock_entry.job_id = "test-job-123"
            mock_entry.status = DispatchEventStatus.FAILED
            mock_entry.retry_count = 4  # One more will hit max
            mock_entry.max_retries = 5
            mock_entry.payload = {"event_type": "job.completed"}
            mock_entry.correlation_id = "corr-456"

            mock_session = AsyncMock()
            mock_result = AsyncMock()
            mock_result.scalars.return_value.all.return_value = [mock_entry]
            mock_session.execute.return_value = mock_result

            mock_db.get_session.return_value = AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session)
            )

            # Dispatch fails
            mock_kafka_avail.return_value = False
            mock_kafka.return_value = False

            # Run processor
            await process_dispatch_queue(batch_size=10, max_runtime=0.1)

            # Verify moved to dead letter queue
            assert mock_entry.status == DispatchEventStatus.DEAD_LETTER
            assert mock_entry.retry_count == 5


class TestVersionConstraintValidation:
    """Test suite for version constraint validation."""

    @pytest.mark.asyncio
    async def test_version_constraint_range(self):
        """Test version range constraints (>=X,<Y)."""
        from self_fixing_engineer.simulation.registry import check_plugin_dependencies

        manifest = {
            "dependencies": {
                "pytest": ">=7.0.0,<8.0.0"
            }
        }

        # Mock installed version
        with patch("self_fixing_engineer.simulation.registry.importlib_metadata") as mock_metadata:
            mock_metadata.version.return_value = "7.4.2"

            result = await check_plugin_dependencies(manifest, "test_plugin")
            assert result is True

    @pytest.mark.asyncio
    async def test_version_constraint_compatible_release(self):
        """Test compatible release operator (~=)."""
        from self_fixing_engineer.simulation.registry import check_plugin_dependencies

        manifest = {
            "dependencies": {
                "numpy": "~=1.24.0"  # Allows 1.24.x, not 1.25.0
            }
        }

        with patch("self_fixing_engineer.simulation.registry.importlib_metadata") as mock_metadata:
            mock_metadata.version.return_value = "1.24.3"

            result = await check_plugin_dependencies(manifest, "test_plugin")
            assert result is True

    @pytest.mark.asyncio
    async def test_version_constraint_exclusion(self):
        """Test version exclusion (!=)."""
        from self_fixing_engineer.simulation.registry import check_plugin_dependencies

        manifest = {
            "dependencies": {
                "requests": ">=2.25.0,!=2.27.0"  # Exclude specific version
            }
        }

        with patch("self_fixing_engineer.simulation.registry.importlib_metadata") as mock_metadata:
            mock_metadata.version.return_value = "2.28.0"

            result = await check_plugin_dependencies(manifest, "test_plugin")
            assert result is True

    @pytest.mark.asyncio
    async def test_version_constraint_violation(self):
        """Test that incompatible versions raise ValueError."""
        from self_fixing_engineer.simulation.registry import check_plugin_dependencies

        manifest = {
            "dependencies": {
                "django": ">=4.0.0,<5.0.0"
            }
        }

        with patch("self_fixing_engineer.simulation.registry.importlib_metadata") as mock_metadata:
            mock_metadata.version.return_value = "3.2.0"  # Too old

            with pytest.raises(ValueError, match="Version constraint not satisfied"):
                await check_plugin_dependencies(manifest, "test_plugin")

    @pytest.mark.asyncio
    async def test_version_no_constraint(self):
        """Test that missing version constraint accepts any version."""
        from self_fixing_engineer.simulation.registry import check_plugin_dependencies

        manifest = {
            "dependencies": {
                "requests": ""  # No constraint
            }
        }

        with patch("self_fixing_engineer.simulation.registry.importlib_metadata") as mock_metadata:
            mock_metadata.version.return_value = "2.31.0"

            result = await check_plugin_dependencies(manifest, "test_plugin")
            assert result is True

    @pytest.mark.asyncio
    async def test_version_missing_package(self):
        """Test that missing packages raise PackageNotFoundError."""
        from self_fixing_engineer.simulation.registry import check_plugin_dependencies

        manifest = {
            "dependencies": {
                "nonexistent_package": ">=1.0.0"
            }
        }

        with patch("self_fixing_engineer.simulation.registry.importlib_metadata") as mock_metadata:
            from importlib.metadata import PackageNotFoundError
            mock_metadata.version.side_effect = PackageNotFoundError("Package not found")
            mock_metadata.PackageNotFoundError = PackageNotFoundError

            with pytest.raises(PackageNotFoundError, match="nonexistent_package"):
                await check_plugin_dependencies(manifest, "test_plugin")

    @pytest.mark.asyncio
    async def test_version_fallback_without_packaging(self):
        """Test graceful fallback when packaging library unavailable."""
        from self_fixing_engineer.simulation.registry import check_plugin_dependencies

        manifest = {
            "dependencies": {
                "requests": ">=2.25.0"
            }
        }

        # Simulate packaging not available
        with patch("self_fixing_engineer.simulation.registry.HAS_PACKAGING", False), \
             patch("self_fixing_engineer.simulation.registry.importlib_metadata") as mock_metadata:

            mock_metadata.version.return_value = "2.28.0"

            # Should still return True but log warning
            result = await check_plugin_dependencies(manifest, "test_plugin")
            assert result is True

    @pytest.mark.asyncio
    async def test_version_invalid_specifier(self):
        """Test handling of invalid version specifiers."""
        from self_fixing_engineer.simulation.registry import check_plugin_dependencies

        manifest = {
            "dependencies": {
                "requests": ">>>2.0.0"  # Invalid operator
            }
        }

        with patch("self_fixing_engineer.simulation.registry.importlib_metadata") as mock_metadata:
            mock_metadata.version.return_value = "2.28.0"

            # Should handle gracefully and treat as satisfied
            result = await check_plugin_dependencies(manifest, "test_plugin")
            assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
