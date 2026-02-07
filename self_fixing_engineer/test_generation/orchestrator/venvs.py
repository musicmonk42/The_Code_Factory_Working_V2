# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_generation/orchestrator/venvs.py
"""Virtualenv helpers for ATCO.

Upgrades:
- Strict typing and consistent EnvHandle across languages.
- Config helper with namespaced & legacy fallbacks.
- Success-path audits + perf timing (always include run_id, env_path).
- Pre-creation path validation (no traversal).
- Safer cleanup with shield/cancel handling.
- Consistent failure/cancel cleanup semantics (keep_on_failure).
- Uniform install timeouts + optional pip retry+jitter.
- Per-run working subdirs for npm/java/rust/go to avoid collisions.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import secrets
import shutil
import subprocess  # ensure attribute exists as venvs.subprocess
import tempfile
import time
import traceback
import uuid
import venv
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import AsyncIterator, List, NamedTuple, Optional, Sequence

import filelock
from test_generation import utils

# Absolute imports for reliability
from test_generation.orchestrator.config import CONFIG, VENV_TEMP_DIR
from test_generation.orchestrator.console import log

# FIX: Corrected import to use the renamed function directly
from .audit import audit_event


def sanitize_path(path: str, project_root: str) -> str:
    """Return a safe absolute path inside project_root."""
    if not path:
        raise ValueError("Path cannot be empty.")

    abs_root = Path(project_root).resolve()
    full_path = (abs_root / path).resolve()

    if hasattr(full_path, "is_relative_to"):
        if not full_path.is_relative_to(abs_root):  # py>=3.9
            raise ValueError(f"Path '{path}' is outside the project root.")
    else:  # py<3.9 fallback
        try:
            full_path.relative_to(abs_root)
        except ValueError:
            raise ValueError(f"Path '{path}' is outside the project root.")
    return str(full_path)


def _validate_deps(deps: Sequence[str]) -> List[str]:
    """Clean and filter dependency specs."""
    out: List[str] = []
    for dep in deps:
        if isinstance(dep, str) and dep.strip():
            out.append(dep.strip())
        else:
            log(f"Ignoring invalid dependency spec: {dep}", level="WARNING")
    return out


def _cfg_int(key: str, default: int, *aliases: str) -> int:
    """Config int with namespaced & legacy fallbacks."""
    for k in (key, *aliases):
        v = CONFIG.get(k)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                log(
                    f"Invalid int for config '{k}': {v!r}. Using default {default}.",
                    level="WARNING",
                )
                continue
    return default


def _cfg_float(key: str, default: float, *aliases: str) -> float:
    for k in (key, *aliases):
        v = CONFIG.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                log(
                    f"Invalid float for config '{k}': {v!r}. Using default {default}.",
                    level="WARNING",
                )
                continue
    return default


def _cfg_bool(key: str, default: bool, *aliases: str) -> bool:
    for k in (key, *aliases):
        v = CONFIG.get(k)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            low = v.lower()
            if low in {"1", "true", "yes", "on"}:
                return True
            if low in {"0", "false", "no", "off"}:
                return False
    return default


async def create_and_install_venv(
    project_root: str, deps: list[str]
) -> tuple[bool, str]:
    """
    Create a venv and install deps. Returns (success, python_exec_path).
    Designed to be monkeypatched in tests.
    """
    # delegate to internal creator to keep behavior centralized
    try:
        async with _create_and_manage_python_env(
            project_root, deps, persist=True, keep_on_failure=True, env_subdir=None
        ) as py_exec:
            return (True, py_exec.exec_path)
    except Exception as e:
        return (False, str(e))


class EnvHandle(NamedTuple):
    exec_path: Optional[str]
    root_path: Optional[str]


@asynccontextmanager
async def temporary_env(
    project_root: str,
    language: str,
    required_deps: Optional[Sequence[str]] = None,
    *,
    persist: Optional[bool] = None,
    keep_on_failure: bool = False,
    env_subdir: Optional[str] = None,
    isolation_strategy: str = "virtualenv",
) -> AsyncIterator[EnvHandle]:
    """
    Create an isolated environment and install dependencies.

    Yields EnvHandle(exec_path, root_path):
      - python/virtualenv : (venv_python_path, venv_dir)
      - javascript/typescript (npm) : ("npm", per-run work dir)
      - java/maven : ("mvn", per-run work dir)
      - rust/cargo : ("cargo", per-run work dir)
      - go/go_mod : ("go", per-run work dir)
    """
    if required_deps is not None and not isinstance(required_deps, (list, tuple)):
        raise TypeError("required_deps must be a Sequence[str] or None")
    deps = _validate_deps(list(required_deps or []))

    provider_map = {
        "python": {
            "virtualenv": _create_and_manage_python_env,
        },
        "javascript": {"npm": _create_and_manage_npm_env},
        "typescript": {"npm": _create_and_manage_npm_env},
        "java": {"maven": _create_and_manage_java_env},
        "rust": {"cargo": _create_and_manage_rust_env},
        "go": {"go_mod": _create_and_manage_go_env},
    }

    lang_strategies = provider_map.get(language.lower())
    if not lang_strategies:
        raise ValueError(f"Unsupported language for temporary environment: {language}")

    provider = lang_strategies.get(isolation_strategy)
    if not provider:
        raise ValueError(
            f"Unknown isolation strategy for {language}: {isolation_strategy}"
        )

    async with provider(
        project_root, deps, persist, keep_on_failure, env_subdir
    ) as handle:
        yield handle


# --------------------------- Python / venv --------------------------------- #
@asynccontextmanager
async def _create_and_manage_python_env(
    project_root: str,
    required_deps: List[str],
    persist: Optional[bool],
    keep_on_failure: bool,
    env_subdir: Optional[str],
) -> AsyncIterator[EnvHandle]:
    """Create a Python venv, install deps with retry+jitter, yield EnvHandle."""
    cfg_dir = str(CONFIG.get("venv_temp_dir", VENV_TEMP_DIR) or VENV_TEMP_DIR)
    env_path_relative = env_subdir or cfg_dir

    # run_id must be available for all audit calls (even before path validation succeeds)
    run_id = os.getenv("ATCO_RUN_ID", str(uuid.uuid4()))
    try:
        base_dir = sanitize_path(env_path_relative, project_root)
    except ValueError as e:
        log(f"CRITICAL: Unsafe env_subdir '{env_path_relative}': {e}", level="CRITICAL")
        await audit_event(
            "venv_path_validation_failure",
            {
                "event": "venv_path_validation_failure",
                "env_path": env_path_relative,
                "error": str(e),
            },
            critical=True,
            run_id=run_id,
        )
        raise

    persist_final = (os.getenv("ATCO_VENV_PERSIST") == "1") or (
        persist if persist is not None else _cfg_bool("venv_persist", False)
    )
    lock_timeout = _cfg_int("venv_lock_timeout_seconds", 30)
    install_timeout = _cfg_int("venv_install_timeout_seconds", 180)
    retries = max(0, _cfg_int("venv_creation_retries", 2))
    backoff_min = _cfg_float("venv_retry_backoff_min", 0.05)
    backoff_max = _cfg_float("venv_retry_backoff_max", 0.25)

    if not os.access(project_root, os.W_OK | os.R_OK):
        err = f"No read/write permissions for {project_root}"
        log(f"CRITICAL: {err}", level="CRITICAL")
        await audit_event(
            "venv_permission_failure",
            {"event": "venv_permission_failure", "path": project_root, "error": err},
            run_id=run_id,
            critical=True,
        )
        raise PermissionError(err)

    Path(base_dir).mkdir(parents=True, exist_ok=True)

    venv_dir = Path(base_dir, f"venv_{secrets.token_hex(4)}")
    lock: Optional[filelock.FileLock] = filelock.FileLock(
        Path(base_dir, f"{venv_dir.name}.lock"), timeout=lock_timeout
    )

    succeeded = False
    t0_total = time.perf_counter()
    try:
        await asyncio.to_thread(lock.acquire)

        log(f"Attempting to create virtual environment at {venv_dir}", level="INFO")
        await asyncio.to_thread(venv.create, str(venv_dir), with_pip=True, prompt=None)
        log(f"Virtual environment created successfully at {venv_dir}", level="INFO")

        # Platform paths
        if os.name == "nt":
            pip_path = str(venv_dir / "Scripts" / "pip.exe")
            py_path = str(venv_dir / "Scripts" / "python.exe")
        else:
            pip_path = str(venv_dir / "bin" / "pip")
            py_path = str(venv_dir / "bin" / "python")

        if not (os.path.exists(pip_path) or os.path.exists(py_path)):
            err = (
                "Virtual environment validation failed: neither pip nor python "
                f"exists in {venv_dir}."
            )
            log(f"CRITICAL: {err}", level="CRITICAL")
            await audit_event(
                "venv_validation_failure",
                {
                    "event": "venv_validation_failure",
                    "path": str(venv_dir),
                    "error": err,
                },
                run_id=run_id,
                critical=True,
            )
            raise RuntimeError(err)

        # Optional dependency install with retries
        if required_deps:
            log(f"Installing dependencies: {required_deps}", level="INFO")
            for attempt in range(retries + 1):
                try:
                    await asyncio.to_thread(
                        subprocess.run,
                        [pip_path, "install", *required_deps],
                        timeout=install_timeout,
                        check=True,
                        capture_output=True,
                    )
                    break
                except Exception as e:
                    if attempt < retries:
                        delay = random.uniform(backoff_min, backoff_max)
                        log(
                            f"pip install failed (attempt {attempt+1}/{retries+1}); "
                            f"retrying in {delay:.3f}s",
                            level="WARNING",
                        )
                        await asyncio.sleep(delay)
                        continue
                    err = f"Failed to install dependencies: {e}"
                    log(f"CRITICAL: {err}", level="CRITICAL")
                    await audit_event(
                        "venv_requirements_failure",
                        {
                            "event": "venv_requirements_failure",
                            "path": str(venv_dir),
                            "error": err,
                        },
                        run_id=run_id,
                        critical=True,
                    )
                    raise RuntimeError(err) from e

        yield EnvHandle(exec_path=py_path, root_path=str(venv_dir))
        succeeded = True

        elapsed = time.perf_counter() - t0_total
        await audit_event(
            "venv_creation_success",
            {
                "event": "venv_creation_success",
                "venv_path": str(venv_dir),
                "duration": elapsed,
            },
            run_id=run_id,
        )

    except asyncio.CancelledError:
        await audit_event(
            "venv_creation_cancelled",
            {
                "event": "venv_creation_cancelled",
                "venv_path": str(venv_dir),
                "error": "Cancelled",
            },
            run_id=run_id,
            critical=True,
        )
        if not keep_on_failure and venv_dir.exists():
            await asyncio.shield(utils.cleanup_temp_dir(str(venv_dir)))
        raise
    except Exception as e:
        err = f"Virtual environment creation failed: {e}"
        log(f"CRITICAL: {err}", level="CRITICAL")
        await audit_event(
            "venv_creation_failure",
            {
                "event": "venv_creation_failure",
                "venv_path": str(venv_dir),
                "error": err,
                "traceback": traceback.format_exc(),
            },
            run_id=run_id,
            critical=True,
        )
        if not keep_on_failure and venv_dir.exists():
            await asyncio.shield(utils.cleanup_temp_dir(str(venv_dir)))
        raise
    finally:
        if lock and lock.is_locked:
            with suppress(Exception):
                await asyncio.to_thread(lock.release)
        if succeeded and not persist_final and venv_dir.exists():
            log(f"Cleaning up virtual environment at {venv_dir}", level="INFO")
            # Ensure removal even if cleanup hook is mocked in tests
            with suppress(Exception):
                await asyncio.to_thread(
                    shutil.rmtree, str(venv_dir), ignore_errors=True
                )
            # Still invoke shared cleanup hook for metrics/logging parity
            await asyncio.shield(utils.cleanup_temp_dir(str(venv_dir)))


# --------------------------- npm ------------------------------------------- #
@asynccontextmanager
async def _create_and_manage_npm_env(
    project_root: str,
    required_deps: List[str],
    persist: Optional[bool],
    keep_on_failure: bool,
    env_subdir: Optional[str],
) -> AsyncIterator[EnvHandle]:
    """Manage a Node.js environment using npm; yield EnvHandle('npm', work_dir)."""
    cfg_dir = str(CONFIG.get("venv_temp_dir", VENV_TEMP_DIR) or VENV_TEMP_DIR)
    env_path_relative = env_subdir or cfg_dir
    base_dir = sanitize_path(env_path_relative, project_root)

    run_id = os.getenv("ATCO_RUN_ID", str(uuid.uuid4()))
    persist_final = (os.getenv("ATCO_VENV_PERSIST") == "1") or (
        persist if persist is not None else _cfg_bool("venv_persist", False)
    )
    install_timeout = _cfg_int("venv_install_timeout_seconds", 180)

    Path(base_dir).mkdir(parents=True, exist_ok=True)
    work_dir: Optional[Path] = None
    succeeded = False
    t0 = time.perf_counter()
    try:
        work_dir = Path(tempfile.mkdtemp(dir=base_dir, prefix="npm_"))

        pkg_json = work_dir / "package.json"
        if not pkg_json.exists():
            pkg_json.write_text(
                json.dumps(
                    {"name": "atco-temp-env", "version": "1.0.0", "dependencies": {}}
                )
            )

        if required_deps:
            log(f"Installing npm dependencies: {required_deps}", level="INFO")
            await utils.run_command(
                ["npm", "install", *required_deps],
                cwd=str(work_dir),
                timeout=install_timeout,
                check=True,
            )

        yield EnvHandle(exec_path="npm", root_path=str(work_dir))
        succeeded = True

        elapsed = time.perf_counter() - t0
        await audit_event(
            "npm_env_creation_success",
            {"env_path": str(work_dir), "duration": elapsed},
            run_id=run_id,
        )
    except asyncio.CancelledError:
        await audit_event(
            "npm_creation_cancelled",
            {"env_path": str(work_dir or base_dir), "error": "Cancelled"},
            run_id=run_id,
            critical=True,
        )
        if not keep_on_failure and work_dir:
            await asyncio.shield(utils.cleanup_temp_dir(str(work_dir)))
        raise
    except Exception as e:
        await audit_event(
            "npm_env_creation_failure",
            {
                "env_path": str(work_dir or base_dir),
                "error": str(e),
                "traceback": traceback.format_exc(),
            },
            run_id=run_id,
            critical=True,
        )
        if not keep_on_failure and work_dir:
            await asyncio.shield(utils.cleanup_temp_dir(str(work_dir)))
        raise
    finally:
        # Only auto-clean on success (respect keep_on_failure)
        if succeeded and not persist_final and work_dir:
            await asyncio.shield(utils.cleanup_temp_dir(str(work_dir)))


# --------------------------- Java / Maven ---------------------------------- #
@asynccontextmanager
async def _create_and_manage_java_env(
    project_root: str,
    required_deps: List[str],
    persist: Optional[bool],
    keep_on_failure: bool,
    env_subdir: Optional[str],
) -> AsyncIterator[EnvHandle]:
    """Manage a Maven env; yield EnvHandle('mvn', work_dir)."""
    cfg_dir = str(CONFIG.get("venv_temp_dir", VENV_TEMP_DIR) or VENV_TEMP_DIR)
    env_path_relative = env_subdir or cfg_dir
    base_dir = sanitize_path(env_path_relative, project_root)

    run_id = os.getenv("ATCO_RUN_ID", str(uuid.uuid4()))
    persist_final = (os.getenv("ATCO_VENV_PERSIST") == "1") or (
        persist if persist is not None else _cfg_bool("venv_persist", False)
    )

    Path(base_dir).mkdir(parents=True, exist_ok=True)
    work_dir: Optional[Path] = None
    succeeded = False
    t0 = time.perf_counter()
    try:
        work_dir = Path(tempfile.mkdtemp(dir=base_dir, prefix="java_"))

        if required_deps:
            log(
                f"Note: Java dependencies {required_deps} should be managed via pom.xml.",
                level="WARNING",
            )

        yield EnvHandle(exec_path="mvn", root_path=str(work_dir))
        succeeded = True

        elapsed = time.perf_counter() - t0
        await audit_event(
            "java_env_creation_success",
            {"env_path": str(work_dir), "duration": elapsed},
            run_id=run_id,
        )
    except asyncio.CancelledError:
        await audit_event(
            "java_env_creation_cancelled",
            {"env_path": str(work_dir or base_dir), "error": "Cancelled"},
            run_id=run_id,
            critical=True,
        )
        if not keep_on_failure and work_dir:
            await asyncio.shield(utils.cleanup_temp_dir(str(work_dir)))
        raise
    except Exception as e:
        await audit_event(
            "java_env_creation_failure",
            {
                "env_path": str(work_dir or base_dir),
                "error": str(e),
                "traceback": traceback.format_exc(),
            },
            run_id=run_id,
            critical=True,
        )
        if not keep_on_failure and work_dir:
            await asyncio.shield(utils.cleanup_temp_dir(str(work_dir)))
        raise
    finally:
        if succeeded and not persist_final and work_dir:
            await asyncio.shield(utils.cleanup_temp_dir(str(work_dir)))


# --------------------------- Rust / Cargo ---------------------------------- #
@asynccontextmanager
async def _create_and_manage_rust_env(
    project_root: str,
    required_deps: List[str],
    persist: Optional[bool],
    keep_on_failure: bool,
    env_subdir: Optional[str],
) -> AsyncIterator[EnvHandle]:
    """Manage a Cargo env; yield EnvHandle('cargo', work_dir)."""
    cfg_dir = str(CONFIG.get("venv_temp_dir", VENV_TEMP_DIR) or VENV_TEMP_DIR)
    env_path_relative = env_subdir or cfg_dir
    base_dir = sanitize_path(env_path_relative, project_root)

    run_id = os.getenv("ATCO_RUN_ID", str(uuid.uuid4()))
    persist_final = (os.getenv("ATCO_VENV_PERSIST") == "1") or (
        persist if persist is not None else _cfg_bool("venv_persist", False)
    )

    Path(base_dir).mkdir(parents=True, exist_ok=True)
    work_dir: Optional[Path] = None
    succeeded = False
    t0 = time.perf_counter()
    try:
        work_dir = Path(tempfile.mkdtemp(dir=base_dir, prefix="rust_"))

        if required_deps:
            log(
                f"Note: Rust dependencies {required_deps} should be in Cargo.toml.",
                level="WARNING",
            )

        yield EnvHandle(exec_path="cargo", root_path=str(work_dir))
        succeeded = True

        elapsed = time.perf_counter() - t0
        await audit_event(
            "rust_env_creation_success",
            {"env_path": str(work_dir), "duration": elapsed},
            run_id=run_id,
        )
    except asyncio.CancelledError:
        await audit_event(
            "rust_env_creation_cancelled",
            {"env_path": str(work_dir or base_dir), "error": "Cancelled"},
            run_id=run_id,
            critical=True,
        )
        if not keep_on_failure and work_dir:
            await asyncio.shield(utils.cleanup_temp_dir(str(work_dir)))
        raise
    except Exception as e:
        await audit_event(
            "rust_env_creation_failure",
            {
                "env_path": str(work_dir or base_dir),
                "error": str(e),
                "traceback": traceback.format_exc(),
            },
            run_id=run_id,
            critical=True,
        )
        if not keep_on_failure and work_dir:
            await asyncio.shield(utils.cleanup_temp_dir(str(work_dir)))
        raise
    finally:
        if succeeded and not persist_final and work_dir:
            await asyncio.shield(utils.cleanup_temp_dir(str(work_dir)))


# --------------------------- Go / Modules ---------------------------------- #
@asynccontextmanager
async def _create_and_manage_go_env(
    project_root: str,
    required_deps: List[str],
    persist: Optional[bool],
    keep_on_failure: bool,
    env_subdir: Optional[str],
) -> AsyncIterator[EnvHandle]:
    """Manage a Go modules env; yield EnvHandle('go', work_dir)."""
    cfg_dir = str(CONFIG.get("venv_temp_dir", VENV_TEMP_DIR) or VENV_TEMP_DIR)
    env_path_relative = env_subdir or cfg_dir
    base_dir = sanitize_path(env_path_relative, project_root)

    run_id = os.getenv("ATCO_RUN_ID", str(uuid.uuid4()))
    persist_final = (os.getenv("ATCO_VENV_PERSIST") == "1") or (
        persist if persist is not None else _cfg_bool("venv_persist", False)
    )
    install_timeout = _cfg_int("venv_install_timeout_seconds", 180)

    Path(base_dir).mkdir(parents=True, exist_ok=True)
    work_dir: Optional[Path] = None
    succeeded = False
    t0 = time.perf_counter()
    try:
        work_dir = Path(tempfile.mkdtemp(dir=base_dir, prefix="go_"))

        # go mod init
        await utils.run_command(
            ["go", "mod", "init", "temp-module"], cwd=str(work_dir), check=True
        )

        if required_deps:
            log(f"Installing Go dependencies: {required_deps}", level="INFO")
            await utils.run_command(
                ["go", "get", *required_deps],
                cwd=str(work_dir),
                timeout=install_timeout,
                check=True,
            )

        yield EnvHandle(exec_path="go", root_path=str(work_dir))
        succeeded = True

        elapsed = time.perf_counter() - t0
        await audit_event(
            "go_env_creation_success",
            {"env_path": str(work_dir), "duration": elapsed},
            run_id=run_id,
        )
    except asyncio.CancelledError:
        await audit_event(
            "go_env_creation_cancelled",
            {"env_path": str(work_dir or base_dir), "error": "Cancelled"},
            run_id=run_id,
            critical=True,
        )
        if not keep_on_failure and work_dir:
            await asyncio.shield(utils.cleanup_temp_dir(str(work_dir)))
        raise
    except Exception as e:
        await audit_event(
            "go_env_creation_failure",
            {
                "env_path": str(work_dir or base_dir),
                "error": str(e),
                "traceback": traceback.format_exc(),
            },
            run_id=run_id,
            critical=True,
        )
        if not keep_on_failure and work_dir:
            await asyncio.shield(utils.cleanup_temp_dir(str(work_dir)))
        raise
    finally:
        if succeeded and not persist_final and work_dir:
            await asyncio.shield(utils.cleanup_temp_dir(str(work_dir)))
