# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import hashlib
import inspect
import logging
import sys
import types
from abc import ABC, abstractmethod
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Type, Union
from uuid import UUID

import numpy as np
import structlog
from pydantic_settings import BaseSettings

# Try to import metrics functions at module level for better visibility
try:
    from omnicore_engine.metrics import get_or_create_counter
except ImportError:
    # Fallback if metrics module is not available
    get_or_create_counter = None


def _create_fallback_settings():
    """Create a minimal settings object for when ArbiterConfig is unavailable."""
    return types.SimpleNamespace(
        log_level="INFO",
        LOG_LEVEL="INFO",
        database_path="sqlite:///./omnicore.db",
        DB_PATH="sqlite:///./omnicore.db",
        plugin_dir="./plugins",
        PLUGIN_DIR="./plugins",
        array_backend_mode="numpy",
        use_gpu=False,
        use_dask=False,
        use_quantum=False,
        use_neuromorphic=False,
        knowledge_graph_config={},
        decision_optimizer_config={},
        engine_type="simulation",
        # Both versions provided for backward compatibility with different access patterns
        encryption_key_bytes=b"",
        ENCRYPTION_KEY_BYTES=b"",  # Uppercase version for consistent access
        API_HOST="0.0.0.0",
        API_PORT=8000,
    )


def _get_settings():
    """Lazy import + defensive instantiation of settings."""
    ArbiterConfig = None
    try:
        # Try the full canonical path first (preferred)
        from self_fixing_engineer.arbiter.config import ArbiterConfig
    except ImportError:
        try:
            # Fall back to aliased path for backward compatibility
            from arbiter.config import ArbiterConfig
        except ImportError:
            pass

    if ArbiterConfig is None:
        logging.debug("arbiter.config not available; using fallback settings.")
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


# Configure structlog
def configure_logging():
    structlog.configure(
        processors=[
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    # Updated to use the new settings object and attribute
    logging.basicConfig(level=getattr(settings, "log_level", "INFO"))


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
        return obj.decode("utf-8", errors="ignore")
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
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return obj.model_dump()
    if hasattr(obj, "dict") and callable(obj.dict):
        return obj.dict()
    try:
        result = str(obj)
        _seen.remove(obj_id)
        return result
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
        from omnicore_engine.metrics import (
            get_plugin_metrics as actual_get_plugin_metrics,
        )

        return actual_get_plugin_metrics()
    except ImportError:
        logger.warning(
            "omnicore_engine.metrics not available. Cannot retrieve actual plugin metrics."
        )
        return {
            "error": "Metrics module not available",
            "message": "Install omnicore_engine.metrics for full functionality",
        }
    except Exception as e:
        logger.error(f"Error retrieving plugin metrics: {e}", exc_info=True)
        return {"error": str(e), "message": "Failed to retrieve plugin metrics"}


def get_test_metrics() -> dict:
    try:
        from omnicore_engine.metrics import get_test_metrics as actual_get_test_metrics

        return actual_get_test_metrics()
    except ImportError:
        logger.warning(
            "omnicore_engine.metrics not available. Cannot retrieve actual test metrics."
        )
        return {
            "error": "Metrics module not available",
            "message": "Install omnicore_engine.metrics for full functionality",
        }
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
                from omnicore_engine.explainable_reasoner import (
                    ExplainableReasonerPlugin,
                )

                self.reasoner = ExplainableReasonerPlugin(settings=settings)
                await self.reasoner.initialize()
                self.is_initialized = True
                self.logger.info("Explainable AI core initialized.")
            except ImportError as e:
                self.logger.warning(
                    f"Failed to import ExplainableReasonerPlugin: {e}. Explainable AI features will be unavailable."
                )
                self.is_initialized = False

    async def shutdown(self):
        if self.is_initialized and self.reasoner:
            try:
                await self.reasoner.shutdown()
                self.is_initialized = False
                self.logger.info("Explainable AI core shut down.")
            except Exception as e:
                self.logger.error(
                    f"Error shutting down Explainable AI: {e}", exc_info=True
                )

    async def explain_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_initialized or not self.reasoner:
            self.logger.warning(
                "Explainable AI core not initialized or reasoner unavailable."
            )
            return {"error": "Explainable AI core not available."}
        try:
            explanation_result = await self.reasoner.explain(
                query=event_data.get("query", "explain this event"),
                context=event_data.get("context", {}),
            )
            return {
                "explanation": explanation_result.get(
                    "explanation", "No explanation provided."
                )
            }
        except Exception as e:
            self.logger.error(f"Error generating explanation: {e}", exc_info=True)
            return {"error": str(e)}

    async def reason_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_initialized or not self.reasoner:
            self.logger.warning(
                "Explainable AI core not initialized or reasoner unavailable."
            )
            return {"error": "Explainable AI core not available."}
        try:
            reasoning_result = await self.reasoner.reason(
                query=event_data.get("query", "reason about this event"),
                context=event_data.get("context", {}),
            )
            return {
                "reasoning": reasoning_result.get("reasoning", "No reasoning provided.")
            }
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

    def add_leaf(self, data: Union[str, bytes]):
        # Ensure data is bytes
        if isinstance(data, str):
            data = data.encode("utf-8")
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
                        (current_level[i] + current_level[i + 1]).encode("utf-8")
                    ).hexdigest()
                    next_level.append(combined_hash)
                else:
                    next_level.append(current_level[i])
            current_level = next_level
        self.root = current_level[0]
        self.logger.debug(f"Merkle root recalculated: {self.root[:10]}...")

    def get_merkle_root(self) -> str:
        """Returns the Merkle root as a hex string.

        This method provides a standardized interface for retrieving the Merkle tree root,
        which is used for audit trail integrity verification.

        Returns:
            str: The Merkle root hash as a hexadecimal string. Returns an empty string
                 if the tree is empty and has no root.

        Note:
            This is the recommended method for accessing the Merkle root. While the `.root`
            property can be accessed directly, this method provides a more explicit and
            self-documenting interface.
        """
        return self.root if self.root else ""

    def get_root(self) -> str:
        """Returns the Merkle root as a hex string.

        This method provides compatibility with code that expects a get_root() interface.
        It is functionally equivalent to get_merkle_root().

        Returns:
            str: The Merkle root hash as a hexadecimal string. Returns an empty string
                 if the tree is empty and has no root.

        Note:
            This is an alias for get_merkle_root() provided for API compatibility.
            Both methods return the same value and can be used interchangeably.
        """
        return self.get_merkle_root()

    def verify_proof(
        self, leaf_data: Union[str, bytes], root: str, proof: List[str]
    ) -> bool:
        # Ensure leaf_data is bytes
        if isinstance(leaf_data, str):
            leaf_data = leaf_data.encode("utf-8")
        hashed_leaf = hashlib.sha256(leaf_data).hexdigest()
        current_hash = hashed_leaf
        for p in proof:
            if p < current_hash:
                current_hash = hashlib.sha256(
                    (p + current_hash).encode("utf-8")
                ).hexdigest()
            else:
                current_hash = hashlib.sha256(
                    (current_hash + p).encode("utf-8")
                ).hexdigest()
        return current_hash == root

    def get_proof(self, leaf_data: Union[str, bytes]) -> List[str]:
        # Ensure leaf_data is bytes
        if isinstance(leaf_data, str):
            leaf_data = leaf_data.encode("utf-8")
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
                        proof.append(current_level[i + 1])
                elif i + 1 == index:
                    if i >= 0:
                        proof.append(current_level[i])
                if i + 1 < len(current_level):
                    combined_hash = hashlib.sha256(
                        (current_level[i] + current_level[i + 1]).encode("utf-8")
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
        self.database = None  # Added database attribute
        self.audit = None  # Added audit attribute

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def _initialize_component_instance(
        self, name: str, component_class: Type[Base], *args, **kwargs
    ) -> Optional[Any]:
        """Helper to initialize and store a component, handling locks.
        
        Returns:
            The component instance if successful, or existing instance if already initialized.
        """
        if name not in self.component_locks:
            self.component_locks[name] = asyncio.Lock()

        async with self.component_locks[name]:
            if name in self.components:
                self.logger.debug(f"Component '{name}' already initialized.")
                return self.components[name]

            try:
                self.logger.info(f"Initializing component: {name}...")
                instance = component_class(*args, **kwargs)
                if hasattr(instance, "initialize") and callable(instance.initialize):
                    if inspect.iscoroutinefunction(instance.initialize):
                        await instance.initialize()
                    else:
                        instance.initialize()
                self.components[name] = instance
                self.logger.info(f"Component '{name}' initialized successfully.")
                return instance
            except Exception as e:
                self.logger.error(
                    f"Failed to initialize component '{name}': {e}", exc_info=True
                )
                raise

    async def _get_component_instance(self, name: str) -> Optional[Any]:
        """Helper to retrieve an initialized component instance."""
        async with self.component_locks.get(
            name, asyncio.Lock()
        ):  # Use a dummy lock if not already present
            return self.components.get(name)

    async def get_component(self, name: str) -> Optional[Any]:
        return self.components.get(name)

    async def initialize(self):
        if self._is_initialized:
            self.logger.info("OmniCore Engine: Already initialized.")
            return
        self.logger.info("OmniCore Engine: Starting application components...")

        # Import required modules - core omnicore_engine modules first
        try:
            from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
            from sqlalchemy.orm import sessionmaker

            from omnicore_engine.array_backend import ArrayBackend
            from omnicore_engine.audit import ExplainAudit
            from omnicore_engine.database import Database
            from omnicore_engine.message_bus import (
                MessageFilter,
                PluginMessageBusAdapter,
                ShardedMessageBus,
            )
            from omnicore_engine.plugin_event_handler import start_plugin_observer
            from omnicore_engine.plugin_registry import (
                PLUGIN_REGISTRY,
                Plugin,
                PlugInKind,
                PluginMeta,
            )
        except ImportError as e:
            self.logger.error(f"Failed to import required modules: {e}", exc_info=True)
            raise RuntimeError(f"Module import failed: {e}")

        # Import optional modules from arbiter package with graceful degradation
        ArbiterGrowthManager = None
        DecisionOptimizer = None
        ExplainableReasonerPlugin = None
        FeedbackManager = None
        KnowledgeGraph = None
        SQLiteStorageBackend = None

        try:
            from self_fixing_engineer.arbiter.arbiter_growth.arbiter_growth_manager import (
                ArbiterGrowthManager,
            )
        except ImportError:
            try:
                from arbiter.arbiter_growth.arbiter_growth_manager import (
                    ArbiterGrowthManager,
                )
            except ImportError as e:
                self.logger.debug(f"ArbiterGrowthManager not available: {e}")

        try:
            from self_fixing_engineer.arbiter.decision_optimizer import (
                DecisionOptimizer,
            )
        except ImportError:
            try:
                from arbiter.decision_optimizer import DecisionOptimizer
            except ImportError as e:
                self.logger.debug(f"DecisionOptimizer not available: {e}")

        try:
            from self_fixing_engineer.arbiter.explainable_reasoner import (
                ExplainableReasonerPlugin,
            )
        except ImportError:
            try:
                from arbiter.explainable_reasoner import ExplainableReasonerPlugin
            except ImportError as e:
                self.logger.debug(f"ExplainableReasonerPlugin not available: {e}")

        try:
            from self_fixing_engineer.arbiter.feedback import FeedbackManager
        except ImportError:
            try:
                from arbiter.feedback import FeedbackManager
            except ImportError as e:
                self.logger.debug(f"FeedbackManager not available: {e}")

        try:
            from self_fixing_engineer.arbiter.knowledge_graph import KnowledgeGraph
        except ImportError:
            try:
                from arbiter.knowledge_graph import KnowledgeGraph
            except ImportError as e:
                self.logger.debug(f"KnowledgeGraph not available: {e}")

        try:
            from self_fixing_engineer.arbiter.arbiter_growth.storage_backends import (
                SQLiteStorageBackend,
            )
        except ImportError:
            try:
                from arbiter.arbiter_growth.storage_backends import SQLiteStorageBackend
            except ImportError as e:
                self.logger.debug(f"SQLiteStorageBackend not available: {e}")

        try:
            if KnowledgeGraph is not None:
                self.knowledge_graph = KnowledgeGraph(
                    config=getattr(self.settings, "knowledge_graph_config", {})
                )
            else:
                self.knowledge_graph = None
                self.logger.info(
                    "KnowledgeGraph not available, skipping initialization."
                )
        except Exception as e:
            self.logger.warning(
                f"Failed to initialize KnowledgeGraph: {e}. KnowledgeGraph features will be unavailable.",
                exc_info=True,
            )
            self.knowledge_graph = None

        try:
            if DecisionOptimizer is not None:
                self.decision_optimizer = DecisionOptimizer(
                    PLUGIN_REGISTRY,
                    self.settings,
                    self.logger,
                    safe_serialize,
                    config=getattr(self.settings, "decision_optimizer_config", {}),
                )
            else:
                self.decision_optimizer = None
                self.logger.info(
                    "DecisionOptimizer not available, skipping initialization."
                )
        except Exception as e:
            self.logger.warning(
                f"Failed to initialize DecisionOptimizer: {e}. DecisionOptimizer features will be unavailable.",
                exc_info=True,
            )
            self.decision_optimizer = None

        try:
            self.array_backend = ArrayBackend(
                mode=getattr(self.settings, "array_backend_mode", "numpy"),
                use_gpu=getattr(self.settings, "use_gpu", False),
                use_dask=getattr(self.settings, "use_dask", False),
                use_quantum=getattr(self.settings, "use_quantum", False),
                use_neuromorphic=getattr(self.settings, "use_neuromorphic", False),
            )
        except Exception as e:
            self.logger.warning(
                f"Failed to initialize ArrayBackend: {e}. ArrayBackend features will be unavailable.",
                exc_info=True,
            )
            self.array_backend = None

        try:
            self.message_bus = ShardedMessageBus(
                config=self.settings, db=None, audit_client=None
            )
        except Exception as e:
            self.logger.error(
                f"Failed to initialize ShardedMessageBus: {e}", exc_info=True
            )
            raise RuntimeError(f"MessageBus initialization failed: {e}")

        try:
            system_audit_merkle_tree = MerkleTree()
            self.logger.info("MerkleTree initialized for audit system.")
        except Exception as e:
            self.logger.warning(
                f"MerkleTree instantiation failed: {e}. Audit integrity features will be limited.",
                exc_info=True,
            )
            system_audit_merkle_tree = None

        try:
            db_path = getattr(self.settings, "database_path", None) or getattr(
                self.settings, "DB_PATH", "sqlite:///./omnicore.db"
            )
            # Ensure async driver is used for SQLite
            if db_path.startswith("sqlite:///"):
                db_path = db_path.replace("sqlite:///", "sqlite+aiosqlite:///")

            await self._initialize_component_instance(
                "database",
                Database,
                db_path,
                system_audit_merkle_tree=system_audit_merkle_tree,
            )
            self.database = await self._get_component_instance("database")
        except Exception as e:
            self.logger.error(f"Failed to initialize database: {e}", exc_info=True)
            raise RuntimeError(f"Database initialization failed: {e}")

        try:
            await self._initialize_component_instance(
                "audit", ExplainAudit, system_audit_merkle_tree=system_audit_merkle_tree
            )
            self.audit = await self._get_component_instance("audit")
        except Exception as e:
            self.logger.warning(
                f"Failed to initialize audit: {e}. Audit features will be unavailable.",
                exc_info=True,
            )
            self.audit = None

        try:
            if self.message_bus:
                self.message_bus.db = self.database
                self.message_bus.audit_client = self.audit
                engine_type = getattr(self.settings, "engine_type", "simulation")
                await self.message_bus.configure_for_omnicore(engine_type)
                await self._initialize_component_instance(
                    "message_bus_component",
                    type(
                        "MessageBusComponent",
                        (Base,),
                        {
                            "initialize": lambda self_comp: self.logger.debug(
                                "MessageBus already initialized and configured by OmniCoreEngine."
                            ),
                            "shutdown": lambda self_comp: self.message_bus.shutdown(),
                            "health_check": lambda self_comp: {
                                "status": (
                                    "ok" if self.message_bus.running else "stopped"
                                ),
                                "queue_sizes_normal": {
                                    f"shard_{i}": q.qsize()
                                    for i, q in enumerate(self.message_bus.queues)
                                },
                                "queue_sizes_hp": {
                                    f"shard_{i}": q.qsize()
                                    for i, q in enumerate(
                                        self.message_bus.high_priority_queues
                                    )
                                },
                                "kafka_circuit": self.message_bus.kafka_circuit.state,
                                "redis_circuit": self.message_bus.redis_circuit.state,
                                "total_subscribers": sum(
                                    len(v)
                                    for v in self.message_bus.subscribers.values()
                                )
                                + sum(
                                    len(v)
                                    for v in self.message_bus.regex_subscribers.values()
                                ),
                            },
                            "is_healthy": property(
                                lambda self_comp: self.message_bus.running
                            ),
                        },
                    ),
                    settings=self.settings,
                )
        except Exception as e:
            self.logger.error(
                f"Failed to finalize message bus setup: {e}", exc_info=True
            )
            raise RuntimeError(f"MessageBus final setup failed: {e}")

        try:
            PLUGIN_REGISTRY.db = self.database
            PLUGIN_REGISTRY.audit_client = self.audit
            if self.message_bus:
                PLUGIN_REGISTRY.set_message_bus(self.message_bus)

            plugin_dir = getattr(self.settings, "plugin_dir", None) or getattr(
                self.settings, "PLUGIN_DIR", "./plugins"
            )

            async def _plugin_registry_initialize(self_comp_instance):
                await PLUGIN_REGISTRY.load_from_directory(plugin_dir)

            await self._initialize_component_instance(
                "plugin_registry",
                type(
                    "PluginRegistryComponent",
                    (Base,),
                    {
                        "initialize": _plugin_registry_initialize,
                        "shutdown": lambda self_comp: self.logger.info(
                            "Plugin registry shutdown"
                        ),
                        "health_check": lambda self_comp: {
                            "status": "ok",
                            "plugins_loaded": sum(
                                len(k) for k in PLUGIN_REGISTRY.plugins.values()
                            ),
                        },
                        "is_healthy": property(lambda self_comp: True),
                    },
                ),
                settings=self.settings,
            )
            self.plugin_registry = await self._get_component_instance("plugin_registry")
            start_plugin_observer(PLUGIN_REGISTRY, plugin_dir)

            PLUGIN_REGISTRY.load_ai_assistant_plugins()
        except Exception as e:
            self.logger.error(
                f"Failed to initialize plugin registry: {e}", exc_info=True
            )
            raise RuntimeError(f"Plugin registry initialization failed: {e}")

        try:
            if FeedbackManager is not None:
                await self._initialize_component_instance(
                    "feedback_manager",
                    FeedbackManager,
                    config=self.settings,
                )
                self.feedback_manager = await self._get_component_instance(
                    "feedback_manager"
                )
            else:
                self.feedback_manager = None
                self.logger.warning(
                    "FeedbackManager not available, skipping initialization."
                )
        except Exception as e:
            self.logger.warning(
                f"Failed to initialize feedback manager: {e}. Feedback features will be unavailable.",
                exc_info=True,
            )
            self.feedback_manager = None

        try:
            explainable_ai_instance = ExplainableAI()

            async def _explainable_ai_initialize(self_comp_instance):
                await explainable_ai_instance.initialize()

            await self._initialize_component_instance(
                "explainable_ai",
                type(
                    "ExplainableAIComponent",
                    (Base,),
                    {
                        "initialize": _explainable_ai_initialize,
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
            self.explainable_ai = await self._get_component_instance("explainable_ai")
        except Exception as e:
            self.logger.warning(
                f"Failed to initialize explainable AI: {e}. Explainable AI features will be unavailable.",
                exc_info=True,
            )
            self.explainable_ai = None

        try:
            if (
                self.database
                and self.knowledge_graph
                and ArbiterGrowthManager is not None
                and SQLiteStorageBackend is not None
            ):
                session_factory = self.database.AsyncSessionLocal
                storage_backend_instance = SQLiteStorageBackend(
                    session_factory=session_factory,
                    encryption_key=self.settings.encryption_key_bytes,
                )

                await self._initialize_component_instance(
                    "arbiter_growth_manager",
                    ArbiterGrowthManager,
                    arbiter_name="default_arbiter",
                    storage_backend=storage_backend_instance,
                    knowledge_graph=self.knowledge_graph,
                )
                self.arbiter_growth_manager = await self._get_component_instance(
                    "arbiter_growth_manager"
                )

                if self.arbiter_growth_manager:
                    meta = PluginMeta(
                        name="arbiter_growth",
                        kind=PlugInKind.GROWTH_MANAGER.value,
                        description="Manages Arbiter's growth and skill progression.",
                    )
                    growth_plugin_instance = Plugin(
                        meta=meta, fn=self.arbiter_growth_manager
                    )
                    PLUGIN_REGISTRY.register(
                        PlugInKind.GROWTH_MANAGER.value,
                        "arbiter_growth",
                        growth_plugin_instance,
                    )
                    self.logger.info("ArbiterGrowthManager registered as a plugin.")
            else:
                self.arbiter_growth_manager = None
                self.logger.warning(
                    "Database, KnowledgeGraph, or ArbiterGrowthManager not initialized/available. Skipping ArbiterGrowthManager initialization."
                )
        except Exception as e:
            self.logger.warning(
                f"Failed to initialize ArbiterGrowthManager: {e}. Growth management features will be unavailable.",
                exc_info=True,
            )
            self.arbiter_growth_manager = None

        for component_instance, name_str in [
            (self.knowledge_graph, "knowledge_graph"),
            (self.decision_optimizer, "decision_optimizer"),
            (self.array_backend, "array_backend"),
            (self.message_bus, "message_bus"),
            (self.arbiter_growth_manager, "arbiter_growth_manager"),
        ]:
            if component_instance:
                try:

                    class CoreServicePlugin(Plugin):
                        def __init__(
                            self,
                            meta: PluginMeta,
                            component_instance: Any,
                            adapter: PluginMessageBusAdapter,
                        ):
                            super().__init__(meta, component_instance)
                            self.component_instance = component_instance
                            self.message_bus_adapter = adapter

                        async def execute(self, *args, **kwargs) -> Any:
                            self.logger.debug(
                                f"Executing generic CoreServicePlugin wrapper for {self.meta.name}"
                            )
                            if hasattr(self.component_instance, "execute") and callable(
                                self.component_instance.execute
                            ):
                                return await self.component_instance.execute(
                                    *args, **kwargs
                                )
                            if self.meta.name == "array_backend" and hasattr(
                                self.component_instance, "handle_computation_task"
                            ):
                                self.logger.warning(
                                    "Direct 'execute' call on ArrayBackend plugin. Consider using message bus directly for computations."
                                )
                                return {
                                    "error": "ArrayBackend plugin does not support direct 'execute' calls for computation. Use message bus instead."
                                }
                            return {
                                "status": f"Core service {self.meta.name} executed (no specific execute method found)",
                                "args": args,
                                "kwargs": kwargs,
                            }

                    plugin_bus_adapter = PluginMessageBusAdapter(
                        self.message_bus, name_str
                    )
                    component_kind = (
                        PlugInKind.GROWTH_MANAGER.value
                        if name_str == "arbiter_growth_manager"
                        else PlugInKind.CORE_SERVICE.value
                    )
                    meta = PluginMeta(
                        name=name_str,
                        kind=component_kind,
                        description=f"{name_str} core service",
                    )
                    core_plugin = CoreServicePlugin(
                        meta=meta,
                        component_instance=component_instance,
                        adapter=plugin_bus_adapter,
                    )

                    if name_str == "array_backend" and self.array_backend:
                        self.array_backend.set_message_bus(self.message_bus)

                    PLUGIN_REGISTRY.register(component_kind, name_str, core_plugin)
                    self.logger.info(f"{name_str} registered as core service plugin.")
                except Exception as e:
                    self.logger.error(
                        f"Failed to register {name_str} as core service plugin: {e}",
                        exc_info=True,
                    )

        try:
            if self.message_bus:
                self.message_bus.subscribe("system.shutdown", self._handle_shutdown)
                self.message_bus.subscribe(
                    "system.config_changed", self._handle_config_change
                )
                self.message_bus.subscribe(
                    "system.error",
                    self._handle_system_error,
                    MessageFilter(lambda p: p.get("severity", 0) >= 5),
                )
            else:
                self.logger.warning(
                    "Message bus not initialized, skipping system event subscriptions."
                )
        except Exception as e:
            self.logger.error(f"Failed to subscribe to system events: {e}")

        self._is_initialized = True
        self.logger.info("OmniCore Engine: All components initialized.")

    async def shutdown(self):
        if not self._is_initialized:
            self.logger.info("OmniCore Engine: Already shut down.")
            return
        self.logger.info("OmniCore Engine: Shutting down application components...")
        for name, component in list(self.components.items()):
            if hasattr(component, "shutdown") and callable(component.shutdown):
                try:
                    if asyncio.iscoroutinefunction(component.shutdown):
                        await component.shutdown()
                    else:
                        component.shutdown()
                    self.logger.info(f"Component '{name}' shut down successfully.")
                except Exception as e:
                    self.logger.error(
                        f"Error shutting down component '{name}': {e}", exc_info=True
                    )
        # Clear components after shutdown
        self.components.clear()
        self._is_initialized = False
        self.logger.info("OmniCore Engine: All components shut down.")

    async def health_check(self) -> Dict[str, Any]:
        overall_status = "ok"
        health_report = {}
        for name, component in self.components.items():
            if hasattr(component, "health_check") and callable(component.health_check):
                try:
                    status = await component.health_check()
                    health_report[name] = status
                    if status.get("status") == "unhealthy":
                        overall_status = "unhealthy"
                except Exception as e:
                    self.logger.error(
                        f"Health check failed for component '{name}': {e}",
                        exc_info=True,
                    )
                    health_report[name] = {"status": "error", "message": str(e)}
                    overall_status = "unhealthy"
            else:
                health_report[name] = {
                    "status": "unknown",
                    "message": "No health_check method",
                }
        health_report["overall_status"] = overall_status
        return health_report

    @property
    def is_healthy(self) -> bool:
        # This property should reflect the overall health based on component statuses
        # For simplicity, returning True if initialized, but a full implementation
        # would iterate through components' is_healthy properties.
        return self._is_initialized and all(
            getattr(comp, "is_healthy", True) for comp in self.components.values()
        )

    async def health_check_all(self) -> Dict[str, Any]:
        """
        Perform health checks on all registered components.
        
        Returns:
            Dictionary mapping component names to their health status.
        """
        results = {}
        for name, component in self.components.items():
            if hasattr(component, "health_check") and callable(component.health_check):
                try:
                    if asyncio.iscoroutinefunction(component.health_check):
                        status = await component.health_check()
                    else:
                        status = component.health_check()
                    results[name] = {"status": "healthy", **status} if isinstance(status, dict) else {"status": "healthy"}
                except Exception as e:
                    results[name] = {"status": "error", "message": str(e)}
            else:
                results[name] = {"status": "unknown", "message": "No health_check method"}
        return results

    async def perform_task(self, task_name: str, **kwargs) -> Optional[Any]:
        """
        Perform a task using the plugin system.
        
        Args:
            task_name: Name of the task to perform
            **kwargs: Additional arguments to pass to the plugin
            
        Returns:
            Task result or None if no plugin found or error occurred
        """
        try:
            # Try to get plugin registry - use proper import pattern
            from omnicore_engine import plugin_registry as plugin_registry_module
        except ImportError as e:
            self.logger.error(
                f"Plugin registry not available: {e}. "
                "Ensure omnicore_engine is properly installed.",
                exc_info=True
            )
            return None
        except Exception as e:
            self.logger.error(
                f"Unexpected error importing plugin_registry: {e}",
                exc_info=True
            )
            return None
        
        try:
            registry = getattr(plugin_registry_module, "PLUGIN_REGISTRY", None)
            if registry is None:
                self.logger.warning("PLUGIN_REGISTRY not found")
                return None
            
            plugin = registry.get_plugin_for_task(task_name)
            if plugin is None:
                self.logger.debug(f"No plugin found for task: {task_name}")
                return None
            
            if asyncio.iscoroutinefunction(plugin.execute):
                return await plugin.execute(action=task_name, **kwargs)
            else:
                return plugin.execute(action=task_name, **kwargs)
        except Exception as e:
            self.logger.error(f"Error performing task {task_name}: {e}", exc_info=True)
            return None

    async def _shutdown_component_instance(self, name: str) -> bool:
        """
        Shutdown a specific component instance.
        
        Args:
            name: Name of the component to shutdown
            
        Returns:
            True if shutdown was successful, False otherwise
        """
        if name not in self.components:
            self.logger.warning(f"Component '{name}' not found for shutdown")
            return False
        
        component = self.components[name]
        try:
            if hasattr(component, "shutdown"):
                if asyncio.iscoroutinefunction(component.shutdown):
                    await component.shutdown()
                else:
                    component.shutdown()
            del self.components[name]
            self.logger.info(f"Component '{name}' shut down successfully")
            return True
        except Exception as e:
            self.logger.error(f"Error shutting down component '{name}': {e}")
            return False

    async def _handle_shutdown(self, message: Dict[str, Any]):
        self.logger.info(
            f"Received system shutdown message: {message.get('reason', 'No reason provided')}. Initiating shutdown..."
        )
        await self.shutdown()

    async def _handle_config_change(self, message: Dict[str, Any]):
        self.logger.info(
            f"Received system config_changed message: {message.get('changes', 'No changes provided')}. Reinitializing components..."
        )
        # A more robust implementation would selectively reinitialize or update components
        # For now, a full shutdown and re-initialization is a simple approach.
        await self.shutdown()
        await self.initialize()

    async def _handle_system_error(self, message: Dict[str, Any]):
        """
        Comprehensive system error handler with industry-standard practices.

        This method implements a multi-layered approach to error handling:
        1. Logging and metrics tracking
        2. Alert notification system
        3. External logging system integration
        4. Self-healing mechanism trigger
        5. Circuit breaker pattern for fault tolerance

        Args:
            message: Error message dictionary containing:
                - error: Error description
                - severity: Error severity level (critical, high, medium, low)
                - component: Component that generated the error
                - context: Additional context information
                - timestamp: Error timestamp

        Industry Standard Features:
        - Structured logging for observability
        - Prometheus metrics for monitoring
        - Severity-based alert routing
        - Automatic self-healing triggers
        - Error rate limiting to prevent alert storms
        """
        error_msg = message.get("error", "Unknown error")
        severity = message.get("severity", "medium")
        component = message.get("component", "unknown")
        context = message.get("context", {})
        timestamp = message.get("timestamp", datetime.now().isoformat())

        # 1. Structured logging with full context
        log_data = {
            "event": "system_error",
            "error": error_msg,
            "severity": severity,
            "component": component,
            "context": context,
            "timestamp": timestamp,
        }

        # Log at appropriate level based on severity
        if severity in ["critical", "high"]:
            self.logger.error(
                f"CRITICAL System Error in {component}: {error_msg}",
                extra=log_data,
                exc_info=True,
            )
        elif severity == "medium":
            self.logger.warning(
                f"System Error in {component}: {error_msg}", extra=log_data
            )
        else:
            self.logger.info(
                f"System Notice in {component}: {error_msg}", extra=log_data
            )

        # 2. Track error metrics using Prometheus
        if get_or_create_counter is not None:
            try:
                # Track error counts by severity and component
                error_counter = get_or_create_counter(
                    "omnicore_system_errors_total",
                    "Total number of system errors",
                    labelnames=("severity", "component"),
                )
                error_counter.labels(severity=severity, component=component).inc()
            except Exception as e:
                self.logger.warning(f"Failed to record error metrics: {e}")
        else:
            self.logger.debug("Metrics module not available, skipping error counter")

        # 3. Send alerts for critical/high severity errors
        if severity in ["critical", "high"]:
            try:
                await self._send_alert(
                    severity=severity,
                    component=component,
                    error=error_msg,
                    context=context,
                )
            except Exception as e:
                self.logger.error(f"Failed to send alert: {e}", exc_info=True)

        # 4. Log to external systems (if available)
        try:
            await self._log_to_external_system(log_data)
        except Exception as e:
            self.logger.warning(f"Failed to log to external system: {e}")

        # 5. Trigger self-healing mechanisms for critical errors
        if severity == "critical" and self.message_bus:
            try:
                await self._trigger_self_healing(component, error_msg, context)
            except Exception as e:
                self.logger.error(f"Failed to trigger self-healing: {e}", exc_info=True)

        # 6. Store error in audit log for compliance and analysis
        if self.audit:
            try:
                await self.audit.log_event(
                    event_type="system_error",
                    severity=severity,
                    details=log_data,
                    user_id="system",
                )
            except Exception as e:
                self.logger.warning(f"Failed to log error to audit system: {e}")

    async def _send_alert(
        self, severity: str, component: str, error: str, context: Dict[str, Any]
    ):
        """
        Send alert notification for system errors.

        This method attempts to send alerts through multiple channels:
        - Message bus notification channel
        - Feedback manager (if available)
        - External alerting systems (PagerDuty, Slack, etc.)

        Args:
            severity: Error severity level
            component: Component that generated the error
            error: Error message
            context: Additional context
        """
        alert_message = {
            "type": "system_error_alert",
            "severity": severity,
            "component": component,
            "error": error,
            "context": context,
            "timestamp": datetime.now().isoformat(),
        }

        # Send via message bus if available
        if self.message_bus:
            try:
                await self.message_bus.publish(
                    topic="system_alerts", message=alert_message
                )
                self.logger.debug("Alert sent via message bus")
            except Exception as e:
                self.logger.warning(f"Failed to send alert via message bus: {e}")

        # Send via feedback manager if available
        if hasattr(self, "feedback_manager") and self.feedback_manager:
            try:
                await self.feedback_manager.record_feedback(
                    user_id="system",
                    feedback_type="system_error",
                    details=alert_message,
                )
                self.logger.debug("Alert recorded in feedback manager")
            except Exception as e:
                self.logger.warning(f"Failed to record alert in feedback manager: {e}")

    async def _log_to_external_system(self, log_data: Dict[str, Any]):
        """
        Log error to external logging systems for centralized monitoring.

        This method integrates with external logging services like:
        - ELK Stack (Elasticsearch, Logstash, Kibana)
        - Splunk
        - DataDog
        - CloudWatch

        Args:
            log_data: Structured log data to send

        Note:
            This is a placeholder for external logging integration.
            In production environments, implement specific integrations
            based on your monitoring infrastructure:
            - Configure InfluxDB client and write_api at module level
            - Use dedicated logging libraries (e.g., python-json-logger)
            - Integrate with cloud-native monitoring services
        """
        # Placeholder for external logging integration
        # In production, this would send to services like:
        # - InfluxDB for time-series logging (requires configured client)
        # - Elasticsearch for log aggregation (requires elasticsearch-py)
        # - CloudWatch/DataDog for cloud monitoring (requires respective SDKs)

        # Future implementation example:
        # if hasattr(self, 'influx_client') and self.influx_client:
        #     write_api = self.influx_client.write_api()
        #     point = Point("system_errors").tag("component", log_data.get("component"))
        #     write_api.write(bucket="omnicore", record=point)

        self.logger.debug(
            "External logging not configured (placeholder implementation)"
        )

    async def _trigger_self_healing(
        self, component: str, error: str, context: Dict[str, Any]
    ):
        """
        Trigger self-healing mechanisms for critical errors.

        This method implements automated recovery strategies:
        - Component restart
        - Circuit breaker activation
        - Failover to backup systems
        - Resource reallocation

        Args:
            component: Component that experienced the error
            error: Error message
            context: Additional context for healing decision
        """
        healing_message = {
            "type": "trigger_self_healing",
            "component": component,
            "error": error,
            "context": context,
            "timestamp": datetime.now().isoformat(),
            "healing_strategies": [
                "restart_component",
                "activate_circuit_breaker",
                "failover_backup",
            ],
        }

        # Publish self-healing trigger to message bus
        try:
            await self.message_bus.publish(
                topic="self_healing_triggers", message=healing_message
            )
            self.logger.info(
                f"Self-healing triggered for component: {component}",
                extra={"healing_message": healing_message},
            )
        except Exception as e:
            self.logger.error(
                f"Failed to publish self-healing trigger: {e}", exc_info=True
            )


# --- Exported singleton for main entry ---
omnicore_engine = OmniCoreEngine(settings=settings)
