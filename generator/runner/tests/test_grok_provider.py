# test_grok_provider.py
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
sys.modules['tiktoken'] = MagicMock() # Mock tiktoken as it's an external dependency
sys.modules['tenacity'] = MagicMock() # Mock tenacity decorators

# Import the provider module
import main.grok_provider as grok_provider

# Hypothesis for property/fuzz testing
import hypothesis
from hypothesis import given, strategies as st
from hypothesis.extra.regex import regex

class TestGrokProvider(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Patch environment variable for API key
        self.patch_api_key = patch.dict(os.environ, {'GROK_API_KEY': 'test_grok_key_123'})
        self.patch_api_key.start()
        
        # Mock tiktoken's get_encoding and encode method
        self.mock_tiktoken_encoding = MagicMock()
        self.mock_tiktoken_encoding.encode.return_value = [1, 2] # Simulate 2 tokens
        self.patch_tiktoken_get_encoding = patch('main.grok_provider.tiktoken.get_encoding', return_value=self.mock_tiktoken_encoding)
        self.patch_tiktoken_get_encoding.start()

        # Re-create provider instance to ensure it picks up patched env and mocks
        self.provider = grok_provider.GrokProvider()
        
        # Reset Prometheus metrics for each test (as they are global singletons)
        grok_provider.calls_total.reset_mock()
        grok_provider.errors_total.reset_mock()
        grok_provider.latency_seconds.reset_mock()
        grok_provider.tokens_input.reset_mock()
        grok_provider.tokens_output.reset_mock()
        grok_provider.cost_total.reset_mock()
        grok_provider.health_gauge.reset_mock()
        grok_provider.stream_chunks_total.reset_mock()
        grok_provider.stream_chunk_latency.reset_mock()

    def tearDown(self):
        self.patch_api_key.stop()
        self.patch_tiktoken_get_encoding.stop()
        # Ensure circuit breaker is reset for next test if it was opened
        self.provider.reset_circuit()
        
    # --- Test Cases ---

    def test_entry_point_exists_and_correct(self):
        """Contract: Plugin exposes get_provider(), returns correct type and has expected methods."""
        self.assertTrue(hasattr(grok_provider, "get_provider"))
        provider = grok_provider.get_provider()
        self.assertIsInstance(provider, grok_provider.GrokProvider)
        self.assertTrue(hasattr(provider, "call"))
        self.assertTrue(inspect.iscoroutinefunction(provider.call))
        self.assertTrue(hasattr(provider, "name")) # Should have a name attribute, though not explicitly set in __init__
        # Default name is 'grok' if not explicitly set
        self.assertEqual(provider.name, "grok") # Assuming default name is 'grok'

    async def test_call_non_stream_success(self):
        """Behavior: call returns content and metadata for non-streaming."""
        self.mock_aiohttp_session = patch('main.grok_provider.aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 200
        self.mock_aiohttp_session.return_value.__aenter__.return_value.json.return_value = {'choices': [{'message': {'content': 'Grok response'}}]}
        
        result = await self.provider.call("Hello, Grok!", "grok-4")
        
        self.assertIsInstance(result, dict)
        self.assertIn("content", result)
        self.assertEqual(result["content"], "Grok response")
        self.assertIn("model", result)
        self.assertEqual(result["model"], "grok-4")
        
        self.mock_aiohttp_session.return_value.__aenter__.return_value.post.assert_awaited_once_with(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": "Bearer test_grok_key_123", "Content-Type": "application/json"},
            json={"model": "grok-4", "messages": [{"role": "user", "content": "Hello, Grok!"}], "stream": False}
        )
        grok_provider.calls_total.labels.assert_called_once_with(model="grok-4")
        grok_provider.latency_seconds.labels.assert_called_once_with(model="grok-4")
        grok_provider.tokens_input.labels.assert_called_once_with(model="grok-4")
        grok_provider.tokens_output.labels.assert_called_once_with(model="grok-4")
        grok_provider.cost_total.labels.assert_called_once_with(model="grok-4")
        self.assertGreater(grok_provider.cost_total.labels.return_value.inc.call_args[0][0], 0)

    async def test_call_stream_success(self):
        """Behavior: call returns async generator for streaming."""
        async def mock_stream_content():
            yield b'data: {"choices": [{"delta": {"content": "chunk1"}}]}\n'
            yield b'data: {"choices": [{"delta": {"content": "chunk2"}}]}\n'

        self.mock_aiohttp_session = patch('main.grok_provider.aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 200
        self.mock_aiohttp_session.return_value.__aenter__.return_value.content = mock_stream_content()
        
        gen = await self.provider.call("Stream me, Grok!", "grok-3", stream=True)
        
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
        
        self.assertEqual(chunks, ["chunk1", "chunk2"])
        self.mock_aiohttp_session.return_value.__aenter__.return_value.post.assert_awaited_once()
        grok_provider.calls_total.labels.assert_called_once_with(model="grok-3")
        grok_provider.latency_seconds.labels.assert_called_once_with(model="grok-3")
        grok_provider.stream_chunks_total.labels.assert_called_once_with(model="grok-3")
        grok_provider.stream_chunk_latency.labels.assert_called_once_with(model="grok-3")
        self.assertGreater(grok_provider.cost_total.labels.return_value.inc.call_args[0][0], 0)

    async def test_call_api_error_raises_client_error(self):
        """Behavior: API errors are caught and re-raised as aiohttp.ClientError."""
        self.mock_aiohttp_session = patch('main.grok_provider.aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 500
        self.mock_aiohttp_session.return_value.__aenter__.return_value.text = AsyncMock(return_value="Internal Server Error")
        
        with self.assertRaisesRegex(aiohttp.ClientError, "API error: 500 - Internal Server Error"):
            await self.provider.call("Fail me!", "grok-4")
        
        grok_provider.calls_total.labels.assert_called_once_with(model="grok-4")
        grok_provider.errors_total.labels.assert_called_once_with(model="grok-4")
        self.assertEqual(self.provider.circuit_breaker.failures, 1) # Circuit breaker records failure

    async def test_circuit_breaker_opens(self):
        self.provider.circuit_breaker.failure_threshold = 2 # Set low threshold for test
        self.mock_aiohttp_session = patch('main.grok_provider.aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 500
        self.mock_aiohttp_session.return_value.__aenter__.return_value.text = AsyncMock(return_value="Error")
        
        with self.assertRaises(aiohttp.ClientError):
            await self.provider.call("test", "grok-4")
        self.assertFalse(self.provider.circuit_breaker.is_open) # Not open yet
        
        with self.assertRaises(aiohttp.ClientError):
            await self.provider.call("test", "grok-4")
        self.assertTrue(self.provider.circuit_breaker.is_open) # Now it's open

        with self.assertRaisesRegex(RuntimeError, "CircuitOpenError"):
            await self.provider.call("test", "grok-4") # Further calls fail immediately

    async def test_circuit_breaker_resets_after_timeout(self):
        self.provider.circuit_breaker.failure_threshold = 1
        self.provider.circuit_breaker.recovery_timeout = 0.01 # Short timeout
        self.mock_aiohttp_session = patch('main.grok_provider.aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 500
        self.mock_aiohttp_session.return_value.__aenter__.return_value.text = AsyncMock(return_value="Error")

        with self.assertRaises(aiohttp.ClientError):
            await self.provider.call("test", "grok-4")
        self.assertTrue(self.provider.circuit_breaker.is_open)

        await asyncio.sleep(0.02) # Wait for timeout

        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 200
        self.mock_aiohttp_session.return_value.__aenter__.return_value.json.return_value = {'choices': [{'message': {'content': 'Reset response'}}]}
        
        result = await self.provider.call("test", "grok-4")
        self.assertFalse(self.provider.circuit_breaker.is_open)
        self.assertEqual(result["content"], "Reset response")

    async def test_reset_circuit_manual(self):
        self.provider.circuit_breaker.record_failure()
        self.provider.circuit_breaker.record_failure()
        self.assertTrue(self.provider.circuit_breaker.is_open)
        self.provider.reset_circuit()
        self.assertFalse(self.provider.circuit_breaker.is_open)

    async def test_count_tokens(self):
        # tiktoken's encode method is mocked in setUp
        self.mock_tiktoken_encoding.encode.return_value = [1, 2, 3, 4, 5] # Simulate 5 tokens
        tokens = await self.provider.count_tokens("This is a test sentence.", "grok-4")
        self.assertEqual(tokens, 5)
        self.mock_tiktoken_encoding.encode.assert_called_once_with("This is a test sentence.")

    async def test_health_check_success(self):
        self.mock_aiohttp_session = patch('main.grok_provider.aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 200
        
        is_healthy = await self.provider.health_check()
        self.assertTrue(is_healthy)
        grok_provider.health_gauge.labels.assert_called_once_with(provider="grok")
        grok_provider.health_gauge.labels.return_value.set.assert_called_once_with(1)

    async def test_health_check_failure(self):
        self.mock_aiohttp_session = patch('main.grok_provider.aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 500
        is_healthy = await self.provider.health_check()
        self.assertFalse(is_healthy)
        grok_provider.health_gauge.labels.assert_called_once_with(provider="grok")
        grok_provider.health_gauge.labels.return_value.set.assert_called_once_with(0)

    async def test_scrub_prompt(self):
        sensitive_prompt = "My API key is sk-xyz123abc, and my email is user@example.com. My card is 1234-5678-9012-3456."
        scrubbed = self.provider._scrub_prompt(sensitive_prompt)
        self.assertIn("[REDACTED]", scrubbed)
        self.assertNotIn("sk-xyz123abc", scrubbed)
        self.assertNotIn("user@example.com", scrubbed)
        self.assertNotIn("1234-5678-9012-3456", scrubbed)

    async def test_register_custom_model(self):
        custom_endpoint = "http://custom.grok.com/v1"
        custom_headers = {"X-Custom": "Test"}
        self.provider.register_custom_model("my-custom-grok", custom_endpoint, custom_headers)
        
        self.assertIn("my-custom-grok", self.provider.custom_models)
        self.assertEqual(self.provider.custom_models["my-custom-grok"]["endpoint"], custom_endpoint)
        self.assertEqual(self.provider.custom_models["my-custom-grok"]["headers"], custom_headers)

        # Test calling with custom model (requires mocking aiohttp.ClientSession.post for custom endpoint)
        self.mock_aiohttp_session = patch('main.grok_provider.aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 200
        self.mock_aiohttp_session.return_value.__aenter__.return_value.json.return_value = {'choices': [{'message': {'content': 'Custom model response'}}]}
        
        result = await self.provider.call("Test custom model", "my-custom-grok")
        self.assertEqual(result["content"], "Custom model response")
        self.mock_aiohttp_session.return_value.__aenter__.return_value.post.assert_called_with(
            custom_endpoint, json=unittest.mock.ANY, headers={"Authorization": "Bearer test_grok_key_123", "Content-Type": "application/json", **custom_headers}
        )

    async def test_add_pre_hook(self):
        def pre_hook_func(prompt): return prompt + " (processed by hook)"
        self.provider.add_pre_hook(pre_hook_func)
        
        self.mock_aiohttp_session = patch('main.grok_provider.aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 200
        self.mock_aiohttp_session.return_value.__aenter__.return_value.json.return_value = {'choices': [{'message': {'content': 'Response'}}]}

        await self.provider.call("Original prompt", "grok-4")
        
        self.mock_aiohttp_session.return_value.__aenter__.return_value.post.assert_awaited_once()
        args, kwargs = self.mock_aiohttp_session.return_value.__aenter__.return_value.post.call_args
        self.assertIn("Original prompt (processed by hook)", kwargs['json']['messages'][0]['content'])

    async def test_add_post_hook(self):
        def post_hook_func(response): response["processed"] = True; return response
        self.provider.add_post_hook(post_hook_func)
        
        self.mock_aiohttp_session = patch('main.grok_provider.aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 200
        self.mock_aiohttp_session.return_value.__aenter__.return_value.json.return_value = {'choices': [{'message': {'content': 'Response'}}]}

        result = await self.provider.call("Prompt", "grok-4")
        
        self.assertIn("processed", result)
        self.assertTrue(result["processed"])

    @given(prompt=st.text(min_size=1, max_size=200, alphabet=st.characters(blacklist_categories=('Cs',))))
    async def test_call_non_stream_fuzz(self, prompt):
        """Fuzz: Should not crash for random prompt input (non-stream)."""
        self.mock_aiohttp_session = patch('main.grok_provider.aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 200
        self.mock_aiohttp_session.return_value.__aenter__.return_value.json.return_value = {'choices': [{'message': {'content': f"Echo: {prompt}"}}]}
        
        try:
            result = await self.provider.call(prompt, "grok-4")
            self.assertIsInstance(result, dict)
            self.assertIn("content", result)
            self.assertIn("model", result)
        except Exception as e:
            self.fail(f"Fuzz test failed with prompt '{prompt}' due to: {e}")

    @given(prompt=st.text(min_size=1, max_size=200, alphabet=st.characters(blacklist_categories=('Cs',))))
    async def test_call_stream_fuzz(self, prompt):
        """Fuzz: Should not crash for random prompt input (stream)."""
        async def mock_stream_content_fuzz():
            yield b'data: {"choices": [{"delta": {"content": "chunk1"}}]}\n'
            yield b'data: {"choices": [{"delta": {"content": "chunk2"}}]}\n'

        self.mock_aiohttp_session = patch('main.grok_provider.aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 200
        self.mock_aiohttp_session.return_value.__aenter__.return_value.content = mock_stream_content_fuzz()
        
        try:
            gen = await self.provider.call(prompt, "grok-3", stream=True)
            chunks = []
            async for chunk in gen:
                chunks.append(chunk)
            self.assertIsInstance(chunks, list)
            self.assertGreater(len(chunks), 0)
        except Exception as e:
            self.fail(f"Fuzz stream test failed with prompt '{prompt}' due to: {e}")

    def test_docstrings_and_types(self):
        """Type/Docs: Provider methods are typed and documented."""
        provider = grok_provider.get_provider()
        self.assertTrue(inspect.getdoc(provider.call))
        sig = inspect.signature(provider.call)
        self.assertIn("prompt", sig.parameters)
        self.assertIn("model", sig.parameters)
        self.assertIn("stream", sig.parameters)
        self.assertNotEqual(sig.return_annotation, inspect.Signature.empty) # Should be annotated

if __name__ == "__main__":
    unittest.main()
