# D:\SFE\self_fixing_engineer\arbiter\plugins\multimodal\interface.py
"""
interface.py — Universal Multimodal Plugin Interface

Bar-setting, production-grade interface for modular, AI-driven multimodal analysis plugins.
Defines the contract for any plugin supporting images, audio, video, and more.
Extensible, typed, robust. For the Self-Fixing Engineer platform.
"""

import asyncio  # For the async examples in the main block
import datetime
import json  # Added for json.dumps in main for better output
import os
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Generic, List, Optional, Tuple, TypeVar, Union

# Using Pydantic for robust data validation and serialization.
# This makes results automatically JSON-serializable and validates structure.
try:
    from prometheus_client import Counter, Histogram
    from pydantic import BaseModel, ConfigDict, Field, ValidationError
except ImportError:
    raise ImportError(
        "Pydantic and prometheus_client are required. Please install them with 'pip install pydantic prometheus_client'."
    )

# Define a generic type variable for raw_data to allow for flexible data types in results
T = TypeVar("T")


# Helper function for metrics
def get_or_create(metric):
    """Helper function to wrap metric creation."""
    return metric


# --- Exception Classes (MOVED HERE from where they were conceptually defined in multi_modal_plugin.py) ---
class MultiModalException(Exception):
    """Base exception for all multimodal plugin errors."""

    pass


class InvalidInputError(MultiModalException):
    """Raised when input data is invalid."""

    pass


class ConfigurationError(MultiModalException):
    """Raised when plugin configuration is invalid."""

    pass


class ProviderNotAvailableError(MultiModalException):
    """Raised when a requested provider is not available or configured."""

    pass


class ProcessingError(MultiModalException):
    """Raised when a generic processing error occurs within a processor."""

    pass


# --- END Exception Classes ---


class ProcessingResult(BaseModel, Generic[T]):
    """
    Standardized result object for all multimodal processing operations.
    """

    success: bool = Field(
        ..., description="True if the operation was successful, False otherwise."
    )
    error: Optional[str] = Field(
        None, description="Error message if the operation failed."
    )
    data: Optional[T] = Field(
        None, description="The processed data or results, type-hinted by Generic[T]."
    )
    summary: Optional[str] = Field(
        None, description="A human-readable summary of the processing result."
    )
    operation_id: str = Field(
        ..., description="A unique identifier for this specific processing operation."
    )
    model_confidence: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Confidence score of the model's output (0.0 to 1.0), if applicable.",
    )

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        extra="allow"
    )


class ImageProcessor(ABC):
    @abstractmethod
    async def process(self, image_data: Any) -> ProcessingResult:
        """
        Processes image data.
        """
        pass


class AudioProcessor(ABC):
    @abstractmethod
    async def process(self, audio_data: Any) -> ProcessingResult:
        """
        Processes audio data.
        """
        pass


class VideoProcessor(ABC):
    @abstractmethod
    async def process(self, video_data: Any) -> ProcessingResult:
        """
        Processes video data.
        """
        pass


class TextProcessor(ABC):
    @abstractmethod
    async def process(self, text_data: str) -> ProcessingResult:
        """
        Processes text data.
        """
        pass


class AnalysisResultType(str, Enum):
    """Enum for standardizing analysis result types."""

    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    TEXT = "text"
    GENERIC = "generic"  # For future expansion or non-specific results


class MultiModalAnalysisResult(BaseModel, Generic[T], ABC):
    """
    Base class for results of multimodal analysis.
    Uses Pydantic for data validation, serialization, and robust structure.
    Includes raw data, standardized metadata, and export/summary methods.
    This class is abstract and should not be instantiated directly.
    """

    raw_data: T = Field(
        ...,
        description="The raw result data (e.g., prediction, embedding, transcript). Type-hinted by Generic[T].",
    )
    meta: Dict[str, Any] = Field(
        default_factory=dict,
        description="Standardized metadata dictionary (model, timestamp, confidence, provenance, etc).",
    )
    result_type: AnalysisResultType = Field(
        ...,
        description="The specific type of analysis result (e.g., 'image', 'audio', 'text').",
    )
    success: bool = Field(True, description="Indicates if the analysis was successful.")
    error_message: Optional[str] = Field(
        None, description="Detailed error message if analysis failed."
    )
    confidence: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Overall confidence score of the analysis result, if applicable.",
    )
    model_id: Optional[str] = Field(
        None, description="Identifier of the model used for analysis."
    )
    timestamp_utc: datetime.datetime = Field(
        default_factory=datetime.datetime.utcnow,
        description="UTC timestamp of when the analysis was performed.",
    )
    # Add fields for provenance, audit trail, PII redaction status, etc.
    data_provenance: Optional[Dict[str, Any]] = Field(
        None,
        description="Information about the source data (e.g., file hash, origin URL, capture device).",
    )
    audit_id: Optional[str] = Field(
        None,
        description="Unique identifier for the audit trail entry associated with this analysis.",
    )

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        extra="forbid"
    )

    @abstractmethod
    def summary(self) -> str:
        """
        Provides a short, human-readable summary of the analysis result.
        Implementations should prioritize key insights relevant to the modality.
        """
        pass

    def is_valid(self) -> bool:
        """
        Checks if the result passes basic validity and success criteria.
        Returns False if 'success' is False or an 'error_message' is present.
        Can be extended by child classes for modality-specific validation.
        """
        return self.success and not self.error_message

    def get_provenance_info(self) -> Dict[str, Any]:
        """
        Extracts key provenance and audit-related information from the result.
        Useful for logging and audit trails.
        """
        return {
            "result_type": self.result_type.value,
            "model_id": self.model_id,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "confidence": self.confidence,
            "data_provenance": self.data_provenance,
            "audit_id": self.audit_id,
            "success": self.success,
            "error_message": self.error_message,
        }

    def __str__(self) -> str:
        return self.summary()

    def __repr__(self) -> str:  # Added __repr__ for better debug/CLI introspection
        return f"{self.__class__.__name__}(result_type={self.result_type.value!r}, success={self.success}, model_id={self.model_id!r}, confidence={self.confidence})"


# Concrete Analysis Result Implementations
class ImageAnalysisResult(
    MultiModalAnalysisResult[Union[Dict[str, Any], List[Dict[str, Any]]]]
):
    """
    Standardized result for image analysis, including common features like
    classification, object detection, segmentation masks, OCR text, and embeddings.
    """

    result_type: AnalysisResultType = AnalysisResultType.IMAGE
    classifications: Optional[List[Dict[str, Union[str, float]]]] = Field(
        None, description="List of detected classes and their probabilities."
    )
    objects: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="List of detected objects with bounding boxes, labels, and confidence.",
    )
    ocr_text: Optional[str] = Field(None, description="Extracted text from OCR.")
    embedding: Optional[List[float]] = Field(
        None, description="Vector embedding of the image."
    )
    segmentation_masks: Optional[Any] = Field(
        None, description="Raw segmentation mask data (e.g., binary masks or RLE)."
    )

    def summary(self) -> str:
        description_parts = []
        if self.classifications:
            top_class = sorted(
                self.classifications, key=lambda x: x.get("score", 0), reverse=True
            )
            if top_class:
                description_parts.append(
                    f"Class: {top_class[0].get('label')} ({top_class[0].get('score', 0):.2f})"
                )
        if self.objects:
            description_parts.append(f"Objects detected: {len(self.objects)}")
        if self.ocr_text:
            description_parts.append(f"OCR text present ({len(self.ocr_text)} chars)")

        base_summary = self.meta.get(
            "description", ""
        )  # Still allow for an optional descriptive meta tag
        if not description_parts:
            description_parts.append("No specific analysis results.")

        # Consolidate description and add confidence if present
        final_summary = (
            f"Image analysis: {base_summary}. " if base_summary else "Image analysis: "
        )
        final_summary += " ".join(description_parts)
        if self.confidence is not None:
            final_summary += f". Confidence: {self.confidence:.2f}"
        return final_summary.strip()


class AudioAnalysisResult(MultiModalAnalysisResult[Union[str, Dict[str, Any]]]):
    """
    Standardized result for audio analysis, including speech-to-text,
    speaker identification, sentiment, and audio event classification.
    """

    result_type: AnalysisResultType = AnalysisResultType.AUDIO
    transcript: Optional[str] = Field(None, description="Transcribed text from speech.")
    speakers: Optional[List[Dict[str, Union[str, float]]]] = Field(
        None, description="List of identified speakers and their segments."
    )
    sentiment: Optional[Dict[str, float]] = Field(
        None,
        description="Sentiment analysis scores (e.g., {'positive': 0.8, 'negative': 0.1}).",
    )
    audio_events: Optional[List[Dict[str, Union[str, float]]]] = Field(
        None,
        description="Detected audio events (e.g., music, siren, speech) and their confidence.",
    )
    language: Optional[str] = Field(None, description="Detected language of speech.")

    def summary(self) -> str:
        description_parts = []
        if self.transcript:
            description_parts.append(f"Transcript: {self.transcript[:50]}...")
        if self.speakers:
            description_parts.append(f"Speakers detected: {len(self.speakers)}")
        if self.sentiment:
            top_sentiment = max(self.sentiment, key=self.sentiment.get)
            description_parts.append(
                f"Sentiment: {top_sentiment} ({self.sentiment[top_sentiment]:.2f})"
            )
        if self.audio_events:
            description_parts.append(f"Audio events detected: {len(self.audio_events)}")

        base_summary = self.meta.get("description", "")
        if not description_parts:
            description_parts.append("No specific analysis results.")

        final_summary = (
            f"Audio analysis: {base_summary}. " if base_summary else "Audio analysis: "
        )
        final_summary += " ".join(description_parts)
        if self.confidence is not None:
            final_summary += f". Confidence: {self.confidence:.2f}"
        return final_summary.strip()


class VideoAnalysisResult(
    MultiModalAnalysisResult[Union[Dict[str, Any], List[Dict[str, Any]]]]
):
    """
    Standardized result for video analysis, combining insights from frames and temporal modeling.
    Includes scene detection, object tracking, action recognition, and summarization.
    """

    result_type: AnalysisResultType = AnalysisResultType.VIDEO
    scene_changes: Optional[List[float]] = Field(
        None, description="Timestamps of detected scene changes."
    )
    tracked_objects: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="List of objects tracked across frames with their trajectories.",
    )
    actions: Optional[List[Dict[str, Union[str, float, Tuple[float, float]]]]] = Field(
        None, description="Detected actions with timestamps/durations and confidence."
    )
    summary_transcript: Optional[str] = Field(
        None, description="Summarized transcript from video's audio."
    )
    key_frames_analysis: Optional[List[ImageAnalysisResult]] = Field(
        None, description="Analysis results for key frames within the video."
    )
    overall_sentiment: Optional[Dict[str, float]] = Field(
        None,
        description="Overall sentiment derived from video content (visual + audio).",
    )

    def summary(self) -> str:
        description_parts = []
        if self.summary_transcript:
            description_parts.append(
                f"Summarized Audio: {self.summary_transcript[:50]}..."
            )
        if self.scene_changes:
            description_parts.append(f"Scenes: {len(self.scene_changes)}")
        if self.tracked_objects:
            description_parts.append(f"Tracked Objects: {len(self.tracked_objects)}")
        if self.actions:
            description_parts.append(f"Actions Detected: {len(self.actions)}")

        base_summary = self.meta.get("description", "")
        if not description_parts:
            description_parts.append("No specific analysis results.")

        final_summary = (
            f"Video analysis: {base_summary}. " if base_summary else "Video analysis: "
        )
        final_summary += " ".join(description_parts)
        if self.confidence is not None:
            final_summary += f". Confidence: {self.confidence:.2f}"
        return final_summary.strip()


class TextAnalysisResult(MultiModalAnalysisResult[str]):
    """
    Standardized result for text analysis, including classification, sentiment,
    entity extraction, summarization, and translation.
    """

    result_type: AnalysisResultType = AnalysisResultType.TEXT
    classification: Optional[List[Dict[str, Union[str, float]]]] = Field(
        None, description="Text classification results (e.g., categories, topics)."
    )
    sentiment: Optional[Dict[str, float]] = Field(
        None, description="Sentiment analysis scores for the text."
    )
    entities: Optional[List[Dict[str, Union[str, str]]]] = Field(
        None,
        description="Extracted named entities (e.g., people, organizations, locations).",
    )
    summary_text: Optional[str] = Field(
        None, description="A summarized version of the input text."
    )
    translation: Optional[str] = Field(
        None, description="Translated text, if translation was performed."
    )
    language: Optional[str] = Field(None, description="Detected language of the text.")

    def summary(self) -> str:
        description_parts = []
        if self.classification:
            top_class = sorted(
                self.classification, key=lambda x: x.get("score", 0), reverse=True
            )
            if top_class:
                description_parts.append(f"Class: {top_class[0].get('label')}")
        if self.sentiment:
            top_sentiment = max(self.sentiment, key=self.sentiment.get)
            description_parts.append(
                f"Sentiment: {top_sentiment} ({self.sentiment[top_sentiment]:.2f})"
            )
        if self.entities:
            description_parts.append(f"Entities: {len(self.entities)}")
        if self.summary_text:
            description_parts.append(f"Summary: {self.summary_text[:50]}...")
        if self.translation:
            description_parts.append(f"Translated to: {self.language or 'N/A'}")

        base_summary = self.meta.get("description", "")
        if not description_parts:
            description_parts.append("No specific analysis results.")

        final_summary = (
            f"Text analysis: {base_summary}. " if base_summary else "Text analysis: "
        )
        final_summary += " ".join(description_parts)
        if self.confidence is not None:
            final_summary += f". Confidence: {self.confidence:.2f}"
        return final_summary.strip()


class MultiModalPluginInterface(ABC):
    """
    Abstract base class for all multimodal plugins.
    Plugins must implement at least one supported media type for synchronous processing.
    Asynchronous methods are also provided for scalable, non-blocking operations.

    This interface emphasizes:
    - Clear, type-hinted methods.
    - Standardized input (Union for flexibility) and output (Pydantic models).
    - Error handling via `AnalysisResult` `success` and `error_message` fields.
    - Extensibility for new modalities and features.
    - Provision for detailed model information and provenance.
    - Context management for resource handling.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initializes the plugin with a configuration dictionary.
        Configuration typically includes API keys, model names, thresholds, etc.

        Args:
            config: An optional dictionary containing plugin-specific configuration.
                    If not provided, the plugin should attempt to load a default or
                    rely on environment variables.
        """
        self.config = config or {}
        # Plugins should validate their configuration here (e.g., using Pydantic models)

    @abstractmethod
    def analyze_image(
        self, image_data: Union[bytes, str, Any], **kwargs
    ) -> ImageAnalysisResult:
        """
        Analyzes an image synchronously, performing tasks like classification,
        object detection, OCR, or embedding generation.

        Args:
            image_data: The image input. Can be raw bytes, a file path (str),
                        or a library-specific image object (e.g., numpy array).
                        Plugins should handle common image formats (e.g., JPEG, PNG).
            **kwargs: Additional parameters specific to the analysis (e.g., features=['classification', 'ocr'],
                      return_embedding=True, user_id='audit_user_123').

        Returns:
            An ImageAnalysisResult object containing the analysis output and metadata.
            The 'success' field indicates operation status, and 'error_message' if failed.

        Raises:
            NotImplementedError: If the plugin does not support image analysis.
            ValueError: If input `image_data` is invalid, malformed, or cannot be processed.
            RuntimeError: For unexpected operational errors during analysis.
        """
        raise NotImplementedError

    @abstractmethod
    def analyze_audio(
        self, audio_data: Union[bytes, str, Any], **kwargs
    ) -> AudioAnalysisResult:
        """
        Analyzes audio synchronously, performing tasks like speech-to-text,
        speaker diarization, sentiment analysis, or audio event detection.

        Args:
            audio_data: The audio input. Can be raw bytes, a file path (str),
                        or a library-specific audio object (e.g., waveform array).
                        Plugins should handle common audio formats (e.g., WAV, MP3).
            **kwargs: Additional parameters specific to the analysis (e.g., language='en', transcribe=True,
                      diarize_speakers=False, user_id='audit_user_123').

        Returns:
            An AudioAnalysisResult object containing the analysis output and metadata.
            The 'success' field indicates operation status, and 'error_message' if failed.

        Raises:
            NotImplementedError: If the plugin does not support audio analysis.
            ValueError: If input `audio_data` is invalid, malformed, or cannot be processed.
            RuntimeError: For unexpected operational errors during analysis.
        """
        raise NotImplementedError

    @abstractmethod
    def analyze_video(
        self, video_data: Union[bytes, str, Any], **kwargs
    ) -> VideoAnalysisResult:
        """
        Analyzes video synchronously, performing tasks like scene detection,
        object tracking, action recognition, or video summarization.

        Args:
            video_data: The video input. Can be raw bytes, a file path (str),
                        or a sequence of frames/video stream. Plugins should
                        handle common video formats (e.g., MP4, AVI).
            **kwargs: Additional parameters specific to the analysis (e.g., resolution='720p',
                      extract_audio=True, enable_object_tracking=True, user_id='audit_user_123').

        Returns:
            A VideoAnalysisResult object containing the analysis output and metadata.
            The 'success' field indicates operation status, and 'error_message' if failed.

        Raises:
            NotImplementedError: If the plugin does not support video analysis.
            ValueError: If input `video_data` is invalid, malformed, or cannot be processed.
            RuntimeError: For unexpected operational errors during analysis.
        """
        raise NotImplementedError

    def analyze_text(self, text_data: str, **kwargs) -> TextAnalysisResult:
        """
        Analyzes text synchronously, performing tasks like classification, sentiment,
        entity extraction, summarization, or translation. This modality is optional;
        plugins can choose to implement it or raise NotImplementedError.

        Args:
            text_data: The text input string.
            **kwargs: Additional parameters specific to the analysis (e.g., translate_to='es',
                      summarize=True, extract_pii=False, user_id='audit_user_123').

        Returns:
            A TextAnalysisResult object containing the analysis output and metadata.
            The 'success' field indicates operation status, and 'error_message' if failed.

        Raises:
            NotImplementedError: If the plugin does not support text analysis (default behavior).
            ValueError: If input `text_data` is invalid (e.g., empty string).
            RuntimeError: For unexpected operational errors during analysis.
        """
        raise NotImplementedError  # Optional modality, so default is NotImplementedError

    @abstractmethod
    def supported_modalities(self) -> List[str]:
        """
        Returns a list of modalities fully supported (synchronously) by this plugin instance.
        Valid values are strings matching AnalysisResultType enum values (e.g., "image", "audio", "video", "text").
        This allows a client to query what capabilities a specific plugin offers.
        """
        pass

    # --- Asynchronous counterparts for scalable inference ---
    # These methods are not abstract to allow plugins to selectively implement async support.
    # If not implemented by a concrete plugin, calling them should raise NotImplementedError.
    # Plugins that support async operations should ensure they are truly non-blocking.

    async def analyze_image_async(
        self, image_data: Union[bytes, str, Any], **kwargs
    ) -> ImageAnalysisResult:
        """
        Asynchronous counterpart to analyze_image.
        Implement if non-blocking I/O or long-running inference is needed for image processing.
        """
        raise NotImplementedError(
            "Asynchronous image analysis not implemented by this plugin."
        )

    async def analyze_audio_async(
        self, audio_data: Union[bytes, str, Any], **kwargs
    ) -> AudioAnalysisResult:
        """
        Asynchronous counterpart to analyze_audio.
        Implement if non-blocking I/O or long-running inference is needed for audio processing.
        """
        raise NotImplementedError(
            "Asynchronous audio analysis not implemented by this plugin."
        )

    async def analyze_video_async(
        self, video_data: Union[bytes, str, Any], **kwargs
    ) -> VideoAnalysisResult:
        """
        Asynchronous counterpart to analyze_video.
        Implement if non-blocking I/O or long-running inference is needed for video processing.
        """
        raise NotImplementedError(
            "Asynchronous video analysis not implemented by this plugin."
        )

    async def analyze_text_async(self, text_data: str, **kwargs) -> TextAnalysisResult:
        """
        Asynchronous counterpart to analyze_text.
        Implement if non-blocking I/O or long-running inference is needed for text processing.
        """
        raise NotImplementedError(
            "Asynchronous text analysis not implemented by this plugin."
        )

    def model_info(self) -> Dict[str, Any]:
        """
        Provides detailed information about the underlying models or algorithms
        used by this plugin. This can include version, architecture, training data,
        performance metrics, licensing, any specific configurations,
        and **runtime state (e.g., call counts, active connections)**.

        Returns:
            A dictionary containing model information. An empty dictionary if no info available.
        """
        return {}

    def __enter__(self) -> "MultiModalPluginInterface":
        """
        Enables synchronous context management (e.g., `with plugin:`) for resource allocation.
        Plugins should implement setup logic here (e.g., load models, establish connections).
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Enables synchronous context management for resource cleanup.
        Plugins should implement cleanup logic here (e.g., release GPU memory, close files).
        """
        self.shutdown()

    async def __aenter__(self) -> "MultiModalPluginInterface":
        """
        Enables asynchronous context management (e.g., `async with plugin:`) for resource allocation.
        Plugins should implement async setup logic here (e.g., warm up models, establish async connections).
        """
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Enables asynchronous context management for resource cleanup.
        Plugins should implement async cleanup logic here (e.g., flush async buffers, close async connections).
        """
        await self.shutdown_async()  # Assuming an async shutdown method

    def shutdown(self) -> None:
        """
        Performs any necessary synchronous cleanup or resource release for the plugin.
        This could include closing API connections, releasing GPU memory, etc.
        """
        pass  # Default no-op for plugins that don't require explicit synchronous shutdown

    async def shutdown_async(self) -> None:
        """
        Performs any necessary asynchronous cleanup or resource release for the plugin.
        Implement if non-blocking shutdown operations are needed.
        """
        pass  # Default no-op for plugins that don't require explicit asynchronous shutdown


# Example stub implementation (for tests/dev)
class DummyMultiModalPlugin(MultiModalPluginInterface):
    """
    Dummy plugin for tests/development. Returns stub results for all modalities.
    This implementation is synchronous by default and raises NotImplementedError
    for async methods, simulating a basic plugin.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.dummy_model_id = (
            config.get("model_id", "dummy-v1.0") if config else "dummy-v1.0"
        )
        self.call_count = 0  # For demonstrating resource management/tracking
        self.requests_total = get_or_create(
            Counter(
                "dummy_plugin_requests_total",
                "Total dummy plugin requests",
                ["modality", "status"],
            )
        )
        self.processing_latency_seconds = get_or_create(
            Histogram(
                "dummy_plugin_processing_latency_seconds",
                "Dummy plugin processing latency",
                ["modality"],
            )
        )

    def analyze_image(
        self, image_data: Union[bytes, str, Any], **kwargs
    ) -> ImageAnalysisResult:
        start_time = time.monotonic()
        try:
            if image_data is None or (
                isinstance(image_data, str) and not os.path.exists(image_data)
            ):
                raise ValueError(
                    "Invalid image_data: must be non-empty bytes or valid file path"
                )

            # Simulate some processing time
            time.sleep(0.01)
            self.call_count += 1

            # description is now passed via meta for the summary method to pick up.
            result = ImageAnalysisResult(
                raw_data={
                    "processed_bytes": (
                        len(image_data) if isinstance(image_data, bytes) else "N/A"
                    )
                },
                meta={
                    "custom_meta": kwargs.get("context", "no_context"),
                    "description": kwargs.get("description", "Dummy image analysis"),
                },
                result_type=AnalysisResultType.IMAGE,
                success=True,
                confidence=0.75,
                model_id=self.dummy_model_id,
                classifications=[{"label": "dummy_category", "score": 0.9}],
                objects=[
                    {
                        "box": [10, 20, 30, 40],
                        "label": "dummy_object",
                        "confidence": 0.8,
                    }
                ],
                ocr_text="dummy text from image",
                data_provenance={
                    "input_type": type(image_data).__name__,
                    "size": len(image_data) if isinstance(image_data, bytes) else "N/A",
                },
            )
            self.requests_total.labels(modality="image", status="success").inc()
            self.processing_latency_seconds.labels(modality="image").observe(
                time.monotonic() - start_time
            )
            return result
        except Exception:
            self.requests_total.labels(modality="image", status="failure").inc()
            self.processing_latency_seconds.labels(modality="image").observe(
                time.monotonic() - start_time
            )
            raise

    def analyze_audio(
        self, audio_data: Union[bytes, str, Any], **kwargs
    ) -> AudioAnalysisResult:
        start_time = time.monotonic()
        try:
            if audio_data is None or (
                isinstance(audio_data, str) and not os.path.exists(audio_data)
            ):
                raise ValueError(
                    "Invalid audio_data: must be non-empty bytes or valid file path"
                )

            time.sleep(0.01)
            self.call_count += 1

            # description is now passed via meta for the summary method to pick up.
            result = AudioAnalysisResult(
                raw_data={
                    "processed_samples": (
                        len(audio_data) if isinstance(audio_data, bytes) else "N/A"
                    )
                },
                meta={
                    "custom_meta": kwargs.get("context", "no_context"),
                    "description": kwargs.get("description", "Dummy audio analysis"),
                },
                result_type=AnalysisResultType.AUDIO,
                success=True,
                confidence=0.8,
                model_id=self.dummy_model_id,
                transcript="This is a dummy transcript.",
                language="en",
                sentiment={"positive": 0.7, "negative": 0.2, "neutral": 0.1},
                audio_events=[{"event": "dummy_event", "confidence": 0.9}],
                data_provenance={
                    "input_type": type(audio_data).__name__,
                    "size": len(audio_data) if isinstance(audio_data, bytes) else "N/A",
                },
            )
            self.requests_total.labels(modality="audio", status="success").inc()
            self.processing_latency_seconds.labels(modality="audio").observe(
                time.monotonic() - start_time
            )
            return result
        except Exception:
            self.requests_total.labels(modality="audio", status="failure").inc()
            self.processing_latency_seconds.labels(modality="audio").observe(
                time.monotonic() - start_time
            )
            raise

    def analyze_video(
        self, video_data: Union[bytes, str, Any], **kwargs
    ) -> VideoAnalysisResult:
        start_time = time.monotonic()
        try:
            if video_data is None or (
                isinstance(video_data, str) and not os.path.exists(video_data)
            ):
                raise ValueError(
                    "Invalid video_data: must be non-empty bytes or valid file path"
                )

            time.sleep(0.01)
            self.call_count += 1

            # description is now passed via meta for the summary method to pick up.
            result = VideoAnalysisResult(
                raw_data={"processed_frames": 100},
                meta={
                    "custom_meta": kwargs.get("context", "no_context"),
                    "description": kwargs.get("description", "Dummy video analysis"),
                },
                result_type=AnalysisResultType.VIDEO,
                success=True,
                confidence=0.85,
                model_id=self.dummy_model_id,
                summary_transcript="Summarized dummy video content.",
                scene_changes=[1.5, 5.2, 10.1],
                tracked_objects=[
                    {"id": 1, "label": "person", "trajectory": [[0, 0], [1, 1]]}
                ],
                actions=[
                    {"action": "running", "confidence": 0.9, "timestamp": (1.0, 2.0)}
                ],
                key_frames_analysis=[],
                overall_sentiment={"positive": 0.7, "negative": 0.1},
                data_provenance={
                    "input_type": type(video_data).__name__,
                    "size": len(video_data) if isinstance(video_data, bytes) else "N/A",
                },
            )
            self.requests_total.labels(modality="video", status="success").inc()
            self.processing_latency_seconds.labels(modality="video").observe(
                time.monotonic() - start_time
            )
            return result
        except Exception:
            self.requests_total.labels(modality="video", status="failure").inc()
            self.processing_latency_seconds.labels(modality="video").observe(
                time.monotonic() - start_time
            )
            raise

    def analyze_text(self, text_data: str, **kwargs) -> TextAnalysisResult:
        start_time = time.monotonic()
        try:
            if not isinstance(text_data, str) or not text_data:
                raise ValueError("Invalid text_data: must be a non-empty string")

            time.sleep(0.005)
            self.call_count += 1

            # description is now passed via meta for the summary method to pick up.
            result = TextAnalysisResult(
                raw_data=text_data,
                meta={
                    "custom_meta": kwargs.get("context", "no_context"),
                    "description": kwargs.get("description", "Dummy text analysis"),
                },
                result_type=AnalysisResultType.TEXT,
                success=True,
                confidence=0.9,
                model_id=self.dummy_model_id,
                classification=[{"label": "dummy_topic", "score": 0.95}],
                sentiment={"positive": 0.9, "negative": 0.05, "neutral": 0.05},
                entities=[{"text": "John Doe", "type": "PERSON"}],
                summary_text=f"Summary of: {text_data[:20]}...",
                translation="Este es un texto de prueba.",
                language="en",
                data_provenance={
                    "input_type": type(text_data).__name__,
                    "length": len(text_data),
                },
            )
            self.requests_total.labels(modality="text", status="success").inc()
            self.processing_latency_seconds.labels(modality="text").observe(
                time.monotonic() - start_time
            )
            return result
        except Exception:
            self.requests_total.labels(modality="text", status="failure").inc()
            self.processing_latency_seconds.labels(modality="text").observe(
                time.monotonic() - start_time
            )
            raise

    def supported_modalities(self) -> List[str]:
        # Returns all supported modalities as strings from the Enum
        return [m.value for m in AnalysisResultType if m != AnalysisResultType.GENERIC]

    def model_info(self) -> Dict[str, Any]:
        return {
            "name": "DummyMultiModalPlugin",
            "version": "1.0",
            "model_id": self.dummy_model_id,
            "description": "A placeholder plugin for testing and development purposes.",
            "supported_features": {
                "image": ["classification", "object_detection", "ocr"],
                "audio": ["speech_to_text", "sentiment"],
                "video": ["summary_transcript", "scene_changes"],
                "text": ["classification", "sentiment", "summarization"],
            },
            "call_count": self.call_count,  # Demonstrating state tracking
        }

    def shutdown(self) -> None:
        """
        Dummy shutdown for demonstration.
        """
        print(
            f"DummyMultiModalPlugin (ID: {self.dummy_model_id}) shutting down after {self.call_count} calls."
        )

    async def shutdown_async(self) -> None:
        """
        Dummy async shutdown for demonstration.
        """
        print(
            f"DummyMultiModalPlugin (ID: {self.dummy_model_id}) async shutting down after {self.call_count} calls."
        )
        await asyncio.sleep(0.001)  # Simulate async cleanup


# Inline sanity test and usage examples
if __name__ == "__main__":
    print("--- Testing DummyMultiModalPlugin (Synchronous) ---")

    # Using context manager for automatic shutdown
    with DummyMultiModalPlugin(config={"model_id": "dummy-test-v1.1"}) as plugin:
        # Test Image
        img_result = plugin.analyze_image(
            b"fake_image_bytes", description="family photo", context="from_user_upload"
        )
        print(f"Image Summary: {img_result.summary()}")
        print(f"Image Valid: {img_result.is_valid()}")
        print(f"Image as JSON: {img_result.model_dump_json(indent=2)}")
        print(
            f"Image Provenance: {json.dumps(img_result.get_provenance_info(), indent=2)}"
        )
        print(f"Image repr: {repr(img_result)}")  # Test __repr__

        # Test Audio
        audio_result = plugin.analyze_audio(
            b"fake_audio_bytes", description="meeting recording"
        )
        print(f"\nAudio Summary: {audio_result.summary()}")
        print(f"Audio Valid: {audio_result.is_valid()}")
        print(f"Audio as JSON: {audio_result.model_dump_json(indent=2)}")
        print(f"Audio repr: {repr(audio_result)}")  # Test __repr__

        # Test Video
        video_result = plugin.analyze_video(
            b"fake_video_bytes", description="security footage"
        )
        print(f"\nVideo Summary: {video_result.summary()}")
        print(f"Video Valid: {video_result.is_valid()}")
        print(f"Video as JSON: {video_result.model_dump_json(indent=2)}")
        print(f"Video repr: {repr(video_result)}")  # Test __repr__

        # Test Text
        text_result = plugin.analyze_text(
            "The quick brown fox jumps over the lazy dog.", description="user query"
        )
        print(f"\nText Summary: {text_result.summary()}")
        print(f"Text Valid: {text_result.is_valid()}")
        print(f"Text as JSON: {text_result.model_dump_json(indent=2)}")
        print(f"Text repr: {repr(text_result)}")  # Test __repr__

        # Test Model Info (should reflect accumulated calls)
        print(f"\nModel Info: {json.dumps(plugin.model_info(), indent=2)}")

    print("\n--- Testing Plugin with Invalid Configuration (via Pydantic) ---")
    try:
        # This will fail if a Pydantic model for config had strict validation rules
        # For this example, we'll demonstrate a result validation error as it's more direct
        # Dummy config class to test validation if config were a Pydantic model
        class TestConfig(BaseModel):
            threshold: float = Field(..., gt=0, lt=1)

        # This would raise ValidationError if passed to a plugin constructor expecting TestConfig
        # TestConfig(threshold=1.5)
        print(
            "Configuration validation test conceptual. See `TestConfig(threshold=1.5)` example."
        )

    except ValidationError as e:
        print(f"Caught expected Pydantic validation error for config: {e.errors()}")

    print("\n--- Testing Error Case in Dummy Plugin (Simulated) ---")
    # Simulate an error by creating a result with success=False
    error_simulated_result = ImageAnalysisResult(
        raw_data={"processed_pixels": 0},
        result_type=AnalysisResultType.IMAGE,
        success=False,
        error_message="Simulated internal processing error during dummy analysis.",
        model_id="dummy-error-model",
    )
    print(f"Error Result Summary: {error_simulated_result.summary()}")
    print(f"Error Result Valid: {error_simulated_result.is_valid()}")  # Should be False
    print(f"Error Result as JSON: {error_simulated_result.model_dump_json(indent=2)}")
    print(
        f"Error Result Provenance: {json.dumps(error_simulated_result.get_provenance_info(), indent=2)}"
    )
    print(f"Error Result repr: {repr(error_simulated_result)}")  # Test __repr__

    print("\n--- Testing Async Method Not Implemented ---")

    async def run_async_test_methods():
        plugin_async = DummyMultiModalPlugin()
        async with plugin_async:  # Test async context manager
            try:
                await plugin_async.analyze_image_async(b"async_test_image")
            except NotImplementedError as e:
                print(f"Caught expected NotImplementedError for async method: {e}")
            try:
                await plugin_async.analyze_audio_async(b"async_test_audio")
            except NotImplementedError as e:
                print(f"Caught expected NotImplementedError for async method: {e}")
            # The __aexit__ will call shutdown_async

    asyncio.run(run_async_test_methods())

    # Test direct instantiation of abstract class (should fail)
    print("\n--- Testing Direct Instantiation of Abstract Base Class ---")
    try:
        # This should raise a TypeError
        # MultiModalAnalysisResult(raw_data="test", result_type=AnalysisResultType.GENERIC)
        print(
            "Direct instantiation of MultiModalAnalysisResult (abstract base class) is prevented at runtime."
        )
        print(
            'Uncomment `MultiModalAnalysisResult(raw_data="test", result_type=AnalysisResultType.GENERIC)` to see TypeError.'
        )
    except TypeError as e:
        print(f"Caught expected TypeError for abstract class instantiation: {e}")
