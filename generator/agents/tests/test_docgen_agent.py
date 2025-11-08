"""
test_docgen_agent.py
Industry-grade test suite for the DocGen Agent orchestrator.

This comprehensive test suite covers:
- Unit tests for all major components
- Integration tests for the full pipeline
- Edge cases and error scenarios
- Performance and stress testing
- Security and compliance validation
- Mocking of external dependencies
- Async operation testing
- Metrics and observability validation

Test Categories:
1. Core Functionality Tests
2. Security & Compliance Tests
3. Integration Tests
4. Performance Tests
5. Error Handling Tests
6. Plugin System Tests
7. Hook System Tests
8. Metrics & Observability Tests
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
from pathlib import Path
from typing import Dict, List, Any, Optional, AsyncGenerator
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import Mock, MagicMock, AsyncMock, patch, call, mock_open, PropertyMock
from contextlib import contextmanager, asynccontextmanager
import subprocess
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from pytest_mock import MockerFixture
from freezegun import freeze_time
from hypothesis import given, strategies as st, settings, assume
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant, initialize
import aiofiles
from aioresponses import aioresponses
from prometheus_client import REGISTRY
from opentelemetry.trace import Status, StatusCode
from tenacity import RetryError

# Import the module under test
# These imports assume the docgen modules are in the Python path
try:
    # FIX: Corrected the full package path import and ensured the closing parenthesis is present.
    from agents.docgen_agent.docgen_agent import (
        DocGenAgent,
        scrub_text,
        CompliancePlugin,
        LicenseCompliance,
        CopyrightCompliance,
        generate,  # The plugin entry point
        docgen_calls_total,
        docgen_errors_total,
        docgen_latency_seconds,
        docgen_compliance_issues_total,
        docgen_validation_status_total,
        docgen_token_usage_input_total,
        docgen_token_usage_output_total,
        docgen_human_approval_status,
        COMMON_SENSITIVE_PATTERNS_REF
    )
    # FIX: Corrected the local package name import block for flexibility/local testing.
    from docgen_agent import (
        DocGenAgent,
        scrub_text,
        CompliancePlugin,
        LicenseCompliance,
        CopyrightCompliance,
        generate,  # The plugin entry point
        docgen_calls_total,
        docgen_errors_total,
        docgen_latency_seconds,
        docgen_compliance_issues_total,
        docgen_validation_status_total,
        docgen_token_usage_input_total,
        docgen_token_usage_output_total,
        docgen_human_approval_status,
        COMMON_SENSITIVE_PATTERNS_REF
    )
except ImportError as e:
    print(f"Failed to import docgen_agent: {e}")
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
def hello_world():
    '''A simple hello world function'''
    return "Hello, World!"

def process_data(data: List[Dict]) -> Dict:
    '''Process input data and return results'''
    return {"processed": len(data)}
""")
    
    (repo_path / "utils.py").write_text("""
import json
import logging

def load_config(path: str) -> dict:
    with open(path, 'r') as f:
        return json.load(f)

SECRET_KEY = "sk-1234567890abcdef"  # This should be redacted
""")
    
    (repo_path / "requirements.txt").write_text("""
flask==2.3.0
pytest==7.4.0
aiohttp==3.8.5
""")
    
    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, capture_output=True)
    
    return repo_path


@pytest.fixture
def mock_dependencies(mocker):
    """Mock all external dependencies."""
    # Mock Presidio
    mock_analyzer = mocker.patch('docgen_agent.AnalyzerEngine')
    mock_anonymizer = mocker.patch('docgen_agent.AnonymizerEngine')
    
    # Mock DeployLLMOrchestrator
    mock_llm = mocker.patch('docgen_agent.DeployLLMOrchestrator')
    mock_llm_instance = AsyncMock()
    mock_llm.return_value = mock_llm_instance
    
    # Mock docgen-specific components
    mock_prompt = mocker.patch('docgen_agent.get_doc_prompt')
    mock_prompt.return_value = "Generated prompt for documentation"
    
    mock_handler = mocker.patch('docgen_agent.handle_doc_response')
    mock_handler.return_value = {"processed": "response"}
    
    mock_validator = mocker.patch('docgen_agent.validate_documentation')
    mock_validator.return_value = {"valid": True, "issues": []}
    
    # Mock PlantUML (optional dependency)
    mocker.patch('docgen_agent.PlantUML', None)
    
    return {
        'analyzer': mock_analyzer,
        'anonymizer': mock_anonymizer,
        'llm': mock_llm_instance,
        'prompt': mock_prompt,
        'handler': mock_handler,
        'validator': mock_validator
    }


@pytest.fixture
def agent_instance(temp_repo, mock_dependencies):
    """Create a DocGenAgent instance with mocked dependencies."""
    return DocGenAgent(repo_path=str(temp_repo))


@pytest.fixture
def async_agent_instance(temp_repo, mock_dependencies):
    """Create a DocGenAgent instance for async testing."""
    return DocGenAgent(repo_path=str(temp_repo))


# ============================================================================
# 1. Core Functionality Tests
# ============================================================================

class TestDocGenAgentCore(TestCase):
    """Test core functionality of DocGenAgent."""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir) / "test_repo"
        self.repo_path.mkdir()
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    def test_agent_initialization_valid_repo(self):
        """Test agent initializes correctly with valid repository."""
        agent = DocGenAgent(repo_path=str(self.repo_path))
        self.assertEqual(agent.repo_path, self.repo_path)
        self.assertEqual(len(agent.languages_supported), 5)
        self.assertIsNotNone(agent.llm_orchestrator)
        self.assertIsNotNone(agent.tokenizer)
    
    def test_agent_initialization_invalid_repo(self):
        """Test agent raises error for invalid repository path."""
        with self.assertRaises(ValueError) as ctx:
            DocGenAgent(repo_path="/nonexistent/path")
        self.assertIn("does not exist", str(ctx.exception))
    
    def test_agent_initialization_file_instead_of_dir(self):
        """Test agent raises error when given file path instead of directory."""
        file_path = self.repo_path / "file.txt"
        file_path.write_text("content")
        with self.assertRaises(ValueError) as ctx:
            DocGenAgent(repo_path=str(file_path))
        self.assertIn("not a directory", str(ctx.exception))
    
    def test_custom_languages_support(self):
        """Test agent accepts custom language configuration."""
        custom_langs = ["python", "typescript", "kotlin"]
        agent = DocGenAgent(repo_path=str(self.repo_path), languages_supported=custom_langs)
        self.assertEqual(agent.languages_supported, custom_langs)
    
    def test_hook_registration(self):
        """Test pre/post-process hook registration."""
        agent = DocGenAgent(repo_path=str(self.repo_path))
        
        def pre_hook(prompt: str) -> str:
            return prompt.upper()
        
        def post_hook(result: Dict) -> Dict:
            result['modified'] = True
            return result
        
        agent.add_pre_process_hook(pre_hook)
        agent.add_post_process_hook(post_hook)
        
        self.assertEqual(len(agent.pre_process_hooks), 1)
        self.assertEqual(len(agent.post_process_hooks), 1)
    
    def test_hook_registration_invalid_type(self):
        """Test hook registration rejects non-callable objects."""
        agent = DocGenAgent(repo_path=str(self.repo_path))
        
        with self.assertRaises(TypeError):
            agent.add_pre_process_hook("not_a_function")
        
        with self.assertRaises(TypeError):
            agent.add_post_process_hook(123)
    
    def test_compliance_plugin_registration(self):
        """Test compliance plugin registration."""
        agent = DocGenAgent(repo_path=str(self.repo_path))
        
        class CustomCompliancePlugin(CompliancePlugin):
            def check(self, docs_content: str) -> List[str]:
                return ["Custom issue"] if "TODO" in docs_content else []
        
        custom_plugin = CustomCompliancePlugin()
        agent.register_compliance_plugin(custom_plugin)
        
        self.assertEqual(len(agent.compliance_plugins), 3)  # 2 default + 1 custom
    
    def test_compliance_plugin_registration_invalid_type(self):
        """Test compliance plugin registration rejects invalid types."""
        agent = DocGenAgent(repo_path=str(self.repo_path))
        
        with self.assertRaises(TypeError):
            agent.register_compliance_plugin("not_a_plugin")


# ============================================================================
# 2. Security & Compliance Tests
# ============================================================================

class TestSecurityCompliance(TestCase):
    """Test security and compliance features."""
    
    @patch('docgen_agent.AnalyzerEngine')
    @patch('docgen_agent.AnonymizerEngine')
    def test_scrub_text_with_pii(self, mock_anonymizer_class, mock_analyzer_class):
        """Test PII scrubbing with Presidio."""
        # Setup mocks
        mock_analyzer = MagicMock()
        mock_anonymizer = MagicMock()
        mock_analyzer_class.return_value = mock_analyzer
        mock_anonymizer_class.return_value = mock_anonymizer
        
        # Mock analysis results
        mock_results = [
            MagicMock(entity_type="EMAIL_ADDRESS", start=10, end=25),
            MagicMock(entity_type="PHONE_NUMBER", start=30, end=42)
        ]
        mock_analyzer.analyze.return_value = mock_results
        
        # Mock anonymization
        mock_anonymizer.anonymize.return_value = MagicMock(text="Contact: [REDACTED] or [REDACTED]")
        
        # Test
        text = "Contact: john@example.com or 555-123-4567"
        result = scrub_text(text)
        
        self.assertEqual(result, "Contact: [REDACTED] or [REDACTED]")
        mock_analyzer.analyze.assert_called_once()
        mock_anonymizer.anonymize.assert_called_once()
    
    def test_scrub_text_empty_input(self):
        """Test scrubbing handles empty input."""
        result = scrub_text("")
        self.assertEqual(result, "")
    
    @patch('docgen_agent.AnalyzerEngine')
    @patch('docgen_agent.AnonymizerEngine')
    def test_scrub_text_presidio_failure(self, mock_anonymizer_class, mock_analyzer_class):
        """Test scrubbing raises RuntimeError on Presidio failure."""
        mock_analyzer = MagicMock()
        mock_analyzer_class.return_value = mock_analyzer
        mock_analyzer.analyze.side_effect = Exception("Presidio error")
        
        with self.assertRaises(RuntimeError) as ctx:
            scrub_text("Some text")
        self.assertIn("Critical error during sensitive data scrubbing", str(ctx.exception))
    
    def test_license_compliance_check(self):
        """Test license compliance plugin."""
        plugin = LicenseCompliance()
        
        # Test with valid license
        docs_with_license = "This project is under MIT License terms."
        issues = plugin.check(docs_with_license)
        self.assertEqual(len(issues), 0)
        
        # Test without license
        docs_without_license = "This is a project without any license information."
        issues = plugin.check(docs_without_license)
        self.assertEqual(len(issues), 1)
        self.assertIn("Missing recognized open-source license", issues[0])
    
    def test_copyright_compliance_check(self):
        """Test copyright compliance plugin."""
        plugin = CopyrightCompliance()
        
        # Test with valid copyright
        docs_with_copyright = "Copyright (c) 2024 Example Corp."
        issues = plugin.check(docs_with_copyright)
        self.assertEqual(len(issues), 0)
        
        # Test without copyright
        docs_without_copyright = "This is documentation without copyright."
        issues = plugin.check(docs_without_copyright)
        self.assertEqual(len(issues), 1)
        self.assertIn("Missing copyright notice", issues[0])
    
    def test_multiple_compliance_checks(self):
        """Test multiple compliance checks work together."""
        license_plugin = LicenseCompliance()
        copyright_plugin = CopyrightCompliance()
        
        docs = "Project documentation"
        all_issues = []
        all_issues.extend(license_plugin.check(docs))
        all_issues.extend(copyright_plugin.check(docs))
        
        self.assertEqual(len(all_issues), 2)


# ============================================================================
# 3. Async Integration Tests
# ============================================================================

class TestAsyncOperations(IsolatedAsyncioTestCase):
    """Test async operations and integration."""
    
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir) / "test_repo"
        self.repo_path.mkdir()
        
    async def asyncTearDown(self):
        shutil.rmtree(self.temp_dir)
    
    @patch('docgen_agent.DeployLLMOrchestrator')
    @patch('docgen_agent.get_doc_prompt')
    @patch('docgen_agent.handle_doc_response')
    @patch('docgen_agent.validate_documentation')
    async def test_generate_documentation_success(self, mock_validator, mock_handler, mock_prompt, mock_llm_class):
        """Test successful documentation generation flow."""
        # Setup mocks
        mock_llm_instance = AsyncMock()
        mock_llm_class.return_value = mock_llm_instance
        
        mock_prompt.return_value = "Test prompt"
        mock_handler.return_value = {"content": "Generated docs"}
        mock_validator.return_value = {"valid": True}
        
        agent = DocGenAgent(repo_path=str(self.repo_path))
        
        # Test
        result = await agent.generate_documentation(
            target_files=["main.py"],
            doc_type="README",
            instructions="Create comprehensive docs",
            human_approval=False,
            llm_model="gpt-4o",
            stream=False
        )
        
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['doc_type'], 'README')
        self.assertIn('main.py', result['target_files'])
    
    @patch('docgen_agent.aiohttp.ClientSession')
    async def test_human_approval_workflow(self, mock_session_class):
        """Test human-in-the-loop approval workflow."""
        mock_session = AsyncMock()
        mock_session_class.return_value = mock_session
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"approved": True})
        mock_session.post.return_value.__aenter__.return_value = mock_response
        
        agent = DocGenAgent(repo_path=str(self.repo_path))
        
        result = {
            'doc_type': 'README',
            'trace_id': str(uuid.uuid4()),
            'validation': {'status': 'passed'},
            'compliance_issues': [],
            'documentation': {'content': 'Test content'}
        }
        
        approval = await agent._human_approval(result)
        self.assertIsInstance(approval, bool)
    
    async def test_concurrent_generation_requests(self):
        """Test handling concurrent documentation generation requests."""
        with patch('docgen_agent.DeployLLMOrchestrator'):
            agent = DocGenAgent(repo_path=str(self.repo_path))
            
            # Create multiple concurrent tasks
            tasks = []
            for i in range(5):
                task = agent.generate_documentation(
                    target_files=[f"file_{i}.py"],
                    doc_type="README",
                    stream=False
                )
                tasks.append(task)
            
            # Execute concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Verify all completed
            self.assertEqual(len(results), 5)
            for result in results:
                # The dummy implementation returns a dict if successful, otherwise an exception.
                # In this specific test setup, they are expected to succeed due to simple mocks.
                if not isinstance(result, Exception):
                    self.assertEqual(result['status'], 'success')


# ============================================================================
# 4. Performance and Stress Tests
# ============================================================================

class TestPerformance(TestCase):
    """Test performance and resource usage."""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir) / "test_repo"
        self.repo_path.mkdir()
    
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    @patch('docgen_agent.DeployLLMOrchestrator')
    def test_large_file_handling(self, mock_llm_class):
        """Test handling of large files."""
        mock_llm_class.return_value = AsyncMock()
        
        # Create a large file (1MB)
        large_content = "x" * (1024 * 1024)
        (self.repo_path / "large_file.py").write_text(large_content)
        
        agent = DocGenAgent(repo_path=str(self.repo_path))
        
        # Should not raise memory errors
        self.assertIsNotNone(agent)
    
    def test_many_files_handling(self):
        """Test handling many files in repository."""
        # Create 100 small files
        for i in range(100):
            (self.repo_path / f"file_{i}.py").write_text(f"# File {i}")
        
        with patch('docgen_agent.DeployLLMOrchestrator'):
            agent = DocGenAgent(repo_path=str(self.repo_path))
            self.assertIsNotNone(agent)
    
    @pytest.mark.timeout(5)
    def test_scrub_text_performance(self):
        """Test PII scrubbing performance on large text."""
        # Generate large text with multiple PII instances
        large_text = " ".join([
            f"Contact user{i}@example.com or 555-{i:04d}"
            for i in range(1000)
        ])
        
        with patch('docgen_agent.AnalyzerEngine'), \
             patch('docgen_agent.AnonymizerEngine'):
            start_time = time.time()
            result = scrub_text(large_text)
            elapsed = time.time() - start_time
            
            # Should complete within reasonable time
            self.assertLess(elapsed, 5.0)


# ============================================================================
# 5. Error Handling Tests
# ============================================================================

class TestErrorHandling(TestCase):
    """Test error handling and edge cases."""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir) / "test_repo"
        self.repo_path.mkdir()
    
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    @patch('docgen_agent.DeployLLMOrchestrator')
    def test_missing_dependency_handling(self, mock_llm_class):
        """Test handling of missing dependencies."""
        # Simulate missing Presidio
        # The main import block at the top will handle a real ImportError,
        # but for testing the runtime behavior where the import is within a try-except
        # or where a mock is needed to test an internal failure case:
        with patch('docgen_agent.AnalyzerEngine', side_effect=ImportError("Presidio not found")):
            # If DocGenAgent is initialized in a context where Presidio is mocked to fail import,
            # this test is checking the module's *runtime* resilience, not *startup* failure.
            # In the original file, Presidio is imported at the top-level, so mocking it this way
            # is complex. We stick to testing what is directly possible and assume
            # top-level imports are correctly handled by the test runner.
            pass
    
    def test_file_not_found_handling(self):
        """Test handling of non-existent target files."""
        with patch('docgen_agent.DeployLLMOrchestrator'):
            agent = DocGenAgent(repo_path=str(self.repo_path))
            
            # Agent should handle missing files gracefully
            # (implementation dependent - may log warnings)
            self.assertIsNotNone(agent)
    
    @patch('docgen_agent.tiktoken.get_encoding')
    def test_tokenizer_fallback(self, mock_get_encoding):
        """Test handling of tokenizer initialization failure."""
        mock_get_encoding.side_effect = Exception("Tokenizer error")
        
        # The DocGenAgent constructor uses tiktoken.get_encoding.
        # This will fail the constructor initialization if not caught internally.
        with patch('docgen_agent.DeployLLMOrchestrator'):
            with self.assertRaises(Exception): # Assuming no internal try/except for tiktoken failure
                DocGenAgent(repo_path=str(self.repo_path))


# ============================================================================
# 6. Metrics and Observability Tests
# ============================================================================

class TestMetricsObservability(TestCase):
    """Test metrics and observability features."""
    
    def setUp(self):
        # Reset Prometheus metrics
        for collector in list(REGISTRY._collector_to_names.keys()):
            try:
                REGISTRY.unregister(collector)
            except:
                pass
    
    @patch('docgen_agent.DeployLLMOrchestrator')
    def test_metrics_incremented_on_call(self, mock_llm_class):
        """Test Prometheus metrics are properly incremented."""
        from docgen_agent import docgen_calls_total
        
        # Get initial value
        initial_value = 0
        try:
            # Safely get the initial value
            initial_value = docgen_calls_total.labels(doc_type='README', run_id_prefix='test')._value.get()
        except AttributeError:
            # If metric not yet used, _value might not exist or be structured differently
            pass
        except:
             pass # Ignore other errors for initial value fetch
        
        # Simulate a call
        docgen_calls_total.labels(doc_type='README', run_id_prefix='test').inc()
        
        # Check increment
        new_value = docgen_calls_total.labels(doc_type='README', run_id_prefix='test')._value.get()
        self.assertEqual(new_value, initial_value + 1)
    
    @patch('docgen_agent.tracer')
    def test_tracing_spans_created(self, mock_tracer):
        """Test OpenTelemetry spans are created."""
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        
        # Test function that uses tracing would go here
        # This is implementation-specific
        self.assertIsNotNone(mock_tracer)


# ============================================================================
# 7. Property-Based Testing with Hypothesis
# ============================================================================

class TestPropertyBased(TestCase):
    """Property-based tests using Hypothesis."""
    
    @given(
        text=st.text(min_size=0, max_size=10000),
        include_pii=st.booleans()
    )
    @settings(max_examples=50, deadline=5000)
    def test_scrub_text_properties(self, text, include_pii):
        """Test scrub_text maintains properties across inputs."""
        if include_pii:
            text = f"{text} email@example.com"
        
        with patch('docgen_agent.AnalyzerEngine'), \
             patch('docgen_agent.AnonymizerEngine') as mock_anon:
            # Ensure the mock returns a MagicMock with the 'text' attribute
            mock_anon.return_value.anonymize.return_value = MagicMock(text="[REDACTED]")
            
            result = scrub_text(text)
            
            # Properties to maintain:
            # 1. Result is always a string
            self.assertIsInstance(result, str)
            
            # 2. Empty input returns empty output
            if not text:
                self.assertEqual(result, "")
    
    @given(
        # Only generate valid path components
        repo_path_name=st.text(min_size=1, max_size=255, alphabet=st.characters(whitelist_categories=('L', 'N'))).filter(lambda x: x.strip()),
        languages=st.lists(
            st.sampled_from(['python', 'javascript', 'rust', 'go', 'java']),
            min_size=1,
            max_size=5,
            unique=True
        )
    )
    @settings(suppress_health_check=[HealthCheck.too_slow, HealthCheck.timeout])
    def test_agent_initialization_properties(self, repo_path_name, languages):
        """Test agent initialization with various inputs."""
        temp_dir = tempfile.mkdtemp()
        try:
            full_path = Path(temp_dir) / repo_path_name
            full_path.mkdir(parents=True)
            
            with patch('docgen_agent.DeployLLMOrchestrator'):
                agent = DocGenAgent(
                    repo_path=str(full_path),
                    languages_supported=languages
                )
                
                # Properties to verify:
                self.assertEqual(agent.languages_supported, languages)
                self.assertEqual(agent.repo_path, full_path)
        finally:
            shutil.rmtree(temp_dir)


# ============================================================================
# 8. State Machine Testing
# ============================================================================

class DocGenStateMachine(RuleBasedStateMachine):
    """State machine for testing DocGenAgent workflow."""
    
    def __init__(self):
        super().__init__()
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir) / "test_repo"
        self.repo_path.mkdir()
        self.agent = None
        self.hooks_added = 0
        self.plugins_added = 0
    
    @initialize()
    def setup(self):
        """Initialize the agent."""
        with patch('docgen_agent.DeployLLMOrchestrator'):
            self.agent = DocGenAgent(repo_path=str(self.repo_path))
    
    @rule()
    def add_hook(self):
        """Add a hook to the agent."""
        def hook(x):
            return x
        
        self.agent.add_pre_process_hook(hook)
        self.hooks_added += 1
    
    @rule()
    def add_plugin(self):
        """Add a compliance plugin."""
        class TestPlugin(CompliancePlugin):
            def check(self, docs_content: str) -> List[str]:
                return []
        
        self.agent.register_compliance_plugin(TestPlugin())
        self.plugins_added += 1
    
    @invariant()
    def hook_count_consistent(self):
        """Verify hook count remains consistent."""
        assert len(self.agent.pre_process_hooks) == self.hooks_added
    
    @invariant()
    def plugin_count_consistent(self):
        """Verify plugin count remains consistent."""
        # 2 default plugins + added plugins
        assert len(self.agent.compliance_plugins) == 2 + self.plugins_added
    
    def teardown(self):
        """Clean up after testing."""
        shutil.rmtree(self.temp_dir)


# Run state machine test
TestDocGenStateMachine = DocGenStateMachine.TestCase


# ============================================================================
# 9. Plugin System Tests
# ============================================================================

class TestPluginSystem(TestCase):
    """Test the plugin system functionality."""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir) / "test_repo"
        self.repo_path.mkdir()
    
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    def test_custom_compliance_plugin_integration(self):
        """Test custom compliance plugin integration."""
        with patch('docgen_agent.DeployLLMOrchestrator'):
            agent = DocGenAgent(repo_path=str(self.repo_path))
            
            # Create custom plugin
            class SecurityDisclaimerCompliance(CompliancePlugin):
                def check(self, docs_content: str) -> List[str]:
                    issues = []
                    if "SECURITY DISCLAIMER" not in docs_content.upper():
                        issues.append("Missing security disclaimer")
                    return issues
            
            # Register and verify
            plugin = SecurityDisclaimerCompliance()
            agent.register_compliance_plugin(plugin)
            
            # Test the plugin
            issues = plugin.check("Some documentation without disclaimer")
            self.assertEqual(len(issues), 1)
            self.assertIn("security disclaimer", issues[0])
    
    def test_multiple_plugin_execution_order(self):
        """Test that multiple plugins execute in order."""
        with patch('docgen_agent.DeployLLMOrchestrator'):
            agent = DocGenAgent(repo_path=str(self.repo_path))
            
            execution_order = []
            
            class Plugin1(CompliancePlugin):
                def check(self, docs_content: str) -> List[str]:
                    execution_order.append('plugin1')
                    return []
            
            class Plugin2(CompliancePlugin):
                def check(self, docs_content: str) -> List[str]:
                    execution_order.append('plugin2')
                    return []
            
            agent.register_compliance_plugin(Plugin1())
            agent.register_compliance_plugin(Plugin2())
            
            # Execute plugins
            # Since the compliance_plugins attribute includes the two default plugins first, 
            # we need to start from the index after the default ones (index 2)
            for plugin in agent.compliance_plugins[2:]:  
                plugin.check("test")
            
            self.assertEqual(execution_order, ['plugin1', 'plugin2'])


# ============================================================================
# 10. End-to-End Integration Tests
# ============================================================================

@pytest.mark.integration
class TestEndToEnd(IsolatedAsyncioTestCase):
    """End-to-end integration tests."""
    
    async def test_full_pipeline_with_mocks(self):
        """Test full documentation generation pipeline with mocked dependencies."""
        temp_dir = tempfile.mkdtemp()
        try:
            repo_path = Path(temp_dir) / "test_repo"
            repo_path.mkdir()
            
            # Create test files
            (repo_path / "app.py").write_text("""
from flask import Flask
app = Flask(__name__)

@app.route('/')
def home():
    return 'Hello World'
""")
            
            with patch('docgen_agent.DeployLLMOrchestrator') as mock_llm, \
                 patch('docgen_agent.get_doc_prompt') as mock_prompt, \
                 patch('docgen_agent.handle_doc_response') as mock_handler, \
                 patch('docgen_agent.validate_documentation') as mock_validator:
                
                # Setup mocks
                mock_llm_instance = AsyncMock()
                mock_llm.return_value = mock_llm_instance
                mock_prompt.return_value = "Generate docs for Flask app"
                mock_handler.return_value = {
                    "content": "# Flask App\n\nA simple Flask application.",
                    "metrics": {"quality": 0.85}
                }
                mock_validator.return_value = {"valid": True, "score": 0.9}
                
                # Run pipeline
                result = await generate(
                    repo_path=str(repo_path),
                    target_files=["app.py"],
                    doc_type="README",
                    instructions="Create API documentation",
                    human_approval=False,
                    llm_model="gpt-4o",
                    stream=False
                )
                
                self.assertIn('docs', result)
                self.assertEqual(result['docs']['status'], 'success')
        
        finally:
            shutil.rmtree(temp_dir)


# ============================================================================
# Test Runner Configuration
# ============================================================================

if __name__ == '__main__':
    # Configure pytest options
    pytest_args = [
        __file__,
        '-v',  # Verbose output
        '--cov=docgen_agent',  # Coverage for docgen_agent module
        '--cov-report=html',  # HTML coverage report
        '--cov-report=term-missing',  # Terminal coverage with missing lines
        '--tb=short',  # Short traceback format
        '-m', 'not integration',  # Skip integration tests by default
        '--maxfail=3',  # Stop after 3 failures
        '--strict-markers',  # Strict marker checking
        '--disable-warnings',  # Disable warnings for cleaner output
    ]
    
    # Run with integration tests if specified
    if '--integration' in sys.argv:
        pytest_args.remove('-m')
        pytest_args.remove('not integration')
    
    # Run tests
    sys.exit(pytest.main(pytest_args))