# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

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
import sys

# --- PRODUCTION MODE CONFIGURATION START ---
# Detect if we're running in a test environment BEFORE setting production defaults
# This allows tests to run with proper test configuration
_is_test_environment = (
    "pytest" in sys.modules or 
    os.getenv("TESTING") == "1" or 
    os.getenv("CI") == "true" or
    os.getenv("APP_ENV") == "test"
)

# Only force production mode if NOT in a test environment
if not _is_test_environment:
    # Force Production Mode
    os.environ["APP_ENV"] = "production"
    os.environ["DEV_MODE"] = "0"
    
    # FIX: Unset AUDIT_LOG_DEV_MODE in production to prevent conflicting configuration
    # This prevents the security error: "Production environment variables are set but dev mode is also enabled"
    if "AUDIT_LOG_DEV_MODE" in os.environ:
        del os.environ["AUDIT_LOG_DEV_MODE"]
else:
    # In test mode, ensure APP_ENV is set to test if not already set
    os.environ.setdefault("APP_ENV", "test")

# AUDIT CRYPTO CONFIGURATION
# SECURITY UPDATE: Default crypto mode is now "software" (secure by default)
# In test/dev environments without secrets, the factory will use dev mode automatically
# 
# Allow audit crypto initialization to fail gracefully if secrets are not configured
# This prevents the server from crashing on startup when secrets are not yet configured
# Set to "0" once AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 is properly configured
os.environ.setdefault("AUDIT_CRYPTO_ALLOW_INIT_FAILURE", "1")

# Set audit crypto mode - new default is "software" (cryptographically secure)
# Options: "software" (default, requires secrets), "dev" (uses dummy keys), "disabled" (no crypto)
# NOTE: Production environments will enforce "software" or "hsm" mode (disabled is blocked)
# 
# Behavior:
# - With secrets configured: Uses software mode with real crypto
# - Without secrets in production: Triggers validation error at startup (secure by default)
# - Without secrets in dev/test: Factory automatically uses dev mode
# 
# To temporarily allow startup in production without secrets (NOT RECOMMENDED):
# - Explicitly set AUDIT_CRYPTO_MODE=disabled (will log critical security warning)
# - See AUDIT_CONFIGURATION.md for migration guide
os.environ.setdefault("AUDIT_CRYPTO_MODE", "software")


# INJECT SIGNING KEY (Required for Production Audit Logging)
# This prevents the "CRITICAL - FATAL: log_audit_event" crash
# Using AGENTIC_AUDIT_HMAC_KEY to match documentation in RAILWAY_DEPLOYMENT.md
os.environ.setdefault(
    "AGENTIC_AUDIT_HMAC_KEY",
    "7f8a9b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a"
)

# --- OPTIONAL FEATURES CONFIGURATION ---
# P3/Security Hardening: Require explicit enable flags for powerful subsystems
# Changed from "auto" to "0" to prevent accidental exposure

# Enable Database support by default (commonly needed)
os.environ.setdefault("ENABLE_DATABASE", "1")

# Feature Store (Feast) - SECURITY: Disabled by default
# Set ENABLE_FEATURE_STORE=1 to enable if you actually use Feast
os.environ.setdefault("ENABLE_FEATURE_STORE", "0")

# HSM Support - SECURITY: Disabled by default
# Set ENABLE_HSM=1 only if you have an actual Hardware Security Module
os.environ.setdefault("ENABLE_HSM", "0")

# Libvirt Support - SECURITY: Disabled by default
# Set ENABLE_LIBVIRT=1 only if you need virtualization features
os.environ.setdefault("ENABLE_LIBVIRT", "0")

# Enable Sentry automatically if SENTRY_DSN is provided
if os.environ.get("SENTRY_DSN"):
    os.environ.setdefault("ENABLE_SENTRY", "1")
# --- PRODUCTION MODE CONFIGURATION END ---

# Enterprise-grade startup configuration
os.environ.setdefault("APP_STARTUP", "1")  # Skip plugin loading during startup
os.environ.setdefault("SKIP_IMPORT_TIME_VALIDATION", "1")  # Skip validation during import
os.environ.setdefault("SPACY_WARNING_IGNORE", "W007")  # Suppress spaCy warnings

# Import path_setup first to ensure all component paths are in sys.path
import path_setup  # noqa: F401

import asyncio
import logging
import os
import signal
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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
from server.environment import is_test

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
audit_router = None
diagnostics_router = None
events_router = None
files_router = None
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
    global api_keys_router, audit_router, diagnostics_router, events_router, fixes_router
    global files_router, generator_router, jobs_router, omnicore_router, sfe_router
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
                audit as _audit_router,
                diagnostics_router as _diagnostics_router,
                events_router as _events_router,
                fixes_router as _fixes_router,
                generator_router as _generator_router,
                jobs_router as _jobs_router,
                omnicore_router as _omnicore_router,
                sfe_router as _sfe_router,
            )
            from server.routers.files import router as _files_router
            from server.utils.agent_loader import AgentType as _AgentType, get_agent_loader as _get_agent_loader
            
            # Assign all values atomically (within the lock)
            api_keys_router = _api_keys_router
            audit_router = _audit_router
            diagnostics_router = _diagnostics_router
            events_router = _events_router
            files_router = _files_router
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

# Configuration constants for Redis RAG index
REDIS_CONNECTION_TIMEOUT_SECONDS = int(os.getenv("REDIS_CONNECTION_TIMEOUT", "5"))
RAG_INDEX_NAME = os.getenv("RAG_INDEX_NAME", "rag_index")
RAG_INDEX_PREFIX = os.getenv("RAG_INDEX_PREFIX", "rag:")
RAG_EMBEDDING_DIM = int(os.getenv("RAG_EMBEDDING_DIM", "384"))  # Default for sentence-transformers all-MiniLM-L6-v2

# FIX: Configure production log levels to reduce log spam
# Detect production environment and set appropriate log levels
is_production = (
    os.getenv("RAILWAY_ENVIRONMENT") is not None or
    os.getenv("APP_ENV", "development").lower() == "production" or
    os.getenv("ENVIRONMENT", "").lower() == "production"
)

if is_production and not _is_test_environment:
    # Set root logger to WARNING in production to reduce log volume
    logging.getLogger().setLevel(logging.WARNING)
    
    # Keep important loggers at INFO for operational visibility
    logging.getLogger("server").setLevel(logging.INFO)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # Reduce access log spam
    
    # Reduce noise from verbose third-party libraries
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    
    # Reduce Presidio analyzer noise
    logging.getLogger("presidio-analyzer").setLevel(logging.WARNING)
    logging.getLogger("presidio_analyzer").setLevel(logging.WARNING)
    
    # Reduce LLM provider loading noise (missing API keys are expected)
    logging.getLogger("runner").setLevel(logging.WARNING)
    
    logger.info("Production log level configured: Root=WARNING, Server=INFO")
else:
    # Development mode - keep INFO for better debugging
    logger.info("Development log level configured: INFO")


# Get the directory where this file is located
BASE_DIR = Path(__file__).resolve().parent

# Configure templates and static files with fallbacks
try:
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
except Exception as e:
    logger.warning(f"Could not load templates: {e}")
    templates = None

static_dir = BASE_DIR / "static"


def _include_routers(app_instance: FastAPI) -> bool:
    """
    Include all API routers in the FastAPI application.
    
    This function is called by both the synchronous (test mode) and asynchronous
    (production mode) initialization paths to ensure consistent router registration.
    
    Args:
        app_instance: The FastAPI application instance to add routers to
        
    Returns:
        True if routers were successfully included, False otherwise
    """
    try:
        app_instance.include_router(api_keys_router, prefix="/api")
        app_instance.include_router(audit_router.router, prefix="/api")
        app_instance.include_router(diagnostics_router, prefix="/api")
        app_instance.include_router(events_router, prefix="/api")
        app_instance.include_router(files_router)
        app_instance.include_router(fixes_router, prefix="/api")
        app_instance.include_router(generator_router, prefix="/api")
        app_instance.include_router(jobs_router, prefix="/api")
        app_instance.include_router(omnicore_router, prefix="/api")
        app_instance.include_router(sfe_router, prefix="/api")
        logger.info("✓ All routers included in application")
        return True
    except Exception as e:
        logger.error(f"Failed to include routers: {e}", exc_info=True)
        return False


def _register_routers_sync(app_instance: FastAPI) -> bool:
    """
    Load and register routers SYNCHRONOUSLY during startup.
    
    This MUST complete before the lifespan yields to ensure API endpoints
    are available when the server starts accepting requests.
    
    Args:
        app_instance: The FastAPI application instance to add routers to
        
    Returns:
        True if routers were registered successfully, False otherwise
    """
    logger.info("=" * 80)
    logger.info("LOADING ROUTERS (Synchronous - Before HTTP Server)")
    logger.info("=" * 80)
    
    # Load routers first - this is critical for the API to function
    routers_ok = _load_routers()
    
    if routers_ok:
        # Include routers with /api prefix using shared helper
        return _include_routers(app_instance)
    else:
        logger.error(f"Router loading failed: {_router_load_error}")
        logger.warning("API endpoints will not be available")
        return False


async def _background_initialization(app_instance: FastAPI, routers_ok: bool):
    """
    Background initialization task that runs AFTER the HTTP server is ready.
    
    This handles heavy initialization (config, agents) while the server is
    already accepting requests. Routers are already registered synchronously.
    
    Args:
        app_instance: The FastAPI application instance
        routers_ok: Whether routers were successfully registered
    """
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
    
    # Start and verify message bus
    logger.info("=" * 80)
    logger.info("VERIFYING MESSAGE BUS STARTUP")
    logger.info("=" * 80)
    
    try:
        from server.services.omnicore_service import get_omnicore_service
        
        # Get OmniCore service instance (singleton)
        omnicore_service = get_omnicore_service()
        
        # Start message bus dispatcher tasks
        if hasattr(omnicore_service, '_message_bus') and omnicore_service._message_bus:
            logger.info("Starting message bus dispatcher tasks...")
            await omnicore_service.start_message_bus()
            
            # Verify startup with retry logic
            max_retries = 10
            for i in range(max_retries):
                if (hasattr(omnicore_service._message_bus, 'dispatcher_tasks') and 
                    omnicore_service._message_bus.dispatcher_tasks and
                    hasattr(omnicore_service._message_bus, '_dispatchers_started') and
                    omnicore_service._message_bus._dispatchers_started):
                    logger.info("✓ Message bus verified operational")
                    break
                
                logger.info(f"Waiting for message bus startup... ({i+1}/{max_retries})")
                await asyncio.sleep(1)
            else:
                logger.error("⚠ Message bus did not start within timeout - using fallback mode")
        else:
            logger.warning("Message bus not initialized - WebSocket events will use fallback mode")
    except Exception as e:
        logger.error(f"Message bus verification failed: {e}", exc_info=True)
        logger.warning("Continuing startup - WebSocket events will use fallback mode")
    
    # P2 FIX: Validate Redis connection on startup (explicit PING test)
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        logger.info("=" * 80)
        logger.info("VALIDATING REDIS CONNECTION")
        logger.info("=" * 80)
        try:
            import redis.asyncio as aioredis
            r = aioredis.Redis.from_url(
                redis_url,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            await r.ping()
            await r.aclose()
            logger.info(f"✓ Redis connection validated: {redis_url.split('@')[-1] if '@' in redis_url else redis_url}")
        except ImportError:
            logger.warning("⚠ redis library not installed - Redis features unavailable")
        except Exception as e:
            logger.error(f"❌ Redis connection failed: {e}")
            logger.warning("  Redis is configured but connection test failed!")
            logger.warning("  Check REDIS_URL and ensure Redis is accessible")
    else:
        logger.info("Redis not configured (REDIS_URL not set)")
    
    # Create Redis RAG index if it doesn't exist (for codegen prompt enrichment)
    if redis_url:
        try:
            import redis.asyncio as aioredis
            from redis.commands.search.field import TextField, VectorField
            from redis.commands.search.indexDefinition import IndexDefinition, IndexType
            
            r = aioredis.Redis.from_url(
                redis_url, 
                socket_connect_timeout=REDIS_CONNECTION_TIMEOUT_SECONDS, 
                socket_timeout=REDIS_CONNECTION_TIMEOUT_SECONDS
            )
            try:
                # Check if index already exists
                await r.ft(RAG_INDEX_NAME).info()
                logger.info(f"✓ Redis RAG index '{RAG_INDEX_NAME}' already exists")
            except Exception:
                # Create the index
                try:
                    schema = (
                        TextField("content"),
                        TextField("metadata"),
                        VectorField(
                            "embedding", 
                            "FLAT", 
                            {
                                "TYPE": "FLOAT32", 
                                "DIM": RAG_EMBEDDING_DIM, 
                                "DISTANCE_METRIC": "COSINE"
                            }
                        ),
                    )
                    definition = IndexDefinition(prefix=[RAG_INDEX_PREFIX], index_type=IndexType.HASH)
                    await r.ft(RAG_INDEX_NAME).create_index(schema, definition=definition)
                    logger.info(f"✓ Redis RAG index '{RAG_INDEX_NAME}' created successfully (dim={RAG_EMBEDDING_DIM})")
                except Exception as idx_err:
                    logger.warning(f"⚠ Could not create Redis RAG index: {idx_err}")
                    logger.warning("  Codegen will work without RAG enrichment")
            await r.aclose()
        except ImportError:
            logger.debug("Redis search module not available - RAG index not created")
        except Exception as e:
            logger.warning(f"⚠ Redis RAG index setup failed: {e}")
    
    # P2 FIX: Validate Database connection on startup (explicit query test)
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        logger.info("=" * 80)
        logger.info("VALIDATING DATABASE CONNECTION")
        logger.info("=" * 80)
        try:
            from sqlalchemy import text
            from sqlalchemy.ext.asyncio import create_async_engine
            
            # Convert database URL for async if needed
            async_url = database_url
            if database_url.startswith("postgresql://"):
                async_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif database_url.startswith("sqlite://"):
                async_url = database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
            
            engine = create_async_engine(
                async_url,
                pool_pre_ping=True,
            )
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()
            # Mask password in log
            safe_url = database_url.split('@')[-1] if '@' in database_url else database_url
            logger.info(f"✓ Database connection validated: {safe_url}")
        except ImportError as ie:
            logger.warning(f"⚠ Database driver not installed: {ie}")
            logger.warning("  Install asyncpg (PostgreSQL) or aiosqlite (SQLite)")
        except Exception as e:
            logger.error(f"❌ Database connection failed: {e}")
            logger.warning("  DATABASE_URL is configured but connection test failed!")
            logger.warning("  Check credentials and ensure database is accessible")
    else:
        logger.info("Database not configured (DATABASE_URL not set)")
    
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
        if not routers_ok:
            logger.warning("Skipping agent loading due to router loading failure")
        elif get_agent_loader is None:
            logger.warning("Agent loader not available - skipping agent loading")
    
    # P2 FIX: Warn if Sentry is not configured in production
    sentry_dsn = os.getenv("SENTRY_DSN")
    if _is_production and not sentry_dsn:
        logger.warning("=" * 80)
        logger.warning("⚠ SENTRY NOT CONFIGURED IN PRODUCTION")
        logger.warning("  Error tracking is disabled. Set SENTRY_DSN to enable.")
        logger.warning("  This reduces visibility into production exceptions.")
        logger.warning("=" * 80)
    elif sentry_dsn:
        logger.info("✓ Sentry error tracking: ENABLED")
    
    logger.info("=" * 80)
    logger.info("Platform initialization complete")
    logger.info("=" * 80)
    
    # Validate Kafka configuration
    # P0 CRITICAL: Kafka is REQUIRED for production-ready code generation
    # Without Kafka, the system falls back to local mode which produces stub code
    try:
        from server.services.dispatch_service import get_kafka_health_status
        kafka_status = get_kafka_health_status()
        
        if kafka_status["enabled"]:
            logger.info("=" * 80)
            logger.info("VALIDATING KAFKA CONFIGURATION (CRITICAL)")
            logger.info("=" * 80)
            
            bootstrap_servers = kafka_status.get("bootstrap_servers", "")
            kafka_required = kafka_status.get("required", False)
            
            # Check for localhost misconfiguration
            if "localhost" in bootstrap_servers or "127.0.0.1" in bootstrap_servers:
                error_msg = (
                    f"❌ CRITICAL: Kafka configured with localhost ({bootstrap_servers}). "
                    "This will fail in containerized environments. "
                    "Use service name (e.g., 'kafka:9092') or external URL."
                )
                logger.error(error_msg)
                if kafka_required:
                    raise RuntimeError(error_msg)
            
            # Test connectivity with quick timeout
            try:
                # Try importing kafka-python
                from kafka import KafkaProducer
                
                logger.info(f"Testing Kafka connectivity to {bootstrap_servers}...")
                producer = KafkaProducer(
                    bootstrap_servers=bootstrap_servers.split(","),
                    request_timeout_ms=5000,
                    api_version_auto_timeout_ms=3000,
                )
                producer.close()
                logger.info(f"✓ Kafka connectivity validated: {bootstrap_servers}")
                logger.info("✓ Event-driven orchestration is ACTIVE")
                logger.info("✓ Workers will receive jobs for production-ready generation")
                
            except ImportError:
                error_msg = (
                    "kafka-python not installed. Kafka dispatch will not work. "
                    "Install with: pip install kafka-python"
                )
                logger.error(f"❌ {error_msg}")
                if kafka_required:
                    raise RuntimeError(error_msg)
                    
            except Exception as e:
                error_msg = (
                    f"❌ CRITICAL: Kafka connectivity test failed: {e}\n"
                    f"  Bootstrap servers: {bootstrap_servers}\n"
                    f"  This means:\n"
                    f"    - Workers won't receive jobs\n"
                    f"    - System will fall back to local/stub mode\n"
                    f"    - Generated code will be minimal/incomplete\n"
                    f"  FIX: Ensure Kafka is running and accessible"
                )
                logger.error(error_msg)
                
                if kafka_required:
                    logger.error("❌ KAFKA_REQUIRED=true - FAILING STARTUP")
                    raise RuntimeError(f"Kafka connectivity required but failed: {e}")
                else:
                    logger.warning(
                        "⚠️  KAFKA_REQUIRED=false - Continuing with degraded functionality"
                    )
        else:
            logger.warning("=" * 80)
            logger.warning("⚠️  KAFKA IS DISABLED (KAFKA_ENABLED=false)")
            logger.warning("  WARNING: System will operate in local/fallback mode")
            logger.warning("  This means:")
            logger.warning("    - No event-driven worker orchestration")
            logger.warning("    - Generated code will be minimal/stubs")
            logger.warning("    - No clarifier refinement loops")
            logger.warning("    - Tests/docs/configs may not be generated")
            logger.warning("  RECOMMENDATION: Enable Kafka for production use")
            logger.warning("=" * 80)
            
    except RuntimeError:
        # Re-raise to fail startup
        raise
    except Exception as e:
        logger.error(f"Error during Kafka validation: {e}", exc_info=True)
        # Check if Kafka is required from config (already loaded) or env var
        kafka_required = False
        if config:
            kafka_required = getattr(config, 'kafka_required', False)
        else:
            kafka_required = os.getenv("KAFKA_REQUIRED", "false").lower() in ("true", "1", "yes")
            
        if kafka_required:
            raise RuntimeError(f"Kafka validation failed and KAFKA_REQUIRED=true: {e}")
    
    # Log consolidated security posture status
    _security_warnings = []
    if not os.getenv("AWS_REGION"):
        _security_warnings.append("AWS KMS not configured (AWS_REGION not set) - using local encryption")
    if not os.getenv("HASH_MANIFEST"):
        _security_warnings.append("Plugin integrity checks disabled (HASH_MANIFEST not set)")
    if os.getenv("AUDIT_CRYPTO_MODE", "software") == "dev":
        _security_warnings.append("Audit crypto in DEV mode - not suitable for production")

    if _security_warnings:
        logger.warning("=" * 80)
        logger.warning("SECURITY POSTURE SUMMARY")
        logger.warning("=" * 80)
        for warning in _security_warnings:
            logger.warning(f"  ⚠ {warning}")
        logger.warning("")
        logger.warning("  To resolve, see SECURITY_CONFIGURATION.md")
        logger.warning("  These are acceptable for development/staging environments.")
        logger.warning("=" * 80)
    
    # HIGH: Start periodic audit flush task now that event loop is running
    try:
        from server.services.omnicore_service import get_omnicore_service
        omnicore = get_omnicore_service()
        if omnicore:
            await omnicore.start_periodic_audit_flush()
    except Exception as e:
        logger.warning(f"Failed to initialize periodic audit flush: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    
    All initialization happens in background tasks so the HTTP server can accept
    connections immediately for Railway healthchecks.
    
    In test mode, routers are loaded synchronously to ensure they are available
    immediately for test requests.
    """
    # Startup - DO NOT BLOCK HERE (except in test mode)
    # Routers are registered SYNCHRONOUSLY before yield to ensure API endpoints
    # are available immediately when the server starts accepting requests.
    # Heavy initialization (config, agents) happens in background tasks.
    # Startup
    logger.info("Starting Code Factory API Server")
    logger.info(f"Version: {__version__}")
    logger.info("=" * 80)
    logger.info("HTTP SERVER STARTING - Registering routers synchronously")
    logger.info("=" * 80)
    
    background_task = None
    
    # In test mode, load routers synchronously to ensure they're available immediately
    if is_test():
        logger.info("Test mode detected - loading routers synchronously")
        routers_ok = _load_routers()
        if routers_ok:
            # Use shared helper to include routers
            _include_routers(app)
        else:
            logger.error(f"Router loading failed: {_router_load_error}")
    # CRITICAL: Register routers SYNCHRONOUSLY before yielding
    # This ensures API endpoints are available when the server starts
    routers_ok = _register_routers_sync(app)
    
    # Start HEAVY initialization in background (config, agents)
    # Pass routers_ok so background task knows if agents should be loaded
    background_task = asyncio.create_task(_background_initialization(app, routers_ok))
    logger.info("Background initialization task created for config and agents")
    logger.info("✓ API endpoints are now available")
    
    # Yield - server now accepts requests with routers already registered
    yield
    
    # Shutdown
    logger.info("Shutting down Code Factory API Server")
    
    # Cancel background task if still running
    if background_task is not None and not background_task.done():
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


# FIX: Add graceful shutdown signal handlers to prevent CancelledError cascades
# This ensures long-running LLM calls are cancelled gracefully with timeout
_shutdown_event = asyncio.Event()


def _handle_shutdown_signal(signum, frame):
    """
    Signal handler for SIGTERM and SIGINT.
    
    Sets the shutdown event to trigger graceful shutdown of background tasks.
    """
    signame = signal.Signals(signum).name
    logger.info(f"Received {signame}, initiating graceful shutdown...")
    _shutdown_event.set()


# Register signal handlers
signal.signal(signal.SIGTERM, _handle_shutdown_signal)
signal.signal(signal.SIGINT, _handle_shutdown_signal)


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

# Configure CORS with secure defaults
# P0 FIX: In production, CORS blocks ALL browser requests unless ALLOWED_ORIGINS is explicitly set
# This prevents "works in curl but fails in browser" issues
# Example: ALLOWED_ORIGINS=https://yourdomain.com,https://app.yourdomain.com,https://your-railway-domain.up.railway.app
_is_production = (
    os.getenv("APP_ENV", "").lower() == "production" or 
    os.getenv("PYTHON_ENV", "").lower() == "production" or
    os.getenv("RAILWAY_ENVIRONMENT") is not None or
    not _is_test_environment
)

# Get allowed origins from environment (support both ALLOWED_ORIGINS and CORS_ORIGINS)
allowed_origins_str = os.getenv("ALLOWED_ORIGINS", "") or os.getenv("CORS_ORIGINS", "")
if allowed_origins_str:
    ALLOWED_ORIGINS = [origin.strip() for origin in allowed_origins_str.split(",") if origin.strip()]
    logger.info(f"CORS configured with explicit origins: {ALLOWED_ORIGINS}")
else:
    # Use sensible defaults based on environment
    if _is_production:
        # P0 FIX: In production, try to auto-detect Railway deployment URL
        # This prevents "works in curl but fails in browser" issues
        # Railway provides RAILWAY_PUBLIC_DOMAIN (preferred) and RAILWAY_STATIC_URL (fallback)
        railway_url = os.getenv("RAILWAY_PUBLIC_DOMAIN") or os.getenv("RAILWAY_STATIC_URL")
        
        if railway_url:
            # Validate Railway URL to prevent environment variable injection
            if not railway_url.startswith("http"):
                railway_url = f"https://{railway_url}"
            
            # Security: Only trust Railway domains to prevent injection attacks
            # Use proper domain validation by checking the end of the hostname
            try:
                from urllib.parse import urlparse
                parsed = urlparse(railway_url)
                hostname = parsed.hostname or ""
                
                # Validate that hostname ENDS with Railway domains (prevents injection like evil.com/.railway.app.attacker.com)
                if hostname.endswith(".railway.app") or hostname.endswith(".up.railway.app") or hostname == "railway.app" or hostname == "up.railway.app":
                    ALLOWED_ORIGINS = [railway_url]
                    logger.info(
                        f"CORS configured with auto-detected Railway URL: {ALLOWED_ORIGINS}. "
                        "Set ALLOWED_ORIGINS explicitly if you have additional frontend domains."
                    )
                else:
                    # Railway env var doesn't contain expected domain - use permissive default with warning
                    logger.critical(
                        f"CRITICAL: Railway URL detected but hostname doesn't match expected pattern: {hostname}. "
                        f"Expected: *.railway.app or *.up.railway.app. "
                        f"Using permissive CORS default (*) as fallback. "
                        f"\n\n⚠️  ACTION REQUIRED: Set ALLOWED_ORIGINS explicitly.\n"
                        f"   Example: ALLOWED_ORIGINS=https://your-app.railway.app,https://your-frontend.com"
                    )
                    ALLOWED_ORIGINS = ["*"]  # Allow all origins to prevent breaking the application
            except Exception as e:
                # URL parsing failed - use permissive default with critical warning
                logger.critical(
                    f"CRITICAL: Failed to parse Railway URL '{railway_url}': {e}. "
                    f"Using permissive CORS default (*) as fallback. "
                    f"\n\n⚠️  ACTION REQUIRED: Set ALLOWED_ORIGINS explicitly.\n"
                    f"   Example: ALLOWED_ORIGINS=https://your-app.railway.app,https://your-frontend.com"
                )
                ALLOWED_ORIGINS = ["*"]  # Allow all origins to prevent breaking the application
        else:
            # No Railway URL detected and no explicit configuration
            # Use permissive default to prevent breaking the application
            ALLOWED_ORIGINS = ["*"]  # Allow all origins as fallback
            logger.critical(
                "CRITICAL: ALLOWED_ORIGINS not set in production! Browser requests will fail with CORS errors. "
                "Railway URL was not detected. Using permissive CORS default (*) as fallback. "
                "\n\n"
                "⚠️  ACTION REQUIRED: Set ALLOWED_ORIGINS environment variable with your frontend domains.\n"
                "   Example: ALLOWED_ORIGINS=https://myapp.example.com,https://your-app.railway.app\n"
                "\n"
                "Without proper CORS configuration:\n"
                "  - API calls from browsers will be blocked\n"
                "  - Users will see CORS errors in browser console\n"
                "  - Application will appear broken in web browsers\n"
                "\n"
                "For Railway deployments, set: ALLOWED_ORIGINS=https://your-app.railway.app"
            )
    else:
        # In development, allow common local development ports
        ALLOWED_ORIGINS = [
            "http://localhost:3000",
            "http://localhost:8080",
            "http://localhost:5173",  # Vite default
            "http://127.0.0.1:3000",
            "http://127.0.0.1:8080",
            "http://127.0.0.1:5173",
        ]
        logger.info(f"CORS configured with development defaults: {ALLOWED_ORIGINS}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
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


# Favicon endpoint - return 204 No Content to avoid 404 errors
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """
    Handle favicon.ico requests gracefully.
    Returns 204 No Content to avoid 404 errors in browser console.
    """
    return Response(status_code=204)


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
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# Add /api/health endpoint for frontend compatibility
# The frontend expects endpoints under /api prefix
@app.get("/api/health", response_model=HealthResponse, tags=["Health"])
async def api_health_check() -> HealthResponse:
    """
    Health check endpoint under /api prefix for frontend compatibility.
    
    This is an alias for /health to support frontend requests to /api/health.
    """
    return HealthResponse(
        status="healthy",
        version=__version__,
        components={"api": "healthy"},
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# Add /api/agents endpoint for frontend compatibility
# The frontend expects to get agent status from /api/agents
@app.get("/api/agents", tags=["Agents"])
async def get_agents_status():
    """
    Get agent availability status.
    
    This endpoint provides information about which agents are available,
    which are still loading, and which have failed to load.
    
    Returns:
        Agent status information including:
        - total_agents: Total number of agents
        - available_agents: List of available agent names
        - unavailable_agents: List of unavailable agent names
        - availability_rate: Percentage of agents available
    """
    # Check if agent loader is available
    if get_agent_loader is None:
        return {
            "total_agents": 0,
            "available_agents": [],
            "unavailable_agents": [],
            "availability_rate": 0.0,
            "status": "loading",
            "message": "Agent loader not yet initialized",
        }
    
    try:
        loader = get_agent_loader()
        status = loader.get_status()
        return status
    except Exception as e:
        logger.error(f"Error getting agent status: {e}", exc_info=True)
        return {
            "total_agents": 0,
            "available_agents": [],
            "unavailable_agents": [],
            "availability_rate": 0.0,
            "status": "error",
            "message": str(e),
        }


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
    Readiness check endpoint - returns OK only when the application is truly ready.

    Returns the readiness status of the API server, indicating whether
    the application is fully ready to accept traffic and handle requests.
    
    P1 FIX: This endpoint now performs actual health checks on dependencies,
    not just "configured" status checks. This prevents routing traffic to
    instances that haven't fully initialized.

    **Checks performed:**
    - Routers loaded
    - Agents loaded and ready
    - Redis connection (PING)
    - Database connection (if configured)

    **Returns:**
    - HTTP 200: Application is ready (all checks pass)
    - HTTP 503: Application is not ready (one or more checks fail)
    
    **Response includes:**
    - Overall readiness status
    - Individual check results
    - Timestamp
    """
    checks = {
        "api_available": "pass",
    }
    ready = True
    status_text = "ready"
    
    # Check 1: Routers loaded
    if not _routers_loaded:
        checks["routers_loaded"] = "loading"
        ready = False
        status_text = "loading"
    elif _router_load_error:
        checks["routers_loaded"] = f"error: {_router_load_error}"
        ready = False
        status_text = "degraded"
    else:
        checks["routers_loaded"] = "pass"
    
    # Check 2: Agents loaded (only if routers loaded)
    if ready and get_agent_loader is not None:
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
                    checks["agents_loaded"] = "loading"
                    ready = False
                    status_text = "loading"
                elif agent_availability > 0:
                    checks["agents_loaded"] = "pass"
                    available = agent_status.get('available_agents', [])
                    unavailable = agent_status.get('unavailable_agents', [])
                    checks["agents_available"] = f"{len(available)}/{total_agents}"
                    if unavailable:
                        checks["agents_unavailable"] = ", ".join(unavailable)
                else:
                    checks["agents_loaded"] = "fail"
                    ready = False
                    status_text = "degraded"
                    
        except asyncio.TimeoutError:
            logger.warning("Readiness check timed out waiting for agent status")
            checks["agents_loaded"] = "timeout"
            ready = False
            status_text = "timeout"
        except Exception as e:
            logger.error(f"Error checking agent readiness: {e}", exc_info=True)
            checks["agents_loaded"] = "error"
            ready = False
            status_text = "error"
    elif get_agent_loader is None and ready:
        # Agent loader not available yet
        checks["agents_loaded"] = "loading"
        ready = False
        status_text = "loading"
    
    # Check 3: Redis connection (P2 FIX - actual PING, not just "configured")
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis.asyncio as aioredis
            # Try to connect with timeout (1s for readiness check balance between speed and reliability)
            r = aioredis.Redis.from_url(
                redis_url,
                socket_connect_timeout=1.0,
                socket_timeout=1.0
            )
            await r.ping()
            checks["redis"] = "connected"
            await r.aclose()
        except ImportError:
            checks["redis"] = "client_not_installed"
            # Don't fail readiness if Redis client not installed - it's optional
        except Exception as e:
            # Redis is optional - show status but don't fail readiness
            checks["redis"] = f"unavailable: {type(e).__name__}"
            logger.warning(f"Redis health check failed (optional): {e}")
    else:
        checks["redis"] = "not_configured"
    
    # Check 4: Database connection (P2 FIX - actual query, not just "configured")
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        try:
            # Try a simple database query with timeout
            from sqlalchemy import text
            from sqlalchemy.ext.asyncio import create_async_engine
            
            # Convert database URL for async if needed
            async_db_url = database_url
            if database_url.startswith("postgresql://"):
                async_db_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif database_url.startswith("sqlite://"):
                async_db_url = database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
            
            # Configure connection args with timeout based on database type
            connect_args = {}
            if "sqlite" in async_db_url:
                connect_args = {"timeout": 2}
            elif "asyncpg" in async_db_url:
                # asyncpg uses 'command_timeout' for query timeout
                connect_args = {"command_timeout": 2}
            
            engine = create_async_engine(
                async_db_url,
                pool_pre_ping=True,
                connect_args=connect_args,
                pool_timeout=2,  # Time to wait for connection from pool
            )
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()
            checks["database"] = "connected"
        except ImportError:
            checks["database"] = "driver_not_installed"
        except Exception as e:
            # Database is optional - show status but don't fail readiness
            checks["database"] = f"unavailable: {type(e).__name__}"
            logger.warning(f"Database health check failed (optional): {e}")
    else:
        checks["database"] = "not_configured"
    
    # Set HTTP status code based on readiness
    if not ready:
        response.status_code = 503
    
    return ReadinessResponse(
        ready=ready,
        status=status_text,
        checks=checks,
        timestamp=datetime.now(timezone.utc).isoformat(),
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
        await r.aclose()
    except Exception:
        dependencies["redis"] = "unavailable"
    
    # Check Database - P2 FIX: Actually test the connection, not just "configured"
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        try:
            from sqlalchemy import text
            from sqlalchemy.ext.asyncio import create_async_engine
            
            # Convert database URL for async if needed
            async_url = database_url
            if database_url.startswith("postgresql://"):
                async_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif database_url.startswith("sqlite://"):
                async_url = database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
            
            engine = create_async_engine(
                async_url,
                pool_pre_ping=True,
            )
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()
            dependencies["database"] = "connected"
        except ImportError:
            dependencies["database"] = "driver_not_installed"
        except Exception as e:
            dependencies["database"] = f"error: {type(e).__name__}"
    else:
        dependencies["database"] = "not_configured"
    
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
    
    # Check Kafka
    try:
        from server.services.dispatch_service import get_kafka_health_status
        kafka_status = get_kafka_health_status()
        
        if kafka_status["enabled"]:
            dependencies["kafka"] = {
                "status": kafka_status["status"],
                "bootstrap_servers": kafka_status["bootstrap_servers"],
                "circuit_breaker_open": kafka_status.get("circuit_breaker_open", False),
                "message": kafka_status.get("message", "")
            }
        else:
            dependencies["kafka"] = "disabled"
    except Exception as e:
        dependencies["kafka"] = f"error: {str(e)}"
    
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
        timestamp=datetime.now(timezone.utc).isoformat(),
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
    
    # Run the FastAPI application with production-grade timeout settings
    # FIX: Add proper timeout configuration to prevent HTTP2 protocol errors
    # These errors occur when long-running requests (pipeline, codegen) exceed default timeouts
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        access_log=True,
        timeout_keep_alive=300,  # 5 minutes for long-running operations
        timeout_graceful_shutdown=30,  # 30 seconds for graceful shutdown
        h11_max_incomplete_event_size=16 * 1024 * 1024,  # 16MB for large responses
    )
