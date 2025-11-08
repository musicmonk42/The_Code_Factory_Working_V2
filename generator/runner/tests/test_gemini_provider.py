# test_gemini_provider.py
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
sys.modules['google.generativeai'] = MagicMock() # Mock the google.generativeai client
sys.modules['tenacity'] = MagicMock() # Mock tenacity decorators

# Import the provider module
import main.gemini_provider as gemini_provider

# Hypothesis for property/fuzz testing
import hypothesis
from hypothesis import given, strategies as st
from hypothesis.extra.regex import regex

class TestGeminiProvider(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Patch environment variable for API key
        self.patch_api_key = patch.dict(os.environ, {'GEMINI_API_KEY': 'test_gemini_key_123'})
        self.patch_api_key.start()
        
        # Patch google.generativeai.configure as it's called globally
        self.patch_gemini_configure = patch('main.gemini_provider.configure').start()

        # Patch GenerativeModel as it's used in provider's call methods
        self.mock_generative_model = MagicMock()
        self.patch_generative_model_class = patch('main.gemini_provider.GenerativeModel', return_value=self.mock_generative_model)
        self.patch_generative_model_class.start()

        # Re-create provider instance to ensure it picks up patched env and mocks
        self.provider = gemini_provider.GeminiProvider()
        
        # Reset mocks on provider's client for each test
        self.mock_generative_model.generate_content_async.reset_mock()
        self.mock_generative_model.count_tokens_async.reset_mock()
        
        # Reset Prometheus metrics for each test (as they are global singletons)
        gemini_provider.calls_total.reset_mock()
        gemini_provider.errors_total.reset_mock()
        gemini_provider.latency_seconds.reset_mock()
        gemini_provider.tokens_input.reset_mock()
        gemini_provider.tokens_output.reset_mock()
        gemini_provider.cost_total.reset_mock()
        gemini_provider.health_gauge.reset_mock()
        gemini_provider.stream_chunks_total.reset_mock()
        gemini_provider.stream_chunk_latency.reset_mock()

    def tearDown(self):
        self.patch_api_key.stop()
        self.patch_gemini_configure.stop()
        self.patch_generative_model_class.stop()
        # Ensure circuit breaker is reset for next test if it was opened
        self.provider.reset_circuit()
        
    # --- Test Cases ---

    def test_entry_point_exists_and_correct(self):
        """Contract: Plugin exposes get_provider(), returns correct type and has expected methods."""
        # GeminiProvider is not exposed via get_provider() in this structure,
        # it's meant to be instantiated directly or via LLMPluginManager.
        # So we test the class directly.
        self.assertIsInstance(self.provider, gemini_provider.GeminiProvider)
        self.assertTrue(hasattr(self.provider, "call"))
        self.assertTrue(inspect.iscoroutinefunction(self.provider.call))
        self.assertTrue(hasattr(self.provider, "name")) # Should have a name attribute, though not explicitly set in __init__
        # Default name would be 'gemini' if used in a manager, but not explicitly set in provider.

    async def test_call_non_stream_success(self):
        """Behavior: call returns content and metadata for non-streaming."""
        self.mock_generative_model.generate_content_async.return_value = MagicMock(text="Gemini response")
        self.mock_generative_model.count_tokens_async.return_value = MagicMock(total_tokens=10) # For input tokens
        
        result = await self.provider.call("Hello, Gemini!", "gemini-2.5-pro")
        
        self.assertIsInstance(result, dict)
        self.assertIn("content", result)
        self.assertEqual(result["content"], "Gemini response")
        self.assertIn("model", result)
        self.assertEqual(result["model"], "gemini-2.5-pro")
        
        self.mock_generative_model.generate_content_async.assert_awaited_once_with("Hello, Gemini!", stream=False)
        gemini_provider.calls_total.labels.assert_called_once_with(model="gemini-2.5-pro")
        gemini_provider.latency_seconds.labels.assert_called_once_with(model="gemini-2.5-pro")
        gemini_provider.tokens_input.labels.assert_called_once_with(model="gemini-2.5-pro")
        gemini_provider.tokens_output.labels.assert_called_once_with(model="gemini-2.5-pro")
        gemini_provider.cost_total.labels.assert_called_once_with(model="gemini-2.5-pro")
        self.assertGreater(gemini_provider.cost_total.labels.return_value.inc.call_args[0][0], 0)

    async def test_call_stream_success(self):
        """Behavior: call returns async generator for streaming."""
        async def mock_stream_response():
            yield MagicMock(text="chunk1")
            yield MagicMock(text="chunk2")
        
        self.mock_generative_model.generate_content_async.return_value = mock_stream_response()
        self.mock_generative_model.count_tokens_async.return_value = MagicMock(total_tokens=10) # For input tokens

        gen = await self.provider.call("Stream me, Gemini!", "gemini-2.5-flash", stream=True)
        
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
        
        self.assertEqual(chunks, ["chunk1", "chunk2"])
        self.mock_generative_model.generate_content_async.assert_awaited_once_with("Stream me, Gemini!", stream=True)
        gemini_provider.calls_total.labels.assert_called_once_with(model="gemini-2.5-flash")
        gemini_provider.latency_seconds.labels.assert_called_once_with(model="gemini-2.5-flash")
        gemini_provider.stream_chunks_total.labels.assert_called_once_with(model="gemini-2.5-flash")
        gemini_provider.stream_chunk_latency.labels.assert_called_once_with(model="gemini-2.5-flash")
        self.assertGreater(gemini_provider.cost_total.labels.return_value.inc.call_args[0][0], 0)

    async def test_call_api_error_raises_runtime_error(self):
        """Behavior: API errors are caught and re-raised as RuntimeError."""
        self.mock_generative_model.generate_content_async.side_effect = Exception("API call failed")
        self.mock_generative_model.count_tokens_async.return_value = MagicMock(total_tokens=5) # For input tokens
        
        with self.assertRaisesRegex(RuntimeError, "API error"):
            await self.provider.call("Fail me!", "gemini-2.5-pro")
        
        gemini_provider.calls_total.labels.assert_called_once_with(model="gemini-2.5-pro")
        gemini_provider.errors_total.labels.assert_called_once_with(model="gemini-2.5-pro")
        self.assertEqual(self.provider.circuit_breaker.failures, 1) # Circuit breaker records failure

    async def test_call_invalid_request_error(self):
        self.mock_generative_model.generate_content_async.side_effect = ValueError("Invalid prompt")
        self.mock_generative_model.count_tokens_async.return_value = MagicMock(total_tokens=5)
        
        with self.assertRaisesRegex(ValueError, "Invalid request"):
            await self.provider.call("Invalid prompt", "gemini-2.5-pro")
        gemini_provider.errors_total.labels.assert_called_once_with(model="gemini-2.5-pro")

    async def test_circuit_breaker_opens(self):
        self.provider.circuit_breaker.failure_threshold = 2 # Set low threshold for test
        self.mock_generative_model.generate_content_async.side_effect = Exception("Fail")
        self.mock_generative_model.count_tokens_async.return_value = MagicMock(total_tokens=5)
        
        with self.assertRaises(RuntimeError):
            await self.provider.call("test", "gemini-2.5-pro")
        self.assertFalse(self.provider.circuit_breaker.is_open) # Not open yet
        
        with self.assertRaises(RuntimeError):
            await self.provider.call("test", "gemini-2.5-pro")
        self.assertTrue(self.provider.circuit_breaker.is_open) # Now it's open

        with self.assertRaisesRegex(RuntimeError, "CircuitOpenError"):
            await self.provider.call("test", "gemini-2.5-pro") # Further calls fail immediately

    async def test_circuit_breaker_resets_after_timeout(self):
        self.provider.circuit_breaker.failure_threshold = 1
        self.provider.circuit_breaker.recovery_timeout = 0.01 # Short timeout
        self.mock_generative_model.generate_content_async.side_effect = Exception("Fail")
        self.mock_generative_model.count_tokens_async.return_value = MagicMock(total_tokens=5)

        with self.assertRaises(RuntimeError):
            await self.provider.call("test", "gemini-2.5-pro")
        self.assertTrue(self.provider.circuit_breaker.is_open)

        await asyncio.sleep(0.02) # Wait for timeout

        self.mock_generative_model.generate_content_async.side_effect = None # Clear error
        self.mock_generative_model.generate_content_async.return_value = MagicMock(text="Reset response")
        
        result = await self.provider.call("test", "gemini-2.5-pro")
        self.assertFalse(self.provider.circuit_breaker.is_open)
        self.assertEqual(result["content"], "Reset response")

    async def test_reset_circuit_manual(self):
        self.provider.circuit_breaker.record_failure()
        self.provider.circuit_breaker.record_failure()
        self.assertTrue(self.provider.circuit_breaker.is_open)
        self.provider.reset_circuit()
        self.assertFalse(self.provider.circuit_breaker.is_open)

    async def test_count_tokens_api_failure_fallback(self):
        self.mock_generative_model.count_tokens_async.side_effect = Exception("API failed")
        tokens = await self.provider.count_tokens("This is a test sentence.", "gemini-2.5-pro")
        self.assertGreater(tokens, 0) # Should use approximation

    async def test_health_check_success(self):
        # Mock aiohttp.ClientSession.get for the /models endpoint
        self.mock_aiohttp_session = patch('main.gemini_provider.aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 200
        
        is_healthy = await self.provider.health_check()
        self.assertTrue(is_healthy)
        gemini_provider.health_gauge.labels.assert_called_once_with(provider="gemini")
        gemini_provider.health_gauge.labels.return_value.set.assert_called_once_with(1)

    async def test_health_check_failure(self):
        self.mock_aiohttp_session = patch('main.gemini_provider.aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 500
        is_healthy = await self.provider.health_check()
        self.assertFalse(is_healthy)
        gemini_provider.health_gauge.labels.assert_called_once_with(provider="gemini")
        gemini_provider.health_gauge.labels.return_value.set.assert_called_once_with(0)

    async def test_scrub_text(self):
        sensitive_text = "My api_key = sk-abc123 and email is test@example.com, password: mysecret, SSN: 999-88-7777, CC: 1234-5678-9012-3456."
        scrubbed = gemini_provider.scrub_text(sensitive_text)
        self.assertIn('[REDACTED]', scrubbed)
        self.assertNotIn('sk-abc123', scrubbed)
        self.assertNotIn('test@example.com', scrubbed)
        self.assertNotIn('mysecret', scrubbed)
        self.assertNotIn('999-88-7777', scrubbed)
        self.assertNotIn('1234-5678-9012-3456', scrubbed)

    async def test_register_custom_model(self):
        self.provider.register_custom_model("my-custom-gemini", "gemini-2.5-flash")
        self.assertIn("my-custom-gemini", self.provider.custom_models)
        self.assertEqual(self.provider.custom_models["my-custom-gemini"], "gemini-2.5-flash")

        # Test calling with custom model
        self.mock_generative_model.generate_content_async.return_value = MagicMock(text="Custom model response")
        self.mock_generative_model.count_tokens_async.return_value = MagicMock(total_tokens=10)
        
        result = await self.provider.call("Test custom model", "my-custom-gemini")
        self.assertEqual(result["content"], "Custom model response")
        # Verify the underlying GenerativeModel was instantiated with the correct Gemini model name
        self.patch_generative_model_class.assert_called_with("gemini-2.5-flash")

    async def test_add_pre_hook(self):
        def pre_hook_func(prompt): return prompt + " (processed by hook)"
        self.provider.add_pre_hook(pre_hook_func)
        
        self.mock_generative_model.generate_content_async.return_value = MagicMock(text="Response")
        self.mock_generative_model.count_tokens_async.return_value = MagicMock(total_tokens=5)
        
        await self.provider.call("Original prompt", "gemini-2.5-pro")
        
        self.mock_generative_model.generate_content_async.assert_awaited_once()
        args, kwargs = self.mock_generative_model.generate_content_async.call_args
        self.assertIn("Original prompt (processed by hook)", args[0])

    async def test_add_post_hook(self):
        def post_hook_func(response): response["processed"] = True; return response
        self.provider.add_post_hook(post_hook_func)
        
        self.mock_generative_model.generate_content_async.return_value = MagicMock(text="Response")
        self.mock_generative_model.count_tokens_async.return_value = MagicMock(total_tokens=5)
        
        result = await self.provider.call("Prompt", "gemini-2.5-pro")
        
        self.assertIn("processed", result)
        self.assertTrue(result["processed"])

    @given(prompt=st.text(min_size=1, max_size=200, alphabet=st.characters(blacklist_categories=('Cs',))))
    async def test_call_non_stream_fuzz(self, prompt):
        """Fuzz: Should not crash for random prompt input (non-stream)."""
        self.mock_generative_model.generate_content_async.return_value = MagicMock(text=f"Echo: {prompt}")
        self.mock_generative_model.count_tokens_async.return_value = MagicMock(total_tokens=len(prompt) // 4 + 1) # Approximate tokens
        
        try:
            result = await self.provider.call(prompt, "gemini-2.5-pro")
            self.assertIsInstance(result, dict)
            self.assertIn("content", result)
            self.assertIn("model", result)
        except Exception as e:
            self.fail(f"Fuzz test failed with prompt '{prompt}' due to: {e}")

    @given(prompt=st.text(min_size=1, max_size=200, alphabet=st.characters(blacklist_categories=('Cs',))))
    async def test_call_stream_fuzz(self, prompt):
        """Fuzz: Should not crash for random prompt input (stream)."""
        async def mock_stream_response_fuzz():
            yield MagicMock(text="chunk1")
            yield MagicMock(text="chunk2")
        
        self.mock_generative_model.generate_content_async.return_value = mock_stream_response_fuzz()
        self.mock_generative_model.count_tokens_async.return_value = MagicMock(total_tokens=len(prompt) // 4 + 1)
        
        try:
            gen = await self.provider.call(prompt, "gemini-2.5-flash", stream=True)
            chunks = []
            async for chunk in gen:
                chunks.append(chunk)
            self.assertIsInstance(chunks, list)
            self.assertGreater(len(chunks), 0)
        except Exception as e:
            self.fail(f"Fuzz stream test failed with prompt '{prompt}' due to: {e}")

    def test_docstrings_and_types(self):
        """Type/Docs: Provider methods are typed and documented."""
        # Since get_provider() is not used, test the class directly
        self.assertTrue(inspect.getdoc(self.provider.call))
        sig = inspect.signature(self.provider.call)
        self.assertIn("prompt", sig.parameters)
        self.assertIn("model", sig.parameters)
        self.assertIn("stream", sig.parameters)
        self.assertNotEqual(sig.return_annotation, inspect.Signature.empty) # Should be annotated

if __name__ == "__main__":
    unittest.main()

