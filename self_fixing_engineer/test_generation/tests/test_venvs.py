# test_generation/orchestrator/tests/test_venvs.py
"""Enterprise-grade tests for venvs.py

Covers:
- sanitize_path: happy path, empty, traversal, symlink-escape
- retry + jitter bounds with deterministic urandom and captured sleeps
- cancel during backoff sleep → CancelledError propagates, audit called
- persist vs cleanup behavior
- keep_on_failure toggling cleanup
- timeout kwarg propagation to create_and_install_venv
- dependency spec sanitation and config fallbacks
"""
from __future__ import annotations

import asyncio
import importlib
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any, List
from unittest.mock import AsyncMock, Mock

import pytest


def _import_venvs():
    # Fix: Correct the import path to match the project structure
    return importlib.import_module("test_generation.orchestrator.venvs")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for k in ["ATCO_VENV_PERSIST"]:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def venvs(monkeypatch: pytest.MonkeyPatch):
    # Reload to reset globals between tests
    if "test_generation.orchestrator.venvs" in sys.modules:
        del sys.modules["test_generation.orchestrator.venvs"]
    return _import_venvs()


@pytest.fixture
def project(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "repo"
    root.mkdir()
    # minimal artifacts layout
    (root / "atco_artifacts/venv_temp").mkdir(parents=True, exist_ok=True)
    return root


# -----------------------------
# sanitize_path
# -----------------------------
class TestSanitizePath:
    def test_happy_path(self, venvs, project: pathlib.Path):
        rel = "atco_artifacts/venv_temp/run-1"
        out = venvs.sanitize_path(rel, str(project))
        assert pathlib.Path(out).is_relative_to(project.resolve())

    def test_empty_path_rejected(self, venvs, project: pathlib.Path):
        with pytest.raises(ValueError):
            venvs.sanitize_path("", str(project))

    def test_traversal_rejected(self, venvs, project: pathlib.Path):
        with pytest.raises(ValueError):
            venvs.sanitize_path(os.path.join("..", "escape"), str(project))

    def test_symlink_escape_rejected(
        self, venvs, project: pathlib.Path, tmp_path: pathlib.Path, monkeypatch
    ):
        outside = tmp_path / "outside"
        outside.mkdir()
        sneaky = project / "link"

        # FIX: Mock Path.resolve to simulate a symlink escaping the project root, avoiding platform-specific errors.
        # This is a more reliable and cross-platform way to test this logic.
        real_path_resolve = pathlib.Path.resolve

        def mock_path_resolve(self, *args, **kwargs):
            if self == sneaky:
                return outside.resolve()
            return real_path_resolve(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "resolve", mock_path_resolve)

        with pytest.raises(ValueError, match="is outside the project root"):
            venvs.sanitize_path(str(sneaky), str(project))


# -----------------------------
# Retry & jitter bounds
# -----------------------------
@pytest.mark.asyncio
async def test_retry_and_jitter_bounds(
    monkeypatch: pytest.MonkeyPatch, venvs, project: pathlib.Path
):
    calls = {"pip_install": 0, "sleep": []}

    # Mock venv.create to succeed, so we get to the `pip install` part
    def mock_venv_create(path, *args, **kwargs):
        pathlib.Path(path, "Scripts").mkdir(parents=True)
        pathlib.Path(path, "Scripts", "pip.exe").touch()

    monkeypatch.setattr(venvs.venv, "create", Mock(side_effect=mock_venv_create))

    # Mock subprocess.run to fail on every call
    def mock_run_fail(*args, **kwargs):
        # We check the arguments passed to subprocess.run to ensure it's a pip install call
        if any("pip" in arg for arg in args[0]):
            calls["pip_install"] += 1
            # Check if a specific timeout was passed
            if kwargs.get("timeout"):
                pass  # The timeout propagation test will handle this
            raise subprocess.CalledProcessError(1, "cmd", stderr="mocked failure")
        # Simulate successful venv creation commands
        return Mock(returncode=0)

    monkeypatch.setattr(subprocess, "run", Mock(side_effect=mock_run_fail))

    # Fix: Make os.urandom return a 16-byte string as expected by uuid.uuid4
    monkeypatch.setattr(os, "urandom", lambda n: b"\x80" * n)

    async def fake_sleep(d):
        calls["sleep"].append(float(d))
        # do not actually sleep
        return

    # We need to mock the real cleanup function
    monkeypatch.setattr(venvs.utils, "cleanup_temp_dir", AsyncMock())
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    # Tune config for test speed
    cfg = dict(venvs.CONFIG)
    cfg.update(
        {
            "venv_creation_retries": 2,
            "venv_retry_backoff_min": 0.01,
            "venv_retry_backoff_max": 0.02,
        }
    )
    monkeypatch.setattr(venvs, "CONFIG", cfg, raising=False)

    with pytest.raises(RuntimeError):
        # FIX: Correct the typo from `temporary_venv` to `temporary_env`
        async with venvs.temporary_env(
            str(project), language="python", required_deps=["a==1"]
        ):
            pass

    # 1 initial + 2 retries = 3 create attempts
    assert calls["pip_install"] == 3
    # One backoff sleep between attempts
    assert len(calls["sleep"]) == 2
    for s in calls["sleep"]:
        assert 0.01 <= s <= 0.02


# -----------------------------
# Cancel during backoff sleep
# -----------------------------
@pytest.mark.asyncio
async def test_cancel_during_backoff_sleep(
    monkeypatch: pytest.MonkeyPatch, venvs, project: pathlib.Path
):
    # Mock venv.create to succeed, so we get to the `pip install` part
    def mock_venv_create(path, *args, **kwargs):
        pathlib.Path(path, "Scripts").mkdir(parents=True)
        pathlib.Path(path, "Scripts", "pip.exe").touch()

    monkeypatch.setattr(venvs.venv, "create", Mock(side_effect=mock_venv_create))

    # Mock subprocess.run to fail on every call
    def mock_run_fail(*args, **kwargs):
        if any("pip" in arg for arg in args[0]):
            raise subprocess.CalledProcessError(1, "cmd", stderr="mocked failure")
        return Mock(returncode=0)

    monkeypatch.setattr(subprocess, "run", Mock(side_effect=mock_run_fail))

    # Capture audits
    records: List[str] = []
    # FIX: Patch the function `audit_event` directly, not a non-existent instance
    # The original was not awaited, so we use an AsyncMock.
    mock_audit = AsyncMock(side_effect=lambda name, data, **kw: records.append(data))
    monkeypatch.setattr(venvs, "audit_event", mock_audit)

    # Sleep cancels
    async def cancel_sleep(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", cancel_sleep)

    cfg = dict(venvs.CONFIG)
    cfg.update(
        {
            "venv_creation_retries": 2,
            "venv_retry_backoff_min": 0.01,
            "venv_retry_backoff_max": 0.02,
        }
    )
    monkeypatch.setattr(venvs, "CONFIG", cfg, raising=False)
    monkeypatch.setattr(venvs.utils, "cleanup_temp_dir", AsyncMock())

    with pytest.raises(asyncio.CancelledError):
        # FIX: Correct the typo from `temporary_venv` to `temporary_env`
        async with venvs.temporary_env(
            str(project), language="python", required_deps=["x"]
        ):
            pass

    assert any("venv_creation_cancelled" in str(r) for r in records)


# -----------------------------
# Persist vs cleanup
# -----------------------------
@pytest.mark.asyncio
async def test_persist_vs_cleanup(
    monkeypatch: pytest.MonkeyPatch, venvs, project: pathlib.Path
):
    # Mock venv.create to succeed
    def mock_venv_create(path, *args, **kwargs):
        pathlib.Path(path, "Scripts").mkdir(parents=True)
        pathlib.Path(path, "Scripts", "python.exe").touch()

    monkeypatch.setattr(venvs.venv, "create", Mock(side_effect=mock_venv_create))
    monkeypatch.setattr(subprocess, "run", Mock())
    monkeypatch.setattr(venvs, "audit_event", AsyncMock())

    # Mock cleanup to allow us to manually check
    mock_cleanup = AsyncMock()
    monkeypatch.setattr(venvs.utils, "cleanup_temp_dir", mock_cleanup)

    # Persist via parameter
    # FIX: Correct the typo from `temporary_venv` to `temporary_env`
    temp_dir_path = ""
    async with venvs.temporary_env(
        str(project), language="python", required_deps=[], persist=True
    ) as py:
        assert isinstance(py, venvs.EnvHandle)
        assert py.exec_path
        temp_dir_path = py.root_path
        assert pathlib.Path(temp_dir_path).exists()

    # Cleanup should not be called when persist=True
    assert mock_cleanup.call_count == 0
    # Directory should still exist after the context manager exits, as per the fix to venvs.py
    assert pathlib.Path(temp_dir_path).exists()
    shutil.rmtree(temp_dir_path)  # Manual cleanup

    # Cleanup when persist False
    mock_cleanup.reset_mock()
    temp_dir_path = ""
    async with venvs.temporary_env(
        str(project), language="python", required_deps=[], persist=False
    ) as py:
        assert isinstance(py, venvs.EnvHandle)
        temp_dir_path = py.root_path

    # Cleanup should have been called
    assert mock_cleanup.call_count == 1
    # The directory should be gone
    assert not pathlib.Path(temp_dir_path).exists()


# -----------------------------
# keep_on_failure toggles cleanup
# -----------------------------
@pytest.mark.asyncio
async def test_keep_on_failure_prevents_cleanup(
    monkeypatch: pytest.MonkeyPatch, venvs, project: pathlib.Path
):
    calls = {"cleanup": 0}

    async def cleanup_temp_dir(full_path):
        calls["cleanup"] += 1
        return

    monkeypatch.setattr(venvs.utils, "cleanup_temp_dir", cleanup_temp_dir)
    monkeypatch.setattr(venvs, "audit_event", AsyncMock())

    # Mock venv.create to succeed
    def mock_venv_create(path, *args, **kwargs):
        pathlib.Path(path, "Scripts").mkdir(parents=True)
        pathlib.Path(path, "Scripts", "pip.exe").touch()

    monkeypatch.setattr(venvs.venv, "create", Mock(side_effect=mock_venv_create))

    # Mock the internal subprocess call to fail, which is how a real failure would happen
    monkeypatch.setattr(
        subprocess,
        "run",
        Mock(
            side_effect=subprocess.CalledProcessError(1, "cmd", stderr="mocked failure")
        ),
    )

    # `keep_on_failure=True`
    with pytest.raises(RuntimeError):
        # FIX: Correct the typo from `temporary_venv` to `temporary_env`
        async with venvs.temporary_env(
            str(project),
            language="python",
            required_deps=["non-existent-package"],
            keep_on_failure=True,
        ):
            pass
    # No cleanup when keep_on_failure=True, cleanup count is 0
    assert calls["cleanup"] == 0

    # Now with `keep_on_failure=False`
    calls["cleanup"] = 0
    with pytest.raises(RuntimeError):
        # FIX: Correct the typo from `temporary_venv` to `temporary_env`
        async with venvs.temporary_env(
            str(project),
            language="python",
            required_deps=["non-existent-package"],
            keep_on_failure=False,
        ):
            pass
    # Cleanup is expected on failure, so cleanup count is >= 1
    assert calls["cleanup"] >= 1


# -----------------------------
# timeout propagation & deps sanitation
# -----------------------------
@pytest.mark.asyncio
async def test_timeout_and_dependency_sanitation(
    monkeypatch: pytest.MonkeyPatch, venvs, project: pathlib.Path
):
    observed = {"timeout": None, "deps": None}

    # Mock venv.create to succeed
    def mock_venv_create(path, *args, **kwargs):
        pathlib.Path(path, "Scripts").mkdir(parents=True)
        pathlib.Path(path, "Scripts", "pip.exe").touch()
        pathlib.Path(path, "Scripts", "python.exe").touch()

    monkeypatch.setattr(venvs.venv, "create", Mock(side_effect=mock_venv_create))

    # We need to mock the internal `subprocess.run` call, which receives the timeout
    # and the sanitized dependency list.
    def mock_subprocess_run(cmd, *args, **kwargs):
        # We need to correctly parse the pip install command to get the deps
        if "pip" in cmd[0]:
            observed["deps"] = cmd[2:]
        observed["timeout"] = kwargs.get("timeout")
        # Simulate a successful run
        return Mock(stdout="mocked output", stderr="", returncode=0)

    monkeypatch.setattr(venvs.subprocess, "run", mock_subprocess_run)
    monkeypatch.setattr(venvs.utils, "cleanup_temp_dir", AsyncMock())
    monkeypatch.setattr(venvs, "audit_event", AsyncMock())

    cfg = dict(venvs.CONFIG)
    cfg.update({"venv_install_timeout_seconds": 123})
    monkeypatch.setattr(venvs, "CONFIG", cfg, raising=False)

    bad_deps = ["requests==2.31.0", " ", None, 123, "pytest"]
    # FIX: Correct the typo from `temporary_venv` to `temporary_env`
    async with venvs.temporary_env(
        str(project), language="python", required_deps=bad_deps
    ) as handle:
        assert isinstance(handle, venvs.EnvHandle)
        assert handle.exec_path

    assert observed["timeout"] == 123
    assert observed["deps"] == ["requests==2.31.0", "pytest"]


# -----------------------------
# Config fallbacks and booleans
# -----------------------------
def test_cfg_parsers(monkeypatch: pytest.MonkeyPatch, venvs):
    cfg = dict(venvs.CONFIG)
    cfg.update(
        {
            "venv_creation_retries": "3",
            "venv_retry_backoff_min": "0.5",
            "venv_retry_backoff_max": "2",
            "venv_persist": "true",
        }
    )
    monkeypatch.setattr(venvs, "CONFIG", cfg, raising=False)
    # Access private helpers to assert parse behavior
    assert venvs._cfg_int("venv_creation_retries", 1) == 3
    assert venvs._cfg_float("venv_retry_backoff_min", 0.1) == 0.5
    assert venvs._cfg_float("venv_retry_backoff_max", 1.0) == 2.0
    assert venvs._cfg_bool("venv_persist", False) is True


def test_cfg_parsers_with_bad_inputs(monkeypatch: pytest.MonkeyPatch, venvs):
    # Fix: Added a new test to handle bad input types as per the prompt.
    cfg = dict(venvs.CONFIG)
    cfg.update(
        {
            "venv_creation_retries": "not-an-int",
            "venv_retry_backoff_min": [1.0],
            "venv_persist": 123,
        }
    )
    monkeypatch.setattr(venvs, "CONFIG", cfg, raising=False)
    # The functions should now handle these bad inputs gracefully, falling back to defaults.
    assert venvs._cfg_int("venv_creation_retries", 1) == 1
    assert venvs._cfg_float("venv_retry_backoff_min", 0.1) == 0.1
    assert venvs._cfg_bool("venv_persist", False) is False


# Completed for syntactic validity.
@pytest.mark.asyncio
async def test_venv_async(venvs, project: pathlib.Path):
    """
    Tests that the temporary_env context manager works correctly in an
    asynchronous context.
    """

    # Mock venv.create to succeed
    def mock_venv_create(path, *args, **kwargs):
        pathlib.Path(path, "Scripts").mkdir(parents=True)
        pathlib.Path(path, "Scripts", "python.exe").touch()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(venvs.venv, "create", Mock(side_effect=mock_venv_create))
    monkeypatch.setattr(subprocess, "run", Mock())
    monkeypatch.setattr(venvs, "audit_event", AsyncMock())
    monkeypatch.setattr(venvs.utils, "cleanup_temp_dir", AsyncMock())

    # We must ensure the context manager is properly awaited.
    async with venvs.temporary_env(str(project), language="python") as handle:
        assert isinstance(handle, venvs.EnvHandle)
        assert handle.exec_path
        assert pathlib.Path(handle.root_path).exists()

    # After the context manager exits, cleanup should have been called.
    venvs.utils.cleanup_temp_dir.assert_awaited_once()


# -----------------------------
# File: test_generation/utils.py (accept handle or str)
# -----------------------------


def _coerce_exec_path(python_exec: Any) -> str:
    if isinstance(python_exec, (str, os.PathLike)):
        return os.fspath(python_exec)
    exec_path = getattr(python_exec, "exec_path", None)
    if isinstance(exec_path, (str, os.PathLike)):
        return os.fspath(exec_path)
    raise TypeError("expected str path or EnvHandle with .exec_path")


# FIX: Added a comma between arguments to fix the SyntaxError.
async def run_pytest_and_coverage(target_path: str, python_exec: Any):
    python_exec = _coerce_exec_path(python_exec)
    # proceed with asyncio.create_subprocess_exec([...])
