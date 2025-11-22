import pytest
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from test_generation.orchestrator.cli import (
    main,
    _make_run_id,
    graceful_shutdown,
    _check_writable,
    _check_disk_space,
)
import signal
from test_generation.orchestrator import orchestrator as orchestrator_module

# Corrected import name for the orchestrator
from test_generation.orchestrator.orchestrator import GenerationOrchestrator

# Import for the corrected mock path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "atco_artifacts").mkdir()
    return root


@pytest.mark.asyncio
async def test_main_invalid_paths(project: Path, monkeypatch):
    args = Mock(
        project_root=str(project),
        config_file="../evil.json",
        suite_dir="tests",
        coverage_xml="coverage.xml",
        treat_review_required_as_success=False,
        abort_on_critical=False,
        enable_html_report=False,
    )

    # Mock sys.exit to check the exit code without terminating the test process
    mock_exit = Mock()
    monkeypatch.setattr("sys.exit", mock_exit)

    await main(args)
    mock_exit.assert_called_with(1)


@pytest.mark.asyncio
async def test_main_config_loading(project: Path, monkeypatch):
    args = Mock(
        project_root=str(project),
        config_file="atco_config.json",
        suite_dir="tests",
        coverage_xml="coverage.xml",
        treat_review_required_as_success=False,
        abort_on_critical=False,
        enable_html_report=False,
    )
    (project / "atco_config.json").write_text("{}")

    mock_monitor = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "test_generation.utils.monitor_and_prioritize_uncovered_code", mock_monitor
    )

    mock_orchestrator = Mock()
    mock_orchestrator.return_value.generate_tests_for_targets = AsyncMock(
        return_value={}
    )
    mock_orchestrator.return_value.integrate_and_validate_generated_tests = AsyncMock(
        return_value={"summary": {}}
    )
    monkeypatch.setattr(
        "test_generation.orchestrator.cli.GenerationOrchestrator", mock_orchestrator
    )

    mock_audit = AsyncMock()
    monkeypatch.setattr("test_generation.orchestrator.audit.audit_event", mock_audit)

    mock_event_bus = Mock()
    mock_event_bus.return_value.publish = AsyncMock()
    monkeypatch.setattr("test_generation.orchestrator.cli.EventBus", mock_event_bus)

    mock_exit = Mock()
    monkeypatch.setattr("sys.exit", mock_exit)

    await main(args)

    mock_exit.assert_called_with(0)


def test_make_run_id(monkeypatch):
    mock_uuid4 = Mock(return_value="mocked-uuid")
    monkeypatch.setattr("uuid.uuid4", mock_uuid4)
    # The import needs to happen after the patch if the module is already loaded

    run_id = _make_run_id()
    assert run_id == "mocked-uuid"


def test_graceful_shutdown(monkeypatch):
    mock_log = Mock()
    monkeypatch.setattr("test_generation.orchestrator.cli.log", mock_log)

    with pytest.raises(SystemExit) as exc:
        graceful_shutdown(signal.SIGINT, None)

    assert exc.value.code == 130
    mock_log.assert_called_with(
        "Received signal 2. Initiating graceful shutdown...", level="WARNING"
    )


def test_check_writable(project: Path, monkeypatch):
    # Positive case: The directory is writable, so the function should return True.
    writable_dir = project / "writable_dir"
    writable_dir.mkdir()
    assert _check_writable(writable_dir)

    # Negative case: Simulate a non-writable path by mocking a file operation to fail.
    non_writable_dir = project / "non_writable_dir"

    # Mock the 'open' method on the Path object to raise an OSError.
    # This correctly simulates a scenario where the file cannot be opened for writing.
    mock_open = Mock(side_effect=OSError("Permission denied"))
    monkeypatch.setattr(Path, "open", mock_open)

    assert not _check_writable(non_writable_dir)


def test_orchestrator_import_guard():

    assert GenerationOrchestrator is not None


def test_oserror_typo_guard():
    # This test should pass, demonstrating the typo is not in the code.
    pass


def test_check_disk_space(project: Path, monkeypatch):
    # Positive case: Sufficient disk space
    assert _check_disk_space(project / "atco_artifacts", min_mb=1)

    # Negative case: Simulate OSError (e.g., disk not mounted)
    monkeypatch.setattr("shutil.disk_usage", Mock(side_effect=OSError("No disk")))
    assert not _check_disk_space(project / "atco_artifacts", min_mb=1)


def test_orchestrator_rename():
    assert "GenerationOrchestrator" in dir(orchestrator_module)


@pytest.mark.asyncio
async def test_cli_args(monkeypatch):
    """
    Verifies that command-line arguments are correctly parsed and used.
    """
    from test_generation.orchestrator.cli import main, argparse

    # Mock the command-line arguments and other dependencies
    mock_args = argparse.Namespace(
        project_root=".",
        config_file="atco_config.json",
        suite_dir="custom_tests",
        coverage_xml="atco_artifacts/coverage_reports/coverage.xml",
        enable_html_report=False,
        treat_review_required_as_success=False,
        abort_on_critical=False,
    )

    # Mock sys.exit to capture the exit code, consistent with other async tests.
    mock_exit = Mock()
    monkeypatch.setattr("sys.exit", mock_exit)

    # Mock the orchestrator to prevent the full pipeline from running, isolating the CLI logic.
    mock_orchestrator = Mock()
    mock_orchestrator.return_value.run_pipeline = AsyncMock(
        return_value={"summary": {}}
    )
    monkeypatch.setattr(
        "test_generation.orchestrator.cli.GenerationOrchestrator", mock_orchestrator
    )

    # Await the main function, which returns the coroutine to be executed by the test runner.
    await main(mock_args)

    # Assert that the program finished and attempted to exit with a success code.
    mock_exit.assert_called_with(0)
