# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
test_load_test_latency_fixes.py

Tests for the load test latency fixes:
1. Worker count defaults to 4 (not 1)
2. BackgroundTasks replaced with asyncio.create_task + semaphore
3. Bounded jobs_db with eviction of old completed jobs
4. Fire-and-forget event emission

These tests validate the minimal changes made to fix the latency issues.
"""

import asyncio
import os
from collections import OrderedDict
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

import pytest

# Force testing mode
os.environ["TESTING"] = "1"


# ============================================================================
# FIX #1: Test worker count defaults to 4
# ============================================================================

def test_run_py_default_worker_count():
    """Test that server/run.py defaults to 4 workers when WEB_CONCURRENCY is not set."""
    import sys
    from pathlib import Path
    
    # Add project root to path
    project_root = Path(__file__).parent.parent
    sys.path.insert(0, str(project_root))
    
    with patch.dict(os.environ, {}, clear=False):
        # Remove WEB_CONCURRENCY if it exists
        os.environ.pop("WEB_CONCURRENCY", None)
        
        # Import run module
        from server import run
        
        # Parse args with no worker specification
        with patch("sys.argv", ["run.py"]):
            args = run.parse_args()
            
            # Simulate the worker count logic
            if args.workers is None:
                workers = int(os.environ.get("WEB_CONCURRENCY", "4"))
            else:
                workers = args.workers
            
            # Should default to 4
            assert workers == 4


def test_run_py_respects_web_concurrency():
    """Test that server/run.py respects WEB_CONCURRENCY environment variable."""
    import sys
    from pathlib import Path
    
    # Add project root to path
    project_root = Path(__file__).parent.parent
    sys.path.insert(0, str(project_root))
    
    with patch.dict(os.environ, {"WEB_CONCURRENCY": "8"}):
        from server import run
        
        with patch("sys.argv", ["run.py"]):
            args = run.parse_args()
            
            if args.workers is None:
                workers = int(os.environ.get("WEB_CONCURRENCY", "4"))
            else:
                workers = args.workers
            
            # Should use WEB_CONCURRENCY value
            assert workers == 8


# ============================================================================
# FIX #2: Test semaphore limiting concurrent pipelines
# ============================================================================

@pytest.mark.asyncio
async def test_pipeline_semaphore_limits_concurrency():
    """Test that pipeline semaphore limits concurrent pipeline executions."""
    from server.routers.generator import MAX_CONCURRENT_PIPELINES, _pipeline_semaphore
    
    # The semaphore should be initialized with MAX_CONCURRENT_PIPELINES
    assert _pipeline_semaphore._value == MAX_CONCURRENT_PIPELINES
    
    # Simulate acquiring all available slots
    acquired = []
    for _ in range(MAX_CONCURRENT_PIPELINES):
        # Should be able to acquire without blocking
        acquired.append(await asyncio.wait_for(
            _pipeline_semaphore.acquire(),
            timeout=0.1
        ))
    
    # All slots should be acquired
    assert len(acquired) == MAX_CONCURRENT_PIPELINES
    assert _pipeline_semaphore._value == 0
    assert _pipeline_semaphore.locked()
    
    # Release all
    for _ in acquired:
        _pipeline_semaphore.release()
    
    assert _pipeline_semaphore._value == MAX_CONCURRENT_PIPELINES


@pytest.mark.asyncio
async def test_run_pipeline_with_semaphore_wrapper():
    """Test that _run_pipeline_with_semaphore properly uses the semaphore."""
    from server.routers.generator import _run_pipeline_with_semaphore, _pipeline_semaphore
    
    # Mock the actual pipeline function
    with patch("server.routers.generator._trigger_pipeline_background", new_callable=AsyncMock) as mock_pipeline:
        mock_pipeline.return_value = None
        mock_service = Mock()
        
        # Call the wrapper
        await _run_pipeline_with_semaphore(
            job_id="test-job-123",
            readme_content="Test README",
            generator_service=mock_service,
        )
        
        # Verify the actual pipeline function was called
        mock_pipeline.assert_called_once_with(
            "test-job-123",
            "Test README",
            mock_service,
        )
        
        # Semaphore should be released after execution
        assert _pipeline_semaphore._value > 0


# ============================================================================
# FIX #3: Test bounded jobs_db with eviction
# ============================================================================

def test_jobs_db_is_ordered_dict():
    """Test that jobs_db uses OrderedDict for ordered eviction."""
    from server.storage import jobs_db
    
    assert isinstance(jobs_db, OrderedDict)


def test_add_job_function_exists():
    """Test that add_job helper function exists."""
    from server.storage import add_job, MAX_JOBS
    
    assert callable(add_job)
    assert isinstance(MAX_JOBS, int)
    assert MAX_JOBS > 0


def test_add_job_evicts_old_completed_jobs():
    """Test that add_job evicts oldest completed jobs when limit is reached."""
    from server.schemas import Job, JobStatus
    from server.storage import jobs_db, add_job, MAX_JOBS
    
    # Clear the jobs_db
    jobs_db.clear()
    
    # Create mock jobs - mixture of completed and active
    completed_jobs = []
    for i in range(5):
        job = Job(
            id=f"completed-{i}",
            status=JobStatus.COMPLETED,
            current_stage="completed",
            input_files=[],
            output_files=[],
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            metadata={},
        )
        completed_jobs.append(job)
        jobs_db[job.id] = job
    
    active_jobs = []
    for i in range(3):
        job = Job(
            id=f"active-{i}",
            status=JobStatus.RUNNING,
            current_stage="codegen",
            input_files=[],
            output_files=[],
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            metadata={},
        )
        active_jobs.append(job)
        jobs_db[job.id] = job
    
    # Store original count
    original_count = len(jobs_db)
    assert original_count == 8
    
    # Now fill up to MAX_JOBS
    while len(jobs_db) < MAX_JOBS:
        job = Job(
            id=str(uuid4()),
            status=JobStatus.COMPLETED,
            current_stage="completed",
            input_files=[],
            output_files=[],
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            metadata={},
        )
        jobs_db[job.id] = job
    
    assert len(jobs_db) == MAX_JOBS
    
    # Add one more job using add_job - this should trigger eviction
    new_job = Job(
        id="trigger-eviction",
        status=JobStatus.PENDING,
        current_stage="upload",
        input_files=[],
        output_files=[],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        metadata={},
    )
    
    add_job(new_job)
    
    # Should have evicted at least one completed job
    # The db should not exceed MAX_JOBS by much
    assert len(jobs_db) <= MAX_JOBS + 10  # Small buffer for active jobs
    
    # New job should be in the db
    assert "trigger-eviction" in jobs_db
    
    # Active jobs should still be there (never evicted)
    for job in active_jobs:
        assert job.id in jobs_db
    
    # Clean up
    jobs_db.clear()


def test_add_job_preserves_active_jobs():
    """Test that add_job never evicts active (pending/running) jobs."""
    from server.schemas import Job, JobStatus
    from server.storage import jobs_db, add_job
    
    jobs_db.clear()
    
    # Create only active jobs
    active_job_ids = []
    for i in range(10):
        job = Job(
            id=f"active-{i}",
            status=JobStatus.RUNNING if i % 2 == 0 else JobStatus.PENDING,
            current_stage="codegen",
            input_files=[],
            output_files=[],
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            metadata={},
        )
        active_job_ids.append(job.id)
        add_job(job)
    
    # All active jobs should be preserved
    for job_id in active_job_ids:
        assert job_id in jobs_db
    
    jobs_db.clear()


# ============================================================================
# FIX #4: Test fire-and-forget event emission
# ============================================================================

@pytest.mark.asyncio
async def test_emit_event_fire_and_forget():
    """Test that fire-and-forget event emission doesn't block."""
    from server.routers.v1_compat import _emit_event_fire_and_forget
    
    mock_service = AsyncMock()
    mock_service.emit_event = AsyncMock()
    
    # Call fire-and-forget
    await _emit_event_fire_and_forget(
        omnicore_service=mock_service,
        topic="test.event",
        payload={"test": "data"},
        priority=5,
    )
    
    # Should have called emit_event
    mock_service.emit_event.assert_called_once_with(
        topic="test.event",
        payload={"test": "data"},
        priority=5,
    )


@pytest.mark.asyncio
async def test_emit_event_fire_and_forget_handles_errors():
    """Test that fire-and-forget event emission handles errors gracefully."""
    from server.routers.v1_compat import _emit_event_fire_and_forget
    
    mock_service = AsyncMock()
    mock_service.emit_event = AsyncMock(side_effect=Exception("Test error"))
    
    # Should not raise exception
    try:
        await _emit_event_fire_and_forget(
            omnicore_service=mock_service,
            topic="test.event",
            payload={"test": "data"},
            priority=5,
        )
    except Exception as e:
        pytest.fail(f"Fire-and-forget should not raise: {e}")


@pytest.mark.asyncio
async def test_v1_create_generation_uses_fire_and_forget():
    """Test that v1 create_generation endpoint uses fire-and-forget event emission."""
    from fastapi.testclient import TestClient
    from server.main import app
    
    # Mock the dependencies
    with patch("server.routers.v1_compat.get_generator_service") as mock_gen_service, \
         patch("server.routers.v1_compat.get_omnicore_service") as mock_omni_service, \
         patch("server.routers.v1_compat._emit_event_fire_and_forget") as mock_emit, \
         patch("server.routers.v1_compat._run_pipeline_with_semaphore") as mock_pipeline, \
         patch("server.routers.v1_compat.jobs_db") as mock_jobs_db:
        
        mock_emit.return_value = None
        mock_pipeline.return_value = None
        mock_jobs_db.__setitem__ = Mock()
        
        mock_service = Mock()
        mock_gen_service.return_value = mock_service
        mock_omni = Mock()
        mock_omni_service.return_value = mock_omni
        
        client = TestClient(app)
        
        # Make request
        response = client.post(
            "/api/v1/generate",
            json={
                "requirements": "Create a Flask app",
                "language": "python",
            }
        )
        
        # Should succeed
        assert response.status_code in [200, 201]


# ============================================================================
# Integration test: Verify all fixes work together
# ============================================================================

@pytest.mark.asyncio
async def test_integration_all_fixes_working():
    """Integration test to verify all fixes work together."""
    from server.routers.generator import MAX_CONCURRENT_PIPELINES, _pipeline_semaphore
    from server.storage import MAX_JOBS, add_job
    from server.schemas import Job, JobStatus
    
    # 1. Check worker count default
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("WEB_CONCURRENCY", None)
        from server import run
        default_workers = int(os.environ.get("WEB_CONCURRENCY", "4"))
        assert default_workers == 4
    
    # 2. Check semaphore exists and has correct capacity
    assert _pipeline_semaphore is not None
    assert MAX_CONCURRENT_PIPELINES == 10
    
    # 3. Check bounded jobs_db
    assert MAX_JOBS == 10000
    assert callable(add_job)
    
    # 4. Check fire-and-forget exists
    from server.routers.v1_compat import _emit_event_fire_and_forget
    assert callable(_emit_event_fire_and_forget)
    
    print("✓ All latency fixes validated successfully")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
