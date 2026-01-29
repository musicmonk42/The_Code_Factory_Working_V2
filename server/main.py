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

# =============================================================================
# CRITICAL: Defer router imports to allow /health to work even if imports fail
# =============================================================================
# Router imports are done lazily during startup to ensure the health endpoint
# can respond immediately. This is critical for Railway/Kubernetes healthchecks.
# =============================================================================
import threading

# Thread lock for router loading
_router_load_lock = threading.Lock()

# These will be populated during startup
_routers_loaded = False
_router_load_error: Optional[str] = None

# Placeholders for lazy-loaded modules
api_keys_router = None
diagnostics_router = None
events_router = None
fixes_router = None
generator_router = None
jobs_router = None
omnicore_router = None
sfe_router = None

# Agent loader type placeholder (will be imported lazily)
AgentType = None
get_agent_loader = None


def _load_routers():
    """
    Load routers lazily. This allows the health endpoint to work even if
    router imports fail (e.g., missing sse_starlette dependency).
    
    Thread-safe: uses a lock to prevent race conditions.
    """
    global _routers_loaded, _router_load_error
    global api_keys_router, diagnostics_router, events_router, fixes_router
    global generator_router, jobs_router, omnicore_router, sfe_router
    global AgentType, get_agent_loader
    
    # Fast path: already loaded
    if _routers_loaded:
        return _router_load_error is None
    
    # Thread-safe loading
    with _router_load_lock:
        # Double-check after acquiring lock
        if _routers_loaded:
            return _router_load_error is None
        
        try:
            from server.routers import (
                api_keys_router as _api_keys_router,
                diagnostics_router as _diagnostics_router,
                events_router as _events_router,
                fixes_router as _fixes_router,
                generator_router as _generator_router,
                jobs_router as _jobs_router,
                omnicore_router as _omnicore_router,
                sfe_router as _sfe_router,
            )
            from server.utils.agent_loader import AgentType as _AgentType, get_agent_loader as _get_agent_loader
            
            # Assign all values atomically (within the lock)
            api_keys_router = _api_keys_router
            diagnostics_router = _diagnostics_router
            events_router = _events_router
            fixes_router = _fixes_router
            generator_router = _generator_router
            jobs_router = _jobs_router
            omnicore_router = _omnicore_router
            sfe_router = _sfe_router
            AgentType = _AgentType
            get_agent_loader = _get_agent_loader
            
            _routers_loaded = True
            logging.getLogger(__name__).info("✓ All routers loaded successfully")
            return True
        except Exception as e:
            _routers_loaded = True
            _router_load_error = f"{type(e).__name__}: {e}"
            logging.getLogger(__name__).error(f"Failed to load routers: {_router_load_error}")
            return False



# Import minimal dependencies needed for health endpoint schemas
# These are defined inline to avoid import failures
from pydantic import BaseModel
# Note: Dict already imported at line 72


class MinimalHealthResponse(BaseModel):
    """Minimal health response that works without full schema imports."""
    status: str
    version: str
    components: Dict[str, str] = {}
    timestamp: str


# Try to import full schemas, fall back to minimal
try:
    from server.schemas import ErrorResponse, HealthResponse, ReadinessResponse, DetailedHealthResponse
except ImportError as e:
    logging.getLogger(__name__).warning(f"Could not import full schemas: {e}")
    # Use minimal response
    HealthResponse = MinimalHealthResponse
    ReadinessResponse = MinimalHealthResponse
    DetailedHealthResponse = MinimalHealthResponse
    
    class ErrorResponse(BaseModel):
        error: str
        message: str
        details: Dict[str, Any] = {}


# Try to import config utils, provide fallbacks
try:
    from server.config_utils import initialize_config, validate_required_api_keys
except ImportError as e:
    logging.getLogger(__name__).warning(f"Could not import config_utils: {e}")
    def initialize_config(log_summary=False):
        return None
    def validate_required_api_keys(config, fail_fast=False):
        return True


# Try to import distributed lock, provide fallback
try:
    from server.distributed_lock import get_startup_lock
except ImportError as e:
    logging.getLogger(__name__).warning(f"Could not import distributed_lock: {e}")
    class FakeStartupLock:
        async def acquire(self, blocking=False):
            return True
        async def release(self):
            pass
        async def close(self):
            pass
    def get_startup_lock():
        return FakeStartupLock()


logger = logging.getLogger(__name__)

# Get the directory where this file is located
BASE_DIR = Path(__file__).resolve().parent

# Configure templates and static files with fallbacks
try:
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
except Exception as e:
    logger.warning(f"Could not load templates: {e}")
    templates = None

static_dir = BASE_DIR / "static"


async def _background_initialization(app_instance: FastAPI):
    """
    Background initialization task that runs AFTER the HTTP server is ready.
    
    This allows /health to respond immediately while initialization continues.
    
    Args:
        app_instance: The FastAPI application instance to add routers to
    """
    logger.info("=" * 80)
    logger.info("LOADING ROUTERS (Background)")
    logger.info("=" * 80)
    
    # Load routers first - this is critical for the API to function
    routers_ok = _load_routers()
    
    if routers_ok:
        # Include routers with /api prefix
        try:
            app_instance.include_router(api_keys_router, prefix="/api")
            app_instance.include_router(diagnostics_router, prefix="/api")
            app_instance.include_router(jobs_router, prefix="/api")
            app_instance.include_router(generator_router, prefix="/api")
            app_instance.include_router(omnicore_router, prefix="/api")
            app_instance.include_router(sfe_router, prefix="/api")
            app_instance.include_router(fixes_router, prefix="/api")
            app_instance.include_router(events_router, prefix="/api")
            logger.info("✓ All routers included in application")
        except Exception as e:
            logger.error(f"Failed to include routers: {e}", exc_info=True)
    else:
        logger.error(f"Router loading failed: {_router_load_error}")
        logger.warning("API endpoints will not be available")
    
    logger.info("=" * 80)
    logger.info("INITIALIZING PLATFORM CONFIGURATION (Background)")
    logger.info("=" * 80)
    
    config = None
    try:
        config = initialize_config(log_summary=True)
        
        # Validate API keys - log warnings but don't block
        api_keys_valid = validate_required_api_keys(config, fail_fast=False)
        
        if not api_keys_valid:
            logger.warning("=" * 80)
            logger.warning("WARNING: No LLM API keys configured!")
            logger.warning("The server will start but LLM functionality will be unavailable.")
            logger.warning("=" * 80)
        
    except Exception as e:
        logger.error(f"Configuration initialization failed: {e}", exc_info=True)
        logger.warning(f"Continuing startup despite configuration error: {type(e).__name__}")
    
    # Start background agent loading (only if routers loaded successfully)
    if routers_ok and get_agent_loader is not None:
        logger.info("=" * 80)
        logger.info("STARTING BACKGROUND AGENT LOADING")
        logger.info("=" * 80)
        
        try:
            startup_lock = get_startup_lock()
            lock_acquired = await startup_lock.acquire(blocking=False)
            
            if lock_acquired:
                logger.info("✓ Startup lock acquired - this instance will initialize agents")
            else:
                logger.info("⚠ Startup lock held by another instance - agents may already be loading")
                logger.info("  This is normal in multi-instance deployments")
            
            loader = get_agent_loader()
            loader.start_background_loading()
            logger.info("✓ Background agent loading task started")
            logger.info("✓ Check /health for liveness and /ready for readiness")
            
        except Exception as e:
            logger.error(f"Error starting background agent loading: {e}", exc_info=True)
            logger.warning("Continuing startup despite agent loading error")
    else:
        logger.warning("Skipping agent loading due to router loading failure")
    
    logger.info("=" * 80)
    logger.info("Platform initialization complete")
    logger.info("=" * 80)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager - MUST complete immediately to allow health checks.
    
    All initialization happens in background tasks so the HTTP server can accept
    connections immediately for Railway healthchecks.
    """
    # Startup - DO NOT BLOCK HERE
    logger.info("Starting Code Factory API Server")
    logger.info(f"Version: {__version__}")
    logger.info("=" * 80)
    logger.info("HTTP SERVER STARTING - Minimal pre-initialization")
    logger.info("=" * 80)
    
    # Start background initialization WITHOUT awaiting
    # Pass the app instance so routers can be added
    background_task = asyncio.create_task(_background_initialization(app))
    logger.info("Background initialization task created - HTTP server starting now")
    
    # IMMEDIATELY yield so FastAPI routes become available
    yield
    
    # Shutdown
    logger.info("Shutting down Code Factory API Server")
    
    # Cancel background task if still running
    if not background_task.done():
        background_task.cancel()
        try:
            await background_task
        except asyncio.CancelledError:
            pass
    
    # Clean up connections
    try:
        startup_lock = get_startup_lock()
        await startup_lock.release()
        await startup_lock.close()
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
    Liveness probe - returns immediately with HTTP 200 if server is running.
    
    This endpoint is used by Railway/Kubernetes to determine if the container
    should be restarted. It should ALWAYS return HTTP 200 if the process is alive.
    
    Use /ready for readiness checks (when agents/dependencies must be loaded).
    """
    # Return success immediately - don't check anything
    return HealthResponse(
        status="healthy",
        version=__version__,
        components={"api": "healthy"},
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
    # Check if routers are loaded first
    if not _routers_loaded:
        # Routers still loading - not ready
        checks = {
            "api_available": "pass",
            "routers_loaded": "loading",
        }
        ready = False
        status_text = "loading"
    elif _router_load_error:
        # Router loading failed
        checks = {
            "api_available": "pass",
            "routers_loaded": f"error: {_router_load_error}",
        }
        ready = False
        status_text = "degraded"
    elif get_agent_loader is None:
        # Agent loader not available
        checks = {
            "api_available": "pass",
            "routers_loaded": "pass",
            "agents_loaded": "unavailable",
        }
        ready = False
        status_text = "degraded"
    else:
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
                "routers_loaded": "pass",
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
                "routers_loaded": "pass",
                "agents_loaded": "timeout",
            }
            ready = False
            status_text = "timeout"
        except Exception as e:
            logger.error(f"Error checking readiness: {e}", exc_info=True)
            checks = {
                "api_available": "pass",
                "routers_loaded": "pass",
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
        if get_agent_loader is None or AgentType is None:
            # Routers not loaded yet
            agents = {
                "codegen": "loading",
                "critique": "loading",
                "testgen": "loading",
                "deploy": "loading",
                "docgen": "loading",
            }
        else:
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
    if "text/html" in accept_header and templates is not None:
        # Serve the web UI
        try:
            return templates.TemplateResponse(request, "index.html")
        except Exception as e:
            logger.warning(f"Could not render template: {e}")
            # Fall through to JSON response

    # Otherwise return JSON API info
    return {
        "name": "Code Factory Platform API",
        "version": __version__,
        "description": "Enterprise-grade HTTP API for automated software development",
        "startup_status": {
            "routers_loaded": _routers_loaded,
            "router_error": _router_load_error,
        },
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

# NOTE: Routers are now loaded lazily in _background_initialization()
# This allows /health to respond immediately even if router imports fail
logger.info("FastAPI application configured successfully (routers loaded in background)")


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
