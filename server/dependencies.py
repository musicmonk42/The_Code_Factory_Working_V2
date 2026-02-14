# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Shared FastAPI dependencies for the Code Factory Platform.

This module provides reusable dependency functions that can be injected
into route handlers to enforce preconditions and gate access.
"""

import logging
from fastapi import HTTPException

logger = logging.getLogger(__name__)


async def require_agents_ready():
    """
    FastAPI dependency that ensures agents are loaded before accepting work.
    
    This dependency checks if agents have finished loading before allowing
    job submission endpoints to accept requests. If agents are still loading
    or haven't started loading yet, it returns HTTP 503 with a clear message
    asking the client to retry.
    
    This prevents jobs from "vanishing" when submitted during the startup
    window before agents are ready.
    
    Usage:
        @router.post("/endpoint")
        async def handler(_: None = Depends(require_agents_ready)):
            # Handler code only runs if agents are ready
            return {"status": "ok"}
    
    Raises:
        HTTPException: 503 if agents are not ready
    """
    # Import here to avoid circular dependencies (main.py imports routers)
    from server.main import get_agent_loader, _routers_loaded
    
    # First ensure routers are loaded (includes agent loader)
    # get_agent_loader is a module-level variable that is None initially and set during router loading
    if not _routers_loaded or get_agent_loader is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_not_ready",
                "message": "Service is still initializing. Please retry in a few seconds.",
                "retry_after": 5
            },
            headers={"Retry-After": "5"}
        )
    
    try:
        loader = get_agent_loader()
        
        # Get agent status to check loading state
        # This uses the public get_status() method instead of private attributes
        status = loader.get_status()
        loading_in_progress = status.get('loading_in_progress', False)
        loading_completed = status.get('loading_completed', False)
        
        # Debug logging to help diagnose agent readiness issues
        logger.debug(
            f"Agent readiness check: loading_in_progress={loading_in_progress}, "
            f"loading_completed={loading_completed}, status={status}"
        )
        
        # Check if agents are currently loading
        if loading_in_progress:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "service_not_ready",
                    "message": "Agents are still loading. Please retry in a few seconds.",
                    "retry_after": 10
                },
                headers={"Retry-After": "10"}
            )
        
        # Check if loading has completed successfully
        if not loading_completed:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "service_not_ready",
                    "message": "Agent loading has not started yet. Please retry shortly.",
                    "retry_after": 5
                },
                headers={"Retry-After": "5"}
            )
    except HTTPException:
        # Re-raise HTTP exceptions (503 responses from above)
        raise
    except Exception as e:
        # If we can't check readiness due to an unexpected error, fail closed (return 503)
        # This prevents jobs from vanishing if agent status check fails
        logger.error(
            f"Error checking agent readiness: {e}. Failing closed to prevent job loss.",
            exc_info=True
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "agent_status_check_failed",
                "message": "Unable to verify agent readiness. Please retry in a few seconds.",
                "retry_after": 10
            },
            headers={"Retry-After": "10"}
        )
