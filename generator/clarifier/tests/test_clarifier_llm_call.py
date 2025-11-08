import unittest
import asyncio
import json
from unittest.mock import patch, AsyncMock, MagicMock
from collections import namedtuple
from datetime import datetime, timedelta
import aiohttp
from clarifier_llm_call import (
    GrokProvider, OpenAIProvider, AnthropicProvider,
    call_llm_with_fallback, rule_based_fallback, merge_responses,
    LLM_LATENCY, LLM_ERRORS, LLM_TOKEN_USAGE_PROMPT, LLM_TOKEN_USAGE_COMPLETION,
    LLM_COST, LLM_LANGUAGE_INFERENCES, fallback_cache, CACHE_EXPIRY_SECONDS, CircuitBreaker
)

# Mock dependencies for testing
patch_log_action = patch('clarifier_llm_call.log_action', new=AsyncMock())
mock_log_action = patch_log_action.start()

patch_redact_sensitive = patch('clarifier_llm_call.redact_sensitive', side_effect=lambda x: x.replace('sensitive', '[REDACTED]'))
mock_redact_sensitive = patch_redact_sensitive.start()

patch_estimate_tokens = patch('clarifier_llm_call.estimate_tokens', return_value=10)
mock_estimate_tokens = patch_estimate_tokens.start()

patch_estimate_cost = patch('clarifier_llm_call.estimate_cost', return_value=0.001)
mock_estimate_cost = patch_estimate_cost.start()

patch_compute_hash = patch('clarifier_llm_call.compute_hash', side_effect=lambda x: str(hash(x)))
mock_compute_hash = patch_compute_hash.start()

try:
    from opentelemetry import trace
    patch_tracer = patch('clarifier_llm_call.tracer', new=MagicMock())
    mock_tracer = patch_tracer.start()
except ImportError:
    mock_tracer = None

try:
    from jinja2 import Environment
    patch_jinja_env = patch('clarifier_llm_call._jinja_env', spec=Environment)
    mock_jinja_env = patch_jinja_env.start()
    mock_jinja_env.get_template.return_value.render.side_effect = lambda **kwargs: json.dumps({
        'prioritized': [{'original': kwargs.get('ambiguities_list', [''])[0], 'score': 10, 'question': f"Q: {kwargs.get('ambiguities_list', [''])[0]}"}],
        'batch': [0]
    })
    mock_jinja_env.from_string.return_value.render.side_effect = lambda **kwargs: kwargs.get('text_to_infer', 'en')
except ImportError:
    mock_jinja_env = None

# Mock config for API keys
patch_config = patch('clarifier_llm_call.config', new=MagicMock(
    GROK_API_KEY='mock_grok_key',
    OPENAI_API_KEY='mock_openai_key',
    ANTHROPIC_API_KEY='mock_anthropic_key'
))
mock_config = patch_config.start()

class TestClarifierLLMForRegulatedIndustry(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider = GrokProvider('mock_grok_key')
        self.mock_session_post = patch('aiohttp.ClientSession.post').start()
        self.mock_resp = AsyncMock()
        self.mock_resp.raise_for_status = AsyncMock()
        self.mock_session_post.return_value.__aenter__.return_value = self.mock_resp

        # Reset metrics and cache
        LLM_LATENCY.clear()
        LLM_ERRORS.clear()
        LLM_TOKEN_USAGE_PROMPT.clear()
        LLM_TOKEN_USAGE_COMPLETION.clear()
        LLM_COST.clear()
        LLM_LANGUAGE_INFERENCES.clear()
        fallback_cache.clear()
        mock_log_action.reset_mock()

    async def asyncTearDown(self):
        self.mock_session_post.stop()
        if mock_tracer:
            patch_tracer.stop()
        if mock_jinja_env:
            patch_jinja_env.stop()

    async def test_sensitive_data_redaction(self):
        """Test that sensitive data in prompts and responses is redacted."""
        prompt_params = {
            'ambiguities_list': ['Patient SSN: sensitive-123', 'Normal ambiguity'],
            'min_batch': 1,
            'max_batch': 1
        }
        self.mock_resp.json = AsyncMock(return_value={
            'choices': [{'message': {'content': json.dumps({
                'prioritized': [{'original': 'Patient SSN: sensitive-123', 'score': 10, 'question': 'What is the SSN?'}],
                'batch': [0]
            })}}]
        })

        result = await call_llm_with_fallback('grok', prompt_params, target_language='en')

        # Verify redaction in logs
        log_calls = mock_log_action.call_args_list
        for call in log_calls:
            args, _ = call
            log_data = args[1]
            if 'prompt_hash' in log_data:
                self.assertNotIn('sensitive-123', json.dumps(log_data))
                self.assertIn('[REDACTED]', json.dumps(log_data))

        # Verify redaction in cache
        cache_key = list(fallback_cache.keys())[0]
        cached_response = fallback_cache[cache_key][0]
        self.assertIn('[REDACTED]', json.dumps(cached_response))
        self.assertNotIn('sensitive-123', json.dumps(cached_response))

    async def test_audit_log_compliance(self):
        """Test that all actions are logged with required details for audit."""
        prompt_params = {'ambiguities_list': ['Test ambiguity'], 'min_batch': 1, 'max_batch': 1}
        self.mock_resp.json = AsyncMock(return_value={
            'choices': [{'message': {'content': json.dumps({
                'prioritized': [{'original': 'Test ambiguity', 'score': 10, 'question': 'Clarify Test?'}],
                'batch': [0]
            })}}]
        })

        await call_llm_with_fallback('grok', prompt_params, project='healthcare', user='user123')

        # Verify audit log contains required fields
        log_calls = mock_log_action.call_args_list
        self.assertGreaterEqual(len(log_calls), 1)
        log_data = log_calls[0][0][1]
        self.assertIn('provider', log_data)
        self.assertIn('model', log_data)
        self.assertIn('prompt_hash', log_data)
        self.assertIn('latency_seconds', log_data)
        self.assertIn('prompt_tokens', log_data)
        self.assertIn('completion_tokens', log_data)
        self.assertIn('estimated_cost', log_data)
        self.assertEqual(log_data.get('project'), 'healthcare')
        self.assertEqual(log_data.get('user'), 'user123')

    async def test_circuit_breaker_compliance(self):
        """Test circuit breaker behavior under repeated failures."""
        mock_cb = CircuitBreaker(threshold=2, timeout=10)
        with patch('clarifier_llm_call.get_circuit_breaker', return_value=mock_cb):
            prompt_params = {'ambiguities_list': ['Test breaker'], 'min_batch': 1, 'max_batch': 1}
            self.mock_resp.raise_for_status.side_effect = aiohttp.ClientError("API failure")

            # Trip circuit breaker
            for _ in range(2):
                with self.assertRaises(aiohttp.ClientError):
                    await call_llm_with_fallback('grok', prompt_params)

            self.assertTrue(mock_cb.is_open())

            # Verify fallback is used
            result = await call_llm_with_fallback('grok', prompt_params)
            self.assertIn('prioritized', result)
            self.assertIn('Clarify: Test breaker?', result['prioritized'][0]['question'])
            self.assertEqual(LLM_ERRORS.labels('GrokProvider', 'grok-1', 'CircuitBreaker')._value, 1)

    async def test_invalid_json_response(self):
        """Test handling of invalid JSON responses from LLM."""
        prompt_params = {'ambiguities_list': ['Test ambiguity'], 'min_batch': 1, 'max_batch': 1}
        self.mock_resp.json = AsyncMock(return_value={
            'choices': [{'message': {'content': 'Invalid JSON'}}]
        })

        with self.assertRaises(json.JSONDecodeError):
            await call_llm_with_fallback('grok', prompt_params)

        # Verify fallback is triggered after retries
        self.assertEqual(LLM_ERRORS.labels('GrokProvider', 'grok-1', 'JSONDecodeError')._value, 1)
        mock_log_action.assert_any_call("LLM Call Error", Any)
        mock_log_action.assert_any_call("Rule-Based Fallback Used", Any)

    async def test_language_inference_non_latin(self):
        """Test language inference for non-Latin scripts."""
        prompt_params = {'ambiguities_list': ['这是一个模糊点'], 'min_batch': 1, 'max_batch': 1}
        self.mock_resp.json = AsyncMock(side_effect=[
            {'choices': [{'message': {'content': 'zh'}}]},  # Language inference
            {'choices': [{'message': {'content': json.dumps({
                'prioritized': [{'original': '这是一个模糊点', 'score': 10, 'question': '请澄清这个模糊点？'}],
                'batch': [0]
            })}}]}
        ])

        result = await call_llm_with_fallback('grok', prompt_params, target_language=None)

        self.assertIn('prioritized', result)
        self.assertEqual(result['prioritized'][0]['question'], '请澄清这个模糊点？')
        self.assertEqual(LLM_LANGUAGE_INFERENCES.labels('GrokProvider', detected_language='zh')._value, 1)

    async def test_concurrent_calls(self):
        """Test concurrent LLM calls to ensure metric accuracy."""
        prompt_params = {'ambiguities_list': ['Concurrent test'], 'min_batch': 1, 'max_batch': 1}
        self.mock_resp.json = AsyncMock(return_value={
            'choices': [{'message': {'content': json.dumps({
                'prioritized': [{'original': 'Concurrent test', 'score': 10, 'question': 'Clarify?'}],
                'batch': [0]
            })}}]
        })

        tasks = [call_llm_with_fallback('grok', prompt_params) for _ in range(5)]
        results = await asyncio.gather(*tasks)

        self.assertEqual(len(results), 5)
        self.assertEqual(LLM_LATENCY.labels('GrokProvider', 'grok-1', 'main_call')._count, 5)
        self.assertEqual(LLM_TOKEN_USAGE_PROMPT.labels('GrokProvider', 'grok-1')._value, 5)  # Last set value

    async def test_empty_ambiguities_list(self):
        """Test handling of empty ambiguities list."""
        prompt_params = {'ambiguities_list': [], 'min_batch': 1, 'max_batch': 1}
        result = await call_llm_with_fallback('grok', prompt_params)

        self.assertEqual(result['prioritized'], [])
        self.assertEqual(result['batch'], [])
        self.assertEqual(LLM_LATENCY.labels('GrokProvider', 'grok-1', 'main_call')._count, 0)  # No LLM call
        mock_log_action.assert_any_call("Rule-Based Fallback Used", Any)

    async def test_data_residency_compliance(self):
        """Test that no sensitive data is sent to unauthorized providers."""
        prompt_params = {'ambiguities_list': ['Patient data: sensitive-info'], 'min_batch': 1, 'max_batch': 1}
        self.mock_resp.json = AsyncMock(return_value={
            'choices': [{'message': {'content': json.dumps({
                'prioritized': [{'original': 'Patient data: [REDACTED]', 'score': 10, 'question': 'Clarify patient data?'}],
                'batch': [0]
            })}}]
        })

        result = await call_llm_with_fallback('grok', prompt_params)

        # Verify redacted data was sent to LLM
        call_args = self.mock_session_post.call_args[0][1]['json']['messages']
        self.assertIn('[REDACTED]', json.dumps(call_args))
        self.assertNotIn('sensitive-info', json.dumps(call_args))

    async def test_template_failure(self):
        """Test handling of template rendering failures."""
        if mock_jinja_env:
            mock_jinja_env.get_template.side_effect = Exception("Template not found")
            prompt_params = {'ambiguities_list': ['Test ambiguity'], 'min_batch': 1, 'max_batch': 1}

            result = await call_llm_with_fallback('grok', prompt_params)

            self.assertIn('prioritized', result)
            self.assertIn('Clarify: Test ambiguity?', result['prioritized'][0]['question'])
            mock_log_action.assert_any_call("Rule-Based Fallback Used", Any)

    async def test_rate_limit_handling(self):
        """Test handling of rate limit errors."""
        self.mock_resp.raise_for_status.side_effect = aiohttp.ClientResponseError(
            status=429, message="Rate limit exceeded", request_info=None, history=None
        )
        prompt_params = {'ambiguities_list': ['Rate limit test'], 'min_batch': 1, 'max_batch': 1}

        with self.assertRaises(aiohttp.ClientResponseError):
            await call_llm_with_fallback('grok', prompt_params)

        self.assertEqual(LLM_ERRORS.labels('GrokProvider', 'grok-1', 'ClientResponseError')._value, 1)
        mock_log_action.assert_any_call("LLM Call Error", Any)
        mock_log_action.assert_any_call("Rule-Based Fallback Used", Any)

if __name__ == '__main__':
    unittest.main()

# Cleanup patchers
patch_log_action.stop()
patch_redact_sensitive.stop()
patch_estimate_tokens.stop()
patch_estimate_cost.stop()
patch_compute_hash.stop()
patch_config.stop()
if mock_tracer:
    patch_tracer.stop()
if mock_jinja_env:
    patch_jinja_env.stop()