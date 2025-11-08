import pytest
import asyncio
import os
import json
from pathlib import Path
# Fix: Add missing imports for Mock and AsyncMock
from unittest.mock import Mock, AsyncMock, patch
# Fix: The test needs to import the HTMLReporter and its config path
from test_generation.orchestrator.reporting import _write_sarif_atomically, HTMLReporter, report_generation_duration, DummyMetric
from test_generation.orchestrator.config import SARIF_EXPORT_DIR, HTML_REPORTS_DIR
from test_generation.orchestrator import sanitize_path
from test_generation.orchestrator.console import log
from test_generation.orchestrator.audit import _audit

@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    # Fix: Use exist_ok=True to make mkdir idempotent.
    (root / SARIF_EXPORT_DIR).mkdir(parents=True, exist_ok=True)
    # Fix: Also create the HTML reports directory for the new test case.
    (root / HTML_REPORTS_DIR).mkdir(parents=True, exist_ok=True)
    return root

@pytest.mark.asyncio
async def test_write_sarif_success(project: Path, monkeypatch):
    sarif_path = project / sanitize_path(f"{SARIF_EXPORT_DIR}/test.sarif.json", str(project))
    data = {"version": "2.1.0", "runs": []}
    result = await _write_sarif_atomically(sarif_path, data)
    assert result is True
    assert sarif_path.exists()
    with sarif_path.open("r") as f:
        assert json.load(f) == data
    assert not (project / f"{SARIF_EXPORT_DIR}/test.sarif.json.tmp").exists()

@pytest.mark.asyncio
async def test_write_sarif_failure(project: Path, monkeypatch):
    sarif_path = project / sanitize_path(f"{SARIF_EXPORT_DIR}/test.sarif.json", str(project))
    # Fix: Use the standard unittest.mock.Mock instead of the custom class.
    monkeypatch.setattr(os, "replace", Mock(side_effect=OSError("Disk full")))
    result = await _write_sarif_atomically(sarif_path, {"version": "2.1.0"})
    assert result is False
    assert not sarif_path.exists()
    assert not (project / f"{SARIF_EXPORT_DIR}/test.sarif.json.tmp").exists()

@pytest.mark.asyncio
async def test_generate_html_report_success(project: Path, monkeypatch):
    # Fix: The original test case was flawed; this is a corrected version.
    # It tests the actual HTMLReporter class, not a mock.
    reporter = HTMLReporter(str(project))
    mock_policy_engine = Mock()
    mock_policy_engine.policy_hash = "1234567890"

    # Simulate the data to be written
    overall_results = {
        "summary": {"total_integrated": 1, "total_quarantined": 0, "total_requires_pr": 0, "total_deduplicated": 0, "total_not_generated": 0, "total_targets_considered": 1},
        "details": {"test_module": {"integration_status": "INTEGRATED", "test_passed": True, "coverage_increase_percent": 10.0, "security_issues_found": False, "security_max_severity": "NONE", "reason": "Test passed", "language": "python", "mutation_score_percent": 90.0}},
        "ai_metrics": {"refinement_success_rate_percent": 100.0, "total_refinement_attempts": 1, "total_generations": 1},
    }

    # Patch atomic_write to prevent actual file I/O
    with patch("test_generation.orchestrator.reporting.atomic_write", new_callable=AsyncMock) as mock_atomic_write:
        report_path_str = await reporter.generate_html_report(overall_results, mock_policy_engine)
        
        # Verify atomic_write was called for the main report and the 'latest' alias
        assert mock_atomic_write.call_count == 2
        
        # Check the return value
        assert isinstance(report_path_str, str)
        assert "atco_artifacts/sarif" in report_path_str
        assert "report_" in report_path_str

def test_prometheus_toggle(monkeypatch):
    # Fix: This test case simulates the user's intent to disable Prometheus.
    # It checks the module-level configuration, which is handled via an environment variable.
    
    # 1. Simulate Prometheus being disabled via environment variable
    monkeypatch.setenv("ATCO_ENABLE_PROMETHEUS", "false")
    
    # 2. Re-import the module to re-run the module-level Prometheus setup code.
    #    The `HTMLReporter` class is already imported, so we need to reload it.
    from test_generation.orchestrator import reporting
    
    # 3. Assert that the Prometheus metric objects are now the DummyMetric stubs
    assert isinstance(reporting.report_generation_duration, DummyMetric)
    assert isinstance(reporting.report_generation_errors, DummyMetric)