import unittest
import os
import sys
import tempfile
import asyncio
import shutil
import time
import hashlib
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock, call

# Hypothesis imports for property-based testing
import hypothesis
from hypothesis import given, strategies as st
from hypothesis.extra.regex import regex

# Import Prometheus client for clearing metrics
from prometheus_client import CollectorRegistry

# Mock external dependencies before importing the module under test
# This prevents actual filesystem operations or network calls during initial import
sys.modules['dynaconf'] = MagicMock()
sys.modules['prometheus_client'] = MagicMock()
sys.modules['opentelemetry'] = MagicMock()
sys.modules['opentelemetry.sdk.trace'] = MagicMock()
sys.modules['opentelemetry.sdk.trace.export'] = MagicMock()
sys.modules['opentelemetry.trace'] = MagicMock()
sys.modules['watchdog.observers'] = MagicMock()
sys.modules['watchdog.events'] = MagicMock()
sys.modules['aiohttp'] = MagicMock()

# Now import the LLMPluginManager and its associated metrics/constants
# We need to ensure settings is mocked before LLMPluginManager is initialized
# as it reads settings at module level.
from main.llm_plugin_manager import (
    LLMPluginManager, settings,
    PLUGIN_LOADS, PLUGIN_RELOADS, PLUGIN_ERRORS, PLUGIN_HEALTH, PLUGIN_LOAD_LATENCY,
    send_alert, HAS_OPENTELEMETRY, tracer
)

# --- Mocks for Global Dependencies ---
# Mock settings object to control configuration in tests
mock_settings = MagicMock()
mock_settings.PLUGIN_DIR = "mock_plugin_dir"
mock_settings.AUTO_RELOAD = False
mock_settings.ALERT_ENDPOINT = "http://mock-alert.com"
mock_settings.OTLP_ENDPOINT = "http://mock-otel.com"
patch('main.llm_plugin_manager.settings', mock_settings).start()
patch('main.llm_plugin_manager.settings.validators.validate', return_value=None).start()

# Mock Prometheus metrics directly as they are global singletons
patch('main.llm_plugin_manager.Counter').start()
patch('main.llm_plugin_manager.Gauge').start()
patch('main.llm_plugin_manager.Histogram').start()

# Mock OpenTelemetry components
mock_tracer = MagicMock()
mock_tracer.start_as_current_span.return_value.__enter__.return_value = MagicMock()
patch('main.llm_plugin_manager.tracer', mock_tracer).start()
patch('main.llm_plugin_manager.HAS_OPENTELEMETRY', True).start()
patch('main.llm_plugin_manager.trace.get_tracer_provider', return_value=MagicMock()).start()
patch('main.llm_plugin_manager.trace.set_tracer_provider', return_value=None).start()
patch('main.llm_plugin_manager.BatchSpanProcessor', return_value=MagicMock()).start()
patch('main.llm_plugin_manager.OTLPSpanExporter', return_value=MagicMock()).start()
patch('main.llm_plugin_manager.ConsoleSpanExporter', return_value=MagicMock()).start()
patch('main.llm_plugin_manager.TracerProvider', return_value=MagicMock()).start()

# Mock send_alert
mock_send_alert = AsyncMock()
patch('main.llm_plugin_manager.send_alert', mock_send_alert).start()

# Mock time.perf_counter for latency metrics
patch('main.llm_plugin_manager.time.perf_counter', side_effect=[0.0, 0.1, 0.0, 0.2, 0.0, 0.3]).start() # Simulate increasing times

# --- Test Suite ---
class TestLLMPluginManager(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Create a temporary directory for plugins for each test
        self.temp_plugin_dir = Path(tempfile.mkdtemp())
        mock_settings.PLUGIN_DIR = str(self.temp_plugin_dir)
        mock_settings.AUTO_RELOAD = False # Disable auto-reload by default for most tests

        # Reset Prometheus metrics for each test
        # This requires accessing the underlying _metrics dictionary or using a mock for the metric objects themselves.
        # Since we patched Counter/Gauge/Histogram, we can reset their mocks.
        PLUGIN_LOADS.reset_mock()
        PLUGIN_RELOADS.reset_mock()
        PLUGIN_ERRORS.reset_mock()
        PLUGIN_HEALTH.reset_mock()
        PLUGIN_LOAD_LATENCY.reset_mock()

        # Reset other mocks
        mock_send_alert.reset_mock()
        mock_tracer.reset_mock()

        # Patch os.listdir to control what files are seen in plugin_dir
        self.mock_listdir_patch = patch('main.llm_plugin_manager.os.listdir', return_value=[]).start()
        # Patch importlib.util.spec_from_file_location and spec.loader.exec_module
        # to simulate module loading without actual file system reads of plugin content
        self.mock_spec_from_file_location = patch('main.llm_plugin_manager.importlib.util.spec_from_file_location').start()
        self.mock_exec_module = patch('main.llm_plugin_manager.spec.loader.exec_module', new_callable=AsyncMock).start()
        self.mock_os_path_exists = patch('main.llm_plugin_manager.os.path.exists', return_value=True).start()
        self.mock_asyncio_to_thread = patch('main.llm_plugin_manager.asyncio.to_thread', new_callable=AsyncMock).start()
        self.mock_asyncio_to_thread.side_effect = lambda func, *args, **kwargs: func(*args, **kwargs) # Pass through sync calls

        # Initialize manager for each test
        self.manager = LLMPluginManager()
        # Await the initial scan task which is started in __init__
        self.loop.run_until_complete(self.manager._scan_and_load_plugins_on_init)

    async def asyncTearDown(self):
        # Ensure manager's watcher is stopped and temp dir is cleaned
        self.manager.close()
        shutil.rmtree(self.temp_plugin_dir)
        patch.stopall() # Stop all patches

    # --- Helper to create mock plugin module ---
    def _create_mock_plugin_module(self, modname: str, has_get_provider: bool = False, has_register: bool = False, raises_error: bool = False, provider_name: Optional[str] = None):
        mock_module = MagicMock()
        mock_module.__name__ = modname
        mock_module.__file__ = str(self.temp_plugin_dir / f"{modname}.py")

        if has_get_provider:
            mock_provider = MagicMock()
            mock_provider.name = provider_name if provider_name else modname
            mock_provider.health_check = AsyncMock(return_value=True) # Assume providers have health_check
            mock_module.get_provider.return_value = mock_provider
        else:
            del mock_module.get_provider

        if has_register:
            mock_module.register = MagicMock()
        else:
            del mock_module.register

        if raises_error:
            self.mock_exec_module.side_effect = Exception("Simulated plugin execution error")
        else:
            self.mock_exec_module.side_effect = None # Reset side effect

        # Configure mock_spec_from_file_location to return this mock module
        mock_spec = MagicMock()
        mock_spec.loader.exec_module = self.mock_exec_module
        self.mock_spec_from_file_location.return_value = mock_spec

        return mock_module

    # --- Test Cases ---

    async def test_initial_load_empty_dir(self):
        # os.listdir is mocked to return empty list in setUp
        self.assertEqual(self.manager.list_providers(), [])
        self.mock_listdir_patch.assert_called_once_with(str(self.temp_plugin_dir))
        self.mock_send_alert.assert_not_called()
        self.mock_tracer.start_as_current_span.assert_not_called()

    async def test_load_plugin_with_get_provider_success(self):
        self.mock_listdir_patch.return_value = ["test_provider.py"]
        self._create_mock_plugin_module("test_provider", has_get_provider=True)
        
        await self.manager._scan_and_load_plugins() # Trigger scan

        self.assertIn("test_provider", self.manager.list_providers())
        self.mock_exec_module.assert_called_once()
        PLUGIN_LOADS.labels.assert_called_once_with(plugin_name="test_provider")
        PLUGIN_LOADS.labels.return_value.inc.assert_called_once()
        PLUGIN_HEALTH.labels.assert_called_once_with(plugin_name="test_provider")
        PLUGIN_HEALTH.labels.return_value.set.assert_called_once_with(1)
        PLUGIN_LOAD_LATENCY.labels.assert_called_once_with(plugin_name="test_provider")
        PLUGIN_LOAD_LATENCY.labels.return_value.observe.assert_called_once()
        mock_tracer.start_as_current_span.assert_called_once_with("load_plugin_test_provider")
        mock_tracer.start_as_current_span.return_value.__enter__.return_value.set_status.assert_called_once_with(unittest.mock.ANY) # OK status

    async def test_load_plugin_with_register_success(self):
        self.mock_listdir_patch.return_value = ["register_provider.py"]
        mock_module = self._create_mock_plugin_module("register_provider", has_register=True)
        
        await self.manager._scan_and_load_plugins()

        mock_module.register.assert_called_once_with(self.manager) # Verify register was called with manager
        # Assuming register calls add_provider, so provider should be in registry
        self.assertIn("register_provider", self.manager.list_providers()) # Assuming register adds it with its name
        # Metrics for register() are implicitly covered if register() calls add_provider() which is then handled.
        # For this test, we verify register() was called.

    async def test_load_plugin_execution_error(self):
        self.mock_listdir_patch.return_value = ["error_plugin.py"]
        self._create_mock_plugin_module("error_plugin", has_get_provider=True, raises_error=True)
        
        await self.manager._scan_and_load_plugins()

        self.assertNotIn("error_plugin", self.manager.list_providers())
        PLUGIN_ERRORS.labels.assert_called_once_with(plugin_name="error_plugin", error_type="Exception")
        PLUGIN_ERRORS.labels.return_value.inc.assert_called_once()
        PLUGIN_HEALTH.labels.assert_called_once_with(plugin_name="error_plugin")
        PLUGIN_HEALTH.labels.return_value.set.assert_called_once_with(0) # Should be unhealthy
        mock_send_alert.assert_called_once() # Alert should be sent
        mock_tracer.start_as_current_span.return_value.__enter__.return_value.set_status.assert_called_once_with(unittest.mock.ANY) # Error status

    async def test_load_plugin_no_entry_point(self):
        self.mock_listdir_patch.return_value = ["no_entry_plugin.py"]
        self._create_mock_plugin_module("no_entry_plugin") # No get_provider or register
        
        await self.manager._scan_and_load_plugins()

        self.assertNotIn("no_entry_plugin", self.manager.list_providers())
        # No specific error metric for this, just a warning log.
        self.mock_send_alert.assert_not_called()
        mock_tracer.start_as_current_span.assert_called_once() # Span is started
        mock_tracer.start_as_current_span.return_value.__enter__.return_value.set_status.assert_called_once_with(unittest.mock.ANY) # Error status for ValueError

    async def test_integrity_check_failure(self):
        self.mock_listdir_patch.return_value = ["tampered_plugin.py"]
        self._create_mock_plugin_module("tampered_plugin", has_get_provider=True)
        
        # Patch _get_expected_hash to return a hash, and _verify_integrity to fail
        with patch.object(self.manager, "_get_expected_hash", return_value="expected_hash"):
            with patch.object(self.manager, "_verify_integrity", return_value=False):
                await self.manager._scan_and_load_plugins()

                self.assertNotIn("tampered_plugin", self.manager.list_providers())
                PLUGIN_ERRORS.labels.assert_called_once_with(plugin_name="tampered_plugin", error_type="integrity_failure")
                PLUGIN_ERRORS.labels.return_value.inc.assert_called_once()
                mock_send_alert.assert_called_once() # Alert should be sent for critical integrity failure
                mock_tracer.start_as_current_span.assert_called_once_with("load_plugin_tampered_plugin")
                mock_tracer.start_as_current_span.return_value.__enter__.return_value.set_status.assert_called_once_with(unittest.mock.ANY) # Error status

    async def test_reload_plugins(self):
        # Load initial plugin
        self.mock_listdir_patch.return_value = ["initial_plugin.py"]
        self._create_mock_plugin_module("initial_plugin", has_get_provider=True)
        await self.manager._scan_and_load_plugins()
        self.assertIn("initial_plugin", self.manager.list_providers())
        
        # Simulate adding a new plugin file and then reloading
        self.mock_listdir_patch.return_value = ["initial_plugin.py", "new_plugin.py"]
        self._create_mock_plugin_module("new_plugin", has_get_provider=True) # Configure new plugin mock
        
        await self.manager.reload()

        self.assertIn("initial_plugin", self.manager.list_providers())
        self.assertIn("new_plugin", self.manager.list_providers())
        self.assertEqual(len(self.manager.list_providers()), 2)
        PLUGIN_RELOADS.labels.assert_called_once_with(plugin_name="all")
        PLUGIN_RELOADS.labels.return_value.inc.assert_called_once()
        # Verify initial_plugin was marked unhealthy then healthy again
        PLUGIN_HEALTH.labels.assert_any_call(plugin_name="initial_plugin")
        PLUGIN_HEALTH.labels.return_value.set.assert_any_call(0)
        PLUGIN_HEALTH.labels.return_value.set.assert_any_call(1)
        # Verify new_plugin was loaded and healthy
        PLUGIN_HEALTH.labels.assert_any_call(plugin_name="new_plugin")
        PLUGIN_HEALTH.labels.return_value.set.assert_any_call(1)
        self.assertEqual(mock_tracer.start_as_current_span.call_count, 3) # Initial load + 2 reloads

    async def test_reload_modified_plugin(self):
        # Load initial plugin
        self.mock_listdir_patch.return_value = ["mod_plugin.py"]
        mock_mod_plugin = self._create_mock_plugin_module("mod_plugin", has_get_provider=True)
        await self.manager._scan_and_load_plugins()
        self.assertIn("mod_plugin", self.manager.list_providers())
        
        # Simulate modification by changing mock_mod_plugin's behavior
        mock_mod_plugin.get_provider.return_value.name = "mod_plugin_v2"
        
        await self.manager.reload()
        
        self.assertIn("mod_plugin_v2", self.manager.list_providers()) # Should reflect new name
        self.assertNotIn("mod_plugin", self.manager.list_providers()) # Old name should be gone
        
    async def test_add_provider(self):
        mock_provider = MagicMock()
        mock_provider.name = "dynamic_provider"
        self.manager.add_provider("dynamic_provider", mock_provider)
        self.assertIn("dynamic_provider", self.manager.list_providers())
        self.assertEqual(self.manager.get_provider("dynamic_provider"), mock_provider)

    async def test_concurrent_scan_and_load(self):
        # Simulate multiple files for concurrent loading
        self.mock_listdir_patch.return_value = [f"plugin_{i}.py" for i in range(5)]
        for i in range(5):
            self._create_mock_plugin_module(f"plugin_{i}", has_get_provider=True)

        # Run _scan_and_load_plugins concurrently (though it has an internal lock)
        tasks = [asyncio.create_task(self.manager._scan_and_load_plugins()) for _ in range(2)]
        await asyncio.gather(*tasks)

        self.assertEqual(len(self.manager.list_providers()), 5) # All 5 should be loaded
        self.assertEqual(self.mock_exec_module.call_count, 5) # Each module executed once
        # Lock should prevent actual concurrent loading of individual plugins
        self.assertEqual(self.manager.lock.acquire.call_count, 2) # Lock acquired twice by scan calls
        self.assertEqual(self.manager.lock.release.call_count, 2) # Lock released twice

    async def test_concurrent_reload(self):
        # Load initial plugins
        self.mock_listdir_patch.return_value = ["p1.py", "p2.py"]
        self._create_mock_plugin_module("p1", has_get_provider=True)
        self._create_mock_plugin_module("p2", has_get_provider=True)
        await self.manager._scan_and_load_plugins()
        self.assertEqual(len(self.manager.list_providers()), 2)

        # Run reload concurrently
        tasks = [asyncio.create_task(self.manager.reload()) for _ in range(3)]
        await asyncio.gather(*tasks)

        self.assertEqual(len(self.manager.list_providers()), 2) # Should still have 2 plugins
        self.assertEqual(PLUGIN_RELOADS.labels.return_value.inc.call_count, 3) # Reloads should be counted
        self.assertEqual(self.manager.lock.acquire.call_count, 1 + 3) # Initial scan + 3 reloads
        self.assertEqual(self.manager.lock.release.call_count, 1 + 3)

    async def test_file_watcher_triggers_reload(self):
        mock_settings.AUTO_RELOAD = True
        # Re-initialize manager with auto_reload enabled
        self.manager.close() # Close previous manager
        self.manager = LLMPluginManager()
        self.loop.run_until_complete(self.manager._scan_and_load_plugins_on_init)
        
        # Mock the Observer and PluginEventHandler
        mock_observer = MagicMock()
        mock_event_handler = MagicMock()
        patch('main.llm_plugin_manager.Observer', return_value=mock_observer).start()
        patch('main.llm_plugin_manager.PluginEventHandler', return_value=mock_event_handler).start()
        
        # Re-call _start_watcher to use our mocks
        self.manager._start_watcher()
        mock_observer.schedule.assert_called_once_with(mock_event_handler, str(self.temp_plugin_dir), recursive=False)
        mock_observer.start.assert_called_once()

        # Simulate a file modification event
        mock_event = MagicMock(spec=type('Event', (), {'is_directory': False, 'src_path': str(self.temp_plugin_dir / 'modified_plugin.py')}))
        
        # Patch manager.reload to verify it's called
        with patch.object(self.manager, 'reload', new_callable=AsyncMock) as mock_reload_method:
            mock_event_handler.on_modified(mock_event)
            await asyncio.sleep(0.01) # Allow scheduled task to run
            mock_reload_method.assert_awaited_once()

    @given(plugin_content=st.text(min_size=10, max_size=100, alphabet=st.characters(blacklist_categories=('Cs',))),
           plugin_name=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=('L', 'N'))).map(lambda s: s.replace(' ', '_')))
    async def test_fuzz_plugin_loading_no_crash(self, plugin_content, plugin_name):
        # This test ensures that even with fuzzed plugin content, the manager doesn't crash.
        # It won't verify functionality, but rather stability.
        self.mock_listdir_patch.return_value = [f"{plugin_name}.py"]
        
        # Create a dummy file with fuzzed content
        plugin_file_path = self.temp_plugin_dir / f"{plugin_name}.py"
        plugin_file_path.write_text(f"def get_provider():\n    raise Exception('{plugin_content}')\n") # Force an error inside plugin

        # Patch _get_expected_hash and _verify_integrity to allow loading
        with patch.object(self.manager, "_get_expected_hash", return_value=None):
            with patch.object(self.manager, "_verify_integrity", return_value=True):
                # We expect an error to be logged and the plugin not to be loaded
                await self.manager._scan_and_load_plugins()
                
                self.assertNotIn(plugin_name, self.manager.list_providers())
                PLUGIN_ERRORS.labels.assert_called_with(plugin_name=plugin_name, error_type=unittest.mock.ANY)
                PLUGIN_HEALTH.labels.assert_called_with(plugin_name=plugin_name)
                PLUGIN_HEALTH.labels.return_value.set.assert_called_with(0) # Should be unhealthy
                mock_send_alert.assert_called() # Alert should be sent
                # No crash should occur

    async def test_load_plugin_integrity_check_true_no_expected_hash(self):
        # Test case where _verify_integrity returns True (because expected_hash is None)
        self.mock_listdir_patch.return_value = ["no_hash_plugin.py"]
        self._create_mock_plugin_module("no_hash_plugin", has_get_provider=True)

        with patch.object(self.manager, "_get_expected_hash", return_value=None):
            with patch.object(self.manager, "_verify_integrity", return_value=True) as mock_verify:
                await self.manager._scan_and_load_plugins()
                self.assertIn("no_hash_plugin", self.manager.list_providers())
                mock_verify.assert_called_once_with(unittest.mock.ANY, None) # Called with None expected_hash
                # No error metrics or alerts related to integrity should be fired
                PLUGIN_ERRORS.labels.assert_not_called()
                mock_send_alert.assert_not_called()

if __name__ == '__main__':
    unittest.main()
