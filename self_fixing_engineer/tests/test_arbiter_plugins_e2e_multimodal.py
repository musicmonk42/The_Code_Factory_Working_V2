# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_e2e_multimodal.py
import asyncio
import os
import tempfile
from unittest.mock import Mock, patch

import pytest
from self_fixing_engineer.arbiter.plugins.multi_modal_config import MultiModalConfig

# Import all the components we need to test
from self_fixing_engineer.arbiter.plugins.multi_modal_plugin import MultiModalPlugin
from self_fixing_engineer.arbiter.plugins.multimodal.interface import DummyMultiModalPlugin, ProcessingResult
from self_fixing_engineer.arbiter.plugins.multimodal.providers.default_multimodal_providers import (
    DefaultAudioProcessor,
    DefaultImageProcessor,
    DefaultTextProcessor,
    DefaultVideoProcessor,
    PluginRegistry,
)

# Ensure providers are registered
if not PluginRegistry._processors.get("image", {}).get("default"):
    PluginRegistry.register_processor("image", "default", DefaultImageProcessor)
    PluginRegistry.register_processor("audio", "default", DefaultAudioProcessor)
    PluginRegistry.register_processor("video", "default", DefaultVideoProcessor)
    PluginRegistry.register_processor("text", "default", DefaultTextProcessor)

from self_fixing_engineer.arbiter.plugins.multimodal.providers.default_multimodal_providers import (
    DefaultAudioProcessor,
    DefaultImageProcessor,
    DefaultTextProcessor,
    DefaultVideoProcessor,
    PluginRegistry,
)


class TestE2EMultiModalSystem:
    """End-to-end tests for the complete multimodal system."""

    @pytest.fixture
    async def plugin_with_config(self):
        """Create a fully configured MultiModalPlugin."""
        config = {
            "image_processing": {
                "enabled": True,
                "default_provider": "default",
                "provider_config": {
                    "mock_min_latency_ms": 1,
                    "mock_max_latency_ms": 5,
                    "max_size_mb": 10,
                },
            },
            "audio_processing": {
                "enabled": True,
                "default_provider": "default",
                "provider_config": {
                    "mock_min_latency_ms": 1,
                    "mock_max_latency_ms": 5,
                    "max_size_mb": 20,
                },
            },
            "video_processing": {
                "enabled": True,
                "default_provider": "default",
                "provider_config": {
                    "mock_min_latency_ms": 1,
                    "mock_max_latency_ms": 5,
                    "max_size_mb": 100,
                },
            },
            "text_processing": {
                "enabled": True,
                "default_provider": "default",
                "provider_config": {
                    "mock_min_latency_ms": 1,
                    "mock_max_latency_ms": 5,
                    "max_length": 10000,
                },
            },
            "security_config": {
                "sandbox_enabled": False,
                "mask_pii_in_logs": True,
                "pii_patterns": {
                    "email": r"\b[\w\.-]+@[\w\.-]+\.\w+\b",
                    "phone": r"\d{3}-\d{3}-\d{4}",
                },
                "input_validation_rules": {
                    "image": {"max_size": 10 * 1024 * 1024},
                    "text": {"max_length": 5000},
                },
                "output_validation_rules": {},
            },
            "cache_config": {
                "enabled": False,  # Disable for E2E tests
                "type": "redis",
                "host": "localhost",
                "port": 6379,
                "ttl_seconds": 60,
            },
            "metrics_config": {
                "enabled": False,  # Disable for E2E tests
                "exporter_port": 9090,
            },
            "circuit_breaker_config": {
                "enabled": True,
                "threshold": 3,
                "timeout_seconds": 1,
                "modalities": ["image", "audio", "video", "text"],
            },
            "audit_log_config": {
                "enabled": True,
                "log_level": "INFO",
                "destination": "console",
            },
            "user_id_for_auditing": "test_user_e2e",
        }

        plugin = MultiModalPlugin(config)
        await plugin.initialize()
        yield plugin
        await plugin.stop()

    @pytest.mark.asyncio
    async def test_complete_workflow_all_modalities(self, plugin_with_config):
        """Test processing all modalities in sequence."""
        # Test Image Processing
        image_data = b"\xff\xd8" + b"fake_jpeg_image_data"
        image_result = await plugin_with_config.process_image(image_data)
        assert image_result.success is True
        assert "ocr_text" in image_result.data
        assert image_result.operation_id

        # Test Audio Processing
        audio_data = b"RIFF" + b"fake_wav_audio_data"
        audio_result = await plugin_with_config.process_audio(audio_data)
        assert audio_result.success is True
        assert "transcription" in audio_result.data

        # Test Video Processing
        video_data = b"xxxx" + b"ftyp" + b"fake_mp4_video_data"
        video_result = await plugin_with_config.process_video(video_data)
        assert video_result.success is True
        assert "summary" in video_result.data

        # Test Text Processing
        text_data = "This is a test text for E2E processing."
        text_result = await plugin_with_config.process_text(text_data)
        assert text_result.success is True
        assert "processed_text" in text_result.data

    @pytest.mark.asyncio
    async def test_pii_masking_e2e(self, plugin_with_config):
        """Test PII masking across the system."""
        # Text with PII
        text_with_pii = "Contact john@example.com or call 555-123-4567"

        with patch("self_fixing_engineer.arbiter.plugins.multi_modal_plugin.logger"):
            result = await plugin_with_config.process_text(text_with_pii)
            assert result.success is True
            # PII should be masked in logs but not necessarily in results

    @pytest.mark.asyncio
    async def test_circuit_breaker_e2e(self, plugin_with_config):
        """Test circuit breaker functionality end-to-end."""
        # Force failures to trigger circuit breaker
        with patch.object(
            plugin_with_config.text_processor,
            "process",
            side_effect=Exception("Simulated failure"),
        ):
            # Fail 3 times to open circuit breaker
            for _ in range(3):
                result = await plugin_with_config.process_text("test")
                assert result.success is False

            # Circuit should be open now
            assert plugin_with_config._circuit_breaker_states["text"] == "open"

            # Next call should fail immediately
            result = await plugin_with_config.process_text("test")
            assert "Circuit breaker" in result.error

            # Wait for timeout
            await asyncio.sleep(1.5)

            # Circuit should transition to half-open
            with patch.object(
                plugin_with_config.text_processor,
                "process",
                return_value=ProcessingResult(
                    success=True, data={}, operation_id="test"
                ),
            ):
                result = await plugin_with_config.process_text("test")
                # Should succeed and close the circuit
                assert result.success is True
                assert plugin_with_config._circuit_breaker_states["text"] == "closed"

    @pytest.mark.asyncio
    async def test_hooks_e2e(self, plugin_with_config):
        """Test pre and post processing hooks."""
        pre_hook_called = False
        post_hook_called = False

        async def pre_hook(data):
            nonlocal pre_hook_called
            pre_hook_called = True
            return data + "_modified"

        async def post_hook(data):
            nonlocal post_hook_called
            post_hook_called = True
            data["hooked"] = True
            return data

        plugin_with_config.add_hook("text", pre_hook, "pre")
        plugin_with_config.add_hook("text", post_hook, "post")

        result = await plugin_with_config.process_text("test")

        assert pre_hook_called
        assert post_hook_called
        assert result.success is True

    @pytest.mark.asyncio
    async def test_parallel_processing(self, plugin_with_config):
        """Test parallel processing of multiple modalities."""
        image_data = b"\x89PNG" + b"fake_png"
        audio_data = b"ID3" + b"fake_mp3"
        text_data = "Parallel test"

        # Process all modalities in parallel
        results = await asyncio.gather(
            plugin_with_config.process_image(image_data),
            plugin_with_config.process_audio(audio_data),
            plugin_with_config.process_text(text_data),
        )

        assert all(r.success for r in results)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_config_from_yaml_e2e(self):
        """Test loading configuration from YAML file."""
        yaml_content = """
        image_processing:
          enabled: true
          default_provider: default
          provider_config:
            mock_min_latency_ms: 1
            mock_max_latency_ms: 5
            max_size_mb: 10
        text_processing:
          enabled: true
          default_provider: default
          provider_config:
            mock_min_latency_ms: 1
            mock_max_latency_ms: 5
            max_length: 1000
        security_config:
          sandbox_enabled: false
          mask_pii_in_logs: true
        cache_config:
          enabled: false
        metrics_config:
          enabled: false
        circuit_breaker_config:
          enabled: true
          threshold: 3
          timeout_seconds: 1
          modalities: ["image", "text"]
        audit_log_config:
          enabled: true
          log_level: INFO
          destination: console
        """

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            config = MultiModalConfig.load_config(temp_path)
            plugin = MultiModalPlugin(config.model_dump())
            await plugin.initialize()

            # Test functionality
            result = await plugin.process_text("YAML config test")
            assert result.success is True

            await plugin.stop()
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_custom_provider_registration_e2e(self):
        """Test registering and using a custom provider."""

        # Create a custom processor
        class CustomTextProcessor:
            def __init__(self, config):
                self.config = config

            async def process(self, text_data, **kwargs):
                return ProcessingResult(
                    success=True,
                    data={"custom_result": f"CUSTOM: {text_data}"},
                    summary="Custom processing complete",
                    operation_id="custom-123",
                )

        # Register the custom processor
        PluginRegistry.register_processor("text", "custom", CustomTextProcessor)

        # Create plugin with custom provider
        config = {
            "text_processing": {
                "enabled": True,
                "default_provider": "custom",
                "provider_config": {},
            },
            "cache_config": {"enabled": False},
            "metrics_config": {"enabled": False},
            "circuit_breaker_config": {"enabled": False, "modalities": []},
            "audit_log_config": {
                "enabled": False,
                "log_level": "INFO",
                "destination": "console",
            },
        }

        plugin = MultiModalPlugin(config)
        await plugin.initialize()

        result = await plugin.process_text("test custom")
        assert result.success is True
        assert "custom_result" in result.data
        assert result.data["custom_result"] == "CUSTOM: test custom"

        await plugin.stop()

        # Clean up
        PluginRegistry.unregister_processor("text", "custom")

    @pytest.mark.asyncio
    async def test_error_propagation_e2e(self, plugin_with_config):
        """Test error propagation through the system."""
        # Test with invalid data
        result = await plugin_with_config.process_image(None)
        assert result.success is False
        assert "Invalid" in result.error

        # Test with data exceeding size limits
        huge_text = "x" * 10000  # Exceeds configured max_length
        result = await plugin_with_config.process_text(huge_text)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_context_manager_e2e(self):
        """Test plugin as context manager."""
        config = {
            "image_processing": {"enabled": True},
            "cache_config": {"enabled": False},
            "metrics_config": {"enabled": False},
            "circuit_breaker_config": {"enabled": False, "modalities": []},
            "audit_log_config": {
                "enabled": False,
                "log_level": "INFO",
                "destination": "console",
            },
        }

        async with MultiModalPlugin(config) as plugin:
            result = await plugin.process_image(b"\xff\xd8" + b"test")
            assert result.success is True

    @pytest.mark.asyncio
    async def test_health_check_e2e(self, plugin_with_config):
        """Test health check functionality."""
        is_healthy = await plugin_with_config.health_check()
        assert is_healthy is True

    @pytest.mark.asyncio
    async def test_capabilities_e2e(self, plugin_with_config):
        """Test getting plugin capabilities."""
        capabilities = await plugin_with_config.get_capabilities()
        assert "multimodal_processing" in capabilities
        assert "image" in capabilities
        assert "audio" in capabilities
        assert "video" in capabilities
        assert "text" in capabilities

    @pytest.mark.asyncio
    async def test_provider_switching_e2e(self, plugin_with_config):
        """Test switching providers at runtime."""

        # Register a new provider
        class AlternativeTextProcessor:
            def __init__(self, config):
                pass

            async def process(self, text_data, **kwargs):
                return ProcessingResult(
                    success=True, data={"alt": "alternative"}, operation_id="alt-123"
                )

        PluginRegistry.register_processor(
            "text", "alternative", AlternativeTextProcessor
        )

        # Switch to the alternative provider
        plugin_with_config.set_default_provider("text", "alternative")

        result = await plugin_with_config.process_text("test")
        assert result.success is True
        assert result.data.get("alt") == "alternative"

        # Clean up
        PluginRegistry.unregister_processor("text", "alternative")

    @pytest.mark.asyncio
    async def test_model_version_tracking_e2e(self, plugin_with_config):
        """Test model version tracking."""
        plugin_with_config.update_model_version("image", "v2.0")
        plugin_with_config.update_model_version("text", "v1.5")

        assert plugin_with_config.config.current_model_version["image"] == "v2.0"
        assert plugin_with_config.config.current_model_version["text"] == "v1.5"

    @pytest.mark.asyncio
    async def test_complete_dummy_plugin_e2e(self):
        """Test the DummyMultiModalPlugin end-to-end."""
        config = {"model_id": "e2e-dummy-test"}

        async with DummyMultiModalPlugin(config) as plugin:
            # Test all modalities
            img_result = plugin.analyze_image(b"test_image")
            assert img_result.success is True
            assert img_result.result_type.value == "image"

            audio_result = plugin.analyze_audio(b"test_audio")
            assert audio_result.success is True
            assert audio_result.result_type.value == "audio"

            video_result = plugin.analyze_video(b"test_video")
            assert video_result.success is True
            assert video_result.result_type.value == "video"

            text_result = plugin.analyze_text("test text")
            assert text_result.success is True
            assert text_result.result_type.value == "text"

            # Check model info
            info = plugin.model_info()
            assert info["model_id"] == "e2e-dummy-test"
            assert info["call_count"] == 4  # One for each modality


# Helper to mock metrics
def get_or_create(metric):
    mock_metric = Mock()
    mock_metric.labels.return_value = mock_metric
    mock_metric.inc.return_value = None
    mock_metric.observe.return_value = None
    return mock_metric


@pytest.fixture(autouse=True)
def mock_metrics():
    """Mock metrics for all tests."""
    with (
        patch(
            "self_fixing_engineer.arbiter.plugins.multi_modal_plugin.get_or_create_counter", get_or_create
        ),
        patch(
            "self_fixing_engineer.arbiter.plugins.multi_modal_plugin.get_or_create_histogram", get_or_create
        ),
        patch(
            "self_fixing_engineer.arbiter.plugins.multimodal.providers.default_multimodal_providers.get_or_create",
            get_or_create,
        ),
        patch("self_fixing_engineer.arbiter.plugins.multimodal.interface.get_or_create", get_or_create),
    ):
        yield


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
