import asyncio
import logging
import types
from typing import Any, Dict, List, Optional

import yaml
from fastapi import FastAPI  # Needed for type hinting

from omnicore_engine.database import Database
from omnicore_engine.message_bus import ShardedMessageBus
from omnicore_engine.plugin_registry import PLUGIN_REGISTRY as global_plugin_registry

# Try to import ArbiterConfig at module level for use in tests
try:
    from self_fixing_engineer.arbiter.config import ArbiterConfig
except ImportError:
    # If import fails, create a fallback class
    class ArbiterConfig:
        """Fallback ArbiterConfig when arbiter module is not available."""
        def __init__(self):
            self.log_level = "INFO"
            self.LOG_LEVEL = "INFO"
            self.database_path = "sqlite:///./omnicore.db"
            self.DB_PATH = "sqlite:///./omnicore.db"
            self.API_HOST = "0.0.0.0"
            self.API_PORT = 8000

try:
    from arbiter.bug_manager import BugManager
except Exception:
    # Minimal stub used when arbiter isn't installed (tests will typically patch this)
    class BugManager:
        def __init__(self, *args, **kwargs):
            pass

        async def report_bug(self, payload):
            # no-op fallback for tests / import-time usage
            return None


try:
    from arbiter import Arbiter
except ImportError:
    # Minimal stub when arbiter isn't installed
    class Arbiter:
        def __init__(self, *args, **kwargs):
            pass

        async def start_async_services(self):
            pass

        async def stop_async_services(self):
            pass

        async def respond(self, *args, **kwargs):
            return "Arbiter unavailable"


try:
    from arbiter.utils import (
        get_system_metrics_async,
    )  # New import needed for helper function
except ImportError:

    async def get_system_metrics_async():
        """Fallback system metrics function."""
        return {"status": "unavailable", "message": "arbiter.utils not available"}

# Optional imports that may not be available in all environments
try:
    from envs.code_health_env import CodeHealthEnv
except ImportError:
    CodeHealthEnv = None

try:
    from intent_capture.api import app as intent_capture_api
except ImportError:
    intent_capture_api = None

try:
    from self_healing_import_fixer.import_fixer.import_fixer_engine import (
        ImportFixerEngine,
        create_import_fixer_engine,
    )
except ImportError:
    ImportFixerEngine = None
    create_import_fixer_engine = None

try:
    from test_generation.orchestrator import TestGenerationOrchestrator
except ImportError:
    TestGenerationOrchestrator = None

try:
    from self_fixing_engineer.agent_orchestration.crew_manager import CrewManager
except ImportError:
    CrewManager = None

try:
    from self_fixing_engineer.simulation.simulation_module import (
        UnifiedSimulationModule,
    )
except ImportError:
    UnifiedSimulationModule = None

# Generator component imports (optional, graceful degradation)
try:
    from generator.runner.runner_core import Runner as GeneratorRunner
except ImportError:
    GeneratorRunner = None

try:
    from generator.runner.llm_client import call_llm_api, call_ensemble_api
except ImportError:
    call_llm_api = None
    call_ensemble_api = None

try:
    from generator.agents import get_available_agents, is_agent_available
except ImportError:
    def get_available_agents():
        return {}
    
    def is_agent_available(agent_name: str) -> bool:
        return False

try:
    from generator.intent_parser.intent_parser import IntentParser
except ImportError:
    IntentParser = None

# --- Engine Registry for discoverable components ---
ENGINE_REGISTRY = {}


def register_engine(engine_name: str, entrypoints: dict):
    """
    Registers an engine/module so it is discoverable and callable by OmniCore and Arbiter.
    """
    if not isinstance(entrypoints, dict):
        raise TypeError("Entrypoints must be a dictionary.")

    ENGINE_REGISTRY[engine_name] = entrypoints
    logging.info(f"Engine '{engine_name}' registered successfully.")


def get_engine(engine_name: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves a registered engine's entrypoints.
    """
    return ENGINE_REGISTRY.get(engine_name)


def _create_fallback_settings():
    """Create a minimal settings object for when ArbiterConfig is unavailable."""
    return types.SimpleNamespace(
        log_level="INFO",
        LOG_LEVEL="INFO",
        database_path="sqlite:///./omnicore.db",
        DB_PATH="sqlite:///./omnicore.db",
        plugin_dir="./plugins",
        PLUGIN_DIR="./plugins",
    )


def _get_settings():
    """Lazy import + defensive instantiation of settings."""
    try:
        from arbiter.config import ArbiterConfig
    except ImportError as e:
        logging.warning(
            "Could not import arbiter.config; using fallback settings. Import error: %s",
            e,
        )
        return _create_fallback_settings()

    try:
        return ArbiterConfig()
    except Exception as e:
        logging.warning(
            "ArbiterConfig() raised during instantiation; falling back to minimal settings. Error: %s",
            e,
        )
        return _create_fallback_settings()


# Initialize the configuration object with graceful fallback
settings = _get_settings()
logger = logging.getLogger(__name__)


class PluginService:
    def __init__(self, plugin_registry):
        self.plugin_registry = plugin_registry
        self.message_bus = ShardedMessageBus(
            config=settings, db=Database(settings.DB_PATH)
        )

        # Subscribe to a channel for a bug detected by the Arbiter
        asyncio.create_task(
            self.message_bus.subscribe("arbiter:bug_detected", self.handle_arbiter_bug)
        )

        # Subscribe to a channel for self-healing import fixer requests
        asyncio.create_task(
            self.message_bus.subscribe(
                "shif:fix_import_request", self.handle_shif_request
            )
        )

        # Subscribe to generator channels
        asyncio.create_task(
            self.message_bus.subscribe(
                "generator:codegen_request", self.handle_codegen_request
            )
        )
        asyncio.create_task(
            self.message_bus.subscribe(
                "generator:testgen_request", self.handle_testgen_request
            )
        )
        asyncio.create_task(
            self.message_bus.subscribe(
                "generator:docgen_request", self.handle_docgen_request
            )
        )
        asyncio.create_task(
            self.message_bus.subscribe(
                "workflow:sfe_to_generator", self.handle_sfe_to_generator
            )
        )

        self.logger = logging.getLogger("PluginService")

    async def handle_arbiter_bug(self, message):
        self.logger.info(f"Received Arbiter bug event: {message.payload}")
        bug_manager = BugManager()
        await bug_manager.report_bug(message.payload)

    async def handle_shif_request(self, message):
        """Handle incoming requests to the Self-Healing Import Fixer."""
        self.logger.info(f"Received SHIF request: {message.payload}")
        path_to_fix = message.payload.get("path")
        code_to_fix = message.payload.get("code")

        import_fixer_engine_entry = get_engine("import_fixer")
        if not import_fixer_engine_entry:
            self.logger.error("Import fixer engine is not registered.")
            return

        import_fixer = import_fixer_engine_entry["engine"]
        try:
            if path_to_fix:
                fixed_code = await import_fixer.fix_file(path_to_fix)
                await self.message_bus.publish(
                    "shif:fix_import_success",
                    {"path": path_to_fix, "fixed_code": fixed_code},
                )
            elif code_to_fix:
                fixed_code = await import_fixer.fix_code(code_to_fix)
                await self.message_bus.publish(
                    "shif:fix_import_success", {"fixed_code": fixed_code}
                )
        except Exception as e:
            self.logger.error(f"SHIF failed to fix import: {e}")
            await self.message_bus.publish(
                "shif:fix_import_failure", {"error": str(e), "path": path_to_fix}
            )

    async def handle_codegen_request(self, message):
        """Route code generation requests to generator."""
        self.logger.info(f"Received CodeGen request: {message.payload}")
        
        codegen_engine = get_engine("generator")
        if not codegen_engine:
            self.logger.error("Generator engine not registered")
            await self.message_bus.publish(
                "generator:codegen_failure",
                {"error": "Generator engine not available", "request": message.payload},
            )
            return
        
        try:
            # Extract request parameters
            code_spec = message.payload.get("spec", "")
            language = message.payload.get("language", "python")
            
            # Check if codegen agent is available
            if not is_agent_available("codegen"):
                raise RuntimeError("CodeGen agent not available")
            
            self.logger.info(f"Processing code generation for language: {language}")
            await self.message_bus.publish(
                "generator:codegen_success",
                {"status": "accepted", "request_id": message.payload.get("request_id")},
            )
        except Exception as e:
            self.logger.error(f"CodeGen request failed: {e}")
            await self.message_bus.publish(
                "generator:codegen_failure",
                {"error": str(e), "request": message.payload},
            )

    async def handle_testgen_request(self, message):
        """Route test generation requests to generator."""
        self.logger.info(f"Received TestGen request: {message.payload}")
        
        codegen_engine = get_engine("generator")
        if not codegen_engine:
            self.logger.error("Generator engine not registered")
            await self.message_bus.publish(
                "generator:testgen_failure",
                {"error": "Generator engine not available", "request": message.payload},
            )
            return
        
        try:
            # Check if testgen agent is available
            if not is_agent_available("testgen"):
                raise RuntimeError("TestGen agent not available")
            
            target_code = message.payload.get("target_code", "")
            self.logger.info(f"Processing test generation")
            await self.message_bus.publish(
                "generator:testgen_success",
                {"status": "accepted", "request_id": message.payload.get("request_id")},
            )
        except Exception as e:
            self.logger.error(f"TestGen request failed: {e}")
            await self.message_bus.publish(
                "generator:testgen_failure",
                {"error": str(e), "request": message.payload},
            )

    async def handle_docgen_request(self, message):
        """Route documentation generation requests to generator."""
        self.logger.info(f"Received DocGen request: {message.payload}")
        
        codegen_engine = get_engine("generator")
        if not codegen_engine:
            self.logger.error("Generator engine not registered")
            await self.message_bus.publish(
                "generator:docgen_failure",
                {"error": "Generator engine not available", "request": message.payload},
            )
            return
        
        try:
            # Check if docgen agent is available
            if not is_agent_available("docgen"):
                raise RuntimeError("DocGen agent not available")
            
            code_path = message.payload.get("code_path", "")
            self.logger.info(f"Processing documentation generation for: {code_path}")
            await self.message_bus.publish(
                "generator:docgen_success",
                {"status": "accepted", "request_id": message.payload.get("request_id")},
            )
        except Exception as e:
            self.logger.error(f"DocGen request failed: {e}")
            await self.message_bus.publish(
                "generator:docgen_failure",
                {"error": str(e), "request": message.payload},
            )

    async def handle_sfe_to_generator(self, message):
        """Handle workflow transitions from SFE to generator."""
        self.logger.info(f"Received SFE to Generator workflow message: {message.payload}")
        
        try:
            workflow_type = message.payload.get("workflow_type", "unknown")
            
            if workflow_type == "fix_and_regenerate":
                # SFE fixed code, now regenerate tests
                self.logger.info("Triggering test regeneration after SFE fix")
                await self.message_bus.publish(
                    "generator:testgen_request",
                    {
                        "target_code": message.payload.get("fixed_code"),
                        "request_id": message.payload.get("request_id"),
                        "source": "sfe_workflow",
                    },
                )
            elif workflow_type == "generate_and_fix":
                # Generator created code, send to SFE for validation/fixing
                self.logger.info("Routing generated code to SFE for validation")
                await self.message_bus.publish(
                    "shif:fix_import_request",
                    {
                        "code": message.payload.get("generated_code"),
                        "request_id": message.payload.get("request_id"),
                    },
                )
            else:
                self.logger.warning(f"Unknown workflow type: {workflow_type}")
        except Exception as e:
            self.logger.error(f"SFE to Generator workflow failed: {e}")

    async def get_companies(self):
        fetcher = self.plugin_registry.get("company_list")
        if fetcher:
            return await fetcher()
        self.logger.error("No 'company_list' plugin registered.")
        raise RuntimeError("No company_list plugin registered")

    async def get_esg(self, ticker):
        fetcher = self.plugin_registry.get("esg_report")
        if fetcher:
            return await fetcher(ticker)
        self.logger.error("No 'esg_report' plugin registered.")
        raise RuntimeError("No esg_report plugin registered")

    async def run_sim(self, tickers):
        simulator = self.plugin_registry.get("simulation_engine")
        if simulator:
            return await simulator(tickers)
        self.logger.error("No 'simulation_engine' plugin registered.")
        raise RuntimeError("No simulation_engine plugin registered")


def run_import_fixer(path):
    """
    Synchronous helper to run the fixer. This method is deprecated in favor of the async engine.
    """
    import_fixer = get_engine("import_fixer")["engine"]
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(import_fixer.fix_file(path))


class OmniCoreOmega:
    """
    Production orchestrator: only real, current modules are wired here.
    Add new engines/services as constructor fields as you implement them.
    """

    def __init__(
        self,
        database: Database,
        message_bus: ShardedMessageBus,
        plugin_service: PluginService,
        crew_manager: CrewManager,
        intent_capture_api: FastAPI,
        test_generation_orchestrator: TestGenerationOrchestrator,
        simulation_engine: UnifiedSimulationModule,
        audit_log_manager: Any,
        import_fixer_engine: ImportFixerEngine,
        num_arbiters: int = 5,
        # Generator components (optional)
        generator_runner: Optional["GeneratorRunner"] = None,
        intent_parser: Optional["IntentParser"] = None,
        llm_client: Optional[Any] = None,
    ):
        self.db = database
        self.message_bus = message_bus
        self.plugin_service = plugin_service
        self.crew_manager = crew_manager
        self.intent_capture_api = intent_capture_api
        self.test_generation_orchestrator = test_generation_orchestrator
        self.simulation_engine = simulation_engine
        self.audit_log_manager = audit_log_manager
        self.import_fixer_engine = import_fixer_engine
        self._is_initialized = False
        self.arbiters = []
        self.num = num_arbiters
        
        # Store generator components
        self.generator_runner = generator_runner
        self.intent_parser = intent_parser
        self.llm_client = llm_client

    @staticmethod
    def _find_crew_config() -> Optional[str]:
        """
        Search for crew_config.yaml in standard locations.
        
        Returns:
            Path to crew_config.yaml if found, None otherwise.
        """
        import os
        from pathlib import Path
        
        # Standard search locations
        search_paths = [
            "./crew_config.yaml",
            "../crew_config.yaml",
            "../self_fixing_engineer/crew_config.yaml",
            "../configs/crew_config.yaml",
            "./self_fixing_engineer/crew_config.yaml",
            "./configs/crew_config.yaml",
        ]
        
        for path_str in search_paths:
            path = Path(path_str)
            if path.exists() and path.is_file():
                logger.info(f"Found crew_config.yaml at: {path.absolute()}")
                return str(path)
        
        logger.warning("crew_config.yaml not found in any standard location")
        return None

    @classmethod
    def create_and_initialize(cls):
        db = Database(settings.DB_PATH)
        message_bus = ShardedMessageBus(config=settings, db=db)
        plugin_service = PluginService(global_plugin_registry)
        simulation_engine = UnifiedSimulationModule(
            config=settings, db=db, message_bus=message_bus
        )

        crew_manager = CrewManager()

        crew_config_path = cls._find_crew_config()
        if crew_config_path:
            try:
                with open(crew_config_path, "r") as f:
                    crew_config = yaml.safe_load(f)

                for agent in crew_config.get("agents", []):
                    agent_class_name = agent.get("class", "GenericAgent")
                    agent_config = agent.get("config", {})
                    agent_tags = agent.get("tags", [])
                    agent_metadata = agent.get("metadata", {})

                    crew_manager.add_agent(
                        name=agent["name"],
                        agent_class=agent_class_name,
                        config=agent_config,
                        tags=agent_tags,
                        metadata=agent_metadata,
                    )
                logger.info(f"CrewManager agents loaded from {crew_config_path}.")
            except Exception as e:
                logger.error(f"Failed to load agents from crew_config.yaml: {e}")
        else:
            logger.warning(
                "crew_config.yaml not found. No agents will be added to the crew manager."
            )

        intent_capture_app_instance = intent_capture_api
        test_generation_orchestrator = TestGenerationOrchestrator()

        # Try to use real audit loggers instead of mock
        audit_log_manager = None
        
        # First, try generator's audit log
        try:
            from generator.audit_log.audit_log import AUDIT_LOG
            audit_log_manager = AUDIT_LOG
            logger.info("Using generator AUDIT_LOG for audit logging")
        except ImportError:
            pass
        
        # Fall back to SFE's audit logger
        if audit_log_manager is None:
            try:
                from self_fixing_engineer.guardrails.audit_log import AuditLogger
                audit_log_manager = AuditLogger()
                logger.info("Using SFE AuditLogger for audit logging")
            except ImportError:
                pass
        
        # Last resort: use mock
        if audit_log_manager is None:
            logger.warning(
                "Real audit loggers not available; using MockAuditLogManager. "
                "Install generator or self_fixing_engineer for production audit logging."
            )
            
            class MockAuditLogManager:
                def __init__(self):
                    self.logs = []

                async def log_audit(self, entry):
                    self.logs.append(entry)
                    logger.info(f"[MockAuditLogManager] Logged: {entry}")

            audit_log_manager = MockAuditLogManager()

        import_fixer_engine = create_import_fixer_engine()

        return cls(
            database=db,
            message_bus=message_bus,
            plugin_service=plugin_service,
            crew_manager=crew_manager,
            intent_capture_api=intent_capture_app_instance,
            test_generation_orchestrator=test_generation_orchestrator,
            simulation_engine=simulation_engine,
            audit_log_manager=audit_log_manager,
            import_fixer_engine=import_fixer_engine,
        )

    def _initialize_arbiters(self):
        logger.info("OmniCoreOmega: Initializing arbiters...")

        db_engine_for_arbiters = self.db.engine

        async def get_system_metrics() -> List[float]:
            metrics = await get_system_metrics_async()
            return [
                metrics.get("pass_rate", 1.0),
                metrics.get("latency", 0.0),
                metrics.get("alert_ratio", 0.0),
            ]

        async def apply_action(action_id: int) -> Dict[str, Any]:
            action_map = {0: "noop", 1: "restart", 2: "rollback"}
            action_name = action_map.get(action_id)
            self.logger.info(f"Applying action: {action_name}")
            if action_name == "restart":
                return {"success": True, "message": "Service restarted."}
            return {"success": True}

        code_health_env = CodeHealthEnv(
            get_metrics=get_system_metrics,
            apply_action=apply_action,
            audit_logger=self.audit_log_manager,
        )

        for i in range(self.num):
            arbiter = Arbiter(
                name=f"Arbiter_{i}",
                db_engine=db_engine_for_arbiters,
                settings=settings,
                code_health_env=code_health_env,
                audit_log_manager=self.audit_log_manager,
            )
            self.arbiters.append(arbiter)
        logger.info(f"OmniCoreOmega: Initialized {len(self.arbiters)} arbiters.")

    async def initialize_asset_data(self):
        logger.info("OmniCoreOmega: Starting asset data initialization.")

        # Initialize the Self-Healing Import Fixer engine
        await self.import_fixer_engine.initialize()

        # Register the SHIF engine in the global registry
        register_engine(
            "import_fixer",
            {
                "engine": self.import_fixer_engine,
                "initialize": self.import_fixer_engine.initialize,
                "shutdown": self.import_fixer_engine.shutdown,
                "fix_file": self.import_fixer_engine.fix_file,
                "fix_code": self.import_fixer_engine.fix_code,
                "health_check": self.import_fixer_engine.health_check,
            },
        )

        # Register test generation engine
        if self.test_generation_orchestrator:
            register_engine(
                "test_generation",
                {
                    "engine": self.test_generation_orchestrator,
                    "description": "SFE test generation orchestrator",
                },
            )
            logger.info("Registered test_generation engine in ENGINE_REGISTRY")

        # Register simulation engine
        if self.simulation_engine:
            register_engine(
                "simulation",
                {
                    "engine": self.simulation_engine,
                    "description": "Unified simulation module",
                },
            )
            logger.info("Registered simulation engine in ENGINE_REGISTRY")

        # Register crew manager
        if self.crew_manager:
            register_engine(
                "crew_manager",
                {
                    "engine": self.crew_manager,
                    "start_all": self.crew_manager.start_all if hasattr(self.crew_manager, "start_all") else None,
                    "description": "Agent crew orchestration manager",
                },
            )
            logger.info("Registered crew_manager engine in ENGINE_REGISTRY")

        # Register arbiters
        register_engine(
            "arbiters",
            {
                "instances": lambda: self.arbiters,
                "count": self.num,
                "description": "Bug detection and RL arbiters",
            },
        )
        logger.info("Registered arbiters in ENGINE_REGISTRY")

        # Register generator capabilities (if available)
        if GeneratorRunner or get_available_agents():
            generator_entrypoints = {
                "description": "Generator code/test/doc generation capabilities",
                "available_agents": get_available_agents(),
            }
            
            if self.generator_runner:
                generator_entrypoints["runner"] = self.generator_runner
            
            if self.intent_parser:
                generator_entrypoints["intent_parser"] = self.intent_parser
            
            if self.llm_client or call_llm_api:
                generator_entrypoints["llm_client"] = self.llm_client or call_llm_api
            
            register_engine("generator", generator_entrypoints)
            logger.info("Registered generator engine in ENGINE_REGISTRY")


        for component in [
            self.db,
            self.message_bus,
            self.simulation_engine,
        ]:
            if hasattr(component, "initialize"):
                try:
                    maybe_coroutine = component.initialize()
                    if asyncio.iscoroutine(maybe_coroutine):
                        await maybe_coroutine
                except Exception as e:
                    logger.error(
                        f"Failed to initialize {component.__class__.__name__}: {e}",
                        exc_info=True,
                    )

        async def start_agents():
            await self.crew_manager.start_all()

        asyncio.create_task(start_agents())

        self._initialize_arbiters()

        self._is_initialized = True
        logger.info("OmniCoreOmega: Asset data initialization complete.")

    async def get_companies(self):
        return await self.plugin_service.get_companies()

    async def get_esg(self, ticker):
        return await self.plugin_service.get_esg(ticker)

    async def run_sim(self, tickers):
        return await self.plugin_service.run_sim(tickers)
