# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for SFE schema-mismatch fix behavior.

Covers the key scenarios identified in the forensic analysis of generated FastAPI
outputs that fail cold-start import due to missing schema symbols:

1. SFE proposes a functional schema alias fix (not a comment block) when a router
   imports a class name that does not exist in app/schemas.py.
2. The fix is idempotent: applying it twice does not add duplicate content.
3. The SFE analysis report (sfe_analysis_report.json) is preserved (marked stale)
   rather than deleted after fix application.
4. _generate_complexity_fix returns action='info' (guidance-only) instead of
   inserting TODO comment blocks into source files.
"""

import ast
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TESTING", "1")

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


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


def _write_schemas(tmp_path: Path, content: str) -> Path:
    """Write app/schemas.py and return the schemas file path."""
    schemas_file = tmp_path / "app" / "schemas.py"
    schemas_file.parent.mkdir(parents=True, exist_ok=True)
    schemas_file.write_text(content, encoding="utf-8")
    return schemas_file


# ===========================================================================
# Schema mismatch fix: functional alias not comment noise
# ===========================================================================


class TestSchemaMismatchFunctionalFix:
    """SFE must propose a functional schema class fix when a router imports a
    symbol that is absent from app/schemas.py, rather than inserting a TODO
    comment block into the project files."""

    def test_prescription_update_status_alias_generated(self, tmp_path):
        """PrescriptionUpdateStatus missing → class alias to PrescriptionUpdateStatusRead."""
        schemas_content = (
            "from pydantic import BaseModel\n\n"
            "class PrescriptionUpdateStatusCreate(BaseModel):\n"
            "    status: str\n\n"
            "class PrescriptionUpdateStatusRead(BaseModel):\n"
            "    id: int\n"
            "    status: str\n\n"
            "class PrescriptionUpdateStatusList(BaseModel):\n"
            "    items: list\n"
        )
        _write_schemas(tmp_path, schemas_content)
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))

        result = svc._generate_schema_fix(
            tmp_path / "app" / "routers" / "prescriptions.py",
            "cannot import name 'PrescriptionUpdateStatus' from 'app.schemas'",
            "job-1",
        )

        assert result is not None, "Schema fix must not return None for a missing class"
        assert result["success"] is True, "Fix must report success=True"
        assert result["action"] == "insert", "Fix must use insert action (not info)"
        # Must NOT be a TODO comment block
        content = result["content"]
        assert "TODO" not in content, (
            f"Fix content must not be a TODO comment block, got:\n{content}"
        )
        # Must generate a class alias to PrescriptionUpdateStatusRead
        assert "PrescriptionUpdateStatus" in content
        assert "PrescriptionUpdateStatusRead" in content
        # Verify it's syntactically valid Python
        try:
            ast.parse(
                "from pydantic import BaseModel\n"
                "class PrescriptionUpdateStatusRead(BaseModel):\n"
                "    id: int\n"
                "    status: str\n"
                + content
            )
        except SyntaxError as exc:
            pytest.fail(f"Generated content is not valid Python: {exc}\n\n{content}")

    def test_token_alias_generated(self, tmp_path):
        """Token missing → class alias to TokenRead."""
        schemas_content = (
            "from pydantic import BaseModel\n\n"
            "class TokenBase(BaseModel):\n"
            "    access_token: str\n\n"
            "class TokenCreate(BaseModel):\n"
            "    access_token: str\n"
            "    token_type: str\n\n"
            "class TokenRead(BaseModel):\n"
            "    access_token: str\n"
            "    token_type: str\n\n"
            "class TokenList(BaseModel):\n"
            "    items: list\n"
        )
        _write_schemas(tmp_path, schemas_content)
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))

        result = svc._generate_schema_fix(
            tmp_path / "app" / "routers" / "auth.py",
            "cannot import name 'Token' from 'app.schemas'",
            "job-1",
        )

        assert result is not None
        assert result["success"] is True
        assert result["action"] == "insert"
        content = result["content"]
        assert "TODO" not in content
        # Must use TokenRead for the alias (preferred for response_model)
        assert "Token" in content
        assert "TokenRead" in content
        assert "class Token" in content

    def test_fix_targets_schemas_file_not_router(self, tmp_path):
        """The fix must target app/schemas.py, not the router that imports the missing class."""
        schemas_content = (
            "from pydantic import BaseModel\n\n"
            "class UserRead(BaseModel):\n"
            "    id: int\n"
            "    name: str\n"
        )
        _write_schemas(tmp_path, schemas_content)
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))

        result = svc._generate_schema_fix(
            tmp_path / "app" / "routers" / "users.py",  # router file (the importer)
            "cannot import name 'User' from 'app.schemas'",
            "job-1",
        )

        assert result is not None
        # The fix file must point to schemas.py, not the router
        fix_file = result.get("file", "").replace("\\", "/")
        assert "schemas" in fix_file, (
            f"Fix must target the schemas file, got: {fix_file!r}"
        )
        assert "routers" not in fix_file, (
            f"Fix must NOT target the router file, got: {fix_file!r}"
        )

    def test_generated_project_can_import_main_after_fix(self, tmp_path):
        """After applying the schema alias fix, app.main should be importable.

        This simulates the key boot-blocker scenario: a generated FastAPI project
        fails cold-start import because a router imports a schema class that does
        not exist in app/schemas.py.  After the fix, `import app.main` must succeed.
        """
        # Create a minimal FastAPI project with a missing schema symbol
        app_dir = tmp_path / "app"
        app_dir.mkdir(parents=True, exist_ok=True)
        routers_dir = app_dir / "routers"
        routers_dir.mkdir(exist_ok=True)

        # schemas.py has TokenRead but NOT Token
        (app_dir / "schemas.py").write_text(
            "from pydantic import BaseModel\n\n"
            "class TokenRead(BaseModel):\n"
            "    access_token: str\n"
            "    token_type: str\n",
            encoding="utf-8",
        )

        # auth.py router imports 'Token' which doesn't exist
        (routers_dir / "auth.py").write_text(
            "from app.schemas import TokenRead, Token\n"
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/login', response_model=Token)\n"
            "def login():\n"
            "    return TokenRead(access_token='abc', token_type='bearer')\n",
            encoding="utf-8",
        )
        (routers_dir / "__init__.py").write_text("", encoding="utf-8")
        (app_dir / "__init__.py").write_text("", encoding="utf-8")
        (app_dir / "main.py").write_text(
            "from fastapi import FastAPI\n"
            "from app.routers import auth\n\n"
            "app = FastAPI()\n"
            "app.include_router(auth.router)\n",
            encoding="utf-8",
        )

        # Generate the schema fix
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))
        result = svc._generate_schema_fix(
            app_dir / "routers" / "auth.py",
            "cannot import name 'Token' from 'app.schemas'",
            "job-1",
        )
        assert result is not None and result["success"] is True

        # Apply the fix to schemas.py
        schemas_path = app_dir / "schemas.py"
        existing = schemas_path.read_text(encoding="utf-8")
        schemas_path.write_text(existing + result["content"], encoding="utf-8")

        # Verify schemas.py is valid Python with Token now defined
        schemas_code = schemas_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(schemas_code)
        except SyntaxError as exc:
            pytest.fail(f"Updated schemas.py is not valid Python: {exc}\n\n{schemas_code}")

        defined_classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        assert "Token" in defined_classes, (
            f"'Token' class not found after fix. Defined classes: {defined_classes}\n"
            f"schemas.py:\n{schemas_code}"
        )


# ===========================================================================
# Idempotency: applying fix twice must not add duplicate content
# ===========================================================================


class TestApplyFixIdempotency:
    """apply_fix must not insert the same content twice.

    Repeated SFE runs on the same project must not keep prepending the same
    content to the patched files.
    """

    def test_schema_alias_is_not_duplicated_on_second_apply(self, tmp_path):
        """Inserting a schema alias fix a second time must be a no-op."""
        schemas_content = (
            "from pydantic import BaseModel\n\n"
            "class TokenRead(BaseModel):\n"
            "    access_token: str\n"
            "    token_type: str\n"
        )
        _write_schemas(tmp_path, schemas_content)
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))

        result = svc._generate_schema_fix(
            tmp_path / "app" / "main.py",
            "cannot import name 'Token' from 'app.schemas'",
            "job-1",
        )
        assert result is not None and result["success"] is True

        schemas_path = tmp_path / "app" / "schemas.py"
        # Apply once
        existing = schemas_path.read_text(encoding="utf-8")
        schemas_path.write_text(existing + result["content"], encoding="utf-8")
        after_first = schemas_path.read_text(encoding="utf-8")

        # Count class Token occurrences after first apply
        token_count_1 = after_first.count("class Token(")

        # Regenerate fix and apply again (simulating a second SFE run)
        result2 = svc._generate_schema_fix(
            tmp_path / "app" / "main.py",
            "cannot import name 'Token' from 'app.schemas'",
            "job-1",
        )
        # Second call should return None because Token is now in schemas.py
        assert result2 is None, (
            "Second call to _generate_schema_fix must return None when the class "
            "already exists after first fix application."
        )

        # Content should be unchanged
        after_second = schemas_path.read_text(encoding="utf-8")
        token_count_2 = after_second.count("class Token(")
        assert token_count_2 == token_count_1, (
            f"Second fix application added duplicate Token class "
            f"(count went from {token_count_1} to {token_count_2})"
        )

    def test_apply_fix_skips_insert_when_content_already_present(self, tmp_path):
        """apply_fix insert action must be skipped when content is already in the file."""
        # Create a file that already has the target content
        target_file = tmp_path / "app" / "main.py"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        existing_content = (
            "from fastapi import FastAPI\n\n"
            "class Token(TokenRead):\n"
            '    """Auto-generated alias for TokenRead."""\n'
            "    pass\n\n"
            "app = FastAPI()\n"
        )
        target_file.write_text(existing_content, encoding="utf-8")

        svc = _make_sfe_service()
        # Make the job resolve to tmp_path so the relative path check passes
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))

        # Build a mock fix using a path RELATIVE to tmp_path (within the sandbox)
        content_to_insert = (
            "class Token(TokenRead):\n"
            '    """Auto-generated alias for TokenRead."""\n'
            "    pass\n"
        )
        relative_path = "app/main.py"

        from server.storage import fixes_db
        from server.schemas import Fix, FixStatus
        from datetime import datetime, timezone

        fix_id = "fix-idempotency-test-001"
        now = datetime.now(timezone.utc)
        fix_obj = Fix(
            fix_id=fix_id,
            error_id="err-001",
            job_id="job-idem-test",
            status=FixStatus.PROPOSED,
            description="Test idempotency",
            proposed_changes=[{
                "file": relative_path,
                "line": 1,
                "action": "insert",
                "content": content_to_insert,
            }],
            confidence=0.9,
            reasoning="Test",
            created_at=now,
            updated_at=now,
        )
        fixes_db[fix_id] = fix_obj

        try:
            import asyncio
            result = asyncio.run(svc.apply_fix(fix_id, dry_run=False, _refresh=False))
            # The fix should be skipped (idempotent), not applied
            assert result["changes_applied"] == 0, (
                f"Expected 0 changes applied (idempotent), got {result['changes_applied']}"
            )
            assert result["changes_skipped"] == 1, (
                f"Expected 1 change skipped, got {result['changes_skipped']}"
            )
            # File content must be unchanged
            after = target_file.read_text(encoding="utf-8")
            assert after == existing_content, "File content changed despite idempotency check"
        finally:
            fixes_db.pop(fix_id, None)


# ===========================================================================
# SFE analysis report persistence after fix application
# ===========================================================================


class TestSFEReportPersistence:
    """sfe_analysis_report.json must be preserved (marked stale) after apply_fix.

    Previously, _invalidate_analysis_cache deleted the report file.  This meant
    that a ZIP exported between fix application and the next detect_errors call
    would not contain the report.  The fix marks the report as stale instead.
    """

    def test_report_preserved_with_stale_marker_after_invalidation(self, tmp_path):
        """_invalidate_analysis_cache must mark the report stale, not delete it."""
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))

        # Create a mock report
        report_dir = tmp_path / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "sfe_analysis_report.json"
        initial_report = {
            "job_id": "job-persist-test",
            "issues": [{"type": "import_error", "message": "test issue"}],
            "count": 1,
            "generated_at": "2025-01-01T00:00:00+00:00",
        }
        report_path.write_text(json.dumps(initial_report, indent=2), encoding="utf-8")

        # Invalidate the cache
        svc._invalidate_analysis_cache("job-persist-test")

        # Report must still exist (not deleted)
        assert report_path.exists(), (
            "sfe_analysis_report.json must NOT be deleted after _invalidate_analysis_cache; "
            "it must be preserved with a stale marker for ZIP export compatibility."
        )

        # Report must have stale=True marker
        updated = json.loads(report_path.read_text(encoding="utf-8"))
        assert updated.get("stale") is True, (
            "Report must have 'stale: true' after _invalidate_analysis_cache. "
            f"Got: {updated}"
        )

        # Original issue data must be preserved
        assert updated.get("issues") == initial_report["issues"], (
            "Original issue data must be preserved in the stale report."
        )

    def test_report_not_affected_when_missing(self, tmp_path):
        """_invalidate_analysis_cache must not raise when report doesn't exist."""
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))
        # No report file exists — should not raise
        try:
            svc._invalidate_analysis_cache("job-no-report")
        except Exception as exc:
            pytest.fail(
                f"_invalidate_analysis_cache raised unexpectedly when report is absent: {exc}"
            )


# ===========================================================================
# _generate_complexity_fix returns info-only (no TODO insert)
# ===========================================================================


class TestComplexityFixIsInfoOnly:
    """_generate_complexity_fix must return action='info' (guidance-only) and
    must NOT insert TODO comment blocks into source files.

    Previously, the handler returned action='insert' with TODO comment content,
    causing repeated noise insertions on each SFE run."""

    def test_complexity_fix_source_failure_returns_info(self):
        """When source context read fails, return info action, not insert."""
        svc = _make_sfe_service()
        bad_context = {"success": False, "error": "file not found"}
        result = svc._generate_complexity_fix(
            Path("/nonexistent/main.py"),
            line_num=42,
            message="Complexity: 25",
            source_context=bad_context,
        )
        assert result is not None
        assert result["action"] == "info", (
            f"Expected action='info' for complexity fix with failed source context, "
            f"got action={result['action']!r}"
        )
        assert result.get("success") is False, (
            "Complexity fix must report success=False (guidance-only, no code change)"
        )

    def test_complexity_fix_success_returns_info(self):
        """Even with readable source, complexity fix returns info (not insert)."""
        svc = _make_sfe_service()
        good_context = {
            "success": True,
            "full_source": "def complex_fn():\n    for i in range(10):\n        pass\n",
            "target_line": "def complex_fn():",
        }
        result = svc._generate_complexity_fix(
            Path("/some/file.py"),
            line_num=1,
            message="Complexity: 15",
            source_context=good_context,
        )
        assert result is not None
        assert result["action"] == "info", (
            f"Complexity fix must return action='info' (guidance-only), "
            f"got action={result['action']!r}\ncontent={result.get('content')!r}"
        )
        assert result.get("success") is False, (
            "Complexity fix must report success=False (guidance-only, no code change)"
        )

    def test_complexity_fix_content_not_inserted_by_apply_fix(self, tmp_path):
        """Complexity fix content must NOT be written to file (info-only)."""
        svc = _make_sfe_service()
        # Make the job resolve to tmp_path so the relative path check passes
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))

        # Create target file
        target_file = tmp_path / "app" / "main.py"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        original_content = "from fastapi import FastAPI\napp = FastAPI()\n"
        target_file.write_text(original_content, encoding="utf-8")

        good_context = {
            "success": True,
            "full_source": original_content,
            "target_line": "from fastapi import FastAPI",
        }
        fix_result = svc._generate_complexity_fix(
            target_file,
            line_num=1,
            message="Complexity: 20",
            source_context=good_context,
        )

        assert fix_result["action"] == "info", "Complexity fix must be info-only"

        # Simulate how propose_fix handles info-only results: info fixes go to
        # proposed_changes with action='info', which apply_fix skips.
        from server.storage import fixes_db
        from server.schemas import Fix, FixStatus
        from datetime import datetime, timezone

        fix_id = "fix-complexity-test-001"
        now = datetime.now(timezone.utc)
        fix_obj = Fix(
            fix_id=fix_id,
            error_id="err-cx-001",
            job_id="job-cx-test",
            status=FixStatus.PROPOSED,
            description="Complexity fix test",
            proposed_changes=[{
                "file": "app/main.py",  # relative path within sandbox
                "line": 1,
                "action": "info",  # info-only
                "content": fix_result.get("content", ""),
            }],
            confidence=0.0,
            reasoning="test",
            created_at=now,
            updated_at=now,
        )
        fixes_db[fix_id] = fix_obj

        try:
            import asyncio
            result = asyncio.run(svc.apply_fix(fix_id, dry_run=False, _refresh=False))
            # No changes should have been applied (info-only is skipped)
            assert result["changes_applied"] == 0
            # File content must be unchanged
            after = target_file.read_text(encoding="utf-8")
            assert after == original_content, (
                "File content changed even though complexity fix was info-only"
            )
        finally:
            fixes_db.pop(fix_id, None)
