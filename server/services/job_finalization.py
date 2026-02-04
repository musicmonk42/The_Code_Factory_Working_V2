"""
Job Finalization Service

Handles atomic job finalization operations to ensure jobs reach SUCCESS/FAILED states
and artifacts are properly persisted. This service is designed to be called immediately
after pipeline completion, NOT in shutdown handlers.

Critical Requirements:
- Must be idempotent (safe to call multiple times)
- Must complete before process exit
- Must update job status atomically
- Must persist output manifests for successful jobs
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from server.schemas import JobStage, JobStatus
from server.storage import jobs_db

logger = logging.getLogger(__name__)

# Track finalized jobs to ensure idempotency
_finalized_jobs = set()


async def finalize_job_success(job_id: str, result: Optional[Dict[str, Any]] = None) -> bool:
    """
    Atomic job finalization for successful completion.
    
    This function MUST complete before process exit to ensure:
    1. Job status is persisted as SUCCESS
    2. Output manifest is available for downloads
    3. Completion timestamp is recorded
    
    Args:
        job_id: Unique job identifier
        result: Optional pipeline result with output information
        
    Returns:
        True if finalization succeeded, False otherwise
        
    Industry Standards:
    - ISO 27001 A.12.3.1: Information backup (persisting job results)
    - Idempotent operations (safe to retry)
    - Atomic state transitions
    """
    # Check idempotency - skip if already finalized
    if job_id in _finalized_jobs:
        logger.info(f"Job {job_id} already finalized, skipping duplicate finalization")
        return True
    
    try:
        logger.info(f"Starting job finalization for job {job_id}")
        
        # Validate job exists
        if job_id not in jobs_db:
            logger.error(f"Cannot finalize job {job_id}: not found in database")
            return False
        
        job = jobs_db[job_id]
        
        # 1. Generate artifact manifest by scanning output directory
        manifest = await _generate_output_manifest(job_id)
        
        # 2. Update job with final status - this is the critical atomic operation
        job.status = JobStatus.COMPLETED
        job.current_stage = JobStage.COMPLETED
        job.completed_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        
        # 3. Store output manifest in job metadata
        if manifest:
            job.metadata["output_manifest"] = manifest
            job.metadata["total_output_files"] = len(manifest.get("files", []))
            job.output_files = [f["path"] for f in manifest.get("files", [])]
            logger.info(
                f"Job {job_id} finalized with {len(manifest.get('files', []))} output files"
            )
        else:
            logger.warning(f"Job {job_id} finalized but no output files found")
        
        # 4. Store pipeline result information if provided
        if result:
            if result.get("output_path"):
                job.metadata["output_path"] = result["output_path"]
            if result.get("message"):
                job.metadata["pipeline_message"] = result["message"]
            if result.get("stages_completed"):
                job.metadata["completed_stages"] = result["stages_completed"]
        
        # 5. Mark finalization timestamp
        job.metadata["finalized_at"] = datetime.now(timezone.utc).isoformat()
        
        # Mark as finalized to prevent duplicates
        _finalized_jobs.add(job_id)
        
        logger.info(
            f"✓ Job {job_id} finalized successfully: "
            f"status={job.status}, files={len(job.output_files)}"
        )
        return True
        
    except Exception as e:
        logger.error(
            f"Error during job finalization for {job_id}: {e}",
            exc_info=True
        )
        return False


async def finalize_job_failure(job_id: str, error: Exception) -> bool:
    """
    Record failure state for a job.
    
    Args:
        job_id: Unique job identifier
        error: Exception that caused the failure
        
    Returns:
        True if finalization succeeded, False otherwise
    """
    # Check idempotency
    if job_id in _finalized_jobs:
        logger.info(f"Job {job_id} already finalized, skipping duplicate finalization")
        return True
    
    try:
        logger.info(f"Finalizing failed job {job_id}")
        
        # Validate job exists
        if job_id not in jobs_db:
            logger.error(f"Cannot finalize job {job_id}: not found in database")
            return False
        
        job = jobs_db[job_id]
        
        # Update job with failure status
        job.status = JobStatus.FAILED
        job.updated_at = datetime.now(timezone.utc)
        job.completed_at = datetime.now(timezone.utc)
        
        # Store error information
        job.metadata["error"] = str(error)
        job.metadata["error_type"] = type(error).__name__
        job.metadata["finalized_at"] = datetime.now(timezone.utc).isoformat()
        
        # Mark as finalized
        _finalized_jobs.add(job_id)
        
        logger.info(f"✓ Job {job_id} finalized as FAILED: {error}")
        return True
        
    except Exception as e:
        logger.error(
            f"Error during failure finalization for {job_id}: {e}",
            exc_info=True
        )
        return False


async def _generate_output_manifest(job_id: str) -> Optional[Dict[str, Any]]:
    """
    Generate manifest of output files for a job.
    
    Scans the job's output directory and creates a structured manifest
    of all generated files with metadata.
    
    Args:
        job_id: Unique job identifier
        
    Returns:
        Manifest dictionary with file information, or None if no files found
    """
    try:
        job_dir = Path(f"./uploads/{job_id}")
        
        if not job_dir.exists():
            logger.warning(f"Job directory {job_dir} does not exist")
            return None
        
        files = []
        total_size = 0
        
        # Recursively scan for all files
        for file_path in job_dir.rglob('*'):
            if file_path.is_file():
                try:
                    rel_path = str(file_path.relative_to(job_dir))
                    file_size = file_path.stat().st_size
                    
                    files.append({
                        "path": rel_path,
                        "name": file_path.name,
                        "size": file_size,
                        "extension": file_path.suffix,
                    })
                    
                    total_size += file_size
                except Exception as e:
                    logger.warning(f"Error processing file {file_path}: {e}")
                    continue
        
        if not files:
            logger.warning(f"No files found in {job_dir}")
            return None
        
        manifest = {
            "job_id": job_id,
            "files": files,
            "total_files": len(files),
            "total_size": total_size,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        logger.info(
            f"Generated manifest for job {job_id}: "
            f"{len(files)} files, {total_size} bytes"
        )
        
        return manifest
        
    except Exception as e:
        logger.error(f"Error generating output manifest for {job_id}: {e}", exc_info=True)
        return None


def reset_finalization_state():
    """
    Reset finalization state (for testing purposes only).
    
    WARNING: Do not call in production code.
    """
    global _finalized_jobs
    _finalized_jobs.clear()
    logger.debug("Finalization state reset (test mode)")
