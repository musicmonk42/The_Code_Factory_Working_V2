import pytest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

# Updated for renamed GenerationOrchestrator to avoid pytest collection
from test_generation.orchestrator.orchestrator import GenerationOrchestrator
from test_generation.orchestrator.cli import main as cli_main
from test_generation.orchestrator.config import CONFIG
from test_generation.orchestrator import (
    sanitize_path,
)  # sanitize_path from orchestrator init re-export.
from test_generation.utils import (
    SecurityScanner,
    PRCreator,
    MutationTester,
)
from test_generation.policy_and_audit import PolicyEngine
import json
import argparse


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    # Fix: Use parents=True and exist_ok=True to prevent FileExistsError.
    # The redundant call to mkdir for atco_artifacts has been removed.
    (root / "atco_artifacts/generated").mkdir(parents=True, exist_ok=True)
    (root / "atco_artifacts/quarantined_tests").mkdir(parents=True, exist_ok=True)
    (root / "atco_artifacts/sarif_reports").mkdir(parents=True, exist_ok=True)
    (root / "atco_artifacts/coverage_reports").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def config(project: Path):
    config = dict(CONFIG)
    config.update(
        {
            "max_parallel_generation": 1,
            "python_venv_deps": ["pytest"],
            "mutation_testing": {"enabled": False},
        }
    )
    config_path = project / "atco_config.json"
    config_path.write_text(json.dumps(config))
    return config


@pytest.mark.asyncio
async def test_e2e_happy_and_quarantine_paths(project: Path, config, monkeypatch):
    # Mock dependencies
    mock_policy = Mock(
        spec=PolicyEngine,
        should_integrate_test=AsyncMock(return_value=(True, "Allowed")),
        requires_pr_for_integration=AsyncMock(return_value=(False, "No PR")),
    )
    mock_scanner = Mock(
        spec=SecurityScanner, scan_test_file=AsyncMock(return_value=(False, [], "NONE"))
    )
    mock_pr = Mock(spec=PRCreator, create_pr=AsyncMock(return_value=(True, "http://pr")))
    mock_tester = Mock(
        spec=MutationTester,
        run_mutations=AsyncMock(return_value=(True, 80.0, "Passed")),
    )

    # Happy path: successful integration
    # Mock the function before the orchestrator is created and calls it
    mock_run_pytest = AsyncMock(return_value=(True, 10.0, "Passed"))
    monkeypatch.setattr("test_generation.utils.run_pytest_and_coverage", mock_run_pytest)

    # We must also mock venv creation, which is a dependency of run_pytest
    monkeypatch.setattr(
        "test_generation.utils.create_and_install_venv",
        AsyncMock(return_value=(True, "path/to/python")),
    )

    orchestrator = GenerationOrchestrator(config, str(project), "tests")
    monkeypatch.setattr(orchestrator, "policy_engine", mock_policy)
    monkeypatch.setattr(orchestrator, "security_scanner", mock_scanner)
    monkeypatch.setattr(orchestrator, "pr_creator", mock_pr)
    monkeypatch.setattr(orchestrator, "mutation_tester", mock_tester)

    gen_summary = {
        "module1": {
            "generation_success": True,
            "generated_test_path": "atco_artifacts/generated/test_module1.py",
            "language": "python",
        }
    }
    test_path = sanitize_path("atco_artifacts/generated/test_module1.py", str(project))
    (project / test_path).write_text("def test_dummy(): assert True")
    result = await orchestrator.integrate_and_validate_generated_tests(gen_summary)
    assert result["summary"]["total_integrated"] == 1
    assert result["details"]["module1"]["integration_status"] == "INTEGRATED"
    assert (project / "tests/test_module1.py").exists()
    assert (project / "atco_artifacts/sarif_reports").exists()

    # Quarantine path: failing test
    # Reset the mock for this part of the test
    mock_run_pytest.return_value = (False, 0.0, "Failed")
    result = await orchestrator.integrate_and_validate_generated_tests(gen_summary)
    assert result["summary"]["total_quarantined"] == 1
    assert result["details"]["module1"]["integration_status"] == "QUARANTINED"
    assert (project / "atco_artifacts/quarantined_tests").exists()

    # Verify audit logs
    audit_log_path = project / sanitize_path("atco_artifacts/atco_audit.log", str(project))
    assert audit_log_path.exists()
    with audit_log_path.open("r") as f:
        logs = [json.loads(line) for line in f if line.strip()]
    assert any(log["event"] == "test_integrated" for log in logs)


@pytest.mark.asyncio
async def test_e2e_cli_main(project: Path, config, monkeypatch):
    # Mock monitor_and_prioritize_uncovered_code to return a target
    monkeypatch.setattr(
        "test_generation.utils.monitor_and_prioritize_uncovered_code",
        AsyncMock(
            return_value=[
                {
                    "identifier": "module1",
                    "language": "python",
                    "priority": 100,
                    "current_line_coverage": 0.0,
                }
            ]
        ),
    )

    # We must mock the run_pytest function to prevent it from trying to run
    monkeypatch.setattr(
        "test_generation.utils.run_pytest_and_coverage",
        AsyncMock(return_value=(True, 10.0, "Passed")),
    )

    monkeypatch.setattr(
        "test_generation.utils.SecurityScanner.scan_test_file",
        AsyncMock(return_value=(False, [], "NONE")),
    )
    # Standardize mock path for PRCreator
    monkeypatch.setattr(
        "test_generation.utils.PRCreator.create_pr",
        AsyncMock(return_value=(True, "http://pr")),
    )

    # Mock the GenerationOrchestrator's key methods to simplify the test
    mock_orchestrator = Mock(spec=GenerationOrchestrator)
    mock_orchestrator.run_pipeline = AsyncMock(return_value=None)

    with patch(
        "test_generation.orchestrator.cli.GenerationOrchestrator",
        return_value=mock_orchestrator,
    ):
        args = argparse.Namespace(
            project_root=str(project),
            config_file="atco_config.json",
            suite_dir="tests",
            coverage_xml="atco_artifacts/coverage_reports/coverage.xml",
        )

        # Create the config file for the cli to load
        (project / "atco_config.json").write_text(json.dumps(config))

        # Create a dummy coverage.xml file
        coverage_file = project / "atco_artifacts/coverage_reports/coverage.xml"
        coverage_file.parent.mkdir(parents=True, exist_ok=True)
        coverage_file.write_text("<coverage></coverage>")

        await cli_main(args)

    mock_orchestrator.run_pipeline.assert_called_once()
    assert mock_orchestrator.project_root == str(project)


# TDD guard test
def test_asyncmock_import_guard():
    from unittest.mock import AsyncMock

    assert AsyncMock is not None


def test_orchestrator_class_rename_exists():
    from test_generation.orchestrator.orchestrator import GenerationOrchestrator

    assert GenerationOrchestrator is not None


# TDD guard test
def test_policy_engine_import_guard():
    from test_generation.policy_and_audit import PolicyEngine

    assert PolicyEngine is not None


# Completed for syntactic validity.
def test_orchestrator_import():
    """
    Verifies that the GenerationOrchestrator class and its run_pipeline method
    are correctly imported and callable.
    """
    from test_generation.orchestrator.orchestrator import GenerationOrchestrator

    assert callable(getattr(GenerationOrchestrator, "run_pipeline", None))
