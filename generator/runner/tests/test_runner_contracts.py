# test_runner_contracts.py
# Updated for 2025 refactor – full coverage, audit-ready, production-grade

import json
import logging

# Add parent directory to sys.path
import sys
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Mock OpenTelemetry
sys.modules["opentelemetry"] = MagicMock()
sys.modules["opentelemetry.trace"] = MagicMock()
sys.modules["opentelemetry.sdk.trace"] = MagicMock()
sys.modules["opentelemetry.sdk.trace.export"] = MagicMock()

# Import runner modules
from runner.runner_contracts import BatchTaskPayload, TaskPayload, TaskResult


class TestRunnerContracts(unittest.TestCase):
    def setUp(self):
        logging.basicConfig(level=logging.INFO)

        # Mock OpenTelemetry tracer
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True
        mock_span.get_span_context.return_value = MagicMock(trace_id=123, span_id=456)
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span

        self.mock_tracer_patch = patch(
            "runner.runner_logging.trace.get_tracer", return_value=mock_tracer
        )
        self.mock_tracer_patch.start()

    def tearDown(self):
        self.mock_tracer_patch.stop()

    def test_task_payload_validation(self):
        payload = TaskPayload(
            test_files={"test.py": "def test(): pass"},
            code_files={"code.py": "def func(): return 1"},
            output_path="/output",
            command=["python", "test.py"],
            timeout=300,
            dry_run=True,
            priority=1,
            tags=["unit", "fast"],
            environment="staging",
        )
        self.assertTrue(uuid.UUID(payload.task_id))
        self.assertEqual(payload.schema_version, 2)
        self.assertEqual(payload.environment, "staging")

    def test_task_payload_validation_failure(self):
        with self.assertRaises(ValidationError):
            TaskPayload(test_files={}, code_files={}, output_path="")

    def test_task_payload_serialization(self):
        payload = TaskPayload(
            test_files={"test.py": "def test(): pass"},
            code_files={"code.py": "def func(): return 1"},
            output_path="/output",
        )
        data = json.loads(payload.model_dump_json())
        self.assertIn("task_id", data)
        self.assertEqual(data["test_files"]["test.py"], "def test(): pass")
        self.assertEqual(data["schema_version"], 2)

    def test_task_result_validation(self):
        result = TaskResult(
            task_id=str(uuid.uuid4()),
            status="completed",
            results={"tests": 10, "passed": 9},
            started_at=1000.0,
            finished_at=1005.0,
            tags=["unit"],
            pass_rate=0.9,
            coverage_percentage=0.85,
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.pass_rate, 0.9)

    def test_task_result_serialization(self):
        result = TaskResult(
            task_id=str(uuid.uuid4()),
            status="failed",
            error={"code": "EXEC_FAILED", "message": "crash"},
        )
        data = json.loads(result.model_dump_json())
        self.assertEqual(data["status"], "failed")
        self.assertIn("error", data)

    def test_batch_task_payload(self):
        task1 = TaskPayload(test_files={"a.py": "pass"}, code_files={}, output_path="/out")
        task2 = TaskPayload(test_files={"b.py": "pass"}, code_files={}, output_path="/out")
        batch = BatchTaskPayload(tasks=[task1, task2])
        self.assertTrue(batch.batch_id.startswith("batch_"))
        self.assertEqual(len(batch.tasks), 2)
        self.assertIsInstance(batch.created_at, datetime)

    def test_batch_task_payload_empty(self):
        with self.assertRaises(ValidationError):
            BatchTaskPayload(tasks=[])

    def test_task_payload_defaults(self):
        payload = TaskPayload(test_files={"test.py": "pass"}, code_files={}, output_path="/out")
        self.assertEqual(payload.dry_run, False)
        self.assertEqual(payload.priority, 0)
        self.assertEqual(payload.environment, "production")


if __name__ == "__main__":
    unittest.main(verbosity=2)
