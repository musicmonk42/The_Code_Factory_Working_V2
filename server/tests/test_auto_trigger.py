# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for auto-trigger pipeline functionality and LLM auto-detection.

Tests the automatic triggering of the full generation pipeline after file upload
and the automatic detection of available LLM providers.
"""

import io
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from server.config import detect_available_llm_provider, get_default_model_for_provider
from server.schemas import Job, JobStatus
from server.storage import jobs_db


@pytest.fixture
def client():
    """Create a test client for the FastAPI app.
    Import deferred to fixture to avoid expensive initialization during collection.
    Uses context manager to properly trigger lifespan events.
    """
    from server.main import app
    with TestClient(app) as client:
        yield client


@pytest.fixture
def sample_job():
    """Create a sample job for testing."""
    job = Job(
        id="test-auto-trigger-123",
        status=JobStatus.PENDING,
        input_files=[],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        metadata={},
    )
    jobs_db[job.id] = job
    yield job
    # Cleanup
    if job.id in jobs_db:
        del jobs_db[job.id]


class TestLLMAutoDetection:
    """Test suite for LLM provider auto-detection."""
    
    def test_detect_openai_provider(self):
        """Test detection of OpenAI provider from environment."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
            provider = detect_available_llm_provider()
            assert provider == "openai"
    
    def test_detect_anthropic_provider(self):
        """Test detection of Anthropic provider from environment."""
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
                provider = detect_available_llm_provider()
                assert provider == "anthropic"
    
    def test_detect_xai_provider(self):
        """Test detection of xAI/Grok provider from environment."""
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict(os.environ, {"XAI_API_KEY": "test-key"}):
                provider = detect_available_llm_provider()
                assert provider == "grok"
    
    def test_detect_grok_api_key(self):
        """Test detection using GROK_API_KEY."""
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict(os.environ, {"GROK_API_KEY": "test-key"}):
                provider = detect_available_llm_provider()
                assert provider == "grok"
    
    def test_detect_google_provider(self):
        """Test detection of Google provider from environment."""
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
                provider = detect_available_llm_provider()
                assert provider == "google"
    
    def test_detect_ollama_provider(self):
        """Test detection of Ollama provider from environment."""
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict(os.environ, {"OLLAMA_HOST": "http://localhost:11434"}):
                provider = detect_available_llm_provider()
                assert provider == "ollama"
    
    def test_priority_order(self):
        """Test that providers are detected in priority order."""
        # OpenAI should be detected first even if others are present
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "openai-key",
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
        }, clear=True):
            provider = detect_available_llm_provider()
            assert provider == "openai"
        
        # Anthropic should be second
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
        }, clear=True):
            provider = detect_available_llm_provider()
            assert provider == "anthropic"
    
    def test_no_provider_configured(self):
        """Test when no provider is configured."""
        with patch.dict(os.environ, {}, clear=True):
            provider = detect_available_llm_provider()
            assert provider is None
    
    def test_get_default_models(self):
        """Test getting default models for each provider."""
        assert get_default_model_for_provider("openai") == "gpt-4o"
        assert get_default_model_for_provider("anthropic") == "claude-3-sonnet-20240229"
        assert get_default_model_for_provider("grok") == "grok-beta"
        assert get_default_model_for_provider("google") == "gemini-pro"
        assert get_default_model_for_provider("ollama") == "codellama"
        assert get_default_model_for_provider("unknown") == "gpt-4"


class TestAutoTriggerPipeline:
    """Test suite for automatic pipeline triggering after upload."""
    
    @patch("server.routers.generator.GeneratorService")
    def test_upload_readme_triggers_pipeline(self, mock_service_class, client, sample_job):
        """Test that uploading README.md automatically triggers the pipeline."""
        readme_content = b"""# Test Project

This is a Python project that does something interesting.

## Requirements
- Feature 1
- Feature 2
"""
        
        # Mock the generator service
        mock_service = Mock()
        mock_service.save_upload = AsyncMock(return_value={
            "filename": "README.md",
            "path": f"./uploads/{sample_job.id}/README.md",
            "size": len(readme_content),
            "uploaded_at": datetime.now(timezone.utc).isoformat()
        })
        mock_service.create_generation_job = AsyncMock(return_value={
            "job_id": sample_job.id,
            "status": "created"
        })
        mock_service.run_full_pipeline = AsyncMock(return_value={
            "job_id": sample_job.id,
            "status": "completed"
        })
        mock_service_class.return_value = mock_service
        
        files = [
            ("files", ("README.md", io.BytesIO(readme_content), "text/markdown"))
        ]
        
        response = client.post(
            f"/api/generator/{sample_job.id}/upload",
            files=files,
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["pipeline_triggered"] is True
        assert "Pipeline auto-triggered" in data["message"]
    
    @patch("server.routers.generator.GeneratorService")
    def test_upload_without_readme_no_trigger(self, mock_service_class, client, sample_job):
        """Test that uploading without README.md doesn't trigger pipeline."""
        test_content = b"def test(): pass"
        
        # Mock the generator service
        mock_service = Mock()
        mock_service.save_upload = AsyncMock(return_value={
            "filename": "test.py",
            "path": f"./uploads/{sample_job.id}/test.py",
            "size": len(test_content),
            "uploaded_at": datetime.now(timezone.utc).isoformat()
        })
        mock_service.create_generation_job = AsyncMock(return_value={
            "job_id": sample_job.id,
            "status": "created"
        })
        mock_service_class.return_value = mock_service
        
        files = [
            ("files", ("test.py", io.BytesIO(test_content), "text/x-python"))
        ]
        
        response = client.post(
            f"/api/generator/{sample_job.id}/upload",
            files=files,
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["pipeline_triggered"] is False
        assert "Pipeline auto-triggered" not in data["message"]
    
    @patch("server.routers.generator.GeneratorService")
    def test_upload_any_md_triggers_pipeline(self, mock_service_class, client, sample_job):
        """Test that uploading ANY .md file triggers the pipeline, not just README.md."""
        spec_content = b"""# Technical Specification
        
        Build a FastAPI service with /api/users endpoint.
        """
        
        mock_service = Mock()
        mock_service.save_upload = AsyncMock(return_value={
            "filename": "technical_spec.md",
            "path": f"./uploads/{sample_job.id}/technical_spec.md",
            "size": len(spec_content),
            "uploaded_at": datetime.now(timezone.utc).isoformat()
        })
        mock_service.create_generation_job = AsyncMock(return_value={
            "job_id": sample_job.id,
            "status": "created"
        })
        mock_service.run_full_pipeline = AsyncMock(return_value={
            "job_id": sample_job.id,
            "status": "completed"
        })
        mock_service_class.return_value = mock_service
        
        files = [
            ("files", ("technical_spec.md", io.BytesIO(spec_content), "text/markdown"))
        ]
        
        response = client.post(
            f"/api/generator/{sample_job.id}/upload",
            files=files,
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["pipeline_triggered"] is True
        assert "Pipeline auto-triggered" in data["message"]


class TestLanguageDetection:
    """Test suite for language auto-detection from README content."""
    
    def test_detect_python_default(self):
        """Test that Python is the default when no specific language is mentioned."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# My Project\n\nThis is a generic project."
        assert detect_language_from_content(readme) == "python"
    
    def test_detect_python_explicit(self):
        """Test detection of Python projects."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# Python Project\n\nThis is a Python application."
        assert detect_language_from_content(readme) == "python"
    
    def test_detect_javascript(self):
        """Test detection of JavaScript projects."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# JavaScript Project\n\nBuilt with Node.js and npm."
        assert detect_language_from_content(readme) == "javascript"
    
    def test_detect_typescript(self):
        """Test detection of TypeScript projects."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# TypeScript Project\n\nA modern TypeScript application."
        assert detect_language_from_content(readme) == "typescript"
    
    def test_detect_java(self):
        """Test detection of Java projects."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# Java Application\n\nA Java-based microservice."
        assert detect_language_from_content(readme) == "java"
    
    def test_detect_go(self):
        """Test detection of Go projects."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# Go Service\n\nA golang microservice."
        assert detect_language_from_content(readme) == "go"
    
    def test_detect_rust(self):
        """Test detection of Rust projects."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# Rust CLI\n\nA fast Rust command-line tool."
        assert detect_language_from_content(readme) == "rust"
    
    def test_typescript_priority_over_javascript(self):
        """Test that TypeScript is detected even if JavaScript is also mentioned."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# Project\n\nBuilt with TypeScript, compiles to JavaScript."
        assert detect_language_from_content(readme) == "typescript"
    
    def test_java_not_confused_with_javascript(self):
        """Test that Java is not confused with JavaScript."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# Java Project\n\nBuilt with Java 17."
        assert detect_language_from_content(readme) == "java"
    
    def test_java_with_javascript_mentioned(self):
        """Test that Java is detected even when JavaScript is mentioned."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# Java Backend\n\nJava REST API that serves a JavaScript frontend."
        assert detect_language_from_content(readme) == "java"
    
    def test_go_not_false_positive(self):
        """Test that 'go' in common English words doesn't trigger Go detection."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# Algorithm Project\n\nLet's go through the algorithm step by step."
        # Should default to Python, not Go
        assert detect_language_from_content(readme) == "python"
    
    def test_go_with_golang(self):
        """Test Go detection with 'golang' keyword."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# Golang Service\n\nBuilt with golang 1.21."
        assert detect_language_from_content(readme) == "go"
    
    def test_go_with_go_language(self):
        """Test Go detection with 'go language' phrase."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# Microservice\n\nWritten in the Go language for high performance."
        assert detect_language_from_content(readme) == "go"
    
    def test_npm_without_space_nodejs(self):
        """Test JavaScript detection with npm and nodejs variations."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# Node Project\n\nInstall with npm install. Uses nodejs runtime."
        assert detect_language_from_content(readme) == "javascript"
    
    def test_npm_at_start_of_line(self):
        """Test npm detection at beginning of line."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# Project\n\nnpm install to get started."
        assert detect_language_from_content(readme) == "javascript"
    
    def test_npm_with_punctuation(self):
        """Test npm detection with punctuation."""
        from server.routers.generator import detect_language_from_content
        
        readme = "# Project\n\nRun npm, then start the server."
        assert detect_language_from_content(readme) == "javascript"
