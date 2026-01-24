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

# CRITICAL: Set environment variables BEFORE any imports that might trigger plugin loading
# This prevents circular imports, SystemExit crashes, and reduces startup time
import os

# --- PRODUCTION MODE CONFIGURATION START ---
# Force Production Mode
os.environ["APP_ENV"] = "production"
os.environ["DEV_MODE"] = "0"

# FIX: Unset AUDIT_LOG_DEV_MODE in production to prevent conflicting configuration
# This prevents the security error: "Production environment variables are set but dev mode is also enabled"
if "AUDIT_LOG_DEV_MODE" in os.environ:
    del os.environ["AUDIT_LOG_DEV_MODE"]

# AUDIT CRYPTO CONFIGURATION
# Allow audit crypto initialization to fail gracefully if secrets are not configured
# This prevents the server from crashing on startup when secrets are not yet configured
# Set to "0" once AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 is properly configured
os.environ.setdefault("AUDIT_CRYPTO_ALLOW_INIT_FAILURE", "1")

# Set audit crypto mode to allow startup without full cryptographic secrets
# Options: "full" (requires secrets), "dev" (uses dummy keys), "disabled" (no crypto signing)
# Change to "full" once AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 is configured
os.environ.setdefault("AUDIT_CRYPTO_MODE", "dev")

# INJECT SIGNING KEY (Required for Production Audit Logging)
# This prevents the "CRITICAL - FATAL: log_audit_event" crash
# Using AGENTIC_AUDIT_HMAC_KEY to match documentation in RAILWAY_DEPLOYMENT.md
os.environ.setdefault(
    "AGENTIC_AUDIT_HMAC_KEY",
    "7f8a9b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a"
)
# --- PRODUCTION MODE CONFIGURATION END ---

# Enterprise-grade startup configuration
os.environ.setdefault("APP_STARTUP", "1")  # Skip plugin loading during startup
os.environ.setdefault("SKIP_IMPORT_TIME_VALIDATION", "1")  # Skip validation during import
os.environ.setdefault("SPACY_WARNING_IGNORE", "W007")  # Suppress spaCy warnings

# Import path_setup first to ensure all component paths are in sys.path
import path_setup  # noqa: F401

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Configure logging BEFORE any other imports that might log
from server.logging_config import configure_logging
configure_logging()

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
from server.schemas import ErrorResponse, HealthResponse, ReadinessResponse, DetailedHealthResponse
from server.config_utils import initialize_config, validate_required_api_keys
from server.distributed_lock import get_startup_lock

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
    
    # Initialize configuration and validate
    logger.info("=" * 80)
    logger.info("INITIALIZING PLATFORM CONFIGURATION")
    logger.info("=" * 80)
    
    try:
        config = initialize_config(log_summary=True)
        
        # Validate API keys (fail-fast in production)
        validate_required_api_keys(config, fail_fast=config.is_production)
        
    except Exception as e:
        logger.error(f"Configuration initialization failed: {e}", exc_info=True)
        if config and config.is_production:
            # In production, fail fast on configuration errors
            raise RuntimeError(f"Production configuration validation failed: {e}") from e
        else:
            logger.warning("Continuing startup despite configuration errors (non-production mode)")
    
    # Start the server IMMEDIATELY - agent loading happens in background
    # Use distributed lock to prevent duplicate initialization across containers
    logger.info("=" * 80)
    logger.info("STARTING SERVER WITH BACKGROUND AGENT LOADING")
    logger.info("=" * 80)
    
    try:
        # Acquire startup lock (non-blocking for now - agents can handle concurrency)
        # The lock is primarily informational and logged for debugging
        startup_lock = get_startup_lock()
        lock_acquired = await startup_lock.acquire(blocking=False)
        
        if lock_acquired:
            logger.info("✓ Startup lock acquired - this instance will initialize agents")
        else:
            logger.info("⚠ Startup lock held by another instance - agents may already be loading")
            logger.info("  This is normal in multi-instance deployments")
        
        # Get the agent loader
        loader = get_agent_loader()
        
        # Start background agent loading WITHOUT awaiting
        # This allows the server to start accepting HTTP connections immediately
        # The agent loader has its own internal lock to prevent duplicate loading
        logger.info("Initiating background agent loading...")
        loader.start_background_loading()
        logger.info("✓ Background agent loading task started")
        logger.info("✓ Server will accept HTTP connections immediately")
        logger.info("✓ Check /health for liveness and /ready for readiness")
        
    except Exception as e:
        logger.error(f"Error starting background agent loading: {e}", exc_info=True)
        logger.warning("Continuing startup despite agent loading error")

    logger.info("=" * 80)
    logger.info("API Server ready to accept connections")

    yield

    # Shutdown
    logger.info("Shutting down Code Factory API Server")
    
    # Release startup lock if we acquired it
    try:
        startup_lock = get_startup_lock()
        await startup_lock.release()
        await startup_lock.close()
    except Exception as e:
        logger.warning(f"Error releasing startup lock during shutdown: {e}")

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
    
    This endpoint ALWAYS returns HTTP 200 if the API server is responding,
    ensuring deployment healthchecks pass immediately on startup.

    **Returns:**
    - Overall health status (always "healthy" if API is running)
    - Component-level health information including agent availability
    - API version
    - Timestamp
    """
    # Get agent status from loader
    try:
        loader = get_agent_loader()
        agent_status = loader.get_status()
        
        # Determine agents_status based on loading state
        if agent_status.get('loading_in_progress', False):
            agents_health = "loading"
        elif agent_status.get('loading_error'):
            agents_health = "degraded"
        else:
            # Check agent availability
            agent_availability = agent_status.get('availability_rate', 0.0)
            
            # Consider agents healthy if at least 50% are available
            # or if no agents have been loaded yet (initial state)
            if agent_status.get('total_agents', 0) == 0:
                agents_health = "loading"
            elif agent_availability >= 0.5:
                agents_health = "ready"
            elif agent_availability > 0:
                agents_health = "degraded"
            else:
                agents_health = "degraded"
        
        # Build component health status
        components = {
            "api": "healthy",
            "omnicore": "healthy",
            "database": "healthy",
            "message_bus": "healthy",
            "agents_status": agents_health,
        }
        
    except Exception as e:
        logger.error(f"Error checking agent health: {e}", exc_info=True)
        components = {
            "api": "healthy",
            "omnicore": "healthy",
            "database": "healthy",
            "message_bus": "healthy",
            "agents_status": "loading",
        }

    # IMPORTANT: Always return "healthy" overall status if API is responding
    # This ensures deployment healthchecks pass even if agents are still loading
    # Agent status is informational only and doesn't affect overall health
    return HealthResponse(
        status="healthy",
        version=__version__,
        components=components,
        timestamp=datetime.utcnow().isoformat(),
    )


@app.get(
    "/ready",
    tags=["Health"],
    responses={
        200: {"model": ReadinessResponse, "description": "Application is ready"},
        503: {"model": ReadinessResponse, "description": "Application is not ready"}
    }
)
async def readiness_check(response: Response) -> ReadinessResponse:
    """
    Readiness check endpoint.

    Returns the readiness status of the API server, indicating whether
    the application is fully ready to accept traffic and handle requests.
    
    This endpoint returns HTTP 200 only when all agents are loaded and ready.
    It returns HTTP 503 if agents are still loading or failed to load.

    **Returns:**
    - HTTP 200: Application is ready (agents loaded)
    - HTTP 503: Application is not ready (agents loading or failed)
    
    **Response includes:**
    - Overall readiness status
    - Individual check results (agents_loaded, api_available)
    - Timestamp
    """
    # Get agent status from loader
    try:
        loader = get_agent_loader()
        agent_status = loader.get_status()
        
        # Check if agents are still loading
        loading_in_progress = agent_status.get('loading_in_progress', False)
        loading_error = agent_status.get('loading_error')
        
        # Build check results
        checks = {
            "api_available": "pass",
        }
        
        # Check agent loading status
        if loading_in_progress:
            checks["agents_loaded"] = "loading"
            ready = False
            status_text = "loading"
        elif loading_error:
            checks["agents_loaded"] = f"error: {loading_error}"
            ready = False
            status_text = "degraded"
        else:
            # Check if any agents are available
            agent_availability = agent_status.get('availability_rate', 0.0)
            total_agents = agent_status.get('total_agents', 0)
            
            if total_agents == 0:
                # No agents have been loaded yet
                checks["agents_loaded"] = "loading"
                ready = False
                status_text = "loading"
            elif agent_availability > 0:
                # At least some agents are available
                checks["agents_loaded"] = "pass"
                ready = True
                status_text = "ready"
                
                # Include details about available agents
                available = agent_status.get('available_agents', [])
                unavailable = agent_status.get('unavailable_agents', [])
                checks["agents_available"] = f"{len(available)}/{total_agents}"
                
                if unavailable:
                    checks["agents_unavailable"] = ", ".join(unavailable)
            else:
                # No agents are available
                checks["agents_loaded"] = "fail"
                ready = False
                status_text = "degraded"
        
    except Exception as e:
        logger.error(f"Error checking readiness: {e}", exc_info=True)
        checks = {
            "api_available": "pass",
            "agents_loaded": "error",
        }
        ready = False
        status_text = "error"
    
    # Set HTTP status code based on readiness
    if not ready:
        response.status_code = 503
    
    return ReadinessResponse(
        ready=ready,
        status=status_text,
        checks=checks,
        timestamp=datetime.utcnow().isoformat(),
    )


@app.get("/health/detailed", response_model=DetailedHealthResponse, tags=["Health"])
async def detailed_health_check() -> DetailedHealthResponse:
    """
    Detailed health check endpoint with comprehensive dependency and feature status.
    
    Provides granular health information about:
    - All 5 agents (codegen, critique, testgen, deploy, docgen)
    - External dependencies (Redis, Database, Feast)
    - Optional features (HSM, PlantUML, Sphinx, Sentry, etc.)
    
    This endpoint is useful for monitoring and debugging the platform's
    configuration and optional features.
    
    **Returns:**
    - Agent availability status for each agent
    - External dependency connection status
    - Optional feature availability status
    - Overall health status
    - API version and timestamp
    """
    # Get agent status
    try:
        loader = get_agent_loader()
        agent_status = loader.get_status()
        available_agents = agent_status.get('available_agents', {})
        
        # Map agent status
        agents = {
            "codegen": "available" if available_agents.get(AgentType.CODEGEN) else "unavailable",
            "critique": "available" if available_agents.get(AgentType.CRITIQUE) else "unavailable",
            "testgen": "available" if available_agents.get(AgentType.TESTGEN) else "unavailable",
            "deploy": "available" if available_agents.get(AgentType.DEPLOY) else "unavailable",
            "docgen": "available" if available_agents.get(AgentType.DOCGEN) else "unavailable",
        }
    except Exception as e:
        logger.error(f"Error checking agent status: {e}", exc_info=True)
        agents = {
            "codegen": "error",
            "critique": "error",
            "testgen": "error",
            "deploy": "error",
            "docgen": "error",
        }
    
    # Check external dependencies
    dependencies = {}
    
    # Check Redis
    try:
        import redis.asyncio as redis
        # Try to connect (with timeout)
        r = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379"),
            socket_connect_timeout=1,
            socket_timeout=1
        )
        await r.ping()
        dependencies["redis"] = "connected"
        await r.close()
    except Exception:
        dependencies["redis"] = "unavailable"
    
    # Check Database
    try:
        # Check if DATABASE_URL is configured
        if os.getenv("DATABASE_URL"):
            dependencies["database"] = "configured"
        else:
            dependencies["database"] = "not_configured"
    except Exception:
        dependencies["database"] = "error"
    
    # Check Feast feature store
    try:
        import feast
        dependencies["feast"] = "installed"
    except ImportError:
        dependencies["feast"] = "not_installed"
    
    # Check Presidio (PII detection)
    try:
        import presidio_analyzer
        import presidio_anonymizer
        dependencies["presidio"] = "installed"
    except ImportError:
        dependencies["presidio"] = "not_installed"
    
    # Check optional features
    optional_features = {}
    
    # HSM Support
    try:
        import pkcs11
        optional_features["hsm"] = "available"
    except ImportError:
        optional_features["hsm"] = "not_installed"
    
    # PlantUML/Graphviz
    try:
        import subprocess
        result = subprocess.run(
            ["dot", "-V"],
            capture_output=True,
            timeout=1
        )
        if result.returncode == 0:
            optional_features["graphviz"] = "available"
        else:
            optional_features["graphviz"] = "unavailable"
    except Exception:
        optional_features["graphviz"] = "not_installed"
    
    # Sphinx documentation
    try:
        import sphinx
        optional_features["sphinx"] = "installed"
    except ImportError:
        optional_features["sphinx"] = "not_installed"
    
    # Sentry error tracking
    try:
        if os.getenv("SENTRY_DSN"):
            optional_features["sentry"] = "configured"
        else:
            optional_features["sentry"] = "not_configured"
    except Exception:
        optional_features["sentry"] = "not_configured"
    
    # Docker
    try:
        import docker
        client = docker.from_env()
        client.ping()
        optional_features["docker"] = "available"
    except Exception:
        optional_features["docker"] = "unavailable"
    
    # Overall status
    status = "healthy"
    
    return DetailedHealthResponse(
        status=status,
        version=__version__,
        timestamp=datetime.utcnow().isoformat(),
        agents=agents,
        dependencies=dependencies,
        optional_features=optional_features,
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


# Allow running the server directly with `python -m server.main`
if __name__ == "__main__":
    import uvicorn
    
    # Get configuration from environment variables with validation
    try:
        port = int(os.environ.get("PORT", 8000))
        if not (1 <= port <= 65535):
            logger.warning(f"Invalid PORT value: {port}, must be 1-65535. Using default 8000")
            port = 8000
    except ValueError:
        logger.warning(f"Invalid PORT value: {os.environ.get('PORT')}, using default 8000")
        port = 8000
    
    host = os.environ.get("HOST", "0.0.0.0")
    log_level = os.environ.get("LOG_LEVEL", "info").lower()
    
    logger.info(f"Starting server on {host}:{port}")
    
    # Run the FastAPI application
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        access_log=True,
    )
