"""
fixer_validate.py

Provides a comprehensive validation pipeline for Python code changes,
including compilation, linting, type-checking, static analysis, and testing.
"""

import os
import subprocess
import json
import logging
import difflib
import shutil
import sys
import asyncio
import hashlib
import tempfile
import ast
import re
from datetime import datetime
from typing import Dict, List, Any, Optional, Callable, Tuple, TYPE_CHECKING
from pathlib import Path
from dataclasses import dataclass, field, asdict

# --- Centralized Utilities (replacing placeholders) ---
try:
    from .compat_core import alert_operator, scrub_secrets, audit_logger, SECRETS_MANAGER
    # Lazy import of get_cache to avoid import-time side effects
    if TYPE_CHECKING:
        from .cache_layer import get_cache
except ImportError as e:
    if __name__ == "__main__" and os.getenv("ALLOW_FAKE_CORES", "1") == "1":
        class _DummyAudit:
            def log_event(self, *a, **k):
                print(f"[AUDIT_LOG] {a}: {k}")
        def alert_operator(msg, level="CRITICAL"):
            print(f"[OPS ALERT - {level}] {msg}")
        def scrub_secrets(x):
            return x
        def get_cache(*a, **k):
            class _DummyCache:
                async def get(self, key): return None
                async def setex(self, key, ttl, val): pass
            return _DummyCache()
        audit_logger = _DummyAudit()
        SECRETS_MANAGER = object()
    else:
        raise RuntimeError(f"[CRITICAL][VALIDATE] fixer_validate requires core modules: {e}") from e

# Handle optional termcolor import gracefully
try:
    from termcolor import colored
except ImportError:  # pragma: no cover
    def colored(s, *_, **__):  # no-op fallback
        return s

# --- Global Production Mode Flag (from main orchestrator) ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

logger = logging.getLogger(__name__)

# --- Custom Exception for critical errors (from analyzer.py) ---
class AnalyzerCriticalError(RuntimeError):
    """
    Custom exception for critical errors that should halt execution.
    """
    def __init__(self, message: str, alert_level: str = "CRITICAL"):
        super().__init__(f"[CRITICAL][VALIDATE] {message}")
        try:
            alert_operator(message, level=alert_level)
        except Exception:
            pass

class NonCriticalError(Exception):
    """
    Custom exception for recoverable issues that should be logged but not halt execution.
    """
    pass

@dataclass
class Issue:
    file: str
    code: str
    message: str
    line: Optional[int] = None
    column: Optional[int] = None
    severity: Optional[str] = None
    tool: Optional[str] = None

@dataclass
class StageResult:
    name: str
    passed: bool
    duration_ms: int
    tool: Optional[str] = None
    version: Optional[str] = None
    issues: List[Issue] = field(default_factory=list)
    stdout_tail: str = ""
    stderr_tail: str = ""

@dataclass
class ValidationReport:
    overall_passed: bool
    stages: Dict[str, StageResult]

class CodeValidator:
    """
    Provides a comprehensive validation pipeline for Python code changes,
    including compilation, linting, type-checking, static analysis, and testing.
    """
    def __init__(self, project_root: str, whitelisted_paths: Optional[List[str]] = None, parallelism: int = 4, tests_dir: Optional[str] = None, timeouts: Optional[Dict[str, int]] = None):
        """
        Initializes the CodeValidator.
        """
        self.project_root = Path(project_root).resolve()
        self.whitelisted_paths = [Path(p).resolve() for p in (whitelisted_paths or [self.project_root])]
        self._tool_versions = {}
        self._sem = asyncio.Semaphore(parallelism)
        self.tests_root = Path(tests_dir).resolve() if tests_dir else self.project_root
        self._cache_client = None

        self._timeouts = {
            "ruff": 60,
            "flake8": 60,
            "mypy": 300,
            "bandit": 300,
            "pytest": 900,  # 15 minutes
        }
        if timeouts:
            self._timeouts.update(timeouts)

        if not self.project_root.is_dir():
            raise AnalyzerCriticalError(f"Project root '{self.project_root}' is not a valid directory. Aborting validation.")

        if PRODUCTION_MODE and not self.whitelisted_paths:
            raise AnalyzerCriticalError("In PRODUCTION_MODE, 'whitelisted_paths' must be configured for CodeValidator. Aborting startup.")

        logger.info(f"CodeValidator initialized for project: {self.project_root}")
        try:
            audit_logger.log_event("code_validator_init", project_root=str(self.project_root), whitelisted_paths=[str(p) for p in self.whitelisted_paths])
        except Exception:
            pass # Failsafe against audit logger being a mock/uninitialized

    async def _get_cache(self):
        if self._cache_client is None:
            # Lazy import to avoid circular dependencies and import-time failures
            from .cache_layer import get_cache
            self._cache_client = await get_cache(
                project_root=str(self.project_root),
                whitelisted_plugin_dirs=[str(p) for p in self.whitelisted_paths]
            )
        return self._cache_client

    def _is_under(self, child: Path) -> bool:
        """Checks if a path is under one of the whitelisted paths and not a symlink."""
        try:
            if child.is_symlink() or any(p.is_symlink() for p in child.parents):
                logger.warning(f"Path '{child}' contains a symlink, which is forbidden.")
                return False

            resolved_child = child.resolve()

            for base in self.whitelisted_paths:
                try:
                    resolved_child.relative_to(base.resolve())
                    return True
                except ValueError:
                    continue
            return False
        except Exception as e:
            logger.warning("Whitelist check failed for %s: %s", child, e)
            return False

    def _assert_whitelisted(self, p: Path) -> None:
        """Raises a critical error if the path is not whitelisted."""
        if not self._is_under(p):
            raise AnalyzerCriticalError(f"Attempted to operate on file '{p}' which is outside whitelisted paths: {self.whitelisted_paths}. Aborting operation.")

    def _tool_config_files(self, tool: str) -> List[Path]:
        """Returns a list of potential config file paths for a given tool."""
        names = {
            "ruff":   ["pyproject.toml", "ruff.toml"],
            "flake8": [".flake8", "setup.cfg", "tox.ini", "pyproject.toml"],
            "mypy":   ["mypy.ini", "setup.cfg", "pyproject.toml"],
            "pytest": ["pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"],
            "bandit": ["bandit.yaml", ".bandit", "pyproject.toml"],
        }.get(tool, [])
        return [p for p in (self.project_root / n for n in names) if p.exists()]

    async def _get_tool_version(self, tool: str) -> Optional[str]:
        """Gets and caches the version of a given tool."""
        if tool in self._tool_versions:
            return self._tool_versions[tool]

        tool_path = shutil.which(tool)
        if not tool_path:
            return None

        try:
            proc = await asyncio.create_subprocess_exec(
                tool_path, '--version',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            version_output = await asyncio.wait_for(proc.communicate(), timeout=5)
            ver_src = version_output[0] or version_output[1]
            version = ver_src.decode('utf-8', errors='ignore').splitlines()[0].strip()
            self._tool_versions[tool] = version
            return version
        except (FileNotFoundError, asyncio.TimeoutError, Exception):
            return None

    def _cache_key(self, stage: str, files: List[Path], extra: Optional[Dict] = None) -> str:
        """Generates a robust cache key based on file content, tool versions, and stage."""
        h = hashlib.sha256()

        for p in sorted(files):
            resolved_path = p.resolve()
            if resolved_path.is_dir():
                try:
                    h.update(str(resolved_path).encode())
                    h.update(str(resolved_path.stat().st_mtime_ns).encode())
                except Exception as e:
                    logger.warning(f"Could not hash directory {p} for cache key: {e}")
                continue

            try:
                st = resolved_path.stat()
                h.update(str(resolved_path).encode())
                h.update(str(st.st_mtime_ns).encode())
                h.update(str(st.st_size).encode())
                h.update(resolved_path.read_bytes())
            except Exception as e:
                logger.warning(f"Could not read file {p} for cache key hashing: {e}")

        for cfg in (extra or {}).get("config_files", []):
            try:
                cp = Path(cfg)
                st = cp.stat()
                h.update(str(cp).encode())
                h.update(str(st.st_mtime_ns).encode())
                h.update(str(st.st_size).encode())
                h.update(cp.read_bytes())
            except Exception:
                continue

        tool = (extra or {}).get("tool")
        payload = {"stage": stage,
                   "tool": tool,
                   "version": (extra or {}).get("version"),
                   "extra": {k: v for k, v in (extra or {}).items() if k not in ("tool", "version")}}

        h.update(json.dumps(payload, sort_keys=True).encode())

        return f"fixer_validate:{h.hexdigest()}"

    def _subprocess_env(self) -> dict:
        """Creates a safe, minimal environment for subprocesses."""
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(self.project_root), os.environ.get("PYTHONPATH", "")]))

        locale_val = (env.get("LC_ALL") or env.get("LANG") or "C.UTF-8")
        if "UTF-8" not in locale_val:
            locale_val = "C.UTF-8"

        env["LANG"] = locale_val
        env["LC_ALL"] = locale_val

        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        return env

    async def _run_command_async(self, command: List[str], cwd: Optional[Path] = None, description: str = "command", timeout_s: int = 120, max_output_kb: int = 512) -> Tuple[bool, str, str]:
        """
        Helper to run a subprocess command asynchronously with timeouts and output caps.
        Returns a tuple: (success, stdout_message, stderr_message)
        """
        logger.info(f"Running {description}: {' '.join(command)}")
        start_time = asyncio.get_event_loop().time()

        async with self._sem:
            try:
                tool_path = shutil.which(command[0])
                if not tool_path:
                    raise NonCriticalError(f"Required tool '{command[0]}' not found for {description}. Skipping validation.")

                kwargs = {}
                if os.name != "nt":
                    import signal, os as _os
                    kwargs["preexec_fn"] = _os.setsid
                else:
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

                process = await asyncio.create_subprocess_exec(
                    tool_path, *command[1:],
                    cwd=cwd or self.project_root,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._subprocess_env(),
                    **kwargs
                )

                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
                except asyncio.TimeoutError:
                    if os.name != "nt":
                        import signal, os as _os
                        try: _os.killpg(process.pid, signal.SIGKILL)
                        except Exception: process.kill()
                    else:
                        process.kill()
                    await process.wait()
                    elapsed = asyncio.get_event_loop().time() - start_time
                    logger.error(f"{description} timed out after {elapsed:.2f}s and was killed.")
                    return False, "", f"{description} timed out after {elapsed:.2f}s"

                def _trim(b: bytes) -> str:
                    s = b.decode("utf-8", errors='ignore')
                    limit = max_output_kb * 1024
                    if len(s) <= limit:
                        return s
                    return s[-limit:] # keep the tail where errors usually are

                stdout_str = _trim(stdout)
                stderr_str = _trim(stderr)

                if process.returncode == 0:
                    logger.debug(f"{description} succeeded.", extra={'command': ' '.join(command), 'cwd': str(cwd)})
                    return True, stdout_str, stderr_str
                else:
                    logger.error(f"{description} failed (exit code {process.returncode}):\n{stderr_str}", extra={'command': ' '.join(command), 'cwd': str(cwd)})
                    return False, stdout_str, stderr_str
            except NonCriticalError:
                raise
            except FileNotFoundError:
                raise NonCriticalError(f"Required tool '{command[0]}' not found for {description}. Skipping validation.")
            except Exception as e:
                logger.critical(f"CRITICAL: Unexpected error running {description}: {e}", exc_info=True, extra={'command': ' '.join(command), 'cwd': str(cwd)})
                try:
                    audit_logger.log_event("validation_tool_error", tool=command[0], description=description, error=str(e))
                except Exception:
                    pass
                raise AnalyzerCriticalError(f"CodeValidator: Unexpected error running {description}: {e}.")

    def _atomic_write(self, path: Path, data: str) -> None:
        """Writes data to a file atomically using a temporary file."""
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except Exception:
                pass

    def compile_file(self, file_path: Path) -> StageResult:
        """Checks if a Python file compiles successfully without writing artifacts."""
        start_time = datetime.now()
        self._assert_whitelisted(file_path)

        logger.info(f"Compiling {file_path}...")
        passed = True
        error_msg = ""
        try:
            src = file_path.read_text(encoding="utf-8")
            ast.parse(src, filename=str(file_path))
            try:
                audit_logger.log_event("validation_step_success", step="compile", file=str(file_path))
            except Exception:
                pass
        except (SyntaxError, Exception) as e:
            passed = False
            error_msg = f"{e.msg} (line {e.lineno}, col {e.offset})" if isinstance(e, SyntaxError) else f"Failed to read/parse {file_path}: {e}"
            logger.error(f"Compilation FAILED for {file_path}: {error_msg}")
            try:
                audit_logger.log_event("validation_step_failure", step="compile", file=str(file_path), error=error_msg)
            except Exception:
                pass

        duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
        return StageResult(
            name="compile",
            passed=passed,
            duration_ms=duration_ms,
            tool="ast",
            version=sys.version,
            stderr_tail=error_msg
        )

    async def _run_tool_stage(self, stage_name: str, files: List[Path], tool: str, command_func: Callable, parser_func: Optional[Callable] = None) -> StageResult:
        """Generic runner for a validation stage."""
        start_time = datetime.now()

        cache_key = None
        cache = await self._get_cache()
        if stage_name != "tests":
            version = await self._get_tool_version(tool)
            config_files = self._tool_config_files(tool)
            cache_key = self._cache_key(stage_name, files, extra={'tool': tool, 'version': version, 'config_files': [str(p) for p in config_files]})

        if cache and cache_key:
            cached_result = await cache.get(cache_key)
            if cached_result:
                try:
                    report_dict = json.loads(cached_result)
                    try:
                        audit_logger.log_event("validation_cache_hit", step=stage_name, files=[str(f) for f in files], cached_result=report_dict)
                    except Exception:
                        pass
                    return StageResult(
                        name=stage_name,
                        passed=report_dict['passed'],
                        duration_ms=report_dict.get('duration_ms', 0),
                        tool=tool,
                        version=report_dict.get('version'),
                        issues=[Issue(**i) for i in report_dict['issues']],
                        stdout_tail=report_dict.get('stdout_tail', ''),
                        stderr_tail=report_dict.get('stderr_tail', '')
                    )
                except Exception as e:
                    logger.error(f"Failed to parse cached result for {stage_name}: {e}")
                    # Invalidate the corrupted cache entry
                    await cache.setex(cache_key, 1, "{}")

        all_passed = True
        issues: List[Issue] = []
        stdout_output = ""
        stderr_output = ""
        timeout_s = self._timeouts.get(tool, 120)
        max_output_kb = (2048 if tool == "bandit" else 512)

        try:
            command = command_func(files)
            success, stdout, stderr = await self._run_command_async(command, description=f"{tool} {stage_name}", timeout_s=timeout_s, max_output_kb=max_output_kb)
            stdout_output = scrub_secrets(stdout)
            stderr_output = scrub_secrets(stderr)

            if not success:
                all_passed = False

            if parser_func:
                parsed_issues = parser_func(stdout_output, stderr_output, files)
                if parsed_issues:
                    issues.extend(parsed_issues)
                    all_passed = False

            if not all_passed:
                logger.error(f"{tool} {stage_name} FAILED.")
                try:
                    audit_logger.log_event("validation_step_failure", step=stage_name, tool=tool, files=[str(f) for f in files], output=stderr_output, issues_count=len(issues))
                except Exception:
                    pass
            else:
                logger.info(f"{tool} {stage_name} passed for {files}.")
                try:
                    audit_logger.log_event("validation_step_success", step=stage_name, tool=tool, files=[str(f) for f in files])
                except Exception:
                    pass
        except NonCriticalError as e:
            logger.warning(str(e))
            try:
                audit_logger.log_event("validation_step_skipped", step=stage_name, reason=str(e))
            except Exception:
                pass
            all_passed = True # Treat as passed if tool is missing
        except AnalyzerCriticalError:
            raise # Re-raise critical errors
        except Exception as e:
            logger.error(f"Error during {stage_name}: {e}", exc_info=True)
            all_passed = False

        duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
        report = StageResult(
            name=stage_name,
            passed=all_passed,
            duration_ms=duration_ms,
            tool=tool,
            version=version if 'version' in locals() else None,
            issues=issues,
            stdout_tail=stdout_output,
            stderr_tail=stderr_output
        )

        if cache and cache_key:
            await cache.setex(cache_key, 3600, json.dumps(asdict(report)))

        return report

    async def run_linting(self, file_paths: List[Path]) -> StageResult:
        """Runs linting (Ruff or Flake8) on specified files."""
        if shutil.which("ruff"):
            return await self._run_tool_stage(
                stage_name="lint",
                files=file_paths,
                tool="ruff",
                command_func=lambda files: ['ruff', 'check', *[str(f) for f in files]],
                parser_func=self._parse_ruff_output
            )
        elif shutil.which("flake8"):
            return await self._run_tool_stage(
                stage_name="lint",
                files=file_paths,
                tool="flake8",
                command_func=lambda files: ['flake8', *[str(f) for f in files]],
                parser_func=self._parse_flake8_output
            )
        else:
            return StageResult(name="lint", passed=True, duration_ms=0, tool=None, version=None, stderr_tail="Neither Ruff nor Flake8 found.")

    def _parse_ruff_output(self, stdout: str, stderr: str, files: List[Path]) -> List[Issue]:
        issues = []
        for line in stdout.splitlines():
            m = re.match(r"^(.*?):(\d+):(\d+):\s*(\S+)\s*(.*)$", line)
            if not m:
                continue
            file_path, ln, col, code, message = m.groups()
            issues.append(Issue(file=file_path, line=int(ln), column=int(col), code=code, message=message, tool="ruff"))
        return issues

    def _parse_flake8_output(self, stdout: str, stderr: str, files: List[Path]) -> List[Issue]:
        issues = []
        for line in stdout.splitlines():
            m = re.match(r"^(.*?):(\d+):(\d+):\s*(\S+)\s*(.*)$", line)
            if not m:
                continue
            file_path, ln, col, code, message = m.groups()
            issues.append(Issue(file=file_path, line=int(ln), column=int(col), code=code, message=message, tool="flake8"))
        return issues

    async def run_type_checking(self, file_paths: List[Path]) -> StageResult:
        """Runs type checking (Mypy) on specified files."""
        return await self._run_tool_stage(
            stage_name="type_check",
            files=file_paths,
            tool="mypy",
            command_func=lambda files: ['mypy', *[str(f) for f in files]],
            parser_func=self._parse_mypy_output
        )

    def _parse_mypy_output(self, stdout: str, stderr: str, files: List[Path]) -> List[Issue]:
        issues = []
        for line in stdout.splitlines():
            # path:line[:col]: severity: message
            m = re.match(r"^(.*?):(\d+)(?::(\d+))?:\s*([a-zA-Z]+):\s*(.*)$", line)
            if not m:
                continue
            file_path, ln, col, sev, msg = m.groups()
            issues.append(Issue(
                file=file_path, line=int(ln), column=int(col) if col else None,
                severity=sev.lower(), message=msg, tool="mypy", code="mypy"
            ))
        return issues

    async def run_static_analysis(self, file_paths: List[Path]) -> StageResult:
        """Runs static analysis (Bandit) for security issues."""
        def _bandit_cmd(files: List[Path]) -> List[str]:
            str_files = [str(f) for f in files]
            if any(f.is_dir() for f in files):
                return ['bandit', '-r', *str_files, '-f', 'json', '-q']
            return ['bandit', *str_files, '-f', 'json', '-q']

        return await self._run_tool_stage(
            stage_name="static_analysis",
            files=file_paths,
            tool="bandit",
            command_func=_bandit_cmd,
            parser_func=self._parse_bandit_output
        )

    def _parse_bandit_output(self, stdout: str, stderr: str, files: List[Path]) -> List[Issue]:
        issues = []
        if not stdout:
            return issues

        try:
            bandit_results = json.loads(stdout)
            raw_issues = bandit_results.get('results', [])
            for issue in raw_issues:
                issues.append(Issue(
                    file=issue.get('filename'),
                    line=issue.get('line_number'),
                    severity=issue.get('issue_severity'),
                    code=issue.get('test_id'),
                    message=f"[{issue.get('test_name')}] {issue.get('issue_text')}",
                    tool="bandit"
                ))
        except json.JSONDecodeError:
            logger.error(f"Failed to parse Bandit JSON output. Raw output: {stdout[:500]}...")
            issues.append(Issue(file="unknown", message="Failed to parse Bandit JSON output.", severity="CRITICAL", tool="bandit"))

        return issues

    async def run_tests(self, test_paths: List[Path], full_suite: bool = False) -> StageResult:
        """
        Runs pytest tests.
        Args:
            test_paths (List[Path]): List of files/directories to test.
            full_suite (bool): If True, runs the entire test suite (usually from project root).
        """
        if not shutil.which("pytest"):
            return StageResult(
                name="tests", passed=False, duration_ms=0, tool="pytest",
                version=None, stderr_tail="pytest not found but tests were requested."
            )

        if not full_suite and test_paths:
            for fp in test_paths:
                self._assert_whitelisted(fp)
            cmd = ['pytest', *map(str, test_paths), '-q', '--maxfail=1']
        else:
            self._assert_whitelisted(self.tests_root)
            cmd = ['pytest', str(self.tests_root), '-q', '--maxfail=1']

        return await self._run_tool_stage(
            stage_name="tests",
            files=test_paths,
            tool="pytest",
            command_func=lambda files: cmd,
            parser_func=lambda *_: []
        )

    def show_diff(self, file_path: Path, new_code: str, interactive: bool) -> bool:
        """
        Displays a unified diff between original and new code, and optionally prompts for approval.
        Returns True if approved or non-interactive, False otherwise.
        """
        self._assert_whitelisted(file_path)

        try:
            original_code = file_path.read_text(encoding='utf-8')
        except FileNotFoundError:
            logger.error(f"Original file not found for diff: {file_path}. Cannot show diff.")
            return False

        original_lines = original_code.splitlines(keepends=True)
        new_lines = new_code.splitlines(keepends=True)

        diff = difflib.unified_diff(original_lines, new_lines, fromfile=str(file_path) + " (original)", tofile=str(file_path) + " (proposed)")
        colored_diff = []
        for line in diff:
            if line.startswith(('+++', '---', '@@')):
                colored_diff.append(line)
            elif line.startswith('+'): colored_diff.append(colored(line, 'green'))
            elif line.startswith('-'): colored_diff.append(colored(line, 'red'))
            elif line.startswith('^'): colored_diff.append(colored(line, 'blue'))
            else: colored_diff.append(line)
        diff_str = "".join(colored_diff)

        if diff_str.strip() == "":
            logger.info(f"No effective change for {file_path}. Skipping diff display.")
            return True

        print(diff_str)
        if interactive:
            response = input(f"Apply this change to {file_path.name}? (y/N) ").lower()
            return response == 'y'
        return True # Auto-approve if not interactive

    async def validate_and_commit_file(self, file_path: str, new_code: str, original_code: str, run_tests: bool, interactive: bool, custom_validators: Optional[List[Callable]] = None) -> ValidationReport:
        """
        Validates a single file change through a pipeline and commits if successful.
        """
        file_path_obj = Path(file_path)
        self._assert_whitelisted(file_path_obj)

        if new_code == original_code:
            logger.info("Proposed change is identical to original; treating as no-op.")
            return ValidationReport(overall_passed=True, stages={})

        logger.info(f"Starting validation pipeline for {file_path_obj}...")
        try:
            audit_logger.log_event("single_file_validation_start", file=str(file_path_obj), interactive=interactive, run_tests=run_tests)
        except Exception:
            pass

        if PRODUCTION_MODE and interactive:
            raise AnalyzerCriticalError("Interactive prompts are forbidden in PRODUCTION_MODE. Aborting validation.")

        if interactive:
            if not self.show_diff(file_path_obj, new_code, interactive):
                logger.info(f"Change to {file_path_obj} declined by user.")
                try:
                    audit_logger.log_event("single_file_validation_declined", file=str(file_path_obj))
                except Exception:
                    pass
                return ValidationReport(overall_passed=False, stages={})

        backup_path = file_path_obj.with_name(
            f"{file_path_obj.name}.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
        )
        try:
            shutil.copy(file_path_obj, backup_path)
            logger.info(f"Backed up original file to {backup_path}.")
            try:
                audit_logger.log_event("file_backup", file=str(file_path_obj), backup_path=str(backup_path))
            except Exception:
                pass
        except Exception as e:
            raise AnalyzerCriticalError(f"Failed to create backup for {file_path_obj}: {e}. Aborting validation.")

        try:
            if not os.access(file_path_obj, os.W_OK):
                raise AnalyzerCriticalError(f"No write access to {file_path_obj}. Aborting.")

            self._atomic_write(file_path_obj, new_code)
            logger.debug(f"Temporarily applied change to {file_path_obj} for validation.")
        except IOError as e:
            self.rollback_change(file_path_obj, original_code, is_critical_failure=True)
            raise AnalyzerCriticalError(f"Failed to write temporary changes to {file_path_obj}: {e}. Aborting validation.")

        validation_passed = True
        report = ValidationReport(overall_passed=False, stages={})

        try:
            report.stages["compile"] = self.compile_file(file_path_obj)
            validation_passed &= report.stages["compile"].passed
            if not validation_passed: return report

            report.stages["lint"] = await self.run_linting([file_path_obj])
            validation_passed &= report.stages["lint"].passed
            if not validation_passed: return report

            report.stages["type_check"] = await self.run_type_checking([file_path_obj])
            validation_passed &= report.stages["type_check"].passed
            if not validation_passed: return report

            report.stages["static_analysis"] = await self.run_static_analysis([file_path_obj])
            validation_passed &= report.stages["static_analysis"].passed
            if not validation_passed: return report

            if run_tests:
                report.stages["tests"] = await self.run_tests([self.tests_root], full_suite=False)
                validation_passed &= report.stages["tests"].passed
                if not validation_passed: return report

            if custom_validators:
                start_time_custom = datetime.now()
                custom_passed = True
                for validator_func in custom_validators:
                    try:
                        if not validator_func(file_path_obj):
                            logger.error(f"Custom validator '{validator_func.__name__}' FAILED for {file_path_obj}.")
                            try:
                                audit_logger.log_event("validation_step_failure", step="custom_validator", validator_name=validator_func.__name__, file=str(file_path_obj))
                            except Exception:
                                pass
                            custom_passed = False
                            break
                    except Exception as e:
                        logger.error(f"Error running custom validator '{validator_func.__name__}' for {file_path_obj}: {e}", exc_info=True)
                        try:
                            audit_logger.log_event("validation_step_failure", step="custom_validator", validator_name=validator_func.__name__, file=str(file_path_obj), error=str(e))
                        except Exception:
                            pass
                        custom_passed = False
                        break
                report.stages["custom_validators"] = StageResult(
                    name="custom_validators",
                    passed=custom_passed,
                    duration_ms=int((datetime.now() - start_time_custom).total_seconds() * 1000),
                    tool="custom",
                    version="1.0"
                )
                validation_passed &= custom_passed
                if not validation_passed: return report

        except AnalyzerCriticalError:
            self.rollback_change(file_path_obj, original_code, is_critical_failure=True)
            raise
        finally:
            if not validation_passed:
                logger.error(f"Validation FAILED for {file_path_obj}. Rolling back changes.")
                self.rollback_change(file_path_obj, original_code, is_critical_failure=False)
                try:
                    audit_logger.log_event("single_file_validation_failed", file=str(file_path_obj), status="rolled_back")
                except Exception:
                    pass
            else:
                logger.info(f"Validation PASSED for {file_path_obj}. Changes are committed (persisted).")
                try:
                    audit_logger.log_event("single_file_validation_success", file=str(file_path_obj))
                except Exception:
                    pass
                try:
                    os.remove(backup_path)
                    logger.debug(f"Removed backup file {backup_path}.")
                except Exception as e:
                    logger.warning(f"Failed to remove backup file {backup_path}: {e}")

        report.overall_passed = validation_passed
        return report

    async def validate_and_commit_batch(self,
                                  files_to_validate: List[str],
                                  original_contents: Dict[str, str],
                                  new_contents: Dict[str, str],
                                  run_tests: bool,
                                  custom_validators: Optional[List[Callable]] = None) -> ValidationReport:
        """
        Validates a batch of file changes.
        """
        files_to_validate_obj = [Path(f) for f in files_to_validate]
        for f in files_to_validate_obj:
            self._assert_whitelisted(f)
            on_disk = f.read_text(encoding="utf-8")
            expected = original_contents.get(str(f))
            if expected is None or expected != on_disk:
                raise AnalyzerCriticalError(
                    f"Original content mismatch for {f}. Aborting batch to prevent corrupt rollback."
                )

        logger.info(f"Starting batch validation pipeline for {len(files_to_validate_obj)} files...")
        try:
            audit_logger.log_event("batch_file_validation_start", files_count=len(files_to_validate_obj), run_tests=run_tests)
        except Exception:
            pass

        if PRODUCTION_MODE:
            raise AnalyzerCriticalError("Batch validation is forbidden in PRODUCTION_MODE. Aborting.")

        validation_passed = True
        report = ValidationReport(overall_passed=False, stages={})

        backup_paths = {}
        try:
            # Step 1: Create backups and apply temporary changes
            for file_path_obj in files_to_validate_obj:
                backup_path = file_path_obj.with_name(
                    f"{file_path_obj.name}.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
                )
                shutil.copy(file_path_obj, backup_path)
                backup_paths[file_path_obj] = backup_path
                self._atomic_write(file_path_obj, new_contents[str(file_path_obj)])
                logger.debug(f"Temporarily applied change to {file_path_obj} for batch validation.")

            # Step 2: Run validation stages on all files
            report.stages["lint"] = await self.run_linting(files_to_validate_obj)
            validation_passed &= report.stages["lint"].passed
            if not validation_passed: return report

            report.stages["type_check"] = await self.run_type_checking(files_to_validate_obj)
            validation_passed &= report.stages["type_check"].passed
            if not validation_passed: return report

            report.stages["static_analysis"] = await self.run_static_analysis(files_to_validate_obj)
            validation_passed &= report.stages["static_analysis"].passed
            if not validation_passed: return report

            if run_tests:
                report.stages["tests"] = await self.run_tests([self.project_root], full_suite=True)
                validation_passed &= report.stages["tests"].passed
                if not validation_passed: return report

            if custom_validators:
                start_time_custom = datetime.now()
                custom_passed = True
                for file_path in files_to_validate_obj:
                    for validator_func in custom_validators:
                        try:
                            if not validator_func(file_path):
                                logger.error(f"Custom validator '{validator_func.__name__}' FAILED for {file_path} in batch.")
                                try:
                                    audit_logger.log_event("validation_step_failure", step="custom_validator", validator_name=validator_func.__name__, file=str(file_path))
                                except Exception:
                                    pass
                                custom_passed = False
                                break
                        except Exception as e:
                            logger.error(f"Error running custom validator '{validator_func.__name__}' for {file_path} in batch: {e}", exc_info=True)
                            try:
                                audit_logger.log_event("validation_step_failure", step="custom_validator", validator_name=validator_func.__name__, file=str(file_path), error=str(e))
                            except Exception:
                                pass
                            custom_passed = False
                            break
                    if not custom_passed:
                        break
                report.stages["custom_validators"] = StageResult(
                    name="custom_validators",
                    passed=custom_passed,
                    duration_ms=int((datetime.now() - start_time_custom).total_seconds() * 1000),
                    tool="custom",
                    version="1.0"
                )
                validation_passed &= custom_passed
                if not validation_passed: return report

        except AnalyzerCriticalError:
            for file_path in files_to_validate_obj:
                self.rollback_change(file_path, original_contents.get(str(file_path), ""), is_critical_failure=True)
            raise
        finally:
            if not validation_passed:
                logger.error(f"Batch validation FAILED. Rolling back all changes in this batch.")
                for file_path in files_to_validate_obj:
                    self.rollback_change(file_path, original_contents.get(str(file_path), ""), is_critical_failure=False)
                try:
                    audit_logger.log_event("batch_file_validation_failed", files_count=len(files_to_validate_obj), status="rolled_back")
                except Exception:
                    pass
            else:
                logger.info(f"Batch validation PASSED for {len(files_to_validate_obj)} files. Changes are committed (persisted).")
                try:
                    audit_logger.log_event("batch_file_validation_success", files_count=len(files_to_validate_obj))
                except Exception:
                    pass
                for file_path_obj, backup_path in backup_paths.items():
                    try:
                        os.remove(backup_path)
                        logger.debug(f"Removed backup file {backup_path}.")
                    except Exception as e:
                        logger.warning(f"Failed to remove backup file {backup_path}: {e}")

        report.overall_passed = validation_passed
        return report

    def rollback_change(self, file_path: Path, original_code: str, is_critical_failure: bool = False):
        """
        Rolls back changes to a file by writing back its original content.
        """
        logger.warning(f"Rolling back changes for {file_path}.")
        try:
            if not os.access(file_path, os.W_OK):
                raise AnalyzerCriticalError(f"No write access to {file_path} for rollback. Manual intervention REQUIRED!")

            backups = sorted(file_path.parent.glob(f"{file_path.name}.bak.*"), reverse=True)
            if backups:
                data = backups[0].read_text(encoding="utf-8")
                for b in backups:
                    try: os.remove(b)
                    except: pass
            else:
                data = original_code

            self._atomic_write(file_path, data)
            logger.info(f"Successfully rolled back {file_path}.")
            try:
                audit_logger.log_event("file_rollback_success", file=str(file_path))
            except Exception:
                pass
        except AnalyzerCriticalError as e:
            logger.critical(f"CRITICAL: Failed to rollback changes for {file_path}: {e}", exc_info=True)
            try:
                audit_logger.log_event("file_rollback_failure", file=str(file_path), error=str(e))
            except Exception:
                pass
            if is_critical_failure:
                alert_operator(f"CRITICAL: Failed to rollback changes for {file_path}. Manual intervention REQUIRED! Aborting.", level="CRITICAL")
                raise
        except IOError as e:
            logger.critical(f"CRITICAL: Failed to rollback changes for {file_path}: {e}. Manual intervention REQUIRED!", exc_info=True)
            try:
                audit_logger.log_event("file_rollback_failure", file=str(file_path), error=str(e))
            except Exception:
                pass
            if is_critical_failure:
                alert_operator(f"CRITICAL: Failed to rollback changes for {file_path}. Manual intervention REQUIRED! Aborting.", level="CRITICAL")
                raise
        except Exception as e:
            logger.critical(f"CRITICAL: An unexpected error occurred during rollback for {file_path}: {e}. Manual intervention REQUIRED!", exc_info=True)
            try:
                audit_logger.log_event("file_rollback_failure", file=str(file_path), error=str(e))
            except Exception:
                pass
            if is_critical_failure:
                alert_operator(f"CRITICAL: Unexpected error during rollback for {file_path}. Manual intervention REQUIRED! Aborting.", level="CRITICAL")
                raise

def make_validator(project_root: str | None = None,
                   whitelisted_paths: list[str] | None = None,
                   parallelism: int = 4,
                   tests_dir: Optional[str] = None,
                   timeouts: Optional[Dict[str, int]] = None) -> "CodeValidator":
    return CodeValidator(project_root=project_root or os.getcwd(),
                         whitelisted_paths=whitelisted_paths,
                         parallelism=parallelism,
                         tests_dir=tests_dir,
                         timeouts=timeouts)

# Example usage (for testing this module independently)
async def main():
    import shutil

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger.setLevel(logging.DEBUG) # Enable debug for this module's internal logs

    # Clean up any old test files
    if os.path.exists("test_validation_project"):
        shutil.rmtree("test_validation_project")

    # Create a dummy project for testing
    test_project_root = Path("test_validation_project")
    os.makedirs(test_project_root, exist_ok=True)

    # Initialize validator with whitelisted paths for testing
    code_validator = make_validator(project_root=str(test_project_root), whitelisted_paths=[str(test_project_root)])

    # Create dummy files
    test_file_path = test_project_root / "my_module.py"
    test_file_path.write_text("import os\n\n# A comment\ndef my_function():\n    x = 1\n    return x\n")

    bad_security_file = test_project_root / "bad_security.py"
    # INTENTIONAL: Creating test file with security issues to test the scanner
    # nosec: This hardcoded password is for testing security detection only
    bad_security_file.write_text("password = 'mysecretpassword'\nimport subprocess\nsubprocess.call('ls') # nosec\n")  # nosec - test data

    syntax_error_file = test_project_root / "syntax_error.py"
    syntax_error_file.write_text("def bad_syntax:\n") # Missing parentheses

    type_error_file = test_project_root / "type_error.py"
    type_error_file.write_text("def add_numbers(a: int, b: str) -> int:\n    return a + b\n")

    test_test_file = test_project_root / "test_my_module.py"
    test_test_file.write_text("import my_module\n\ndef test_my_function():\n    assert my_module.my_function() == 1\n")

    # --- Test Single File Validation (Success) ---
    print("\n--- Testing Single File Validation (Success) ---")
    original_code_ok = test_file_path.read_text()
    new_code_ok = "import os\n\ndef my_function():\n    x = 2 # Changed value\n    return x\n"

    report_ok = await code_validator.validate_and_commit_file(
        file_path=str(test_file_path),
        new_code=new_code_ok,
        original_code=original_code_ok,
        run_tests=True,
        interactive=False,
        custom_validators=[]
    )
    print(f"Validation Result (OK): {report_ok.overall_passed}")
    print(f"File content after OK validation: {test_file_path.read_text().strip()}")
    assert report_ok.overall_passed
    assert "2 # Changed value" in test_file_path.read_text()

    # --- Test Single File Validation (Syntax Error) ---
    print("\n--- Testing Single File Validation (Syntax Error) ---")
    original_syntax_ok = syntax_error_file.read_text()
    new_syntax_bad = "def bad_syntax:\n"

    try:
        report_syntax_error = await code_validator.validate_and_commit_file(
            file_path=str(syntax_error_file),
            new_code=new_syntax_bad,
            original_code=original_syntax_ok,
            run_tests=False,
            interactive=False,
            custom_validators=[]
        )
    except AnalyzerCriticalError as e:
        print(f"Caught expected AnalyzerCriticalError: {e}")
        report_syntax_error = ValidationReport(overall_passed=False, stages={})

    print(f"Validation Result (Syntax Error): {report_syntax_error.overall_passed}")
    print(f"File content after Syntax Error validation: {syntax_error_file.read_text().strip()}")
    assert not report_syntax_error.overall_passed
    assert original_syntax_ok == syntax_error_file.read_text()

    # --- Test Single File Validation (Security Issue) ---
    print("\n--- Testing Single File Validation (Security Issue) ---")
    original_security_ok = bad_security_file.read_text()
    # INTENTIONAL: Test data with hardcoded password for security scanner testing
    # nosec: This is test data only
    new_security_bad = "password = 'hardcoded'\n"  # nosec - test data

    try:
        report_security = await code_validator.validate_and_commit_file(
            file_path=str(bad_security_file),
            new_code=new_security_bad,
            original_code=original_security_ok,
            run_tests=False,
            interactive=False,
            custom_validators=[]
        )
    except AnalyzerCriticalError as e:
        print(f"Caught expected AnalyzerCriticalError: {e}")
        report_security = ValidationReport(overall_passed=False, stages={})

    print(f"Validation Result (Security Issue): {report_security.overall_passed}")
    print(f"File content after Security Issue validation: {bad_security_file.read_text().strip()}")
    assert not report_security.overall_passed
    assert original_security_ok == bad_security_file.read_text()

    # --- Test Batch Validation (Success) ---
    print("\n--- Testing Batch Validation (Success) ---")
    batch_files = [str(test_file_path), str(test_test_file)]
    original_batch_contents = {
        str(test_file_path): test_file_path.read_text(),
        str(test_test_file): test_test_file.read_text()
    }

    new_code_for_batch = "import os\n\ndef my_function():\n    x = 3 # Batch change\n    return x\n"
    test_file_path.write_text(new_code_for_batch)

    report_batch_ok = await code_validator.validate_and_commit_batch(
        files_to_validate=batch_files,
        original_contents=original_batch_contents,
        new_contents={
            str(test_file_path): new_code_for_batch,
            str(test_test_file): test_test_file.read_text()
        },
        run_tests=True,
        custom_validators=[]
    )
    print(f"Batch Validation Result (OK): {report_batch_ok.overall_passed}")
    print(f"File content after Batch OK validation: {test_file_path.read_text().strip()}")
    assert report_batch_ok.overall_passed
    assert "3 # Batch change" in test_file_path.read_text()

    # --- Test Batch Validation (Failure - Type Error) ---
    print("\n--- Testing Batch Validation (Failure - Type Error) ---")
    batch_files_fail = [str(test_file_path), str(type_error_file)]
    original_batch_contents_fail = {
        str(test_file_path): test_file_path.read_text(),
        str(type_error_file): type_error_file.read_text()
    }

    new_code_for_batch_fail = "import os\ndef my_function():\n    x = 4 # Batch change for fail\n    return x\n"
    test_file_path.write_text(new_code_for_batch_fail)

    new_type_error_content = "def add_numbers(a: int, b: str) -> int:\n    return a + b\n"
    type_error_file.write_text(new_type_error_content)

    try:
        report_batch_fail = await code_validator.validate_and_commit_batch(
            files_to_validate=batch_files_fail,
            original_contents=original_batch_contents_fail,
            new_contents={
                str(test_file_path): new_code_for_batch_fail,
                str(type_error_file): new_type_error_content
            },
            run_tests=True,
            custom_validators=[]
        )
    except AnalyzerCriticalError as e:
        print(f"Caught expected AnalyzerCriticalError: {e}")
        report_batch_fail = ValidationReport(overall_passed=False, stages={})

    print(f"Batch Validation Result (Type Error): {report_batch_fail.overall_passed}")
    print(f"File content after Batch Type Error validation: {test_file_path.read_text().strip()}")
    print(f"Type Error file content after Batch Type Error validation: {type_error_file.read_text().strip()}")
    assert not report_batch_fail.overall_passed
    assert original_batch_contents_fail[str(test_file_path)] == test_file_path.read_text()
    assert original_batch_contents_fail[str(type_error_file)] == type_error_file.read_text()

    # Clean up dummy project
    print("\n--- Cleaning up test project ---")
    if os.path.exists(test_project_root):
        shutil.rmtree(test_project_root)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AnalyzerCriticalError as e:
        print(f"Fatal error during testing: {e}", file=sys.stderr)
        sys.exit(1)