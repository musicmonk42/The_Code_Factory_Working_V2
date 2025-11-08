
import asyncio
import json
import os
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path
from intent_parser import IntentParser, IntentParserConfig, LLMConfig, MultiLanguageSupportConfig, ParserStrategy, RegexExtractor, LLMDetector, LLMSummarizer
from intent_parser import PARSE_LATENCY, PARSE_ERRORS, AMBIGUITY_RATE, LANG_DETECTION_COUNT, FORMAT_DETECTION_COUNT, EXTRACTION_COUNT, LLM_CLIENT_CALLS, REDACTION_COUNT, FEEDBACK_RECORDED_COUNT, CACHE_CORRUPTION_EVENTS

# Mock dependencies
patch_redact_sensitive = patch('intent_parser.redact_sensitive', side_effect=lambda x: x.replace('secret', '[REDACTED_SECRET]').replace('123-45-6789', '[REDACTED_SSN]'))
mock_redact_sensitive = patch_redact_sensitive.start()

patch_log_action = patch('intent_parser.log_action', AsyncMock())
mock_log_action = patch_log_action.start()

patch_detect_pii = patch('intent_parser.detect_pii', side_effect=lambda x: 'secret' in x or '123-45-6789' in x)
mock_detect_pii = patch_detect_pii.start()

patch_fernet = patch('intent_parser.Fernet', return_value=MagicMock(
    encrypt=lambda x: b'encrypted_' + x,
    decrypt=lambda x: x[len(b'encrypted_'):],
))
mock_fernet = patch_fernet.start()

patch_translator = patch('intent_parser.Translator', return_value=MagicMock())
mock_translator = patch_translator.start()

patch_tracer = patch('intent_parser.tracer', new=MagicMock())
mock_tracer = patch_tracer.start()

patch_aiohttp_session = patch('aiohttp.ClientSession')
mock_aiohttp_session = patch_aiohttp_session.start()

class TestIntentParser(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Reset metrics
        PARSE_LATENCY.clear()
        PARSE_ERRORS.clear()
        AMBIGUITY_RATE.clear()
        LANG_DETECTION_COUNT.clear()
        FORMAT_DETECTION_COUNT.clear()
        EXTRACTION_COUNT.clear()
        LLM_CLIENT_CALLS.clear()
        REDACTION_COUNT.clear()
        FEEDBACK_RECORDED_COUNT.clear()
        CACHE_CORRUPTION_EVENTS.clear()

        # Reset mocks
        mock_log_action.reset_mock()
        mock_redact_sensitive.reset_mock()
        mock_detect_pii.reset_mock()
        mock_translator.reset_mock()
        mock_tracer.reset_mock()

        # Setup test configuration
        self.config = IntentParserConfig(
            format='auto',
            extraction_patterns={'features': r'-\s*(.+)', 'constraints': r'Constraint:\s*(.+)'},
            llm_config=LLMConfig(provider='openai', model='gpt-4o', api_key_env_var='OPENAI_API_KEY'),
            feedback_file='feedback.json',
            cache_dir='parser_cache',
            multi_language_support=MultiLanguageSupportConfig(
                enabled=True,
                default_lang='en',
                language_patterns={
                    'es': {'features': r'- *(rasgo|característica):\s*(.+)', 'constraints': r'Restricción:\s*(.+)'}
                }
            ),
            security_config={'enable_custom_redaction': True, 'custom_redaction_patterns': [r'SSN:\s*(\d{3}-\d{2}-\d{4})'], 'pii_detection_sensitivity': 'medium'}
        )

        # Initialize IntentParser
        self.parser = IntentParser(self.config)
        self.parser.feedback = {'ratings': []}
        self.content = "- Feature 1\n- Feature 2\nConstraint: Must be secure\nSSN: 123-45-6789"
        self.user_id = 'test_user'

        # Create cache directory
        os.makedirs('parser_cache', exist_ok=True)

    async def asyncTearDown(self):
        import shutil
        if os.path.exists('parser_cache'):
            shutil.rmtree('parser_cache')
        if os.path.exists('feedback.json'):
            os.remove('feedback.json')
        patch_redact_sensitive.stop()
        patch_log_action.stop()
        patch_detect_pii.stop()
        patch_fernet.stop()
        patch_translator.stop()
        patch_tracer.stop()
        patch_aiohttp_session.stop()

    async def test_parse_markdown(self):
        """Test parsing Markdown content."""
        self.parser.parser = self.parser._select_parser('markdown')
        result = await self.parser.parse(self.content, user_id=self.user_id)

        self.assertIn('features', result)
        self.assertIn('constraints', result)
        self.assertIn('ambiguities', result)
        self.assertEqual(result['features'], ['Feature 1', 'Feature 2'])
        self.assertEqual(result['constraints'], ['Must be secure'])
        self.assertIn('[REDACTED_SSN]', json.dumps(result))
        self.assertEqual(FORMAT_DETECTION_COUNT.labels(format='markdown')._value, 1)
        self.assertEqual(EXTRACTION_COUNT.labels(extractor_type='RegexExtractor', language='en')._value, 1)
        mock_log_action.assert_any_call('Parse Completed', Any)

    async def test_pii_redaction(self):
        """Test PII redaction in parsed content."""
        result = await self.parser.parse(self.content, user_id=self.user_id)

        self.assertIn('[REDACTED_SSN]', json.dumps(result))
        self.assertNotIn('123-45-6789', json.dumps(result))
        self.assertEqual(REDACTION_COUNT._value, 1)
        mock_redact_sensitive.assert_called()
        mock_detect_pii.assert_called()

    async def test_multilingual_support(self):
        """Test parsing Spanish content."""
        content = "- rasgo: Característica 1\nRestricción: Debe ser seguro"
        with patch('intent_parser.detect', return_value='es'):
            result = await self.parser.parse(content, user_id=self.user_id)

        self.assertEqual(result['features'], ['Característica 1'])
        self.assertEqual(result['constraints'], ['Debe ser seguro'])
        self.assertEqual(LANG_DETECTION_COUNT.labels(language='es')._value, 1)
        mock_translator.return_value.translate.assert_called()

    async def test_cache_encryption(self):
        """Test encryption of cached LLM responses."""
        with patch.object(self.parser.detector, 'detect', AsyncMock(return_value=['ambiguity'])):
            result = await self.parser.parse(self.content, user_id=self.user_id)

        cache_file = os.path.join('parser_cache', f"{hashlib.sha256(self.content.encode()).hexdigest()}.cache")
        with open(cache_file, 'rb') as f:
            cached_data = f.read()
        self.assertTrue(cached_data.startswith(b'encrypted_'))
        mock_fernet.return_value.encrypt.assert_called()

    async def test_llm_failure_fallback(self):
        """Test fallback on LLM failure."""
        with patch.object(self.parser.detector, 'detect', side_effect=aiohttp.ClientError("API failure")):
            with patch('intent_parser.LLMDetector.fallback_detection', return_value=['fallback ambiguity']):
                result = await self.parser.parse(self.content, user_id=self.user_id)

        self.assertEqual(result['ambiguities'], ['fallback ambiguity'])
        self.assertEqual(LLM_CLIENT_FALLBACKS.labels(reason='ClientError')._value, 1)
        self.assertEqual(PARSE_ERRORS.labels(stage='ambiguity_detection', error_type='ClientError')._value, 1)
        mock_log_action.assert_any_call('Parse Completed', Any)

    async def test_feedback_recording(self):
        """Test recording user feedback."""
        with patch('aiofiles.open', new_callable=AsyncMock) as mock_open:
            feedback = {'rating': 0.8, 'comments': 'Good clarity'}
            await self.parser.record_feedback(feedback, self.user_id)

        self.assertEqual(FEEDBACK_RECORDED_COUNT._value, 1)
        mock_open.assert_called_with('feedback.json', mode='w', encoding='utf-8')
        mock_log_action.assert_called_with('Feedback Recorded', {'user_id': self.user_id, 'feedback': Any})

    async def test_invalid_format(self):
        """Test handling of unsupported document format."""
        with self.assertRaises(ValueError) as cm:
            self.parser._select_parser('unsupported')
        self.assertEqual(str(cm.exception), 'Unsupported format: unsupported')
        self.assertEqual(PARSE_ERRORS.labels(stage='parser_selection', error_type='ValueError')._value, 1)

    async def test_empty_content(self):
        """Test handling of empty content."""
        with self.assertRaises(ValueError) as cm:
            await self.parser.parse('', user_id=self.user_id)
        self.assertEqual(str(cm.exception), 'No content or file path provided.')
        self.assertEqual(PARSE_ERRORS.labels(stage='overall_parse', error_type='ValueError')._value, 1)

if __name__ == '__main__':
    unittest.main()
