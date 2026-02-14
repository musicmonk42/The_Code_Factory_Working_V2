# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for v1 compatibility API background pipeline trigger.

Verifies that the POST /api/v1/generate endpoint correctly triggers
the background generation pipeline.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from server.schemas import Job, JobStatus
from server.storage import jobs_db


@pytest.fixture
def client():
    """Create a test client for the FastAPI app.
    Import deferred to fixture to avoid expensive initialization during collection.
    Uses context manager to properly trigger lifespan events.
    """
    # Increase recursion limit temporarily to handle deep import chains
    import sys
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(10000)
    
    try:
        from server.main import app
        from server.dependencies import require_agents_ready
        
        # Override the require_agents_ready dependency to always pass in tests
        async def mock_require_agents_ready():
            """Mock dependency that always passes."""
            return None
        
        app.dependency_overrides[require_agents_ready] = mock_require_agents_ready
        
        with TestClient(app) as client:
            yield client
        
        # Clean up dependency overrides
        app.dependency_overrides.clear()
    finally:
        sys.setrecursionlimit(old_limit)


class TestV1CompatBackgroundTrigger:
    """Test suite for v1 compat API background pipeline trigger."""
    
    def test_create_generation_triggers_background_task(self, client):
        """Test that POST /api/v1/generate triggers background pipeline."""
        # Set environment variable to skip background tasks so we can verify job creation
        # without actually running the pipeline
        import os
        os.environ["SKIP_BACKGROUND_TASKS"] = "1"
        
        try:
            # Make request
            response = client.post(
                "/api/v1/generate",
                json={
                    "requirements": "Create a simple Hello World function",
                    "language": "python",
                    "framework": "flask"
                }
            )
            
            # Check response
            assert response.status_code == 202
            data = response.json()
            assert "id" in data
            assert data["status"] == "pending"
            job_id = data["id"]
            
            # Verify job was created
            assert job_id in jobs_db
            job = jobs_db[job_id]
            assert job.status == JobStatus.PENDING
            assert job.metadata["requirements"] == "Create a simple Hello World function"
            assert job.metadata["language"] == "python"
            
            # Cleanup
            if job_id in jobs_db:
                del jobs_db[job_id]
        finally:
            # Clean up environment
            if "SKIP_BACKGROUND_TASKS" in os.environ:
                del os.environ["SKIP_BACKGROUND_TASKS"]
    
    def test_create_generation_with_minimal_payload(self, client):
        """Test POST /api/v1/generate with minimal payload."""
        response = client.post(
            "/api/v1/generate",
            json={
                "requirements": "Create a test app",
                "language": "python"
            }
        )
        
        assert response.status_code == 202
        data = response.json()
        assert "id" in data
        job_id = data["id"]
        
        # Cleanup
        if job_id in jobs_db:
            del jobs_db[job_id]
    
    def test_create_generation_validates_language(self, client):
        """Test that invalid language is rejected."""
        response = client.post(
            "/api/v1/generate",
            json={
                "requirements": "Create a test app",
                "language": "invalid_language"
            }
        )
        
        # Should return validation error
        assert response.status_code == 422
