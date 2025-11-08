
# test_runner_contracts.py
# Highly regulated industry-grade test suite for runner_contracts.py.
# Provides comprehensive unit and integration tests for Pydantic models with strict
# traceability, reproducibility, security, and observability for audit compliance.

import unittest
import uuid
import json
import logging
from pathlib import Path
from pydantic import ValidationError
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

# Add parent directory to sys.path to import runner modules
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Mock dependencies for logging and OpenTelemetry
sys.modules['opentelemetry'] = MagicMock()
sys.modules['opentelemetry.trace'] = MagicMock()
sys.modules['opentelemetry.sdk.trace'] = MagicMock()
sys.modules['opentelemetry.sdk.trace.export'] = MagicMock()

# Import runner modules
from runner.contracts import TaskPayload, TaskResult, BatchTaskPayload, CURRENT_SCHEMA_VERSION
from runner.logging import logger, log_action, LOG_HISTORY
from runner.errors import ValidationError as RunnerValidationError

class TestRunnerContracts(unittest.TestCase):
    def setUp(self):
        # Configure logging
        logging.basicConfig(level=logging.INFO)
        self.run_id = str(uuid.uuid4())
        LOG_HISTORY.clear()  # Clear in-memory log store for isolation

        # Mock OpenTelemetry tracer
        self.mock_tracer = patch('runner.logging.trace.get_tracer', return_value=MagicMock())
        self.mock_tracer.start()
        self.mock_span = MagicMock()
        self.mock_span.is_recording.return_value = True
        self.mock_span.get_span_context.return_value = MagicMock(trace_id=123, span_id=456)
        self.mock_tracer.return_value.start_as_current_span.return_value.__enter__.return_value = self.mock_span

    def tearDown(self):
        self.mock_tracer.stop()
        LOG_HISTORY.clear()

    def test_task_payload_validation(self):
        """Test: TaskPayload validates required fields and defaults."""
        payload = TaskPayload(
            test_files={'test.py': 'def test(): pass'},
            code_files={'code.py': 'def func(): return 1'},
            output_path='/tmp/output',
            tags=['test'],
            environment='staging'
        )
        self.assertEqual(payload.schema_version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(payload.test_files, {'test.py': 'def test(): pass'})
        self.assertEqual(payload.code_files, {'code.py': 'def func(): return 1'})
        self.assertEqual(payload.output_path, '/tmp/output')
        self.assertIsInstance(payload.task_id, str)
        self.assertEqual(payload.tags, ['test'])
        self.assertEqual(payload.environment, 'staging')
        self.assertIsNone(payload.timeout)
        self.assertFalse(payload.dry_run)
        self.assertEqual(payload.priority, 0)
        log_action.assert_called_with(
            'ContractValidated',
            {'model': 'TaskPayload', 'task_id': payload.task_id},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )
        self.mock_span.set_attribute.assert_called()
        calls = self.mock_span.set_attribute.call_args_list
        self.assertIn(('model', 'TaskPayload'), [(c[0][0], c[0][1]) for c in calls])
        self.assertIn(('task_id', payload.task_id), [(c[0][0], c[0][1]) for c in calls])

    def test_task_payload_validation_failure(self):
        """Test: TaskPayload raises ValidationError for missing required fields."""
        with self.assertRaises(ValidationError) as cm:
            TaskPayload()  # Missing test_files, code_files, output_path
        self.assertIn('test_files', str(cm.exception))
        self.assertIn('code_files', str(cm.exception))
        self.assertIn('output_path', str(cm.exception))
        log_action.assert_called_with(
            'ContractValidationFailed',
            {'model': 'TaskPayload', 'error': 'ValidationError', 'details': unittest.mock.ANY},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )
        self.mock_span.set_attribute.assert_called()
        self.assertIn(('error', 'ValidationError'), [(c[0][0], c[0][1]) for c in self.mock_span.set_attribute.call_args_list])
        self.assertTrue(any(log['run_id'] for log in LOG_HISTORY))

    def test_task_payload_serialization(self):
        """Test: TaskPayload serializes correctly and redacts sensitive data."""
        payload = TaskPayload(
            test_files={'test.py': 'def test(): print("API_KEY=sk-abc123")'},
            code_files={'code.py': 'def func(): return 1'},
            output_path='/tmp/output',
            task_id='test_task_123',
            tags=['test'],
            environment='staging'
        )
        with patch('runner.utils.redact_secrets', return_value={'test.py': 'def test(): print("[REDACTED]")'}) as mock_redact:
            serialized = payload.model_dump_json()
            mock_redact.assert_called_with(payload.test_files)
            self.assertIn('[REDACTED]', serialized)
            self.assertNotIn('sk-abc123', serialized)
            deserialized = TaskPayload.model_validate_json(serialized)
            self.assertEqual(deserialized.task_id, 'test_task_123')
            log_action.assert_called_with(
                'ContractSerialized',
                {'model': 'TaskPayload', 'task_id': 'test_task_123'},
                run_id=unittest.mock.ANY,
                provenance_hash=unittest.mock.ANY
            )

    def test_task_result_validation(self):
        """Test: TaskResult validates required fields and defaults."""
        result = TaskResult(
            task_id='test_task_123',
            status='completed',
            results={'tests': 1, 'pass_rate': 1.0},
            started_at=1625097600.0,
            finished_at=1625097601.0,
            tags=['test'],
            environment='production'
        )
        self.assertEqual(result.schema_version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(result.task_id, 'test_task_123')
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.results, {'tests': 1, 'pass_rate': 1.0})
        self.assertEqual(result.started_at, 1625097600.0)
        self.assertEqual(result.finished_at, 1625097601.0)
        self.assertEqual(result.tags, ['test'])
        self.assertEqual(result.environment, 'production')
        self.assertIsNone(result.error)
        log_action.assert_called_with(
            'ContractValidated',
            {'model': 'TaskResult', 'task_id': 'test_task_123'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )
        self.mock_span.set_attribute.assert_called()
        calls = self.mock_span.set_attribute.call_args_list
        self.assertIn(('model', 'TaskResult'), [(c[0][0], c[0][1]) for c in calls])
        self.assertIn(('task_id', 'test_task_123'), [(c[0][0], c[0][1]) for c in calls])

    def test_task_result_validation_failure(self):
        """Test: TaskResult raises ValidationError for invalid status."""
        with self.assertRaises(ValidationError) as cm:
            TaskResult(task_id='test_task_123', status='invalid')  # Invalid status
        self.assertIn('status', str(cm.exception))
        log_action.assert_called_with(
            'ContractValidationFailed',
            {'model': 'TaskResult', 'error': 'ValidationError', 'details': unittest.mock.ANY},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )
        self.mock_span.set_attribute.assert_called()
        self.assertIn(('error', 'ValidationError'), [(c[0][0], c[0][1]) for c in self.mock_span.set_attribute.call_args_list])

    def test_task_result_with_error(self):
        """Test: TaskResult handles error field correctly."""
        result = TaskResult(
            task_id='test_task_123',
            status='failed',
            error={'error_code': 'TEST_EXECUTION_FAILED', 'detail': 'Test failed'},
            tags=['test'],
            environment='staging'
        )
        self.assertEqual(result.schema_version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(result.error, {'error_code': 'TEST_EXECUTION_FAILED', 'detail': 'Test failed'})
        serialized = result.model_dump_json()
        self.assertIn('TEST_EXECUTION_FAILED', serialized)
        log_action.assert_called_with(
            'ContractValidated',
            {'model': 'TaskResult', 'task_id': 'test_task_123', 'status': 'failed'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    def test_batch_task_payload_validation(self):
        """Test: BatchTaskPayload validates tasks and batch_id."""
        task1 = TaskPayload(
            test_files={'test1.py': 'def test(): pass'},
            code_files={'code1.py': 'def func(): return 1'},
            output_path='/tmp/output1',
            task_id='task1'
        )
        task2 = TaskPayload(
            test_files={'test2.py': 'def test(): pass'},
            code_files={'code2.py': 'def func(): return 2'},
            output_path='/tmp/output2',
            task_id='task2'
        )
        batch = BatchTaskPayload(tasks=[task1, task2])
        self.assertEqual(batch.schema_version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(len(batch.tasks), 2)
        self.assertEqual(batch.tasks[0].task_id, 'task1')
        self.assertEqual(batch.tasks[1].task_id, 'task2')
        self.assertIsInstance(batch.batch_id, str)
        log_action.assert_called_with(
            'ContractValidated',
            {'model': 'BatchTaskPayload', 'batch_id': batch.batch_id, 'task_count': 2},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )
        self.mock_span.set_attribute.assert_called()
        calls = self.mock_span.set_attribute.call_args_list
        self.assertIn(('model', 'BatchTaskPayload'), [(c[0][0], c[0][1]) for c in calls])
        self.assertIn(('batch_id', batch.batch_id), [(c[0][0], c[0][1]) for c in calls])

    def test_batch_task_payload_empty_tasks(self):
        """Test: BatchTaskPayload raises ValidationError for empty tasks."""
        with self.assertRaises(ValidationError) as cm:
            BatchTaskPayload(tasks=[])
        self.assertIn('tasks', str(cm.exception))
        log_action.assert_called_with(
            'ContractValidationFailed',
            {'model': 'BatchTaskPayload', 'error': 'ValidationError', 'details': unittest.mock.ANY},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    def test_batch_task_payload_serialization(self):
        """Test: BatchTaskPayload serializes correctly with nested tasks."""
        task = TaskPayload(
            test_files={'test.py': 'def test(): pass'},
            code_files={'code.py': 'def func(): return 1'},
            output_path='/tmp/output',
            task_id='test_task_123'
        )
        batch = BatchTaskPayload(tasks=[task], batch_id='batch_123')
        serialized = batch.model_dump_json()
        deserialized = BatchTaskPayload.model_validate_json(serialized)
        self.assertEqual(deserialized.batch_id, 'batch_123')
        self.assertEqual(deserialized.tasks[0].task_id, 'test_task_123')
        log_action.assert_called_with(
            'ContractSerialized',
            {'model': 'BatchTaskPayload', 'batch_id': 'batch_123'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    def test_schema_version_mismatch(self):
        """Test: Models reject invalid schema versions."""
        with self.assertRaises(ValidationError) as cm:
            TaskPayload(
                test_files={'test.py': 'def test(): pass'},
                code_files={'code.py': 'def func(): return 1'},
                output_path='/tmp/output',
                schema_version=CURRENT_SCHEMA_VERSION + 1
            )
        self.assertIn('schema_version', str(cm.exception))
        log_action.assert_called_with(
            'ContractValidationFailed',
            {'model': 'TaskPayload', 'error': 'ValidationError', 'details': unittest.mock.ANY},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    def test_traceability(self):
        """Test: All model validations are traceable with run_id and OpenTelemetry."""
        task = TaskPayload(
            test_files={'test.py': 'def test(): pass'},
            code_files={'code.py': 'def func(): return 1'},
            output_path='/tmp/output',
            task_id='test_task_123'
        )
        self.mock_span.set_attribute.assert_called()
        calls = self.mock_span.set_attribute.call_args_list
        self.assertIn(('model', 'TaskPayload'), [(c[0][0], c[0][1]) for c in calls])
        self.assertIn(('task_id', 'test_task_123'), [(c[0][0], c[0][1]) for c in calls])
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
- **Reproducibility**: Uses deterministic inputs and mocks for OpenTelemetry and logging to ensure consistent results.
- **Security**: Validates that sensitive data (e.g., API keys in test files) is redacted during serialization.
- **Comprehensive Coverage**: Tests validation, default values, serialization, and schema versioning for `TaskPayload`, `TaskResult`, and `BatchTaskPayload`.
- **Isolation**: No external dependencies; relies solely on `pydantic` and `unittest`, with logging and OpenTelemetry mocked.

#### Test Cases
1. **TaskPayload Validation (`test_task_payload_validation`)**:
   - Verifies `TaskPayload` validates required fields (`test_files`, `code_files`, `output_path`) and applies defaults (`task_id`, `tags`, `environment`, `schema_version`).
   - Checks logging and tracing with `run_id`.

2. **TaskPayload Validation Failure (`test_task_payload_validation_failure`)**:
   - Tests that missing required fields raise `ValidationError`.
   - Verifies error logging and tracing.

3. **TaskPayload Serialization (`test_task_payload_serialization`)**:
   - Tests serialization with PII redaction (mocked via `redact_secrets`).
   - Verifies deserialization preserves data integrity.

4. **TaskResult Validation (`test_task_result_validation`)**:
   - Verifies `TaskResult` validates required fields (`task_id`, `status`) and defaults (`results`, `error`, `tags`, `environment`, `schema_version`).
   - Checks logging and tracing.

5. **TaskResult Validation Failure (`test_task_result_validation_failure`)**:
   - Tests that an invalid `status` raises `ValidationError`.
   - Verifies error logging and tracing.

6. **TaskResult with Error (`test_task_result_with_error`)**:
   - Tests handling of the `error` field in `TaskResult` for failed tasks.
   - Verifies serialization and logging.

7. **BatchTaskPayload Validation (`test_batch_task_payload_validation`)**:
   - Verifies `BatchTaskPayload` validates a list of `TaskPayload`s and `batch_id`.
   - Checks logging and tracing with `batch_id`.

8. **BatchTaskPayload Empty Tasks (`test_batch_task_payload_empty_tasks`)**:
   - Tests that an empty `tasks` list raises `ValidationError`.
   - Verifies error logging.

9. **BatchTaskPayload Serialization (`test_batch_task_payload_serialization`)**:
   - Tests serialization and deserialization of `BatchTaskPayload` with nested tasks.
   - Verifies data integrity and logging.

10. **Schema Version Mismatch (`test_schema_version_mismatch`)**:
    - Tests that an invalid `schema_version` raises `ValidationError`.
    - Verifies error logging and tracing.

11. **Traceability (`test_traceability`)**:
    - Ensures all validations are traced with `run_id` and OpenTelemetry attributes (`model`, `task_id`, `batch_id`).

#### Regulatory Features
- **Traceability**: Each test logs with a `run_id` and mocked OpenTelemetry spans for audit trails.
- **Security**: Validates PII redaction during serialization (e.g., API keys replaced with `[REDACTED]`).
- **Auditability**: Structured logs include metadata (`run_id`, `provenance_hash`, `model`, `task_id`).
- **Reproducibility**: No external dependencies; tests are pure and deterministic.
- **Metrics**: Logs actions with `log_action` for traceability, though no direct metrics are used in `runner_contracts.py`.

#### Implementation Notes
- **Mocks**: Mocks OpenTelemetry and `runner.utils.redact_secrets` to isolate tests and simulate PII redaction.
- **Logging**: Integrates with `runner_logging.py` for structured logs and `LOG_HISTORY`.
- **Pydantic**: Relies on `pydantic` for model validation, ensuring compliance with the lightweight design of `runner_contracts.py`.
- **Isolation**: Clears `LOG_HISTORY` in `setUp` and `tearDown` to ensure test isolation.

### Running the Tests
1. Save the file in the `runner` directory (e.g., `D:\Code_Factory\Generator\runner\tests\test_runner_contracts.py`).
2. Install required test dependencies:
   ```bash
   pip install unittest pydantic
   ```
3. Run the tests:
   ```bash
   python -m unittest D:\Code_Factory\Generator\runner\tests\test_runner_contracts.py
   ```
4. For verbose output:
   ```bash
   python -m unittest D:\Code_Factory\Generator\runner\tests\test_runner_contracts.py -v
   ```

### Notes
- **Dependencies**: Requires only `unittest` and `pydantic`, aligning with the dependency-light design of `runner_contracts.py`.
- **Scope**: Covers all models (`TaskPayload`, `TaskResult`, `BatchTaskPayload`) and their validation, serialization, and error handling.
- **Regulatory Compliance**: Designed for auditability with traceability, PII redaction, and structured error handling.
- **Future Enhancements**: If E2E tests are needed later, we can integrate with `runner_core.py` to test task execution with these models.
- **Proprietary Nature**: The test suite is for internal use by Unexpected Innovations Inc., aligning with the proprietary license.

If you need additional test cases, tests for specific scenarios, or clarification on any aspect, please let me know! I can also provide a combined test suite for multiple `runner` modules or proceed with E2E tests if needed later.