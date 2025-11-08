# test_claude_provider.py
import unittest
import asyncio
import os
import sys
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path
import inspect
import json # For JSON parsing in mock responses

# Add parent directory to sys.path to import the provider module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock external dependencies before importing the provider
sys.modules['prometheus_client'] = MagicMock()
sys.modules['opentelemetry'] = MagicMock()
sys.modules['opentelemetry.trace'] = MagicMock()
sys.modules['opentelemetry.sdk.trace'] = MagicMock()
sys.modules['opentelemetry.sdk.resources'] = MagicMock()
sys.modules['opentelemetry.sdk.trace.export'] = MagicMock()
sys.modules['aiohttp'] = MagicMock()
sys.modules['anthropic'] = MagicMock() # Mock the anthropic client
sys.modules['tenacity'] = MagicMock() # Mock tenacity decorators

# Import the provider module
import main.claude_provider as claude_provider

# Hypothesis for property/fuzz testing
import hypothesis
from hypothesis import given, strategies as st
from hypothesis.extra.regex import regex

class TestClaudeProvider(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Patch environment variable for API key
        self.patch_api_key = patch.dict(os.environ, {'CLAUDE_API_KEY': 'test_claude_key_123'})
        self.patch_api_key.start()
        
        # Patch AsyncAnthropic client directly as it's initialized in provider's __init__
        self.mock_anthropic_client = MagicMock()
        self.patch_async_anthropic = patch('main.claude_provider.AsyncAnthropic', return_value=self.mock_anthropic_client)
        self.patch_async_anthropic.start()

        # Re-create provider instance to ensure it uses the patched env and client
        self.provider = claude_provider.ClaudeProvider()
        
        # Reset mocks on provider's client for each test
        self.mock_anthropic_client.messages.create.reset_mock()
        
        # Reset Prometheus metrics for each test (as they are global singletons)
        claude_provider.calls_total.reset_mock()
        claude_provider.errors_total.reset_mock()
        claude_provider.latency_seconds.reset_mock()
        claude_provider.token_count.reset_mock()
        claude_provider.cost_total.reset_mock()
        claude_provider.health_gauge.reset_mock()
        claude_provider.stream_chunks_total.reset_mock()
        claude_provider.stream_chunk_latency.reset_mock()

    def tearDown(self):
        self.patch_api_key.stop()
        self.patch_async_anthropic.stop()
        # Ensure circuit breaker is reset for next test if it was opened
        self.provider.reset_circuit()
        
    # --- Test Cases ---

    def test_entry_point_exists_and_correct(self):
        """Contract: Plugin exposes get_provider(), returns correct type and has expected methods."""
        self.assertTrue(hasattr(claude_provider, "get_provider"))
        provider = claude_provider.get_provider()
        self.assertIsInstance(provider, claude_provider.ClaudeProvider)
        self.assertTrue(hasattr(provider, "call"))
        self.assertTrue(inspect.iscoroutinefunction(provider.call))
        self.assertTrue(hasattr(provider, "name")) # Should have a name attribute, though not explicitly set in __init__
        # Default name is 'claude' if not explicitly set
        self.assertEqual(provider.name, "claude") # Assuming default name is 'claude'

    async def test_call_non_stream_success(self):
        """Behavior: call returns content and metadata for non-streaming."""
        self.mock_anthropic_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="Claude response")]
        )
        
        result = await self.provider.call("Hello, Claude!", "claude-3-haiku-20240307")
        
        self.assertIsInstance(result, dict)
        self.assertIn("content", result)
        self.assertEqual(result["content"], "Claude response")
        self.assertIn("model", result)
        self.assertEqual(result["model"], "claude-3-haiku-20240307")
        
        self.mock_anthropic_client.messages.create.assert_awaited_once_with(
            model="claude-3-haiku-20240307", max_tokens=4096, messages=[{"role": "user", "content": "Hello, Claude!"}], stream=False
        )
        claude_provider.calls_total.labels.assert_called_once_with(model="claude-3-haiku-20240307")
        claude_provider.latency_seconds.labels.assert_called_once_with(model="claude-3-haiku-20240307")
        claude_provider.token_count.labels.assert_any_call(type='input', model="claude-3-haiku-20240307")
        claude_provider.token_count.labels.assert_any_call(type='output', model="claude-3-haiku-20240307")
        claude_provider.cost_total.labels.assert_called_once_with(model="claude-3-haiku-20240307")
        self.assertGreater(claude_provider.cost_total.labels.return_value.inc.call_args[0][0], 0)

    async def test_call_stream_success(self):
        """Behavior: call returns async generator for streaming."""
        async def mock_stream_response():
            yield MagicMock(type='content_block_delta', delta=MagicMock(text="chunk1"))
            yield MagicMock(type='content_block_delta', delta=MagicMock(text="chunk2"))
        
        self.mock_anthropic_client.messages.create.return_value = mock_stream_response()

        gen = await self.provider.call("Stream me, Claude!", "claude-3-sonnet-20240229", stream=True)
        
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
        
        self.assertEqual(chunks, ["chunk1", "chunk2"])
        self.mock_anthropic_client.messages.create.assert_awaited_once_with(
            model="claude-3-sonnet-20240229", max_tokens=4096, messages=[{"role": "user", "content": "Stream me, Claude!"}], stream=True
        )
        claude_provider.calls_total.labels.assert_called_once_with(model="claude-3-sonnet-20240229")
        claude_provider.latency_seconds.labels.assert_called_once_with(model="claude-3-sonnet-20240229")
        claude_provider.stream_chunks_total.labels.assert_called_once_with(model="claude-3-sonnet-20240229")
        claude_provider.stream_chunk_latency.labels.assert_called_once_with(model="claude-3-sonnet-20240229")
        self.assertGreater(claude_provider.cost_total.labels.return_value.inc.call_args[0][0], 0)

    async def test_call_api_error_raises_runtime_error(self):
        """Behavior: API errors are caught and re-raised as RuntimeError."""
        self.mock_anthropic_client.messages.create.side_effect = claude_provider.AnthropicError("API call failed")
        
        with self.assertRaisesRegex(RuntimeError, "Anthropic API error"):
            await self.provider.call("Fail me!", "claude-3-haiku-20240307")
        
        claude_provider.calls_total.labels.assert_called_once_with(model="claude-3-haiku-20240307")
        claude_provider.errors_total.labels.assert_called_once_with(model="claude-3-haiku-20240307")
        self.assertEqual(self.provider.circuit_breaker.failures, 1) # Circuit breaker records failure

    async def test_call_authentication_error(self):
        self.mock_anthropic_client.messages.create.side_effect = claude_provider.AuthenticationError("Invalid key")
        with self.assertRaisesRegex(ValueError, "Authentication failed"):
            await self.provider.call("Auth fail", "claude-3-haiku-20240307")
        claude_provider.errors_total.labels.assert_called_once_with(model="claude-3-haiku-20240307")

    async def test_call_rate_limit_error(self):
        self.mock_anthropic_client.messages.create.side_effect = claude_provider.RateLimitError("Too fast")
        with self.assertRaisesRegex(RuntimeError, "Rate limit exceeded"):
            await self.provider.call("Rate limit", "claude-3-haiku-20240307")
        claude_provider.errors_total.labels.assert_called_once_with(model="claude-3-haiku-20240307")

    async def test_call_connection_error(self):
        self.mock_anthropic_client.messages.create.side_effect = claude_provider.APIConnectionError("No network")
        with self.assertRaisesRegex(RuntimeError, "Connection error"):
            await self.provider.call("Connection", "claude-3-haiku-20240307")
        claude_provider.errors_total.labels.assert_called_once_with(model="claude-3-haiku-20240307")

    async def test_circuit_breaker_opens(self):
        self.provider.circuit_breaker.failure_threshold = 2 # Set low threshold for test
        self.mock_anthropic_client.messages.create.side_effect = claude_provider.AnthropicError("Fail")
        
        with self.assertRaises(RuntimeError):
            await self.provider.call("test", "claude-3-haiku-20240307")
        self.assertFalse(self.provider.circuit_breaker.is_open) # Not open yet
        
        with self.assertRaises(RuntimeError):
            await self.provider.call("test", "claude-3-haiku-20240307")
        self.assertTrue(self.provider.circuit_breaker.is_open) # Now it's open

        with self.assertRaisesRegex(RuntimeError, "Circuit breaker open"):
            await self.provider.call("test", "claude-3-haiku-20240307") # Further calls fail immediately

    async def test_circuit_breaker_resets_after_timeout(self):
        self.provider.circuit_breaker.failure_threshold = 1
        self.provider.circuit_breaker.recovery_timeout = 0.01 # Short timeout
        self.mock_anthropic_client.messages.create.side_effect = claude_provider.AnthropicError("Fail")

        with self.assertRaises(RuntimeError):
            await self.provider.call("test", "claude-3-haiku-20240307")
        self.assertTrue(self.provider.circuit_breaker.is_open)

        await asyncio.sleep(0.02) # Wait for timeout

        self.mock_anthropic_client.messages.create.side_effect = None # Clear error
        self.mock_anthropic_client.messages.create.return_value = MagicMock(content=[MagicMock(text="Reset response")])
        
        result = await self.provider.call("test", "claude-3-haiku-20240307")
        self.assertFalse(self.provider.circuit_breaker.is_open)
        self.assertEqual(result["content"], "Reset response")

    async def test_reset_circuit_manual(self):
        self.provider.circuit_breaker.record_failure()
        self.provider.circuit_breaker.record_failure()
        self.assertTrue(self.provider.circuit_breaker.is_open)
        self.provider.reset_circuit()
        self.assertFalse(self.provider.circuit_breaker.is_open)

    async def test_count_tokens(self):
        # Anthropic's count_tokens is synchronous, but we can mock it
        with patch.object(self.provider.client, 'count_tokens', return_value=10) as mock_count:
            tokens = await self.provider.count_tokens("This is a test sentence.", "claude-3-haiku-20240307")
            self.assertEqual(tokens, 10)
            mock_count.assert_called_once_with("This is a test sentence.")

    async def test_health_check_success(self):
        # Mock aiohttp.ClientSession.get for the /models endpoint
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 200
        
        is_healthy = await self.provider.health_check()
        self.assertTrue(is_healthy)
        claude_provider.health_gauge.labels.assert_called_once_with(provider="claude")
        claude_provider.health_gauge.labels.return_value.set.assert_called_once_with(1)

    async def test_health_check_failure(self):
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 500
        is_healthy = await self.provider.health_check()
        self.assertFalse(is_healthy)
        claude_provider.health_gauge.labels.assert_called_once_with(provider="claude")
        claude_provider.health_gauge.labels.return_value.set.assert_called_once_with(0)

    async def test_scrub_prompt(self):
        sensitive_prompt = "My API key is sk-xyz123abc, email: user@example.com, card: 1234-5678-9012-3456, SSN: 999-88-7777."
        scrubbed = self.provider._scrub_prompt(sensitive_prompt)
        self.assertIn("[REDACTED]", scrubbed)
        self.assertNotIn("sk-xyz123abc", scrubbed)
        self.assertNotIn("user@example.com", scrubbed)
        self.assertNotIn("1234-5678-9012-3456", scrubbed)
        self.assertNotIn("999-88-7777", scrubbed)

    async def test_register_custom_model(self):
        custom_endpoint = "http://custom.claude.com/v1"
        custom_headers = {"X-Custom": "Test"}
        self.provider.register_custom_model("my-custom-claude", custom_endpoint, custom_headers)
        
        self.assertIn("my-custom-claude", self.provider.custom_models)
        self.assertEqual(self.provider.custom_models["my-custom-claude"]["endpoint"], custom_endpoint)
        self.assertEqual(self.provider.custom_models["my-custom-claude"]["headers"], custom_headers)

        # Test calling with custom model (requires mocking aiohttp.ClientSession.post for custom endpoint)
        self.mock_aiohttp_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value.status = 200
        self.mock_aiohttp_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value.json.return_value = {"content": [{"text": "Custom model response"}]}
        
        result = await self.provider.call("Test custom model", "my-custom-claude")
        self.assertEqual(result["content"], "Custom model response")
        self.mock_aiohttp_session.return_value.__aenter__.return_value.post.assert_called_with(
            custom_endpoint, json=unittest.mock.ANY, headers={"x-api-key": "test_claude_key_123", **custom_headers}
        )

    async def test_add_pre_hook(self):
        def pre_hook_func(prompt): return prompt + " (processed by hook)"
        self.provider.add_pre_hook(pre_hook_func)
        
        self.mock_anthropic_client.messages.create.return_value = MagicMock(content=[MagicMock(text="Response")])
        await self.provider.call("Original prompt", "claude-3-haiku-20240307")
        
        self.mock_anthropic_client.messages.create.assert_awaited_once()
        args, kwargs = self.mock_anthropic_client.messages.create.call_args
        self.assertIn("Original prompt (processed by hook)", kwargs['messages'][0]['content'])

    async def test_add_post_hook(self):
        def post_hook_func(response): response["processed"] = True; return response
        self.provider.add_post_hook(post_hook_func)
        
        self.mock_anthropic_client.messages.create.return_value = MagicMock(content=[MagicMock(text="Response")])
        result = await self.provider.call("Prompt", "claude-3-haiku-20240307")
        
        self.assertIn("processed", result)
        self.assertTrue(result["processed"])

    @given(prompt=st.text(min_size=1, max_size=200, alphabet=st.characters(blacklist_categories=('Cs',))))
    async def test_call_non_stream_fuzz(self, prompt):
        """Fuzz: Should not crash for random prompt input (non-stream)."""
        self.mock_anthropic_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=f"Echo: {prompt}")]
        )
        try:
            result = await self.provider.call(prompt, "claude-3-haiku-20240307")
            self.assertIsInstance(result, dict)
            self.assertIn("content", result)
            self.assertIn("model", result)
        except Exception as e:
            self.fail(f"Fuzz test failed with prompt '{prompt}' due to: {e}")

    @given(prompt=st.text(min_size=1, max_size=200, alphabet=st.characters(blacklist_categories=('Cs',))))
    async def test_call_stream_fuzz(self, prompt):
        """Fuzz: Should not crash for random prompt input (stream)."""
        async def mock_stream_response_fuzz():
            yield MagicMock(type='content_block_delta', delta=MagicMock(text="chunk1"))
            yield MagicMock(type='content_block_delta', delta=MagicMock(text="chunk2"))
        
        self.mock_anthropic_client.messages.create.return_value = mock_stream_response_fuzz()

        try:
            gen = await self.provider.call(prompt, "claude-3-haiku-20240307", stream=True)
            chunks = []
            async for chunk in gen:
                chunks.append(chunk)
            self.assertIsInstance(chunks, list)
            self.assertGreater(len(chunks), 0)
        except Exception as e:
            self.fail(f"Fuzz stream test failed with prompt '{prompt}' due to: {e}")

    def test_docstrings_and_types(self):
        """Type/Docs: Provider methods are typed and documented."""
        provider = claude_provider.get_provider()
        self.assertTrue(inspect.getdoc(provider.call))
        sig = inspect.signature(provider.call)
        self.assertIn("prompt", sig.parameters)
        self.assertIn("model", sig.parameters)
        self.assertIn("stream", sig.parameters)
        self.assertNotEqual(sig.return_annotation, inspect.Signature.empty) # Should be annotated

if __name__ == "__main__":
    unittest.main()
