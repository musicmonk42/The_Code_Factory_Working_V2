import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Optional, Type

from arbiter.config import ArbiterConfig
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# Initialize the configuration object from arbiter.config
settings = ArbiterConfig()


# SECURITY: Lazy import to avoid side effects at module load time
# Import gen_plugins only when needed to prevent circular import issues
def _ensure_plugins_loaded():
    """Lazily import gen_plugins to register plugins when needed."""
    try:
        import self_fixing_engineer.test_generation.gen_plugins

        return True
    except ImportError as e:
        logger.warning(f"Could not import gen_plugins: {e}")
        return False


def safe_serialize(obj: Any) -> Any:
    """
    Safely serializes various Python objects into JSON-compatible formats.
    Handles common non-serializable types and attempts best-effort conversion.
    """
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="ignore")
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, (list, tuple)):
        return [safe_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): safe_serialize(v) for k, v in obj.items()}

    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return obj.model_dump()
    if hasattr(obj, "dict") and callable(obj.dict):
        return obj.dict()
    try:
        return str(obj)
    except Exception:
        return f"<unserializable object of type {type(obj)}>"


class Base(ABC):
    """
    Abstract base class for all core OmniCore components.
    Provides common interfaces and ensures foundational structure.
    """

    def __init__(self, settings: BaseSettings = settings):
        self.settings = settings

    @abstractmethod
    async def initialize(self) -> None:
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        pass

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        pass

    @property
    @abstractmethod
    def is_healthy(self) -> bool:
        pass


def get_plugin_metrics() -> dict:
    """
    Retrieves and processes plugin-related metrics.
    This is a placeholder that would interact with Prometheus or other metrics systems.
    """
    try:
        from omnicore_engine.metrics import (
            get_plugin_metrics as actual_get_plugin_metrics,
        )

        return actual_get_plugin_metrics()
    except ImportError:
        logger.warning("Metrics system not available. Returning mock plugin metrics.")
        return {
            "error": "Metrics system not available.",
            "message": "Failed to retrieve plugin metrics",
        }
    except Exception as e:
        logger.error(f"Error retrieving plugin metrics: {e}", exc_info=True)
        return {"error": str(e), "message": "Failed to retrieve plugin metrics"}


def get_test_metrics() -> dict:
    """
    Retrieves and processes test-related metrics.
    This is a placeholder that would interact with CI/CD metrics.
    """
    try:
        from omnicore_engine.metrics import get_test_metrics as actual_get_test_metrics

        return actual_get_test_metrics()
    except ImportError:
        logger.warning("Metrics system not available. Returning mock test metrics.")
        return {
            "error": "Metrics system not available.",
            "message": "Failed to retrieve test metrics",
        }
    except Exception as e:
        logger.error(f"Error retrieving test metrics: {e}", exc_info=True)
        return {"error": str(e), "message": "Failed to retrieve test metrics"}


class ExplainableAI:
    """
    Centralizes explanation and reasoning capabilities within OmniCore.
    This is a high-level abstraction.
    """

    def __init__(self):
        try:
            from omnicore_engine.explainable_reasoner import ExplainableReasoner

            self.reasoner = ExplainableReasoner()
        except ImportError:
            self.reasoner = None
        self.logger = logger
        self.is_initialized = False

    async def initialize(self):
        if not self.is_initialized:
            self.is_initialized = True
            self.logger.info("Explainable AI core initialized.")

    async def shutdown(self):
        if self.is_initialized:
            if self.reasoner and hasattr(self.reasoner, "shutdown_executor"):
                self.reasoner.shutdown_executor()
            self.is_initialized = False
            self.logger.info("Explainable AI core shut down.")

    async def explain_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a human-readable explanation for an event."""
        if not self.reasoner:
            self.logger.warning(
                "Explainable AI core is not available. Returning dummy explanation."
            )
            return {
                "explanation": "Mock explanation: AI explanation feature is disabled."
            }
        return await self.reasoner.explain_event(event_data)

    async def reason_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate logical reasoning for an event or decision."""
        if not self.reasoner:
            self.logger.warning(
                "Explainable AI core is not available. Returning dummy reasoning."
            )
            return {"reasoning": "Mock reasoning: AI reasoning feature is disabled."}
        return await self.reasoner.reason_event(event_data)


class OmniCoreEngine:
    """
    The central orchestration engine for a self-fixing engineering task platform.
    Manages the lifecycle and dependencies of core components.
    """

    def __init__(self, settings: BaseSettings):
        self.settings = settings
        self.components: Dict[str, Any] = {}
        self.component_locks: Dict[str, asyncio.Lock] = {}
        self.logger = logger
        self._is_initialized = False

    async def initialize(self):
        if self._is_initialized:
            self.logger.info("OmniCore Engine: Already initialized.")
            return

        self.logger.info("OmniCore Engine: Starting application components...")

        try:
            from omnicore_engine.merkle_tree import MerkleTree

            system_audit_merkle_tree = MerkleTree()
            self.logger.info("MerkleTree initialized for audit system.")
        except ImportError:
            self.logger.warning(
                "MerkleTree not found. Audit integrity features will be limited."
            )
            system_audit_merkle_tree = None

        from omnicore_engine.database import Database

        await self._initialize_component_instance(
            "database",
            Database,
            self.settings.database_path,
            system_audit_merkle_tree=system_audit_merkle_tree,
        )
        database_instance = await self.get_component("database")

        from omnicore_engine.audit import ExplainAudit

        await self._initialize_component_instance(
            "audit", ExplainAudit, system_audit_merkle_tree=system_audit_merkle_tree
        )

        from omnicore_engine.plugin_registry import (
            PLUGIN_REGISTRY,
            start_plugin_observer,
        )

        PLUGIN_REGISTRY.db = database_instance

        # A more robust component for plugin registry to ensure loading happens correctly
        class PluginRegistryComponent(Base):
            def __init__(self, settings, registry, observer):
                super().__init__(settings)
                self.registry = registry
                self.observer = observer
                self.is_started = False

            async def initialize(self):
                if not self.is_started:
                    self.registry.load_from_directory(self.settings.plugin_dir)
                    self.observer(self.registry, self.settings.plugin_dir)
                    self.is_started = True

            async def shutdown(self):
                # The observer might have a shutdown method in a more complex setup, but for now, logging is sufficient.
                self.logger.info("Plugin registry does not require explicit shutdown.")
                self.is_started = False

            async def health_check(self) -> Dict[str, Any]:
                return {
                    "status": "ok",
                    "plugins_loaded": sum(
                        len(k) for k in self.registry.plugins.values()
                    ),
                }

            @property
            def is_healthy(self) -> bool:
                return self.is_started

        await self._initialize_component_instance(
            "plugin_registry",
            PluginRegistryComponent,
            settings=self.settings,
            registry=PLUGIN_REGISTRY,
            observer=start_plugin_observer,
        )

        from omnicore_engine.feedback_manager import FeedbackManager

        await self._initialize_component_instance(
            "feedback_manager",
            FeedbackManager,
            db_dsn=self.settings.database_path,
            redis_url=self.settings.redis_url,
            encryption_key=self.settings.ENCRYPTION_KEY.get_secret_value(),
        )

        explainable_ai_instance = ExplainableAI()
        await self._initialize_component_instance(
            "explainable_ai",
            type(
                "ExplainableAIComponent",
                (Base,),
                {
                    "initialize": lambda self_comp: explainable_ai_instance.initialize(),
                    "shutdown": lambda self_comp: explainable_ai_instance.shutdown(),
                    "health_check": lambda self_comp: {
                        "status": "ok",
                        "reasoner_initialized": explainable_ai_instance.is_initialized,
                    },
                    "is_healthy": property(
                        lambda self_comp: explainable_ai_instance.is_initialized
                    ),
                },
            ),
            settings=self.settings,
        )

        self._is_initialized = True
        self.logger.info("OmniCore Engine: All application components started.")

    async def shutdown(self):
        if not self._is_initialized:
            self.logger.info("OmniCore Engine: Not initialized or already shut down.")
            return

        self.logger.info("OmniCore Engine: Shutting down application components...")

        await self._shutdown_component_instance("explainable_ai")
        await self._shutdown_component_instance("feedback_manager")
        await self._shutdown_component_instance("plugin_registry")
        await self._shutdown_component_instance("audit")
        await self._shutdown_component_instance("database")

        self._is_initialized = False
        self.logger.info("OmniCore Engine: All application components shut down.")

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def _initialize_component_instance(
        self, name: str, component_class: Type[Base], *args, **kwargs
    ) -> Base:
        if name not in self.component_locks:
            self.component_locks[name] = asyncio.Lock()

        async with self.component_locks[name]:
            if name not in self.components:
                self.logger.info(f"Engine initializing component: {name}...")
                instance = component_class(*args, **kwargs)
                await instance.initialize()
                self.components[name] = instance
                self.logger.info(
                    f"Component '{name}' initialized successfully by engine."
                )
            else:
                self.logger.debug(
                    f"Component '{name}' already initialized by engine. Skipping."
                )
            return self.components[name]

    async def _shutdown_component_instance(self, name: str):
        if name not in self.component_locks:
            self.component_locks[name] = asyncio.Lock()
        async with self.component_locks[name]:
            if name in self.components:
                self.logger.info(f"Engine shutting down component: {name}...")
                component = self.components.pop(name)
                await component.shutdown()
                self.logger.info(
                    f"Component '{name}' shut down successfully by engine."
                )
            else:
                self.logger.warning(
                    f"Component '{name}' not found or already shut down by engine."
                )

    async def get_component(self, name: str) -> Optional[Base]:
        return self.components.get(name)

    async def health_check_all(self) -> Dict[str, Any]:
        results = {}
        for name, component in self.components.items():
            try:
                health = await component.health_check()
                results[name] = health
            except Exception as e:
                logger.error(
                    f"Health check failed for component {name}: {e}", exc_info=True
                )
                results[name] = {"status": "error", "message": str(e)}
        return results

    async def perform_task(self, task_name: str, **kwargs) -> Any:
        from omnicore_engine.plugin_registry import PLUGIN_REGISTRY

        # The original code had get_plugin_for_task, which is not a standard method.
        # Assuming a more general way to find a plugin.
        # This is a placeholder for the actual logic to find and execute a plugin.
        plugin_instance = (
            PLUGIN_REGISTRY.get_plugin_for_task(task_name)
            if hasattr(PLUGIN_REGISTRY, "get_plugin_for_task")
            else None
        )

        if plugin_instance:
            try:
                result = await plugin_instance.execute(action=task_name, **kwargs)
                return result
            except Exception as e:
                logger.error(
                    f"Error performing task '{task_name}' via plugin: {e}",
                    exc_info=True,
                )
                return None
        logger.warning(f"No plugin found for task '{task_name}'.")
        return None


omnicore_engine = OmniCoreEngine(settings=settings)

__all__ = [
    "Base",
    "omnicore_engine",
    "safe_serialize",
    "logger",
    "settings",
    "get_plugin_metrics",
    "get_test_metrics",
    "ExplainableAI",
]
