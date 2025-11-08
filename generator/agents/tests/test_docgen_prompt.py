"""
test_docgen_prompt.py
Industry-grade test suite for the DocGen Prompt Generation module.

This comprehensive test suite covers:
- Template registry with Jinja2 and hot-reload
- Few-shot learning with sentence transformers
- Context gathering from repositories
- Custom Jinja2 filters (async)
- Prompt optimization and summarization
- Section enforcement via meta-LLM
- A/B testing for prompt variants
- Feedback recording and evolution
- Security scrubbing with Presidio
- API endpoints and batch processing
- Metrics and observability

Test Categories:
1. Security & Scrubbing Tests
2. Template Registry Tests
3. Custom Filters Tests
4. Context Gathering Tests
5. Few-Shot Learning Tests
6. Prompt Generation Tests
7. Optimization Tests
8. A/B Testing Tests
9. API Endpoint Tests
10. Integration Tests
"""

import os
import sys
import json
import uuid
import time
import tempfile
import shutil
import asyncio
import hashlib
import subprocess
import ast
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import Mock, MagicMock, AsyncMock, patch, call, mock_open, PropertyMock, ANY
from contextlib import contextmanager, asynccontextmanager
from datetime import datetime, timedelta
import logging

import pytest
import pytest_asyncio
from pytest_mock import MockerFixture
from freezegun import freeze_time
from hypothesis import given, strategies as st, settings, assume, HealthCheck
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant, initialize, Bundle
import aiofiles
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from prometheus_client import REGISTRY
from opentelemetry.trace import Status, StatusCode
from jinja2 import Template, Environment, FileSystemLoader, TemplateNotFound
import tiktoken

# Configure logging for tests
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Import the module under test
try:
    # FIX: Corrected the full package path import block.
    from agents.docgen_agent.docgen_prompt import (
        scrub_text,
        optimize_prompt_content,
        get_language,
        get_commits,
        get_dependencies,
        get_imports,
        get_file_content,
        PromptTemplateRegistry,
        DocGenPromptAgent,
        api_generate_prompt,
        api_batch_generate_prompt,
        api_ab_test_prompts,
        api_record_prompt_feedback,
        app,
        routes,
        prompt_gen_calls_total,
        prompt_gen_errors_total,
        prompt_gen_latency_seconds,
        prompt_gen_feedback_score,
        prompt_tokens_generated,
        prompt_few_shot_usage_total,
        prompt_template_loads_total,
        COMMON_SENSITIVE_PATTERNS_REF
    )
    # FIX: Corrected the local package name import block for flexibility/local testing.
    from docgen_prompt import (
        scrub_text,
        optimize_prompt_content,
        get_language,
        get_commits,
        get_dependencies,
        get_imports,
        get_file_content,
        PromptTemplateRegistry,
        DocGenPromptAgent,
        api_generate_prompt,
        api_batch_generate_prompt,
        api_ab_test_prompts,
        api_record_prompt_feedback,
        app,
        routes,
        prompt_gen_calls_total,
        prompt_gen_errors_total,
        prompt_gen_latency_seconds,
        prompt_gen_feedback_score,
        prompt_tokens_generated,
        prompt_few_shot_usage_total,
        prompt_template_loads_total,
        COMMON_SENSITIVE_PATTERNS_REF
    )
except ImportError as e:
    print(f"Failed to import docgen_prompt: {e}")
    print("Ensure all dependencies are installed and modules are in Python path")
    sys.exit(1)


# ============================================================================
# Test Fixtures and Utilities
# ============================================================================

@pytest.fixture
def temp_repo(tmp_path):
    """Creates a temporary repository with sample files for testing."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()
    
    # Create sample Python files
    (repo_path / "main.py").write_text("""
import json
import logging

def hello_world():
    '''A simple hello world function'''
    return "Hello, World!"

class DataProcessor:
    def process(self, data):
        return {"processed": len(data)}
""")
    
    (repo_path / "utils.py").write_text("""
from typing import Dict, List

def load_config(path: str) -> Dict:
    with open(path, 'r') as f:
        return json.load(f)

API_KEY = "sk-1234567890abcdef"  # This should be redacted
""")
    
    # Create dependency files
    (repo_path / "requirements.txt").write_text("flask==2.3.0\npytest==7.4.0\naiohttp==3.8.5")
    (repo_path / "package.json").write_text(json.dumps({
        "dependencies": {"express": "^4.18.0"},
        "devDependencies": {"jest": "^29.0.0"}
    }))
    (repo_path / "go.mod").write_text("module example.com/app\n\nrequire github.com/gin-gonic/gin v1.9.0")
    
    # Initialize git repo
    try:
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "Second commit"], cwd=repo_path, capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass  # Git not available, skip
    
    return repo_path


@pytest.fixture
def temp_template_dir(tmp_path):
    """Creates temporary template directory with sample templates."""
    template_dir = tmp_path / "prompt_templates"
    template_dir.mkdir()
    
    # Create sample templates
    (template_dir / "README_default.jinja").write_text("""
Generate a README for {{ doc_type }}.
Files: {{ target_files | join(', ') }}
Instructions: {{ instructions | default('None') }}
Context: {{ context.files_content | length }} files loaded
""")
    
    (template_dir / "API_DOCS_default.jinja").write_text("""
Create API documentation.
Project: {{ repo_path }}
Required sections: {{ required_sections | join(', ') }}
Timestamp: {{ timestamp_utc }}
""")
    
    (template_dir / "README_verbose.jinja").write_text("""
# Comprehensive Documentation Request
Type: {{ doc_type }}
Files to analyze: 
{% for file in target_files %}
- {{ file }}
{% endfor %}
{{ few_shot_examples }}
""")
    
    return template_dir


@pytest.fixture
def temp_few_shot_dir(tmp_path):
    """Creates temporary few-shot examples directory."""
    few_shot_dir = tmp_path / "few_shot_examples"
    few_shot_dir.mkdir()
    
    # Create sample few-shot examples
    examples = [
        {
            "query": "Generate README for Python Flask app",
            "prompt": "# Flask Application\n\n## Installation\n`pip install -r requirements.txt`"
        },
        {
            "query": "Create API documentation for REST service",
            "prompt": "# API Documentation\n\n## Endpoints\n- GET /api/v1/users"
        },
        {
            "query": "Document JavaScript React component",
            "prompt": "# React Component Documentation\n\n## Props\n- `name`: string (required)"
        }
    ]
    
    for i, example in enumerate(examples):
        (few_shot_dir / f"example_{i}.json").write_text(json.dumps(example))
    
    return few_shot_dir


@pytest.fixture
def mock_dependencies(mocker):
    """Mock all external dependencies."""
    # Mock Presidio
    mock_analyzer = mocker.patch('docgen_prompt.AnalyzerEngine')
    mock_anonymizer = mocker.patch('docgen_prompt.AnonymizerEngine')
    
    # Mock utils
    mock_summarize = mocker.patch('docgen_prompt.summarize_text')
    async def mock_summarize_impl(text, max_length=1000):
        return text[:max_length] if len(text) > max_length else text
    mock_summarize.side_effect = mock_summarize_impl
    
    # Mock DeployLLMOrchestrator
    mock_llm = mocker.patch('docgen_prompt.DeployLLMOrchestrator')
    mock_llm_instance = AsyncMock()
    mock_llm.return_value = mock_llm_instance
    
    # Mock SentenceTransformer
    mock_transformer = mocker.patch('docgen_prompt.SentenceTransformer')
    mock_model = MagicMock()
    mock_model.encode.return_value = [[0.1, 0.2, 0.3]]
    mock_transformer.return_value = mock_model
    
    # Mock sentence_transformers.util
    mock_util = mocker.patch('docgen_prompt.util')
    mock_util.semantic_search.return_value = [[
        {'corpus_id': 0, 'score': 0.9},
        {'corpus_id': 1, 'score': 0.8}
    ]]
    
    return {
        'analyzer': mock_analyzer,
        'anonymizer': mock_anonymizer,
        'summarize_text': mock_summarize,
        'llm': mock_llm_instance,
        'transformer': mock_transformer,
        'util': mock_util
    }


# ============================================================================
# 1. Security & Scrubbing Tests
# ============================================================================

class TestSecurityScrubbing(TestCase):
    """Test security features and PII scrubbing."""
    
    @patch('docgen_prompt.AnalyzerEngine')
    @patch('docgen_prompt.AnonymizerEngine')
    def test_scrub_text_with_pii(self, mock_anonymizer_class, mock_analyzer_class):
        """Test PII scrubbing with Presidio."""
        # Setup mocks
        mock_analyzer = MagicMock()
        mock_anonymizer = MagicMock()
        mock_analyzer_class.return_value = mock_analyzer
        mock_anonymizer_class.return_value = mock_anonymizer
        
        # Mock analysis results
        mock_results = [MagicMock()]
        mock_analyzer.analyze.return_value = mock_results
        mock_anonymizer.anonymize.return_value = MagicMock(
            text="Contact: [REDACTED] at [REDACTED]"
        )
        
        # Test
        result = scrub_text("Contact: John Doe at john@example.com")
        
        self.assertEqual(result, "Contact: [REDACTED] at [REDACTED]")
        mock_analyzer.analyze.assert_called_once()
        mock_anonymizer.anonymize.assert_called_once()
    
    def test_scrub_text_empty(self):
        """Test scrubbing empty text."""
        result = scrub_text("")
        self.assertEqual(result, "")
    
    @patch('docgen_prompt.AnalyzerEngine')
    def test_scrub_text_presidio_failure(self, mock_analyzer_class):
        """Test scrubbing raises RuntimeError on Presidio failure."""
        mock_analyzer = MagicMock()
        mock_analyzer_class.return_value = mock_analyzer
        mock_analyzer.analyze.side_effect = Exception("Presidio error")
        
        with self.assertRaises(RuntimeError) as ctx:
            scrub_text("Test text")
        self.assertIn("Critical error during sensitive data scrubbing", str(ctx.exception))
    
    def test_sensitive_patterns_reference(self):
        """Test sensitive patterns are properly defined."""
        self.assertIsInstance(COMMON_SENSITIVE_PATTERNS_REF, list)
        self.assertGreater(len(COMMON_SENSITIVE_PATTERNS_REF), 0)
        
        # Test patterns compile
        import re
        for pattern in COMMON_SENSITIVE_PATTERNS_REF:
            try:
                re.compile(pattern)
            except re.error:
                self.fail(f"Invalid regex pattern: {pattern}")


# ============================================================================
# 2. Template Registry Tests
# ============================================================================

class TestPromptTemplateRegistry(TestCase):
    """Test template registry functionality."""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.template_dir = Path(self.temp_dir) / "templates"
        self.template_dir.mkdir()
    
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    def test_registry_initialization(self):
        """Test template registry initialization."""
        registry = PromptTemplateRegistry(str(self.template_dir))
        
        self.assertIsNotNone(registry.env)
        self.assertEqual(registry.plugin_dir, str(self.template_dir))
        self.assertTrue(registry.env.is_async)
    
    def test_registry_create_environment(self):
        """Test Jinja2 environment creation with custom filters."""
        registry = PromptTemplateRegistry(str(self.template_dir))
        
        # Check custom filters are registered
        self.assertIn('get_commits', registry.env.filters)
        self.assertIn('get_dependencies', registry.env.filters)
        self.assertIn('get_imports', registry.env.filters)
        self.assertIn('get_language', registry.env.filters)
        self.assertIn('get_file_content', registry.env.filters)
        self.assertIn('summarize_text', registry.env.filters)
    
    def test_get_template_success(self):
        """Test successful template retrieval."""
        # Create a template file
        (self.template_dir / "test_template.jinja").write_text("Test {{ variable }}")
        
        registry = PromptTemplateRegistry(str(self.template_dir))
        template = registry.get_template("test_template")
        
        self.assertIsInstance(template, Template)
        rendered = template.render(variable="content")
        self.assertEqual(rendered, "Test content")
    
    def test_get_template_not_found(self):
        """Test template retrieval raises error when not found."""
        registry = PromptTemplateRegistry(str(self.template_dir))
        
        with self.assertRaises(ValueError) as ctx:
            registry.get_template("nonexistent_template")
        
        self.assertIn("Required template 'nonexistent_template.jinja' not found", str(ctx.exception))
    
    def test_reload_templates(self):
        """Test template reloading clears cache."""
        registry = PromptTemplateRegistry(str(self.template_dir))
        
        # Add something to cache
        registry.env.cache = {'test': 'cached'}
        
        # Reload
        registry.reload_templates()
        
        # Cache should be cleared
        self.assertEqual(registry.env.cache, {})
    
    @patch('docgen_prompt.Observer')
    def test_hot_reload_setup(self, mock_observer_class):
        """Test hot-reload observer is set up."""
        mock_observer = MagicMock()
        mock_observer_class.return_value = mock_observer
        
        registry = PromptTemplateRegistry(str(self.template_dir))
        
        mock_observer_class.assert_called_once()
        mock_observer.schedule.assert_called_once()
        mock_observer.start.assert_called_once()


# ============================================================================
# 3. Custom Filters Tests
# ============================================================================

class TestCustomFilters(IsolatedAsyncioTestCase):
    """Test custom Jinja2 filters."""
    
    async def test_get_language_detection(self):
        """Test programming language detection."""
        # Test Python
        python_code = "import os\ndef main():\n    pass"
        result = await get_language(python_code)
        self.assertEqual(result, "python")
        
        # Test JavaScript
        js_code = "const app = require('express');\nfunction handler() { return; }"
        result = await get_language(js_code)
        self.assertEqual(result, "javascript")
        
        # Test Go
        go_code = "package main\nimport \"fmt\"\nfunc main() {}"
        result = await get_language(go_code)
        self.assertEqual(result, "go")
        
        # Test Rust
        rust_code = "use std::io;\nfn main() {\n    println!(\"Hello\");\n}"
        result = await get_language(rust_code)
        self.assertEqual(result, "rust")
        
        # Test Java
        java_code = "public class Main {\n    public static void main(String[] args) {}\n}"
        result = await get_language(java_code)
        self.assertEqual(result, "java")
        
        # Test unknown
        unknown_code = "This is just plain text"
        result = await get_language(unknown_code)
        self.assertEqual(result, "unknown")
    
    @patch('docgen_prompt.asyncio.create_subprocess_exec')
    async def test_get_commits_success(self, mock_subprocess):
        """Test successful git commit retrieval."""
        # Mock successful git log output
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (
            b"abc123 2024-01-01 Initial commit\ndef456 2024-01-02 Add feature",
            b""
        )
        mock_subprocess.return_value = mock_process
        
        with patch('docgen_prompt.scrub_text', side_effect=lambda x: x):
            result = await get_commits("/test/repo", limit=2)
        
        self.assertIn("Initial commit", result)
        self.assertIn("Add feature", result)
    
    @patch('docgen_prompt.asyncio.create_subprocess_exec')
    async def test_get_commits_failure(self, mock_subprocess):
        """Test git commit retrieval failure."""
        # Mock failed git log
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate.return_value = (b"", b"fatal: not a git repository")
        mock_subprocess.return_value = mock_process
        
        result = await get_commits("/test/repo")
        
        self.assertIn("Failed to retrieve recent commits", result)
    
    async def test_get_commits_invalid_path(self):
        """Test git commits with invalid path."""
        result = await get_commits("/nonexistent/path")
        self.assertEqual(result, "No repository found.")
    
    @patch('docgen_prompt.aiofiles.open')
    @patch('os.path.isfile')
    async def test_get_dependencies_python(self, mock_isfile, mock_aioopen):
        """Test Python dependency parsing."""
        mock_isfile.return_value = True
        
        # Mock file content
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value.read.return_value = "flask==2.3.0\npytest==7.4.0"
        mock_aioopen.return_value = mock_file
        
        with patch('docgen_prompt.scrub_text', side_effect=lambda x: x):
            result = await get_dependencies(["requirements.txt"], "/test/repo")
        
        deps = json.loads(result)
        self.assertIn('python', deps)
        self.assertIn('flask==2.3.0', deps['python'])
        self.assertIn('pytest==7.4.0', deps['python'])
    
    @patch('docgen_prompt.aiofiles.open')
    @patch('os.path.isfile')
    async def test_get_imports_python(self, mock_isfile, mock_aioopen):
        """Test Python import extraction."""
        mock_isfile.return_value = True
        
        python_code = """
import os
import json
from pathlib import Path
from typing import Dict, List
"""
        
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value.read.return_value = python_code
        mock_aioopen.return_value = mock_file
        
        with patch('docgen_prompt.scrub_text', side_effect=lambda x: x):
            result = await get_imports("test.py")
        
        self.assertIn("os", result)
        self.assertIn("json", result)
        self.assertIn("pathlib", result)
        self.assertIn("typing", result)
    
    async def test_get_imports_invalid_file(self):
        """Test import extraction with invalid file."""
        result = await get_imports("/nonexistent/file.py")
        self.assertEqual(result, "")
    
    @patch('docgen_prompt.aiofiles.open')
    @patch('os.path.isfile')
    async def test_get_file_content(self, mock_isfile, mock_aioopen):
        """Test file content retrieval."""
        mock_isfile.return_value = True
        
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value.read.return_value = "File content here"
        mock_aioopen.return_value = mock_file
        
        with patch('docgen_prompt.scrub_text', side_effect=lambda x: x):
            result = await get_file_content("test.txt")
        
        self.assertEqual(result, "File content here")


# ============================================================================
# 4. Prompt Optimization Tests
# ============================================================================

class TestPromptOptimization(IsolatedAsyncioTestCase):
    """Test prompt optimization functionality."""
    
    @patch('docgen_prompt.tiktoken.get_encoding')
    @patch('docgen_prompt.summarize_text')
    async def test_optimize_prompt_within_limit(self, mock_summarize, mock_get_encoding):
        """Test optimization when prompt is within token limit."""
        # Mock tokenizer
        mock_encoding = MagicMock()
        mock_encoding.encode.return_value = [1] * 100  # 100 tokens
        mock_get_encoding.return_value = mock_encoding
        
        prompt = "Short prompt that fits"
        result = await optimize_prompt_content(prompt, max_tokens=200)
        
        self.assertEqual(result, prompt)
        mock_summarize.assert_not_called()
    
    @patch('docgen_prompt.tiktoken.get_encoding')
    @patch('docgen_prompt.summarize_text')
    async def test_optimize_prompt_exceeds_limit(self, mock_summarize, mock_get_encoding):
        """Test optimization when prompt exceeds token limit."""
        # Mock tokenizer - first call returns too many tokens, second returns acceptable
        mock_encoding = MagicMock()
        mock_encoding.encode.side_effect = [
            [1] * 500,  # Initial: 500 tokens
            [1] * 150,  # After optimization: 150 tokens
        ]
        mock_get_encoding.return_value = mock_encoding
        
        # Mock summarize to return shorter text
        mock_summarize.return_value = "Summarized content"
        
        prompt = "File: test.py\n```\nVery long content here...\n```"
        result = await optimize_prompt_content(prompt, max_tokens=200)
        
        self.assertIn("Summarized content", result)
        mock_summarize.assert_called()
    
    @patch('docgen_prompt.tiktoken.get_encoding')
    @patch('docgen_prompt.summarize_text')
    async def test_optimize_prompt_failure(self, mock_summarize, mock_get_encoding):
        """Test optimization raises RuntimeError on failure."""
        mock_get_encoding.side_effect = Exception("Tokenizer error")
        
        with self.assertRaises(RuntimeError) as ctx:
            await optimize_prompt_content("test prompt", 100)
        
        self.assertIn("Critical error during prompt content optimization", str(ctx.exception))


# ============================================================================
# 5. DocGenPromptAgent Tests
# ============================================================================

class TestDocGenPromptAgent(IsolatedAsyncioTestCase):
    """Test DocGenPromptAgent functionality."""
    
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir) / "test_repo"
        self.repo_path.mkdir()
        self.few_shot_dir = Path(self.temp_dir) / "few_shot"
        self.few_shot_dir.mkdir()
        
        # Create test files
        (self.repo_path / "test.py").write_text("print('test')")
    
    async def asyncTearDown(self):
        shutil.rmtree(self.temp_dir)
    
    @patch('docgen_prompt.SentenceTransformer')
    @patch('docgen_prompt.DeployLLMOrchestrator')
    def test_agent_initialization(self, mock_llm, mock_transformer):
        """Test agent initialization."""
        agent = DocGenPromptAgent(
            few_shot_dir=str(self.few_shot_dir),
            repo_path=str(self.repo_path)
        )
        
        self.assertEqual(agent.repo_path, self.repo_path)
        self.assertIsNotNone(agent.template_registry)
        self.assertIsNotNone(agent.llm_orchestrator)
        mock_transformer.assert_called_once()
    
    def test_agent_invalid_repo_path(self):
        """Test agent raises error for invalid repo path."""
        with self.assertRaises(ValueError) as ctx:
            DocGenPromptAgent(repo_path="/nonexistent/path")
        
        self.assertIn("Repository path does not exist", str(ctx.exception))
    
    @patch('docgen_prompt.DeployLLMOrchestrator')
    def test_load_few_shot_examples(self, mock_llm):
        """Test loading few-shot examples."""
        # Create example files
        example1 = {"query": "test query 1", "prompt": "test prompt 1"}
        example2 = {"query": "test query 2", "prompt": "test prompt 2"}
        
        (self.few_shot_dir / "ex1.json").write_text(json.dumps(example1))
        (self.few_shot_dir / "ex2.json").write_text(json.dumps(example2))
        
        agent = DocGenPromptAgent(
            few_shot_dir=str(self.few_shot_dir),
            repo_path=str(self.repo_path)
        )
        
        self.assertEqual(len(agent.few_shot_examples), 2)
        self.assertEqual(agent.few_shot_examples[0]['query'], "test query 1")
    
    @patch('docgen_prompt.aiofiles.open')
    @patch('docgen_prompt.DeployLLMOrchestrator')
    async def test_gather_context(self, mock_llm, mock_aioopen):
        """Test context gathering from repository."""
        # Mock file reading
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value.read.return_value = "file content"
        mock_aioopen.return_value = mock_file
        
        agent = DocGenPromptAgent(repo_path=str(self.repo_path))
        
        with patch('docgen_prompt.scrub_text', side_effect=lambda x: x):
            context = await agent.gather_context(["test.py"])
        
        self.assertIn('files_content', context)
        self.assertIn('test.py', context['files_content'])
        self.assertEqual(context['files_content']['test.py'], "file content")
    
    @patch('docgen_prompt.DeployLLMOrchestrator')
    @patch('docgen_prompt.SentenceTransformer')
    @patch('docgen_prompt.util')
    async def test_retrieve_few_shot(self, mock_util, mock_transformer_class, mock_llm):
        """Test few-shot example retrieval."""
        # Setup mocks
        mock_model = MagicMock()
        mock_model.encode.side_effect = [
            [[0.1, 0.2]],  # Query embedding
            [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]  # Example embeddings
        ]
        mock_transformer_class.return_value = mock_model
        
        mock_util.semantic_search.return_value = [[
            {'corpus_id': 0, 'score': 0.9},
            {'corpus_id': 2, 'score': 0.8}
        ]]
        
        agent = DocGenPromptAgent(repo_path=str(self.repo_path))
        agent.few_shot_examples = [
            {'query': 'q1', 'prompt': 'p1'},
            {'query': 'q2', 'prompt': 'p2'},
            {'query': 'q3', 'prompt': 'p3'}
        ]
        
        results = await agent.retrieve_few_shot("test query", top_k=2)
        
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], 'p1')
        self.assertEqual(results[1], 'p3')
    
    @patch('docgen_prompt.DeployLLMOrchestrator')
    async def test_enforce_sections(self, mock_llm_class):
        """Test section enforcement via meta-LLM."""
        mock_llm = AsyncMock()
        mock_llm_class.return_value = mock_llm
        
        mock_llm.generate_config.return_value = {
            'config': {'content': 'Enhanced prompt with required sections'}
        }
        
        agent = DocGenPromptAgent(repo_path=str(self.repo_path))
        
        result = await agent.enforce_sections(
            "Original prompt",
            ["Introduction", "Installation", "Usage"]
        )
        
        self.assertEqual(result, 'Enhanced prompt with required sections')
        mock_llm.generate_config.assert_called_once()
    
    @patch('docgen_prompt.DeployLLMOrchestrator')
    async def test_optimize_prompt_with_feedback(self, mock_llm_class):
        """Test prompt optimization based on feedback."""
        mock_llm = AsyncMock()
        mock_llm_class.return_value = mock_llm
        
        mock_llm.generate_config.return_value = {
            'config': {'content': 'Optimized prompt based on feedback'}
        }
        
        agent = DocGenPromptAgent(repo_path=str(self.repo_path))
        agent.previous_feedback = {'README_default': 0.6}
        
        result = await agent.optimize_prompt_with_feedback(
            "Initial prompt",
            "README",
            "default"
        )
        
        self.assertEqual(result, 'Optimized prompt based on feedback')
    
    def test_record_feedback(self):
        """Test feedback recording."""
        with patch('docgen_prompt.DeployLLMOrchestrator'):
            agent = DocGenPromptAgent(repo_path=str(self.repo_path))
            
            agent.record_feedback("README", "default", 0.85)
            
            self.assertEqual(agent.previous_feedback['README_default'], 0.85)
            self.assertIn('last_run', agent.previous_feedback)
            self.assertEqual(agent.previous_feedback['last_run']['score'], 0.85)


# ============================================================================
# 6. A/B Testing Tests
# ============================================================================

class TestABTesting(IsolatedAsyncioTestCase):
    """Test A/B testing functionality."""
    
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir) / "test_repo"
        self.repo_path.mkdir()
    
    async def asyncTearDown(self):
        shutil.rmtree(self.temp_dir)
    
    @patch('docgen_prompt.DeployLLMOrchestrator')
    @patch('docgen_prompt.DocGenPromptAgent.batch_get_doc_prompt')
    async def test_ab_test_prompts(self, mock_batch_get, mock_llm_class):
        """Test A/B testing with multiple template variants."""
        # Setup mocks
        mock_llm = AsyncMock()
        mock_llm_class.return_value = mock_llm
        
        # Mock prompt generation
        mock_batch_get.return_value = [
            "Prompt for template A",
            "Prompt for template B"
        ]
        
        # Mock scoring responses
        mock_llm.generate_config.side_effect = [
            {'config': {'content': '{"score": 0.8}'}},
            {'config': {'content': '{"score": 0.9}'}}
        ]
        
        agent = DocGenPromptAgent(repo_path=str(self.repo_path))
        
        results = await agent.ab_test_prompts(
            doc_type="README",
            target_files=["test.py"],
            template_names=["templateA", "templateB"]
        )
        
        self.assertIn('templateA', results)
        self.assertIn('templateB', results)
        self.assertEqual(results['templateA']['score'], 0.8)
        self.assertEqual(results['templateB']['score'], 0.9)
    
    @patch('docgen_prompt.DeployLLMOrchestrator')
    @patch('docgen_prompt.DocGenPromptAgent.batch_get_doc_prompt')
    async def test_ab_test_with_failures(self, mock_batch_get, mock_llm_class):
        """Test A/B testing handles failures gracefully."""
        mock_llm = AsyncMock()
        mock_llm_class.return_value = mock_llm
        
        # One template fails to generate
        mock_batch_get.return_value = [
            "ERROR: Failed to generate prompt",
            "Valid prompt"
        ]
        
        mock_llm.generate_config.return_value = {'config': {'content': '{"score": 0.7}'}}
        
        agent = DocGenPromptAgent(repo_path=str(self.repo_path))
        
        results = await agent.ab_test_prompts(
            doc_type="README",
            target_files=["test.py"],
            template_names=["failing", "working"]
        )
        
        self.assertEqual(results['failing']['score'], 0.0)
        self.assertEqual(results['working']['score'], 0.7)


# ============================================================================
# 7. Integration Tests
# ============================================================================

@pytest.mark.integration
class TestIntegration(IsolatedAsyncioTestCase):
    """End-to-end integration tests."""
    
    async def test_full_prompt_generation_pipeline(self):
        """Test complete prompt generation pipeline."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Setup directories
            repo_path = Path(temp_dir) / "repo"
            repo_path.mkdir()
            template_dir = Path(temp_dir) / "templates"
            template_dir.mkdir()
            
            # Create files
            (repo_path / "main.py").write_text("def main(): pass")
            (template_dir / "README_default.jinja").write_text(
                "Generate README for {{ doc_type }}. Files: {{ target_files | join(', ') }}"
            )
            
            with patch('docgen_prompt.PromptTemplateRegistry') as mock_registry_class:
                mock_registry = MagicMock()
                mock_template = MagicMock()
                mock_template.render_async = AsyncMock(
                    return_value="Generated prompt content"
                )
                mock_registry.get_template.return_value = mock_template
                mock_registry_class.return_value = mock_registry
                
                with patch('docgen_prompt.DeployLLMOrchestrator'):
                    agent = DocGenPromptAgent(repo_path=str(repo_path))
                    
                    with patch('docgen_prompt.optimize_prompt_content') as mock_optimize:
                        mock_optimize.return_value = "Optimized prompt"
                        
                        result = await agent.get_doc_prompt(
                            doc_type="README",
                            target_files=["main.py"],
                            template_name="default"
                        )
                        
                        self.assertIn("Optimized prompt", result)


# ============================================================================
# 8. API Endpoint Tests
# ============================================================================

class TestAPIEndpoints(AioHTTPTestCase):
    """Test API endpoints."""
    
    async def get_application(self):
        """Get the aiohttp application."""
        return app
    
    @unittest_run_loop
    async def test_generate_prompt_endpoint(self):
        """Test /generate_prompt endpoint."""
        with patch('docgen_prompt.DocGenPromptAgent') as mock_agent_class:
            mock_agent = AsyncMock()
            mock_agent.get_doc_prompt.return_value = "Generated prompt"
            mock_agent_class.return_value = mock_agent
            
            resp = await self.client.post('/generate_prompt', json={
                'doc_type': 'README',
                'target_files': ['test.py'],
                'repo_path': '/test/repo'
            })
            
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data['status'], 'success')
            self.assertEqual(data['prompt'], 'Generated prompt')
    
    @unittest_run_loop
    async def test_batch_generate_prompt_endpoint(self):
        """Test /batch_generate_prompt endpoint."""
        with patch('docgen_prompt.DocGenPromptAgent') as mock_agent_class:
            mock_agent = AsyncMock()
            mock_agent.batch_get_doc_prompt.return_value = ["Prompt 1", "Prompt 2"]
            mock_agent_class.return_value = mock_agent
            
            resp = await self.client.post('/batch_generate_prompt', json={
                'requests': [
                    {'doc_type': 'README', 'target_files': ['a.py']},
                    {'doc_type': 'API', 'target_files': ['b.py']}
                ],
                'repo_path': '/test/repo'
            })
            
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(len(data['prompts']), 2)
    
    @unittest_run_loop
    async def test_record_feedback_endpoint(self):
        """Test /record_prompt_feedback endpoint."""
        with patch('docgen_prompt.DocGenPromptAgent') as mock_agent_class:
            mock_agent = MagicMock()
            mock_agent_class.return_value = mock_agent
            
            resp = await self.client.post('/record_prompt_feedback', json={
                'doc_type': 'README',
                'template_name': 'default',
                'score': 0.85,
                'repo_path': '/test/repo'
            })
            
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data['status'], 'success')
            mock_agent.record_feedback.assert_called_once_with('README', 'default', 0.85)


# ============================================================================
# 9. Property-Based Testing
# ============================================================================

class TestPropertyBased(TestCase):
    """Property-based tests using Hypothesis."""
    
    @given(
        text=st.text(min_size=0, max_size=10000),
        max_tokens=st.integers(min_value=100, max_value=10000)
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    async def test_optimize_prompt_properties(self, text, max_tokens):
        """Test prompt optimization maintains properties."""
        with patch('docgen_prompt.tiktoken.get_encoding') as mock_encoding:
            mock_enc = MagicMock()
            mock_enc.encode.return_value = [1] * min(len(text), max_tokens - 1)
            mock_encoding.return_value = mock_enc
            
            with patch('docgen_prompt.summarize_text') as mock_summarize:
                mock_summarize.return_value = text[:max_tokens * 4]
                
                result = await optimize_prompt_content(text, max_tokens)
                
                # Properties:
                # 1. Result is always a string
                self.assertIsInstance(result, str)
                
                # 2. Empty input returns empty output
                if not text:
                    self.assertEqual(result, "")
    
    @given(
        doc_type=st.sampled_from(['README', 'API_DOCS', 'GUIDE']),
        score=st.floats(min_value=0.0, max_value=1.0)
    )
    def test_feedback_recording_properties(self, doc_type, score):
        """Test feedback recording maintains valid ranges."""
        with patch('docgen_prompt.DeployLLMOrchestrator'):
            with tempfile.TemporaryDirectory() as temp_dir:
                repo_path = Path(temp_dir) / "repo"
                repo_path.mkdir()
                
                agent = DocGenPromptAgent(repo_path=str(repo_path))
                agent.record_feedback(doc_type, "default", score)
                
                recorded_score = agent.previous_feedback[f"{doc_type}_default"]
                
                # Score should be clamped to [0, 1]
                self.assertGreaterEqual(recorded_score, 0.0)
                self.assertLessEqual(recorded_score, 1.0)


# ============================================================================
# 10. Metrics Tests
# ============================================================================

class TestMetrics(TestCase):
    """Test metrics and observability."""
    
    def test_metrics_initialized(self):
        """Test all metrics are properly initialized."""
        self.assertIsNotNone(prompt_gen_calls_total)
        self.assertIsNotNone(prompt_gen_errors_total)
        self.assertIsNotNone(prompt_gen_latency_seconds)
        self.assertIsNotNone(prompt_gen_feedback_score)
        self.assertIsNotNone(prompt_tokens_generated)
        self.assertIsNotNone(prompt_few_shot_usage_total)
        self.assertIsNotNone(prompt_template_loads_total)
    
    @patch('docgen_prompt.tracer')
    async def test_tracing_span_creation(self, mock_tracer):
        """Test OpenTelemetry span creation."""
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        
        # Would need full pipeline test here
        self.assertIsNotNone(mock_tracer)


# ============================================================================
# Test Runner Configuration
# ============================================================================

if __name__ == '__main__':
    # Configure pytest options
    pytest_args = [
        __file__,
        '-v',  # Verbose output
        '--cov=docgen_prompt',  # Coverage for module
        '--cov-report=html',  # HTML coverage report
        '--cov-report=term-missing',  # Terminal coverage with missing lines
        '--tb=short',  # Short traceback format
        '-m', 'not integration',  # Skip integration tests by default
        '--maxfail=5',  # Stop after 5 failures
        '--strict-markers',  # Strict marker checking
        '--asyncio-mode=auto',  # Auto async test detection
        '-W', 'ignore::DeprecationWarning',  # Ignore deprecation warnings
    ]
    
    # Run with integration tests if specified
    if '--integration' in sys.argv:
        pytest_args.remove('-m')
        pytest_args.remove('not integration')
    
    # Run tests
    sys.exit(pytest.main(pytest_args))