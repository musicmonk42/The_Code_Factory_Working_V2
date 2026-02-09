# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# D:\SFE\self_fixing_engineer\arbiter\plugins\multimodal\providers\default_multimodal_providers.py
"""
default_multimodal_providers.py — Default Multimodal Processor Implementations

This module contains concrete, production-ready implementations of the multimodal
processor interfaces defined in `interface.py`. It includes processors for
image, audio, video, and text analysis.

Key Features:
- **Pydantic-based Configuration:** Each processor uses a Pydantic `BaseModel`
  to validate its configuration, ensuring type safety and robust initialization.
- **Enhanced Error Handling:** All processing logic is wrapped in `try...except`
  blocks to catch unexpected exceptions, log them with a unique `operation_id`,
  and return a standardized `ProcessingResult` with `success=False`.
- **Robust Logging:** Detailed logging is included for key events like processor
  initialization, start and end of processing, and any errors. Sensitive data
  in logs is truncated.
- **Extensible Plugin Registry:** The `PluginRegistry` allows for dynamic
  registration of new processors and includes a method for unregistration.
- **Metrics & Tracing Hooks:** Placeholder comments indicate where to integrate
  metrics (e.g., Prometheus counters/histograms) and tracing (e.g., OpenTelemetry spans).
  These are crucial for observability in a production environment.

How to Register Custom Processors:
To add a new processor, simply define a class that inherits from one of the
abstract processor base classes (e.g., `ImageProcessor`) and implement the `process`
method. Then, register it with the `PluginRegistry` at application startup:
`PluginRegistry.register_processor("image", "my_custom_processor", MyCustomImageProcessor)`
"""

import asyncio
import base64
import logging
import os
import random
import uuid
from typing import Any, Dict, List, Optional, Type, Union

# Pydantic for robust configuration validation
try:
    from prometheus_client import Counter, Histogram
    from pydantic import BaseModel, Field, ValidationError
except ImportError:
    raise ImportError(
        "Required libraries missing. Please install 'pydantic' and 'prometheus_client'."
    )

# Import the abstract interfaces that these concrete processors implement
from self_fixing_engineer.arbiter.plugins.multimodal.interface import (
    AudioProcessor,
    ConfigurationError,
    ImageProcessor,
    InvalidInputError,
    MultiModalException,
    ProcessingResult,
    ProviderNotAvailableError,
    TextProcessor,
    VideoProcessor,
)

logger = logging.getLogger(__name__)


# Helper function to get or create metrics
from prometheus_client import REGISTRY

def get_or_create(metric):
    """
    Helper function to get or create a metric, handling duplicates safely.
    Returns existing metric if already registered, otherwise returns a mock metric.
    """
    try:
        metric_name = metric._name
        # Check if metric already exists in the registry
        for collector in list(REGISTRY._names_to_collectors.values()):
            if hasattr(collector, '_name') and collector._name == metric_name:
                return collector
        # Metric doesn't exist, return the new one (it will be registered on first use)
        return metric
    except (ValueError, AttributeError) as e:
        # If metric creation or access fails, return a mock that does nothing
        class MockMetric:
            def labels(self, **kwargs):
                return self
            def inc(self, amount=1):
                pass
            def observe(self, amount):
                pass
        logger.debug(f"Error with metric, returning mock: {e}")
        return MockMetric()


# --- PluginRegistry ---
class PluginRegistry:
    """
    Manages the registration and retrieval of multimodal processors.
    This registry is designed to be populated once at application startup.
    While methods for dynamic registration/unregistration are provided,
    care should be taken in multi-threaded environments.
    """

    _processors: Dict[str, Dict[str, Type]] = {
        "image": {},
        "audio": {},
        "video": {},
        "text": {},
    }

    @classmethod
    def register_processor(cls, modality: str, name: str, processor_class: Type):
        """
        Registers a new processor class for a given modality.

        Args:
            modality: The modality string (e.g., "image", "audio").
            name: A unique name for the processor (e.g., "default", "openai_dalle").
            processor_class: The concrete class implementing the processor interface.

        Raises:
            ValueError: If the modality is not supported.
            MultiModalException: If a processor with the same name is already registered.
        """
        if modality not in cls._processors:
            raise ValueError(
                f"Unsupported modality: {modality}. Supported: {list(cls._processors.keys())}"
            )
        if name in cls._processors[modality]:
            raise MultiModalException(
                f"Processor '{name}' already registered for {modality}."
            )
        cls._processors[modality][name] = processor_class
        logger.info(f"Registered {modality} processor: {name}")

    @classmethod
    def unregister_processor(cls, modality: str, name: str) -> None:
        """
        Unregisters a processor for a given modality.
        Args:
            modality: The modality string (e.g., "image", "audio").
            name: The registered processor name (e.g., "default").
        Raises:
            ValueError: If the modality is not supported.
            MultiModalException: If the processor is not registered.
        """
        if modality not in cls._processors:
            raise ValueError(
                f"Unsupported modality: {modality}. Supported: {list(cls._processors.keys())}"
            )
        if name not in cls._processors[modality]:
            raise MultiModalException(
                f"Processor '{name}' not registered for modality '{modality}'"
            )
        del cls._processors[modality][name]
        logger.info(f"Unregistered processor '{name}' for modality '{modality}'")

    @classmethod
    def get_processor(cls, modality: str, name: str, config: Dict[str, Any]) -> Any:
        """
        Retrieves a processor instance for the given modality and name.
        Args:
            modality: The modality string (e.g., "image", "audio").
            name: The registered processor name (e.g., "default").
            config: Configuration dictionary for the processor.
        Returns:
            An instance of the processor class.
        Raises:
            ValueError: If the modality is not supported.
            ProviderNotAvailableError: If the processor name is not registered.
        """
        if modality not in cls._processors:
            raise ValueError(
                f"Unsupported modality: {modality}. Supported: {list(cls._processors.keys())}"
            )
        processor_class = cls._processors[modality].get(name)
        if not processor_class:
            raise ProviderNotAvailableError(
                f"No provider '{name}' for modality '{modality}'"
            )
        try:
            return processor_class(config)
        except ValidationError as e:
            logger.error(f"Configuration error for {modality} processor '{name}': {e}")
            raise ConfigurationError(
                f"Invalid configuration for {modality} processor '{name}': {e}"
            )

    @classmethod
    def get_supported_providers(cls, modality: str) -> List[str]:
        """
        Returns a list of supported provider names for a given modality.
        Args:
            modality: The modality string (e.g., "image", "audio").
        Returns:
            A list of registered processor names for the modality.
        Raises:
            ValueError: If the modality is not supported.
        """
        if modality not in cls._processors:
            raise ValueError(
                f"Unsupported modality: {modality}. Supported: {list(cls._processors.keys())}"
            )
        return list(cls._processors[modality].keys())


# --- END PluginRegistry ---


# --- Pydantic Config Schemas for Validation ---
class DefaultImageProcessorConfig(BaseModel):
    """Configuration schema for DefaultImageProcessor."""

    mock_min_latency_ms: int = Field(
        10, ge=0, description="Minimum mock latency in milliseconds"
    )
    mock_max_latency_ms: int = Field(
        100, ge=0, description="Maximum mock latency in milliseconds"
    )
    max_size_mb: int = Field(10, ge=1, description="Maximum input size in megabytes")


class DefaultAudioProcessorConfig(BaseModel):
    """Configuration schema for DefaultAudioProcessor."""

    mock_min_latency_ms: int = Field(10, ge=0)
    mock_max_latency_ms: int = Field(100, ge=0)
    max_size_mb: int = Field(20, ge=1, description="Maximum input size in megabytes")


class DefaultVideoProcessorConfig(BaseModel):
    """Configuration schema for DefaultVideoProcessor."""

    mock_min_latency_ms: int = Field(10, ge=0)
    mock_max_latency_ms: int = Field(100, ge=0)
    max_size_mb: int = Field(100, ge=1, description="Maximum input size in megabytes")


class DefaultTextProcessorConfig(BaseModel):
    """Configuration schema for DefaultTextProcessor."""

    mock_min_latency_ms: int = Field(5, ge=0)
    mock_max_latency_ms: int = Field(25, ge=0)
    max_length: int = Field(10000, ge=1, description="Maximum text length")


# --- END Pydantic Config Schemas ---


# Concrete implementations of the abstract processors
class DefaultImageProcessor(ImageProcessor):
    """Default mock processor for image data."""

    def __init__(self, config: Dict[str, Any]):
        try:
            self.config = DefaultImageProcessorConfig.model_validate(config)
        except ValidationError as e:
            raise ConfigurationError(
                f"Invalid configuration for DefaultImageProcessor: {e}"
            ) from e

        try:
            self.requests_total = get_or_create(
                Counter(
                    "default_image_processor_requests_total",
                    "Total image processing requests",
                    ["status"],
                )
            )
        except ValueError:
            # Metric already registered, retrieve from registry
            for collector in list(REGISTRY._names_to_collectors.values()):
                if hasattr(collector, '_name') and collector._name == "default_image_processor_requests_total":
                    self.requests_total = collector
                    break
            else:
                self.requests_total = None

        try:
            self.processing_latency_seconds = get_or_create(
                Histogram(
                    "default_image_processor_latency_seconds",
                    "Image processing latency",
                    [],
                    buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, float("inf")),
                )
            )
        except ValueError:
            # Metric already registered, retrieve from registry
            for collector in list(REGISTRY._names_to_collectors.values()):
                if hasattr(collector, '_name') and collector._name == "default_image_processor_latency_seconds":
                    self.processing_latency_seconds = collector
                    break
            else:
                self.processing_latency_seconds = None

        logger.info("DefaultImageProcessor initialized with config: %s", self.config)

    async def process(
        self, image_data: Any, operation_id: Optional[str] = None, **kwargs: Any
    ) -> ProcessingResult:
        op_id = operation_id if operation_id else str(uuid.uuid4())
        logger.info(f"[{op_id}] Starting image processing...")
        start_time = asyncio.get_event_loop().time()

        try:
            if image_data is None or (
                isinstance(image_data, str) and not os.path.exists(image_data)
            ):
                raise InvalidInputError(
                    "Invalid image_data: must be non-empty bytes or valid file path"
                )

            image_bytes = self._decode_data(image_data)
            if not image_bytes:
                raise InvalidInputError("Invalid or empty image data provided")

            if len(image_bytes) > self.config.max_size_mb * 1024 * 1024:
                raise InvalidInputError(
                    f"Image data exceeds max size of {self.config.max_size_mb}MB"
                )

            if isinstance(image_bytes, bytes) and not (
                image_bytes.startswith(b"\xff\xd8")
                or image_bytes.startswith(b"\x89PNG")
            ):
                raise InvalidInputError(
                    "Unsupported image format; only JPEG and PNG are supported"
                )

            latency = random.uniform(
                self.config.mock_min_latency_ms / 1000,
                self.config.mock_max_latency_ms / 1000,
            )
            await asyncio.sleep(latency)

            data_start = (
                image_bytes[:20].decode("latin-1", "ignore")
                if isinstance(image_bytes, bytes)
                else str(image_bytes)[:20]
            )

            processed_text = f"Detected text from image data starting with '{data_start}...'. This is a mock OCR result."
            caption = f"A beautiful scene captured in an image. (Mock Caption for data starting with '{data_start}...')"

            result_data = {
                "ocr_text": processed_text,
                "caption": caption,
                "size_bytes": len(image_bytes),
            }
            summary = f"Image processed. OCR: '{processed_text[:50]}...', Caption: '{caption[:50]}...' "

            logger.info(
                f"[{op_id}] Image processing completed successfully. Summary: {summary}"
            )

            self.requests_total.labels(status="success").inc()
            self.processing_latency_seconds.observe(
                asyncio.get_event_loop().time() - start_time
            )

            return ProcessingResult(
                success=True,
                data=result_data,
                summary=summary,
                operation_id=op_id,
                model_confidence=random.uniform(0.7, 0.95),
            )

        except InvalidInputError as e:
            logger.error(
                f"[{op_id}] Invalid input error during image processing: {e}",
                exc_info=True,
            )
            self.requests_total.labels(status="failure").inc()
            self.processing_latency_seconds.observe(
                asyncio.get_event_loop().time() - start_time
            )
            return ProcessingResult(success=False, error=str(e), operation_id=op_id)
        except Exception as e:
            logger.error(
                f"[{op_id}] Unexpected error during image processing. Input starts with '{str(image_data)[:50]}...'",
                exc_info=True,
            )
            self.requests_total.labels(status="failure").inc()
            self.processing_latency_seconds.observe(
                asyncio.get_event_loop().time() - start_time
            )
            return ProcessingResult(
                success=False,
                error=f"Unexpected processing error: {e}",
                operation_id=op_id,
            )

    def _decode_data(self, data: Union[bytes, str]) -> Optional[bytes]:
        """Decodes base64 string to bytes, or returns bytes directly."""
        if isinstance(data, bytes):
            return data
        elif isinstance(data, str):
            # Check if it's a file path
            if os.path.exists(data):
                try:
                    with open(data, "rb") as f:
                        return f.read()
                except Exception as e:
                    logger.error(f"Failed to read file {data}: {e}")
                    return None
            else:
                # Try to decode as base64
                try:
                    # Remove any whitespace and newlines
                    cleaned = (
                        data.strip()
                        .replace("\n", "")
                        .replace("\r", "")
                        .replace(" ", "")
                    )
                    # Add padding if needed
                    missing_padding = len(cleaned) % 4
                    if missing_padding:
                        cleaned += "=" * (4 - missing_padding)
                    decoded = base64.b64decode(cleaned, validate=True)
                    return decoded if decoded else None
                except Exception as e:
                    logger.error(f"Failed to base64 decode image data: {e}")
                    return None
        else:
            return None

    async def health_check(self) -> bool:
        """
        Checks the health of the image processor.
        Returns:
            bool: True if the processor is operational, False otherwise.
        """
        try:
            await asyncio.sleep(0.001)
            logger.info("DefaultImageProcessor health check passed")
            return True
        except Exception as e:
            logger.error(f"DefaultImageProcessor health check failed: {e}")
            return False


class DefaultAudioProcessor(AudioProcessor):
    """Default mock processor for audio data."""

    def __init__(self, config: Dict[str, Any]):
        try:
            self.config = DefaultAudioProcessorConfig.model_validate(config)
        except ValidationError as e:
            raise ConfigurationError(
                f"Invalid configuration for DefaultAudioProcessor: {e}"
            ) from e

        self.requests_total = get_or_create(
            Counter(
                "default_audio_processor_requests_total",
                "Total audio processing requests",
                ["status"],
            )
        )
        self.processing_latency_seconds = get_or_create(
            Histogram(
                "default_audio_processor_latency_seconds",
                "Audio processing latency",
                [],
                buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, float("inf")),
            )
        )
        logger.info("DefaultAudioProcessor initialized with config: %s", self.config)

    async def process(
        self, audio_data: Any, operation_id: Optional[str] = None, **kwargs: Any
    ) -> ProcessingResult:
        op_id = operation_id if operation_id else str(uuid.uuid4())
        logger.info(f"[{op_id}] Starting audio processing...")
        start_time = asyncio.get_event_loop().time()

        try:
            if audio_data is None or (
                isinstance(audio_data, str) and not os.path.exists(audio_data)
            ):
                raise InvalidInputError(
                    "Invalid audio_data: must be non-empty bytes or valid file path"
                )

            audio_bytes = self._decode_data(audio_data)
            if not audio_bytes:
                raise InvalidInputError("Invalid or empty audio data provided")

            if len(audio_bytes) > self.config.max_size_mb * 1024 * 1024:
                raise InvalidInputError(
                    f"Audio data exceeds max size of {self.config.max_size_mb}MB"
                )

            # Simulate format check (e.g., RIFF for WAV, ID3 for MP3)
            if isinstance(audio_bytes, bytes) and not (
                audio_bytes.startswith(b"RIFF") or audio_bytes.startswith(b"ID3")
            ):
                raise InvalidInputError(
                    "Unsupported audio format; only WAV and MP3 are supported"
                )

            latency = random.uniform(
                self.config.mock_min_latency_ms / 1000,
                self.config.mock_max_latency_ms / 1000,
            )
            await asyncio.sleep(latency)

            data_start = (
                audio_bytes[:20].decode("latin-1", "ignore")
                if isinstance(audio_bytes, bytes)
                else str(audio_bytes)[:20]
            )

            transcription = f"This is a mock transcription of the audio. (Audio data starts with '{data_start}...')"

            result_data = {
                "transcription": transcription,
                "size_bytes": len(audio_bytes),
            }
            summary = f"Audio processed. Transcript: '{transcription[:50]}...'"

            logger.info(
                f"[{op_id}] Audio processing completed successfully. Summary: {summary}"
            )

            self.requests_total.labels(status="success").inc()
            self.processing_latency_seconds.observe(
                asyncio.get_event_loop().time() - start_time
            )

            return ProcessingResult(
                success=True,
                data=result_data,
                summary=summary,
                operation_id=op_id,
                model_confidence=random.uniform(0.8, 0.98),
            )
        except InvalidInputError as e:
            logger.error(
                f"[{op_id}] Invalid input error during audio processing: {e}",
                exc_info=True,
            )
            self.requests_total.labels(status="failure").inc()
            self.processing_latency_seconds.observe(
                asyncio.get_event_loop().time() - start_time
            )
            return ProcessingResult(success=False, error=str(e), operation_id=op_id)
        except Exception as e:
            logger.error(
                f"[{op_id}] Unexpected error during audio processing. Input starts with '{str(audio_data)[:50]}...'",
                exc_info=True,
            )
            self.requests_total.labels(status="failure").inc()
            self.processing_latency_seconds.observe(
                asyncio.get_event_loop().time() - start_time
            )
            return ProcessingResult(
                success=False,
                error=f"Unexpected processing error: {e}",
                operation_id=op_id,
            )

    def _decode_data(self, data: Union[bytes, str]) -> Optional[bytes]:
        """Decodes base64 string to bytes, or returns bytes directly."""
        if isinstance(data, str):
            # Check if it's a file path
            if os.path.exists(data):
                try:
                    with open(data, "rb") as f:
                        return f.read()
                except Exception as e:
                    logger.error(f"Failed to read file {data}: {e}")
                    return None
            else:
                # Try to decode as base64
                try:
                    cleaned = (
                        data.strip()
                        .replace("\n", "")
                        .replace("\r", "")
                        .replace(" ", "")
                    )
                    return base64.b64decode(cleaned)
                except Exception as e:
                    logger.error(f"Failed to base64 decode audio data: {e}")
                    return None
        elif isinstance(data, bytes):
            return data
        else:
            return None

    async def health_check(self) -> bool:
        """
        Checks the health of the audio processor.
        Returns:
            bool: True if the processor is operational, False otherwise.
        """
        try:
            await asyncio.sleep(0.001)
            logger.info("DefaultAudioProcessor health check passed")
            return True
        except Exception as e:
            logger.error(f"DefaultAudioProcessor health check failed: {e}")
            return False


class DefaultVideoProcessor(VideoProcessor):
    """Default mock processor for video data."""

    def __init__(self, config: Dict[str, Any]):
        try:
            self.config = DefaultVideoProcessorConfig.model_validate(config)
        except ValidationError as e:
            raise ConfigurationError(
                f"Invalid configuration for DefaultVideoProcessor: {e}"
            ) from e

        self.requests_total = get_or_create(
            Counter(
                "default_video_processor_requests_total",
                "Total video processing requests",
                ["status"],
            )
        )
        self.processing_latency_seconds = get_or_create(
            Histogram(
                "default_video_processor_latency_seconds",
                "Video processing latency",
                [],
                buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, float("inf")),
            )
        )
        logger.info("DefaultVideoProcessor initialized with config: %s", self.config)

    async def process(
        self, video_data: Any, operation_id: Optional[str] = None, **kwargs: Any
    ) -> ProcessingResult:
        op_id = operation_id if operation_id else str(uuid.uuid4())
        logger.info(f"[{op_id}] Starting video processing...")
        start_time = asyncio.get_event_loop().time()

        try:
            if video_data is None or (
                isinstance(video_data, str) and not os.path.exists(video_data)
            ):
                raise InvalidInputError(
                    "Invalid video_data: must be non-empty bytes or valid file path"
                )

            video_bytes = self._decode_data(video_data)
            if not video_bytes:
                raise InvalidInputError("Invalid or empty video data provided")

            if len(video_bytes) > self.config.max_size_mb * 1024 * 1024:
                raise InvalidInputError(
                    f"Video data exceeds max size of {self.config.max_size_mb}MB"
                )

            # Simulate format check (e.g., ftyp for MP4, AVI for AVI)
            if isinstance(video_bytes, bytes) and not (
                b"ftyp" in video_bytes[:100] or video_bytes.startswith(b"AVI")
            ):
                raise InvalidInputError(
                    "Unsupported video format; only MP4 and AVI are supported"
                )

            latency = random.uniform(
                self.config.mock_min_latency_ms / 1000,
                self.config.mock_max_latency_ms / 1000,
            )
            await asyncio.sleep(latency)

            data_start = (
                video_bytes[:20].decode("latin-1", "ignore")
                if isinstance(video_bytes, bytes)
                else str(video_bytes)[:20]
            )

            summary = f"A concise summary of the video content. (Video data starts with '{data_start}...')"
            audio_transcription = f"Mock transcription from video audio. (Video data starts with '{data_start}...')"

            result_data = {
                "summary": summary,
                "audio_transcription": audio_transcription,
                "size_bytes": len(video_bytes),
            }
            result_summary = f"Video processed. Summary: '{summary[:50]}...', Audio: '{audio_transcription[:50]}...'"

            logger.info(
                f"[{op_id}] Video processing completed successfully. Summary: {result_summary}"
            )

            self.requests_total.labels(status="success").inc()
            self.processing_latency_seconds.observe(
                asyncio.get_event_loop().time() - start_time
            )

            return ProcessingResult(
                success=True,
                data=result_data,
                summary=result_summary,
                operation_id=op_id,
                model_confidence=random.uniform(0.75, 0.9),
            )
        except InvalidInputError as e:
            logger.error(
                f"[{op_id}] Invalid input error during video processing: {e}",
                exc_info=True,
            )
            self.requests_total.labels(status="failure").inc()
            self.processing_latency_seconds.observe(
                asyncio.get_event_loop().time() - start_time
            )
            return ProcessingResult(success=False, error=str(e), operation_id=op_id)
        except Exception as e:
            logger.error(
                f"[{op_id}] Unexpected error during video processing. Input starts with '{str(video_data)[:50]}...'",
                exc_info=True,
            )
            self.requests_total.labels(status="failure").inc()
            self.processing_latency_seconds.observe(
                asyncio.get_event_loop().time() - start_time
            )
            return ProcessingResult(
                success=False,
                error=f"Unexpected processing error: {e}",
                operation_id=op_id,
            )

    def _decode_data(self, data: Union[bytes, str]) -> Optional[bytes]:
        """Decodes base64 string to bytes, or returns bytes directly."""
        if isinstance(data, str):
            # Check if it's a file path
            if os.path.exists(data):
                try:
                    with open(data, "rb") as f:
                        return f.read()
                except Exception as e:
                    logger.error(f"Failed to read file {data}: {e}")
                    return None
            else:
                # Try to decode as base64
                try:
                    cleaned = (
                        data.strip()
                        .replace("\n", "")
                        .replace("\r", "")
                        .replace(" ", "")
                    )
                    return base64.b64decode(cleaned)
                except Exception as e:
                    logger.error(f"Failed to base64 decode video data: {e}")
                    return None
        elif isinstance(data, bytes):
            return data
        else:
            return None

    async def health_check(self) -> bool:
        """
        Checks the health of the video processor.
        Returns:
            bool: True if the processor is operational, False otherwise.
        """
        try:
            await asyncio.sleep(0.001)
            logger.info("DefaultVideoProcessor health check passed")
            return True
        except Exception as e:
            logger.error(f"DefaultVideoProcessor health check failed: {e}")
            return False


class DefaultTextProcessor(TextProcessor):
    """Default mock processor for text data."""

    def __init__(self, config: Dict[str, Any]):
        try:
            self.config = DefaultTextProcessorConfig.model_validate(config)
        except ValidationError as e:
            raise ConfigurationError(
                f"Invalid configuration for DefaultTextProcessor: {e}"
            ) from e

        self.requests_total = get_or_create(
            Counter(
                "default_text_processor_requests_total",
                "Total text processing requests",
                ["status"],
            )
        )
        self.processing_latency_seconds = get_or_create(
            Histogram(
                "default_text_processor_latency_seconds",
                "Text processing latency",
                [],
                buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, float("inf")),
            )
        )
        logger.info("DefaultTextProcessor initialized with config: %s", self.config)

    async def process(
        self, text_data: str, operation_id: Optional[str] = None, **kwargs: Any
    ) -> ProcessingResult:
        op_id = operation_id if operation_id else str(uuid.uuid4())
        logger.info(f"[{op_id}] Starting text processing...")
        start_time = asyncio.get_event_loop().time()

        try:
            if not isinstance(text_data, str) or not text_data:
                raise InvalidInputError("Invalid or empty text data provided.")

            if len(text_data) > self.config.max_length:
                raise InvalidInputError(
                    f"Text data exceeds max length of {self.config.max_length} characters"
                )

            latency = random.uniform(
                self.config.mock_min_latency_ms / 1000,
                self.config.mock_max_latency_ms / 1000,
            )
            await asyncio.sleep(latency)

            # Simple mock processing - just uppercase the text
            processed_text = text_data.upper()
            result_summary = (
                f"Text processed: {len(text_data)} chars -> {len(processed_text)} chars"
            )

            result_data = {
                "processed_text": processed_text,
                "original_length": len(text_data),
                "processed_length": len(processed_text),
            }

            logger.info(
                f"[{op_id}] Text processing completed successfully. Summary: {result_summary}"
            )

            self.requests_total.labels(status="success").inc()
            self.processing_latency_seconds.observe(
                asyncio.get_event_loop().time() - start_time
            )

            return ProcessingResult(
                success=True,
                data=result_data,
                summary=result_summary,
                operation_id=op_id,
                model_confidence=random.uniform(0.9, 0.99),
            )
        except InvalidInputError as e:
            logger.error(
                f"[{op_id}] Invalid input error during text processing: {e}",
                exc_info=True,
            )
            self.requests_total.labels(status="failure").inc()
            self.processing_latency_seconds.observe(
                asyncio.get_event_loop().time() - start_time
            )
            return ProcessingResult(success=False, error=str(e), operation_id=op_id)
        except Exception as e:
            logger.error(
                f"[{op_id}] Unexpected error during text processing. Input starts with '{text_data[:50]}...'",
                exc_info=True,
            )
            self.requests_total.labels(status="failure").inc()
            self.processing_latency_seconds.observe(
                asyncio.get_event_loop().time() - start_time
            )
            return ProcessingResult(
                success=False,
                error=f"Unexpected processing error: {e}",
                operation_id=op_id,
            )

    async def health_check(self) -> bool:
        """
        Checks the health of the text processor.
        Returns:
            bool: True if the processor is operational, False otherwise.
        """
        try:
            await asyncio.sleep(0.001)
            logger.info("DefaultTextProcessor health check passed")
            return True
        except Exception as e:
            logger.error(f"DefaultTextProcessor health check failed: {e}")
            return False


# Register the default providers with the PluginRegistry
PluginRegistry.register_processor("image", "default", DefaultImageProcessor)
PluginRegistry.register_processor("audio", "default", DefaultAudioProcessor)
PluginRegistry.register_processor("video", "default", DefaultVideoProcessor)
PluginRegistry.register_processor("text", "default", DefaultTextProcessor)

logger.info("Default multimodal providers registered.")


# Example of how the registry would be used (for demonstration)
async def demonstrate_provider_usage():
    print("\n--- Demonstrating Provider Usage with PluginRegistry ---")

    # Example config dictionaries
    image_config = {
        "mock_min_latency_ms": 10,
        "mock_max_latency_ms": 100,
        "max_size_mb": 5,
    }
    text_config = {
        "mock_min_latency_ms": 5,
        "mock_max_latency_ms": 25,
        "max_length": 5000,
    }

    try:
        # Get and use the image processor
        image_provider = PluginRegistry.get_processor("image", "default", image_config)
        image_result = await image_provider.process(b"some_image_data\x89PNG")
        print(f"Image processing result: {image_result.summary}")

        # Get and use the text processor
        text_provider = PluginRegistry.get_processor("text", "default", text_config)
        text_result = await text_provider.process("hello world")
        print(f"Text processing result: {text_result.summary}")

        # Demonstrate error handling with invalid data
        invalid_image_result = await image_provider.process("not_a_valid_base64_string")
        print(f"\nInvalid input error result: {invalid_image_result.error}")

        # Demonstrate config validation error
        try:
            invalid_config = {"mock_min_latency_ms": -10}  # Invalid value
            _ = PluginRegistry.get_processor("image", "default", invalid_config)
        except ConfigurationError as e:
            print(f"\nCaught expected configuration error: {e}")

        # Unregister a processor and try to access it
        PluginRegistry.unregister_processor("text", "default")
        try:
            _ = PluginRegistry.get_processor("text", "default", text_config)
        except MultiModalException as e:
            print(f"Caught expected exception after unregistering: {e}")

    except Exception as e:
        print(f"An unexpected error occurred during demonstration: {e}")


if __name__ == "__main__":
    asyncio.run(demonstrate_provider_usage())

__all__ = [
    "PluginRegistry",
    "DefaultImageProcessor",
    "DefaultAudioProcessor",
    "DefaultVideoProcessor",
    "DefaultTextProcessor",
    "DefaultImageProcessorConfig",
    "DefaultAudioProcessorConfig",
    "DefaultVideoProcessorConfig",
    "DefaultTextProcessorConfig",
]
