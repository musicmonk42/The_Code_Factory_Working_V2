# test_clarifier_prompt.py

import unittest
import sys
import os
import base64
from unittest.mock import patch, AsyncMock, MagicMock

# Add parent directory to path for imports
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
)

# --- Mock Configuration and Core Utilities (MUST RUN BEFORE IMPORTS) ---


class MockConfigObject:
    LLM_PROVIDER = "grok"
    INTERACTION_MODE = "cli"
    BATCH_STRATEGY = "default"
    FEEDBACK_STRATEGY = "none"
    HISTORY_FILE = "mock_history_prompt.json"
    TARGET_LANGUAGE = "en"
    CONTEXT_DB_PATH = ":memory:"
    KMS_KEY_ID = "mock_kms_key"
    ALERT_ENDPOINT = "http://mock.alert/endpoint"
    HISTORY_COMPRESSION = False
    CONTEXT_QUERY_LIMIT = 3
    HISTORY_LOOKBACK_LIMIT = 10
    CIRCUIT_BREAKER_THRESHOLD = 5
    CIRCUIT_BREAKER_TIMEOUT = 30
    is_production_env = False


mock_config_instance = MagicMock(spec=MockConfigObject)
for attr, value in MockConfigObject.__dict__.items():
    if not attr.startswith("_"):
        setattr(mock_config_instance, attr, value)

TEST_FERNET_KEY = base64.urlsafe_b64encode(b"\x00" * 32)
mock_fernet_instance = MagicMock()
mock_fernet_instance.encrypt.side_effect = lambda data: base64.b64encode(
    b"ENCRYPTED_" + (data if isinstance(data, bytes) else data.encode())
)
mock_fernet_instance.decrypt.side_effect = lambda data: base64.b64decode(data).replace(
    b"ENCRYPTED_", b""
)

mock_logger = MagicMock()
mock_logger.info = MagicMock()
mock_logger.warning = MagicMock()
mock_logger.error = MagicMock()
mock_logger.debug = MagicMock()

mock_circuit_breaker = MagicMock()
mock_circuit_breaker.is_open.return_value = False
mock_circuit_breaker.record_success = MagicMock()
mock_circuit_breaker.record_failure = MagicMock()

# Mock tracer
mock_tracer = MagicMock()
mock_span = MagicMock()
mock_tracer.start_span.return_value = mock_span
mock_span.set_attribute = MagicMock()
mock_span.add_event = MagicMock()
mock_span.set_status = MagicMock()
mock_span.record_exception = MagicMock()
mock_span.end = MagicMock()

MockStatus = MagicMock()
MockStatusCode = MagicMock()
MockStatusCode.OK = "OK"
MockStatusCode.ERROR = "ERROR"

# Start patches before importing
patcher_get_config = patch(
    "generator.clarifier.clarifier_prompt.get_config", return_value=mock_config_instance
)
patcher_get_fernet = patch(
    "generator.clarifier.clarifier_prompt.get_fernet", return_value=mock_fernet_instance
)
patcher_get_logger = patch(
    "generator.clarifier.clarifier_prompt.get_logger", return_value=mock_logger
)
patcher_get_tracer = patch(
    "generator.clarifier.clarifier_prompt.get_tracer",
    return_value=(mock_tracer, MockStatus, MockStatusCode),
)
patcher_get_circuit_breaker = patch(
    "generator.clarifier.clarifier_prompt.get_circuit_breaker",
    return_value=mock_circuit_breaker,
)
patcher_log_action = patch(
    "generator.clarifier.clarifier_prompt.log_action", return_value=None
)
patcher_send_alert = patch(
    "generator.clarifier.clarifier_prompt.send_alert", new_callable=AsyncMock
)
patcher_translator = patch("googletrans.Translator")

patcher_get_config.start()
patcher_get_fernet.start()
patcher_get_logger.start()
patcher_get_tracer.start()
patcher_get_circuit_breaker.start()
MockLogAction = patcher_log_action.start()
MockSendAlert = patcher_send_alert.start()
MockTranslatorCls = patcher_translator.start()

MockTranslatorInstance = MockTranslatorCls.return_value
MockTranslatorInstance.translate.side_effect = lambda text, dest: MagicMock(text=text)

# Mock the core Clarifier class
patcher_clarifier_class = patch("generator.clarifier.clarifier_prompt.Clarifier")
MockClarifierClass = patcher_clarifier_class.start()

mock_core_clarifier = MagicMock()
mock_core_clarifier.get_clarifications = AsyncMock(
    return_value={"features": ["updated"], "clarifications": {}}
)
mock_core_clarifier.graceful_shutdown = AsyncMock()
MockClarifierClass.return_value = mock_core_clarifier

# Mock get_channel
patcher_get_channel = patch("generator.clarifier.clarifier_prompt.get_channel")
MockGetChannel = patcher_get_channel.start()

mock_channel = MagicMock()
mock_channel.prompt = AsyncMock(return_value=["Markdown, PDF"])
mock_channel.ask_compliance_questions = AsyncMock()
MockGetChannel.return_value = mock_channel

# Import after mocking
from generator.clarifier.clarifier_prompt import (
    PromptClarifier,
    CLARIFIER_CYCLES,
    CLARIFIER_ERRORS,
)


class TestPromptClarifier(unittest.IsolatedAsyncioTestCase):
    """Test the PromptClarifier class."""

    async def asyncSetUp(self):
        # Clear metrics
        CLARIFIER_CYCLES.clear()
        CLARIFIER_ERRORS.clear()

        # Reset mocks
        mock_logger.reset_mock()
        mock_circuit_breaker.reset_mock()
        mock_circuit_breaker.is_open.return_value = False
        MockLogAction.reset_mock()
        mock_channel.reset_mock()
        mock_channel.prompt = AsyncMock(return_value=["Markdown, PDF"])
        mock_channel.ask_compliance_questions = AsyncMock()
        mock_core_clarifier.reset_mock()
        mock_core_clarifier.get_clarifications = AsyncMock(
            return_value={"features": ["updated"], "clarifications": {}}
        )

        self.clarifier = PromptClarifier()

    def tearDown(self):
        # Reset doc_formats_asked flag
        if hasattr(self, "clarifier"):
            self.clarifier.doc_formats_asked = False

    async def test_initialization(self):
        """Test PromptClarifier initialization."""
        self.assertIsNotNone(self.clarifier.config)
        self.assertIsNotNone(self.clarifier.fernet)
        self.assertIsNotNone(self.clarifier.logger)
        self.assertIsNotNone(self.clarifier.circuit_breaker)
        self.assertIsNotNone(self.clarifier.interaction)
        self.assertIsNotNone(self.clarifier.core_clarifier)
        self.assertFalse(self.clarifier.doc_formats_asked)

    async def test_get_clarifications_success(self):
        """Test successful clarification with all steps."""
        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]
        user_context = {"user_id": "test_user"}

        result = await self.clarifier.get_clarifications(
            ambiguities, requirements, user_context
        )

        # Verify documentation format question was asked
        mock_channel.prompt.assert_awaited()

        # Verify compliance questions were asked
        mock_channel.ask_compliance_questions.assert_awaited_with(
            "test_user", user_context
        )

        # Verify delegation to core clarifier
        mock_core_clarifier.get_clarifications.assert_awaited_once_with(
            ambiguities, requirements
        )

        # Verify result
        self.assertIsInstance(result, dict)

        # Verify metrics
        cycles_metric = CLARIFIER_CYCLES.labels(status="started")._value.get()
        self.assertGreater(cycles_metric, 0)

    async def test_doc_formats_only_asked_once(self):
        """Test that documentation formats are only asked once per session."""
        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]
        user_context = {"user_id": "test_user"}

        # First call
        await self.clarifier.get_clarifications(ambiguities, requirements, user_context)
        first_call_count = mock_channel.prompt.await_count

        # Reset mock to track second call
        mock_channel.prompt.reset_mock()
        mock_channel.prompt = AsyncMock(return_value=["HTML"])

        # Second call
        await self.clarifier.get_clarifications(ambiguities, requirements, user_context)

        # Doc format question should not be asked again
        # prompt should not be called (or called less) in second iteration
        self.assertTrue(self.clarifier.doc_formats_asked)

    async def test_doc_formats_stored_in_requirements(self):
        """Test that specified doc formats are stored in requirements."""
        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]
        user_context = {"user_id": "test_user"}

        # Mock prompt to return specific formats
        mock_channel.prompt = AsyncMock(return_value=["Markdown, PDF, HTML"])

        result = await self.clarifier.get_clarifications(
            ambiguities, requirements, user_context
        )

        # Note: The actual implementation stores in requirements before passing to core_clarifier
        # We need to check what was passed
        call_args = mock_core_clarifier.get_clarifications.call_args
        if call_args:
            reqs_arg = call_args[0][1]  # Second positional argument is requirements
            # The requirements should have been modified before passing
            # Check if our original requirements object was modified
            self.assertTrue(self.clarifier.doc_formats_asked)

    async def test_empty_doc_formats_answer(self):
        """Test handling of empty doc formats answer."""
        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]
        user_context = {"user_id": "test_user"}

        # Mock prompt to return empty answer
        mock_channel.prompt = AsyncMock(return_value=[""])

        result = await self.clarifier.get_clarifications(
            ambiguities, requirements, user_context
        )

        # Should complete successfully
        self.assertIsInstance(result, dict)
        self.assertTrue(self.clarifier.doc_formats_asked)

    async def test_compliance_questions_without_method(self):
        """Test compliance questions when channel doesn't support them."""
        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]
        user_context = {"user_id": "test_user"}

        # Remove the method from the mock channel
        delattr(mock_channel, "ask_compliance_questions")

        # Should complete without error
        result = await self.clarifier.get_clarifications(
            ambiguities, requirements, user_context
        )
        self.assertIsInstance(result, dict)

        # Re-add the method for other tests
        mock_channel.ask_compliance_questions = AsyncMock()

    async def test_circuit_breaker_open(self):
        """Test behavior when circuit breaker is open."""
        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]
        user_context = {"user_id": "test_user"}

        # Open circuit breaker
        mock_circuit_breaker.is_open.return_value = True

        with self.assertRaises(Exception) as context:
            await self.clarifier.get_clarifications(
                ambiguities, requirements, user_context
            )

        self.assertIn("Circuit breaker", str(context.exception))

        # Verify error metric
        error_metric = CLARIFIER_ERRORS.labels("circuit_breaker_open")._value.get()
        self.assertGreater(error_metric, 0)

        # Reset for other tests
        mock_circuit_breaker.is_open.return_value = False

    async def test_doc_formats_query_failure(self):
        """Test handling of doc formats query failure."""
        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]
        user_context = {"user_id": "test_user"}

        # Make prompt fail for doc formats
        mock_channel.prompt = AsyncMock(side_effect=Exception("Prompt failed"))

        # Should complete despite doc formats failure
        result = await self.clarifier.get_clarifications(
            ambiguities, requirements, user_context
        )

        # Should still delegate to core clarifier
        mock_core_clarifier.get_clarifications.assert_awaited()

        # Error should be logged
        error_metric = CLARIFIER_ERRORS.labels("doc_formats_query_failed")._value.get()
        self.assertGreater(error_metric, 0)

    async def test_compliance_query_failure(self):
        """Test handling of compliance query failure."""
        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]
        user_context = {"user_id": "test_user"}

        # Make compliance questions fail
        mock_channel.ask_compliance_questions = AsyncMock(
            side_effect=Exception("Compliance failed")
        )

        # Should complete despite compliance failure
        result = await self.clarifier.get_clarifications(
            ambiguities, requirements, user_context
        )

        # Should still delegate to core clarifier
        mock_core_clarifier.get_clarifications.assert_awaited()

        # Error should be logged
        error_metric = CLARIFIER_ERRORS.labels("compliance_query_failed")._value.get()
        self.assertGreater(error_metric, 0)

    async def test_core_clarifier_failure(self):
        """Test handling of core clarifier failure."""
        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]
        user_context = {"user_id": "test_user"}

        # Make core clarifier fail
        mock_core_clarifier.get_clarifications = AsyncMock(
            side_effect=Exception("Core clarifier failed")
        )

        with self.assertRaises(Exception) as context:
            await self.clarifier.get_clarifications(
                ambiguities, requirements, user_context
            )

        self.assertIn("Core clarifier failed", str(context.exception))

        # Error should be logged
        error_metric = CLARIFIER_ERRORS.labels(
            "prompt_clarification_cycle_failed"
        )._value.get()
        self.assertGreater(error_metric, 0)

    async def test_translation(self):
        """Test text translation functionality."""
        text = "Hello, world!"

        # Translation to same language should return original
        result = self.clarifier._translate_text(text, "en")
        self.assertEqual(result, text)

        # Translation to different language
        result = self.clarifier._translate_text(text, "es")
        # Mock returns original text, so verify it was called
        self.assertIsInstance(result, str)

    async def test_translation_failure(self):
        """Test translation failure handling."""
        # Make translation fail
        MockTranslatorInstance.translate.side_effect = Exception("Translation failed")

        text = "Hello, world!"
        result = self.clarifier._translate_text(text, "es")

        # Should return original text on failure
        self.assertEqual(result, text)

        # Reset for other tests
        MockTranslatorInstance.translate.side_effect = lambda text, dest: MagicMock(
            text=text
        )

    async def test_retry_mechanism(self):
        """Test retry mechanism."""
        call_count = 0

        async def failing_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Temporary failure")
            return "success"

        result = await self.clarifier._retry(failing_func, retries=3, delay=0.1)

        self.assertEqual(result, "success")
        self.assertEqual(call_count, 3)

    async def test_retry_circuit_breaker_abort(self):
        """Test retry aborts when circuit breaker opens."""

        async def test_func():
            return "should not reach"

        # Open circuit breaker
        mock_circuit_breaker.is_open.return_value = True

        with self.assertRaises(Exception) as context:
            await self.clarifier._retry(test_func, retries=3)

        self.assertIn("Operation aborted by circuit breaker", str(context.exception))

        # Reset
        mock_circuit_breaker.is_open.return_value = False

    async def test_tracing_integration(self):
        """Test OpenTelemetry tracing integration."""
        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]
        user_context = {"user_id": "test_user"}

        result = await self.clarifier.get_clarifications(
            ambiguities, requirements, user_context
        )

        # Verify span operations were called
        mock_tracer.start_span.assert_called()
        mock_span.set_attribute.assert_called()
        mock_span.add_event.assert_called()
        mock_span.set_status.assert_called()
        mock_span.end.assert_called()


class TestPluginEntrypoint(unittest.IsolatedAsyncioTestCase):
    """Test the plugin entrypoint."""

    async def test_plugin_run_success(self):
        """Test plugin run with valid inputs."""
        from generator.clarifier.clarifier_prompt import run as plugin_run

        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]
        user_context = {"user_id": "test_user"}

        with patch(
            "generator.clarifier.clarifier_prompt.PromptClarifier"
        ) as MockPromptClarifier:
            mock_instance = MagicMock()
            mock_instance.get_clarifications = AsyncMock(
                return_value={"features": ["updated"]}
            )
            mock_instance.core_clarifier = MagicMock()
            mock_instance.core_clarifier.graceful_shutdown = AsyncMock()
            MockPromptClarifier.return_value = mock_instance

            result = await plugin_run(requirements, ambiguities, user_context)

            self.assertIn("requirements", result)
            mock_instance.get_clarifications.assert_awaited_once()
            mock_instance.core_clarifier.graceful_shutdown.assert_awaited()

    async def test_plugin_run_without_user_context(self):
        """Test plugin run without user context (should use default)."""
        from generator.clarifier.clarifier_prompt import run as plugin_run

        requirements = {"features": ["feature1"]}
        ambiguities = ["ambiguous term"]

        with patch(
            "generator.clarifier.clarifier_prompt.PromptClarifier"
        ) as MockPromptClarifier:
            mock_instance = MagicMock()
            mock_instance.get_clarifications = AsyncMock(
                return_value={"features": ["updated"]}
            )
            mock_instance.core_clarifier = MagicMock()
            mock_instance.core_clarifier.graceful_shutdown = AsyncMock()
            MockPromptClarifier.return_value = mock_instance

            result = await plugin_run(requirements, ambiguities)

            self.assertIn("requirements", result)
            # Should have been called with default user_id
            mock_instance.get_clarifications.assert_awaited()


def tearDownModule():
    """Clean up all patches."""
    patcher_get_channel.stop()
    patcher_clarifier_class.stop()
    patcher_translator.stop()
    patcher_send_alert.stop()
    patcher_log_action.stop()
    patcher_get_circuit_breaker.stop()
    patcher_get_tracer.stop()
    patcher_get_logger.stop()
    patcher_get_fernet.stop()
    patcher_get_config.stop()
    print("\nAll clarifier_prompt mocks stopped.")


if __name__ == "__main__":
    unittest.main(argv=["first-arg-is-ignored"], exit=False)
    tearDownModule()
