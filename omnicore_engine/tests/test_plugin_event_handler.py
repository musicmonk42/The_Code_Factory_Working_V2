"""
Test suite for omnicore_engine/plugin_event_handler.py
Tests file system event handling for plugin hot-reloading.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Add the parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnicore_engine.plugin_event_handler import PluginEventHandler, start_plugin_observer


class TestPluginEventHandler:
    """Test the PluginEventHandler class"""

    @pytest.fixture
    def mock_registry(self):
        """Create a mock plugin registry"""
        registry = Mock()
        registry.plugins = {"execution": {"test_plugin": Mock()}, "validation": {}}
        registry.load_from_directory = AsyncMock()
        registry.db = Mock()
        registry.db.save_plugin_legacy = AsyncMock()
        return registry

    @pytest.fixture
    def handler(self, mock_registry):
        """Create a PluginEventHandler instance"""
        with patch("omnicore_engine.plugin_event_handler.settings") as mock_settings:
            mock_settings.plugin_dir = "/tmp/plugins"
            handler = PluginEventHandler(mock_registry)
            # Create a new event loop for testing
            handler._loop = asyncio.new_event_loop()
            return handler

    def test_initialization(self, mock_registry):
        """Test PluginEventHandler initialization"""
        with patch("omnicore_engine.plugin_event_handler.settings") as mock_settings:
            mock_settings.plugin_dir = "/default/plugins"

            # Test with default plugin_dir
            handler = PluginEventHandler(mock_registry)
            assert handler.registry == mock_registry
            assert handler.plugin_dir == "/default/plugins"
            assert handler.last_modified_times == {}

            # Test with custom plugin_dir
            handler = PluginEventHandler(mock_registry, "/custom/plugins")
            assert handler.plugin_dir == "/custom/plugins"

    def test_schedule_async_task_running_loop(self, handler):
        """Test scheduling async task with running loop"""
        mock_coro = Mock()

        with patch("asyncio.create_task") as mock_create_task:
            handler._loop.is_running = Mock(return_value=True)
            handler._schedule_async_task(mock_coro)

            mock_create_task.assert_called_once_with(mock_coro)

    def test_schedule_async_task_not_running_loop(self, handler):
        """Test scheduling async task with non-running loop"""
        mock_coro = Mock()

        handler._loop.is_running = Mock(return_value=False)
        handler._loop.run_until_complete = Mock()

        with patch("omnicore_engine.plugin_event_handler.logger") as mock_logger:
            handler._schedule_async_task(mock_coro)

            mock_logger.warning.assert_called()
            handler._loop.run_until_complete.assert_called_once_with(mock_coro)

    def test_schedule_async_task_error(self, handler):
        """Test error handling in schedule_async_task"""
        mock_coro = Mock()

        handler._loop.is_running = Mock(side_effect=Exception("Loop error"))

        with patch("omnicore_engine.plugin_event_handler.logger") as mock_logger:
            handler._schedule_async_task(mock_coro)

            mock_logger.error.assert_called()
            assert "Failed to schedule" in mock_logger.error.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_plugin_file_event_async_modified(self, handler, mock_registry):
        """Test handling modified plugin file"""
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            temp_file = f.name
            f.write(b"# Test plugin\n")

        try:
            # Setup mock plugin
            mock_plugin = Mock()
            mock_plugin.meta.name = "test_plugin"
            mock_plugin.meta.kind = "execution"
            mock_plugin.meta.version = "1.0.0"
            mock_plugin.meta.description = "Test"
            mock_plugin.meta.safe = True
            mock_plugin.meta.source = "test"
            mock_plugin.meta.params_schema = {}
            mock_plugin.fn = lambda: "test"

            plugin_name = Path(temp_file).stem
            mock_registry.plugins["execution"][plugin_name] = mock_plugin

            await handler._handle_plugin_file_event_async(temp_file, "modified")

            # Verify registry was reloaded
            mock_registry.load_from_directory.assert_called_once_with(handler.plugin_dir)

            # Verify plugin was saved to DB
            mock_registry.db.save_plugin_legacy.assert_called_once()
            save_call = mock_registry.db.save_plugin_legacy.call_args[0][0]
            assert save_call["name"] == "test_plugin"
            assert save_call["kind"] == "execution"

            # Verify mtime was updated
            assert temp_file in handler.last_modified_times
        finally:
            os.unlink(temp_file)

    @pytest.mark.asyncio
    async def test_handle_plugin_file_event_async_created(self, handler, mock_registry):
        """Test handling created plugin file"""
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            temp_file = f.name

        try:
            mock_plugin = Mock()
            mock_plugin.meta.name = "new_plugin"
            mock_plugin.meta.kind = "validation"
            mock_plugin.meta.version = "1.0.0"
            mock_plugin.meta.description = "New"
            mock_plugin.meta.safe = True
            mock_plugin.meta.source = "test"
            mock_plugin.meta.params_schema = {}
            mock_plugin.fn = lambda: "new"

            plugin_name = Path(temp_file).stem
            mock_registry.plugins["validation"][plugin_name] = mock_plugin

            await handler._handle_plugin_file_event_async(temp_file, "created")

            mock_registry.load_from_directory.assert_called_once()
            mock_registry.db.save_plugin_legacy.assert_called_once()
        finally:
            os.unlink(temp_file)

    @pytest.mark.asyncio
    async def test_handle_plugin_file_event_async_skip_unchanged(self, handler):
        """Test skipping reload for unchanged mtime"""
        temp_file = "/tmp/test_plugin.py"
        current_mtime = 1234567890.0

        handler.last_modified_times[temp_file] = current_mtime

        with patch("os.path.realpath", return_value=temp_file):
            with patch("os.path.getmtime", return_value=current_mtime):
                with patch("omnicore_engine.plugin_event_handler.logger") as mock_logger:
                    await handler._handle_plugin_file_event_async(temp_file, "modified")

                    mock_logger.debug.assert_called()
                    assert "Skipping redundant" in mock_logger.debug.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_plugin_file_event_async_no_load_method(self, handler, mock_registry):
        """Test handling when registry lacks load_from_directory method"""
        delattr(mock_registry, "load_from_directory")

        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            temp_file = f.name

        try:
            with patch("omnicore_engine.plugin_event_handler.logger") as mock_logger:
                await handler._handle_plugin_file_event_async(temp_file, "modified")

                mock_logger.warning.assert_called()
                assert (
                    "does not have an async 'load_from_directory'"
                    in mock_logger.warning.call_args[0][0]
                )
        finally:
            os.unlink(temp_file)

    @pytest.mark.asyncio
    async def test_handle_plugin_file_event_async_plugin_not_found(self, handler, mock_registry):
        """Test handling when plugin not found in registry after reload"""
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            temp_file = f.name

        try:
            # Plugin not in registry
            mock_registry.plugins = {"execution": {}, "validation": {}}

            with patch("omnicore_engine.plugin_event_handler.logger") as mock_logger:
                await handler._handle_plugin_file_event_async(temp_file, "created")

                mock_logger.warning.assert_called()
                assert "Could not find plugin" in mock_logger.warning.call_args[0][0]
        finally:
            os.unlink(temp_file)

    @pytest.mark.asyncio
    async def test_handle_plugin_file_event_async_no_db(self, handler, mock_registry):
        """Test handling when DB is not available"""
        mock_registry.db = None

        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            temp_file = f.name

        try:
            mock_plugin = Mock()
            mock_plugin.meta.name = "test_plugin"
            plugin_name = Path(temp_file).stem
            mock_registry.plugins["execution"][plugin_name] = mock_plugin

            with patch("omnicore_engine.plugin_event_handler.logger") as mock_logger:
                await handler._handle_plugin_file_event_async(temp_file, "modified")

                mock_logger.warning.assert_called()
                assert "DB not available" in mock_logger.warning.call_args[0][0]
        finally:
            os.unlink(temp_file)

    @pytest.mark.asyncio
    async def test_handle_plugin_file_event_async_error(self, handler):
        """Test error handling in _handle_plugin_file_event_async"""
        with patch("os.path.realpath", side_effect=Exception("Path error")):
            with patch("omnicore_engine.plugin_event_handler.logger") as mock_logger:
                await handler._handle_plugin_file_event_async("/tmp/test.py", "modified")

                mock_logger.error.assert_called()
                assert "Error during async plugin" in mock_logger.error.call_args[0][0]

    def test_on_modified_python_file(self, handler):
        """Test on_modified event for Python file"""
        event = Mock()
        event.is_directory = False
        event.src_path = "/tmp/test_plugin.py"

        with patch.object(handler, "_schedule_async_task") as mock_schedule:
            handler.on_modified(event)

            mock_schedule.assert_called_once()
            # Check that the coroutine was created
            call_args = mock_schedule.call_args[0][0]
            assert asyncio.iscoroutine(call_args)

    def test_on_modified_directory(self, handler):
        """Test on_modified ignores directories"""
        event = Mock()
        event.is_directory = True
        event.src_path = "/tmp/plugins"

        with patch.object(handler, "_schedule_async_task") as mock_schedule:
            handler.on_modified(event)

            mock_schedule.assert_not_called()

    def test_on_modified_non_python_file(self, handler):
        """Test on_modified ignores non-Python files"""
        event = Mock()
        event.is_directory = False
        event.src_path = "/tmp/readme.txt"

        with patch.object(handler, "_schedule_async_task") as mock_schedule:
            handler.on_modified(event)

            mock_schedule.assert_not_called()

    def test_on_created_python_file(self, handler):
        """Test on_created event for Python file"""
        event = Mock()
        event.is_directory = False
        event.src_path = "/tmp/new_plugin.py"

        with patch.object(handler, "_schedule_async_task") as mock_schedule:
            handler.on_created(event)

            mock_schedule.assert_called_once()
            call_args = mock_schedule.call_args[0][0]
            assert asyncio.iscoroutine(call_args)

    def test_on_created_directory(self, handler):
        """Test on_created ignores directories"""
        event = Mock()
        event.is_directory = True
        event.src_path = "/tmp/new_dir"

        with patch.object(handler, "_schedule_async_task") as mock_schedule:
            handler.on_created(event)

            mock_schedule.assert_not_called()


class TestStartPluginObserver:
    """Test the start_plugin_observer function"""

    @patch("omnicore_engine.plugin_event_handler.Observer")
    @patch("omnicore_engine.plugin_event_handler.PluginEventHandler")
    def test_start_observer_success(self, mock_handler_class, mock_observer_class):
        """Test successful observer start"""
        mock_registry = Mock()
        plugin_dir = "/tmp/plugins"

        mock_observer = Mock()
        mock_observer_class.return_value = mock_observer

        mock_handler = Mock()
        mock_handler_class.return_value = mock_handler

        with patch("omnicore_engine.plugin_event_handler.logger") as mock_logger:
            start_plugin_observer(mock_registry, plugin_dir)

            mock_handler_class.assert_called_once_with(mock_registry, plugin_dir)
            mock_observer.schedule.assert_called_once_with(
                mock_handler, plugin_dir, recursive=False
            )
            mock_observer.start.assert_called_once()
            mock_logger.info.assert_called()
            assert "Watchdog started" in mock_logger.info.call_args[0][0]

    @patch("omnicore_engine.plugin_event_handler.Observer")
    @patch("omnicore_engine.plugin_event_handler.PluginEventHandler")
    def test_start_observer_failure(self, mock_handler_class, mock_observer_class):
        """Test observer start failure"""
        mock_registry = Mock()
        plugin_dir = "/tmp/plugins"

        mock_observer = Mock()
        mock_observer.start.side_effect = Exception("Start failed")
        mock_observer_class.return_value = mock_observer

        mock_handler = Mock()
        mock_handler_class.return_value = mock_handler

        with patch("omnicore_engine.plugin_event_handler.logger") as mock_logger:
            start_plugin_observer(mock_registry, plugin_dir)

            mock_logger.error.assert_called()
            assert "Failed to start watchdog" in mock_logger.error.call_args[0][0]


class TestIntegration:
    """Integration tests for plugin event handling"""

    @pytest.mark.asyncio
    async def test_full_plugin_reload_flow(self):
        """Test complete flow from file change to plugin reload"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a test plugin file
            plugin_file = Path(temp_dir) / "test_plugin.py"
            plugin_file.write_text(
                """
from omnicore_engine.plugin_registry import plugin

@plugin(kind='execution', name='test_plugin')
def test_function():
    return "original"
"""
            )

            # Setup mock registry
            mock_registry = Mock()
            mock_registry.plugins = {"execution": {}}
            mock_registry.load_from_directory = AsyncMock()
            mock_registry.db = Mock()
            mock_registry.db.save_plugin_legacy = AsyncMock()

            # Create handler
            handler = PluginEventHandler(mock_registry, temp_dir)

            # Simulate file modification
            event = Mock()
            event.is_directory = False
            event.src_path = str(plugin_file)

            # Update file content
            plugin_file.write_text(
                """
from omnicore_engine.plugin_registry import plugin

@plugin(kind='execution', name='test_plugin')
def test_function():
    return "modified"
"""
            )

            # Wait a bit to ensure mtime changes
            await asyncio.sleep(0.1)

            # Create mock plugin after "reload"
            mock_plugin = Mock()
            mock_plugin.meta.name = "test_plugin"
            mock_plugin.meta.kind = "execution"
            mock_plugin.meta.version = "1.0.0"
            mock_plugin.meta.description = "Test"
            mock_plugin.meta.safe = True
            mock_plugin.meta.source = "test"
            mock_plugin.meta.params_schema = {}
            mock_plugin.fn = lambda: "modified"

            mock_registry.plugins["execution"]["test_plugin"] = mock_plugin

            # Handle the event
            await handler._handle_plugin_file_event_async(str(plugin_file), "modified")

            # Verify the flow completed
            mock_registry.load_from_directory.assert_called()
            mock_registry.db.save_plugin_legacy.assert_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
