"""
test_deploy_prompt.py

Industry-grade test suite for DeployPromptAgent with comprehensive coverage including:
- Template registry and hot-reload
- Few-shot learning and retrieval
- Meta-LLM prompt optimization
- Security and data scrubbing
- Async Jinja2 filters
- A/B testing capabilities
- Performance and concurrency
- API endpoints
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from unittest.mock import Mock, AsyncMock, patch, MagicMock, call, ANY

import pytest
import pytest_asyncio
from pytest_mock import MockerFixture
import aiofiles
from freezegun import freeze_time
from hypothesis import given, strategies as st, settings
from faker import Faker
from jinja2 import Template, Environment
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

# Test utilities
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from deploy_prompt import (
    DeployPromptAgent,
    PromptTemplateRegistry,
    scrub_text,
    optimize_deployment_prompt_text,
    get_language,
    get_commits,
    get_dependencies,
    get_imports,
    get_file_content,
    app
)

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_TEMPLATE_DIR = "/tmp/test_deploy_templates"
TEST_FEW_SHOT_DIR = "/tmp/test_few_shot"
TEST_REPO_PATH = "/tmp/test_prompt_repo"


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture(scope="function")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def cleanup_test_environment():
    """Clean up test environment before and after tests."""
    # Clean up before test
    for path in [TEST_TEMPLATE_DIR, TEST_FEW_SHOT_DIR, TEST_REPO_PATH]:
        if Path(path).exists():
            import shutil
            shutil.rmtree(path)
    
    # Create test directories
    Path(TEST_TEMPLATE_DIR).mkdir(parents=True, exist_ok=True)
    Path(TEST_FEW_SHOT_DIR).mkdir(parents=True, exist_ok=True)
    Path(TEST_REPO_PATH).mkdir(parents=True, exist_ok=True)
    
    yield
    
    # Clean up after test
    for path in [TEST_TEMPLATE_DIR, TEST_FEW_SHOT_DIR, TEST_REPO_PATH]:
        if Path(path).exists():
            import shutil
            shutil.rmtree(path)


@pytest_asyncio.fixture
async def test_repository():
    """Create a test repository with sample files and git history."""
    repo_path = Path(TEST_REPO_PATH)
    
    # Create sample files
    files = {
        "main.py": """
import flask
import requests
from datetime import datetime

app = flask.Flask(__name__)

@app.route('/')
def hello():
    return 'Hello World!'

if __name__ == '__main__':
    app.run()
""",
        "requirements.txt": "flask==2.0.1\nrequests==2.27.1\npytest==7.0.0",
        "package.json": json.dumps({
            "name": "test-app",
            "version": "1.0.0",
            "dependencies": {
                "express": "^4.17.1",
                "axios": "^0.27.2"
            },
            "devDependencies": {
                "jest": "^27.0.0"
            }
        }),
        "go.mod": """
module github.com/test/app

go 1.17

require (
    github.com/gin-gonic/gin v1.7.0
    github.com/stretchr/testify v1.7.0
)
""",
        "Cargo.toml": """
[package]
name = "test-app"
version = "0.1.0"

[dependencies]
actix-web = "4.0"
tokio = { version = "1", features = ["full"] }
serde = "1.0"
""",
        "pom.xml": """
<project>
    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
            <version>2.6.0</version>
        </dependency>
    </dependencies>
</project>
"""
    }
    
    for filename, content in files.items():
        file_path = repo_path / filename
        async with aiofiles.open(file_path, 'w') as f:
            await f.write(content)
    
    # Initialize git repo
    try:
        subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "Second commit"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "Third commit"], cwd=repo_path, check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass  # Git not available, tests will handle this
    
    return repo_path


@pytest.fixture
def create_template():
    """Helper to create Jinja2 template files."""
    def _create(name: str, content: str):
        template_path = Path(TEST_TEMPLATE_DIR) / name
        template_path.write_text(content)
        return template_path
    return _create


@pytest.fixture
def create_few_shot_example():
    """Helper to create few-shot example files."""
    def _create(name: str, query: str, example: str):
        example_data = {"query": query, "example": example}
        example_path = Path(TEST_FEW_SHOT_DIR) / name
        example_path.write_text(json.dumps(example_data))
        return example_path
    return _create


@pytest_asyncio.fixture
async def mock_llm_orchestrator(mocker):
    """Mock the DeployLLMOrchestrator."""
    mock_orchestrator = AsyncMock()
    
    async def mock_generate_config(prompt, model, stream=False, ensemble=False):
        # Return a mock optimized prompt for meta-LLM calls
        if "improve the following prompt" in prompt.lower():
            return {
                "config": {"content": "Optimized prompt: Generate secure Docker config with constraints."}
            }
        # Return a mock score for A/B testing
        elif "evaluate the quality" in prompt.lower():
            return {
                "config": {"content": '{"score": 0.85}'}
            }
        return {
            "config": {"content": "Mock LLM response"}
        }
    
    mock_orchestrator.generate_config = mock_generate_config
    mocker.patch('deploy_prompt.DeployLLMOrchestrator', return_value=mock_orchestrator)
    return mock_orchestrator


@pytest_asyncio.fixture
async def prompt_agent(test_repository, mock_llm_orchestrator):
    """Create a DeployPromptAgent instance."""
    agent = DeployPromptAgent(
        few_shot_dir=TEST_FEW_SHOT_DIR,
        repo_path=str(test_repository)
    )
    agent.template_registry.template_dir = TEST_TEMPLATE_DIR
    agent.template_registry.env = agent.template_registry._create_environment()
    return agent


@pytest.fixture
def mock_metrics(mocker):
    """Mock Prometheus metrics."""
    metrics = {
        'prompt_gen_calls': mocker.patch('deploy_prompt.prompt_gen_calls'),
        'prompt_gen_errors': mocker.patch('deploy_prompt.prompt_gen_errors'),
        'prompt_gen_latency': mocker.patch('deploy_prompt.prompt_gen_latency'),
        'prompt_feedback_score': mocker.patch('deploy_prompt.prompt_feedback_score'),
        'prompt_tokens_generated': mocker.patch('deploy_prompt.prompt_tokens_generated'),
        'FEW_SHOT_USAGE': mocker.patch('deploy_prompt.FEW_SHOT_USAGE'),
        'TEMPLATE_LOADS': mocker.patch('deploy_prompt.TEMPLATE_LOADS'),
    }
    return metrics


# ============================================================================
# UNIT TESTS - Security Features
# ============================================================================

class TestSecurityFeatures:
    """Test security and data scrubbing functionality."""
    
    def test_scrub_text_basic_patterns(self):
        """Test basic sensitive pattern scrubbing."""
        test_cases = [
            ("api_key=sk-abc123def456ghi789jkl", "api_key=[REDACTED]"),
            ("API-KEY: xyz987654321abcdefghij", "API-KEY: [REDACTED]"),
            ("password: SuperSecret123!", "password: [REDACTED]"),
            ("email: user@example.com", "email: [REDACTED]"),
            ("SSN: 123-45-6789", "SSN: [REDACTED]"),
            ("card: 4111111111111111", "card: [REDACTED]"),
            ("Bearer eyJhbGciOiJIUzI1NiIs", "Bearer [REDACTED]"),
            ("ghp_1234567890abcdef1234567890abcdef1234", "[REDACTED]"),
            ("sk-proj-abcdefghijklmnopqrstuvwxyz123456", "[REDACTED]"),
        ]
        
        for input_text, expected in test_cases:
            result = scrub_text(input_text)
            assert result == expected, f"Failed for input: {input_text}"
    
    def test_scrub_text_multiple_patterns(self):
        """Test scrubbing multiple sensitive patterns in one text."""
        text = """
        Configuration:
        api_key=sk-secret123456789012345
        password: mypass123
        admin_email: admin@company.com
        token: Bearer abc123def456
        github_token: ghp_abcdef123456789012345678901234567890
        """
        
        result = scrub_text(text)
        
        assert "secret123456789012345" not in result
        assert "mypass123" not in result
        assert "admin@company.com" not in result
        assert "abc123def456" not in result
        assert "ghp_" not in result
        assert result.count("[REDACTED]") >= 5
    
    @patch('deploy_prompt.AnalyzerEngine')
    @patch('deploy_prompt.AnonymizerEngine')
    def test_scrub_text_with_presidio(self, mock_anonymizer_class, mock_analyzer_class):
        """Test scrubbing with Presidio when available."""
        # Setup mocks
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = [Mock(start=0, end=10)]
        mock_analyzer_class.return_value = mock_analyzer
        
        mock_anonymizer = Mock()
        mock_anonymizer.anonymize.return_value = Mock(text="[REDACTED] data")
        mock_anonymizer_class.return_value = mock_anonymizer
        
        result = scrub_text("sensitive data")
        
        assert "[REDACTED]" in result
        mock_analyzer.analyze.assert_called_once()
        mock_anonymizer.anonymize.assert_called_once()
    
    def test_scrub_text_empty_input(self):
        """Test scrubbing with empty or None input."""
        assert scrub_text("") == ""
        assert scrub_text(None) == ""


# ============================================================================
# UNIT TESTS - Template Registry
# ============================================================================

class TestPromptTemplateRegistry:
    """Test template registry functionality."""
    
    def test_initialization(self):
        """Test PromptTemplateRegistry initialization."""
        registry = PromptTemplateRegistry(TEST_TEMPLATE_DIR)
        
        assert registry.template_dir == TEST_TEMPLATE_DIR
        assert registry.env is not None
        assert Path(TEST_TEMPLATE_DIR).exists()
    
    def test_custom_filters_registered(self):
        """Test that custom filters are registered in Jinja2 environment."""
        registry = PromptTemplateRegistry(TEST_TEMPLATE_DIR)
        
        assert 'get_commits' in registry.env.filters
        assert 'get_dependencies' in registry.env.filters
        assert 'get_imports' in registry.env.filters
        assert 'get_language' in registry.env.filters
        assert 'get_file_content' in registry.env.filters
        assert 'summarize_text' in registry.env.filters
    
    def test_get_template_success(self, create_template):
        """Test successful template retrieval."""
        create_template("docker_default.jinja", "Docker template: {{ target }}")
        
        registry = PromptTemplateRegistry(TEST_TEMPLATE_DIR)
        template = registry.get_template("docker", "default")
        
        assert template is not None
        assert isinstance(template, Template)
    
    def test_get_template_missing(self):
        """Test error when template is missing."""
        registry = PromptTemplateRegistry(TEST_TEMPLATE_DIR)
        
        with pytest.raises(ValueError, match="Required template.*not found"):
            registry.get_template("nonexistent", "default")
    
    def test_template_hot_reload(self, create_template):
        """Test template hot-reload functionality."""
        # Create initial template
        template_path = create_template("test_default.jinja", "Version 1")
        
        registry = PromptTemplateRegistry(TEST_TEMPLATE_DIR)
        template_v1 = registry.get_template("test", "default")
        
        # Modify template
        template_path.write_text("Version 2")
        
        # Trigger reload
        registry.reload_templates()
        
        # Get updated template
        template_v2 = registry.get_template("test", "default")
        
        # Verify cache was cleared (templates should be different objects)
        assert template_v1 is not template_v2


# ============================================================================
# UNIT TESTS - Async Jinja2 Filters
# ============================================================================

class TestAsyncJinja2Filters:
    """Test async Jinja2 filter functions."""
    
    @pytest.mark.asyncio
    async def test_get_language_detection(self):
        """Test programming language detection."""
        test_cases = [
            ("import os\ndef main():\n    pass", "python"),
            ("function test() { return true; }", "javascript"),
            ("package main\nfunc main() {}", "go"),
            ("fn main() {\n    use std::io;\n}", "rust"),
            ("public class Main { public static void main(String[] args) {} }", "java"),
            ("Unknown content", "unknown"),
        ]
        
        for content, expected in test_cases:
            result = await get_language(content)
            assert result == expected
    
    @pytest.mark.asyncio
    async def test_get_commits(self, test_repository):
        """Test git commit retrieval."""
        result = await get_commits(str(test_repository), limit=2)
        
        # Check if git is available
        if "No repository found" not in result and "Git command not available" not in result:
            assert "commit" in result.lower() or "initial" in result.lower()
    
    @pytest.mark.asyncio
    async def test_get_commits_invalid_repo(self):
        """Test git commits with invalid repository."""
        result = await get_commits("/nonexistent/path", limit=5)
        assert "No repository found" in result
    
    @pytest.mark.asyncio
    async def test_get_dependencies(self, test_repository):
        """Test dependency extraction from various files."""
        files = ["requirements.txt", "package.json", "go.mod", "Cargo.toml", "pom.xml"]
        result = await get_dependencies(files, str(test_repository))
        
        deps = json.loads(result)
        
        # Check Python dependencies
        if "python" in deps:
            assert "flask==2.0.1" in deps["python"]
        
        # Check JavaScript dependencies
        if "javascript" in deps:
            assert "express" in deps["javascript"]["dependencies"]
        
        # Check Go dependencies
        if "go" in deps:
            assert "github.com/gin-gonic/gin" in deps["go"]
        
        # Check Rust dependencies
        if "rust" in deps:
            assert "actix-web" in deps["rust"]
        
        # Check Java dependencies
        if "java" in deps:
            assert len(deps["java"]) > 0
    
    @pytest.mark.asyncio
    async def test_get_imports(self, test_repository):
        """Test Python import extraction."""
        result = await get_imports(str(test_repository / "main.py"))
        
        assert "flask" in result
        assert "requests" in result
        assert "datetime" in result
    
    @pytest.mark.asyncio
    async def test_get_imports_non_python_file(self, test_repository):
        """Test import extraction from non-Python file."""
        result = await get_imports(str(test_repository / "package.json"))
        assert result == ""
    
    @pytest.mark.asyncio
    async def test_get_file_content(self, test_repository):
        """Test file content retrieval."""
        result = await get_file_content(str(test_repository / "requirements.txt"))
        
        assert "flask" in result
        assert "requests" in result
    
    @pytest.mark.asyncio
    async def test_get_file_content_missing(self):
        """Test file content retrieval for missing file."""
        result = await get_file_content("/nonexistent/file.txt")
        assert result == ""


# ============================================================================
# UNIT TESTS - DeployPromptAgent
# ============================================================================

class TestDeployPromptAgent:
    """Test DeployPromptAgent functionality."""
    
    @pytest.mark.asyncio
    async def test_initialization(self, test_repository):
        """Test DeployPromptAgent initialization."""
        agent = DeployPromptAgent(
            few_shot_dir=TEST_FEW_SHOT_DIR,
            repo_path=str(test_repository)
        )
        
        assert agent.repo_path == str(test_repository)
        assert agent.template_registry is not None
        assert agent.few_shot_examples == []  # Empty initially
        assert agent.previous_feedback == {}
        assert agent.llm_orchestrator is not None
    
    @pytest.mark.asyncio
    async def test_load_few_shot_examples(self, create_few_shot_example):
        """Test loading few-shot examples."""
        create_few_shot_example("example1.json", "test query 1", "test example 1")
        create_few_shot_example("example2.json", "test query 2", "test example 2")
        
        agent = DeployPromptAgent(few_shot_dir=TEST_FEW_SHOT_DIR)
        
        assert len(agent.few_shot_examples) == 2
        assert any(ex["query"] == "test query 1" for ex in agent.few_shot_examples)
        assert any(ex["example"] == "test example 2" for ex in agent.few_shot_examples)
    
    @pytest.mark.asyncio
    async def test_gather_context_for_prompt(self, prompt_agent, test_repository):
        """Test context gathering from repository files."""
        files = ["main.py", "requirements.txt"]
        context = await prompt_agent.gather_context_for_prompt(files)
        
        assert "files_content" in context
        assert "main.py" in context["files_content"]
        assert "requirements.txt" in context["files_content"]
        assert "flask" in context["files_content"]["main.py"]
    
    @pytest.mark.asyncio
    async def test_retrieve_few_shot_with_embedding(self, prompt_agent, create_few_shot_example):
        """Test few-shot retrieval with embedding model."""
        # Create examples
        create_few_shot_example("docker.json", "docker deployment", "FROM python:3.9")
        create_few_shot_example("k8s.json", "kubernetes deployment", "apiVersion: apps/v1")
        
        # Reload examples
        prompt_agent.few_shot_examples = prompt_agent._load_few_shot(TEST_FEW_SHOT_DIR)
        
        # Test retrieval
        if prompt_agent.embedding_model:
            results = await prompt_agent.retrieve_few_shot("docker config", top_k=1)
            assert len(results) <= 1
            if results:
                assert "python" in results[0] or "FROM" in results[0]
    
    @pytest.mark.asyncio
    async def test_optimize_prompt_with_feedback(self, prompt_agent):
        """Test prompt optimization with feedback."""
        # Record some feedback
        prompt_agent.record_feedback("docker", "default", 0.3)
        
        # Test optimization
        initial_prompt = "Generate Docker config"
        optimized = await prompt_agent.optimize_prompt_with_feedback(
            initial_prompt, "docker", "default", "gpt-4o"
        )
        
        # Should return optimized version
        assert optimized != initial_prompt
        assert "secure" in optimized.lower() or "constraints" in optimized.lower()
    
    @pytest.mark.asyncio
    async def test_build_deploy_prompt_basic(self, prompt_agent, create_template):
        """Test basic prompt building."""
        # Create template
        create_template("docker_default.jinja", """
Target: {{ target }}
Files: {{ files | join(', ') }}
Instructions: {{ instructions }}
Context: {{ context.files_content | length }} files loaded
""")
        
        prompt = await prompt_agent.build_deploy_prompt(
            target="docker",
            files=["main.py"],
            instructions="Create minimal config"
        )
        
        assert "Target: docker" in prompt
        assert "Files: main.py" in prompt
        assert "Instructions: Create minimal config" in prompt
    
    @pytest.mark.asyncio
    async def test_build_deploy_prompt_with_few_shot(self, prompt_agent, create_template, create_few_shot_example):
        """Test prompt building with few-shot examples."""
        # Create template with few-shot support
        create_template("docker_default.jinja", """
Target: {{ target }}
{{ few_shot_examples }}
""")
        
        # Create few-shot example
        create_few_shot_example("docker.json", "docker config", "FROM python:3.9")
        prompt_agent.few_shot_examples = prompt_agent._load_few_shot(TEST_FEW_SHOT_DIR)
        
        # Build prompt with model that supports few-shot
        prompt = await prompt_agent.build_deploy_prompt(
            target="docker",
            files=["main.py"],
            model_specific_info={"name": "gpt-4", "few_shot_support": True}
        )
        
        # Check if few-shot was included (if embedding model is available)
        if prompt_agent.embedding_model:
            assert "Few-shot Examples" in prompt or "FROM python" in prompt
    
    @pytest.mark.asyncio
    async def test_build_deploy_prompt_error_handling(self, prompt_agent):
        """Test prompt building with missing template."""
        # No template exists
        prompt = await prompt_agent.build_deploy_prompt(
            target="nonexistent",
            files=["main.py"]
        )
        
        # Should return fallback prompt
        assert "Generate production-grade" in prompt
        assert "nonexistent" in prompt
    
    @pytest.mark.asyncio
    async def test_ab_test_prompts(self, prompt_agent, create_template):
        """Test A/B testing of prompt variants."""
        # Create templates for variants
        create_template("docker_default.jinja", "Default: {{ target }}")
        create_template("docker_secure.jinja", "Secure: {{ target }}")
        
        results = await prompt_agent.ab_test_prompts(
            target="docker",
            files=["main.py"],
            variants=["default", "secure"]
        )
        
        assert "default" in results
        assert "secure" in results
        
        # Check structure
        for variant, data in results.items():
            assert "prompt" in data
            assert "length" in data
            assert "hash" in data
            assert "score" in data
            
            # Score should be 0.85 based on mock
            if "Error" not in data["prompt"]:
                assert data["score"] == 0.85
    
    def test_record_feedback(self, prompt_agent):
        """Test feedback recording."""
        prompt_agent.record_feedback("docker", "default", 0.75)
        
        assert prompt_agent.previous_feedback["docker_default"] == 0.75
        assert prompt_agent.previous_feedback["last_run"]["score"] == 0.75
        assert prompt_agent.previous_feedback["last_run"]["variant"] == "default"
    
    def test_record_feedback_clamping(self, prompt_agent):
        """Test feedback score clamping to [0, 1]."""
        prompt_agent.record_feedback("docker", "test", 1.5)
        assert prompt_agent.previous_feedback["docker_test"] == 1.0
        
        prompt_agent.record_feedback("docker", "test2", -0.5)
        assert prompt_agent.previous_feedback["docker_test2"] == 0.0


# ============================================================================
# UNIT TESTS - Prompt Optimization
# ============================================================================

class TestPromptOptimization:
    """Test prompt optimization functionality."""
    
    @pytest.mark.asyncio
    @patch('deploy_prompt.summarize_text')
    async def test_optimize_deployment_prompt_text(self, mock_summarize):
        """Test text-based prompt optimization."""
        mock_summarize.return_value = "Summarized prompt content"
        
        result = await optimize_deployment_prompt_text("Very long prompt " * 1000)
        
        assert result == "Summarized prompt content"
        mock_summarize.assert_called_once()
    
    @pytest.mark.asyncio
    @patch('deploy_prompt.summarize_text')
    async def test_optimize_deployment_prompt_text_error(self, mock_summarize):
        """Test optimization error handling."""
        mock_summarize.side_effect = Exception("Summarization failed")
        
        original = "Original prompt"
        result = await optimize_deployment_prompt_text(original)
        
        assert result == original  # Should return original on error


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestIntegration:
    """End-to-end integration tests."""
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_full_prompt_generation_pipeline(self, prompt_agent, create_template, create_few_shot_example):
        """Test complete prompt generation pipeline."""
        # Setup template
        create_template("kubernetes_production.jinja", """
Deployment Target: {{ target }}
Environment: Production
Files to analyze: {{ files | join(', ') }}

Repository commits:
{{ repo_path | get_commits(3) }}

Dependencies detected:
{{ ['requirements.txt', 'package.json'] | get_dependencies(repo_path) }}

Instructions: {{ instructions }}

{{ few_shot_examples }}

Please generate a production-ready Kubernetes configuration.
""")
        
        # Setup few-shot example
        create_few_shot_example(
            "k8s_example.json",
            "kubernetes production deployment",
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: example"
        )
        prompt_agent.few_shot_examples = prompt_agent._load_few_shot(TEST_FEW_SHOT_DIR)
        
        # Generate prompt
        prompt = await prompt_agent.build_deploy_prompt(
            target="kubernetes",
            files=["main.py", "requirements.txt"],
            instructions="Ensure high availability",
            variant="production",
            model_specific_info={"name": "gpt-4", "few_shot_support": True}
        )
        
        # Verify components
        assert "Deployment Target: kubernetes" in prompt
        assert "Environment: Production" in prompt
        assert "main.py, requirements.txt" in prompt
        assert "Instructions: Ensure high availability" in prompt
        
        # Check if dynamic content was included
        if "Git command not available" not in prompt:
            assert "commit" in prompt.lower() or "repository" in prompt.lower()
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_feedback_driven_optimization(self, prompt_agent, create_template):
        """Test feedback-driven prompt evolution."""
        # Create initial template
        create_template("docker_default.jinja", "Basic prompt: {{ target }}")
        
        # Generate initial prompt
        prompt1 = await prompt_agent.build_deploy_prompt("docker", ["main.py"])
        
        # Record poor feedback
        prompt_agent.record_feedback("docker", "default", 0.2)
        
        # Generate improved prompt
        prompt2 = await prompt_agent.build_deploy_prompt("docker", ["main.py"])
        
        # Should be different due to optimization
        assert prompt2 != prompt1
        assert "secure" in prompt2.lower() or "optimized" in prompt2.lower()


# ============================================================================
# API TESTS
# ============================================================================

class TestAPIEndpoints(AioHTTPTestCase):
    """Test aiohttp API endpoints."""
    
    async def get_application(self):
        """Return the aiohttp application."""
        return app
    
    @unittest_run_loop
    async def test_generate_prompt_endpoint(self):
        """Test /generate_prompt API endpoint."""
        # Create test template
        Path(TEST_TEMPLATE_DIR).mkdir(exist_ok=True)
        template_path = Path(TEST_TEMPLATE_DIR) / "docker_default.jinja"
        template_path.write_text("API Test: {{ target }}")
        
        data = {
            "target": "docker",
            "files": ["main.py"],
            "instructions": "Test instructions",
            "variant": "default",
            "repo_path": TEST_REPO_PATH
        }
        
        resp = await self.client.post("/generate_prompt", json=data)
        
        assert resp.status == 200
        result = await resp.json()
        assert "prompt" in result
        assert "status" in result
        assert result["status"] == "success"
    
    @unittest_run_loop
    async def test_ab_test_prompts_endpoint(self):
        """Test /ab_test_prompts API endpoint."""
        # Create test templates
        Path(TEST_TEMPLATE_DIR).mkdir(exist_ok=True)
        for variant in ["default", "secure"]:
            template_path = Path(TEST_TEMPLATE_DIR) / f"docker_{variant}.jinja"
            template_path.write_text(f"Variant {variant}: {{{{ target }}}}")
        
        data = {
            "target": "docker",
            "files": ["main.py"],
            "variants": ["default", "secure"],
            "repo_path": TEST_REPO_PATH
        }
        
        resp = await self.client.post("/ab_test_prompts", json=data)
        
        assert resp.status == 200
        result = await resp.json()
        assert "results" in result
        assert "default" in result["results"]
        assert "secure" in result["results"]
    
    @unittest_run_loop
    async def test_record_feedback_endpoint(self):
        """Test /record_prompt_feedback API endpoint."""
        data = {
            "target": "docker",
            "variant": "default",
            "score": 0.85,
            "repo_path": TEST_REPO_PATH
        }
        
        resp = await self.client.post("/record_prompt_feedback", json=data)
        
        assert resp.status == 200
        result = await resp.json()
        assert result["status"] == "success"
    
    @unittest_run_loop
    async def test_api_error_handling(self):
        """Test API error handling."""
        # Invalid data (missing required fields)
        data = {"invalid": "data"}
        
        resp = await self.client.post("/generate_prompt", json=data)
        
        assert resp.status == 500
        result = await resp.json()
        assert result["status"] == "error"


# ============================================================================
# PERFORMANCE TESTS
# ============================================================================

class TestPerformance:
    """Test performance and concurrency."""
    
    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_concurrent_prompt_generation(self, prompt_agent, create_template):
        """Test concurrent prompt generation."""
        create_template("test_default.jinja", "Concurrent: {{ target }}")
        
        tasks = [
            prompt_agent.build_deploy_prompt(f"target{i}", ["main.py"])
            for i in range(10)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # All should succeed
        assert len(results) == 10
        for result in results:
            assert not isinstance(result, Exception)
            assert "Concurrent:" in result or "Generate production-grade" in result
    
    @pytest.mark.asyncio
    async def test_large_context_handling(self, prompt_agent, test_repository):
        """Test handling of large context data."""
        # Create large file
        large_file = test_repository / "large.txt"
        async with aiofiles.open(large_file, 'w') as f:
            await f.write("x" * 100000)  # 100KB file
        
        context = await prompt_agent.gather_context_for_prompt(["large.txt"])
        
        assert "large.txt" in context["files_content"]
        assert len(context["files_content"]["large.txt"]) > 0


# ============================================================================
# PROPERTY-BASED TESTS
# ============================================================================

class TestPropertyBased:
    """Property-based tests using Hypothesis."""
    
    @given(
        text=st.text(min_size=0, max_size=1000),
        include_secrets=st.booleans()
    )
    def test_scrub_text_properties(self, text, include_secrets):
        """Property: scrubbed text never contains sensitive patterns."""
        if include_secrets:
            text = f"api_key=sk-{fake.sha256()[:32]} {text}"
        
        result = scrub_text(text)
        
        # Properties
        assert "sk-" not in result or "[REDACTED]" in result
        assert len(result) <= len(text) + result.count("[REDACTED]") * 20
    
    @given(
        score=st.floats(min_value=-10, max_value=10)
    )
    def test_feedback_score_clamping(self, score):
        """Property: feedback scores are always clamped to [0, 1]."""
        agent = DeployPromptAgent()
        agent.record_feedback("test", "variant", score)
        
        recorded_score = agent.previous_feedback["test_variant"]
        assert 0.0 <= recorded_score <= 1.0
    
    @given(
        files=st.lists(st.text(min_size=1, max_size=50), min_size=0, max_size=10)
    )
    @pytest.mark.asyncio
    async def test_context_gathering_properties(self, files):
        """Property: context gathering handles any file list."""
        agent = DeployPromptAgent(repo_path=TEST_REPO_PATH)
        
        # Should not crash regardless of input
        context = await agent.gather_context_for_prompt(files)
        
        assert "files_content" in context
        assert isinstance(context["files_content"], dict)


# ============================================================================
# EDGE CASES
# ============================================================================

class TestEdgeCases:
    """Test edge cases and error scenarios."""
    
    @pytest.mark.asyncio
    async def test_empty_template_directory(self):
        """Test behavior with empty template directory."""
        empty_dir = "/tmp/empty_templates"
        Path(empty_dir).mkdir(exist_ok=True)
        
        registry = PromptTemplateRegistry(empty_dir)
        
        with pytest.raises(ValueError):
            registry.get_template("any", "default")
    
    @pytest.mark.asyncio
    async def test_malformed_few_shot_examples(self, prompt_agent):
        """Test handling of malformed few-shot examples."""
        # Create malformed example
        bad_example_path = Path(TEST_FEW_SHOT_DIR) / "bad.json"
        bad_example_path.write_text("not valid json")
        
        # Should handle gracefully
        examples = prompt_agent._load_few_shot(TEST_FEW_SHOT_DIR)
        # Malformed example should be skipped
        assert all("query" in ex and "example" in ex for ex in examples)
    
    @pytest.mark.asyncio
    async def test_unicode_in_templates(self, prompt_agent, create_template):
        """Test handling of Unicode in templates."""
        create_template("unicode_default.jinja", "Unicode: 你好世界 🚀 émojis {{ target }}")
        
        prompt = await prompt_agent.build_deploy_prompt("unicode", ["main.py"])
        
        assert "你好世界" in prompt
        assert "🚀" in prompt
        assert "émojis" in prompt
    
    @pytest.mark.asyncio
    async def test_circular_dependency_detection(self, prompt_agent):
        """Test handling of potential circular dependencies."""
        # This is more of a design test - ensure no infinite loops
        prompt_agent.previous_feedback["test_default"] = 0.5
        
        # Should not cause infinite loop
        result = await prompt_agent.optimize_prompt_with_feedback(
            "test prompt", "test", "default", "gpt-4"
        )
        
        assert result is not None


if __name__ == "__main__":
    # Run tests with coverage
    pytest.main([
        __file__,
        "-v",
        "--cov=deploy_prompt",
        "--cov-report=html",
        "--cov-report=term-missing",
        "-m", "not integration"  # Skip integration tests by default
    ])
