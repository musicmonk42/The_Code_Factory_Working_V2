import os
import asyncio
import json
import shutil
import logging
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Callable, List

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

PLUGIN_MANIFEST = {
    "name": "JestTestRunnerPlugin",
    "version": "0.4.3",  # Fix: correct path resolution for temp projects
    "description": "Provides a robust Jest test runner for JavaScript/TypeScript with coverage, TS/ESM support, timeouts, bounded project discovery, and safer defaults.",
    "author": "Self-Fixing Engineer Team",
    "capabilities": ["test_execution", "coverage_analysis"],
    "permissions_required": ["filesystem_read", "filesystem_write", "process_execution"],
    "compatibility": {
        "min_sim_runner_version": "1.0.0",
        "max_sim_runner_version": "2.0.0"
    },
    "entry_points": {
        "run_jest_tests": {
            "description": "Executes Jest tests for a given JavaScript/TypeScript file.",
            "parameters": ["test_file_path", "target_identifier", "project_root", "temp_coverage_report_path_relative"]
        }
    },
    "health_check": "plugin_health",
    "api_version": "v1",
    "license": "MIT",
    "homepage": "",
    "tags": ["jest", "javascript", "typescript", "test_runner"]
}

# ---------------- Utilities ----------------

def _shutil_which(cmd: str) -> Optional[str]:
    try:
        from shutil import which
        return which(cmd)
    except Exception:
        return None

async def _which(cmd: str) -> Optional[str]:
    """
    Prefer shutil.which; fall back to shelling out to which/where.
    """
    path = _shutil_which(cmd)
    if path:
        return path
    try:
        proc = await asyncio.create_subprocess_exec(
            "which" if os.name != "nt" else "where",
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip().splitlines()[0] if proc.returncode == 0 else None
    except Exception as e:
        logger.debug(f"Error finding executable '{cmd}': {e}")
        return None

def _is_path_under(base: Path, child: Path) -> bool:
    """
    Ensure 'child' path is within the 'base' directory tree.
    """
    try:
        base_res = base.resolve()
        child_res = child.resolve()
        return child_res == base_res or str(child_res).startswith(str(base_res) + os.sep)
    except Exception:
        return False

def _bound_search_for_package_json(start_dir: Path, stop_at: Path) -> Optional[Path]:
    """
    Search upwards for a package.json starting at 'start_dir' but never above 'stop_at'.
    Returns the directory containing package.json, or None.
    """
    cur = start_dir.resolve()
    stop = stop_at.resolve()
    while True:
        pkg = cur / "package.json"
        if pkg.exists():
            return cur
        if cur == stop:
            break
        if cur.parent == cur:
            break
        cur = cur.parent
    return None

def _copytree_compat(src: Path, dst: Path) -> None:
    """
    Copy a directory tree; allow destination to pre-exist (Py<3.8 fallback).
    """
    try:
        shutil.copytree(src, dst, dirs_exist_ok=True)  # type: ignore[arg-type]
    except TypeError:
        # Python < 3.8 fallback
        for root, dirs, files in os.walk(src):
            rel = Path(root).relative_to(src)
            target_dir = dst / rel
            target_dir.mkdir(parents=True, exist_ok=True)
            for f in files:
                shutil.copy2(Path(root) / f, target_dir / f)

async def _detect_package_manager() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Detect available Node.js package managers in PATH.
    Returns (npx_path, npm_path, yarn_path).
    """
    npx_path = await _which("npx")
    npm_path = await _which("npm")
    yarn_path = await _which("yarn")
    return npx_path, npm_path, yarn_path

async def _get_package_version(cwd: str, package: str) -> Optional[str]:
    """
    Retrieve version of a Node.js package declared in package.json within cwd or via npx for jest.
    """
    package_json_path = os.path.join(cwd, "package.json")
    if os.path.exists(package_json_path):
        try:
            with open(package_json_path, 'r', encoding="utf-8") as f:
                package_json = json.load(f)
            version = package_json.get("devDependencies", {}).get(package) or \
                      package_json.get("dependencies", {}).get(package)
            if version:
                return str(version).lstrip("^~=<>")
        except Exception as e:
            logger.debug(f"Could not parse package.json for {package} version in {cwd}: {e}")

    if package == "jest":
        try:
            npx = await _which("npx")
            if npx:
                proc = await asyncio.create_subprocess_exec(
                    npx, "jest", "--version",
                    cwd=cwd,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                return stdout.decode().strip() if proc.returncode == 0 else None
        except Exception as e:
            logger.debug(f"Error running npx jest --version in {cwd}: {e}")
    return None

def _read_package_json_field(cwd: str, field: str) -> Optional[Any]:
    """
    Read a top-level field from package.json in cwd, if present.
    """
    path = os.path.join(cwd, "package.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(field)
    except Exception:
        return None

def _cap_text_tail(s: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = s.encode(errors="replace")
    if len(encoded) <= max_bytes:
        return s
    return encoded[-max_bytes:].decode(errors="replace")

async def _install_packages(cwd: str, packages: List[str], npm_path: Optional[str], yarn_path: Optional[str]) -> Tuple[bool, str]:
    """
    Install Node.js packages using npm (preferred) or yarn.
    - npm: 'npm ci' if package-lock.json exists and no packages specified, else 'npm install [packages]'
    - yarn: 'yarn install' if no packages specified, else 'yarn add [packages]'
    """
    manager_name = ""
    install_command: List[str] = []

    if npm_path:
        manager_name = "npm"
        lockfile = os.path.join(cwd, "package-lock.json")
        if not packages:
            if os.path.exists(lockfile):
                install_command = [npm_path, "ci", "--no-audit", "--no-fund"]
            else:
                install_command = [npm_path, "install", "--no-audit", "--no-fund"]
        else:
            install_command = [npm_path, "install", "--no-audit", "--no-fund"] + packages
    elif yarn_path:
        manager_name = "yarn"
        if not packages:
            install_command = [yarn_path, "install", "--silent"]
        else:
            install_command = [yarn_path, "add"] + packages
    else:
        error_msg = "Neither npm nor yarn found in PATH. Cannot install packages."
        logger.error(error_msg)
        return False, error_msg

    logger.info(f"Installing packages using {manager_name}: {' '.join(install_command)} in {cwd}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *install_command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            logger.info(f"Successfully installed packages using {manager_name}")
            return True, stdout.decode(errors="replace").strip()
        else:
            error_msg = f"Failed to install packages using {manager_name}\nSTDOUT: {stdout.decode(errors='replace').strip()}\nSTDERR: {stderr.decode(errors='replace').strip()}"
            logger.error(error_msg)
            return False, error_msg
    except FileNotFoundError:
        error_msg = f"{manager_name} executable not found."
        logger.error(error_msg)
        return False, error_msg
    except Exception as e:
        error_msg = f"Unexpected error installing packages with {manager_name}: {e}"
        logger.error(error_msg, exc_info=True)
        return False, error_msg

# ---------------- Health Check ----------------

async def plugin_health() -> Dict[str, Any]:
    """
    Health check for Node.js/jest environment.
    Honors JEST_RUNNER_PROJECT_ROOT env var to determine project context for version checks.
    """
    status = "ok"
    details: List[str] = []

    npx_path, npm_path, yarn_path = await _detect_package_manager()

    if not npx_path:
        status = "degraded"
        details.append("npx not found in PATH. Jest execution may fail.")
    else:
        details.append(f"npx detected: {npx_path}")

    if npm_path:
        details.append(f"npm detected: {npm_path}")
    elif yarn_path:
        details.append(f"yarn detected: {yarn_path}")
    else:
        status = "degraded"
        details.append("npm or yarn not found in PATH. Package installation for Jest may fail.")

    node_path = await _which("node")
    if node_path:
        try:
            proc = await asyncio.create_subprocess_exec(
                node_path, "--version",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            details.append(f"Node.js detected: {stdout.decode().strip()}")
        except Exception as e:
            status = "degraded"
            details.append(f"Error running node --version: {e}")
    else:
        status = "degraded"
        details.append("Node.js not found in PATH.")

    # Project-aware version checks
    project_root_env = os.getenv("JEST_RUNNER_PROJECT_ROOT", os.getcwd())
    jest_version = await _get_package_version(project_root_env, "jest")
    if jest_version:
        details.append(f"Jest detected in project: version {jest_version}")
    else:
        details.append("Jest not detected in project package.json; version check failed.")

    ts_jest_version = await _get_package_version(project_root_env, "ts-jest")
    if ts_jest_version:
        details.append(f"ts-jest detected in project: version {ts_jest_version}")
    else:
        details.append("ts-jest not detected in project package.json; TypeScript support may be limited.")

    logger.info(f"Jest plugin health: {status}")
    return {"status": status, "details": details}

# ---------------- Main Functionality ----------------

async def run_jest_tests(
    test_file_path: str,
    target_identifier: str,
    project_root: str,
    temp_coverage_report_path_relative: str,
    **kwargs
) -> Dict[str, Any]:
    """
    Executes Jest tests and analyzes coverage.
    Safety: bounds package.json discovery to project_root, validates all paths under project_root, and uses JSON output file instead of parsing stdout.
    Kwargs:
      - extra_jest_args: list[str]
      - timeout_seconds: int (default 600)
      - log_max_bytes: int (default 262144)
      - log_artifact_path_relative: Optional[str] (saved under project_root if provided)
    """
    # Resolve and validate core paths
    project_root_path = Path(project_root).resolve()
    full_test_file_path = (project_root_path / test_file_path).resolve()
    full_target_path = (project_root_path / target_identifier).resolve()
    full_coverage_report_path = (project_root_path / temp_coverage_report_path_relative).resolve()

    result: Dict[str, Any] = {
        "success": False,
        "coverage_increase_percent": 0.0,  # retains original field name for compatibility
        "reason": "",
        "raw_log": "",
        "temp_dirs_used": [],
        "jest_version": "N/A",
        "ts_jest_version": "N/A",
        "numTotalTests": 0,
        "numPassedTests": 0,
        "numFailedTests": 0,
        "numPendingTests": 0,
        "coverage_report_path": str(full_coverage_report_path),
        "jest_project_root": ""
    }

    # Path safety validations
    for label, path in [("test_file_path", full_test_file_path),
                        ("coverage_output_path", full_coverage_report_path)]:
        if not _is_path_under(project_root_path, path):
            msg = f"Invalid {label} outside project_root: {path}"
            logger.error(msg)
            result["reason"] = msg
            return result

    # Validate test file exists
    if not full_test_file_path.exists():
        msg = f"Jest test file not found: {full_test_file_path}"
        logger.error(msg)
        result["reason"] = msg
        return result

    # Target validation: if present, must be under project_root; may not exist (coverage then limited)
    collect_coverage_from: Optional[Path] = None
    if str(target_identifier).strip():
        if not _is_path_under(project_root_path, full_target_path):
            msg = f"Invalid target_identifier outside project_root: {full_target_path}"
            logger.error(msg)
            result["reason"] = msg
            return result
        if full_target_path.exists():
            collect_coverage_from = full_target_path

    # Detect package managers
    npx_path, npm_path, yarn_path = await _detect_package_manager()
    if not npx_path:
        result["reason"] = "npx not found in PATH. Cannot run Jest tests."
        logger.error(result["reason"])
        return result

    # Determine Jest project root, bounded to project_root
    start_dir = full_test_file_path.parent
    jest_project_root_path = _bound_search_for_package_json(start_dir, project_root_path) or project_root_path

    # If no package.json within project_root or node_modules missing, set up a temp Jest environment
    temp_dir_obj = None
    using_temp_project = False
    temp_target_path_abs: Optional[Path] = None  # New absolute path to copied target in temp project (if any)
    if not (jest_project_root_path / "package.json").exists() or not (jest_project_root_path / "node_modules").exists():
        using_temp_project = True
        artifacts_dir = project_root_path / "atco_artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        temp_dir_obj = tempfile.TemporaryDirectory(prefix="jest_run_", dir=str(artifacts_dir))
        temp_jest_dir = Path(temp_dir_obj.name)
        result["temp_dirs_used"].append(str(temp_jest_dir))

        # Preserve the test file's relative directory under the temp project
        rel_test = full_test_file_path.relative_to(project_root_path)
        dest_test_path = temp_jest_dir / rel_test
        dest_test_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(full_test_file_path, dest_test_path)

        # Copy common source roots to maintain relative imports
        src_dir = project_root_path / "src"
        lib_dir = project_root_path / "lib"
        if src_dir.exists():
            _copytree_compat(src_dir, temp_jest_dir / "src")
        if lib_dir.exists():
            _copytree_compat(lib_dir, temp_jest_dir / "lib")

        # Copy the top-level directory containing the target if it's outside src/lib (avoid node_modules/.git/etc.)
        if collect_coverage_from:
            rel_target = collect_coverage_from.relative_to(project_root_path)
            top = rel_target.parts[0] if rel_target.parts else None
            skip_tops = {"node_modules", ".git", "atco_artifacts", "coverage"}
            if top and top not in {"src", "lib"} | skip_tops:
                top_src = project_root_path / top
                if top_src.exists() and top_src.is_dir():
                    _copytree_compat(top_src, temp_jest_dir / top)
            # Ensure at least the target file exists in temp (in case src/lib copy wasn't sufficient)
            temp_target_path_abs = temp_jest_dir / rel_target
            temp_target_path_abs.parent.mkdir(parents=True, exist_ok=True)
            try:
                if not temp_target_path_abs.exists():
                    shutil.copy2(collect_coverage_from, temp_target_path_abs)
            except Exception as e:
                logger.debug(f"Could not copy target file into temp project: {e}")

        # Determine if TS tests are used
        is_ts_test = full_test_file_path.suffix.lower() in (".ts", ".tsx")

        # Minimal package.json with aligned versions (Jest 29 + ts-jest 29)
        package_json_content = {
            "name": "atco-jest-temp",
            "version": "1.0.0",
            "private": True,
            "description": "Temporary Jest environment",
            "main": "index.js",
            "type": "commonjs",
            "scripts": {"test": "jest"},
            "devDependencies": {
                "jest": "^29.7.0",
                "jest-junit": "^16.0.0",
                "ts-jest": "^29.1.1",
                "@types/jest": "^29.5.12"
            }
        }
        (temp_jest_dir / 'package.json').write_text(json.dumps(package_json_content, indent=2), encoding="utf-8")

        # tsconfig.json for TS runs
        if is_ts_test:
            tsconfig = {
                "compilerOptions": {
                    "target": "ES2019",
                    "module": "commonjs",
                    "esModuleInterop": True,
                    "jsx": "react-jsx",
                    "sourceMap": True,
                    "skipLibCheck": True
                },
                "include": ["**/*.ts", "**/*.tsx"]
            }
            (temp_jest_dir / "tsconfig.json").write_text(json.dumps(tsconfig, indent=2), encoding="utf-8")

        # ESM awareness (if later toggled to module)
        esm = False  # temp defaults to commonjs

        # jest.config.js (ts-jest preset if TS; ESM tweak if esm)
        jest_config = {
            "testEnvironment": "node",
            "collectCoverage": True,
            "coverageReporters": ["json", "lcov", "text"],
            "coverageDirectory": "<rootDir>/coverage",
            "roots": ["<rootDir>"],
            "moduleFileExtensions": ["ts", "tsx", "js", "jsx", "json", "node"],
            "transform": {}
        }
        if is_ts_test:
            # ts-jest transform; ESM-aware config if needed
            jest_config["preset"] = "ts-jest"
            jest_config["transform"] = {
                "^.+\\.(ts|tsx)$": "ts-jest"
            }
            if esm:
                jest_config["extensionsToTreatAsEsm"] = [".ts", ".tsx", ".jsx"]
                jest_config["globals"] = {"ts-jest": {"useESM": True}}

        # Save config
        (temp_jest_dir / 'jest.config.js').write_text(
            "module.exports = " + json.dumps(jest_config, indent=2),
            encoding="utf-8"
        )

        # Install dependencies in temp project
        success, msg = await _install_packages(str(temp_jest_dir), [], npm_path, yarn_path)
        if not success:
            result["reason"] = f"Failed to setup Jest environment: {msg}"
            # Cleanup temp dir via context manager in finally
            return result

        # Switch execution context to temp project and updated paths
        jest_project_root_path = temp_jest_dir
        full_test_file_path = dest_test_path

        # FIXED: Update collect_coverage_from to point inside temp project if we have a target
        if collect_coverage_from:
            try:
                rel_target = collect_coverage_from.relative_to(project_root_path)
                temp_target_path_abs = temp_jest_dir / rel_target
                if temp_target_path_abs.exists():
                    collect_coverage_from = temp_target_path_abs
                else:
                    # If target doesn't exist in temp project, disable coverage collection
                    collect_coverage_from = None
            except Exception:
                collect_coverage_from = None

    # Finalize versions and ESM detection for existing or temp project
    result["jest_project_root"] = str(jest_project_root_path)
    result["jest_version"] = await _get_package_version(str(jest_project_root_path), "jest") or "Unknown"
    result["ts_jest_version"] = await _get_package_version(str(jest_project_root_path), "ts-jest") or "Unknown"
    esm_type = _read_package_json_field(str(jest_project_root_path), "type")
    is_esm = (esm_type == "module")

    # Prepare JSON results output file path inside jest project root
    jest_results_json_path = jest_project_root_path / "jest-results.json"
    jest_results_json_cli = str(jest_results_json_path).replace("\\", "/")  # normalize for CLI

    # JUnit reporter availability: only include when temp project or when present
    use_junit_reporter = using_temp_project or (jest_project_root_path / "node_modules" / "jest-junit").exists()
    junit_xml_path = (jest_project_root_path / "jest-junit.xml") if use_junit_reporter else None

    # Build Jest command
    npx = npx_path
    # Normalize paths for Jest CLI on Windows
    test_arg = str(full_test_file_path).replace("\\", "/")
    cmd: List[str] = [
        npx, "jest",
        test_arg,
        "--coverage",
        "--reporters=default",
        "--json",
        f"--outputFile={jest_results_json_cli}",
        "--silent",
        "--forceExit"
    ]
    if use_junit_reporter:
        # Only add the reporter; configure its output via environment to avoid --outputFile collision
        cmd += ["--reporters=jest-junit"]

    # Collect coverage from target if available
    if collect_coverage_from:
        rel_target_for_cli = str(collect_coverage_from.relative_to(jest_project_root_path)).replace("\\", "/")
        cmd.append(f"--collectCoverageFrom={rel_target_for_cli}")

    # Add --config if a jest.config.js exists in project root
    jest_config_js = jest_project_root_path / "jest.config.js"
    if jest_config_js.exists():
        cmd.append(f"--config={str(jest_config_js)}")

    # Extra args
    extra_args = kwargs.get("extra_jest_args", []) or []
    if extra_args:
        cmd.extend([str(a) for a in extra_args])

    # Runtime controls
    timeout_seconds = int(kwargs.get("timeout_seconds", 600))
    log_max_bytes = int(kwargs.get("log_max_bytes", 262_144))
    log_artifact_rel = kwargs.get("log_artifact_path_relative")
    log_artifact_path: Optional[Path] = (project_root_path / log_artifact_rel).resolve() if log_artifact_rel else None
    if log_artifact_path and not _is_path_under(project_root_path, log_artifact_path):
        logger.error(f"Invalid log artifact path outside project_root: {log_artifact_path}")
        log_artifact_path = None

    logger.info(f"Running Jest command: {' '.join(cmd)} in {jest_project_root_path}")
    stdout_data = ""
    stderr_data = ""
    proc: Optional[asyncio.subprocess.Process] = None

    # Prepare environment, including jest-junit output path (if used)
    env = os.environ.copy()
    if junit_xml_path:
        env["JEST_JUNIT_OUTPUT"] = str(junit_xml_path)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(jest_project_root_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning(f"Jest process timed out after {timeout_seconds}s; terminating...")
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
            stdout_bytes = b""
            stderr_bytes = f"Process timed out after {timeout_seconds}s".encode()

        stdout_data = (stdout_bytes or b"").decode(errors="replace")
        stderr_data = (stderr_bytes or b"").decode(errors="replace")

        full_log = f"Jest STDOUT:\n{stdout_data}\n\nJest STDERR:\n{stderr_data}"
        # Optionally persist full logs
        if log_artifact_path:
            try:
                log_artifact_path.parent.mkdir(parents=True, exist_ok=True)
                log_artifact_path.write_text(full_log, encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to write log artifact to {log_artifact_path}: {e}")

        # Cap logs in result
        result["raw_log"] = _cap_text_tail(full_log, log_max_bytes)

        # Prefer the JSON output file for results
        jest_json_output = None
        if jest_results_json_path.exists():
            try:
                with open(jest_results_json_path, "r", encoding="utf-8") as jf:
                    jest_json_output = json.load(jf)
            except Exception as e:
                logger.warning(f"Failed to parse Jest JSON results at {jest_results_json_path}: {e}")

        # Determine success, counters
        if jest_json_output and isinstance(jest_json_output, dict):
            result["numTotalTests"] = int(jest_json_output.get("numTotalTests", 0))
            result["numPassedTests"] = int(jest_json_output.get("numPassedTests", 0))
            result["numFailedTests"] = int(jest_json_output.get("numFailedTests", 0))
            result["numPendingTests"] = int(jest_json_output.get("numPendingTests", 0))
            if bool(jest_json_output.get("success", False)):
                result["success"] = True
                result["reason"] = f"Jest tests passed for {target_identifier}."
            else:
                result["success"] = False
                result["reason"] = f"Jest tests failed for {target_identifier}: {result['numFailedTests']} failed."
        else:
            # Fallback to exit code
            if proc and proc.returncode == 0:
                result["success"] = True
                result["reason"] = f"Jest tests passed for {target_identifier}."
            else:
                result["success"] = False
                result["reason"] = f"Jest tests failed for {target_identifier}. See raw log for details."

        # Coverage extraction
        coverage_final_json_path = jest_project_root_path / "coverage" / "coverage-final.json"
        if coverage_final_json_path.exists():
            try:
                with open(coverage_final_json_path, "r", encoding="utf-8") as f:
                    coverage_data = json.load(f)
                # Try temp target path first (if used), then original absolute path, then original identifier
                keys_to_try: List[str] = []
                if temp_target_path_abs:
                    keys_to_try.append(str(temp_target_path_abs))
                keys_to_try.append(str(full_target_path))
                if str(target_identifier).strip():
                    keys_to_try.append(target_identifier)
                file_cov = None
                for k in keys_to_try:
                    file_cov = coverage_data.get(k)
                    if file_cov:
                        break
                if file_cov and isinstance(file_cov, dict):
                    lines = file_cov.get("lines", {})
                    pct = lines.get("pct")
                    if isinstance(pct, (int, float)):
                        result["coverage_increase_percent"] = float(pct)
            except Exception as e:
                logger.error(f"Failed to parse coverage-final.json: {e}", exc_info=True)
        elif jest_json_output and isinstance(jest_json_output, dict):
            # Fallback: extract coverage if present in JSON output structure
            cov_map = jest_json_output.get("coverageMap")
            if isinstance(cov_map, dict):
                for entry in cov_map.values():
                    entry_path = entry.get("path")
                    if not isinstance(entry_path, str):
                        continue
                    if (temp_target_path_abs and entry_path == str(temp_target_path_abs)) or entry_path == str(full_target_path):
                        lines = entry.get("lines", {})
                        pct = lines.get("pct")
                        if isinstance(pct, (int, float)):
                            result["coverage_increase_percent"] = float(pct)
                            break

        # Save coverage report JSON (either coverage-final.json or coverageMap)
        try:
            full_coverage_report_path.parent.mkdir(parents=True, exist_ok=True)
            if coverage_final_json_path.exists() and result["coverage_increase_percent"] >= 0.0:
                shutil.copyfile(str(coverage_final_json_path), str(full_coverage_report_path))
                logger.info(f"Jest coverage report saved to {full_coverage_report_path}")
            elif jest_json_output and isinstance(jest_json_output, dict) and "coverageMap" in jest_json_output:
                with open(full_coverage_report_path, "w", encoding="utf-8") as outf:
                    json.dump(jest_json_output["coverageMap"], outf, indent=2)
                logger.info(f"Jest coverage map saved to {full_coverage_report_path}")
            else:
                logger.warning("No coverage report data available to save.")
        except Exception as e:
            logger.warning(f"Failed to write coverage JSON to {full_coverage_report_path}: {e}")
            # keep going

    except FileNotFoundError:
        result["reason"] = "npx or jest not found in PATH. Please ensure Node.js and Jest are installed."
        logger.error(result["reason"])
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        raise
    except Exception as e:
        result["reason"] = f"An unexpected error occurred during Jest execution: {e}"
        result["raw_log"] = _cap_text_tail(f"Jest STDOUT:\n{stdout_data}\nJest STDERR:\n{stderr_data}", int(kwargs.get("log_max_bytes", 262_144)))
        logger.error(result["reason"], exc_info=True)
    finally:
        if 'temp_dir_obj' in locals() and temp_dir_obj:
            try:
                temp_dir_obj.cleanup()
            except Exception:
                pass

    return result

# ---------------- Registration ----------------

def register_plugin_entrypoints(register_func: Callable):
    """
    Registers this plugin's test runner function with the core simulation system.
    """
    logger.info("Registering JestTestRunnerPlugin entrypoints...")
    register_func(
        language_or_framework="javascript",
        runner_info={
            "command": ["npx", "jest"],  # Placeholder; actual execution uses run_jest_tests
            "extensions": [".js", ".jsx", ".mjs"],
            "test_discovery": ["test", "spec"],
            "runner_function": run_jest_tests
        }
    )
    register_func(
        language_or_framework="typescript",
        runner_info={
            "command": ["npx", "jest"],
            "extensions": [".ts", ".tsx"],
            "test_discovery": ["test", "spec"],
            "runner_function": run_jest_tests
        }
    )

if __name__ == "__main__":
    async def _mock_register_test_runner(lang_or_framework: str, runner_info: Dict[str, Any]):
        print(f"Mocked registration for {lang_or_framework}: {runner_info}")

    _mock_runners = {}
    register_plugin_entrypoints(_mock_register_test_runner)
    print("\n--- Registered Runners ---")
    for lang, info in _mock_runners.items():
        print(f"Language: {lang}")
        print(f"  Command: {info.get('command')}")
        print(f"  Extensions: {info.get('extensions')}")
        print(f"  Runner Function: {info.get('runner_function').__name__}")
        print("-" * 20)

    async def main_test_run():
        print("\n--- Running Mock Jest Test (JS) ---")
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a dummy JS source file
            dummy_source_path_relative = os.path.join("src", "sum.js")
            os.makedirs(os.path.join(temp_dir, "src"), exist_ok=True)
            with open(os.path.join(temp_dir, dummy_source_path_relative), 'w', encoding="utf-8") as f:
                f.write("function sum(a, b) { return a + b; }\nmodule.exports = sum;")

            # Create a dummy JS test file (preserve directory)
            dummy_test_path_relative = os.path.join("tests", "sum.test.js")
            os.makedirs(os.path.join(temp_dir, "tests"), exist_ok=True)
            with open(os.path.join(temp_dir, dummy_test_path_relative), 'w', encoding="utf-8") as f:
                f.write("""
                    const sum = require('../src/sum');
                    describe('sum', () => {
                        test('adds 1 + 2 to equal 3', () => {
                            expect(sum(1, 2)).toBe(3);
                        });
                    });
                """)

            temp_coverage_report_path_relative = os.path.join("atco_artifacts", "coverage_reports", "jest_coverage_output.json")
            os.makedirs(os.path.join(temp_dir, "atco_artifacts", "coverage_reports"), exist_ok=True)

            res = await run_jest_tests(
                test_file_path=dummy_test_path_relative,
                target_identifier=dummy_source_path_relative,
                project_root=temp_dir,
                temp_coverage_report_path_relative=temp_coverage_report_path_relative,
                extra_jest_args=["--verbose"],
                timeout_seconds=120,
                log_max_bytes=64_000,
                log_artifact_path_relative=os.path.join("atco_artifacts", "logs", "jest_run.log")
            )

            print(f"\nTest Result: {'PASS' if res['success'] else 'FAIL'}")
            print(f"Coverage: {res['coverage_increase_percent']:.2f}%")
            print(f"Reason: {res['reason']}")
            print("Execution Log (tail):\n", res["raw_log"][-1000:])
            print(f"Temporary Directories Used: {res['temp_dirs_used']}")
            print(f"Jest Version: {res.get('jest_version', 'N/A')}")
            print(f"ts-Jest Version: {res.get('ts_jest_version', 'N/A')}")
            full_cov = os.path.join(temp_dir, temp_coverage_report_path_relative)
            print(f"Coverage report exists: {os.path.exists(full_cov)} at {full_cov}")

        print("\n--- Running Mock Jest Test (TS) ---")
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a dummy TS source file
            dummy_source_path_relative = os.path.join("src", "sum.ts")
            os.makedirs(os.path.join(temp_dir, "src"), exist_ok=True)
            with open(os.path.join(temp_dir, dummy_source_path_relative), 'w', encoding="utf-8") as f:
                f.write("export function sum(a: number, b: number): number { return a + b; }")

            # Create a dummy TS test file
            dummy_test_path_relative = os.path.join("tests", "sum.test.ts")
            os.makedirs(os.path.join(temp_dir, "tests"), exist_ok=True)
            with open(os.path.join(temp_dir, "tests", "sum.test.ts"), 'w', encoding="utf-8") as f:
                f.write("""
                    import { sum } from '../src/sum';
                    describe('sum', () => {
                        test('adds 1 + 2 to equal 3', () => {
                            expect(sum(1, 2)).toBe(3);
                        });
                    });
                """)

            temp_coverage_report_path_relative = os.path.join("atco_artifacts", "coverage_reports", "jest_coverage_output.json")
            os.makedirs(os.path.join(temp_dir, "atco_artifacts", "coverage_reports"), exist_ok=True)

            res = await run_jest_tests(
                test_file_path=dummy_test_path_relative,
                target_identifier=dummy_source_path_relative,
                project_root=temp_dir,
                temp_coverage_report_path_relative=temp_coverage_report_path_relative,
                extra_jest_args=["--verbose"],
                timeout_seconds=180
            )

            print(f"\nTest Result: {'PASS' if res['success'] else 'FAIL'}")
            print(f"Coverage: {res['coverage_increase_percent']:.2f}%")
            print(f"Reason: {res['reason']}")
            print("Execution Log (tail):\n", res["raw_log"][-1000:])
            print(f"Temporary Directories Used: {res['temp_dirs_used']}")
            print(f"Jest Version: {res.get('jest_version', 'N/A')}")
            print(f"ts-Jest Version: {res.get('ts_jest_version', 'N/A')}")
            full_cov = os.path.join(temp_dir, temp_coverage_report_path_relative)
            print(f"Coverage report exists: {os.path.exists(full_cov)} at {full_cov}")

        print("\n--- Test Run Complete ---")

    asyncio.run(main_test_run())