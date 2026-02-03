# tests/test_gen_agent_cli.py
import asyncio
import logging
import os
import signal
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from click.testing import CliRunner

# Absolute import to the CLI module
from test_generation.gen_agent import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def temp_config_file():
    # Use a temporary directory to avoid file name collisions
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "config.yml"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("TEST_KEY: test_value\n")
        yield file_path


def test_cli_version_option(runner):
    """CLI should print version without error."""
    result = runner.invoke(cli.cli, ["--version"])
    assert result.exit_code == 0
    assert "version" in result.output.lower()


def test_cli_loads_config_file(runner, temp_config_file):
    """CLI should load YAML config and merge into environment."""

    async def check_env_and_succeed(*_args, **_kwargs):
        # The key is uppercased and prefixed by the cli module.
        assert os.environ.get("ATCO_TEST_KEY") == "test_value"
        return 0

    # Patch the async generator used by the command so we don't run deep logic
    with patch(
        "self_fixing_engineer.test_generation.gen_agent.cli._generate_async", new=check_env_and_succeed
    ):
        result = runner.invoke(
            cli.cli,
            ["--config-file", str(temp_config_file), "generate", "--session", "test"],
        )

    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_run_async_command_graceful_shutdown():
    """_run_async_command should cancel its inner task and return 1 on signal."""
    main_task_cancelled = asyncio.Event()

    async def fake_main():
        try:
            await asyncio.Event().wait()  # Wait indefinitely until cancelled
        except asyncio.CancelledError:
            main_task_cancelled.set()  # Signal that this inner task was cancelled
            raise

    with patch(
        "self_fixing_engineer.test_generation.gen_agent.cli.install_default_handlers"
    ) as mock_install:
        # Run the command and let it install its signal handler
        task = asyncio.create_task(cli._run_async_command(fake_main()))
        await asyncio.sleep(0.01)  # Give asyncio time to schedule and run the setup

        # The real handler is the first argument passed to our mock installer
        assert mock_install.call_count == 1
        real_handler = mock_install.call_args[0][0]

        # Simulate a signal by calling the handler directly
        real_handler(signal.SIGINT, None)

        # The command should now shut down and return an exit code
        exit_code = await task

        assert exit_code == 1
        assert (
            main_task_cancelled.is_set()
        ), "The main task within _run_async_command was not cancelled."


def test_cli_generate_command_runs(runner, tmp_path):
    """Generate command should complete and exit with code 0."""
    async_mock = AsyncMock(return_value=0)

    # Prevent the passed coroutine from being awaited/executed; return 0 immediately
    with patch(
        "self_fixing_engineer.test_generation.gen_agent.cli._run_async_command",
        new=AsyncMock(return_value=0),
    ) as run_mock:
        with patch("self_fixing_engineer.test_generation.gen_agent.cli._generate_async", new=async_mock):
            # NOTE: group options must precede the subcommand
            result = runner.invoke(
                cli.cli,
                [
                    "--project-root",
                    str(tmp_path),
                    "generate",
                    "--session",
                    "test_session",
                ],
            )

    # `_generate_async` is called exactly once to create the coroutine
    async_mock.assert_called_once()
    # The wrapper was used
    run_mock.assert_called_once()
    # Command exited cleanly
    assert result.exit_code == 0
    assert result.exception is None


def test_cli_handles_missing_yaml_dependency(runner, temp_config_file):
    """
    Using --config-file without PyYAML should error nicely and exit with code 1.
    With the production CLI, this is a ClickException, so we assert on exit code and message.
    """
    with patch("self_fixing_engineer.test_generation.gen_agent.cli.yaml", None):
        result = runner.invoke(
            cli.cli,
            ["--config-file", str(temp_config_file), "generate", "--session", "test"],
        )

    assert result.exit_code == 1
    # Click prints "Error: <message>" to stderr for ClickException
    assert "PyYAML is required for --config-file" in result.output


def test_make_run_id(monkeypatch):
    """
    Test _make_run_id() returns a valid UUID string.
    """
    mock_uuid4 = Mock(return_value=uuid.UUID("123e4567-e89b-12d3-a456-426614174000"))
    monkeypatch.setattr("uuid.uuid4", mock_uuid4)
    assert cli._make_run_id() == "123e4567-e89b-12d3-a456-426614174000"


@pytest.mark.asyncio
async def test_graceful_shutdown_logs_message(caplog):
    """Test that the signal handler used by _run_async_command logs a message."""

    async def dummy_task():
        await asyncio.sleep(
            5
        )  # A task that does nothing, just to keep the command alive.

    with patch(
        "self_fixing_engineer.test_generation.gen_agent.cli.install_default_handlers"
    ) as mock_install:
        with caplog.at_level(logging.WARNING):
            # Start the command, which will install the real handler via our mock
            task = asyncio.create_task(cli._run_async_command(dummy_task()))
            await asyncio.sleep(0.01)  # Let the handler be installed.

            assert mock_install.called, "install_default_handlers was not called."
            real_handler = mock_install.call_args[0][0]

            # Simulate the signal by calling the handler
            real_handler(signal.SIGINT, None)

            # Wait for the command to finish shutting down
            await task

    assert "Received signal" in caplog.text
    assert "initiating graceful shutdown" in caplog.text


@pytest.mark.asyncio
async def test_feedback_async_command(runner, tmp_path):
    """
    Tests that the feedback command runs and exits with code 0.
    """
    # Create a mock feedback log file
    log_file_path = tmp_path / "test.jsonl"
    with open(log_file_path, "w") as f:
        f.write('{"execution_status": "PASS", "final_scores": {"coverage": 95.0}}\n')

    # Mock the underlying asynchronous function call
    with patch(
        "self_fixing_engineer.test_generation.gen_agent.cli.summarize_feedback",
        new=AsyncMock(return_value={"total_runs": 1}),
    ):
        result = runner.invoke(
            cli.cli, ["feedback", "summarize", "--log-file", str(log_file_path)]
        )

    assert result.exit_code == 0


# Completed for syntactic validity.
def test_cli_import():
    """
    Verifies that the main CLI entry point can be imported and is callable.
    """
    from test_generation.gen_agent import cli

    assert callable(cli.cli)
