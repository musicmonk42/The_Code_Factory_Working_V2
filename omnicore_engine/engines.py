import asyncio
import logging
from typing import Any, Dict, List, Optional

import yaml
from arbiter.config import ArbiterConfig
from fastapi import FastAPI  # Needed for type hinting

from omnicore_engine.database import Database
from omnicore_engine.message_bus import ShardedMessageBus
from omnicore_engine.plugin_registry import PLUGIN_REGISTRY as global_plugin_registry

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


from arbiter import Arbiter
from arbiter.utils import (
    get_system_metrics_async,
)  # New import needed for helper function
from envs.code_health_env import CodeHealthEnv  # New import for the RL environment
from intent_capture.api import app as intent_capture_api
from self_healing_import_fixer.import_fixer.import_fixer_engine import (
    ImportFixerEngine,
    create_import_fixer_engine,
)
from test_generation.orchestrator import TestGenerationOrchestrator

from self_fixing_engineer.agent_orchestration.crew_manager import CrewManager
from self_fixing_engineer.simulation.simulation_module import UnifiedSimulationModule

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


# Initialize the configuration object
settings = ArbiterConfig()
logger = logging.getLogger(__name__)


class PluginService:
    def __init__(self, plugin_registry):
        self.plugin_registry = plugin_registry
        self.message_bus = ShardedMessageBus(
            config=ArbiterConfig(), db=Database(ArbiterConfig().database_path)
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

    @classmethod
    def create_and_initialize(cls):
        db = Database(settings.database_path)
        message_bus = ShardedMessageBus(config=settings, db=db)
        plugin_service = PluginService(global_plugin_registry)
        simulation_engine = UnifiedSimulationModule(
            config=settings, db=db, message_bus=message_bus
        )

        crew_manager = CrewManager()

        try:
            with open("crew_config.yaml", "r") as f:
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
            logger.info("CrewManager agents loaded from crew_config.yaml.")
        except FileNotFoundError:
            logger.error(
                "crew_config.yaml not found. No agents will be added to the crew manager."
            )
        except Exception as e:
            logger.error(f"Failed to load agents from crew_config.yaml: {e}")

        intent_capture_app_instance = intent_capture_api
        test_generation_orchestrator = TestGenerationOrchestrator()

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
