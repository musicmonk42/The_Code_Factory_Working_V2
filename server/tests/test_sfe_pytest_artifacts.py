# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for SFE pytest artifact ingestion.

Verifies that SFEService:
- Parses JUnit XML (results.xml) pytest artifacts to surface collection failures.
- Does NOT silently return 0 issues when a cached analysis report has 0 issues
  but pytest artifacts reveal failures (e.g. ModuleNotFoundError).
- Populates _errors_cache so propose_fix can work on discovered issues.
- Writes/updates reports/sfe_analysis_report.json with artifact-derived issues.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# Minimal JUnit XML fixtures -----------------------------------------------

# Collection error: pytest failed to import 'app' module.
JUNIT_MODULE_NOT_FOUND = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="0" skipped="0" tests="1" time="0.5">
    <testcase classname="tests.test_routes" name="tests/test_routes.py">
      <error type="ModuleNotFoundError"
             message="No module named &apos;app&apos;">
        ImportError while importing test module '/project/tests/test_routes.py'.
        ModuleNotFoundError: No module named 'app'
      </error>
    </testcase>
  </testsuite>
</testsuites>
"""

# Import error: 'cannot import name' variant.
JUNIT_CANNOT_IMPORT = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="0" skipped="0" tests="1" time="0.2">
    <testcase classname="tests.test_utils" name="tests/test_utils.py">
      <error type="ImportError"
             message="cannot import name &apos;helper&apos; from &apos;utils&apos;">
        ImportError: cannot import name 'helper' from 'utils'
      </error>
    </testcase>
  </testsuite>
</testsuites>
"""

# Generic assertion failure (not an import error).
JUNIT_ASSERTION_FAILURE = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="2" time="1.0">
    <testcase classname="tests.test_main" name="test_home_returns_200">
      <failure type="AssertionError" message="assert 404 == 200">
        E   assert 404 == 200
      </failure>
    </testcase>
    <testcase classname="tests.test_main" name="test_passing" />
  </testsuite>
</testsuites>
"""

# Zero-issue cached report (the scenario described in the problem statement).
CACHED_REPORT_ZERO_ISSUES = {
    "job_id": "placeholder",
    "issues": [],
    "all_defects": [],
    "count": 0,
    "source": "sfe_analysis_report",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sfe_service():
    """Return an SFEService with no OmniCore (unit-test mode)."""
    from server.services.sfe_service import SFEService

    return SFEService(omnicore_service=None)


def _make_job(job_id: str, output_path: str):
    """Create a minimal Job record and insert it into jobs_db."""
    from server.schemas import Job, JobStatus
    from server.storage import jobs_db

    job = Job(
        id=job_id,
        status=JobStatus.COMPLETED,
        input_files=[],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        metadata={"output_path": output_path},
    )
    jobs_db[job_id] = job
    return job


def _cleanup_job(job_id: str):
    from server.storage import jobs_db

    if job_id in jobs_db:
        del jobs_db[job_id]


# ---------------------------------------------------------------------------
# Unit tests: _parse_pytest_artifacts
# ---------------------------------------------------------------------------


class TestParsePytestArtifacts:
    """Tests for SFEService._parse_pytest_artifacts."""

    def test_module_not_found_error_surfaces_issue(self, tmp_path):
        """results.xml with ModuleNotFoundError is parsed into a high-severity issue."""
        sfe = _make_sfe_service()
        (tmp_path / "results.xml").write_text(JUNIT_MODULE_NOT_FOUND, encoding="utf-8")

        issues = sfe._parse_pytest_artifacts(tmp_path)

        assert len(issues) == 1
        issue = issues[0]
        assert issue["type"] in ("ModuleNotFoundError", "ImportError")
        assert issue["risk_level"] == "high"
        # Missing module name should appear somewhere in the issue details
        details = issue["details"]
        assert (
            "app" in details.get("missing_module", "")
            or "app" in details.get("message", "")
        )

    def test_module_not_found_provides_fix_recommendations(self, tmp_path):
        """ImportError issues include non-empty fix recommendations."""
        sfe = _make_sfe_service()
        (tmp_path / "results.xml").write_text(JUNIT_MODULE_NOT_FOUND, encoding="utf-8")

        issues = sfe._parse_pytest_artifacts(tmp_path)

        assert issues, "Expected at least one issue"
        fix_recs = issues[0]["details"].get("fix_recommendations", [])
        assert len(fix_recs) >= 2, "Expected multiple fix recommendations"
        combined = " ".join(fix_recs).lower()
        assert any(
            kw in combined for kw in ("pythonpath", "import", "package", "app")
        ), "Fix recommendations should mention PYTHONPATH, import, or the module name"

    def test_cannot_import_name_variant(self, tmp_path):
        """'cannot import name' errors are classified as ImportError."""
        sfe = _make_sfe_service()
        (tmp_path / "results.xml").write_text(JUNIT_CANNOT_IMPORT, encoding="utf-8")

        issues = sfe._parse_pytest_artifacts(tmp_path)

        assert len(issues) == 1
        assert issues[0]["type"] in ("ImportError", "ModuleNotFoundError")

    def test_generic_assertion_failure_captured(self, tmp_path):
        """Generic test failures (non-import) are also captured."""
        sfe = _make_sfe_service()
        (tmp_path / "results.xml").write_text(JUNIT_ASSERTION_FAILURE, encoding="utf-8")

        issues = sfe._parse_pytest_artifacts(tmp_path)

        assert len(issues) == 1
        assert issues[0]["type"] == "AssertionError"
        assert issues[0]["risk_level"] == "medium"

    def test_results_xml_in_results_subdirectory(self, tmp_path):
        """results.xml nested in a 'results/' subdirectory is discovered."""
        sfe = _make_sfe_service()
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        (results_dir / "results.xml").write_text(JUNIT_MODULE_NOT_FOUND, encoding="utf-8")

        issues = sfe._parse_pytest_artifacts(tmp_path)

        assert len(issues) >= 1
        assert any(
            i["type"] in ("ModuleNotFoundError", "ImportError") for i in issues
        )

    def test_no_artifacts_returns_empty_list(self, tmp_path):
        """When no results.xml exists the method returns an empty list."""
        sfe = _make_sfe_service()
        issues = sfe._parse_pytest_artifacts(tmp_path)
        assert issues == []

    def test_malformed_xml_returns_empty_list(self, tmp_path):
        """Malformed XML does not raise; returns empty list with a warning."""
        sfe = _make_sfe_service()
        (tmp_path / "results.xml").write_text("not valid xml <<<", encoding="utf-8")

        issues = sfe._parse_pytest_artifacts(tmp_path)

        assert issues == []

    def test_passing_tests_not_surfaced(self, tmp_path):
        """Passing testcases do not produce issues."""
        sfe = _make_sfe_service()
        xml = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" tests="3" time="0.1">
    <testcase classname="tests.test_app" name="test_a" />
    <testcase classname="tests.test_app" name="test_b" />
    <testcase classname="tests.test_app" name="test_c" />
  </testsuite>
</testsuites>
"""
        (tmp_path / "results.xml").write_text(xml, encoding="utf-8")

        issues = sfe._parse_pytest_artifacts(tmp_path)

        assert issues == []


# ---------------------------------------------------------------------------
# Integration tests: analyze_code augments cached 0-issue reports
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAnalyzeCodeAugmentsWithPytestArtifacts:
    """
    Tests that analyze_code does not return 0 issues when:
    - A cached analysis report has 0 issues, AND
    - pytest artifacts reveal failures (e.g. ModuleNotFoundError).
    """

    async def test_cached_zero_issues_augmented_by_artifacts(self, tmp_path):
        """
        analyze_code should return issues from pytest artifacts when the cached
        report has 0 issues, instead of silently returning an empty result.
        """
        sfe = _make_sfe_service()
        job_id = "test-artifact-augment-01"

        # Set up project directory with reports/ and a zero-issue cached report.
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        reports_dir = project_dir / "reports"
        reports_dir.mkdir()
        report = dict(CACHED_REPORT_ZERO_ISSUES)
        report["job_id"] = job_id
        (reports_dir / "sfe_analysis_report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )

        # Pytest results.xml with ModuleNotFoundError.
        (project_dir / "results.xml").write_text(
            JUNIT_MODULE_NOT_FOUND, encoding="utf-8"
        )

        _make_job(job_id, str(project_dir))
        try:
            result = await sfe.analyze_code(job_id, str(project_dir))
        finally:
            _cleanup_job(job_id)

        assert result["issues_found"] > 0, (
            f"Expected issues_found > 0 when cached has 0 issues but artifacts exist, "
            f"got issues_found={result['issues_found']}"
        )
        issue_types = {i["type"] for i in result["issues"]}
        assert issue_types & {"ModuleNotFoundError", "ImportError"}, (
            f"Expected at least one ModuleNotFoundError/ImportError, got: {issue_types}"
        )

    async def test_analyze_code_populates_errors_cache(self, tmp_path):
        """
        _errors_cache must be populated after analyze_code so that propose_fix
        can generate recommendations.
        """
        sfe = _make_sfe_service()
        job_id = "test-artifact-cache-02"

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "reports").mkdir()
        report = dict(CACHED_REPORT_ZERO_ISSUES)
        report["job_id"] = job_id
        (project_dir / "reports" / "sfe_analysis_report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        (project_dir / "results.xml").write_text(
            JUNIT_MODULE_NOT_FOUND, encoding="utf-8"
        )

        _make_job(job_id, str(project_dir))
        try:
            await sfe.analyze_code(job_id, str(project_dir))
        finally:
            _cleanup_job(job_id)

        assert len(sfe._errors_cache) > 0, (
            "Expected _errors_cache to be populated with artifact-derived issues "
            "so that propose_fix can work."
        )

    async def test_analyze_code_writes_report_when_artifacts_augment(self, tmp_path):
        """
        When artifact issues augment a 0-issue cached report, the report file
        must be updated so GET /analysis-report reflects the real state.
        """
        sfe = _make_sfe_service()
        job_id = "test-artifact-write-03"

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "reports").mkdir()
        report = dict(CACHED_REPORT_ZERO_ISSUES)
        report["job_id"] = job_id
        report_file = project_dir / "reports" / "sfe_analysis_report.json"
        report_file.write_text(json.dumps(report), encoding="utf-8")
        (project_dir / "results.xml").write_text(
            JUNIT_MODULE_NOT_FOUND, encoding="utf-8"
        )

        _make_job(job_id, str(project_dir))
        try:
            await sfe.analyze_code(job_id, str(project_dir))
        finally:
            _cleanup_job(job_id)

        updated = json.loads(report_file.read_text(encoding="utf-8"))
        issues_in_report = updated.get("issues", updated.get("all_defects", []))
        assert len(issues_in_report) > 0, (
            "The on-disk report should be updated with artifact issues after augmentation"
        )

    async def test_no_artifacts_and_no_cached_report_returns_empty(self, tmp_path):
        """
        When neither a cached report nor pytest artifacts exist, and neither
        CodebaseAnalyzer nor OmniCore is available, analyze_code returns 0 issues.
        """
        sfe = _make_sfe_service()
        job_id = "test-artifact-empty-04"

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        _make_job(job_id, str(project_dir))
        try:
            result = await sfe.analyze_code(job_id, str(project_dir))
        finally:
            _cleanup_job(job_id)

        assert result["issues_found"] == 0

    async def test_non_zero_cached_report_not_overridden(self, tmp_path):
        """
        When the cached report already has issues, artifact issues should NOT
        replace them (existing behavior preserved).
        """
        sfe = _make_sfe_service()
        job_id = "test-artifact-no-override-05"

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "reports").mkdir()

        # Cached report with 1 existing issue.
        # _load_sfe_analysis_report uses "all_defects" then "issues", so we
        # populate all_defects to ensure the issue is loaded.
        existing_issue = {
            "type": "SyntaxError",
            "risk_level": "medium",
            "file": "app/main.py",
            "details": {"message": "invalid syntax", "line": 5},
        }
        cached = {
            "job_id": job_id,
            "all_defects": [existing_issue],
            "issues": [existing_issue],
            "count": 1,
            "source": "sfe_analysis_report",
        }
        (project_dir / "reports" / "sfe_analysis_report.json").write_text(
            json.dumps(cached), encoding="utf-8"
        )
        # Also add results.xml to verify it doesn't override the cached result.
        (project_dir / "results.xml").write_text(
            JUNIT_MODULE_NOT_FOUND, encoding="utf-8"
        )

        _make_job(job_id, str(project_dir))
        try:
            result = await sfe.analyze_code(job_id, str(project_dir))
        finally:
            _cleanup_job(job_id)

        # The cached non-zero result is returned unchanged (artifacts not used).
        assert result["issues_found"] == 1
        assert result["issues"][0]["type"] == "SyntaxError"
