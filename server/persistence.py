# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Job persistence layer for database storage.

This module provides a simple interface for persisting jobs to the database
to ensure they survive application restarts (e.g., after SIGTERM).

FIX Issue 3: Jobs are now persisted to database to prevent loss after restart.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from server.schemas import Job

logger = logging.getLogger(__name__)

# Database will be initialized by the application
_database = None
_use_database = False


def initialize_persistence(database=None):
    """
    Initialize the persistence layer with a database connection.
    
    Args:
        database: Database instance from omnicore_engine.database.Database
    """
    global _database, _use_database
    _database = database
    _use_database = database is not None
    
    if _use_database:
        logger.info("Job persistence initialized with database backend")
    else:
        logger.warning("Job persistence initialized without database - using memory only")


async def save_job_to_database(job: Job) -> bool:
    """
    Save a job to the database for persistence.
    
    Args:
        job: Job instance to persist
        
    Returns:
        True if saved successfully, False otherwise
    """
    if not _use_database or not _database:
        logger.debug(f"Database not available, skipping persistence for job {job.id}")
        return False
    
    try:
        # Serialize job to JSON
        job_data = job.model_dump(mode='json')
        
        # Convert datetime objects to ISO format strings
        for key in ['created_at', 'updated_at', 'completed_at']:
            if key in job_data and job_data[key] is not None:
                if isinstance(job_data[key], datetime):
                    job_data[key] = job_data[key].isoformat()
        
        # Store in GeneratorAgentState table with custom_attributes
        # We use the job_id as the agent name for easy lookup
        from omnicore_engine.database.models import GeneratorAgentState
        
        # Check if agent state already exists
        agent_name = f"job_{job.id}"
        existing_state = await _database.get_agent_state(agent_name)
        
        if existing_state:
            # Update existing state
            existing_state.custom_attributes = job_data
            existing_state.energy = 100.0  # Keep alive
            await _database.save_agent_state(existing_state)
            logger.debug(f"Updated job {job.id} in database")
        else:
            # Create new agent state for this job
            agent_state = GeneratorAgentState(
                name=agent_name,
                x=0.0,
                y=0.0,
                energy=100.0,
                world_size=100,
                agent_type="job_storage",
                custom_attributes=job_data,
            )
            await _database.save_agent_state(agent_state)
            logger.debug(f"Saved job {job.id} to database")
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to save job {job.id} to database: {e}", exc_info=True)
        return False


async def load_job_from_database(job_id: str) -> Optional[Job]:
    """
    Load a job from the database.
    
    Args:
        job_id: Job ID to load
        
    Returns:
        Job instance if found, None otherwise
    """
    if not _use_database or not _database:
        logger.debug(f"Database not available, cannot load job {job_id}")
        return None
    
    try:
        agent_name = f"job_{job_id}"
        agent_state = await _database.get_agent_state(agent_name)
        
        if not agent_state or not agent_state.custom_attributes:
            logger.debug(f"Job {job_id} not found in database")
            return None
        
        # Reconstruct Job from custom_attributes
        job_data = agent_state.custom_attributes
        
        # Convert ISO format strings back to datetime objects
        for key in ['created_at', 'updated_at', 'completed_at']:
            if key in job_data and job_data[key] is not None:
                if isinstance(job_data[key], str):
                    job_data[key] = datetime.fromisoformat(job_data[key])
        
        job = Job(**job_data)
        logger.debug(f"Loaded job {job_id} from database")
        return job
        
    except Exception as e:
        logger.error(f"Failed to load job {job_id} from database: {e}", exc_info=True)
        return None


async def delete_job_from_database(job_id: str) -> bool:
    """
    Delete a job from the database.
    
    Args:
        job_id: Job ID to delete
        
    Returns:
        True if deleted successfully, False otherwise
    """
    if not _use_database or not _database:
        logger.debug(f"Database not available, cannot delete job {job_id}")
        return False
    
    try:
        agent_name = f"job_{job_id}"
        # Set energy to 0 to mark for cleanup (soft delete)
        agent_state = await _database.get_agent_state(agent_name)
        if agent_state:
            agent_state.energy = 0.0
            await _database.save_agent_state(agent_state)
            logger.debug(f"Marked job {job_id} for deletion in database")
            return True
        return False
        
    except Exception as e:
        logger.error(f"Failed to delete job {job_id} from database: {e}", exc_info=True)
        return False


__all__ = [
    "initialize_persistence",
    "save_job_to_database",
    "load_job_from_database",
    "delete_job_from_database",
]
