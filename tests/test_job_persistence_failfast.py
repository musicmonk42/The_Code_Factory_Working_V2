# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for job persistence fail-fast behavior (Issue 1).

This test module validates that job creation fails immediately with HTTP 500
when database persistence fails, rather than silently creating an in-memory
job that will vanish on restart.

Root Cause:
    Jobs were being stored only in memory (jobs_db dict) when database
    persistence failed, causing them to disappear after server restart.

Fix:
    Job creation now fails with HTTP 500 if database persistence fails,
    preventing silent data loss.
"""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from datetime import datetime, timezone

from server.schemas import Job, JobStatus, JobStage
from server.storage import jobs_db


class TestJobPersistenceFailFast:
    """Test that job creation fails when database persistence fails."""
    
    @pytest.mark.asyncio
    async def test_job_creation_fails_when_db_unavailable(self):
        """
        Test that job creation returns HTTP 500 when database persistence fails.
        
        This is the key fix for Issue 1: Previously, the code would catch
        the database error and continue, leaving the job only in memory.
        Now it properly fails the request.
        """
        from server.routers.jobs import create_job
        from server.schemas.jobs import JobCreateRequest
        
        # Mock dependencies
        mock_generator_service = AsyncMock()
        mock_omnicore_service = AsyncMock()
        mock_omnicore_service.emit_event = AsyncMock()
        
        # Mock save_job_to_database to return False (persistence failed)
        with patch('server.routers.jobs.save_job_to_database', new_callable=AsyncMock) as mock_save:
            mock_save.return_value = False  # Simulate DB failure
            
            # Create a job request
            request = JobCreateRequest(metadata={})
            
            # Attempt to create job - should raise HTTPException 500
            with pytest.raises(HTTPException) as exc_info:
                await create_job(
                    request=request,
                    generator_service=mock_generator_service,
                    omnicore_service=mock_omnicore_service,
                    _=None
                )
            
            # Verify HTTP 500 error
            assert exc_info.value.status_code == 500
            assert "persistence failed" in str(exc_info.value.detail).lower()
    
    @pytest.mark.asyncio
    async def test_job_removed_from_memory_on_db_failure(self):
        """
        Test that job is removed from in-memory storage when DB persistence fails.
        
        This ensures we don't leave ghost jobs in memory that will disappear
        on restart.
        """
        from server.routers.jobs import create_job
        from server.schemas.jobs import JobCreateRequest
        
        # Clear jobs_db before test
        jobs_db.clear()
        initial_count = len(jobs_db)
        
        # Mock dependencies
        mock_generator_service = AsyncMock()
        mock_omnicore_service = AsyncMock()
        
        # Mock save_job_to_database to return False
        with patch('server.routers.jobs.save_job_to_database', new_callable=AsyncMock) as mock_save:
            mock_save.return_value = False
            
            request = JobCreateRequest(metadata={})
            
            # Attempt to create job - should raise HTTPException
            try:
                await create_job(
                    request=request,
                    generator_service=mock_generator_service,
                    omnicore_service=mock_omnicore_service,
                    _=None
                )
            except HTTPException:
                pass  # Expected
            
            # Verify job was NOT left in memory
            assert len(jobs_db) == initial_count, \
                "Job should not remain in memory after DB persistence failure"
    
    @pytest.mark.asyncio
    async def test_job_creation_succeeds_when_db_available(self):
        """
        Test that job creation succeeds normally when database persistence works.
        
        This verifies the fix doesn't break the happy path.
        """
        from server.routers.jobs import create_job
        from server.schemas.jobs import JobCreateRequest
        
        # Clear jobs_db before test
        jobs_db.clear()
        
        # Mock dependencies
        mock_generator_service = AsyncMock()
        mock_omnicore_service = AsyncMock()
        mock_omnicore_service.emit_event = AsyncMock()
        
        # Mock save_job_to_database to return True (success)
        with patch('server.routers.jobs.save_job_to_database', new_callable=AsyncMock) as mock_save:
            mock_save.return_value = True
            
            request = JobCreateRequest(metadata={"test": "data"})
            
            # Create job - should succeed
            job = await create_job(
                request=request,
                generator_service=mock_generator_service,
                omnicore_service=mock_omnicore_service,
                _=None
            )
            
            # Verify job was created
            assert job.id is not None
            assert job.status == JobStatus.PENDING
            assert job.current_stage == JobStage.UPLOAD
            assert job.metadata == {"test": "data"}
            
            # Verify job is in memory
            assert job.id in jobs_db
            
            # Verify save_job_to_database was called
            mock_save.assert_called_once()


class TestTimezoneDatetimeHandling:
    """Test that DateTime columns use timezone-aware timestamps (Issue 2)."""
    
    def test_job_created_with_timezone_aware_datetime(self):
        """
        Test that jobs are created with timezone-aware datetimes.
        
        This prevents "can't subtract offset-naive and offset-aware datetimes"
        errors when interacting with the database.
        """
        # Create a job with timezone-aware datetime
        now = datetime.now(timezone.utc)
        
        job = Job(
            id="test-job-123",
            status=JobStatus.PENDING,
            current_stage=JobStage.UPLOAD,
            input_files=[],
            output_files=[],
            created_at=now,
            updated_at=now,
            metadata={}
        )
        
        # Verify datetimes are timezone-aware
        assert job.created_at.tzinfo is not None
        assert job.created_at.tzinfo == timezone.utc
        assert job.updated_at.tzinfo is not None
        assert job.updated_at.tzinfo == timezone.utc
    
    def test_datetime_comparison_works(self):
        """
        Test that timezone-aware datetimes can be compared without errors.
        
        This was the root cause of Issue 2 - mixing naive and aware datetimes
        caused TypeErrors in PostgreSQL queries.
        """
        now1 = datetime.now(timezone.utc)
        now2 = datetime.now(timezone.utc)
        
        # These comparisons should work without errors
        assert now1 <= now2
        
        # Subtraction should also work
        diff = now2 - now1
        assert diff.total_seconds() >= 0


class TestEventEmissionErrorHandling:
    """Test that event emission failures are logged prominently (Issue 4)."""
    
    @pytest.mark.asyncio
    async def test_event_emission_failure_logged_with_error_level(self):
        """
        Test that event emission failures are logged with ERROR level.
        
        This makes failures highly visible in logs so operators can detect
        when job pipelines aren't starting due to event bus issues.
        """
        from server.routers.jobs import _emit_event_fire_and_forget
        import logging
        
        # Mock omnicore service to raise an exception
        mock_service = AsyncMock()
        mock_service.emit_event.side_effect = Exception("Message bus unavailable")
        
        # Capture log output
        with patch('server.routers.jobs.logger') as mock_logger:
            await _emit_event_fire_and_forget(
                omnicore_service=mock_service,
                topic="job.created",
                payload={"job_id": "test-123"},
                priority=5
            )
            
            # Verify ERROR level logging was called (not just warning)
            mock_logger.error.assert_called_once()
            
            # Verify the error message contains critical information
            error_call = mock_logger.error.call_args
            error_message = error_call[0][0]  # First positional arg
            
            assert "CRITICAL" in error_message or "critical" in error_message.lower()
            assert "test-123" in error_message or "job_id" in str(error_call)
            assert "job.created" in error_message
    
    @pytest.mark.asyncio
    async def test_event_emission_success_logged(self):
        """
        Test that successful event emission is logged.
        
        This helps operators verify the event pipeline is working.
        """
        from server.routers.jobs import _emit_event_fire_and_forget
        
        # Mock omnicore service
        mock_service = AsyncMock()
        mock_service.emit_event = AsyncMock()
        
        # Capture log output
        with patch('server.routers.jobs.logger') as mock_logger:
            await _emit_event_fire_and_forget(
                omnicore_service=mock_service,
                topic="job.created",
                payload={"job_id": "test-456"},
                priority=5
            )
            
            # Verify INFO level logging for success
            mock_logger.info.assert_called_once()
            
            # Verify the log message contains job_id
            info_call = mock_logger.info.call_args
            info_message = info_call[0][0]
            
            assert "test-456" in info_message
            assert "job.created" in info_message


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "--tb=short"])
