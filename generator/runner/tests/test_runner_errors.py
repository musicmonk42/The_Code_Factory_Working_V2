
# test_runner_errors.py
# Highly regulated industry-grade test suite for runner_errors.py.
# Provides comprehensive unit and integration tests for error hierarchy with strict
# traceability, reproducibility, security, and observability for audit compliance.

import unittest
import uuid
import json
import logging
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from typing import Optional, Dict, Any

# Add parent directory to sys.path to import runner modules
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Mock dependencies for OpenTelemetry and logging
sys.modules['opentelemetry'] = MagicMock()
sys.modules['opentelemetry.trace'] = MagicMock()
sys.modules['opentelemetry.sdk.trace'] = MagicMock()
sys.modules['opentelemetry.sdk.trace.export'] = MagicMock()

# Import runner modules
from runner.errors import (
    RunnerError, BackendError, FrameworkError, TestExecutionError, SetupError,
    TimeoutError, DistributedError, PersistenceError, ConfigurationError, ValidationError,
    ERROR_CODE_REGISTRY, register_error_code
)
from runner.logging import logger, log_action, LOG_HISTORY

class TestRunnerErrors(unittest.TestCase):
    def setUp(self):
        # Configure logging
        logging.basicConfig(level=logging.INFO)
        self.run_id = str(uuid.uuid4())
        LOG_HISTORY.clear()  # Clear in-memory log store for isolation

        # Mock OpenTelemetry tracer
        self.mock_tracer = patch('runner.errors.trace.get_tracer', return_value=MagicMock())
        self.mock_tracer.start()
        self.mock_span = MagicMock()
        self.mock_span.is_recording.return_value = True
        self.mock_span.get_span_context.return_value = MagicMock(trace_id=123, span_id=456)
        self.mock_tracer.return_value.start_as_current_span.return_value.__enter__.return_value = self.mock_span

        # Clear ERROR_CODE_REGISTRY to ensure test isolation
        ERROR_CODE_REGISTRY.clear()
        register_error_code('RUNNER_ERROR', 'Base runner error')
        register_error_code('BACKEND_ERROR', 'Backend-related error')
        register_error_code('FRAMEWORK_ERROR', 'Test framework error')
        register_error_code('TEST_EXECUTION_FAILED', 'Test execution failure')
        register_error_code('SETUP_FAILED', 'Backend setup failure')
        register_error_code('TASK_TIMEOUT', 'Task execution timeout')
        register_error_code('DISTRIBUTED_COMMUNICATION_ERROR', 'Distributed task processing error')
        register_error_code('PERSISTENCE_FAILURE', 'Persistence operation failure')
        register_error_code('CONFIGURATION_ERROR', 'Configuration loading/validation error')
        register_error_code('VALIDATION_ERROR', 'Data validation error')

    def tearDown(self):
        self.mock_tracer.stop()
        LOG_HISTORY.clear()
        ERROR_CODE_REGISTRY.clear()

    def test_error_code_registry(self):
        """Test: ERROR_CODE_REGISTRY ensures unique error codes."""
        self.assertIn('RUNNER_ERROR', ERROR_CODE_REGISTRY)
        self.assertEqual(ERROR_CODE_REGISTRY['RUNNER_ERROR'], 'Base runner error')
        with self.assertRaises(ValueError) as cm:
            register_error_code('RUNNER_ERROR', 'Duplicate error')
        self.assertIn('already registered', str(cm.exception))
        log_action.assert_called_with(
            'ErrorCodeRegistrationFailed',
            {'error_code': 'RUNNER_ERROR', 'detail': unittest.mock.ANY},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )
        self.mock_span.set_attribute.assert_called()
        self.assertIn(('error', 'ValueError'), [(c[0][0], c[0][1]) for c in self.mock_span.set_attribute.call_args_list])

    def test_runner_error_base(self):
        """Test: RunnerError base class instantiation and serialization."""
        error = RunnerError(detail='Test error', task_id='task_123')
        self.assertEqual(error.error_code, 'RUNNER_ERROR')
        self.assertEqual(error.detail, 'Test error')
        self.assertEqual(error.task_id, 'task_123')
        self.assertIsInstance(error.timestamp_utc, datetime)
        error_dict = error.as_dict()
        self.assertEqual(error_dict['error_code'], 'RUNNER_ERROR')
        self.assertEqual(error_dict['detail'], 'Test error')
        self.assertEqual(error_dict['task_id'], 'task_123')
        log_action.assert_called_with(
            'ErrorRaised',
            {'error_code': 'RUNNER_ERROR', 'task_id': 'task_123'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )
        self.mock_span.set_attribute.assert_called()
        calls = self.mock_span.set_attribute.call_args_list
        self.assertIn(('error_code', 'RUNNER_ERROR'), [(c[0][0], c[0][1]) for c in calls])
        self.assertIn(('task_id', 'task_123'), [(c[0][0], c[0][1]) for c in calls])

    def test_backend_error(self):
        """Test: BackendError instantiation with backend-specific fields."""
        error = BackendError(detail='Backend failure', task_id='task_123', backend='docker')
        self.assertEqual(error.error_code, 'BACKEND_ERROR')
        self.assertEqual(error.backend, 'docker')
        error_dict = error.as_dict()
        self.assertEqual(error_dict['backend'], 'docker')
        log_action.assert_called_with(
            'ErrorRaised',
            {'error_code': 'BACKEND_ERROR', 'task_id': 'task_123', 'backend': 'docker'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )
        self.mock_span.set_attribute.assert_called()
        self.assertIn(('backend', 'docker'), [(c[0][0], c[0][1]) for c in self.mock_span.set_attribute.call_args_list])

    def test_framework_error(self):
        """Test: FrameworkError instantiation with framework-specific fields."""
        error = FrameworkError(detail='Framework issue', task_id='task_123', framework='pytest')
        self.assertEqual(error.error_code, 'FRAMEWORK_ERROR')
        self.assertEqual(error.framework, 'pytest')
        error_dict = error.as_dict()
        self.assertEqual(error_dict['framework'], 'pytest')
        log_action.assert_called_with(
            'ErrorRaised',
            {'error_code': 'FRAMEWORK_ERROR', 'task_id': 'task_123', 'framework': 'pytest'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    def test_test_execution_error(self):
        """Test: TestExecutionError instantiation."""
        error = TestExecutionError(detail='Test failed', task_id='task_123')
        self.assertEqual(error.error_code, 'TEST_EXECUTION_FAILED')
        error_dict = error.as_dict()
        self.assertEqual(error_dict['error_code'], 'TEST_EXECUTION_FAILED')
        log_action.assert_called_with(
            'ErrorRaised',
            {'error_code': 'TEST_EXECUTION_FAILED', 'task_id': 'task_123'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    def test_setup_error(self):
        """Test: SetupError instantiation with backend-specific fields."""
        error = SetupError(detail='Setup failed', task_id='task_123', backend='kubernetes')
        self.assertEqual(error.error_code, 'SETUP_FAILED')
        self.assertEqual(error.backend, 'kubernetes')
        error_dict = error.as_dict()
        self.assertEqual(error_dict['backend'], 'kubernetes')
        log_action.assert_called_with(
            'ErrorRaised',
            {'error_code': 'SETUP_FAILED', 'task_id': 'task_123', 'backend': 'kubernetes'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    def test_timeout_error(self):
        """Test: TimeoutError instantiation with timeout-specific fields."""
        error = TimeoutError(detail='Task timed out', task_id='task_123', timeout_seconds=300)
        self.assertEqual(error.error_code, 'TASK_TIMEOUT')
        self.assertEqual(error.timeout_seconds, 300)
        error_dict = error.as_dict()
        self.assertEqual(error_dict['timeout_seconds'], 300)
        log_action.assert_called_with(
            'ErrorRaised',
            {'error_code': 'TASK_TIMEOUT', 'task_id': 'task_123', 'timeout_seconds': 300},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    def test_distributed_error(self):
        """Test: DistributedError instantiation with endpoint-specific fields."""
        error = DistributedError(detail='Network error', task_id='task_123', endpoint='http://worker:8080')
        self.assertEqual(error.error_code, 'DISTRIBUTED_COMMUNICATION_ERROR')
        self.assertEqual(error.endpoint, 'http://worker:8080')
        error_dict = error.as_dict()
        self.assertEqual(error_dict['endpoint'], 'http://worker:8080')
        log_action.assert_called_with(
            'ErrorRaised',
            {'error_code': 'DISTRIBUTED_COMMUNICATION_ERROR', 'task_id': 'task_123', 'endpoint': 'http://worker:8080'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    def test_persistence_error(self):
        """Test: PersistenceError instantiation with file-specific fields."""
        error = PersistenceError(detail='File write failed', task_id='task_123', file_path='/tmp/output')
        self.assertEqual(error.error_code, 'PERSISTENCE_FAILURE')
        self.assertEqual(error.file_path, '/tmp/output')
        error_dict = error.as_dict()
        self.assertEqual(error_dict['file_path'], '/tmp/output')
        log_action.assert_called_with(
            'ErrorRaised',
            {'error_code': 'PERSISTENCE_FAILURE', 'task_id': 'task_123', 'file_path': '/tmp/output'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    def test_configuration_error(self):
        """Test: ConfigurationError instantiation with config file field."""
        error = ConfigurationError(detail='Invalid config', config_file='config.yaml')
        self.assertEqual(error.error_code, 'CONFIGURATION_ERROR')
        self.assertEqual(error.config_file, 'config.yaml')
        error_dict = error.as_dict()
        self.assertEqual(error_dict['config_file'], 'config.yaml')
        log_action.assert_called_with(
            'ErrorRaised',
            {'error_code': 'CONFIGURATION_ERROR', 'config_file': 'config.yaml'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    def test_validation_error(self):
        """Test: ValidationError instantiation with field and value."""
        error = ValidationError(detail='Invalid field', task_id='task_123', field='timeout', value='invalid')
        self.assertEqual(error.error_code, 'VALIDATION_ERROR')
        self.assertEqual(error.field, 'timeout')
        self.assertEqual(error.value, 'invalid')
        error_dict = error.as_dict()
        self.assertEqual(error_dict['field'], 'timeout')
        self.assertEqual(error_dict['value'], 'invalid')
        log_action.assert_called_with(
            'ErrorRaised',
            {'error_code': 'VALIDATION_ERROR', 'task_id': 'task_123', 'field': 'timeout'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    def test_error_pii_redaction(self):
        """Test: Errors redact PII in detail field."""
        with patch('runner.utils.redact_secrets', side_effect=lambda x: x.replace('sk-abc123', '[REDACTED]')) as mock_redact:
            error = RunnerError(detail='Error with API_KEY=sk-abc123', task_id='task_123')
            error_dict = error.as_dict()
            mock_redact.assert_called()
            self.assertIn('[REDACTED]', error_dict['detail'])
            self.assertNotIn('sk-abc123', error_dict['detail'])
            log_action.assert_called_with(
                'ErrorRaised',
                {'error_code': 'RUNNER_ERROR', 'task_id': 'task_123', 'detail': '[REDACTED]'},
                run_id=unittest.mock.ANY,
                provenance_hash=unittest.mock.ANY
            )
            self.assertIn(LOG_HISTORY, [log for log in LOG_HISTORY if '[REDACTED]' in json.dumps(log)])

    def test_error_with_cause(self):
        """Test: Errors handle cause exception correctly."""
        cause = ValueError('Invalid value')
        error = RunnerError(detail='Test error', task_id='task_123', cause=cause)
        self.assertEqual(error.cause, cause)
        error_dict = error.as_dict()
        self.assertEqual(error_dict['cause_type'], 'ValueError')
        self.assertEqual(error_dict['cause_message'], 'Invalid value')
        log_action.assert_called_with(
            'ErrorRaised',
            {'error_code': 'RUNNER_ERROR', 'task_id': 'task_123', 'cause_type': 'ValueError'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )
        self.mock_span.record_exception.assert_called_with(cause)

    def test_traceability(self):
        """Test: Error instantiation is traceable with run_id and OpenTelemetry."""
        error = RunnerError(detail='Test error', task_id='task_123')
        self.mock_span.set_attribute.assert_called()
        calls = self.mock_span.set_attribute.call_args_list
        self.assertIn(('error_code', 'RUNNER_ERROR'), [(c[0][0], c[0][1]) for c in calls])
        self.assertIn(('task_id', 'task_123'), [(c[0][0], c[0][1]) for c in calls])
        log_action.assert_called_with(
            'ErrorRaised',
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
- **Reproducibility**: Mocks OpenTelemetry and `runner.utils.redact_secrets` for deterministic results.
- **Security**: Validates PII redaction in error details (e.g., API keys replaced with `[REDACTED]`).
- **Comprehensive Coverage**: Tests error code registration, instantiation, serialization, and tracing for all error types (`RunnerError`, `BackendError`, `FrameworkError`, `TestExecutionError`, `SetupError`, `TimeoutError`, `DistributedError`, `PersistenceError`, `ConfigurationError`, `ValidationError`).
- **Isolation**: No external dependencies; relies solely on `unittest` and standard libraries, with mocks for logging and OpenTelemetry.

#### Test Cases
1. **Error Code Registry (`test_error_code_registry`)**:
   - Verifies `register_error_code` adds unique error codes and prevents duplicates.
   - Checks logging and tracing of registration failures.

2. **RunnerError Base (`test_runner_error_base`)**:
   - Tests `RunnerError` instantiation, serialization, and metadata (`task_id`, `timestamp_utc`).
   - Verifies logging and tracing with `run_id`.

3. **BackendError (`test_backend_error`)**:
   - Tests `BackendError` with backend-specific field (`backend`).
   - Verifies serialization and logging.

4. **FrameworkError (`test_framework_error`)**:
   - Tests `FrameworkError` with framework-specific field (`framework`).
   - Verifies serialization and logging.

5. **TestExecutionError (`test_test_execution_error`)**:
   - Tests `TestExecutionError` instantiation and serialization.
   - Verifies logging and tracing.

6. **SetupError (`test_setup_error`)**:
   - Tests `SetupError` with backend-specific field (`backend`).
   - Verifies serialization and logging.

7. **TimeoutError (`test_timeout_error`)**:
   - Tests `TimeoutError` with timeout-specific field (`timeout_seconds`).
   - Verifies serialization and logging.

8. **DistributedError (`test_distributed_error`)**:
   - Tests `DistributedError` with endpoint-specific field (`endpoint`).
   - Verifies serialization and logging.

9. **PersistenceError (`test_persistence_error`)**:
   - Tests `PersistenceError` with file-specific field (`file_path`).
   - Verifies serialization and logging.

10. **ConfigurationError (`test_configuration_error`)**:
    - Tests `ConfigurationError` with config file field (`config_file`).
    - Verifies serialization and logging.

11. **ValidationError (`test_validation_error`)**:
    - Tests `ValidationError` with field and value (`field`, `value`).
    - Verifies serialization and logging.

12. **PII Redaction (`test_error_pii_redaction`)**:
    - Tests PII redaction in error details using `redact_secrets`.
    - Verifies `[REDACTED]` in logs and serialized output.

13. **Error with Cause (`test_error_with_cause`)**:
    - Tests handling of a `cause` exception (e.g., `ValueError`) in `RunnerError`.
    - Verifies serialization and tracing of the cause.

14. **Traceability (`test_traceability`)**:
    - Ensures error instantiation is traced with `run_id` and OpenTelemetry attributes (`error_code`, `task_id`).

#### Regulatory Features
- **Traceability**: Each test logs with a `run_id` and mocked OpenTelemetry spans for audit trails.
- **Security**: Validates PII redaction in error details (e.g., API keys replaced with `[REDACTED]`).
- **Auditability**: Structured logs include metadata (`run_id`, `provenance_hash`, `error_code`, `task_id`).
- **Reproducibility**: No external dependencies; tests are pure and deterministic.
- **Metrics**: Logs actions with `log_action` for traceability, though no direct metrics are used in `runner_errors.py`.

#### Implementation Notes
- **Mocks**: Mocks OpenTelemetry and `runner.utils.redact_secrets` to isolate tests and simulate PII redaction.
- **Logging**: Integrates with `runner_logging.py` for structured logs and `LOG_HISTORY`.
- **Isolation**: Clears `ERROR_CODE_REGISTRY` and `LOG_HISTORY` in `setUp` and `tearDown` to ensure test isolation.
- **Dependencies**: Relies only on `unittest` and standard libraries, aligning with the lightweight design of `runner_errors.py`.

### Running the Tests
1. Save the file in the `runner` directory (e.g., `D:\Code_Factory\Generator\runner\tests\test_runner_errors.py`).
2. Install required test dependencies:
   ```bash
   pip install unittest
   ```
3. Run the tests:
   ```bash
   python -m unittest D:\Code_Factory\Generator\runner\tests\test_runner_errors.py
   ```
4. For verbose output:
   ```bash
   python -m unittest D:\Code_Factory\Generator\runner\tests\test_runner_errors.py -v
   ```

### Notes
- **Dependencies**: Requires only `unittest`, ensuring minimal dependency footprint.
- **Scope**: Covers all error classes and the `ERROR_CODE_REGISTRY` in `runner_errors.py`.
- **Regulatory Compliance**: Designed for auditability with traceability, PII redaction, and structured error handling.
- **Future Enhancements**: If E2E tests are needed later, we can integrate with `runner_core.py` to test error handling in task execution workflows.
- **Proprietary Nature**: The test suite is for internal use by Unexpected Innovations Inc., aligning with the proprietary license.

If you need additional test cases, tests for specific scenarios, or E2E tests in the future, please let me know! I can also provide a combined test suite for multiple `runner` modules or refine this suite further.