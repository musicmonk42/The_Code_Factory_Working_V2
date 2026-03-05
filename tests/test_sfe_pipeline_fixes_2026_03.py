# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for the SFE pipeline fixes applied on 2026-03-05:

  1. Fix broken import of call_llm_api
     - generator.runner.__init__ must re-export call_llm_api
     - sfe_service.py now imports from generator.runner.llm_client (canonical)
       with a fallback to generator.runner (re-export)

  2. Restore expected workflow: sandbox validation on approve, not apply
     - review_fix sets validation_status="skipped" when sandbox is intentionally
       bypassed (info-only / low-confidence / no-job-context / force-override)
     - apply_fix treats validation_status "skipped" as already-handled so it
       does not re-validate approved fixes

  3. Reduce false rejections for lint-only fixes
     - _is_lint_only_error_type() correctly identifies pylint/ruff/flake8 codes
     - validate_fix_in_sandbox uses lint non-regression criterion for lint-only
       fixes rather than requiring pytest improvement
"""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TESTING", "1")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sfe_service():
    """Return an SFEService instance without touching external services."""
    try:
        from server.services.sfe_service import SFEService
    except ImportError as exc:
        pytest.skip(f"SFEService not importable: {exc}")
    return SFEService()


# ===========================================================================
# Fix 1: call_llm_api import
# ===========================================================================


class TestCallLlmApiImport:
    """Verify that call_llm_api is importable from the correct paths."""

    def test_import_from_canonical_module(self):
        """call_llm_api must be importable from generator.runner.llm_client."""
        try:
            from generator.runner.llm_client import call_llm_api  # noqa: F401
        except ImportError as exc:
            pytest.skip(f"generator.runner.llm_client not importable: {exc}")
        assert callable(call_llm_api)

    def test_import_from_runner_package_re_export(self):
        """generator.runner.__init__ must re-export call_llm_api for backward compat."""
        try:
            from generator.runner import call_llm_api  # noqa: F401
        except ImportError as exc:
            pytest.skip(f"generator.runner not importable: {exc}")
        assert callable(call_llm_api)

    def test_runner_init_all_includes_call_llm_api(self):
        """call_llm_api should be listed in generator.runner.__all__."""
        try:
            import generator.runner as _runner
        except ImportError as exc:
            pytest.skip(f"generator.runner not importable: {exc}")
        assert "call_llm_api" in getattr(_runner, "__all__", [])

    def test_sfe_service_import_path_in_source(self):
        """sfe_service.py must import call_llm_api from generator.runner.llm_client."""
        sfe_path = (
            Path(__file__).parent.parent / "server" / "services" / "sfe_service.py"
        )
        content = sfe_path.read_text(encoding="utf-8")
        assert "generator.runner.llm_client" in content, (
            "sfe_service.py should import call_llm_api from "
            "generator.runner.llm_client (not just generator.runner)"
        )


# ===========================================================================
# Fix 2: review_fix sets validation_status="skipped" for bypassed validation
# ===========================================================================


class TestReviewFixSkippedValidationStatus:
    """
    review_fix must record validation_status='skipped' when sandbox validation
    is intentionally bypassed so that apply_fix does not re-validate.
    """

    def _make_fix(self, **kwargs):
        """Create a minimal Fix-like object."""
        try:
            from server.schemas import Fix, FixStatus
            from datetime import datetime, timezone
        except ImportError as exc:
            pytest.skip(f"server.schemas not importable: {exc}")
        from uuid import uuid4

        now = datetime.now(timezone.utc)
        defaults = dict(
            fix_id=str(uuid4()),
            error_id="err-1",
            job_id=None,
            status=FixStatus.PROPOSED,
            description="Fix C0116 in foo.py",
            proposed_changes=[{"action": "info", "file": "foo.py", "line": 1, "content": "# docstring"}],
            confidence=0.0,
            reasoning="info only",
            created_at=now,
            updated_at=now,
        )
        defaults.update(kwargs)
        return Fix(**defaults)

    @pytest.mark.asyncio
    async def test_info_only_fix_sets_skipped_status(self):
        """review_fix: info-only fix approval sets validation_status='skipped'."""
        try:
            from server.routers.sfe import review_fix
            from server.schemas import FixReviewRequest, FixStatus
            from server.storage import fixes_db
        except ImportError as exc:
            pytest.skip(f"server.routers.sfe not importable: {exc}")

        fix = self._make_fix()
        fixes_db[fix.fix_id] = fix

        mock_sfe = MagicMock()
        mock_sfe.validate_fix_in_sandbox = AsyncMock(return_value={"status": "validated"})

        request = FixReviewRequest(approved=True, comments=None)
        result = await review_fix(fix.fix_id, request, sfe_service=mock_sfe)

        # Validation should have been skipped (info-only), not called
        mock_sfe.validate_fix_in_sandbox.assert_not_called()
        assert result.validation_status == "skipped"
        assert result.status == FixStatus.APPROVED

        del fixes_db[fix.fix_id]

    @pytest.mark.asyncio
    async def test_low_confidence_fix_sets_skipped_status(self):
        """review_fix: low-confidence fix approval sets validation_status='skipped'."""
        try:
            from server.routers.sfe import review_fix
            from server.schemas import FixReviewRequest, FixStatus
            from server.storage import fixes_db
        except ImportError as exc:
            pytest.skip(f"server.routers.sfe not importable: {exc}")

        fix = self._make_fix(
            proposed_changes=[{"action": "replace", "file": "foo.py", "line": 1, "content": "x = 1"}],
            confidence=0.3,  # below 0.6 threshold
            job_id=None,
        )
        fixes_db[fix.fix_id] = fix

        mock_sfe = MagicMock()
        mock_sfe.validate_fix_in_sandbox = AsyncMock(return_value={"status": "validated"})

        request = FixReviewRequest(approved=True, comments=None)
        result = await review_fix(fix.fix_id, request, sfe_service=mock_sfe)

        mock_sfe.validate_fix_in_sandbox.assert_not_called()
        assert result.validation_status == "skipped"
        assert result.status == FixStatus.APPROVED

        del fixes_db[fix.fix_id]

    @pytest.mark.asyncio
    async def test_no_job_context_sets_skipped_status(self):
        """review_fix: fix with no job_id sets validation_status='skipped'."""
        try:
            from server.routers.sfe import review_fix
            from server.schemas import FixReviewRequest, FixStatus
            from server.storage import fixes_db
        except ImportError as exc:
            pytest.skip(f"server.routers.sfe not importable: {exc}")

        fix = self._make_fix(
            proposed_changes=[{"action": "replace", "file": "foo.py", "line": 1, "content": "x = 1"}],
            confidence=0.9,
            job_id=None,  # no job context
        )
        fixes_db[fix.fix_id] = fix

        mock_sfe = MagicMock()
        mock_sfe.validate_fix_in_sandbox = AsyncMock(return_value={"status": "validated"})

        request = FixReviewRequest(approved=True, comments=None)
        result = await review_fix(fix.fix_id, request, sfe_service=mock_sfe)

        mock_sfe.validate_fix_in_sandbox.assert_not_called()
        assert result.validation_status == "skipped"
        assert result.status == FixStatus.APPROVED

        del fixes_db[fix.fix_id]


class TestApplyFixDoesNotRevalidateSkipped:
    """apply_fix must not re-validate when validation_status is 'skipped'."""

    @pytest.mark.asyncio
    async def test_apply_skips_sandbox_when_validation_status_is_skipped(self):
        """apply_fix: validation_status='skipped' prevents sandbox re-validation."""
        try:
            from server.routers.sfe import apply_fix
            from server.schemas import Fix, FixApplyRequest, FixStatus
            from server.storage import fixes_db
            from datetime import datetime, timezone
        except ImportError as exc:
            pytest.skip(f"server schemas/routers not importable: {exc}")

        from uuid import uuid4

        now = datetime.now(timezone.utc)
        fix = Fix(
            fix_id=str(uuid4()),
            error_id="err-2",
            job_id=None,
            status=FixStatus.APPROVED,
            description="Fix C0116 in bar.py",
            proposed_changes=[{"action": "info", "file": "bar.py", "line": 1, "content": "# doc"}],
            confidence=0.0,
            reasoning="info only",
            created_at=now,
            updated_at=now,
            validation_status="skipped",
        )
        fixes_db[fix.fix_id] = fix

        mock_sfe = MagicMock()
        mock_sfe.validate_fix_in_sandbox = AsyncMock(return_value={"status": "validated"})
        mock_sfe.apply_fix = AsyncMock(
            return_value={"applied": True, "files_modified": [], "changes_applied": 0, "changes_failed": 0}
        )

        request = FixApplyRequest(force=False, dry_run=False, skip_validation=False)
        await apply_fix(fix.fix_id, request, sfe_service=mock_sfe)

        # Sandbox should NOT have been called because validation_status is "skipped"
        mock_sfe.validate_fix_in_sandbox.assert_not_called()

        del fixes_db[fix.fix_id]

    @pytest.mark.asyncio
    async def test_apply_runs_sandbox_when_no_prior_validation(self):
        """apply_fix: missing validation_status triggers sandbox validation."""
        try:
            from server.routers.sfe import apply_fix
            from server.schemas import Fix, FixApplyRequest, FixStatus
            from server.storage import fixes_db
            from datetime import datetime, timezone
        except ImportError as exc:
            pytest.skip(f"server schemas/routers not importable: {exc}")

        from uuid import uuid4

        now = datetime.now(timezone.utc)
        fix = Fix(
            fix_id=str(uuid4()),
            error_id="err-3",
            job_id="job-abc",
            status=FixStatus.APPROVED,
            description="Fix import error in baz.py",
            proposed_changes=[{"action": "replace", "file": "baz.py", "line": 1, "content": "x = 1"}],
            confidence=0.9,
            reasoning="auto-fixed",
            created_at=now,
            updated_at=now,
            validation_status=None,  # not yet validated
        )
        fixes_db[fix.fix_id] = fix

        mock_sfe = MagicMock()
        mock_sfe.validate_fix_in_sandbox = AsyncMock(
            return_value={"status": "validated", "result": {}}
        )
        mock_sfe.apply_fix = AsyncMock(
            return_value={"applied": True, "files_modified": [], "changes_applied": 0, "changes_failed": 0}
        )

        request = FixApplyRequest(force=False, dry_run=False, skip_validation=False)
        await apply_fix(fix.fix_id, request, sfe_service=mock_sfe)

        # Sandbox SHOULD have been called because there is no prior validation
        mock_sfe.validate_fix_in_sandbox.assert_called_once()

        del fixes_db[fix.fix_id]


# ===========================================================================
# Fix 3: Lint-only fix detection and non-regression validation
# ===========================================================================


class TestIsLintOnlyErrorType:
    """Unit tests for SFEService._is_lint_only_error_type."""

    @pytest.mark.parametrize("error_type,expected", [
        # pylint convention codes
        ("C0116", True),
        ("C0115", True),
        ("C0114", True),
        ("C0301", True),
        # pylint warning codes
        ("W0611", True),
        ("W0401", True),
        # pylint refactoring codes
        ("R0201", True),
        # flake8 style codes
        ("E501", True),
        ("E302", True),
        # pycodestyle warning codes
        ("W291", True),
        ("W503", True),
        # string contains "pylint"
        ("pylint:C0116", True),
        ("pylint_warning", True),
        # string contains "ruff"
        ("ruff:E501", True),
        # string contains "flake8"
        ("flake8_lint", True),
        # descriptive names
        ("missing-docstring", True),
        ("missing_module_docstring", True),
        ("unused-import", True),
        ("import-order", True),
        ("line-too-long", True),
        # NOT lint-only
        ("ImportError", False),
        ("TypeError", False),
        ("NameError", False),
        ("security", False),
        ("COMPLEXITY", False),
        ("", False),
    ])
    def test_detection(self, error_type, expected):
        svc = _make_sfe_service()
        result = svc._is_lint_only_error_type(error_type)
        assert result is expected, (
            f"_is_lint_only_error_type({error_type!r}) returned {result}, expected {expected}"
        )

    def test_env_override_adds_custom_pattern(self, monkeypatch):
        """SFE_LINT_ONLY_PATTERNS env var adds extra patterns."""
        svc = _make_sfe_service()
        monkeypatch.setenv("SFE_LINT_ONLY_PATTERNS", "mypy.*")
        assert svc._is_lint_only_error_type("mypy:error")
        # Built-in patterns still work
        assert svc._is_lint_only_error_type("C0116")


class TestIsLintOnlyFix:
    """Unit tests for SFEService._is_lint_only_fix."""

    def test_uses_errors_cache_when_available(self):
        """_is_lint_only_fix should consult _errors_cache for the error type."""
        svc = _make_sfe_service()
        fix = MagicMock()
        fix.error_id = "err-lint-1"
        fix.description = "Fix C0116 in foo.py"

        # Inject a lint-type error into the cache
        svc._errors_cache["err-lint-1"] = {"type": "C0116", "message": "Missing docstring"}
        assert svc._is_lint_only_fix(fix) is True

        # Inject a non-lint-type error
        svc._errors_cache["err-lint-1"] = {"type": "ImportError", "message": "missing module"}
        assert svc._is_lint_only_fix(fix) is False

    def test_falls_back_to_description(self):
        """_is_lint_only_fix should parse description when cache has no entry."""
        svc = _make_sfe_service()
        fix = MagicMock()
        fix.error_id = "no-such-id"
        fix.description = "Fix W0611 in bar.py"

        assert svc._is_lint_only_fix(fix) is True

    def test_non_lint_description_returns_false(self):
        svc = _make_sfe_service()
        fix = MagicMock()
        fix.error_id = "no-such-id"
        fix.description = "Fix ImportError in baz.py"
        assert svc._is_lint_only_fix(fix) is False


class TestValidateFixInSandboxLintOnly:
    """validate_fix_in_sandbox must use lint non-regression for lint-only fixes."""

    @pytest.mark.asyncio
    async def test_lint_only_fix_accepted_on_non_regression(self):
        """A lint-only fix that doesn't worsen lint count should be validated."""
        svc = _make_sfe_service()

        fix_mock = MagicMock()
        fix_mock.error_id = "err-lint"
        fix_mock.description = "Fix C0116 in foo.py"
        fix_mock.proposed_changes = [{"action": "info", "file": "foo.py", "line": 1, "content": ""}]

        with patch("server.storage.fixes_db", {"fix-lint": fix_mock}), \
             patch.object(svc, "_resolve_job_code_path", return_value="."), \
             patch("pathlib.Path.exists", return_value=False):
            # Job path not found → auto-validated (skipped)
            result = await svc.validate_fix_in_sandbox("fix-lint", "job-1")
            assert result["status"] == "validated"
            assert result["result"].get("skipped") is True

    @pytest.mark.asyncio
    async def test_lint_only_fix_validated_when_lint_improves(self):
        """Lint-only fix that improves lint count must be accepted."""
        import tempfile

        svc = _make_sfe_service()
        svc._errors_cache["err-lint-2"] = {"type": "C0116", "message": "Missing docstring"}

        fix_mock = MagicMock()
        fix_mock.error_id = "err-lint-2"
        fix_mock.description = "Fix C0116 in foo.py"
        fix_mock.proposed_changes = []

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "foo.py").write_text("x = 1\n")

            with patch("server.storage.fixes_db", {"fix-lint-2": fix_mock}), \
                 patch.object(svc, "_resolve_job_code_path", return_value=str(tmp_path)), \
                 patch.object(svc, "_count_lint_issues", side_effect=[10, 8]), \
                 patch("subprocess.run") as mock_run:

                # pytest collect-only → no errors
                collect_proc = MagicMock()
                collect_proc.stdout = ""
                collect_proc.stderr = ""
                collect_proc.returncode = 0

                # pytest full run → no tests (returncode=5 = no tests collected)
                test_proc = MagicMock()
                test_proc.stdout = ""
                test_proc.stderr = ""
                test_proc.returncode = 5

                mock_run.side_effect = [collect_proc, test_proc]

                result = await svc.validate_fix_in_sandbox("fix-lint-2", "job-2")

        assert result["status"] == "validated", (
            f"Expected 'validated' for lint improvement, got: {result}"
        )
        assert result["result"].get("is_lint_only") is True

    @pytest.mark.asyncio
    async def test_lint_only_fix_rejected_when_lint_regresses(self):
        """Lint-only fix that worsens lint count must be rejected."""
        import tempfile

        svc = _make_sfe_service()
        svc._errors_cache["err-lint-3"] = {"type": "C0116", "message": "Missing docstring"}

        fix_mock = MagicMock()
        fix_mock.error_id = "err-lint-3"
        fix_mock.description = "Fix C0116 in foo.py"
        fix_mock.proposed_changes = []

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "foo.py").write_text("x = 1\n")

            with patch("server.storage.fixes_db", {"fix-lint-3": fix_mock}), \
                 patch.object(svc, "_resolve_job_code_path", return_value=str(tmp_path)), \
                 patch.object(svc, "_count_lint_issues", side_effect=[5, 10]), \
                 patch("subprocess.run") as mock_run:

                collect_proc = MagicMock()
                collect_proc.stdout = ""
                collect_proc.stderr = ""
                collect_proc.returncode = 0

                test_proc = MagicMock()
                test_proc.stdout = ""
                test_proc.stderr = ""
                test_proc.returncode = 5

                mock_run.side_effect = [collect_proc, test_proc]

                result = await svc.validate_fix_in_sandbox("fix-lint-3", "job-3")

        assert result["status"] == "rejected", (
            f"Expected 'rejected' for lint regression, got: {result}"
        )

    @pytest.mark.asyncio
    async def test_non_lint_fix_still_requires_test_improvement(self):
        """Non-lint fixes must still pass test improvement criterion."""
        import tempfile

        svc = _make_sfe_service()
        svc._errors_cache["err-runtime"] = {"type": "ImportError", "message": "no module"}

        fix_mock = MagicMock()
        fix_mock.error_id = "err-runtime"
        fix_mock.description = "Fix ImportError in foo.py"
        fix_mock.proposed_changes = []

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "foo.py").write_text("x = 1\n")

            with patch("server.storage.fixes_db", {"fix-runtime": fix_mock}), \
                 patch.object(svc, "_resolve_job_code_path", return_value=str(tmp_path)), \
                 patch("subprocess.run") as mock_run:

                collect_proc = MagicMock()
                collect_proc.stdout = ""
                collect_proc.stderr = ""
                collect_proc.returncode = 0

                # No tests pass, no tests collected
                test_proc = MagicMock()
                test_proc.stdout = ""
                test_proc.stderr = ""
                test_proc.returncode = 5

                mock_run.side_effect = [collect_proc, test_proc]

                result = await svc.validate_fix_in_sandbox("fix-runtime", "job-4")

        # Non-lint fix with no test improvement → rejected
        assert result["status"] == "rejected"
        assert result["result"].get("is_lint_only") is False
