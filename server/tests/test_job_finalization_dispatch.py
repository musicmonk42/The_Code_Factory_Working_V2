"""
Tests for Job Finalization and Dispatch Services

This test suite validates the enterprise-grade job finalization and dispatch
services following industry best practices:
- Unit test isolation with mocking
- Comprehensive test coverage
- Clear test naming and documentation
- Industry-standard assertions

Test Categories:
1. Job Finalization Tests
   - Success finalization
   - Failure finalization
   - Idempotency verification
   - Manifest generation
   
2. Dispatch Service Tests
   - Circuit breaker functionality
   - Kafka dispatch
   - Webhook fallback
   - Health status reporting
"""

import asyncio
import pytest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

# Import the services to test
from server.services.job_finalization import (
    finalize_job_success,
    finalize_job_failure,
    reset_finalization_state,
    _generate_output_manifest,
)
from server.services.dispatch_service import (
    kafka_available,
    mark_kafka_failure,
    mark_kafka_success,
    get_kafka_health_status,
    CircuitBreakerState,
)
from server.schemas import JobStatus, JobStage


class TestJobFinalization:
    """Test suite for job finalization service."""
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """Reset state before and after each test."""
        reset_finalization_state()
        yield
        reset_finalization_state()
    
    @pytest.fixture
    def mock_jobs_db(self):
        """Mock the jobs database."""
        with patch('server.services.job_finalization.jobs_db') as mock_db:
            # Create a simple mock job
            mock_job = MagicMock()
            mock_job.status = JobStatus.RUNNING
            mock_job.current_stage = JobStage.GENERATOR_GENERATION
            mock_job.metadata = {}
            mock_job.output_files = []
            mock_db.__contains__ = MagicMock(return_value=True)
            mock_db.__getitem__ = MagicMock(return_value=mock_job)
            yield mock_db, mock_job
    
    @pytest.mark.asyncio
    async def test_finalize_job_success_basic(self, mock_jobs_db):
        """Test basic successful job finalization."""
        mock_db, mock_job = mock_jobs_db
        
        # Mock manifest generation to return None (no files)
        with patch('server.services.job_finalization._generate_output_manifest') as mock_manifest:
            mock_manifest.return_value = None
            
            result = await finalize_job_success("test-job-123")
            
            assert result is True
            assert mock_job.status == JobStatus.COMPLETED
            assert mock_job.current_stage == JobStage.COMPLETED
            assert mock_job.completed_at is not None
            assert "finalized_at" in mock_job.metadata
    
    @pytest.mark.asyncio
    async def test_finalize_job_success_with_manifest(self, mock_jobs_db):
        """Test job finalization with output manifest."""
        mock_db, mock_job = mock_jobs_db
        
        # Mock manifest with test files
        test_manifest = {
            "job_id": "test-job-123",
            "files": [
                {"path": "app.py", "size": 1024, "name": "app.py"},
                {"path": "tests.py", "size": 512, "name": "tests.py"},
            ],
            "total_files": 2,
            "total_size": 1536,
        }
        
        with patch('server.services.job_finalization._generate_output_manifest') as mock_manifest:
            mock_manifest.return_value = test_manifest
            
            result = await finalize_job_success("test-job-123")
            
            assert result is True
            assert mock_job.status == JobStatus.COMPLETED
            assert "output_manifest" in mock_job.metadata
            assert mock_job.metadata["total_output_files"] == 2
            assert len(mock_job.output_files) == 2
    
    @pytest.mark.asyncio
    async def test_finalize_job_success_idempotency(self, mock_jobs_db):
        """Test that finalization is idempotent (can be called multiple times safely)."""
        mock_db, mock_job = mock_jobs_db
        
        with patch('server.services.job_finalization._generate_output_manifest') as mock_manifest:
            mock_manifest.return_value = None
            
            # First call should finalize
            result1 = await finalize_job_success("test-job-123")
            assert result1 is True
            
            # Second call should skip (idempotent)
            result2 = await finalize_job_success("test-job-123")
            assert result2 is True
            
            # Manifest should only be generated once
            assert mock_manifest.call_count == 1
    
    @pytest.mark.asyncio
    async def test_finalize_job_success_job_not_found(self):
        """Test finalization fails gracefully when job doesn't exist."""
        with patch('server.services.job_finalization.jobs_db') as mock_db:
            mock_db.__contains__ = MagicMock(return_value=False)
            
            result = await finalize_job_success("nonexistent-job")
            
            assert result is False
    
    @pytest.mark.asyncio
    async def test_finalize_job_failure_basic(self, mock_jobs_db):
        """Test basic failure finalization."""
        mock_db, mock_job = mock_jobs_db
        
        test_error = Exception("Test error message")
        result = await finalize_job_failure("test-job-123", test_error)
        
        assert result is True
        assert mock_job.status == JobStatus.FAILED
        assert mock_job.completed_at is not None
        assert mock_job.metadata["error"] == "Test error message"
        assert mock_job.metadata["error_type"] == "Exception"
        assert "finalized_at" in mock_job.metadata
    
    @pytest.mark.asyncio
    async def test_finalize_job_failure_with_traceback(self, mock_jobs_db):
        """Test that failure finalization captures traceback."""
        mock_db, mock_job = mock_jobs_db
        
        test_error = ValueError("Invalid value")
        result = await finalize_job_failure("test-job-123", test_error)
        
        assert result is True
        assert "error_traceback" in mock_job.metadata
        assert isinstance(mock_job.metadata["error_traceback"], str)


class TestDispatchService:
    """Test suite for dispatch service with circuit breaker."""
    
    @pytest.fixture(autouse=True)
    def reset_circuit_breaker(self):
        """Reset circuit breaker state before each test."""
        import server.services.dispatch_service as ds
        ds._kafka_circuit_state = CircuitBreakerState.CLOSED
        ds._kafka_consecutive_failures = 0
        ds._kafka_last_check = None
        yield
        # Reset after test
        ds._kafka_circuit_state = CircuitBreakerState.CLOSED
        ds._kafka_consecutive_failures = 0
        ds._kafka_last_check = None
    
    def test_kafka_available_when_disabled(self):
        """Test that kafka_available returns False when Kafka is disabled."""
        with patch.dict('os.environ', {'KAFKA_ENABLED': 'false'}):
            assert kafka_available() is False
    
    def test_kafka_available_initial_state(self):
        """Test that kafka_available returns True initially when enabled."""
        with patch.dict('os.environ', {'KAFKA_ENABLED': 'true'}):
            assert kafka_available() is True
    
    def test_circuit_breaker_opens_after_threshold(self):
        """Test that circuit breaker opens after consecutive failures."""
        with patch.dict('os.environ', {'KAFKA_ENABLED': 'true'}):
            # Circuit should start closed
            assert kafka_available() is True
            
            # Mark failures up to threshold
            mark_kafka_failure()  # Failure 1
            assert kafka_available() is True  # Still closed
            
            mark_kafka_failure()  # Failure 2
            assert kafka_available() is True  # Still closed
            
            mark_kafka_failure()  # Failure 3 - should open circuit
            assert kafka_available() is False  # Circuit now open
    
    def test_circuit_breaker_closes_on_success(self):
        """Test that circuit breaker closes on successful connection."""
        with patch.dict('os.environ', {'KAFKA_ENABLED': 'true'}):
            # Open the circuit
            mark_kafka_failure()
            mark_kafka_failure()
            mark_kafka_failure()
            assert kafka_available() is False
            
            # Success should close circuit
            mark_kafka_success()
            assert kafka_available() is True
    
    def test_get_kafka_health_status_disabled(self):
        """Test health status when Kafka is disabled."""
        with patch.dict('os.environ', {'KAFKA_ENABLED': 'false'}):
            status = get_kafka_health_status()
            
            assert status["enabled"] is False
            assert status["status"] == "disabled"
    
    def test_get_kafka_health_status_available(self):
        """Test health status when Kafka is available."""
        with patch.dict('os.environ', {'KAFKA_ENABLED': 'true'}):
            # Reset to closed state
            mark_kafka_success()
            
            status = get_kafka_health_status()
            
            assert status["enabled"] is True
            assert status["status"] == "available"
            assert status["circuit_breaker_open"] is False
            assert status["consecutive_failures"] == 0
    
    def test_get_kafka_health_status_circuit_open(self):
        """Test health status when circuit breaker is open."""
        with patch.dict('os.environ', {'KAFKA_ENABLED': 'true'}):
            # Open the circuit
            mark_kafka_failure()
            mark_kafka_failure()
            mark_kafka_failure()
            
            status = get_kafka_health_status()
            
            assert status["enabled"] is True
            assert status["status"] == "unavailable"
            assert status["circuit_breaker_open"] is True
            assert status["consecutive_failures"] == 3


class TestManifestGeneration:
    """Test suite for output manifest generation."""
    
    @pytest.mark.asyncio
    async def test_generate_manifest_no_directory(self):
        """Test manifest generation when directory doesn't exist."""
        result = await _generate_output_manifest("nonexistent-job", "test-correlation")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_generate_manifest_empty_directory(self, tmp_path):
        """Test manifest generation with empty directory."""
        # Create empty job directory
        job_dir = tmp_path / "uploads" / "test-job"
        job_dir.mkdir(parents=True)
        
        with patch('server.services.job_finalization.Path') as mock_path:
            mock_path.return_value.resolve.return_value = job_dir
            mock_path.return_value.exists.return_value = True
            mock_path.return_value.rglob.return_value = []
            
            result = await _generate_output_manifest("test-job", "test-correlation")
            assert result is None
    
    @pytest.mark.asyncio
    async def test_generate_manifest_with_files(self, tmp_path):
        """Test manifest generation with actual files."""
        # Create test directory structure
        job_dir = tmp_path / "uploads" / "test-job"
        job_dir.mkdir(parents=True)
        
        # Create test files
        (job_dir / "app.py").write_text("print('hello')")
        (job_dir / "README.md").write_text("# Test")
        
        sub_dir = job_dir / "tests"
        sub_dir.mkdir()
        (sub_dir / "test_app.py").write_text("def test(): pass")
        
        # Patch Path to return our test directory
        with patch('server.services.job_finalization.Path') as mock_path:
            mock_path_instance = MagicMock()
            # Make resolve() return the mock instance itself, not the real job_dir
            mock_path_instance.resolve.return_value = mock_path_instance
            mock_path_instance.exists.return_value = True
            # Store the job_dir for string comparison in security checks
            mock_path_instance.__str__.return_value = str(job_dir)
            
            # Create file mocks
            def create_file_mock(path, size):
                file_mock = MagicMock()
                file_mock.is_file.return_value = True
                file_mock.resolve.return_value = job_dir / path
                file_mock.relative_to.return_value = Path(path)
                file_mock.name = Path(path).name
                file_mock.suffix = Path(path).suffix
                stat_mock = MagicMock()
                stat_mock.st_size = size
                stat_mock.st_mtime = datetime.now(timezone.utc).timestamp()
                file_mock.stat.return_value = stat_mock
                return file_mock
            
            files = [
                create_file_mock("app.py", 100),
                create_file_mock("README.md", 50),
                create_file_mock("tests/test_app.py", 75),
            ]
            
            mock_path_instance.rglob.return_value = files
            mock_path.return_value = mock_path_instance
            
            result = await _generate_output_manifest("test-job", "test-correlation")
            
            assert result is not None
            assert result["total_files"] == 3
            assert result["total_size"] == 225  # 100 + 50 + 75
            assert len(result["files"]) == 3


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "--tb=short"])
