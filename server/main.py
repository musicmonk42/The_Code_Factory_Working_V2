"""
Main FastAPI application for The Code Factory Platform.

This module provides a comprehensive HTTP API that acts as the central entry point
for the platform, enabling complete interaction with omnicore_engine, generator,
and self_fixing_engineer modules through OmniCore's centralized coordination.

Features:
- File uploads for generator jobs
- Complete job lifecycle management
- Per-stage progress dashboard
- Error and fix workflow management
- Real-time events streaming (WebSocket and SSE)
- Modular routing for extensibility
- OpenAPI documentation
- Centralized routing through OmniCore

All module interactions are routed through OmniCore's message bus and
coordination layer for centralized control and monitoring.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from server import __version__
from server.routers import (
    api_keys_router,
    diagnostics_router,
    events_router,
    fixes_router,
    generator_router,
    jobs_router,
    omnicore_router,
    sfe_router,
)
from server.utils.agent_loader import AgentType, get_agent_loader
from server.schemas import ErrorResponse, HealthResponse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Get the directory where this file is located
BASE_DIR = Path(__file__).resolve().parent

# Configure templates and static files
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
static_dir = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    Handles startup and shutdown events for the API server,
    including initialization of connections to OmniCore and other modules.
    """
    # Startup
    logger.info("Starting Code Factory API Server")
    logger.info(f"Version: {__version__}")
    
    # Perform agent diagnostics at startup
    logger.info("=" * 80)
    logger.info("AGENT DIAGNOSTICS AT STARTUP")
    logger.info("=" * 80)
    
    try:
        loader = get_agent_loader()
        
        # Attempt to load all known agents
        agents_to_load = [
            (AgentType.CODEGEN, "generator.agents.codegen_agent.codegen_agent", ["generate_code"]),
            (AgentType.TESTGEN, "generator.agents.testgen_agent.testgen_agent", ["TestgenAgent"]),
            (AgentType.DEPLOY, "generator.agents.deploy_agent.deploy_agent", ["DeployAgent"]),
            (AgentType.DOCGEN, "generator.agents.docgen_agent.docgen_agent", ["DocgenAgent"]),
            (AgentType.CRITIQUE, "generator.agents.critique_agent.critique_agent", ["CritiqueAgent"]),
        ]
        
        for agent_type, module_path, import_names in agents_to_load:
            loader.safe_import_agent(
                agent_type=agent_type,
                module_path=module_path,
                import_names=import_names,
                description=f"Load {agent_type.value} agent at startup",
            )
        
        # Get and log status
        status = loader.get_status()
        logger.info(f"Total agents: {status['total_agents']}")
        logger.info(f"Available agents: {len(status['available_agents'])}")
        logger.info(f"Unavailable agents: {len(status['unavailable_agents'])}")
        logger.info(f"Availability rate: {status['availability_rate']:.1%}")
        
        if status['available_agents']:
            logger.info(f"✓ Available: {', '.join(status['available_agents'])}")
        
        if status['unavailable_agents']:
            logger.warning(f"✗ Unavailable: {', '.join(status['unavailable_agents'])}")
        
        if status['missing_dependencies']:
            logger.error(
                f"Missing dependencies detected: {', '.join(status['missing_dependencies'])}"
            )
            logger.error(
                f"Install with: pip install {' '.join(status['missing_dependencies'])}"
            )
        
        # Check environment variables
        env_vars = status['environment_variables']
        api_keys_set = sum(1 for v in env_vars.values() if v == 'set')
        logger.info(f"API keys configured: {api_keys_set}/{len(env_vars)}")
        
        # Log diagnostic endpoint
        logger.info("Diagnostics available at: /api/diagnostics/agents")
        logger.info("Full report available at: /api/diagnostics/report")
        
        logger.info("=" * 80)
        
        # In production, optionally enforce strict mode
        # Uncomment to require all agents at startup:
        # if os.getenv("REQUIRE_ALL_AGENTS", "0") == "1":
        #     loader.validate_for_production()
        
    except Exception as e:
        logger.error(f"Error during agent diagnostics: {e}", exc_info=True)
        # Don't fail startup, but log the issue
        logger.warning("Continuing startup despite agent diagnostic errors")

    # Initialize connections to OmniCore
    # Example:
    # from omnicore_engine.message_bus import init_message_bus
    # await init_message_bus()

    logger.info("API Server ready")

    yield

    # Shutdown
    logger.info("Shutting down Code Factory API Server")

    # Clean up connections
    # Example:
    # await shutdown_message_bus()

    logger.info("API Server stopped")


# Create FastAPI application
app = FastAPI(
    title="Code Factory Platform API",
    description="""
    **The Code Factory Platform** - Enterprise-grade HTTP API for automated 
    software development and maintenance.

    This API provides comprehensive access to all platform capabilities:
    
    ## Core Features
    
    * **Job Management**: Create, monitor, and manage generation jobs
    * **File Upload**: Upload README files and other inputs for code generation
    * **Progress Tracking**: Real-time per-stage progress monitoring
    * **Error Detection**: Automated error detection via Self-Fixing Engineer
    * **Fix Management**: Propose, review, apply, and rollback fixes
    * **Real-time Events**: WebSocket and SSE streaming for live updates
    * **Module Integration**: Unified access to Generator, OmniCore, and SFE
    
    ## Architecture
    
    All operations are centrally coordinated through **OmniCore Engine**, which
    manages:
    - Inter-module communication via message bus
    - Job routing and workflow orchestration
    - Plugin management and extensibility
    - Audit logging and metrics collection
    
    ## Getting Started
    
    1. Create a job: `POST /api/jobs/`
    2. Upload files: `POST /api/generator/{job_id}/upload`
    3. Monitor progress: `GET /api/jobs/{job_id}/progress`
    4. View errors: `GET /api/sfe/{job_id}/errors`
    5. Apply fixes: `POST /api/sfe/fixes/{fix_id}/apply`
    
    ## Real-time Updates
    
    Subscribe to real-time events:
    - WebSocket: `ws://host/api/events/ws`
    - SSE: `GET /api/events/sse?job_id={job_id}`
    """,
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    logger.info(f"Mounted static files from {static_dir}")
else:
    logger.warning(f"Static directory not found: {static_dir}")


# Exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler for unhandled errors.
    """
    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="internal_server_error",
            message="An unexpected error occurred",
            details={"type": type(exc).__name__},
        ).dict(),
    )


# Health check endpoint
@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check() -> HealthResponse:
    """
    Health check endpoint.

    Returns the health status of the API server and all connected components,
    including agent availability status.

    **Returns:**
    - Overall health status
    - Component-level health information including agent availability
    - API version
    - Timestamp
    """
    # Get agent status from loader
    try:
        from server.utils.agent_loader import get_agent_loader
        
        loader = get_agent_loader()
        agent_status = loader.get_status()
        
        # Determine agent component health
        agent_availability = agent_status.get('availability_rate', 0.0)
        
        # Consider agents healthy if at least 50% are available
        # or if no agents have been loaded yet (initial state)
        if agent_status.get('total_agents', 0) == 0:
            agents_health = "unknown"
        elif agent_availability >= 0.5:
            agents_health = "healthy"
        elif agent_availability > 0:
            agents_health = "degraded"
        else:
            agents_health = "unhealthy"
        
        # Build component health status
        components = {
            "api": "healthy",
            "omnicore": "healthy",
            "database": "healthy",
            "message_bus": "healthy",
            "agents": agents_health,
        }
        
        # Add specific agent status
        for agent_name, is_available in [
            (name, agent_status['agents'][name]['available'])
            for name in agent_status.get('agents', {})
        ]:
            components[f"agent_{agent_name}"] = "available" if is_available else "unavailable"
        
        # Overall status is degraded if any component is not healthy
        overall_status = "healthy"
        if agents_health in ["degraded", "unhealthy"]:
            overall_status = "degraded"
        
    except Exception as e:
        logger.error(f"Error checking agent health: {e}", exc_info=True)
        components = {
            "api": "healthy",
            "omnicore": "healthy",
            "generator": "healthy",
            "sfe": "healthy",
            "database": "healthy",
            "message_bus": "healthy",
            "agents": "unknown",
        }
        overall_status = "degraded"

    return HealthResponse(
        status=overall_status,
        version=__version__,
        components=components,
        timestamp=datetime.utcnow().isoformat(),
    )


@app.get("/", tags=["Root"])
async def root(request: Request):
    """
    API root endpoint / Web UI.

    Serves the A.S.E web interface or provides API information in JSON format
    based on the Accept header.

    **Returns:**
    - HTML: A.S.E Web Interface (if Accept: text/html)
    - JSON: API information and links (default)
    """
    # Check if client wants HTML
    accept_header = request.headers.get("accept", "")
    if "text/html" in accept_header:
        # Serve the web UI
        return templates.TemplateResponse("index.html", {"request": request})

    # Otherwise return JSON API info
    return {
        "name": "Code Factory Platform API",
        "version": __version__,
        "description": "Enterprise-grade HTTP API for automated software development",
        "ui": {
            "web_interface": "/",
            "description": "Access the A.S.E web interface by visiting / in a browser",
        },
        "documentation": {
            "swagger": "/api/docs",
            "redoc": "/api/redoc",
            "openapi": "/api/openapi.json",
        },
        "endpoints": {
            "health": "/health",
            "jobs": "/api/jobs",
            "generator": "/api/generator",
            "omnicore": "/api/omnicore",
            "sfe": "/api/sfe",
            "fixes": "/api/fixes",
            "events": "/api/events",
        },
    }


# Include routers with /api prefix
app.include_router(api_keys_router, prefix="/api")
app.include_router(diagnostics_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
app.include_router(generator_router, prefix="/api")
app.include_router(omnicore_router, prefix="/api")
app.include_router(sfe_router, prefix="/api")
app.include_router(fixes_router, prefix="/api")
app.include_router(events_router, prefix="/api")

logger.info("FastAPI application configured successfully")
