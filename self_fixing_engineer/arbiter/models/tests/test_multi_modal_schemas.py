import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

# Import from the correct module
from multi_modal_schemas import (
    AudioAnalysisResult,
    AudioTranscriptionResult,
    BaseConfig,
    ImageAnalysisResult,
    ImageCaptioningResult,
    ImageOCRResult,
    Sentiment,
    Severity,
    VideoAnalysisResult,
    VideoSummaryResult,
    to_camel,
)
from pydantic import ValidationError

# Configure logging for tests
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Sample valid data for tests
SAMPLE_IMAGE_ANALYSIS = {
    "image_id": "img_12345",
    "source_url": "http://example.com/image.jpg",
    "timestamp_utc": datetime(2023, 10, 26, 14, 30, 0, tzinfo=timezone.utc),
    "ocr_result": {"text": "Hello World", "confidence": 0.95},
    "captioning_result": {
        "caption": "A person standing in front of a building.",
        "confidence": 0.88,
    },
    "detected_objects": ["person", "building"],
    "face_detection_count": 1,
    "raw_response": {"model": "test_model"},
}

SAMPLE_AUDIO_ANALYSIS = {
    "audio_id": "audio_abcde",
    "source_url": "http://example.com/audio.mp3",
    "timestamp_utc": datetime(2023, 10, 26, 15, 0, 0, tzinfo=timezone.utc),
    "transcription": {
        "text": "This is a test.",
        "language": "en",
        "duration_seconds": 15.5,
        "speakers": ["speaker1"],
    },
    "sentiment": Sentiment.NEUTRAL,
    "keywords": ["test", "audio"],
    "speaker_count": 1,
    "raw_response": {"model": "audio_model"},
}

SAMPLE_VIDEO_ANALYSIS = {
    "video_id": "vid_67890",
    "source_url": "http://example.com/video.mp4",
    "timestamp_utc": datetime(2023, 10, 26, 16, 0, 0, tzinfo=timezone.utc),
    "duration_seconds": 120.0,
    "summary_result": {
        "summary_text": "Python tutorial.",
        "key_moments_timestamps": [10.5, 45.2],
        "chapters": [{"title": "Intro", "start": 0.0}],
    },
    "audio_transcription_result": {"text": "Welcome to Python.", "language": "en"},
    "main_entities": ["Python", "tutorial"],
    "raw_response": {"model": "video_model"},
}


@pytest.fixture(autouse=True)
def clear_logging():
    """Clear logging handlers before each test."""
    logger.handlers = []
    logger.addHandler(logging.StreamHandler())
    yield


def test_to_camel_function():
    """Test to_camel utility function."""
    assert to_camel("snake_case") == "snakeCase"
    assert to_camel("already_camel") == "alreadyCamel"
    assert to_camel("multiple_words_here") == "multipleWordsHere"
    assert to_camel("") == ""
    assert to_camel("no_underscores") == "noUnderscores"


def test_base_config_sanitization():
    """Test text field sanitization in BaseConfig."""
    # The sanitization only applies to fields with json_schema_extra={"sanitize": True}
    ocr = ImageOCRResult(text="<script>alert('xss')</script>", confidence=0.95)
    assert ocr.text == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"


def test_base_config_timestamp_utc(caplog):
    """Test timestamp_utc validation in BaseConfig."""

    class TestModel(BaseConfig):
        timestamp_utc: datetime

    caplog.set_level(logging.WARNING)

    # Test string timestamp with UTC
    model = TestModel(timestamp_utc="2023-10-26T14:30:00Z")
    assert model.timestamp_utc.tzinfo == timezone.utc
    assert model.timestamp_utc == datetime(2023, 10, 26, 14, 30, 0, tzinfo=timezone.utc)

    # Test naive datetime
    caplog.clear()
    naive_dt = datetime(2023, 10, 26, 14, 30, 0)
    model = TestModel(timestamp_utc=naive_dt)
    assert model.timestamp_utc.tzinfo == timezone.utc
    assert model.timestamp_utc == datetime(2023, 10, 26, 14, 30, 0, tzinfo=timezone.utc)
    assert "naive, assuming UTC" in caplog.text

    # Test non-UTC timezone
    caplog.clear()
    non_utc_dt = datetime(2023, 10, 26, 14, 30, 0, tzinfo=timezone(timedelta(hours=2)))
    model = TestModel(timestamp_utc=non_utc_dt)
    assert model.timestamp_utc.tzinfo == timezone.utc
    # 14:30+02:00 should become 12:30+00:00
    assert model.timestamp_utc == datetime(2023, 10, 26, 12, 30, 0, tzinfo=timezone.utc)


def test_image_ocr_result_validation():
    """Test ImageOCRResult schema validation."""
    ocr = ImageOCRResult(text="Test OCR", confidence=0.95)
    assert ocr.text == "Test OCR"
    assert ocr.confidence == 0.95

    # Test confidence bounds
    with pytest.raises(ValidationError) as exc_info:
        ImageOCRResult(text="Test", confidence=1.5)
    assert "less than or equal to 1" in str(exc_info.value)

    # Test required field
    with pytest.raises(ValidationError) as exc_info:
        ImageOCRResult(confidence=0.5)
    assert "Field required" in str(exc_info.value)


def test_image_captioning_result_validation():
    """Test ImageCaptioningResult schema validation."""
    caption = ImageCaptioningResult(caption="A test caption", confidence=0.88)
    assert caption.caption == "A test caption"
    assert caption.confidence == 0.88

    with pytest.raises(ValidationError) as exc_info:
        ImageCaptioningResult(caption="Test", confidence=-0.1)
    assert "greater than or equal to 0" in str(exc_info.value)


def test_image_analysis_result_validation():
    """Test ImageAnalysisResult schema validation."""
    image = ImageAnalysisResult(**SAMPLE_IMAGE_ANALYSIS)
    assert image.image_id == "img_12345"
    assert image.source_url == "http://example.com/image.jpg"
    assert image.ocr_result.text == "Hello World"
    assert image.captioning_result.confidence == 0.88
    assert image.face_detection_count == 1
    assert image.kind == "image"  # Check discriminator field

    # Test minimal required fields
    minimal_image = ImageAnalysisResult(image_id="minimal_img")
    assert minimal_image.image_id == "minimal_img"
    assert minimal_image.timestamp_utc.tzinfo == timezone.utc

    # Test invalid URL
    with pytest.raises(ValidationError) as exc_info:
        ImageAnalysisResult(image_id="invalid", source_url="not-a-url")
    assert "URL" in str(exc_info.value) or "Invalid" in str(exc_info.value)

    # Test negative face count
    with pytest.raises(ValidationError) as exc_info:
        ImageAnalysisResult(image_id="invalid", face_detection_count=-1)
    assert "greater than or equal to 0" in str(exc_info.value)


def test_audio_transcription_result_validation():
    """Test AudioTranscriptionResult schema validation."""
    transcription = AudioTranscriptionResult(
        text="Test audio", language="en", duration_seconds=15.5, speakers=["speaker1"]
    )
    assert transcription.text == "Test audio"
    assert transcription.language == "en"
    assert transcription.duration_seconds == 15.5
    assert transcription.speakers == ["speaker1"]

    # Test negative duration
    with pytest.raises(ValidationError) as exc_info:
        AudioTranscriptionResult(text="Test", duration_seconds=-1.0)
    assert "greater than or equal to 0" in str(exc_info.value)

    # Test language length
    with pytest.raises(ValidationError) as exc_info:
        AudioTranscriptionResult(text="Test", language="e")
    assert "at least 2 characters" in str(exc_info.value)


def test_audio_analysis_result_validation():
    """Test AudioAnalysisResult schema validation."""
    audio = AudioAnalysisResult(**SAMPLE_AUDIO_ANALYSIS)
    assert audio.audio_id == "audio_abcde"
    assert audio.sentiment == Sentiment.NEUTRAL
    assert audio.transcription.text == "This is a test."
    assert audio.speaker_count == 1
    assert audio.kind == "audio"  # Check discriminator field

    # Test invalid sentiment
    with pytest.raises(ValidationError) as exc_info:
        AudioAnalysisResult(audio_id="invalid", sentiment="invalid_sentiment")
    # Pydantic V2 message format
    assert "Input should be" in str(exc_info.value)

    # Test negative speaker count
    with pytest.raises(ValidationError) as exc_info:
        AudioAnalysisResult(audio_id="invalid", speaker_count=-1)
    assert "greater than or equal to 0" in str(exc_info.value)


def test_video_summary_result_validation():
    """Test VideoSummaryResult schema validation."""
    summary = VideoSummaryResult(
        summary_text="Test summary",
        key_moments_timestamps=[10.5, 20.0],
        chapters=[{"title": "Intro", "start": 0.0}],
    )
    assert summary.summary_text == "Test summary"
    assert summary.key_moments_timestamps == [10.5, 20.0]
    assert summary.chapters[0]["title"] == "Intro"

    # Test required field
    with pytest.raises(ValidationError) as exc_info:
        VideoSummaryResult(key_moments_timestamps=[1.0])
    assert "Field required" in str(exc_info.value)

    # Test negative timestamp
    with pytest.raises(ValidationError) as exc_info:
        VideoSummaryResult(summary_text="test", key_moments_timestamps=[-10.0])
    assert "Timestamps must be non-negative" in str(exc_info.value)


def test_video_analysis_result_validation():
    """Test VideoAnalysisResult schema validation."""
    video = VideoAnalysisResult(**SAMPLE_VIDEO_ANALYSIS)
    assert video.video_id == "vid_67890"
    assert video.duration_seconds == 120.0
    assert video.summary_result.summary_text == "Python tutorial."
    assert video.audio_transcription_result.text == "Welcome to Python."
    assert video.kind == "video"  # Check discriminator field

    # Test negative duration
    with pytest.raises(ValidationError) as exc_info:
        VideoAnalysisResult(video_id="invalid", duration_seconds=-10.0)
    assert "greater than or equal to 0" in str(exc_info.value)


def test_multi_modal_analysis_result():
    """Test MultiModalAnalysisResult union type with discriminator."""
    # Create instances of each type
    image = ImageAnalysisResult(**SAMPLE_IMAGE_ANALYSIS)
    audio = AudioAnalysisResult(**SAMPLE_AUDIO_ANALYSIS)
    video = VideoAnalysisResult(**SAMPLE_VIDEO_ANALYSIS)

    # Check they are valid instances
    assert isinstance(image, ImageAnalysisResult)
    assert isinstance(audio, AudioAnalysisResult)
    assert isinstance(video, VideoAnalysisResult)

    # Check discriminator field
    assert image.kind == "image"
    assert audio.kind == "audio"
    assert video.kind == "video"

    # Test that each can be serialized and deserialized
    for obj in [image, audio, video]:
        json_str = obj.model_dump_json()
        parsed = json.loads(json_str)
        assert "kind" in parsed


def test_camel_case_serialization():
    """Test camelCase alias serialization."""
    image = ImageAnalysisResult(**SAMPLE_IMAGE_ANALYSIS)
    json_str = image.model_dump_json(by_alias=True)
    data_dict = json.loads(json_str)

    # Check camelCase fields
    assert "imageId" in data_dict
    assert "sourceUrl" in data_dict
    assert "timestampUtc" in data_dict
    assert "ocrResult" in data_dict

    # Ensure snake_case not used
    assert "image_id" not in data_dict
    assert "source_url" not in data_dict


def test_camel_case_deserialization():
    """Test deserialization with camelCase input."""
    json_data = {
        "imageId": "img_camel",
        "sourceUrl": "http://example.com/camel.jpg",
        "timestampUtc": "2023-10-27T10:00:00Z",
        "ocrResult": {"text": "Camel Case", "confidence": 0.9},
    }
    image = ImageAnalysisResult.model_validate(json_data)
    assert image.image_id == "img_camel"
    assert (
        str(image.source_url) == "http://example.com/camel.jpg/"
    )  # Note: Pydantic may normalize URLs
    assert image.ocr_result.text == "Camel Case"


def test_sanitization_xss_protection():
    """Test XSS sanitization for string fields."""
    # Test sanitization in transcription text
    audio = AudioAnalysisResult(
        audio_id="audio_xss",
        transcription=AudioTranscriptionResult(text="<script>alert('xss')</script>"),
    )
    assert audio.transcription.text == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"

    # Test sanitization in caption
    image = ImageAnalysisResult(
        image_id="img_xss",
        captioning_result=ImageCaptioningResult(caption="<script>alert('xss')</script>"),
    )
    assert image.captioning_result.caption == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"


def test_enum_usage():
    """Test proper use of Sentiment and Severity enums."""
    audio = AudioAnalysisResult(audio_id="audio_enum", sentiment=Sentiment.NEGATIVE)
    assert audio.sentiment == Sentiment.NEGATIVE
    assert audio.sentiment.value == "negative"

    # Test severity enum
    image = ImageAnalysisResult(image_id="img_severity", severity=Severity.HIGH)
    assert image.severity == Severity.HIGH
    assert image.severity.value == "high"

    # Test invalid enum value
    with pytest.raises(ValidationError) as exc_info:
        AudioAnalysisResult(audio_id="invalid", sentiment="invalid")
    assert "Input should be" in str(exc_info.value)


def test_timestamp_default():
    """Test default UTC timestamp."""
    image = ImageAnalysisResult(image_id="img_default_ts")
    assert image.timestamp_utc.tzinfo == timezone.utc
    # Check it's close to now
    time_diff = (datetime.now(timezone.utc) - image.timestamp_utc).total_seconds()
    assert abs(time_diff) < 1.0


def test_invalid_field_extra():
    """Test extra fields are forbidden."""
    with pytest.raises(ValidationError) as exc_info:
        ImageAnalysisResult(image_id="img_extra", extra_field="forbidden")
    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_field_types_and_constraints():
    """Test field type constraints and edge cases."""
    # Test valid edge cases
    image = ImageAnalysisResult(
        image_id="img_edge",
        face_detection_count=0,
        ocr_result=ImageOCRResult(text="Edge", confidence=0.0),
    )
    assert image.face_detection_count == 0
    assert image.ocr_result.confidence == 0.0

    # Test invalid types
    with pytest.raises(ValidationError) as exc_info:
        ImageAnalysisResult(image_id="img_invalid", face_detection_count="not_an_int")
    assert "Input should be a valid integer" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        ImageAnalysisResult(image_id="img_invalid", raw_response="not_a_dict")
    assert "Input should be a valid dictionary" in str(exc_info.value)


def test_speaker_count_validation():
    """Test speaker_count validation against transcription speakers."""
    # Valid: matching speaker count
    audio = AudioAnalysisResult(
        audio_id="audio_valid",
        transcription=AudioTranscriptionResult(text="test", speakers=["s1", "s2"]),
        speaker_count=2,
    )
    assert audio.speaker_count == 2

    # Invalid: mismatched speaker count
    with pytest.raises(ValidationError) as exc_info:
        AudioAnalysisResult(
            audio_id="audio_mismatch",
            transcription=AudioTranscriptionResult(text="test", speakers=["s1", "s2"]),
            speaker_count=3,
        )
    assert "must equal number of speakers" in str(exc_info.value)


def test_id_pattern_validation():
    """Test ID field pattern validation."""
    # Valid IDs
    valid_ids = ["test123", "test_123", "test-123", "test.123"]
    for valid_id in valid_ids:
        image = ImageAnalysisResult(image_id=valid_id)
        assert image.image_id == valid_id

    # Invalid IDs
    invalid_ids = ["test 123", "test@123", "test#123", ""]
    for invalid_id in invalid_ids:
        with pytest.raises(ValidationError):
            ImageAnalysisResult(image_id=invalid_id)


def test_list_max_length():
    """Test max_length constraints on list fields."""
    # Test within limit
    image = ImageAnalysisResult(image_id="img_list", detected_objects=["obj"] * 500)  # Max is 500
    assert len(image.detected_objects) == 500

    # Test exceeding limit
    with pytest.raises(ValidationError) as exc_info:
        ImageAnalysisResult(image_id="img_list_exceed", detected_objects=["obj"] * 501)
    assert "List should have at most 500 items" in str(exc_info.value)
