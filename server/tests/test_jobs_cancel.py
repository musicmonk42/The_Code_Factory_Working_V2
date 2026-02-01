"""
Test the POST /jobs/{job_id}/cancel endpoint.
"""
import pytest
from datetime import datetime
from fastapi.testclient import TestClient

from server.main import app
from server.schemas import JobStatus, JobStage
from server.storage import jobs_db


@pytest.fixture
def client():
    """Create a test client."""
    from server.main import _load_routers, _include_routers
    
    # Manually load and include routers for test environment
    # TestClient doesn't automatically trigger lifespan, so we do it manually
    if _load_routers():
        _include_routers(app)
    
    return TestClient(app)


@pytest.fixture
def sample_job():
    """Create a sample job for testing."""
    from server.schemas import Job
    job = Job(
        id="test-job-123",
        status=JobStatus.RUNNING,
        current_stage=JobStage.GENERATOR_GENERATION,
        input_files=["README.md"],
        output_files=[],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        metadata={},
    )
    jobs_db[job.id] = job
    yield job
    # Cleanup
    if job.id in jobs_db:
        del jobs_db[job.id]


def test_cancel_job_post_endpoint_exists(client, sample_job):
    """Test that POST /jobs/{job_id}/cancel endpoint exists and works."""
    response = client.post(f"/api/jobs/{sample_job.id}/cancel")
    
    # Should return 200 OK
    assert response.status_code == 200
    
    # Should return success response
    data = response.json()
    assert data["success"] is True
    assert "cancelled successfully" in data["message"]
    
    # Job should be marked as cancelled
    job = jobs_db[sample_job.id]
    assert job.status == JobStatus.CANCELLED


def test_cancel_job_post_not_found(client):
    """Test that POST /jobs/{job_id}/cancel returns 404 for non-existent job."""
    response = client.post("/api/jobs/non-existent-job/cancel")
    assert response.status_code == 404


def test_cancel_job_post_already_completed(client, sample_job):
    """Test that POST /jobs/{job_id}/cancel returns 400 for completed job."""
    # Mark job as completed
    sample_job.status = JobStatus.COMPLETED
    
    response = client.post(f"/api/jobs/{sample_job.id}/cancel")
    assert response.status_code == 400
    assert "cannot be cancelled" in response.json()["detail"]
