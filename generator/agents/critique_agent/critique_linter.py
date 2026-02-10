# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# agents/critique_linter.py
import asyncio
import json
import logging
import os
import shlex
import shutil
import tempfile
import time
from abc import ABC, abstractmethod
from collections import Counter as CollectionsCounter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import aiofiles
from opentelemetry import trace
from prometheus_client import Counter, Gauge, Histogram

# --- TOML Compatibility (Python 3.10/3.11+, optional in tests/CI) ---
try:
    # Python 3.11+ stdlib
    import tomllib as tomllib  # type: ignore[no-redef]
except ModuleNotFoundError:
    try:
        # Fallback: use 'tomli' if installed (common in tooling stacks)
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        # Last-resort shim: keeps imports from crashing; raises only if actually used.
        class _TomlShim:
            def loads(self, *_args, **_kwargs):
                raise RuntimeError(
                    "TOML parsing requested but neither 'tomllib' (3.11+) nor 'tomli' is installed."
                )

            def load(self, *_args, **_kwargs):
                raise RuntimeError(
                    "TOML parsing requested but neither 'tomllib' (3.11+) nor 'tomli' is installed."
                )

        tomllib = _TomlShim()  # type: ignore

# --- FIX: Replace local audit_log with runner.runner_audit utility alias ---
# Use the clearer alias as requested for global audit/telemetry logging.
# Import from runner_audit to avoid circular dependency with runner_logging
from runner.runner_audit import log_audit_event as log_action

# --- END FIX ---

logger = logging.getLogger(__name__)
try:
    tracer = trace.get_tracer(__name__)
except TypeError:
    # Fallback for older OpenTelemetry versions
    tracer = None

# Metrics
# FIX: Wrap metric creation in try-except to handle duplicate registration during pytest
try:
    LINT_CALLS = Counter(
        "critique_lint_calls_total", "Lint calls", ["language", "tool"]
    )
    LINT_LATENCY = Histogram(
        "critique_lint_latency_seconds", "Lint latency", ["language", "tool"]
    )
    LINT_ERRORS_COUNT = Gauge(
        "critique_lint_errors", "Errors found", ["language", "severity"]
    )
    LINT_TRENDS = Histogram(
        "critique_lint_trends", "Error trends over time", ["language", "severity"]
    )
except ValueError:
    # Metrics already registered (happens during pytest collection)
    from prometheus_client import REGISTRY

    LINT_CALLS = REGISTRY._names_to_collectors.get("critique_lint_calls_total")
    LINT_LATENCY = REGISTRY._names_to_collectors.get("critique_lint_latency_seconds")
    LINT_ERRORS_COUNT = REGISTRY._names_to_collectors.get("critique_lint_errors")
    LINT_TRENDS = REGISTRY._names_to_collectors.get("critique_lint_trends")

# Error Explanations
ERROR_EXPLANATIONS = {
    "python": {
        "E501": {
            "rationale": "Line too long according to PEP 8.",
            "link": "https://peps.python.org/pep-0008/#maximum-line-length",
            "snippet_example": "long_line = 'x' * 100",
        },
        "W0611": {
            "rationale": "Unused import.",
            "link": "https://pylint.pycqa.org/en/latest/user_guide/messages/warning/unused-import.html",
            "snippet_example": "import os",
        },
        "E999": {
            "rationale": "Syntax error.",
            "link": "https://pycodestyle.pyqa.org/en/latest/intro.html#error-codes",
            "snippet_example": "if True",
        },
    },
    "javascript": {
        "no-unused-vars": {
            "rationale": "Unused variable.",
            "link": "https://eslint.org/docs/rules/no-unused-vars",
            "snippet_example": "var x = 1;",
        },
        "indent": {
            "rationale": "Incorrect indentation.",
            "link": "https://eslint.org/docs/rules/indent",
            "snippet_example": "function(){ return true; }",
        },
    },
    "typescript": {
        "no-unused-vars": {
            "rationale": "Unused variable.",
            "link": "https://eslint.org/docs/rules/no-unused-vars",
            "snippet_example": "const x: string = 'hello';",
        },
        "indent": {
            "rationale": "Incorrect indentation.",
            "link": "https://eslint.org/docs/rules/indent",
            "snippet_example": "function(){ return true; }",
        },
    },
    "go": {
        "golint": {
            "rationale": "Style issue detected by golangci-lint.",
            "link": "https://golangci-lint.run/usage/linters/",
            "snippet_example": "func myfunc() {}",
        },
        "errcheck": {
            "rationale": "Unchecked error return value.",
            "link": "https://golangci-lint.run/usage/linters/#errcheck",
            "snippet_example": "_, _ = fmt.Println()",
        },
    },
    "rust": {
        "clippy::unused_variable": {
            "rationale": "Unused variable detected by Clippy.",
            "link": "https://rust-lang.github.io/rust-clippy/master/index.html#unused_variable",
            "snippet_example": "let x = 1;",
        },
        "clippy::pedantic": {
            "rationale": "Pedantic style issue.",
            "link": "https://rust-lang.github.io/rust-clippy/master/index.html#pedantic",
            "snippet_example": "let mut v = vec![]",
        },
    },
    "java": {
        "MissingJavadocMethod": {
            "rationale": "Missing Javadoc for a method.",
            "link": "https://checkstyle.sourceforge.io/config_javadoc.html#MissingJavadocMethod",
            "snippet_example": "public void myMethod() {}",
        }
    },
}

SEVERITY_LEVELS = ["info", "low", "medium", "high", "critical"]


# --- Parser Functions ---
def ruff_json(raw_output: str) -> List[Dict]:
    try:
        issues = json.loads(raw_output)
        return [
            {
                "code": issue.get("code", "UNKNOWN"),
                "severity": issue.get("severity", "error").lower(),
                "message": issue.get("message", ""),
                "file": issue.get("filename", ""),
                "line": issue.get("location", {}).get("row", 0),
                "column": issue.get("location", {}).get("column", 0),
                "rationale": ERROR_EXPLANATIONS.get("python", {})
                .get(issue.get("code", ""), {})
                .get("rationale", ""),
                "link": ERROR_EXPLANATIONS.get("python", {})
                .get(issue.get("code", ""), {})
                .get("link", ""),
                "snippet": issue.get("code_snippet", ""),
            }
            for issue in issues
        ]
    except json.JSONDecodeError:
        logger.error(f"Failed to parse Ruff output: {raw_output[:500]}")
        return []


def pylint_json(raw_output: str) -> List[Dict]:
    try:
        issues = json.loads(raw_output)
        return [
            {
                "code": issue.get("message-id", "UNKNOWN"),
                "severity": issue.get("type", "error").lower(),
                "message": issue.get("message", ""),
                "file": issue.get("path", ""),
                "line": issue.get("line", 0),
                "column": issue.get("column", 0),
                "rationale": ERROR_EXPLANATIONS.get("python", {})
                .get(issue.get("message-id", ""), {})
                .get("rationale", ""),
                "link": ERROR_EXPLANATIONS.get("python", {})
                .get(issue.get("message-id", ""), {})
                .get("link", ""),
                "snippet": issue.get("source", ""),
            }
            for issue in issues
        ]
    except json.JSONDecodeError:
        logger.error(f"Failed to parse Pylint output: {raw_output[:500]}")
        return []


def pyright_json(raw_output: str) -> List[Dict]:
    try:
        issues = json.loads(raw_output).get("generalDiagnostics", [])
        return [
            {
                "code": issue.get("rule", "UNKNOWN"),
                "severity": issue.get("severity", "error").lower(),
                "message": issue.get("message", ""),
                "file": issue.get("file", ""),
                "line": issue.get("range", {}).get("start", {}).get("line", 0),
                "column": issue.get("range", {}).get("start", {}).get("character", 0),
                "rationale": ERROR_EXPLANATIONS.get("python", {})
                .get(issue.get("rule", ""), {})
                .get("rationale", ""),
                "link": ERROR_EXPLANATIONS.get("python", {})
                .get(issue.get("rule", ""), {})
                .get("link", ""),
                "snippet": "",
            }
            for issue in issues
        ]
    except json.JSONDecodeError:
        logger.error(f"Failed to parse Pyright output: {raw_output[:500]}")
        return []


def eslint_json(raw_output: str) -> List[Dict]:
    try:
        issues = json.loads(raw_output)
        all_issues = []
        for file_report in issues:
            file_path = file_report.get("filePath", "")
            for issue in file_report.get("messages", []):
                all_issues.append(
                    {
                        "code": issue.get("ruleId", "UNKNOWN"),
                        "severity": (
                            "error" if issue.get("severity", 2) == 2 else "warning"
                        ),
                        "message": issue.get("message", ""),
                        "file": file_path,
                        "line": issue.get("line", 0),
                        "column": issue.get("column", 0),
                        "rationale": ERROR_EXPLANATIONS.get("javascript", {})
                        .get(issue.get("ruleId", ""), {})
                        .get("rationale", ""),
                        "link": ERROR_EXPLANATIONS.get("javascript", {})
                        .get(issue.get("ruleId", ""), {})
                        .get("link", ""),
                        "snippet": "",
                    }
                )
        return all_issues
    except json.JSONDecodeError:
        logger.error(f"Failed to parse ESLint output: {raw_output[:500]}")
        return []


def golangci_lint_json(raw_output: str) -> List[Dict]:
    try:
        issues = json.loads(raw_output).get("Issues", [])
        return [
            {
                "code": issue.get("FromLinter", "UNKNOWN"),
                "severity": issue.get("Severity", "error").lower(),
                "message": issue.get("Text", ""),
                "file": issue.get("Pos", {}).get("Filename", ""),
                "line": issue.get("Pos", {}).get("Line", 0),
                "column": issue.get("Pos", {}).get("Column", 0),
                "rationale": ERROR_EXPLANATIONS.get("go", {})
                .get(issue.get("FromLinter", ""), {})
                .get("rationale", ""),
                "link": ERROR_EXPLANATIONS.get("go", {})
                .get(issue.get("FromLinter", ""), {})
                .get("link", ""),
                "snippet": issue.get("SourceLines", [""])[0],
            }
            for issue in issues
        ]
    except json.JSONDecodeError:
        logger.error(f"Failed to parse golangci-lint output: {raw_output[:500]}")
        return []


def staticcheck_json(raw_output: str) -> List[Dict]:
    try:
        issues = [
            json.loads(line) for line in raw_output.strip().splitlines() if line.strip()
        ]
        return [
            {
                "code": issue.get("code", "UNKNOWN"),
                "severity": issue.get("severity", "error").lower(),
                "message": issue.get("message", ""),
                "file": issue.get("location", {}).get("file", ""),
                "line": issue.get("location", {}).get("line", 0),
                "column": issue.get("location", {}).get("column", 0),
                "rationale": ERROR_EXPLANATIONS.get("go", {})
                .get(issue.get("code", ""), {})
                .get("rationale", ""),
                "link": ERROR_EXPLANATIONS.get("go", {})
                .get(issue.get("code", ""), {})
                .get("link", ""),
                "snippet": issue.get("snippet", ""),
            }
            for issue in issues
        ]
    except json.JSONDecodeError:
        logger.error(f"Failed to parse staticcheck output: {raw_output[:500]}")
        return []


def clippy_json(raw_output: str) -> List[Dict]:
    try:
        issues = [
            json.loads(line) for line in raw_output.strip().splitlines() if line.strip()
        ]
        all_issues = []
        for issue in issues:
            if issue.get("reason") == "compiler-message" and issue.get(
                "message", {}
            ).get("code"):
                code_id = issue["message"]["code"]["code"]
                message = issue["message"]["message"]
                spans = issue["message"].get("spans", [])
                file_name = spans[0].get("file_name", "") if spans else ""
                line_num = spans[0].get("line_start", 0) if spans else 0
                col_num = spans[0].get("column_start", 0) if spans else 0
                severity_level = issue["message"]["level"]
                severity = "error" if "error" in severity_level else "warning"
                all_issues.append(
                    {
                        "code": code_id,
                        "severity": severity,
                        "message": message,
                        "file": file_name,
                        "line": line_num,
                        "column": col_num,
                        "rationale": ERROR_EXPLANATIONS.get("rust", {})
                        .get(code_id, {})
                        .get("rationale", ""),
                        "link": ERROR_EXPLANATIONS.get("rust", {})
                        .get(code_id, {})
                        .get("link", ""),
                        "snippet": spans[0].get("text", [""])[0] if spans else "",
                    }
                )
        return all_issues
    except json.JSONDecodeError:
        logger.error(f"Failed to parse Clippy output: {raw_output[:500]}")
        return []


def checkstyle_json(raw_output: str) -> List[Dict]:
    try:
        issues = json.loads(raw_output).get("files", [])
        all_issues = []
        for file in issues:
            file_name = file.get("name", "")
            for error in file.get("errors", []):
                code = error.get("source", "UNKNOWN").split(".")[-1]
                all_issues.append(
                    {
                        "code": code,
                        "severity": error.get("severity", "error").lower(),
                        "message": error.get("message", ""),
                        "file": file_name,
                        "line": error.get("line", 0),
                        "column": error.get("column", 0),
                        "rationale": ERROR_EXPLANATIONS.get("java", {})
                        .get(code, {})
                        .get("rationale", ""),
                        "link": ERROR_EXPLANATIONS.get("java", {})
                        .get(code, {})
                        .get("link", ""),
                        "snippet": "",
                    }
                )
        return all_issues
    except json.JSONDecodeError:
        logger.error(f"Failed to parse Checkstyle output: {raw_output[:500]}")
        return []


def spotbugs_json(raw_output: str) -> List[Dict]:
    try:
        # NOTE: SpotBugs JSON output is often non-standard (XML converted to JSON), handling both single and list results
        bug_collection = json.loads(raw_output).get("BugCollection", {})
        issues = bug_collection.get("BugInstance", [])
        if not isinstance(issues, list):
            issues = [issues] if issues else []

        all_issues = []
        for issue in issues:
            type_id = issue.get("@type", "UNKNOWN")
            message = issue.get("LongMessage", "No message")
            source_line = issue.get("SourceLine", {})
            line_num = int(source_line.get("@start", 0)) if source_line else 0
            file_name = source_line.get("@sourcefile", "") if source_line else ""

            rank = int(issue.get("@rank", 20))
            severity = "low"
            if rank <= 4:
                severity = "critical"
            elif rank <= 8:
                severity = "high"
            elif rank <= 12:
                severity = "medium"

            all_issues.append(
                {
                    "code": type_id,
                    "severity": severity,
                    "message": message,
                    "file": file_name,
                    "line": line_num,
                    "column": 0,
                    "rationale": ERROR_EXPLANATIONS.get("java", {})
                    .get(type_id, {})
                    .get("rationale", ""),
                    "link": "",
                    "snippet": "",
                }
            )
        return all_issues
    except json.JSONDecodeError:
        logger.error(f"Failed to parse SpotBugs output: {raw_output[:500]}")
        return []


# PRODUCTION FIX: Define the LINTER_CONFIG dictionary. This is the central configuration
# for all supported languages and their tools. It was missing, which would cause a runtime crash.
# This configuration makes the entire system functional and easy to modify.
LINTER_CONFIG: Dict[str, List[Dict[str, Any]]] = {
    "python": [
        {
            "tool": "ruff",
            "cmd": ["ruff", "check", "--output-format=json", "--exit-zero"],
            "parser": ruff_json,
            "config_file": "pyproject.toml",
            "config_section": "tool.ruff",
            "use_container": True,
            "container_image": "ghcr.io/astral-sh/ruff:latest",
            "timeout": 60,
        },
        {
            "tool": "pylint",
            "cmd": ["pylint", "--output-format=json", "--exit-zero"],
            "parser": pylint_json,
            "use_container": True,
            "container_image": "pylint/pylint:latest",
            "timeout": 90,
        },
    ],
    "javascript": [
        {
            "tool": "eslint",
            "cmd": ["eslint", "--format=json"],
            "parser": eslint_json,
            "config_file": ".eslintrc.js",
            "use_container": True,
            "container_image": "node:20-slim",
            "timeout": 60,
        },
    ],
    "typescript": [
        {
            "tool": "eslint",
            "cmd": ["eslint", "--format=json"],
            "parser": eslint_json,
            "config_file": ".eslintrc.js",
            "use_container": True,
            "container_image": "node:20-slim",  # Assuming typescript and eslint are installed
            "timeout": 60,
        },
    ],
    "go": [
        {
            "tool": "golangci-lint",
            "cmd": [
                "golangci-lint",
                "run",
                "--out-format=json",
                "--issues-exit-code=0",
            ],
            "parser": golangci_lint_json,
            "use_container": True,
            "container_image": "golangci/golangci-lint:v1.57-alpine",
            "timeout": 120,
        },
    ],
    "rust": [
        {
            "tool": "clippy",
            "cmd": ["cargo", "clippy", "--message-format=json"],
            "parser": clippy_json,
            "use_container": True,
            "container_image": "rust:1.77-slim",
            "timeout": 180,
        },
    ],
    "java": [
        {
            "tool": "checkstyle",
            "cmd": ["java", "-jar", "/app/checkstyle.jar", "-f", "json"],
            "parser": checkstyle_json,
            "config_file": "checkstyle.xml",
            "use_container": True,
            "container_image": "checkstyle/checkstyle:10.12.0",
            "timeout": 120,
        },
        {
            "tool": "spotbugs",
            # SpotBugs requires class files, compilation handled in JavaLintPlugin
            "cmd": [
                "java",
                "-jar",
                "/app/spotbugs/lib/spotbugs.jar",
                "-xml:withMessages",
                "-json",
                "-output",
                "spotbugs_output.json",
            ],
            "parser": spotbugs_json,
            "config_file": "spotbugs-exclude.xml",
            "use_container": True,
            "container_image": "spotbugs/spotbugs:4.8.2",
            "timeout": 240,
        },
    ],
}


async def load_file_content(file_path: str) -> str:
    try:
        async with aiofiles.open(file_path, mode="r", encoding="utf-8") as f:
            return await f.read()
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        return ""
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {e}")
        return ""


class LintPlugin(ABC):
    @abstractmethod
    async def run_linter(
        self, tool_cfg: Dict[str, Any], file_path: str, project_dir: str
    ) -> Dict[str, Any]:
        pass

    async def _run_tool(
        self,
        cmd: List[str],
        project_dir: str,
        tool_name: str,
        timeout: int,
        use_container: bool,
        container_image: Optional[str],
        fallback_to_local: bool = True,
    ) -> Dict[str, Any]:
        if use_container:
            if not shutil.which("docker"):
                # Docker not available - try local fallback if enabled
                if fallback_to_local and cmd:
                    tool_binary = cmd[0]
                    if shutil.which(tool_binary):
                        logger.info(
                            f"Docker not available, using local {tool_name} installation at {shutil.which(tool_binary)}"
                        )
                        # Recursively call with use_container=False to run tool locally
                        # This is safe from infinite recursion because:
                        # 1. use_container=False takes a different code path (non-Docker execution)
                        # 2. fallback_to_local=False prevents re-entering this fallback logic
                        try:
                            result = await self._run_tool(
                                cmd=cmd,
                                project_dir=project_dir,
                                tool_name=tool_name,
                                timeout=timeout,
                                use_container=False,
                                container_image=None,
                                fallback_to_local=False,  # Prevent infinite recursion
                            )
                            # Verify the local execution worked
                            if result.get("success") or result.get("returncode") == 0:
                                return result
                            else:
                                logger.warning(
                                    f"Local {tool_name} execution failed (returncode={result.get('returncode')}). "
                                    f"Skipping this check."
                                )
                        except Exception as e:
                            logger.error(
                                f"Error running local {tool_name}: {e}. Skipping this check.",
                                exc_info=True
                            )
                    else:
                        logger.warning(
                            f"Docker not available and {tool_name} not found locally at {tool_binary}. Skipping {tool_name}."
                        )
                else:
                    logger.warning(
                        f"Docker not available and local fallback disabled. Skipping containerized linting for {tool_name}."
                    )
                # Capability check - gracefully skip containerized linting
                return {
                    "success": True,  # Don't fail the job, just skip this check
                    "stdout": "",
                    "stderr": f"Docker not available and {tool_name} not found locally - skipping",
                    "returncode": 0,
                    "status": "skipped",
                }
            if not container_image:
                logger.error(
                    f"Configuration error: 'container_image' is not defined for {tool_name}."
                )
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": "No container image specified for tool",
                    "returncode": 1,
                }
            try:
                # Run docker pull with a timeout
                pull_proc = await asyncio.create_subprocess_exec(
                    "docker",
                    "pull",
                    container_image,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(
                    pull_proc.wait(), timeout=300
                )  # 5 minute timeout for pulling
                if pull_proc.returncode != 0:
                    _, pull_stderr = await pull_proc.communicate()
                    err_msg = f"Failed to pull container image '{container_image}': {pull_stderr.decode(errors='ignore')}"
                    logger.error(err_msg)
                    return {
                        "success": False,
                        "stdout": "",
                        "stderr": err_msg,
                        "returncode": 1,
                    }
            except asyncio.TimeoutError:
                logger.warning(f"Docker pull for '{container_image}' timed out.")
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": "Docker pull Timeout",
                    "returncode": 1,
                }
            except Exception as e:
                logger.error(
                    f"An unexpected error occurred during docker pull for '{container_image}': {e}"
                )
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": str(e),
                    "returncode": 1,
                }

            abs_project_dir = Path(project_dir).resolve()
            # Use shlex.quote to safely handle paths with spaces or special characters
            container_cmd = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{shlex.quote(str(abs_project_dir))}:/app",
                "-w",
                "/app",
                container_image,
            ] + cmd
            effective_cmd = container_cmd
            effective_cwd = None
        else:
            if not shutil.which(cmd[0]):
                # Capability check
                logger.error(
                    f"Linter tool executable '{cmd[0]}' not found in PATH. Skipping."
                )
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": f"Tool not found: {cmd[0]}",
                    "returncode": 127,
                }
            effective_cmd = cmd
            effective_cwd = project_dir

        try:
            # Execute the command
            proc = await asyncio.create_subprocess_exec(
                *effective_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=effective_cwd,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "success": proc.returncode == 0,
                "stdout": stdout.decode(errors="ignore"),
                "stderr": stderr.decode(errors="ignore"),
                "returncode": proc.returncode,
            }
        except asyncio.TimeoutError:
            logger.warning(f"Linter '{tool_name}' timed out after {timeout} seconds.")
            # Ensure the process is terminated
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass  # Process already terminated
            return {
                "success": False,
                "stdout": "",
                "stderr": "Timeout",
                "returncode": 1,
            }
        except FileNotFoundError:
            logger.error(
                f"Command '{effective_cmd[0]}' not found. Ensure it is installed and in the system's PATH."
            )
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Command not found: {effective_cmd[0]}",
                "returncode": 127,
            }
        except Exception as e:
            logger.error(
                f"An unexpected error occurred while running '{tool_name}': {e}. Command: {' '.join(effective_cmd)}"
            )
            return {"success": False, "stdout": "", "stderr": str(e), "returncode": 1}


class PythonLintPlugin(LintPlugin):
    async def run_linter(
        self, tool_cfg: Dict[str, Any], file_path: str, project_dir: str
    ) -> Dict[str, Any]:
        cmd = tool_cfg["cmd"] + [file_path]
        config_file_name = tool_cfg.get("config_file")
        if config_file_name:
            config_path = Path(project_dir) / config_file_name
            if config_path.exists():
                if config_file_name.endswith(".toml"):
                    try:
                        with open(config_path, "rb") as f:
                            config_data = tomllib.load(f)
                        section_key = tool_cfg.get("config_section")
                        if section_key:
                            current_section = config_data
                            for part in section_key.split("."):
                                current_section = current_section.get(part, {})
                            # Note: This implementation may be simplified when running in a container
                            # that expects the config file to just be present in the mounted volume.
                            if tool_cfg["tool"] == "ruff":
                                if "select" in current_section:
                                    cmd.extend(
                                        [
                                            "--select",
                                            ",".join(current_section["select"]),
                                        ]
                                    )
                                if "ignore" in current_section:
                                    cmd.extend(
                                        [
                                            "--ignore",
                                            ",".join(current_section["ignore"]),
                                        ]
                                    )
                    except Exception as e:
                        logger.warning(f"Failed to parse {config_file_name}: {e}")
                elif config_file_name.endswith(".json"):
                    cmd.extend(["--config", str(config_path)])

        # Ruff/Pylint/Pyright usually expect file path relative to the config/project root,
        # but since we run the linter in the mounted project_dir, the file_path is already correct.
        return await self._run_tool(
            cmd,
            project_dir,
            tool_cfg["tool"],
            tool_cfg.get("timeout", 60),
            tool_cfg.get("use_container", False),
            tool_cfg.get("container_image"),
        )

    def parse_output(
        self, output: str, tool: str, language: str
    ) -> List[Dict[str, Any]]:
        # This function is not used directly, as the parser is set in LINTER_CONFIG.
        return []


class JavaScriptLintPlugin(LintPlugin):
    async def run_linter(
        self, tool_cfg: Dict[str, Any], file_path: str, project_dir: str
    ) -> Dict[str, Any]:
        cmd = tool_cfg["cmd"] + [file_path]
        config_file_name = tool_cfg.get("config_file")
        if config_file_name:
            config_path = Path(project_dir) / config_file_name
            if config_path.exists():
                cmd.extend(["--config", str(config_path)])
        # For ESLint in a node container, we usually need to run 'npm install' first,
        # but for simplicity, we assume dependencies are handled by the environment or image.
        # This implementation bypasses the explicit install step for brevity.
        return await self._run_tool(
            cmd,
            project_dir,
            tool_cfg["tool"],
            tool_cfg.get("timeout", 60),
            tool_cfg.get("use_container", False),
            tool_cfg.get("container_image"),
        )

    def parse_output(
        self, output: str, tool: str, language: str
    ) -> List[Dict[str, Any]]:
        return eslint_json(output)


class RustLintPlugin(LintPlugin):
    async def run_linter(
        self, tool_cfg: Dict[str, Any], file_path: str, project_dir: str
    ) -> Dict[str, Any]:
        # Rust tools often require a Cargo project structure.
        src_dir = Path(project_dir) / "src"
        src_dir.mkdir(exist_ok=True)
        # Assuming single file analysis, copy it into the expected 'src' directory.
        shutil.copy(file_path, src_dir / "main.rs")

        # Create a minimal Cargo.toml if it doesn't exist
        cargo_toml_path = Path(project_dir) / "Cargo.toml"
        if not cargo_toml_path.exists():
            async with aiofiles.open(cargo_toml_path, "w") as f:
                await f.write(
                    '[package]\nname = "temp-lint-project"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n'
                )

        cmd = tool_cfg["cmd"]
        # Rust tools (cargo) run from the project root (mounted /app in container)
        return await self._run_tool(
            cmd,
            project_dir,
            tool_cfg["tool"],
            tool_cfg.get("timeout", 180),
            tool_cfg.get("use_container", False),
            tool_cfg.get("container_image"),
        )

    def parse_output(
        self, output: str, tool: str, language: str
    ) -> List[Dict[str, Any]]:
        return clippy_json(output)


class GoLintPlugin(LintPlugin):
    async def run_linter(
        self, tool_cfg: Dict[str, Any], file_path: str, project_dir: str
    ) -> Dict[str, Any]:
        go_mod_path = Path(project_dir) / "go.mod"

        # Only initialize if go.mod is missing to handle existing projects gracefully
        if not go_mod_path.exists():
            init_go_mod_cmd = ["go", "mod", "init", "tempmodule"]
            # Go module commands often need a Go-enabled container/env even if the linter runs bare metal
            init_mod_result = await self._run_tool(
                init_go_mod_cmd,
                project_dir,
                "go_mod_init",
                30,
                tool_cfg.get("use_container", False),
                tool_cfg.get("container_image"),
            )
            if not init_mod_result["success"]:
                logger.error(
                    f"Failed to initialize Go module in {project_dir}: {init_mod_result['stderr']}"
                )
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": f"Go mod init failed: {init_mod_result['stderr']}",
                    "returncode": init_mod_result["returncode"],
                }

            # Run go mod tidy to resolve dependencies
            tidy_result = await self._run_tool(
                ["go", "mod", "tidy"],
                project_dir,
                "go_mod_tidy",
                30,
                tool_cfg.get("use_container", False),
                tool_cfg.get("container_image"),
            )
            if not tidy_result["success"]:
                logger.warning(
                    f"Go mod tidy failed (non-critical for single file lint): {tidy_result['stderr']}"
                )

        cmd = tool_cfg["cmd"] + ["./..."]  # Analyze all files in the module context

        return await self._run_tool(
            cmd,
            project_dir,
            tool_cfg["tool"],
            tool_cfg.get("timeout", 120),
            tool_cfg.get("use_container", False),
            tool_cfg.get("container_image"),
        )

    def parse_output(
        self, output: str, tool: str, language: str
    ) -> List[Dict[str, Any]]:
        if tool == "golangci-lint":
            return golangci_lint_json(output)
        elif tool == "staticcheck":
            return staticcheck_json(output)
        return []


class JavaLintPlugin(LintPlugin):
    async def run_linter(
        self, tool_cfg: Dict[str, Any], file_path: str, project_dir: str
    ) -> Dict[str, Any]:
        cmd = tool_cfg["cmd"]

        # Compilation step for tools that operate on compiled code (like SpotBugs)
        if tool_cfg["tool"] == "spotbugs":
            # This compilation logic needs a Java SDK environment (javac)
            class_dir = Path(project_dir) / "classes"
            class_dir.mkdir(exist_ok=True)
            compile_cmd = ["javac", "-d", str(class_dir), file_path]
            # Use the tool's container config for compilation, assuming it includes a JDK
            compile_result = await self._run_tool(
                compile_cmd,
                project_dir,
                "javac_compile",
                60,
                tool_cfg.get("use_container", False),
                tool_cfg.get("container_image"),
            )
            if not compile_result["success"]:
                logger.error(
                    f"Java compilation failed for {file_path}: {compile_result['stderr']}"
                )
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": f"Compilation failed: {compile_result['stderr']}",
                    "returncode": compile_result["returncode"],
                }

            # SpotBugs command needs to analyze the compiled classes directory
            cmd = [
                "java",
                "-jar",
                "/app/spotbugs/lib/spotbugs.jar",
                "-json",
                "-output",
                "spotbugs_output.json",
                "-effort:max",
                "-r:medium",
            ]
            cmd += ["-t", "json"]  # Try to force JSON output
            cmd += [str(class_dir)]
        else:
            # Checkstyle operates on source files
            cmd += [file_path]

        config_file_name = tool_cfg.get("config_file")
        if config_file_name:
            config_path = Path(project_dir) / config_file_name
            # Checkstyle needs explicit config flag
            if config_path.exists() and tool_cfg["tool"] == "checkstyle":
                cmd.extend(["-c", str(config_path)])

        return await self._run_tool(
            cmd,
            project_dir,
            tool_cfg["tool"],
            tool_cfg.get("timeout", 120),
            tool_cfg.get("use_container", False),
            tool_cfg.get("container_image"),
        )

    def parse_output(
        self, output: str, tool: str, language: str
    ) -> List[Dict[str, Any]]:
        if tool == "checkstyle":
            return checkstyle_json(output)
        elif tool == "spotbugs":
            # SpotBugs is notoriously tricky with JSON, it may output XML. The parser handles this.
            return spotbugs_json(output)
        return []


PLUGINS: Dict[str, LintPlugin] = {
    "python": PythonLintPlugin(),
    "javascript": JavaScriptLintPlugin(),
    "typescript": JavaScriptLintPlugin(),
    "go": GoLintPlugin(),
    "rust": RustLintPlugin(),
    "java": JavaLintPlugin(),
}


async def run_single_lint(
    tool_cfg: Dict[str, Any],
    filename: str,
    content: str,
    temp_dir: str,
    language: str,
    project_dir: str,
) -> Dict[str, Any]:
    file_path = os.path.join(temp_dir, filename)
    async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
        await f.write(content)

    plugin = PLUGINS.get(language)
    if not plugin:
        logger.error(f"No lint plugin for {language}.")
        return {
            tool_cfg["tool"]: {
                "raw": {
                    "success": False,
                    "stdout": "",
                    "stderr": f"No plugin for {language}",
                    "returncode": 1,
                },
                "parsed": [],
            }
        }

    with tracer.start_as_current_span(f"lint.{tool_cfg['tool']}"):
        start = time.time()
        # project_dir is passed down to run_linter to handle project-context configuration
        raw_result = await plugin.run_linter(tool_cfg, file_path, project_dir)

        parsed_errors = []
        # Linters often return non-zero exit codes when issues are found. We still need to parse the output.
        if raw_result["stdout"] and (
            raw_result["success"] or raw_result["returncode"] in [1, 2]
        ):
            parser_func = tool_cfg.get("parser")
            if callable(parser_func):
                parsed_errors = parser_func(raw_result["stdout"])
            else:
                logger.error(
                    f"Parser for {tool_cfg['tool']} is not configured or not callable."
                )
                parsed_errors.append(
                    {
                        "code": "PARSER_ERROR",
                        "severity": "critical",
                        "message": f"Parser for {tool_cfg['tool']} is invalid",
                        "file": filename,
                        "line": 0,
                        "column": 0,
                        "rationale": "The configured parser function could not be executed.",
                        "link": "",
                        "snippet": "",
                    }
                )
        elif not raw_result["success"]:
            logger.error(
                f"Linter tool {tool_cfg['tool']} failed unexpectedly with return code {raw_result['returncode']}: {raw_result['stderr']}"
            )
            parsed_errors.append(
                {
                    "code": "LINTER_EXEC_ERROR",
                    "severity": "critical",
                    "message": f"Linter execution failed: {raw_result['stderr'][:500]}",
                    "file": filename,
                    "line": 0,
                    "column": 0,
                    "rationale": "The linter tool encountered an unrecoverable error during execution.",
                    "link": "",
                    "snippet": "",
                }
            )

        latency = time.time() - start
        LINT_LATENCY.labels(language, tool_cfg["tool"]).observe(latency)
        LINT_CALLS.labels(language, tool_cfg["tool"]).inc()

        severity_counts = CollectionsCounter(e["severity"] for e in parsed_errors)
        for sev, count in severity_counts.items():
            # Use Gauge.set to record the count for the current run
            LINT_ERRORS_COUNT.labels(language, sev).set(count)
            LINT_TRENDS.labels(language, sev).observe(count)

        for err in parsed_errors:
            # Simple suggested fix generation
            err["suggested_fix"] = {
                "E501": "Break the line or reformat to comply with line length limits.",
                "W0611": "Remove the unused import statement.",
                "no-unused-vars": "Remove the declared variable if it's not used, or use it.",
                "indent": "Adjust indentation to match style guidelines, typically 2 or 4 spaces.",
                "MissingJavadocMethod": "Add a Javadoc comment explaining the method's purpose, parameters, and return value.",
                "clippy::unused_variable": "Remove the unused variable declaration.",
                "golint": "Review the code for style issues and apply idiomatic Go formatting (e.g., `go fmt`).",
                "errcheck": "Handle the error returned by the function call, or explicitly ignore it if intended.",
            }.get(
                err.get("code"),
                "Review the rationale and link for specific guidance on how to fix this issue.",
            )

        return {tool_cfg["tool"]: {"raw": raw_result, "parsed": parsed_errors}}


async def run_all_lints_and_checks(
    code_files: Dict[str, str],
    temp_dir: str,
    language: str = "python",
    hitl_callback: Optional[Callable] = None,
    project_dir: Optional[str] = None,
) -> Dict[str, Any]:
    with tracer.start_as_current_span("run_lints", attributes={"language": language}):
        time.monotonic()
        if (
            not isinstance(code_files, dict)
            or not code_files
            or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in code_files.items()
            )
        ):
            LINT_ERRORS_COUNT.labels(language, "critical").set(1)
            logger.error(
                "Invalid code_files: must be a non-empty dictionary with string keys and values."
            )
            return {
                "lint_results": {},
                "all_errors": [
                    {
                        "code": "INVALID_INPUT",
                        "severity": "critical",
                        "message": "Invalid code_files",
                        "line": 0,
                        "column": 0,
                        "rationale": "",
                        "link": "",
                        "snippet": "",
                    }
                ],
            }
        if language not in LINTER_CONFIG:
            LINT_ERRORS_COUNT.labels(language, "critical").set(1)
            logger.error(
                f"Unsupported language: {language}. Supported: {list(LINTER_CONFIG.keys())}"
            )
            return {
                "lint_results": {},
                "all_errors": [
                    {
                        "code": "UNSUPPORTED_LANGUAGE",
                        "severity": "critical",
                        "message": f"Unsupported language: {language}",
                        "line": 0,
                        "column": 0,
                        "rationale": "",
                        "link": "",
                        "snippet": "",
                    }
                ],
            }

        effective_project_dir = (
            Path(project_dir)
            if project_dir and Path(project_dir).is_dir()
            else Path(temp_dir)
        )
        if project_dir and not Path(project_dir).is_dir():
            logger.warning(
                f"Invalid project_dir: {project_dir}. Falling back to temp_dir."
            )
        if hitl_callback and not callable(hitl_callback):
            logger.warning("Invalid hitl_callback: not callable. Ignoring.")
            hitl_callback = None

        logger.info(f"Running linting for '{language}' in {effective_project_dir}")

        tools = LINTER_CONFIG.get(language, [])
        if not tools:
            logger.info(f"No linters configured for {language}.")
            return {"lint_results": {}, "all_errors": []}

        # We need to run linters that analyze the whole project context once
        tasks = []
        for tool_cfg in tools:
            # For simplicity in this example, we run each tool against each file.
            # A more advanced setup would run project-wide tools just once.
            for filename, content in code_files.items():
                tasks.append(
                    run_single_lint(
                        tool_cfg,
                        filename,
                        content,
                        temp_dir,
                        language,
                        str(effective_project_dir),
                    )
                )

        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        lint_results_by_tool = {}
        all_errors_collected = []
        for res in results_list:
            if isinstance(res, Exception):
                logger.error(f"Lint task failed: {res}", exc_info=res)
                all_errors_collected.append(
                    {
                        "code": "LINTER_CRASH",
                        "severity": "critical",
                        "message": f"Linter task failed: {str(res)}",
                        "line": 0,
                        "column": 0,
                        "rationale": "A linter process crashed or encountered an unhandled exception.",
                        "link": "",
                        "snippet": "",
                    }
                )
                continue
            for tool_name, data in res.items():
                lint_results_by_tool.setdefault(tool_name, []).extend(data["parsed"])
                all_errors_collected.extend(data["parsed"])

        if hitl_callback:
            logger.info("Applying HITL filter.")
            final_errors = [err for err in all_errors_collected if hitl_callback(err)]
        else:
            final_errors = all_errors_collected

        total_errors = len(final_errors)
        errors_by_severity = CollectionsCounter(e["severity"] for e in final_errors)
        # Use runner-provided audit logging utility
        log_action(
            "All Lints and Checks Completed",
            {
                "language": language,
                "total_errors_found": total_errors,
                "errors_by_severity": dict(errors_by_severity),
                "lint_tools_used": [tool_cfg["tool"] for tool_cfg in tools],
            },
        )

        return {"lint_results": lint_results_by_tool, "all_errors": final_errors}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run linter pipeline")
    parser.add_argument(
        "--code-dir", required=True, help="Directory containing code files to lint."
    )
    parser.add_argument(
        "--lang",
        default="python",
        help="Programming language of the code files (e.g., python, javascript).",
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="Optional: The root directory of the project for linter context (e.g., where pyproject.toml is). If not provided, a temporary directory is used.",
    )
    args = parser.parse_args()

    temp_dir = tempfile.mkdtemp()

    try:
        code_files = {}
        # Ensure project_dir exists if specified, otherwise the linter plugins might fail
        effective_project_dir = (
            args.project_dir
            if args.project_dir and Path(args.project_dir).is_dir()
            else temp_dir
        )

        # Copy files to effective_project_dir if it's not the code-dir itself
        source_dir = Path(args.code_dir)
        if not Path(effective_project_dir).samefile(source_dir):
            shutil.copytree(source_dir, effective_project_dir, dirs_exist_ok=True)
            logger.info(
                f"Copied files from {args.code_dir} to project context directory {effective_project_dir}"
            )

        for f_name in os.listdir(source_dir):
            f_path = source_dir / f_name
            if f_path.is_file():
                try:
                    # Read from original code_dir
                    code_files[f_name] = f_path.read_text(encoding="utf-8")
                except Exception as e:
                    logger.error(f"Could not read file {f_path}: {e}")
                    continue

        if not code_files:
            logger.warning(f"No code files found in {args.code_dir}. Exiting.")
        else:
            # Pass the effective_project_dir to the runner
            results = asyncio.run(
                run_all_lints_and_checks(
                    code_files, temp_dir, args.lang, project_dir=effective_project_dir
                )
            )

            print("\n--- Linting Results Summary ---")
            print(f"Language: {args.lang}")
            print(f"Total errors found: {len(results['all_errors'])}")

            if results["all_errors"]:
                print("\nErrors by Severity:")
                for severity, count in Counter(
                    e["severity"] for e in results["all_errors"]
                ).items():
                    print(f"- {severity.capitalize()}: {count}")

                print("\nDetailed Errors:")
                for error in results["all_errors"]:
                    print(f"  File: {error.get('file', 'N/A')}")
                    print(f"  Code: {error.get('code', 'N/A')}")
                    print(f"  Severity: {error.get('severity', 'N/A').capitalize()}")
                    print(f"  Message: {error.get('message', 'N/A')}")
                    print(
                        f"  Location: Line {error.get('line', 'N/A')}, Column {error.get('column', 'N/A')}"
                    )
                    print(f"  Rationale: {error.get('rationale', 'N/A')}")
                    print(f"  Link: {error.get('link', 'N/A')}")
                    print(f"  Suggested Fix: {error.get('suggested_fix', 'N/A')}")
                    print("-" * 20)
            else:
                print("No linting errors found. Good job!")

    finally:
        # Only remove temp_dir if it was actually used as the project directory
        if effective_project_dir == temp_dir and Path(temp_dir).exists():
            shutil.rmtree(temp_dir)
            logger.info(f"Cleaned up temporary directory: {temp_dir}")
