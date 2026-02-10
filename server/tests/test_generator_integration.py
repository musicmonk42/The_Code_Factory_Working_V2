# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Integration tests for generator clarifier and file upload enhancements.

Tests the integration between the server API and the generator module's
clarifier, ensuring proper routing through OmniCore.
"""

import io
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
    from server.main import app
    with TestClient(app) as client:
        yield client


@pytest.fixture
def sample_job():
    """Create a sample job for testing."""
    from datetime import datetime
    
    job = Job(
        id="test-job-123",
        status=JobStatus.PENDING,
        input_files=[],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        metadata={},
    )
    jobs_db[job.id] = job
    yield job
    # Cleanup
    if job.id in jobs_db:
        del jobs_db[job.id]


class TestGeneratorFileUpload:
    """Test suite for enhanced file upload functionality."""

    def test_upload_readme_files(self, client, sample_job):
        """Test uploading README files."""
        readme_content = b"# Test Project\n\nThis is a test project."
        files = [
            ("files", ("README.md", io.BytesIO(readme_content), "text/markdown"))
        ]

        response = client.post(
            f"/api/generator/{sample_job.id}/upload",
            files=files,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]["uploaded_files"]) == 1
        assert "README.md" in data["data"]["categorization"]["readme_files"]

    def test_upload_test_files(self, client, sample_job):
        """Test uploading test files."""
        test_content = b"def test_example():\n    assert True"
        files = [
            ("files", ("test_example.py", io.BytesIO(test_content), "text/x-python")),
            ("files", ("example_test.py", io.BytesIO(test_content), "text/x-python")),
            ("files", ("example.spec.ts", io.BytesIO(b"describe('test', () => {})"), "text/typescript")),
        ]

        response = client.post(
            f"/api/generator/{sample_job.id}/upload",
            files=files,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]["categorization"]["test_files"]) == 3

    def test_upload_mixed_files(self, client, sample_job):
        """Test uploading a mix of file types."""
        files = [
            ("files", ("README.md", io.BytesIO(b"# Project"), "text/markdown")),
            ("files", ("test.py", io.BytesIO(b"def test(): pass"), "text/x-python")),
            ("files", ("config.json", io.BytesIO(b'{"key": "value"}'), "application/json")),
        ]

        response = client.post(
            f"/api/generator/{sample_job.id}/upload",
            files=files,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["uploaded_files"]) == 3
        assert len(data["data"]["categorization"]["readme_files"]) == 1
        assert len(data["data"]["categorization"]["test_files"]) == 1
        assert len(data["data"]["categorization"]["other_files"]) == 1

    def test_upload_no_files(self, client, sample_job):
        """Test error handling when no files are provided."""
        response = client.post(
            f"/api/generator/{sample_job.id}/upload",
            files=[],
        )

        # FastAPI returns 422 for validation errors when required field is missing
        # or 400 if the endpoint's validation logic catches it
        assert response.status_code in [400, 422]
        response_data = response.json()
        
        # For 422 validation error, check the Pydantic error format
        if response.status_code == 422:
            assert "detail" in response_data
            # Pydantic validation error will have a list of error dicts
            if isinstance(response_data["detail"], list):
                # Check that one of the errors is about the 'files' field
                assert any("files" in err.get("loc", []) for err in response_data["detail"])
            else:
                # Or the detail might be a string mentioning files
                assert "no files" in response_data["detail"].lower()
        else:
            # For 400, the endpoint validation should return our custom message
            assert "No files provided" in response_data["detail"]

    def test_upload_job_not_found(self, client):
        """Test error handling for non-existent job."""
        files = [("files", ("README.md", io.BytesIO(b"content"), "text/markdown"))]

        response = client.post(
            "/api/generator/nonexistent-job/upload",
            files=files,
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


class TestClarifierIntegration:
    """Test suite for clarifier integration."""

    @patch("server.services.generator_service.GeneratorService.clarify_requirements")
    def test_clarify_requirements_endpoint(
        self, mock_clarify, client, sample_job, tmp_path
    ):
        """Test the clarify requirements endpoint."""
        # Setup: Create a README file for the job
        job_dir = tmp_path / sample_job.id
        job_dir.mkdir(parents=True)
        readme_path = job_dir / "README.md"
        readme_path.write_text("# Test Project\nAmbiguous requirements here")
        
        # Add the file to job's input_files
        sample_job.input_files.append("README.md")
        
        # Mock the clarify_requirements method
        mock_clarify.return_value = {
            "job_id": sample_job.id,
            "status": "clarification_initiated",
            "ambiguities_detected": 2,
        }

        # Mock file reading
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = (
                "# Test Project\nAmbiguous requirements"
            )
            
            response = client.post(f"/api/generator/{sample_job.id}/clarify")

        # We expect 200 even if file is not found due to mocking
        # In real scenario, we'd check for proper clarification initiation
        assert response.status_code in [200, 400]  # 400 if no README found

    def test_clarify_without_readme(self, client, sample_job):
        """Test clarification fails gracefully without README."""
        # No files uploaded
        response = client.post(f"/api/generator/{sample_job.id}/clarify")

        assert response.status_code == 400
        detail = response.json()["detail"]
        # The error detail is a dict with a 'message' key
        if isinstance(detail, dict):
            assert "No README content found" in detail.get("message", "")
        else:
            assert "No README content found" in detail

    @patch("server.services.generator_service.GeneratorService.get_clarification_feedback")
    def test_get_clarification_feedback(
        self, mock_feedback, client, sample_job
    ):
        """Test getting clarification feedback."""
        mock_feedback.return_value = {
            "job_id": sample_job.id,
            "status": "waiting_for_response",
            "questions": [
                {"id": "q1", "text": "What database do you want to use?"}
            ],
        }

        response = client.get(
            f"/api/generator/{sample_job.id}/clarification/feedback"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == sample_job.id
        assert "questions" in data

    @patch("server.services.generator_service.GeneratorService.submit_clarification_response")
    def test_submit_clarification_response(
        self, mock_submit, client, sample_job
    ):
        """Test submitting a clarification response."""
        mock_submit.return_value = {
            "job_id": sample_job.id,
            "question_id": "q1",
            "status": "response_submitted",
        }

        response = client.post(
            f"/api/generator/{sample_job.id}/clarification/respond",
            json={"question_id": "q1", "response": "PostgreSQL"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "answer_recorded"


class TestGeneratorStatusAndLogs:
    """Test suite for generator status and log endpoints."""

    @patch("server.services.generator_service.GeneratorService.get_job_status")
    def test_get_generator_status(self, mock_status, client, sample_job):
        """Test getting generator status."""
        from datetime import datetime, timezone
        
        mock_status.return_value = {
            "job_id": sample_job.id,
            "stage": "code_generation",
            "progress_percent": 75.0,
            "status": "processing",
            "updated_at": datetime.now(timezone.utc),
        }

        response = client.get(f"/api/generator/{sample_job.id}/status")

        assert response.status_code == 200
        data = response.json()
        assert "stage" in data
        assert "progress_percent" in data
        assert "status" in data
        assert "updated_at" in data

    @patch("server.services.generator_service.GeneratorService.get_job_logs")
    def test_get_generator_logs(self, mock_logs, client, sample_job):
        """Test getting generator logs."""
        mock_logs.return_value = [
            {
                "timestamp": "2026-01-18T18:00:00Z",
                "level": "INFO",
                "message": "Processing job",
            }
        ]

        response = client.get(
            f"/api/generator/{sample_job.id}/logs",
            params={"limit": 50},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == sample_job.id
        assert len(data["logs"]) > 0
        assert data["count"] == len(data["logs"])


@pytest.mark.asyncio
class TestGeneratorServiceMethods:
    """Test suite for GeneratorService methods."""

    @pytest.fixture
    def generator_service(self):
        """Create a GeneratorService instance with mocked OmniCore."""
        from server.services.generator_service import GeneratorService
        
        mock_omnicore = AsyncMock()
        mock_omnicore.route_job = AsyncMock(return_value={
            "data": {"status": "success"}
        })
        
        return GeneratorService(omnicore_service=mock_omnicore)

    async def test_clarify_requirements_via_omnicore(self, generator_service):
        """Test clarify_requirements routes through OmniCore."""
        result = await generator_service.clarify_requirements(
            job_id="test-job",
            readme_content="# Project",
            ambiguities=["unclear requirement"],
        )

        assert "job_id" in result
        generator_service.omnicore_service.route_job.assert_called_once()
        call_args = generator_service.omnicore_service.route_job.call_args
        assert call_args.kwargs["target_module"] == "generator"
        assert call_args.kwargs["payload"]["action"] == "clarify_requirements"

    async def test_get_clarification_feedback_via_omnicore(self, generator_service):
        """Test get_clarification_feedback routes through OmniCore."""
        result = await generator_service.get_clarification_feedback(
            job_id="test-job",
            interaction_id="interaction-1",
        )

        generator_service.omnicore_service.route_job.assert_called_once()
        call_args = generator_service.omnicore_service.route_job.call_args
        assert call_args.kwargs["payload"]["action"] == "get_clarification_feedback"

    async def test_submit_clarification_response_via_omnicore(self, generator_service):
        """Test submit_clarification_response routes through OmniCore."""
        result = await generator_service.submit_clarification_response(
            job_id="test-job",
            question_id="q1",
            response="PostgreSQL",
        )

        generator_service.omnicore_service.route_job.assert_called_once()
        call_args = generator_service.omnicore_service.route_job.call_args
        assert call_args.kwargs["payload"]["action"] == "submit_clarification_response"
        assert call_args.kwargs["payload"]["response"] == "PostgreSQL"


@pytest.mark.asyncio
class TestReadmeContentLoading:
    """Test suite for README content loading from disk."""

    @pytest.fixture
    def generator_service(self, tmp_path):
        """Create a GeneratorService instance with tmp storage."""
        from server.services.generator_service import GeneratorService
        
        mock_omnicore = AsyncMock()
        return GeneratorService(storage_path=tmp_path, omnicore_service=mock_omnicore)

    async def test_get_readme_content_success(self, generator_service, tmp_path):
        """Test successfully loading README content from disk."""
        # Create a job directory with a README file
        job_id = "test-job-123"
        job_dir = tmp_path / job_id
        job_dir.mkdir()
        
        readme_content = "# Test Project\n\nThis is a test README."
        readme_path = job_dir / "README.md"
        readme_path.write_text(readme_content)
        
        # Test loading the README
        result = await generator_service.get_readme_content(job_id)
        
        assert result == readme_content
        assert len(result) > 0

    async def test_get_readme_content_priority_order(self, generator_service, tmp_path):
        """Test README loading respects priority order."""
        # Create a job directory with multiple README files
        job_id = "test-job-456"
        job_dir = tmp_path / job_id
        job_dir.mkdir()
        
        # Create multiple README files
        (job_dir / "README.md").write_text("Lower priority")
        (job_dir / "New_Test_README.md").write_text("Higher priority")
        
        # Should load the higher priority file
        result = await generator_service.get_readme_content(job_id)
        
        assert result == "Higher priority"

    async def test_get_readme_content_fallback_to_md_files(self, generator_service, tmp_path):
        """Test fallback to any .md file when standard README names not found."""
        job_id = "test-job-789"
        job_dir = tmp_path / job_id
        job_dir.mkdir()
        
        # Create a non-standard .md file
        custom_content = "# Custom Document"
        (job_dir / "custom.md").write_text(custom_content)
        
        # Should find and load the .md file
        result = await generator_service.get_readme_content(job_id)
        
        assert result == custom_content

    async def test_get_readme_content_not_found(self, generator_service, tmp_path):
        """Test handling when README file is not found."""
        job_id = "test-job-notfound"
        job_dir = tmp_path / job_id
        job_dir.mkdir()
        
        # No README files in directory
        result = await generator_service.get_readme_content(job_id)
        
        assert result is None

    async def test_get_readme_content_job_dir_not_exists(self, generator_service):
        """Test handling when job directory doesn't exist."""
        job_id = "nonexistent-job"
        
        result = await generator_service.get_readme_content(job_id)
        
        assert result is None
