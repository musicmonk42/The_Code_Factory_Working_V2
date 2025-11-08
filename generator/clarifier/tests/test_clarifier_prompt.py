
import asyncio
import json
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime
from clarifier_prompt import PromptClarifier, log_action, send_alert
from clarifier import (
    get_config, get_fernet, get_logger, get_tracer, get_circuit_breaker,
    CLARIFIER_CYCLES, CLARIFIER_LATENCY, CLARIFIER_ERRORS, CLARIFIER_QUESTION_PROMPT_LATENCY
)
from clarifier_user_prompt import UserPromptChannel

# Mock dependencies
patch_config = patch('clarifier_prompt.get_config', return_value=MagicMock(
    INTERACTION_MODE='cli',
    TARGET_LANGUAGE='en'
))
mock_config = patch_config.start()

patch_fernet = patch('clarifier_prompt.get_fernet', return_value=MagicMock(encrypt=lambda x: b'encrypted_' + x, decrypt=lambda x: x[len(b'encrypted_'):]))
mock_fernet = patch_fernet.start()

patch_logger = patch('clarifier_prompt.get_logger', return_value=MagicMock())
mock_logger = patch_logger.start()

patch_tracer = patch('clarifier_prompt.get_tracer', return_value=(MagicMock(), MagicMock(), MagicMock()))
mock_tracer = patch_tracer.start()

patch_circuit_breaker = patch('clarifier_prompt.get_circuit_breaker', return_value=MagicMock(is_open=MagicMock(return_value=False), record_success=MagicMock(), record_failure=MagicMock()))
mock_circuit_breaker = patch_circuit_breaker.start()

patch_log_action = patch('clarifier_prompt.log_action', new=AsyncMock())
mock_log_action = patch_log_action.start()

patch_send_alert = patch('clarifier_prompt.send_alert', new=AsyncMock())
mock_send_alert = patch_send_alert.start()

patch_get_channel = patch('clarifier_prompt.get_channel', return_value=AsyncMock(spec=UserPromptChannel))
mock_get_channel = patch_get_channel.start()

class TestPromptClarifierRegulated(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Initialize metrics
        CLARIFIER_CYCLES.clear()
        CLARIFIER_LATENCY.clear()
        CLARIFIER_ERRORS.clear()
        CLARIFIER_QUESTION_PROMPT_LATENCY.clear()

        # Reset mocks
        mock_log_action.reset_mock()
        mock_send_alert.reset_mock()
        mock_logger.reset_mock()
        mock_tracer.reset_mock()
        mock_circuit_breaker.reset_mock()

        # Mock interaction channel
        self.mock_channel = AsyncMock(spec=UserPromptChannel)
        self.mock_channel.prompt = AsyncMock(return_value=["Markdown, PDF"])
        self.mock_channel.ask_compliance_questions = AsyncMock()
        mock_get_channel.return_value = self.mock_channel

        # Mock core Clarifier
        self.mock_core_clarifier = AsyncMock()
        self.mock_core_clarifier.get_clarifications = AsyncMock(return_value={"features": ["clarified"]})
        self.mock_core_clarifier.graceful_shutdown = AsyncMock()

        # Initialize PromptClarifier with mocked dependencies
        with patch('clarifier_prompt.Clarifier', return_value=self.mock_core_clarifier):
            self.clarifier = PromptClarifier()

        self.requirements = {"features": ["test"]}
        self.ambiguities = ["ambiguous term"]
        self.user_context = {"user_id": "test_user", "user_email": "test@example.com"}

    async def asyncTearDown(self):
        # Cleanup mocks
        patch_config.stop()
        patch_fernet.stop()
        patch_logger.stop()
        patch_tracer.stop()
        patch_circuit_breaker.stop()
        patch_log_action.stop()
        patch_send_alert.stop()
        patch_get_channel.stop()

    async def test_doc_formats_prompting(self):
        """Test prompting for documentation formats and storing in requirements."""
        result = await self.clarifier.get_clarifications(self.ambiguities, self.requirements.copy(), self.user_context)

        self.mock_channel.prompt.assert_awaited_once()
        self.assertIn("desired_doc_formats", result)
        self.assertEqual(result["desired_doc_formats"], ["Markdown", "PDF"])
        self.assertTrue(self.clarifier.doc_formats_asked)
        mock_log_action.assert_any_call("clarification_doc_formats_asked", {
            "question": "What documentation formats do you prefer (e.g., Markdown, PDF, HTML)?",
            "answer": "Markdown, PDF"
        })
        self.assertEqual(CLARIFIER_CYCLES.labels(status="started")._value, 1)
        self.assertGreater(CLARIFIER_LATENCY._value, 0)

    async def test_compliance_questions(self):
        """Test compliance questions are asked when supported."""
        await self.clarifier.get_clarifications(self.ambiguities, self.requirements, self.user_context)

        self.mock_channel.ask_compliance_questions.assert_awaited_with("test_user", self.user_context)
        mock_tracer.return_value[0].start_span.assert_called_with("prompt_clarification_cycle")
        mock_tracer.return_value[0].start_span.return_value.add_event.assert_any_call("Compliance questions asked")

    async def test_delegation_to_core_clarifier(self):
        """Test delegation to core Clarifier."""
        result = await self.clarifier.get_clarifications(self.ambiguities, self.requirements, self.user_context)

        self.mock_core_clarifier.get_clarifications.assert_awaited_with(self.ambiguities, {
            "features": ["test"], "desired_doc_formats": ["Markdown", "PDF"]
        })
        self.assertEqual(result, {"features": ["clarified"]})

    async def test_sensitive_data_redaction(self):
        """Test redaction of sensitive data in user context and logs."""
        sensitive_context = {"user_id": "test_user", "user_email": "sensitive@example.com", "ssn": "123-45-6789"}
        with patch('clarifier_prompt.redact_sensitive', side_effect=lambda x: x.replace("123-45-6789", "[REDACTED]")):
            await self.clarifier.get_clarifications(self.ambiguities, self.requirements, sensitive_context)

        log_calls = mock_log_action.call_args_list
        for call in log_calls:
            args, _ = call
            log_data = args[1]
            self.assertNotIn("123-45-6789", json.dumps(log_data))
            self.assertIn("[REDACTED]", json.dumps(log_data))

    async def test_encryption_of_sensitive_data(self):
        """Test encryption of sensitive user context using fernet."""
        sensitive_context = {"user_id": "test_user", "user_email": "sensitive@example.com"}
        self.clarifier.fernet.encrypt = MagicMock(return_value=b"encrypted_context")
        self.clarifier.fernet.decrypt = MagicMock(return_value=b"sensitive@example.com")

        await self.clarifier.get_clarifications(self.ambiguities, self.requirements, sensitive_context)

        self.clarifier.fernet.encrypt.assert_called_with(b"sensitive@example.com")
        mock_log_action.assert_any_call("clarification_doc_formats_asked", Any)

    async def test_translation_success(self):
        """Test translation of prompts to target language."""
        with patch('clarifier_prompt.get_config', return_value=MagicMock(TARGET_LANGUAGE='es')):
            with patch('googletrans.Translator') as mock_translator:
                mock_translator_instance = MagicMock()
                mock_translator_instance.translate.return_value.text = "Formato de documentación preferido?"
                mock_translator.return_value = mock_translator_instance
                clarifier = PromptClarifier()

                result = await clarifier.get_clarifications(self.ambiguities, self.requirements, self.user_context)

                mock_translator_instance.translate.assert_called_with(
                    "What documentation formats do you prefer (e.g., Markdown, PDF, HTML)?", dest='es'
                )
                self.mock_channel.prompt.assert_awaited_with(["Formato de documentación preferido?"], self.user_context, 'es')
                self.assertIn("desired_doc_formats", result)

    async def test_translation_failure(self):
        """Test fallback when translation fails."""
        with patch('clarifier_prompt.get_config', return_value=MagicMock(TARGET_LANGUAGE='fr')):
            with patch('googletrans.Translator', side_effect=Exception("Translation API down")):
                clarifier = PromptClarifier()

                result = await clarifier.get_clarifications(self.ambiguities, self.requirements, self.user_context)

                self.mock_channel.prompt.assert_awaited_with(
                    ["What documentation formats do you prefer (e.g., Markdown, PDF, HTML)?"], self.user_context, 'fr'
                )
                self.assertEqual(CLARIFIER_ERRORS.labels(channel='PromptClarifier', type='translation_failed')._value, 1)
                mock_log_action.assert_any_call("clarification_doc_formats_asked", Any)

    async def test_circuit_breaker_open(self):
        """Test behavior when circuit breaker is open."""
        mock_circuit_breaker.return_value.is_open.return_value = True

        with self.assertRaises(Exception) as cm:
            await self.clarifier.get_clarifications(self.ambiguities, self.requirements, self.user_context)

        self.assertIn("Circuit breaker is open", str(cm.exception))
        self.assertEqual(CLARIFIER_ERRORS.labels('circuit_breaker_open')._value, 1)
        mock_log_action.assert_any_call("prompt_clarification_cycle_error", {"error": Any, "status": "failed"})

    async def test_retry_logic(self):
        """Test retry logic on transient failures."""
        self.mock_channel.prompt = AsyncMock(side_effect=[Exception("Transient error"), ["Markdown"]])
        result = await self.clarifier.get_clarifications(self.ambiguities, self.requirements, self.user_context)

        self.assertEqual(self.mock_channel.prompt.call_count, 2)
        self.assertIn("desired_doc_formats", result)
        self.assertEqual(result["desired_doc_formats"], ["Markdown"])
        mock_circuit_breaker.return_value.record_failure.assert_called_once()
        mock_circuit_breaker.return_value.record_success.assert_called_once()

    async def test_empty_ambiguities(self):
        """Test handling of empty ambiguities list."""
        result = await self.clarifier.get_clarifications([], self.requirements, self.user_context)

        self.mock_core_clarifier.get_clarifications.assert_awaited_with([], {
            "features": ["test"], "desired_doc_formats": ["Markdown", "PDF"]
        })
        self.assertIn("desired_doc_formats", result)
        self.assertEqual(CLARIFIER_CYCLES.labels(status="started")._value, 1)

    async def test_compliance_questions_unsupported(self):
        """Test handling when interaction channel does not support compliance questions."""
        self.mock_channel.ask_compliance_questions = None
        await self.clarifier.get_clarifications(self.ambiguities, self.requirements, self.user_context)

        mock_logger.return_value.warning.assert_called_with(
            f"Interaction channel {self.mock_channel.__class__.__name__} does not support ask_compliance_questions."
        )

    async def test_concurrent_calls(self):
        """Test concurrent clarification calls."""
        tasks = [
            self.clarifier.get_clarifications(self.ambiguities, self.requirements, self.user_context)
            for _ in range(3)