```python
import asyncio
import json
import os
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path
from intent_parser import IntentParser, IntentParserConfig, LLMConfig, MultiLanguageSupportConfig
from intent_parser import PARSE_LATENCY, PARSE_ERRORS, AMBIGUITY_RATE, LANG_DETECTION_COUNT, FORMAT_DETECTION_COUNT, EXTRACTION_COUNT, LLM_CLIENT_CALLS, REDACTION_COUNT, FEEDBACK_RECORDED_COUNT, CACHE_CORRUPTION_EVENTS
from clarifier import Clarifier
from clarifier_user_prompt import UserPromptChannel, CLIPrompt
from clarifier_updater import RequirementsUpdater, HistoryStore

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

patch_clarifier_config = patch('clarifier.get_config', return_value=MagicMock(
    INTERACTION_MODE='cli',
    TARGET_LANGUAGE='en',
    KMS_KEY='mock_key',
    ALERT_ENDPOINT='http://mock-alert:8080',
    SCHEMA_VERSION=2,
    CONFLICT_STRATEGY='auto_merge'
))
mock_clarifier_config = patch_clarifier_config.start()

patch_clarifier_fernet = patch('clarifier.get_fernet', return_value=MagicMock(
    encrypt=lambda x: b'encrypted_' + x,
    decrypt=lambda x: x[len(b'encrypted_'):],
))
mock_clarifier_fernet = patch_clarifier_fernet.start()

patch_get_channel = patch('clarifier_user_prompt.get_channel', return_value=AsyncMock(spec=UserPromptChannel))
mock_get_channel = patch_get_channel.start()

class TestIntentParserE2ERegulated(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Reset metrics
        from intent_parser import PARSE_LATENCY, PARSE_ERRORS, AMBIGUITY_RATE, LANG_DETECTION_COUNT, FORMAT_DETECTION_COUNT, EXTRACTION_COUNT, LLM_CLIENT_CALLS, REDACTION_COUNT, FEEDBACK_RECORDED_COUNT, CACHE_CORRUPTION_EVENTS
        from clarifier import CLARIFIER_CYCLES, CLARIFIER_LATENCY, CLARIFIER_ERRORS
        from clarifier_user_prompt import PROMPT_CYCLES, PROMPT_LATENCY, PROMPT_ERRORS, COMPLIANCE_ANSWERS_RECEIVED
        from clarifier_updater import UPDATE_CYCLES, UPDATE_ERRORS, UPDATE_CONFLICTS, REDACTION_EVENTS
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
        CLARIFIER_CYCLES.clear()
        CLARIFIER_LATENCY.clear()
        CLARIFIER_ERRORS.clear()
        PROMPT_CYCLES.clear()
        PROMPT_LATENCY.clear()
        PROMPT_ERRORS.clear()
        COMPLIANCE_ANSWERS_RECEIVED.clear()
        UPDATE_CYCLES.clear()
        UPDATE_ERRORS.clear()
        UPDATE_CONFLICTS.clear()
        REDACTION_EVENTS.clear()

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

        # Initialize IntentParser and Clarifier components
        self.parser = IntentParser(self.config)
        self.parser.feedback = {'ratings': []}
        self.clarifier = Clarifier()
        self.updater = RequirementsUpdater()
        self.updater.history_store = HistoryStore(':memory:', mock_fernet.return_value)
        await self.updater.history_store._init_db()

        # Mock user interaction channel
        self.mock_channel = AsyncMock(spec=UserPromptChannel)
        self.mock_channel.prompt = AsyncMock(side_effect=[
            ['Markdown, PDF'],  # Documentation formats
            ['OAuth login', '[REDACTED_SECRET] key']  # Clarifications
        ])
        self.mock_channel.ask_compliance_questions = AsyncMock(return_value={
            'gdpr_apply': True,
            'phi_data': False,
            'pci_dss': True,
            'data_residency': 'EU',
            'child_privacy': False
        })
        mock_get_channel.return_value = self.mock_channel
        self.clarifier.interaction = self.mock_channel

        # Setup test data
        self.content = "- User login system\n- Payment processing\nConstraint: Must be secure\nSSN: 123-45-6789\nsecret_key_v1: abc123"
        self.user_id = 'test_user'
        self.correlation_id = 'test-correlation-id'

        # Create directories
        os.makedirs('parser_cache', exist_ok=True)
        os.makedirs('user_profiles', exist_ok=True)

    async def asyncTearDown(self):
        await self.updater.history_store.close()
        import shutil
        if os.path.exists('parser_cache'):
            shutil.rmtree('parser_cache')
        if os.path.exists('user_profiles'):
            shutil.rmtree('user_profiles')
        if os.path.exists('feedback.json'):
            os.remove('feedback.json')
        patch_redact_sensitive.stop()
        patch_log_action.stop()
        patch_detect_pii.stop()
        patch_fernet.stop()
        patch_translator.stop()
        patch_tracer.stop()
        patch_aiohttp_session.stop()
        patch_clarifier_config.stop()
        patch_clarifier_fernet.stop()
        patch_get_channel.stop()

    async def test_e2e_parse_and_clarify(self):
        """Test full E2E pipeline: parsing, ambiguity detection, clarification, and requirement update."""
        with patch('intent_parser.LLMDetector.detect', AsyncMock(return_value=['Login method unclear', 'Payment method unspecified'])):
            with patch('intent_parser.LLMSummarizer.summarize', return_value={
                'features': ['User login system', 'Payment processing'],
                'constraints': ['Must be secure'],
                'ambiguities': ['Login method unclear', 'Payment method unspecified'],
                'summary': 'Login and payment system with security constraints.'
            }):
                with patch('clarifier_llm_call.call_llm_with_fallback', AsyncMock(return_value={
                    'prioritized': [
                        {'original': 'Login method unclear', 'score': 8, 'question': 'What login method is required?'},
                        {'original': 'Payment method unspecified', 'score': 10, 'question': 'Specify payment method.'}
                    ],
                    'batch': [0, 1]
                })):
                    # Parse document
                    parsed_result = await self.parser.parse(self.content, user_id=self.user_id)

                    # Clarify ambiguities
                    requirements = {
                        'features': parsed_result['features'],
                        'constraints': parsed_result['constraints'],
                        'schema_version': 1
                    }
                    clarified_result = await self.clarifier.get_clarifications(parsed_result['ambiguities'], requirements, {'user_id': self.user_id})

        # Verify parsed result
        self.assertEqual(parsed_result['features'], ['User login system', 'Payment processing'])
        self.assertEqual(parsed_result['constraints'], ['Must be secure'])
        self.assertIn('[REDACTED_SSN]', json.dumps(parsed_result))
        self.assertNotIn('123-45-6789', json.dumps(parsed_result))
        self.assertEqual(PARSE_LATENCY._count, 1)
        self.assertEqual(REDACTION_COUNT._value, 1)

        # Verify clarified result
        self.assertIn('desired_doc_formats', clarified_result)
        self.assertEqual(clarified_result['desired_doc_formats'], ['Markdown', 'PDF'])
        self.assertIn('clarifications', clarified_result)
        self.assertEqual(clarified_result['clarifications']['Login method unclear'], 'OAuth login')
        self.assertEqual(clarified_result['clarifications']['Payment method unspecified'], '[REDACTED_SECRET] key')
        self.assertEqual(clarified_result['schema_version'], 2)

        # Verify compliance questions
        profile = self.clarifier.interaction.load_profile(self.user_id)
        self.assertTrue(profile.compliance_preferences['gdpr_apply'])
        self.assertEqual(profile.compliance_preferences['data_residency'], 'EU')

        # Verify history storage
        history = await self.updater.history_store.query(limit=1)
        self.assertEqual(len(history), 1)
        self.assertIn('[REDACTED_SSN]', json.dumps(history[0]))
        self.assertTrue(self.updater._verify_hash_chain(history[0]))

        # Verify metrics
        self.assertEqual(PARSE_LATENCY._count, 1)
        self.assertEqual(CLARIFIER_CYCLES.labels(status='started')._value, 1)
        self.assertEqual(PROMPT_CYCLES.labels(channel='CLIPrompt')._value, 2)  # Doc formats + clarifications
        self.assertEqual(UPDATE_CYCLES._value, 1)
        self.assertEqual(REDACTION_EVENTS.labels(pattern_type='ssn')._value, 1)
        self.assertEqual(COMPLIANCE_ANSWERS_RECEIVED.labels(question_id='gdpr_apply', answer_value='True')._value, 1)

        # Verify audit logging
        mock_log_action.assert_any_call('Parse Completed', Any)
        mock_log_action.assert_any_call('Prompt Interaction', Any)
        mock_log_action.assert_any_call('Compliance Question Answered', Any)
        mock_log_action.assert_any_call('requirements_updated', category='update_workflow', version=Any, conflicts_detected=0, final_status='success')

    async def test_e2e_pii_redaction(self):
        """Test PII redaction across the pipeline."""
        content = "SSN: 123-45-6789\nsecret_key_v1: abc123"
        with patch('intent_parser.LLMDetector.detect', AsyncMock(return_value=['SSN usage unclear'])):
            with patch('intent_parser.LLMSummarizer.summarize', return_value={
                'features': [],
                'constraints': [],
                'ambiguities': ['SSN usage unclear'],
                'summary': 'Document with sensitive data.'
            }):
                with patch('clarifier_llm_call.call_llm_with_fallback', AsyncMock(return_value={
                    'prioritized': [{'original': '[REDACTED_SSN] usage unclear', 'score': 10, 'question': 'Clarify SSN usage?'}],
                    'batch': [0]
                })):
                    parsed_result = await self.parser.parse(content, user_id=self.user_id)
                    requirements = {'features': [], 'constraints': [], 'schema_version': 1}
                    clarified_result = await self.clarifier.get_clarifications(parsed_result['ambiguities'], requirements, {'user_id': self.user_id})

        self.assertIn('[REDACTED_SSN]', json.dumps(parsed_result))
        self.assertNotIn('123-45-6789', json.dumps(parsed_result))
        self.assertIn('[REDACTED_SSN]', json.dumps(clarified_result))
        self.assertEqual(REDACTION_COUNT._value, 1)
        self.assertEqual(REDACTION_EVENTS.labels(pattern_type='ssn')._value, 1)
        log_calls = mock_log_action.call_args_list
        for call in log_calls:
            self.assertNotIn('123-45-6789', json.dumps(call))

    async def test_e2e_llm_failure(self):
        """Test LLM failure handling with fallback."""
        with patch('intent_parser.LLMDetector.detect', side_effect=aiohttp.ClientError('API failure')):
            with patch('intent_parser.LLMDetector.fallback_detection', return_value=['fallback ambiguity']):
                parsed_result = await self.parser.parse(self.content, user_id=self.user_id)

        self.assertEqual(parsed_result['ambiguities'], ['fallback ambiguity'])
        self.assertEqual(LLM_CLIENT_FALLBACKS.labels(reason='ClientError')._value, 1)
        self.assertEqual(PARSE_ERRORS.labels(stage='ambiguity_detection', error_type='ClientError')._value, 1)

    async def test_e2e_multilingual(self):
        """Test parsing and clarifying Spanish content."""
        content = "- rasgo: Sistema de login\nRestricción: Debe ser seguro"
        with patch('intent_parser.detect', return_value='es'):
            with patch('intent_parser.LLMDetector.detect', AsyncMock(return_value=['Método de login poco claro'])):
                with patch('intent_parser.LLMSummarizer.summarize', return_value={
                    'features': ['Sistema de login'],
                    'constraints': ['Debe ser seguro'],
                    'ambiguities': ['Método de login poco claro'],
                    'summary': 'Sistema de login con restricciones de seguridad.'
                }):
                    with patch('clarifier_llm_call.call_llm_with_fallback', AsyncMock(return_value={
                        'prioritized': [{'original': 'Método de login poco claro', 'score': 8, 'question': '¿Qué método de login se requiere?'}],
                        'batch': [0]
                    })):
                        parsed_result = await self.parser.parse(content, user_id=self.user_id)
                        requirements = {'features': parsed_result['features'], 'constraints': parsed_result['constraints'], 'schema_version': 1}
                        clarified_result = await self.clarifier.get_clarifications(parsed_result['ambiguities'], requirements, {'user_id': self.user_id})

        self.assertEqual(parsed_result['features'], ['Sistema de login'])
        self.assertEqual(clarified_result['clarifications']['Método de login poco claro'], 'OAuth login')
        self.assertEqual(LANG_DETECTION_COUNT.labels(language='es')._value, 1)

    async def test_e2e_concurrent_requests(self):
        """Test concurrent parsing and clarification requests."""
        with patch('intent_parser.LLMDetector.detect', AsyncMock(return_value=['Login method unclear'])):
            with patch('intent_parser.LLMSummarizer.summarize', return_value={
                'features': ['User login system'],
                'constraints': ['Must be secure'],
                'ambiguities': ['Login method unclear'],
                'summary': 'Login system with security constraints.'
            }):
                tasks = [self.parser.parse(self.content, user_id=self.user_id) for _ in range(3)]
                results = await asyncio.gather(*tasks)

        self.assertEqual(len(results), 3)
        self.assertEqual(PARSE_LATENCY._count, 3)
        self.assertEqual(CLARIFIER_CYCLES.labels(status='started')._value, 0)  # Clarifier not called
        history = await self.updater.history_store.query(limit=3)
        self.assertEqual(len(history), 0)  # No clarifications yet

    async def test_e2e_cache_corruption(self):
        """Test handling of corrupted cache entries."""
        cache_file = os.path.join('parser_cache', f"{hashlib.sha256(self.content.encode()).hexdigest()}.cache")
        with open(cache_file, 'wb') as f:
            f.write(b'corrupted_data')

        with patch('intent_parser.LLMDetector.detect', AsyncMock(return_value=['ambiguity'])):
            result = await self.parser.parse(self.content, user_id=self.user_id)

        self.assertEqual(CACHE_CORRUPTION_EVENTS._value, 1)
        self.assertEqual(PARSE_LATENCY._count, 1)
        mock_log_action.assert_any_call('Parse Completed', Any)

    async def test_e2e_feedback_integration(self):
        """Test feedback recording and integration with Clarifier."""
        with patch('intent_parser.LLMDetector.detect', AsyncMock(return_value=['Login method unclear'])):
            with patch('intent_parser.LLMSummarizer.summarize', return_value={
                'features': ['User login system'],
                'constraints': ['Must be secure'],
                'ambiguities': ['Login method unclear'],
                'summary': 'Login system with security constraints.'
            }):
                parsed_result = await self.parser.parse(self.content, user_id=self.user_id)
                feedback = {'rating': 0.8, 'comments': 'Good clarity'}
                await self.parser.record_feedback(feedback, self.user_id)

        self.assertEqual(FEEDBACK_RECORDED_COUNT._value, 1)
        with open('feedback.json', 'r') as f:
            feedback_data = json.load(f)
        self.assertIn({'rating': 0.8, 'comments': '[REDACTED_SECRET] clarity'}, feedback_data['ratings'])

if __name__ == '__main__':
    unittest.main()
```