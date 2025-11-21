import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union, Pattern
from collections import defaultdict
from pydantic import BaseModel, Field, ValidationError
import asyncio
import inspect
import traceback
from pathlib import Path
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from enum import Enum
import importlib.util
import sys
import os

# --- BEGIN SOLUTION (Corrected Path) ---
# Force Python to find the local 'self_fixing_engineer' package
# This resolves import conflicts when running from the project root.
_project_root = Path(__file__).parent.parent  # This is The_Code_Factory-master
_arbiter_path = _project_root / 'self_fixing_engineer'
if _arbiter_path.exists() and str(_arbiter_path) not in sys.path:
    sys.path.insert(0, str(_arbiter_path))
# --- END SOLUTION ---

import multiprocessing as mp
import uuid
import random
import json
from datetime import datetime
import pkgutil, importlib
import time
import pickle
import ast
import builtins
try:
    import resource
except ImportError:
    resource = None
import signal
import hmac
import hashlib

# Corrected imports to use centralized config and new package structure
try:
    from arbiter.config import ArbiterConfig
    from arbiter import __path__ as assistant_pkg_path
except ImportError:
    class ArbiterConfig:
        def __init__(self):
            self.PLUGIN_REGISTRY = {}
            self.log_level = 'INFO'
            self.PLUGIN_DIR = 'plugins'
            self.PLUGIN_EXECUTION_TIMEOUT = 30
            self.PLUGINS_ENABLED = True
            self.PLUGIN_SIGNING_KEY = 'insecure_default_key'
    assistant_pkg_path = []
    print("arbiter package not found. Using mock config.")

try:
    from arbiter_plugin_registry import PlugInKind as ArbiterPlugInKind, PLUGIN_REGISTRY as ARBITER_PLUGIN_REGISTRY
except ImportError:
    ArbiterPlugInKind = None
    ARBITER_PLUGIN_REGISTRY = None
    print("arbiter_plugin_registry not found. Using None values.")


try:
    from omnicore_engine.database import Database
except ImportError:
    Database = None
    print("omnicore_engine.database not found. Database functionality disabled.")

try:
    from omnicore_engine.metrics import plugin_executions
except ImportError:
    plugin_executions = None
    print("omnicore_engine.metrics not found. Metrics functionality disabled.")
    
try:
    from redis.asyncio import Redis
except ImportError:
    Redis = None
    print("redis.async asyncio not found. Redis functionality disabled.")

try:
    from omnicore_engine.message_bus import ShardedMessageBus, PluginMessageBusAdapter, MessageFilter, Message
except ImportError:
    ShardedMessageBus = None
    PluginMessageBusAdapter = None
    MessageFilter = None
    Message = None
    print("omnicore_engine.message_bus not found. Message bus functionality disabled.")

try:
    from intent_capture.agent_core import CollaborativeAgent, AgentTeam
except ImportError:
    CollaborativeAgent = None
    AgentTeam = None
    print("intent_capture not found. Intent capture functionality disabled.")

try:
    from self_healing_import_fixer.import_fixer.fixer_ai import AIManager
    from self_healing_import_fixer.import_fixer.import_fixer_engine import create_import_fixer_engine, ImportFixerEngine
except ImportError:
    AIManager = None
    create_import_fixer_engine = None
    ImportFixerEngine = None
    print("self_healing_import_fixer not found. Import fixer functionality disabled.")

# --- FIX: Broaden exception handling for heavy, optional dependencies ---
try:
    from test_generation.backends import MyBackend
except Exception as e:
    MyBackend = None
    print(f"test_generation.backends not available ({e}); skipping.")

try:
    from simulation.runners import MyCustomRunner, MyBetterRunner
except Exception as e:
    MyCustomRunner = None
    MyBetterRunner = None
    print(f"simulation.runners not available ({e}); skipping.")
# --- END FIX ---

try:
    from envs.code_health_env import CodeHealthEnv
    from envs.evolution import evolve_configs
except ImportError:
    CodeHealthEnv = None
    evolve_configs = None
    print("envs module not found.")

try:
    from simulation.registry import SIM_REGISTRY
except ImportError:
    SIM_REGISTRY = None
    print("simulation.registry not found.")

# New imports for security utils
from .security_utils import get_security_utils

settings = ArbiterConfig()

try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:
    NETWORKX_AVAILABLE = False
    nx = None
    print("networkx not available. Install 'networkx' for full PluginDependencyGraph functionality.")

logger = logging.getLogger(__name__)
logger.setLevel(getattr(settings, 'log_level', 'INFO').upper())
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

if os.name == "nt":
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass # already set

class PlugInKind(str, Enum):
    FIX = "fix"
    CHECK = "check"
    VALIDATION = "validation"
    EXECUTION = "execution"
    CORE_SERVICE = "core_service"
    SCENARIO = "scenario"
    CUSTOM = "custom"
    AGGREGATOR = "aggregator"
    AI_ASSISTANT = "ai_assistant"
    OPTIMIZATION = "optimization"
    MONITORING = "monitoring"
    GROWTH_MANAGER = "growth_manager"
    SIMULATION_RUNNER = "simulation_runner"
    EVOLUTION = "evolution"
    RL_ENVIRONMENT = "rl_environment"

class SecurityError(Exception):
    """Custom exception for security-related issues."""
    pass

def timeout_handler(signum, frame):
    """Handler for the alarm signal."""
    raise TimeoutError("Plugin execution timed out")

def execute_with_limits(func, *args, **kwargs):
    """Executes a function with resource limits and a timeout."""
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(getattr(settings, 'PLUGIN_EXECUTION_TIMEOUT', 30))  # 30 second timeout

    # Limit memory usage (e.g., 512MB)
    try:
        if resource:
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, -1))
    except (ValueError, OSError) as e:
        logger.warning(f"Could not set resource limits: {e}")
        
    try:
        return func(*args, **kwargs)
    finally:
        signal.alarm(0)

SAFE_BUILTINS = {
    'None': None,
    'True': True,
    'False': False,
    'int': int,
    'float': float,
    'str': str,
    'list': list,
    'dict': dict,
    'tuple': tuple,
    'range': range,
    'len': len,
    'min': min,
    'max': max,
    'sum': sum,
    'abs': abs,
    'round': round,
    'set': set,
    'frozenset': frozenset,
    'map': map,
    'filter': filter,
    'zip': zip,
    'isinstance': isinstance,
    'callable': callable,
}

def safe_exec_plugin(code: str, filename: str):
    """
    Safely executes plugin code by restricting imports and dangerous builtins.
    """
    tree = ast.parse(code, filename)
    
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            allowed_modules = {'math', 'datetime', 'json', 'typing', 're', 'asyncio'}
            for name in node.names:
                if name.name not in allowed_modules:
                    raise SecurityError(f"Import of {name.name} not allowed")
        
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in ['eval', 'exec', '__import__', 'compile', 'open']:
                    raise SecurityError(f"Dangerous function {node.func.id} not allowed")

    restricted_globals = {'__builtins__': SAFE_BUILTINS}
    
    local_scope = {}
    
    exec(compile(tree, filename, 'exec'), restricted_globals, local_scope)
    
    return local_scope


def _plugin_wrapper(queue, fn_to_run, args_to_pass, kwargs_to_pass):
    try:
        result = execute_with_limits(fn_to_run, *args_to_pass, **kwargs_to_pass)
        try:
            queue.put(("ok", result))
        except Exception as put_err:
            import traceback as _tb
            queue.put(("err", ("UnpicklableResult", repr(put_err), _tb.format_exc())))
    except Exception as e:
        import traceback as _tb
        queue.put(("err", (e.__class__.__name__, str(e), _tb.format_exc())))

def _is_picklable(obj) -> bool:
    try:
        pickle.dumps(obj)
        return True
    except Exception:
        return False

def _all_picklable(*objs) -> bool:
    try:
        pickle.dumps(objs)
        return True
    except Exception:
        return False

def safe_execute_plugin(fn: Callable, *args, **kwargs):
    """
    Runs a plugin function in an isolated process with restricted imports and a timeout.
    This provides a basic sandboxing mechanism to prevent malicious or buggy code from
    affecting the main process.
    """
    q = mp.Queue()
    p = mp.Process(target=_plugin_wrapper, args=(q, fn, args, kwargs))
    try:
        p.start()
        p.join(timeout=getattr(settings, 'PLUGIN_EXECUTION_TIMEOUT', 30))
        
        if p.is_alive():
            p.terminate()
            p.join()
            raise TimeoutError("Plugin execution timed out.")
        
        if q.empty():
            raise RuntimeError("Plugin process exited without returning a result.")
        
        status, payload = q.get()
        if status == "err":
            exc_type, msg, tb = payload
            raise RuntimeError(f"Plugin raised {exc_type}: {msg}\n{tb}")
        return payload
    finally:
        try:
            q.close()
            q.join_thread()
        except Exception:
            pass

def verify_plugin_signature(plugin_code: bytes, signature: str) -> bool:
    """Verifies the HMAC signature of plugin code."""
    signing_key = getattr(settings, 'PLUGIN_SIGNING_KEY', 'insecure_default_key').encode()
    expected_sig = hmac.new(
        signing_key,
        plugin_code,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_sig, signature)

def validate_plugin_path(filepath: Path, plugin_dir: Path) -> Path:
    """Validates that a plugin path is within the designated directory to prevent path traversal."""
    resolved = filepath.resolve()
    plugin_dir_resolved = plugin_dir.resolve()
    if not str(resolved).startswith(str(plugin_dir_resolved)):
        raise SecurityError(f"Path traversal detected: {filepath}")
    return resolved

class PluginMeta(BaseModel):
    name: str
    kind: str
    description: str = ""
    version: str = "0.1.0"
    safe: bool = True
    source: str = "local"
    params_schema: Dict[str, Any] = Field(default_factory=dict)
    signature: Optional[str] = None

class PluginPerformanceTracker:
    """Tracks performance metrics for each plugin version."""
    def __init__(self, db: Database, audit_client: Optional[Any] = None):
        self.db = db
        self.audit_client = audit_client
        self.logger = logging.getLogger("PluginPerformanceTracker")
        
    async def record_performance(self, kind: str, name: str, version: str, metrics: Dict[str, Any]):
        """Records performance metrics for a plugin version in the database."""
        try:
            record = {
                "kind": "plugin_performance",
                "name": f"{kind}:{name}",
                "detail": metrics,
                "custom_attributes": {"version": version},
                "ts": time.time(),
            }
            if self.db:
                await self.db.save_audit_record(record)
                self.logger.info(f"Recorded performance for plugin {kind}:{name}:{version}.")
            else:
                self.logger.warning("Database not initialized. Skipping performance record.")
        except Exception as e:
            self.logger.error(f"Failed to record performance for plugin {kind}:{name}:{version}: {e}", exc_info=True)
            if self.audit_client:
                await self.audit_client.add_entry_async(
                    "plugin_performance_record_failed", name,
                    {"version": version, "error": str(e)},
                    error=str(e), agent_id="PluginPerformanceTracker"
                )

    async def get_performance_history(self, kind: str, name: str, version: str, lookback_period: int = 3600) -> List[Dict[str, Any]]:
        """Retrieves historical performance metrics for a plugin version."""
        try:
            if not self.db:
                self.logger.warning("Database not initialized. Cannot retrieve performance history.")
                return []
            
            end_time = time.time()
            start_time = end_time - lookback_period
            records = await self.db.query_audit_records(
                filters={
                    "kind": "plugin_performance",
                    "name": f"{kind}:{name}",
                    "ts_start": start_time,
                    "ts_end": end_time
                }
            )
            # Filter for the specific version
            history = [
                r['detail'] for r in records if r.get('custom_attributes', {}).get('version') == version
            ]
            self.logger.info(f"Retrieved {len(history)} performance records for plugin {kind}:{name}:{version}.")
            return history
        except Exception as e:
            self.logger.error(f"Failed to retrieve performance history for plugin {kind}:{name}:{version}: {e}", exc_info=True)
            return []


class Plugin:
    def __init__(self, meta: PluginMeta, fn: Callable, performance_tracker: Optional[PluginPerformanceTracker] = None):
        self.meta = meta
        self.fn = fn
        self.logger = logging.getLogger(f"Plugin.{meta.name}")
        self.name = meta.name
        self.kind = meta.kind
        self.message_bus_adapter: Optional[PluginMessageBusAdapter] = None
        self.subscriptions_to_register: List[Tuple[Union[str, Pattern], Callable[[Message], Any], Optional[MessageFilter]]] = []
        self.performance_tracker = performance_tracker

    async def execute(self, *args, **kwargs) -> Any:
        """
        Executes the plugin's core function and records its performance.
        This method can be called directly or via the message bus adapter.
        """
        start_time = time.time()
        error_occurred = False
        error_type = "none"
        
        timeout = getattr(settings, "PLUGIN_EXECUTION_TIMEOUT", 30)

        try:
            self.logger.info(f"Executing plugin '{self.meta.name}' (Kind: {self.meta.kind}, Version: {self.meta.version})")
            
            executable_fn = self.fn
            if hasattr(self.fn, 'execute') and callable(self.fn.execute):
                executable_fn = self.fn.execute

            is_coroutine = asyncio.iscoroutinefunction(executable_fn)

            if self.meta.safe and not is_coroutine:
                if _is_picklable(executable_fn) and _all_picklable(args, kwargs):
                    self.logger.debug("Executing plugin in process sandbox.")
                    result = await asyncio.to_thread(safe_execute_plugin, executable_fn, *args, **kwargs)
                else:
                    self.logger.warning("Safe mode: non-picklable fn/args; running in thread (no process sandbox).")
                    result = await asyncio.to_thread(execute_with_limits, executable_fn, *args, **kwargs)
            elif self.meta.safe and is_coroutine:
                self.logger.warning("Async safe plugin — not sandboxed; enforcing timeout.")
                result = await asyncio.wait_for(executable_fn(*args, **kwargs), timeout=timeout)
            elif not self.meta.safe and is_coroutine:
                result = await asyncio.wait_for(executable_fn(*args, **kwargs), timeout=timeout)
            else:
                result = executable_fn(*args, **kwargs)
            
            self.logger.debug(f"Plugin '{self.meta.name}' executed successfully. Result snippet: {str(result)[:100]}")
            return result
        except Exception as e:
            error_occurred = True
            error_type = type(e).__name__
            self.logger.error(f"Error executing plugin '{self.meta.name}': {e}", exc_info=True)
            raise
        finally:
            end_time = time.time()
            execution_time = end_time - start_time
            if self.performance_tracker:
                metrics = {
                    "execution_time": execution_time,
                    "error_rate": 1 if error_occurred else 0,
                    "error_type": error_type
                }
                await self.performance_tracker.record_performance(self.kind, self.name, self.meta.version, metrics)
            if plugin_executions:
                try:
                    plugin_executions.labels(kind=self.kind, name=self.name, version=self.meta.version, error=error_occurred).inc()
                except Exception:
                    self.logger.debug("metrics increment failed (noop).")

class PluginRegistry:
    def __init__(self):
        self._plugins = defaultdict(dict)
        self._entrypoints = {}
        self._is_initialized = False
        self.db: Optional[Database] = None
        self.audit_client: Optional[Any] = None
        self.message_bus: Optional[ShardedMessageBus] = None
        self.performance_tracker: Optional[PluginPerformanceTracker] = None
        self.logger = logging.getLogger("PluginRegistry")
        self._loop = None

    @property
    def plugins(self):
        return self._plugins
    
    def _attach_bus_adapter_if_any(self, name: str, kind_str: str, plugin: "Plugin"):
        if self.message_bus and PluginMessageBusAdapter is not None:
            plugin.message_bus_adapter = PluginMessageBusAdapter(self.message_bus, f"{kind_str}:{name}")
            if getattr(plugin, "subscriptions_to_register", None):
                for topic_info in plugin.subscriptions_to_register:
                    topic_to_subscribe: Union[str, Pattern]
                    filter_obj: Optional[MessageFilter] = None
                    if isinstance(topic_info, tuple) and len(topic_info) == 2:
                        topic_to_subscribe, filter_dict = topic_info
                        if filter_dict and isinstance(filter_dict, dict) and MessageFilter is not None:
                            filter_obj = MessageFilter(lambda p, f=filter_dict: all(p.get(k) == v for k, v in f.items()))
                    else:
                        topic_to_subscribe = topic_info
                    plugin.message_bus_adapter.subscribe(topic_to_subscribe, plugin.execute, filter=filter_obj)

    async def initialize(self):
        if self._is_initialized:
            return
        
        self.logger.info("Initializing PluginRegistry...")
        self._loop = asyncio.get_running_loop()

        if self.db:
            self.performance_tracker = PluginPerformanceTracker(db=self.db, audit_client=self.audit_client)
        
        await self.load_arbiter_plugins()
        await self.load_from_directory(settings.PLUGIN_DIR)
        self.load_ai_assistant_plugins()
        
        # CodeHealthEnv
        if CodeHealthEnv is not None:
            try:
                plugin_meta = PluginMeta(name="code_health_env", kind=PlugInKind.CORE_SERVICE.value,
                                         description="Reinforcement learning environment for code health.")
                self.register(PlugInKind.CORE_SERVICE.value, "code_health_env",
                              Plugin(plugin_meta, CodeHealthEnv, performance_tracker=self.performance_tracker))
                self.logger.info("Registered CodeHealthEnv plugin.")
            except Exception as e:
                self.logger.error(f"Failed to register CodeHealthEnv: {e}")
        else:
            self.logger.info("CodeHealthEnv not available; skipping.")

        # evolve_configs
        if evolve_configs is not None:
            try:
                plugin_meta = PluginMeta(name="evolve_configs", kind=PlugInKind.OPTIMIZATION.value,
                                         description="Genetic algorithm for configuration tuning.")
                self.register(PlugInKind.OPTIMIZATION.value, "evolve_configs",
                              Plugin(plugin_meta, evolve_configs, performance_tracker=self.performance_tracker))
                self.logger.info("Registered evolve_configs plugin.")
            except Exception as e:
                self.logger.error(f"Failed to register evolve_configs: {e}")
        else:
            self.logger.info("evolve_configs not available; skipping.")
            
        backend_name = "test_generation_backend"
        if MyBackend is not None:
            try:
                backend_plugin_instance = Plugin(PluginMeta(name=backend_name, kind=PlugInKind.EXECUTION.value), MyBackend(), performance_tracker=self.performance_tracker)
                self.register(PlugInKind.EXECUTION.value, backend_name, backend_plugin_instance)
                self.logger.info(f"Registered test generation backend: {backend_name}")
            except Exception as e:
                self.logger.error(f"Failed to register test generation backend: {e}", exc_info=True)
        else:
            self.logger.info("MyBackend not available; skipping.")

        runner_name = "my_custom_runner"
        if MyCustomRunner is not None:
            try:
                runner_instance = MyCustomRunner()
                runner_plugin_instance = Plugin(
                    PluginMeta(
                        name=runner_name,
                        kind=PlugInKind.SIMULATION_RUNNER.value,
                        description="A custom simulation runner.",
                        version="1.0.0"
                    ),
                    runner_instance,
                    performance_tracker=self.performance_tracker
                )
                self.register(PlugInKind.SIMULATION_RUNNER.value, runner_name, runner_plugin_instance)
                self.logger.info(f"Registered custom simulation runner: {runner_name}")
            except Exception as e:
                self.logger.error(f"Failed to register custom simulation runner: {e}", exc_info=True)
        else:
            self.logger.info("MyCustomRunner not available; skipping.")
            
        better_runner_name = "better_runner"
        if MyBetterRunner is not None:
            try:
                better_runner_instance = MyBetterRunner()
                better_runner_plugin = Plugin(
                    PluginMeta(
                        name=better_runner_name,
                        kind=PlugInKind.SIMULATION_RUNNER.value,
                        description="An improved simulation runner.",
                        version="1.0.0"
                    ),
                    better_runner_instance,
                    performance_tracker=self.performance_tracker
                )
                self.register(PlugInKind.SIMULATION_RUNNER.value, better_runner_name, better_runner_plugin)
                self.logger.info(f"Registered new simulation runner: {better_runner_name}")
            except Exception as e:
                self.logger.error(f"Failed to register new simulation runner {better_runner_name}: {e}", exc_info=True)
        else:
            self.logger.info("MyBetterRunner not available; skipping.")

        if SIM_REGISTRY is not None:
            simulation_plugin = Plugin(
                PluginMeta(
                    name="simulation_registry",
                    kind=PlugInKind.SIMULATION_RUNNER.value,
                    description="Full simulation API as defined in simulation/registry.py",
                    version="1.0.0",
                ),
                SIM_REGISTRY,
            )
            self.register(PlugInKind.SIMULATION_RUNNER.value, "simulation_registry", simulation_plugin)
            self.logger.info("Registered simulation registry plugin.")
        else:
            self.logger.info("SIM_REGISTRY not available; skipping simulation_registry plugin.")

        # Register AI Import Fixer as a Plugin
        try:
            from self_healing_import_fixer.import_fixer.import_fixer_engine import create_import_fixer_engine
            import_fixer_engine = create_import_fixer_engine()
            self.register_import_fixer_plugin(import_fixer_engine)
            self.logger.info("Dual-registered ImportFixerEngine as a plugin.")
        except Exception as e:
            self.logger.info(f"ImportFixerEngine not available; skipping. Reason: {e}")

# -- Make SFE Test Generation available via the registry (no new files) -------
        try:
            if os.getenv("SFE_ENABLED", "true").lower() == "true":
                from test_generation.orchestrator.orchestrator import GenerationOrchestrator  # local import to avoid cost when unused

                async def _sfe_generate_tests(*, targets, project_root=None, suite_dir=None, language=None, **kwargs):
                    """
                    Thin forwarder into SFE. Keeps SFE’s contract intact.
                    targets: list[dict] like {"identifier": "...", "language": "..."}.
                    If caller passed strings, normalize to dicts with default language.
                    """
                    # Normalize inputs (don’t mutate caller data)
                    _targets = []
                    for t in targets or []:
                        if isinstance(t, str):
                            _targets.append({"identifier": t, "language": (language or "python")})
                        else:
                            _targets.append(t)

                    # Minimal, safe defaults (no new config files)
                    _cfg = kwargs.get("config") or {
                        "max_parallel_generation": 4,
                        "demo_max_per_language": 2,
                        "enrichment_plugins": {
                            "header_enabled": True,
                            "mocking_import_enabled": True,
                            "llm_refinement_enabled": False,
                        },
                    }
                    _project_root = project_root or os.getcwd()
                    _suite_dir    = suite_dir or os.path.join(_project_root, "tests", "generated")

                    async def _call_maybe_async(fn, *a, **kw):
                        return (await fn(*a, **kw)) if inspect.iscoroutinefunction(fn) \
                               else await asyncio.to_thread(fn, *a, **kw)

                    orch = GenerationOrchestrator(_cfg, _project_root, _suite_dir)
                    out_rel = os.path.relpath(_suite_dir, _project_root)
                    return await _call_maybe_async(
                        orch.generate_tests_for_targets,
                        targets=_targets,
                        output_base_relative=out_rel,
                    )

                meta = PluginMeta(
                    name="sfe.generate_tests",
                    kind=PlugInKind.EXECUTION.value,   # use an existing runner kind you already have
                    description="Self-Fixing Engineer: generate tests for specified targets",
                    version="1.0.0",
                    safe=True,                         # your Plugin class will sandbox non-async; this is async
                    source="engine",
                    params_schema={
                        "targets": {"type": "array"},
                        "project_root": {"type": "string"},
                        "suite_dir": {"type": "string"},
                        "language": {"type": "string"},
                    },
                )
                plugin_instance = Plugin(meta, _sfe_generate_tests, performance_tracker=self.performance_tracker)
                self.register(meta.kind, meta.name, plugin_instance)
                self.logger.info("Registered plugin 'sfe.generate_tests' (EXECUTION).")
            else:
                self.logger.info("SFE_ENABLED=false — skipping SFE registration.")
        except Exception as e:
            self.logger.error(f"Failed to register 'sfe.generate_tests': {e}", exc_info=True)
# -----------------------------------------------------------------------------
        self._is_initialized = True
        self.logger.info("PluginRegistry initialization complete.")

    def set_message_bus(self, message_bus: ShardedMessageBus):
        self.message_bus = message_bus
        self.logger.info("MessageBus set for PluginRegistry.")

    def register(self, kind: str, name: str, plugin: Any, version: str = "1.0.0", author: str = "unknown", entrypoints: Dict[str, Callable] = None):
        config = ArbiterConfig()
        if not config.PLUGINS_ENABLED:
            raise ValueError("Plugins are disabled in ArbiterConfig")
        if kind not in self._plugins:
            self._plugins[kind] = {}
        self._plugins[kind][name] = plugin
        self._attach_bus_adapter_if_any(name, kind, plugin)
        if name == "arbiter":
            self._entrypoints.update({
                "orchestrate": plugin.orchestrate,
                "health_check": plugin.health_check,
                "register_plugin": plugin.register_plugin
            })
        if entrypoints:
            self._entrypoints.update({f"{name}_{k}": v for k, v in entrypoints.items()})
        self.logger.info(f"Registered plugin [{kind}:{name}] in omnicore_engine")
        if self.audit_client:
            asyncio.create_task(self.audit_client.add_entry_async(
                "plugin_registered_omnicore",
                name,
                {"kind": kind, "version": version, "author": author}
            ))

    async def load_arbiter_plugins(self):
        try:
            from arbiter.config import ArbiterConfig
        except ImportError:
            self.logger.info("Arbiter not installed; skipping arbiter plugin load.")
            return

        config = ArbiterConfig()
        for kind, plugins in config.PLUGIN_REGISTRY.items():
            for name, plugin_info in plugins.items():
                self.register(kind, name, plugin_info["plugin"], plugin_info["version"], plugin_info["author"])
        self.logger.info("Loaded arbiter plugins into omnicore_engine")

    def unregister(self, kind: str, name: str) -> bool:
        """
        Unregister (remove) a plugin by kind and name.
        If the plugin has a message bus adapter, all its subscriptions are also unsubscribed.
        Returns True if the plugin was removed, False if not found.
        """
        kind_str = kind if isinstance(kind, str) else kind.value
        if name in self.plugins.get(kind_str, {}):
            plugin_to_unregister = self.plugins[kind_str][name]
            
            if plugin_to_unregister.message_bus_adapter:
                plugin_to_unregister.message_bus_adapter.unsubscribe_all()
                self.logger.info(f"Unsubscribed old adapter for plugin '{name}' (Kind: {kind_str}).")
            
            del self.plugins[kind_str][name]
            self.logger.info(f"Unregistered plugin '{name}' of kind '{kind_str}'.")
            return True
        self.logger.warning(f"Plugin '{name}' of kind '{kind_str}' not found for unregistration.")
        return False

    def get(self, kind: str, name: str) -> Optional[Plugin]:
        return self.plugins.get(kind, {}).get(name)

    def get_plugins_by_kind(self, kind: str) -> List[Plugin]:
        return list(self.plugins.get(kind, {}).values())
    
    def list_plugins(self) -> Dict[str, List[str]]:
        return {kind: list(names.keys()) for kind, names in self.plugins.items()}

    def get_plugin_names(self, kind: Optional[str] = None) -> List[str]:
        if kind:
            return list(self.plugins.get(kind, {}).keys())
        all_names = []
        for k in self.plugins:
            all_names.extend(self.plugins[k].keys())
        return all_names

    def __repr__(self):
        return f"PluginRegistry(plugins={dict(self.plugins)})"

    async def load_from_directory(self, directory: str):
        plugin_dir_path = Path(directory)
        if not plugin_dir_path.is_dir():
            self.logger.error(f"Plugin directory not found: {directory}")
            return

        self.logger.info(f"Scanning directory '{directory}' for plugins...")
        
        if str(plugin_dir_path) not in sys.path:
            sys.path.insert(0, str(plugin_dir_path))

        for item in plugin_dir_path.iterdir():
            if item.is_file() and item.suffix == ".py" and item.name != "__init__.py":
                module_name = item.stem
                file_path = validate_plugin_path(item, plugin_dir_path)
                self.logger.debug(f"Attempting to load plugin from file: {file_path}")
                try:
                    # New: Add security validation before loading
                    security = get_security_utils()
                    safe_filename = security.sanitize_filename(file_path.name)
                    with open(file_path, 'rb') as f:
                        content = f.read()
                        is_valid, mime_type = security.validate_file_type(str(file_path), content)
                        if not is_valid:
                            raise SecurityError(f"Invalid file type: {mime_type}")
                    # End new security validation

                    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        if module_name in sys.modules:
                            importlib.reload(sys.modules[module_name])
                            self.logger.debug(f"Reloaded existing module: {module_name}")
                        else:
                            sys.modules[module_name] = module
                            spec.loader.exec_module(module)
                            self.logger.debug(f"Loaded new module: {module_name}")

                        self.logger.info(f"Finished processing plugin file: {item.name}")
                    else:
                        self.logger.error(f"Could not get spec/loader for module {module_name} from {file_path}.")
                except Exception as e:
                    self.logger.error(f"Error loading plugin from {file_path}: {e}", exc_info=True)
                    if self.audit_client:
                        await self.audit_client.add_entry_async("plugin_load_failed", module_name, 
                                                                 {"file": str(file_path), "error": str(e), "traceback": traceback.format_exc()},
                                                                 error=str(e), agent_id="PluginRegistry")
        self.logger.info(f"Finished scanning directory '{directory}' for plugins. Total plugins registered: {sum(len(k) for k in self.plugins.values())}")

    def load_ai_assistant_plugins(self) -> None:
        self.logger.info("Loading AI assistant plugins from arbiter package...")
        if not assistant_pkg_path:
            self.logger.info("No arbiter package found; skipping AI assistant plugins.")
            return
        for finder, name, ispkg in pkgutil.iter_modules(assistant_pkg_path):
            full_module_name = f"arbiter.{name}"
            try:
                if full_module_name in sys.modules:
                    importlib.reload(sys.modules[full_module_name])
                    self.logger.debug(f"Reloaded AI assistant module: {full_module_name}")
                else:
                    importlib.import_module(full_module_name)
                    self.logger.debug(f"Imported AI assistant module: {full_module_name}")
                self.logger.info(f"Successfully loaded AI assistant module: {full_module_name}")
            except Exception as e:
                self.logger.error(f"Error loading AI assistant plugin from {full_module_name}: {e}", exc_info=True)
                if self.audit_client:
                    asyncio.create_task(self.audit_client.add_entry_async(
                        "ai_assistant_plugin_load_failed", full_module_name,
                        {"error": str(e), "traceback": traceback.format_exc()},
                        error=str(e), agent_id="PluginRegistry"
                    ))
        self.logger.info("Finished loading AI assistant plugins.")

    def register_import_fixer_plugin(self, engine_instance: ImportFixerEngine):
        """
        Registers the ImportFixerEngine as a plugin for PluginRegistry.
        This enables it to be discovered/used by the message bus, scenarios, etc.
        """
        meta = PluginMeta(
            name="import_fixer_engine",
            kind=PlugInKind.FIX.value,
            description="Native OmniCore import fixing engine (AI + policy + audit, full lifecycle)",
            version="1.0.0",
            safe=True,
            source="engine",
            params_schema={},
        )
        plugin_instance = Plugin(meta, engine_instance.fix_code)
        self.register(meta.kind, meta.name, plugin_instance)
        engine_instance.logger.info("ImportFixerEngine registered as a plugin.")
        return plugin_instance

class PluginWatcher:
    def __init__(self, registry: "PluginRegistry", directory: str):
        self._observer = Observer()
        self._handler = PluginEventHandler(registry, directory)
        self.directory = directory
        self.logger = logging.getLogger("PluginWatcher")
        
    def start(self):
        self.logger.info(f"Starting to watch directory: {self.directory}")
        self._observer.schedule(self._handler, path=self.directory, recursive=False)
        self._observer.start()

    def stop(self):
        self.logger.info("Stopping file watcher...")
        self._observer.stop()
        self._observer.join()


PLUGIN_REGISTRY = PluginRegistry()

def plugin(kind: PlugInKind, name: str, description: str = "", version: str = "0.1.0", safe: bool = True,
           source: str = "code", params_schema: Optional[Dict[str, Any]] = None, signature: Optional[str] = None,
           subscriptions: Optional[List[Union[str, Pattern, Tuple[Union[str, Pattern], Optional[Dict[str, Any]]]]]] = None):
    def decorator(fn: Callable):
        meta = PluginMeta(
            name=name,
            kind=kind.value,
            description=description,
            version=version,
            safe=safe,
            source=source,
            params_schema=params_schema if params_schema is not None else {},
            signature=signature
        )
        plugin_instance = Plugin(meta, fn, performance_tracker=PLUGIN_REGISTRY.performance_tracker)
        if subscriptions is not None:
            plugin_instance.subscriptions_to_register = subscriptions
        if PLUGIN_REGISTRY is None:
            logger.error(f"PLUGIN_REGISTRY is not initialized when plugin '{name}' is being registered.")
            return fn
        
        # Verify signature for plugins not sourced from 'engine' or 'local'
        if signature and source not in ['engine', 'local']:
            try:
                fn_code = inspect.getsource(fn) if callable(fn) else str(fn)
                if not verify_plugin_signature(fn_code.encode(), signature):
                    raise SecurityError("Plugin signature verification failed.")
            except (SecurityError, Exception) as e:
                logger.error(f"Plugin '{name}' failed signature verification: {e}")
                if PLUGIN_REGISTRY.audit_client:
                    asyncio.create_task(PLUGIN_REGISTRY.audit_client.add_entry_async(
                        "plugin_signature_failed", name,
                        {"error": str(e)},
                        error=str(e), agent_id="PluginRegistry"
                    ))
                return fn # Do not register the plugin

        PLUGIN_REGISTRY.register(kind.value, name, plugin_instance)
        logger.info(f"Plugin '{name}' registered via decorator to global registry.")
        if PLUGIN_REGISTRY.db:
            asyncio.create_task(PLUGIN_REGISTRY.db.save_plugin_legacy({
                'uuid': str(uuid.uuid4()),
                'name': name,
                'kind': kind.value,
                'version': version,
                'description': description,
                'safe': safe,
                'source': source,
                'params_schema': params_schema if params_schema is not None else {},
                'code': inspect.getsource(fn) if not isinstance(fn, str) else fn
            }))
        else:
            logger.warning(f"PluginRegistry DB not initialized. Plugin '{name}' metadata not persisted.")
        return fn
    return decorator

class PluginVersionManager:
    """Manages versioning of plugins, enabling A/B testing and rollback capabilities."""
    def __init__(self, registry: PluginRegistry, db: Database, audit_client: Optional[Any] = None):
        self.registry = registry
        self.db = db
        self.audit_client = audit_client
        self.logger = logging.getLogger("PluginVersionManager")
        self.versions: Dict[str, Dict[str, List[Plugin]]] = defaultdict(lambda: defaultdict(list))
        self.registry.version_manager = self # Inject self into the registry
        
    async def register_version(self, kind: str, name: str, plugin_instance: Plugin, version: str):
        """Register a specific version of a plugin."""
        try:
            kind_str = kind if isinstance(kind, str) else kind.value
            existing_versions = self.versions[kind_str][name]
            self.versions[kind_str][name] = [p for p in existing_versions if p.meta.version != version]
            self.versions[kind_str][name].append(plugin_instance)
            fn_code = inspect.getsource(plugin_instance.fn) if callable(plugin_instance.fn) else str(plugin_instance.fn)
            if self.db:
                await self.db.save_plugin_legacy({
                    'uuid': str(uuid.uuid4()),
                    'name': name,
                    'kind': kind_str,
                    'version': version,
                    'description': plugin_instance.meta.description,
                    'safe': plugin_instance.meta.safe,
                    'source': plugin_instance.meta.source,
                    'params_schema': plugin_instance.meta.params_schema,
                    'code': fn_code
                })
            else:
                self.logger.warning("Database not initialized. Cannot persist plugin version.")
            self.logger.info(f"Registered version {version} of plugin {kind_str}:{name}")
        except Exception as e:
            self.logger.error(f"Failed to register version {version} of plugin {name}: {e}", exc_info=True)
            if self.audit_client:
                await self.audit_client.add_entry_async(
                    "plugin_version_registration_failed", name,
                    {"version": version, "error": str(e), "traceback": traceback.format_exc()},
                    error=str(e), agent_id="PluginVersionManager"
                )
            raise
            
    async def get_version(self, kind: str, name: str, version: str) -> Optional[Plugin]:
        """Retrieve a specific plugin version."""
        try:
            kind_str = kind if isinstance(kind, str) else kind.value
            for plugin_inst in self.versions[kind_str][name]:
                if plugin_inst.meta.version == version:
                    return plugin_inst
            
            if not self.db:
                self.logger.warning("Database not initialized. Cannot retrieve plugins from DB.")
                return None
            
            plugin_data = await self.db.get_plugin_legacy(name=name, kind=kind_str) 
            if plugin_data and plugin_data.get('version') == version:
                meta = PluginMeta(
                    name=plugin_data['name'], kind=plugin_data['kind'], version=plugin_data['version'],
                    description=plugin_data.get('description', ''), safe=plugin_data.get('safe', True),
                    source=plugin_data.get('source', 'database'),
                    params_schema=plugin_data.get('params_schema', {})
                )
                
                loaded_fn = None
                if "code" in plugin_data and isinstance(plugin_data["code"], str):
                    temp_dir = Path(settings.PLUGIN_DIR) / ".temp"
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    
                    temp_module_name = f"dynamic_plugin_{uuid.uuid4().hex}"
                    temp_file_path = temp_dir / f"{temp_module_name}.py"
                    
                    try:
                        # Path traversal prevention for dynamic load
                        validated_path = validate_plugin_path(temp_file_path, Path(settings.PLUGIN_DIR))

                        with open(validated_path, "w") as f:
                            f.write(plugin_data["code"])
                        
                        spec = importlib.util.spec_from_file_location(temp_module_name, str(validated_path))
                        if spec and spec.loader:
                            module = importlib.util.module_from_spec(spec)
                            sys.modules[temp_module_name] = module
                            spec.loader.exec_module(module)
                            
                            if hasattr(module, 'execute') and callable(getattr(module, 'execute')):
                                loaded_fn = getattr(module, 'execute')
                            elif hasattr(module, name) and callable(getattr(module, name)):
                                loaded_fn = getattr(module, name)
                            else:
                                self.logger.warning(f"Dynamically loaded plugin {name}:{version} from DB has no 'execute' method or '{name}' function.")
                                loaded_fn = lambda *args, **kwargs: {"error": "Plugin function not found after dynamic load."}
                    except Exception as compile_err:
                        self.logger.error(f"Failed to dynamically load code for plugin {name}:{version}: {compile_err}", exc_info=True)
                        loaded_fn = lambda *args, **kwargs: {"error": f"Dynamic code load failed: {compile_err}"}
                    finally:
                        try:
                            if temp_file_path.exists():
                                os.remove(temp_file_path)
                        except OSError as cleanup_err:
                            self.logger.warning(f"Failed to clean up temporary file {temp_file_path}: {cleanup_err}")
                
                if loaded_fn is None:
                    loaded_fn = lambda *args, **kwargs: {"error": "Plugin function not available or load failed."}

                plugin_instance = Plugin(meta=meta, fn=loaded_fn, performance_tracker=self.registry.performance_tracker)
                if self.registry.message_bus and PluginMessageBusAdapter is not None:
                    plugin_instance.message_bus_adapter = PluginMessageBusAdapter(self.registry.message_bus, f"{kind_str}:{name}")
                    if hasattr(plugin_instance, 'subscriptions_to_register') and isinstance(plugin_instance.subscriptions_to_register, list):
                        for topic_info in plugin_instance.subscriptions_to_register:
                            topic_to_subscribe: Union[str, Pattern]
                            filter_obj: Optional[MessageFilter] = None
                            if isinstance(topic_info, tuple) and len(topic_info) == 2:
                                topic_to_subscribe = topic_info[0]
                                filter_dict = topic_info[1]
                                if filter_dict and isinstance(filter_dict, dict) and MessageFilter is not None:
                                    filter_obj = MessageFilter(lambda p, f=filter_dict: all(p.get(k) == v for k, v in f.items()))
                            elif isinstance(topic_info, (str, Pattern)):
                                topic_to_subscribe = topic_info
                            plugin_instance.message_bus_adapter.subscribe(topic_to_subscribe, plugin_instance.execute, filter=filter_obj)

                self.versions[kind_str][name].append(plugin_instance)
                return plugin_instance
            return None
        except Exception as e:
            self.logger.error(f"Failed to retrieve version {version} of plugin {name}: {e}", exc_info=True)
            if self.audit_client:
                await self.audit_client.add_entry_async(
                    "plugin_version_retrieval_failed", name,
                    {"version": version, "error": str(e), "traceback": traceback.format_exc()},
                    error=str(e), agent_id="PluginVersionManager"
                )
            return None
            
class PluginSelfOptimizer:
    """Enables plugins to self-optimize based on performance metrics."""
    def __init__(self, db: Database, version_manager: 'PluginVersionManager',
                 performance_tracker: Any, # Placeholder
                 code_proposal_manager: Any,
                 audit_client: Optional[Any] = None):
        self.db = db
        self.version_manager = version_manager
        self.performance_tracker = performance_tracker
        self.code_proposal_manager = code_proposal_manager
        self.audit_client = audit_client
        self.logger = logging.getLogger("PluginSelfOptimizer")
        
    async def optimize_plugin(self, kind: str, name: str, version: str, metrics: Dict):
        """Optimize a plugin if metrics indicate underperformance."""
        try:
            try:
                from arbiter.policy.core import PolicyEngine
            except Exception as e:
                self.logger.info(f"Policy engine unavailable; skipping optimization. {e}")
                return
            
            policy_engine = PolicyEngine(settings=settings)
            should_learn, policy_reason = await policy_engine.should_auto_learn(
                "PluginOptimization", "optimize_plugin", {"plugin_id": f"{kind}:{name}:{version}", "metrics": metrics}
            )
            if not should_learn:
                self.logger.info(f"Skipping optimization for {kind}:{name}:{version}: Policy does not permit. Reason: {policy_reason}. Current metrics: {metrics}")
                if self.audit_client:
                    await self.audit_client.add_entry_async(
                        "plugin_optimization_skipped", name,
                        {"version": version, "reason": policy_reason, "metrics": metrics},
                        agent_id="PluginSelfOptimizer"
                    )
                return
            optimization_threshold = getattr(settings, 'LOW_CONFIDENCE_THRESHOLD', 0.5) 
            if metrics.get('error_rate', 0) > optimization_threshold:
                self.logger.info(f"Plugin {kind}:{name}:{version} underperforming (error rate {metrics.get('error_rate', 0):.2f}). Proposing code change...")
                
                try:
                    proposed_code_stub = f"""
def new_optimized_function():
    # Placeholder for AI-generated code
    pass
                    """
                    new_version = f"{version}-optimized-{str(uuid.uuid4())[:8]}"
                    await self.version_manager.register_version(
                        kind=kind, name=name, 
                        plugin_instance=Plugin(PluginMeta(name=name, kind=kind, version=new_version), fn=proposed_code_stub, performance_tracker=self.performance_tracker),
                        version=new_version
                    )
                    self.logger.info(f"Successfully created a new plugin version '{new_version}' with AI-proposed changes.")
                    if self.audit_client:
                        await self.audit_client.add_entry_async(
                            "optimize_plugin_proposed_code_change", name,
                            {"version": version, "new_version": new_version, "metrics": metrics},
                            agent_id="PluginSelfOptimizer"
                        )
                except Exception as ai_gen_e:
                    self.logger.error(f"Failed to generate or register AI-proposed code for {kind}:{name}:{version}: {ai_gen_e}", exc_info=True)
                    if self.audit_client:
                        await self.audit_client.add_entry_async(
                            "optimize_plugin_failed_ai_gen", name,
                            {"version": version, "metrics": metrics, "error": str(ai_gen_e)},
                            error=str(ai_gen_e), agent_id="PluginSelfOptimizer"
                        )
            else:
                self.logger.debug(f"Plugin {kind}:{name}:{version} performing adequately. No optimization needed.")
        except Exception as e:
            self.logger.error(f"Failed to optimize plugin {kind}:{name}:{version}: {e}", exc_info=True)
            if self.audit_client:
                await self.audit_client.add_entry_async(
                    "optimize_plugin_failed_unexpected", name,
                    {"version": version, "error": str(e), "traceback": traceback.format_exc()},
                    error=str(e), agent_id="PluginSelfOptimizer"
                )
            raise

class PluginDependencyGraph:
    """Represents the dependency graph of plugins for orchestration and ordering."""
    def __init__(self, registry: 'PluginRegistry'):
        if NETWORKX_AVAILABLE:
            self.graph = nx.DiGraph()
        else:
            self.graph = defaultdict(set)
        self.logger = logging.getLogger("PluginDependencyGraph")
        self.is_networkx_available = NETWORKX_AVAILABLE

    def add_dependency(self, source: str, target: str):
        if self.is_networkx_available:
            self.graph.add_edge(source, target)
        else:
            self.graph[source].add(target)
        self.logger.debug(f"Added dependency: {source} -> {target}")

    def resolve_order(self) -> List[str]:
        if self.is_networkx_available:
            try:
                return list(nx.topological_sort(self.graph))
            except nx.NetworkXUnfeasible:
                self.logger.error("Cyclic dependency detected in plugin graph.")
                raise ValueError("Cyclic dependency detected.")
        else:
            graph_copy = {k: set(v) for k, v in self.graph.items()}
            nodes = set(graph_copy.keys()) | {n for vs in graph_copy.values() for n in vs}
            in_degree = defaultdict(int)
            for u, vs in graph_copy.items():
                for v in vs:
                    in_degree[v] += 1
            for n in nodes:
                in_degree.setdefault(n, 0)
            
            queue = [n for n in nodes if in_degree[n] == 0]
            resolved = []
            while queue:
                u = queue.pop(0)
                resolved.append(u)
                for v in sorted(list(graph_copy.get(u, []))):
                    in_degree[v] -= 1
                    if in_degree[v] == 0:
                        queue.append(v)
            
            if len(resolved) != len(nodes):
                self.logger.error("Cyclic dependency detected in plugin graph (simple fallback).")
                raise ValueError("Cyclic dependency detected.")
            
            return resolved
class PluginEventHandler(FileSystemEventHandler):
    """Handles file system events for automatic plugin loading/reloading."""
    def __init__(self, registry: 'PluginRegistry', directory: str):
        self.registry = registry
        self.directory = directory
        self.logger = logging.getLogger("PluginEventHandler")

    def on_modified(self, event):
        if event.is_directory: return
        if event.src_path.endswith('.py') and self.registry._loop:
            self.logger.info(f"File modified: {event.src_path}. Reloading plugin...")
            self.registry._loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self.registry.load_from_directory(self.directory))
            )

    def on_created(self, event):
        if event.is_directory: return
        if event.src_path.endswith('.py') and self.registry._loop:
            self.logger.info(f"File created: {event.src_path}. Loading new plugin...")
            self.registry._loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self.registry.load_from_directory(self.directory))
            )

    def on_deleted(self, event):
        if event.is_directory: return
        if event.src_path.endswith('.py'):
            self.logger.info(f"File deleted: {event.src_path}. Unloading plugin...")
            module_name = Path(event.src_path).stem
            self.logger.warning(f"Automatic plugin unregistration from file deletion is not implemented. Please manually restart if necessary: {module_name}")

class PluginRollbackHandler:
    def __init__(self, registry: 'PluginRegistry', version_manager: 'PluginVersionManager'):
        self.registry = registry
        self.version_manager = version_manager
        self.logger = logging.getLogger("PluginRollbackHandler")

    async def rollback_plugin(self, kind: str, name: str, target_version: str):
        self.logger.info(f"Initiating rollback for plugin {kind}:{name} to version {target_version}...")
        
        target_plugin = await self.version_manager.get_version(kind=kind, name=name, version=target_version)
        if not target_plugin:
            self.logger.error(f"Rollback failed: Version {target_version} of plugin {kind}:{name} not found.")
            raise ValueError("Target version not found.")
        
        current_plugin = self.registry.get(kind, name)
        if current_plugin:
            self.registry.unregister(kind, name)

        self.registry.register(kind, name, target_plugin)
        self.logger.info(f"Rollback of plugin {kind}:{name} to version {target_version} completed successfully.")

class PluginMarketplace:
    def __init__(self, db: Database, redis_client: Optional[Redis] = None, audit_client: Optional[Any] = None):
        self.db = db
        self.redis_client = redis_client
        self.audit_client = audit_client
        self.logger = logging.getLogger("PluginMarketplace")
        
    async def install_plugin(self, kind: str, name: str, version: str):
        self.logger.info(f"Installing plugin {kind}:{name}:{version}...")
        mock_plugin_code = f"def my_plugin_function(*args, **kwargs): return 'Hello from {name} v{version}'"
        
        try:
            if self.db:
                await self.db.save_plugin_legacy({
                    'uuid': str(uuid.uuid4()),
                    'name': name,
                    'kind': kind,
                    'version': version,
                    'description': f"Dynamically installed plugin: {name}",
                    'safe': True,
                    'source': 'marketplace',
                    'params_schema': {},
                    'code': mock_plugin_code
                })
                self.logger.info(f"Plugin {kind}:{name}:{version} installed successfully.")
            else:
                self.logger.warning("Database not initialized. Cannot install plugin.")
        except Exception as e:
            self.logger.error(f"Failed to install plugin {kind}:{name}:{version}: {e}", exc_info=True)
            if self.audit_client:
                await self.audit_client.add_entry_async(
                    "plugin_marketplace_install_failed", name,
                    {"version": version, "error": str(e)},
                    error=str(e), agent_id="PluginMarketplace"
                )
            raise

    async def rate_plugin(self, kind: str, name: str, version: str, rating: int, comment: Optional[str], user_id: str):
        self.logger.info(f"User {user_id} rating plugin {kind}:{name}:{version} with {rating} stars.")
        
        try:
            if not (1 <= rating <= 5):
                raise ValueError("Rating must be between 1 and 5.")
            
            if self.db:
                rating_key = f"plugin_rating:{kind}:{name}:{version}:{user_id}"
                await self.db.save_preferences(
                    user_id=rating_key,
                    prefs={"rating": rating, "comment": comment, "timestamp": datetime.utcnow().isoformat()},
                    encrypt=True
                )
                self.logger.info(f"Rating for plugin {kind}:{name}:{version} saved successfully.")
            else:
                self.logger.warning("Database not initialized. Cannot save plugin rating.")
        except Exception as e:
            self.logger.error(f"Failed to rate plugin {kind}:{name}:{version}: {e}", exc_info=True)
            if self.audit_client:
                await self.audit_client.add_entry_async(
                    "plugin_marketplace_rating_failed", name,
                    {"version": version, "rating": rating, "error": str(e)},
                    error=str(e), agent_id="PluginMarketplace"
                )
            raise

__all__ = [
    "PluginRegistry",
    "PluginMeta",
    "Plugin",
    "PlugInKind",
    "PLUGIN_REGISTRY",
    "plugin",
    "PluginVersionManager",
    "PluginSelfOptimizer",
    "PluginDependencyGraph",
    "PluginEventHandler",
    "PluginRollbackHandler",
    "PluginMarketplace",
    "PluginPerformanceTracker",
    "PluginWatcher"
]