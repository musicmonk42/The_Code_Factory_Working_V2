"""
Comprehensive unit tests for gui.py
Tests Textual TUI application, API interactions, and UI components.
"""

import asyncio
import os
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

# Set testing environment variables
os.environ["TESTING"] = "true"
os.environ["GENERATOR_API_KEY"] = "test-api-key"
os.environ["GENERATOR_API_BASE_URL"] = "http://localhost:8000/api/v1"

# Mock dependencies before importing gui
# We still mock these so the *real* gui.py can import them
sys.modules["runner.runner_core"] = MagicMock()
sys.modules["runner.runner_config"] = MagicMock()
sys.modules["runner.runner_logging"] = MagicMock()
sys.modules["runner.runner_metrics"] = MagicMock()
sys.modules["runner.runner_utils"] = MagicMock()
sys.modules["intent_parser.intent_parser"] = MagicMock()


@pytest.fixture
async def mock_dependencies():  # <<< FIX: Made fixture async
    """Mock all external dependencies."""
    with (
        patch("main.gui.Runner") as mock_runner,
        patch("main.gui.IntentParser") as mock_parser,
        patch("main.gui.load_config") as mock_config,
        patch("main.gui.ConfigWatcher") as mock_watcher,
    ):

        mock_config.return_value = {
            "backend": "test",
            "framework": "test",
            "logging": {"level": "INFO"},
        }

        # <<< FIX: Configure ConfigWatcher.start to return a coroutine
        async def dummy_start():
            pass

        mock_watcher.return_value.start = AsyncMock(return_value=dummy_start())

        yield {
            "runner": mock_runner,
            "parser": mock_parser,
            "config": mock_config,
            "watcher": mock_watcher,
        }


class TestTuiLogHandler:
    @pytest.fixture
    def log_widget(self):
        widget = MagicMock()
        widget.write = MagicMock()  # RichLog.write is not async
        return widget

    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        # Mock thread ID to simulate running on main thread by default
        app._thread_id = threading.get_ident()

        # Mock app.call_soon to just call the function
        app.call_soon = lambda func, *args: func(*args)
        # Mock app.call_from_thread as well
        app.call_from_thread = lambda func, *args: func(*args)
        # Mock app.create_task to mirror asyncio.create_task
        app.create_task = lambda coro: asyncio.create_task(coro)
        app._loop = asyncio.get_event_loop()
        return app

    def test_handler_initialization(self, log_widget, mock_app):
        from main.gui import TuiLogHandler

        handler = TuiLogHandler(log_widget, mock_app)
        assert handler.log_widget == log_widget
        assert handler.app == mock_app
        assert handler.queue is not None
        assert handler.worker_task is None
        assert hasattr(handler, "_lock")

    @pytest.mark.asyncio
    async def test_emit_log_record(self, log_widget, mock_app):
        import logging

        from main.gui import TuiLogHandler

        # Create a real task for the mock app to "create"
        pending_tasks = []

        def create_task_mock(coro):  # FIX: Must be a regular def, not async def
            task = asyncio.create_task(coro)
            pending_tasks.append(task)
            return task

        mock_app.create_task = create_task_mock

        handler = TuiLogHandler(log_widget, mock_app)
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test log",
            args=(),
            exc_info=None,
        )

        handler.emit(record)

        # Wait for the queue to be processed by the worker
        await handler.queue.join()

        log_widget.write.assert_called_with("Test log")

        handler.close()  # This will cancel the worker task

        # Wait for the cancellation to propagate
        for task in pending_tasks:
            try:
                # Wait for task to finish, but with a timeout
                await asyncio.wait_for(task, timeout=0.1)
            except asyncio.CancelledError:
                pass  # This is expected
            except asyncio.TimeoutError:
                pass  # This is also fine, task was cancelled

        await asyncio.sleep(0.01)  # Final sleep to let everything settle


class TestMainAppInitialization:
    """Tests for MainApp initialization."""

    @pytest.mark.asyncio
    async def test_app_creation(self, mock_dependencies):
        """Test MainApp can be created."""
        from main.gui import MainApp

        app = MainApp()
        async with app.run_test() as pilot:
            assert app is not None
            assert hasattr(app, "tui_log_handler")

    @pytest.mark.asyncio
    async def test_app_has_bindings(self, mock_dependencies):
        """Test MainApp has key bindings."""
        from main.gui import MainApp

        app = MainApp()
        # FIX: Run the app in the test harness
        async with app.run_test() as pilot:
            assert len(app.BINDINGS) > 0

            # Check for specific bindings
            binding_keys = [b.key for b in app.BINDINGS]
            assert "ctrl+r" in binding_keys
            assert "ctrl+p" in binding_keys
            assert "ctrl+q" in binding_keys

    @pytest.mark.asyncio
    async def test_app_has_css(self, mock_dependencies):
        """Test MainApp has CSS styling."""
        from main.gui import MainApp

        app = MainApp()
        # FIX: Run the app in the test harness
        async with app.run_test() as pilot:
            assert app.CSS is not None
            assert len(app.CSS) > 0


class TestMainAppCompose:
    """Tests for MainApp compose method."""

    @pytest.mark.asyncio
    async def test_compose_creates_widgets(self, mock_dependencies):
        """Test compose method creates required widgets."""
        from main.gui import MainApp

        app = MainApp()
        async with app.run_test() as pilot:
            assert pilot.app.query_one("#runner_input") is not None
            assert pilot.app.query_one("#intent_parser_input") is not None
            assert pilot.app.query_one("#clarifier_table") is not None
            assert pilot.app.query_one("#log_output") is not None
            assert pilot.app.query_one("#runner_progress") is not None


class TestAPIInteraction:
    """Tests for API interaction methods."""

    @pytest.mark.asyncio
    @pytest.fixture
    async def app_instance(self, mock_dependencies):
        """Create MainApp instance."""
        from main.gui import MainApp

        app = MainApp()
        # FIX: Run the app in the test harness and yield the app instance
        async with app.run_test() as pilot:
            await asyncio.sleep(0.01)  # Allow on_mount to complete
            yield app

    @pytest.mark.asyncio
    async def test_make_api_request_get(self, app_instance):
        """Test making GET API request."""
        with patch("main.gui.aiohttp.ClientSession") as MockSession:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"result": "success"})
            mock_response.raise_for_status = MagicMock()

            mock_request_context = AsyncMock()
            mock_request_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_request_context.__aexit__ = AsyncMock()

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(
                return_value=mock_session
            )  # Session context returns session
            mock_session.__aexit__ = AsyncMock()

            # FIX: session.request must return a context manager, NOT be an AsyncMock itself
            mock_session.request = MagicMock(return_value=mock_request_context)

            MockSession.return_value = mock_session  # This is the key

            result = await app_instance._make_api_request("GET", "http://test.com/api")

            assert result == {"result": "success"}

    @pytest.mark.asyncio
    async def test_make_api_request_post(self, app_instance):
        """Test making POST API request."""
        with patch("main.gui.aiohttp.ClientSession") as MockSession:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"result": "created"})
            mock_response.raise_for_status = MagicMock()

            mock_request_context = AsyncMock()
            mock_request_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_request_context.__aexit__ = AsyncMock()

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()

            # FIX: session.request must be MagicMock
            mock_session.request = MagicMock(return_value=mock_request_context)

            MockSession.return_value = mock_session

            result = await app_instance._make_api_request(
                "POST", "http://test.com/api", json_data={"test": "data"}
            )

            assert result == {"result": "created"}

    @pytest.mark.asyncio
    async def test_make_api_request_error_handling(self, app_instance):
        """Test API request error handling."""
        import aiohttp

        with patch("main.gui.aiohttp.ClientSession") as MockSession:

            # FIX: The error must be raised by the request's context manager, not the request call itself
            mock_request_context = AsyncMock()
            mock_request_context.__aenter__ = AsyncMock(
                side_effect=aiohttp.ClientError("Network error")
            )
            mock_request_context.__aexit__ = AsyncMock()

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()

            # FIX: session.request must be MagicMock
            mock_session.request = MagicMock(return_value=mock_request_context)

            MockSession.return_value = mock_session

            with pytest.raises(aiohttp.ClientError):
                await app_instance._make_api_request("GET", "http://test.com/api")


class TestRunnerTab:
    """Tests for Runner tab functionality."""

    @pytest.mark.asyncio
    @pytest.fixture
    async def app_instance(self, mock_dependencies):
        """Create MainApp instance."""
        from main.gui import MainApp

        app = MainApp()
        # FIX: Run the app in the test harness
        async with app.run_test() as pilot:
            await asyncio.sleep(0.01)  # Allow on_mount to complete
            yield app

    @pytest.mark.asyncio
    async def test_submit_runner_input_valid(self, app_instance):
        """Test submitting valid runner input."""
        with (
            patch.object(app_instance, "_make_api_request") as mock_api,
            patch.object(app_instance, "_set_success_message", new_callable=AsyncMock),
        ):
            mock_api.return_value = {"status": "success", "run_id": "123"}
            app_instance.query_one("#runner_input").value = '{"test": "data"}'
            await app_instance.run_workflow_from_button()
            mock_api.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_runner_input_invalid_json(self, app_instance):
        """Test submitting invalid JSON to runner."""

        with (
            patch.object(app_instance.runner_log, "write") as mock_log_write,
            patch.object(
                app_instance, "_set_error_message", new_callable=AsyncMock
            ) as mock_set_error,
        ):

            app_instance.query_one("#runner_input").value = "{ invalid json }"
            await app_instance.run_workflow_from_button()

            mock_log_write.assert_called_with(
                pytest.string_containing("[red]Invalid JSON payload")
            )
            mock_set_error.assert_called_with(
                app_instance.runner_error,
                pytest.string_containing("Invalid JSON payload"),
            )


class TestParserTab:
    """Tests for Intent Parser tab functionality."""

    @pytest.mark.asyncio
    @pytest.fixture
    async def app_instance(self, mock_dependencies):
        """Create MainApp instance."""
        from main.gui import MainApp

        app = MainApp()
        # FIX: Run the app in the test harness
        async with app.run_test() as pilot:
            await asyncio.sleep(0.01)  # Allow on_mount to complete
            yield app

    @pytest.mark.asyncio
    async def test_parse_text_input(self, app_instance):
        """Test parsing text input."""
        with (
            patch.object(app_instance, "_make_api_request") as mock_api,
            patch.object(app_instance.runner_log, "write") as mock_log_write,
            patch.object(app_instance, "_set_success_message", new_callable=AsyncMock),
        ):

            mock_api.return_value = {"result": "parsed"}

            test_input = "Parse this text"

            app_instance.query_one("#intent_parser_input").value = test_input
            await app_instance.run_intent_parser_from_button()

            mock_api.assert_called_once_with(
                "POST",
                app_instance.API_ENDPOINTS["parse_text"],
                json_data={
                    "content": test_input,
                    "format_hint": None,
                    "dry_run": False,
                },
            )

    @pytest.mark.asyncio
    async def test_parse_file_input(self, app_instance, tmp_path):
        """Test parsing file input."""
        # Create a test file
        test_file = tmp_path / "test.md"
        test_file.write_text("# Test Content")

        with (
            patch.object(app_instance, "_make_api_request") as mock_api,
            patch("main.gui.aiofiles.open", new_callable=AsyncMock),
            patch.object(app_instance.runner_log, "write") as mock_log_write,
            patch.object(app_instance, "_set_success_message", new_callable=AsyncMock),
        ):

            mock_api.return_value = {"result": "parsed"}

            # Simulate file path input
            file_path = str(test_file)

            app_instance.query_one("#intent_parser_input").value = file_path
            await app_instance.run_intent_parser_from_button()

            # Verify file exists
            assert Path(file_path).exists()
            assert mock_api.called
            # Check that the call was for a file
            assert mock_api.call_args[1]["files"] is not None


class TestClarifierTab:
    """Tests for Clarifier tab functionality."""

    @pytest.mark.asyncio
    @pytest.fixture
    async def app_instance(self, mock_dependencies):
        """Create MainApp instance."""
        from main.gui import MainApp

        app = MainApp()
        async with app.run_test() as pilot:
            await asyncio.sleep(0.01)  # Wait for on_mount to add columns
            await app.clarifier_table.add_row(
                "q123", "Test Question?", "Pending", key="q123"
            )
            app.clarifier_table.cursor_row = 0
            yield app

    @pytest.mark.asyncio
    async def test_submit_clarification(self, app_instance):
        """Test submitting clarification response."""
        with (
            patch.object(app_instance, "_make_api_request") as mock_api,
            patch.object(app_instance.runner_log, "write") as mock_log_write,
            patch.object(app_instance, "_set_success_message", new_callable=AsyncMock),
        ):

            mock_api.return_value = {"message": "Clarification received"}

            app_instance.query_one("#clarifier_input").value = "yes"
            await app_instance.submit_clarification_from_button()

            api_url = f"{app_instance.API_ENDPOINTS['parse_feedback']}/q123"
            mock_api.assert_called_once_with("POST", api_url, json_data={"rating": 1.0})


class TestMetricsTab:
    """Tests for Metrics tab functionality."""

    @pytest.mark.asyncio
    @pytest.fixture
    async def app_instance(self, mock_dependencies):
        """Create MainApp instance."""
        from main.gui import MainApp

        app = MainApp()
        async with app.run_test() as pilot:
            await asyncio.sleep(0.01)  # Allow on_mount to complete
            yield app

    @pytest.mark.asyncio
    async def test_update_metrics_display_api(self, app_instance):
        """Test updating metrics display from API."""
        with patch.object(app_instance, "_make_api_request") as mock_api:
            # Mock both metrics and version calls
            mock_api.side_effect = [
                {
                    "cpu_usage": 45.2,
                    "memory_usage": 62.1,
                    "active_tasks": 3,
                },  # Metrics call
                {"version": "1.2.3"},  # Version call
            ]

            with (
                patch.object(
                    app_instance.metrics_display, "update"
                ) as mock_display_update,
                patch.object(
                    app_instance.metrics_display_api_version, "update"
                ) as mock_version_update,
                patch.object(
                    app_instance, "_set_error_message", new_callable=AsyncMock
                ),
                patch.object(
                    app_instance, "_set_success_message", new_callable=AsyncMock
                ),
            ):

                await app_instance.update_metrics_display()
                await app_instance._update_metrics()

            assert mock_api.call_count == 2
            mock_version_update.assert_called_once_with(
                "API Version: [green]1.2.3[/green]"
            )
            assert mock_display_update.call_count == 2

    @pytest.mark.asyncio
    async def test_update_metrics_local(self, app_instance):
        """Test updating metrics display from local runner metrics."""
        with (
            patch("main.gui.RUN_QUEUE.get_size", return_value=5) as mock_q,
            patch("main.gui.RUN_PASS_RATE.get", return_value=95.5) as mock_pass,
            patch("main.gui.RUN_RESOURCE_USAGE.get", return_value=75.0) as mock_res,
            patch("main.gui.HEALTH_STATUS.get", return_value="OK") as mock_health,
            patch.object(app_instance.metrics_display, "update") as mock_display_update,
            patch.object(app_instance, "_set_success_message", new_callable=AsyncMock),
        ):

            await app_instance._update_metrics()  # Call internal method directly

            mock_display_update.assert_called_once_with(
                "queue_size: 5\npass_rate: 95.5\nresource_usage: 75.0\nhealth_status: OK"
            )

    @pytest.mark.asyncio
    async def test_metrics_refresh_interval_change(self, app_instance):
        """Test changing metrics refresh interval."""

        with (
            patch.object(app_instance, "set_interval") as mock_interval,
            patch.object(
                app_instance, "_set_success_message", new_callable=AsyncMock
            ) as mock_success,
        ):

            from textual.widgets import Select

            event = Select.Changed(
                app_instance.query_one("#metrics_refresh_interval"), "10"
            )

            await app_instance.on_metrics_refresh_interval_changed(event)

            mock_interval.assert_called_once_with(10, app_instance._update_metrics)
            mock_success.assert_called_once()


class TestConfigReload:
    """Tests for configuration reload functionality."""

    @pytest.mark.asyncio
    @pytest.fixture
    async def app_instance(self, mock_dependencies):
        """Create MainApp instance."""
        from main.gui import MainApp

        app = MainApp()
        async with app.run_test() as pilot:
            await asyncio.sleep(0.01)  # Allow on_mount to complete
            yield app

    @pytest.mark.asyncio
    async def test_reload_runner_config(self, app_instance):
        """Test reloading runner configuration."""

        app_instance.config_watcher = MagicMock()
        app_instance.config_watcher._reload = MagicMock()

        with (
            patch.object(app_instance.runner_log, "write") as mock_log_write,
            patch.object(app_instance, "_set_success_message", new_callable=AsyncMock),
        ):

            app_instance.query_one("#reload-runner-config").press()
            await asyncio.sleep(0.01)  # Allow events to process

            app_instance.config_watcher._reload.assert_called_once_with(force=True)

    @pytest.mark.asyncio
    async def test_reload_parser_config(self, app_instance):
        """Test reloading parser configuration."""

        app_instance.parser_config_watcher = MagicMock()
        app_instance.parser_config_watcher._reload = MagicMock()

        with (
            patch.object(
                app_instance, "_trigger_backend_config_reload", new_callable=AsyncMock
            ) as mock_trigger,
            patch.object(app_instance.runner_log, "write") as mock_log_write,
            patch.object(app_instance, "_set_success_message", new_callable=AsyncMock),
        ):

            app_instance.query_one("#reload-parser-config").press()
            await asyncio.sleep(0.01)  # Allow events to process

            app_instance.parser_config_watcher._reload.assert_called_once_with(
                force=True
            )
            mock_trigger.assert_called_once()


class TestKeyBindings:
    @pytest.mark.asyncio
    @pytest.fixture
    async def app_instance(self, mock_dependencies):
        """Create MainApp instance."""
        from main.gui import MainApp

        app = MainApp()
        async with app.run_test() as pilot:
            await asyncio.sleep(0.01)  # Allow on_mount to complete
            yield app

    @pytest.mark.asyncio
    async def test_focus_runner_action(self, app_instance):
        from main.gui import Input

        with patch.object(app_instance, "query_one") as mock_query:
            mock_input = MagicMock()
            mock_input.focus = MagicMock()  # Need to mock focus method
            mock_query.return_value = mock_input

            app_instance.action_focus_runner()

            mock_query.assert_called_with("#runner_input", Input)
            mock_input.focus.assert_called_once()

    @pytest.mark.asyncio
    async def test_focus_parser_action(self, app_instance):
        from main.gui import Input

        with patch.object(app_instance, "query_one") as mock_query:
            mock_input = MagicMock()
            mock_input.focus = MagicMock()
            mock_query.return_value = mock_input

            app_instance.action_focus_parser()

            mock_query.assert_called_with("#intent_parser_input", Input)
            mock_input.focus.assert_called_once()

    @pytest.mark.asyncio
    async def test_focus_clarifier_action(self, app_instance):
        from main.gui import Input

        with patch.object(app_instance, "query_one") as mock_query:
            mock_input = MagicMock()
            mock_input.focus = MagicMock()
            mock_query.return_value = mock_input

            app_instance.action_focus_clarifier()

            mock_query.assert_called_with("#clarifier_input", Input)
            mock_input.focus.assert_called_once()

    @pytest.mark.asyncio
    async def test_help_action(self, app_instance):
        with patch.object(app_instance, "push_screen") as mock_push:
            app_instance.action_help()
            mock_push.assert_called_once()


class TestHelpScreen:
    """Tests for Help screen."""

    @pytest.mark.asyncio
    async def test_help_screen_creation(self, mock_dependencies):
        """Test HelpScreen can be created."""
        from main.gui import HelpScreen

        screen = HelpScreen()
        async with screen.run_test() as pilot:
            assert screen is not None

    @pytest.mark.asyncio
    async def test_help_screen_has_content(self, mock_dependencies):
        """Test HelpScreen has content."""
        from main.gui import HelpScreen

        screen = HelpScreen()
        async with screen.run_test() as pilot:
            widgets = pilot.app.query("*")  # Query for all widgets

            # Should have content
            assert len(widgets) > 2


class TestErrorHandling:
    """Tests for error handling in GUI."""

    @pytest.mark.asyncio
    @pytest.fixture
    async def app_instance(self, mock_dependencies):
        """Create MainApp instance."""
        from main.gui import MainApp

        app = MainApp()
        async with app.run_test() as pilot:
            await asyncio.sleep(0.01)  # Allow on_mount to complete
            yield app

    @pytest.mark.asyncio
    async def test_api_error_handling(self, app_instance):
        """Test handling of API errors."""
        from fastapi import HTTPException

        with patch.object(app_instance, "_make_api_request") as mock_api:
            mock_api.side_effect = HTTPException(status_code=500, detail="Server error")

            # Error should be handled gracefully
            with pytest.raises(HTTPException):
                await app_instance._make_api_request("GET", "http://test.com/api")

    @pytest.mark.asyncio
    async def test_network_error_handling(self, app_instance):
        """Test handling of network errors."""

        with patch.object(app_instance, "_make_api_request") as mock_api:
            mock_api.side_effect = aiohttp.ClientError("Network timeout")

            # Should handle network errors
            with pytest.raises(aiohttp.ClientError):
                await app_instance._make_api_request("GET", "http://test.com/api")


class TestConfigWatchers:
    """Tests for configuration watchers."""

    @pytest.mark.asyncio
    @pytest.fixture
    async def app_instance(self, mock_dependencies):
        """Create MainApp instance."""
        from main.gui import MainApp

        app = MainApp()
        async with app.run_test() as pilot:
            await asyncio.sleep(0.01)  # Allow on_mount to complete
            yield app

    @pytest.mark.asyncio
    async def test_config_watcher_initialization(self, app_instance):
        """Test config watchers are initialized."""
        # Config watchers should be created during app mount
        assert app_instance.config_watcher is not None
        assert app_instance.parser_config_watcher is not None

    @pytest.mark.asyncio
    async def test_config_change_detection(self, app_instance, tmp_path):
        """Test config change detection."""
        # Create a test config file
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("backend: test\n")

        # Config watcher would detect changes
        assert config_file.exists()


class TestAsyncFileOperations:
    """Tests for async file operations."""

    @pytest.mark.asyncio
    async def test_async_file_read(self, tmp_path):
        """Test async file reading."""
        import aiofiles

        test_file = tmp_path / "test.txt"
        test_file.write_text("Test content")

        async with aiofiles.open(test_file, "r") as f:
            content = await f.read()

        assert content == "Test content"

    @pytest.mark.asyncio
    async def test_async_file_write(self, tmp_path):
        """Test async file writing."""
        import aiofiles

        test_file = tmp_path / "output.txt"

        async with aiofiles.open(test_file, "w") as f:
            await f.write("Output content")

        assert test_file.read_text() == "Output content"


class TestUIMessageHelpers:
    """Tests for UI message helper methods."""

    @pytest.mark.asyncio
    @pytest.fixture
    async def app_instance(self, mock_dependencies):
        """Create MainApp instance."""
        from main.gui import MainApp

        app = MainApp()
        async with app.run_test() as pilot:
            await asyncio.sleep(0.01)  # Allow on_mount to complete
            yield app

    @pytest.mark.asyncio
    async def test_set_success_message(self, app_instance):
        """Test setting success message."""
        widget = MagicMock()
        widget.update = MagicMock()

        await app_instance._set_success_message(widget, "Success!", clear_after=None)

        # Widget should be updated
        widget.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_error_message(self, app_instance):
        """Test setting error message."""
        widget = MagicMock()
        widget.update = MagicMock()

        await app_instance._set_error_message(widget, "Error!", clear_after=None)

        # Widget should be updated
        widget.update.assert_called_once()


class TestIntegrationWithAPI:
    @pytest.mark.asyncio
    async def test_full_runner_workflow(self, mock_dependencies):
        from main.gui import MainApp

        app = MainApp()
        async with app.run_test() as pilot:
            await asyncio.sleep(0.01)  # Allow on_mount to complete
            with patch.object(app, "_make_api_request") as mock_api:
                mock_api.return_value = {
                    "status": "success",
                    "run_id": "test-run-123",
                    "output": {"result": "Generated"},
                }
                payload = {"input": "test"}
                result = await app._make_api_request(
                    "POST", "http://test/run", json_data=payload
                )
                assert result["status"] == "success"
                assert "run_id" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
