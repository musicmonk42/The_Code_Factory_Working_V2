# generator/runner/tests/test_runner_integration.py

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Import the configuration and contracts
from runner.runner_config import RunnerConfig
from runner.runner_contracts import TaskPayload, TaskResult

# --- Import Components Under Test ---
# Import the main orchestrator
from runner.runner_core import Runner

# Import the schemas for mocking parser returns
from runner.runner_parsers import CoverageReportSchema, ParserInfo, TestReportSchema

# Import the specific backend we will tell the Runner to use


class TestRunnerIntegration(unittest.IsolatedAsyncioTestCase):
    """
    Integration test for the core Runner orchestration.

    This test validates the complete flow of the `Runner.run_tests` method,
    ensuring it correctly interacts with the config, backend setup,
    subprocess execution, parsers, metrics, and logging.
    """

    def setUp(self):
        """
        Set up a full integration test environment with mocks at the
        system's "seams" (I/O, Subprocesses, Parsers).
        """
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.output_path = Path(self.tmp_dir.name) / "output"
        self.output_path.mkdir()

        # 1. --- Mock Configuration ---
        # We instantiate a *real* config object
        self.mock_config = RunnerConfig(
            version=4,
            backend="local",  # Tell the Runner to use LocalBackend
            framework="pytest",  # Tell the Runner to use the 'pytest' framework logic
            instance_id="test-integration-instance",
            log_sinks=[],
            real_time_log_streaming=False,
            timeout=300,
            parallel_workers=1,
            # (other fields will use their defaults)
        )

        # 2. --- Patch External Seams (I/O, Subprocess, Parsers) ---

        # Patch the file saver to avoid actual async I/O
        self.patch_save_files = patch(
            "runner.runner_core.Runner._save_files_to_temp_dir", new_callable=AsyncMock
        )
        self.mock_save_files = self.patch_save_files.start()

        # Patch the subprocess_wrapper. This is the ACTUAL execution seam
        # being used by runner_core.py.
        # [FIX] We must patch where the function is *used* (in runner_core),
        # not where it is *defined* (in process_utils).
        self.patch_subprocess = patch(
            "runner.runner_core.subprocess_wrapper", new_callable=AsyncMock
        )
        self.mock_subprocess_wrapper = self.patch_subprocess.start()

        # Patch the parsers that the 'pytest' framework logic calls
        self.patch_parse_junit = patch(
            "runner.runner_parsers.parse_junit_xml", new_callable=AsyncMock
        )
        self.mock_parse_junit = self.patch_parse_junit.start()

        self.patch_parse_coverage = patch(
            "runner.runner_parsers.parse_coverage_xml", new_callable=AsyncMock
        )
        self.mock_parse_coverage = self.patch_parse_coverage.start()

        # [FIX] Patch pathlib.Path.exists to "fake" the existence of
        # output files like 'coverage.xml' and 'results.xml'.
        # This is necessary because the mocked subprocess doesn't create them.
        self.patch_path_exists = patch("pathlib.Path.exists", return_value=True)
        self.mock_path_exists = self.patch_path_exists.start()

        # 3. --- Patch Observability (Metrics & Logging) ---

        # [FIX] Patch the metrics where they are *used* (in runner.runner_core)
        self.patch_metric_pass_rate = patch("runner.runner_core.RUN_PASS_RATE.set")
        self.mock_run_pass_rate = self.patch_metric_pass_rate.start()

        self.patch_metric_coverage = patch(
            "runner.runner_core.RUN_COVERAGE_PERCENT.set"
        )
        self.mock_run_coverage = self.patch_metric_coverage.start()

        self.patch_metric_task_status = patch("runner.runner_core.RUNNER_TASK_STATUS")
        self.mock_task_status = self.patch_metric_task_status.start()

        # Patch the audit logger to avoid crypto/external dependencies
        self.patch_log_audit = patch(
            "runner.runner_logging.log_audit_event", new_callable=AsyncMock
        )
        self.mock_log_audit_event = self.patch_log_audit.start()

        # 4. --- Patch Backend (for the diagnostic test) ---
        # This mock will be used in test_backend_abstraction_conflict
        self.patch_backend_execute = patch(
            "runner.runner_backends.LocalBackend.execute", new_callable=AsyncMock
        )
        self.mock_backend_execute = self.patch_backend_execute.start()

        # --- Instantiate the Runner ---
        # The Runner is instantiated *after* all patches are active.
        # We must also patch its async background services.
        with patch("asyncio.create_task") as mock_create_task:
            self.runner = Runner(self.mock_config)
            # We explicitly *don't* start services here, as we are only
            # testing the run_tests method.
            # await self.runner.start_services()

    def tearDown(self):
        """Clean up all patches and temporary directories."""
        self.tmp_dir.cleanup()
        self.patch_save_files.stop()
        self.patch_subprocess.stop()
        self.patch_parse_junit.stop()
        self.patch_parse_coverage.stop()
        self.patch_path_exists.stop()  # Stop the new patch
        self.patch_metric_pass_rate.stop()
        self.patch_metric_coverage.stop()
        self.patch_metric_task_status.stop()
        self.patch_log_audit.stop()
        self.patch_backend_execute.stop()

    async def test_full_successful_run(self):
        """
        Tests the entire `run_tests` orchestration flow for a successful run.
        It verifies that the Runner correctly:
        1. Calls to save files.
        2. Calls the (correct) subprocess wrapper with the pytest command.
        3. Calls the correct parsers.
        4. Calculates the final results.
        5. Updates all relevant metrics.
        6. Logs the audit event.
        """
        # --- 1. ARRANGE ---

        # Define mock returns for our patched seams
        self.mock_subprocess_wrapper.return_value = {
            "success": True,
            "stdout": "Subprocess success",
            "stderr": "",
            "returncode": 0,
            "duration": 1.23,
            "start_time": time.time(),
        }

        self.mock_parse_junit.return_value = TestReportSchema(
            total_tests=1,
            passed_tests=1,
            failed_tests=0,
            error_tests=0,
            skipped_tests=0,
            pass_rate=1.0,
            _parser_info=ParserInfo(parser_name="junit_xml", status="success"),
        )

        self.mock_parse_coverage.return_value = CoverageReportSchema(
            coverage_percentage=90.0,
            _parser_info=ParserInfo(parser_name="cobertura_xml", status="success"),
        )

        # Create the input payload. The content triggers 'pytest' detection
        # .
        mock_payload = TaskPayload(
            task_id="int-test-123",
            test_files={"test_foo.py": "def test_bar(): assert True"},
            code_files={"foo.py": "def bar(): return True"},
            output_path=str(self.output_path),
        )

        # --- 2. ACT ---
        result = await self.runner.run_tests(mock_payload)

        # --- 3. ASSERT ---

        # Check the final TaskResult
        self.assertIsInstance(result, TaskResult)
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.error)

        # Check aggregated results
        self.assertEqual(result.results["total_tests"], 1)
        self.assertEqual(result.results["pass_rate"], 1.0)
        self.assertEqual(result.results["coverage_percentage"], 90.0)

        # Check that the correct orchestration steps were called
        # The application correctly calls this twice (for code and for tests).
        self.assertEqual(self.mock_save_files.call_count, 2)

        # Check that the subprocess wrapper was called with the 'pytest' command
        # This confirms the framework detection logic worked.
        self.mock_subprocess_wrapper.assert_called_once()
        called_cmd = self.mock_subprocess_wrapper.call_args[0][0]
        self.assertEqual(
            called_cmd,
            ["pytest", "--junitxml=results.xml", "--cov", "--cov-report=xml:cov.xml"],
        )

        # Check that the correct parsers were called
        self.mock_parse_junit.assert_called_once()
        self.mock_parse_coverage.assert_called_once()

        # Check that metrics were updated
        # [FIX] The mock is the .set method itself, so we call it directly.
        self.mock_run_pass_rate.assert_called_with(1.0)
        self.mock_run_coverage.assert_called_with(90.0)

        # Check that status metrics were updated (running, completed)
        # This patch is on the object, so this assertion is correct.
        self.assertIn(
            unittest.mock.call("running"),
            self.mock_task_status.labels().inc.call_args_list,
        )
        self.assertIn(
            unittest.mock.call("completed"),
            self.mock_task_status.labels().inc.call_args_list,
        )

        # Check that the audit log was called
        self.mock_log_audit_event.assert_called()
        self.assertEqual(
            self.mock_log_audit_event.call_args[1]["action"], "TestRunCompleted"
        )

    async def test_backend_abstraction_conflict(self):
        """
        Diagnostic test to confirm Finding 1:
        The Runner.run_tests method bypasses self.backend.execute and
        calls process_utils.subprocess_wrapper directly.
        """
        # --- 1. ARRANGE ---
        # We use the same mocks as before, but this time we will also
        # check the call count of self.mock_backend_execute.

        self.mock_subprocess_wrapper.return_value = {
            "success": True,
            "stdout": "...",
            "stderr": "",
            "returncode": 0,
        }
        self.mock_parse_junit.return_value = TestReportSchema(
            total_tests=1,
            passed_tests=1,
            _parser_info=ParserInfo(parser_name="junit", status="success"),
        )
        self.mock_parse_coverage.return_value = CoverageReportSchema(
            coverage_percentage=90.0,
            _parser_info=ParserInfo(parser_name="coverage", status="success"),
        )

        mock_payload = TaskPayload(
            task_id="diag-test-456",
            test_files={"test_foo.py": "def test_bar(): pass"},
            code_files={"foo.py": "def bar(): pass"},
            output_path=str(self.output_path),
        )

        # --- 2. ACT ---
        await self.runner.run_tests(mock_payload)

        # --- 3. ASSERT ---

        # This is the key finding. We assert that:

        # 1. The *intended* abstraction (Backend.execute) was NOT called.
        self.mock_backend_execute.assert_not_called()

        # 2. The *actual* implementation (subprocess_wrapper) WAS called.
        self.mock_subprocess_wrapper.assert_called_once()

        print("\n--- Diagnostic Test 'test_backend_abstraction_conflict' PASSED ---")
        print("This confirms Finding 1: `Runner.run_tests` bypasses `Backend.execute`")
        print("and calls `runner.core.subprocess_wrapper` (which is now mocked).")
        print("-----------------------------------------------------------------")


if __name__ == "__main__":
    unittest.main()
