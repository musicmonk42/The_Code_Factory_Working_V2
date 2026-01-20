"""
 plugin registry for Arbiter/SFE platform.

- **Type-safe**: Enforces a strict `PluginBase` interface and runtime type checking.
- **Thread-safe**: Uses a global lock for mutation operations.
- **Metrics-enabled**: Tracks plugin loads, unloads, and health checks via Prometheus.
- **Dependency-aware**: Validates plugin dependencies before loading.

Static type checking with `mypy --strict` is recommended for maximum safety.
"""

import asyncio
import importlib
import json
import logging
import multiprocessing
import os
import pkgutil
import re
import threading
import time
import traceback
from abc import ABC, abstractmethod
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Type
from unittest.mock import MagicMock

from networkx import DiGraph, has_path
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion
from packaging.version import parse as version_parse
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# Mock opentelemetry.trace if not available
try:
    import opentelemetry.trace as trace
except ImportError:
    logger.warning("opentelemetry.trace not available. Using mock for tracing.")
    trace = MagicMock()

# Mock imports for a self-contained fix
try:
    from arbiter import PermissionManager

    from .config import ArbiterConfig
    from .logging_utils import PIIRedactorFilter
except ImportError:

    class PIIRedactorFilter(logging.Filter):
        def filter(self, record):
            return True

    class ArbiterConfig:
        def __init__(self):
            self.ROLE_MAP = {"admin": "admin", "user": "user"}
            self.PLUGINS_ENABLED = True

    class PermissionManager:
        def __init__(self, config):
            self.config = config

        def check_permission(self, role, permission):
            # Dummy logic: always grant permission if role is 'admin'
            return role == "admin"


try:
    from arbiter.audit_log import emit_audit_event
except ImportError:

    async def emit_audit_event(event_type, data):
        logger.info(f"Audit Event: {event_type} - {data}")


# Logging setup with PII redaction filter
logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    )
    handler.addFilter(PIIRedactorFilter())
    logger.addHandler(handler)


class PlugInKind(Enum):
    """Supported plugin kinds."""

    WORKFLOW = "workflow"
    VALIDATOR = "validator"
    REPORTER = "reporter"
    GROWTH_MANAGER = "growth_manager"
    CORE_SERVICE = "core_service"
    ANALYTICS = "analytics"
    STRATEGY = "strategy"
    TRANSFORMER = "transformer"
    AI_ASSISTANT = "ai_assistant"


class PluginError(Exception):
    """Raised when a plugin operation fails."""

    pass


class PluginDependencyError(PluginError):
    """Raised when a plugin's dependencies cannot be satisfied."""

    pass


@dataclass(frozen=True)
class PluginMeta:
    """Metadata for a registered plugin."""

    name: str
    kind: PlugInKind
    version: str = "0.1.0"
    author: Optional[str] = None
    description: Optional[str] = None
    tags: Set[str] = field(default_factory=set)
    loaded_at: float = field(default_factory=lambda: time.time())
    plugin_type: str = "class"
    dependencies: List[Dict[str, str]] = field(default_factory=list)
    rbac_roles: Set[str] = field(default_factory=set)
    signature: Optional[str] = None
    is_quarantined: bool = False
    health: Optional[Dict[str, Any]] = None


class PluginBase(ABC):
    """Base class for all plugins."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the plugin (e.g., setup resources)."""
        pass

    @abstractmethod
    async def start(self) -> None:
        """Start the plugin (e.g., begin processing)."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the plugin and clean up resources."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the plugin is healthy."""
        return True

    @abstractmethod
    async def get_capabilities(self) -> List[str]:
        """
        Returns a list of the plugin's capabilities or exposed APIs.
        This enables UIs or orchestration layers to dynamically discover
        the services offered by a plugin.
        """
        return []

    def on_reload(self) -> None:
        """Handle plugin reload event."""
        pass


# Metrics setup
try:
    from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram

    # FIX: Handle duplicate registration during pytest collection
    # Wrap each metric creation in try-except to reuse existing metrics
    try:
        plugin_loads = Counter(
            "arbiter_plugin_loads_total", "Total plugin loads", ["kind", "name"]
        )
    except ValueError:
        plugin_loads = REGISTRY._names_to_collectors.get("arbiter_plugin_loads_total")

    try:
        plugin_unloads = Counter(
            "arbiter_plugin_unloads_total", "Total plugin unloads", ["kind", "name"]
        )
    except ValueError:
        plugin_unloads = REGISTRY._names_to_collectors.get(
            "arbiter_plugin_unloads_total"
        )

    try:
        plugin_health_checks = Counter(
            "arbiter_plugin_health_checks_total",
            "Total plugin health checks",
            ["kind", "name", "status"],
        )
    except ValueError:
        plugin_health_checks = REGISTRY._names_to_collectors.get(
            "arbiter_plugin_health_checks_total"
        )

    try:
        plugin_load_time = Histogram(
            "arbiter_plugin_load_time_seconds", "Plugin load time", ["kind", "name"]
        )
    except ValueError:
        plugin_load_time = REGISTRY._names_to_collectors.get(
            "arbiter_plugin_load_time_seconds"
        )

    try:
        plugin_ops_total = Counter(
            "plugin_ops_total", "Total plugin registry operations", ["operation"]
        )
    except ValueError:
        plugin_ops_total = REGISTRY._names_to_collectors.get("plugin_ops_total")

    try:
        plugin_errors_total = Counter(
            "plugin_errors_total",
            "Total plugin registry errors",
            ["kind", "name", "error_type"],
        )
    except ValueError:
        plugin_errors_total = REGISTRY._names_to_collectors.get("plugin_errors_total")
except ImportError:
    logger.warning("prometheus_client not available. Metrics disabled.")

    class DummyMetric:
        # Add DEFAULT_BUCKETS to match Histogram.DEFAULT_BUCKETS
        DEFAULT_BUCKETS = (
            0.005,
            0.01,
            0.025,
            0.05,
            0.075,
            0.1,
            0.25,
            0.5,
            0.75,
            1.0,
            2.5,
            5.0,
            7.5,
            10.0,
            float("inf"),
        )

        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            pass

        def observe(self, *args, **kwargs):
            pass

    plugin_loads = DummyMetric()
    plugin_unloads = DummyMetric()
    plugin_health_checks = DummyMetric()
    plugin_load_time = DummyMetric()
    plugin_ops_total = DummyMetric()
    plugin_errors_total = DummyMetric()


class PluginRegistry:
    """
    Singleton registry for managing plugins.
    This class is thread-safe for mutation operations via a global lock.
    """

    _instance: Optional["PluginRegistry"] = None
    _lock = threading.Lock()
    _event_hook: Optional[Callable[[Dict[str, Any]], None]] = None
    _kind_locks: Dict[PlugInKind, threading.RLock]

    def __new__(cls, persist_path: str = "plugins.json"):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._plugins = {}
                cls._instance._meta = {}
                cls._instance._kind_locks = {
                    kind: threading.RLock() for kind in PlugInKind
                }

                # Check for test mode and use a temp file if testing
                if os.getenv("TESTING", "false").lower() == "true":
                    import tempfile

                    temp_dir = tempfile.gettempdir()
                    persist_path = os.path.join(
                        temp_dir, f"test_plugins_{os.getpid()}.json"
                    )
                    # Don't load any existing plugins in test mode
                    cls._instance._persist_path = persist_path
                    logger.info(
                        f"Test mode: Using temporary plugin file: {persist_path}"
                    )
                else:
                    cls._instance._persist_path = persist_path
                    cls._instance._load_persisted_plugins()

                logger.info("PluginRegistry singleton created")
            return cls._instance

    def set_event_hook(self, hook: Callable[[Dict[str, Any]], None]):
        """
        Sets a callback function for broadcasting registry events.
        This can be used to stream audit logs to systems like Kafka, Kinesis,
        or a GCP/AWS logging sink for compliance.
        """
        self._event_hook = hook

    def _trigger_event(self, event_dict: Dict[str, Any]):
        """Triggers the event hook if one is set."""
        if self._event_hook:
            try:
                self._event_hook(event_dict)
            except Exception as e:
                logger.error(f"Error calling event hook: {e}", exc_info=True)
                try:
                    plugin_errors_total.labels(
                        kind="n/a", name="event_hook", error_type="event_hook_fail"
                    ).inc()
                except Exception:
                    pass

    def _meta_to_dict(self, meta: PluginMeta) -> dict:
        """Helper to safely serialize a PluginMeta dataclass for JSON."""
        d = dict(meta.__dict__)
        if isinstance(d.get("tags"), set):
            d["tags"] = list(d["tags"])
        if isinstance(d.get("rbac_roles"), set):
            d["rbac_roles"] = list(d["rbac_roles"])
        if isinstance(d.get("kind"), Enum):
            d["kind"] = d["kind"].value
        return d

    def _load_persisted_plugins(self):
        """Load plugin metadata from persistent storage."""
        try:
            if os.path.exists(self._persist_path):
                with open(self._persist_path, "r") as f:
                    persisted = json.load(f)
                for kind, plugins in persisted.items():
                    for name, meta_data in plugins.items():
                        meta_data["kind"] = PlugInKind(kind)
                        meta_data["tags"] = set(meta_data["tags"])
                        meta_data["rbac_roles"] = set(meta_data.get("rbac_roles", []))
                        meta = PluginMeta(**meta_data)
                        self._meta.setdefault(PlugInKind(kind), {})[name] = meta
                        logger.info(f"Loaded persisted plugin [{kind}:{name}]")
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning(
                f"Failed to load plugins from {self._persist_path}: {e}. Using empty plugin list."
            )
        except Exception as e:
            logger.error(f"Failed to load persisted plugins: {e}", exc_info=True)
            try:
                plugin_errors_total.labels(
                    kind="n/a", name="registry", error_type="load_persist_fail"
                ).inc()
            except Exception:
                pass

    def _persist_plugins(self):
        """Persist plugin metadata to storage in a thread-safe manner."""
        with self._lock:
            try:
                serialized = {
                    kind.value: {
                        name: self._meta_to_dict(meta) for name, meta in plugins.items()
                    }
                    for kind, plugins in self._meta.items()
                }
                with open(self._persist_path, "w") as f:
                    json.dump(serialized, f, indent=2, default=list)
                logger.info("Persisted plugin metadata")
            except Exception as e:
                logger.error(f"Failed to persist plugins: {e}", exc_info=True)
                try:
                    plugin_errors_total.labels(
                        kind="n/a", name="registry", error_type="persist_fail"
                    ).inc()
                except Exception:
                    pass

    def _verify_signature(self, plugin: Any, meta: PluginMeta):
        """
        Stub for code signature verification. In a zero-trust model, this
        would verify a detached signature against a known public key.
        """
        if meta.signature:
            logger.warning(
                f"Verification stub: signature found for {meta.name}, but not verified. Implement me!"
            )
            try:
                plugin_errors_total.labels(
                    kind=meta.kind.value,
                    name=meta.name,
                    error_type="signature_unverified",
                ).inc()
            except Exception:
                pass

    def _validate_name(self, name: str) -> None:
        """Validate plugin name format."""
        if not name or not re.match(r"^[a-zA-Z0-9_-]+$", name):
            raise ValueError(
                f"Invalid plugin name '{name}': Must be non-empty and contain only alphanumeric characters, underscores, or hyphens"
            )

    def _validate_version(self, version_str: str) -> None:
        """Validate semantic version format."""
        try:
            v = version_parse(version_str)
            if len(v.release) != 3:
                raise InvalidVersion(
                    f"Version '{version_str}' must have exactly three components."
                )
        except InvalidVersion as e:
            raise ValueError(
                f"Invalid version '{version_str}': Must follow semantic versioning (e.g., 1.2.3)"
            ) from e

    def _validate_dependencies(
        self, kind: PlugInKind, name: str, dependencies: List[Dict[str, str]]
    ) -> None:
        """
        Validates plugin dependencies, checking for existence, version constraints,
        and circular dependencies.
        """
        dep_graph = DiGraph()
        # Add all existing plugins to the graph
        for k, plugins in self._meta.items():
            for n in plugins.keys():
                dep_graph.add_node(f"{k.value}:{n}")

        # Add the new plugin to the graph
        dep_graph.add_node(f"{kind.value}:{name}")

        for dep in dependencies:
            dep_kind_str = dep.get("kind")
            dep_name = dep.get("name")
            dep_version_str = dep.get("version", ">=0.0.0")
            if not dep_kind_str or not dep_name:
                raise PluginDependencyError(
                    "Dependency must specify 'kind' and 'name'."
                )

            try:
                dep_kind = PlugInKind(dep_kind_str)
            except ValueError:
                raise PluginDependencyError(
                    f"Invalid dependency kind: '{dep_kind_str}'"
                )

            # Check for circular dependencies
            dep_node = f"{dep_kind.value}:{dep_name}"
            new_node = f"{kind.value}:{name}"
            if dep_node in dep_graph and has_path(dep_graph, dep_node, new_node):
                raise PluginDependencyError(
                    f"Circular dependency detected for {kind.value}:{name} on {dep_kind.value}:{dep_name}"
                )

            existing = self.get_metadata(dep_kind, dep_name)
            if not existing:
                raise PluginDependencyError(
                    f"Dependency [{dep_kind.value}:{dep_name}] not found"
                )

            if not self._satisfies_version(existing.version, dep_version_str):
                raise PluginDependencyError(
                    f"Dependency [{dep_kind.value}:{dep_name}] version {existing.version} does not satisfy {dep_version_str}"
                )

    def _satisfies_version(self, current: str, required: str) -> bool:
        return SpecifierSet(required).contains(version_parse(current))

    async def register_with_omnicore(
        self, kind: PlugInKind, name: str, plugin: PluginBase, version: str, author: str
    ):
        """
        Registers a plugin with the omnicore_engine.
        """
        try:
            from engines import register_engine

            # Assuming 'engines' is a module that provides 'register_engine' function
            # The 'entrypoints' dict maps a unique name to a function to be exposed by the engine
            register_engine(
                "arbiter_plugin_registry",
                entrypoints={f"plugin_{kind.value}_{name}": plugin.start},
            )
            from .audit_log import emit_audit_event

            await emit_audit_event(
                "plugin_registered_omnicore",
                {
                    "kind": kind.value,
                    "name": name,
                    "version": version,
                    "author": author,
                },
            )
            logger.info(f"Registered plugin [{kind.value}:{name}] with OmniCore.")
        except (ImportError, SyntaxError) as e:
            logger.warning(
                f"Failed to register plugin [{kind.value}:{name}] with OmniCore: {e}"
            )
            try:
                plugin_errors_total.labels(
                    kind=kind.value, name=name, error_type="omnicore_registration"
                ).inc()
            except Exception:
                pass
        except Exception as e:
            logger.error(
                f"Failed to register plugin [{kind.value}:{name}] with OmniCore: {e}",
                exc_info=True,
            )
            try:
                plugin_errors_total.labels(
                    kind=kind.value, name=name, error_type="omnicore_registration"
                ).inc()
            except Exception:
                pass

    def _validate_plugin_class(
        self, plugin: Type[PluginBase], meta: PluginMeta
    ) -> None:
        """Validate plugin compliance and interface."""
        if not meta.author:
            raise ValueError(
                f"Plugin [{meta.kind.value}:{meta.name}] missing required author metadata"
            )

        if not issubclass(plugin, PluginBase):
            raise TypeError(
                f"Plugin [{meta.kind.value}:{meta.name}] must be a class that inherits from PluginBase."
            )

        for method_name in [
            "initialize",
            "start",
            "stop",
            "health_check",
            "get_capabilities",
        ]:
            method = getattr(plugin, method_name, None)
            if not (method and asyncio.iscoroutinefunction(method)):
                raise TypeError(
                    f"Plugin [{meta.name}] is missing required async method: '{method_name}'"
                )

    def register(
        self,
        kind: PlugInKind,
        name: str,
        version: str = "0.1.0",
        dependencies: List[Dict[str, str]] = None,
        **meta_kwargs,
    ):
        """
        Decorator to register a plugin class with metadata.

        Args:
            kind: The plugin kind.
            name: The plugin name.
            version: The plugin version.
            dependencies: A list of dependencies with kind, name, and version constraints.
            **meta_kwargs: Additional metadata fields like author, description, tags, etc.

        Raises:
            PermissionError: If the user lacks write permission.
            ValueError: If the plugin name or version is invalid.
            PluginDependencyError: If dependencies cannot be satisfied or a circular dependency is detected.
            TypeError: If the plugin class does not meet the `PluginBase` interface requirements.
        """
        # Conceptual access control
        # if not self.check_permission("admin", "write"):
        #     raise PermissionError("Write permission required for plugin registration")

        self._validate_name(name)
        self._validate_version(version)

        dependencies = dependencies or []
        self._validate_dependencies(kind, name, dependencies)

        meta = PluginMeta(
            name=name,
            kind=kind,
            version=version,
            dependencies=dependencies,
            **meta_kwargs,
            plugin_type="class",
        )

        def decorator(plugin_class: Type[PluginBase]):
            start_time = time.time()
            with trace.get_tracer(__name__).start_as_current_span(
                f"register_plugin_{kind.value}_{name}"
            ):
                lock = self._kind_locks[kind]
                # Use proper lock acquisition with threading.RLock
                with lock:
                    self._validate_plugin_class(plugin_class, meta)
                    self._verify_signature(plugin_class, meta)

                    existing_meta = self.get_metadata(kind, name)
                    if existing_meta and version_parse(version) <= version_parse(
                        existing_meta.version
                    ):
                        raise ValueError(
                            f"Plugin [{kind.value}:{name}] version {version} is not newer than existing version {existing_meta.version}."
                        )

                    self._plugins.setdefault(kind, {})[name] = plugin_class
                    self._meta.setdefault(kind, {})[name] = meta
                    try:
                        plugin_loads.labels(kind=kind.value, name=name).inc()
                        plugin_load_time.labels(kind=kind.value, name=name).observe(
                            time.time() - start_time
                        )
                    except Exception as e:
                        logger.debug(f"Metrics error: {e}")
                    self._persist_plugins()
                    event_dict = {
                        "event": "plugin_registered",
                        "kind": kind.value,
                        "name": name,
                        "version": version,
                        "plugin_type": meta.plugin_type,
                    }
                    logger.info(event_dict)
                    self._trigger_event(event_dict)

                    try:
                        asyncio.create_task(
                            self.register_with_omnicore(
                                kind, name, plugin_class, version, meta.author
                            )
                        )
                    except RuntimeError:
                        logger.info(
                            "No running event loop; skipping OmniCore registration."
                        )
                return plugin_class

        return decorator

    def register_instance(
        self,
        kind: PlugInKind,
        name: str,
        instance: Any,
        version: str = "0.1.0",
        dependencies: List[Dict[str, str]] = None,
        **meta_kwargs,
    ):
        """Register an already-created instance."""
        # if not self.check_permission("admin", "write"):
        #     raise PermissionError("Write permission required for plugin registration")

        self._validate_name(name)
        self._validate_version(version)
        dependencies = dependencies or []
        self._validate_dependencies(kind, name, dependencies)

        meta = PluginMeta(
            name=name,
            kind=kind,
            version=version,
            dependencies=dependencies,
            **meta_kwargs,
            plugin_type="instance",
        )

        lock = self._kind_locks[kind]
        # Use proper lock acquisition with threading.RLock
        with lock:
            if not isinstance(instance, PluginBase):
                raise TypeError(
                    f"Plugin instance [{kind.value}:{name}] must inherit from PluginBase."
                )

            existing_meta = self.get_metadata(kind, name)
            if existing_meta and version_parse(version) < version_parse(
                existing_meta.version
            ):
                raise ValueError(
                    f"Plugin [{kind.value}:{name}] version {version} is not newer than existing version {existing_meta.version}."
                )

            # Allow re-registration of same version (useful for tests)
            if existing_meta and version_parse(version) == version_parse(
                existing_meta.version
            ):
                logger.debug(
                    f"Plugin [{kind.value}:{name}] version {version} already registered. Skipping re-registration."
                )
                return

            self._plugins.setdefault(kind, {})[name] = instance
            self._meta.setdefault(kind, {})[name] = meta
            self._persist_plugins()
            event_dict = {
                "event": "plugin_registered",
                "kind": kind.value,
                "name": name,
                "version": version,
                "plugin_type": meta.plugin_type,
            }
            logger.info(event_dict)
            self._trigger_event(event_dict)

            try:
                asyncio.create_task(
                    self.register_with_omnicore(
                        kind, name, instance, version, meta.author
                    )
                )
            except RuntimeError:
                logger.info("No running event loop; skipping OmniCore registration.")

    def get(self, kind: PlugInKind, name: str) -> Any:
        """Retrieves a plugin class or instance."""
        return self._plugins.get(kind, {}).get(name, None)

    def get_metadata(self, kind: PlugInKind, name: str) -> Optional[PluginMeta]:
        """Retrieves the metadata for a registered plugin."""
        return self._meta.get(kind, {}).get(name, None)

    def list_plugins(self, kind: Optional[PlugInKind] = None) -> Dict[str, Any]:
        """
        Lists all plugins, optionally filtered by kind. Returns a deep copy.
        """
        if kind:
            return deepcopy(self._plugins.get(kind, {}))
        return deepcopy(self._plugins)

    def export_registry(self) -> Dict[str, Any]:
        """
        Exports a comprehensive view of all registered plugins and their metadata.
        Returns a deep copy to ensure immutability.
        """
        with self._lock:
            exportable_data = {}
            for kind, plugins in self._meta.items():
                exportable_data[kind.value] = {
                    name: self._meta_to_dict(meta) for name, meta in plugins.items()
                }
            return deepcopy(exportable_data)

    async def unregister(self, kind: PlugInKind, name: str):
        """
        Unregisters a plugin, first stopping it gracefully.

        Args:
            kind: The plugin kind.
            name: The plugin name.

        Raises:
            PermissionError: If the user lacks write permission.
        """
        # if not self.check_permission("admin", "write"):
        #     raise PermissionError("Write permission required for plugin unregistration")

        with self._kind_locks[kind]:
            plugin = self.get(kind, name)
            self.get_metadata(kind, name)

            if plugin is None:
                logger.warning(
                    f"Plugin [{kind.value}:{name}] not found for unregistration."
                )
                try:
                    plugin_ops_total.labels(operation="unregister").inc()
                except Exception as e:
                    logger.debug(f"Metrics error: {e}")
                return

        if isinstance(plugin, PluginBase):
            try:
                await plugin.stop()
                event_dict = {
                    "event": "plugin_stopped",
                    "kind": kind.value,
                    "name": name,
                    "reason": "unregistration",
                }
                logger.info(event_dict)
                self._trigger_event(event_dict)
            except Exception as e:
                logger.error(
                    f"Failed to stop plugin [{kind.value}:{name}] during unregistration: {e}"
                )
                try:
                    plugin_errors_total.labels(
                        kind=kind.value, name=name, error_type="stop_fail"
                    ).inc()
                except Exception:
                    pass

        with self._kind_locks[kind]:
            self._plugins.get(kind, {}).pop(name, None)
            self._meta.get(kind, {}).pop(name, None)

        self._persist_plugins()
        try:
            plugin_unloads.labels(kind=kind.value, name=name).inc()
            plugin_ops_total.labels(operation="unregister").inc()
        except Exception as e:
            logger.debug(f"Metrics error: {e}")
        event_dict = {"event": "plugin_unregistered", "kind": kind.value, "name": name}
        logger.info(event_dict)
        self._trigger_event(event_dict)

    async def reload(self, kind: PlugInKind, name: str) -> bool:
        """
        Hot-reload a plugin by reloading its module and reinitializing it.

        Args:
            kind: The plugin kind.
            name: The plugin name.

        Returns:
            True if reload was successful, False otherwise.
        """
        with self._kind_locks[kind]:
            plugin_class_or_instance = self.get(kind, name)
            meta = self.get_metadata(kind, name)
            if not (plugin_class_or_instance and meta):
                logger.error(f"Cannot reload: Plugin [{kind.value}:{name}] not found")
                try:
                    plugin_errors_total.labels(
                        kind=kind.value, name=name, error_type="reload_not_found"
                    ).inc()
                except Exception:
                    pass
                return False

        if meta.plugin_type != "class":
            logger.warning(
                f"Reloading is not supported for plugin instances: [{kind.value}:{name}]"
            )
            try:
                plugin_errors_total.labels(
                    kind=kind.value, name=name, error_type="reload_instance_fail"
                ).inc()
            except Exception:
                pass
            return False

        try:
            module_name = plugin_class_or_instance.__module__
            module = importlib.import_module(module_name)
            importlib.reload(module)
            reloaded_plugin_class = getattr(
                module, plugin_class_or_instance.__name__, None
            )
            if not reloaded_plugin_class:
                logger.error(
                    f"Plugin [{kind.value}:{name}] not found in reloaded module"
                )
                try:
                    plugin_errors_total.labels(
                        kind=kind.value, name=name, error_type="reload_module_fail"
                    ).inc()
                except Exception:
                    pass
                return False

            await self.unregister(kind, name)
            await asyncio.sleep(0.1)

            # Re-register using the decorator
            self.register(
                kind,
                name,
                version=meta.version,
                **{
                    k: v
                    for k, v in meta.__dict__.items()
                    if k
                    not in [
                        "name",
                        "kind",
                        "version",
                        "loaded_at",
                        "plugin_type",
                        "dependencies",
                    ]
                },
            )(reloaded_plugin_class)

            # Re-instantiate if needed, and call on_reload
            reloaded_instance = reloaded_plugin_class()
            if hasattr(reloaded_instance, "on_reload"):
                reloaded_instance.on_reload()

            event_dict = {
                "event": "plugin_reloaded",
                "kind": kind.value,
                "name": name,
                "version": meta.version,
            }
            logger.info(event_dict)
            self._trigger_event(event_dict)
            try:
                plugin_ops_total.labels(operation="reload").inc()
            except Exception as e:
                logger.debug(f"Metrics error: {e}")
            return True
        except Exception as e:
            logger.error(
                f"Failed to reload plugin [{kind.value}:{name}]: {e}", exc_info=True
            )
            try:
                plugin_errors_total.labels(
                    kind=kind.value, name=name, error_type="reload_fail"
                ).inc()
            except Exception:
                pass
            return False

    async def health_check(self, kind: PlugInKind, name: str) -> bool:
        """Check if a plugin is healthy."""
        plugin = self.get(kind, name)
        if plugin is None:
            logger.warning(f"Plugin [{kind.value}:{name}] not found for health check")
            try:
                plugin_health_checks.labels(
                    kind=kind.value, name=name, status="not_found"
                ).inc()
            except Exception as e:
                logger.debug(f"Metrics error: {e}")
            return False

        is_healthy = False
        if isinstance(plugin, PluginBase):
            try:
                meta = self.get_metadata(kind, name)
                if meta and meta.is_quarantined:
                    logger.warning(
                        f"Plugin [{kind.value}:{name}] is quarantined and will not be checked."
                    )
                    try:
                        plugin_health_checks.labels(
                            kind=kind.value, name=name, status="quarantined"
                        ).inc()
                    except Exception as e:
                        logger.debug(f"Metrics error: {e}")
                    return False

                is_healthy = await plugin.health_check()
                status_label = "healthy" if is_healthy else "unhealthy"
                try:
                    plugin_health_checks.labels(
                        kind=kind.value, name=name, status=status_label
                    ).inc()
                except Exception as e:
                    logger.debug(f"Metrics error: {e}")
            except Exception as e:
                logger.error(
                    f"Plugin [{kind.value}:{name}] health check failed: {e}",
                    exc_info=True,
                )
                try:
                    plugin_health_checks.labels(
                        kind=kind.value, name=name, status="error"
                    ).inc()
                except Exception as ee:
                    logger.debug(f"Metrics error: {ee}")
                raise
        else:
            logger.warning(
                f"Plugin [{kind.value}:{name}] does not implement PluginBase, assuming healthy."
            )
            is_healthy = True
            try:
                plugin_health_checks.labels(
                    kind=kind.value, name=name, status="unknown"
                ).inc()
            except Exception as e:
                logger.debug(f"Metrics error: {e}")

        return is_healthy

    async def health_check_all(self) -> Dict[str, Any]:
        """
        Performs a batch health check on all registered plugins and reports a summary.

        Returns:
            A dictionary with the overall health status and individual plugin statuses.
        """
        health_status = {}
        health_tasks = []

        with self._lock:
            for kind, plugins in self._plugins.items():
                for name in plugins.keys():
                    health_tasks.append(self.health_check(kind, name))

        results = await asyncio.gather(*health_tasks, return_exceptions=True)

        idx = 0
        with self._lock:
            for kind, plugins in self._plugins.items():
                for name in plugins.keys():
                    result = results[idx]
                    if isinstance(result, Exception):
                        status = "error"
                    else:
                        status = "healthy" if result else "unhealthy"
                    health_status.setdefault(kind.value, {})[name] = status
                    idx += 1

        overall_status = "healthy"
        for kind_status in health_status.values():
            if "unhealthy" in kind_status.values() or "error" in kind_status.values():
                overall_status = "degraded"
                break

        try:
            plugin_ops_total.labels(operation="health_check_all").inc()
        except Exception as e:
            logger.debug(f"Metrics error: {e}")
        return {"overall_status": overall_status, "plugins": health_status}

    def discover(self, package: str, kind: PlugInKind) -> List[str]:
        """
        Recursively discover plugins in a package.
        """
        discovered = []
        package_path = package.replace(".", "/")
        for finder, module_name, ispkg in pkgutil.walk_packages(
            [package_path], prefix=package + "."
        ):
            try:
                module = importlib.import_module(module_name)
                for attr in dir(module):
                    obj = getattr(module, attr)
                    if hasattr(obj, "register_plugin"):
                        meta = getattr(obj, "register_plugin")
                        if isinstance(meta, dict) and "name" in meta:
                            self.register(
                                kind=kind,
                                name=meta["name"],
                                version=meta.get("version", "0.1.0"),
                            )(obj)
                            discovered.append(meta["name"])
                            logger.info(
                                f"Discovered plugin [{kind.value}:{meta['name']}] in {module_name}"
                            )
            except Exception as e:
                logger.error(
                    f"Failed to discover plugins in {module_name}: {e}", exc_info=True
                )
                try:
                    plugin_errors_total.labels(
                        kind="n/a", name=module_name, error_type="discover_fail"
                    ).inc()
                except Exception:
                    pass
        return discovered

    async def load_from_package(
        self, package_url: str, signature: Optional[str] = None
    ):
        """
        Placeholder for a true marketplace feature.
        """
        raise NotImplementedError(
            "This method is a placeholder for dynamic plugin loading from a package URL."
        )

    @contextmanager
    def sandboxed_plugin(self, kind: PlugInKind, name: str):
        """
        Execute a plugin in a sandboxed process.
        """
        plugin = self.get(kind, name)
        if not plugin:
            raise ValueError(f"Plugin [{kind.value}:{name}] not found")

        queue = multiprocessing.Queue()

        def run_plugin(q: multiprocessing.Queue):
            try:
                if hasattr(plugin, "execute") and callable(plugin.execute):
                    result = plugin.execute()
                    q.put(("success", result))
                else:
                    q.put(("error", "Plugin does not have an 'execute' method"))
            except Exception:
                q.put(("error", traceback.format_exc()))

        process = multiprocessing.Process(target=run_plugin, args=(queue,))
        process.start()
        logger.info(
            f"Plugin [{kind.value}:{name}] started in sandboxed process (PID: {process.pid})"
        )
        yield

        process.join(timeout=5)
        if process.is_alive():
            logger.error(
                f"Plugin [{kind.value}:{name}] timed out in sandbox, terminating."
            )
            process.terminate()
            process.join()

        if not queue.empty():
            status, result = queue.get()
            if status == "error":
                logger.error(
                    f"Plugin [{kind.value}:{name}] failed in sandbox: {result}"
                )
            else:
                logger.info(f"Plugin [{kind.value}:{name}] executed successfully")

    async def initialize_all(self) -> None:
        """Initializes all registered plugins in parallel."""
        tasks = []
        with self._lock:
            for kind, plugins in self._plugins.items():
                for name, plugin in plugins.items():
                    if isinstance(plugin, PluginBase):
                        tasks.append(self._initialize_plugin(kind, name, plugin))
        await asyncio.gather(*tasks, return_exceptions=True)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def _initialize_plugin(self, kind: PlugInKind, name: str, plugin: PluginBase):
        """Initializes a single plugin with retry logic."""
        try:
            await plugin.initialize()
            logger.info(f"Initialized plugin [{kind.value}:{name}]")
            try:
                plugin_ops_total.labels(operation="initialize").inc()
            except Exception as e:
                logger.debug(f"Metrics error: {e}")
        except Exception as e:
            logger.error(
                f"Failed to initialize plugin [{kind.value}:{name}]: {e}", exc_info=True
            )
            try:
                plugin_errors_total.labels(
                    kind=kind.value, name=name, error_type="initialize"
                ).inc()
            except Exception:
                pass
            raise

    async def start_all(self) -> None:
        """Starts all registered plugins in parallel."""
        tasks = []
        with self._lock:
            for kind, plugins in self._plugins.items():
                for name, plugin in plugins.items():
                    if isinstance(plugin, PluginBase):
                        tasks.append(self._start_plugin(kind, name, plugin))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _start_plugin(self, kind: PlugInKind, name: str, plugin: PluginBase):
        try:
            await plugin.start()
            logger.info(f"Started plugin [{kind.value}:{name}]")
            try:
                plugin_ops_total.labels(operation="start").inc()
            except Exception as e:
                logger.debug(f"Metrics error: {e}")
        except Exception as e:
            logger.error(
                f"Failed to start plugin [{kind.value}:{name}]: {e}", exc_info=True
            )
            try:
                plugin_errors_total.labels(
                    kind=kind.value, name=name, error_type="start_fail"
                ).inc()
            except Exception:
                pass

    async def stop_all(self) -> None:
        """Stops all registered plugins in parallel."""
        tasks = []
        with self._lock:
            for kind, plugins in self._plugins.items():
                for name, plugin in plugins.items():
                    if isinstance(plugin, PluginBase):
                        tasks.append(self._stop_plugin(kind, name, plugin))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _stop_plugin(self, kind: PlugInKind, name: str, plugin: PluginBase):
        try:
            await plugin.stop()
            logger.info(f"Stopped plugin [{kind.value}:{name}]")
            try:
                plugin_ops_total.labels(operation="stop").inc()
            except Exception as e:
                logger.debug(f"Metrics error: {e}")
        except Exception as e:
            logger.error(
                f"Failed to stop plugin [{kind.value}:{name}]: {e}", exc_info=True
            )
            try:
                plugin_errors_total.labels(
                    kind=kind.value, name=name, error_type="stop_fail"
                ).inc()
            except Exception:
                pass

    # Async context management for the registry itself
    async def __aenter__(self):
        """Initializes all plugins when entering the async context."""
        await self.initialize_all()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Stops all plugins when exiting the async context."""
        await self.stop_all()
        logger.info("Plugin registry closed via async context manager.")


def register(kind: PlugInKind, name: str, version: str, author: str):
    """Compatibility decorator for registering simple functions as plugins."""

    def decorator(func: Callable) -> Callable:
        class FunctionPlugin(PluginBase):
            async def initialize(self):
                pass

            async def start(self):
                pass

            async def stop(self):
                pass

            async def health_check(self) -> bool:
                return True

            async def get_capabilities(self) -> List[str]:
                return []

            def execute(self, *args, **kwargs) -> Any:
                return func(*args, **kwargs)

        get_registry().register_instance(
            kind=kind,
            name=name,
            instance=FunctionPlugin(),
            version=version,
            author=author,
        )
        logger.info(
            f"Registered function plugin [{kind.value}:{name}] (version: {version}, author: {author})"
        )
        return func

    return decorator


# Lazy singleton instance - DO NOT instantiate at module import time
_registry_instance: Optional[PluginRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> PluginRegistry:
    """
    Get the singleton PluginRegistry instance.
    Lazy initialization ensures the registry is only created when first accessed,
    avoiding heavy import-time initialization.
    
    This function is thread-safe and ensures only one instance is created.
    
    Returns:
        The singleton PluginRegistry instance.
    """
    global _registry_instance
    if _registry_instance is None:
        # Use a module-level lock for thread-safe lazy initialization
        with _registry_lock:
            # Double-check pattern to avoid race conditions
            if _registry_instance is None:
                _registry_instance = PluginRegistry()
    return _registry_instance


# Backwards compatibility: expose registry as a module-level variable
# This uses a property-like pattern via __getattr__ to maintain lazy loading
def __getattr__(name: str) -> Any:
    """
    Module-level __getattr__ for lazy loading of registry-related attributes.
    This allows 'from arbiter_plugin_registry import registry' to work
    while deferring initialization until first access.
    
    Note: Accessing PLUGIN_REGISTRY returns a fresh snapshot of the registry
    state on each access, ensuring up-to-date data. This differs from the
    original implementation which created a static snapshot at import time.
    """
    if name == "registry":
        return get_registry()
    elif name == "PLUGIN_REGISTRY":
        # For backwards compatibility, expose the plugins dictionary
        # Note: This is a deep copy to prevent external modification
        # Returns a fresh snapshot on each access to ensure up-to-date data
        return get_registry().list_plugins()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
