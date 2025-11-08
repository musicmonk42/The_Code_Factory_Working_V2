
"""
test_e2e_deployment.py

End-to-end integration test suite for the deployment pipeline.

Features:
- Tests full pipeline: prompt generation, LLM call, response handling, validation, and deployment simulation.
- Uses real implementations with mocked external dependencies (LLM, Presidio, external tools).
- Validates output correctness, provenance, and metrics.
- Tests error handling and edge cases.
- Comprehensive coverage of component interactions.
"""

import asyncio
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from faker import Faker
import aiofiles
from freezegun import freeze_time

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy_prompt import DeployPromptAgent, scrub_text as prompt_scrub_text
from deploy_llm_call import DeployLLMOrchestrator
from deploy_response_handler import handle_deploy_response, scrub_text as handler_scrub_text
from deploy_validator import validate_deployment_config, scrub_text as validator_scrub_text
from deploy_agent import DeployAgent, PluginRegistry

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_REPO_PATH = "/tmp/test_e2e_repo"
TEST_PLUGIN_DIR = "/tmp/test_e2e_plugins"
TEST_DB_PATH = "/tmp/test_e2e_deploy.db"
TEST_TEMPLATE_DIR = "/tmp/test_e2e_templates"
TEST_FEW_SHOT_DIR = "/tmp/test_e2e_few_shot"
MOCK_RUN_ID = str(uuid.uuid4())


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
    for path in [TEST_REPO_PATH, TEST_PLUGIN_DIR, TEST_DB_PATH, TEST_TEMPLATE_DIR, TEST_FEW_SHOT_DIR]:
        if Path(path).exists():
            import shutil
            shutil.rmtree(path)
    
    # Create test directories
    for path in [TEST_REPO_PATH, TEST_PLUGIN_DIR, TEST_TEMPLATE_DIR, TEST_FEW_SHOT_DIR]:
        Path(path).mkdir(parents=True, exist_ok=True)
    
    yield
    
    # Clean up after test
    for path in [TEST_REPO_PATH, TEST_PLUGIN_DIR, TEST_DB_PATH, TEST_TEMPLATE_DIR, TEST_FEW_SHOT_DIR]:
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
app = flask.Flask(__name__)

@app.route('/')
def hello():
    return 'Hello, World!'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
""",
        "requirements.txt": "flask==2.0.1\nrequests==2.27.1",
        "README.md": "# Test App\nA simple Flask application."
    }
    
    for filename, content in files.items():
        file_path = repo_path / filename
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(content)
    
    # Initialize git repo
    try:
        import subprocess
        subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass  # Git not available, tests will still work without commit history
    
    yield repo_path


@pytest_asyncio.fixture
async def mock_presidio():
    """Mock Presidio analyzer and anonymizer for all modules."""
    with patch('deploy_prompt.AnalyzerEngine') as mock_prompt_analyzer, \
         patch('deploy_prompt.AnonymizerEngine') as mock_prompt_anonymizer, \
         patch('deploy_response_handler.AnalyzerEngine') as mock_handler_analyzer, \
         patch('deploy_response_handler.AnonymizerEngine') as mock_handler_anonymizer, \
         patch('deploy_validator.AnalyzerEngine') as mock_validator_analyzer, \
         patch('deploy_validator.AnonymizerEngine') as mock_validator_anonymizer:
        
        mock_analyzer = MagicMock()
        mock_anonymizer = MagicMock()
        
        # Configure analyzer to find PII
        mock_analyzer.analyze.return_value = [
            MagicMock(entity_type='EMAIL_ADDRESS', start=10, end=25),
            MagicMock(entity_type='CREDIT_CARD', start=30, end=46)
        ]
        
        # Configure anonymizer to redact
        mock_anonymizer.anonymize.return_value = MagicMock(
            text="[REDACTED_EMAIL] [REDACTED_CREDIT_CARD]"
        )
        
        mock_prompt_analyzer.return_value = mock_analyzer
        mock_prompt_anonymizer.return_value = mock_anonymizer
        mock_handler_analyzer.return_value = mock_analyzer
        mock_handler_anonymizer.return_value = mock_anonymizer
        mock_validator_analyzer.return_value = mock_analyzer
        mock_validator_anonymizer.return_value = mock_anonymizer
        
        yield mock_analyzer, mock_anonymizer


@pytest_asyncio.fixture
async def mock_llm_orchestrator():
    """Create a DeployLLMOrchestrator instance with mocked provider."""
    orch = DeployLLMOrchestrator(provider_plugin_dir=TEST_PLUGIN_DIR)
    
    # Mock the provider to return a controlled response
    mock_provider = AsyncMock()
    mock_provider.__class__.__name__ = "MockProvider"
    mock_provider.call = AsyncMock(return_value={
        "content": json.dumps({
            "config": {
                "type": "docker",
                "content": """
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
"""
            },
            "model": "gpt-4o",
            "provider": "mock",
            "valid": True
        })
    )
    mock_provider.count_tokens = AsyncMock(return_value=100)
    mock_provider._calculate_cost.return_value = 0.01
    mock_provider.health_check = AsyncMock(return_value=True)
    
    orch.registry.providers = {"MockProvider": mock_provider}
    orch.circuit_breakers = {"MockProvider": MagicMock(can_proceed=Mock(return_value=True))}
    
    # Override database path
    orch.conn.close()
    orch.db_path = TEST_DB_PATH
    orch.conn = sqlite3.connect(TEST_DB_PATH)
    orch._setup_database()
    
    yield orch
    
    # Cleanup
    orch.conn.close()


@pytest_asyncio.fixture
async def deploy_agent(mock_llm_orchestrator, test_repository):
    """Create a DeployAgent instance with mocked dependencies."""
    # Create a mock plugin for docker
    mock_plugin = AsyncMock()
    mock_plugin.__version__ = "1.0.0"
    mock_plugin.health_check.return_value = True
    mock_plugin.generate_config = AsyncMock(return_value={
        "type": "docker",
        "config": """
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
"""
    })
    mock_plugin.validate_config = AsyncMock(return_value={
        "valid": True,
        "details": "Validation passed"
    })
    mock_plugin.simulate_deployment = AsyncMock(return_value={
        "status": "success",
        "message": "Deployment simulation successful"
    })
    
    agent = DeployAgent(
        repo_path=str(test_repository),
        languages_supported=["python"],
        plugin_dir=TEST_PLUGIN_DIR,
        llm_orchestrator_instance=mock_llm_orchestrator
    )
    
    # Register mock plugin
    agent.registry.register("docker", mock_plugin)
    
    yield agent
    
    # Cleanup
    if agent.db:
        agent.db.close()


@pytest.fixture
def create_template():
    """Helper to create Jinja2 template files."""
    def _create(name: str, content: str):
        template_path = Path(TEST_TEMPLATE_DIR) / name
        template_path.write_text(content, encoding='utf-8')
        return template_path
    return _create


# ============================================================================
# E2E INTEGRATION TESTS
# ============================================================================

class TestE2EDeploymentPipeline:
    """End-to-end tests for the deployment pipeline."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_full_pipeline_docker(self, test_repository, mock_llm_orchestrator, deploy_agent, create_template, mock_presidio):
        """Test the full deployment pipeline for a Docker configuration."""
        # Setup: Create a Jinja2 template
        create_template("docker_default.jinja", """
Generate a production-grade Dockerfile for {{ context.language }}:
{% for file, content in context.files_content.items() %}
- File: {{ file }}
  Content: {{ content | scrub }}
{% endfor %}
Instructions: {{ instructions }}
""")

        # Step 1: Initialize DeployPromptAgent
        prompt_agent = DeployPromptAgent(
            repo_path=str(test_repository),
            template_dir=TEST_TEMPLATE_DIR,
            few_shot_dir=TEST_FEW_SHOT_DIR
        )

        # Step 2: Generate prompt
        target = "docker"
        files = ["main.py", "requirements.txt"]
        instructions = "Generate a minimal, secure Dockerfile for production deployment."
        model_info = {"name": "gpt-4o", "few_shot_support": True, "token_limit": 8000}
        
        with freeze_time("2025-09-01T12:00:00Z"):
            prompt = await prompt_agent.build_deploy_prompt(
                target=target,
                files=files,
                instructions=instructions,
                variant="default",
                context=None,
                model_specific_info=model_info
            )

        # Verify prompt contains expected content
        assert "Dockerfile" in prompt
        assert "main.py" in prompt
        assert "requirements.txt" in prompt
        assert "Generate a minimal, secure Dockerfile" in prompt

        # Step 3: Generate configuration using DeployLLMOrchestrator
        config_result = await mock_llm_orchestrator.generate_config(
            prompt=prompt,
            model="gpt-4o",
            stream=False
        )

        # Verify LLM output
        assert "content" in config_result
        config_content = json.loads(config_result["content"])
        assert config_content["config"]["type"] == "docker"
        assert "FROM python:3.9-slim" in config_content["config"]["content"]

        # Step 4: Handle response with deploy_response_handler
        response_result = await handle_deploy_response(
            raw_response=config_content["config"]["content"],
            output_format="dockerfile",
            to_format="dockerfile",
            repo_path=str(test_repository)
        )

        # Verify response handling
        assert "final_config_output" in response_result
        assert "FROM python:3.9-slim" in response_result["final_config_output"]
        assert "provenance" in response_result
        assert response_result["provenance"]["run_id"]
        assert response_result["provenance"]["timestamp"] == "2025-09-01T12:00:00Z"
        assert "lint_issues" in response_result["provenance"]["quality_analysis"]

        # Step 5: Validate configuration with deploy_validator
        validation_result = await validate_deployment_config(
            config=response_result["structured_data"],
            config_format="dockerfile"
        )

        # Verify validation
        assert validation_result["valid"]
        assert validation_result["details"] == "Validation passed"

        # Step 6: Simulate deployment with DeployAgent
        deploy_result = await deploy_agent.generate_config(
            targets=[target],
            instructions=instructions,
            llm_model="gpt-4o",
            files=files
        )

        # Verify deployment result
        assert deploy_result["run_id"] == deploy_agent.run_id
        assert deploy_result["configs"][target]["type"] == "docker"
        assert deploy_result["validations"][target]["valid"]
        assert deploy_result["simulations"][target]["status"] == "success"
        assert "provenance" in deploy_result
        assert deploy_result["provenance"]["model_used"] == "gpt-4o"

        # Step 7: Verify database logging
        cursor = deploy_agent.db.cursor()
        cursor.execute("SELECT result FROM history WHERE id=?", (deploy_result["run_id"],))
        db_result = cursor.fetchone()
        assert db_result is not None
        stored_result = json.loads(db_result[0])
        assert stored_result["run_id"] == deploy_result["run_id"]

        # Step 8: Verify security scrubbing
        sensitive_content = "api_key=sk-1234567890abcdef email=test@example.com"
        scrubbed_prompt = prompt_scrub_text(sensitive_content)
        scrubbed_handler = handler_scrub_text(sensitive_content)
        scrubbed_validator = validator_scrub_text(sensitive_content)
        assert "[REDACTED]" in scrubbed_prompt
        assert "[REDACTED]" in scrubbed_handler
        assert "[REDACTED]" in scrubbed_validator

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_pipeline_with_invalid_config(self, test_repository, mock_llm_orchestrator, deploy_agent, create_template, mock_presidio):
        """Test the pipeline with an invalid configuration and self-healing."""
        # Setup: Create a Jinja2 template
        create_template("docker_default.jinja", """
Generate a production-grade Dockerfile for {{ context.language }}:
{% for file, content in context.files_content.items() %}
- File: {{ file }}
  Content: {{ content | scrub }}
{% endfor %}
Instructions: {{ instructions }}
""")

        # Mock LLM to return an invalid config
        original_call = mock_llm_orchestrator.registry.providers["MockProvider"].call
        async def invalid_config_call(prompt, model, stream=False, **kwargs):
            return {
                "content": json.dumps({
                    "config": {
                        "type": "docker",
                        "content": "RUN apt-get update"  # Invalid: missing FROM
                    },
                    "model": "gpt-4o",
                    "provider": "mock",
                    "valid": False
                })
            }
        mock_llm_orchestrator.registry.providers["MockProvider"].call = invalid_config_call

        # Step 1: Initialize DeployPromptAgent
        prompt_agent = DeployPromptAgent(
            repo_path=str(test_repository),
            template_dir=TEST_TEMPLATE_DIR,
            few_shot_dir=TEST_FEW_SHOT_DIR
        )

        # Step 2: Generate prompt
        target = "docker"
        files = ["main.py", "requirements.txt"]
        instructions = "Generate a minimal, secure Dockerfile for production deployment."
        model_info = {"name": "gpt-4o", "few_shot_support": True, "token_limit": 8000}
        
        with freeze_time("2025-09-01T12:00:00Z"):
            prompt = await prompt_agent.build_deploy_prompt(
                target=target,
                files=files,
                instructions=instructions,
                variant="default",
                context=None,
                model_specific_info=model_info
            )

        # Step 3: Generate configuration
        config_result = await mock_llm_orchestrator.generate_config(
            prompt=prompt,
            model="gpt-4o",
            stream=False
        )

        # Step 4: Handle response
        response_result = await handle_deploy_response(
            raw_response=json.loads(config_result["content"])["config"]["content"],
            output_format="dockerfile",
            to_format="dockerfile",
            repo_path=str(test_repository)
        )

        # Verify response contains issues
        assert "lint_issues" in response_result["provenance"]["quality_analysis"]
        assert any("missing a FROM instruction" in issue for issue in response_result["provenance"]["quality_analysis"]["lint_issues"])

        # Step 5: Validate configuration
        validation_result = await validate_deployment_config(
            config=response_result["structured_data"],
            config_format="dockerfile"
        )

        # Verify validation fails
        assert not validation_result["valid"]
        assert "missing a FROM instruction" in validation_result["details"]

        # Step 6: Test self-healing
        mock_llm_orchestrator.registry.providers["MockProvider"].call = AsyncMock(return_value={
            "content": json.dumps({
                "config": {
                    "type": "docker",
                    "content": """
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
"""
                },
                "model": "gpt-4o",
                "provider": "mock",
                "valid": True
            })
        })

        healed_result = await deploy_agent.self_heal(
            result={
                "configs": {"docker": json.loads(config_result["content"])["config"]},
                "validations": {"docker": validation_result},
                "run_id": MOCK_RUN_ID
            },
            targets=[target],
            llm_model="gpt-4o",
            max_attempts=1
        )

        # Verify self-healing
        assert healed_result is not None
        assert healed_result["configs"]["docker"]["type"] == "docker"
        assert "FROM python:3.9-slim" in healed_result["configs"]["docker"]["content"]
        assert healed_result["validations"]["docker"]["valid"]
        assert healed_result["provenance"]["heal_rationale"]

        # Restore original mock
        mock_llm_orchestrator.registry.providers["MockProvider"].call = original_call


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=deploy_prompt",
        "--cov=deploy_llm_call",
        "--cov=deploy_response_handler",
        "--cov=deploy_validator",
        "--cov=deploy_agent",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
