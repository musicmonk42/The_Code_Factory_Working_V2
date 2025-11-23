import asyncio
import json
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the module under test - only import what actually exists
from intent_capture.autocomplete import (
    AutocompleteState,
    CommandCompleter,
    CommandRegistry,
    FernetEncryptor,
    JsonFormatter,
    add_to_history,
    anonymize_pii,
    execute_macro,
    fuzzy_matches,
    get_ai_suggestions,
    handle_command_not_found,
    is_toxic,
    log_audit_event,
    setup_autocomplete,
)


# --- Test Fixtures ---
@pytest.fixture
def mock_autocomplete_state():
    """Mock AutocompleteState instance."""
    # Reset the singleton
    AutocompleteState._instance = None

    # Create state manually without calling instance()
    state = AutocompleteState()

    # Mock the redis client
    state.redis_client = AsyncMock()
    state.redis_client.ping = AsyncMock(return_value=True)
    state.redis_client.get = AsyncMock(return_value=None)
    state.redis_client.set = AsyncMock(return_value=True)
    state.redis_client.lrange = AsyncMock(return_value=[])
    state.redis_client.rpush = AsyncMock(return_value=1)

    # Mock the encryptor
    state.encryptor = MagicMock(spec=FernetEncryptor)
    state.encryptor.encrypt = MagicMock(return_value=b"encrypted")
    state.encryptor.decrypt = MagicMock(return_value="decrypted")

    # Set other attributes
    state.llm_provider = "openai"
    state.llm_token_count = 0

    # Set as singleton instance
    AutocompleteState._instance = state

    yield state

    # Clean up
    AutocompleteState._instance = None


@pytest.fixture
def mock_llm():
    """Mock LLM for AI suggestions."""
    mock_response = MagicMock()
    mock_response.content = '["suggestion1", "suggestion2"]'
    mock_response.token_usage = 100
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    return mock_llm


@pytest.fixture
def mock_readline():
    """Mock readline for completer testing."""
    with patch("intent_capture.autocomplete.readline") as mock_rl:
        mock_rl.get_line_buffer.return_value = ""
        mock_rl.add_history = MagicMock()
        mock_rl.get_current_history_length.return_value = 0
        mock_rl.get_history_item.return_value = "test_history"
        mock_rl.set_completer = MagicMock()
        mock_rl.parse_and_bind = MagicMock()
        yield mock_rl


@pytest.fixture
def mock_logger():
    """Mock logger to capture logs."""
    with patch("intent_capture.autocomplete.logger") as mock_log:
        yield mock_log


# --- Tests for Logging Setup ---
def test_json_formatter():
    """Test JsonFormatter outputs valid JSON."""
    formatter = JsonFormatter()
    record = logging.LogRecord("name", logging.INFO, "path", 10, "message", (), None)
    formatted = formatter.format(record)
    parsed = json.loads(formatted)  # Should not raise JSONDecodeError
    assert "timestamp" in parsed
    assert "level" in parsed
    assert "message" in parsed


def test_anonymize_pii():
    """Test PII anonymization."""
    text = "Email: test@example.com, IP: 192.168.1.1, Name: John Doe"
    anonymized = anonymize_pii(text)
    assert "[REDACTED_EMAIL]" in anonymized
    assert "[REDACTED_IP]" in anonymized
    assert "test@example.com" not in anonymized


# --- Tests for AutocompleteState ---
@pytest.mark.asyncio
async def test_autocomplete_state_singleton():
    """Test AutocompleteState singleton pattern."""
    with patch.object(AutocompleteState, "_initialize_dependencies", new_callable=AsyncMock):
        state1 = await AutocompleteState.instance()
        state2 = await AutocompleteState.instance()
        assert state1 is state2
        AutocompleteState._instance = None  # Clean up


@pytest.mark.asyncio
async def test_autocomplete_state_initialize_redis():
    """Test Redis initialization in AutocompleteState."""
    # Test actual initialization logic
    with patch.dict(os.environ, {"CLI_REDIS_URL": "redis://localhost:6379"}):
        with patch("intent_capture.autocomplete.aredis.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            mock_from_url.return_value = mock_client

            # Reset singleton and create new state
            AutocompleteState._instance = None
            state = AutocompleteState()
            await state._initialize_redis()

            assert state.redis_client is not None
            mock_client.ping.assert_called()
            AutocompleteState._instance = None  # Clean up


# --- Tests for CommandRegistry ---
def test_command_registry_initialization():
    """Test CommandRegistry initialization."""
    registry = CommandRegistry()
    assert "help" in registry.all_commands
    assert "exit" in registry.all_commands
    assert "ai:" in registry.all_commands


def test_command_registry_update_all_commands():
    """Test updating all commands in registry."""
    registry = CommandRegistry()
    registry.update_all_commands()
    assert len(registry.all_commands) > 0
    assert all(isinstance(cmd, str) for cmd in registry.all_commands)


# --- Tests for FernetEncryptor ---
def test_fernet_encryptor():
    """Test FernetEncryptor encrypt/decrypt."""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    encryptor = FernetEncryptor(key)

    original = "test data"
    encrypted = encryptor.encrypt(original)
    decrypted = encryptor.decrypt(encrypted)

    assert decrypted == original
    assert encrypted != original.encode()


# --- Tests for Toxicity Detection ---
def test_is_toxic():
    """Test toxicity detection (mocked)."""
    with patch("intent_capture.autocomplete._moderation_pipeline") as mock_pipeline:
        mock_mdl = MagicMock()
        mock_mdl.return_value = [[{"label": "TOXIC", "score": 0.8}]]
        mock_pipeline.return_value.__enter__.return_value = mock_mdl

        assert is_toxic("bad content")

        mock_mdl.return_value = [[{"label": "NOT_TOXIC", "score": 0.9}]]
        assert not is_toxic("good content")


# --- Tests for History Management ---
def test_add_to_history(mock_readline):
    """Test adding command to history."""
    # Mock the state and ensure it's returned by instance()
    mock_state = MagicMock()
    mock_state.encryptor = MagicMock(spec=FernetEncryptor)
    mock_state.encryptor.encrypt.return_value = b"encrypted"
    AutocompleteState._instance = mock_state

    with patch("intent_capture.autocomplete.asyncio.run") as mock_run:
        # Make asyncio.run return the mock state
        mock_run.return_value = mock_state

        add_to_history("test command")
        mock_readline.add_history.assert_called()

    AutocompleteState._instance = None  # Clean up


# --- Tests for Command Not Found ---
def test_handle_command_not_found(capsys):
    """Test handle_command_not_found suggestions."""
    state = MagicMock(spec=AutocompleteState)
    state.command_registry = CommandRegistry()

    with patch("intent_capture.autocomplete.asyncio.run") as mock_run:
        mock_run.return_value = ["help", "exit"]

        handle_command_not_found("hel", state)

        captured = capsys.readouterr()
        assert "Command not found" in captured.out
        assert "help" in captured.out


# --- Tests for AI Suggestions ---
@pytest.mark.asyncio
async def test_get_ai_suggestions_success(mock_llm, mock_autocomplete_state):
    """Test successful AI suggestions."""
    state = mock_autocomplete_state
    state.llm_instance = mock_llm
    state.llm_provider = "openai"

    with patch("intent_capture.autocomplete.is_toxic", return_value=False):
        with patch("intent_capture.autocomplete.os.getlogin", return_value="testuser"):
            suggestions = await get_ai_suggestions("test query", state)
            assert suggestions == ["suggestion1", "suggestion2"]
            mock_llm.ainvoke.assert_called_once()


@pytest.mark.asyncio
async def test_get_ai_suggestions_no_llm(mock_autocomplete_state):
    """Test AI suggestions when no LLM is provided."""
    state = mock_autocomplete_state
    state.llm_instance = None

    suggestions = await get_ai_suggestions("test query", state)
    assert suggestions == []


@pytest.mark.asyncio
async def test_get_ai_suggestions_filters_toxic(mock_llm, mock_autocomplete_state):
    """Test AI suggestions filters toxic content."""
    state = mock_autocomplete_state
    state.llm_instance = mock_llm

    with patch("intent_capture.autocomplete.is_toxic") as mock_toxic:
        mock_toxic.side_effect = [True, False]  # First is toxic, second is not
        with patch("intent_capture.autocomplete.os.getlogin", return_value="testuser"):
            suggestions = await get_ai_suggestions("test query", state)
            assert suggestions == ["suggestion2"]  # Only non-toxic suggestion


# --- Tests for Fuzzy Matching ---
@pytest.mark.asyncio
async def test_fuzzy_matches_with_cache(mock_autocomplete_state):
    """Test fuzzy matching with Redis cache."""
    state = mock_autocomplete_state
    state.redis_client.get.return_value = '["cached1", "cached2"]'

    matches = await fuzzy_matches("cmd", ["cmd1", "cmd2"], state)
    assert matches == ["cached1", "cached2"]
    state.redis_client.get.assert_called_once()


@pytest.mark.asyncio
async def test_fuzzy_matches_without_cache(mock_autocomplete_state):
    """Test fuzzy matching without cache hit."""
    state = mock_autocomplete_state
    state.redis_client.get.return_value = None

    with patch("intent_capture.autocomplete.extract") as mock_extract:
        mock_extract.return_value = [("cmd1", 90), ("cmd2", 85)]

        matches = await fuzzy_matches("cmd", ["cmd1", "cmd2", "other"], state)
        assert matches == ["cmd1", "cmd2"]
        state.redis_client.set.assert_called_once()


# --- Tests for CommandCompleter ---
def test_command_completer_basic(mock_readline):
    """Test basic command completion."""
    mock_readline.get_line_buffer.return_value = "hel"

    # Create a mock state and set it as singleton
    mock_state = MagicMock(spec=AutocompleteState)
    mock_state.command_registry = CommandRegistry()
    AutocompleteState._instance = mock_state

    # Mock the async operations to avoid event loop issues
    async def mock_async_complete(self, text, state_index):
        return ["help", "hello"][state_index] if state_index < 2 else None

    with patch.object(CommandCompleter, "_async_complete", mock_async_complete):
        # Create new event loop in thread to avoid conflicts
        def run_in_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                completer = CommandCompleter()
                results = []
                results.append(completer.complete("hel", 0))
                results.append(completer.complete("hel", 1))
                return results
            finally:
                loop.close()

        results = run_in_thread()
        assert results[0] == "help"
        assert results[1] == "hello"

    AutocompleteState._instance = None


def test_command_completer_ai_suggestions(mock_readline, mock_llm):
    """Test completer for AI suggestions."""
    mock_readline.get_line_buffer.return_value = "ai: test query"

    # Create a mock state and set it as singleton
    mock_state = MagicMock(spec=AutocompleteState)
    mock_state.command_registry = CommandRegistry()
    mock_state.llm_instance = mock_llm
    AutocompleteState._instance = mock_state

    async def mock_async_complete(self, text, state_index):
        # Simulate AI suggestion flow
        return ["suggestion1", "suggestion2"][state_index] if state_index < 2 else None

    with patch.object(CommandCompleter, "_async_complete", mock_async_complete):

        def run_in_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                completer = CommandCompleter()
                results = []
                results.append(completer.complete("", 0))
                results.append(completer.complete("", 1))
                return results
            finally:
                loop.close()

        results = run_in_thread()
        assert results[0] == "suggestion1"
        assert results[1] == "suggestion2"

    AutocompleteState._instance = None


# --- Tests for Macro Execution ---
def test_execute_macro_success():
    """Test successful macro execution."""
    mock_state = MagicMock()
    mock_state.macros = {"gs": lambda args: f"generate spec {' '.join(args)}"}
    AutocompleteState._instance = mock_state

    with patch("intent_capture.autocomplete.asyncio.run") as mock_run:
        mock_run.return_value = mock_state

        result = execute_macro("gs my_spec")
        assert result == "generate spec my_spec"

    AutocompleteState._instance = None


def test_execute_macro_unknown():
    """Test unknown macro returns original input."""
    mock_state = MagicMock()
    mock_state.macros = {}
    AutocompleteState._instance = mock_state

    with patch("intent_capture.autocomplete.asyncio.run") as mock_run:
        mock_run.return_value = mock_state

        result = execute_macro("unknown command")
        assert result == "unknown command"

    AutocompleteState._instance = None


# --- Tests for Setup ---
def test_setup_autocomplete(mock_readline):
    """Test autocomplete setup."""
    # Create a mock state and set it as singleton
    mock_state = MagicMock(spec=AutocompleteState)
    mock_state.encryptor = None
    mock_state.llm_instance = None
    mock_state.redis_client = None
    mock_state.command_registry = CommandRegistry()

    # Mock the async initialization
    async def mock_instance():
        return mock_state

    with patch.object(AutocompleteState, "instance", side_effect=mock_instance):
        # Mock asyncio.run to execute coroutines in a new loop
        def run_coro(coro):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        with patch("intent_capture.autocomplete.asyncio.run", side_effect=run_coro):
            # Mock asyncio.create_task to prevent background tasks
            with patch("intent_capture.autocomplete.asyncio.create_task"):
                # Mock atexit to prevent side effects
                with patch("intent_capture.autocomplete.atexit.register"):
                    setup_autocomplete()

                    mock_readline.set_completer.assert_called_once()
                    mock_readline.parse_and_bind.assert_called_with("tab: complete")

    # Clean up
    AutocompleteState._instance = None


# --- Test for audit logging ---
def test_log_audit_event():
    """Test audit event logging."""
    with patch.dict(
        os.environ,
        {
            "ENABLE_AUDIT": "true",
            "AUDIT_BUCKET": "test-bucket",
            "AWS_REGION": "us-east-1",
        },
    ):
        with patch("intent_capture.autocomplete.boto3.client") as mock_boto:
            with patch("intent_capture.autocomplete.os.getlogin", return_value="testuser"):
                mock_s3 = MagicMock()
                mock_boto.return_value = mock_s3

                log_audit_event("test command", "test result")
                mock_s3.put_object.assert_called_once()

                call_args = mock_s3.put_object.call_args
                assert call_args.kwargs["Bucket"] == "test-bucket"
                assert (
                    "test command" in call_args.kwargs["Body"]
                    or "[REDACTED" in call_args.kwargs["Body"]
                )
