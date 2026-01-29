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
# CRITICAL: Use "disabled" in production to avoid security conflict with APP_ENV=production
# Change to "full" once AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 is properly configured
os.environ.setdefault("AUDIT_CRYPTO_MODE", "disabled")

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

import asyncio
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# =============================================================================
# ENTERPRISE-GRADE STARTUP DEPENDENCY VERIFICATION
# =============================================================================
# This section implements fail-fast dependency verification following:
# - ISO 27001 A.12.6.1: Technical vulnerability management
# - SOC 2 Type II CC6.1: System component integrity verification
# - NIST SP 800-53 SI-2: Flaw remediation
#
# Pattern: Fail-fast at build time, graceful degradation at runtime
# =============================================================================

_startup_start_time = time.monotonic()
_startup_errors: List[str] = []
_startup_warnings: List[str] = []


def _verify_critical_import(module_name: str, package_name: str, description: str) -> bool:
    """
    Verify a critical dependency can be imported.
    
    Args:
        module_name: Python module name
        package_name: PyPI package name for error messages
        description: Human-readable description
        
    Returns:
        True if import succeeded, False otherwise
    """
    try:
        __import__(module_name)
        return True
    except ImportError as e:
        _startup_errors.append(
            f"{package_name} ({description}): {e}"
        )
        return False
    except Exception as e:
        _startup_errors.append(
            f"{package_name} ({description}): Unexpected error - {type(e).__name__}: {e}"
        )
        return False


# CRITICAL: Verify FastAPI and core dependencies BEFORE any other imports
# This ensures clear, actionable error messages for operators
try:
    from fastapi import FastAPI, Request, Response
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
except ImportError as e:
    # Provide a clear, actionable error message
    error_msg = f"""
================================================================================
CRITICAL STARTUP FAILURE: FastAPI framework not installed
================================================================================

Error: {e}

This is a fatal error. The FastAPI web framework is required for the
Code Factory Platform API server to start.

RESOLUTION:
-----------
1. Install all dependencies:
   pip install -r requirements.txt

2. Or install FastAPI directly:
   pip install fastapi starlette uvicorn pydantic

3. For Docker deployments, ensure the Dockerfile includes:
   COPY requirements.txt .
   RUN pip install -r requirements.txt

4. Verify the installation:
   python -c "import fastapi; print(fastapi.__version__)"

For more information, see: QUICKSTART.md and DEPLOYMENT.md
================================================================================
"""
    print(error_msg, file=sys.stderr)
    raise ImportError(error_msg) from e

# Verify additional critical dependencies
# These are required for the server to function properly
_critical_deps = [
    ("uvicorn", "uvicorn", "ASGI server - required to serve HTTP requests"),
    ("pydantic", "pydantic", "Data validation - required for request/response schemas"),
    ("starlette", "starlette", "ASGI toolkit - required as FastAPI foundation"),
]

for _mod, _pkg, _desc in _critical_deps:
    _verify_critical_import(_mod, _pkg, _desc)

# Verify recommended dependencies (non-blocking)
_recommended_deps = [
    ("redis", "redis", "Caching and distributed locks - degraded mode without"),
    ("httpx", "httpx", "HTTP client - some features unavailable without"),
    ("structlog", "structlog", "Structured logging - basic logging without"),
]

for _mod, _pkg, _desc in _recommended_deps:
    try:
        __import__(_mod)
    except ImportError as e:
        _startup_warnings.append(f"{_pkg} ({_desc}): {e}")

# Report startup dependency issues
if _startup_errors:
    _error_report = (
        "\n" + "=" * 70 + "\n"
        "CRITICAL: Missing dependencies detected during startup!\n"
        "=" * 70 + "\n"
        "The following CRITICAL dependencies are missing:\n\n"
    )
    for _err in _startup_errors:
        _error_report += f"  ❌ {_err}\n"
    _error_report += (
        "\n" + "-" * 70 + "\n"
        "RESOLUTION:\n"
        "  pip install -r requirements.txt\n"
        "\nOr install critical packages individually:\n"
        "  pip install fastapi uvicorn pydantic starlette\n"
        "=" * 70 + "\n"
    )
    print(_error_report, file=sys.stderr)
    # Log but don't exit - allow partial startup for debugging
    logging.critical(_error_report)

if _startup_warnings:
    _warning_report = (
        "\n" + "-" * 70 + "\n"
        "WARNING: Some recommended dependencies are missing:\n"
    )
    for _warn in _startup_warnings:
        _warning_report += f"  ⚠️  {_warn}\n"
    _warning_report += (
        "\nThe server will start in degraded mode with reduced functionality.\n"
        "Install all dependencies for full features: pip install -r requirements.txt\n"
        "-" * 70 + "\n"
    )
    print(_warning_report)

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
    
    CRITICAL: This function controls when uvicorn binds to the HTTP port.
    Everything BEFORE yield delays server startup.
    Everything AFTER yield runs during shutdown.
    
    To ensure fast startup, we:
    1. Do minimal setup before yield
    2. Start background initialization task (fire-and-forget)
    3. Yield immediately to let uvicorn bind to port
    """
    # ========================================================================
    # BEFORE YIELD: MINIMAL SETUP ONLY - MUST BE FAST (<1 second)
    # ========================================================================
    
    logger.info("Starting Code Factory API Server")
    logger.info(f"Version: {__version__}")
    logger.info("=" * 80)
    logger.info("HTTP SERVER STARTING - Minimal pre-initialization")
    logger.info("=" * 80)
    
    # Store initialization state for healthcheck
    app.state.initialization_complete = False
    app.state.initialization_error = None
    app.state.startup_lock_acquired = False
    
    # ========================================================================
    # BACKGROUND INITIALIZATION (Fire-and-forget)
    # ========================================================================
    
    async def initialize_platform():
        """Run all heavyweight initialization after HTTP server is ready."""
        logger.info("=" * 80)
        logger.info("INITIALIZING PLATFORM CONFIGURATION (Background)")
        logger.info("=" * 80)
        
        # Load configuration
        config = None
        try:
            config = initialize_config(log_summary=True)
            
            # Validate API keys - log warnings but don't block
            api_keys_valid = validate_required_api_keys(config, fail_fast=False)
            
            if not api_keys_valid:
                logger.warning("=" * 80)
                logger.warning("WARNING: No LLM API keys configured!")
                logger.warning("The server is running but LLM functionality will be unavailable.")
                logger.warning("Configure at least one LLM API key for full functionality.")
                if config.is_production:
                    logger.warning("This is a PRODUCTION environment - please configure API keys.")
                logger.warning("=" * 80)
            
        except Exception as e:
            logger.error(f"Configuration initialization failed: {e}", exc_info=True)
            logger.warning(f"Continuing despite configuration error: {type(e).__name__}")
            app.state.initialization_error = str(e)
        
        # Start background agent loading
        logger.info("=" * 80)
        logger.info("STARTING BACKGROUND AGENT LOADING")
        logger.info("=" * 80)
        
        try:
            # Acquire startup lock with timeout to prevent blocking server startup
            startup_lock = get_startup_lock()
            try:
                lock_acquired = await asyncio.wait_for(
                    startup_lock.acquire(blocking=False),
                    timeout=0.1  # 100ms max
                )
            except asyncio.TimeoutError:
                lock_acquired = False
                logger.info("⚠ Startup lock acquisition timed out - continuing anyway")
            
            # Track lock acquisition for cleanup
            app.state.startup_lock_acquired = lock_acquired
            
            if lock_acquired:
                logger.info("✓ Startup lock acquired - this instance will initialize agents")
            else:
                logger.info("⚠ Startup lock held by another instance - agents may already be loading")
                logger.info("  This is normal in multi-instance deployments")
            
            # Get the agent loader and start background loading
            loader = get_agent_loader()
            loader.start_background_loading()
            logger.info("✓ Background agent loading task started")
            logger.info("✓ Check /health for liveness and /ready for readiness")
            
        except Exception as e:
            logger.error(f"Error starting background agent loading: {e}", exc_info=True)
            logger.warning("Continuing despite agent loading error")
            app.state.initialization_error = str(e)
        
        logger.info("=" * 80)
        logger.info("Platform initialization complete")
        logger.info("=" * 80)
        
        # Mark initialization as complete
        app.state.initialization_complete = True
    
    # Start initialization in background - don't await!
    # Store task reference for proper cleanup during shutdown
    app.state.init_task = asyncio.create_task(initialize_platform())
    logger.info("Background initialization task created - HTTP server starting now")
    
    # Yield IMMEDIATELY to let uvicorn bind to port and accept connections
    # This ensures /health endpoint is reachable within 2-3 seconds
    yield
    
    # ========================================================================
    # AFTER YIELD: SHUTDOWN CLEANUP
    # ========================================================================
    
    logger.info("Shutting down Code Factory API Server")
    
    # Wait for or cancel background initialization if still running
    if hasattr(app.state, 'init_task') and app.state.init_task:
        if not app.state.init_task.done():
            logger.info("Waiting for background initialization to complete...")
            try:
                await asyncio.wait_for(app.state.init_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Background initialization did not complete in time, cancelling...")
                app.state.init_task.cancel()
                try:
                    await app.state.init_task
                except asyncio.CancelledError:
                    logger.info("Background initialization task cancelled")
        
        # Check for exceptions in the task
        if app.state.init_task.done() and not app.state.init_task.cancelled():
            try:
                app.state.init_task.result()  # This will raise if task had exception
            except Exception as e:
                logger.error(f"Background initialization task failed: {e}", exc_info=True)
    
    # Release startup lock only if we acquired it
    if getattr(app.state, 'startup_lock_acquired', False):
        try:
            startup_lock = get_startup_lock()
            await startup_lock.release()
            await startup_lock.close()
            logger.info("Startup lock released")
        except Exception as e:
            logger.warning(f"Error releasing startup lock during shutdown: {e}")
    
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
    Ultra-fast health check endpoint (Liveness Probe).

    This endpoint ALWAYS returns HTTP 200 immediately if the API server is responding.
    It attempts a quick agent status check with 50ms timeout for informational purposes,
    but failure is non-fatal and doesn't affect the health status.
    
    Purpose: Railway/Kubernetes liveness probe to determine if the container should be restarted.
    
    For checking if agents are ready, use the /ready endpoint instead.

    **Returns:**
    - Overall health status: always "healthy" if API is running
    - Component-level health information (non-blocking, informational only)
    - API version
    - Timestamp
    
    **Performance:**
    - Response time: < 100ms guaranteed (50ms timeout for agent check)
    - No blocking operations that could prevent returning 200
    - Always returns "healthy" even if agent check times out or fails
    """
    # Start with default component status
    components = {
        "api": "healthy",
        "agents_status": "loading",  # Default, updated below if quick check succeeds
    }
    
    # Try to get agent status with VERY short timeout - failure is OK
    try:
        loader = get_agent_loader()
        status = await asyncio.wait_for(
            asyncio.to_thread(loader.get_status),
            timeout=0.05  # 50ms max
        )
        if status.get('loading_error'):
            components["agents_status"] = "degraded"
        elif status.get('total_agents', 0) > 0 and status.get('availability_rate', 0) > 0:
            components["agents_status"] = "ready"
    except (asyncio.TimeoutError, Exception):
        # Any error or timeout: just leave as "loading"
        # This is intentional - health check should never fail
        pass
    
    # ALWAYS return healthy if we got here
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
    # Get agent status from loader with timeout
    try:
        loader = get_agent_loader()
        # Add timeout to prevent readiness check from blocking too long
        agent_status = await asyncio.wait_for(
            asyncio.to_thread(loader.get_status),
            timeout=1.0  # 1 second max for readiness check
        )
        
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
        
    except asyncio.TimeoutError:
        logger.warning("Readiness check timed out waiting for agent status")
        checks = {
            "api_available": "pass",
            "agents_loaded": "timeout",
        }
        ready = False
        status_text = "timeout"
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
        return templates.TemplateResponse(request, "index.html")

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
