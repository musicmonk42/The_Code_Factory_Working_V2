# D:\Code_Factory\self_fixing_engineer\arbiter\knowledge_graph\multimodal.py
import hashlib
import asyncio
import tempfile
import os
from abc import ABC, abstractmethod
from typing import Dict, Any
import logging
import json
import io

# Conditional imports for external libraries
try:
    from PIL import Image, UnidentifiedImageError

    IMAGE_PROCESSING_AVAILABLE = True
except ImportError:
    IMAGE_PROCESSING_AVAILABLE = False

    class UnidentifiedImageError(Exception):
        pass


try:
    import pydub
    from pydub.exceptions import CouldntDecodeError

    AUDIO_PROCESSING_AVAILABLE = True
except ImportError:
    AUDIO_PROCESSING_AVAILABLE = False

    class CouldntDecodeError(Exception):
        pass


try:
    from transformers import pipeline

    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False
    pipeline = None

try:
    from moviepy.editor import VideoFileClip

    VIDEO_PROCESSING_AVAILABLE = True
except ImportError:
    VIDEO_PROCESSING_AVAILABLE = False
    VideoFileClip = None

try:
    from PyPDF2 import PdfReader

    PDF_PROCESSING_AVAILABLE = True
except ImportError:
    PDF_PROCESSING_AVAILABLE = False
    PdfReader = None

# Local imports
from .utils import (
    AgentCoreException,
    AgentErrorCode,
    trace_id_var,
    AGENT_METRICS,
    audit_ledger_client,
    async_with_retry,
)
from .config import MultiModalData, Config


# A base class for multimodal processing.
class MultiModalProcessor(ABC):
    """
    Abstract base class for processing multi-modal data.
    """

    @abstractmethod
    async def summarize(self, item: MultiModalData) -> Dict[str, Any]:
        """
        Processes a MultiModalData item and returns a summary.
        """
        pass


# The default implementation for multi-modal processing.
class DefaultMultiModalProcessor(MultiModalProcessor):
    """
    A concrete implementation of the MultiModalProcessor.
    Relies on external libraries for specific data types and includes caching, timeouts, metrics, and auditing.
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger
        self._image_processing_available = IMAGE_PROCESSING_AVAILABLE
        self._audio_processing_available = AUDIO_PROCESSING_AVAILABLE
        self._transformers_available = TRANSFORMERS_AVAILABLE
        self._video_processing_available = VIDEO_PROCESSING_AVAILABLE
        self._pdf_processing_available = PDF_PROCESSING_AVAILABLE
        if not self._image_processing_available:
            self._logger.warning("Pillow not found. Image processing is disabled.")
        if not self._audio_processing_available:
            self._logger.warning("pydub not found. Audio processing is disabled.")
        if not self._transformers_available:
            self._logger.warning("transformers not found. Advanced summarization is disabled.")
        if not self._video_processing_available:
            self._logger.warning("moviepy not found. Video processing is disabled.")
        if not self._pdf_processing_available:
            self._logger.warning("PyPDF2 not found. PDF processing is disabled.")

        # Redis for caching
        self.redis_client = None
        if Config.REDIS_URL:
            try:
                from redis.asyncio import Redis

                redis_url = (
                    str(Config.REDIS_URL)
                    if hasattr(Config.REDIS_URL, "__str__")
                    else Config.REDIS_URL
                )
                self.redis_client = Redis.from_url(redis_url)
                self._logger.info("Redis client initialized for multimodal caching.")
            except Exception as e:
                self._logger.warning(
                    f"Failed to initialize Redis client: {e}. Proceeding without cache."
                )
                self.redis_client = None

        # Initialize transformers pipelines if available (lazy-loaded to avoid blocking)
        # These will be initialized on first use to prevent blocking during __init__
        self.image_captioner = None
        self.audio_transcriber = None
        self.text_summarizer = None
        self._models_initialized = False

    async def _ensure_models_initialized(self):
        """Lazy-load transformer models on first use to avoid blocking during __init__."""
        if self._models_initialized or not self._transformers_available:
            return

        try:
            # Run model initialization in executor to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            self.image_captioner = await loop.run_in_executor(
                None,
                lambda: pipeline("image-to-text", model="Salesforce/blip-image-captioning-base"),
            )
            self.audio_transcriber = await loop.run_in_executor(
                None,
                lambda: pipeline("automatic-speech-recognition", model="openai/whisper-tiny"),
            )
            self.text_summarizer = await loop.run_in_executor(
                None, lambda: pipeline("summarization", model="facebook/bart-large-cnn")
            )
            self._models_initialized = True
            self._logger.info("Transformer models initialized successfully.")
        except Exception as e:
            self._logger.warning(
                f"Failed to initialize transformer models: {e}. Proceeding without them."
            )
            self._models_initialized = True  # Mark as attempted to avoid retrying

    async def summarize(self, item: MultiModalData) -> Dict[str, Any]:
        """
        Dispatches processing based on the data type and returns a summary.

        Args:
            item: The MultiModalData item to summarize.

        Returns:
            A dictionary containing the processing status and summary.
        """
        # Ensure models are loaded before processing
        await self._ensure_models_initialized()
        self._logger.debug(
            f"Starting to summarize multi-modal item: {item.data_type} with size {len(item.data)} bytes. Trace ID: {trace_id_var.get()}"
        )

        data_hash = hashlib.sha256(item.data).hexdigest()

        # Cache check
        cache_key = f"mm_summary:{data_hash}"
        if self.redis_client:
            try:
                cached_result = await async_with_retry(
                    lambda: self.redis_client.get(cache_key), retries=3, delay=1
                )
                if cached_result:
                    AGENT_METRICS["multimodal_data_processed_total"].labels(
                        data_type=item.data_type
                    ).inc()
                    return json.loads(cached_result)
            except Exception as e:
                self._logger.warning(f"Redis cache check failed: {e}. Proceeding without cache.")

        # Size validation
        if len(item.data) > Config.MAX_MM_DATA_SIZE_MB * 1024 * 1024:
            self._logger.warning(
                f"MultiModalData too large ({len(item.data) / (1024*1024):.2f}MB). Trace ID: {trace_id_var.get()}"
            )
            AGENT_METRICS["mm_processor_failures_total"].labels(
                data_type=item.data_type,
                error_type=AgentErrorCode.MM_DATA_TOO_LARGE.value,
            ).inc()
            await audit_ledger_client.log_event(
                event_type="multimodal:size_exceeded",
                details={"data_type": item.data_type, "size_bytes": len(item.data)},
            )
            return {
                "status": "failed",
                "summary": "Data exceeds maximum size limit.",
                "data_hash": data_hash,
            }

        # Processing with timeout - using wait_for for Python 3.10 compatibility
        try:
            processor_map = {
                "image": self._process_image,
                "audio": self._process_audio,
                "video": self._process_video,
                "text_file": self._process_text_file,
                "pdf_file": self._process_pdf_file,
            }

            processor = processor_map.get(item.data_type)
            if processor:
                # Use asyncio.wait_for instead of asyncio.timeout for Python 3.10
                result = await asyncio.wait_for(processor(item), timeout=15)
                # Cache the successful result
                if self.redis_client and result.get("status") == "success":
                    try:
                        await async_with_retry(
                            lambda: self.redis_client.setex(
                                cache_key,
                                Config.CACHE_EXPIRATION_SECONDS,
                                json.dumps(result),
                            ),
                            retries=3,
                            delay=1,
                        )
                    except Exception as e:
                        self._logger.warning(f"Failed to cache result: {e}")
                return result

            self._logger.warning(
                f"Unsupported data type: {item.data_type}. Trace ID: {trace_id_var.get()}"
            )
            AGENT_METRICS["mm_processor_failures_total"].labels(
                data_type=item.data_type,
                error_type=AgentErrorCode.MM_UNSUPPORTED_DATA.value,
            ).inc()
            await audit_ledger_client.log_event(
                event_type="multimodal:unsupported",
                details={"data_type": item.data_type},
            )
            return {
                "status": "failed",
                "summary": "Unsupported data type.",
                "data_hash": data_hash,
            }
        except asyncio.TimeoutError:
            self._logger.error(
                f"Timeout processing {item.data_type}. Trace ID: {trace_id_var.get()}"
            )
            AGENT_METRICS["mm_processor_failures_total"].labels(
                data_type=item.data_type, error_type=AgentErrorCode.TIMEOUT.value
            ).inc()
            await audit_ledger_client.log_event(
                event_type="multimodal:timeout", details={"data_type": item.data_type}
            )
            return {
                "status": "failed",
                "summary": "Processing timed out.",
                "data_hash": data_hash,
            }
        except Exception as e:
            self._logger.error(
                f"Unexpected error processing {item.data_type}: {e}. Trace ID: {trace_id_var.get()}",
                exc_info=True,
            )
            AGENT_METRICS["mm_processor_failures_total"].labels(
                data_type=item.data_type,
                error_type=AgentErrorCode.MM_PROCESSING_FAILED.value,
            ).inc()
            await audit_ledger_client.log_event(
                event_type="multimodal:failed",
                details={"data_type": item.data_type, "error": str(e)},
            )
            raise AgentCoreException(
                f"Multi-modal processing failed: {e}",
                code=AgentErrorCode.MM_PROCESSING_FAILED,
                original_exception=e,
            ) from e

    async def _process_image(self, item: MultiModalData) -> Dict[str, Any]:
        data_hash = hashlib.sha256(item.data).hexdigest()
        if not self._image_processing_available:
            self._logger.warning("Image processing disabled. Returning basic summary.")
            try:
                await audit_ledger_client.log_event(
                    event_type="multimodal:skipped",
                    details={"data_type": "image", "reason": "missing library"},
                )
            except Exception as e:
                self._logger.warning(f"Failed to log audit event: {e}")
            return {
                "status": "skipped",
                "summary": "Image processing not available.",
                "data_hash": data_hash,
            }

        try:
            image = Image.open(io.BytesIO(item.data))
            width, height = image.size
            mode = image.mode
            summary = f"Image: {mode} mode, {width}x{height} pixels."

            if self.image_captioner:
                caption = self.image_captioner(image)[0]["generated_text"]
                summary += f" Caption: {caption}"

            return {"status": "success", "summary": summary, "data_hash": data_hash}
        except UnidentifiedImageError as e:
            return {
                "status": "failed",
                "summary": f"Failed to identify image: {e}",
                "data_hash": data_hash,
            }
        except Exception as e:
            raise AgentCoreException(
                f"Error processing image: {e}",
                code=AgentErrorCode.MM_PROCESSING_FAILED,
                original_exception=e,
            ) from e

    async def _process_audio(self, item: MultiModalData) -> Dict[str, Any]:
        data_hash = hashlib.sha256(item.data).hexdigest()
        if not self._audio_processing_available:
            self._logger.warning("Audio processing disabled. Returning basic summary.")
            try:
                await audit_ledger_client.log_event(
                    event_type="multimodal:skipped",
                    details={"data_type": "audio", "reason": "missing library"},
                )
            except Exception as e:
                self._logger.warning(f"Failed to log audit event: {e}")
            return {
                "status": "skipped",
                "summary": "Audio processing not available.",
                "data_hash": data_hash,
            }

        try:
            audio = pydub.AudioSegment.from_file(io.BytesIO(item.data))
            duration = len(audio) / 1000.0
            summary = f"Audio: {duration:.2f} seconds."

            if self.audio_transcriber:
                transcription = self.audio_transcriber(item.data)["text"]
                summary += f" Transcription: {transcription}"

            return {"status": "success", "summary": summary, "data_hash": data_hash}
        except CouldntDecodeError as e:
            return {
                "status": "failed",
                "summary": f"Failed to decode audio: {e}",
                "data_hash": data_hash,
            }
        except Exception as e:
            raise AgentCoreException(
                f"Error processing audio: {e}",
                code=AgentErrorCode.MM_PROCESSING_FAILED,
                original_exception=e,
            ) from e

    async def _process_video(self, item: MultiModalData) -> Dict[str, Any]:
        data_hash = hashlib.sha256(item.data).hexdigest()
        if not self._video_processing_available:
            self._logger.warning("Video processing disabled. Returning basic summary.")
            try:
                await audit_ledger_client.log_event(
                    event_type="multimodal:skipped",
                    details={"data_type": "video", "reason": "missing library"},
                )
            except Exception as e:
                self._logger.warning(f"Failed to log audit event: {e}")
            return {
                "status": "skipped",
                "summary": "Video processing not available.",
                "data_hash": data_hash,
            }

        # VideoFileClip expects a file path, not a BytesIO object
        # Write to a temporary file first
        temp_file_path = None  # Initialize to None; only set after file is created
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_file:
                temp_file.write(item.data)
                temp_file_path = temp_file.name  # Only set if write succeeds

            with VideoFileClip(temp_file_path) as clip:
                duration = clip.duration
                summary = f"Video: {duration:.2f} seconds."

                if self.image_captioner:
                    frame = clip.get_frame(0)
                    caption = self.image_captioner(Image.fromarray(frame))[0]["generated_text"]
                    summary += f" First frame caption: {caption}"

            return {"status": "success", "summary": summary, "data_hash": data_hash}
        except Exception as e:
            return {
                "status": "failed",
                "summary": f"Failed to process video: {e}",
                "data_hash": data_hash,
            }
        finally:
            # Clean up temporary file
            if temp_file_path is not None:
                try:
                    os.unlink(temp_file_path)
                except Exception:
                    pass

    async def _process_text_file(self, item: MultiModalData) -> Dict[str, Any]:
        data_hash = hashlib.sha256(item.data).hexdigest()
        try:
            content = item.data.decode("utf-8")
            summary = f"Text file: {len(content)} characters. Preview: {content[:200]}"

            if self.text_summarizer:
                summarization = self.text_summarizer(content[:2000])[0]["summary_text"]
                summary += f" Summary: {summarization}"

            return {"status": "success", "summary": summary, "data_hash": data_hash}
        except UnicodeDecodeError as e:
            return {
                "status": "failed",
                "summary": f"Failed to decode text: {e}",
                "data_hash": data_hash,
            }
        except Exception as e:
            raise AgentCoreException(
                f"Error processing text file: {e}",
                code=AgentErrorCode.MM_PROCESSING_FAILED,
                original_exception=e,
            ) from e

    async def _process_pdf_file(self, item: MultiModalData) -> Dict[str, Any]:
        data_hash = hashlib.sha256(item.data).hexdigest()
        if not self._pdf_processing_available:
            self._logger.warning("PDF processing disabled. Returning basic summary.")
            try:
                await audit_ledger_client.log_event(
                    event_type="multimodal:skipped",
                    details={"data_type": "pdf_file", "reason": "missing library"},
                )
            except Exception as e:
                self._logger.warning(f"Failed to log audit event: {e}")
            return {
                "status": "skipped",
                "summary": "PDF processing not available.",
                "data_hash": data_hash,
            }

        try:
            reader = PdfReader(io.BytesIO(item.data))
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            summary = f"PDF: {len(reader.pages)} pages. Preview: {text[:200]}"

            if self.text_summarizer:
                summarization = self.text_summarizer(text[:2000])[0]["summary_text"]
                summary += f" Summary: {summarization}"

            return {"status": "success", "summary": summary, "data_hash": data_hash}
        except Exception as e:
            return {
                "status": "failed",
                "summary": f"Failed to process PDF: {e}",
                "data_hash": data_hash,
            }
