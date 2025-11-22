# test_utils.py
# Comprehensive production-grade tests for utils.py
# Requires: pytest, pytest-asyncio, unittest.mock
# Run with: pytest test_utils.py -v --cov=utils --cov-report=html

import json
import time
from datetime import datetime, date, time as dt_time
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

# Import the module under test
from arbiter.explainable_reasoner.utils import (
    _sanitize_context,
    _simple_text_sanitize,
    _rule_based_fallback,
    _format_multimodal_for_prompt,
    rate_limited,
)
from arbiter.explainable_reasoner.reasoner_config import ReasonerConfig, SensitiveValue


# --- Fixtures ---
@pytest.fixture(autouse=True)
def mock_dependencies():
    """Mocks dependencies that are not the focus of the tests."""
    with patch(
        "arbiter.explainable_reasoner.metrics.get_or_create_metric"
    ) as mock_get_metric, patch(
        "arbiter.explainable_reasoner.utils._utils_logger"
    ) as mock_logger:

        # Configure metric mock
        def create_metric(*args, **kwargs):
            metric = MagicMock()
            metric.labels.return_value = MagicMock(
                inc=MagicMock(), dec=MagicMock(), set=MagicMock(), observe=MagicMock()
            )
            return metric

        mock_get_metric.side_effect = create_metric

        # Configure logger
        mock_logger.info = MagicMock()
        mock_logger.error = MagicMock()
        mock_logger.warning = MagicMock()
        mock_logger.debug = MagicMock()

        yield {"logger": mock_logger, "get_metric": mock_get_metric}


@pytest.fixture
def mock_redis():
    """Mock aioredis for rate limiting tests."""
    with patch("arbiter.explainable_reasoner.utils.REDIS_AVAILABLE", True):
        with patch("arbiter.explainable_reasoner.utils.aioredis") as mock_aioredis:
            mock_client = AsyncMock()
            mock_client.eval = AsyncMock(return_value=0)  # No wait time
            mock_aioredis.Redis = MagicMock(return_value=mock_client)
            yield mock_client


@pytest.fixture
def dummy_multimodal_obj():
    """Provides a multimodal data object."""
    from arbiter.explainable_reasoner.utils import MultiModalData

    return MultiModalData(
        data_type="image",
        data=b"test_image_data",
        metadata={"width": 100, "height": 100},
    )


# --- Test Cases ---


# Test _sanitize_context
@pytest.mark.parametrize(
    "context, options, expected",
    [
        (
            {"api_key": "secret", "normal": "value"},
            {"redact_keys": ["api_key"]},
            {"api_key": "[REDACTED]", "normal": "value"},
        ),
        (
            {"outer": {"inner": {"password": "pass"}}},
            {"redact_keys": ["password"]},
            {"outer": {"inner": {"password": "[REDACTED]"}}},
        ),
        (
            {"card": "1234-5678-9012-3456"},
            {"redact_patterns": [r"\b\d{4}-\d{4}-\d{4}-\d{4}\b"]},
            {"card": "[REDACTED]"},
        ),
        (
            {"list": [{"key": "secret"}]},
            {"redact_keys": ["key"]},
            {"list": [{"key": "[REDACTED]"}]},
        ),
        ({"sens": SensitiveValue("hidden")}, {}, {"sens": "[REDACTED]"}),
        (
            {"func": lambda: None},
            {},
            {"func": "<function <lambda>"},  # Python's string representation
        ),
    ],
)
@pytest.mark.asyncio
async def test_sanitize_context_features(context, options, expected):
    config = ReasonerConfig(sanitization_options=options)
    result = await _sanitize_context(context, config)

    # For lambda functions, just check it's converted to string
    if "func" in context and callable(context["func"]):
        assert isinstance(result["func"], str)
        assert "<lambda>" in result["func"] or "function" in result["func"]
    else:
        assert result == expected


@pytest.mark.asyncio
async def test_sanitize_context_max_depth():
    context = {"l1": {"l2": {"l3": "deep"}}}
    config = ReasonerConfig(sanitization_options={"max_nesting_depth": 2})
    result = await _sanitize_context(context, config)
    assert result == {"l1": {"l2": "[MAX_DEPTH_EXCEEDED]"}}


@pytest.mark.asyncio
async def test_sanitize_context_multimodal(dummy_multimodal_obj):
    context = {"mm": dummy_multimodal_obj}
    config = ReasonerConfig(sanitization_options={"max_nesting_depth": 3})
    result = await _sanitize_context(context, config)

    # Should format multimodal data properly
    assert "mm" in result
    # The multimodal object should be converted to a formatted string
    assert isinstance(result["mm"], str) or isinstance(result["mm"], dict)


@pytest.mark.asyncio
async def test_sanitize_context_errors(mock_dependencies):
    # Test max depth exceeded
    config_depth = ReasonerConfig(sanitization_options={"max_nesting_depth": 2})
    result_depth = await _sanitize_context(
        {"too_deep": {"nest": {"more": "deeper"}}}, config_depth
    )
    assert result_depth == {"too_deep": {"nest": "[MAX_DEPTH_EXCEEDED]"}}

    # Test circular reference
    config_circular = ReasonerConfig(sanitization_options={"max_nesting_depth": 5})
    context = {"circular": []}
    context["circular"].append(context)  # Create circular reference
    result_circular = await _sanitize_context(context, config_circular)
    assert "[CIRCULAR_REFERENCE]" in str(result_circular)


@pytest.mark.asyncio
async def test_sanitize_context_redaction_count(mock_dependencies):
    context = {"api_key": "secret", "password": "pass"}
    config = ReasonerConfig(
        sanitization_options={"redact_keys": ["api_key", "password"]}
    )
    result = await _sanitize_context(context, config)

    assert result["api_key"] == "[REDACTED]"
    assert result["password"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_sanitize_context_edge_cases():
    # Empty context
    config_empty = ReasonerConfig()
    result = await _sanitize_context({}, config_empty)
    assert result == {}

    # Large context that exceeds size limit
    config_size = ReasonerConfig(sanitization_options={"max_size_bytes": 100})
    large_context = {"key": "a" * 200}
    result = await _sanitize_context(large_context, config_size)
    # Should truncate or handle gracefully
    assert "_truncated_context_error" in result or len(json.dumps(result)) <= 100


@pytest.mark.asyncio
async def test_sanitize_primitive_types():
    primitives = {
        "dt": datetime(2025, 8, 6),
        "d": date(2025, 8, 6),
        "t": dt_time(12, 30),
        "path": Path("/test/path"),
        "sens": SensitiveValue("secret"),
    }
    config = ReasonerConfig()
    result = await _sanitize_context(primitives, config)

    assert result["dt"] == "2025-08-06T00:00:00"
    assert result["d"] == "2025-08-06"
    assert result["t"] == "12:30:00"
    # Handle both Unix and Windows path separators
    assert result["path"] in ["/test/path", "\\test\\path"]
    assert result["sens"] == "[REDACTED]"


# Test _simple_text_sanitize
@pytest.mark.parametrize(
    "text, max_len, expected",
    [
        ("Clean text", 100, "Clean text"),
        (" leading/trailing whitespace ", 100, "leading/trailing whitespace"),
        ("<script>alert('xss')</script>", 100, "alert('xss')"),
        ("A\x00B\x1fC", 100, "ABC"),
        ("Long text" * 10, 50, "Long textLong textLong textLong textLong textLong "),
        ("12345", 100, "12345"),
    ],
)
def test_simple_text_sanitize(text, max_len, expected):
    result = _simple_text_sanitize(text, max_len)
    assert result == expected


# Test _rule_based_fallback
def test_rule_based_fallback():
    result_explain = _rule_based_fallback("What is AI?", {}, "explain")
    assert "fallback" in result_explain.lower()
    assert "explanation" in result_explain.lower() or "ai" in result_explain.lower()

    result_reason = _rule_based_fallback("Reason about ML", {}, "reason")
    assert "fallback" in result_reason.lower()
    assert "reasoning" in result_reason.lower() or "ml" in result_reason.lower()

    result_unknown = _rule_based_fallback("Invalid", {}, "unknown_task")
    assert "fallback" in result_unknown.lower()
    assert "could not process" in result_unknown.lower()


# Test _format_multimodal_for_prompt
def test_format_multimodal_for_prompt(dummy_multimodal_obj):
    # Test with MultiModalData object
    result = _format_multimodal_for_prompt(dummy_multimodal_obj)
    # Should return empty string for base MultiModalData
    assert result == ""

    # Test with primitive
    assert _format_multimodal_for_prompt(123) == ""
    assert _format_multimodal_for_prompt("text") == ""


# Test rate_limited Decorator
@pytest.mark.asyncio
async def test_rate_limited_local_delay():
    """Test local rate limiting without Redis."""

    @rate_limited(calls_per_second=10.0)  # 10 calls per second = 0.1s between calls
    async def fast_func():
        return time.monotonic()

    start_time = await fast_func()
    end_time = await fast_func()
    # Should have some delay
    assert (end_time - start_time) >= 0.09  # Allow small timing variance


@pytest.mark.asyncio
async def test_rate_limited_with_redis(mock_redis):
    """Test Redis-based rate limiting."""
    mock_redis_client = AsyncMock()
    mock_redis_client.eval = AsyncMock(return_value=0)  # No wait needed

    class MyClass:
        _redis_client = mock_redis_client

        @rate_limited(
            calls_per_second=10.0,
            key_extractor=lambda self, kwargs: kwargs.get("user_id", "global"),
        )
        async def do_work(self, user_id: str):
            return f"work done for {user_id}"

    instance = MyClass()
    result1 = await instance.do_work(user_id="user1")
    result2 = await instance.do_work(user_id="user2")

    assert result1 == "work done for user1"
    assert result2 == "work done for user2"

    # Check Redis interactions
    assert mock_redis_client.eval.call_count >= 2


@pytest.mark.asyncio
async def test_rate_limited_redis_delay():
    """Test that rate limiting enforces delays."""
    mock_redis_client = AsyncMock()
    # Return wait time of 0.5 seconds
    mock_redis_client.eval = AsyncMock(return_value=0.5)

    class MyClass:
        _redis_client = mock_redis_client

        @rate_limited(calls_per_second=1.0)  # 1 call per second
        async def do_work(self):
            return "done"

    instance = MyClass()
    start = time.monotonic()
    result = await instance.do_work()
    duration = time.monotonic() - start

    # Should wait approximately 0.5s
    assert duration >= 0.45
    assert result == "done"


@pytest.mark.asyncio
async def test_rate_limited_no_redis():
    """Test fallback when Redis is not available."""

    @rate_limited(calls_per_second=10.0)
    async def func():
        return time.monotonic()

    # Should still work with local rate limiting
    start_time = await func()
    end_time = await func()
    assert (end_time - start_time) >= 0.09


@pytest.mark.asyncio
async def test_rate_limited_error_handling():
    """Test that rate limiting handles Redis errors gracefully."""
    mock_redis_client = AsyncMock()
    mock_redis_client.eval = AsyncMock(side_effect=Exception("Redis error"))

    class MyClass:
        _redis_client = mock_redis_client

        @rate_limited(calls_per_second=10.0)
        async def do_work(self):
            return "done"

    instance = MyClass()
    # Should fall back to local limiting and not raise
    result = await instance.do_work()
    assert result == "done"


# Test multimodal formatting edge cases
def test_format_multimodal_edge_cases():
    """Test edge cases in multimodal formatting."""
    # None input
    assert _format_multimodal_for_prompt(None) == ""

    # Empty dict
    assert _format_multimodal_for_prompt({}) == ""

    # String input
    assert _format_multimodal_for_prompt("text") == ""

    # Numeric input
    assert _format_multimodal_for_prompt(123) == ""


# Additional edge case tests
@pytest.mark.asyncio
async def test_sanitize_context_with_none_values():
    """Test sanitization with None values."""
    context = {"key1": None, "key2": {"nested": None}, "key3": [None, "value", None]}
    config = ReasonerConfig()
    result = await _sanitize_context(context, config)

    assert result["key1"] is None
    assert result["key2"]["nested"] is None
    assert result["key3"] == [None, "value", None]


def test_simple_text_sanitize_unicode():
    """Test text sanitization with unicode characters."""
    text = "Hello 世界 🌍 \u200b"  # Includes Chinese, emoji, zero-width space
    result = _simple_text_sanitize(text, 100)
    assert "世界" in result
    assert "🌍" in result
    # Zero-width space should be removed
    assert "\u200b" not in result


@pytest.mark.asyncio
async def test_sanitize_context_performance():
    """Test that sanitization handles large contexts efficiently."""
    # Create a large nested structure
    large_context = {}
    for i in range(100):
        large_context[f"key_{i}"] = {
            "nested": {"data": f"value_{i}" * 10, "list": list(range(10))}
        }

    # Increase max_size_bytes to handle the large context
    config = ReasonerConfig(
        sanitization_options={"max_nesting_depth": 3, "max_size_bytes": 100000}
    )
    start = time.monotonic()
    result = await _sanitize_context(large_context, config)
    duration = time.monotonic() - start

    # Should complete in reasonable time
    assert duration < 1.0  # Less than 1 second for large context
    assert len(result) == 100
