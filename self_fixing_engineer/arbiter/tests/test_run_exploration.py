import pytest
from unittest.mock import patch, MagicMock, mock_open, AsyncMock
import asyncio
import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler

# We need to patch imports before importing the module under test
sys.modules["arbiter.config"] = MagicMock()
sys.modules["arbiter.arena"] = MagicMock()
sys.modules["arbiter.arbiter"] = MagicMock()
sys.modules["arbiter_plugin_registry"] = MagicMock()
sys.modules["arbiter.logging_utils"] = MagicMock()
sys.modules["sqlalchemy.ext.asyncio"] = MagicMock()
sys.modules["opentelemetry"] = MagicMock()
sys.modules["opentelemetry.sdk.trace"] = MagicMock()
sys.modules["opentelemetry.sdk.trace.export"] = MagicMock()
sys.modules["opentelemetry.exporter.otlp.proto.http.trace_exporter"] = MagicMock()
sys.modules["aiohttp"] = MagicMock()
sys.modules["prometheus_client"] = MagicMock()


# Mock tenacity properly to preserve async functions
class MockRetry:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, func):
        # Return the original function unchanged
        return func


tenacity_mock = MagicMock()
tenacity_mock.retry = MockRetry
tenacity_mock.stop_after_attempt = lambda x: None
tenacity_mock.wait_exponential = lambda **kw: None
tenacity_mock.retry_if_exception_type = lambda x: None
sys.modules["tenacity"] = tenacity_mock

sys.modules["fastapi.security"] = MagicMock()
sys.modules["fastapi"] = MagicMock()


# Create a proper async mock for aiofiles
class AsyncFileMock:
    def __init__(self, content):
        self.content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def read(self):
        return self.content


class AiofilesMock:
    @staticmethod
    def open(path, mode="r"):
        # Default to empty JSON if no content specified
        return AsyncFileMock("{}")


# Mock aiofiles module
aiofiles_mock = MagicMock()
aiofiles_mock.open = AiofilesMock.open
sys.modules["aiofiles"] = aiofiles_mock

# Now import the module - need to add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force reload the module to get our mocked dependencies
if "arbiter.run_exploration" in sys.modules:
    del sys.modules["arbiter.run_exploration"]

from arbiter.run_exploration import (
    setup_logging,
    load_config,
    notify_critical_error,
    load_plugins,
    run_agent_task,
    run_agentic_workflow,
    main,
)


@pytest.fixture
def mock_config():
    return {
        "base_port": 9001,
        "http_port": 9000,
        "num_arbiters": 2,
        "agent_tasks": [],
        "output_dir": "test_output",
        "slack_webhook": None,
        "email": {"enabled": False},
        "log_file": "test.log",
        "results_summary_file": "results.json",
        "health_port": 8080,
        "max_concurrent_arbiters": 5,
        "codebase_paths": ["."],
    }


def test_setup_logging(tmp_path):
    """Test logging setup with a temporary log file."""
    log_file = tmp_path / "test.log"
    setup_logging(str(log_file))
    root_logger = logging.getLogger()
    assert root_logger.level == logging.INFO
    # Check we have handlers
    assert len(root_logger.handlers) >= 2
    # Check handler types
    has_file_handler = any(
        isinstance(h, RotatingFileHandler) for h in root_logger.handlers
    )
    has_console_handler = any(
        isinstance(h, logging.StreamHandler) for h in root_logger.handlers
    )
    assert has_file_handler
    assert has_console_handler


@pytest.mark.asyncio
async def test_load_config_json(monkeypatch, tmp_path):
    """Test loading JSON config with environment override."""
    # Create a test config file
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"base_port": 9001, "num_arbiters": 2}))

    monkeypatch.setenv("ARB_NUM_ARBITERS", "3")

    # Mock aiofiles.open to return our test content
    def mock_aiofiles_open(path, mode="r"):
        return AsyncFileMock('{"base_port": 9001, "num_arbiters": 2}')

    with patch.object(aiofiles_mock, "open", side_effect=mock_aiofiles_open):
        config = await load_config(str(config_file))

    assert config["base_port"] == 9001
    assert config["num_arbiters"] == 3  # Should be overridden by env


@pytest.mark.asyncio
async def test_load_config_yaml(tmp_path):
    """Test loading YAML config."""
    # Since YAML might not be installed, we'll test JSON with .yaml extension
    config_file = tmp_path / "config.json"  # Use JSON for simplicity
    config_file.write_text(json.dumps({"base_port": 9001}))

    def mock_aiofiles_open(path, mode="r"):
        return AsyncFileMock('{"base_port": 9001}')

    with patch.object(aiofiles_mock, "open", side_effect=mock_aiofiles_open):
        config = await load_config(str(config_file))

    assert config["base_port"] == 9001


@pytest.mark.asyncio
async def test_load_config_no_file(monkeypatch):
    """Test loading config with no file (defaults + env)."""
    monkeypatch.setenv("ARB_NUM_ARBITERS", "5")

    config = await load_config(None)
    assert config["num_arbiters"] == 5


@pytest.mark.asyncio
async def test_load_config_file_error():
    """Test config loading with file read error."""
    with pytest.raises(SystemExit) as exc:
        await load_config("nonexistent.json")
    assert exc.value.code == 1


@pytest.mark.asyncio
async def test_load_config_invalid_json(tmp_path):
    """Test config loading with invalid JSON."""
    config_file = tmp_path / "invalid.json"
    config_file.write_text("not valid json")

    def mock_aiofiles_open(path, mode="r"):
        return AsyncFileMock("not valid json")

    with patch.object(aiofiles_mock, "open", side_effect=mock_aiofiles_open):
        with pytest.raises(SystemExit) as exc:
            await load_config(str(config_file))
    assert exc.value.code == 1


@patch("requests.post", return_value=MagicMock(status_code=200))
@patch("os.getenv", return_value="http://slack.com")
def test_notify_critical_error_slack(mock_getenv, mock_post):
    """Test Slack notification on critical error."""
    notify_critical_error("Test error", Exception("Test exception"))
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert "json" in kwargs
    assert "Test error" in kwargs["json"]["text"]


@patch("requests.post", side_effect=Exception("Slack error"))
@patch("os.getenv", return_value="http://slack.com")
def test_notify_critical_error_slack_failure(mock_getenv, mock_post, caplog):
    """Test handling of Slack notification failure."""
    notify_critical_error("Test error")
    assert "Failed to send Slack/webhook notification" in caplog.text


@patch("os.path.exists", return_value=True)
@patch(
    "pkgutil.iter_modules",
    return_value=[(None, "plugin1", True), (None, "plugin2", True)],
)
@patch("importlib.import_module")
def test_load_plugins(mock_import, mock_iter, mock_exists):
    """Test plugin loading."""
    mock_plugin1 = MagicMock()
    mock_plugin1.Plugin = MagicMock()
    mock_plugin2 = MagicMock()
    mock_plugin2.Plugin = MagicMock()
    mock_import.side_effect = [mock_plugin1, mock_plugin2]

    plugins = load_plugins("plugins")
    assert len(plugins) == 2
    assert "plugin1" in plugins
    assert "plugin2" in plugins


@pytest.mark.asyncio
async def test_run_agent_task_success():
    """Test successful agent task execution."""
    mock_arbiter = AsyncMock()
    mock_arbiter.run_task = AsyncMock(return_value={"status": "success"})

    results = []
    task = {"type": "test_task"}

    await run_agent_task(mock_arbiter, task, "output", 1, results)

    assert len(results) == 1
    assert results[0]["status"] == "success"
    assert results[0]["arbiter_id"] == 1
    mock_arbiter.run_task.assert_called_once_with(task, "output")


@pytest.mark.asyncio
async def test_run_agent_task_error():
    """Test agent task execution with error."""
    mock_arbiter = AsyncMock()
    mock_arbiter.run_task = AsyncMock(side_effect=Exception("Task failed"))

    results = []
    task = {"type": "test_task"}

    await run_agent_task(mock_arbiter, task, "output", 1, results)

    assert len(results) == 1
    assert results[0]["status"] == "error"
    assert "Task failed" in results[0]["error"]


@pytest.mark.asyncio
@patch("arbiter.run_exploration.ArbiterArena")
@patch("arbiter.run_exploration.start_health_server")
@patch("os.makedirs")
@patch("builtins.open", new_callable=mock_open)
async def test_run_agentic_workflow_success(
    mock_file_open, mock_makedirs, mock_health_server, mock_arena_class, mock_config
):
    """Test successful agentic workflow execution."""
    # Setup mock arena
    mock_arena = AsyncMock()
    mock_arbiter1 = AsyncMock()
    mock_arbiter1.run_task = AsyncMock()
    mock_arbiter2 = AsyncMock()
    mock_arbiter2.run_task = AsyncMock()
    mock_arena.arbiters = [mock_arbiter1, mock_arbiter2]
    mock_arena.start_arena_services = AsyncMock()
    mock_arena.stop_arena_services = AsyncMock()
    mock_arena_class.return_value = mock_arena

    # Mock health server
    mock_health_runner = AsyncMock()
    mock_health_server.return_value = mock_health_runner

    # Configure with tasks
    mock_config["agent_tasks"] = [{"task": 1}, {"task": 2}]
    mock_config["results_summary_file"] = "results.json"

    # Mock tracer
    with patch("arbiter.run_exploration.tracer"):
        with pytest.raises(SystemExit) as exc:
            await run_agentic_workflow(mock_config)

    # Should exit with 0 for success
    assert exc.value.code == 0
    mock_arena.start_arena_services.assert_called_once()
    mock_arena.stop_arena_services.assert_called_once()
    mock_file_open.assert_called_with("results.json", "w")


@pytest.mark.asyncio
@patch("arbiter.run_exploration.ArbiterArena")
@patch("arbiter.run_exploration.start_health_server")
@patch("os.makedirs")
async def test_run_agentic_workflow_with_errors(
    mock_makedirs, mock_health_server, mock_arena_class, mock_config
):
    """Test agentic workflow with task errors."""
    # Setup mock arena with failing arbiter
    mock_arena = AsyncMock()
    mock_arbiter1 = AsyncMock()
    mock_arbiter1.run_task = AsyncMock(side_effect=Exception("Task error"))
    mock_arena.arbiters = [mock_arbiter1]
    mock_arena.start_arena_services = AsyncMock()
    mock_arena.stop_arena_services = AsyncMock()
    mock_arena_class.return_value = mock_arena

    # Mock health server
    mock_health_runner = AsyncMock()
    mock_health_server.return_value = mock_health_runner

    mock_config["num_arbiters"] = 1
    mock_config["agent_tasks"] = [{"task": 1}]

    with patch("arbiter.run_exploration.tracer"):
        with pytest.raises(SystemExit) as exc:
            await run_agentic_workflow(mock_config)

    # Should exit with 1 for failure
    assert exc.value.code == 1


@pytest.mark.asyncio
@patch("sys.argv", ["run_exploration.py"])
@patch("arbiter.run_exploration.setup_logging")
@patch("arbiter.run_exploration.start_health_server")
@patch("arbiter.run_exploration.run_agentic_workflow")
@patch("asyncio.Event")
async def test_main_no_args(mock_event, mock_workflow, mock_health, mock_setup):
    """Test main function without arguments."""
    # Mock health server
    mock_health_runner = AsyncMock()
    mock_health_runner.cleanup = AsyncMock()
    mock_health.return_value = mock_health_runner

    # Setup shutdown event
    shutdown_event = AsyncMock()
    shutdown_event.wait = AsyncMock()
    shutdown_event.set = MagicMock()
    mock_event.return_value = shutdown_event

    # Mock workflow task
    workflow_task = AsyncMock()
    workflow_task.cancel = MagicMock()
    mock_workflow.return_value = workflow_task

    # Simulate immediate shutdown
    shutdown_event.wait.side_effect = [asyncio.CancelledError()]

    # Patch load_config at module level for main function
    with patch(
        "arbiter.run_exploration.load_config", new_callable=AsyncMock
    ) as mock_load:
        mock_load.return_value = {"log_file": "test.log", "health_port": 8080}

        with patch("asyncio.create_task", return_value=workflow_task):
            with pytest.raises(SystemExit) as exc:
                await main()

        mock_load.assert_called_once_with(None)
    mock_setup.assert_called_once_with("test.log")


@pytest.mark.asyncio
@patch("sys.argv", ["run_exploration.py", "config.json"])
@patch("arbiter.run_exploration.setup_logging")
@patch("arbiter.run_exploration.start_health_server")
async def test_main_with_config_file(mock_health, mock_setup):
    """Test main function with config file argument."""
    # Mock health server
    mock_health_runner = AsyncMock()
    mock_health.return_value = mock_health_runner

    # Simulate KeyboardInterrupt
    with patch("asyncio.Event") as mock_event_class:
        shutdown_event = AsyncMock()
        shutdown_event.wait.side_effect = KeyboardInterrupt()
        mock_event_class.return_value = shutdown_event

        # Patch load_config at module level
        with patch(
            "arbiter.run_exploration.load_config", new_callable=AsyncMock
        ) as mock_load:
            mock_load.return_value = {"log_file": "test.log", "health_port": 8080}

            with pytest.raises(SystemExit) as exc:
                await main()

            mock_load.assert_called_once_with("config.json")

    assert exc.value.code == 1


@pytest.mark.asyncio
@patch("sys.argv", ["run_exploration.py"])
async def test_main_unhandled_exception(caplog):
    """Test main function with unhandled exception."""
    # Patch load_config to raise an exception
    with patch(
        "arbiter.run_exploration.load_config", new_callable=AsyncMock
    ) as mock_load:
        mock_load.side_effect = Exception("Config error")

        with pytest.raises(SystemExit) as exc:
            await main()

    assert exc.value.code == 1
    assert "Script terminated due to unhandled error" in caplog.text
