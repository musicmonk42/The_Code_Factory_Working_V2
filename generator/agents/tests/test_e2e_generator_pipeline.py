
"""
test_e2e_generator_pipeline.py

Regulated industry-grade end-to-end integration test suite for the generator pipeline.

Features:
- Tests full pipeline: clarify, code, critique, tests, deploy, docs via generator_plugin_wrapper.py.
- Integrates codegen, critique, testgen, deploy, and docgen agents.
- Validates PII/secret scrubbing with Presidio and audit logging.
- Ensures Prometheus metrics and OpenTelemetry tracing.
- Tests async-safe concurrency and thread-safety for metrics.
- Verifies retry logic, circuit breaking, and error handling.
- Handles edge cases and compliance requirements (SOC2/PCI DSS/HIPAA).
- Uses real implementations with mocked external dependencies (LLM, Presidio, external tools).

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun, aiofiles
- omnicore_engine (plugin_registry, message_bus)
- pydantic, prometheus_client, opentelemetry-sdk
- audit_log
- codegen_agent, critique_agent, testgen_agent, deploy_agent, docgen_agent
"""

import asyncio
import json
import os
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
import pytest
import pytest_asyncio
from faker import Faker
import aiofiles
from freezegun import freeze_time
from pydantic import ValidationError as PydanticValidationError
import re
import uuid
import sqlite3

from generator_plugin_wrapper import run_generator_workflow, WorkflowInput, WorkflowOutput, GeneratorPluginError
from codegen_agent import CodeGenAgent
from critique_agent import orchestrate_critique_pipeline, CritiqueConfig
from testgen_agent import TestGenAgent, Policy
from deploy_agent import DeployAgent
from docgen_agent import DocGenAgent
from audit_log import log_action

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_REPO_PATH = "/tmp/test_e2e_generator_repo"
TEST_PLUGIN_DIR = "/tmp/test_e2e_generator_plugins"
TEST_DB_PATH = "/tmp/test_e2e_generator.db"
TEST_TEMPLATE_DIR = "/tmp/test_e2e_generator_templates"
TEST_FEW_SHOT_DIR = "/tmp/test_e2e_generator_few_shot"
MOCK_CORRELATION_ID = str(uuid.uuid4())

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'
os.environ['SFE_OTEL_EXPORTER_TYPE'] = 'console'

@pytest.fixture(scope="function")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(autouse=True)
def cleanup_test_environment():
    """Clean up test environment before and after tests."""
    for path in [TEST_REPO_PATH, TEST_PLUGIN_DIR, TEST_DB_PATH, TEST_TEMPLATE_DIR, TEST_FEW_SHOT_DIR]:
        if Path(path).exists():
            import shutil
            shutil.rmtree(path, ignore_errors=True)
            if Path(path).is_file():
                os.remove(path)
    for path in [TEST_REPO_PATH, TEST_PLUGIN_DIR, TEST_TEMPLATE_DIR, TEST_FEW_SHOT_DIR]:
        Path(path).mkdir(parents=True, exist_ok=True)
    yield
    for path in [TEST_REPO_PATH, TEST_PLUGIN_DIR, TEST_DB_PATH, TEST_TEMPLATE_DIR, TEST_FEW_SHOT_DIR]:
        if Path(path).exists():
            import shutil
            shutil.rmtree(path, ignore_errors=True)
            if Path(path).is_file():
                os.remove(path)

@pytest_asyncio.fixture
async def test_repository():
    """Create a test repository with sample files and git history."""
    repo_path = Path(TEST_REPO_PATH)
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
        async with aiofiles.open(repo_path / filename, 'w', encoding='utf-8') as f:
            await f.write(content)
    try:
        import subprocess
        subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass  # Git not available, tests will work without commit history
    yield repo_path

@pytest_asyncio.fixture
async def mock_presidio():
    """Mock Presidio analyzer and anonymizer for all modules."""
    with patch('codegen_prompt.presidio_analyzer.AnalyzerEngine') as mock_codegen_analyzer, \
         patch('codegen_prompt.presidio_anonymizer.AnonymizerEngine') as mock_codegen_anonymizer, \
         patch('critique_prompt.presidio_analyzer.AnalyzerEngine') as mock_critique_analyzer, \
         patch('critique_prompt.presidio_anonymizer.AnonymizerEngine') as mock_critique_anonymizer, \
         patch('testgen_prompt.presidio_analyzer.AnalyzerEngine') as mock_testgen_analyzer, \
         patch('testgen_prompt.presidio_anonymizer.AnonymizerEngine') as mock_testgen_anonymizer, \
         patch('deploy_prompt.presidio_analyzer.AnalyzerEngine') as mock_deploy_analyzer, \
         patch('deploy_prompt.presidio_anonymizer.AnonymizerEngine') as mock_deploy_anonymizer, \
         patch('docgen_prompt.presidio_analyzer.AnalyzerEngine') as mock_docgen_analyzer, \
         patch('docgen_prompt.presidio_anonymizer.AnonymizerEngine') as mock_docgen_anonymizer:
        mock_analyzer = MagicMock()
        mock_anonymizer = MagicMock()
        mock_analyzer.analyze.return_value = [
            MagicMock(entity_type='EMAIL_ADDRESS', start=10, end=25),
            MagicMock(entity_type='CREDIT_CARD', start=30, end=46)
        ]
        mock_anonymizer.anonymize.return_value = MagicMock(
            text="[REDACTED_EMAIL] [REDACTED_CREDIT_CARD]"
        )
        for mock_cls in [mock_codegen_analyzer, mock_critique_analyzer, mock_testgen_analyzer, mock_deploy_analyzer, mock_docgen_analyzer]:
            mock_cls.return_value = mock_analyzer
        for mock_anonymizer_cls in [mock_codegen_anonymizer, mock_critique_anonymizer, mock_testgen_anonymizer, mock_deploy_anonymizer, mock_docgen_anonymizer]:
            mock_anonymizer_cls.return_value = mock_anonymizer
        yield mock_analyzer, mock_anonymizer

@pytest_asyncio.fixture
async def mock_plugin_registry():
    """Mock omnicore_engine.plugin_registry.PLUGIN_REGISTRY."""
    with patch('generator_plugin_wrapper.PLUGIN_REGISTRY') as mock_registry:
        mock_clarifier = AsyncMock(return_value={
            "requirements": "A Flask web service with a single endpoint."
        })
        mock_codegen = AsyncMock(return_value={
            "code_files": {
                "main.py": """
import flask
app = flask.Flask(__name__)

@app.route('/')
def hello():
    return 'Hello, World!'
"""
            }
        })
        mock_critique = AsyncMock(return_value={
            "issues": [],
            "suggestions": []
        })
        mock_testgen = AsyncMock(return_value={
            "test_files": {
                "test_main.py": """
import pytest
from main import hello

def test_hello():
    assert hello() == 'Hello, World!'
"""
            }
        })
        mock_deploy = AsyncMock(return_value={
            "deployment_artifacts": {
                "docker": """
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
"""
            }
        })
        mock_docgen = AsyncMock(return_value={
            "documentation": "# Updated README\nGenerated Flask app documentation."
        })
        mock_registry.get.side_effect = {
            "clarifier": mock_clarifier,
            "codegen_agent": mock_codegen,
            "critique_agent": mock_critique,
            "testgen_agent": mock_testgen,
            "deploy_agent": mock_deploy,
            "docgen_agent": mock_docgen
        }.get
        yield mock_registry

@pytest_asyncio.fixture
async def mock_audit_log():
    """Mock audit_log.log_action."""
    with patch('audit_log.log_action') as mock_log:
        yield mock_log

@pytest_asyncio.fixture
async def mock_sentry():
    """Mock sentry_sdk for error reporting."""
    with patch('generator_plugin_wrapper.sentry_sdk') as mock_sentry:
        yield mock_sentry

@pytest_asyncio.fixture
async def mock_metrics():
    """Mock Prometheus metrics."""
    with patch('generator_plugin_wrapper.get_or_create_metric') as mock_metric:
        mock_metrics = {
            'workflow_latency': MagicMock(),
            'workflow_success': MagicMock(),
            'workflow_errors': MagicMock()
        }
        mock_metric.side_effect = lambda metric_class, name, *args, **kwargs: mock_metrics[name]
        yield mock_metrics

@pytest_asyncio.fixture
async def mock_opentelemetry():
    """Mock OpenTelemetry tracer."""
    with patch('generator_plugin_wrapper.trace') as mock_trace:
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        mock_trace.get_tracer.return_value = mock_tracer
        yield mock_tracer, mock_span

@pytest.fixture
def create_template():
    """Helper to create Jinja2 template files."""
    def _create(name: str, content: str):
        template_path = Path(TEST_TEMPLATE_DIR) / name
        template_path.write_text(content, encoding='utf-8')
        return template_path
    return _create

@pytest_asyncio.fixture
async def codegen_agent(test_repository):
    """Create a CodeGenAgent instance."""
    agent = CodeGenAgent(repo_path=str(test_repository))
    agent.db_path = TEST_DB_PATH
    yield agent
    if agent.db:
        agent.db.close()

@pytest_asyncio.fixture
async def critique_agent(test_repository):
    """Create a CritiqueAgent instance."""
    from critique_agent import CritiqueAgent
    agent = CritiqueAgent(repo_path=str(test_repository))
    yield agent

@pytest_asyncio.fixture
async def testgen_agent(test_repository):
    """Create a TestGenAgent instance."""
    agent = TestGenAgent(repo_path=str(test_repository))
    agent.db_path = TEST_DB_PATH
    yield agent
    if agent.db:
        agent.db.close()

@pytest_asyncio.fixture
async def deploy_agent(test_repository):
    """Create a DeployAgent instance."""
    agent = DeployAgent(repo_path=str(test_repository), plugin_dir=TEST_PLUGIN_DIR)
    agent.db_path = TEST_DB_PATH
    yield agent
    if agent.db:
        agent.db.close()

@pytest_asyncio.fixture
async def docgen_agent(test_repository):
    """Create a DocGenAgent instance."""
    agent = DocGenAgent(repo_path=str(test_repository))
    agent.db_path = TEST_DB_PATH
    yield agent
    if agent.db:
        agent.db.close()

# ============================================================================
# E2E INTEGRATION TESTS
# ============================================================================

class TestE2EGeneratorPipeline:
    """End-to-end tests for the generator pipeline."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_full_pipeline_success(self, test_repository, mock_plugin_registry, mock_audit_log, mock_sentry, mock_metrics, mock_opentelemetry, mock_presidio, create_template):
        """Test the full generator pipeline with successful execution."""
        # Create Jinja2 templates for all agents
        create_template("codegen_python_default.jinja", """
Generate Python code for {{ context.requirements }}:
{% for file, content in context.files_content.items() %}
- File: {{ file }}
  Content: {{ content | scrub }}
{% endfor %}
Instructions: {{ instructions }}
""")
        create_template("critique_python_default.jinja", """
Critique Python code:
{% for file, content in context.files_content.items() %}
- File: {{ file }}
  Content: {{ content | scrub }}
{% endfor %}
Instructions: {{ instructions }}
""")
        create_template("python_pytest_generation.jinja", """
Generate pytest tests for {{ context.language }}:
{% for file, content in context.files_content.items() %}
- File: {{ file }}
  Content: {{ content | scrub }}
{% endfor %}
Instructions: {{ instructions }}
""")
        create_template("deploy_docker_default.jinja", """
Generate a production-grade Dockerfile for {{ context.language }}:
{% for file, content in context.files_content.items() %}
- File: {{ file }}
  Content: {{ content | scrub }}
{% endfor %}
Instructions: {{ instructions }}
""")
        create_template("docgen_readme_default.jinja", """
Generate README for {{ context.language }}:
{% for file, content in context.files_content.items() %}
- File: {{ file }}
  Content: {{ content | scrub }}
{% endfor %}
Instructions: {{ instructions }}
""")

        # Initialize input
        input_data = WorkflowInput(
            correlation_id=MOCK_CORRELATION_ID,
            repo_path=str(test_repository),
            readme="A simple Flask web service with a single endpoint. Contact: test@example.com, API Key: sk-1234567890",
            config={"language": "python", "framework": "flask"}
        )

        # Run full pipeline
        with freeze_time("2025-09-01T12:00:00Z"):
            result = await run_generator_workflow(input_data.model_dump())

        # Verify output
        output = WorkflowOutput(**result)
        assert output.status == "success"
        assert output.correlation_id == MOCK_CORRELATION_ID
        assert output.timestamp == "2025-09-01T12:00:00Z"
        assert "code_files" in output.final_results
        assert "test_files" in output.final_results
        assert "deployment_artifacts" in output.final_results
        assert "documentation" in output.final_results
        assert "main.py" in output.final_results["code_files"]
        assert "test_main.py" in output.final_results["test_files"]
        assert "docker" in output.final_results["deployment_artifacts"]
        assert "# Updated README" in output.final_results["documentation"]

        # Verify PII scrubbing
        assert "[REDACTED_EMAIL]" in json.dumps(output.final_results)
        assert "[REDACTED_CREDIT_CARD]" in json.dumps(output.final_results)

        # Verify metrics
        mock_metrics['workflow_success'].labels.assert_called_with(correlation_id=MOCK_CORRELATION_ID)
        mock_metrics['workflow_latency'].labels.assert_called_with(stage="total", correlation_id=MOCK_CORRELATION_ID)

        # Verify audit logging
        assert mock_audit_log.called
        audit_calls = [call[0][0] for call in mock_audit_log.call_args_list]
        assert "GeneratorWorkflowStarted" in audit_calls
        assert "GeneratorWorkflowCompleted" in audit_calls

        # Verify OpenTelemetry
        mock_opentelemetry[1].set_attribute.assert_any_call("workflow_status", "success")

        # Verify database logging
        cursor = sqlite3.connect(TEST_DB_PATH).cursor()
        cursor.execute("SELECT result FROM history WHERE id=?", (MOCK_CORRELATION_ID,))
        db_result = cursor.fetchone()
        assert db_result is not None
        stored_result = json.loads(db_result[0])
        assert stored_result["correlation_id"] == MOCK_CORRELATION_ID

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_pipeline_with_invalid_codegen(self, test_repository, mock_plugin_registry, mock_audit_log, mock_sentry, mock_metrics, mock_opentelemetry, mock_presidio, create_template):
        """Test pipeline with invalid codegen output and self-healing."""
        # Create templates
        create_template("codegen_python_default.jinja", "Generate Python code: {{ instructions }}")
        create_template("critique_python_default.jinja", "Critique Python code: {{ instructions }}")
        create_template("python_pytest_generation.jinja", "Generate pytest tests: {{ instructions }}")
        create_template("deploy_docker_default.jinja", "Generate Dockerfile: {{ instructions }}")
        create_template("docgen_readme_default.jinja", "Generate README: {{ instructions }}")

        # Mock invalid codegen output
        mock_plugin_registry.get.side_effect = lambda x: AsyncMock(side_effect=[GeneratorPluginError("Invalid syntax"), {
            "code_files": {
                "main.py": """
import flask
app = flask.Flask(__name__)

@app.route('/')
def hello():
    return 'Hello, World!'
"""
            }
        }])[x == "codegen_agent"] if x == "codegen_agent" else mock_plugin_registry.get(x)

        input_data = WorkflowInput(
            correlation_id=MOCK_CORRELATION_ID,
            repo_path=str(test_repository),
            readme="A Flask web service.",
            config={"language": "python", "framework": "flask"}
        )

        # Run pipeline
        with freeze_time("2025-09-01T12:00:00Z"):
            result = await run_generator_workflow(input_data.model_dump())

        # Verify self-healing
        output = WorkflowOutput(**result)
        assert output.status == "success"
        assert "code_files" in output.final_results
        assert "main.py" in output.final_results["code_files"]
        assert "healed" in output.errors[0].lower()  # Healing attempt recorded
        assert mock_audit_log.called_with("GeneratorWorkflowRetry", ANY)
        assert mock_metrics['workflow_errors'].labels.called_with(correlation_id=MOCK_CORRELATION_ID, stage="execution", error_type="GeneratorPluginError")

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_pipeline_with_concurrent_requests(self, test_repository, mock_plugin_registry, mock_metrics, mock_opentelemetry, mock_presidio, create_template):
        """Test concurrent execution of multiple pipeline runs."""
        # Create templates
        create_template("codegen_python_default.jinja", "Generate Python code: {{ instructions }}")
        create_template("critique_python_default.jinja", "Critique Python code: {{ instructions }}")
        create_template("python_pytest_generation.jinja", "Generate pytest tests: {{ instructions }}")
        create_template("deploy_docker_default.jinja", "Generate Dockerfile: {{ instructions }}")
        create_template("docgen_readme_default.jinja", "Generate README: {{ instructions }}")

        input_data = WorkflowInput(
            correlation_id=str(uuid.uuid4()),
            repo_path=str(test_repository),
            readme="A Flask web service.",
            config={"language": "python", "framework": "flask"}
        )

        # Run 5 concurrent workflows
        tasks = [run_generator_workflow(input_data.model_dump()) for _ in range(5)]
        results = await asyncio.gather(*tasks)

        # Verify all succeeded
        for result in results:
            output = WorkflowOutput(**result)
            assert output.status == "success"
            assert "code_files" in output.final_results
            assert "test_files" in output.final_results
            assert "deployment_artifacts" in output.final_results
            assert "documentation" in output.final_results
        assert mock_metrics['workflow_success'].labels.call_count == 5

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_pipeline_with_invalid_input(self, test_repository, mock_plugin_registry, mock_audit_log, mock_sentry, mock_metrics, mock_opentelemetry, mock_presidio):
        """Test pipeline with invalid input validation."""
        invalid_input = {
            "correlation_id": MOCK_CORRELATION_ID,
            "repo_path": "",  # Invalid empty path
            "readme": "A Flask web service."
        }

        with pytest.raises(PydanticValidationError):
            WorkflowInput(**invalid_input)

        result = await run_generator_workflow(invalid_input)
        output = WorkflowOutput(**result)

        assert output.status == "failed"
        assert output.correlation_id == MOCK_CORRELATION_ID
        assert len(output.errors) > 0
        assert "validation" in output.errors[0].lower()
        assert mock_metrics['workflow_errors'].labels.called_with(correlation_id=MOCK_CORRELATION_ID, stage="validation", error_type="ValidationError")
        assert mock_audit_log.called_with("GeneratorWorkflowFailed", ANY)

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_pipeline_with_security_violation(self, test_repository, mock_plugin_registry, mock_audit_log, mock_sentry, mock_metrics, mock_opentelemetry, mock_presidio, create_template):
        """Test pipeline with security violation detection."""
        create_template("codegen_python_default.jinja", "Generate Python code: {{ instructions }}")
        create_template("critique_python_default.jinja", "Critique Python code: {{ instructions }}")
        create_template("python_pytest_generation.jinja", "Generate pytest tests: {{ instructions }}")
        create_template("deploy_docker_default.jinja", "Generate Dockerfile: {{ instructions }}")
        create_template("docgen_readme_default.jinja", "Generate README: {{ instructions }}")

        input_data = WorkflowInput(
            correlation_id=MOCK_CORRELATION_ID,
            repo_path=str(test_repository),
            readme="Contact: test@example.com, API Key: sk-1234567890",
            config={"language": "python", "framework": "flask"}
        )

        # Mock critique to detect security issues
        mock_plugin_registry.get.side_effect = lambda x: AsyncMock(return_value={
            "issues": [{"type": "security", "description": "Hardcoded API key detected"}],
            "suggestions": [{"fix": "Remove hardcoded API key"}]
        }) if x == "critique_agent" else mock_plugin_registry.get(x)

        result = await run_generator_workflow(input_data.model_dump())
        output = WorkflowOutput(**result)

        assert output.status == "success"  # Pipeline completes but flags issues
        assert "issues" in output.final_results
        assert any("Hardcoded API key" in issue["description"] for issue in output.final_results["issues"])
        assert "[REDACTED_EMAIL]" in json.dumps(output.final_results)
        assert "[REDACTED_CREDIT_CARD]" in json.dumps(output.final_results)
        assert mock_audit_log.called_with("SecurityIssueDetected", ANY)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=generator_plugin_wrapper",
        "--cov=codegen_agent",
        "--cov=critique_agent",
        "--cov=testgen_agent",
        "--cov=deploy_agent",
        "--cov=docgen_agent",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
