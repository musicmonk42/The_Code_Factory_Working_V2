import sqlite3
from datetime import datetime
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from self_fixing_engineer.simulation.explain import (
    ExplainableReasoner,
    ExplainableReasonerPlugin,
    ExplanationResult,
    HistoryManager,
    ReasonerConfig,
    ReasonerError,
    ReasoningHistory,
    ReasoningResult,
    _rule_based_fallback,
    _sanitize_context,
    _sanitize_input,
)

# Mark all tests as unit tests for selective running
pytestmark = pytest.mark.unit


@pytest.fixture
def temp_db_path(tmp_path):
    """Fixture for a temporary database path."""
    return tmp_path / "test_history.db"


@pytest.fixture
def mock_config():
    """Fixture for a mock ReasonerConfig."""
    return ReasonerConfig(
        model_name="test_model",
        device=0,
        max_workers=2,
        generation_timeout=10,
        max_generation_tokens=100,
        temperature_explain=0.5,
        temperature_reason=0.6,
        history_db_path="test_history.db",
        max_history_size=10,
        strict_mode=False,
        mock_mode=True,
        log_prompts=False,
    )


@pytest.fixture
def mock_settings():
    """Fixture for mock settings object."""

    class MockSettings:
        LLM_API_URL = "https://api.example.com"
        LLM_API_KEY_LOADED = True
        TRANSFORMERS_OFFLINE = False

    return MockSettings()


@pytest.fixture
def mock_history_manager(temp_db_path):
    """Fixture for HistoryManager with temporary DB."""
    manager = HistoryManager(str(temp_db_path), max_size=10)
    return manager


@pytest_asyncio.fixture
async def reasoner_with_shutdown(mock_config, mock_settings):
    """Fixture to create and properly shut down an ExplainableReasoner instance."""
    # Patch run_in_executor to work without a real model/tokenizer
    with (
        patch("simulation.explain.AutoTokenizer.from_pretrained", MagicMock()),
        patch("simulation.explain.AutoModelForCausalLM.from_pretrained", MagicMock()),
        patch(
            "simulation.explain.pipeline",
            MagicMock(
                return_value=MagicMock(
                    tokenizer=MagicMock(pad_token_id=0, eos_token_id=1)
                )
            ),
        ),
        patch("simulation.explain.psutil.__spec__", MagicMock()),
    ):

        reasoner = ExplainableReasoner(mock_settings, mock_config)
        # Manually patch the executor to handle kwargs
        with patch.object(
            reasoner.executor, "submit", wraps=reasoner.executor.submit
        ) as mock_submit:

            def side_effect(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_submit.side_effect = (
                lambda func, *args, **kwargs: reasoner.executor.submit(
                    partial(func, **kwargs) if kwargs else func, *args
                )
            )
            await reasoner.async_init()
            try:
                yield reasoner
            finally:
                await reasoner.shutdown()


# --- Tests for Dataclasses ---


def test_explanation_result_dataclass():
    """Test ExplanationResult dataclass."""
    result = ExplanationResult(
        id="test_id",
        query="test_query",
        explanation="test_explanation",
        context_used={"key": "value"},
        generated_by="test_model",
        timestamp="2025-08-04T12:00:00",
    )
    assert result.id == "test_id"
    assert result.query == "test_query"


# Similar tests for ReasoningResult and ReasoningHistory can be added if needed

# --- Tests for _sanitize_input ---


@pytest.mark.parametrize(
    "input_text, expected",
    [
        ("clean text", "clean text"),
        ("  text with spaces  ", "text with spaces"),
        ("\x00invalid\x1fcontrol", "invalidcontrol"),
        ("<script>alert('xss')</script>", "alert('xss')"),
        ("a" * 2000, "a" * 1024),  # Truncation
        ("", None),  # Empty after sanitization
    ],
)
def test_sanitize_input(input_text, expected):
    """Test input sanitization."""
    if expected is None:
        with pytest.raises(
            ReasonerError, match="Input is empty or invalid after sanitization"
        ):
            _sanitize_input(input_text)
    else:
        assert _sanitize_input(input_text) == expected


# --- Tests for _sanitize_context ---


@pytest.mark.parametrize(
    "input_context, expected",
    [
        ({"key": "value"}, {"key": "value"}),
        ({"datetime": datetime(2025, 8, 4)}, {"datetime": "2025-08-04T00:00:00"}),
        ({"path": Path("/test")}, {"path": str(Path("/test"))}),
        ({"set": {1, 2}}, {"set": [1, 2]}),
        ({"large": "a" * 5000}, None),  # Size exceed
    ],
)
def test_sanitize_context(input_context, expected):
    """Test context sanitization."""
    if expected is None:
        sanitized = _sanitize_context(input_context, max_size_bytes=10)
        assert "_truncated_context_error" in sanitized
    else:
        assert _sanitize_context(input_context) == expected


# --- Tests for _rule_based_fallback ---


def test_rule_based_fallback():
    """Test rule-based fallback explanation."""
    fallback = _rule_based_fallback(
        "test_query", {"summary": "test_summary"}, "explain"
    )
    assert "Fallback" in fallback
    assert "test_query" in fallback


# --- Tests for HistoryManager ---


@pytest.mark.asyncio
async def test_history_manager_init_db(mock_history_manager):
    """Test HistoryManager database initialization."""
    await mock_history_manager.init_db()
    conn = sqlite3.connect(mock_history_manager.db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='reasoner_history'"
    )
    assert cursor.fetchone() is not None
    conn.close()


@pytest.mark.asyncio
async def test_history_manager_add_entry(mock_history_manager):
    """Test adding an entry to HistoryManager."""
    await mock_history_manager.init_db()
    entry = ReasoningHistory(
        id="test_id",
        query="test_query",
        context={"key": "value"},
        response="test_response",
        response_type="test_type",
        timestamp="2025-08-04T12:00:00",
    )
    await mock_history_manager.add_entry(entry)
    size = await mock_history_manager.get_size()
    assert size == 1


@pytest.mark.asyncio
async def test_history_manager_get_entries(mock_history_manager):
    """Test getting entries from HistoryManager."""
    await mock_history_manager.init_db()
    entry = ReasoningHistory(
        id="test_id",
        query="test_query",
        context={"key": "value"},
        response="test_response",
        response_type="test_type",
        timestamp="2025-08-04T12:00:00",
    )
    await mock_history_manager.add_entry(entry)
    entries = await mock_history_manager.get_entries(limit=1)
    assert len(entries) == 1
    assert entries[0].id == "test_id"


@pytest.mark.asyncio
async def test_history_manager_clear(mock_history_manager):
    """Test clearing HistoryManager."""
    await mock_history_manager.init_db()
    entry = ReasoningHistory(
        id="test_id",
        query="test_query",
        context={"key": "value"},
        response="test_response",
        response_type="test_type",
        timestamp="2025-08-04T12:00:00",
    )
    await mock_history_manager.add_entry(entry)
    await mock_history_manager.clear()
    size = await mock_history_manager.get_size()
    assert size == 0


# --- Tests for ExplainableReasoner ---


@pytest.mark.asyncio
async def test_explainable_reasoner_explain(reasoner_with_shutdown):
    """Test explain method of ExplainableReasoner."""
    explanation = await reasoner_with_shutdown.explain(
        "test_query", {"summary": "test_summary"}
    )
    assert isinstance(explanation, ExplanationResult)
    assert "Fallback" in explanation.explanation


@pytest.mark.asyncio
async def test_explainable_reasoner_reason(reasoner_with_shutdown):
    """Test reason method of ExplainableReasoner."""
    reasoning = await reasoner_with_shutdown.reason(
        "test_query", {"summary": "test_summary"}
    )
    assert isinstance(reasoning, ReasoningResult)
    assert "Fallback" in reasoning.reasoning


# --- Tests for ExplainableReasonerPlugin ---


@pytest.mark.asyncio
async def test_explainable_reasoner_plugin_explain_result(
    reasoner_with_shutdown, mock_settings, mock_config
):
    """Test explain_result method of ExplainableReasonerPlugin."""
    plugin = ExplainableReasonerPlugin(settings=mock_settings)
    plugin.config = mock_config
    await plugin.initialize()
    result = {"id": "test_id", "status": "COMPLETED"}
    explanation = await plugin.explain_result(result)
    assert isinstance(explanation, str)
    assert "Fallback" in explanation


@pytest.mark.asyncio
async def test_explainable_reasoner_plugin_execute(
    reasoner_with_shutdown, mock_settings, mock_config
):
    """Test execute method of ExplainableReasonerPlugin."""
    plugin = ExplainableReasonerPlugin(settings=mock_settings)
    plugin.config = mock_config
    await plugin.initialize()
    result = await plugin.execute(
        "explain", result={"id": "test_id", "status": "COMPLETED"}
    )
    assert isinstance(result, str)
