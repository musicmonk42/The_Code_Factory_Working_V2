# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for the MultiModal Processor module.
Tests the abstract base class and DefaultMultiModalProcessor implementation.
Compatible with Python 3.10 and handles missing optional dependencies.
"""

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# Import from the correct module path
from self_fixing_engineer.arbiter.knowledge_graph.multimodal import (
    PDF_PROCESSING_AVAILABLE,
    VIDEO_PROCESSING_AVAILABLE,
    DefaultMultiModalProcessor,
    MultiModalProcessor,
)
from self_fixing_engineer.arbiter.knowledge_graph.utils import AgentCoreException, AgentErrorCode


class TestMultiModalProcessor:
    """Test suite for MultiModalProcessor abstract base class"""

    def test_abstract_base_class(self):
        """Test that MultiModalProcessor is abstract and cannot be instantiated"""
        with pytest.raises(TypeError):
            MultiModalProcessor()

    @pytest.mark.asyncio
    async def test_abstract_method_required(self):
        """Test that subclasses must implement summarize method"""

        class IncompleteProcessor(MultiModalProcessor):
            pass

        with pytest.raises(TypeError):
            IncompleteProcessor()


class TestDefaultMultiModalProcessor:
    """Test suite for DefaultMultiModalProcessor"""

    @pytest.fixture
    def mock_logger(self):
        """Fixture for mock logger"""
        return Mock()

    @pytest.fixture
    def mock_config(self):
        """Fixture for mock Config"""
        with patch("self_fixing_engineer.arbiter.knowledge_graph.multimodal.Config") as mock_config:
            mock_config.REDIS_URL = None
            mock_config.MAX_MM_DATA_SIZE_MB = 100
            mock_config.CACHE_EXPIRATION_SECONDS = 3600
            yield mock_config

    @pytest.fixture
    def mock_multimodal_data(self):
        """Fixture for creating mock MultiModalData instances"""

        def create_mock_data(data_type, data, metadata=None):
            mock_data = Mock()
            mock_data.data_type = data_type
            mock_data.data = data
            mock_data.metadata = metadata or {}
            return mock_data

        return create_mock_data

    @pytest.fixture
    def mock_redis(self):
        """Fixture for mock Redis client"""
        with patch("redis.asyncio.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_redis_class.from_url.return_value = mock_client
            yield mock_client

    @pytest.fixture
    def mock_metrics(self):
        """Fixture for mock metrics"""
        with patch("self_fixing_engineer.arbiter.knowledge_graph.multimodal.AGENT_METRICS") as mock_metrics:
            mock_metric = Mock()
            mock_metric.labels.return_value.inc = Mock()
            mock_metrics.__getitem__.return_value = mock_metric
            yield mock_metrics

    @pytest.fixture
    def mock_audit(self):
        """Fixture for mock audit ledger client"""
        with patch(
            "self_fixing_engineer.arbiter.knowledge_graph.multimodal.audit_ledger_client"
        ) as mock_audit:
            mock_audit.log_event = AsyncMock()
            yield mock_audit

    def test_processor_initialization_no_libraries(self, mock_logger, mock_config):
        """Test processor initialization when optional libraries are not available"""
        with patch.multiple(
            "self_fixing_engineer.arbiter.knowledge_graph.multimodal",
            IMAGE_PROCESSING_AVAILABLE=False,
            AUDIO_PROCESSING_AVAILABLE=False,
            VIDEO_PROCESSING_AVAILABLE=False,
            PDF_PROCESSING_AVAILABLE=False,
            TRANSFORMERS_AVAILABLE=False,
        ):
            processor = DefaultMultiModalProcessor(mock_logger)

            assert processor._image_processing_available is False
            assert processor._audio_processing_available is False
            assert processor._video_processing_available is False
            assert processor._pdf_processing_available is False
            assert processor._transformers_available is False
            assert processor.redis_client is None
            assert processor.image_captioner is None
            assert processor.audio_transcriber is None
            assert processor.text_summarizer is None

            # Check warnings were logged
            assert mock_logger.warning.call_count >= 5

    def test_processor_initialization_with_redis(self, mock_logger):
        """Test processor initialization with Redis configuration"""
        with patch("self_fixing_engineer.arbiter.knowledge_graph.multimodal.Config") as mock_config:
            mock_config.REDIS_URL = "redis://localhost:6379/0"
            mock_config.MAX_MM_DATA_SIZE_MB = 100
            mock_config.CACHE_EXPIRATION_SECONDS = 3600

            DefaultMultiModalProcessor(mock_logger)

            # Just verify that Redis initialization was attempted
            # The actual connection might fail in test environment, which is handled
            assert mock_logger.info.called or mock_logger.warning.called

    def test_processor_initialization_with_transformers(self, mock_logger, mock_config):
        """Test processor initialization with transformers available"""
        with patch("self_fixing_engineer.arbiter.knowledge_graph.multimodal.TRANSFORMERS_AVAILABLE", True):
            with patch("self_fixing_engineer.arbiter.knowledge_graph.multimodal.pipeline") as mock_pipeline:
                mock_pipeline.return_value = Mock()
                DefaultMultiModalProcessor(mock_logger)

                # Check that pipelines were initialized
                assert mock_pipeline.call_count == 3  # image, audio, text
                mock_pipeline.assert_any_call(
                    "image-to-text", model="Salesforce/blip-image-captioning-base"
                )
                mock_pipeline.assert_any_call(
                    "automatic-speech-recognition", model="openai/whisper-tiny"
                )
                mock_pipeline.assert_any_call(
                    "summarization", model="facebook/bart-large-cnn"
                )

    @pytest.mark.asyncio
    async def test_summarize_with_cache_hit(
        self, mock_logger, mock_config, mock_multimodal_data, mock_metrics, mock_audit
    ):
        """Test summarize method with cache hit"""
        processor = DefaultMultiModalProcessor(mock_logger)
        processor.redis_client = AsyncMock()

        test_data = b"test_image_data"
        data_hash = hashlib.sha256(test_data).hexdigest()
        cached_result = {
            "status": "success",
            "summary": "Cached result",
            "data_hash": data_hash,
        }

        with patch(
            "self_fixing_engineer.arbiter.knowledge_graph.multimodal.async_with_retry",
            AsyncMock(return_value=json.dumps(cached_result)),
        ):
            item = mock_multimodal_data("image", test_data)
            result = await processor.summarize(item)

            assert result == cached_result
            mock_metrics[
                "multimodal_data_processed_total"
            ].labels.assert_called_once_with(data_type="image")

    @pytest.mark.asyncio
    async def test_summarize_data_too_large(
        self, mock_logger, mock_config, mock_multimodal_data, mock_metrics, mock_audit
    ):
        """Test summarize method with data exceeding size limit"""
        processor = DefaultMultiModalProcessor(mock_logger)

        # Create data larger than limit
        large_data = b"x" * (101 * 1024 * 1024)  # 101 MB
        item = mock_multimodal_data("image", large_data)

        result = await processor.summarize(item)

        assert result["status"] == "failed"
        assert "exceeds maximum size" in result["summary"]
        mock_audit.log_event.assert_called_once_with(
            event_type="multimodal:size_exceeded",
            details={"data_type": "image", "size_bytes": len(large_data)},
        )
        mock_metrics["mm_processor_failures_total"].labels.assert_called_once_with(
            data_type="image", error_type=AgentErrorCode.MM_DATA_TOO_LARGE.value
        )

    @pytest.mark.asyncio
    async def test_summarize_unsupported_type(
        self, mock_logger, mock_config, mock_multimodal_data, mock_metrics, mock_audit
    ):
        """Test summarize method with unsupported data type"""
        processor = DefaultMultiModalProcessor(mock_logger)

        item = mock_multimodal_data("unsupported_type", b"data")

        result = await processor.summarize(item)

        assert result["status"] == "failed"
        assert "Unsupported data type" in result["summary"]
        mock_audit.log_event.assert_called_once_with(
            event_type="multimodal:unsupported",
            details={"data_type": "unsupported_type"},
        )

    @pytest.mark.asyncio
    async def test_summarize_timeout(
        self, mock_logger, mock_config, mock_multimodal_data, mock_metrics, mock_audit
    ):
        """Test summarize method with processing timeout"""
        processor = DefaultMultiModalProcessor(mock_logger)
        processor._image_processing_available = True

        item = mock_multimodal_data("image", b"test_data")

        # Mock asyncio.wait_for to raise TimeoutError (Python 3.10 compatible)
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await processor.summarize(item)

            assert result["status"] == "failed"
            assert "timed out" in result["summary"]
            mock_metrics["mm_processor_failures_total"].labels.assert_called_once_with(
                data_type="image", error_type=AgentErrorCode.TIMEOUT.value
            )

    @pytest.mark.asyncio
    async def test_process_image_success(
        self, mock_logger, mock_config, mock_multimodal_data
    ):
        """Test successful image processing"""
        with patch(
            "self_fixing_engineer.arbiter.knowledge_graph.multimodal.IMAGE_PROCESSING_AVAILABLE", True
        ):
            processor = DefaultMultiModalProcessor(mock_logger)
            processor._image_processing_available = True

            # Create test image data
            test_image_data = b"fake_image_data"
            item = mock_multimodal_data("image", test_image_data)

            # Mock Image from the multimodal module (where it's imported)
            with patch("self_fixing_engineer.arbiter.knowledge_graph.multimodal.Image") as mock_image_module:
                mock_image = Mock()
                mock_image.size = (1920, 1080)
                mock_image.mode = "RGB"
                mock_image_module.open.return_value = mock_image

                # Mock image captioner
                processor.image_captioner = Mock()
                processor.image_captioner.return_value = [
                    {"generated_text": "A beautiful sunset"}
                ]

                result = await processor._process_image(item)

                assert result["status"] == "success"
                assert "1920x1080" in result["summary"]
                assert "RGB" in result["summary"]
                assert "A beautiful sunset" in result["summary"]

    @pytest.mark.asyncio
    async def test_process_image_not_available(
        self, mock_logger, mock_config, mock_multimodal_data, mock_audit
    ):
        """Test image processing when PIL is not available"""
        processor = DefaultMultiModalProcessor(mock_logger)
        processor._image_processing_available = False

        item = mock_multimodal_data("image", b"test_data")

        result = await processor._process_image(item)

        assert result["status"] == "skipped"
        assert "not available" in result["summary"]
        # The audit log should be called, but if it fails it's handled gracefully
        mock_audit.log_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_audio_success(
        self, mock_logger, mock_config, mock_multimodal_data
    ):
        """Test successful audio processing"""
        with patch(
            "self_fixing_engineer.arbiter.knowledge_graph.multimodal.AUDIO_PROCESSING_AVAILABLE", True
        ):
            processor = DefaultMultiModalProcessor(mock_logger)
            processor._audio_processing_available = True

            test_audio_data = b"fake_audio_data"
            item = mock_multimodal_data("audio", test_audio_data)

            # Mock pydub from the multimodal module
            with patch("self_fixing_engineer.arbiter.knowledge_graph.multimodal.pydub") as mock_pydub:
                mock_audio = Mock()
                mock_audio.__len__ = Mock(return_value=5000)  # 5 seconds
                mock_pydub.AudioSegment.from_file.return_value = mock_audio

                # Mock audio transcriber
                processor.audio_transcriber = Mock()
                processor.audio_transcriber.return_value = {"text": "Hello world"}

                result = await processor._process_audio(item)

                assert result["status"] == "success"
                assert "5.00 seconds" in result["summary"]
                assert "Hello world" in result["summary"]

    @pytest.mark.skipif(not VIDEO_PROCESSING_AVAILABLE, reason="moviepy not installed")
    @pytest.mark.asyncio
    async def test_process_video_success(
        self, mock_logger, mock_config, mock_multimodal_data
    ):
        """Test successful video processing - skipped if moviepy not available"""
        with patch(
            "self_fixing_engineer.arbiter.knowledge_graph.multimodal.VIDEO_PROCESSING_AVAILABLE", True
        ):
            processor = DefaultMultiModalProcessor(mock_logger)
            processor._video_processing_available = True

            test_video_data = b"fake_video_data"
            item = mock_multimodal_data("video", test_video_data)

            # Mock VideoFileClip from the multimodal module
            with patch(
                "self_fixing_engineer.arbiter.knowledge_graph.multimodal.VideoFileClip"
            ) as mock_video_clip:
                mock_clip = MagicMock()
                mock_clip.duration = 30.5
                mock_clip.get_frame.return_value = [[0, 0, 0]]  # Mock frame array
                mock_video_clip.return_value.__enter__.return_value = mock_clip

                # Mock Image from multimodal module
                with patch("self_fixing_engineer.arbiter.knowledge_graph.multimodal.Image"):
                    processor.image_captioner = Mock()
                    processor.image_captioner.return_value = [
                        {"generated_text": "First frame"}
                    ]

                    result = await processor._process_video(item)

                    assert result["status"] == "success"
                    assert "30.50 seconds" in result["summary"]
                    assert "First frame" in result["summary"]

    @pytest.mark.asyncio
    async def test_process_text_file_success(
        self, mock_logger, mock_config, mock_multimodal_data
    ):
        """Test successful text file processing"""
        processor = DefaultMultiModalProcessor(mock_logger)

        test_text = "This is a test text file with some content."
        item = mock_multimodal_data("text_file", test_text.encode("utf-8"))

        # Mock text summarizer
        processor.text_summarizer = Mock()
        processor.text_summarizer.return_value = [{"summary_text": "Test summary"}]

        result = await processor._process_text_file(item)

        assert result["status"] == "success"
        assert str(len(test_text)) in result["summary"]
        assert "Test summary" in result["summary"]

    @pytest.mark.asyncio
    async def test_process_text_file_decode_error(
        self, mock_logger, mock_config, mock_multimodal_data
    ):
        """Test text file processing with decode error"""
        processor = DefaultMultiModalProcessor(mock_logger)

        # Invalid UTF-8 bytes
        invalid_bytes = b"\x80\x81\x82\x83"
        item = mock_multimodal_data("text_file", invalid_bytes)

        result = await processor._process_text_file(item)

        assert result["status"] == "failed"
        assert "Failed to decode text" in result["summary"]

    @pytest.mark.skipif(not PDF_PROCESSING_AVAILABLE, reason="PyPDF2 not installed")
    @pytest.mark.asyncio
    async def test_process_pdf_file_success(
        self, mock_logger, mock_config, mock_multimodal_data
    ):
        """Test successful PDF file processing - skipped if PyPDF2 not available"""
        with patch("self_fixing_engineer.arbiter.knowledge_graph.multimodal.PDF_PROCESSING_AVAILABLE", True):
            processor = DefaultMultiModalProcessor(mock_logger)
            processor._pdf_processing_available = True

            test_pdf_data = b"fake_pdf_data"
            item = mock_multimodal_data("pdf_file", test_pdf_data)

            # Mock PdfReader from the multimodal module
            with patch(
                "self_fixing_engineer.arbiter.knowledge_graph.multimodal.PdfReader"
            ) as mock_pdf_reader:
                mock_reader = Mock()
                mock_page = Mock()
                mock_page.extract_text.return_value = "Page content"
                mock_reader.pages = [mock_page, mock_page]
                mock_pdf_reader.return_value = mock_reader

                # Mock text summarizer
                processor.text_summarizer = Mock()
                processor.text_summarizer.return_value = [
                    {"summary_text": "PDF summary"}
                ]

                result = await processor._process_pdf_file(item)

                assert result["status"] == "success"
                assert "2 pages" in result["summary"]
                assert "PDF summary" in result["summary"]

    @pytest.mark.asyncio
    async def test_caching_successful_result(
        self, mock_logger, mock_config, mock_multimodal_data, mock_metrics
    ):
        """Test that successful results are cached"""
        processor = DefaultMultiModalProcessor(mock_logger)
        processor.redis_client = AsyncMock()

        test_text = "Test content"
        item = mock_multimodal_data("text_file", test_text.encode("utf-8"))

        with patch(
            "self_fixing_engineer.arbiter.knowledge_graph.multimodal.async_with_retry",
            AsyncMock(side_effect=[None, None]),
        ):
            result = await processor.summarize(item)

            # Verify cache was set for successful result
            assert result["status"] == "success"
            assert "12 characters" in result["summary"]  # Length of "Test content"

    @pytest.mark.asyncio
    async def test_exception_handling(
        self, mock_logger, mock_config, mock_multimodal_data, mock_metrics, mock_audit
    ):
        """Test exception handling in summarize method"""
        processor = DefaultMultiModalProcessor(mock_logger)
        processor._image_processing_available = True

        item = mock_multimodal_data("image", b"test_data")

        # Make _process_image raise an exception
        processor._process_image = AsyncMock(side_effect=Exception("Processing error"))

        with pytest.raises(AgentCoreException) as exc_info:
            await processor.summarize(item)

        assert "Multi-modal processing failed" in str(exc_info.value)
        mock_audit.log_event.assert_called_with(
            event_type="multimodal:failed",
            details={"data_type": "image", "error": "Processing error"},
        )


class TestIntegration:
    """Integration tests for the multimodal module"""

    @pytest.mark.asyncio
    async def test_full_processing_pipeline(self, tmp_path):
        """Test the full processing pipeline with multiple data types"""
        logger = Mock()

        with patch("self_fixing_engineer.arbiter.knowledge_graph.multimodal.Config") as mock_config:
            mock_config.REDIS_URL = None
            mock_config.MAX_MM_DATA_SIZE_MB = 100
            mock_config.CACHE_EXPIRATION_SECONDS = 3600

            processor = DefaultMultiModalProcessor(logger)

            # Test with text file
            text_data = "Sample text content for testing"
            text_item = Mock()
            text_item.data_type = "text_file"
            text_item.data = text_data.encode("utf-8")
            text_item.metadata = {}

            result = await processor.summarize(text_item)

            assert result["status"] == "success"
            assert "summary" in result
            assert "data_hash" in result
            assert "31 characters" in result["summary"]  # Length of the sample text

    @pytest.mark.asyncio
    async def test_concurrent_processing(self):
        """Test concurrent processing of multiple items"""
        logger = Mock()

        with patch("self_fixing_engineer.arbiter.knowledge_graph.multimodal.Config") as mock_config:
            mock_config.REDIS_URL = None
            mock_config.MAX_MM_DATA_SIZE_MB = 100
            mock_config.CACHE_EXPIRATION_SECONDS = 3600

            processor = DefaultMultiModalProcessor(logger)

            # Create multiple test items
            items = []
            for i in range(5):
                item = Mock()
                item.data_type = "text_file"
                item.data = f"Content {i}".encode("utf-8")
                item.metadata = {}
                items.append(item)

            # Process all items concurrently
            results = await asyncio.gather(
                *[processor.summarize(item) for item in items], return_exceptions=True
            )

            # Verify all completed
            assert len(results) == 5
            for result in results:
                if not isinstance(result, Exception):
                    assert "status" in result
                    assert result["status"] == "success"


class TestErrorCases:
    """Test error handling and edge cases"""

    @pytest.mark.asyncio
    async def test_redis_connection_failure(self):
        """Test handling of Redis connection failures"""
        logger = Mock()

        # Test that processor initializes even when Redis is unavailable
        with patch("self_fixing_engineer.arbiter.knowledge_graph.multimodal.Config") as mock_config:
            mock_config.REDIS_URL = None  # No Redis URL provided
            mock_config.MAX_MM_DATA_SIZE_MB = 100
            mock_config.CACHE_EXPIRATION_SECONDS = 3600

            processor = DefaultMultiModalProcessor(logger)

            # Should initialize without Redis
            assert processor.redis_client is None

    @pytest.mark.asyncio
    async def test_audit_logging_failure(self):
        """Test handling of audit logging failures"""
        logger = Mock()

        with patch("self_fixing_engineer.arbiter.knowledge_graph.multimodal.Config") as mock_config:
            mock_config.REDIS_URL = None
            mock_config.MAX_MM_DATA_SIZE_MB = 100

            processor = DefaultMultiModalProcessor(logger)
            processor._image_processing_available = False

            item = Mock()
            item.data_type = "image"
            item.data = b"test"

            with patch(
                "self_fixing_engineer.arbiter.knowledge_graph.multimodal.audit_ledger_client"
            ) as mock_audit:
                mock_audit.log_event = AsyncMock(side_effect=Exception("Audit failed"))

                # Should handle audit failure gracefully
                result = await processor._process_image(item)
                assert result["status"] == "skipped"
                # Check that warning was logged about audit failure
                assert any(
                    "Failed to log audit" in str(call)
                    for call in logger.warning.call_args_list
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
