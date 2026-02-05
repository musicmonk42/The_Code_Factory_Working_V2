"""
Test suite for job status update fix after code generation.

Tests that jobs transition to COMPLETED status after successful code generation,
ensuring generated files are visible and downloadable in the UI.

Issue: Jobs were staying in RUNNING state forever after code generation completed.
Fix: Update job status, completed_at, and current_stage after successful generation.
"""

import pytest
import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch
from server.services.omnicore_service import OmniCoreService
from server.schemas.jobs import JobStatus, JobStage, Job
from server.storage import jobs_db


class TestJobStatusUpdateAfterCodegen:
    """Test suite for job status update after code generation."""

    @pytest.fixture
    def service(self):
        """Create an OmniCoreService instance for testing."""
        return OmniCoreService()

    @pytest.fixture
    def mock_job(self):
        """Create a mock job in RUNNING state."""
        job_id = "test-job-123"
        # Use fixed datetime for deterministic tests
        fixed_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        job = Job(
            id=job_id,
            status=JobStatus.RUNNING,
            current_stage=JobStage.GENERATOR_GENERATION,
            input_files=[],
            output_files=[],
            created_at=fixed_time,
            updated_at=fixed_time,
            completed_at=None,
            metadata={}
        )
        jobs_db[job_id] = job
        return job

    @pytest.fixture
    def cleanup_jobs_db(self):
        """Clean up jobs_db after each test."""
        yield
        # Clear all test jobs
        test_job_ids = [jid for jid in jobs_db.keys() if jid.startswith("test-")]
        for jid in test_job_ids:
            del jobs_db[jid]

    @pytest.mark.asyncio
    async def test_job_status_updated_to_completed_after_codegen(
        self, service, mock_job, cleanup_jobs_db
    ):
        """
        Test that job status is updated to COMPLETED after successful code generation.
        
        This is the core fix: after generating files, the job should transition from
        RUNNING to COMPLETED, with completed_at timestamp and current_stage set.
        """
        job_id = mock_job.id
        
        # Mock the codegen function to return successful result
        service._codegen_func = AsyncMock(return_value={
            "main.py": "print('Hello, World!')",
            "requirements.txt": "pytest\n",
            "README.md": "# Test Project\n"
        })
        service.agents_available['codegen'] = True
        service._agents_loaded = True
        
        # Mock LLM config to simulate configured LLM
        service.llm_config = Mock()
        service.llm_config.default_llm_provider = "openai"
        service.llm_config.is_provider_configured = Mock(return_value=True)
        
        # Mock agent config for upload directory
        service.agent_config = Mock()
        service.agent_config.upload_dir = Path("./uploads")
        
        # Create test upload directory
        upload_dir = Path(f"./uploads/{job_id}/generated")
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            # Run code generation
            payload = {
                "requirements": "Create a simple Python hello world application",
                "language": "python",
                "framework": None
            }
            
            result = await service._run_codegen(job_id, payload)
            
            # Verify the result indicates success
            assert result["status"] == "completed"
            assert "generated_files" in result
            assert len(result["generated_files"]) > 0
            
            # Get the updated job from jobs_db
            updated_job = jobs_db[job_id]
            
            # CRITICAL: Verify job status was updated to COMPLETED
            assert updated_job.status == JobStatus.COMPLETED, \
                "Job status should be COMPLETED after successful code generation"
            
            # Verify completed_at timestamp was set
            assert updated_job.completed_at is not None, \
                "Job completed_at should be set after successful code generation"
            assert isinstance(updated_job.completed_at, datetime), \
                "completed_at should be a datetime object"
            
            # Verify current_stage was updated to COMPLETED
            assert updated_job.current_stage == JobStage.COMPLETED, \
                "Job current_stage should be COMPLETED after successful code generation"
            
            # Verify output_files were updated
            assert len(updated_job.output_files) > 0, \
                "Job output_files should contain generated files"
            
            # Verify updated_at was refreshed
            assert updated_job.updated_at is not None
            
        finally:
            # Cleanup test directory
            if Path(f"./uploads/{job_id}").exists():
                shutil.rmtree(f"./uploads/{job_id}")

    @pytest.mark.asyncio
    async def test_job_status_not_updated_when_no_files_generated(
        self, service, mock_job, cleanup_jobs_db
    ):
        """
        Test that job status is NOT updated to COMPLETED when no files are generated.
        
        Edge case: If code generation returns 0 files, job should remain in
        RUNNING or error state, not transition to COMPLETED.
        """
        job_id = mock_job.id
        
        # Mock codegen to return empty result (no files)
        service._codegen_func = AsyncMock(return_value={})
        service.agents_available['codegen'] = True
        service._agents_loaded = True
        
        # Mock LLM config
        service.llm_config = Mock()
        service.llm_config.default_llm_provider = "openai"
        service.llm_config.is_provider_configured = Mock(return_value=True)
        
        # Mock agent config
        service.agent_config = Mock()
        service.agent_config.upload_dir = Path("./uploads")
        
        # Create test upload directory
        upload_dir = Path(f"./uploads/{job_id}/generated")
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            payload = {
                "requirements": "Invalid requirements",
                "language": "python",
                "framework": None
            }
            
            result = await service._run_codegen(job_id, payload)
            
            # Verify result indicates error
            assert result["status"] == "error"
            assert "no files" in result["message"].lower()
            
            # Get the job from jobs_db
            job = jobs_db[job_id]
            
            # Verify job status is NOT COMPLETED
            assert job.status != JobStatus.COMPLETED, \
                "Job should NOT be COMPLETED when no files generated"
            
            # Verify completed_at was NOT set
            assert job.completed_at is None, \
                "Job completed_at should NOT be set when no files generated"
            
        finally:
            # Cleanup
            if Path(f"./uploads/{job_id}").exists():
                shutil.rmtree(f"./uploads/{job_id}")

    @pytest.mark.asyncio
    async def test_job_status_update_handles_missing_job_gracefully(self, service):
        """
        Test that job status update handles missing job gracefully.
        
        Edge case: If job_id is not in jobs_db, the code should not crash.
        """
        job_id = "non-existent-job-999"
        
        # Ensure job does not exist in jobs_db
        if job_id in jobs_db:
            del jobs_db[job_id]
        
        # Mock the codegen function
        service._codegen_func = AsyncMock(return_value={
            "test.py": "print('test')"
        })
        service.agents_available['codegen'] = True
        service._agents_loaded = True
        
        # Mock LLM config
        service.llm_config = Mock()
        service.llm_config.default_llm_provider = "openai"
        service.llm_config.is_provider_configured = Mock(return_value=True)
        
        # Mock agent config
        service.agent_config = Mock()
        service.agent_config.upload_dir = Path("./uploads")
        
        # Create test upload directory
        upload_dir = Path(f"./uploads/{job_id}/generated")
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            payload = {
                "requirements": "Test requirements",
                "language": "python",
                "framework": None
            }
            
            # Should not crash even though job doesn't exist in jobs_db
            result = await service._run_codegen(job_id, payload)
            
            # Result should still indicate success (files were generated)
            assert result["status"] == "completed"
            assert "generated_files" in result
            
        finally:
            # Cleanup
            if Path(f"./uploads/{job_id}").exists():
                shutil.rmtree(f"./uploads/{job_id}")

    @pytest.mark.asyncio
    async def test_job_timestamps_are_timezone_aware(
        self, service, mock_job, cleanup_jobs_db
    ):
        """
        Test that completed_at timestamp is timezone-aware (UTC).
        
        Important: All timestamps should use timezone-aware datetime objects
        to ensure consistency across systems.
        """
        job_id = mock_job.id
        
        # Mock codegen
        service._codegen_func = AsyncMock(return_value={
            "file.py": "# Test file"
        })
        service.agents_available['codegen'] = True
        service._agents_loaded = True
        
        # Mock LLM config
        service.llm_config = Mock()
        service.llm_config.default_llm_provider = "openai"
        service.llm_config.is_provider_configured = Mock(return_value=True)
        
        # Mock agent config
        service.agent_config = Mock()
        service.agent_config.upload_dir = Path("./uploads")
        
        # Create test upload directory
        upload_dir = Path(f"./uploads/{job_id}/generated")
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            payload = {
                "requirements": "Test",
                "language": "python",
                "framework": None
            }
            
            await service._run_codegen(job_id, payload)
            
            # Get updated job
            job = jobs_db[job_id]
            
            # Verify completed_at has timezone info
            assert job.completed_at is not None
            assert job.completed_at.tzinfo is not None, \
                "completed_at should be timezone-aware"
            assert job.completed_at.tzinfo == timezone.utc, \
                "completed_at should use UTC timezone"
            
        finally:
            # Cleanup
            if Path(f"./uploads/{job_id}").exists():
                shutil.rmtree(f"./uploads/{job_id}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
