# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# -----------------------------------------------------------------------------
# Bootstrap minimal core modules BEFORE importing the system under test (SUT)
# -----------------------------------------------------------------------------
core_utils = types.ModuleType("core_utils")


def _alert_operator(msg, level="INFO"):  # pragma: no cover
    return None


def _scrub_secrets(x):  # pragma: no cover
    return x


core_utils.alert_operator = _alert_operator
core_utils.scrub_secrets = _scrub_secrets
sys.modules["core_utils"] = core_utils

core_audit = types.ModuleType("core_audit")


class _AuditLogger:  # pragma: no cover
    def log_event(self, *a, **k):
        return None


core_audit.audit_logger = _AuditLogger()
sys.modules["core_audit"] = core_audit

core_secrets = types.ModuleType("core_secrets")


class _SecretsMgr:  # pragma: no cover
    def get_secret(self, *a, **k):
        return "dummy_secret_value"


core_secrets.SECRETS_MANAGER = _SecretsMgr()
sys.modules["core_secrets"] = core_secrets

# Make package importable (tests/ is sibling of import_fixer/)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# -----------------------------------------------------------------------------
# Import the validator under test (package path)
# -----------------------------------------------------------------------------
from import_fixer.fixer_validate import (  # noqa: E402
    AnalyzerCriticalError,
    CodeValidator,
    StageResult,
)

# -----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def quiet_logs(monkeypatch):
    # Silence noisy tooling during tests
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    yield


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "test_validation_project"
    root.mkdir()

    file_ok = root / "ok_file.py"
    file_ok.write_text("def my_function():\n    return 1\n", encoding="utf-8")

    file_lint_fail = root / "lint_fail.py"
    file_lint_fail.write_text("x=1\n", encoding="utf-8")

    file_type_fail = root / "type_fail.py"
    file_type_fail.write_text(
        "def bad_add(a: int, b: str) -> int:\n    return a + b\n", encoding="utf-8"
    )

    file_security_fail = root / "security_fail.py"
    file_security_fail.write_text("password='secret'\n", encoding="utf-8")

    file_test_fail = root / "test_fail.py"
    file_test_fail.write_text("def test_fail():\n    assert False\n", encoding="utf-8")

    file_syntax_fail = root / "syntax_fail.py"
    file_syntax_fail.write_text("def bad_syntax:\n", encoding="utf-8")

    no_write_file = root / "no_write.py"
    no_write_file.write_text("pass\n", encoding="utf-8")

    return {
        "root": root,
        "ok": file_ok,
        "lint_fail": file_lint_fail,
        "type_fail": file_type_fail,
        "security_fail": file_security_fail,
        "test_fail": file_test_fail,
        "syntax_fail": file_syntax_fail,
        "no_write": no_write_file,
        "whitelist": [str(root)],
    }


# --------------------------- Initialization / Guards --------------------------


def test_validator_init_auto_whitelists_project_root_in_prod_when_list_empty(
    monkeypatch, tmp_path
):
    """
    Validator currently defaults empty whitelisted_paths to [project_root] even in PRODUCTION_MODE.
    Verify it does NOT raise and auto-whitelists the project root.
    """
    real_project = tmp_path / "proj"
    real_project.mkdir()
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    v = CodeValidator(project_root=str(real_project), whitelisted_paths=[])
    assert [str(p) for p in v.whitelisted_paths] == [str(real_project.resolve())]


def test_compile_file_unwhitelisted_path_raises_error(project):
    v = CodeValidator(str(project["root"]), project["whitelist"])
    outside = Path("/tmp/malicious.py")
    with pytest.raises(AnalyzerCriticalError, match="outside whitelisted paths"):
        v.compile_file(outside)


# ------------------------------- Linting --------------------------------------


@pytest.mark.asyncio
async def test_run_linting_with_ruff_success(project, monkeypatch):
    v = CodeValidator(str(project["root"]), project["whitelist"])

    # Pretend ruff exists
    monkeypatch.setattr(
        "import_fixer.fixer_validate.shutil.which",
        lambda cmd: "/usr/bin/ruff" if cmd == "ruff" else None,
        raising=False,
    )

    # Mock subprocess
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"", b""))
    monkeypatch.setattr(
        "import_fixer.fixer_validate.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
        raising=False,
    )

    res = await v.run_linting([project["ok"]])
    assert isinstance(res, StageResult)
    assert res.passed is True


@pytest.mark.asyncio
async def test_run_linting_with_ruff_failure(project, monkeypatch):
    v = CodeValidator(str(project["root"]), project["whitelist"])

    monkeypatch.setattr(
        "import_fixer.fixer_validate.shutil.which",
        lambda cmd: "/usr/bin/ruff" if cmd == "ruff" else None,
        raising=False,
    )

    proc = MagicMock()
    proc.returncode = 1
    proc.communicate = AsyncMock(return_value=(b"linting failed\n", b""))
    monkeypatch.setattr(
        "import_fixer.fixer_validate.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
        raising=False,
    )

    res = await v.run_linting([project["lint_fail"]])
    assert isinstance(res, StageResult)
    assert res.passed is False


@pytest.mark.asyncio
async def test_run_linting_missing_tools_is_graceful_even_in_prod(project, monkeypatch):
    """
    The validator treats missing tools as NonCritical (skipped), not a hard failure,
    even in PRODUCTION_MODE.
    """
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    v = CodeValidator(str(project["root"]), project["whitelist"])

    # Both tools missing
    monkeypatch.setattr(
        "import_fixer.fixer_validate.shutil.which", lambda cmd: None, raising=False
    )

    res = await v.run_linting([project["ok"]])
    assert isinstance(res, StageResult)
    assert res.passed is True  # skipped == treated as pass


# ------------------------- Single-file validation -----------------------------


@pytest.mark.asyncio
async def test_validate_and_commit_file_no_write_access_raises_error(
    project, monkeypatch
):
    v = CodeValidator(str(project["root"]), project["whitelist"])

    original = project["no_write"].read_text(encoding="utf-8")
    new_code = "print('hi')\n"

    # Force no write access
    monkeypatch.setattr(
        "import_fixer.fixer_validate.os.access",
        lambda p, m: m != os.W_OK,
        raising=False,
    )

    with pytest.raises(AnalyzerCriticalError, match="No write access"):
        await v.validate_and_commit_file(
            file_path=str(project["no_write"]),
            new_code=new_code,
            original_code=original,
            run_tests=False,
            interactive=False,
        )


@pytest.mark.asyncio
async def test_validate_and_commit_file_pipeline_success(
    project, monkeypatch, tmp_path
):
    v = CodeValidator(str(project["root"]), project["whitelist"])

    original_content = project["ok"].read_text(encoding="utf-8")
    new_content = "def my_function():\n    return 2\n"

    # Safe backup + write
    monkeypatch.setattr(
        "import_fixer.fixer_validate.shutil.copy", lambda *a, **k: None, raising=False
    )
    monkeypatch.setattr(
        "import_fixer.fixer_validate.os.access", lambda p, m: True, raising=False
    )

    # All stages pass
    ok_stage = StageResult(name="x", passed=True, duration_ms=1)
    monkeypatch.setattr(
        "import_fixer.fixer_validate.CodeValidator.compile_file",
        lambda *a, **k: ok_stage,
        raising=False,
    )
    monkeypatch.setattr(
        "import_fixer.fixer_validate.CodeValidator.run_linting",
        AsyncMock(return_value=ok_stage),
        raising=False,
    )
    monkeypatch.setattr(
        "import_fixer.fixer_validate.CodeValidator.run_type_checking",
        AsyncMock(return_value=ok_stage),
        raising=False,
    )
    monkeypatch.setattr(
        "import_fixer.fixer_validate.CodeValidator.run_static_analysis",
        AsyncMock(return_value=ok_stage),
        raising=False,
    )
    monkeypatch.setattr(
        "import_fixer.fixer_validate.CodeValidator.run_tests",
        AsyncMock(return_value=ok_stage),
        raising=False,
    )

    report = await v.validate_and_commit_file(
        file_path=str(project["ok"]),
        new_code=new_content,
        original_code=original_content,
        run_tests=True,
        interactive=False,
    )
    assert report.overall_passed is True
    assert project["ok"].read_text(encoding="utf-8") == new_content


@pytest.mark.asyncio
async def test_validate_and_commit_file_pipeline_failure_rolls_back(
    project, monkeypatch
):
    v = CodeValidator(str(project["root"]), project["whitelist"])

    original_content = project["ok"].read_text(encoding="utf-8")
    new_content = "def my_function():\n    return 999\n"

    monkeypatch.setattr(
        "import_fixer.fixer_validate.shutil.copy", lambda *a, **k: None, raising=False
    )
    monkeypatch.setattr(
        "import_fixer.fixer_validate.os.access", lambda p, m: True, raising=False
    )

    ok_stage = StageResult(name="x", passed=True, duration_ms=1)
    bad_stage = StageResult(name="lint", passed=False, duration_ms=1)

    monkeypatch.setattr(
        "import_fixer.fixer_validate.CodeValidator.compile_file",
        lambda *a, **k: ok_stage,
        raising=False,
    )
    monkeypatch.setattr(
        "import_fixer.fixer_validate.CodeValidator.run_linting",
        AsyncMock(return_value=bad_stage),
        raising=False,
    )
    # The rest would be skipped once lint fails

    report = await v.validate_and_commit_file(
        file_path=str(project["ok"]),
        new_code=new_content,
        original_code=original_content,
        run_tests=True,
        interactive=False,
    )
    # Failure => rollback performed and report.overall_passed False
    assert report.overall_passed is False
    assert project["ok"].read_text(encoding="utf-8") == original_content


# ------------------------------- Batch API ------------------------------------


@pytest.mark.asyncio
async def test_validate_and_commit_batch_unwhitelisted_path_raises_error(project):
    v = CodeValidator(str(project["root"]), project["whitelist"])
    with pytest.raises(AnalyzerCriticalError, match="outside whitelisted paths"):
        await v.validate_and_commit_batch(
            files_to_validate=[str(Path("/tmp/malicious.py"))],
            original_contents={},
            new_contents={},
            run_tests=False,
        )


# ----------------------------- PROD interactivity ----------------------------


@pytest.mark.asyncio
async def test_interactive_prompt_in_prod_commits_when_allowed(monkeypatch, project):
    """
    Current behavior: interactive confirmation still proceeds in PRODUCTION_MODE.
    This test asserts that the change is committed (no exception is raised).
    """
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    v = CodeValidator(str(project["root"]), project["whitelist"])

    original_content = project["ok"].read_text(encoding="utf-8")
    new_content = "def my_function():\n    return 42\n"

    # Ensure we can go through the flow and avoid pytest stdin capture
    monkeypatch.setattr(
        "import_fixer.fixer_validate.os.access", lambda p, m: True, raising=False
    )
    monkeypatch.setattr(
        "import_fixer.fixer_validate.input", lambda *a, **k: "y", raising=False
    )

    report = await v.validate_and_commit_file(
        file_path=str(project["ok"]),
        new_code=new_content,
        original_code=original_content,
        run_tests=False,
        interactive=True,
    )
    assert report.overall_passed is True
    assert project["ok"].read_text(encoding="utf-8") == new_content
