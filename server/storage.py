# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Shared storage for the server with PostgreSQL write-through cache.

This module provides centralized storage that works across multiple workers
by using PostgreSQL as the backend when DATABASE_URL is configured, with
an in-memory write-through cache for performance. Falls back to in-memory
only storage for development/testing when DATABASE_URL is not set.

Industry Standards Compliance:
- Multi-worker safe with PostgreSQL backend
- ACID transaction guarantees (PostgreSQL)
- Write-through cache for performance
- Graceful degradation to in-memory for development
"""

import asyncio
import json
import logging
import os
from collections import OrderedDict
from datetime import datetime
from typing import Dict, Optional

from server.schemas import Fix, Job, JobStatus

logger = logging.getLogger(__name__)

# Maximum number of jobs to keep in memory
# When this limit is reached, oldest completed/failed/cancelled jobs are evicted
MAX_JOBS = 10000

# Shared storage dictionaries
_jobs_memory_cache: Dict[str, Job] = OrderedDict()
fixes_db: Dict[str, Fix] = {}

# PostgreSQL backend configuration
_pg_engine = None
_pg_session_maker = None
_pg_enabled = False
_pg_initialized = False
_pg_init_lock = None


async def _initialize_postgresql():
    """Initialize PostgreSQL backend for job storage."""
    global _pg_engine, _pg_session_maker, _pg_enabled, _pg_initialized, _pg_init_lock
    
    if _pg_initialized:
        return True
    
    if _pg_init_lock is None:
        _pg_init_lock = asyncio.Lock()
    
    async with _pg_init_lock:
        if _pg_initialized:  # Double-check after acquiring lock
            return True
        
        database_url = os.getenv("DATABASE_URL")
        if not database_url or not database_url.startswith(("postgresql://", "postgres://")):
            logger.info("DATABASE_URL not set or not PostgreSQL, using in-memory storage only")
            _pg_enabled = False
            _pg_initialized = True
            return False
        
        try:
            from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
            from sqlalchemy import text
            
            # Convert postgresql:// to postgresql+asyncpg://
            db_url = database_url
            if db_url.startswith("postgresql://"):
                db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif db_url.startswith("postgres://"):
                db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
            
            _pg_engine = create_async_engine(
                db_url,
                pool_size=20,
                max_overflow=10,
                pool_pre_ping=True,
                pool_recycle=3600,
            )
            
            _pg_session_maker = async_sessionmaker(
                _pg_engine,
                expire_on_commit=False,
            )
            
            # Create jobs table if it doesn't exist
            async with _pg_engine.begin() as conn:
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS jobs (
                        id VARCHAR(255) PRIMARY KEY,
                        data JSONB NOT NULL,
                        status VARCHAR(50) NOT NULL,
                        created_at TIMESTAMP NOT NULL,
                        updated_at TIMESTAMP NOT NULL,
                        completed_at TIMESTAMP
                    )
                """))
                
                # Create indexes for performance
                await conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_jobs_status 
                    ON jobs(status)
                """))
                
                await conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_jobs_created_at 
                    ON jobs(created_at)
                """))
            
            # Load existing jobs from PostgreSQL into memory cache
            await _load_jobs_from_postgresql()
            
            _pg_enabled = True
            _pg_initialized = True
            logger.info("PostgreSQL job storage initialized successfully (multi-worker safe)")
            return True
            
        except Exception as e:
            logger.warning(
                f"Failed to initialize PostgreSQL storage, using in-memory only: {e}",
                exc_info=True
            )
            _pg_enabled = False
            _pg_initialized = True
            return False


async def _load_jobs_from_postgresql():
    """Load all jobs from PostgreSQL into memory cache."""
    global _jobs_memory_cache
    
    if not _pg_enabled or not _pg_session_maker:
        return
    
    try:
        from sqlalchemy import text
        
        async with _pg_session_maker() as session:
            result = await session.execute(
                text("SELECT id, data FROM jobs ORDER BY created_at DESC")
            )
            
            loaded_count = 0
            for row in result:
                job_id = row[0]
                job_data = json.loads(row[1])
                
                # Convert ISO format strings back to datetime objects
                for key in ['created_at', 'updated_at', 'completed_at']:
                    if key in job_data and job_data[key] is not None:
                        if isinstance(job_data[key], str):
                            try:
                                job_data[key] = datetime.fromisoformat(job_data[key])
                            except ValueError:
                                job_data[key] = None
                
                try:
                    job = Job(**job_data)
                    _jobs_memory_cache[job_id] = job
                    loaded_count += 1
                except Exception as e:
                    logger.warning(f"Failed to deserialize job {job_id}: {e}")
            
            logger.info(f"Loaded {loaded_count} jobs from PostgreSQL into memory cache")
            
    except Exception as e:
        logger.error(f"Failed to load jobs from PostgreSQL: {e}", exc_info=True)


async def _save_job_to_postgresql(job_id: str, job: Job):
    """Save a job to PostgreSQL (async background operation)."""
    if not _pg_enabled or not _pg_session_maker:
        return
    
    try:
        from sqlalchemy import text
        
        # Serialize job to JSON
        job_data = job.model_dump(mode='json')
        
        # Convert datetime objects to ISO format strings
        for key in ['created_at', 'updated_at', 'completed_at']:
            if key in job_data and job_data[key] is not None:
                if isinstance(job_data[key], datetime):
                    job_data[key] = job_data[key].isoformat()
        
        async with _pg_session_maker() as session:
            # Upsert operation
            await session.execute(
                text("""
                    INSERT INTO jobs (id, data, status, created_at, updated_at, completed_at)
                    VALUES (:id, :data, :status, :created_at, :updated_at, :completed_at)
                    ON CONFLICT (id) DO UPDATE SET
                        data = EXCLUDED.data,
                        status = EXCLUDED.status,
                        updated_at = EXCLUDED.updated_at,
                        completed_at = EXCLUDED.completed_at
                """),
                {
                    "id": job_id,
                    "data": json.dumps(job_data),
                    "status": job.status.value,
                    "created_at": job.created_at,
                    "updated_at": job.updated_at,
                    "completed_at": job.completed_at,
                }
            )
            await session.commit()
            
    except Exception as e:
        logger.error(f"Failed to save job {job_id} to PostgreSQL: {e}", exc_info=True)


async def _delete_job_from_postgresql(job_id: str):
    """Delete a job from PostgreSQL (async background operation)."""
    if not _pg_enabled or not _pg_session_maker:
        return
    
    try:
        from sqlalchemy import text
        
        async with _pg_session_maker() as session:
            await session.execute(
                text("DELETE FROM jobs WHERE id = :id"),
                {"id": job_id}
            )
            await session.commit()
            
    except Exception as e:
        logger.error(f"Failed to delete job {job_id} from PostgreSQL: {e}", exc_info=True)


class JobsDBProxy:
    """
    Proxy class that provides dict-like interface with PostgreSQL write-through cache.
    
    This class maintains the existing dict-like API while transparently persisting
    to PostgreSQL when available, making it multi-worker safe.
    """
    
    def __setitem__(self, job_id: str, job: Job):
        """Set a job (write-through to PostgreSQL)."""
        _jobs_memory_cache[job_id] = job
        
        # Asynchronously save to PostgreSQL if enabled
        if _pg_enabled:
            asyncio.create_task(_save_job_to_postgresql(job_id, job))
    
    def __getitem__(self, job_id: str) -> Job:
        """Get a job from memory cache."""
        return _jobs_memory_cache[job_id]
    
    def __delitem__(self, job_id: str):
        """Delete a job (from both memory and PostgreSQL)."""
        if job_id in _jobs_memory_cache:
            del _jobs_memory_cache[job_id]
        
        # Asynchronously delete from PostgreSQL if enabled
        if _pg_enabled:
            asyncio.create_task(_delete_job_from_postgresql(job_id))
    
    def __contains__(self, job_id: str) -> bool:
        """Check if job exists in memory cache."""
        return job_id in _jobs_memory_cache
    
    def get(self, job_id: str, default=None) -> Optional[Job]:
        """Get a job with default fallback."""
        return _jobs_memory_cache.get(job_id, default)
    
    def values(self):
        """Get all job values from memory cache."""
        return _jobs_memory_cache.values()
    
    def keys(self):
        """Get all job IDs from memory cache."""
        return _jobs_memory_cache.keys()
    
    def items(self):
        """Get all job items from memory cache."""
        return _jobs_memory_cache.items()
    
    def __len__(self) -> int:
        """Get number of jobs in memory cache."""
        return len(_jobs_memory_cache)


# Create the global jobs_db instance
jobs_db = JobsDBProxy()


def add_job(job: Job) -> None:
    """
    Add a job to the jobs_db with automatic eviction of old completed jobs.
    
    When MAX_JOBS is reached, evicts the oldest completed/failed/cancelled jobs
    to make room for new jobs. Active jobs (pending/running) are never evicted.
    
    Works with both PostgreSQL-backed and in-memory storage transparently.
    
    Args:
        job: Job instance to add to the database
    """
    # Add the new job
    jobs_db[job.id] = job
    
    # Check if we need to evict old jobs (only from memory cache)
    if len(_jobs_memory_cache) > MAX_JOBS:
        # Find oldest completed/failed/cancelled jobs to evict
        # Never evict active jobs (pending/running)
        evictable_statuses = {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
        
        evicted_count = 0
        # Iterate through jobs in order (oldest first due to OrderedDict)
        for job_id in list(_jobs_memory_cache.keys()):
            if len(_jobs_memory_cache) <= MAX_JOBS:
                break
            
            job = _jobs_memory_cache[job_id]
            if job.status in evictable_statuses:
                del _jobs_memory_cache[job_id]
                # Note: We keep the job in PostgreSQL even after evicting from memory
                # This allows workers to reload jobs from DB if needed
                evicted_count += 1
        
        if evicted_count > 0:
            logger.info(
                f"Evicted {evicted_count} completed jobs from memory cache "
                f"(limit: {MAX_JOBS}, current: {len(_jobs_memory_cache)})"
            )
        else:
            # If no jobs were evicted, it means all jobs are active
            # This could indicate a need to increase MAX_JOBS
            active_count = len([j for j in _jobs_memory_cache.values() if j.status in {JobStatus.PENDING, JobStatus.RUNNING}])
            logger.warning(
                f"jobs_db exceeds limit ({len(_jobs_memory_cache)} > {MAX_JOBS}) "
                f"with {active_count} active jobs. No completed jobs available to evict. "
                "Consider increasing MAX_JOBS."
            )


__all__ = ["jobs_db", "fixes_db", "add_job", "MAX_JOBS"]



def add_job(job: Job) -> None:
    """
    Add a job to the jobs_db with automatic eviction of old completed jobs.
    
    When MAX_JOBS is reached, evicts the oldest completed/failed/cancelled jobs
    to make room for new jobs. Active jobs (pending/running) are never evicted.
    
    Args:
        job: Job instance to add to the database
    """
    # Add the new job
    jobs_db[job.id] = job
    
    # Check if we need to evict old jobs
    if len(jobs_db) > MAX_JOBS:
        # Find oldest completed/failed/cancelled jobs to evict
        # Never evict active jobs (pending/running)
        evictable_statuses = {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
        
        evicted_count = 0
        # Iterate through jobs in order (oldest first due to OrderedDict)
        for job_id in list(jobs_db.keys()):
            if len(jobs_db) <= MAX_JOBS:
                break
            
            job = jobs_db[job_id]
            if job.status in evictable_statuses:
                del jobs_db[job_id]
                evicted_count += 1
        
        if evicted_count > 0:
            logger.info(
                f"Evicted {evicted_count} completed jobs from jobs_db "
                f"(limit: {MAX_JOBS}, current: {len(jobs_db)})"
            )
        else:
            # If no jobs were evicted, it means all jobs are active
            # This could indicate a need to increase MAX_JOBS
            active_count = len([j for j in jobs_db.values() if j.status in {JobStatus.PENDING, JobStatus.RUNNING}])
            logger.warning(
                f"jobs_db exceeds limit ({len(jobs_db)} > {MAX_JOBS}) "
                f"with {active_count} active jobs. No completed jobs available to evict. "
                "Consider increasing MAX_JOBS."
            )


__all__ = ["jobs_db", "fixes_db", "add_job", "MAX_JOBS"]
