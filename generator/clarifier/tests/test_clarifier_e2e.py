
import asyncio
import json
import os
import sqlite3
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from clarifier import Clarifier, get_config, get_fernet, get_logger, get_tracer, get_circuit_breaker
from clarifier_llm_call import call_llm_with_fallback, CircuitBreaker
from clarifier_prompt import PromptClarifier
from clarifier_updater import RequirementsUpdater, HistoryStore
from clarifier_user_prompt import UserPromptChannel, CLIPrompt, load_profile, save_profile, store_compliance_answer

# Mock dependencies
patch_config = patch('clarifier.get_config', return_value=MagicMock(
    INTERACTION_MODE='cli',
    TARGET_LANGUAGE='en',
    KMS_KEY='mock_key',
    ALERT_ENDPOINT='http://mock-alert:8080',
    SCHEMA_VERSION=2,
    CONFLICT_STRATEGY='auto_merge',
    CLARIFIER_EMAIL_SERVER='smtp.mock.com',
    CLARIFIER_EMAIL_PORT=587,
    CLARIFIER_EMAIL_USER='user@mock.com',
    CLARIFIER_EMAIL_PASS='mockpass',
    CLARIFIER_SLACK_WEBHOOK='https://slack.mock.com',
    CLARIFIER_SMS_API='https://sms.mock.com',
    CLARIFIER_SMS_KEY='mock_sms_key'
))
mock_config = patch_config.start()

patch_fernet = patch('clarifier.get_fernet', return_value=MagicMock(
    encrypt=lambda x: b'encrypted_' + x,
    decrypt=lambda x: x[len(b'encrypted_'):],
))
mock_fernet = patch_fernet.start()

patch_logger = patch('clarifier.get_logger', return_value=MagicMock())
mock_logger = patch_logger.start()

patch_tracer = patch('clarifier.get_tracer', return_value=(MagicMock(), MagicMock(), MagicMock()))
mock_tracer = patch_tracer.start()

patch_circuit_breaker = patch('clarifier.get_circuit_breaker', return_value=MagicMock(is_open=MagicMock(return_value=False), record_success=MagicMock(), record_failure=MagicMock()))
mock_circuit_breaker = patch_circuit_breaker.start()

patch_log_action = patch('clarifier.log_action', AsyncMock())
mock_log_action = patch_log_action.start()

patch_send_alert = patch('clarifier.send_alert', AsyncMock())
mock_send_alert = patch_send_alert.start()

patch_get_channel = patch('clarifier_user_prompt.get_channel', return_value=AsyncMock(spec=UserPromptChannel))
mock_get_channel = patch_get_channel.start()

patch_redact_sensitive = patch('clarifier_user_prompt.redact_sensitive', side_effect=lambda x: x.replace('secret', '[REDACTED_SECRET]').replace('user@example.com', '[REDACTED_EMAIL]').replace('123-45-6789', '[REDACTED_SSN]'))
mock_redact_sensitive = patch_redact_sensitive.start()

patch_translator = patch('clarifier_user_prompt.Translator', return_value=MagicMock())
mock_translator = patch_translator.start()

class TestClarifierE2ERegulated(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Reset metrics
        from clarifier import CLARIFIER_CYCLES, CLARIFIER_LATENCY, CLARIFIER_ERRORS, CLARIFIER_QUESTION_PROMPT_LATENCY
        from clarifier_llm_call import LLM_LATENCY, LLM_ERRORS, LLM_TOKEN_USAGE_PROMPT, LLM_TOKEN_USAGE_COMPLETION
        from clarifier_updater import UPDATE_CYCLES, UPDATE_ERRORS, UPDATE_CONFLICTS, REDACTION_EVENTS, SCHEMA_MIGRATIONS, HISTORY_STORAGE_LATENCY
        from clarifier_user_prompt import PROMPT_CYCLES, PROMPT_LATENCY, PROMPT_ERRORS, COMPLIANCE_QUESTIONS_ASKED, COMPLIANCE_ANSWERS_RECEIVED
        CLARIFIER_CYCLES.clear()
        CLARIFIER_LATENCY.clear()
        CLARIFIER_ERRORS.clear()
        CLARIFIER_QUESTION_PROMPT_LATENCY.clear()
        LLM_LATENCY.clear()
        LLM_ERRORS.clear()
        LLM_TOKEN_USAGE_PROMPT.clear()
        LLM_TOKEN_USAGE_COMPLETION.clear()
        UPDATE_CYCLES.clear()
        UPDATE_ERRORS.clear()
        UPDATE_CONFLICTS.clear()
        REDACTION_EVENTS.clear()
        SCHEMA_MIGRATIONS.clear()
        HISTORY_STORAGE_LATENCY.clear()
        PROMPT_CYCLES.clear()
        PROMPT_LATENCY.clear()
        PROMPT_ERRORS.clear()
        COMPLIANCE_QUESTIONS_ASKED.clear()
        COMPLIANCE_ANSWERS_RECEIVED.clear()

        # Reset mocks
        mock_log_action.reset_mock()
        mock_send_alert.reset_mock()
        mock_logger.reset_mock()
        mock_tracer.reset_mock()
        mock_circuit_breaker.reset_mock()
        mock_redact_sensitive.reset_mock()
        mock_translator.reset_mock()

        # Setup test data
        self.requirements = {
            "features": ["test_feature", "contradictory_feature"],
            "schema_version": 1
        }
        self.ambiguities = ["ambiguous term", "secret API key", "SSN: 123-45-6789"]
        self.user_context = {"user_id": "test_user", "user_email": "user@example.com"}
        self.correlation_id = "test-correlation-id"

        # Mock user interaction channel
        self.mock_channel = AsyncMock(spec=UserPromptChannel)
        self.mock_channel.prompt = AsyncMock(side_effect=[
            ["Markdown, PDF"],  # Documentation formats
            ["Clarified term", "[REDACTED_SECRET] key", "[REDACTED_SSN]"]  # Clarifications
        ])
        self.mock_channel.ask_compliance_questions = AsyncMock(return_value={
            "gdpr_apply": True,
            "phi_data": False,
            "pci_dss": True,
            "data_residency": "EU",
            "child_privacy": False
        })
        mock_get_channel.return_value = self.mock_channel

        # Mock LLM response
        self.mock_llm_response = {
            "prioritized": [
                {"original": "ambiguous term", "score": 8, "question": "What does this term mean?"},
                {"original": "[REDACTED_SECRET] key", "score": 10, "question": "Specify the API key usage."},
                {"original": "[REDACTED_SSN]", "score": 9, "question": "Clarify SSN requirement."}
            ],
            "batch": [0, 1, 2]
        }

        # Initialize components
        with patch('clarifier_llm_call.call_llm_with_fallback', AsyncMock(return_value=self.mock_llm_response)):
            self.clarifier = Clarifier()
            self.prompt_clarifier = PromptClarifier()
            self.prompt_clarifier.interaction = self.mock_channel
            self.updater = RequirementsUpdater()
            self.updater.history_store = HistoryStore(':memory:', mock_fernet.return_value)
            await self.updater.history_store._init_db()

        # Create user profile directory
        os.makedirs('user_profiles', exist_ok=True)

    async def asyncTearDown(self):
        await self.updater.history_store.close()
        import shutil
        if os.path.exists('user_profiles'):
            shutil.rmtree('user_profiles')
        patch_config.stop()
        patch_fernet.stop()
        patch_logger.stop()
        patch_tracer.stop()
        patch_circuit_breaker.stop()
        patch_log_action.stop()
        patch_send_alert.stop()
        patch_get_channel.stop()
        patch_redact_sensitive.stop()
        patch_translator.stop()

    async def test_e2e_happy_path(self):
        """Test the full E2E pipeline with valid inputs."""
        result = await self.clarifier.get_clarifications(self.ambiguities, self.requirements, self.user_context)

        # Verify requirements update
        self.assertIn("desired_doc_formats", result)
        self.assertEqual(result["desired_doc_formats"], ["Markdown", "PDF"])
        self.assertIn("clarifications", result)
        self.assertEqual(result["clarifications"]["ambiguous term"], "Clarified term")
        self.assertEqual(result["clarifications"]["secret API key"], "[REDACTED_SECRET] key")
        self.assertEqual(result["clarifications"]["SSN: 123-45-6789"], "[REDACTED_SSN]")
        self.assertEqual(result["schema_version"], 2)
        self.assertNotIn("contradictory_feature", result["features"])  # Conflict resolved

        # Verify compliance questions
        profile = load_profile("test_user")
        self.assertTrue(profile.compliance_preferences["gdpr_apply"])
        self.assertEqual(profile.compliance_preferences["data_residency"], "EU")

        # Verify history storage
        history = await self.updater.history_store.query(limit=1)
        self.assertEqual(len(history), 1)
        self.assertIn("[REDACTED_SSN]", json.dumps(history[0]))
        self.assertTrue(self.updater._verify_hash_chain(history[0]))

        # Verify metrics
        self.assertEqual(CLARIFIER_CYCLES.labels(status="started")._value, 1)
        self.assertEqual(PROMPT_CYCLES.labels(channel="CLIPrompt")._value, 2)  # Doc formats + clarifications
        self.assertEqual(UPDATE_CYCLES._value, 1)
        self.assertEqual(REDACTION_EVENTS.labels(pattern_type="ssn")._value, 1)
        self.assertEqual(COMPLIANCE_ANSWERS_RECEIVED.labels(question_id="gdpr_apply", answer_value="True")._value, 1)

        # Verify audit logging
        mock_log_action.assert_any_call("Prompt Interaction", Any)
        mock_log_action.assert_any_call("Compliance Question Answered", Any)
        mock_log_action.assert_any_call("requirements_updated", category="update_workflow", version=Any, conflicts_detected=1, final_status="success")

    async def test_e2e_pii_redaction(self):
        """Test PII redaction across the pipeline."""
        ambiguities = ["SSN: 123-45-6789", "user@example.com"]
        with patch('clarifier_llm_call.call_llm_with_fallback', AsyncMock(return_value={
            "prioritized": [
                {"original": "[REDACTED_SSN]", "score": 10, "question": "Clarify SSN?"},
                {"original": "[REDACTED_EMAIL]", "score": 8, "question": "Clarify email?"}
            ],
            "batch": [0, 1]
        })):
            self.mock_channel.prompt.side_effect = [["PDF"], ["[REDACTED_SSN]", "[REDACTED_EMAIL]"]]
            result = await self.clarifier.get_clarifications(ambiguities, self.requirements, self.user_context)

        self.assertIn("[REDACTED_SSN]", result["clarifications"])
        self.assertIn("[REDACTED_EMAIL]", result["clarifications"])
        self.assertNotIn("123-45-6789", json.dumps(result))
        self.assertNotIn("user@example.com", json.dumps(result))

        history = await self.updater.history_store.query(limit=1)
        self.assertIn("[REDACTED_SSN]", json.dumps(history))
        self.assertNotIn("123-45-6789", json.dumps(history))
        self.assertEqual(REDACTION_EVENTS.labels(pattern_type="ssn")._value, 1)
        self.assertEqual(REDACTION_EVENTS.labels(pattern_type="email")._value, 1)

        log_calls = mock_log_action.call_args_list
        for call in log_calls:
            self.assertNotIn("123-45-6789", json.dumps(call))
            self.assertNotIn("user@example.com", json.dumps(call))

    async def test_e2e_circuit_breaker(self):
        """Test circuit breaker behavior when LLM fails."""
        mock_cb = CircuitBreaker(threshold=2, timeout=10)
        with patch('clarifier.get_circuit_breaker', return_value=mock_cb):
            with patch('clarifier_llm_call.call_llm_with_fallback', side_effect=aiohttp.ClientError("API failure")):
                for _ in range(2):
                    with self.assertRaises(aiohttp.ClientError):
                        await self.clarifier.get_clarifications(self.ambiguities, self.requirements)
                self.assertTrue(mock_cb.is_open())
                with self.assertRaises(Exception) as cm:
                    await self.clarifier.get_clarifications(self.ambiguities, self.requirements)
                self.assertIn("circuit breaker", str(cm.exception).lower())
                self.assertEqual(CLARIFIER_ERRORS.labels(channel="Clarifier", type="circuit_breaker_open")._value, 1)
                mock_send_alert.assert_awaited_with(Any, severity="high")

    async def test_e2e_translation_failure(self):
        """Test translation failure handling."""
        with patch('clarifier_prompt.get_config', return_value=MagicMock(INTERACTION_MODE='cli', TARGET_LANGUAGE='fr')):
            with patch('googletrans.Translator', side_effect=Exception("Translation API down")):
                result = await self.clarifier.get_clarifications(self.ambiguities, self.requirements, self.user_context)

        self.assertIn("desired_doc_formats", result)
        self.assertEqual(result["desired_doc_formats"], ["Markdown", "PDF"])
        self.assertEqual(CLARIFIER_ERRORS.labels(channel="PromptClarifier", type="translation_failed")._value, 1)
        mock_log_action.assert_any_call("Prompt Interaction", Any)

    async def test_e2e_concurrent_requests(self):
        """Test concurrent clarification requests."""
        tasks = [self.clarifier.get_clarifications(self.ambiguities, self.requirements, self.user_context) for _ in range(3)]
        results = await asyncio.gather(*tasks)

        self.assertEqual(len(results), 3)
        for result in results:
            self.assertIn("desired_doc_formats", result)
            self.assertIn("clarifications", result)
        history = await self.updater.history_store.query(limit=3)
        self.assertEqual(len(history), 3)
        self.assertEqual(CLARIFIER_CYCLES.labels(status="started")._value, 3)
        self.assertEqual(PROMPT_CYCLES.labels(channel="CLIPrompt")._value, 6)  # 3 doc formats + 3 clarifications
        self.assertEqual(UPDATE_CYCLES._value, 3)

    async def test_e2e_empty_ambiguities(self):
        """Test handling of empty ambiguities."""
        result = await self.clarifier.get_clarifications([], self.requirements, self.user_context)

        self.assertIn("desired_doc_formats", result)
        self.assertEqual(result["clarifications"], {})
        self.assertEqual(UPDATE_CONFLICTS.labels(conflict_type="none")._value, 0)
        mock_log_action.assert_any_call("requirements_updated", category="update_workflow", version=Any, conflicts_detected=0, final_status="success")

    async def test_e2e_compliance_questions(self):
        """Test compliance question handling and storage."""
        result = await self.clarifier.get_clarifications(self.ambiguities, self.requirements, self.user_context)

        profile = load_profile("test_user")
        self.assertTrue(profile.compliance_preferences["gdpr_apply"])
        self.assertEqual(profile.compliance_preferences["data_residency"], "EU")
        self.assertEqual(COMPLIANCE_QUESTIONS_ASKED.labels(question_id="gdpr_apply")._value, 1)
        self.assertEqual(COMPLIANCE_ANSWERS_RECEIVED.labels(question_id="gdpr_apply", answer_value="True")._value, 1)
        mock_log_action.assert_any_call("Compliance Question Answered", {"user_id": "test_user", "question_id": "gdpr_apply", "answer": "True"})

    async def test_e2e_history_corruption(self):
        """Test handling of corrupted history entries."""
        await self.clarifier.get_clarifications(self.ambiguities, self.requirements, self.user_context)
        await asyncio.to_thread(
            self.updater.history_store.conn.execute,
            "UPDATE history SET encrypted_data = ? WHERE version = ?",
            (b'corrupted_data', 1)
        )
        await asyncio.to_thread(self.updater.history_store.conn.commit)

        history = await self.updater.history_store.query(limit=1)
        self.assertEqual(len(history), 0)  # Corrupted entry skipped
        self.assertEqual(UPDATE_ERRORS.labels("history", "decrypt_failed")._value, 1)
        mock_send_alert.assert_awaited_with(Any, severity="medium")

    async def test_e2e_invalid_json_response(self):
        """Test handling of invalid JSON from LLM."""
        with patch('clarifier_llm_call.call_llm_with_fallback', AsyncMock(return_value="Invalid JSON")):
            with self.assertRaises(json.JSONDecodeError):
                await self.clarifier.get_clarifications(self.ambiguities, self.requirements)
            self.assertEqual(LLM_ERRORS.labels("GrokProvider", "grok-1", "JSONDecodeError")._value, 1)
            mock_log_action.assert_any_call("Rule-Based Fallback Used", Any)

if __name__ == '__main__':
    unittest.main()
