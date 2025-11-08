# audit_plugins.py
import asyncio
import concurrent.futures
import functools
import importlib
import inspect
import json
import logging
import multiprocessing
import os
import resource  # For limits
import sys
import time
import traceback
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import hypothesis
import hypothesis.strategies as st

# Integrate with audit_metrics if it exists, otherwise provide dummy classes
try:
    from prometheus_client import Counter, Gauge
except ImportError:
    logger = logging.getLogger(__name__) # Need logger defined early
    class Counter:
        def __init__(self, *args, **kwargs): self._value = 0
        def labels(self, *args, **kwargs): return self
        def inc(self, *args, **kwargs): self._value += 1
    class Gauge:
        def __init__(self, *args, **kwargs): self._value = 0
        def labels(self, *args, **kwargs): return self
        def set(self, *args, **kwargs): self._value = args[0] if args else 0
    logger.warning("prometheus_client not found. Metrics will be unavailable.")

# Import log_action, handle potential circular dependency
try:
    # Use relative import if part of a package
    from .audit_log import log_action as real_log_action
    _DUMMY_LOG_ACTION_USED = False
except ImportError:
    try:
        from audit_log import log_action as real_log_action
        _DUMMY_LOG_ACTION_USED = False
    except ImportError:
        _DUMMY_LOG_ACTION_USED = True
        logger = logging.getLogger(__name__)
        logger.warning("audit_log.py not found or circular dependency. log_action will be a dummy function.")
        async def real_log_action(*args, **kwargs): # Make dummy async to match expected signature
            logging.info(f"Dummy log_action: {args}, {kwargs}")

log_action = real_log_action
logger = logging.getLogger(__name__)

# Constants/Security
PLUGIN_DIR = 'audit_plugins_dir'  # Dynamic load from here
PLUGIN_CONFIG = 'plugins.json'  # Config for enabled plugins/policies
# NOTE: MAX_PLUGIN_CPU_SECONDS is enforced as an integer
MAX_PLUGIN_CPU_SECONDS = 1  # rlimit fraction - now in seconds directly (min 1)
MAX_PLUGIN_MEM_BYTES = 100 * 1024 * 1024  # 100MB (100 MB)
MAX_PLUGIN_TIME_SECONDS = 5  # Seconds timeout
# Default policy controls; these can be overridden per plugin in plugins.json
POLICY_CONTROLS = {'modify': False, 'redact': True, 'augment': True}

os.makedirs(PLUGIN_DIR, exist_ok=True)

# Metrics
PLUGIN_INVOCATIONS = Counter('audit_plugin_invocations_total', 'Plugin calls', ['event', 'plugin'])
PLUGIN_ERRORS = Counter('audit_plugin_errors_total', 'Plugin errors', ['event', 'plugin', 'type'])
PLUGIN_LATENCY = Gauge('audit_plugin_latency_seconds', 'Plugin execution time', ['event', 'plugin'])
PLUGIN_MODIFICATIONS = Counter('audit_plugin_modifications_total', 'Entry modifications', ['plugin', 'type'])  # modify/redact/augment
# New metric for commercial plugin usage
COMMERCIAL_PLUGIN_USAGE = Counter('audit_commercial_plugin_usage_total', 'Usage count for commercial plugins', ['plugin', 'feature'])


# Lifecycle Events (all possible)
EVENTS = [
    'pre_append', 'post_append',
    'pre_query', 'post_query',
    'error', 'tamper',
    'oscillation',
    'startup', 'shutdown',
    'billing_report' # New event for commercial plugin reporting
]

# Hooks registry: defaultdict for easy management of multiple hooks per event
hooks: Dict[str, List[Callable[[Any], Optional[Any]]]] = defaultdict(list)  # Event: hooks

class AuditPlugin(ABC):
    """
    Abstract base class for audit plugins.
    Plugins implement the `process` method to modify, redact, or augment log entries based on policy controls.
    """
    @abstractmethod
    def process(self, entry: Dict[str, Any], event: str) -> Optional[Dict[str, Any]]:
        """
        Processes a log entry for a specific event.
        Args:
            entry (Dict[str, Any]): The log entry to process.
            event (str): The lifecycle event triggering this process (e.g., 'pre_append').
        Returns:
            Optional[Dict[str, Any]]: The modified entry, or None if the entry should be skipped/dropped.
        """
        pass

    def get_plugin_name(self) -> str:
        """Helper method to get the plugin's class name for metrics and logging."""
        return self.__class__.__name__

class CommercialPlugin(AuditPlugin, ABC):
    """
    Abstract base class for commercial plugins.
    Extends AuditPlugin with methods for billing and usage tracking.
    """
    @abstractmethod
    def get_usage_data(self) -> Dict[str, Any]:
        """
        Collects and returns current usage data for billing/tracking purposes.
        This method will be called periodically or on specific events (e.g., 'billing_report').
        """
        pass

    @abstractmethod
    def reset_usage_data(self) -> None:
        """
        Resets internal usage counters after the data has been reported or billed.
        """
        pass

class DefaultPlugin(AuditPlugin):
    """
    An example default plugin demonstrating basic redaction functionality.
    """
    def process(self, entry: Dict[str, Any], event: str) -> Optional[Dict[str, Any]]:
        # Example: Redact sensitive 'password' field if redaction is allowed by policy
        if POLICY_CONTROLS['redact'] and 'details' in entry and isinstance(entry['details'], dict) and 'password' in entry['details']:
            entry['details']['password'] = '[REDACTED]'
            PLUGIN_MODIFICATIONS.labels(plugin=self.get_plugin_name(), type='redact').inc()
        return entry

# Example Commercial Plugin
class BillingPlugin(CommercialPlugin):
    """
    An example commercial plugin that tracks various usage metrics.
    Demonstrates billing, reporting, and usage quotas concepts.
    """
    def __init__(self):
        self.processed_entries_count = 0
        self.redacted_fields_count = 0
        self.augmented_data_size = 0 # In bytes or character count
        logger.info("BillingPlugin initialized.")

    def process(self, entry: Dict[str, Any], event: str) -> Optional[Dict[str, Any]]:
        self.processed_entries_count += 1
        COMMERCIAL_PLUGIN_USAGE.labels(plugin=self.get_plugin_name(), feature='processed_entries').inc()

        original_entry_json = json.dumps(entry, sort_keys=True) # Ensure consistent sorting for comparison
        modified_entry = entry.copy() # Work on a copy to avoid unintended side effects

        # Example: Perform a redaction that also counts
        if 'sensitive_info' in modified_entry and POLICY_CONTROLS['redact']:
            modified_entry['sensitive_info'] = '[REDACTED_BY_BILLING_PLUGIN]'
            self.redacted_fields_count += 1
            PLUGIN_MODIFICATIONS.labels(plugin=self.get_plugin_name(), type='redact').inc()
            COMMERCIAL_PLUGIN_USAGE.labels(plugin=self.get_plugin_name(), feature='redacted_fields').inc() # Also track as commercial usage
        
        # Example: Perform an augmentation that also tracks size
        if 'additional_context' not in modified_entry and POLICY_CONTROLS['augment']:
            added_data = "This is augmented data for billing tracking."
            modified_entry['additional_context'] = added_data
            added_bytes = len(added_data.encode('utf-8'))
            self.augmented_data_size += added_bytes # Track size in bytes
            PLUGIN_MODIFICATIONS.labels(plugin=self.get_plugin_name(), type='augment').inc()
            COMMERCIAL_PLUGIN_USAGE.labels(plugin=self.get_plugin_name(), feature='augmented_data_bytes').inc(added_bytes) # Increment by bytes

        # Check if actual modification occurred before returning the modified entry
        if json.dumps(modified_entry, sort_keys=True) != original_entry_json:
            return modified_entry
        return entry # Return original if no modification happened

    def get_usage_data(self) -> Dict[str, Any]:
        """Collect current usage data."""
        return {
            "processed_entries": self.processed_entries_count,
            "redacted_fields": self.redacted_fields_count,
            "augmented_data_bytes": self.augmented_data_size,
            "timestamp": time.time(),
            "plugin_name": self.get_plugin_name()
        }

    def reset_usage_data(self) -> None:
        """Reset counts after reporting."""
        logger.info(f"Resetting usage data for {self.get_plugin_name()}.")
        self.processed_entries_count = 0
        self.redacted_fields_count = 0
        self.augmented_data_size = 0


# Dynamic registry for loaded plugin instances
plugins: Dict[str, AuditPlugin] = {}  # Name: instance

def discover_plugins():
    """
    Discovers and loads plugins dynamically from a configured directory and a JSON configuration file.
    Supports hot-reloading by clearing and reloading plugins.
    """
    # Reset plugins dict to avoid duplicates on re-discovery
    plugins.clear()

    # Load plugins defined in the configuration file first (higher priority)
    if os.path.exists(PLUGIN_CONFIG):
        with open(PLUGIN_CONFIG, 'r') as f:
            try:
                config = json.load(f)
                for name, cfg in config.get('plugins', {}).items():
                    if cfg.get('enabled', False): # Plugin must be explicitly enabled
                        try:
                            # Import the module specified in config
                            mod = importlib.import_module(cfg['module'])
                            # Get the class from the module
                            cls = getattr(mod, cfg['class'])
                            # Ensure it's a valid AuditPlugin (not the abstract base classes)
                            if issubclass(cls, AuditPlugin) and cls not in (AuditPlugin, CommercialPlugin):
                                plugins[name] = cls(**cfg.get('params', {})) # Instantiate with parameters
                                logger.info(f"Loaded plugin '{name}' from config: {cls.__name__}")
                            else:
                                logger.warning(f"Class {cls.__name__} from config is not a valid AuditPlugin or is an abstract base; skipping.")
                        except (ImportError, AttributeError, TypeError, KeyError) as e:
                            logger.error(f"Failed to load plugin '{name}' from config ({cfg.get('module')}.{cfg.get('class')}): {e}")
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing plugin config file {PLUGIN_CONFIG}: {e}")

    # Discover and load .py files from the PLUGIN_DIR
    for file in Path(PLUGIN_DIR).glob('*.py'):
        if file.stem == '__init__':
            continue # Skip __init__.py files
        
        module_name = file.stem
        # Add plugin directory to sys.path temporarily to allow importing modules from it
        original_sys_path = sys.path[:]
        if PLUGIN_DIR not in sys.path:
            sys.path.insert(0, PLUGIN_DIR)
        
        try:
            # Use importlib.util for more explicit and safer module loading
            spec = importlib.util.spec_from_file_location(module_name, str(file)) # Use str(file)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                
                for name, obj in inspect.getmembers(mod):
                    # Check if it's a class, subclass of AuditPlugin, and not one of the abstract base classes
                    if inspect.isclass(obj) and issubclass(obj, AuditPlugin) and obj not in (AuditPlugin, CommercialPlugin):
                        # Only load if not already loaded from config (config has priority)
                        if obj.__name__ not in plugins: 
                            plugins[obj.__name__] = obj() # Instantiate the plugin
                            logger.info(f"Discovered and loaded plugin '{obj.__name__}' from file: {file.name}")
            else:
                logger.warning(f"Could not get spec for module {module_name} from file {file.name}.")
        except Exception as e:
            logger.error(f"Failed to load plugin from file {file.name}: {e}", exc_info=True)
        finally:
            # Always restore original sys.path to prevent side effects
            sys.path[:] = original_sys_path

# Call discover_plugins during module initialization to load plugins on startup
discover_plugins()

def register_hook(event: str, hook: Callable[[Any], Optional[Any]]):
    """
    Registers a callable hook function for a specific audit lifecycle event.
    Args:
        event (str): The name of the event (must be in EVENTS list).
        hook (Callable): The hook function to register. Can be async or sync.
    Raises:
        ValueError: If the event is unknown.
        TypeError: If the hook is not callable.
    """
    if event not in EVENTS:
        raise ValueError(f"Unknown event: {event}. Supported events are: {', '.join(EVENTS)}")
    if not callable(hook):
        raise TypeError("hook must be a callable function.")
    hooks[event].append(hook)
    logger.debug(f"Hook '{getattr(hook, '__name__', 'anonymous_hook')}' registered for event '{event}'.")

async def execute_hooks_sync(event: str, data: Any) -> Any:
    """
    Executes synchronous hooks registered for an event.
    Synchronous hooks are run in a thread pool to avoid blocking the event loop.
    """
    current_data = data
    for hook in hooks[event]:
        hook_name = getattr(hook, '__name__', 'anonymous_sync_hook')
        start_time = time.perf_counter()
        try:
            if asyncio.iscoroutinefunction(hook):
                # If an async hook is mistakenly registered here, warn and run in thread.
                logger.warning(f"Async hook '{hook_name}' registered for sync event '{event}'. Running in thread for compatibility.")
                result = await asyncio.to_thread(hook, current_data)
            else:
                # FIX: Run sync hooks in a thread to avoid blocking the event loop
                result = await asyncio.to_thread(hook, current_data)
            
            if result is not None:
                current_data = result # Allow hooks to chain modifications

        except Exception as e:
            logger.error(f"Error in synchronous hook '{hook_name}' for event '{event}': {e}\n{traceback.format_exc()}")
            PLUGIN_ERRORS.labels(event=event, plugin=hook_name, type=type(e).__name__).inc()
            # Policy for hook failure: continue or halt. Currently continues.
        finally:
            PLUGIN_LATENCY.labels(event=event, plugin=hook_name).set(time.perf_counter() - start_time)
    return current_data

async def execute_hooks_async(event: str, data: Any) -> Any:
    """
    Executes asynchronous hooks registered for an event.
    Asynchronous hooks are awaited sequentially to apply modifications in order.
    """
    processed_data = data

    for hook in hooks[event]:
        hook_name = getattr(hook, '__name__', 'anonymous_async_hook')
        start_time = time.perf_counter()
        try:
            if asyncio.iscoroutinefunction(hook):
                result = await hook(processed_data)
            else:
                # If a sync hook is mistakenly registered here, warn and run in thread.
                logger.warning(f"Sync hook '{hook_name}' registered for async event '{event}'. Running in thread for compatibility.")
                result = await asyncio.to_thread(hook, processed_data)
            
            if result is not None:
                processed_data = result # Apply modification if hook returned a value
        except Exception as e:
            logger.error(f"Error in asynchronous hook '{hook_name}' for event '{event}': {e}\n{traceback.format_exc()}")
            PLUGIN_ERRORS.labels(event=event, plugin=hook_name, type=type(e).__name__).inc()
            # Policy for hook failure: continue or halt. Currently continues.
        finally:
            PLUGIN_LATENCY.labels(event=event, plugin=hook_name).set(time.perf_counter() - start_time)

    return processed_data


def _sandboxed_worker(q: multiprocessing.Queue, plugin_name: str, entry: Dict[str, Any], event: str):
    """
    Worker process for sandboxed plugin execution.
    Sets resource limits (CPU, memory) for isolation.
    """
    try:
        # Set resource limits within the child process
        # RLIMIT_CPU limits CPU time in seconds.
        # RLIMIT_AS limits the virtual memory (address space) in bytes.
        # Note: `resource.setrlimit` is Unix-specific and does not work on Windows.
        if sys.platform != "win32":
            # Set soft and hard limits for CPU time
            resource.setrlimit(resource.RLIMIT_CPU, (MAX_PLUGIN_CPU_SECONDS, MAX_PLUGIN_CPU_SECONDS))
            # Set soft and hard limits for virtual memory
            resource.setrlimit(resource.RLIMIT_AS, (MAX_PLUGIN_MEM_BYTES, MAX_PLUGIN_MEM_BYTES))
        
        # Re-discover plugins *within the child process* to get a clean instance.
        # This is safer than relying on inherited globals, especially for 'spawn' start method.
        # Note: This means the plugin *must* be discoverable from the child (e.g., in PLUGIN_DIR).
        discover_plugins()
        
        plugin_instance = plugins.get(plugin_name)
        if not plugin_instance:
            raise RuntimeError(f"Plugin instance '{plugin_name}' not found in worker process.")

        result = plugin_instance.process(entry, event)
        q.put(result) # Send result back via queue
    except Exception as e:
        q.put(e) # Send exception back for handling in parent process
        # Logging here might be lost if the process is terminated abruptly
        # It's safer to log from the parent process after catching the exception.


async def sandboxed_execute(plugin: AuditPlugin, entry: Dict[str, Any], event: str) -> Optional[Dict[str, Any]]:
    """
    Executes a plugin's `process` method within a separate, resource-limited subprocess.
    Args:
        plugin (AuditPlugin): The plugin instance to execute.
        entry (Dict[str, Any]): The log entry to pass to the plugin.
        event (str): The event triggering the plugin.
    Returns:
        Optional[Dict[str, Any]]: The processed entry, or None if the plugin failed or timed out.
    """
    start_time = time.perf_counter()
    plugin_name = plugin.get_plugin_name()
    q = multiprocessing.Queue() # Queue for inter-process communication
    
    p = multiprocessing.Process(target=_sandboxed_worker, args=(q, plugin_name, entry, event))
    p.start()
    
    result_or_exception = None
    try:
        # Use asyncio.wait_for with asyncio.to_thread to await the blocking q.get() call
        result_or_exception = await asyncio.wait_for(
            asyncio.to_thread(q.get), timeout=MAX_PLUGIN_TIME_SECONDS
        )
    except asyncio.TimeoutError:
        p.terminate() # Terminate the process if it times out
        p.join(timeout=1) # Wait for process to exit cleanly
        logger.error(f"Plugin '{plugin_name}' for event '{event}' timed out after {MAX_PLUGIN_TIME_SECONDS} seconds. Process terminated.")
        PLUGIN_ERRORS.labels(event=event, plugin=plugin_name, type='timeout').inc()
        return None
    except Exception as e: # Catch any other errors from q.get or to_thread
        logger.error(f"Error retrieving result from plugin '{plugin_name}' sandbox: {e}")
        PLUGIN_ERRORS.labels(event=event, plugin=plugin_name, type=type(e).__name__).inc()
        return None
    finally:
        # Ensure the process is joined if it hasn't already (e.g., if it completed within timeout)
        if p.is_alive():
            p.join(timeout=1) # Give it a moment to terminate if it was terminated
        PLUGIN_LATENCY.labels(event=event, plugin=plugin_name).set(time.perf_counter() - start_time)

    if isinstance(result_or_exception, Exception):
        logger.error(f"Plugin '{plugin_name}' for event '{event}' failed in sandbox: {result_or_exception}")
        PLUGIN_ERRORS.labels(event=event, plugin=plugin_name, type=type(result_or_exception).__name__).inc()
        return None
    
    return result_or_exception

async def trigger_event(event: str, data: Any) -> Any:
    """
    Triggers all registered hooks and plugins for a given event, with plugin execution in isolation.
    Args:
        event (str): The lifecycle event name.
        data (Any): The data (e.g., log entry) to pass to hooks and plugins.
    Returns:
        Any: The potentially modified data after all hooks and plugins have processed it.
    """
    if event not in EVENTS:
        logger.warning(f"Attempted to trigger unknown event: {event}. Skipping.")
        return data

    PLUGIN_INVOCATIONS.labels(event=event, plugin='all_plugins').inc() # General invocation metric for the event
    
    current_data = data

    # Execute hooks (synchronous and asynchronous)
    # Hooks modify `current_data` sequentially
    current_data = await execute_hooks_sync(event, current_data) # Use original event name, hooks handle their type
    current_data = await execute_hooks_async(event, current_data)

    # Execute plugins (sandboxed)
    for name, plugin in plugins.items():
        # Commercial Plugins: Handle specific billing_report event outside regular processing
        if isinstance(plugin, CommercialPlugin) and event == 'billing_report':
            usage_data = plugin.get_usage_data()
            logger.info(f"Commercial plugin '{name}' usage data for billing: {usage_data}")
            # In a real system, `usage_data` would be sent to an external billing service.
            # After successful reporting, reset usage data for the next billing cycle.
            plugin.reset_usage_data()
            COMMERCIAL_PLUGIN_USAGE.labels(plugin=name, feature='billing_reported').inc() # Metric for reporting
            continue # Skip normal process() call for billing_report event

        # For regular events, execute the plugin's process method in a sandbox
        PLUGIN_INVOCATIONS.labels(event=event, plugin=name).inc() # Per-plugin invocation metric
        modified_data = await sandboxed_execute(plugin, current_data, event)
        
        if modified_data is not None:
            # Policy Enforcement: Determine if the plugin's modification is allowed
            plugin_policies = getattr(plugin, 'policy_controls', POLICY_CONTROLS) # Plugin can define its own policies
            
            original_data_bytes = json.dumps(current_data, sort_keys=True).encode('utf-8') if current_data is not None else b''
            modified_data_bytes = json.dumps(modified_data, sort_keys=True).encode('utf-8') if modified_data is not None else b''
            
            modification_allowed = False
            modification_type = 'none'

            # Determine modification type based on data changes and sizes
            if modified_data_bytes == original_data_bytes:
                modification_allowed = True # No actual modification, so always allowed
                modification_type = 'none'
            elif len(modified_data_bytes) < len(original_data_bytes):
                # Data size decreased, indicative of redaction
                if plugin_policies['redact']:
                    modification_allowed = True
                    modification_type = 'redact'
                else:
                    logger.warning(f"Plugin '{name}' attempted unauthorized redaction for event '{event}'. Redaction policy is disabled.")
            elif len(modified_data_bytes) > len(original_data_bytes):
                # Data size increased, indicative of augmentation
                if plugin_policies['augment']:
                    modification_allowed = True
                    modification_type = 'augment'
                else:
                    logger.warning(f"Plugin '{name}' attempted unauthorized augmentation for event '{event}'. Augmentation policy is disabled.")
            elif modified_data_bytes != original_data_bytes:
                # Content changed but size is the same, or direct modification
                if plugin_policies['modify']:
                    modification_allowed = True
                    modification_type = 'modify'
                else:
                    logger.warning(f"Plugin '{name}' attempted unauthorized general modification for event '{event}'. Modification policy is disabled.")

            if modification_allowed:
                current_data = modified_data
                if modification_type != 'none': # Only increment if an actual type of modification occurred
                    PLUGIN_MODIFICATIONS.labels(plugin=plugin.get_plugin_name(), type=modification_type).inc()
            else:
                # If modification is not allowed, revert to original data
                logger.warning(f"Plugin '{name}' attempted unauthorized modification of type '{modification_type}' for event '{event}'. Original data retained.")
                PLUGIN_ERRORS.labels(event=event, plugin=name, type='policy_denial').inc()
        else:
            logger.warning(f"Plugin '{name}' for event '{event}' returned None (e.g., error/timeout in sandbox). Original data retained.")
            # Errors for plugin execution are already logged by `sandboxed_execute`

    # Audit plugin events for traceability
    # FIX: Added await for the async log_action
    await log_action("plugin_event", {
        "event": event,
        "plugins_invoked": list(plugins.keys()),
        "hooks_count": len(hooks[event]),
        "final_data_hash": compute_hash(json.dumps(current_data, sort_keys=True).encode('utf-8') if current_data is not None else b'')
    })
    
    return current_data

# Rich API for plugins (modify/redact/augment with policies)
# Example usage in `audit_log.py` or other modules:
# processed_entry = await trigger_event('pre_append', original_entry)

# --- Test Suite ---
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
# `hypothesis` and `st` are already imported at the top

# Define a simple plugin file for dynamic loading in tests
# Create a dummy plugin file in the PLUGIN_DIR
dummy_plugin_content = """
from audit_plugins import AuditPlugin
import time

class DynamicTestPlugin(AuditPlugin):
    def process(self, entry, event):
        if event == 'pre_append':
            entry['dynamic_processed'] = True
        elif event == 'post_query':
            entry['query_augmented'] = True
        return entry

class AnotherDynamicPlugin(AuditPlugin):
    def process(self, entry, event):
        # This plugin might try to modify even if 'modify' policy is False
        entry['another_dynamic'] = True
        return entry
"""

# Test configuration file content
dummy_config_content = json.dumps({
    "plugins": {
        "ConfiguredTestPlugin": {
            "enabled": True,
            "module": "audit_plugins_dir.configured_plugin", # Assuming a file named configured_plugin.py
            "class": "ConfiguredTestPluginClass",
            "params": {"setting": "test"}
        },
        "DisabledPlugin": {
            "enabled": False,
            "module": "audit_plugins_dir.disabled_plugin",
            "class": "DisabledPluginClass"
        }
    }
})

dummy_configured_plugin_content = """
from audit_plugins import AuditPlugin

class ConfiguredTestPluginClass(AuditPlugin):
    def __init__(self, setting):
        self.setting = setting
    def process(self, entry, event):
        entry['configured_processed'] = self.setting
        return entry
"""

class TestAuditPlugins(unittest.IsolatedAsyncioTestCase): # Use IsolatedAsyncioTestCase for async tests
    async def asyncSetUp(self):
        # Clear/create plugin directory and config for isolated tests
        if os.path.exists(PLUGIN_DIR):
            import shutil
            shutil.rmtree(PLUGIN_DIR)
        os.makedirs(PLUGIN_DIR, exist_ok=True)

        # Create dummy plugin files
        with open(os.path.join(PLUGIN_DIR, 'dynamic_plugin.py'), 'w') as f:
            f.write(dummy_plugin_content)
        with open(os.path.join(PLUGIN_DIR, 'configured_plugin.py'), 'w') as f:
            f.write(dummy_configured_plugin_content)
        
        # Create dummy plugin config
        with open(PLUGIN_CONFIG, 'w') as f:
            f.write(dummy_config_content)

        # Re-discover plugins after creating files
        global plugins
        plugins = {} # Clear global plugins to ensure fresh discovery
        discover_plugins() # Call discover_plugins
        
        # Reset hooks for each test
        global hooks
        hooks = defaultdict(list)
        
        # Reset global policy controls for each test
        global POLICY_CONTROLS
        POLICY_CONTROLS = {'modify': False, 'redact': True, 'augment': True} # Default policies for tests

        # Patch log_action to prevent actual logging interference and allow inspection
        self.log_action_patch = patch('audit_plugins.log_action', new=AsyncMock())
        self.mock_log_action = self.log_action_patch.start()
        
        # Clear Prometheus metrics for a clean test run
        PLUGIN_INVOCATIONS.labels(event='pre_append', plugin='all_plugins')._value = 0
        PLUGIN_ERRORS.labels(event='pre_query', plugin='SlowPlugin', type='timeout')._value = 0
        PLUGIN_LATENCY.labels(event='pre_append', plugin='test_sync_hook')._value = 0
        PLUGIN_MODIFICATIONS.labels(plugin='DynamicTestPlugin', type='augment')._value = 0


    async def asyncTearDown(self):
        import shutil
        if os.path.exists(PLUGIN_DIR):
            shutil.rmtree(PLUGIN_DIR)
        if os.path.exists(PLUGIN_CONFIG):
            os.remove(PLUGIN_CONFIG)
        self.log_action_patch.stop()

    async def test_discover_plugins(self):
        self.assertIn('DynamicTestPlugin', plugins)
        self.assertIn('AnotherDynamicPlugin', plugins)
        self.assertIn('ConfiguredTestPlugin', plugins) # Loaded from config
        self.assertNotIn('DisabledPlugin', plugins) # Should be disabled

        self.assertIsInstance(plugins['DynamicTestPlugin'], AuditPlugin)
        self.assertEqual(plugins['ConfiguredTestPlugin'].setting, 'test') # Check params passed

    async def test_register_trigger_hook(self):
        async def test_async_hook(data):
            data['async_hook_processed'] = True
            return data

        def test_sync_hook(data):
            data['sync_hook_processed'] = True
            return data
        
        register_hook('pre_append', test_sync_hook)
        register_hook('pre_append', test_async_hook)
        
        entry = {"initial": True}
        result = await trigger_event('pre_append', entry)
        
        self.assertIn('sync_hook_processed', result)
        self.assertTrue(result['sync_hook_processed'])
        self.assertIn('async_hook_processed', result)
        self.assertTrue(result['async_hook_processed'])
        
        # Verify log_action for plugin event
        self.mock_log_action.assert_called_with(
            "plugin_event", 
            {"event": "pre_append", "plugins_invoked": list(plugins.keys()), "hooks_count": 2, "final_data_hash": unittest.mock.ANY}
        )
        # Verify metrics for hook invocations and latency
        self.assertGreater(PLUGIN_LATENCY.labels(event='pre_append', plugin='test_sync_hook')._value, 0)
        self.assertGreater(PLUGIN_LATENCY.labels(event='pre_append', plugin='test_async_hook')._value, 0)


    async def test_plugin_process(self):
        # DynamicTestPlugin is loaded and adds 'dynamic_processed' on 'pre_append'
        entry = {"data": "original"}
        result = await trigger_event('pre_append', entry)
        
        self.assertIn('dynamic_processed', result)
        self.assertTrue(result['dynamic_processed'])
        self.assertIn('configured_processed', result) # Configured plugin also ran
        self.assertEqual(result['configured_processed'], 'test')
        self.assertIn('another_dynamic', result) # Another dynamic plugin also ran and modified

        # Test another event for DynamicTestPlugin
        entry_query = {"query_data": "fetch"}
        result_query = await trigger_event('post_query', entry_query)
        self.assertIn('query_augmented', result_query)
        self.assertTrue(result_query['query_augmented'])
        
        # Check plugin invocation metrics
        self.assertGreater(PLUGIN_INVOCATIONS.labels(event='pre_append', plugin='DynamicTestPlugin')._value, 0)
        self.assertGreater(PLUGIN_INVOCATIONS.labels(event='post_query', plugin='DynamicTestPlugin')._value, 0)
        self.assertGreater(PLUGIN_INVOCATIONS.labels(event='pre_append', plugin='ConfiguredTestPlugin')._value, 0)
        self.assertGreater(PLUGIN_INVOCATIONS.labels(event='pre_append', plugin='AnotherDynamicPlugin')._value, 0)


    async def test_sandbox_timeout(self):
        # This test is tricky as it relies on multiprocessing and real timeouts
        class SlowPlugin(AuditPlugin):
            def process(self, entry, event):
                # Simulate a long-running task that exceeds timeout
                start = time.time()
                while time.time() - start < MAX_PLUGIN_TIME_SECONDS * 2: # Twice the timeout
                    _ = 1 + 1 # Burn CPU
                return entry
        plugins['SlowPlugin'] = SlowPlugin()
        
        entry = {"test": "timeout"}
        result = await trigger_event('pre_query', entry)
        
        # The result should be None if the sandbox terminates it
        self.assertIsNone(result) 
        # Check if the error metric was incremented
        self.assertGreater(PLUGIN_ERRORS.labels(event='pre_query', plugin='SlowPlugin', type='timeout')._value, 0)
        self.assertGreater(PLUGIN_LATENCY.labels(event='pre_query', plugin='SlowPlugin')._value, 0) # Should record duration up to timeout


    async def test_policy_block_augment(self):
        global POLICY_CONTROLS
        POLICY_CONTROLS['augment'] = False # Disable augmentation globally
        
        class AugmentPlugin(AuditPlugin):
            def process(self, entry, event):
                entry['extra_data'] = "added"
                return entry
        plugins['AugmentPlugin'] = AugmentPlugin()
        
        entry = {"original": True}
        original_entry_copy = json.loads(json.dumps(entry)) # Deep copy for comparison
        
        result = await trigger_event('post_append', entry)
        
        # Policy is false, so 'extra_data' should NOT be added to the result
        self.assertNotIn('extra_data', result)
        self.assertEqual(result, original_entry_copy) # Should be original data
        
        # Verify PLUGIN_MODIFICATIONS was NOT incremented for 'augment'
        # and PLUGIN_ERRORS was incremented for policy denial.
        self.assertEqual(PLUGIN_MODIFICATIONS.labels(plugin='AugmentPlugin', type='augment')._value, 0)
        self.assertGreater(PLUGIN_ERRORS.labels(event='post_append', plugin='AugmentPlugin', type='policy_denial')._value, 0)


    async def test_policy_allow_redact(self):
        global POLICY_CONTROLS
        POLICY_CONTROLS['redact'] = True # Ensure redaction is allowed

        class RedactPlugin(AuditPlugin):
            def process(self, entry, event):
                if 'private_key' in entry:
                    entry['private_key'] = '[REDACTED]'
                return entry
        plugins['RedactPlugin'] = RedactPlugin()

        entry = {"user": "test", "private_key": "abc123def456"}
        result = await trigger_event('pre_append', entry)
        self.assertEqual(result['private_key'], '[REDACTED]')
        
        # Verify PLUGIN_MODIFICATIONS was incremented for 'redact'
        self.assertGreater(PLUGIN_MODIFICATIONS.labels(plugin='RedactPlugin', type='redact')._value, 0)


    # Fuzz test with hypothesis
    @hypothesis.given(st.dictionaries(st.text(min_size=1), st.recursive(st.integers() | st.text() | st.booleans() | st.none(), lambda children: st.lists(children) | st.dictionaries(st.text(min_size=1), children), max_leaves=10)))
    @hypothesis.settings(max_examples=50, deadline=None, suppress_health_check=[hypothesis.HealthCheck.too_slow]) # Limit examples for faster test, no deadline
    async def test_fuzz_plugin_resilience(self, entry):
        class FuzzPlugin(AuditPlugin):
            def process(self, e, event):
                # Introduce some non-crashing but potentially modifying logic
                if isinstance(e.get('foo'), int):
                    e['foo'] = e['foo'] * 2
                e['fuzz_processed'] = True
                return e
        plugins['fuzz'] = FuzzPlugin()
        
        # Ensure deep copy for comparison as plugins might modify in place
        original_entry_copy = json.loads(json.dumps(entry, sort_keys=True)) # Deep copy
        
        result = await trigger_event('tamper', entry)
        
        # Assert that the result is still a dictionary and contains the fuzz_processed key
        self.assertIsInstance(result, dict)
        self.assertIn('fuzz_processed', result)
        
        # Remove the 'fuzz_processed' key for comparison
        result_without_fuzz = result.copy()
        if 'fuzz_processed' in result_without_fuzz:
            del result_without_fuzz['fuzz_processed']
        
        # The FuzzPlugin explicitly modifies 'foo' and adds 'fuzz_processed'.
        # We need to test the expected outcome.
        expected_result_without_fuzz = original_entry_copy.copy()
        if isinstance(expected_result_without_fuzz.get('foo'), int):
            expected_result_without_fuzz['foo'] = expected_result_without_fuzz['foo'] * 2
        
        self.assertEqual(result_without_fuzz, expected_result_without_fuzz)


    async def test_malicious_plugin_resource_limits(self):
        # This test ensures `resource.setrlimit` is effective.
        # It's specifically for POSIX systems where `resource` module works.
        if sys.platform == "win32":
            self.skipTest("Resource limits (RLIMIT_CPU) are not available on Windows.")

        class MaliciousPlugin(AuditPlugin):
            def process(self, entry, event):
                # This infinite loop will hit the CPU limit
                i = 0
                while True:
                    i += 1
                return entry
        plugins['malicious_cpu'] = MaliciousPlugin()
        
        entry = {"test": "malicious"}
        result = await trigger_event('oscillation', entry)
        
        # The result should be None if the sandbox terminates it
        self.assertIsNone(result)
        # Check that a timeout error was recorded (resource limit usually manifests as a signal/timeout)
        self.assertGreater(PLUGIN_ERRORS.labels(event='oscillation', plugin='malicious_cpu', type='timeout')._value, 0)


    async def test_commercial_plugin_usage_tracking(self):
        billing_plugin_instance = BillingPlugin()
        plugins['BillingPlugin'] = billing_plugin_instance
        
        # Simulate processing several entries (pre_append event)
        for i in range(10):
            entry = {"id": i, "data": f"some_data_{i}", "sensitive_info": "secret"}
            await trigger_event('pre_append', entry)
            await asyncio.sleep(0.001) # Small delay

        # Before billing report, check internal counts
        self.assertEqual(billing_plugin_instance.processed_entries_count, 10)
        self.assertEqual(billing_plugin_instance.redacted_fields_count, 10) # Each entry has sensitive_info
        # Augmented data size depends on the 'additional_context' length
        self.assertEqual(billing_plugin_instance.augmented_data_size, 10 * len("This is augmented data for billing tracking.".encode('utf-8')))


        # Trigger the billing report event
        result = await trigger_event('billing_report', {}) # Data here can be empty
        
        # Verify that log_action was called for the 'plugin_event' of 'billing_report'
        self.mock_log_action.assert_called_with(
            "plugin_event", 
            {"event": "billing_report", "plugins_invoked": list(plugins.keys()), "hooks_count": 0, "final_data_hash": unittest.mock.ANY}
        )

        # After billing report, internal counts should be reset
        self.assertEqual(billing_plugin_instance.processed_entries_count, 0)
        self.assertEqual(billing_plugin_instance.redacted_fields_count, 0)
        self.assertEqual(billing_plugin_instance.augmented_data_size, 0)

        # Check if COMMERCIAL_PLUGIN_USAGE metrics were incremented
        self.assertGreaterEqual(COMMERCIAL_PLUGIN_USAGE.labels(plugin='BillingPlugin', feature='processed_entries')._value, 10)
        self.assertEqual(COMMERCIAL_PLUGIN_USAGE.labels(plugin='BillingPlugin', feature='billing_reported')._value, 1)


# Entry point for running tests
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    unittest.main()