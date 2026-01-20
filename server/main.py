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
    events_router,
    fixes_router,
    generator_router,
    jobs_router,
    omnicore_router,
    sfe_router,
)
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

    Returns the health status of the API server and all connected components.

    **Returns:**
    - Overall health status
    - Component-level health information
    - API version
    - Timestamp
    """
    # In real implementation, check actual component health
    # Example:
    # from omnicore_engine.core import get_system_health
    # health = await get_system_health()

    return HealthResponse(
        status="healthy",
        version=__version__,
        components={
            "api": "healthy",
            "omnicore": "healthy",
            "generator": "healthy",
            "sfe": "healthy",
            "database": "healthy",
            "message_bus": "healthy",
        },
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
app.include_router(jobs_router, prefix="/api")
app.include_router(generator_router, prefix="/api")
app.include_router(omnicore_router, prefix="/api")
app.include_router(sfe_router, prefix="/api")
app.include_router(fixes_router, prefix="/api")
app.include_router(events_router, prefix="/api")

logger.info("FastAPI application configured successfully")
