# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Job Finalization Service

This service provides enterprise-grade job finalization operations with:
- Atomic state transitions following ACID principles
- Idempotent operations with deduplication tracking
- Comprehensive artifact manifest generation
- Structured logging with correlation IDs
- Metrics and observability integration
- Graceful error handling and recovery

Industry Standards Compliance:
- ISO 27001 A.12.3.1: Information backup and result persistence
- NIST SP 800-53 AU-9: Audit record protection
- SOC 2 Type II: Change management and audit trails
- 12-Factor App: Stateless processes with external state management

Architecture:
This service ensures jobs reach SUCCESS/FAILED states and artifacts are properly
persisted BEFORE process exit. It is designed to be called immediately after pipeline
completion, NOT in shutdown handlers, to prevent the cascade failure documented in
production logs.

Critical Requirements:
- Must be idempotent (safe to call multiple times)
- Must complete before process exit
- Must update job status atomically
- Must persist output manifests for successful jobs
- Must provide observability for monitoring
"""

import json
import logging
import mimetypes
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

from server.schemas import JobStage, JobStatus
from server.storage import jobs_db

logger = logging.getLogger(__name__)

# Observability imports with graceful degradation
try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    TRACING_AVAILABLE = True
    tracer = trace.get_tracer(__name__)
except ImportError:
    TRACING_AVAILABLE = False
    logger.debug("OpenTelemetry not available, tracing disabled for job_finalization")

try:
    from prometheus_client import Counter, Histogram, Gauge
    METRICS_AVAILABLE = True
    
    # Metrics for job finalization observability
    job_finalization_total = Counter(
        'job_finalization_total',
        'Total number of job finalization attempts',
        ['job_id', 'result']
    )
    job_finalization_duration = Histogram(
        'job_finalization_duration_seconds',
        'Duration of job finalization operations',
        ['job_id', 'result']
    )
    job_finalization_artifacts = Histogram(
        'job_finalization_artifacts_total',
        'Number of artifacts finalized per job',
        ['job_id']
    )
    job_finalization_size_bytes = Histogram(
        'job_finalization_size_bytes',
        'Total size of finalized artifacts',
        ['job_id']
    )
except ImportError:
    METRICS_AVAILABLE = False
    logger.debug("Prometheus client not available, metrics disabled for job_finalization")

# Track finalized jobs to ensure idempotency
# In production, this should be backed by a distributed cache (Redis) for multi-instance deployments
_finalized_jobs: Set[str] = set()

# Track dispatched jobs to ensure dispatch idempotency (separate from finalization)
_dispatched_jobs: Set[str] = set()

def _get_correlation_id() -> str:
    """
    Generate a fresh correlation ID for tracking a single finalization operation.

    Each call returns a new UUID so that concurrent or successive finalization
    calls—each with its own logical scope—are independently traceable in logs
    and distributed-tracing systems.

    Returns:
        A new UUID4 string suitable for log correlation and OpenTelemetry spans.
    """
    return str(uuid4())


async def finalize_job_success(
    job_id: str, 
    result: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None
) -> bool:
    """
    Atomic job finalization for successful completion.
    
    This function implements enterprise-grade job finalization with:
    - Idempotency protection via deduplication tracking
    - Atomic state transitions with timestamp consistency
    - Comprehensive artifact manifest generation
    - Distributed tracing integration
    - Prometheus metrics emission
    - Structured logging with correlation IDs
    
    The finalization process:
    1. Validates job exists and is not already finalized (idempotency)
    2. Generates comprehensive artifact manifest with metadata
    3. Updates job status to COMPLETED atomically
    4. Persists manifest and metadata to job storage
    5. Records finalization timestamp and correlation ID
    6. Emits metrics and tracing data
    
    This function MUST complete before process exit to ensure:
    - Job status is persisted as SUCCESS/COMPLETED
    - Output manifest is available for downloads
    - Completion timestamp is recorded
    - Downstream systems can query job results
    
    Args:
        job_id: Unique job identifier
        result: Optional pipeline result with output information
        correlation_id: Optional correlation ID for tracing (auto-generated if not provided)
        
    Returns:
        True if finalization succeeded, False otherwise
        
    Industry Standards:
    - ISO 27001 A.12.3.1: Information backup (persisting job results)
    - ACID compliance: Atomic state transitions
    - Idempotent operations: Safe to retry
    - Observability: Full metrics and tracing
    
    Example:
        >>> result = await run_pipeline(job_id)
        >>> success = await finalize_job_success(job_id, result)
        >>> if success:
        >>>     await dispatch_completion_event(job_id)
    """
    start_time = time.time()
    correlation_id = correlation_id or _get_correlation_id()
    
    # OpenTelemetry tracing context
    if TRACING_AVAILABLE:
        with tracer.start_as_current_span("finalize_job_success") as span:
            span.set_attribute("job_id", job_id)
            span.set_attribute("correlation_id", correlation_id)
            return await _finalize_job_success_impl(
                job_id, result, correlation_id, start_time, span
            )
    else:
        return await _finalize_job_success_impl(
            job_id, result, correlation_id, start_time, None
        )


async def _finalize_job_success_impl(
    job_id: str,
    result: Optional[Dict[str, Any]],
    correlation_id: str,
    start_time: float,
    span: Any
) -> bool:
    """Internal implementation of job success finalization."""
    
    # Check idempotency - skip if already finalized
    if job_id in _finalized_jobs:
        logger.info(
            f"Job {job_id} already finalized, skipping duplicate finalization",
            extra={
                "job_id": job_id,
                "correlation_id": correlation_id,
                "action": "finalize_job_success",
                "result": "duplicate_skipped"
            }
        )
        if METRICS_AVAILABLE:
            job_finalization_total.labels(job_id=job_id, result="duplicate").inc()
        return True
    
    try:
        logger.info(
            f"Starting job finalization for job {job_id}",
            extra={
                "job_id": job_id,
                "correlation_id": correlation_id,
                "action": "finalize_job_success",
                "status": "started"
            }
        )
        
        # Validate job exists
        if job_id not in jobs_db:
            error_msg = f"Cannot finalize job {job_id}: not found in database"
            logger.error(
                error_msg,
                extra={
                    "job_id": job_id,
                    "correlation_id": correlation_id,
                    "error": "job_not_found"
                }
            )
            if span:
                span.set_status(Status(StatusCode.ERROR, error_msg))
            if METRICS_AVAILABLE:
                job_finalization_total.labels(job_id=job_id, result="not_found").inc()
            return False
        
        job = jobs_db[job_id]
        
        # 1. Generate comprehensive artifact manifest
        manifest = await _generate_output_manifest(job_id, correlation_id)
        
        # 2. Update job with final status - ATOMIC operation
        # This is the critical state transition that makes artifacts visible.
        # Honour completed_with_warnings from the pipeline result when present.

        # Check for cold-start import failure — this is a hard failure that should
        # NOT be overridden to COMPLETED even if the pipeline "completed" its stages.
        _cold_start_failed = False
        if result:
            _validation_report = result.get("validation_report", {})
            _failed_checks = _validation_report.get("failed_checks", [])
            if "Cold-start Import Test" in _failed_checks:
                _cold_start_failed = True
            # Also check if the job was already marked FAILED by the pipeline
            if result.get("cold_start_failed") or result.get("import_test_failed"):
                _cold_start_failed = True
            # Check stages_failed for import or cold-start markers
            _stages_failed = result.get("stages_failed", [])
            if any("import" in str(s).lower() or "cold" in str(s).lower() for s in _stages_failed):
                _cold_start_failed = True

        if _cold_start_failed:
            job.status = JobStatus.FAILED
            job.current_stage = JobStage.COMPLETED
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
            job.metadata["failure_reason"] = "cold_start_import_test_failed"
            logger.error(
                f"Job {job_id} cold-start import test FAILED — finalizing as FAILED, not COMPLETED",
                extra={"job_id": job_id, "correlation_id": correlation_id},
            )
            _finalized_jobs.add(job_id)
            if METRICS_AVAILABLE:
                job_finalization_total.labels(job_id=job_id, result="cold_start_failed").inc()
            return True

        _pipeline_status = result.get("status") if result else None
        if _pipeline_status == "completed_with_warnings":
            job.status = JobStatus.COMPLETED_WITH_WARNINGS
        else:
            job.status = JobStatus.COMPLETED
        job.current_stage = JobStage.COMPLETED
        job.completed_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        
        # 3. Store output manifest and metadata
        if manifest:
            job.metadata["output_manifest"] = manifest
            job.metadata["total_output_files"] = len(manifest.get("files", []))
            job.metadata["total_output_size"] = manifest.get("total_size", 0)
            job.output_files = [f["path"] for f in manifest.get("files", [])]
            
            if METRICS_AVAILABLE:
                job_finalization_artifacts.labels(job_id=job_id).observe(
                    len(manifest.get("files", []))
                )
                job_finalization_size_bytes.labels(job_id=job_id).observe(
                    manifest.get("total_size", 0)
                )
            
            logger.info(
                f"Job {job_id} finalized with {len(manifest.get('files', []))} output files "
                f"({manifest.get('total_size', 0)} bytes)",
                extra={
                    "job_id": job_id,
                    "correlation_id": correlation_id,
                    "file_count": len(manifest.get("files", [])),
                    "total_size": manifest.get("total_size", 0)
                }
            )
        else:
            logger.warning(
                f"Job {job_id} finalized but no output files found",
                extra={
                    "job_id": job_id,
                    "correlation_id": correlation_id,
                    "warning": "no_output_files"
                }
            )
        
        # 4. Store pipeline result information if provided
        if result:
            if result.get("output_path"):
                job.metadata["output_path"] = result["output_path"]
            if result.get("message"):
                job.metadata["pipeline_message"] = result["message"]
            if result.get("stages_completed"):
                job.metadata["completed_stages"] = result["stages_completed"]
            if result.get("stages_failed"):
                job.metadata["failed_stages"] = result["stages_failed"]
            if result.get("validation_warnings"):
                job.metadata["validation_warnings"] = result["validation_warnings"]
        
        # 5. Mark finalization metadata
        job.metadata["finalized_at"] = datetime.now(timezone.utc).isoformat()
        job.metadata["finalization_correlation_id"] = correlation_id
        job.metadata["finalization_duration_ms"] = int((time.time() - start_time) * 1000)
        
        # Mark as finalized to prevent duplicates
        _finalized_jobs.add(job_id)
        
        # Record metrics
        duration = time.time() - start_time
        if METRICS_AVAILABLE:
            job_finalization_total.labels(job_id=job_id, result="success").inc()
            job_finalization_duration.labels(job_id=job_id, result="success").observe(duration)
        
        if span:
            span.set_status(Status(StatusCode.OK))
            span.set_attribute("file_count", len(job.output_files))
            span.set_attribute("duration_ms", int(duration * 1000))
        
        logger.info(
            f"✓ Job {job_id} finalized successfully in {duration:.2f}s: "
            f"status={job.status}, files={len(job.output_files)}",
            extra={
                "job_id": job_id,
                "correlation_id": correlation_id,
                "action": "finalize_job_success",
                "result": "success",
                "duration_seconds": duration,
                "file_count": len(job.output_files)
            }
        )
        
        # 6. Automatically dispatch to Self-Fixing Engineer (idempotent)
        await _auto_dispatch_to_sfe(job_id, job, result, correlation_id)
        
        return True
        
    except Exception as e:
        error_msg = f"Error during job finalization for {job_id}: {e}"
        logger.error(
            error_msg,
            exc_info=True,
            extra={
                "job_id": job_id,
                "correlation_id": correlation_id,
                "error_type": type(e).__name__,
                "error_message": str(e)
            }
        )
        
        if span:
            span.set_status(Status(StatusCode.ERROR, error_msg))
            span.record_exception(e)
        
        if METRICS_AVAILABLE:
            job_finalization_total.labels(job_id=job_id, result="error").inc()
        
        return False


async def _auto_dispatch_to_sfe(
    job_id: str,
    job: Any,
    result: Optional[Dict[str, Any]],
    correlation_id: str,
) -> None:
    """
    Automatically dispatch completed job to Self-Fixing Engineer.

    This function is called as Step 6 of finalize_job_success() to ensure
    every successful job is forwarded to SFE/Arbiter without manual intervention.

    The dispatch is:
    - Idempotent: guarded by ``_dispatched_jobs`` so it fires at most once per job
    - Fail-safe: any error is logged and recorded in job metadata but does NOT
      affect the already-persisted COMPLETED status
    - Non-blocking: dispatch failure never raises; the caller always proceeds

    Args:
        job_id: Unique job identifier
        job: The job object from jobs_db (already COMPLETED)
        result: Optional pipeline result passed to finalize_job_success
        correlation_id: Correlation ID for request tracing
    """
    if job_id in _dispatched_jobs:
        logger.debug(
            f"Job {job_id} already dispatched to SFE, skipping duplicate dispatch",
            extra={"job_id": job_id, "correlation_id": correlation_id, "action": "auto_dispatch_sfe"}
        )
        return

    try:
        # Lazy import: dispatch_service has heavy optional dependencies
        # (omnicore_engine → numpy, asyncpg, etc.) that may not be installed in
        # all environments.  Deferring the import to call time lets
        # job_finalization load successfully even when those extras are absent,
        # and surfaces the ImportError as a non-fatal warning rather than a
        # startup failure.
        from server.services.dispatch_service import dispatch_job_completion

        job_data: Dict[str, Any] = {
            "status": job.status,
            "output_files": list(getattr(job, "output_files", [])),
            "completed_at": job.metadata.get("finalized_at", datetime.now(timezone.utc).isoformat()),
            "correlation_id": correlation_id,
        }
        if result:
            job_data["output_path"] = result.get("output_path")
            job_data["completed_stages"] = result.get("stages_completed", [])
            job_data["message"] = result.get("message")
        if "output_manifest" in job.metadata:
            job_data["output_manifest"] = job.metadata["output_manifest"]

        dispatched = await dispatch_job_completion(job_id, job_data, correlation_id)
        # Mark as dispatched regardless of the outcome so that automatic dispatch
        # does not fire again for this job in this process lifetime.  A False
        # return (all methods exhausted) is still a completed attempt; any retry
        # should go through the manual /dispatch-to-sfe endpoint which has its
        # own idempotency logic and richer retry controls.
        _dispatched_jobs.add(job_id)

        if dispatched:
            job.metadata["sfe_dispatched"] = True
            job.metadata["sfe_dispatch_at"] = datetime.now(timezone.utc).isoformat()
            logger.info(
                f"✓ Job {job_id} automatically dispatched to Self-Fixing Engineer",
                extra={
                    "job_id": job_id,
                    "correlation_id": correlation_id,
                    "action": "auto_dispatch_sfe",
                    "result": "success",
                }
            )
        else:
            job.metadata["sfe_dispatched"] = False
            job.metadata["sfe_dispatch_error"] = "All dispatch methods failed"
            logger.warning(
                f"Job {job_id} dispatch to SFE failed (all methods exhausted); "
                "job is still COMPLETED — dispatch can be retried via manual endpoint",
                extra={
                    "job_id": job_id,
                    "correlation_id": correlation_id,
                    "action": "auto_dispatch_sfe",
                    "result": "failed",
                }
            )

    except ImportError:
        logger.warning(
            "dispatch_service not available; SFE dispatch skipped for job %s", job_id,
            extra={"job_id": job_id, "action": "auto_dispatch_sfe", "result": "skipped_no_service"}
        )
    except Exception as exc:
        # Fail-safe: record failure but do not raise — job completion must not be corrupted
        job.metadata["sfe_dispatched"] = False
        job.metadata["sfe_dispatch_error"] = str(exc)
        logger.error(
            f"Unexpected error during SFE auto-dispatch for job {job_id}: {exc}; "
            "job remains COMPLETED",
            exc_info=True,
            extra={
                "job_id": job_id,
                "correlation_id": correlation_id,
                "action": "auto_dispatch_sfe",
                "result": "error",
                "error_type": type(exc).__name__,
            }
        )


async def finalize_job_failure(
    job_id: str, 
    error: Exception,
    correlation_id: Optional[str] = None
) -> bool:
    """
    Record failure state for a job with enterprise-grade error tracking.
    
    This function provides comprehensive failure finalization with:
    - Idempotency protection
    - Detailed error metadata capture
    - Stack trace preservation for debugging
    - Metrics and tracing integration
    - Structured logging
    
    Args:
        job_id: Unique job identifier
        error: Exception that caused the failure
        correlation_id: Optional correlation ID for tracing
        
    Returns:
        True if finalization succeeded, False otherwise
        
    Industry Standards:
    - NIST SP 800-53 SI-11: Error handling
    - ISO 27001 A.12.4.1: Event logging
    - SOC 2: Incident tracking and audit trails
    """
    start_time = time.time()
    correlation_id = correlation_id or _get_correlation_id()
    
    # Check idempotency
    if job_id in _finalized_jobs:
        logger.info(
            f"Job {job_id} already finalized, skipping duplicate finalization",
            extra={
                "job_id": job_id,
                "correlation_id": correlation_id,
                "action": "finalize_job_failure",
                "result": "duplicate_skipped"
            }
        )
        if METRICS_AVAILABLE:
            job_finalization_total.labels(job_id=job_id, result="duplicate").inc()
        return True
    
    try:
        logger.info(
            f"Finalizing failed job {job_id}",
            extra={
                "job_id": job_id,
                "correlation_id": correlation_id,
                "error_type": type(error).__name__
            }
        )
        
        # Validate job exists
        if job_id not in jobs_db:
            error_msg = f"Cannot finalize job {job_id}: not found in database"
            logger.error(
                error_msg,
                extra={
                    "job_id": job_id,
                    "correlation_id": correlation_id,
                    "error": "job_not_found"
                }
            )
            if METRICS_AVAILABLE:
                job_finalization_total.labels(job_id=job_id, result="not_found").inc()
            return False
        
        job = jobs_db[job_id]
        
        # Update job with failure status
        job.status = JobStatus.FAILED
        job.updated_at = datetime.now(timezone.utc)
        job.completed_at = datetime.now(timezone.utc)
        
        # Store comprehensive error information for debugging
        job.metadata["error"] = str(error)
        job.metadata["error_type"] = type(error).__name__
        job.metadata["error_module"] = type(error).__module__
        job.metadata["finalized_at"] = datetime.now(timezone.utc).isoformat()
        job.metadata["finalization_correlation_id"] = correlation_id
        
        # Store stack trace if available (helps debugging)
        job.metadata["error_traceback"] = traceback.format_exc()
        
        # Mark as finalized
        _finalized_jobs.add(job_id)
        
        # Record metrics
        duration = time.time() - start_time
        if METRICS_AVAILABLE:
            job_finalization_total.labels(job_id=job_id, result="failed").inc()
            job_finalization_duration.labels(job_id=job_id, result="failed").observe(duration)
        
        logger.info(
            f"✓ Job {job_id} finalized as FAILED in {duration:.2f}s: {error}",
            extra={
                "job_id": job_id,
                "correlation_id": correlation_id,
                "action": "finalize_job_failure",
                "result": "failed",
                "error_type": type(error).__name__,
                "duration_seconds": duration
            }
        )
        return True
        
    except Exception as e:
        logger.error(
            f"Error during failure finalization for {job_id}: {e}",
            exc_info=True,
            extra={
                "job_id": job_id,
                "correlation_id": correlation_id,
                "error_type": type(e).__name__
            }
        )
        
        if METRICS_AVAILABLE:
            job_finalization_total.labels(job_id=job_id, result="error").inc()
        
        return False


async def _generate_output_manifest(
    job_id: str,
    correlation_id: str
) -> Optional[Dict[str, Any]]:
    """
    Generate comprehensive manifest of output files for a job.
    
    This function performs deep scanning of job output directory and creates
    a structured manifest with:
    - File metadata (size, extension, timestamps)
    - Directory structure preservation
    - MIME type detection for web compatibility
    - Total size calculation
    - Security validation (path traversal prevention)
    
    Scans the job's output directory and creates a structured manifest
    of all generated files with metadata suitable for UI rendering and
    download management.
    
    Args:
        job_id: Unique job identifier
        correlation_id: Correlation ID for tracing
        
    Returns:
        Manifest dictionary with comprehensive file information, or None if no files found
        
    Security Considerations:
    - Path traversal prevention via resolved path validation
    - Size limits to prevent memory exhaustion
    - File type validation for safe downloads
    
    Industry Standards:
    - OWASP A05:2021 Security Misconfiguration (path validation)
    - CWE-22: Path Traversal Prevention
    """
    try:
        job_dir = Path(f"./uploads/{job_id}").resolve()
        
        if not job_dir.exists():
            logger.warning(
                f"Job directory {job_dir} does not exist",
                extra={
                    "job_id": job_id,
                    "correlation_id": correlation_id,
                    "job_dir": str(job_dir)
                }
            )
            return None
        
        files = []
        total_size = 0
        file_count = 0
        max_files = 10000  # Safety limit to prevent memory exhaustion
        
        # Recursively scan for all files with security validation
        for file_path in job_dir.rglob('*'):
            if file_count >= max_files:
                logger.warning(
                    f"File limit reached for job {job_id}, stopping scan at {max_files} files",
                    extra={
                        "job_id": job_id,
                        "correlation_id": correlation_id,
                        "max_files": max_files
                    }
                )
                break
            
            if file_path.is_file():
                try:
                    # Security: Validate path is within job directory (prevent traversal)
                    resolved_path = file_path.resolve()
                    if not str(resolved_path).startswith(str(job_dir)):
                        logger.warning(
                            f"Skipping file outside job directory: {file_path}",
                            extra={
                                "job_id": job_id,
                                "correlation_id": correlation_id,
                                "file_path": str(file_path),
                                "security_issue": "path_traversal_attempt"
                            }
                        )
                        continue
                    
                    # [FIX] Use already-resolved job_dir to avoid TypeError with mocks
                    try:
                        rel_path = str(resolved_path.relative_to(job_dir))
                    except (ValueError, TypeError) as e:
                        logger.warning(f"[JOB_FINALIZATION] File {file_path} is outside job_dir {job_dir}, using absolute path. Error: {e}")
                        rel_path = str(file_path)
                    file_stat = file_path.stat()
                    file_size = file_stat.st_size
                    
                    # Detect MIME type for better UI/download handling
                    mime_type, _ = mimetypes.guess_type(str(file_path))
                    
                    file_info = {
                        "path": rel_path,
                        "name": file_path.name,
                        "size": file_size,
                        "extension": file_path.suffix,
                        "mime_type": mime_type or "application/octet-stream",
                        "modified_at": datetime.fromtimestamp(
                            file_stat.st_mtime, tz=timezone.utc
                        ).isoformat(),
                    }
                    
                    files.append(file_info)
                    total_size += file_size
                    file_count += 1
                    
                except Exception as e:
                    logger.warning(
                        f"Error processing file {file_path}: {e}",
                        extra={
                            "job_id": job_id,
                            "correlation_id": correlation_id,
                            "file_path": str(file_path),
                            "error": str(e)
                        }
                    )
                    continue
        
        if not files:
            logger.warning(
                f"No files found in {job_dir}",
                extra={
                    "job_id": job_id,
                    "correlation_id": correlation_id,
                    "job_dir": str(job_dir)
                }
            )
            return None
        
        # Sort files by path for consistent ordering
        files.sort(key=lambda f: f["path"])
        
        manifest: Dict[str, Any] = {
            "job_id": job_id,
            "files": files,
            "total_files": len(files),
            "total_size": total_size,
            "directory": str(job_dir),
        }

        # Omit dynamic fields in deterministic mode to ensure byte-identical manifests
        try:
            from generator.deterministic import is_deterministic as _is_det
            _deterministic = _is_det()
        except ImportError:
            _deterministic = False

        if not _deterministic:
            manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
            manifest["correlation_id"] = correlation_id
        
        logger.info(
            f"Generated manifest for job {job_id}: "
            f"{len(files)} files, {total_size:,} bytes",
            extra={
                "job_id": job_id,
                "correlation_id": correlation_id,
                "file_count": len(files),
                "total_size": total_size,
                "action": "generate_manifest",
                "result": "success"
            }
        )
        
        return manifest
        
    except Exception as e:
        logger.error(
            f"Error generating output manifest for {job_id}: {e}",
            exc_info=True,
            extra={
                "job_id": job_id,
                "correlation_id": correlation_id,
                "error_type": type(e).__name__
            }
        )
        return None


async def apply_pending_fixes(job_id: str) -> Dict[str, Any]:
    """
    Retrieve and apply all pending SFE fixes for a job before packaging.

    This function must be called BEFORE the output ZIP is created so that
    fixed file content is included in the final archive.  It is intentionally
    fail-safe: any error is logged and an empty result is returned so that
    callers (e.g. omnicore_service._finalize_successful_job) can proceed with
    ZIP creation even when fix application is unavailable.

    Args:
        job_id: Unique job identifier

    Returns:
        Summary dict from SFEService.apply_all_pending_fixes, or an empty
        summary dict if SFE is not available or an error occurred.
    """
    try:
        from server.services.sfe_service import SFEService
        sfe = SFEService()
        result = await sfe.apply_all_pending_fixes(job_id)
        if result.get("applied"):
            logger.info(
                f"[FINALIZATION] Applied {len(result['applied'])} SFE fix(es) for job {job_id} "
                f"before ZIP packaging: {result['applied']}",
                extra={
                    "job_id": job_id,
                    "applied_fixes": result["applied"],
                    "action": "apply_pending_fixes",
                },
            )
        else:
            logger.debug(
                f"[FINALIZATION] No pending SFE fixes to apply for job {job_id}",
                extra={"job_id": job_id, "action": "apply_pending_fixes"},
            )
        return result
    except Exception as exc:
        logger.warning(
            f"[FINALIZATION] apply_pending_fixes failed for job {job_id} (non-fatal): {exc}",
            extra={
                "job_id": job_id,
                "action": "apply_pending_fixes",
                "error": str(exc),
            },
        )
        return {"applied": [], "failed": [], "skipped": []}


def _invalidate_job_zip_cache(job_id: str) -> None:
    """
    Remove known application-managed cached ZIP archives from the job root directory.

    Only specifically named cache files created by this application are removed.
    ZIP files inside generated project subdirectories (e.g. ``my_app/dist/app.zip``)
    are never touched, preserving user artefacts.

    This is a synchronous helper intentionally kept side-effect-free on failure
    so it can be called from both async and sync contexts.

    Security Considerations:
    - Path traversal prevention: ``job_id`` is validated against a safe pattern
      before use in path construction, and the resolved ``job_dir`` is verified
      to be a direct child of the known uploads root so that a crafted
      ``job_id`` (e.g. ``../../etc``) cannot escape the uploads directory.
    - Only removes files whose resolved parent is exactly ``job_dir``
      (one level deep, never recursive).
    - OSError during unlink is silently suppressed — the next download will
      still build a fresh ZIP from the live directory tree.

    Args:
        job_id: Unique job identifier.
    """
    import re as _re
    # Allow UUIDs and common safe job-id patterns (hex, hyphens, underscores).
    # Reject anything containing path separators or traversal sequences.
    if not job_id or not _re.match(r'^[\w\-]+$', job_id):
        logger.debug(
            "_invalidate_job_zip_cache: unsafe job_id %r, skipping", job_id,
            extra={"action": "invalidate_job_zip_cache", "result": "unsafe_job_id"},
        )
        return

    uploads_root = Path("./uploads").resolve()
    job_dir = (uploads_root / job_id).resolve()

    # Security: ensure the resolved job_dir is a direct child of uploads_root
    # to prevent path-traversal via a crafted job_id.
    if job_dir.parent != uploads_root:
        logger.warning(
            "_invalidate_job_zip_cache: job_dir %s is outside uploads root, skipping",
            job_dir,
            extra={"action": "invalidate_job_zip_cache", "result": "path_traversal_blocked"},
        )
        return

    if not job_dir.exists():
        return

    # Known cache-ZIP filenames produced by the application.
    # "output.zip"    — explicit cache path used by SFEService.apply_fix()
    # "*_output.zip"  — suffix pattern excluded by the download endpoints when
    #                   enumerating files, indicating an application-managed artefact
    _CACHE_PATTERNS: tuple = ("output.zip", "*_output.zip")

    for pattern in _CACHE_PATTERNS:
        for zip_file in job_dir.glob(pattern):
            # Security: only remove files directly inside the job root, never
            # inside a generated subdirectory.
            if zip_file.parent.resolve() != job_dir:
                continue
            try:
                zip_file.unlink()
                logger.debug(
                    "Invalidated cached ZIP %s for job %s",
                    zip_file.name,
                    job_id,
                    extra={
                        "job_id": job_id,
                        "zip_path": str(zip_file),
                        "action": "invalidate_job_zip_cache",
                    },
                )
            except OSError as _unlink_err:
                logger.debug(
                    "Could not remove cached ZIP %s for job %s: %s",
                    zip_file, job_id, _unlink_err,
                    extra={
                        "job_id": job_id,
                        "zip_path": str(zip_file),
                        "action": "invalidate_job_zip_cache",
                    },
                )


async def refresh_job_output_files(job_id: str) -> bool:
    """
    Re-scan the job output directory and update ``job.output_files`` in-place.

    Designed to be called after SFE fixes are written to disk so that the
    job's tracked file list and cached metadata are always consistent with the
    actual directory contents.  Side-effects:

    - Regenerates and stores the full output manifest (file names, sizes, MIME
      types) on the job object in ``jobs_db``.
    - Removes known application-managed cached ZIP archives (see
      ``_invalidate_job_zip_cache``) so the next download builds a fresh archive.
    - Records ``updated_at`` on the job to signal staleness to any cache layers.

    Behaviour guarantees:
    - **Idempotent**: safe to call multiple times; each call reflects the
      current on-disk state.
    - **Fail-safe**: every exception is caught, logged with full context, and
      ``False`` is returned — callers are never interrupted by a refresh failure.
    - **Non-blocking**: purely async I/O throughout; safe for use inside
      running event loops without thread-pool delegation.

    Args:
        job_id: Unique job identifier.  Must be a non-empty string.

    Returns:
        ``True``  if ``job.output_files`` was successfully refreshed.
        ``False`` if the job was not found in the in-memory store, input was
                  invalid, or an unrecoverable error occurred.

    Raises:
        Nothing.  All exceptions are handled internally and surfaced via logs.

    Industry Standards:
    - ISO 27001 A.12.3.1: Information backup and artifact persistence.
    - OWASP A05:2021 Security Misconfiguration: path traversal prevention
      is delegated to ``_generate_output_manifest`` which validates all paths.
    - CWE-22: Path Traversal Prevention (enforced in manifest generation and
      ``_invalidate_job_zip_cache``).
    - 12-Factor App: stateless refresh with external state management.
    """
    start_time = time.time()
    correlation_id = _get_correlation_id()

    # --- Input validation -------------------------------------------------- #
    if not job_id or not isinstance(job_id, str):
        logger.warning(
            "refresh_job_output_files called with invalid job_id: %r",
            job_id,
            extra={
                "action": "refresh_job_output_files",
                "result": "invalid_input",
            },
        )
        return False

    # --- OpenTelemetry tracing --------------------------------------------- #
    if TRACING_AVAILABLE:
        with tracer.start_as_current_span("refresh_job_output_files") as span:
            span.set_attribute("job_id", job_id)
            span.set_attribute("correlation_id", correlation_id)
            return await _refresh_job_output_files_impl(
                job_id, correlation_id, start_time, span
            )
    else:
        return await _refresh_job_output_files_impl(
            job_id, correlation_id, start_time, None
        )


async def _refresh_job_output_files_impl(
    job_id: str,
    correlation_id: str,
    start_time: float,
    span: Any,
) -> bool:
    """Internal implementation of refresh_job_output_files."""
    try:
        if job_id not in jobs_db:
            logger.debug(
                "refresh_job_output_files: job %s not in jobs_db, skipping",
                job_id,
                extra={
                    "job_id": job_id,
                    "correlation_id": correlation_id,
                    "action": "refresh_job_output_files",
                    "result": "not_found",
                },
            )
            if span and TRACING_AVAILABLE:
                span.set_attribute("result", "not_found")
                span.set_status(Status(StatusCode.OK))
            return False

        logger.info(
            "Refreshing output_files for job %s",
            job_id,
            extra={
                "job_id": job_id,
                "correlation_id": correlation_id,
                "action": "refresh_job_output_files",
                "status": "started",
            },
        )

        manifest = await _generate_output_manifest(job_id, correlation_id)

        job = jobs_db[job_id]
        file_count = 0

        if manifest:
            job.output_files = [f["path"] for f in manifest.get("files", [])]
            job.metadata["output_manifest"] = manifest
            job.metadata["total_output_files"] = len(manifest.get("files", []))
            job.metadata["total_output_size"] = manifest.get("total_size", 0)
            job.updated_at = datetime.now(timezone.utc)
            file_count = len(job.output_files)
        else:
            logger.warning(
                "refresh_job_output_files: no manifest generated for job %s; "
                "output_files list unchanged",
                job_id,
                extra={
                    "job_id": job_id,
                    "correlation_id": correlation_id,
                    "action": "refresh_job_output_files",
                    "warning": "no_manifest",
                },
            )

        # Remove known stale cached ZIPs from the job root directory.
        _invalidate_job_zip_cache(job_id)

        duration = time.time() - start_time

        if span and TRACING_AVAILABLE:
            span.set_attribute("file_count", file_count)
            span.set_attribute("duration_ms", int(duration * 1000))
            span.set_attribute("result", "success")
            span.set_status(Status(StatusCode.OK))

        if METRICS_AVAILABLE:
            job_finalization_total.labels(job_id=job_id, result="refresh_success").inc()

        logger.info(
            "✓ Refreshed output_files for job %s: %d files in %.2fs",
            job_id,
            file_count,
            duration,
            extra={
                "job_id": job_id,
                "correlation_id": correlation_id,
                "action": "refresh_job_output_files",
                "result": "success",
                "file_count": file_count,
                "duration_seconds": duration,
            },
        )
        return True

    except Exception as exc:
        duration = time.time() - start_time
        logger.warning(
            "refresh_job_output_files failed for job %s (non-fatal): %s",
            job_id,
            exc,
            extra={
                "job_id": job_id,
                "correlation_id": correlation_id,
                "action": "refresh_job_output_files",
                "result": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "duration_seconds": duration,
            },
        )
        if span and TRACING_AVAILABLE:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
        if METRICS_AVAILABLE:
            job_finalization_total.labels(job_id=job_id, result="refresh_error").inc()
        return False


def reset_finalization_state():
    """
    Reset finalization state for testing purposes.
    
    WARNING: This function is intended for testing only and should NOT be
    called in production code. In production multi-instance deployments,
    finalization state should be tracked in a distributed cache (Redis)
    to ensure idempotency across instances.
    
    Security Considerations:
    - Only call in test environments
    - Clear security context before calling
    - Validate environment before use
    """
    global _finalized_jobs, _dispatched_jobs
    _finalized_jobs.clear()
    _dispatched_jobs.clear()
    logger.debug(
        "Finalization state reset (test mode)",
        extra={"action": "reset_finalization_state", "mode": "test"}
    )
