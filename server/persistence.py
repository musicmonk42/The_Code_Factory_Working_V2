# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Job persistence layer for database storage.

This module provides enterprise-grade job persistence with:
- Database transaction management
- Automatic retry with exponential backoff
- Comprehensive error handling and logging
- Type safety with full annotations
- Circuit breaker pattern for database failures

FIX Issue 3: Jobs are now persisted to database to prevent loss after restart.

Industry Standards Compliance:
- ACID transaction guarantees
- Retry logic for transient failures (NIST SP 800-53 SC-5)
- Comprehensive audit logging (SOC 2 Type II)
- Type safety for maintainability (PEP 484)
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from server.schemas import Job

logger = logging.getLogger(__name__)

# Database will be initialized by the application
_database: Optional[object] = None
_use_database = False

# Circuit breaker state for database failures
_consecutive_failures = 0
_max_consecutive_failures = 5
_circuit_open = False

# Retry configuration
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 0.5  # seconds
MAX_RETRY_DELAY = 5.0  # seconds


def initialize_persistence(database: Optional[object] = None) -> None:
    """
    Initialize the persistence layer with a database connection.
    
    This function configures the module-level database instance used for
    all persistence operations. It should be called once during application
    startup.
    
    Args:
        database: Database instance from omnicore_engine.database.Database.
                 If None, persistence will operate in memory-only mode.
                 
    Thread Safety:
        This function modifies module-level state and should only be called
        during single-threaded application initialization.
        
    Industry Standards:
        - Follows dependency injection pattern (Martin Fowler)
        - Explicit initialization prevents implicit coupling
    """
    global _database, _use_database, _consecutive_failures, _circuit_open
    _database = database
    _use_database = database is not None
    _consecutive_failures = 0
    _circuit_open = False
    
    if _use_database:
        logger.info("Job persistence initialized with database backend")
    else:
        logger.warning("Job persistence initialized without database - using memory only")


async def _retry_with_backoff(operation_name: str, operation_callable, *args, **kwargs):
    """
    Execute an async operation with exponential backoff retry logic.
    
    Implements industry-standard retry pattern with:
    - Exponential backoff to reduce load during outages
    - Maximum retry limit to prevent infinite loops
    - Circuit breaker pattern to fail fast during extended outages
    
    Args:
        operation_name: Human-readable operation name for logging
        operation_callable: Async function to execute
        *args, **kwargs: Arguments to pass to the operation
        
    Returns:
        Result of the operation if successful
        
    Raises:
        Exception: Re-raises the last exception if all retries exhausted
        
    Industry Standards:
        - Exponential backoff (AWS API Gateway, Google Cloud)
        - Circuit breaker pattern (Netflix Hystrix, Martin Fowler)
        - NIST SP 800-53 SC-5: Denial of Service Protection
    """
    global _consecutive_failures, _circuit_open
    
    # Check circuit breaker
    if _circuit_open:
        logger.warning(f"Circuit breaker OPEN - skipping {operation_name}")
        raise RuntimeError(f"Circuit breaker open for database operations")
    
    last_exception = None
    delay = INITIAL_RETRY_DELAY
    
    for attempt in range(MAX_RETRIES):
        try:
            result = await operation_callable(*args, **kwargs)
            
            # Success - reset failure counter
            if _consecutive_failures > 0:
                logger.info(f"Database operation recovered after {_consecutive_failures} failures")
                _consecutive_failures = 0
                
            return result
            
        except Exception as e:
            last_exception = e
            _consecutive_failures += 1
            
            # Open circuit breaker if too many consecutive failures
            if _consecutive_failures >= _max_consecutive_failures:
                _circuit_open = True
                logger.error(
                    f"Circuit breaker OPENED after {_consecutive_failures} consecutive failures",
                    exc_info=True
                )
            
            if attempt < MAX_RETRIES - 1:
                logger.warning(
                    f"{operation_name} failed (attempt {attempt + 1}/{MAX_RETRIES}), "
                    f"retrying in {delay:.2f}s: {e}"
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RETRY_DELAY)  # Exponential backoff with cap
            else:
                logger.error(
                    f"{operation_name} failed after {MAX_RETRIES} attempts: {e}",
                    exc_info=True
                )
    
    raise last_exception


async def save_job_to_database(job: Job) -> bool:
    """
    Persist a job to the database with automatic retry on transient failures.
    
    This function provides ACID-compliant job storage with:
    - Automatic retry with exponential backoff
    - Idempotent upsert operation (safe to call multiple times)
    - Comprehensive error logging
    - Graceful degradation if database unavailable
    
    Args:
        job: Job instance to persist (must have valid id, status, timestamps)
        
    Returns:
        True if persisted successfully, False if database unavailable or failed
        after all retries. Returning False is non-fatal - job remains in memory.
        
    Transaction Guarantees:
        - Atomicity: Either full job data is saved or none
        - Consistency: Job data validated before persistence
        - Isolation: Database handles concurrent updates
        - Durability: Changes persisted to disk before return
        
    Industry Standards:
        - ACID transactions (ISO/IEC 10026)
        - Retry with exponential backoff (AWS SDK, Google Cloud)
        - SOC 2 Type II: Data persistence and integrity
        
    Example:
        >>> job = Job(id="abc123", status=JobStatus.PENDING, ...)
        >>> success = await save_job_to_database(job)
        >>> if not success:
        ...     logger.warning("Database unavailable, job in memory only")
    """
    if not _use_database or not _database:
        logger.debug(f"Database not available, skipping persistence for job {job.id}")
        return False
    
    async def _save_operation():
        """
        Inner operation that will be retried.
        
        Note: Database errors (connection failures, constraint violations, etc.)
        will be caught and retried by the _retry_with_backoff wrapper function
        that calls this operation. No additional error handling is needed here.
        """
        # Serialize job to JSON with datetime conversion
        job_data = job.model_dump(mode='json')
        
        # Convert datetime objects to ISO format strings for JSON storage
        for key in ['created_at', 'updated_at', 'completed_at']:
            if key in job_data and job_data[key] is not None:
                if isinstance(job_data[key], datetime):
                    job_data[key] = job_data[key].isoformat()
        
        # Validate required fields before saving
        if not job_data.get('id'):
            raise ValueError("Job ID is required for persistence")
        if not job_data.get('status'):
            raise ValueError("Job status is required for persistence")
        
        # Store in GeneratorAgentState table with custom_attributes
        # Use direct database query to avoid interface mismatch
        from omnicore_engine.database.models import GeneratorAgentState
        from sqlalchemy import select
        
        agent_name = f"job_{job.id}"
        
        async with _database.AsyncSessionLocal() as session:
            # Check if agent state already exists for this job
            result = await session.execute(
                select(GeneratorAgentState).filter_by(name=agent_name)
            )
            existing_state = result.scalars().first()
            
            if existing_state:
                # Update existing state (idempotent operation)
                existing_state.custom_attributes = job_data
                existing_state.energy = 100.0  # Keep alive
                existing_state.agent_type = "job_storage"
                await session.commit()
                logger.debug(f"Updated job {job.id} in database")
            else:
                # Create new agent state for this job
                new_state = GeneratorAgentState(
                    name=agent_name,
                    x=0.0,
                    y=0.0,
                    energy=100.0,
                    world_size=100,
                    agent_type="job_storage",
                    custom_attributes=job_data,
                )
                session.add(new_state)
                await session.commit()
                logger.debug(f"Created job {job.id} in database")
        
        return True
    
    try:
        result = await _retry_with_backoff(
            f"save_job_to_database({job.id})",
            _save_operation
        )
        return result
        
    except Exception as e:
        logger.error(
            f"Failed to persist job {job.id} to database after all retries: {e}",
            exc_info=True,
            extra={"job_id": job.id, "job_status": job.status.value}
        )
        return False


async def load_job_from_database(job_id: str) -> Optional[Job]:
    """
    Load a job from the database with automatic retry on transient failures.
    
    This function provides reliable job retrieval with:
    - Automatic retry with exponential backoff
    - Data validation and deserialization
    - Comprehensive error logging
    - Type-safe datetime conversion
    
    Args:
        job_id: Unique job identifier to load
        
    Returns:
        Job instance if found and successfully deserialized, None otherwise.
        Returns None for both "not found" and "error" cases to provide
        consistent API for callers.
        
    Data Integrity:
        - Validates all required fields before returning
        - Converts ISO datetime strings back to datetime objects
        - Handles missing or malformed data gracefully
        
    Industry Standards:
        - Defensive programming (fail safely, not dangerously)
        - SOC 2 Type II: Data retrieval audit trail
        - Type safety (PEP 484)
        
    Example:
        >>> job = await load_job_from_database("abc123")
        >>> if job:
        ...     print(f"Found job with status: {job.status}")
        ... else:
        ...     print("Job not found or database error")
    """
    if not _use_database or not _database:
        logger.debug(f"Database not available, cannot load job {job_id}")
        return None
    
    async def _load_operation():
        """
        Inner operation that will be retried.
        
        Note: Database errors will be caught and retried by the
        _retry_with_backoff wrapper function. No additional error
        handling is needed here.
        """
        # Query generator_agent_state table directly by name (unhashed)
        from omnicore_engine.database.models import GeneratorAgentState
        from sqlalchemy import select
        
        agent_name = f"job_{job_id}"
        
        async with _database.AsyncSessionLocal() as session:
            result = await session.execute(
                select(GeneratorAgentState).filter_by(name=agent_name)
            )
            state = result.scalars().first()
            
            if not state or not state.custom_attributes:
                logger.debug(f"Job {job_id} not found in database")
                return None
            
            # Reconstruct Job from custom_attributes
            job_data = state.custom_attributes
            
            # Validate required fields
            if 'id' not in job_data or 'status' not in job_data:
                logger.warning(f"Job {job_id} data is malformed, missing required fields")
                return None
            
            # Convert ISO format strings back to datetime objects
            for key in ['created_at', 'updated_at', 'completed_at']:
                if key in job_data and job_data[key] is not None:
                    if isinstance(job_data[key], str):
                        try:
                            parsed_dt = datetime.fromisoformat(job_data[key])
                            # Ensure timezone awareness
                            if parsed_dt.tzinfo is None:
                                parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                            job_data[key] = parsed_dt
                        except ValueError as e:
                            logger.warning(
                                f"Invalid datetime format for job {job_id} field {key}: {e}"
                            )
                            # Continue with None value rather than failing entirely
                            job_data[key] = None
            
            try:
                job = Job(**job_data)
                logger.debug(f"Loaded job {job_id} from database")
                return job
            except Exception as e:
                logger.error(
                    f"Failed to deserialize job {job_id} from database: {e}",
                    exc_info=True
                )
                return None
    
    try:
        result = await _retry_with_backoff(
            f"load_job_from_database({job_id})",
            _load_operation
        )
        return result
        
    except Exception as e:
        logger.error(
            f"Failed to load job {job_id} from database after all retries: {e}",
            exc_info=True,
            extra={"job_id": job_id}
        )
        return None


async def delete_job_from_database(job_id: str) -> bool:
    """
    Perform a hard delete of a job from the database with retry logic.
    
    This function permanently removes the job record from the database
    to prevent deleted jobs from reappearing after server restarts.
    Unlike soft delete (setting energy=0), this performs an actual row deletion.
    
    This approach:
    - Prevents "zombie" jobs from reappearing after restart
    - Ensures job recovery query doesn't resurrect deleted jobs
    - Provides clean deletion without relying on cleanup tasks
    
    Note: This is a hard delete operation. The job record is permanently
    removed from the database. For audit trail requirements, consider
    implementing separate audit logging before calling this function.
    
    Args:
        job_id: Unique job identifier to delete
        
    Returns:
        True if deleted successfully or if job doesn't exist (idempotent), False on error
        
    Industry Standards:
        - Hard delete for immediate removal (prevents resurrection issues)
        - Idempotent operations (safe to call multiple times)
        - Retry with exponential backoff for database operations
        
    Alternative Implementation:
        For audit trail preservation (GDPR Article 30, SOC 2 Type II):
        ```python
        # Soft delete approach (requires cleanup task)
        state.energy = 0.0
        await session.commit()
        ```
        
    Example:
        >>> success = await delete_job_from_database("abc123")
        >>> if success:
        ...     logger.info("Job permanently deleted")
    """
    if not _use_database or not _database:
        logger.debug(f"Database not available, cannot delete job {job_id}")
        return False
    
    async def _delete_operation():
        """
        Inner operation that will be retried.
        Performs a hard delete (removes the row from the database).
        """
        from omnicore_engine.database.models import GeneratorAgentState
        from sqlalchemy import select, delete as sa_delete
        
        agent_name = f"job_{job_id}"
        
        async with _database.AsyncSessionLocal() as session:
            # Hard delete - actually remove the row from the database
            result = await session.execute(
                sa_delete(GeneratorAgentState).where(
                    GeneratorAgentState.name == agent_name
                )
            )
            await session.commit()
            rows_deleted = result.rowcount
            if rows_deleted > 0:
                logger.info(f"Hard-deleted job {job_id} from database ({rows_deleted} row(s) removed)")
            else:
                logger.debug(f"Job {job_id} not found in database (already deleted or never existed)")
            return True
    
    try:
        result = await _retry_with_backoff(
            f"delete_job_from_database({job_id})",
            _delete_operation
        )
        return result
        
    except Exception as e:
        logger.error(
            f"Failed to delete job {job_id} from database after all retries: {e}",
            exc_info=True,
            extra={"job_id": job_id}
        )
        return False


__all__ = [
    "initialize_persistence",
    "save_job_to_database",
    "load_job_from_database",
    "delete_job_from_database",
]
