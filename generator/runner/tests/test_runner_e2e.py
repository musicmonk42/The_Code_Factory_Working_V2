
# test_runner_e2e.py
# Industry-grade end-to-end integration test suite for the runner module.
# Ensures all components (app, backends, config, contracts, core, errors, logging, metrics, mutation, parsers, utils)
# work together seamlessly, with traceability, reproducibility, and security for regulated environments.

import pytest
import asyncio
import json
import os
import base64
import uuid
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone
import logging
from collections import deque

# Import required classes and functions from the runner module
from runner.app import RunnerApp
from runner.backends import BACKEND_REGISTRY, DockerBackend
from runner.config import RunnerConfig, load_config
from runner.contracts import TaskPayload, TaskResult
from runner.core import Runner
from runner.errors import (
    RunnerError, BackendError, TestExecutionError, ParsingError, TimeoutError,
    ConfigurationError, PersistenceError, ERROR_CODE_REGISTRY
)
from runner.logging import logger, log_action, LOG_HISTORY, configure_logging_from_config
from runner.metrics import (
    RUN_SUCCESS, RUN_FAILURE, RUN_PASS_RATE, RUN_RESOURCE_USAGE, RUN_QUEUE,
    MetricsExporter
)
from runner.mutation import mutation_test, fuzz_test
from runner.parsers import parse_junit_xml, parse_coverage_xml, TestReportSchema, CoverageReportSchema
from runner.utils import redact_secrets, save_files_to_output

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
    return tmp_path_factory.mktemp("e2e_test")

# Fixture for mock OpenTelemetry
@pytest.fixture(autouse=True)
def mock_opentelemetry():
    """Mock OpenTelemetry for all tests."""
    with patch('runner.errors.trace', mock_tracer), \
         patch('runner.logging.trace', mock_tracer), \
         patch('runner.metrics.trace', mock_tracer), \
         patch('runner.mutation.trace', mock_tracer):
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

# Fixture for mock RunnerConfig
@pytest.fixture
def mock_config(tmp_path):
    """Create a mock RunnerConfig for testing."""
    config = RunnerConfig(
        version=4,
        backend='docker',
        framework='pytest',
        instance_id='e2e_test_instance',
        parallel_workers=1,
        timeout=10,
        mutation=False,
        fuzz=False,
        distributed=False,
        log_sinks=[
            {'type': 'file', 'config': {'path': str(tmp_path / 'test.log')}},
            {'type': 'stream', 'config': {}}
        ],
        log_level='DEBUG',
        log_redaction_enabled=True,
        log_encryption_enabled=True,
        log_signing_enabled=True,
        log_signing_key='mock_signing_key',
        real_time_log_streaming=True,
        metrics_failover_file=str(tmp_path / 'metrics_failover.json'),
        metrics_export_retry_interval_seconds=1,
        max_metrics_export_retries=2,
        alert_monitor_interval_seconds=1,
        alert_thresholds={
            'runner_resource_usage_percent': {'cpu': 90.0, 'mem': 90.0},
            'runner_overall_test_pass_rate': 0.7
        }
    )
    config_file = tmp_path / "runner.yaml"
    config_file.write_text(json.dumps(config.model_dump(), indent=2))
    return config

# Fixture for mock Docker client
@pytest.fixture
def mock_docker():
    """Mock Docker client for backend testing."""
    with patch('runner.backends.DockerClient', MagicMock()) as mock_client:
        mock_client.containers.run.return_value = MagicMock(
            logs=MagicMock(return_value=b"Mocked output")
        )
        yield mock_client

# Helper function to log test execution for auditability
def log_test_execution(test_name, result, trace_id):
    """Log test execution details for audit trail."""
    logger.debug(
        f"Test {test_name}: {result}",
        extra={'trace_id': trace_id}
    )

# E2E integration test class
class TestRunnerE2E:
    """End-to-end integration tests for the runner module."""

    @pytest.mark.asyncio
    @patch('runner.metrics.prom.start_http_server')
    @patch('runner.app.Runner')
    @patch('runner.metrics.MetricsExporter')
    @patch('aiohttp.ClientSession.post')
    async def test_e2e_test_execution(self, mock_post, mock_metrics_exporter, mock_runner, mock_prometheus, mock_config, mock_docker, tmp_path, audit_log):
        """Test end-to-end test execution from configuration to result parsing."""
        trace_id = str(uuid.uuid4())
        # Mock logging configuration
        with patch.dict(os.environ, {'FERNET_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode()}):
            configure_logging_from_config(mock_config)

        # Mock Runner behavior
        mock_result = TaskResult(
            task_id="mock_task",
            status="completed",
            results={
                "total_tests": 2,
                "passed_tests": 1,
                "failed_tests": 1,
                "pass_rate": 0.5
            },
            tags=["e2e"],
            environment="test",
            started_at=datetime.now(timezone.utc).timestamp(),
            finished_at=datetime.now(timezone.utc).timestamp()
        )
        mock_runner.return_value.run_tests.return_value = mock_result
        mock_runner.return_value.enqueue = AsyncMock()

        # Mock MetricsExporter
        mock_metrics_exporter.return_value.export_all = AsyncMock()
        mock_metrics_exporter.return_value.shutdown = AsyncMock()

        # Mock HTTP post for log sinks
        mock_post.return_value.__aenter__.return_value = AsyncMock(status=200)

        # Prepare test and code files
        test_files = {'test_example.py': 'def test_add(): assert add(1, 2) == 3'}
        code_files = {'example.py': 'def add(a, b): return a + b'}
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Write JUnit XML result
        junit_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="TestSuite" tests="2" failures="1">
    <testcase classname="test_example" name="test_pass" time="0.1"/>
    <testcase classname="test_example" name="test_fail" time="0.2">
      <failure message="Assertion failed">Traceback...</failure>
    </testcase>
  </testsuite>
</testsuites>"""
        (output_dir / "results.xml").write_text(junit_content)

        # Create TaskPayload
        task_payload = TaskPayload(
            test_files=test_files,
            code_files=code_files,
            output_path=str(output_dir),
            task_id="mock_task",
            environment="test"
        )

        # Initialize Runner and execute
        runner = Runner(mock_config)
        result = await runner.run_tests(task_payload)

        # Validate results
        assert result.status == "completed"
        assert result.results["total_tests"] == 2
        assert result.results["pass_rate"] == 0.5
        assert RUN_SUCCESS._metrics[('docker', 'pytest', 'e2e_test_instance')].get() == 1
        assert RUN_PASS_RATE._metrics[()].get() == 0.5

        # Validate logs
        assert len(LOG_HISTORY) > 0
        last_log = LOG_HISTORY[-1]
        assert '[REDACTED]' not in json.dumps(last_log)  # No sensitive data in test
        assert 'signature' in last_log

        # Validate parsed output
        test_report = await parse_junit_xml(output_dir / "results.xml")
        assert test_report.total_tests == 2
        assert test_report.passed_tests == 1
        assert test_report.pass_rate == 0.5

        log_test_execution("test_e2e_test_execution", "Passed", trace_id)

    @pytest.mark.asyncio
    @patch('runner.metrics.prom.start_http_server')
    @patch('runner.mutation.HAS_MUTMUT', True)
    @patch('runner.mutation.mutmut')
    async def test_e2e_mutation_testing(self, mock_mutmut, mock_prometheus, mock_config, mock_docker, tmp_path, audit_log):
        """Test end-to-end mutation testing workflow."""
        trace_id = str(uuid.uuid4())
        mock_config.mutation = True
        with patch.dict(os.environ, {'FERNET_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode()}):
            configure_logging_from_config(mock_config)

        # Mock mutmut behavior
        mock_mutmut.run.return_value = MagicMock(
            results={'killed': 3, 'survived': 1, 'timeout': 0, 'error': 0}
        )

        # Prepare test and code files
        code_files = {'example.py': 'def add(a, b): return a + b'}
        test_files = {'test_example.py': 'def test_add(): assert add(1, 2) == 3'}
        temp_dir = tmp_path / "temp"
        temp_dir.mkdir()
        for file_name, content in code_files.items():
            (temp_dir / file_name).write_text(content)
        for file_name, content in test_files.items():
            (temp_dir / file_name).write_text(content)

        # Mock subprocess for mutation
        with patch('runner.mutation._run_subprocess_safe', AsyncMock(return_value={'returncode': 0})):
            result = await mutation_test(temp_dir, mock_config, code_files, test_files)

        # Validate mutation results
        assert result['killed_mutants'] == 3
        assert result['survived_mutants'] == 1
        assert result['survival_rate'] == pytest.approx(1 / 4, rel=1e-2)
        assert result['parser_info']['status'] == "success"

        # Validate metrics
        from runner.metrics import MUTATION_SURVIVED
        assert MUTATION_SURVIVED._metrics[('python', 'operator', 'mutmut', 'e2e_test_instance')].get() == 1

        # Validate logs
        assert len(LOG_HISTORY) > 0
        assert any('mutation' in json.dumps(log).lower() for log in LOG_HISTORY)

        log_test_execution("test_e2e_mutation_testing", "Passed", trace_id)

    @pytest.mark.asyncio
    @patch('runner.metrics.prom.start_http_server')
    async def test_e2e_error_handling(self, mock_prometheus, mock_config, mock_docker, tmp_path, audit_log):
        """Test end-to-end error handling with invalid configuration."""
        trace_id = str(uuid.uuid4())
        mock_config.backend = 'invalid_backend'  # Invalid backend to trigger error
        with patch.dict(os.environ, {'FERNET_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode()}):
            configure_logging_from_config(mock_config)

        task_payload = TaskPayload(
            test_files={'test_example.py': 'def test_add(): assert add(1, 2) == 3'},
            code_files={'example.py': 'def add(a, b): return a + b'},
            output_path=str(tmp_path / "output"),
            task_id="mock_task"
        )

        runner = Runner(mock_config)
        with pytest.raises(ConfigurationError) as exc_info:
            await runner.run_tests(task_payload)

        # Validate error
        assert exc_info.value.error_code == ERROR_CODE_REGISTRY["CONFIGURATION_ERROR"]
        assert "Invalid backend" in exc_info.value.detail

        # Validate logs
        assert len(LOG_HISTORY) > 0
        assert any('CONFIGURATION_ERROR' in json.dumps(log) for log in LOG_HISTORY)

        log_test_execution("test_e2e_error_handling", "Passed", trace_id)

    @pytest.mark.asyncio
    @patch('runner.metrics.prom.start_http_server')
    @patch('runner.app.RunnerApp.run', AsyncMock())
    async def test_e2e_tui_interaction(self, mock_prometheus, mock_config, mock_docker, tmp_path, audit_log):
        """Test end-to-end TUI interaction with task submission."""
        trace_id = str(uuid.uuid4())
        with patch.dict(os.environ, {'FERNET_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode(), 'RUNNER_ENV': 'development'}):
            configure_logging_from_config(mock_config)

        # Mock Runner behavior
        mock_result = TaskResult(
            task_id="mock_task",
            status="completed",
            results={"total_tests": 1, "passed_tests": 1, "pass_rate": 1.0}
        )
        with patch('runner.core.Runner.run_tests', AsyncMock(return_value=mock_result)):
            app = RunnerApp(production_mode=False)
            async with app.run_test() as pilot:
                # Simulate task submission
                task_payload = TaskPayload(
                    test_files={'test_example.py': 'def test_add(): assert add(1, 2) == 3'},
                    code_files={'example.py': 'def add(a, b): return a + b'},
                    output_path=str(tmp_path / "output"),
                    task_id="mock_task"
                )
                await app._submit_task(task_payload)

        # Validate metrics
        assert RUN_SUCCESS._metrics[('docker', 'pytest', 'e2e_test_instance')].get() == 1
        assert RUN_PASS_RATE._metrics[()].get() == 1.0

        # Validate logs
        assert len(LOG_HISTORY) > 0
        assert any('Task mock_task' in json.dumps(log) for log in LOG_HISTORY)

        log_test_execution("test_e2e_tui_interaction", "Passed", trace_id)

# Run tests with audit logging
if __name__ == "__main__":
    pytest.main(["-v", "--log-level=DEBUG"])
