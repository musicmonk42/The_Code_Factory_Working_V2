# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Shared in-memory storage for the server.

This module provides centralized in-memory storage that is shared across
all routers to ensure data consistency. In production, this should be
replaced with a proper database backend.
"""

import logging
from collections import OrderedDict
from typing import Dict

from server.schemas import Fix, Job, JobStatus

logger = logging.getLogger(__name__)

# Maximum number of jobs to keep in memory
# When this limit is reached, oldest completed/failed/cancelled jobs are evicted
MAX_JOBS = 10000

# Shared storage dictionaries
jobs_db: Dict[str, Job] = OrderedDict()
fixes_db: Dict[str, Fix] = {}


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
        elif len(jobs_db) > MAX_JOBS:
            # If we still exceed the limit, log a warning
            # This means too many active jobs
            logger.warning(
                f"jobs_db exceeds limit ({len(jobs_db)} > {MAX_JOBS}) "
                "but no completed jobs available to evict. Consider increasing MAX_JOBS."
            )


__all__ = ["jobs_db", "fixes_db", "add_job", "MAX_JOBS"]
