
# test_runner_app.py
# Highly regulated industry-grade test suite for runner_app.py.
# Provides comprehensive unit and integration tests for the RunnerApp TUI with strict
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
from datetime import datetime, timezone
import logging

# Add parent directory to sys.path to import runner modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock external dependencies before importing runner modules
sys.modules['textual'] = MagicMock()
sys.modules['textual.app'] = MagicMock()
sys.modules['textual.widgets'] = MagicMock()
sys.modules['textual.containers'] = MagicMock()
sys.modules['textual.events'] = MagicMock()
sys.modules['textual.binding'] = MagicMock()
sys.modules['textual.css'] = MagicMock()
sys.modules['textual.worker'] = MagicMock()
sys.modules['textual.timer'] = MagicMock()
sys.modules['aiohttp'] = MagicMock()
sys.modules['opentelemetry'] = MagicMock()
sys.modules['opentelemetry.trace'] = MagicMock()
sys.modules['opentelemetry.sdk.trace'] = MagicMock()
sys.modules['opentelemetry.sdk.trace.export'] = MagicMock()

# Import runner modules
from runner.app import RunnerApp, TuiLogHandler
from runner.config import RunnerConfig, load_config, ConfigWatcher
from runner.core import Runner
from runner.contracts import TaskPayload, TaskResult
from runner.errors import RunnerError, TestExecutionError, TimeoutError
from runner.logging import logger, log_action, LOG_HISTORY
from runner.metrics import RUN_QUEUE, RUN_PASS_RATE, RUN_RESOURCE_USAGE, HEALTH_STATUS

class TestRunnerApp(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Create temporary directory for config and output files
        self.temp_dir = Path(tempfile.mkdtemp())
        self.config_file = self.temp_dir / "config.yaml"
        self.config_file.write_text("""
version: 4
backend: docker
framework: pytest
parallel_workers: 1
timeout: 300
mutation: false
fuzz: false
distributed: false
log_sinks:
  - type: stream
    config: {}
real_time_log_streaming: true
user_subscription_level: free
instance_id: test_instance
metrics_interval_seconds: 5
""")
        self.output_dir = self.temp_dir / "output/docs"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "project_doc.md").write_text("# Test Doc\nContent", encoding='utf-8')

        # Mock environment variables
        self.patch_env = patch.dict(os.environ, {
            'RUNNER_ENV': 'development',
            'RUNNER_CONFIG': str(self.config_file)
        })
        self.patch_env.start()

        # Mock textual widgets and app
        self.mock_app = MagicMock()
        self.mock_rich_log = MagicMock()
        self.mock_data_table = MagicMock()
        self.mock_progress_bar = MagicMock()
        self.mock_markdown = MagicMock()
        self.patch_textual_app = patch('runner.app.App', return_value=self.mock_app)
        self.patch_textual_app.start()
        self.patch_rich_log = patch('runner.app.RichLog', return_value=self.mock_rich_log)
        self.patch_rich_log.start()
        self.patch_data_table = patch('runner.app.DataTable', return_value=self.mock_data_table)
        self.patch_data_table.start()
        self.patch_progress_bar = patch('runner.app.ProgressBar', return_value=self.mock_progress_bar)
        self.patch_progress_bar.start()
        self.patch_markdown = patch('runner.app.Markdown', return_value=self.mock_markdown)
        self.patch_markdown.start()

        # Mock other dependencies
        self.mock_runner = MagicMock()
        self.patch_runner = patch('runner.core.Runner', return_value=self.mock_runner)
        self.patch_runner.start()
        self.mock_config_watcher = MagicMock()
        self.patch_config_watcher = patch('runner.config.ConfigWatcher', return_value=self.mock_config_watcher)
        self.patch_config_watcher.start()
        self.mock_aiohttp_session = patch('aiohttp.ClientSession', new_callable=AsyncMock)
        self.mock_aiohttp_session.start()
        self.mock_tracer = patch('runner.logging.trace.get_tracer', return_value=MagicMock())
        self.mock_tracer.start()

        # Configure logging
        logging.basicConfig(level=logging.INFO)
        self.run_id = str(uuid.uuid4())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.patch_env.stop()
        self.patch_textual_app.stop()
        self.patch_rich_log.stop()
        self.patch_data_table.stop()
        self.patch_progress_bar.stop()
        self.patch_markdown.stop()
        self.patch_runner.stop()
        self.patch_config_watcher.stop()
        self.mock_aiohttp_session.stop()
        self.mock_tracer.stop()
        for collector in list(REGISTRY._collectors.values()):
            REGISTRY.unregister(collector)

    async def test_tui_initialization(self):
        """Test: RunnerApp initializes correctly with config and logging."""
        with patch('runner.app.load_config', return_value=RunnerConfig(
            version=4, backend='docker', framework='pytest', instance_id='test_instance'
        )) as mock_load_config:
            app = RunnerApp(production_mode=False)
            self.assertIsNotNone(app.runner)
            self.assertIsNotNone(app.config_watcher)
            self.assertIsInstance(app.log_handler, TuiLogHandler)
            mock_load_config.assert_called_with(str(self.config_file))
            self.assertEqual(app.config.instance_id, 'test_instance')

    async def test_tui_log_handler(self):
        """Test: TuiLogHandler processes logs with PII redaction."""
        with patch('runner.app.load_config', return_value=RunnerConfig(
            version=4, backend='docker', framework='pytest', instance_id='test_instance',
            log_sinks=[{'type': 'stream', 'config': {}}], real_time_log_streaming=True
        )):
            app = RunnerApp(production_mode=False)
            log_handler = app.log_handler
            log_record = logging.LogRecord(
                name='test', level=logging.INFO, pathname='', lineno=0, msg='Sensitive: sk-abc123', args=(), exc_info=None
            )
            log_handler.emit(log_record)
            self.mock_rich_log.write.assert_called()
            call_args = self.mock_rich_log.write.call_args[0][0]
            self.assertIn('[REDACTED]', call_args)
            self.assertNotIn('sk-abc123', call_args)
            self.assertIn(LOG_HISTORY, [log for log in LOG_HISTORY if '[REDACTED]' in json.dumps(log)])

    async def test_task_submission(self):
        """Test: Task submission via TUI form enqueues task correctly."""
        with patch('runner.app.load_config', return_value=RunnerConfig(
            version=4, backend='docker', framework='pytest', instance_id='test_instance'
        )):
            app = RunnerApp(production_mode=False)
            task_payload = TaskPayload(
                task_id=str(uuid.uuid4()),
                test_files={'test.py': 'def test(): pass'},
                code_files={'code.py': 'def func(): return 1'},
                output_path=str(self.temp_dir / 'output')
            )
            self.mock_runner.enqueue.return_value = AsyncMock(return_value=TaskResult(
                task_id=task_payload.task_id, status='enqueued', tags=['test']
            ))
            result = await app._submit_task(task_payload)
            self.mock_runner.enqueue.assert_called_with(task_payload)
            self.assertEqual(result.status, 'enqueued')
            log_action.assert_called_with(
                'TaskEnqueued',
                {'task_id': task_payload.task_id, 'status': 'enqueued'},
                run_id=unittest.mock.ANY,
                provenance_hash=unittest.mock.ANY
            )
            RUN_QUEUE.labels.assert_called_with(framework='pytest', instance_id='test_instance')

    async def test_config_reload(self):
        """Test: ConfigWatcher triggers reload on config file change."""
        with patch('runner.app.load_config', return_value=RunnerConfig(
            version=4, backend='docker', framework='pytest', instance_id='test_instance'
        )) as mock_load_config:
            app = RunnerApp(production_mode=False)
            new_config = RunnerConfig(
                version=4, backend='podman', framework='unittest', instance_id='new_instance'
            )
            self.mock_config_watcher.notify = AsyncMock()
            await app._on_config_reload_callback(new_config)
            mock_load_config.assert_called_with(str(self.config_file))
            self.assertEqual(app.config.backend, 'podman')
            self.assertEqual(app.config.instance_id, 'new_instance')
            log_action.assert_called_with(
                'ConfigReloaded',
                {'new_backend': 'podman', 'new_instance_id': 'new_instance'},
                run_id=unittest.mock.ANY,
                provenance_hash=unittest.mock.ANY
            )

    async def test_metrics_display(self):
        """Test: Metrics are displayed correctly in TUI."""
        with patch('runner.app.load_config', return_value=RunnerConfig(
            version=4, backend='docker', framework='pytest', instance_id='test_instance',
            metrics_interval_seconds=5
        )):
            app = RunnerApp(production_mode=False)
            RUN_QUEUE.labels(framework='pytest', instance_id='test_instance').set(2)
            RUN_PASS_RATE.labels(framework='pytest', instance_id='test_instance').set(0.95)
            RUN_RESOURCE_USAGE.labels(resource_type='cpu', instance_id='test_instance').set(75.0)
            HEALTH_STATUS.labels(backend='docker', instance_id='test_instance').set(1)
            await app._update_metrics()
            self.mock_data_table.add_row.assert_called()
            call_args = self.mock_data_table.add_row.call_args[0]
            self.assertIn('RUN_QUEUE', call_args[0])
            self.assertEqual(call_args[1], 2)
            self.assertIn('RUN_PASS_RATE', call_args[0])
            self.assertEqual(call_args[3], 0.95)
            self.assertIn('RUN_RESOURCE_USAGE', call_args[0])
            self.assertEqual(call_args[5], 75.0)
            self.assertIn('HEALTH_STATUS', call_args[0])
            self.assertEqual(call_args[7], 1)

    async def test_error_handling(self):
        """Test: TUI handles RunnerError and displays structured error."""
        with patch('runner.app.load_config', return_value=RunnerConfig(
            version=4, backend='docker', framework='pytest', instance_id='test_instance'
        )):
            app = RunnerApp(production_mode=False)
            task_payload = TaskPayload(
                task_id=str(uuid.uuid4()),
                test_files={'test.py': 'def test(): pass'},
                code_files={'code.py': 'def func(): return 1'},
                output_path=str(self.temp_dir / 'output')
            )
            error = TestExecutionError(
                error_code='TEST_EXECUTION_FAILED',
                detail='Test failed',
                task_id=task_payload.task_id
            )
            self.mock_runner.enqueue.side_effect = error
            with self.assertRaises(TestExecutionError):
                await app._submit_task(task_payload)
            self.mock_rich_log.write.assert_called()
            call_args = self.mock_rich_log.write.call_args[0][0]
            self.assertIn('TEST_EXECUTION_FAILED', call_args)
            self.assertIn('Test failed', call_args)
            self.assertIn(task_payload.task_id, call_args)
            log_action.assert_called_with(
                'TaskFailed',
                {'task_id': task_payload.task_id, 'error': 'TEST_EXECUTION_FAILED'},
                run_id=unittest.mock.ANY,
                provenance_hash=unittest.mock.ANY
            )

    async def test_production_mode(self):
        """Test: Production mode disables debugging features."""
        with patch('runner.app.load_config', return_value=RunnerConfig(
            version=4, backend='docker', framework='pytest', instance_id='test_instance'
        )):
            with patch.dict(os.environ, {'RUNNER_ENV': 'production'}):
                app = RunnerApp(production_mode=True)
                self.assertTrue(app.production_mode)
                # In production mode, sensitive data logging should be minimal
                log_record = logging.LogRecord(
                    name='test', level=logging.INFO, pathname='', lineno=0, msg='API_KEY=sk-abc123', args=(), exc_info=None
                )
                app.log_handler.emit(log_record)
                call_args = self.mock_rich_log.write.call_args[0][0]
                self.assertIn('[REDACTED]', call_args)
                self.assertNotIn('sk-abc123', call_args)

    async def test_documentation_display(self):
        """Test: Documentation is loaded and displayed in TUI."""
        with patch('runner.app.load_config', return_value=RunnerConfig(
            version=4, backend='docker', framework='pytest', instance_id='test_instance'
        )):
            app = RunnerApp(production_mode=False)
            await app._load_documentation()
            self.mock_markdown.update.assert_called_with("# Test Doc\nContent")

    async def test_timeout_handling(self):
        """Test: TUI handles TimeoutError during task submission."""
        with patch('runner.app.load_config', return_value=RunnerConfig(
            version=4, backend='docker', framework='pytest', instance_id='test_instance'
        )):
            app = RunnerApp(production_mode=False)
            task_payload = TaskPayload(
                task_id=str(uuid.uuid4()),
                test_files={'test.py': 'def test(): pass'},
                code_files={'code.py': 'def func(): return 1'},
                output_path=str(self.temp_dir / 'output')
            )
            error = TimeoutError(
                error_code='TASK_TIMEOUT',
                detail='Task timed out',
                task_id=task_payload.task_id,
                timeout_seconds=300
            )
            self.mock_runner.enqueue.side_effect = error
            with self.assertRaises(TimeoutError):
                await app._submit_task(task_payload)
            self.mock_rich_log.write.assert_called()
            call_args = self.mock_rich_log.write.call_args[0][0]
            self.assertIn('TASK_TIMEOUT', call_args)
            self.assertIn('Task timed out', call_args)
            self.assertIn(str(300), call_args)

    async def test_traceability(self):
        """Test: All actions are traceable with run_id and OpenTelemetry."""
        with patch('runner.app.load_config', return_value=RunnerConfig(
            version=4, backend='docker', framework='pytest', instance_id='test_instance'
        )):
            mock_span = MagicMock()
            mock_span.is_recording.return_value = True
            mock_span.get_span_context.return_value = MagicMock(trace_id=123, span_id=456)
            self.mock_tracer.return_value.start_as_current_span.return_value.__enter__.return_value = mock_span
            app = RunnerApp(production_mode=False)
            task_payload = TaskPayload(
                task_id=str(uuid.uuid4()),
                test_files={'test.py': 'def test(): pass'},
                code_files={'code.py': 'def func(): return 1'},
                output_path=str(self.temp_dir / 'output')
            )
            self.mock_runner.enqueue.return_value = AsyncMock(return_value=TaskResult(
                task_id=task_payload.task_id, status='completed', results={'tests': 1}
            ))
            await app._submit_task(task_payload)
            mock_span.set_attribute.assert_called()
            calls = mock_span.set_attribute.call_args_list
            self.assertIn(('task_id', task_payload.task_id), [(c[0][0], c[0][1]) for c in calls])
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
