import logging
import asyncio
from typing import Any, Dict, Optional, List, Type, Set
from abc import ABC, abstractmethod
import json
import hashlib
from datetime import datetime, date
from pydantic import BaseModel, Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
import sys
import os
from cryptography.fernet import Fernet
import structlog
import inspect
from decimal import Decimal
from uuid import UUID
import numpy as np
from arbiter.config import ArbiterConfig

# Initialize the configuration object
settings = ArbiterConfig()

# Configure structlog
def configure_logging():
    structlog.configure(
        processors=[
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    # Updated to use the new settings object and attribute
    logging.basicConfig(level=getattr(settings, 'log_level', 'INFO'))

configure_logging()
logger = structlog.get_logger(__name__)
logger = logger.bind(module="OmniCoreEngine")

# --- Core Utility Functions ---
def safe_serialize(obj: Any, _seen: Optional[Set[int]] = None) -> Any:
    if _seen is None:
        _seen = set()
    obj_id = id(obj)
    if obj_id in _seen:
        return f"<<<CIRCULAR REFERENCE: {type(obj).__name__}>>>"
    _seen.add(obj_id)

    if isinstance(obj, (str, int, float, bool)) or obj is None:
        _seen.remove(obj_id)
        return obj
    if isinstance(obj, (datetime, date)):
        _seen.remove(obj_id)
        return obj.isoformat()
    if isinstance(obj, bytes):
        _seen.remove(obj_id)
        return obj.decode('utf-8', errors='ignore')
    if isinstance(obj, (set, frozenset)):
        return [safe_serialize(item, _seen) for item in list(obj)]
    if isinstance(obj, (list, tuple)):
        return [safe_serialize(item, _seen) for item in obj]
    if isinstance(obj, dict):
        return {str(k): safe_serialize(v, _seen) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        _seen.remove(obj_id)
        return float(obj)
    if isinstance(obj, np.ndarray):
        _seen.remove(obj_id)
        return obj.tolist()
    if isinstance(obj, np.generic):
        _seen.remove(obj_id)
        return obj.item()
    if isinstance(obj, UUID):
        _seen.remove(obj_id)
        return str(obj)
    if hasattr(obj, 'model_dump') and callable(obj.model_dump):
        return obj.model_dump()
    if hasattr(obj, 'dict') and callable(obj.dict):
        return obj.dict()
    try:
        _seen.remove(obj_id)
        return str(obj)
    except Exception:
        _seen.remove(obj_id)
        return f"<unserializable object of type {type(obj)}>"

# --- Base Classes for OmniCore Components ---
class Base(ABC):
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

# --- Metrics Functions ---
def get_plugin_metrics() -> dict:
    try:
        from omnicore_engine.metrics import get_plugin_metrics as actual_get_plugin_metrics
        return actual_get_plugin_metrics()
    except ImportError:
        logger.warning("omnicore_engine.metrics not available. Cannot retrieve actual plugin metrics.")
        return {"error": "Metrics module not available", "message": "Install omnicore_engine.metrics for full functionality"}
    except Exception as e:
        logger.error(f"Error retrieving plugin metrics: {e}", exc_info=True)
        return {"error": str(e), "message": "Failed to retrieve plugin metrics"}

def get_test_metrics() -> dict:
    try:
        from omnicore_engine.metrics import get_test_metrics as actual_get_test_metrics
        return actual_get_test_metrics()
    except ImportError:
        logger.warning("omnicore_engine.metrics not available. Cannot retrieve actual test metrics.")
        return {"error": "Metrics module not available", "message": "Install omnicore_engine.metrics for full functionality"}
    except Exception as e:
        logger.error(f"Error retrieving test metrics: {e}", exc_info=True)
        return {"error": str(e), "message": "Failed to retrieve test metrics"}

# --- Explainable AI Core ---
class ExplainableAI:
    def __init__(self):
        self.reasoner = None
        self.logger = logger
        self.is_initialized = False

    async def initialize(self):
        if not self.is_initialized:
            try:
                from omnicore_engine.explainable_reasoner import ExplainableReasonerPlugin
                self.reasoner = ExplainableReasonerPlugin(settings=settings)
                await self.reasoner.initialize()
                self.is_initialized = True
                self.logger.info("Explainable AI core initialized.")
            except ImportError as e:
                self.logger.warning(f"Failed to import ExplainableReasonerPlugin: {e}. Explainable AI features will be unavailable.")
                self.is_initialized = False

    async def shutdown(self):
        if self.is_initialized and self.reasoner:
            try:
                await self.reasoner.shutdown()
                self.is_initialized = False
                self.logger.info("Explainable AI core shut down.")
            except Exception as e:
                self.logger.error(f"Error shutting down Explainable AI: {e}", exc_info=True)

    async def explain_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_initialized or not self.reasoner:
            self.logger.warning("Explainable AI core not initialized or reasoner unavailable.")
            return {"error": "Explainable AI core not available."}
        try:
            explanation_result = await self.reasoner.explain(
                query=event_data.get("query", "explain this event"),
                context=event_data.get("context", {})
            )
            return {"explanation": explanation_result.get("explanation", "No explanation provided.")}
        except Exception as e:
            self.logger.error(f"Error generating explanation: {e}", exc_info=True)
            return {"error": str(e)}

    async def reason_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_initialized or not self.reasoner:
            self.logger.warning("Explainable AI core not initialized or reasoner unavailable.")
            return {"error": "Explainable AI core not available."}
        try:
            reasoning_result = await self.reasoner.reason(
                query=event_data.get("query", "reason about this event"),
                context=event_data.get("context", {})
            )
            return {"reasoning": reasoning_result.get("reasoning", "No reasoning provided.")}
        except Exception as e:
            self.logger.error(f"Error generating reasoning: {e}", exc_info=True)
            return {"error": str(e)}

# --- Merkle Tree ---
class MerkleTree:
    def __init__(self):
        self.leaves = []
        self.root = None
        self.logger = logger
        self.logger.info("MerkleTree placeholder initialized.")

    def add_leaf(self, data: bytes):
        hashed_data = hashlib.sha256(data).hexdigest()
        self.leaves.append(hashed_data)
        self._recalculate_root()
        self.logger.debug(f"Added leaf: {hashed_data[:10]}... to Merkle tree.")

    def _recalculate_root(self):
        if not self.leaves:
            self.root = None
            return
        current_level = list(self.leaves)
        while len(current_level) > 1:
            next_level = []
            for i in range(0, len(current_level), 2):
                if i + 1 < len(current_level):
                    combined_hash = hashlib.sha256(
                        (current_level[i] + current_level[i+1]).encode('utf-8')
                    ).hexdigest()
                    next_level.append(combined_hash)
                else:
                    next_level.append(current_level[i])
            current_level = next_level
        self.root = current_level[0]
        self.logger.debug(f"Merkle root recalculated: {self.root[:10]}...")

    def verify_proof(self, leaf_data: bytes, root: str, proof: List[str]) -> bool:
        hashed_leaf = hashlib.sha256(leaf_data).hexdigest()
        current_hash = hashed_leaf
        for p in proof:
            if p < current_hash:
                current_hash = hashlib.sha256((p + current_hash).encode('utf-8')).hexdigest()
            else:
                current_hash = hashlib.sha256((current_hash + p).encode('utf-8')).hexdigest()
        return current_hash == root

    def get_proof(self, leaf_data: bytes) -> List[str]:
        hashed_leaf = hashlib.sha256(leaf_data).hexdigest()
        if hashed_leaf not in self.leaves:
            raise ValueError("Leaf not found in tree.")
        index = self.leaves.index(hashed_leaf)
        proof = []
        current_level = list(self.leaves)
        while len(current_level) > 1:
            next_level = []
            for i in range(0, len(current_level), 2):
                if i == index:
                    if (i + 1) < len(current_level):
                        proof.append(current_level[i+1])
                elif i + 1 == index:
                    if i >= 0:
                        proof.append(current_level[i])
                if i + 1 < len(current_level):
                    combined_hash = hashlib.sha256(
                        (current_level[i] + current_level[i+1]).encode('utf-8')
                    ).hexdigest()
                    next_level.append(combined_hash)
                else:
                    next_level.append(current_level[i])
            index = index // 2
            current_level = next_level
        return proof

# --- OmniCore Engine ---
class OmniCoreEngine:
    def __init__(self, settings: BaseSettings = settings):
        self.settings = settings
        self.components: Dict[str, Any] = {}
        self.component_locks: Dict[str, asyncio.Lock] = {}
        self.logger = logger
        self._is_initialized = False
        self.knowledge_graph = None
        self.decision_optimizer = None
        self.array_backend = None
        self.message_bus = None
        self.arbiter_growth_manager = None
        self.database = None # Added database attribute
        self.audit = None # Added audit attribute

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def _initialize_component_instance(self, name: str, component_class: Type[Base], *args, **kwargs):
        """Helper to initialize and store a component, handling locks."""
        if name not in self.component_locks:
            self.component_locks[name] = asyncio.Lock()

        async with self.component_locks[name]:
            if name in self.components:
                self.logger.debug(f"Component '{name}' already initialized.")
                return

            try:
                self.logger.info(f"Initializing component: {name}...")
                instance = component_class(*args, **kwargs)
                if hasattr(instance, 'initialize') and callable(instance.initialize):
                    await instance.initialize()
                self.components[name] = instance
                self.logger.info(f"Component '{name}' initialized successfully.")
            except Exception as e:
                self.logger.error(f"Failed to initialize component '{name}': {e}", exc_info=True)
                raise

    async def _get_component_instance(self, name: str) -> Optional[Any]:
        """Helper to retrieve an initialized component instance."""
        async with self.component_locks.get(name, asyncio.Lock()): # Use a dummy lock if not already present
            return self.components.get(name)

    async def get_component(self, name: str) -> Optional[Any]:
        return self.components.get(name)

    async def initialize(self):
        if self._is_initialized:
            self.logger.info("OmniCore Engine: Already initialized.")
            return
        self.logger.info("OmniCore Engine: Starting application components...")

        try:
            from omnicore_engine.knowledge_graph import KnowledgeGraph
            from omnicore_engine.explainable_reasoner import ExplainableReasonerPlugin
            from omnicore_engine.database import Database
            from omnicore_engine.audit import ExplainAudit
            from omnicore_engine.feedback_manager import FeedbackManager
            from omnicore_engine.plugin_registry import PLUGIN_REGISTRY, PlugInKind, start_plugin_observer, Plugin, PluginMeta
            from omnicore_engine.message_bus import ShardedMessageBus, PluginMessageBusAdapter, MessageFilter
            from omnicore_engine.array_backend import ArrayBackend
            from omnicore_engine.decision_optimizer import DecisionOptimizer
            from omnicore_engine.arbiter_growth import ArbiterGrowthManager
            from omnicore_engine.database.backends.sqlite import SQLiteStorageBackend # Added import
            from sqlalchemy.orm import sessionmaker
            from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
        except ImportError as e:
            self.logger.error(f"Failed to import required modules: {e}", exc_info=True)
            raise RuntimeError(f"Module import failed: {e}")

        try:
            self.knowledge_graph = KnowledgeGraph(config=getattr(self.settings, "knowledge_graph_config", {}))
        except Exception as e:
            self.logger.warning(f"Failed to initialize KnowledgeGraph: {e}. KnowledgeGraph features will be unavailable.", exc_info=True)
            self.knowledge_graph = None

        try:
            self.decision_optimizer = DecisionOptimizer(
                PLUGIN_REGISTRY, self.settings, self.logger, safe_serialize,
                config=getattr(self.settings, "decision_optimizer_config", {})
            )
        except Exception as e:
            self.logger.warning(f"Failed to initialize DecisionOptimizer: {e}. DecisionOptimizer features will be unavailable.", exc_info=True)
            self.decision_optimizer = None

        try:
            self.array_backend = ArrayBackend(
                mode=getattr(self.settings, "array_backend_mode", "numpy"),
                use_gpu=getattr(self.settings, "use_gpu", False),
                use_dask=getattr(self.settings, "use_dask", False),
                use_quantum=getattr(self.settings, "use_quantum", False),
                use_neuromorphic=getattr(self.settings, "use_neuromorphic", False)
            )
        except Exception as e:
            self.logger.warning(f"Failed to initialize ArrayBackend: {e}. ArrayBackend features will be unavailable.", exc_info=True)
            self.array_backend = None

        try:
            self.message_bus = ShardedMessageBus(config=self.settings, db=None, audit_client=None)
        except Exception as e:
            self.logger.error(f"Failed to initialize ShardedMessageBus: {e}", exc_info=True)
            raise RuntimeError(f"MessageBus initialization failed: {e}")

        try:
            system_audit_merkle_tree = MerkleTree()
            self.logger.info("MerkleTree initialized for audit system.")
        except Exception as e:
            self.logger.warning(f"MerkleTree instantiation failed: {e}. Audit integrity features will be limited.", exc_info=True)
            system_audit_merkle_tree = None

        try:
            await self._initialize_component_instance("database", Database, self.settings.database_path, system_audit_merkle_tree=system_audit_merkle_tree)
            self.database = await self._get_component_instance("database")
        except Exception as e:
            self.logger.error(f"Failed to initialize database: {e}", exc_info=True)
            raise RuntimeError(f"Database initialization failed: {e}")

        try:
            await self._initialize_component_instance("audit", ExplainAudit, system_audit_merkle_tree=system_audit_merkle_tree)
            self.audit = await self._get_component_instance("audit")
        except Exception as e:
            self.logger.error(f"Failed to initialize audit: {e}", exc_info=True)
            raise RuntimeError(f"Audit initialization failed: {e}")

        try:
            if self.message_bus:
                self.message_bus.db = self.database
                self.message_bus.audit_client = self.audit
                engine_type = getattr(self.settings, "engine_type", "simulation")
                await self.message_bus.configure_for_omnicore(engine_type)
                await self._initialize_component_instance("message_bus_component", type("MessageBusComponent", (Base,), {
                    "initialize": lambda self_comp: self.logger.debug("MessageBus already initialized and configured by OmniCoreEngine."),
                    "shutdown": lambda self_comp: self.message_bus.shutdown(),
                    "health_check": lambda self_comp: {
                        "status": "ok" if self.message_bus.running else "stopped",
                        "queue_sizes_normal": {f"shard_{i}": q.qsize() for i, q in enumerate(self.message_bus.queues)},
                        "queue_sizes_hp": {f"shard_{i}": q.qsize() for i, q in enumerate(self.message_bus.high_priority_queues)},
                        "kafka_circuit": self.message_bus.kafka_circuit.state,
                        "redis_circuit": self.message_bus.redis_circuit.state,
                        "total_subscribers": sum(len(v) for v in self.message_bus.subscribers.values()) + sum(len(v) for v in self.message_bus.regex_subscribers.values())
                    },
                    "is_healthy": property(lambda self_comp: self.message_bus.running)
                }), settings=self.settings)
        except Exception as e:
            self.logger.error(f"Failed to finalize message bus setup: {e}", exc_info=True)
            raise RuntimeError(f"MessageBus final setup failed: {e}")

        try:
            PLUGIN_REGISTRY.db = self.database
            PLUGIN_REGISTRY.audit_client = self.audit
            if self.message_bus:
                PLUGIN_REGISTRY.set_message_bus(self.message_bus)

            async def _plugin_registry_initialize(self_comp_instance):
                await PLUGIN_REGISTRY.load_from_directory(self_comp_instance.settings.plugin_dir)

            await self._initialize_component_instance("plugin_registry", type("PluginRegistryComponent", (Base,), {
                "initialize": _plugin_registry_initialize,
                "shutdown": lambda self_comp: self.logger.info("Plugin registry shutdown"),
                "health_check": lambda self_comp: {
                    "status": "ok",
                    "plugins_loaded": sum(len(k) for k in PLUGIN_REGISTRY.plugins.values())
                },
                "is_healthy": property(lambda self_comp: True)
            }), settings=self.settings)
            self.plugin_registry = await self._get_component_instance("plugin_registry")
            start_plugin_observer(PLUGIN_REGISTRY, self.settings.plugin_dir)

            PLUGIN_REGISTRY.load_ai_assistant_plugins()
        except Exception as e:
            self.logger.error(f"Failed to initialize plugin registry: {e}", exc_info=True)
            raise RuntimeError(f"Plugin registry initialization failed: {e}")

        try:
            await self._initialize_component_instance("feedback_manager", FeedbackManager,
                db_dsn=self.settings.database_path,
                redis_url=self.settings.redis_url,
                encryption_key=self.settings.encryption_key.get_secret_value()
            )
            self.feedback_manager = await self._get_component_instance("feedback_manager")
        except Exception as e:
            self.logger.error(f"Failed to initialize feedback manager: {e}", exc_info=True)
            raise RuntimeError(f"Feedback manager initialization failed: {e}")

        try:
            explainable_ai_instance = ExplainableAI()
            async def _explainable_ai_initialize(self_comp_instance):
                await explainable_ai_instance.initialize()

            await self._initialize_component_instance("explainable_ai", type("ExplainableAIComponent", (Base,), {
                "initialize": _explainable_ai_initialize,
                "shutdown": lambda self_comp: explainable_ai_instance.shutdown(),
                "health_check": lambda self_comp: {"status": "ok", "reasoner_initialized": explainable_ai_instance.is_initialized},
                "is_healthy": property(lambda self_comp: explainable_ai_instance.is_initialized)
            }), settings=self.settings)
            self.explainable_ai = await self._get_component_instance("explainable_ai")
        except Exception as e:
            self.logger.error(f"Failed to initialize explainable AI: {e}", exc_info=True)
            raise RuntimeError(f"Explainable AI initialization failed: {e}")

        try:
            if self.database and self.knowledge_graph:
                session_factory = self.database.AsyncSessionLocal
                storage_backend_instance = SQLiteStorageBackend(
                    session_factory=session_factory,
                    encryption_key=self.settings.encryption_key_bytes
                )

                await self._initialize_component_instance("arbiter_growth_manager", ArbiterGrowthManager,
                    arbiter_name="default_arbiter",
                    storage_backend=storage_backend_instance,
                    knowledge_graph=self.knowledge_graph
                )
                self.arbiter_growth_manager = await self._get_component_instance("arbiter_growth_manager")

                if self.arbiter_growth_manager:
                    meta = PluginMeta(name="arbiter_growth", kind=PlugInKind.GROWTH_MANAGER.value, description="Manages Arbiter's growth and skill progression.")
                    growth_plugin_instance = Plugin(meta=meta, fn=self.arbiter_growth_manager)
                    PLUGIN_REGISTRY.register(PlugInKind.GROWTH_MANAGER.value, "arbiter_growth", growth_plugin_instance)
                    self.logger.info("ArbiterGrowthManager registered as a plugin.")
            else:
                self.logger.warning("Database or KnowledgeGraph not initialized. Skipping ArbiterGrowthManager initialization.")
        except Exception as e:
            self.logger.error(f"Failed to initialize ArbiterGrowthManager: {e}", exc_info=True)
            raise RuntimeError(f"ArbiterGrowthManager initialization failed: {e}")

        for component_instance, name_str in [
            (self.knowledge_graph, "knowledge_graph"),
            (self.decision_optimizer, "decision_optimizer"),
            (self.array_backend, "array_backend"),
            (self.message_bus, "message_bus"),
            (self.arbiter_growth_manager, "arbiter_growth_manager")
        ]:
            if component_instance:
                try:
                    class CoreServicePlugin(Plugin):
                        def __init__(self, meta: PluginMeta, component_instance: Any, adapter: PluginMessageBusAdapter):
                            super().__init__(meta, component_instance)
                            self.component_instance = component_instance
                            self.message_bus_adapter = adapter

                        async def execute(self, *args, **kwargs) -> Any:
                            self.logger.debug(f"Executing generic CoreServicePlugin wrapper for {self.meta.name}")
                            if hasattr(self.component_instance, 'execute') and callable(self.component_instance.execute):
                                return await self.component_instance.execute(*args, **kwargs)
                            if self.meta.name == "array_backend" and hasattr(self.component_instance, 'handle_computation_task'):
                                self.logger.warning("Direct 'execute' call on ArrayBackend plugin. Consider using message bus directly for computations.")
                                return {"error": "ArrayBackend plugin does not support direct 'execute' calls for computation. Use message bus instead."}
                            return {"status": f"Core service {self.meta.name} executed (no specific execute method found)", "args": args, "kwargs": kwargs}

                    plugin_bus_adapter = PluginMessageBusAdapter(self.message_bus, name_str)
                    component_kind = PlugInKind.GROWTH_MANAGER.value if name_str == "arbiter_growth_manager" else PlugInKind.CORE_SERVICE.value
                    meta = PluginMeta(name=name_str, kind=component_kind, description=f"{name_str} core service")
                    core_plugin = CoreServicePlugin(meta=meta, component_instance=component_instance, adapter=plugin_bus_adapter)

                    if name_str == "array_backend" and self.array_backend:
                        self.array_backend.set_message_bus(self.message_bus)

                    PLUGIN_REGISTRY.register(component_kind, name_str, core_plugin)
                    self.logger.info(f"{name_str} registered as core service plugin.")
                except Exception as e:
                    self.logger.error(f"Failed to register {name_str} as core service plugin: {e}", exc_info=True)

        try:
            if self.message_bus:
                self.message_bus.subscribe("system.shutdown", self._handle_shutdown)
                self.message_bus.subscribe("system.config_changed", self._handle_config_change)
                self.message_bus.subscribe("system.error", self._handle_system_error, MessageFilter(lambda p: p.get("severity", 0) >= 5))
            else:
                self.logger.warning("Message bus not initialized, skipping system event subscriptions.")
        except Exception as e:
            self.logger.error(f"Failed to subscribe to system events: {e}")

        self._is_initialized = True
        self.logger.info("OmniCore Engine: All components initialized.")

    async def shutdown(self):
        if not self._is_initialized:
            self.logger.info("OmniCore Engine: Already shut down.")
            return
        self.logger.info("OmniCore Engine: Shutting down application components...")
        for name, component in self.components.items():
            if hasattr(component, 'shutdown') and callable(component.shutdown):
                try:
                    await component.shutdown()
                    self.logger.info(f"Component '{name}' shut down successfully.")
                except Exception as e:
                    self.logger.error(f"Error shutting down component '{name}': {e}", exc_info=True)
        self._is_initialized = False
        self.logger.info("OmniCore Engine: All components shut down.")

    async def health_check(self) -> Dict[str, Any]:
        overall_status = "ok"
        health_report = {}
        for name, component in self.components.items():
            if hasattr(component, 'health_check') and callable(component.health_check):
                try:
                    status = await component.health_check()
                    health_report[name] = status
                    if status.get("status") == "unhealthy":
                        overall_status = "unhealthy"
                except Exception as e:
                    self.logger.error(f"Health check failed for component '{name}': {e}", exc_info=True)
                    health_report[name] = {"status": "error", "message": str(e)}
                    overall_status = "unhealthy"
            else:
                health_report[name] = {"status": "unknown", "message": "No health_check method"}
        health_report["overall_status"] = overall_status
        return health_report

    @property
    def is_healthy(self) -> bool:
        # This property should reflect the overall health based on component statuses
        # For simplicity, returning True if initialized, but a full implementation
        # would iterate through components' is_healthy properties.
        return self._is_initialized and all(
            getattr(comp, 'is_healthy', True) for comp in self.components.values()
        )

    async def _handle_shutdown(self, message: Dict[str, Any]):
        self.logger.info(f"Received system shutdown message: {message.get('reason', 'No reason provided')}. Initiating shutdown...")
        await self.shutdown()

    async def _handle_config_change(self, message: Dict[str, Any]):
        self.logger.info(f"Received system config_changed message: {message.get('changes', 'No changes provided')}. Reinitializing components...")
        # A more robust implementation would selectively reinitialize or update components
        # For now, a full shutdown and re-initialization is a simple approach.
        await self.shutdown()
        await self.initialize()

    async def _handle_system_error(self, message: Dict[str, Any]):
        self.logger.error(f"Received system error message: {message.get('error', 'Unknown error')}. Severity: {message.get('severity', 'N/A')}")
        # Implement error handling logic, e.g., send alerts, log to external system, trigger self-healing.


# --- Exported singleton for main entry ---
omnicore_engine = OmniCoreEngine(settings=settings)
"""
Test suite for omnicore_engine/core.py
Tests the core orchestration engine, component initialization, and utility functions.
"""

import pytest
import asyncio
import json
import hashlib
from datetime import datetime, date
from decimal import Decimal
from uuid import UUID
import numpy as np
from unittest.mock import Mock, MagicMock, patch, AsyncMock, PropertyMock
from pathlib import Path
import sys
import os

# Add the parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnicore_engine.core import (
    safe_serialize,
    Base,
    get_plugin_metrics,
    get_test_metrics,
    ExplainableAI,
    MerkleTree,
    OmniCoreEngine,
    omnicore_engine,
    configure_logging
)


class TestSafeSerialize:
    """Test the safe_serialize utility function"""
    
    def test_primitive_types(self):
        """Test serialization of primitive types"""
        assert safe_serialize("string") == "string"
        assert safe_serialize(42) == 42
        assert safe_serialize(3.14) == 3.14
        assert safe_serialize(True) == True
        assert safe_serialize(None) == None
    
    def test_datetime_serialization(self):
        """Test datetime and date serialization"""
        dt = datetime(2024, 1, 1, 12, 0, 0)
        assert safe_serialize(dt) == "2024-01-01T12:00:00"
        
        d = date(2024, 1, 1)
        assert safe_serialize(d) == "2024-01-01"
    
    def test_bytes_serialization(self):
        """Test bytes serialization"""
        assert safe_serialize(b"hello") == "hello"
        assert safe_serialize(b"\x00\x01\x02") == "\x00\x01\x02"
    
    def test_collection_serialization(self):
        """Test serialization of collections"""
        # Set and frozenset
        assert safe_serialize({1, 2, 3}) == [1, 2, 3]
        assert safe_serialize(frozenset([1, 2])) == [1, 2]
        
        # List and tuple
        assert safe_serialize([1, 2, 3]) == [1, 2, 3]
        assert safe_serialize((1, 2, 3)) == [1, 2, 3]
        
        # Dict
        assert safe_serialize({"key": "value"}) == {"key": "value"}
    
    def test_numpy_serialization(self):
        """Test NumPy array serialization"""
        arr = np.array([1, 2, 3])
        assert safe_serialize(arr) == [1, 2, 3]
        
        scalar = np.float32(3.14)
        assert safe_serialize(scalar) == pytest.approx(3.14)
    
    def test_decimal_uuid_serialization(self):
        """Test Decimal and UUID serialization"""
        d = Decimal("3.14159")
        assert safe_serialize(d) == pytest.approx(3.14159)
        
        u = UUID("12345678-1234-5678-1234-567812345678")
        assert safe_serialize(u) == "12345678-1234-5678-1234-567812345678"
    
    def test_circular_reference_handling(self):
        """Test handling of circular references"""
        obj = {}
        obj['self'] = obj
        
        result = safe_serialize(obj)
        assert "<<<CIRCULAR REFERENCE" in str(result['self'])
    
    def test_model_dump_objects(self):
        """Test objects with model_dump method"""
        mock_obj = Mock()
        mock_obj.model_dump.return_value = {"field": "value"}
        
        assert safe_serialize(mock_obj) == {"field": "value"}
    
    def test_dict_method_objects(self):
        """Test objects with dict method"""
        mock_obj = Mock()
        mock_obj.dict.return_value = {"field": "value"}
        del mock_obj.model_dump  # Ensure model_dump doesn't exist
        
        assert safe_serialize(mock_obj) == {"field": "value"}
    
    def test_unserializable_objects(self):
        """Test handling of unserializable objects"""
        class UnserializableClass:
            def __str__(self):
                raise Exception("Cannot convert to string")
        
        obj = UnserializableClass()
        result = safe_serialize(obj)
        assert "<unserializable object" in result


class TestBase:
    """Test the Base abstract class"""
    
    def test_base_cannot_be_instantiated(self):
        """Test that Base class cannot be directly instantiated"""
        with pytest.raises(TypeError):
            Base()
    
    def test_base_subclass_implementation(self):
        """Test proper subclass implementation of Base"""
        class TestComponent(Base):
            async def initialize(self):
                pass
            
            async def shutdown(self):
                pass
            
            async def health_check(self):
                return {"status": "ok"}
            
            @property
            def is_healthy(self):
                return True
        
        component = TestComponent()
        assert hasattr(component, 'settings')


class TestMetricsFunctions:
    """Test plugin and test metrics functions"""
    
    @patch('omnicore_engine.core.actual_get_plugin_metrics')
    def test_get_plugin_metrics_success(self, mock_metrics):
        """Test successful plugin metrics retrieval"""
        mock_metrics.return_value = {"metric1": 10, "metric2": 20}
        
        with patch.dict('sys.modules', {'omnicore_engine.metrics': Mock(get_plugin_metrics=mock_metrics)}):
            result = get_plugin_metrics()
            assert result == {"metric1": 10, "metric2": 20}
    
    def test_get_plugin_metrics_import_error(self):
        """Test plugin metrics when module not available"""
        with patch.dict('sys.modules', {'omnicore_engine.metrics': None}):
            result = get_plugin_metrics()
            assert "error" in result
            assert "Metrics module not available" in result["error"]
    
    @patch('omnicore_engine.core.actual_get_test_metrics')
    def test_get_test_metrics_success(self, mock_metrics):
        """Test successful test metrics retrieval"""
        mock_metrics.return_value = {"tests_run": 100, "tests_passed": 95}
        
        with patch.dict('sys.modules', {'omnicore_engine.metrics': Mock(get_test_metrics=mock_metrics)}):
            result = get_test_metrics()
            assert result == {"tests_run": 100, "tests_passed": 95}


class TestExplainableAI:
    """Test ExplainableAI class"""
    
    @pytest.mark.asyncio
    async def test_initialization_success(self):
        """Test successful initialization of ExplainableAI"""
        mock_reasoner = Mock()
        mock_reasoner.initialize = AsyncMock()
        
        with patch('omnicore_engine.core.ExplainableReasonerPlugin', return_value=mock_reasoner):
            ai = ExplainableAI()
            await ai.initialize()
            
            assert ai.is_initialized
            assert ai.reasoner is not None
            mock_reasoner.initialize.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_initialization_import_error(self):
        """Test initialization when reasoner module not available"""
        with patch.dict('sys.modules', {'omnicore_engine.explainable_reasoner': None}):
            ai = ExplainableAI()
            await ai.initialize()
            
            assert not ai.is_initialized
            assert ai.reasoner is None
    
    @pytest.mark.asyncio
    async def test_shutdown(self):
        """Test shutdown of ExplainableAI"""
        ai = ExplainableAI()
        ai.is_initialized = True
        ai.reasoner = Mock()
        ai.reasoner.shutdown = AsyncMock()
        
        await ai.shutdown()
        
        assert not ai.is_initialized
        ai.reasoner.shutdown.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_explain_event_success(self):
        """Test successful event explanation"""
        ai = ExplainableAI()
        ai.is_initialized = True
        ai.reasoner = Mock()
        ai.reasoner.explain = AsyncMock(return_value={"explanation": "Test explanation"})
        
        result = await ai.explain_event({"query": "test", "context": {}})
        
        assert result == {"explanation": "Test explanation"}
    
    @pytest.mark.asyncio
    async def test_explain_event_not_initialized(self):
        """Test explain_event when not initialized"""
        ai = ExplainableAI()
        ai.is_initialized = False
        
        result = await ai.explain_event({"query": "test"})
        
        assert "error" in result
        assert "not available" in result["error"]
    
    @pytest.mark.asyncio
    async def test_reason_event_success(self):
        """Test successful event reasoning"""
        ai = ExplainableAI()
        ai.is_initialized = True
        ai.reasoner = Mock()
        ai.reasoner.reason = AsyncMock(return_value={"reasoning": "Test reasoning"})
        
        result = await ai.reason_event({"query": "test", "context": {}})
        
        assert result == {"reasoning": "Test reasoning"}


class TestMerkleTree:
    """Test MerkleTree class"""
    
    def test_empty_tree(self):
        """Test empty Merkle tree"""
        tree = MerkleTree()
        assert tree.root is None
        assert tree.leaves == []
    
    def test_add_single_leaf(self):
        """Test adding a single leaf"""
        tree = MerkleTree()
        tree.add_leaf(b"test_data")
        
        assert len(tree.leaves) == 1
        assert tree.root is not None
        assert tree.root == tree.leaves[0]
    
    def test_add_multiple_leaves(self):
        """Test adding multiple leaves"""
        tree = MerkleTree()
        
        for i in range(4):
            tree.add_leaf(f"data_{i}".encode())
        
        assert len(tree.leaves) == 4
        assert tree.root is not None
    
    def test_verify_proof_valid(self):
        """Test proof verification with valid proof"""
        tree = MerkleTree()
        
        leaf_data = b"test_leaf"
        tree.add_leaf(leaf_data)
        tree.add_leaf(b"another_leaf")
        
        proof = tree.get_proof(leaf_data)
        assert tree.verify_proof(leaf_data, tree.root, proof)
    
    def test_verify_proof_invalid(self):
        """Test proof verification with invalid proof"""
        tree = MerkleTree()
        
        tree.add_leaf(b"leaf1")
        tree.add_leaf(b"leaf2")
        
        # Invalid proof
        assert not tree.verify_proof(b"invalid_leaf", tree.root, [])
    
    def test_get_proof_leaf_not_found(self):
        """Test get_proof with non-existent leaf"""
        tree = MerkleTree()
        tree.add_leaf(b"existing_leaf")
        
        with pytest.raises(ValueError, match="Leaf not found"):
            tree.get_proof(b"non_existent_leaf")


class TestOmniCoreEngine:
    """Test OmniCoreEngine class"""
    
    @pytest.fixture
    def mock_settings(self):
        """Create mock settings"""
        settings = Mock()
        settings.database_path = "sqlite:///:memory:"
        settings.redis_url = "redis://localhost"
        settings.plugin_dir = "/tmp/plugins"
        settings.encryption_key = Mock(get_secret_value=lambda: "test_key")
        settings.encryption_key_bytes = b"test_key_bytes"
        return settings
    
    @pytest.fixture
    def engine(self, mock_settings):
        """Create engine instance with mock settings"""
        return OmniCoreEngine(mock_settings)
    
    def test_initialization_state(self, engine):
        """Test initial state of engine"""
        assert not engine.is_initialized
        assert engine.components == {}
        assert engine.component_locks == {}
    
    @pytest.mark.asyncio
    async def test_initialize_already_initialized(self, engine):
        """Test initialize when already initialized"""
        engine._is_initialized = True
        
        await engine.initialize()
        
        # Should return early without initializing components
        assert engine.components == {}
    
    @pytest.mark.asyncio
    async def test_component_initialization(self, engine):
        """Test component initialization helper"""
        mock_component_class = Mock()
        mock_instance = Mock()
        mock_instance.initialize = AsyncMock()
        mock_component_class.return_value = mock_instance
        
        await engine._initialize_component_instance(
            "test_component",
            mock_component_class,
            arg1="value1"
        )
        
        assert "test_component" in engine.components
        assert engine.components["test_component"] == mock_instance
        mock_instance.initialize.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_component_already_initialized(self, engine):
        """Test initializing already initialized component"""
        mock_instance = Mock()
        engine.components["test_component"] = mock_instance
        engine.component_locks["test_component"] = asyncio.Lock()
        
        mock_component_class = Mock()
        
        await engine._initialize_component_instance(
            "test_component",
            mock_component_class
        )
        
        # Should not create new instance
        mock_component_class.assert_not_called()
        assert engine.components["test_component"] == mock_instance
    
    @pytest.mark.asyncio
    async def test_get_component(self, engine):
        """Test getting a component"""
        mock_component = Mock()
        engine.components["test"] = mock_component
        
        result = await engine.get_component("test")
        assert result == mock_component
        
        result = await engine.get_component("nonexistent")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_shutdown(self, engine):
        """Test engine shutdown"""
        mock_component1 = Mock()
        mock_component1.shutdown = AsyncMock()
        mock_component2 = Mock()
        mock_component2.shutdown = AsyncMock()
        
        engine._is_initialized = True
        engine.components = {
            "comp1": mock_component1,
            "comp2": mock_component2
        }
        
        await engine.shutdown()
        
        assert not engine._is_initialized
        mock_component1.shutdown.assert_called_once()
        mock_component2.shutdown.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_shutdown_not_initialized(self, engine):
        """Test shutdown when not initialized"""
        engine._is_initialized = False
        mock_component = Mock()
        mock_component.shutdown = AsyncMock()
        engine.components = {"comp": mock_component}
        
        await engine.shutdown()
        
        # Should return early
        mock_component.shutdown.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_health_check(self, engine):
        """Test health check"""
        mock_comp1 = Mock()
        mock_comp1.health_check = AsyncMock(return_value={"status": "ok"})
        
        mock_comp2 = Mock()
        mock_comp2.health_check = AsyncMock(return_value={"status": "unhealthy"})
        
        mock_comp3 = Mock()  # No health_check method
        
        engine.components = {
            "comp1": mock_comp1,
            "comp2": mock_comp2,
            "comp3": mock_comp3
        }
        
        result = await engine.health_check()
        
        assert result["overall_status"] == "unhealthy"
        assert result["comp1"]["status"] == "ok"
        assert result["comp2"]["status"] == "unhealthy"
        assert result["comp3"]["status"] == "unknown"
    
    @pytest.mark.asyncio
    async def test_health_check_exception(self, engine):
        """Test health check with exception"""
        mock_comp = Mock()
        mock_comp.health_check = AsyncMock(side_effect=Exception("Test error"))
        
        engine.components = {"comp": mock_comp}
        
        result = await engine.health_check()
        
        assert result["overall_status"] == "unhealthy"
        assert result["comp"]["status"] == "error"
        assert "Test error" in result["comp"]["message"]
    
    def test_is_healthy_property(self, engine):
        """Test is_healthy property"""
        # Not initialized
        assert not engine.is_healthy
        
        # Initialized with healthy components
        engine._is_initialized = True
        mock_comp = Mock()
        mock_comp.is_healthy = True
        engine.components = {"comp": mock_comp}
        
        assert engine.is_healthy
        
        # Initialized with unhealthy component
        mock_comp.is_healthy = False
        assert not engine.is_healthy
    
    @pytest.mark.asyncio
    async def test_handle_shutdown_message(self, engine):
        """Test handling shutdown message"""
        engine.shutdown = AsyncMock()
        
        await engine._handle_shutdown({"reason": "test shutdown"})
        
        engine.shutdown.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_config_change_message(self, engine):
        """Test handling config change message"""
        engine.shutdown = AsyncMock()
        engine.initialize = AsyncMock()
        
        await engine._handle_config_change({"changes": "test changes"})
        
        engine.shutdown.assert_called_once()
        engine.initialize.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_system_error_message(self, engine):
        """Test handling system error message"""
        await engine._handle_system_error({
            "error": "test error",
            "severity": 5
        })
        
        # Should log error (check via logger mock if needed)


class TestGlobalSingleton:
    """Test the global omnicore_engine singleton"""
    
    def test_singleton_exists(self):
        """Test that global singleton exists"""
        assert omnicore_engine is not None
        assert isinstance(omnicore_engine, OmniCoreEngine)
    
    def test_singleton_has_settings(self):
        """Test that singleton has settings"""
        assert hasattr(omnicore_engine, 'settings')


class TestLoggingConfiguration:
    """Test logging configuration"""
    
    @patch('omnicore_engine.core.structlog.configure')
    @patch('omnicore_engine.core.logging.basicConfig')
    def test_configure_logging(self, mock_basic_config, mock_structlog_configure):
        """Test logging configuration"""
        configure_logging()
        
        mock_structlog_configure.assert_called_once()
        mock_basic_config.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
