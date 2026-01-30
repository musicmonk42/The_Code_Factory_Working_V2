import asyncio
import json
import logging
import sys
import threading
from unittest.mock import AsyncMock, MagicMock, patch

# Import the module under test
import intent_capture.cli as cli_module  # Import the module itself to patch its internals
import psutil
import pytest

# Import what's actually available from the cli module
from intent_capture.cli import (
    SessionState,
)  # Changed from session_state to SessionState
from intent_capture.cli import (  # These don't appear to exist in the new cli.py:; COMMAND_HELP,; _capture_state,; _save_for_undo,; _restore_from_state,; undo_last_action,; redo_last_undo,; collab_client_listener,; display_markdown,; colored_diff,; process_agent_response,; handle_error,; parse_command,; playback_mode,; show_help_cmd,; display_security_best_practices,; _local_input_worker,; maybe_start_input_thread,
    CollabServer,
    CommandDispatcher,
    JsonFormatter,
    main_cli_loop,
    resource_guard,
    shutdown_handler,
)
from rich.console import Console
from rich.prompt import Prompt


# --- Test Fixtures ---
@pytest.fixture
def mock_console(capsys):
    """Mock Rich Console for output testing."""
    # Patch the global console object in cli.py
    with patch(
        "intent_capture.cli.CONSOLE",
        new=Console(file=sys.stdout, force_terminal=True, no_color=True),
    ):
        yield cli_module.CONSOLE
    # Output can be read via capsys later if needed


@pytest.fixture
def mock_agent():
    """Mock CollaborativeAgent."""
    mock_agent = MagicMock()
    mock_agent.predict = AsyncMock(
        return_value={"response": "mock_response", "trace": {"mock": True}}
    )
    mock_agent.memory.clear = MagicMock()
    mock_agent.get_transcript = MagicMock(return_value="mock_transcript")
    mock_agent.meta = {"mock": "meta"}
    mock_agent.set_persona = MagicMock()
    mock_agent.set_language = MagicMock()
    mock_agent.llm_config = {"provider": "mock"}
    mock_agent.get_state = AsyncMock(return_value={"mock_state": True, "memory": []})
    mock_agent._llm = MagicMock()
    yield mock_agent


@pytest.fixture
def mock_session_state():
    """Mock SessionState instance."""
    state = SessionState()
    return state


@pytest.fixture
def mock_get_or_create_agent(mock_agent):
    """Mock get_or_create_agent."""
    with patch(
        "intent_capture.cli.get_or_create_agent", AsyncMock(return_value=mock_agent)
    ) as mock_func:
        yield mock_func


@pytest.fixture
def mock_websockets():
    """Mock websockets for collaboration."""
    # Check if websockets is actually available before patching
    if not cli_module.WEBSOCKETS_AVAILABLE:
        pytest.skip("websockets not available for testing")

    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock()
    mock_ws.recv = AsyncMock(
        side_effect=[
            json.dumps({"type": "history", "payload": []}),
            json.dumps({"type": "command", "payload": "test_message"}),
            asyncio.CancelledError,
        ]
    )
    mock_ws.close = AsyncMock()

    with (
        patch(
            "intent_capture.cli.websockets.serve",
            AsyncMock(return_value=MagicMock(wait_closed=AsyncMock())),
        ),
        patch("intent_capture.cli.websockets.connect", AsyncMock(return_value=mock_ws)),
    ):
        yield mock_ws


@pytest.fixture
def mock_logger():
    """Mock logger to capture logs."""
    with patch(
        "intent_capture.cli.logging.root.handlers", new=[MagicMock()]
    ) as mock_handlers:
        yield mock_handlers


@pytest.fixture
def mock_console_output(capsys):
    """Capture console output."""
    yield capsys


@pytest.fixture
def mock_input(monkeypatch):
    """Mock input for prompts."""

    def mock_prompt(*args, **kwargs):
        return "y"

    monkeypatch.setattr(Prompt, "ask", mock_prompt)
    yield


@pytest.fixture
def temp_files(tmp_path):
    """Create temporary files for testing."""
    yield tmp_path


# --- Tests for Logging Setup ---
def test_json_formatter():
    """Test JsonFormatter outputs valid JSON."""
    formatter = JsonFormatter()
    record = logging.LogRecord("name", logging.INFO, "path", 10, "message", (), None)
    formatted = formatter.format(record)
    json.loads(formatted)  # Should not raise JSONDecodeError


# --- Tests for Shutdown Handler ---
def test_shutdown_handler():
    """Test shutdown handler sets event."""
    cli_module._shutdown_event.clear()
    shutdown_handler(None, None)
    assert cli_module._shutdown_event.is_set()


# --- Tests for Resource Monitoring ---
def test_resource_guard_normal():
    """Test resource guard under normal conditions."""
    # This should not raise an exception under normal memory conditions
    try:
        resource_guard()
    except RuntimeError:
        pytest.skip("System memory is actually too high for this test")


def test_resource_guard_high_memory(monkeypatch):
    """Test resource guard with high memory."""
    mock_mem = MagicMock()
    mock_mem.percent = 96.0
    monkeypatch.setattr(psutil, "virtual_memory", lambda: mock_mem)
    with pytest.raises(RuntimeError, match="Hard memory limit exceeded"):
        resource_guard()


# --- Tests for SessionState ---
@pytest.mark.asyncio
async def test_session_state_get_set():
    """Test SessionState get and set methods."""
    state = SessionState()
    await state.set("test_key", "test_value")
    value = await state.get("test_key")
    assert value == "test_value"


@pytest.mark.asyncio
async def test_session_state_get_agent():
    """Test SessionState get_agent method."""
    state = SessionState()
    mock_agent = MagicMock()
    await state.set("agent", mock_agent)
    agent = await state.get_agent()
    assert agent == mock_agent


# --- Tests for CollabServer ---
@pytest.mark.asyncio
async def test_collab_server_init():
    """Test CollabServer initialization."""
    server = CollabServer("localhost", 8765)
    assert server.host == "localhost"
    assert server.port == 8765
    assert len(server.clients) == 0


# --- Tests for CommandDispatcher ---
@pytest.mark.asyncio
async def test_command_dispatcher_help(mock_session_state, mock_console_output):
    """Test dispatcher for help command."""
    dispatcher = CommandDispatcher(mock_session_state)
    await dispatcher.dispatch("help", [])
    captured = mock_console_output.readouterr().out
    assert "Available Commands" in captured


@pytest.mark.asyncio
async def test_command_dispatcher_unknown(mock_session_state):
    """Test dispatcher for unknown command."""
    dispatcher = CommandDispatcher(mock_session_state)
    with pytest.raises(ValueError, match="Unknown command"):
        await dispatcher.dispatch("unknown", [])


@pytest.mark.asyncio
async def test_command_dispatcher_clear(mock_session_state, mock_agent):
    """Test dispatcher for clear command."""
    await mock_session_state.set("agent", mock_agent)
    dispatcher = CommandDispatcher(mock_session_state)
    await dispatcher.dispatch("clear", [])
    mock_agent.memory.clear.assert_called_once()


@pytest.mark.asyncio
async def test_command_dispatcher_exit(mock_session_state):
    """Test dispatcher for exit command."""
    cli_module._shutdown_event.clear()
    dispatcher = CommandDispatcher(mock_session_state)
    await dispatcher.dispatch("exit", [])
    assert cli_module._shutdown_event.is_set()


# --- Tests for Main CLI Loop ---
@pytest.mark.asyncio
async def test_main_cli_loop_basic_flow(monkeypatch, mock_console_output):
    """Test basic flow of main CLI loop."""
    # Mock the necessary imports that main_cli_loop tries
    mock_agent = MagicMock()
    mock_agent.predict = AsyncMock(return_value={"response": "test", "token_usage": 10})
    mock_agent.memory = MagicMock()

    async def mock_get_or_create_agent(*args, **kwargs):
        return mock_agent

    # Since get_or_create_agent is imported inside main_cli_loop,
    # we need to mock the import itself
    import sys

    mock_agent_core = MagicMock()
    mock_agent_core.get_or_create_agent = mock_get_or_create_agent
    sys.modules["agent_core"] = mock_agent_core

    # Also mock the autocomplete module
    mock_autocomplete = MagicMock()
    mock_autocomplete.add_to_history = MagicMock()
    mock_autocomplete.execute_macro = lambda x: x
    mock_autocomplete.handle_command_not_found = MagicMock()
    mock_autocomplete.setup_autocomplete = MagicMock()
    sys.modules["autocomplete"] = mock_autocomplete

    # Create a controlled input queue
    test_queue = asyncio.Queue()
    await test_queue.put("help")
    await test_queue.put("exit")

    # Mock the local input worker thread creation
    def mock_thread(*args, **kwargs):
        thread = MagicMock()
        thread.start = MagicMock()
        return thread

    monkeypatch.setattr(threading, "Thread", mock_thread)

    # Mock the input queue to use our test queue
    monkeypatch.setattr(asyncio, "Queue", lambda: test_queue)

    # Clear shutdown event
    cli_module._shutdown_event.clear()

    # Run the main loop
    await main_cli_loop()

    # Check that shutdown was triggered
    assert cli_module._shutdown_event.is_set()

    # Check console output
    captured = mock_console_output.readouterr().out
    assert "Welcome to the Hardened Intent Capture Agent CLI" in captured
    assert "Available Commands" in captured  # From help command
