# D:\SFE\self_fixing_engineer\arbiter\models\multi_modal_schemas.py

"""
multi_modal_schemas.py

Pydantic schemas for structured representation of multi-modal data analysis results.
These schemas define the data models for outputs from the MultiModalProcessor,
ensuring data consistency, validation, and ease of use with LLMs or Knowledge Graphs.
"""

import re
import logging
from pydantic import BaseModel, Field, HttpUrl, field_validator, ValidationError, constr, conlist, AnyUrl, ConfigDict, ValidationInfo
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timezone
from enum import Enum
from html import escape
from typing_extensions import Literal, Annotated
from .common import Severity

# Pinning pydantic<2 for explicit V1 API usage.

# Define a module-level logger
logger = logging.getLogger(__name__)
# Add a null handler to prevent "No handlers could be found" warning
# in case the library is used without a logger configured.
if not logger.handlers:
    logger.addHandler(logging.NullHandler())

def to_camel(string: str) -> str:
    """Converts a snake_case string to camelCase."""
    return re.sub(r'_([a-z])', lambda m: m.group(1).upper(), string)

# Enums for controlled vocabulary
class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    MIXED = "mixed"
    UNKNOWN = "unknown"

class BaseConfig(BaseModel):
    """
    Base configuration for Pydantic models to ensure consistent JSON serialization
    and camelCase alias generation.
    """
    model_config = ConfigDict(
        json_encoders={
            datetime: lambda v: v.isoformat(),
        },
        arbitrary_types_allowed=True,
        extra='forbid',
        alias_generator=to_camel, # Apply camelCase alias generator
        populate_by_name=True, # Allow both field name and alias
        use_enum_values=True,
        validate_assignment=True
    )

    @field_validator('*', mode='before')
    @classmethod
    def sanitize_text_fields(cls, v, info: ValidationInfo):
        """
        Sanitizes string fields to prevent potential XSS if rendered in a web context.
        NOTE: This validator runs on all fields to check the 'sanitize' flag.
        Keep the flag narrowly scoped to long free-text fields to avoid unexpected mutation.
        """
        if isinstance(v, str):
            field = cls.model_fields.get(info.field_name)
            if field and (getattr(field, 'json_schema_extra', None) or {}).get('sanitize'):
                return escape(v, quote=True)
        return v

class ImageOCRResult(BaseConfig):
    text: str = Field(..., description="The extracted text from the image.", json_schema_extra={"sanitize": True})
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Confidence score of the OCR result (0.0 to 1.0).")

class ImageCaptioningResult(BaseConfig):
    caption: str = Field(..., description="A descriptive caption generated for the image.", json_schema_extra={"sanitize": True})
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Confidence score of the caption (0.0 to 1.0).")

class ImageAnalysisResult(BaseConfig):
    kind: Literal['image'] = 'image'
    image_id: constr(min_length=1, max_length=200, pattern=r'^[a-zA-Z0-9._-]+$') = Field(..., description="Unique identifier for the analyzed image.")
    source_url: Optional[Union[HttpUrl, AnyUrl]] = Field(None, description="URL from which the image was obtained (can be HTTP or object store URI).")
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="UTC timestamp of the analysis.")
    ocr_result: Optional[ImageOCRResult] = Field(None, description="Results from Optical Character Recognition.")
    captioning_result: Optional[ImageCaptioningResult] = Field(None, description="Results from image captioning.")
    detected_objects: Optional[conlist(str, max_length=500)] = Field(None, description="List of objects detected in the image (e.g., ['cat', 'dog']).")
    face_detection_count: Optional[int] = Field(None, ge=0, description="Number of faces detected in the image.")
    raw_response: Optional[Dict[str, Any]] = Field(None, description="Raw response from the underlying ML model/API for debugging/inspection.")
    severity: Optional[Severity] = Field(None, description="An optional severity level for the analysis result.")
    
    @field_validator('timestamp_utc', mode='after')
    @classmethod
    def ensure_utc_timestamp(cls, v):
        """Ensures that timestamps are timezone-aware and in UTC."""
        if isinstance(v, datetime):
            if v.tzinfo is None:
                logger.warning("Timestamp %s is naive, assuming UTC.", v)
                return v.replace(tzinfo=timezone.utc)
            return v.astimezone(timezone.utc)
        # Let Pydantic handle parsing of strings and other types first
        return v


class AudioTranscriptionResult(BaseConfig):
    text: str = Field(..., description="The transcribed text from the audio.", json_schema_extra={"sanitize": True})
    language: Optional[str] = Field(None, min_length=2, max_length=10, description="Detected language of the audio (e.g., 'en', 'es').")
    duration_seconds: Optional[float] = Field(None, ge=0.0, description="Duration of the transcribed audio in seconds.")
    speakers: Optional[conlist(str, max_length=100)] = Field(None, description="List of identified speakers in the audio.")

class AudioAnalysisResult(BaseConfig):
    kind: Literal['audio'] = 'audio'
    audio_id: constr(min_length=1, max_length=200, pattern=r'^[a-zA-Z0-9._-]+$') = Field(..., description="Unique identifier for the analyzed audio.")
    source_url: Optional[Union[HttpUrl, AnyUrl]] = Field(None, description="URL from which the audio was obtained (can be HTTP or object store URI).")
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="UTC timestamp of the analysis.")
    transcription: Optional[AudioTranscriptionResult] = Field(None, description="Results from audio transcription.")
    sentiment: Optional[Sentiment] = Field(None, description="Overall sentiment detected in the audio (e.g., 'positive', 'neutral', 'negative').")
    keywords: Optional[conlist(str, max_length=500)] = Field(None, description="Extracted keywords from the audio content.")
    speaker_count: Optional[int] = Field(None, ge=0, description="Number of unique speakers identified.")
    raw_response: Optional[Dict[str, Any]] = Field(None, description="Raw response from the underlying ML model/API for debugging/inspection.")
    severity: Optional[Severity] = Field(None, description="An optional severity level for the analysis result.")

    @field_validator('speaker_count')
    @classmethod
    def check_speaker_count(cls, v, info: ValidationInfo):
        values = info.data
        tx = values.get('transcription')
        if v is not None and tx and tx.speakers and v != len(tx.speakers):
            raise ValueError(f"speaker_count ({v}) must equal number of speakers ({len(tx.speakers)}).")
        return v
    
    @field_validator('timestamp_utc', mode='after')
    @classmethod
    def ensure_utc_timestamp(cls, v):
        """Ensures that timestamps are timezone-aware and in UTC."""
        if isinstance(v, datetime):
            if v.tzinfo is None:
                logger.warning("Timestamp %s is naive, assuming UTC.", v)
                return v.replace(tzinfo=timezone.utc)
            return v.astimezone(timezone.utc)
        # Let Pydantic handle parsing of strings and other types first
        return v


class VideoSummaryResult(BaseConfig):
    summary_text: str = Field(..., description="A textual summary of the video content.", json_schema_extra={"sanitize": True})
    key_moments_timestamps: Optional[conlist(float, max_length=1000)] = Field(None, description="List of timestamps (in seconds) for key moments in the video.")
    chapters: Optional[conlist(Dict[str, Any], max_length=100)] = Field(None, description="List of identified chapters or segments with their summaries.")

    @field_validator('key_moments_timestamps')
    @classmethod
    def non_negative(cls, v):
        if v is not None:
            for timestamp in v:
                if timestamp is not None and timestamp < 0:
                    raise ValueError("Timestamps must be non-negative.")
        return v

class VideoAnalysisResult(BaseConfig):
    kind: Literal['video'] = 'video'
    video_id: constr(min_length=1, max_length=200, pattern=r'^[a-zA-Z0-9._-]+$') = Field(..., description="Unique identifier for the analyzed video.")
    source_url: Optional[Union[HttpUrl, AnyUrl]] = Field(None, description="URL from which the video was obtained (can be HTTP or object store URI).")
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="UTC timestamp of the analysis.")
    duration_seconds: Optional[float] = Field(None, ge=0.0, description="Duration of the video in seconds.")
    summary_result: Optional[VideoSummaryResult] = Field(None, description="Results from video summarization.")
    audio_transcription_result: Optional[AudioTranscriptionResult] = Field(None, description="Transcription of the audio track in the video.")
    main_entities: Optional[conlist(str, max_length=500)] = Field(None, description="List of main entities (people, objects, concepts) identified in the video.")
    raw_response: Optional[Dict[str, Any]] = Field(None, description="Raw response from the underlying ML model/API for debugging/inspection.")
    severity: Optional[Severity] = Field(None, description="An optional severity level for the analysis result.")

    @field_validator('timestamp_utc', mode='after')
    @classmethod
    def ensure_utc_timestamp(cls, v):
        """Ensures that timestamps are timezone-aware and in UTC."""
        if isinstance(v, datetime):
            if v.tzinfo is None:
                logger.warning("Timestamp %s is naive, assuming UTC.", v)
                return v.replace(tzinfo=timezone.utc)
            return v.astimezone(timezone.utc)
        # Let Pydantic handle parsing of strings and other types first
        return v

MultiModalAnalysisResult = Annotated[
    Union[ImageAnalysisResult, AudioAnalysisResult, VideoAnalysisResult],
    Field(discriminator='kind')
]

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO) # Configure logging for example usage

    print("--- Demonstrating Multi-Modal Schemas ---")

    # Example 1: Image Analysis
    try:
        image_analysis = ImageAnalysisResult(
            image_id="img_12345",
            source_url="s3://example-bucket/image.jpg",
            ocr_result=ImageOCRResult(text="Hello World", confidence=0.95),
            captioning_result=ImageCaptioningResult(caption="A person standing in front of a building.", confidence=0.88),
            detected_objects=["person", "building", "sky"],
            face_detection_count=1,
            timestamp_utc="2023-10-26T14:30:00Z" # Test string timestamp
        )
        print(f"\nImage Analysis Result:\n{image_analysis.model_dump_json(indent=2, by_alias=True)}")
        assert str(image_analysis.source_url) == "s3://example-bucket/image.jpg"
        assert image_analysis.timestamp_utc.tzinfo == timezone.utc
        assert image_analysis.face_detection_count == 1
        print("Image Analysis Example Validated.")
    except ValidationError as e:
        print(f"\nImage Analysis Validation Error: {e}")
    except Exception as e:
        print(f"\nImage Analysis Unexpected Error: {e}")

    # Example 2: Audio Analysis with Enum and sanitization
    try:
        audio_analysis = AudioAnalysisResult(
            audio_id="audio_abcde",
            source_url="https://example.com/audio.mp3",
            transcription=AudioTranscriptionResult(text="This is a <script>alert('xss')</script> test transcription for the audio file.", language="en", duration_seconds=15.5, speakers=["speaker1"]),
            sentiment=Sentiment.NEUTRAL, # Using Enum
            keywords=["test", "audio", "file"],
            speaker_count=1,
            severity=Severity.INFO
        )
        print(f"\nAudio Analysis Result:\n{audio_analysis.model_dump_json(indent=2, by_alias=True)}")
        assert audio_analysis.transcription.text == "This is a &lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt; test transcription for the audio file."
        assert audio_analysis.sentiment == Sentiment.NEUTRAL
        assert audio_analysis.severity == Severity.INFO
        print("Audio Analysis Example Validated.")
    except ValidationError as e:
        print(f"\nAudio Analysis Validation Error: {e}")
    except Exception as e:
        print(f"\nAudio Analysis Unexpected Error: {e}")

    # Example 3: Video Analysis
    try:
        video_analysis = VideoAnalysisResult(
            video_id="vid_67890",
            source_url="http://example.com/video.mp4",
            duration_seconds=120.0,
            summary_result=VideoSummaryResult(summary_text="The video shows a quick tutorial on Python programming.", key_moments_timestamps=[10.5, 45.2, 90.0]),
            audio_transcription_result=AudioTranscriptionResult(text="Welcome to this tutorial on Python.", language="en"),
            main_entities=["Python", "programming", "tutorial"],
            severity=Severity.HIGH
        )
        print(f"\nVideo Analysis Result:\n{video_analysis.model_dump_json(indent=2, by_alias=True)}")
        assert video_analysis.severity == Severity.HIGH
        print("Video Analysis Example Validated.")
    except ValidationError as e:
        print(f"\nVideo Analysis Validation Error: {e}")
    except Exception as e:
        print(f"\nVideo Analysis Unexpected Error: {e}")

    # Example 4: Validated Image with default timestamp
    try:
        validated_image = ImageAnalysisResult(image_id="img_validated_default_ts")
        print(f"\nValidated Image (with default timestamp):\n{validated_image.model_dump_json(indent=2, by_alias=True)}")
        assert validated_image.timestamp_utc.tzinfo == timezone.utc
        print("Default Timestamp Validation Validated.")
    except ValidationError as e:
        print(f"\nDefault Timestamp Validation Error: {e}")
    except Exception as e:
        print(f"\nDefault Timestamp Validation Unexpected Error: {e}")

    # Example 5: Generic MultiModalAnalysisResult
    if 'image_analysis' in locals():
        print(f"\nGeneric MultiModalAnalysisResult (Image): {image_analysis.model_dump()}")
    if 'video_analysis' in locals():
        print(f"\nGeneric MultiModalAnalysisResult (Video): {video_analysis.model_dump()}")

    # Example 6: Deserialization with camelCase input
    json_data_camel = {
        "imageId": "img_from_json_camel",
        "timestampUtc": "2023-10-27T10:00:00Z",
        "captioningResult": {"caption": "An image loaded from JSON with camelCase."}
    }
    try:
        deserialized_image_camel = ImageAnalysisResult.model_validate(json_data_camel) # Use model_validate for dict input
        print(f"\nDeserialized Image Result (from camelCase JSON):\n{deserialized_image_camel.model_dump_json(indent=2, by_alias=True)}")
        assert deserialized_image_camel.image_id == "img_from_json_camel" # Check snake_case access
        assert deserialized_image_camel.captioning_result.caption == "An image loaded from JSON with camelCase."
        print("Deserialization from camelCase JSON Validated.")
    except ValidationError as e:
        print(f"\nDeserialization Validation Error (camelCase): {e}")
    except Exception as e:
        print(f"\nDeserialization Unexpected Error (camelCase): {e}")

    # Example 7: Invalid URL
    try:
        # Pydantic's AnyUrl still requires a scheme, so 'not-a-valid-url' will fail
        ImageAnalysisResult(image_id="invalid_url_test", source_url="not-a-valid-url")
        assert False, "Should have raised ValidationError for invalid URL"
    except ValidationError as e:
        print(f"\nSuccessfully caught expected invalid URL error: {e}")

    # Example 8: Invalid Confidence Score
    try:
        ImageOCRResult(text="test", confidence=1.5)
        assert False, "Should have raised ValidationError for invalid confidence"
    except ValidationError as e:
        print(f"\nSuccessfully caught expected invalid confidence error: {e}")

    # Example 9: Invalid timestamp
    try:
        VideoSummaryResult(summary_text="test", key_moments_timestamps=[-10.0])
        assert False, "Should have raised ValidationError for negative timestamp"
    except ValidationError as e:
        print(f"\nSuccessfully caught expected negative timestamp error: {e}")

    # Example 10: Mismatched speaker count
    try:
        AudioAnalysisResult(audio_id="audio_mismatch", transcription=AudioTranscriptionResult(text="test", speakers=["s1", "s2"]), speaker_count=1)
        assert False, "Should have raised ValueError for mismatched speaker count"
    except ValidationError as e:
        print(f"\nSuccessfully caught expected speaker count mismatch error: {e}")

    # Example 11: Invalid ID
    try:
        AudioAnalysisResult(audio_id="id with spaces", transcription=AudioTranscriptionResult(text="test"))
        assert False, "Should have raised ValidationError for invalid ID regex"
    except ValidationError as e:
        print(f"\nSuccessfully caught expected invalid ID error: {e}")

    print("\n--- Multi-Modal Schemas Demonstration Complete ---")