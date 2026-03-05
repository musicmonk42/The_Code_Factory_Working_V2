# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite — SFE Output-Files Refresh Pipeline
===============================================

Validates the changes that ensure SFE fixes are reflected in the job's
tracked file list and the downloadable ZIP archive:

1. ``_invalidate_job_zip_cache()``          — removes only known cache ZIPs
2. ``refresh_job_output_files()``            — re-scans dir, updates job metadata
3. ``SFEService.apply_fix(_refresh=True)``   — calls refresh after applying fixes
4. ``SFEService.apply_fix(_refresh=False)``  — suppresses per-fix refresh in batches
5. ``SFEService.apply_all_pending_fixes()``  — single refresh after batch, not N+1
6. ``apply_fix`` API endpoint (sfe.py)       — returns ``output_refreshed`` flag

Loading strategy
----------------
All modules are loaded directly from their source files using
``importlib.util.spec_from_file_location``, bypassing the package
``__init__`` chains that pull in heavy optional dependencies (numpy, aiohttp,
redis, …).  Stubs are installed/removed manually (not via ``patch.dict``) so
the loaded module object outlives the stub context.

Coverage contract
-----------------
* All tests are self-contained — no network access, no real API keys.
* Filesystem tests use ``tmp_path`` (pytest fixture).

Author: Code Factory Platform Team
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_SERVER_DIR = PROJECT_ROOT / "server"


# ---------------------------------------------------------------------------
# Module loader infrastructure
# ---------------------------------------------------------------------------

def _mk(name: str, **attrs) -> types.ModuleType:
    """Create a minimal stub module with the given attributes."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def _mk_pkg(name: str, **attrs) -> types.ModuleType:
    """Create a minimal stub *package* (has __path__ so submodule lookups work)."""
    mod = _mk(name, **attrs)
    mod.__path__ = []
    mod.__package__ = name
    return mod


def _load_by_path(dotted_name: str, file_path: Path,
                  extra_stubs: dict | None = None) -> Any:
    """
    Load a Python source file directly by path and register it in
    ``sys.modules`` under *dotted_name*.

    Stubs in *extra_stubs* are installed before execution and restored
    afterward, while the loaded module itself remains in ``sys.modules``
    so that subsequent attribute look-ups (``server.services.X``) work.
    """
    stubs = extra_stubs or {}
    saved: dict = {}

    # Install stubs; save whatever was there before
    for name, stub in stubs.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = stub

    sys.modules.pop(dotted_name, None)

    try:
        spec = importlib.util.spec_from_file_location(
            dotted_name, file_path, submodule_search_locations=[]
        )
        mod = importlib.util.module_from_spec(spec)   # type: ignore[arg-type]
        sys.modules[dotted_name] = mod
        spec.loader.exec_module(mod)                  # type: ignore[union-attr]
        return mod
    finally:
        # Restore original modules (removes stubs) but do NOT remove the
        # loaded module — it stays registered under dotted_name.
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def _finalization_stubs(jobs_db: dict) -> dict:
    return {
        "server.schemas": _mk(
            "server.schemas",
            JobStage=MagicMock(),
            JobStatus=MagicMock(),
        ),
        "server.storage": _mk("server.storage", jobs_db=jobs_db),
    }


def _load_finalization(jobs_db: dict) -> Any:
    """Load server.services.job_finalization with a controlled jobs_db."""
    return _load_by_path(
        "server.services.job_finalization",
        _SERVER_DIR / "services" / "job_finalization.py",
        extra_stubs=_finalization_stubs(jobs_db),
    )


def _sfe_service_stubs() -> dict:
    """Stubs that break the circular import chain in sfe_service.py."""
    services_pkg = _mk_pkg("server.services")
    return {
        "server.storage": _mk("server.storage", jobs_db={}, fixes_db={}),
        "server.services": services_pkg,
        "server.services.omnicore_service": _mk(
            "server.services.omnicore_service",
            _load_sfe_analysis_report=MagicMock(return_value=None),
        ),
        "server.services.sfe_utils": _mk(
            "server.services.sfe_utils",
            transform_pipeline_issues_to_frontend_errors=MagicMock(),
            transform_pipeline_issues_to_bugs=MagicMock(),
            MAX_ISSUES_PER_BATCH=100,
            ERROR_ID_PREFIX="",
            DEFAULT_SEVERITY="medium",
        ),
    }


def _load_sfe_service() -> Any:
    """Load server.services.sfe_service with heavy transitive deps stubbed."""
    return _load_by_path(
        "server.services.sfe_service",
        _SERVER_DIR / "services" / "sfe_service.py",
        extra_stubs=_sfe_service_stubs(),
    )


def _load_sfe_router() -> Any:
    """
    Load server.routers.sfe directly by file path, bypassing the routers
    __init__ that imports server.services (and transitively numpy/redis/…).
    Also returns the loaded module so tests can use patch.object on it.
    """
    from server.schemas import FixStatus, Fix, FixApplyRequest
    from server.schemas import SuccessResponse

    # Build the minimal set of stubs that sfe.py imports at the top level
    services_pkg = _mk_pkg("server.services")
    services_pkg.SFEService = MagicMock()

    routers_pkg = _mk_pkg("server.routers")

    sfe_service_mod = _mk(
        "server.services.sfe_service",
        SFEService=MagicMock(),
        get_sfe_service=MagicMock(),
    )

    return _load_by_path(
        "server.routers.sfe",
        _SERVER_DIR / "routers" / "sfe.py",
        extra_stubs={
            "server.routers": routers_pkg,
            "server.services": services_pkg,
            "server.services.sfe_service": sfe_service_mod,
            "server.storage": _mk("server.storage", jobs_db={}, fixes_db={}),
        },
    )


def _minimal_job() -> MagicMock:
    """Return a job stub that mirrors the real Job object's mutable fields."""
    job = MagicMock()
    job.output_files = []
    job.metadata = {}
    return job


# ---------------------------------------------------------------------------
# 1. _invalidate_job_zip_cache
# ---------------------------------------------------------------------------


class TestInvalidateJobZipCache:
    """Unit tests for the _invalidate_job_zip_cache helper."""

    def _get_fn(self) -> tuple:
        """Return (module, _invalidate_job_zip_cache function)."""
        mod = _load_finalization({})
        return mod, mod._invalidate_job_zip_cache

    def _path_patch(self, tmp_path: Path):
        uploads = str(tmp_path / "uploads")
        return lambda p: Path(str(p).replace("./uploads", uploads))

    def test_removes_output_zip_from_job_root(self, tmp_path):
        """output.zip in the job root is deleted."""
        mod, fn = self._get_fn()
        job_dir = tmp_path / "uploads" / "job-a"
        job_dir.mkdir(parents=True)
        cache = job_dir / "output.zip"
        cache.write_bytes(b"PK")

        with patch.object(mod, "Path", side_effect=self._path_patch(tmp_path)):
            fn("job-a")

        assert not cache.exists()

    def test_removes_underscore_output_zip_from_job_root(self, tmp_path):
        """Files matching *_output.zip in the job root are deleted."""
        mod, fn = self._get_fn()
        job_dir = tmp_path / "uploads" / "job-b"
        job_dir.mkdir(parents=True)
        cache = job_dir / "run_output.zip"
        cache.write_bytes(b"PK")

        with patch.object(mod, "Path", side_effect=self._path_patch(tmp_path)):
            fn("job-b")

        assert not cache.exists()

    def test_preserves_zip_inside_generated_subdirectory(self, tmp_path):
        """ZIP files nested under a generated subdirectory are never deleted."""
        mod, fn = self._get_fn()
        job_dir = tmp_path / "uploads" / "job-c"
        sub = job_dir / "my_app" / "dist"
        sub.mkdir(parents=True)
        user_zip = sub / "app.zip"
        user_zip.write_bytes(b"PK")
        root_cache = job_dir / "output.zip"
        root_cache.write_bytes(b"PK")

        with patch.object(mod, "Path", side_effect=self._path_patch(tmp_path)):
            fn("job-c")

        assert user_zip.exists(), "User ZIP inside subdirectory must be preserved"
        assert not root_cache.exists(), "Root output.zip must be removed"

    def test_no_error_when_job_directory_absent(self):
        """No exception when the job directory does not exist."""
        _, fn = self._get_fn()
        fn("no-such-job")  # must not raise


# ---------------------------------------------------------------------------
# 2. refresh_job_output_files
# ---------------------------------------------------------------------------


class TestRefreshJobOutputFiles:
    """Unit tests for the refresh_job_output_files public function."""

    @pytest.mark.asyncio
    async def test_returns_false_for_unknown_job(self):
        """job_id absent from jobs_db → False, no exception."""
        mod = _load_finalization({})
        assert await mod.refresh_job_output_files("ghost-job") is False

    @pytest.mark.asyncio
    async def test_returns_false_for_empty_string(self):
        """Empty string job_id is rejected immediately."""
        mod = _load_finalization({})
        assert await mod.refresh_job_output_files("") is False

    @pytest.mark.asyncio
    async def test_returns_false_for_non_string(self):
        """Non-string input is rejected without raising TypeError."""
        mod = _load_finalization({})
        assert await mod.refresh_job_output_files(None) is False  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_updates_output_files_from_manifest(self):
        """When a manifest is available, job.output_files is updated correctly."""
        job_id = "manifest-job"
        job = _minimal_job()
        mod = _load_finalization({job_id: job})
        manifest = {
            "files": [
                {"path": "app/main.py", "size": 100},
                {"path": "app/models.py", "size": 200},
            ],
            "total_files": 2,
            "total_size": 300,
        }

        with patch.object(mod, "_generate_output_manifest", AsyncMock(return_value=manifest)), \
             patch.object(mod, "_invalidate_job_zip_cache"):
            result = await mod.refresh_job_output_files(job_id)

        assert result is True
        assert job.output_files == ["app/main.py", "app/models.py"]
        assert job.metadata["total_output_files"] == 2
        assert job.metadata["total_output_size"] == 300

    @pytest.mark.asyncio
    async def test_preserves_existing_output_files_when_no_manifest(self):
        """
        When the directory scan returns None, the existing output_files list
        is left unchanged — not silently replaced with an empty list.
        """
        job_id = "empty-dir-job"
        job = _minimal_job()
        job.output_files = ["stale/path.py"]
        mod = _load_finalization({job_id: job})

        with patch.object(mod, "_generate_output_manifest", AsyncMock(return_value=None)), \
             patch.object(mod, "_invalidate_job_zip_cache"):
            result = await mod.refresh_job_output_files(job_id)

        assert result is True
        assert job.output_files == ["stale/path.py"]

    @pytest.mark.asyncio
    async def test_always_calls_invalidate_zip_cache(self):
        """_invalidate_job_zip_cache is invoked regardless of manifest presence."""
        job_id = "zip-job"
        job = _minimal_job()
        mod = _load_finalization({job_id: job})
        invalidate = MagicMock()

        with patch.object(mod, "_generate_output_manifest", AsyncMock(return_value=None)), \
             patch.object(mod, "_invalidate_job_zip_cache", invalidate):
            await mod.refresh_job_output_files(job_id)

        invalidate.assert_called_once_with(job_id)

    @pytest.mark.asyncio
    async def test_fail_safe_on_manifest_error(self):
        """Errors from manifest generation are caught; refresh returns False."""
        job_id = "error-job"
        job = _minimal_job()
        mod = _load_finalization({job_id: job})

        with patch.object(
            mod, "_generate_output_manifest",
            AsyncMock(side_effect=RuntimeError("disk I/O error")),
        ):
            result = await mod.refresh_job_output_files(job_id)

        assert result is False, "Errors in manifest generation must not propagate"


# ---------------------------------------------------------------------------
# 3 & 4. SFEService.apply_fix — _refresh parameter
# ---------------------------------------------------------------------------


class TestApplyFixRefreshParameter:
    """
    Verify that SFEService.apply_fix honours the ``_refresh`` flag.

    The SFE module is loaded from its source file with heavy deps stubbed.
    """

    def _make_instance(self, tmp_path: Path) -> tuple:
        """Return (sfe_mod, sfe_instance, target_file_path)."""
        mod = _load_sfe_service()
        target = tmp_path / "app" / "main.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# original\n")
        sfe = mod.SFEService.__new__(mod.SFEService)
        sfe._sfe_components = {}
        sfe._UPLOADS_BASE_DIR = tmp_path.parent
        return mod, sfe, target

    def _fix_stub(self, job_id: str) -> MagicMock:
        fix = MagicMock()
        fix.job_id = job_id
        fix.proposed_changes = [
            {"action": "replace", "file": "app/main.py", "content": "# fixed\n", "line": 1}
        ]
        return fix

    @pytest.mark.asyncio
    async def test_refresh_called_by_default(self, tmp_path):
        """With _refresh=True (default), refresh_job_output_files is called once."""
        refresh_mock = AsyncMock(return_value=True)
        mod, sfe, target = self._make_instance(tmp_path)
        fix = self._fix_stub("default-job")
        storage = _mk("server.storage", fixes_db={"fix-A": fix}, jobs_db={})

        with patch.object(sfe, "_resolve_job_code_path", return_value=str(tmp_path)), \
             patch.object(sfe, "_resolve_fix_path", return_value=target), \
             patch.object(sfe, "_invalidate_analysis_cache"), \
             patch.dict(sys.modules, {"server.storage": storage}), \
             patch.dict(sys.modules, {
                 "server.services.job_finalization": _mk(
                     "server.services.job_finalization",
                     refresh_job_output_files=refresh_mock,
                 )
             }):
            result = await sfe.apply_fix("fix-A")

        assert result["applied"] is True
        refresh_mock.assert_called_once_with("default-job")

    @pytest.mark.asyncio
    async def test_refresh_suppressed_with_refresh_false(self, tmp_path):
        """With _refresh=False, refresh_job_output_files is NOT called."""
        refresh_mock = AsyncMock(return_value=True)
        mod, sfe, target = self._make_instance(tmp_path)
        fix = self._fix_stub("no-refresh-job")
        storage = _mk("server.storage", fixes_db={"fix-B": fix}, jobs_db={})

        with patch.object(sfe, "_resolve_job_code_path", return_value=str(tmp_path)), \
             patch.object(sfe, "_resolve_fix_path", return_value=target), \
             patch.object(sfe, "_invalidate_analysis_cache"), \
             patch.dict(sys.modules, {"server.storage": storage}), \
             patch.dict(sys.modules, {
                 "server.services.job_finalization": _mk(
                     "server.services.job_finalization",
                     refresh_job_output_files=refresh_mock,
                 )
             }):
            result = await sfe.apply_fix("fix-B", _refresh=False)

        assert result["applied"] is True
        refresh_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_not_called_on_dry_run(self, tmp_path):
        """During a dry-run, no refresh occurs regardless of _refresh value."""
        refresh_mock = AsyncMock(return_value=True)
        mod, sfe, target = self._make_instance(tmp_path)
        fix = self._fix_stub("dryrun-job")
        storage = _mk("server.storage", fixes_db={"fix-C": fix}, jobs_db={})

        with patch.object(sfe, "_resolve_job_code_path", return_value=str(tmp_path)), \
             patch.object(sfe, "_resolve_fix_path", return_value=target), \
             patch.object(sfe, "_invalidate_analysis_cache"), \
             patch.dict(sys.modules, {"server.storage": storage}), \
             patch.dict(sys.modules, {
                 "server.services.job_finalization": _mk(
                     "server.services.job_finalization",
                     refresh_job_output_files=refresh_mock,
                 )
             }):
            result = await sfe.apply_fix("fix-C", dry_run=True)

        assert result["dry_run"] is True
        refresh_mock.assert_not_called()


# ---------------------------------------------------------------------------
# 5. apply_all_pending_fixes — single batch refresh, not N+1
# ---------------------------------------------------------------------------


class TestApplyAllPendingFixesBatchRefresh:
    """
    Verify that apply_all_pending_fixes:
    - groups proposed_changes by target file and writes each file exactly once
    - performs exactly one refresh after all fixes are written (not N per fix)
    - merges same-line inserts so they are combined into a single write
    """

    def _make_sfe_and_mod(self) -> tuple:
        mod = _load_sfe_service()
        sfe = mod.SFEService.__new__(mod.SFEService)
        sfe._sfe_components = {}
        sfe._invalidate_analysis_cache = MagicMock()
        return mod, sfe

    def _approved_fix(self, job_id: str, proposed_changes=None) -> MagicMock:
        from server.schemas import FixStatus
        f = MagicMock()
        f.job_id = job_id
        f.status = FixStatus.APPROVED
        f.proposed_changes = proposed_changes or []
        return f

    @pytest.mark.asyncio
    async def test_exactly_one_refresh_for_multiple_applied_fixes(self, tmp_path):
        """
        For N fixes that each write to a temp file, refresh_job_output_files is
        called exactly once after all fixes have been written — never N times.
        Each file is also written exactly once per unique path.
        """
        refresh_mock = AsyncMock(return_value=True)
        mod, sfe = self._make_sfe_and_mod()

        # Create a real temp file that the fixes can target.
        target = tmp_path / "app" / "main.py"
        target.parent.mkdir(parents=True)
        target.write_text("# original\n")

        # All 3 fixes insert different content at line 1 of the same file.
        fixes = {}
        for i in range(3):
            fix_id = f"fix-{i}"
            fixes[fix_id] = self._approved_fix(
                "batch-job",
                proposed_changes=[
                    {
                        "file": str(target),
                        "action": "insert",
                        "line": 1,
                        "content": f"# fix {i}",
                    }
                ],
            )

        storage = _mk("server.storage", fixes_db=fixes, jobs_db={})

        # Mock path resolution so the test does not need a real job directory.
        sfe._resolve_job_code_path = MagicMock(return_value=str(tmp_path))
        sfe._resolve_fix_path = MagicMock(return_value=target)

        with patch.dict(sys.modules, {"server.storage": storage}), \
             patch.dict(sys.modules, {
                 "server.services.job_finalization": _mk(
                     "server.services.job_finalization",
                     refresh_job_output_files=refresh_mock,
                 )
             }):
            result = await sfe.apply_all_pending_fixes("batch-job")

        assert len(result["applied"]) == 3
        # Exactly one refresh at the end — not three
        refresh_mock.assert_called_once_with("batch-job")

    @pytest.mark.asyncio
    async def test_refresh_not_called_when_nothing_applied(self):
        """
        When all fixes have no applicable changes (empty proposed_changes),
        refresh_job_output_files must not be called.
        """
        refresh_mock = AsyncMock(return_value=True)
        mod, sfe = self._make_sfe_and_mod()

        # Fix with no proposed_changes — nothing to write.
        fix = self._approved_fix("fail-job", proposed_changes=[])
        storage = _mk("server.storage", fixes_db={"fix-X": fix}, jobs_db={})

        sfe._resolve_job_code_path = MagicMock(return_value=".")
        sfe._resolve_fix_path = MagicMock(return_value=None)

        with patch.dict(sys.modules, {"server.storage": storage}), \
             patch.dict(sys.modules, {
                 "server.services.job_finalization": _mk(
                     "server.services.job_finalization",
                     refresh_job_output_files=refresh_mock,
                 )
             }):
            result = await sfe.apply_all_pending_fixes("fail-job")

        assert result["applied"] == []
        refresh_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_same_line_inserts_merged_into_single_write(self, tmp_path):
        """
        When multiple fixes all insert content at line 1 of the same file,
        apply_all_pending_fixes must merge them into a single write so the file
        is not written multiple times (once per fix) and all content is preserved.
        """
        refresh_mock = AsyncMock(return_value=True)
        mod, sfe = self._make_sfe_and_mod()

        target = tmp_path / "app" / "main.py"
        target.parent.mkdir(parents=True)
        target.write_text("# original\n")

        # 2 fixes both insert at line 1
        fixes = {
            "fix-A": self._approved_fix(
                "merge-job",
                proposed_changes=[{
                    "file": str(target), "action": "insert", "line": 1,
                    "content": "import os",
                }],
            ),
            "fix-B": self._approved_fix(
                "merge-job",
                proposed_changes=[{
                    "file": str(target), "action": "insert", "line": 1,
                    "content": "import sys",
                }],
            ),
        }

        storage = _mk("server.storage", fixes_db=fixes, jobs_db={})
        sfe._resolve_job_code_path = MagicMock(return_value=str(tmp_path))
        sfe._resolve_fix_path = MagicMock(return_value=target)

        with patch.dict(sys.modules, {"server.storage": storage}), \
             patch.dict(sys.modules, {
                 "server.services.job_finalization": _mk(
                     "server.services.job_finalization",
                     refresh_job_output_files=refresh_mock,
                 )
             }):
            result = await sfe.apply_all_pending_fixes("merge-job")

        assert set(result["applied"]) == {"fix-A", "fix-B"}
        # Both imports must be present in the written file
        written = target.read_text()
        assert "import os" in written
        assert "import sys" in written
        # Manifest refresh called exactly once
        refresh_mock.assert_called_once_with("merge-job")


# ---------------------------------------------------------------------------
# 6. apply_fix API endpoint — output_refreshed flag
# ---------------------------------------------------------------------------


class TestApplyFixEndpointOutputRefreshedFlag:
    """
    Verify the sfe.py router's apply_fix endpoint returns the
    ``output_refreshed`` boolean in its response data.
    Loads the router directly from its source file to avoid numpy/redis deps.
    """

    def _build_fix(self, fix_id: str, job_id: str):
        from server.schemas import FixStatus, Fix
        from datetime import datetime, timezone
        return Fix(
            fix_id=fix_id,
            error_id="err-1",
            job_id=job_id,
            status=FixStatus.APPROVED,
            description="Test fix",
            proposed_changes=[],
            confidence=0.9,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    def _sfe_mock(self, applied: bool, dry_run: bool = False):
        m = MagicMock()
        m.apply_fix = AsyncMock(return_value={
            "fix_id": "x",
            "applied": applied,
            "dry_run": dry_run,
            "status": "success" if applied else ("simulated" if dry_run else "error"),
            "files_modified": ["app/main.py"] if applied else [],
            "changes_applied": 1 if applied else 0,
            "changes_skipped": 0,
            "changes_failed": 0 if applied else 1,
        })
        return m

    def _get_endpoint_and_mod(self) -> tuple:
        """
        Load the sfe router directly from its source file (bypasses the routers
        __init__ chain) and return (apply_fix function, router module).
        """
        router_mod = _load_sfe_router()
        return router_mod.apply_fix, router_mod

    @pytest.mark.asyncio
    async def test_output_refreshed_true_when_applied(self):
        """output_refreshed=True when fix is applied (not dry-run)."""
        from server.schemas import FixApplyRequest
        fix_id = "ok-fix"
        fix = self._build_fix(fix_id, "ok-job")
        endpoint, router_mod = self._get_endpoint_and_mod()

        with patch.object(router_mod, "fixes_db", {fix_id: fix}):
            resp = await endpoint(
                fix_id, FixApplyRequest(force=False, dry_run=False),
                sfe_service=self._sfe_mock(applied=True),
            )

        assert resp.success is True
        assert resp.data["output_refreshed"] is True

    @pytest.mark.asyncio
    async def test_output_refreshed_false_on_dry_run(self):
        """output_refreshed=False during a dry-run."""
        from server.schemas import FixApplyRequest
        fix_id = "dryrun-fix"
        fix = self._build_fix(fix_id, "dryrun-job")
        endpoint, router_mod = self._get_endpoint_and_mod()

        with patch.object(router_mod, "fixes_db", {fix_id: fix}):
            resp = await endpoint(
                fix_id, FixApplyRequest(force=False, dry_run=True),
                sfe_service=self._sfe_mock(applied=False, dry_run=True),
            )

        assert resp.data["output_refreshed"] is False

    @pytest.mark.asyncio
    async def test_output_refreshed_false_when_application_fails(self):
        """output_refreshed=False when applied=False (all changes failed)."""
        from server.schemas import FixApplyRequest
        fix_id = "fail-fix"
        fix = self._build_fix(fix_id, "fail-job")
        endpoint, router_mod = self._get_endpoint_and_mod()

        with patch.object(router_mod, "fixes_db", {fix_id: fix}):
            resp = await endpoint(
                fix_id, FixApplyRequest(force=False, dry_run=False),
                sfe_service=self._sfe_mock(applied=False),
            )

        assert resp.data["output_refreshed"] is False
