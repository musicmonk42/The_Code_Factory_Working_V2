# test_e2e_integration.py
import unittest
import asyncio
import os
import sys
import tempfile
import shutil
import time
import hashlib
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from prometheus_client import Counter, Gauge, Histogram, REGISTRY
from opentelemetry import trace
import aiohttp

# Add parent directory to sys.path to import modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock external dependencies
sys.modules['dynaconf'] = MagicMock()
sys.modules['watchdog.observers'] = MagicMock()
sys.modules['watchdog.events'] = MagicMock()
sys.modules['opentelemetry.sdk.trace'] = MagicMock()
sys.modules['opentelemetry.sdk.trace.export'] = MagicMock()
sys.modules['opentelemetry.exporter.otlp.proto.grpc.trace_exporter'] = MagicMock()
sys.modules['aiohttp'] = MagicMock()
sys.modules['anthropic'] = MagicMock()
sys.modules['google.generativeai'] = MagicMock()
sys.modules['tiktoken'] = MagicMock()
sys.modules['tenacity'] = MagicMock()

# Import modules to test
from main.llm_plugin_manager import LLMPluginManager, settings, send_alert, tracer, HAS_OPENTELEMETRY
from main.ai_provider import OpenAIProvider
from main.claude_provider import ClaudeProvider
from main.gemini_provider import GeminiProvider
from main.grok_provider import GrokProvider
from main.local_provider import LocalProvider

class TestE2EIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Create temporary plugin directory
        self.temp_plugin_dir = Path(tempfile.mkdtemp())
        self.manifest_file = self.temp_plugin_dir / "plugin_hash_manifest.json"
        self.manifest = {}

        # Mock settings
        self.mock_settings = MagicMock()
        self.mock_settings.PLUGIN_DIR = str(self.temp_plugin_dir)
        self.mock_settings.AUTO_RELOAD = True
        self.mock_settings.ALERT_ENDPOINT = "http://mock-alert.com"
        self.mock_settings.OTLP_ENDPOINT = "http://mock-otel.com"
        self.mock_settings.HASH_MANIFEST = str(self.manifest_file)
        self.patch_settings = patch('main.llm_plugin_manager.settings', self.mock_settings)
        self.patch_settings.start()

        # Mock environment variables for API keys
        self.patch_env = patch.dict(os.environ, {
            'OPENAI_API_KEY': 'test_openai_key',
            'CLAUDE_API_KEY': 'test_claude_key',
            'GEMINI_API_KEY': 'test_gemini_key',
            'GROK_API_KEY': 'test_grok_key'
        })
        self.patch_env.start()

        # Mock external dependencies
        self.mock_observer = patch('main.llm_plugin_manager.Observer', return_value=MagicMock()).start()
        self.mock_event_handler = patch('main.llm_plugin_manager.PluginEventHandler', return_value=MagicMock()).start()
        self.mock_tracer = patch('main.llm_plugin_manager.tracer', MagicMock()).start()
        self.mock_send_alert = patch('main.llm_plugin_manager.send_alert', new_callable=AsyncMock).start()
        self.mock_aiohttp_session = patch('aiohttp.ClientSession', new_callable=AsyncMock).start()
        self.mock_tiktoken_encoding = MagicMock()
        self.mock_tiktoken_encoding.encode.return_value = [1, 2]  # Simulate 2 tokens
        self.patch_tiktoken = patch('main.grok_provider.tiktoken.get_encoding', return_value=self.mock_tiktoken_encoding).start()
        self.patch_gemini_configure = patch('main.gemini_provider.configure').start()
        self.mock_generative_model = MagicMock()
        self.patch_generative_model = patch('main.gemini_provider.GenerativeModel', return_value=self.mock_generative_model).start()

        # Clear Prometheus registry
        for collector in list(REGISTRY._collectors.values()):
            REGISTRY.unregister(collector)

        # Create mock provider files
        self._create_mock_providers()

        # Initialize manager
        self.manager = LLMPluginManager()

    def tearDown(self):
        # Clean up temp directory
        shutil.rmtree(self.temp_plugin_dir, ignore_errors=True)
        self.patch_settings.stop()
        self.patch_env.stop()
        self.mock_observer.stop()
        self.mock_event_handler.stop()
        self.mock_tracer.stop()
        self.mock_send_alert.stop()
        self.mock_aiohttp_session.stop()
        self.patch_tiktoken.stop()
        self.patch_gemini_configure.stop()
        self.patch_generative_model.stop()
        self.manager.close()

    def _create_mock_providers(self):
        """Create mock provider files with valid get_provider() functions."""
        providers = {
            'ai_provider.py': """
from main.ai_provider import OpenAIProvider
def get_provider():
    return OpenAIProvider()
""",
            'claude_provider.py': """
from main.claude_provider import ClaudeProvider
def get_provider():
    return ClaudeProvider()
""",
            'gemini_provider.py': """
from main.gemini_provider import GeminiProvider
def get_provider():
    return GeminiProvider()
""",
            'grok_provider.py': """
from main.grok_provider import GrokProvider
def get_provider():
    return GrokProvider()
""",
            'local_provider.py': """
from main.local_provider import LocalProvider
def get_provider():
    return LocalProvider()
"""
        }

        for filename, content in providers.items():
            file_path = self.temp_plugin_dir / filename
            file_path.write_text(content)
            with open(file_path, 'rb') as f:
                self.manifest[filename] = hashlib.sha256(f.read()).hexdigest()
        
        # Write manifest
        with open(self.manifest_file, 'w') as f:
            json.dump(self.manifest, f)

    async def test_e2e_plugin_loading_and_calls(self):
        """Test: Load all providers and make successful calls."""
        # Mock provider responses
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 200
        self.mock_aiohttp_session.return_value.__aenter__.return_value.json = AsyncMock(return_value={'choices': [{'message': {'content': 'Mock response'}}]})
        self.mock_aiohttp_session.return_value.__aenter__.return_value.text = AsyncMock(return_value='{"response": "Mock local response"}')
        self.mock_generative_model.generate_content_async.return_value = MagicMock(text="Mock Gemini response")
        self.mock_generative_model.count_tokens_async.return_value = MagicMock(total_tokens=10)

        # Wait for initial scan
        await self.manager._scan_and_load_plugins_on_init()

        # Verify all providers loaded
        providers = self.manager.list_providers()
        self.assertEqual(set(providers), {'openai', 'claude', 'gemini', 'grok', 'local'})

        # Test non-streaming calls
        for provider_name in providers:
            provider = self.manager.get_provider(provider_name)
            result = await provider.call("Test prompt", model=f"{provider_name}-model")
            self.assertIn("content", result)
            self.assertIn("model", result)
            if provider_name == 'local':
                self.assertEqual(result["content"], "Mock local response")
            elif provider_name == 'gemini':
                self.assertEqual(result["content"], "Mock Gemini response")
            else:
                self.assertEqual(result["content"], "Mock response")

        # Test streaming calls
        async def mock_stream_content():
            yield b'{"choices": [{"delta": {"content": "chunk1"}}]}\n'
            yield b'{"choices": [{"delta": {"content": "chunk2"}}]}\n'
        async def mock_local_stream():
            yield b'{"response": "chunk1"}\n'
            yield b'{"response": "chunk2"}\n'
        async def mock_gemini_stream():
            yield MagicMock(text="chunk1")
            yield MagicMock(text="chunk2")

        self.mock_aiohttp_session.return_value.__aenter__.return_value.content.__aiter__ = mock_stream_content
        self.mock_aiohttp_session.return_value.__aenter__.return_value.content.__aiter__ = mock_local_stream  # For local
        self.mock_generative_model.generate_content_async.return_value = mock_gemini_stream()

        for provider_name in providers:
            provider = self.manager.get_provider(provider_name)
            gen = await provider.call("Test stream", model=f"{provider_name}-model", stream=True)
            chunks = [chunk async for chunk in gen]
            self.assertEqual(chunks, ["chunk1", "chunk2"])

    async def test_e2e_integrity_check_failure(self):
        """Test: Plugin with invalid hash is rejected."""
        # Modify a plugin file to cause hash mismatch
        bad_plugin_path = self.temp_plugin_dir / 'ai_provider.py'
        with open(bad_plugin_path, 'a') as f:
            f.write("# Tampered content")

        # Update manifest with original hash to trigger failure
        with open(bad_plugin_path, 'rb') as f:
            original_content = f.read().replace(b"# Tampered content", b"")
            self.manifest['ai_provider.py'] = hashlib.sha256(original_content).hexdigest()
        with open(self.manifest_file, 'w') as f:
            json.dump(self.manifest, f)

        # Reload plugins
        await self.manager.reload()

        # Verify ai_provider was not loaded
        self.assertNotIn('openai', self.manager.list_providers())
        from main.llm_plugin_manager import PLUGIN_ERRORS
        PLUGIN_ERRORS.labels.assert_called_with(plugin_name='ai_provider', error_type='integrity_failure')
        self.mock_send_alert.assert_called()

    async def test_e2e_hot_reloading(self):
        """Test: Adding a new plugin triggers reload."""
        # Initial load
        await self.manager._scan_and_load_plugins_on_init()
        self.assertEqual(len(self.manager.list_providers()), 5)

        # Add new plugin
        new_plugin_path = self.temp_plugin_dir / 'new_provider.py'
        new_plugin_content = """
class NewProvider:
    def __init__(self):
        self.name = 'new'
    async def call(self, prompt, model, stream=False):
        return {'content': 'New response', 'model': model} if not stream else ['chunk1', 'chunk2']
    async def health_check(self):
        return True
def get_provider():
    return NewProvider()
"""
        new_plugin_path.write_text(new_plugin_content)
        with open(new_plugin_path, 'rb') as f:
            self.manifest['new_provider.py'] = hashlib.sha256(f.read()).hexdigest()
        with open(self.manifest_file, 'w') as f:
            json.dump(self.manifest, f)

        # Simulate file creation event
        mock_event = MagicMock(is_directory=False, src_path=str(new_plugin_path))
        self.mock_event_handler.return_value.on_created(mock_event)
        await asyncio.sleep(0.1)  # Allow watcher to process

        # Verify new provider loaded
        self.assertIn('new', self.manager.list_providers())
        provider = self.manager.get_provider('new')
        result = await provider.call("Test", "new-model")
        self.assertEqual(result['content'], "New response")

    async def test_e2e_circuit_breaker(self):
        """Test: Repeated failures trigger circuit breaker."""
        # Mock failure for OpenAI provider
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 500
        self.mock_aiohttp_session.return_value.__aenter__.return_value.json.side_effect = aiohttp.ClientResponseError(None, None, status=500)

        provider = self.manager.get_provider('openai')
        for _ in range(5):  # Failure threshold
            with self.assertRaises(Exception):
                await provider.call("Test", "gpt-3.5-turbo")
        
        # Verify circuit breaker is open
        self.assertFalse(await provider.health_check())
        from main.ai_provider import error_counter
        error_counter.labels.assert_called_with(model="gpt-3.5-turbo", error_type="ClientResponseError")

        # Reset circuit breaker and verify recovery
        provider.reset_circuit()
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 200
        self.mock_aiohttp_session.return_value.__aenter__.return_value.json.return_value = {'choices': [{'message': {'content': 'Recovered'}}]}
        result = await provider.call("Test", "gpt-3.5-turbo")
        self.assertEqual(result['content'], 'Recovered')

    async def test_e2e_metrics_and_tracing(self):
        """Test: Metrics and traces are recorded."""
        # Mock successful response
        self.mock_aiohttp_session.return_value.__aenter__.return_value.status = 200
        self.mock_aiohttp_session.return_value.__aenter__.return_value.json.return_value = {'choices': [{'message': {'content': 'Mock response'}}]}
        self.mock_generative_model.generate_content_async.return_value = MagicMock(text="Mock Gemini response")
        self.mock_generative_model.count_tokens_async.return_value = MagicMock(total_tokens=10)

        # Make a call
        provider = self.manager.get_provider('openai')
        await provider.call("Test", "gpt-3.5-turbo")

        # Verify metrics
        from main.ai_provider import call_counter, latency_histogram, cost_gauge
        call_counter.labels.assert_called_with(model="gpt-3.5-turbo", status='success')
        latency_histogram.labels.assert_called_with(model="gpt-3.5-turbo")
        cost_gauge.labels.assert_called_with(model="gpt-3.5-turbo")
        self.mock_tracer.start_as_current_span.assert_called()

if __name__ == '__main__':
    unittest.main()