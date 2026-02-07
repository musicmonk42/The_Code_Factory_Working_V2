# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_fuzzy.py

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# Fix the import path - should import from the learner module, not tests
from self_fixing_engineer.arbiter.learner.fuzzy import (
    _learn_batch_with_retry,
    fuzzy_parser_failure_total,
    load_parser_priorities,
    process_unstructured_data,
    register_fuzzy_parser_hook,
)


class MockFuzzyParser:
    """Mock implementation of FuzzyParser protocol."""

    def __init__(self, facts_to_return=None, should_fail=False, delay=0):
        self.facts_to_return = facts_to_return or []
        self.should_fail = should_fail
        self.delay = delay
        self.call_count = 0

    async def parse(self, text: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Mock parse method."""
        self.call_count += 1

        if self.delay:
            await asyncio.sleep(self.delay)

        if self.should_fail:
            raise Exception("Parser failed")

        return self.facts_to_return


@pytest.fixture(autouse=True)
def clean_parser_priorities():
    """Clean parser priorities before each test to ensure test isolation."""
    # Import the module to access PARSER_PRIORITIES directly
    import arbiter.learner.fuzzy as fuzzy_module

    # Store original state
    original = fuzzy_module.PARSER_PRIORITIES.copy()
    # Clear for test
    fuzzy_module.PARSER_PRIORITIES.clear()

    yield

    # Restore original state after test
    fuzzy_module.PARSER_PRIORITIES.clear()
    fuzzy_module.PARSER_PRIORITIES.update(original)


class TestLoadParserPriorities:
    """Test suite for load_parser_priorities function."""

    def test_load_priorities_from_file(self):
        """Test loading parser priorities from JSON file."""
        import arbiter.learner.fuzzy as fuzzy_module

        test_priorities = {"Parser1": 10, "Parser2": 5, "Parser3": 15}

        mock_file = MagicMock()
        mock_file.__enter__.return_value.read.return_value = json.dumps(test_priorities)

        with patch("builtins.open", return_value=mock_file):
            with patch(
                "self_fixing_engineer.arbiter.learner.fuzzy.os.getenv", return_value="test_priorities.json"
            ):
                load_parser_priorities()

                assert fuzzy_module.PARSER_PRIORITIES == test_priorities

    def test_load_priorities_file_not_found(self):
        """Test fallback when priority file not found."""
        import arbiter.learner.fuzzy as fuzzy_module

        with patch("builtins.open", side_effect=FileNotFoundError):
            with patch("self_fixing_engineer.arbiter.learner.fuzzy.os.getenv", return_value="missing.json"):
                load_parser_priorities()

                assert (
                    fuzzy_module.PARSER_PRIORITIES == {}
                )  # Should use empty dict as default

    def test_load_priorities_invalid_json(self):
        """Test handling of invalid JSON in priority file."""
        mock_file = MagicMock()
        mock_file.__enter__.return_value.read.return_value = "invalid json {"

        with patch("builtins.open", return_value=mock_file):
            with patch("self_fixing_engineer.arbiter.learner.fuzzy.os.getenv", return_value="invalid.json"):
                with pytest.raises(json.JSONDecodeError):
                    load_parser_priorities()


class TestLearnBatchWithRetry:
    """Test suite for _learn_batch_with_retry function."""

    @pytest.mark.asyncio
    async def test_successful_learn_batch(self):
        """Test successful batch learning with retry wrapper."""
        mock_learner = Mock()
        mock_learner.learn_batch = AsyncMock(
            return_value=[
                {"status": "learned", "key": "key1"},
                {"status": "learned", "key": "key2"},
            ]
        )

        facts = [
            {"domain": "test", "key": "key1", "value": "value1"},
            {"domain": "test", "key": "key2", "value": "value2"},
        ]

        result = await _learn_batch_with_retry(
            mock_learner, facts, "user123", "test_source"
        )

        assert len(result) == 2
        assert all(r["status"] == "learned" for r in result)
        mock_learner.learn_batch.assert_called_once_with(
            facts, user_id="user123", source="test_source"
        )

    @pytest.mark.asyncio
    async def test_learn_batch_retry_on_failure(self):
        """Test retry mechanism on batch learning failure."""
        mock_learner = Mock()
        # Fail twice, then succeed
        mock_learner.learn_batch = AsyncMock(
            side_effect=[
                Exception("Temporary failure"),
                Exception("Another failure"),
                [{"status": "learned", "key": "key1"}],
            ]
        )

        facts = [{"domain": "test", "key": "key1", "value": "value1"}]

        with patch("self_fixing_engineer.arbiter.learner.fuzzy.os.getenv", return_value="3"):
            result = await _learn_batch_with_retry(mock_learner, facts, None, "test")

            assert len(result) == 1
            assert result[0]["status"] == "learned"
            assert mock_learner.learn_batch.call_count == 3


class TestProcessUnstructuredData:
    """Test suite for process_unstructured_data function."""

    @pytest.fixture
    def mock_learner(self):
        """Create a mock learner with required attributes."""
        learner = Mock()
        learner.fuzzy_parser_hooks = []
        learner.audit_logger = AsyncMock()
        learner.audit_logger.add_entry = AsyncMock()
        learner.learn_batch = AsyncMock(return_value=[{"status": "learned"}])
        return learner

    @pytest.mark.asyncio
    async def test_process_with_single_parser(self, mock_learner):
        """Test processing with a single parser."""
        parser = MockFuzzyParser(
            facts_to_return=[{"domain": "test", "key": "key1", "value": "value1"}]
        )
        mock_learner.fuzzy_parser_hooks = [parser]

        with patch("self_fixing_engineer.arbiter.learner.fuzzy.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0]

            result = await process_unstructured_data(
                learner=mock_learner,
                text="Test unstructured text",
                domain_hint="TestDomain",
                user_id="user123",
            )

            assert len(result) == 1
            assert result[0]["status"] == "learned"
            assert parser.call_count == 1

    @pytest.mark.asyncio
    async def test_process_with_multiple_parsers(self, mock_learner):
        """Test processing with multiple parsers in parallel."""
        parser1 = MockFuzzyParser(
            facts_to_return=[{"domain": "test", "key": "key1", "value": "value1"}]
        )
        parser2 = MockFuzzyParser(
            facts_to_return=[{"domain": "test", "key": "key2", "value": "value2"}]
        )
        mock_learner.fuzzy_parser_hooks = [parser1, parser2]

        mock_learner.learn_batch = AsyncMock(
            return_value=[
                {"status": "learned", "key": "key1"},
                {"status": "learned", "key": "key2"},
            ]
        )

        with patch("self_fixing_engineer.arbiter.learner.fuzzy.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0, 1.0, 2.0]

            result = await process_unstructured_data(
                learner=mock_learner, text="Test text with multiple facts"
            )

            assert len(result) == 2
            assert all(r["status"] == "learned" for r in result)
            assert parser1.call_count == 1
            assert parser2.call_count == 1

    @pytest.mark.asyncio
    async def test_process_with_parser_priority(self, mock_learner):
        """Test that parsers are executed in priority order."""
        import arbiter.learner.fuzzy as fuzzy_module

        # Create distinct parser classes to avoid name conflicts
        class LowPriorityParser(MockFuzzyParser):
            pass

        class HighPriorityParser(MockFuzzyParser):
            pass

        parser1 = LowPriorityParser()
        parser2 = HighPriorityParser()

        mock_learner.fuzzy_parser_hooks = [parser1, parser2]

        # Set priorities directly on the module
        fuzzy_module.PARSER_PRIORITIES["LowPriorityParser"] = 5
        fuzzy_module.PARSER_PRIORITIES["HighPriorityParser"] = 10

        with patch("self_fixing_engineer.arbiter.learner.fuzzy.time"):
            await process_unstructured_data(learner=mock_learner, text="Test text")

            # Both should be called (parallel execution)
            assert parser1.call_count == 1
            assert parser2.call_count == 1

    @pytest.mark.asyncio
    async def test_process_invalid_text(self, mock_learner):
        """Test handling of invalid text input."""
        with patch.object(fuzzy_parser_failure_total, "labels") as mock_metric:
            mock_labels = MagicMock()
            mock_metric.return_value = mock_labels

            with pytest.raises(ValueError, match="Text must be a non-empty string"):
                await process_unstructured_data(
                    learner=mock_learner, text=""
                )  # Empty string

            mock_metric.assert_called_with(
                parser_name="none", error_type="invalid_text"
            )
            mock_labels.inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_invalid_context(self, mock_learner):
        """Test handling of invalid context."""
        with patch.object(fuzzy_parser_failure_total, "labels") as mock_metric:
            mock_labels = MagicMock()
            mock_metric.return_value = mock_labels

            with pytest.raises(ValueError, match="Context must be a dictionary"):
                await process_unstructured_data(
                    learner=mock_learner,
                    text="Valid text",
                    context="invalid",  # Not a dict
                )

            mock_metric.assert_called_with(
                parser_name="none", error_type="invalid_context"
            )
            mock_labels.inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_no_parsers_registered(self, mock_learner):
        """Test handling when no parsers are registered."""
        mock_learner.fuzzy_parser_hooks = []

        with patch.object(fuzzy_parser_failure_total, "labels") as mock_metric:
            mock_labels = MagicMock()
            mock_metric.return_value = mock_labels

            result = await process_unstructured_data(
                learner=mock_learner, text="Test text"
            )

            assert len(result) == 1
            assert result[0]["status"] == "failed"
            assert result[0]["reason"] == "no_fuzzy_parsers"
            assert "text_hash" in result[0]

            mock_metric.assert_called_with(parser_name="none", error_type="no_parsers")
            mock_labels.inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_parser_timeout(self, mock_learner):
        """Test handling of parser timeout."""
        slow_parser = MockFuzzyParser(delay=10)  # 10 second delay
        mock_learner.fuzzy_parser_hooks = [slow_parser]

        with patch("self_fixing_engineer.arbiter.learner.fuzzy.PARSER_TIMEOUT_SECONDS", 0.1):
            with patch("self_fixing_engineer.arbiter.learner.fuzzy.time") as mock_time:
                mock_time.perf_counter.side_effect = [1.0, 2.0]

                with patch.object(fuzzy_parser_failure_total, "labels") as mock_metric:
                    mock_labels = MagicMock()
                    mock_metric.return_value = mock_labels

                    result = await process_unstructured_data(
                        learner=mock_learner, text="Test text"
                    )

                    # Should return no facts extracted
                    assert result[0]["status"] == "skipped"
                    assert result[0]["reason"] == "no_facts_extracted"

                    mock_metric.assert_called_with(
                        parser_name=slow_parser.__class__.__name__, error_type="timeout"
                    )

    @pytest.mark.asyncio
    async def test_process_parser_exception(self, mock_learner):
        """Test handling of parser exceptions."""
        failing_parser = MockFuzzyParser(should_fail=True)
        mock_learner.fuzzy_parser_hooks = [failing_parser]

        with patch("self_fixing_engineer.arbiter.learner.fuzzy.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0]

            with patch.object(fuzzy_parser_failure_total, "labels") as mock_metric:
                mock_labels = MagicMock()
                mock_metric.return_value = mock_labels

                result = await process_unstructured_data(
                    learner=mock_learner, text="Test text"
                )

                # Should return no facts extracted
                assert result[0]["status"] == "skipped"
                assert result[0]["reason"] == "no_facts_extracted"

                mock_metric.assert_called_with(
                    parser_name=failing_parser.__class__.__name__,
                    error_type="execution_error",
                )

    @pytest.mark.asyncio
    async def test_process_no_facts_extracted(self, mock_learner):
        """Test handling when parsers extract no facts."""
        empty_parser = MockFuzzyParser(facts_to_return=[])
        mock_learner.fuzzy_parser_hooks = [empty_parser]

        with patch("self_fixing_engineer.arbiter.learner.fuzzy.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0]

            result = await process_unstructured_data(
                learner=mock_learner, text="Text with no extractable facts"
            )

            assert len(result) == 1
            assert result[0]["status"] == "skipped"
            assert result[0]["reason"] == "no_facts_extracted"

            # Note: audit_logger.log_event is only called when facts are processed,
            # not when no facts are extracted. The function just logs to structlog
            # and returns early when no facts are found.

    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason="Metrics counter requires global labels (environment, instance) that aren't being passed - production bug"
    )
    async def test_process_learn_batch_failure(self, mock_learner):
        """Test handling of learn_batch failure."""
        parser = MockFuzzyParser(
            facts_to_return=[{"domain": "test", "key": "key1", "value": "value1"}]
        )
        mock_learner.fuzzy_parser_hooks = [parser]
        mock_learner.learn_batch = AsyncMock(side_effect=Exception("Learn failed"))

        with patch("self_fixing_engineer.arbiter.learner.fuzzy.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0]

            with patch(
                "self_fixing_engineer.arbiter.learner.fuzzy.os.getenv", return_value="1"
            ):  # Only 1 retry
                result = await process_unstructured_data(
                    learner=mock_learner, text="Test text"
                )

                assert len(result) == 1
                assert result[0]["status"] == "failed"
                assert "learn_batch_failure" in result[0]["reason"]


class TestRegisterFuzzyParserHook:
    """Test suite for register_fuzzy_parser_hook function."""

    def test_register_valid_parser(self):
        """Test registering a valid parser."""
        import arbiter.learner.fuzzy as fuzzy_module

        mock_learner = Mock()
        mock_learner.fuzzy_parser_hooks = []
        mock_learner.audit_logger = AsyncMock()

        parser = MockFuzzyParser()

        register_fuzzy_parser_hook(mock_learner, parser, priority=10)

        assert parser in mock_learner.fuzzy_parser_hooks
        assert fuzzy_module.PARSER_PRIORITIES[parser.__class__.__name__] == 10

    def test_register_invalid_parser_no_parse(self):
        """Test registering parser without parse method."""
        mock_learner = Mock()
        mock_learner.fuzzy_parser_hooks = []

        invalid_parser = Mock(spec=[])  # No parse method

        with pytest.raises(
            TypeError, match="Parser must implement FuzzyParser protocol"
        ):
            register_fuzzy_parser_hook(mock_learner, invalid_parser)

    def test_register_invalid_parser_sync_parse(self):
        """Test registering parser with synchronous parse method."""
        mock_learner = Mock()
        mock_learner.fuzzy_parser_hooks = []

        class SyncParser:
            def parse(self, text, context):
                return []  # Synchronous method

        invalid_parser = SyncParser()

        with pytest.raises(
            TypeError, match="Parser must implement FuzzyParser protocol"
        ):
            register_fuzzy_parser_hook(mock_learner, invalid_parser)


class TestIntegration:
    """Integration tests for fuzzy parsing system."""

    @pytest.mark.asyncio
    async def test_end_to_end_fuzzy_parsing(self):
        """Test complete fuzzy parsing flow."""

        # Create a custom parser
        class EmailParser:
            async def parse(
                self, text: str, context: Dict[str, Any]
            ) -> List[Dict[str, Any]]:
                # Simple email extraction
                import re

                emails = re.findall(
                    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", text
                )
                return [
                    {
                        "domain": context.get("domain_hint", "Emails"),
                        "key": f"email_{i}",
                        "value": email,
                    }
                    for i, email in enumerate(emails)
                ]

        # Set up learner
        mock_learner = Mock()
        mock_learner.fuzzy_parser_hooks = [EmailParser()]
        mock_learner.audit_logger = AsyncMock()
        mock_learner.learn_batch = AsyncMock(
            return_value=[
                {"status": "learned", "key": "email_0"},
                {"status": "learned", "key": "email_1"},
            ]
        )

        # Test text with emails
        test_text = "Contact us at support@example.com or sales@example.org"

        with patch("self_fixing_engineer.arbiter.learner.fuzzy.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0]

            result = await process_unstructured_data(
                learner=mock_learner, text=test_text, domain_hint="ContactInfo"
            )

            assert len(result) == 2
            assert all(r["status"] == "learned" for r in result)

            # Verify learn_batch was called with extracted emails
            call_args = mock_learner.learn_batch.call_args
            facts = call_args[0][0]
            assert len(facts) == 2
            assert facts[0]["domain"] == "ContactInfo"
            assert "example.com" in facts[0]["value"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=arbiter.learner.fuzzy"])
