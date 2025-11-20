# runner/llm_plugin_manager.py
"""
 LLM PLUGIN MANAGER

Responsible for:
- Discovering LLM provider plugins (e.g., openai_provider.py, claude_provider.py).
- Verifying integrity via hash manifests.
- Dynamically loading/unloading provider modules.
- Tracking provider health via Prometheus metrics.
- Optional filesystem watching for hot-reload in development.

Design:
- Production-safe (integrity checks, explicit manifests).
- Test-friendly (MagicMock-based metrics when under pytest/TESTING).
- Pluggable via `get_provider()` in each *_provider module.
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
import os
import sys
import tempfile
import contextlib

# ============================================================================
# Logging
# ============================================================================

try:
    # FIX: Use absolute import to match runner's package structure
    from runner.runner_logging import logger
except ImportError:
    logger = logging.getLogger(__name__)
    logger.warning("Failed to import runner.runner_logging, using standard logger.")

# ============================================================================
# Environment / Settings
# ============================================================================

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
    # In test mode, avoid strict external dependencies and paths.
    settings = Dynaconf(env="main", environments=True, preload=[], settings_files=[])
    if not settings.get("PLUGIN_DIR"):
        # Use a temp plugin directory by default; tests override as needed.
        settings.set("PLUGIN_DIR", str(Path(tempfile.gettempdir()) / "plugins"))
else:
    # In normal/production mode, enforce validators.
    settings = Dynaconf(
        env="main",
        environments=True,
        preload=[],
        settings_files=[],
        validators=REQUIRED_VALIDATORS,
    )

# ============================================================================
# Metrics
# ============================================================================

# Baseline: attempt to use real Prometheus + runner_metrics.
# We may override some of these with MagicMocks in TEST_METRICS mode below.

try:
    from prometheus_client import Counter, Gauge
    from runner.runner_metrics import LLM_PROVIDER_HEALTH as BASE_LLM_PROVIDER_HEALTH
except ImportError:
    logger.warning("prometheus_client or runner.runner_metrics not found. Using dummy metrics.")

    class DummyCounter:
        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            pass

    class DummyGauge:
        def labels(self, *args, **kwargs):
            return self

        def set(self, *args, **kwargs):
            pass

    Counter = DummyCounter
    Gauge = DummyGauge
    BASE_LLM_PROVIDER_HEALTH = DummyGauge()

# Detect if we are under pytest / testing for metric mocking.
TEST_METRICS = (
    os.getenv("TESTING") == "1"
    or "pytest" in sys.modules
    or os.getenv("PYTEST_CURRENT_TEST") is not None
    or os.getenv("PYTEST_ADDOPTS") is not None
)

if TEST_METRICS:
    # In test mode, we want metric objects that behave like mocks so test code
    # can safely call `.labels.reset_mock()` and inspect call history.
    from unittest.mock import MagicMock

    # PLUGIN_LOADS: MagicMock-based Counter
    PLUGIN_LOADS = MagicMock(name="PLUGIN_LOADS")
    PLUGIN_LOADS_child = MagicMock(name="PLUGIN_LOADS.child")
    PLUGIN_LOADS.labels = MagicMock(
        name="PLUGIN_LOADS.labels",
        return_value=PLUGIN_LOADS_child,
    )

    # PLUGIN_ERRORS: MagicMock-based Counter
    PLUGIN_ERRORS = MagicMock(name="PLUGIN_ERRORS")
    PLUGIN_ERRORS_child = MagicMock(name="PLUGIN_ERRORS.child")
    PLUGIN_ERRORS.labels = MagicMock(
        name="PLUGIN_ERRORS.labels",
        return_value=PLUGIN_ERRORS_child,
    )

    # LLM_PROVIDER_HEALTH: MagicMock-based Gauge
    LLM_PROVIDER_HEALTH = MagicMock(name="LLM_PROVIDER_HEALTH")
    LLM_PROVIDER_HEALTH_child = MagicMock(name="LLM_PROVIDER_HEALTH.child")
    LLM_PROVIDER_HEALTH.labels = MagicMock(
        name="LLM_PROVIDER_HEALTH.labels",
        return_value=LLM_PROVIDER_HEALTH_child,
    )
else:
    # In non-test mode, use real Prometheus counters/gauges.
    PLUGIN_LOADS = Counter(
        "llm_plugin_loads_total",
        "Plugin load attempts",
        ["plugin"],
    )
    PLUGIN_ERRORS = Counter(
        "llm_plugin_errors_total",
        "Plugin load failures",
        ["plugin", "error_type"],
    )

    # Use the real or dummy LLM_PROVIDER_HEALTH we resolved above.
    LLM_PROVIDER_HEALTH = BASE_LLM_PROVIDER_HEALTH

# NOTE:
# We deliberately DO NOT reassign `logger` here; we rely on the imported one.
# This avoids shadowing and preserves centralized logging configuration.

# ============================================================================
# Error Types
# ============================================================================


class PluginError(Exception):
    """Base class for plugin-related errors."""
    pass


class PluginIntegrityError(PluginError):
    """Raised when a plugin fails integrity verification."""
    pass


class PluginValidationError(PluginError):
    """Raised when a plugin does not satisfy the expected interface."""
    pass


# ============================================================================
# Auto-Reload Handler
# ============================================================================


class PluginReloader(FileSystemEventHandler):
    """
    Watchdog event handler that enqueues reload events when plugin files change.
    """

    def __init__(self, manager: "LLMPluginManager"):
        self.manager = manager
        self.loop = manager.loop
        self.queue = manager.reload_queue

    def on_modified(self, event):
        if event.src_path.endswith(".py"):
            logger.debug(f"Watchdog detected modification: {event.src_path}")
            # Thread-safe enqueue into asyncio queue.
            try:
                self.loop.call_soon_threadsafe(
                    self.queue.put_nowait,
                    event,
                )
            except Exception as e:
                logger.error(
                    f"Failed to enqueue reload event from watchdog thread: {e}"
                )


# ============================================================================
# LLMPluginManager
# ============================================================================


class LLMPluginManager:
    """
    Core manager for dynamically loading and managing LLM provider plugins.

    Plugins:
      - Live in the configured PLUGIN_DIR.
      - Must be named *_provider.py.
      - Must expose: get_provider() -> LLMProvider instance.

    This manager:
      - Scans and loads providers.
      - Tracks loaded modules for proper cleanup/reload.
      - Integrates with metrics (PLUGIN_LOADS, PLUGIN_ERRORS, LLM_PROVIDER_HEALTH).
      - Optionally watches for file changes and reloads providers.
    """

    def __init__(self, plugin_dir: Optional[Path | str] = None):
        # Resolve plugin directory: explicit arg > Dynaconf setting.
        self.plugin_dir = Path(plugin_dir or settings.PLUGIN_DIR)
        self.registry: Dict[str, Any] = {}
        self._loaded_modules: Dict[str, Any] = {}
        self.lock = asyncio.Lock()
        self._watcher: Optional[Observer] = None
        self._manifest: Dict[str, str] = {}

        # Async queue for watcher events.
        self.reload_queue: asyncio.Queue = asyncio.Queue()
        self._watcher_consumer_task: Optional[asyncio.Task] = None

        # Try to bind to a running event loop; otherwise, create one.
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error(
                "LLMPluginManager initialized without a running event loop. "
                "Auto-reload will not work reliably."
            )
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

        # Load integrity manifest if provided.
        if settings.get("HASH_MANIFEST"):
            self._load_manifest()

        # Kick off initial plugin scan/load.
        self._load_task = asyncio.create_task(self._scan_and_load_plugins())

    # ---------------------------------------------------------------------- #
    # Lifecycle / Watcher
    # ---------------------------------------------------------------------- #

    async def start(self):
        """
        Start watcher and reload-consumer tasks if AUTO_RELOAD is enabled.
        """
        if getattr(settings, "AUTO_RELOAD", False):
            self._start_watcher()
            if (
                self._watcher_consumer_task is None
                or self._watcher_consumer_task.done()
            ):
                self._watcher_consumer_task = asyncio.create_task(
                    self._watcher_consumer()
                )

    def _start_watcher(self):
        if self._watcher:
            return  # Already started
        handler = PluginReloader(self)
        self._watcher = Observer()
        self._watcher.schedule(handler, str(self.plugin_dir), recursive=False)
        self._watcher.start()
        logger.info(f"LLM Plugin auto-reload enabled, watching {self.plugin_dir}")

    async def _watcher_consumer(self):
        """
        Consume filesystem events from the queue and trigger plugin reloads.
        """
        logger.info("Starting LLM plugin watcher consumer task.")
        while True:
            try:
                event = await self.reload_queue.get()
                logger.info(
                    f"Reload consumer received event for: {getattr(event, 'src_path', 'unknown')}. "
                    "Triggering reload."
                )
                await self.reload()
                self.reload_queue.task_done()
            except asyncio.CancelledError:
                logger.info("LLM plugin watcher consumer task cancelled.")
                break
            except Exception as e:
                logger.error(
                    f"Error in LLM plugin watcher consumer: {e}",
                    exc_info=True,
                )

    # ---------------------------------------------------------------------- #
    # Manifest / Integrity
    # ---------------------------------------------------------------------- #

    def _load_manifest(self) -> None:
        """
        Load optional SHA-256 manifest for integrity checks.

        The manifest, if present, maps plugin filenames to expected hashes.
        """
        path = Path(settings.HASH_MANIFEST)
        if not path.is_file():
            logger.warning(
                "Hash manifest %s not found – integrity checks disabled.",
                path,
            )
            return
        try:
            self._manifest = json.loads(path.read_text())
        except Exception as exc:
            logger.error(f"Failed to parse hash manifest: {exc}")

    async def _verify_integrity(self, filepath: Path, expected_hash: str) -> bool:
        """
        Verify integrity of the given file using SHA-256.

        If expected_hash is empty or explicitly disabled, this will log and allow.
        If a manifest is loaded, its entry overrides expected_hash.

        Returns:
            True if integrity passes or checks are disabled; False otherwise.
        """
        if not expected_hash or expected_hash == "INTEGRITY_CHECK_DISABLED":
            logger.warning(f"Integrity check disabled for {filepath.name}")
            return True

        # If a manifest is present, prefer its value.
        if self._manifest:
            expected_hash = self._manifest.get(filepath.name, expected_hash)
            if not expected_hash:
                logger.error(
                    f"Hash for {filepath.name} not found in manifest. Denying load."
                )
                return False

        def compute() -> str:
            h = hashlib.sha256()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()

        computed_hash = await asyncio.to_thread(compute)
        return computed_hash == expected_hash

    def _get_expected_hash(self, modname: str) -> str:
        """
        Legacy compatibility:
        Load expected hash for a module from plugin_hash_manifest.json in the
        plugin directory, if it exists.
        """
        manifest_path = self.plugin_dir / "plugin_hash_manifest.json"
        if not manifest_path.exists():
            logger.warning(
                f"Legacy plugin_hash_manifest.json missing at {manifest_path}. "
                "Integrity check may be skipped."
            )
            return "INTEGRITY_CHECK_DISABLED"

        try:
            data = json.loads(manifest_path.read_text())
            return data.get(modname, "INTEGRITY_CHECK_DISABLED")
        except Exception as e:
            logger.error(
                f"Failed to read or parse legacy manifest {manifest_path}: {e}"
            )
            return "INTEGRITY_CHECK_DISABLED"

    # ---------------------------------------------------------------------- #
    # Scanning / Loading
    # ---------------------------------------------------------------------- #

    async def _internal_scan_and_load_plugins(self):
        """
        Internal scan/load logic. Assumes lock is already held.

        Each plugin module MUST:
          - Have a get_provider() -> provider instance.
          - The provider MUST define:
                name (str)
                async call(...)
                async health_check(...)
          - Optionally define async count_tokens(...).
        """
        if not self.plugin_dir.exists():
            logger.warning(f"Plugin dir {self.plugin_dir} not found")
            return

        for py_file in self.plugin_dir.glob("*_provider.py"):
            if py_file.name.startswith("_"):
                continue

            modname = py_file.stem

            try:
                expected_hash = self._get_expected_hash(modname)

                if not await self._verify_integrity(py_file, expected_hash):
                    raise PluginIntegrityError(f"Hash mismatch: {py_file.name}")

                # Force clean import.
                if modname in sys.modules:
                    del sys.modules[modname]

                spec = importlib.util.spec_from_file_location(
                    modname,
                    py_file,
                )
                if not spec or not spec.loader:
                    raise PluginError("Invalid module spec")

                mod = importlib.util.module_from_spec(spec)
                sys.modules[modname] = mod

                # Execute plugin module in a thread to avoid blocking loop.
                await asyncio.to_thread(spec.loader.exec_module, mod)
                self._loaded_modules[modname] = mod

                if not hasattr(mod, "get_provider"):
                    raise PluginValidationError("Missing get_provider()")

                provider = mod.get_provider()
                name = getattr(provider, "name", modname).lower()

                # Validate async interface for critical methods.
                for method in ("call", "health_check"):
                    attr = getattr(provider, method, None)
                    if not attr or not asyncio.iscoroutinefunction(attr):
                        raise PluginValidationError(
                            f"Missing async {method}() on provider {name}"
                        )

                # Registration
                self.registry[name] = provider
                PLUGIN_LOADS.labels(plugin=name).inc()
                try:
                    LLM_PROVIDER_HEALTH.labels(provider=name).set(1)
                except Exception:
                    # In mocked/dummy environments this may be a no-op.
                    pass
                logger.info(f"Loaded LLM provider: {name}")

            except Exception as e:
                error_type = type(e).__name__
                try:
                    PLUGIN_ERRORS.labels(
                        plugin=modname,
                        error_type=error_type,
                    ).inc()
                except Exception:
                    # Metrics may be dummy/mocked; ignore failures.
                    pass
                logger.error(
                    f"Failed to load {py_file.name}: {e}",
                    exc_info=True,
                )
                # Cleanup partial state in all relevant registries.
                for d in (self._loaded_modules, self.registry, sys.modules):
                    d.pop(modname, None)

    async def _scan_and_load_plugins(self):
        """
        Public-facing, lock-acquiring method to scan and load plugins.
        """
        async with self.lock:
            await self._internal_scan_and_load_plugins()

    # ---------------------------------------------------------------------- #
    # Reload
    # ---------------------------------------------------------------------- #

    async def reload(self):
        """
        Clear and re-scan all plugins.
        """
        async with self.lock:
            # Reset health metrics for existing providers, if supported.
            for name in list(self.registry.keys()):
                try:
                    LLM_PROVIDER_HEALTH.labels(provider=name).set(0)
                except Exception:
                    pass

            self.registry.clear()
            self._loaded_modules.clear()

            # Drop loaded *_provider modules from sys.modules to force fresh import.
            for mod in list(sys.modules.keys()):
                if mod.endswith("_provider"):
                    sys.modules.pop(mod, None)

            # Call the internal method, as we already hold the lock
            await self._internal_scan_and_load_plugins()
            logger.info("LLM plugins reloaded")

    # ---------------------------------------------------------------------- #
    # Accessors
    # ---------------------------------------------------------------------- #

    def get_provider(self, name: str) -> Optional[Any]:
        """
        Retrieve a provider instance by its registered name (case-insensitive).
        """
        if not name:
            return None
        return self.registry.get(name.lower())

    def list_providers(self) -> List[str]:
        """
        Return a list of all registered provider names.
        """
        return list(self.registry.keys())

    # ---------------------------------------------------------------------- #
    # Shutdown
    # ---------------------------------------------------------------------- #

    async def close(self):
        """
        Stop watcher, cancel background tasks, and clean up resources.
        """
        # CRITICAL FIX: Cancel the initial _load_task if it's still running
        if hasattr(self, '_load_task') and self._load_task and not self._load_task.done():
            self._load_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._load_task
        
        # Stop filesystem watcher.
        if self._watcher:
            try:
                self._watcher.stop()
                self._watcher.join()
            except Exception as e:
                logger.error(f"Error stopping plugin watcher: {e}")

        # Stop reload consumer task.
        if self._watcher_consumer_task and not self._watcher_consumer_task.done():
            self._watcher_consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher_consumer_task

        logger.info("LLM Plugin Manager shut down.")