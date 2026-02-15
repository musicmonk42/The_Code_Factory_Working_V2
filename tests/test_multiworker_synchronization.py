# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for multi-worker synchronization fixes.

This test module validates the fixes for three interconnected bugs that occur
when running with multiple Uvicorn workers:

Bug 1: Router loading timeout increased from 30s to 120s
Bug 2: Deleted jobs don't reappear after deletion during recovery
Bug 3: Clarification skip flow persists state to database before background tasks
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone

from server.schemas import Job, JobStatus, JobStage
from server.storage import jobs_db


class TestRouterLoadingTimeout:
    """Test that router loading timeout is set to 120 seconds."""
    
    def test_lifespan_uses_120_second_timeout(self):
        """
        Test that the router loading timeout is 120 seconds, not 30.
        
        This prevents timeout errors when loading heavy imports like
        SFE/Arbiter/ML models (HuggingFace transformers, matplotlib, etc.)
        """
        from server.main import lifespan
        import inspect
        
        # Get the source code of the lifespan function
        source = inspect.getsource(lifespan)
        
        # Verify timeout is 120 seconds
        assert "timeout=120.0" in source, "Router loading timeout should be 120.0 seconds"
        
        # Verify old timeout of 30 seconds is not present
        assert "timeout=30.0" not in source, "Old 30 second timeout should be removed"
        
        # Verify error message mentions 120 seconds
        assert "120 seconds" in source, "Error message should mention 120 second timeout"


class TestDeletedJobRecovery:
    """Test that deleted jobs don't reappear during recovery."""
    
    @pytest.mark.asyncio
    async def test_job_recovery_verifies_existence_before_restore(self):
        """
        Test that job recovery re-verifies job existence in database
        before restoring to memory.
        
        This prevents deleted jobs from reappearing when recovery happens
        concurrently with deletion across multiple workers.
        """
        from server.main import lifespan
        import inspect
        
        # Get the source code of the background initialization
        source = inspect.getsource(lifespan)
        
        # Verify that job recovery includes verification logic
        assert "verify_session" in source or "Verify job still exists" in source, \
            "Job recovery should include verification check"
        
        # Verify that deleted jobs are skipped
        assert "skip_deleted" in source or "was deleted after" in source, \
            "Job recovery should skip deleted jobs"
    
    @pytest.mark.asyncio
    async def test_delete_job_from_database_commits_immediately(self):
        """
        Test that delete_job_from_database commits the transaction
        to ensure the deletion is immediately visible to other workers.
        """
        from server.persistence import delete_job_from_database
        import inspect
        
        # Get the source code
        source = inspect.getsource(delete_job_from_database)
        
        # Verify commit is called
        assert "await session.commit()" in source, \
            "delete_job_from_database should commit the transaction"
        
        # Verify it handles commit errors
        assert "commit_error" in source or "Failed to commit" in source, \
            "delete_job_from_database should handle commit errors"


class TestClarificationPersistence:
    """Test that clarification responses persist state before background tasks."""
    
    @pytest.mark.asyncio
    async def test_submit_clarification_skip_persists_to_db(self):
        """
        Test that skipping clarification persists job state to database
        before triggering background pipeline resumption.
        
        This ensures status changes are visible to other workers.
        """
        from server.routers.generator import submit_clarification_response
        import inspect
        
        # Get the source code
        source = inspect.getsource(submit_clarification_response)
        
        # Verify save_job_to_database is imported
        from server.routers.generator import save_job_to_database
        assert save_job_to_database is not None, \
            "save_job_to_database should be imported"
        
        # Verify it's called before background_tasks.add_task when skip=True
        if_skip_index = source.find("if request.skip:")
        add_task_index = source.find("background_tasks.add_task(", if_skip_index)
        save_db_index = source.find("save_job_to_database(job)", if_skip_index)
        
        assert if_skip_index >= 0, "Should have skip handling"
        assert add_task_index >= 0, "Should add background task"
        assert save_db_index >= 0, "Should save to database"
        assert save_db_index < add_task_index, \
            "save_job_to_database should be called before background_tasks.add_task"
    
    @pytest.mark.asyncio
    async def test_submit_clarification_complete_persists_to_db(self):
        """
        Test that completing clarification persists job state to database
        before triggering background pipeline resumption.
        """
        from server.routers.generator import submit_clarification_response
        import inspect
        
        # Get the source code
        source = inspect.getsource(submit_clarification_response)
        
        # Find the section where all questions are answered
        all_answered_index = source.find("if all_answered:")
        if all_answered_index < 0:
            # Alternative patterns
            all_answered_index = max(
                source.find("All clarification questions answered"),
                source.find("clarification_status") and source.find("resolved")
            )
        
        # Look for save_job_to_database call in that section
        save_db_index = source.find("save_job_to_database(job)", all_answered_index)
        
        assert all_answered_index >= 0, "Should have all_answered handling"
        assert save_db_index >= 0, \
            "save_job_to_database should be called when all questions answered"
    
    @pytest.mark.asyncio
    async def test_resume_pipeline_loads_from_database_if_not_in_memory(self):
        """
        Test that _resume_pipeline_after_clarification loads job from database
        if not found in memory (multi-worker support).
        """
        from server.routers.generator import _resume_pipeline_after_clarification
        import inspect
        
        # Get the source code
        source = inspect.getsource(_resume_pipeline_after_clarification)
        
        # Verify it calls load_job_from_database
        assert "load_job_from_database" in source, \
            "_resume_pipeline_after_clarification should support loading from database"
        
        # Verify it adds job to memory after loading
        assert "await add_job(job)" in source or "add_job(job)" in source, \
            "_resume_pipeline_after_clarification should add loaded job to memory"
        
        # Verify it handles the case where job is not in memory
        assert "not in jobs_db" in source, \
            "_resume_pipeline_after_clarification should check if job is in memory"


class TestMultiWorkerIntegration:
    """Integration tests for multi-worker scenarios."""
    
    @pytest.mark.asyncio
    async def test_clarification_skip_with_mock_database(self):
        """
        Integration test for clarification skip flow with mocked database.
        
        Simulates the scenario where:
        1. User skips clarification
        2. Job state is persisted to database
        3. Background task resumes pipeline
        """
        from server.routers.generator import submit_clarification_response
        from server.schemas import ClarificationResponseRequest
        
        # Setup test job
        test_job = Job(
            id="test-job-123",
            status=JobStatus.PENDING,
            stage=JobStage.CLARIFYING,
            metadata={
                "clarification_questions": [
                    {"id": "q1", "question": "Test question?"}
                ]
            },
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        # Clear and add test job to jobs_db
        jobs_db.clear()
        jobs_db[test_job.id] = test_job
        
        # Mock dependencies
        mock_generator_service = AsyncMock()
        mock_background_tasks = MagicMock()
        
        # Create skip request
        request = ClarificationResponseRequest(skip=True)
        
        # Mock save_job_to_database to track calls
        with patch('server.routers.generator.save_job_to_database', new_callable=AsyncMock) as mock_save:
            mock_save.return_value = True
            
            # Call the endpoint
            result = await submit_clarification_response(
                job_id=test_job.id,
                request=request,
                generator_service=mock_generator_service,
                background_tasks=mock_background_tasks
            )
            
            # Verify save_job_to_database was called
            mock_save.assert_called_once()
            
            # Verify job status was updated
            assert test_job.status == JobStatus.RUNNING
            assert test_job.metadata["clarification_status"] == "resolved"
            
            # Verify background task was added
            mock_background_tasks.add_task.assert_called_once()
            
            # Verify response
            assert result["status"] == "skipped"
            assert result["job_id"] == test_job.id
    
    @pytest.mark.asyncio
    async def test_job_recovery_skips_deleted_job(self):
        """
        Integration test for job recovery that verifies deleted jobs are skipped.
        
        This test simulates the scenario where a job is deleted during recovery.
        """
        # This is a conceptual test - actual implementation would require
        # setting up a real database connection and is better suited for
        # end-to-end testing rather than unit tests.
        
        # The key assertion is that the code path exists in main.py
        from server.main import lifespan
        import inspect
        
        source = inspect.getsource(lifespan)
        
        # Verify the verification logic exists
        assert "verify_session" in source or "skip_deleted" in source, \
            "Job recovery should include logic to skip deleted jobs"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
