"""
Diagnostics Router
==================

This router provides diagnostic endpoints for monitoring agent availability,
import status, and system health. It implements comprehensive visibility into
the agent loading process for debugging and production monitoring.

Endpoints:
----------
- GET /api/diagnostics/agents - Get agent availability status
- GET /api/diagnostics/agents/{agent_name} - Get specific agent details
- GET /api/diagnostics/report - Get detailed diagnostic report
- GET /api/diagnostics/environment - Get environment variable status
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from server.utils.agent_loader import get_agent_loader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/diagnostics", tags=["Diagnostics"])


@router.get("/agents", response_model=Dict[str, Any])
async def get_agents_status(
    include_errors: bool = Query(
        default=True,
        description="Include detailed error information in response"
    ),
) -> Dict[str, Any]:
    """
    Get status of all agent imports.
    
    Returns comprehensive information about which agents are available,
    which failed to load, and why they failed.
    
    **Query Parameters:**
    - `include_errors`: Include detailed error information (default: true)
    
    **Returns:**
    - Agent availability status
    - Missing dependencies
    - Environment variable status
    - Import attempt counts
    
    **Response Example:**
    ```json
    {
      "total_agents": 5,
      "available_agents": ["deploy"],
      "unavailable_agents": ["codegen", "testgen", "docgen", "critique"],
      "availability_rate": 0.2,
      "missing_dependencies": ["fastapi", "prometheus_client", "tiktoken", "hypothesis"],
      "agents": {
        "codegen": {
          "available": false,
          "error": {
            "type": "ModuleNotFoundError",
            "message": "No module named 'fastapi'",
            "missing_dependencies": ["fastapi"]
          }
        }
      }
    }
    ```
    """
    try:
        loader = get_agent_loader()
        status = loader.get_status()
        
        if not include_errors:
            # Remove error details if not requested
            for agent_name in status.get("agents", {}):
                if "error" in status["agents"][agent_name]:
                    status["agents"][agent_name]["error"] = "Error details hidden"
        
        return status
        
    except Exception as e:
        logger.error(f"Error getting agent status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve agent status: {str(e)}"
        )


@router.get("/agents/{agent_name}", response_model=Dict[str, Any])
async def get_agent_details(agent_name: str) -> Dict[str, Any]:
    """
    Get detailed information about a specific agent.
    
    **Path Parameters:**
    - `agent_name`: Name of the agent (e.g., 'codegen', 'testgen')
    
    **Returns:**
    - Agent availability status
    - Module path
    - Detailed error information if unavailable
    - Import attempt count
    
    **Response Example:**
    ```json
    {
      "name": "codegen",
      "available": false,
      "module_path": "generator.agents.codegen_agent.codegen_agent",
      "error": {
        "type": "ModuleNotFoundError",
        "message": "No module named 'fastapi'",
        "traceback": "...",
        "missing_dependencies": ["fastapi"],
        "environment_issues": [],
        "timestamp": "2026-01-20T03:00:00Z"
      },
      "import_attempts": 1
    }
    ```
    """
    try:
        loader = get_agent_loader()
        status = loader.get_status()
        
        # Check if agent exists
        if agent_name not in status["agents"]:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{agent_name}' not found. Available agents: {list(status['agents'].keys())}"
            )
        
        agent_info = status["agents"][agent_name]
        
        # Add import attempt count
        agent_info["import_attempts"] = status["import_attempts"].get(agent_name, 0)
        
        return {
            "name": agent_name,
            **agent_info
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting agent details for {agent_name}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve agent details: {str(e)}"
        )


@router.get("/report", response_class=PlainTextResponse)
async def get_diagnostic_report() -> str:
    """
    Get a detailed human-readable diagnostic report.
    
    Returns a comprehensive text report with:
    - Summary of agent availability
    - List of available agents with load times
    - List of unavailable agents with error details
    - Missing dependencies summary
    - Environment variable status
    - Troubleshooting suggestions
    
    **Returns:**
    - Plain text diagnostic report
    
    **Example:**
    ```
    ================================================================================
    AGENT LOADER DIAGNOSTIC REPORT
    ================================================================================
    Generated at: 2026-01-20T03:00:00Z
    Startup time: 2026-01-20T02:59:00Z
    Strict mode: False
    Debug mode: False
    
    SUMMARY
    --------------------------------------------------------------------------------
    Total agents: 5
    Available: 1
    Unavailable: 4
    Availability rate: 20.0%
    ...
    ```
    """
    try:
        loader = get_agent_loader()
        report = loader.get_detailed_error_report()
        return report
        
    except Exception as e:
        logger.error(f"Error generating diagnostic report: {e}", exc_info=True)
        return f"ERROR: Failed to generate diagnostic report: {str(e)}"


@router.get("/environment", response_model=Dict[str, Any])
async def get_environment_status() -> Dict[str, Any]:
    """
    Get environment variable and configuration status.
    
    Returns information about:
    - Environment variables (API keys, configuration)
    - Python path configuration
    - System settings
    
    **Returns:**
    - Environment variable status (set/not_set, not values)
    - System configuration
    
    **Response Example:**
    ```json
    {
      "environment_variables": {
        "OPENAI_API_KEY": "set",
        "ANTHROPIC_API_KEY": "not_set",
        "GENERATOR_STRICT_MODE": "not_set",
        "DEBUG": "not_set"
      },
      "system": {
        "python_path_count": 5,
        "strict_mode": false,
        "debug_mode": false
      }
    }
    ```
    """
    try:
        import os
        import sys
        
        loader = get_agent_loader()
        status = loader.get_status()
        
        # System information
        system_info = {
            "python_path_count": len(sys.path),
            "strict_mode": status["strict_mode"],
            "debug_mode": status["debug_mode"],
            "startup_time": status["startup_time"],
        }
        
        return {
            "environment_variables": status["environment_variables"],
            "system": system_info,
        }
        
    except Exception as e:
        logger.error(f"Error getting environment status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve environment status: {str(e)}"
        )


@router.post("/validate", response_model=Dict[str, Any])
async def validate_agents_for_production(
    required_agents: Optional[list[str]] = None,
) -> Dict[str, Any]:
    """
    Validate that all required agents are available for production use.
    
    This endpoint can be used in health checks or deployment validation
    to ensure the system is ready to handle requests.
    
    **Request Body:**
    ```json
    {
      "required_agents": ["codegen", "testgen"]
    }
    ```
    
    **Returns:**
    - Validation result
    - List of missing agents if validation fails
    
    **Response Example (Success):**
    ```json
    {
      "status": "valid",
      "message": "All required agents are available",
      "required_agents": ["codegen", "testgen"],
      "available_agents": ["codegen", "testgen"]
    }
    ```
    
    **Response Example (Failure):**
    ```json
    {
      "status": "invalid",
      "message": "Some required agents are unavailable",
      "required_agents": ["codegen", "testgen"],
      "missing_agents": ["testgen"],
      "errors": {
        "testgen": "ModuleNotFoundError: No module named 'tiktoken'"
      }
    }
    ```
    """
    try:
        loader = get_agent_loader()
        status = loader.get_status()
        
        # Default to all agents if not specified
        if required_agents is None:
            required_agents = list(status["agents"].keys())
        
        # Check which required agents are missing
        missing_agents = []
        errors = {}
        
        for agent_name in required_agents:
            if agent_name not in status["agents"]:
                missing_agents.append(agent_name)
                errors[agent_name] = "Agent not found"
            elif not status["agents"][agent_name]["available"]:
                missing_agents.append(agent_name)
                error_info = status["agents"][agent_name].get("error", {})
                errors[agent_name] = (
                    f"{error_info.get('type', 'Unknown')}: "
                    f"{error_info.get('message', 'Unknown error')}"
                )
        
        if missing_agents:
            return {
                "status": "invalid",
                "message": "Some required agents are unavailable",
                "required_agents": required_agents,
                "missing_agents": missing_agents,
                "errors": errors,
            }
        
        return {
            "status": "valid",
            "message": "All required agents are available",
            "required_agents": required_agents,
            "available_agents": required_agents,
        }
        
    except Exception as e:
        logger.error(f"Error validating agents: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to validate agents: {str(e)}"
        )
