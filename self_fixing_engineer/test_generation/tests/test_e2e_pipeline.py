import pytest
import os
import json
import tempfile
from unittest.mock import patch, AsyncMock, MagicMock, mock_open
from test_generation.orchestrator.orchestrator import GenerationOrchestrator # Fix: Import from the correct module

@pytest.mark.asyncio
async def test_e2e_pipeline_full_success():
    """
    Full end-to-end pipeline success test for the orchestrator.
    This verifies that:
      - Coverage analysis is invoked
      - A backend is selected and used
      - Compliance reporting runs
      - HTML reporting runs
      - Audit logging occurs
      - The pipeline exits cleanly
    """

    with tempfile.TemporaryDirectory() as temp_project_root:
        # ----- Test fixtures -----
        uncovered_targets = [
            {"identifier": "my_module", "language": "python", "priority": 10}
        ]
        test_code = "def test_foo(): assert True"
        config = {
            "quarantine_dir": "atco_artifacts/quarantined_tests",
            "generated_output_dir": "atco_artifacts/generated",
            "sarif_export_dir": "atco_artifacts/sarif_reports",
            "audit_log_file": "atco_artifacts/atco_audit.log",
            "coverage_reports_dir": "atco_artifacts/coverage_reports",
            "suite_dir": "tests",
            "python_venv_deps": ["pytest", "pytest-cov"],
            "backend_timeouts": {"pynguin": 60},
            "test_exec_timeout_seconds": 30,
            "mutation_testing": {"enabled": False},
            "compliance_reporting": {"enabled": True}, # Fix: Enable compliance reporting for the test
        }

        # Write config and coverage XML
        os.makedirs(os.path.join(temp_project_root, "tests"), exist_ok=True)
        with open(os.path.join(temp_project_root, "atco_config.json"), "w") as f:
            json.dump(config, f)
        with open(os.path.join(temp_project_root, "coverage.xml"), "w") as f:
            f.write(
                "<coverage><packages><package><classes>"
                "<class filename='my_module.py' line-rate='0.0'/>"
                "</classes></package></packages></coverage>"
            )

        # ----- Patch external dependencies -----
        with patch(
            "test_generation.utils.monitor_and_prioritize_uncovered_code",
            AsyncMock(return_value=uncovered_targets)
        ) as mock_monitor, \
            patch(
                "test_generation.backends.BackendRegistry.get_backend"
            ) as mock_get_backend, \
            patch(
                "test_generation.orchestrator.venvs.create_and_install_venv", # Fix: Corrected mock path
                AsyncMock(return_value=(True, "/mock/venv/bin/python"))
            ) as mock_venv, \
            patch(
                "test_generation.orchestrator.orchestrator.run_pytest_and_coverage", # Fix: Corrected mock path
                AsyncMock(return_value=(True, 80.0, "SUCCESS"))
            ) as mock_pytest_cov, \
            patch(
                "test_generation.orchestrator.orchestrator.compare_files", # Fix: Corrected mock path
                return_value=False
            ) as mock_compare, \
            patch(
                "test_generation.orchestrator.orchestrator.backup_existing_test", # Fix: Corrected mock path
                AsyncMock(return_value="backup/path")
            ) as mock_backup, \
            patch(
                "test_generation.orchestrator.orchestrator.generate_file_hash", # Fix: Corrected mock path
                return_value="mock_hash"
            ) as mock_hash, \
            patch(
                "test_generation.utils.SecurityScanner.scan_test_file",
                AsyncMock(return_value=(False, [], "NONE"))
            ) as mock_scan, \
            patch(
                "test_generation.orchestrator.reporting.HTMLReporter.generate_html_report", # Fix: Corrected mock path
                new_callable=AsyncMock, # Fix: HTMLReporter is an async function
                return_value="sarif_reports/report.html"
            ) as mock_html_report, \
            patch(
                "test_generation.compliance_mapper.generate_report", # Fix: Corrected mock path
                return_value=MagicMock(issues=[], is_compliant=True) # Fix: Return a mock object for the report
            ) as mock_compliance, \
            patch(
                "test_generation.orchestrator.audit.AuditLogger" # Fix: Corrected mock path
            ) as mock_audit_logger_class, \
            patch(
                "builtins.open",
                mock_open(read_data=test_code)
            ) as mock_file, \
            patch(
                "os.path.exists",
                return_value=True
            ) as mock_exists:

            # Mock backend behavior
            mock_backend_instance = AsyncMock()
            mock_backend_instance.generate_tests = AsyncMock(
                return_value=(True, "", "output/my_module_test.py")
            )
            mock_get_backend.return_value = lambda *_a, **_kw: mock_backend_instance

            # Mock audit logger instance
            mock_audit_logger = AsyncMock()
            mock_audit_logger_class.return_value = mock_audit_logger

            # Import orchestrator main
            from test_generation.orchestrator.pipeline import main

            class Args:
                project_root = temp_project_root
                coverage_xml = "coverage.xml"
                config_file = "atco_config.json"
                suite_dir = "tests"
                max_parallel = 1
                dry_run = False

            # Run orchestrator
            with pytest.raises(SystemExit) as exit_info:
                await main(Args)
            assert exit_info.value.code == 0

            # ----- Assertions -----
            mock_monitor.assert_awaited_once()
            mock_get_backend.assert_called_once_with("python")
            mock_backend_instance.generate_tests.assert_awaited_once()
            mock_venv.assert_awaited_once()
            mock_pytest_cov.assert_awaited()
            mock_compare.assert_called()
            mock_backup.assert_awaited()
            mock_hash.assert_called()
            mock_scan.assert_awaited()
            mock_html_report.assert_called_once()
            mock_compliance.assert_called_once()
            assert mock_audit_logger.log_event.await_count > 0
            mock_file.assert_called()
            mock_exists.assert_called()

# Fix: The test file provided was not truncated. The mock_aiofiles hint was not relevant to this file.
# The following test is added as requested by the user.

def test_pipeline_import():
    """
    Tests that the GenerationOrchestrator and its run_pipeline method can be imported and are callable.
    This serves as a guard against import chain failures and ensures the public API is accessible.
    """
    assert GenerationOrchestrator is not None
    assert callable(getattr(GenerationOrchestrator, "run_pipeline", None))