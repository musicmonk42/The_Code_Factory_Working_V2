# File: omnicore_engine/fastapi_app.py
import ast
import os
import re
import secrets
import sys
from pathlib import Path

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from dotenv import load_dotenv

load_dotenv()

import asyncio
import datetime
import json
import time
from typing import Any, Dict, List, Optional

import jwt
from cryptography.fernet import Fernet
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError
from prometheus_client import make_asgi_app
from pydantic import BaseModel
from contextlib import asynccontextmanager
from starlette.middleware.base import BaseHTTPMiddleware

# Corrected imports to use the centralized OmniCore Engine singletons
from omnicore_engine.core import logger, omnicore_engine, safe_serialize, settings
from omnicore_engine.database.database import Database
from omnicore_engine.meta_supervisor import MetaSupervisor
from omnicore_engine.plugin_registry import (
    PLUGIN_REGISTRY,
    PlugInKind,
    PluginMarketplace,
)

# Import configuration validator for production mode checks
try:
    from omnicore_engine.config_validator import is_production_mode
except ImportError:
    # Fallback if config_validator not available
    def is_production_mode():
        return os.getenv("PRODUCTION_MODE", "0") == "1"


def check_production_mode_usage(component_name: str, method_name: str = None):
    """
    Helper function to check and log production mode usage of mock implementations.
    
    Args:
        component_name: Name of the component (e.g., "ExplainableReasonerPlugin")
        method_name: Optional method name being called
    
    Raises:
        RuntimeError: If in production mode and method_name is provided
    """
    if not is_production_mode():
        return
    
    if method_name:
        logger.error(f"Mock {component_name}.{method_name}() called in production mode")
        raise RuntimeError(
            f"Mock {component_name} should not be used in production. "
            f"Please install the required Arbiter package."
        )
    else:
        logger.error(
            f"CRITICAL: Mock {component_name} initialized in production mode. "
            f"Real implementation required for production."
        )

try:
    from self_fixing_engineer.simulation.simulation_module import (
        UnifiedSimulationModule,
        Database as SimulationDatabase,
        ShardedMessageBus as SimulationMessageBus,
        create_simulation_module,
    )
except ImportError:
    try:
        from simulations.simulation_module import UnifiedSimulationModule
    except ImportError:
        UnifiedSimulationModule = None
try:
    from self_healing_import_fixer.import_fixer.fixer_ai import AIManager
except ImportError:
    AIManager = None
from omnicore_engine.message_bus.message_types import Message
from omnicore_engine.metrics import API_ERRORS, API_REQUESTS

# Using functools.partial to create a callable that mimics the plugin's interface
# This is a good practice for dynamic plugin execution.
# from arbiter.arbiter import Arbiter as RealArbiter
# from omnicore_engine.fastapi_app import trigger_test_generation_via_omnicore
# from omnicore_engine.fastapi_app import run_test_generation_plugin
# from arbiter.arbiter_plugin_registry import PLUGIN_REGISTRY

try:
    # Updated imports to reflect the new arbiter package structure
    import sqlalchemy
    from arbiter.agent_state import ArbiterConfig
    from arbiter import Arbiter
    from arbiter.arena import ArbiterArena
    from arbiter.explainable_reasoner import ExplainableReasonerPlugin
    from arbiter.knowledge_loader import KnowledgeLoader
    from arbiter.policy.core import PolicyEngine

    from omnicore_engine.feedback_manager import FeedbackManager, FeedbackType
    from omnicore_engine.merkle_tree import MerkleTree

    ARBITER_AVAILABLE = True
    ARENA_AVAILABLE = True
    MERKLE_TREE_AVAILABLE = True
except ImportError as e:
    logger.warning(
        f"Could not import all core engine components for FastAPI: {e}. Some features will be mocked."
    )

    # Check if we're in production mode - fail fast if so
    if is_production_mode():
        error_msg = (
            f"CRITICAL: Required Arbiter components are not available in production mode. "
            f"Import error: {e}. "
            f"Please ensure all required dependencies are installed. "
            f"See DEPENDENCY_GUIDE.md for installation instructions."
        )
        logger.error(error_msg)
        # In production, we should not start with mock implementations
        # However, we'll allow the application to start but log a critical error
        # The health check should catch this and report unhealthy status
        logger.error(
            "WARNING: Starting with mock implementations in production mode. "
            "This is not recommended and may cause unexpected behavior."
        )

    ARBITER_AVAILABLE = False
    ARENA_AVAILABLE = False
    MERKLE_TREE_AVAILABLE = False

    class ExplainableReasonerPlugin:
        """
        Mock implementation of ExplainableReasonerPlugin for development/testing.

        This stub provides minimal functionality when the real Arbiter explainable
        reasoner plugin is not available. It follows industry standards for:
        - Graceful degradation in development environments
        - Predictable mock responses for testing
        - Clear indication that real functionality is unavailable

        Real Implementation Features (when Arbiter is installed):
        - AI-driven explanation generation for agent decisions
        - Multi-level explanation detail (high-level, detailed, technical)
        - Natural language reasoning descriptions
        - Counterfactual analysis ("what-if" scenarios)
        - Confidence scores and uncertainty quantification
        - Citation of decision factors and data sources
        
        WARNING: This mock should not be used in production mode.
        """

        def __init__(self, *args, **kwargs):
            """Initialize mock ExplainableReasonerPlugin."""
            check_production_mode_usage("ExplainableReasonerPlugin")

        async def explain(self, *args, **kwargs):
            """
            Return a mock explanation.

            Real implementation would provide detailed reasoning about:
            - Why a particular decision was made
            - What factors influenced the outcome
            - Alternative paths that were considered
            - Confidence levels and uncertainties

            Returns:
                str: Mock explanation message
            """
            check_production_mode_usage("ExplainableReasonerPlugin", "explain")
            return "Mock explanation."

    class PolicyEngine:
        """
        Mock implementation of PolicyEngine for development/testing.

        This stub provides minimal policy checking functionality when the real
        Arbiter policy engine is not available. Production systems should use
        the full PolicyEngine for:
        - Governance and compliance enforcement
        - Access control and authorization
        - Resource usage policies
        - Behavior constraints and guardrails
        - Audit requirements and logging policies

        Industry Standard Features (when Arbiter is installed):
        - Declarative policy definition (YAML/JSON)
        - Real-time policy evaluation
        - Policy versioning and rollback
        - Policy conflict detection
        - Explainable policy decisions
        
        WARNING: This mock should not be used in production mode.
        """

        def __init__(self, *args, **kwargs):
            """Initialize mock PolicyEngine."""
            check_production_mode_usage("PolicyEngine")

        async def should_auto_learn(self, *args, **kwargs):
            """
            Mock policy check for auto-learning permission.

            Real implementation would evaluate:
            - User/tenant permissions
            - Resource availability constraints
            - Regulatory compliance requirements
            - Risk assessment thresholds
            - Learning mode configurations

            Returns:
                tuple: (bool, str) - (should_learn, policy_reason)
            """
            check_production_mode_usage("PolicyEngine", "should_auto_learn")
            return True, "Mock Policy"

    class FeedbackManager:
        """
        Mock implementation of FeedbackManager for development/testing.

        This stub provides no-op feedback collection when the real feedback
        system is not available. Production deployments should use the full
        FeedbackManager for:
        - User feedback collection and analysis
        - Bug report aggregation
        - Feature request tracking
        - Sentiment analysis
        - Feedback-driven improvements
        - Integration with issue tracking systems

        Industry Standard Features (when installed):
        - Multi-channel feedback collection (API, UI, CLI)
        - Structured feedback taxonomy
        - Automated categorization and routing
        - Priority scoring and triage
        - Analytics and trend detection
        
        WARNING: This mock should not be used in production mode.
        """

        def __init__(self, *args, **kwargs):
            """Initialize mock FeedbackManager."""
            if is_production_mode():
                logger.warning(
                    "Mock FeedbackManager initialized in production mode. "
                    "Feedback features will be disabled. "
                    "Install the full FeedbackManager for production use."
                )

        async def initialize(self):
            """No-op initialization for mock."""
            pass

        async def record_feedback(self, *args, **kwargs):
            """
            No-op feedback recording for mock.

            Real implementation would:
            - Validate feedback structure
            - Store in database with metadata
            - Trigger automated workflows
            - Send notifications to relevant teams
            - Update metrics and dashboards
            """
            if is_production_mode():
                logger.warning("Mock FeedbackManager.record_feedback() called in production mode")
            pass

    class FeedbackType:
        """
        Enumeration of feedback types supported by the system.

        This class defines standard feedback categories used throughout
        the platform for consistent feedback handling and routing.
        """

        BUG_REPORT = "bug_report"
        GENERAL = "general"
        MOOD_CORRECTION = "mood_correction"
        FEATURE_REQUEST = "feature_request"

    class Arbiter:
        """
        Mock Arbiter implementation for development/testing.

        See engines.py for full documentation of Arbiter capabilities.
        This mock provides minimal no-op functionality for environments
        without the full Arbiter installation.
        """

        def __init__(self, *args, **kwargs):
            """Initialize mock Arbiter."""
            pass

        async def start_async_services(self):
            """No-op service startup for mock."""
            pass

        async def stop_async_services(self):
            """No-op service shutdown for mock."""
            pass

        async def respond(self, *args, **kwargs):
            """Return unavailable message for mock."""
            return "Chatbot unavailable"

    class KnowledgeLoader:
        """
        Mock KnowledgeLoader for development/testing.

        This stub provides no-op knowledge loading when the real knowledge
        graph system is not available. Production systems should use the
        full KnowledgeLoader for:
        - Loading domain knowledge from various sources
        - Building and updating knowledge graphs
        - Semantic reasoning and inference
        - Knowledge base versioning
        - Integration with external knowledge sources

        Industry Standard Features (when available):
        - Multi-format knowledge ingestion (JSON, RDF, GraphML)
        - Ontology management and validation
        - Knowledge graph embedding generation
        - Incremental knowledge updates
        - Conflict resolution and consistency checking
        """

        def load_all(self):
            """No-op knowledge loading for mock."""
            pass

        def inject_to_arbiter(self, arbiter):
            """No-op knowledge injection for mock."""
            pass

    class ArbiterArena:
        """
        Mock ArbiterArena for development/testing.

        This stub provides minimal scanning functionality when the real
        Arbiter Arena (multi-agent coordination system) is not available.

        Real Arena Features (when installed):
        - Multi-agent task coordination
        - Competitive agent evaluation
        - Collaborative problem solving
        - Agent performance benchmarking
        - Automated code scanning and analysis
        - Test case generation
        - Security vulnerability detection

        Industry Standard Features:
        - Agent sandboxing and isolation
        - Resource allocation and scheduling
        - Performance monitoring and metrics
        - Result aggregation and consensus
        - Explainable agent decisions
        """

        def __init__(self, *args, **kwargs):
            """Initialize mock ArbiterArena."""
            pass

        async def start_arena_services(self, *args, **kwargs):
            """No-op arena service startup for mock."""
            pass

        async def run_scan(self, codebase_path: str):
            """
            Mock codebase scanning.

            Real implementation would perform:
            - Static code analysis
            - Security vulnerability scanning
            - Code quality metrics collection
            - Dependency analysis
            - License compliance checking
            - Architecture validation

            Args:
                codebase_path: Path to codebase to scan

            Returns:
                dict: Mock scan results
            """
            return {"status": "mock_scan", "results": "mock_results"}

        async def generate_test_cases(self, *args, **kwargs):
            """
            Mock test case generation.

            Real implementation would generate:
            - Unit tests with multiple scenarios
            - Integration tests for workflows
            - Edge case coverage
            - Property-based tests
            - Performance test scenarios

            Returns:
                str: Mock test cases message
            """
            return "Mock test cases generated."

    class MerkleTree:
        """
        Mock MerkleTree implementation for development/testing.

        This stub provides basic Merkle tree functionality for tamper-proof
        audit logging when the full implementation is not available.

        Real Implementation Features (when available):
        - Cryptographic hash tree construction
        - Efficient proof of inclusion
        - Tamper detection and verification
        - Incremental updates with proof generation
        - Integration with blockchain systems
        - Persistence and recovery

        Industry Standard Applications:
        - Audit log integrity verification
        - Distributed system consistency
        - Certificate transparency
        - Version control systems
        - Blockchain and cryptocurrency
        """

        def __init__(self, leaves: Optional[List[bytes]] = None, *args, **kwargs):
            """
            Initialize mock MerkleTree.

            Args:
                leaves: Optional initial leaf nodes (stored but not processed in mock)
                *args: Additional arguments (ignored)
                **kwargs: Additional keyword arguments (ignored)
            """
            self._mock_root = b"mock_merkle_root"
            self.leaves_data = leaves or []

        def _recalculate_root(self):
            """
            Mock root recalculation.

            Real implementation would:
            - Hash all leaf nodes
            - Build tree bottom-up with pairwise hashing
            - Store intermediate nodes for proof generation
            - Update root hash atomically
            """
            self._mock_root = b"mock_recalculated_root"

        def add_leaf(self, leaf: bytes, key: Optional[bytes] = None) -> None:
            """
            Add a leaf to the mock Merkle tree.

            Args:
                leaf: Data to add as a leaf node
                key: Optional key for indexed access (ignored in mock)
            """
            self.leaves_data.append(leaf)

        def get_root(self) -> bytes:
            """
            Get the mock Merkle tree root hash.

            Returns:
                bytes: Mock root hash
            """
            return self._mock_root

        def get_merkle_root(self) -> str:
            """Legacy method name for compatibility."""
            return self._mock_root.hex()

        def make_tree(self):
            self._recalculate_root()

    class UnifiedSimulationModule:
        """Mock UnifiedSimulationModule for fallback when real module is unavailable.

        This mock class provides minimal interface compatibility with the real
        UnifiedSimulationModule to allow the application to start and run basic
        operations even when the simulation module cannot be imported.
        """

        def __init__(self, *args, **kwargs):
            """Initialize the mock simulation module.

            Args:
                *args: Variable length argument list (ignored in mock).
                **kwargs: Arbitrary keyword arguments (ignored in mock).
            """
            pass

        async def initialize(self):
            """Initialize the simulation module asynchronously.

            This is a no-op in the mock implementation.
            """
            pass

        async def shutdown(self):
            """Shutdown the simulation module gracefully.

            This is a no-op in the mock implementation, but is required for
            compatibility with the application shutdown lifecycle.
            """
            pass


chatbot_arbiter: Optional[Arbiter] = None
arena: Optional[ArbiterArena] = None
simulation_module: Optional[UnifiedSimulationModule] = None
_db_engine = None
system_audit_merkle_tree: MerkleTree = None

# Note: app is defined after the lifespan function below
# Middlewares and routes are also configured after app definition

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")


# Feature Flag Management Store
# In-memory store for feature flags (replace with persistent storage in production)
_feature_flags: Dict[str, Dict[str, Any]] = {
    "experimental_features": {
        "value": False,
        "description": "Enable experimental features",
        "created_at": None,
        "updated_at": None,
        "updated_by": None,
    },
    "debug_mode": {
        "value": False,
        "description": "Enable debug logging and diagnostics",
        "created_at": None,
        "updated_at": None,
        "updated_by": None,
    },
    "maintenance_mode": {
        "value": False,
        "description": "Put system in maintenance mode",
        "created_at": None,
        "updated_at": None,
        "updated_by": None,
    },
}
_feature_flags_lock = asyncio.Lock()


class SizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.headers.get("content-length"):
            if int(request.headers["content-length"]) > 10_000_000:  # 10MB
                return JSONResponse(
                    status_code=413, content={"error": "Request too large"}
                )
        return await call_next(request)


# In fastapi_app.py, add security middleware
from omnicore_engine.security_config import get_security_config

from omnicore_engine.security_utils import RateLimiter, get_security_utils

security_config = get_security_config()
security_utils = get_security_utils()
rate_limiter = RateLimiter(
    max_calls=100, per_seconds=60
)  # Default: 100 calls per minute


@CsrfProtect.load_config
def get_csrf_config():
    """Return CSRF configuration as a Pydantic BaseSettings compatible class"""
    from pydantic_settings import BaseSettings

    # Handle both SecretStr (from ArbiterConfig) and fallback settings
    jwt_secret = getattr(settings, "JWT_SECRET_KEY", None)
    if jwt_secret is not None and hasattr(jwt_secret, "get_secret_value"):
        secret_key_value = jwt_secret.get_secret_value()
    elif jwt_secret is not None:
        secret_key_value = str(jwt_secret)
    else:
        # Generate a fallback secret key for development/testing only
        secret_key_value = secrets.token_urlsafe(32)
        logger.warning(
            "JWT_SECRET_KEY not configured. Using a randomly generated key. "
            "This is NOT suitable for production."
        )

    class CsrfSettings(BaseSettings):
        secret_key: str = secret_key_value

    return CsrfSettings()


async def get_user_id(token: str = Depends(oauth2_scheme)):
    # Handle both SecretStr (from ArbiterConfig) and fallback settings
    jwt_secret = getattr(settings, "JWT_SECRET_KEY", None)
    if jwt_secret is not None and hasattr(jwt_secret, "get_secret_value"):
        secret_key_value = jwt_secret.get_secret_value()
    elif jwt_secret is not None:
        secret_key_value = str(jwt_secret)
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT secret key not configured",
        )

    try:
        payload = jwt.decode(token, secret_key_value, algorithms=["HS256"])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


plugin_upload_lock = asyncio.Lock()
# Use ENCRYPTION_KEY_BYTES which is properly initialized by ArbiterConfig
# The ArbiterConfig singleton initializes this during __new__, so it should always be available
try:
    # Ensure we have a valid key - if empty bytes, generate one
    key_bytes = (
        settings.ENCRYPTION_KEY_BYTES
        if settings.ENCRYPTION_KEY_BYTES
        else Fernet.generate_key()
    )
    encrypter = Fernet(key_bytes)
except (AttributeError, ValueError, Exception) as e:
    logger.error(
        f"Failed to initialize Fernet encrypter: {e}. Generating temporary key for testing."
    )
    encrypter = Fernet(Fernet.generate_key())
meta_supervisor_instance = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI application startup and shutdown.
    Replaces deprecated @app.on_event decorators.
    """
    global chatbot_arbiter, arena, system_audit_merkle_tree, meta_supervisor_instance, simulation_module

    # Startup
    await omnicore_engine.initialize()

    # Initialize simulation module with proper database and message bus adapters
    try:
        if UnifiedSimulationModule is not None:
            # Check if we have the enhanced simulation module with adapter classes
            if "create_simulation_module" in dir():
                # Use the factory function to create simulation module with proper adapters
                logger.info(
                    "Initializing simulation module with real database adapter..."
                )

                # Get database URL from environment or use default with null safety
                db_url = os.getenv("DATABASE_URL")
                if not db_url and omnicore_engine.database:
                    # Safely access db_path with null check
                    db_url = getattr(omnicore_engine.database, "db_path", None)

                # Provide fallback if no database URL available
                if not db_url:
                    logger.warning(
                        "No database URL available, using default SQLite path"
                    )
                    db_url = "sqlite:///./omnicore.db"

                # Create database adapter
                sim_db = SimulationDatabase(db_path=db_url)

                # Create message bus adapter
                sim_bus = SimulationMessageBus()

                # Create simulation module configuration
                sim_config = {
                    "SIM_MAX_WORKERS": getattr(settings, "SIM_MAX_WORKERS", 4),
                    "SIM_RETRY_ATTEMPTS": getattr(settings, "SIM_RETRY_ATTEMPTS", 3),
                    "SIM_BACKOFF_FACTOR": getattr(settings, "SIM_BACKOFF_FACTOR", 1.0),
                }

                # Initialize using factory function
                simulation_module = await create_simulation_module(
                    config=sim_config, db=sim_db, message_bus=sim_bus
                )
                logger.info(
                    "Simulation module initialized successfully with real adapters."
                )
            elif omnicore_engine.database and omnicore_engine.message_bus:
                # Fallback to original initialization method
                simulation_module = UnifiedSimulationModule(
                    config=settings,
                    db=omnicore_engine.database,
                    message_bus=omnicore_engine.message_bus,
                )
                await simulation_module.initialize()
                logger.info(
                    "UnifiedSimulationModule initialized successfully (legacy mode)."
                )
            else:
                logger.warning(
                    "Database or MessageBus not available, creating minimal simulation module."
                )
                simulation_module = UnifiedSimulationModule(
                    config=settings, db=None, message_bus=None
                )
        else:
            logger.warning(
                "UnifiedSimulationModule not available, skipping initialization."
            )
            simulation_module = None
    except Exception as e:
        logger.error(
            f"Failed to initialize UnifiedSimulationModule: {e}", exc_info=True
        )
        simulation_module = UnifiedSimulationModule(
            config=settings, db=None, message_bus=None
        )

    try:
        if MERKLE_TREE_AVAILABLE:
            system_audit_merkle_tree = MerkleTree(
                leaves=None,
                branching_factor=settings.MERKLE_TREE_BRANCHING_FACTOR,
                private_key=(
                    settings.MERKLE_TREE_PRIVATE_KEY.get_secret_value().encode()
                    if settings.MERKLE_TREE_PRIVATE_KEY
                    else None
                ),
            )
            system_audit_merkle_tree.make_tree()
            logger.info(
                f"System audit Merkle tree initialized. Initial root: {system_audit_merkle_tree.get_root()}"
            )
        else:
            system_audit_merkle_tree = MerkleTree()
            logger.warning("MerkleTree not available, using mock.")

        if ARBITER_AVAILABLE:
            arbiter_db_client = Database(
                settings.database_path,
                system_audit_merkle_tree=system_audit_merkle_tree,
            )
            arbiter_feedback_manager = FeedbackManager(
                db_dsn=settings.database_path,
                redis_url=settings.redis_url,
                encryption_key=settings.ENCRYPTION_KEY.get_secret_value(),
            )
            await arbiter_feedback_manager.initialize()

            if not omnicore_engine.crew_manager:
                raise RuntimeError("CrewManager not initialized on omnicore_engine.")

            # New: Pass the simulation module instance to the Arbiter
            if not omnicore_engine.test_generation_orchestrator:
                logger.warning(
                    "TestGenerationOrchestrator not available. Arbiter's test generation capability will be limited."
                )

            # Collect all available engines to pass to the arbiter
            available_engines = {
                "simulation": simulation_module,
                "test_generation": omnicore_engine.test_generation_orchestrator,
                "code_health_env": omnicore_engine.code_health_env,
                "audit_log_manager": omnicore_engine.audit,
                "intent_capture": omnicore_engine.intent_capture_engine,
            }

            chatbot_arbiter = Arbiter(
                settings=settings,
                db_engine=arbiter_db_client.engine,  # Corrected: Pass the engine, not the client
                feedback_manager=arbiter_feedback_manager,
                crew_manager=omnicore_engine.crew_manager,
                engines=available_engines,  # Pass the collected engines
            )
            await chatbot_arbiter.start_async_services()
            logger.info("AI assistant services started.")
        else:
            logger.warning("AI assistant is not available.")

        if ARENA_AVAILABLE and omnicore_engine.database:
            arena = ArbiterArena(
                name="MainArena",
                port=settings.ARENA_PORT,
                settings=settings,
                db_engine=omnicore_engine.database.engine,
            )
            await arena.start_arena_services(http_port=settings.ARENA_PORT)
            logger.info(
                f"AI assistant arena services started on port {settings.ARENA_PORT}."
            )
        else:
            logger.warning("AI assistant arena is not available.")

        meta_supervisor_instance = MetaSupervisor(
            interval=300, backend_mode="torch", use_quantum=True
        )
        await meta_supervisor_instance.initialize()
        asyncio.create_task(meta_supervisor_instance.run())
        logger.info("MetaSupervisor initialized and background task started.")

        logger.info("FastAPI app startup complete. OmniCore Engine ready.")
    except Exception as e:
        logger.critical(f"FastAPI startup failed: {e}", exc_info=True)
        raise

    # Yield control to the application
    yield

    # Shutdown
    if simulation_module:
        await simulation_module.shutdown()
        logger.info("UnifiedSimulationModule shutdown complete.")

    await omnicore_engine.shutdown()
    if chatbot_arbiter:
        await chatbot_arbiter.stop_async_services()
        logger.info("AI assistant services stopped.")
    if arena:
        await arena.stop_arena_services()
        logger.info("AI assistant arena services stopped.")
    if meta_supervisor_instance:
        await meta_supervisor_instance.stop()
        logger.info("MetaSupervisor services stopped.")
    logger.info("FastAPI app shutdown complete.")


app = FastAPI(
    title="OmniCore Omega Pro Engine API",
    description="Universal orchestration engine with audit and AI integration",
    version=settings.LOG_LEVEL,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

# Configure middlewares
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Add security middleware
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Rate limiting
    client_ip = request.client.host
    if not rate_limiter.is_allowed(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})

    # Add security headers
    response = await call_next(request)
    for header, value in security_config.SECURITY_HEADERS.items():
        response.headers[header] = value

    return response


app.add_middleware(SizeLimitMiddleware)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[
        "*.yourdomain.com",
        "localhost",
        "127.0.0.1",
        "testserver",
        # Railway deployment domains for healthcheck and public access
        "*.railway.app",
        "*.up.railway.app",
    ],
)


@app.exception_handler(CsrfProtectError)
def csrf_protect_exception_handler(request: Request, exc: CsrfProtectError):
    return JSONResponse(status_code=403, content={"error": "CSRF validation failed"})


app.mount("/metrics", make_asgi_app())


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url, title=app.title + " - Swagger UI"
    )


@app.get("/redoc", include_in_schema=False)
async def custom_redoc_html():
    return get_redoc_html(openapi_url=app.openapi_url, title=app.title + " - ReDoc")


@app.get("/health")
async def root_health_check():
    """
    Root-level health check endpoint for container orchestration and load balancers.

    This endpoint is separate from /api/health to provide a simple, fast health check
    at the root level that container orchestrators (Docker, Kubernetes, Railway) expect.

    Returns:
        dict: Health status with status and timestamp
    """
    return {
        "status": "healthy",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


class ChatRequest(BaseModel):
    user_id: str
    message: str
    context: Dict[str, Any] = {}


class ChatResponse(BaseModel):
    response: str
    status: str = "success"
    message: Optional[str] = None


class FeatureFlagUpdateRequest(BaseModel):
    value: bool


class PluginInstallRequest(BaseModel):
    kind: str
    name: str
    version: str


class PluginRateRequest(BaseModel):
    kind: str
    name: str
    version: str
    rating: int
    comment: Optional[str] = None


class TestGenerationRequest(BaseModel):
    targets: List[Dict[str, Any]]
    config: Optional[Dict[str, Any]] = {}


router = APIRouter(prefix="/api")


def safe_jsonify(data: Dict[str, Any]) -> JSONResponse:
    try:
        return JSONResponse(content=data)
    except TypeError:
        return JSONResponse(
            content=json.loads(json.dumps(data, default=safe_serialize))
        )


ALLOWED_EXTENSIONS = {".py", ".json", ".yaml", ".yml"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


async def validate_upload(file: UploadFile):
    """
    Validates file extension, size, and content for uploads.
    """
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type {ext} not allowed")

    # Read the file content to check size and content
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")

    if ext == ".py":
        try:
            ast.parse(content)
        except SyntaxError:
            raise HTTPException(status_code=400, detail="Invalid Python file syntax")

    # Reset file pointer after reading
    await file.seek(0)
    return file


@router.post("/test-generation/run")
async def run_test_generation(request: TestGenerationRequest):
    """
    Triggers the autonomous test generation and integration pipeline.
    """
    try:
        if not omnicore_engine.test_generation_orchestrator:
            raise HTTPException(
                status_code=500, detail="TestGenerationOrchestrator is not initialized."
            )

        response = await omnicore_engine.test_generation_orchestrator.generate_tests_for_targets(
            targets=request.targets, config=request.config
        )
        return {"status": "success", "result": response}
    except Exception as e:
        logger.error(f"Error during test generation API call: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"message": str(e)})


@router.post("/scenarios/test_generation/run")
async def run_test_generation_plugin(payload: Dict[str, Any]):
    """
    Runs the 'generate_tests' plugin directly from the OmniCore registry.
    """
    plugin = PLUGIN_REGISTRY.get(PlugInKind.EXECUTION, "generate_tests")
    if not plugin:
        raise HTTPException(status_code=404, detail="test_generation plugin not found")

    # We need to call the plugin with the correct arguments from the payload
    try:
        code = payload.get("code")
        language = payload.get("language", "python")
        config = payload.get("config", {})

        if code is None:
            raise ValueError("The 'code' field is required in the payload.")

        result = await plugin.execute(code=code, language=language, config=config)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})
    except Exception as e:
        logger.error(f"Error calling test_generation plugin: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"message": f"Internal error calling plugin: {e}"}
        )


@router.post("/simulation/execute")
async def execute_simulation(request: Request):
    """
    Executes a simulation using the simulation engine.
    """
    if not simulation_module:
        raise HTTPException(
            status_code=500, detail="Simulation engine is not initialized."
        )
    try:
        sim_config = await request.json()
        result = await simulation_module.execute_simulation(sim_config)
        return {"status": "success", "result": result}
    except Exception as e:
        logger.error(f"Error executing simulation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"message": str(e)})


@router.post("/simulation/explain")
async def explain_simulation(request: Request):
    """
    Requests an explanation for a simulation result from the simulation engine.
    """
    if not simulation_module:
        raise HTTPException(
            status_code=500, detail="Simulation engine is not initialized."
        )
    try:
        result = await request.json()
        explanation = await simulation_module.explain_result(result)
        return {"status": "success", "explanation": explanation}
    except Exception as e:
        logger.error(f"Error explaining simulation result: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"message": str(e)})


@router.post("/notify")
async def notify(request: Request):
    API_REQUESTS.labels(endpoint="/notify", method="POST").inc()
    try:
        data = await request.json()
        logger.info(
            f"Received UI notification: {data.get('message')} (Type: {data.get('type')})"
        )
        return {"status": "received", "data": data}
    except Exception as e:
        API_ERRORS.labels(
            endpoint="/notify", method="POST", error_type="exception"
        ).inc()
        logger.error(f"Error in /notify: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"message": str(e)})


@router.post("/chat", response_model=ChatResponse)
async def chat_with_bot(chat_request: ChatRequest):
    API_REQUESTS.labels(endpoint="/chat", method="POST").inc()
    if not ARBITER_AVAILABLE:
        return ChatResponse(
            response="Chatbot unavailable.",
            status="error",
            message="AI assistant not loaded",
        )
    try:
        if chatbot_arbiter is None:
            logger.error("AI assistant is None. Cannot respond.")
            return ChatResponse(
                response="Chatbot is not initialized.",
                status="error",
                message="Chatbot initialization error.",
            )

        chatbot_response = await chatbot_arbiter.respond(
            user_id=chat_request.user_id,
            message=chat_request.message,
            context=chat_request.context,
        )
        return ChatResponse(response=chatbot_response, status="success")
    except Exception as e:
        API_ERRORS.labels(endpoint="/chat", method="POST", error_type="exception").inc()
        logger.error(f"Chatbot response error: {e}", exc_info=True)
        return ChatResponse(
            response="Error processing request.", status="error", message=str(e)
        )


@router.post("/arbiter/analyze-code")
async def analyze_code(codebase_path: str):
    if not ARENA_AVAILABLE:
        raise HTTPException(status_code=500, detail="Arbiter Arena not available.")
    settings = ArbiterConfig()
    arena = ArbiterArena(
        name="CodebaseAnalyzerArena",
        port=settings.ARENA_PORT,
        settings=settings,
        db_engine=omnicore_engine.database.engine,
    )
    result = await arena.run_scan(codebase_path)
    return safe_jsonify({"status": "success", "result": result})


@router.get("/health")
async def health_check_api():
    API_REQUESTS.labels(endpoint="/health", method="GET").inc()
    return await omnicore_engine.health_check()


@app.post("/code-factory-workflow")
async def code_factory_workflow(request: Request, user_id: str = Depends(get_user_id)):
    API_REQUESTS.labels(endpoint="/code-factory-workflow", method="POST").inc()
    payload = await request.json()
    message = Message(topic="start_workflow", payload=payload)
    await omnicore_engine.message_bus.publish(message.topic, message.payload)
    return {"status": "workflow_started", "trace_id": message.trace_id}


admin_router = APIRouter(prefix="/admin")


async def verify_admin_api_enabled():
    if not settings.EXPERIMENTAL_FEATURES_ENABLED:
        logger.warning("Attempted access to disabled admin API.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Admin API is not enabled."
        )


@admin_router.get("/feature-flag")
async def get_feature_flag(
    flag_name: Optional[str] = Query(
        None, description="Specific feature flag name to retrieve"
    )
):
    """
    Get feature flag configuration.

    Retrieves one or all feature flags from the system.
    Feature flags allow dynamic control of system behavior without code changes.

    Args:
        flag_name: Optional specific feature flag name to retrieve.
                  If not provided, returns all feature flags.

    Returns:
        JSON response with feature flag(s) data

    Example Response (single flag):
        {
            "flag_name": "experimental_features",
            "value": false,
            "description": "Enable experimental features",
            "created_at": "2026-01-14T00:00:00Z",
            "updated_at": null,
            "updated_by": null
        }

    Example Response (all flags):
        {
            "flags": {
                "experimental_features": {...},
                "debug_mode": {...}
            },
            "count": 2
        }
    """
    API_REQUESTS.labels(endpoint="/admin/feature-flag", method="GET").inc()

    async with _feature_flags_lock:
        if flag_name:
            # Get specific flag
            if flag_name not in _feature_flags:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "message": f"Feature flag '{flag_name}' not found.",
                        "available_flags": list(_feature_flags.keys()),
                    },
                )
            flag_data = _feature_flags[flag_name].copy()
            flag_data["flag_name"] = flag_name
            return flag_data
        else:
            # Get all flags
            return {
                "flags": {k: v for k, v in _feature_flags.items()},
                "count": len(_feature_flags),
            }


@admin_router.post("/feature-flag")
async def set_feature_flag(
    flag_name: str,
    request_body: FeatureFlagUpdateRequest,
    user_id: str = Depends(get_user_id),
):
    """
    Set or update feature flag configuration.

    Updates the value of an existing feature flag or creates a new one.
    Changes are applied immediately and affect system behavior in real-time.

    Security:
        - Requires authentication (admin access recommended)
        - Changes are logged with user ID for audit trail

    Args:
        flag_name: Feature flag name to set (alphanumeric and underscores)
        request_body: Feature flag configuration with 'value' field (boolean)
        user_id: Authenticated user ID (from token)

    Returns:
        JSON response with updated feature flag data

    Example Request:
        POST /admin/feature-flag?flag_name=debug_mode
        {
            "value": true
        }

    Example Response:
        {
            "message": "Feature flag 'debug_mode' updated successfully",
            "flag_name": "debug_mode",
            "value": true,
            "updated_at": "2026-01-14T04:55:00Z",
            "updated_by": "user_123",
            "is_new": false
        }
    """
    API_REQUESTS.labels(endpoint="/admin/feature-flag", method="POST").inc()

    # Validate flag name (alphanumeric and underscores only)
    if not re.match(r"^[a-zA-Z0-9_]+$", flag_name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Feature flag name must contain only letters, numbers, and underscores",
        )

    # Use timezone-aware datetime for compatibility
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    async with _feature_flags_lock:
        is_new = flag_name not in _feature_flags

        if is_new:
            # Create new flag
            _feature_flags[flag_name] = {
                "value": request_body.value,
                "description": f"Feature flag: {flag_name}",
                "created_at": timestamp,
                "updated_at": timestamp,
                "updated_by": user_id,
            }
            logger.info(
                f"Created new feature flag '{flag_name}' with value {request_body.value} by user {user_id}"
            )
        else:
            # Update existing flag
            _feature_flags[flag_name]["value"] = request_body.value
            _feature_flags[flag_name]["updated_at"] = timestamp
            _feature_flags[flag_name]["updated_by"] = user_id
            logger.info(
                f"Updated feature flag '{flag_name}' to {request_body.value} by user {user_id}"
            )

        return {
            "message": f"Feature flag '{flag_name}' {'created' if is_new else 'updated'} successfully",
            "flag_name": flag_name,
            "value": request_body.value,
            "updated_at": timestamp,
            "updated_by": user_id,
            "is_new": is_new,
        }


@admin_router.post("/plugins/install")
async def install_plugin(
    request_body: PluginInstallRequest, user_id: str = Depends(get_user_id)
):
    API_REQUESTS.labels(endpoint="/admin/plugins/install", method="POST").inc()
    try:
        marketplace = PluginMarketplace(db=omnicore_engine.database)
        await marketplace.install_plugin(
            request_body.kind, request_body.name, request_body.version
        )
        return {
            "status": "success",
            "message": f"Plugin {request_body.name} (v{request_body.version}) installed.",
        }
    except ValueError as ve:
        logger.warning(f"Plugin installation failed due to invalid input: {ve}")
        raise HTTPException(status_code=400, detail={"message": str(ve)})
    except Exception as e:
        logger.error(f"Error installing plugin {request_body.name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"message": str(e)})


@admin_router.post("/plugins/rate")
async def rate_plugin(
    request_body: PluginRateRequest, user_id: str = Depends(get_user_id)
):
    API_REQUESTS.labels(endpoint="/admin/plugins/rate", method="POST").inc()
    try:
        marketplace = PluginMarketplace(db=omnicore_engine.database)
        await marketplace.rate_plugin(
            request_body.kind,
            request_body.name,
            request_body.version,
            request_body.rating,
            request_body.comment,
            user_id,
        )
        return {"status": "success", "message": f"Plugin {request_body.name} rated."}
    except ValueError as ve:
        logger.warning(f"Plugin rating failed due to invalid input: {ve}")
        raise HTTPException(status_code=400, detail={"message": str(ve)})
    except Exception as e:
        logger.error(f"Error rating plugin {request_body.name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"message": str(e)})


@admin_router.get("/audit/export-proof-bundle")
async def export_audit_proof_bundle(
    tenant_id: Optional[str] = Query(
        None, description="Optional tenant ID to filter audit records"
    ),
    user_id: str = Depends(get_user_id),
):
    API_REQUESTS.labels(endpoint="/admin/audit/export-proof-bundle", method="GET").inc()
    if not omnicore_engine.audit:
        raise HTTPException(status_code=500, detail="Audit system not initialized.")

    try:
        proof_bundle = await omnicore_engine.audit.proof_exporter.export_proof_bundle(
            user_id, tenant_id
        )
        return safe_jsonify({"status": "success", "data": proof_bundle})
    except ValueError as ve:
        logger.warning(f"Audit export denied for user {user_id}: {ve}")
        raise HTTPException(status_code=403, detail={"message": str(ve)})
    except Exception as e:
        logger.error(f"Audit proof bundle export failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"message": "Internal server error during export."}
        )


@admin_router.get("/generate-test-cases")
async def generate_test_cases(user_id: str = Depends(get_user_id)):
    API_REQUESTS.labels(endpoint="/admin/generate-test-cases", method="GET").inc()
    if meta_supervisor_instance is None:
        raise HTTPException(status_code=500, detail="MetaSupervisor not initialized.")

    try:
        result = await meta_supervisor_instance.generate_test_cases()
        return {
            "status": "success",
            "message": "Test cases generated successfully.",
            "result": result,
        }
    except Exception as e:
        logger.error(f"Test case generation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"message": "Internal server error during test case generation."},
        )


app.include_router(admin_router, dependencies=[Depends(verify_admin_api_enabled)])
app.include_router(router)


@app.post("/fix-imports/")
async def fix_imports(file: UploadFile = Depends(validate_upload)):
    """
    Exposes the AI-powered import fixer via an HTTP endpoint.
    """
    try:
        if AIManager is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "AI Import Fixer is not available. Required module not installed."
                },
            )

        ai_manager = AIManager()

        code = await file.read()

        suggestion = ai_manager.get_refactoring_suggestion(code.decode())

        return {"suggestion": suggestion}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during import fixing: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"message": str(e)})


# ============================================================================
# AUDIT CONFIGURATION ENDPOINTS
# ============================================================================

@router.get("/audit/config/status")
async def get_omnicore_audit_config_status() -> Dict[str, Any]:
    """
    Get OmniCore audit configuration status.
    
    Returns OmniCore-specific audit configuration including routing,
    DLT integration, and module-specific settings.
    """
    import yaml
    from pathlib import Path
    
    logger.info("Fetching OmniCore audit configuration status")
    
    config_info = {
        "module": "omnicore",
        "config_source": "environment_variables",
        "config_file": None,
        "omnicore_config": {},
        "integration": {},
        "routing": {},
        "features": {},
    }
    
    # Check for config file
    config_path = Path("omnicore_engine/audit_config.yaml")
    if config_path.exists():
        config_info["config_source"] = "yaml_file"
        config_info["config_file"] = str(config_path)
        try:
            with open(config_path) as f:
                config_data = yaml.safe_load(f) or {}
                config_info["omnicore_config"] = {
                    "audit_log_path": config_data.get("AUDIT_LOG_PATH", "./logs/omnicore_audit.jsonl"),
                    "rotation": config_data.get("AUDIT_LOG_ROTATION", "midnight"),
                    "retention_days": config_data.get("AUDIT_LOG_RETENTION", 30),
                    "compression": config_data.get("AUDIT_LOG_COMPRESSION", "gzip"),
                }
        except Exception as e:
            logger.error(f"Error loading config: {e}")
    
    # Integration status
    config_info["integration"] = {
        "main_audit_system": "http://localhost:8003",
        "routing_enabled": True,
        "omnicore_ingestion": "http://localhost:8001/audit/ingest",
    }
    
    # Features
    config_info["features"] = {
        "dlt_enabled": os.getenv("DLT_ENABLED", "false").lower() == "true",
        "prometheus_enabled": True,
        "prometheus_port": 9091,
    }
    
    config_info["documentation"] = {
        "config_file": "omnicore_engine/audit_config.yaml",
        "routing": "audit_routing_config.yaml",
        "docs": "docs/AUDIT_CONFIGURATION.md",
    }
    
    return config_info


@router.post("/audit/ingest")
async def ingest_audit_log(log_entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ingest audit log from other modules.
    
    Receives audit logs from Generator and SFE modules for centralized processing.
    """
    try:
        required_fields = ["source_module", "event_type"]
        missing = [f for f in required_fields if f not in log_entry]
        if missing:
            raise HTTPException(400, f"Missing fields: {missing}")
        
        ingestion_id = f"ing_{int(time.time() * 1000)}"
        log_entry["ingestion_id"] = ingestion_id
        log_entry["ingestion_timestamp"] = datetime.datetime.utcnow().isoformat()
        
        routed_to = []
        if omnicore_engine.audit:
            try:
                await omnicore_engine.audit.log_action(
                    action=log_entry.get("event_type"),
                    details=log_entry.get("details", {}),
                    user=log_entry.get("user", "system"),
                    job_id=log_entry.get("job_id"),
                )
                routed_to.append("omnicore_audit")
            except Exception as e:
                logger.error(f"Error routing to OmniCore audit: {e}")
        
        logger.info(f"Audit log ingested: {ingestion_id}")
        
        return {
            "status": "accepted",
            "ingestion_id": ingestion_id,
            "routed_to": routed_to,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error ingesting audit log: {e}")
        raise HTTPException(500, str(e))
