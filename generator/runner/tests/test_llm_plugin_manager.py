import asyncio
import contextlib
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

from runner.llm_plugin_manager import (
    LLM_PROVIDER_HEALTH,
    PLUGIN_ERRORS,
    PLUGIN_LOADS,
    LLMPluginManager,
    settings,
)


class TestLLMPluginManager(IsolatedAsyncioTestCase):
    """
    Tests for the LLMPluginManager using real async flows under pytest/TESTING.

    Key points:
    - Uses asyncSetUp/asyncTearDown (no manual self.loop/run_until_complete).
    - Uses a real temporary plugin directory passed into LLMPluginManager.
    - Relies on MagicMock metrics provided by llm_plugin_manager in TESTING mode.
    """

    async def asyncSetUp(self):
        # Isolated temp directory for each test
        self.temp_plugin_dir = Path(tempfile.mkdtemp())
        settings.PLUGIN_DIR = str(self.temp_plugin_dir)

        # Reset metric mocks between tests
        if hasattr(PLUGIN_LOADS, "labels"):
            PLUGIN_LOADS.labels.reset_mock()
        if hasattr(PLUGIN_ERRORS, "labels"):
            PLUGIN_ERRORS.labels.reset_mock()
        if hasattr(LLM_PROVIDER_HEALTH, "labels"):
            LLM_PROVIDER_HEALTH.labels.reset_mock()

        # Create manager bound to this directory
        self.manager = LLMPluginManager(plugin_dir=self.temp_plugin_dir)

        # Wait for initial scan task to complete with timeout (it will see an empty dir)
        load_task = getattr(self.manager, "_load_task", None)
        if load_task is not None:
            try:
                await asyncio.wait_for(load_task, timeout=5.0)
            except asyncio.TimeoutError:
                # If initial load times out, cancel it to prevent hanging
                load_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await load_task
                raise AssertionError("LLMPluginManager initial load timed out after 5 seconds")

    async def asyncTearDown(self):
        # Clean up manager + temp dir with timeout protection
        try:
            await asyncio.wait_for(self.manager.close(), timeout=3.0)
        except asyncio.TimeoutError:
            # If close times out, force cancel any remaining tasks
            if hasattr(self.manager, "_load_task") and self.manager._load_task:
                self.manager._load_task.cancel()
            if (
                hasattr(self.manager, "_watcher_consumer_task")
                and self.manager._watcher_consumer_task
            ):
                self.manager._watcher_consumer_task.cancel()
        except Exception as e:
            # Log but don't fail the test on cleanup errors
            print(f"Error during cleanup: {e}")
        finally:
            shutil.rmtree(self.temp_plugin_dir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _write_valid_provider(self, name: str = "test_provider") -> Path:
        """
        Write a minimal, valid provider plugin:
        - filename: {name}_provider.py
        - exports get_provider() with async call + health_check
        """
        path = self.temp_plugin_dir / f"{name}_provider.py"
        path.write_text(
            "import asyncio\n"
            "from types import SimpleNamespace\n"
            "\n"
            "async def _call(*args, **kwargs):\n"
            "    return 'ok'\n"
            "\n"
            "async def _health_check():\n"
            "    return True\n"
            "\n"
            f"def get_provider():\n"
            f"    return SimpleNamespace(name='{name}', call=_call, health_check=_health_check)\n"
        )
        return path

    def _write_no_entry_provider(self, name: str = "no_entry") -> Path:
        """
        Write a plugin missing get_provider() to trigger PluginValidationError.
        """
        path = self.temp_plugin_dir / f"{name}_provider.py"
        path.write_text("import asyncio\n" "async def dummy():\n" "    return 'x'\n")
        return path

    # ------------------------------------------------------------------ #
    # Tests
    # ------------------------------------------------------------------ #

    async def test_initial_load_empty_dir(self):
        """
        With an empty plugin dir, no providers should be registered.
        """
        self.assertEqual(self.manager.list_providers(), [])

    async def test_load_plugin_with_get_provider_success(self):
        """
        A well-formed *_provider.py with get_provider() registers correctly
        and updates metrics.
        """
        self._write_valid_provider("test_provider")

        # Clear metrics before scan
        PLUGIN_LOADS.labels.reset_mock()
        LLM_PROVIDER_HEALTH.labels.reset_mock()

        await self.manager._scan_and_load_plugins()

        providers = self.manager.list_providers()
        self.assertIn("test_provider", providers)

        # Validate metrics interactions (MagicMocks in TESTING)
        PLUGIN_LOADS.labels.assert_called_once_with(plugin="test_provider")
        LLM_PROVIDER_HEALTH.labels.assert_called_once_with(provider="test_provider")
        LLM_PROVIDER_HEALTH.labels.return_value.set.assert_called_with(1)

    async def test_load_plugin_execution_error_records_failure(self):
        """
        If module execution fails, the plugin is not registered and
        PLUGIN_ERRORS is updated.
        """
        plugin_path = self._write_valid_provider("error_plugin")

        # Patch importlib spec/loader to raise on exec_module
        with patch(
            "runner.llm_plugin_manager.importlib.util.spec_from_file_location"
        ) as mock_spec_from_file:
            mock_spec = MagicMock()
            mock_loader = MagicMock()
            mock_loader.exec_module.side_effect = RuntimeError("boom")
            mock_spec.loader = mock_loader
            mock_spec_from_file.return_value = mock_spec

            PLUGIN_ERRORS.labels.reset_mock()

            await self.manager._scan_and_load_plugins()

        self.assertNotIn("error_plugin", self.manager.list_providers())
        PLUGIN_ERRORS.labels.assert_called_once()
        args, kwargs = PLUGIN_ERRORS.labels.call_args
        # plugin arg is derived from stem: "error_plugin_provider"
        self.assertIn("error_plugin", kwargs.get("plugin", "") or args[0])
        self.assertEqual(
            (kwargs.get("error_type") or args[1]),
            "RuntimeError",
        )

    async def test_load_plugin_no_entry_point_records_validation_error(self):
        """
        Plugin without get_provider() should raise PluginValidationError path
        and mark metrics accordingly.
        """
        self._write_no_entry_provider("no_entry")

        PLUGIN_ERRORS.labels.reset_mock()

        await self.manager._scan_and_load_plugins()

        self.assertEqual(self.manager.list_providers(), [])
        PLUGIN_ERRORS.labels.assert_called_once()
        _, kwargs = PLUGIN_ERRORS.labels.call_args
        self.assertEqual(kwargs.get("error_type"), "PluginValidationError")

    async def test_integrity_check_failure_blocks_load(self):
        """
        If _verify_integrity returns False, plugin must not load and
        PluginIntegrityError should be recorded.
        """
        self._write_valid_provider("tampered")

        PLUGIN_ERRORS.labels.reset_mock()

        with patch.object(
            self.manager, "_get_expected_hash", return_value="expected_hash"
        ), patch.object(self.manager, "_verify_integrity", return_value=False) as mock_verify:
            await self.manager._scan_and_load_plugins()

        self.assertNotIn("tampered", self.manager.list_providers())
        mock_verify.assert_called()
        PLUGIN_ERRORS.labels.assert_called_once()
        _, kwargs = PLUGIN_ERRORS.labels.call_args
        self.assertEqual(kwargs.get("error_type"), "PluginIntegrityError")

    async def test_add_provider_and_get_provider_manual_registry(self):
        """
        Direct registry manipulation (for dynamic providers) works as expected.
        """
        provider = SimpleNamespace(name="dynamic", call=asyncio.sleep, health_check=asyncio.sleep)
        self.manager.registry["dynamic"] = provider

        self.assertIn("dynamic", self.manager.list_providers())
        self.assertIs(self.manager.get_provider("dynamic"), provider)

    async def test_reload_plugins_logic(self):
        """
        reload() clears existing providers, rescans the directory, and
        loads new providers.
        """
        # Initial: one plugin
        self._write_valid_provider("initial")
        await self.manager._scan_and_load_plugins()
        self.assertIn("initial", self.manager.list_providers())

        # Add second plugin and call reload()
        self._write_valid_provider("new_one")
        await self.manager.reload()

        providers = set(self.manager.list_providers())
        self.assertIn("initial", providers)
        self.assertIn("new_one", providers)
