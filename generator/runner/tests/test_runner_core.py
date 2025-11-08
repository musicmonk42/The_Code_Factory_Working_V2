
# test_runner_core.py
# Highly regulated industry-grade test suite for runner_core.py.
# Provides comprehensive unit and integration tests for Runner core logic with strict
# traceability, reproducibility, security, and observability for audit compliance.

import unittest
import asyncio
import os
import sys
import tempfile
import shutil
import json
import uuid
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from prometheus_client import REGISTRY
from datetime import datetime, timezone
import logging

# Add parent directory to sys.path to import runner modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock external dependencies before importing runner modules
sys.modules['aiohttp'] = MagicMock()
sys.modules['backoff'] = MagicMock()
sys.modules['opentelemetry'] = MagicMock()
sys.modules['opentelemetry.trace'] = MagicMock()
sys.modules['opentelemetry.sdk.trace'] = MagicMock()
sys.modules['opentelemetry.sdk.trace.export'] = MagicMock()
sys.modules['jinja2'] = MagicMock()
sys.modules['cryptography'] = MagicMock()
sys.modules['cryptography.hazmat.primitives'] = MagicMock()
sys.modules['cryptography.hazmat.primitives.asymmetric'] = MagicMock()
sys.modules['cryptography.hazmat.primitives.serialization'] = MagicMock()

# Import runner modules
from runner.core import Runner
from runner.config import RunnerConfig
from runner.contracts import TaskPayload, TaskResult
from runner.errors import RunnerError, TestExecutionError, TimeoutError, ParsingError
from runner.logging import logger, log_action, LOG_HISTORY
from runner.metrics import RUN_QUEUE, RUN_PASS_RATE, RUN_EXECUTION_DURATION, RUN_EXECUTION_ERRORS
from runner.parsers import TestReportSchema
from runner.utils import save_files_to_output, redact_secrets

class TestRunnerCore(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Create temporary directory for test files
        self.temp_dir = Path(tempfile.mkdtemp())
        self.output_dir = self.temp_dir / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Mock configuration
        self.config = RunnerConfig(
            version=4,
            backend='docker',
            framework='pytest',
            instance_id='test_instance',
            timeout=300,
            mutation=False,
            fuzz=False
        )

        # Mock environment variables
        self.patch_env = patch.dict(os.environ, {
            'RUNNER_ENV': 'development'
        })
        self.patch_env.start()

        # Mock dependencies
        self.mock_backend = MagicMock()
        self.patch_backend = patch('runner.core.ALL_BACKENDS', {
            'docker': MagicMock(return_value=self.mock_backend)
        })
        self.patch_backend.start()
        self.mock_parse_junit = patch('runner.core.parse_junit_xml', return_value=TestReportSchema(
            total_tests=1,
            passed_tests=1,
            failed_tests=0,
            error_tests=0,
            skipped_tests=0,
            pass_rate=1.0,
            parser_info={'parser_name': 'junit', 'status': 'success', 'version': '1.0'}
        ))
        self.mock_parse_junit.start()
        self.mock_save_files = patch('runner.core.save_files_to_output', new_callable=AsyncMock)
        self.mock_save_files.start()
        self.mock_redact_secrets = patch('runner.core.redact_secrets', side_effect=lambda x: {k: v.replace('sk-abc123', '[REDACTED]') for k, v in x.items()})
        self.mock_redact_secrets.start()
        self.mock_tracer = patch('runner.core.trace.get_tracer', return_value=MagicMock())
        self.mock_tracer.start()

        # Clear Prometheus registry
        for collector in list(REGISTRY._collectors.values()):
            REGISTRY.unregister(collector)

        # Configure logging
        logging.basicConfig(level=logging.INFO)
        self.run_id = str(uuid.uuid4())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.patch_env.stop()
        self.patch_backend.stop()
        self.mock_parse_junit.stop()
        self.mock_save_files.stop()
        self.mock_redact_secrets.stop()
        self.mock_tracer.stop()
        LOG_HISTORY.clear()

    async def test_runner_enqueue(self):
        """Test: Runner enqueues a task correctly."""
        runner = Runner(self.config)
        task = TaskPayload(
            task_id=str(uuid.uuid4()),
            test_files={'test.py': 'def test(): pass'},
            code_files={'code.py': 'def func(): return 1'},
            output_path=str(self.output_dir)
        )
        await runner.enqueue(task)
        self.assertEqual(RUN_QUEUE.labels(framework='pytest', instance_id='test_instance')._value, 1)
        log_action.assert_called_with(
            'TaskEnqueued',
            {'task_id': task.task_id, 'priority': 0},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    async def test_runner_process_queue(self):
        """Test: Runner processes queue and executes task."""
        runner = Runner(self.config)
        task = TaskPayload(
            task_id=str(uuid.uuid4()),
            test_files={'test.py': 'def test(): pass'},
            code_files={'code.py': 'def func(): return 1'},
            output_path=str(self.output_dir)
        )
        self.mock_backend.execute.return_value = TaskResult(
            task_id=task.task_id,
            status='completed',
            results={'tests': 1, 'pass_rate': 1.0},
            started_at=1625097600.0,
            finished_at=1625097601.0
        )
        await runner.enqueue(task)
        await runner.process_queue()
        self.assertEqual(RUN_QUEUE.labels(framework='pytest', instance_id='test_instance')._value, 0)
        self.assertEqual(RUN_PASS_RATE.labels(framework='pytest', instance_id='test_instance')._value, 1.0)
        RUN_EXECUTION_DURATION.labels.assert_called_with(backend='docker', framework='pytest', instance_id='test_instance')
        log_action.assert_called_with(
            'TaskExecution',
            {'task_id': task.task_id, 'status': 'completed'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    async def test_runner_timeout(self):
        """Test: Runner handles TimeoutError during task execution."""
        runner = Runner(self.config)
        task = TaskPayload(
            task_id=str(uuid.uuid4()),
            test_files={'test.py': 'def test(): pass'},
            code_files={'code.py': 'def func(): return 1'},
            output_path=str(self.output_dir)
        )
        self.mock_backend.execute.side_effect = TimeoutError(
            error_code='TASK_TIMEOUT',
            detail='Task timed out',
            task_id=task.task_id,
            timeout_seconds=300
        )
        await runner.enqueue(task)
        await runner.process_queue()
        self.assertEqual(RUN_QUEUE.labels(framework='pytest', instance_id='test_instance')._value, 0)
        RUN_EXECUTION_ERRORS.labels.assert_called_with(
            backend='docker', framework='pytest', instance_id='test_instance', error_type='TASK_TIMEOUT'
        )
        log_action.assert_called_with(
            'TaskFailed',
            {'task_id': task.task_id, 'error': 'TASK_TIMEOUT'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    async def test_runner_pii_redaction(self):
        """Test: Runner redacts PII in task files."""
        runner = Runner(self.config)
        task = TaskPayload(
            task_id=str(uuid.uuid4()),
            test_files={'test.py': 'def test(): print("API_KEY=sk-abc123")'},
            code_files={'code.py': 'def func(): return 1'},
            output_path=str(self.output_dir)
        )
        self.mock_backend.execute.return_value = TaskResult(
            task_id=task.task_id,
            status='completed',
            results={'output': 'API_KEY=sk-abc123'}
        )
        await runner.enqueue(task)
        await runner.process_queue()
        self.mock_redact_secrets.assert_called()
        self.assertIn('[REDACTED]', json.dumps(list(LOG_HISTORY)))
        self.assertNotIn('sk-abc123', json.dumps(list(LOG_HISTORY)))
        self.mock_save_files.assert_called()
        call_args = self.mock_save_files.call_args[0][0]
        self.assertIn('[REDACTED]', json.dumps(call_args))

    async def test_runner_parsing(self):
        """Test: Runner parses execution results correctly."""
        runner = Runner(self.config)
        task = TaskPayload(
            task_id=str(uuid.uuid4()),
            test_files={'test.py': 'def test(): pass'},
            code_files={'code.py': 'def func(): return 1'},
            output_path=str(self.output_dir)
        )
        self.mock_backend.execute.return_value = TaskResult(
            task_id=task.task_id,
            status='completed',
            results={'output': '<testsuite tests="1" failures="0"></testsuite>'}
        )
        await runner.enqueue(task)
        await runner.process_queue()
        self.mock_parse_junit.assert_called()
        self.assertEqual(RUN_PASS_RATE.labels(framework='pytest', instance_id='test_instance')._value, 1.0)
        log_action.assert_called_with(
            'ResultParsed',
            {'task_id': task.task_id, 'parser': 'junit'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    async def test_runner_mutation(self):
        """Test: Runner handles mutation testing when enabled."""
        with patch.object(self.config, 'mutation', True):
            with patch('runner.core._mutation_test_func', new=AsyncMock(return_value={'survived': 0, 'killed': 1})) as mock_mutation:
                runner = Runner(self.config)
                task = TaskPayload(
                    task_id=str(uuid.uuid4()),
                    test_files={'test.py': 'def test(): pass'},
                    code_files={'code.py': 'def func(): return 1'},
                    output_path=str(self.output_dir)
                )
                self.mock_backend.execute.return_value = TaskResult(
                    task_id=task.task_id,
                    status='completed',
                    results={'tests': 1}
                )
                await runner.enqueue(task)
                await runner.process_queue()
                mock_mutation.assert_called()
                log_action.assert_called_with(
                    'MutationTest',
                    {'task_id': task.task_id, 'survived': 0, 'killed': 1},
                    run_id=unittest.mock.ANY,
                    provenance_hash=unittest.mock.ANY
                )

    async def test_runner_invalid_backend(self):
        """Test: Runner handles invalid backend configuration."""
        config = RunnerConfig(
            version=4,
            backend='invalid',
            framework='pytest',
            instance_id='test_instance'
        )
        with self.assertRaises(ConfigurationError) as cm:
            Runner(config)
        self.assertEqual(cm.exception.error_code, 'CONFIGURATION_ERROR')
        self.assertIn('invalid', cm.exception.detail)
        log_action.assert_called_with(
            'RunnerInitializationFailed',
            {'error': 'CONFIGURATION_ERROR', 'detail': unittest.mock.ANY},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    async def test_runner_traceability(self):
        """Test: Runner operations are traceable with run_id and OpenTelemetry."""
        runner = Runner(self.config)
        task = TaskPayload(
            task_id=str(uuid.uuid4()),
            test_files={'test.py': 'def test(): pass'},
            code_files={'code.py': 'def func(): return 1'},
            output_path=str(self.output_dir)
        )
        self.mock_backend.execute.return_value = TaskResult(
            task_id=task.task_id,
            status='completed',
            results={'tests': 1}
        )
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True
        mock_span.get_span_context.return_value = MagicMock(trace_id=123, span_id=456)
        self.mock_tracer.return_value.start_as_current_span.return_value.__enter__.return_value = mock_span
        await runner.enqueue(task)
        await runner.process_queue()
        mock_span.set_attribute.assert_called()
        calls = mock_span.set_attribute.call_args_list
        self.assertIn(('task_id', task.task_id), [(c[0][0], c[0][1]) for c in calls])
        self.assertIn(('status', 'completed'), [(c[0][0], c[0][1]) for c in calls])
        log_action.assert_called_with(
            unittest.mock.ANY,
            unittest.mock.ANY,
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )
        self.assertTrue(any(log['run_id'] for log in LOG_HISTORY))

if __name__ == '__main__':
    unittest.main()


### Explanation of the Test Suite

#### Design Principles
- **Regulatory Compliance**: Ensures traceability (via `run_id` and OpenTelemetry), PII redaction, and structured error handling for auditability.
- **Reproducibility**: Mocks all external dependencies (`aiohttp`, `jinja2`, `runner.backends`, `runner.parsers`, `runner.mutation`) for deterministic results.
- **Security**: Validates PII redaction in task files and secure file handling with provenance.
- **Comprehensive Coverage**: Tests task enqueueing, queue processing, backend execution, result parsing, mutation testing, and error handling.
- **Isolation**: No real backend executions or file system interactions; all operations are mocked.

#### Test Cases
1. **Task Enqueue (`test_runner_enqueue`)**:
   - Tests `Runner.enqueue` adds a task to the priority queue.
   - Verifies `RUN_QUEUE` metric and logging with `run_id`.

2. **Queue Processing (`test_runner_process_queue`)**:
   - Tests `Runner.process_queue` executes tasks via the backend and updates metrics (`RUN_QUEUE`, `RUN_PASS_RATE`).
   - Verifies task results and logging.

3. **Timeout Handling (`test_runner_timeout`)**:
   - Simulates a `TimeoutError` during execution and verifies error handling.
   - Checks `RUN_EXECUTION_ERRORS` metric and logging.

4. **PII Redaction (`test_runner_pii_redaction`)**:
   - Tests PII redaction in task files and outputs using `redact_secrets`.
   - Verifies `[REDACTED]` in logs and saved files.

5. **Result Parsing (`test_runner_parsing`)**:
   - Tests integration with `parse_junit_xml` for result processing.
   - Verifies `RUN_PASS_RATE` and logging.

6. **Mutation Testing (`test_runner_mutation`)**:
   - Tests mutation testing when enabled in `RunnerConfig`.
   - Verifies `mutation_test` call and logging.

7. **Invalid Backend (`test_runner_invalid_backend`)**:
   - Tests that an invalid backend raises `ConfigurationError`.
   - Verifies error logging.

8. **Traceability (`test_runner_traceability`)**:
   - Ensures all operations are traced with `run_id` and OpenTelemetry attributes (`task_id`, `status`).

#### Regulatory Features
- **Traceability**: Each test logs with a `run_id` and mocked OpenTelemetry spans for audit trails.
- **Security**: Validates PII redaction (e.g., API keys replaced with `[REDACTED]`) in logs and outputs.
- **Auditability**: Structured errors and logs include metadata (`task_id`, `run_id`, `provenance_hash`).
- **Reproducibility**: Mocks ensure consistent outcomes, critical for regulated environments.
- **Metrics**: Verifies Prometheus metrics (`RUN_QUEUE`, `RUN_PASS_RATE`, `RUN_EXECUTION_DURATION`, `RUN_EXECUTION_ERRORS`).

#### Implementation Notes
- **Mocks**: Mocks `runner.backends`, `runner.parsers`, `runner.utils`, `runner.mutation`, `aiohttp`, `jinja2`, and OpenTelemetry to isolate tests.
- **Temporary Directory**: Uses `tempfile` for output files, cleaned up in `tearDown`.
- **Logging**: Integrates with `runner_logging.py` for structured logs and `LOG_HISTORY`.
- **Metrics**: Clears Prometheus registry in `tearDown` to isolate tests.
- **Focus**: Tests focus on `Runner` class functionality, covering task orchestration, execution, and parsing.

### Running the Tests
1. Save the file in the `runner` directory (e.g., `D:\Code_Factory\Generator\runner\tests\test_runner_core.py`).
2. Install required test dependencies:
   ```bash
   pip install unittest mock prometheus_client pydantic
   ```
3. Run the tests:
   ```bash
   python -m unittest D:\Code_Factory\Generator\runner\tests\test_runner_core.py
   ```
4. For verbose output:
   ```bash
   python -m unittest D:\Code_Factory\Generator\runner\tests\test_runner_core.py -v
   ```

### Notes
- **Dependencies**: Requires `unittest`, `mock`, `prometheus_client`, and `pydantic`. All other dependencies are mocked.
- **Scope**: Covers all critical `Runner` functionality in `runner_core.py`, including task management, execution, and parsing.
- **Regulatory Compliance**: Designed for auditability with traceability, PII redaction, and structured error handling.
- **Future Enhancements**: If E2E tests are needed, we can add tests with real backend executions (e.g., Docker) or distributed scenarios.
- **Proprietary Nature**: The test suite is for internal use by Unexpected Innovations Inc., aligning with the proprietary license.

If you need additional test cases, tests for specific scenarios, or E2E tests in the future, please let me know! I can also provide a combined test suite for multiple `runner` modules if needed.