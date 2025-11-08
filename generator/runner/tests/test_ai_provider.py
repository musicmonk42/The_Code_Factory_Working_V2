# test_ai_provider.py
import unittest
import asyncio
import os
import sys
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path
import inspect

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

# Import the provider module
import main.ai_provider as ai_provider

# Hypothesis for property/fuzz testing
import hypothesis
from hypothesis import given, strategies as st
from hypothesis.extra.regex import regex

class TestOpenAIProvider(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Patch environment variable for API key
        self.patch_api_key = patch.dict(os.environ, {'OPENAI_API_KEY': 'test_api_key_123'})
        self.patch_api_key.start()
        
        # Re-initialize provider to pick up patched env var
        # Also patch AsyncOpenAI client directly as it's initialized in provider's __init__
        self.mock_openai_client = MagicMock()
        self.patch_async_openai = patch('main.ai_provider.AsyncOpenAI', return_value=self.mock_openai_client)
        self.patch_async_openai.start()

        # Re-create provider instance to ensure it uses the patched env and client
        self.provider = ai_provider.OpenAIProvider()
        
        # Reset mocks on provider's client for each test
        self.mock_openai_client.chat.completions.create.reset_mock()
        
        # Mock tiktoken's get_encoding
        self.mock_tiktoken_encoding = MagicMock()
        self.mock_tiktoken_encoding.encode.return_value = [1, 2] # Simulate 2 tokens
        self.patch_tiktoken_get_encoding = patch('main.ai_provider.get_encoding', return_value=self.mock_tiktoken_encoding)
        self.patch_tiktoken_get_encoding.start()

        # Reset Prometheus metrics for each test (as they are global singletons)
        ai_provider.call_counter.reset_mock()
        ai_provider.latency_histogram.reset_mock()
        ai_provider.error_counter.reset_mock()
        ai_provider.cost_gauge.reset_mock()

    def tearDown(self):
        self.patch_api_key.stop()
        self.patch_async_openai.stop()
        self.patch_tiktoken_get_encoding.stop()
        # Ensure circuit breaker is reset for next test if it was opened
        self.provider.reset_circuit()
        
    # --- Test Cases ---

    def test_entry_point_exists_and_correct(self):
        """Contract: Plugin exposes get_provider(), returns correct type and has expected methods."""
        self.assertTrue(hasattr(ai_provider, "get_provider"))
        provider = ai_provider.get_provider()
        self.assertIsInstance(provider, ai_provider.OpenAIProvider)
        self.assertTrue(hasattr(provider, "call"))
        self.assertTrue(inspect.iscoroutinefunction(provider.call))
        self.assertTrue(hasattr(provider, "name")) # Should have a name attribute, though not explicitly set in __init__
        # Default name is 'openai' if not explicitly set
        self.assertEqual(provider.name, "openai") # Assuming default name is 'openai'

    async def test_call_non_stream_success(self):
        """Behavior: call returns content and metadata for non-streaming."""
        self.mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Test response"))]
        )
        
        result = await self.provider.call("Hello, world!", "gpt-3.5-turbo")
        
        self.assertIsInstance(result, dict)
        self.assertIn("content", result)
        self.assertEqual(result["content"], "Test response")
        self.assertIn("model", result)
        self.assertEqual(result["model"], "gpt-3.5-turbo")
        
        self.mock_openai_client.chat.completions.create.assert_awaited_once_with(
            model="gpt-3.5-turbo", messages=[{"role": "user", "content": "Hello, world!"}], stream=False
        )
        ai_provider.call_counter.labels.assert_called_once_with(model="gpt-3.5-turbo", status='success')
        ai_provider.latency_histogram.labels.assert_called_once_with(model="gpt-3.5-turbo")
        ai_provider.cost_gauge.labels.assert_called_once_with(model="gpt-3.5-turbo")
        self.assertGreater(ai_provider.cost_gauge.labels.return_value.set.call_args[0][0], 0) # Cost should be calculated

    async def test_call_stream_success(self):
        """Behavior: call returns async generator for streaming."""
        async def mock_stream_response():
            yield MagicMock(choices=[MagicMock(delta=MagicMock(content="chunk1"))])
            yield MagicMock(choices=[MagicMock(delta=MagicMock(content="chunk2"))])
        
        self.mock_openai_client.chat.completions.create.return_value = mock_stream_response()

        gen = await self.provider.call("Stream me!", "gpt-4", stream=True)
        
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
        
        self.assertEqual(chunks, ["chunk1", "chunk2"])
        self.mock_openai_client.chat.completions.create.assert_awaited_once_with(
            model="gpt-4", messages=[{"role": "user", "content": "Stream me!"}], stream=True
        )
        ai_provider.call_counter.labels.assert_called_once_with(model="gpt-4", status='success')
        ai_provider.latency_histogram.labels.assert_called_once_with(model="gpt-4")
        ai_provider.cost_gauge.labels.assert_called_once_with(model="gpt-4")
        self.assertGreater(ai_provider.cost_gauge.labels.return_value.set.call_args[0][0], 0)

    async def test_call_api_error_raises_runtime_error(self):
        """Behavior: API errors are caught and re-raised as RuntimeError."""
        self.mock_openai_client.chat.completions.create.side_effect = ai_provider.OpenAIError("API call failed")
        
        with self.assertRaisesRegex(RuntimeError, "OpenAI API error"):
            await self.provider.call("Fail me!", "gpt-3.5-turbo")
        
        ai_provider.call_counter.labels.assert_called_once_with(model="gpt-3.5-turbo", status='failure')
        ai_provider.error_counter.labels.assert_called_once_with(model="gpt-3.5-turbo", error_type="OpenAIError")
        self.assertEqual(self.provider.circuit_breaker.failures, 1) # Circuit breaker records failure

    async def test_call_authentication_error(self):
        self.mock_openai_client.chat.completions.create.side_effect = ai_provider.AuthenticationError("Invalid key")
        with self.assertRaisesRegex(ValueError, "Authentication failed"):
            await self.provider.call("Auth fail", "gpt-3.5-turbo")
        ai_provider.error_counter.labels.assert_called_once_with(model="gpt-3.5-turbo", error_type="AuthenticationError")

    async def test_call_rate_limit_error(self):
        self.mock_openai_client.chat.completions.create.side_effect = ai_provider.RateLimitError("Too fast")
        with self.assertRaisesRegex(RuntimeError, "Rate limit exceeded"):
            await self.provider.call("Rate limit", "gpt-3.5-turbo")
        ai_provider.error_counter.labels.assert_called_once_with(model="gpt-3.5-turbo", error_type="RateLimitError")

    async def test_call_connection_error(self):
        self.mock_openai_client.chat.completions.create.side_effect = ai_provider.APIConnectionError("No network")
        with self.assertRaisesRegex(RuntimeError, "Connection error"):
            await self.provider.call("Connection", "gpt-3.5-turbo")
        ai_provider.error_counter.labels.assert_called_once_with(model="gpt-3.5-turbo", error_type="APIConnectionError")

    async def test_circuit_breaker_opens(self):
        self.provider.circuit_breaker.failure_threshold = 2 # Set low threshold for test
        self.mock_openai_client.chat.completions.create.side_effect = ai_provider.OpenAIError("Fail")
        
        with self.assertRaises(RuntimeError):
            await self.provider.call("test", "gpt-3.5-turbo")
        self.assertFalse(self.provider.circuit_breaker.is_open) # Not open yet
        
        with self.assertRaises(RuntimeError):
            await self.provider.call("test", "gpt-3.5-turbo")
        self.assertTrue(self.provider.circuit_breaker.is_open) # Now it's open
        self.assertTrue(self.provider.disabled) # Provider should be disabled

        with self.assertRaisesRegex(RuntimeError, "Provider disabled due to circuit breaker"):
            await self.provider.call("test", "gpt-3.5-turbo") # Further calls fail immediately

    async def test_circuit_breaker_resets_after_timeout(self):
        self.provider.circuit_breaker.failure_threshold = 1
        self.provider.circuit_breaker.reset_timeout = 0.01 # Short timeout
        self.mock_openai_client.chat.completions.create.side_effect = ai_provider.OpenAIError("Fail")

        with self.assertRaises(RuntimeError):
            await self.provider.call("test", "gpt-3.5-turbo")
        self.assertTrue(self.provider.circuit_breaker.is_open)

        await asyncio.sleep(0.02) # Wait for timeout

        self.mock_openai_client.chat.completions.create.side_effect = None # Clear error
        self.mock_openai_client.chat.completions.create.return_value = MagicMock(choices=[MagicMock(message=MagicMock(content="Reset response"))])
        
        result = await self.provider.call("test", "gpt-3.5-turbo")
        self.assertFalse(self.provider.circuit_breaker.is_open)
        self.assertFalse(self.provider.disabled)
        self.assertEqual(result["content"], "Reset response")

    async def test_reset_circuit_manual(self):
        self.provider.circuit_breaker.record_failure()
        self.provider.circuit_breaker.record_failure()
        self.assertTrue(self.provider.circuit_breaker.is_open)
        self.provider.reset_circuit()
        self.assertFalse(self.provider.circuit_breaker.is_open)
        self.assertFalse(self.provider.disabled)

    async def test_count_tokens(self):
        # Mock tiktoken's encode method directly
        self.mock_tiktoken_encoding.encode.return_value = [1, 2, 3, 4, 5] # Simulate 5 tokens
        tokens = await self.provider.count_tokens("This is a test sentence.", "gpt-4")
        self.assertEqual(tokens, 5)
        self.mock_tiktoken_encoding.encode.assert_called_once_with("This is a test sentence.")

    async def test_health_check_success(self):
        self.mock_openai_client.models.list.return_value = MagicMock(data=[]) # Simulate successful API call
        # Mock aiohttp.ClientSession.get for health check (provider uses self.client.base_url/models)
        # The provider uses client.base_url/models, so we need to mock the underlying httpx/aiohttp call
        # Since AsyncOpenAI client is mocked, its methods are mocked.
        # The health check uses self.client.base_url/models, so we mock self.mock_openai_client.models.list
        
        is_healthy = await self.provider.health_check()
        self.assertTrue(is_healthy)
        self.mock_openai_client.models.list.assert_awaited_once()
        ai_provider.latency_histogram.labels.assert_called_once_with(model='health_check')

    async def test_health_check_failure(self):
        self.mock_openai_client.models.list.side_effect = Exception("Health check API failed")
        is_healthy = await self.provider.health_check()
        self.assertFalse(is_healthy)
        self.mock_openai_client.models.list.assert_awaited_once()
        ai_provider.latency_histogram.labels.assert_called_once_with(model='health_check')

    async def test_scrub_prompt(self):
        sensitive_prompt = "My API key is sk-xyz123abc, and my email is user@example.com. My card is 1234-5678-9012-3456."
        scrubbed = self.provider._scrub_prompt(sensitive_prompt)
        self.assertIn("[REDACTED_API_KEY]", scrubbed)
        self.assertIn("[REDACTED_EMAIL]", scrubbed)
        self.assertIn("[REDACTED_CREDIT_CARD]", scrubbed)
        self.assertNotIn("sk-xyz123abc", scrubbed)
        self.assertNotIn("user@example.com", scrubbed)
        self.assertNotIn("1234-5678-9012-3456", scrubbed)

    async def test_register_custom_headers_and_endpoint(self):
        custom_headers = {"X-Custom-Header": "Value"}
        custom_endpoint = "http://custom.openai.com/v1"
        
        self.provider.register_custom_headers(custom_headers)
        self.provider.register_custom_endpoint(custom_endpoint)

        self.mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Custom response"))]
        )
        
        await self.provider.call("Test custom", "gpt-3.5-turbo")
        
        # Verify custom headers and base_url were passed to the client
        self.mock_openai_client.chat.completions.create.assert_awaited_once()
        args, kwargs = self.mock_openai_client.chat.completions.create.call_args
        self.assertIn('base_url', kwargs)
        self.assertEqual(kwargs['base_url'], custom_endpoint)
        self.assertIn('extra_headers', kwargs)
        self.assertEqual(kwargs['extra_headers'], custom_headers)

    async def test_register_model(self):
        self.assertNotIn("my-custom-model", self.provider.registered_models)
        self.provider.register_model("my-custom-model")
        self.assertIn("my-custom-model", self.provider.registered_models)
        
        # Test calling with unregistered model
        with self.assertRaisesRegex(ValueError, "Model unregistered"):
            await self.provider.call("test", "unregistered-model")


    @given(prompt=st.text(min_size=1, max_size=200, alphabet=st.characters(blacklist_categories=('Cs',))))
    async def test_call_non_stream_fuzz(self, prompt):
        """Fuzz: Should not crash for random prompt input (non-stream)."""
        self.mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=f"Echo: {prompt}"))]
        )
        
        try:
            result = await self.provider.call(prompt, "gpt-3.5-turbo")
            self.assertIsInstance(result, dict)
            self.assertIn("content", result)
            self.assertIn("model", result)
            # Ensure no sensitive data from prompt is in the result content if it were an echo service
            if self.provider._is_sensitive(prompt):
                self.assertNotIn(prompt, result["content"]) # Should be scrubbed if echoed
            
        except Exception as e:
            # Only allow specific expected errors (e.g., if prompt length exceeds model limits, etc.)
            # For this mock, we don't expect errors unless explicitly set.
            self.fail(f"Fuzz test failed with prompt '{prompt}' due to: {e}")

    @given(prompt=st.text(min_size=1, max_size=200, alphabet=st.characters(blacklist_categories=('Cs',))))
    async def test_call_stream_fuzz(self, prompt):
        """Fuzz: Should not crash for random prompt input (stream)."""
        async def mock_stream_response_fuzz():
            yield MagicMock(choices=[MagicMock(delta=MagicMock(content="chunk1"))])
            yield MagicMock(choices=[MagicMock(delta=MagicMock(content="chunk2"))])
        
        self.mock_openai_client.chat.completions.create.return_value = mock_stream_response_fuzz()

        try:
            gen = await self.provider.call(prompt, "gpt-4", stream=True)
            chunks = []
            async for chunk in gen:
                chunks.append(chunk)
            self.assertIsInstance(chunks, list)
            self.assertGreater(len(chunks), 0)
        except Exception as e:
            self.fail(f"Fuzz stream test failed with prompt '{prompt}' due to: {e}")

    def test_docstrings_and_types(self):
        """Type/Docs: Provider methods are typed and documented."""
        provider = ai_provider.get_provider()
        self.assertTrue(inspect.getdoc(provider.call))
        sig = inspect.signature(provider.call)
        self.assertIn("prompt", sig.parameters)
        self.assertIn("model", sig.parameters)
        self.assertIn("stream", sig.parameters)
        self.assertNotEqual(sig.return_annotation, inspect.Signature.empty) # Should be annotated

if __name__ == "__main__":
    unittest.main()

