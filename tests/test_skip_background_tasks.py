# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for SKIP_BACKGROUND_TASKS environment variable.

Tests that background tasks (pipeline execution and event emission) are properly
skipped when SKIP_BACKGROUND_TASKS environment variable is set, preventing
event loop starvation and server crashes under load.

Issue: Background tasks run unconditionally, causing server crashes under load
Fix: Check SKIP_BACKGROUND_TASKS env var before spawning asyncio.create_task()
"""

import os
import pytest
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime, timezone
from server.schemas.jobs import JobStatus, JobStage, Job
from server.storage import jobs_db


class TestSkipBackgroundTasks:
    """Test suite for SKIP_BACKGROUND_TASKS functionality."""

    @pytest.fixture
    def mock_generator_service(self):
        """Create a mock generator service."""
        service = Mock()
        service.create_generation_job = AsyncMock()
        return service

    @pytest.fixture
    def mock_omnicore_service(self):
        """Create a mock OmniCore service."""
        service = Mock()
        service.emit_event = AsyncMock()
        return service

    @pytest.fixture
    def cleanup_env(self):
        """Clean up environment variable after test."""
        yield
        if "SKIP_BACKGROUND_TASKS" in os.environ:
            del os.environ["SKIP_BACKGROUND_TASKS"]

    @pytest.mark.asyncio
    async def test_v1_compat_skips_tasks_when_env_set(
        self, mock_generator_service, mock_omnicore_service, cleanup_env
    ):
        """Test that v1_compat create_generation skips background tasks when SKIP_BACKGROUND_TASKS is set."""
        from server.routers.v1_compat import router
        from fastapi.testclient import TestClient
        from fastapi import FastAPI

        # Set the environment variable
        os.environ["SKIP_BACKGROUND_TASKS"] = "1"

        app = FastAPI()
        app.include_router(router)

        with patch("server.routers.v1_compat._run_pipeline_with_semaphore") as mock_pipeline, \
             patch("server.routers.v1_compat._emit_event_fire_and_forget") as mock_emit, \
             patch("server.routers.v1_compat.get_generator_service", return_value=mock_generator_service), \
             patch("server.routers.v1_compat.get_omnicore_service", return_value=mock_omnicore_service), \
             patch("asyncio.create_task") as mock_create_task:
            
            client = TestClient(app)
            response = client.post(
                "/api/v1/generate",
                json={"requirements": "Build a web app"}
            )

            # Should still return 202 Accepted
            assert response.status_code == 202
            
            # asyncio.create_task should NOT have been called (tasks skipped)
            assert mock_create_task.call_count == 0

    @pytest.mark.asyncio
    async def test_v1_compat_runs_tasks_when_env_not_set(
        self, mock_generator_service, mock_omnicore_service, cleanup_env
    ):
        """Test that v1_compat create_generation runs background tasks when SKIP_BACKGROUND_TASKS is NOT set."""
        from server.routers.v1_compat import router
        from fastapi.testclient import TestClient
        from fastapi import FastAPI

        # Ensure env var is NOT set
        if "SKIP_BACKGROUND_TASKS" in os.environ:
            del os.environ["SKIP_BACKGROUND_TASKS"]

        app = FastAPI()
        app.include_router(router)

        with patch("server.routers.v1_compat._run_pipeline_with_semaphore") as mock_pipeline, \
             patch("server.routers.v1_compat._emit_event_fire_and_forget") as mock_emit, \
             patch("server.routers.v1_compat.get_generator_service", return_value=mock_generator_service), \
             patch("server.routers.v1_compat.get_omnicore_service", return_value=mock_omnicore_service), \
             patch("asyncio.create_task") as mock_create_task:
            
            client = TestClient(app)
            response = client.post(
                "/api/v1/generate",
                json={"requirements": "Build a web app"}
            )

            # Should still return 202 Accepted
            assert response.status_code == 202
            
            # asyncio.create_task should have been called twice (event + pipeline)
            assert mock_create_task.call_count == 2

    @pytest.mark.asyncio
    async def test_jobs_router_skips_event_when_env_set(
        self, mock_omnicore_service, cleanup_env
    ):
        """Test that jobs router skips event emission when SKIP_BACKGROUND_TASKS is set."""
        from server.routers.jobs import router
        from fastapi.testclient import TestClient
        from fastapi import FastAPI

        # Set the environment variable
        os.environ["SKIP_BACKGROUND_TASKS"] = "1"

        app = FastAPI()
        app.include_router(router)

        with patch("server.routers.jobs._emit_event_fire_and_forget") as mock_emit, \
             patch("server.routers.jobs.get_omnicore_service", return_value=mock_omnicore_service), \
             patch("asyncio.create_task") as mock_create_task:
            
            client = TestClient(app)
            response = client.post(
                "/api/v2/jobs",
                json={"metadata": {}}
            )

            # Should return 200 OK with job data
            assert response.status_code == 200
            
            # asyncio.create_task should NOT have been called
            assert mock_create_task.call_count == 0

    @pytest.mark.asyncio
    async def test_generator_router_skips_pipeline_when_env_set(
        self, mock_generator_service, cleanup_env
    ):
        """Test that generator router skips pipeline when SKIP_BACKGROUND_TASKS is set."""
        from server.routers.generator import router
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from io import BytesIO

        # Set the environment variable
        os.environ["SKIP_BACKGROUND_TASKS"] = "1"

        # Create a test job
        job_id = "test-job-456"
        job = Job(
            id=job_id,
            status=JobStatus.PENDING,
            current_stage=JobStage.GENERATOR_UPLOAD,
            input_files=[],
            output_files=[],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            metadata={}
        )
        jobs_db[job_id] = job

        app = FastAPI()
        app.include_router(router)

        with patch("server.routers.generator._run_pipeline_with_semaphore") as mock_pipeline, \
             patch("server.routers.generator.get_generator_service", return_value=mock_generator_service), \
             patch("asyncio.create_task") as mock_create_task:
            
            client = TestClient(app)
            
            # Create a file-like object for README
            readme_content = b"# Test Project\n\nThis is a test."
            files = {"files": ("README.md", BytesIO(readme_content), "text/markdown")}
            
            response = client.post(
                f"/api/v2/generator/{job_id}/upload",
                files=files
            )

            # Should return 200 OK
            assert response.status_code == 200
            
            # asyncio.create_task should NOT have been called (pipeline skipped)
            assert mock_create_task.call_count == 0

        # Cleanup
        if job_id in jobs_db:
            del jobs_db[job_id]


class TestBackgroundTaskLogging:
    """Test that appropriate log messages are generated when tasks are skipped."""

    @pytest.fixture
    def cleanup_env(self):
        """Clean up environment variable after test."""
        yield
        if "SKIP_BACKGROUND_TASKS" in os.environ:
            del os.environ["SKIP_BACKGROUND_TASKS"]

    @pytest.mark.asyncio
    async def test_skip_logs_are_generated(self, cleanup_env, caplog):
        """Test that skip messages are logged when SKIP_BACKGROUND_TASKS is set."""
        from server.routers.v1_compat import router
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        import logging

        # Set the environment variable
        os.environ["SKIP_BACKGROUND_TASKS"] = "1"

        # Set up logging capture
        caplog.set_level(logging.INFO)

        app = FastAPI()
        app.include_router(router)

        mock_generator = Mock()
        mock_generator.create_generation_job = AsyncMock()
        mock_omnicore = Mock()
        mock_omnicore.emit_event = AsyncMock()

        with patch("server.routers.v1_compat.get_generator_service", return_value=mock_generator), \
             patch("server.routers.v1_compat.get_omnicore_service", return_value=mock_omnicore):
            
            client = TestClient(app)
            response = client.post(
                "/api/v1/generate",
                json={"requirements": "Build a web app"}
            )

            # Verify response is still correct
            assert response.status_code == 202

            # Check that skip messages were logged
            log_messages = [record.message for record in caplog.records]
            skip_messages = [msg for msg in log_messages if "Skipping" in msg and "SKIP_BACKGROUND_TASKS" in msg]
            
            # Should have at least one skip message (could be 2: one for event, one for pipeline)
            assert len(skip_messages) >= 1
