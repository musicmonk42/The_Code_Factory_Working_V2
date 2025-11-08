
import asyncio
import json
import os
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from clarifier_user_prompt import (
    UserPromptChannel, CLIPrompt, GUIPrompt, WebPrompt, SlackPrompt, EmailPrompt, SMSPrompt, VoicePrompt,
    load_profile, save_profile, store_compliance_answer, recover_error,
    PROMPT_CYCLES, PROMPT_LATENCY, PROMPT_ERRORS, USER_ENGAGEMENT, FEEDBACK_RATINGS,
    COMPLIANCE_QUESTIONS_ASKED, COMPLIANCE_ANSWERS_RECEIVED, HAS_TEXTUAL, HAS_FASTAPI
)
from cryptography.fernet import Fernet, InvalidToken
from googletrans import Translator

# Mock dependencies
patch_config = patch('clarifier_user_prompt.get_config', return_value=MagicMock(
    CLARIFIER_EMAIL_SERVER='smtp.mock.com',
    CLARIFIER_EMAIL_PORT=587,
    CLARIFIER_EMAIL_USER='user@mock.com',
    CLARIFIER_EMAIL_PASS='mockpass',
    CLARIFIER_SLACK_WEBHOOK='https://slack.mock.com',
    CLARIFIER_SMS_API='https://sms.mock.com',
    CLARIFIER_SMS_KEY='mock_sms_key'
))
mock_config = patch_config.start()

patch_fernet = patch('clarifier_user_prompt.get_fernet', return_value=MagicMock(
    encrypt=lambda x: b'encrypted_' + x,
    decrypt=lambda x: x[len(b'encrypted_'):],
))
mock_fernet = patch_fernet.start()

patch_logger = patch('clarifier_user_prompt.get_logger', return_value=MagicMock())
mock_logger = patch_logger.start()

patch_log_action = patch('clarifier_user_prompt.log_action', AsyncMock())
mock_log_action = patch_log_action.start()

patch_translator = patch('clarifier_user_prompt.Translator', return_value=MagicMock())
mock_translator = patch_translator.start()

patch_redact_sensitive = patch('clarifier_user_prompt.redact_sensitive', side_effect=lambda x: x.replace('secret', '[REDACTED_SECRET]').replace('user@example.com', '[REDACTED_EMAIL]').replace('123-45-6789', '[REDACTED_SSN]'))
mock_redact_sensitive = patch_redact_sensitive.start()

patch_detect_language = patch('clarifier_user_prompt.detect_language', return_value='en')
mock_detect_language = patch_detect_language.start()

class TestClarifierUserPromptRegulated(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Reset metrics
        PROMPT_CYCLES.clear()
        PROMPT_LATENCY.clear()
        PROMPT_ERRORS.clear()
        USER_ENGAGEMENT.clear()
        FEEDBACK_RATINGS.clear()
        COMPLIANCE_QUESTIONS_ASKED.clear()
        COMPLIANCE_ANSWERS_RECEIVED.clear()

        # Reset mocks
        mock_log_action.reset_mock()
        mock_logger.reset_mock()
        mock_translator.reset_mock()
        mock_redact_sensitive.reset_mock()
        mock_detect_language.reset_mock()

        # Setup test user profile
        self.user_id = 'test_user'
        self.context = {'user_id': self.user_id, 'user_email': 'user@example.com', 'user_phone': '+1234567890'}
        self.questions = ["What is the feature?", "Describe the secret API key."]
        self.profile = load_profile(self.user_id)
        self.profile.language = 'en'
        save_profile(self.user_id, self.profile)

        # Mock Translator
        mock_translator.return_value.translate.return_value.text = lambda x: x

        # Create temporary profile directory
        os.makedirs('user_profiles', exist_ok=True)

    async def asyncTearDown(self):
        # Cleanup profile directory
        import shutil
        if os.path.exists('user_profiles'):
            shutil.rmtree('user_profiles')
        patch_config.stop()
        patch_fernet.stop()
        patch_logger.stop()
        patch_log_action.stop()
        patch_translator.stop()
        patch_redact_sensitive.stop()
        patch_detect_language.stop()

    async def test_cli_prompt_pii_redaction(self):
        """Test CLI prompt with PII redaction."""
        channel = CLIPrompt(target_language='en')
        with patch('builtins.input', side_effect=['secret answer', '[REDACTED_SSN]']):
            answers = await channel.prompt(self.questions, self.context)

        self.assertEqual(answers, ["[REDACTED_SECRET] answer", "[REDACTED_SSN]"])
        mock_redact_sensitive.assert_called()
        self.assertEqual(PROMPT_CYCLES.labels(channel='CLIPrompt')._value, 1)
        mock_log_action.assert_any_call("Prompt Interaction", {
            "user_id": self.user_id, "channel": "CLIPrompt", "questions": self.questions,
            "answers": ["[REDACTED_SECRET] answer", "[REDACTED_SSN]"], "duration": Any, "language": "en"
        })

    @unittest.skipUnless(HAS_TEXTUAL, "Textual not installed.")
    async def test_gui_prompt_encryption(self):
        """Test GUI prompt with encrypted answers."""
        channel = GUIPrompt(target_language='en')
        with patch('textual.app.App.run', new=AsyncMock(return_value=["encrypted_answer"])):
            answers = await channel.prompt(self.questions, self.context)

        self.assertEqual(len(answers), 2)
        decrypted_answer = channel._decrypt_answer(answers[0])
        self.assertEqual(decrypted_answer, "[REDACTED_SECRET] answer")
        mock_fernet.return_value.encrypt.assert_called()
        self.assertEqual(PROMPT_CYCLES.labels(channel='GUIPrompt')._value, 1)

    @unittest.skipUnless(HAS_FASTAPI, "FastAPI not installed.")
    async def test_web_prompt_compliance(self):
        """Test Web prompt with compliance answers."""
        channel = WebPrompt(target_language='en')
        channel._user_session_id_mock = 'mock_session_uuid'
        async def mock_wait_for_answer():
            await asyncio.sleep(0.01)
            await WebPrompt._web_prompt_queue[channel._user_session_id_mock].put(["Web Answer", "[REDACTED_SSN]"])
            return ["Web Answer", "[REDACTED_SSN]"]

        with patch('clarifier_user_prompt.WebPrompt._web_prompt_queue', defaultdict(asyncio.Queue)), \
             patch('clarifier_user_prompt.WebPrompt._web_question_cache', {}), \
             patch('uuid.uuid4', return_value='mock_session_uuid'), \
             patch('asyncio.wait_for', new=mock_wait_for_answer):
            answers = await channel.prompt(self.questions, self.context)

        self.assertEqual(answers, ["Web Answer", "[REDACTED_SSN]"])
        mock_redact_sensitive.assert_called()
        self.assertEqual(PROMPT_CYCLES.labels(channel='WebPrompt')._value, 1)
        mock_log_action.assert_any_call("Prompt Interaction", Any)

    @patch('aiohttp.ClientSession.post', new_callable=AsyncMock)
    async def test_slack_prompt_data_residency(self, mock_post):
        """Test Slack prompt with data residency compliance."""
        mock_response = AsyncMock()
        mock_response.raise_for_status = AsyncMock()
        mock_post.return_value.__aenter__.return_value = mock_response

        channel = SlackPrompt(target_language='en')
        answers = await channel.prompt(self.questions, self.context)

        self.assertEqual(answers, ["Mocked Slack Answer"])
        sent_payload = mock_post.call_args[1]['json']
        self.assertNotIn("secret", json.dumps(sent_payload))
        self.assertIn("[REDACTED_SECRET]", json.dumps(sent_payload))
        self.assertEqual(PROMPT_CYCLES.labels(channel='SlackPrompt')._value, 1)
        mock_log_action.assert_any_call("Prompt Interaction", Any)

    @patch('smtplib.SMTP', new_callable=MagicMock)
    @patch('ssl.create_default_context', return_value=MagicMock())
    async def test_email_prompt_encryption(self, mock_ssl_context, mock_smtp):
        """Test Email prompt with encrypted answers."""
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        channel = EmailPrompt(target_language='en')
        answers = await channel.prompt(self.questions, self.context)

        self.assertEqual(answers, ["Mocked Email Answer"])
        mock_fernet.return_value.encrypt.assert_called()
        mock_smtp.assert_called_with('smtp.mock.com', 587)
        mock_server.sendmail.assert_called_once()
        self.assertEqual(PROMPT_CYCLES.labels(channel='EmailPrompt')._value, 1)
        mock_log_action.assert_any_call("Prompt Interaction", Any)

    @patch('aiohttp.ClientSession.post', new_callable=AsyncMock)
    async def test_sms_prompt_truncation(self, mock_post):
        """Test SMS prompt with truncation and PII redaction."""
        mock_response = AsyncMock()
        mock_response.raise_for_status = AsyncMock()
        mock_post.return_value.__aenter__.return_value = mock_response

        channel = SMSPrompt(target_language='en')
        long_question = "This is a very long question that should be truncated for SMS to ensure it fits within the 160-character limit."
        answers = await channel.prompt([long_question, "Secret question?"], self.context)

        self.assertEqual(answers, ["Mocked SMS Answer"])
        sent_data = mock_post.call_args[1]['data']
        self.assertLess(len(sent_data['body']), 160)
        self.assertIn("[REDACTED_SECRET]", sent_data['body'])
        self.assertEqual(PROMPT_CYCLES.labels(channel='SMSPrompt')._value, 1)

    @patch('speech_recognition.Recognizer.listen', new_callable=AsyncMock)
    @patch('speech_recognition.Recognizer.recognize_google', return_value='voice answer with secret')
    @patch('speech_recognition.Microphone', new_callable=MagicMock)
    async def test_voice_prompt_accessibility(self, mock_mic, mock_recognize_google, mock_listen):
        """Test Voice prompt with accessibility and PII redaction."""
        mock_mic.return_value.__enter__.return_value = mock_mic.return_value
        mock_mic.return_value.__exit__.return_value = False

        channel = VoicePrompt(target_language='en')
        profile = load_profile(self.user_id)
        profile.preferences['accessibility'] = True
        save_profile(self.user_id, profile)

        answers = await channel.prompt(self.questions, self.context)

        self.assertEqual(answers, ["[REDACTED_SECRET] answer with [REDACTED_SECRET]"])
        mock_redact_sensitive.assert_called()
        self.assertEqual(PROMPT_CYCLES.labels(channel='VoicePrompt')._value, 1)
        mock_log_action.assert_any_call("Prompt Interaction", Any)

    async def test_compliance_questions_all_channels(self):
        """Test compliance questions across all supported channels."""
        channels = ['cli', 'slack', 'email', 'sms', 'voice']
        if HAS_TEXTUAL:
            channels.append('gui')
        if HAS_FASTAPI:
            channels.append('web')

        mock_answers = {
            "gdpr_apply": True,
            "phi_data": False,
            "pci_dss": True,
            "data_residency": "EU",
            "child_privacy": False
        }

        for channel_name in channels:
            with patch('clarifier_user_prompt.store_compliance_answer', new=MagicMock()) as mock_store_compliance:
                if channel_name == 'cli':
                    with patch('builtins.input', side_effect=['yes', 'no', 'yes', 'EU', 'no']):
                        channel = CLIPrompt(target_language='en')
                        await channel.ask_compliance_questions(self.user_id, self.context)
                elif channel_name == 'slack':
                    with patch('aiohttp.ClientSession.post', new_callable=AsyncMock) as mock_post:
                        mock_response = AsyncMock()
                        mock_response.raise_for_status = AsyncMock()
                        mock_post.return_value.__aenter__.return_value = mock_response
                        channel = SlackPrompt(target_language='en')
                        await channel.ask_compliance_questions(self.user_id, self.context)
                elif channel_name == 'email':
                    with patch('smtplib.SMTP', new_callable=MagicMock) as mock_smtp:
                        mock_server = MagicMock()
                        mock_smtp.return_value.__enter__.return_value = mock_server
                        channel = EmailPrompt(target_language='en')
                        await channel.ask_compliance_questions(self.user_id, self.context)
                elif channel_name == 'sms':
                    with patch('aiohttp.ClientSession.post', new_callable=AsyncMock) as mock_post:
                        mock_response = AsyncMock()
                        mock_response.raise_for_status = AsyncMock()
                        mock_post.return_value.__aenter__.return_value = mock_response
                        channel = SMSPrompt(target_language='en')
                        await channel.ask_compliance_questions(self.user_id, self.context)
                elif channel_name == 'voice':
                    with patch('speech_recognition.Recognizer.listen', new_callable=AsyncMock), \
                         patch('speech_recognition.Recognizer.recognize_google', side_effect=['yes', 'no', 'yes', 'EU', 'no']):
                        channel = VoicePrompt(target_language='en')
                        await channel.ask_compliance_questions(self.user_id, self.context)
                elif channel_name == 'gui' and HAS_TEXTUAL:
                    with patch('textual.app.App.run', new=AsyncMock(return_value=['yes', 'no', 'yes', 'EU', 'no'])):
                        channel = GUIPrompt(target_language='en')
                        await channel.ask_compliance_questions(self.user_id, self.context)
                elif channel_name == 'web' and HAS_FASTAPI:
                    channel = WebPrompt(target_language='en')
                    channel._user_session_id_mock_compliance = 'mock_session_uuid'
                    async def mock_wait_for_compliance():
                        await asyncio.sleep(0.01)
                        await WebPrompt._web_compliance_queue[channel._user_session_id_mock_compliance].put(mock_answers)
                        return mock_answers
                    with patch('clarifier_user_prompt.WebPrompt._web_compliance_queue', defaultdict(asyncio.Queue)), \
                         patch('clarifier_user_prompt.WebPrompt._web_compliance_questions_cache', {}), \
                         patch('uuid.uuid4', return_value='mock_session_uuid'), \
                         patch('asyncio.wait_for', new=mock_wait_for_compliance):
                        await channel.ask_compliance_questions(self.user_id, self.context)

                self.assertEqual(mock_store_compliance.call_count, len(COMPLIANCE_QUESTIONS))
                mock_store_compliance.assert_any_call(self.user_id, 'gdpr_apply', True)
                mock_store_compliance.assert_any_call(self.user_id, 'data_residency', 'EU')
                self.assertEqual(COMPLIANCE_ANSWERS_RECEIVED.labels(question_id='gdpr_apply', answer_value='True')._value, 1)
                mock_log_action.assert_any_call("Compliance Question Answered", Any)

    async def test_translation_failure(self):
        """Test translation failure handling."""
        channel = CLIPrompt(target_language='fr')
        mock_translator.return_value.translate.side_effect = Exception("Translation API down")
        with patch('builtins.input', side_effect=['answer']):
            answers = await channel.prompt(self.questions, self.context)

        self.assertEqual(answers, ["answer"])
        self.assertEqual(PROMPT_ERRORS.labels(channel='CLIPrompt', type='translation_failed')._value, 1)
        mock_log_action.assert_any_call("Prompt Interaction", Any)

    async def test_concurrent_prompts(self):
        """Test concurrent prompts across channels."""
        channel = CLIPrompt(target_language='en')
        with patch('builtins.input', side_effect=['answer1', 'answer2', 'answer3']):
            tasks = [channel.prompt(self.questions, self.context) for _ in range(3)]
            results = await asyncio.gather(*tasks)

        self.assertEqual(len(results), 3)
        self.assertEqual(PROMPT_CYCLES.labels(channel='CLIPrompt')._value, 3)
        self.assertEqual(PROMPT_LATENCY.labels(channel='CLIPrompt')._count, 3)
        mock_log_action.assert_called()

    async def test_error_recovery(self):
        """Test error recovery mechanism."""
        channel = CLIPrompt(target_language='en')
        with patch('builtins.input', side_effect=['invalid', 'corrected answer']):
            answer = await recover_error(channel, "Test Q?", "Invalid input", self.context)

        self.assertEqual(answer, "corrected answer")
        self.assertEqual(PROMPT_ERRORS.labels(channel='CLIPrompt', type='recovery_prompt')._value, 1)
        mock_log_action.assert_any_call("Prompt Recovery Attempt", Any)

    async def test_profile_corruption(self):
        """Test handling of corrupted user profile."""
        with open(os.path.join('user_profiles', f"{self.user_id}.json"), 'w') as f:
            f.write("invalid json")
        
        profile = load_profile(self.user_id)
        self.assertEqual(profile.user_id, self.user_id)
        self.assertEqual(profile.preferred_channel, 'cli')
        self.assertEqual(PROMPT_ERRORS.labels(channel='system', type='profile_load_corrupt')._value, 1)

    async def test_encryption_failure(self):
        """Test handling of encryption failure."""
        channel = CLIPrompt(target_language='en')
        mock_fernet.return_value.encrypt.side_effect = Exception("Encryption error")
        with patch('builtins.input', side_effect=['answer']):
            answers = await channel.prompt(self.questions, self.context)

        self.assertEqual(answers, ["answer"])  # Fallback to unencrypted
        self.assertEqual(PROMPT_ERRORS.labels(channel='CLIPrompt', type='encryption_failed')._value, 1)
        mock_log_action.assert_any_call("Prompt Interaction", Any)

if __name__ == '__main__':
    unittest.main()
