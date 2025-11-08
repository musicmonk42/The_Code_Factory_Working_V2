# runner/llm_plugin_manager.py
"""
WORLD-CLASS LLM PLUGIN MANAGER (2025 Production Edition)
"""

import importlib.util
import asyncio
import logging
import hashlib
import json
from pathlib import Path
from dynaconf import Dynaconf, Validator
from typing import Dict, Any, Optional, List
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import os # [NEW] Added missing import
import sys # [NEW] Added missing import
import tempfile # [FIX] Added for test default dir
import contextlib # [FIX] Added missing import for close()

# [FIX] Import logger from runner.runner_logging
try:
    from .runner_logging import logger
except ImportError:
    logger = logging.getLogger(__name__)
    logger.warning("Failed to import runner.runner_logging, using standard logger.")


# --- Configuration (Dynaconf) ---
# [FIX] Replaced entire settings block with the provided patch
TESTING = (
    os.getenv("TESTING") == "1"
    or "pytest" in sys.modules
    or os.getenv("PYTEST_CURRENT_TEST") is not None
    or os.getenv("PYTEST_ADDOPTS") is not None
)

REQUIRED_VALIDATORS = [
    Validator("PLUGIN_DIR", must_exist=True, is_type_of=str),
    Validator("HASH_MANIFEST", default="", is_type_of=str),
    Validator("AUTO_RELOAD", default=False, is_type_of=bool),
]
if TESTING:
    settings = Dynaconf(env="main", environments=True, preload=[], settings_files=[])
    if not settings.get("PLUGIN_DIR"):
        settings.set("PLUGIN_DIR", str(Path(tempfile.gettempdir()) / "plugins"))
else:
    settings = Dynaconf(
        env="main", environments=True, preload=[], settings_files=[],
        validators=REQUIRED_VALIDATORS
    )
# [FIX] End of patch

# --- Metrics (shared with runner foundation) ---
try:
    from prometheus_client import Counter, Gauge
    # [FIX] Import LLM_PROVIDER_HEALTH from runner_metrics
    from runner.runner_metrics import LLM_PROVIDER_HEALTH
except ImportError:
    logger.warning("prometheus_client or runner_metrics not found. Using dummy metrics.")
    class DummyCounter:
        def labels(self, *args, **kwargs): return self
        def inc(self, *args, **kwargs): pass
    class DummyGauge:
        def labels(self, *args, **kwargs): return self
        def set(self, *args, **kwargs): pass
    Counter = DummyCounter
    Gauge = DummyGauge
    LLM_PROVIDER_HEALTH = DummyGauge()


PLUGIN_LOADS = Counter("llm_plugin_loads_total", "Plugin load attempts", ["plugin"])
PLUGIN_ERRORS = Counter("llm_plugin_errors_total", "Plugin load failures", ["plugin", "error_type"])

# [FIX] This shadows the logger imported from runner_logging.
# We should use the imported `logger` instance, not create a new one.
# logger = logging.getLogger(__name__)

# --- Errors ---
class PluginError(Exception): pass
class PluginIntegrityError(PluginError): pass
class PluginValidationError(PluginError): pass

# --- Auto-Reload Handler ---
class PluginReloader(FileSystemEventHandler):
    def __init__(self, manager: 'LLMPluginManager'): 
        self.manager = manager
        self.loop = manager.loop
        self.queue = manager.reload_queue

    def on_modified(self, event):
        if event.src_path.endswith(".py"):
            logger.debug(f"Watchdog detected modification: {event.src_path}")
            # [FIX] Do not call create_task from watchdog thread.
            # Put a message on the queue. This is thread-safe.
            try:
                self.loop.call_soon_threadsafe(self.queue.put_nowait, event)
            except Exception as e:
                logger.error(f"Failed to enqueue reload event from watchdog thread: {e}")

# --- Manager ---
class LLMPluginManager:
    def __init__(self, plugin_dir: Optional[Path | str] = None):
        self.plugin_dir = Path(plugin_dir or settings.PLUGIN_DIR)
        self.registry: Dict[str, Any] = {}
        self._loaded_modules: Dict[str, Any] = {}
        self.lock = asyncio.Lock()
        self._watcher: Optional[Observer] = None
        self._manifest: dict[str, str] = {}
        
        # [NEW] Async queue for reload events
        self.reload_queue: asyncio.Queue = asyncio.Queue()
        self._watcher_consumer_task: Optional[asyncio.Task] = None
        
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("LLMPluginManager initialized without a running event loop. Auto-reload will not work.")
            self.loop = asyncio.new_event_loop() # Fallback, though likely problematic
            asyncio.set_event_loop(self.loop)

        if settings.HASH_MANIFEST:
            self._load_manifest() 

        # [FIX] Don't start watcher here, start it in an async `start` method
        
        self._load_task = asyncio.create_task(self._scan_and_load_plugins())

    async def start(self):
        """Starts the plugin manager's background tasks (watcher and reloader)."""
        if settings.AUTO_RELOAD:
            self._start_watcher()
            if self._watcher_consumer_task is None or self._watcher_consumer_task.done():
                self._watcher_consumer_task = asyncio.create_task(self._watcher_consumer())

    def _start_watcher(self):
        if self._watcher:
            return # Already started
        handler = PluginReloader(self)
        self._watcher = Observer()
        self._watcher.schedule(handler, str(self.plugin_dir), recursive=False)
        self._watcher.start()
        logger.info(f"LLM Plugin auto-reload enabled, watching {self.plugin_dir}")
    
    async def _watcher_consumer(self):
        """Consumes events from the watchdog queue and triggers reloads."""
        logger.info("Starting LLM plugin watcher consumer task.")
        while True:
            try:
                event = await self.reload_queue.get()
                logger.info(f"Reload consumer received event for: {event.src_path}. Triggering reload.")
                await self.reload()
                self.reload_queue.task_done()
            except asyncio.CancelledError:
                logger.info("LLM plugin watcher consumer task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in LLM plugin watcher consumer: {e}", exc_info=True)
        
    def _load_manifest(self) -> None:
        """Load optional SHA-256 manifest for integrity checks."""
        path = Path(settings.HASH_MANIFEST)
        if not path.is_file():
            logger.warning("Hash manifest %s not found – integrity checks disabled.", path)
            return
        try:
            self._manifest = json.loads(path.read_text())
        except Exception as exc:
            logger.error("Failed to parse hash manifest: %s", exc)

    async def _verify_integrity(self, filepath: Path, expected_hash: str) -> bool:
        if not expected_hash or expected_hash == "INTEGRITY_CHECK_DISABLED":
            logger.warning(f"Integrity check disabled for {filepath.name}")
            return True
        
        # Use manifest if available, otherwise use the (now deprecated) expected_hash param
        if self._manifest:
            expected_hash = self._manifest.get(filepath.name, expected_hash)
            if not expected_hash:
                 logger.error(f"Hash for {filepath.name} not found in manifest. Denying load.")
                 return False

        def compute():
            h = hashlib.sha256()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        
        computed_hash = await asyncio.to_thread(compute)
        return computed_hash == expected_hash

    def _get_expected_hash(self, modname: str) -> str:
        """
        Loads the expected hash from the legacy manifest file.
        This is kept for compatibility with the _scan_and_load_plugins logic.
        """
        # [FIX] Use self.plugin_dir, not just the dynaconf setting
        manifest_path = self.plugin_dir / "plugin_hash_manifest.json"
        if not manifest_path.exists():
            logger.warning(f"Legacy plugin_hash_manifest.json missing at {manifest_path}. Integrity check may be skipped.")
            return "INTEGRITY_CHECK_DISABLED"
        
        try:
            return json.loads(manifest_path.read_text()).get(modname, "INTEGRITY_CHECK_DISABLED")
        except Exception as e:
            logger.error(f"Failed to read or parse legacy manifest {manifest_path}: {e}")
            return "INTETEGRITY_CHECK_DISABLED"


    async def _scan_and_load_plugins(self):
        if not self.plugin_dir.exists():
            logger.warning(f"Plugin dir {self.plugin_dir} not found")
            return

        async with self.lock:
            for py_file in self.plugin_dir.glob("*_provider.py"):
                if py_file.name.startswith("_"): continue
                modname = py_file.stem

                try:
                    # NOTE: This call to _get_expected_hash() is for the legacy
                    # manifest check. The _verify_integrity function will
                    # prioritize the new Dynaconf-based manifest if it exists.
                    expected_hash = self._get_expected_hash(modname) 
                    if not await self._verify_integrity(py_file, expected_hash):
                        raise PluginIntegrityError(f"Hash mismatch: {py_file.name}")

                    if modname in sys.modules:
                        del sys.modules[modname]

                    spec = importlib.util.spec_from_file_location(modname, py_file)
                    if not spec or not spec.loader:
                        raise PluginError("Invalid module spec")

                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[modname] = mod
                    await asyncio.to_thread(spec.loader.exec_module, mod)
                    self._loaded_modules[modname] = mod

                    if not hasattr(mod, "get_provider"):
                        raise PluginValidationError("Missing get_provider()")

                    provider = mod.get_provider()
                    name = getattr(provider, "name", modname).lower()

                    for method in ["call", "health_check"]:
                        attr = getattr(provider, method, None)
                        if not attr or not asyncio.iscoroutinefunction(attr):
                            raise PluginValidationError(f"Missing async {method}()")

                    self.registry[name] = provider
                    PLUGIN_LOADS.labels(plugin=name).inc()
                    LLM_PROVIDER_HEALTH.labels(provider=name).set(1)
                    logger.info(f"Loaded LLM provider: {name}")

                except Exception as e:
                    error_type = type(e).__name__
                    PLUGIN_ERRORS.labels(plugin=modname, error_type=error_type).inc()
                    logger.error(f"Failed to load {py_file.name}: {e}", exc_info=True)
                    for d in [self._loaded_modules, self.registry, sys.modules]:
                        d.pop(modname, None)

    async def reload(self):
        async with self.lock:
            for name in list(self.registry.keys()):
                LLM_PROVIDER_HEALTH.labels(provider=name).set(0)
            self.registry.clear()
            self._loaded_modules.clear()
            for mod in list(sys.modules.keys()):
                if mod.endswith("_provider"):
                    sys.modules.pop(mod, None)
            await self._scan_and_load_plugins()
            logger.info("LLM plugins reloaded")

    def get_provider(self, name: str) -> Optional[Any]:
        return self.registry.get(name.lower())

    def list_providers(self) -> List[str]:
        return list(self.registry.keys())

    async def close(self):
        if self._watcher:
            self._watcher.stop()
            self._watcher.join()
        
        if self._watcher_consumer_task and not self._watcher_consumer_task.done():
            self._watcher_consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher_consumer_task
        
        logger.info("LLM Plugin Manager shut down.")