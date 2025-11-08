# SFE_Code_Guardian/arena.py

import asyncio
import random
import logging
import json
import aiohttp
import jwt
import os
import signal
import time
import threading
from typing import List, Dict, Any, Optional, Callable, Coroutine, Tuple
from fastapi import FastAPI, Request, HTTPException, APIRouter, Depends
from fastapi.responses import JSONResponse
# REMOVED: Direct import from prometheus_client (PromCounter, PromGauge, REGISTRY)
from prometheus_client import Counter, Gauge, REGISTRY
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from datetime import datetime, date, timedelta
from urllib.parse import urlparse
from tenacity import retry, stop_after_attempt, wait_exponential
import secrets
from functools import wraps

__all__ = ["ArbiterArena"]

# Configure basic logging for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Mock/Plausholder Implementations for Standalone Operation ---
logger.warning("Using mock/placeholder implementations for external components.")
class MockSimulationModule:
    @staticmethod
    def get_tools(): return {"mock_auto_fixer": lambda x: "fixed"}
    @staticmethod
    def is_available(): return True
    async def run_simulation(self, *args, **kwargs):
        return {"status": "mock_sim_complete"}
SimulationModule = MockSimulationModule

# Import core components with ABSOLUTE PATHS
from arbiter.config import ArbiterConfig
from arbiter.codebase_analyzer import CodebaseAnalyzer
from arbiter.agent_state import Base
from arbiter.feedback import FeedbackManager
from arbiter.human_loop import HumanInLoop, HumanInLoopConfig
from arbiter.monitoring import Monitor
from arbiter.arbiter import Arbiter # Correct import
from arbiter.arbiter_plugin_registry import PLUGIN_REGISTRY, PlugInKind
from arbiter.logging_utils import PIIRedactorFilter
from arbiter.otel_config import get_tracer
# NEW: Import metric creation helpers from arbiter.metrics
from arbiter.metrics import (
    get_or_create_counter,
    get_or_create_gauge,
)


tracer = get_tracer(__name__)

# PII Redaction Filter
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.addFilter(PIIRedactorFilter())
    logger.addHandler(handler)


JWT_SECRET_FALLBACK = "your-arena-jwt-secret-fallback-if-config-not-loaded"


# --- Helper functions for idempotent and thread-safe metric creation ---
_metrics_lock = threading.Lock() # Note: This lock is technically not needed here anymore, but keeping it for structure.

# REFACTORED: Now wraps the imported get_or_create_counter
def get_or_create_prom_counter(name: str, documentation: str, labelnames: Tuple[str, ...] = ()):
    # The actual locking/creation logic is now deferred to arbiter.metrics
    return get_or_create_counter(name, documentation, labelnames)

# REFACTORED: Now wraps the imported get_or_create_gauge
def get_or_create_prom_gauge(name: str, documentation: str, labelnames: Tuple[str, ...] = ()):
    # The actual locking/creation logic is now deferred to arbiter.metrics
    return get_or_create_gauge(name, documentation, labelnames)

# FIXED: Use the renamed helper functions (which now wrap the imported metric functions)
# Use idempotent and thread-safe metric creation
scan_repair_cycles_total = get_or_create_prom_counter(
    'scan_repair_cycles_total', 
    'Total scan/repair cycles executed'
)
defects_found_total = get_or_create_prom_counter(
    'defects_found_total', 
    'Total number of defects found in the codebase', 
    ['defect_type']
)
repairs_attempted_total = get_or_create_prom_counter(
    'repairs_attempted_total', 
    'Total number of repair attempts by arbiters', 
    ['arbiter_name', 'repair_strategy']
)
repairs_successful_total = get_or_create_prom_counter(
    'repairs_successful_total', 
    'Total successful repairs', 
    ['arbiter_name', 'repair_strategy']
)
agent_evolutions_total = get_or_create_prom_counter(
    'agent_evolutions_total', 
    'Total number of agent evolution/mutation events', 
    ['arbiter_name']
)
# FIXED: Use the renamed helper function
active_arbiters = get_or_create_prom_gauge('arena_active_arbiters', 'Number of active arbiters in the arena')
arena_ops_total = get_or_create_prom_counter("arena_ops_total", "Total arena operations", ["operation"])
arena_errors_total = get_or_create_prom_counter("arena_errors_total", "Total arena errors", ["error_type"])
_metrics_lock = threading.Lock()

def get_or_create_counter(name: str, documentation: str, labelnames: Tuple[str, ...] = ()):
    with _metrics_lock:
        try:
            collector = REGISTRY._names_to_collectors.get(name)
            if collector and isinstance(collector, Counter):
                return collector
            return Counter(name, documentation, labelnames=labelnames)
        except ValueError:
            logger.warning(f"Metric '{name}' already registered. Reusing existing instance.")
            return REGISTRY._names_to_collectors[name]
        except Exception as e:
            logger.error(f"Error getting or creating Counter '{name}': {e}")
            raise

def get_or_create_gauge(name: str, documentation: str, labelnames: Tuple[str, ...] = ()):
    with _metrics_lock:
        try:
            collector = REGISTRY._names_to_collectors.get(name)
            if collector and isinstance(collector, Gauge):
                return collector
            return Gauge(name, documentation, labelnames=labelnames)
        except ValueError:
            logger.warning(f"Metric '{name}' already registered. Reusing existing instance.")
            return REGISTRY._names_to_collectors[name]
        except Exception as e:
            logger.error(f"Error getting or creating Gauge '{name}': {e}")
            raise

# Use idempotent and thread-safe metric creation
scan_repair_cycles_total = get_or_create_counter('scan_repair_cycles_total', 'Total scan/repair cycles executed')
defects_found_total = get_or_create_counter('defects_found_total', 'Total number of defects found in the codebase', ['defect_type'])
repairs_attempted_total = get_or_create_counter('repairs_attempted_total', 'Total number of repair attempts by arbiters', ['arbiter_name', 'repair_strategy'])
repairs_successful_total = get_or_create_counter('repairs_successful_total', 'Total successful repairs', ['arbiter_name', 'repair_strategy'])
agent_evolutions_total = get_or_create_counter('agent_evolutions_total', 'Total number of agent evolution/mutation events', ['arbiter_name'])
active_arbiters = get_or_create_gauge('arena_active_arbiters', 'Number of active arbiters in the arena')
arena_ops_total = get_or_create_counter("arena_ops_total", "Total arena operations", ["operation"])
arena_errors_total = get_or_create_counter("arena_errors_total", "Total arena errors", ["error_type"])

def require_auth(func: Callable) -> Callable:
    """
    Authenticates API requests using JWT token from the Authorization header.
    
    Raises:
        HTTPException: If the token is missing, invalid, or the role is unauthorized.
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        request: Request = next((arg for arg in args if isinstance(arg, Request)), None)
        if not request:
            raise HTTPException(status_code=400, detail="Request object missing.")
        
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise HTTPException(status_code=401, detail="Authorization header missing.")
        
        try:
            scheme, token = auth_header.split()
            if scheme.lower() != "bearer":
                raise HTTPException(status_code=401, detail="Invalid authentication scheme. Must be 'Bearer'.")
            
            settings = kwargs.get('settings')
            if settings is None:
                settings = ArbiterConfig.initialize()
            
            jwt_secret_value = settings.ARENA_JWT_SECRET.get_secret_value() if settings.ARENA_JWT_SECRET else JWT_SECRET_FALLBACK
            payload = jwt.decode(token, jwt_secret_value, algorithms=["HS256"])
            
            if payload.get("role") not in ["admin", "user"]:
                raise HTTPException(status_code=403, detail="Insufficient privileges.")
                
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid or expired authentication token.")
        except Exception as e:
            logger.error(f"Authentication failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Authentication service error.")
            
        return await func(*args, **kwargs)
    return wrapper

class ArbiterArena:
    def __init__(self, settings: ArbiterConfig, port: Optional[int] = None, name: Optional[str] = None, db_engine: Optional[Any] = None, intent_capture_engine: Optional[Any] = None, **kwargs):
        self.settings = settings
        self.name = name or "DefaultCodeGuardianArena"
        self.version = "1.1.0"
        self.base_port = port if port is not None else 9001
        self.num = kwargs.get('num', 3)
        self.arbiters: List[Arbiter] = []
        self._lock = asyncio.Lock()
        self._db_engine = db_engine
        self.session_maker = async_sessionmaker(self._db_engine, expire_on_commit=False) if self._db_engine else None
        self.codebase_map = {}
        self.app = FastAPI(title=f"Arbiter Arena API - {self.name}", version=self.version)
        self._current_arbiter = 0
        self.http_port = self.settings.ARENA_PORT

        # NEW: Storing the intent capture engine for later use
        self.intent_capture_engine = intent_capture_engine
        
        # Replace mock with a pluggable component from the registry
        self.simulation_module = PLUGIN_REGISTRY.get(PlugInKind.CORE_SERVICE, "simulation_module") or SimulationModule()
        if not self.simulation_module:
            logger.error("SimulationModule not found in plugin registry. Falling back to mock.")
            self.simulation_module = MockSimulationModule()

        self.analyzer = CodebaseAnalyzer(
            root_dir=self.settings.REPORTS_DIRECTORY
        )
        
        self.feedback = FeedbackManager(
            config=self.settings,
            log_file=os.path.join(self.settings.REPORTS_DIRECTORY, "feedback_log.json")
        )

        hitl_config = HumanInLoopConfig(
            DATABASE_URL=self.settings.DB_PATH,
            IS_PRODUCTION=True,
            EMAIL_ENABLED=self.settings.EMAIL_ENABLED,
            EMAIL_SMTP_SERVER=self.settings.EMAIL_SMTP_SERVER,
            EMAIL_SMTP_PORT=self.settings.EMAIL_SMTP_PORT,
            EMAIL_SMTP_USER=self.settings.EMAIL_SMTP_USERNAME,
            EMAIL_SMTP_PASSWORD=self.settings.EMAIL_SMTP_PASSWORD,
            EMAIL_SENDER=self.settings.EMAIL_SENDER,
            EMAIL_USE_TLS=self.settings.EMAIL_USE_TLS,
            EMAIL_RECIPIENTS={"reviewer": self.settings.EMAIL_RECIPIENTS},
            SLACK_WEBHOOK_URL=self.settings.SLACK_WEBHOOK_URL
        )

        self.human_in_loop = HumanInLoop(
            config=hitl_config,
            feedback_manager=self.feedback
        )
        
        self.monitor = Monitor(
            log_file=os.path.join(self.settings.REPORTS_DIRECTORY, f"{self.name}_arena_monitor_log.json")
        )
        
        self.codebase_entry_points = [
            str(p).rstrip('/') for p in self.settings.CODEBASE_PATHS
        ] if hasattr(self.settings, 'CODEBASE_PATHS') and self.settings.CODEBASE_PATHS else ["./src"]

        self._setup_error_handlers()
        self._initialize_arbiters()
        logger.info(f"Arbiter Arena '{self.name}' v{self.version} initialized with {len(self.arbiters)} arbiters.")

    async def __aenter__(self):
        """Asynchronous context manager entry point. Starts all services."""
        await self.start_arena_services(http_port=self.http_port)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Asynchronous context manager exit point. Stops all services."""
        await self.stop_all()
        if self._db_engine:
            await self._db_engine.dispose()
        logger.info(f"[{self.name}] Arena closed.")

    async def _send_webhook(self, event_type: str, data: Dict):
        """Sends a webhook notification to a configured URL for orchestration."""
        webhook_url = getattr(self.settings, 'WEBHOOK_URL', None)
        if not webhook_url:
            return

        payload = {
            "event_type": event_type,
            "source_arena": self.name,
            "timestamp": datetime.utcnow().isoformat(),
            "data": data
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=payload, timeout=10) as response:
                    if response.status >= 300:
                        logger.warning(f"Webhook for event '{event_type}' failed with status {response.status}.")
        except Exception as e:
            logger.error(f"Error sending webhook for event '{event_type}': {e}", exc_info=True)
            arena_errors_total.labels(error_type="webhook_fail").inc()

    def _setup_error_handlers(self):
        """Adds custom exception handlers to the FastAPI app for consistent error responses."""
        @self.app.exception_handler(HTTPException)
        async def http_exception_handler(request: Request, exc: HTTPException):
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": "ClientError", "message": exc.detail},
            )

        @self.app.exception_handler(Exception)
        async def generic_exception_handler(request: Request, exc: Exception):
            logger.error(f"An unhandled error occurred on request to {request.url}: {exc}", exc_info=True)
            arena_errors_total.labels(error_type="unhandled_exception").inc()
            return JSONResponse(
                status_code=500,
                content={"error": "ServerError", "message": "An internal server error occurred. Please check logs."},
            )

    async def _update_and_persist_map(self, new_map_data: Dict, source: str):
        """Atomically updates the in-memory map and persists it to a file."""
        async with self._lock:
            self.codebase_map.update(new_map_data)
            try:
                with open(f"{self.name}_codebase_map.json", "w") as f:
                    json.dump(self.codebase_map, f, indent=2, default=str)
                logger.info(f"Codebase map updated from '{source}' and persisted.")
                self.monitor.log_action({'type': 'codebase_map_persistence', 'source': source, 'status': 'success'})
            except Exception as e:
                logger.error(f"Failed to save codebase map from '{source}': {e}", exc_info=True)
                self.monitor.log_action({'type': 'codebase_map_persistence', 'source': source, 'status': 'failed', 'error': str(e)})
                arena_errors_total.labels(error_type="map_persistence_fail").inc()

    async def _create_initial_scan_coro(self):
        logger.info("Launching initial codebase scan.")
        await self._send_webhook("scan_started", {"scan_type": "initial"})
        
        scan_results = await self.analyzer.scan_codebase(self.codebase_entry_points)
        defect_results = await self.analyzer.find_defects()
        tool_issues = await self.analyzer.audit_repair_tools()
        
        codebase_map = {
            "file_tree": scan_results.get("files_scanned", []),
            "dependencies": await self.analyzer.map_dependencies(),
            "defects": defect_results.get("defects", []),
            "repair_history": [],
            "issues": tool_issues
        }

        await self._update_and_persist_map(codebase_map, "initial_scan")
        await self.feedback.record_metric("codebase_map_update", 1)

        await self.analyzer.cache_data()
        await self.analyzer.preload_models()
        await self.analyzer.clear_old_logs()
        self.monitor.log_action({'type': 'arena_initial_scan_complete', 'status': 'success'})
        await self._send_webhook("scan_completed", {"scan_type": "initial", "files_scanned": len(codebase_map["file_tree"])})
        logger.info("Initial codebase scan and learning complete.")

    async def _create_periodic_scan_coro(self):
        scan_interval = getattr(self.settings, 'PERIODIC_SCAN_INTERVAL_S', 3600)
        logger.info(f"Periodic scanner configured to run every {scan_interval} seconds.")
        while True:
            await asyncio.sleep(scan_interval)
            logger.info("Running scheduled codebase scan.")
            await self._send_webhook("scan_started", {"scan_type": "periodic"})

            scan_results = await self.analyzer.scan_codebase(self.codebase_entry_points)
            defect_results = await self.analyzer.find_defects()
            tool_issues = await self.analyzer.audit_repair_tools()

            codebase_map_update = {
                "file_tree": scan_results.get("files_scanned", []),
                "dependencies": await self.analyzer.map_dependencies(),
                "defects": defect_results.get("defects", []),
                "issues": tool_issues
            }
            
            await self._update_and_persist_map(codebase_map_update, "periodic_scan")
            await self.feedback.record_metric("scheduled_codebase_map_update", 1)

            await self.analyzer.cache_data()
            await self.analyzer.preload_models()
            await self.analyzer.clear_old_logs()
            self.monitor.log_action({'type': 'arena_scheduled_scan_complete', 'status': 'success'})
            await self._send_webhook("scan_completed", {"scan_type": "periodic", "files_scanned": len(codebase_map_update["file_tree"])})
            logger.info("Scheduled codebase scan complete.")

    def _initialize_arbiters(self):
        db_engine_for_arbiters = self._db_engine

        if db_engine_for_arbiters is None:
            logger.warning("No DB engine provided. Arbiters may fail to initialize state. Creating in-memory.")
            from sqlalchemy.ext.asyncio import create_async_engine
            db_engine_for_arbiters = create_async_engine("sqlite+aiosqlite:///:memory:")
            async def create_in_memory_tables():
                async with db_engine_for_arbiters.begin() as conn:
                    from arbiter.agent_state import Base
                    await conn.run_sync(Base.metadata.create_all)
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(create_in_memory_tables())
            except RuntimeError:
                asyncio.run(create_in_memory_tables())
            logger.info("Created in-memory database and agent_state table for arbiters.")

        world_size = getattr(self.settings, 'WORLD_SIZE', 3)
        self.arbiters = []

        for i in range(world_size):
            peer_ports = [self.base_port + j for j in range(world_size) if j != i]
            
            # This is the primary production integration point, updated to pass the new dependency.
            arbiter = Arbiter(
                name=f"Arbiter_{self.base_port + i}",
                db_engine=db_engine_for_arbiters,
                world_size=world_size,
                analyzer=self.analyzer,
                feedback_manager=self.feedback,
                human_in_loop=self.human_in_loop,
                monitor=self.monitor,
                port=self.base_port + i,
                peer_ports=peer_ports,
                settings=self.settings,
                intent_capture_engine=self.intent_capture_engine # NEW: Passing the dependency
            )
            self.arbiters.append(arbiter)
        logger.info(f"Initialized {len(self.arbiters)} arbiters in the arena structure.")
        self.monitor.log_action({'type': 'arena_arbiters_initialization_struct_only', 'num_arbiters': len(self.arbiters)})

    async def register(self, arbiter: Any):
        """Registers an arbiter with the arena."""
        async with self._lock:
            if arbiter not in self.arbiters:
                self.arbiters.append(arbiter)
                active_arbiters.set(len(self.arbiters))
                logger.info(f"Registered arbiter: {arbiter.name} (Total: {len(self.arbiters)})")
                arena_ops_total.labels(operation="register_arbiter").inc()

    async def remove(self, arbiter: Any):
        """Removes an arbiter from the arena."""
        async with self._lock:
            if arbiter in self.arbiters:
                self.arbiters.remove(arbiter)
                active_arbiters.set(len(self.arbiters))
                logger.info(f"Removed arbiter: {arbiter.name} (Remaining: {len(self.arbiters)})")
                await self._send_webhook("agent_removed", {"agent_name": arbiter.name})
                arena_ops_total.labels(operation="remove_arbiter").inc()

    async def get_random_arbiter(self) -> Arbiter:
        """Returns a random active arbiter from the arena."""
        async with self._lock:
            if not self.arbiters:
                raise ValueError("No arbiters available in the arena.")
            return random.choice(self.arbiters)
    
    async def distribute_task(self, task_coro: Callable, *args, **kwargs) -> Any:
        """Distributes a task to an arbiter using a round-robin strategy."""
        async with self._lock:
            if not self.arbiters:
                raise ValueError("No arbiters available to distribute tasks.")
            self._current_arbiter = (self._current_arbiter + 1) % len(self.arbiters)
            arbiter = self.arbiters[self._current_arbiter]
            return await task_coro(arbiter, *args, **kwargs)

    def _setup_routes(self):
        """Sets up FastAPI routes for the code guardian arena API."""
        self.router = APIRouter()

        @self.router.get("/health", summary="Get Service Health", tags=["Arena Operations"])
        @require_auth
        async def health_check_endpoint(request: Request, settings: ArbiterConfig = Depends(ArbiterConfig.initialize)):
            """Returns the current operational status of the Arena service."""
            with tracer.start_as_current_span("arena_health_check"):
                try:
                    health_data = {"arena": self.name, "status": "healthy", "arbiters": []}
                    for arbiter in self.arbiters:
                        arbiter_health = await arbiter.health_check()
                        health_data["arbiters"].append({"name": arbiter.name, "health": arbiter_health})
                    arena_ops_total.labels(operation="health_check").inc()
                    return JSONResponse(content=health_data)
                except Exception as e:
                    logger.error(f"Health check failed: {e}", exc_info=True)
                    arena_errors_total.labels(error_type="health_check").inc()
                    raise HTTPException(status_code=500, detail=str(e))
        
        @self.router.get("/version", summary="Get API Version", tags=["Arena Operations"])
        async def version_endpoint():
            """Returns the current version of the Arena service and its name."""
            return {"name": self.name, "version": self.version}
        
        @self.router.get("/status", summary="Get Full System Status", tags=["Arena Operations"])
        @require_auth
        async def status_endpoint(request: Request, settings: ArbiterConfig = Depends(ArbiterConfig.initialize)):
            """Retrieves the current status of the arena, its arbiters, and overall code health."""
            with tracer.start_as_current_span("arena_status_check"):
                return await self.handle_status(request)

        @self.router.post("/scan", summary="Trigger a Codebase Scan", tags=["Code Actions"])
        @require_auth
        async def scan_endpoint(request: Request, settings: ArbiterConfig = Depends(ArbiterConfig.initialize)):
            """Triggers an immediate, on-demand scan of the codebase."""
            with tracer.start_as_current_span("arena_manual_scan"):
                data = await request.json()
                paths = data.get("paths", self.codebase_entry_points)
                
                await self._send_webhook("scan_started", {"scan_type": "manual"})
                
                scan_results = await self.analyzer.scan_codebase(paths)
                defect_results = await self.analyzer.find_defects()
                tool_issues = await self.analyzer.audit_repair_tools()
                
                codebase_map_update = {
                    "file_tree": scan_results.get("files_scanned", []),
                    "dependencies": await self.analyzer.map_dependencies(),
                    "defects": defect_results.get("defects", []),
                    "issues": tool_issues
                }

                await self._update_and_persist_map(codebase_map_update, "manual_scan")
                await self._send_webhook("scan_completed", {"scan_type": "manual", "files_scanned": len(codebase_map_update["file_tree"])})
                
                arena_ops_total.labels(operation="manual_scan").inc()
                return {"message": "Codebase scan initiated and results processed", "scan_results": scan_results, "defect_results": defect_results, "tool_issues": tool_issues}

        @self.router.post("/repair", summary="Attempt a Code Repair", tags=["Code Actions"])
        @require_auth
        async def repair_endpoint(request: Request, settings: ArbiterConfig = Depends(ArbiterConfig.initialize)):
            """Dispatches a task to an arbiter to attempt a repair on a specific module."""
            with tracer.start_as_current_span("arena_manual_repair"):
                data = await request.json()
                target_module = data.get("module")
                if not target_module:
                    raise HTTPException(status_code=400, detail="A target 'module' must be specified for repair.")
                
                random_arbiter = await self.get_random_arbiter()
                repair_result = await random_arbiter.evolve(arena=self, target_module=target_module)
                
                if repair_result and isinstance(repair_result, dict):
                    self.codebase_map.setdefault("repair_history", []).append(repair_result)
                    await self._update_and_persist_map({}, "manual_repair")
                
                arena_ops_total.labels(operation="manual_repair").inc()
                return {"message": f"Repair task for '{target_module}' dispatched to {random_arbiter.name}.", "result": repair_result}

        @self.router.get("/history", summary="Get Repair History", tags=["Code Actions"])
        @require_auth
        async def history_endpoint(request: Request, settings: ArbiterConfig = Depends(ArbiterConfig.initialize)):
            """Returns the history of all repair attempts recorded by the Arena."""
            repair_history = self.codebase_map.get("repair_history", [])
            return {"repair_history": repair_history}
        
        @self.router.post("/scenarios/test_generation/run", summary="Trigger Test Generation", tags=["Code Actions"])
        @require_auth
        async def run_test_generation_endpoint(request: Request, settings: ArbiterConfig = Depends(ArbiterConfig.initialize)):
            """Triggers test generation for a specific arbiter with a given code snippet."""
            with tracer.start_as_current_span("arena_test_generation"):
                data = await request.json()
                arbiter_name = data.get("arbiter_name")
                code = data.get("code")
                language = data.get("language", "python")
                config = data.get("config", {})

                arbiter = next((a for a in self.arbiters if a.name == arbiter_name), None)
                if not arbiter:
                    raise HTTPException(status_code=404, detail=f"Arbiter '{arbiter_name}' not found.")

                result = await arbiter.run_test_generation(code, language, config)
                arena_ops_total.labels(operation="test_generation").inc()
                return JSONResponse(content={"result": result})

        @self.router.get("/arbiters", summary="List All Arbiters", tags=["Arena Operations"])
        @require_auth
        async def list_arbiters_endpoint(request: Request, settings: ArbiterConfig = Depends(ArbiterConfig.initialize)):
            """Lists all arbiters in the arena and their current status."""
            with tracer.start_as_current_span("arena_list_arbiters"):
                try:
                    arbiters_list = [{"name": a.name, "status": await a.get_status()} for a in self.arbiters]
                    arena_ops_total.labels(operation="list_arbiters").inc()
                    return JSONResponse(content={"arbiters": arbiters_list})
                except Exception as e:
                    logger.error(f"Failed to list arbiters: {e}", exc_info=True)
                    arena_errors_total.labels(error_type="list_arbiters").inc()
                    raise HTTPException(status_code=500, detail=str(e))

        @self.router.post("/security/rotate_jwt_secret", summary="Rotate JWT Secret", tags=["Security Operations"])
        @require_auth
        async def rotate_jwt_secret_endpoint(request: Request):
            """Rotates the JWT secret for the Arena and returns a new token."""
            new_secret = secrets.token_urlsafe(32)
            # This is a dangerous operation in a real system. It should be managed
            # centrally and rolled out to all services. For this mock, we just update env.
            os.environ["ARENA_JWT_SECRET"] = new_secret
            
            logger.info(f"[{self.name}] JWT secret rotated.")
            arena_ops_total.labels(operation="jwt_rotation").inc()
            
            new_token = jwt.encode({"role": "admin", "exp": datetime.utcnow() + timedelta(days=1)}, new_secret, algorithm="HS256")
            return {"message": "JWT secret rotated successfully.", "new_jwt_token": new_token}

        self.app.include_router(self.router)
        logger.info("Arena API routes setup complete.")

    async def start_arena_services(self, http_port: int):
        """Starts the FastAPI web server for the arena and all arbiter's async services."""
        await self._send_webhook("arena_started", {"http_port": http_port, "arbiters": len(self.arbiters)})
        logger.info(f"Starting ArbiterArena services for '{self.name}' on HTTP port {http_port}")
        self.monitor.log_action({'type': 'arena_services_start', 'http_port': http_port})

        if self.feedback:
            await self.feedback.connect_db()

        try:
            await self._create_initial_scan_coro()
            asyncio.create_task(self._create_periodic_scan_coro())
            logger.info("Initial and periodic codebase scan tasks scheduled.")
        except Exception as e:
            logger.error(f"Failed to start scan tasks in Arena: {e}", exc_info=True)
            arena_errors_total.labels(error_type="scan_tasks_fail").inc()

        async with self._lock:
            tasks = [asyncio.create_task(arbiter.start_async_services()) for arbiter in self.arbiters]
            await asyncio.gather(*tasks)
            logger.info(f"All {len(self.arbiters)} arbiters' async services started.")

        try:
            import uvicorn
            config = uvicorn.Config(self.app, host="0.0.0.0", port=http_port, log_level="info")
            self.server = uvicorn.Server(config)
            await self.server.serve()
        except ImportError:
            logger.error("Uvicorn not found. Please install it with 'pip install uvicorn'.")
            arena_errors_total.labels(error_type="uvicorn_missing").inc()
        except Exception as e:
            logger.error(f"Failed to start arena HTTP services: {e}", exc_info=True)
            arena_errors_total.labels(error_type="http_services_fail").inc()

    async def handle_status(self, request: Optional[Request] = None) -> Dict[str, Any]:
        """Gathers and returns the current status of the arena, its arbiters, and overall code health."""
        async with self._lock:
            arbiter_statuses = [await arb.get_status() for arb in self.arbiters]
            analyzer_status_data = await self.analyzer.get_analyzer_status()
            feedback_summary = await self.feedback.get_analytics() or {}

            status_data = {
                "arena_name": self.name,
                "version": self.version,
                "arbiter_count": len(self.arbiters),
                "code_health_summary": analyzer_status_data,
                "arbiter_statuses": arbiter_statuses,
                "feedback_summary": feedback_summary,
                "monitor_report_summary": self.monitor.generate_reports(),
                "message": "Code Guardian Arena operational"
            }
        
        logger.info("Arena status checked. Notification services are handled via webhooks.")
        return status_data

    async def run_all(self, max_cycles: int = 100):
        """Runs the arena for a specified number of scan/repair cycles."""
        logger.info(f"Starting arena run for {max_cycles} scan/repair cycles.")
        for cycle in range(max_cycles):
            logger.info(f"Arena Cycle {cycle + 1}/{max_cycles} started.")
            await self.run_arena_rounds()
            scan_repair_cycles_total.inc()

            arbiters_to_remove = [arb for arb in self.arbiters if not arb.is_alive]
            for arbiter in arbiters_to_remove:
                await self.remove(arbiter)
                await arbiter.stop_async_services()
                logger.info(f"Arbiter {arbiter.name} was removed due to poor performance.")

            if not self.arbiters:
                logger.info("All arbiters have been deactivated. Stopping arena run.")
                break
            logger.info(f"Arena Cycle {cycle + 1}/{max_cycles} completed.")
        logger.info("Arena run finished.")

    async def run_arena_rounds(self):
        """Executes one round of repair/evolution for all active arbiters concurrently."""
        async with self._lock:
            if not self.arbiters:
                logger.warning("No arbiters to run in this repair round.")
                return
            
            tasks = [arbiter.evolve(arena=self) for arbiter in self.arbiters]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        repair_outcomes = []
        for i, res in enumerate(results):
            arbiter_name = self.arbiters[i].name if i < len(self.arbiters) else "unknown"
            if isinstance(res, Exception):
                logger.error(f"Error during arbiter evolution for {arbiter_name}: {res}", exc_info=True)
                repair_outcomes.append({"arbiter": arbiter_name, "status": "error", "details": str(res), "timestamp": datetime.utcnow().isoformat()})
                arena_errors_total.labels(error_type="arbiter_evolution_fail").inc()
            elif isinstance(res, dict):
                repair_outcomes.append(res)
        
        if repair_outcomes:
            self.codebase_map.setdefault("repair_history", []).extend(repair_outcomes)
            await self._update_and_persist_map({}, "run_arena_rounds")
        
        logger.info("All arbiter evolution tasks completed for this round.")

    async def stop_all(self):
        """Stops all arbiters in the arena and clears the list."""
        logger.info(f"Stopping all arbiters in arena '{self.name}'.")
        await self._send_webhook("arena_stopped", {"reason": "manual_shutdown"})
        
        async with self._lock:
            tasks = [arb.stop_async_services() for arb in self.arbiters]
            await asyncio.gather(*tasks, return_exceptions=True)
            self.arbiters.clear()
            active_arbiters.set(0)
            logger.info(f"Stopped all arbiters in the arena '{self.name}'.")
            arena_ops_total.labels(operation="stop_all").inc()

def _handle_shutdown(loop: asyncio.AbstractEventLoop, arena: ArbiterArena):
    """Callback for signal handling, initiating graceful shutdown."""
    async def shutdown_coroutine():
        logger.info("Shutting down code guardian arena services gracefully...")
        await arena.stop_all()
        tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task(loop)]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        loop.stop()

    asyncio.ensure_future(shutdown_coroutine(), loop=loop)

def run_arena():
    from arbiter.config import ArbiterConfig as Settings
    import sys
    
    try:
        settings = Settings.initialize()
    except Exception as e:
        logger.critical(f"Failed to initialize configuration: {e}")
        sys.exit(1)

    try:
        main_loop = asyncio.get_event_loop()
    except RuntimeError:
        main_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(main_loop)
        logger.info("Created and set a new event loop for __main__ execution.")

    db_path = settings.DB_PATH
    db_file = urlparse(db_path).path if db_path.startswith("sqlite") else db_path
    
    logger.info(f"Starting Code Guardian Arena test setup...")
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
            logger.info(f"Cleaned up existing DB file: {db_file}")
        except OSError as e:
            logger.warning(f"Could not remove existing DB file {db_file}: {e}")
            arena_errors_total.labels(error_type="db_cleanup_fail").inc()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", echo=False)
    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    try:
        main_loop.run_until_complete(create_tables())
    except Exception as e:
        logger.critical(f"Failed to create database tables: {e}")
        arena_errors_total.labels(error_type="db_table_create_fail").inc()
        sys.exit(1)
        
    logger.info("Database initialized for arena arbiters.")
    
    arena = ArbiterArena(name="MainCodeGuardianArena", num=2, settings=settings, db_engine=engine)

    try:
        main_loop.add_signal_handler(signal.SIGINT, lambda: _handle_shutdown(main_loop, arena))
        main_loop.add_signal_handler(signal.SIGTERM, lambda: _handle_shutdown(main_loop, arena))
    except NotImplementedError:
        logger.warning("Cannot add signal handlers on this platform. Use Ctrl+C to stop.")

    try:
        main_loop.run_until_complete(arena.start_arena_services(http_port=settings.ARENA_PORT))
    except KeyboardInterrupt:
        logger.info("Arena interrupted by user.")
    finally:
        logger.info("Closing loop and exiting.")
        main_loop.close()

if __name__ == '__main__':
    import subprocess
    import sys

    if os.environ.get("SANDBOXED_ARENA", "") == "1":
        run_arena()
    else:
        env = os.environ.copy()
        env["SANDBOXED_ARENA"] = "1"
        logger.info("Orchestrator: Launching sandboxed Arena process...")
        proc = subprocess.Popen([sys.executable, __file__], env=env)
        proc.wait()
        logger.info(f"Orchestrator: Sandboxed Arena process exited with code: {proc.returncode}")
        logger.info(f"Orchestrator: Sandboxed Arena process exited with code: {proc.returncode}")
