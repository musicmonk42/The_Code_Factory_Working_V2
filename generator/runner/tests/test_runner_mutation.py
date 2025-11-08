
# test_runner_mutation.py
# Industry-grade test suite for runner_mutation.py, ensuring compliance with regulated standards.
# Covers unit and integration tests for mutation and fuzzing functions, with traceability, reproducibility, and security.

import pytest
import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone
import logging
import uuid
import random
from collections import defaultdict, deque

# Import required classes and functions from runner_mutation
from runner.mutation import (
    mutation_test, fuzz_test, property_based_test,
    _run_subprocess_safe, register_mutator,
    MUTATION_TOTAL, MUTATION_KILLED, MUTATION_SURVIVED, MUTATION_TIMEOUT,
    MUTATION_ERROR, MUTATION_SURVIVAL_RATE, FUZZ_DISCOVERIES, COVERAGE_GAPS,
    HAS_MUTMUT, HAS_HYPOTHESIS, _MUTATOR_REGISTRY
)

# Import dependencies from runner module
from runner.config import RunnerConfig
from runner.contracts import TaskPayload
from runner.errors import RunnerError, TestExecutionError, SetupError, ConfigurationError, TimeoutError
from runner.errors import ERROR_CODE_REGISTRY as error_codes
from runner.logging import logger as mutation_logger
from runner.metrics import prom

# Configure logging for traceability and auditability
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s [trace_id=%(trace_id)s]',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Mock OpenTelemetry tracer for testing without external dependencies
class MockSpan:
    def set_attribute(self, key, value): pass
    def set_status(self, status): pass
    def record_exception(self, exception): pass
    def end(self): pass
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): pass

class MockTracer:
    def start_as_current_span(self, name, *args, **kwargs): return MockSpan()

mock_tracer = MockTracer()

# Fixture for temporary directory
@pytest.fixture
def tmp_path(tmp_path_factory):
    """Create a temporary directory for test files."""
    return tmp_path_factory.mktemp("mutation_test")

# Fixture for mock OpenTelemetry tracer
@pytest.fixture(autouse=True)
def mock_opentelemetry():
    """Mock OpenTelemetry tracer for all tests."""
    with patch('runner.mutation.trace', mock_tracer):
        yield

# Fixture for audit log
@pytest.fixture
def audit_log(tmp_path):
    """Set up an audit log file for traceability."""
    log_file = tmp_path / "audit.log"
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s [trace_id=%(trace_id)s]'
    ))
    logger.addHandler(handler)
    yield log_file
    logger.removeHandler(handler)

# Helper function to log test execution for auditability
def log_test_execution(test_name, result, trace_id):
    """Log test execution details for audit trail."""
    logger.debug(
        f"Test {test_name}: {result}",
        extra={'trace_id': trace_id}
    )

# Mock RunnerConfig for tests
class MockRunnerConfig(RunnerConfig):
    def __init__(self, **kwargs):
        super().__init__(
            version=4,
            backend='docker',
            framework='pytest',
            mutation=False,
            fuzz=False,
            instance_id='test_instance',
            **kwargs
        )

# Test class for mutation and fuzzing functions
class TestMutationFunctions:
    """Tests for mutation testing functions in runner_mutation.py."""

    @pytest.mark.asyncio
    @patch('runner.mutation.HAS_MUTMUT', True)
    @patch('runner.mutation.mutmut')
    async def test_mutation_test_python_valid(self, mock_mutmut, tmp_path, audit_log):
        """Test mutation testing for Python with valid setup."""
        trace_id = str(uuid.uuid4())
        config = MockRunnerConfig(mutation=True)
        code_files = {'example.py': 'def add(a, b): return a + b'}
        test_files = {'test_example.py': 'def test_add(): assert add(1, 2) == 3'}

        # Mock mutmut execution
        mock_mutmut.run.return_value = MagicMock(
            results={'killed': 5, 'survived': 2, 'timeout': 1, 'error': 0}
        )

        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            for file_name, content in code_files.items():
                (temp_dir / file_name).write_text(content)
            for file_name, content in test_files.items():
                (temp_dir / file_name).write_text(content)

            with patch('runner.mutation._run_subprocess_safe', AsyncMock(return_value={'returncode': 0})):
                result = await mutation_test(temp_dir, config, code_files, test_files)

        assert result['killed_mutants'] == 5
        assert result['survived_mutants'] == 2
        assert result['survival_rate'] == pytest.approx(2 / (5 + 2 + 1 + 0), rel=1e-2)
        assert MUTATION_KILLED.labels('python', 'operator', 'mutmut', 'test_instance')._value == 5
        assert result['parser_info']['status'] == "success"
        log_test_execution("test_mutation_test_python_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    @patch('runner.mutation.HAS_MUTMUT', False)
    async def test_mutation_test_no_mutmut(self, tmp_path, audit_log):
        """Test mutation testing when mutmut is not installed."""
        trace_id = str(uuid.uuid4())
        config = MockRunnerConfig(mutation=True)
        code_files = {'example.py': 'def add(a, b): return a + b'}
        test_files = {'test_example.py': 'def test_add(): assert add(1, 2) == 3'}

        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            with pytest.raises(RunnerError) as exc_info:
                await mutation_test(temp_dir, config, code_files, test_files)
            assert exc_info.value.error_code == error_codes["CONFIGURATION_ERROR"]
            assert "mutmut not installed" in exc_info.value.detail
        log_test_execution("test_mutation_test_no_mutmut", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_fuzz_test_valid(self, tmp_path, audit_log):
        """Test general fuzz testing with valid setup."""
        trace_id = str(uuid.uuid4())
        config = MockRunnerConfig(fuzz=True, fuzz_examples=10)
        code_files = {'example.py': 'def process(data): return data.upper()'}
        test_files = {}

        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            for file_name, content in code_files.items():
                (temp_dir / file_name).write_text(content)

            with patch('runner.mutation._run_subprocess_safe', AsyncMock(return_value={'returncode': 0})):
                with patch('random.random', side_effect=[0.01] * 10):  # Simulate discoveries
                    result = await fuzz_test(temp_dir, config, code_files, test_files)

        assert result['discoveries'] >= 0
        assert result['status'] == "completed"
        assert FUZZ_DISCOVERIES.labels('python', 'general', 'test_instance')._value >= 0
        log_test_execution("test_fuzz_test_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    @patch('runner.mutation.HAS_HYPOTHESIS', True)
    @patch('runner.mutation.hypothesis')
    async def test_property_based_test_valid(self, mock_hypothesis, tmp_path, audit_log):
        """Test property-based testing with Hypothesis."""
        trace_id = str(uuid.uuid4())
        config = MockRunnerConfig(fuzz=True)
        code_files = {'example.py': 'def square(x): return x * x'}
        test_files = {}

        mock_hypothesis.errors = MagicMock(FalsifyingExample=MagicMock(side_effect=ValueError("Falsified")))

        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            for file_name, content in code_files.items():
                (temp_dir / file_name).write_text(content)

            with patch('runner.mutation._run_subprocess_safe', AsyncMock(return_value={'returncode': 0})):
                with patch('hypothesis.find.find', MagicMock(side_effect=mock_hypothesis.errors.FalsifyingExample)):
                    result = await property_based_test(temp_dir, config, code_files)

        assert result['killed_mutants'] > 0
        assert result['status'] == "completed"
        assert FUZZ_DISCOVERIES.labels('python', 'property', 'test_instance')._value > 0
        log_test_execution("test_property_based_test_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    @patch('runner.mutation.HAS_HYPOTHESIS', False)
    async def test_property_based_test_no_hypothesis(self, tmp_path, audit_log):
        """Test property-based testing when Hypothesis is not installed."""
        trace_id = str(uuid.uuid4())
        config = MockRunnerConfig(fuzz=True)
        code_files = {'example.py': 'def square(x): return x * x'}
        test_files = {}

        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            with pytest.raises(RunnerError) as exc_info:
                await property_based_test(temp_dir, config, code_files)
            assert exc_info.value.error_code == error_codes["CONFIGURATION_ERROR"]
            assert "Hypothesis not installed" in exc_info.value.detail
        log_test_execution("test_property_based_test_no_hypothesis", "Passed", trace_id)

# Test class for subprocess and registry
class TestMutationUtils:
    """Tests for utility functions and registries in runner_mutation.py."""

    @pytest.mark.asyncio
    async def test_run_subprocess_safe_valid(self, audit_log):
        """Test safe subprocess execution."""
        trace_id = str(uuid.uuid4())
        cmd = ['echo', 'Hello World']
        result = await _run_subprocess_safe(cmd, timeout=10)
        assert result['stdout'] == 'Hello World\n'
        assert result['returncode'] == 0
        log_test_execution("test_run_subprocess_safe_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_run_subprocess_safe_timeout(self, audit_log):
        """Test subprocess timeout handling."""
        trace_id = str(uuid.uuid4())
        cmd = ['sleep', '5']
        with pytest.raises(TimeoutError) as exc_info:
            await _run_subprocess_safe(cmd, timeout=1)
        assert exc_info.value.error_code == error_codes["TASK_TIMEOUT"]
        log_test_execution("test_run_subprocess_safe_timeout", "Passed", trace_id)

    def test_register_mutator(self, audit_log):
        """Test mutator registration."""
        trace_id = str(uuid.uuid4())
        def mock_run_func(*args): return {}
        def mock_parse_func(*args): return {}
        register_mutator('test_lang', 'test_tool', ['.ext'], mock_run_func, mock_parse_func)
        assert 'test_lang' in _MUTATOR_REGISTRY
        assert _MUTATOR_REGISTRY['test_lang']['test_tool']['extensions'] == ['.ext']
        log_test_execution("test_register_mutator", "Passed", trace_id)

# Integration test class
class TestMutationIntegration:
    """Integration tests for mutation and fuzzing workflows."""

    @pytest.mark.asyncio
    @patch('runner.mutation.HAS_MUTMUT', True)
    @patch('runner.mutation.mutmut')
    async def test_mutation_and_fuzz_integration(self, mock_mutmut, tmp_path, audit_log):
        """Test integrated mutation and fuzz testing."""
        trace_id = str(uuid.uuid4())
        config = MockRunnerConfig(mutation=True, fuzz=True)
        code_files = {'example.py': 'def add(a, b): return a + b'}
        test_files = {'test_example.py': 'def test_add(): assert add(1, 2) == 3'}

        # Mock mutmut for mutation
        mock_mutmut.run.return_value = MagicMock(
            results={'killed': 3, 'survived': 1, 'timeout': 0, 'error': 0}
        )

        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            for file_name, content in code_files.items():
                (temp_dir / file_name).write_text(content)
            for file_name, content in test_files.items():
                (temp_dir / file_name).write_text(content)

            with patch('runner.mutation._run_subprocess_safe', AsyncMock(return_value={'returncode': 0})):
                with patch('random.random', side_effect=[0.01] * 5 + [0.9] * 5):
                    mutation_result = await mutation_test(temp_dir, config, code_files, test_files)
                    fuzz_result = await fuzz_test(temp_dir, config, code_files, test_files)

        assert mutation_result['survival_rate'] == pytest.approx(1 / 4, rel=1e-2)
        assert fuzz_result['discoveries'] >= 0
        assert MUTATION_SURVIVED.labels('python', 'operator', 'mutmut', 'test_instance')._value == 1
        assert FUZZ_DISCOVERIES.labels('python', 'general', 'test_instance')._value >= 0
        log_test_execution("test_mutation_and_fuzz_integration", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_mutation_error_propagation(self, tmp_path, audit_log):
        """Test error propagation in mutation testing."""
        trace_id = str(uuid.uuid4())
        config = MockRunnerConfig(mutation=True)
        code_files = {'invalid.py': 'syntax error'}
        test_files = {}

        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            with pytest.raises(TestExecutionError) as exc_info:
                await mutation_test(temp_dir, config, code_files, test_files)
            assert exc_info.value.error_code == error_codes["TEST_EXECUTION_FAILED"]
        log_test_execution("test_mutation_error_propagation", "Passed", trace_id)

# Run tests with audit logging
if __name__ == "__main__":
    pytest.main(["-v", "--log-level=DEBUG"])
