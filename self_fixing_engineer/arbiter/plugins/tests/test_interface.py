# test_interface.py
import pytest
import asyncio
import datetime
import json
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any, List

from arbiter.plugins.multimodal.interface import (
    ProcessingResult,
    ImageProcessor,
    AudioProcessor,
    VideoProcessor,
    TextProcessor,
    AnalysisResultType,
    MultiModalAnalysisResult,
    ImageAnalysisResult,
    AudioAnalysisResult,
    VideoAnalysisResult,
    TextAnalysisResult,
    MultiModalPluginInterface,
    DummyMultiModalPlugin,
    MultiModalException,
    InvalidInputError,
    ConfigurationError,
    ProviderNotAvailableError,
    ProcessingError
)
from pydantic import ValidationError


class TestProcessingResult:
    """Test suite for ProcessingResult."""

    def test_processing_result_success(self):
        """Test successful ProcessingResult creation."""
        result = ProcessingResult(
            success=True,
            data={"key": "value"},
            summary="Test successful",
            operation_id="op123",
            model_confidence=0.95
        )
        
        assert result.success is True
        assert result.data == {"key": "value"}
        assert result.model_confidence == 0.95
        assert result.error is None

    def test_processing_result_failure(self):
        """Test failed ProcessingResult creation."""
        result = ProcessingResult(
            success=False,
            error="Processing failed",
            operation_id="op456"
        )
        
        assert result.success is False
        assert result.error == "Processing failed"
        assert result.data is None

    def test_processing_result_confidence_validation(self):
        """Test confidence score validation."""
        # Valid confidence
        result = ProcessingResult(
            success=True,
            operation_id="op789",
            model_confidence=0.5
        )
        assert result.model_confidence == 0.5
        
        # Invalid confidence (out of range)
        with pytest.raises(ValidationError):
            ProcessingResult(
                success=True,
                operation_id="op789",
                model_confidence=1.5
            )

    def test_processing_result_extra_fields_allowed(self):
        """Test that extra fields are allowed."""
        result = ProcessingResult(
            success=True,
            operation_id="op123",
            custom_field="custom_value"
        )
        assert result.custom_field == "custom_value"


class TestAnalysisResults:
    """Test suite for Analysis Result classes."""

    def test_image_analysis_result(self):
        """Test ImageAnalysisResult creation and methods."""
        result = ImageAnalysisResult(
            raw_data={"pixels": 1024},
            result_type=AnalysisResultType.IMAGE,
            success=True,
            confidence=0.9,
            model_id="vision-v1",
            classifications=[{"label": "cat", "score": 0.95}],
            objects=[{"box": [10, 20, 30, 40], "label": "cat", "confidence": 0.9}],
            ocr_text="Hello World",
            embedding=[0.1, 0.2, 0.3]
        )
        
        assert result.is_valid() is True
        assert "cat" in result.summary()
        assert "Objects detected: 1" in result.summary()
        assert "OCR text present" in result.summary()
        
        provenance = result.get_provenance_info()
        assert provenance["result_type"] == "image"
        assert provenance["model_id"] == "vision-v1"
        assert provenance["confidence"] == 0.9

    def test_audio_analysis_result(self):
        """Test AudioAnalysisResult creation and methods."""
        result = AudioAnalysisResult(
            raw_data="audio_data",
            result_type=AnalysisResultType.AUDIO,
            success=True,
            confidence=0.85,
            transcript="Hello, this is a test",
            speakers=[{"speaker": "Speaker1", "confidence": 0.9}],
            sentiment={"positive": 0.8, "negative": 0.1, "neutral": 0.1},
            language="en"
        )
        
        assert result.is_valid() is True
        assert "Transcript:" in result.summary()
        assert "Sentiment: positive" in result.summary()
        assert result.language == "en"

    def test_video_analysis_result(self):
        """Test VideoAnalysisResult creation and methods."""
        result = VideoAnalysisResult(
            raw_data={"frames": 100},
            result_type=AnalysisResultType.VIDEO,
            success=True,
            confidence=0.92,
            summary_transcript="Video summary text",
            scene_changes=[1.0, 5.0, 10.0],
            tracked_objects=[{"id": 1, "label": "person"}],
            actions=[{"action": "walking", "confidence": 0.9, "timestamp": (1.0, 3.0)}]
        )
        
        assert result.is_valid() is True
        assert "Scenes: 3" in result.summary()
        assert "Tracked Objects: 1" in result.summary()
        assert "Actions Detected: 1" in result.summary()

    def test_text_analysis_result(self):
        """Test TextAnalysisResult creation and methods."""
        result = TextAnalysisResult(
            raw_data="Original text",
            result_type=AnalysisResultType.TEXT,
            success=True,
            confidence=0.88,
            classification=[{"label": "technology", "score": 0.95}],
            sentiment={"positive": 0.7, "negative": 0.2, "neutral": 0.1},
            entities=[{"text": "John Doe", "type": "PERSON"}],
            summary_text="This is a summary",
            language="en"
        )
        
        assert result.is_valid() is True
        assert "Class: technology" in result.summary()
        assert "Sentiment: positive" in result.summary()
        assert "Entities: 1" in result.summary()

    def test_analysis_result_invalid_state(self):
        """Test is_valid() for failed results."""
        result = ImageAnalysisResult(
            raw_data={},
            result_type=AnalysisResultType.IMAGE,
            success=False,
            error_message="Processing failed"
        )
        
        assert result.is_valid() is False
        assert result.error_message == "Processing failed"

    def test_analysis_result_repr(self):
        """Test __repr__ method."""
        result = ImageAnalysisResult(
            raw_data={},
            result_type=AnalysisResultType.IMAGE,
            success=True,
            model_id="test-model",
            confidence=0.8
        )
        
        repr_str = repr(result)
        assert "ImageAnalysisResult" in repr_str
        assert "result_type='image'" in repr_str
        assert "success=True" in repr_str
        assert "model_id='test-model'" in repr_str


class TestDummyMultiModalPlugin:
    """Test suite for DummyMultiModalPlugin."""

    @pytest.fixture
    def plugin(self):
        """Create a DummyMultiModalPlugin instance."""
        config = {"model_id": "test-dummy-v1"}
        return DummyMultiModalPlugin(config)

    def test_plugin_initialization(self, plugin):
        """Test plugin initialization."""
        assert plugin.dummy_model_id == "test-dummy-v1"
        assert plugin.call_count == 0

    def test_analyze_image(self, plugin):
        """Test image analysis."""
        result = plugin.analyze_image(b"fake_image_bytes", description="test image")
        
        assert result.success is True
        assert result.result_type == AnalysisResultType.IMAGE
        assert result.confidence == 0.75
        assert len(result.classifications) > 0
        assert len(result.objects) > 0
        assert result.ocr_text == "dummy text from image"
        assert plugin.call_count == 1

    def test_analyze_image_invalid_input(self, plugin):
        """Test image analysis with invalid input."""
        with pytest.raises(ValueError, match="Invalid image_data"):
            plugin.analyze_image(None)
        
        with pytest.raises(ValueError, match="Invalid image_data"):
            plugin.analyze_image("/nonexistent/path.jpg")

    def test_analyze_audio(self, plugin):
        """Test audio analysis."""
        result = plugin.analyze_audio(b"fake_audio_bytes", description="test audio")
        
        assert result.success is True
        assert result.result_type == AnalysisResultType.AUDIO
        assert result.transcript == "This is a dummy transcript."
        assert result.language == "en"
        assert result.sentiment["positive"] == 0.7
        assert plugin.call_count == 1

    def test_analyze_video(self, plugin):
        """Test video analysis."""
        result = plugin.analyze_video(b"fake_video_bytes", description="test video")
        
        assert result.success is True
        assert result.result_type == AnalysisResultType.VIDEO
        assert len(result.scene_changes) == 3
        assert len(result.tracked_objects) == 1
        assert len(result.actions) == 1
        assert plugin.call_count == 1

    def test_analyze_text(self, plugin):
        """Test text analysis."""
        test_text = "This is a test text for analysis"
        result = plugin.analyze_text(test_text, description="test text")
        
        assert result.success is True
        assert result.result_type == AnalysisResultType.TEXT
        assert result.raw_data == test_text
        assert len(result.classification) > 0
        assert result.sentiment["positive"] == 0.9
        assert len(result.entities) > 0
        assert plugin.call_count == 1

    def test_analyze_text_invalid_input(self, plugin):
        """Test text analysis with invalid input."""
        with pytest.raises(ValueError, match="Invalid text_data"):
            plugin.analyze_text("")
        
        with pytest.raises(ValueError, match="Invalid text_data"):
            plugin.analyze_text(None)

    def test_supported_modalities(self, plugin):
        """Test supported_modalities method."""
        modalities = plugin.supported_modalities()
        
        assert "image" in modalities
        assert "audio" in modalities
        assert "video" in modalities
        assert "text" in modalities
        assert "generic" not in modalities

    def test_model_info(self, plugin):
        """Test model_info method."""
        # Process some data first
        plugin.analyze_image(b"test")
        plugin.analyze_text("test")
        
        info = plugin.model_info()
        
        assert info["name"] == "DummyMultiModalPlugin"
        assert info["model_id"] == "test-dummy-v1"
        assert info["call_count"] == 2
        assert "supported_features" in info

    def test_context_manager(self, plugin):
        """Test synchronous context manager."""
        with plugin as p:
            assert p is plugin
            p.analyze_image(b"test")
        
        # After exiting, shutdown should have been called

    @pytest.mark.asyncio
    async def test_async_context_manager(self, plugin):
        """Test asynchronous context manager."""
        async with plugin as p:
            assert p is plugin
        
        # After exiting, shutdown_async should have been called

    @pytest.mark.asyncio
    async def test_async_methods_not_implemented(self, plugin):
        """Test that async methods raise NotImplementedError."""
        with pytest.raises(NotImplementedError):
            await plugin.analyze_image_async(b"test")
        
        with pytest.raises(NotImplementedError):
            await plugin.analyze_audio_async(b"test")
        
        with pytest.raises(NotImplementedError):
            await plugin.analyze_video_async(b"test")
        
        with pytest.raises(NotImplementedError):
            await plugin.analyze_text_async("test")

    @patch('arbiter.plugins.multimodal.interface.time.sleep')
    def test_metrics_tracking(self, mock_sleep, plugin):
        """Test that metrics are properly tracked."""
        # Mock sleep to speed up tests
        mock_sleep.return_value = None
        
        # Successful request
        plugin.analyze_image(b"test")
        
        # Failed request
        with pytest.raises(ValueError):
            plugin.analyze_image(None)
        
        # Check metrics were called (mocked in the fixture)
        # In real scenario, you'd check the actual Prometheus metrics


class TestExceptionClasses:
    """Test suite for custom exception classes."""

    def test_multimodal_exception(self):
        """Test MultiModalException."""
        exc = MultiModalException("Base error")
        assert str(exc) == "Base error"
        assert isinstance(exc, Exception)

    def test_invalid_input_error(self):
        """Test InvalidInputError."""
        exc = InvalidInputError("Invalid input")
        assert str(exc) == "Invalid input"
        assert isinstance(exc, MultiModalException)

    def test_configuration_error(self):
        """Test ConfigurationError."""
        exc = ConfigurationError("Config error")
        assert str(exc) == "Config error"
        assert isinstance(exc, MultiModalException)

    def test_provider_not_available_error(self):
        """Test ProviderNotAvailableError."""
        exc = ProviderNotAvailableError("Provider unavailable")
        assert str(exc) == "Provider unavailable"
        assert isinstance(exc, MultiModalException)

    def test_processing_error(self):
        """Test ProcessingError."""
        exc = ProcessingError("Processing failed")
        assert str(exc) == "Processing failed"
        assert isinstance(exc, MultiModalException)


class TestAbstractInterfaces:
    """Test suite for abstract interfaces."""

    def test_image_processor_abstract(self):
        """Test that ImageProcessor is abstract."""
        with pytest.raises(TypeError):
            ImageProcessor()

    def test_audio_processor_abstract(self):
        """Test that AudioProcessor is abstract."""
        with pytest.raises(TypeError):
            AudioProcessor()

    def test_video_processor_abstract(self):
        """Test that VideoProcessor is abstract."""
        with pytest.raises(TypeError):
            VideoProcessor()

    def test_text_processor_abstract(self):
        """Test that TextProcessor is abstract."""
        with pytest.raises(TypeError):
            TextProcessor()

    def test_multimodal_plugin_interface_abstract(self):
        """Test that MultiModalPluginInterface is abstract."""
        with pytest.raises(TypeError):
            MultiModalPluginInterface()


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
    with patch('arbiter.plugins.multimodal.interface.get_or_create', get_or_create):
        yield


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])