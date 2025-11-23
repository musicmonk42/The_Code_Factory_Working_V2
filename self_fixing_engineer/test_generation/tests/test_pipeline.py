# test_generation/orchestrator/tests/test_pipeline.py
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from test_generation.orchestrator import sanitize_path
from test_generation.orchestrator.config import CONFIG
from test_generation.orchestrator.orchestrator import GenerationOrchestrator
from test_generation.policy_and_audit import PolicyEngine
from test_generation.utils import MutationTester, PRCreator, SecurityScanner


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "atco_artifacts/generated").mkdir(parents=True, exist_ok=True)
    (root / "atco_artifacts/quarantined_tests").mkdir(parents=True, exist_ok=True)
    (root / "atco_artifacts/sarif_reports").mkdir(parents=True, exist_ok=True)
    (root / "atco_artifacts/coverage_reports").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    return root


# Helper to create a mock async context manager
@asynccontextmanager
async def mock_aiofiles_open(*args, **kwargs):
    mock_file = Mock()
    mock_file.read = AsyncMock(return_value="def test_dummy(): pass")
    mock_file.write = AsyncMock()
    yield mock_file


@pytest.fixture
def orchestrator(project: Path, monkeypatch):
    config = dict(CONFIG)
    config.update(
        {
            "max_parallel_generation": 2,
            "python_venv_deps": ["pytest"],
            "mutation_testing": {"enabled": True, "min_score_for_integration": 50.0},
            "policy": {"min_coverage_gain_for_integration": 1.0},
        }
    )

    # Mock the components that GenerationOrchestrator tries to load
    mock_policy_engine = Mock(spec=PolicyEngine, policy_hash="mock_hash_123")
    mock_policy_engine.should_integrate_test = AsyncMock(return_value=(True, "Allowed"))
    mock_policy_engine.requires_pr_for_integration = AsyncMock(return_value=(False, "No PR"))

    mock_security_scanner = Mock(
        spec=SecurityScanner, scan_test_file=AsyncMock(return_value=(False, [], "NONE"))
    )
    mock_mutation_tester = Mock(
        spec=MutationTester,
        run_mutations=AsyncMock(return_value=(True, 80.0, "Passed")),
    )
    mock_pr_creator = Mock(
        spec=PRCreator,
        create_jira_ticket=AsyncMock(return_value=(True, "https://jira.com/ticket")),
        create_or_update_pr=AsyncMock(return_value=(True, "http://pr-url")),
    )

    def mock_load_component(self, component_name, config_key, **kwargs):
        if component_name == "policy_engine":
            return mock_policy_engine
        if component_name == "security_scanner":
            return mock_security_scanner
        if component_name == "mutation_tester":
            return mock_mutation_tester
        if component_name == "pr_creator":
            return mock_pr_creator
        return AsyncMock()

    monkeypatch.setattr(
        "test_generation.orchestrator.orchestrator.GenerationOrchestrator._load_component",
        mock_load_component,
    )

    @asynccontextmanager
    async def mock_temporary_env_context(*args, **kwargs):
        yield "/dummy/venv"

    monkeypatch.setattr(
        "test_generation.orchestrator.orchestrator.temporary_env",
        mock_temporary_env_context,
    )

    # FIX: Patch the awaited functions at the correct module level
    monkeypatch.setattr(
        "test_generation.orchestrator.orchestrator.run_pytest_and_coverage",
        AsyncMock(return_value=(True, 10.0, "Passed")),
    )
    monkeypatch.setattr("test_generation.orchestrator.orchestrator.shutil.move", Mock())
    monkeypatch.setattr("test_generation.orchestrator.orchestrator.os.makedirs", Mock())
    monkeypatch.setattr(
        "test_generation.orchestrator.orchestrator.aiofiles.open", mock_aiofiles_open
    )
    monkeypatch.setattr("test_generation.orchestrator.orchestrator.cleanup_path_safe", AsyncMock())
    monkeypatch.setattr(
        "test_generation.orchestrator.orchestrator._write_sarif_atomically", AsyncMock()
    )

    return GenerationOrchestrator(config, str(project), "tests")


@pytest.mark.asyncio
async def test_empty_targets(orchestrator, project: Path):
    result = await orchestrator.generate_tests_for_targets([], "atco_artifacts/generated")
    assert result == {}
    result = await orchestrator.integrate_and_validate_generated_tests({})
    assert result["summary"]["total_targets_considered"] == 0


@pytest.mark.asyncio
async def test_quarantine_on_failing_test(orchestrator, project: Path, monkeypatch):
    gen_summary = {
        "module1": {
            "generation_success": True,
            "generated_test_path": "atco_artifacts/generated/test_module1.py",
            "language": "python",
        }
    }
    test_path = sanitize_path("atco_artifacts/generated/test_module1.py", str(project))
    (project / test_path).write_text("def test_dummy(): assert True")
    monkeypatch.setattr(
        "test_generation.orchestrator.orchestrator.run_pytest_and_coverage",
        AsyncMock(return_value=(False, 0.0, "Test failed")),
    )
    result = await orchestrator.integrate_and_validate_generated_tests(gen_summary)
    assert result["summary"]["total_quarantined"] == 1
    assert result["details"]["module1"]["integration_status"] == "QUARANTINED"
    assert "Test failed during execution." in result["details"]["module1"]["reason"]


@pytest.mark.asyncio
async def test_quarantine_on_low_coverage(orchestrator, project: Path, monkeypatch):
    gen_summary = {
        "module1": {
            "generation_success": True,
            "generated_test_path": "atco_artifacts/generated/test_module1.py",
            "language": "python",
        }
    }
    test_path = sanitize_path("atco_artifacts/generated/test_module1.py", str(project))
    (project / test_path).write_text("def test_dummy(): assert True")
    monkeypatch.setattr(
        "test_generation.orchestrator.orchestrator.run_pytest_and_coverage",
        AsyncMock(return_value=(True, 0.0, "Passed")),
    )
    result = await orchestrator.integrate_and_validate_generated_tests(gen_summary)
    assert result["summary"]["total_quarantined"] == 1
    assert "Test generated insufficient coverage" in result["details"]["module1"]["reason"]


@pytest.mark.asyncio
async def test_quarantine_on_security_issues(orchestrator, project: Path, monkeypatch):
    gen_summary = {
        "module1": {
            "generation_success": True,
            "generated_test_path": "atco_artifacts/generated/test_module1.py",
            "language": "python",
        }
    }
    test_path = sanitize_path("atco_artifacts/generated/test_module1.py", str(project))
    (project / test_path).write_text("def test_dummy(): assert True")
    mock_scanner = Mock(
        spec=SecurityScanner,
        scan_test_file=AsyncMock(return_value=(True, [{"text": "Issue"}], "HIGH")),
    )
    monkeypatch.setattr(orchestrator, "security_scanner", mock_scanner)
    monkeypatch.setattr(
        "test_generation.orchestrator.orchestrator.run_pytest_and_coverage",
        AsyncMock(return_value=(True, 10.0, "Passed")),
    )
    result = await orchestrator.integrate_and_validate_generated_tests(gen_summary)
    assert result["summary"]["total_quarantined"] == 1
    assert "Security scan found issues" in result["details"]["module1"]["reason"]


@pytest.mark.asyncio
async def test_quarantine_on_low_mutation_score(orchestrator, project: Path, monkeypatch):
    gen_summary = {
        "module1": {
            "generation_success": True,
            "generated_test_path": "atco_artifacts/generated/test_module1.py",
            "language": "python",
        }
    }
    test_path = sanitize_path("atco_artifacts/generated/test_module1.py", str(project))
    (project / test_path).write_text("def test_dummy(): assert True")
    mock_tester = Mock(
        spec=MutationTester,
        run_mutations=AsyncMock(return_value=(True, 40.0, "Low score")),
    )
    monkeypatch.setattr(orchestrator, "mutation_tester", mock_tester)
    orchestrator.config["mutation_testing"] = {
        "enabled": True,
        "min_score_for_integration": 50.0,
    }
    monkeypatch.setattr(
        "test_generation.orchestrator.orchestrator.run_pytest_and_coverage",
        AsyncMock(return_value=(True, 10.0, "Passed")),
    )
    result = await orchestrator.integrate_and_validate_generated_tests(gen_summary)
    assert result["summary"]["total_quarantined"] == 1
    assert "Mutation score" in result["details"]["module1"]["reason"]


@pytest.mark.asyncio
async def test_pr_required_stages_file(orchestrator, project: Path, monkeypatch):
    gen_summary = {
        "module1": {
            "generation_success": True,
            "generated_test_path": "atco_artifacts/generated/test_module1.py",
            "language": "python",
        }
    }
    test_path = sanitize_path("atco_artifacts/generated/test_module1.py", str(project))
    (project / test_path).write_text("def test_dummy(): assert True")

    monkeypatch.setattr(
        orchestrator.policy_engine,
        "requires_pr_for_integration",
        AsyncMock(return_value=(True, "PR required")),
    )

    result = await orchestrator.integrate_and_validate_generated_tests(gen_summary)

    assert result["summary"]["total_requires_pr"] == 1
    assert result["details"]["module1"]["integration_status"] == "REQUIRES_PR"
