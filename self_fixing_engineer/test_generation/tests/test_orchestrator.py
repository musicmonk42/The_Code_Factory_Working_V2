# test_generation/orchestrator/tests/test_orchestrator.py
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from test_generation.orchestrator.orchestrator import GenerationOrchestrator, InitializationError
from test_generation.orchestrator.config import CONFIG
from test_generation.orchestrator import sanitize_path
from test_generation.orchestrator.console import log
from test_generation.orchestrator.audit import audit_event
from test_generation.orchestrator.metrics import generation_duration, integration_success
from test_generation.backends import BackendRegistry
from test_generation.policy_and_audit import PolicyEngine, EventBus
from test_generation.utils import SecurityScanner, PRCreator, MutationTester
from test_generation.compliance_mapper import generate_report
from contextlib import asynccontextmanager

@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "atco_artifacts/generated").mkdir(parents=True, exist_ok=True)
    (root / "atco_artifacts/quarantined_tests").mkdir(parents=True, exist_ok=True)
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
    """
    This fixture now correctly mocks the internal _load_component method
    on the GenerationOrchestrator class to prevent an AttributeError during initialization.
    All mocked components that are awaited are now AsyncMock objects.
    """
    config = dict(CONFIG)
    config.update({
        "max_parallel_generation": 2,
        "python_venv_deps": ["pytest"],
        "jira_integration": {"enabled": True},
        "mutation_testing": {"enabled": True},
        "compliance_reporting": {"enabled": True},
    })
    
    # Mock the components that GenerationOrchestrator tries to load
    mock_policy_engine = Mock(
        spec=PolicyEngine,
        should_integrate_test=AsyncMock(return_value=(True, "Allowed")),
        requires_pr_for_integration=AsyncMock(return_value=(False, "No PR")),
        policy_hash="mock_hash_123"
    )
    mock_event_bus = Mock(spec=EventBus, publish=AsyncMock())
    mock_security_scanner = Mock(spec=SecurityScanner, scan_test_file=AsyncMock(return_value=(False, [], "NONE")))
    mock_pr_creator = Mock(
        spec=PRCreator,
        create_jira_ticket=AsyncMock(return_value=(True, "https://jira.com/ticket")),
        create_or_update_pr=AsyncMock(return_value=(True, "http://pr-url"))
    )
    mock_mutation_tester = Mock(spec=MutationTester, run_mutations=AsyncMock(return_value=(True, 80.0, "Passed")))
    
    def mock_load_component(self, component_name, config_key, **kwargs):
        if component_name == 'policy_engine':
            return mock_policy_engine
        if component_name == 'event_bus':
            return mock_event_bus
        if component_name == 'security_scanner':
            return mock_security_scanner
        if component_name == 'pr_creator':
            return mock_pr_creator
        if component_name == 'mutation_tester':
            return mock_mutation_tester
        return Mock()
    
    monkeypatch.setattr(
        "test_generation.orchestrator.orchestrator.GenerationOrchestrator._load_component", 
        mock_load_component
    )
    
    monkeypatch.setattr("test_generation.orchestrator.orchestrator.aiofiles.open", mock_aiofiles_open)
    
    @asynccontextmanager
    async def mock_temporary_env_context(*args, **kwargs):
        yield '/dummy/venv'

    monkeypatch.setattr(
        "test_generation.orchestrator.orchestrator.temporary_env",
        mock_temporary_env_context
    )

    monkeypatch.setattr("shutil.move", Mock())
    monkeypatch.setattr("test_generation.orchestrator.orchestrator.shutil.move", Mock())
    monkeypatch.setattr("test_generation.orchestrator.orchestrator.os.path.getsize", Mock(return_value=1))

    monkeypatch.setattr("test_generation.orchestrator.orchestrator.cleanup_path_safe", AsyncMock())
    
    monkeypatch.setattr("test_generation.orchestrator.orchestrator.audit_event", AsyncMock())

    monkeypatch.setattr("test_generation.orchestrator.orchestrator.run_pytest_and_coverage", AsyncMock(return_value=(True, 10.0, "Passed")))
    monkeypatch.setattr("test_generation.orchestrator.orchestrator._write_sarif_atomically", AsyncMock(return_value=True))
    monkeypatch.setattr("test_generation.orchestrator.orchestrator.compare_files", Mock(return_value=True))

    # FIX: Patch the compliance report generation function to prevent it from executing real code
    monkeypatch.setattr("test_generation.orchestrator.orchestrator.generate_compliance_report", AsyncMock())
    
    return GenerationOrchestrator(config, str(project), "tests")

@pytest.mark.asyncio
async def test_generate_tests_with_concurrency(orchestrator, project: Path, monkeypatch):
    mock_backend_instance = Mock(generate_tests=AsyncMock(return_value=(True, "", "atco_artifacts/generated/test_module1.py")))
    mock_backend_class = Mock(return_value=mock_backend_instance)
    monkeypatch.setattr(BackendRegistry, "get_backend", Mock(return_value=mock_backend_class))
    
    targets = [
        {"identifier": "module1", "language": "python"},
        {"identifier": "module2", "language": "python"}
    ]
    result = await orchestrator.generate_tests_for_targets(targets, "atco_artifacts/generated")
    
    assert len(result) == 2
    assert result["module1"]["generation_success"] is True
    assert result["module2"]["generation_success"] is True
    assert mock_backend_instance.generate_tests.call_count == 2

@pytest.mark.asyncio
async def test_integrate_with_stubbed_components(orchestrator, project: Path, monkeypatch):
    gen_summary = {
        "module1": {
            "generation_success": True,
            "generated_test_path": "atco_artifacts/generated/test_module1.py",
            "language": "python"
        }
    }
    test_path = sanitize_path("atco_artifacts/generated/test_module1.py", str(project))
    (project / test_path).write_text("def test_dummy(): assert True")
    
    result = await orchestrator.integrate_and_validate_generated_tests(gen_summary)
    
    assert result["summary"]["total_integrated"] == 1
    assert result["details"]["module1"]["integration_status"] == "INTEGRATED"

@pytest.mark.asyncio
async def test_stub_initialization(project: Path, monkeypatch):
    def mock_load_component_failing(self, component_name, config_key, **kwargs):
        raise InitializationError("Failed to load")

    monkeypatch.setattr(
        "test_generation.orchestrator.orchestrator.GenerationOrchestrator._load_component",
        mock_load_component_failing
    )
    
    with pytest.raises(InitializationError):
        _ = GenerationOrchestrator(
            {"policy_config": {"module": "non.existent.module", "class": "PolicyEngine"}},
            str(project),
            "tests"
        )
    
def test_calculate_test_quality_score(orchestrator):
    score = orchestrator._calculate_test_quality_score(True, 50.0, 80.0)
    assert score == 0.5 + (50.0 / 100 * 0.25) + (80.0 / 100 * 0.25)
    score = orchestrator._calculate_test_quality_score(False, 0.0, -1.0)
    assert score == 0.0

@pytest.mark.asyncio
async def test_handle_single_test_deduplication(orchestrator, project: Path, monkeypatch):
    test_path_relative = "atco_artifacts/generated/test_module1.py"
    test_path = sanitize_path(test_path_relative, str(project))
    suite_path = sanitize_path("tests/test_module1.py", str(project))
    (project / test_path).parent.mkdir(parents=True, exist_ok=True)
    (project / test_path).write_text("def test_dummy(): pass")
    (project / suite_path).write_text("def test_dummy(): pass")
    
    result = await orchestrator._handle_single_test_integration(test_path_relative, "module1", "python")
    assert result["integration_status"] == "DEDUPLICATED"

@pytest.mark.asyncio
async def test_jira_integration(orchestrator, project: Path, monkeypatch):
    gen_summary = {
        "module1": {
            "generation_success": True,
            "generated_test_path": "atco_artifacts/generated/test_module1.py",
            "language": "python"
        }
    }
    test_path = sanitize_path("atco_artifacts/generated/test_module1.py", str(project))
    (project / test_path).parent.mkdir(parents=True, exist_ok=True)
    (project / test_path).write_text("def test_dummy(): assert False")
    
    monkeypatch.setattr("test_generation.orchestrator.orchestrator.run_pytest_and_coverage", AsyncMock(return_value=(False, 0.0, "Failed")))
    
    result = await orchestrator.integrate_and_validate_generated_tests(gen_summary)
    
    assert result["summary"]["total_quarantined"] == 1
    orchestrator.pr_creator.create_jira_ticket.assert_called()


def test_compliance_mapper_import_guard():
    from test_generation import compliance_mapper
    assert compliance_mapper.generate_report is not None

@pytest.mark.asyncio
async def test_compliance_reporting(orchestrator, project: Path, monkeypatch):
    gen_summary = {
        "module1": {
            "generation_success": True,
            "generated_test_path": "atco_artifacts/generated/test_module1.py",
            "language": "python"
        }
    }
    test_path = sanitize_path("atco_artifacts/generated/test_module1.py", str(project))
    (project / test_path).parent.mkdir(parents=True, exist_ok=True)
    (project / test_path).write_text("def test_dummy(): assert True")
    
    monkeypatch.setattr("test_generation.orchestrator.orchestrator.run_pytest_and_coverage", AsyncMock(return_value=(True, 10.0, "Passed")))
    
    mock_compliance = AsyncMock()
    monkeypatch.setattr("test_generation.orchestrator.orchestrator.generate_compliance_report", mock_compliance)
    
    orchestrator.config["compliance_reporting"] = {"enabled": True}
    
    result = await orchestrator.integrate_and_validate_generated_tests(gen_summary)
    
    assert result["summary"]["total_integrated"] == 1
    mock_compliance.assert_called_once()

@pytest.mark.asyncio
async def test_low_mutation_score_quarantine(orchestrator, project: Path, monkeypatch):
    """
    Test that a test is quarantined if its mutation score is too low.
    """
    gen_summary = {
        "module1": {
            "generation_success": True,
            "generated_test_path": "atco_artifacts/generated/test_module1.py",
            "language": "python"
        }
    }
    
    test_path_relative = "atco_artifacts/generated/test_module1.py"
    test_path = sanitize_path(test_path_relative, str(project))
    (project / test_path).parent.mkdir(parents=True, exist_ok=True)
    (project / test_path).write_text("def test_dummy(): pass")
    
    # Mock the mutation tester to return a low score
    mock_mutation_tester = AsyncMock(return_value=(True, 40.0, "Low score"))
    monkeypatch.setattr(orchestrator.mutation_tester, "run_mutations", mock_mutation_tester)
    
    # Ensure the config reflects a min score higher than the mock return
    orchestrator.config["mutation_testing"]["min_score_for_integration"] = 50.0
    
    result = await orchestrator.integrate_and_validate_generated_tests(gen_summary)
    
    # Assert that the test was quarantined due to the low mutation score
    assert result["summary"]["total_quarantined"] == 1
    assert result["details"]["module1"]["integration_status"] == "QUARANTINED"
    assert "Mutation score" in result["details"]["module1"]["reason"]

# Completed for syntactic validity.
def test_orchestrator_import():
    """
    Tests that the GenerationOrchestrator class and its run_pipeline method
    are correctly imported and callable.
    """
    from test_generation.orchestrator.orchestrator import GenerationOrchestrator
    assert callable(getattr(GenerationOrchestrator, 'run_pipeline', None))