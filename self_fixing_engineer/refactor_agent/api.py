# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
api.py

FastAPI application for the Refactor Agent.

Endpoints:
    POST /refactor   — trigger refactoring
    GET  /status     — crew status
    GET  /agents     — list agents
    GET  /health     — health check
"""

import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = os.path.join(os.path.dirname(__file__), "refactor_agent.yaml")

try:
    from fastapi import FastAPI
    from pydantic import BaseModel

    _FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _FASTAPI_AVAILABLE = False
    logger.warning("FastAPI not installed; api.py will not serve HTTP requests.")

    class FastAPI:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            pass

        def get(self, *a, **kw):
            def _d(fn):
                return fn
            return _d
        post = get

    class BaseModel:  # type: ignore[no-redef]
        pass


app = FastAPI(
    title="Refactor Agent API",
    description="REST API for the AI-powered Refactor Agent crew.",
    version="1.0.0",
)

_crew = None


async def _get_crew():
    """Return or initialise the shared CrewManager instance."""
    global _crew
    if _crew is None:
        from self_fixing_engineer.agent_orchestration.crew_manager import CrewManager

        config_path = os.environ.get("REFACTOR_AGENT_CONFIG", _DEFAULT_CONFIG)
        if os.path.exists(config_path):
            _crew = await CrewManager.from_config_yaml(config_path)
        else:
            _crew = CrewManager()
    return _crew


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

if _FASTAPI_AVAILABLE:
    class RefactorRequest(BaseModel):
        codebase_path: str = "."
        mode: str = "single"
        options: Optional[Dict[str, Any]] = None

    class RefactorResponse(BaseModel):
        status: str
        result: Optional[Dict[str, Any]] = None
        error: Optional[str] = None
        timestamp: float = 0.0
else:
    class RefactorRequest:  # type: ignore[no-redef]
        pass

    class RefactorResponse:  # type: ignore[no-redef]
        pass


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> Dict[str, Any]:
    """Health check endpoint."""
    return {"status": "ok", "timestamp": time.time()}


@app.get("/agents")
async def list_agents() -> Dict[str, Any]:
    """List all configured agents."""
    crew = await _get_crew()
    return {"agents": crew.list_agents(), "timestamp": time.time()}


@app.get("/status")
async def status() -> Dict[str, Any]:
    """Return current crew status."""
    crew = await _get_crew()
    s = await crew.status()
    return s


@app.post("/refactor")
async def refactor(request: RefactorRequest) -> Dict[str, Any]:
    """Trigger a refactoring run."""
    crew = await _get_crew()
    agents = crew.list_agents(tags=["refactor"])
    if not agents:
        agents = crew.list_agents()

    if not agents:
        return {
            "status": "error",
            "error": "No agents available.",
            "timestamp": time.time(),
        }

    agent_name = agents[0]
    try:
        await crew.start_agent(agent_name, caller_role="admin")
        return {
            "status": "started",
            "agent": agent_name,
            "codebase_path": request.codebase_path,
            "timestamp": time.time(),
        }
    except Exception as exc:
        logger.error("refactor endpoint error: %s", exc)
        return {
            "status": "error",
            "error": str(exc),
            "timestamp": time.time(),
        }
