"""
Integration tests for SFE monitoring and interaction enhancements.

Tests the integration between the server API and the self_fixing_engineer module,
ensuring proper routing through OmniCore and real-time monitoring capabilities.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from server.main import app
from server.schemas import Job, JobStatus
from server.storage import jobs_db


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def sample_job():
    """Create a sample job for testing."""
    from datetime import datetime
    
    job = Job(
        id="test-sfe-job-456",
        status=JobStatus.RUNNING,
        input_files=["main.py", "test_main.py"],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        metadata={},
    )
    jobs_db[job.id] = job
    yield job
    # Cleanup
    if job.id in jobs_db:
        del jobs_db[job.id]


class TestSFEStatus:
    """Test suite for SFE status monitoring."""

    @patch("server.services.sfe_service.SFEService.get_sfe_status")
    def test_get_sfe_status(self, mock_status, client, sample_job):
        """Test getting detailed SFE status."""
        mock_status.return_value = {
            "job_id": sample_job.id,
            "status": "analyzing",
            "current_operation": "scanning_codebase",
            "progress_percentage": 45.0,
            "operations_history": [
                {"timestamp": "2026-01-18T18:00:00Z", "operation": "scan_started"},
                {"timestamp": "2026-01-18T18:05:00Z", "operation": "errors_detected"},
            ],
        }

        response = client.get(f"/api/sfe/{sample_job.id}/status")

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == sample_job.id
        assert data["status"] == "analyzing"
        assert "current_operation" in data
        assert "progress_percentage" in data
        assert len(data["operations_history"]) == 2

    def test_get_sfe_status_job_not_found(self, client):
        """Test error handling for non-existent job."""
        response = client.get("/api/sfe/nonexistent-job/status")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


class TestSFELogs:
    """Test suite for SFE log retrieval."""

    @patch("server.services.sfe_service.SFEService.get_sfe_logs")
    def test_get_sfe_logs_default(self, mock_logs, client, sample_job):
        """Test getting SFE logs with default parameters."""
        mock_logs.return_value = [
            {
                "timestamp": "2026-01-18T18:00:00Z",
                "level": "INFO",
                "message": "Starting analysis",
                "module": "self_fixing_engineer.arbiter",
            },
            {
                "timestamp": "2026-01-18T18:01:00Z",
                "level": "WARNING",
                "message": "Potential issue detected",
                "module": "self_fixing_engineer.bug_manager",
            },
        ]

        response = client.get(f"/api/sfe/{sample_job.id}/logs")

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == sample_job.id
        assert len(data["logs"]) == 2
        assert data["count"] == 2
        mock_logs.assert_called_once_with(sample_job.id, limit=100, level=None)

    @patch("server.services.sfe_service.SFEService.get_sfe_logs")
    def test_get_sfe_logs_with_limit(self, mock_logs, client, sample_job):
        """Test getting SFE logs with custom limit."""
        mock_logs.return_value = []

        response = client.get(
            f"/api/sfe/{sample_job.id}/logs",
            params={"limit": 50},
        )

        assert response.status_code == 200
        mock_logs.assert_called_once_with(sample_job.id, limit=50, level=None)

    @patch("server.services.sfe_service.SFEService.get_sfe_logs")
    def test_get_sfe_logs_with_level_filter(self, mock_logs, client, sample_job):
        """Test getting SFE logs filtered by level."""
        mock_logs.return_value = [
            {
                "timestamp": "2026-01-18T18:00:00Z",
                "level": "ERROR",
                "message": "Critical error detected",
                "module": "self_fixing_engineer.arbiter",
            }
        ]

        response = client.get(
            f"/api/sfe/{sample_job.id}/logs",
            params={"level": "ERROR"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["logs"]) == 1
        assert data["logs"][0]["level"] == "ERROR"
        mock_logs.assert_called_once_with(sample_job.id, limit=100, level="ERROR")

    def test_get_sfe_logs_job_not_found(self, client):
        """Test error handling for non-existent job."""
        response = client.get("/api/sfe/nonexistent-job/logs")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


class TestSFEInteraction:
    """Test suite for SFE interactive commands."""

    @patch("server.services.sfe_service.SFEService.interact_with_sfe")
    def test_pause_command(self, mock_interact, client, sample_job):
        """Test sending pause command to SFE."""
        mock_interact.return_value = {
            "job_id": sample_job.id,
            "command": "pause",
            "status": "command_executed",
        }

        response = client.post(
            f"/api/sfe/{sample_job.id}/interact",
            params={"command": "pause", "params": {}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["command"] == "pause"
        assert data["status"] == "command_executed"
        mock_interact.assert_called_once()

    @patch("server.services.sfe_service.SFEService.interact_with_sfe")
    def test_resume_command(self, mock_interact, client, sample_job):
        """Test sending resume command to SFE."""
        mock_interact.return_value = {
            "job_id": sample_job.id,
            "command": "resume",
            "status": "command_executed",
        }

        response = client.post(
            f"/api/sfe/{sample_job.id}/interact",
            params={"command": "resume"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["command"] == "resume"

    @patch("server.services.sfe_service.SFEService.interact_with_sfe")
    def test_analyze_file_command(self, mock_interact, client, sample_job):
        """Test sending analyze_file command with parameters."""
        mock_interact.return_value = {
            "job_id": sample_job.id,
            "command": "analyze_file",
            "status": "command_executed",
        }

        response = client.post(
            f"/api/sfe/{sample_job.id}/interact",
            params={
                "command": "analyze_file",
                "params": {"file_path": "src/main.py"},
            },
        )

        assert response.status_code == 200

    def test_invalid_command(self, client, sample_job):
        """Test error handling for invalid command."""
        response = client.post(
            f"/api/sfe/{sample_job.id}/interact",
            params={"command": "invalid_command"},
        )

        assert response.status_code == 400
        assert "Invalid command" in response.json()["detail"]

    def test_interact_job_not_found(self, client):
        """Test error handling for non-existent job."""
        response = client.post(
            "/api/sfe/nonexistent-job/interact",
            params={"command": "pause"},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


class TestSFELearningInsights:
    """Test suite for SFE meta-learning insights."""

    @patch("server.services.sfe_service.SFEService.get_learning_insights")
    def test_get_global_insights(self, mock_insights, client):
        """Test getting global learning insights."""
        mock_insights.return_value = {
            "total_fixes": 250,
            "success_rate": 0.88,
            "common_patterns": ["missing_imports", "type_errors", "syntax_errors"],
        }

        response = client.get("/api/sfe/insights")

        assert response.status_code == 200
        data = response.json()
        assert "total_fixes" in data
        assert "success_rate" in data
        assert "common_patterns" in data
        mock_insights.assert_called_once_with(job_id=None)

    @patch("server.services.sfe_service.SFEService.get_learning_insights")
    def test_get_job_specific_insights(self, mock_insights, client):
        """Test getting job-specific learning insights."""
        mock_insights.return_value = {
            "job_id": "test-job",
            "total_fixes": 5,
            "success_rate": 1.0,
        }

        response = client.get("/api/sfe/insights", params={"job_id": "test-job"})

        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        mock_insights.assert_called_once_with(job_id="test-job")


@pytest.mark.asyncio
class TestSFEServiceMethods:
    """Test suite for SFEService methods."""

    @pytest.fixture
    def sfe_service(self):
        """Create an SFEService instance with mocked OmniCore."""
        from server.services.sfe_service import SFEService
        
        mock_omnicore = AsyncMock()
        mock_omnicore.route_job = AsyncMock(return_value={
            "data": {"status": "success"}
        })
        
        return SFEService(omnicore_service=mock_omnicore)

    async def test_get_sfe_status_via_omnicore(self, sfe_service):
        """Test get_sfe_status routes through OmniCore."""
        result = await sfe_service.get_sfe_status(job_id="test-job")

        sfe_service.omnicore_service.route_job.assert_called_once()
        call_args = sfe_service.omnicore_service.route_job.call_args
        assert call_args.kwargs["target_module"] == "sfe"
        assert call_args.kwargs["payload"]["action"] == "get_sfe_status"

    async def test_get_sfe_logs_via_omnicore(self, sfe_service):
        """Test get_sfe_logs routes through OmniCore."""
        result = await sfe_service.get_sfe_logs(
            job_id="test-job",
            limit=50,
            level="ERROR",
        )

        sfe_service.omnicore_service.route_job.assert_called_once()
        call_args = sfe_service.omnicore_service.route_job.call_args
        assert call_args.kwargs["payload"]["action"] == "get_sfe_logs"
        assert call_args.kwargs["payload"]["limit"] == 50
        assert call_args.kwargs["payload"]["level"] == "ERROR"

    async def test_interact_with_sfe_via_omnicore(self, sfe_service):
        """Test interact_with_sfe routes through OmniCore."""
        result = await sfe_service.interact_with_sfe(
            job_id="test-job",
            command="pause",
            params={},
        )

        sfe_service.omnicore_service.route_job.assert_called_once()
        call_args = sfe_service.omnicore_service.route_job.call_args
        assert call_args.kwargs["payload"]["action"] == "sfe_command"
        assert call_args.kwargs["payload"]["command"] == "pause"

    async def test_get_learning_insights_via_omnicore(self, sfe_service):
        """Test get_learning_insights routes through OmniCore."""
        result = await sfe_service.get_learning_insights(job_id="test-job")

        sfe_service.omnicore_service.route_job.assert_called_once()
        call_args = sfe_service.omnicore_service.route_job.call_args
        assert call_args.kwargs["payload"]["action"] == "get_learning_insights"

    async def test_fallback_when_omnicore_unavailable(self):
        """Test fallback behavior when OmniCore is unavailable."""
        from server.services.sfe_service import SFEService
        
        sfe_service = SFEService(omnicore_service=None)
        
        result = await sfe_service.get_sfe_status(job_id="test-job")
        
        # Should use fallback
        assert "job_id" in result
        assert "fallback" in result.get("sfe_module", "")
