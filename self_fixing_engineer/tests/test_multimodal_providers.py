# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for OpenAI and xAI multimodal providers.

Tests are designed to work without real API keys by mocking aiohttp.ClientSession.post.
"""

import asyncio
import importlib
import importlib.util
import json
import os
import sys
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the interface module directly to bypass plugins/__init__.py dependency chain
_interface_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "arbiter", "plugins", "multimodal", "interface.py"
)
_spec = importlib.util.spec_from_file_location(
    "self_fixing_engineer.arbiter.plugins.multimodal.interface",
    _interface_path
)
_interface_module = importlib.util.module_from_spec(_spec)
sys.modules["self_fixing_engineer.arbiter.plugins.multimodal.interface"] = _interface_module
_spec.loader.exec_module(_interface_module)

AudioAnalysisResult = _interface_module.AudioAnalysisResult
ImageAnalysisResult = _interface_module.ImageAnalysisResult
TextAnalysisResult = _interface_module.TextAnalysisResult
OpenAIMultiModalProvider = _interface_module.OpenAIMultiModalProvider
XAIMultiModalProvider = _interface_module.XAIMultiModalProvider
get_multimodal_provider = _interface_module.get_multimodal_provider

# Small valid JPEG bytes (1x1 red pixel)
SAMPLE_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f"
    b"\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01"
    b"\x00\x00?\x00\xfb\xff\xd9"
)

SAMPLE_TEXT = "The Code Factory is a self-fixing software engineering platform using AI agents."


def make_mock_response(json_data: Dict[str, Any]) -> MagicMock:
    """Create a mock aiohttp response that returns the given JSON data."""
    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(return_value=json_data)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def make_mock_session(json_data: Dict[str, Any]) -> MagicMock:
    """Create a mock aiohttp.ClientSession that returns canned JSON responses."""
    mock_resp = make_mock_response(json_data)
    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_client_session_cls = MagicMock(return_value=mock_session)
    return mock_client_session_cls


OPENAI_IMAGE_RESPONSE = {
    "choices": [{
        "message": {
            "content": json.dumps({
                "classifications": [{"label": "test_image", "score": 0.95}],
                "objects": [{"label": "test_object", "confidence": 0.9}],
                "ocr_text": "Sample OCR",
            })
        }
    }]
}

OPENAI_TEXT_RESPONSE = {
    "choices": [{
        "message": {
            "content": json.dumps({
                "classification": [{"label": "technology", "score": 0.9}],
                "sentiment": "positive",
                "named_entities": [{"text": "Code Factory", "type": "ORG"}],
                "summary": "AI platform summary",
                "keywords": ["AI", "platform"],
            })
        }
    }]
}

XAI_IMAGE_RESPONSE = {
    "choices": [{
        "message": {
            "content": json.dumps({
                "classifications": [{"label": "xai_image", "score": 0.88}],
                "objects": [{"label": "xai_object", "confidence": 0.85}],
                "ocr_text": "xAI OCR",
            })
        }
    }]
}

XAI_TEXT_RESPONSE = {
    "choices": [{
        "message": {
            "content": json.dumps({
                "classification": [{"label": "technology", "score": 0.85}],
                "sentiment": "neutral",
                "named_entities": [],
                "summary": "Test summary",
                "keywords": ["test"],
            })
        }
    }]
}


class TestOpenAIMultiModalProvider:
    """Tests for OpenAIMultiModalProvider."""

    def setup_method(self):
        """Set up test instance with mock API key."""
        self.provider = OpenAIMultiModalProvider(config={"api_key": "test-key"})

    @pytest.mark.asyncio
    async def test_analyze_image_async_success(self):
        """OpenAIMultiModalProvider.analyze_image() parses API response correctly."""
        mock_session_cls = make_mock_session(OPENAI_IMAGE_RESPONSE)
        with patch.object(_interface_module, "_AIOHTTP_AVAILABLE", True):
            with patch.object(_interface_module, "_aiohttp") as mock_aiohttp:
                mock_aiohttp.ClientSession = mock_session_cls
                mock_aiohttp.ClientTimeout = MagicMock(return_value=MagicMock())
                result = await self.provider.analyze_image_async(SAMPLE_JPEG)

        assert isinstance(result, ImageAnalysisResult)
        assert result.success is True
        assert result.classifications is not None
        assert result.classifications[0]["label"] == "test_image"

    @pytest.mark.asyncio
    async def test_analyze_text_async_success(self):
        """OpenAIMultiModalProvider.analyze_text() parses API response correctly."""
        mock_session_cls = make_mock_session(OPENAI_TEXT_RESPONSE)
        with patch.object(_interface_module, "_AIOHTTP_AVAILABLE", True):
            with patch.object(_interface_module, "_aiohttp") as mock_aiohttp:
                mock_aiohttp.ClientSession = mock_session_cls
                mock_aiohttp.ClientTimeout = MagicMock(return_value=MagicMock())
                result = await self.provider.analyze_text_async(SAMPLE_TEXT)

        assert isinstance(result, TextAnalysisResult)
        assert result.success is True
        assert result.sentiment == {"positive": 1.0}
        assert result.summary_text == "AI platform summary"

    def test_supported_modalities(self):
        """OpenAIMultiModalProvider supports image, audio, text, video."""
        modalities = self.provider.supported_modalities()
        assert "image" in modalities
        assert "text" in modalities
        assert "audio" in modalities
        assert "video" in modalities

    def test_model_info(self):
        """OpenAIMultiModalProvider returns model info dict."""
        info = self.provider.model_info()
        assert info["provider"] == "openai"
        assert "image_model" in info
        assert "text_model" in info

    @pytest.mark.asyncio
    async def test_analyze_image_fallback_on_api_error(self):
        """OpenAIMultiModalProvider returns failure result on API error."""
        with patch.object(_interface_module, "_AIOHTTP_AVAILABLE", False):
            result = await self.provider.analyze_image_async(SAMPLE_JPEG)
        assert result.success is False
        assert result.error_message is not None


class TestXAIMultiModalProvider:
    """Tests for XAIMultiModalProvider."""

    def setup_method(self):
        """Set up test instance with mock API key."""
        self.provider = XAIMultiModalProvider(config={"api_key": "test-xai-key"})

    @pytest.mark.asyncio
    async def test_analyze_image_async_success(self):
        """XAIMultiModalProvider.analyze_image() parses API response correctly."""
        mock_session_cls = make_mock_session(XAI_IMAGE_RESPONSE)
        with patch.object(_interface_module, "_AIOHTTP_AVAILABLE", True):
            with patch.object(_interface_module, "_aiohttp") as mock_aiohttp:
                mock_aiohttp.ClientSession = mock_session_cls
                mock_aiohttp.ClientTimeout = MagicMock(return_value=MagicMock())
                result = await self.provider.analyze_image_async(SAMPLE_JPEG)

        assert isinstance(result, ImageAnalysisResult)
        assert result.success is True
        assert result.model_id == "grok-2-vision-1212"

    def test_audio_raises_not_implemented(self):
        """XAIMultiModalProvider.analyze_audio() raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            self.provider.analyze_audio(b"audio_bytes")

    def test_video_raises_not_implemented(self):
        """XAIMultiModalProvider.analyze_video() raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            self.provider.analyze_video(b"video_bytes")

    def test_supported_modalities(self):
        """XAIMultiModalProvider supports only image and text."""
        modalities = self.provider.supported_modalities()
        assert "image" in modalities
        assert "text" in modalities
        assert "audio" not in modalities
        assert "video" not in modalities

    def test_model_info(self):
        """XAIMultiModalProvider returns model info dict."""
        info = self.provider.model_info()
        assert info["provider"] == "xai"
        assert "grok" in info["image_model"]


class TestGetMultimodalProvider:
    """Tests for the get_multimodal_provider factory function."""

    def test_get_openai_provider_with_env_key(self):
        """get_multimodal_provider('openai') returns OpenAIMultiModalProvider when key is set."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-openai-key"}):
            provider = get_multimodal_provider("openai")
        assert isinstance(provider, OpenAIMultiModalProvider)

    def test_get_xai_provider_with_env_key(self):
        """get_multimodal_provider('xai') returns XAIMultiModalProvider when key is set."""
        with patch.dict(os.environ, {"XAI_API_KEY": "test-xai-key"}):
            provider = get_multimodal_provider("xai")
        assert isinstance(provider, XAIMultiModalProvider)

    def test_auto_detection_prefers_xai(self):
        """get_multimodal_provider('auto') prefers xAI when both keys are set."""
        with patch.dict(os.environ, {"XAI_API_KEY": "test-xai-key", "OPENAI_API_KEY": "test-openai-key"}):
            provider = get_multimodal_provider("auto")
        assert isinstance(provider, XAIMultiModalProvider)

    def test_auto_detection_falls_back_to_openai(self):
        """get_multimodal_provider('auto') falls back to OpenAI when only OPENAI_API_KEY is set."""
        env = {k: v for k, v in os.environ.items() if k not in ("XAI_API_KEY", "GROK_API_KEY")}
        env["OPENAI_API_KEY"] = "test-openai-key"
        with patch.dict(os.environ, env, clear=True):
            provider = get_multimodal_provider("auto")
        assert isinstance(provider, OpenAIMultiModalProvider)

    def test_no_api_key_raises_value_error(self):
        """get_multimodal_provider('auto') raises ValueError when no API key is set."""
        env = {k: v for k, v in os.environ.items() if k not in ("XAI_API_KEY", "GROK_API_KEY", "OPENAI_API_KEY")}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="No multimodal provider"):
                get_multimodal_provider("auto")

    def test_openai_no_key_raises_value_error(self):
        """get_multimodal_provider('openai') raises ValueError when OPENAI_API_KEY not set."""
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError):
                get_multimodal_provider("openai")

    def test_unknown_provider_raises_value_error(self):
        """get_multimodal_provider() raises ValueError for unknown provider."""
        with pytest.raises(ValueError, match="Unknown provider"):
            get_multimodal_provider("unknown_provider")
