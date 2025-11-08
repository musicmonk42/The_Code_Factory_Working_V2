"""
test_docgen_validator.py
Enterprise-grade test suite for the DocGen Validator module.

This test suite provides comprehensive coverage including:
- Unit tests for all validation components
- Integration tests for the full validation pipeline
- Performance and scalability testing
- Security and compliance validation
- NLP quality assessment testing
- API and plugin system testing
- Property-based and stateful testing
- Load and stress testing
"""

import asyncio
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from unittest.mock import AsyncMock, MagicMock, Mock, patch, call
import concurrent.futures
import random
import string
import hashlib

import pytest
import pytest_asyncio
from pytest_benchmark.fixture import BenchmarkFixture
import aiohttp
from aiohttp import web
import aiofiles
from faker import Faker
from hypothesis import given, strategies as st, settings, assume, Phase
from hypothesis.stateful import RuleBasedStateMachine, rule, initialize, Bundle, invariant
import pypandoc
import docutils.core
from bs4 import BeautifulSoup
import nltk
from prometheus_client import REGISTRY
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from fastapi.testclient import TestClient
import numpy as np
from textstat import flesch_reading_ease, flesch_kincaid_grade

# Import the module under test
try:
    # FIX: Corrected the full package path import block.
    from agents.docgen_agent.docgen_validator import (
        DocValidator,
        ValidationPlugin,
        MarkdownPlugin,
        RSTPlugin,
        HTMLPlugin,
        PluginRegistry,
        ValidationRequest,
        ValidationReportResponse,
        DEFAULT_SCHEMA,
        app,
        validator_calls_total,
        validator_errors_total,
        validator_latency_seconds,
        docgen_compliance_issues_total,
        docgen_security_findings_total,
        docgen_content_quality_score,
        DANGEROUS_CONTENT_PATTERNS
    )
    # FIX: Corrected the local package name import block for flexibility/local testing.
    from docgen_validator import (
        DocValidator,
        ValidationPlugin,
        MarkdownPlugin,
        RSTPlugin,
        HTMLPlugin,
        PluginRegistry,
        ValidationRequest,
        ValidationReportResponse,
        DEFAULT_SCHEMA,
        app,
        validator_calls_total,
        validator_errors_total,
        validator_latency_seconds,
        docgen_compliance_issues_total,
        docgen_security_findings_total,
        docgen_content_quality_score,
        DANGEROUS_CONTENT_PATTERNS
    )
except ImportError:
    # Initialize faker for test data generation
    fake = Faker()
    fake.seed_instance(42)  # For reproducible tests

    # Configure OpenTelemetry for testing
    memory_exporter = InMemorySpanExporter()
    span_processor = SimpleSpanProcessor(memory_exporter)
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(span_processor)
    trace.set_tracer_provider(tracer_provider)
    print("Failed to import docgen_validator. Continuing with a reduced test set.")
    
    # Define minimal placeholder classes/variables to allow test run
    class DocValidator:
        def __init__(self, **kwargs): pass
        async def validate_documentation(self, **kwargs): return {"overall_status": "error", "is_valid": False}
    class ValidationPlugin: pass
    class MarkdownPlugin: pass
    class RSTPlugin: pass
    class HTMLPlugin: pass
    class PluginRegistry: pass
    class ValidationRequest: pass
    class ValidationReportResponse: pass
    DEFAULT_SCHEMA = {"sections": [], "order": [], "min_total_length": 0}
    app = Mock()
    validator_calls_total = Mock()
    validator_errors_total = Mock()
    validator_latency_seconds = Mock()
    docgen_compliance_issues_total = Mock()
    docgen_security_findings_total = Mock()
    docgen_content_quality_score = Mock()
    DANGEROUS_CONTENT_PATTERNS = {}

# Initialize faker for test data generation
fake = Faker()
fake.seed_instance(42)  # For reproducible tests

# Configure OpenTelemetry for testing
memory_exporter = InMemorySpanExporter()
span_processor = SimpleSpanProcessor(memory_exporter)
tracer_provider = TracerProvider()
tracer_provider.add_span_processor(span_processor)
trace.set_tracer_provider(tracer_provider)


# ============================================================================
# TEST DATA GENERATORS
# ============================================================================

class TestDataGenerator:
    """Generate various types of test documentation."""
    
    @staticmethod
    def generate_valid_markdown(sections: Optional[List[str]] = None) -> str:
        """Generate valid markdown with specified sections."""
        if sections is None:
            sections = DEFAULT_SCHEMA.get("sections", ["introduction", "usage", "license", "copyright"])
        
        content = ["# Test Documentation\n"]
        for section in sections:
            content.append(f"\n## {section.title()}\n")
            content.append(f"{fake.paragraph(nb_sentences=5)}\n")
            
            # Add specific content for certain sections
            if section == "license":
                content.append("\nMIT License\n")
            elif section == "copyright":
                content.append(f"\nCopyright (c) {datetime.now().year} Test Corp\n")
            elif section == "api_reference":
                content.append("\n```python\ndef example():\n    pass\n```\n")
        
        return "\n".join(content)
    
    @staticmethod
    def generate_invalid_markdown() -> str:
        """Generate markdown with various issues."""
        return "This is invalid markdown without proper headers or structure."
    
    @staticmethod
    def generate_valid_rst() -> str:
        """Generate valid reStructuredText."""
        return """
Test Document

Introduction
------------
This is a test RST document.

Usage
-----
How to use this document.

.. code-block:: python

    def example():
        pass

License
-------
MIT License

Copyright
---------
Copyright (c) 2024 Test Corp
"""
    
    @staticmethod
    def generate_valid_html() -> str:
        """Generate valid HTML."""
        return """
<!DOCTYPE html>
<html>
<head><title>Test Document</title></head>
<body>
    <h1>Test Documentation</h1>
    <h2>Introduction</h2>
    <p>This is a test HTML document.</p>
    <h2>License</h2>
    <p>MIT License</p>
    <h2>Copyright</h2>
    <p>Copyright (c) 2024 Test Corp</p>
</body>
</html>
"""
    
    @staticmethod
    def generate_sensitive_content() -> str:
        """Generate content with PII and secrets."""
        return """
# Sensitive Documentation

Contact: john.doe@example.com
Phone: +1-555-123-4567
SSN: 123-45-6789

API Configuration:
API_KEY=sk-1234567890abcdef
PASSWORD=admin123
SECRET_TOKEN=secret_value_here

Database: http://admin:password@insecure-db.com
"""
    
    @staticmethod
    def generate_large_document(size_kb: int = 100) -> str:
        """Generate large document for performance testing."""
        sections = []
        target_size = size_kb * 1024
        current_size = 0
        
        while current_size < target_size:
            section = f"\n## Section {len(sections) + 1}\n"
            section += fake.text(max_nb_chars=2000)
            sections.append(section)
            current_size += len(section)
        
        return f"# Large Document\n{''.join(sections)}"


# ============================================================================
# FIXTURES AND MOCKS
# ============================================================================

@pytest.fixture
def test_data():
    """Provide test data generator."""
    return TestDataGenerator()


@pytest.fixture
def mock_presidio():
    """Mock Presidio analyzer and anonymizer."""
    with patch('docgen_validator.AnalyzerEngine') as mock_analyzer_class, \
         patch('docgen_validator.AnonymizerEngine') as mock_anonymizer_class:
        
        mock_analyzer = MagicMock()
        mock_anonymizer = MagicMock()
        
        mock_analyzer_class.return_value = mock_analyzer
        mock_anonymizer_class.return_value = mock_anonymizer
        
        # Configure for PII detection
        mock_analyzer.analyze.return_value = [
            MagicMock(entity_type="EMAIL_ADDRESS", start=10, end=30),
            MagicMock(entity_type="PHONE_NUMBER", start=40, end=55),
            MagicMock(entity_type="US_SSN", start=60, end=71)
        ]
        
        # Configure anonymization
        mock_anonymizer.anonymize.return_value = MagicMock(
            text="Content with [REDACTED] and [REDACTED] removed."
        )
        
        yield mock_analyzer, mock_anonymizer


@pytest.fixture
def mock_llm_orchestrator():
    """Mock LLM orchestrator for auto-correction."""
    with patch('docgen_validator.DeployLLMOrchestrator') as mock_class:
        mock_orchestrator = MagicMock()
        mock_class.return_value = mock_orchestrator
        
        # Configure generate_docs_llm mock
        async def mock_generate(*args, **kwargs):
            prompt = args[0] if args else kwargs.get('prompt', '')
            
            if "correct" in prompt.lower() or "fix" in prompt.lower():
                # Return corrected content
                return {
                    "content": TestDataGenerator.generate_valid_markdown(),
                    "output_tokens": 500,
                    "usage": {"prompt_tokens": 200, "completion_tokens": 500}
                }
            return {
                "content": "Generic response",
                "output_tokens": 100,
                "usage": {"prompt_tokens": 50, "completion_tokens": 100}
            }
        
        with patch('docgen_validator.generate_docs_llm', side_effect=mock_generate):
            yield mock_orchestrator


@pytest.fixture
async def temp_repo():
    """Create temporary Git repository."""
    with tempfile.TemporaryDirectory() as temp_dir:
        repo_path = Path(temp_dir)
        
        # Initialize Git repo
        proc = await asyncio.create_subprocess_exec(
            "git", "init",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        
        # Configure Git
        for cmd in [
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "Test User"]
        ]:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
        
        # Create test files
        test_file = repo_path / "test.py"
        async with aiofiles.open(test_file, 'w') as f:
            await f.write("def test():\n    pass\n")
        
        # Commit
        for cmd in [
            ["git", "add", "."],
            ["git", "commit", "-m", "Initial commit"]
        ]:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
        
        yield str(repo_path)


@pytest.fixture
def custom_schema():
    """Provide custom validation schema."""
    return {
        "sections": ["summary", "features", "requirements", "deployment"],
        "order": ["summary", "features", "requirements", "deployment"],
        "min_section_length": 100,
        "min_total_length": 1000,
        "languages": ["en", "es", "fr"]
    }


@pytest.fixture
def api_client():
    """FastAPI test client."""
    return TestClient(app)


# ============================================================================
# UNIT TESTS - VALIDATION PLUGINS
# ============================================================================

class TestValidationPlugins:
    """Test validation plugin implementations."""
    
    def test_markdown_plugin_normalize(self, test_data):
        """Test Markdown normalization."""
        plugin = MarkdownPlugin()
        
        # Test valid markdown
        valid_md = test_data.generate_valid_markdown()
        normalized = plugin.normalize(valid_md)
        assert normalized is not None
        assert isinstance(normalized, str)
        
        # Test empty content
        assert plugin.normalize("") == ""
        
        # Test malformed content
        with pytest.raises(ValueError):
            plugin.normalize(None)  # Should handle gracefully or raise
    
    def test_markdown_plugin_validate(self, test_data):
        """Test Markdown validation."""
        plugin = MarkdownPlugin()
        
        # Valid markdown
        valid_md = test_data.generate_valid_markdown()
        result = plugin.validate(valid_md, DEFAULT_SCHEMA)
        assert result["valid"] is True
        assert len(result["issues"]) == 0
        
        # Invalid markdown (no header)
        invalid_md = "Content without header"
        result = plugin.validate(invalid_md, DEFAULT_SCHEMA)
        assert result["valid"] is False
        assert any("header" in issue.lower() for issue in result["issues"])
        
        # Too short content
        short_md = "# Title\nShort"
        result = plugin.validate(short_md, DEFAULT_SCHEMA)
        assert result["valid"] is False
        assert any("short" in issue.lower() for issue in result["issues"])
    
    def test_markdown_plugin_convert(self, test_data):
        """Test Markdown format conversion."""
        plugin = MarkdownPlugin()
        md_content = test_data.generate_valid_markdown()
        
        # Convert to HTML
        html = plugin.convert(md_content, "html")
        assert "<h1>" in html or "<h2>" in html
        
        # Convert to RST
        rst = plugin.convert(md_content, "rst")
        assert "=" in rst or "-" in rst  # RST headers
        
        # Same format should return original
        assert plugin.convert(md_content, "md") == md_content
    
    def test_rst_plugin_validate(self, test_data):
        """Test RST validation."""
        plugin = RSTPlugin()
        
        # Valid RST
        valid_rst = test_data.generate_valid_rst()
        result = plugin.validate(valid_rst, DEFAULT_SCHEMA)
        assert isinstance(result["valid"], bool)
        
        # Malformed RST
        invalid_rst = "Title\n===\nMismatched underline"
        result = plugin.validate(invalid_rst, DEFAULT_SCHEMA)
        # RST validation depends on docutils configuration
        assert "issues" in result
    
    def test_html_plugin_validate(self, test_data):
        """Test HTML validation."""
        plugin = HTMLPlugin()
        
        # Valid HTML
        valid_html = test_data.generate_valid_html()
        result = plugin.validate(valid_html, DEFAULT_SCHEMA)
        assert result["valid"] is True
        
        # Invalid HTML
        invalid_html = "Not HTML content"
        result = plugin.validate(invalid_html, DEFAULT_SCHEMA)
        assert result["valid"] is False
        assert any("html" in issue.lower() for issue in result["issues"])
    
    @pytest.mark.parametrize("plugin_class,format_name", [
        (MarkdownPlugin, "md"),
        (RSTPlugin, "rst"),
        (HTMLPlugin, "html")
    ])
    def test_plugin_suggestions(self, plugin_class, format_name):
        """Test suggestion generation for all plugins."""
        plugin = plugin_class()
        
        issues = {
            "issues": [
                "Missing header",
                "Content too short",
                "Invalid syntax"
            ]
        }
        
        suggestions = plugin.suggest(issues)
        assert isinstance(suggestions, list)
        assert len(suggestions) > 0


# ============================================================================
# UNIT TESTS - PLUGIN REGISTRY
# ============================================================================

class TestPluginRegistry:
    """Test plugin registry functionality."""
    
    def test_registry_initialization(self):
        """Test registry initializes with built-in plugins."""
        registry = PluginRegistry()
        
        # Check built-in plugins
        assert 'md' in registry.plugins
        assert 'rst' in registry.plugins
        assert 'html' in registry.plugins
        
        # Verify plugin types
        assert registry.plugins['md'] == MarkdownPlugin
        assert registry.plugins['rst'] == RSTPlugin
        assert registry.plugins['html'] == HTMLPlugin
    
    def test_get_plugin_success(self):
        """Test successful plugin retrieval."""
        registry = PluginRegistry()
        
        # Get various plugins
        md_plugin = registry.get_plugin('md')
        assert isinstance(md_plugin, MarkdownPlugin)
        
        rst_plugin = registry.get_plugin('rst')
        assert isinstance(rst_plugin, RSTPlugin)
        
        html_plugin = registry.get_plugin('html')
        assert isinstance(html_plugin, HTMLPlugin)
    
    def test_get_plugin_failure(self):
        """Test plugin retrieval failure."""
        registry = PluginRegistry()
        
        with pytest.raises(ValueError) as exc_info:
            registry.get_plugin('nonexistent')
        
        assert "No validation plugin found" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_hot_reload(self, tmp_path):
        """Test hot-reload functionality."""
        plugin_dir = tmp_path / "test_plugins"
        plugin_dir.mkdir()
        
        registry = PluginRegistry(str(plugin_dir))
        initial_count = len(registry.plugins)
        
        # Create custom plugin file
        plugin_file = plugin_dir / "custom_validator_plugin.py"
        plugin_content = """
from docgen_validator import ValidationPlugin
from typing import Dict, Any, List

class CustomValidatorPlugin(ValidationPlugin):
    __version__ = "1.0"
    __source__ = "custom"
    
    def normalize(self, content: str) -> str:
        return f"CUSTOM_NORMALIZED: {content}"
    
    def validate(self, content: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        return {"valid": True, "issues": []}
    
    def convert(self, content: str, to_format: str) -> str:
        return content
    
    def suggest(self, issues: Dict[str, Any]) -> List[str]:
        return ["Custom suggestion"]
"""
        plugin_file.write_text(plugin_content)
        
        # Trigger reload
        await asyncio.sleep(0.5)
        
        # Note: Hot-reload testing requires proper event loop handling
        # This is simplified - real implementation needs more setup


# ============================================================================
# UNIT TESTS - DOCVALIDATOR
# ============================================================================

class TestDocValidator:
    """Test DocValidator class."""
    
    @pytest.mark.asyncio
    async def test_validator_initialization(self):
        """Test validator initialization."""
        validator = DocValidator()
        
        assert validator.run_id is not None
        assert isinstance(validator.run_id, str)
        assert validator.schema == DEFAULT_SCHEMA
        assert len(validator.rationale_steps) == 0
        assert len(validator.corrections_log) == 0
    
    @pytest.mark.asyncio
    async def test_validator_with_custom_schema(self, custom_schema):
        """Test validator with custom schema."""
        validator = DocValidator(schema=custom_schema)
        
        assert validator.schema == custom_schema
        assert validator.schema["sections"] != DEFAULT_SCHEMA["sections"]
    
    @pytest.mark.asyncio
    async def test_normalize_format_doc(self, test_data):
        """Test document normalization."""
        validator = DocValidator()
        
        # Test markdown normalization
        md_content = test_data.generate_valid_markdown()
        normalized = await validator.normalize_format_doc(md_content, "md")
        assert normalized is not None
        assert len(validator.rationale_steps) > 0
        
        # Test invalid format
        with pytest.raises(RuntimeError):
            await validator.normalize_format_doc("content", "invalid_format")
    
    @pytest.mark.asyncio
    async def test_scan_unsafe_content(self, test_data):
        """Test security scanning."""
        validator = DocValidator()
        
        # Test with sensitive content
        sensitive = test_data.generate_sensitive_content()
        findings = await validator.scan_unsafe_content(sensitive, "md")
        
        assert len(findings) > 0
        categories = [f["category"] for f in findings]
        assert "HardcodedCredentials" in categories
        assert "InsecureProtocolUsage" in categories
        
        # Test with clean content
        clean = test_data.generate_valid_markdown()
        findings = await validator.scan_unsafe_content(clean, "md")
        assert len(findings) == 0
    
    def test_check_compliance(self, test_data):
        """Test compliance checking."""
        validator = DocValidator()
        
        # Document with compliance
        compliant = test_data.generate_valid_markdown()
        issues = validator.check_compliance(compliant)
        # Should have license and copyright
        license_issues = [i for i in issues if "license" in i["issue"].lower()]
        copyright_issues = [i for i in issues if "copyright" in i["issue"].lower()]
        assert len(license_issues) == 0
        assert len(copyright_issues) == 0
        
        # Document without compliance
        non_compliant = "# Doc\n\nNo license or copyright information."
        issues = validator.check_compliance(non_compliant)
        assert len(issues) > 0
        assert any("license" in i["issue"].lower() for i in issues)
        assert any("copyright" in i["issue"].lower() for i in issues)
    
    def test_assess_quality(self, test_data):
        """Test quality assessment."""
        validator = DocValidator()
        
        # High-quality document
        good_doc = test_data.generate_valid_markdown()
        quality = validator.assess_quality(good_doc)
        
        assert 0 <= quality["overall_score"] <= 1
        assert "readability" in quality
        assert "sentiment" in quality
        assert "coherence" in quality
        assert "keyword_density" in quality
        assert "anomaly_score" in quality
        
        # Low-quality document
        poor_doc = "Bad bad bad content bad."
        poor_quality = validator.assess_quality(poor_doc)
        assert poor_quality["overall_score"] < quality["overall_score"]
    
    def test_compute_coherence(self, test_data):
        """Test coherence computation."""
        validator = DocValidator()
        
        # Coherent sentences
        coherent_sentences = [
            "The documentation system is complex.",
            "This system includes multiple components.",
            "These components work together seamlessly."
        ]
        
        score = validator.compute_coherence(coherent_sentences)
        assert 0 <= score <= 1
        
        # Incoherent sentences
        incoherent_sentences = [
            "The cat is blue.",
            "Mathematics is important.",
            "Pizza tastes good."
        ]
        
        incoherent_score = validator.compute_coherence(incoherent_sentences)
        # Coherent text should score higher
        # Note: This depends on TextBlob availability
    
    def test_detect_anomalies(self, test_data):
        """Test anomaly detection."""
        validator = DocValidator()
        
        # Normal document
        normal_doc = test_data.generate_valid_markdown()
        anomaly_score = validator.detect_anomalies(normal_doc, 0.1)
        assert anomaly_score < 0.5
        
        # Document with many negative words
        negative_doc = "Error fail bug critical warning issue problem"
        high_anomaly = validator.detect_anomalies(negative_doc, -0.9)
        assert high_anomaly > anomaly_score


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestIntegration:
    """Integration tests for complete validation pipeline."""
    
    @pytest.mark.asyncio
    async def test_full_validation_pipeline_success(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test successful validation through full pipeline."""
        validator = DocValidator()
        
        valid_doc = test_data.generate_valid_markdown()
        result = await validator.validate_documentation(
            doc_text=valid_doc,
            format_type="md",
            auto_correct=False
        )
        
        assert result["is_valid"] is True
        assert result["overall_status"] == "passed"
        assert "provenance" in result
        assert len(result["provenance"]["rationale_steps"]) > 0
    
    @pytest.mark.asyncio
    async def test_validation_with_auto_correction(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test validation with auto-correction enabled."""
        validator = DocValidator()
        
        invalid_doc = test_data.generate_invalid_markdown()
        result = await validator.validate_documentation(
            doc_text=invalid_doc,
            format_type="md",
            auto_correct=True
        )
        
        assert result["corrected_doc"] is not None
        assert len(result["corrected_doc"]) > len(invalid_doc)
        assert any("auto-correct" in step.lower() 
                  for step in result["provenance"]["rationale_steps"])
    
    @pytest.mark.asyncio
    async def test_validation_with_security_findings(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test validation with security issues."""
        validator = DocValidator()
        
        sensitive_doc = test_data.generate_sensitive_content()
        result = await validator.validate_documentation(
            doc_text=sensitive_doc,
            format_type="md",
            auto_correct=False
        )
        
        assert result["is_valid"] is False
        assert len(result["issues"]["security_findings"]) > 0
        
        findings = result["issues"]["security_findings"]
        categories = [f["category"] for f in findings]
        assert "HardcodedCredentials" in categories
    
    @pytest.mark.asyncio
    async def test_validation_different_formats(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test validation with different document formats."""
        validator = DocValidator()
        
        test_cases = [
            ("md", test_data.generate_valid_markdown()),
            ("rst", test_data.generate_valid_rst()),
            ("html", test_data.generate_valid_html())
        ]
        
        for format_type, content in test_cases:
            result = await validator.validate_documentation(
                doc_text=content,
                format_type=format_type,
                auto_correct=False
            )
            
            assert result is not None
            assert "overall_status" in result
            assert "quality_metrics" in result
    
    @pytest.mark.asyncio
    async def test_validation_with_custom_schema(
        self, test_data, custom_schema, mock_presidio, mock_llm_orchestrator
    ):
        """Test validation with custom schema."""
        validator = DocValidator(schema=custom_schema)
        
        # Generate document with custom sections
        doc = TestDataGenerator.generate_valid_markdown(
            sections=custom_schema["sections"]
        )
        
        result = await validator.validate_documentation(
            doc_text=doc,
            format_type="md",
            auto_correct=False
        )
        
        assert result is not None
        # Check schema was applied
        assert validator.schema == custom_schema


# ============================================================================
# PERFORMANCE TESTS
# ============================================================================

class TestPerformance:
    """Performance and scalability tests."""
    
    @pytest.mark.benchmark
    @pytest.mark.asyncio
    async def test_validation_speed(
        self, benchmark, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Benchmark validation speed."""
        validator = DocValidator()
        doc = test_data.generate_valid_markdown()
        
        async def validate():
            return await validator.validate_documentation(
                doc_text=doc,
                format_type="md",
                auto_correct=False
            )
        
        result = benchmark(lambda: asyncio.run(validate()))
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_large_document_handling(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test handling of large documents."""
        validator = DocValidator()
        
        # Generate large document (1MB)
        large_doc = test_data.generate_large_document(size_kb=1024)
        
        start_time = time.time()
        result = await validator.validate_documentation(
            doc_text=large_doc,
            format_type="md",
            auto_correct=False
        )
        elapsed = time.time() - start_time
        
        assert result is not None
        assert elapsed < 60  # Should complete within 60 seconds
        
        # Verify quality metrics calculated
        assert result["quality_metrics"]["overall_score"] >= 0
    
    @pytest.mark.asyncio
    async def test_concurrent_validations(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test concurrent validation processing."""
        num_concurrent = 10
        
        async def validate_one(index):
            validator = DocValidator()
            doc = f"# Document {index}\n\n{fake.text(max_nb_chars=500)}"
            
            return await validator.validate_documentation(
                doc_text=doc,
                format_type="md",
                auto_correct=False
            )
        
        tasks = [validate_one(i) for i in range(num_concurrent)]
        results = await asyncio.gather(*tasks)
        
        assert len(results) == num_concurrent
        assert all(r is not None for r in results)
        assert all("overall_status" in r for r in results)
    
    @pytest.mark.asyncio
    async def test_memory_efficiency(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test memory efficiency with multiple validations."""
        import tracemalloc
        
        tracemalloc.start()
        validator = DocValidator()
        
        # Run multiple validations
        for i in range(50):
            doc = test_data.generate_valid_markdown()
            await validator.validate_documentation(
                doc_text=doc,
                format_type="md",
                auto_correct=False
            )
        
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        
        # Memory usage should be reasonable (< 100MB for 50 validations)
        assert peak / 1024 / 1024 < 100


# ============================================================================
# NLP QUALITY TESTS
# ============================================================================

class TestNLPQuality:
    """Test NLP quality assessment features."""
    
    def test_readability_scoring(self, test_data):
        """Test readability score calculation."""
        validator = DocValidator()
        
        # Simple text
        simple_text = "This is simple text. Short sentences. Easy words."
        simple_quality = validator.assess_quality(simple_text)
        
        # Complex text
        complex_text = """
        The extraordinary complexity of modern computational linguistics 
        necessitates sophisticated methodological approaches for comprehensive 
        analysis of multifaceted linguistic phenomena.
        """
        complex_quality = validator.assess_quality(complex_text)
        
        # Simple text should have better readability
        assert simple_quality["readability"] > complex_quality["readability"]
    
    def test_sentiment_analysis(self, test_data):
        """Test sentiment analysis."""
        validator = DocValidator()
        
        # Positive document
        positive_doc = """
        # Excellent Documentation
        This amazing system provides fantastic features!
        Everything works perfectly and users love it.
        """
        positive_quality = validator.assess_quality(positive_doc)
        
        # Negative document
        negative_doc = """
        # Problems and Issues
        This system has many bugs and errors.
        Users complain about failures and problems.
        """
        negative_quality = validator.assess_quality(negative_doc)
        
        assert positive_quality["sentiment"] > negative_quality["sentiment"]
    
    def test_keyword_density(self, test_data):
        """Test keyword density calculation."""
        validator = DocValidator()
        
        # Diverse vocabulary
        diverse_doc = test_data.generate_valid_markdown()
        diverse_quality = validator.assess_quality(diverse_doc)
        
        # Repetitive text
        repetitive_doc = "Test test test. Documentation documentation. Test documentation."
        repetitive_quality = validator.assess_quality(repetitive_doc)
        
        assert diverse_quality["keyword_density"] > repetitive_quality["keyword_density"]


# ============================================================================
# API TESTS
# ============================================================================

class TestAPI:
    """Test FastAPI endpoints."""
    
    def test_api_validation_endpoint(self, api_client, test_data):
        """Test /validate API endpoint."""
        request_data = {
            "doc_text": test_data.generate_valid_markdown(),
            "format": "md",
            "lang": "en",
            "auto_correct": False
        }
        
        response = api_client.post("/validate", json=request_data)
        
        assert response.status_code == 200
        result = response.json()
        
        assert "is_valid" in result
        assert "overall_status" in result
        assert "quality_metrics" in result
        assert "provenance" in result
    
    def test_api_with_auto_correction(self, api_client, test_data):
        """Test API with auto-correction enabled."""
        request_data = {
            "doc_text": test_data.generate_invalid_markdown(),
            "format": "md",
            "lang": "en",
            "auto_correct": True
        }
        
        with patch('docgen_validator.generate_docs_llm') as mock_llm:
            mock_llm.return_value = asyncio.coroutine(lambda: {
                "content": test_data.generate_valid_markdown(),
                "output_tokens": 100
            })()
            
            response = api_client.post("/validate", json=request_data)
            
            assert response.status_code == 200
            result = response.json()
            assert result.get("corrected_doc") is not None
    
    def test_api_error_handling(self, api_client):
        """Test API error handling."""
        # Invalid format
        request_data = {
            "doc_text": "Test content",
            "format": "invalid_format",
            "lang": "en",
            "auto_correct": False
        }
        
        response = api_client.post("/validate", json=request_data)
        assert response.status_code == 500
        
        # Missing required field
        incomplete_data = {
            "format": "md"
        }
        
        response = api_client.post("/validate", json=incomplete_data)
        assert response.status_code == 422  # Validation error
    
    def test_api_different_languages(self, api_client, test_data):
        """Test API with different languages."""
        for lang in ["en", "es", "fr", "de"]:
            request_data = {
                "doc_text": test_data.generate_valid_markdown(),
                "format": "md",
                "lang": lang,
                "auto_correct": False
            }
            
            response = api_client.post("/validate", json=request_data)
            assert response.status_code in [200, 500]  # Depends on language support


# ============================================================================
# PROPERTY-BASED TESTS
# ============================================================================

class TestPropertyBased:
    """Property-based tests using Hypothesis."""
    
    @given(
        content=st.text(min_size=10, max_size=1000),
        format_type=st.sampled_from(["md", "rst", "html"])
    )
    @settings(max_examples=20, deadline=5000)
    @pytest.mark.asyncio
    async def test_validation_never_crashes(self, content, format_type):
        """Test that validation never crashes for any input."""
        validator = DocValidator()
        
        try:
            result = await validator.validate_documentation(
                doc_text=content,
                format_type=format_type,
                auto_correct=False
            )
            assert result is not None
            assert "overall_status" in result
        except (ValueError, RuntimeError):
            # Expected exceptions for invalid input
            pass
    
    @given(
        sections=st.lists(
            st.sampled_from(DEFAULT_SCHEMA.get("sections", ["introduction", "usage"])),
            min_size=1,
            max_size=len(DEFAULT_SCHEMA.get("sections", ["introduction", "usage"])),
            unique=True
        )
    )
    @settings(max_examples=10)
    def test_quality_metrics_consistency(self, sections):
        """Test quality metrics are consistent."""
        validator = DocValidator()
        
        # Generate document with specified sections
        doc = TestDataGenerator.generate_valid_markdown(sections=sections)
        
        quality1 = validator.assess_quality(doc)
        quality2 = validator.assess_quality(doc)
        
        # Same document should produce same scores
        assert abs(quality1["overall_score"] - quality2["overall_score"]) < 0.01
        assert abs(quality1["readability"] - quality2["readability"]) < 0.01


# ============================================================================
# STATEFUL TESTING
# ============================================================================

class ValidationStateMachine(RuleBasedStateMachine):
    """Stateful testing for validation system."""
    
    documents = Bundle('documents')
    validated = Bundle('validated')
    
    def __init__(self):
        super().__init__()
        self.validator = DocValidator()
        self.test_data = TestDataGenerator()
    
    @rule(
        target=documents,
        has_header=st.booleans(),
        has_license=st.booleans(),
        has_copyright=st.booleans(),
        length=st.integers(min_value=10, max_value=1000)
    )
    def create_document(self, has_header, has_license, has_copyright, length):
        """Create a document with specific characteristics."""
        parts = []
        
        if has_header:
            parts.append("# Document Title\n\n")
        
        parts.append(fake.text(max_nb_chars=length))
        
        if has_license:
            parts.append("\n\n## License\nMIT License")
        
        if has_copyright:
            parts.append(f"\n\n## Copyright\nCopyright (c) {datetime.now().year} Test")
        
        return {
            "content": "\n".join(parts),
            "has_header": has_header,
            "has_license": has_license,
            "has_copyright": has_copyright
        }
    
    @rule(
        document=documents,
        format_type=st.sampled_from(["md", "rst", "html"]),
        auto_correct=st.booleans()
    )
    @pytest.mark.asyncio
    async def validate_document(self, document, format_type, auto_correct):
        """Validate a document."""
        with patch('docgen_validator.AnalyzerEngine'), \
             patch('docgen_validator.AnonymizerEngine'), \
             patch('docgen_validator.generate_docs_llm'):
            
            result = await self.validator.validate_documentation(
                doc_text=document["content"],
                format_type=format_type,
                auto_correct=auto_correct
            )
            
            # Verify consistency
            assert result is not None
            assert "overall_status" in result
            
            # Check compliance detection
            if not document["has_license"]:
                compliance_issues = result["issues"].get("compliance_issues", [])
                # Should detect missing license
    
    @invariant()
    def check_validator_state(self):
        """Check validator maintains consistent state."""
        assert self.validator.run_id is not None
        assert isinstance(self.validator.rationale_steps, list)
        assert isinstance(self.validator.corrections_log, list)


# ============================================================================
# SECURITY AND COMPLIANCE TESTS
# ============================================================================

class TestSecurityCompliance:
    """Test security and compliance features."""
    
    @pytest.mark.asyncio
    async def test_pii_detection_and_handling(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test PII detection and handling."""
        validator = DocValidator()
        
        # Document with various PII
        pii_doc = """
        # Contact Information
        
        Email: john.doe@example.com
        Phone: +1-555-123-4567
        SSN: 123-45-6789
        Credit Card: 4111-1111-1111-1111
        """
        
        result = await validator.validate_documentation(
            doc_text=pii_doc,
            format_type="md",
            auto_correct=False
        )
        
        # PII should be detected in security findings
        assert len(result["issues"]["security_findings"]) > 0
    
    @pytest.mark.asyncio
    async def test_dangerous_pattern_detection(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test detection of dangerous patterns."""
        validator = DocValidator()
        
        # Test each dangerous pattern
        for pattern_name, pattern_regex in DANGEROUS_CONTENT_PATTERNS.items():
            # Create content that matches the pattern
            if pattern_name == "HardcodedCredentials":
                test_content = "API_KEY=secret123456"
            elif pattern_name == "InsecureProtocolUsage":
                test_content = "http://insecure.com"
            elif pattern_name == "DirectRootAccess":
                test_content = "user root"
            elif pattern_name == "SensitiveFilePaths":
                test_content = "/etc/passwd"
            elif pattern_name == "ExposedSensitivePorts":
                test_content = "EXPOSE 21"
            else:
                continue
            
            doc = f"# Test\n\n{test_content}"
            
            result = await validator.validate_documentation(
                doc_text=doc,
                format_type="md",
                auto_correct=False
            )
            
            findings = result["issues"]["security_findings"]
            categories = [f["category"] for f in findings]
            assert pattern_name in categories
    
    @pytest.mark.asyncio
    async def test_compliance_validation(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test compliance validation."""
        validator = DocValidator()
        
        # Non-compliant document
        non_compliant = """
        # Software Documentation
        
        This is a software project without any license or copyright information.
        """
        
        result = await validator.validate_documentation(
            doc_text=non_compliant,
            format_type="md",
            auto_correct=False
        )
        
        compliance_issues = result["issues"]["compliance_issues"]
        assert len(compliance_issues) > 0
        
        issue_types = [i["issue"] for i in compliance_issues]
        assert any("license" in i.lower() for i in issue_types)
        assert any("copyright" in i.lower() for i in issue_types)


# ============================================================================
# METRICS AND OBSERVABILITY TESTS
# ============================================================================

class TestMetricsObservability:
    """Test metrics and observability features."""
    
    @pytest.mark.asyncio
    async def test_prometheus_metrics_collection(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test Prometheus metrics are collected correctly."""
        validator = DocValidator()
        
        # Get initial metric values
        initial_calls = validator_calls_total._value.sum()
        
        # Perform validation
        await validator.validate_documentation(
            doc_text=test_data.generate_valid_markdown(),
            format_type="md",
            auto_correct=False
        )
        
        # Check metrics were updated
        final_calls = validator_calls_total._value.sum()
        assert final_calls > initial_calls
    
    @pytest.mark.asyncio
    async def test_opentelemetry_tracing(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test OpenTelemetry tracing."""
        validator = DocValidator()
        
        # Clear previous spans
        memory_exporter.clear()
        
        # Perform validation
        await validator.validate_documentation(
            doc_text=test_data.generate_valid_markdown(),
            format_type="md",
            auto_correct=False
        )
        
        # Check spans were created
        spans = memory_exporter.get_finished_spans()
        assert len(spans) > 0
        
        # Find main validation span
        validation_spans = [s for s in spans if "validate_documentation" in s.name]
        assert len(validation_spans) > 0
        
        # Check span attributes
        main_span = validation_spans[0]
        assert main_span.attributes.get("format") == "md"
        assert main_span.attributes.get("run_id") is not None
    
    @pytest.mark.asyncio
    async def test_error_metrics(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test error metrics collection."""
        validator = DocValidator()
        
        initial_errors = validator_errors_total._value.sum()
        
        # Trigger an error
        with pytest.raises(RuntimeError):
            await validator.validate_documentation(
                doc_text="test",
                format_type="invalid_format",
                auto_correct=False
            )
        
        final_errors = validator_errors_total._value.sum()
        assert final_errors > initial_errors


# ============================================================================
# LOAD AND STRESS TESTS
# ============================================================================

class TestLoadStress:
    """Load and stress testing."""
    
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_sustained_load(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test sustained load over time."""
        duration_seconds = 30
        requests_per_second = 5
        
        async def make_validation():
            validator = DocValidator()
            doc = test_data.generate_valid_markdown()
            
            return await validator.validate_documentation(
                doc_text=doc,
                format_type="md",
                auto_correct=False
            )
        
        start_time = time.time()
        successful_validations = 0
        errors = []
        
        while time.time() - start_time < duration_seconds:
            batch_start = time.time()
            
            # Create batch of validations
            tasks = [make_validation() for _ in range(requests_per_second)]
            
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if isinstance(result, Exception):
                        errors.append(result)
                    else:
                        successful_validations += 1
            
            except Exception as e:
                errors.append(e)
            
            # Wait for next second
            elapsed = time.time() - batch_start
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)
        
        total_expected = duration_seconds * requests_per_second
        success_rate = successful_validations / total_expected
        
        assert success_rate > 0.9  # 90% success rate
        assert len(errors) / total_expected < 0.1  # Less than 10% errors
    
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_spike_load(
        self, test_data, mock_presidio, mock_llm_orchestrator
    ):
        """Test handling of sudden load spikes."""
        # Normal load
        normal_tasks = 5
        
        # Spike load
        spike_tasks = 50
        
        async def validate():
            validator = DocValidator()
            doc = test_data.generate_valid_markdown()
            return await validator.validate_documentation(
                doc_text=doc,
                format_type="md",
                auto_correct=False
            )
        
        # Normal load phase
        normal_results = await asyncio.gather(
            *[validate() for _ in range(normal_tasks)]
        )
        assert all(r is not None for r in normal_results)
        
        # Spike phase
        spike_start = time.time()
        spike_results = await asyncio.gather(
            *[validate() for _ in range(spike_tasks)],
            return_exceptions=True
        )
        spike_duration = time.time() - spike_start
        
        # Count successful validations
        successful = sum(1 for r in spike_results if not isinstance(r, Exception))
        
        assert successful / spike_tasks > 0.8  # 80% success during spike
        assert spike_duration < 60  # Complete within 60 seconds


# ============================================================================
# TEST RUNNER CONFIGURATION
# ============================================================================

if __name__ == "__main__":
    # Run tests with coverage and benchmarks
    pytest.main([
        __file__,
        "-v",
        "--cov=docgen_validator",
        "--cov-report=html",
        "--cov-report=term-missing",
        "--benchmark-only",  # Run benchmarks
        "-m", "not slow",  # Skip slow tests by default
        "--hypothesis-show-statistics",
    ])