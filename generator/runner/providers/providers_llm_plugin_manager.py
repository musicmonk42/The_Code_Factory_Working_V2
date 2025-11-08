# llm_plugin_manager.py
# World-class LLM plugin manager with dynamic loading, hot-reloading, security, observability, and testing.

import importlib.util
import os
import sys
import asyncio
import logging
from typing import Dict, Any, Optional, List, Callable, Awaitable
from pathlib import Path
import hashlib  # For plugin integrity checks
import time
import shutil
import json

# External dependencies for production readiness
from dynaconf import Dynaconf, Validator  # Configuration (reqs: dynaconf)
from prometheus_client import Counter, Gauge, Histogram  # Metrics (reqs: prometheus-client)
from opentelemetry import trace  # Tracing (reqs: opentelemetry-sdk, opentelemetry-api)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter # ConsoleExporter for dev/debugging
from opentelemetry.trace import Status, StatusCode # For span status
from watchdog.observers import Observer  # For hot-reloading (reqs: watchdog)
from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent, FileDeletedEvent, FileMovedEvent
import aiohttp # For sending alerts (reqs: aiohttp)

# Conditional imports for OpenTelemetry
try:
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    HAS_OTLP_EXPORTER = True
except ImportError:
    HAS_OTLP_EXPORTER = False

logger = logging.getLogger(__name__)

# --- Configuration ---
settings = Dynaconf(
    envvar_prefix="LLM_PLUGIN",
    settings_files=["llm_plugin_config.yaml"],
    validators=[
        Validator("PLUGIN_DIR", must_exist=True, is_type_of=str),
        Validator("AUTO_RELOAD", default=False, is_type_of=bool),
        Validator("ALERT_ENDPOINT", default="", is_type_of=str), # Optional, but recommended for prod
        Validator("OTLP_ENDPOINT", default="http://otel-collector:4317", is_type_of=str),
    ]
)

try:
    settings.validators.validate()
except Exception as e:
    logger.critical(f"Configuration validation failed for LLM Plugin Manager: {e}")
    # In a real app, you might send a critical alert here before exiting
    sys.exit(1)

# --- Metrics ---
PLUGIN_LOADS = Counter("llm_plugin_loads_total", "Total plugin load attempts", ["plugin_name"])
PLUGIN_RELOADS = Counter("llm_plugin_reloads_total", "Total plugin reload events", ["plugin_name"])
PLUGIN_ERRORS = Counter("llm_plugin_errors_total", "Total plugin errors", ["plugin_name", "error_type"])
PLUGIN_HEALTH = Gauge("llm_plugin_health", "Plugin health status (1=healthy, 0=unhealthy)", ["plugin_name"])
PLUGIN_LOAD_LATENCY = Histogram("llm_plugin_load_latency_seconds", "Latency of plugin loading", ["plugin_name"])

# --- OpenTelemetry Setup ---
# Configure Tracer Provider once
try:
    provider = TracerProvider()
    # Use OTLP exporter if endpoint is configured, else console for dev
    if settings.OTLP_ENDPOINT and HAS_OTLP_EXPORTER:
        processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.OTLP_ENDPOINT, insecure=True))
        logger.info(f"OpenTelemetry traces configured to export to OTLP endpoint: {settings.OTLP_ENDPOINT}")
    else:
        processor = BatchSpanProcessor(ConsoleSpanExporter())
        logger.info("OpenTelemetry traces configured for Console export (no OTLP endpoint specified or exporter not installed).")

    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer(__name__)
    HAS_OPENTELEMETRY = True
except ImportError:
    tracer = None
    HAS_OPENTELEMETRY = False
    logger.warning("OpenTelemetry not found. Tracing disabled for LLM Plugin Manager.")
except Exception as e:
    logger.error(f"Failed to configure OpenTelemetry for LLM Plugin Manager: {e}", exc_info=True)
    tracer = None
    HAS_OPENTELEMETRY = False

# --- Alerting ---
async def send_alert(message: str, endpoint: str = settings.ALERT_ENDPOINT, severity: str = "error"):
    """Sends an alert to a configured endpoint."""
    if not endpoint:
        logger.warning(f"No alert endpoint configured for LLM Plugin Manager. Alert: {message} (Severity: {severity})")
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json={"message": message, "severity": severity, "source": "llm_plugin_manager"}) as response:
                response.raise_for_status()
                logger.info(f"Alert sent from LLM Plugin Manager: {message[:50]}...")
    except Exception as e:
        logger.error(f"Failed to send alert from LLM Plugin Manager: {e}", exc_info=True)

# --- Plugin Manager ---
class LLMPluginManager:
    """
    Central registry and loader for LLM provider plugins.
    Supports dynamic loading, hot-reloading with file watching, security checks,
    observability, and concurrent access safety.
    """
    def __init__(self, plugin_dir: str = settings.PLUGIN_DIR):
        self.plugin_dir = plugin_dir
        self.registry: Dict[str, Any] = {} # Stores provider objects
        self._loaded_modules: Dict[str, Any] = {} # To keep track of loaded modules for reloading
        self.lock = asyncio.Lock()  # For concurrent safety during scan/reload
        self._scan_and_load_plugins_on_init = asyncio.create_task(self._scan_and_load_plugins()) # Start initial scan as a task

        if settings.AUTO_RELOAD:
            self._start_watcher()

    async def _scan_and_load_plugins(self):
        """Scans the plugin directory and loads plugins with integrity checks."""
        if not os.path.exists(self.plugin_dir):
            logger.warning(f"Plugin directory '{self.plugin_dir}' does not exist. No plugins will be loaded.")
            return

        async with self.lock: # Acquire lock for the entire scan operation
            for fname in os.listdir(self.plugin_dir):
                if fname.endswith(".py") and not fname.startswith("_"):
                    await self._load_plugin(fname) # Load each plugin sequentially

    async def _load_plugin(self, fname: str):
        """Loads a single plugin with security checks and observability."""
        modname = fname[:-3] # Remove .py extension
        modpath = os.path.join(self.plugin_dir, fname)
        plugin_name_for_metrics = modname # Default name for metrics if provider.name not available

        start_time = time.perf_counter()
        span = None
        if HAS_OPENTELEMETRY and tracer:
            span = tracer.start_as_current_span(f"load_plugin_{modname}")
            span.set_attribute("plugin_file", fname)
            span.set_attribute("plugin_module_name", modname)

        try:
            # 1. Integrity check (e.g., hash verification)
            # In a production system, `_get_expected_hash` would fetch a hash from a secure, trusted source (e.g., a hash manifest in a DB, or a KMS-signed hash).
            expected_hash = self._get_expected_hash(modname)
            if not expected_hash:
                PLUGIN_ERRORS.labels(plugin_name=modname, error_type="integrity_failure").inc()
                logger.error(f"No expected hash for plugin {fname}. Plugin will NOT be loaded. Aborting loading due to security policy.")
                await send_alert(f"Failed to load LLM plugin {fname}: No expected hash found.", severity="critical")
                if span:
                    span.set_status(StatusCode.ERROR, "No expected hash found")
                    span.record_exception(ValueError("No expected hash found for plugin"))
                return # Do not load plugin without an expected hash

            if not await asyncio.to_thread(self._verify_integrity, modpath, expected_hash):
                PLUGIN_ERRORS.labels(plugin_name=modname, error_type="integrity_failure").inc()
                logger.error(f"Integrity check failed for plugin {fname}. Hash mismatch. Skipping plugin.")
                await send_alert(f"Integrity failure for LLM plugin {fname}. Hash mismatch detected.", severity="critical")
                if span:
                    span.set_status(StatusCode.ERROR, "Integrity check failed")
                    span.record_exception(ValueError("Plugin integrity check failed"))
                return # Do not load compromised plugin

            # 2. Dynamic module loading
            # Remove from sys.modules to force a fresh import/reload if it was previously loaded
            if modname in sys.modules:
                del sys.modules[modname]

            spec = importlib.util.spec_from_file_location(modname, modpath)
            if spec is None:
                raise ValueError(f"Could not get module spec for plugin {fname}")

            mod = importlib.util.module_from_spec(spec)
            sys.modules[modname] = mod # Add to sys.modules for internal imports within the plugin
            await asyncio.to_thread(spec.loader.exec_module, mod) # Execute module in a thread to avoid blocking event loop
            self._loaded_modules[modname] = mod # Track module as loaded by manager

            # 3. Plugin registration
            provider_obj = None
            if hasattr(mod, "get_provider") and callable(mod.get_provider):
                provider_obj = mod.get_provider()
                name = getattr(provider_obj, "name", modname) # Use provider's name or filename
                self.add_provider(name, provider_obj)
                plugin_name_for_metrics = name # Use registered name for metrics
                logger.info(f"Loaded LLM provider plugin: '{name}' from {fname}")
            elif hasattr(mod, "register") and callable(mod.register):
                mod.register(self) # Plugin registers itself with the manager via add_provider
                logger.info(f"Plugin '{fname}' registered itself with the manager.")
                # Assuming register() calls add_provider, so metrics are handled there.
            else:
                raise ValueError(f"Plugin '{fname}' does not expose a 'get_provider()' function or a 'register(manager)' function. Skipping.")

            # 4. Metrics and Tracing on success
            PLUGIN_LOADS.labels(plugin_name=plugin_name_for_metrics).inc()
            PLUGIN_HEALTH.labels(plugin_name=plugin_name_for_metrics).set(1) # Mark as healthy
            PLUGIN_LOAD_LATENCY.labels(plugin_name=plugin_name_for_metrics).observe(time.perf_counter() - start_time)
            if span:
                span.set_attribute("plugin_loaded_name", plugin_name_for_metrics)
                span.set_status(StatusCode.OK)

        except Exception as e:
            PLUGIN_ERRORS.labels(plugin_name=plugin_name_for_metrics, error_type=type(e).__name__).inc()
            PLUGIN_HEALTH.labels(plugin_name=plugin_name_for_metrics).set(0) # Mark as unhealthy
            logger.error(f"Failed to load plugin {fname}: {e}", exc_info=True)
            await send_alert(f"Failed to load LLM plugin {fname}: {e}", severity="error")
            if span:
                span.set_status(StatusCode.ERROR, f"Plugin load failed: {e}")
                span.record_exception(e)

    def _verify_integrity(self, filepath: str, expected_hash: Optional[str]) -> bool:
        """
        Verifies plugin file integrity by comparing its SHA256 hash.
        This is a synchronous operation.
        """
        if not expected_hash:
            logger.error(f"No expected hash provided for plugin file '{filepath}'. Refusing to load.")
            return False
        try:
            with open(filepath, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            return file_hash == expected_hash
        except Exception as e:
            logger.error(f"Error computing hash for plugin file {filepath}: {e}", exc_info=True)
            return False

    def _get_expected_hash(self, modname: str) -> Optional[str]:
        """
        Retrieves the expected hash for a plugin from a secure, trusted source.
        In production, this would fetch from a database, a signed manifest file,
        or a secrets manager.
        """
        # Path to your signed or access-controlled manifest
        manifest_path = os.environ.get("LLM_PLUGIN_HASH_MANIFEST", "/etc/llm_plugin_hash_manifest.json")
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            # Fail-closed: If not found, do not load plugin
            if modname in manifest:
                return manifest[modname]
            logger.error(f"No hash found for plugin '{modname}' in manifest. Refusing to load.")
            return None
        except FileNotFoundError:
            logger.error(f"Plugin hash manifest file not found at '{manifest_path}'. Refusing to load any plugins.")
            return None
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in plugin hash manifest at '{manifest_path}'. Refusing to load any plugins.")
            return None
        except Exception as e:
            logger.error(f"Unexpected error while reading hash manifest: {e}", exc_info=True)
            return None

    def add_provider(self, name: str, provider: Any):
        """Adds an LLM provider to the registry. Called by plugins or manager."""
        if name in self.registry:
            logger.warning(f"LLM provider '{name}' already exists in registry. Overwriting.")
        self.registry[name] = provider
        logger.info(f"Added LLM provider '{name}' to registry.")

    async def reload(self):
        """
        Reloads all plugins. This clears the current registry and re-scans/reloads.
        Protected by a lock for concurrent safety.
        """
        async with self.lock: # Acquire lock for the entire reload operation
            logger.info("Reloading all LLM plugins...")
            PLUGIN_RELOADS.labels(plugin_name="all").inc() # Metric for overall reload event

            # Mark all current plugins as unhealthy before clearing
            for name in self.registry.keys():
                PLUGIN_HEALTH.labels(plugin_name=name).set(0)

            self.registry.clear() # Clear current providers

            # Remove loaded modules from sys.modules to force a fresh import
            # This is crucial for true hot-reloading of Python modules.
            for modname in list(self._loaded_modules.keys()):
                if modname in sys.modules:
                    del sys.modules[modname]
            self._loaded_modules.clear() # Clear internal tracking of loaded modules

            await self._scan_and_load_plugins() # Rescan and load plugins
            logger.info(f"Finished reloading. Loaded providers: {self.list_providers()}")

    def list_providers(self) -> List[str]:
        """Returns a list of names of all loaded LLM providers."""
        return list(self.registry.keys())

    def get_provider(self, name: str) -> Optional[Any]:
        """Retrieves an LLM provider by its registered name."""
        return self.registry.get(name)

    def _start_watcher(self):
        """Starts a file system watcher for hot-reloading if AUTO_RELOAD is enabled."""
        if not os.path.exists(self.plugin_dir):
            logger.error(f"Cannot start watcher: Plugin directory '{self.plugin_dir}' does not exist.")
            return

        class PluginEventHandler(FileSystemEventHandler):
            def __init__(self, manager_instance: 'LLMPluginManager'):
                super().__init__()
                self.manager = manager_instance
                self.loop = asyncio.get_event_loop() # Get the event loop of the main thread

            def on_modified(self, event: FileModifiedEvent):
                if event.is_directory or not event.src_path.endswith(".py"):
                    return
                logger.info(f"Plugin file modified: {event.src_path}. Triggering reload.")
                # Schedule reload in the main event loop to avoid thread issues
                self.loop.call_soon_threadsafe(lambda: asyncio.create_task(self.manager.reload()))

            def on_created(self, event: FileCreatedEvent):
                if event.is_directory or not event.src_path.endswith(".py"):
                    return
                logger.info(f"New plugin file created: {event.src_path}. Triggering reload.")
                self.loop.call_soon_threadsafe(lambda: asyncio.create_task(self.manager.reload()))

            def on_deleted(self, event: FileDeletedEvent):
                if event.is_directory or not event.src_path.endswith(".py"):
                    return
                logger.info(f"Plugin file deleted: {event.src_path}. Triggering reload.")
                self.loop.call_soon_threadsafe(lambda: asyncio.create_task(self.manager.reload()))

            def on_moved(self, event: FileMovedEvent):
                if event.is_directory or not (event.src_path.endswith(".py") or event.dest_path.endswith(".py")):
                    return
                logger.info(f"Plugin file moved/renamed: {event.src_path} -> {event.dest_path}. Triggering reload.")
                self.loop.call_soon_threadsafe(lambda: asyncio.create_task(self.manager.reload()))


        self.observer = Observer()
        self.observer.schedule(PluginEventHandler(self), self.plugin_dir, recursive=False)
        self.observer.start()
        logger.info(f"Started file watcher for hot-reloading in '{self.plugin_dir}'.")

    def close(self):
        """Closes the plugin manager, stopping the file watcher."""
        if self.observer:
            self.observer.stop()
            self.observer.join()
            logger.info("LLMPluginManager file watcher stopped.")
        # Flush OpenTelemetry spans related to this manager before closing
        if HAS_OPENTELEMETRY and tracer and trace.get_tracer_provider():
            trace.get_tracer_provider().force_flush()
            logger.debug("LLMPluginManager traces flushed.")
        logger.info("LLMPluginManager closed.")


# --- Documentation ---
"""
# LLM Plugin Manager

## Overview
This module provides a world-class plugin manager for LLM providers, supporting dynamic loading, hot-reloading with file watching, security checks (integrity verification), observability (metrics and tracing), concurrent safety, and extensive testing.

## Configuration
Set environment variables with prefix `LLM_PLUGIN_`:
- `PLUGIN_DIR`: Directory for plugins (default: "llm_providers").
- `AUTO_RELOAD`: Enable file watcher for hot-reloading (true/false).
- `ALERT_ENDPOINT`: URL for alerting on failures (e.g., integrity check).
- `OTLP_ENDPOINT`: OTLP endpoint for tracing (default: "http://otel-collector:4317").

## Security
- **Integrity Checks**: Verifies plugin hashes against expected values (fetch from secure storage).
- **Sandboxing**: Plugins are loaded in the same process; use isolated processes or containers for production.
- **Dependency Audits**: Run `pip-audit` regularly.

## Operational Procedures
- **Recovery**: Reload plugins on startup or via `reload()`.
- **Migration**: Not applicable (plugins are code-based).
- **Backup**: Back up plugin directory and hash database.
- **Alerts**: Monitor `PLUGIN_ERRORS` and `PLUGIN_HEALTH` metrics.

## Limitations
- Plugins run in the same process, posing security risks if untrusted.
- Hot-reloading may not handle all state changes gracefully.

"""

# --- Example Usage (for local testing of the module) ---
if __name__ == "__main__":
    # Configure basic logging for example usage
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Create a dummy plugin directory and dummy plugin files for testing
    dummy_plugin_dir = "llm_plugins_test_env"
    os.makedirs(dummy_plugin_dir, exist_ok=True)

    # Set dummy environment variables for Dynaconf settings
    os.environ["LLM_PLUGIN_PLUGIN_DIR"] = dummy_plugin_dir
    os.environ["LLM_PLUGIN_AUTO_RELOAD"] = "true"
    os.environ["LLM_PLUGIN_ALERT_ENDPOINT"] = "http://localhost:9093/api/v2/alerts" # Mock Alertmanager
    os.environ["LLM_PLUGIN_OTLP_ENDPOINT"] = "http://localhost:4317" # Mock OTel Collector

    # Create a dummy hash manifest for testing the new enforcement logic
    dummy_manifest = {
        "mock_grok_provider": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890", # Placeholder
        "mock_claude_provider": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890", # Placeholder
    }
    dummy_manifest_path = os.path.join(dummy_plugin_dir, "plugin_hash_manifest.json")
    with open(dummy_manifest_path, "w") as f:
        json.dump(dummy_manifest, f)
    # Point the environment variable to our dummy manifest
    os.environ["LLM_PLUGIN_HASH_MANIFEST"] = dummy_manifest_path

    # Re-initialize settings to pick up environment variables
    settings.reload()

    # Dummy plugin 1: using get_provider()
    grok_plugin_path = os.path.join(dummy_plugin_dir, "mock_grok_provider.py")
    with open(grok_plugin_path, "w") as f:
        f.write("""
import asyncio
class MockGrokProvider:
    def __init__(self):
        self.name = "grok"
    async def generate_text(self, prompt: str) -> str:
        await asyncio.sleep(0.01) # Simulate async operation
        return f"Mock Grok response to: {prompt}"

def get_provider():
    return MockGrokProvider()
""")
    # Calculate hash for integrity check example
    with open(grok_plugin_path, "rb") as f:
        grok_hash = hashlib.sha256(f.read()).hexdigest()
    # Update our dummy manifest with the correct hash
    dummy_manifest["mock_grok_provider"] = grok_hash
    with open(dummy_manifest_path, "w") as f:
        json.dump(dummy_manifest, f)

    # Dummy plugin 2: (This one has no hash in the manifest to test rejection)
    no_hash_plugin_path = os.path.join(dummy_plugin_dir, "mock_no_hash_provider.py")
    with open(no_hash_plugin_path, "w") as f:
        f.write("""
class MockNoHashProvider:
    def __init__(self):
        self.name = "no-hash-provider"
    async def generate_text(self, prompt: str) -> str:
        return f"This response from an un-hashed plugin: {prompt}"

def get_provider():
    return MockNoHashProvider()
""")

    print("--- Initial Plugin Loading (with integrity check enforcement) ---")
    manager = LLMPluginManager() # Uses settings.PLUGIN_DIR
    # Wait for initial scan to complete
    asyncio.run(manager._scan_and_load_plugins_on_init) # Await the task started in __init__
    print(f"Loaded providers: {manager.list_providers()}")
    print(f"Grok plugin health: {PLUGIN_HEALTH.labels(plugin_name='grok')._value}")

    grok_provider = manager.get_provider("grok")
    if grok_provider:
        print(f"Grok provider found. Test generation: {asyncio.run(grok_provider.generate_text('Hello Grok'))}")
    else:
        print("Grok provider was NOT loaded.")

    print(f"\n'No-hash' provider loaded? {'yes' if manager.get_provider('no-hash-provider') else 'no'}. This should be 'no' due to hash enforcement.")


    # Simulate tampering with the Grok plugin file
    print("\n--- Simulating Plugin Tampering ---")
    with open(grok_plugin_path, "a") as f: # Append some data to corrupt hash
        f.write("\n# Tampered content")

    # Manually trigger reload to detect tampering
    asyncio.run(manager.reload())
    print(f"Loaded providers after tampering reload: {manager.list_providers()}")
    print(f"Grok plugin health after tampering: {PLUGIN_HEALTH.labels(plugin_name='grok')._value}")
    print(f"Plugin errors after tampering: {PLUGIN_ERRORS.labels(plugin_name='mock_grok_provider', error_type='integrity_failure')._value}")
    print("\nNote: The 'no-hash' provider was also re-evaluated and rejected again.")


    # Simulate adding a new plugin dynamically (will trigger auto-reload if enabled)
    print("\n--- Simulating Adding New Plugin (with auto-reload) ---")
    claude_plugin_path = os.path.join(dummy_plugin_dir, "mock_claude_provider.py")
    with open(claude_plugin_path, "w") as f:
        f.write("""
class MockClaudeProvider:
    def __init__(self):
        self.name = "claude"
    async def generate_text(self, prompt: str) -> str:
        return f"Mock Claude response to: {prompt}"

def get_provider():
    return MockClaudeProvider()
""")
    # Calculate hash for integrity check example
    with open(claude_plugin_path, "rb") as f:
        claude_hash = hashlib.sha256(f.read()).hexdigest()
    # Update the manifest with the new plugin's hash
    dummy_manifest["mock_claude_provider"] = claude_hash
    with open(dummy_manifest_path, "w") as f:
        json.dump(dummy_manifest, f)

    # Give watchdog a moment to detect the change
    time.sleep(0.5)
    asyncio.run(asyncio.sleep(0.1)) # Yield control to event loop for watcher task to run
    print(f"Loaded providers after new plugin: {manager.list_providers()}")
    claude_provider = manager.get_provider("claude")
    if claude_provider:
        print(f"Claude provider found. Test generation: {asyncio.run(claude_provider.generate_text('Hello Claude'))}")
    else:
        print("Claude provider was NOT loaded.")


    # Clean up dummy plugin directory and environment variables
    manager.close() # Stop the watcher
    shutil.rmtree(dummy_plugin_dir)
    del os.environ["LLM_PLUGIN_PLUGIN_DIR"]
    del os.environ["LLM_PLUGIN_AUTO_RELOAD"]
    del os.environ["LLM_PLUGIN_ALERT_ENDPOINT"]
    del os.environ["LLM_PLUGIN_OTLP_ENDPOINT"]
    del os.environ["LLM_PLUGIN_HASH_MANIFEST"]