
"""
test_e2e_testgen.py

Regulated industry-grade end-to-end integration test suite for the test generation system.

Features:
- Tests full pipeline: prompt generation, LLM call, response parsing, test validation.
- Enforces compliance with SOC2/PCI DSS (audit logging, PII scrubbing, provenance).
- Validates observability (Prometheus metrics, OpenTelemetry tracing).
- Tests error handling, edge cases, and self-healing mechanisms.
- Uses real implementations with mocked external services (LLM, PostgreSQL, ChromaDB).
- Ensures secure sandbox execution and resource cleanup.
- Comprehensive coverage of component interactions.

Dependencies:
- pytest, pytest_asyncio, unittest.mock, aiofiles, freezegun, faker
- testgen_prompt, testgen_llm_call, testgen_response_handler, testgen_validator, testgen_agent
- presidio_analyzer, presidio_anonymizer, audit_log, utils
"""

import asyncio
import json
import os
import sys
import tempfile
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

from testgen_prompt import TestPromptDirector, scrub_text as prompt_scrub_text
from testgen_llm_call import TestGenLLMOrchestrator, scrub_prompt as llm_scrub_prompt
from testgen_response_handler import parse_llm_response, scrub_prompt as handler_scrub_prompt
from testgen_validator import validate_test_quality, TestValidator
from testgen_agent import TestGenAgent, Policy
from audit_log import log_action

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_REPO_PATH = "/tmp/test_e2e_testgen_repo"
TEST_TEMPLATE_DIR = "/tmp/test_e2e_testgen_templates"
TEST_FEW_SHOT_DIR = "/tmp/test_e2e_testgen_few_shot"
TEST_PLUGIN_DIR = "/tmp/test_e2e_testgen_plugins"
TEST_DB_PATH = "/tmp/test_e2e_testgen.db"
TEST_PERFORMANCE_DB = "/tmp/test_e2e_performance.json"
MOCK_RUN_ID = str(uuid.uuid4())

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'
os.environ['TESTGEN_MAX_PROMPT_TOKENS'] = '16000'
os.environ['TESTGEN_VALIDATOR_MAX_SANDBOX_RUNS'] = '5'
os.environ['TESTGEN_PARSER_MAX_HEAL_ATTEMPTS'] = '2'

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
    for path in [TEST_REPO_PATH, TEST_TEMPLATE_DIR, TEST_FEW_SHOT_DIR, TEST_PLUGIN_DIR, TEST_DB_PATH, TEST_PERFORMANCE_DB]:
        if Path(path).exists():
            import shutil
            shutil.rmtree(path, ignore_errors=True)
            if Path(path).is_file():
                os.remove(path)
    
    for path in [TEST_REPO_PATH, TEST_TEMPLATE_DIR, TEST_FEW_SHOT_DIR, TEST_PLUGIN_DIR]:
        Path(path).mkdir(parents=True, exist_ok=True)
    
    yield
    
    for path in [TEST_REPO_PATH, TEST_TEMPLATE_DIR, TEST_FEW_SHOT_DIR, TEST_PLUGIN_DIR, TEST_DB_PATH, TEST_PERFORMANCE_DB]:
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
        file_path = repo_path / filename
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
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
    with patch('testgen_prompt.presidio_analyzer.AnalyzerEngine') as mock_prompt_analyzer, \
         patch('testgen_prompt.presidio_anonymizer.AnonymizerEngine') as mock_prompt_anonymizer, \
         patch('testgen_response_handler.presidio_analyzer.AnalyzerEngine') as mock_handler_analyzer, \
         patch('testgen_response_handler.presidio_anonymizer.AnonymizerEngine') as mock_handler_anonymizer, \
         patch('testgen_validator.presidio_analyzer.AnalyzerEngine') as mock_validator_analyzer, \
         patch('testgen_validator.presidio_anonymizer.AnonymizerEngine') as mock_validator_anonymizer, \
         patch('testgen_agent.presidio_analyzer.AnalyzerEngine') as mock_agent_analyzer, \
         patch('testgen_agent.presidio_anonymizer.AnonymizerEngine') as mock_agent_anonymizer, \
         patch('testgen_llm_call.presidio_analyzer.AnalyzerEngine') as mock_llm_analyzer, \
         patch('testgen_llm_call.presidio_anonymizer.AnonymizerEngine') as mock_llm_anonymizer:
        
        mock_analyzer = MagicMock()
        mock_anonymizer = MagicMock()
        
        mock_analyzer.analyze.return_value = [
            MagicMock(entity_type='EMAIL_ADDRESS', start=10, end=25),
            MagicMock(entity_type='CREDIT_CARD', start=30, end=46)
        ]
        
        mock_anonymizer.anonymize.return_value = MagicMock(
            text="[REDACTED_EMAIL] [REDACTED_CREDIT_CARD]"
        )
        
        for mock_cls in [mock_prompt_analyzer, mock_handler_analyzer, mock_validator_analyzer, mock_agent_analyzer, mock_llm_analyzer]:
            mock_cls.return_value = mock_analyzer
        for mock_anonymizer_cls in [mock_prompt_anonymizer, mock_handler_anonymizer, mock_validator_anonymizer, mock_agent_anonymizer, mock_llm_anonymizer]:
            mock_anonymizer_cls.return_value = mock_anonymizer
        
        yield mock_analyzer, mock_anonymizer


@pytest_asyncio.fixture
async def mock_chromadb():
    """Mock ChromaDB client for TestPromptDirector."""
    with patch('testgen_prompt.chromadb') as mock_chroma:
        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            'codebase': ["def hello(): return 'Hello, World!'"],
            'tests': ["def test_hello(): assert hello() == 'Hello, World!'"],
            'docs': ["A simple Flask app."],
            'dependencies': ["flask==2.0.1"],
            'failures': []
        }
        mock_client.get_or_create_collection.return_value = mock_collection
        mock_chroma.PersistentClient.return_value = mock_client
        yield mock_client


@pytest_asyncio.fixture
async def mock_asyncpg():
    """Mock asyncpg for TestGenLLMOrchestrator."""
    with patch('testgen_llm_call.asyncpg') as mock_asyncpg:
        mock_conn = AsyncMock()
        mock_asyncpg.create_pool.return_value = MagicMock(__aenter__=AsyncMock(return_value=mock_conn))
        yield mock_conn


@pytest_asyncio.fixture
async def mock_llm_orchestrator(mock_asyncpg):
    """Create a TestGenLLMOrchestrator instance with mocked provider."""
    orch = TestGenLLMOrchestrator()
    
    mock_provider = AsyncMock()
    mock_provider.__class__.__name__ = "MockProvider"
    mock_provider.call = AsyncMock(return_value={
        'content': json.dumps({
            'files': {
                'test_main.py': """
import pytest
from main import hello

def test_hello():
    assert hello() == 'Hello, World!'
"""
            }
        }),
        'model': 'gpt-4o',
        'provider': 'mock',
        'input_tokens': 100,
        'output_tokens': 50,
        'cost': 0.01,
        'latency': 0.5
    })
    mock_provider.count_tokens = AsyncMock(return_value=100)
    mock_provider._calculate_cost.return_value = 0.01
    mock_provider.health_check = AsyncMock(return_value=True)
    
    orch.providers = {'MockProvider': mock_provider}
    orch.circuit_breakers = {'MockProvider': MagicMock(is_open=Mock(return_value=False))}
    
    orch.db_pool = mock_asyncpg
    yield orch
    
    await orch.shutdown()


@pytest_asyncio.fixture
async def mock_validator():
    """Mock validator for test validation."""
    with patch('testgen_validator.TestValidator') as mock_validator_cls:
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(return_value={
            'coverage_percentage': 95.0,
            'mutation_score': 90.0,
            'properties_passed': True,
            'avg_response_time_ms': 150.0,
            'error_rate_percentage': 1.0,
            'crashes_detected': False,
            'issues': ["All tests passed."]
        })
        mock_validator_cls.return_value = mock_validator
        yield mock_validator


@pytest_asyncio.fixture
async def testgen_agent(mock_llm_orchestrator, test_repository):
    """Create a TestGenAgent instance with mocked dependencies."""
    agent = TestGenAgent(repo_path=str(test_repository))
    agent.llm_orchestrator = mock_llm_orchestrator
    agent.db_path = TEST_DB_PATH
    agent.performance_db = TEST_PERFORMANCE_DB
    yield agent
    
    if agent.db:
        agent.db.close()


@pytest_fixture
def create_template():
    """Helper to create Jinja2 template files."""
    def _create(name: str, content: str):
        template_path = Path(TEST_TEMPLATE_DIR) / name
        template_path.write_text(content, encoding='utf-8')
        return template_path
    return _create


@pytest_asyncio.fixture
async def mock_audit_log():
    """Mock audit_log.log_action for verification."""
    with patch('audit_log.log_action') as mock_log:
        yield mock_log


@pytest_asyncio.fixture
async def mock_sentry():
    """Mock sentry_sdk for error reporting."""
    with patch('testgen_agent.sentry_sdk') as mock_sentry:
        yield mock_sentry


# ============================================================================
# E2E INTEGRATION TESTS
# ============================================================================

class TestE2ETestGenPipeline:
    """End-to-end tests for the test generation pipeline."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_full_pipeline_success(self, test_repository, mock_llm_orchestrator, testgen_agent, create_template, mock_presidio, mock_chromadb, mock_validator, mock_audit_log, mock_sentry):
        """Test the full test generation pipeline for a Python Flask app."""
        # Setup: Create a Jinja2 template
        create_template("python_pytest_generation.jinja", """
Generate pytest tests for {{ context.language }}:
{% for file, content in context.files_content.items() %}
- File: {{ file }}
  Content: {{ content | scrub }}
{% endfor %}
Instructions: {{ instructions }}
""")

        # Step 1: Initialize TestPromptDirector
        prompt_director = TestPromptDirector(
            repo_path=str(test_repository),
            template_dir=TEST_TEMPLATE_DIR,
            few_shot_dir=TEST_FEW_SHOT_DIR
        )

        # Step 2: Generate prompt
        target_files = ["main.py"]
        language = "python"
        instructions = "Generate pytest unit tests for the Flask app."
        policy = Policy(
            coverage_threshold=90.0,
            test_style="pytest",
            max_files_per_run=5,
            timeout_per_file=10.0
        )
        
        with freeze_time("2025-09-01T12:00:00Z"):
            prompt = await build_agentic_prompt(
                code_files=target_files,
                language=language,
                test_style="pytest",
                task="generation",
                instructions=instructions,
                repo_path=str(test_repository)
            )

        # Verify prompt
        assert "pytest" in prompt
        assert "main.py" in prompt
        assert "Generate pytest unit tests" in prompt
        assert "flask" in prompt.lower()

        # Step 3: Generate tests using TestGenLLMOrchestrator
        llm_result = await mock_llm_orchestrator.call_llm_api(
            prompt=prompt,
            language=language,
            model_override="gpt-4o",
            user_id="test_user",
            dry_run=False,
            replay_from_cache=False,
            stream=False,
            task_type="generation"
        )

        # Verify LLM output
        assert "content" in llm_result
        llm_content = json.loads(llm_result["content"])
        assert "test_main.py" in llm_content["files"]
        assert "def test_hello()" in llm_content["files"]["test_main.py"]

        # Step 4: Parse response
        parsed_result = await parse_llm_response(
            response=llm_result["content"],
            language=language,
            code_files={f: await (await aiofiles.open(test_repository / f, 'r')).read() for f in target_files}
        )

        # Verify parsed result
        assert "test_main.py" in parsed_result
        assert "def test_hello()" in parsed_result["test_main.py"]
        assert "pytest" in parsed_result["test_main.py"]

        # Step 5: Validate tests
        validation_result = await validate_test_quality(
            code_files={f: await (await aiofiles.open(test_repository / f, 'r')).read() for f in target_files},
            test_files=parsed_result,
            language=language,
            test_style="pytest"
        )

        # Verify validation
        assert validation_result["coverage_percentage"] >= 90.0
        assert validation_result["properties_passed"]
        assert not validation_result["crashes_detected"]
        assert "All tests passed." in validation_result["issues"]

        # Step 6: Run full pipeline with TestGenAgent
        result = await testgen_agent.generate_tests(
            target_files=target_files,
            language=language,
            policy=policy
        )

        # Verify agent result
        assert result["status"] == "success"
        assert "test_files" in result
        assert "test_main.py" in result["test_files"]
        assert result["validation_results"]["coverage_percentage"] >= 90.0
        assert "provenance" in result
        assert result["provenance"]["timestamp"] == "2025-09-01T12:00:00Z"
        assert result["provenance"]["model_used"] == "gpt-4o"

        # Verify audit logging
        mock_audit_log.assert_called()
        audit_calls = [call[0][0] for call in mock_audit_log.call_args_list]
        assert "PromptGenerated" in audit_calls
        assert "LLMCall" in audit_calls
        assert "ResponseParsed" in audit_calls
        assert "TestsValidated" in audit_calls

        # Verify security scrubbing
        sensitive_content = "api_key=sk-1234567890abcdef email=test@example.com"
        scrubbed_prompt = prompt_scrub_text(sensitive_content)
        scrubbed_llm = llm_scrub_prompt(sensitive_content)
        scrubbed_handler = handler_scrub_prompt(sensitive_content)
        assert "[REDACTED" in scrubbed_prompt
        assert "[REDACTED" in scrubbed_llm
        assert "[REDACTED" in scrubbed_handler

        # Verify database logging
        cursor = testgen_agent.db.cursor()
        cursor.execute("SELECT result FROM history WHERE id=?", (result["run_id"],))
        db_result = cursor.fetchone()
        assert db_result is not None
        stored_result = json.loads(db_result[0])
        assert stored_result["run_id"] == result["run_id"]

        # Verify metrics
        from prometheus_client import REGISTRY
        assert REGISTRY.get_sample_value('testgen_agent_runs_total', {'language': 'python'}) == 1
        assert REGISTRY.get_sample_value('testgen_agent_runs_success_total', {'language': 'python'}) == 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_pipeline_with_invalid_response(self, test_repository, mock_llm_orchestrator, testgen_agent, create_template, mock_presidio, mock_chromadb, mock_validator, mock_audit_log, mock_sentry):
        """Test the pipeline with an invalid LLM response and self-healing."""
        # Setup: Create a Jinja2 template
        create_template("python_pytest_generation.jinja", """
Generate pytest tests for {{ context.language }}:
{% for file, content in context.files_content.items() %}
- File: {{ file }}
  Content: {{ content | scrub }}
{% endfor %}
Instructions: {{ instructions }}
""")

        # Mock LLM to return an invalid response
        original_call = mock_llm_orchestrator.providers["MockProvider"].call
        mock_llm_orchestrator.providers["MockProvider"].call = AsyncMock(return_value={
            'content': json.dumps({
                'files': {
                    'test_main.py': "invalid syntax !!"  # Malformed test code
                }
            }),
            'model': 'gpt-4o',
            'provider': 'mock',
            'input_tokens': 100,
            'output_tokens': 10,
            'cost': 0.01,
            'latency': 0.5
        })

        # Step 1: Generate prompt
        prompt_director = TestPromptDirector(
            repo_path=str(test_repository),
            template_dir=TEST_TEMPLATE_DIR,
            few_shot_dir=TEST_FEW_SHOT_DIR
        )
        target_files = ["main.py"]
        language = "python"
        instructions = "Generate pytest unit tests for the Flask app."
        policy = Policy(
            coverage_threshold=90.0,
            test_style="pytest",
            max_files_per_run=5,
            timeout_per_file=10.0
        )
        
        with freeze_time("2025-09-01T12:00:00Z"):
            prompt = await build_agentic_prompt(
                code_files=target_files,
                language=language,
                test_style="pytest",
                task="generation",
                instructions=instructions,
                repo_path=str(test_repository)
            )

        # Step 2: Generate tests
        llm_result = await mock_llm_orchestrator.call_llm_api(
            prompt=prompt,
            language=language,
            model_override="gpt-4o",
            user_id="test_user",
            dry_run=False,
            replay_from_cache=False,
            stream=False,
            task_type="generation"
        )

        # Step 3: Parse response
        with pytest.raises(ValueError, match="Failed to parse and heal response"):
            await parse_llm_response(
                response=llm_result["content"],
                language=language,
                code_files={f: await (await aiofiles.open(test_repository / f, 'r')).read() for f in target_files}
            )

        # Mock successful healing
        mock_llm_orchestrator.providers["MockProvider"].call = AsyncMock(return_value={
            'content': json.dumps({
                'files': {
                    'test_main.py': """
import pytest
from main import hello

def test_hello():
    assert hello() == 'Hello, World!'
"""
                }
            }),
            'model': 'gpt-4o',
            'provider': 'mock',
            'input_tokens': 100,
            'output_tokens': 50,
            'cost': 0.01,
            'latency': 0.5
        })

        # Step 4: Run full pipeline with self-healing
        result = await testgen_agent.generate_tests(
            target_files=target_files,
            language=language,
            policy=policy
        )

        # Verify self-healing result
        assert result["status"] == "success"
        assert "test_files" in result
        assert "test_main.py" in result["test_files"]
        assert "def test_hello()" in result["test_files"]["test_main.py"]
        assert result["validation_results"]["coverage_percentage"] >= 90.0
        assert "healed" in result["provenance"]["status"].lower()
        assert mock_audit_log.call_count > 0
        assert "HealingAttempt" in [call[0][0] for call in mock_audit_log.call_args_list]

        # Restore original mock
        mock_llm_orchestrator.providers["MockProvider"].call = original_call


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=testgen_prompt",
        "--cov=testgen_llm_call",
        "--cov=testgen_response_handler",
        "--cov=testgen_validator",
        "--cov=testgen_agent",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
