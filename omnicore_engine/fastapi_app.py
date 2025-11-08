# File: omnicore_engine/fastapi_app.py
import sys
import os
from pathlib import Path
import ast

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from dotenv import load_dotenv
load_dotenv()

import json
import uuid
import asyncio
import aiofiles
from typing import Dict, Any, Optional, List, Tuple, Union
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends, BackgroundTasks, status, APIRouter, Query, Response, UploadFile
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from prometheus_client import make_asgi_app
import traceback
import hashlib
import datetime
import importlib.util
import inspect
import numpy as np
from pydantic import BaseModel
from cryptography.fernet import Fernet
import redis.asyncio as redis
import time
import jwt
import aiohttp
import functools
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError

# Corrected imports to use the centralized OmniCore Engine singletons
from omnicore_engine.core import logger, safe_serialize, omnicore_engine, settings
from omnicore_engine.database import Database
from omnicore_engine.audit import ExplainAudit
from omnicore_engine.plugin_registry import PLUGIN_REGISTRY, PluginMeta, PlugInKind, PluginMarketplace
from omnicore_engine.meta_supervisor import MetaSupervisor
from omnicore_engine.config.legal_tender_settings import settings as ArbiterConfig
from simulations.simulation_module import UnifiedSimulationModule
from self_healing_import_fixer.import_fixer.fixer_ai import AIManager
from omnicore_engine.message_bus.message_types import Message
from omnicore_engine.metrics import API_REQUESTS, API_ERRORS

# Using functools.partial to create a callable that mimics the plugin's interface
# This is a good practice for dynamic plugin execution.
# from arbiter.arbiter import Arbiter as RealArbiter
# from omnicore_engine.fastapi_app import trigger_test_generation_via_omnicore
# from omnicore_engine.fastapi_app import run_test_generation_plugin
# from arbiter.arbiter_plugin_registry import PLUGIN_REGISTRY

try:
    # Updated imports to reflect the new arbiter package structure
    from arbiter.explainable_reasoner import ExplainableReasonerPlugin
    from arbiter.policy.core import PolicyEngine
    from omnicore_engine.feedback_manager import FeedbackManager, FeedbackType
    from arbiter.arbiter import Arbiter
    from arbiter.knowledge_loader import KnowledgeLoader
    from arbiter.arena import ArbiterArena
    from omnicore_engine.merkle_tree import MerkleTree
    import sqlalchemy

    ARBITER_AVAILABLE = True
    ARENA_AVAILABLE = True
    MERKLE_TREE_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Could not import all core engine components for FastAPI: {e}. Some features will be mocked.")
    
    ARBITER_AVAILABLE = False
    ARENA_AVAILABLE = False
    MERKLE_TREE_AVAILABLE = False

    class ExplainableReasonerPlugin:
        def __init__(self, *args, **kwargs): pass
        async def explain(self, *args, **kwargs): return "Mock explanation."
    class PolicyEngine:
        def __init__(self, *args, **kwargs): pass
        async def should_auto_learn(self, *args, **kwargs): return True, "Mock Policy"
    class FeedbackManager:
        def __init__(self, *args, **kwargs): pass
        async def initialize(self): pass
        async def record_feedback(self, *args, **kwargs): pass
    class FeedbackType:
        BUG_REPORT = "bug_report"
        GENERAL = "general"
        MOOD_CORRECTION = "mood_correction"
        FEATURE_REQUEST = "feature_request"
    class Arbiter:
        def __init__(self, *args, **kwargs): pass
        async def start_async_services(self): pass
        async def stop_async_services(self): pass
        async def respond(self, *args, **kwargs): return "Chatbot unavailable"
    class KnowledgeLoader:
        def load_all(self): pass
        def inject_to_arbiter(self, arbiter): pass
    class ArbiterArena:
        def __init__(self, *args, **kwargs): pass
        async def start_arena_services(self, *args, **kwargs): pass
        async def run_scan(self, codebase_path: str): return {"status": "mock_scan", "results": "mock_results"}
        async def generate_test_cases(self, *args, **kwargs): return "Mock test cases generated."
    class MerkleTree:
        def __init__(self, leaves: Optional[List[bytes]] = None, *args, **kwargs):
            self._mock_root = b"mock_merkle_root"
            self.leaves_data = leaves or []
        def _recalculate_root(self): self._mock_root = b"mock_recalculated_root"
        def add_leaf(self, leaf: bytes, key: Optional[bytes] = None) -> None: self.leaves_data.append(leaf)
        def get_root(self) -> bytes: return self._mock_root
        def get_merkle_root(self) -> str: return self._mock_root.hex()
        def make_tree(self): self._recalculate_root()
    class UnifiedSimulationModule:
        def __init__(self, *args, **kwargs): pass
        async def initialize(self): pass
    import sqlalchemy

chatbot_arbiter: Optional[Arbiter] = None
arena: Optional[ArbiterArena] = None
simulation_module: Optional[UnifiedSimulationModule] = None
_db_engine = None
system_audit_merkle_tree: MerkleTree = None
app = FastAPI(
    title="OmniCore Omega Pro Engine API",
    description="Universal orchestration engine with audit and AI integration",
    version=settings.LOG_LEVEL,
    docs_url=None,
    redoc_url=None
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
app.mount("/metrics", make_asgi_app())
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")

class SizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.headers.get('content-length'):
            if int(request.headers['content-length']) > 10_000_000:  # 10MB
                return JSONResponse(status_code=413, content={"error": "Request too large"})
        return await call_next(request)

# In fastapi_app.py, add security middleware
from security_config import get_security_config
from security_utils import get_security_utils, RateLimiter

security_config = get_security_config()
security_utils = get_security_utils()
rate_limiter = RateLimiter()

# Add authentication middleware
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
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*.yourdomain.com", "localhost", "127.0.0.1"])

@app.exception_handler(CsrfProtectError)
def csrf_protect_exception_handler(request: Request, exc: CsrfProtectError):
    return JSONResponse(status_code=403, content={"error": "CSRF validation failed"})

@CsrfProtect.load_config
def get_csrf_config():
    class CsrfConfig:
        secret_key = settings.JWT_SECRET_KEY.get_secret_value()
    return CsrfConfig()

async def get_user_id(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY.get_secret_value(), algorithms=["HS256"])
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
encrypter = Fernet(settings.ENCRYPTION_KEY.get_secret_value().encode('utf-8'))
meta_supervisor_instance = None


@app.on_event("startup")
async def startup_event_fastapi():
    global chatbot_arbiter, arena, system_audit_merkle_tree, meta_supervisor_instance, simulation_module
    
    await omnicore_engine.initialize()

    try:
        if omnicore_engine.database and omnicore_engine.message_bus:
            simulation_module = UnifiedSimulationModule(
                config=settings,
                db=omnicore_engine.database,
                message_bus=omnicore_engine.message_bus
            )
            await simulation_module.initialize()
            logger.info("UnifiedSimulationModule initialized successfully.")
        else:
            logger.warning("Database or MessageBus not available, skipping UnifiedSimulationModule initialization.")
            simulation_module = UnifiedSimulationModule(config=settings, db=None, message_bus=None)
    except Exception as e:
        logger.error(f"Failed to initialize UnifiedSimulationModule: {e}", exc_info=True)
        simulation_module = UnifiedSimulationModule(config=settings, db=None, message_bus=None)

    try:
        if MERKLE_TREE_AVAILABLE:
            system_audit_merkle_tree = MerkleTree(
                leaves=None,
                branching_factor=settings.MERKLE_TREE_BRANCHING_FACTOR,
                private_key=settings.MERKLE_TREE_PRIVATE_KEY.get_secret_value().encode() if settings.MERKLE_TREE_PRIVATE_KEY else None
            )
            system_audit_merkle_tree.make_tree()
            logger.info(f"System audit Merkle tree initialized. Initial root: {system_audit_merkle_tree.get_merkle_root()}")
        else:
            system_audit_merkle_tree = MerkleTree()
            logger.warning("MerkleTree not available, using mock.")
        
        if ARBITER_AVAILABLE:
            arbiter_db_client = Database(settings.database_path, system_audit_merkle_tree=system_audit_merkle_tree)
            arbiter_feedback_manager = FeedbackManager(
                db_dsn=settings.database_path,
                redis_url=settings.redis_url,
                encryption_key=settings.ENCRYPTION_KEY.get_secret_value()
            )
            await arbiter_feedback_manager.initialize()

            if not omnicore_engine.crew_manager:
                 raise RuntimeError("CrewManager not initialized on omnicore_engine.")

            # New: Pass the simulation module instance to the Arbiter
            if not omnicore_engine.test_generation_orchestrator:
                logger.warning("TestGenerationOrchestrator not available. Arbiter's test generation capability will be limited.")

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
                db_engine=arbiter_db_client.engine, # Corrected: Pass the engine, not the client
                feedback_manager=arbiter_feedback_manager,
                crew_manager=omnicore_engine.crew_manager,
                engines=available_engines # Pass the collected engines
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
                db_engine=omnicore_engine.database.engine
            )
            await arena.start_arena_services(http_port=settings.ARENA_PORT)
            logger.info(f"AI assistant arena services started on port {settings.ARENA_PORT}.")
        else:
            logger.warning("AI assistant arena is not available.")

        meta_supervisor_instance = MetaSupervisor(interval=300, backend_mode="torch", use_quantum=True)
        await meta_supervisor_instance.initialize()
        asyncio.create_task(meta_supervisor_instance.run())
        logger.info("MetaSupervisor initialized and background task started.")

        logger.info(f"FastAPI app startup complete. OmniCore Engine ready.")
    except Exception as e:
        logger.critical(f"FastAPI startup failed: {e}", exc_info=True)
        raise

@app.on_event("shutdown")
async def shutdown_event_fastapi():
    global chatbot_arbiter, arena, meta_supervisor_instance, simulation_module
    
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


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(openapi_url=app.openapi_url, title=app.title + " - Swagger UI")

@app.get("/redoc", include_in_schema=False)
async def custom_redoc_html():
    return get_redoc_html(openapi_url=app.openapi_url, title=app.title + " - ReDoc")

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
        return JSONResponse(content=json.loads(json.dumps(data, default=safe_serialize)))

ALLOWED_EXTENSIONS = {'.py', '.json', '.yaml', '.yml'}
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
    
    if ext == '.py':
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
            raise HTTPException(status_code=500, detail="TestGenerationOrchestrator is not initialized.")
        
        response = await omnicore_engine.test_generation_orchestrator.generate_tests_for_targets(
            targets=request.targets,
            config=request.config
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
        raise HTTPException(status_code=500, detail={"message": f"Internal error calling plugin: {e}"})


@router.post("/simulation/execute")
async def execute_simulation(request: Request):
    """
    Executes a simulation using the simulation engine.
    """
    global simulation_module
    if not simulation_module:
        raise HTTPException(status_code=500, detail="Simulation engine is not initialized.")
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
    global simulation_module
    if not simulation_module:
        raise HTTPException(status_code=500, detail="Simulation engine is not initialized.")
    try:
        result = await request.json()
        explanation = await simulation_module.explain_result(result)
        return {"status": "success", "explanation": explanation}
    except Exception as e:
        logger.error(f"Error explaining simulation result: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"message": str(e)})

@router.post("/notify")
async def notify(request: Request):
    API_REQUESTS.labels(endpoint="/notify").inc()
    start_time = time.time()
    try:
        data = await request.json()
        logger.info(f"Received UI notification: {data.get('message')} (Type: {data.get('type')})")
        return {"status": "received", "data": data}
    except Exception as e:
        API_ERRORS.labels(endpoint="/notify").observe(time.time() - start_time)
        logger.error(f"Error in /notify: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"message": str(e)})

@router.post("/chat", response_model=ChatResponse)
async def chat_with_bot(chat_request: ChatRequest):
    API_REQUESTS.labels(endpoint="/chat").inc()
    start_time = time.time()
    if not ARBITER_AVAILABLE:
        return ChatResponse(response="Chatbot unavailable.", status="error", message="AI assistant not loaded")
    try:
        global chatbot_arbiter
        if chatbot_arbiter is None:
            logger.error("AI assistant is None. Cannot respond.")
            return ChatResponse(response="Chatbot is not initialized.", status="error", message="Chatbot initialization error.")

        chatbot_response = await chatbot_arbiter.respond(
            user_id=chat_request.user_id,
            message=chat_request.message,
            context=chat_request.context
        )
        return ChatResponse(response=chatbot_response, status="success")
    except Exception as e:
        API_ERRORS.labels(endpoint="/chat").observe(time.time() - start_time)
        logger.error(f"Chatbot response error: {e}", exc_info=True)
        return ChatResponse(response="Error processing request.", status="error", message=str(e))

@router.post("/arbiter/analyze-code")
async def analyze_code(codebase_path: str):
    if not ARENA_AVAILABLE:
        raise HTTPException(status_code=500, detail="Arbiter Arena not available.")
    settings = ArbiterConfig()
    arena = ArbiterArena(
        name="CodebaseAnalyzerArena",
        port=settings.ARENA_PORT,
        settings=settings,
        db_engine=omnicore_engine.database.engine
    )
    result = await arena.run_scan(codebase_path)
    return safe_jsonify({"status": "success", "result": result})

@router.get("/health")
async def health_check_api():
    API_REQUESTS.labels(endpoint="/health").inc()
    return await omnicore_engine.health_check()

@app.post("/code-factory-workflow")
async def code_factory_workflow(request: Request, user_id: str = Depends(get_user_id)):
    API_REQUESTS.labels(endpoint="/code-factory-workflow").inc()
    payload = await request.json()
    message = Message(topic="start_workflow", payload=payload)
    await omnicore_engine.message_bus.publish(message.topic, message.payload)
    return {"status": "workflow_started", "trace_id": message.trace_id}

admin_router = APIRouter(prefix="/admin")

async def verify_admin_api_enabled():
    if not settings.EXPERIMENTAL_FEATURES_ENABLED:
        logger.warning(f"Attempted access to disabled admin API.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin API is not enabled.")

@admin_router.get("/feature-flag")
async def get_feature_flag(flag_name: Optional[str] = Query(None, description="Specific feature flag name to retrieve")):
    API_REQUESTS.labels(endpoint="/admin/feature-flag_get").inc()
    return {"status": "not_implemented", "message": "Feature flag management to be implemented."}


@admin_router.post("/feature-flag")
async def set_feature_flag(flag_name: str, request_body: FeatureFlagUpdateRequest, user_id: str = Depends(get_user_id)):
    API_REQUESTS.labels(endpoint="/admin/feature-flag_post").inc()
    return {"status": "not_implemented", "message": "Feature flag management to be implemented."}

@admin_router.post("/plugins/install")
async def install_plugin(request_body: PluginInstallRequest, user_id: str = Depends(get_user_id)):
    API_REQUESTS.labels(endpoint="/admin/plugins/install").inc()
    try:
        marketplace = PluginMarketplace(db=omnicore_engine.database)
        await marketplace.install_plugin(request_body.kind, request_body.name, request_body.version)
        return {"status": "success", "message": f"Plugin {request_body.name} (v{request_body.version}) installed."}
    except ValueError as ve:
        logger.warning(f"Plugin installation failed due to invalid input: {ve}")
        raise HTTPException(status_code=400, detail={"message": str(ve)})
    except Exception as e:
        logger.error(f"Error installing plugin {request_body.name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"message": str(e)})

@admin_router.post("/plugins/rate")
async def rate_plugin(request_body: PluginRateRequest, user_id: str = Depends(get_user_id)):
    API_REQUESTS.labels(endpoint="/admin/plugins/rate").inc()
    try:
        marketplace = PluginMarketplace(db=omnicore_engine.database)
        await marketplace.rate_plugin(
            request_body.kind, request_body.name, request_body.version, 
            request_body.rating, request_body.comment, user_id
        )
        return {"status": "success", "message": f"Plugin {request_body.name} rated."}
    except ValueError as ve:
        logger.warning(f"Plugin rating failed due to invalid input: {ve}")
        raise HTTPException(status_code=400, detail={"message": str(ve)})
    except Exception as e:
        logger.error(f"Error rating plugin {request_body.name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"message": str(e)})

@admin_router.get("/audit/export-proof-bundle")
async def export_audit_proof_bundle(tenant_id: Optional[str] = Query(None, description="Optional tenant ID to filter audit records"), user_id: str = Depends(get_user_id)):
    API_REQUESTS.labels(endpoint="/admin/audit/export-proof-bundle").inc()
    if not omnicore_engine.audit:
        raise HTTPException(status_code=500, detail="Audit system not initialized.")
    
    try:
        proof_bundle = await omnicore_engine.audit.proof_exporter.export_proof_bundle(user_id, tenant_id)
        return safe_jsonify({"status": "success", "data": proof_bundle})
    except ValueError as ve:
        logger.warning(f"Audit export denied for user {user_id}: {ve}")
        raise HTTPException(status_code=403, detail={"message": str(ve)})
    except Exception as e:
        logger.error(f"Audit proof bundle export failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"message": "Internal server error during export."})


@admin_router.get("/generate-test-cases")
async def generate_test_cases(user_id: str = Depends(get_user_id)):
    API_REQUESTS.labels(endpoint="/admin/generate-test-cases").inc()
    global meta_supervisor_instance
    if meta_supervisor_instance is None:
        raise HTTPException(status_code=500, detail="MetaSupervisor not initialized.")
    
    try:
        result = await meta_supervisor_instance.generate_test_cases()
        return {"status": "success", "message": "Test cases generated successfully.", "result": result}
    except Exception as e:
        logger.error(f"Test case generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"message": "Internal server error during test case generation."})

app.include_router(admin_router, dependencies=[Depends(verify_admin_api_enabled)])
app.include_router(router)


@app.post("/fix-imports/")
async def fix_imports(file: UploadFile = Depends(validate_upload)):
    """
    Exposes the AI-powered import fixer via an HTTP endpoint.
    """
    try:
        ai_manager = AIManager()
        
        code = await file.read()
        
        suggestion = ai_manager.get_refactoring_suggestion(code.decode())
        
        return {"suggestion": suggestion}
    except Exception as e:
        logger.error(f"Error during import fixing: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"message": str(e)})