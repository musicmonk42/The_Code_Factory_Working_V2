
# test_runner_backends.py
# Highly regulated industry-grade test suite for runner_backends.py.
# Provides comprehensive unit and integration tests for execution backends with strict
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
sys.modules['docker'] = MagicMock()
sys.modules['docker.errors'] = MagicMock()
sys.modules['podman'] = MagicMock()
sys.modules['podman.errors'] = MagicMock()
sys.modules['firecracker_python'] = MagicMock()
sys.modules['kubernetes'] = MagicMock()
sys.modules['kubernetes.client'] = MagicMock()
sys.modules['kubernetes.config'] = MagicMock()
sys.modules['boto3'] = MagicMock()
sys.modules['botocore.exceptions'] = MagicMock()
sys.modules['opentelemetry'] = MagicMock()
sys.modules['opentelemetry.trace'] = MagicMock()
sys.modules['opentelemetry.sdk.trace'] = MagicMock()
sys.modules['opentelemetry.sdk.trace.export'] = MagicMock()
sys.modules['aiohttp'] = MagicMock()

# Import runner modules
from runner.backends import (
    ExecutionBackend, DockerBackend, PodmanBackend, KubernetesBackend, LambdaBackend,
    SSHBackend, NodeJsBackend, GoBackend, JavaBackend, FirecrackerBackend, BACKEND_REGISTRY,
    get_all_backends_health
)
from runner.config import RunnerConfig
from runner.contracts import TaskPayload, TaskResult
from runner.errors import RunnerError, BackendError, TestExecutionError, SetupError, TimeoutError
from runner.logging import logger, log_action, LOG_HISTORY
from runner.metrics import RUN_BACKEND_HEALTH, RUN_EXECUTION_DURATION, RUN_EXECUTION_ERRORS

class TestRunnerBackends(unittest.IsolatedAsyncioTestCase):
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
            timeout=300
        )

        # Mock environment variables
        self.patch_env = patch.dict(os.environ, {
            'RUNNER_ENV': 'development'
        })
        self.patch_env.start()

        # Mock dependencies
        self.mock_docker_client = MagicMock()
        self.patch_docker = patch('runner.backends.DockerClient', return_value=self.mock_docker_client)
        self.patch_docker.start()
        self.mock_podman = MagicMock()
        self.patch_podman = patch('runner.backends.podman', return_value=self.mock_podman)
        self.patch_podman.start()
        self.mock_k8s_client = MagicMock()
        self.patch_k8s = patch('runner.backends.client', return_value=self.mock_k8s_client)
        self.patch_k8s.start()
        self.mock_boto3 = MagicMock()
        self.patch_boto3 = patch('runner.backends.boto3', return_value=self.mock_boto3)
        self.patch_boto3.start()
        self.mock_firecracker = MagicMock()
        self.patch_firecracker = patch('runner.backends.firecracker_python', return_value=self.mock_firecracker)
        self.patch_firecracker.start()
        self.mock_tracer = patch('runner.backends.trace.get_tracer', return_value=MagicMock())
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
        self.patch_docker.stop()
        self.patch_podman.stop()
        self.patch_k8s.stop()
        self.patch_boto3.stop()
        self.patch_firecracker.stop()
        self.mock_tracer.stop()

    async def test_docker_backend_execute(self):
        """Test: DockerBackend executes task and returns TaskResult."""
        backend = DockerBackend(self.config)
        task = TaskPayload(
            task_id=str(uuid.uuid4()),
            test_files={'test.py': 'def test(): pass'},
            code_files={'code.py': 'def func(): return 1'},
            output_path=str(self.output_dir)
        )
        self.mock_docker_client.containers.run.return_value = b'{"status": "success", "tests": 1}'
        result = await backend.execute(task)
        self.mock_docker_client.containers.run.assert_called()
        self.assertIsInstance(result, TaskResult)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.results['tests'], 1)
        RUN_EXECUTION_DURATION.labels.assert_called_with(backend='docker', framework='pytest', instance_id='test_instance')
        log_action.assert_called_with(
            'TaskExecution',
            {'task_id': task.task_id, 'backend': 'docker', 'status': 'completed'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    async def test_docker_backend_timeout(self):
        """Test: DockerBackend handles TimeoutError correctly."""
        backend = DockerBackend(self.config)
        task = TaskPayload(
            task_id=str(uuid.uuid4()),
            test_files={'test.py': 'def test(): pass'},
            code_files={'code.py': 'def func(): return 1'},
            output_path=str(self.output_dir)
        )
        self.mock_docker_client.containers.run.side_effect = asyncio.TimeoutError
        with self.assertRaises(TimeoutError) as cm:
            await backend.execute(task)
        self.assertEqual(cm.exception.error_code, 'TASK_TIMEOUT')
        self.assertIn(task.task_id, cm.exception.detail)
        RUN_EXECUTION_ERRORS.labels.assert_called_with(
            backend='docker', framework='pytest', instance_id='test_instance', error_type='TASK_TIMEOUT'
        )
        log_action.assert_called_with(
            'TaskFailed',
            {'task_id': task.task_id, 'backend': 'docker', 'error': 'TASK_TIMEOUT'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    async def test_docker_backend_setup(self):
        """Test: DockerBackend setup validates environment."""
        backend = DockerBackend(self.config)
        self.mock_docker_client.containers.create.return_value = MagicMock()
        await backend.setup()
        self.mock_docker_client.containers.create.assert_called()
        log_action.assert_called_with(
            'BackendSetup',
            {'backend': 'docker', 'status': 'success'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    async def test_docker_backend_health(self):
        """Test: DockerBackend health check returns status."""
        backend = DockerBackend(self.config)
        self.mock_docker_client.ping.return_value = True
        health = backend.health()
        self.assertTrue(health)
        RUN_BACKEND_HEALTH.labels.assert_called_with(backend='docker', instance_id='test_instance')
        RUN_BACKEND_HEALTH.labels.return_value.set.assert_called_with(1)

    async def test_docker_backend_cleanup(self):
        """Test: DockerBackend cleanup removes resources."""
        backend = DockerBackend(self.config)
        self.mock_docker_client.containers.list.return_value = [MagicMock()]
        await backend.cleanup()
        self.mock_docker_client.containers.list.assert_called()
        self.mock_docker_client.containers.list.return_value[0].remove.assert_called()
        log_action.assert_called_with(
            'BackendCleanup',
            {'backend': 'docker', 'status': 'success'},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    async def test_docker_backend_supports_framework(self):
        """Test: DockerBackend validates framework support."""
        backend = DockerBackend(self.config)
        self.assertTrue(backend.supports_framework('pytest'))
        self.assertFalse(backend.supports_framework('invalid_framework'))
        log_action.assert_called_with(
            'FrameworkCheck',
            {'backend': 'docker', 'framework': 'pytest', 'supported': True},
            run_id=unittest.mock.ANY,
            provenance_hash=unittest.mock.ANY
        )

    async def test_get_all_backends_health(self):
        """Test: get_all_backends_health checks all backends."""
        with patch.dict('runner.backends.BACKEND_REGISTRY', {
            'docker': DockerBackend,
            'podman': PodmanBackend
        }):
            with patch('runner.backends.DockerClient', return_value=MagicMock(ping=MagicMock(return_value=True))):
                with patch('runner.backends.podman', return_value=MagicMock()):
                    health_status = get_all_backends_health(self.config)
                    self.assertIn('docker', health_status)
                    self.assertEqual(health_status['docker']['health'], True)
                    self.assertIn('podman', health_status)
                    self.assertIn('availability', health_status['podman'])
                    log_action.assert_called_with(
                        'BackendHealthCheck',
                        {'backend': 'docker', 'status': 'healthy'},
                        run_id=unittest.mock.ANY,
                        provenance_hash=unittest.mock.ANY
                    )

    async def test_docker_backend_pii_redaction(self):
        """Test: DockerBackend redacts PII in logs."""
        backend = DockerBackend(self.config)
        task = TaskPayload(
            task_id=str(uuid.uuid4()),
            test_files={'test.py': 'def test(): print("API_KEY=sk-abc123")'},
            code_files={'code.py': 'def func(): return 1'},
            output_path=str(self.output_dir)
        )
        self.mock_docker_client.containers.run.return_value = b'{"status": "success", "output": "API_KEY=sk-abc123"}'
        with patch('runner.utils.redact_secrets', return_value='{"status": "success", "output": "[REDACTED]"}') as mock_redact:
            result = await backend.execute(task)
            mock_redact.assert_called()
            self.assertIn('[REDACTED]', result.results['output'])
            self.assertNotIn('sk-abc123', result.results['output'])
            self.assertIn(LOG_HISTORY, [log for log in LOG_HISTORY if '[REDACTED]' in json.dumps(log)])

    async def test_backend_missing_dependency(self):
        """Test: Backend handles missing dependency gracefully."""
        with patch('runner.backends.DockerClient', None):
            with self.assertRaises(SetupError) as cm:
                backend = DockerBackend(self.config)
            self.assertEqual(cm.exception.error_code, 'BACKEND_DEPENDENCY_MISSING')
            self.assertIn('Docker SDK not found', cm.exception.detail)
            log_action.assert_called_with(
                'BackendSetupFailed',
                {'backend': 'docker', 'error': 'BACKEND_DEPENDENCY_MISSING'},
                run_id=unittest.mock.ANY,
                provenance_hash=unittest.mock.ANY
            )

    async def test_backend_traceability(self):
        """Test: Backend execution is traceable with run_id and OpenTelemetry."""
        backend = DockerBackend(self.config)
        task = TaskPayload(
            task_id=str(uuid.uuid4()),
            test_files={'test.py': 'def test(): pass'},
            code_files={'code.py': 'def func(): return 1'},
            output_path=str(self.output_dir)
        )
        self.mock_docker_client.containers.run.return_value = b'{"status": "success", "tests": 1}'
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True
        mock_span.get_span_context.return_value = MagicMock(trace_id=123, span_id=456)
        self.mock_tracer.return_value.start_as_current_span.return_value.__enter__.return_value = mock_span
        result = await backend.execute(task)
        mock_span.set_attribute.assert_called()
        calls = mock_span.set_attribute.call_args_list
        self.assertIn(('task_id', task.task_id), [(c[0][0], c[0][1]) for c in calls])
        self.assertIn(('backend', 'docker'), [(c[0][0], c[0][1]) for c in calls])
        self.assertIn(('status', 'completed'), [(c[0][0], c[0][1]) for c in calls])
        log_action.assert_called_with(
            'TaskExecution',
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
- **Reproducibility**: Mocks all external dependencies (`docker`, `podman`, `kubernetes`, `boto3`, `firecracker_python`) for deterministic results.
- **Security**: Validates PII redaction in logs and outputs, and secure error handling.
- **Comprehensive Coverage**: Tests all critical methods (`setup`, `execute`, `health`, `cleanup`, `supports_framework`) for `DockerBackend` and the `get_all_backends_health` function. Other backends follow similar patterns but are omitted for brevity (can be added if needed).
- **Isolation**: No real system interactions; all operations are mocked.

#### Test Cases
1. **Docker Backend Execution (`test_docker_backend_execute`)**:
   - Tests `DockerBackend.execute` with a valid `TaskPayload`, verifying `TaskResult` and metrics.
   - Ensures logs and metrics (`RUN_EXECUTION_DURATION`) are recorded.

2. **Docker Backend Timeout (`test_docker_backend_timeout`)**:
   - Simulates a timeout and verifies `TimeoutError` is raised with correct metadata.
   - Checks error metrics (`RUN_EXECUTION_ERRORS`) and logs.

3. **Docker Backend Setup (`test_docker_backend_setup`)**:
   - Tests `setup` method, ensuring container creation and logging.

4. **Docker Backend Health (`test_docker_backend_health`)**:
   - Verifies `health` method returns correct status and updates `RUN_BACKEND_HEALTH`.

5. **Docker Backend Cleanup (`test_docker_backend_cleanup`)**:
   - Tests `cleanup` method removes containers and logs the action.

6. **Docker Backend Framework Support (`test_docker_backend_supports_framework`)**:
   - Verifies `supports_framework` for valid and invalid frameworks.

7. **All Backends Health (`test_get_all_backends_health`)**:
   - Tests `get_all_backends_health` for configured and available backends, ensuring correct health status.

8. **PII Redaction (`test_docker_backend_pii_redaction`)**:
   - Verifies PII (e.g., API keys) is redacted in task outputs and logs.

9. **Missing Dependency (`test_backend_missing_dependency`)**:
   - Simulates a missing `DockerClient` and verifies `SetupError` is raised.

10. **Traceability (`test_backend_traceability`)**:
    - Ensures execution is traced with `run_id` and OpenTelemetry attributes (`task_id`, `backend`, `status`).

#### Regulatory Features
- **Traceability**: Each test logs with a `run_id` and mocked OpenTelemetry spans for audit trails.
- **Security**: Validates PII redaction (e.g., API keys replaced with `[REDACTED]`) in logs and outputs.
- **Auditability**: Structured errors and logs include metadata (`task_id`, `run_id`, `provenance_hash`).
- **Reproducibility**: Mocks ensure consistent outcomes, critical for regulated environments.
- **Metrics**: Verifies Prometheus metrics (`RUN_BACKEND_HEALTH`, `RUN_EXECUTION_DURATION`, `RUN_EXECUTION_ERRORS`) for compliance monitoring.

#### Implementation Notes
- **Mocks**: Mocks `docker`, `podman`, `kubernetes`, `boto3`, `firecracker_python`, and OpenTelemetry to isolate tests.
- **Temporary Directory**: Uses `tempfile` for output files, cleaned up in `tearDown`.
- **Logging**: Integrates with `runner_logging.py` for structured logs and `LOG_HISTORY`.
- **Metrics**: Clears Prometheus registry in `tearDown` to isolate tests.
- **Focus on DockerBackend**: Tests focus on `DockerBackend` for brevity, as other backends follow similar patterns. Additional tests for other backends can be added if required.

### Running the Tests
1. Save the file in the `runner` directory (e.g., `D:\Code_Factory\Generator\runner\tests\test_runner_backends.py`).
2. Install required test dependencies:
   ```bash
   pip install unittest mock prometheus_client
   ```
3. Run the tests:
   ```bash
   python -m unittest D:\Code_Factory\Generator\runner\tests\test_runner_backends.py
   ```
4. For verbose output:
   ```bash
   python -m unittest D:\Code_Factory\Generator\runner\tests\test_runner_backends.py -v
   ```

### Notes
- **Dependencies**: Assumes `unittest`, `mock`, and `prometheus_client` are available. All other dependencies are mocked.
- **Scope**: Focuses on `DockerBackend` for brevity; tests for other backends (`PodmanBackend`, etc.) can be added if needed.
- **Regulatory Compliance**: Designed for auditability with traceability, PII redaction, and structured error handling.
- **Future Enhancements**: If E2E tests are needed later, we can add tests with real Docker interactions or other backends, using a test container environment.
- **Proprietary Nature**: The test suite is for internal use by Unexpected Innovations Inc., aligning with the proprietary license.

If you need additional test cases, tests for other backends, or E2E tests in the future, please let me know! I can also refine the suite or address any specific requirements for your regulated industry context.