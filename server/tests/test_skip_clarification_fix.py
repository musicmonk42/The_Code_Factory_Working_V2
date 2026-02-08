# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for skip_clarification flag fix that prevents infinite loop.

These tests validate that when resuming the pipeline after clarification
is completed, the skip_clarification flag is properly passed through the
pipeline to prevent re-running clarification.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

from server.schemas.jobs import JobStatus, JobStage, Job


class TestSkipClarificationFlag:
    """Test that skip_clarification flag prevents re-clarification on resume."""

    @pytest.mark.asyncio
    async def test_resume_pipeline_passes_skip_clarification_flag(self):
        """When resuming after clarification, skip_clarification=True should be passed."""
        from server.routers.generator import _resume_pipeline_after_clarification
        from server.storage import jobs_db

        job_id = "test-skip-clarification-001"
        readme_content = "# Test Project\n\nBuild a web app with database."
        job = Job(
            id=job_id,
            status=JobStatus.NEEDS_CLARIFICATION,
            input_files=[],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            metadata={
                "readme_content": readme_content,
                "language": "python",
                "clarification_status": "pending_response",
                "clarification_questions": ["What database?"],
                "clarification_answers": {"q1": "PostgreSQL"},
            },
        )
        jobs_db[job_id] = job

        try:
            mock_service = MagicMock()
            mock_service.run_full_pipeline = AsyncMock(return_value={
                "status": "completed",
                "stages_completed": ["codegen"],
            })

            clarified_requirements = {
                "clarified_requirements": {
                    "database": "PostgreSQL",
                },
            }

            with patch("server.routers.generator.finalize_job_success", new_callable=AsyncMock) as mock_finalize:
                mock_finalize.return_value = True
                await _resume_pipeline_after_clarification(
                    job_id=job_id,
                    generator_service=mock_service,
                    clarified_requirements=clarified_requirements,
                )

            # Verify run_full_pipeline was called with skip_clarification=True
            call_args = mock_service.run_full_pipeline.call_args
            assert call_args is not None, "run_full_pipeline should have been called"
            assert call_args.kwargs.get("skip_clarification") is True, \
                "skip_clarification should be True when resuming after clarification"
        finally:
            if job_id in jobs_db:
                del jobs_db[job_id]

    @pytest.mark.asyncio
    async def test_generator_service_forwards_skip_clarification_to_payload(self):
        """GeneratorService should include skip_clarification in OmniCore payload."""
        from server.services.generator_service import GeneratorService

        mock_omnicore = MagicMock()
        mock_omnicore.route_job = AsyncMock(return_value={"data": {"status": "completed"}})

        service = GeneratorService(omnicore_service=mock_omnicore)

        await service.run_full_pipeline(
            job_id="test-job-001",
            readme_content="# Test",
            language="python",
            include_tests=True,
            include_deployment=True,
            include_docs=True,
            run_critique=True,
            skip_clarification=True,
        )

        # Verify route_job was called with skip_clarification in payload
        call_args = mock_omnicore.route_job.call_args
        assert call_args is not None
        payload = call_args.kwargs.get("payload", {})
        assert payload.get("skip_clarification") is True, \
            "skip_clarification should be included in OmniCore payload"

    @pytest.mark.asyncio
    async def test_omnicore_skips_clarification_when_flag_is_true(self):
        """OmniCore should skip clarification step when skip_clarification=True."""
        from server.services.omnicore_service import OmniCoreService
        from server.storage import jobs_db

        job_id = "test-omnicore-skip-001"
        job = Job(
            id=job_id,
            status=JobStatus.RUNNING,
            input_files=[],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            metadata={"readme_content": "# Test", "language": "python"},
        )
        jobs_db[job_id] = job

        try:
            service = OmniCoreService()
            
            # Mock the agent-related methods and codegen
            service._ensure_agents_loaded = MagicMock()
            service._run_clarifier = AsyncMock(return_value={
                "status": "clarification_initiated",
                "clarifications": ["What database?"],
            })
            service._run_codegen = AsyncMock(return_value={
                "status": "completed",
                "output_path": "/tmp/test",
            })

            payload = {
                "readme_content": "# Test Project\n\nBuild a web app.",
                "language": "python",
                "include_tests": False,
                "include_deployment": False,
                "include_docs": False,
                "run_critique": False,
                "skip_clarification": True,
            }

            result = await service._run_full_pipeline(job_id, payload)

            # Verify clarifier was NOT called
            service._run_clarifier.assert_not_called()
            # Verify codegen WAS called
            service._run_codegen.assert_called_once()
            # Verify result is not a clarification_initiated status
            assert result.get("status") != "clarification_initiated"
        finally:
            if job_id in jobs_db:
                del jobs_db[job_id]
            # Clean up the in-progress tracking
            if hasattr(service, '_jobs_in_pipeline'):
                service._jobs_in_pipeline.discard(job_id)


class TestBulkResponseHandling:
    """Test that bulk responses properly trigger pipeline resumption."""

    @pytest.mark.asyncio
    async def test_bulk_responses_trigger_resume_when_covering_all_questions(self):
        """Bulk responses that cover all questions should trigger pipeline resume."""
        from server.routers.generator import submit_clarification_response
        from server.schemas.generator_schemas import ClarificationResponseRequest
        from server.storage import jobs_db
        from fastapi import BackgroundTasks

        job_id = "test-bulk-response-001"
        job = Job(
            id=job_id,
            status=JobStatus.NEEDS_CLARIFICATION,
            input_files=[],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            metadata={
                "readme_content": "# Test",
                "language": "python",
                "clarification_questions": ["Q1", "Q2", "Q3"],
                "clarification_answers": {},
            },
        )
        jobs_db[job_id] = job

        try:
            mock_service = MagicMock()
            mock_service.submit_clarification_response = AsyncMock(return_value={
                "status": "recorded"
            })

            request = ClarificationResponseRequest(
                responses={
                    "q1": "Answer 1",
                    "q2": "Answer 2", 
                    "q3": "Answer 3",
                }
            )

            background_tasks = BackgroundTasks()

            with patch("server.routers.generator._resume_pipeline_after_clarification") as mock_resume:
                result = await submit_clarification_response(
                    job_id=job_id,
                    request=request,
                    generator_service=mock_service,
                    background_tasks=background_tasks,
                )

            # Verify that resume was scheduled (task added to background_tasks)
            assert result.get("status") in ["all_answered", "completed"], \
                "Status should indicate all questions answered"
            # The background task should have been added
            assert len(background_tasks.tasks) > 0, "Resume task should be scheduled"
        finally:
            if job_id in jobs_db:
                del jobs_db[job_id]
