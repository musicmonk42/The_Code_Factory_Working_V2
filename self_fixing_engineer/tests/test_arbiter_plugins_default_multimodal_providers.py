# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_default_multimodal_providers.py
import base64
from unittest.mock import Mock, patch

import pytest
from self_fixing_engineer.arbiter.plugins.multimodal.interface import (
    ConfigurationError,
    MultiModalException,
    ProviderNotAvailableError,
)
from self_fixing_engineer.arbiter.plugins.multimodal.providers.default_multimodal_providers import (
    DefaultAudioProcessor,
    DefaultAudioProcessorConfig,
    DefaultImageProcessor,
    DefaultImageProcessorConfig,
    DefaultTextProcessor,
    DefaultTextProcessorConfig,
    DefaultVideoProcessor,
    DefaultVideoProcessorConfig,
    PluginRegistry,
)
from pydantic import ValidationError


class TestPluginRegistry:
    """Test suite for PluginRegistry."""

    def setup_method(self):
        """Reset the registry before each test."""
        # Clear existing registrations
        PluginRegistry._processors = {"image": {}, "audio": {}, "video": {}, "text": {}}

    def test_register_processor_success(self):
        """Test successful processor registration."""

        class MockProcessor:
            def __init__(self, config):
                pass

        PluginRegistry.register_processor("image", "mock", MockProcessor)

        assert "mock" in PluginRegistry._processors["image"]
        assert PluginRegistry._processors["image"]["mock"] == MockProcessor

    def test_register_processor_invalid_modality(self):
        """Test registration with invalid modality."""

        class MockProcessor:
            pass

        with pytest.raises(ValueError, match="Unsupported modality"):
            PluginRegistry.register_processor("invalid", "mock", MockProcessor)

    def test_register_processor_duplicate(self):
        """Test registration of duplicate processor."""

        class MockProcessor:
            pass

        PluginRegistry.register_processor("image", "mock", MockProcessor)

        with pytest.raises(MultiModalException, match="already registered"):
            PluginRegistry.register_processor("image", "mock", MockProcessor)

    def test_unregister_processor_success(self):
        """Test successful processor unregistration."""

        class MockProcessor:
            def __init__(self, config):
                pass

        PluginRegistry.register_processor("audio", "mock", MockProcessor)
        PluginRegistry.unregister_processor("audio", "mock")

        assert "mock" not in PluginRegistry._processors["audio"]

    def test_unregister_processor_not_registered(self):
        """Test unregistration of non-existent processor."""
        with pytest.raises(MultiModalException, match="not registered"):
            PluginRegistry.unregister_processor("video", "nonexistent")

    def test_get_processor_success(self):
        """Test successful processor retrieval."""

        class MockProcessor:
            def __init__(self, config):
                self.config = config

        PluginRegistry.register_processor("text", "mock", MockProcessor)

        config = {"test": "value"}
        processor = PluginRegistry.get_processor("text", "mock", config)

        assert isinstance(processor, MockProcessor)
        assert processor.config == config

    def test_get_processor_not_registered(self):
        """Test retrieval of non-existent processor."""
        with pytest.raises(ProviderNotAvailableError, match="No provider"):
            PluginRegistry.get_processor("image", "nonexistent", {})

    def test_get_processor_config_validation_error(self):
        """Test processor retrieval with invalid config."""

        class MockProcessor:
            def __init__(self, config):
                raise ValidationError.from_exception_data("test", [])

        PluginRegistry.register_processor("audio", "mock", MockProcessor)

        with pytest.raises(ConfigurationError, match="Invalid configuration"):
            PluginRegistry.get_processor("audio", "mock", {})

    def test_get_supported_providers(self):
        """Test getting supported providers."""

        class MockProcessor1:
            pass

        class MockProcessor2:
            pass

        PluginRegistry.register_processor("image", "provider1", MockProcessor1)
        PluginRegistry.register_processor("image", "provider2", MockProcessor2)

        providers = PluginRegistry.get_supported_providers("image")

        assert "provider1" in providers
        assert "provider2" in providers
        assert len(providers) == 2

    def test_get_supported_providers_invalid_modality(self):
        """Test getting providers for invalid modality."""
        with pytest.raises(ValueError, match="Unsupported modality"):
            PluginRegistry.get_supported_providers("invalid")


class TestConfigSchemas:
    """Test suite for Pydantic configuration schemas."""

    def test_default_image_processor_config_valid(self):
        """Test valid DefaultImageProcessorConfig."""
        config = DefaultImageProcessorConfig(
            mock_min_latency_ms=20, mock_max_latency_ms=200, max_size_mb=15
        )

        assert config.mock_min_latency_ms == 20
        assert config.mock_max_latency_ms == 200
        assert config.max_size_mb == 15

    def test_default_image_processor_config_defaults(self):
        """Test DefaultImageProcessorConfig with defaults."""
        config = DefaultImageProcessorConfig()

        assert config.mock_min_latency_ms == 10
        assert config.mock_max_latency_ms == 100
        assert config.max_size_mb == 10

    def test_default_image_processor_config_invalid(self):
        """Test invalid DefaultImageProcessorConfig."""
        with pytest.raises(ValidationError):
            DefaultImageProcessorConfig(mock_min_latency_ms=-1)

        with pytest.raises(ValidationError):
            DefaultImageProcessorConfig(max_size_mb=0)

    def test_default_audio_processor_config(self):
        """Test DefaultAudioProcessorConfig."""
        config = DefaultAudioProcessorConfig(max_size_mb=30)
        assert config.max_size_mb == 30

    def test_default_video_processor_config(self):
        """Test DefaultVideoProcessorConfig."""
        config = DefaultVideoProcessorConfig(max_size_mb=200)
        assert config.max_size_mb == 200

    def test_default_text_processor_config(self):
        """Test DefaultTextProcessorConfig."""
        config = DefaultTextProcessorConfig(max_length=5000)
        assert config.max_length == 5000


class TestDefaultImageProcessor:
    """Test suite for DefaultImageProcessor."""

    @pytest.fixture
    def processor(self):
        """Create a DefaultImageProcessor instance."""
        config = {"mock_min_latency_ms": 1, "mock_max_latency_ms": 2, "max_size_mb": 10}
        with patch(
            "self_fixing_engineer.arbiter.plugins.multimodal.providers.default_multimodal_providers.get_or_create",
            get_or_create,
        ):
            return DefaultImageProcessor(config)

    @pytest.mark.asyncio
    async def test_process_success_with_bytes(self, processor):
        """Test successful processing with byte data."""
        # JPEG magic bytes
        image_data = b"\xff\xd8\xff\xe0" + b"fake_image_data"

        result = await processor.process(image_data)

        assert result.success is True
        assert "ocr_text" in result.data
        assert "caption" in result.data
        assert result.data["size_bytes"] == len(image_data)
        assert result.model_confidence >= 0.7
        assert result.model_confidence <= 0.95

    @pytest.mark.asyncio
    async def test_process_success_with_base64(self, processor):
        """Test successful processing with base64 encoded data."""
        image_bytes = b"\x89PNG" + b"fake_png_data"
        image_b64 = base64.b64encode(image_bytes).decode("ascii")

        result = await processor.process(image_b64)

        assert result.success is True
        assert "ocr_text" in result.data

    @pytest.mark.asyncio
    async def test_process_invalid_none_input(self, processor):
        """Test processing with None input."""
        result = await processor.process(None)

        assert result.success is False
        assert "Invalid image_data" in result.error

    @pytest.mark.asyncio
    async def test_process_invalid_file_path(self, processor):
        """Test processing with invalid file path."""
        result = await processor.process("/nonexistent/file.jpg")

        assert result.success is False
        assert "Invalid image_data" in result.error

    @pytest.mark.asyncio
    async def test_process_exceeds_max_size(self, processor):
        """Test processing with data exceeding max size."""
        # Create data larger than max_size_mb (10MB)
        large_data = b"\xff\xd8" + b"x" * (11 * 1024 * 1024)

        result = await processor.process(large_data)

        assert result.success is False
        assert "exceeds max size" in result.error

    @pytest.mark.asyncio
    async def test_process_unsupported_format(self, processor):
        """Test processing with unsupported image format."""
        # Data that doesn't start with JPEG or PNG magic bytes
        invalid_data = b"INVALID" + b"fake_data"

        result = await processor.process(invalid_data)

        assert result.success is False
        assert "Unsupported image format" in result.error

    @pytest.mark.asyncio
    async def test_process_with_operation_id(self, processor):
        """Test processing with custom operation ID."""
        op_id = "custom-op-123"
        image_data = b"\x89PNG" + b"data"

        result = await processor.process(image_data, operation_id=op_id)

        assert result.operation_id == op_id

    @pytest.mark.asyncio
    async def test_health_check(self, processor):
        """Test health check method."""
        is_healthy = await processor.health_check()
        assert is_healthy is True


class TestDefaultAudioProcessor:
    """Test suite for DefaultAudioProcessor."""

    @pytest.fixture
    def processor(self):
        """Create a DefaultAudioProcessor instance."""
        config = {"mock_min_latency_ms": 1, "mock_max_latency_ms": 2, "max_size_mb": 20}
        with patch(
            "self_fixing_engineer.arbiter.plugins.multimodal.providers.default_multimodal_providers.get_or_create",
            get_or_create,
        ):
            return DefaultAudioProcessor(config)

    @pytest.mark.asyncio
    async def test_process_success_with_wav(self, processor):
        """Test successful processing with WAV data."""
        # WAV file starts with "RIFF"
        audio_data = b"RIFF" + b"fake_wav_data"

        result = await processor.process(audio_data)

        assert result.success is True
        assert "transcription" in result.data
        assert result.data["size_bytes"] == len(audio_data)

    @pytest.mark.asyncio
    async def test_process_success_with_mp3(self, processor):
        """Test successful processing with MP3 data."""
        # MP3 file starts with "ID3"
        audio_data = b"ID3" + b"fake_mp3_data"

        result = await processor.process(audio_data)

        assert result.success is True
        assert "transcription" in result.data

    @pytest.mark.asyncio
    async def test_process_unsupported_format(self, processor):
        """Test processing with unsupported audio format."""
        invalid_data = b"INVALID" + b"fake_data"

        result = await processor.process(invalid_data)

        assert result.success is False
        assert "Unsupported audio format" in result.error

    @pytest.mark.asyncio
    async def test_health_check(self, processor):
        """Test health check method."""
        is_healthy = await processor.health_check()
        assert is_healthy is True


class TestDefaultVideoProcessor:
    """Test suite for DefaultVideoProcessor."""

    @pytest.fixture
    def processor(self):
        """Create a DefaultVideoProcessor instance."""
        config = {
            "mock_min_latency_ms": 1,
            "mock_max_latency_ms": 2,
            "max_size_mb": 100,
        }
        with patch(
            "self_fixing_engineer.arbiter.plugins.multimodal.providers.default_multimodal_providers.get_or_create",
            get_or_create,
        ):
            return DefaultVideoProcessor(config)

    @pytest.mark.asyncio
    async def test_process_success_with_mp4(self, processor):
        """Test successful processing with MP4 data."""
        # MP4 contains "ftyp" in header
        video_data = b"xxxx" + b"ftyp" + b"fake_mp4_data"

        result = await processor.process(video_data)

        assert result.success is True
        assert "summary" in result.data
        assert "audio_transcription" in result.data

    @pytest.mark.asyncio
    async def test_process_success_with_avi(self, processor):
        """Test successful processing with AVI data."""
        # AVI starts with "AVI"
        video_data = b"AVI" + b"fake_avi_data"

        result = await processor.process(video_data)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_process_unsupported_format(self, processor):
        """Test processing with unsupported video format."""
        invalid_data = b"x" * 200  # No format markers

        result = await processor.process(invalid_data)

        assert result.success is False
        assert "Unsupported video format" in result.error

    @pytest.mark.asyncio
    async def test_health_check(self, processor):
        """Test health check method."""
        is_healthy = await processor.health_check()
        assert is_healthy is True


class TestDefaultTextProcessor:
    """Test suite for DefaultTextProcessor."""

    @pytest.fixture
    def processor(self):
        """Create a DefaultTextProcessor instance."""
        config = {
            "mock_min_latency_ms": 1,
            "mock_max_latency_ms": 2,
            "max_length": 1000,
        }
        with patch(
            "self_fixing_engineer.arbiter.plugins.multimodal.providers.default_multimodal_providers.get_or_create",
            get_or_create,
        ):
            return DefaultTextProcessor(config)

    @pytest.mark.asyncio
    async def test_process_success(self, processor):
        """Test successful text processing."""
        text_data = "This is a test text."

        result = await processor.process(text_data)

        assert result.success is True
        assert "processed_text" in result.data
        assert text_data.upper() in result.data["processed_text"]

    @pytest.mark.asyncio
    async def test_process_empty_text(self, processor):
        """Test processing with empty text."""
        result = await processor.process("")

        assert result.success is False
        assert "Invalid or empty text" in result.error

    @pytest.mark.asyncio
    async def test_process_invalid_type(self, processor):
        """Test processing with non-string input."""
        result = await processor.process(None)

        assert result.success is False
        assert "Invalid or empty text" in result.error

    @pytest.mark.asyncio
    async def test_process_exceeds_max_length(self, processor):
        """Test processing with text exceeding max length."""
        long_text = "x" * 1001  # Exceeds max_length of 1000

        result = await processor.process(long_text)

        assert result.success is False
        assert "exceeds max length" in result.error

    @pytest.mark.asyncio
    async def test_health_check(self, processor):
        """Test health check method."""
        is_healthy = await processor.health_check()
        assert is_healthy is True


# Helper function to mock get_or_create for metrics
def get_or_create(metric):
    """Mock function for get_or_create metrics."""
    mock_metric = Mock()
    mock_metric.labels.return_value = mock_metric
    mock_metric.inc.return_value = None
    mock_metric.observe.return_value = None
    return mock_metric


# Patch the get_or_create function in the module
@pytest.fixture(autouse=True)
def mock_metrics():
    """Auto-use fixture to mock metrics."""
    with patch(
        "self_fixing_engineer.arbiter.plugins.multimodal.providers.default_multimodal_providers.get_or_create",
        get_or_create,
    ):
        yield


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
